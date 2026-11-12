"""Tests for the SAFE intercom HTTP client."""

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.api import (
    AlarmEvent,
    ControllerInfo,
    RelayInfo,
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomCommandError,
    SafeIntercomConnectionError,
    SystemInfo,
    parse_alarm_event,
)

from .const import HOST, SYSTEMINFO_RESPONSE, VIDEOCODING_RESPONSE, controller_response

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


ALARM_URL = (
    f"http://{HOST}/cgi-bin/alarmchangestate_cgi?channel=0&parameter=SensorOutAlarm"
)


def test_parse_alarm_event() -> None:
    """Event lines carry date, time, type, status and optional areas/sensor."""
    assert parse_alarm_event("2012-06-04;20:00:40;SensorOutAlarm;1;2") == AlarmEvent(
        alarm_type="SensorOutAlarm", active=True, areas=(), sensor=2
    )
    assert parse_alarm_event("2012-06-04;20:00:36;MotionDetection;1;0,1") == AlarmEvent(
        alarm_type="MotionDetection", active=True, areas=(0, 1), sensor=None
    )
    assert parse_alarm_event("2012-06-04;20:00:47;SensorInAlarm;0;0").active is False


@pytest.mark.parametrize("line", ["", "\r", "garbage", "2012-06-04;20:00:40;X"])
def test_parse_alarm_event_ignores_keepalives_and_junk(line: str) -> None:
    """The bare keepalive newline and malformed lines yield no event."""
    assert parse_alarm_event(line) is None


async def test_alarm_stream_yields_events(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """The stream skips keepalives and yields one event per line."""
    aioclient_mock.get(
        ALARM_URL,
        text="\n2012-06-04;20:00:40;SensorOutAlarm;1;0\n\n"
        "2012-06-04;20:00:44;SensorOutAlarm;0;0\n",
    )

    events = [event async for event in client.async_alarm_events()]

    assert [e.active for e in events] == [True, False]
    assert all(e.alarm_type == "SensorOutAlarm" for e in events)
    assert dict(aioclient_mock.mock_calls[0][1].query) == {
        "channel": "0",
        "parameter": "SensorOutAlarm",
    }


async def test_alarm_stream_connection_error(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """A failed connection raises the client's own error type."""
    aioclient_mock.get(ALARM_URL, exc=aiohttp.ClientError)

    with pytest.raises(SafeIntercomConnectionError):
        async for _ in client.async_alarm_events():
            pass


async def test_alarm_stream_subscribes_to_outputs_and_signals_connect(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """Only output alarms are requested; on_connect fires once the stream is up."""
    aioclient_mock.get(ALARM_URL, text="\n")
    connects = []

    events = [
        e
        async for e in client.async_alarm_events(on_connect=lambda: connects.append(1))
    ]

    assert events == []
    assert connects == [1]
    assert dict(aioclient_mock.mock_calls[0][1].query) == {
        "channel": "0",
        "parameter": "SensorOutAlarm",
    }


async def test_alarm_stream_no_connect_signal_on_auth_error(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """A rejected stream raises an auth error and never signals a connection."""
    aioclient_mock.get(ALARM_URL, status=401)
    connects = []

    with pytest.raises(SafeIntercomAuthError):
        async for _ in client.async_alarm_events(on_connect=lambda: connects.append(1)):
            pass
    assert connects == []


async def test_get_system_info(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """Model and versions come from systeminfo_cgi."""
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/systeminfo_cgi", text=SYSTEMINFO_RESPONSE
    )

    assert await client.async_get_system_info() == SystemInfo(
        "B1710", "2.0.0.2.52.18", "Hi3518 IP Camera"
    )
    assert client.base_url == f"http://{HOST}:80"


async def test_get_video_coding(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """The spec's videocoding_cgi example is parsed into its keys."""
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/videocoding_cgi?action=get&channel=0",
        text=VIDEOCODING_RESPONSE,
    )
    result = await client.async_get_video_coding()
    assert result["EncType1"] == "H.264"
    assert result["Resolution2"] == "352*288"
    assert "Main stream options:" not in result
    assert "Main stream options" not in result
