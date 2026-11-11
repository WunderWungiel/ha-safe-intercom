"""Tests for SAFE Intercom setup."""

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.const import DOMAIN

from .const import HOST, MAC, NETWORK_RESPONSE, controller_response, hanging_stream

NETWORK_URL = f"http://{HOST}/cgi-bin/network_cgi?action=get"
CONTROLLER_URL = f"http://{HOST}/cgi-bin/controller_cgi?action=get"
ALARM_URL = f"http://{HOST}/cgi-bin/alarmchangestate_cgi?channel=0"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry for a device on the default port."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        data={
            CONF_HOST: HOST,
            CONF_PORT: 80,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_and_unload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """The entry loads when the device answers, and unloads cleanly."""
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.mac == MAC
    assert entry.runtime_data.coordinator.data.type == "3n"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("mock_kwargs", "state"),
    [
        ({"exc": aiohttp.ClientConnectionError()}, ConfigEntryState.SETUP_RETRY),
        ({"status": 401}, ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_setup_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    mock_kwargs: dict,
    state: ConfigEntryState,
) -> None:
    """Unreachable devices retry; rejected credentials fail permanently."""
    aioclient_mock.get(NETWORK_URL, **mock_kwargs)

    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is state


@pytest.mark.parametrize(
    ("mock_kwargs", "state"),
    [
        ({"exc": aiohttp.ClientConnectionError()}, ConfigEntryState.SETUP_RETRY),
        ({"text": "Request failed:Param error"}, ConfigEntryState.SETUP_RETRY),
        ({"status": 401}, ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_setup_controller_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    mock_kwargs: dict,
    state: ConfigEntryState,
) -> None:
    """A failing controller fetch also blocks setup."""
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, **mock_kwargs)

    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is state
