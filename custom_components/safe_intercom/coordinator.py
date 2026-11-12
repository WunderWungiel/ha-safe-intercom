"""Controller state coordinator for the SAFE Intercom integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ALARM_SENSOR_OUT,
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


class SafeIntercomCoordinator(DataUpdateCoordinator[ControllerInfo]):
    """Holds the controller state.

    Polled every 30 s as a safety net; also refreshed after commands and
    whenever the device's alarm stream reports an output change.
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

    async def _async_update_data(self) -> ControllerInfo:
        """Fetch the controller state."""
        try:
            return await self.client.async_get_controller()
        except SafeIntercomAuthError as err:
            # TODO: switch to ConfigEntryAuthFailed once a reauth flow exists.
            raise ConfigEntryError("Invalid credentials") from err
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
        Runs until cancelled.
        """
        backoff = STREAM_BACKOFF_MIN
        first = True

        def on_connect() -> None:
            nonlocal backoff
            backoff = STREAM_BACKOFF_MIN
            if self._stream_lost:
                _LOGGER.info("Alarm stream from %s connected", self.client.host)
            self._stream_connected = True

        while True:
            try:
                if not first:
                    # Events may have been missed while disconnected.
                    await self.async_refresh()
                first = False
                async for event in self.client.async_alarm_events(on_connect):
                    if event.alarm_type == ALARM_SENSOR_OUT:
                        await self.async_refresh()
                reason = "closed by the device"
            except SafeIntercomAuthError:
                _LOGGER.warning("Alarm stream rejected the credentials")
                reason = "credentials rejected"
            except SafeIntercomError as err:
                reason = str(err)
            if self._stream_connected:
                _LOGGER.info("Alarm stream lost: %s", reason)
                self._stream_connected = False
                self._stream_lost = True
            else:
                _LOGGER.debug("Alarm stream unavailable: %s", reason)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, STREAM_BACKOFF_MAX)
