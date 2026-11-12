"""Client for the SAFE IP intercom HTTP API.

Must not import anything from Home Assistant, so it can later be split into a
standalone library. See AGENTS.md for the endpoints and verified device behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from http import HTTPStatus

import aiohttp
from yarl import URL

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# The alarm stream is long-lived. The device sends a keepalive newline every
# 5 s, so a read that stalls far longer than that means the connection is dead.
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=10, sock_read=20)

ALARM_SENSOR_OUT = "SensorOutAlarm"

DEFAULT_RTSP_PORT = 554

STREAM_MAIN = 0
"""Main stream profile, `av0_0`."""
STREAM_SUB = 1
"""Alternative (sub) stream profile, `av0_1`."""


class SafeIntercomError(Exception):
    """Base error for the SAFE intercom client."""


class SafeIntercomConnectionError(SafeIntercomError):
    """The device could not be reached or returned an unexpected response."""


class SafeIntercomAuthError(SafeIntercomError):
    """The device rejected the credentials."""


class SafeIntercomCommandError(SafeIntercomError):
    """The device answered but refused a command (`Request failed:...`)."""


CONTROLLER_TYPE_3N = "3n"
"""Controller type reported by current firmware for the 3-relay controller."""


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    """Network parameters reported by `network_cgi?action=get`."""

    mac_address: str
    host_name: str | None
    ip_address: str | None


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Device model and versions reported by `systeminfo_cgi`."""

    model: str | None
    software_version: str | None
    hardware_version: str | None


@dataclass(frozen=True, slots=True)
class RtspInfo:
    """RTSP server settings reported by `rtsp_cgi?action=get`."""

    enabled: bool
    port: int
    auth: bool


@dataclass(frozen=True, slots=True)
class RelayInfo:
    """One controller relay, as reported by `controller_cgi?action=get`.

    `number` is 1-based, matching the `controller_cgi` keys.
    """

    number: int
    delay: int
    """Seconds until the relay drops back by itself; 0 means latching."""
    is_on: bool
    close_by_call: bool
    close_by_face: bool
    close_by_night: bool


@dataclass(frozen=True, slots=True)
class ControllerInfo:
    """Controller type and relay states."""

    type: str
    relays: tuple[RelayInfo, ...]
    play_sound: bool

    def relay(self, number: int) -> RelayInfo | None:
        """Return the relay with the given 1-based number, if present."""
        return next((r for r in self.relays if r.number == number), None)


@dataclass(frozen=True, slots=True)
class AlarmEvent:
    """One change reported by the `alarmchangestate_cgi` stream."""

    alarm_type: str
    active: bool
    areas: tuple[int, ...]
    """Motion detection zones; empty for other alarm types."""
    sensor: int | None
    """0-based sensor/output number for `SensorInAlarm`/`SensorOutAlarm`."""


def parse_alarm_event(line: str) -> AlarmEvent | None:
    """Parse one stream line; keepalives and malformed lines give None.

    Format: `<date>;<time>;<alarmType>;<0|1>[;<areas>][;<sensorNo>]`. A third
    optional field is a sensor number, except for MotionDetection where it is
    a comma-separated list of zones.
    """
    parts = line.strip().split(";")
    if len(parts) < 4 or parts[3] not in ("0", "1"):
        return None
    alarm_type = parts[2]
    extra = parts[4] if len(parts) > 4 else ""
    try:
        if alarm_type == "MotionDetection":
            areas = tuple(int(a) for a in extra.split(",") if a)
            sensor = None
        else:
            areas = ()
            sensor = int(extra) if extra else None
    except ValueError:
        return None
    return AlarmEvent(alarm_type, parts[3] == "1", areas, sensor)


def _is_open(value: str | None) -> bool:
    """Interpret the device's `open`/`close` flag values."""
    return value == "open"


