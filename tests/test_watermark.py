from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gmail_oauth_reader import coordinator as coordinator_module
from custom_components.gmail_oauth_reader.const import (
    CONF_EXTRACT_OTP,
    CONF_QUERY,
    CONF_QUEUE_DWELL_TIME,
    DEFAULT_QUERY,
    DOMAIN,
    EVENT_GMAIL_NEW_EMAIL,
    EVENT_GMAIL_NEW_OTP,
    GMAIL_MESSAGES_URL,
    LATE_ARRIVAL_GRACE_MS,
)
from custom_components.gmail_oauth_reader.coordinator import (
    GmailDataUpdateCoordinator,
    parse_internal_date,
)

STARTUP_MS = 1_800_000_000_000
MINUTE_MS = 60_000
_UNSET = object()
created: list[GmailDataUpdateCoordinator] = []


class FakeGmail:
    """In-memory Gmail API that serves list pages and message details like the real API."""

    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.detail_failures: dict[str, list[int]] = {}
        self.detail_order_reversed = False

    def add(
        self,
        message_id: str,
        internal_date: Any,
        subject: str | None = None,
        date_header: str | None = "Mon, 01 Jan 2024 09:00:00 +0000",
        labels: tuple[str, ...] = ("INBOX", "UNREAD"),
        hidden: bool = False,
    ) -> None:
        self.messages[message_id] = {
            "internal_date": internal_date,
            "subject": subject if subject is not None else f"Parcel update {message_id}",
            "date_header": date_header,
            "labels": set(labels),
            "hidden": hidden,
        }

    def mark_read(self, message_ids: list[str]) -> None:
        for mid in message_ids:
            self.messages[mid]["labels"].discard("UNREAD")

    def delete(self, message_ids: list[str]) -> None:
        for mid in message_ids:
            del self.messages[mid]

    def _matches(self, msg: dict[str, Any], query: str) -> bool:
        if msg["hidden"]:
            return False
        if query == DEFAULT_QUERY:
            return {"INBOX", "UNREAD"} <= msg["labels"]
        return query.removeprefix("label:") in msg["labels"]

    def _sort_key(self, item: tuple[str, dict[str, Any]]) -> int:
        parsed = parse_internal_date(item[1]["internal_date"])
        return parsed or 0

    async def request(self, method: str, url: str, params: dict | None = None, **_: Any):
        resp = MagicMock()
        resp.status = 200
        params = params or {}
        if url == GMAIL_MESSAGES_URL:
            visible = sorted(
                (
                    item
                    for item in self.messages.items()
                    if self._matches(item[1], params["q"])
                ),
                key=self._sort_key,
                reverse=True,
            )
            start = int(params.get("pageToken") or 0)
            size = int(params["maxResults"])
            body: dict[str, Any] = {
                "messages": [
                    {"id": mid, "threadId": mid} for mid, _ in visible[start : start + size]
                ],
                "resultSizeEstimate": len(visible),
            }
            if start + size < len(visible):
                body["nextPageToken"] = str(start + size)
            resp.json = AsyncMock(return_value=body)
            return resp

        message_id = url.rsplit("/", 1)[1]
        failures = self.detail_failures.get(message_id)
        if failures:
            resp.status = failures.pop(0)
            return resp
        msg = self.messages[message_id]
        headers = [
            {"name": "From", "value": "Royal Mail <no-reply@royalmail.com>"},
            {"name": "Subject", "value": msg["subject"]},
        ]
        if msg["date_header"] is not None:
            headers.append({"name": "Date", "value": msg["date_header"]})
        detail: dict[str, Any] = {
            "id": message_id,
            "snippet": msg["subject"],
            "payload": {"headers": headers},
        }
        if msg["internal_date"] is not _UNSET:
            detail["internalDate"] = msg["internal_date"]
        resp.json = AsyncMock(return_value=detail)
        return resp


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    state = {"now": STARTUP_MS}
    monkeypatch.setattr(coordinator_module, "utc_now_ms", lambda: state["now"])
    return state


@pytest.fixture
def gmail() -> FakeGmail:
    return FakeGmail()


@pytest.fixture
def events(hass: HomeAssistant) -> dict[str, list[dict[str, Any]]]:
    captured: dict[str, list[dict[str, Any]]] = {"email": [], "otp": []}

    @callback
    def _email(event: Event) -> None:
        captured["email"].append(dict(event.data))

    @callback
    def _otp(event: Event) -> None:
        captured["otp"].append(dict(event.data))

    hass.bus.async_listen(EVENT_GMAIL_NEW_EMAIL, _email)
    hass.bus.async_listen(EVENT_GMAIL_NEW_OTP, _otp)
    return captured


@pytest.fixture(autouse=True)
async def cancel_timers(hass: HomeAssistant):
    yield
    for coordinator in created:
        coordinator.cancel_queue_task()
    created.clear()


