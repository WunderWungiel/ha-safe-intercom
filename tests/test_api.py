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
    MotionSettings,
    RelayInfo,
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomCommandError,
    SafeIntercomConnectionError,
    SystemInfo,
    parse_alarm_event,
    parse_http_event,
)

from .const import (
    ALARM_URL,
    HOST,
    MOTION_URL,
    SYSTEMINFO_RESPONSE,
    VIDEOCODING_RESPONSE,
    controller_response,
    motion_response,
)

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
    [
        "Request failed:Param error\r\n",
        "Type=3n\r\nDelay1=x\r\nStatus1=0\r\n",
        "Type=3n\r\nDelay1=nan\r\nStatus1=0\r\n",
        "Type=3n\r\nDelay1=-1\r\nStatus1=0\r\n",
    ],
)
async def test_get_controller_invalid(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient, text: str
) -> None:
    """Responses without a Type or with garbage delays are rejected."""
    aioclient_mock.get(CONTROLLER_URL, text=text)

    with pytest.raises(SafeIntercomConnectionError):
        await client.async_get_controller()


@pytest.mark.parametrize(
    ("controller_type", "numbers"),
    [("1", (1,)), ("3", (1, 2, 3)), ("3n", (1, 2, 3)), ("9", ()), ("3N", ())],
)
async def test_supported_relays(
    aioclient_mock: AiohttpClientMocker,
    client: SafeIntercomClient,
    controller_type: str,
    numbers: tuple[int, ...],
) -> None:
    """Each known type exposes a fixed number of relays; unknown types none."""
    aioclient_mock.get(
        CONTROLLER_URL, text=controller_response(controller_type=controller_type)
    )

    info = await client.async_get_controller()

    assert len(info.relays) == 3
    assert tuple(r.number for r in info.supported_relays) == numbers


async def test_get_controller_fractional_delay(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """NC311P delays can be fractional (0.1-3600 s per the Beward manual)."""
    aioclient_mock.get(CONTROLLER_URL, text="Type=3n\r\nDelay1=0.5\r\nStatus1=0\r\n")

    info = await client.async_get_controller()

    assert info.relay(1).delay == 0.5


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


async def test_alarm_stream_connection_error(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """A failed connection raises the client's own error type."""
    aioclient_mock.get(ALARM_URL, exc=aiohttp.ClientError)

    with pytest.raises(SafeIntercomConnectionError):
        async for _ in client.async_alarm_events():
            pass


async def test_alarm_stream_subscribes_all_types_and_signals_connect(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """Motion, input and output alarms are requested; on_connect fires once up.

    The `;` separators go out literally, as in the spec example, not as `%3B`.
    """
    aioclient_mock.get(ALARM_URL, text="\n")
    connects = []

    events = [
        e
        async for e in client.async_alarm_events(on_connect=lambda: connects.append(1))
    ]

    assert events == []
    assert connects == [1]
    assert aioclient_mock.mock_calls[0][1].raw_query_string == (
        "channel=0&parameter=MotionDetection;SensorInAlarm;SensorOutAlarm"
    )


async def test_alarm_stream_no_connect_signal_without_data(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """An HTTP 200 that closes without a line is not a connection."""
    aioclient_mock.get(ALARM_URL, text="")
    connects = []

    events = [
        e
        async for e in client.async_alarm_events(on_connect=lambda: connects.append(1))
    ]

    assert events == []
    assert connects == []


async def test_alarm_stream_signals_connect_once(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """on_connect fires on the first line, before its event, and only once."""
    aioclient_mock.get(
        ALARM_URL,
        text="2012-06-04;20:00:40;SensorOutAlarm;1;0\n"
        "2012-06-04;20:00:44;SensorOutAlarm;0;0\n",
    )
    calls: list[str] = []

    async for _ in client.async_alarm_events(on_connect=lambda: calls.append("c")):
        calls.append("e")

    assert calls == ["c", "e", "e"]


@pytest.mark.parametrize(
    "body", ["Request failed: bad parameter\n", "Request failed: bad parameter"]
)
async def test_alarm_stream_request_failed(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient, body: str
) -> None:
    """An error body sent with HTTP 200 raises and never signals a connection."""
    aioclient_mock.get(ALARM_URL, text=body)
    connects = []

    with pytest.raises(SafeIntercomCommandError, match="bad parameter"):
        async for _ in client.async_alarm_events(on_connect=lambda: connects.append(1)):
            pass
    assert connects == []


async def test_alarm_stream_yields_input_and_motion_events(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """Input and motion lines come through as events too."""
    aioclient_mock.get(
        ALARM_URL,
        text="2012-06-04;20:00:36;SensorInAlarm;1;2\n"
        "2012-06-04;20:00:37;MotionDetection;1;0,1\n",
    )

    events = [event async for event in client.async_alarm_events()]

    assert events == [
        AlarmEvent("SensorInAlarm", True, (), 2),
        AlarmEvent("MotionDetection", True, (0, 1), None),
    ]


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


@pytest.mark.parametrize(
    ("query", "body", "expected"),
    [
        ("event=call", "", "call"),
        ("", "event=call", "call"),
        ("event=call", "event=other", "call"),
        ("foo=bar&event=call", "", "call"),
        ("foo=bar", "", None),
        ("", "", None),
        ("event", "", ""),
        ("", "not a form \x00", None),
        ("", "event=call\r\n", "call"),
        ("event=%20call%20", "", "call"),
    ],
)
def test_parse_http_event(query: str, body: str, expected: str | None) -> None:
    """The `event` value comes from the query string, else the POST body."""
    assert parse_http_event(query, body) == expected


@pytest.mark.parametrize(
    ("switch", "schedule", "expected"),
    [
        ("open", "close", MotionSettings(enabled=True, scheduled=False)),
        ("open", "open", MotionSettings(enabled=True, scheduled=True)),
        ("close", "close", MotionSettings(enabled=False, scheduled=False)),
    ],
)
async def test_get_motion_settings(
    client: SafeIntercomClient,
    aioclient_mock: AiohttpClientMocker,
    switch: str,
    schedule: str,
    expected: MotionSettings,
) -> None:
    """A schedule on any one day counts, though later days repeat the keys."""
    aioclient_mock.get(MOTION_URL, text=motion_response(switch, schedule))
    assert await client.async_get_motion_settings() == expected


async def test_get_motion_settings_failed(
    client: SafeIntercomClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A `Request failed` body has no MotionSwitch and is an error."""
    aioclient_mock.get(MOTION_URL, text="Request failed:Param error")
    with pytest.raises(SafeIntercomConnectionError):
        await client.async_get_motion_settings()
