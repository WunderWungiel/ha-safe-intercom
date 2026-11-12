"""Tests for the SAFE Intercom motion and alarm input binary sensors."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.api import (
    AlarmEvent,
    SafeIntercomClient,
    SafeIntercomConnectionError,
    parse_alarm_event,
)
from custom_components.safe_intercom.const import DOMAIN
from custom_components.safe_intercom.coordinator import SafeIntercomCoordinator

from .const import (
    HOST,
    MAC,
    MOTION_URL,
    NETWORK_RESPONSE,
    RTSP_RESPONSE,
    SYSTEMINFO_RESPONSE,
    controller_response,
    motion_response,
)

MOTION = "binary_sensor.intercom_motion"
INPUT_1 = "binary_sensor.intercom_alarm_input_1"

CLOSE = object()


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured intercom."""
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
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def lines(
    monkeypatch: pytest.MonkeyPatch, aioclient_mock: AiohttpClientMocker
) -> asyncio.Queue:
    """Mock the device; the alarm stream yields the lines put on the queue.

    Putting CLOSE ends the stream; reconnects then wait forever.
    """
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/network_cgi?action=get", text=NETWORK_RESPONSE
    )
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/systeminfo_cgi", text=SYSTEMINFO_RESPONSE
    )
    aioclient_mock.get(f"http://{HOST}/cgi-bin/rtsp_cgi?action=get", text=RTSP_RESPONSE)
    aioclient_mock.get(MOTION_URL, text=motion_response())
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/controller_cgi?action=get", text=controller_response()
    )
    queue: asyncio.Queue = asyncio.Queue()

    async def events(
        self: SafeIntercomClient, on_connect: Callable[[], None] | None = None
    ) -> AsyncIterator[AlarmEvent]:
        if on_connect is not None:
            on_connect()
        while (item := await queue.get()) is not CLOSE:
            if isinstance(item, Exception):
                raise item
            if event := parse_alarm_event(f"2026-01-01;10:00:00;{item}"):
                yield event

    async def wait_forever(self: SafeIntercomCoordinator, seconds: float) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(SafeIntercomClient, "async_alarm_events", events)
    monkeypatch.setattr(SafeIntercomCoordinator, "_async_stream_wait", wait_forever)
    return queue


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=False)


async def _send(hass: HomeAssistant, queue: asyncio.Queue, item: object) -> None:
    await queue.put(item)
    for _ in range(10):
        await asyncio.sleep(0)
    await hass.async_block_till_done(wait_background_tasks=False)


async def test_motion_sensor(
    hass: HomeAssistant, entry: MockConfigEntry, lines: asyncio.Queue
) -> None:
    """Motion follows the stream's zones."""
    await _setup(hass, entry)
    assert hass.states.get(MOTION).state == STATE_OFF
    assert hass.states.get(MOTION).attributes["device_class"] == "motion"

    await _send(hass, lines, "MotionDetection;1;0")
    assert hass.states.get(MOTION).state == STATE_ON
    await _send(hass, lines, "MotionDetection;0;0")
    assert hass.states.get(MOTION).state == STATE_OFF


async def test_unavailable_without_stream(
    hass: HomeAssistant, entry: MockConfigEntry, lines: asyncio.Queue
) -> None:
    """Without the stream, motion state is unknowable, so unavailable."""
    await _setup(hass, entry)
    await _send(hass, lines, "MotionDetection;1;0")
    await _send(hass, lines, SafeIntercomConnectionError("gone"))
    assert hass.states.get(MOTION).state == STATE_UNAVAILABLE


async def test_inputs_disabled_by_default(
    hass: HomeAssistant, entry: MockConfigEntry, lines: asyncio.Queue
) -> None:
    """Inputs exist but are disabled; once enabled they start unknown."""
    await _setup(hass, entry)
    registry = er.async_get(hass)
    for number in (1, 2, 3):
        reg = registry.async_get(f"binary_sensor.intercom_alarm_input_{number}")
        assert reg is not None
        assert reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert reg.unique_id == f"{MAC}_alarm_input_{number}"
    assert hass.states.get(INPUT_1) is None

    registry.async_update_entity(INPUT_1, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=False)
    assert hass.states.get(INPUT_1).state == STATE_UNKNOWN

    await _send(hass, lines, "SensorInAlarm;1;0")
    assert hass.states.get(INPUT_1).state == STATE_ON
    await _send(hass, lines, "SensorInAlarm;0;0")
    assert hass.states.get(INPUT_1).state == STATE_OFF
