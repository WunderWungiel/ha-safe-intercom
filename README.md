<p align="center">
  <img src="https://raw.githubusercontent.com/WunderWungiel/ha-safe-intercom/main/custom_components/safe_intercom/brand/icon.png" alt="SAFE Intercom" width="96">
</p>

<h1 align="center">SAFE Intercom for Home Assistant</h1>

<p align="center">
  Local-push Home Assistant integration for <b>SAFE IP intercoms</b> (S03M, S06, S06M, S06A).<br>
  Camera, controller relays, call button, motion and alarm inputs. No cloud, no polling for changes.
</p>

<p align="center">
  <a href="https://github.com/WunderWungiel/ha-safe-intercom/releases"><img src="https://img.shields.io/github/v/release/WunderWungiel/ha-safe-intercom?include_prereleases&style=flat-square" alt="Release"></a>
  <a href="https://github.com/WunderWungiel/ha-safe-intercom/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/WunderWungiel/ha-safe-intercom/validate.yml?branch=main&label=validate&style=flat-square" alt="Validate"></a>
  <a href="https://github.com/WunderWungiel/ha-safe-intercom/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/WunderWungiel/ha-safe-intercom/tests.yml?branch=main&label=tests&style=flat-square" alt="Tests"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?style=flat-square&logo=homeassistant&logoColor=white" alt="Home Assistant 2026.7+">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS Custom"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/WunderWungiel/ha-safe-intercom?style=flat-square" alt="License"></a>
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=WunderWungiel&repository=ha-safe-intercom&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open this repository in your Home Assistant Marketplace or HACS"></a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=safe_intercom"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Add the SAFE Intercom integration to Home Assistant"></a>
</p>

