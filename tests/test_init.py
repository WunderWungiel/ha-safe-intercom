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

NETWORK_URL = "http://192.0.2.10/cgi-bin/network_cgi?action=get"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry for a device on the default port."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:5d:20:a0:35:12",
        data={
            CONF_HOST: "192.0.2.10",
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
    aioclient_mock.get(NETWORK_URL, text="MACAddress=00-5d-20-a0-35-12\n")

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED

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
