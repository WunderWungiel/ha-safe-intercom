# Camera Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the camera entities added in `20a8c2a` follow the patterns of core camera integrations: no device I/O in `stream_source()`, an RTSP URL in a format verified on the device, snapshot failures that surface properly, and camera data in diagnostics.

**Architecture:** RTSP settings are read once at setup, the way Foscam and Reolink do it, and kept in `SafeIntercomData`. Each camera builds its URL from them, so `stream_source()` makes no network calls, and a camera only gets `CameraEntityFeature.STREAM` when RTSP is on (DoorBird and Reolink pattern). Snapshot errors raise a translated `HomeAssistantError` (Reolink pattern), and an auth error starts reauth.

**Tech Stack:** Home Assistant custom integration, aiohttp, yarl, pytest-homeassistant-custom-component.

**Spec:** `AGENTS.md` (project rules and observed device behaviour) plus the review findings below. Vendor references: `docs/API_Safe-IP/API, HTTP - Interface Specification (working)_20151120.pdf` §1, §2, §9, §28 and `docs/web_ip_safe.pdf` §8.8.

### Review findings this plan addresses

1. `stream_source()` calls `rtsp_cgi` on every call. HA calls it when each camera is added (WebRTC provider pick, `camera/webrtc.py`), on every go2rtc offer, and when browsing media. It also runs inside `CAMERA_STREAM_SOURCE_TIMEOUT` (10 s), which is the same as our `REQUEST_TIMEOUT`. When the device is offline, the view stalls and the stream can fail to start. HA also caches the `Stream` object after the first `async_create_stream`, so the claim that the port "is read each time a stream starts" is only true once. Core integrations (Foscam, Reolink, ONVIF, DoorBird, Axis) work out the URL once and return it with no I/O.
2. If RTSP is off when HA starts, the camera still advertises `STREAM`, and `stream_source()` returns `None` at provider-pick time. DoorBird and Reolink only set `STREAM` when a stream URL exists.
3. The RTSP login format is unverified. The web manual §8.8 gives `rtsp://host:port/av0_0&user=<u>&password=<p>`. That suffix is part of the *path*, so HA's `stream.redact_credentials` (which only redacts userinfo and the `?user=`/`?password=`/`?auth=` query keys) would **not** hide the password in logs. `RtspInfo.auth` is parsed but never used.
4. `PARALLEL_UPDATES = 1` and its comment are wrong. The semaphore only covers `async_update` and service calls, not `async_camera_image`. Read-only platforms backed by a coordinator use `0`.
5. Snapshot errors are logged at debug level and return `None`. An auth error never starts reauth, and the user only sees HA's generic "Unable to get image".
6. Diagnostics have no camera data. The device can encode MJPEG (spec §9), which HA's HLS stream can't play, so the codec is the first thing to check when a stream won't play. Axis puts redacted camera sources into diagnostics.
7. `async_get_snapshot` repeats `_get_text`'s status and error handling.

## Global Constraints

- `api.py` has no Home Assistant imports.
- Never log credentials; redact them in diagnostics.
- Translations: `strings.json` mirrored in `translations/en.json`, Polish in `translations/pl.json`.
- Tests mock HTTP. Never send `set` / `alarmout_cgi` / `restart_cgi` / `factorydefault_cgi` requests to a real device unless the user explicitly asks.
- Unique IDs stay `f"{mac}_main_stream"` / `f"{mac}_sub_stream"`, so existing entities are kept.
- Must pass: `uv run ruff check . && uv run ruff format --check . && uv run pytest`.

## Review Focus

