"""Tests for the SAFE Intercom options flow (relay selection)."""

from unittest.mock import patch

import aiohttp
from homeassistant.components.http import ApiConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_WEBHOOK_ID,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.const import CONF_ENABLED_RELAYS, DOMAIN

from .const import (
    HOST,
    MAC,
    MOTION_URL,
    NETWORK_RESPONSE,
    RTSP_RESPONSE,
    SYSTEMINFO_RESPONSE,
    controller_response,
    hanging_stream,
    motion_response,
)

NETWORK_URL = f"http://{HOST}/cgi-bin/network_cgi?action=get"
SYSTEMINFO_URL = f"http://{HOST}/cgi-bin/systeminfo_cgi"
RTSP_URL = f"http://{HOST}/cgi-bin/rtsp_cgi?action=get"
CONTROLLER_URL = f"http://{HOST}/cgi-bin/controller_cgi?action=get"
ALARM_URL = f"http://{HOST}/cgi-bin/alarmchangestate_cgi?channel=0&parameter=MotionDetection;SensorInAlarm;SensorOutAlarm"

WEBHOOK_ID = "0123456789abcdef0123456789abcdef"


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
            CONF_WEBHOOK_ID: WEBHOOK_ID,
        },
        minor_version=2,
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
    aioclient_mock.get(MOTION_URL, text=motion_response())
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
    assert result["description_placeholders"]["relay_delays"] == (
        "1: 2 s, 2: 2 s, 3: 0 s"
    )
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
        ({"text": controller_response(controller_type="9")}, "unsupported_controller"),
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


async def test_options_shows_http_event_settings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Configure tells the user what to enter for the call button's HTTP event."""
    hass.config.internal_url = "http://192.0.2.50:8123"
    await _setup(hass, aioclient_mock, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    placeholders = result["description_placeholders"]
    assert placeholders["ha_protocol"] == "HTTP"
    assert placeholders["ha_host"] == "192.0.2.50:8123"
    assert placeholders["webhook_path"] == f"/api/webhook/{WEBHOOK_ID}"
    assert placeholders["call_parameter"] == "event=call"
    assert placeholders["controller_type"] == "3n"


async def test_options_http_event_settings_https(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """HTTPS and the default port are shown as the device expects them.

    With no local IP known, a host name is still better than nothing.
    """
    hass.config.internal_url = "https://ha.local"
    await _setup(hass, aioclient_mock, entry)
    hass.config.api = None

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["description_placeholders"]["ha_protocol"] == "HTTPS"
    assert result["description_placeholders"]["ha_host"] == "ha.local:443"


@pytest.mark.parametrize(
    ("internal_url", "use_ssl", "protocol"),
    [
        ("http://homeassistant.local:8123", False, "HTTP"),
        ("https://ha.example.com", True, "HTTPS"),
    ],
)
async def test_options_prefers_local_ip_over_host_name(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    internal_url: str,
    use_ssl: bool,
    protocol: str,
) -> None:
    """The intercom likely can't resolve host names, so suggest HA's own IP."""
    hass.config.internal_url = internal_url
    await _setup(hass, aioclient_mock, entry)
    hass.config.api = ApiConfig("192.0.2.60", "0.0.0.0", 8123, use_ssl)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["description_placeholders"]["ha_protocol"] == protocol
    assert result["description_placeholders"]["ha_host"] == "192.0.2.60:8123"


async def test_options_keeps_ip_internal_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """An internal URL that already uses an IP wins over the detected one."""
    hass.config.internal_url = "http://192.0.2.50:8123"
    await _setup(hass, aioclient_mock, entry)
    hass.config.api = ApiConfig("192.0.2.60", "0.0.0.0", 8123, False)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["description_placeholders"]["ha_host"] == "192.0.2.50:8123"


async def test_options_without_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Without a known local URL the form still opens, with a fallback hint."""
    await _setup(hass, aioclient_mock, entry)
    hass.config.api = None

    with patch(
        "custom_components.safe_intercom.config_flow.get_url",
        side_effect=NoURLAvailableError,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["ha_protocol"] == "HTTP"
    assert result["description_placeholders"]["ha_host"] == "<Home Assistant IP>:8123"


async def test_unsupported_controller_still_shows_http_event_settings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """The call button works with any controller, so its settings are shown."""
    await _setup(hass, aioclient_mock, entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(CONTROLLER_URL, text=controller_response(controller_type="9"))

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["reason"] == "unsupported_controller"
    assert result["description_placeholders"]["webhook_path"] == (
        f"/api/webhook/{WEBHOOK_ID}"
    )
