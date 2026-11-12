"""Controller state coordinator for the SAFE Intercom integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ALARM_MOTION,
    ALARM_SENSOR_IN,
    ALARM_SENSOR_OUT,
    AlarmEvent,
    ControllerInfo,
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomError,
)
from .const import DOMAIN

if TYPE_CHECKING:
    from . import SafeIntercomConfigEntry

_LOGGER = logging.getLogger(__name__)

# After a command the device can lag before reporting the new state, so read
# again until it agrees: up to CONFIRM_ATTEMPTS reads, CONFIRM_INTERVAL apart.
CONFIRM_ATTEMPTS = 6
CONFIRM_INTERVAL = 0.5

# The alarm stream may not report every relay change (see AGENTS.md), so the
# controller is also read on this interval as a safety net.
FALLBACK_POLL_INTERVAL = timedelta(seconds=30)

# Event stream reconnect backoff, in seconds.
STREAM_BACKOFF_MIN = 1
STREAM_BACKOFF_MAX = 60
# Only a connection that stayed up this long resets the backoff. The device can
# accept the stream and close it at once; resetting on every connect would then
# reconnect (and re-read the controller) every second.
STREAM_STABLE_AFTER = 30


def _monotonic() -> float:
    return time.monotonic()


class SafeIntercomCoordinator(DataUpdateCoordinator[ControllerInfo]):
    """Holds the controller state, plus motion and inputs from the stream.

    The controller is polled every 30 s as a safety net; also refreshed after
    commands and whenever the device's alarm stream reports an output change.
    Motion and alarm inputs come only from the stream; they are forgotten
    whenever it disconnects, since "off" lines may have been missed.
    """

    config_entry: SafeIntercomConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SafeIntercomConfigEntry,
        client: SafeIntercomClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=FALLBACK_POLL_INTERVAL,
            always_update=False,
        )
        self.client = client
        self._stream_connected = False
        self._stream_lost = False
        self._motion_zones: set[int] = set()
        self._inputs: dict[int, bool] = {}

    @property
    def stream_connected(self) -> bool:
        """Whether the alarm stream is currently connected."""
        return self._stream_connected

    @property
    def motion(self) -> bool:
        """Whether any motion detection zone is active."""
        return bool(self._motion_zones)

    def input_state(self, number: int) -> bool | None:
        """State of the 1-based alarm input; None until its first event."""
        return self._inputs.get(number)

    def _handle_alarm(self, event: AlarmEvent) -> None:
        """Apply a motion or input event and notify listeners of a change."""
        if event.alarm_type == ALARM_MOTION:
            # A line without zones is taken as zone 0.
            zones = set(event.areas) or {0}
            before = set(self._motion_zones)
            if event.active:
                self._motion_zones |= zones
            else:
                self._motion_zones -= zones
            changed = before != self._motion_zones
        elif event.alarm_type == ALARM_SENSOR_IN and event.sensor is not None:
            number = event.sensor + 1
            changed = self._inputs.get(number) != event.active
            self._inputs[number] = event.active
        else:
            return
        if changed:
            self.async_update_listeners()

    def _set_stream_connected(self, connected: bool) -> None:
        """Track the stream state; a disconnect forgets the pushed state."""
        self._stream_connected = connected
        if not connected:
            self._motion_zones.clear()
            self._inputs.clear()
        self.async_update_listeners()

    async def _async_update_data(self) -> ControllerInfo:
        """Fetch the controller state."""
        try:
            return await self.client.async_get_controller()
        except SafeIntercomAuthError as err:
            raise ConfigEntryAuthFailed("Invalid credentials") from err
        except SafeIntercomError as err:
            raise UpdateFailed(str(err)) from err

    async def async_refresh_until(self, number: int, on: bool) -> None:
        """Refresh until relay `number` reports `on`, or attempts run out.

        Gives up quietly: a momentary relay may already have dropped back.
        """
        for attempt in range(CONFIRM_ATTEMPTS):
            if attempt:
                await asyncio.sleep(CONFIRM_INTERVAL)
            await self.async_refresh()
            relay = self.data.relay(number) if self.last_update_success else None
            if relay is None or relay.is_on == on:
                return

    async def async_run_event_stream(self) -> None:
        """Refresh on relay changes pushed by the device; reconnect forever.

        The stream only signals *that* something changed. The state itself
        always comes from `controller_cgi`, which is the verified source.
        Runs until cancelled, or until the device rejects the credentials
        (then a reauth flow is started instead).
        """
        backoff = STREAM_BACKOFF_MIN
        first = True
        connected_at: float | None = None

        def on_connect() -> None:
            nonlocal connected_at
            connected_at = _monotonic()
            if self._stream_lost:
                _LOGGER.info("Alarm stream from %s connected", self.client.host)
            self._set_stream_connected(True)

        while True:
            connected_at = None
            try:
                if not first:
                    # Events may have been missed while disconnected.
                    await self.async_refresh()
                first = False
                async for event in self.client.async_alarm_events(on_connect):
                    if event.alarm_type == ALARM_SENSOR_OUT:
                        await self.async_refresh()
                    else:
                        self._handle_alarm(event)
                reason = "closed by the device"
            except SafeIntercomAuthError:
                # Retrying cannot help; reauthentication reloads the entry,
                # which starts a new stream.
                _LOGGER.debug("Alarm stream rejected the credentials")
                if self._stream_connected:
                    self._set_stream_connected(False)
                self.config_entry.async_start_reauth(self.hass)
                return
            except SafeIntercomError as err:
                reason = str(err)
            if (
                connected_at is not None
                and _monotonic() - connected_at >= STREAM_STABLE_AFTER
            ):
                backoff = STREAM_BACKOFF_MIN
            if self._stream_connected:
                _LOGGER.info("Alarm stream lost: %s", reason)
                self._set_stream_connected(False)
                self._stream_lost = True
            else:
                _LOGGER.debug("Alarm stream unavailable: %s", reason)
            await self._async_stream_wait(backoff)
            backoff = min(backoff * 2, STREAM_BACKOFF_MAX)

    async def _async_stream_wait(self, seconds: float) -> None:
        """Wait before reconnecting the stream."""
        await asyncio.sleep(seconds)
