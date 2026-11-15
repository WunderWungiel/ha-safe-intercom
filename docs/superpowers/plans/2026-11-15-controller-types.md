# Controller Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support controller types `1`, `3` and `3n` with a fixed relay count each, and accept fractional relay delays.

**Architecture:** A type → relay-count table in `api.py` drives a `ControllerInfo.supported_relays` property; the config/options flows and the switch platform use it instead of comparing against `"3n"`. `RelayInfo.delay` becomes a float.

**Tech Stack:** Python 3.13, Home Assistant custom integration, pytest-homeassistant-custom-component, ruff.

**Spec:** `docs/superpowers/specs/2026-11-15-controller-types-design.md`

## Global Constraints

- `api.py` keeps no Home Assistant imports.
- Mapping exactly: `{"1": 1, "3": 3, "3n": 3}`; `Type` compared verbatim.
- Unknown types keep aborting with `unsupported_controller` naming the type.
- Never send `set` requests to a real device; tests mock HTTP.
- Translations: `strings.json`, `translations/en.json` (mirror) and `translations/pl.json`.

## Review Focus

- `Type=1` response that still lists `Delay2/3`, `Status2/3` (spec example): only relay 1 may appear anywhere (form, switches).
- Fractional delay (`Delay2=0.5`): parses, shown as `0.5`, momentary refresh still scheduled.
- Integer delay shown as `2`, not `2.0`.
- Unknown type (`9`): config flow creates entry with no relays, options flow aborts, no switches.
- Existing `3n` setup unchanged (unique IDs, three switches).

---

### Task 1: Parse fractional delays and know relay counts per type

**Files:**
- Modify: `custom_components/safe_intercom/api.py` (`CONTROLLER_TYPE_3N`, `RelayInfo`, `ControllerInfo`, `_parse_controller`)
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `CONTROLLER_RELAYS: Final[dict[str, int]]`; `RelayInfo.delay: float`; `ControllerInfo.supported_relays -> tuple[RelayInfo, ...]` (relays with `number <= CONTROLLER_RELAYS[type]`, `()` for unknown types). `CONTROLLER_TYPE_3N` is removed.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize(("controller_type", "numbers"),
    [("1", (1,)), ("3", (1, 2, 3)), ("3n", (1, 2, 3)), ("9", ()), ("3N", ())])
async def test_supported_relays(aioclient_mock, client, controller_type, numbers):
    aioclient_mock.get(CONTROLLER_URL, text=controller_response(controller_type=controller_type))
    info = await client.async_get_controller()
    assert len(info.relays) == 3  # raw keys kept for diagnostics
    assert tuple(r.number for r in info.supported_relays) == numbers

async def test_get_controller_fractional_delay(aioclient_mock, client):
    aioclient_mock.get(CONTROLLER_URL, text="Type=3n\r\nDelay1=0.5\r\nStatus1=0\r\n")
    assert (await client.async_get_controller()).relay(1).delay == 0.5
```

Add `"Type=3n\r\nDelay1=nan\r\nStatus1=0\r\n"` and `"...Delay1=-1..."` to `test_get_controller_invalid`.

- [ ] **Step 2: Run** `uv run pytest tests/test_api.py -v` — new tests FAIL.
- [ ] **Step 3: Implement.** `float()` the delay; reject non-finite or negative values with `SafeIntercomConnectionError`.
- [ ] **Step 4: Run** `uv run pytest tests/test_api.py -v` — PASS.
- [ ] **Step 5: Commit** `Parse fractional relay delays; add relay counts per controller type`.

### Task 2: Use supported relays in flows and switches

**Files:**
- Modify: `custom_components/safe_intercom/config_flow.py` (`_is_supported`, `_controller_schema`, `_controller_placeholders`), `custom_components/safe_intercom/switch.py:66`, `strings.json`, `translations/en.json`, `translations/pl.json`, `AGENTS.md`
- Test: `tests/test_config_flow.py`, `tests/test_options_flow.py`, `tests/test_switch.py`

**Interfaces:**
- Consumes: `ControllerInfo.supported_relays`, `RelayInfo.delay: float`.
- Produces: placeholder `relay_delays` replacing `delay_1..3`, e.g. `"1: 2 s, 2: 2 s, 3: 0 s"` (delay formatted with `:g`).

- [ ] **Step 1: Update/write failing tests**
  - Existing "unsupported" tests switch `controller_type="1"` → `"9"`.
  - `test_config_flow.py`: placeholders `== {"controller_type": "3n", "relay_delays": "1: 2 s, 2: 2 s, 3: 0 s"}`; new `test_user_flow_one_channel_controller`: type `1` shows the `controller` step whose schema has only `relay_1`, placeholder `relay_delays == "1: 2 s"`; submitting `{"relay_1": True}` gives `options == {CONF_ENABLED_RELAYS: [1]}`.
  - `test_options_flow.py:90`: assert on `relay_delays` instead of `delay_3`.
  - `test_switch.py`: new `test_one_channel_controller_has_one_switch` — type `1`, entry options `[1, 2, 3]` → only `RELAY_1` exists.
- [ ] **Step 2: Run** `uv run pytest -v` — new/changed tests FAIL.
- [ ] **Step 3: Implement.** `_is_supported` → `bool(controller.supported_relays)`; schema and switch iterate `supported_relays`. In both description strings (config `controller` step, options `init` step) replace the three "Relay N: delay {delay_N} s" lines with one line `Relay delays: {relay_delays}` (PL: `Opóźnienia przekaźników: {relay_delays}`).
- [ ] **Step 4: AGENTS.md.** Replace the `Type=3n` note with the type table from the spec (`3n` = NC311P verified; `1`/`3` from spec and manuals, unverified; delays may be fractional, 0.1–3600 s per Beward manual); drop "the only controller type supported so far" wording in `const.py:19`.
- [ ] **Step 5: Run** `uv run pytest && uv run ruff check . && uv run ruff format --check .` — all PASS.
- [ ] **Step 6: Commit** `Support 1-channel and wired 3-channel controllers`.
