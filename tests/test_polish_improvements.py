from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gmail_oauth_reader.const import DOMAIN
from custom_components.gmail_oauth_reader.coordinator import GmailDataUpdateCoordinator


async def test_coordinator_update_result_size_estimate(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_oauth_session: MagicMock,
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "messages": [{"id": f"msg_{i}"} for i in range(20)],
            "resultSizeEstimate": 142,
        }
    )
    mock_oauth_session.async_request = AsyncMock(return_value=mock_resp)

    coordinator = GmailDataUpdateCoordinator(
        hass=hass,
        entry=mock_config_entry,
        session=mock_oauth_session,
        update_interval=timedelta(seconds=60),
    )

    await coordinator._async_update_data()
    assert coordinator.unread_count == 142


async def test_coordinator_http_400_raises_update_failed_not_auth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_oauth_session: MagicMock,
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_resp = MagicMock()
    mock_resp.status = 400
    mock_oauth_session.async_request = AsyncMock(return_value=mock_resp)

    coordinator = GmailDataUpdateCoordinator(
        hass=hass,
        entry=mock_config_entry,
        session=mock_oauth_session,
        update_interval=timedelta(seconds=60),
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()
    assert "Invalid search query" in str(exc_info.value)


async def test_coordinator_http_403_raises_update_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_oauth_session: MagicMock,
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_resp = MagicMock()
    mock_resp.status = 403
    mock_oauth_session.async_request = AsyncMock(return_value=mock_resp)

    coordinator = GmailDataUpdateCoordinator(
        hass=hass,
        entry=mock_config_entry,
        session=mock_oauth_session,
        update_interval=timedelta(seconds=60),
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()
    assert "forbidden or quota exceeded" in str(exc_info.value)


async def test_modify_email_requires_write_permission(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_oauth_session: MagicMock,
) -> None:
    mock_config_entry.add_to_hass(hass)
    coordinator = GmailDataUpdateCoordinator(
        hass=hass,
        entry=mock_config_entry,
        session=mock_oauth_session,
        update_interval=timedelta(seconds=60),
    )
    assert not coordinator.is_write_enabled

    with pytest.raises(HomeAssistantError) as exc_info:
        await coordinator.async_modify_email("real_msg_123", remove_labels=["UNREAD"])
    assert "Write/modify access is not enabled" in str(exc_info.value)
