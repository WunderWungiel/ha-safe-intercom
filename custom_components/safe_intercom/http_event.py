"""Webhook receiving the intercom's HTTP events (web UI manual §8.13).

The user points the device's HTTP event at this webhook; the integration never
writes the setting itself. The webhook ID is the only secret, so it is never
logged here.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from aiohttp import hdrs, web
from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .api import HTTP_EVENT_CALL, parse_http_event
from .const import DOMAIN, signal_call

if TYPE_CHECKING:
    from . import SafeIntercomConfigEntry

_LOGGER = logging.getLogger(__name__)

# The event value is whatever the sender put there; keep diagnostics small.
MAX_RECORDED_EVENT = 100


@dataclass(frozen=True, slots=True)
class HttpEventRecord:
    """One request received by the webhook."""

    received_at: str
    """ISO 8601, UTC."""
    method: str
    event: str | None
    """The `event` value, or None if the request had none."""
    rang: bool


@dataclass(slots=True)
class HttpEventStats:
    """Webhook traffic since the entry was loaded, for diagnostics.

    Shows whether the intercom reaches Home Assistant at all, what it sends,
    and whether one press arrives as several requests.
    """

    requests: int = 0
    last: HttpEventRecord | None = None


def async_register_webhook(hass: HomeAssistant, entry: SafeIntercomConfigEntry) -> None:
    """Register the entry's webhook; only reachable from the local network."""

    async def handle(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        # Decoded leniently: an exception here would make HA log the webhook
        # ID at error level.
        body = (
            (await request.read()).decode(errors="replace")
            if request.method == hdrs.METH_POST
            else ""
        )
        event = parse_http_event(request.query_string, body)
        rang = event == HTTP_EVENT_CALL
        stats = entry.runtime_data.http_events
        stats.requests += 1
        stats.last = HttpEventRecord(
            received_at=dt_util.utcnow().isoformat(),
            method=request.method,
            event=event[:MAX_RECORDED_EVENT] if event is not None else None,
            rang=rang,
        )
        if rang:
            _LOGGER.debug("Call button pressed on %s", entry.title)
            async_dispatcher_send(hass, signal_call(entry.entry_id))
        else:
            _LOGGER.debug("Ignored HTTP event %r from %s", event, entry.title)
        return web.Response(status=200)

    webhook.async_register(
        hass,
        DOMAIN,
        entry.title,
        entry.data[CONF_WEBHOOK_ID],
        handle,
        local_only=True,
        allowed_methods=(hdrs.METH_GET, hdrs.METH_POST),
    )


def async_unregister_webhook(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> None:
    """Remove the entry's webhook."""
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
