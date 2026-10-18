# Plasmic Electronic control 2.0.0 — design proposal "operator UI first"

Angle of this proposal: the operator's and the maintenance person's experience, and fidelity to the original HMI v1.1.
Everything fixed in `constraints.md` is taken as given and not reopened. Where this document says "original" it means
`C:\Users\PlasmaLab\Desktop\UI\UI\hmi_v1_1`; "V7" means `C:\Users\PlasmaLab\Desktop\independent laboratoryv7`; "EC" means
`C:\Users\PlasmaLab\Desktop\autonomous control`.

Nothing in this document was run. No port was opened. Facts about the three code bases come from the code maps in
`scratchpad\understand\` and from reading the sources named in each section.

Notation: text in “…” is on-screen text, exactly as it must appear. `{x}` inside “…” is a value filled in at run time;
`{a|b}` (inside tables written `{a/b}`) means one of the alternatives. In JSON shapes `a / b` separates alternatives.

---

## 0. The design in one page

| Topic | Decision | Why |
|---|---|---|
| Frame | The original's frame, pixel for pixel: top 56 · banner 44 · nav 116 + page 908×598 · footer 70, in a 1024×768 box that is scaled as a whole to fit the window | fixed decision 9 |
| Pages | Overview · Scheduled measurement · Manual operation · Execution record · Alarm The file in · 🔧 Maintenance (Recipe · Wiring · Plasma Parameters · Pumps and Scales · Spectrum · Operator) | fixed decision 10 |
| One way to measure | Footer “Start scheduling” only. One measurement = a schedule with “common 1 times”, which is the default of the form | fixed decision 6; "No extra buttons" |
| One way to stop | Footer “Stop STOP”, always enabled, no confirmation, no PIN: ends the schedule, stops the round, sends stop-all to the pump board and `stop` to the RPi — also when nothing is running. There is no second "HSPY OFF" or "stop all" button anywhere | fixed decision 8 |
| Numbers | Every value is a tile; a tap opens the original's number pad. The pad also accepts the keyboard (digits, `.`, Backspace, Enter, Esc) | the original's touch pattern; the owner's PC has a keyboard |
| Text | Three things need text (operator name, Recipe name, RPi address). They use one dialog with a native text field, so a Chinese name can be typed with the IME. The original's on-screen QWERTY keyboard is not rebuilt (it cannot type Chinese; it would be 40 buttons for three fields) | "No extra buttons" |
| Removed from the original's frame | language switch (Traditional Chinese only), hidden 3-second long-press entry, blank-solution tile, Trend and Correction pages, flow diagram and faceplates, “Set as schedule Recipe”, the second operator picker on the schedule page | no dead buttons, no second way to do one thing |
| Removed from EC 1.3.0 | the “fast/original” choice, “⚡ Start Plasma” as an operator button (it becomes a maintenance tool, as in the original), the on-screen TCP terminal (`pc\plasmic_client.py` stays as the command-line tool) | fixed decisions 5, 6, 7 |
| PIN | The original's model: no PIN file → the whole Maintenance area is closed and the page says how to set one. Operators never sign in. Section B.9 lists every action | fixed decision 7 |
| Pump board port | Nothing is opened until a person has chosen the board once in Maintenance › connection. The choice is stored with the USB identity; afterwards the program reconnects by itself, only to that identity | fixed decision 3; and until the owner has made that choice, starting the program on this PC cannot open COM3 (development and tests are safe by default) |
| Recipe language | 19 actions + `repeat_start`/`repeat_end`; motors are `M2`–`M9`, syringe valve positions `CH1`–`CH3`; continuous pumping is `pump_on` (value = longest run time) / `delay` / `pump_off` as in the original; dosing by volume and by weight are single blocking steps | section A |
| First Recipe | “Discharge measurement only” (one line: `plasma_run`) is published automatically at first start, so the program measures on day one without the pump board. Two more are shipped as templates that maintenance must review and publish | onboarding |
| Recipe editing | CSV import as in the original, plus a step editor built from the same tiles, choice lists and number pad (one file, `maint-recipe.js`; can be dropped without touching anything else) | the owner is not a software person |
| Alarms | Computed by the program (not by the browser), written to the event log, acknowledged on the server | unattended schedules need a record |
| Web code | Plain ES modules, one module per page and per maintenance tab, no build step, no inline script or style | CSP `'self'`; several developers at once |

---

# A. Recipe language

## A.1 File format

- UTF-8 text, optional BOM, at most 200 000 bytes. First line exactly `action,target,value,unit,note`.
- One step per line. Empty lines are skipped. Every field is trimmed. `note` is free text, cut at 200 characters, never executed.
- A Recipe describes **one round**. Repetition over time belongs to the schedule.
- No variables, no branches, no conditions on sensors (as the original).
- Limits (the original's numbers): 500 source rows · 2000 steps after expansion · `repeat` nesting 5 · repeat count 1–20.
- `unit` may be left empty; it is then filled in. A wrong unit is an error.
- Names are ASCII keywords. The screen never shows the keyword alone; it shows the Chinese label of table A.2.

Targets

| Kind | Values | Source of the label on screen |
|---|---|---|
| motor | `M2` `M3` `M4` `M5` (group A) · `M6` `M7` `M8` `M9` (group B) | `motors.groups[].channels[].label` of the vendored `hardware.yaml`: “motor 2 · Water” ”Motor 3”…“motor 8 · OUT”“motor 9 · CuSO₄” |
| syringe valve position | `CH1` `CH2` `CH3` (count = `syringe_pump.valve_positions`) | “Valve position 1”“Valve position 2”“Valve position 3” — no liquid names, V7 defines none |
| direction | `forward` `reverse` | “”Forward” ”Reverse” (V7 direction `1` / `-1`) |

## A.2 Action table

"Board" = the pump board wrapper of section B.4; `fn x.y` = `backend.registry.get('x.y').invoke(ctx, payload)` on the vendored V7 code;
`TCP x` = the RPi command `x` over the existing link. "All pumps off" = no motor is between its `pump_on` and `pump_off`.

| action | Screen name | target | value (range, step) | unit | What it does | Maps to | Finished when | Time limit | While paused / on pause request |
|---|---|---|---|---|---|---|---|---|---|
| `delay` | Waiting | — | 0–3600, 0.1 | `s` | waits | runner only | time elapsed (paused time does not count) | — | waits; the rest continues after continue |
| `pump_flow` | Set flow rate | motor | 1–`max_flow_ml_min` (80), 0.1 | `mL/min` | flow used by later `pump_on` of this motor | runner memory | at once | — | — |
| `pump_direction` | Set direction | motor | `forward` / `reverse` in the **value** column | — | direction used by later `pump_on` and `dose_volume` of this motor; default `forward` | runner memory | at once | — | — |
| `pump_on` | Start pump | motor | 1–300, 0.1 = **longest** run time | `s` | starts continuous running at the stored flow and direction; the Board stops it by itself when the time is up | `fn motors.run {motor, flow_ml_min, direction}` | command accepted and a `?` shows this motor selected with state `wait` or `run` | 5 s | on pause: stopped, used time remembered; on continue: started again with the remaining time (at least 1 s) |
| `pump_off` | stop pump | motor | — | — | stops that motor | `fn motors.stop {motor}` | group state `idle`; for `M2` / `M9` also the linked valve closed | 8 s | allowed |
| `dose_volume` | Quantitative (volume) | motor | 0.01–1000, 0.01 | `mL` | delivers a volume by step count at V7's fixed dose speed (`dose_flow_ml_min` 40) | `fn motors.dose {motor, volume_ml, direction}` | after the call returns: a `?` shows the group `idle` (for `M2` / `M9` also the valve closed) | `eta_s × 1.5 + 15 s` | the dose in progress finishes first |
| `dose_weight` | Quantitative (weight) | motor | 0.01–1000, 0.01 | `g` | V7's closed loop on the scale, forward only | `fn motors.dose_by_weight {motor, target_g, direction: 1}` (blocks) | the function returns | 420 s (V7's own limit is 300 s plus settling) | finishes first |
| `clean` | automatic cleaning | — | — | — | the firmware's rinse: motor 2 in, motor 8 out, 30 / 45 / 45 s | `fn motors.clean {}` | state `cleaning` was true and is false again | `total_pump_timeline_s + 21 s` (141 s) | finishes first |
| `syringe_init` | syringe pump initialization | — | — | — | homes the plunger; software estimate of the content becomes 0 | `fn pump.init {}` then `Y,ST` polling | `PUMP,…ready=1,error=0` | 90 s | finishes first |
| `syringe_speed` | Syringe pump speed | — | 0.001–max of the syringe (2.5 for 5 mL, 0.625 for 1.25 mL), 0.001 | `mL/s` | plunger speed for later moves | `fn pump.set_speed {speed_ml_s}` | reply without error code | 5 s | — |
| `syringe_aspirate` | syringe pump for pumping liquid | `CH1`–`CH3` | 0.001–syringe size, 0.001 | `mL` | turns the head valve to the position, then draws the volume | `fn pump.set_valve {position}` → wait ready → `fn pump.aspirate {volume_ul}` | computed move time elapsed **and** `Y,ST` says `ready=1,error=0` | valve 5 s; move `volume ÷ speed + 15 s` | finishes first |
| `syringe_dispense` | syringe pump injection | `CH1`–`CH3` | same | `mL` | as above, pushes out | `fn pump.set_valve` → `fn pump.dispense {volume_ul}` | same | same | finishes first |
| `scale_read` | Record weight | — | — | — | writes the weight (median of 5 readings) into the round record; `note` is kept as its name | Board weight cache | 5 new readings | 5 s | — |
| `rpi_status` | Check high voltage status | — | — | — | fresh read of HSPY and Nano; fails if HSPY is ON, D13 is HIGH or a read-back is invalid | `TCP status {fresh: true}` | reply | 40 s | finishes first |
| `nano_prepare` | synchronous Nano parameter | — | — | — | writes `parameter.json` into the Nano, never arms | `TCP prepare` | reply | 60 s | finishes first |
| `plasma_run` | discharge＋capture spectrum | — | — | — | the unified flow: 1 background frame, then discharge + signal frames | interlock check (B.6), then `TCP plasma_run {confirm_high_voltage: true}`, wait for the job, `TCP result {frames: true}`, save | job state `done` and the result stored on this PC | accept 40 s · job 360 s · result 2 tries of 60 s | finishes first (never interrupted by pause; STOP cancels it) |
| `spectrum_capture` | capture spectrum (no discharge) | — | empty = the integration time of plasma parameters; or 1–10000, 1 | `ms` | 3 frames without high voltage, saved as a run | `TCP spectrum {integration_ms}`, wait, `TCP result` | job `done` and stored | accept 40 s · job 120 s | finishes first |
| `power_off` | Turn off high voltage | — | — | — | HSPY OFF + Nano STOP with read-back | `TCP stop` (fresh connection) | reply `ok` | 120 s | allowed |
| `nano_stop` | Turn off high voltage | — | — | — | same as `power_off`; kept so CSV files of the original load. The step editor offers only `power_off` | `TCP stop` | reply `ok` | 120 s | allowed |
| `repeat_start` | Repeat start | — | 1–20, integer | — | repeats the block up to the matching `repeat_end` | expanded before anything runs | — | — | — |
| `repeat_end` | Repeat end | — | — | — | | | | | |

Not Recipe actions, on purpose: scale tare and calibration (they rewrite the calibration file; maintenance only), syringe size,
`motors.select`, `motors.stop_all`, anything about valves (V7 has no valve command; valves follow motors 2 and 9), plasma parameters
(they live on the RPi and are edited in Maintenance › plasma parameters, as in the original).

**On failure of any step** (error reply, time limit, lost link, board reset, interlock): the step is marked failed with the reason, the
round ends through the common exit of B.5.4 (outputs off, read back), the round is `failed`, and the schedule ends (fixed decision 8).
Nothing is retried, with one exception taken from the original: reading the result of a **finished** discharge is tried twice.
A `dose_weight` that ends outside V7's tolerance is **not** a failure (V7 returns normally): the step is Complete, the numbers go into the
record, and alarm `dose_tolerance` (priority middle) is raised.

Display rounds (as the original): only the outermost `repeat_start` counts as a displayed round; steps outside any repeat are round 1 of 1.

## A.3 Preflight (checked when a Recipe is validated, published, previewed and again at every start; no hardware involved)

| # | Rule | Message (row number filled in) |
|---|---|---|
| P1 | header, size, row, step, nesting and repeat limits of A.1 | “CSV field must be action,target,value,unit,note” · “CSV must be 200 KB Within UTF-8 text” · “CSV most 500 column” · ”Maximum after expansion 2000 actions” · “repeat Most nested 5 layer” · ”No. {n} List repeat_start of value Must be 1–20 times” · ”No. Unable to start measurement {n} List repeat_end has no corresponding repeat_start” · “repeat_start Lack repeat_end” · “CSV No executable action” |
| P2 | known action; target, value, unit as in A.2 | “No. {n} Column does not support command {action}” · “No. {n} List {Screen name} The object must be {allowed values}” · “No. {n} List {Screen name} must be between In the method of {min}–{max} {unit}” · “No. {n} List {Screen name} is The structure and appearance of {step}” · “No. {n} List {Screen name} Leave the object blank/numerical value” |
| P3 | `pump_on` needs an earlier `pump_flow` for the same motor | “No. {n} List {motor} The flow rate must be set before starting (pump_flow) ” |
| P4 | `pump_flow` / `pump_direction` of a motor only while that motor is off | “No. {n} List {motor} The flow rate or direction cannot be changed during operation” |
| P5 | `pump_on` of a motor that is already on; `pump_off` of a motor that is not on | “No. {n} List {motor} is already running” · ”No. {n} List {motor} has not been started yet” |
| P6 | one motor per driver group: `pump_on` while another motor of the same group is on | “No. {n} List {A/B} group can only have one motor running at the same time:{motor} It hasn’t stopped yet” |
| P7 | while any pump is on, only `delay`, `pump_off`, `pump_on` (other group), `pump_flow` / `pump_direction` (other motors) are allowed. Every other step — dosing, cleaning, syringe, `scale_read`, all RPi steps — needs all pumps off | “No. {n} Column execution {Screen name} ” |
| P8 | the delays between a `pump_on` and its `pump_off` may not exceed that `pump_on`'s value | “No. {n} Column wait will exceed {motor} The maximum operating time set” |
| P9 | every pump is off at the end | “There is still no discharge from the pump at the end of the process. pump_off: {motor…}” |
| P10 | syringe: the first syringe step is `syringe_init`; `syringe_speed` comes before the first move; the tracked content (0 after init, + aspirate, − dispense) stays within 0…syringe size; it is 0 at the end | “No. {n} column must be initialized before using the syringe pump (syringe_init) ” · “No. {n} column aspiration/The syringe pump speed must be set before injecting (syringe_speed) ” · “No. {n} The syringe capacity will be exceeded after liquid extraction. {x} mL” · “No. {n} The amount of liquid injected is more than the amount in the syringe.” · ”There is still more liquid in the syringe at the end of the process. {x} mL,Please drain” |
| P11 | `dose_weight` needs `motors.gravimetric_dose.enabled` in `hardware.yaml` | “hardware.yaml Weighing and quantification is not enabled” |
| P12 | estimated time of one round (table A.4) at most 7200 s | “Estimated time for one round {t} exceeds 2 Hour” |

Rules P6–P7 together give fixed decision 8: at `plasma_run` no pump is on, no dose or cleaning is in progress and the syringe is not
moving, because every one of those steps blocks until it has finished. The run-time check of B.6 verifies it on the real state.

Result of a successful preflight:
`{step_count, rounds, phases[], uses_plasma, uses_rpi, uses_board, uses_scale, uses_syringe, estimate_seconds, delay_seconds, warnings[]}`.

Warnings (shown, never blocking): “this Recipe has no measurement steps (plasma_run) ” · “Recipe executed every time {n} wheel; it is recommended to use a single wheel for scheduling Recipe” ·
“motor 2/9 has linkage solenoid valve: after starting 1 It starts turning in seconds,After stopping 1 seconds to close the valve” (only when `M2` / `M9` is used with `pump_on`).

Display phases (never change what is sent): each step gets a class; neighbouring steps of one class form one phase.

| Class | Steps |
|---|---|
| “Liquid delivery” | `pump_on` `pump_off` `dose_volume` `dose_weight` `syringe_init` `syringe_aspirate` `syringe_dispense` `scale_read` |
| “Cleaning | `clean` |
| “Measurement | `rpi_status` `nano_prepare` `plasma_run` `spectrum_capture` |
| “Turn off high voltage” | `power_off` `nano_stop` |
| takes the class of the **next** device step (“Prepare” if none follows) | `pump_flow` `pump_direction` `syringe_speed` |
| takes the class of the **previous** step (“Waiting” if it is the first) | `delay` |

## A.4 Estimated time per step (for the schedule preview; seconds)

| Step | Estimate |
|---|---|
| `delay` | its value |
| `pump_on` / `pump_off` | 1 (+1 for `M2` / `M9`) |
| `dose_volume` | `volume × dose_pulses_per_ml ÷ round(dose_flow × pulses_per_ml ÷ 60)` + 1 (+2 for `M2` / `M9`) — 10 mL ≈ 17 s |
| `dose_weight` | `target_g ÷ (fast_flow_ml_min ÷ 60)` + 25 — 10 g ≈ 40 s |
| `clean` | `water_lead_s + overlap_s + out_tail_s` + 3 = 123 |
| `syringe_init` | 6 · `syringe_speed` 1 · `syringe_aspirate` / `syringe_dispense` `volume ÷ speed + 4` |
| `scale_read` | 1 |
| `rpi_status` 3 · `nano_prepare` 6 · `power_off` / `nano_stop` 5 | |
| `plasma_run` | `status.estimate_s` of the RPi (it already depends on the parameters); 30 when the RPi is not connected |
| `spectrum_capture` | `3 × integration_ms ÷ 1000 + 8` |

The numbers for the pump board come from the vendored `hardware.yaml`, not from constants in the program.

## A.5 Example Recipes (shipped in `pc\recipes\`)

Files `plasma-only.csv`, `dose-and-measure.csv`, `clean.csv`; their screen names are in `recipestore.TEMPLATES`
(“Discharge measurement only”, “Measurement after adding liquid (example)”, “Cleaning (example)”).

**1. Only discharge measurement** — works without the pump board. Published automatically at first start.

```csv
action,target,value,unit,note
plasma_run,,,,discharge＋capture spectrum (background 1 sheets, the number of signal sheets depends on the number of rounds of plasma parameters)
```

One line on purpose. `plasma_run` makes its own checks on the RPi and re-syncs the Nano only when needed, so the discharge starts about
`status.start_s` (≈ 4 s) after the slot is due. Adding `rpi_status` or `nano_prepare` in front would break the owner's "not more than 5 seconds".

**2. Measurement after adding liquid (example)** — template; maintenance must check the volumes against the real tubing, then publish. Uses only the three motors V7 names.

```csv
action,target,value,unit,note
dose_volume,M9,5,mL,Example: Add CuSO₄ 5 mL.Please modify the value according to the on-site pipeline.
dose_volume,M2,20,mL,Example: Add water 20 mL
delay,,10,s,and other liquid levels are stable
plasma_run,,,,discharge＋capture spectrum
pump_flow,M8,40,mL/min,Flow rate for discharge
pump_on,M8,90,s,Most transfers 90 Second
delay,,60,s,discharge 60 Second
pump_off,M8,,,
```

**3. Cleaning (example)** — template.

```csv
action,target,value,unit,note
repeat_start,,2,,Wash twice
clean,,,,Automatic cleaning: motor 2 water inlet, motor 8 discharge, approx. 2 minutes
delay,,5,s,
repeat_end,,,,
```

Preflight results by hand: (1) 1 step, `uses_plasma`, no board; (2) 8 steps, `uses_plasma`, `uses_board`, estimate ≈ 11 + 35 + 10 + Measure + 62
≈ 120 s plus the measurement; (3) 4 steps in 2 rounds, `uses_board`, estimate (123 + 5) × 2 = 256 s, warning “Recipe executed every time 2 wheel…”.


---

# B. PC program structure

## B.1 Modules (all under `pc\`; standard library only, plus the vendored code of section F)

`pc\app.py` shrinks to the composition root; what it holds today moves into modules so that several people can work at once.
`app.py` re-exports `Application`, `ExclusiveHTTPServer`, `make_handler`, `PinLock`, `VERSION` so existing imports (`import app as pc`) keep working.

| File | Contents | Depends on |
|---|---|---|
| `app.py` | `VERSION = '2.0.0'`; `Application` (creates and closes everything, `state()`, `stop_everything()`, 1 Hz `ticker` thread); `main()` (arguments `--demo --port --data --no-browser --set-pin`, console control handler, browser) | everything below |
| `httpd.py` | `ExclusiveHTTPServer`, `make_handler(app, csrf)`, route table, static files, security headers, error mapping | `app` |
| `store.py` | `Store`: `config.json`, `events.jsonl`, `runs\`, `rounds\`, `schedules\`; `write_json` (atomic); `now_iso` | — |
| `migrate.py` | `migrate(data_dir)`: 1.3.0 → 2.0.0 (section D.3) | `store`, `recipestore`, `operators` |
| `pinlock.py` | `PinLock`, `PracticePin` | — |
| `rpilink.py` | `Link` — today's class moved unchanged, minus `SEQUENCES` / `sequence_fields`, plus `claim(job_id)` and `fetch_result(job_id)` | `plasmic_client`, `netcheck`, `store` |
| `pumpboard.py` | `PumpBoard`, `BoardError`, `load_v7()` (section B.4) | vendored `backend`, `serial`, `yaml` |
| `recipe.py` | `ACTIONS` table, `parse(text)`, `preflight(steps, board_facts)`, `estimate(steps, facts, rpi_estimate)`, `to_csv(rows)` — pure functions, no I/O | — |
| `recipestore.py` | `RecipeStore`: published Recipes, templates, first-start seed | `recipe`, `store` |
| `interlock.py` | pure functions: `hv_idle_problems`, `board_idle_problems`, `start_problems`, `manual_problems`, `plasma_problems`, `rewrite_fix` (section B.6) | — |
| `runner.py` | `RoundRunner` (section B.5) | `recipe`, `interlock`, `pumpboard`, `rpilink`, `store` |
| `schedule.py` | time arithmetic, unchanged except: `ACTIVE` gains `'paused'`; labels as section E.9 | — |
| `schedrun.py` | `ScheduleRunner` — EC's `Schedule` class, a slot now runs one round (section B.7) | `schedule`, `runner`, `recipestore` |
| `manual.py` | `Manual`: single-device operations from manual operation and the maintenance tools, with interlocks and the screen watchdog | `interlock`, `pumpboard`, `rpilink` |
| `operators.py` | `Operators`: list file and the current operator | `store` |
| `alarms.py` | `AlarmBook` (section B.8) | — |
| `netcheck.py`, `plasmic_client.py` | unchanged | — |

## B.2 Threads

| Thread | Owner | Lifetime | Does |
|---|---|---|---|
| main | `app.main` | program | `serve_forever()` |
| one per HTTP request (daemon) | `ExclusiveHTTPServer` | request | handlers; a request never waits for a long device operation — those are handed to a `manual-op` worker and the request returns at once |
| `rpi-link` (daemon) | `Link` | program | connect, reconnect, `status` once a second, save unclaimed results (today's `_loop`) |
| `board-monitor` (daemon) | `PumpBoard` | program | connect and reconnect, 1 Hz `?`, run-time limits, board reset detection, weight stream upkeep |
| `serial-reader`, `sim` (daemon) | vendored `transport.Link` / `SimulatedMega` | per connection | V7's own |
| `round-<id>` (daemon) | `RoundRunner` | one round | executes the steps top to bottom |
| `schedule` (daemon) | `ScheduleRunner` | one schedule | waits for slots, starts rounds, joins them |
| `manual-op` (daemon) | `Manual` | one blocking manual operation (dose, clean, syringe move, maintenance plasma) | so the HTTP request returns at once and STOP stays responsive |
| `ticker` (daemon) | `Application` | program | once a second: `AlarmBook.evaluate`, screen watchdog for manual work |
| child process | `Application` (practice mode) | program | `plasmic_tcpd.py --sim` on 127.0.0.1:8767, as today |

## B.3 Locks and ownership

| Lock | Guards | Rule |
|---|---|---|
| `Store.lock` | events, run rows, config dict | never held while calling a device |
| `PinLock.lock` | sessions, lock-out counters | |
| `Link.request_lock` / `Link.lock` | the TCP connection / link fields | unchanged |
| `PumpBoard.lock` | board fields: status, link object, settings, deadlines, syringe estimate, epoch | short sections only |
| `PumpBoard.op_lock` | one device **operation** at a time on the board (held for the whole of a blocking operation) | `stop_all()` never takes it |
| vendored `Link._tx_lock` | one serial command at a time | V7's own |
| `RoundRunner.lock` | round record | |
| `ScheduleRunner.lock` | schedule record | |
| `AlarmBook.lock` | alarm table | |
| `Application.owner_lock` | who owns the equipment: `None`, `'round'`, `'manual'` | see below |

Never hold two of these at once, with one allowed order where unavoidable: schedule → round → board → store.

**Ownership of the equipment** (one owner, as the original's "one owner for PLC, RPi and workflows"):

| Owner | Taken | Released | Effect |
|---|---|---|---|
| `'round'` | when a round starts | when its common exit has finished — also kept while the round is paused | manual starts refused; OFF and STOP work |
| `'manual'` | when a manual operation starts | when it has finished and no manual pump is running | a round cannot start (`not_started` slot); STOP works |
| schedule `waiting` / `stopping` | — | — | manual starts refused although no round runs (as the original); while the schedule is `paused` **between** rounds the equipment is free |

## B.4 Pump board wrapper — `pc\pumpboard.py`

The only file that imports the vendored V7 modules. V7's files are never edited (the sync tool of F.3 checks it).

### B.4.1 Loading V7 once per process — `load_v7(data_dir, simulate, speedup=1)`

Called lazily, under a module lock, exactly once:

1. `os.environ['LAB_CONFIG'] = <pc\vendor\v7\config\hardware.yaml>` · `os.environ['SCALE_CALIBRATION_FILE'] = <data_dir>\pumpboard\scale_calibration.json`
   (copied there from `pc\vendor\v7\config\scale_calibration.json` when it does not exist yet, so the vendored file never changes).
2. `sys.path[:0] = [pc\vendor\pylib, pc\vendor\v7]`.
3. `import backend.config, backend.registry, backend.transport, backend.functions` — always under the name `backend` (a second import under another name would register the 26 functions twice and V7 raises `Duplicate registration of function name End by itself in`).
4. `cfg = backend.config.get_config()` — this **is** V7's cached dict (read in `config.py:234-239`), so changes made here are what every later `transport.Link()` sees.
   Immediately: `cfg['link']['port'] = 'PLASMIC-NOT-CHOSEN'`. V7's `auto` (first port whose name contains "ch340", else the first port) can therefore never run, even by accident.
5. `cfg['link']['simulate'] = simulate`; with `simulate`: `cfg['link']['simulation_speedup'] = speedup`, and `backend.transport.serial = _Forbidden()` / `backend.transport.list_ports = None`,
   where `_Forbidden.Serial(...)` raises `BoardError('forbidden_port', …)`.
6. The same replacement is made when the environment variable `PLASMIC_NO_SERIAL=1` is set, whatever `simulate` says. Tests set it before importing anything.
7. `transport.get_link()` (V7's process-wide singleton) is never called. The wrapper creates `transport.Link()` itself, a new object for every connection, because V7 fixes `simulated` in the constructor.
8. One process is either real or simulated for its whole life: a second `load_v7` with the other mode raises `RuntimeError`.

V7 functions are called as `backend.registry.get(name).invoke(backend.registry.Context(link, cfg), payload)`.
`registry.FunctionError` → `BoardError('refused', text)`; `transport.LinkError` → `BoardError('link', text)`; V7's Chinese message is passed on unchanged.

### B.4.2 Settings — `pc\data\pumpboard.json` (shape in D.2)

`enabled` is `null` (never chosen: nothing is opened), `true` (use the board with the stored USB identity) or `false` (“does not use the pump control board”).

### B.4.3 Choosing and opening the port

`ports()` — enumeration only, opens nothing: `serial.tools.list_ports.comports()` →
`[{device, description, vid, pid, serial_number, is_mega, chosen}]`, `is_mega` = (VID, PID) ∈ {(2341, 0042), (2A03, 0042)}. In practice mode: one fixed row `{device: 'simulator', is_mega: true}`.

`configure(enabled, device)` (from Maintenance › connection, PIN): stores the device **and its VID, PID and serial number**, then connects.

Connecting (thread `board-monitor`; every 3 s while `enabled` and not connected):

| Step | Action | If it fails |
|---|---|---|
| 1 | Find the port: the stored name if a port with that name and the stored VID:PID(+serial) exists; else, if exactly one port has the stored identity, that one (its new name is saved, event “The connection port of the pump control board is from {old} becomes {new}”) | status `missing`, text “Pump control board not found: Check USB line” — enumeration is repeated, nothing is opened |
| 2 | `cfg['link']['port'] = name`; `link = transport.Link()`; `link.start()` (V7 opens at 115200, waits `boot_delay_s` 2.5 s, starts its reader, sends `?`) | "access denied" / "PermissionError" → status `busy_port`, text “{port} is occupied by other programs: please close the independent laboratory v7 (run.bat) or Arduino IDE Serial port monitoring”; anything else → `missing` with the system's message |
| 3 | Identity: `lines = link.send('?')` must contain a line that starts with `STATE,` and carries `a=`, `sa=`, `b=`, `sb=`, `valves=`, `cleaning=`; **and** either `link.state['firmware']` starts with `READY,arduino+pump` or the line carries `cal_pulses_per_ml=` | `link.stop()`; status `wrong_device`, text “{port} is not a pump control board (did not receive READY/STATE response)”; **no automatic retry** until “Reconnect” or a new choice — a foreign device must not be reset every 3 seconds |
| 4 | Fingerprint: `cal_pulses_per_ml` (2 decimals) and `max_flow_uL_min` of that line against `motors.pulses_per_ml` and `motors.max_flow_ml_min × 1000` | not a failure: `config_match = false`, alarm `fw_mismatch` (middle) |
| 5 | Known state: `X` (result ignored). Then what V7's `main.py` does at start: `scale.restore_calibration(link)`, `W,AVG,<n>`, `W,STABLE,<g>`, `W,STREAM,<hz>`, `?`, `link.weight_hooks.append(scale.record)`; then `fn pump.set_syringe` when the stored size is not the firmware default | a failed scale step is only noted (V7 does the same) |
| 6 | `epoch += 1`; syringe estimate reset (not initialised, content unknown, speed unknown); status `ok`; event “pump control board is wired ({port}) ” | |

Practice mode and tests: steps 1–2 are replaced by `link = transport.Link(); link.start()` with `simulate` set; steps 3–6 are the same (V7's simulator prints the `READY` line). Status `simulated`.

Guards against opening a real port, in the order they act: `enabled` is not `true` → no attempt; `simulate` or `PLASMIC_NO_SERIAL` → `_Forbidden`; no port with the stored identity → no attempt.

### B.4.4 While connected (`board-monitor`, tick 0.1 s)

| Duty | How |
|---|---|
| Liveness and fresh state | `link.refresh_state()` once a second (skipped while an `X` is being sent). Three failures in a row, or `link.connected` false (V7's reader ended) → status `lost` |
| Lost link | `link.stop()`; alarm `board_lost` — priority Emergency when a motor was running, cleaning or a dose was in progress, else high; reconnect every 3 s. **Reopening the port is the only way left to stop a pump** (it resets the Mega if the board auto-resets, and step 5 sends `X`), so reconnection is not delayed |
| Board reset | a subscriber queue (`link.subscribe()`) is read; an `rx` log line starting with `READY` after the first connect means the Mega restarted: `epoch += 1`, step 5 is repeated, alarm `board_reset` (high, latched). Every operation in progress fails with `BoardError('device', 'Pump control board restarts')` |
| Longest run time | each continuous start has a deadline (monotonic clock). At the deadline: `fn motors.stop {motor}`; if the group is not `idle` 2 s later: `stop_all()`; if that fails: alarm `pump_stop_unconfirmed` (Emergency) |
| Weight stream | V7's `X` switches the stream off. 1 s after any stop-all, and every 10 s while the board is `ok` and `scale.stream_hz` is 0: `fn scale.restart`. No “Restart reading” button is needed |

### B.4.5 Operations

All raise `BoardError(code, text)`. Blocking ones take `op_lock` for their whole duration (non-blocking acquire → `BoardError('busy', 'The pump control board is performing another action')`) and check the caller's `cancel` event and the board's `abort` event every 0.1 s.

| Method | V7 calls | Extra checks before | Finished when | Notes |
|---|---|---|---|---|
| `run(motor, flow, direction, max_seconds, owner)` | `motors.run` | status `ok`/`simulated`; group of `motor` is `idle` (never replace a running motor silently); not cleaning; no weight dose | `?` shows `selected == motor` and state `wait`/`run` | registers the deadline |
| `stop_motor(motor)` | `motors.stop` | — (always allowed) | group `idle`, linked valve closed (≤ 8 s) | cancels a weight dose of that motor (V7 does); falls back to `stop_all()` when not confirmed |
| `dose_volume(motor, ml, direction, cancel)` | `motors.dose` | both groups idle; not cleaning | poll `?` every 0.25 s: group `idle` and linked valve closed | returns `{steps, sps, eta_s, elapsed_s}`; time limit `eta_s × 1.5 + 15` → `stop_all()` and `BoardError('timeout', …)` |
| `dose_weight(motor, grams, cancel)` | `motors.dose_by_weight` | both groups idle; not cleaning; scale online (a weight within 3 s) | the call returns | returns V7's result (`dispensed_g`, `error_g`, `within_tolerance`, `topups`, `elapsed_s`, `warning`); a watcher calls `stop_all()` on `cancel` or after 420 s |
| `clean(cancel)` | `motors.clean` | both groups idle | `cleaning` true then false | abort only through `stop_all()` |
| `syringe_init(cancel)` | `pump.init`, then `Y,ST` | — | `PUMP,` line with `ready=1,error=0` (poll 0.5 s, ≤ 30 s) | estimate: initialised, content 0 |
| `syringe_speed(ml_s)` | `pump.set_speed` | ≤ max of the syringe | reply | remembered for move times |
| `syringe_move(kind, position, ml, cancel)` | `pump.set_valve`, `Y,ST`, `pump.aspirate` / `pump.dispense`, `Y,ST` | initialised; speed known; content stays within 0…size | wait `ml ÷ speed + 0.5 s`, then `Y,ST` until `ready=1` (≤ 15 s) | any `PUMP,…,error=<n>` with n ≠ 0 → `BoardError('device', 'Syringe pump report error {n} ({illustrate}) ')`; V7 itself ignores that code |
| `scale_read(samples=5)` | — | scale online | 5 new sequence numbers | median, grams |
| `tare()`, `calibrate(grams)`, `set_syringe(ul)`, `detect_syringe()` | `scale.tare`, `scale.calibrate`, `pump.set_syringe`, `pump.detect` | both groups idle, no operation | reply | maintenance tools |
| `suspend(owner)` / `restore(list)` | `motors.stop` / `motors.run` | — | as `stop_motor` / `run` | for pause: returns the motors with flow, direction and remaining seconds |
| `stop_all(reason)` | `motors.stop_all` (cancels a weight dose, `X`, `W,STREAM,0`, `?`); if that raises, one direct `link.send('X', timeout=3)` | none; does not take `op_lock` | groups `idle`, `cleaning` false, valves closed within 1.5 s | sets `abort`, clears all deadlines, marks the syringe content unknown when it was moving; returns `{ok, error}`; not connected → `{ok: false, error: 'The pump control board is not connected and the stop command cannot be sent.'}` |
| `idle_problems(max_age=1.0)` | `?` when the state is older than `max_age` | — | — | list of reasons, empty when both groups are idle, not cleaning, no weight dose, no syringe move, valves closed |
| `close()` | `stop_all` when connected, then `link.stop()` | | | program exit |

Error codes of the syringe pump shown in Chinese (from V7's wiring document, unverified on the pump): 2 / 3 “Parameter out of range”, 7 “has not been initialized yet”, 9 “Piston overload”, 10 “Valve overload”, others “error code {n}”.

## B.5 Round runner — `pc\runner.py`

### B.5.1 Start — `RoundRunner.start(recipe, operator, slot, hv_confirmed)`

Returns the round id or raises `RuntimeError(reason)`; the schedule turns the reason into a `not_started` slot.

1. Parse and preflight the stored CSV again (a Recipe valid at publish time may be invalid for another syringe size).
2. `interlock.start_problems(...)` (B.6) must be empty.
3. `uses_plasma` → `hv_confirmed` must be true: “Processes containing high-voltage plasma must first confirm the high-voltage prompt.”.
4. Take ownership `'round'`; write `rounds\ROUND-<id>.json` with status `running`; start the thread.

### B.5.2 States

| State | picture | Enters | Leaves |
|---|---|---|---|
| `running` | is executing | start; resume | pause request, stop, end, failure |
| `pausing` | Paused | “pause PAUSE” while a step is in progress that cannot be interrupted | when that step ends → `paused`; continue → `running`; stop → `stopping` |
| `paused` | Suspended | at the next step boundary or inside a `delay`; continuous pumps of the round are suspended first | continue → `running` (pumps restored); stop → `stopping` |
| `stopping` | Stopping | STOP | common exit → `stopped` |
| `completed` | Complete | last step done, common exit clean | — |
| `failed` | failed | a step failed, or the common exit could not confirm a stop | — |
| `stopped` | Stopped | STOP | — |
| `interrupted` | Interrupted | only in records: the program ended while the round was `running` | — |

A stopped or failed round is never continued; the next round starts at step 1.

### B.5.3 Executing a step

Before every step and every 0.2 s inside waits: stop requested → leave; pause requested → pause point; `board.epoch` changed since the round started and the round uses the board → fail “Pump control board restarts, process stops”.
Each step record gets `started_at`, `finished_at`, `status` (`done` / `failed` / `skipped` for steps never reached) and `result` (e.g. `{dispensed_g, error_g}`, `{job, peak_nm}`, `{grams}`).
The line shown on Overview while a step runs (`round.current.detail`):

| Step | Text |
|---|---|
| `delay` | “Waiting {Passed}/{total length} Second” |
| `pump_on` | “{motor} starts,{flow} mL/min {Forward rotation/reverse},most {n} Second” |
| `dose_volume` | “{motor} Quantitative {x} mL · already {t} Second/about {eta} Second” |
| `dose_weight` | “{motor} Weighing Quantification · at present +{gain} g/Target {x} g” |
| `clean` | “automatic cleaning · {Water ingress/Water inlet and drain/finally empty into the air} · already {t} Second/about 121 Second” |
| `syringe_*` | “syringe pump {Liquid extraction/Liquid injection} Valve position {n} · {x} mL” / “Syringe pump initializing” |
| `plasma_run` | RPi phase: “Prepare” ”Capture background spectrum (high voltage has not been turned on yet)” ”Boost” ”Discharging · Signal {done}/{total} Zhang” ”Turn off the high voltage and confirm” ”Read the spectrum” |
| `spectrum_capture` | “capture spectrum (no discharge)” |
| others | the Screen name of A.2 |

`plasma_run` in detail: `interlock.plasma_problems()` must be empty (else the step fails with the first reason) → `link.command('plasma_run', attended=False, confirm_high_voltage=True)` → `link.claim(job)` so the link thread does not fetch the same result → wait on `link.latest()` until the job leaves `running` (limit 360 s; link lost for 30 s → failed “and RPi ’s connection is interrupted and the results of this measurement cannot be confirmed; please go to the site to confirm the equipment.” For) → state `done`: `link.fetch_result(job)` (second try after 2 s; never a second discharge) → `Store.save_run` with `round_id`, `recipe_name`, `operator`, `slot` in the meta.

### B.5.4 Common exit (every way a round ends)

1. A job of this round still `running` on the RPi → `link.stop()` on a fresh connection; wait until it leaves `running` (≤ 90 s).
2. Round `uses_plasma` and did not complete → one more `link.stop()` while idle (the RPi then runs its stop tool and reads OFF back). Not confirmed → `stop_errors`, alarm `hv_off_unconfirmed` (Emergency).
3. Round `uses_board`: completed → `board.idle_problems()`; anything listed → `board.stop_all()`. Failed or stopped → `board.stop_all()` always. Not confirmed → `stop_errors`, alarm `pump_stop_unconfirmed` (Emergency), status forced to `failed`.
4. Remaining steps get `skipped`. Record written. Ownership released. Event “Process{Complete|failed|Stopped}: {Recipe} (No. {n}/{N} times){reason}”.

## B.6 Interlocks — `pc\interlock.py`

Pure functions over the latest views; each returns `[{reason, fix}]`. Shown wherever a control is disabled, and re-checked on the server when the request arrives.

| Function | Used for | Rows (first matching rows are returned; texts in Chinese as shown) |
|---|---|---|
| `hv_idle_problems(rpi)` | pumps and syringe may move only when high voltage is off | not connected → **no row**, but a warning: “RPi Not connected: The program cannot confirm the high voltage status, please go to the site to confirm. HSPY has been closed” · status older than 20 s “RPi Status exceeds 20 No update in seconds” · “HSPY Power readback is invalid” · “HSPY power supply is ON” · “Nano readback is invalid” · “Nano status is {state} (is ready or being exported)” · “Nano fault lock {fault}” · “Nano D13 pulse output is not LOW” · a job is running on the RPi “RPi is executing work” |
| `board_idle_problems(board)` | high voltage may start only when nothing moves on the board | `enabled` is `null` or `false` → no row (there is no board) · enabled and not connected “The pump control board has been set but not connected, and the pump cannot be confirmed to have stopped.” · “{motor} Still there{operation/Quantitative}” · “Automatic cleaning in progress” · ”Weighing and quantification in progress ({motor}) ” · “Syringe pump in action” · ”Solenoid valve {n} is not closed yet” · state older than 2 s and refresh failed “Pump control board does not respond” |
| `plasma_problems(rpi, board, owner)` | right before every `plasma_run` (round and maintenance tool) | `board_idle_problems` + the RPi's own `status.ready.problems` when `ready.plasma` is false |
| `manual_problems(rpi, board, owner, schedule, round)` | every manual start | in this order: “Pump control board has not been set yet”/“is set to not be used” · ”The pump control board is not connected ({text}) ” · “The schedule is in progress: manual start and deactivation; if manual operation is required, please pause the schedule first” · ”The process is paused: the pump is still retained by the process, please press ”Continue” or ”Stop” at the bottom” · ”The process is being executed: the pump is controlled by the process, manual start and deactivation” When · `hv_idle_problems` |
| `start_problems(recipe_facts, rpi, board, owner)` | start of every round; also the schedule preview (`blockers`) | another round “The process is already being executed” · manual work “Manual operation is in progress, please stop first” · `uses_rpi`: “RPi not connected”; `uses_plasma`: `ready.problems`, else `hv_idle_problems` · `uses_board`: board not `ok` “Pump control board is not connected ({text}) ”, then `board_idle_problems` · `uses_scale`: “Weight module did not respond” · not `uses_board`: `board_idle_problems` (a manual pump must not run during a discharge) · not `uses_rpi` and RPi connected: `hv_idle_problems` |
| `rewrite_fix(text)` | the RPi's `fix` texts still name buttons of the old HMI; the RPi side is not changed | “Click ”Read Status”→“Click ”Reread Status”” · ”Click ”HSPY OFF＋Nano STOP””→“Press ”Stop” at the bottom STOP”” · “Click Clear Nano fault”, then click ”Synchronize Nano parameter””→“Ask maintenance personnel to go to ”Repair › Plasma Parameters” click ”Clear” Nano fault”, then click ”Synchronize Nano Parameters”” · ”In ”Plasma Parameters””→“Ask maintenance personnel to go to ”Repair › Plasma Parameters” |

## B.7 Schedule — `pc\schedrun.py` on top of `pc\schedule.py`

EC's `Schedule` class is kept with these changes. `pc\schedule.py` keeps all its arithmetic (`validate`, `slot_time`, `last_index`, `planned_total`, `next_due_index`, `MINUTE` for tests).

| Topic | 1.3.0 | 2.0.0 |
|---|---|---|
| A slot | one `plasma_run` | one round of the chosen Recipe (`RoundRunner.start`, then `join`) |
| Start request | `{schedule, sequence, confirm_high_voltage}` | `{recipe_id, schedule, confirm_hv}`; the operator is the current one and is required: “Please select the operator first (the list is provided by the maintenance personnel in ”Maintenance › Operator ”Maintenance)” |
| Refusals at start | not ready | Recipe missing or invalid; `start_problems`; interval not longer than the Recipe's delays “interval {n} minutes shorter than Recipe The total waiting time of {s} seconds, every transaction will time out”; `uses_plasma` without `confirm_hv` “Schedules containing high-voltage plasma must first check the high-voltage safety item in the confirmation window.”; “Already has a schedule in progress” |
| Warning in the preview | estimate longer than the interval | same text, the original's: “Estimated single appointment {m} point {s} seconds, longer than the interval: some periods will be skipped due to timeout.” |
| Pause | none | `pause()`: status `paused`; a running round is paused. A slot that comes due (+5 s) while paused → `skipped_paused`, note “The schedule is paused when the time period arrives.” |
| Resume | none | `resume()`: refused while a manual pump runs “The manual pump is still running, please stop it before continuing the schedule.”; resumes the paused round |
| A slot cannot start | `not_started`; 3 in a row end the schedule | same |
| A round fails or is stopped | schedule ends | same (`failed` / `stopped`) |
| Overrun | `skipped_overrun` | same |
| Program restart | `interrupted`, never resumed | same; the round record of the cut-off round becomes `interrupted` |
| Record | `schedule.json` | `schedules\SCHEDULE-<id>.json`, saved after every change |
| Single `plasma_run` tied to the screen | no (unattended) | no (unattended) |

## B.8 Operators, events, alarms

**Operators** (`operators.py`, the original's rules): at most 50 names; each 1–20 characters, no control characters, none of `< > " \`; duplicates dropped. The current operator lives in memory and is “” after a restart. It is written into every round and schedule record and is the `actor` of events.

