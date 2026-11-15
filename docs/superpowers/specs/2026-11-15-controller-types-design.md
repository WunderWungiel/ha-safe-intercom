# Controller types: design

## Intent

Support every documented controller type, not only `3n`, without ever exposing a relay that may
not exist. The author can only test `3n` (NC311P), so wrong guesses must fail safe.

## Findings

| Controller | Relays | Latching (0 s delay) | `Type` value |
|---|---|---|---|
| 1-channel (NC103 / NC103P) | 1 | no | `1` (API spec §22, unverified) |
| wired 3-channel (S03P(C1)) | 3 | no | `3` (API spec §22, unverified) |
| NC311P (paired via LINK) | 3 | yes, NC311P only | `3n` (verified on S06A) |

Sources: API spec §22, SAFE web UI manual ch. 11, Beward DS06M(P) Operation User Manual ch. 11
(SAFE devices are rebranded Beward DS06). Beward documents NC311P durations of 0.1–3600 s, so
`DelayN` can be fractional.

## Approaches

1. **Fixed table (picked).** Known `Type` → relay count; unknown types stay unsupported. Safe:
   a wrong guess means no switches.
2. Trust `StatusN` keys in the response. Rejected: the spec's `Type=1` example lists three
   outputs, so a 1-channel box could get phantom switches driving `alarmout_cgi Output=1/2`.

## Design

- `api.py`: `CONTROLLER_RELAYS = {"1": 1, "3": 3, "3n": 3}`; `ControllerInfo.supported_relays`
  returns relays 1..N for a known type, empty otherwise. `relays` stays raw for diagnostics.
- Config flow, options flow and switch platform use `supported_relays` instead of
  `type == "3n"`. Unknown types keep the "not supported" message naming the reported type.
- `RelayInfo.delay` becomes `float`; `0` still means latching.
- Form description lists only existing relays via one `{relay_delays}` placeholder (EN + PL).
- AGENTS.md records the mapping and what is verified.
- Tests: each type, unknown type, fractional and invalid delays.

## Calls made by the builder

- Existing entries with type `1`/`3` were created with `enabled_relays: []` (the flow skipped
  the relay step), so they keep no switches until the user picks relays via Configure.
- `Type` is matched exactly (`3N`, `3 ` are unknown).
