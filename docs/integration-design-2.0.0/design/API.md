# Plasmic Electronic control 2.0.0 — HTTP contract (API.md)

Section C of the specification. **Single source of truth** for `pc\httpd.py` / `pc\application.py` (WP5), the web code
(WP6–WP8) and the tests (`tests\test_app.py`, `tests\test_api_pin.py`, `tests\test_web_static.py`). Section letters such as
"B.4" point into `SPEC.md`. All on-screen texts are Traditional Chinese exactly as written.

Removed from 1.3.0 (answer 404): `POST /api/command`, `/api/raw`, `/api/config`, `/api/find`, `/api/diagnose`.
The state key `link` is now `rpi`.

---

## C.1 Rules for every request

| Item | Rule |
|---|---|
| Bind | `127.0.0.1:8766`, exclusive port (`SO_EXCLUSIVEADDRUSE`), daemon request threads (unchanged) |
| Host | must be `127.0.0.1:<port>` or `localhost:<port>`; else 403 `{"error": "Invalid host"}` |
| POST | `Origin` absent or same origin; header `X-Token` = the page token of `/api/bootstrap` (new at every program start, `hmac.compare_digest`); else 403 `{"error": "Invalid local request"}` (the page re-reads bootstrap once and retries) |
| Body | one JSON object; `Content-Length` ≤ 100 000 bytes; ≤ 300 000 for `/api/recipes/validate` and `/api/recipes/publish`. Anything else → 400 `need JSON object` |
| PIN | header `X-Unlock: <session>`. **Every GET is open. PIN-gated routes are POSTs only** (column "PIN" in C.4). A gated route without a valid session → 403 `{"error": "This operation is only for maintenance personnel (maintenance PIN) ", "code": "locked"}`; with no PIN file → 403 `{"error": "has not been set for maintenance PIN", "code": "pin_unset"}`. A gated POST that passes renews the session (600 s sliding); nothing else does |
| Reply | JSON, UTF-8, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, the CSP of C.2 |
| Errors | `{"error": "<text for the screen>", "code": "<code>", "reasons": [{"code", "text", "fix"}]?}` |
| Ids in paths | matched before any file name is built: Recipes and rounds `[0-9a-f]{32}`; spectra `(?:HMI|CAP|LITE)-[0-9a-f]{32}`; example files: the three fixed names |
| Events | every accepted POST that changes something writes one line to `events.jsonl` with the actor (operator name, “Maintenance” for a valid PIN session, “system” On) |

