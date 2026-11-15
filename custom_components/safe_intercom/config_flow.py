"""Config flow for the SAFE Intercom integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_WEBHOOK_ID,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util.network import is_ip_address
import voluptuous as vol
from yarl import URL

from .api import (
    HTTP_EVENT_CALL,
    HTTP_EVENT_KEY,
    ControllerInfo,
    NetworkInfo,
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomError,
)
from .const import (
    CONF_ENABLED_RELAYS,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
    enabled_relays,
)
from .http_event import generate_webhook_id

if TYPE_CHECKING:
    from . import SafeIntercomConfigEntry

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

# Like the user step, but the password may be left empty to keep the stored one,
# so it never has to be sent back to the browser as a suggested value.
STEP_RECONFIGURE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)


async def async_validate_input(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[NetworkInfo, ControllerInfo]:
    """Connect with the given settings; return what the device reports.

    Raises the client's SafeIntercomError subclasses on failure.
    """
    client = SafeIntercomClient(
        async_get_clientsession(hass),
        data[CONF_HOST],
        data[CONF_PORT],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
    )
    return await client.async_get_network_info(), await client.async_get_controller()


def _error_key(err: Exception) -> str:
    """Map a validation failure to a form error key."""
    if isinstance(err, SafeIntercomAuthError):
        return "invalid_auth"
    if isinstance(err, SafeIntercomError):
        return "cannot_connect"
    _LOGGER.exception("Unexpected error while connecting to the intercom")
    return "unknown"


def _is_supported(controller: ControllerInfo) -> bool:
    """Return whether the integration knows how to set up this controller."""
    return bool(controller.supported_relays)


def _controller_schema(controller: ControllerInfo, enabled: list[int]) -> vol.Schema:
    """A compact checkbox list with one entry per relay the controller has."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_ENABLED_RELAYS,
                default=[
                    str(relay.number)
                    for relay in controller.supported_relays
                    if relay.number in enabled
                ],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=str(relay.number), label=f"Relay {relay.number}"
                        )
                        for relay in controller.supported_relays
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_ENABLED_RELAYS,
                )
            )
        }
    )


def _controller_placeholders(controller: ControllerInfo) -> dict[str, str]:
    """Read-only info shown in the step description."""
    return {
        "controller_type": controller.type,
        "relay_delays": ", ".join(
            f"{relay.number}: {relay.delay:g} s"
            for relay in controller.supported_relays
        ),
    }


