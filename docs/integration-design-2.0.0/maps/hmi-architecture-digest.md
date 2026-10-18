# Original HMI v1.1 — logic and architecture digest

For the engineer who rebuilds this program around different hardware: the PLC is gone, liquid handling is now the Arduino Mega system (V7), the RPi side is the EC program.

Source: `C:/Users/PlasmaLab/Desktop/UI/UI/hmi_v1_1` (called HMI). References are `file:line` in that folder. Chinese texts are verbatim.

How this was made. Five reader reports were cross-checked against the source on 2026-10-06 (`hmi-server-auth.md`, `hmi-controller.md`, `hmi-analysis.md`, `hmi-ui-structure.md`, `hmi-ui-style.md`; each now ends with a `## Cross-check` section listing what was verified and corrected). Read completely for the cross-check: `server.py`, `auth.py`, `controller.py`, `plc.py`, `scheduler.py`, `csv_workflow.py`, `backend.py`, `calibration.py`, `measurements.py`, `calibration_config.py`, `spectrum_result.py`, `spectrum_store.py`, `rpi_pairing.py`, `README.md`, the launchers; `web/index.html` about 70 % (lines 1-140, 204-343, 384-503, 1042-1874, 1950-2571: all logic except the flow-diagram view-model; the CSS and the template only in part); parts of `rpi_tools/plasma_job.py` and `rpi_tools/spectrum_analysis.py`. Nothing was run. No `data` folder was opened. Everything said about the Mega system in section 9 comes from `v7-backend.md` and `v7-firmware.md`, not from my own reading of V7.

Detail lives in the five reports; this digest gives the model and the decisions that follow from it.

---

## 1. What the original was

One Python process (standard library HTTP server, bound to `127.0.0.1`, started with `--port 8766`) serving one fixed 1024 × 768 touch page in Edge kiosk mode (`server.py:428-469`, `Start-Kiosk.cmd`). No database: JSON and JSONL files in one data folder.

```
Browser (one page + 3 iframes) ──fetch JSON, polling──► server.py (HTTP, auth gate)
                                                          └─ controller.IntegratedApplication  "one owner for PLC, RPi and CSV workflows"
                                                               ├─ PLCMonitor (plc.py)     Modbus TCP, one worker thread      ← REMOVED from the rig
                                                               ├─ SSHAdapter (backend.py) paramiko → RPi: plasma_job.py, spectrum_analysis.py
                                                               ├─ flow thread             runs one Recipe (expanded CSV step list)
                                                               ├─ schedule thread         starts one flow per time slot
                                                               └─ files: recipes, runs, schedules, calibrations, measurements, audit, operators, admin.json
```

Design rules that explain most of the code (all stated in the source or README):

1. **One owner of the equipment.** A running flow reserves the pump controller; manual starts are locked; OFF always works (`plc.py:194-199,226-234`).
2. **Pumps and high voltage never run together.** Checked when a Recipe is validated (`csv_workflow.py:194-197`) and again at run time (`controller.py:602-603,696-697,863-868`).
3. **Every pump start has a software time limit** (1–120 s), enforced by the PC process only (`plc.py:337,380-396`).
4. **Never repeat a discharge automatically, never resume after a power cut** (`backend.py:154`, `controller.py:1458-1465`).
5. **Software STOP is not an emergency stop.** The page has no E-stop button; it shows `For emergency stop, please press the physical emergency stop button on the device.` with the tooltip `STOP is a software stop, not a safety level emergency stop.` (`web/index.html:985-987`; README:45).
6. **Operators need no sign-in; whatever changes how the instrument measures needs the maintenance PIN** (`server.py:276-277`).
7. **Colour means "running" or "abnormal" only**; normal is grey (`web/index.html:19-28`).
8. **Touch only:** no scroll bars (fixed rows per page), own number pad and keyboard, own confirmation dialogs that cancel themselves after 30 s (README:46,56).

---

## 2. Screens and navigation

Frame (every page): top bar 56 px · alarm banner 44 px · left navigation 116 px · page area 908 × 598 · footer 70 px.

| Zone | Content |
|---|---|
| Top bar | product name (press 3 s = hidden maintenance entry) · mode chip (`official mode` / `Simulation mode · No device connected`) · chip `PLC {state}` · chip `RPi {state}` · operator button (`operator` + name / `Please select`) · `maintenance mode` (only when logged in) · `Traditional / simple / EN` · clock |
| Banner | the worst alarm only, `confirm ACK`, `Alarm list`; else `No active alarm` |
| Footer | **three buttons only**: `Start scheduling` (becomes `continue RESUME`) · `pause PAUSE` · `Stop STOP`; then progress (`Schedule No. n / N Second-rate`, `Next measurement`, `stage`, `Not measured`) or the reason it cannot start; `⚡ contains high voltage`; the physical E-stop notice |

Navigation (`NAV`, `web/index.html:1055`), in order:

