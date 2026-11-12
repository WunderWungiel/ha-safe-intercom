# Call Button, Motion and Alarm Inputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the call button as a doorbell `event` entity (fed by the device's "HTTP event" to a Home Assistant webhook), plus motion and alarm-input `binary_sensor`s, both fed by the existing `alarmchangestate_cgi` push stream.

**Architecture:** Part A widens the existing alarm stream subscription to `MotionDetection;SensorInAlarm;SensorOutAlarm`. The coordinator keeps the pushed motion and input state next to the polled controller state and notifies its listeners when that state changes. Part B gives each config entry a webhook ID. The integration registers a local-only webhook and shows the user the host, path and parameter to enter in the intercom's web UI (it never writes them to the device). A call-button request fires a dispatcher signal that the event entity turns into a `ring` event.

**Tech Stack:** Home Assistant 2026.10 custom integration, aiohttp/yarl, `homeassistant.components.webhook`, `event` + `binary_sensor` platforms, pytest-homeassistant-custom-component.

**Spec:** `AGENTS.md` ("Call button", "Key endpoints", "Observed device behaviour") and `docs/web_ip_safe.pdf` §8.13 / §10.2. Stream format: HTTP API spec §20.1.

## Global Constraints

- `api.py` imports nothing from Home Assistant.
- Never send `set`/`alarmout_cgi`/`controller_cgi?action=set` to a real device. The integration only *displays* HTTP-event settings; the user enters them in the web UI. Tests mock all HTTP.
- `has_entity_name = True`, translated names in `strings.json` + `translations/en.json` (identical) + `translations/pl.json`.
- Unique IDs are `f"{mac}_{key}"` via `SafeIntercomEntity`, never the host.
- Never log credentials or the webhook ID. Redact `webhook_id` in diagnostics.
- Spec facts used verbatim:
  - Stream line: `<date>;<time>;<alarmType>;<0|1>[;<areas>|;<sensorNo>]`. `MotionDetection` carries comma-separated zones; `SensorInAlarm`/`SensorOutAlarm` carry a 0-based sensor number.
  - Spec example: `MotionDetection;1;0,1` then `MotionDetection;0;0` then `MotionDetection;0;1`, so "off" is reported per zone.
  - HTTP event request: `http://<Host name>/<Request>?<HTTP event parameter>`, GET or POST. The host field rejects `&` and `=`. The per-trigger parameter allows up to 127 characters, `&` and `=` included.
  - Modbus registers 106–108 are alarm inputs CH1–CH3, so assume **3 inputs**.
- Commands: `uv run ruff check . && uv run ruff format --check . && uv run pytest`.

## Review Focus

1. **`;` in the stream query.** yarl encodes `;` as `%3B` (checked: `with_query` gives `parameter=MotionDetection%3BSensorInAlarm%3BSensorOutAlarm`). The device's CGI may not decode it, and then *relay* updates break too, which is a regression. Task 0 decides the form; Task 1 pins the exact URL in a test.
2. **Motion stuck "on" after a dropped stream.** A missed `…;0;<zone>` would leave motion on forever. On disconnect the coordinator clears zones and inputs, and the sensors go unavailable (Task 2 tests).
3. **Unknown or garbage webhook requests** (wrong `event` value, empty body, a browser hitting the URL): return 200, fire nothing, don't raise (Task 5 test).
4. **Entries created before this change have no webhook ID.** Migration 1.1 → 1.2 adds one, so existing users aren't forced to re-add the device (Task 4 test).
5. **No reachable HA URL** (`get_url` raises `NoURLAvailableError`): the options form still opens, with a fallback placeholder (Task 6 test).

---

### Task 0: Verify the multi-type stream on the real device (manual, read-only)

Done by the user. This is a GET of a stream and changes nothing on the device. The result decides Task 1's URL encoding and fills the AGENTS.md notes in Task 7.

- [ ] **Step 1:** Run both commands, wave at the camera, then switch a relay from the SAFE app. Stop each with Ctrl-C after about 30 s.

