"""Tests for the SAFE Intercom relay switches."""

import asyncio
from datetime import timedelta

import aiohttp
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.safe_intercom import coordinator as coordinator_module
from custom_components.safe_intercom.const import CONF_ENABLED_RELAYS, DOMAIN
from custom_components.safe_intercom.coordinator import SafeIntercomCoordinator

from .const import (
    HOST,
    MAC,
    NETWORK_RESPONSE,
    SYSTEMINFO_RESPONSE,
    controller_response,
    hanging_stream,
)

NETWORK_URL = f"http://{HOST}/cgi-bin/network_cgi?action=get"
SYSTEMINFO_URL = f"http://{HOST}/cgi-bin/systeminfo_cgi"
CONTROLLER_URL = f"http://{HOST}/cgi-bin/controller_cgi?action=get"
ALARMOUT_URL = f"http://{HOST}/cgi-bin/alarmout_cgi"
ALARM_URL = (
    f"http://{HOST}/cgi-bin/alarmchangestate_cgi?channel=0&parameter=SensorOutAlarm"
)

RELAY_1 = "switch.intercom_relay_1"  # momentary, Delay1=2
RELAY_2 = "switch.intercom_relay_2"
RELAY_3 = "switch.intercom_relay_3"  # latching, Delay3=0


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry with relays 1 and 3 enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Intercom",
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


def _raise_connection_error(method, url, data):
    raise aiohttp.ClientConnectionError()


def _mock_device(
    aioclient_mock: AiohttpClientMocker,
    status: tuple[int, int, int] = (0, 0, 0),
    alarmout_text: str = "OK\r\n",
    controller_side_effect=None,
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    if controller_side_effect:
        aioclient_mock.get(CONTROLLER_URL, side_effect=controller_side_effect)
    else:
        aioclient_mock.get(CONTROLLER_URL, text=controller_response(status))
    aioclient_mock.get(ALARMOUT_URL, text=alarmout_text)


def _alarmout_calls(aioclient_mock: AiohttpClientMocker) -> list[dict[str, str]]:
    return [
        dict(url.query)
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path == "/cgi-bin/alarmout_cgi"
    ]


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    status: tuple[int, int, int] = (0, 0, 0),
) -> None:
    _mock_device(aioclient_mock, status)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_switches_for_enabled_relays(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Only enabled relays get a switch, with state from StatusN."""
    await _setup(hass, aioclient_mock, entry, status=(0, 1, 1))

    assert hass.states.get(RELAY_1).state == STATE_OFF
    assert hass.states.get(RELAY_2) is None
    assert hass.states.get(RELAY_3).state == STATE_ON
    assert hass.states.get(RELAY_1).attributes["friendly_name"] == "Intercom Relay 1"
    assert entity_registry.async_get(RELAY_3).unique_id == f"{MAC}_relay_3"


async def test_disabled_relay_entity_removed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A relay disabled in the options loses its registry entry."""
    entity_registry.async_get_or_create(
        SWITCH_DOMAIN, DOMAIN, f"{MAC}_relay_2", config_entry=entry
    )

    await _setup(hass, aioclient_mock, entry)

    assert (
        entity_registry.async_get_entity_id(SWITCH_DOMAIN, DOMAIN, f"{MAC}_relay_2")
        is None
    )
    assert entity_registry.async_get(RELAY_1) is not None


async def test_unsupported_controller_has_no_switches(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """No relays are set up for unknown controller types."""
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response(controller_type="1"))
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.async_entity_ids(SWITCH_DOMAIN) == []


async def test_latching_relay_on_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Turning a latching relay on/off drives Output=2 and refetches state."""
    await _setup(hass, aioclient_mock, entry)

    _mock_device(aioclient_mock, status=(0, 0, 1))
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: RELAY_3}, blocking=True
    )
    assert _alarmout_calls(aioclient_mock) == [
        {"action": "set", "Output": "2", "Status": "1"}
    ]
    assert hass.states.get(RELAY_3).state == STATE_ON

    _mock_device(aioclient_mock, status=(0, 0, 0))
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: RELAY_3}, blocking=True
    )
    assert _alarmout_calls(aioclient_mock) == [
        {"action": "set", "Output": "2", "Status": "0"}
    ]
    assert hass.states.get(RELAY_3).state == STATE_OFF

    # Latching relays schedule no follow-up refresh (checked before the poll).
    _mock_device(aioclient_mock, status=(0, 0, 1))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()
    assert hass.states.get(RELAY_3).state == STATE_OFF


async def test_momentary_relay_refreshes_after_delay(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A momentary relay's state is re-read once its Delay has passed."""
    await _setup(hass, aioclient_mock, entry)

    _mock_device(aioclient_mock, status=(1, 0, 0))
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: RELAY_1}, blocking=True
    )
    assert _alarmout_calls(aioclient_mock) == [
        {"action": "set", "Output": "0", "Status": "1"}
    ]
    assert hass.states.get(RELAY_1).state == STATE_ON

    # The device drops the relay after Delay1=2 s; we look again at 3 s.
    _mock_device(aioclient_mock, status=(0, 0, 0))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert hass.states.get(RELAY_1).state == STATE_ON

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert hass.states.get(RELAY_1).state == STATE_OFF


