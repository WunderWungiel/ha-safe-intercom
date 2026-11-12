"""Config flow for the SAFE Intercom integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
import voluptuous as vol

from .api import (
    CONTROLLER_TYPE_3N,
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
    SUPPORTED_RELAYS,
    enabled_relays,
)

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
    return controller.type == CONTROLLER_TYPE_3N


def _relay_key(number: int) -> str:
    return f"relay_{number}"


def _controller_schema(controller: ControllerInfo, enabled: list[int]) -> vol.Schema:
    """One enable checkbox per relay the controller reports."""
    return vol.Schema(
        {
            vol.Required(
                _relay_key(relay.number), default=relay.number in enabled
            ): bool
            for relay in controller.relays
            if relay.number in SUPPORTED_RELAYS
        }
    )


def _controller_placeholders(controller: ControllerInfo) -> dict[str, str]:
    """Read-only info shown in the step description."""
    placeholders = {"controller_type": controller.type}
    for number in SUPPORTED_RELAYS:
        relay = controller.relay(number)
        placeholders[f"delay_{number}"] = str(relay.delay) if relay else "?"
    return placeholders


def _enabled_from_input(user_input: dict[str, Any]) -> list[int]:
    return [n for n in SUPPORTED_RELAYS if user_input.get(_relay_key(n))]


class SafeIntercomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SAFE Intercom."""

    VERSION = 1

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
            data_schema=_controller_schema(self._controller, list(SUPPORTED_RELAYS)),
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

    def _create_entry(self, enabled: list[int]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._title,
            data=self._data,
            options={CONF_ENABLED_RELAYS: enabled},
        )


class SafeIntercomOptionsFlow(OptionsFlow):
    """Change which controller relays are exposed."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._controller: ControllerInfo | None = None

    async def async_step_init(
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
            self._controller = await entry.runtime_data.client.async_get_controller()
        except SafeIntercomError:
            return self.async_abort(reason="cannot_connect")
        if not _is_supported(self._controller):
            return self.async_abort(
                reason="unsupported_controller",
                description_placeholders={"controller_type": self._controller.type},
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_controller_schema(
                self._controller, enabled_relays(entry.options)
            ),
            description_placeholders=_controller_placeholders(self._controller),
        )
