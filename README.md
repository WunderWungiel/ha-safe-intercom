# SAFE Intercom for Home Assistant

Custom integration for SAFE IP intercoms (S03M / S06 / S06M).

> **Status:** early development. Controller relays work; camera, call button and push
> updates are not implemented yet.

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

## Entities

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