@pytest.fixture
def make_coordinator(hass: HomeAssistant, gmail: FakeGmail, clock: dict[str, int]):
    def _make(**options: Any) -> GmailDataUpdateCoordinator:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="watermark@example.com",
            title="watermark@example.com",
            data={"auth_implementation": "google"},
            options={CONF_QUEUE_DWELL_TIME: 0, CONF_EXTRACT_OTP: True, **options},
        )
        entry.add_to_hass(hass)
        session = MagicMock()
        session.async_request = AsyncMock(side_effect=gmail.request)
        coordinator = GmailDataUpdateCoordinator(hass, entry, session, timedelta(seconds=60))
        created.append(coordinator)
        return coordinator

    return _make


async def drain_queue(hass: HomeAssistant, coordinator: GmailDataUpdateCoordinator) -> None:
    if coordinator._queue_task is not None:
        await coordinator._queue_task
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant, coordinator: GmailDataUpdateCoordinator) -> None:
    await coordinator._async_update_data()
    await drain_queue(hass, coordinator)


def ids(captured: list[dict[str, Any]]) -> list[str]:
    return [event["message_id"] for event in captured]


async def test_mark_as_read_exposes_old_unread_without_replay(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator, clock
) -> None:
    for i in range(30):
        gmail.add(
            f"old_{i:02d}",
            str(STARTUP_MS - (i + 1) * MINUTE_MS),
            subject=f"Your verification code is {100000 + i}",
        )
    coordinator = make_coordinator()

    clock["now"] += MINUTE_MS
    await poll(hass, coordinator)
    assert events["email"] == []
    assert len(coordinator.recent_emails) == 20
    assert coordinator.last_message.message_id == "old_00"

    gmail.mark_read([f"old_{i:02d}" for i in range(10)])
    clock["now"] += MINUTE_MS
    await poll(hass, coordinator)
    assert events["email"] == []
    assert events["otp"] == []
    for i in range(20, 30):
        assert f"old_{i:02d}" in coordinator._seen_message_ids

    gmail.add("new_1", str(clock["now"]), subject="Your verification code is 424242")
    clock["now"] += MINUTE_MS
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["new_1"]
    assert ids(events["otp"]) == ["new_1"]


async def test_bulk_cleanup_of_100_messages_does_not_replay(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator, clock
) -> None:
    for i in range(160):
        gmail.add(f"old_{i:03d}", str(STARTUP_MS - (i + 1) * MINUTE_MS))
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.mark_read([f"old_{i:03d}" for i in range(100)])
    await poll(hass, coordinator)
    gmail.delete([f"old_{i:03d}" for i in range(100, 140)])
    await poll(hass, coordinator)
    assert events["email"] == []

    gmail.add("fresh", str(STARTUP_MS + 5 * MINUTE_MS))
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["fresh"]


