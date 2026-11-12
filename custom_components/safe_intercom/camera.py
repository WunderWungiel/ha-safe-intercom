"""Camera entities for the intercom's main and alternative video streams.

Both streams share the device's single JPEG snapshot (`images_cgi`). The RTSP
port and on/off switch are read from `rtsp_cgi` whenever a stream is opened, so
changes made in the device's web interface are picked up without a reload.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SafeIntercomConfigEntry
from .api import (
    DEFAULT_RTSP_PORT,
    STREAM_MAIN,
    STREAM_SUB,
    RtspInfo,
    SafeIntercomError,
)
from .coordinator import SafeIntercomCoordinator
from .entity import SafeIntercomEntity

_LOGGER = logging.getLogger(__name__)

# One device serves all requests; snapshots of both cameras go one at a time.
PARALLEL_UPDATES = 1


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

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self, coordinator: SafeIntercomCoordinator, description: StreamDescription
    ) -> None:
        """Initialize the camera."""
        SafeIntercomEntity.__init__(self, coordinator, description.key)
        Camera.__init__(self)
        self._stream = description.stream
        self._attr_translation_key = description.key
        self._attr_entity_registry_enabled_default = description.enabled_default
        self._rtsp: RtspInfo | None = None
        """Last RTSP settings read from the device."""

    async def stream_source(self) -> str | None:
        """Return the RTSP URL, or None if RTSP is turned off on the device."""
        client = self.coordinator.config_entry.runtime_data.client
        try:
            self._rtsp = await client.async_get_rtsp_info()
        except SafeIntercomError as err:
            # Keep the last known settings; the stream itself will report
            # whether the device is really gone.
            _LOGGER.debug("Cannot read RTSP settings from %s: %s", client.host, err)
        if self._rtsp is not None and not self._rtsp.enabled:
            _LOGGER.debug("RTSP is turned off on %s", client.host)
            return None
        port = self._rtsp.port if self._rtsp is not None else DEFAULT_RTSP_PORT
        return client.rtsp_url(port, self._stream)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a JPEG snapshot; the device has no size options."""
        client = self.coordinator.config_entry.runtime_data.client
        try:
            return await client.async_get_snapshot()
        except SafeIntercomError as err:
            _LOGGER.debug("Snapshot from %s failed: %s", client.host, err)
            return None
