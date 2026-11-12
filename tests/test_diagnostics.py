"""Tests for SAFE Intercom diagnostics."""

from unittest.mock import ANY

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.safe_intercom.const import DOMAIN

from .const import (
    HOST,
    MAC,
    NETWORK_RESPONSE,
    RTSP_RESPONSE,
    SYSTEMINFO_RESPONSE,
    VIDEOCODING_RESPONSE,
    controller_response,
    hanging_stream,
)

NETWORK_URL = f"http://{HOST}/cgi-bin/network_cgi?action=get"
SYSTEMINFO_URL = f"http://{HOST}/cgi-bin/systeminfo_cgi"
RTSP_URL = f"http://{HOST}/cgi-bin/rtsp_cgi?action=get"
CONTROLLER_URL = f"http://{HOST}/cgi-bin/controller_cgi?action=get"
VIDEOCODING_URL = f"http://{HOST}/cgi-bin/videocoding_cgi?action=get&channel=0"
ALARM_URL = (
    f"http://{HOST}/cgi-bin/alarmchangestate_cgi?channel=0&parameter=SensorOutAlarm"
)


async def _diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    videocoding_kwargs: dict | None = None,
) -> dict:
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
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    aioclient_mock.get(
        VIDEOCODING_URL, **(videocoding_kwargs or {"text": VIDEOCODING_RESPONSE})
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await get_diagnostics_for_config_entry(hass, hass_client, entry)


async def test_diagnostics_redacts_credentials(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Diagnostics show device state but never credentials or addresses."""
    result = await _diagnostics(hass, hass_client, aioclient_mock)

    for key in (CONF_PASSWORD, CONF_USERNAME, CONF_HOST):
        assert result["entry"]["data"][key] == "**REDACTED**"
    assert result["entry"]["unique_id"] == "**REDACTED**"
    assert "secret" not in str(result)
    assert result["controller"]["type"] == "3n"
    assert result["system"]["model"] == "B1710"


async def test_diagnostics_camera(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """RTSP settings and the video encoding are included."""
    result = await _diagnostics(hass, hass_client, aioclient_mock)

    assert result["rtsp"] == {"enabled": True, "port": 8554, "auth": False}
    assert result["video_coding"]["EncType1"] == "H.264"


async def test_diagnostics_video_coding_unavailable(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A failed videocoding_cgi read is reported, not raised."""
    result = await _diagnostics(
        hass, hass_client, aioclient_mock, videocoding_kwargs={"status": 500}
    )

    assert result["video_coding"] == {"error": ANY}


async def test_diagnostics_video_coding_error_hides_host(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A connection error in diagnostics does not reveal the device address."""
    result = await _diagnostics(
        hass, hass_client, aioclient_mock, videocoding_kwargs={"exc": TimeoutError}
    )

    assert result["video_coding"] == {"error": "SafeIntercomConnectionError"}
    assert HOST not in str(result)