**Events** (`Store.event(text, level='info', actor=None)`): one line per event in `events.jsonl`: `{time, level, text, actor}`; `actor` is an operator name, “Maintenance” (request with a valid PIN session), or “system” On. Shown under alarm › operation event.

**Alarms** (`alarms.py`): a table of conditions evaluated once a second by the `ticker` thread, so they exist without an open page. An alarm that clears while unacknowledged stays listed as Restored until acknowledged (as the original). Appearing, clearing and acknowledging are written as events. Acknowledgement is kept in memory (after a restart a condition that still holds raises its alarm again).

| id | level | area | Condition | message |
|---|---|---|---|---|
| `ui_comm` | Emergency | picture | **browser only**: no fresh `/api/state` for 5 s | “The program did not respond: the screen data has expired and the actual status of the device is unknown.” |
| `pump_stop_unconfirmed` | Emergency | pump | a stop (deadline, STOP, common exit) was not confirmed; latched | “The pump stop command is not confirmed, please use the physical stop method immediately.” |
| `hv_off_unconfirmed` | Emergency | high pressure | RPi `stop` failed, or a job ended with `post_stop.ok` false; latched | “High voltage shutdown not confirmed: Please confirm physically that the high voltage has been shut down” |
| `board_lost` | Emergency if something was moving, else high | pump | board enabled and link lost | “The connection to the pump control board is interrupted. The pump may still be running: please use the physical switch to turn off the power to the pump.” / “Pump control board connection interrupted ({port}) ,program is reconnecting” |
| `board_reset` | high | pump | unexpected `READY`; latched | “Pump control board restart: pump stopped, settings reloaded” |
| `round_failed:{id}` | high | Process | newest round `failed` | “process failed:{error}” |
| `sched_failed:{id}` | high | Schedule | schedule `failed` | “Schedule has stopped:{error}” |
| `sched_int:{id}` | high | Schedule | schedule `interrupted` | “The schedule has been interrupted and will not be automatically restored.” |
| `nano_fault` | high | high pressure | `nano.fault` is not `NONE` | “Nano fault lock {fault}” |
| `rpi_comm` | high while a schedule that uses the RPi is active, else middle | RPi | RPi needed (active schedule, else the Recipe of the last preview) and not connected | “RPi Not connected: Schedule content measurement step, the program is automatically reconnecting.” / “RPi Not connected: selected Recipe content measurement step cannot be executed” Is |
| `sched_skip:{id}` | middle | Schedule | `counts.skipped_overrun > 0` | “Already {n} periods were not measured due to the timeout of the previous measurement.” |
| `sched_ns:{id}:{i}` | middle | Schedule | a slot `not_started` | “No. {n} times failed to start:{note}” |
| `dose_tolerance:{round}:{step}` | middle | pump | `dose_weight` returned `within_tolerance` false; latched | “Weighing quantity exceeds the allowable range: target {x} g,Actual {y} g” |
| `scale_offline` | middle | scales | board `ok` and no weight for 10 s | “Weight module did not respond” |
| `syringe_error` | middle | syringe pump | last syringe reply had an error code; latched | “Syringe pump report error {n} ({illustrate}) ” |
| `fw_mismatch` | middle | pump | B.4.3 step 4 | “pump control board firmware settings and hardware.yaml no the same” |