| HTTP | `code` | Meaning | Raised from |
|---|---|---|---|
| 400 | `bad_request` | wrong or missing field, value out of range, invalid Recipe on publish, missing tick | `ValueError` |
| 403 | `locked` / `pin_unset` | PIN session needed / no PIN set yet | `PermissionError` |
| 403 | — | wrong Host, Origin or token | handler |
| 404 | `not_found` | unknown route, id, file | |
| 409 | `busy` | another owner holds the equipment (B.4 "ownership") | `devices.Busy` |
| 409 | `blocked` | interlock or readiness; `reasons` lists why | `devices.Blocked` |
| 409 | `refused` (or the RPi's own code, e.g. `not_ready`) | the device said no (`ERR,…`, `PUMP error`, RPi `{"ok": false}`) | `PumpBoardError('refused' | 'device' | 'blocked')`, `PlasmicError` |
| 409 | `pin` | wrong PIN, lockout, unlock without a PIN file | `RuntimeError` from `PinLock` |
| 409 | `state` | the request does not fit the present state (nothing to pause, schedule already active) | `RuntimeError` |
| 503 | `pump_link` / `rpi_link` | link down or the device did not answer in time | `PumpBoardError('link' | 'timeout')`, connection errors |
| 500 | `internal` | anything else; generic text “Program internal error, details are in the event record”, detail to `events.jsonl` | |

**Busy rule** (used in C.4): refused with 409 `busy` while a schedule is `waiting` / `running` / `pausing` / `stopping`, while a
round is paused, or while a manual operation or another tool runs. Texts:
“Scheduled measurement is in progress: press ”Stop” first. STOP”,Or wait for it to end” · ”The process is paused: Please click ”Continue” at the bottom RESUME”or“stop STOP”” · “Another operation is in progress”.

Reason codes (in `reasons`, `blockers`, `interlock`): `no_operator`, `schedule_active`, `round_active`, `manual_active`,
`tool_active`, `pump_unset`, `pump_disabled`, `pump_link`, `pump_uncertain`, `pump_moving`, `cleaning`, `syringe_not_ready`,
`scale_offline`, `scale_uncalibrated`, `hv_on`, `hv_unknown`, `rpi_link`, `rpi_busy`, `rpi_old`, `rpi_not_ready`,
`recipe_invalid`, `interval_too_short`. Each reason is `{"code", "text", "fix"}`; `fix` may be `""`.

---

## C.2 Static files under the CSP

Header on every reply:
`Content-Security-Policy: default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'`
(`/guide` only: `style-src 'self' 'unsafe-inline'`). Fonts fall under `default-src 'self'`.

- At start `httpd` builds a table from the files under `pc\web\` whose extension is in `{.html .css .js .ttf .txt}`:
  URL path (`/` + relative path with `/`) → (file, content type). `/` → `index.html`. **A request is served only on an exact
  table hit; no file name is ever built from the request**, so `..`, `%2e`, back-slashes or absolute paths cannot reach a file.
- Types: `.html text/html` · `.css text/css` · `.js text/javascript` (needed for ES modules) · `.ttf font/ttf` · `.txt text/plain`;
  all `; charset=utf-8` except `.ttf`.
- `GET /guide` → `guide.html`, the short guide; `GET /docs/detailed-guide.html` (percent-encoded) → the complete one (AMENDMENTS M15.5).
- No inline `<script>`, no `style="…"`, no `on…=` attributes, no `eval`. Run-time styles through the CSSOM.

---

## C.3 GET routes (all open, none renews a PIN session)

| Path | Reply |
|---|---|
| `/`, `/css/…`, `/js/…`, `/fonts/…` | static file (C.2) |
| `/guide` | the guide |
| `/api/bootstrap` | C.3.1 |
| `/api/state` | C.6. Feeds the page watchdogs (`ui_alive`). Reads `X-Unlock` with `peek` only |
| `/api/recipes` | `{"recipes": [row…]}` — published Recipes, validated against the current settings |
| `/api/recipes/<id>` | the row + `warnings`, `phases`, `liquids`, `delay_seconds`, `plasma_count`, `csv`, `steps[]` |
| `/api/recipes/<id>.csv` | the published CSV, UTF-8 with BOM, `Content-Disposition: attachment; filename="recipe-<first 12 of id>.csv"` |
| `/api/recipes/examples/<file>.csv` | one of `plasma-only.csv`, `dose-measure-drain.csv`, `cleaning.csv` (UTF-8 with BOM, attachment) |
| `/api/runlog?before=<iso>&limit=<1–200>` | `{"rows": [...], "more": bool}` newest first (default limit 50) |
| `/api/rounds/<id>` | the round record (SPEC D.2) |
| `/api/runs?limit=<1–200>` | `{"runs": [meta…]}` newest first (default 200) |
| `/api/runs/<job>` | the stored spectrum set as 1.3.0 (`axis`, `background`, `signal`, `difference`, `frames`, `flow`, `background_frames`, `signal_frames`, `timing`, …) |
| `/api/runs/<job>.csv` | CSV download, same columns as 1.3.0 (`run_csv`) |
| `/api/events?before=<iso>&limit=<1–200>` | `{"events": [{"time", "level", "text", "actor"}], "more": bool}` newest first (default 50) |
| `/api/pump/ports` | C.3.2 |
| `/api/flow` | the step list of the schedule page (AMENDMENTS M23; the four stages of M16.4 before): `{steps: [[action, target, value, unit, note]…], view: [{text, ok}], valid, error, error_step \| null, warnings, estimate_seconds, uses_plasma, uses_syringe, recipe_id \| null, name, pumps: [{pump, motor, label, short, group, group_label, drain, water}], weighing, clean_seconds, dose_flow_ml_min, valve_positions, limits: {action: {min, max, decimals, unit}}, max_steps}`. Nothing saved yet: one step, `plasma_run`. A file of before M23 is read as steps |
| `/api/pump/calibration` | `{pumps: [{motor, pump, label, dose_pulses_per_ml, factory_pulses_per_ml, calibrated, nominal_ml, measured_ml, saved_at}], nominal_ml: 10}` (AMENDMENTS M16.5) |
| `/api/pump/setup` | what each pump carries, the two pumps of the cleaning and the pump of each valve, as they are in force (AMENDMENTS M20): `{editable, reason, source: "saved" \| "default" \| "board", pumps: [{motor, pump, short, label, group, half, use: "water" \| "out" \| "chemical" \| "none", name, valve}], valves: [{id, label, motor}], name_max: 16, names: {water: "water", out: "OUT"}, valve_open_before_ms, valve_close_after_ms}` |

Unknown path or id → 404 `{"error": "Not found", "code": "not_found"}`.

### C.3.1 `GET /api/bootstrap`

```json
{"csrf": "kT3…", "version": "2.0.0", "demo": false, "boot_id": "9f3c1a0e5b7d4c22",
 "pin_set": true, "practice_pin": null, "ui_watchdog_s": 10,
 "config": {"rpi_host": "192.168.1.1", "rpi_port": 8767, "token_set": true},
 "hardware": {
   "groups": [{"id": "A", "label": "A Group", "motors": [{"id": 2, "label": "Pump 1 · water", "fluid": "water", "use": "water", "valve": null},
                                                      {"id": 3, "label": "Pump 2", "fluid": null, "use": "none", "valve": null},
                                                      {"id": 4, "label": "Pump 3", "fluid": null, "use": "none", "valve": null},
                                                      {"id": 5, "label": "Pump 4", "fluid": null, "use": "none", "valve": null}]},
              {"id": "B", "label": "B Group", "motors": [{"id": 6, "label": "Pump 5", "fluid": null, "use": "none", "valve": null},
                                                      {"id": 7, "label": "Pump 6", "fluid": null, "use": "none", "valve": null},
                                                      {"id": 8, "label": "Pump 7 · OUT", "fluid": "OUT", "use": "out", "valve": null},
                                                      {"id": 9, "label": "Pump 8 · CuSO₄", "fluid": "CuSO₄", "use": "chemical", "valve": null}]}],
   "valves": [{"id": 1, "label": "solenoid valve 1", "motor": null}, {"id": 2, "label": "solenoid valve 2", "motor": null}],
   "valve_open_before_ms": 1000, "valve_close_after_ms": 1000,
   "max_flow_ml_min": 80, "default_flow_ml_min": 40, "dose_flow_ml_min": 40,
   "gravimetric": {"enabled": true, "fast_flow_ml_min": 40, "approach_flow_ml_min": 10, "trim_flow_ml_min": 3,
                   "tolerance_percent": 0.5, "timeout_s": 300},
   "cleaning": {"water_motor": 2, "out_motor": 8, "flow_ml_min": 40, "water_lead_s": 30, "overlap_s": 45, "out_tail_s": 45, "total_s": 120},
   "syringe": {"options_ul": [5000, 1250], "valve_positions": 3},
   "scale": {"decimals": 3, "calibration_presets_g": [10, 50, 100, 200], "default_calibration_g": 100},
   "rev": 1},
 "limits": {"run_max_s": 300, "flow_min_ml_min": 0.1, "dose_min": 0.01, "dose_max": 1000,
            "calibration_min_g": 0.1, "calibration_max_g": 20000},
 "recipe": {"limits": {"bytes": 200000, "rows": 500, "steps": 2000, "nesting": 5, "repeat": 20, "delay_total_s": 7200},
            "examples": [{"file": "plasma-only.csv", "name": "Only discharge measurement"},
                         {"file": "dose-measure-drain.csv", "name": "Example: Add liquid＋Measure＋discharge"},
                         {"file": "cleaning.csv", "name": "Example: automatic cleaning"}]},
 "labels": {"schedule": {"waiting": "Waiting for the next time", "running": "under measurement", "pausing": "Paused", "paused": "Suspended", "stopping": "Stopping",
                         "completed": "Completed", "stopped": "Stopped", "failed": "stopped due to failure", "interrupted": "Interrupted"},
            "slot": {"running": "under measurement", "done": "Complete", "failed": "failed", "stopped": "Stopped", "not_started": "not started",
                     "skipped_overrun": "Skip (last timeout)", "skipped_paused": "skip (scheduling pause)"},
            "round": {"running": "is executing", "pausing": "Paused", "paused": "Suspended", "stopping": "Stopping", "completed": "Complete",
                      "failed": "failed", "stopped": "Stopped", "interrupted": "Interrupted"},
            "step": {"pending": "—", "running": "is executing", "done": "Complete", "failed": "failed", "stopped": "Stopped", "skipped": "not executed"}}}
```

`hardware` is the vendored `hardware.yaml` with the pump setup of this program on it (AMENDMENTS M20: `fluid`, `label`,
`use` and `valve` of every pump, the two pumps of `cleaning`, the `valves`; the example is the setup before anybody saved
one: V7's names, no valve in use). Labels are never copied into JavaScript. `rev` grows whenever the view changes;
`state.pump.hardware_rev` is the same number, and a page that holds another one reads bootstrap again. `hardware` is
`null` only when `pc\vendor` is incomplete (then the board is `off` with that error and pump Recipes do not validate). `practice_pin` is `"000000"` in
practice mode, else `null`. `boot_id` is random per program start (the page reloads itself when it changes).

### C.3.2 `GET /api/pump/ports`

```json
{"simulated": false,
 "current": {"enabled": true, "device": "COM3", "description": "Arduino Mega 2560 (COM3)"},
 "ports": [{"device": "COM3", "description": "Arduino Mega 2560 (COM3)", "vid": "2341", "pid": "0042", "serial_number": "5573932393735180F0A1", "chosen": true}],
 "other_count": 0}
```

Only ports with an Arduino Mega USB id are listed; `other_count` = other serial devices on the PC (never listed, never
opened). Listing opens nothing. Practice mode: `{"simulated": true, "current": {…}, "ports": [], "other_count": 0}`.

### C.3.3 Other GET examples

`GET /api/recipes`

```json
{"recipes": [{"id": "0d6c3d8e2b6a4b0f9a51c8f6a2e4b7d1", "name": "Only discharge measurement", "created_at": "2026-10-06T06:40:00+00:00", "source": "seed",
              "step_count": 1, "rounds": 1, "estimate_seconds": 21, "uses_pump": false, "uses_rpi": true, "uses_plasma": true,
              "valid": true, "error": ""}]}
```

`GET /api/recipes/0d6c…` adds:

```json
{"csv": "action,target,value,unit,note\nplasma_run,,,,discharge＋capture spectrum\n", "warnings": [], "delay_seconds": 0, "plasma_count": 1,
 "phases": [{"name": "Measure", "from": 1, "to": 1}], "liquids": [],
 "steps": [{"n": 1, "line": 2, "action": "plasma_run", "name": "discharge＋capture spectrum", "target": "", "target_label": "", "value": "", "unit": "",
            "note": "discharge＋capture spectrum", "round": 1, "rounds": 1, "phase": 0, "text": "discharge＋capture spectrum", "planned_s": null}]}
```

`GET /api/runlog?limit=3`

```json
{"rows": [{"kind": "round", "time": "2026-10-06T14:20:00+08:00", "operator": "Wang Xiaoming", "recipe_name": "Only discharge measurement",
           "schedule_id": "4b1f…", "slot": 2, "slots_planned": 6, "status": "completed", "finished_at": "2026-10-06T14:20:22+08:00",
           "round_id": "7a90…", "spectra": ["HMI-5f2c…"], "result_text": "highest peak 337.1 nm · signal 8 open", "note": ""},
          {"kind": "slot", "time": "2026-10-06T14:10:00+08:00", "operator": "Wang Xiaoming", "recipe_name": "Only discharge measurement",
           "schedule_id": "4b1f…", "slot": 1, "slots_planned": 6, "status": "not_started", "finished_at": null,
           "round_id": null, "spectra": [], "result_text": "Not measured", "note": "The pre-discharge inspection failed:HSPY power supply is ON"},
          {"kind": "legacy", "time": "2026-10-05T16:02:11+00:00", "operator": "", "recipe_name": "Discharge measurement (1.3.0) ",
           "schedule_id": null, "slot": null, "slots_planned": null, "status": "completed", "finished_at": null,
           "round_id": null, "spectra": ["HMI-ca33…"], "result_text": "highest peak 337.1 nm", "note": ""}],
 "more": true}
```

`GET /api/events?limit=2`

```json
{"events": [{"time": "2026-10-06T06:21:07+00:00", "level": "info", "text": "Process completed: only discharge measurement (No. 2/6 times)", "actor": "system"},
            {"time": "2026-10-06T06:20:00+00:00", "level": "warn", "text": "Scheduled measurement starts: only discharge measurement, every 10 minutes, first time 14:10,common 6 times,300 V (High voltage confirmed)", "actor": "Wang Xiaoming"}],
 "more": true}
```

`GET /api/runs?limit=1` → `{"runs": [{"job": "HMI-5f2c…", "kind": "plasma", "started_at": "…", "finished_at": "…", "saved_at": "…", "voltage_v": 300, "integration_ms": 1000, "pixels": 1880, "signal_frames": 8, "background_frames": 1, "flow": "unified", "parameters": {…}, "peak_nm": 337.1, "peak_value": 5120.4, "wait_s": 3.4, "round_id": "7a90…", "recipe_name": "Only discharge measurement", "operator": "Wang Xiaoming", "slot": 2, "source": "round"}]}`

---

## C.4 POST routes

| Path | PIN | Busy rule | Body | Reply (200) | Refusals |
|---|---|---|---|---|---|
| `/api/unlock` | — | — | `{pin}` | `{session, expires_in: 600}` | 409 `pin`: “Maintenance PIN Error” · ”Too many attempts, please N Try again in seconds” · ”Repair has not been set yet PIN” |
| `/api/lock` | — | — | `{}` | `{locked: true}` | |
| `/api/operator` | no | — | `{name}` | `{operator}` | 400 “operator is not in the list” |
| `/api/operators` | **yes** | — | `{operators: [..]}` | `{operators, current}` | 400 “The operator name must be 1–20 characters,Not to be included < > " \” · “most 50 people” |
| `/api/stop` | **never** | — | `{}` | C.4.1 | none — **always 200** |
| `/api/pause` | no | — | `{}` | `{accepted: true, status: "pausing" \| "paused"}` | 409 `state` “There is currently no schedule that can be paused” |
| `/api/resume` | no | — | `{}` | `{accepted: true, status: "running" \| "waiting"}` | 409 `state` “The schedule is not in paused state” · 409 `blocked` with reasons (“The manually started pump is still running, please stop it first.”, HV not idle, board lost) |
| `/api/schedule/preview` | no | — | C.4.2 | C.4.2 | 400 form errors (texts of `schedule.validate`) · 404 Recipe |
| `/api/schedule/start` | no | — | preview body + `confirm` | `{accepted: true, schedule}` | 400 “Please select the operator first” · ”You must first check each item in the confirmation window” · form errors · the interval rule · 409 `state` “Already has a schedule in progress” · 409 `blocked` with the reasons of SPEC A.4 |
| `/api/alarms/ack` | no | — | `{ids: [..]}` or `{all: true}` | `{acked: n}` | |
| `/api/manual` | no | ownership (C.5) | C.5 | C.5 | 409 `busy`, `blocked`, `refused` · 503 `pump_link` |
| `/api/recipes/validate` | **yes** | — | `{file_b64}` (the file's bytes) or `{csv}` (text) | C.4.3 — 200 also when `valid` is false | 400 only when the file cannot be decoded: “CSV cannot be read: please Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)” and then import. ” |
| `/api/recipes/publish` | **yes** | — | `{name, csv}` | `{id, name, sha256}` | 400 “Recipe The name must be 1–60 characters” · the preflight error · “already has the same name Recipe” |
| `/api/recipes/delete` | **yes + `pin`** | — | `{id, pin}` | `{deleted: true}` (file moved to `recipes\deleted\`) | 409 `pin` · 409 `state` “this Recipe is being used in schedule” · 404 |
| `/api/rpi/config` | **yes** | yes | `{rpi_host, rpi_port, token?, clear_token?}` (empty or missing `token` = keep the saved one) | `{saved: true}`; reconnects | 400 in practice mode “simulation mode is fixedly connected to the local simulator” · field errors (1.3.0 texts) |
| `/api/rpi/find` | **yes** | — | `{}` | `{found: [{host, name, version, …}], host, port, addresses}` (as 1.3.0 `/api/find`) | |
| `/api/rpi/check` | no | — | `{}` | `{ok, busy, rows: [{name, ok, text}], advice}` — a reading of the supply and the Nano taken now (the RPi's `status` with `fresh`; nothing is switched), in plain words (AMENDMENTS M18) | 503 `rpi_link` |
| `/api/rpi/diagnose` | **yes** | — | `{}` | `{steps: [{key, title, ok, detail, fix}]}` (as 1.3.0 `/api/diagnose`) | |
| `/api/rpi/tool` | **yes** | yes | `{cmd: "prepare" \| "clear_fault" \| "spectrum" \| "plasma_run", confirm_high_voltage?}` | the RPi reply: `prepare` `{prepared, parameters}` · `clear_fault` `{cleared, previous_fault, …}` · `spectrum` `{job, estimate_s}` (integration time = plasma parameters's) · `plasma_run` `{job, voltage_v, estimate_s, flow, watchdog_s}` (attended: tied to the page watchdog; runs inside `Devices.hv_session()`) | `plasma_run` without `confirm_high_voltage: true` → 400 “You must first check the high voltage confirmation in the confirmation window.” · 409 `blocked` (“The pump is still moving and cannot discharge.”, `rpi_old`, RPi readiness) · RPi codes (409) · 503 `rpi_link` |
| `/api/parameters` | no (AMENDMENTS M17.3: the schedule page sets them too) | yes | `{parameters: {Ontime, Offtime, Cycle, Loopnum, Integration_Time, Spectra_Interval, Voltage}}` | `{saved, parameters, synced, sync_error?}` (`set_parameters` then `prepare`, as 1.3.0) | RPi codes (409, e.g. `bad_request` with the RPi's text) · 503 `rpi_link` |
| `/api/pump/config` | **yes** | yes | `{enabled: true, device: "COM3"}` or `{enabled: false}` | `{saved: true}`; connecting starts | 400 “COM5 Not now Arduino Mega (or has been removed). Didn’t turn it on. ” · 400 practice mode “Simulation mode uses the built-in simulation control panel” |
| `/api/pump/reconnect` | **yes** | yes | `{}` | `{accepted: true}` | 400 “Pump control board has not been set yet” |
| `/api/pump/settings` | **yes** | yes | `{syringe_ul: 5000 \| 1250, syringe_speed_ml_s}` (both) | `{saved: true, settings}`; a new syringe size un-initialises the syringe | 400 “syringe capacity only supports 5 mL or 1.25 mL” · “The syringe pump speed should be at 0.001–{max} mL/s between” |
| `/api/scale/tare` | **yes** | yes | `{}` | `{calibration: {divisor, offset, reference_grams, saved_at}}` | 409 `blocked` “The pump is still running” · ”The scale has no reading” · ”The reading is still changing, wait for it to stabilize” · 409 `refused` “The scale did not respond” · 503 `pump_link` |
| `/api/scale/calibrate` | **yes** | yes | `{grams}` 0.1–20000 | the same shape | the same + 409 `refused` “The weight has not changed and cannot be corrected.” |
| `/api/flow` | no | — | `{steps}`: rows of a Recipe, at most 60, only the actions of `recipe.FLOW_ACTIONS` (AMENDMENTS M23) | what `GET /api/flow` gives now. The steps are kept also when they do not pass the check yet (`valid: false`, `error` in the words of the page); the Recipe with the fixed id then refuses to start. 400: not a step list. 409: a schedule that uses the flow is active |
| `/api/pump/calibrate` | **yes** | yes | `{motor, measured_ml, nominal_ml?: 10}` — `motor` is the pin (2–9) | `{motor, dose_pulses_per_ml, before, change_percent}`; kept in `pumpboard.json` → `pump_calibration` | 400 “The content of the pump calibration is incorrect” · 409 `refused` “The measured volume sum 10 mL Too much difference (should be in 5–20 mL ). Please confirm before entering” · ”The volume used for calibration must be within 1–100 mL between” · 409 `busy` |
| `/api/pump/calibrate/reset` | **yes** | yes | `{motor}` | `{motor, dose_pulses_per_ml}` (the value of the settings file again) | 400 “The content of the pump calibration is incorrect” · 409 `busy` |
| `/api/pump/setup` | **yes** | yes | `{pumps: [{motor, use, name}] ×8, valves: [{id, motor \| null}] ×2}` — the whole setup at once (AMENDMENTS M20); one `water`, one `out`, on different drivers | the setup as `GET /api/pump/setup`, plus `summary`, `flow: "" \| "kept" \| "open"` (the step list of the schedule page was checked again with the new layout; `open`: it does not make a round any more — AMENDMENTS M23, which also dropped the refusal “is available in ”Add Liquid” on the schedule page. Pump n…”) and `recipes_to_check: [name…]` (Recipes that use a changed pump or no longer pass, “Schedule page process” among them); kept in `pumpboard.json` → `pump_setup`; a connected board is told at once (`CM`, `VL`), and when it does not take it nothing is kept | 400 with the sentence (“There needs to be a pump connected to ”water”, and there can only be one (for cleaning)” · ””water” and ”OUT”Want one in Pump 1–4, One in Pump 5–8: …” · “Pump 2 The name of 1–16 words,...” · ”There are ”Liquid Adding” on the schedule page Pump n: …” …) · 409 `busy` · 409 `refused` “The pump is still moving” or “The firmware of this pump control board cannot change these settings (requires Plasmic p2 above) …” |

### C.4.1 `POST /api/stop`

```json
{"accepted": true, "schedule_ended": true, "round_stopping": true, "manual_cancelled": false,
 "pump": {"result": "confirmed", "error": ""},
 "rpi": {"result": "cancel_sent", "error": "", "job": "HMI-5f2c…"}}
```

`pump.result`: `confirmed` · `unconfirmed` · `pending` (no answer within 8 s; an alarm follows) · `none` (board not in use).
`rpi.result`: `confirmed` (idle: OFF read back) · `cancel_sent` (a job was running) · `unreachable` · `unconfirmed` · `pending` ·
`silent` · `none` (AMENDMENTS M13). The RPi half is waited for 8 s, or 22 s when nothing suggests that the high voltage is on
(AMENDMENTS M14).
Never refused, never needs the PIN, works when nothing is running. Unconfirmed halves raise the alarms of SPEC B.4.
Example with the RPi unreachable (still 200):

```json
{"accepted": true, "schedule_ended": false, "round_stopping": false, "manual_cancelled": true,
 "pump": {"result": "confirmed", "error": ""},
 "rpi": {"result": "unreachable", "error": "The stop command cannot be sent RPi (timed out) — Please physically turn off the high voltage immediately"}}
```

### C.4.2 `POST /api/schedule/preview` and `/api/schedule/start`

Request (`/start` adds `confirm`):

```json
{"recipe_id": "0d6c3d8e2b6a4b0f9a51c8f6a2e4b7d1",
 "schedule": {"start": {"mode": "now"}, "interval_minutes": 10, "end": {"mode": "count", "count": 6}},
 "confirm": {"high_voltage": true, "liquid": false, "hv_off_checked": false}}
```

`schedule.start`: `{"mode": "now"}` or `{"mode": "at", "time": "HH:MM"}`; `end`: `{"mode": "count", "count": 1–1000}` ·
`{"mode": "until", "time": "HH:MM"}` · `{"mode": "continuous"}` (the 1.3.0 form of `schedule.validate`).

Preview reply:

```json
{"spec": {"first_slot": "2026-10-06T14:10:00+08:00", "interval_seconds": 600, "interval_minutes": 10,
          "start": {"mode": "now", "time": null}, "end": {"mode": "count", "count": 6}},
 "planned_total": 6, "last_slot_at": "2026-10-06T15:00:00+08:00", "estimate_seconds": 21,
 "recipe": {"id": "0d6c…", "name": "Only discharge measurement", "step_count": 1, "rounds": 1,
            "uses_pump": false, "uses_rpi": true, "uses_plasma": true, "liquids": [], "warnings": []},
 "voltage_v": 300, "flow": {"name": "unified", "ok": true, "background_frames": 1, "text": "background 1 sheets, click to make approx. Stop automatically after 4 seconds discharge"},
 "warnings": [],
 "blockers": [],
 "needs": {"high_voltage": true, "liquid": false, "hv_off_checked": false}}
```

`blockers` = the start checks of SPEC A.4 evaluated now (operator **not** included; the page opens the picker first).
`warnings` = the Recipe's warnings + the estimate-versus-interval warning. `needs` tells the page which tick rows to show.
`/start` reply: `{"accepted": true, "schedule": {…the record as in state.schedule…}}`.
Refusal example (409):

```json
{"error": "Cannot start scheduling now", "code": "blocked",
 "reasons": [{"code": "rpi_not_ready", "text": "The pre-discharge inspection failed:HSPY power supply is ON", "fix": "Press ”Stop” at the bottom STOP”"}]}
```

### C.4.3 `POST /api/recipes/validate`

Request `{"file_b64": "YWN0aW9uLHRhcmdldCx2YWx1ZSx1bml0LG5vdGUK…"}`. Reply:

```json
{"valid": false, "error": "No. 6 List: Do“discharge＋Stop all pumps before capturing the spectrum.", "error_line": 6,
 "encoding": "utf-8", "csv": "action,target,value,unit,note\n…",
 "warnings": ["No. 4 column:Pump 7 · OUT reverse"],
 "step_count": 8, "rounds": 1, "delay_seconds": 65, "estimate_seconds": 139, "plasma_count": 1,
 "uses_pump": true, "uses_motors": [2, 8, 9], "uses_syringe": false, "uses_scale": true, "uses_rpi": true, "uses_plasma": true,
 "phases": [{"name": "Liquid delivery", "from": 1, "to": 4}, {"name": "Measure", "from": 5, "to": 5}, {"name": "Liquid delivery", "from": 6, "to": 8}],
 "liquids": [{"motor": 9, "label": "Pump 8 · CuSO₄", "ml": null, "g": 10.0, "approx": false},
             {"motor": 2, "label": "Pump 1 · water", "ml": 20.0, "g": null, "approx": false},
             {"motor": 8, "label": "Pump 7 · OUT", "ml": 40.0, "g": null, "approx": true}],
 "steps": [{"n": 1, "line": 2, "action": "dose_weight", "name": "Quantitative (weight)", "target": "M9", "target_label": "Pump 8 · CuSO₄",
            "value": "10", "unit": "g", "note": "…", "round": 1, "rounds": 1, "phase": 0, "text": "Pump 8 · CuSO₄ scale weight added to 10 g", "planned_s": null}]}
```

When `valid` is false the counts describe what could be parsed (they may be zero). `encoding` is `utf-8` or `cp950`.
`/api/recipes/publish` takes the `csv` text of this reply: `{"name": "Measurement after adding liquid", "csv": "action,target,…"}` →
`{"id": "c41d…", "name": "Measurement after adding liquid", "sha256": "9b0e…"}`.

### C.4.4 Other POST examples

| Request | Reply |
|---|---|
| `POST /api/unlock {"pin": "246810"}` | `{"session": "p9Xw…", "expires_in": 600}` |
| `POST /api/operator {"name": "Wang Xiaoming"}` | `{"operator": "Wang Xiaoming"}` |
| `POST /api/operators {"operators": ["Wang Xiaoming", "Li Dahua"]}` | `{"operators": ["Wang Xiaoming", "Li Dahua"], "current": "Wang Xiaoming"}` |
| `POST /api/pause {}` | `{"accepted": true, "status": "pausing"}` |
| `POST /api/resume {}` | `{"accepted": true, "status": "running"}` |
| `POST /api/alarms/ack {"ids": [17]}` | `{"acked": 1}` |
| `POST /api/recipes/delete {"id": "c41d…", "pin": "246810"}` | `{"deleted": true}` |
| `POST /api/rpi/config {"rpi_host": "192.168.1.1", "rpi_port": 8767, "token": ""}` | `{"saved": true}` |
| `POST /api/rpi/tool {"cmd": "plasma_run", "confirm_high_voltage": true}` | `{"job": "HMI-5f2c…", "voltage_v": 300, "estimate_s": 21, "flow": "unified", "watchdog_s": 5}` |
| `POST /api/rpi/tool {"cmd": "spectrum"}` | `{"job": "CAP-81aa…", "estimate_s": 11}` |
| `POST /api/parameters {"parameters": {"Ontime": 25, "Offtime": 5, "Cycle": 25, "Loopnum": 8, "Integration_Time": 1000, "Spectra_Interval": 1500, "Voltage": 300}}` | `{"saved": true, "parameters": {…}, "nano_sync_required": true, "synced": true}` |
| `POST /api/pump/config {"enabled": true, "device": "COM3"}` | `{"saved": true}` |
| `POST /api/pump/settings {"syringe_ul": 5000, "syringe_speed_ml_s": 0.4}` | `{"saved": true, "settings": {"syringe_ul": 5000, "syringe_speed_ml_s": 0.4}}` |
| `POST /api/scale/calibrate {"grams": 200}` | `{"calibration": {"divisor": 287703.03125, "offset": -1.0, "reference_grams": 200.0, "saved_at": "2026-10-06T07:02:11+00:00"}}` |
| any gated POST without a session | 403 `{"error": "This operation is only for maintenance personnel (maintenance PIN) ", "code": "locked"}` |
| `POST /api/pump/config …` during a schedule | 409 `{"error": "Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out", "code": "busy"}` |

---

## C.5 `POST /api/manual`

Every body may carry `"hv_off_checked": true` (the tick of the start dialog when the RPi is not connected).

| `op` | Fields | Reply (200) |
|---|---|---|
| `pump_on` | `motor` 2–9, `flow_ml_min` 0.1–80, `direction` 1 \| −1, `seconds` 1–300 | `{"accepted": true, "motor": 8, "until_s": 30}` |
| `pump_off` | `motor` | `{"stopped": true}` |
| `dose_volume` | `motor`, `volume_ml` 0.01–1000, `direction` 1 \| −1 | `{"accepted": true, "op_id": "a1b2…", "eta_s": 17.1}` |
| `dose_weight` | `motor`, `target_g` 0.01–1000 | `{"accepted": true, "op_id": "a1b2…"}` |
| `clean` | — | `{"accepted": true, "op_id": "a1b2…", "eta_s": 122}` |
| `syringe_init` | — | `{"accepted": true, "op_id": "a1b2…"}` |
| `syringe_move` | `kind` `aspirate` \| `dispense`, `position` 1–3, `volume_ml` 0.001–syringe size | `{"accepted": true, "op_id": "a1b2…", "eta_s": 4.5}` |

All but `pump_off` are refused by ownership (409 `busy`), by `Devices.permit_motion()` (409 `blocked` with `reasons`), by the
board (409 `refused`, e.g. “A group can only transfer one at a time (Pump 1 · water is still turning)”, “The syringe pump has not been initialized yet”, “The syringe cannot be installed (existing 4.5 mL,capacity 5 mL) ”) or
503 `pump_link`. **`pump_off` is always accepted**, for any owner (during cleaning: 409 `refused`
“A single pump cannot be stopped during automatic cleaning. To abort, click ”Stop” STOP”.”). Blocking operations answer at once; their progress is `state.pump.op` and
their end is `state.pump.last_result`. Example of a refusal:

```json
{"error": "Cannot start pump now", "code": "blocked",
 "reasons": [{"code": "hv_on", "text": "high voltage power supply is ON", "fix": "Press ”Stop” at the bottom STOP”"}]}
```

---

## C.6 `GET /api/state` — complete shape

One poll per second drives the whole screen. Lists are cut (`schedule.slots` last 60, `alarms.active` ≤ 100, operators ≤ 50);
the steps of a round are a 12-step window, the rest comes from `/api/rounds/<id>`.

```jsonc
{
  "version": "2.0.0",
  "boot_id": "9f3c1a0e5b7d4c22",       // random per program start
  "time": "2026-10-06T14:20:07+08:00", // the PC's local time; only to detect a frozen program
  "demo": false,
  "pin_set": true,
  "unlocked": false,                   // this tab's session is valid (this call does not renew it)
  "unlock_left_s": 0,
  "operator": "Wang Xiaoming",                 // current operator, "" = none
  "operators": ["Wang Xiaoming", "Li Dahua"],
  "setup": {"pin": true, "rpi": true, "board": "ok", "operators": 2, "recipes": 1, "complete": true},
                                       // board: ok | unset | off (Not used) | problem; complete = pin && rpi && board in (ok, off) && both counts > 0
  "owner": null,                       // null | {"kind": "round"|"manual"|"tool", "label": "Discharge measurement only 2/6 Second-rate", "since": "…"}
  "message": {"time": "…", "level": "info", "text": "Process completed: only discharge measurement (No. 2/6 times)"},   // newest line for the footer
  "revs": {"events": 412, "recipes": 3, "runlog": 57, "runs": 31},   // each grows when its list changes

  "rpi": {
    "connected": true, "host": "192.168.1.1", "port": 8767, "error": "",
    "server": {"server": "plasmic-tcpd", "version": "2.0.0", "protocol": 1, "sim": false, "flow": "unified", "watchdog_s": 5,
               "limits": {"max_voltage_v": 700, "max_job_s": 215}},   // the hello reply (no current limit: AMENDMENTS M19)
    "pending": "",                     // RPi command in flight
    "status": {                        // the RPi status reply as it is
      "busy": false, "job": null, "age_s": 1.2, "updated_at": "…", "error": "",
      "snapshot": {"parameter": {"valid": true, "voltage_v": 300, "on_time_ms": 25, "off_time_ms": 5, "cycles": 25, "loops": 8,
                                 "integration_ms": 1000, "spectra_interval_ms": 1500},
                   "power": {"valid": true, "run_stop_setting": 0, "voltage_v": 0, "current_a": 0, "set_voltage_v": 300, "set_current_a": 0.1},
                   "nano": {"valid": true, "firmware": "1.2.1", "state": "IDLE", "fault": "NONE", "outputs_allowed": true,
                            "output_command_high": false, "output_pin_sample": false, "active_parameters": {"…": "…"}}},
      "ready": {"plasma": true, "problems": []},
      "estimate_s": 21, "start_s": 4, "flow": "unified", "sim": false},
    "status_age_s": 0.4,               // seconds since this PC received that status
    "hv": {"known": true, "idle": true, "reasons": []},            // SPEC B.4
    "flow": {"name": "unified", "ok": true, "background_frames": 1, "text": "background 1 sheets, click to make approx. Stop automatically after 4 seconds discharge"},
    "parameters": {"values": {"Ontime": 25, "Offtime": 5, "Cycle": 25, "Loopnum": 8, "Integration_Time": 1000,
                              "Spectra_Interval": 1500, "Voltage": 300},
                   "nano_synced": true},                           // true | false | null (unknown)
    "own_job": null,                   // id of a maintenance discharge tied to this page
    "found": [], "addresses": ["192.168.1.2"]
  },

  "pump": {
    "mode": "real",                    // real | sim | off
    "link": "connected",               // unset | disabled | missing | ambiguous | connecting | connected | lost | wrong_device | port_busy | error | forbidden
                                       // unset = no board found (it is looked for by itself: nobody chooses a port, AMENDMENTS M15.4)
    "text": "Connected COM3",              // the Chinese line for it (SPEC B.3.3); sim: “Simulation”
    "device": "COM3", "description": "Arduino Mega 2560 (COM3)", "error": "", "since": "2026-10-06T14:02:11+08:00",
    "banner_seen": true,               // false: the board did not reset when the port was opened
    "firmware_ok": true,               // identity check passed (null before a connect)
    "firmware_match": true,            // false: STATE fingerprint differs from the YAML
    "firmware_tag": "p2",              // the firmware of mega/ (weight dose on the board; from p2 on it takes the pump setup); "" = V7's own firmware, or not connected
    "hardware_rev": 1,                 // the `rev` of bootstrap.hardware that goes with this state (AMENDMENTS M20)
    "motion_uncertain": false,         // true: nobody knows whether a pump is turning
    "cleaning": false, "clean_phase": "",                        // "" | valve | water | both | drain
    "groups": [{"id": "A", "label": "A Group", "selected": null, "state": "idle"},     // state: idle | wait | run | move
               {"id": "B", "label": "B Group", "selected": 8, "state": "run"}],
    "motors": [{"id": 2, "label": "Pump 1 · water", "fluid": "water", "group": "A", "valve": null,   // valve: the linked solenoid valve, null unless one was set
                "state": "off",        // off | wait (valve opening) | run | move (volume dose) | weigh (weight dose) | clean
                "flow_ml_min": null, "direction": 1,
                "left_s": null,        // seconds until the host deadline of a continuous run
                "by": null,            // null | manual | round
                "blocked": ""},        // why ON is not possible for this row now (“B group is in use”), else ""
               {"id": 8, "label": "Pump 7 · OUT", "fluid": "OUT", "group": "B", "valve": null,
                "state": "run", "flow_ml_min": 40, "direction": 1, "left_s": 41, "by": "round", "blocked": ""}],
               // …one entry per motor, in the order of hardware.groups
    "valves": [{"id": 1, "label": "solenoid valve 1", "open": false}, {"id": 2, "label": "solenoid valve 2", "open": false}],
    "syringe": {"online": true,        // true | false | null (never asked)
                "initialised": false, "busy": false, "size_ul": 5000,
                "position": null,      // head valve position last commanded
                "volume_ul": null,     // host bookkeeping; null = unknown
                "speed_ml_s": 0.4, "max_speed_ml_s": 2.5, "error": null},
    "scale": {"online": true, "grams": 12.345, "stable": true, "age_s": 0.1,
              "calibrated": true, "calibrated_at": "2026-08-26T10:16:20+00:00", "reference_g": 200.0},
    "op": null,                        // running blocking operation: {"id", "kind", "motor", "by", "started_at", "eta_s", "elapsed_s", "text"}
    "weight_dose": null,               // {"motor", "target_g", "gain_g", "elapsed_s"} — gain_g is display only
    "last_result": null,               // last finished manual operation: {"time", "ok", "text"}
    "settings": {"syringe_ul": 5000, "syringe_speed_ml_s": 0.4}
  },

  "interlock": {
    "manual": {"ok": true, "reasons": [], "need_hv_ack": false},   // may a manual pump / syringe operation start now; need_hv_ack: the RPi is not connected, the start dialog must show the tick row
    "plasma": {"ok": true, "reasons": []},                         // may a maintenance discharge start now
    "start":  {"ok": true, "reasons": []}                          // Recipe-independent blockers of Start scheduling; Recipe-specific ones come from /api/schedule/preview
  },

  "schedule": {                        // null, or the record of SPEC D.2 with only the last 60 slots
    "version": 2, "id": "4b1f…", "status": "running", "created_at": "…", "finished_at": null, "operator": "Wang Xiaoming",
    "recipe": {"id": "0d6c…", "name": "Only discharge measurement", "sha256": "…"},
    "uses_pump": false, "uses_rpi": true, "uses_plasma": true,
    "confirmed": {"high_voltage": true, "liquid": false, "hv_off_checked": false},
    "voltage_v": 300, "estimate_seconds": 21,
    "spec": {"first_slot": "…", "interval_seconds": 600, "interval_minutes": 10, "start": {"mode": "now", "time": null}, "end": {"mode": "count", "count": 6}},
    "planned_total": 6, "last_slot_at": "…", "next_index": 1, "next_slot_at": "…",
    "slots": [{"index": 0, "planned_at": "…", "status": "done", "round_id": "7a90…", "job": "HMI-5f2c…", "started_at": "…", "finished_at": "…", "note": ""},
              {"index": 1, "planned_at": "…", "status": "running", "round_id": "8c11…", "started_at": "…"}],
    "counts": {"done": 1}, "error": ""},

  "round": {                           // null when no round has run since start; else the running round or the last finished one
    "id": "8c11…", "rev": 5,           // rev rises whenever a step changes state
    "status": "running", "schedule_id": "4b1f…", "slot": 2, "slots_planned": 6, "operator": "Wang Xiaoming",
    "recipe": {"id": "0d6c…", "name": "Only discharge measurement"},
    "started_at": "…", "finished_at": null,
    "step": 1, "step_count": 1, "round": 1, "rounds": 1,
    "phase": 0, "phases": [{"name": "Measure", "from": 1, "to": 1}],
    "current": {"n": 1, "action": "plasma_run", "text": "discharge＋capture spectrum", "detail": "Discharging·Signal 3/8 open", "started_at": "…", "eta_s": 21},
    "uses_plasma": true, "uses_pump": false,
    "paused_pumps": [],                // labels of the pumps a paused round will restart
    "plasma_job": "HMI-77d0…", "error": "", "stop_report": null,
    "steps_window": {"from": 1,        // the 6-row page that holds the current step and the following page
                     "items": [{"n": 1, "text": "discharge＋capture spectrum", "phase": 0, "status": "running",
                                "started_at": "…", "finished_at": null, "result_text": "", "error": ""}]}},

  "latest_run": {"job": "HMI-5f2c…", "kind": "plasma", "saved_at": "…", "voltage_v": 300, "peak_nm": 337.1, "peak_value": 5120.4,
                 "signal_frames": 8, "background_frames": 1, "flow": "unified", "wait_s": 3.4, "round_id": "7a90…"},   // newest stored spectrum set, or null

  "alarms": {"worst": null,            // 1 | 2 | 3 | null — worst unacknowledged priority
             "unacked": 0, "counts": {"p1": 0, "p2": 0, "p3": 0},
             "active": []}             // [{"id", "key", "priority", "area", "text", "since", "acked", "cleared"}], unacknowledged first, then priority, then newest
}
```

| Field | Notes for the page |
|---|---|
| `unlocked`, `unlock_left_s` | from `PinLock.peek`; the top-bar tag “Maintenance m:ss” |
| `setup` | the four lines of “Before you start using it” on Overview; `pin` is true in practice mode; `rpi` = connected at least once since start |
| `owner` | who holds the equipment; controls of other owners are disabled with the busy texts |
| `rpi.status` | exactly what the RPi sends (`job.progress.phase`: `prepare` / `background` / `ramp` / `signal` / `off` / `stopping` / `finishing` / `capture`) |
| `rpi.hv`, `rpi.flow`, `rpi.parameters` | computed on the PC so the page compares nothing itself |
| `pump.text` | shown in the chip tooltip, the D4 strip and the connection tab |
| `pump.motors[].blocked` | the row tag when another motor of the group is busy |
| `interlock` | what the server would answer now; the server checks again when the request arrives |
| `round.steps_window` | enough for the auto-following step table; other pages of the table come from `/api/rounds/<id>` (refetch when `round.rev` changed) |
| `alarms` | server-side and persisted; `ui_stale` is added by the page itself when no fresh state arrived for 5 s |
| mode `off` (tests, incomplete `pc\vendor`) | `pump` = `{"mode": "off", "link": "disabled", "text": "Not used", …}` with empty `groups` / `motors` |

Token, PIN hash and the RPi token never appear in any reply (`config.token_set` only).

## Addendum 2026-10-07 (AMENDMENTS M22): the RPi command `sync_voltage`

| command | token | fields | reply |
|---|---|---|---|
| `sync_voltage` | yes | none | `{changed, set_voltage_v, previous_set_voltage_v}`: the supply's set voltage is made the voltage of `parameter.json` (supply OFF with nothing at its output, read back; `plasma_measure.py --set-voltage`). `busy` while a job runs; `not_ready` when the parameters hold no voltage of 1–700 V; `tool_failed` with the tool's sentence when the supply is not OFF or does not take the number. Nothing is switched on |

`set_parameters` does the same write first and then runs the verified tool. `status.ready.problems` names a set voltage that differs
only in the `verified` flow. The PC sends `sync_voltage` by itself (`rpilink.RpiLink._sync_voltage`); no HTTP route sends it.