> [!IMPORTANT]
> **Early development.** Relays and the camera are tested on a real S06A. Relay changes made
> elsewhere (for example in the SAFE app) show up instantly. The call button, motion and alarm
> inputs are implemented but not yet verified on a real device. Reports, good or bad, are very
> welcome in [issues](https://github.com/WunderWungiel/ha-safe-intercom/issues).

## Features

| | |
|---|---|
| 📷 **Camera** | Main and alternative RTSP streams with JPEG snapshots |
| 🔌 **Relays** | One switch per controller relay, showing its real state |
| 🔔 **Call button** | Doorbell event entity, fed by the intercom's HTTP event |
| 🏃 **Motion** | Binary sensor from the intercom's motion detection |
| 🚨 **Alarm inputs** | Binary sensors for the intercom's sensor inputs |
| ⚡ **Local push** | State changes arrive over the intercom's event stream, not polling |
| 🛠️ **UI setup** | Config flow, reauthentication, reconfigure, diagnostics, English and Polish |

## Supported devices

SAFE IP intercoms sold by SAFE Automation (Poland), built on Hi3518A / Hi3521 firmware.

| Model | Status |
|---|---|
| S06A (firmware 3.1.5.0.7.18.36) | Tested |
| S03M, S06, S06M | Expected to work (same HTTP API), untested |

| Controller (`Type` reported by the intercom) | Relays | Status |
|---|---|---|
| NC311P (`3n`) | 3 | Tested |
| Wired 3-channel, S03P(C1) (`3`) | 3 | From documentation, untested |
| 1-channel, NC103 / NC103P (`1`) | 1 | From documentation, untested |

Other controller types get no relay switches. If you have a different model or controller,
or an untested one works (or doesn't), please open an issue with the diagnostics attached.

## Installation

### Marketplace or HACS (recommended)

Click **Open your Home Assistant instance** above, or add the repository by hand:

- **Home Assistant 2026.11 and newer:** Settings → **Marketplace** → **Add from link**, and enter
  `https://github.com/WunderWungiel/ha-safe-intercom`.
- **Older versions with HACS:** HACS → ⋮ → **Custom repositories**, add
  `https://github.com/WunderWungiel/ha-safe-intercom` with type **Integration**.

Then install **SAFE Intercom** and restart Home Assistant.

### Manual

Copy `custom_components/safe_intercom` from the
[latest release](https://github.com/WunderWungiel/ha-safe-intercom/releases) into your
Home Assistant `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Add the SAFE Intercom integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=safe_intercom)

Or go to Settings → Devices & services → **Add integration** → **SAFE Intercom**.

1. Enter the intercom's IP address, HTTP port and credentials.
2. On the **Controller relays** step, choose which relays to add. The step shows the controller
   type and each relay's delay as set in the intercom's web interface.

You can change the relay selection later with **Configure** on the integration's card.
Configure also shows the settings for the call button (see [Call button](#call-button)).

- **Reauthentication:** if the intercom's password changes, Home Assistant asks for the new
  credentials.
- **Reconfigure:** to change the IP address, port or credentials, use **Reconfigure** in the
  integration's menu. It only accepts the same intercom (checked by MAC address).

## Entities

### Cameras

| Entity | Stream | Default |
|---|---|---|
| **Main stream** | `rtsp://<host>:<port>/av0_0` | Enabled |
| **Alternative stream** | `av0_1`, lower resolution | Disabled; enable it for a lighter dashboard feed |

Both show the same JPEG snapshot from the intercom. Live video needs RTSP enabled in the
intercom's web interface (Network settings → RTSP); with RTSP off the cameras show snapshots
only. The RTSP settings (on/off, port, authentication) are read when the integration loads, so
reload it after changing them. The stream must be H.264: Home Assistant cannot play MJPEG
streams.

### Relays

Each enabled relay is a **switch** (`Relay 1`, `Relay 2`, `Relay 3`) that shows the relay's real
state:

- **Delay 0 s** (latching): stays on until you turn it off.
- **Delay > 0 s** (momentary): turns on, then drops back to off by itself after the delay.

The intercom doesn't know what a relay is wired to, so the integration doesn't guess. Rename
the switches and build on them; see [Examples](#examples).

### Call button

**Call button** is a doorbell `event` entity that fires `ring` when someone presses the
intercom's call button. The intercom reports presses with an *HTTP event* sent to a Home
Assistant webhook, which you set up once in the intercom's web interface (the integration
never changes device settings itself):

1. Open **Configure** on the integration's card. The description lists the exact values.
2. In the intercom's web interface, open Network settings → **HTTP event**, enable it and enter
   the protocol, host name (`<Home Assistant IP>:<port>`) and request (`/api/webhook/…`)
   shown in Configure. Method **GET**, authentication off.
3. In Alarm settings → **Call button**, tick HTTP event and enter `event=call`.

> [!WARNING]
> The webhook only accepts requests from your local network. Keep its path private: anyone on
> your network who knows it can trigger the event.

The call button stays available when the intercom is unreachable. Reloading the integration
or restarting Home Assistant still makes it briefly unavailable, and its return to the last
`ring` would look like a new press, so exclude `unavailable` in triggers (see
[Examples](#examples)).

### Motion and alarm inputs

These come from the intercom's event stream, as relay changes do:

- **Motion**: on while any motion detection zone is active. Turn on motion detection in the
  intercom's web interface (Alarm settings → Motion detection), or it stays off. If detection
  is off, Home Assistant shows a repair warning; disable the Motion sensor if you don't need it.
- **Alarm input 1–3**: disabled by default, because nothing tells the integration what is
  wired to them. Their state is unknown until the first change after Home Assistant connects.

Both are unavailable while the event stream is disconnected, since changes during that time
can't be known.

## Examples

<details>
<summary><b>Doorbell notification</b></summary>

```yaml
automation:
  - alias: Doorbell
    triggers:
      - trigger: state
        entity_id: event.intercom_call_button
        not_from: unavailable
        not_to: unavailable
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'ring' }}"
    actions:
      - action: notify.notify
        data:
          message: Someone is at the gate
```

</details>

<details>
<summary><b>Wicket strike as a lock</b> (momentary relay 1)</summary>

```yaml
template:
  - lock:
      - name: Wicket
        state: "{{ is_state('switch.intercom_relay_1', 'off') }}"
        unlock:
          - action: switch.turn_on
            target:
              entity_id: switch.intercom_relay_1
        lock: []
```

</details>

## How it works

The integration talks to the intercom over its local HTTP API only:

- **State** comes from `controller_cgi`, re-read whenever the intercom's long-lived
  `alarmchangestate_cgi` stream reports a change, after each command, and every 30 s as a
  safety net. The stream reconnects with backoff if it drops.
- **Commands** go to `alarmout_cgi`.
- **Video** is the intercom's RTSP stream; **snapshots** come from `images_cgi`.
- **Call button presses** arrive as an HTTP request from the intercom to a local-only webhook.

## Troubleshooting

- **Diagnostics:** download them from the device page. Credentials, host and MAC are redacted.
  They show whether the event stream is connected, the current motion and input state, and how
  many requests the call button webhook has received (with the last one's method and `event`
  value), which helps when setting up the HTTP event.
- **Debug logs:** on the integration's card, choose ⋮ → **Enable debug logging**, reproduce the
  problem, then disable it to download the log.
- When opening an issue, attach diagnostics and the debug log, and say which model and firmware
  you have.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Device protocol code lives in `api.py` with no Home Assistant imports. See
[AGENTS.md](AGENTS.md) for the API notes and observed device behaviour.

## Disclaimer

This is an independent community project, not affiliated with or endorsed by SAFE Automation.
The relays drive real gates and locks: test your automations carefully.

## License

[MIT](LICENSE)
