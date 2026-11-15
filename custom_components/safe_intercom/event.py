"""The call button as a doorbell event, fed by the HTTP event webhook."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SafeIntercomConfigEntry
from .const import signal_call
from .coordinator import SafeIntercomCoordinator
from .entity import SafeIntercomEntity

# Push-fed and read-only.
PARALLEL_UPDATES = 0

EVENT_RING = "ring"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SafeIntercomConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the call button event."""
    async_add_entities([SafeIntercomCallButton(entry.runtime_data.coordinator)])


class SafeIntercomCallButton(SafeIntercomEntity, EventEntity):
    """Fires `ring` when the intercom's call button is pressed."""

    _attr_translation_key = "call_button"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_RING]

    def __init__(self, coordinator: SafeIntercomCoordinator) -> None:
        """Initialize the call button."""
        super().__init__(coordinator, "call_button")

    @property
    def available(self) -> bool:
        """Always available while the entry is loaded.

        Presses arrive by webhook, whether or not `controller_cgi` answers.
        Following the coordinator would turn the entity unavailable and back
        on every outage, and state-trigger automations would take the return
        to the last `ring` for a new press.
        """
        return True

    async def async_added_to_hass(self) -> None:
        """Listen for presses reported by the webhook."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_call(self.coordinator.config_entry.entry_id),
                self._async_ring,
            )
        )

    @callback
    def _async_ring(self) -> None:
        self._trigger_event(EVENT_RING)
        self.async_write_ha_state()
