import asyncio
import base64
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import email.header
from email.message import EmailMessage
import email.utils
import html
import logging
import mimetypes
from pathlib import Path
import random
import re
import secrets
import time
from typing import Any, NamedTuple

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ENABLE_WRITE,
    CONF_EXTRACT_OTP,
    CONF_OTP_EXPIRY_MINUTES,
    CONF_POLL_INTERVAL,
    CONF_QUERY,
    CONF_QUEUE_DWELL_TIME,
    DEFAULT_ENABLE_WRITE,
    DEFAULT_EXTRACT_OTP,
    DEFAULT_OTP_EXPIRY_MINUTES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_QUERY,
    DEFAULT_QUEUE_DWELL_TIME,
    DOMAIN,
    EVENT_GMAIL_NEW_EMAIL,
    EVENT_GMAIL_NEW_OTP,
    GMAIL_MESSAGES_URL,
    GMAIL_SEND_URL,
    LATE_ARRIVAL_GRACE_MS,
    LIST_PAGE_SIZE,
    MAX_BODY_PREVIEW_LENGTH,
    MAX_DETAIL_FETCH_ATTEMPTS,
    MAX_LIST_PAGES,
    MAX_RECENT_EMAILS,
    MAX_SEEN_CACHE_SIZE,
    SIMULATED_MESSAGE_PREFIX,
)
from .otp import extract_otp_code
from .repairs import async_create_issue, async_delete_issue

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GmailMessage:
    message_id: str
    sender: str
    sender_name: str
    sender_email: str
    subject: str
    body_preview: str
    received_time: str
    otp_code: str | None = None


class FetchedMessage(NamedTuple):
    message: GmailMessage
    internal_date_ms: int | None


def utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def parse_internal_date(value: Any) -> int | None:
    """Parse Gmail's top-level internalDate (epoch milliseconds, sent as a string)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return None
    return parsed if parsed > 0 else None


def decode_mime_header(header_value: str) -> str:
    if not header_value:
        return ""
    try:
        decoded_parts = email.header.decode_header(header_value)
        return str(email.header.make_header(decoded_parts))
    except Exception:
        return header_value


def parse_sender_components(raw_sender: str) -> tuple[str, str, str]:
    if not raw_sender:
        return "", "", ""
    name, address = email.utils.parseaddr(decode_mime_header(raw_sender))
    clean_name = name.strip("\"' ")
    clean_address = address.strip()
    return clean_name or clean_address, clean_name, clean_address


def sanitize_text(text: str, max_length: int = MAX_BODY_PREVIEW_LENGTH) -> str:
    if not text:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", text)
    unescaped = html.unescape(without_tags)
    without_urls = re.sub(r"https?://\S+|www\.\S+", "", unescaped)
    without_noise = re.sub(r"[-=_*~]{2,}", " ", without_urls)
    normalized = re.sub(r"\s+", " ", without_noise).strip()
    if len(normalized) > max_length:
        return normalized[:max_length].rstrip() + "..."
    return normalized


def extract_service_name(sender_name: str, sender_email: str) -> str:
    if sender_name:
        cleaned = re.split(r"[-–—:|]", sender_name)[0].strip()
        cleaned = re.sub(
            r"(?i)\b(team|support|security|service|account|alerts?|notifications?|no-?reply)\b",
            "",
            cleaned,
        ).strip()
        if cleaned:
            return cleaned
    if sender_email and "@" in sender_email:
        domain = sender_email.split("@", 1)[1].lower()
        parts = domain.split(".")
        if len(parts) >= 2:
            base = parts[-2]
            if base in ("co", "com", "org", "net", "gov", "edu") and len(parts) >= 3:
                base = parts[-3]
            return base.capitalize()
    return ""


def parse_email_date(raw_date: str) -> str:
    if not raw_date:
        return datetime.now(timezone.utc).isoformat()
    try:
        return email.utils.parsedate_to_datetime(raw_date).isoformat()
    except Exception:
        return raw_date


def decode_base64url_bytes(data_str: str) -> bytes:
    if not data_str:
        return b""
    padding = 4 - (len(data_str) % 4)
    if padding and padding < 4:
        data_str += "=" * padding
    return base64.urlsafe_b64decode(data_str.encode("ascii"))


def decode_base64url(data_str: str) -> str:
    try:
        return decode_base64url_bytes(data_str).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_mime_bodies_and_attachments(
    payload: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        mime_type = part.get("mimeType", "").lower()
        filename = part.get("filename", "")
        body = part.get("body", {})
        data = body.get("data")
        attachment_id = body.get("attachmentId")

        if filename or attachment_id:
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "size": body.get("size", 0),
                    "attachment_id": attachment_id or "",
                }
            )

        if mime_type == "text/plain" and data:
            text_parts.append(decode_base64url(data))
        elif mime_type == "text/html" and data:
            html_parts.append(decode_base64url(data))

        for subpart in part.get("parts", []):
            walk(subpart)

    walk(payload)
    return "\n".join(text_parts), "\n".join(html_parts), attachments


class GmailDataUpdateCoordinator(DataUpdateCoordinator[GmailMessage | None]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: OAuth2Session,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self._entry = entry
        self._session = session
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._initial_run: bool = True
        self._queue: deque[GmailMessage] = deque()
        self._active_message: GmailMessage | None = None
        self._last_message: GmailMessage | None = None
        self._recent_emails: deque[GmailMessage] = deque(maxlen=MAX_RECENT_EMAILS)
        self._queue_task: asyncio.Task[None] | None = None
        self._unread_count: int = 0
        self._last_polled: datetime | None = None
        self._latest_otp: GmailMessage | None = None
        self._otp_received_at: datetime | None = None
        self._otp_expiry_timer: asyncio.Task[None] | None = None
        self._simulated_messages: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # All freshness timestamps are UTC epoch milliseconds, the unit of Gmail's internalDate.
        self._started_at: int = utc_now_ms()
        self._baseline_ms: int = self._started_at
        self._watermark_ms: int = self._started_at
        self._recent_accepted: dict[str, int] = {}
        self._retry_state: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._active_query: str | None = None
        self._last_poll_started_ms: int | None = None

    @property
    def is_write_enabled(self) -> bool:
        return bool(
            self._entry.options.get(
                CONF_ENABLE_WRITE,
                self._entry.data.get(CONF_ENABLE_WRITE, DEFAULT_ENABLE_WRITE),
            )
        )

    @property
    def last_polled(self) -> datetime | None:
        return self._last_polled

    @property
    def unread_count(self) -> int:
        return self._unread_count

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    @property
    def last_message(self) -> GmailMessage | None:
        return self._last_message

    @property
    def recent_emails(self) -> list[GmailMessage]:
        return list(self._recent_emails)

    @property
    def latest_otp(self) -> GmailMessage | None:
        return self._latest_otp

    @property
    def otp_received_at(self) -> datetime | None:
        return self._otp_received_at

    @property
    def otp_expires_at(self) -> datetime | None:
        if self._otp_received_at is None:
            return None
        expiry_minutes = self._entry.options.get(
            CONF_OTP_EXPIRY_MINUTES, DEFAULT_OTP_EXPIRY_MINUTES
        )
        return self._otp_received_at + timedelta(minutes=expiry_minutes)

    @property
    def latest_otp_service_name(self) -> str:
        if self._latest_otp is None:
            return ""
        return extract_service_name(
            self._latest_otp.sender_name, self._latest_otp.sender_email
        )

    def cancel_queue_task(self) -> None:
        if self._queue_task is not None and not self._queue_task.done():
            self._queue_task.cancel()
            self._queue_task = None
        if self._otp_expiry_timer is not None and not self._otp_expiry_timer.done():
            self._otp_expiry_timer.cancel()
            self._otp_expiry_timer = None

    def _record_seen(self, message_id: str) -> None:
        self._seen_message_ids[message_id] = None
        if len(self._seen_message_ids) > MAX_SEEN_CACHE_SIZE:
            self._seen_message_ids.popitem(last=False)

    def _freshness_floor(self) -> int:
        return max(self._baseline_ms, self._watermark_ms - LATE_ARRIVAL_GRACE_MS)

    def _is_fresh(self, message_id: str, internal_date_ms: int, floor: int) -> bool:
        if message_id in self._recent_accepted:
            return False
        retry = self._retry_state.get(message_id)
        if retry is not None:
            floor = min(floor, retry[0])
        return internal_date_ms >= floor

    def _handle_auth_failure(self, error_message: str) -> None:
        async_create_issue(self.hass, self._entry)
        raise ConfigEntryAuthFailed(error_message)

    async def _fetch_message_details(self, message_id: str) -> FetchedMessage | None:
        url = f"{GMAIL_MESSAGES_URL}/{message_id}"
        params = {
            "format": "metadata",
            "metadataHeaders": ["From", "Subject", "Date"],
        }
        try:
            async with asyncio.timeout(10):
                resp = await self._session.async_request("GET", url, params=params)
                if resp.status == 401:
                    self._handle_auth_failure(
                        f"Authentication failed fetching message {message_id}: {resp.status}"
                    )
                if resp.status in (400, 403):
                    _LOGGER.warning(
                        "Error %s fetching message %s from Gmail API", resp.status, message_id
                    )
                    return None
                if resp.status in (429, 503):
                    raise UpdateFailed(
                        f"Rate limited or server error fetching message {message_id}: {resp.status}"
                    )
                if resp.status != 200:
                    return None
                data = await resp.json()
        except TimeoutError as err:
            raise UpdateFailed(f"Timeout fetching message {message_id}: {err}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Network error fetching message {message_id}: {err}") from err

        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in data.get("payload", {}).get("headers", [])
        }
        raw_from = headers.get("from", "")
        raw_subject = headers.get("subject", "")
        raw_date = headers.get("date", "")
        raw_snippet = data.get("snippet", "")

        display_sender, sender_name, sender_email = parse_sender_components(raw_from)
        subject = decode_mime_header(raw_subject)
        body_preview = sanitize_text(raw_snippet, MAX_BODY_PREVIEW_LENGTH)
        received_time = parse_email_date(raw_date)

        extract_otp = self._entry.options.get(
            CONF_EXTRACT_OTP, DEFAULT_EXTRACT_OTP
        )
        otp_code = (
            extract_otp_code(subject, raw_snippet) if extract_otp else None
        )

        message = GmailMessage(
            message_id=message_id,
            sender=display_sender,
            sender_name=sender_name,
            sender_email=sender_email,
            subject=subject,
            body_preview=body_preview,
            received_time=received_time,
            otp_code=otp_code,
        )
        return FetchedMessage(message, parse_internal_date(data.get("internalDate")))

    async def _fetch_messages_bounded(
        self, message_ids: list[str]
    ) -> list[FetchedMessage | None]:
        semaphore = asyncio.Semaphore(5)

        async def _fetch(msg_id: str) -> FetchedMessage | None:
            async with semaphore:
                return await self._fetch_message_details(msg_id)

        return list(await asyncio.gather(*(_fetch(mid) for mid in message_ids)))

    async def _list_messages(
        self, query: str, page_token: str | None
    ) -> dict[str, Any]:
        params = {"q": query, "maxResults": str(LIST_PAGE_SIZE)}
        if page_token:
            params["pageToken"] = page_token
        try:
            async with asyncio.timeout(10):
                resp = await self._session.async_request(
                    "GET", GMAIL_MESSAGES_URL, params=params
                )
                if resp.status == 401:
                    self._handle_auth_failure(
                        f"Authentication failed querying messages: {resp.status}"
                    )
                if resp.status == 400:
                    raise UpdateFailed(
                        f"Invalid search query or request parameters: {resp.status}"
                    )
                if resp.status == 403:
                    raise UpdateFailed(
                        f"Gmail API access forbidden or quota exceeded: {resp.status}"
                    )
                if resp.status in (429, 503):
                    raise UpdateFailed(
                        f"Gmail API rate limited or server error: {resp.status}"
                    )
                if resp.status != 200:
                    raise UpdateFailed(
                        f"Unexpected response querying messages: {resp.status}"
                    )
                return await resp.json()
        except ConfigEntryAuthFailed:
            async_create_issue(self.hass, self._entry)
            raise
        except TimeoutError as err:
            raise UpdateFailed(f"Timeout querying messages: {err}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Network error querying messages: {err}") from err

    async def _async_update_data(self) -> GmailMessage | None:
        if self._entry.data.get("simulated"):
            self._last_polled = datetime.now(timezone.utc)
            return self._active_message

        query = self._entry.options.get(CONF_QUERY, DEFAULT_QUERY)
        if self._active_query is not None and query != self._active_query:
            # Mail that only matches the new query is history, not new arrivals.
            self._baseline_ms = max(
                self._baseline_ms, self._last_poll_started_ms or utc_now_ms()
            )
        self._active_query = query

        poll_started_ms = utc_now_ms()
        floor = self._freshness_floor()
        listed_ids: set[str] = set()
        fresh: list[FetchedMessage] = []
        stale_ids: list[str] = []
        failed_ids: list[str] = []
        initial_history: list[GmailMessage] = []
        page_token: str | None = None

        for page_index in range(MAX_LIST_PAGES):
            data = await self._list_messages(query, page_token)
            page_ids = [
                summary["id"]
                for summary in data.get("messages", [])
                if summary.get("id")
            ]
            if page_index == 0:
                async_delete_issue(self.hass, self._entry.entry_id)
                self._unread_count = data.get("resultSizeEstimate", len(page_ids))
                self._last_polled = datetime.now(timezone.utc)

            candidates = [
                mid
                for mid in page_ids
                if mid not in listed_ids and mid not in self._seen_message_ids
            ]
            listed_ids.update(page_ids)
            records = await self._fetch_messages_bounded(candidates)

            page_has_recent = any(mid in self._recent_accepted for mid in page_ids)
            for mid, record in zip(candidates, records):
                if record is None:
                    failed_ids.append(mid)
                elif record.internal_date_ms is None:
                    _LOGGER.warning(
                        "Gmail message %s has no valid internalDate; not announcing it",
                        mid,
                    )
                    stale_ids.append(mid)
                elif self._is_fresh(mid, record.internal_date_ms, floor):
                    fresh.append(record)
                    page_has_recent = True
                else:
                    stale_ids.append(mid)
                    if self._initial_run:
                        initial_history.append(record.message)

            page_token = data.get("nextPageToken")
            if not page_token or not page_has_recent:
                break

        for mid in stale_ids:
            self._record_seen(mid)
            self._retry_state.pop(mid, None)
        for record in fresh:
            mid = record.message.message_id
            self._record_seen(mid)
            self._retry_state.pop(mid, None)
            self._recent_accepted[mid] = record.internal_date_ms
        for mid in failed_ids:
            retry_floor, attempts = self._retry_state.pop(mid, (floor, 0))
            attempts += 1
            if attempts >= MAX_DETAIL_FETCH_ATTEMPTS:
                _LOGGER.warning(
                    "Giving up on Gmail message %s after %s failed fetches", mid, attempts
                )
                self._record_seen(mid)
                continue
            self._retry_state[mid] = (min(retry_floor, floor), attempts)
            if len(self._retry_state) > MAX_SEEN_CACHE_SIZE:
                self._retry_state.popitem(last=False)

        if fresh:
            self._watermark_ms = max(
                self._watermark_ms, max(r.internal_date_ms for r in fresh)
            )
        new_floor = self._freshness_floor()
        self._recent_accepted = {
            mid: ts for mid, ts in self._recent_accepted.items() if ts >= new_floor
        }
        self._last_poll_started_ms = poll_started_ms

        if self._initial_run:
            for message in reversed(initial_history[:MAX_RECENT_EMAILS]):
                self._recent_emails.append(message)
                self._last_message = message
            self._initial_run = False

        fresh.sort(key=lambda r: (r.internal_date_ms, r.message.message_id))
        new_messages = [record.message for record in fresh]

        extract_otp = self._entry.options.get(
            CONF_EXTRACT_OTP, DEFAULT_EXTRACT_OTP
        )
        if extract_otp:
            otp_messages = [
                msg for msg in new_messages if msg.otp_code is not None
            ]
            routine_messages = [
                msg for msg in new_messages if msg.otp_code is None
            ]
            for message in reversed(otp_messages):
                self._queue.appendleft(message)
            for message in routine_messages:
                self._queue.append(message)
        else:
            for message in new_messages:
                self._queue.append(message)

        if self._queue and (self._queue_task is None or self._queue_task.done()):
            self._queue_task = self.hass.async_create_background_task(
                self._process_queue(), "gmail_queue_processor"
            )

        return self._active_message

    async def _process_queue(self) -> None:
        dwell_time = self._entry.options.get(
            CONF_QUEUE_DWELL_TIME, DEFAULT_QUEUE_DWELL_TIME
        )
        try:
            while self._queue:
                self._active_message = self._queue.popleft()
                self._last_message = self._active_message
                self._recent_emails.append(self._active_message)
                if self._active_message.otp_code:
                    self._set_latest_otp(self._active_message)
                event_payload = {
                    **asdict(self._active_message),
                    "entry_id": self._entry.entry_id,
                }
                self.hass.bus.async_fire(EVENT_GMAIL_NEW_EMAIL, event_payload)
                if self._active_message.otp_code:
                    self.hass.bus.async_fire(EVENT_GMAIL_NEW_OTP, event_payload)
                self.async_set_updated_data(self._active_message)
                await asyncio.sleep(dwell_time)
        finally:
            self._active_message = None
            self.async_set_updated_data(None)

    def _set_latest_otp(self, message: GmailMessage) -> None:
        self._latest_otp = message
        self._otp_received_at = datetime.now(timezone.utc)
        if self._otp_expiry_timer is not None and not self._otp_expiry_timer.done():
            self._otp_expiry_timer.cancel()
        expiry_minutes = self._entry.options.get(
            CONF_OTP_EXPIRY_MINUTES, DEFAULT_OTP_EXPIRY_MINUTES
        )
        self._otp_expiry_timer = self.hass.async_create_background_task(
            self._async_expire_otp(expiry_minutes * 60),
            "gmail_otp_expiry_timer",
        )

    async def _async_expire_otp(self, delay_seconds: int) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            self._latest_otp = None
            self._otp_received_at = None
            self.async_update_listeners()
        except asyncio.CancelledError:
            pass

    async def async_simulate_email(
        self,
        sender: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        otp_code: str | None = None,
    ) -> str:
        resolved_otp = (
            otp_code if otp_code is not None else f"{random.randint(100000, 999999)}"
        )
        resolved_sender = (
            sender or "Home Assistant Simulator <simulator@homeassistant.local>"
        )
        resolved_subject = (
            subject or f"Your verification code is {resolved_otp}"
        )
        resolved_body = (
            body or f"Use the code {resolved_otp} to verify your sign-in. Do not share this code."
        )

        message_id = f"{SIMULATED_MESSAGE_PREFIX}{secrets.token_hex(4)}"
        received_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        display_sender, sender_name, sender_email = parse_sender_components(resolved_sender)

        self._simulated_messages[message_id] = {
            "message_id": message_id,
            "thread_id": f"thread_{message_id}",
            "sender": display_sender,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "to": self._entry.title,
            "cc": "",
            "bcc": "",
            "subject": resolved_subject,
            "date": received_time,
            "labels": ["UNREAD", "INBOX", "SIMULATED"],
            "snippet": resolved_body[:MAX_BODY_PREVIEW_LENGTH],
            "text_body": resolved_body,
            "html_body": f"<p>{html.escape(resolved_body)}</p>",
            "otp_code": resolved_otp,
            "attachments": [],
            "headers": {"from": resolved_sender, "subject": resolved_subject},
        }
        if len(self._simulated_messages) > 50:
            self._simulated_messages.popitem(last=False)

        simulated_message = GmailMessage(
            message_id=message_id,
            sender=display_sender,
            sender_name=sender_name,
            sender_email=sender_email,
            subject=resolved_subject,
            body_preview=resolved_body[:MAX_BODY_PREVIEW_LENGTH],
            received_time=received_time,
            otp_code=resolved_otp,
        )

        if resolved_otp:
            self._queue.appendleft(simulated_message)
        else:
            self._queue.append(simulated_message)

        if self._queue_task is None or self._queue_task.done():
            self._queue_task = self.hass.async_create_background_task(
                self._process_queue(), "gmail_queue_processor"
            )

        return message_id

    async def async_get_full_email(self, message_id: str) -> dict[str, Any]:
        if message_id.startswith(SIMULATED_MESSAGE_PREFIX):
            if message_id in self._simulated_messages:
                return dict(self._simulated_messages[message_id])
            return {
                "message_id": message_id,
                "thread_id": f"thread_{message_id}",
                "sender": "Home Assistant Simulator <simulator@homeassistant.local>",
                "sender_name": "Home Assistant Simulator",
                "sender_email": "simulator@homeassistant.local",
                "to": self._entry.title,
                "cc": "",
                "bcc": "",
                "subject": "Simulated Test Email",
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "labels": ["UNREAD", "INBOX", "SIMULATED"],
                "snippet": "Simulated email payload for testing.",
                "text_body": "Simulated email payload for testing.",
                "html_body": "<p>Simulated email payload for testing.</p>",
                "otp_code": None,
                "attachments": [],
                "headers": {"from": "simulator@homeassistant.local", "subject": "Simulated Test Email"},
            }

        url = f"{GMAIL_MESSAGES_URL}/{message_id}"
        params = {"format": "full"}
        try:
            async with asyncio.timeout(15):
                resp = await self._session.async_request("GET", url, params=params)
                if resp.status == 401:
                    self._handle_auth_failure(
                        f"Authentication failed fetching full email {message_id}: {resp.status}"
                    )
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"Failed to fetch full email {message_id}: {resp.status}"
                    )
                data = await resp.json()
        except TimeoutError as err:
            raise HomeAssistantError(
                f"Timeout fetching full email {message_id}: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise HomeAssistantError(
                f"Network error fetching full email {message_id}: {err}"
            ) from err

        payload = data.get("payload", {})
        raw_headers = {
            header.get("name", ""): header.get("value", "")
            for header in payload.get("headers", [])
        }
        headers_lower = {name.lower(): value for name, value in raw_headers.items()}

        display_sender, sender_name, sender_email = parse_sender_components(
            headers_lower.get("from", "")
        )
        subject = decode_mime_header(headers_lower.get("subject", ""))
        received_time = parse_email_date(headers_lower.get("date", ""))
        text_body, html_body, attachments = extract_mime_bodies_and_attachments(payload)

        extract_otp = self._entry.options.get(
            CONF_EXTRACT_OTP, DEFAULT_EXTRACT_OTP
        )
        otp_code = (
            extract_otp_code(subject, text_body or data.get("snippet", ""))
            if extract_otp
            else None
        )

        return {
            "message_id": message_id,
            "thread_id": data.get("threadId", ""),
            "sender": display_sender,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "to": headers_lower.get("to", ""),
            "cc": headers_lower.get("cc", ""),
            "bcc": headers_lower.get("bcc", ""),
            "subject": subject,
            "date": received_time,
            "labels": data.get("labelIds", []),
            "snippet": data.get("snippet", ""),
            "text_body": text_body,
            "html_body": html_body,
            "otp_code": otp_code,
            "attachments": attachments,
            "headers": raw_headers,
        }

    async def async_modify_email(
        self,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_write_enabled:
            raise HomeAssistantError(
                "Write/modify access is not enabled for this Gmail account. "
                "Please re-authenticate to grant write permissions."
            )

        if message_id.startswith(SIMULATED_MESSAGE_PREFIX):
            return {
                "message_id": message_id,
                "labels": ["SIMULATED"],
            }

        url = f"{GMAIL_MESSAGES_URL}/{message_id}/modify"
        payload = {
            "addLabelIds": add_labels or [],
            "removeLabelIds": remove_labels or [],
        }
        try:
            async with asyncio.timeout(10):
                resp = await self._session.async_request("POST", url, json=payload)
                if resp.status == 401:
                    self._handle_auth_failure(
                        f"Authentication failed modifying email {message_id}: {resp.status}"
                    )
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"Failed modifying email {message_id}: {resp.status}"
                    )
                data = await resp.json()
        except TimeoutError as err:
            raise HomeAssistantError(
                f"Timeout modifying email {message_id}: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise HomeAssistantError(
                f"Network error modifying email {message_id}: {err}"
            ) from err

        return {
            "message_id": message_id,
            "labels": data.get("labelIds", []),
        }

    async def async_download_attachment(
        self, message_id: str, attachment_id: str
    ) -> bytes:
        url = f"{GMAIL_MESSAGES_URL}/{message_id}/attachments/{attachment_id}"
        try:
            async with asyncio.timeout(15):
                resp = await self._session.async_request("GET", url)
                if resp.status == 401:
                    self._handle_auth_failure(
                        f"Authentication failed downloading attachment: {resp.status}"
                    )
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"Failed downloading attachment: {resp.status}"
                    )
                data = await resp.json()
        except TimeoutError as err:
            raise HomeAssistantError(
                f"Timeout downloading attachment: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise HomeAssistantError(
                f"Network error downloading attachment: {err}"
            ) from err

        return decode_base64url_bytes(data.get("data", ""))

    async def async_send_email(
        self,
        to: str | list[str],
        subject: str,
        body: str | None = None,
        html_body: str | None = None,
        cc: str | list[str] | None = None,
        bcc: str | list[str] | None = None,
        reply_to: str | None = None,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_write_enabled:
            raise HomeAssistantError(
                "Write access is not enabled for this Gmail account. "
                "Please re-authenticate to grant write permissions."
            )

        if not body and not html_body:
            raise HomeAssistantError(
                "Either body or html_body must be provided to send an email"
            )

        msg = EmailMessage()
        msg["To"] = ", ".join(to) if isinstance(to, list) else to
        msg["Subject"] = subject
        if self._entry.title and "@" in self._entry.title:
            msg["From"] = self._entry.title
        if cc:
            msg["Cc"] = ", ".join(cc) if isinstance(cc, list) else cc
        if bcc:
            msg["Bcc"] = ", ".join(bcc) if isinstance(bcc, list) else bcc
        if reply_to:
            msg["Reply-To"] = reply_to

        if body and html_body:
            msg.set_content(body)
            msg.add_alternative(html_body, subtype="html")
        elif html_body:
            msg.set_content(html_body, subtype="html")
        else:
            msg.set_content(body or "")

        if attachments:
            for file_path_str in attachments:
                if not self.hass.config.is_allowed_path(file_path_str):
                    raise HomeAssistantError(
                        f"Access to file path '{file_path_str}' is forbidden"
                    )
                file_path = Path(file_path_str)
                if not file_path.is_file():
                    raise HomeAssistantError(
                        f"Attachment file not found: {file_path_str}"
                    )
                file_data = await self.hass.async_add_executor_job(
                    file_path.read_bytes
                )
                mime_type, _ = mimetypes.guess_type(file_path.name)
                if mime_type:
                    main_type, sub_type = mime_type.split("/", 1)
                else:
                    main_type, sub_type = "application", "octet-stream"
                msg.add_attachment(
                    file_data,
                    maintype=main_type,
                    subtype=sub_type,
                    filename=file_path.name,
                )

        raw_bytes = msg.as_bytes()
        raw_b64 = (
            base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
        )
        payload = {"raw": raw_b64}

        try:
            async with asyncio.timeout(30):
                resp = await self._session.async_request(
                    "POST", GMAIL_SEND_URL, json=payload
                )
                if resp.status in (401, 403):
                    error_text = await resp.text()
                    raise HomeAssistantError(
                        f"Authentication or permission error sending email ({resp.status}): {error_text}. Ensure write scope is authorized."
                    )
                if resp.status not in (200, 201):
                    error_text = await resp.text()
                    raise HomeAssistantError(
                        f"Failed to send email via Gmail API ({resp.status}): {error_text}"
                    )
                data = await resp.json()
                return {
                    "message_id": data.get("id"),
                    "thread_id": data.get("threadId"),
                }
        except TimeoutError as err:
            raise HomeAssistantError(
                f"Timeout connecting to Gmail API while sending email: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise HomeAssistantError(
                f"Network error connecting to Gmail API while sending email: {err}"
            ) from err
