from dataclasses import asdict
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATE_IDLE
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
            GmailLatestEmailSensor(coordinator, entry),
            GmailLatestOTPSensor(coordinator, entry),
            GmailUnreadCountSensor(coordinator, entry),
            GmailLastPolledSensor(coordinator, entry),
            GmailQueueSizeSensor(coordinator, entry),
        ]
    )


class GmailLatestEmailSensor(
    CoordinatorEntity[GmailDataUpdateCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "latest_email"

    def __init__(
        self,
        coordinator: GmailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_latest_email"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Gmail ({entry.title})",
            manufacturer="Google",
            model="Gmail API",
        )

    @property
    def native_value(self) -> str:
        if self.coordinator.data is not None:
            return self.coordinator.data.message_id
        return STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        message = self.coordinator.data or self.coordinator.last_message
        attributes = asdict(message) if message is not None else {}
        attributes["unread_count"] = self.coordinator.unread_count
        attributes["queue_size"] = self.coordinator.queue_size
        attributes["messages"] = [
            asdict(email_item)
            for email_item in reversed(self.coordinator.recent_emails)
        ]
        return attributes


class GmailLatestOTPSensor(
    CoordinatorEntity[GmailDataUpdateCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "latest_otp"

    def __init__(
        self,
        coordinator: GmailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_latest_otp"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Gmail ({entry.title})",
            manufacturer="Google",
            model="Gmail API",
        )

    @property
    def native_value(self) -> str:
        if (
            self.coordinator.latest_otp is not None
            and self.coordinator.latest_otp.otp_code
        ):
            return self.coordinator.latest_otp.otp_code
        return STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        otp_message = self.coordinator.latest_otp
        if otp_message is None:
            return {}

        expires_at = self.coordinator.otp_expires_at
        return {
            "sender": otp_message.sender,
            "sender_name": otp_message.sender_name,
            "sender_email": otp_message.sender_email,
            "subject": otp_message.subject,
            "received_time": otp_message.received_time,
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
            "service_name": self.coordinator.latest_otp_service_name,
            "message_id": otp_message.message_id,
            "body_preview": otp_message.body_preview,
        }


class GmailUnreadCountSensor(
    CoordinatorEntity[GmailDataUpdateCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "unread_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GmailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_unread_count"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Gmail ({entry.title})",
            manufacturer="Google",
            model="Gmail API",
        )

    @property
    def native_value(self) -> int:
        return self.coordinator.unread_count


class GmailLastPolledSensor(
    CoordinatorEntity[GmailDataUpdateCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "last_polled"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: GmailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_last_polled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Gmail ({entry.title})",
            manufacturer="Google",
            model="Gmail API",
        )

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_polled


class GmailQueueSizeSensor(
    CoordinatorEntity[GmailDataUpdateCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "queue_size"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GmailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_queue_size"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"Gmail ({entry.title})",
            manufacturer="Google",
            model="Gmail API",
        )

    @property
    def native_value(self) -> int:
        return self.coordinator.queue_size