```bash
curl -su admin:PASS --no-buffer 'http://HOST/cgi-bin/alarmchangestate_cgi?channel=0&parameter=MotionDetection;SensorInAlarm;SensorOutAlarm'
curl -su admin:PASS --no-buffer 'http://HOST/cgi-bin/alarmchangestate_cgi?channel=0&parameter=MotionDetection%3BSensorInAlarm%3BSensorOutAlarm'
```

- [ ] **Step 2:** Record which form delivers both `MotionDetection` and `SensorOutAlarm` lines, and copy one real line of each type. If motion detection is off in the web UI (Configuration → Recording → Motion detection), note that, because the sensor will then stay off.

If **neither** form delivers all types, stop and re-plan Part A (fallback: one stream per type).

---

## Part A: Motion and alarm inputs from the push stream

### Task 1: Subscribe the stream to all three alarm types

**Files:**
- Modify: `custom_components/safe_intercom/api.py` (constants near `ALARM_SENSOR_OUT`; `async_alarm_events`)
- Modify: `tests/const.py` (add the shared `ALARM_URL`)
- Modify: every test that defines its own `ALARM_URL` (`grep -rn "ALARM_URL =" tests`) so it imports it from `tests/const.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `ALARM_MOTION = "MotionDetection"` and `ALARM_SENSOR_IN = "SensorInAlarm"` in `api.py` (`ALARM_SENSOR_OUT` already exists). `async_alarm_events` now yields events of all three types; its signature is unchanged.
- Produces (tests): `tests/const.ALARM_URL`, the exact stream URL.

- [ ] **Step 1: Write the failing test.** In `test_api.py`, `test_alarm_stream_subscribes_all_types` asserts that the requested URL's raw query string is exactly `channel=0&parameter=MotionDetection;SensorInAlarm;SensorOutAlarm` (or the `%3B` form if Task 0 said so). Use `str(url)` on `aioclient_mock.mock_calls[0][1]`. Also assert that a `SensorInAlarm;1;2` line yields `AlarmEvent("SensorInAlarm", True, (), 2)`.
- [ ] **Step 2:** Run `uv run pytest tests/test_api.py -k subscribes -v`. Expected: FAIL on the URL.
- [ ] **Step 3: Implement.** Build the query so `;` goes out literally: `self._base_url.with_path(...)` followed by `.with_query(...)` is not enough. Use `URL(f"{base}/cgi-bin/alarmchangestate_cgi?channel=0&parameter={...}", encoded=True)`, where the value is `";".join((ALARM_MOTION, ALARM_SENSOR_IN, ALARM_SENSOR_OUT))`. Update the docstring ("Yield alarm changes…").
- [ ] **Step 4:** Point all tests at `tests/const.ALARM_URL` and run `uv run pytest`. Expected: all PASS. If the mocker fails to match, compare `str()` of both URLs.
- [ ] **Step 5: Commit** `Subscribe the alarm stream to motion and alarm inputs`.

### Task 2: Keep pushed motion and input state in the coordinator

**Files:**
- Modify: `custom_components/safe_intercom/coordinator.py`
- Test: `tests/test_coordinator_stream.py` (new; drive the stream with an aiohttp side effect that yields lines, the same way the existing switch tests feed `SensorOutAlarm`)

**Interfaces:**
- Consumes: `ALARM_MOTION`, `ALARM_SENSOR_IN` (Task 1).
- Produces on `SafeIntercomCoordinator`:
  - `motion: bool` property: true while any zone is active.
  - `input_state(number: int) -> bool | None`: `number` is **1-based** (sensor 0 → input 1). `None` means no event seen since the stream (re)connected.
  - `stream_connected: bool` property (backed by the existing `_stream_connected`).
  - Calls `self.async_update_listeners()` whenever motion, any input or `stream_connected` changes. `SensorOutAlarm` keeps calling `async_refresh()` as today.

- [ ] **Step 1: Write the failing tests:**
  - `test_motion_zones`: lines `…;MotionDetection;1;0,1`, then `…;0;0` (motion still true), then `…;0;1` (motion false).
  - `test_input_state`: before any event `input_state(1) is None`; after `…;SensorInAlarm;1;0`, `input_state(1) is True`; after `…;0;0`, it is `False`; `input_state(2) is None` throughout.
  - `test_state_cleared_on_disconnect`: after motion on and an input on, the stream closes; then `stream_connected is False`, `motion is False` and `input_state(1) is None`.
  - `test_sensor_out_still_refreshes`: a `SensorOutAlarm` line still triggers a `controller_cgi` read.
- [ ] **Step 2:** Run `uv run pytest tests/test_coordinator_stream.py -v`. Expected: FAIL (`AttributeError: motion`).
- [ ] **Step 3: Implement.** Keep `self._motion_zones: set[int]` and `self._inputs: dict[int, bool]`. In the stream loop, dispatch on `event.alarm_type`: motion adds or discards `event.areas` (an active event with no areas counts as zone 0). For inputs, `event.sensor + 1` maps to `event.active`; skip `sensor is None`. Clear both, and notify, wherever `_stream_connected` becomes False. Notify in `on_connect` too.
- [ ] **Step 4:** Run `uv run pytest`. Expected: all PASS.
- [ ] **Step 5: Commit** `Track motion and alarm inputs from the push stream`.

### Task 3: `binary_sensor` platform

**Files:**
- Create: `custom_components/safe_intercom/binary_sensor.py`
- Modify: `custom_components/safe_intercom/__init__.py` (`PLATFORMS` gets `Platform.BINARY_SENSOR`)
- Modify: `custom_components/safe_intercom/const.py`: `SUPPORTED_INPUTS: Final = (1, 2, 3)` with a docstring citing Modbus registers 106–108
- Modify: `strings.json`, `translations/en.json`, `translations/pl.json`
- Test: `tests/test_binary_sensor.py`

**Interfaces:**
- Consumes: `coordinator.motion`, `coordinator.input_state(n)`, `coordinator.stream_connected` (Task 2).
- Produces: entities with keys `motion` (translation_key `motion`, `BinarySensorDeviceClass.MOTION`, enabled by default) and `alarm_input_{n}` (translation_key `alarm_input`, placeholder `number`, no device class, **disabled by default** because nothing says what is wired).

- [ ] **Step 1: Write the failing tests** (set up like `test_switch.py`'s `_setup`):
  - `test_motion_sensor`: `binary_sensor.intercom_motion` is `off`, turns `on` after a motion line and back `off` after the zone clears.
  - `test_unavailable_without_stream`: the stream mock raises a connection error, so the motion state is `unavailable`.
  - `test_inputs_disabled_by_default`: the registry has `binary_sensor.intercom_alarm_input_1..3` with `disabled_by=INTEGRATION`. After enabling input 1 and reloading, its state is `unknown` until a `SensorInAlarm;1;0` line arrives, then it is `on`.
- [ ] **Step 2:** Run `uv run pytest tests/test_binary_sensor.py -v`. Expected: FAIL (no entities).
- [ ] **Step 3: Implement** `SafeIntercomMotionSensor` and `SafeIntercomInputSensor(SafeIntercomEntity, BinarySensorEntity)`, `PARALLEL_UPDATES = 0`. `available` is `super().available and self.coordinator.stream_connected`. `is_on` reads the coordinator. Names: en "Motion" / "Alarm input {number}"; pl "Ruch" / "Wejście alarmowe {number}".
- [ ] **Step 4:** Run the full check command from Global Constraints. Expected: clean, all PASS.
- [ ] **Step 5: Commit** `Add motion and alarm input binary sensors`.

---

## Part B: Call button via HTTP event and webhook

### Task 4: Webhook ID on the config entry

**Files:**
- Modify: `custom_components/safe_intercom/config_flow.py` (`MINOR_VERSION = 2`; `_create_entry` adds `CONF_WEBHOOK_ID: webhook.async_generate_id()` to `data`)
- Modify: `custom_components/safe_intercom/__init__.py` (`async_migrate_entry`)
- Modify: `custom_components/safe_intercom/diagnostics.py` (`TO_REDACT` gets `CONF_WEBHOOK_ID`)
- Modify: `custom_components/safe_intercom/manifest.json` (`"dependencies": ["webhook"]`)
- Test: `tests/test_init.py`, `tests/test_config_flow.py`, `tests/test_diagnostics.py`

**Interfaces:**
- Produces: `entry.data[CONF_WEBHOOK_ID]` (`homeassistant.const.CONF_WEBHOOK_ID`) is always present after setup. `async_migrate_entry(hass, entry) -> bool`.

- [ ] **Step 1: Write the failing tests:**
  - `test_migrate_adds_webhook_id`: a `MockConfigEntry(version=1, minor_version=1)` without the key ends up `minor_version == 2` after setup, with a non-empty `data[CONF_WEBHOOK_ID]` and the other data unchanged.
  - Config flow: the created entry's `data` has `CONF_WEBHOOK_ID`. Update the existing equality assertions with `ANY`.
  - Diagnostics: `webhook_id` is `**REDACTED**`.
- [ ] **Step 2:** Run `uv run pytest -k "migrate or webhook or diagnostics or flow" -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The migration returns False for `version > 1`. For `minor_version < 2` it calls `hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_WEBHOOK_ID: webhook.async_generate_id()}, minor_version=2)`.
- [ ] **Step 4:** Run `uv run pytest`. Expected: all PASS.
- [ ] **Step 5: Commit** `Give each intercom entry a webhook ID`.