- RTSP turned off on the device at setup: cameras still show snapshots and don't advertise `STREAM`. (Task 2, `test_rtsp_disabled_snapshot_only`)
- Old firmware without `rtsp_cgi` (HTTP 404): setup still succeeds and uses port 554 with credentials. (Task 2, `test_rtsp_info_unavailable_uses_defaults`)
- Credentials rejected by `rtsp_cgi` or `images_cgi`: a reauth flow starts instead of a silent failure. (Task 2, `test_rtsp_info_auth_error_starts_reauth`; Task 3, `test_camera_image_auth_error_starts_reauth`)
- Device offline after setup: `stream_source()` returns at once with no HTTP request. (Task 2, `test_stream_source_no_device_io`)
- Password with `@ : /` characters: still percent-encoded correctly in the RTSP URL. (Task 2, `test_rtsp_url` keeps the `p@ss:w/rd` case)

---

### Task 1: Verify RTSP auth and codecs on the real device (manual, read-only)

The user runs these commands, or Claude runs them after the user gives the host and approves. Every request is a `get` or an RTSP DESCRIBE. Do **not** toggle `RtspAuth` from the CLI: if testing with auth on is wanted, the user switches it in the web UI themselves.

**Files:**
- Modify: `AGENTS.md` (sections "Key endpoints" and "Observed device behaviour")

**Interfaces:**
- Produces: the decision **URL_FORM** used by Task 2. It is `userinfo` (`rtsp://u:p@host:port/av0_N`) unless only the manual's suffix form works with auth on, in which case it is `suffix` (`rtsp://host:port/av0_N&user=u&password=p`).

- [ ] **Step 1: Read RTSP and encoder settings**

```bash
read -rp 'Host: ' H; read -rp 'User: ' U; read -rsp 'Password: ' P; echo
curl -su "$U:$P" "http://$H/cgi-bin/rtsp_cgi?action=get"
curl -su "$U:$P" "http://$H/cgi-bin/videocoding_cgi?action=get&channel=0"
```

Expected: `RtspSwitch`, `RtspAuth`, `RtspPort`, and `EncType1`/`EncType2`, `Resolution1`/`Resolution2`. Write down `RtspAuth` and the port.

- [ ] **Step 2: Probe the three URL forms on both streams**

```bash
PORT=554   # from step 1
for s in 0 1; do
  for url in "rtsp://$H:$PORT/av0_$s" "rtsp://$U:$P@$H:$PORT/av0_$s" "rtsp://$H:$PORT/av0_$s&user=$U&password=$P"; do
    echo "== ${url//$P/***}"; ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name,width,height -of compact "$url"
  done
done
```

Expected: codec and size for each form that works. If `RtspAuth=close`, all three should work. Ask the user whether to repeat with auth switched on in the web UI. Without that check, the auth-on behaviour stays marked unverified.

- [ ] **Step 3: Check that a snapshot works while a stream is open**

```bash
ffplay -rtsp_transport tcp "rtsp://$U:$P@$H:$PORT/av0_0" & sleep 5
curl -su "$U:$P" -o /tmp/snap.jpg -w '%{http_code} %{content_type}\n' "http://$H/cgi-bin/images_cgi?channel=0"; kill %1
```

Expected: `200 image/jpeg`. If it fails, record that the device handles one connection at a time. This plan then needs a serialising lock added in the client (Amcrest pattern), as a follow-up task.

- [ ] **Step 4: Record results in `AGENTS.md`**

