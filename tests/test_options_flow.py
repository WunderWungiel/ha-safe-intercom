"""Tests for the SAFE Intercom options flow (relay selection)."""

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.const import CONF_ENABLED_RELAYS, DOMAIN

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
    """A config entry with relays 1 and 3 enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        data={
            CONF_HOST: HOST,
            CONF_PORT: 80,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
        },
        options={CONF_ENABLED_RELAYS: [1, 3]},
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_options_flow_changes_relays_and_reloads(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """The form reflects current options and saving reloads the entry."""
    await _setup(hass, aioclient_mock, entry)
    old_data = entry.runtime_data

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["description_placeholders"]["delay_3"] == "0"
    assert {key: key.default() for key in result["data_schema"].schema} == {
        "relay_1": True,
        "relay_2": False,
        "relay_3": True,
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"relay_1": False, "relay_2": True, "relay_3": True}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_ENABLED_RELAYS: [2, 3]}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not old_data


async def test_options_flow_entry_not_loaded(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Without a loaded entry there is no live controller info to show."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


@pytest.mark.parametrize(
    ("mock_kwargs", "reason"),
    [
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"text": controller_response(controller_type="1")}, "unsupported_controller"),
    ],
)
async def test_options_flow_aborts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    mock_kwargs: dict,
    reason: str,
) -> None:
    """Device errors and unsupported controllers abort the options flow."""
    await _setup(hass, aioclient_mock, entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(CONTROLLER_URL, **mock_kwargs)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
