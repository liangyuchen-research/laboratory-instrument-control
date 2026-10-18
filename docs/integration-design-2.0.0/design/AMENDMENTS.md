# Plasmic Electronic control 2.0.0 — amendments of 2026-10-06 (afternoon)

**This file overrides `SPEC.md`, `API.md` and `WORKPACKAGES.md` wherever they disagree.** Read it after them.
Section letters such as "B.5", "E.10", "C.4" point into those files. Everything not named here stays as written there.

## M0. Why

After the specification was written the owner answered six questions (2026-10-06, his words):

> 1. Turn on the high voltage before taking the background photo but do not discharge it(Only take one picture of the background, refer to the originalUIdata processing)
> 2. has repairs PINJust fine
> 3. There should be a simple step-by-step editor on the screen, no need for an additional ”measure once” button The action of
> 4. should also have the original UI ’s ”Calibration” and ”Trend” (concentration calculation) The spectral records of
> 5. The side of the pump24Vhas an emergency power-off switch.Megabeing pawned is very low. Should I transfer the amount directly to The source code ofmega … letmegaDetermine when to stop
> 6. 2.0.0All programs need to be completed

| # | Consequence | Section |
|---|---|---|
| 1 | the RPi's measuring order changed (already built and tested as 1.4.1): supply ON without pulses → voltage reached → ONE background frame → discharge | M1 |
| 2 | the PIN model of SPEC B.10 stands | — |
| 3 | Maintenance › Recipe gets a step editor. There is no discharge button outside Schedule measurement: **the maintenance discharge is removed** | M3, M4 |
| 4 | the original's Correction and Trend pages, concentration, blank check and blank zero are part of 2.0.0 | M5 |
| 5 | the Mega firmware is **not** changed in this build (the owner has not said yes to a change). Recipe pump steps that have an amount use the firmware's finite `P` move (the Mega stops by itself), as SPEC A.2 already says; weight dosing stays V7's host loop. SPEC J-17 now reads: a physical cut-off for the pumps' 24 V **exists** (owner, 2026-10-06) | — |
| 6 | every package is built | M6 |

Also true since the specification was written (do not "fix" these):

- TCP protocol is **2** (`pc\plasmic_client.py`, `pc\netcheck.py`, `rpi\tcpd\plasmic_tcpd.py`). A 1.3.0 RPi cannot connect at all. The `rpi_old` rule (missing `hello.flow`) stays as a second line.
- `hello` also carries `flow_error` (text, `''` when fine). `RpiLink.flow_view()`: a non-empty `flow_error` → `{name: <flow>, ok: false, background_frames: …, text: <flow_error>}`; every `plasma_run` is then refused with that text.
- `pc\app.py` has no "sequence" code any more (it is 1.4.1 now). `tests\test_app.py` has no sequence tests any more.
- `rpi\**`, `tests\test_tcpd.py`, `tests\test_measure.py`, `tests\simulated_rig.py`, `tests\simulated_spectrometer.py` belong to the lead. Nobody else edits them. The lead sets the RPi's `VERSION` to `2.0.0` at integration.
- Paths written `…\scratchpad\understand\…` in the three files correspond to `docs\integration-design-2.0.0\maps\…`. Current English interface screenshots are in `docs\guide-source\shots\…`.

---

## M1. The measuring order (RPi side; done, here only for the texts)

unified flow: `prepare` (checks, Nano parameters, Nano ARMED, spectrometer open) → `ramp` (HSPY ON, **no pulses**, wait for the voltage)
→ `background` (ONE frame, supply on, no pulses; START is sent meanwhile) → `signal` (per loop: exposure, a few ms later the
pulse train) → `off`. `job.progress.phase` therefore comes in the order `prepare` → `ramp` → `background` → `signal` → `off`
(`stopping`, `finishing` as before). `status.start_s` is about 5 for 1000 ms integration (3.6 s + the integration time).

| Where | Old text | New text |
|---|---|---|
| A.2 `plasma_run`, "What it does" | unified: 1 background frame, then discharge + signal frames | unified: supply on without pulses, 1 background frame, then discharge + signal frames |
| B.2 `flow_view()` `unified` | “background 1 sheets, click to make approx. Stop automatically after {start_s} Second discharge” | “Turn on the high voltage first and shoot the background 1 sheets, click to make approx. Stop automatically after {start_s} Second discharge” |
| B.5 `round.current.detail`, `ramp` | “Boost” | “The high voltage is on (not discharged yet), wait for the voltage to arrive.” |
| B.5 `round.current.detail`, `background` | “captures the background spectrum (high voltage is not turned on yet)” | “captures the background spectrum (high voltage is on, but not discharged yet)” |
| E.11 “Measurement Process” examples, F.5 step 4, API.md examples | “background 1 sheets, click to make approx. Stop automatically after 4 Second discharge” | “Turn on the high voltage first and shoot the background 1 sheets, click to make approx. Stop automatically after 5 Second discharge” |
| E.12 tile “background spectrum”, small line | “shot before discharge (RPi measurement process) ” | “High voltage is on, shooting before discharge” |

---

## M3. No maintenance discharge

The only way to discharge is Schedule measurement (one measurement = common 1 Second-rate). Removed everywhere:

- `POST /api/rpi/tool` accepts `cmd` = `prepare` · `clear_fault` · `spectrum` only. `plasma_run` → 400 `bad_request` “Unsupported command”.
- E.12: the button “⚠ Manual start plasma” and its dialog do not exist. The tool row holds only “Clear Nano Fault”.
- `state.interlock` has `manual` and `start` only (no `plasma`). `Devices.view()` likewise.
- B.7 "Maintenance tools": `spectrum` (no high voltage) stays as written; nothing sends an attended `plasma_run`. The attended-discharge
  page watchdog of the moved `Link` code stays in `rpilink.py` untouched (moved, not reworded) and keeps its test in `test_rpilink.py`.
