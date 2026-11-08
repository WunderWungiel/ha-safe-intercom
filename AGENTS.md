# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project

A Home Assistant custom integration (domain `safe_intercom`) for **SAFE IP intercoms** (bramofony
IP SAFE, e.g. S03M / S06 / S06M, sold by SAFE Automation in Poland; OEM firmware for Hi3518A /
Hi3521). Meant for distribution through HACS.

Goal: one config entry per intercom, exposing:

- camera (RTSP + JPEG snapshot)
- one entity per controller relay, plus call-button / motion / sensor-input events
- updates pushed from the device's event stream, not polled

**Out of scope:** what each relay is physically wired to (gate, wicket, lights…). The device and
the official SAFE Intercom app don't know this, so the integration must not guess. Users build
covers/locks/lights on top with templates. The only hint the device gives is the relay **Delay**
(see below).

## Repository layout

```
custom_components/safe_intercom/   the integration (what HACS installs)
tests/                             pytest suite (pytest-homeassistant-custom-component)
docs/                              vendor documentation (PDF/DOCX, mostly Polish), reference only
hacs.json                          HACS metadata
pyproject.toml                     dev tooling config (ruff, pytest)
```

## Vendor documentation (`docs/`)

| File | Contents |
|---|---|
| `docs/API_Safe-IP/API, HTTP - Interface Specification (working)_20151120.pdf` | **Main reference.** HTTP CGI API (English, §20.1 in Russian). |
| `docs/API_Safe-IP/Modbus API specification_20160114.docx` | Modbus TCP registers: call button, talk state, relay/input states, day/night. |
| `docs/web_ip_safe.pdf` | Web UI manual (PL): HTTP events (§8.13), call button (§10.2), controller (§11), SIP, RTSP. |
| `docs/safe-oprogramowanie-windows.pdf` | Windows app manual (PL). |
| `docs/montaz_ip_safe.pdf`, `docs/instrukcja-montazu-…g06mp….pdf` | Installation manuals (PL): wiring, controller, factory defaults. |

The PDFs are binary. Read them with `pdftotext -layout <file> /tmp/<name>.txt`, then grep. Read
the DOCX with `unzip -p <file> word/document.xml | sed 's/<[^>]*>//g'`.

**The spec is from 2015 and does not match current firmware exactly.** Real device responses beat
the docs. Record verified deviations in the "Observed device behaviour" section below.

### Key endpoints (HTTP)

Auth: the spec puts `user=`/`pwd=` in the query string. HTTP Basic auth (`curl -u`) also works on
the real device; prefer it.

- `rtsp://<host>/av0_0` (main stream), `rtsp://<host>/av0_1` (sub stream)
- `GET /cgi-bin/images_cgi?channel=0`: JPEG snapshot
- `GET /cgi-bin/alarmout_cgi?action=set&Output=<0..7>&Status=<0|1>`: drive a relay output
- `GET /cgi-bin/controller_cgi?action=get`: controller type, relay states, per-relay `DelayN`
- `GET /cgi-bin/alarmchangestate_cgi?channel=0[&parameter=MotionDetection;SensorInAlarm;SensorOutAlarm]`:
  **long-lived push stream**. One line per change,
  `<date>;<time>;<alarmType>;<0|1>[;<areas>][;<sensorNo>]\n`, plus a bare `\n` keepalive every 5 s.
  Use this for state updates, not polling.
- `GET /cgi-bin/motiondetectionstate_cgi?channel=0`: streamed motion levels per zone
- System/info: `network_cgi`, `date_cgi`, firmware version, system info (spec §35, §37)

### Call button

The HTTP API has no call-button event in the alarm stream. The options are:

1. **HTTP event** (web UI §8.13 / §10.2): the device sends GET/POST to a configured host and path
   when the call button is pressed. The hostname field rejects `&` and `=`. This fits a Home
   Assistant webhook.
2. **Modbus TCP**: register 102 (`MODBUS_REG_PRESS_TALK_NEW`) holds the call-button state and
   register 103 the talk state. Registers 106–108 are alarm inputs, 109–111 relay outputs,
   104 motion, 105 day/night.

## Observed device behaviour (overrides the spec)

- `controller_cgi?action=get` returns `StatusN=0|1` keys for relay state (the spec says
  `OutputN=open|close`). Example: relay 3 on → `Status3=1`.
- Relay indexing: `alarmout_cgi` `Output` is **0-based**, `controller_cgi` keys are **1-based**
  (`Output=2` ↔ `Status3`).
- `Status=1` energises the relay (on/pulse), `Status=0` releases it.
- **Delay semantics:** `DelayN=0` means a latching (toggle) relay that stays until commanded
  otherwise → model as a `switch`. `DelayN>0` means a momentary relay that drops back after N
  seconds → model as a `button`.
- Known install: relay 1 (`Output=0`) = wicket strike, relay 2 (`Output=1`) = gate
  pulse, relay 3 (`Output=2`) = gate lights (latching). Use this for manual testing only; never
  hard-code it.

## Home Assistant conventions to follow

- Config flow only, no YAML configuration. Store host, username, password in `entry.data`.
- Typed config entry: `type SafeIntercomConfigEntry = ConfigEntry[SafeIntercomData]`, set
  `entry.runtime_data` in `async_setup_entry`, and use the alias everywhere.
- All I/O is async through `aiohttp` (`async_get_clientsession(hass)`). No `requests`, no blocking
  calls in the event loop.
- Keep device protocol code in `api.py` with no Home Assistant imports, so it can later move into
  a PyPI library.
- Use `has_entity_name = True`, translated entity names (`strings.json` + `translations/`),
  `DeviceInfo` keyed by device MAC/serial, and unique IDs built from that, never from the host.
- Handle unavailability properly (`ConfigEntryNotReady` on setup, `available` on entities).
  Reconnect the event stream with backoff.
- Never log credentials. Redact them in diagnostics.
- Translations: English (`translations/en.json`, mirroring `strings.json`) and Polish
  (`translations/pl.json`).

## Commands

```bash
uv venv && uv pip install -e '.[dev]'   # dev environment
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Manual test against a real device: copy or symlink `custom_components/safe_intercom` into a Home
Assistant `config/custom_components/` and restart Home Assistant.

## Safety

The relays drive real gates and locks. Never send `alarmout_cgi` / `controller_cgi?action=set`,
`restart_cgi`, `factorydefault_cgi` or any other `set` request to a real device unless the user
explicitly asks. Tests must mock HTTP.
