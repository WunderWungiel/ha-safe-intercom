"""Controller state coordinator for the SAFE Intercom integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ControllerInfo,
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomError,
)
from .const import DOMAIN

if TYPE_CHECKING:
    from . import SafeIntercomConfigEntry

_LOGGER = logging.getLogger(__name__)


class SafeIntercomCoordinator(DataUpdateCoordinator[ControllerInfo]):
    """Holds the controller state.

    Not polled: refreshed on setup and after commands. The event stream will
    push updates later.
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
            update_interval=None,
        )
        self.client = client

    async def _async_update_data(self) -> ControllerInfo:
        """Fetch the controller state."""
        try:
            return await self.client.async_get_controller()
        except SafeIntercomAuthError as err:
            # TODO: switch to ConfigEntryAuthFailed once a reauth flow exists.
            raise ConfigEntryError("Invalid credentials") from err
        except SafeIntercomError as err:
            raise UpdateFailed(str(err)) from err
