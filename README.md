# SAFE Intercom for Home Assistant

Custom integration for SAFE IP intercoms (S03M / S06 / S06M / S06A).

> **Status:** early development. Controller relays work. Changes made elsewhere (for example
> in the SAFE app) show up instantly through the device's event stream; the state is also
> re-read every 30 s as a safety net. The camera works (main and alternative streams); the
> call button is not implemented yet.

## Installation (HACS)

1. HACS → Integrations → ⋮ → Custom repositories → add this repository (category: Integration).
2. Install **SAFE Intercom**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **SAFE Intercom**.

## Configuration

1. Enter the intercom's IP address, HTTP port and credentials.
2. On the **Controller relays** step, choose which relays to add. The step shows the controller
   type and each relay's delay as set in the intercom's web interface.

You can change the relay selection later with **Configure** on the integration's card.
Only the 3-relay controller (`3n`) is supported so far.

- **Reauthentication:** if the intercom's password changes, Home Assistant asks for the new
  credentials.
- **Reconfigure:** to change the IP address, port or credentials, use **Reconfigure** in the
  integration's menu. It only accepts the same intercom (checked by MAC address).
- **Diagnostics:** download them from the device page; credentials, host and MAC are redacted.

## Entities

### Cameras

- **Main stream** (`rtsp://<host>:<port>/av0_0`): enabled by default.
- **Alternative stream** (`av0_1`, lower resolution): disabled by default. Enable it on the
  device page if you want a lighter feed for dashboards.

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
the switches and build on them, for example a template lock for a wicket strike on relay 1:

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