Replace the "**not yet verified**" sentence under `rtsp_cgi` with the outcome: which forms work with auth off and with auth on (or "auth-on untested"), the codecs and resolutions seen, and the result of step 3. Keep the device model and firmware in the note.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "Record verified RTSP URL and codec behaviour"
```

---

### Task 2: Read RTSP settings once at setup; no I/O in `stream_source()`

**Files:**
- Modify: `custom_components/safe_intercom/api.py` (`rtsp_url`)
- Modify: `custom_components/safe_intercom/__init__.py` (`SafeIntercomData`, `async_setup_entry`)
- Modify: `custom_components/safe_intercom/camera.py`
- Modify: `README.md` (Cameras section)
- Test: `tests/test_camera.py`

**Interfaces:**
- Consumes: URL_FORM from Task 1; the existing `SafeIntercomClient.async_get_rtsp_info() -> RtspInfo`.
- Produces:
  - `SafeIntercomData.rtsp: RtspInfo | None = None`: `None` means "rtsp_cgi not available, assume defaults".
  - `SafeIntercomClient.rtsp_url(self, rtsp: RtspInfo | None, stream: int) -> str | None`: `None` when `rtsp is not None and not rtsp.enabled`. Port is `rtsp.port` or `DEFAULT_RTSP_PORT`. Credentials are left out when `rtsp is not None and not rtsp.auth`. Otherwise they go in as URL_FORM, built with `yarl.URL.build` so the userinfo is percent-encoded.

- [ ] **Step 1: Write the failing tests**

In `tests/test_camera.py`, add `RTSP_RESPONSE_AUTH = RTSP_RESPONSE.replace("RtspAuth=close", "RtspAuth=open")` and:

```python
def test_rtsp_url(client):  # replaces the existing test
    on = RtspInfo(enabled=True, port=8554, auth=True)
    assert client.rtsp_url(on, STREAM_MAIN) == f"rtsp://admin:p%40ss%3Aw%2Frd@{HOST}:8554/av0_0"
    assert client.rtsp_url(RtspInfo(True, 8554, False), STREAM_SUB) == f"rtsp://{HOST}:8554/av0_1"
    assert client.rtsp_url(RtspInfo(False, 554, True), STREAM_MAIN) is None
    assert client.rtsp_url(None, STREAM_MAIN) == f"rtsp://admin:p%40ss%3Aw%2Frd@{HOST}:554/av0_0"

async def test_stream_sources(hass, aioclient_mock):  # replaces the existing test
    await _setup(hass, aioclient_mock, enable_sub=True, rtsp_kwargs={"text": RTSP_RESPONSE_AUTH})
    assert await async_get_stream_source(hass, MAIN) == f"rtsp://admin:secret@{HOST}:8554/av0_0"
    assert await async_get_stream_source(hass, SUB) == f"rtsp://admin:secret@{HOST}:8554/av0_1"

async def test_stream_source_without_auth(hass, aioclient_mock):
    await _setup(hass, aioclient_mock)  # RTSP_RESPONSE has RtspAuth=close
    assert await async_get_stream_source(hass, MAIN) == f"rtsp://{HOST}:8554/av0_0"

async def test_stream_source_no_device_io(hass, aioclient_mock):
    await _setup(hass, aioclient_mock)
    calls = aioclient_mock.call_count
    await async_get_stream_source(hass, MAIN)
    await async_get_stream_source(hass, MAIN)
    assert aioclient_mock.call_count == calls

async def test_rtsp_disabled_snapshot_only(hass, aioclient_mock):
    await _setup(hass, aioclient_mock, rtsp_kwargs={"text": "RtspSwitch=close\r\n"})
    assert hass.states.get(MAIN).attributes["supported_features"] == 0
    assert await async_get_stream_source(hass, MAIN) is None
    assert (await async_get_image(hass, MAIN)).content == JPEG

async def test_rtsp_info_unavailable_uses_defaults(hass, aioclient_mock):  # replaces test_stream_source_rtsp_info_unavailable
    entry = await _setup(hass, aioclient_mock, rtsp_kwargs={"status": 404})
    assert entry.state is ConfigEntryState.LOADED
    assert await async_get_stream_source(hass, MAIN) == f"rtsp://admin:secret@{HOST}:554/av0_0"