async def test_command_failure_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A refused command surfaces as a translated HomeAssistantError."""
    await _setup(hass, aioclient_mock, entry)
    _mock_device(aioclient_mock, alarmout_text="Request failed:Param error\r\n")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: RELAY_3}, blocking=True
        )
    assert exc_info.value.translation_key == "relay_command_failed"


async def test_command_waits_for_stale_state(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the device lags behind the command, keep reading until it agrees."""
    monkeypatch.setattr(coordinator_module, "CONFIRM_INTERVAL", 0)
    await _setup(hass, aioclient_mock, entry)

    reads = 0

    async def lagging(method, url, data):
        nonlocal reads
        reads += 1
        status = (0, 0, 1) if reads > 2 else (0, 0, 0)  # two stale reads
        return AiohttpClientMockResponse(method, url, text=controller_response(status))

    _mock_device(aioclient_mock, controller_side_effect=lagging)
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: RELAY_3}, blocking=True
    )

    assert hass.states.get(RELAY_3).state == STATE_ON
    assert reads == 3


async def test_refreshes_are_serialised(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Overlapping refreshes never have two controller reads in flight."""
    await _setup(hass, aioclient_mock, entry)
    in_flight = peak = 0

    async def slow(method, url, data):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return AiohttpClientMockResponse(method, url, text=controller_response())

    _mock_device(aioclient_mock, controller_side_effect=slow)
    coordinator = entry.runtime_data.coordinator
    await asyncio.gather(coordinator.async_refresh(), coordinator.async_refresh())

    assert peak == 1


async def test_stream_event_updates_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A relay changed elsewhere (app, button) shows up without any command."""
    status = (0, 0, 0)
    gate = asyncio.Event()

    async def controller(method, url, data):
        return AiohttpClientMockResponse(method, url, text=controller_response(status))

    async def stream(method, url, data):
        await gate.wait()
        return AiohttpClientMockResponse(
            method, url, text="2026-01-01;10:00:00;SensorOutAlarm;1;2\n"
        )

    _mock_device(aioclient_mock, controller_side_effect=controller)
    aioclient_mock.clear_requests()
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, side_effect=controller)
    aioclient_mock.get(ALARM_URL, side_effect=stream)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=False)
    assert hass.states.get(RELAY_3).state == STATE_OFF

    status = (0, 0, 1)  # changed on the device, no command from HA
    gate.set()
    for _ in range(10):  # let the background stream task run
        await asyncio.sleep(0)
    await hass.async_block_till_done(wait_background_tasks=False)

    assert hass.states.get(RELAY_3).state == STATE_ON


async def test_poll_recovers_after_failed_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A failed read makes switches unavailable; the next poll brings them back."""
    await _setup(hass, aioclient_mock, entry)
    _mock_device(aioclient_mock, controller_side_effect=_raise_connection_error)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(RELAY_3).state == STATE_UNAVAILABLE

    _mock_device(aioclient_mock, status=(0, 0, 1))
    async_fire_time_changed(
        hass, dt_util.utcnow() + coordinator_module.FALLBACK_POLL_INTERVAL
    )
    await hass.async_block_till_done(wait_background_tasks=False)
    assert hass.states.get(RELAY_3).state == STATE_ON


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == "reauth"
    ]


async def test_poll_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A password changed on the device starts exactly one reauth flow."""
    await _setup(hass, aioclient_mock, entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    aioclient_mock.get(CONTROLLER_URL, status=401)

    async_fire_time_changed(
        hass, dt_util.utcnow() + coordinator_module.FALLBACK_POLL_INTERVAL
    )
    await hass.async_block_till_done(wait_background_tasks=False)
    assert len(_reauth_flows(hass)) == 1
    assert hass.states.get(RELAY_3).state == STATE_UNAVAILABLE

    async_fire_time_changed(
        hass, dt_util.utcnow() + 2 * coordinator_module.FALLBACK_POLL_INTERVAL
    )
    await hass.async_block_till_done(wait_background_tasks=False)
    assert len(_reauth_flows(hass)) == 1


async def test_stream_auth_failure_starts_reauth_and_stops(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream rejected with 401 starts reauth and is not retried."""
    monkeypatch.setattr(coordinator_module, "STREAM_BACKOFF_MIN", 0)
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, status=401)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=False)
    for _ in range(10):
        await asyncio.sleep(0)
    await hass.async_block_till_done(wait_background_tasks=False)

    assert len(_reauth_flows(hass)) == 1
    stream_calls = [
        url
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path == "/cgi-bin/alarmchangestate_cgi"
    ]
    assert len(stream_calls) == 1


async def _stream_reconnect_delays(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
    clock: list[float],
    count: int,
) -> list[float]:
    """Set up with a stream the device closes at once; return the first waits.

    `clock` holds the monotonic times the coordinator reads, in order: one
    pair (connected, closed) per connection.
    """
    times = iter(clock)
    monkeypatch.setattr(coordinator_module, "_monotonic", lambda: next(times))
    delays: list[float] = []
    done = asyncio.Event()

    async def fake_wait(self: SafeIntercomCoordinator, seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == count:
            done.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(SafeIntercomCoordinator, "_async_stream_wait", fake_wait)

    async def stream(method, url, data):
        return AiohttpClientMockResponse(method, url, text="\n")

    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=stream)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=False)
    await asyncio.wait_for(done.wait(), timeout=1)
    return delays


async def test_stream_backoff_grows_when_device_drops_at_once(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepting the stream and closing it at once does not reset the backoff."""
    delays = await _stream_reconnect_delays(
        hass, aioclient_mock, entry, monkeypatch, clock=[0.0] * 10, count=5
    )
    assert delays == [1, 2, 4, 8, 16]


async def test_stream_backoff_resets_after_stable_connection(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection that stayed up long enough resets the backoff."""
    stable = coordinator_module.STREAM_STABLE_AFTER
    clock = [0.0, 0.0, 0.0, 0.0, 0.0, stable, 0.0, 0.0, 0.0, 0.0]
    delays = await _stream_reconnect_delays(
        hass, aioclient_mock, entry, monkeypatch, clock=clock, count=5
    )
    assert delays == [1, 2, 1, 2, 4]