### Task 5: Parse HTTP events, register the webhook, add the doorbell event entity

**Files:**
- Modify: `custom_components/safe_intercom/api.py`: `HTTP_EVENT_KEY = "event"`, `HTTP_EVENT_CALL = "call"`, `parse_http_event(query: str, body: str) -> str | None`
- Create: `custom_components/safe_intercom/webhook.py`: `async_register_webhook(hass, entry) -> None`, `async_unregister_webhook(hass, entry) -> None`
- Create: `custom_components/safe_intercom/event.py`
- Modify: `custom_components/safe_intercom/const.py`: `def signal_call(entry_id: str) -> str` returning `f"{DOMAIN}_{entry_id}_call"`
- Modify: `__init__.py`: register after the platforms are forwarded, unregister in `async_unload_entry`; `PLATFORMS` gets `Platform.EVENT`
- Modify: `strings.json`, `translations/en.json`, `translations/pl.json`
- Test: `tests/test_api.py`, `tests/test_event.py`

**Interfaces:**
- Consumes: `entry.data[CONF_WEBHOOK_ID]` (Task 4).
- Produces:
  - `parse_http_event`: parses `query` and `body` with `urllib.parse.parse_qs` (`keep_blank_values=True`) and returns the first `event` value from either, query first. Returns None if neither has one.
  - The webhook is registered with `webhook.async_register(hass, DOMAIN, entry.title, webhook_id, handler, local_only=True, allowed_methods=(METH_GET, METH_POST))`. The handler reads `request.query_string` and (POST only) `await request.text()`. If the parsed value is `HTTP_EVENT_CALL` it calls `async_dispatcher_send(hass, signal_call(entry.entry_id))`. Anything else is logged at debug level **without** the webhook ID. The handler always returns `web.Response(status=200)`.
  - Event entity: key `call_button`, translation_key `call_button`, `EventDeviceClass.DOORBELL`, `event_types = ["ring"]`. It connects to `signal_call` in `async_added_to_hass` (through `self.async_on_remove`) and calls `self._trigger_event("ring")` and then `self.async_write_ha_state()`.