async def test_rtsp_info_auth_error_starts_reauth(hass, aioclient_mock):
    entry = await _setup(hass, aioclient_mock, rtsp_kwargs={"status": 401})
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())
```

`_setup` currently does `assert await hass.config_entries.async_setup(...)`. Change it to `await hass.config_entries.async_setup(...)` without the assert, so the 401 case can return; tests that need a loaded entry check `entry.state`. Delete `test_stream_source_rtsp_disabled` (replaced by `test_rtsp_disabled_snapshot_only`) and `test_stream_source_keeps_last_rtsp_info` (that behaviour no longer exists). If Task 1 chose URL_FORM = `suffix`, the expected auth-on URLs become `rtsp://{HOST}:8554/av0_0&user=admin&password=secret` (and `p%40ss%3Aw%2Frd` for the encoded case).

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `uv run pytest tests/test_camera.py -v`
Expected: the new tests FAIL (`rtsp_url()` signature mismatch, extra `rtsp_cgi` calls, `supported_features == 2`, 401 leaves the entry LOADED).

- [ ] **Step 3: Change `rtsp_url` in `api.py`** to the signature in the Interfaces block. Keep the docstring warning that the result contains the password.

- [ ] **Step 4: Read RTSP settings in `async_setup_entry`**

Right after the `system` lookup, call `client.async_get_rtsp_info()`. `SafeIntercomAuthError` raises `ConfigEntryAuthFailed("Invalid credentials")`. `SafeIntercomError` logs at debug `"No RTSP settings from %s: %s"` with `client.host` and sets `rtsp = None`. Pass `rtsp=rtsp` into `SafeIntercomData`, and add the field there with a docstring.

- [ ] **Step 5: Rework `camera.py`**

- In `__init__`, set `self._stream_url = client.rtsp_url(data.rtsp, description.stream)`, and set `_attr_supported_features = CameraEntityFeature.STREAM` only when it is not `None` (remove the class-level attribute and `self._rtsp`).
- `stream_source()` returns `self._stream_url`.
- Mark `stream_source` and `async_camera_image` with `typing.override`, like core does.
- Set `PARALLEL_UPDATES = 0` with the comment `# Coordinator-backed and read-only; camera images are not throttled by this.`
- Update the module docstring: RTSP settings are read at setup; reload the entry after changing them on the device.

- [ ] **Step 6: Run the tests to make sure they pass**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Update the README Cameras section**

Replace "the port is read from the device each time a stream starts" with: the RTSP settings (on/off, port, authentication) are read when the integration loads, so reload it after changing them; the stream must be H.264, because MJPEG isn't supported by HA's stream. If URL_FORM = `suffix`, add a note that the password then appears in the stream URL that HA's stream logging prints.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add custom_components/safe_intercom/{api,__init__,camera}.py tests/test_camera.py README.md
git commit -m "Read RTSP settings at setup and only offer streaming when RTSP is on"
```

---

### Task 3: Snapshot errors: translated error, reauth, shared request helper

**Files:**
- Modify: `custom_components/safe_intercom/api.py` (`_get_text`, `async_get_snapshot`)
- Modify: `custom_components/safe_intercom/camera.py` (`async_camera_image`)
- Modify: `custom_components/safe_intercom/strings.json`, `translations/en.json`, `translations/pl.json`
- Test: `tests/test_camera.py`

**Interfaces:**
- Consumes: the camera from Task 2.
- Produces: `SafeIntercomClient._get_bytes(self, path: str, params: dict[str, str] | None = None) -> bytes`, with the status and error mapping that `_get_text` has today. `_get_text` returns `(await self._get_bytes(...)).decode(errors="replace")`. `async_get_snapshot` uses `_get_bytes` and keeps its JPEG start-bytes check. Translation key `exceptions.snapshot_failed`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_camera_image_failed(hass, aioclient_mock):  # replaces the existing test
    await _setup(hass, aioclient_mock, snapshot_kwargs={"text": "Request failed:Param error\r\n"})
    with pytest.raises(HomeAssistantError) as err:
        await async_get_image(hass, MAIN)
    assert err.value.translation_key == "snapshot_failed"

async def test_camera_image_auth_error_starts_reauth(hass, aioclient_mock):
    await _setup(hass, aioclient_mock, snapshot_kwargs={"status": 401})
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, MAIN)
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())
```

