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

from custom_components.safe_intercom.const import DOMAIN

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
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Intercom"
    assert result["data"] == USER_INPUT
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
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


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
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.10"
    assert entry.data[CONF_PORT] == 8080