## B.9 The PIN lock and exactly what needs it

`PinLock` keeps today's parameters (6–8 digits, PBKDF2-HMAC-SHA256 600 000, `pin.json`, session 600 s sliding, 5 wrong → 30 s doubling to 900 s). Changes:

| Topic | 1.3.0 | 2.0.0 (the original's model) |
|---|---|---|
| No PIN file | everything is open | the Maintenance area is closed; every PIN route answers 403 `{code: 'pin_unset'}`; the page says how to set one |
| Setting | `Set-PIN.cmd` | `Set-PIN.cmd` (unchanged way; no screen and no route sets or changes it). Running it again replaces the PIN |
| PIN file replaced | sessions stay | all sessions end (file time is compared at every check) |
| What renews a session | every checked request | every POST and every PIN-only GET that the person caused. `GET /api/state` never renews. The page makes **no** automatic PIN-only request, so an idle screen really ends after 10 minutes; the page also locks itself after 600 s without pointer or key input (“Maintenance mode has automatically logged out”) |
| Practice mode | same file rules | fixed PIN “000000”, shown on the login panel; the real `pin.json` is never read |
| Session on the page | a variable | `sessionStorage['plasmic.unlock']`, header `X-Unlock` |

Actions (the server rule; the screen follows it — a control that needs the PIN exists only inside Maintenance):

| Action | PIN | In the original |
|---|---|---|
| Look at Overview, Schedule measurement, manual operation, execution record, alarm; read the guide | no | no |
| Choose the operator | no | no |
| Schedule: preview, start (also with high voltage, after the tick boxes), pause, resume | no | no |
| “Stop STOP” (schedule + round + pump board stop-all + RPi stop) | no | no (and the RPi stop had no operator button) |
| Acknowledge alarms | no | no (was per browser) |
| Manual: motor ON / OFF, dose by volume, dose by weight, auto-clean | no | pump ON / OFF: no |
| Manual: syringe initialise, aspirate, dispense, speed | no | no |
| “re-read status” (fresh RPi status) | no | no (`status`) |
| Round record, mean spectrum, CSV download | no | run log: no; spectra: PIN — changed on purpose, because without concentrations the spectrum **is** the operator's result |
| Recipe: import, edit, validate a draft | PIN (the controls are in Maintenance) | validate: no; the page was PIN |
| Recipe: publish | PIN | PIN |
| Recipe: delete | PIN session **and** the PIN typed again | same |
| RPi connection settings (address, port, token), connection diagnosis, automatic search RPi | PIN | PIN |
| Pump board: choose the port, “Not used”, Reconnect | PIN | PIN (`plc-config`) |
| Plasma parameters: save to the RPi | PIN | PIN |
| synchronous Nano parameter, Clear Nano fault, capture spectrum (no discharge) | PIN | PIN |
| ⚠ manual start plasma (one discharge outside a schedule) | PIN + tick box | PIN + tick box |
| Syringe size, Detect syringe pump | PIN | — |
| Scale: Empty cup returns to zero, Correction | PIN | — (fixed decision 7) |
| Single frames of a spectrum (spectrum tab) | PIN | PIN |
| Operator list | PIN | PIN |

## B.10 Stop paths and program exit

| Trigger | What is done | Confirmation shown |
|---|---|---|
| “Stop STOP” → `POST /api/stop` | `Application.stop_everything('operator')`: (1) schedule: no further slot; (2) round: stop flag; (3) manual operations: cancel; then in parallel (4) `board.stop_all()` when the board is enabled and (5) `link.stop()` on a fresh connection. Waits at most 8 s for (4) and (5) and reports both | footer message “Stopped: Both pump and high pressure have been read back to confirm shutdown” or what failed, plus the Emergency alarm |
| Motor row “OFF” | `board.stop_motor(n)`; allowed for any owner | row tag |
| A step fails | common exit B.5.4 | alarm `round_failed` |
| Longest run time reached | B.4.4 | event “{motor} Already set {n} seconds, automatic stop” |
| The page stops polling for 10 s while a **manual** operation is in progress (a manual pump runs, a manual dose, clean or syringe move) or a maintenance discharge runs | `ticker`: `board.stop_all()` / `link.stop()` once | event “The operation screen exceeds 10 No response for 20 seconds, stopped automatically{pump/discharge}” |
| The page disappears during a schedule | nothing (unattended by design, as 1.3.0 and the original) | — |
| Program ends: Ctrl+C, window closed, log-off, shutdown | `Application.close()`: schedule and round stop (join ≤ 5 s), `board.close()` (sends `X`), `link.stop()` when a job of this program runs, links closed. The console window's close button does not run Python's `finally`, so `main()` registers `SetConsoleCtrlHandler` (through `ctypes`, present in the bundled Python) and calls a 3-second `close_fast()` — `X` first — on `CTRL_CLOSE_EVENT`, `CTRL_LOGOFF_EVENT`, `CTRL_SHUTDOWN_EVENT` | console line “Stopping pump and high pressure...” |
| Program killed, PC loses power, USB cable pulled | nothing can be sent. The RPi's own watchdog switches the high voltage off. **The Mega has no watchdog: a continuous run goes on.** At the next start the board is opened again and `X` is sent (B.4.3 step 5) | section J |


---

# C. HTTP API

Server: unchanged kind — `ThreadingHTTPServer` on `127.0.0.1:8766`, exclusive port, `Host` must be `127.0.0.1:<port>` or `localhost:<port>`.
The single `POST /api/command {cmd}` of 1.3.0 is replaced by one route per action, so the PIN table can be read off the route list.

## C.1 Rules for every request

| Rule | Value |
|---|---|
| GET | `Host` check only. PIN-only GETs also need header `X-Unlock` |
| POST | header `X-Token` = the token from `/api/bootstrap` (new at every program start; on 403 “Invalid local request” the page re-reads bootstrap and retries once); `Origin` absent or same origin; body a JSON object, at most 250 000 bytes (a Recipe CSV may be 200 000) |
| PIN | header `X-Unlock: <session>`. Missing or expired → 403 `{error: 'This operation is only for maintenance personnel (maintenance PIN) ', code: 'locked'}`. No PIN file → 403 `{error: 'has not been set for maintenance PIN', code: 'pin_unset'}` |
| Reply headers | as today on every reply: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'` (`/guide` alone: `style-src 'self' 'unsafe-inline'`) |
| Errors | JSON `{error: <Chinese text>, code?: <word>, reasons?: [...], fixes?: [...]}` |

| Exception in the handler | Status | `code` |
|---|---|---|
| `ValueError` (bad input) | 400 | — |
| `PermissionError` | 403 | `locked` / `pin_unset` |
| unknown path, unknown id | 404 | — |
| `PlasmicError` (the RPi said no) | 409 | the RPi's code (`busy`, `not_ready`, `tool_failed`, …) |
| `BoardError` | 409 | `board_` + its code (`board_busy`, `board_link`, `board_refused`, `board_not_connected`, `board_timeout`, `board_device`) |
| `InterlockError` (carries `reasons`, `fixes`) | 409 | `interlock` |
| `RuntimeError` (state conflict) | 409 | — |
| `OSError` | 500 | — |

## C.2 Static files (CSP `'self'`)

| Path | File | Content-Type |
|---|---|---|
| `/` | `pc\web\index.html` | `text/html; charset=utf-8` |
| `/css/<name>.css` | `pc\web\css\` | `text/css; charset=utf-8` |
| `/js/<name>.js`, `/js/pages/<name>.js` | `pc\web\js\`, `pc\web\js\pages\` | `text/javascript; charset=utf-8` (module scripts are refused by the browser with any other type) |
| `/fonts/<name>.ttf` | `pc\web\fonts\` | `font/ttf` |
| `/guide` | `detailed-guide.html` | `text/html` |

A static path is served only if it matches `^/(css|js|js/pages|fonts)/[a-z0-9][a-z0-9._-]{0,60}\.(css|js|ttf)$`, the resolved file lies inside `pc\web`, and it is a regular file. No listing, no other extension, no `..`. The fixed `STATIC` dictionary of 1.3.0 goes away: a new module needs no server change.
`index.html` contains no `<script>` text, no `style=` attribute, no event attribute; it loads `/css/*.css` and one `<script type="module" src="/js/main.js">`. Scripts set positions and sizes through the CSSOM (`element.style.setProperty`), which the policy allows (1.3.0 already does this).

## C.3 GET routes

| Path | PIN | Reply |
|---|---|---|
| `/api/bootstrap` | no | `{csrf, version, demo, ui_watchdog_s: 10, stale_ms: 5000, pin: {set, practice}, hardware, actions, guide: '/guide'}` — `hardware` and `actions` below |
| `/api/state` | no (`X-Unlock` only sets `unlocked`; never renews) | section C.5. Each call feeds the screen watchdog |
| `/api/recipes` | no | `{recipes: [{id, name, created_at, sha256, step_count, rounds, uses_plasma, uses_rpi, uses_board, estimate_seconds, valid, error}]}` — `valid` / `error` = preflight against the present board settings |
| `/api/recipes/<id>` | no | `{id, name, created_at, sha256, csv, rows: [{line, action, label, target, target_label, value, unit, note}], facts, phases}` |
| `/api/recipes/<id>.csv` | no | the CSV as a download, `Content-Disposition: attachment; filename="recipe-<first 8 of id>.csv"`, UTF-8 with BOM |
| `/api/recipe-templates` | **yes** | `{templates: [{key, name, csv}]}` — the files of `pc\recipes\` |
| `/api/runlog?limit=200` | no | `{rows: [{kind: 'round' / 'slot', time, operator, recipe_name, slot, slots_planned, finished_at, status, result: {job, peak_nm, signal_frames} / null, note, round_id, schedule_id}]}` newest first; `kind: 'slot'` rows are slots that did not run |
| `/api/rounds/<id>` | no | the round record (D.2) |
| `/api/runs` | no | `{runs: [meta…]}` (max 200), as today |
| `/api/runs/<job>` | no | the stored run **without** `frames` |
| `/api/runs/<job>?frames=1` | **yes** | the stored run with the single frames |
| `/api/runs/<job>.csv` | no | as today |
| `/api/events?limit=200` | no | `{events: [{time, level, text, actor}]}` newest first |
| `/api/pumpboard/ports` | **yes** | `{ports: [{device, description, vid, pid, serial_number, is_mega, chosen}], enabled}` — enumeration only |

`hardware` (from the vendored `hardware.yaml`, so the page holds no hardware constant):

```json
{"groups": [{"id": "A", "label": "A Group", "motors": [2,3,4,5]}, {"id": "B", "label": "B Group", "motors": [6,7,8,9]}],
 "motors": [{"id": 2, "target": "M2", "label": "motor 2 · water", "group": "A", "valve": 1}, {"id": 3, "target": "M3", "label": "motor 3", "group": "A", "valve": null}, "…"],
 "valves": [{"id": 1, "label": "solenoid valve 1 · water", "motor": 2}, {"id": 2, "label": "solenoid valve 2 · CuSO₄", "motor": 9}],
 "valve_ms": {"open_before": 1000, "close_after": 1000},
 "flow": {"max": 80, "default": 40, "dose": 40.0}, "max_run_s": 300,
 "dose": {"ml": [0.01, 1000], "g": [0.01, 1000], "weight_enabled": true, "tolerance_percent": 0.5},
 "cleaning": {"water_motor": 2, "out_motor": 8, "flow_ml_min": 40.0, "water_lead_s": 30.0, "overlap_s": 45.0, "out_tail_s": 45.0, "total_s": 121},
 "syringe": {"options_ul": [5000, 1250], "positions": 3},
 "scale": {"decimals": 3, "presets_g": [10, 50, 100, 200], "default_calibration_g": 100, "stream_hz": 10}}
```

`actions` = table A.2 as data, one object per action:
`{action, label, group: 'pump'|'syringe pump'|'Measure'|'Process', target: 'none'|'motor'|'channel', value: null | {kind: 'number', min, max, step, decimals, unit, optional} | {kind: 'choice', choices: [{value, label}]} | {kind: 'count', min, max}, editor: true|false}` (`editor` false for `nano_stop`).
The step editor, the step tables and all range texts are driven by it.

## C.4 POST routes

| Path | PIN | Request | Reply 200 | Errors |
|---|---|---|---|---|
| `/api/unlock` | — | `{pin}` | `{session, expires_in: 600}` | 409 “Maintenance PIN error” · 409 “Too many attempts, please {n} Try again in seconds” · 403 `pin_unset` |
| `/api/lock` | — | `{}` | `{locked: true}` | — |
| `/api/operator` | no | `{name}` | `{operator}` | 400 “The operator is not in the list, please contact the maintenance personnel to add it.” |
| `/api/operators` | **yes** | `{operators: [name…]}` (the whole list) | `{operators, operator}` | 400 “has the largest list of operators 50 people” · 400 “The operator name must be 1–20 characters without control characters or < > " \” |
| `/api/schedule/preview` | no | `{recipe_id, schedule}` — `schedule` as in 1.3.0: `{interval_minutes, start: {mode: 'now'} / {mode: 'at', time}, end: {mode: 'count', count} / {mode: 'until', time} / {mode: 'continuous'}}` | `{spec, planned_total, last_slot_at, estimate_seconds, voltage_v, recipe: {id, name, step_count, rounds}, uses_plasma, uses_rpi, uses_board, warnings: [text], blockers: [{reason, fix}]}` | 400 form texts of `schedule.validate` · 400 “interval...every transaction will time out” · 404 “Recipe does not exist” · 400 the preflight message |
| `/api/schedule/start` | no | `{recipe_id, schedule, confirm_hv}` | `{accepted: true, schedule}` | 400 “Please select an operator first...” · 400 “Schedules containing high-voltage plasma must first check the high-voltage safety item in the confirmation window.” · 409 `interlock` with `reasons` · 409 “Already has a schedule in progress” |
| `/api/schedule/pause` | no | `{}` | `{accepted: true, status}` | 409 “There are currently no ongoing schedules that can be paused.” |
| `/api/schedule/resume` | no | `{}` | `{accepted: true, status}` | 409 “The schedule is not in paused state” · 409 “The manual pump is still running, please stop it before continuing the schedule.” |
| `/api/stop` | no | `{}` | always 200: `{schedule: bool, round: bool, manual: bool, board: null / {ok, error}, rpi: {ok, cancel_sent, error}}` — `null` when the board is not enabled | — (what could not be confirmed is in the body and raises the Emergency alarm) |
| `/api/alarms/ack` | no | `{id}` or `{all: true}` | `{acked: n}` | 404 unknown id |
| `/api/rpi/refresh` | no | `{}` | `{status}` (TCP `status` with `fresh: true`) | 409 “Not connected yet RPi: …” |
| `/api/manual` | no | one of the rows of C.4.1 | `{accepted: true, …}` | 400 range texts · 409 `interlock` · 409 `board_*` |
| `/api/recipes/validate` | **yes** | `{csv}` | `{ok: true, rows, facts, phases}` or `{ok: false, error, line}` | — |
| `/api/recipes/publish` | **yes** | `{name, csv}` | `{recipe}` | 400 “Recipe The name must be 1–60 characters” · 400 preflight message · 409 “already has the same name Recipe” |
| `/api/recipes/delete` | **yes** + `pin` in the body | `{id, pin}` | `{deleted: true}` | 409 “Maintenance PIN error” · 409 “this Recipe is being used in the schedule, please stop the schedule before deleting it.” |
| `/api/rpi/config` | **yes** | `{rpi_host, rpi_port, token, clear_token?}` (today's `/api/config`) | `{saved: true}` | 400 today's texts · 400 “simulation mode is fixedly connected to the local simulator” |
| `/api/rpi/diagnose` | **yes** | `{}` | `{steps: [{key, title, ok, detail, fix}]}` | — |
| `/api/rpi/find` | **yes** | `{}` | `{found, host, port, addresses}` | — |
| `/api/rpi/parameters` | **yes** | `{parameters: {Ontime, Offtime, Cycle, Loopnum, Integration_Time, Spectra_Interval, Voltage}}` | `{saved, parameters, synced, sync_error?}` (save, then `prepare`, as today) | 409 RPi texts · 409 “The schedule is in progress: Please pause or stop the schedule first.” · 409 `interlock` (a pump is moving) |
| `/api/rpi/tool` | **yes** | `{action: 'prepare' / 'clear_fault' / 'spectrum' / 'plasma_run', integration_ms?, confirm_high_voltage?}` | the RPi's reply (`{job, …}` for the two jobs) | 400 “You must first check the high voltage confirmation in the confirmation window.” · 409 `interlock` · 409 schedule active · 409 RPi texts |
| `/api/pumpboard/config` | **yes** | `{enabled: true, device}` or `{enabled: false}` or `{syringe_ul}` | `{saved: true, board}` | 400 “The port cannot be found” · 409 “The pump control panel settings cannot be changed while the pump is running” · 409 schedule active |
| `/api/pumpboard/reconnect` | **yes** | `{}` | `{accepted: true}` | 409 as above |
| `/api/pumpboard/tool` | **yes** | `{action: 'syringe_detect' / 'scale_tare' / 'scale_calibrate', grams?}` | `{ok: true, text, calibration?}` | 400 range · 409 `interlock` · 409 `board_*` |

The maintenance discharge (`/api/rpi/tool` `plasma_run`) is sent `attended=True`: it is tied to the screen watchdog as the “Start Plasma” button of 1.3.0 was. Its result is saved as a run with `source: 'manual'` and creates no round record (as the original).

### C.4.1 `POST /api/manual` bodies

Every start is checked with `interlock.manual_problems`; `pump_off` is always accepted. Blocking operations run in a `manual-op` worker; their end is reported in `state.manual.last` and as an event.

| `action` | Fields | Reply | Notes |
|---|---|---|---|
| `pump_on` | `motor` 2–9, `flow_ml_min` 1–80, `direction` 1 / −1, `seconds` 1–300 | `{accepted, motor, seconds}` | refused when the motor's group is busy: “{A/B} Group can only have one motor running at the same time: please stop it first {motor}” |
| `pump_off` | `motor` | `{accepted}` | |
| `dose_volume` | `motor`, `volume_ml` 0.01–1000, `direction` | `{accepted, eta_s}` | both groups must be idle |
| `dose_weight` | `motor`, `target_g` 0.01–1000 | `{accepted}` | scale must be online |
| `clean` | — | `{accepted, total_s}` | |
| `syringe_init` | — | `{accepted}` | |
| `syringe_speed` | `speed_ml_s` | `{accepted, speed_ml_s}` | |
| `syringe_aspirate` / `syringe_dispense` | `position` 1–3, `volume_ml` | `{accepted}` | needs initialise and speed; “Please press ”Initialize” first” · ”Please set the speed first” · ”The syringe capacity will be exceeded after withdrawing the liquid” · ”The injection volume is more than what is in the syringe” |

## C.5 `GET /api/state` — complete shape

One poll per second drives the whole screen. Sizes stay small: lists are cut (`events` 50, `slots` 60, `alarms` 100); the steps of a round are **not** in the state — the page fetches `/api/rounds/<id>` when `round.rev` changes.

```json
{
  "version": "2.0.0", "demo": false, "now": "2026-10-06T14:32:10+08:00",
  "pin_set": true, "unlocked": false, "unlock_left_s": 0,
  "operator": "", "operators": ["operator"],
  "setup": {"pin": true, "rpi": true, "board": "unset", "operators": 1, "recipes": 1, "complete": false},
  "owner": null,
  "rpi": {"connected": true, "host": "192.168.1.1", "port": 8767, "error": "", "server": {"version": "2.0.0", "sim": false, "flow": "unified", "…": "the hello reply"},
          "pending": "", "status": {"busy": false, "job": null, "age_s": 0.4, "snapshot": {"parameter": {}, "power": {}, "nano": {}}, "ready": {"plasma": true, "problems": []}, "estimate_s": 21, "start_s": 4, "flow": "unified", "sim": false},
          "status_age_s": 0.6, "own_job": null, "found": [], "addresses": ["192.168.1.2"]},
  "board": {
    "enabled": null, "status": "unset", "text": "Not set", "device": "", "simulated": false, "error": "", "firmware": null, "config_match": null, "age_s": null,
    "busy": false, "op": "", "cleaning": false, "clean_phase": "",
    "groups": [{"id": "A", "label": "A Group", "selected": null, "state": "idle"}, {"id": "B", "label": "B Group", "selected": null, "state": "idle"}],
    "motors": [{"id": 2, "state": "idle", "owner": null, "remaining_s": null, "flow_ml_min": null, "direction": null, "blocked": ""}],
    "valves": [{"id": 1, "open": false}, {"id": 2, "open": false}],
    "syringe": {"online": null, "initialized": false, "syringe_ul": 5000, "content_ul": null, "position": null, "speed_ml_s": null, "max_speed_ml_s": 2.5, "busy": false, "error": ""},
    "scale": {"online": false, "grams": null, "raw": null, "stable": false, "age_s": null, "calibration": {"divisor": 287703.031, "offset": -1.0, "saved_at": "2026-08-26T10:16:20+00:00", "reference_grams": 200.0}},
    "weight_dose": null
  },
  "manual": {"busy": false, "op": "", "last": {"action": "", "ok": true, "text": "", "time": ""}},
  "round": null,
  "schedule": null,
  "interlock": {"manual": {"ok": false, "reasons": ["Pump control board has not been set yet"], "fixes": ["Ask maintenance personnel to go to ”Repair › Connection” select the port"], "warn": ""},
                "plasma": {"ok": true, "reasons": [], "fixes": []}},
  "alarms": [], "alarm_counts": {"p1": 0, "p2": 0, "p3": 0, "unacked": 0},
  "events": [{"time": "…", "level": "info", "text": "…", "actor": "system"}],
  "latest_run": null,
  "message": {"text": "", "level": "info", "time": ""}
}
```

| Field | Meaning |
|---|---|
| `now` | the PC's local time; the clock of the page uses its own clock, this only detects a frozen program |
| `unlocked`, `unlock_left_s` | whether the session of this request is valid, and for how long without activity |
| `setup` | the four lines of “Before you start using it” on Overview: `pin` (PIN file exists or practice mode), `rpi` (connected at least once since start), `board` (`ok` / `unset` / `off` / `problem`), counts of operators and Recipes; `complete` = `pin` and `rpi` and `board` in (`ok`, `off`) and both counts > 0 |
| `owner` | `null`, `'round'`, `'manual'` (B.3) |
| `rpi` | `Link.view()` of 1.3.0, renamed from `link`; `status.snapshot`, `ready`, `estimate_s`, `start_s`, `flow`, `job` exactly as the RPi sends them |
| `board.status` | `unset` · `off` · `connecting` · `ok` · `simulated` · `missing` · `busy_port` · `wrong_device` · `lost`; `text` is the Chinese line for it (E.2) |
| `board.motors[].state` | `idle` · `wait` (valve opening) · `run` · `move` (volume dose) · `weigh` (weight dose) · `clean`; `owner` `null` / `'manual'` / `'round'`; `remaining_s` to the run-time limit; `blocked` = why ON is not possible for this row now (“A group is in use”), else empty |
| `board.clean_phase` | `''` · `valve` · `water` · `both` · `drain` |
| `board.syringe` | `online`: `true` / `false` / `null` (not asked yet); `content_ul`: the program's estimate, `null` = unknown; `position`: last valve position sent |
| `board.weight_dose` | `null` or `{motor, target_g, gain_g, elapsed_s}` — `gain_g` is the program's reading since the start, for display only |
| `manual.last` | result of the last manual operation, e.g. “motor 9 Quantitative completion:10.02 g (error +0.02 g) ” |
| `round` | `null` when no round has run since start; else the summary of the running or last round: `{id, rev, status, recipe_id, recipe_name, operator, schedule_id, slot, slots_planned, started_at, finished_at, step, step_count, round, rounds, phase, phases: [{name, from, to}], current: {n, line, action, label, target, target_label, value, unit, note, detail, started_at, eta_s}, uses_plasma, uses_board, paused_pumps: [target…], error, stop_errors: [text]}` — `rev` rises whenever a step changes state |
| `schedule` | `null` or the record of D.2 with the last 60 slots; `status` may be `paused` |
| `interlock` | what the server would answer now for a manual start and for a discharge; `warn` is the "cannot confirm" sentence |
| `alarms[]` | `{id, pri: 1 / 2 / 3, area, text, since, active, acked, cleared_at}`; `ui_comm` is added by the page itself |
| `message` | the newest line meant for the footer (the last event of level `warn` / `error`, or the result of the last command) |
| `latest_run` | meta of the newest stored spectrum (as 1.3.0) |

---

# D. Disk layout under `pc\data` (practice mode: `pc\data-demo`)

## D.1 Files

| Path | Written by | Atomic | Notes |
|---|---|---|---|
| `config.json` | installer, Maintenance › connection | yes | **unchanged**: `{rpi_host, rpi_port, token}` — the installer and 1.3.0 keep working with it |
| `pin.json` | `Set-PIN.cmd` | yes | unchanged |
| `version.json` | migration | yes | `{data_version: 2, program: '2.0.0', migrated_from: '1.3.0' / null, migrated_at}` |
| `events.jsonl` | `Store.event` | append | unchanged lines plus `actor`; old lines without it are shown with “system” On |
| `operators.json` | Maintenance › operator | yes | `{operators: [name…]}` |
| `pumpboard.json` | Maintenance › connection / Pump and Scale | yes | D.2 |
| `pumpboard\scale_calibration.json` | V7's `scale.tare` / `scale.calibrate` (through `SCALE_CALIBRATION_FILE`) | V7's own (`.tmp` + `os.replace`) | V7's shape `{version, saved_at, offset, divisor, reference_grams}` |
| `recipes\<32 hex>.json` | publish | yes | D.2; deleted ones are **moved** to `recipes\deleted\` |
| `schedules\SCHEDULE-<32 hex>.json` | `ScheduleRunner` | yes | saved after every change |
| `rounds\ROUND-<32 hex>.json` | `RoundRunner` | yes | written at the start, after steps (at most once a second) and at the end |
| `rounds\index.jsonl` | `RoundRunner` | append | one summary line per finished round; rebuilt from the files when missing |
| `runs\<job>.json`, `runs\<job>.meta.json` | `Store.save_run` | yes | unchanged; the meta gains `round_id`, `recipe_name`, `operator`, `slot`, `flow`, `source` (`round` / `manual` / `capture`); old metas without them stay valid |
| `schedule.json` | — | — | the 1.3.0 file: read once by the migration, then left where it is |
| `sim\…` | practice mode | — | simulator log and files, as today |

All JSON through `store.write_json` (`.tmp` in the same folder, rename, 10 retries on `PermissionError`). Ids are `uuid4().hex`; every id that arrives in a URL is checked against `[0-9a-f]{32}` (runs: today's `RUN_ID`).

## D.2 Shapes

`pumpboard.json`

```json
{"version": 1, "enabled": true, "device": "COM3", "vid": "2341", "pid": "0042", "serial_number": "…", "description": "Arduino Mega 2560 (COM3)", "syringe_ul": 5000, "chosen_at": "2026-10-06T06:40:00+00:00"}
```

`recipes\<id>.json`

```json
{"id": "…", "name": "Only discharge measurement", "created_at": "…", "source": "seed", "csv": "action,target,value,unit,note\nplasma_run,,,,…\n", "sha256": "…of csv…",
 "step_count": 1, "rounds": 1, "uses_plasma": true, "uses_rpi": true, "uses_board": false}
```

The hash is checked on every load (“Recipe file integrity verification failed”), as the original. `source`: `seed` · `import` · `editor` · `template`.

`schedules\SCHEDULE-<id>.json` = the 1.3.0 record plus the Recipe and the operator:

```json
{"id": "…", "status": "waiting", "created_at": "…", "finished_at": null, "operator": "Wang Xiaoming",
 "recipe_id": "…", "recipe_name": "Only discharge measurement", "recipe_sha256": "…", "uses_plasma": true, "uses_rpi": true, "uses_board": false, "hv_confirmed": true,
 "spec": {"first_slot": "…", "interval_seconds": 600, "interval_minutes": 10, "start": {"mode": "now", "time": null}, "end": {"mode": "count", "count": 1}},
 "planned_total": 1, "last_slot_at": "…", "estimate_seconds": 21, "voltage_v": 300,
 "next_index": 0, "next_slot_at": "…",
 "slots": [{"index": 0, "planned_at": "…", "status": "done", "round_id": "…", "job": "HMI-…", "started_at": "…", "finished_at": "…", "note": ""}],
 "counts": {"done": 1}, "error": ""}
```

`rounds\ROUND-<id>.json` — everything a later analysis needs; the original stored neither the CSV nor the parameters:

```json
{"id": "…", "version": 1, "status": "completed", "demo": false,
 "recipe": {"id": "…", "name": "Measurement after adding liquid", "sha256": "…", "csv": "…the full text that ran…"},
 "operator": "Wang Xiaoming", "schedule_id": "…", "slot": 3, "slots_planned": 6, "sample_kind": "sample",
 "started_at": "…", "finished_at": "…", "step_count": 8, "rounds": 1,
 "phases": [{"name": "Liquid delivery", "from": 1, "to": 3}, {"name": "Measure", "from": 4, "to": 4}, {"name": "Liquid delivery", "from": 5, "to": 8}],
 "steps": [{"n": 1, "line": 2, "round": 1, "action": "dose_volume", "target": "M9", "value": "5", "unit": "mL", "note": "…", "phase": 0,
            "status": "done", "started_at": "…", "finished_at": "…", "result": {"steps": 22795, "sps": 2825, "eta_s": 10.1, "elapsed_s": 10.6}, "error": ""}],
 "error": "", "stop_errors": [],
 "plasma": {"parameters": {"Ontime": 25, "Offtime": 5, "Cycle": 25, "Loopnum": 8, "Integration_Time": 1000, "Spectra_Interval": 1500, "Voltage": 300},
            "flow": "unified",
            "jobs": [{"job": "HMI-…", "kind": "plasma", "state": "done", "voltage_v": 300, "wait_s": 3.4, "background_frames": 1, "signal_frames": 8, "peak_nm": 337.1}]},
 "board": {"used": true, "simulated": false, "device": "COM3", "firmware": "READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0",
           "hardware_sha256": "…of the vendored hardware.yaml…", "syringe_ul": 5000,
           "scale_calibration": {"offset": -1.0, "divisor": 287703.031, "saved_at": "…"}},
 "weights": [{"n": 6, "name": "after adding liquid", "grams": 123.456}],
 "versions": {"pc": "2.0.0", "rpi": "2.0.0"},
 "events": [{"time": "…", "text": "1/8 No. 2 column quantitative (volume) motor 9 · CuSO₄ 5 mL"}]}
```

`sample_kind` is always `sample` in 2.0.0; the field is there for the blank and standard runs of a later version. `steps[].status`: `pending` · `running` · `done` · `failed` · `skipped`. `events` keeps the last 200 lines of the round.

## D.3 Migration from 1.3.0 (`migrate.py`, runs at every start before anything else; does nothing when `version.json` says `data_version: 2`)

| Step | Action | Never |
|---|---|---|
| 1 | `config.json`, `pin.json`, `events.jsonl`, `runs\` stay as they are | rewritten |
| 2 | `schedule.json` present → copied into `schedules\SCHEDULE-<its id>.json` with `recipe_name: 'Discharge measurement only (1.3.0) '`, `operator: ''`, `recipe_id: null`; a status in `ACTIVE` becomes `interrupted` as before | deleted or moved |
| 3 | `recipes\` missing → created; “Discharge measurement only” published from `pc\recipes\plasma-only.csv` (`source: 'seed'`) | overwrites an existing Recipe |
| 4 | `operators.json` missing → `{operators: ['operator']}` — one neutral entry, so a schedule can start on day one; maintenance replaces it with real names | overwrites |
| 5 | `pumpboard.json` missing → `{version: 1, enabled: null, syringe_ul: 5000}`; `pumpboard\scale_calibration.json` missing → copy of the vendored one | opens a port |
| 6 | `rounds\`, `schedules\` created; `version.json` written last | |

A fresh folder takes the same path with `migrated_from: null`. Going back to 1.3.0 still works: it reads `config.json`, `pin.json`, `events.jsonl`, `runs\` and its own `schedule.json`, all untouched.
A PIN that was set in 1.3.0 keeps working. A 1.3.0 folder **without** a PIN behaves differently after the update: the maintenance area is closed until `Set-PIN.cmd` has been run once (the guide and Overview's “Before you start using it” say so).


---

# E. User interface

The look is the original's live screen. Implementation rule: take the original's inline style block
(`hmi_v1_1\web\index.html` lines 18–468; reproduced verbatim in `scratchpad\understand\hmi-ui-style.md`, Appendix A.4, with the
design-system residue it relies on in §7.1) as the starting stylesheet and keep its class names. This section names those classes;
anything not mentioned keeps the original's CSS. Templates for doing it without React: `hmi_v1_1\web\calibration\` and `web\trend\`.

## E.1 Rules for every page

| Rule | Specification |
|---|---|
| Frame | `.hmi` 1024 × 768, `overflow: hidden`. Regions (x, y, w, h): top bar 0, 0, 1024, 56 · alarm banner 0, 56, 1024, 44 · nav rail 0, 100, 116, 598 · page 116, 100, 908, 598 (padding 8 / 10 → usable 888 × 582 at 126, 108) · footer 0, 698, 1024, 70. Overlays (`.overlay` z 40, `.dialog` z 41) are children of `.hmi` |
| Scaling | `body { margin: 0; height: 100vh; overflow: hidden; background: #d9dadc }`. `.hmi { position: absolute; left: 50%; top: 50%; margin: -384px 0 0 -512px; transform: scale(var(--k, 1)); transform-origin: 50% 50% }`. `main.js` sets `--k = min(innerWidth / 1024, innerHeight / 768)` on `resize` and at start. Proportions never change; nothing scrolls; on a 1920 × 1080 window k = 1.40625 |
| Practice mode | `.hmi.demo` (5 px amber inner ring) + striped mode chip, as the original |
| Colour | Colour means "running" or "abnormal" only. Running / command ON: solid `--run` #2f5f8f (`.q-run`). Normal: grey text (`.q-ok`). Value not trustworthy: magenta `--bad` with “?” prefix and dashed frame (`.q-bad`). Not installed / not chosen: light grey dashed (`.q-na`). Alarm priorities only: Emergency `--p1` red, high `--p2` orange, middle `--p3` yellow. High voltage: 3 px `--p1` frame with yellow-black hazard stripes (`.gbtn.hv`, `.stile.hv`, `.dialog.danger.hv`). Green #3d7a52 only for “Completed” and the OK square of a chip |
| Shape | square corners, 1 px `--line` borders, no shadows except overlays, no animation except the blinking priority box of an unacknowledged alarm |
| Fonts | Barlow 400 / 500 / 700 and Barlow Condensed 400 / 600 from `/fonts/` (`@font-face` in `base.css`); stack `"Barlow", system-ui, "Microsoft JhengHei", sans-serif`; base 15 px; Barlow Condensed for the title, the clock, time and number columns, big values and `.btn` labels |
| Lists | never scroll. Fixed rows per page with `.pager`: “◀ Previous page” `No. {a} / {b} Page` “Next page ▶”; both buttons disabled on a single page. Rows per page: steps on Overview 6 · slots 9 · run log 9 · round steps in the detail 9 · alarms 8 · events 12 · Recipes 6 · CSV rows 7 (draft 6) · operators 8 · spectrum sets 5 · single-frame table 10 · choices in a dialog 8 |
| Long text | one line with ellipsis; a tap opens it in a message dialog (cut at 700 characters) |
| Numbers | never typed into the page. A value is a tile (`.stile`, `.pbtn`, `.vol`, `.btn`); a tap opens the number pad dialog (E.3) |
| Text | the text dialog of E.3 (three uses: operator name, Recipe name / note, RPi address and token) |
| Disabled controls | a disabled control always has its reason on screen: the page's notice bar, the row's tag, or the small line of the button. The server checks again and its answer is shown as a message |
| Stale data | no fresh `/api/state` for 5 s: every device value becomes “? Unknown” (`.q-bad`), start buttons are disabled, alarm `ui_comm` is raised by the page, “Stop STOP” stays enabled |
| Polling | `/api/state` every 1000 ms (abort after 4000 ms; skipped while the previous one is pending) · clock, dialog countdown and message fading every 1000 ms · `/api/runlog` when execution record opens and every 10 s while it is open · `/api/schedule/preview` when the form changes and every 30 s while Schedule measurement is open and no schedule is active · `/api/rounds/<id>` when `round.rev` changes |
| Browser storage | `localStorage['plasmic.schedule2']` (schedule form) · `localStorage['plasmic.manual']` (`{flow, direction, seconds, syringe: {position, volume_ml, speed_ml_s}}`) · `sessionStorage['plasmic.unlock']` · `sessionStorage['plasmic.draft']` (Recipe draft) |
| Deep links | `/#dash` `/#schedule` `/#manual` `/#runs` `/#alarms` `/#maint` and `/#maint-recipe` `-link` `-plasma` `-pumps` `-spectra` `-operators` (login first when locked) |

## E.2 The frame

### E.2.1 Top bar `.top` (56 px; `gap: 10px; padding: 0 12px`), left to right

| # | Element | Class | Text and states |
|---|---|---|---|
| 1 | product name | `.title` 22 px | “Plasmic electronic control” |
| 2 | mode | `.mode` / `.mode.demo` | “official mode” / “Simulation mode·No connected device” |
| 3 | spacer | `flex: 1` | |
| 4 | RPi chip | `.chip` + `i` | “RPi is connected” (`.q-ok`) · “RPi not connected” (`.q-na`) · “RPi Simulation” (`.q-ok`, practice mode) · “RPi ? Unknown” (`.q-bad`, stale). `title` = the link's error text, else “{host}:{port}” |
| 5 | pump board chip | `.chip` + `i` | “pump is connected” (`.q-ok`) · “Pump Simulation” (`.q-ok`) · “pump not set” (`.q-na`) · “pump not used” (`.q-na`) · “pump is connecting” (`.q-na`) · “pump ? interrupt” (`.q-bad`: `missing`, `busy_port`, `wrong_device`, `lost`) · “pump ? Unknown” (stale) |
| 6 | operator | `button.opbtn` 44 high, 118–170 wide | `<small>operator</small><b>{name}</b>`; none: `.none` with “Please select”. Tap → dialog “Select operator” (choice grid of the names; empty list → message “No operator list yet” / “Ask maintenance personnel to go to ”Repair › Operator” is added. ”). Can be changed at any time, also during a schedule: each round records the operator at its own start, the schedule record keeps who started it |
| 7 | maintenance mark (only while unlocked) | `span.tag.q-maint` (blue outline, bold, not a button) | “maintenance mode {m}:{ss}” — time left without activity |
| 8 | guide | `a.userbtn` (`href="/guide" target="_blank" rel="noopener"`) | “illustrate” |
| 9 | clock | `.clock` | `<small>{YYYY-MM-DD}</small>{HH:MM:SS}` |

Not carried over: language switch, the hidden long-press entry, the “maintenance mode” button (the nav item is the one way in).

### E.2.2 Alarm banner `.banner` (44 px)

| State | Class | Content |
|---|---|---|
| no alarm | `banner none` | “No active alarm” |
| worst alarm unacknowledged | `banner p1 unacked` / `p2` / `p3` | `.pri` “urgent”/“high”/“middle” (blinks) · `.time` `HH:MM:SS` · `.msg` text (tap → dialog “alarm · {level}” with the full text, the area and the time) · `.more` “Also {n} item” · button “confirm ACK” · button “Alarm List” |
| all acknowledged, some active | `banner acked` | same without “confirm ACK” |

Order: unacknowledged first, then priority, then newest. “confirm ACK” → `POST /api/alarms/ack {id}`. “Alarm List” → page alarm.

### E.2.3 Navigation rail `.nav` (116 px; buttons 104 × 54, gap 5)

“Overview” · ”Scheduled Measurement” · ”Manual Operation” · ”Execution Record” · ”Alarm” (with `.badge` = number of alarms, coloured by the worst unacknowledged priority, grey when all are acknowledged) · pinned to the bottom, 104 × 46, dashed: “🔧 Maintenance” (locked) / “Maintenance Settings” (unlocked). Selected: `.sel`.
There are no Trend and Correction items. When they come back in a later version they take the two free places between manual operation and alarm; the round record already holds what they need (D.2).

### E.2.4 Footer `.foot` (70 px), left to right

| Element | Size | Text | Action |
|---|---|---|---|
| `.start` | 140 × 54 | “Start scheduling” · ”Scheduling” · ”Continue RESUME” (`.start.resume`, green) | idle → dialog E.6.3 · paused → dialog “Continue to schedule” (lines “Recipe”“Schedule””Current”; confirm “continue”) → `POST /api/schedule/resume` |
| `.pause-btn` | 120 × 54 | “pause PAUSE” · “Paused...” | `POST /api/schedule/pause`, no dialog |
| `.stop` | 120 × 54 | “Stop STOP” | `POST /api/stop`, **no dialog, always enabled** |
| `.prog` | flexible | see below | |
| `.hvtag` | auto | “⚡ contains high voltage” — only while an active schedule uses plasma; `title` “Schedules to automatically output high voltage for each measurement.” | — |
| `.estop` | 188 wide, 2 px red frame, SVG red disc r 14 with yellow disc r 8 | “For emergency stop, please press on the device**Physical emergency stop button**”; `title` “STOP is a software stop, not a safety level emergency stop.” | not a button |

Buttons by situation:

| Situation | `.start` | `.pause-btn` | `.stop` |
|---|---|---|---|
| no schedule active, start possible | “Start scheduling” enabled | disabled | enabled |
| no schedule active, start not possible | “Start scheduling” disabled | disabled | enabled |
| schedule `waiting` / `running` | “is scheduled” disabled | “pause PAUSE” enabled | enabled |
| round `pausing` | “is scheduled” disabled | “Paused...” disabled | enabled |
| schedule `paused` | “continue RESUME” enabled | disabled | enabled |
| schedule `stopping` | “is scheduled” disabled | disabled | enabled |

`.prog` shows exactly one of:

| Case | Content |
|---|---|
| start not possible | `<small>cannot start</small><b class="why">{reason}</b>`; tap → dialog “Unable to start schedule at this time” with every reason and its fix; when a reason comes from the RPi the confirm button reads “re-read status” (→ `POST /api/rpi/refresh`), else only “Close” |
| schedule active | four cells: “schedule” `No. {n} / {N} Second-rate` (continuous: `No. {n} Second-rate`) · “Next measurement” `{HH:MM} ({mm:ss}) ` or “at present” `under measurement` / `Paused` / `Suspended` / `Stopping` · “Steps” `{a} / {b} · {phase}` · “Not measured” `{n} Second-rate` |
| otherwise | `<small>message</small><b>{state.message.text}</b>`; a line of level `info` disappears after 12 s, `warn` / `error` stay until the next one; tap → dialog “message” |

Reason for "start not possible", first match: “program did not respond” (stale) · “Please go to ”Scheduling Measurement” to select Recipe” · the preview's error text · “Schedule setting is being calculated...” · the first `blockers[].reason` of the preview.
A missing operator is not a reason: “Start scheduling” opens the operator picker first, then the start dialog.

After “Stop STOP” the message is built from the reply: “Stopped: Both pump and high pressure have been read back and confirmed to be closed” · ”Stopped: High pressure has been confirmed to be closed (pump control board is not used)” Every button on the ·
“⚠ Stop command not confirmed:{pump control board|RPi} ({error}) — Please stop physically” (level `error`).

## E.3 Dialogs

One engine (`dialog.js`), one dialog at a time, built inside `.hmi`. A tap on the scrim or `Esc` cancels.

| Part (all optional, in this order) | Class | Notes |
|---|---|---|
| title | `.dlg-h` | high-voltage dialogs start with “⚠” |
| lines | `.dlg-line` | grey label left, bold value right |
| fields | `.dlg-line.tap` | a line that opens another dialog and comes back with the new value (used by connection settings and the Recipe step) |
| choices | `.dlg-choices.grid` | two 52 px buttons per row, 8 per page, “◀ Previous page” / “Next page ▶” inside the dialog; a choice may carry a `<small>` line and may be disabled with its reason there |
| number pad | `.dlg-buf` + `.dlg-range` + `.keys` | keys `7 8 9 / 4 5 6 / 1 2 3 / C 0 ⌫`; with decimals `. 0 ⌫` and a row `C`; range line “range {min}–{max},integer” or “range {min}–{max},decimal {n} Bit”; error “is out of range or the format does not match”; confirm disabled until valid. The keyboard works too: digits, `.`, Backspace, Enter (= confirm when valid), Esc. PIN variant: “●” per digit, “Please enter 6–8 digital repair PIN” |
| text | `input.dlg-input` (looks like `.login-buf`: 52 px, 2 px blue frame, 22 px) + a line with the rule | the only native input of the program; `maxlength` set; focus on open; Enter confirms. Masked variant (`type="password"`) with a “show” / “Hide” button for the token |
| detail | `.dlg-detail` (`.err` for errors) | plain text, `white-space: pre-wrap` |
| warning | `.dlg-warn` | yellow left bar |
| tick rows | `.ack-row` (+ `.on`) | 54 px, 2 px red frame, 28 px box with “✓”; **every** row must be ticked before the confirm button is enabled |
| footer | `.dlg-f` | “Cancel” + confirm with its own label (default “Sure”); message dialogs have one button “Close” |
| countdown | `.dlg-timeout` | “{n} seconds, it will be automatically canceled and no command will be sent. ” |

Variants: default 440 wide · `.wide` 720 (text, choices) · `.danger` (orange frame and confirm) · `.danger.hv` (580 wide, 3 px red frame, red title bar, red confirm).
Auto-cancel: 30 s, restarted by every key or tap inside the dialog; 90 s for Start scheduling; 120 s for message dialogs, the Recipe step chain and Select port. On time-out the footer message is “Confirmation window timed out and was canceled,No command sent”.
Why tick boxes and a pad instead of plain form fields: the original's operators work on a touch panel, a wrong number on this rig moves liquid or sets a voltage, and one path for every number keeps range checks and their texts in one place.

## E.4 First start and set-up (onboarding)

What a new owner, or the owner coming from 1.3.0, sees and does. Nothing here needs a command line.

| Step | Where | What the screen shows | What the person does |
|---|---|---|---|
| 0 | `Start.cmd` | browser opens Overview. Chips: “RPi is connected” (when the cable network is as before), “pump not set”. The third panel of Overview is “Before you start using it” (E.5, block D3) with four lines | reads the four lines |
| 1 | line 1 “Setup maintenance PIN”: “Not set” | “Double-click in the folder Set-PIN.cmd,input 6–8 digit. No need to restart the program after setting. ” | double-clicks `Set-PIN.cmd` once; within 10 s the line reads “has been set” |
| 2 | line 3 “pump control board (Arduino Mega) ”: “Not selected yet”, button “Go for repair › connection” | login page, then the connection tab | “Select port” → the list shows “COM3 · Arduino Mega 2560 (COM3)” with “Arduino Mega (suggestion) ” → confirm dialog with the restart warning → “connection”. About 4 s later: “Status Connected COM3”. Without pumps: “does not use the pump control board” instead |
| 3 | line 4 “Operator Vs. Recipe”: “1 operators · 1 indivual Recipe”, “can start” | the seeded operator “Operator” and the published Recipe “Discharge measurement only” | optional: Maintenance › operator → real names; Maintenance › Recipe → “New Recipe” → a template → adjust → “release” |
| 4 | Schedule measurement | Recipe tile “Please select” (amber dashed) | taps it → “Discharge measurement only”. The form already says “Start now”, “common 1 times” |
| 5 | footer “Start scheduling” | operator picker (first time), then the start dialog with the tick boxes | ticks, “Start scheduling” → about 4 s later the discharge; the result appears on Overview (“latest measurement” Will) and in execution record |

When all four lines are done the panel “Before you start using it” is replaced by the step list and returns only if one of the four becomes untrue (for example the pump board is unplugged).

## E.5 Page Overview (`dash`) — read-only

Four blocks, top to bottom in the 888 × 582 area, 8 px apart.

| Block | Height | Class |
|---|---|---|
| D1 four value tiles | 84 | `.metals` with four `.panel.metal` (each 216 wide) |
| D2 process stage | 84 | `.panel.steps` |
| D3 Steps in this round / Recipe Steps / Before you start using | 334 (flexible) | `.panel.table` |
| D4 status strip | 56 | `.panel.strip` with six `.cell` |

**D1 tiles** — `.metal-head` = name (19 px bold) + `.tag`; `.metal-val` = `<b>` value (30 px Barlow Condensed; class `has` when the value is real) + `<span>` sub-line.

| Tile | Tag | Value | Sub-line |
|---|---|---|---|
| “HSPY high voltage power supply” | “HSPY OFF” · “HSPY ON” (`.q-run`) · “? readback is invalid” (`.q-bad`) · “not connected” (`.q-na`) | `{voltage_v} V` (read-back) or “—” | “settings {set_voltage_v} V · output {current_a × 1000} mA” |
| “pump” | “Connected” · ”Simulation” · ”Not set” (`.q-na`) · “Not used” (`.q-na`) · “? interrupt” (`.q-bad`) | “stop all” (grey) · “motor 2” · “motor 2＋8” · “motor 9 Quantitative” · ”Cleaning” (blue, `.q-run`) | “A Group {motor n running/idle} · B Group {…}” |
| “weight” | “Stable” · ”Changing” · “? No response” (`.q-bad`) · “is not connected” (`.q-na`, board not in use) | `{grams} g` with `scale.decimals` decimals, or “—” | “Last calibration {YYYY-MM-DD}” |
| “latest measurement” Will — the whole tile is a `button` | “Completed” (green outline) or none | highest peak `{peak_nm} nm` (1 decimal) or “—” | “{MM-DD HH:MM} · {V} V · signal {n} open” / “has not been measured yet”. Tap → execution record, detail of that measurement |

The tag “HSPY OFF” is the supply's read-back, nothing more; the footer's E-stop notice is on the same screen at all times.

**D2 process stage** — `.ph`: `<h2>process stage</h2>`, `.sub` = source + Recipe name, `.sp`, `.meta`.

| Situation | `.sub` | `.meta` |
|---|---|---|
| round running | “is executing:{Recipe}” | “Schedule No. {n}/{N} times · steps {a}/{b}” |
| round paused | “Suspended:{Recipe}” | same |
| schedule waiting | “last executed: {state} · {Recipe}” | “Schedule {state} · next time {HH:MM}” |
| nothing active, a Recipe is chosen on Schedule measurement | “Selected:{Recipe}” | “common {b} step · {R} Wheel” |
| no Recipe chosen | — | body is `.steps-empty`: “Not selected yet Recipe: Please go to ”Scheduling Measurement” to select Recipe” |

Body `.steps-row`: one `.step` box per phase (`<span class="no">{n}</span>{name}`): `.done` grey, `.cur` blue, `.pause` amber, `.fail` orange. More than 10 phases: a window of 10 that contains the current one.

**D3** — `.ph` + `.thead` (34) + 6 `.trow.slim` (32) + `.pager`.

| Situation | Title | `.sub` | Body |
|---|---|---|---|
| a round is running or paused | “Steps for this round” | `round.current.detail` (B.5.3) | steps of that round |
| a round has ended and nothing else is chosen | “Steps for this round” | “{HH:MM} {Complete/failed/Stopped}{: error}” | its steps with their results |
| idle, Recipe chosen, set-up complete | “Recipe Steps” | “will be executed in this order after the schedule starts.” | the Recipe's expanded steps, all “—” |
| set-up not complete and no round running | “Before you start using it” | “After completing these four things in order, you can start measuring.” | the four lines below |
| nothing to show | “Steps for this round” | — | `.empty`: “There is no process yet” / “Go to ”Scheduled Measurement” and select Recipe,Then click ”Start Scheduling” at the bottom. ” |

Step table columns: `#` (44) · action (flex; the Screen name) · object (150; label) · numerical value (90, right; value + unit) · state (110) · time (70; `HH:MM:SS` of the end, or of the start while running).
state: “—” pending · `.st.running` “is executing” (row background `--run-bg`) · `.st.completed` “Completed” · `.st.failed` “failed” (tap the row → dialog “step failed” with the error) · “not executed” (grey).
The page that holds the current step is shown automatically unless the person used the pager in the last 30 s.

“Before you start using it” lines (four rows of 58 px; left: number, bold title, grey sub-line; right: `.tag` and at most one `.btn`):

| # | Title | Sub-line | Tag | Button |
|---|---|---|---|---|
| 1 | “Setup maintenance PIN” | “Double-click in the folder Set-PIN.cmd,input 6–8 digit. No need to restart the program after setting. ” (practice mode: “Practice mode maintenance PIN fixed yes 000000”) | “has been set” / “Not set” (`.q-na`) | — |
| 2 | “connected RPi (Plasma)” | “Connected {host}” or the link's error text | “is connected” / “not connected” (`.q-na`) | “Go for repair › connection” when not connected |
| 3 | “pump control board (Arduino Mega) ” | “is under maintenance › Select a port for connection. If you do not use a pump, select ”Do not use pump control panel”. ” | “is connected” / “Not used” / “Not selected yet” (`.q-na`) / “? Not connected” (`.q-bad`) | “Go for repair › connection” unless Connected or Not used |
| 4 | “Operator Vs. Recipe” | “{n} operators · {m} indivual Recipe” | “can start” / “Not completed” (`.q-na`) | “Go for repair › Operator” when n = 0, “Go for repair › Recipe” when m = 0 |

The buttons of lines 2–4 are hidden while line 1 is “Not set”.

**D4 strip**

| Cell label | Value |
|---|---|
| “RPi connection” | “Connected · {n} seconds ago” · ”Not connected” · “? Unknown” |
| “HSPY high voltage power supply” | “OFF · 0 V” · “ON · {V} V” (`.q-run`) · “? readback is invalid” · “—” |
| “Nano controller” | “{state} · {fault}”, e.g. “IDLE · NONE”; class `p2` when the fault is not `NONE` |
| “pump control board | `board.text` |
| “syringe pump” | “Initialized · Inside the syringe {x} mL” · “has not been initialized yet” · ”Not yet detected” · “—” |
| “active alert” | “{n} (unconfirmed {m}) ” with class `p1` / `p2` / `p3` of the worst · “none” |

## E.6 Page Schedule measurement (`schedule`)

`.sch-body`: left `.panel.sch-left` 392 wide, right `.panel.sch-right.table` 488 wide.

### E.6.1 Left: Schedule setting

`.ph`: `<h2>Schedule setting</h2>` + `.sub` “Click on each item to modify; the settings will be recorded on this computer.”; while a schedule is active “The schedule is in progress: it cannot be modified until it is stopped.” (tiles disabled and showing the running schedule's values).
`.sch-grid` (two columns, 8 px gap), tiles `.stile` 80 px high (`<span>` label, `<b>` value, `<small>` sub-line):

| Tile | Value | Sub-line | Editing |
|---|---|---|---|
| “Recipe” (spans both columns) | name, or “Please select” with `.need` | “{n} step · {contains high voltage plasma/Does not contain plasma} · {uses pump/No need for pump}” | dialog “Select Recipe”: choice grid of the published Recipes; one that fails preflight is disabled with its reason in the small line. No Recipe at all → message “Not yet Recipe” / “Ask maintenance personnel to go to ”Repair › Recipe”release.” |
| “Start” | “Start now” / “{HH:MM} Start” | “If this time has passed, it will be tomorrow.” (only for a clock time) | dialog “Start method”: “Start now” / “specified time” → pad “Start time: hour” 0–23 → pad “Start time: minutes” 0–59 |
| “Measurement interval” | “Every {N} minute”; whole hours “Every {h} Hour” | “is calculated based on the starting time each time”; with “common 1 times”: “Just measure 1 not used for the next time” | pad “Measurement interval” 1–1440, unit “minute” |
| “end condition” | “common {N} times” / “arrive {HH:MM} ” / “Continue until stop is pressed” | “Just measure once and choose 1 times” / — / “according to STOP Just ended” | dialog “end condition”: “Specified times” → pad “Measurement times” 1–1000 · “to the specified time” → the two time pads · “Continue until stop is pressed” |
| “measurement content” (a `div`, not a button; `.stile.hv` with plasma) | “⚡ contains high-voltage plasma” / “includes spectrum measurement” / “only delivers liquid” | “{V} V · Confirm once at the beginning, then automatically discharge / “No high voltage output” | — |

Default form: `{recipe_id: '', start: {mode: 'now', time: '08:00'}, interval_minutes: 10, end: {mode: 'count', count: 1, time: '17:00'}}`.

`.sch-est` (`.dlg-line` rows): before the start “Estimated single time” `about {m} point {s} Second` · “first measurement” `{HH:MM}` (+“ (tomorrow)”) · ”Estimated number of times” `{N} Second-rate` / `Continuous` · “The last time” `{MM-DD HH:MM}` / `according to STOP until`.
While active: “status” · ”First measurement” · ”Estimated times” · ”Last time”.
Then `.sch-err` (the preview's error, or `schedule.error`) and one `.sch-warn` per warning; at most two boxes are shown, a third reads “Also {n} , click here to see”.

### E.6.2 Right: slot log

`.ph`: title “This schedule” (active) / “Last scheduled” (ended) / “Schedule record” (none yet); `.sub` “{Recipe} · {operator} · {establishment time}”; right: “Complete {n} · Skip {n} · failed/not started {n}”.
Columns: sequence (52) · scheduled time (104, `MM-DD HH:MM`) · state (136) · start (48) · End (48) · Remarks (flex). Newest first, 9 per page. A row with a round opens its detail in execution record.
Empty: `.empty` “Scheduling has not started yet” / “Select on the left Recipe, Start time, interval and end conditions, then click ”Start Scheduling” at the bottom. Only measure once and select ”Total 1 times”. Results for each period (including periods skipped due to timeouts) are listed here. ”

### E.6.3 Start dialog (from the footer)

Title “Start scheduling”; with plasma “⚠ Start scheduling: high voltage will be automatically output for each measurement. (`.danger.hv`). 90 s countdown.

| Line | Value |
|---|---|
| “Recipe” | name |
| “Operator” | name |
| “first measurement” | `{HH:MM}` or “immediately” |
| “interval” | “Every {N} minute” (left out with “common 1 times”) |
| “End” | as on the tile |
| “Estimated single time” | “about {m} point {s} Second” |
| “HSPY Set voltage” (plasma only) | “{V} V (upper limit 700 V) ” |

Tick rows, in this order (2 to 4 rows):

| Shown when | Text |
|---|---|
| Recipe uses plasma | “I have confirmed that each measurement will automatically output {V} V high pressure” |
| Recipe uses plasma | “The electrode and high-voltage line have been connected; personnel have been kept away, and the upper cover has been closed.” |
| Recipe uses the pump board | “Pipes, reagents and waste are in place” |
| always | “Physical emergency stop/power-off switch can be used at any time” |

Warning with plasma: “Each measurement during scheduling will be automatically output. {V} V High voltage, no longer confirmed one after another. Please check the lid when leaving it unattended/door and emergency stop are available. In an emergency, please press the physical emergency stop button. ”
Without: “schedule will automatically start the pump and syringe pump according to time.STOP Stop for software,Not a safety level emergency stop.”
Confirm “Start scheduling” → `POST /api/schedule/start {recipe_id, schedule, confirm_hv: true}`. A refusal is shown as a message dialog “The schedule did not start” with the reasons.

## E.7 Page manual operation (`manual`)

Top: `.notice` (41 px). Below: `.manual` = `.panel.pumps` (550 wide) + `.side` (330 wide).

### E.7.1 Notice bar — first match

| Condition | Class | Text |
|---|---|---|
| stale | `.notice.err` | “program does not respond, manual operation is disabled” |
| board never chosen | `.notice.block` | “The pump control board has not been set yet: please ask maintenance personnel to › Connection ”Select Port” |
| board set to "not used" | `.notice.block` | “Pump control panel is set to not be used: There are no devices that can be operated on this page.” |
| board not connected | `.notice.err` | “Pump control board is not connected ({board.text}) : startup operations disabled” |
| schedule `waiting` / `running` / `stopping` | `.notice.block` | “Scheduling in progress: manual start and deactivation; if manual operation is required, please pause the schedule first.” |
| round paused | `.notice.block` | “The process is paused: the pump is still retained by the process, please click ”Continue” or ”Stop” at the bottom |
| high-voltage interlock (`interlock.manual.reasons`) | `.notice.err` with `.n-body` | “Manual start and deactivation: High voltage safety conditions are not met ({reasons}) ” and `.n-fix` “Processing method: {fixes}.OFF Still available.” |
| auto-clean running | `.notice.ok` | “Automatic cleaning is in progress: wait for it to end, or press ”Stop” at the bottom STOP”” |
| weight dose running | `.notice.ok` | “Weighing and quantification in progress ({motor}) : Wait for it to end, or press the OFF” |
| a command is being sent | `.notice.ok` | “command is being executed, please wait” |
| RPi not connected (interlock warning) | `.notice.warn` | “RPi Not connected: The program cannot confirm the high voltage status, please go to the site to confirm. HSPY has been closed” |
| otherwise | `.notice.warn` | “After the pump is started, the program will automatically stop feeding when the set seconds are reached. program, computer or USB There is no guarantee that it will stop when the line is interrupted. Please prepare a physical stop method. ” |

Rows 1–7 disable every start control of the page. OFF buttons are disabled only when the board is not connected.

### E.7.2 Left: peristaltic pump

`.ph` (46 px): `<h2>peristaltic pump</h2>` · `.sp` · `.seg` “”Forward rotation” ”Reverse rotation” · `button.btn` “{flow} mL/min” (pad “continuous operation flow” 1–80, 1 decimal, default 40) · `button.btn` “{n} Automatically stop in seconds” (pad “Automatic stop seconds” 1–300, integer, default 30).
These three settings are what the next “ON” uses (and the direction is what the next volume dose uses). They are kept in `localStorage['plasmic.manual']`.

Nine `.prow` rows share the remaining 487 px (54 px each): eight motors in the order of `hardware.motors`, then auto-clean.

Motor row: `.nm` (`<b>` 17 px label, `<small>` “A group” / “A group · Linked solenoid valve 1”) · `.tag` (min-width 104) · `button.btn` “ON” (56 × 44) · `button.btn` “Quantitative” (60 × 44) · `button.btn` “OFF” (56 × 44).

| Motor state | Tag | “ON” / “Quantitative” | “OFF” |
|---|---|---|---|
| idle, group free | “stop” | enabled | enabled |
| idle, other motor of the group busy | “{A/B} group is in use” (`.q-na`) | disabled | enabled |
| `wait` | “Valve is opening” (`.q-run`) | disabled | enabled |
| `run` (manual) | “Operation · Remaining {n} Second” (`.q-run`) | disabled | enabled |
| `run` (round) | “process control” (`.q-run`) | disabled | enabled |
| `move` | “Quantification in progress” (`.q-run`) | disabled | enabled |
| `weigh` | “weighing +{x} g” (`.q-run`) | disabled | enabled |
| `clean` | “Cleaning” (`.q-run`) | disabled | disabled (the firmware refuses single stops during cleaning; the notice says to use STOP) |
| board not connected / stale | “? Unknown” (`.q-bad`) | disabled | disabled / enabled when only stale |

Auto-clean row: `.nm` `<b>automatic cleaning</b><small>motor {water} Water ingress → motor {out} Discharge · Approx. {total_s} Second</small>` · `.tag` “standby” / “water valve” / “in water” / “Water inlet and drainage” / “finally emptied” · one `button.btn` “Start cleaning” (120 × 44; “Cleaning...” disabled while running).

Dialogs

| Trigger | Dialog |
|---|---|
| “ON” | “start {motor}” — lines “equipment” `{label}` · “direction/flow” `{Forward rotation/reverse} · {flow} mL/min` · “automatically stops” `{n} Second` · for `M2` / `M9` “solenoid valve” `valve {n} Will open first 1 Second`; warning “program, computer or USB The pump will not stop automatically when the line is interrupted. Please confirm that the pipeline is correct and the physical stop method is available before starting. ”; confirm “Start” → `pump_on` |
| “Quantitative” | (1) “{motor} quantitative method”: choices “volume (mL) ” / “weight (g) ” — the second disabled with “Weight module did not respond” or “hardware.yaml is not enabled” when it cannot be used; (2) pad “quantitative volume” 0.01–1000, 2 decimals, `mL` / “quantitative weight” 0.01–1000, 2 decimals, `g`; (3) “{motor} Quantitative” — lines “Way” `volume {x} mL` / `weight {x} g (scale re-closed circuit)` · “direction” `{Forward rotation/reverse}` (weight: “Forward rotation (fixed)”) · “speed” `{dose_flow} mL/min (fixed)` (weight: “40 → 10 → 3 mL/min” from `hardware.yaml`) · “Estimate” `about {t} Second`; warning “Stop midway,According to this column OFF or the ”Stop” at the bottom STOP”.”; confirm “Start quantification” |
| “OFF” | no dialog; `pump_off` at once |
| “Start cleaning” | “automatic cleaning — lines “Water intrusion” `motor {water},alone first {lead} Second` · “at the same time” `Again with the motor {out} Together {overlap} Second` · “empty” `Last motor {out} alone {tail} Second`; warning “Do not stop a single pump during cleaning. To stop, click ”Stop” at the bottom STOP”.”; confirm “Start cleaning” |

Results arrive as the footer message (from `state.manual.last`), e.g. “motor 9 Quantitative completion:10.02 g (error +0.02 g) ”, “motor 2 Already set 30 seconds, automatic stop”, “Operation failed: {text}”.

### E.7.3 Right: syringe pump and weight

**Panel syringe pump** (318 high). `.ph`: `<h2>syringe pump</h2>` · `.sub` “Syringe {5|1.25} mL” · `.sp` · `.tag`: “Initialized” / “has not been initialized yet” (`.q-na`) / “in action” (`.q-run`) / “? mistake {n}” (`.q-bad`).
`.syr` rows:

| Row label | Control |
|---|---|
| “valve position” | `.seg` of `syringe.positions` buttons, 50 px: “Valve position 1” “Valve position 2” “Valve position 3” — the position the next move uses; nothing is sent when it is tapped |
| “volume” | `−` (48 × 46) · `.vol` `{x.xxx} mL` (pad “injection volume” 0.001–syringe size, 3 decimals) · `+` (48 × 46); step 0.1 mL |
| “speed” | `.vol` `{x.xx} mL/s` (pad “Syringe pump speed” 0.001–max, 3 decimals; default 0.4). Sent with the next move when it differs from the last one sent |
| (actions) | `.acts`: “”Aspirate” · ”Inject” · ”Initialize” (46 px each) |
| (note) | `.fb`: “Estimate inside the syringe {x} mL.The syringe pump does not have position readback. Please go to the site to confirm the action results. ” / before initialising: “Please click ”Initialize” first. Initialization will push the piston to the top: the liquid in the syringe will be discharged from the current valve position. ” |

“Aspirate fluid” / “injection” are disabled until initialised, and when the move would leave 0…syringe size (the `.fb` line then reads “The syringe capacity will be exceeded after withdrawing liquid” / “There’s not that much in the syringe”).
Dialogs: “syringe pump pumping liquid” / “syringe pump injection” — lines “Valve position” · ”Volume” · ”Speed” · ”Estimation”; warning “The syringe pump does not have position readback, and the action results must be confirmed on site. ”; confirm “Aspirate fluid” / “injection”.
“Syringe pump initialization” (`.danger`) — warning “The piston will push to the top and then return. The liquid in the syringe will be discharged from the current valve position. Please confirm that the outlet is connected to the right place. ”; confirm “initialization”.

**Panel weight** (207 high). `.ph`: `<h2>weight</h2>` · `.sp` · `.tag` “stable” / “is changing” / “? No response”. Body: the weight in 44 px Barlow Condensed, centred, `{grams} g`; below 13 px grey “RAW {raw} · {hz} Hz”; bottom 13 px grey “Zero reset and calibration in ”Maintenance › Pump and Scale”. ” No buttons.

## E.8 Page execution record (`runs`)

### E.8.1 List

One `.panel.table`. `.ph`: `<h2>execution record</h2>` + `.sub` “Measured and unmeasured periods (recent 200 pen)”.
Columns: time (`.c-time` 126, `MM-DD HH:MM:SS`) · operator (80) · Recipe (flex) · sequence (52: `{n}/{N}`, continuous `{n}`) · End (48, `HH:MM`) · state (76) · results (190) · Remarks (110).
state: `.st.running` “is executing” · `.st.completed` “Completed” · `.st.failed` “failed” · `.st.stopped` “has been stopped” · `.st.skipped` “Skip” / “not started” · ”Aborted”.
results: “highest peak {nm} nm · signal {n} Zhang” · ”No measurement steps” · ”Not measured” (slot rows) · “—”.
Remarks: the error or the slot's note, ellipsis, tap → message dialog.
9 rows per page. A round row is a button → detail. Empty: “has no measurement record yet” / “After starting the schedule, each measurement and the period of time that was not measured due to timeout or suspension will be listed here. ”

### E.8.2 Detail (replaces the list inside the same page)

`.ph`: `button.btn` “◀ Return to list” · `<h2>No. {n}/{N} Second-rate · {Recipe}</h2>` · `.sub` “{YYYY-MM-DD HH:MM} · {operator}” · `.sp` · `.seg` “Spectrum” ”Steps”.

View “spectrum” — left column 250 wide `.kv.big`: “Status” · ”Start” · ”End” · ”Set Voltage” `{V} V` · “background/signal” `{b} open/{s} open` · “discharges in a few seconds after pressing” `{wait_s} Second` · “The highest peak” `{nm} nm`.
Right: `.chart` 620 × 308 (SVG plot 566 × 282 at 52, 4): the mean signal minus background, line `.sl-mean`; grid `.sl-grid`; zero line `.sl-zero`; y ticks left, x ticks below, unit “nm”; legend “black: average {n} Zhang (button background)”. Under the chart: “The strongest peak:” + up to five `.btn` chips `{nm}` (tap = zoom ±10 nm; a sixth chip “full spectrum” returns) and `a.btn` “download CSV (Excel can be opened)” (`/api/runs/<job>.csv`).
A round with several discharges adds `.fnav` “◀ No. {i} / {k} discharge ▶”. A round without a spectrum shows in the chart area “There is no spectrum this time (Recipe There is no measurement step, or the measurement is not completed)”.

View “Steps” — the table of E.5 D3 with 9 rows per page plus a column results (flex): “{dispensed} g (error {±e} g) ”, “{grams} g”, “spectrum {job forward 12 code}”, the error text.

## E.9 Page alarm (`alarms`) and the status words

`.ph` holds `.seg.tabs` “alarm list ({n}) ” “Operational events”, the counters `.count` “Emergency {n}” “high {n}” “middle {n}”, `.sp`, `button.btn` “Confirm all” (disabled when nothing is unacknowledged).

Alarm list — columns Occurrence time (126) · level (58, `.pri`) · area (60) · message (flex, tap → dialog) · state (118: “Active·Unconfirmed” ”Activated·Confirmed” ”Restored·Unconfirmed”) · action (72: `button.ack` “confirm”). Unacknowledged rows `.trow.unacked`. 8 per page.
Empty: “No active alarm” / “Alarm condition: The program does not respond, Use afterRPi or the connection to the pump control board is interrupted, the process fails, the stop of the pump or high pressure is not confirmed, the schedule is interrupted or the period is skipped,Nano fault. ”

operation event — `.sub` “recent 200 pen”; columns time (126) · who (80) · event (flex); `.trow.slim`, 12 per page; data `GET /api/events`.

Status words (one table for every page; `labels.js`):

| Thing | Value → text |
|---|---|
| round | `running` is executing · `pausing` Paused · `paused` Suspended · `stopping` Stopping · `completed` Complete · `failed` failed · `stopped` Stopped · `interrupted` Interrupted |
| schedule | `waiting` Wait for the next period · `running` under measurement · `paused` Suspended · `stopping` Stopping · `completed` Completed · `stopped` Stopped · `failed` has stopped (error) · `interrupted` has been interrupted (not automatically restored) |
| slot | `running` under measurement · `done` Complete · `failed` failed · `stopped` Stopped · `not_started` not started · `skipped_overrun` Skip (last timeout) · `skipped_paused` skip (scheduling pause) |
| motor group | `idle` idle · `wait` Valve is opening Starting with · `run` running · `move` Quantifying |
| board | `unset` Not set · `off` Not used · `connecting` Connecting... · `ok` Connected {device} · `simulated` simulation · `missing` Pump control board not found · `busy_port` {device} is occupied by other programs · `wrong_device` {device} is not a pump control board · `lost` Connection interrupted |
| alarm priority | 1 Emergency · 2 high · 3 middle |


## E.10 🔧 Maintenance — login and tab bar

### E.10.1 Locked: `.panel.login`

`.ph`: `<h2>Maintenance login</h2>` + `.sub` “Recipe, Connections, plasma parameters, pumps and scales, spectrum raw data and operator lists are only for maintenance personnel.”.

| State | Content |
|---|---|
| no PIN file (`pin_set` false) | `.empty`: `<b>has not been set for maintenance PIN</b>` · `<p>Please double-click in the folder on your computer Set-PIN.cmd,settings 6–8 Digital repair PIN.After setting up, return here without restarting the program.</p>` · `button.btn` “Return to overview”. The page notices the new PIN through `state.pin_set` within a second |
| PIN set | `.pin-wrap`: `.pin-dots` (360 × 56, one “●” per digit) · `.login-err` · `.pin-keys` (3 × 104 px, keys 62 high): `1 2 3 / 4 5 6 / 7 8 9 / Clear 0 ⌫`, at most 8 digits · `.pin-foot`: “Return” · ”Login” (primary, disabled below 6 digits). Keyboard: digits, Backspace, Enter |
| practice mode | as "PIN set", plus one line above the dots: “Practice mode maintenance PIN fixed yes 000000” |

“log in” → `POST /api/unlock {pin}`; while waiting “Logging in...”; failure in `.login-err`: “Login failed: repair PIN error” / “Login failed: Too many attempts, please {n} Try again in seconds”. Success: the session goes to `sessionStorage`, the page opens the tab that was asked for (default Recipe).
The original's “English keyboard” switch (for old 12-character passwords) is not carried over: EC never had such passwords.

### E.10.2 Unlocked: `.adm-bar` + one tab body (530 px high)

`.adm-bar` (44 px): `.seg.tabs` “Recipe” “Connection” ”Plasma Parameters” ”Pumps and Scales” ”Spectrum” ”Operator” · `button.msgchip` (last result of a maintenance action: `.ok` grey, `.busy` blue “Processing…”, `.err` “⚠ operation failed · Click here for details”; tap → dialog “Maintenance operation message”) · `button.btn` “Sign out” (`POST /api/lock`, back to Overview).
A request answered with `locked` (session ended) returns the page to the login panel with “Maintenance mode has automatically logged out”.

## E.11 Tab Recipe

`.adm-body`: left `.panel.adm-left` 300 wide, right `.panel.adm-right` 580 wide.

**Left “Published Recipe”** — `.list` of `.li` rows (58 px, 6 per page): `<b>{name}</b><span>{n} step · {contains plasma|Does not contain plasma} · {uses pump|No need for pump}</span>`; a Recipe that fails preflight now adds `<span class="warn">⚠ is currently unavailable</span>`. An unpublished draft is listed first as “ (draft) {name}”.
`.adm-foot`: `label.btn.file-btn` “Import CSV files” (hidden `input type="file" accept=".csv"`) · `button.btn` “New Recipe” → dialog “Where to start?: choices “Blank” · ”Discharge measurement only” · ”Measurement after adding liquid (example)” · ”Cleaning (example)” (from `/api/recipe-templates`).
Empty list: “Not yet Recipe” / “Click ”Add Recipe”Start from the example, or import CSV file. ”

**Right, a published Recipe is selected**

| Region | Content |
|---|---|
| `.ph` | `<h2>{name}</h2>` + `.sub` “Posted in {YYYY-MM-DD HH:MM}” |
| `.kv` (3 columns) | “steps/Wheel” `{n} step · {R} wheel` · “Measurement `contains high voltage plasma` / `Does not contain plasma` · “pump control board `Used` / `Need not` · “Estimated time” `about {m} point {s} Second` · “SHA-256” first 12 characters · “High voltage confirmation” `Confirm when starting schedule` / `unnecessary` |
| `.sch-err` (only when invalid now) | the preflight message |
| table | columns List (44) · action (flex) · object (150) · numerical value (90) · Remarks (flex); `.trow.slim`, 7 per page |
| `.adm-foot` | “Copy and modify” (makes a draft) · `a.btn` “remit CSV” (`/api/recipes/<id>.csv`) · `.sp` · “delete Recipe” (disabled while this Recipe is in an active schedule; small text then “schedule is in use”) |

**Right, a draft** (after “Import CSV files”, “New Recipe” or “Copy and modify”). The draft lives in the page and in `sessionStorage['plasmic.draft']`; every change is validated after 400 ms with `POST /api/recipes/validate`.

| Region | Content |
|---|---|
| `.ph` | `<h2>new Recipe (not released)</h2>` |
| `.kv` | “name” — a `button` showing the name or “Please name” (text dialog “Recipe name”, 1–60 characters) · “verify” `passed` / `failed` (`.txt-warn`) · “steps/wheel” · ”Estimated time” · ”Measurement/pump” · ”Error” (the message, tap → dialog; the row it names is marked `.flag`) |
| table | as above, 6 per page; each row is a button → row menu |
| `.adm-foot` | “＋ New step” (adds after the last row) · “give up” (dialog “discard draft” / “Content that has not been published will disappear. ”) · `.sp` · “release” (primary; disabled until valid and named) |

Row menu — dialog “No. {n} column:{action} {object} {numerical value}”, choices: “Modification” · ”Remarks” (text dialog, 200 characters) · “”Insert above” · ”Insert below” · ”Move up” · ”Move down” · ”Delete”.

Step chain (“Revise”, “insert”, “＋ New step”), driven by `bootstrap.actions`:

| Step | Dialog | Choices / pad |
|---|---|---|
| 1 | “Select action type” | “Pump” · ”Syringe Pump” · ”Measurement” · ”Process” |
| 2 | “Select action” | the Screen name of that group, each with a small line: e.g. “Start pump” `Maximum running seconds` · “Quantitative (volume)” `mL` · “discharge＋Capture spectrum” `background 1 open` |
| 3 (target `motor`) | “Select motor” | the eight labels |
| 3 (target `channel`) | “select valve position” | “Valve position 1” “Valve position 2” “Valve position 3” |
| 4 (number) | pad titled with the Screen name | range, step and unit of the action |
| 4 (`pump_direction`) | “direction” | “”Forward rotation” · ”Reverse rotation” |
| 4 (`spectrum_capture`) | “integration time” | “Integration time using plasma parameters” · ”Specify...” → pad 1–10000 `ms` |
| 4 (`repeat_start`) | pad “Repeat times” | 1–20 |

Publish — dialog “release Recipe”: lines “Name” · ”Number of steps/wheel” · ”Estimated time” · ”Measurement” · ”Pump control panel”; with plasma the warning “this Recipe contains high-voltage plasma: Confirm once at the beginning of the schedule, and it will automatically discharge for each subsequent measurement. ”; confirm “release” → `POST /api/recipes/publish`.
Delete — dialog “delete Recipe” (`.danger`): lines “Name” · ”Number of Steps”; warning “After deleting the schedule, you cannot select it again. The file will be moved to data\recipes\deleted.”; confirm “Next step” → PIN pad “input repair PIN to delete” → `POST /api/recipes/delete {id, pin}`.
Import errors (not a CSV, too large, wrong header) are shown as a message dialog “CSV cannot be read in” with the preflight text; nothing becomes a draft.

## E.12 Tab connection

Two `.panel.adm-half`, 440 wide each: left the RPi, right the pump board. Both render from `/api/state`; nothing is polled behind the PIN.

**Left “RPi (Plasma)”** — `.sub` “{host}:{port}”. `.kv.big`:

| Label | Value |
|---|---|
| “connection” | “is connected” / “not connected” (`.q-na`) / “simulator” |
| “server” | “plasmic-tcpd {version} · Process {flow}” |
| “This computer’s IP” | `addresses` joined with “, ” |
| “token” | “has been set” / “Not set” |
| “last update” | “{n} seconds ago” |
| “Recent error” | text with ellipsis (tap → dialog) or “—” |

`.btn-grid` (`.gbtn` 66 px): “connection setting” `<small>RPi ’s address, port and Allow list of token</small>` · “connection diagnosis” `<small>Check the device step by step</small>` · “automatic search RPi” `<small>Search on this computer’s network</small>`. In practice mode “connection setting” and “automatic search RPi” are disabled with the small line “simulation mode is fixedly connected to the local simulator”.

| Button | Dialog |
|---|---|
| “connection setting” | “RPi connection setting” with three fields: “RPi address” (text, e.g. `192.168.1.1`) · “TCP port” (pad 1–65535) · “token” (masked text; left empty = keep the saved one), and one plain tick row “Clear stored token”; confirm “Save and reconnect” → `POST /api/rpi/config` |
| “connection diagnosis” | “connection diagnosis” (`.wide`): one line per step of `/api/rpi/diagnose` — “✓” or “✗”, title, detail, and for a failed step its fix; buttons “Recheck” · ”Close” |
| “automatic search RPi” | “automatic search RPi” (`.wide`): while searching “Looking for...(most 20 seconds)”; then choices “{host} · {name} · Version {v}” (the present one marked “at present”); none: “not found. Please confirm the network line,RPi power supply, try again. ”; choosing another → confirm “Use instead {host}” → `POST /api/rpi/config` with the same port and the saved token |

**Right “pump control board (Arduino Mega) ”** — `.sub` = the device name or “Not selected yet”. `.kv.big`:

| Label | Value |
|---|---|
| “status” | `board.text` (`.q-run` never; `.q-bad` for the four problem states; `.q-na` for Not set / Not used) |
| “port” | “{device} · {description}” / “Not selected yet” / “Not used” |
| “firmware response” | the `READY…` line (ellipsis, tap → dialog) or “—” |
| “Firmware Settings” | “and hardware.yaml consistent” / “⚠ Does not match” / “—” |
| “syringe pump” | “is connected” / “has not been detected yet” / “—” |
| “weight module” | “{hz} Hz · normal” / “No response” / “—” |
| “Recent error” | `board.error` or “—” |

`.btn-grid`: “Select port” `<small>First time use, or changed USB hole</small>` · “Reconnect” `<small>Pump control board will restart</small>` (disabled when not enabled). Both disabled while a schedule is active or a pump runs, small line “The schedule or pump cannot be changed while it is running”.

| Step | Dialog |
|---|---|
| “Select port” | “Select the connection port of the pump control board” (`.wide`, 120 s), data `GET /api/pumpboard/ports`. One full-width choice per port: `<b>{device} · {description}</b><small>Arduino Mega (suggestion) </small>` or `<small>no Arduino Mega</small>`; Mega first. Last choice: `<b>Does not use pump control board</b><small>Only conduct discharge measurement</small>`. No port: detail “Not found on this computer USB serial port. Please check the pump control board USB line, and then click ”Refresh”. ”. Footer: “Cancel” · ”Rearrange” |
| a port chosen | “connect to {device}” (`.danger`): lines “device” `{description}` · “USB identification code” `{vid}:{pid}`; warning “is connected: the pump will stop and the solenoid valve will close. Please close the independent laboratory first v7 (run.bat) and Arduino IDE ’s serial port monitoring; the same port can only be used by one program at a time. ”; when it is not a Mega one tick row “This device does not appear to be Arduino Mega,I’m sure I want to use it”; confirm “connection” → `POST /api/pumpboard/config {enabled: true, device}` |
| “does not use the pump control board” | “does not use the pump control board”: detail “Manually operated pumps, syringe pumps and scales are deactivated; Recipe cannot be executed. Discharge only Recipe is not affected. ”; confirm “Sure” → `{enabled: false}` |
| “Reconnect” | “Rewire the pump control board”: the same restart warning; confirm “Reconnect” → `POST /api/pumpboard/reconnect` |

After a choice the “status” line goes “Connecting...” → “Connected COM3”, or to one of the three problem texts of B.4.3, which name what to do.

## E.13 Tab plasma parameters

One `.panel.adm-full`. `.ph`: `<h2>plasma parameters</h2>` + `.sub` “Click to modify the value; when saving HSPY Must OFF, zero output,Nano is idle and will not activate high voltage”.

`.param-grid` (4 columns, `.pbtn` 112 px): `<span>` label, `<b>` value, `<small>` “range {min}–{max}”; a changed tile gets `.changed` and its small line reads “RPi at present {old}”.

| Label | Key sent | Range | Decimals |
|---|---|---|---|
| “Pulse ON (ms) ” | `Ontime` | 1–1000 | 1 |
| “Pulse OFF (ms) ” | `Offtime` | 1–1000 | 1 |
| “every round pulse times (times)” | `Cycle` | 1–10000 | 0 |
| “Number of rounds (rounds)=signal spectrum number” | `Loopnum` | 1–1000 | 0 |
| “integration time (ms) ” | `Integration_Time` | 1–10000 | 0 |
| “Spectral interval (ms) ” | `Spectra_Interval` | 1–60000 | 0 |
| “HSPY set voltage (V) ” | `Voltage` | 1–700 | 0 |
| “background spectrum” — a `div.pbtn.local`, not a button | — | value “1 open”, small “Shoot before discharge, fixed” | — |

The cross rules (cycles × loops, interval ≥ integration, total time) are the RPi's; its message is shown when it refuses.

Tool row (`.btn-grid` with three columns, `.gbtn` 66 px) and below it the high-voltage button over the full width:

| Button | Small line | Action |
|---|---|---|
| “re-read status” | “HSPY/Nano/parameter” | `POST /api/rpi/refresh` |
| “synchronous Nano parameter” | “write parameters into Nano,No discharge” | dialog “synchronous Nano parameter” / detail “Bundle RPi Parameter writing of configuration file Nano and read back. High voltage will not start. ” → `/api/rpi/tool {action: 'prepare'}` |
| “Clear Nano Fault” | “at present {fault}” / “There is currently no fault” (disabled) / “Need first HSPY OFF” (disabled) | dialog “Clear Nano Fault” / detail “After clearing, click ”Synchronize” again. Nano parameter”. ” → `{action: 'clear_fault'}` |
| “⚠ Manual start plasma” (`.gbtn.hv`, 72 px) / “⚠ Plasma execution...” | “will output high voltage · Current setting {V} V” or the first reason of `interlock.plasma` / “Scheduling in progress” / “process is being executed” | dialog below → `{action: 'plasma_run', confirm_high_voltage: true}` |

`.adm-foot`: note “After modification, click ”Save to RPi”will be written. One measurement is estimated to be approximately {estimate_s} seconds, approximately after pressing {start_s} seconds to start discharging. ” (no parameters read yet: “RPi parameters have not been read yet: please confirm first RPi is connected”) · `.sp` · “Abandon modification” · ”Save to RPi” (primary; both disabled without a change).

Dialog “Store plasma parameters”: one line per changed value “{label}” `{old} → {new}`; warning “, each measurement will be {V} V discharge (upper limit 700 V) .Must HSPY OFF, zero output,Nano is idle and no process is executing; this operation will not activate high voltage. ”; confirm “save to RPi” → `POST /api/rpi/parameters` (all seven keys). Result in the message chip: “has been saved and synced to Nano” / “saved but synced Nano failed:{text}”.

Dialog “⚠ Manually start plasma: high voltage will be output. (`.danger.hv`): lines “HSPY Set voltage” · “Pulse ON/OFF” · “every round pulse times” · “Number of rounds” · “Integration time” · “Spectral interval” · “Estimated time” · “HSPY at present”; one tick row “I have confirmed that it will be output soon {V} V High voltage, personnel are far away from the electrode, physical emergency stop/The power-off switch is at hand”; confirm “Start plasma (output high voltage)”.
Message chip while it runs: “Plasma is executing... High voltage output is in progress. To stop, please press ”Stop” at the bottom. STOP””; afterwards “The plasma measurement is completed and the spectrum has been saved in the ”Spectrum” tab..

Heights inside the 530 px tab body: head 34 · grid 12 + 112 + 10 + 112 + 12 = 258 · tools 66 + 8 + 72 + 10 = 156 · foot 60 → 508.

## E.14 Tab Pump and Scale

Two `.panel.adm-half`.

**Left “Peristaltic Pump and Syringe Pump”** — `.kv.big`, read-only facts from the vendored `hardware.yaml`:

| Label | Value |
|---|---|
| “peristaltic pump” | “8 · Max. {max} mL/min” |
| “Quantitative Speed” | “{dose} mL/min (fixed)” |
| “Continuous operation upper limit” | “{max_run_s} Second” |
| “automatic cleaning | “motor {w} → motor {o} · {lead}/{overlap}/{tail} Second” |
| “linkage solenoid valve” | “motor 2↔valve 1, motor 9↔valve 2 · open first 1 Second, Houguan 1 Second” (built from `valves`) |
| “Set source” | “hardware.yaml (independent laboratory v7) · matches the attached list” / “⚠ The attached file has been modified” (the manifest check of F.3 at program start) |
| “syringe pump” | “is connected” / “has not been detected yet” / “No response” |

`.adm-note`: “These values come from independent laboratories v7 of hardware.yaml,can only be viewed here. ”
`.btn-grid`: “Syringe Capacity” `<small>at present {5|1.25} mL</small>` → dialog “Syringe Capacity”, choices from `syringe.options_ul` (“5 mL” “1.25 mL”), confirm text “Please re-initialize the syringe pump after changing the syringe. ” → `/api/pumpboard/config {syringe_ul}` · “Detect syringe pump” `<small>Ask if the syringe pump is online</small>` → `/api/pumpboard/tool {action: 'syringe_detect'}`, result in the message chip “syringe pump is connected” / “Syringe pump not found”.

**Right “weight module”** — top: the weight in 44 px, centred, with its tag (as on manual operation); `.kv.big`: “RAW” · “Correction” `divisor {divisor} · zero point {offset}` · “Last calibration” `{YYYY-MM-DD} · weight {g} g`.
`.adm-note`: “Sequence: Empty cup returns to zero → Put the weight on → Correction. The correction will be saved and automatically loaded next time it is started. ”
`.btn-grid`:

| Button | Dialog |
|---|---|
| “Empty cup returns to zero” `<small>Only empty cups are placed on the scale</small>` | “Empty cup returns to zero”: line “Current reading”; warning “Please make sure there are only empty cups on the scale and the reading has stabilized. ”; confirm “Return to zero” → `{action: 'scale_tare'}` |
| “Calibrate after adding weights” `<small>Select weight weight</small>` | (1) “Weight weight”: choices “10 g” “50 g” “100 g” “200 g” (`scale.presets_g`) · “other…” → pad 0.1–20000, 1 decimal; (2) “Calibration weight module”: lines “Weight” · ”Current Reading Value”; warning “Please make sure that the weight is added to an empty cup on the scale, and that the reading is stable. Calibration will overwrite the last result. ”; confirm “Correction” → `{action: 'scale_calibrate', grams}` |

All four buttons of this tab are disabled while a pump runs, a round runs or a schedule is active (small line “The pump cannot be used while it is running or scheduled.”), and when the board is not connected (“Pump control board is not connected”).

## E.15 Tab spectrum

`.adm-body`: `.panel.spec-list` 250 wide + `.panel.spec-main`.

**Left “spectral group”** — `.li` rows (5 per page): `<b>{MM-DD HH:MM:SS}</b><span>{Recipe Name|manual plasma|capture (no discharge)} · signal {n} open</span>`. The list follows `state.latest_run`; no refresh button.
`.adm-foot`: `button.btn` “capture spectrum (no discharge)” → dialog “capture spectrum (no discharge)”: field “integration time” (pad 1–10000 `ms`, default = plasma parameters); detail “read only 3 Zhang spectrum, no high pressure. Used to check the spectrometer alone. ”; confirm “Retrieve” → `/api/rpi/tool {action: 'spectrum', integration_ms}`. Disabled with its reason in a small line while a schedule or round is active or the RPi is not connected.

**Right** — `.ph` with `.spec-info` “background {b} Zhang · Signal {s} Zhang · Points {ms} ms · {job forward 16 code}”. `.spec-ctl`: `.seg` “Spectrum” ”Single Check” · `.seg` “Average” “Single image” “All stacked images” · `.fnav` “◀ No. {i} / {N} open ▶” (mode leaflet) · `.seg` “Full Spectrum” ”Near the Highest Peak”.
Spectrum: `.chart` 620 × 308; lines `.sl-mean` (black, mean), `.sl-frame` (blue, one frame), `.sl-over` (light blue, all frames); legend “black: average {n} Zhang (button background)” / “blue: No. {i} Zhang (button background)” / “Light blue: Each picture · Black: Average (all deduct the background)”. Data `GET /api/runs/<job>?frames=1`.
Single sheet inspection: table Zhang Ci (56) · peak (96) · Peak wavelength (96) · total intensity (96) · difference from median (96) · Reminder (flex: “difference > 20%” / “below background”); 10 per page; a tap on a row shows that frame in Spectrum.
Nothing selected: “Select the set of spectra on the left”.

## E.16 Tab operator

One `.panel.adm-full.table`. `.ph`: `<h2>Operator list</h2>` + `.sub` “The operator must choose a name before starting the schedule; who will be recorded for each measurement and operation event.” · `.sp` · `button.btn` “New operator”.
Columns `#` (56) · Name (flex) · delete (72, `button.ack` “Delete”); 8 per page.
“New operator” → text dialog “New operator” with the rule line “1–20 characters, Chinese can be typed directly”; “Delete” → dialog “Delete operator” / line “name” → both send the whole list with `POST /api/operators`.
Deleting the name that is currently chosen clears the choice (as the original). Empty list: “No operator yet” / “At least one operator is required to start scheduling. ”

## E.17 All dialogs (check-list for the implementers)

| Side | Dialogs |
|---|---|
| Frame | “Select operator” · ”No operator list yet” · ”Alert · {level}” · “message” · ”The schedule cannot be started at this time” · ”Start the schedule” / “⚠ Start scheduling: High voltage will be automatically output for each measurement” · ”Scheduling has not started” · ”Continue scheduling” When the version on |
| Overview | “step failed” |
| Schedule measurement | “Select Recipe” · “Not yet Recipe” · “How to start” · “start time: hour” · “start time: point” · “Measurement interval” · “end condition” · “Number of measurements” · “end time: hour” · “end time: point” |
| manual operation | “Continuous operation flow” · ”Auto stop seconds” · ”Start {motor}” · “{motor} Quantitative method” · ”Quantitative volume” · ”Quantitative weight” · “{motor} Quantitative” · “automatic cleaning” · “Injection volume” · “Syringe pump speed” · “syringe pump” · “Syringe pump injection” · “Syringe pump initialization” |
| Maintenance | “Maintenance operation message” · Recipe: “Where to start? · “Recipe Name” · ”No. {n} Column:...” · ”Remarks” · ”Select action type” · ”Select action” · ”Select motor” · ”Select valve position” ” · the value pads · “direction” · ”Integration time” · ”Number of repetitions” · ”Discard draft” · ”Publish Recipe” · “delete Recipe” · “input repair PIN to delete” · “CSV cannot be read” — Wired: “RPi connection setting” (+ “RPi address”, “TCP port”, “token”) · “connection diagnosis” · ”Automatic search RPi” · “Use instead {host}” · “Select the port for the pump control board” · ”Connect to {device}” · “Not using pump control board” · ”Rewiring pump control board” - Plasma parameters: one pad per parameter · “Store plasma parameters” · ”Synchronization Nano parameter” · ”Clear Nano Fault” · “⚠ Manually start plasma: high voltage will be output” — Pumps and Scales: “Syringe Capacity” · ”Empty Cup Return to Zero” · ”Weight Weight” · ”Calibration Weight Module” - Spectrum: “capture spectrum (without discharge)” — Operator: “Add operator” · ”Delete operator” |

Tick boxes are used in exactly three dialogs: the schedule start, the maintenance discharge, and the choice of a port that is not an Arduino Mega.

## E.18 Web code: files and contract

No build step. ES modules (`<script type="module">`), relative imports, same origin. No `innerHTML` with data, no inline style attribute, no `eval`.

| File | Owner (section H) | Contents |
|---|---|---|
| `pc\web\index.html` | W-frame | static skeleton: `.hmi` > `.top`, `.banner`, `.body` (`.nav`, one empty `<section class="page" id="page-…" hidden>` per page), `.foot`, `#overlay`, `#dialog`; links to the four style sheets; one module script |
| `pc\web\css\base.css` | W-frame | tokens, `@font-face`, reset, scaling, frame regions (top, banner, nav, footer) |
| `pc\web\css\components.css` | W-frame | `.panel` `.ph` `.btn` `.seg` `.tag` `.chip` `.st` `.pri` `.table` `.trow` `.pager` `.li` `.empty` `.notice` `.kv` `.stile` `.pbtn` `.gbtn` `.vol` dialogs, pad, tick rows, `.chart` |
| `pc\web\css\pages.css` | W-pages | Overview, Schedule measurement, manual operation, execution record, alarm |
| `pc\web\css\maint.css` | W-maint | login, `.adm-*`, the six tabs |
| `pc\web\fonts\barlow-400.ttf` `barlow-500.ttf` `barlow-700.ttf` `barlow-condensed-400.ttf` `barlow-condensed-600.ttf` `Barlow-OFL.txt` `BarlowCondensed-OFL.txt` | V | copied from the original (F.2) |
| `pc\web\js\main.js` | W-frame | start: bootstrap, scaling, poll loop, router (hash), activity timer for the PIN, calls `render` of the frame and of the visible page |
| `pc\web\js\api.js` | W-frame | `get(path)`, `post(path, body)` with `X-Token`, `X-Unlock`, one retry after “Invalid local request”; `ApiError {status, code, message, reasons, fixes}` |
| `pc\web\js\store.js` | W-frame | holds `boot`, `state`, `stale`, the unlock session, the schedule form and its preview; `subscribe(fn)` |
| `pc\web\js\dom.js` | W-frame | `h(tag, props, …children)`, `setText`, `setClass`, `list(container, rows, key, make, update)` (keeps rows under the pointer), `pager(container, total, perPage, onChange)` |
| `pc\web\js\dialog.js` | W-frame | E.3: `confirm({...})`, `choose({...})`, `pad({...})`, `text({...})`, `message(title, text)`, `chain(steps)`; all return promises that resolve to the value or `null` |
| `pc\web\js\labels.js` | W-frame | every shared Chinese word: status tables of E.9, alarm priorities, `fmtTime`, `fmtDuration`, `motorLabel(id)`, `actionLabel(row)` |
| `pc\web\js\frame.js` | W-frame | top bar, banner, nav, footer (E.2), the start dialog (E.6.3), the browser-side alarm `ui_comm` |
| `pc\web\js\chart.js` | W-frame | `drawSpectrum(svgBox, {axis, series, range})` — SVG, nice ticks, min/max decimation |
| `pc\web\js\pages\dash.js` `schedule.js` `manual.js` `runs.js` `alarms.js` | W-pages | one page each |
| `pc\web\js\pages\maint.js` | W-maint | login panel, tab bar, message chip |
| `pc\web\js\pages\maint-recipe.js` `maint-link.js` `maint-plasma.js` `maint-pumps.js` `maint-spectra.js` `maint-operators.js` | W-maint | one tab each |

Contract of a page or tab module:

```js
export function mount(root, ctx) {}   // once; build the static DOM of the page inside root
export function render(state, ctx) {} // after every poll and after local changes, only while visible; must be idempotent
export function show(ctx) {}          // optional: the page became visible (start its own fetches)
export function hide(ctx) {}          // optional
```

`ctx = {api, store, dialog, labels, go(page, arg), message(text, level), unlocked()}`. A module touches only its own `root`; shared things go through `ctx`.
`render` must not rebuild a list whose data did not change (use `dom.list`) and must never replace a control that is being pressed.


---

# F. Vendoring, launchers, installer, versions

## F.1 V7 code (byte-identical copies; `backend\main.py` is not copied)

Source root `C:\Users\PlasmaLab\Desktop\independent laboratoryv7\` → destination root `C:\Users\PlasmaLab\Desktop\autonomous control\pc\vendor\v7\`.
The layout is kept, so V7's own path logic works unchanged: `backend\config.py` finds `..\config\hardware.yaml` by itself.

| Source | Destination | Used at run time |
|---|---|---|
| `backend\__init__.py` `config.py` `registry.py` `transport.py` `simulator.py` | `pc\vendor\v7\backend\` | yes |
| `backend\functions\__init__.py` `motors.py` `pump.py` `scale.py` | `pc\vendor\v7\backend\functions\` | yes |
| `config\hardware.yaml` | `pc\vendor\v7\config\hardware.yaml` | yes (read-only) |
| `config\scale_calibration.json` | `pc\vendor\v7\config\scale_calibration.json` | only as the seed of `pc\data\pumpboard\scale_calibration.json` |
| `firmware\Arduino\Arduino.ino` `hw_config.h` | `pc\vendor\v7\firmware\Arduino\` | no — reference |
| `tools\__init__.py` `gen_firmware_config.py` | `pc\vendor\v7\tools\` | no — reference |
| — | `pc\vendor\v7\SOURCE.txt` (new, ASCII): where the files come from, the date, V7's `__version__` 5.0.0, "do not edit; change V7 and copy again" | |

V7 carries no licence file (it is the owner's own project); `SOURCE.txt` records that.

## F.2 pyserial 3.5, PyYAML 6.0.2, fonts (no download)

Source root `C:\Users\PlasmaLab\Desktop\independent laboratoryv7\.venv\Lib\site-packages\`.

| Source | Destination | Notes |
|---|---|---|
| `serial\` — every `.py` in it and in `tools\`, `threaded\`, `urlhandler\`; no `__pycache__` | `pc\vendor\pylib\serial\` | whole package unchanged; on Windows only `__init__`, `serialutil`, `serialwin32`, `win32`, `tools\list_ports*` are loaded |
| `yaml\*.py` (17 files) | `pc\vendor\pylib\yaml\` | **not** `yaml\_yaml.cp313-win_amd64.pyd`, **not** the `_yaml\` folder; pure Python (`yaml.__with_libyaml__` is False, proven by the lead) |
| `PyYAML-6.0.2.dist-info\LICENSE` | `pc\vendor\licenses\PyYAML-LICENSE.txt` | MIT |
| `PyYAML-6.0.2.dist-info\METADATA` | `pc\vendor\licenses\PyYAML-METADATA.txt` | |
| `pyserial-3.5.dist-info\METADATA`, `DESCRIPTION.rst` | `pc\vendor\licenses\pyserial-METADATA.txt`, `pyserial-DESCRIPTION.rst` | the wheel contains **no** licence file |
| — | `pc\vendor\licenses\pyserial-LICENSE.txt` (new) | the BSD 3-Clause text with pyserial's copyright line (Chris Liechti); written by hand because the wheel does not carry it — the lead checks the wording against `METADATA` |

Fonts — source `C:\Users\PlasmaLab\Desktop\UI\UI\hmi_v1_1\web\vendor\` → `pc\web\fonts\`:
`font-a35caa4687f434d6.ttf` → `barlow-400.ttf` · `font-91c841fdfa8e7b94.ttf` → `barlow-500.ttf` · `font-74103fd6d41bc32c.ttf` → `barlow-700.ttf` ·
`font-c82d65280835cb3c.ttf` → `barlow-condensed-400.ttf` · `font-0d85af813fc3ed87.ttf` → `barlow-condensed-600.ttf` · `Barlow-OFL.txt`, `BarlowCondensed-OFL.txt` unchanged.
React, Babel and the original's `support.js` are not copied.

## F.3 Manifest and sync check

| File | Purpose |
|---|---|
| `pc\vendor\MANIFEST.json` | `{created_at, v7_version, files: [{path, origin: 'v7' / 'venv' / 'hmi', source, sha256, bytes}]}` for every copied file |
| `docs\tools\vendor_v7.py` | the copy tool: `--v7 <folder> --hmi <folder>`; copies the lists above, writes the manifest; refuses to overwrite a differing file without `--update` |
| `docs\tools\check_v7_sync.py` | (a) every vendored file still has the manifest's hash (somebody edited a copy → exit 1); (b) when the V7 folder exists (default `%USERPROFILE%\Desktop\independent laboratoryv7`, or `--v7`): every `v7` file equals its source, printed per file as “same” / “different (V7 It was changed over there)” / “Source does not exist” (exit 2 when any differs). Without the V7 folder it says so and does (a) only |
| at program start | `pumpboard.load_v7` does check (a) for `v7\backend\**` and `v7\config\hardware.yaml`; the result is the line “Set source” of Maintenance › Pump and Scale and an event when it fails. It does not block |

## F.4 Launchers

| File | 2.0.0 |
|---|---|
| `Start.cmd` | stays; the Python call gets `-B` (no `__pycache__` inside `pc\vendor`) |
| `Start-Demo.cmd` | stays; `-B`; comment: two simulators (RPi on 127.0.0.1:8767, pump board inside the program), no real port is opened |
| `Start-Kiosk.cmd` | stays; `-B` |
| `Set-PIN.cmd` | stays; `-B`; comment: sets or replaces the maintenance PIN; without a PIN the maintenance area stays closed |
| `Install.cmd`, `Flash-Nano.cmd`, `installer\unpack-python.cmd` | unchanged |
| new launchers | none |

`app.py --set-pin` prints “Maintenance PIN has been saved. forget PIN , double-click again Set-PIN.cmd Set new. ” (the old hint to delete `pin.json` would now close the maintenance area).

## F.5 Installer

`installer\install.py`: the required-file list of `step_computer` is replaced (new modules, `pc\vendor\v7\backend\transport.py`, `pc\vendor\pylib\serial\__init__.py`, `pc\vendor\pylib\yaml\__init__.py`, `pc\web\js\main.js`; `pc\web\app.js` removed); the closing text gains two lines: “First time use: Double click Set-PIN.cmd Setup maintenance PIN.” and “To use a pump: After opening the program, go to ”Maintenance › Connect”Select a port for the pump control board.”. The installer never lists or opens a COM port. Nothing else changes; `pc\data\config.json` keeps its shape.

## F.6 Version numbers

| Thing | Version |
|---|---|
| PC program (`pc\app.py` `VERSION`) | 2.0.0 |
| RPi TCP server (`rpi\tcpd\plasmic_tcpd.py` `VERSION`) | must change from 1.3.0, otherwise `Install.cmd` sees "same version" and does not push the single-flow server (`current-ec.md` §0.3). Proposed 2.0.0. Owned by the RPi review, not by a package of section H |
| TCP protocol | 1 (unchanged) |
| Nano firmware | 1.2.1 (unchanged) |
| Mega firmware | as flashed; V7 calls itself 5.0.0 (unchanged) |
| data folder | `data_version` 2 |

---

# G. Test plan

`unittest`, standard library, each file run alone with `pc\runtime\python\python.exe -B`. Only 127.0.0.1. Every test file that can reach the pump board starts with `import noserial`.

`tests\noserial.py`: sets `PLASMIC_NO_SERIAL=1` before anything is imported; after `pumpboard.load_v7` it asserts that `backend.transport.serial.Serial` is the forbidden stub; offers `assert_no_real_port(board)`. A test that would open a real port fails with `BoardError('forbidden_port')` turned into `AssertionError('a test tried to open a real serial port')`.

| File | Owner | Important cases |
|---|---|---|
| `test_tcpd.py`, `test_measure.py`, `test_flash_nano.py`, `simulated_rig.py`, `simulated_spectrometer.py` | nobody (unchanged) | stay green |
| `test_installer.py` | D | file lists updated; `.cmd` ASCII; launchers call Python with `-B` |
| `test_app.py` | C | the three sequence tests are deleted; the rest moves to the new routes: static files and guide, token / origin / host, parameters → run saved, stop cancels a discharge, screen watchdog for the maintenance discharge, RPi found at another address, new Nano sync, offline texts |
| `harness.py` | C | `Harness(sim RPi + Application(simulate board) + HTTP)` for all other files; `speed=40`, board `speedup=20` |
| `test_api.py` | C | route table complete; **PIN matrix** table-driven over every PIN route: no PIN file → 403 `pin_unset`; no session → 403 `locked`; session → not 403; operator routes → never 403; `/api/state` never renews a session; session ends after the idle time (patched to 1 s); replacing `pin.json` ends sessions; delete needs the PIN again; practice PIN 000000; static rules (traversal, unknown extension, `text/javascript` for modules, fonts, CSP on every reply); every top-level key of C.5 present; `/api/runs/<job>?frames=1` gated; body limit; `/api/stop` always 200 |
| `test_migrate.py` | C | a synthetic 1.3.0 folder (config, pin, events, runs with `sequence`, an active `schedule.json`): after migration the old files are byte-identical, the schedule copy is `interrupted`, one Recipe and one operator are seeded, `pumpboard.json` has `enabled: null`, `version.json` exists; a second run changes nothing; a fresh folder; a folder already at version 2 |
| `test_vendor.py` | V | manifest hashes; no `.pyd`, no `__pycache__`; licence files present; `serial` and `yaml` import from `pc\vendor\pylib` under the bundled Python; `hardware.yaml` validates through `backend.config`; exactly 26 functions; `backend\main.py` absent |
| `test_recipe.py` | R | header and limits; every action's range, step, unit auto-fill; one failing and one passing CSV for each of P3–P12; expansion and display rounds; phases; estimates of the three shipped Recipes; `nano_stop` accepted; BOM; CSV round trip; syringe size 1.25 mL makes a 5 mL Recipe invalid |
| `test_pumpboard.py` | B | **port safety**: `enabled: null` and `false` never construct a `Link`; `load_v7` leaves `cfg['link']['port']` at the sentinel; a real open raises; identity: simulated port lists (one Mega, two Megas, a CH340 only, renumbered COM, missing); wrong device → closed and **not** retried; busy port text; connect traffic in order (`?`, `X`, `W,DIV`, `W,OFFSET`, `W,AVG`, `W,STABLE`, `W,STREAM`, `?`); run-time limit stops the motor; start on a busy group refused; `stop_motor` verified; `dose_volume` waits for `idle`; weight dose result and cancel; clean wait and abort; syringe needs init, error code becomes `BoardError`, content estimate; injected `READY` → epoch, settings re-applied, running operation fails; lost link → status, reconnect, `X` first; `stop_all` while disconnected → `ok: false`; weight stream restarted after stop-all; `close()` sends `X`; calibration goes to the data folder and the vendored file's hash is unchanged |
| `test_interlock.py` | X | every row of B.6 from hand-made views; `rewrite_fix` |
| `test_runner.py` | X | plasma-only round: run saved, record complete (CSV, parameters, job); pump + plasma round: order of serial and TCP commands; **interlocks**: start refused while a manual pump runs; `plasma_run` refused when a motor is not idle, when the board is enabled but disconnected; allowed when the board is not in use; **stop paths**: STOP in a delay, in a dose, in a clean, in a discharge (job cancelled on the RPi), each ends `stopped` with `X` and RPi `stop` sent; failed step → common exit, schedule ends; **pause**: inside a delay (rest kept), with a pump on (suspended, restored with the remaining time), during a dose (`pausing` first); board reset mid-round → failed; result read tried twice, never a second `plasma_run`; weight dose out of tolerance → alarm, not failure; record written at start; `interrupted` after a restart |
| `test_schedrun.py` | X | count 1 = one round; operator required; tick-box flag required only with plasma; three `not_started` end it; overrun skip; pause → `skipped_paused`; resume refused while a manual pump runs; manual start refused while `waiting`, allowed while `paused` between rounds; restart → `interrupted`; files under `schedules\` |
| `test_web.py` | W-frame | every `import` in `pc\web\js` resolves; no `innerHTML`, `eval(`, `new Function`, `style="`, `on…="` in the web files; `index.html` loads only `/css/` and `/js/`; LF, UTF-8 without BOM; every page section has a module; every status key of `schedule.py` and `runner.py` appears in `labels.js` |

Not automated; written into the guide as a check-list: the screen in practice mode at 1024 × 768 and at a larger window (nothing clipped, no scroll bar), every dialog of E.17 opened once.
On the real rig, by the owner only: section J.

---

# H. Build plan

Implementers work in one shared folder without git. **A file belongs to exactly one package.** Nobody edits another package's file; a needed change is asked from its owner through the lead.

## H.1 Packages and files

| Package | Files (all paths under `autonomous control\`) |
|---|---|
| **V** vendor | `pc\vendor\**` · `pc\web\fonts\**` · `docs\tools\vendor_v7.py` · `docs\tools\check_v7_sync.py` · `tests\test_vendor.py` |
| **R** recipe language | `pc\recipe.py` · `pc\recipestore.py` · `pc\recipes\plasma-only.csv` · `pc\recipes\dose-and-measure.csv` · `pc\recipes\clean.csv` · `tests\test_recipe.py` |
| **B** pump board | `pc\pumpboard.py` · `tests\noserial.py` · `tests\test_pumpboard.py` |
| **C** core | `pc\app.py` · `pc\httpd.py` · `pc\store.py` · `pc\migrate.py` · `pc\pinlock.py` · `pc\rpilink.py` · `pc\operators.py` · `tests\harness.py` · `tests\test_app.py` · `tests\test_api.py` · `tests\test_migrate.py` |
| **X** execution | `pc\interlock.py` · `pc\runner.py` · `pc\schedrun.py` · `pc\schedule.py` · `pc\manual.py` · `pc\alarms.py` · `tests\test_interlock.py` · `tests\test_runner.py` · `tests\test_schedrun.py` |
| **W-frame** | `pc\web\index.html` · `pc\web\css\base.css` · `pc\web\css\components.css` · `pc\web\js\main.js` `api.js` `store.js` `dom.js` `dialog.js` `labels.js` `frame.js` `chart.js` · `tests\test_web.py` · deletes `pc\web\app.js` and `pc\web\style.css` |
| **W-pages** | `pc\web\css\pages.css` · `pc\web\js\pages\dash.js` `schedule.js` `manual.js` `runs.js` `alarms.js` |
| **W-maint** | `pc\web\css\maint.css` · `pc\web\js\pages\maint.js` `maint-recipe.js` `maint-link.js` `maint-plasma.js` `maint-pumps.js` `maint-spectra.js` `maint-operators.js` |
| **D** delivery and documents | `Start.cmd` · `Start-Demo.cmd` · `Start-Kiosk.cmd` · `Set-PIN.cmd` · `installer\install.py` · `tests\test_installer.py` · `READ-ME-FIRST.txt` · `historical-development-notes.md` · `docs\work record.md` · `docs\1-How to continue on another computer.txt` · `docs\guide-source\**` · `detailed-guide.html` |
| nobody | `rpi\**` (separate review) · `nano\**` · `SHA256SUMS.txt` · `pc\netcheck.py` · `pc\plasmic_client.py` · `pc\examples\**` · `pc\runtime\**` · `pc\data\**` · `tests\test_tcpd.py` `test_measure.py` `test_flash_nano.py` `simulated_rig.py` `simulated_spectrometer.py` · `docs\tools\check_sums.py` |

## H.2 Order

| Stage | Packages (parallel inside a stage) | Done when |
|---|---|---|
| 1 | **V** · **R** · **C** (skeleton: modules split, every route of C.3 / C.4 answering the documented shapes, board and runner as stubs returning the default views of C.5, migration) · **W-frame** | `test_vendor`, `test_recipe`, `test_migrate` green; the frame renders from `/api/state` in practice mode |
| 2 | **B** (needs V) · **X** (needs R, and B's method list of B.4.5 — it can start against a simulated board with the same methods) · **W-pages** and **W-maint** (need W-frame's `ctx` and CSS) | `test_pumpboard`, `test_interlock`, `test_runner`, `test_schedrun` green |
| 3 | **C** (wires B and X into `Application`, finishes `test_app`, `test_api`) · **D** | all test files green; `check_sums.py` and `check_v7_sync.py` clean; practice-mode check-list done; guide rebuilt with new screenshots |

## H.3 What each implementer needs to know

| Package | Read | Interfaces it provides | Traps |
|---|---|---|---|
| V | F.1–F.3 | the folder layout; `MANIFEST.json` | copy with a binary copy, never through an editor; no `__pycache__`; the `.venv` is read from, nothing is installed; stay out of the HMI's `data` folder |
| R | section A | `ACTIONS`; `parse(text) -> steps` (`ValueError` with the Chinese message and `.line`); `preflight(steps, facts) -> dict`; `estimate(steps, facts, plasma_s)`; `to_csv(rows)`; `RecipeStore(dir, templates)`: `list() get(id) publish(name, csv, source) delete(id) templates() seed()` | `facts` (motor groups, valve links, limits, syringe size) is passed in — `recipe.py` imports nothing from the vendor folder, so its tests need no board |
| B | B.4, `v7-integration-notes.md`, the vendored `transport.py` | `PumpBoard(data_dir, simulate, on_event, speedup=1)`: `start() close() view() facts() ports() configure(...) reconnect()` and the methods of B.4.5; `BoardError` | never call `transport.get_link()`; never edit a vendored file; `cfg` is process-wide; V7's `motors.stop` on a motor that is not selected answers `OK` and does nothing; `X` switches the weight stream off; a `PUMP,` line with `error≠0` is a fault; **never run anything that could open COM3** — tests only with `noserial` |
| C | B.1–B.3, B.9, B.10, C, D | `Application` with attributes `store pin link board recipes operators runner schedule manual alarms demo`; `Store.event(text, level, actor)`; `Link.claim(job)`, `Link.fetch_result(job)`; `harness.Harness` | `pc\data` is the owner's real folder: tests use temp folders; keep `config.json`'s shape; the `/guide` exception in the CSP; replace `netcheck.local_ipv4` in tests |
| X | A.3 (rules), B.5–B.8, B.10 | `RoundRunner(app)`: `start(recipe, operator, slot, hv_confirmed) pause() resume() stop(reason) view() join()`; `ScheduleRunner(app)`: `preview(...) start(...) pause() resume() request_end() view() close()`; `Manual(app)`: `do(body, actor)`; `AlarmBook`: `evaluate(app) raise_latched(...) ack(...) view()`; `interlock.*` | no lock held while calling a device; STOP must work from any state; never repeat a discharge; a paused round keeps ownership |
| W-frame | E.1–E.3, E.18, C.1–C.5, the style appendix of `hmi-ui-style.md` | `ctx`, the dialog functions, `dom.list`, `labels` | CSP: no inline anything; sizes are the original's; text only through `textContent` |
| W-pages | E.4–E.9 | the five page modules | a disabled control needs its reason on screen; never rebuild a row under the pointer |
| W-maint | E.10–E.17 | the maintenance modules | nothing behind the PIN is polled automatically; the draft Recipe survives a reload |
| D | F.4–F.6, I | launchers, installer text, documents | `.cmd` ASCII + CRLF; `READ-ME-FIRST.txt` UTF-8 BOM + CRLF; edit the guide **source** and rebuild; headings that programs cite by name must match |

Rules for everybody: review-workspace never makes a real device act (no discharge, no pump, no COM3, no 192.168.1.1); simulators only. Python always with `-B`. `.py` `.js` `.css` `.html` `.md` with LF. After any change `docs\tools\check_sums.py`.

---

# I. Documentation changes (list only)

| Document | Change |
|---|---|
| `docs\guide-source\installation-and-usage.src.html` | Look here first: seven steps (new: Setup maintenance PIN; pump control board) · Safety instructions: pumps have no watchdog; STOP is not an E-stop; pumps and high voltage never together · What does the whole set look like?: diagram with the Mega on USB · Steps 4 One click installation: closing lines · new Steps: Setup maintenance PIN (`Set-PIN.cmd`) · new Steps: pump control board (close V7 / Arduino IDE, choose the port, what the three error texts mean) · Step ”First power-on check”: check-list rewritten for 2.0.0 (plasma-only Recipe first, then pumps one by one, then a pump Recipe) · Daily operation: one sub-section per page (Overview, Schedule measurement, manual operation, execution record, alarm) · new Maintenance: one sub-section per tab; Recipe language table and the three examples · when something goes wrong: rows for every alarm text and every pump-board status text · References: API routes, data files, migration from 1.3.0, vendored files and the sync check · Verification status: what has and has not run on the rig · remove: “Two orders”, “fast/original”, the TCP terminal on screen, 3 background frames, `test_quick.py` |
| `docs\guide-source\shots\*`, `shots_app.py`, `README.txt` | seven new screenshots (Overview, Start scheduling dialog, Schedule measurement, manual operation, execution record detail, Maintenance › connection, Maintenance › Recipe); placeholder list updated |
| `detailed-guide.html` | rebuilt with the new test count |
| `READ-ME-FIRST.txt` | version 2.0.0; what is new in six lines; first use: `Install.cmd` → `Set-PIN.cmd` → `Start.cmd` → Maintenance › connection; practice mode; remove the sequence text |
| `historical-development-notes.md` | architecture picture with the Mega; file table (new modules, `pc\vendor`, tools); rules: never open COM3, vendored files are not edited (`check_v7_sync.py`), `plasma_measure.py` instead of `plasma_quick.py`, PIN model, "PLC and pump have been removed" replaced by the pump board; commands: all test files and the new count; state of verification |
| `docs\work record.md` | new section 13: the 2.0.0 merge — decisions, what was built, what is unverified, the open list |
| `docs\1-How to continue on another computer.txt` | the other PC needs the pump board choice again (`pumpboard.json` stores a COM name and USB identity) |
| `installer\install.py` texts | F.5 |

---

# J. Risks, and what stays unverified until the owner tries it on the real rig

| # | Item | Why it is open | What the design does about it | First check on the rig (owner presses) |
|---|---|---|---|---|
| 1 | Opening a real COM port from the bundled Python | importing pyserial was proven; an open was never tried (nobody may open COM3) | errors of the open become readable texts; nothing else depends on it | Maintenance › connection → Select port → “Connected COM3” |
| 2 | Does the Mega reset when the port is opened | inferred, not tested | not relied on: `X` is sent after every connect; an unexpected `READY` is handled | pumps stay still after connecting; “firmware response” shows the `READY` line or “—” |
| 3 | **The Mega has no watchdog** | firmware fact; it is not changed | run-time limits on the PC, stop on exit and on a lost page, console close handler, reconnect + `X`, Emergency alarm with the instruction to cut the pump power; the guide says it plainly | start a motor with 30 s, close the program window: the motor must stop within about 3 s. Pull the USB cable while a motor runs: it will **not** stop — know where the 24 V switch is |
| 4 | Syringe pump: `Y,ST` and its `ready` bit during a stroke, the error codes | not in V7's code; the codes come from a wiring page | wait by computed time first, then ask; a wrong assumption shows as a clear time-out text, not as a silent error | manual operation: initialization, Liquid extraction 1 mL, Liquid injection 1 mL with water |
| 5 | Dose by volume: "finished" = group `idle` | no `DONE` exists in the firmware | poll, time limit above `eta_s`, stop-all on time-out | Quantitative 10 mL into a measuring cylinder; compare time (≈ 16 s) and volume |
| 6 | Dose by weight on the real scale; calibration from 2026-08-26 | never run from this program | V7's function unchanged; result and tolerance recorded | Maintenance › Pump and Scale: Empty cup returns to zero, Correction with a known weight; then Quantitative 10 g |
| 7 | Firmware constants vs `hardware.yaml` (cleaning times, flow coefficient) | the board cannot report its build | fingerprint check of two values, shown in Maintenance › connection | “Firmware Settings and hardware.yaml consistent”; time one auto-clean (≈ 121 s) |
| 8 | The unified measuring flow on the RPi | written 2026-10-06, only on the pretend rig; under separate review | this design only calls `plasma_run` | the 30 V check of the guide, with “Discharge measurement only” |
| 9 | "Not more than 5 seconds" from Start scheduling to the discharge | depends on the RPi's `start_s` (≈ 4 s) plus under 0.5 s on the PC | the plasma-only Recipe is one step; the interlock uses the cached status | “discharges in a few seconds after pressing” in the detail of execution record |
| 10 | RPi server version | must differ from 1.3.0 or the installer will not update it | named in F.6 for the RPi review | `Install.cmd` reports the new version |
| 11 | Pumps and high voltage in one round | never run together on the rig | preflight + run-time check + common exit | the template “Measure after adding liquid” with water and 30 V, after items 4–8 |
| 12 | Stop during a discharge, the RPi watchdogs | listed as unverified in `historical-development-notes.md` since 1.3.0 | unchanged mechanism | “Stop STOP” during a 30 V discharge |
| 13 | Folder coming from 1.3.0 without a PIN | behaviour change: maintenance closed until `Set-PIN.cmd` | Overview says so in line 1; guide | — |
| 14 | Seeded operator “Operator” | an invented name, to make day one work | maintenance replaces it; the lead may prefer an empty list (then line 4 of “Before you start using it” sends the owner to Maintenance › operator) | — |
| 15 | The step editor of Maintenance › Recipe | larger than the original (which only imported files) | one file; CSV import and export work without it; can be cut by removing the row menu and “New Recipe” | — |
| 16 | The on-screen TCP terminal is removed | nobody asked for it to stay; the command-line tool remains | the lead may keep it as a seventh tab; the route was `POST /api/raw` | — |
| 17 | Text widths | Chinese is drawn by the system font, not by Barlow; widths were estimated, not measured | ellipsis everywhere, long text opens in a dialog | practice-mode check-list at 1024 × 768 |
| 18 | A touch panel without a keyboard | the three text fields need a keyboard (Windows' touch keyboard works) | numbers never need one | — |
| 19 | The RPi's fix texts name old buttons | the RPi side is not changed | string replacement in `interlock.rewrite_fix`; if the RPi texts change the replacement silently stops matching | read the reasons in “Unable to start schedule at this time” once |
| 20 | Closing the console window gives Windows about 5 s | system limit | `close_fast()` sends `X` first, within 3 s | item 3 |
| 21 | V7's module state is process-wide (config cache, weight-dose lock, calibration path from an environment variable) | by V7's design | one board object per process; tests create boards one after another | — |
| 22 | Motors 3–7 and the syringe valve positions have no names | V7 defines none | shown as “motor 3”, “Valve position 1”; names come from `hardware.yaml` when the owner adds them in V7 and syncs | — |
| 23 | pyserial licence text | not in the wheel | written by hand, flagged in F.2 | — |
| 24 | The EC working tree is in a mixed state today (PC side still has the two sequences, `test_app.py` cannot pass) | another session was converting it | package C replaces those parts completely | — |
