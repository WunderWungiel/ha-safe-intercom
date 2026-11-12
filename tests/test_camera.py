"""Tests for the SAFE Intercom cameras and the client's camera calls."""

import aiohttp
from homeassistant.components.camera import (
    async_get_image,
    async_get_stream_source,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.safe_intercom.api import (
    STREAM_MAIN,
    STREAM_SUB,
    RtspInfo,
    SafeIntercomAuthError,
    SafeIntercomClient,
    SafeIntercomCommandError,
    SafeIntercomConnectionError,
)
from custom_components.safe_intercom.const import DOMAIN

from .const import (
    HOST,
    MAC,
    NETWORK_RESPONSE,
    RTSP_RESPONSE,
    SYSTEMINFO_RESPONSE,
    controller_response,
    hanging_stream,
)

NETWORK_URL = f"http://{HOST}/cgi-bin/network_cgi?action=get"
SYSTEMINFO_URL = f"http://{HOST}/cgi-bin/systeminfo_cgi"
CONTROLLER_URL = f"http://{HOST}/cgi-bin/controller_cgi?action=get"
ALARM_URL = (
    f"http://{HOST}/cgi-bin/alarmchangestate_cgi?channel=0&parameter=SensorOutAlarm"
)
RTSP_URL = f"http://{HOST}/cgi-bin/rtsp_cgi?action=get"
SNAPSHOT_URL = f"http://{HOST}/cgi-bin/images_cgi?channel=0"


RTSP_RESPONSE_AUTH = RTSP_RESPONSE.replace("RtspAuth=close", "RtspAuth=open")

JPEG = b"\xff\xd8\xff\xe0fake jpeg\xff\xd9"

MAIN = "camera.intercom_main_stream"
SUB = "camera.intercom_alternative_stream"


@pytest.fixture
def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> SafeIntercomClient:
    """A client using the mocked aiohttp session."""
    return SafeIntercomClient(
        async_get_clientsession(hass), HOST, 80, "admin", "p@ss:w/rd"
    )


async def test_get_rtsp_info(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """The spec's rtsp_cgi example is parsed."""
    aioclient_mock.get(RTSP_URL, text=RTSP_RESPONSE)
    assert await client.async_get_rtsp_info() == RtspInfo(
        enabled=True, port=8554, auth=False
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Unknown auth setting: keep credentials in the URL.
        ("", RtspInfo(enabled=True, port=554, auth=True)),
        # The device's error form (HTTP 200) carries no settings.
        ("Request failed:Param error\r\n", RtspInfo(enabled=True, port=554, auth=True)),
        ("RtspAuth=close\r\n", RtspInfo(enabled=True, port=554, auth=False)),
        (
            "RtspSwitch=close\r\nRtspAuth=open\r\nRtspPort=junk\r\n",
            RtspInfo(enabled=False, port=554, auth=True),
        ),
    ],
)
async def test_get_rtsp_info_defaults(
    aioclient_mock: AiohttpClientMocker,
    client: SafeIntercomClient,
    text: str,
    expected: RtspInfo,
) -> None:
    """Missing or invalid keys fall back to the factory defaults."""
    aioclient_mock.get(RTSP_URL, text=text)
    assert await client.async_get_rtsp_info() == expected


def test_rtsp_url(client: SafeIntercomClient) -> None:
    """Credentials are percent-encoded, and left out when RTSP auth is off."""
    assert (
        client.rtsp_url(RtspInfo(enabled=True, port=8554, auth=True), STREAM_MAIN)
        == f"rtsp://admin:p%40ss%3Aw%2Frd@{HOST}:8554/av0_0"
    )
    assert (
        client.rtsp_url(RtspInfo(enabled=True, port=8554, auth=False), STREAM_SUB)
        == f"rtsp://{HOST}:8554/av0_1"
    )
    assert (
        client.rtsp_url(RtspInfo(enabled=False, port=554, auth=True), STREAM_MAIN)
        is None
    )
    # Settings unknown: default port, credentials included to be safe.
    assert (
        client.rtsp_url(None, STREAM_MAIN)
        == f"rtsp://admin:p%40ss%3Aw%2Frd@{HOST}:554/av0_0"
    )


async def test_get_snapshot(
    aioclient_mock: AiohttpClientMocker, client: SafeIntercomClient
) -> None:
    """A JPEG is returned as-is, fetched with Basic auth."""
    aioclient_mock.get(SNAPSHOT_URL, content=JPEG)
    assert await client.async_get_snapshot() == JPEG
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"text": "Request failed:Param error\r\n"}, SafeIntercomCommandError),
        ({"text": ""}, SafeIntercomCommandError),
        ({"status": 401}, SafeIntercomAuthError),
        ({"status": 500}, SafeIntercomConnectionError),
        ({"exc": aiohttp.ClientError}, SafeIntercomConnectionError),
    ],
)
async def test_get_snapshot_errors(
    aioclient_mock: AiohttpClientMocker,
    client: SafeIntercomClient,
    kwargs: dict,
    error: type[Exception],
) -> None:
    """Failures map to the client's error types."""
    aioclient_mock.get(SNAPSHOT_URL, **kwargs)
    with pytest.raises(error):
        await client.async_get_snapshot()


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    *,
    enable_sub: bool = False,
    rtsp_kwargs: dict | None = None,
    snapshot_kwargs: dict | None = None,
) -> MockConfigEntry:
    aioclient_mock.get(ALARM_URL, side_effect=hanging_stream)
    aioclient_mock.get(NETWORK_URL, text=NETWORK_RESPONSE)
    aioclient_mock.get(SYSTEMINFO_URL, text=SYSTEMINFO_RESPONSE)
    aioclient_mock.get(CONTROLLER_URL, text=controller_response())
    aioclient_mock.get(RTSP_URL, **(rtsp_kwargs or {"text": RTSP_RESPONSE}))
    aioclient_mock.get(SNAPSHOT_URL, **(snapshot_kwargs or {"content": JPEG}))
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
    if enable_sub:
        er.async_get(hass).async_get_or_create(
            "camera",
            DOMAIN,
            f"{MAC}_sub_stream",
            config_entry=entry,
            suggested_object_id="intercom_alternative_stream",
            disabled_by=None,
        )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_cameras_created(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The main stream is enabled, the alternative one disabled by default."""
    await _setup(hass, aioclient_mock)
    registry = er.async_get(hass)
    main = registry.async_get(MAIN)
    sub = registry.async_get(SUB)
    assert main is not None
    assert main.unique_id == f"{MAC}_main_stream"
    assert main.disabled_by is None
    assert sub is not None
    assert sub.unique_id == f"{MAC}_sub_stream"
    assert sub.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(MAIN) is not None
    assert hass.states.get(SUB) is None


async def test_stream_sources(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each camera points at its own stream, on the port the device reports."""
    await _setup(
        hass, aioclient_mock, enable_sub=True, rtsp_kwargs={"text": RTSP_RESPONSE_AUTH}
    )
    assert (
        await async_get_stream_source(hass, MAIN)
        == f"rtsp://admin:secret@{HOST}:8554/av0_0"
    )
    assert (
        await async_get_stream_source(hass, SUB)
        == f"rtsp://admin:secret@{HOST}:8554/av0_1"
    )


async def test_stream_source_without_auth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With RTSP auth off, the URL carries no credentials."""
    await _setup(hass, aioclient_mock)
    assert await async_get_stream_source(hass, MAIN) == f"rtsp://{HOST}:8554/av0_0"


async def test_stream_source_no_device_io(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The stream source is known after setup; asking for it hits no endpoint."""
    await _setup(hass, aioclient_mock)
    calls = aioclient_mock.call_count
    await async_get_stream_source(hass, MAIN)
    await async_get_stream_source(hass, MAIN)
    assert aioclient_mock.call_count == calls


async def test_rtsp_disabled_snapshot_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With RTSP off the camera offers no stream but still shows snapshots."""
    await _setup(hass, aioclient_mock, rtsp_kwargs={"text": "RtspSwitch=close\r\n"})
    assert hass.states.get(MAIN).attributes["supported_features"] == 0
    assert await async_get_stream_source(hass, MAIN) is None
    assert (await async_get_image(hass, MAIN)).content == JPEG


async def test_rtsp_info_unavailable_uses_defaults(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Firmware without rtsp_cgi still loads, using the default port."""
    entry = await _setup(hass, aioclient_mock, rtsp_kwargs={"status": 404})
    assert entry.state is ConfigEntryState.LOADED
    assert (
        await async_get_stream_source(hass, MAIN)
        == f"rtsp://admin:secret@{HOST}:554/av0_0"
    )


async def test_rtsp_info_auth_error_still_loads(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """rtsp_cgi needs admin rights (spec §28); a refusal keeps the defaults.

    network_cgi has already accepted the credentials, so this is no reason
    to fail setup or ask for reauthentication.
    """
    entry = await _setup(hass, aioclient_mock, rtsp_kwargs={"status": 401})
    assert entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress()
    assert (
        await async_get_stream_source(hass, MAIN)
        == f"rtsp://admin:secret@{HOST}:554/av0_0"
    )


async def test_camera_image(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The snapshot comes from images_cgi."""
    await _setup(hass, aioclient_mock)
    image = await async_get_image(hass, MAIN)
    assert image.content == JPEG
    assert image.content_type == "image/jpeg"


async def test_camera_image_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused snapshot surfaces as a translated HomeAssistantError."""
    await _setup(
        hass,
        aioclient_mock,
        snapshot_kwargs={"text": "Request failed:Param error\r\n"},
    )
    with pytest.raises(HomeAssistantError) as err:
        await async_get_image(hass, MAIN)
    assert err.value.translation_key == "snapshot_failed"


async def test_camera_image_auth_error_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A snapshot refused for bad credentials starts reauth."""
    await _setup(hass, aioclient_mock, snapshot_kwargs={"status": 401})
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, MAIN)
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
