# Device Test Plan: Alarm Stream, Motion, Alarm Inputs, Call Button

Manual tests against a real intercom for the features added in `bfaca45..8b22cd4`. Cameras and
relays are out of scope. Two relay checks are listed as optional (T9, and the relay part of
P1.1), because the new combined stream subscription can break relay push. Both use only the
SAFE app.

**Safety.** Every command below is a read-only `get`. Do not send `alarmout_cgi`, any
`action=set`, `restart_cgi` or `factorydefault_cgi`. You change device settings (HTTP event,
motion detection, password) yourself in the intercom's web UI.

Variables used below:

```bash
IC=192.168.1.50          # intercom
HA=192.168.1.10          # Home Assistant
PC=192.168.1.20          # any LAN machine for the capture tests (can be the HA host)
U=admin; P='secret'      # intercom credentials
```

Keep a log of every result. Section P5 says where each one goes.

---

## P0: Automated checks (before going to the device)

1. `uv run ruff check . && uv run ruff format --check . && uv run pytest`. Expected: clean and
   145 passed (99 % coverage).
2. Push the 13 unpushed commits (or open a PR) so **hassfest** and the **HACS action** run on
   the new code: the `webhook` dependency, the new translations and the repair issue. CI has
   last validated `7000bb3`.

---

## P1: Raw device behaviour (no Home Assistant)

These answer the open questions in AGENTS.md and the follow-up plan's step 0. Use the helper
below to timestamp each line and make keepalives visible (`''`):

```bash
stamp() { while IFS= read -r l; do printf '%s %q\n' "$(date +%T.%3N)" "$l"; done; }
```

### P1.1 Alarm stream subscription forms

Run each for about 60 s. Meanwhile, wave in front of the camera, press the call button once,
and (optional) toggle relay 3, the gate lights, from the SAFE app.

```bash
# a) Form the integration uses: literal ';'
curl -sN --globoff -u "$U:$P" \
  "http://$IC/cgi-bin/alarmchangestate_cgi?channel=0&parameter=MotionDetection;SensorInAlarm;SensorOutAlarm" | stamp
# b) Encoded ';' (what yarl would send)
curl -sN --globoff -u "$U:$P" \
  "http://$IC/cgi-bin/alarmchangestate_cgi?channel=0&parameter=MotionDetection%3BSensorInAlarm%3BSensorOutAlarm" | stamp
# c) No filter: the spec says every alarm type
curl -sN -u "$U:$P" "http://$IC/cgi-bin/alarmchangestate_cgi?channel=0" | stamp
# d) Error behaviour: invalid parameter
curl -sN -i --globoff -u "$U:$P" \
  "http://$IC/cgi-bin/alarmchangestate_cgi?channel=0&parameter=Bogus" | stamp
```

Record:

| Question | a | b | c |
|---|---|---|---|
| HTTP status, keepalive every 5 s? | | | |
| `MotionDetection` lines arrive? Exact on/off lines | | | |
| Off line names zones? Zones 0-based? | | | |
| `SensorOutAlarm` on the SAFE app toggle (`<sensorNo>`?) | | | |
| Any line on a call-button press? (only c can show a new type) | | | |

For d: is it HTTP 200 with `Request failed…` and then a close, or a stream with no lines? This
decides finding B2 (stream counted as connected on an error body).

**Pass:** (a) delivers motion and SensorOut. If (a) fails but (b) works, switch the encoding.
If (c) shows a call-button line, the HTTP event setup is unnecessary.

### P1.2 Motion settings

```bash
curl -s -u "$U:$P" "http://$IC/cgi-bin/motion_cgi?action=get&channel=0"
```

Record `MotionSwitch`, `Time1Switch`/`Time2Switch`, and the layout (one key per line, or
several per line).

### P1.3 How many alarm inputs exist (finding I1)

```bash
curl -s -u "$U:$P" "http://$IC/cgi-bin/sensor_cgi?action=get&channel=0"
```

One input or several? If you can safely trigger an input (only if you know its wiring), watch
P1.1 (a) and note the `SensorInAlarm` sensor number. 0 is expected for the first input.

