"""The SAFE Intercom integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SafeIntercomClient

# TODO: Platform.CAMERA, Platform.SWITCH, Platform.BUTTON, Platform.EVENT, ...
PLATFORMS: list[Platform] = []


@dataclass
class SafeIntercomData:
    """Runtime data stored on the config entry."""

    client: SafeIntercomClient


type SafeIntercomConfigEntry = ConfigEntry[SafeIntercomData]


async def async_setup_entry(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> bool:
    """Set up SAFE Intercom from a config entry."""
    client = SafeIntercomClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    # TODO: verify connectivity (raise ConfigEntryNotReady), start event stream.

    entry.runtime_data = SafeIntercomData(client=client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
