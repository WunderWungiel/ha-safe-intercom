"""Client for the SAFE IP intercom HTTP API.

Must not import anything from Home Assistant, so it can later be split into a
standalone library. See AGENTS.md for the endpoints and verified device behaviour.
"""

from __future__ import annotations

import aiohttp


class SafeIntercomClient:
    """Async client for a single SAFE intercom."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._host = host
        self._headers = {"Authorization": aiohttp.encode_basic_auth(username, password)}

    # TODO: device info (MAC / firmware), controller_cgi state + delays,
    # alarmout_cgi relay control, snapshot, alarmchangestate_cgi event stream.
