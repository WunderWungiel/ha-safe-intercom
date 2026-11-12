"""Tests for the SAFE Intercom call button event and its webhook."""

from __future__ import annotations

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_WEBHOOK_ID,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.safe_intercom.const import DOMAIN

from .const import (
    ALARM_URL,
    HOST,
    MAC,
    NETWORK_RESPONSE,
    RTSP_RESPONSE,
    SYSTEMINFO_RESPONSE,
    controller_response,
    hanging_stream,
)

WEBHOOK_ID = "0123456789abcdef0123456789abcdef"
WEBHOOK_URL = f"/api/webhook/{WEBHOOK_ID}"
CALL_BUTTON = "event.intercom_call_button"


@pytest.fixture
async def entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> MockConfigEntry:
    """A loaded intercom entry with a known webhook ID."""
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/network_cgi?action=get", text=NETWORK_RESPONSE
    )
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/systeminfo_cgi", text=SYSTEMINFO_RESPONSE
    )
    aioclient_mock.get(f"http://{HOST}/cgi-bin/rtsp_cgi?action=get", text=RTSP_RESPONSE)
    aioclient_mock.get(
        f"http://{HOST}/cgi-bin/controller_cgi?action=get", text=controller_response()
    )
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Intercom",
        unique_id=MAC,
        minor_version=2,
        data={
            CONF_HOST: HOST,
            CONF_PORT: 80,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_WEBHOOK_ID: WEBHOOK_ID,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_call_button_entity(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The call button is a doorbell event entity, idle until pressed."""
    state = hass.states.get(CALL_BUTTON)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["device_class"] == "doorbell"
    assert state.attributes["event_types"] == ["ring"]


async def test_call_button_rings_on_get(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
) -> None:
    """A GET with the call parameter fires a ring."""
    client = await hass_client_no_auth()
    resp = await client.get(f"{WEBHOOK_URL}?event=call")
    await hass.async_block_till_done()

    assert resp.status == 200
    state = hass.states.get(CALL_BUTTON)
    assert state.state != STATE_UNKNOWN
    assert state.attributes["event_type"] == "ring"


async def test_call_button_rings_on_post(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
) -> None:
    """A POST carrying the call parameter in its body fires a ring."""
    client = await hass_client_no_auth()
    resp = await client.post(WEBHOOK_URL, data="event=call")
    await hass.async_block_till_done()

    assert resp.status == 200
    assert hass.states.get(CALL_BUTTON).attributes["event_type"] == "ring"


@pytest.mark.parametrize("path", ["?event=motion", "", "?call"])
async def test_unknown_event_ignored(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    path: str,
) -> None:
    """Anything but the call parameter is answered but fires nothing."""
    client = await hass_client_no_auth()
    resp = await client.get(f"{WEBHOOK_URL}{path}")
    await hass.async_block_till_done()

    assert resp.status == 200
    assert hass.states.get(CALL_BUTTON).state == STATE_UNKNOWN


async def test_webhook_unregistered_on_unload(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
) -> None:
    """After unload the webhook is gone; setting up again works."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    await client.get(f"{WEBHOOK_URL}?event=call")
    await hass.async_block_till_done()
    assert hass.states.get(CALL_BUTTON).attributes.get("event_type") is None

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await client.get(f"{WEBHOOK_URL}?event=call")
    await hass.async_block_till_done()
    assert hass.states.get(CALL_BUTTON).attributes["event_type"] == "ring"


async def test_webhook_id_not_logged(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The webhook ID is a secret; our own logging never shows it."""
    caplog.set_level("DEBUG", logger="custom_components.safe_intercom")
    client = await hass_client_no_auth()
    await client.get(f"{WEBHOOK_URL}?event=other")
    await client.get(f"{WEBHOOK_URL}?event=call")
    await hass.async_block_till_done()
    ours = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.safe_intercom")
    ]
    assert ours
    assert not any(WEBHOOK_ID in message for message in ours)