def _http_event_values(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> dict[str, str]:
    """What to enter in the intercom's HTTP event settings for the call button.

    The device's host field takes `host[:port]` only, and the protocol is a
    separate choice, so the URL is split accordingly. An IP address is
    preferred: the intercom's firmware is unlikely to resolve `.local` or
    other local host names, so a configured internal URL with a host name
    gives way to the IP Home Assistant detected for itself.
    """
    url: URL | None
    try:
        url = URL(get_url(hass, allow_external=False, allow_cloud=False))
    except NoURLAvailableError:
        url = None
    api = hass.config.api
    if url is not None and url.host and is_ip_address(url.host):
        protocol, host = (url.scheme or "http").upper(), f"{url.host}:{url.port}"
    elif api is not None:
        protocol, host = (
            ("HTTPS" if api.use_ssl else "HTTP"),
            f"{api.local_ip}:{api.port}",
        )
    elif url is not None:
        protocol, host = (url.scheme or "http").upper(), f"{url.host}:{url.port}"
    else:
        protocol, host = "HTTP", "<Home Assistant IP>:8123"
    return {
        "protocol": protocol,
        "host": host,
        "request": webhook.async_generate_path(entry.data[CONF_WEBHOOK_ID]),
        "method": "GET",
        "call_parameter": f"{HTTP_EVENT_KEY}={HTTP_EVENT_CALL}",
    }


def _http_event_schema(values: dict[str, str]) -> vol.Schema:
    """Read-only fields the user copies into the intercom's web interface."""
    return vol.Schema(
        {
            vol.Optional(key, description={"suggested_value": value}): TextSelector(
                TextSelectorConfig(read_only=True)
            )
            for key, value in values.items()
        }
    )


def _enabled_from_input(user_input: dict[str, Any]) -> list[int]:
    return sorted(int(n) for n in user_input.get(CONF_ENABLED_RELAYS, []))


class SafeIntercomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SAFE Intercom."""

    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._title = ""
        self._controller: ControllerInfo | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: SafeIntercomConfigEntry,
    ) -> SafeIntercomOptionsFlow:
        """Return the options flow."""
        return SafeIntercomOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the connection step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info, controller = await async_validate_input(self.hass, user_input)
            except Exception as err:
                errors["base"] = _error_key(err)
            else:
                await self.async_set_unique_id(format_mac(info.mac_address))
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                    }
                )
                self._data = user_input
                self._title = info.host_name or user_input[CONF_HOST]
                self._controller = controller
                if not _is_supported(controller):
                    _LOGGER.warning(
                        "Controller type %r is not supported yet; no relays will be "
                        "set up",
                        controller.type,
                    )
                    return self._create_entry([])
                return await self.async_step_controller()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_controller(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which controller relays to expose."""
        assert self._controller is not None
        if user_input is not None:
            return self._create_entry(_enabled_from_input(user_input))

        return self.async_show_form(
            step_id="controller",
            data_schema=_controller_schema(
                self._controller,
                [relay.number for relay in self._controller.supported_relays],
            ),
            description_placeholders=_controller_placeholders(self._controller),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the device rejected the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials and check them against the same device."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info, _ = await async_validate_input(
                    self.hass,
                    {
                        CONF_PORT: DEFAULT_PORT,
                        **entry.data,
                        **user_input,
                    },
                )
            except Exception as err:
                errors["base"] = _error_key(err)
            else:
                await self.async_set_unique_id(format_mac(info.mac_address))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the host, port or credentials of the same intercom."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                **user_input,
                CONF_PASSWORD: user_input.get(CONF_PASSWORD)
                or entry.data[CONF_PASSWORD],
            }
            try:
                info, _ = await async_validate_input(self.hass, data)
            except Exception as err:
                errors["base"] = _error_key(err)
            else:
                await self.async_set_unique_id(format_mac(info.mac_address))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates=data)

        suggested = {
            key: value
            for key, value in (user_input or entry.data).items()
            if key != CONF_PASSWORD
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_RECONFIGURE_DATA_SCHEMA, suggested
            ),
            errors=errors,
        )

    def _create_entry(self, enabled: list[int]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._title,
            data={**self._data, CONF_WEBHOOK_ID: generate_webhook_id()},
            options={CONF_ENABLED_RELAYS: enabled},
        )


class SafeIntercomOptionsFlow(OptionsFlowWithReload):
    """Choose the exposed relays, or look up the call button settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        return self.async_show_menu(
            step_id="init", menu_options=["relays", "call_button"]
        )

    async def async_step_relays(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the relay selection, with live info from the device."""
        entry: SafeIntercomConfigEntry = self.config_entry

        if user_input is not None:
            return self.async_create_entry(
                data={
                    **entry.options,
                    CONF_ENABLED_RELAYS: _enabled_from_input(user_input),
                }
            )

        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        try:
            controller = await entry.runtime_data.client.async_get_controller()
        except SafeIntercomError:
            return self.async_abort(reason="cannot_connect")
        if not _is_supported(controller):
            return self.async_abort(
                reason="unsupported_controller",
                description_placeholders={"controller_type": controller.type},
            )

        return self.async_show_form(
            step_id="relays",
            data_schema=_controller_schema(controller, enabled_relays(entry.options)),
            description_placeholders=_controller_placeholders(controller),
        )

    async def async_step_call_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what to enter in the intercom's HTTP event settings.

        Nothing here is stored; submitting returns to the menu.
        """
        if user_input is not None:
            return await self.async_step_init()

        return self.async_show_form(
            step_id="call_button",
            data_schema=_http_event_schema(
                _http_event_values(self.hass, self.config_entry)
            ),
            last_step=False,
        )
