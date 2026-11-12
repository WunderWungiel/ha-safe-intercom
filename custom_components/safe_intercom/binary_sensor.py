"""Motion and alarm inputs, pushed by the device's alarm stream.

Neither can be read on demand, so both are unavailable while the stream is
down, and inputs are unknown until their first event after (re)connecting.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SafeIntercomConfigEntry
from .const import DOMAIN, SUPPORTED_INPUTS, motion_issue_id
from .coordinator import SafeIntercomCoordinator
from .entity import SafeIntercomEntity

# Push-fed and read-only.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SafeIntercomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the motion sensor and one sensor per alarm input."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            SafeIntercomMotionSensor(coordinator),
            *(SafeIntercomInputSensor(coordinator, n) for n in SUPPORTED_INPUTS),
        ]
    )


class _StreamSensor(SafeIntercomEntity, BinarySensorEntity):
    """A binary sensor whose state only the alarm stream can tell."""

    @property
    def available(self) -> bool:
        """Available only while the stream is connected."""
        return super().available and self.coordinator.stream_connected


class SafeIntercomMotionSensor(_StreamSensor):
    """Motion detection in any of the camera's zones."""

    _attr_translation_key = "motion"
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: SafeIntercomCoordinator) -> None:
        """Initialize the motion sensor."""
        super().__init__(coordinator, "motion")

    @property
    def is_on(self) -> bool:
        """Whether motion is detected."""
        return self.coordinator.motion

    async def async_added_to_hass(self) -> None:
        """Warn if the device's motion detection is off.

        The sensor would otherwise show a confident "off" forever. The issue
        lives as long as this entity, so disabling the sensor also clears it.
        """
        await super().async_added_to_hass()
        entry = self.coordinator.config_entry
        issue_id = motion_issue_id(entry.entry_id)
        motion = entry.runtime_data.motion
        if motion is not None and not motion.enabled:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="motion_detection_off",
                translation_placeholders={"name": entry.title},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def async_will_remove_from_hass(self) -> None:
        """Drop the warning; it is raised again if the sensor comes back."""
        ir.async_delete_issue(
            self.hass, DOMAIN, motion_issue_id(self.coordinator.config_entry.entry_id)
        )
        await super().async_will_remove_from_hass()


class SafeIntercomInputSensor(_StreamSensor):
    """An alarm input; what is wired to it is unknown, so no device class."""

    _attr_translation_key = "alarm_input"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SafeIntercomCoordinator, number: int) -> None:
        """Initialize the sensor for the 1-based input `number`."""
        super().__init__(coordinator, f"alarm_input_{number}")
        self._number = number
        self._attr_translation_placeholders = {"number": str(number)}

    @property
    def is_on(self) -> bool | None:
        """Input state; None until the stream reports it."""
        return self.coordinator.input_state(self._number)
