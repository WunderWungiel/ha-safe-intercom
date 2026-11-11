"""Controller relays as switches.

Every relay is a switch regardless of its Delay (see AGENTS.md). A momentary
relay (Delay > 0) turns on and then drops back to off by itself on the device.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import SafeIntercomConfigEntry
from .api import CONTROLLER_TYPE_3N, RelayInfo, SafeIntercomError
from .const import DOMAIN, SUPPORTED_RELAYS, enabled_relays
from .coordinator import SafeIntercomCoordinator
from .entity import SafeIntercomEntity

# Extra time after a momentary relay's Delay before re-reading its state.
MOMENTARY_REFRESH_MARGIN = 1

# Commands go to one device; keep them sequential.
PARALLEL_UPDATES = 1


def _relay_key(number: int) -> str:
    return f"relay_{number}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SafeIntercomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a switch per enabled controller relay."""
    data = entry.runtime_data
    controller = data.coordinator.data
    enabled = set(enabled_relays(entry.options))
    if controller.type != CONTROLLER_TYPE_3N:
        enabled.clear()

    # Drop entities of relays the user has since disabled, so they do not
    # linger as orphaned "unavailable" entities.
    registry = er.async_get(hass)
    wanted = {f"{data.mac}_{_relay_key(n)}" for n in enabled}
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == "switch"
            and reg_entry.unique_id.startswith(f"{data.mac}_relay_")
            and reg_entry.unique_id not in wanted
        ):
            registry.async_remove(reg_entry.entity_id)

    async_add_entities(
        SafeIntercomRelaySwitch(data.coordinator, relay.number)
        for relay in controller.relays
        if relay.number in enabled and relay.number in SUPPORTED_RELAYS
    )


class SafeIntercomRelaySwitch(SafeIntercomEntity, SwitchEntity):
    """A controller relay output."""

    _attr_translation_key = "relay"

    def __init__(self, coordinator: SafeIntercomCoordinator, number: int) -> None:
        """Initialize the switch for the 1-based relay `number`."""
        super().__init__(coordinator, _relay_key(number))
        self._number = number
        self._attr_translation_placeholders = {"number": str(number)}
        self._cancel_delayed_refresh: Callable[[], None] | None = None

    @property
    def _relay(self) -> RelayInfo | None:
        return self.coordinator.data.relay(self._number)

    @property
    def available(self) -> bool:
        """Unavailable if the device stops reporting this relay."""
        return super().available and self._relay is not None

    @property
    def is_on(self) -> bool | None:
        """Return the relay state reported by the device."""
        relay = self._relay
        return relay.is_on if relay else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Energise the relay."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Release the relay."""
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        self._cancel_pending_refresh()
        try:
            await self.coordinator.client.async_set_relay(self._number, on)
        except SafeIntercomError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="relay_command_failed",
                translation_placeholders={"number": str(self._number)},
            ) from err
        await self.coordinator.async_refresh()

        # A momentary relay drops back on its own; read its state once it has.
        relay = self._relay
        if on and relay is not None and relay.delay > 0:
            self._cancel_delayed_refresh = async_call_later(
                self.hass,
                relay.delay + MOMENTARY_REFRESH_MARGIN,
                self._async_delayed_refresh,
            )

    async def _async_delayed_refresh(self, _now: datetime) -> None:
        self._cancel_delayed_refresh = None
        await self.coordinator.async_refresh()

    @callback
    def _cancel_pending_refresh(self) -> None:
        if self._cancel_delayed_refresh is not None:
            self._cancel_delayed_refresh()
            self._cancel_delayed_refresh = None

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending refresh."""
        self._cancel_pending_refresh()
        await super().async_will_remove_from_hass()