The existing `test_get_snapshot` / `test_get_snapshot_errors` stay as they are and cover the refactor.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `uv run pytest tests/test_camera.py -k "camera_image" -v`
Expected: FAIL (translation_key is `None`; no reauth flow).

- [ ] **Step 3: Add the translations**

`exceptions.snapshot_failed.message`: en `"Failed to get a snapshot from the intercom."`, pl `"Nie udało się pobrać zdjęcia z bramofonu."` Add it to `strings.json` and both translation files, next to `relay_command_failed`.

- [ ] **Step 4: Change `async_camera_image`**

On `SafeIntercomAuthError`: call `self.coordinator.config_entry.async_start_reauth(self.hass)`, then raise. On any `SafeIntercomError`: `raise HomeAssistantError(translation_domain=DOMAIN, translation_key="snapshot_failed") from err`. Remove the debug-log-and-return-`None` path.

- [ ] **Step 5: Add `_get_bytes` in `api.py`** as in the Interfaces block. Move the shared code out of `_get_text` and `async_get_snapshot`.

- [ ] **Step 6: Run the tests to make sure they pass**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add custom_components/safe_intercom tests/test_camera.py
git commit -m "Raise translated snapshot errors and start reauth on rejected credentials"
```

---

### Task 4: Camera data in diagnostics

**Files:**
- Modify: `custom_components/safe_intercom/api.py`
- Modify: `custom_components/safe_intercom/diagnostics.py`
- Test: `tests/test_diagnostics.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `SafeIntercomData.rtsp` (Task 2).
- Produces: `SafeIntercomClient.async_get_video_coding(self) -> dict[str, str]`, which GETs `/cgi-bin/videocoding_cgi?action=get&channel=0` and returns `_parse_key_values(...)`. Diagnostics gain two keys: `"rtsp"` (`asdict(data.rtsp)` or `None`) and `"video_coding"` (the dict, or `{"error": str(err)}` on `SafeIntercomError`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`, add `test_get_video_coding`. Mock the spec §9.1 response body (`EncTypes= H.264,MJPEG` … `ResolutionList2=352*288,176*144`) and assert `result["EncType1"] == "H.264"`, `result["Resolution2"] == "352*288"`, and that the section header lines (`Main stream options:`) are not keys.

In `tests/test_diagnostics.py`, extend the setup with mocks for `rtsp_cgi` (RtspAuth=open, port 8554) and `videocoding_cgi`, then assert:

```python
assert diag["rtsp"] == {"enabled": True, "port": 8554, "auth": True}
assert diag["video_coding"]["EncType1"] == "H.264"
assert "secret" not in json.dumps(diag)
```

Add a second case where `videocoding_cgi` returns 500 → `diag["video_coding"] == {"error": ANY}`.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `uv run pytest tests/test_api.py tests/test_diagnostics.py -v`
Expected: FAIL (`async_get_video_coding` missing; `rtsp` key missing).

- [ ] **Step 3: Implement `async_get_video_coding` and the two diagnostics keys** as in the Interfaces block.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add custom_components/safe_intercom/{api,diagnostics}.py tests/test_api.py tests/test_diagnostics.py
git commit -m "Add RTSP settings and video encoding to diagnostics"
```

---

## Deliberately left out

- **Live refresh of RTSP settings / `Stream.update_source()`:** core integrations (Foscam, Reolink, ONVIF) don't do this either. A reload is enough, and the README says so.
- **Returning the last image on failure (DoorBird):** for a door camera, a stale picture while the device is down is misleading.
- **Snapshot dedupe or lock (Amcrest):** only if Task 1 step 3 shows the device can't serve a snapshot while a stream is open.
- **`use_stream_for_stills`:** the device has a real snapshot endpoint.
