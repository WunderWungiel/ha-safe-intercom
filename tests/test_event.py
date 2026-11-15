"""Tests for the SAFE Intercom call button event and its webhook."""

from __future__ import annotations

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_WEBHOOK_ID,
    STATE_UNAVAILABLE,
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
    CONTROLLER_URL,
    HOST,
    MAC,
    MOTION_URL,
    NETWORK_RESPONSE,
    RTSP_RESPONSE,
    SYSTEMINFO_RESPONSE,
    controller_response,
    hanging_stream,
    motion_response,
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
    aioclient_mock.get(MOTION_URL, text=motion_response())
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
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


async def test_undecodable_body_logs_no_error(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A body that isn't UTF-8 is ignored without an error log naming the ID."""
    client = await hass_client_no_auth()
    resp = await client.post(
        WEBHOOK_URL,
        data=b"\xff\xfeevent=call",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    await hass.async_block_till_done()

    assert resp.status == 200
    # HA core's own debug and access logs show webhook URLs; that's outside the
    # integration. What must not happen is an error log with a traceback.
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


async def test_call_button_available_while_controller_fails(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
) -> None:
    """A controller outage leaves the call button alone.

    Rings come by webhook, not from controller_cgi. Flipping to unavailable
    and back would replay the last `ring` to state-trigger automations.
    """
    client = await hass_client_no_auth()
    await client.get(f"{WEBHOOK_URL}?event=call")
    await hass.async_block_till_done()
    rang = hass.states.get(CALL_BUTTON)

    aioclient_mock.clear_requests()
    aioclient_mock.get(CONTROLLER_URL, status=500)
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success

    state = hass.states.get(CALL_BUTTON)
    assert state.state != STATE_UNAVAILABLE
    assert state.state == rang.state
    assert state.last_changed == rang.last_changed


async def test_http_event_recorded(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    entry: MockConfigEntry,
) -> None:
    """Every webhook request is counted; the last one is kept for diagnostics."""
    assert entry.runtime_data.http_events.requests == 0
    assert entry.runtime_data.http_events.last is None

    client = await hass_client_no_auth()
    await client.get(f"{WEBHOOK_URL}?event=call")
    await client.post(WEBHOOK_URL, data="event=motion")
    await hass.async_block_till_done()

    stats = entry.runtime_data.http_events
    assert stats.requests == 2
    assert stats.last is not None
    assert stats.last.method == "POST"
    assert stats.last.event == "motion"
    assert stats.last.rang is False
    assert stats.last.received_at
