"""The SAFE Intercom integration."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_WEBHOOK_ID,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .api import (
    MotionSettings,
    RtspInfo,
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomConnectionError,
    SafeIntercomError,
    SystemInfo,
)
from .const import DEFAULT_PORT, DOMAIN, motion_issue_id
from .coordinator import SafeIntercomCoordinator
from .http_event import (
    WEBHOOK_ID_LENGTH,
    HttpEventStats,
    async_register_webhook,
    async_unregister_webhook,
    generate_webhook_id,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.SWITCH,
]


@dataclass
class SafeIntercomData:
    """Runtime data stored on the config entry."""

    client: SafeIntercomClient
    coordinator: SafeIntercomCoordinator
    mac: str
    """Normalised (colon-separated, lower-case) MAC, the base for unique IDs."""
    system: SystemInfo | None = None
    """Model and versions; None if the firmware does not report them."""
    rtsp: RtspInfo | None = None
    """RTSP settings read at setup; None if the firmware does not report them."""
    motion: MotionSettings | None = None
    """Motion detection settings read at setup; None if they could not be read."""
    http_events: HttpEventStats = field(default_factory=HttpEventStats)
    """Requests received by the call button webhook."""


type SafeIntercomConfigEntry = ConfigEntry[SafeIntercomData]


async def async_migrate_entry(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> bool:
    """Migrate an entry to the current version."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        # 1.2: webhook for the call button's HTTP event.
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_WEBHOOK_ID: generate_webhook_id()},
            minor_version=2,
        )
    if entry.minor_version < 3:
        # 1.3: HA's 64-character webhook IDs did not fit the device's Request field.
        data = dict(entry.data)
        if len(data[CONF_WEBHOOK_ID]) > WEBHOOK_ID_LENGTH:
            data[CONF_WEBHOOK_ID] = generate_webhook_id()
        hass.config_entries.async_update_entry(entry, data=data, minor_version=3)
    return True


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
        raise ConfigEntryAuthFailed("Invalid credentials") from err
    except SafeIntercomConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    mac = format_mac(network.mac_address)
    if entry.unique_id is not None and mac != entry.unique_id:
        # Possibly a temporary DHCP shuffle; Reconfigure fixes a permanent one.
        raise ConfigEntryNotReady(
            f"Expected intercom {entry.unique_id} at {entry.data[CONF_HOST]}, "
            f"found {mac}"
        )

    system: SystemInfo | None
    try:
        system = await client.async_get_system_info()
    except SafeIntercomAuthError as err:
        raise ConfigEntryAuthFailed("Invalid credentials") from err
    except SafeIntercomConnectionError as err:
        _LOGGER.debug("No system info from %s: %s", client.host, err)
        system = None

    rtsp: RtspInfo | None
    try:
        rtsp = await client.async_get_rtsp_info()
    except SafeIntercomError as err:
        # Includes auth errors: rtsp_cgi needs admin rights (spec §28), while
        # network_cgi above has already accepted the credentials.
        _LOGGER.debug("No RTSP settings from %s: %s", client.host, err)
        rtsp = None

    motion: MotionSettings | None
    try:
        motion = await client.async_get_motion_settings()
    except SafeIntercomError as err:
        # Includes auth errors: motion_cgi needs admin rights (spec §15).
        _LOGGER.debug("No motion settings from %s: %s", client.host, err)
        motion = None

    coordinator = SafeIntercomCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SafeIntercomData(
        client=client,
        coordinator=coordinator,
        mac=entry.unique_id or mac,
        system=system,
        rtsp=rtsp,
        motion=motion,
    )

    entry.async_create_background_task(
        hass, coordinator.async_run_event_stream(), "safe_intercom event stream"
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_webhook(hass, entry)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        async_unregister_webhook(hass, entry)
    return unloaded


async def async_remove_entry(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> None:
    """Drop the entry's repair issues."""
    ir.async_delete_issue(hass, DOMAIN, motion_issue_id(entry.entry_id))