- [ ] **Step 1: Write the failing tests:**
  - `test_parse_http_event`, parametrized: `("event=call", "") → "call"`, `("", "event=call") → "call"`, `("event=call", "event=other") → "call"`, `("foo=bar", "") → None`, `("", "") → None`, `("event", "") → ""`.
  - `test_call_button_rings` (`hass_client_no_auth`): GET `/api/webhook/<id>?event=call` returns 200, and `event.intercom_call_button` gets a state with attribute `event_type == "ring"`. The same holds for a POST with body `event=call`.
  - `test_unknown_event_ignored`: `?event=motion` and an empty request both return 200 and leave the entity state `unknown`.
  - `test_webhook_unregistered_on_unload`: after `async_unload`, the same GET fires nothing (HA answers 200 for unknown webhooks, so assert on the entity state), and setting the entry up again doesn't raise "already defined".
- [ ] **Step 2:** Run `uv run pytest tests/test_event.py tests/test_api.py -k "http_event or call or webhook" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces. `event.py` has `PARALLEL_UPDATES = 0`. Translations, en: entity "Call button", state attribute `event_type` value `ring` = "Ring". pl: "Przycisk wywołania", `ring` = "Dzwonek".
- [ ] **Step 4:** Run the full check command. Expected: clean, all PASS.
- [ ] **Step 5: Commit** `Add the call button as a doorbell event via a webhook`.

### Task 6: Show the HTTP event settings in the options flow

**Files:**
- Modify: `custom_components/safe_intercom/config_flow.py` (`_controller_placeholders`, or a new `_http_event_placeholders(hass, entry)`, merged into the options `init` form placeholders and the `unsupported_controller` abort placeholders)
- Modify: `strings.json`, `translations/en.json`, `translations/pl.json` (options `init` description and the `unsupported_controller` abort)
- Test: `tests/test_options_flow.py`

**Interfaces:**
- Consumes: `entry.data[CONF_WEBHOOK_ID]`; `homeassistant.helpers.network.get_url(hass, allow_external=False, allow_cloud=False)` and `webhook.async_generate_path(webhook_id)`.
- Produces: placeholders `ha_protocol` (`HTTP`/`HTTPS`), `ha_host` (`host:port` from the URL; the device's host field takes no path), `webhook_path` (`/api/webhook/<id>`), `call_parameter` (`event=call`). On `NoURLAvailableError`: `ha_protocol` = `HTTP`, `ha_host` = `<Home Assistant IP>:8123`.

- [ ] **Step 1: Write the failing tests:**
  - `test_options_shows_http_event_settings`: with `hass.config.internal_url = "http://192.0.2.50:8123"`, the form's `description_placeholders` equal `{"ha_protocol": "HTTP", "ha_host": "192.0.2.50:8123", "webhook_path": f"/api/webhook/{id}", "call_parameter": "event=call", ...existing}`.
  - `test_options_without_url`: no internal URL configured (patch `get_url` to raise `NoURLAvailableError`), so the form still shows with the fallback values.
- [ ] **Step 2:** Run `uv run pytest tests/test_options_flow.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Description text appended in en (pl to match):

  > **Call button:** in the intercom's web interface, open Network settings → HTTP event, enable it, and set Protocol **{ha_protocol}**, Host name **{ha_host}**, Request **{webhook_path}**, Method **GET**, authentication off. Then in Recording → Call button, tick HTTP event and enter **{call_parameter}**. Keep the request path private.

- [ ] **Step 4:** Run the full check command. Expected: clean, all PASS.
- [ ] **Step 5: Commit** `Show HTTP event settings for the call button in Configure`.

### Task 7: Documentation and device verification

**Files:**
- Modify: `README.md` (Status line; new "Call button" and "Motion and alarm inputs" sections under Entities, including a doorbell automation example triggered by `event.intercom_call_button` state change)
- Modify: `AGENTS.md` (Observed device behaviour: Task 0 results; HTTP event results from Step 2)

- [ ] **Step 1:** Write the README sections and say clearly what is still unverified.
- [ ] **Step 2 (user, real device):** Install, follow the Configure instructions, press the call button. Then confirm: `event.intercom_call_button` fires `ring`; which method and URL actually arrive (temporarily set `logger: custom_components.safe_intercom: debug`); whether the device sends `HTTP/1.0` requests aiohttp accepts. Also wave at the camera and check `binary_sensor.intercom_motion`.
- [ ] **Step 3:** Record the verified facts in AGENTS.md "Observed device behaviour", run the full check command, then **commit** `Document call button, motion and alarm inputs`.
