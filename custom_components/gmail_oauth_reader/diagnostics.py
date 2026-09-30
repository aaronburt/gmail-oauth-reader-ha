from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import GmailDataUpdateCoordinator

TO_REDACT = {
    "token",
    "access_token",
    "refresh_token",
    "client_id",
    "client_secret",
    "otp_code",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    coordinator: GmailDataUpdateCoordinator = (
        getattr(entry, "runtime_data", None) or hass.data[DOMAIN][entry.entry_id]
    )
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {
            "unread_count": coordinator.unread_count,
            "queue_size": coordinator.queue_size,
            "last_polled": (
                coordinator.last_polled.isoformat()
                if coordinator.last_polled is not None
                else None
            ),
            "has_active_message": coordinator.data is not None,
            "recent_emails_count": len(coordinator.recent_emails),
        },
    }
