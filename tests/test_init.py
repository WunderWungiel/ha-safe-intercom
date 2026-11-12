"""Tests for SAFE Intercom setup."""

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.const import DOMAIN

from .const import (
    HOST,
    MAC,
    NETWORK_RESPONSE,
    RTSP_RESPONSE,
    SYSTEMINFO_RESPONSE,
    controller_response,
    hanging_stream,
)

NETWORK_URL = f"http://{HOST}/cgi-bin/network_cgi?action=get"
SYSTEMINFO_URL = f"http://{HOST}/cgi-bin/systeminfo_cgi"
RTSP_URL = f"http://{HOST}/cgi-bin/rtsp_cgi?action=get"
CONTROLLER_URL = f"http://{HOST}/cgi-bin/controller_cgi?action=get"
ALARM_URL = (
    f"http://{HOST}/cgi-bin/alarmchangestate_cgi?channel=0&parameter=SensorOutAlarm"
)


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
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
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
    """Unreachable devices retry; rejected credentials start reauthentication."""
    aioclient_mock.get(NETWORK_URL, **mock_kwargs)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    if state is ConfigEntryState.SETUP_ERROR:
        assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    else:
        assert flows == []


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
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, **mock_kwargs)

    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is state


async def test_setup_wrong_device_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A different intercom at the host is not set up as this one."""
    aioclient_mock.get(
        NETWORK_URL,
        text=NETWORK_RESPONSE.replace("00-5d-20-a0-35-12", "00-11-22-33-44-55"),
    )
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.async_entity_ids("switch") == []


async def test_device_info(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The device shows model, firmware and a link to its web interface."""
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, MAC), entry.entry_id
    )
    assert device is not None
    assert device.model == "B1710"
    assert device.sw_version == "2.0.0.2.52.18"
    assert device.hw_version == "Hi3518 IP Camera"
    assert device.configuration_url == f"http://{HOST}:80"


async def test_setup_without_system_info(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Firmware without systeminfo_cgi still sets up, just without a model."""
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, status=404)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, MAC), entry.entry_id
    )
    assert device is not None
    assert device.model is None