def _parse_controller(data: dict[str, str]) -> ControllerInfo:
    """Build a ControllerInfo from parsed `controller_cgi` keys."""
    if not (controller_type := data.get("Type")):
        raise SafeIntercomConnectionError("controller_cgi response has no Type")
    relays: list[RelayInfo] = []
    number = 1
    while f"Status{number}" in data or f"Delay{number}" in data:
        try:
            delay = int(data.get(f"Delay{number}", "0"))
        except ValueError as err:
            raise SafeIntercomConnectionError(
                f"controller_cgi returned an invalid Delay{number}"
            ) from err
        relays.append(
            RelayInfo(
                number=number,
                delay=delay,
                is_on=data.get(f"Status{number}") == "1",
                close_by_call=_is_open(data.get(f"CloseByCall{number}")),
                close_by_face=_is_open(data.get(f"CloseByFace{number}")),
                close_by_night=_is_open(data.get(f"CloseByNight{number}")),
            )
        )
        number += 1
    return ControllerInfo(
        type=controller_type,
        relays=tuple(relays),
        play_sound=_is_open(data.get("PlaySound")),
    )


def _parse_key_values(text: str) -> dict[str, str]:
    """Parse a `key=value` per line response body."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    return result


class SafeIntercomClient:
    """Async client for a single SAFE intercom.

    Credentials are sent with HTTP Basic auth rather than the `user=`/`pwd=`
    query parameters from the spec, so they never end up in URLs or logs.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = URL.build(scheme="http", host=host, port=port)
        self._username = username
        self._password = password
        self._headers = {"Authorization": aiohttp.encode_basic_auth(username, password)}

    @property
    def host(self) -> str:
        """The device's host name or address."""
        return self._base_url.host or ""

    @property
    def base_url(self) -> str:
        """The device's web interface, e.g. `http://192.0.2.10:80`."""
        return f"{self._base_url.scheme}://{self._base_url.host}:{self._base_url.port}"

    async def _get_bytes(
        self, path: str, params: dict[str, str] | None = None
    ) -> bytes:
        """Perform an authenticated GET and return the raw response body."""
        url = self._base_url.with_path(path).with_query(params or {})
        try:
            async with self._session.get(
                url, headers=self._headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise SafeIntercomAuthError
                if resp.status != HTTPStatus.OK:
                    raise SafeIntercomConnectionError(
                        f"Unexpected HTTP status {resp.status} from {path}"
                    )
                return await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SafeIntercomConnectionError(
                f"Error communicating with {self._base_url.host}: {err!r}"
            ) from err

    async def _get_text(self, path: str, params: dict[str, str] | None = None) -> str:
        """Perform an authenticated GET and return the response body as text."""
        return (await self._get_bytes(path, params)).decode(errors="replace")

    async def async_get_network_info(self) -> NetworkInfo:
        """Fetch network parameters; also serves as a credentials check."""
        text = await self._get_text("/cgi-bin/network_cgi", {"action": "get"})
        data = _parse_key_values(text)
        if not (mac := data.get("MACAddress")):
            raise SafeIntercomConnectionError("network_cgi response has no MACAddress")
        return NetworkInfo(
            mac_address=mac,
            host_name=data.get("HostName"),
            ip_address=data.get("IPAddress"),
        )

    async def async_get_system_info(self) -> SystemInfo:
        """Fetch the device model and firmware/hardware versions."""
        data = _parse_key_values(await self._get_text("/cgi-bin/systeminfo_cgi"))
        return SystemInfo(
            model=data.get("DeviceModel"),
            software_version=data.get("SoftwareVersion"),
            hardware_version=data.get("HardwareVersion"),
        )

    async def async_get_controller(self) -> ControllerInfo:
        """Fetch the controller type, relay states and delays."""
        text = await self._get_text("/cgi-bin/controller_cgi", {"action": "get"})
        return _parse_controller(_parse_key_values(text))

    async def async_set_relay(self, number: int, on: bool) -> None:
        """Energise (`on`) or release a relay, by its 1-based number.

        `alarmout_cgi` numbers outputs from 0, `controller_cgi` from 1; this is
        the only place that converts between them.
        """
        if number < 1:
            raise ValueError(f"Relay numbers start at 1, got {number}")
        text = await self._get_text(
            "/cgi-bin/alarmout_cgi",
            {"action": "set", "Output": str(number - 1), "Status": "1" if on else "0"},
        )
        if "Request failed" in text:
            raise SafeIntercomCommandError(text.strip())

    async def async_alarm_events(
        self, on_connect: Callable[[], None] | None = None
    ) -> AsyncIterator[AlarmEvent]:
        """Yield output alarm changes from the device's push stream.

        `on_connect` is called once the device has accepted the stream, before
        any event. Returns when the device closes the connection; raises
        SafeIntercomConnectionError if it cannot be reached or goes silent.
        """
        url = self._base_url.with_path("/cgi-bin/alarmchangestate_cgi").with_query(
            {"channel": "0", "parameter": ALARM_SENSOR_OUT}
        )
        try:
            async with self._session.get(
                url, headers=self._headers, timeout=STREAM_TIMEOUT
            ) as resp:
                if resp.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise SafeIntercomAuthError
                if resp.status != HTTPStatus.OK:
                    raise SafeIntercomConnectionError(
                        f"Unexpected HTTP status {resp.status} from alarm stream"
                    )
                if on_connect is not None:
                    on_connect()
                async for raw in resp.content:
                    if event := parse_alarm_event(raw.decode(errors="replace")):
                        yield event
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SafeIntercomConnectionError(
                f"Alarm stream from {self._base_url.host} failed: {err!r}"
            ) from err

    async def async_get_video_coding(self) -> dict[str, str]:
        """Fetch the encoder settings (codec, resolution, ...) per stream."""
        return _parse_key_values(
            await self._get_text(
                "/cgi-bin/videocoding_cgi", {"action": "get", "channel": "0"}
            )
        )

    async def async_get_rtsp_info(self) -> RtspInfo:
        """Fetch the RTSP server settings."""
        data = _parse_key_values(
            await self._get_text("/cgi-bin/rtsp_cgi", {"action": "get"})
        )
        try:
            port = int(data.get("RtspPort", DEFAULT_RTSP_PORT))
        except ValueError:
            port = DEFAULT_RTSP_PORT
        return RtspInfo(
            # Missing keys mean old firmware; assume the factory defaults.
            enabled=data.get("RtspSwitch", "open") == "open",
            port=port,
            # Unknown means "send credentials": only an explicit close drops them.
            auth=data.get("RtspAuth") != "close",
        )

    def rtsp_url(self, rtsp: RtspInfo | None, stream: int) -> str | None:
        """Return the RTSP URL of `stream` (STREAM_MAIN/STREAM_SUB).

        `rtsp` is the device's RTSP settings, or None if they are unknown (then
        the default port is used and credentials are included). Returns None
        when RTSP is turned off. The result may contain the password; never
        log it.
        """
        if rtsp is not None and not rtsp.enabled:
            return None
        with_auth = rtsp is None or rtsp.auth
        return str(
            URL.build(
                scheme="rtsp",
                user=self._username if with_auth else None,
                password=self._password if with_auth else None,
                host=self.host,
                port=rtsp.port if rtsp is not None else DEFAULT_RTSP_PORT,
                path=f"/av0_{stream}",
            )
        )

    async def async_get_snapshot(self) -> bytes:
        """Fetch a JPEG snapshot of the camera."""
        body = await self._get_bytes("/cgi-bin/images_cgi", {"channel": "0"})
        # Errors come back as HTTP 200 with a `Request failed:...` text body.
        if not body.startswith(b"\xff\xd8"):
            raise SafeIntercomCommandError(
                body[:100].decode(errors="replace").strip() or "Empty snapshot"
            )
        return body