### P1.4 Raw HTTP event capture (call button)

Run this on `$PC`. It prints the raw request line, headers and body, and replies `200 OK`, so
the device does not retry the way it might against bare `nc`:

```bash
python3 -I - <<'PY'
import socket, datetime
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 8124)); s.listen(5)
while True:
    c, a = s.accept(); c.settimeout(2); data = b""
    try:
        while chunk := c.recv(4096): data += chunk
    except OSError: pass
    print(f"--- {datetime.datetime.now():%T.%f} from {a[0]}\n{data!r}\n", flush=True)
    c.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n"); c.close()
PY
```

In the intercom's web UI, open Network settings → HTTP event: Enable, Protocol HTTP, Host name
`$PC:8124`, Request `/test`, Method GET, authentication off. Then open Alarm settings → Call
button, tick HTTP event and enter `event=call`.

1. Press **Test** on the HTTP event page (if there is one). Record what it sends.
2. Press the call button once. Record:
   - The request line. Is it `GET /test?event=call HTTP/1.0`? Is the path there? Is there a
     space before `HTTP/1.0`? The manual writes `?<param>HTTP/1.0` with no space.
   - The `Host` header and any `Authorization` header.
   - How many requests one press produces, and how far apart. This decides C3, the debounce.
3. Switch Method to POST and press again. Is `event=call` in the query, the body, or both? Is
   there a trailing `\r\n`?
4. Fill the optional "additional parameter/value" fields once, and check that they come
   before `&event=call`.

**Pass:** a well-formed request line with the path and `?event=call`. If the line is
malformed, aiohttp (HA) will answer 400 and the webhook approach needs a workaround. Note this
before moving on.

---

## P2: Install and baseline in Home Assistant

1. Symlink or copy `custom_components/safe_intercom` into HA's `config/custom_components/` and
   restart HA.
2. On the integration card, choose ⋮ → **Enable debug logging**. Also enable debug for the
   webhook component with Developer tools → Actions → `logger.set_level`:
   ```yaml
   custom_components.safe_intercom: debug
   homeassistant.components.webhook: debug
   ```
3. **Migration (existing entry only):** download diagnostics. `entry.minor_version` must be
   `2` and `entry.data.webhook_id` must show `**REDACTED**`, which means it is present.
4. **Fresh entry (optional):** delete the entry and add it again. The flow runs user →
   controller → entry created.
5. Entities on the device page:
   - `event.<name>_call_button`: state `unknown`, event types `[ring]`
   - `binary_sensor.<name>_motion`: available and `off`
   - `Alarm input 1–3`: present but disabled
6. Diagnostics: no password, username, host, MAC, title or webhook ID. The `motion` block
   matches P1.2, and `video_coding` is filled in.

---

## P3: Alarm stream, motion and inputs in HA

| # | Test | Steps | Expected |
|---|---|---|---|
| T1 | Stream up | After setup, watch the log | No error. The motion sensor is available. |
| T2 | Motion on/off | Wave in front of the camera, then stand still | Motion turns `on` within about 1 s of the matching line in P1.1 (a), and `off` with the off line. |
| T3 | Several zones | Move across two zones, then leave one | Stays `on` until every zone has reported off. |
| T4 | Stream loss | Unplug the intercom's Ethernet (or cut its power) for about 90 s | Within about 20 s: INFO `Alarm stream lost`; motion and inputs `unavailable`. Switches go unavailable after the next 30 s poll fails. |
| T4b | Reconnect | Plug it back in | INFO `Alarm stream from … connected`. Motion is available and `off`. Inputs are `unknown`. The backoff in the debug log stays at 60 s or less. |
| T5 | Detection off | Turn motion detection off in the web UI, then reload the entry | A repair "Motion detection is off on …" appears. |
| T5b | Clear by disabling | Disable the Motion entity | The repair disappears. Enable the entity again and the repair comes back. |
| T5c | Clear by fixing | Turn detection on in the web UI, then reload | The repair is gone. |
| T6 | Schedule | If Time 1/2 is set, test outside the window | No motion lines; the sensor stays `off`. Diagnostics shows `scheduled: true`. |
| T7 | Inputs | Enable Alarm input 1 (and 2, 3). Trigger the input if you can | `unknown` until the first change, then follows the input. Input 1 ↔ `sensorNo 0`. Inputs that never change stay `unknown`, which is evidence for I1. |
| T8 | Reauth via stream | Change the intercom password in the web UI | A reauth flow appears. Enter the new password: the entry reloads and the stream reconnects. Then change the password back. |
| T9 | Relay push still works *(optional)* | Toggle relay 3 (lights) from the SAFE app | The HA switch follows within about 1 s, not after up to 30 s. This shows the combined subscription still carries `SensorOutAlarm`. |
| T10 | Stream rejected *(only if P1.1 d showed `Request failed`)* | Covered by unit tests; on the device, just watch the log if P1.1 (a) itself fails | One WARNING `Alarm stream refused by …`, with no connected/lost INFO flapping. Motion stays unavailable. Diagnostics: `stream.connected: false`. |
| T11 | Stream diagnostics | Download diagnostics during motion | `stream.connected: true`, `motion: true` and the active zones listed. |