| Page | What it is for | Needs PIN |
|---|---|---|
| `Overview` | read-only: three metal cards (Cu, Zn, Ni in mg/L), phase strip, flow diagram with faceplates, status strip | no |
| `Schedule measurement` | the only way to measure: Recipe, operator, start, interval, end, blank solution; slot log | no |
| `manual operation` | pumps (direction, speed, per-pump auto-stop seconds, ON / OFF), syringe pump (channel, volume, Liquid extraction / Liquid injection / reset) | no |
| `Trend` (iframe) | concentration / intensity charts, blank band; blank thresholds and blank-zero dialog | saving the dialog: yes |
| `execution record` | one row per run and per missed slot | no |
| `Correction` (iframe) | standards table, run with bottle-swap prompts, regression results | only `application coefficient` |
| `alarm` | alarm list and `operation event` (audit rows) | no |
| `🔧 Maintenance` → `Maintenance settings` | six tabs: `Recipe` · `RPi connection` · `plasma parameters` · `correction coefficient` · `spectrum` · `operator` | yes, whole page |
| `test page` | hidden unless `ui_features.json` enables it | capture: yes |

Things a rebuild should know about the navigation:

- **There is no "measure once" button.** One measurement = a schedule with `specified times` 1. The server still has a PIN-gated direct start (`/api/recipes/run`), but no shipped page calls it.
- **High-voltage OFF (`HSPY OFF＋Nano STOP`) and manual discharge are only on `Maintenance settings › RPi connection`.** The operator's manual page has pumps and syringe only.
- **The discharge has exactly one sequence.** No page offers a choice of sequence; the only plasma controls are the seven parameters, `synchronous Nano parameter`, `capture raw spectrum`, `manual start plasma`, `HSPY OFF＋Nano STOP`, `Clear Nano fault`.
- Parameters are edited as tiles on `plasma parameters` and saved with one button `save to RPi`: `Pulse ON`, `Pulse OFF`, `every round pulse times`, `Number of rounds`, `integration time`, `Spectral interval`, `HSPY Set voltage` (1–700 V), plus the analysis setting `Spectral grouping addition (photo)`. **There is no background-frame setting**; the count is fixed in the RPi tool (section 6).
- Alarms are computed in the browser from `/api/state` (eleven conditions, three priorities Emergency / high / middle); acknowledgement is stored in that browser only. The server has no alarm object.
- The page polls: `/api/state` every 1.5 s, data older than 5 s turns every value into `? unknown` and raises `HMI Server no response...`.

Where the PLC shows in the UI (all of it has to be re-pointed): the `PLC` chip and its tooltip `Modbus TCP {host}`, the status cell `PLC Communication`, alarms `plc_comm` / `plc_cmd`, register numbers printed on pump rows and faceplates (`scratchpad 40008` …), six pump names, shared `direction CW / CCW` and `speed N rpm`, syringe channel names `CH1 Drainage · CH2 Nitric acid · CH3 Air`, the flow diagram (P1–P5 + syringe, hard-coded `PHASE_PATH`), the text `status is PLC Command register readback does not represent actual traffic.`, three hatched bars `Liquid level is not connected` / `weight is not connected`, and the panel `No device installed` (V1–V3 valves, stirrer).

---

## 3. Roles and the password model

**Two roles, one secret.**

| | Operator | Maintenance (`Maintenance`, earlier `Administrator`) |
|---|---|---|
| Identity | a name picked from a list (`POST /api/operator {name}`); **no password** | none — one shared PIN, no user names |
| Sign-in | none | PIN on a 3 × 4 keypad; `POST /api/admin/login {pin}` |
| Can do | schedule preview / start / pause / resume / stop; flow pause / resume / stop; manual pumps and syringe; choose operator; calibration plan, start, bottle-swap confirm, re-analyse, exclude a level; RPi `status` and `stop` | everything else: Recipes (publish, delete), RPi link and tools, plasma parameters, manual discharge, `prepare` / `spectrum` / `clear_fault`, coefficients and `application coefficient`, blank thresholds / zero / frame sum, operator list, raw spectra |

Exact server rule (`server.py:278-285`): admin session required for
`/api/plc-config, /api/connect, /api/disconnect, /api/refresh, /api/calibration-config, /api/parameters, /api/recipes/publish, /api/recipes/run, /api/recipes/delete, /api/start, /api/capture, /api/spectra/import, /api/rpi-tools/deploy, /api/rpi/hostkey, /api/rpi/pair, /api/operators, /api/calibration/apply, /api/blank-qc, /api/frame-sum, /api/blank-zero`, for `/api/rpi` with any action except `stop` and `status`, and for `GET /api/spectra*`, `/api/rpi-jobs`, `/api/rpi-tools`. Denial text `This operation is only for maintenance personnel (maintenance PIN) `.

**High voltage is not behind the PIN.** An operator starts it through a schedule (`confirm_hv: true` after ticking three acknowledgement rows once in the start dialog; afterwards every slot discharges automatically) or through a calibration (`confirm_high_voltage: true`). The PIN gates the *direct* discharge and everything that changes parameters.

The PIN (`auth.py`):

