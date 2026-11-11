"""Tests for the SAFE intercom HTTP client."""

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.api import (
    ControllerInfo,
    RelayInfo,
    SafeIntercomClient,
    SafeIntercomCommandError,
    SafeIntercomConnectionError,
)

from .const import HOST, controller_response

CONTROLLER_URL = f"http://{HOST}/cgi-bin/controller_cgi?action=get"
ALARMOUT_URL = f"http://{HOST}/cgi-bin/alarmout_cgi"


@pytest.fixture
def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> SafeIntercomClient:
    """A client using the mocked aiohttp session."""
    return SafeIntercomClient(
        async_get_clientsession(hass), HOST, 80, "admin", "secret"
    )


async def test_get_controller(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """The real 3n response parses into three relays with their delays."""
    aioclient_mock.get(CONTROLLER_URL, text=controller_response((0, 0, 1)))

    info = await client.async_get_controller()

    assert info == ControllerInfo(
        type="3n",
        relays=(
            RelayInfo(1, 2, False, False, True, False),
            RelayInfo(2, 2, False, False, False, False),
            RelayInfo(3, 0, True, False, False, False),
        ),
        play_sound=False,
    )
    assert info.relay(3).is_on
    assert info.relay(4) is None


@pytest.mark.parametrize(
    "text",
    ["Request failed:Param error\r\n", "Type=3n\r\nDelay1=x\r\nStatus1=0\r\n"],
)
async def test_get_controller_invalid(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient, text: str
) -> None:
    """Responses without a Type or with garbage delays are rejected."""
    aioclient_mock.get(CONTROLLER_URL, text=text)

    with pytest.raises(SafeIntercomConnectionError):
        await client.async_get_controller()


@pytest.mark.parametrize(
    ("number", "on", "output", "status"),
    [(1, True, "0", "1"), (3, True, "2", "1"), (3, False, "2", "0")],
)
async def test_set_relay(
    aioclient_mock: AiohttpClientMocker,
    client: SafeIntercomClient,
    number: int,
    on: bool,
    output: str,
    status: str,
) -> None:
    """Relay numbers are converted to the 0-based alarmout_cgi Output."""
    aioclient_mock.get(ALARMOUT_URL, text="OK\r\n")

    await client.async_set_relay(number, on)

    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert dict(url.query) == {"action": "set", "Output": output, "Status": status}
    assert "secret" not in str(url)
    assert headers["Authorization"] == aiohttp.encode_basic_auth("admin", "secret")


async def test_set_relay_refused(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """A `Request failed` body is reported as a command error."""
    aioclient_mock.get(ALARMOUT_URL, text="Request failed:Param error\r\n")

    with pytest.raises(SafeIntercomCommandError):
        await client.async_set_relay(1, True)


async def test_set_relay_invalid_number(client: SafeIntercomClient) -> None:
    """Relay numbers are 1-based."""
    with pytest.raises(ValueError):
        await client.async_set_relay(0, True)
