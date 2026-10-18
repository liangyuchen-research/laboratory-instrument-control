# Plasmic Electronic control 2.0.0 — final specification (SPEC)

Written 2026-10-06. Design only: nothing in the three project folders was changed, nothing was run, no port was opened,
no network connection was made. Companion files in this folder: `API.md` (section C — the HTTP contract, single source of
truth for back end, front end and tests) and `WORKPACKAGES.md` (section H — the build plan).

Abbreviations: **EC** = `C:\Users\PlasmaLab\Desktop\autonomous control` · **V7** = `C:\Users\PlasmaLab\Desktop\independent laboratoryv7` ·
**HMI** = `C:\Users\PlasmaLab\Desktop\UI\UI\hmi_v1_1` · **board** = the Arduino Mega pump board · **RPi** = the plasma side.
`[FACT file:line]` = read in the code for this document. `[INFER]` = follows from code plus general knowledge, unproven
until the owner tries it (section J).

---

## 0. The design in one page

```
Browser (one page, 1024x768 frame scaled to the window, polls /api/state every 1 s)
   │ HTTP 127.0.0.1:8766 (Host / Origin / X-Token checks, CSP default-src 'self', PIN session in X-Unlock)
pc\app.py ── Application
   ├─ Store / Alarms / PinLock      disk under pc\data (atomic JSON, JSONL logs)
   ├─ RpiLink (was app.Link)        TCP 8767 to the RPi, protocol unchanged, STOP over a fresh connection
   ├─ PumpBoard (NEW wrapper)       the only importer of the vendored V7 code, pyserial, PyYAML
   │     └─ vendored V7 (byte-identical): transport.Link + registry + functions.{motors,pump,scale} + simulator
   ├─ Devices (NEW)                 one owner at a time, pump / high-voltage mutual exclusion, STOP = everything off
   ├─ Runner (NEW, replaces Schedule)  slots -> one Recipe round each -> steps; pause / resume / stop
   └─ ManualOps (NEW)               one manual operation at a time, tied to the page watchdog
pc\pump_guard.py (child process, real mode only)   program dies while a pump runs without end -> reopen the port, send X
```

### 0.1 Which proposal is the base

The two judges chose different bases (safety-backend / operator-ui) but asked for the same grafts in both directions.
Decision: **the back end (B, C, D, G) is safety-backend's; the screen (E) is operator-ui's; the Recipe grammar for
continuous pumping, the "old RPi program" refusal, the launcher handling and the test-porting strategy are lean-integration's.**
Reason: every hole the judges verified in the device layer (partial reply taken as success, 60 s lock behind `Y,INIT`,
no `X` on first connect, pumps allowed with unknown high voltage) is closed only in safety-backend, and every UI point
they praised (original frame and class names, per-pump rows, one way to do each thing, all texts written) is only in
operator-ui. Nothing below needs a further decision by an implementer.

### 0.2 Points where the judges disagreed — rulings

| Topic | Ruling | Why |
|---|---|---|
| V7 config at run time (generated YAML + reload vs. changing the cached dict) | **Reload the vendored YAML with `config.get_config(reload=True)` before every connection, then change the fresh dict in memory.** No runtime YAML file | `get_config(reload=True)` re-reads and re-validates `[FACT config.py:234-239]`; `Link.__init__` keeps the dict it got `[FACT transport.py:80-83]`. So every link has its own config object (judge 1's point) and no file is generated or re-serialised (judge 2's point). Tests put overrides into the fresh dict |
| Alarms (persisted vs. evaluated in memory) | **Server-side, persisted in `alarms.json`** | costs one small file; an unattended rig still shows the unacknowledged alarm after a night restart, and an acknowledgement survives |
| Guard process arming | **Armed only while motion without an end is active** (continuous run, weight dose); otherwise it exits without touching the port | judge 2's refinement: no second program opens the port at an ordinary unclean exit |
| Syringe speed | **Saved default (0.4 mL/s) in Maintenance › Pump and Scale, applied after every initialise; `syringe_speed` is an optional Recipe row; no tile on manual operation** | a Recipe never fails for a missing row; one setting place plus the Recipe ("All conditions should be settable"); fewer controls on the operator page |
| Run-time / dose caps as settings | **Constants, shown read-only**: continuous run ≤ 300 s; dose 0.01–1000 mL / g (V7's own limits) | they are safety caps, not measuring conditions; "No extra buttons" |
| Reverse metering in a Recipe | **`dose_reverse` is its own action** (maps to `motors.dose` with `direction: -1`) | V7 offers it; an action name cannot be mistyped into a reversal the way a sign can |
| RPi reports `flow: 'verified'` | **Allowed, shown as what it is** (alarm priority middle, labels follow the RPi). **Missing `hello.flow` (1.3.0 program) → every `plasma_run` refused** | `verified` is the RPi's documented fallback `[FACT plasmic_tcpd.py:48-55]`; the unified flow has never run on the rig, so the fallback must stay usable. 1.3.0 without a choice would run the 10-second order unannounced |
| USB ids as a setting | **Constant in code** `MEGA_USB_IDS = {('2341','0042'), ('2A03','0042')}` | fixed decision 3 names exactly these; a file edit must not switch the rule off |
| plasma parameters buttons | **One button whose label follows the state** (“Save and sync”/“synchronized to Nano”/“has been synchronized”), “Give up modification”, “Clear Nano Fault”, “⚠ Manual start plasma”. No “re-read status” | the program polls every second and refreshes a stale RPi snapshot by itself (B.2) |
| Module split | safety-backend's list (B.1) | disjoint file ownership |
| PIN-only GET routes | **None. Every GET is open; every PIN-gated action is a POST** | a download link cannot carry the session header; the PIN table becomes the POST route list (table-driven test); `/api/state` can never renew a session. The spectrum tab stays inside Maintenance because “capture spectrum (no discharge)” is a device tool |
| Launchers | all `.cmd` byte-identical **except the two `rem` lines of `Set-PIN.cmd`** (they say the PIN is optional and can be deleted, which would now close Maintenance) | wrong advice in a file the owner may open |
| Spectrum routes | keep the 1.3.0 names `/api/runs…` | existing tests port one to one |

### 0.3 Facts re-verified for this document

| Claim the build depends on | Where read |
|---|---|
| 26 registry functions; names and parameters used below (`motors.run {motor, flow_ml_min, direction}`, `motors.stop {motor}`, `motors.dose {motor, volume_ml, direction}` → `{steps, sps, eta_s}`, `motors.dose_by_weight {motor, target_g, direction}`, `motors.clean {}` → `{started, total_pump_timeline_s}`, `motors.stop_all {}`, `pump.detect/init/stop {}`, `pump.set_syringe {volume_ul}`, `pump.set_valve {position}`, `pump.set_speed {speed_ml_s}`, `pump.aspirate/dispense {volume_ul}`, `scale.tare {}`, `scale.calibrate {grams}`, `scale.restart {}`) | V7 `functions\motors.py:167-526`, `pump.py:38-123`, `scale.py:195-284` |
| `Function.invoke(ctx, payload)` refuses unknown keys, fills defaults; `Context(link, config)` | `registry.py:67-103` |
| V7 functions use only `link.send`, `link.snapshot`, `link.refresh_state` | grep over `functions\*.py` |
| `Link.send(command, terminators=None, timeout=None)`; returns the collected lines as success when the terminator never came but any line did | `transport.py:318-357` |
| `Y,INIT` uses `long_command_timeout_s` (60 s) under `_tx_lock` | `transport.py:74-76, 327-331`; `hardware.yaml` `link` |
| `READY…` only sets `state['firmware']`; subscriber queues hold 200 events and drop silently | `transport.py:222-223, 389-407` |
| Simulator prints `READY`, answers `Y,ST` with `PUMP,…ready=1,error=0`, answers `W,CFG`, prints `cal_pulses_per_ml`; weight rises only in state `run`; cleaning and valve times are **not** scaled by `simulation_speedup` | `simulator.py:59-62, 145-179, 226-236, 272-451` |
| Weight dose: own baseline, cancel flag, un-cancellable `time.sleep(settle_s)` after a top-up, final `V,m,0` always | `motors.py:375-377, 117-120, 468, 506-511` |
| EC: `Link`, `Schedule`, `PinLock`, `Store`, `Application(data_dir, demo=False, auto_sync=True)`, routes, `pin.json` = `{salt, digest, iterations}` | `pc\app.py` in full |
| Installer imports `app` and uses `write_json`, `VERSION`, `validate_config`, `DEFAULT_CONFIG` | `installer\install.py:36, 132, 228, 378-385` |
| RPi: `hello.flow`, `status.{busy, job, age_s, snapshot, ready{plasma, problems[{reason, fix}]}, estimate_s, start_s, flow, sim}`, `job.{id, kind, state, high_voltage, flow, timing, cancel_reason, error, post_stop, progress{phase, frames_done, frames_total}}`, `plasma_run {confirm_high_voltage: true}` → `{job, voltage_v, estimate_s, flow, watchdog_s}`, `stop` → `{cancel_sent, …}`, result `{flow, background_frames, signal_frames, axis, background, signal, difference, frames}`; `VERSION = '1.3.0'` still | `rpi\tcpd\plasmic_tcpd.py:46, 279-336, 405-420, 971-1025, 1059-1068, 1264-1347` |
| The RPi's `fix` texts name buttons that no longer exist | `plasmic_tcpd.py:292-327` |
| Original font files and their weights | HMI `web\vendor\fonts.css` |

---

## A. Recipe language

### A.1 File format and limits

- One Recipe = one published CSV describing **one round**. Repetition belongs to Schedule measurement. No variables, no branches.
- Header exactly `action,target,value,unit,note`; every row has five cells; cells are stripped; an empty row is skipped;
  `note` is cut to 200 characters; `line` = CSV row number (header = 1).
- Limits (the original's): 200 000 bytes · 500 source rows · 2000 steps after expansion · repeat nesting 5 · repeat count 1–20 ·
  sum of `delay` ≤ 7200 s.
- Encoding on import (server side, `recipe.decode(bytes)`): UTF-8 with or without BOM; if that fails, cp950 (what Excel's plain
  “CSV (comma separated) ” writes on this PC) — then the check result carries the warning
  “This file is not UTF-8 (Excel ). The special words in the remarks may become ”?”.It is recommended to Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)”. ”;
  if both fail: “CSV cannot be read: please Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)” and then import. ” The stored text is UTF-8 with LF.
  Exports and example downloads are sent as UTF-8 **with** BOM so Excel shows the Chinese notes.
- Numbers are parsed with `decimal.Decimal`; a value outside the range or not on the step is refused (never rounded).
- Targets come from the vendored `hardware.yaml`: motors `M<id>` for every channel id (today `M2`…`M9`); syringe head-valve
  positions `CH1`…`CH<valve_positions>` (today `CH1`–`CH3`, shown as “Valve position 1/2/3”).

### A.2 Action table

"fn x.y" = `registry.get('x.y').invoke(Context(checked_link, cfg), payload)` through `PumpBoard` (B.3). "TCP x" = RPi command
over `RpiLink`. `S` = the round's stop flag. Before every fn that moves something `Devices.permit_motion()` is called (B.4).
**On any failure the step is marked failed, the round ends `failed`, `Devices.all_off()` runs (outputs off, read back) and the
schedule ends** — not repeated per row. Nothing is retried, except the read of a finished discharge (twice).