- 6–8 digits (`\d{6,8}`); a legacy password of 12–256 characters is still accepted. Texts `Maintenance PIN Must be 6–8 digits`, `Maintenance PIN Must be 6–8 digits (or 12 (a password containing more than ZXQ characters)`.
- **It is set only from the PC console**: `Set-Admin-Password.cmd` → `server.py --set-admin-password` → two hidden prompts (`server.py:438-447`). **No screen and no HTTP endpoint sets or changes it.** Until then the page says `has not been set for maintenance PIN` / `Please execute on the computer Set-Admin-Password.cmd settings 6–8 Digital repair PIN , restart HMI.`
- Stored in `data/admin.json`: `{"version":1,"salt":<64 hex>,"digest":<hex>,"iterations":600000,"kind":"pin"|"password"}` — PBKDF2-HMAC-SHA256, 32 random salt bytes, 600 000 iterations, written as `.tmp` then renamed. Checked with `hmac.compare_digest`.
- Lockout: 5 wrong entries → 30 s, then doubling per further lockout up to 900 s; counters are global and in memory; during a lock even the correct PIN is refused; a correct entry resets both counters. Texts `Maintenance PIN mistake`, `Too many attempts, please N Try again in seconds`.
- Session: `secrets.token_urlsafe(32)`, server memory only, 600 s sliding, sent as header `X-Admin-Session`, kept in the browser's `sessionStorage['hmi_admin_session']`. All sessions drop when the file changes or the server restarts.
- One operation asks for the PIN again inside a session: deleting a Recipe (`POST /api/recipes/delete {id, pin}`).
- Quirk found in the cross-check: the "10 minutes idle" expiry is defeated on two pages, because the shell itself sends renewing requests there (schedule preview every 30 s; spectrum list every 10 s). A rebuild that wants a real idle timeout must count user input.

Other request protection (independent of the PIN): every POST needs header `X-HMI-Token` (random per server start, from `GET /api/bootstrap`), a `Host` of `127.0.0.1:<port>` / `localhost:<port>`, an absent or same-origin `Origin`, and a JSON object body ≤ 220 KB (`server.py:248-263`).

Operators (`controller.py:74-76,272-301`): list in `operators.json` (`{"operators": [...]}`), at most 50 names, 1–20 characters, edited only by maintenance (whole list replaced). The current operator lives in memory, is `''` after a restart, is required to start a schedule (`Please select the operator first (the list is provided by the maintenance personnel in ”Maintenance Settings” › Operator maintenance)`), and is written into every run record and, as the default `actor`, into audit rows.

Audit (`audit.jsonl`): one line `{time, action, detail (≤300), actor}`; actor = operator name, `Maintenance`, `system` or `''`. Successful maintenance logins are logged; failed ones are not.

---

## 4. Workflow model

**A Recipe is a published CSV** (name 1–60 characters, stored with its SHA-256, re-verified on every load). It describes **one round**; repetition belongs to the schedule. It is validated on publish and again on every start. There are no variables, no branches, no sensor conditions.

Header, exactly: `action,target,value,unit,note`. Limits: 200 000 bytes, 500 source rows, 2000 expanded steps, nesting 5, repeat 1–20.

### 4.1 Step types (`csv_workflow.py:19-22`)

| action | target | value | unit | Executed by |
|---|---|---|---|---|
| `delay` | — | 0–3600, step 0.1 | `s` | the flow thread (paused time does not count) |
| `pump_on` | `P1`–`P6` | 1–120, step 0.1 = **maximum** run time, not the duration | `s` | PLC |
| `pump_off` | `P1`–`P6` | — | — | PLC |
| `pump_direction` | `CW` / `CCW` | — | — | PLC (shared by all pumps) |
| `pump_speed` | `CW` / `CCW` | 1–1000 integer | `rpm` | PLC (shared, one value per direction) |
| `syringe_aspirate` | `CH1`–`CH3` | 0.01–5, step 0.01 | `mL` | PLC |
| `syringe_dispense` | `CH1`–`CH3` | 0.01–5, step 0.01 | `mL` | PLC |
| `rpi_status` | — | — | — | RPi `status`; fails the run if HSPY is ON or D13 HIGH |
| `nano_prepare` | — | — | — | RPi `prepare` (push `parameter.json` to the Nano) |
| `plasma_run` | — | — | — | RPi `plasma_run`: discharge + spectra, then result read, archive, analysis |
| `spectrum_capture` | — | — | — | RPi `spectrum` (no discharge; nothing is fetched to the PC) |
| `nano_stop`, `power_off` | — | — | — | both map to RPi `stop` |
| `repeat_start` / `repeat_end` | — | count 1–20 / — | — | expanded before anything runs; only the outermost repeat counts as a displayed round |
| `operator_prompt` | — | — | — | not a CSV action: generated by a calibration run; waits for `Confirm fluid change` |

The duration of a pump run is the `delay` between its `pump_on` and `pump_off`. Dosing is therefore **time at a shared speed**; the original has no volume, flow-rate or weight concept for the pumps.

### 4.2 Preflight (before any hardware, `csv_workflow.py:170-211`)

1. `pump_direction` / `pump_speed` only while all pumps are off.
2. A `pump_on` needs an earlier `pump_direction` and a `pump_speed` for that direction in the same CSV.
3. No `pump_on` of a running pump; no `pump_off` of a stopped one.
4. Syringe steps and all RPi steps only while all pumps are off. Several pumps may run together.
5. The delays accumulated while a pump runs may not exceed that pump's maximum.
6. Every pump must be switched off before the end.
7. Total delay ≤ 7200 s.

Result: `step_count`, `delay_seconds`, `phases`, `rounds`, `uses_plasma`, `uses_rpi`.

Display phases are derived from the step targets and never change what is sent: `P1 sampling`, `P2 Sample delivery`, `P3 pure water`, `P4 Submit for inspection` (`Waste discharge` while P5 runs), `P5 Waste discharge`, `P6 standard product`, `CH2 add acid`, `CH3 air push`, RPi step `Measure`, delay after a dispense `Mix`, delay after a pump-off `standby`, start `Prepare`. The flow diagram highlights a hard-coded path per phase name.

