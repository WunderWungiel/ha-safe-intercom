# Motion and Call Button Follow-ups

Review of `feature/call-button-alarm-inputs` (commits `bfaca45..26dafe0`) against the HTTP API spec
§15, §17, §20.1, the Modbus spec and the web UI manual §8.13, §10.1 and §10.2. Lint is clean and
129 tests pass. The design holds up: the stream is only a change signal for relays, motion and
inputs are cleared when the stream drops, the webhook is local-only and never logs its ID, and
the migration is correct. What follows are the gaps found.

## Step 0: verify on the device before merging (read-only, done by the user)

The plan's Task 0 and Task 7 step 2 were never run. AGENTS.md still marks both as unverified.

1. **Combined stream subscription.** Relay push was verified only with `parameter=SensorOutAlarm`.
   If the firmware rejects `MotionDetection;SensorInAlarm;SensorOutAlarm` or ignores part of it,
   relays fall back to the 30 s poll without any warning. Run the two `curl` commands from the
   original plan's Task 0, plus a third with **no** `parameter` at all. Per spec, no parameter
   means every alarm type. Check whether the unfiltered stream carries any call-button line. If
   it does, the call button needs no HTTP event setup.
2. **Raw HTTP event request.** Before testing through HA, capture what the device sends:
   `nc -lk 8124` on the HA host, with Host name `<ha-ip>:8124`, Request `/test` and parameter
   `event=call`. Then press Test and the call button. The manual writes the request as
   `http://<host>?<param>HTTP/1.0`: no Request path and no space before `HTTP/1.0`. Confirm the
   path is sent and that the request line is well formed. Also note the GET vs POST body layout
   and whether one press produces one request or several.
3. **Motion line format.** Record one real `MotionDetection` on/off pair. Check whether the
   off line names zones and whether zones are 0-based.

Record the results in AGENTS.md under "Observed device behaviour". Steps 1–3 can change C1, M1
and I1 below.

## Code changes

### M1: motion "on" without zones can get stuck (done)

`coordinator._handle_alarm` stores an on-line with no zones as zone `0`. An off-line that names
only other zones (`…;0;1`) then leaves zone `0` set, so motion stays on until the stream
reconnects.

- Track a separate `_motion_unzoned: bool` instead of the fake zone 0. Any off-line clears it;
  `motion` is `bool(zones) or unzoned`.
- Test: `1` (no zones) then `0;1` → motion off.

### M2: motion sensor is misleading when detection is off on the device (done)

Motion lines arrive only while detection is enabled and inside its schedule (web UI §10.1:
`Włącz`, `Czas 1/2`; spec §15 `motion_cgi`: `MotionSwitch`, `Time1Switch`, `Time2Switch`). With
detection off, the sensor shows a confident `off` forever.

- `api.py`: `async_get_motion_settings()` reads `motion_cgi?action=get&channel=0` and returns
  `enabled` plus whether a schedule is active. This is a read-only `get`. It needs admin rights,
  so tolerate failure the same way as `rtsp_cgi`.
- Read it at setup into `SafeIntercomData`. Add it to diagnostics. If `MotionSwitch=close`,
  raise a repair issue ("Motion detection is turned off on the intercom"). Do not change the
  entity.
- Tests: the settings are parsed, a failure is tolerated, and the issue is created only for
  `close`.

### I1: three alarm inputs is a guess the docs do not support

`SUPPORTED_INPUTS = (1, 2, 3)` comes from generic Modbus registers 106–108. The HTTP spec's
`sensor_cgi` (§17) describes a single sensor input. The S0x installation manuals list only
ALARM OUT terminals. The result is three phantom, disabled entities that may never exist.

- Create input entities **on first sight** instead. When the coordinator sees a `SensorInAlarm`
  line with a new sensor number, it fires a dispatcher signal. `binary_sensor.async_setup_entry`
  adds the entity, and also restores those already in the entity registry for this entry. Enable
  the entities by default, since a reported input really exists.
- Drop `SUPPORTED_INPUTS`. Update the README and AGENTS.md.
- Tests: no input entities before an event, one appears after `SensorInAlarm;1;1`, it is still
  there after a reload, and a second event for the same input adds no duplicate.

If step 0 shows that the device never sends `SensorInAlarm`, drop the input entities and the
subscription to that type.

### C1: be lenient about the HTTP event value (done)

`parse_http_event` compares the raw value exactly. A POST body ending in `\r\n`, or a request
whose parameter runs into trailing text, would not match. Strip whitespace from the value. Step 0.2
decides whether anything more is needed (for example, accepting `call` as a prefix). Tests:
`"event=call\r\n"` in the body gives `call`.

### C2: suggest an address the intercom can reach (done)

`_http_event_placeholders` uses `get_url(allow_external=False)`. That returns
`hass.config.internal_url` as configured, which may be `homeassistant.local` (2015 firmware
almost certainly cannot resolve mDNS) or an HTTPS URL whose certificate the device rejects.

- Prefer an IP. If the URL host is not an IP address, use
  `homeassistant.components.network.async_get_source_ip` with the HA HTTP server port and HTTP.
  Keep the current fallback text when nothing is known.
- Add one sentence to the options description saying HTTP and an IP address work best.
- Tests: internal URL `http://homeassistant.local:8123` gives the source IP with port 8123, and
  internal URL `http://192.0.2.50:8123` stays unchanged.

### C3: debounce repeated requests (only if step 0.2 shows duplicates)

If one press sends several requests (Test plus real, retries, or GET followed by POST), ignore
rings that arrive within about 2 s of the previous one, in `event.py`. Skip this if the device
sends exactly one request.

## Not changing

- **Event entity availability** follows the coordinator. A ring that arrives while
  `controller_cgi` is failing still updates the event state; HA writes it but the entity shows
  unavailable. The device can still send to HA in that situation, so this is rare. Leave it.
- **Modbus for the call button** (register 102). It needs no device setup, but adds a dependency
  and constant polling. Keep it as the fallback only if the HTTP event proves unusable in step 0.2.
- **Initial input state.** Inputs stay unknown until they first change. `alarmstate_cgi` or
  Modbus could seed it, but that is not worth doing until I1 confirms inputs exist.

## Order

Step 0 first, then M1 and C1 (small and independent), then M2, I1 and C2. C3 only if needed.
Commands: `uv run ruff check . && uv run ruff format --check . && uv run pytest`.
