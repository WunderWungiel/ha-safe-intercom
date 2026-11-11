"""The SAFE Intercom integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .api import (
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomConnectionError,
)
from .const import DEFAULT_PORT
from .coordinator import SafeIntercomCoordinator

# TODO: Platform.CAMERA, Platform.EVENT, ...
PLATFORMS: list[Platform] = [Platform.SWITCH]


@dataclass
class SafeIntercomData:
    """Runtime data stored on the config entry."""

    client: SafeIntercomClient
    coordinator: SafeIntercomCoordinator
    mac: str
    """Normalised (colon-separated, lower-case) MAC, the base for unique IDs."""


type SafeIntercomConfigEntry = ConfigEntry[SafeIntercomData]


async def async_setup_entry(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> bool:
    """Set up SAFE Intercom from a config entry."""
    client = SafeIntercomClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    try:
        network = await client.async_get_network_info()
    except SafeIntercomAuthError as err:
        # TODO: switch to ConfigEntryAuthFailed once a reauth flow exists.
        raise ConfigEntryError("Invalid credentials") from err
    except SafeIntercomConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = SafeIntercomCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    # TODO: start event stream.

    entry.runtime_data = SafeIntercomData(
        client=client, coordinator=coordinator, mac=format_mac(network.mac_address)
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> None:
    """Reload when the options change (e.g. the set of enabled relays)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
