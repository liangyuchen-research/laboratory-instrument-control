# Plasmic Electronic control 2.0.0 — design proposal (angle: backend correctness and safety first)

Written 2026-10-06. Read-only work: nothing in the three project folders was changed, nothing was run, no port was opened.
Read for this proposal: `constraints.md`; historical development notes; all eleven maps in `maps\`; the Overview, Schedule, and Manual Control layouts documented in the generated English guide;
the V7 sources `backend\{__init__,config,registry,transport,simulator}.py`, `backend\functions\{__init__,motors,pump,scale}.py`,
`config\hardware.yaml` in full; EC `pc\app.py`, `pc\schedule.py`, `pc\plasmic_client.py` in full; parts of
`rpi\tcpd\plasmic_tcpd.py` (status, readiness, job end), `tests\test_app.py`, `tests\test_installer.py`, the launchers.
One slip to report: a top-level `Glob` on the EC folder returned file *names* under `pc\runtime` and `pc\data\runs`; no file there was opened.

Abbreviations: **EC** = `C:\Users\PlasmaLab\Desktop\autonomous control`; **V7** = `C:\Users\PlasmaLab\Desktop\independent laboratoryv7`;
**HMI** = `C:\Users\PlasmaLab\Desktop\UI\UI\hmi_v1_1`; **board** = the Arduino Mega pump board; **RPi** = the plasma side.
`[FACT]` = read in code. `[INFER]` = follows from code plus general knowledge, not proven here. Section J lists what stays unverified.

---

## 0. The shape in one page

```
Browser (one page, 1024x768 frame, polls /api/state every 1 s)
   │  HTTP 127.0.0.1:8766  (Host / Origin / X-Token checks, CSP default-src 'self', PIN session in X-Unlock)
pc\app.py ── Application
   ├─ Store / Alarms / PinLock            disk under pc\data (atomic JSON, JSONL logs)
   ├─ RpiLink  (was app.Link)             TCP 8767 to the RPi, unchanged protocol, STOP over a fresh connection
   ├─ PumpBoard (NEW wrapper)             the only importer of the vendored V7 code, pyserial, PyYAML
   │     └─ vendored V7: transport.Link + registry + functions.{motors,pump,scale} + simulator   (byte-identical)
   ├─ Devices  (NEW)                      one owner at a time, pump/HV mutual exclusion, STOP = everything off
   ├─ Runner   (NEW, replaces Schedule)   schedule slots -> one Recipe round each -> steps; pause / resume / stop
   └─ ManualOps (NEW)                     one manual operation at a time, tied to the page watchdog
pump_guard.py (child process, real mode)  if the main program dies without saying goodbye: reopen the port, send X
```

| # | Choice | Why |
|---|---|---|
| 1 | V7 functions are called through `registry.get(name).invoke(Context(link, cfg), payload)`. The `link` handed to them is **our `CheckedLink` proxy** around a V7 `transport.Link` | V7 code stays byte-identical, yet every wire command passes one choke point where we add: reply check, `PUMP,…error=n` check, the pump/HV gate, motion bookkeeping |
| 2 | `transport.get_link()` (singleton) is never used. A fresh `transport.Link()` per connection attempt; config through `LAB_CONFIG` → a generated runtime YAML + `config.get_config(reload=True)` | Port and simulate flag can change at run time without touching V7 files |
| 3 | Real ports need an explicit opt-in: `pumpboard.allow_real_ports()` is called only by `main()` when not `--demo`. Default is "forbidden" | A test (or any import) cannot open COM3 by accident; the existing test harness keeps working with the pump board simply off |
| 4 | A port is opened only when its USB id is an Arduino Mega (2341:0042 / 2A03:0042) — also for a saved port. After opening, exactly one thing is sent (`?`) until a well-formed `STATE,` came back | Opening a port resets whatever is on it (a Nano would flicker D13). Never guessed, never a CH340 |
| 5 | On every connect, reconnect and unexpected `READY`: `X` first, then calibration, `W,AVG`, `W,STABLE`, `W,STREAM` | Known safe state; the Mega forgets everything at reset and reports raw counts as grams (div 1.0) |
| 6 | Liquid steps in a Recipe are mostly **finite** (`pump_dose` → firmware `P`, `clean` → firmware `C`): they end by themselves even if the PC dies. Continuous `pump_run` gets a host deadline = planned time + 5 s, capped by a maintenance setting (default 120 s) | The firmware has no watchdog and is not changed |
| 7 | A small guard child process covers "program killed while a pump runs" | Closing the port does not stop the Mega; reopening it does `[INFER]` |
| 8 | One thread runs schedule + round (not two as in the HMI). Round record is written at start and updated as it goes | A crash leaves a trace; restart marks it `interrupted`, never resumes |
| 9 | STOP = stop flag + pump `X` + RPi `stop`, in parallel, always the same, also when idle. Each half is confirmed or raises a priority-1 alarm | Constraint 8; no "it depends" for the operator |
| 10 | PAUSE acts only at safe points (between steps, inside `delay`); continuous pumps are stopped and restarted with their remaining time; nothing pauses a discharge, a finite dose, a weight dose or cleaning | Those cannot be suspended without losing their meaning |
| 11 | Alarms live on the server (`alarms.json`), not in the browser | Two tabs agree; alarms are logged; testable |
| 12 | Maintenance needs a configured PIN (as the HMI). No PIN file → Maintenance shows how to run `Set-PIN.cmd` | "and original UI Same" |
| 13 | Explicit HTTP routes replace `/api/command`; the TCP terminal (`/api/raw`) is removed | No way around the interlocks; no extra buttons |
| 14 | Built-in Recipe “Discharge measurement only” = one line `plasma_run`, published automatically at migration | 2.0.0 does what 1.3.0 did right after the upgrade; press-to-discharge delay stays that of the RPi flow |

---

## A. Recipe language

### A.1 File format

- UTF-8 text (a BOM is accepted), at most 200 000 bytes. Header exactly `action,target,value,unit,note`.
- Every field is stripped. An empty row is skipped. `note` is cut to 200 characters. `line` = CSV row number (header = 1).
- Limits (same as the HMI): 500 source rows, 2000 steps after expansion, repeat nesting 5, repeat count 1–20, total `delay` ≤ 7200 s.
- A Recipe describes **one round**. Repetition belongs to Schedule measurement. No variables, no branches.
- Targets come from the vendored `hardware.yaml`, not from constants: motors `M<id>` for every channel id (today `M2`…`M9`); syringe valve positions `PORT1`…`PORT<valve_positions>` (today 3).
- Numbers are parsed with `decimal.Decimal`; a value that is not a multiple of the step is refused (no silent rounding).

### A.2 Action table

"Maps to" names V7 registry functions (`motors.*`, `pump.*`, `scale.*`) or RPi TCP commands. All V7 calls go through `PumpBoard` (section B.3). `S` = the round's stop flag.
On any failure the round ends `failed` and `Devices.all_off()` runs (B.4) — that is not repeated in every row.

| action | target | value | unit | What it does | Maps to | Completion is | Time limit |
|---|---|---|---|---|---|---|---|
| `delay` | empty | 0–3600, step 0.1 | `s` | waits; paused time does not count | — | clock; checks `S` and pause every 0.1 s | the value |
| `pump_run` | `M2`–`M9` | ±(0.1–80), step 0.1; negative = reverse | `mL/min` | starts a continuous run and goes on to the next row | `motors.run {motor, flow_ml_min, direction}` → `V,<m>,<sps>`, `?` | `OK`, then group state `wait` or `run` with `selected == motor` | reply 3 s; **host deadline = planned run time + 5 s** (planned = sum of `delay` up to its `pump_stop`), never above `max_run_s` + 5 |
| `pump_stop` | `M2`–`M9` | empty | empty | stops that motor; waits until the group is `idle` and a linked valve is closed | `motors.stop {motor}` → `V,<m>,0`, `?` | group `idle` (poll `?` every 0.3 s), valve bit 0 for motors 2 / 9 | idle 2 s, valve 3 s; then `X` |
| `pump_dose` | `M2`–`M9` | ±(0.01–`max_dose_ml`), step 0.01; negative = reverse | `mL` | delivers a volume by step count at the fixed V7 dose speed (40 mL/min, not settable) and waits for the end | `motors.dose {motor, volume_ml, direction}` → `P,<m>,<steps>,<sps>`, `?` | group back to `idle` (unsolicited `STATE` or `?` every 1 s), then valve closed for 2 / 9 | `eta_s × 1.5 + 10 s` (`eta_s` from V7's reply); then `X` |
| `pump_dose_weight` | `M2`–`M9` | 0.01–`max_dose_ml`, step 0.01 | `g` | closed loop on the scale until the weight has risen by the target | `motors.dose_by_weight {motor, target_g, direction: 1}` (blocks inside V7) | the V7 call returns; then group `idle` is verified | V7: 300 s from pump start, 30 s without gain, 4 s without a reading; host hard stop at 420 s (`X`) |
| `clean` | empty | empty | empty | firmware-timed rinse (water motor + OUT motor from `hardware.yaml`) | `motors.clean {}` → `?`, `C`, `?` | `cleaning` false and both groups `idle`, valves closed | sum of the three YAML times + valve delay + 20 s (today 141 s); then `X` |
| `syringe_init` | empty | empty | empty | homes the syringe pump (the plunger moves); then applies the saved syringe size and speed | `pump.init` → `Y,INIT`; `pump.set_syringe` if not 5000; `pump.set_speed` | `Y,ST` polled every 0.3 s until `ready=1`, `error=0` | 20 s |
| `syringe_speed` | empty | 0.001–max for the syringe (2.5 / 0.625), step 0.001 | `mL/s` | changes the speed for the following moves of this round | `pump.set_speed {speed_ml_s}` → `Y,SPEED,<raw>` | `PUMP,` reply with `error=0` | 3 s |
| `syringe_aspirate` | `PORT1`–`PORT3` | 0.001–syringe size in mL, step 0.001 | `mL` | switches the head valve, then draws in | `pump.set_valve {position}`, wait ready; `pump.aspirate {volume_ul}` → `Y,ASPUL,<µL>` | `Y,ST` → `ready=1`, `error=0`, **and** at least 80 % of `volume ÷ speed` has passed | valve 5 s; move `volume ÷ speed × 1.5 + 5 s` |
| `syringe_dispense` | `PORT1`–`PORT3` | same | `mL` | switches the head valve, then pushes out | `pump.set_valve`, `pump.dispense` → `Y,DISPUL,<µL>` | same | same |
| `rpi_status` | empty | empty | empty | reads the equipment now and requires "high voltage is off" | TCP `status` with `fresh: true` | reply; fails when `hv_view()` (B.4) is not `known and idle` | 40 s |
| `nano_prepare` | empty | empty | empty | writes `parameter.json` into the Nano (never arms) | TCP `prepare` | reply `prepared: true` | 60 s |
| `plasma_run` | empty | empty | empty | the unified measuring flow: 1 background frame, discharge, signal frames; then the spectra are fetched and stored | `Devices.hv_session()` → TCP `plasma_run {confirm_high_voltage: true}` (unattended) → poll `status` → TCP `result {job, frames: true}` | job state `done`; result saved under `runs\` | reply 40 s; job 360 s; link may be gone 30 s; result read 60 s, tried twice (the discharge is never repeated) |
| `spectrum_capture` | empty | empty | empty | spectrometer check without high voltage (3 frames, integration time of `parameter.json`) | TCP `spectrum {integration_ms}` → poll → `result` | job `done`; result saved | `3 × integration + 60 s` |
| `nano_stop`, `power_off` | empty | empty | empty | HSPY OFF + Nano STOP with read-back (two names, one action, kept from the HMI) | TCP `stop` over a fresh connection | reply `ok` | 120 s |
| `repeat_start` | empty | integer 1–20 | empty | opens a block | — (expanded before anything runs) | | |
| `repeat_end` | empty | empty | empty | closes it | — | | |

Notes
- `pump_dose` speed is V7's `dose_flow_ml_min` (40). V7 ties its dose coefficient to that speed and ignores a flow argument `[FACT motors.py:284-287]`. It is shown read-only on Maintenance › Pump and Scale.
- µL sent to the syringe are whole numbers (`round(mL × 1000)`); V7 would truncate a fraction `[FACT pump.py:105]`, the step 0.001 mL prevents one.
- `plasma_run` carries no parameters. They are the seven values of the RPi's `parameter.json` (Maintenance › plasma parameters), as in the HMI.

### A.3 Preflight (structure only; runs at publish, at preview and again at every start)

State kept while walking the expanded list: `running` (motor → flow, start line, planned seconds so far), syringe volume `vol` (µL, starts 0), `inited` flag.

| # | Rule | Message (shown as is) |
|---|---|---|
| P1 | Unknown action / wrong column use / value out of range or off-step | `No. N Column: Unsupported action ”x”` · `No. N column:delay of target should be blank and the unit should be written. s` · `No. N List: The value must be within a–b between` · `No. N column: The minimum unit of the value is s` |
| P2 | Target must exist | `No. N List: no motor Mx (Available M2–M9) ` · `No. N Column: Injection pump valve position needs to be written PORT1–PORT3` |
| P3 | One motor per driver group | `No. N column:A group can only rotate one piece at a time (motor 2 is still transferring)` |
| P4 | `pump_run` of a motor that already runs; `pump_stop` of one that does not | `No. N Column: Motor x is already transferring` · `No. N Column: Motor x is not transferring` |
| P5 | While any motor runs continuously the only allowed rows are `delay`, `pump_run` (other group), `pump_stop` | `No. N column: do ”x”All pumps must be stopped before` |
| P6 | Planned run time of every `pump_run` (sum of the `delay` rows up to its `pump_stop`) must be 0.1 s … `max_run_s` | `No. N Column: Motor x will transfer continuously T seconds, exceeding the upper limit L Second` · `No. N Column: Motor x Turn it off when it is turned on, there must be something in between delay` |
| P7 | Every `pump_run` has its `pump_stop` before the end | `The pump is still running at the end of the process: motor x (No. N column startup)` |
| P8 | Because of P5: syringe steps, `clean`, both doses and all six RPi steps only with all pumps stopped. This is the static half of "pumps and high voltage never together" | (P5 text) |
| P9 | A syringe move needs `syringe_init` earlier in the Recipe, **or** the Recipe is marked `needs_ready_syringe` (checked at start, A.4) | — |
| P10 | `vol` stays within 0 … syringe size | `No. N column: The syringe cannot be loaded (existing a mL,capacity c mL) ` · `No. N column: There are only a mL,Can’t push it out b mL` |
| P11 | Without a `syringe_init` before the first move, `vol` must be 0 again at the end | `At the end of one round, the syringe pump should return to the starting position (push as much as you suck)` |
| P12 | `repeat_end` without a block, unclosed block, nesting, count | `No. N column:repeat_end has no corresponding repeat_start` · `repeat_start less repeat_end` · `repeat most 5 layer` |
| P13 | Totals | `Expand beyond 2000 action` · `The total waiting time exceeds 7200 Second` · `CSV There is no action that can be performed` |
| P14 | Limits that depend on settings (`max_run_s`, `max_dose_ml`, syringe size) are re-checked at every start with the current settings | same texts |

Warnings (never block; shown at publish and in the start dialog):

| # | Condition | Text |
|---|---|---|
| W1 | `pump_run` above the dose speed after a `pump_dose` of the same group | `No. N column: After the same group has been quantified, continuous operation may be limited to 40 mL/min (firmware characteristics, not yet tested)` |
| W2 | more than one `plasma_run` | `this Recipe One round will discharge n Second-rate` |
| W3 | reverse direction used | `No. N Column: Motor x reverse` |
| W4 | `pump_dose_weight` on a motor with no fluid label | `No. N Column: Motor x No liquid is marked, please make sure its outlet is on the scale The` |

Result of validation: `{valid, error, warnings[], step_count, rounds, delay_seconds, estimate_seconds, phases[], uses_pump, uses_motors[], uses_syringe, uses_scale, uses_rpi, uses_plasma, needs_ready_syringe, volumes_ml{motor: total}, steps[]}`.

### A.4 Start checks (devices; at schedule start and again at the start of every round)

| Recipe has | Required now | Text when not |
|---|---|---|
| any pump / syringe / `clean` step | board `connected`, not `motion_uncertain`, both groups `idle`, not cleaning, no manual operation | `Pump control board is not connected` · `The status of the pump control board is unknown. Please go to the site to confirm first.` · `The pump is still running` · `Manual operation is not over yet` |
| `pump_dose_weight` | scale online (reading younger than 1 s), `gravimetric_dose.enabled` | `scale has no reading value` |
| `needs_ready_syringe` | syringe initialised since the last connect / stop; tracked volume + the Recipe's peak ≤ size | `The syringe pump has not been initialized yet: Go to ”Manual Operation” and click ”Initialization”` |
| any pump step, RPi connected | `hv_view()` known and idle | `The high voltage is not turned off yet:…` |
| any pump step, RPi not connected | the start dialog's tick `hv_off_checked` | `RPi There is no connection and it is impossible to confirm that the high voltage is off.` |
| any RPi step | RPi connected, status younger than 10 s, not busy | `RPi Not connected` · `RPi is doing other work` |
| `plasma_run` | `status.ready.plasma` true (the RPi's own list) | `The pre-discharge inspection failed:` + the RPi's reasons |
| `plasma_run`, no pump step | board either not in use (`off`, `not_found`, never connected) or connected and quiescent; never `motion_uncertain` | `The pump is still moving and cannot discharge. There are two options for ”` |

A round that cannot start becomes slot status `not_started` with the text; three in a row end the schedule (as today).

### A.5 Estimate of one round (for the preview and the interval check)

`delay` value · `pump_run` 1.5 s (2.5 for motors 2 / 9) · `pump_stop` 1 s (2 s) · `pump_dose` `|steps| ÷ sps × 1.1 + 1` (+2 s for 2 / 9) · `pump_dose_weight` `g ÷ (fast flow ÷ 60) × 1.3 + 12` · `clean` YAML total + 2 · `syringe_init` 6 · `syringe_speed` 0.5 · syringe move `mL ÷ speed + 2` · `rpi_status` 3 · `nano_prepare` 6 · `plasma_run` `status.estimate_s` (RPi; 20 when unknown) · `spectrum_capture` `3 × integration + 8` · stop 5.
Hard error at preview/start: interval ≤ total `delay` of the Recipe (`interval N minute ratio Recipe waiting time S Seconds are still short, and it times out every time`). Warning: estimate > interval.

### A.6 Display phases (display only, derived — no plumbing is invented)

| Step | Phase label |
|---|---|
| `pump_run` / `pump_stop` / `pump_dose` / `pump_dose_weight` on a motor with `fluid` in the YAML | the fluid text (`water`, `OUT`, `CuSO₄`) |
| same, motor without `fluid` | `motor N` |
| `clean` | `Cleaning` |
| `syringe_*` | `syringe pump` |
| `rpi_status`, `nano_prepare` | `Prepare` |
| `plasma_run`, `spectrum_capture` | `Measure` |
| `nano_stop`, `power_off` | `Turn off high voltage` |
| `delay` | the phase of a pump that is running; otherwise `Waiting` |

`phases` = distinct labels in first-seen order (the phase strip on Overview). Only the outermost `repeat` counts as a displayed round (`round`, `rounds`), as in the HMI.

### A.7 Example Recipes (files `pc\recipes\*.csv`, LF, UTF-8 without BOM)

`plasma-only.csv` — name “Discharge measurement only”. Published automatically at migration. Needs only the RPi; works with the board unplugged.

```
action,target,value,unit,note
plasma_run,,,,discharge＋capture spectrum (background 1 Zhang, followed by discharge and signal spectrum)
```

(One line on purpose: the RPi repeats every readiness check itself, and each extra RPi step would add seconds between pressing and the discharge.)

`pump-and-plasma.csv` — name “Example: Add liquid＋Measure＋discharge”. **Not** published automatically; an example to edit. It uses only the three motors that `hardware.yaml` names (2 water, 8 OUT, 9 CuSO₄). Amounts and order must be adapted to the real tubing.

```
action,target,value,unit,note
pump_dose_weight,M9,10,g,join in CuSO₄ solution 10 g (Confirm with scale) .Please modify the value according to actual needs
delay,,5,s,and other liquid levels are stable
rpi_status,,,,Confirm that the high voltage is off
plasma_run,,,,discharge＋capture spectrum
pump_run,M8,40,mL/min,discharge (OUT) 
delay,,30,s,Row 30 Second
pump_stop,M8,,,
pump_run,M2,40,mL/min,Water ingress
pump_run,M8,40,mL/min,discharged simultaneously (A group and B group can transfer one piece each)
delay,,20,s,
pump_stop,M2,,,
delay,,20,s,Continue emptying
pump_stop,M8,,,
```

`cleaning.csv` — name “Example: automatic cleaning. Not published automatically. Needs only the board (with the RPi off, the start dialog asks for the extra tick).

```
action,target,value,unit,note
clean,,,,Automatic cleaning: water first, then water in and out at the same time, and finally emptying (the timing is in the firmware)
```

---

## B. PC program structure

### B.1 Modules (all under `pc\`, standard library only, except `pumpboard.py` / `pump_guard.py` which use the vendored packages)

| File | Contains | Notes |
|---|---|---|
| `app.py` | `VERSION = '2.0.0'`, `main()`, argument parsing, `--set-pin`, console-close handler, start of the HTTP server. Re-exports `Application`, `ExclusiveHTTPServer`, `make_handler` (tests do `import app as pc`) | the only place that calls `pumpboard.allow_real_ports()` |
| `application.py` | `Application`: builds and owns every object below; `state()`; one thin method per API route; the PIN table of B.10 | no device logic |
| `httpd.py` | `ExclusiveHTTPServer`, `make_handler(app, csrf)`, router, static-file table, error mapping, response headers | section C |
| `store.py` | `write_json`, `Store`: `config.json`, `settings.json`, `operators.json`, `events.jsonl`, spectra `runs\`, Recipes, round records, `runlog.jsonl`, schedule record | calls `migrate.run()` first |
| `migrate.py` | 1.3.0 → 2.0.0 data migration (D.3) | idempotent, never deletes |
| `pinlock.py` | `PinLock` moved from `app.py`; new `peek()` (no renewal) and `configured` semantics of B.10 | same file format, same parameters |
| `alarms.py` | `Alarms` (raise / clear / ack / list / persist) | B.9 |
| `rpilink.py` | `Link` moved from `app.py` and renamed `RpiLink`; `sequence_fields` and all "sequence" code removed; new `hv_view()`, `wait_job()`, `fetch_result()` | TCP protocol untouched |
| `recipe.py` | parse, expand, preflight, estimate, phases; the action table of A.2 as data | pure functions, no I/O |
| `pumpboard.py` | `PumpBoard`, `CheckedLink`, port listing, runtime YAML, V7 loader | B.3 |
| `pump_guard.py` | guard child process | B.3.10 |
| `devices.py` | `Devices`: ownership, pump/HV mutual exclusion, `all_off()`, `stop_everything()` | B.4 |
| `steps.py` | `StepExecutor.run(step, ctx)`: one function per action of A.2 | uses `PumpBoard`, `RpiLink`, `Devices` |
| `runner.py` | `Runner`: schedule loop + round execution, pause / resume / stop, records | B.5 |
| `manual.py` | `ManualOps`: manual operations of manual operation | B.7 |
| `schedule.py` | time arithmetic — **kept**; only the label tables and `ACTIVE` gain `pausing`, `paused`, `skipped_paused` | B.6 |
| `netcheck.py`, `plasmic_client.py` | unchanged | |
| `vendor\…` | vendored V7, pyserial, PyYAML | section F |
| `recipes\*.csv`, `recipes\index.json` | example Recipes of A.7 | |

### B.2 Threads and locks

| Thread | Created by | Lives | Does |
|---|---|---|---|
| main | `main()` | program | `serve_forever()` |
| request threads (daemon) | `ThreadingHTTPServer` | one request | call `Application` methods; none blocks longer than 8 s except `/api/rpi/tool`, `/api/parameters` (RPi reply times, as today) |
| `rpi-link` (daemon) | `RpiLink` | program | connect / reconnect, `status` every 1 s, manual-plasma screen watchdog, saving finished jobs (as today) |
| `pump-monitor` (daemon) | `PumpBoard.start()` | program | connect / reconnect, V7 event queue, heartbeat `?`, run deadlines, reset detection, page watchdog for manual pump work |
| `serial-reader` (+ `sim`) (daemon) | V7 `Link.start()` | one connection | V7's reader `[FACT]` |
| `runner` (daemon) | `Runner.start()` | one schedule | slots, rounds, steps |
| `manual-op` (daemon) | `ManualOps` | one blocking manual operation | dose / weight dose / clean / syringe move |
| `stop-pump`, `stop-rpi` (daemon) | `Devices.stop_everything()` | one STOP | the two halves of STOP in parallel |
| guard (process) | `PumpBoard` after a real connect | program | B.3.10 |
| RPi simulator (process) | `Application` in demo | program | `plasmic_tcpd.py --sim` (as today) |

| Lock / event | Owner | Guards | Rule |
|---|---|---|---|
| V7 `Link._tx_lock` | V7 | one wire command at a time `[FACT]` | never taken by us directly |
| `PumpBoard.op_lock` (Lock, non-blocking acquire) | PumpBoard | one motion-starting operation at a time | **stops never take it** |
| `PumpBoard.lock` (RLock) | PumpBoard | wrapper fields: link, deadlines, syringe bookkeeping, flags | held for microseconds, never across `send` |
| `Devices.lock` (Lock) | Devices | `owner`, `hv_pending`, `hv_active` | held only for check-and-set, never across I/O |
| `Runner.lock` (RLock) | Runner | schedule record, round record | copies are made for writing to disk and for `state()` |
| `Runner.stop_request`, `pause_request` (Events) | Runner | — | set by HTTP threads, read by the runner thread |
| `RpiLink.request_lock`, `.lock` | RpiLink | as today | STOP uses a fresh connection, so it never waits for `request_lock` |
| `Store.lock`, `Alarms.lock`, `PinLock.lock` | each | their data | leaf locks |

Lock order when two are needed: `Runner.lock` → `Devices.lock` → `PumpBoard.lock` → leaf locks. No device I/O under `Runner.lock` or `Devices.lock`.

### B.3 `pumpboard.py` — the wrapper around the vendored V7 link

#### B.3.1 Loading V7 (once per process)

```python
_runtime_yaml = None      # fixed for the life of the process
def configure_runtime(path):      # called by Application before the first PumpBoard; a second call with another path raises
def _load_v7():                   # under a module lock, first use only
    os.environ['LAB_CONFIG'] = str(_runtime_yaml)          # V7 config.py reads it at import [FACT config.py:16]
    sys.path.append(str(VENDOR / 'v7'));  sys.path.append(str(VENDOR / 'site'))
    from backend import config, registry, transport        # no port is opened by importing [FACT]
    from backend import functions                          # registers the 26 functions (a second import would raise) [FACT registry.py:112]
    from backend.functions import scale
