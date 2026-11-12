"""Tests for the SAFE Intercom config flow."""

import aiohttp
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.const import CONF_ENABLED_RELAYS, DOMAIN

from .const import controller_response, hanging_stream

USER_INPUT = {
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 8080,
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
}
NETWORK_URL = "http://192.0.2.10:8080/cgi-bin/network_cgi?action=get"
NETWORK_RESPONSE = (
    "BootProto=none\r\n"
    "IPAddress=192.0.2.10\r\n"
    "MACAddress=00-5d-20-a0-35-12\r\n"
    "HostName=Intercom\r\n"
    "Web Port=8080\r\n"
)
CONTROLLER_URL = "http://192.0.2.10:8080/cgi-bin/controller_cgi?action=get"
ALARM_URL = "http://192.0.2.10:8080/cgi-bin/alarmchangestate_cgi?channel=0&parameter=SensorOutAlarm"


def _mock_device(
    aioclient_mock: AiohttpClientMocker, controller_type: str = "3n"
) -> None:
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(
        CONTROLLER_URL, text=controller_response(controller_type=controller_type)
    )
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)


async def _start_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Valid credentials create an entry keyed by the device MAC."""
    _mock_device(aioclient_mock)
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "controller"
    assert result["description_placeholders"] == {
        "controller_type": "3n",
        "delay_1": "2",
        "delay_2": "2",
        "delay_3": "0",
    }
    # All relays are offered and enabled by default.
    assert {key: key.default() for key in result["data_schema"].schema} == {
        "relay_1": True,
        "relay_2": True,
        "relay_3": True,
    }

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"relay_1": True, "relay_2": False, "relay_3": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Intercom"
    assert result["data"] == USER_INPUT
    assert result["options"] == {CONF_ENABLED_RELAYS: [1, 3]}
    assert result["result"].unique_id == "00:5d:20:a0:35:12"

    # The configured port is used and credentials go via Basic auth, not the URL.
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert url.port == 8080
    assert "secret" not in str(url)
    assert headers["Authorization"] == aiohttp.encode_basic_auth("admin", "secret")


async def test_user_flow_default_port(hass: HomeAssistant) -> None:
    """The port field defaults to 80."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    schema = result["data_schema"].schema
    port_key = next(key for key in schema if key == CONF_PORT)
    assert port_key.default() == 80


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"status": 403}, "invalid_auth"),
        ({"status": 500}, "cannot_connect"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"text": "Request failed:Param error"}, "cannot_connect"),
        ({"exc": ValueError()}, "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict,
    error: str,
) -> None:
    """Failures show an error on the form, and a retry can still succeed."""
    aioclient_mock.get(NETWORK_URL, **mock_kwargs)
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    _mock_device(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "controller"


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"text": "Request failed:Param error"}, "cannot_connect"),
    ],
)
async def test_user_flow_controller_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict,
    error: str,
) -> None:
    """A failing controller fetch shows an error on the connection form."""
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, **mock_kwargs)
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


async def test_user_flow_unsupported_controller(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Unknown controller types skip the relay step and enable nothing."""
    _mock_device(aioclient_mock, controller_type="1")
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_ENABLED_RELAYS: []}


async def test_user_flow_already_configured_updates_host(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Re-adding the same device aborts and updates its host and port."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:5d:20:a0:35:12",
        data={**USER_INPUT, CONF_HOST: "192.0.2.99", CONF_PORT: 80},
    )
    entry.add_to_hass(hass)
    _mock_device(aioclient_mock)
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.10"
    assert entry.data[CONF_PORT] == 8080


OTHER_NETWORK_RESPONSE = NETWORK_RESPONSE.replace(
    "00-5d-20-a0-35-12", "00-11-22-33-44-55"
)


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured intercom on port 8080."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="00:5d:20:a0:35:12", data=dict(USER_INPUT)
    )
    entry.add_to_hass(hass)
    return entry


async def _start_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    username_key = next(k for k in result["data_schema"].schema if k == CONF_USERNAME)
    assert username_key.default() == "admin"
    return result["flow_id"]


async def test_reauth_updates_credentials(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """New credentials are checked against the device and stored."""
    _mock_device(aioclient_mock)
    flow_id = await _start_reauth(hass, entry)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "admin", CONF_PASSWORD: "new"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.data[CONF_HOST] == USER_INPUT[CONF_HOST]
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == aiohttp.encode_basic_auth("admin", "new")


async def test_reauth_invalid_auth_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Wrong credentials show an error; correct ones finish the flow."""
    aioclient_mock.get(NETWORK_URL, status=401)
    flow_id = await _start_reauth(hass, entry)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "admin", CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    _mock_device(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "admin", CONF_PASSWORD: "new"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_wrong_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A different intercom at the host aborts and leaves the entry alone."""
    aioclient_mock.get(NETWORK_URL, text=OTHER_NETWORK_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    flow_id = await _start_reauth(hass, entry)
    old_data = dict(entry.data)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "admin", CONF_PASSWORD: "new"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert dict(entry.data) == old_data