async def test_exposed_message_older_than_watermark_is_dropped(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator, clock
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("after_start_hidden", str(STARTUP_MS + MINUTE_MS), hidden=True)
    newest = STARTUP_MS + MINUTE_MS + LATE_ARRIVAL_GRACE_MS + MINUTE_MS
    gmail.add("latest", str(newest))
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["latest"]
    assert coordinator._watermark_ms == newest

    gmail.messages["after_start_hidden"]["hidden"] = False
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["latest"]
    assert "after_start_hidden" in coordinator._seen_message_ids


async def test_late_visible_message_within_grace_is_delivered(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("slow_index", str(STARTUP_MS + 2 * MINUTE_MS), hidden=True)
    gmail.add("fast_index", str(STARTUP_MS + 3 * MINUTE_MS))
    await poll(hass, coordinator)
    gmail.messages["slow_index"]["hidden"] = False
    await poll(hass, coordinator)
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["fast_index", "slow_index"]


@pytest.mark.parametrize(
    "date_header",
    [None, "not a date at all", "Fri, 01 Jan 2100 00:00:00 +0000", "Sat, 01 Jan 2000 00:00:00 +0000"],
)
async def test_new_message_with_broken_date_header_fires_once(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator, date_header
) -> None:
    gmail.add("backlog", str(STARTUP_MS - MINUTE_MS), date_header="Fri, 01 Jan 2100 00:00:00 +0000")
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("new", str(STARTUP_MS + MINUTE_MS), date_header=date_header)
    await poll(hass, coordinator)
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["new"]


async def test_equal_timestamps_and_burst_across_pages(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator
) -> None:
    coordinator = make_coordinator(**{CONF_EXTRACT_OTP: False})
    await poll(hass, coordinator)

    same_ms = str(STARTUP_MS + MINUTE_MS)
    gmail.add("tie_b", same_ms)
    gmail.add("tie_a", same_ms)
    await poll(hass, coordinator)
    assert sorted(ids(events["email"])) == ["tie_a", "tie_b"]

    gmail.add("tie_c", same_ms)
    await poll(hass, coordinator)
    assert ids(events["email"])[-1] == "tie_c"

    burst = [f"burst_{i:02d}" for i in range(45)]
    for i, mid in enumerate(burst):
        gmail.add(mid, str(STARTUP_MS + 2 * MINUTE_MS + i))
    await poll(hass, coordinator)
    await poll(hass, coordinator)
    assert ids(events["email"])[3:] == burst
    assert len(ids(events["email"])) == len(set(ids(events["email"])))


async def test_evicted_seen_ids_stay_protected(
    hass: HomeAssistant,
    gmail: FakeGmail,
    events,
    make_coordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "MAX_SEEN_CACHE_SIZE", 3)
    for i in range(10):
        gmail.add(f"old_{i}", str(STARTUP_MS - (i + 1) * MINUTE_MS))
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("delivered", str(STARTUP_MS + MINUTE_MS))
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["delivered"]

    for i in range(5):
        gmail.add(f"filler_{i}", str(STARTUP_MS + 2 * MINUTE_MS + i))
    await poll(hass, coordinator)
    assert "delivered" not in coordinator._seen_message_ids
    assert "old_0" not in coordinator._seen_message_ids

    gmail.add("later", str(STARTUP_MS + MINUTE_MS + LATE_ARRIVAL_GRACE_MS * 2))
    await poll(hass, coordinator)
    assert "delivered" not in coordinator._recent_accepted
    await poll(hass, coordinator)
    await poll(hass, coordinator)
    assert ids(events["email"]) == (
        ["delivered"] + [f"filler_{i}" for i in range(5)] + ["later"]
    )


async def test_startup_race_message_is_delivered_but_backlog_is_not(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator
) -> None:
    gmail.add("backlog", str(STARTUP_MS - 1))
    coordinator = make_coordinator()
    gmail.add("arrived_before_first_poll", str(STARTUP_MS + 1))
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["arrived_before_first_poll"]


async def test_failed_detail_fetch_is_retried_without_duplicates(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("ok", str(STARTUP_MS + MINUTE_MS))
    gmail.add("flaky", str(STARTUP_MS + 2 * MINUTE_MS))
    gmail.detail_failures["flaky"] = [503]
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert "flaky" not in coordinator._seen_message_ids
    assert "ok" not in coordinator._seen_message_ids
    assert coordinator._watermark_ms == STARTUP_MS

    await poll(hass, coordinator)
    await poll(hass, coordinator)
    assert sorted(ids(events["email"])) == ["flaky", "ok"]


async def test_failed_fetch_keeps_its_cutoff_while_watermark_moves(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("forbidden_once", str(STARTUP_MS + MINUTE_MS))
    gmail.detail_failures["forbidden_once"] = [403]
    gmail.add("much_later", str(STARTUP_MS + LATE_ARRIVAL_GRACE_MS * 4))
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["much_later"]
    assert "forbidden_once" not in coordinator._seen_message_ids

    await poll(hass, coordinator)
    assert ids(events["email"]) == ["much_later", "forbidden_once"]


async def test_permanently_failing_fetch_is_given_up(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("broken", str(STARTUP_MS + MINUTE_MS))
    gmail.detail_failures["broken"] = [404] * 10
    for _ in range(3):
        await poll(hass, coordinator)
    assert "broken" in coordinator._seen_message_ids
    assert "broken" not in coordinator._retry_state
    assert events["email"] == []


@pytest.mark.parametrize("internal_date", [_UNSET, None, "", "abc", "-5", "0", 12.5, True])
async def test_missing_or_invalid_internal_date_is_not_announced(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator, internal_date
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    gmail.add("no_timestamp", internal_date, date_header="Fri, 01 Jan 2100 00:00:00 +0000")
    await poll(hass, coordinator)
    await poll(hass, coordinator)
    assert events["email"] == []
    assert "no_timestamp" in coordinator._seen_message_ids
    assert coordinator._watermark_ms == STARTUP_MS


async def test_query_change_baselines_newly_matched_history(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator, clock
) -> None:
    coordinator = make_coordinator()
    clock["now"] += 10 * MINUTE_MS
    await poll(hass, coordinator)

    for i in range(5):
        gmail.add(
            f"receipt_{i}",
            str(STARTUP_MS + (i + 1) * MINUTE_MS),
            labels=("Receipts",),
        )
    hass.config_entries.async_update_entry(
        coordinator._entry, options={**coordinator._entry.options, CONF_QUERY: "label:Receipts"}
    )
    clock["now"] += MINUTE_MS
    await poll(hass, coordinator)
    assert events["email"] == []

    gmail.add("receipt_new", str(clock["now"]), labels=("Receipts",))
    clock["now"] += MINUTE_MS
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["receipt_new"]


async def test_unrelated_option_change_does_not_suppress_arrivals(
    hass: HomeAssistant, gmail: FakeGmail, events, make_coordinator, clock
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    hass.config_entries.async_update_entry(
        coordinator._entry, options={**coordinator._entry.options, CONF_EXTRACT_OTP: False}
    )
    gmail.add("arrival", str(STARTUP_MS + MINUTE_MS))
    clock["now"] += 2 * MINUTE_MS
    await poll(hass, coordinator)
    assert ids(events["email"]) == ["arrival"]


async def test_simulated_email_still_fires(
    hass: HomeAssistant, events, make_coordinator
) -> None:
    coordinator = make_coordinator()
    await poll(hass, coordinator)

    sim_id = await coordinator.async_simulate_email(subject="Simulated", otp_code="")
    await drain_queue(hass, coordinator)
    assert ids(events["email"]) == [sim_id]