```

| V7 property `[FACT]` | Handling |
|---|---|
| `config.CONFIG_PATH` fixed at import; `get_config()` cached | one runtime YAML path per process; after rewriting the file call `config.get_config(reload=True)` |
| `transport.get_link()` singleton, `simulated` fixed at construction | never called. `transport.Link()` is constructed directly, a new instance for every connection attempt, after the reload |
| `Link._find_port()`: `auto` = first port matching `arduino/mega/ch340/usb-serial/wch`, else the first port | the runtime YAML never contains `auto`: the wrapper has chosen and checked the port before (B.3.3) and asserts `config.get_config()['link']['port'] == chosen` (and that it is not `auto`) before `start()` |
| `functions.motors` / `functions.scale` keep process-wide state (weight-dose lock and session, weight history, calibration status) | exactly one live `PumpBoard` per process (class-level guard; a second `start()` raises). `scale.clear_history()` on every connect |
| `scale.calibration_path()` reads env `SCALE_CALIBRATION_FILE` at each call | set to `<data>\pumpboard\scale_calibration.json` in `PumpBoard.__init__` |
| pyserial import failure is swallowed (`serial = None`) | real mode checks `transport.serial is not None` first: `Program folder is incomplete: missing pc\vendor\site\serial.Please copy the entire folder again.` |

#### B.3.2 Runtime YAML (`<data>\pumpboard\hardware.runtime.yaml`)

Built from the vendored `pc\vendor\v7\config\hardware.yaml` (read with `yaml.safe_load`), written with `yaml.safe_dump(allow_unicode=True, sort_keys=False)` through `write_text` + replace. Only the `link` section is changed in production:

| Key | Real | Demo / tests |
|---|---|---|
| `link.port` | the checked port, e.g. `COM3` | `simulator` (unused) |
| `link.simulate` | `false` | `true` |
| `link.simulation_speedup` | 1 | 1 in demo; tests pass what they need |
| `link.boot_delay_s` | 2.5 (V7 value) | 0 |
| `link.command_timeout_s` | 3.0 (V7 value) | 3.0 |
| `link.long_command_timeout_s` | 6.0 (only `Y,INIT` is still "slow"; the firmware waits at most 2.5 s for it) | 6.0 |

`PumpBoard(..., overrides=None)`: tests may pass extra overrides (short cleaning times, short `settle_s`, short valve delays). Production code never passes any; a test asserts that.

#### B.3.3 Port choice and identity check (real mode)

`settings.pump.port`: `"auto"` (default) · `"COMn"` · `""` (board not used).

| Step | Rule | Text on failure (Maintenance › connection and the top chip's tooltip) |
|---|---|---|
| 1 list | `serial.tools.list_ports.comports()` → `{device, description, usb_id "VVVV:PPPP", is_mega}`; `is_mega` = usb id in `settings.pump.usb_ids` (default `["2341:0042", "2A03:0042"]`). Listing does not open anything | |
| 2 choose | `auto`: exactly one Mega → it. None → stop. More than one → stop | `Pump control board not found. Please confirm Arduino Mega of USB cable is plugged into this computer.` · `Found more than one piece Arduino Mega.Please go to ”Maintenance › Connection” choose which one to use.` |
| | `COMn`: must be in the list **and** `is_mega` | `COM5 Not now Arduino Mega (or removed). Didn’t turn it on.` |
| 3 open | write runtime YAML, reload, `Link()`, `start()` (V7 sleeps 2.5 s, starts the reader, sends `?`) | `COM3 is occupied by another program. please close Arduino IDE , or the old The instructions of V7 Windows (run.bat) .` (when V7's error text contains `PermissionError`, which is how pyserial reports a port that is already open `[INFER]`; any other open error is shown as it is after `cannot be opened COM3: `) |
| 4 identify | up to 3 × `?` (1.5 s each). Pass = a line starting `STATE,` that contains `a=`, `sa=`, `b=`, `sb=`, `valves=`, `w_stream_hz=`, `cleaning=` **and** the banner in `link.state['firmware']` is either absent or starts with `READY,arduino+pump`. Nothing else is sent before this passes | `COM3 There is a device, but not the pump control board (correct response not received). This port has been closed.` |
| 5 fingerprint | `cal_pulses_per_ml` and `max_flow_uL_min` of that `STATE` against the YAML (`round(pulses_per_ml, 2)`, `max_flow_ml_min × 1000`). A mismatch is a priority-3 alarm, not a refusal | `The firmware setting of the pump control board is different from the configuration file on the computer (the firmware may be an old version). The cleaning sequence is subject to firmware.` |

Retry policy (every open resets the device): link errors → 2, 5, 10, 30 s then every 30 s. Identity failure → the port is closed and **not retried** until “Reconnect” is pressed or the port list changes. Banner absent but `STATE` fine = the board did not reset on open; recorded as `banner_seen: false` (step 1 of B.3.4 makes the state safe anyway).

#### B.3.4 After a successful identity check (also after a reconnect and after an unexpected `READY`)

1. `motors.stop_all` (`X`, `W,STREAM,0`, `?`) — whatever was running stops. Verify: both groups `idle`, nothing selected, valves `00`, `cleaning` false.
2. `scale.restore_calibration(link)` (`W,DIV`, `W,OFFSET` when the file exists); then `W,AVG,<average_samples>`, `W,STABLE,<stable_threshold_g>`, `W,STREAM,<stream_hz>`, `?` — the same traffic as V7's `main.py` lifespan `[FACT]`.
3. `link.weight_hooks.append(scale.record)`; our own hook stores the newest sample with `time.monotonic()`.
4. Syringe: `pump.set_syringe` when the saved size is not 5000; `pump.detect` once (an `ERR,pump_not_found…` only sets `syringe.online = false`). No `Y,INIT` by itself, ever.
5. Syringe bookkeeping reset: `initialised = false`, `volume_ul = None`. `motion_uncertain = false`. Alarms `pump_link_lost`, `pump_stop_unconfirmed` cleared. Guard told the port (B.3.10).

#### B.3.5 `CheckedLink` — what V7 functions get as `ctx.link`

It offers what the function modules use `[FACT integration notes §6.3]`: `send(cmd)`, `snapshot()`, `refresh_state()`, `weight_hooks`. Everything delegates to the V7 `Link`; `send` adds:

| Addition | Rule |
|---|---|
| allow-list | only `?`, `X`, `C`, `S,`, `V,`, `P,`, `Y,DETECT`, `Y,INIT`, `Y,SYR`, `Y,VALVE`, `Y,SPEED`, `Y,ASPUL`, `Y,DISPUL`, `Y,STOP`, `Y,ST`, `W,DIV`, `W,OFFSET`, `W,AVG`, `W,STABLE`, `W,STREAM`, `W,TARE`, `W,CAL`, `W,CFG`. Anything else (`W,SCAN`, `W,SNIFF`, `W,SET`, `W,BAUD`, …) is refused before it is written: those block the firmware for up to a minute, during which `X` is not read |
| pump/HV gate | motion commands (`V` with a non-zero speed, `P`, `C`, `Y,INIT`, `Y,VALVE`, `Y,ASPUL`, `Y,DISPUL`) are refused while `Devices.hv_pending or hv_active`: `The high pressure has not been turned off and the pump cannot be started.` |
| own terminators and timeouts | passed explicitly to `Link.send(cmd, terminators, timeout)`: `?` → `STATE,` 1.5 s · `V`/`P`/`C`/`X` → `OK`/`ERR` 3 s · `S,` → `DONE,S`/`ERR` 3 s · `Y,INIT` 5 s, other `Y,` 3 s → `PUMP,`/`FOUND,`/`OK,`/`ERR` · `W,CFG` → `CFG,` · other `W,` → `OK`/`ERR` 3 s |
| reply really arrived | V7 returns collected lines as success when the terminator never came `[FACT transport.py:352]`. If the last line does not start with a terminator → treated as a timeout |
| after any timeout | `resync()`: `W,CFG` with terminator `CFG,` (never unsolicited, answered at once, emulated by the simulator). The firmware answers in order, so when `CFG,` arrives every late reply has been swallowed. Tried twice; failure → link `lost` (B.3.8). A motion command that timed out also sets `motion_uncertain` and triggers `stop_all` |
| syringe status | every `PUMP,…` line is parsed; `error ≠ 0` raises `SyringeFault(n)` — V7 ignores it `[FACT]`. Texts: 2 / 3 `value is out of range` · 7 `The syringe pump has not been initialized yet.` · 9 `Piston overload` · 10 `Valve overload` · other `Syringe pump report error n` |
| firmware errors | `ERR,cleaning_active` → `This action cannot be performed during automatic cleaning.` · `ERR,arg` → `The pump control board does not accept this value.` · `ERR,pump_timeout` / `pump_bad_reply` / `pump_echo_only` → `Syringe pump does not respond` · `ERR,scale_timeout` → `Scale does not respond` · `ERR,no_change` → `The weight has not changed and cannot be corrected.` · `ERR,bad_cmd` → `The pump control board does not recognize this command (different firmware versions?)` |
| write failure / not connected | V7 raises `failed to send:` / `Not connected yet Arduino` and does not mark the link `[FACT]` → the proxy marks it `lost` |
| bookkeeping | time of the last command and of the last `STATE`; which group was sent a motion command |

Exceptions raised by the proxy subclass V7's `LinkError`, so V7 code (`_stop_quietly`, `restore_calibration`) treats them as before.

#### B.3.6 Operations (public methods; all raise `PumpBoardError(code, text)`)

`owner` is `'round'` or `'manual'`. `abort` is a `threading.Event` set by STOP. Blocking methods run on the caller's thread (runner or manual-op).

| Method | Preconditions (else `refused`) | Sequence | Ends when / fails when |
|---|---|---|---|
| `run(motor, flow, direction, limit_s, owner)` | connected; not cleaning; **that motor's group `idle`** (a different selected motor that is idle is fine); flow within YAML limit; `1 ≤ limit_s ≤ max_run_s + 5` | `?` → `motors.run` → check `selected == motor` and state `wait`/`run` → deadline `now + limit_s` (+1 s for motors 2 / 9) | returns at once with `{motor, sps, until}`. Wrong state after the command → `stop_all`, `device` error `motor N did not start according to the instructions.` |
| `stop_motor(motor)` | — (never refused for ownership). During cleaning: `A single pump cannot be stopped during automatic cleaning. To abort, click ”Stop” STOP”.` | `motors.stop` (also cancels a weight dose on that motor `[FACT]`) → poll `?` until the group is `idle` (2 s) → wait for the valve bit (3 s) → clear the deadline | not idle in time → `stop_all` |
| `dose(motor, volume_ml, direction, owner, abort)` | connected; everything quiescent | `motors.dose` → remember `steps`, `sps`, `eta_s` → wait. (`OK` to `P` means the move is queued, so an `idle` reported after that `OK` means "finished") | group `idle` + valve closed → `{steps, sps, eta_s, elapsed_s}`. Timeout `eta × 1.5 + 10`, `abort`, link lost, `READY` seen, `selected` changed → `stop_all` and error. Real mode only: `idle` earlier than `0.8 × steps ÷ sps` → error `Quantification ended earlier than expected and may have been interrupted.` (a real move can only take longer than that `[INFER]`; the simulator's speed-up makes the rule meaningless there) |
| `dose_weight(motor, target_g, owner, abort)` | quiescent; scale sample younger than 1 s; in real mode a calibration was loaded at connect (without one the Mega reports raw counts as grams): `The scale has not been calibrated yet: go to ”Maintenance › Pump and Scale” do a calibration` | a `threading.Timer(420, stop_all)`; `motors.dose_by_weight` (blocks; V7's own limits apply) | returns V7's result (`dispensed_g`, `error_g`, `within_tolerance`, `topups`, `elapsed_s`, `warning`). `FunctionError` → `abort` set ? `stopped` : `device` with V7's Chinese text. **If the link is not connected at that moment the text is replaced** (V7 says "Peristaltic pump stopped" although its stop could not be sent `[FACT]`): `The pump control board is lost and the stop command cannot be sent. The pump may still be spinning.` and `motion_uncertain` is set. Afterwards the group must be `idle`, else `stop_all` |
| `clean(owner, abort)` | quiescent | `motors.clean` → `started` must be true → wait | `cleaning` false, groups `idle`, valves closed. Timeout / `abort` → `stop_all` |
| `syringe_init(owner, abort)` | quiescent (peristaltic pumps stopped: the firmware does not read commands while it waits for the syringe pump) | `pump.init` → poll `Y,ST` → `pump.set_speed(saved speed)` | `ready=1` within 20 s → `initialised = true`, `volume_ul = 0`, `port = None` |
| `syringe_speed(speed_ml_s)` | initialised; not busy | `pump.set_speed` | reply |
| `syringe_move(kind, port, volume_ml, owner, abort)` | initialised; quiescent; volume fits the bookkeeping | `pump.set_valve` (skipped when `port` is the current one) → poll ready → `pump.aspirate` / `pump.dispense` → poll | `ready=1`, `error=0`, 80 % of the expected time passed → bookkeeping updated. Error / timeout / `abort` → `stop_all`, `initialised = false` |
| `scale_tare()` | quiescent; newest sample younger than 1 s and `stable` (the firmware takes one single reading, without a stability check `[FACT]`) | `scale.tare` (`W,TARE`, `W,CFG`, file written by V7) | reply |
| `scale_calibrate(grams)` | the same; 0.1–20000 g | `scale.calibrate` | reply |
| `quiescent(fresh=True)` | — | optional `?`; true when both groups `idle`, not cleaning, all valves closed, no operation, no deadline, syringe not busy | returns `(ok, reasons)` |
| `stop_all(reason)` | — | B.3.9 | |

Never repeated automatically: any motion command. A command that failed is reported, not retried.

#### B.3.7 The monitor thread (every 0.1 s)

1. Not connected and the board is configured → connect attempts with the backoff of B.3.3.
2. Drain the V7 event queue (`link.subscribe()`): a `log` event `rx` whose text starts with `READY` after the connect → **board reset** (B.3.8). `state` events wake waiting operations.
3. Deadlines: a continuous run past its deadline → `motors.stop(motor)`; not idle within 2 s → `stop_all`. Manual run: event `motor N has automatically stopped when the set time is reached.`. Round: the flag `deadline_tripped` makes the round fail (`motor N The planned operation time has been exceeded and has automatically stopped.`).
4. Page watchdog: a manual pump operation is active and `/api/state` has not been asked for 10 s → `ManualOps.cancel()` + `stop_all('page')`; alarm priority 3 `The operation screen exceeds 10 No response for seconds, manually started pump stopped`.
5. Heartbeat: when the last `STATE` is older than 2 s → `?` (1.5 s). Two failures in a row → `resync()` → `lost`.
6. `link.connected` false (V7's reader ended on a read error `[FACT]`) → `lost`.
7. Scale: a sample older than 3 s while the stream should be on → one `scale.restart`; still nothing → alarm priority 3 `Scale does not respond`.

#### B.3.8 Lost link, reset, uncertainty

| Event | Detection | Action |
|---|---|---|
| link lost | reader ended; write failure; heartbeat + resync failed | `link.stop()` (closes the handle); state `lost`; if any motion was active, a motion command was unanswered, or a stop was pending → `motion_uncertain = true` + alarm priority 1 `Pump control board lost connection. The pump may still be spinning. Immediately turn off power to the pump using the physical switch.`; otherwise alarm priority 2 (schedule busy) or 3 `Pump control board connection interrupted`. The running operation fails. Reconnect starts at once (opening the port resets the Mega `[INFER]`, then step B.3.4-1 sends `X`) |
| board reset | `READY` line while connected | the running operation fails (`The pump control board restarted on its own`); B.3.4 is repeated (the Mega is back at divisor 1.0, 5000 µL, stream 10 Hz); alarm priority 2 `The pump control board restarted itself. It could be the power supply or USB is unstable. The syringe pump needs to be reinitialized.` |
| reply out of step | timeout | `resync()` (B.3.5) |
| `motion_uncertain` | set as above | blocks every pump start and every `plasma_run` until a reconnect has passed B.3.4 |

#### B.3.9 `stop_all(reason)` — never waits for `op_lock`

1. Set `abort` of the running operation; clear all deadlines.
2. Connected: `motors.stop_all` (sets V7's weight-dose cancel flag, `X`, `W,STREAM,0`, `?`). `X` waits only for V7's `_tx_lock`, i.e. at most for the command in flight (3 s; 5 s behind `Y,INIT`).
3. If the syringe was busy or initialised: `pump.stop` (`Y,STOP`, awaited — `X` only fires `/1T` without confirmation `[FACT]`).
4. Verify with `?`: groups `idle`, nothing selected, valves `00`, not cleaning → `confirmed`.
5. Syringe bookkeeping: `initialised = false`, `volume_ul = None` (a stroke may have been cut).
6. `scale.restart` (`W,STREAM,<hz>`) so the weight display comes back — `X` switched the stream off `[FACT]`.
7. Not connected: one immediate reconnect attempt (budget 8 s). Success → step 2. Failure → `{sent: false, confirmed: false}` + alarm priority 1 `The pump stop command was not confirmed. Immediately turn off power to the pump using the physical switch. The settings saved in`

Returns `{sent, confirmed, error}`. Not confirmed → the same priority-1 alarm.

#### B.3.10 Close and the guard

`PumpBoard.close()`: `stop_all('exit')` with a 2 s budget → `link.stop()` → tell the guard `bye`. V7's own `Link.stop()` sends nothing to the Mega `[FACT]`.

`pump_guard.py` (real mode only, started after the first successful connect): `python -B pump_guard.py --usb 2341:0042 --log <data>\logs\guard.log`, `stdin` = a pipe, no window.
- The parent writes `port COM3\n` after every connect and `bye\n` at a clean close.
- The guard reads lines. End of input without `bye` = the parent died. Then: wait 0.3 s; check that the port still has a Mega usb id; open it with pyserial (115200; retries for 10 s while the dead parent's handle is released); wait 2.5 s; write `X\n`; read up to 3 s for `OK,all_stopped`; append one line to the log; close; exit.
- It imports only the vendored pyserial. It never runs in demo or in tests (same real-port policy).

#### B.3.11 Simulator selection and test safety

| Layer | Mechanism |
|---|---|
| mode | `Application(data_dir, demo=False, pump_mode='off')`: `main()` passes `'real'`; `--demo` passes `'sim'`; tests pass `'sim'` or leave `'off'`. In `'off'` the board is absent for every rule (state `mode: "off"`) |
| policy | `pumpboard._real_ports_allowed = False` at import. `allow_real_ports()` sets it; only `app.main()` calls it, only without `--demo`. Building a non-simulated link without it raises `RealPortForbidden` **before** pyserial is touched |
| tripwire (tests) | `tests\pump_testing.py`: `install_tripwire()` replaces `backend.transport.serial` by an object whose `Serial(...)` records the call and raises `AssertionError('a test tried to open a real serial port')`, and replaces the port lister by a simulated list. `tearDownModule` asserts zero recorded calls |
| static check (tests) | `tests\test_port_safety.py`: no file under `tests\` contains `allow_real_ports(`; no file under `pc\` except `app.py` calls it; `pump_guard.py` and `pumpboard.py` are the only files that import `serial` |
| fault injection (tests) | `pump_testing.FaultPlan` patches methods of `backend.simulator.SimulatedMega` (drop a reply, answer `ERR,…`, answer `PUMP,…,error=7`, emit `READY…`, make `readline` raise = cable pulled). Patches live in the tests; vendored files are not edited |

#### B.3.12 V7 quirks and what the wrapper does about each

| Quirk `[FACT]` unless marked | Wrapper |
|---|---|
| `motors.dose`, `motors.clean`, syringe moves return at once | completion waits of B.3.6 |
| no time limit for `V` anywhere | deadlines, page watchdog, guard |
| a second motor in a group silently stops the first | `run`/`dose` refuse unless that group is `idle` |
| `motors.stop` for a non-selected motor answers `OK` and does nothing | `stop_motor` verifies `idle` |
| nothing sent on exit; after a lost link every stop fails silently | `close()`, `stop_all` reconnect, `motion_uncertain`, guard |
| `X` switches the weight stream off | `scale.restart` after every `stop_all` |
| no reconnect | monitor thread |
| `auto` port heuristics | not used |
| reply mis-attribution after a timeout | `resync()` |
| `PUMP,…,error=n` ignored; `pump.online` stale | parsed by the proxy; `Y,ST` polling |
| nobody tracks the plunger | host bookkeeping (`volume_ul`), invalidated by every stop / reset |
| a tare during a weight dose is not blocked | `scale_tare` needs `quiescent` |
| weight dose ignores other motors' state | `dose_weight` needs `quiescent` |
| state dicts shared with the reader thread | `view()` makes a deep copy |
| after reset the divisor is 1.0 (grams = raw counts) | reset detection + B.3.4 before any further weight dose |
| `[INFER]` a `P` move may cap later `V` speeds of that group at the dose speed | warning W1; item in J |
| `[INFER]` every `?` pauses stepping about 10 ms | heartbeat only when no `STATE` arrived for 2 s; waits poll at 1 s |

### B.4 `devices.py` — ownership, interlock, everything-off

**Ownership.** `owner` = `None` or `{kind: 'round' | 'manual' | 'tool', id, label, since}`.

| Caller | Acquires | Refused when |
|---|---|---|
| Runner, start of a round | `round` | owner is `manual` or `tool` → slot `not_started` (`Manual operation is not over yet`) |
| ManualOps | `manual` | owner is `round` / `tool`; schedule status `waiting`, `running`, `pausing`, `stopping`; a round is paused. Allowed while the schedule is `paused` **between** rounds (as the HMI) |
| maintenance tools (manual plasma, spectrum check, `prepare`, `clear_fault`, parameters, scale calibration, pump settings, port change, reconnect) | `tool` | same as manual |
| any stop | nothing | never refused |

A round keeps ownership while it is paused. Ownership is released in `finally`.

**High-voltage view** (`RpiLink.hv_view()` → `{known, idle, reasons[]}`), from the newest `status`:

| Condition | Result |
|---|---|
| not connected, or status older than 10 s | `known: false` (`RPi Not connected` / `RPi status exceeds 10 seconds`) |
| a job with `high_voltage` is running | `idle: false` (`is discharging`) |
| `power.valid` or `nano.valid` not true | `known: false` (`high voltage power supply/Nano is invalid Press the password of`) |
| `power.run_stop_setting != 0` | `idle: false` (`high voltage power supply is ON`) |
| `power.voltage_v != 0` or `current_a != 0` | `idle: false` (`High voltage output has not returned to zero yet`) |
| `nano.state` not `IDLE` / `DONE` / `FAULT`, or `output_command_high` / `output_pin_sample` not false | `idle: false` (`Nano is still outputting or is ready`) |
| otherwise | `known: true, idle: true` |

**Mutual exclusion — pumps/syringe and high voltage never together** (runtime half; the static half is preflight P5/P8):

| Direction | Where | Rule |
|---|---|---|
| before any pump / syringe motion | `Devices.permit_motion(ack)` called by `ManualOps` and by `StepExecutor` for every motion step; **and** the `CheckedLink` gate for every wire command | refused when `hv_pending or hv_active`; when the RPi is connected, `hv_view()` must be known and idle; when it is not connected, only with the acknowledgement given in the start dialog (`ack`) |
| before `plasma_run` (Recipe step or manual) | `Devices.hv_session()` (context manager) | under `Devices.lock`: set `hv_pending`. Then: board connected → `PumpBoard.quiescent(fresh=True)` must hold (valves are given 3 s to close); board not connected → allowed only when it is not in use and not `motion_uncertain`. Fail → clear `hv_pending`, raise `The pump is still moving and cannot discharge. There are two options for ”`. Pass → `hv_active = true` |
| after `plasma_run` | `hv_session().__exit__` | `hv_active` stays true until `hv_view()` is known and idle (polled up to 30 s). Not reached → stays true + alarm priority 1 `The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately.` |

Setting `hv_pending` before the pump check closes the race with a manual start arriving at the same moment: from then on the gate refuses every new motion command.

**`all_off(need_pump, need_rpi)`** (end of a failed or stopped round) and **`stop_everything(actor)`** (the STOP button):

```
stop_everything(actor):
  1. runner.request_stop()      schedule -> 'stopping', no further slot; round stop flag     (instant)
  2. manual.cancel()            abort flag of the manual operation                           (instant)
  3. thread stop-pump: pumpboard.stop_all('stop')          (skipped only when the board is 'off' / never connected and not uncertain -> "none")
     thread stop-rpi:  rpilink.stop()  (fresh TCP connection, no token)   (skipped only when no RPi address is configured -> "none")
  4. join both for 8 s; a half that has not answered is reported "pending" and reports through an alarm later
  5. event “Stop pressed: pump...; high pressure …” with the actor; alarms for every unconfirmed half
```

| Half | Result values | Alarm when not confirmed |
|---|---|---|
| pump | `confirmed` · `unconfirmed` · `pending` · `none` | priority 1 `pump_stop_unconfirmed` |
| RPi | `confirmed` (idle: stop tool ran, OFF read back) · `cancel_sent` (a job was running; the RPi reads back and reports `post_stop`) · `unreachable` · `unconfirmed` · `pending` · `none` | priority 1 `rpi_stop_unconfirmed` when a discharge was running or the HV state is not known idle (`The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm.`); otherwise priority 3 |

`all_off()` is the same two halves; the RPi half is sent only when the Recipe has an RPi step or `hv_view()` is not known-idle.

### B.5 `runner.py` — schedule and rounds

**Start** (`POST /api/schedule/start`), checks in this order, first failure answers 409/400 with all reasons of that stage:

1. an operator is selected · 2. no schedule is active · 3. Recipe exists, its SHA-256 matches, `recipe.validate()` passes with the current settings · 4. ticks: `confirm.high_voltage` when `uses_plasma`; `confirm.liquid` when `uses_pump`; `confirm.hv_off_checked` when `uses_pump` and the RPi is not connected · 5. the start checks of A.4 · 6. `schedule.validate(raw)` and the interval rule of A.5.

Then the record is created, written, and the `runner` thread starts.

**Loop** (structure of today's `Schedule._loop`, same slot rules, same `START_FAILURES = 3`, `KEEP_SLOTS = 2000`):

```
for each slot index:
    r = wait_until(due)             # stop -> schedule 'stopped'
                                    # paused and now > due + 5 s -> slot 'skipped_paused', next index
    try:    status, note = run_round(index)
    except SlotNotStarted as e:     slot 'not_started'; 3 in a row -> schedule 'failed'
    done    -> next slot (slots that passed meanwhile: 'skipped_overrun', never run late)
    stopped -> schedule 'stopped'
    failed  -> schedule 'failed'    ("after a round that did not end cleanly nobody knows the state of the equipment")
```

**One round** (`run_round`):

1. `devices.acquire('round')`; start checks of A.4 → `SlotNotStarted(text)` (nothing has moved yet).
2. Expand the stored CSV; create the round record (`status: running`), write it; slot `running`.
3. For each step: `checkpoint()` → mark `running` → `StepExecutor.run()` → mark `done` with its result. The record is written at each device step and at most once a second for `delay` steps.
4. Exceptions: `Stopped` → `stopped`; anything else → `failed` with the text (`No. N step (pump_dose M9) failed:…`).
5. `finally`:
   - `completed`: when `uses_pump`, `pumpboard.quiescent(fresh=True)` must hold; if not → `stop_all`, status `failed` (`The pumps did not stop at the end of one round, they all stopped. The program on`).
   - `failed` / `stopped`: `devices.all_off()`; its report goes into the record (`stop_report`); an unconfirmed half forces `failed` and raises the alarm.
   - write the final record, append one line to `runlog.jsonl`, release ownership, event.

**`checkpoint()`** — before every step and every 0.1 s inside `delay`:

| Flag | Effect |
|---|---|
| stop | raise `Stopped` |
| `deadline_tripped`, board `lost`, board reset (only when the Recipe uses the board) | raise `StepFailed` |
| pause | `hold()` |

**`hold()` — PAUSE**

1. Every continuous pump of this round: `used += now − started`; `pumpboard.stop_motor()`.
2. Status `paused` (round and schedule); `paused_at`; event `Process has been suspended` (+ `; Motor stopped 2, 8,will restart when continuing`).
3. Wait for resume or stop. Ownership is kept: manual starts stay locked.
4. Resume: `devices.permit_motion()` again; each paused pump restarts with the same flow and direction and the deadline `planned − used + 5 s`; a `delay` continues with its remaining time; status `running`; event `Process continues`.

A pause requested during a blocking step shows `pausing` (`Pausing: Pause after completing this step`) until that step ends. Blocking steps that are **not** interrupted by PAUSE: `plasma_run`, `spectrum_capture`, `pump_dose`, `pump_dose_weight`, `clean`, every syringe step, `rpi_status`, `nano_prepare`, `nano_stop`.

**STOP inside each step type**

| Step | How STOP reaches it | Worst-case delay |
|---|---|---|
| `delay` | flag, 0.1 s | 0.1 s |
| `pump_run` / `pump_stop` | `X` from `stop_everything` | command in flight ≤ 3 s |
| `pump_dose`, `clean` | `X` (the wait sees `abort`) | ≤ 3 s; the firmware reads `X` when its loop is free |
| `pump_dose_weight` | V7's cancel flag (set by `motors.stop_all`) + `X` | ≤ 3 s for the pump; the V7 call may return up to 5 s later (its settle sleep after a top-up is not cancellable `[FACT motors.py:468]`) — the pump is already stopped then |
| syringe step | `X` (fires `/1T`) + `Y,STOP` (confirmed) | ≤ 3 s (5 s behind `Y,INIT`) |
| `plasma_run`, `spectrum_capture` | RPi `stop` on a fresh connection = one SIGTERM to the tool, which runs its own OFF sequence; the step waits for the job to end (≤ 60 s) | the RPi's own shutdown time |
| `rpi_status`, `nano_prepare`, `nano_stop` | not interruptible; the RPi `stop` queues behind them on the RPi | their reply time |

A stopped round cannot be continued; the next start begins at step 1. A discharge is never repeated automatically.

**Restart.** At program start `Runner` loads the schedule record and the newest round record:
- schedule in an active state → `interrupted` (`The program is restarted, the schedule has been interrupted and will not automatically resume. Please restart the schedule after confirming the device.`); its `running` slot → `failed`.
- round `running` / `pausing` / `paused` / `stopping` → `interrupted`, note `The program is closed or the computer is restarted during execution; please go to the site to confirm the device status.`; one `runlog` line.
- if either was found: alarm priority 2 `sched_interrupted`; one RPi `stop` when that round's Recipe had RPi steps and the RPi is reachable; the board gets its `X` at connect anyway (B.3.4).

### B.6 Schedule

`pc\schedule.py` stays the time arithmetic (`validate`, `slot_time`, `last_index`, `planned_total`, `next_due_index`). Changes: `ACTIVE = ('waiting', 'running', 'pausing', 'paused', 'stopping')`; labels `pausing: Paused`, `paused: Suspended`, `skipped_paused: skip (scheduling pause)`. A new constant `BUSY = ('waiting', 'running', 'pausing', 'stopping')` (the schedule owns the equipment; `paused` between rounds does not).

A single measurement = a schedule with `end: {mode: 'count', count: 1}`. There is no other way to run a Recipe.

Schedule states: `waiting Waiting for the next time` · `running under measurement` · `pausing Paused` · `paused Suspended` · `stopping Stopping` · `completed Completed` · `stopped Stopped` · `failed stopped due to failure` · `interrupted Interrupted`.
Slot states: `running under measurement` · `done Complete` · `failed failed` · `stopped Stopped` · `not_started not started` · `skipped_overrun Skip (last timeout)` · `skipped_paused skip (scheduling pause)`.
Round states: `running is executing` · `pausing Paused` · `paused Suspended` · `stopping Stopping` · `completed Complete` · `failed failed` · `stopped Stopped` · `interrupted Interrupted`.
Step states: `pending` · `running` · `done` · `failed` · `stopped` · `skipped` (not reached).

### B.7 `manual.py` — manual operation

One manual operation at a time (`devices.acquire('manual')`). Every start passes `devices.permit_motion()`.

| Operation | Runs | Ends |
|---|---|---|
| `pump_run` | inline in the request (`PumpBoard.run`), then returns | by `pump_stop`, its deadline (1 … `max_run_s` s, chosen on the page), the page watchdog, STOP. Ownership is held while any manual run is active. A second `pump_run` in the **other** group is allowed |
| `pump_stop` | inline | — |
| `pump_dose`, `pump_dose_weight`, `clean`, `syringe_init`, `syringe_move` | on the `manual-op` thread; the request answers `{accepted, op_id}` at once; progress is in `state.pump.op` | completion, error (alarm-free; shown as the page message and an event), STOP, page watchdog |

The scale's zero (tare) is **not** a manual operation: a weight dose takes its own zero `[FACT motors.py:375-377]`, so tare is only needed for calibration and lives on Maintenance › Pump and Scale (one place, behind the PIN).

Manual operations record the current operator name (may be empty) in the event log. They do not need an operator.

### B.8 Operators

`operators.json` `{operators: [names]}`: at most 50, each 1–20 characters, no control characters, none of `< > " \` (the HMI rule). Edited as a whole list (PIN). The current operator is in memory, `''` after a restart, shared by all tabs, required to start a schedule, written into every schedule, round and event (`actor`). Removing the current operator from the list clears the selection.

### B.9 Alarms and events

`Alarms.raise_(key, priority, area, text)` · `clear(key)` · `ack(ids | all)`. Persisted in `alarms.json`. An alarm that clears while unacknowledged stays listed as `Restored` until acknowledged; acknowledged and cleared → removed. Raise, clear and ack each write an event.

| key | Pri | area | Raised when | Cleared when |
|---|---|---|---|---|
| `pump_stop_unconfirmed` | 1 | pump | B.3.9 not confirmed; link lost with `motion_uncertain` | a connect passed B.3.4 |
| `rpi_stop_unconfirmed` | 1 | high pressure | B.4; a job's `post_stop.ok` false | `hv_view()` known and idle |
| `pump_link_lost` | 2 if a schedule is busy, else 3 | pump | link `lost` after it had been connected | connected |
| `rpi_link_lost` | 2 if a busy schedule needs the RPi, else 3 | RPi | disconnected after it had been connected | connected |
| `pump_reset` | 2 | pump | unexpected `READY` | acknowledged |
| `pump_wrong_device` | 2 | pump | identity check failed | next successful connect |
| `syringe_fault` | 2 | syringe pump | `PUMP,…,error=n` | next successful `syringe_init` |
| `nano_fault` | 2 | high pressure | `nano.fault != 'NONE'` | fault `NONE` |
| `round_failed:<id>` | 2 | Process | a round ended `failed` | acknowledged |
| `sched_failed:<id>`, `sched_interrupted:<id>` | 2 | Schedule | schedule ended so | acknowledged |
| `sched_skipped:<id>` | 3 | Schedule | first `skipped_overrun` of a schedule | acknowledged |
| `slot_not_started:<id>:<n>` | 3 | Schedule | slot `not_started` | acknowledged |
| `weight_tolerance:<round>` | 3 | scales | `dose_by_weight` returned `within_tolerance: false` (the round continues, as V7 returns normally) | acknowledged |
| `scale_offline` | 3 | scales | B.3.7-7 | samples arrive |
| `firmware_mismatch` | 3 | pump | B.3.3-5 | acknowledged |
| `page_lost` | 3 | picture | page watchdog fired | acknowledged |
| `ui_stale` (browser only) | 1 | picture | no fresh `/api/state` for 5 s: `The program did not respond: the data on the screen has expired and the actual status of the device is unknown.` | next good poll |

Events: `events.jsonl`, one `{time, level, text, actor}` per line (`level` `info`/`warn`/`error`; `actor` = operator name, `Maintenance`, `system` or `''`). Old 1.3.0 lines without `actor` stay readable.

### B.10 PIN lock

Same `PinLock` and `pin.json` as today (PBKDF2-HMAC-SHA256, 600 000 iterations, 32-byte salt, 6–8 digits, 5 wrong → 30 s doubling to 900 s, session 600 s sliding, header `X-Unlock`, set only by `Set-PIN.cmd`). Changes:
- **No PIN file → Maintenance is closed** (today: everything open). The Maintenance page then shows how to set one. `Set-PIN.cmd` takes effect without a restart (`configured` is checked per request).
- `peek(session)` reports without renewing; `/api/state` uses it. Only PIN-gated requests renew the session, so an unattended Maintenance page really closes after 10 minutes.
- Demo: the PIN file of the real data folder is used when it exists; otherwise the fixed practice PIN `000000` (shown on the login panel: `Practice mode maintenance PIN yes 000000`).

| Action | PIN |
|---|---|
| Look at Overview / Schedule measurement / manual operation / execution record / alarm; open a round's detail; see and download a round's spectrum | no |
| Choose the operator | no |
| Schedule preview / start (incl. high voltage, with the tick boxes) / PAUSE / RESUME | no |
| **STOP** | **never** |
| Acknowledge alarms | no |
| Manual: pump ON / OFF, dose, weight dose, auto-clean; syringe initialise / aspirate / dispense | no |
| Enter Maintenance (any tab) | yes |
| Recipe: import + check, publish, download CSV | yes |
| Recipe: delete | yes, and the PIN is typed again |
| connection: RPi address / port / token; automatic search RPi; connection diagnosis; pump-board port; Reconnect | yes |
| plasma parameters: save the seven parameters; synchronized to Nano; Clear Nano fault; ⚠ manual start plasma | yes |
| Pump and Scale: Continuous operation upper limit, Single quantitative upper limit, Syringe capacity, Syringe pump speed; scale zero (Return to zero) and calibration with a weight | yes |
| spectrum: the list of all stored spectra, frame-by-frame view; capture spectrum (no discharge) | yes |
| operator: edit the list | yes |

### B.11 Start and exit

Start order in `Application.__init__`: `Store` (migration) → `Alarms`, `PinLock` → `RpiLink` (starts connecting) → `pumpboard.configure_runtime()`, `PumpBoard(...)` (not yet started) → `Devices` (gives the board its HV gate) → `PumpBoard.start()` (connects in the background; the page is usable meanwhile) → `Runner` (restart handling) → `ManualOps`.

`Application.close(fast=False)`: `runner.request_stop()` → `manual.cancel()` → in parallel `pumpboard.close()` (sends `X`) and, only when a job runs or the HV is not known idle, `rpilink.stop()` → join the runner (5 s) → close the links → end the simulator.

| Way the program ends | Covered by |
|---|---|
| Ctrl+C, normal return | `finally: app.close()` |
| the console window is closed, log-off, shutdown | `ctypes.windll.kernel32.SetConsoleCtrlHandler`: on `CTRL_CLOSE_EVENT`, `CTRL_LOGOFF_EVENT`, `CTRL_SHUTDOWN_EVENT`, `CTRL_BREAK_EVENT` run `app.close(fast=True)` (pump `X` 1.5 s, RPi stop 3 s, in parallel; Windows allows about 5 s) |
| killed (Task Manager), crash | pump side: the guard. Plasma side: the RPi's own watchdog (`owner_disconnected`) |
| PC power loss | nothing can be sent. A Mega powered only from USB loses power with the PC `[INFER]`; a discharge is ended by the RPi watchdog |

### B.12 Failure cases asked for

| Case | What happens |
|---|---|
| Board disappears during `pump_dose` | reader ends → `lost`, `motion_uncertain`, priority-1 alarm, immediate reconnect attempts (a successful open resets the Mega, then `X`). The step fails → round `failed` → RPi half of `all_off` when needed → schedule `failed`. If the Mega kept its power, a finite dose ends by itself; a continuous run would not — hence the alarm text |
| Board disappears during `pump_dose_weight` | V7 stops getting samples, raises after 4 s, its final `V,m,0` cannot be sent; the wrapper replaces V7's "pump stopped" text (B.3.6), same path as above |
| Board resets by itself mid-step | `READY` seen → step fails, B.3.4 re-applied (`X`, calibration), alarm priority 2; the round fails |
| RPi link drops during `plasma_run` | RPi: `owner_disconnected` → SIGTERM → OFF sequence, by itself. PC: `wait_job` tolerates 30 s without status; on reconnect it reads the job (`cancelled`); round `failed`; pumps get `all_off`; schedule `failed`; `post_stop` not ok → priority-1 alarm |
| RPi link drops while a Recipe is in its pump steps | pumps already running keep their deadlines; the next motion step is refused by `permit_motion` (the high-voltage state is no longer known and nobody ticked the box for this case) → round `failed`, `all_off` |
| Page closes during a manual continuous run | page watchdog after 10 s → `stop_all`; in any case the run's own deadline |
| Page closes during a schedule | nothing: rounds are unattended by design; every run has its deadline, every step its limit |
| Two browser tabs | both show the same server state (operator, alarms, schedule). Commands are serialised by ownership (`Another operation is in progress`). STOP from either tab works. The PIN session is per tab. Either tab keeps the page watchdog fed |
| Program restarts mid-round | B.5 "Restart": `interrupted`, never resumed; `X` at connect; guard already sent `X` if the old process was killed |
| Recipe leaves a pump running | refused by preflight P7. If it still happened: end-of-round quiescence check → `stop_all` → round `failed` |
| STOP while the board link is down | reconnect attempt inside `stop_all`; not confirmed → priority-1 alarm telling the operator to cut the power |
| A manual start and a round's `plasma_run` at the same moment | `hv_pending` is set first; the `CheckedLink` gate refuses the motion command; or the pump check of `hv_session` fails and the round fails before any high voltage |
| Operator presses RESUME while a manual pump still runs | refused: `The manually started pump is still running, please stop it first.` |

---

## C. HTTP API

### C.1 Rules for every request (unchanged from 1.3.0 unless marked NEW)

| Item | Rule |
|---|---|
| Bind | `127.0.0.1:8766`, exclusive port (`SO_EXCLUSIVEADDRUSE`), daemon request threads |
| Host | must be `127.0.0.1:<port>` or `localhost:<port>`; else 403 `{"error": "Invalid host"}` |
| POST | `Origin` absent or same origin; header `X-Token` = the page token of `/api/bootstrap` (new at every program start, `hmac.compare_digest`); else 403 `{"error": "Invalid local request"}` (the page re-reads bootstrap once and retries) |
| Body | one JSON object; `Content-Length` ≤ 100 000 bytes; NEW: ≤ 300 000 for `/api/recipes/validate` and `/api/recipes/publish` |
| PIN | header `X-Unlock: <session>`; a gated route without a valid session → 403 `{"error": "This operation is only for maintenance personnel (maintenance PIN) ", "code": "locked"}` |
| Reply | JSON, UTF-8, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, the CSP of C.2 |
| NEW errors | `{"error": "<text for the screen>", "code": "<code>", "reasons": [{code, text, fix}]?}` |

| HTTP | `code` | Meaning | Raised from |
|---|---|---|---|
| 400 | `bad_request` | wrong or missing field, form value out of range, invalid Recipe on publish | `ValueError` |
| 403 | `locked` | PIN session needed | `PermissionError` |
| 404 | `not_found` | unknown route, id, file | |
| 409 | `busy` | another owner holds the equipment | `devices.Busy` |
| 409 | `blocked` | interlock or readiness; `reasons` lists why | `devices.Blocked` |
| 409 | `refused` / the RPi's own code | the device said no (`ERR,…`, `PUMP error`, RPi `{"ok": false}`) | `PumpBoardError('refused')`, `PlasmicError` |
| 409 | `pin` | wrong PIN, lockout, PIN not set | `RuntimeError` from `PinLock` |
| 503 | `pump_link`, `rpi_link` | link down or the device did not answer in time | `PumpBoardError('link' / 'timeout')`, connection errors |
| 500 | `internal` | anything else; the text is generic, the detail goes to `events.jsonl` | |

### C.2 Static files under the CSP

Header on every reply: `Content-Security-Policy: default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'` (`/guide` only: `style-src 'self' 'unsafe-inline'`). Fonts fall under `default-src 'self'`.

- At start `httpd` builds a table from the files under `pc\web\` whose extension is in `{.html .css .js .ttf .txt .svg .png .ico}`: URL path (`/` + relative path with `/`) → (file, content type). `/` → `index.html`. A request is served only on an exact table hit; nothing is resolved from the request path, so `..`, `%2e`, back-slashes or absolute paths cannot reach a file.
- Types: `.html text/html`, `.css text/css`, `.js text/javascript` (needed for ES modules), `.ttf font/ttf`, `.txt text/plain`, `.svg image/svg+xml`, `.png image/png`.
- No inline `<script>`, no `style="…"` in markup, no `on…=` attributes, no `eval`. Styles that change at run time are set through the CSSOM (`el.style.setProperty`), which the policy allows.
- Example Recipes are served by `GET /api/recipes/examples/<file>.csv` from `pc\recipes\` through its own fixed table (`recipes\index.json`).
- Ids in API paths are matched against a pattern before any file name is built: Recipes and rounds `[0-9a-f]{32}`, spectra `(?:HMI|CAP)-[0-9a-f]{32}` (the 1.3.0 `RUN_ID` rule).

### C.3 GET routes

| Path | PIN | Reply |
|---|---|---|
| `/`, `/css/…`, `/js/…`, `/fonts/…` | no | static file |
| `/guide` | no | `detailed-guide.html` |
| `/api/bootstrap` | no | `{csrf, version, demo, boot_id, pin_set, practice_pin: "000000" \| null, ui_watchdog_s: 10, hardware: {groups: [{id, label, motors: [{id, label, fluid, valve}]}], max_flow_ml_min, default_flow_ml_min, dose_flow_ml_min, cleaning: {water_motor, out_motor, flow_ml_min, water_lead_s, overlap_s, out_tail_s}, valves: {open_before_ms, close_after_ms}, syringe: {options_ul, valve_positions}, scale: {decimals, calibration_presets_g, default_calibration_g}, gravimetric: {tolerance_percent, timeout_s}}, recipe_limits: {rows, steps, nesting, repeat, delay_total_s}, config: {rpi_host, rpi_port, token_set}}` — the hardware block is the vendored YAML, so labels are never copied into JavaScript |
| `/api/state` | no | C.6. Feeds the page watchdog. Reads `X-Unlock` without renewing it |
| `/api/operators` | no | `{operators: [..], current}` |
| `/api/recipes` | no | `{recipes: [{id, name, created_at, sha12, step_count, rounds, estimate_seconds, uses_pump, uses_rpi, uses_plasma, valid, error}]}` (validated against the current settings) |
| `/api/recipes/<id>` | no | the row above + `steps[]` (expanded: `{line, action, target, value, unit, note, round, rounds, phase, text}`; `text` = the Chinese sentence shown on Overview) + `phases[]`, `warnings[]`, `volumes_ml{}` |
| `/api/recipes/<id>.csv` | yes | the published CSV (`Content-Disposition: attachment`) |
| `/api/recipes/examples` | yes | `{examples: [{file, name, note}]}` |
| `/api/recipes/examples/<file>.csv` | yes | the example CSV |
| `/api/runlog?before=<iso>&limit=<1–200>` | no | `{rows: [...], more}` newest first (D.2) |
| `/api/rounds/<id>` | no | the round record (D.2) |
| `/api/spectra?limit=<1–200>` | yes | `{runs: [meta…]}` newest first |
| `/api/spectra/<job>` | no | the stored spectrum set (`axis`, `background`, `signal`, `difference`, `frames`, …) |
| `/api/spectra/<job>.csv` | no | CSV download (same columns as 1.3.0) |
| `/api/events?before=<iso>&limit=<1–200>` | no | `{events: [{time, level, text, actor}], more}` |
| `/api/pump/ports` | yes | `{setting, current, ports: [{device, description, usb_id, is_mega, in_use}]}`; `[]` in demo |

### C.4 POST routes

"Busy rule" = refused with 409 `busy` while a schedule is `waiting` / `running` / `pausing` / `stopping`, while a round is paused, or while a manual operation runs (`Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out` / `Another operation is in progress`).

| Path | PIN | Body | Reply (200) | Refusals |
|---|---|---|---|---|
| `/api/unlock` | — | `{pin}` | `{session, expires_in: 600}` | 409 `pin`: `Maintenance PIN mistake` · `Too many attempts, please N Try again in seconds` · `has not been set for maintenance PIN` |
| `/api/lock` | — | `{}` | `{locked: true}` | |
| `/api/operator` | no | `{name}` | `{operator}` | 400 `operator is not in the list` |
| `/api/operators` | yes | `{operators: [..]}` | `{operators}` | 400 name rules, `most 50 people` |
| `/api/stop` | **never** | `{}` | `{accepted: true, schedule_ended, round_stopping, manual_cancelled, pump: {result, error}, rpi: {result, error}}` (B.4) | none — always 200 |
| `/api/pause` | no | `{}` | `{accepted: true, status: "pausing" \| "paused"}` | 409 `There is currently no schedule that can be paused` |
| `/api/resume` | no | `{}` | `{accepted: true, status}` | 409 `Schedule is not in pause state` · `blocked` with reasons (`The manually started pump is still running, please stop it first.`, HV not idle, board lost) |
| `/api/schedule/preview` | no | `{recipe_id, schedule: {start: {mode: "now"} \| {mode: "at", time}, interval_minutes, end: {mode: "count", count} \| {mode: "until", time} \| {mode: "continuous"}}}` | `{spec, planned_total, last_slot_at, estimate_seconds, recipe: {id, name, step_count, rounds, uses_pump, uses_rpi, uses_plasma, volumes_ml}, voltage_v, warnings[], blockers: [{code, text, fix}], needs: {high_voltage, liquid, hv_off_checked}}` | 400 form errors (texts of `schedule.validate`) · 404 Recipe |
| `/api/schedule/start` | no | the same + `confirm: {high_voltage, liquid, hv_off_checked}` | `{accepted: true, schedule}` | 400 `Please select the operator first` · `You must first check each item in the confirmation window` · 409 `Already has a schedule in progress` · `blocked` with the A.4 reasons |
| `/api/alarms/ack` | no | `{ids: [..]}` or `{all: true}` | `{acked: n}` | |
| `/api/manual` | no | C.5 | C.5 | `busy`, `blocked`, `refused`, `pump_link` |
| `/api/recipes/validate` | yes | `{csv}` | `{valid, error, warnings[], step_count, rounds, delay_seconds, estimate_seconds, phases[], uses_*, volumes_ml, steps[]}` — 200 also when `valid` is false | 400 only for a body that is not text |
| `/api/recipes/publish` | yes | `{name, csv}` | `{id, name, sha256}` | 400 `Recipe The name must be 1–60 characters` · the preflight error · `already has the same name Recipe` |
| `/api/recipes/delete` | yes + `pin` | `{id, pin}` | `{deleted: true}` (file moved to `recipes\deleted\`) | 409 `pin` · `this Recipe is being used in scheduling` |
| `/api/rpi/config` | yes | `{rpi_host, rpi_port, token, clear_token?}` | `{saved: true}`; reconnects | 400 in demo `simulation mode is fixedly connected to the local simulator` · field errors · busy rule |
| `/api/rpi/find` | yes | `{}` | as today (`{found[], host, port, addresses}`) | |
| `/api/rpi/diagnose` | yes | `{}` | `{steps: [{key, title, ok, detail, fix}]}` | |
| `/api/rpi/tool` | yes | `{cmd: "prepare" \| "clear_fault" \| "spectrum" \| "plasma_run", confirm_high_voltage?}` | the RPi reply (`plasma_run`: `{job, voltage_v, estimate_s}`; attended: tied to the page watchdog) | busy rule · `plasma_run` without `confirm_high_voltage: true` → 400 · `blocked` (`The pump is still moving and cannot discharge. There are two options for ”`, RPi readiness) · RPi codes |
| `/api/parameters` | yes | `{parameters: {Ontime, Offtime, Cycle, Loopnum, Integration_Time, Spectra_Interval, Voltage}}` | `{saved, parameters, synced, sync_error?}` (`set_parameters` then `prepare`, as today) | busy rule · RPi codes |
| `/api/pump/config` | yes | `{port: "auto" \| "COMn" \| ""}` | `{saved: true}`; reconnect starts | busy rule · 400 `COM5 no Arduino Mega` · demo |
| `/api/pump/reconnect` | yes | `{}` | `{accepted: true}` | busy rule |
| `/api/pump/settings` | yes | `{max_run_s 1–600, max_dose_ml 0.01–1000, syringe_ul 5000 \| 1250, syringe_speed_ml_s}` (all four) | `{saved: true, settings}`; a new syringe size un-initialises the syringe | busy rule · range errors |
| `/api/scale/tare` | yes | `{}` | `{calibration: {divisor, offset, reference_grams, saved_at}}` | busy rule · `blocked` (pumps moving) · `refused` (`Scale does not respond`) |
| `/api/scale/calibrate` | yes | `{grams 0.1–20000}` | `{calibration: {divisor, offset, reference_grams, saved_at}}` | busy rule · `blocked` (pumps moving) · `refused` (`The weight has not changed and cannot be corrected.`) |

Removed from 1.3.0: `/api/command`, `/api/raw`, `/api/config`, `/api/find`, `/api/diagnose`, `/api/runs…` (replaced as above).

### C.5 `POST /api/manual`

| `op` | Fields | Reply |
|---|---|---|
| `pump_run` | `motor`, `flow_ml_min` 0.1–80, `direction` 1 \| −1, `seconds` 1–`max_run_s` | `{accepted, motor, until}` |
| `pump_stop` | `motor` | `{stopped: true}` |
| `pump_dose` | `motor`, `volume_ml` 0.01–`max_dose_ml`, `direction` 1 \| −1 | `{accepted, op_id, eta_s}` |
| `pump_dose_weight` | `motor`, `target_g` 0.01–`max_dose_ml` | `{accepted, op_id}` |
| `clean` | — | `{accepted, op_id, eta_s}` |
| `syringe_init` | — | `{accepted, op_id}` |
| `syringe_move` | `kind` `aspirate` \| `dispense`, `port` 1–3, `volume_ml` | `{accepted, op_id, eta_s}` |

All but `pump_stop` are refused by ownership (`busy`) and by `Devices.permit_motion()` (`blocked`). `pump_stop` is always accepted.

### C.6 `GET /api/state` — complete shape

```jsonc
{
  "version": "2.0.0",
  "boot_id": "9f3c1a…",          // random per program start
  "time": "2026-10-06T12:00:00+08:00",
  "demo": false,
  "pin_set": true,
  "unlocked": false,             // this tab's session is valid (this call does not renew it)
  "unlock_left_s": 0,
  "operator": "",                // current operator, "" = none
  "owner": null,                 // who holds the equipment: null | {"kind": "round"|"manual"|"tool", "label": "…", "since": "…"}
  "message": {"time": "…", "level": "info", "text": "…"},   // newest event (footer)
  "events_rev": 412,             // grows with every event; pages reload /api/events when it changes

  "rpi": {
    "connected": true, "host": "192.168.1.1", "port": 8767, "error": "",
    "server": { /* hello reply: version, sim, flow, watchdog_s, limits … */ },
    "pending": "",               // RPi command in flight
    "status": { /* the RPi status reply as it is: busy, job, age_s, snapshot{parameter, power, nano},
                   ready{plasma, problems[]}, estimate_s, start_s, flow, sim */ },
    "status_age_s": 0.4,
    "hv": {"known": true, "idle": true, "reasons": []},      // B.4
    "own_job": null,             // id of a manual discharge tied to this page
    "found": [], "addresses": ["192.168.1.2"]
  },

  "pump": {
    "mode": "real",              // real | sim | off
    "link": "connected",         // off | not_configured | not_found | several | connecting | connected | lost | wrong_device | port_busy
    "port_setting": "auto", "port": "COM3", "error": "", "since": "…",
    "banner_seen": true,         // false: the board did not reset when the port was opened
    "firmware_match": true,      // false: STATE fingerprint differs from the YAML
    "motion_uncertain": false,   // true: nobody knows whether a pump is turning
    "cleaning": false,
    "groups": [{"id": "A", "label": "A Group", "selected": 2, "state": "idle"},      // state: idle | wait | run | move
               {"id": "B", "label": "B Group", "selected": null, "state": "idle"}],
    "motors": [{"id": 2, "label": "motor 2 · water", "fluid": "water", "group": "A", "valve": 1,
                "state": "off",  // off | wait (valve opening) | run | move (finite dose)
                "flow_ml_min": null, "direction": 1,
                "left_s": null,  // seconds until the host deadline of a continuous run
                "by": null}],    // manual | round | clean
    "valves": [{"id": 1, "label": "solenoid valve 1 · water", "open": false}],
    "syringe": {"online": true,        // null = never asked
                "initialised": false, "busy": false, "size_ul": 5000,
                "port": null,          // head valve position last commanded
                "volume_ul": null,     // host bookkeeping; null = unknown
                "speed_ml_s": 0.4, "error": null},
    "scale": {"online": true, "grams": 12.345, "stable": true, "age_s": 0.1,
              "calibrated_at": "2026-08-26T10:16:20+00:00", "reference_g": 200.0},
    "op": null,                  // running blocking operation: {"id", "kind", "motor", "by", "started_at", "eta_s", "elapsed_s", "text"}
    "last_result": null,         // last finished manual operation: {"time", "ok", "text"}
    "settings": {"max_run_s": 120, "max_dose_ml": 200, "syringe_ul": 5000, "syringe_speed_ml_s": 0.4}
  },

  "interlock": {
    "manual": {"ok": true, "reasons": []},          // may a manual pump / syringe operation start now
    "plasma": {"ok": true, "reasons": []},          // may a manual discharge start now (pump side + RPi readiness)
    "start":  {"ok": false, "reasons": [{"code": "no_operator", "text": "Please select the operator first", "fix": "Click ”Operator” in the upper right corner"}]}
                                                    // Recipe-independent blockers of Start scheduling; Recipe-specific ones come from /api/schedule/preview
  },

  "schedule": null,              // or the record of D.2 with only the last 60 slots
  "round": null,                 // the running round, else the last finished one:
                                 // {id, status, schedule_id, slot, recipe{id, name}, started_at, finished_at, step_index, step_count,
                                 //  round, rounds, phase, phases[], paused_at, paused_pumps[], error, plasma_job,
                                 //  steps_window: {from, items: [{i, text, phase, status, started_at, finished_at, result_text}]}}   // 12 steps around the current one
  "latest_run": null,            // newest stored spectrum set: {job, kind, saved_at, voltage_v, peak_nm, peak_value, signal_frames, background_frames, flow, wait_s}

  "alarms": {"worst": null, "unacked": 0,
             "active": [ /* {"id", "key", "priority", "area", "text", "since", "acked", "cleared"} */ ]}
}
```

Reason codes used in `interlock` and `blockers`: `no_operator`, `schedule_active`, `round_active`, `manual_active`, `tool_active`, `pump_off`, `pump_link`, `pump_uncertain`, `pump_moving`, `cleaning`, `syringe_not_ready`, `scale_offline`, `hv_on`, `hv_unknown`, `rpi_link`, `rpi_busy`, `rpi_not_ready`, `recipe_invalid`, `interval_too_short`.

---

## D. Disk layout

### D.1 Files under `pc\data` (demo: `pc\data-demo`, same layout plus `sim\` as today)

| Path | New | Written by | Notes |
|---|---|---|---|
| `config.json` | | installer, Maintenance › connection | `{rpi_host, rpi_port, token}` — unchanged, so `Install.cmd` keeps working |
| `settings.json` | NEW | migration, Maintenance | `{"version": 2, "pump": {"port": "auto", "usb_ids": ["2341:0042", "2A03:0042"], "max_run_s": 120, "max_dose_ml": 200, "syringe_ul": 5000, "syringe_speed_ml_s": 0.4}}`; unknown keys are refused |
| `pin.json` | | `Set-PIN.cmd` | unchanged |
| `operators.json` | NEW | Maintenance › operator | `{"operators": []}` |
| `alarms.json` | NEW | `Alarms` | `{"next_id": 18, "alarms": [{id, key, priority, area, text, since, acked, acked_at, cleared, cleared_at}]}` (active and cleared-unacked only) |
| `events.jsonl` | | `Store.event` | `{time, level, text, actor}`; append only |
| `schedule.json` | | `Runner` | current / last schedule (D.2) |
| `runlog.jsonl` | NEW | `Runner` | one line per finished round and per slot that did not run; source of execution record |
| `recipes\<32 hex>.json` | NEW | Maintenance › Recipe | `{id, name, created_at, csv, sha256, step_count, rounds, uses_pump, uses_rpi, uses_plasma, builtin}`; SHA-256 of the CSV text is re-checked at every load (`Recipe file is different from when published, please re-publish.`) |
| `recipes\deleted\<id>.deleted-<YYYYmmdd-HHMMSS>.json` | NEW | delete | moved, never erased |
| `rounds\ROUND-<32 hex>.json` | NEW | `Runner` | D.2 |
| `runs\<job>.json`, `runs\<job>.meta.json` | | `RpiLink` | spectrum sets as today. Meta gains `flow`, `round_id`, `source` (`round` / `manual` / `check`); old files with `sequence` stay readable |
| `pumpboard\hardware.runtime.yaml` | NEW | `PumpBoard` | generated at every connect; not user data |
| `pumpboard\scale_calibration.json` | NEW | V7 `scale.py` | `{version: 1, saved_at, offset, divisor, reference_grams}`; seeded from the vendored copy at migration |
| `logs\pumpboard.log` (+ `.1`…`.3`) | NEW | `PumpBoard` | serial lines sent and received (no `WEIGHT`), 1 MB each |
| `logs\guard.log` | NEW | guard | one line per action |
| `backup\from-1.3.0-<stamp>\` | NEW | migration | copies of what was rewritten |

Writing: every JSON file through `write_json` (`<name>.tmp`, then `replace`, 10 retries on `PermissionError`). JSONL: open for append, one `write`, `flush`; readers skip a broken last line. Nothing under `pc\data` is ever deleted by the program.

### D.2 Record shapes

**Schedule** (`schedule.json`)

```jsonc
{"version": 2, "id": "…", "status": "waiting", "created_at": "…", "finished_at": null,
 "operator": "Wang Xiaoming",
 "recipe": {"id": "…", "name": "Only discharge measurement", "sha256": "…"},
 "uses_pump": false, "uses_rpi": true, "uses_plasma": true,
 "confirmed": {"high_voltage": true, "liquid": false, "hv_off_checked": false},
 "voltage_v": 300, "estimate_seconds": 21,
 "spec": { /* schedule.validate() */ }, "planned_total": 6, "last_slot_at": "…",
 "next_index": 0, "next_slot_at": "…",
 "slots": [{"index": 0, "planned_at": "…", "status": "done", "round_id": "…", "started_at": "…", "finished_at": "…", "note": ""}],
 "counts": {"done": 1}, "error": ""}
```

**Round** (`rounds\ROUND-<id>.json`) — written at the start, updated while it runs, final at the end. It keeps what a later analysis needs.

| Field | Content |
|---|---|
| `id`, `status`, `started_at`, `finished_at`, `error` | |
| `schedule_id`, `slot` (1-based), `slots_planned`, `planned_at` | |
| `operator` | name at the start |
| `sample_kind` | `"sample"` (reserved: `"blank"` when blank handling returns) |
| `recipe` | `{id, name, sha256, csv}` — the CSV text itself (the HMI did not keep it) |
| `steps[]` | `{i, line, action, target, value, unit, note, round, rounds, phase, status, started_at, finished_at, result}`; `result` per action: dose `{steps, sps, eta_s, elapsed_s}`; weight dose `{target_g, dispensed_g, error_g, within_tolerance, topups, elapsed_s, baseline_g}`; run `{sps, flow_ml_min, direction, planned_s, ran_s}`; syringe `{port, volume_ul}`; `plasma_run` `{job}` |
| `plasma[]` | per `plasma_run`: `{job, started_at, finished_at, flow, voltage_v, parameters{7 values}, integration_ms, background_frames, signal_frames, timing{wait_s, lead_ms}, peak_nm, peak_value, saved}`; the frames are in `runs\<job>.json`, the raw files stay on the RPi under that job id |
| `hardware` | `{pulses_per_ml, dose_pulses_per_ml, dose_flow_ml_min, firmware_banner, scale_calibration{divisor, offset, saved_at}, syringe_ul}` at the start (only when the Recipe uses the board) |
| `versions` | `{pc, rpi, v7_backend, sim}` |
| `stop_report` | `{pump{result, error}, rpi{result, error}}` when the round did not complete |
| `paused` | `[{at, resumed_at}]` |
| `messages[]` | last 120 `{time, text}` |

**Run log line** (`runlog.jsonl`): `{kind: "round" | "slot" | "legacy", time, operator, recipe_name, schedule_id, slot, slots_planned, status, finished_at, round_id, spectra: [job…], result_text, note}`. `result_text` examples: `highest peak 337.1 nm`, `CuSO₄ 10.02 g`, `Not measured`.

### D.3 Migration from 1.3.0 (`migrate.run(data_dir)`; runs when `settings.json` is missing; safe to run again)

| # | Step | Detail |
|---|---|---|
| 1 | backup | copy `config.json`, `schedule.json`, the last 2 MB of `events.jsonl` to `backup\from-1.3.0-<stamp>\` |
| 2 | `operators.json`, `alarms.json` | created empty when missing |
| 3 | `schedule.json` | when it has no `version`: add `version: 2`, `operator: ""`, `recipe: {id: null, name: "Discharge measurement (1.3.0) ", sha256: null}`, `uses_plasma: true`, `uses_rpi: true`, `uses_pump: false`; slot `job` → kept and also listed as `spectra`. A schedule that was active becomes `interrupted` by the normal start-up rule |
| 4 | `recipes\` | created; the built-in “Discharge measurement only” is published from `pc\recipes\plasma-only.csv` (`builtin: true`) |
| 5 | `runlog.jsonl` | built once from `runs\*.meta.json` of kind `plasma`: `{kind: "legacy", time: started_at, recipe_name: "Discharge measurement (1.3.0) ", status: "completed", spectra: [job], result_text: "highest peak … nm"}` so the old measurements appear in execution record. Captures stay visible in Maintenance › spectrum |
| 6 | `pumpboard\scale_calibration.json` | copied from `pc\vendor\v7\config\scale_calibration.json` when missing (real data folder only) |
| 7 | `settings.json` | written last — marks the migration as done |

`config.json`, `pin.json`, `events.jsonl`, `runs\` are not rewritten. A failure in any step writes an error event and leaves everything in place; the program still starts.

---

## E. UI

Every text below is given as it appears on screen. The look is the HMI's live screen (`web\index.html` style block): tokens, sizes and class names are taken from `understand\hmi-ui-style.md`.

### E.1 Frame and scaling

```
#stage 1024 x 768 (column)                         demo: 5 px amber inner ring (#d9a300)
├─ .top     56   title | mode chip | ··· | pump control board chip | RPi chip | operator | [maintenance mode] | illustrate | clock
├─ .banner  44   worst alarm, or “No active alarm”
├─ .body   598   .nav 116 | .page 908 (padding 8 x 10 -> 888 x 582 usable)
└─ .foot    70   Start scheduling | pause PAUSE | Stop STOP | progress | [⚡] | E-stop notice
overlays inside #stage: .overlay + .dialog
```

- `body` background `#d9dadc`, `overflow: hidden`. `main.js` computes `k = min(innerWidth / 1024, innerHeight / 768)` on load and on `resize` and sets, through the CSSOM, `transform: scale(k)` with `transform-origin: 0 0` and the left / top offsets that centre the stage. Proportions never change; dialogs are inside the stage and scale with it.
- No scrolling anywhere. Lists are paged: `◀ Previous page` · `No. a / b Page` · `Next page ▶`. Rows per page: steps 8, slots 9, run log 9, alarms 8, events 12, Recipes 6, CSV rows 7, spectra 5, operators 8, ports 4, dialog choices 8.
- Fonts: Barlow 400 / 500 / 700 and Barlow Condensed 400 / 600 from `pc\web\fonts\` (F.2); Chinese falls back to `"Microsoft JhengHei"`. Base 15 px.
- One poll drives the screen: `GET /api/state` every 1000 ms (4 s abort). No fresh answer for 5 s → every device value shows `? unknown`, alarm `ui_stale`, start buttons disabled; STOP stays enabled.

### E.2 Colour and state rules (the HMI's, unchanged)

| Meaning | Look | Used for |
|---|---|---|
| running / ON | solid blue `#2f5f8f`, white bold | a pump that turns, current step, selected nav / tab / segment, primary buttons |
| normal / OFF | dark grey text on white or `#fbfbfb` | everything normal |
| value not trustworthy | magenta `#a3168e`, `?` prefix, dashed frame | link lost, stale data, `motion_uncertain` |
| not present | light grey dashed frame | board not connected / not used, RPi not connected yet |
| alarm priority | Emergency red `#c62828` · high orange `#e06a00` · middle yellow `#e0b400` | banner, badge, alarm rows only |
| high voltage | red 2–3 px frame, hazard stripes `#ffd000` / `#1d1f20` | HV tile, HV dialog, manual-plasma button, `⚡` tags |
| good | green `#3d7a52` (dot, outline) | link chip dot, completed status |
| paused / must fill in | amber `#b88a00` (dashed 2 px), `#ffe9a8` fill | PAUSE button, paused step, unset tile |

Radius 0, 1 px borders `#b9bcc0` (buttons `#8b8f94`), no gradients, shadows only on dialogs. The only animation: the blinking priority box of an unacknowledged alarm.

### E.3 Top bar

| Element | Texts | Behaviour |
|---|---|---|
| title | `Plasmic Electronic control` + small `2.0.0` | — |
| mode chip | `official mode` · `Simulation mode · No real device connected` (amber stripes) | — |
| chip pump control board | `pump control board wired` (green dot) · `pump control board connecting...` · `pump control board ? Connection interrupted` (magenta; `lost`, `port_busy`, `wrong_device`, `several`) · `pump control board not connected` (grey dashed; `not_found`, `not_configured`, `off`) · `pump control board simulation` | tooltip: port and the error text |
| chip RPi | `RPi Connected` · `RPi ? Connection interrupted` (was connected in this session) · `RPi not connected` · `RPi simulation` | tooltip: address and the error text |
| operator button | small `operator`, below the name or `Please select` (brown) | dialog Select operator |
| `maintenance mode` | only while this tab is unlocked (blue outline) | goes to Maintenance |
| `illustrate` | — | opens `/guide` in a new tab |
| clock | `YYYY-MM-DD` over `HH:MM:SS` | — |

### E.4 Alarm banner

`No active alarm` (grey), or the worst alarm (unacknowledged first, then priority, then newest): priority box `Emergency` / `high` / `middle` · time · text (click → full text in a dialog) · `Also N item` · button `confirm` (when unacknowledged) · button `Alarm list`. Colours: full priority colour while unacknowledged, white when acknowledged; `· Restored` after the text when cleared.

### E.5 Navigation

`Overview` · `Schedule measurement` · `manual operation` · `execution record` · `alarm` (badge: number, coloured by the worst unacknowledged priority) · at the bottom, dashed: `🔧 Maintenance` (locked) / `Maintenance settings` (unlocked). No other items. Hash links `#dash #schedule #manual #runs #alarms #maint`.

### E.6 Footer

| Element | Texts | Enabled when / effect |
|---|---|---|
| start (140 × 54, blue) | `Start scheduling` · `Scheduled` · `continue RESUME` (green) | idle and no blocker → start dialog (E.13). `Scheduled` is disabled. Paused → `POST /api/resume` (no dialog) |
| pause (amber outline) | `pause PAUSE` · `Pausing...` | schedule `waiting` / `running` → `POST /api/pause`, no dialog |
| stop (black 2 px outline) | `Stop STOP` | **always enabled**, no dialog, `POST /api/stop`. Result in the progress area: `Stopped: Pump Confirmed; High Pressure Confirmed` or the alarm. If the request itself fails: `The stop command cannot be sent: the program does not respond. Please use a physical switch.` |
| progress | (a) `Unable to start` + first reason (click → dialog `Cannot start scheduling now` listing all with their fix) · (b) schedule active: `Schedule` `No. n / N Second-rate` · `Next measurement` `HH:MM (mm:ss) ` or `at present` `under measurement` / `Suspended` / `Stopping` · `Steps` `a / b · {stage}` · `Not measured` `n Second-rate` · (c) otherwise `message` + newest message (click → dialog) | — |
| tag | `⚡ contains high voltage` (red outline; an active schedule discharges) · `⚡ high voltage output` (solid red; a discharge job is running) | tooltip `Schedules to automatically output high voltage for each measurement` |
| E-stop notice | `For emergency stop, please press the physical emergency stop button on the device.` | not a button; tooltip `STOP is a software stop, not a safety level emergency stop.` |

`Unable to start` reasons, first match shown: `program does not respond` · `Please go to ”Schedule Measurement” first Recipe` · `Please select the operator first` · the `blockers` of `/api/schedule/preview` (A.4 texts, e.g. `The pump control board is not connected (this Recipe Need a pump) `, `RPi is not connected (this Recipe To measure) `, `The pre-discharge inspection failed:…`, `Manual operation is not over yet`).

### E.7 Overview (read-only)

| Region | Content |
|---|---|
| device tiles, one row of six (84 px) | **high voltage power supply**: `OFF` · `ON · 300 V` (red) · `? unknown`; sub `settings 300 V · output 0 V/0.000 A`. **Nano**: `standby` · `Ready` · `Waiting for trigger` · `Outputting` · `Complete` · `fault`; sub `Fault: None` or the fault name. **peristaltic pump**: `stop all` · `motor 8 running` (blue) · `Quantifying` · `Cleaning` · `? Unknown status` · `not connected`; sub `A Group: Stop · B Group: Motor 8 · OUT 40 mL/min`. **syringe pump**: `not initialized` · `standby` · `in action` · `not detected`; sub `Valve position 2 · inside the syringe 1.000 mL`. **scales**: `12.345 g`; sub `stable` · `Changing` · `No reading value`. **latest spectrum** (a button): `highest peak 337.1 nm`; sub the time → opens that round's record |
| panel Process | header: `is executing:` / `Suspended:` / `last executed (Finish｜failed｜Stopped｜Interrupted) : ` + Recipe name; right `Schedule No. n/N times · No. r/R wheel · step a/b`. Phase strip (numbered boxes: done grey, current blue, paused amber, failed orange). Step table with the columns `#` · `action` · `state` · `results`, 8 rows, follows the current step; pager when the round has ended. Empty: `There is no process now. Go to ”Schedule Measurement” and select Recipe,Then click ”Start Scheduling” below.` |
| status strip (56 px, five cells) | `RPi connection` · `pump control board` · `solenoid valve` (`1 close · 2 close`) · `operator` · `active alert` (`N (unconfirmed M) ` / `none`) |

Step sentences (`text`, built on the server from the YAML labels): `Waiting 30 Second` · `motor 8 (OUT) continuous operation 40 mL/min` · `motor 8 (OUT) Stop` · `motor 9 (CuSO₄) Quantitative 10 mL` · `motor 9 (CuSO₄) scale weight added to 10 g` · `automatic cleaning` · `syringe pump initialization` · `Syringe pump speed 0.4 mL/s` · `Syringe pump slave valve position 2 Inhalation 1 mL` · `Syringe pump moves to valve position 1 launched 1 mL` · `Read the device status (confirm that the high voltage is turned off)` · `synchronous Nano parameter` · `discharge＋capture spectrum` · `capture spectrum (no discharge)` · `Turn off high voltage`. Step status: `Waiting` · `in progress` · `Complete` · `failed` · `Stopped` · `Not done`.

### E.8 Schedule measurement

Left panel `Schedule setting` (392 px), sub `Click on each item to modify; The settings will be remembered on this computer` (while a schedule is active: `Scheduling is in progress: modification can only be made after stopping`, tiles disabled and showing the running schedule). Tiles, two columns, 80 px (an unset required tile: amber dashed frame):

| Tile | Value | Sub-text | Edit |
|---|---|---|---|
| `Recipe` | name / `Please select` | `N step · including discharge · including pump` | choice dialog `Select Recipe` (invalid ones greyed with the reason) |
| `operator` | name / `Please select` | `Each measurement will be recorded` | `Select operator` |
| `start` | `Start now` / `HH:MM start` | `Even if the time has passed, it will be tomorrow` | `Start method`: `Start now` · `specified time` (two number fields hour 0–23 / point 0–59) |
| `Measurement interval` | `Every N minutes` | `Calculate from the starting time of each time` | number 1–1440 |
| `end condition` | `common N Second-rate` / `arrive HH:MM until` / `Continue until stop is pressed` | `Only once` when N = 1 | `specified times` (1–1000) · `to the specified time` · `Continue until stop is pressed` |
| `measurement content` (read-only) | `⚡ contains high voltage discharge` (red frame + stripe) / `including spectrum acquisition` / `Only liquid line` | `300 V · Confirm once at the beginning, then automatically discharge` | — |

Below: `Estimate once` · `first measurement` · `Estimated times` (`N Second-rate` / `Continuous`) · `last time` (`according to STOP until`); error box; warning boxes.
Form default (kept in `localStorage['plasmic.schedule.v2']`): start now, every 10 minutes, **1 time**.

Right panel `This schedule` / `Last scheduled` / `Scheduling record`; sub `{Recipe} · {operator} · {start time}`; right `Complete n · Skip n · failed/not started n`. Table `sequence | scheduled time | state | start | End | Remarks`, newest first, 9 per page. Empty: `has not started scheduling yet`, `Select on the left Recipe, start time, interval and end condition, and then click ”Start Schedule” below. Only measure once and set the end condition to ”Total 1 times”.`

### E.9 manual operation

Notice bar (one line + optional fix line):

| State | Bar | Text |
|---|---|---|
| normal | yellow edge | `A manually started pump will automatically stop after a set number of seconds. program or USB There is no guarantee that it will stop when interrupted, please prepare the physical switch. It can only be used after` |
| RPi not connected | yellow edge | `RPi There is no connection, and the program cannot confirm that the high voltage is off. Please go to the site to confirm that the high-voltage power supply is turned off before starting.` (each start dialog then carries the extra tick) |
| schedule busy | grey edge | `Scheduled measurement in progress: Manual start is locked. To operate manually, please pause or stop the schedule first.` |
| round paused | grey edge | `Process paused: The pump is reserved by the process. Please click ”Continue” or ”Stop” below.` |
| high voltage not off | orange edge | `High voltage is not turned off yet: manual start is locked ({reason}) .OFF and ”Stop STOP”still available.` |
| board not connected | grey edge | `Pump control board is not wired.` + fix `Go to ”Repair › Connect”Check.` |
| `motion_uncertain` | orange edge | `The status of the pump is unknown. Please go to the site to confirm and turn off the power to the pump if necessary.` |
| demo | blue edge | `Simulation mode: The simulated pump is operated here.` |

Three columns:

**Left 300 px — panel `peristaltic pump`** (sub `A group can only transfer one at a time; A group and B group can transfer one piece each`). Eight selectable rows (58 px), grouped under `A Group` / `B Group` labels: `motor 2 · water` + state tag (`Stop` · `Wait for the valve to open` · `running 40 mL/min · left 12 Second` (blue) · `Quantifying` (blue) · `Cleaning` · `? unknown`). A running row carries its own `OFF` button.

**Middle 290 px — panel `motor N · {Liquid}`** (the selected row):

| Section | Controls | Disabled when (reason shown under the button) |
|---|---|---|
| `continuous operation` | tile `flow` `40 mL/min` (0.1–80) · segment `direction` `Forward rotation` / `reverse` · tile `automatic stop` `30 Second` (1–upper limit) · buttons `start ON` (blue) and `Stop OFF` | ON: notice-bar states; `Motors from the same group N is still transferring`; `Cleaning`. OFF: only during cleaning (`Cleaning cannot be stopped alone, please press ”Stop” STOP”`) |
| `volume quantification` | tile `volume` `10 mL` (0.01–upper limit; uses the `direction` above) · button `Quantitative conveying`; sub `fixed 40 mL/min,Stop when finished` | notice-bar states; `There is a pump that is still running` |
| `Weighing and quantitative` | tile `weight` `10 g` (0.01–upper limit) · button `Weighing and quantitative`; sub `Use a scale to confirm, it can only run forward, the longest 5 minutes` | same; `scale has no reading value` |

Values are remembered per motor in `localStorage['plasmic.manual.v2']`.

**Right 280 px**

| Panel | Content |
|---|---|
| `syringe pump` | status `not initialized` · `Standby · Valve position 2 · inside the syringe 1.000 mL` · `In action...` · `not detected`; button `initialization`; segment `Valve position` `1` `2` `3`; tile `volume` `1.000 mL`; buttons `Inhalation` · `launched` (disabled with `Please initialize first` · `There is a pump that is still running` · `The syringe cannot be installed` · `Not enough in the syringe`); sub `speed 0.4 mL/s (in ”Maintenance › ”Pump and scale” settings)` |
| `scales` | `12.345 g` (Barlow Condensed 30 px) + `stable` / `Changing` / `No reading value`. Display only |
| `automatic cleaning` | `motor 2 (water) water in 30 Second → and motor 8 (OUT) at the same time 45 Second → Empty 45 Second`; button `Start cleaning` / `Cleaning…`; while running the phase: `Opening water valve` · `Water ingress` · `Water inlet and drain` · `finally empty into the air` |

A running blocking operation shows one line above the columns: `Quantifying: motor 9 · 10 mL · already 4 Second/about 18 Second` — and nothing else can be started until it ends (`Another operation is in progress`).

### E.10 execution record

Panel `execution record`, sub `Each measurement and the period of no measurement`. Table `time | operator | Recipe | sequence | End | state | results | Remarks`, 9 per page (`GET /api/runlog`, reloaded when `events_rev` changes while the page is open). `sequence` = `n/N`. `results`: `highest peak 337.1 nm` · `CuSO₄ 10.02 g` · `Not measured`. Click on a row → dialog `measurement record` (720 px): lines `Recipe` · `operator` · `start` · `End` · `state` · `mistake`; step list (6 per page); for each spectrum a chart (Difference = signal − background), `The strongest peak`, `After pressing N starts discharging in seconds`, button `download CSV`. Empty: `There is no record yet`.

### E.11 alarm

Tabs `alarm list (N) ` · `operation event`.
- Alarm list: counters `Emergency n` · `high n` · `middle n`; button `Confirm all`. Table `time | level | area | message | state | confirm`; state `in action｜Restored · Unconfirmed｜Confirmed`; row button `confirm`; click on the text → full text. Unacknowledged rows pale amber. Empty: `There are currently no alerts`.
- operation event: table `time | who | event`, 12 slim rows per page (`GET /api/events`).

### E.12 Maintenance

**Locked.** Panel `Maintenance login`, sub `Recipe, Connections, plasma parameters, pumps and scales, spectra and operator lists are for maintenance personnel only`. Dots field (`●` per digit), error line (`Maintenance PIN mistake` · `Too many attempts, please N Try again in seconds`), keypad `1 2 3 / 4 5 6 / 7 8 9 / Clear 0 ⌫` (keyboard digits, Backspace and Enter also work), buttons `Return` · `log in`. In demo without a PIN file one more line: `Practice mode maintenance PIN yes 000000`.
No PIN set: `has not been set for maintenance PIN` / `Please double-click on your computer Set-PIN.cmd,settings 6–8 Digital repair PIN.After setting up, return here without restarting.` / button `Return to overview`.

**Open.** Bar: tabs `Recipe` · `connection` · `plasma parameters` · `Pump and Scale` · `spectrum` · `operator`; message chip (last result of a maintenance action; click → full text); `Sign out`. While a schedule is busy every changing control is disabled with `Scheduled measurement in progress: stop scheduling first`.

| Tab | Regions and controls |
|---|---|
| **Recipe** | Left 300 px `Published Recipe`: list (name + `N step · including discharge · including pump`; an invalid one: `Not available at the moment`); footer buttons `Import CSV files` (file picker, `.csv`) · `Example…` (dialog with the three example files to download). Right, a published Recipe: facts `steps/Number of rounds` · `Estimate once` · `content` · `Liquid used` (`motor 9 CuSO₄ 10 g · motor 2 water approx. 13 mL`) · `SHA-256` (12 characters) · `state` (`can be used` / the error); table with the columns `List` · `action` · `object` · `numerical value` · `unit` · `Remarks` (7 per page); buttons `download CSV` · `delete`. Right, an imported file (`new Recipe (has not been released yet)`): tile `Name`; `examine` `passed` / `failed` + the error; warnings; the table; buttons `discard` · `release` (disabled until the check passes and a name is set) |
| **connection** | Left half `RPi (plasma side)`: rows `connection` · `address` · `Server version` · `measurement process` (`Unified Process: Background 1 open`) · `high voltage power supply` · `Nano` · `Last updated` · `Recent errors`; buttons `Modify address…` · `automatic search RPi` · `connection diagnosis`. Right half `pump control board (Arduino Mega) `: rows `connection` · `port` (`automatic → COM3`) · `Firmware response` (`correct` / `is not a pump control board`) · `syringe pump` (`has a response` / `No response`) · `scales` (`has a reading value` / `No reading value`) · `Recent errors`; list of ports, each `COM3 · Arduino Mega 2560 · 2341:0042` with `use this` (a port that is not a Mega: no button, `no Arduino Mega`), first row `Automatic search (recommended)` with `use this`; button `Reconnect`; note `Opening the port will cause the control board to reboot and all pumps to stop.` In demo the right half shows `Simulation mode: use the built-in simulation control panel` and no port list |
| **plasma parameters** | Sub `point value modified. The high pressure should be turned off during storage and the high pressure should not be turned on.`. Seven tiles (4 columns, 112 px): `Pulse ON` ms 1–1000 (0.1) · `Pulse OFF` ms 1–1000 (0.1) · `every round pulse times` 1–10000 · `Number of rounds` 1–1000 · `integration time` ms 1–10000 · `Spectral interval` ms 1–60000 · `HSPY Set voltage` V 1–700; each shows `range a–b`, or `RPi at present N` once changed (blue frame). Buttons `Give up modification` · `save to RPi` · `synchronized to Nano` (enabled only when the Nano's active parameters differ from the file or are missing; else `Nano is already the latest parameter`) · `Clear Nano fault` (enabled only with a fault; else `There is currently no fault`) · full width, red frame + hazard stripes: `⚠ manual start plasma` / `⚠ Discharging...`, sub `will output high voltage · Current setting N V` or the blocking reason (`The pump is still moving and cannot discharge. There are two options for ”` · the RPi's reasons) |
| **Pump and Scale** | Left half `Safety upper limit and syringe pump`: tiles `Continuous operation upper limit` Second (1–600) · `Single quantitative upper limit` mL/g (0.01–1000) · `Syringe pump speed` mL/s; segment `Syringe capacity` `5 mL` / `1.25 mL`; buttons `Give up modification` · `Storage`. Read-only lines: `Quantitative speed 40 mL/min (V7 Profile) ` · `Weighing and quantitative 40 → 10 → 3 mL/min,allowed ±0.5 %` · `cleaning water 30 Second/at the same time 45 Second/Empty 45 seconds (subject to firmware)`. Right half `scale calibration`: weight + `stable` / `Changing`; `Last calibration 2026-08-26 · weight 200 g`; `① Press ”Reset” for empty cup ② Put the weight on ③ Enter weight weight ④ according to“Correction”`; tile `Weight weight` g (0.1–20000) with quick buttons `10` `50` `100` `200`; buttons `Return to zero` · `Correction` (disabled with `There is a pump that is still running` · `scale has no reading value` · `The reading value is still changing, wait for it to stabilize.`) |
| **spectrum** | Left 250 px `spectrum`: list (time; `Schedule measurement` / `Manual discharge` / `Spectrometer inspection`; `signal N Zhang · Background M open`), 5 per page; button `capture spectrum (no discharge)`, sub `Check spectrometer only: read 3 Zhang, high voltage is not turned on`. Right: segment `Difference` · `signal` · `background` · `leaflet`; `◀ No. i / N open ▶` (leaflet); `Return to full spectrum`; chart (drag to zoom, as 1.3.0); info `points 1000 ms · 300 V · After pressing 3.4 starts discharging in seconds`; `The strongest peak (click to enlarge)`; button `download CSV` |
| **operator** | Panel `Operator list`, sub `You need to select a name before starting the schedule. Every measurement and operation will be recorded as to who did it.`. Table with the columns `#` · `Name` · `delete`, 8 per page; button `New operator` |

### E.13 Dialogs

One dialog component (`dialogs.js`), inside the stage: title · key/value lines · choices · number entry · text entry · detail text · yellow warning box · acknowledgement rows (big tick rows; the confirm button stays disabled until all are ticked) · footer `Cancel` + a specific confirm label · line `N If there is no confirmation within seconds, it will be automatically canceled and no instruction will be sent. If there is no message for` Default 30 s (schedule start 90 s, messages 120 s); any key press restarts the countdown; Escape or a click on the scrim cancels. `danger` = orange frame; `hv` = red 3 px frame, red title bar, red confirm button, title starts with `⚠`. No browser `alert` / `confirm` / `prompt`.

| Dialog | Content | Confirm |
|---|---|---|
| `Select operator` | choice grid; empty list: `There is no operator list yet. Ask maintenance personnel to go to ”Repair › Operator” is added.` | (choice) |
| `Select Recipe` | choice grid | (choice) |
| `Start method` · `Measurement interval` · `end condition` · `Measurement times` · `start time` · `end time` | E.8 | `Sure` |
| `Start scheduling` (no plasma) | lines `Recipe` · `operator` · `first measurement` · `interval` · `End` · `Estimate once` · `Liquid used`; warnings; ticks below; warn `schedule will automatically start the pump according to time. ”Stop” on the screen means the software stops, not an emergency stop.` | `Start scheduling` |
| `⚠ Start scheduling: high voltage will be automatically output for each measurement` (hv) | the same lines + `High pressure setting` `300 V (upper limit 700 V) `; warn `During the scheduling period, each measurement will be automatically discharged and will not be confirmed one after another. ”Stop” on the screen means the software stops, not an emergency stop. In an emergency, please press the physical emergency stop button.` | `Start scheduling` |
| ticks of the two dialogs above | with `plasma_run`: `I have confirmed: Every measurement will be automatically output 300 V high pressure,Won’t ask again.` and `The electrode and high-voltage line have been connected; personnel have stayed away; physical emergency stop/Power-off switch is at hand.` · with pump steps: `Piping, reagents, and waste are in place; the physical switch for the pump is operational.` · pump steps and RPi not connected: `RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.` | |
| `Cannot start scheduling now` | list of reasons, each with its fix | `closure` |
| `starter motor 8 · OUT` | lines `flow` · `direction` · `automatic stop`; warn `program, computer or USB is interrupted, the pump will not stop itself. Please confirm that the pipeline is correct and the physical switch can be used.` (+ the RPi tick when it is not connected) | `start` |
| `Quantitative conveying` | lines `motor` · `volume` · `direction` · `Estimated time`; warn the same + `The quantitative meeting will not stop until it is finished. If you want to stop during the process, please click ”Stop” STOP”.` | `Start quantification` |
| `Weighing and quantitative` | lines `motor` · `target weight`; warn `Please do not touch the scale. longest 5 minutes. If you want to stop during the process, please click ”Stop” STOP”.` | `Start quantification` |
| `Start cleaning` | the three times; warn `approx. 2 minutes. If you want to stop during the process, please click ”Stop” STOP”.` | `Start cleaning` |
| `syringe pump initialization` | warn `The piston will move and the liquid in the syringe will be pushed out.` | `initialization` |
| `syringe pump suction` · `syringe pump launched` | lines `Valve position` · `volume` · `speed` · `Estimated time` | `Inhalation` · `launched` |
| `alarm` · `message` | full text | `closure` |
| `measurement record` | E.10 | `closure` |
| `Recipe Name` | text field (1–60) | `Sure` |
| `release Recipe` | lines `Name` · `steps` · `content`; warnings | `release` |
| `delete Recipe` (danger) → `input repair PIN to delete` | name; then the PIN pad | `delete` |
| `RPi address` | fields `RPi of IP address` · `TCP port` · `token (passcode)` (empty = keep) | `Save and reconnect` |
| `automatic search RPi` · `connection diagnosis` | as 1.3.0 (found servers with `Use this address instead`; the six diagnosis steps with their fixes) | `closure` |
| `Use this port instead` | `COM3 · Arduino Mega 2560`; warn `is turned on, the control board will restart and all pumps will stop.` | `Sure` |
| `Store plasma parameters` | each changed item `old → New Unit`; warn `will be used for every measurement thereafter. N V discharge (upper limit 700 V) .Storage does not activate high voltage.` | `Storage` |
| `Clear Nano fault` | the fault name | `Clear` |
| `⚠ Manually start plasma: high voltage will be output` (hv) | lines `HSPY Set voltage` · `Pulse ON/OFF` · `every round pulse times` · `Number of rounds` · `integration time` · `Spectral interval` · `Estimated time`; tick `I have confirmed that it will be output soon 300 V High voltage, personnel are far away from the electrode, physical emergency stop/Power-off switch is at hand.` | `Start plasma (output high voltage)` |
| `Storage pump and scale settings` | changed items `old → new` | `Storage` |
| `Scale reset to zero` | warn `Please confirm that the cup on the scale is empty. The current weight will be treated as 0,and archive.` | `Return to zero` |
| `scale calibration` | lines `Weight weight` · `Reading value now`; warn `Please confirm that the weight is in place and the reading is stable. Calibration results are archived.` | `Correction` |
| `New operator` | text field (1–20) | `New` |
| `Delete operator` | the name | `delete` |

### E.14 Number pad or plain inputs — decision

A value tile opens a dialog that holds **one native text field and the on-screen number pad next to it**; both write the same value (the 1.3.0 parameter dialog already works this way). Text (Recipe name, operator name, RPi address, token) uses a native field only.
Reason: this PC has a keyboard and a mouse and the owner types Chinese names with the IME, so a touch-only pad would slow him down; the pad keeps `Start-Kiosk.cmd` on a touch panel usable for all numbers. A full on-screen letter keyboard (the HMI's) is left out: with a real keyboard it would be a second way to do the same thing. The range line (`range a–b,decimal n Bit` / `,integer`) and the error `is out of range or the format does not match` are as in the HMI; the confirm button is disabled until the value is valid.

### E.15 Web files (`pc\web\`, ES modules, no build step, no dependency)

| File | Contains |
|---|---|
| `index.html` | the stage skeleton (top, banner, nav, page container, footer, overlay root), one `<script type="module" src="/js/main.js">`, four `<link rel="stylesheet">` |
| `css\fonts.css` | the five `@font-face` rules pointing at `/fonts/…` |
| `css\frame.css` | tokens, stage, top, banner, nav, footer |
| `css\components.css` | panel, buttons, segments, tabs, tiles, tags, chips, tables, pager, notice, dialog, keypad, chart |
| `css\pages.css` | per-page layout (dash, schedule, manual, runs, alarms) |
| `css\maint.css` | Maintenance login and the six tabs |
| `js\main.js` | start: bootstrap, poll loop, router (hash), scaling, stale detection |
| `js\api.js` | `get`, `post` (token, unlock header, one re-bootstrap on `Invalid local request`), error object |
| `js\ui.js` | `$`, `make` (textContent only — no `innerHTML`), formatters, pager, state-to-class helpers (`q-run`, `q-bad`, …) |
| `js\dialogs.js` | the dialog component, number entry + pad, text entry, choices, tick rows, timeout |
| `js\frame.js` | top bar, banner, nav, footer (start / pause / stop, progress, start dialog) |
| `js\chart.js` | the spectrum canvas chart (moved from 1.3.0 `app.js`) |
| `js\page-dash.js`, `page-schedule.js`, `page-manual.js`, `page-runs.js`, `page-alarms.js` | one module per page: `render(state)` (idempotent) and its handlers |
| `js\page-maint.js` | login panel, tab bar, lock |
| `js\maint-recipe.js`, `maint-link.js`, `maint-plasma.js`, `maint-pump.js`, `maint-spectra.js`, `maint-operators.js` | one module per tab |
| `fonts\*.ttf`, `fonts\Barlow-OFL.txt`, `fonts\BarlowCondensed-OFL.txt` | F.2 |

Removed: `pc\web\app.js`, `pc\web\style.css`. Lists are rebuilt only when their key changes, so a row is never replaced under the pointer (the 1.3.0 rule).

---

## F. Vendoring, launchers, installer, versions

### F.1 V7 code (byte-identical copies; `V7\` = `C:\Users\PlasmaLab\Desktop\independent laboratoryv7`)

| Source | Destination under `pc\vendor\v7\` | Used |
|---|---|---|
| `V7\backend\__init__.py`, `config.py`, `registry.py`, `transport.py`, `simulator.py` | `backend\` (same names) | at run time |
| `V7\backend\functions\__init__.py`, `motors.py`, `pump.py`, `scale.py` | `backend\functions\` | at run time |
| `V7\config\hardware.yaml` | `config\hardware.yaml` | read by the wrapper to build the runtime YAML; never edited |
| `V7\config\scale_calibration.json` | `config\scale_calibration.json` | seed for `pc\data\pumpboard\` (D.3-6) |
| `V7\firmware\Arduino\Arduino.ino`, `hw_config.h` | `firmware\Arduino\` | reference only |
| `V7\tools\gen_firmware_config.py`, `tools\__init__.py` | `tools\` | reference only |

Not copied: `backend\main.py` (FastAPI), `frontend\`, `docs\`, `tools\selftest.py`, `tools\probe.py`, `tools\url.py`, `run.bat`, `selftest.bat`, `requirements.txt`, `.venv`.

### F.2 Third-party packages and fonts (no download)

| What | Source | Destination | Licence text |
|---|---|---|---|
| pyserial 3.5 | `V7\.venv\Lib\site-packages\serial\` — all 28 `.py` files incl. `tools\`, `urlhandler\`, `threaded\`; no `__pycache__` | `pc\vendor\site\serial\` | `…\pyserial-3.5.dist-info\METADATA` and `DESCRIPTION.rst` → `pc\vendor\licenses\pyserial-3.5\`. **That folder holds no licence file** `[FACT, listing]`; `METADATA` says `License: BSD`, and 25 of the 28 source files carry the line `SPDX-License-Identifier: BSD-3-Clause` `[FACT, counted]`. Recorded in `pc\vendor\README.txt`; getting the full text would be a download and needs the owner's yes |
| PyYAML 6.0.2 | `V7\.venv\Lib\site-packages\yaml\*.py` (17 files). **Not** `_yaml.cp313-win_amd64.pyd` (built for Python 3.13; the bundled interpreter is 3.12) | `pc\vendor\site\yaml\` | `…\PyYAML-6.0.2.dist-info\LICENSE` (MIT) and `METADATA` → `pc\vendor\licenses\PyYAML-6.0.2\` |
| Barlow, Barlow Condensed | `HMI\web\vendor\font-a35caa4687f434d6.ttf` (Barlow 400), `font-91c841fdfa8e7b94.ttf` (500), `font-74103fd6d41bc32c.ttf` (700), `font-c82d65280835cb3c.ttf` (Condensed 400), `font-0d85af813fc3ed87.ttf` (Condensed 600) | `pc\web\fonts\barlow-400.ttf`, `barlow-500.ttf`, `barlow-700.ttf`, `barlow-condensed-400.ttf`, `barlow-condensed-600.ttf` | `HMI\web\vendor\Barlow-OFL.txt`, `BarlowCondensed-OFL.txt` → `pc\web\fonts\` |

`pc\vendor\README.txt` (UTF-8, LF): what was copied, from where, on which date, V7's `backend.__version__` (`5.0.0`), the rule "do not edit; changes belong in `pc\pumpboard.py`", and the pyserial licence note.
`pc\vendor\MANIFEST.sha256`: one line per vendored file (`sha256  size  relative path`), written at vendoring time.
Vendored files keep their own line endings (they must stay byte-identical); EC's LF rule applies to EC's own files.
`SHA256SUMS.txt` is not touched; the vendored files are covered by the manifest instead.

### F.3 Sync-check tool — `docs\tools\check_v7_sync.py` (read-only, standard library)

| Call | Does | Exit |
|---|---|---|
| no argument | every file under `pc\vendor` against `MANIFEST.sha256`; extra or missing files | 1 on any difference |
| `--v7 <folder>` (default: `%USERPROFILE%\Desktop\independent laboratoryv7` when it exists) | each vendored V7 file against its source: `same` / `different` / `V7 does not have this file`; lists V7 files in `backend\` and `backend\functions\` that are not vendored; compares `serial\` and `yaml\` with V7's `.venv` when present; compares V7's `scale_calibration.json` with `pc\data\pumpboard\` (information only) | 1 when a vendored file differs from V7 |
| `--write-manifest` | rewrites the manifest (used once by the vendoring package, and after a deliberate re-sync) | 0 |

### F.4 Launchers

| File | 2.0.0 |
|---|---|
| `Start.cmd` | stays. Change: `"runtime\python\python.exe" -B app.py` (no `__pycache__`). The program now also connects to the pump board |
| `Start-Demo.cmd` | stays. Change: `-B`. `--demo` starts both simulators (RPi `--sim` and V7 `link.simulate`) |
| `Start-Kiosk.cmd` | stays. Change: `-B` |
| `Set-PIN.cmd` | stays. Change: `-B`. (The text printed by `app.py --set-pin` gains the line `maintenance page must have PIN to enter.`) |
| `Install.cmd`, `Flash-Nano.cmd`, `installer\unpack-python.cmd` | unchanged |
| new launchers | none (the pump board needs no installer: genuine Mega 2560, Windows' own serial driver `[INFER]`) |

All `.cmd` stay ASCII + CRLF.

### F.5 Installer (`installer\install.py`)

- The list of required files (step 1) gains: `pc/application.py`, `pc/httpd.py`, `pc/store.py`, `pc/migrate.py`, `pc/pinlock.py`, `pc/alarms.py`, `pc/rpilink.py`, `pc/recipe.py`, `pc/pumpboard.py`, `pc/pump_guard.py`, `pc/devices.py`, `pc/steps.py`, `pc/runner.py`, `pc/manual.py`, `pc/recipes/plasma-only.csv`, `pc/vendor/MANIFEST.sha256`, `pc/vendor/v7/backend/transport.py`, `pc/vendor/site/serial/__init__.py`, `pc/vendor/site/yaml/__init__.py`, `pc/web/js/main.js`.
- One printed line after step 5: `pump control board (Arduino Mega) No need to install: USB cable into this computer. After opening the program, go to ”Maintenance › Connection” you can see it.`
- Nothing else changes: `config.json` keeps its shape, the RPi steps are untouched, the installer never touches a COM port.

### F.6 Version numbers

| Part | Version |
|---|---|
| PC program (`pc\app.py`) | **2.0.0** |
| RPi TCP server (`rpi\tcpd\plasmic_tcpd.py`) | must also become **2.0.0** — today the file still says `1.3.0` `[FACT :46]`, and the installer pushes the RPi package only when the versions differ. This file belongs to the separate RPi review; listed here as a dependency, not as work of this plan |
| TCP protocol | 1 (unchanged) |
| Nano firmware | 1.2.1 (unchanged) |
| Mega firmware | unchanged; unversioned |
| vendored V7 backend | `5.0.0` (its own `__version__`) |
| data layout (`settings.json` `version`) | 2 |

---

## G. Test plan

`unittest`, standard library, bundled interpreter with `-B`, each file runnable by itself, only `127.0.0.1`, `netcheck.local_ipv4` replaced by `[]` in every new harness. Pump tests use `pump_mode='sim'` with the tripwire installed.

| File | New | Important cases |
|---|---|---|
| `tests\pump_testing.py` | NEW (helper) | `install_tripwire()`, simulated port lists (names like `COM91`), `FaultPlan`, `make_board(tmp, **overrides)`, one process-wide runtime YAML path |
| `tests\test_vendor.py` | NEW | manifest matches every vendored file; no `.pyd`, no `main.py`, no `__pycache__` under `pc\vendor`; the vendored modules import under the bundled interpreter; 26 functions register; `yaml.__with_libyaml__` is false |
| `tests\test_port_safety.py` | NEW | a non-simulated board without `allow_real_ports()` raises `RealPortForbidden` before pyserial is touched; `Application(tmp)` (old harness form) leaves the board `off`; only `pc\app.py` calls `allow_real_ports(`; only `pumpboard.py` and `pump_guard.py` import `serial`; no file under `tests\` names a real port; tripwire count is zero at module end |
| `tests\test_pumpboard.py` | NEW | **port choice**: one Mega → chosen; none; two; a CH340 is never chosen; a saved port that is not a Mega is refused; `auto` never reaches V7. **Identity**: wrong reply → port closed and nothing but `?` was written; no automatic retry. **Connect**: traffic order `X` → calibration → `W,AVG` → `W,STABLE` → `W,STREAM` → `?`. **Run**: deadline stops the motor; second motor of the same group refused; A and B together; `stop_motor` verifies `idle`. **Dose**: `wait → move → idle` for motor 2 incl. valve close; timeout → `X`; tiny dose. **Weight dose**: reaches the target in the simulator; `stop_all` cancels it without waiting for `op_lock`; hard timer. **Clean**: completes with short times; `stop_all` aborts it. **Syringe**: init → aspirate → dispense bookkeeping; over-capacity refused; `PUMP,…,error=7` → `SyringeFault`; bookkeeping void after `stop_all`. **Faults**: dropped reply → `resync` with `W,CFG`; no terminator → timeout, not success; `ERR,…` texts; cable pulled during a dose → `lost`, `motion_uncertain`, alarm, reconnect; pulled during a weight dose → V7's text replaced; `READY` mid-operation → failure and B.3.4 traffic again; heartbeat failure → `lost`. **Stops**: `stop_all` ends with the stream back on; `close()` writes `X`. **Gate**: motion commands refused while the HV flag is set; allow-list refuses `W,SCAN`. One live board per process |
| `tests\test_pump_guard.py` | NEW | `bye` → the port is not opened; end of input → open, `X`; port not a Mega → not opened; port busy → retries, then gives up and logs |
| `tests\test_recipe.py` | NEW | every action: good row, each bad column, range, step; P1–P14 each with the smallest failing CSV and its exact message; W1–W4; repeat expansion, rounds, limits (rows, steps, nesting, total delay); estimates; phases from the YAML labels; the three example files validate; settings change makes a published Recipe invalid (P14) |
| `tests\test_store.py` | NEW | atomic write survives a `PermissionError` retry; **migration** from a synthetic 1.3.0 folder (`tests\fixtures\data-1.3.0\`: `config.json`, old `schedule.json` in state `running`, two `runs\*.meta.json` with `sequence`, `events.jsonl`, `pin.json`): nothing deleted, backup made, built-in Recipe published, `runlog.jsonl` built, old schedule shown as `interrupted`, second run changes nothing; Recipe SHA check, delete = move; operator rules; alarms raise / clear / ack / reload; `PinLock`: no file → not allowed, lockout doubling, `peek` does not renew, practice PIN only in demo |
| `tests\test_rpilink.py` | NEW | `hv_view()` for every row of B.4 (simulator states and edited status dicts); `wait_job` with a 30 s link gap (shortened); `fetch_result` retries the read, never the discharge; STOP uses a fresh connection |
| `tests\test_interlock.py` | NEW | ownership matrix (round / manual / tool / paused schedule); `permit_motion` × (RPi idle, HV on, stale, not connected with / without the tick); `hv_session` × (board quiescent, moving, off, uncertain, valve still open); **race**: 200 rounds of "manual run" against "`hv_session`" on two threads → never both active; `stop_everything` result matrix incl. board link down (→ priority-1 alarm) and RPi unreachable |
| `tests\test_runner.py` | NEW | “Discharge measurement only” with the board `off`: one slot, spectrum saved, round record complete; the pump + plasma example with short overrides; cleaning Recipe without RPi needs `hv_off_checked`; missing ticks refused; **failure of each step type** (`FaultPlan`) → round `failed`, simulator state all idle, RPi `stop` sent when the Recipe has RPi steps, schedule `failed`, alarm; **STOP** during `delay` / `pump_run` / `pump_dose` / `pump_dose_weight` / `clean` / syringe move / `plasma_run` → `stopped`, everything off; **PAUSE** in a `delay` with a running pump → pump stopped, resume restarts it with the remaining time; pause during a dose → `pausing` then `paused`; `skipped_paused`; `skipped_overrun`; three `not_started` end the schedule; deadline trip; pump left running at the end (preflight bypassed in the test) → `X` + `failed`; board lost mid-dose; RPi connection cut during `plasma_run`; restart with a `running` round on disk → `interrupted`, nothing restarts, one RPi `stop`; a discharge is never sent twice; a schedule goes on when nobody polls `/api/state` |
| `tests\test_manual.py` | NEW | every operation; **page watchdog** stops a manual run after the page went silent (shortened); manual locked while a schedule is busy, allowed while it is paused between rounds; RESUME refused while a manual pump runs; OFF always accepted; second blocking operation refused |
| `tests\test_app.py` | REWRITTEN | static table (every file type, `..` / encoded traversal refused, CSP header on all); token / Origin / Host; `/api/state` has every key of C.6; `/api/stop` without PIN is always 200; removed routes answer 404; parameters → discharge through a count-1 schedule → spectrum endpoints and CSV; two sessions see the same operator and alarms; kept from 1.3.0: manual-plasma page watchdog, program-death watchdog, find / moved RPi, first start from `site.json`, new-Nano auto-sync, offline texts; no `sequence` anywhere in replies |
| `tests\test_api_pin.py` | NEW | table-driven over B.10: every gated route is 403 `locked` without a session and works with one; every open route works without; no PIN file → Maintenance routes closed; delete needs the PIN again; `/api/state` does not renew; session expiry |
| `tests\test_web_static.py` | NEW | no `<script>` without `src`, no `style=`, no `on…=` in `pc\web\*.html`; no `innerHTML`, `eval(`, `new Function` in `pc\web\js`; every `import` path exists; fonts and licence files present; web files are LF |
| `tests\test_installer.py` | EXTENDED | the launcher / required-file list of F.5; `.cmd` still ASCII |
| `tests\test_tcpd.py`, `test_measure.py`, `test_flash_nano.py`, `simulated_rig.py`, `simulated_spectrometer.py` | untouched | must stay green |

After every change: `docs\tools\check_sums.py` (22 protected files) and `docs\tools\check_v7_sync.py`.

---

## H. Build plan

No git, one shared folder, implementers in parallel: **every file has exactly one owner**. Interfaces are fixed by this document (B.3.6, B.4, B.5, C, D) before work starts; a package may be built against stubs of its neighbours.

| WP | Owns (no other package touches these) | Depends on | Must know |
|---|---|---|---|
| **1 Vendor** | `pc\vendor\**`; `docs\tools\check_v7_sync.py`; `tests\test_vendor.py` | — | copy byte-for-byte, no `.pyd`, no `__pycache__`; run nothing of V7; write the manifest last |
| **2 Pump board** | `pc\pumpboard.py`, `pc\pump_guard.py`; `tests\pump_testing.py`, `tests\test_pumpboard.py`, `tests\test_pump_guard.py`, `tests\test_port_safety.py` | 1 | B.3 in full; never call `get_link()`; never open a real port (COM3 is the live Mega); every wait checks `abort`, link and reset; stops never take `op_lock` |
| **3 Recipe** | `pc\recipe.py`; `pc\recipes\*.csv`, `pc\recipes\index.json`; `tests\test_recipe.py` | reads `pc\vendor\v7\config\hardware.yaml` through a function argument (tests pass a dict) | A in full; pure functions; messages exactly as written |
| **4 Store** | `pc\store.py`, `pc\migrate.py`, `pc\pinlock.py`, `pc\alarms.py`; `tests\test_store.py`, `tests\fixtures\data-1.3.0\**` | — | D; never open the real `pc\data` in tests (the fixture is synthetic); never delete; `PinLock` file format unchanged |
| **5 RPi link** | `pc\rpilink.py`, `pc\schedule.py`; `tests\test_rpilink.py` | 4 (`Store`) | move `Link` as it is, remove every "sequence", keep the screen watchdog and the new-Nano sync; the TCP protocol is not changed; `tests\test_tcpd.SimServer` is the test server |
| **6 Core** | `pc\devices.py`, `pc\steps.py`, `pc\runner.py`, `pc\manual.py`; `tests\test_interlock.py`, `tests\test_runner.py`, `tests\test_manual.py` | 2, 3, 4, 5 | B.4, B.5, B.7, B.12; no I/O under `Runner.lock` / `Devices.lock`; `finally` always releases ownership and leaves outputs off; a discharge is never repeated |
| **7 App + HTTP** | `pc\app.py`, `pc\application.py`, `pc\httpd.py`; `tests\test_app.py`, `tests\test_api_pin.py` | 2–6 | C in full; B.10 table; B.11; `app.py` is the only caller of `allow_real_ports()`; `import app as pc` must keep exposing `Application`, `ExclusiveHTTPServer`, `make_handler` |
| **8 Web: frame + operator pages** | `pc\web\index.html`; `pc\web\css\fonts.css`, `frame.css`, `components.css`, `pages.css`; `pc\web\js\main.js`, `api.js`, `ui.js`, `dialogs.js`, `frame.js`, `chart.js`, `page-dash.js`, `page-schedule.js`, `page-manual.js`, `page-runs.js`, `page-alarms.js`; `pc\web\fonts\**`; removal of `pc\web\app.js`, `style.css`; `tests\test_web_static.py` | C.6 (can start against a saved example state) | E; CSP rules; texts exactly as written; `render(state)` idempotent; no dead control |
| **9 Web: Maintenance** | `pc\web\css\maint.css`; `pc\web\js\page-maint.js`, `maint-recipe.js`, `maint-link.js`, `maint-plasma.js`, `maint-pump.js`, `maint-spectra.js`, `maint-operators.js` | 8 (`ui.js`, `dialogs.js`, `api.js`, `chart.js` exports) | E.12, E.13; the unlock session lives in `sessionStorage` of the tab |
| **10 Packaging** | `Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`, `Set-PIN.cmd`; `installer\install.py`; `tests\test_installer.py` | file names of 1–9 | F.4–F.6; ASCII + CRLF |
| **11 Documents** | `docs\guide-source\installation-and-usage.src.html`, the built `detailed-guide.html`, `docs\guide-source\README.txt`, `docs\guide-source\shots\*`; `READ-ME-FIRST.txt`; `historical-development-notes.md`; `docs\work record.md` | everything (last) | section I; headings that programs cite must match; nothing is "verified" that was only simulated |
| — (not in this plan) | `rpi\**`, `tests\test_tcpd.py`, `tests\test_measure.py`, `tests\simulated_*.py`, `pc\netcheck.py`, `pc\plasmic_client.py`, `pc\examples\*`, `SHA256SUMS.txt`, `nano\**` | | owned by the RPi review / unchanged |

Order: **1** first (an hour). Then **2, 3, 4, 5, 8** in parallel. **6** when 2–5 expose their interfaces (stubs allowed earlier). **7** after 6. **9** alongside 7. **10, 11** last. Integration by the lead: all test files, `check_sums.py`, `check_v7_sync.py`, a walk through `Start-Demo.cmd` (simulators only), a look that `pc\data\config.json` still points at `192.168.1.1` and that no `__pycache__` was left.

Interfaces the packages build against (signatures only):

```python
# WP2
pumpboard.configure_runtime(path); pumpboard.allow_real_ports()
PumpBoard(data_dir, mode, settings, devices_gate, on_event, on_alarm, ui_age, overrides=None)
  .start() .close() .view() .ports() .reconnect() .apply_settings(settings)
  .quiescent(fresh=True) -> (bool, [reason]) ; .in_use -> bool ; .motion_uncertain -> bool
  .run(motor, flow_ml_min, direction, limit_s, owner) ; .stop_motor(motor)
  .dose(motor, volume_ml, direction, owner, abort) ; .dose_weight(motor, target_g, owner, abort) ; .clean(owner, abort)
  .syringe_init(owner, abort) ; .syringe_speed(v) ; .syringe_move(kind, port, volume_ml, owner, abort)
  .scale_tare() ; .scale_calibrate(grams) ; .stop_all(reason) -> {sent, confirmed, error}
# WP3
recipe.validate(csv_text, hardware, limits) -> dict ; recipe.estimate(steps, hardware, rpi_estimate_s, syringe_speed) -> float
# WP4
Store(data_dir): .config .settings .event(text, level='info', actor=None) .events(before, limit) .operators / .set_operators
  .recipes() .recipe(id) .publish_recipe(name, csv, summary) .delete_recipe(id) .save_round(record) .round(id) .append_runlog(row) .runlog(before, limit)
  .save_schedule(record) .load_schedule() .save_run(result, **meta) .list_runs(limit) .load_run(job)
Alarms(store): .raise_(key, priority, area, text) .clear(key) .ack(ids=None) .view()
PinLock(path, practice_pin=None): .configured .unlock(pin) .allowed(session) .peek(session) .lock_session(session) .verify(pin)
# WP5
RpiLink(store, search=True, auto_sync=True): .view() .latest(max_age) .hv_view() .command(cmd, attended=True, **fields) .wait_job(job_id, stop) .fetch_result(job_id) .stop() .reconnect() .ui_alive() .close()
# WP6
Devices(store, alarms, rpi, pump): .acquire(kind, id, label) .release(id) .owner .permit_motion(ack=False) .hv_session() .hv_blocks_motion() .all_off(need_pump, need_rpi) .stop_everything(actor) .view()
Runner(store, alarms, devices, rpi, pump, operator_getter): .preview(body) .start(body) .pause() .resume() .request_stop() .view() .round_view() .close()
ManualOps(store, devices, pump, operator_getter): .submit(body) .cancel() .active
```

---

## I. Documentation changes (list only)

`docs\guide-source\installation-and-usage.src.html` (then rebuild with the new test count)
- Look here first: six steps — step 2 mentions the pump board's USB cable; step 6 rewritten for the new pages
- Safety instructions — pumps: software STOP is not an emergency stop; no firmware watchdog; cut the pump power before work on tubing
- What does the whole set look like? — new drawing with the Mega (USB to this PC), motors 2–9, valves, syringe pump, scale
- Steps 4 One click installation — nothing to install for the pump board; `Set-PIN.cmd` is now required for Maintenance
- Steps 5 First power-on inspection — new checklist: pump board found, each pump by hand with water, scale zero + calibration, syringe initialise, “Discharge measurement only” once, STOP during a pump run, STOP during a discharge, PAUSE / RESUME, closing the page during a manual run
- Steps 6 Daily operation — rewritten: Overview, Schedule measurement (single measurement = common 1 Second-rate), manual operation, execution record, alarm, footer buttons
- new section Maintenance — the six tabs; how to write a Recipe (action table, the three examples, error messages)
- when something goes wrong — pump board not found / port busy / wrong device / lost link / board restarted; priority-1 alarms and what to do
- References — files under `pc\data` (D.1); HTTP routes are not documented for users; the TCP section stays but loses "sequence"; Verification status rewritten (everything of 2.0.0: simulator only)
- every mention of fast/original, “order”, background 3 open, `plasma_quick.py`, `test_quick.py`, the TCP terminal — removed
- seven screenshots retaken from the demo (`{{IMG_*}}`), new ones for manual operation and Maintenance

`docs\guide-source\README.txt` — screenshot list, new pages
`READ-ME-FIRST.txt` (UTF-8 BOM + CRLF) — what 2.0.0 is, pump board, PIN, first steps
`historical-development-notes.md` — version 2.0.0; architecture drawing with the Mega; file table (new modules, vendor, recipes); rule 2 extended to pumps ("review-workspace never makes a pump, the syringe pump or a valve move on the real rig; COM3 is never opened by review-workspace"); rule 4: vendored files + manifest + `check_v7_sync.py`; the PLC/pump sentence at the top replaced; test list and counts; "one measuring flow"; where things stand (nothing of 2.0.0 run on the rig)
`docs\work record.md` — new section 13: the merge, decisions, what is unverified, the "Not done yet" list (Correction/Trend, blank samples, real-rig checks)
Titles cited by programs (`Steps 5 First power-on inspection`, `Steps 3 Connect to the Internet`, `References › Allow list`, …) stay or are changed on both sides.

---

## J. Risks, and what stays unverified until the owner tries it on the real rig

| # | Item | Why it matters | Mitigation in this design |
|---|---|---|---|
| 1 | Nothing of 2.0.0 will have run on the rig. The unified RPi flow (`plasma_measure.py`) has not either | every claim is "simulator only" | guide Verification status; step-5 checklist in a safe order (water, low voltage) |
| 2 | Does this Mega reset when the port is opened? `[INFER]` | reconnect and the guard rely on "open = everything off"; a reset also interrupts nothing of the syringe pump | `X` is sent after every open anyway; `banner_seen` shows the answer on Maintenance › connection |
| 3 | No firmware watchdog (firmware is not changed). PC power cut, USB re-enumeration or a frozen firmware during a continuous run cannot be covered by software | flooding | finite steps preferred, tight deadlines, guard, priority-1 alarm texts, the E-stop notice. A hardware cut-off for the pumps' 24 V is not known to exist |
| 4 | `X` is read only when the firmware loop is free (≤ 2.5 s behind `Y,INIT`); the syringe gets `/1T` without confirmation | STOP latency | `Y,STOP` after `X`; no long-blocking commands are ever sent (allow-list) |
| 5 | SY-01B `ready` bit during a stroke; whether `T` or `X` un-initialises it; `error` codes beyond the wiring page | syringe step completion | 80 % minimum time + `ready`; bookkeeping void after every stop; a too-early next command is caught by `error ≠ 0` |
| 6 | Plunger position after `Y,INIT` assumed 0 µL; `Y,INIT` pushes the content out through the current port | wrong bookkeeping, liquid in the wrong place | init only on request with a warning; never automatic |
| 7 | Which firmware build is in the Mega | cleaning times, limits | fingerprint check (two numbers only) → alarm; cleaning waits follow the observed `cleaning` flag, not the YAML |
| 8 | Speed cap after a `P` move `[INFER]` | a `pump_run` above 40 mL/min may run slower while answering `OK` | warning W1; bench check listed in the guide |
| 9 | Real dose time is longer than `steps ÷ sps`; the 10 Hz weight stream and every `?` pause stepping `[INFER]` | time limits, continuous-run volume | waits use state, limits are `× 1.5 + 10 s`; heartbeat only when no `STATE` for 2 s |
| 10 | Saved scale calibration (2026-08-26) may no longer be valid; `W,TARE` / `W,CAL` take one reading without a stability check `[FACT]` | weight doses | date shown on Maintenance › Pump and Scale; the host allows Return to zero / Correction only while the newest reading is `stable`; a weight dose is refused when no calibration was loaded |
| 11 | pyserial under the embeddable Python was proven only by import and simulator runs; no real port was opened (by rule) | first real connect | clear error texts for every open failure; `logs\pumpboard.log` |
| 12 | Windows closes the console: about 5 s for the close handler | `X` might not get out | `fast=True` path (1.5 s), guard as second line |
| 13 | Guard and a quickly restarted program may compete for the port for a few seconds | first connect delayed | the program retries; the guard gives up after 10 s |
| 14 | `motors.dose_by_weight` may return up to 5 s after STOP (V7's uncancellable settle sleep) | the round shows `Stopping` a little longer | the pump is already stopped by `X`; documented |
| 15 | HV-idle is judged from a status up to 10 s old plus the RPi's own checks; the RPi has no pump interlock (`permit_problems()` is empty and the protocol is not changed) | a discharge sent by another TCP client (example scripts) is not covered | PC-side gate on every wire command; the guide says not to use other clients while 2.0.0 runs |
| 16 | The RPi package must report 2.0.0 for the installer to push it (F.6) — outside this plan | PC 2.0.0 against an RPi still running the two-sequence 1.3.0 would still work (`plasma_run` without a choice = classic), but with 3 background frames and a 10 s start | version check at connect: `hello.flow` missing → event and a line on Maintenance › connection `RPi is an old version, please execute Install.cmd renew` |
| 17 | Maintenance closed without a PIN is a change from 1.3.0 | the owner cannot reach parameters until he runs `Set-PIN.cmd` | the page says exactly what to do; `READ-ME-FIRST.txt` |
| 18 | Tare moved behind the PIN, STOP without a confirmation dialog, single measurement as "common 1 Second-rate" | habits from 1.3.0 / the HMI | explained in the guide; each is one decision that can be reversed in one place |
| 19 | pyserial ships without a licence text in V7's `.venv` | redistribution hygiene | noted in `pc\vendor\README.txt`; the owner decides about a download |
| 20 | Motors 3–7 carry unknown liquids; no plumbing is known beyond water / OUT / CuSO₄ | example Recipes | only the plasma-only Recipe is published automatically; the others are examples to edit |

Things only the owner can decide later (kept out of 2.0.0 on purpose): Correction / Trend and blank samples (the round record already keeps the CSV, parameters, job ids and weights they would need); a hardware cut-off for the pumps; changing the Mega firmware to add a watchdog.
