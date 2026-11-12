"""Diagnostics support for the SAFE Intercom integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import SafeIntercomConfigEntry
from .api import SafeIntercomError

TO_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME, "unique_id", "title"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SafeIntercomConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    video_coding: dict[str, str]
    try:
        video_coding = await data.client.async_get_video_coding()
    except SafeIntercomError as err:
        # The message can contain the host, which is redacted elsewhere.
        video_coding = {"error": type(err).__name__}
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "system": asdict(data.system) if data.system is not None else None,
        "controller": asdict(data.coordinator.data),
        "rtsp": asdict(data.rtsp) if data.rtsp is not None else None,
        "video_coding": video_coding,
    }
