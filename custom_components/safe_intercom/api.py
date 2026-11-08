"""Client for the SAFE IP intercom HTTP API.

Must not import anything from Home Assistant, so it can later be split into a
standalone library. See AGENTS.md for the endpoints and verified device behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

import aiohttp
from yarl import URL

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class SafeIntercomError(Exception):
    """Base error for the SAFE intercom client."""


class SafeIntercomConnectionError(SafeIntercomError):
    """The device could not be reached or returned an unexpected response."""


class SafeIntercomAuthError(SafeIntercomError):
    """The device rejected the credentials."""


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    """Network parameters reported by `network_cgi?action=get`."""

    mac_address: str
    host_name: str | None
    ip_address: str | None


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
        self._headers = {"Authorization": aiohttp.encode_basic_auth(username, password)}

    async def _get_text(self, path: str, params: dict[str, str]) -> str:
        """Perform an authenticated GET and return the response body."""
        url = self._base_url.with_path(path).with_query(params)
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
                return await resp.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SafeIntercomConnectionError(
                f"Error communicating with {self._base_url.host}: {err!r}"
            ) from err

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

    # TODO: firmware version, controller_cgi state + delays, alarmout_cgi relay
    # control, snapshot, alarmchangestate_cgi event stream.