- Tick rows are used in two kinds of dialog only: the schedule / calibration start, and a manual start while the RPi is not connected.
- SPEC J-14 loses "then a maintenance discharge and close the tab".
- `state.rpi.own_job` stays (it is the capture's job while “capture spectrum (no discharge)” runs).

---

## M4. Recipe step editor (Maintenance › Recipe)

One "draft" on the page: its rows live in the browser until “release” or “give up”. A draft comes from “New Recipe” (empty), from
“Import CSV files” (the file's rows) or from “Copy to modify” on a published Recipe. Publishing is unchanged (`/api/recipes/publish {name, csv}`);
a published Recipe is never changed in place (publish under a new name, then delete the old one).

### M4.1 Back end (WP3 `recipe.py`, WP5 route)

- `recipe.ACTIONS` gets, per action, the facts a form needs. `bootstrap.recipe.actions` (new) is that list, in the order of SPEC A.2:
  `[{"action": "delay", "name": "Waiting", "target": "" | "motor" | "valve", "value": null | {"min", "max", "decimals", "unit"}, "group": "generally" | "pump" | "syringe pump" | "scales" | "plasma"}]`.
  `repeat_start` has value `{min: 1, max: 20, decimals: 0, unit: ""}`; for `syringe_*` volumes and `syringe_speed` `max` is the value
  for the saved syringe size at the time of the bootstrap call (the server checks again). `nano_stop` and `power_off` appear once, as `power_off` “Turn off high voltage”.
- `recipe.compose(rows) -> str`: rows = list of 5-item lists `[action, target, value, unit, note]` (strings) → CSV text with the
  header line, written with the `csv` module (`lineterminator='\n'`). More than 500 rows or a row that is not 5 strings → `ValueError('The step data format is incorrect.')`.
- The result of `recipe.validate()` gains `rows`: one item per source row of the CSV (not expanded), also when the Recipe is not valid,
  as far as the file could be parsed: `{"line", "action", "target", "value", "unit", "note", "name", "text", "ok"}` — `name` = Screen name (`""`
  for an unknown action), `text` = the step sentence (for `repeat_start` “Repeat start ×n”, for `repeat_end` “Repeat ends”, for a row
  that fails R2 the raw cells joined with “ · ”), `ok` = false for the row named by `error_line`.
- `POST /api/recipes/validate` (PIN) accepts a third body form `{rows: [[action, target, value, unit, note], …]}` (the server calls
  `compose`, then `validate`). Reply as C.4.3 plus `rows`. An empty list is allowed: `valid: false`, `error` “CSV There is no action that can be performed”, `rows: []`.
- `GET /api/recipes/<id>` gains `rows` too.

### M4.2 The page (WP8 `maint-recipe.js`) — replaces "Right, an imported file" of E.10

Left panel footer: “New Recipe” · “Import CSV file” · ”Example...”. A published Recipe's footer: “remit CSV” · “Copy to modify” · `.sp` · “delete Recipe”.
While a draft exists the left list is disabled with the small line “Publish or discard what is being edited first Recipe”.

Draft view (right panel):

| Region | Content |
|---|---|
| `.ph` | `<h2>new Recipe (has not been released yet)</h2>` + `.sub` “Click a column to select,Then use the button below to modify” |
| `.kv` | “name” (button, text dialog “Recipe name”) · “Check” `passed` / `failed` · “steps/wheel” · ”Estimated time” · ”Measurement/pump” · ”Error” (tap → dialog) |
| warnings | as E.10 |
| table | columns List (44) · action (flex; `rows[].text`) · Remarks (flex); 6 rows per page; a tap selects a row (`.sel`); the row of `error_line` is `.flag`; empty draft: `.empty` “No steps yet” / “Press ”＋ New step” starts. ” |
| `.ed-bar` (44 px, above the footer) | “＋ New step” · ”Modify” · ”Move up” · ”Move down” · ”Delete” — the last four disabled until a row is selected. “＋ New step” inserts **after** the selected row (at the end when none is selected) |
| `.adm-foot` | “give up” (dialog “Discard this Recipe” / “The steps that have not yet been released will disappear. ”) · `.sp` · “release” (as E.10) |

“＋ New step” / “Revise” — one flow of dialogs (cancel anywhere = nothing changes):
1. “Select action” (`.wide` choices, 8 per page, in the order of `bootstrap.recipe.actions`; small line = the group). “Revise” skips this step when the action is kept: its first dialog is “No. N column:{Name}” with the choices “Change the value” ”Change the note” ”Change to another action”.
2. target `motor` → “Select motor” (choices = `hardware.groups[].motors[].label`); target `valve` → “select valve position” (“Valve position 1”…).
3. value → number pad titled with the action name, range / decimals / unit from the action.
4. “Remark” text dialog, 0–200 characters, may stay empty (rule line “can be left blank”).

After every change the page sends all rows to `/api/recipes/validate {rows}` and redraws from the reply (`rows`, `error`, counts).
While the reply is pending the bar is disabled. “move up” / “move down” / “Delete” need no dialog (delete: no question — discard and the row count make it visible, and the draft is not published yet).
SPEC E.10's last sentence and SPEC K's row "Recipe step editor" are void.

---

## M5. Correction, Trend, concentration (ported from the original HMI)

Sources to port **the logic and the texts** from (read-only; never run them, never open the HMI's `data\` folder):
`UI\UI\hmi_v1_1\rpi_tools\spectrum_analysis.py`, `calibration.py`, `measurements.py`, `calibration_config.py`, `tests\test_calibration.py`,
`web\calibration\{index.html,app.js,style.css}`, `web\trend\{index.html,app.js,style.css}`. The reading notes are
`maps\hmi-analysis.md` (formulas in §7, every message quoted) and `maps\hmi-ui-structure.md` §3.6, §10, §11.

What changes against the original:

| Topic | 2.0.0 |
|---|---|
| where the analysis runs | **on this PC**, on the stored spectrum set `runs\<job>.json` (`axis`, `signal` = mean of the signal frames, `background` = the background frame). No SSH, nothing on the RPi |
| background | one frame, taken with the supply on and no pulses (M1). The analysis subtracts it when `subtract_dark` is true, exactly as the original subtracted the mean of three |
| `frames_per_sum` | not carried over (a pure scale factor; the calibration absorbs it). No `/api/frame-sum` |
| metals | `('Cu', 'Zn', 'Ni')`, unit `mg/L`, as the original |
| analysis settings | `pc\data\analysis_config.json` in the original's format (`validate_config` rules unchanged), created at first use from `pc\analysis_default.json`. That default holds the windows of the original's deployed file (`rpi_tools\analysis_config.json`); its `comment` says they were tuned for the original spectrometer and have **not** been checked for the one on this rig (1862 pixels, 180–500 nm). Edited by hand in a text editor (as in the original); the page shows `method_id` and any error of the file |
| the applied calibration | `calibration_active.json` keeps a **snapshot** of what measurements use: `{calibration_id, applied_at, metals, method_id, unit, previous, reference: {axis, values} \| null, blank_norm}`. Re-analysing or changing the exclusions of a record never changes measurements until “Reapply” (the original read the record again after a restart) |
| bottle-swap prompt | “Please replace with standard product {name (Cu x / Zn y / Ni z mg/L) },Put the pipette into the new bottle and click ”Confirm Liquid Change” (the original named pump P6) |
| Recipe templates | none shipped (the originals are timed for the PLC pumps). The standard Recipe is any published Recipe with exactly one `plasma_run`; “Discharge measurement only” qualifies |
| standards table CSV import / export | not built |
| pages | inside the frame as ordinary page modules (the original used two iframes with native inputs); numbers through the pad, choices through the choice dialog |

### M5.1 Modules (WP9; standard library only; no import of `app`, `runner`, `devices`, `pumpboard`, `rpilink`)

| File | Contains |
|---|---|
| `pc\analysis.py` | pure functions, ported: `ANALYSIS_VERSION = 'area-v1'`, `DEFAULT_CONFIG`, `validate_config(raw)`, `method_id(config)`, `Spline`, the shift fit, the three intensity methods, `spectrum_of(run, config) -> {axis, values, signal_frames, background_frames}` (replaces `job_spectrum`: `values = signal − background` when `subtract_dark`, else `signal`; the original's checks on pixel count 8–8192, finite numbers, increasing axis), `build_reference(named_spectra, config)`, `analyze_one(spectrum, reference, config) -> row`. Error texts stay in the original's English (they are shown after “Analysis failed: ”) |
| `pc\analysis_default.json` | the default analysis settings (see the table above) |
| `pc\calibration.py` | ported: constants, `DEFAULT_PLAN`, `validate_plan`, `plan_warnings`, `describe`, `build_steps(plan, steps, prime_steps)`, `linear_fit`, `regress(plan, runs, rows, exclude)`, `validate_exclusions`, `summary(record)`, `validate_calibration_config`, `FORMULA`. `build_steps` works on the step dicts of `recipe.validate()['steps']` and returns the same shape renumbered (`n`), each with `calibration: {level, level_name, run, discard, round, prime?}`, `round` / `rounds` = run number / total runs, plus the generated step `{action: 'operator_prompt', name: 'Fluid change', text: <the prompt>, target: '', target_label: '', value: '', unit: '', note: <the prompt>, line: 0, phase: …, planned_s: null}` |
| `pc\measurements.py` | ported: `METALS`, `KINDS`, blank-check thresholds (`DEFAULT_QC`, `validate_qc`), blank zero (`DEFAULT_BLANK_ZERO`, `validate_blank_zero`, `zero_status`, `apply_zero`), `concentration`, `evaluate`, `failed`, `summary`, `Log(path)` (append-only JSONL, newest 5000 in memory, `add`, `latest(kind)`, `since(cutoff_iso, limit)`). Without `scale_for_frame_sum` |
| `pc\analyst.py` | `Analyst` — the one object the rest of the program talks to (below) |
| `tests\test_analysis.py`, `tests\test_calibration.py`, `tests\test_measurements.py`, `tests\test_analyst.py` | the original's test cases ported to these modules (synthetic spectra; the original's numbers), plus the cases of M5.6 |

```python
class Analyst:
    def __init__(self, data_dir, load_run, write_json, on_event=None, on_alarm=None)
        # load_run(job) -> the stored spectrum set (Store.load_run); write_json(path, value) = store.write_json;
        # on_event(text, level); on_alarm(key, priority, area, text, kind). Records 'running' / 'analyzing' found at start -> 'interrupted'
    # settings
    def settings_view(self) -> {'method_id': str | None, 'config': dict | None, 'error': str, 'path': str}
    def coefficients(self) -> {metal: {'a', 'b', 'c'}}
    def set_coefficients(self, raw) -> same            # ValueError with the original's texts
    def qc(self) -> dict;                 def set_qc(self, raw) -> dict
    def blank_zero(self) -> {'settings', 'status'};   def set_blank_zero(self, raw) -> same
    # calibration
    def plan(self) -> dict;               def save_plan(self, raw) -> {'saved', 'valid', 'plan' | 'error', 'warnings'}
    def check_plan(self, raw, recipe, prime) -> {'plan', 'warnings', 'recipe_name', 'prime_recipe_name', 'prime_delay_seconds', 'runs', 'kept_runs', 'round_delay_seconds', 'estimated_seconds', 'steps'}
        # recipe / prime = {'id', 'name', 'sha256', 'validated': <recipe.validate() result>, 'estimate_seconds'} or None; raises ValueError (the original's texts)
    def create(self, plan, recipe, prime, operator, schedule_id) -> record          # status 'running'
    def add_run(self, cal_id, info)          # info = {level, level_name, run, discard, round, job, finished_at}
    def finish(self, cal_id, status, error='') -> record      # status 'completed' -> analysed here ('analyzed' / 'analysis_failed'); 'stopped' / 'failed' kept with their runs
    def analyze(self, cal_id) -> record      # Reanalysis; RuntimeError('Calibration analysis is in progress, please wait.') when one runs
    def set_exclusions(self, cal_id, exclude) -> record
    def apply(self, cal_id) -> {'applied', 'config', 'active'}
    def records(self, limit=50) -> [summary];   def record(self, cal_id) -> dict      # KeyError -> 404
    def active(self) -> dict | None           # without 'reference' (large); for state and pages
    def running(self) -> dict | None          # the record of a calibration that is running now (summary), else None
    # measurements
    def measure(self, job, kind, info) -> record     # after a round's plasma_run; never raises; appends to measurements.jsonl
    def measurements(self, hours=None) -> {'rows', 'qc', 'calibration', 'coefficients', 'blank_zero', 'unit'}
    def latest(self) -> {'latest_sample': … | None, 'latest_blank': … | None}
    revs: {'measurements': int, 'calibrations': int}   # each grows when its list changes
```

Calibration record, measurement record, `summary` shapes, statuses and every Chinese message: as the original (`maps\hmi-analysis.md` §3.8, §4.2),
minus `frames_per_sum`, with `job` = the run id (`HMI-<32 hex>`) and `info` = `{round_id, schedule_id, slot, recipe_name, operator}`.
Files under `pc\data`: `analysis_config.json`, `calibration_plan.json`, `calibrations\CAL-<32 hex>.json`, `calibration_config.json`,
`calibration_active.json`, `measurements.jsonl`, `blank_qc.json`, `blank_zero.json` (never deleted, written through `write_json`).

### M5.2 Runner and steps (WP4)

`Runner(store, alarms, devices, rpi, pump, operator_getter, analyst=None)`. With `analyst=None` (old tests) nothing of this section runs.

**Blank slots.** The schedule body may carry `blank: {recipe_id, every}` (`every` 1–100) or `null`. Slot kinds: the first slot is
`blank`, then one blank after every `every` samples that finished `done` (the original's rule; skipped slots do not count). Slot
record gains `kind: 'sample' | 'blank'`; the round of a blank slot runs the blank Recipe and has `sample_kind: 'blank'`. Start
checks, ticks, estimate and interval rule consider **both** Recipes (the longer estimate; `uses_*` = either). Errors:
“Blank Recipe does not exist” · “blank Recipe Cannot be compared with samples Recipe is the same” · ”Insert blanks for every few samples: must be 1–100 ’s integer” · ”Blank At the time of Recipe Must content measurement steps (plasma_run) ”.

**After every `plasma_run` of a round that is not a calibration**: `analyst.measure(job, round.sample_kind, info)`; the returned
record's `measurements.summary()` is appended to `round.measurements` and to the step result (`measurement`). It never fails the step.
A blank whose `qc.status` is `fail` → alarm `blank_qc:<round id>` (middle, area measurement, kind `event`) “Blank solution exceeds baseline threshold:{problems}” and the
same text as a `warn` event; `unchecked` → event “Blank is not compared:{reason}”. A sample with concentrations → event
“sample concentration:Cu x, Zn y, Ni z mg/L” (`.4g`; only the metals that have one).

**Run-log row** gains `sample_kind` and `measurements` (the summaries). `result_text`: sample with concentrations “Cu 0.123 · Zn 0.045 · Ni 0.300 mg/L”
(`.3g`, only the metals that have one) · sample without “Uncorrected” (reason Correction has not been applied yet) / “analysis failed” / “Unconverted concentration” followed by “ · highest peak 337.1 nm” ·
blank “Blank ✓ baseline is normal” / “Blank ⚠ exceeds baseline threshold” / “blank (not compared)” · no `plasma_run` “has no measurement steps” · slot rows “Not measured”.

**Calibration run.** `Runner.start_calibration(body)` with `body = {plan, confirm: {high_voltage, liquid, hv_off_checked}}`:
checks in this order — operator selected (400 “Please select an operator first”) · no schedule active (409) · `analyst.check_plan(plan, recipe, prime)`
(400 with the original's texts; the standard Recipe must hold exactly one `plasma_run`, the prime Recipe no RPi step) · ticks as
B.5 stage 4 (`high_voltage` always) · the start checks of A.4 for the union of both Recipes (409 `blocked`). Then
`analyst.create(...)`, a schedule record with `kind: 'calibration'`, `calibration_id`, `planned_total: 1`, one slot, and one round whose
steps are `check_plan(...)['steps']`; round record `mode: 'calibration'`, `calibration_id`. Event “Standard calibration starts:{Recipe},{n} concentration,common {runs} wheel (high pressure confirmed)”.

- Step `operator_prompt` (only generated, never in a CSV): the executor sets `round.prompt = {text, since}` and waits (stop flag every
  0.1 s; pause is honoured like in `delay`) until `Runner.confirm_prompt()` → 409 `state` “There are currently no fluid changes waiting for confirmation” when none waits.
  Event “Waiting for fluid change:{level name}” / “Fluid change confirmed:{level name}”.
- After each `plasma_run` with a `calibration` tag: `analyst.add_run(...)` (no `measure`); event “Correction {level} pre-run completed (not included in return)” / “Correction {level} No. n round completed”.
- End of the round: `analyst.finish(cal_id, 'completed' | 'stopped' | 'failed', error)`; its analysis result → event “Calibration analysis completed:{Cu R² …}” / “Calibration analysis failed:…”.
- Restart: as any round (`interrupted`, never resumed); the `Analyst` marks the record `interrupted` by itself.
- `schedule.kind` is `'schedule'` or `'calibration'` (old records without the key: `'schedule'`).

### M5.3 HTTP (WP5) — additions to API.md

| Path | Method | PIN | Busy rule | Body → reply |
|---|---|---|---|---|
| `/api/calibration` | GET | — | — | `{plan, records, active, coefficients, recipes: [{id, name, plasma_count, uses_rpi, valid}], unit, metals, settings: {method_id, error}, run: state.calibration_run}` |
| `/api/calibrations/<CAL-32hex>` | GET | — | — | the full record; 404 “Calibration record does not exist” |
| `/api/measurements?hours=<1–8784>` | GET | — | — | `Analyst.measurements(hours)` |
| `/api/calibration/plan` | POST | no | — | `{plan}` → `save_plan` |
| `/api/calibration/check` | POST | no | — | `{plan}` → `check_plan` without `steps` |
| `/api/calibration/start` | POST | no | — | `{plan, confirm}` → `{accepted: true, calibration_id, schedule}` |
| `/api/calibration/confirm` | POST | no | — | `{}` → `{accepted: true}` |
| `/api/calibration/analyze` | POST | no | — | `{id}` → `{accepted: true, id}` (runs in a thread `cal-analysis`; the record goes `analyzing` → `analyzed` / `analysis_failed`) |
| `/api/calibration/exclude` | POST | no | — | `{id, exclude}` → `{accepted: true, exclude_levels}` |
| `/api/calibration/apply` | POST | **yes** | yes | `{id}` → `{applied, config, active}` |
| `/api/calibration-config` | POST | **yes** | yes | `{config: {Cu: {a, b, c}, Zn: …, Ni: …}}` → `{config}` |
| `/api/blank-qc` | POST | **yes** | — | `{qc}` → `{qc}` |
| `/api/blank-zero` | POST | **yes** | — | `{settings}` → `{settings, status}` |

`/api/schedule/preview` and `/start` take `blank` (M5.2); the preview reply gains `blank: {recipe_id, recipe_name, every} | null`.
`/api/stop` also ends a calibration (it is a round). `ValueError` → 400, `RuntimeError` → 409 `state`, unknown id → 404.

`GET /api/state` gains:

```jsonc
"measurement": {"latest_sample": {"time", "valid", "conc_reason", "metals": {"Cu": {"conc", "zero"}, …}} | null,
                "latest_blank": {"id", "time", "qc": {"status", "problems", "reason"}} | null},
"calibration_active": {"calibration_id", "applied_at", "metals": ["Cu", …]} | null,
"calibration_run": null | {"id", "status", "recipe_name", "level", "level_name", "run", "runs", "prompt": {"text", "since"} | null},
"revs": {…, "measurements": n, "calibrations": n},
// and inside existing objects:
"schedule": {…, "kind": "schedule" | "calibration", "calibration_id": null, "blank": {"recipe_id", "recipe_name", "every"} | null, "slots": [{…, "kind": "sample" | "blank"}]},
"round": {…, "mode": "schedule" | "calibration", "sample_kind": "sample" | "blank", "prompt": {"text", "since"} | null, "measurements": [summary…]}
```

`Application` builds `Analyst(data_dir, store.load_run, store.write_json, on_event, on_alarm)` after `Store` and hands it to `Runner`.

### M5.4 Frame and operator pages (WP6, WP7)

- Nav rail, in the original's order: “Overview” · ”Scheduled Measurement” · ”Manual Operation” · ”Trend” · ”Execution Record” · ”Calibration” · ”Alarm” · “🔧 Maintenance”. Buttons 104 × 50,
  gap 4, so that eight fit. Page ids `dash` `schedule` `manual` `trend` `runs` `calibration` `alarms` `maint`; deep links `/#trend`, `/#calibration`.
  “Correction” carries a `.badge` “!” (orange) while `state.calibration_run.prompt` is set.
- Footer while `schedule.kind == 'calibration'` is active: `.start` “Calibrating” (disabled; “continue RESUME” when paused) · `.prog` cells
  “Correction” `No. r / R wheel` · “at present” `Waiting for fluid change` / `under measurement` / `Paused` / `Suspended` / `Stopping` · “Steps” `a / b · {phase}` · “standard product” `{level_name}`.
- Overview D1: the original's three metal cards “copper Cu” “zinc Zn” “nickel Ni” — tag “Corrected” (green outline) / “Uncorrected” (`.q-na`); value = the latest
  sample's concentration (3 significant digits; “—” when none); sub-line, first match: “mg/L · {MM-DD HH:MM} · Blank zero point has been deducted {x}” ·
  “mg/L · {MM-DD HH:MM}” · “mg/L · {conc_reason}” · “mg/L · Applied {MM-DD} Calibration, waiting for the next sample measurement” · “mg/L · has not been calibrated with the standard and does not display the concentration.” —
  then the tile “latest measurement” Will as in E.4. The tiles “HSPY high voltage power supply”, “pump”, “weight” move into D4, which has seven cells:
  “RPi connection” · “HSPY high voltage power supply” · “Nano Controller” · ”Pump” (`{pump.text} · {stop all｜Pump 1 running｜Cleaning…}`) · “Syringe Pump” · ”Weight” (`{grams} g` / “—”) · “active alert”.
- Schedule measurement: one more tile “blank solution” between “end condition” and “measurement content”: value “Not inserted” / “Every N samples”, sub-line
  “Regularly confirm the baseline” / “{Recipe} · Enter blank first time”; editing: dialog “Blank Recipe” (choices “does not insert blanks” + the published Recipes that have a `plasma_run`,
  except the chosen sample Recipe) → pad “Insert blanks every few samples” 1–100. Form default `blank: {enabled: false, recipe_id: '', every: 4}`.
  Slot log: a blank slot's sequence is prefixed “［Blank］”. Start dialog: with a blank one more line “blank solution” `{Recipe} · Every N samples`.
- execution record: the column results is titled “concentration/results” and shows `result_text` (M5.2); the detail's left column gains “category” `sample` / `Blank` and,
  when there is one, “concentration” `Cu x · Zn y · Ni z mg/L`.

### M5.5 Pages Trend and Correction (WP10) and the tab correction coefficient (WP8)

Both are ordinary page modules (`js\pages\trend.js`, `js\pages\calibration.js`, styles in `css\analysis.css`, loaded by `index.html`
— WP6 adds the `<link>`). They keep the original pages' structure, texts, colours and chart drawing (`maps\hmi-ui-structure.md` §3.6,
§11.2; the original `app.js` files), rebuilt with `ctx.dom`, `ctx.dialog` and the frame's classes; nothing scrolls (pagers);
numbers through the pad; SVG charts drawn in the module (no dependency).

**Trend** — title “concentration trend”; three stacked charts “copper Cu” “zinc Zn” “nickel Ni”; mode `.seg` “concentration” “No zero points deducted” “light intensity”; button “display range…”
(dialog: choices “1 Hour” “8 Hour” “24 Hour” “7 days” “30 days” ”All”; the per-metal y-limits and the custom from–to of the original are not
built); button “blank threshold/zero point” (dialog with the original's fields, each a tap line → pad or choice; “store” needs the maintenance
session: without one the message “needs repair PIN: Please click on the left first🔧 Maintenance ”Login”) → `POST /api/blank-qc`, then `/api/blank-zero`. Series, band, point
detail line and the "no concentration" notice as the original. Data `GET /api/measurements?hours=N`, reloaded when
`revs.measurements` changes and every 60 s. Choice kept in `localStorage['plasmic.trend']`.

**Correction** — title “standard calibration”; status line “Current coefficient (YYYY/MM/DD correction):…” / “Current coefficient (uncorrected page):…”; `.seg.tabs` “Calibration Settings” ”Execution” ”Results and Records”.
- Calibration setting: standards table (Name · Cu · Zn · Ni · Number of rounds · ✕; each cell a button → text dialog / pad; a metal cell may be cleared with the
  pad's “Clear” choice = this bottle does not hold the metal), “＋ New concentration” (max 12); “standard product Recipe” and “Fluid replacement and draining Recipe (optional)” tiles
  (choice dialogs; Recipes with RPi steps are not offered for the second), stepper “The number of pre-running rounds for each concentration (not included in the regression)” 0–3; the six summary
  lines, warnings, error; “Start calibration” → dialog “⚠ Start standard calibration” (`.danger.hv`; lines; the original's red text; tick rows as the schedule
  start dialog: high voltage ×2, “Pipes, reagents and waste are in place” when a Recipe uses the board, `hv_off_checked` when needed, the E-stop row) →
  `POST /api/calibration/start`. Every edit: `POST /api/calibration/check` after 400 ms, `/plan` after 1200 ms. No operator chosen →
  the operator picker first (as Start scheduling).
- execution: prompt bar “Waiting for fluid change” + the text + big button “Confirm fluid change”; panel title by state; level cards; the last 30 events of the round;
  the page moves to this tab when a prompt appears. Stopping is the footer's “Stop STOP” (no second stop button).
- results and records: record list (6 per page) and the detail as the original (metal tabs, regression chart, coefficients, the R² < 0.995
  warning, the two tables with the included tick per level → `/exclude`), “re-analysis”, “application coefficient” / “Reapply” (dialog “Apply correction coefficient”; needs the
  maintenance session, else the message “application needs maintenance PIN: Please click on the left first🔧 Maintenance”Log in and click Apply”).
- Data `GET /api/calibration` when `revs.calibrations` changes and every 2 s while the page is visible and a calibration runs.

**Maintenance › correction coefficient** (seventh tab, between spectrum and operator; module `maint-coefficients.js`; deep link `/#maint-coefficients`): title
“Cu/Zn/Ni correction coefficient”, sub “concentration = (a × light intensity + b) × c; When applying correction a, b will be overwritten,c reset to 1”; a 3 × 3 grid of `.pbtn` (rows Cu / Zn / Ni,
columns a / b / c) → pad (−1000000…1000000, 6 decimals, with a “±” key — WP6 adds `signed: true` to `dialog.pad`); “Give up modification”,
“store” → dialog “Storage correction coefficient” → `POST /api/calibration-config`. Below the grid, read-only: “analysis method” `{method_id}` · “Analysis profile”
`data\analysis_config.json` (+ its error in `.q-bad` when it does not load) · “Calibration in application” `{YYYY-MM-DD HH:MM} · Cu, Zn, Ni` / “Not applied yet”.

### M5.6 Tests that must exist (besides the ported ones)

- `spectrum_of` on a stored run with one background frame; `subtract_dark` false ignores the background.
- A calibration on synthetic stored runs end to end through `Analyst`: plan → `create` → `add_run` × n → `finish('completed')` →
  `analyzed`, R² > 0.999, `apply` → a later `measure()` of a known sample within 5 %; a drifted blank → `qc.status == 'fail'`.
- Re-analysis and exclusion after `apply` do not change `active()` nor the next `measure()`.
- A broken `analysis_config.json` → `settings_view().error` set, `measure()` returns a failed record (no exception), the program still starts.
- Runner (WP4): blank slot order for `every = 2` over 7 slots (blank, sample, sample, blank, sample, sample, blank) on the simulators;
  a calibration with 2 levels × (1 discard + 1 kept) → 2 prompts, 4 discharges, record `analyzed`; STOP while a prompt waits →
  `stopped`, everything off; `plasma_run` of a calibration never produces a measurement row.
- API (WP5): the PIN table rows of M5.3; `/api/calibration/confirm` without a prompt → 409.

---

## M6. Work packages — what changes in WORKPACKAGES.md

| WP | Change |
|---|---|
| WP1b | the guide, `READ-ME-FIRST.txt`, `historical-development-notes.md`, `work record.md` also cover M1–M5; `installer\install.py`'s file list gains the new modules; done by the lead |
| WP2 | unchanged |
| WP3 | + M4.1 (`recipe.ACTIONS` facts, `recipe.compose`, `rows` in the validate result); `flow_view()` texts of M1 and the `flow_error` rule of M0; `interlock` has no `plasma` |
| WP4 | + M5.2; no maintenance discharge (M3): `Devices.view()` without `plasma`, `hv_session()` is used by rounds only; `tests\test_runner.py` gains the cases of M5.6 |
| WP5 | + M5.3, M4.1 route form, `bootstrap.recipe.actions`; `/api/rpi/tool` per M3; `Application` owns the `Analyst` |
| WP6 | + nav and footer of M5.4, `dialog.pad({signed})`, `dialog.pad` / `dialog.choose` unchanged otherwise; `index.html` links `css/analysis.css`; page ids `trend`, `calibration`; tab id `coefficients` |
| WP7 | + Overview D1 / D4, Schedule measurement tile “blank solution”, execution record column of M5.4; the M1 texts |
| WP8 | + M4.2 (the editor), tab correction coefficient (`maint-coefficients.js`); E.12 without the discharge button |
| **WP9 (new)** | `pc\analysis.py` · `pc\analysis_default.json` · `pc\calibration.py` · `pc\measurements.py` · `pc\analyst.py` · `tests\test_analysis.py` · `tests\test_calibration.py` · `tests\test_measurements.py` · `tests\test_analyst.py`. Depends on nothing (uses a stub `load_run` / `write_json` until WP3 is there) |
| **WP10 (new)** | `pc\web\css\analysis.css` · `pc\web\js\pages\trend.js` · `pc\web\js\pages\calibration.js`. Depends on WP6's contract and M5.3 |

Order: WP3, WP9 and WP6 (step 1) start at once, beside the running WP2. WP4 after WP2 + WP3 (+ WP9's `Analyst` signature above).
WP5 after WP4. WP7, WP8, WP10 on WP6's contract, finished on the practice mode. WP1b and the integration last (lead).

---

## M8. A pump run with a time limit is a finite move — the Mega stops by itself (WP2, then WP4)

The owner's point 5: the board, not the PC, should decide when a pump stops. The stock firmware can do that for every run whose
length is known: `P,<motor>,<steps>,<sps>` ends by itself. So **`PumpBoard.run(motor, flow_ml_min, direction, limit_s, owner)` no
longer sends V7's endless `V`**. It sends, through the checked link, `?` then `P,<motor>,<±steps>,<sps>` with
`sps = round(flow_ml_min × pulses_per_ml ÷ 60)` (V7's own speed conversion and sign convention, as `motors.run` computes them; the
flow limits of `motors.run` are checked the same way) and `steps = sps × limit_s`, then verifies `selected == motor` and state `wait` / `move`.

| Topic | Rule |
|---|---|
| Recipe `pump_on` / `pump_reverse` | `limit_s = planned_s + 5` as before. `pump_off` stops it at the planned time (`motors.stop`, verified idle). If the move runs out before `pump_off` arrives the round fails with “{motor} The planned operation time has been exceeded and it has automatically stopped.” (the `deadline_tripped` path) — but the pump has already stopped by itself, also when the PC was asleep, frozen or gone |
| manual “ON” with “n Automatically stop in seconds” | `limit_s = n`: the Mega stops after n seconds by itself; the event “{motor} The set time has arrived and it has automatically stopped” is written when the board reports `idle` |
| pause / resume of a round | stop, then a new finite move for the remaining time (+5 s), as B.5 `hold()` |
| what the page sees | `state.pump.motors[].state` stays `run` with `left_s` for such a move (the wrapper knows it started a timed run; the firmware's own word for it is `move`). `dose_volume` stays `move` “Quantification in progress” |
| the host deadline | stays as a second line: `limit_s` + 2 s after the start and still not `idle` → `motors.stop`, then `stop_all` |
| what is still endless | only V7's weight dose (`motors.dose_by_weight` drives `V`). The guard process is armed for weight doses only |
| speed clamp `[INFER]` (firmware notes §2.3-1: a `P` lowers the driver's top speed until a faster `P`) | every timed run and every dose now carries its own `sps`, so they are not affected. Before `dose_weight` the wrapper sends `P,<motor>,0,<sps of the fast weight flow>` (a zero-step move, accepted and finished at once) and waits for `idle`, so V7's `V` speeds are not capped by an earlier slower move. Unproven on the rig either way; stays on the bench list (J-8) |
| SPEC warning W1 | dropped (it warned about the cap after a dose) |
| SPEC J-17, J-18 | the endless-run exposure is now limited to weight dosing; the guard test on the rig uses a weight dose |

## M9. The PC does not go to sleep while it is working (WP5)

`Application` asks Windows to stay awake (`ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`)
while a schedule is active, a round or a manual operation runs, or a calibration waits; and gives the request back
(`ES_CONTINUOUS`) when nothing runs and at `close()`. It is a request of this process only, no setting is changed. Not called in
tests (`Application(..., keep_awake=False)` default; `main()` passes `True`).

---

## M7. Building beside the working 1.4.1 — names used until the integration

The owner's shortcut starts this very folder, and 1.4.1 must keep working in it until 2.0.0 is complete. So, **until the lead
integrates** (he renames; nobody else does):

| In SPEC / API / WORKPACKAGES | During the build |
|---|---|
| `pc\app.py` (the 2.0.0 entry point) | **`pc\app2.py`**. The present `pc\app.py` (1.4.1) is not edited by anybody. `import app2` wherever a test or tool would `import app` |
| `tests\test_app.py` (ported) | **`tests\test_app2.py`**. The present `tests\test_app.py` (1.4.1) is not edited |
| `pc\web\index.html`, `pc\web\css\…`, `pc\web\js\…` | **`pc\web2\…`** (same structure). `pc\web\index.html`, `app.js`, `style.css` (1.4.1) are not edited or removed. `httpd.py` serves `pc\web2\` when that folder exists, else `pc\web\`. Fonts: `pc\web2\fonts\` holds copies of `pc\web\fonts\*` |
| `pc\data-demo` (practice data) | every implementer passes his own `--data` folder under `%TEMP%`; nothing writes to `pc\data` or `pc\data-demo` |
| `pc\schedule.py` | only **added** names and added tuple members (B.6); nothing 1.4.1 uses changes its meaning. `tests\test_app.py` (1.4.1) must still pass |
| `installer\install.py`, `tests\test_installer.py`, `Set-PIN.cmd`, the guide, `READ-ME-FIRST.txt`, `historical-development-notes.md` | untouched until the integration (lead) |

New files that 1.4.1 never loads (`pc\recipe.py`, `pc\store.py`, `pc\pumpboard.py`, … and their tests) are simply added.
Practice mode for page work: `.\pc\runtime\python\python.exe -B .\pc\app2.py --demo --no-browser --port 879N --sim-port 877N --data $env:TEMP\plasmic-dev-N`.
`tests\test_measure.py` and `tests\test_tcpd.py` are timing-sensitive and belong to the lead: do not run them (import `test_tcpd.SimServer` freely).

---

## M10. Small interface facts fixed while the packages were built (2026-10-06)

| From | Fact |
|---|---|
| WP3a | `recipe.validate(text, hardware, syringe_ul=5000, encoding='utf-8')`: pass the `encoding` that `recipe.decode()` returned; `'cp950'` adds warning W6. The route adds `csv`, `encoding`, `estimate_seconds` to the result |
| WP3a | `recipe.estimate(result, hardware, rpi_estimate_s, syringe_speed, integration_ms=None)`: pass the integration time of plasma parameters for `spectrum_capture` (1000 ms when unknown) |
| WP3a | `bootstrap.hardware` (and `PumpBoard.hardware()`) gains `pulses_per_ml`, `dose_pulses_per_ml` and `syringe.full_stroke_steps` (from the vendored YAML): the dose estimate of A.5 needs them |
| WP3a | `validate()['rows']` leaves out empty CSV rows: the List column and the `.flag` row come from `rows[].line` and `rows[].ok`, never from the list index. A failure of R1 / R2 gives `steps: []`; a failure of R3–R10 keeps the steps |
| WP3a | `steps[].action` may be `nano_stop` (same as `power_off`); `planned_s` is set on `pump_on` / `pump_reverse` only. Bootstrap takes `recipe.LIMITS`, `recipe.EXAMPLES`, `recipe.actions_view(hardware, syringe_ul)` |
| WP3a | missing-hardware text: “No. N Column: Program folder is incomplete (missing pc\vendor) ,can’t do“{Name}”.Please copy the entire folder again. ” |
| WP9 | `Analyst.check_plan()` returns one more key `phases` (`[{name, from, to}]` of the generated sequence; the round uses it). `Analyst.request_analysis(cal_id) -> {'accepted': True, 'id'}` starts the thread `cal-analysis` after the same checks as `analyze()`: the route `/api/calibration/analyze` calls this one. `add_run()` returns a bool and never raises; `measure(job, kind, info=None)`; `finish()` also takes `'interrupted'`; `Analyst.UNIT`, `Analyst.METALS`; an unknown record id is `KeyError` (→ 404), a malformed one `ValueError` |
| WP9 | The `Analyst` writes no event and raises no alarm about a measurement's result: the Runner does (M5.2). `pc\analysis_default.json` must be in the installer's file list |
| WP9 (texts changed by the lead) | “The analysis settings are different from the applied calibration, please recalibrate” (the original said “RPi Analysis settings…”) · “program closed during calibration execution or analysis” (the original said “HMI is in...”) |
| WP3b | `RpiLink.on_alarm` (attribute, `None` by default): called as `on_alarm('raise', key, priority, area, text, kind)` / `on_alarm('clear', key)` for `rpi_link_lost`, `rpi_old`, `rpi_flow_fallback`, `nano_fault` (B.9 texts). `Application` sets it to a function that forwards to `Alarms` and raises `rpi_link_lost` as high while a busy schedule uses the RPi. `PumpBoard`'s `on_alarm` has the same call shape |
| WP3b | `Alarms.ack(ids=None, actor=None)`. `Store.events(before, limit)` and `Store.runlog(before, limit)` return lists, newest first; "more" = ask for `limit + 1`. Counters for `state.revs`: `store.events_rev`, `recipes_rev`, `runlog_rev`, `runs_rev`. `Store.write_json(path, value)` is the one writer. `Store.recipe(id)` / `round(id)` / `delete_recipe(id)`: unknown id → `KeyError` (→ 404); a Recipe file that changed → `ValueError` “Recipe file is different from when published, please re-publish”. `Store.save_run(result, round_id=, recipe_name=, operator=, slot=, source=)` takes exactly these keywords. `publish_recipe(name, csv, summary, source)`: `summary` may be the whole `recipe.validate()` result |
| WP3b | `Store.save_round()` / `save_schedule()` write what they are given: the Runner copies the record and writes it inside one writer lock of its own (as 1.4.1's `Schedule._save`). `RpiLink.wait_job()` returns only `(status, note)`: for `hv_stop_unconfirmed` read `rpi.latest()['job']['post_stop']`. After a stop request `wait_job` gives the job `rpilink.STOP_WAIT_SECONDS` (60) more, then answers `('failed', 'exceeds 60 seconds are not over yet;…')` |
| WP3b | `RpiLink.hv_view()['reasons']` is a list of plain sentences (B.4 rows). `Devices` turns them into `{code, text, fix}`: `known` false → `hv_unknown`, fix `''`; not idle → `hv_on`, fix “Press ”Stop” at the bottom STOP”” |
| WP3b | `pinlock.SAVED_TEXT` is the closing sentence of `--set-pin`. `PinLock(path, practice_pin='000000')` never reads a file. `migrate.PROGRAM = '2.0.0'` |
| lead, to do in `pc\rpilink.py` / `pc\pinlock.py` before the integration | the link thread ends silently on an unexpected exception (`KeyError` / `TypeError` from a status of an unexpected shape): give its loop a catch-all that writes one event per distinct text, drops the connection and waits 2 s. `PinLock.unlock()` / `verify()` with a number instead of text must answer “Maintenance PIN error” |

---

## M11. A pretend sample in practice mode (RPi simulator, WP4 hook, WP5 wiring)

So that Correction and Trend show real results in practice mode, the RPi simulator (`plasmic_tcpd.py --sim`) has a pretend sample: its Cu / Zn / Ni
lines grow with a concentration, and two fixed lines sit inside the shift windows of `pc\analysis_default.json`.
New **simulator-only** TCP command `sim_sample {"sample": {"Cu": mg/L, "Zn": …, "Ni": …} | null}` → `{"sample": {...}}`
(a metal left out is 0; `null` restores the default 0.5 / 0.5 / 0.5; on the real RPi the command answers “Only simulation mode supports this command”).
Checked on the simulator with the default analysis settings: at 400 V R² ≈ 0.9998, at 100 V 0.98–0.997 (blank + 0.2 / 0.5 / 1.0 mg/L, two rounds each).

- **WP4**: `Runner(..., before_discharge=None)`. When given, it is called on the runner thread right before a round sends `plasma_run`, with
  `{'round_id', 'mode': 'schedule' | 'calibration', 'sample_kind': 'sample' | 'blank', 'conc': {'Cu', 'Zn', 'Ni'} | None}` — `conc` is the
  standard's concentrations (a metal the bottle does not hold: 0) for a calibration step, else `None`. An exception from it is written as a
  `warn` event and otherwise ignored. Production passes nothing.
- **WP5**: `Application(demo=True)` passes a function that sends `sim_sample` over the RPi link (only when `hello.sim` is true):
  calibration → `conc`; blank → all 0; sample → a slowly drifting demo sample (Cu `0.50 + 0.10·sin(n/3)`, Zn `0.30 + 0.05·sin(n/5)`,
  Ni `0.40 + 0.08·cos(n/4)`, n = number of sample rounds since the program started). Never in real mode.

---

## M12. The pump board wrapper as built (WP2) — facts for WP4, WP5, WP7

`PumpBoard(data_dir, mode, store, on_event, on_alarm, ui_age, overrides=None, link_factory=None, port_lister=None)`:
`store` needs `pump_settings()` / `save_pump_settings(dict)`; `on_event(text, level)` (`None` → `store.event(text, level, 'system')`);
`on_alarm(action, key, priority=None, area=None, text=None, kind=None)` with action `'raise'` / `'clear'`; `ui_age()` → seconds since the page last asked
(or `None`); **`set_gate(hv_blocks_motion)` must be called before anything moves** (without a gate every motion command is refused).

| Method | Returns / raises |
|---|---|
| `run(motor, flow_ml_min, direction, limit_s, owner)` | `{motor, sps, steps, until, limit_s}`; may raise `PumpBoardError('stopped', 'Stopped')` when a stop arrives while the motor is being started |
| `stop_motor(motor)` | `{motor, stopped: True, ran_s}`. **Raises `PumpBoardError('device', '{motor} The planned operation time has been exceeded and has automatically stopped.')` when a round's run had already run out before this `pump_off` arrived** — the step fails, as B.3.7-4 wants |
| `dose` / `dose_weight` / `clean` / `syringe_init` / `syringe_speed` / `syringe_move` / `scale_read` / `scale_tare` / `scale_calibrate` | `{steps, sps, eta_s, elapsed_s}` / V7's result keys / `{started, total_pump_timeline_s, elapsed_s}` / `{initialised, speed_ml_s}` / `{speed_ml_s}` / `{position, volume_ul, speed_ml_s, contents_ul}` / `{grams, samples, stable}` / `{calibration: {divisor, offset, reference_grams, saved_at}}` |
| `quiescent(fresh=True)` | `(ok, [{code, text, fix}])`; codes `pump_link`, `pump_uncertain`, `cleaning`, `pump_moving`, `syringe_not_ready` |
| `stop_all(reason)` | `{sent, confirmed, error}` |
| `configure(enabled, device=None)` / `reconnect()` / `apply_settings(syringe_ul, syringe_speed_ml_s)` | `{saved: True}` / `{accepted: True}` / `{saved: True, settings}`; a wrong request is `ValueError` (→ 400) |
| `ports()` | the shape of API.md C.3.2 |
| `facts()` | the `board` block of a round record (D.2) |
| `view()`, `hardware()`, `deadline_tripped(owner)`, `in_use`, `connected`, `motion_uncertain`, `start()`, `close(budget_s=2)` | as B.13; `hardware()` has the M10 keys |

`PumpBoardError.code`: `busy`, `refused`, `blocked`, `link`, `timeout`, `device`, `stopped`. The scale preconditions raise `blocked` (→ 409 `blocked`).
The board's own page watchdog cannot call `ManualOps.cancel()`: it cancels the board's running operation, which then ends with
`('stopped', <the page_lost text>)` — `ManualOps` must take that as its own end. A timed run that the board has not ended is stopped by the PC
`limit_s × 0.15 + 2 s` after its time (the board finishes its own count of steps first; on the real board that takes a little longer than the clock says).
`pump_link_lost` is raised high only when a round-owned run or operation was active at that moment (the board does not know about schedules).

For the static checks of `tests\test_port_safety.py` (three are skipped until the files exist): `Application(folder).pump.view()['mode'] == 'off'`;
`application.py` contains neither `link_factory` nor `port_lister`, has exactly one `PumpBoard(` call and passes `overrides=` only as
`pump_overrides`; the entry point (`app2.py`) calls `allow_real_ports(` exactly once; no file under `pc\` or `tests\` names a real port (`COM3`).

**Texts changed by M8 (WP7):**

| Where | New text |
|---|---|
| E.6.1, last notice row | “After starting, the pump will stop itself after the set number of seconds (counted by the pump control board). The weighing quantity is still determined by this computer: computer or USB There is no guarantee that it will stop when the line is interrupted. Please prepare a physical stop method. ” |
| E.6.2, dialog “start {motor}”, warning | “When the set seconds are reached, the pump control panel will stop by itself. Please confirm that the pipeline is correct and the physical stop method is available before starting. ” |
| E.6.2, tag of a manual run whose time is over while the board still counts steps | “operation·will stop” (`left_s` ≤ 0) |

Still to be measured on the rig (owner): whether a finite move at the step rate of a continuous run moves the same liquid per second (V7 keeps two
factors, `pulses_per_ml` for `V` and `dose_pulses_per_ml` for `P`, 7.6 % apart; a timed run uses the first, as V7's `motors.run` does), and how much
longer than `steps ÷ speed` a move really takes.

---

## M13. The core as built (WP4) — facts for WP5 and the pages

- **Wiring order**: `Devices(store, alarms, rpi, pump)` sets the pump gate itself; after `Runner` and `ManualOps` exist call `devices.bind(runner, manual)`.
  `PumpBoard.on_alarm` and `RpiLink.on_alarm` must forward to `Alarms` (see `Rig._alarm` in `tests\core_testing.py`).
- `Runner.request_stop() -> {'schedule': bool, 'round': bool}` · `Runner.active` · `Runner.ownership()` (`''` | `'busy'` | `'paused'` | `'paused_round'`) ·
  `Runner.uses_recipe(id)` (for “this Recipe is being used in schedule”) · `Runner.start_calibration(body)` · `confirm_prompt()` · `calibration_view()`.
- `Devices.rpi_connected()`, `.motion_problems(ack)`, `.pumps_moving()`, `.raise_hv_unconfirmed(text, priority)`, `devices.reasons_text()`, `devices.unconfirmed(report)`; `Busy` has `.code`.
- **Errors → HTTP**: `Runner.preview` → `KeyError` for an unknown Recipe (404); `start` → `ValueError` “Recipe does not exist” (400); `ValueError` 400;
  `RuntimeError` 409 `state`; `Blocked` 409 `blocked` with `.reasons` (texts “Cannot start scheduling now” / “The pump cannot be started now” / “The schedule cannot be continued now” / “Calibration cannot be started now”);
  `Busy` 409 `busy`. `ManualOps.submit` can also raise `PumpBoardError` with code `busy` (→ 409 `busy`) or `stopped` (→ 409 `state`).
- `round_view()` also carries `calibration_id`; `round.current` is present for the whole of an active round (between two steps or paused: the next
  step, its sentence, `started_at` null).
- `state.pump.generation` (new): a number that grows with every new connection to the board and every restart of it. `RpiLink.connected` (new property).
- The blank body also accepts `{enabled: false}` (= no blank). A schedule with one slot skips the interval rule.
- `result_text` uses `.3g` (0.300 shows as 0.3); a calibration round's row reads “Calibration · Completed n/N Wheel”.
- **Added after the first report (2026-10-06 evening):**
  - `Devices.close()` ends the thread `devices-watch` (started by the first `bind()`): every 0.5 s it checks that no pump moves while the high
    voltage is **known and not idle** without a session of this program; if so → round stop, `manual.cancel()`, `pump.stop_all('hv')`, alarm
    `pump_with_hv` (Emergency, pump, `event`) “High voltage is displayed as ON,But the pump is still running: All pumps have been stopped. Please go to the site to confirm. ” (or, when that stop is not confirmed,
    “High voltage is displayed as ON,But the pump is still moving: the pump stop command was not acknowledged. Immediately turn off power to the pump using the physical switch. ”).
  - The `hv_off_checked` tick is a dated object (`devices.tick()` → `devices.Tick`); `ack` arguments take `True` (ticked with this very request) or a
    `Tick` kept from a start dialog. A tick is void once the high voltage has been reported ON after it. With a tick that counts, a stuck
    `hv_active` (an unconfirmed stop) no longer blocks pumps while the state cannot be read.
  - **The tick counts whenever the high-voltage state is unknown — also while the RPi is connected** (status too old, read-back invalid: the usual
    state of the rig when the supply is switched off at its mains switch). `interlock.manual.need_hv_ack` and the preview's `needs.hv_off_checked`
    are true then; both objects carry **`hv_ack_text`**, the sentence of the tick row: RPi not connected → “RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. ”;
    connected but unknown → “Unable to read the status of the high-voltage power supply ({reason}) ,The program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. ” The. **Pages and the frame show `hv_ack_text` in the tick row**
    (not a text of their own). Known and not idle: refused, the tick is ignored.
  - An `unreachable` RPi half of STOP is Emergency only when a session of ours is open, a round with RPi steps is active, or the RPi's last known word
    was "not idle"; otherwise middle. A stopped pump-only round under a valid tick sends no RPi half and stays `stopped`.
  - `Runner.round_uses_rpi()`.
  - `/api/stop`: `rpi.result` may also be **`silent`** — the supply does not answer, the Nano is confirmed quiet, and nothing of this program
    was running (the usual state when the supply is switched off at its mains switch). Alarm `hv_stop_unconfirmed` is then middle with
    “The high voltage power supply does not respond and the program cannot confirm that it is off;Nano Confirmed no output. If the power is turned off from the switch, this is normal. ”, and a stopped round stays `stopped`. The footer says
    “Stopped: The pump has stopped; the high-voltage power supply does not respond (Nano Confirmed no output)” (`labels.stopMessage`). `rpi.result` `none` = the RPi half was not sent.
  - `RpiLink.hv_view()`: a device that answers and reads ON decides before the row “high voltage power supply/Nano is invalid” Security fix for (a silent supply with a pulsing
    Nano is **known, not idle**), so a tick can never overrule a reading of ON.

## M14. How long STOP waits for the RPi (devices.py) — from a measurement on the rig, 2026-10-07

- Measured 01:38 on the real RPi (2.0.0, nothing running, the supply switched off at its mains, the Nano idle): `status fresh` takes
  2.0 s; **`stop` takes 10.35 s** and answers `{"ok": false, "code": "tool_failed", "error": "Timed out waiting for HSPY status"}` (twice,
  the same). That is the verified tool retrying HSPY OFF on a supply that does not answer; the server reads the equipment once more at
  the end of every stop, so the plain `status` that follows is newer than the stop.
- SPEC B.4 step 4 ("join both for 8 s") would therefore give `pending` — footer “⚠ Stop command not confirmed:RPi — Please use the physical switch to cut off the power.”, event
  level error — every time STOP is pressed in the usual state of the rig, before `silent` (M13) arrives 2.4 s later.
- As built: the pump half is waited for `STOP_JOIN_SECONDS` = 8 s as before. The RPi half is waited for 8 s when `serious` or `ours`
  (a discharge of ours open or not read back, a round with an RPi step, an RPi high-voltage job, the last word "not idle", no status
  at all), otherwise for `STOP_QUIET_JOIN_SECONDS` = 22 s. `/api/stop` then returns after about 10.5 s with `silent`; the frame shows
  “Stop command has been sent, waiting for readback...” meanwhile and waits 30 s for the reply. The pump board stops within its own second either way.
- Why 22 s and not 15: STOP pressed a second time while the first is on its way. The RPi takes one stop at a time (its bus), waits
  8 s for the bus and then answers "busy"; so a second stop asked within 2.3 s is answered at about 12.4 s (busy, then a fresh
  reading: `silent`), and one asked later runs after the first and is answered up to 18.4 s after it was asked.
- Not changed: an RPi that cannot be reached answers within the 5 s of the connection attempt; `Application.close()` has its own limits.

## M15. The Mega's firmware is this project's (p1), the board is found by itself, two guides — 2026-10-07

The user, 2026-10-07 02:35: “Mega can be changed, and the burning is directly done by you. Now there is a connection to The firmware ofmega,Don’t let me chooseCOM3,should be marked directlyCOM3? The interface must be intuitive and concise.
Give me a new descriptionHTML,There is only necessary information in it, not too much, and everything is organized into a folder on the desktop.”. This overrules SPEC B.2 ("the firmware is not
changed"), B.3.1/E.11 ("the port is chosen once") and M12 where they say otherwise.

**M15.1 Firmware (`mega/`).** `mega/tools/make_firmware.py` generates `mega/source/PlasmicMega/PlasmicMega.ino` from the vendored V7
source by exact-once substitutions (V7's commands are untouched; five of its lines are extended); `build_firmware.py` compiles with the
Arduino IDE of the PC; `flash_mega.py` writes, verifies and asks `?` / `G,?`. The board was read out first (`mega/backup/`, identical to
a fresh build of V7's source). Tag `p1`, written to the rig's board 2026-10-07 03:48. What it adds:

| line | answer | meaning |
|---|---|---|
| `GP,<fast>,<approach>,<trim sps>,<min gain mg>,<settle ms>,<no gain ms>,<limit ms>` | `OK,dose_profile` / `ERR,arg` | speeds and times of weight doses; needed once after every restart. 0 < trim ≤ approach ≤ fast ≤ 8000; min gain 1–100000; settle 0–60000; no gain 2000–600000; limit 2000–1800000 |
| `GN,<baseline n>,<final n>,<ms without a reading>` | `OK,dose_samples` / `ERR,arg` | 1–9, 1–9, 500–30000; 5, 7, 4000 when never sent |
| `G,<motor>,<target mg>,<slow zone mg>,<trim zone mg>,<tolerance mg>` | `OK,dose_started` + STATE, or `ERR,arg` · `ERR,dose_profile` · `ERR,cleaning_active` · `ERR,busy` (any motor not idle) · `ERR,scale_uncalibrated` (no `W,DIV`/`W,CAL` since the restart) · `ERR,scale_timeout` | the board doses by weight by itself |
| `G,?` | `GSTAT,active=,phase=,result=,motor=,target=,gain=,baseline=,topups=,stable=,ms=` | phase: idle baseline priming coarse approach trim settle final topup; result: none done cancelled timeout no_gain scale |
| `K,<ms>` | `OK,watchdog_ms=<ms>` / `ERR,arg` | 0 (off, the state after a restart) or 2000–60000: no line from the PC for that long stops the open-ended runs (`V`), not `P`, not the cleaning, not a dose |

The dose is V7's `motors.dose_by_weight` as a state machine fed by every good scale reading: baseline (median) → fast → approach inside
the slow zone → trim inside the trim zone → stop at target − tolerance → settle → final weight (median) → top up at trim speed while
below. It ends by itself on: done, the time limit, no gain for too long, three failed readings in a row or no reading for the GN time,
`X`, `V,<its motor>,0`. While it runs only `?`, `X`, `G,?`, `K`, `W,READ`, `W,CFG`, `Y,STOP` (sent on, not waited for) and `V,<any>,0`
are taken; everything else gets `ERR,dose_active`. Unsolicited lines never start with OK/ERR/DONE: `DOSE,phase=…,gain=…` (followed by a
STATE line), `DOSE,end,result=…,gain=…,target=…,topups=…,ms=…`, `WATCHDOG,host_silent,ms=…`. STATE ends with `,fw=p1,wd=<ms>,dose=<0|1>`
and the READY banner with `,fw=p1`. V7's own program works with it unchanged.

**No line longer than 62 characters** (`pumpboard.LINE_MAX`): the Mega's receive buffer is 64 bytes and the firmware does not read while
it talks to the scale (about 21 ms, ten times a second); a 64-character line lost its tail about one time in six (found by the review of
2026-10-07, before anything was flashed). That is why the numbers of a dose come in three lines.

**M15.2 The wrapper (`pc/pumpboard.py`).** `_caps(STATE)`: `fw=p<n>` → `{'tag', 'dose', 'watchdog'}`; `view()['firmware_tag']` and
`facts()['firmware_tag']` ('' = V7's firmware). With `dose`: `dose_weight` sends GP, GN, G and asks `G,?` every 0.5 s
(`_dose_weight_on_board`); nothing is endless, the guard is not armed; this PC gives up `BOARD_DOSE_EXTRA_S` (30 s) after the board's
own limit. Without it: V7's host loop and `pump_guard.py`, as before. With `watchdog`: `K,10000` at the end of the safe-state sequence
(the heartbeat `?` comes every 2 s) and `K,0` on a clean close. New device errors: `ERR,dose_active`, `ERR,busy`,
`ERR,scale_uncalibrated`, `ERR,dose_profile`.

**M15.3 The model (`pc/megasim.py`).** A subclass of V7's `SimulatedMega` with the additions written as the firmware has them; the
practice mode and the core tests use it (`pumpboard.SIM_FIRMWARE = 'plasmic'`, `megasim.use(v7, link)`); `pump_testing.Rig` keeps V7's
own simulator unless `sim_firmware='plasmic'`. `FaultPlan` wraps its `_handle` too. **When the firmware changes, the model and
`tests/test_megasim.py` change with it** (one test checks that the source in `mega/source` is what the generator makes, another the
SHA-256 of the build that was flashed).

**M15.4 Nobody chooses the port.** `pumpboard.json` `enabled: null` no longer means "ask": the one Arduino Mega of the PC (USB ids
2341:0042 / 2A03:0042) is taken by itself (`_adopt`, `chosen_by: 'auto'`, event “Locate the pump control board:COMx (…) ,has been automatically set”) and connected; none → link `unset`,
text “Pump control board not found: please connect USB line” (looked for again every `RELIST_S`); several → `unset`, “This computer has more than one connection Arduino Mega: Please go to ”Maintenance › connection”
Choose a piece”. A board taken by itself that disappears is replaced by the only other Mega; one chosen by hand (`configure`, `chosen_by:
'user'`) never is. Nothing is taken by a process that may not open real ports. Texts: `interlock` reason `pump_unset` = “Pump control board not found”
/ “Please connect the USB line; after connecting, the program will connect itself.”; `pump_disabled` fix = “Ask maintenance personnel to go to ”Repair › connection ”Change the pump control board back to use”.
Pages: the top bar says “pump control board COM3” when connected (`labels.pumpChip`), “not connected” for `unset`; Maintenance › connection shows “COM3 = pump control board (Arduino Mega
2560) ” (`labels.pumpPort`), one line “firmware” (“Plasmic p1 (The weighing quantity is determined by the board itself)” / “v7 original version (weighing and quantification are checked by computer)” / “is not a pump control board”, plus “⚠ Settings and PC
difference in”), and the button “Change one piece/Not used” instead of “Select port”. No file under `pc/` or `tests/` names the rig's real port
(`test_port_safety`): comments say COMx.

**M15.5 Two guides.** `GET /guide` serves `guide.html` (short: start, safety, the first check list, daily use, trouble, state);
`GET /docs/detailed-guide.html` (percent-encoded; where the short one's relative links lead) serves the complete one. Both are built by
`docs/guide-source/build_guide.py` from `guide.src.html` and `installation-and-usage.src.html`. The messages of the programs that name steps
(“Steps 1”, “Steps 3 Connect to the Internet”, “References › …”) mean the complete one.

**Not verified on the rig:** a pump has not turned on this firmware. Done on the real board: write and verify, banner and STATE with
`fw=p1`, both groups idle, valves closed, and the answers of `K`, `GP`, `GN`, `G,?`, `W,CFG` (accepted and refused as expected). The
scale module did not answer at that time.

## M16. Pump 1–8, several pumps at once, the flow on the schedule page, a calibration per pump, a wide touch screen — 2026-10-07

The user, 2026-10-07 11:55 (after the first start of 2.0.0 on the rig): “Scheduling should also be selectablepumpand set the transferml,To setpump 1~8,andpumpshould be an option now
1. Fixed rotation amount(showml),and multiple can be transferred together(In addition to emptying) 2. Use weight module to stop, there can only be one at the same time No answers forpumptransfer It should be included in the maintenancepump calibration,beat10mlI tell the system the real
How much to hitmlis calibrated, and eachpumpcan be calibrated individually, and the weight module must also be calibrated. If you have any questions, please ask me first before continuing.UITo make everything touch-sensitive, the current interface is square, it should
is made to fit the screen as much as possible”. Asked back, he chose: pumps named in the order of the wiring; at most two at once (one per driver group), the rest
of a group queued; a round is “Add liquid / Measure / Empty / Cleaning; the screen opens in full screen. This overrules SPEC and the earlier amendments
where they say otherwise. **No firmware change**: every dose is the board's own finite move `P`.

**M16.1 Names.** People count the pumps **Pump 1–8** in the order of the YAML's channels (pin 2 = Pump 1 · water … pin 8 = Pump 7 · OUT,
pin 9 = Pump 8 · CuSO₄). `PumpBoard.hardware()` gives every motor `pump` (1–8), `short` (“Pump 7”), `label` (“Pump 7 · OUT”),
`dose_pulses_per_ml` and `factory_pulses_per_ml`; the ids of the API, of the board and of V7 stay the pins (2–9). Every sentence of the
program names a pump by its label; V7's own sentences (“motor 8 …”) are rewritten on the way out (`PumpBoard._words`). In a Recipe a
pump is written **`P1`–`P8`**; `M2`–`M9` (the pin) is still read. A validated step carries `motor` (the pin) — `steps.py` uses that.

**M16.2 The step `dose_together` (Simultaneous quantitative).** Row: target `P1+P8`, value `10+2`, unit `mL` — two to eight different pumps, one amount
each. Refusals: “No. N column:{Name} needs to write more than two different pumps, use + Connect together (example: P1+P8) ” · “No. N column:{Name} should be the same as the pump, use Nothing is opened when + Connect together (example: 10+2) ” ·
“No. N column:{motor} is a pump for emptying and cannot be used for dosing with other pumps.” (the board's `cleaning.out_motor`; the person's rule: several may turn together,
the one that empties the cell alone), then the range and the step of `dose_volume`. Sentence “Simultaneous quantitative:…” with one “{motor} n mL” per pump. The step carries `motors` (pins) and
`amounts`. R5 applies (every continuous pump stopped first); R3 does not: `PumpBoard.dose_many([(motor, mL), …], owner, abort)` runs the
pumps of different driver groups side by side and those of one group one after the other, each as V7's `motors.dose`; it waits until
all are done and their linked valves closed; a stop, or “OFF” on one of them, ends all (X). Limit per pump: ETA × 2.5 + 10 s (two finite
moves at once make the board compute both — each may turn slower; the step count, so the volume, does not change). Estimate: per driver
group the sum of its doses, of the groups the longest.

**M16.3 Phases.** Add liquid (was Liquid delivery) · Measure · **Empty** = a pump step of the board's OUT pump (`cleaning.out_motor`) · Clean · Turn off high pressure · Wait.

**M16.4 The flow of the schedule page.** `GET /api/flow` → `{flow, recipe_id, name, pumps, weighing, clean_seconds, dose_flow_ml_min,
limits}`; `POST /api/flow {flow}` (no PIN; refused while a schedule uses it: “Scheduling is in progress: the process cannot be modified until it is stopped.”) → `{recipe_id, name, flow, steps,
warnings, estimate_seconds}`. `flow` = `{fill: [{pump, mode: 'ml' | 'g', amount}], measure, drain: {on, ml}, clean}`.
`recipe.flow_csv()` turns it into Recipe rows — the pumps that stop on the scale first, one after the other (note “Add liquid (stop with scale)”), then
the pumps with a volume as one `dose_volume` / `dose_together` (note “Add liquid”), `plasma_run`, a `dose_volume` of the OUT pump (note
“empty”), `clean` (note “Cleaning) — and the result is stored as the Recipe with the fixed id `store.FLOW_RECIPE_ID` (source `flow`, name
“Process: Add liquid → Measure → Empty → Cleaning with the stages that are on). The schedule page starts it like any other Recipe; nothing else in the
runner knows about flows. Refusals of a flow: “The content format of the process is wrong” · ”All four stages are closed: at least one must be turned on” · ”The pump for adding liquid is wrong:N” · “Pump N selected twice” ·
“Pump N is a pump for emptying and cannot be used for adding liquid.” · “{motor} The quantity should be within a–b u , the smallest unit s” · “profile does not have a pump for emptying” ·
“program folder is incomplete (missing pc\vendor) ,Unable to set pump flow.”.

**M16.5 A calibration per pump.** V7 reads a channel's own `dose_pulses_per_ml` when it has one; the wrapper puts the saved ones there
(`pumpboard.json` → `pump_calibration: {"<pin>": {dose_pulses_per_ml, nominal_ml, measured_ml, before, saved_at}}`), in the hardware view and
in the live connection, without touching `pc/vendor`. The speed factor `pulses_per_ml` stays V7's, the same for every pump: a calibration
changes how many steps a volume is, not how fast the pump turns. `GET /api/pump/calibration` → `{pumps: [{motor, pump, label,
dose_pulses_per_ml, factory_pulses_per_ml, calibrated, nominal_ml, measured_ml, saved_at}], nominal_ml: 10}`;
`POST /api/pump/calibrate {motor, measured_ml, nominal_ml?}` (PIN): new factor = factor × nominal ÷ measured, refused unless measured is
within 0.5–2 × nominal; `POST /api/pump/calibrate/reset {motor}` (PIN). The 10 mL themselves are an ordinary manual dose, pressed by the
person. A round record's `board.pump_dose_pulses_per_ml` holds the factors of the calibrated pumps.

**M16.6 The frame.** Logical height 768 as before; the logical width follows the window (1024 for 4:3 up to 1680; 1365 on the rig's
1280×720), so the pages fill a wide screen; controls are at least 44 px for a finger; text fields have an on-screen keyboard; the page
asks once for full screen (Fullscreen API) and has a button to leave it. Details: `WEB-FRAME-NOTES.md`.

## M17. A second start, the silent supply in plain words, the plasma parameters on the schedule page — 2026-10-07

From the person's first hour with the real screen (work log, section 18). His words: “Why can’t I start scheduling? And why not?ontime /
offtime / cycle / integration time...settings” and, with a picture of the port sentence, “And after closing it and then opening it again immediately, this will be displayed.”.

**M17.1 A second start (`app.py`).** The port still tells two copies apart, and the second one touches nothing before it has the port
(`claim_port`). But it asks the holder what it is doing (`port_holder`: `GET /api/bootstrap`, never through a proxy):

- **closing** — the window of the first copy was closed and it is switching everything off (Windows ends it within five seconds). Its
  console handler tells its web server first (`server.RequestHandlerClass = httpd.Closing`: every request → 503
  `{"error": "Program is ending", "code": "closing"}`). The second copy prints “Previous Plasmic The electronic control is ending (stop the pump first and turn off the high voltage). Wait for it for a few seconds...” once, waits
  up to `PORT_WAIT_S` = 10 s for the port, and then starts as usual.
- **starting** (503 `starting` of `httpd.NotReady`) — “another Plasmic The electronic control is starting: the operation screen will open automatically after a while. This window can be closed. ”, exit 0.
- **running, the same mode** — “Plasmic The electronic control is already on (official mode), and there is no need to turn on another one. Operation screen:{url}” (or “Practice Mode”) and
  “This window can be closed; the original black window is the program itself. ”; the browser is opened at it (not with `--no-browser`); exit 0.
- **running, the other mode** — “Practice mode is still on, it uses the same port {port}.Please close its black window and open it again. ” (or “Official mode is still on...”).
- **anything else**, or a copy that is still there after the wait — the sentence there always was, after `PORT_GRACE_S` = 2 s:
  “port {port} is already used by another program (maybe a Plasmic electronic control). Please close it first. ”.

**M17.2 Why a schedule cannot start, in plain words (`rpilink.rewrite_reason`).** The RPi's reason “HSPY Power readback is invalid:…” — the supply
does not answer: it is switched off at its mains switch, the usual state of the rig between measurements — is shown as
a plain sentence, and its fix (`rewrite_fix`, a sixth replacement) as one that says what to do — both as M18 words them now. Used by
`Devices.start_problems` (the footer and the start dialog) and by `RpiLink.rpi_ready` (a slot that could not start). The RPi side is not
changed; Overview and the high-voltage view of B.4 keep their words.

**M17.3 The plasma parameters on the schedule page.** The person's answer to the question where they belong: “The schedule page can be modified directly without PIN”.
`set_parameters` moved from `PIN_ACTIONS` to `OPEN_ACTIONS`; `POST /api/parameters` keeps the busy rule (it is the one busy route that is
no maintenance action). `pc\web\js\plasma.js` holds the table of the seven parameters for both pages, `summary(values)`
(“30 V · ON 25/OFF 5 ms × 25 · 5 wheel · integral 1000 ms · interval 1500 ms”), `inWords(sentence)` (the RPi names a parameter by its key; the screen
by its label) and `edit(ctx)`: one form dialog “Plasma parameters: click an item to change”, “Save and sync”, with the line “Storage will not activate high voltage; these values will be used for each subsequent measurement.
 (Voltage upper limit N V) .After changing the parameters, the concentration calibration done before is no longer accurate and needs to be recalibrated. ”; what the RPi refuses is shown under “The plasma parameters are not saved”.
Schedule measurement: the tile measurement content is a button that says the summary and opens the dialog; with the four stages of M16.4 the dialog of ② Measure has
a third choice “Change plasma parameters”. Maintenance › plasma parameters stays as it is (the page still needs the login; “Clear Nano Fault” and the other tools of
`/api/rpi/tool` keep their PIN). Sentences that named a Recipe where the tile is called Process since M16: “Please go to the ”Scheduling Measurement” selection process first. ·
“The selected process no longer exists: please go to ”Scheduling Measurement” and select again” · ”The process has not been selected yet: please go to ”Scheduling Measurement” and click ”Process””.

## M18. “Recheck”: a reading taken now, said in plain words — 2026-10-07

The person had switched the supply on and the screen still said that it did not answer. His words: “I have high voltage power on now(There is no output when the main power is turned on),
But I haven’t read it yet. There should be a button to turn it on and check whether it is turned on now.”. (It really did not answer: work log, section 19.)

- `POST /api/rpi/check {}` — no PIN, no busy rule, read-only: the program sends the RPi `status` with `fresh` (the RPi reads the supply
  and the Nano at once; nothing is switched, written or started) and answers `rpilink.check_view(status)`:
  `{ok, busy, rows: [{name, ok, text}, {name, ok, text}], advice}` — the rows are “high voltage power supply (HSPY) ” and “Nano controller”; `ok` = both answer.
  Not connected → 503 `rpi_link`. One event per press: “Recheck the equipment: high voltage power supply (HSPY)  …; Nano controller …” (`warn` when one is silent).
- Row texts: “has response: output Is OFF,0 V (settings 30 V) ” (or “output ON”) · “has a response: Standby” (the Nano's state in the words of the screen) ·
  “responded: fault (HEARTBEAT_LOST) ” · “No response” · ”Something came back, but I can’t read it (…)”.
- Advice: “high voltage power supply and Nano all responded. ” (with “cannot be discharged yet:…” when the RPi's list `ready.problems` still holds something) ·
  the supply silent and the Nano answering — “Nano has a response, so RPi, RS485 The adapter and the line are connected; the problem is in the high-voltage power supply section. Please see:① Is the panel of the high-voltage power supply illuminated?
  ② Behind it RS485 of A, B Check whether the two wires are loose or connected reversely (then debug if there is still no connection);③ Is the communication address on the panel 1, rate 9600.” ·
  “There is something in the high-voltage power supply, but the content is wrong: most likely A, B Line contact is poor or the speed is not 9600.” · “High voltage power supply responds,Nano None: See Nano power supply,and its RS485 module line. ” ·
  “high voltage power supply and Nano No response: See first RS485 adapter USB plugged in? RPi superior,Look at those two lines again.” · while a job runs
  “RPi Measurement is being performed, and the device cannot be read at this time. Wait for it to finish and press again. ”.
- Screens (`pc\web\js\check.js`, one function `checkNow(api, dialog)`): a button “Recheck” in the footer beside “cannot be started” whenever a
  reason has the code `rpi_not_ready`, `hv_unknown` or `hv_on` (“Checking...” while it runs; the preview is asked again afterwards); and in the
  dialogs of the two status cells “HSPY high voltage power supply” and “Nano controller” of Overview. The result is a dialog titled “Recheck: All responded.” or
  “Result of recheck” with the two rows and the advice. The footer shows a reason without its lead “The pre-discharge inspection failed:” (the footer is short;
  a tap shows the whole sentence).
- The two sentences of M17.2 changed with it, because a supply can be switched on and still not answer: the reason reads
  “high voltage power supply (HSPY) No response: The power is not on, or its RS485 line is not connected properly”, the fix “Turn on the high voltage power supply (HSPY) ’s power switch, and then click ”Recheck”. Already on and still not responding: look behind it
  RS485 of A, B Are the two lines loose or connected reversely? Is the communication address correct? 1, rate 9600”.

## M19. No limit on the supply's current — 2026-10-07

The person's words: “There shouldn’t be0.1Arestriction, delete this restriction directly”.

- `rpi\tcpd\plasmic_tcpd.py`: `MAX_CURRENT_A` is gone. The readiness list no longer has “HSPY current limit setting … A exceeds 0.1 A”; the limits of the
  `hello` reply are `{"max_voltage_v": 700, "max_job_s": 215}` (API.md C.6, `rpi.status.hello.limits`).
- `rpi\tcpd\plasma_measure.py` (the `unified` flow): `prepare` compares the set voltage only (“HSPY Set voltage and parameter.json Does not match”); while the
  voltage rises, the read-back must be valid, ON and at the set voltage (“HSPY ON readback is incorrect”; it was “…or current exceeds limit” with 0.11 A). The output
  current is not judged anywhere.
- The current limit is whatever the panel of the supply is set to (0.999 A on 2026-09-20); the program never writes it and now never
  compares it either.
- **The fall-back flow keeps its two checks**: `rpi\rpi_tools\plasma_job.py` is one of the 22 protected files (set current ≤ 0.1 A before the
  run, output current ≤ 0.11 A during the ramp). It runs only with `"flow": "verified"` in the RPi's `config.json`; with a panel above 0.1 A
  that flow refuses, as before.
- Tests: `test_tcpd` (a set current of 0.5, 0.999 or 3 A is no reason; the module has no `MAX_CURRENT_A`), `test_measure` 81 (+2: the
  current during the ramp is not judged; the current limit set on the supply is not judged; a wrong read-back during the ramp still ends the
  run with everything off).
- On the RPi since 2026-10-07 16:55 (`tcpd` only, no restart of the RPi by the installer; the three files are byte-identical with this folder).

## M20. The pump setup: what each pump carries, water and OUT on any pump, valves only when linked; firmware p2 — 2026-10-07

The person's words: “It should be possible to set each in the administrator modificationpumpis connected to(Orout)and show up, especially water andout,Because water andout”;
then “Shuihe OUT also needs to be able to be replaced with another pump.” and, about the valves, “No, don’t use the solenoid valve directly at first, but you must be able to set the solenoid valve linkage in the administrator settings.”.

### M20.1 The setup

One setting of this program (`pc\data\pumpboard.json`, key `pump_setup`; `pc\vendor` is not touched):

    {"pumps":  [{"motor": 2, "use": "water", "name": "water"}, … one row per pump, motor = pin 2–9 …],
     "valves": [{"id": 1, "motor": null}, {"id": 2, "motor": null}],
     "saved_at": "2026-10-07T17:40:00+08:00"}

- `use` is `water` · `out` · `chemical` (with `name`, 1–16 characters, none of `, ; " ' \ < >`, not “water” or “OUT”) · `none` (nothing
  connected). **Exactly one water and one OUT, one on each driver** (Pump 1–4 and Pump 5–8): they are the two pumps of the cleaning and turn
  together. A valve opens with one pump or with none (`motor: null`); a pump has one valve at most.
- **Nothing saved** (the state of every installation until somebody saves): V7's names (Pump 1 water, Pump 7 OUT, Pump 8 CuSO₄), V7's two
  cleaning pumps, and **no valve in use**.
- The names are what the screens show (`Pump 1 · water`): `bootstrap.hardware.groups[].motors[]` has `fluid`, `label`, the new `use`, and
  `valve` (null unless linked); `hardware.valves[]` is `{id, label: “solenoid valve 1” or “solenoid valve 1 · water”, motor}`; `hardware.cleaning` has the
  two pumps of the setup, and `total_s` counts the valve times only when the water or the OUT pump has a valve.
- `hardware.rev` grows whenever the view changes (a setup or a pump calibration was saved, the board turned out to be one that cannot be
  told); `state.pump.hardware_rev` is the same number. A page that holds another one reads `/api/bootstrap` again (`main.js`), `labels.js`
  is set up from every bootstrap reply, and the two pages that build rows from the hardware (manual operation, Maintenance › Pump and Scale) build them again.

### M20.2 The board (firmware Plasmic p2, `mega\`)

Two commands that only store numbers (nothing moves, no valve switches):

| command | answer | refused |
|---|---|---|
| `CM,<water motor>,<out motor>` | `OK,clean=<water>:<out>` | `ERR,arg` (not two motors 2–9, or both on one driver) · `ERR,cleaning_active` · `ERR,dose_active` |
| `VL,<v2>,<v3>,…,<v9>` (the valve of motor 2 … 9; 0 = none) | `OK,links=<8 digits>` | `ERR,arg` (not eight numbers 0–2, or one valve twice) · `ERR,busy` (a pump turns or waits, or a valve is open or about to close) · `ERR,cleaning_active` · `ERR,dose_active` |

- `STATE` ends with `…,dose=0,clean=2:8,links=10000002`; `READY` ends with `fw=p2`.
- **After every restart the board holds V7's layout** (`hw_config.h`: cleaning 2 and 8, valve 1 with motor 2, valve 2 with motor 9), so a
  program that does not know the two commands (V7's own, a Plasmic before M20) sees the board it expects. This program sends both
  **at the start of every connection, right after `X` and before anything can move**, and checks the two answers; a board that answers
  otherwise is not taken into use (“Pump control board did not respond correctly: The pump control board did not accept the pump settings (CM,2,8 → …) ”).
- In the source: `MOTOR_VALVE[]` lost its `const`; `cleanWaterMotor` / `cleanOutMotor` replace the two macros in `startCleaning()` and
  `serviceCleaning()`. Twelve of V7's lines are changed in all (five by p1), everything else of p2 is added lines. 35,404 bytes.
- `pc\megasim.py` keeps the two settings for itself (a private copy of `cfg['cleaning']` and `cfg['valves']`), as the board does.
- `pumpboard._caps()` has `setup` (true from `fw=p2` on). **A board that cannot be told** (V7's own firmware, p1): nothing is sent, the
  whole layout is V7's (names, cleaning pumps, valves 1↔Pump 1 and 2↔Pump 8) and the screens show that; `GET /api/pump/setup` says
  `editable: false` with the reason, saving is refused, and when a setup had been saved the alarm `pump_setup_ignored` (middle, a condition)
  says what the board does instead.

### M20.3 HTTP

- `GET /api/pump/setup` → `{editable, reason, source: "saved"|"default"|"board", pumps: [{motor, pump, short, label, group, half, use, name,
  valve}], valves: [{id, label, motor}], name_max: 16, names: {water: "water", out: "OUT"}, valve_open_before_ms, valve_close_after_ms}`.
- `POST /api/pump/setup {pumps: [{motor, use, name}] ×8, valves: [{id, motor}] ×2}` — PIN, busy rule; the whole setup at once.
  400 with the sentence for a setup that cannot be; 409 while a pump moves or an operation runs; 409 `refused` on a board that cannot be told.
  The file is written and a connected board is told right after (`CM`, `VL`; the file first, so that a board which restarts in that
  very moment is told the same setup); when the board does not take it the file is put back and the link is made again.
  Reply: the new setup as `GET`, plus `summary` (“water=Pump 2, OUT=Pump 5; Pump 8=CuSO₄; solenoid valve: not used”), `flow`, `recipes_to_check`.
- **The flow of the schedule page** names its drain pump by what the setup says, so it is made again from the saved four stages
  (`flow: "kept"`; `"removed"` when that cannot be, with an event; `""` when there is no flow). A setup that would make a fill pump of the
  flow the OUT pump is refused first: “is available in ”Add Liquid” on the schedule page. Pump n: Please go to ”Schedule Measurement” first and remove it from the liquid filling before setting it to OUT”.
- **Published Recipes name pumps by number and are not changed.** `recipes_to_check` lists the ones that use a pump whose line was
  changed; the page shows the list after saving.

### M20.4 The page (Maintenance › Pump and Scale)

Two buttons in the left panel, each with what is in force in its small line:

- **“What to connect to the pump?”** (“water Pump 1 · OUT Pump 7”) → the eight pumps → one pump → “water” · “OUT (Empty)” · ”Chemical...” (the name,
  on the screen keyboard) · “didn’t answer” → “store” → a confirmation with the changed lines. Choosing water (or OUT) for a pump takes it from
  the pump that had it; when both would then be on the same side, the other one moves to where this one was, so the draft is always a setup
  that can be saved. The pump that is water or OUT cannot be given another line directly (“Choose another one to pick up first”).
- **“solenoid valve linkage”** (“Not used” or “valve 1 follow Pump 1”) → the two valves → one valve → a pump, or “Not used” → “store”. The confirmation warns:
  “The solenoid valve set to ”not in use” will always be closed: if it is really installed on the pipeline, liquid cannot be pumped out of that pipeline. ”
- The read-only facts lost the line “linkage solenoid valve” (it is the second button now); “automatic cleaning keeps its three times.

## M21. A softer face: one style sheet of looks — 2026-10-07

The person's words: “Make the page a little more beautiful”, then “The layout is also more refined.” for “All pages together”.

- `pc\web\css\polish.css`, linked **last** in `index.html`. **Looks only**: corners (8 px panels and the three big buttons of the footer,
  6 px for everything a finger taps, 4 px for tags and marks, 12 px dialogs), softer lines (`--line` #c7cbd0, `--line-soft`, `--edge` for
  what can be tapped), a light floor (`--color-bg` #eceef1) under white panels with a hair of shadow (`--lift`), a tinted strip for a
  panel's title (`.ph`), round status dots in the two chips of the top bar. **No size and no position is set there**: the layout of every
  page and the 44 px of every tap target are what the five files before it say, and the test of the frame's sizes reads `base.css` as before.
- The rule of the status colours is unchanged (colour = “abnormal” or “operation”; red frame and hazard stripes = high voltage; red/Orange/yellow = the
  three alarm priorities). Nothing needs a mouse (`:hover` is still forbidden in every sheet, a test checks it).
- `.panel` has `overflow: hidden` now (a row with a fill must not cover the rounded corner); a row that spans its panel draws its focus
  mark inside (`outline-offset: -3px`). Rows that fill a list from edge to edge (`button.trow`, `.li`, `.seg button`) keep square corners.
- Checked in practice mode on every page and every tab of Maintenance at 1280×720 (frame 1365 wide) and 1024×768: no page and no panel has more
  content than room (`scrollHeight`/`scrollWidth` against `clientHeight`/`clientWidth`), the dialogs fit, and the seven pictures of the
  guides were taken again (`shots_app.py`, which also walks the pump setup of M20 now).
- To go back to the old face: remove the one `<link>` of `polish.css` from `index.html` (and its name from `STYLES` in
  `tests\test_web_static.py`). Nothing else depends on it.

## M22. The supply's set voltage is written by the program — 2026-10-07

The person, with 1000 V on the supply's panel and 30 V in the program, the footer reading “cannot start — parameter.json Voltage 30 V and HSPY
set value 1000.0 V inconsistent”: **“There should be no error message, just useRS485Change it and enter itvoltage”**.

- **The measurement writes it** (`rpi\tcpd\plasma_measure.py`, `prepare()`): after `require_off` has seen the supply OFF with nothing at
  its output, a set voltage that is not the one of the parameters is written (`write_set_voltage`: FC06 to register 0, the value 1–700 V
  that `checked_power_write` allows), read back, and only then does the run go on. The read-back must say: valid, OFF, 0 V, 0 A, set
  voltage = target; anything else ends the run before the spectrometer is started and before anything is switched on, and an OFF is sent
  once more. The report carries `set_voltage_written: {from_v, to_v}`, the timeline a mark of that name, the steps one sentence (“HSPY
  The set voltage was originally 1000 V,Already in OFF The login password of 30 V and read back”). `ramp()` still looks at the set voltage with every reading, as before.
- **Why not the verified tool's own way**: `plasma_job.py`'s `set_voltage_off` writes nothing when the set voltage it finds is above 700 V
  or not a whole number (it wants to be able to put it back) — with 1000.0 V on the panel not even “Save and sync” could work. Nothing is put
  back here: the supply is OFF, and the caller ends what it was doing when the write fails. `plasma_job.py` (the fall-back flow
  `verified`, one of the protected files) is unchanged and still refuses a set voltage that differs.
- **The same write on its own**: `plasma_measure.py --set-voltage N --execute` (no `--outdir`, no high voltage; prints `{valid, changed,
  previous_set_voltage_v, set_voltage_v, hspy}`). The server runs it for the new command **`sync_voltage`** (token as every command; refused
  while a job runs; the voltage is the one of `parameter.json`, nothing is passed in) and **before the verified tool in `set_parameters`**
  (first the supply, then the file: the verified tool then finds the new voltage there already). A server without `plasma_measure.py`
  (`flow_errors['unified']`) saves as it always did and refuses `sync_voltage`.
- **Readiness**: `ready_problems(snapshot, age, flow)` names the difference only in the `verified` flow. In `unified` a supply whose
  panel says another voltage is ready.
- **The PC writes it when it sees it** (`rpilink.RpiLink._sync_voltage`, called with every status while no job runs, like the Nano's
  automatic sync and switched off by the same `auto_sync`): supply valid, OFF, 0 V, 0 A; parameters valid; wanted 1–700 V; the two numbers
  differ → `sync_voltage`, once for each pair of numbers (again when either changes; after a `busy` it is tried again). Events: “high voltage power supply
  The set voltage on the panel was originally 1000 V,Used RS485 changed to 30 V (The power is off and there is no output)”; a refusal is said once as a warning (“…is not automatically changed to…. Program meeting before discharge
  Write it again; if it cannot be written in, the high voltage will not be turned on.”); an RPi that does not know the command is told to be updated. `rewrite_reason` says the remaining
  reason of an older server or the fall-back flow as “high-voltage power supply panel is 1000 V,program is 30 V”, its fix as “Wait a few seconds and the program will change it by itself...”.
- The simulator does the same (`SimBackend.write_set_voltage`; its unified run writes the voltage, its verified run refuses).
- The software limit is unchanged: nothing above 700 V is ever written, whatever the panel says.
- **Not verified on the rig**: whether the real supply takes the write at 1000 V on its panel and shows the new number. The tests run the
  real `plasma_measure.py` and the verified module against the pretend supply (panel at 1000, 50, 701 and 0 V; a supply that answers the
  write and keeps its number; a supply that does not answer the write).

## M23. The flow of the schedule page is a list of steps — 2026-10-07

The person: **“should be able to set the process, such as a calibration line ex: Call firstpump 1: 5ml,pump 2: 25ml,Exploitpump 3Empty to another tank and make plasma When(10Zhang Pupu),pump 4Let him leave
plasma tank, cleaning...and the like It should be possible to set the steps1, Steps2...,allows me to write it down myself and also include the syringe pump in the step options.”**, and of the tile that read “Pump 1 5 mL”:
**“should bePump 1: 5mlinstead of15ml”**. Asked the same evening, he chose: the number of spectra of a discharge is the one of the plasma
parameters for the whole flow (not one per step), and a calibration line is written as steps for now (the regression stays with Correction).
This replaces the four stages of M16.4 (the rest of M16 stands).

- **A step is one row of a Recipe** (`[action, target, value, unit, note]`, strings). The page offers `recipe.FLOW_ACTIONS`:
  `dose_volume` (“pump: How many times mL”, any pump, also the OUT pump: filling, moving to another vessel, draining), `dose_together` (“Use two pumps at the same time”),
  `dose_weight` (“Pump: Stop with scale”), `plasma_run` (“Plasma＋Take Spectrum”), `clean` (“automatic cleaning), `delay` (“Waiting”), and under “syringe pump”:
  `syringe_aspirate` (Liquid extraction), `syringe_dispense` (Liquid injection), `syringe_init` (initialization), `syringe_speed` (speed). At most `FLOW_STEPS_MAX` = 60
  steps. Everything else of the Recipe language (continuous runs, repeats, `power_off`…) stays with Maintenance › Recipe.
- **Kept as it stands, also unfinished.** `POST /api/flow {steps}` refuses only what is not a step list (shape, more than 60, an action
  the page does not offer: 400). Whatever else — a syringe filled and not emptied yet, a pump that is not there — is stored: `flow.json`
  (`{version: 2, flow: {steps}}`) and the Recipe with the fixed id, whose check result then says not valid. The schedule preview, the
  start and the slot refuse such a Recipe with its own sentence, as they do for any Recipe. A person writes one step at a time and never
  loses one.
- **In the words of the page**: `recipe.as_flow()` / `flow_words()` turn “No. 6 column” (the CSV row; the header is row 1) into “Steps 5” and
  the names of actions into the names of the steps. Applied where the Recipe with the fixed id is checked: `Application._recipe_entry`,
  `Application.flow`, `Runner._checked`.
- **`GET /api/flow`** → `{steps, view: [{text, ok}] (one per step), valid, error, error_step, warnings, estimate_seconds, uses_plasma,
  uses_syringe, recipe_id, name, pumps: [{pump, motor, label, short, group, group_label, drain, water}], weighing, clean_seconds,
  dose_flow_ml_min, valve_positions, limits: {action: {min, max, decimals, unit}}, max_steps}`. `POST` answers the same. Nothing saved
  yet: one step, the discharge. The Recipe's name is “Process: Set by yourself (n step)”.
- **An older file** (`{version: 1, flow: {fill, measure, drain, clean}}`, M16.4) is read as steps (`recipe.flow_stage_steps`, which is
  `flow_csv` read back) and becomes a file of today at the next save. `flow_csv` / `flow_default` are kept for that.
- **“What to connect to the pump?” (M20)**: the steps name their pumps by number, as every Recipe does — the flow no longer follows the OUT pump. After a
  change the Recipe is checked again with the new layout (`_flow_again` → `'kept'` or `'open'`), and “Schedule page process” is among
  `recipes_to_check` when it uses a changed pump or no longer passes (so is any Recipe that no longer passes). The refusal of M20.3
  (“is available in ”Add Liquid” on the schedule page. Pump n…”) is gone.
- **The page** (`pc\web\js\pages\schedule.js`, `pc\web\js\flowsteps.js`): under Process one tile “Steps” (128 px: its steps ①②③… on up to four
  lines, then “common n Step · Click here to modify” The or “is not available yet:…” with the dashed frame). A tap opens the list: one row per step (“Steps 3 Pump 3: 30 mL”,
  the note under it), eight to a page, the step touched last marked; “＋ New step”, “Plasma Parameters” (when a step discharges), “Completed”. A tap on a
  step: Revise (or Change plasma parameters) · Remarks · Move forward · Move back · Insert a step before it · Copy this step · Delete this step. **Every change is saved at once.** A first
  move of the syringe gets a “Syringe Pump: Initialization” step in front of it by itself (it can be removed; the check then says so).
- **A pump and its amount are never side by side**: “Pump 1: 5 mL” everywhere — the step texts of the page, `S_TOGETHER_PART`, the
  operation text of `PumpBoard.dose_many`, “” of the schedule page, the start dialog and Maintenance › Recipe. A test reads every script
  for a label, a space and an amount.
- `dialog.choose` takes `left: true` (the choices read as a list).
- **Only on the simulators**: the list was written, changed and run in practice mode (two pumps, a move, the discharge, a drain; the
  syringe steps were checked by the Recipe rules and by the pump board model). What the valve positions of the syringe lead to on this
  rig the program does not know.
