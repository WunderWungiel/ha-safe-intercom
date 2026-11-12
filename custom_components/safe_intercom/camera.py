"""Camera entities for the intercom's main and alternative video streams.

Both streams share the device's single JPEG snapshot (`images_cgi`). The RTSP
settings (on/off, port, authentication) are read from `rtsp_cgi` at setup;
reload the entry after changing them on the device.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import override

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SafeIntercomConfigEntry
from .api import STREAM_MAIN, STREAM_SUB, SafeIntercomAuthError, SafeIntercomError
from .const import DOMAIN
from .coordinator import SafeIntercomCoordinator
from .entity import SafeIntercomEntity

_LOGGER = logging.getLogger(__name__)

# Coordinator-backed and read-only; camera images are not throttled by this.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, slots=True)
class StreamDescription:
    """One of the device's video streams."""

    key: str
    stream: int
    enabled_default: bool


STREAMS = (
    StreamDescription("main_stream", STREAM_MAIN, enabled_default=True),
    # Disabled by default so a fresh install does not get two near-identical
    # cameras; useful for dashboards on slow links.
    StreamDescription("sub_stream", STREAM_SUB, enabled_default=False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SafeIntercomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a camera per video stream."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(SafeIntercomCamera(coordinator, desc) for desc in STREAMS)


class SafeIntercomCamera(SafeIntercomEntity, Camera):
    """One RTSP stream of the intercom, with the device's JPEG snapshot."""

    def __init__(
        self, coordinator: SafeIntercomCoordinator, description: StreamDescription
    ) -> None:
        """Initialize the camera."""
        SafeIntercomEntity.__init__(self, coordinator, description.key)
        Camera.__init__(self)
        data = coordinator.config_entry.runtime_data
        self._attr_translation_key = description.key
        self._attr_entity_registry_enabled_default = description.enabled_default
        self._stream_url = data.client.rtsp_url(data.rtsp, description.stream)
        """None when RTSP is turned off; then only snapshots are offered."""
        if self._stream_url is not None:
            self._attr_supported_features = CameraEntityFeature.STREAM

    @override
    async def stream_source(self) -> str | None:
        """Return the RTSP URL, or None if RTSP is turned off on the device."""
        return self._stream_url

    @override
    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a JPEG snapshot; the device has no size options."""
        entry = self.coordinator.config_entry
        try:
            return await entry.runtime_data.client.async_get_snapshot()
        except SafeIntercomError as err:
            if isinstance(err, SafeIntercomAuthError):
                entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="snapshot_failed"
            ) from err
