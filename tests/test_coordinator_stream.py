"""Tests for the pushed motion and alarm input state in the coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.safe_intercom.api import (
    AlarmEvent,
    ControllerInfo,
    parse_alarm_event,
)
from custom_components.safe_intercom.const import DOMAIN
from custom_components.safe_intercom.coordinator import SafeIntercomCoordinator

from .const import MAC

CONTROLLER = ControllerInfo(type="3n", relays=(), play_sound=False)
CLOSE = object()
"""Queue marker: the device closes the stream."""


class FakeClient:
    """A client whose alarm stream yields whatever the test queues."""

    host = "192.0.2.10"

    def __init__(self) -> None:
        self.queue: asyncio.Queue[AlarmEvent | object] = asyncio.Queue()
        self.controller_reads = 0

    async def async_get_controller(self) -> ControllerInfo:
        self.controller_reads += 1
        return CONTROLLER

    async def async_alarm_events(
        self, on_connect: Callable[[], None] | None = None
    ) -> AsyncIterator[AlarmEvent]:
        if on_connect is not None:
            on_connect()
        while (item := await self.queue.get()) is not CLOSE:
            assert isinstance(item, AlarmEvent)
            yield item


def _event(line: str) -> AlarmEvent:
    event = parse_alarm_event(f"2012-06-04;20:00:36;{line}")
    assert event is not None
    return event


@pytest.fixture
async def stream(hass: HomeAssistant):
    """Run the coordinator's stream against a FakeClient; yield both."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={})
    entry.add_to_hass(hass)
    client = FakeClient()
    coordinator = SafeIntercomCoordinator(hass, entry, client)  # type: ignore[arg-type]
    coordinator.data = CONTROLLER

    reconnect = asyncio.Event()

    async def wait(seconds: float) -> None:
        await reconnect.wait()

    coordinator._async_stream_wait = wait  # type: ignore[method-assign]
    updates: list[None] = []
    coordinator.async_add_listener(lambda: updates.append(None))
    task = asyncio.create_task(coordinator.async_run_event_stream())
    await _settle()
    yield coordinator, client, updates
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def _send(client: FakeClient, item: AlarmEvent | object) -> None:
    await client.queue.put(item)
    await _settle()


async def test_motion_zones(stream) -> None:
    """Motion is on while any zone is active; zones clear one by one."""
    coordinator, client, updates = stream
    assert coordinator.motion is False

    await _send(client, _event("MotionDetection;1;0,1"))
    assert coordinator.motion is True
    await _send(client, _event("MotionDetection;0;0"))
    assert coordinator.motion is True
    await _send(client, _event("MotionDetection;0;1"))
    assert coordinator.motion is False


async def test_motion_without_zones(stream) -> None:
    """An active motion line without zones still counts as motion."""
    coordinator, client, _ = stream
    await _send(client, _event("MotionDetection;1"))
    assert coordinator.motion is True
    await _send(client, _event("MotionDetection;0"))
    assert coordinator.motion is False


async def test_input_state(stream) -> None:
    """Inputs are 1-based and unknown until their first event."""
    coordinator, client, updates = stream
    assert coordinator.input_state(1) is None

    before = len(updates)
    await _send(client, _event("SensorInAlarm;1;0"))
    assert coordinator.input_state(1) is True
    assert len(updates) > before
    await _send(client, _event("SensorInAlarm;0;0"))
    assert coordinator.input_state(1) is False
    assert coordinator.input_state(2) is None


async def test_state_cleared_on_disconnect(stream) -> None:
    """A dropped stream forgets pushed state: missed `off` lines can't stick."""
    coordinator, client, updates = stream
    assert coordinator.stream_connected is True
    await _send(client, _event("MotionDetection;1;0"))
    await _send(client, _event("SensorInAlarm;1;0"))

    before = len(updates)
    await _send(client, CLOSE)

    assert coordinator.stream_connected is False
    assert coordinator.motion is False
    assert coordinator.input_state(1) is None
    assert len(updates) > before


async def test_sensor_out_still_refreshes(stream) -> None:
    """Output changes still re-read the controller; other types don't."""
    coordinator, client, _ = stream
    reads = client.controller_reads
    await _send(client, _event("MotionDetection;1;0"))
    await _send(client, _event("SensorInAlarm;1;0"))
    assert client.controller_reads == reads

    await _send(client, _event("SensorOutAlarm;1;2"))
    assert client.controller_reads == reads + 1


async def test_motion_off_without_zones_clears_all(stream) -> None:
    """An `off` line naming no zones ends motion, whatever zones were on."""
    coordinator, client, _ = stream
    await _send(client, _event("MotionDetection;1;0,1"))
    assert coordinator.motion is True
    await _send(client, _event("MotionDetection;0"))
    assert coordinator.motion is False


async def test_motion_without_zones_cleared_by_zoned_off(stream) -> None:
    """Motion reported without zones ends with any `off`, even one naming zones."""
    coordinator, client, _ = stream
    await _send(client, _event("MotionDetection;1"))
    assert coordinator.motion is True
    await _send(client, _event("MotionDetection;0;1"))
    assert coordinator.motion is False


async def test_motion_zoned_on_survives_unrelated_off(stream) -> None:
    """A zoned `off` only clears its own zones, with or without unzoned motion."""
    coordinator, client, _ = stream
    await _send(client, _event("MotionDetection;1;0"))
    await _send(client, _event("MotionDetection;1"))
    await _send(client, _event("MotionDetection;0;1"))
    assert coordinator.motion is True
    await _send(client, _event("MotionDetection;0;0"))
    assert coordinator.motion is False