| action | Screen name | target | value (range, step) | unit | What it does | Maps to | Finished when | Time limit → then |
|---|---|---|---|---|---|---|---|---|
| `delay` | Waiting | empty | 0–3600, 0.1 | `s` | waits; paused time does not count | runner | clock; `S` and pause checked every 0.1 s | the value |
| `pump_on` | Start pump (forward rotation) | `P1`–`P8` | 0.1–80 (`max_flow_ml_min`), 0.1 | `mL/min` | starts continuous forward running and goes on to the next row | fn `motors.run {motor, flow_ml_min, direction: 1}` → `V,<m>,<sps>`, `?` | `OK`, then group state `wait` or `run` with `selected == motor` | reply 3 s. **Host deadline = `planned_s` + 5 s** (`planned_s` = sum of the `delay` rows up to its `pump_off`, from preflight; +1 s for M2 / M9) → `motors.stop`, round fails |
| `pump_reverse` | Start pump (reverse) | `P1`–`P8` | same | `mL/min` | the same, reverse | fn `motors.run {…, direction: -1}` | same | same |
| `pump_off` | stop pump | `P1`–`P8` | empty | empty | stops that motor | fn `motors.stop {motor}` → `V,<m>,0`, `?` | group `idle` (`?` every 0.3 s); M2 / M9 also valve bit 0 | idle 2 s, valve 3 s → `X` |
| `dose_volume` | Quantitative (volume) | `P1`–`P8` | 0.01–1000, 0.01 | `mL` | forward dose by step count at V7's fixed dose speed (`dose_flow_ml_min` 40; V7 ignores a flow argument `[FACT motors.py:284-287]`); ends by itself in the firmware | fn `motors.dose {motor, volume_ml, direction: 1}` → `P,<m>,<steps>,<sps>`, `?` | group back to `idle` with `selected` unchanged (state follows the unsolicited `STATE`; `?` every 1 s as fallback); M2 / M9 also valve closed | `eta_s × 1.5 + 10 s` (`eta_s` from V7's reply) → `X` |
| `dose_reverse` | Quantification (volume, inversion) | `P1`–`P8` | same | `mL` | the same, reverse | fn `motors.dose {…, direction: -1}` | same | same |
| `dose_together` | Simultaneous quantitative | two or more of `P1`–`P8` joined by `+` (`P1+P8`) | one amount per pump, joined by `+` (`10+2`), each 0.01–1000, 0.01 | `mL` | AMENDMENTS M16: several volume doses as one step; the pumps of different driver groups turn together, those of one group one after the other | `PumpBoard.dose_many` → fn `motors.dose` per pump | every group `idle`, linked valves closed | per pump ETA × 2.5 + 10 s → `X` |
| `dose_weight` | Quantitative (weight) | `P1`–`P8` | 0.01–1000, 0.01 | `g` | V7's closed loop on the scale (own zero at the start, 40 → 10 → 3 mL/min), forward only | fn `motors.dose_by_weight {motor, target_g, direction: 1}` (blocks inside V7) | the call returns; then group `idle` is verified. `within_tolerance: false` is **not** a failure: step Complete, numbers recorded, alarm `weight_tolerance` (middle) | V7: 300 s from pump start, 30 s without gain, 4 s without a reading. Host hard stop 420 s → `X` |
| `clean` | automatic cleaning | empty | empty | empty | firmware-timed rinse (water motor + OUT motor of `hardware.yaml`; times compiled into the firmware) | fn `motors.clean {}` → `?`, `C`, `?` | reply `started: true`, then `cleaning` false and both groups `idle`, valves closed | `total_pump_timeline_s` + valve delays + 20 s (today 142 s) → `X` |
| `syringe_init` | syringe pump initialization | empty | empty | empty | homes the plunger; then applies the saved speed. Tracked content becomes 0 | fn `pump.init` (`Y,INIT`, host wait cut to 5 s by the proxy), poll `Y,ST`; fn `pump.set_speed {saved}` | `PUMP,…ready=1,error=0` | 20 s |
| `syringe_speed` | Syringe pump speed | empty | 0.001–max for the syringe (2.5 for 5 mL, 0.625 for 1.25 mL), 0.001 | `mL/s` | speed for the following moves of this round (optional row; the saved default applies otherwise) | fn `pump.set_speed {speed_ml_s}` → `Y,SPEED,<raw>` | `PUMP,` reply with `error=0` | 3 s |
| `syringe_aspirate` | syringe pump for pumping liquid | `CH1`–`CH3` | 0.001–syringe size in mL, 0.001 | `mL` | turns the head valve (skipped when already there), then draws in | fn `pump.set_valve {position}`, poll ready; fn `pump.aspirate {volume_ul}` (whole µL = `round(mL × 1000)`) | `Y,ST` → `ready=1`, `error=0` **and** at least 80 % of `volume ÷ speed` has passed | valve 5 s; move `volume ÷ speed × 1.5 + 5 s` → `X` + `Y,STOP` |
| `syringe_dispense` | syringe pump injection | `CH1`–`CH3` | same | `mL` | turns the head valve, then pushes out | fn `pump.set_valve`, fn `pump.dispense {volume_ul}` | same | same |
| `scale_read` | Record weight | empty | empty | empty | writes the weight (median of 5 **new** readings) into the round record; `note` is its name | board weight cache (no command) | 5 new sequence numbers | 5 s |
| `rpi_status` | Check that the high voltage is off | empty | empty | empty | fresh read of HSPY and Nano; fails unless `hv_view()` is known and idle (B.4) | TCP `status {fresh: true}` | reply | 40 s |
| `nano_prepare` | Synchronization parameters arrive Nano | empty | empty | empty | writes `parameter.json` into the Nano, never arms | TCP `prepare` | reply `prepared: true` | 60 s |
| `plasma_run` | discharge＋capture spectrum | empty | empty | empty | the RPi's measuring flow (unified: 1 background frame, then discharge + signal frames); the spectra are fetched and stored | `Devices.hv_session()` → TCP `plasma_run {confirm_high_voltage: true}` (unattended, claimed) → `wait_job` → TCP `result {job, frames: true}` → `Store.save_run` | job state `done` and the result stored on this PC | reply as 1.3.0; job 360 s; link may be gone 30 s; result read 2 tries 2 s apart. **The discharge is never repeated** |
| `spectrum_capture` | Spectrometer inspection (no discharge) | empty | empty | empty | 3 frames without high voltage at the integration time of plasma parameters; saved as a run. This is not the background of a measurement | TCP `spectrum {integration_ms}` (claimed) → `wait_job` → `result` | job `done` and stored | `3 × integration + 60 s` |
| `nano_stop`, `power_off` | Turn off high voltage | empty | empty | empty | HSPY OFF + Nano STOP with read-back (two names, one action, kept so the original's CSV files load) | TCP `stop` over a fresh connection | reply `ok` | 120 s |
| `repeat_start` | Repeat start | empty | 1–20, integer | empty | opens a block | expanded before anything runs | | |
| `repeat_end` | Repeat end | empty | empty | empty | closes it | | | |

Not Recipe actions, on purpose: scale Return to zero / Correction (they rewrite the calibration file; PIN), syringe size, `motors.select`,
`motors.stop_all`, valves (V7 has no valve command), plasma parameters (the seven values of the RPi's `parameter.json`).

### A.3 Preflight (no hardware; at import, publish, list, preview and again at every start)

State kept while walking the expanded list: `running` {motor → start line, planned seconds so far}, syringe `inited`,
syringe content `vol` (µL).

| # | Rule | Message (shown as is; `{Name}` = Screen name, `{motor}` = the YAML label) |
|---|---|---|
| R1 | file limits, header, columns, repeats | “CSV The first column of must be action,target,value,unit,note” · “CSV is too large (upper limit 200 KB) ” · “CSV most 500 column” · ”No. N Column: The number of fields is incorrect (required 5 column)” · ”Exceeded after expansion 2000 actions” · “repeat most 5 layer” · ”No. N column:repeat_start If the number of 1–20 integer” · “No. N column:repeat_end has no corresponding repeat_start” · “No. N column:repeat_start less repeat_end” · “CSV There is no action that can be performed” |
| R2 | known action; target, value, unit as in A.2 | “No. N Column: Unsupported action ”x”” · “No. N column:{Name} The object to be written P1–P8” / “…to write CH1–CH3” / “…must be blank” · ”Chapter Programs such as the serial port monitoring window of N column:{Name} The value should be within a–b ” · ”No. N column:{Name} is The new entry point of s” · “No. N column:{Name} must be blank” · ”No. N column:{Name} should be written The four lines of u” / “…The unit should be blank” |
| R3 | one motor per driver group | “No. N column:A group can only transfer one at a time ({motor} is still transferring)” |
| R4 | `pump_on` / `pump_reverse` of a motor that already runs; `pump_off` of one that does not | “No. N column:{motor} is already transferring” · ”No. N column:{motor} is not transferring” |
| R5 | while any motor runs continuously only `delay`, `pump_on` / `pump_reverse` (other group) and `pump_off` are allowed. So doses, cleaning, syringe, `scale_read` and all RPi steps need every pump stopped — **the static half of "pumps and high voltage never together"** (every other board step blocks until it has finished) | “No. N column: do ”{Name}”” between |
| R6 | planned on-time of every continuous run (sum of the `delay` rows up to its `pump_off`) is 0.1…300 s; stored in the step as `planned_s` | “No. N column:{motor} will transfer continuously T seconds, exceeding the upper limit 300 seconds” · ”No. N column:{motor} Turn it off when it is turned on, there must be something in between delay” |
| R7 | every continuous pump is switched off before the end | “The pump is still running at the end of the process:{motor} (No. N column startup)” |
| R8 | syringe: a move or `syringe_speed` needs `syringe_init` earlier **in the same Recipe**; `syringe_init` only with `vol` = 0; `vol` stays within 0…size; `vol` = 0 at the end; speed ≤ the limit of the saved size | “No. N column: Before using the syringe pump, syringe_init” · “No. N Column: There is still something in the syringe a mL,initialization will push it out; please first syringe_dispense” · “No. N column: The syringe cannot be loaded (existing a mL,capacity c mL) ” · “No. N column: There are only a mL,Can’t push it out b mL” · “There is still something left in the syringe at the end of the process. a mL,Please launch it first” · ”No. N column: Syringe pump speed exceeds the upper limit x mL/s” |
| R9 | `dose_weight` needs `motors.gravimetric_dose.enabled` | “No. N Column: Profile does not enable weighing and rationing” |
| R10 | totals | “The total waiting time exceeds 7200 Second” |
| R11 | what depends on a setting (syringe size) is re-checked at every start with the current setting | same texts |

Warnings (never block; shown at import, on the Recipe facts and in the start dialog):

| # | Condition | Text |
|---|---|---|
| W1 | `pump_on` / `pump_reverse` above the dose speed after a `dose_volume` / `dose_reverse` of the same group | “No. N column: After the same group has been quantified, continuous operation may be limited to 40 mL/min (firmware characteristics, not yet tested)” |
| W2 | more than one `plasma_run` | “this Recipe One round will discharge n times” |
| W3 | `pump_reverse` or `dose_reverse` used | “No. N column:{motor} reverse” |
| W4 | `dose_weight` on a motor without `fluid` in the YAML | “No. N column:{motor} No liquid is marked, please make sure its outlet is on the scale” |
| W5 | no `plasma_run` | “this Recipe has no measurement steps (plasma_run) ” |
| W6 | cp950 file | the text of A.1 |

Result of `recipe.validate(text, hardware, syringe_ul)`:
`{valid, error, error_line, warnings[], step_count, rounds, delay_seconds, phases[{name, from, to}], uses_pump, uses_motors[], uses_syringe, uses_scale, uses_rpi, uses_plasma, plasma_count, liquids[{motor, label, ml, g}], steps[]}`;
`steps[]` item `{n, line, action, name, target, target_label, value, unit, note, round, rounds, phase, text, planned_s}`.
`liquids` adds up `dose_volume`/`dose_reverse` (mL), `dose_weight` (g) and continuous runs (`flow × planned_s ÷ 60`, mL, marked about).
Only the outermost `repeat_start` counts as a displayed round (`round`, `rounds`), as the original.

Step sentence `text` (built on the server, shown on Overview, in the Recipe table and in records):
“Waiting 30 Second” · “Pump 7 · OUT Forward rotation 40 mL/min (longest running time 60 seconds)” · “Pump 7 · OUT reverse 40 mL/min (longest running time 60 seconds)” ·
“Pump 7 · OUT stop” · “Pump 1 · water quantitative 20 mL” · “Pump 1 · water inversion quantitative 20 mL” · “Simultaneous quantification:Pump 1 · Water:10 mL, Pump 8 · CuSO₄: 2 mL” · “Pump 8 · CuSO₄ scale weight added to 10 g” ·
“automatic cleaning (Pump 1 Water ingress,Pump 7 discharge, approx. 122 seconds)” · ”Syringe pump initialization” · ”Syringe pump speed 0.4 mL/s” · “Syringe pump slave valve position 2 smoke 1 mL” ·
“Syringe pump moves to valve position 1 Note 1 mL” · “Record weight” (+“: {note}”) · “Check that the high voltage is off” · ”Synchronize the parameters to Nano” · “discharge＋Capture spectrum” ·
“Spectrometer inspection (no discharge, reading 3 Zhang)” · ”Turn off high voltage”.

Display phases (display only; neighbouring steps of one class form one phase `{name, from, to}`):
Add liquid = pump, dose, syringe and `scale_read` steps · Empty = a pump step of the OUT pump (`cleaning.out_motor`; AMENDMENTS M16) · Cleaning = `clean` · Measure = `rpi_status`, `nano_prepare`, `plasma_run`,
`spectrum_capture` · Turn off high voltage = `nano_stop`, `power_off` · `syringe_speed` takes the class of the next device step ·
`delay` takes the class of the previous step (Waiting when it is the first).

### A.4 Start checks (devices; at schedule start, in the preview as `blockers`, and again at the start of every round)

| Recipe has | Required now | Reason code · text · fix |
|---|---|---|
| anything | no other owner of the equipment | `manual_active` “The manual operation is not over yet.” ”Click that column first. OFF,Or wait for it to finish” · `tool_active` “Maintenance tool is using the device” |
| pump / syringe / `clean` / `scale_read` | board configured and connected | `pump_unset` “The pump control board has not been set yet.” ”Ask maintenance personnel to ”Repair › Connection ”Select Port” · `pump_disabled` “Pump control board is set to not use” (same fix) · `pump_link` “Pump control board is not connected ({text}) ” “examine USB line” |
| same | not `motion_uncertain`, both groups `idle`, not cleaning, valves closed, syringe not busy | `pump_uncertain` “The status of the pump control board is unknown, please go to the site to confirm first.” · `pump_moving` “The pump is still moving” |
| `dose_weight` / `scale_read` | newest weight younger than 1 s; for `dose_weight` a calibration was loaded at connect | `scale_offline` “scale has no reading value” · `scale_uncalibrated` “The scale has not been calibrated yet. Please ask maintenance personnel to repair it. › Pump and scale ”Do a calibration” |
| any pump step, RPi connected | `hv_view()` known and idle | `hv_on` / `hv_unknown` with the rows of B.4 |
| any pump step, RPi not connected | the start dialog's tick `hv_off_checked` | `hv_unknown` “RPi There is no connection, so it is impossible to confirm that the high voltage is off.” |
| any RPi step | RPi connected, status fresh, not busy | `rpi_link` “RPi Not connected” · `rpi_busy` “RPi is performing other work” |
| `plasma_run` | `hello.flow` present | `rpi_old` “RPi is still an old version: please double-click first Install.cmd renew RPi” |
| `plasma_run` | `status.ready.plasma` true | `rpi_not_ready` “The pre-discharge inspection failed:” + the RPi's reasons, each `fix` passed through `rewrite_fix` (B.2) |
| `plasma_run`, no pump step | board not in use (mode off, never chosen, “Not used”), or connected and quiescent; never `motion_uncertain` | `pump_moving` “The pump is still moving and cannot discharge.” · `pump_link` “The pump control board has been set but not connected, and the pump cannot be confirmed to have stopped.” ”Connected. USB,Or ask maintenance personnel to go to ”Repair › Connection” was changed to ”Do not use pump control board” |

A round that cannot start → slot `not_started` with the text; three in a row end the schedule (as 1.3.0).

### A.5 Estimate of one round (preview, interval check)

`delay` its value · `pump_on` / `pump_reverse` 1.5 s (2.5 for M2 / M9) · `pump_off` 1 s (2 s) · `dose_volume` / `dose_reverse`
`|steps| ÷ sps × 1.1 + 1` (+2 for M2 / M9; `steps = mL × dose_pulses_per_ml`, `sps = round(dose_flow × pulses_per_ml ÷ 60)`) ·
`dose_weight` `g ÷ (fast_flow ÷ 60) × 1.3 + 12` · `clean` YAML total + 2 · `syringe_init` 6 · `syringe_speed` 0.5 ·
syringe move `mL ÷ speed + 2` · `scale_read` 1 · `rpi_status` 3 · `nano_prepare` 6 · `plasma_run` `status.estimate_s`
(30 when unknown) · `spectrum_capture` `3 × integration ÷ 1000 + 8` · `nano_stop` / `power_off` 5. All board numbers come from
the vendored YAML.
Hard error at preview and start: `interval_seconds ≤ delay_seconds` → “interval N minute ratio Recipe waiting time S Seconds are still short, and it times out every time.”.
Warning: estimate > interval → “One measurement is estimated to be approximately S seconds, longer than the interval: some periods will be skipped because the last time has not ended yet.” (the 1.3.0 text).

### A.6 Example Recipes (`pc\recipes\*.csv`, UTF-8 without BOM, LF)

`plasma-only.csv` — “Discharge measurement only”. **Published automatically** at migration. Needs only the RPi; works with no pump board.
One line on purpose: the RPi repeats every readiness check itself, so the discharge starts about `status.start_s` after the
slot is due (owner's "no more than 5 Second").

```
action,target,value,unit,note
plasma_run,,,,discharge＋capture spectrum
```

`dose-measure-drain.csv` — “Example: Add liquid＋Measure＋discharge”. Not published: a file for maintenance to adapt. Uses only the three motors
`hardware.yaml` names (2 water, 8 OUT, 9 CuSO₄).

```
action,target,value,unit,note
dose_weight,P8,10,g,join in CuSO₄ solution 10 g (Confirm with scale) .Please modify the value according to actual needs
dose_volume,P1,20,mL,add water 20 mL
delay,,5,s,and other liquid levels are stable
scale_read,,,,Weight after adding liquid
plasma_run,,,,discharge＋capture spectrum
pump_on,P7,40,mL/min,discharge (OUT) 
delay,,60,s,Row 60 Second
pump_off,P7,,,
```

`cleaning.csv` — “Example: automatic cleaning. Not published. Needs only the board (RPi off → the start dialog asks for the extra tick).

```
action,target,value,unit,note
clean,,,,Automatic cleaning: water first, then water in and out at the same time, and finally emptying (the timing is in the firmware)
```

---

## B. PC program structure

### B.1 Modules (all under `pc\`; standard library only, except `pumpboard.py` / `pump_guard.py`, which use the vendored packages)

| File | Contains | Notes |
|---|---|---|
| `app.py` | `sys.dont_write_bytecode = True` (first statement after the `sys.path` line), `VERSION = '2.0.0'`, `main()`, argument parsing, `--set-pin`, console-close handler. **Import surface kept for the installer and the tests**: `Application`, `ExclusiveHTTPServer`, `make_handler`, `PinLock`, `Store`, `write_json`, `now_iso`, `validate_config`, `DEFAULT_CONFIG`, `site_config`, `RUN_ID`, `schedule`, `netcheck`, `plasmic_client`, `datetime` | the only caller of `pumpboard.allow_real_ports()`. `UI_WATCHDOG_SECONDS` now lives in `rpilink.py`; tests patch it there |
| `application.py` | `Application`: builds and owns every object; `state()`; one thin method per API route; the PIN table (B.10) | no device logic |
| `httpd.py` | `ExclusiveHTTPServer`, `make_handler(app, csrf)`, router, static table, error mapping, headers | API.md |
| `store.py` | `write_json`, `now_iso`, `validate_config`, `DEFAULT_CONFIG`, `site_config`, `RUN_ID`, `run_csv`, `Store` (config, events, spectra `runs\`, Recipes, round records, `runlog.jsonl`, schedule record, operators, `pumpboard.json`) | code moved from `app.py`, not reworded; calls `migrate.run()` first |
| `migrate.py` | 1.3.0 → 2.0.0 data migration (D.3) | idempotent, never deletes |
| `pinlock.py` | `PinLock` moved; new rules of B.10 | same file format and parameters |
| `alarms.py` | `Alarms` (raise / clear / ack / view / persist) | B.9 |
| `rpilink.py` | `Link` moved and renamed `RpiLink` (`Link = RpiLink` alias); all "sequence" code removed; `UI_WATCHDOG_SECONDS`, `CANCEL_REASONS`, `rpi_ready()`, `wait_job()` (the moved `_ready` / `_wait_for_job`, texts unchanged); new `hv_view()`, `flow_view()`, `params_view()`, `fetch_result()`, `rewrite_fix()` | TCP protocol untouched |
| `recipe.py` | `decode`, `parse`, `expand`, `validate`, `estimate`; the action table as data (`ACTIONS`); `EXAMPLES` | pure functions |
| `pumpboard.py` | `PumpBoard`, `CheckedLink`, `PumpBoardError`, port listing, V7 loader, `allow_real_ports()` | B.3 |
| `pump_guard.py` | guard child process | B.3.10 |
| `devices.py` | `Devices`, `Busy`, `Blocked`: ownership, mutual exclusion, `all_off()`, `stop_everything()`, interlock views | B.4 |
| `steps.py` | `StepExecutor.run(step, ctx)`: one function per action | B.5 |
| `runner.py` | `Runner`: schedule loop + round execution, pause / resume / stop, records | B.5, B.6 |
| `manual.py` | `ManualOps` | B.7 |
| `schedule.py` | time arithmetic — kept; only `ACTIVE`, new `BUSY`, and three labels change (B.6) | |
| `netcheck.py`, `plasmic_client.py`, `examples\*` | unchanged | `plasmic_client.py` stays as the command-line TCP tool |
| `vendor\…`, `recipes\*.csv` | section F, A.6 | |

### B.2 `rpilink.py` — what is added to the moved `Link`

| Addition | Specification |
|---|---|
| `command(cmd, attended=True, claim=False, **fields)` | as 1.3.0. `claim=True` adds `reply['job']` to `self.handled` **before `request_lock` is released**, so the link thread (which saves every finished job it has not handled `[FACT app.py:417-419, 449-461]`) never fetches, saves and logs a round's job a second time |
| `wait_job(job_id, stop_event=None, limit_s=360, lost_s=30)` → `(status, note)` | the moved `Schedule._wait_for_job`, texts unchanged (“and RPi ’s connection is interrupted and the results of this measurement cannot be confirmed; please go to the site to confirm the equipment.” For …) |
| `fetch_result(job_id)` → result dict | `result {job, frames: true}`; second try after 2 s; never sends `plasma_run` |
| `rpi_ready()` → status | the moved `Schedule._ready`: “and RPi Not connected” · “RPi is performing other work” · ”The pre-discharge inspection failed:...” |
| keeping the RPi snapshot fresh | the link thread sends `status {fresh: true}` instead of plain `status` when the last reply had `age_s` > 8 (or `None`), no job is running and no command is pending; at most once every 5 s. (The RPi refreshes by itself every `poll_s`, which its config allows up to 60 s.) This replaces every “reading status” button |
| `hv_view()` → `{known, idle, reasons[]}` | B.4 table |
| `flow_view()` → `{name, ok, background_frames, text}` | `hello.flow` missing → `{name: null, ok: false, text: 'RPi is still an old version'}`; `unified` → `{ok: true, background_frames: 1, text: 'background 1 sheets, click to make approx. Stop automatically after {start_s} seconds discharge'}`; `verified` → `{ok: true, background_frames: 3, text: 'Old Process: Background 3 sheets, click to make approx. Stop automatically after {start_s} seconds discharge'}`. The frame count shown before a run comes from this mapping of the RPi's flow name; after a run from the stored `background_frames` |
| `params_view()` → `{values, nano_synced}` | `values` = the seven `parameter.json` keys rebuilt from `snapshot.parameter` (`on_time_ms`→`Ontime`, `off_time_ms`→`Offtime`, `cycles`→`Cycle`, `loops`→`Loopnum`, `integration_ms`→`Integration_Time`, `spectra_interval_ms`→`Spectra_Interval`, `voltage_v`→`Voltage`) or `null`; `nano_synced` = every value equals the Nano's `active_parameters` (`on_time_us`/1000, `off_time_us`/1000, `cycles`, `loop_count`, `integration_time_us`/1000, `spectra_interval_us`/1000, `voltage_display_v` — the table of 1.3.0 `web\app.js:14-22`), `false` when they differ or the Nano has none, `null` when unknown |
| `rewrite_fix(text)` | the RPi side is not changed, but its `fix` texts name old buttons. Replacements, longest first: “Wait for the voltage to drop to 0 and then click ”Read Status”→“Wait until the voltage drops to 0,The program will re-read itself” · ”Click ”Read Status”; if it still fails, please check RS485 USB and RPi”→“Wait a few seconds, the program will re-read itself; if it still fails, please check RS485 USB and RPi” · “Click Clear Nano fault”, then click ”Synchronize Nano parameter””→“Ask maintenance personnel to go to ”Repair › Plasma Parameters” click ”Clear” Nano fault”, then click ”Sync to Nano”” · “Press ”HSPY OFF＋Nano STOP””→“Press ”Stop” at the bottom STOP”” · “in ”Plasma Parameters”→“Ask maintenance personnel to go to ”Repair › Plasma Parameters”. Applied wherever RPi problems are shown (A.4, interlock, blockers). A test scans `plasmic_tcpd.py` (read only) for “according to“…”” and “in ”…” in `ready_problems` and fails when one has no mapping |
| removed | `sequence_fields`, `SEQUENCES`, `raw()` (the TCP terminal) |
| unchanged | connect / reconnect / search, new-Nano auto-sync, attended-discharge page watchdog (`own_job`, `ui_alive`), `stop()` over a fresh connection, all event texts — with one exception: the auto-sync failure sentence ends “; Ask maintenance personnel to go to ”Repair › Plasma Parameters” press ”Synchronize to Nano”” instead of “; can be found in steps 2 according to“Sync to Nano”try again” (that place no longer exists) |

### B.3 `pumpboard.py` — the wrapper around the vendored V7 link

#### B.3.1 Loading V7 (once per process, under a module lock, on first use)

```python
os.environ['LAB_CONFIG'] = str(VENDOR / 'v7' / 'config' / 'hardware.yaml')      # read at import [FACT config.py:16]
os.environ['SCALE_CALIBRATION_FILE'] = str(data_dir / 'pumpboard' / 'scale_calibration.json')   # read at each call
sys.path[:0] = [str(VENDOR / 'site'), str(VENDOR / 'v7')]
from backend import config, registry, transport        # importing opens nothing
from backend import functions                          # registers the 26 functions; a second import under another name raises
from backend.functions import scale
cfg = config.get_config(); cfg['link'] = dict(cfg['link'], port='PLASMIC-NOT-CHOSEN')   # V7's 'auto' can never run
if mode != 'real' or os.environ.get('PLASMIC_NO_SERIAL') == '1':
    transport.serial = _Forbidden(); transport.list_ports = None        # _Forbidden.Serial(...) raises RealPortForbidden
```

| V7 property | Handling |
|---|---|
| `get_config()` cached; `Link.__init__` keeps the dict | per connection, under the module lock: `cfg = config.get_config(reload=True)`; `cfg['link'] = dict(cfg['link'], **patch)`; apply test overrides; `link = transport.Link()`; `assert link.cfg is cfg and str(cfg['link']['port']).lower() != 'auto'`; then again `config.get_config(reload=True)` + the `PLASMIC-NOT-CHOSEN` patch, so the cache never holds a usable port |
| `transport.get_link()` singleton | never called |
| `link` patch | real: `port` = the checked port, `simulate: false`, `boot_delay_s` 2.5, `command_timeout_s` 3.0, `long_command_timeout_s` 6.0. Sim: `port: 'simulator'`, `simulate: true`, `simulation_speedup` (1 in practice mode; tests pass theirs), `boot_delay_s` 0 |
| process-wide state in `functions.motors` / `functions.scale` (weight-dose lock, history, calibration status) | exactly one live `PumpBoard` per process (class guard; a second `start()` raises). `scale.clear_history()` on every connect. One process is real or simulated for its whole life |
| pyserial import failure is swallowed (`serial = None`) | real mode checks `transport.serial is not None` first: “Program folder is incomplete: missing pc\vendor\site\serial.Please copy the entire folder again. ” |
| vendored `hardware.yaml` | read only; never written |

`PumpBoard(data_dir, mode, store, on_event, on_alarm, ui_age, overrides=None, link_factory=None, port_lister=None)`:
`mode` = `'real'` · `'sim'` · `'off'`. `overrides` (a function `cfg -> None`, tests only: short cleaning, valve and settle times),
`link_factory` (scripted simulated link) and `port_lister` (simulated port list) are test seams; production passes none of them (a test
asserts that `application.py` does not).

#### B.3.2 Settings — `pc\data\pumpboard.json`

`{"version": 1, "enabled": null | true | false, "device": "COM3", "vid": "2341", "pid": "0042", "serial_number": "…", "description": "Arduino Mega 2560 (COM3)", "chosen_at": iso, "syringe_ul": 5000, "syringe_speed_ml_s": 0.4}`.
`enabled`: `null` = never chosen (**nothing is ever opened**) · `true` = use the board with the stored identity · `false` = “does not use the pump control board”.
In `sim` mode (practice mode, tests) `enabled` and the identity are ignored: the simulator is always connected (link
`connected`, text “Simulation”). In `off` mode the board does not exist for any rule (link `disabled`, text “Not used”).

#### B.3.3 Port choice, identity check, retries (real mode)

| Step | Rule | Link state and text on failure |
|---|---|---|
| list | `serial.tools.list_ports.comports()` → `{device, description, vid, pid, serial_number, is_mega}`; `is_mega` = (VID, PID) ∈ `MEGA_USB_IDS`. Listing opens nothing | |
| choose (PIN, Maintenance › connection) | `configure(enabled=True, device)`: the device must be in the list **and** `is_mega`; its identity is stored; then connect. Only Mega rows are offered | 400 “COM5 Not now Arduino Mega (or has been removed). Didn’t turn it on. ” |
| find | the stored name, if a port with that name has the stored VID:PID (and serial number when one is stored); else exactly one port with the stored identity → follow it, save the new name, event “The connection port of the pump control board is from COM3 becomes COM5” | `missing` “Pump control board not found: Check USB line” (re-listed every 3 s, nothing opened) · `ambiguous` “Found more than one identical piece Arduino Mega.Please go to ”Maintenance › Connection” reselect. ” |
| open | fresh config (B.3.1), `Link()`, `start()` (V7 opens at 115200, sleeps 2.5 s, starts its reader, sends `?`) | `port_busy` “COM3 is occupied by another program. please close Arduino IDE , or the old The instructions of V7 Windows (run.bat) .” (error text contains `PermissionError` / "Access denied" `[INFER]`); otherwise `error` “cannot be opened COM3: {system message}” |
| identify | up to 3 × `?` (1.5 s each). Pass = a line starting `STATE,` that contains `a=`, `sa=`, `b=`, `sb=`, `valves=`, `w_stream_hz=`, `cleaning=` **and** `link.state['firmware']` is `None` or starts with `READY,arduino+pump`. **Nothing but `?` is written before this passes** | `wrong_device` “COM3 There is a device, but not the pump control board (correct response not received). This port has been closed. ”; `link.stop()`; alarm `pump_wrong_device` |
| fingerprint | `cal_pulses_per_ml` and `max_flow_uL_min` of that `STATE` against `round(pulses_per_ml, 2)` and `max_flow_ml_min × 1000` | not a refusal: `firmware_match: false`, alarm `firmware_mismatch` (middle) |

Retries (every open resets the device): `missing` → list again every 3 s. Open / link errors → 2, 5, 10, 30 s, then every 30 s.
`wrong_device` → **not retried** until “Reconnect”, a new choice, or the port disappears and comes back. After `lost`: one
immediate attempt (reopening is the only way left to stop a pump), then the same backoff. For the first 15 s after program
start `port_busy` is shown as `connecting` (a guard of a killed earlier process may still hold the port).
`banner_seen` records whether a `READY` line came with the open (false = the board did not reset; the next step makes the
state safe anyway).

#### B.3.4 Safe-state sequence (after every successful identity check, every reconnect, every unexpected `READY`)

1. `X` (through `motors.stop_all`: also `W,STREAM,0`, `?`). Verify: both groups `idle`, nothing selected, all valves closed,
   `cleaning` false. Not verified → `link.stop()`, state `error`, alarm `pump_stop_unconfirmed`.
2. `scale.restore_calibration(link)` (`W,DIV`, `W,OFFSET` when the file exists) → `calibration_loaded` = its result is not
   `None`; then `W,AVG,<average_samples>`, `W,STABLE,<stable_threshold_g>`, `W,STREAM,<stream_hz>`, `?` — the traffic of
   V7's own start-up. `link.weight_hooks.append(scale.record)` plus the wrapper's own hook (newest sample + `time.monotonic()`).
3. Syringe: `pump.set_syringe {saved size}` (always — a board that did not reset may hold another size); `pump.detect` once
   (`ERR,pump_not_found…` only sets `syringe.online = false`). **Never an automatic `Y,INIT`.**
4. Bookkeeping: syringe `initialised = false`, `volume_ul = None`, `position = None`; `motion_uncertain = false`; copy
   `link.state['firmware']` to `banner` and set `link.state['firmware'] = None` (the reset detector of B.3.7);
   alarms `pump_link_lost`, `pump_stop_unconfirmed`, `pump_wrong_device` cleared; guard told `port COMn`; state `connected`;
   event “pump control board is wired (COM3) ”.

#### B.3.5 `CheckedLink` — what V7 functions get as `ctx.link`

Offers `send(cmd, terminators=None, timeout=None)`, `snapshot()`, `refresh_state()` (everything V7's functions use).
`snapshot` delegates; `refresh_state` = `self.send('?')` + `snapshot()`. `send` adds:

| Addition | Rule |
|---|---|
| allow-list | only `?`, `X`, `C`, `S,`, `V,`, `P,`, `Y,DETECT`, `Y,INIT`, `Y,SYR`, `Y,VALVE`, `Y,SPEED`, `Y,ASPUL`, `Y,DISPUL`, `Y,STOP`, `Y,ST`, `W,DIV`, `W,OFFSET`, `W,AVG`, `W,STABLE`, `W,STREAM`, `W,TARE`, `W,CAL`, `W,CFG`. Anything else (`W,SCAN`, `W,SNIFF`, `W,SET`, `W,BAUD`, …) is refused before it is written: those block the firmware for up to a minute, during which `X` is not read |
| pump / HV gate | motion commands (`V` with a non-zero speed, `P`, `C`, `Y,INIT`, `Y,VALVE`, `Y,ASPUL`, `Y,DISPUL`) are refused while `Devices.hv_blocks_motion()`: “The high pressure has not been turned off and the pump cannot be started.” |
| own terminators and timeouts | always passed explicitly to V7's `send`: `?` → `STATE,` 1.5 s · `V` / `P` / `C` / `X` → `OK` / `ERR` 3 s · `S,` → `DONE,S` / `ERR` 3 s · `Y,INIT` 5 s, other `Y,` 3 s → `PUMP,` / `FOUND,` / `OK,` / `ERR` · `W,CFG` → `CFG,` 3 s · other `W,` → `OK` / `ERR` 3 s |
| reply really arrived | if no returned line starts with one of the terminators → treated as a timeout (V7 would return the stray lines as success) |
| after any timeout | `resync()`: `W,CFG` with terminator `CFG,` (never unsolicited, answered at once, emulated by the simulator). The firmware answers in order, so when `CFG,` arrives every late reply has been swallowed. Tried twice; failure → link `lost` (B.3.8). A **motion** command that timed out also sets `motion_uncertain` and triggers `stop_all` |
| syringe status | every `PUMP,…` line is parsed; `error ≠ 0` raises `SyringeFault(n)` (V7 ignores it): 2 / 3 “value is out of range” · 7 “The syringe pump has not been initialized yet” · 9 “Piston overload” · 10 “Valve overload” · other “Syringe pump report error n”; alarm `syringe_fault` |
| firmware errors | `ERR,cleaning_active` → “This action cannot be performed during automatic cleaning.” · `ERR,arg` → “Pump control board does not accept this value” · `ERR,pump_timeout` / `pump_bad_reply` / `pump_echo_only` → “Syringe pump does not respond” · `ERR,scale_timeout` → “The scale did not respond” · `ERR,no_change` → “The weight has not changed and cannot be corrected.” · `ERR,bad_cmd` → “The pump control board does not recognize this command (different firmware version?)” |
| write failure / not connected | V7 raises “failed to send:” / “Not connected yet Arduino” and does not mark the link → the proxy marks it `lost` |
| bookkeeping | time of the last command and of the last `STATE`; which group was sent a motion command |

Exceptions raised by the proxy subclass V7's `LinkError`, so V7 code that catches it (`_stop_quietly`, `restore_calibration`) behaves as before.
`FunctionError` → `PumpBoardError('refused', V7's Chinese text)`; `LinkError` → `PumpBoardError('link' | 'timeout' | 'blocked' | 'device', text)`.

#### B.3.6 Operations (public methods; all raise `PumpBoardError(code, text)`)

`owner` is `'round'` or `'manual'`; `abort` is a `threading.Event` set by STOP. Blocking methods run on the caller's thread
(runner or manual-op) and hold `op_lock` (non-blocking acquire → `PumpBoardError('busy', 'The pump control board is performing another action')`).
"Quiescent" = both groups `idle`, not cleaning, all valves closed, no operation, no deadline, syringe not busy.
`k` = `simulation_speedup` (1 on the real board).

| Method | Preconditions (else `refused`) | Sequence | Ends when / fails when |
|---|---|---|---|
| `run(motor, flow, direction, limit_s, owner)` | connected; not cleaning; no weight dose; **that motor's group `idle`**; flow within the YAML limit; `1 ≤ limit_s ≤ 306` | `?` → `motors.run` → check `selected == motor` and state `wait` / `run` → deadline `now + limit_s` (+1 s for M2 / M9); guard `arm` | returns at once `{motor, sps, until}`. Wrong state → `stop_all`, “motor N did not start as instructed” |
| `stop_motor(motor)` | — (never refused for ownership). During cleaning: “A single pump cannot be stopped during automatic cleaning. To abort, click ”Stop” STOP”.” | `motors.stop` (also cancels a weight dose on that motor) → poll `?` until the group is `idle` (2 s) → wait for the valve bit (3 s) → clear the deadline; guard `disarm` when nothing endless is left | not idle in time → `stop_all` |
| `dose(motor, volume_ml, direction, owner, abort)` | quiescent | `motors.dose` → remember `steps`, `sps`, `eta_s` → wait | group `idle` + valve closed → `{steps, sps, eta_s, elapsed_s}`. Timeout `eta × 1.5 + 10`, `abort`, link lost, reset, `selected` changed → `stop_all` and error. `idle` earlier than `0.8 × |steps| ÷ sps ÷ k` → “The ration ended earlier than expected and may have been interrupted.” (runs in tests too) |
| `dose_weight(motor, target_g, owner, abort)` | quiescent; weight younger than 1 s; `calibration_loaded` (else “The scale has not been calibrated yet: go to ”Maintenance › Pump and scale ”Do a calibration” — after a reset the Mega reports raw counts as grams) | guard `arm`; `threading.Timer(420, stop_all)`; `motors.dose_by_weight` (blocks; V7's limits apply) | returns V7's result (`dispensed_g`, `error_g`, `within_tolerance`, `topups`, `elapsed_s`, `baseline_g`, `warning`). `FunctionError` → `abort` set ? `stopped` : `device` with V7's text. **If the link is not connected at that moment the text is replaced** (V7 says “Peristaltic pump has stopped” although its stop could not be sent): “The pump control board is lost and the stop command cannot be sent. The pump may still be spinning. ” + `motion_uncertain`. Afterwards the group must be `idle`, else `stop_all` |
| `clean(owner, abort)` | quiescent | `motors.clean` → `started` must be true → wait | `cleaning` false, groups `idle`, valves closed. Timeout / `abort` → `stop_all` |
| `syringe_init(owner, abort)` | quiescent (the firmware reads no command while it waits for the syringe pump) | `pump.init` → poll `Y,ST` every 0.3 s → `pump.set_speed(saved speed)` | `ready=1` within 20 s → `initialised = true`, `volume_ul = 0`, `position = None`; clears alarm `syringe_fault` |
| `syringe_speed(speed_ml_s)` | initialised; not busy; ≤ max | `pump.set_speed` | reply; remembered for move times |
| `syringe_move(kind, position, volume_ml, owner, abort)` | initialised; quiescent; volume fits the bookkeeping | `pump.set_valve` (skipped when `position` is the current one) → poll ready (5 s) → `pump.aspirate` / `pump.dispense` → poll | `ready=1`, `error=0` and `0.8 × volume ÷ speed ÷ k` passed → bookkeeping updated. Error / timeout / `abort` → `stop_all`, `initialised = false` |
| `scale_read(samples=5)` | weight younger than 1 s | wait for 5 new sequence numbers (5 s) | median grams |
| `scale_tare()` | quiescent; newest sample younger than 1 s **and `stable`** (the firmware takes one reading without a stability check) — else “The pump is still running” · ”The scale has no reading” · ”The reading is still changing, wait for it to stabilize” | `scale.tare` (`W,TARE`, `W,CFG`, file written by V7 into `pc\data\pumpboard\`) | reply; `calibration_loaded = true` |
| `scale_calibrate(grams)` | the same; 0.1–20000 g | `scale.calibrate` | reply |
| `apply_settings(syringe_ul, syringe_speed_ml_s)` | quiescent | saves `pumpboard.json`; connected → `pump.set_syringe`; a new size sets `initialised = false` | |
| `quiescent(fresh=True)` | — | optional `?` | `(ok, [reason])` |
| `stop_all(reason)` | — | B.3.9 | |

Never repeated automatically: any motion command.

#### B.3.7 The monitor thread (`pump-monitor`, every 0.1 s)

1. Not connected and `enabled` → connect attempts (B.3.3).
2. **Reset detection without the subscriber queue**: `link.state['firmware']` is no longer `None` → the Mega printed `READY`
   again → board reset (B.3.8). (The queue holds 200 events and drops silently while weights arrive at 10 Hz; this flag
   cannot be lost. `link.subscribe()` is not used at all; waits read `link.state`, which the reader thread updates.)
3. `link.connected` false (V7's reader ended on a read error) → `lost`.
4. Deadlines: a continuous run past its deadline → `motors.stop(motor)`; not idle within 2 s → `stop_all`. Manual run: event
   “{motor} The set time has arrived and it has automatically stopped”. Round: flag `deadline_tripped` (the round fails with “{motor} The planned operation time has been exceeded and it has automatically stopped.”).
5. Page watchdog: a manual pump operation is active and `/api/state` has not been asked for `UI_WATCHDOG_SECONDS` (10 s) →
   `ManualOps.cancel()` + `stop_all('page')`; alarm `page_lost` “The operation screen exceeds 10 No response for seconds,Manually started pump stopped”.
6. Heartbeat: last `STATE` older than 2 s → `?` (1.5 s). Two failures in a row → `resync()` → `lost`.
7. Scale: no sample for 3 s while the stream should be on, or 1 s after any `stop_all` → one `scale.restart`; still nothing
   after 10 s → alarm `scale_offline`. (No “Reread” button is needed.)
8. Serial log: new entries of `link.log` (tx / rx, no `WEIGHT`) are appended to `pc\data\logs\pumpboard.log` (rotated at 1 MB, 3 old files).

#### B.3.8 Lost link, reset, uncertainty

| Event | Detection | Action |
|---|---|---|
| link lost | reader ended; write failure; heartbeat + resync failed | `link.stop()`; state `lost`. If any motion was active, a motion command was unanswered, or a stop was pending → `motion_uncertain = true` + alarm `pump_stop_unconfirmed` (Emergency) “Pump control board lost connection. The pump may still be spinning. Immediately turn off power to the pump using the physical switch. ”; otherwise alarm `pump_link_lost`. The running operation fails. Reconnect starts at once |
| board reset | B.3.7-2 | the running operation fails (“The pump control board restarted by itself”); B.3.4 is repeated; alarm `pump_reset` (high) “The pump control board restarted itself. It could be the power supply or USB is unstable. The syringe pump needs to be reinitialized. ” |
| reply out of step | timeout | `resync()` |
| `motion_uncertain` | set as above | blocks every pump start and every `plasma_run` until a reconnect has passed B.3.4 |

#### B.3.9 `stop_all(reason)` — never waits for `op_lock`

1. Set `abort` of the running operation; clear all deadlines.
2. Connected: `motors.stop_all` (sets V7's weight-dose cancel flag, `X`, `W,STREAM,0`, `?`). `X` waits only for V7's `_tx_lock`,
   i.e. for the command in flight (≤ 3 s; ≤ 5 s behind `Y,INIT`).
3. If the syringe was busy or initialised: `pump.stop` (`Y,STOP`, awaited — `X` only fires `/1T` without confirmation).
4. Verify with `?`: groups `idle`, nothing selected, valves closed, not cleaning → `confirmed`.
5. Syringe bookkeeping: `initialised = false`, `volume_ul = None`. Guard `disarm`.
6. `scale.restart` after 1 s (B.3.7-7) — `X` switched the stream off.
7. Not connected: one immediate reconnect attempt (budget 8 s). Success → B.3.4 has sent `X` → `confirmed`. Failure →
   `{sent: false, confirmed: false}`.

Returns `{sent, confirmed, error}`. Not confirmed → alarm `pump_stop_unconfirmed` (Emergency)
“The pump stop command was not confirmed. Immediately turn off power to the pump using the physical switch. ”

#### B.3.10 Close and the guard

`PumpBoard.close(budget_s=2)`: `stop_all('exit')` → `link.stop()` → guard `bye`. (V7's `Link.stop()` sends nothing to the Mega.)

`pump_guard.py` — real mode only, started after the first successful connect:
`python -B pump_guard.py --vid 2341 --pid 0042 --serial <sn> --log <data>\logs\guard.log`, `stdin` = a pipe,
`creationflags = CREATE_NO_WINDOW` (it has no console, so closing the program's window does not end it).
- The parent writes lines: `port COM3` after every connect · `arm` when a continuous run or a weight dose starts · `disarm`
  when none is left · `bye` at a clean close.
- The guard reads lines. `bye` → exit. End of input (the parent died): **not armed → exit without touching the port.**
  Armed → wait 0.3 s; list the ports; the remembered port must still have the Mega id and the given identity; open it with
  pyserial at 115200 (retry for 10 s while the dead parent's handle is released); wait 2.5 s; write `X\n`; read up to 3 s for
  `OK,all_stopped`; append one line to the log; close; exit.
- It imports only the vendored pyserial. It honours `PLASMIC_NO_SERIAL=1` (exits). Never started in practice mode or tests.

#### B.3.11 Simulator selection and test safety (acting in this order)

| Layer | Mechanism |
|---|---|
| mode | `Application(data_dir, demo=False, auto_sync=True, pump_mode=None, pump_overrides=None, sim_port=8767)`; `pump_mode` default = `'sim'` if `demo` else `'off'`. `main()` passes `'real'` (only without `--demo`). In `'off'` the board is absent for every rule; the existing test harness therefore keeps working unchanged |
| policy | `pumpboard._real_ports_allowed = False` at import. `allow_real_ports()` sets it; **only `app.main()` calls it**. Building a non-simulated link without it raises `RealPortForbidden` before pyserial is touched |
| kill switch | environment `PLASMIC_NO_SERIAL=1` → `transport.serial` is the `_Forbidden` object whatever the mode says; `tests\pump_testing.py` sets it before any import; implementers set it in every shell they work in |
| choice | `enabled` is not `true` → no attempt; no port with the stored identity → no attempt |
| tripwire (tests) | `pump_testing.install_tripwire()` replaces `backend.transport.serial` by an object whose `Serial(...)` records the call and raises `AssertionError('a test tried to open a real serial port')` (it survives `start()` wrapping exceptions into `LinkError` because the **record** is asserted to be empty in `tearDownModule`), and the port lister by a simulated list (names like `COM91`) |
| static check (tests) | `tests\test_port_safety.py`: no file under `tests\` contains `allow_real_ports(`; no file under `pc\` except `app.py` calls it; `pumpboard.py` and `pump_guard.py` are the only files that import `serial`; no file names `COM3` |
| fault injection (tests) | `pump_testing.FaultPlan` patches methods of `backend.simulator.SimulatedMega` at run time (drop a reply, answer `ERR,…`, answer `PUMP,…,error=7`, emit `READY…`, make `readline` raise = cable pulled, flood `WEIGHT` lines); `link_factory` gives a fully scripted link for port-choice and identity cases. Vendored files are not edited |

#### B.3.12 V7 quirks and what the wrapper does about each

| Quirk | Wrapper |
|---|---|
| `motors.dose`, `motors.clean`, syringe moves return at once | completion waits of B.3.6 |
| no time limit for `V` anywhere; nothing sent on exit; no reconnect | deadlines, page watchdog, `close()`, guard, monitor thread |
| a second motor in a group silently stops the first; `motors.stop` for a non-selected motor answers `OK` and does nothing | `run` / `dose` refuse unless that group is `idle`; `stop_motor` verifies `idle` |
| `X` switches the weight stream off | `scale.restart` after every `stop_all` |
| `auto` port heuristics (CH340, first port) | never reachable (B.3.1) |
| partial reply = success; late reply taken for the next command | terminator check, `resync()` |
| `PUMP,…,error=n` ignored; nobody tracks the plunger | parsed by the proxy; host bookkeeping, void after every stop / reset |
| tare / weight dose not blocked by other motion | `scale_tare` and `dose_weight` need quiescent |
| after a reset the divisor is 1.0 (grams = raw counts) | reset detection + B.3.4; `dose_weight` needs `calibration_loaded` |
| `snapshot()` is a shallow copy shared with the reader | `view()` makes a deep copy |
| `[INFER]` a `P` move may cap later `V` speeds of that group; every `?` pauses stepping about 10 ms | warning W1; heartbeat only when no `STATE` for 2 s, waits poll at 1 s |

### B.4 `devices.py` — ownership, interlock, everything-off

**Ownership.** `owner` = `None` or `{kind: 'round' | 'manual' | 'tool', id, label, since}`.

| Caller | Acquires | Refused (`Busy`) when |
|---|---|---|
| Runner, start of a round | `round` | owner is `manual` or `tool` → slot `not_started` |
| ManualOps | `manual` | owner is `round` / `tool`; schedule status in `BUSY` (`waiting`, `running`, `pausing`, `stopping`); a round is paused. **Allowed while the schedule is `paused` between rounds** (as the original) |
| maintenance tools (manual plasma, spectrum check, `prepare`, `clear_fault`, parameters, scale Return to zero / Correction, pump settings, port change, reconnect, RPi address) | `tool` | the same |
| any stop | nothing | never refused |

A round keeps ownership while it is paused. Ownership is released in `finally`.
Texts: “Scheduled measurement is in progress: press ”Stop” first. STOP”,Or wait it out” (schedule) · “The process is paused: Please click ”Continue” at the bottom RESUME”or“stop STOP”” (paused round) · “Another operation is in progress” (manual / tool).

**High-voltage view** — `RpiLink.hv_view()` → `{known, idle, reasons[]}`, first matching row:

| Condition | Result |
|---|---|
| not connected | `known: false` “RPi Not connected” |
| the newest status was **received** more than 5 s ago (`status_age_s`, PC clock) | `known: false` “RPi status exceeds 5 seconds not received” |
| a job with `high_voltage` is running | `known: true, idle: false` “is discharging” |
| the RPi's own snapshot age `status.age_s` is `None` or > 10 s | `known: false` “RPi The device status read exceeds 10 No update in seconds” |
| `power.valid` or `nano.valid` not true | `known: false` “high voltage power supply/Nano is invalid” Security fix for |
| `power.run_stop_setting != 0` | `idle: false` “high voltage power supply is ON” |
| `power.voltage_v != 0` or `current_a != 0` | `idle: false` “High voltage output has not returned to zero yet” |
| `nano.state` not `IDLE` / `DONE` / `FAULT`, or `output_command_high` / `output_pin_sample` not false | `idle: false` “Nano is still being exported or is ready” |
| otherwise | `known: true, idle: true` |

**Mutual exclusion — pumps / syringe and high voltage never together** (runtime half; the static half is preflight R5):

| Direction | Where | Rule |
|---|---|---|
| before any pump / syringe motion | `Devices.permit_motion(ack=False)`, called by `ManualOps` and by `StepExecutor` for every motion step; **and** the `CheckedLink` gate for every wire command | refused (`Blocked`) when `hv_pending or hv_active`; RPi connected → `hv_view()` must be known and idle; RPi not connected → only with `ack` (the tick “RPi is not connected...I have gone to the site to confirm that the high-voltage power supply is turned off.” given in the start dialog of this schedule or of this manual start) |
| before `plasma_run` (Recipe step or maintenance) | `Devices.hv_session()` (context manager) | under `Devices.lock`: set `hv_pending`. Then: board connected → `PumpBoard.quiescent(fresh=True)` must hold (valves are given 3 s to close); board not connected → allowed only when it is not in use and not `motion_uncertain`. Fail → clear `hv_pending`, raise `Blocked` “The pump is still moving and cannot discharge.”. Pass → `hv_active = true` |
| after `plasma_run` | `hv_session().__exit__` | `hv_active` stays true until `hv_view()` is known and idle (polled up to 30 s). Not reached → stays true + alarm `hv_stop_unconfirmed` (Emergency) “The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately. ” |

Setting `hv_pending` **before** the pump check closes the race with a manual start arriving at the same moment: from then on
the gate refuses every new motion command.

**`stop_everything(actor)`** (the STOP button) and **`all_off(need_pump, need_rpi)`** (end of a failed or stopped round):

```
stop_everything(actor):
  1. runner.request_stop()     schedule -> 'stopping', no further slot; round stop flag          (instant)
  2. manual.cancel()           abort flag of the manual operation                                (instant)
  3. thread stop-pump: pumpboard.stop_all('stop')   (skipped only when the board is not in use and not uncertain -> "none")
     thread stop-rpi:  rpilink.stop()               (fresh TCP connection, no token)
  4. join both for 8 s; a half that has not answered is reported "pending" and reports through an alarm later
  5. event “Stop pressed: pump...; high pressure …” with the actor; alarms for every unconfirmed half
```

| Half | Result values | Alarm when not confirmed |
|---|---|---|
| pump | `confirmed` · `unconfirmed` · `pending` · `none` | `pump_stop_unconfirmed` (Emergency) |
| RPi | `confirmed` (idle: stop tool ran, OFF read back) · `cancel_sent` (a job was running; the RPi reads back and reports `post_stop`) · `unreachable` · `unconfirmed` · `pending` | `hv_stop_unconfirmed` (Emergency) when a discharge was running or the high-voltage state is not known idle: “The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm. ” The program of; otherwise priority middle with the same text |

`all_off()` is the same two halves; the RPi half is sent only when the Recipe has an RPi step or `hv_view()` is not known-idle.
STOP is never refused, never asks, never needs the PIN, and works when nothing is running.

**Interlock views** for `/api/state` (no I/O; pure functions over the latest views):
`interlock.manual` (may a manual pump / syringe operation start: board state, ownership, `hv_view`; `need_hv_ack` when the RPi
is not connected), `interlock.plasma` (may a maintenance discharge start: `flow_view().ok`, board idle rule, `status.ready`),
`interlock.start` (Recipe-independent blockers of Start scheduling). Each `{ok, reasons: [{code, text, fix}]}`.

### B.5 `runner.py` and `steps.py` — schedule and rounds

**Start** (`POST /api/schedule/start`), checks in this order; the first failing stage answers with all its reasons:
1 an operator is selected (400) · 2 no schedule is active (409) · 3 the Recipe exists, its SHA-256 matches, `recipe.validate()`
passes with the current settings (400) · 4 ticks: `confirm.high_voltage` when `uses_plasma`; `confirm.liquid` when `uses_pump`;
`confirm.hv_off_checked` when `uses_pump` and the RPi is not connected (400 “You must first check each item in the confirmation window.”) · 5 the start checks of A.4
(409 `blocked`) · 6 `schedule.validate(raw)` and the interval rule of A.5 (400). Then the record is created and written, and
the `runner` thread starts. Event (the 1.3.0 sentence, extended):
“Scheduled measurement starts:{Recipe},Every N minutes, first time HH:MM,common n times,{V} V (High voltage confirmed)”.

**Loop** — the structure of 1.3.0's `Schedule._loop` with the same slot rules, `START_FAILURES = 3`, `KEEP_SLOTS = 2000`:

```
for each slot index:
    wait_until(due)                 # stop -> schedule 'stopped'
                                    # paused and now > due + 5 s -> slot 'skipped_paused', next index
    try:    status, note = run_round(index)
    except SlotNotStarted as e:     slot 'not_started'; 3 in a row -> schedule 'failed' “Continuous 3 times:…”
    done    -> next slot (slots that passed meanwhile: 'skipped_overrun', never run late)
    stopped -> schedule 'stopped'
    failed  -> schedule 'failed'    “No. n measurements did not end normally and the schedule has been stopped:…”
```

**One round** (`run_round`):
1. `devices.acquire('round')`; start checks of A.4 → `SlotNotStarted(text)` (nothing has moved yet).
2. Expand the stored CSV; create the round record (`status: running`), write it; slot `running`.
3. For each step: `checkpoint()` → mark `running` (+ `rev`) → `StepExecutor.run()` → mark `done` with its result. The record
   is written at each device step and at most once a second for `delay` steps.
4. Exceptions: `Stopped` → `stopped`; anything else → `failed` with “No. N step ({Name}) failed:…”.
5. `finally`: `completed` and `uses_pump` → `pumpboard.quiescent(fresh=True)` must hold, else `stop_all`, status `failed`
   (“The pumps did not stop at the end of one round, they all stopped.”). `failed` / `stopped` → `devices.all_off()`; its report goes into the record
   (`stop_report`); an unconfirmed half forces `failed` and raises the alarm. Remaining steps → `skipped`. Final record
   written, one line appended to `runlog.jsonl`, ownership released, event “Process{Complete｜failed｜Stopped}: {Recipe} (No. n/N times){reason}”,
   alarm `round_failed:<id>` when failed.

**`checkpoint()`** — before every step and every 0.1 s inside `delay`: stop → raise `Stopped` · `deadline_tripped`, board
`lost`, board reset (only when the Recipe uses the board) → raise `StepFailed` · pause → `hold()`.

**`hold()` — PAUSE**
1. Every continuous pump of this round: `used += now − started`; `pumpboard.stop_motor()`.
2. Status `paused` (round and schedule); event “process has been suspended” (+“; StoppedPump 1, 8,will restart when continuing”).
3. Wait for resume or stop. Ownership is kept: manual starts stay locked.
4. Resume: `devices.permit_motion()` again; each paused pump restarts with the same flow and direction and the deadline
   `planned − used + 5 s`; a `delay` continues with its remaining time; status `running`; event “Process continues”.

A pause requested during a blocking step shows `pausing` until that step ends. Not interrupted by PAUSE: `plasma_run`,
`spectrum_capture`, both doses, `dose_weight`, `clean`, every syringe step, `rpi_status`, `nano_prepare`, `nano_stop`.
A pause while the schedule waits for a slot: status `paused` at once, no owner (manual work allowed).

**Step details**

| Step | Executor |
|---|---|
| `pump_on` / `pump_reverse` | `permit_motion(ack)`; `pump.run(motor, flow, ±1, planned_s + 5, 'round')`; remembered in `round.pumps[motor] = {flow, direction, planned_s, used_s, started}`; result `{sps, flow_ml_min, direction, planned_s}` |
| `pump_off` | `pump.stop_motor(motor)`; result `{ran_s}` |
| `dose_volume` / `dose_reverse` / `dose_weight` / `clean` / syringe steps / `scale_read` | `permit_motion(ack)` (not for `scale_read`); the `PumpBoard` method of B.3.6 with the round's `abort`; result = its return value (`scale_read`: `{grams, name}`; also appended to `round.weights`) |
| `rpi_status` | `rpi.command('status', fresh=True)`; `hv_view()` must be known and idle, else fail with its reasons |
| `nano_prepare` | `rpi.command('prepare')` |
| `plasma_run` | `flow_view().ok` (else `rpi_old` text) → `with devices.hv_session():` → `rpi_ready()` → `rpi.command('plasma_run', attended=False, claim=True, confirm_high_voltage=True)` → job id into the step and the record → `rpi.wait_job(job, stop)` → `done`: `rpi.fetch_result(job)`, `store.save_run(result, round_id=…, recipe_name=…, operator=…, slot=…, source='round')`; a read that fails twice fails the step with “The discharge is completed, but the spectrum reading fails: ...(the data is still RPi) ”; `stopped` / `failed` as `wait_job` says. Result `{job, voltage_v, flow, background_frames, signal_frames, wait_s, peak_nm}` |
| `spectrum_capture` | integration = `snapshot.parameter.integration_ms` (missing → “Unable to read plasma parameters”); `rpi.command('spectrum', claim=True, integration_ms=…)` → `wait_job(limit_s = 3 × ms ÷ 1000 + 60)` → `fetch_result` → `save_run(source='round')` |
| `nano_stop` / `power_off` | `rpi.stop()` |

`round.current.detail` (the live line on Overview): “Waiting 12/30 Second” · the step sentence for pump starts · “{motor} Quantitative 5 mL · already 8 Second/about 17 Second” ·
“{motor} Weighing Quantification · at present +3.20 g/Target 10 g” · “automatic cleaning · {water valve｜Water ingress｜Water inlet and discharge｜finally emptied} · already 40 Second/about 122 Second” ·
“Syringe pump initializing” / “syringe pump {Liquid extraction｜Liquid injection} Valve position 2 · 1 mL” · `plasma_run` by the RPi's `job.progress.phase`: `prepare` “Prepare” ·
`background` “captures the background spectrum (high voltage is not turned on yet)” · `ramp` “Boost” · `signal` “Discharging·Signal {done}/{total} open” · `off` “Turn off the high voltage and confirm” ·
`stopping` “Stopping, waiting for readback of high voltage shutdown” · `finishing` “Read Spectrum” · others: the step sentence.

**STOP inside each step type**

| Step | How STOP reaches it | Worst-case delay |
|---|---|---|
| `delay` | flag | 0.1 s |
| `pump_on` / `pump_off`, doses, `clean` | `X` from `stop_everything` (the wait sees `abort`) | the command in flight, ≤ 3 s |
| `dose_weight` | V7's cancel flag (set by `motors.stop_all`) + `X` | ≤ 3 s for the pump; the V7 call may return up to 5 s later (its settle sleep after a top-up is not cancellable `[FACT motors.py:468]`) — the pump is already stopped |
| syringe step | `X` (fires `/1T`) + `Y,STOP` (confirmed) | ≤ 3 s (5 s behind `Y,INIT`) |
| `plasma_run`, `spectrum_capture` | RPi `stop` on a fresh connection = one SIGTERM to the tool, which runs its own OFF sequence; the step waits for the job to end (≤ 60 s) | the RPi's own shutdown time |
| `rpi_status`, `nano_prepare`, `nano_stop` | not interruptible; the RPi `stop` queues behind them | their reply time |

A stopped or failed round is never continued; the next start begins at step 1. A discharge is never repeated automatically.

**Restart.** At program start `Runner` loads the schedule record and the newest round record: schedule in an active state →
`interrupted` (the 1.3.0 text “The program is restarted, the schedule has been interrupted and will not automatically resume. Please restart the schedule after confirming the device. ”), its `running` slot → `failed`;
round `running` / `pausing` / `paused` / `stopping` → `interrupted` with the note “The running program was closed or the computer was restarted; please go to the site to confirm the device status.”,
one `runlog` line. If either was found: alarm `sched_interrupted:<id>`; one RPi `stop` when that round's Recipe had RPi
steps and the RPi is reachable; the board gets its `X` at connect anyway.

### B.6 Schedule (reuse of `pc\schedule.py`)

`schedule.py` stays the time arithmetic (`validate`, `slot_time`, `last_index`, `planned_total`, `next_due_index`, `MINUTE`,
`local_now`). Changes: `ACTIVE = ('waiting', 'running', 'pausing', 'paused', 'stopping')`; new
`BUSY = ('waiting', 'running', 'pausing', 'stopping')`; `STATUS_LABEL` gains `pausing: Paused`, `paused: Suspended`; `SLOT_LABEL`
gains `skipped_paused: skip (scheduling pause)`. The existing labels keep their 1.3.0 words (`Waiting for the next time`, `under measurement`, `Stopping`, `Completed`,
`Stopped`, `stopped due to failure`, `Interrupted`; slots `under measurement`, `Complete`, `failed`, `Stopped`, `not started`, `Skip (last timeout)`). New in `schedule.py`:
`ROUND_LABEL = {running: is executing, pausing: Paused, paused: Suspended, stopping: Stopping, completed: Complete, failed: failed, stopped: Stopped, interrupted: Interrupted}`
and `STEP_LABEL = {pending: —, running: is executing, done: Complete, failed: failed, stopped: Stopped, skipped: not executed}`. The page gets all
four tables from `/api/bootstrap` (one source for every status word).

A single measurement = a schedule with `end: {mode: 'count', count: 1}`. There is no other way to run a Recipe.
RESUME is refused while a manual pump still runs (“The manually started pump is still running, please stop it first.”), while the high voltage is not idle, or
while the board is lost.

### B.7 `manual.py` — manual operation

One manual operation at a time (`devices.acquire('manual')`). Every start passes `devices.permit_motion(ack)`.

| Operation | Runs | Ends |
|---|---|---|
| `pump_on` | inline in the request (`PumpBoard.run`), then returns | by `pump_off`, its deadline (1–300 s, chosen on the page), the page watchdog, STOP. Ownership is held while any manual run is active. A second `pump_on` in the **other** group is allowed |
| `pump_off` | inline; always accepted, for any owner | — |
| `dose_volume`, `dose_weight`, `clean`, `syringe_init`, `syringe_move` | on the `manual-op` thread; the request answers `{accepted, op_id}` at once; progress in `state.pump.op` | completion, error (page message + event), STOP, page watchdog |

Manual operations record the current operator name (may be empty) in the event log; they do not need an operator.
Scale Return to zero is not a manual operation: a weight dose takes its own zero, so tare is only needed for calibration and lives on
Maintenance › Pump and Scale.

**Maintenance tools on the RPi** (`Application.rpi_tool`, `POST /api/rpi/tool`): `prepare`, `clear_fault` and the parameter save
hold ownership `tool` for the length of the request. `spectrum` and `plasma_run` acquire `tool` (for `plasma_run` also
`flow_view().ok`, `devices.hv_session()` and `rpi_ready()`), send the command **attended and not claimed** — so the link
thread's 1.3.0 page watchdog stops the discharge when the page goes silent for 10 s, and the link thread saves the result as
in 1.3.0 (meta `source: 'manual'` for a discharge, `'check'` for a capture) — and return the RPi's reply to the request at
once; a daemon thread `tool-job` keeps the ownership and the high-voltage session until `wait_job` reports the end of the job.

### B.8 Operators

`operators.json` `{operators: [names]}`: at most 50, each 1–20 characters, no control characters, none of `< > " \`, duplicates
dropped. Edited as a whole list (PIN). Seeded with one neutral entry “Operator” at migration so the first measurement after the
upgrade needs no maintenance session. The current operator is in memory, `''` after a restart, shared by all tabs, required
to start a schedule, written into every schedule, round and event (`actor`). Removing the current operator clears the selection.

### B.9 Alarms and events

`Alarms.raise_(key, priority, area, text, kind)` · `clear(key)` · `ack(ids | all)` · `view()`. Persisted in `alarms.json`.
`kind`: `condition` (raised and cleared by its owner; at program start every stored `condition` alarm is marked cleared and
its owner raises it again if it still holds) · `event` (cleared the moment it is acknowledged) · `latched` (cleared by the
success named below). An alarm that clears while unacknowledged stays listed as “Restored·Unconfirmed” until acknowledged;
acknowledged and cleared → removed. Raise, clear and ack each write an event.

| key | level | area | kind | Raised when | Cleared when | Text |
|---|---|---|---|---|---|---|
| `pump_stop_unconfirmed` | Emergency | pump | latched | `stop_all` not confirmed; link lost with `motion_uncertain` | a connect passed B.3.4 | B.3.8 / B.3.9 |
| `hv_stop_unconfirmed` | Emergency (or middle, B.4) | high pressure | latched | B.4; a job's `post_stop.ok` false | `hv_view()` known and idle | B.4 |
| `pump_link_lost` | high if a busy schedule uses the board, else middle | pump | condition | link `lost` / `missing` after it had been connected | connected | “The connection to the pump control board is interrupted and the program is reconnecting.” |
| `rpi_link_lost` | high if a busy schedule uses the RPi, else middle | RPi | condition | disconnected after it had been connected | connected | “RPi The connection was interrupted and the program is reconnecting.” |
| `pump_reset` | high | pump | event | unexpected `READY` | ack | B.3.8 |
| `pump_wrong_device` | high | pump | condition | identity check failed | next successful connect or a new choice | B.3.3 |
| `syringe_fault` | high | syringe pump | latched | `PUMP,…,error=n` | next successful `syringe_init` | “Syringe pump report error n ({illustrate}) ” |
| `nano_fault` | high | high pressure | condition | `nano.fault != 'NONE'` | fault `NONE` | “Nano Fault: {Heartbeat interruption｜trigger timeout｜work timeout｜code}.Ask maintenance personnel to go to ”Repair › Plasma Parameters” cleared. ” |
| `round_failed:<id>` | high | Process | event | a round ended `failed` | ack | “process failed:{error}” |
| `sched_failed:<id>` · `sched_interrupted:<id>` | high | Schedule | event | schedule ended so | ack | “Schedule has stopped:{error}” · “The schedule has been interrupted and will not be automatically restored. Please restart the schedule after confirming the device. ” |
| `sched_skipped:<id>` | middle | Schedule | event | first `skipped_overrun` of a schedule | ack | “Sometimes the segment is skipped because the last time has not ended yet.” |
| `slot_not_started:<id>` | middle | Schedule | event | a slot `not_started` (text updated) | ack | “No. n times failed to start:{note}” |
| `weight_tolerance:<round>:<n>` | middle | scales | event | `dose_by_weight` returned `within_tolerance: false` | ack | “Weighing quantity exceeds the allowable range: target x g,Actual y g” |
| `scale_offline` | middle | scales | condition | B.3.7-7 | samples arrive | “The scale did not respond” |
| `firmware_mismatch` | middle | pump | event | B.3.3 | ack | “The firmware settings of the pump control board are different from those on the computer (the firmware may be an old version). The cleaning sequence is subject to firmware. ” |
| `page_lost` | middle | picture | event | a page watchdog fired | ack | “The operation screen exceeds 10 No response for seconds,Manually started pump stopped” / “…has automatically stopped discharging” |
| `rpi_old` | middle | RPi | condition | connected and `hello.flow` missing | flow present | “RPi is still an old version: please double-click first Install.cmd renew RPi” |
| `rpi_flow_fallback` | middle | RPi | condition | `flow == 'verified'` | `unified` | “RPi Set to the old measurement process: Background 3 sheets, click to make approx. Stop automatically after 11 seconds to discharge” |
| `ui_stale` | Emergency | picture | browser only | no fresh `/api/state` for 5 s | next good poll | “The program did not respond: The data on the screen has expired, and the actual status of the device is unknown.” |

Events: `events.jsonl`, one `{time, level, text, actor}` per line (`level` `info` / `warn` / `error`; `actor` = operator
name, “Maintenance” for a request with a valid PIN session, “system” On, or `''`). Old 1.3.0 lines without `actor` stay readable (shown as “system” On).

### B.10 PIN lock

`PinLock` and `pin.json` as today (PBKDF2-HMAC-SHA256, 600 000 iterations, 32-byte salt, 6–8 digits, 5 wrong → 30 s
doubling to 900 s, session 600 s sliding, header `X-Unlock`, set only by `Set-PIN.cmd`). Changes:

| Topic | 2.0.0 |
|---|---|
| no PIN file | **Maintenance is closed** (1.3.0: open). Every gated route answers 403 `pin_unset`. The page says how to run `Set-PIN.cmd`; it takes effect without a restart (`configured` is checked per request) |
| PIN file replaced | all sessions end (file time and size are compared at every check) |
| what renews a session | only a **gated POST** that succeeds the gate. `peek(session)` reports without renewing; `/api/state` uses it. The page also locks itself after 600 s without pointer or key input (“Maintenance mode has automatically logged out”) |
| practice mode | `PinLock(None, practice_pin='000000')`: fixed PIN 000000, shown on the login panel; **no file of `pc\data` is read** |
| `--set-pin` | prints “Maintenance PIN has been saved. forget PIN , double-click again Set-PIN.cmd Set new. ” (the 1.3.0 hint to delete `pin.json` would now close Maintenance) |
| session on the page | `sessionStorage['plasmic.unlock']` |

| Action | PIN |
|---|---|
| Look at Overview / Schedule measurement / manual operation / execution record / alarm; a round's detail, spectrum and CSV; read the guide | no |
| Choose the operator | no |
| Schedule preview / start (also with high voltage, after the tick boxes) / PAUSE / RESUME | no |
| **STOP** | **never** |
| Acknowledge alarms | no |
| Manual: motor ON / OFF, Quantitative (volume, weight), automatic cleaning; syringe initialization / Liquid extraction / Liquid injection | no |
| Enter Maintenance (any tab) | yes |
| Recipe: import + check, publish | yes |
| Recipe: delete | yes, **and the PIN is typed again** |
| connection: RPi address / port / token; automatic search RPi; connection diagnosis; pump-board port, “does not use the pump control board”, Reconnect | yes |
| plasma parameters: save the seven parameters; synchronized to Nano; Clear Nano fault; ⚠ manual start plasma (+ tick box) | yes |
| Pump and Scale: Syringe capacity, Syringe pump speed; scale Return to zero and Correction | yes |
| spectrum: capture spectrum (no discharge) (the frame-by-frame view sits on the same tab) | yes |
| operator: edit the list | yes |

### B.11 Threads, locks, start and exit

| Thread | Created by | Lives | Does |
|---|---|---|---|
| main | `main()` | program | `serve_forever()` |
| request threads (daemon) | `ThreadingHTTPServer` | one request | call `Application` methods; none blocks longer than 8 s except `/api/rpi/tool`, `/api/parameters`, `/api/rpi/find` (RPi reply times, as today) |
| `rpi-link` (daemon) | `RpiLink` | program | connect / reconnect, `status` every 1 s, attended-discharge page watchdog, saving finished unclaimed jobs |
| `pump-monitor` (daemon) | `PumpBoard.start()` | program | B.3.7 |
| `serial-reader` (+ `sim`) (daemon) | V7 `Link.start()` | one connection | V7's reader |
| `runner` (daemon) | `Runner.start()` | one schedule | slots, rounds, steps |
| `manual-op` (daemon) | `ManualOps` | one blocking manual operation | |
| `stop-pump`, `stop-rpi` (daemon) | `Devices.stop_everything()` | one STOP | the two halves in parallel |
| guard (process) | `PumpBoard` after a real connect | program | B.3.10 |
| RPi simulator (process) | `Application` in practice mode | program | `python -B plasmic_tcpd.py --sim …` (as today, plus `-B`) |

| Lock / event | Guards | Rule |
|---|---|---|
| V7 `Link._tx_lock` | one wire command at a time | never taken by us directly |
| `PumpBoard.op_lock` (non-blocking acquire) | one motion-starting operation at a time | **stops never take it** |
| `PumpBoard.lock` (RLock) | wrapper fields | held for microseconds, never across `send` |
| `Devices.lock` | `owner`, `hv_pending`, `hv_active` | check-and-set only, never across I/O |
| `Runner.lock` (RLock) | schedule record, round record | copies are made for disk and for `state()` |
| `Runner.stop_request`, `pause_request` (Events) | — | set by HTTP threads, read by the runner thread |
| `RpiLink.request_lock`, `.lock` | as today | STOP uses a fresh connection, so it never waits for `request_lock` |
| `Store.lock`, `Alarms.lock`, `PinLock.lock` | their data | leaf locks |

Lock order when two are needed: `Runner.lock` → `Devices.lock` → `PumpBoard.lock` → leaf locks. No device I/O under
`Runner.lock` or `Devices.lock`.

Start order in `Application.__init__`: `Store` (migration) → `Alarms`, `PinLock` → RPi simulator (practice mode) → `RpiLink`
(starts connecting) → `PumpBoard(...)` (not yet started) → `Devices` (gives the board its HV gate) → `PumpBoard.start()`
(connects in the background; the page is usable meanwhile) → `Runner` (restart handling) → `ManualOps`.

`Application.close(fast=False)`: `runner.request_stop()` → `manual.cancel()` → in parallel `pumpboard.close()` (sends `X`)
and, only when a job of this program runs or the high voltage is not known idle, `rpilink.stop()` → join the runner (5 s) →
close the links → end the simulator.

| Way the program ends | Covered by |
|---|---|
| Ctrl+C, normal return | `finally: app.close()` |
| the console window is closed, log-off, shutdown | `ctypes.windll.kernel32.SetConsoleCtrlHandler` in `main()`: on `CTRL_CLOSE_EVENT`, `CTRL_LOGOFF_EVENT`, `CTRL_SHUTDOWN_EVENT`, `CTRL_BREAK_EVENT` run `app.close(fast=True)` — pump `X` within 1.5 s first, RPi stop (3 s) in parallel; console line “Stopping pump and high pressure...” (Windows allows about 5 s) |
| killed (Task Manager), crash | pump side: the guard (when armed). Plasma side: the RPi's own watchdog (`owner_disconnected`) |
| PC power loss | nothing can be sent (section J) |

### B.12 Failure cases — these rows are the acceptance tests of `test_runner.py` / `test_interlock.py` / `test_manual.py`

| Case | What happens |
|---|---|
| Board disappears during a dose | reader ends → `lost`, `motion_uncertain`, Emergency alarm, immediate reconnect (a successful open resets the Mega `[INFER]`, then `X`). Step fails → round `failed` → RPi half of `all_off` when needed → schedule `failed` |
| Board disappears during `dose_weight` | V7 raises after 4 s without samples, its final `V,m,0` cannot be sent; the wrapper replaces V7's "pump stopped" text; same path |
| Board resets by itself mid-step | reset flag → step fails, B.3.4 again, alarm high; round fails |
| RPi link drops during `plasma_run` | RPi: `owner_disconnected` → SIGTERM → OFF sequence by itself. PC: `wait_job` tolerates 30 s without status; on reconnect it reads the job (`cancelled`); round `failed`; pumps get `all_off`; schedule `failed`; `post_stop` not ok → Emergency alarm |
| RPi link drops while a Recipe is in its pump steps | pumps already running keep their deadlines; the next motion step is refused by `permit_motion` (nobody ticked the box for this case) → round `failed`, `all_off` |
| Page closes during a manual continuous run | page watchdog after 10 s → `stop_all`; in any case the run's own deadline |
| Page closes during a schedule | nothing: rounds are unattended by design; every run has its deadline, every step its limit |
| Two browser tabs | both show the same server state (operator, alarms, schedule). Commands are serialised by ownership. STOP from either tab works. The PIN session is per tab |
| Program restarts mid-round | `interrupted`, never resumed; `X` at connect; the guard already sent `X` if it was armed |
| Recipe leaves a pump running | refused by preflight R7. If it still happened: end-of-round quiescence check → `stop_all` → round `failed` |
| STOP while the board link is down | reconnect attempt inside `stop_all`; not confirmed → Emergency alarm telling the operator to cut the power |
| A manual start and a round's `plasma_run` at the same moment | `hv_pending` is set first; the `CheckedLink` gate refuses the motion command; or the pump check of `hv_session` fails and the round fails before any high voltage (200 rounds of this race in `test_interlock.py`: never both active) |
| RESUME while a manual pump still runs | refused |
| RPi still runs the 1.3.0 program | connects, shows everything, alarm `rpi_old`; every `plasma_run` (round and maintenance) refused |

### B.13 Frozen interfaces (signatures only; the packages build against these)

```python
# pumpboard.py
allow_real_ports(); class RealPortForbidden(RuntimeError); class PumpBoardError(RuntimeError): code, text
PumpBoard(data_dir, mode, store, on_event, on_alarm, ui_age, overrides=None, link_factory=None, port_lister=None)
  .set_gate(hv_blocks_motion)  .start()  .close(budget_s=2)  .view() -> dict (API.md state.pump)  .hardware() -> dict (bootstrap.hardware)
  .ports() -> {ports: [...], other_count}  .configure(enabled, device=None)  .reconnect()  .apply_settings(syringe_ul, syringe_speed_ml_s)
  .in_use -> bool  .connected -> bool  .motion_uncertain -> bool  .quiescent(fresh=True) -> (bool, [reason])  .deadline_tripped(owner) -> str | None
  .run(motor, flow_ml_min, direction, limit_s, owner)  .stop_motor(motor)
  .dose(motor, volume_ml, direction, owner, abort)  .dose_weight(motor, target_g, owner, abort)  .clean(owner, abort)
  .syringe_init(owner, abort)  .syringe_speed(v)  .syringe_move(kind, position, volume_ml, owner, abort)
  .scale_read(samples=5)  .scale_tare()  .scale_calibrate(grams)  .stop_all(reason) -> {sent, confirmed, error}
# recipe.py
decode(data: bytes) -> (text, encoding)   validate(text, hardware, syringe_ul=5000) -> dict   estimate(result, hardware, rpi_estimate_s, syringe_speed) -> float
ACTIONS: dict   EXAMPLES: [{file, name, note}]
# store.py
Store(data_dir): .dir .config .save_config(raw) .event(text, level='info', actor=None) .events(before=None, limit=50) .events_rev
  .operators() .set_operators(names) .pump_settings() .save_pump_settings(d)
  .recipes() .recipe(id) .publish_recipe(name, csv, summary, source) .delete_recipe(id)
  .save_round(record) .round(id) .newest_round() .append_runlog(row) .runlog(before=None, limit=50)
  .save_schedule(record) .load_schedule() .save_run(result, **meta) .list_runs(limit=200) .load_run(job) .run_ids()
# alarms.py / pinlock.py
Alarms(store): .raise_(key, priority, area, text, kind='event') .clear(key) .ack(ids=None) .view() -> dict (state.alarms)
PinLock(path, practice_pin=None): .configured .set(pin) .unlock(pin) -> session .allowed(session) -> bool (renews) .peek(session) -> seconds left | 0 .verify(pin) .lock_session(session)
# rpilink.py
RpiLink(store, search=True, auto_sync=True): .view() .latest(max_age=10.0) .hv_view() .flow_view() .params_view()
  .command(cmd, attended=True, claim=False, **fields) .rpi_ready() .wait_job(job_id, stop_event=None, limit_s=360, lost_s=30) -> (status, note)
  .fetch_result(job_id) .stop() .reconnect() .ui_alive() .close()      rewrite_fix(text) -> text
# devices.py
class Busy(RuntimeError); class Blocked(RuntimeError): reasons
Devices(store, alarms, rpi, pump): .bind(runner, manual) .acquire(kind, id, label) .release(id) .owner
  .permit_motion(ack=False) .hv_session() .hv_blocks_motion() -> bool .all_off(need_pump, need_rpi) -> report
  .stop_everything(actor) -> report .start_problems(facts, ack=False) -> [reason] .view() -> dict (state.interlock, state.owner)
# runner.py / manual.py
Runner(store, alarms, devices, rpi, pump, operator_getter): .preview(body) .start(body) .pause() .resume() .request_stop()
  .view() -> schedule | None .round_view() -> dict | None .busy -> bool .close()
ManualOps(store, devices, pump, operator_getter): .submit(body) -> dict .cancel() .active -> bool
```


---

## C. HTTP API

See `API.md` (every route, request, reply, error, PIN yes / no, the complete `/api/state`, static-file rules).

---

## D. Disk layout

### D.1 Files under `pc\data` (practice mode: `pc\data-demo`, same layout plus `sim\` as today)

| Path | New | Written by | Notes |
|---|---|---|---|
| `config.json` | | installer, Maintenance › connection | `{rpi_host, rpi_port, token}` — **unchanged**, so `Install.cmd` keeps working |
| `pin.json` | | `Set-PIN.cmd` | `{salt, digest, iterations}` — unchanged |
| `version.json` | NEW | migration (last step) | `{"data_version": 2, "program": "2.0.0", "migrated_from": "1.3.0" \| null, "migrated_at": iso}` |
| `operators.json` | NEW | migration, Maintenance › operator | `{"operators": ["operator"]}` |
| `pumpboard.json` | NEW | migration, Maintenance › connection / Pump and Scale | B.3.2 |
| `alarms.json` | NEW | `Alarms` | `{"next_id": 18, "alarms": [{id, key, priority, area, text, kind, since, acked, acked_at, cleared, cleared_at}]}` (active and cleared-unacknowledged only) |
| `events.jsonl` | | `Store.event` | `{time, level, text, actor}`; append only |
| `schedule.json` | | `Runner` | current / last schedule (D.2); 1.3.0 keys kept, keys only added |
| `runlog.jsonl` | NEW | `Runner`, migration | one line per finished round and per slot that did not run; source of execution record |
| `recipes\<32 hex>.json` | NEW | Maintenance › Recipe, migration | D.2; the SHA-256 of the CSV text is re-checked at every load (“Recipe file is different from when published, please re-publish”) |
| `recipes\deleted\<id>.deleted-<YYYYmmdd-HHMMSS>.json` | NEW | delete | moved, never erased |
| `rounds\ROUND-<32 hex>.json` | NEW | `Runner` | D.2; written at the start, during the round, and at the end |
| `runs\<job>.json`, `runs\<job>.meta.json` | | `RpiLink`, `StepExecutor` | spectrum sets as today. Meta gains `flow`, `round_id`, `recipe_name`, `operator`, `slot`, `source` (`round` / `manual` / `check`); old files (with `sequence`) stay readable |
| `pumpboard\scale_calibration.json` | NEW | V7 `scale.py` (through `SCALE_CALIBRATION_FILE`), migration seed | V7's shape `{version: 1, saved_at, offset, divisor, reference_grams}`; V7 writes it with `.tmp` + `os.replace` |
| `logs\pumpboard.log` (+ `.1`…`.3`) | NEW | `PumpBoard` | serial lines sent and received (no `WEIGHT`), 1 MB each |
| `logs\guard.log` | NEW | guard | one line per action |
| `backup\from-1.3.0-<stamp>\` | NEW | migration | copies of what existed |

Writing: every JSON file through `write_json` (`<name>.tmp`, then `replace`, 10 retries on `PermissionError`). JSONL: open
for append, one `write`, `flush`; readers skip a broken last line. **Nothing under `pc\data` is ever deleted by the program.**
Ids are `uuid4().hex`; every id that arrives in a URL is matched against `[0-9a-f]{32}` (spectra: `RUN_ID`,
`(?:HMI|CAP|LITE)-[0-9a-f]{32}`) before any file name is built.

### D.2 Record shapes

**Recipe** (`recipes\<id>.json`):
`{"id", "name", "created_at", "source": "seed" | "import", "csv": "action,target,value,unit,note\n…", "sha256", "step_count", "rounds", "uses_pump", "uses_rpi", "uses_plasma"}`.
Names are unique (exact match): “already has the same name Recipe”.

**Schedule** (`schedule.json`):

```jsonc
{"version": 2, "id": "…", "status": "waiting", "created_at": "…", "finished_at": null,
 "operator": "Wang Xiaoming",
 "recipe": {"id": "…", "name": "Only discharge measurement", "sha256": "…"},
 "uses_pump": false, "uses_rpi": true, "uses_plasma": true,
 "confirmed": {"high_voltage": true, "liquid": false, "hv_off_checked": false},
 "voltage_v": 300, "estimate_seconds": 21,
 "spec": { /* schedule.validate(): first_slot, interval_seconds, interval_minutes, start, end */ },
 "planned_total": 6, "last_slot_at": "…", "next_index": 0, "next_slot_at": "…",
 "slots": [{"index": 0, "planned_at": "…", "status": "done", "round_id": "…", "job": "HMI-…", "started_at": "…", "finished_at": "…", "note": ""}],
 "counts": {"done": 1}, "error": ""}
```

**Round** (`rounds\ROUND-<id>.json`) — keeps what a later analysis (Correction, Trend) needs; the original kept neither the CSV nor the parameters.

| Field | Content |
|---|---|
| `id`, `version: 1`, `status`, `demo`, `started_at`, `finished_at`, `error`, `rev` | |
| `schedule_id`, `slot` (1-based), `slots_planned`, `planned_at` | |
| `operator` | name at the start of this round |
| `sample_kind` | always `"sample"` in 2.0.0 (reserved: `"blank"`, `"standard"`) |
| `recipe` | `{id, name, sha256, csv}` — the CSV text that ran |
| `step_count`, `rounds`, `phases[{name, from, to}]` | |
| `steps[]` | `{n, line, action, name, target, target_label, value, unit, note, round, rounds, phase, text, planned_s, status, started_at, finished_at, result, error}`; `result` per action: dose `{steps, sps, eta_s, elapsed_s}` · weight dose `{target_g, dispensed_g, error_g, within_tolerance, topups, elapsed_s, baseline_g}` · run `{sps, flow_ml_min, direction, planned_s}` · stop `{ran_s}` · syringe `{position, volume_ul, speed_ml_s}` · `scale_read` `{grams, name}` · `plasma_run` `{job, voltage_v, flow, background_frames, signal_frames, wait_s, peak_nm}` |
| `plasma` | `{"parameters": {7 values}, "flow": "unified", "jobs": [{job, kind, state, voltage_v, wait_s, lead_ms, background_frames, signal_frames, integration_ms, peak_nm, peak_value, saved}]}`; the frames are in `runs\<job>.json`, the raw files stay on the RPi under that job id |
| `weights[]` | `{n, name, grams}` from `scale_read` |
| `board` | only when the Recipe uses the board: `{simulated, device, firmware_banner, firmware_match, hardware_sha256, pulses_per_ml, dose_pulses_per_ml, dose_flow_ml_min, syringe_ul, syringe_speed_ml_s, scale_calibration{divisor, offset, saved_at}}` |
| `versions` | `{pc, rpi, v7_backend}` |
| `stop_report` | `{pump{result, error}, rpi{result, error}}` when the round did not complete |
| `paused[]` | `{at, resumed_at}` |
| `messages[]` | last 200 `{time, text}` |

**Run log line** (`runlog.jsonl`):
`{kind: "round" | "slot" | "legacy", time, operator, recipe_name, schedule_id, slot, slots_planned, status, finished_at, round_id, spectra: [job…], result_text, note}`.
`result_text`: “highest peak 337.1 nm · signal 8 open” · “CuSO₄ 10.02 g” · “No measurement steps” · ”Not measured” (slot rows).

### D.3 Migration from 1.3.0 (`migrate.run(data_dir, vendor_dir, recipes_dir)`; runs at every start, does nothing when `version.json` says `data_version: 2`; safe to run again)

| # | Step | Detail |
|---|---|---|
| 1 | backup | when `config.json`, `schedule.json` or `events.jsonl` exist: copy `config.json`, `schedule.json` and the last 2 MB of `events.jsonl` to `backup\from-1.3.0-<stamp>\` |
| 2 | `operators.json`, `alarms.json` | created when missing (`{"operators": ["operator"]}`, empty alarm list) |
| 3 | `pumpboard.json`, `pumpboard\scale_calibration.json` | `{version: 1, enabled: null, syringe_ul: 5000, syringe_speed_ml_s: 0.4}`; the calibration file is copied from `pc\vendor\v7\config\scale_calibration.json` when missing — **in every data folder** (real, practice, tests), so the "calibration loaded" rule runs everywhere |
| 4 | `recipes\` | created; when it holds no Recipe, “Discharge measurement only” is published from `pc\recipes\plasma-only.csv` (`source: "seed"`) |
| 5 | `schedule.json` | when it has no `version`: add `version: 2`, `operator: ""`, `recipe: {id: null, name: "Discharge measurement (1.3.0) ", sha256: null}`, `uses_plasma: true`, `uses_rpi: true`, `uses_pump: false`, `confirmed: {high_voltage: true, liquid: false, hv_off_checked: false}`; nothing is removed (`sequence` stays). A schedule that was active becomes `interrupted` by the normal start-up rule |
| 6 | `runlog.jsonl` | when missing: built once from `runs\*.meta.json` of kind `plasma` — `{kind: "legacy", time: started_at, recipe_name: "Discharge measurement (1.3.0) ", status: "completed", spectra: [job], result_text: "highest peak … nm"}` — so the owner's old measurements appear in execution record. Captures stay visible in Maintenance › spectrum |
| 7 | `rounds\`, `logs\` created; `version.json` written last | event “has been upgraded to 2.0.0: Original connection settings and maintenance PIN and spectral records are retained” Automatic testing of (only when `migrated_from` is `1.3.0`) |

`config.json`, `pin.json`, `events.jsonl`, `runs\` are never rewritten. A failure in any step writes an error event and leaves
everything in place; the program still starts. A fresh folder takes the same path with `migrated_from: null`.
A 1.3.0 folder **without** a PIN behaves differently after the update: Maintenance is closed until `Set-PIN.cmd` has been run once
(Overview's “Before you start using it”, the guide and `READ-ME-FIRST.txt` say so).

---

## E. UI

Every text below is given as it appears on screen. The look is the original's live screen. **Implementation rule: take the
original's inline style block (`hmi_v1_1\web\index.html` lines 18–468; reproduced verbatim in
`scratchpad\understand\hmi-ui-style.md`, Appendix A.4, with the design-system residue it relies on in §7.1) as the starting
stylesheet and keep its class names.** This section names those classes; anything not mentioned keeps the original's CSS.
Templates for doing it without React: `hmi_v1_1\web\calibration\` and `web\trend\`. Reference pictures: `hmi-shots\orig-*.png`.

### E.1 Rules for every page

| Rule | Specification |
|---|---|
| Frame | `.hmi` 1024 × 768, `overflow: hidden`. Regions (x, y, w, h): top bar 0, 0, 1024, 56 · alarm banner 0, 56, 1024, 44 · nav rail 0, 100, 116, 598 · page 116, 100, 908, 598 (padding 8 / 10 → usable 888 × 582) · footer 0, 698, 1024, 70. Overlays (`.overlay` z 40, `.dialog` z 41) are children of `.hmi` |
| Scaling | `body { margin: 0; height: 100vh; overflow: hidden; background: #d9dadc }`. `.hmi { position: absolute; left: 50%; top: 50%; margin: -384px 0 0 -512px; transform: scale(var(--k, 1)); transform-origin: 50% 50% }`. `main.js` sets `--k = min(innerWidth / 1024, innerHeight / 768)` through `style.setProperty` at start and on `resize`. Proportions never change; nothing scrolls |
| Practice mode | `.hmi.demo` (5 px amber inner ring) + striped mode chip, as the original |
| Colour | Colour means "running" or "abnormal" only. Running / command ON: solid `--run` #2f5f8f (`.q-run`). Normal: grey text (`.q-ok`). Value not trustworthy: magenta `--bad` with “?” prefix and dashed frame (`.q-bad`). Not installed / not chosen: light grey dashed (`.q-na`). Alarm priorities only: Emergency `--p1` red, high `--p2` orange, middle `--p3` yellow. High voltage: 3 px `--p1` frame with yellow-black hazard stripes (`.gbtn.hv`, `.stile.hv`, `.dialog.danger.hv`). Green #3d7a52 only for “Completed” and the OK square of a chip |
| Shape | square corners, 1 px `--line` borders, no shadows except overlays, no animation except the blinking priority box of an unacknowledged alarm |
| Fonts | Barlow 400 / 500 / 700 and Barlow Condensed 400 / 600 from `/fonts/` (`@font-face` in `base.css`); stack `"Barlow", system-ui, "Microsoft JhengHei", sans-serif`; base 15 px; Barlow Condensed for the title, the clock, time and number columns, big values and `.btn` labels |
| Lists | never scroll. Fixed rows per page with `.pager`: “◀ Previous page” `No. a / b Page` “Next page ▶”; both disabled on a single page. Rows per page: steps on Overview 6 · slots 9 · run log 9 · round steps in the detail 9 · alarms 8 · events 12 · Recipes 6 · CSV rows 7 · operators 8 · spectrum sets 5 · single-frame table 10 · choices in a dialog 8 |
| Long text | one line with ellipsis; a tap opens it in a message dialog (cut at 700 characters) |
| **Numbers** | never typed into the page. A value is a tile (`.stile`, `.pbtn`, `.vol`, `.btn`); a tap opens the **number pad dialog**, which also listens to the keyboard (digits, `.`, Backspace, Enter, Esc). It is the only number entry of the program |
| **Text** | one dialog with one native text field (three uses: operator name, Recipe name, RPi address / token). No on-screen letter keyboard |
| Why this way | the original's operators work on a touch panel and a wrong number here moves liquid or sets a voltage: one path for every number keeps range, step and error text in one place and works with touch, mouse and keyboard. Names are Chinese and need the IME, so text uses a real field; a second on-screen keyboard would be a second way to do one thing |
| Disabled controls | a disabled control always has its reason on screen: the page's notice bar, the row's tag, or the small line of the button. The server checks again and its answer is shown as a message |
| Stale data | no fresh `/api/state` for 5 s: every device value becomes “? Unknown” (`.q-bad`), start buttons are disabled, alarm `ui_stale` is raised by the page, “Stop STOP” stays enabled |
| Polling | `/api/state` every 1000 ms (abort after 4000 ms; skipped while the previous one is pending) · clock, dialog countdown, message fading every 1000 ms · `/api/schedule/preview` when the form changes, every 10 s while no schedule is active, and right before the start dialog opens · `/api/runlog`, `/api/recipes`, `/api/events`, `/api/runs` when their `revs` number in the state changes and the page that shows them is open · `/api/rounds/<id>` when the person pages away from the current step or opens a detail |
| Browser storage | `localStorage['plasmic.schedule2']` (schedule form) · `localStorage['plasmic.manual']` (`{flow, direction, seconds, syringe: {position, volume_ml}}`) · `sessionStorage['plasmic.unlock']` |
| Deep links | `/#dash` `/#schedule` `/#manual` `/#runs` `/#alarms` `/#maint` and `/#maint-recipe` `-link` `-plasma` `-pumps` `-spectra` `-operators` (login first when locked) |
| Markup rules (CSP) | no inline `<script>`, no `style="…"`, no `on…=` attributes, no `eval`, no `innerHTML` with data; run-time styles through the CSSOM (`el.style.setProperty`) |

### E.2 The frame

#### E.2.1 Top bar `.top` (56 px; `gap: 10px; padding: 0 12px`), left to right

| # | Element | Class | Text and states |
|---|---|---|---|
| 1 | product name | `.title` 22 px | “Plasmic electronic control” |
| 2 | mode | `.mode` / `.mode.demo` | “official mode” / “simulation mode” (`title` “is not connected to any device”) |
| 3 | spacer | `flex: 1` | |
| 4 | RPi chip | `.chip` + `i` | “RPi is connected” (`.q-ok`) · “RPi not connected” (`.q-na`) · “RPi Simulation” (`.q-ok`) · “RPi ? Unknown” (`.q-bad`, stale). `title` = the link's error text, else “{host}:{port}” |
| 5 | pump board chip | `.chip` + `i` | “pump control board connected” (`.q-ok`) · “Pump Control Board Simulation” (`.q-ok`) · “pump control board not set” (`.q-na`) · “pump control board not used” (`.q-na`) · “pump control board connecting...” (`.q-na`) · “pump control board ? Connection interrupted” (`.q-bad`: `missing`, `ambiguous`, `port_busy`, `wrong_device`, `lost`, `error`) · “pump control board ? Unknown” (stale). `title` = `pump.text` |
| 6 | operator | `button.opbtn` 44 high, 110–140 wide | `<small>operator</small><b>{name}</b>`; none: `.none` with “Please select”. Tap → dialog “Select operator” (choice grid; empty list → message “There is no operator list yet” / “Ask maintenance personnel to go to ”Repair › Operator” is added. ”). **The only operator picker of the program.** Can be changed at any time: each round records the operator at its own start, the schedule record keeps who started it |
| 7 | maintenance mark (only while unlocked) | `span.tag.q-maint` (blue outline, bold, **not a button**) | “Maintenance {m}:{ss}” — time left without activity |
| 8 | guide | `a.userbtn` (`href="/guide" target="_blank" rel="noopener"`) | “illustrate” |
| 9 | clock | `.clock` | `<small>{YYYY-MM-DD}</small>{HH:MM:SS}` |

Not carried over: language switch, hidden long-press entry, a “maintenance mode” button (the nav item is the one way in).

#### E.2.2 Alarm banner `.banner` (44 px)

| State | Class | Content |
|---|---|---|
| no alarm | `banner none` | “No active alarm” |
| worst alarm unacknowledged | `banner p1 unacked` / `p2` / `p3` | `.pri` “urgent”/“high”/“middle” (blinks) · `.time` `HH:MM:SS` · `.msg` text (tap → dialog “alarm · {level}” with the full text, the area and the time) · `.more` “Also n item” · button “confirm ACK” · button “Alarm List” |
| all acknowledged, some active | `banner acked` | same without “confirm ACK” |

Order: unacknowledged first, then priority, then newest. “confirm ACK” → `POST /api/alarms/ack {ids: [id]}`. “Alarm List” → page alarm.

#### E.2.3 Navigation rail `.nav` (116 px; buttons 104 × 54, gap 5)

“Overview” · ”Scheduled Measurement” · ”Manual Operation” · ”Execution Record” · ”Alarm” (with `.badge` = number of alarms, coloured by the worst unacknowledged
priority, grey when all are acknowledged) · pinned to the bottom, 104 × 46, dashed: “🔧 Maintenance” (locked) / “Maintenance Settings” (unlocked).
Selected: `.sel`. No Trend and no Correction item; when they come back they take the two free places between manual operation and alarm.

#### E.2.4 Footer `.foot` (70 px), left to right

| Element | Size | Text | Action |
|---|---|---|---|
| `.start` | 140 × 54 | “Start scheduling” · ”Scheduling” · ”Continue RESUME” (`.start.resume`, green) | idle → operator picker first when no operator is chosen, then the start dialog (E.5.3) · paused → dialog “Continue to schedule” (lines “Recipe” “schedule” `No. n / N Second-rate` “at present” `Suspended`; when the paused round holds pumps one more line “will restart after continuing” `Pump 7 · OUT`; confirm “continue”) → `POST /api/resume` |
| `.pause-btn` | 120 × 54 | “pause PAUSE” · “Paused...” | `POST /api/pause`, no dialog |
| `.stop` | 120 × 54 | “Stop STOP” | `POST /api/stop`, **no dialog, always enabled** |
| `.prog` | flexible | below | |
| `.hvtag` | auto | “⚡ contains high voltage” — only while an active schedule uses plasma; `title` “Schedules to automatically output high voltage for each measurement.” | — |
| `.estop` | 188 wide, 2 px red frame, SVG red disc r 14 with yellow disc r 8 | “For emergency stop, please press on the device**Physical emergency stop button**”; `title` “STOP is a software stop, not a safety level emergency stop.” | not a button |

| Situation | `.start` | `.pause-btn` | `.stop` |
|---|---|---|---|
| no schedule active, start possible | “Start scheduling” enabled | disabled | enabled |
| no schedule active, start not possible | “Start scheduling” disabled | disabled | enabled |
| schedule `waiting` / `running` | “is scheduled” disabled | “pause PAUSE” enabled | enabled |
| `pausing` | “is scheduled” disabled | “Paused...” disabled | enabled |
| `paused` | “continue RESUME” enabled | disabled | enabled |
| `stopping` | “is scheduled” disabled | disabled | enabled |

`.prog` shows exactly one of:

| Case | Content |
|---|---|
| start not possible | `<small>cannot start</small><b class="why">{reason}</b>`; tap → dialog “Unable to start schedule at this time” with every reason and its fix; one button “Close” |
| schedule active | four cells: “schedule” `No. n / N Second-rate` (continuous: `No. n Second-rate`) · “Next measurement” `{HH:MM} ({mm:ss}) ` or “at present” `under measurement` / `Paused` / `Suspended` / `Stopping` · “Steps” `a / b · {phase}` · “Not measured” `n Second-rate` |
| otherwise | `<small>message</small><b>{state.message.text}</b>`; a line of level `info` disappears after 12 s, `warn` / `error` stay until the next one; tap → dialog “message” |

Reason for "start not possible", first match: “program did not respond” (stale) · “Please go to ”Scheduling Measurement” to select Recipe” · the preview's error text ·
“Schedule setting is being calculated...” · the first `blockers[].text` of the preview. A missing operator is not a reason (the picker opens first).

After “Stop STOP” the message is built from the reply (API.md): “Stopped: The pump has stopped; high voltage power supply readback OFF” ·
“Stopped: High Voltage Power Supply Readback OFF (The pump control board is not used)” · ”Stopped: The pump has stopped; the discharge work has been stopped, etc. RPi read back” ·
“⚠ Stop command not confirmed:{pump control board｜RPi} ({error}) — Please use the physical switch to cut off the power.” (level `error`). These sentences say only what was read
back; the E-stop notice stays beside them.

### E.3 Dialogs

One engine (`dialog.js`), one dialog at a time, built inside `.hmi`. A tap on the scrim or `Esc` cancels. No browser
`alert` / `confirm` / `prompt`.

| Part (all optional, in this order) | Class | Notes |
|---|---|---|
| title | `.dlg-h` | high-voltage dialogs start with “⚠” |
| lines | `.dlg-line` | grey label left, bold value right |
| fields | `.dlg-line.tap` | a line that opens another dialog and comes back with the new value (used only by “RPi connection setting”) |
| choices | `.dlg-choices.grid` | two 52 px buttons per row, 8 per page, “◀ Previous page” / “Next page ▶” inside the dialog; a choice may carry a `<small>` line and may be disabled with its reason there |
| number pad | `.dlg-buf` + `.dlg-range` + `.keys` | keys `7 8 9 / 4 5 6 / 1 2 3 / C 0 ⌫`; with decimals `. 0 ⌫` and a row `C`; range line “range a–b,integer” or “range a–b,decimal n Bit”; error “is out of range or the format does not match”; confirm disabled until valid. Keyboard: digits, `.`, Backspace, Enter (= confirm when valid), Esc. PIN variant: “●” per digit, “Please enter 6–8 digital repair PIN” |
| text | `input.dlg-input` (52 px, 2 px blue frame, 22 px) + a line with the rule | the only native input; `maxlength` set; focus on open; Enter confirms. Masked variant (`type="password"`) with “show” / “Hide” for the token |
| detail | `.dlg-detail` (`.err` for errors) | plain text, `white-space: pre-wrap` |
| warning | `.dlg-warn` | yellow left bar |
| tick rows | `.ack-row` (+ `.on`) | 54 px, 2 px red frame, 28 px box with “✓”; **every** row must be ticked before the confirm button is enabled |
| footer | `.dlg-f` | “Cancel” + confirm with its own label (default “Sure”); message dialogs have one button “Close” |
| countdown | `.dlg-timeout` | “n seconds, it will be automatically canceled and no command will be sent. ” |

Variants: default 440 wide · `.wide` 720 (text, choices) · `.danger` (orange frame and confirm) · `.danger.hv` (580 wide,
3 px red frame, red title bar, red confirm). Auto-cancel: 30 s, restarted by every key or tap inside the dialog; 90 s for
Start scheduling; 120 s for message dialogs and “Select the connection port of the pump control board”. On time-out the footer message is “Confirmation window timed out and was canceled,No command sent”.
Tick rows are used in exactly three kinds of dialog: the schedule start, the maintenance discharge, and a manual start while
the RPi is not connected.

### E.4 Page Overview (`dash`) — read-only

Five blocks, top to bottom in the 888 × 582 area, 8 px apart.

| Block | Height | Class |
|---|---|---|
| D1 four value tiles | 84 | `.metals` with four `.panel.metal` (each 216 wide) |
| D2 process stage | 84 | `.panel.steps` |
| D3 Steps in this round / Recipe Steps / Before you start using | 304 | `.panel.table` |
| D4 status strip | 56 | `.panel.strip` with six `.cell` |
| D5 safety line | 22 | `.dash-note` (13 px grey): “screen display OFF does not mean that the electrode is out of power. Before repairing or wiring, please use a physical switch to disconnect the high-voltage power supply from the 24 V drive power supply. ” |

**D1 tiles** — `.metal-head` = name (19 px bold) + `.tag`; `.metal-val` = `<b>` value (30 px Barlow Condensed) + `<span>` sub-line.

| Tile | Tag | Value | Sub-line |
|---|---|---|---|
| “HSPY high voltage power supply” | “HSPY OFF” · “HSPY ON” (`.q-run`) · “? readback is invalid” (`.q-bad`) · “not connected” (`.q-na`) | `{voltage_v} V` (read-back) or “—” | “settings {set_voltage_v} V · output {current_a × 1000} mA” |
| “pump” | “Connected” · ”Simulation” · ”Not set” (`.q-na`) · “Not used” (`.q-na`) · “? Connection interrupted” (`.q-bad`) | “stop all” (grey) · “Pump 1” · “Pump 1＋8” · “Pump 8 Quantitative” · ”Cleaning” (blue, `.q-run`) | “A Group {motor n running｜idle} · B Group {…}” |
| “weight” | “Stable” · ”Changing” · “? No response” (`.q-bad`) · “is not connected” (`.q-na`, board not in use) | `{grams} g` with `scale.decimals` decimals, or “—” | “Last calibration {YYYY-MM-DD}” / “has not been calibrated yet” |
| “latest measurement” Will — the whole tile is a `button` | “Completed” (green outline) or none | highest peak `{peak_nm} nm` (1 decimal) or “—” | “{MM-DD HH:MM} · {V} V · signal n open” / “has not been measured yet”. Tap → execution record, detail of that measurement |

**D2 process stage** — `.ph`: `<h2>process stage</h2>`, `.sub`, `.sp`, `.meta`.

| Situation | `.sub` | `.meta` |
|---|---|---|
| round running | “is executing:{Recipe}” | “Schedule No. n/N times · steps a/b” |
| round paused | “Suspended:{Recipe}” | same |
| schedule waiting | “last executed: {state} · {Recipe}” | “Schedule {state} · next time {HH:MM}” |
| nothing active, a Recipe is chosen on Schedule measurement | “Selected:{Recipe}” | “common b step · R Wheel” |
| no Recipe chosen | — | body `.steps-empty`: “Not selected yet Recipe: Please go to ”Scheduling Measurement” to select Recipe” |

Body `.steps-row`: one `.step` box per phase (`<span class="no">{n}</span>{name}`): `.done` grey, `.cur` blue, `.pause` amber,
`.fail` orange. More than 10 phases: a window of 10 that contains the current one.

**D3** — `.ph` + `.thead` (34) + 6 `.trow.slim` (32) + `.pager`.

| Situation | Title | `.sub` | Body |
|---|---|---|---|
| a round is running or paused | “Steps for this round” | `round.current.detail` (B.5) | steps of that round (from `round.steps_window`; other pages from `/api/rounds/<id>`) |
| a round has ended and nothing else is chosen | “Steps for this round” | “{HH:MM} {Complete｜failed｜Stopped}{: error}” | its steps with their results |
| idle, Recipe chosen, set-up complete | “Recipe Steps” | “will be executed in this order after the schedule starts.” | the Recipe's expanded steps, all “—” |
| set-up not complete and no round running | “Before you start using it” | “After completing these four things in order, you can start measuring.” | the four lines below |
| nothing to show | “Steps for this round” | — | `.empty`: “There is no process yet” / “Go to ”Scheduled Measurement” and select Recipe,Then click ”Start Scheduling” at the bottom. ” |

Step table columns: `#` (44) · action (flex; the step sentence `text`) · state (110) · results (190) · time (70; `HH:MM:SS` of the end, or
of the start while running). state: “—” pending · `.st.running` “is executing” (row background `--run-bg`) · `.st.completed` “Completed” ·
`.st.failed` “failed” (tap the row → dialog “step failed” with the error) · “Stopped” · ”Not executed” (grey). results: “10.02 g (error +0.02 g) ” ·
“123.456 g” · “highest peak 337.1 nm” · the error text · empty. The page that holds the current step is shown automatically unless
the person used the pager in the last 30 s.

“Before you start using it” — four **status lines** of 58 px (left: number, bold title, grey sub-line; right: one `.tag`). **No buttons**: the nav
rail is the one way into Maintenance.

| # | Title | Sub-line | Tag |
|---|---|---|---|
| 1 | “Setup maintenance PIN” | “Double-click in the folder Set-PIN.cmd,input 6–8 digit. No need to restart the program after setting. ” (practice mode: “Practice mode maintenance PIN fixed yes 000000”) | “has been set” / “Not set” (`.q-na`) |
| 2 | “connected RPi (Plasma)” | “Connected {host}” or the link's error text | “is connected” / “not connected” (`.q-na`) |
| 3 | “pump control board (Arduino Mega) ” | “Go to ”Repair › Connection” select a port. If you do not use a pump, select ”Do not use pump control panel”. ” | “is connected” / “Not used” / “Not selected yet” (`.q-na`) / “? Not connected” (`.q-bad`) |
| 4 | “Operator Vs. Recipe” | “n operators · m indivual Recipe.list can go to ”Maintenance › Operator” is replaced with the real name. ” | “can start” / “Not completed” (`.q-na`) |

The panel is replaced by the step list when `setup.complete` is true and returns only if one of the four becomes untrue.

**D4 strip**

| Cell label | Value |
|---|---|
| “RPi connection” | “Connected · n seconds ago” · ”Not connected” · “? Unknown” |
| “HSPY high voltage power supply” | “OFF · 0 V” · “ON · {V} V” (`.q-run`) · “? readback is invalid” · “—” |
| “Nano controller” | “Standby” · ”Ready” · ”Waiting for trigger” · ”Outputting” (`.q-run`) · “Completed” · ”Fault:{Heartbeat interruption｜trigger timeout｜work timeout}” (class `p2`) · “? readback is invalid” · “—” |
| “pump control board | `pump.text` |
| “syringe pump” | “Initialized · Inside the syringe x mL” · “has not been initialized yet” · ”No response” · “—” |
| “active alert” | “n (unconfirmed m) ” with class `p1` / `p2` / `p3` of the worst · “none” |

### E.5 Page Schedule measurement (`schedule`)

`.sch-body`: left `.panel.sch-left` 392 wide, right `.panel.sch-right.table` 488 wide.

#### E.5.1 Left: Schedule setting

`.ph`: `<h2>Schedule setting</h2>` + `.sub` “Click on each item to modify; the settings will be recorded on this computer.”; while a schedule is active “The schedule is in progress: it cannot be modified until it is stopped.”
(tiles disabled, showing the running schedule's values). `.sch-grid` (two columns, 8 px gap), tiles `.stile` 80 px
(`<span>` label, `<b>` value, `<small>` sub-line):

| Tile | Value | Sub-line | Editing |
|---|---|---|---|
| “Recipe” (spans both columns) | name, or “Please select” with `.need` | “n step · {contains high voltage plasma｜Does not contain plasma} · {uses pump｜No need for pump}” | dialog “Select Recipe”: choice grid of the published Recipes; one that fails preflight now is disabled with its reason in the small line. No Recipe at all → message “Not yet Recipe” / “Ask maintenance personnel to go to ”Repair › Recipe”release.” |
| “Start” | “Start now” / “{HH:MM} Start” | “If this time has passed, it will be tomorrow.” (clock time only) | dialog “Start method”: “Start now” / “specified time” → pad “Start time: hour” 0–23 → pad “Start time: minutes” 0–59 |
| “Measurement interval” | “Every N minute”; whole hours “Every h Hour” | “is calculated based on the starting time each time”; with “common 1 times”: “Just measure 1 not used for the next time” | pad “Measurement interval” 1–1440, unit “minute” |
| “end condition” | “common N times” / “arrive {HH:MM} ” / “Continue until stop is pressed” | “Just measure once and choose 1 times” / — / “according to STOP Just ended” | dialog “end condition”: “Specified times” → pad “Measurement times” 1–1000 · “to the specified time” → the two time pads · “Continue until stop is pressed” |
| “measurement content” (a `div`, not a button; `.stile.hv` with plasma) | “⚡ contains high-voltage plasma” / “includes spectrum measurement” / “Only add liquid” | “{V} V · Confirm once at the beginning, then automatically discharge / “No high voltage output” | — |

Default form: `{recipe_id: '', start: {mode: 'now', time: '08:00'}, interval_minutes: 10, end: {mode: 'count', count: 1, time: '17:00'}}`
(Start now / Every 10 minutes / common 1 Second-rate). There is no operator tile (the top bar is the one picker).

`.sch-est` (`.dlg-line` rows): before the start “Estimated single time” `about m point s Second` · “first measurement” `{HH:MM}`(+“ (tomorrow)”) · “Estimated number of times” `N Second-rate` / `Continuous` ·
“The last time” `{MM-DD HH:MM}` / `according to STOP until` · with pump steps “” `Pump 8 · CuSO₄ 10 g · Pump 1 · water 20 mL · Pump 7 · OUT about 40 mL`.
While active: “status” · ”First measurement” · ”Estimated times” · ”Last time”. Then `.sch-err` (the preview's error, or `schedule.error`) and one
`.sch-warn` per warning; at most two boxes, a third reads “Also n , click here to see”.

#### E.5.2 Right: slot log

`.ph`: title “This schedule” (active) / “Last scheduled” (ended) / “Schedule record” (none yet); `.sub` “{Recipe} · {operator} · {establishment time}”; right:
“Complete n · Skip n · failed/not started n”. Columns: sequence (52) · scheduled time (104, `MM-DD HH:MM`) · state (136) · start (48) · End (48) · Remarks (flex).
Newest first, 9 per page. A row with a round opens its detail in execution record.
Empty: `.empty` “Scheduling has not started yet” / “Select on the left Recipe, Start time, interval and end conditions, then click ”Start Scheduling” at the bottom. Only measure once and select ”Total 1 times”. Results for each period (including skipped periods) are listed here. ”

#### E.5.3 Start dialog (from the footer)

Title “Start scheduling”; with plasma “⚠ Start scheduling: high voltage will be automatically output for each measurement. (`.danger.hv`). 90 s countdown.
Lines: “Recipe” · “Operator” · ”First Measurement” `{HH:MM}` / “Immediately” · ”Interval” `Every N minutes` (left out with common 1 Second-rate) · “Ended” · ”Estimated Single Time” `about m point s Second` ·
(pump steps) “” · (plasma) “HSPY Set voltage” `{V} V (upper limit 700 V) ` · (plasma) “Measurement Process” `rpi.flow.text`. Then the Recipe's warnings.

Tick rows, in this order:

| Shown when | Text | Server flag (`confirm.…`) |
|---|---|---|
| Recipe uses plasma | “I have confirmed that each measurement will automatically output {V} V high pressure,Won’t ask again” | `high_voltage` |
| Recipe uses plasma | “The electrode and high-voltage line have been connected; personnel have been kept away, and the upper cover has been closed.” | `high_voltage` |
| Recipe uses the pump board | “Pipes, reagents and waste are in place” | `liquid` |
| Recipe uses the pump board and the RPi is not connected (`preview.needs.hv_off_checked`) | “RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. ” | `hv_off_checked` |
| always | “Physical emergency stop/power-off switch can be used at any time” | — (page only) |

Warning with plasma: “Each measurement during scheduling will be automatically output. {V} V High voltage, no longer confirmed one after another. ”Stop” on the screen means the software stops, not an emergency stop. In an emergency, please press the physical emergency stop button. ”
Without: “schedule will automatically start the pump and syringe pump according to time. ”Stop” on the screen means the software stops, not an emergency stop. ”
Confirm “Start scheduling” → `POST /api/schedule/start`. A refusal is shown as a message dialog “The schedule did not start” with the reasons and fixes.

### E.6 Page manual operation (`manual`)

Top: `.notice` (41 px). Below: `.manual` = `.panel.pumps` (550 wide) + `.side` (330 wide). The generated English guide describes the layout.

#### E.6.1 Notice bar — first match

| Condition | Class | Text |
|---|---|---|
| stale | `.notice.err` | “program does not respond, manual operation is disabled” |
| board never chosen | `.notice.block` | “The pump control board has not been set yet: please ask maintenance personnel to › Connection ”Select Port” |
| board set to "not used" | `.notice.block` | “Pump control panel is set to not be used: There are no devices that can be operated on this page.” |
| board not connected | `.notice.err` | “Pump control board is not connected ({pump.text}) : startup operations disabled” |
| `motion_uncertain` | `.notice.err` | “The status of the pump control board is unknown: please go to the site to confirm first, and then wait for it to reconnect.” |
| schedule `waiting` / `running` / `pausing` / `stopping` | `.notice.block` | “Scheduling in progress: manual start and deactivation; if manual operation is required, please pause the schedule first.” |
| round paused | `.notice.block` | “The process is paused: the pump is still retained by the process, please click ”Continue” at the bottom RESUME”or“stop STOP”” |
| high-voltage interlock (`interlock.manual.reasons` with code `hv_on` / `hv_unknown`, RPi connected) | `.notice.err` with `.n-body` | “Manual start and deactivation:{reasons}” and `.n-fix` “Processing method: {fixes}.OFF Still available.” |
| auto-clean running | `.notice.ok` | “Automatic cleaning is in progress: wait for it to end, or press ”Stop” at the bottom STOP”” |
| weight dose running | `.notice.ok` | “Weighing and quantification in progress ({motor}) : Wait for it to end, or press the OFF” |
| another manual operation running | `.notice.ok` | “{op.text}” (e.g. “Pump 8 · CuSO₄ Quantitative 5 mL · already 8 Second/about 17 Second”) |
| RPi not connected (`interlock.manual.need_hv_ack`) | `.notice.warn` | “RPi Not connected: The program cannot confirm the high voltage status. Before starting, go to the site to confirm that the high-voltage power supply is turned off. ” |
| otherwise | `.notice.warn` | “After the pump is started, the program will automatically stop feeding when the set seconds are reached. program, computer or USB There is no guarantee that it will stop when the line is interrupted. Please prepare a physical stop method. ” |

Rows 1–8 disable every start control of the page. OFF buttons are disabled only when the board is not connected.

#### E.6.2 Left: peristaltic pump

`.ph` (46 px): `<h2>peristaltic pump</h2>` · `.sp` · `.seg` “”Forward rotation” ”Reverse rotation” · `button.btn` “{flow} mL/min” (pad “continuous operation flow” 0.1–80, 1 decimal,
default 40) · `button.btn` “{n} Automatically stop in seconds” (pad “Automatic stop seconds” 1–300, integer, default 30). These three are what the next “ON”
uses; the direction is also what the next volume dose uses. Kept in `localStorage['plasmic.manual']`.

Nine `.prow` rows of 54 px: eight motors in the order of `hardware.groups`, then auto-clean.
Motor row: `.nm` (`<b>` 17 px label, `<small>` “A group” / “A group · Linked solenoid valve 1”) · `.tag` (min-width 104) · `button.btn` “ON” (56 × 44) ·
`button.btn` “Quantitative” (60 × 44) · `button.btn` “OFF” (56 × 44).

| `motors[].state` / situation | Tag | “ON” / “Quantitative” | “OFF” |
|---|---|---|---|
| `off`, group free | “stop” | enabled | enabled |
| `off`, other motor of the group busy (`blocked`) | “{A｜B} group is in use” (`.q-na`) | disabled | enabled |
| `wait` | “Valve is opening” (`.q-run`) | disabled | enabled |
| `run`, `by: manual` | “Operation · Remaining n Second” (`.q-run`) | disabled | enabled |
| `run`, `by: round` | “process control” (`.q-run`) | disabled | enabled |
| `move` | “Quantification in progress” (`.q-run`) | disabled | enabled |
| `weigh` | “weighing +x g” (`.q-run`) | disabled | enabled |
| `clean` | “Cleaning” (`.q-run`) | disabled | disabled (the firmware refuses single stops during cleaning; the notice says to use STOP) |
| board not connected / stale | “? Unknown” (`.q-bad`) | disabled | disabled / enabled when only stale |

Auto-clean row: `.nm` `<b>automatic cleaning</b><small>motor {water} Water ingress → motor {out} Discharge · Approx. {total} Second</small>` · `.tag` “standby” / “water valve” /
“in water” / “Water inlet and discharge” / “finally emptied” · one `button.btn` “Start cleaning” (120 × 44; “Cleaning...” disabled while running).

| Trigger | Dialog |
|---|---|
| “ON” | “start {motor}” — lines “direction/flow” `{Forward rotation｜reverse} · {flow} mL/min` · “automatically stops” `n Second` · for M2 / M9 “solenoid valve” `valve n Will open first 1 Second`; warning “program, computer or USB The pump will not stop automatically when the line is interrupted. Please confirm that the pipeline is correct and the physical stop method is available before starting. ”; confirm “Start” → `op: pump_on` |
| “Quantitative” | (1) “{motor} quantitative method”: choices “volume (mL) ” / “weight (g) ” — the second disabled with “scale has no reading value” / “The scale has not been calibrated yet” / “configuration file does not enable weighing and quantification” when it cannot be used; (2) pad “quantitative volume” 0.01–1000, 2 decimals, `mL` / “quantitative weight” 0.01–1000, 2 decimals, `g`; (3) “{motor} Quantitative” — lines “Way” `volume x mL` / `weight x g (Confirm with scale)` · “direction” `{Forward rotation｜reverse}` (weight: “Forward rotation (fixed)”) · “speed” `40 mL/min (fixed)` (weight: “40 → 10 → 3 mL/min”) · “Estimate” `about t Second`; warning “Stop midway,According to this column OFF or the ”Stop” at the bottom STOP”.” (weight adds “Please do not touch the scale. longest 5 minute.”); confirm “Start quantification” → `op: dose_volume` / `dose_weight` |
| “OFF” | no dialog; `op: pump_off` at once |
| “Start cleaning” | “automatic cleaning — lines “Water intrusion” `motor {water},alone first {lead} Second` · “at the same time” `Again with the motor {out} Together {overlap} Second` · “empty” `Last motor {out} alone {tail} Second`; warning “Do not stop a single pump during cleaning. To stop, click ”Stop” at the bottom STOP”.”; confirm “Start cleaning” → `op: clean` |

When `interlock.manual.need_hv_ack` is true every start dialog of this page (ON, Quantitative, Start cleaning, initialization, Liquid extraction, Liquid injection) carries one tick row
“RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. ” and sends `hv_off_checked: true`.
Results arrive as the footer message (from `state.pump.last_result`): “Pump 8 · CuSO₄ Quantitative completion:10.02 g (error +0.02 g) ”,
“Pump 1 · Water has reached the setting 30 seconds, automatically stopped”, “Operation failed: {text}”.

#### E.6.3 Right: syringe pump and weight

**Panel syringe pump** (318 high). `.ph`: `<h2>syringe pump</h2>` · `.sub` “Syringe {5｜1.25} mL · speed {v} mL/s” · `.sp` · `.tag`: “Initialized” /
“has not been initialized yet” (`.q-na`) / “in action” (`.q-run`) / “? mistake n” (`.q-bad`) / “No response” (`.q-bad`).

| Row label | Control |
|---|---|
| “valve position” | `.seg` of `hardware.syringe.valve_positions` buttons, 50 px: “Valve position 1” “Valve position 2” “Valve position 3” — the position the next move uses; nothing is sent when it is tapped |
| “volume” | `−` (48 × 46) · `.vol` `{x.xxx} mL` (pad “injection volume” 0.001–syringe size, 3 decimals) · `+` (48 × 46); step 0.1 mL |
| (actions) | `.acts`: “”Aspirate” · ”Inject” · ”Initialize” (46 px each) |
| (note) | `.fb`: “Estimate inside the syringe x mL.The syringe pump does not have position readback. Please go to the site to confirm the action results. ” / before initialising: “Please click ”Initialize” first. Initialization will push the piston to the top: the liquid in the syringe will be discharged from the current valve position. ” |

“Aspirate fluid” / “injection” are disabled until initialised, and when the move would leave 0…syringe size (the `.fb` line then reads
“The syringe capacity will be exceeded after withdrawing liquid” / “There’s not that much in the syringe”). Size and speed are set in Maintenance › Pump and Scale (the `.sub` shows them).
Dialogs: “syringe pump pumping liquid” / “syringe pump injection” — lines “Valve position” · ”Volume” · ”Speed” · ”Estimation”; warning “The syringe pump does not have position readback, and the action results must be confirmed on site. ”; confirm
“Aspirate fluid” / “injection” → `op: syringe_move`. “Syringe pump initialization” (`.danger`) — warning “The piston will push to the top and then return. The liquid in the syringe will be discharged from the current valve position. Please confirm that the outlet is connected to the right place. ”;
confirm “initialization” → `op: syringe_init`.

**Panel weight** (207 high). `.ph`: `<h2>weight</h2>` · `.sp` · `.tag` “stable” / “is changing” / “? No response”. Body: the weight in 44 px Barlow
Condensed, centred, `{grams} g`; bottom 13 px grey “Zero reset and calibration in ”Maintenance › Pump and Scale”. ” **No buttons.**

### E.7 Page execution record (`runs`)

**List** — one `.panel.table`. `.ph`: `<h2>execution record</h2>` + `.sub` “Measured and unmeasured periods (recent 200 pen)”.
Columns: time (`.c-time` 126, `MM-DD HH:MM:SS`) · operator (80) · Recipe (flex) · sequence (52: `n/N`, continuous `n`) · End (48, `HH:MM`) ·
state (76) · results (190) · Remarks (110). state uses the round / slot words of `bootstrap.labels`. results = `result_text`. Remarks: the error or the
slot's note, ellipsis, tap → message dialog. 9 rows per page. A row with a round or a spectrum is a button → detail.
Empty: “has no measurement record yet” / “After starting the schedule, each measurement and the period of time that was not measured due to timeout or suspension will be listed here. ”

**Detail** (replaces the list inside the same page — no dialog inside a dialog).
`.ph`: `button.btn` “◀ Return to list” · `<h2>No. n/N Second-rate · {Recipe}</h2>` · `.sub` “{YYYY-MM-DD HH:MM} · {operator}” · `.sp` · `.seg` “Spectrum” ”Steps”.

View “spectrum” — left column 250 wide `.kv.big`: “Status” · ”Start” · ”End” · ”Set Voltage” `{V} V` · “background/signal” `b open/s open` (from the stored
result) · “discharges in a few seconds after pressing” `{wait_s} Second` · “The highest peak” `{nm} nm`. Right: `.chart` 620 × 308 (SVG plot 566 × 282 at 52, 4): the mean
signal minus background, line `.sl-mean`; grid `.sl-grid`; zero line `.sl-zero`; y ticks left, x ticks below, unit “nm”;
legend “black: average n Zhang (button background)”. Under the chart: “The strongest peak:” + up to five `.btn` chips `{nm}` (tap = zoom ±10 nm; a sixth chip
“full spectrum” returns) and `a.btn` “download CSV (Excel can be opened)” (`/api/runs/<job>.csv`). A round with several discharges adds `.fnav`
“◀ No. i / k discharge ▶”. No spectrum: “There is no spectrum this time (Recipe There is no measurement step, or the measurement is not completed)”.

View “Steps” — the table of E.4 D3 with 9 rows per page. A `legacy` row (a 1.3.0 measurement): “1.3.0 ’s measurement has no step record”.

### E.8 Page alarm (`alarms`)

`.ph` holds `.seg.tabs` “alarm list (n) ” “Operational events”, the counters `.count` “Emergency n” “high n” “middle n”, `.sp`, `button.btn` “Confirm all”
(disabled when nothing is unacknowledged).

Alarm list — columns Occurrence time (126) · level (58, `.pri`) · area (60) · message (flex, tap → dialog) · state (118: “in action · Not confirmed”
“Active · Confirmed” ”Restored · Unconfirmed”) · action (72: `button.ack` “confirm”). Unacknowledged rows `.trow.unacked`. 8 per page.
Empty: “No active alarm” / “Alarm condition: The program does not respond, Use afterRPi or the connection to the pump control board is interrupted, the process fails, the stop of the pump or high pressure is not confirmed, the schedule is interrupted or the period is skipped,Nano fault. ”

operation event — `.sub` “recent 200 pen”; columns time (126) · who (80) · event (flex); `.trow.slim`, 12 per page; data `GET /api/events`.

Words (in `labels.js`; the schedule / slot / round / step tables come from `bootstrap.labels`): motor group `idle` idle · `wait`
Valve is opening Starting with · `run` running · `move` Quantifying · alarm priority 1 Emergency · 2 high · 3 middle · Nano `IDLE` standby · `ARMED` Ready · `WAIT_TRIGGER` Waiting for trigger ·
`RUNNING` Outputting · `DONE` Complete · `FAULT` fault · fault `NONE` none · `HEARTBEAT_LOST` Heartbeat interruption · `TRIGGER_TIMEOUT` trigger timeout · `JOB_TIMEOUT` work timeout.

### E.9 🔧 Maintenance — login and tab bar

**Locked: `.panel.login`.** `.ph`: `<h2>Maintenance login</h2>` + `.sub` “Recipe, Connections, plasma parameters, pumps and scales, spectra and operator lists are only for maintenance personnel.”.

| State | Content |
|---|---|
| no PIN file (`pin_set` false) | `.empty`: `<b>has not been set for maintenance PIN</b>` · `<p>Please double-click in the folder on your computer Set-PIN.cmd,settings 6–8 Digital repair PIN.After setting up, return here without restarting the program.</p>` · `button.btn` “Return to overview”. The page notices the new PIN through `state.pin_set` within a second |
| PIN set | `.pin-wrap`: `.pin-dots` (360 × 56, one “●” per digit) · `.login-err` · `.pin-keys` (3 × 104 px, keys 62 high): `1 2 3 / 4 5 6 / 7 8 9 / Clear 0 ⌫`, at most 8 digits · `.pin-foot`: “Return” · ”Login” (primary, disabled below 6 digits). Keyboard: digits, Backspace, Enter |
| practice mode | as "PIN set", plus one line above the dots: “Practice mode maintenance PIN fixed yes 000000” |

“log in” → `POST /api/unlock {pin}`; while waiting “Logging in...”; failure in `.login-err`: “Login failed: repair PIN error” /
“Login failed: Too many attempts, please n Try again in seconds”. Success: the session goes to `sessionStorage`, the page opens the tab that was asked for
(default Recipe).

**Unlocked: `.adm-bar` (44 px) + one tab body (530 px high).** `.seg.tabs` “Recipe” “Connection” ”Plasma Parameters” ”Pumps and Scales” ”Spectrum” ”Operator” ·
`button.msgchip` (last result of a maintenance action: `.ok` grey, `.busy` blue “Processing…”, `.err` “⚠ operation failed · Click here for details”; tap →
dialog “Maintenance operation message”) · `button.btn` “Sign out” (`POST /api/lock`, back to Overview). A request answered with `locked` returns the page
to the login panel with “Maintenance mode has automatically logged out”. While the equipment is busy (API.md "busy rule") every changing control is disabled
with the small line “The schedule or pump cannot be changed while it is running”.

### E.10 Tab Recipe

`.adm-body`: left `.panel.adm-left` 300 wide, right `.panel.adm-right` 580 wide.

**Left “Published Recipe”** — `.list` of `.li` rows (58 px, 6 per page): `<b>{name}</b><span>n step · {contains plasma｜Does not contain plasma} · {uses pump｜No need for pump}</span>`;
a Recipe that fails preflight now adds `<span class="warn">⚠ Not available at the moment</span>`. `.adm-foot`: `label.btn.file-btn` “Import CSV files”
(hidden `input type="file" accept=".csv"`) · `button.btn` “example…” → dialog “Example CSV files”: three download links `a.btn` —
“only discharge measurement” · ”Example: Add liquid＋Measure＋discharge” · ”Example: automatic cleaning” (`/api/recipes/examples/<file>.csv`), detail “Download and use Excel or Notepad to modify, then click ”Import” CSV file”. use Excel Please select ”CSV UTF-8 (comma separated)”. ”
Empty list: “Not yet Recipe” / “Click ”Import CSV file”, or download one from ”Example...” to modify it. ”

**Right, a published Recipe is selected**

| Region | Content |
|---|---|
| `.ph` | `<h2>{name}</h2>` + `.sub` “Posted in {YYYY-MM-DD HH:MM}” |
| `.kv` (3 columns) | “steps/Wheel” `n step · R wheel` · “Measurement `contains high voltage plasma` / `Does not contain plasma` · “pump control board `Used` / `Need not` · “Estimated time” `about m point s Second` · “” `Pump 8 · CuSO₄ 10 g · …` / `—` · “status” `can be used` / the preflight message |
| warnings | one `.sch-warn` line per warning (at most two; “Also n , click here to see”) |
| table | columns List (44) · action (flex; the step sentence) · Remarks (flex); `.trow.slim`, 7 per page |
| `.adm-foot` | `a.btn` “remit CSV” (`/api/recipes/<id>.csv`) · `.sp` · “delete Recipe” (disabled while this Recipe is in an active schedule; small text “schedule is in use”) |

**Right, an imported file** (after “Import CSV files”: the page reads the file as bytes and sends them base64 to
`POST /api/recipes/validate`; the reply holds the decoded text, which the page keeps in memory until release or discard)

| Region | Content |
|---|---|
| `.ph` | `<h2>new Recipe (has not been released yet)</h2>` |
| `.kv` | “name” — a `button` showing the name or “Please name” (text dialog “Recipe name”, 1–60 characters; default = the file name without `.csv`) · “Check” `passed` / `failed` (`.txt-warn`) · “steps/wheel” · ”Estimated time” · ”Measurement/pump” · ”Error” (the message, tap → dialog; the row it names is marked `.flag`) |
| warnings, table | as above |
| `.adm-foot` | “give up” · `.sp` · “release” (primary; disabled until the check passed and a name is set) |

Publish — dialog “release Recipe”: lines “Name” · ”Number of steps/wheel” · ”Estimated time” · ”Measurement” · ”Pump control panel”; with plasma the warning
“this Recipe contains high-voltage plasma: Confirm once at the beginning of the schedule, and it will automatically discharge for each subsequent measurement. ”; confirm “release” → `POST /api/recipes/publish`.
Delete — dialog “delete Recipe” (`.danger`): lines “Name” · ”Number of Steps”; warning “After deleting the schedule, you cannot select it again. The file will be moved to data\recipes\deleted.”;
confirm “Next step” → PIN pad “input repair PIN to delete” → `POST /api/recipes/delete {id, pin}`.
A file that cannot be read at all → message dialog “CSV cannot be read in” with the server's text; nothing is kept.
There is no step editor on the screen in 2.0.0 (section K).

### E.11 Tab connection

Two `.panel.adm-half`, 440 wide each. Both render from `/api/state`.

**Left “RPi (Plasma)”** — `.sub` “{host}:{port}”. `.kv.big`:

| Label | Value |
|---|---|
| “connection” | “is connected” / “not connected” (`.q-na`) / “simulator” |
| “RPi program version” | `rpi.server.version` / “—” |
| “Measurement Process” | `rpi.flow.text` — “background 1 sheets, click to make approx. Stop automatically after 4 Second Discharge” · ”Old Process: Background 3 sheets, click to make approx. Stop automatically after 11 Second discharge” · “RPi is still an old version: please double-click first Install.cmd renew RPi” (`.q-bad`) |
| “This computer’s IP” | `addresses` joined with “, ” |
| “token” | “has been set” / “Not set” |
| “last update” | “n seconds ago” |
| “Recent error” | text with ellipsis (tap → dialog) or “—” |

`.btn-grid` (`.gbtn` 66 px): “connection setting” `<small>RPi ’s address, port and Allow list of token</small>` · “connection diagnosis” `<small>Check the device step by step</small>` ·
“automatic search RPi” `<small>Search on this computer’s network</small>`. Practice mode: “connection setting” and “automatic search RPi” disabled with “simulation mode is fixedly connected to the local simulator”.

| Button | Dialog |
|---|---|
| “connection setting” | “RPi connection setting” with three fields: “RPi address” (text, e.g. `192.168.1.1`) · “TCP port” (pad 1–65535) · “token” (masked text; left empty = keep the saved one), and one plain tick row “Clear stored token”; confirm “Save and reconnect” → `POST /api/rpi/config` |
| “connection diagnosis” | “connection diagnosis” (`.wide`): one line per step of `/api/rpi/diagnose` — “✓” or “✗”, title, detail, and for a failed step its fix; buttons “Recheck” · ”Close” |
| “automatic search RPi” | “automatic search RPi” (`.wide`): while searching “Looking for...(most 20 seconds)”; then choices “{host} · {name} · Version {v}” (the present one marked “at present”); none: “not found. Please confirm the network line,RPi power supply, try again. ”; choosing another → confirm “Use instead {host}” → `POST /api/rpi/config` with the same port and no token field (keeps the saved one) |

**Right “pump control board (Arduino Mega) ”** — `.sub` = the device name or “Not selected yet”. `.kv.big`:

| Label | Value |
|---|---|
| “status” | `pump.text` (`.q-bad` for the problem states; `.q-na` for Not set / Not used) |
| “port” | “{device} · {description}” / “Not selected yet” / “Not used” |
| “firmware response” | “is correct” / “is not a pump control board” (`.q-bad`) / “—” |
| “Firmware Settings” | “is the same as the settings on the computer” / “⚠ is different (the firmware may be an old version)” / “—” |
| “syringe pump” | “has a response” / “No response” / “—” |
| “scale” | “has a reading value” / “has no reading value” / “—” |
| “Recent error” | `pump.error` or “—” |

`.btn-grid`: “Select port” `<small>First time use, or changed USB hole</small>` · “Reconnect” `<small>Pump control board will restart</small>` (disabled when not enabled).
Practice mode: both disabled, panel note “Simulation mode: Use the built-in simulation control panel”.

| Step | Dialog |
|---|---|
| “Select port” | “Select the connection port of the pump control board” (`.wide`, 120 s), data `GET /api/pump/ports`. One full-width choice per **Arduino Mega** port: `<b>{device} · {description}</b><small>Arduino Mega</small>` (the present one marked “at present”). Last choice: `<b>Does not use pump control board</b><small>Only conduct discharge measurement</small>`. No Mega: detail “not found Arduino Mega.Please check the pump control board USB line, and then click ”Refresh”. ” (+ “There are other n Not Arduino Mega sequence device,The program will not open them.” when `other_count` > 0). Footer: “Cancel” · ”Rearrange” |
| a port chosen | “connect to {device}” (`.danger`): line “device” `{description}`; warning “is connected: the pump will stop and the solenoid valve will close. Please close the independent laboratory first v7 (run.bat) and Arduino IDE ’s serial port monitoring window; the same port can only be used by one program at a time. ”; confirm “connection” → `POST /api/pump/config {enabled: true, device}` |
| “does not use the pump control board” | “does not use the pump control board”: detail “Manually operated pumps, syringe pumps and scales are deactivated; Recipe cannot be executed. Discharge only Recipe is not affected. ”; confirm “Sure” → `{enabled: false}` |
| “Reconnect” | “Rewire the pump control board”: the same restart warning; confirm “Reconnect” → `POST /api/pump/reconnect` |

There is no row for a device that is not an Arduino Mega, no typed port name and no "auto" choice. After the one choice the
program reconnects by itself to that identity, also when the COM number changes.

### E.12 Tab plasma parameters

One `.panel.adm-full`. `.ph`: `<h2>plasma parameters</h2>` + `.sub` “point value modified. The high pressure should be turned off during storage, and the high pressure should not be turned on.”.

`.param-grid` (4 columns, `.pbtn` 112 px): `<span>` label, `<b>` value, `<small>` “range a–b”; a changed tile gets `.changed` and
its small line reads “RPi at present {old}”.

| Label | Key sent | Range | Decimals |
|---|---|---|---|
| “pulse ON time (ms) ” | `Ontime` | 1–1000 | 1 |
| “pulse OFF time (ms) ” | `Offtime` | 1–1000 | 1 |
| “Number of pulses per round” | `Cycle` | 1–10000 | 0 |
| “Number of rounds=signal spectrum number” | `Loopnum` | 1–1000 | 0 |
| “Spectral integration time (ms) ” | `Integration_Time` | 1–10000 | 0 |
| “Spectral interval (ms) ” | `Spectra_Interval` | 1–60000 | 0 |
| “discharge voltage (V) ” | `Voltage` | 1–700 | 0 |
| “background spectrum” — a `div.pbtn.local`, not a button | — | value from `rpi.flow`: “1 open” small “shot before discharge (RPi measurement process) ” · “3 open” small “RPi is set to the old process” · “—” small “RPi is still an old version” | — |

The cross rules (cycles × loops, interval ≥ integration, total time) are the RPi's; its message is shown when it refuses.

Tool row under the grid (`.btn-grid`, 2 columns): `button.gbtn` “Clear Nano Fault” (66 px; small line “at present {Fault name}”; disabled with
“There is currently no glitch”) · `button.gbtn.hv` “⚠ Manual start plasma” / “⚠ Discharging...” (72 px, full width below; small line “will output high voltage · Current setting {V} V” or
the first reason of `interlock.plasma` / “Scheduling in progress”).

`.adm-foot`: note “One measurement is estimated to be approximately {estimate_s} seconds, approximately after pressing {start_s} seconds to start discharging. ” (no parameters read yet:
“RPi have not been read yet: please confirm first The place of RPi is connected”) · `.sp` · “Give up modification” (enabled only with a change) · **one primary button whose label follows the state**:

| State | Label | Action |
|---|---|---|
| a tile was changed | “Save and sync” | dialog “Store plasma parameters” → `POST /api/parameters` (all seven keys; the server saves, then syncs the Nano) |
| nothing changed and `rpi.parameters.nano_synced` is `false` | “synchronized to Nano” | dialog “synchronized to Nano” / detail “Bundle RPi Parameter writing of configuration file Nano and read back. High voltage will not start. ” → `POST /api/rpi/tool {cmd: "prepare"}` |
| otherwise | “has been synchronized” (disabled) | — |

Dialog “Store plasma parameters”: one line per changed value “{label}” `{old} → {new}`; warning “, each measurement will be {V} V discharge (upper limit 700 V) .Storage does not activate high voltage. ”;
confirm “Save and sync”. Result in the message chip: “has been saved and synced to Nano” / “saved but synced Nano failed:{text}”.
Dialog “Clear Nano Fault”: line “Fault”; detail “After clearing, please click ”Sync to Nano”.”; confirm “Clear” → `{cmd: "clear_fault"}`.
Dialog “⚠ Manually start plasma: high voltage will be output. (`.danger.hv`): lines “Discharge voltage” · ”Pulse ON/OFF” · “Number of pulses per round” · ”Number of rounds” · ”Spectral integration time” · ”Spectral interval” ·
“Estimated time” · ”Measurement process”; one tick row “I have confirmed that it will be output soon {V} V High voltage, personnel are far away from the electrode, physical emergency stop/The power-off switch is at hand”; confirm “Start plasma (output high voltage)”
→ `{cmd: "plasma_run", confirm_high_voltage: true}`. Message chip while it runs: “Discharging...To abort, please click ”Stop” at the bottom. STOP””; afterwards
“The discharge is completed and the spectrum has been saved in the ”Spectrum” tab..

### E.13 Tab Pump and Scale

Two `.panel.adm-half`.

**Left “Peristaltic Pump and Syringe Pump”** — `.kv.big`, read-only facts from `bootstrap.hardware`:

| Label | Value |
|---|---|
| “peristaltic pump” | “8 · Max. 80 mL/min” |
| “Quantitative Speed” | “40 mL/min (fixed)” |
| “Weighing Quantification” | “40 → 10 → 3 mL/min,allowed ±0.5 %” |
| “Continuous operation upper limit” | “300 Second” |
| “automatic cleaning | “Pump 1 Water ingress 30 Second → at the same time 45 Second → Pump 7 Empty 45 Second (Subject to firmware) ” |
| “linkage solenoid valve” | “Pump 1↔valve 1, Pump 8↔valve 2 · open first 1 Second, Houguan 1 Second” |

`.adm-note`: “These values come from independent laboratories v7 can only be viewed here. ”
`.btn-grid`: “Syringe Capacity” `<small>at present {5｜1.25} mL</small>` → dialog “Syringe Capacity”: choices from `hardware.syringe.options_ul`
(“5 mL” “1.25 mL”), detail “The syringe pump must be re-initialized after changing the syringe. ” · ”Syringe pump speed” `<small>at present {v} mL/s</small>` → pad “Syringe pump speed”
0.001–max of the size, 3 decimals. Each → `POST /api/pump/settings` (both values).

**Right “scale”** — top: the weight in 44 px, centred, with its tag (as on manual operation); `.kv.big`: “Last calibration” `{YYYY-MM-DD} · weight {g} g` /
`has not been calibrated yet`. `.adm-note`: “① Press ”Reset” for empty cup ② Put the weight on ③ Press ”Calibration” to select the weight. The results will be saved and loaded automatically next time. ”

| Button | Dialog |
|---|---|
| “Return to zero” `<small>Only empty cups are placed on the scale</small>` | “Scale returns to zero”: line “Current reading”; warning “Please confirm that the cup on the scale is empty. The current weight will be treated as 0,and archive.”; confirm “Return to zero” → `POST /api/scale/tare` |
| “Correction” `<small>Put the weight and press</small>` | (1) “Weight weight”: choices “10 g” “50 g” “100 g” “200 g” (`hardware.scale.calibration_presets_g`) · “other…” → pad 0.1–20000, 1 decimal; (2) “scale calibration”: lines “Weight” · ”Current Reading Value”; warning “Please confirm that the weights have been placed. Calibration will overwrite the last result. ”; confirm “Correction” → `POST /api/scale/calibrate {grams}` |

Both buttons are disabled with their reason in the small line: “The pump control board is not connected” · ”The pump is still running” · ”The scale has no reading” · ”The reading is still changing, wait for it to stabilize”.

### E.14 Tab spectrum

`.adm-body`: `.panel.spec-list` 250 wide + `.panel.spec-main`.

**Left “spectrum”** — `.li` rows (5 per page): `<b>{MM-DD HH:MM:SS}</b><span>{Recipe Name｜Manual discharge｜Spectrometer inspection} · signal n Zhang · Background m open</span>`
(`GET /api/runs`, reloaded when `revs.runs` changes; no refresh button). `.adm-foot`: `button.btn` “capture spectrum (no discharge)” → dialog
“capture spectrum (no discharge)”: detail “Check spectrometer only: read with integration time of plasma parameters 3 Zhang, high voltage is not turned on. This is not a measurement background. ”; confirm “Retrieve” →
`POST /api/rpi/tool {cmd: "spectrum"}`. Disabled with its reason in a small line while the equipment is busy or the RPi is not connected.

**Right** — `.ph` with `.spec-info` “background b Zhang · Signal s Zhang · Points {ms} ms · {V} V · After pressing {wait_s} seconds to start discharging”. `.spec-ctl`: `.seg`
“Spectrum” ”Single Check” · `.seg` “Average” “Single image” “All stacked images” · `.fnav` “◀ No. i / N open ▶” (mode leaflet) · `.seg` “Full Spectrum” ”Near the Highest Peak”.
Spectrum: `.chart` 620 × 308; lines `.sl-mean` (black, mean), `.sl-frame` (blue, one frame), `.sl-over` (light blue, all frames);
legend “black: average n Zhang (button background)” / “blue: No. i Zhang (button background)” / “Light blue: Each picture · Black: Average (all deduct the background)”. Data `GET /api/runs/<job>`; `a.btn`
“download CSV”. Single sheet inspection: table Zhang Ci (56) · peak (96) · Peak wavelength (96) · total intensity (96) · difference from median (96) · Reminder (flex: “difference > 20%” / “below background”);
10 per page; a tap on a row shows that frame in Spectrum. Nothing selected: “Select the set of spectra on the left”.

### E.15 Tab operator

One `.panel.adm-full.table`. `.ph`: `<h2>Operator list</h2>` + `.sub` “You need to select a name before starting the schedule; each measurement and operation event will be recorded.” · `.sp` ·
`button.btn` “New operator”. Columns `#` (56) · Name (flex) · delete (72, `button.ack` “Delete”); 8 per page.
“New operator” → text dialog “New operator” with the rule line “1–20 characters, Chinese can be typed directly”; “Delete” → dialog “Delete operator” / line “name” → both
send the whole list with `POST /api/operators`. Empty list: “No operator yet” / “At least one operator is required to start scheduling. ”

### E.16 All dialogs (check-list for the implementers)

| Side | Dialogs |
|---|---|
| Frame | “Select operator” · ”There is no operator list yet” · ”Alert · {level}” · “message” · ”The schedule cannot be started at this time” · ”Start the schedule” / “⚠ Start scheduling: High voltage will be automatically output for each measurement” · ”Scheduling has not started” · ”Continue scheduling” When the version on |
| Overview | “step failed” |
| Schedule measurement | “Select Recipe” · “Not yet Recipe” · “How to start” · “start time: hour” · “start time: point” · “Measurement interval” · “end condition” · “Number of measurements” · “end time: hour” · “end time: point” |
| manual operation | “Continuous operation flow” · ”Auto stop seconds” · ”Start {motor}” · “{motor} Quantitative method” · ”Quantitative volume” · ”Quantitative weight” · “{motor} Quantitative” · “automatic cleaning” · “Injection volume” · “syringe pump” · “Syringe pump injection” · “Syringe pump initialization” |
| Maintenance | “Maintenance operation message” · Recipe: “Example CSV files” · “Recipe name” · ”Release Recipe” · “delete Recipe” · “input repair PIN to delete” · “CSV cannot be read” — Wired: “RPi connection setting” (+ “RPi address”, “TCP port”, “token”) · “connection diagnosis” · ”Automatic search RPi” · “Use instead {host}” · “Select the port for the pump control board” · ”Connect to {device}” · “Not using pump control board” · ”Rewiring pump control board” - Plasma parameters: one pad per parameter · “Store plasma parameters” · ”Synchronized to Nano” · “Clear Nano Fault” · “⚠ Manually start plasma: high voltage will be output” — Pumps and Scales: “Syringe capacity” · ”Syringe pump speed” · ”Scale zero” · ”Weight weight” · ”Scale calibration” — Spectrum: “capture spectrum (without discharge)” — Operator: “Add operator” · ”Delete operator” |

### E.17 Web code: files and contract (`pc\web\`, ES modules, no build step, no dependency)

| File | Package | Contents |
|---|---|---|
| `index.html` | WP6 | static skeleton: `.hmi` > `.top`, `.banner`, `.body` (`.nav`, one empty `<section class="page" id="page-…" hidden>` per page), `.foot`, `#overlay`, `#dialog`; four `<link rel="stylesheet">`; one `<script type="module" src="/js/main.js">` |
| `css\base.css` | WP6 | tokens, `@font-face`, reset, scaling, frame regions (top, banner, nav, footer) |
| `css\components.css` | WP6 | `.panel` `.ph` `.btn` `.seg` `.tag` `.chip` `.st` `.pri` `.table` `.trow` `.pager` `.li` `.empty` `.notice` `.kv` `.stile` `.pbtn` `.gbtn` `.vol` dialogs, pad, tick rows, `.chart` |
| `css\pages.css` | WP7 | Overview, Schedule measurement, manual operation, execution record, alarm |
| `css\maint.css` | WP8 | login, `.adm-*`, the six tabs |
| `fonts\barlow-400.ttf` `barlow-500.ttf` `barlow-700.ttf` `barlow-condensed-400.ttf` `barlow-condensed-600.ttf` `Barlow-OFL.txt` `BarlowCondensed-OFL.txt` | WP1 | copied from the original (F.2) |
| `js\main.js` | WP6 | start: bootstrap, scaling, poll loop, router (hash), activity timer for the PIN, calls `render` of the frame and of the visible page |
| `js\api.js` | WP6 | `get(path)`, `post(path, body)` with `X-Token`, `X-Unlock`, one re-bootstrap + retry after “Invalid local request”; `ApiError {status, code, message, reasons}` |
| `js\store.js` | WP6 | holds `boot`, `state`, `stale`, the unlock session, the schedule form and its preview; `subscribe(fn)` |
| `js\dom.js` | WP6 | `h(tag, props, …children)` (textContent only), `setText`, `setClass`, `list(container, rows, key, make, update)` (keeps rows under the pointer), `pager(container, total, perPage, onChange)` |
| `js\dialog.js` | WP6 | E.3: `confirm({...})`, `choose({...})`, `pad({...})`, `pin({...})`, `text({...})`, `message(title, text)`, `form({...})`; all return promises that resolve to the value or `null` |
| `js\labels.js` | WP6 | every shared Chinese word of E.8, `fmtTime`, `fmtDuration`, `motorLabel(id)`, `pumpText(pump)`, `stopMessage(reply)` |
| `js\frame.js` | WP6 | top bar, banner, nav, footer (E.2), the operator picker, the start and resume dialogs (E.5.3), the browser-side alarm `ui_stale` |
| `js\chart.js` | WP6 | `drawSpectrum(svgBox, {axis, series, range})` — SVG, nice ticks, min / max decimation (logic moved from 1.3.0 `web\app.js`) |
| `js\pages\dash.js` `schedule.js` `manual.js` `runs.js` `alarms.js` | WP7 | one page each |
| `js\pages\maint.js` | WP8 | login panel, tab bar, message chip |
| `js\pages\maint-recipe.js` `maint-link.js` `maint-plasma.js` `maint-pumps.js` `maint-spectra.js` `maint-operators.js` | WP8 | one tab each |

Removed: `pc\web\app.js`, `pc\web\style.css` (WP6).

Contract of a page or tab module:

```js
export function mount(root, ctx) {}   // once; build the static DOM of the page inside root
export function render(state, ctx) {} // after every poll and after local changes, only while visible; must be idempotent
export function show(ctx) {}          // optional: the page became visible (start its own fetches)
export function hide(ctx) {}          // optional
```

`ctx = {api, store, dialog, dom, labels, chart, go(page, arg), message(text, level), unlocked()}`. A module touches only its
own `root`; shared things go through `ctx`. `render` must not rebuild a list whose data did not change (use `dom.list`) and
must never replace a control that is being pressed. `main.js` loads each page module with a dynamic
`import('/js/pages/<name>.js')` (fixed names: `dash`, `schedule`, `manual`, `runs`, `alarms`, `maint`, and `maint-recipe`,
`maint-link`, `maint-plasma`, `maint-pumps`, `maint-spectra`, `maint-operators`) and shows a placeholder panel while a
module does not exist yet; WP7 and WP8 add files, they never edit WP6's. The exported names of the shared modules are
frozen in `WORKPACKAGES.md` (H.3, WP6).


---

## F. Vendoring, launchers, installer, versions

### F.1 V7 code (byte-identical copies, binary mode; `V7\` = `C:\Users\PlasmaLab\Desktop\independent laboratoryv7`)

| Source | Destination under `pc\vendor\v7\` | Used |
|---|---|---|
| `V7\backend\__init__.py`, `config.py`, `registry.py`, `transport.py`, `simulator.py` | `backend\` (same names) | at run time |
| `V7\backend\functions\__init__.py`, `motors.py`, `pump.py`, `scale.py` | `backend\functions\` | at run time |
| `V7\config\hardware.yaml` | `config\hardware.yaml` | read by V7's `config.py` (`LAB_CONFIG`); never edited, never rewritten |
| `V7\config\scale_calibration.json` | `config\scale_calibration.json` | seed for `<data>\pumpboard\` (D.3-3); V7 writes only to the data folder (`SCALE_CALIBRATION_FILE`) |
| `V7\firmware\Arduino\Arduino.ino`, `hw_config.h` | `firmware\Arduino\` | reference only |
| `V7\tools\gen_firmware_config.py`, `tools\__init__.py` | `tools\` | reference only |

Not copied: `backend\main.py` (FastAPI), `backend\__pycache__`, `frontend\`, `docs\`, `tools\selftest.py`, `tools\probe.py`,
`tools\url.py`, `run.bat`, `selftest.bat`, `requirements.txt`, `README.md`, `.venv`.
Vendored files keep their own line endings (they must stay byte-identical); EC's LF rule applies to EC's own files only.

### F.2 Third-party packages and fonts (no download)

| What | Source | Destination | Licence text |
|---|---|---|---|
| pyserial 3.5 | `V7\.venv\Lib\site-packages\serial\` — every `.py` file including `tools\`, `urlhandler\`, `threaded\`; no `__pycache__` | `pc\vendor\site\serial\` | `…\pyserial-3.5.dist-info\METADATA` and `DESCRIPTION.rst` → `pc\vendor\licenses\pyserial-3.5\`. **That folder holds no licence file** (verified by a judge: it has DESCRIPTION.rst, METADATA, metadata.json, RECORD …); `METADATA` says `License: BSD` and the sources carry `SPDX-License-Identifier: BSD-3-Clause`. Recorded in `pc\vendor\README.txt`. **Nothing is written from memory**; getting the full text would be a download and needs the owner's yes |
| PyYAML 6.0.2 | `V7\.venv\Lib\site-packages\yaml\*.py`. **Not** the compiled `_yaml*.pyd` (built for another Python; `yaml.__with_libyaml__` is then False, which is fine) | `pc\vendor\site\yaml\` | `…\PyYAML-6.0.2.dist-info\LICENSE` (MIT) and `METADATA` → `pc\vendor\licenses\PyYAML-6.0.2\` |
| Barlow, Barlow Condensed | HMI `web\vendor\font-a35caa4687f434d6.ttf` (Barlow 400), `font-91c841fdfa8e7b94.ttf` (500), `font-74103fd6d41bc32c.ttf` (700), `font-c82d65280835cb3c.ttf` (Condensed 400), `font-0d85af813fc3ed87.ttf` (Condensed 600) — mapping read in HMI `web\vendor\fonts.css` | `pc\web\fonts\barlow-400.ttf`, `barlow-500.ttf`, `barlow-700.ttf`, `barlow-condensed-400.ttf`, `barlow-condensed-600.ttf` | HMI `web\vendor\Barlow-OFL.txt`, `BarlowCondensed-OFL.txt` → `pc\web\fonts\` |

`pc\vendor\README.txt` (UTF-8, LF): what was copied, from where, on which date, V7's `backend.__version__` (`5.0.0`), the rule
"do not edit; changes belong in `pc\pumpboard.py`", how to take in a V7 change (copy again, `check_v7_sync.py --write-manifest`,
run the tests), and the pyserial licence note.
`pc\vendor\MANIFEST.sha256`: one line per vendored file and per font file (`sha256  size  relative path`), written at vendoring time.
`SHA256SUMS.txt` (the 22 protected files) is not touched.

### F.3 Sync-check tool — `docs\tools\check_v7_sync.py` (read-only, standard library, run like `check_sums.py`)

| Call | Does | Exit |
|---|---|---|
| no argument | every file under `pc\vendor` and `pc\web\fonts` against `MANIFEST.sha256`; extra or missing files; any `.pyd`, `main.py` or `__pycache__` under `pc\vendor` | 1 on any difference |
| `--v7 <folder>` (default: `%USERPROFILE%\Desktop\independent laboratoryv7` when it exists) | each vendored V7 file against its source: “same” / “different” / “V7 does not have this file”; lists V7 files in `backend\` and `backend\functions\` that are not vendored; compares `serial\` and `yaml\` with V7's `.venv` when present; compares V7's `scale_calibration.json` with `pc\data\pumpboard\` (information only) | 1 when a vendored file differs from V7 |
| `--write-manifest` | rewrites the manifest (once by the vendoring package, and after a deliberate re-sync) | 0 |

### F.4 Launchers

| File | 2.0.0 |
|---|---|
| `Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`, `Install.cmd`, `Flash-Nano.cmd`, `installer\unpack-python.cmd` | **byte-identical** |
| `Set-PIN.cmd` | only its two `rem` lines change (ASCII, CRLF): `rem Sets the 6-8 digit maintenance PIN. The maintenance pages stay closed until a PIN is set.` / `rem Run this again to replace a forgotten PIN.` |
| new launchers | none (the pump board needs no installer; the maintainer tool `check_v7_sync.py` is run like `check_sums.py`) |

No `__pycache__`: `sys.dont_write_bytecode = True` before the first local import in `pc\app.py` and before `import app` in
`installer\install.py`; child processes (RPi simulator, guard) are started with `-B`; tests and tools are run with `-B`.
`app.py` options: `--demo`, `--port`, `--data`, `--no-browser`, `--set-pin` as before, plus one new `--sim-port` (practice mode
only, default 8767: the port of the RPi simulator, so two practice instances can run on one PC during the build; no launcher
uses it). `Start-Demo.cmd` now starts both simulators (RPi `--sim` and V7 `link.simulate`).

### F.5 Installer (`installer\install.py`)

- `sys.dont_write_bytecode = True` before `import app as pc_app`. The four names it uses (`write_json`, `VERSION`,
  `validate_config`, `DEFAULT_CONFIG`) stay exported by `app.py`.
- `step_computer()`: the "folder complete" list loses `pc/web/app.js` and gains `pc/application.py`, `pc/httpd.py`,
  `pc/store.py`, `pc/migrate.py`, `pc/pinlock.py`, `pc/alarms.py`, `pc/rpilink.py`, `pc/recipe.py`, `pc/pumpboard.py`,
  `pc/pump_guard.py`, `pc/devices.py`, `pc/steps.py`, `pc/runner.py`, `pc/manual.py`, `pc/recipes/plasma-only.csv`,
  `pc/vendor/MANIFEST.sha256`, `pc/vendor/v7/backend/transport.py`, `pc/vendor/v7/config/hardware.yaml`,
  `pc/vendor/site/serial/__init__.py`, `pc/vendor/site/yaml/__init__.py`, `pc/web/js/main.js`, `pc/web/css/base.css`.
- One printed line after the shortcut step: “pump control board (Arduino Mega) No need to install: USB cable into this computer. After opening the program, go to ”Maintenance › Connection” select a port. ”
  and one: “maintenance page must have PIN can enter: If it has not been set yet, double-click Set-PIN.cmd.”
- Nothing else changes: `config.json` keeps its shape, the RPi steps are untouched, the installer never touches a COM port.

**RPi update (tcpd only, no reboot) — done by the owner after the PC tests pass; goes into the guide:**
1. Close the control screen and the black window (the token changes). Nothing may be running (`busy` false).
2. Double-click `Install.cmd`. The RPi already has the tools, so only the programs in `tcpd\` are replaced and the
   `plasmic-tcpd` service is restarted. No reboot, the Nano is not reset.
3. The installer writes the new token into `pc\data\config.json` and reads `status` once.
4. Start the program: chip “RPi is connected”; Maintenance › connection shows “Measurement Process Background 1 sheets, click to make approx. Stop automatically after 4 Second discharge”.
Until step 2 is done, PC 2.0.0 against RPi 1.3.0 connects, shows everything and refuses every discharge with
“RPi is still an old version: please double-click first Install.cmd renew RPi”.

### F.6 Version numbers

| Part | Version |
|---|---|
| PC program (`pc\app.py` `VERSION`) | **2.0.0** |
| RPi TCP server (`rpi\tcpd\plasmic_tcpd.py` `VERSION`) | must become **2.0.0** — the file still says `1.3.0` `[FACT :46]`, and the installer pushes the RPi package only when the versions differ. Owned by the separate RPi review; a dependency of this plan, not its work |
| TCP protocol | 1 (unchanged) |
| Nano firmware | 1.2.1 (unchanged) |
| Mega firmware | unchanged; unversioned; identified by the `READY` line and the two-number fingerprint |
| vendored V7 backend | `5.0.0` (its own `__version__`); identified by `MANIFEST.sha256` |
| pyserial / PyYAML | 3.5 / 6.0.2 (pure-Python parts) |
| data layout (`version.json` `data_version`) | 2 |

---

## G. Test plan

`unittest`, standard library, bundled interpreter with `-B`, each file runnable by itself, only `127.0.0.1`,
`netcheck.local_ipv4` replaced by `[]` in every new harness, `PLASMIC_NO_SERIAL=1` set by `tests\pump_testing.py` before any
import. Pump tests use `pump_mode='sim'` with the tripwire installed and config overrides for short times
(`cleaning.*_s`, `valves.*_ms`, `gravimetric_dose.settle_s`, `link.simulation_speedup`).

About fixed decision 12 ("the existing five test files stay green"): `tests\test_app.py` cannot stay as it is — it still
sends `sequence`, which the RPi side no longer knows, and it calls routes that 2.0.0 removes. It is **ported** (same
assertions, new routes). `test_installer.py` only gains cases. `test_tcpd.py`, `test_measure.py` and `test_flash_nano.py`
are not touched by this plan.

| File | Package | Important cases |
|---|---|---|
| `tests\test_vendor.py` | WP1 | manifest matches every vendored and font file; no `.pyd`, no `main.py`, no `__pycache__`, no `fastapi` / `uvicorn` import under `pc\vendor`; the vendored modules import under the bundled interpreter with the sim patch; 26 functions register; `yaml.__with_libyaml__` is false; licence files present; `hardware.yaml` has the keys `recipe.py` and `pumpboard.py` read |
| `tests\test_installer.py` | WP1 (extended; the RPi cases are not edited) | the required-file list of F.5; `.cmd` still ASCII; `Set-PIN.cmd` CRLF; every launcher except `Set-PIN.cmd` unchanged (hash recorded in the test) |
| `tests\pump_testing.py` | WP2 (helper) | `install_tripwire()`, simulated port lists (`COM91`…), `FaultPlan`, `make_board(tmp, **overrides)`, scripted `SimulatedLink` for `link_factory` |
| `tests\test_port_safety.py` | WP2 | a non-simulated board without `allow_real_ports()` raises `RealPortForbidden` before pyserial is touched; with `PLASMIC_NO_SERIAL=1` even after `allow_real_ports()`; `Application(tmp)` (old harness form) leaves the board `off`; `enabled: null` → nothing opened; only `pc\app.py` calls `allow_real_ports(`; only `pumpboard.py` and `pump_guard.py` import `serial`; no file under `tests\` or `pc\` names `COM3`; `application.py` passes no `overrides` / `link_factory` / `port_lister`; tripwire record is empty at module end |
| `tests\test_pumpboard.py` | WP2 | **port choice**: a Mega is offered, a CH340 (1A86:7523) and a port without USB id never; a saved port that is no longer a Mega is not opened; identity follows a renumbered port and logs it; two identical boards → `ambiguous`; the cached config never holds `auto`; two links do not share the `link` dict. **Identity**: wrong reply → port closed, nothing but `?` was written, no automatic retry. **Connect**: traffic order `X` → `W,DIV` → `W,OFFSET` → `W,AVG` → `W,STABLE` → `W,STREAM` → `?` → `Y,SYR` → `Y,DETECT`; `X` also on the first connect. **Run**: deadline stops the motor; second motor of the same group refused; A and B together; `stop_motor` verifies `idle`; M2 opens valve 1 and waits. **Dose**: `wait → move → idle` incl. valve close; timeout → `X`; early finish refused. **Weight dose**: reaches the target; refused without a calibration file; `stop_all` cancels it without waiting for `op_lock`; hard timer. **Clean**: completes with short times; `stop_all` aborts it. **Syringe**: init → aspirate → dispense bookkeeping; over-capacity refused; `PUMP,…,error=7` → fault + alarm; bookkeeping void after `stop_all`; `Y,INIT` is sent with a 5 s timeout. **Faults**: dropped reply → `resync` with `W,CFG`; stray line without terminator → timeout, not success; `ERR,…` texts; cable pulled during a dose → `lost`, `motion_uncertain`, alarm, reconnect; pulled during a weight dose → V7's text replaced; `READY` mid-operation → failure and B.3.4 again — **also while 500 `WEIGHT` lines flood the link**; heartbeat failure → `lost`. **Stops**: `stop_all` ends with the stream back on; `close()` writes `X`; `stop_all` with the link down reconnects once. **Gate**: motion commands refused while the HV flag is set; allow-list refuses `W,SCAN`. One live board per process; vendored `hardware.yaml` unchanged after the run (hash) |
| `tests\test_pump_guard.py` | WP2 | (with a simulated serial module injected) `bye` → port not opened; end of input while not armed → not opened; end of input while armed → open, `X`; port not a Mega → not opened; port busy → retries, gives up after 10 s and logs; `PLASMIC_NO_SERIAL` → exits |
| `tests\test_recipe.py` | WP3 | every action: good row, each bad column, range, step; R1–R11 each with the smallest failing CSV and its exact message; W1–W6; repeat expansion, rounds, limits; `planned_s`; `liquids`; estimates; phases; step sentences from the YAML labels; the three example files validate (example 1: `uses_pump` false); `decode`: UTF-8, UTF-8 with BOM, cp950 (warning W6), garbage (the exact instruction); a new syringe size makes a published Recipe invalid (R11) |
| `tests\test_store.py` | WP3 | atomic write survives a `PermissionError` retry; **migration** from a synthetic 1.3.0 folder (`tests\fixtures\data-1.3.0\`: `config.json`, old `schedule.json` in state `running` with `sequence`, two `runs\*.meta.json`, `events.jsonl`, `pin.json`): nothing deleted, `config.json` / `pin.json` byte-identical, backup made, “Discharge measurement only” published, “Operator” seeded, calibration seeded, `runlog.jsonl` built, old schedule still loadable, second run changes nothing; fresh folder; Recipe SHA check, duplicate name, delete = move; operator rules; alarms: raise / clear / ack / reload, `condition` alarms cleared at load, events written; `PinLock`: no file → not allowed (`pin_unset`), lockout doubling, `peek` does not renew, file replaced → sessions end, practice PIN never reads a file, `--set-pin` text |
| `tests\test_rpilink.py` | WP3 | (on `test_tcpd.SimServer`) `hv_view()` for every row of B.4 (simulator states and edited status dicts, both ages); `flow_view()` for `unified` / `verified` / missing; `params_view()` synced / not synced; `claim=True` → the link thread does not save or log the job; `wait_job` with a link gap (shortened); `fetch_result` retries the read, never the discharge; STOP uses a fresh connection; stale snapshot → `status {fresh: true}` is sent; `rewrite_fix` covers every button name found in `plasmic_tcpd.ready_problems` (source scan, read only); no `sequence` in any request |
| `tests\core_testing.py` | WP4 (helper) | builds Store + Alarms + RpiLink on `test_tcpd.SimServer` + PumpBoard (sim, tripwire) + Devices + Runner + ManualOps in a temp folder; shrinks `schedule.MINUTE` |
| `tests\test_interlock.py` | WP4 | ownership matrix (round / manual / tool / schedule waiting / paused between rounds / paused round); `permit_motion` × (RPi idle, HV on, stale, not connected with / without the tick); `hv_session` × (board quiescent, moving, not in use, enabled but not connected, uncertain, valve still open); **race**: 200 rounds of "manual run" against "`hv_session`" on two threads → never both active; `stop_everything` result matrix incl. board link down (→ Emergency alarm), RPi unreachable (pump half still confirmed), idle STOP |
| `tests\test_runner.py` | WP4 | “Discharge measurement only” with the board `off`: one slot, spectrum saved once, round record complete (CSV, parameters, job, versions); the pump + plasma example with short overrides (weights, liquids, `plasma.jobs`); cleaning Recipe without RPi needs `hv_off_checked`; missing ticks refused; no operator refused; `hello.flow` removed → refused with the Install.cmd text; `verified` allowed + alarm; **failure of each step type** (`FaultPlan`) → round `failed`, simulator all idle, RPi `stop` sent when the Recipe has RPi steps, schedule `failed`, alarm; **STOP** during `delay` / `pump_on` / `dose_volume` / `dose_weight` / `clean` / syringe move / `plasma_run` → `stopped`, everything off; **PAUSE** in a `delay` with a running pump → pump stopped, resume restarts it with the remaining time; pause during a dose → `pausing` then `paused`; pause while waiting → `skipped_paused`; `skipped_overrun`; three `not_started` end the schedule; deadline trip; pump left running at the end (preflight bypassed in the test) → `X` + `failed`; board lost mid-dose; RPi connection cut during `plasma_run` and during pump steps; restart with a `running` round on disk → `interrupted`, nothing restarts, one RPi `stop`; a discharge is never sent twice; a schedule goes on when nobody polls `/api/state` |
| `tests\test_manual.py` | WP4 | every operation; **page watchdog** stops a manual run after the page went silent (shortened); manual locked while a schedule is busy, allowed while it is paused between rounds; RESUME refused while a manual pump runs; OFF always accepted; second blocking operation refused; RPi not connected → refused without `hv_off_checked`; scale Return to zero refused while the reading is not stable |
| `tests\test_app.py` | WP5 (**ported, not rewritten**) | every 1.3.0 case that still describes wanted behaviour moves to the new routes with its assertions: token / Origin / Host; parameters → discharge → spectrum endpoints and CSV; STOP cancels a running discharge; attended-discharge page watchdog (`rpilink.UI_WATCHDOG_SECONDS` patched); program-death watchdog; find / moved RPi; first start from `site.json`; new-Nano auto-sync; offline texts; token never shown; schedule slot rules, never resumed after a restart, three slots that cannot start, a run that does not end cleanly. Only the three sequence tests (1.3.0 `test_app.py:132-171, 314-337`) and the TCP-terminal test are deleted. `Harness` selects the seeded operator and the seeded Recipe; `sim_fault` is injected through `h.sim` / `plasmic_client` directly (there is no `/api/raw`). New: static table (every file type, `..` / encoded traversal refused, CSP header on all); `/api/state` has every key of API.md; `/api/stop` is 200 without PIN and when idle; removed routes answer 404; two sessions see the same operator and alarms; practice mode: `Application(demo=True)` uses the board simulator and `serial.Serial` is never called |
| `tests\test_api_pin.py` | WP5 | table-driven over API.md: every gated POST is 403 `locked` without a session and passes the gate with one; every open route works without; no PIN file → gated routes 403 `pin_unset`; delete needs the PIN again; `/api/state` does not renew; session expiry; every GET is open |
| `tests\test_web_static.py` | WP6 (covers WP7 and WP8 files too) | no `<script>` without `src`, no `style=`, no `on…=` in `pc\web\*.html`; no `innerHTML`, `eval(`, `new Function`, `http://`, `https://` in `pc\web\js`; every static `import` path exists; every `/api/…` path used in the JS exists in `httpd.py`'s route table; fonts and licence files present; web files are LF and UTF-8 |
| `tests\test_tcpd.py`, `test_measure.py`, `test_flash_nano.py`, `simulated_rig.py`, `simulated_spectrometer.py` | RPi review / untouched | must stay green |

Three cases that must exist whatever else is cut: (1) a test process cannot open a real port (`test_port_safety`);
(2) pumps and high voltage are refused together in both directions (`test_recipe` R5, `test_interlock`, `test_runner`);
(3) STOP reaches both the pump board and the RPi even when one of them does not answer (`test_interlock`, `test_app`).

After every change: `docs\tools\check_sums.py` (22 protected files) and `docs\tools\check_v7_sync.py`; no `__pycache__` left;
`pc\data\config.json` still points at `192.168.1.1`.

---

## H. Build plan

See `WORKPACKAGES.md`.

---

## I. Documentation changes (list only)

`docs\guide-source\installation-and-usage.src.html` (then rebuilt with the new test count)
- Look here first: six steps — step 2 mentions the pump board's USB cable; step 6 rewritten for the new pages
- Safety instructions — pumps: software STOP is not an emergency stop; the Mega has no watchdog; cut the pump power before work on tubing; “screen display OFF does not mean that the electrode is out of power” stays
- What does the whole set look like? — new drawing: PC ─USB─ Mega (motors 2–9, valves, syringe pump, scale); PC ─cable─ RPi
- Steps 3 Connect to the Internet — heading unchanged; new sub-part: the pump board's USB cable
- Steps 4 One click installation — the RPi update to 2.0.0 (four steps of F.5); nothing to install for the pump board; `Set-PIN.cmd` is now required for Maintenance
- Steps 5 First power-on inspection — heading unchanged; new checklist = the "first check on the rig" column of section J, in that order
- Steps 6 Daily operation — rewritten: Overview, Schedule measurement (single measurement = common 1 Second-rate), manual operation, execution record, alarm, the three footer buttons
- new section Maintenance — the six tabs; choosing the pump board's port once
- new section Recipe how to write — the action table of A.2, the rules with their messages, the three examples, Excel “Save as new file → CSV UTF-8 (comma separated) ”
- when something goes wrong — pump board not found / port busy / not a pump board / link lost / board restarted; RPi still old; syringe error; scale silent; Emergency alarms and what to do
- References — files under `pc\data` (D.1); how to take in a V7 change (`check_v7_sync.py`); third-party licences; the TCP section stays but loses "sequence"; Allow list heading unchanged
- Verification status — rewritten: everything of 2.0.0 is simulator-only until the owner has done step 5
- removed everywhere: fast/original, “order”, background 3 open as the normal case, `plasma_quick.py`, `test_quick.py`, the TCP terminal, “reading status”, “Stop schedule”
- screenshots retaken in practice mode (`{{IMG_*}}`): Overview, Schedule measurement with the start dialog, manual operation, execution record detail, alarm, Maintenance login, Maintenance › connection, Maintenance › plasma parameters

`docs\guide-source\README.txt` — screenshot list and commands for the new pages; `docs\guide-source\shots_app.py` — uses the new routes
`READ-ME-FIRST.txt` (UTF-8 BOM + CRLF) — what 2.0.0 is; pump board on this PC's USB; PIN is required for Maintenance; the first three things to do
`historical-development-notes.md` — version 2.0.0; architecture drawing with the Mega; file table (new modules, `pc\vendor`, `pc\recipes`); the sentence "PLC and pumps were removed" replaced; rule 2 extended ("review-workspace never makes a pump, the syringe pump or a valve move on the real rig; COM3 is never opened by review-workspace; never start `Start.cmd`; simulators only; set `PLASMIC_NO_SERIAL=1`"); rule 4: `plasma_measure.py` instead of `plasma_quick.py`, vendored files + `MANIFEST.sha256` + `check_v7_sync.py`; rule 9: `pumpboard.json`; test list and counts; "one measuring flow"; where things stand (nothing of 2.0.0 run on the rig)
`docs\work record.md` — new section 13: the merge, the decisions of 0.2, what is unverified (section J), the "Not done yet" list (section K, real-rig checks)
`docs\1-How to continue on another computer.txt` — the pump board is on this PC's USB; on another PC choose the port again
`pc\vendor\README.txt` — F.2
Titles cited by programs (`Steps 5 First power-on inspection`, `Steps 3 Connect to the Internet`, `References › Allow list`, `Steps 1`) stay unchanged.

---

## J. Risks, and what stays unverified until the owner tries it on the real rig

"First check" = what the owner presses, in a safe order (water only, low voltage, hand on the physical switches). These
rows, top to bottom, are the step-5 checklist of the guide. review-workspace never performs any of them.

| # | Item | Why it matters | Mitigation in this design | First check on the rig (owner presses) |
|---|---|---|---|---|
| 1 | Nothing of 2.0.0 has run on the rig; the RPi's unified flow (`plasma_measure.py`) has not either | every claim is "simulator only" | Verification status in the guide; the checklist below; the RPi's `verified` fallback stays usable | — |
| 2 | First real open of COM3 with pyserial under the embeddable Python (proven only by import and simulator) | first connect | clear text for every open failure; `logs\pumpboard.log` | Maintenance › connection › Select port → COM3 → “connection”; expect “Connected COM3”, “Firmware response correct” |
| 3 | Does this Mega reset when the port is opened? `[INFER]` | reconnect and the guard rely on "open = everything off" | `X` is sent after every open anyway; `banner_seen` is recorded in the log | read the connection tab after step 2; press “Reconnect” once and listen for the relays |
| 4 | Fingerprint / firmware build in the Mega | cleaning times, limits | alarm `firmware_mismatch`; cleaning waits follow the observed `cleaning` flag | “Firmware Settings” line on the connection tab |
| 5 | Each pump by hand | wiring, direction, valve delay | per-run deadline, page watchdog | manual operation: Pump 1 · water, 10 mL/min, 10 Automatically stop in seconds, “ON”; watch it stop by itself; then “OFF” during a run; then each other motor in use |
| 6 | STOP during a pump run | STOP path | `X` + verification | start a 30 s run, press “Stop STOP”; expect “Stopped: The pump has stopped...” |
| 7 | Closing the page during a manual run | page watchdog | 10 s rule | start a 60 s run, close the browser tab; the pump must stop within about 10 s |
| 8 | Real dose time versus `steps ÷ sps`; speed cap after a `P` move `[INFER]` | time limits; W1 | limits `× 1.5 + 10 s`; warning W1 | Quantitative 10 mL with water; note the time; afterwards “ON” at 80 mL/min in the same group and compare by ear |
| 9 | Saved scale calibration (2026-08-26) may no longer be valid; `W,TARE` / `W,CAL` take one reading | weight doses | date shown; Return to zero / Correction only while stable; weight dose refused without a loaded calibration | Maintenance › Pump and Scale: Return to zero with the empty cup, Correction with a known weight; then Quantitative (weight)10 g with water |
| 10 | SY-01B `ready` bit during a stroke; plunger position after `Y,INIT`; whether `T` un-initialises it | syringe step completion and bookkeeping | 80 % minimum time + `ready`; bookkeeping void after every stop; `error ≠ 0` always fails | manual operation: “initialization” (outlet into a beaker), Liquid extraction 1 mL from Valve position with water, Liquid injection 1 mL |
| 11 | Auto-clean on the rig (firmware timings) | 2 minutes of unattended pumping | waits follow the `cleaning` flag; STOP = `X` | “Start cleaning” with water; press “Stop STOP” once in the middle of a second run |
| 12 | RPi update to 2.0.0 and the unified flow | the measurement itself | old program → discharges refused with the fix | `Install.cmd` (F.5); connection tab shows “background 1 open…” |
| 13 | “Discharge measurement only” once at low voltage; "not more than 5 seconds" | the owner's request | `wait_s` is stored and shown | plasma parameters 30 V; Schedule measurement: Only discharge measurement, common 1 Second-rate; read “discharges in a few seconds after pressing” in execution record |
| 14 | STOP during a discharge; page watchdog of the maintenance discharge; program-death watchdog | high-voltage stop paths (never verified on the rig, also not in 1.3.0) | RPi's own OFF sequence; alarms when unconfirmed | a common 1 Second-rate schedule with Number of rounds raised, “Stop STOP” during Discharging; then a maintenance discharge and close the tab |
| 15 | A pump + plasma Recipe; PAUSE / RESUME; interlock both ways | the merge itself | preflight R5, `hv_session`, gate | import the adapted example; common 1 Second-rate; press “pause PAUSE” during the drain and “continue RESUME”; try “ON” on manual operation during the discharge (must be refused) |
| 16 | A schedule of several rounds left alone | unattended operation | deadlines, alarms persisted | common 3 Second-rate, every 2 minutes, with the browser closed |
| 17 | No firmware watchdog (firmware is not changed): PC power cut, USB re-enumeration or a frozen firmware during a continuous run cannot be covered by software | flooding | finite steps preferred, 300 s cap, guard, Emergency alarm texts, the E-stop notice. A hardware cut-off for the pumps' 24 V is not known to exist | decide whether a physical cut-off switch is added |
| 18 | The guard (`pump_guard.py`) reopening the port after a kill; Windows giving the close handler about 5 s | last line against an endless run | armed only when needed; `fast=True` path (1.5 s) | start a 120 s manual run, end `python.exe` in Task Manager; the pump must stop within about 15 s; then close the black window during another run |
| 19 | `X` is read only when the firmware loop is free (≤ 2.5 s behind `Y,INIT`); `dose_by_weight` may return up to 5 s after STOP | STOP latency; “Stopping” a little longer | `Y,STOP` after `X`; no long-blocking command is ever sent | observed during 10 and 9 |
| 20 | High voltage is judged from a status up to 10 s old plus the RPi's own checks; the RPi has no pump interlock and another TCP client (example scripts) is not covered | a discharge sent from elsewhere | PC-side gate on every wire command; the guide says not to use other clients while 2.0.0 runs | — |
| 21 | Maintenance closed without a PIN; Return to zero behind the PIN; STOP without a question; single measurement as “common 1 times”; all GET routes open on 127.0.0.1 | habits from 1.3.0 / the original | the page says exactly what to do; each is one decision that can be reversed in one place | `Set-PIN.cmd` once |
| 22 | A program rolled back to 1.3.0 reads a `schedule.json` with the new states (`paused`) and extra keys | going back | keys are only added; a `paused` status has no 1.3.0 label (shown raw) | — |
| 23 | pyserial ships without a licence text in V7's `.venv` | redistribution hygiene | noted in `pc\vendor\README.txt`; the owner decides about a download | — |
| 24 | Motors 3–7 carry unknown liquids; syringe valve positions are unnamed; TB6600 or DM542J | example Recipes | nothing depends on them; only “Discharge measurement only” is published automatically | adapt the example files to the real tubing |
| 25 | The RPi review edits `rpi\**` and three test files at the same time (no git); it must bump `VERSION` | `Install.cmd` would not push the RPi programs | those files are in nobody else's package; F.6 | — |
| 26 | Size of the page work (about 4000 lines in three packages) | schedule | frame and dialogs first; pages are independent files; Maintenance tabs reuse 1.3.0 logic | — |

Kept out of 2.0.0 on purpose and only the owner can decide later: Correction / Trend and blank samples (the round record already keeps
the CSV, parameters, job ids and weights they need); a hardware cut-off for the pumps; a watchdog in the Mega firmware.

---

## K. Not built in 2.0.0 — and why that is still what was asked

| Not built | Reason |
|---|---|
| Correction, Trend, concentration, blank check | fixed decision 10; round records keep `sample_kind`, the Recipe text, the parameters and the job ids |
| A "measure once" / “fast” button, any sequence choice, a “Stop schedule” button | “Unified Process”: one way to measure — Schedule measurement with common 1 Second-rate as the default form; the footer STOP stops everything |
| Recipe step editor on the screen | the original imports CSV files too; an editor is a second way to make a Recipe. May be proposed later as its own package (one file, `maint-recipe.js`) |
| TCP terminal on the screen, `/api/raw`, `/api/command` | a path beside the interlocks; `pc\plasmic_client.py` stays as the command-line tool |
| “re-read status”, “Detect syringe pump”, a scale “Reread” button, a `motors.select` button, a syringe "valve only" button | the program polls, detects at connect, restarts the weight stream, selects by starting, turns the valve with the move |
| Opening a port that is not an Arduino Mega (tick box, typed name, `usb_ids` setting), an "auto" port | fixed decision 3; every open resets the device and the plasma side's Nano is a CH340 |
| Settings for the run-time and dose caps | constants shown read-only |
| `scale_tare` in a Recipe; reverse weight dosing; dosing while another pump runs; `pump_flow` / `pump_direction` rows | calibration stays behind the PIN; V7's weight dose only ends forward; direction and flow are per start |
| Flow diagram, faceplates, level / valve placeholders | fixed decision 10: no invented plumbing |
| Language switch, hidden long-press entry, on-screen letter keyboard, operator passwords | Traditional Chinese only; the nav item is the entry; the PIN is the password |
| Weight chart, weight CSV, scale diagnose / Modbus tools, editing `hardware.yaml`, regenerating `hw_config.h`, flashing the Mega | V7 is the authority and the firmware is not changed; a V7 change comes in by re-vendoring |
| Import of the original HMI's Recipes, operators or PIN | its data folder is off limits and its Recipes are timed for the PLC pumps |
| Automatic resume after a restart; automatic repeat of a discharge | never (kept from both programs) |
| FastAPI, a second server, SSE | fixed decision 1; the 1 s poll drives the screen |