### 4.3 The intended sequence (template `web/workflow_template.csv`, calibration templates)

`pump_speed` + `pump_direction` → pure water (`P3` on, delay, off) → Nitric acid (`syringe_aspirate CH2`, `syringe_dispense CH1`) → air push (`aspirate CH3 5 mL`, `dispense CH1 5 mL`) → Mix (delay) → Submit for inspection (`P4` on, delay, off) → `rpi_status` → `nano_prepare` → `plasma_run` → Empty (`P5` on, delay, `P4` on, delay, `P4` off, `P5` off) → standby (delay). A standard replaces the sample pump by `P6`; a blank Recipe uses another pump (README example: `P1`). The drain step runs **two pumps at once**.

### 4.4 Runner (`controller.py:840-1016`)

- Start checks: not demo; valid CSV; high-voltage confirmation when the Recipe has `plasma_run`; no other flow. When the Recipe has pump or syringe steps: pump controller read-back valid and no pump on. When it has RPi steps: RPi connected and "ready" (status ≤ 20 s old, HSPY OFF and zero output, Nano 1.2.x idle, D13 LOW; with `plasma_run` also `parameter.json` voltage = HSPY set-point, integration ≤ 10 000 ms, `loops × max(interval, (on+off) × cycles) ≤ 100 000 ms`). A pump-only Recipe with an RPi connected still requires a fresh "high voltage idle" status.
- One thread executes the expanded list top to bottom. Pump and syringe steps are synchronous calls with a 25 s limit; RPi steps are one blocking remote command (≤ 215 s).
- Flow status: `idle standby`, `running is executing`, `pausing Paused`, `paused Suspended`, `stopping Stopping`, `completed Complete`, `failed failed`, `stopped Stopped`.
- **Pause**: takes effect before the next step or inside a `delay`; running pumps are switched off and their used time is remembered; on resume each restarts with `min(120, max(1, max − used))` seconds; the `delay` continues with its remaining time. The pump controller stays reserved while paused.
- **Stop**: sets a flag checked before each step and every 0.2 s in waits; a pump/syringe command in progress finishes; a running `plasma_run` is cancelled through a **second** connection to the RPi; at the end every pump the flow still owns is switched off; if a pump-off cannot be confirmed the run becomes `failed` with `stop_errors` and the page raises the priority-1 alarm `The pump stop command is not confirmed, please use the physical stop method immediately`. A stopped run cannot be continued.
- A failed `plasma_run` is never repeated; only the *read* of a finished result is tried twice.

---

## 5. Schedule model (`scheduler.py`, `controller.py:1447-1748`)

One schedule at a time. Slot `i` is planned at `first_slot + i × interval`.

Request: `{recipe_id, operator, schedule: {interval_minutes 1–1440, start: {mode: 'now'} | {mode: 'at', time: 'HH:MM'}, end: {mode: 'count', count 1–1000} | {mode: 'until', time} | {mode: 'continuous'}}, blank: {recipe_id, every 1–100} | null, confirm_hv}`.

Rules:

| Rule | Behaviour |
|---|---|
| Start at a clock time | next occurrence of `HH:MM`; if already past, tomorrow |
| Bounded schedules | at most 31 days (`The total schedule length cannot exceed 31 days…`); otherwise use `Continue until stop is pressed` |
| Interval ≤ total delay of the Recipe | refused (`…Every transaction will time out`) |
| Estimated run longer than the interval | warning only |
| A run still busy when later slots come due | those slots are **skipped** (`skipped_overrun`, `Skip (last timeout)`), never run late; 5 s grace |
| Paused while a slot comes due (+5 s) | `skipped_paused`, `skip (scheduling pause)` |
| A slot cannot start | `not_started` with the reason; three in a row end the schedule as `failed` |
| A run fails or is stopped | the schedule ends at once ("Equipment state after a failed run is uncertain: do not continue unattended") |
| Blank solution | a separate Recipe; **the first slot that runs is a blank, then one blank after every N samples** |
| High voltage | confirmed once at start (`hv_confirmed`), then every slot discharges |
| HMI restart or power cut | the schedule becomes `interrupted` (`has been interrupted (not automatically restored)`), its running slot `failed`; nothing restarts by itself |
| While `waiting` / `running` / `stopping` | manual pump start, parameters, manual discharge, RPi tools, disconnect, pairing, calibration start are refused |
| While `paused` between slots | the equipment is free for manual work |

Schedule status: `waiting Wait for the next period`, `running under measurement`, `paused Suspended`, `stopping Stopping`, `completed Completed`, `stopped Stopped`, `failed has stopped (error)`, `interrupted`. Slot status: `running`, `done Complete`, `failed`, `stopped`, `not_started not started`, `skipped_overrun`, `skipped_paused`.

The estimate of one run = sum of delays + fixed allowances per step (syringe 12 s, pump/direction/speed 1 s, `rpi_status` 6, `nano_prepare` 10, `spectrum_capture` 40, stop 5, `plasma_run` 90 s adjusted by the pulse parameters). These allowances were measured on the PLC rig and do not describe the Mega.

---

## 6. Measurement and analysis pipeline

