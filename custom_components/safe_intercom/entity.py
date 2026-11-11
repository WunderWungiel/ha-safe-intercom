"""Base entity for the SAFE Intercom integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SafeIntercomCoordinator


class SafeIntercomEntity(CoordinatorEntity[SafeIntercomCoordinator]):
    """An entity belonging to a SAFE intercom device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SafeIntercomCoordinator, key: str) -> None:
        """Initialize the entity; `key` makes the unique ID unique per device."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        mac = entry.runtime_data.mac
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            manufacturer=MANUFACTURER,
            name=entry.title,
        )
