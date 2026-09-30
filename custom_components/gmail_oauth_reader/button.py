from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GmailDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GmailDataUpdateCoordinator = (
        getattr(entry, "runtime_data", None) or hass.data[DOMAIN][entry.entry_id]
    )
    async_add_entities(
        [
            GmailPollNowButton(coordinator, entry),
            GmailSimulateTestEmailButton(coordinator, entry),
        ]
    )


class GmailPollNowButton(CoordinatorEntity[GmailDataUpdateCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "poll_now"

    def __init__(
        self,
        coordinator: GmailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_poll_now"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Gmail ({entry.title})",
            manufacturer="Google",
            model="Gmail API",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class GmailSimulateTestEmailButton(
    CoordinatorEntity[GmailDataUpdateCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "simulate_test_email"
    _attr_icon = "mdi:email-fast-outline"

    def __init__(
        self,
        coordinator: GmailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_simulate_test_email"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Gmail ({entry.title})",
            manufacturer="Google",
            model="Gmail API",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_simulate_email()