```
plasma_run step
  └─ RPi: plasma_job.py plasma_run  →  job folder  <output_dir>/HMI-<32 hex>/
        job.json, background/spectrum-1..3.csv, signal/spectrum-1..<Number of rounds>.csv
  └─ PC: read result (remote_result.py over the link) → check → archive  spectra/SPEC-*.json   (maintenance page spectrum)
  └─ PC: (not for calibration rounds) RPi spectrum_analysis.py analyze → intensities per metal
        → × frames_per_sum → concentration = (a × intensity + b) × c  [mg/L] → blank zero / blank check
        → one line in measurements.jsonl → metal cards, run log, trend
```

Facts that matter for the rebuild:

- **Background spectrum in the original: exactly 3 frames, taken before the discharge with HSPY OFF**, same integration time as the signal. The "3" is fixed on the RPi (`rpi_tools/plasma_job.py:395-396` default `frames=3`, `:429` `expected_frames=3`, `:513-514` the background call uses both defaults). The order is: checks → Nano config → background → ARM → signal acquisition ready → HSPY ON → ramp → START → one triggered frame per loop → OFF.
- **For the background of a discharge job, nothing on the PC side depends on the number 3.** The result check accepts 1–1000 frames per group (`spectrum_result.py:95`); the analyzer averages whatever `background/spectrum-N.csv` files exist and needs at least one (`spectrum_analysis.py:224-237`); the analysis `method_id` covers the analysis config only. A background of one frame needs no change here, but every job folder must still contain its own `background/` group while `subtract_dark` is true.
- **The separate no-discharge capture does depend on 3.** The RPi action `spectrum` (maintenance button `capture raw spectrum`, test page `capture 3 Zhang Pupu`) must return exactly three frames or the PC rejects it with `RPi raw Spectrum response is incomplete` (`controller.py:705-707`), and the download route only knows frames 1–3 (`server.py:214`). This capture is not the background of a measurement; it is a stand-alone check of the spectrometer.
- Spectrum used for analysis: mean of signal frames − mean of background frames, per pixel.
- Intensities are computed **on the RPi** by `spectrum_analysis.py` with windows from `analysis_config.json` (shift correction against the calibration's blank reference, then per metal a baseline + metric; details in `hmi-analysis.md` §2). A run is invalid when the shift cannot be fitted within limits.
- Concentration only when: a calibration is applied, the run is valid, the metal was calibrated, and the analysis `method_id` equals the one of the applied calibration; otherwise the reason is shown (`Correction has not been applied yet`, `RPi The analysis settings are different from the applied calibration, please recalibrate.`, `This analysis is invalid`, `Analysis failed`). Analysis failures never fail the run.
- Three metals everywhere: `Cu`, `Zn`, `Ni`. Unit `mg/L`.
- `frames_per_sum` (1–50) only multiplies intensities; nothing checks it against the calibration's value.

Calibration (`Correction` page, `calibration.py`):

- Plan: 2–12 mixed standards, each with Cu/Zn/Ni mg/L (blank = all 0, must be the lowest and unique), 1–10 rounds, 0–3 discard rounds, a standard Recipe (exactly one `plasma_run`) and an optional prime Recipe (pumps, syringe, delays only). At most 80 runs.
- Run: per standard `operator_prompt` (`Please replace with standard product …,P6 Put the pipette into the new bottle and press ”Confirm Liquid Change”`) → prime Recipe → discard rounds → kept rounds. It is a normal flow with `mode: 'calibration'`; the footer buttons control it.
- Analysis: blank rounds build the reference spectrum; one unweighted straight line per metal, concentration on intensity, over every kept round; R² < 0.995 is a warning only.
- `application coefficient` (PIN) writes `a`, `b`, `c = 1` and records which calibration is active.

Blank handling: a blank measurement is checked against thresholds (spectral shift, apparent concentration; a third check on normalization area never runs with the deployed analysis config) → `ok` / `fail` / `unchecked`; a failure raises a priority-3 alarm and never stops the schedule. Optional blank zero: the median of recent blanks is subtracted from sample concentrations per metal.

---

## 7. Records

All under one data folder; atomic writes (`.tmp` + rename, retried on Windows `PermissionError`).

| File | One per | Key content |
|---|---|---|
| `recipes/<32 hex>.json` | Recipe | `id, name, created_at, csv, sha256, step_count, uses_rpi, uses_plasma`; deleted ones move to `recipes/deleted/` |
| `HMI-FLOW-<32 hex>.json` | finished run (**written only at the end**) | `id, status, mode, recipe_name, started_at, finished_at, step_count, current_step, phases, rounds, events (last 120), operator, schedule_id, slot, slots_planned, sample_kind, latest_plasma_job, plasma_runs_completed, measurements, error, stop_errors`. Not stored: the CSV, the plasma parameters, the pump speed |
| `SCHEDULE-<32 hex>.json` | schedule (saved after every change) | spec, recipe id / name / hash, operator, `hv_confirmed`, `slots` (last 2000), `counts`, `blank`, status, error |
| `measurements.jsonl` | analysed `plasma_run` | `id, time, flow_id, schedule_id, slot, round, recipe_name, kind (sample / blank), job, method_id, valid, error, shift_nm, metals{intensity, conc, conc_raw, zero…}, calibration_id, conc_reason, qc, frames_per_sum, blank_zero` |
| `spectra/SPEC-<32 hex>.json` + `.meta.json` | `plasma_run` job | axis, mean difference, per-frame differences, background / signal means, frame counts, source (`flow` / `manual` / `rpi_import`) |
| `calibrations/CAL-<32 hex>.json` | calibration run | plan, recipe, runs with job folders, reference spectrum, analysis rows, fits, exclusions, applied marker |
| `calibration_config.json`, `calibration_active.json`, `calibration_plan.json` | — | coefficients in use; which calibration is applied; the draft plan |
| `blank_qc.json`, `blank_zero.json`, `frame_sum.json` | — | analysis settings |
| `operators.json`, `admin.json` | — | operator names; PIN hash |
| `audit.jsonl` | action | `time, action, detail, actor` — append-only, no rotation |
| `config.json`, `plc_config.json`, `known_hosts`, `rpi_key` | — | RPi link and PLC link settings, SSH pairing material |

Views: `GET /api/run-log` (last 200: runs from an in-memory list of 300 plus slots that did not run), `GET /api/measurements?hours=N` (from the newest 5000 lines), `GET /api/events` (last 200 audit rows). The raw frames stay on the RPi.

Per-browser state (not on the server): the schedule form, acknowledged alarms, per-pump auto-stop seconds, language.

---

## 8. Endpoints: what the UI calls and what the server offers

Full tables are in `hmi-server-auth.md` (b) and `hmi-ui-structure.md` §12. Summary of the mismatch:

- Called by the live shell: `/api/bootstrap, /api/state, /api/auth, /api/recipes[/id], /api/run-log, /api/calibration-config, /api/spectra[/id], /api/rpi-jobs, /api/rpi-tools` (GET) and `/api/admin/login|logout, /api/operator, /api/operators, /api/schedule/preview|start|pause|resume|stop, /api/workflow-pause|resume|stop, /api/plc-manual, /api/validate, /api/recipes/publish|delete, /api/rpi/hostkey|pair, /api/connect, /api/disconnect, /api/rpi, /api/rpi-tools/deploy, /api/parameters, /api/frame-sum, /api/calibration-config, /api/spectra/import` (POST).
- Called by the iframes: calibration `/api/calibration`, `/api/calibrations/<id>`, `/api/calibration/plan|check|start|confirm|analyze|exclude|apply`, `/api/workflow-stop`; trend `/api/measurements`, `/api/blank-qc`, `/api/blank-zero`.
- Offered but called by no shipped page: `GET /api/events`, `GET /api/schedule`, `GET /api/operators`, `GET /api/result/latest`, `POST /api/start`, `POST /api/stop`, `POST /api/plc`. Only the legacy `/workflow/` page: `/api/workflow-state`, `/api/recipes/run`. Only the hidden test page: `/api/plc-config`, `/api/plc-refresh`, `/api/refresh`, `/api/capture`, `/api/history`, `/api/captures/…`.
- One button does less than its label: `read status` posts `/api/rpi {action:'status'}`, whose reply the server does not store; the screen is refreshed by the server's own 5 s poll.

---

## 9. PLC actions of the original → closest equivalent in the Arduino Mega system

Left column: verified in `plc.py` / `csv_workflow.py` / `web/index.html`. Right columns: taken from `v7-backend.md` (function registry, `POST /fn/{name}` on the V7 backend) and `v7-firmware.md` (serial commands). I did not read the V7 source.

Where workflows used each action: manual page and faceplates → `POST /api/plc-manual`; Recipe steps → `PLCMonitor.flow_step`; pause/resume and the end of a run → `flow_step('pump_off' / 'pump_on')`.

| # | Original PLC action | Used by | Closest Mega equivalent | Gaps and mismatches |
|---|---|---|---|---|
| 1 | **Pump ON** — manual `{action:'pump_on', pump 1–6, seconds 1–120}`; CSV `pump_on,P1–P6,<max s>,s`. Writes 1 to register 40008–40013, verified by read-back; refused unless the pump reads OFF and direction/speed are valid | manual page, faceplate, every Recipe, resume after pause | `motors.run {motor, flow_ml_min, direction}` → serial `V,<motor>,<sps>` | **(a) No time limit anywhere in V7**: firmware runs until told to stop and has no watchdog; the per-pump auto-stop must be rebuilt on the PC and still cannot stop a pump when the PC or USB link dies. **(b) Only one motor per driver group** (2–5, 6–9): starting a second motor in the same group silently stops the first and still answers `OK`. The original runs several pumps at once (drain step: P5 then P4 together). **(c)** 6 pumps → 8 motors, numbered 2–9; which motor is which process pump is not defined anywhere (V7 labels only `2 · water`, `8 · OUT`, `9 · CuSO₄`). **(d)** Speed and direction are arguments of each call, not shared registers. **(e)** Motors 2 and 9 open a valve first: the motor starts 1 s after the command and the valve closes 1 s after the stop. **(f)** `OK` means accepted; there is no read-back that the motor turned. |
| 2 | **Pump OFF** — `{action:'pump_off', pump}`; CSV `pump_off,Pn`. Always allowed; verified by read-back; failure → retry every 2 s and an alarm | manual page, every Recipe, pause, stop, end of run | `motors.stop {motor}` → `V,<motor>,0` | A stop for a motor that is **not the selected one of its group is a no-op that answers `OK`**. Refused by the backend during auto-clean. No confirmation of standstill other than the group state going `idle`. |
| 3 | **Several pumps in one command** — `pump_on_many` / `pump_off_many {pumps:[…]}` (accepted by the server; the live page never sends it) | old lite page only | none | Would have to be a loop, and is limited to one motor per group. |
| 4 | **Shared direction** — `{action:'pump_direction', direction:'CW'|'CCW'}`; CSV `pump_direction,CW`. Register 40027; only with all pumps off | manual page header, first lines of every Recipe | `direction: 1 | -1` on `motors.run` / `motors.dose` | No shared setting to write. Which of `1` / `-1` corresponds to `CW` is not stated. A Recipe line `pump_direction` has nothing to execute. |
| 5 | **Shared speed** — `{action:'pump_speed', direction, rpm 1–1000}`; CSV `pump_speed,CW,200,rpm`. Registers 40029 / 40030 | manual page header, first lines of every Recipe | `flow_ml_min` on each `motors.run` (0.01 … `max_flow_ml_min` 80) | Unit differs: rpm vs mL/min (V7 converts with `pulses_per_ml` 4237.0076; 1600 pulses per revolution). Arithmetic only: 80 mL/min = 5649 steps/s ≈ 212 rpm; the firmware ceiling 8000 steps/s = 300 rpm; the original allowed up to 1000 rpm and its templates use 200 rpm. Whether the PLC pumps and the Mega pumps deliver the same volume per revolution is unknown, so old Recipe times cannot be converted by calculation. |
| 6 | **Syringe aspirate** — `{action:'aspirate', channel 1–3, volume_ml 0.01–5.00}`; CSV `syringe_aspirate,CH2,1,mL`. Writes the volume (40007), pulses the channel register, pulses the aspirate register (two 1 s pulses) | manual page, Recipes (Nitric acid from CH2, Air from CH3) | `pump.set_valve {position}` then `pump.aspirate {volume_ul}` → `Y,VALVE,<n>`, `Y,ASPUL,<µL>` | Two calls instead of one; unit mL → µL (whole µL). The syringe must be initialised first (`pump.init`), or the pump reports an error (stated in the V7 wiring document, per the V7 report). Which valve position is which liquid (the original: `CH1 Drainage`, `CH2 Nitric acid`, `CH3 Air`) is not defined in V7. The backend returns when the pump acknowledges, not when the stroke ends, and has no "busy?" function (the firmware has `Y,ST`, the backend does not expose it). The original had no completion feedback either. A `PUMP,…,error=<n>` reply with n ≠ 0 is not treated as an error by the V7 backend. |
| 7 | **Syringe dispense** — `{action:'dispense', …}`; CSV `syringe_dispense,CH1,1,mL` | manual page, Recipes (into the mixing tank through CH1) | `pump.set_valve` then `pump.dispense {volume_ul}` → `Y,DISPUL,<µL>` | Same as row 6. The backend keeps no plunger position. |
| 8 | **Syringe reset** — `{action:'syringe_reset'}` (pulse on 40001). Manual only; not a Recipe step | manual page | `pump.init` (`Y,INIT`, flagged dangerous) or `pump.stop` | Whether the PLC "reset" was a homing move like `pump.init` cannot be seen in the HMI code. V7 adds `pump.set_speed` and `pump.set_syringe`, which have no original counterpart. |
| 9 | **Auto-off timer** — the HMI process switches a pump off when its deadline passes (default 30 s manual, CSV value in a Recipe) | every pump start | none | Must be re-implemented by the new program. |
| 10 | **Ownership** — `reserve_flow` / `release_flow`: a running flow owns the PLC; manual starts refused, OFF allowed | start and end of every run | none (V7 only refuses motor commands during its own auto-clean and weight dose) | Must be re-implemented. The V7 backend has no authentication (CORS `*`), so anything that reaches port 8000 can command pumps beside the new program unless that port is closed or the backend is embedded. |
| 11 | **State read-back** — FC03 of 30 registers every 5 s; "valid" for `max(15, 3 × poll)` s; per pump `raw` 0/1; `pending`, `last_command`, `auto_off_seconds` | top-bar chip, flow diagram, manual page, start checks, interlocks | `GET /api/state` / `GET /api/stream` (events): `connected`, `motors.A|B.{selected, state idle/wait/run/move}`, `valves`, `pump.online`, `scale`, `cleaning` | State is per **group**, not per pump: "which pump is on" = the group's `selected` when its state is not `idle`. No reconnect by itself after a USB drop (`POST /api/reconnect`). A continuous run on motors 3–8 produces no state line at start. Firmware errors arrive as HTTP 503 with the raw line. |
| 12 | **Link settings** — `{host, port, unit_id, poll_seconds}` via `/api/plc-config` (PIN), `/api/plc-refresh` | hidden test page | serial port in `hardware.yaml` (`link.port: auto`, 115200 baud) | Auto-detection looks for "ch340" and otherwise takes the first COM port; the Nano of the plasma side is also a CH340 device (the EC notes say its USB cable is probably on the RPi, so the clash arises only when a Nano is plugged into the PC). Opening the port resets the Mega, so pumps stop and valves close (the V7 report marks this as inferred from standard Mega behaviour, not read in code). |
| 13 | **Valves V1–V3** (registers 40016 / 40017 / 40033) — drawn in the first design, never wired, removed from the screen (`No device installed`) | nowhere | 2 solenoid valves that follow motors 2 and 9 automatically | No manual valve command exists in V7 by design. |
| 14 | **Stirrer** (40028) — never wired; the Mix step is a delay | nowhere | none | — |
| 15 | **Syringe position / done feedback** (40042–40044) — never provided (`No position readback`) | nowhere | pump status byte (`ready`, `error`) in the `PUMP,…` reply of each command | Only seen as text lines in a function result. |
| 16 | **Level / weight** — placeholders `Liquid level is not connected` / `weight is not connected` on the flow diagram | nowhere | `scale.*` and the 10 Hz weight stream | New capability. After `motors.stop_all` the stream is off until `scale.restart`. |
| 17 | **Stop all pumps** — did not exist as one action (per-pump OFF, auto-off, end-of-run sweep) | — | `motors.stop_all` → `X` | Also stops the syringe (unconfirmed) and the weight stream; waits behind any command still in progress. It is a software stop, not an emergency stop. |

Mega functions without any original counterpart: `motors.dose` (fixed volume at 40 mL/min, returns before it finishes), `motors.dose_by_weight` (blocks until the target weight is reached), `motors.clean` (firmware-timed 120 s rinse with motors 2 and 8), `motors.select`, `scale.tare / calibrate / …`.

What cannot be answered from code and decides the mapping: the motor number for each of sampling / Sample delivery / pure water / Submit for inspection / Waste discharge / standard product / Blank; the liquid on each syringe valve position; which two pumps must run together and whether they sit in different groups; whether dosing stays "time at a speed" or becomes volume or weight.

---

## 10. Consequences for the rebuild (facts, not a design)

1. The pump layer of the original is a narrow seam — `validate_plc_config`, `PLCMonitor.state / submit / flow_step / reserve_flow / release_flow / configure / refresh / stop`, plus `PUMP_COUNT`, `PUMP_TARGETS` used by the CSV validator (`hmi-controller.md` §4.8). Everything above it (runner, schedule, records, UI) only needs "is the state valid", "which pumps are on", "start / stop this pump", "syringe move".
2. The CSV step vocabulary carries PLC assumptions in three places: `pump_direction` / `pump_speed` as mandatory first lines, `pump_on` value = time limit, targets `P1–P6` / `CH1–CH3`. Phase names and the highlighted flow path hang on those targets.
3. Two properties of the original Recipes meet the Mega differently: "no syringe or RPi step while a pump runs" (preflight rule 4) costs nothing on the Mega; "several pumps may run together" is limited there to one motor per driver group, two in total.
4. Existing Recipes are in the HMI `data` folder (not opened). Their times belong to the PLC pumps.
5. The RPi side of the original is SSH + `plasma_job.py` actions `status, prepare, stop, spectrum, plasma_run, clear_fault, set_parameters, cancel` and `spectrum_analysis.py config | reference | analyze`. The concentration analysis runs on the RPi from files in the job folder. Whether the EC RPi has `spectrum_analysis.py` is not something the HMI code answers.
6. The original port (8766) is the port the EC program uses today.

---

## 11. Open questions

Questions the code cannot answer. The first three block design.

1. **What "operation password" should be.** The original has no operator password. It has one maintenance PIN that gates settings, and that PIN can only be set from the PC console (`Set-Admin-Password.cmd`), not from the screen. "and original UI can also be set UI operation password" could mean (a) the same maintenance gate, (b) a password before anyone may operate at all, or (c) setting or changing the PIN on the screen — which the original never offered.
2. **Pump and syringe mapping.** Which Mega motor (2–9) is sampling / Sample delivery / pure water / Submit for inspection / Waste discharge / standard product / Blank, and which syringe valve position is Drainage / Nitric acid / Air. V7 names only `motor 2 · water`, `motor 8 · OUT`, `motor 9 · CuSO₄`. The two pumps that must run together in the drain step have to be in different driver groups.
3. **What "one background frame" covers.** One frame per discharge (each job folder keeps its own `background/spectrum-1.csv`), or one per session / schedule shared by many discharges (the analyzer has no such notion)? Before the discharge with the supply OFF, as in the original? And does the stand-alone no-discharge capture (3 frames today, enforced on the PC side) change too?
4. Which "original UI" is meant: the shipped `web/index.html` (grey ISA-101 look) or the first V1 design, of which only two fragments are in the folder.
5. Dosing: stay "run a pump for N seconds" as in the original Recipes, or use the Mega's volume or weight dosing? Old Recipe times were tuned for the PLC pumps.
6. The pump time limit (1–120 s, enforced by the PC) has no counterpart in V7. Keep it?
7. Should operators without the PIN still be able to start high-voltage schedules and calibrations, as in the original? Should high-voltage OFF be reachable without the PIN (in the original the button exists only on the maintenance page)?
8. Does the merged program keep the concentration pipeline (calibration, RPi-side `spectrum_analysis.py`, blank checks, trend)? It needs the analysis tool and its config on the RPi; whether the EC RPi has them is not visible in the HMI code.
9. After the background change, is a new calibration planned? No code forces one (`method_id` ignores acquisition settings), and any coefficients made by this HMI came from jobs with 3-frame backgrounds.
10. Are Recipes, operators, calibration records and the PIN in the HMI `data` folder to be carried over? (Not opened.)
11. Is there a physical emergency stop that also cuts the pumps' 24 V? The original's footer notice assumes one; the V7 wiring documents mention none.
12. One process or two: the original and EC both use port 8766; the V7 backend is a separate server on port 8000 without authentication.
