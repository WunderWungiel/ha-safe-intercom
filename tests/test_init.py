"""Tests for SAFE Intercom setup."""

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_WEBHOOK_ID,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.const import DOMAIN

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
    aioclient_mock.get(MOTION_URL, text=motion_response())
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
    aioclient_mock.get(MOTION_URL, text=motion_response())
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
    aioclient_mock.get(MOTION_URL, text=motion_response())
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
    aioclient_mock.get(MOTION_URL, text=motion_response())
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


def _mock_device(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    aioclient_mock.get(MOTION_URL, text=motion_response())
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)


async def test_migrate_adds_webhook_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Entries from before the call button get a webhook ID on setup."""
    assert (entry.version, entry.minor_version) == (1, 1)
    before = dict(entry.data)
    _mock_device(aioclient_mock)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    assert isinstance(webhook_id, str)
    assert len(webhook_id) >= 32
    assert {k: v for k, v in entry.data.items() if k != CONF_WEBHOOK_ID} == before


async def test_migrate_keeps_existing_webhook_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A current entry's webhook ID is left alone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Intercom",
        unique_id=MAC,
        minor_version=2,
        data={
            CONF_HOST: HOST,
            CONF_PORT: 80,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_WEBHOOK_ID: "existing",
        },
    )
    entry.add_to_hass(hass)
    _mock_device(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data[CONF_WEBHOOK_ID] == "existing"


async def test_migrate_refuses_future_version(hass: HomeAssistant) -> None:
    """An entry from a newer integration version is not downgraded."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, version=2, data={})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


def _mock_device_motion(aioclient_mock: AiohttpClientMocker, **motion) -> None:
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    aioclient_mock.get(MOTION_URL, **motion)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)


def _motion_issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"motion_detection_off_{entry.entry_id}"
    )


async def test_motion_detection_off_raises_issue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Motion detection turned off on the device is reported as a repair issue."""
    _mock_device_motion(aioclient_mock, text=motion_response("close"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.motion is not None
    assert entry.runtime_data.motion.enabled is False
    issue = _motion_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "motion_detection_off"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False

    await hass.config_entries.async_remove(entry.entry_id)
    assert _motion_issue(hass, entry) is None


async def test_motion_detection_on_clears_issue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Once detection is back on, the next setup removes the issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"motion_detection_off_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="motion_detection_off",
    )
    _mock_device_motion(aioclient_mock, text=motion_response("open"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _motion_issue(hass, entry) is None


async def test_motion_settings_unreadable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """motion_cgi needs admin rights; without them setup goes on, no issue."""
    _mock_device_motion(aioclient_mock, status=401)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.motion is None
    assert _motion_issue(hass, entry) is None


async def test_motion_issue_skipped_when_sensor_disabled(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A user who disabled the motion sensor does not want to hear about it."""
    entity_registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{MAC}_motion",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    _mock_device_motion(aioclient_mock, text=motion_response("close"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _motion_issue(hass, entry) is None


async def test_motion_issue_cleared_when_sensor_disabled(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Disabling the sensor does not reload the entry, but still drops the issue."""
    _mock_device_motion(aioclient_mock, text=motion_response("close"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _motion_issue(hass, entry) is not None

    entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{MAC}_motion"
    )
    assert entity_id is not None
    entity_registry.async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _motion_issue(hass, entry) is None