---

## P4: Call button in HA

| # | Test | Steps | Expected |
|---|---|---|---|
| C1 | Suggested settings | Integration card → **Configure** | Host name is HA's **LAN IP**:8123: not `.local`, not `127.0.0.1`, not a `172.x` container address. Protocol HTTP, unless you serve HA over HTTPS on an IP. Request is `/api/webhook/<id>`. Parameter is `event=call`. |
| C2 | Webhook by curl (GET) | `curl -i "http://$HA:8123/api/webhook/<id>?event=call"` from `$PC` | `200`. The event entity state becomes the current timestamp, with `event_type: ring`. Debug: `Call button pressed on …`. |
| C3 | POST | `curl -i -X POST -d 'event=call' "http://$HA:8123/api/webhook/<id>"` | It rings. |
| C4 | Other values | `…?event=motion`, no query, `?event=call2` | `200`, no ring, debug `Ignored HTTP event …`. |
| C5 | Not local | Call the webhook through your external URL or Nabu Casa, e.g. from a phone on mobile data | No ring. HA warns `Received remote request for local webhook` (HA core logs the ID there). |
| C6 | Wrong method | `curl -X PUT …` | `405`, no ring. |
| C7 | Device setup | Enter the C1 values in the intercom's web UI (see the P1.4 path), with method GET | — |
| C8 | Real press | Press the call button 5 times, about 10 s apart | Exactly 5 rings, each under 1 s after the press. Duplicates within about 2 s mean C3 (debounce) is needed. The normal call (SIP/app) still rings. |
| C9 | Device Test button | Press Test on the HTTP event page | Note whether it rings, or is ignored as some other value. Document it. |
| C10 | POST method on device | Switch the device to POST and press | It rings. If it does not, document "use GET". |
| C11 | HA restart | Restart HA | The event state is restored (same timestamp). A test automation using the README example must **not** fire. |
| C12 | Coordinator outage | With the README automation active, unplug the intercom until the switches are `unavailable`, then plug it back in | The call button stays available, and the automation does **not** fire (B1, fixed). |
| C12b | Webhook diagnostics | After C8, download diagnostics | `http_event.requests` is the number of requests received (compare with the presses, to spot duplicates). `last` shows the method, `event: call` and `rang: true`. |
| C13 | Reload / delete | Reload the entry, then press the button (it rings). Delete the entry, then repeat C2 | After deletion: `200`, no ring, INFO `Received message for unregistered webhook`. |
| C14 | Motion HTTP event | Leave the motion page's HTTP event **without** `event=call` | Otherwise motion rings the doorbell. Add this note to the README. |

---

## P5: Record results

- AGENTS.md → "Observed device behaviour": the stream form that works (P1.1), the motion line
  format, the call-button line if (c) showed one, the number of inputs (P1.3), the raw HTTP
  event request (P1.4), duplicates, and what the Test button does.
- Then decide: I1 (inputs on first sight), C3 (debounce), B2 (connected on first line), and
  whether the README needs "use GET".
