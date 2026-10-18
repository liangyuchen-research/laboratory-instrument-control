# Proposal "lean integration" — Plasmic Electronic control 2.0.0

Angle: **the smallest safe change**. Extend EC 1.3.0 in place, reuse its classes and tests, call the vendored V7
functions through one new wrapper, rebuild only the web page. Written 2026-10-06 from `constraints.md`, the code maps
in `scratchpad\understand\`, the screenshots, and a full read of `pc\app.py`, `pc\schedule.py`, `tests\test_app.py`,
V7 `backend\transport.py`, `registry.py`, `config.py`, `functions\{motors,pump,scale}.py`, `config\hardware.yaml`,
the original's `csv_workflow.py`. Nothing was run, no port was opened, nothing outside `scratchpad\design` was written.

Abbreviations: EC = `C:\Users\PlasmaLab\Desktop\autonomous control`; V7 = `C:\Users\PlasmaLab\Desktop\independent laboratoryv7`;
HMI = `C:\Users\PlasmaLab\Desktop\UI\UI\hmi_v1_1`. "Board" = the Arduino Mega pump board. Paths without a root are
relative to EC.

---

## 0. The design in one page

| Question | Answer |
|---|---|
| What stays byte-for-byte | `PinLock`, `write_json`, `validate_config`, `ExclusiveHTTPServer`, the Host/Origin/token checks, the CSP, `pc\netcheck.py`, `pc\plasmic_client.py`, every `.cmd`, the installer flow, `SHA256SUMS.txt`, the whole `rpi\` tree (apart from the one-line version bump that belongs to the RPi review) |
| What is edited | `pc\app.py` (about −80 / +380 lines), `pc\schedule.py` (+6 lines), `tests\test_app.py` (3 tests replaced, 3 helpers touched), `tests\test_installer.py` (2 lists), `installer\install.py` (1 list) |
| What is new (Python) | `pc\recipe.py` (≈330 lines), `pc\mega.py` (≈520), `pc\runner.py` (≈380), `pc\alarms.py` (≈130), `docs\tools\check_vendor.py` (≈90) |
| What is new (vendored, unchanged) | `pc\vendor\v7\…` (V7 backend without `main.py`, config, firmware, generator), `pc\vendor\python\{serial,yaml}\…`, `pc\web\fonts\…` |
| What is rewritten | `pc\web\*` (the page): same server, same poll, the original HMI's frame |
| `pc\data\config.json` | **schema unchanged** (`rpi_host`, `rpi_port`, `token`). The installer rewrites this file with exactly these three keys, so pump-board settings live in a separate `settings.json`. No migration code touches the owner's connection settings |
| Pump board default | `mega_port: "off"`. Nothing ever opens a COM port until the owner chooses the port once in Maintenance › connection. Old data folders and every test therefore start with the board off |
| One way to measure | Schedule measurement with a Recipe; a single measurement is `common 1 Second-rate` with the built-in Recipe “Discharge measurement only” (one row: `plasma_run`). The slot body of 1.3.0's `Schedule._loop` becomes "run one round" |
| Round runner | a blocking method called **on the existing schedule thread** (no extra flow thread) |
| Alarms | computed on the server (`pc\alarms.py`, a pure function of the state) so they are covered by `unittest`; acknowledgement in server memory |
| PIN | `PinLock` as it is. Decision: **no PIN file = maintenance open, with a permanent notice** (1.3.0 behaviour; the owner "can set" a PIN with `Set-PIN.cmd`). The stricter original rule (no PIN = maintenance closed) is a 3-line change, listed in §J as a choice for the lead |
| RPi | protocol untouched. The PC refuses plasma steps when `hello.flow` is missing (RPi still on 1.3.0) and says “Please double-click first Install.cmd renew RPi”. Update = the existing tcpd-only path, no reboot |

Not built in 2.0.0 and why that is still the request: §K.

---

## A. Recipe language

A Recipe is a published CSV with the header `action,target,value,unit,note` (exactly, as the original). It describes
**one round**. No variables, no branches, no sensor conditions. It is checked when published and again at every start.

### A.1 Limits

| Limit | Value | Why |
|---|---|---|
| File size | 200 000 bytes UTF-8 (BOM allowed) | original |
| Source rows | 300 | original 500; 300 keeps `/api/state` small |
| Expanded steps | 500 | original 2000; the page lists 8 steps per page, the state carries every step once a second (≈35 KB at 500) |
| `repeat` nesting | 3 | original 5; nobody needs more on this rig |
| `repeat_start` value | 1–20 | original |
| Sum of all `delay` | ≤ 7200 s | original |
| One continuous pump run | ≤ 600 s (`MAX_RUN_SECONDS`) | host-side maximum, see A.4 rule 6 |
| `note` | cut at 200 characters | original |
| Empty rows | skipped | original |

### A.2 Action table

"Fn" = vendored V7 function called through `registry.get(name).invoke(Context(link, config), payload)`; "RPi" = TCP
command over the existing `Link`. Every wait checks the stop flag at least every 0.2 s. "Fail" always means: the step
gets the error text, the round ends `failed`, then the clean-up of B.4 runs (pump stop-all when the round used the
board, RPi `stop` when an RPi job may still run). A failed round ends the schedule (fixed decision 8).

| action | target | value | unit | What it does | Maps to | Finished when | Time limit | On failure |
|---|---|---|---|---|---|---|---|---|
| `delay` | empty | 0–3600, step 0.1 | `s` | waits; paused time does not count | runner only | time is over | — | — |
| `pump_on` | `M2`…`M9` | 0.1–80, step 0.1 | `mL/min` | continuous run, forward (Forward rotation). The host arms a maximum time = planned on-time + 5 s | Fn `motors.run {motor, flow_ml_min, direction: 1}` → `V,<m>,<sps>`, `?` | `OK` and the group shows this motor in `run` (M2/M9: `wait` then `run` within 3 s) | 3 s per command, 3 s to reach `run` | Fn `motors.stop`, fail |
| `pump_reverse` | `M2`…`M9` | 0.1–80, step 0.1 | `mL/min` | the same, reverse (reverse) | Fn `motors.run {…, direction: -1}` | same | same | same |
| `pump_off` | `M2`…`M9` | empty | empty | stops that motor | Fn `motors.stop {motor}` → `V,<m>,0`, `?` | group `idle`; M2/M9 also valve bit 0 | 5 s | stop-all, fail, alarm “Pump stop command not confirmed” (priority 1) |
| `dose_volume` | `M2`…`M9` | 0.01–1000, step 0.01 | `mL` | finite forward dose at V7's fixed 40 mL/min; ends by itself in the firmware | Fn `motors.dose {motor, volume_ml, direction: 1}` → `P,<m>,<steps>,2825`, `?` | poll `?` every 0.3 s: group `idle`, selected motor never changed; M2/M9 also valve bit 0 | `eta_s × 1.5 + 10 s` (`eta_s` from the Fn reply) | Fn `motors.stop`, fail |
| `dose_weight` | `M2`…`M9` | 0.01–1000, step 0.01 | `g` | closed loop on the scale, zero = weight at start (V7 procedure, 40 → 10 → 3 mL/min) | Fn `motors.dose_by_weight {motor, target_g, direction: 1}` (blocks) | the Fn returns. `within_tolerance: false` → step is `done` with a warning and alarm “The weighing exceeds the allowable range” (priority 3), as V7 treats it | V7's own 300 s / 30 s / 4 s; host hard limit 480 s → stop-all | fail with V7's text |
| `clean` | empty | empty | empty | firmware cleaning: water 30 s → water＋discharge 45 s → Empty 45 s (motors 2 and 8, compiled in) | Fn `motors.clean {}` → `?`, `C`, `?` | reply `started: true`, then poll `?` every 1 s: `cleaning` false, both groups `idle`, valves closed | `(total_pump_timeline_s + 2) × 1.25 + 15` = 167 s | stop-all, fail |
| `syringe_init` | empty | empty | empty | homes the plunger (liquid in the syringe is pushed out); afterwards applies the saved speed | wrapper `link.send('Y,INIT', timeout=5)` (the command `pump.init` sends, with a shorter host wait), then Fn `pump.set_speed` | `PUMP,` line with `error=0`, then `Y,ST` polled every 0.3 s until `ready=1` | 30 s | fail |
| `syringe_speed` | empty | 0.001–2.5 (≤ limit of the syringe size), step 0.001 | `mL/s` | sets the plunger speed for the following moves | Fn `pump.set_speed {speed_ml_s}` → `Y,SPEED,<raw>` | `PUMP,` line with `error=0` | 3 s | fail |
| `syringe_aspirate` | `V1`…`V3` | 0.001–5 (≤ syringe size and ≤ free volume), step 0.001 | `mL` | turns the head valve to that position, then draws in | Fn `pump.set_valve {position}`; wait ready; Fn `pump.aspirate {volume_ul}` | waits `volume ÷ speed` (at least 0.5 s), then `Y,ST` every 0.3 s until `ready=1`. Any `PUMP,…,error=<n≠0>` = failure | `volume ÷ speed × 2 + 10 s` | Fn `pump.stop`, fail “Syringe pump report error n” |
| `syringe_dispense` | `V1`…`V3` | 0.001–5 (≤ volume held), step 0.001 | `mL` | turns the head valve, then pushes out | Fn `pump.set_valve`; Fn `pump.dispense {volume_ul}` | same | same | same |
| `scale_tare` | empty | empty | empty | zero the scale (V7 behaviour: also saves the offset to the calibration file) | Fn `scale.tare {}` → `W,TARE`, `W,CFG` | Fn returns | 3 s per command | fail |
| `rpi_status` | empty | empty | empty | fresh read of HSPY/Nano; fails when the supply is ON, D13 is HIGH or the read is invalid | RPi `status {fresh: true}` | reply | 40 s | fail |
| `nano_prepare` | empty | empty | empty | writes `parameter.json` into the Nano, no output | RPi `prepare` | reply `prepared` | 60 s | fail |
| `plasma_run` | empty | empty | empty | the unified measurement (1 background frame, then discharge + signal frames); spectra saved to `runs\` | interlock B.5-I2, `rpi_ready`, RPi `plasma_run {confirm_high_voltage: true}` (unattended) | status shows the job `done`; `Link` fetches and saves the result | 360 s; no status for 30 s → fail | never repeated; RPi `stop`; fail |
| `spectrum_capture` | empty | 1–10000, integer | `ms` | 3 frames without high voltage | RPi `spectrum {integration_ms}` | job `done` | `3 × ms ÷ 1000 + 60 s` | fail |
| `nano_stop` | empty | empty | empty | HSPY OFF + Nano STOP with read-back | RPi `stop` (fresh connection, as the STOP button) | reply `ok` | 120 s | fail |
| `power_off` | empty | empty | empty | the same as `nano_stop` (both names kept from the original) | RPi `stop` | same | same | fail |
| `repeat_start` | empty | 1–20, integer | empty | repeats the rows up to its `repeat_end`; expanded before anything runs | — | — | — | — |
| `repeat_end` | empty | empty | empty | closes the block | — | — | — | — |

Notes on choices:
- `M2`…`M9` are V7's motor numbers. The labels shown on screen come from the vendored `hardware.yaml`
  (`motor 2 · water`, `motor 8 · OUT`, `motor 9 · CuSO₄`, others `motor n`). Nothing else is named.
- Direction is in the **action name**, not in a sign: a stray `-` can then never reverse a pump; a typo gives
  “Unsupported command”.
- `pump_on` carries the flow in `value`. The time limit is not typed by the author: it is the planned on-time the
  preflight computes (rule 6), enforced by the host. One number less per row, and it cannot disagree with the delays.
- The syringe keeps the original's shape (target = channel, value = mL); the channel is the head-valve position.
  V7 does not say what liquid is on which position, so the screen says “Valve position 1/2/3” only.
- Weight dosing is forward only (V7's page does the same; a reverse run can only end in the 30 s error).

### A.3 Field rules (parser)

Header must be exactly the five names. Every row must have five cells. `action` not in the table →
“No. N Column does not support command 'x'”. A cell that the table shows as "empty" must be empty. Numbers are parsed with `Decimal`
(finite, in range, on the step). Unit must match exactly. Texts follow the original's style:
“No. N List pump_on Must use M2–M9 and mL/min”, “flow rate must be between 0.1–80”, “The minimum unit is 0.1”.

### A.4 Preflight rules (before any hardware, on the expanded list)

| # | Rule | Message |
|---|---|---|
| 1 | Motor exists in `hardware.yaml` | “No. N column has no number n motor” |
| 2 | One motor per group at a time: `pump_on`/`pump_reverse` while another motor of the same group runs, or the same motor twice | “No. N List: same group (A group) can only transfer one at a time, please first pump_off motor n” |
| 3 | `pump_off` only for a motor that is running | “No. N Lemada n has not started yet” |
| 4 | While any continuous pump runs only `delay`, `pump_on`, `pump_reverse`, `pump_off` are allowed. No dose, cleaning, syringe, scale or RPi step | “No. N Column execution x ” |
| 5 | **Pumps and high voltage never together**: follows from rule 4 (RPi steps need all pumps off) and from the fact that every other board step is blocking and has finished before the next row | same message |
| 6 | Planned on-time of each continuous run = sum of the delays up to its `pump_off`; must be > 0 and ≤ 600 s. It is stored in the step (`planned_s`) and shown in the Recipe view as “longest running time N Second” | “No. N Lemada n continuous operation S seconds, exceeding the upper limit 600 Second” |
| 7 | Every continuous pump is switched off before the end | “Is there still a pump at the end of the process? pump_off: motor n” |
| 8 | Syringe: the first move must come after a `syringe_init` in the same Recipe. From there a virtual volume is tracked: aspirate beyond the syringe size, or dispense more than is held, is refused | “No. N The syringe pump must be syringe_init”, “No. N The syringe capacity is exceeded after liquid extraction. 5 mL”, “No. N The amount of liquid injected is more than the amount in the syringe” |
| 9 | `syringe_speed` ≤ the limit for the saved syringe size (2.5 mL/s for 5 mL, 0.625 mL/s for 1.25 mL) | “No. N Column speed exceeds the upper limit x mL/s” |
| 10 | Sum of delays ≤ 7200 s; at least one executable step | “Single process delay The total length shall not exceed 7200 Second”, “CSV No executable action” |

Warnings (shown, never block): more than one `plasma_run` per round; a round estimate longer than the schedule
interval (existing warning text “…is longer than the interval: some periods will be skipped because the last time has not ended yet.”).

Summary returned by `recipe.check()`: `step_count`, `rounds`, `delay_seconds`, `uses_plasma`, `uses_rpi`,
`uses_mega`, `uses_syringe`, `uses_scale`, `motors` (list), `warnings`, and per step `text` (the sentence the screen
shows, e.g. “motor 8 · OUT Forward rotation 40 mL/min (longest running time 50 seconds)”).

Estimate of one round (for the schedule preview): delays + per step — `pump_on/off` 1 s (+1 s for M2/M9),
`dose_volume` `mL × 4558.96 ÷ 2825 × 1.1 + 1` (+2 s for M2/M9), `dose_weight` `g ÷ 0.6 + 25`, `clean` 122,
`syringe_init` 10, syringe move `mL ÷ speed + 2`, `syringe_speed`/`scale_tare` 1, `rpi_status` 3, `nano_prepare` 6,
`spectrum_capture` `3 × ms ÷ 1000 + 8`, `nano_stop`/`power_off` 5, `plasma_run` = `status.estimate_s` of the RPi
(30 when unknown).

### A.5 Example Recipes

1 — `Only discharge measurement` (seeded at first start; works with the board set to “Not used”):

```csv
action,target,value,unit,note
plasma_run,,,,discharge＋capture spectrum (background 1 open) 
```

2 — `Add liquid＋discharge (template)` (shipped as a file for import, **not** seeded: the numbers are placeholders the owner must
replace; the design does not know the plumbing):

```csv
action,target,value,unit,note
dose_volume,M2,10,mL,motor 2 · Water: add 10 mL
dose_weight,M9,1.00,g,motor 9 · CuSO₄: Add to scale 1.00 g
delay,,10,s,wait 10 Second
plasma_run,,,,discharge＋capture spectrum
pump_on,M8,40,mL/min,motor 8 · OUT: starts to discharge
delay,,45,s,discharge 45 Second
pump_off,M8,,,Stop discharge
```

3 — `automatic cleaning` (seeded):

```csv
action,target,value,unit,note
clean,,,,automatic cleaning (water 30 Second → water＋discharge 45 Second → Empty 45 seconds)
```

---

## B. PC program structure

### B.1 Modules

| File | Status | Content |
|---|---|---|
| `pc\app.py` | edited | constants, `write_json`, `validate_config`, `PinLock` (unchanged), `Store` (+ the operator list; `save_run` writes `flow` instead of `sequence`), `migrate()`, `Link` (sequence code removed, `hv_busy()` added), `Schedule` (slot body = one round; pause/resume), `Application` (wiring, `state()`, `command()`), HTTP handler, `main()` |
| `pc\schedule.py` | edited | time arithmetic unchanged; `ACTIVE` gains `'paused'`, new `BUSY = ('waiting','running','stopping')`, two labels |
| `pc\recipe.py` | new | `parse`, `preflight`, `check`, `estimate`, `RecipeStore`; pure, no hardware imports |
| `pc\mega.py` | new | `PumpBoard`: the only code that imports the vendored V7 modules |
| `pc\runner.py` | new | `RoundRunner`, plus `rpi_ready()` and `wait_for_job()` moved out of `Schedule` unchanged |
| `pc\alarms.py` | new | `evaluate(state)` and `AlarmBook` |
| `pc\netcheck.py`, `pc\plasmic_client.py` | unchanged | |

Frozen interfaces (so the packages of §H can be written in parallel):

```python
# pc/recipe.py
class RecipeError(ValueError): ...
DEFAULT_LIMITS = {'motors': {2:'A',3:'A',4:'A',5:'A',6:'B',7:'B',8:'B',9:'B'}, 'labels': {...}, 'valve_linked': [2, 9],
                  'max_flow': 80.0, 'syringe_ul': 5000, 'syringe_valves': 3, 'syringe_max_speed': 2.5,
                  'syringe_speed': 0.4, 'clean_seconds': 122}
def parse(text) -> list            # expanded steps: n, line, action, target, value, unit, note, round, rounds
def preflight(steps, limits=DEFAULT_LIMITS) -> dict      # summary of A.4; adds text and planned_s to the steps
def check(text, limits=DEFAULT_LIMITS) -> dict           # parse + preflight; raises RecipeError
def estimate(steps, plasma_s=30) -> int
class RecipeStore:                 # <data>/recipes
    def __init__(self, folder): ...
    def seed(self, limits): ...                      # first start only
    def rows(self) -> list         # id, name, created_at, step_count, uses_plasma, uses_rpi, uses_mega
    def load(self, recipe_id) -> dict                # + csv, sha256; re-verifies the hash
    def publish(self, name, csv_text, limits) -> dict
    def delete(self, recipe_id): ...                 # moves the file to recipes/deleted/

# pc/mega.py
class PumpError(RuntimeError): ...
class Interlock(PumpError): ...
class PumpBoard:
    def __init__(self, data_dir, event, simulate=False, speedup=1, hv_busy=lambda: '',
                 link_factory=None, port_lister=None): ...
    def start(self); def close(self); def ui_alive(self)
    def describe(self) -> dict; def limits(self) -> dict; def view(self) -> dict
    def ports(self) -> list; def configure(self, port); def reconnect(self)
    def save_settings(self, syringe_ul, syringe_speed_ml_s)
    def acquire(self, who); def release(self, who)            # who: 'round' | 'manual'
    def idle_problem(self) -> str                             # '' = safe to discharge
    def run(self, who, motor, flow, direction, max_seconds); def stop(self, motor)
    def dose(self, who, motor, volume_ml, direction=1, cancel=None) -> dict
    def dose_weight(self, who, motor, target_g) -> dict
    def clean(self, who, cancel=None) -> dict
    def syringe_init(self, who); def syringe_speed(self, who, ml_s)
    def syringe_move(self, who, op, valve, volume_ml, cancel=None) -> dict
    def tare(self, who) -> dict; def calibrate(self, grams) -> dict; def scale_restart(self)
    def stop_all(self, reason='') -> dict                     # no ownership needed, never raises: {ok, error}
    def manual(self, action, **args) -> dict                  # front door for Application.command

# pc/runner.py
def rpi_ready(link) -> dict                                   # RuntimeError says why not (was Schedule._ready)
def wait_for_job(link, job_id) -> tuple                       # (slot status, note) (was Schedule._wait_for_job)
class RoundNotStarted(RuntimeError): ...
class RoundRunner:
    def __init__(self, store, link, mega, folder, version): ...
    def start_problem(self, summary) -> str
    def run_round(self, recipe, operator, schedule_id=None, slot=None, slots_planned=None, planned_at=None) -> dict
    def record_slot(self, status, note, recipe, operator, schedule_id, slot, slots_planned, planned_at)
    def pause(self); def resume(self); def request_stop(self)
    active: bool                                              # running, pausing, paused or stopping
    def view(self) -> dict | None
    def rows(self, limit=200) -> list; def load(self, round_id) -> dict

# pc/alarms.py
def evaluate(state) -> list        # [{id, pri, area, text}] — pure
class AlarmBook:
    def update(self, state) -> list                           # adds since, active, acked
    def ack(self, alarm_id=None) -> int
```

### B.2 Threads

| Thread | Started by | Does | May block for |
|---|---|---|---|
| main | `main()` | `serve_forever` | — |
| one per HTTP request | `ThreadingHTTPServer` | API calls. Long manual board actions are handed to the board's job thread, so a request returns in < 1 s (worst case 3–5 s behind a serial command) | ≤ 5 s |
| `Link._loop` | `Link` (unchanged) | RPi connect, 1 s status poll, screen watchdog for a maintenance discharge, result download | — |
| `Schedule._loop` | `Schedule.start` | slot timing **and the round itself** (`runner.run_round` is called here) | a whole round |
| `PumpBoard._loop` | `PumpBoard.start` | connect / identity check / reconnect, a `?` every 5 s as life sign, run deadlines (0.2 s tick), screen watchdog for manual runs, reads the V7 event queue (`READY`, scale errors), restarts the weight stream after a STOP | 2.5 s (boot delay) |
| `PumpBoard` job thread | `PumpBoard.manual` | one manual dose / weight dose / cleaning / syringe move at a time | minutes |
| `serial-reader`, `sim` | vendored V7 `Link` / `SimulatedMega` | read lines; simulator tick | — |
| demo only: child process | `Application` | `plasmic_tcpd.py --sim` (unchanged, now started with `-B`) | — |

### B.3 Locks and who owns what

| Lock | Owner | Guards |
|---|---|---|
| `Store.lock`, `PinLock.lock`, `Link.lock`, `Link.request_lock`, `Schedule.lock` | as today | as today |
| `PumpBoard.lock` | `PumpBoard` | `status`, `owner`, `job`, `deadlines`, syringe book-keeping, the current V7 `Link` object |
| V7 `_tx_lock` | vendored `transport.Link` | one serial command at a time |
| `RoundRunner.lock` | `RoundRunner` | the round record shown to the page |
| `AlarmBook.lock` | `AlarmBook` | alarm list and acknowledged ids |

Rule kept from 1.3.0: copy what is needed under the own lock, release it, then call another component. No component
calls another while holding its own lock, so there is no lock order to get wrong.

Device ownership: `PumpBoard.owner` is `None`, `'round'` or `'manual'`.
- A round takes `'round'` at its start when the Recipe uses the board **or** contains `plasma_run` while the board is
  configured; it keeps it while paused; it releases it at the end.
- A manual start needs `owner is None` and takes `'manual'` until the action ends or is stopped.
- `stop`, `stop_all` and every `…off` never need ownership.
- The RPi side keeps its own single-job rule (`busy`); the PC adds: manual RPi commands are refused while
  `schedule.status in BUSY` or a round is active (existing text “Scheduled measurement in progress:…”).

### B.4 The pump-board wrapper (`pc\mega.py`)

**Loading the vendored code (no edit of V7 files).**

| Topic | How |
|---|---|
| Import | first use: `os.environ['LAB_CONFIG'] = <pc>\vendor\v7\config\hardware.yaml`; `sys.path[:0] = [<pc>\vendor\v7, <pc>\vendor\python]`; then `from backend import registry, transport`, `from backend.config import get_config`, `from backend.functions import motors, pump, scale`. Done once per process under a module lock. The 26 functions register at import; a failed import sets the board status to `error` “program file is incomplete (pc\vendor) ” and the rest of the program keeps working |
| `hardware.yaml` | read only, never rewritten. It is V7's file; changes are made in V7 and brought over with the sync tool (§F) |
| Module-level config cache | `get_config()` returns one dict per process. Before each connection the wrapper replaces the `link` section by a private copy: `cfg['link'] = dict(cfg['link'], port=<explicit name>, simulate=<bool>, simulation_speedup=<n>)`. `port` is **always an explicit name**, never `auto`, so V7's "first port found / CH340" search can never run |
| The `get_link()` singleton | not used. The wrapper builds `transport.Link()` itself, one new object per connection (the class reads `simulate` and the `link` section at construction). `link.weight_hooks.append(scale.record)` after each construction |
| Scale calibration file | `os.environ['SCALE_CALIBRATION_FILE'] = <data>\mega\scale_calibration.json` (V7 reads this variable at every use). The vendored `config\scale_calibration.json` is only the seed copied at first start |
| Simulator | `simulate=True` (always in `--demo`, and in tests): `cfg['link']['simulate'] = True`; V7's own `SimulatedMega`; port name `simulator`; no pyserial call |
| Errors | `registry.FunctionError` → `PumpError(text)` (V7's Chinese text as it is); `transport.LinkError` → `PumpError('Pump control board not responding:' + text)` |

**Port choice and identity check.**

| Step | Rule |
|---|---|
| Setting | `settings.json` → `mega_port`: `"off"` (default), `"auto"`, or a name such as `"COM3"` |
| Candidates | `serial.tools.list_ports.comports()` filtered to USB id 2341:0042 or 2A03:0042. Listing opens no port |
| `auto` | exactly one candidate → use it. None → status `no_port` “not found Arduino Mega (USB line connected? )”, look again every 5 s (listing only). Two or more → status `ambiguous` “found 2 indivual Arduino Mega: Please go to repair › Connection Specify one”, nothing is opened |
| A named port | the screen only offers candidates. A name typed into `settings.json` by hand is used as written (for a clone board), and then stands or falls with the identity check |
| Open | V7 `Link.start()` (115200, waits `boot_delay_s` 2.5 s; opening resets the board) on the board thread |
| Identity | accepted when the reader stored a line starting `READY,arduino+pump`, **or** `link.send('?')` returns a `STATE,` line that has all of `a=`, `sa=`, `b=`, `sb=`, `valves=`, `w_stream_hz=`, `cleaning=`. Otherwise `link.stop()` at once, status `wrong_device` “COM3 is not a pump control board” The measurement program on; **no automatic retry** (a wrong device is not reset again and again) until the setting changes or “Reconnect” is pressed |
| Firmware fingerprint | the raw `STATE` line carries `cal_pulses_per_ml` and `max_flow_uL_min`; when they differ from the vendored YAML (4237.01 / 80000) → alarm priority 3 “The firmware settings of the pump control board are different from those of the program.”. Not blocking (V7 ignores them too) |
| After every connect | what V7's `main.py` did: `scale.restore_calibration(link)`, `W,AVG,<n>`, `W,STABLE,<g>`, `W,STREAM,<hz>`, `?`. Added: `pump.set_syringe` when the saved size is not 5000 (the board forgets it at every reset; V7 never re-applied it), and one `stop_all` when the connection is a **re**connect |

**Reconnect and resets.**

| Event | Wrapper reaction |
|---|---|
| Reader thread ended (`link.connected` false) or two life-sign `?` in a row failed | status `lost`; a running board step fails; alarm priority 1 “The connection to the pump control board is interrupted: the pump may still be running...” when something was running, else priority 2; `link.stop()`; a new `Link` every 5 s. Reopening resets the board (pumps stop, valves close), then one `stop_all` |
| A `READY` line after the first one (board reset by itself) | alarm priority 2 “Pump control board restarted”; a running round fails; calibration, stream and syringe size are applied again |
| `ERR,stream_stopped_no_reply` | scale shown as “No response”, alarm priority 3; no automatic retry (button “Reread” on manual operation) |

**Completion waits and time limits**: the table in A.2. The same primitives serve the manual page.

**Host-side maximum time for continuous runs.** `run(who, motor, flow, direction, max_seconds)` stores a deadline;
the board thread sends `motors.stop` when it passes and logs “motor n The maximum operating time has been reached and it will automatically stop”. In a round
`max_seconds = planned_s + 5`; on the manual page it is the operator's “automatically stops” value (1–600 s, default 30).
`dose_weight` gets a 480 s deadline that fires `stop_all`. Doses and cleaning end in the firmware by themselves.

**Screen watchdog for manual runs.** `Application.state()` calls `mega.ui_alive()` (next to the existing
`link.ui_alive()`). A manual continuous run or manual job with no `/api/state` request for
`UI_WATCHDOG_SECONDS` (10) is stopped: “The operation screen exceeds 10 No response for seconds,Manually started pump stopped”. Rounds are not tied to the
screen (as scheduled discharges in 1.3.0).

**Stop on exit.**
- `Application.close()`: `schedule.close()` → `runner.request_stop()` → `mega.close()` (= `stop_all`, then
  `link.stop()`) → `link.close()`.
- Closing the black window does not run `finally` blocks on Windows. `main()` therefore registers a console control
  handler with `ctypes` (`SetConsoleCtrlHandler`; close, log-off, shutdown): it calls `mega.stop_all()` (≤ 3 s; Windows
  allows 5 s) and returns. The RPi side is already covered by its own `owner_disconnected` watchdog.
- Not coverable without new firmware: power cut, PC crash, USB cable pulled. The Mega has no watchdog; a continuous
  run then goes on until someone switches it off. This is why the guide recommends `dose_volume` (ends in the
  firmware) wherever a volume is meant, and why `pump_on` is capped.

**`stop_all()`**: V7 `motors.stop_all` (`X`, `W,STREAM,0`, `?`); when a syringe move was in progress also
`pump.stop` and its reply is checked; all deadlines cleared; `owner` released; job cancelled. Result
`{ok: true}` or `{ok: false, error}` → alarm priority 1 “The pump stop command is not confirmed: please use the physical switch to cut off the power immediately.”. Two seconds later,
when nothing is running, the weight stream is switched on again (`scale.restart`).

**How tests are kept away from real ports** (three independent guards):
1. Default `mega_port: "off"`: an `Application` on a fresh or 1.3.0 data folder never touches pyserial.
2. `tests\safety.py`, imported first by every test file: sets `PLASMIC_NO_REAL_SERIAL=1`. `PumpBoard` checks this
   variable immediately before a real open and raises `AssertionError('test cannot open the real serial port')`.
3. The same helper replaces `serial.Serial` by a function that raises and `serial.tools.list_ports.comports` by one
   that returns an empty list; port-choice tests pass their own `port_lister`, failure tests their own `link_factory`
   (a scripted simulated with `send`, `snapshot`, `refresh_state`, `subscribe`, `weight_hooks`, `connected`, `stop`).
A test proves that guards 2 and 3 fire.

### B.5 The round runner (`pc\runner.py`)

States: `running` → (`pausing` → `paused` → `running`) → `done` | `failed` | `stopped`; `stopping` while a stop is
being carried out. Labels: is executing, Paused, Suspended, Stopping, Complete, failed, Stopped.

`run_round()` (on the schedule thread):
1. **Start checks** (`start_problem`; any problem raises `RoundNotStarted` → the slot is `not_started`, nothing has
   moved): Recipe re-parsed and preflighted with today's limits; with `plasma_run`: RPi connected, `hello.flow`
   present (else “RPi is still an old version: please double-click first Install.cmd renew RPi”), `rpi_ready` (the 1.3.0 check, moved);
   with other RPi steps: RPi connected and not busy; with board steps: board `connected`, `idle_problem() == ''`,
   `owner is None` (when the board is `off`: “this Recipe A pump is required, but the pump control panel is set to ”Not Used”); with `plasma_run`
   and the board configured: board connected and idle.
2. Write the round record with status `running` (so a crash leaves a trace), take board ownership.
3. For each step: pause point → stop check → execute (table A.2) → store result and seconds.
4. `finally`: outcome `done` / `failed` / `stopped`. Not `done` and the round used the board → `mega.stop_all()`;
   an RPi job of this round possibly running → `link.stop()` and wait for the RPi to report the end. A stop that is
   not confirmed → `stop_errors`, status forced to `failed`, alarm priority 1. Release ownership, write the record.

**Interlocks at run time**

| # | Check | When | Refusal |
|---|---|---|---|
| I1 | high voltage idle before any board motion: when the RPi is connected, `link.hv_busy()` must be empty (no `plasma_run` pending, no running job with `high_voltage`, status ≤ 10 s old with `power.run_stop_setting == 0` and `nano.output_pin_sample` false). RPi not connected → not checked (pump-only work stays possible, as the original) | every `run`, `dose`, `dose_weight`, `clean`, syringe call — round or manual | `Interlock` “The high pressure has not been confirmed to be turned off, and the pump cannot be started:...” |
| I2 | board idle right before `plasma_run`: when `mega_port` is not `off`: connected, a fresh `?` just succeeded, both groups `idle`, both valves closed, `cleaning` false, no manual job, syringe `ready` (when it was initialised) | the `plasma_run` step and the maintenance discharge | step fails “The pump is still operating (or the status is unknown) and cannot be discharged:...” |
| I3 | manual starts locked while a round is active or the schedule is in `BUSY`; STOP and OFF always pass | `Application.command` | 409 “Scheduled measurement in progress: Press ”Stop” or ”Pause” before manual operation” |

**Pause** (`runner.pause()`; the footer button): takes effect before the next step or inside a `delay`. A dose,
cleaning, syringe move or RPi step that is in progress finishes first (status `pausing`). At the pause point every
continuous pump the round switched on is stopped and its used time remembered; status `paused`; the board stays
reserved. **Resume**: those pumps start again with `max_seconds = remaining + 5`, the `delay` continues with its
remaining time. **Stop** (`request_stop()`): flag checked before each step and every 0.2 s in waits; `mega.stop_all()`
and `link.stop()` are sent by the STOP command itself (B.7), so a blocking V7 call or an RPi job returns promptly.
A stopped round cannot be continued.

### B.6 The schedule (reuse of 1.3.0)

`pc\schedule.py` stays the time arithmetic. `Schedule` in `app.py` keeps `_load`, `_write`, `_save`, `view`, `_slot`,
`_phase`, the loop skeleton, `START_FAILURES`, `KEEP_SLOTS`, "never resumed after a restart". Changes:

| Part | 1.3.0 | 2.0.0 |
|---|---|---|
| `preview(raw, sequence)` | estimate from `status.estimates[sequence]` | `preview(raw, recipe_id)`: estimate from `recipe.estimate()`; adds `recipe`, `uses_plasma`, `uses_rpi`, `uses_mega`, `blockers`; `sequence` is gone |
| `start(raw, sequence)` | `_ready()` | `start(raw, recipe_id, operator, confirmed)`: operator required, `runner.start_problem()`, Recipe CSV kept in memory and in the record (`recipe: {id, name, sha256}`, `operator`) |
| slot body | `_ready()` + `plasma_run` + `_wait_for_job` | `runner.run_round(...)` → `RoundNotStarted` = `not_started`; result `done` / `stopped` / `failed`; `job` (last plasma job of the round) and `round` id stored in the slot |
| pause | — | `pause()` / `resume()`: status `paused`; a slot that comes due more than 5 s into a pause is `skipped_paused` (original); a running round is paused / resumed with it |
| skipped slots | slot rows only | also `runner.record_slot(...)` so execution record lists them |
| equipment ownership | `ACTIVE` | `BUSY` (a schedule paused between two slots leaves the equipment free, as the original) |

### B.7 `Application.command` additions

- `stop`: `schedule.request_end()`; `runner.request_stop()`; `mega.stop_all()` on a helper thread while the request
  thread runs `link.stop()`; join ≤ 4 s. Reply = the RPi reply plus `schedule_ended`, `round_stopped`, `mega`.
  An unreachable RPi still raises the 1.3.0 `RuntimeError` (“…Please physically turn off the high voltage immediately”) **after** the pump stop was
  attempted; the pump result is appended to the text.
- Operators: `Store.operators` (`operators.json`), `Application.operator` in memory (`''` after a restart). Rules as
  the original: ≤ 50 names, 1–20 characters, no control characters, none of `< > " \`; removing the current operator
  clears the selection.
- Events: `Store.event(text, level='info', actor=None)`; `actor` defaults to the current operator, `Maintenance` for
  PIN-gated actions, `system` for automatic ones. Old lines without `actor` stay valid.

### B.8 PIN: which exact actions need it

`unlocked = app.pin.allowed(session)` as today (true when no PIN file exists; see §J-1).

| Needs the PIN | Open to the operator |
|---|---|
| `/api/config` (RPi address, port, token) | `stop`, `refresh`, `alarm_ack`, `operator_select` |
| `/api/raw` (TCP terminal) | `schedule_preview`, `schedule_start` (with the tick-box flags), `schedule_pause`, `schedule_resume`, `schedule_stop` |
| `set_parameters`, `prepare`, `clear_fault`, `spectrum`, `plasma_run` (maintenance discharge) | manual board actions: `pump_start`, `pump_stop`, `pump_dose`, `pump_dose_weight`, `pump_clean`, `syringe_init`, `syringe_move`, `scale_restart` |
| `recipe_publish`, `recipe_delete` | `recipe_validate` (reads nothing, writes nothing) |
| `operators_set` | all GET routes (state, Recipes, rounds, spectra, CSV) |
| `mega_ports`, `mega_configure`, `mega_reconnect`, `pump_settings` | `/api/diagnose`, `/api/find` (read-only, as 1.3.0) |
| `scale_tare`, `scale_calibrate` | |
| `sim_fault` (practice mode only) | |

High voltage is not behind the PIN for a schedule (original): the operator ticks the confirmation rows once.

---

## C. HTTP API

Unchanged for every route: bind 127.0.0.1, `Host` check, and for POST the `Origin` check, header `X-Token`, JSON
object body ≤ 100 000 bytes (a Recipe of 200 KB does not fit: the limit for `/api/command` rises to 220 000 bytes, as
the original's). Header `X-Unlock` carries the PIN session. Error mapping unchanged: `PermissionError` → 403
`{error, code: 'locked'}`; `PlasmicError` → 409 `{error, code}`; `ValueError` (incl. `RecipeError`) → 400;
`RuntimeError` (incl. `PumpError`; `Interlock` adds `code: 'interlock'`) → 409; `OSError` → 500; bad token/host/origin
→ 403 `{error: 'Invalid local request'}`.

### C.1 GET

| Path | Reply | Notes |
|---|---|---|
| `/` | `web\index.html` | |
| any path matching the static rule | file under `pc\web\` | see C.4 |
| `/guide` | the guide, `style-src 'self' 'unsafe-inline'` | unchanged |
| `/api/bootstrap` | `{csrf, version, demo, config: {rpi_host, rpi_port, token_set}, ui_watchdog_s, board: <PumpBoard.describe()>, limits: {recipe…}}` | `config` unchanged (a test compares it) |
| `/api/state` | C.3 | feeds both screen watchdogs |
| `/api/runs`, `/api/runs/<job>`, `/api/runs/<job>.csv` | unchanged | meta gains `flow`, `round`, `operator`, `recipe_name` |
| `/api/recipes` | `{recipes: [{id, name, created_at, step_count, rounds, uses_plasma, uses_rpi, uses_mega}]}` | |
| `/api/recipes/<id>` | `{id, name, created_at, sha256, csv, summary, steps: [{n, line, action, target, value, unit, note, text}]}` | 404 unknown id |
| `/api/rounds` | `{rounds: [{id, kind, status, time, finished_at, operator, recipe_name, slot, slots_planned, job, note}]}` newest first, ≤ 200 | execution record |
| `/api/rounds/<id>` | the full round record (D.3) | id pattern `R-\d{8}T\d{6}Z-[0-9a-f]{8}` |
| anything else | 404 `{error}` | |

### C.2 POST

| Path | Body | Reply | PIN |
|---|---|---|---|
| `/api/unlock` | `{pin}` | `{session}`; 409 wrong PIN / locked out / no PIN set | — |
| `/api/lock` | `{}` | `{locked: true}` | — |
| `/api/config` | `{rpi_host, rpi_port, token, clear_token?}` | `{saved: true}`; 400 bad value or practice mode | yes |
| `/api/diagnose` | `{}` | `{steps: [...]}` | no |
| `/api/find` | `{}` | `{found, host, port, addresses}` | no |
| `/api/raw` | `{line}` | `{reply}`; refuses `plasma_run`, `set_parameters`, `auth`, and while busy `prepare`, `clear_fault`, `spectrum` | yes |
| `/api/command` | `{cmd, …}` | table below | per command |

`/api/command`:

| `cmd` | Extra fields | Reply (200) | Errors | PIN |
|---|---|---|---|---|
| `stop` | — | RPi reply (`cancel_sent`/`stopped`, …) + `schedule_ended`, `round_stopped`, `mega: null \| {ok} \| {ok: false, error}` | 409 RPi stop not confirmed | no |
| `refresh` | — | RPi `status` (fresh) | 409 not connected | no |
| `operator_select` | `name` | `{operator}` | 400 not in the list | no |
| `alarm_ack` | `id` or `all: true` | `{acked: n}` | — | no |
| `schedule_preview` | `recipe_id`, `schedule` | `{spec, planned_total, last_slot_at, estimate_seconds, voltage_v, recipe: {id, name, step_count}, uses_plasma, uses_rpi, uses_mega, warnings, blockers}` | 400 form or Recipe invalid, no Recipe | no |
| `schedule_start` | `recipe_id`, `schedule`, `confirm_high_voltage` (required `true` when the Recipe has `plasma_run`) | `{accepted: true, schedule}` | 400 missing confirmation / invalid; 409 “Please select an operator first”, “Already has a schedule in progress”, “The schedule cannot be started:…” | no |
| `schedule_pause` / `schedule_resume` | — | `{accepted: true, schedule}` | 409 nothing to pause / not paused | no |
| `schedule_stop` | — | `{accepted: true, stop: <RPi reply or null>, mega: …}` | 409 no active schedule | no |
| `pump_start` | `motor`, `flow_ml_min`, `direction` (1 / −1), `seconds` (1–600) | `{accepted: true}` | 400 range; 409 interlock, owned, not connected | no |
| `pump_stop` | `motor` | `{stopped: true}` | 409 not connected | no |
| `pump_dose` | `motor`, `volume_ml`, `direction` | `{accepted: true, eta_s}` | as `pump_start` | no |
| `pump_dose_weight` | `motor`, `target_g` | `{accepted: true}` | as `pump_start` | no |
| `pump_clean` | — | `{accepted: true, seconds}` | as `pump_start`; V7 refusals | no |
| `syringe_init` | — | `{accepted: true}` | as `pump_start` | no |
| `syringe_move` | `op` (`aspirate` / `dispense`), `valve` (1–3), `volume_ml` | `{accepted: true}` | as `pump_start` | no |
| `scale_restart` | — | `{hz}` | 409 | no |
| `recipe_validate` | `csv` | `{summary, steps}` | 400 with the row message | no |
| `recipe_publish` | `name` (1–60 characters), `csv` | `{recipe}` | 400 | yes |
| `recipe_delete` | `id` | `{deleted: true}` | 409 in use by the active schedule | yes |
| `operators_set` | `operators: [...]` | `{operators}` | 400 | yes |
| `set_parameters` | `parameters` | unchanged (`saved`, `synced`, `sync_error`) | unchanged | yes |
| `prepare`, `clear_fault`, `spectrum` | `spectrum`: `integration_ms` | unchanged | 409 while busy | yes |
| `plasma_run` | `confirm_high_voltage: true` | unchanged (attended, tied to the screen) | 400 no confirmation; 409 busy, old RPi program, I2 | yes |
| `mega_ports` | — | `{ports: [{device, description, vid, pid}], setting}` | — | yes |
| `mega_configure` | `port` (`off` / `auto` / name) | `{saved: true}` | 400; 409 while a round or manual job runs; practice mode | yes |
| `mega_reconnect` | — | `{accepted: true}` | 409 as above | yes |
| `pump_settings` | `syringe_ul` (5000 / 1250), `syringe_speed_ml_s` | `{saved: true}` | 400 | yes |
| `scale_tare` | — | `{calibration}` | 409 | yes |
| `scale_calibrate` | `grams` (0.1–20000) | `{calibration}` | 409 | yes |
| `sim_fault` | `fault` | unchanged | practice mode only | yes |
| anything else | | | 400 “Unsupported command” | |

### C.3 `GET /api/state`

```json
{
  "version": "2.0.0", "demo": false,
  "unlocked": true, "pin_set": false,
  "operator": "", "operators": ["…"],
  "link": { "…": "exactly as 1.3.0: connected, host, port, error, server, pending, status, status_age_s, own_job, found, addresses" },
  "board": {
    "setting": "off", "status": "off", "port": "", "simulated": false, "error": "",
    "firmware": null, "firmware_ok": null,
    "owner": null,
    "job": null,
    "groups": { "A": {"selected": null, "state": "idle", "left_s": null, "flow_ml_min": null, "direction": null},
                "B": {"selected": null, "state": "idle", "left_s": null, "flow_ml_min": null, "direction": null} },
    "cleaning": false, "valves": {"1": false, "2": false},
    "syringe": {"online": false, "syringe_ul": 5000, "speed_ml_s": 0.4, "initialised": false, "error": null},
    "scale": {"online": false, "grams": null, "stable": false, "age_s": null},
    "last": null,
    "interlock": {"ok": true, "reasons": []}
  },
  "schedule": null,
  "round": null,
  "alarms": [],
  "events": [],
  "latest_run": null
}
```

| Field | Meaning |
|---|---|
| `unlocked` | this request's `X-Unlock` session is valid, or no PIN is set |
| `pin_set` | a PIN file exists |
| `operator`, `operators` | chosen name (`''` = none) and the list |
| `link` | unchanged; `link.server.flow` tells whether the RPi runs the unified flow |
| `board.setting` | `off` / `auto` / port name (from `settings.json`) |
| `board.status` | `off`, `connecting`, `connected`, `lost`, `no_port`, `ambiguous`, `wrong_device`, `error` |
| `board.port` | the open port, `simulator`, or `''` |
| `board.firmware`, `firmware_ok` | the `READY` line; fingerprint equal to the YAML (null = unknown) |
| `board.owner` | `null`, `round`, `manual` |
| `board.job` | running manual action: `{kind, text, motor, started_at, eta_s}` |
| `board.groups.X` | selected motor, V7 state `idle` / `wait` / `run` / `move`, seconds left until the host stops it, flow and direction of a run this program started |
| `board.cleaning`, `valves` | from `STATE` |
| `board.syringe` | `online` from `STATE`; size; saved speed; whether `syringe_init` ran since the connect; last `error=` code |
| `board.scale` | last weight, stable flag, age in seconds (`online` false when older than 3 s or the stream is off) |
| `board.last` | result of the last manual action `{kind, ok, text, at}` |
| `board.interlock` | may a manual start happen now, and the reasons when not (I1, I3, ownership, not connected) |
| `schedule` | the 1.3.0 view (`id, status, created_at, finished_at, spec, planned_total, last_slot_at, estimate_seconds, voltage_v, next_index, next_slot_at, slots (last 60), counts, error`) plus `recipe {id, name}`, `operator`, `uses_plasma`, `uses_rpi`, `uses_mega`; `status` may be `paused`; slots may be `skipped_paused` and carry `round` |
| `round` | the running or last round: `{id, status, recipe {id, name}, operator, schedule_id, slot, slots_planned, started_at, finished_at, step, step_count, steps: [{n, text, st, s}], error, stop_errors, jobs, uses_plasma, uses_mega, paused_pumps}`; `st` is `wait` / `run` / `done` / `fail` / `skip`, `s` the seconds used |
| `alarms` | `[{id, pri (1–3), area, text, since, active, acked}]`, worst first |
| `events` | last 60 `{time, level, text, actor}` newest first |
| `latest_run` | newest spectrum meta (unchanged) |

### C.4 Static files under the CSP

- The `STATIC` dict is replaced by one rule: the path (after `/`) must match
  `[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*\.(html|js|css|ttf|txt|csv)`, must not contain `..`, and the resolved file must
  lie inside `pc\web`. `/` → `index.html`. Everything else → 404. `/app.js` and `/style.css` keep their names (the
  existing test fetches them).
- Types: `text/html`, `text/javascript`, `text/css`, `font/ttf` (sent without `charset`), `text/plain`, `text/csv`.
- Headers on every reply unchanged: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, the CSP
  `default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none';
  object-src 'none'; base-uri 'none'`. Fonts load through `default-src 'self'`.
- Page rules that follow: scripts are ES modules from `/app.js` and `/js/*.js`; no inline script, no inline event
  handler, no `style="…"` in markup, no `innerHTML`, no `eval`, no CDN. Styles set from script use the CSSOM
  (`element.style.setProperty`), as `app.js` already does today.

---

## D. Disk layout under `pc\data` and migration

### D.1 Files

| Path | 1.3.0 | 2.0.0 | Shape |
|---|---|---|---|
| `config.json` | yes | **unchanged** | `{rpi_host, rpi_port, token}` |
| `pin.json` | yes | unchanged | `{salt, digest, iterations}` |
| `events.jsonl` | yes | same file, lines may carry `actor` | `{time, level, text, actor?}` append-only |
| `runs\<job>.json`, `<job>.meta.json` | yes | same; meta gains `flow`, `round`, `operator`, `recipe_name`; `sequence` no longer written | |
| `schedule.json` | yes | same file (latest schedule); record gains `recipe`, `operator`, `uses_*`; slots gain `round` | |
| `settings.json` | — | new | `{version: 2, mega_port: "off", syringe_ul: 5000, syringe_speed_ml_s: 0.4}` |
| `operators.json` | — | new | `{operators: []}` |
| `recipes\<32 hex>.json` | — | new | `{id, name, created_at, csv, sha256, step_count, rounds, uses_plasma, uses_rpi, uses_mega}` |
| `recipes\deleted\<id>.json` | — | new | a deleted Recipe, moved, never erased |
| `rounds\<round id>.json` | — | new | D.3; id `R-<UTC yyyymmddThhmmssZ>-<8 hex>` so file names sort by time |
| `mega\scale_calibration.json` | — | new | V7's file: `{version, saved_at, offset, divisor, reference_grams}` (written by V7 code, `.tmp` + `os.replace`) |
| `backup-1.3.0\` | — | new, once | copies of `config.json`, `schedule.json`, `pin.json` as they were |
| `pc\data-demo\…` | yes | same layout; `sim\` as before | |

All JSON files are written with the existing `write_json` (`.tmp`, rename, 10 retries on `PermissionError`).
`events.jsonl` is appended line by line as today.

### D.2 Migration from 1.3.0 (`migrate(data_dir)`, called once by `Application.__init__`)

Nothing is converted or deleted. The trigger is "`settings.json` does not exist".

| Step | Action |
|---|---|
| 1 | `backup-1.3.0\`: copy `config.json`, `schedule.json`, `pin.json` when present |
| 2 | write `settings.json` with the defaults (`mega_port: "off"`) |
| 3 | `operators.json` missing → `{operators: []}` |
| 4 | `recipes\` missing → create and seed “Discharge measurement only” and “automatic cleaning |
| 5 | `mega\scale_calibration.json` missing → copy `pc\vendor\v7\config\scale_calibration.json` (the rig's calibration of 2026-08-26) |
| 6 | create `rounds\` |
| 7 | event “has been upgraded to 2.0.0: Original connection settings and maintenance PIN and spectral records are retained” Automatic testing of |

Reading old data: `schedule.json` of 1.3.0 is loaded as it is (`sequence` ignored; missing `recipe`/`operator` shown
as “—”; an active one becomes `interrupted` by the existing code). Old run metas without `flow` show “1.3.0” in the
spectrum list. `config.json` is not touched, so the owner's `192.168.1.1` and token stay valid, and `Install.cmd` can go
on rewriting it with its three keys.

### D.3 Round record (everything a later analysis needs)

```json
{ "version": 1, "app_version": "2.0.0",
  "id": "R-20261006T041822Z-3fa9c2d1", "kind": "round", "sample_kind": "sample",
  "status": "done", "error": "", "stop_errors": [],
  "schedule_id": "…", "slot": 1, "slots_planned": 6, "planned_at": "…",
  "operator": "…", "recipe": {"id": "…", "name": "…", "sha256": "…", "csv": "…"},
  "started_at": "…", "finished_at": "…",
  "step_count": 7,
  "steps": [ {"n": 1, "line": 2, "action": "dose_volume", "target": "M2", "value": "10", "unit": "mL", "note": "…",
              "text": "…", "status": "done", "started_at": "…", "seconds": 18.4, "result": {"steps": 45590, "eta_s": 18.1}, "error": ""} ],
  "jobs": ["HMI-…"],
  "plasma_parameters": {"voltage_v": 300, "integration_ms": 1000, "…": "snapshot.parameter at the start"},
  "rpi": {"host": "192.168.1.1", "version": "2.0.0", "flow": "unified"},
  "board": {"port": "COM3", "simulated": false, "firmware": "READY,…", "hardware_sha256": "…",
            "scale_calibration": {"offset": -1.0, "divisor": 287703.03125, "saved_at": "…"}},
  "events": [ {"time": "…", "text": "…"} ] }
```

`kind: "slot"` records (a slot that did not run) carry only `id, kind, status (skipped_overrun | skipped_paused |
not_started), schedule_id, slot, slots_planned, planned_at, operator, recipe {id, name}, error`. `sample_kind` is
always `"sample"` in 2.0.0; the field is there for Blank and standard product later. `dose_weight` results keep V7's
`baseline_g, dispensed_g, error_g, within_tolerance, topups, elapsed_s`. A record found `running` at start becomes
`interrupted` (“The program was closed or the computer was restarted; please go to the site to confirm the device status.”).

---

## E. UI

### E.1 Frame (as the original's live screen)

| Zone | Size | Content |
|---|---|---|
| ground | whole window, `#d9dadc` | the 1024 × 768 frame, centred |
| `.top` | 56 px | “Plasmic electronic control” + small version · mode chip · spacer · chip “RPi …” · chip “pump control board …” · operator button · “maintenance mode” (only while unlocked with a PIN set) · link “illustrate” · clock |
| `.banner` | 44 px | worst alarm: priority box, time, text, “Also N item”, “confirm”, “Alarm List”; else “No alarm” |
| `.nav` | 116 px wide | Overview · Scheduled measurement · Manual operation · Execution record · Alarm The file in (badge) · at the bottom “🔧 Maintenance” (dashed) |
| `.page` | 908 × 598 (usable 888 × 582) | one page |
| `.foot` | 70 px | “Start scheduling” / “continue RESUME” · “pause PAUSE” · “Stop STOP” · progress · “⚡ contains high voltage” · E-stop reminder |

Scaling: `k = min(innerWidth ÷ 1024, innerHeight ÷ 768)`; `app.js` sets `--k`, `--x`, `--y` on the root element at
load and on `resize`; `.hmi { transform: translate(var(--x), var(--y)) scale(var(--k)); transform-origin: 0 0; }`.
The proportions never change, nothing scrolls, lists are paged (rows per page fixed, as the original).
Practice mode: 5 px amber inner ring and the chip “Simulation mode · No device connected”.

Chip texts: `RPi Connected` (green dot) / `RPi not connected` (grey dashed) / `RPi ? unknown` (magenta, status older than 5 s) /
`RPi simulation`; `pump control board wired` / `not connected` / `Not used` (grey dashed) / `? Connection interrupted` (magenta) / `simulation`.

Operator button: small “Operator”, below the name or “Please select” (brown). Tap → dialog “Select operator” (choice list). With an
empty list: “There is no operator list yet” / “Please go to repair › Operator New. ”.

Footer:

| Element | Text | Enabled | Action |
|---|---|---|---|
| start | “Start scheduling”; while active “is scheduled”; while paused “continue RESUME” (green) | idle: when the preview has no blocker; paused: always | start dialog (E.4) → `schedule_start`; paused → `schedule_resume` |
| pause | “pause PAUSE”; “Paused...” | schedule `waiting` or `running` | `schedule_pause`, no dialog |
| stop | “Stop STOP” | **always** | `stop`, no dialog, no PIN |
| progress | cannot start: “cannot be started” + first reason · active: “Schedule No. n / N times”, “Next measurement HH:MM (countdown)” or “is currently under measurement/Suspended/Stopping”, “Steps a / b”, “Not measured n times” · else “message” + last message (errors stay, others fade after 12 s) | | tap → the full text in a dialog |
| tag | “⚡ contains high voltage” | an active schedule whose Recipe has `plasma_run` | |
| reminder | “For emergency stop, please press the physical emergency stop button on the device.” (red frame, red/yellow disc), tooltip “STOP is a software stop, not a safety-level emergency stop.” | not a button | |

Reasons after “cannot be started”, first match: “The program did not respond” · ”Please go to ”Schedule Measurement” to select Recipe” · “Please select an operator first” ·
the preview's first blocker (e.g. “RPi not connected”, “RPi is still an old version: please double-click first Install.cmd”,
“The pre-discharge inspection failed:...”, “Pump control board is not connected”, “this Recipe A pump is required, but the pump control panel is set to ”Not Used”, “The pump is still operating”) ·
“Schedule setting is being calculated...”. Practice mode is never a reason: there everything runs on the two simulators.

### E.2 Operator pages

**Overview** (read-only)

| Region | Content |
|---|---|
| status strip, 56 px, 6 cells | “RPi connection” (Connected · N seconds ago) · “HSPY high voltage power supply” (closure 0 V / Outputting N V) · “Nano controller” (state · fault) · “pump control board (standby / motor n running / Cleaning / not connected / Not used) · “syringe pump” (Online · Ready / not initialized / mistake n / No response) · “weight” (N.NNN g · stable/Changing/No response) |
| panel “Steps for this round”, left 500 px | sub-title “is executing:{Recipe} · Schedule No. n/N times · steps a/b” / “has been suspended:…” / “last executed: {state} · {Recipe}” / “has not been executed yet”. Table “# ｜ content ｜ state ｜ time”, 8 rows per page, the page follows the current step; done = grey, current = blue, failed = orange, paused = amber. Pager |
| panel “latest spectrum”, right top | small chart (difference curve), lines “time”, “highest peak N nm”, “Voltage N V”, “signal N Zhang · Background N open”, “After pressing N seconds to start discharging”. No button |
| panel “Pump and Valve”, right bottom | “A group motor 2 · water running 40 mL/min left 18 Second”, “B group ”standby”, “valve 1 open valve 2 close”, “Clean No”. Note “status is reported from the pump control board and does not represent the actual flow rate. ” |

The HSPY cell never says more than the read-back; the note under it stays as today's safety text:
“Just because the screen displays ”Off” does not mean that the electrode is out of power. Before repairing or wiring, please use a physical switch to disconnect the high-voltage power supply from the Unplug the network cable of 24 V drive power supply. ”

**Schedule measurement**

Left panel “Schedule settings” (392 px), sub “Click each item to modify; the settings will be recorded on this computer.” / “The schedule is in progress: it cannot be modified until it is stopped.”. Six tiles:

| Tile | Value | Sub-text | Edit |
|---|---|---|---|
| “Recipe” | name / “Please select” (amber dashed) | “N step · with plasma” / “N step · only pump” | choice list |
| “Operator” | name / “Please select” | “Every measurement will be recorded” | choice list |
| “Start” | “Start now” / “HH:MM Start” | “If this time has passed, it will be tomorrow.” | choice → two pads (hour, point) |
| “Measurement interval” | “Every N minute”; with common 1 Second-rate: “Just do 1 times” (tile disabled) | “is calculated based on the starting time each time” | pad 1–1440 |
| “end condition” | “common N times” / “arrive HH:MM ” / “Continue until stop is pressed” | | choice → pad 1–1000 / time pads |
| “measurement content” (read-only) | “⚡ contains high-voltage plasma” / “includes spectrum measurement” / “only pump” | “N V · Confirm once at the beginning, then automatically discharge | — (red frame + hazard stripe with high voltage) |

Default form: Start now, Every 10 minutes, **common 1 Second-rate** (one measurement is the common case). Below: “Estimated single time”,
“first measurement”, “Estimated number of times”, “The last time”; error box; warning boxes. The form is kept in `localStorage`
(`plasmic.schedule2`). Preview: on every change and every 30 s.

Right panel “This schedule” / “Last scheduled” / “Schedule record”: sub “{Recipe} · {operator} · {establishment time}”, right
“Complete n · Skip n · failed/not started n”. Table “sequence ｜ scheduled time ｜ state ｜ start ｜ End ｜ Remark”, 9 per page. Empty:
“has not started scheduling yet” / “Select on the left Recipe, start time, interval and end conditions, and then click ”Start Schedule” below. ”

**manual operation**

Notice bar on top, first match: “Simulation mode: only simulated devices” (blue) · “Pump control board is set to ”not used”: Go to repair › Connect settings” (grey) ·
“The pump control board is not connected: start-up operations are disabled; you can still press the button to stop. (orange) · “Scheduled measurement in progress: Press ”Stop” or ”Pause” before manual operation” (grey) ·
“The high pressure has not been confirmed to be turned off, and the pump cannot be started (...)” (orange) · default “A manually started pump will automatically stop after a set number of seconds. The computer crashes or USB There is no guarantee that it will stop when the wire is loose.
Please prepare the physical stop method. ” (yellow).

Left panel “peristaltic pump”:

| Control | Text | Enabled | Action |
|---|---|---|---|
| motor buttons, two labelled rows | “A group” `2 water` `3` `4` `5` · “B group” `6` `7` `8 OUT` `9 CuSO₄` | no round, interlock ok | selects (blue) — local only, nothing is sent |
| mode | “continuous operation” ｜ “Quantitative mL” ｜ “weighing g” | | shows the tiles of that mode |
| tile “flow” (continuous operation) | “40 mL/min”, range 0.1–80 | | pad |
| “direction” (continuous operation, Quantitative) | “forward” ｜ “reverse” | | |
| tile “automatically stops” (continuous operation) | “30 Second” / “” Restart after, range 1–600 | | pad |
| tile “volume” (Quantitative) | “10 mL”, range 0.01–1000 | | pad |
| tile “target weight” (weighing) | “10.00 g”, range 0.01–1000; sub “Take the weight when pressed as zero point” | scale online | pad |
| “Start” (primary) | | a motor is chosen, group idle, interlock ok | dialog “start motor n” → `pump_start` / `pump_dose` / `pump_dose_weight` |
| status row per group | “A group motor 2 · water running 40 mL/min left 18 Second” / “Valve is opening” / “Quantification in progress” / “Weighing” / “standby” + “stop” | “stop”: the group has a selected motor | `pump_stop` at once, no dialog |

Right column (330 px):

| Panel | Controls |
|---|---|
| “syringe pump” (tag “online” / “No response” / “mistake n”) | “valve position” `1` `2` `3` · “volume” `−` “1.000 mL” `＋` (pad 0.001–syringe size) · “”Aspirate” · ”Inject” · ”Initialize” (each through a dialog; initialization warns “The piston will return to the origin and the liquid in the syringe will be pushed out. ”). Foot note “speed N mL/s, Syringe N mL: is in repair › Pump and scale settings. ” |
| “weight” | big value “12.345 g”, tag “stable” / “is changing” / “No response”; button “Reread” shown only when the scale is off |
| “automatic cleaning | state “standby” / “Opening water valve” / “in water” / “Water entering and draining” / “finally emptied into the air” (V7's texts); line “water 30 Second → water＋discharge 45 Second → Empty 45 Second”; button “Start cleaning” / “Cleaning in progress...” |

**execution record** — one panel, sub “Each measurement and the period of no measurement (the most recent 200 pen)”. Table
“time ｜ operator ｜ Recipe ｜ sequence ｜ End ｜ state ｜ Remarks ｜ spectrum”, 9 per page; “order” = `n/N`; the last cell has a
button “Look at the spectrum” when the round has a spectrum → dialog (720 px) with the chart, the series switch and
“download CSV”. Tap on “Remark” → the full text and the step list of that round. Empty: “has no measurement record yet”.

**alarm** — tabs “alarm list (N) ” ｜ “Operational events”; counters “Emergency n” “high n” “middle n”; “Confirm all”.
Alarm table “Occurrence time ｜ level ｜ area ｜ message ｜ state ｜ Action” (button “confirm”), 8 per page; state
“in action/Restored · Confirmed/Unconfirmed”. Event table “time ｜ who ｜ event”, 12 per page (the 60 newest).

Alarm list (`pc\alarms.py`):

| id | Priority | area | Condition | Text |
|---|---|---|---|---|
| (page only) | 1 Emergency | program | no `/api/state` for 5 s | “The program did not respond: the screen data has expired and the actual status of the device is unknown.” |
| `rpi_stop` | 1 | high pressure | last job `post_stop.ok` false, or the last STOP could not reach the RPi | “There is no confirmation that the high voltage has been turned off after stopping: please use a physical switch to cut off the power.” |
| `board_stop` | 1 | pump | `stop_all` / `pump_off` not confirmed | “The pump stop command is not confirmed: please use the physical switch to cut off the power immediately.” |
| `board_lost` | 1 when something was running, else 2 | pump | status `lost` | “The connection of the pump control board is interrupted: the pump may still be running, please go to the site to confirm” For / “Pump control board connection interrupted” |
| `rpi_link` | 2 | RPi | an active schedule uses the RPi and it is not connected | “RPi Not connected: Schedule content measurement steps” |
| `nano_fault` | 2 | high pressure | `nano.fault` not `NONE` | “Nano Fault: {fault}” |
| `board_reset` | 2 | pump | unexpected `READY` | “Pump control board restarted: outputs have all been turned off” |
| `syringe` | 2 | pump | last `PUMP` line had `error≠0` | “Syringe pump report error {n}” |
| `round:<id>` | 2 | Process | last round `failed` | “process failed:{error}” |
| `sched:<id>` | 2 | Schedule | schedule `failed` or `interrupted` | “Schedule has stopped:{error}” / “The schedule has been interrupted and will not be automatically restored.” |
| `skip:<id>` | 3 middle | Schedule | `skipped_overrun` > 0 | “Already N periods were not measured because the last time period has not ended yet.” |
| `ns:<id>:<i>` | 3 | Schedule | a slot `not_started` | “No. N times failed to start:{note}” |
| `dose:<round>` | 3 | pump | weight dose outside tolerance | “Weighing exceeds the allowable range: Target x g,Actual y g” |
| `scale` | 3 | pump | the weight stream stopped by itself | “Weight module did not respond” |
| `board_fw` | 3 | pump | fingerprint mismatch | “The firmware settings of the pump control board are different from those of the program.” |
| `rpi_old` | 3 | RPi | connected and `hello.flow` missing | “RPi is still an old version: please double-click Install.cmd renew” |

An alarm that clears while unacknowledged stays listed as “has been restored” until acknowledged. Acknowledgement lives in the
program's memory (gone after a restart).

### E.3 Maintenance (nav “🔧 Maintenance”; label “Maintenance Settings” while unlocked)

Entry states:

| State | Screen |
|---|---|
| PIN set, locked | panel “Maintenance Login”, sub “connection, plasma parameters,Recipe, Pumps and scales, spectroscopic tools and operator lists are only for maintenance personnel”; dots field, error line, keys `1 2 3 / 4 5 6 / 7 8 9 / Clear 0 ⌫` (keyboard digits also work), “Return”, “log in”. Errors: “Maintenance PIN error”, “Too many attempts, please N Try again in seconds” |
| PIN set, unlocked | tab bar + message chip + “Sign out” (`/api/lock`, back to Overview). The session is renewed only by maintenance requests of this page, never by the 1 s poll |
| no PIN set | the tabs open directly, with a fixed notice: “has not been set for maintenance yet PIN: Anyone can change the settings here. To set up, double-click on your computer Set-PIN.cmd (6–8 digits) .” |

Tabs: “Recipe” ｜ “connection” ｜ “Plasma Parameters” ｜ “Pump and Scale” ｜ “spectrum” ｜ “Operator”.

**Recipe** — left 300 px “Published Recipe”: list (6 per page) `name` / “N step · with plasma｜only pump”; below “Import CSV files”
(file picker, `.csv`, read in the page) and the link “Download template” (`/templates/recipe-template.csv`).
Right, published Recipe: facts “steps/Wheel”, “content” (contains plasma / with pump / with syringe pump), “Estimated time”, “SHA-256” (12 characters),
“High voltage confirmation” (Confirm when starting schedule / unnecessary); table “List ｜ content ｜ Remark” (7 per page; content = the server's sentence);
button “delete Recipe” (disabled while used by the active schedule; dialog “delete Recipe”).
Right, after an import: “name”, “Check” (passed / failed + the row message), the same table, warnings; buttons
“Modify name” (text field dialog, 1–60 characters), “give up”, “release” (dialog “release Recipe”).

**connection** — two halves.

| Half | Rows | Buttons |
|---|---|---|
| “RPi (High voltage and spectrum)”, sub `host:port` | “connection”, “Server version”, “Measurement Process” (background 1 open / Old version), “This computer’s IP”, “Recent error” | “Connection settings…” (dialog: “RPi of IP address”, “TCP port”, “token (Passcode)” as plain text fields, “Save and reconnect”) · “automatic search RPi” · “connection diagnosis” · “TCP terminal…” (dialog with one text field, “Sent”, the reply) — all four exist in 1.3.0 and are moved here |
| “pump control board (Arduino Mega) ”, sub “use USB cable is connected to this computer” The | “status” (Connected COM3 / not connected / Not used / not found Arduino Mega / The device on this port is not a pump control panel), “settings” (Not used / automatic search / COM3), “confirm” (has been confirmed to be the pump control board / —), “Recent error” | “Select port…” · “Reconnect” |

Dialog “Where is the pump control board connected? (how a non-technical owner sets it up): choices “does not use the pump control board”, “Automatic search (suggestion)”, and one
row per found board “COM3 · Arduino Mega 2560”. When none is found: “not found Arduino Mega.Please confirm USB line has been connected to this computer. ”
Warning box: “The pump control board will restart when connected: running pumps will stop and valves will close. ” Confirm “Save and connect”. After that the board is opened
at every program start without asking. Practice mode: the half shows “Simulation mode: Use the built-in simulation pump control board”, buttons disabled.

**plasma parameters** — sub “point value modification; high voltage must be turned off when storing, high voltage will not be started.”. Seven tiles (4 columns, 112 px): “Pulse ON” ms 1–1000 ·
“Pulse OFF” ms 1–1000 · “every round pulse times” 1–10000 · “round number” 1–1000 · “integration time” ms 1–10000 · “Spectral interval” ms 1–60000 ·
“HSPY Set voltage” V 1–700. A changed tile gets the blue frame and “RPi at present N”. Buttons: “Give up modification”; one button whose
label follows the state — “Save and sync” (something changed → dialog listing `old → new`, `set_parameters`), “synchronized to Nano”
(nothing changed but the Nano differs from the file → `prepare`), “has been synchronized” (disabled). “Clear Nano Fault” (enabled
only with a fault; dialog). Full-width hazard button “⚠ Manually discharge once” / “⚠ Discharging...” with sub
“will output high voltage · Current setting N V” or the blocking reason → dialog E.4 → `plasma_run` (the existing attended run: stops when
the screen goes away).

**Pump and Scale** — left panel “Syringe Pump Settings”: tiles “Syringe Capacity” (choice “5 mL” / “1.25 mL”) and “Push speed” (pad, mL/s, limit of
the size), button “store” (`pump_settings`). Right panel “weight module”: big value + tag; “Empty cup returns to zero” (dialog) ; tile
“Weight weight” (choice 10 / 50 / 100 / 200 g or pad 0.1–20000) ; “Calibrate after adding weights” (dialog “calibration will overwrite the last calibration”);
line “Last calibration: {date} · {weight} g”; hint “Empty cup returns to zero → put weight → Select grams → Correction”. Bottom panel (read-only)
“Pump settings (from V7 of hardware.yaml) ”: “Flow limit 80 mL/min”, “Quantitative speed 40 mL/min”, “cleaning water 30 Second → Same transfer 45 Second → Empty 45 Second”,
“valve linkage motor 2 ↔ valve 1,motor 9 ↔ valve 2”, “weighing allowable ±0.5 %”; note “These numbers are in V7 and import it again (see instructions). ” The limit of

**spectrum** — left 250 px “Spectral record” (5 per page: time, “discharge”/“Retrieve”, voltage, “highest peak N nm”); button
“capture spectrum (no discharge)” with sub “Check the spectrometer alone: Read 3 Zhang, do not turn on high voltage”. Right: the 1.3.0 chart moved as it is (series switch,
drag to zoom, “Return to full spectrum”, peaks, “download CSV”) and the line “background N Zhang · Signal N Zhang · Points N ms”.

**operator** — panel “Operator List”, sub “You must choose a name before starting the schedule; each measurement will be recorded who did it.”. Table “# ｜ Name ｜ Delete”,
8 per page. “New operator” → text field dialog (1–20 characters). Both send the whole list (`operators_set`).

Disabled states on the maintenance tabs (the reason is the button's sub-text or the message chip):

| Control | Disabled when | Reason shown |
|---|---|---|
| “Save and sync”, “synchronized to Nano”, “Clear Nano Fault”, “capture spectrum (no discharge)”, “⚠ Manually discharge once” | schedule in `BUSY` or a round active | “Scheduled measurement in progress” |
| the same five | RPi not connected | “RPi not connected” |
| “⚠ Manually discharge once” | RPi readiness problems / RPi program without `flow` / I2 | the first RPi reason · “RPi is still an old version” · ”The pump is still operating and cannot discharge” |
| “Clear Nano Fault” | no fault | “There is currently no glitch” |
| “Select port...”, “Reconnect” | a round or manual pump action is running; practice mode | “The pump is operating: press to stop first” · ”Simulation Mode” |
| “Connection settings…” | practice mode | “simulation mode is fixedly connected to the local simulator” |
| “Empty cup returns to zero”, “Calibrate after adding weights”, “store” (Pump and Scale) | board not connected; a round or manual action running | “The pump control board is not connected” · ”The pump is operating” |
| “delete Recipe” | used by the active schedule | “this Recipe is being used in schedule” |
| “release” | the check did not pass | the row message |

### E.4 Dialogs and entry

One dialog component (`js\dialog.js`), parts in this order: title; key/value lines; choice list (2 per row, 8 per
page); number pad; text field; detail text; warning box; acknowledgement rows; footer “Cancel” + a named confirm
button; the line “N If there is no confirmation within seconds, it will be automatically canceled and no instruction will be sent. ” (30 s; 90 s for the schedule start). Scrim tap or Esc cancels.

Decisions:
- **Numbers: the original's number pad**, in a dialog, with the range printed (“range 1–700,integer”) and
  “is out of range or the format does not match”; the confirm button is disabled until the value is valid. The pad also takes the keyboard
  (digits, `.`, Backspace, Enter). Reason: looks and behaves like the original, works on a touch panel and on the
  owner's desktop PC, and one validated entry path replaces native number fields.
- **Text (operator name, Recipe name, RPi address, token, terminal line): a plain text field** in the dialog.
  Reason: the original's on-screen keyboard cannot type Chinese names (its own foot note says so) and would be
  ≈150 lines for nothing on a PC with a keyboard.
- **High voltage and pump starts: tick rows.** Every row must be ticked before the confirm button works.

| Dialog | Lines | Tick rows | Confirm |
|---|---|---|---|
| “⚠ Start scheduling: High voltage will be automatically output for each measurement. (red, 580 px) | Recipe, operator, first measurement, interval, End, Estimated single time, HSPY Set voltage “N V (upper limit 700 V) ” | “I have confirmed that each measurement will automatically output N V High voltage, will not ask again” · ”The electrodes and high-voltage lines have been connected; personnel have stayed away” · ”Physical emergency stop/The power-off switch is at hand” · with pumps also “Liquid lines, reagents and drains are in place” | “Start scheduling” |
| “Start scheduling” (Recipe without `plasma_run`) | the same without the voltage | “Liquid path, Reagents and drains are in place; Entity emergency stop/The power-off switch is at hand” | “Start scheduling” |
| “⚠ Manual discharge once: high voltage will be output. | the seven parameters, “HSPY at present” | “I have confirmed that it will be output soon N V High voltage, personnel are far away from the electrode, physical emergency stop/The power-off switch is at hand” | “Start plasma (output high voltage)” |
| “start motor n · {label}” | How to do, flow / volume / target weight, direction, automatic stop | “The fluid line has been connected; I know the computer or USB The pump will not stop itself when interrupted” | “Start” |
| “syringe pump for pumping liquid/Liquid injection/initialization” | Valve position, volume | — (warning box only) | “Sent” |
| “Start cleaning” | “water 30 Second → water＋discharge 45 Second → Empty 45 seconds, a total of approx. 2 minute” | “The water and discharge pipes have been connected” | “Start cleaning” |

Warning text in both schedule dialogs: “The schedule will be automatically started based on time.STOP is a software stop, not a safety level emergency stop; in an emergency, please press the physical emergency stop. ”

### E.5 Colour rules (unchanged from the original)

Colour means "running" or "abnormal"; normal is grey. `--run #2f5f8f` running / selected / primary button;
`--bad #a3168e` + “?” + dashed frame = communication lost, value not trustworthy; grey dashed = not used / not
installed; `--p1 #c62828` priority 1 and everything high-voltage (frame, hazard stripes `#ffd000`/`#1d1f20`);
`--p2 #e06a00` priority 2, failed step, danger dialog; `--p3 #e0b400` priority 3; green `#3d7a52` only for the OK dot
and “continue RESUME”; amber for paused and practice mode. Square corners, 1 px `#b9bcc0` panel borders, `#8b8f94`
button borders, no shadows except dialogs, no animation except the blinking priority box. Fonts: Barlow (text),
Barlow Condensed (numbers, clock, big values), fallback `"Microsoft JhengHei"`; base 15 px.

### E.6 File split of the web code

| File | Content | Approx. lines |
|---|---|---|
| `pc\web\index.html` | frame, page containers, dialog host; `<link>` to the three style sheets; one `<script type="module" src="/app.js">` | 220 |
| `pc\web\style.css` | tokens, frame, components (from the original's style block, rewritten without the template runtime's wrapper spans) | 520 |
| `pc\web\css\pages.css` | layouts of the five operator pages | 200 |
| `pc\web\css\maint.css` | layouts of the maintenance tabs and the PIN page | 160 |
| `pc\web\fonts\fonts.css` + 5 `.ttf` + 2 licence texts | vendored (§F) | — |
| `pc\web\app.js` | `api()`, `command()` (from 1.3.0), 1 s poll, stale detection, scaling, top bar, banner, nav, footer, page registry | 420 |
| `pc\web\js\dialog.js` | dialog component, number pad, choice list, text field, tick rows, PIN entry | 330 |
| `pc\web\js\chart.js` | the 1.3.0 spectrum chart moved (lines 838–1145 of today's `app.js`) | 330 |
| `pc\web\js\page-dash.js`, `page-schedule.js`, `page-manual.js`, `page-runs.js`, `page-alarms.js` | one page each: `export function render(state)` and `export function wire()` | 150–300 each |
| `pc\web\js\maint.js` | lock page, tab bar, message chip | 150 |
| `pc\web\js\maint-recipe.js`, `maint-link.js`, `maint-plasma.js`, `maint-pumps.js`, `maint-spectra.js`, `maint-operators.js` | one tab each (`maint-link.js` takes over 1.3.0's settings / diagnose / find / terminal code) | 100–250 each |
| `pc\web\templates\recipe-template.csv`, `recipe-dose-plasma.csv` | header-only template and example 2 | — |

Every render function stays idempotent (text, class, `hidden`, `disabled`), lists are rebuilt only when their key
changes — the two habits of today's `app.js`.

---

## F. Vendoring, launchers, installer, versions

### F.1 What is copied (byte-identical; names kept unless noted)

| Source | Destination |
|---|---|
| V7 `backend\__init__.py`, `config.py`, `registry.py`, `transport.py`, `simulator.py` | `pc\vendor\v7\backend\` |
| V7 `backend\functions\__init__.py`, `motors.py`, `pump.py`, `scale.py` | `pc\vendor\v7\backend\functions\` |
| V7 `config\hardware.yaml`, `config\scale_calibration.json` | `pc\vendor\v7\config\` |
| V7 `firmware\Arduino\Arduino.ino`, `hw_config.h` (reference; never built or flashed by EC) | `pc\vendor\v7\firmware\Arduino\` |
| V7 `tools\__init__.py`, `tools\gen_firmware_config.py` (reference) | `pc\vendor\v7\tools\` |
| **not** copied | V7 `backend\main.py`, `frontend\`, `docs\`, `tools\selftest.py`, `probe.py`, `url.py`, `run.bat` |
| V7 `.venv\Lib\site-packages\serial\**\*.py` (pyserial 3.5; 28 files in `serial\`, `serial\tools\`, `serial\urlhandler\`, `serial\threaded\`) | `pc\vendor\python\serial\` |
| V7 `.venv\Lib\site-packages\yaml\*.py` (PyYAML 6.0.2; 17 files; **not** `_yaml.cp313-win_amd64.pyd`, not the `_yaml\` package) | `pc\vendor\python\yaml\` |
| V7 `.venv\Lib\site-packages\PyYAML-6.0.2.dist-info\LICENSE` | `pc\vendor\python\LICENSES\PyYAML-LICENSE.txt` |
| V7 `.venv\Lib\site-packages\pyserial-3.5.dist-info\METADATA` (the wheel ships no licence file; METADATA says `License: BSD`) | `pc\vendor\python\LICENSES\pyserial-METADATA.txt`, plus `pyserial-LICENSE.txt` = the BSD-3-Clause text with the copyright line from `serial\__init__.py` (`(C) 2001-2020 Chris Liechti`) — see §J-9 |
| HMI `web\vendor\font-a35caa4687f434d6.ttf` (Barlow 400), `font-91c841fdfa8e7b94.ttf` (500), `font-74103fd6d41bc32c.ttf` (700), `font-c82d65280835cb3c.ttf` (Condensed 400), `font-0d85af813fc3ed87.ttf` (Condensed 600) | `pc\web\fonts\Barlow-400.ttf`, `Barlow-500.ttf`, `Barlow-700.ttf`, `BarlowCondensed-400.ttf`, `BarlowCondensed-600.ttf` (renamed, content identical) |
| HMI `web\vendor\Barlow-OFL.txt`, `BarlowCondensed-OFL.txt` | `pc\web\fonts\` |
| new, written for EC | `pc\web\fonts\fonts.css` (five `@font-face` rules with `/fonts/…` URLs), `pc\vendor\VENDORED.json`, `pc\vendor\THIRD-PARTY-SOURCES.txt` |

`pc\vendor\**` is exempt from the "`.py` files use LF" rule: the copies keep whatever line ends the source has.
`SHA256SUMS.txt` is not touched (its 22 files have their own meaning); the vendored files get their own manifest.

### F.2 Sync-check tool `docs\tools\check_vendor.py`

`pc\vendor\VENDORED.json`: `[{dest, origin: "v7" | "v7-venv" | "hmi", source, sha256, bytes}]`, plus the date of the
copy. The tool (run with the bundled Python, `-B`, like `check_sums.py`):
1. always: every `dest` exists and has the recorded hash; no extra `.py` under `pc\vendor` (ignoring `__pycache__`);
2. when the source folders exist (defaults: the three Desktop paths; options `--v7`, `--hmi`): compares every `dest`
   with its `source` and prints “same” or “V7 It has been changed over there:<file>”; also lists V7 backend files that are new there;
3. `--update` re-copies from the sources and rewrites the manifest (the only supported way to bring a V7 change in).
Exit code 1 on any difference. It never imports the vendored code and never touches a port.

### F.3 Launchers

| File | 2.0.0 |
|---|---|
| `Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`, `Install.cmd`, `Flash-Nano.cmd`, `installer\unpack-python.cmd` | unchanged |
| `Set-PIN.cmd` | unchanged except the two `rem` lines (what the PIN guards); still ASCII + CRLF |
| new launchers | none. The maintainer tool `check_vendor.py` is run like `check_sums.py` |

`__pycache__`: `app.py` sets `sys.dont_write_bytecode = True` before its first local import, and starts the
simulator child with `-B`, so no launcher has to change and nothing is written into `pc\vendor`.
New `app.py` option: none (`--demo`, `--port`, `--data`, `--no-browser`, `--set-pin` stay).

### F.4 Installer

- `installer\install.py` `step_computer()`: the "folder complete" list gains `pc\recipe.py`, `pc\mega.py`,
  `pc\runner.py`, `pc\alarms.py`, `pc\vendor\v7\backend\transport.py`, `pc\vendor\v7\config\hardware.yaml`,
  `pc\vendor\python\serial\__init__.py`, `pc\vendor\python\yaml\__init__.py`, `pc\web\style.css`. Nothing else
  changes: `save_config` keeps writing the three keys, which is exactly why `config.json` keeps its schema.
- RPi side: no installer change in this design. The version in `rpi\tcpd\plasmic_tcpd.py` must read `2.0.0` (one
  line, owned by the RPi review); with it the installer sees “RPi on TCP The server is 1.3.0,This installation package is 2.0.0” and offers
  the update with default yes.

**RPi migration (tcpd-only, no reboot), done by the owner after the PC tests pass:**
1. Close the control screen and the black window (the token changes). Nothing may be running (`busy` false).
2. Double-click `Install.cmd` (or the command of `historical-development-notes.md` with `--yes --no-shortcuts --no-launch --identity …`).
   The RPi already has the tools, so `remote_install.sh` takes `--tcpd-only`: it replaces the three programs in
   `tcpd\`, moves `plasma_quick.py` into the backup folder, keeps `config.json` (adds the allow entry, new token),
   restarts `plasmic-tcpd`. No reboot, the Nano is not reset.
3. The installer writes the new token into `pc\data\config.json` and reads `status` once.
4. Start the program: chip “RPi is connected”, Maintenance › connection shows “Measurement Process Background 1 open”.
Until step 2 is done, PC 2.0.0 against RPi 1.3.0 connects, shows everything, and refuses plasma steps with
“RPi is still an old version: please double-click first Install.cmd renew RPi”.

### F.5 Version numbers

| Thing | Version |
|---|---|
| `pc\app.py` `VERSION` | `2.0.0` |
| `rpi\tcpd\plasmic_tcpd.py` `VERSION` | `2.0.0` (RPi review) |
| TCP protocol | `1` (unchanged) |
| Nano firmware | 1.2.1 (unchanged) |
| Mega firmware | unversioned; identified by the `READY` line and the fingerprint (unchanged) |
| Vendored V7 | `backend.__version__` `5.0.0`; identified by `VENDORED.json` (date + hashes) |
| pyserial / PyYAML | 3.5 / 6.0.2 (pure Python parts) |
| `settings.json` `version` | `2` |

---

## G. Test plan

All with `unittest`, bundled interpreter, `-B`, 127.0.0.1 only. Every test file that imports `app`, `mega` or
`runner` starts with `import safety` (B.4).

| File | Owner | Cases (≈ count) |
|---|---|---|
| `tests\safety.py` | WP2 | helper, no tests |
| `tests\test_recipe.py` | WP1 | header/columns/limits (5) · every action accepts its good row and refuses bad target, range, step, unit (19 sub-cases in 4 tests) · repeat expansion, nesting 3, count 20, 500-step limit (3) · preflight rules 1–10, one test each (10) · the three example files pass; example 1 has `uses_mega` false (2) · estimate (2) · store: publish, hash check on load, tampered file refused, delete moves the file, seeding happens once (5) — **31** |
| `tests\test_mega.py` | WP2 | guards: `PLASMIC_NO_REAL_SERIAL` raises; patched `serial.Serial` raises; default setting is `off` and constructs no link (3) · port choice with a simulated lister: one Mega → picked; none → `no_port`; two → `ambiguous`; a CH340 (1A86:7523) is never offered; a port without USB id is never offered (5) · identity with a scripted link: `READY` accepted; `STATE`-only accepted; garbage → `wrong_device`, link stopped, no retry (3) · vendored config: `hardware.yaml` untouched after use; `link.port` is never `auto`; two boards in one process do not share the `link` dict (2) · simulator: `run` reaches `run`, deadline stops it; M2 opens valve 1 and waits; `stop` → idle + valve closed; `dose` waits for `idle`; `dose_weight` 10 g within tolerance; `clean` waits for `cleaning` false (speed-up 20); syringe init → move → ready (7) · failures with a scripted link: dose never idle → timeout + stop sent; selected motor changes → error; `PUMP,…,error=7` → `PumpError`; lost link during a run → `lost`, alarm data, reconnect makes a new link and sends stop-all; unexpected `READY` → reset handling; fingerprint mismatch flagged (6) · ownership: manual refused while `round` owns; `stop_all` works for anybody and clears deadlines; stream restarted after stop-all (3) · I1: `hv_busy` text blocks every motion call (1) · screen watchdog stops a manual run (1) · after connect the calibration file in the data folder is used, never the vendored one; `tare` writes only into the data folder (1) — **32** |
| `tests\test_runner.py` | WP3 | plasma-only round on `SimServer` with the board `off` → `done`, job id, spectrum saved (1) · pump round on the simulator: steps in order, record on disk with CSV, results, parameters (2) · not-ready RPi → `RoundNotStarted`, nothing sent (1) · old RPi program (`hello.flow` removed) → refused with the Install.cmd text (1) · I2: board made busy right before `plasma_run` → step fails, no job on the RPi (1) · failed step → `stop_all` called, RPi `stop` when a job ran, status `failed` (2) · stop during delay / during a dose / during a weight dose / during the discharge → `stopped`, outputs off (4) · pause in a delay with a pump on: pump stopped, remaining time kept, resume restarts it (2) · pause requested during a dose takes effect after it (1) · deadline: `pump_on` whose `pump_off` never comes is stopped by the host (1) · a record left `running` becomes `interrupted` (1) · `record_slot` rows appear in `rows()` (1) — **18** |
| `tests\test_alarms.py` | WP3 | one state per alarm of E.2 (table-driven, 1 test) · order worst first (1) · ack, cleared-but-unacked stays, ack-all (3) — **5** |
| `tests\test_app.py` | WP4 | existing 37: three sequence tests are replaced by `test_the_unified_flow_is_used_and_its_timing_is_kept` (flow `unified`, `wait_s`, `lead_ms`, 1 background frame) and `test_an_rpi_with_the_older_program_cannot_discharge`; `ScheduleBase.start()` and the two `preview()` helpers add `recipe_id` (the seeded “Discharge measurement only”); `Harness` adds and selects an operator. All other assertions stay as they are — **36** |
| `tests\test_app2.py` | WP4 | static rule: `/js/…`, `/fonts/…ttf` type, traversal and unknown extension refused (2) · migration: a 1.3.0 folder (config, pin, schedule with `sequence`, a run meta) → same `config.json` bytes, backup made, defaults written, Recipes seeded, second start changes nothing (3) · operators: set (PIN), select, schedule start without operator refused, operator in the round record and events (3) · PIN gating: every command of B.8's left column answers 403 `locked` with a PIN set and no session, and passes with one; every command of the right column passes without (2, table-driven) · Recipes over HTTP: validate error text, publish, list, load, delete refused while scheduled (3) · schedule with a pump Recipe on both simulators; slots carry `round`; skipped slot listed in `/api/rounds` (2) · pause / resume / `skipped_paused` (2) · STOP: during a pump step stops both sides and ends the schedule; with the RPi unreachable still stops the pumps and reports “entity method”; idle STOP is harmless (3) · manual: start refused during a schedule, during high voltage (I1), without connection; `pump_stop` always accepted (2) · maintenance discharge refused while a pump runs (I2) (1) · state shape: all top-level keys of C.3 present, token never in it (1) · practice mode: `Application(demo=True)` uses the board simulator and `serial.Serial` is never called (1) — **25** |
| `tests\test_vendor.py` | WP0 | manifest integrity (hashes); no `main.py`, no `.pyd`, no `fastapi`/`uvicorn` import in `pc\vendor`; `.cmd` rule untouched; licence files present; fonts match the manifest (5) — **5** |
| `tests\test_web.py` | WP5 | `index.html` has no inline script/style/handler; every `src`/`href` file exists and passes the static rule; no `innerHTML`, `eval`, `http://`, `https://` in `pc\web\**\*.js`; `.js`/`.css`/`.html` are LF and UTF-8; every `cmd:` string used in the JS exists in `Application.command` (5) — **5** |
| `tests\test_installer.py` | WP7 | two file lists extended — **37** |
| `tests\test_tcpd.py`, `test_measure.py`, `test_flash_nano.py` | RPi review / unchanged | 48 / 16 / 13 |

Total ≈ 271 (today 151). Not testable here and left to the owner: §J.

Three cases that must exist whatever else is cut: (1) a test process cannot open a real port (`test_mega`, guards);
(2) pumps and high voltage are refused together in both directions (`test_recipe` rule 4, `test_runner` I2,
`test_app2` I1); (3) STOP reaches both the pump board and the RPi even when one of them does not answer
(`test_app2`).

---

## H. Build plan

No git, one shared folder: **a file belongs to exactly one package**; nobody edits a file of another package; the
interfaces of B.1, the API of §C and the state of C.3 are the contract. Packages hand over by saying "done" with the
test file green, not by editing each other's code.

| WP | Files (exclusive) | Depends on | Size | Risk |
|---|---|---|---|---|
| **WP0 Vendoring** | `pc\vendor\**`, `pc\web\fonts\**`, `docs\tools\check_vendor.py`, `tests\test_vendor.py` | — | copies + 150 lines | low |
| **WP1 Recipe** | `pc\recipe.py`, `tests\test_recipe.py`, `pc\web\templates\*.csv` | — | 330 + 400 | low |
| **WP2 Board wrapper** | `pc\mega.py`, `tests\safety.py`, `tests\test_mega.py` | WP0 | 520 + 600 | **high** (threads, waits, V7 internals) |
| **WP3 Runner + alarms** | `pc\runner.py`, `pc\alarms.py`, `tests\test_runner.py`, `tests\test_alarms.py` | WP1, WP2 (interfaces only to start; simulator to finish) | 510 + 550 | medium |
| **WP4 Application** | `pc\app.py`, `pc\schedule.py`, `tests\test_app.py`, `tests\test_app2.py` | WP1–WP3 | +380 / −80, + 600 | medium |
| **WP5 Page frame + operator pages** | `pc\web\index.html`, `style.css`, `app.js`, `css\pages.css`, `js\dialog.js`, `js\chart.js`, `js\page-*.js`, `tests\test_web.py` | §C/§E; WP4 for real data | ≈ 2500 | medium (size) |
| **WP6 Maintenance pages** | `pc\web\css\maint.css`, `js\maint.js`, `js\maint-*.js` | WP5's `app.js` and `dialog.js` exports (frozen first) | ≈ 1400 | low |
| **WP7 Packaging + documents** | `installer\install.py`, `tests\test_installer.py`, `Set-PIN.cmd`, `docs\guide-source\**`, `detailed-guide.html` (built), `READ-ME-FIRST.txt`, `historical-development-notes.md`, `docs\work record.md`, `docs\1-How to continue on another computer.txt` | everything | — | low |
| **RPi review (running, not part of this plan)** | `rpi\**`, `tests\test_tcpd.py`, `tests\test_measure.py`, `tests\simulated_rig.py`, `tests\simulated_spectrometer.py` | — | version line only | — |

Untouched by everybody: `pc\netcheck.py`, `pc\plasmic_client.py`, `pc\examples\*`, `pc\runtime\**`, `pc\data\**`,
`nano\**`, `SHA256SUMS.txt`, the other launchers, `installer\*.ps1`, `installer\flash_nano.py`,
`tests\test_flash_nano.py`.

Order:

```
day 0   WP0 ──────────────┐
        WP1 ──────────┐   │        WP5 step 1: index.html, style.css, app.js skeleton, dialog.js   (against a
                      │   ▼                     hand-written state object, no server needed)
        WP2 (after WP0)───┐
                      ▼   ▼
                     WP3 ───► WP4 ───► WP5 step 2 (pages on the practice mode) ∥ WP6 ───► WP7 ───► owner: Install.cmd, rig checks
```

What each implementer must know:

| WP | Must know |
|---|---|
| all | Never start `Start.cmd`. Never open a COM port (the Mega is on COM3 of this PC). Never connect to 192.168.1.1. Run tests only with the bundled Python and `-B`. Stay out of `pc\data`. Vendored files are never edited |
| WP0 | copy byte for byte (binary mode); no `.pyd`; no `main.py`; write the manifest from the copies; run `check_vendor.py` and `check_sums.py` |
| WP1 | no import of `mega`, `app` or vendored code; limits come in as a dict; messages in Traditional Chinese with the row number; keep the original's parser shape (`csv_workflow.py`) |
| WP2 | §B.4 completely; V7 facts: `OK` = accepted, not finished; `V,<m>,0` is a no-op for a non-selected motor; `X` switches the weight stream off; `PUMP,…,error=n` is not an exception in V7; a `P`/`V` to another motor of the group silently replaces the running one; `motors.dose_by_weight` blocks and is cancelled by `motors.stop` / `stop_all`; `Link.snapshot()` is a shallow copy. `view()` must never send a serial command. Use `time.perf_counter()` for short timings (16 ms clock) |
| WP3 | the moved functions `rpi_ready` / `wait_for_job` must keep their texts (tests in `test_app.py` look for “Failed the pre-discharge inspection”, “No. 1 measurements did not end normally”); never repeat a `plasma_run`; write the record at the start and at the end |
| WP4 | keep every existing route, reply key and event text that `test_app.py` asserts; `validate_config` and `DEFAULT_CONFIG` stay (the installer imports them); `Schedule` keeps the record keys of 1.3.0 and only adds; start the simulator child with `-B`; `sys.dont_write_bytecode` |
| WP5 | §E, the screenshots `hmi-shots\orig-*.png`, `understand\hmi-ui-style.md` (appendix = the original CSS); CSP rules of C.4; exports that WP6 relies on: `api`, `command`, `state`, `registerPage`, `toast/message`, and from `dialog.js` `openDialog`, `askNumber`, `askChoice`, `askText`, `askPin`, `confirm` |
| WP6 | only its own files; 1.3.0's settings / diagnose / find / terminal code is in today's `app.js` lines 1283–1403 and is moved, not redesigned |
| WP7 | guide headings that programs cite by title must not be renamed (list in `understand\current-ec.md` §6.5); rebuild with the new test count; `READ-ME-FIRST.txt` is UTF-8 with BOM + CRLF |

Realistic effort (one implementer each, simulators only): WP0 ½ day, WP1 1 day, WP2 3 days, WP3 2 days, WP4 2 days,
WP5 4 days, WP6 2 days, WP7 1½ days; with three people in parallel about 7 working days, then the owner's rig checks.

---

## I. Documentation changes (list only)

- `docs\guide-source\installation-and-usage.src.html`: Look here first (pump board added to the picture) · Safety instructions (pumps: no watchdog in
  the Mega; STOP is not an emergency stop; prefer `dose_volume`) · What does the whole set look like? (new diagram: PC ─USB─ Mega; PC ─TCP─ RPi) ·
  Steps 3 (new sub-part: USB cable of the pump board; heading text unchanged) · Steps 4 (update of the RPi to 2.0.0) ·
  Steps 5 (checklist: Maintenance › connection › Select port; one pump at low flow; syringe init; scale; “Discharge measurement only” Total 1 Second-rate; stop
  during a discharge; stop during a pump run) · Steps 6 rewritten page by page (Overview, Schedule measurement, manual operation, execution record, alarm, Maintenance
  and its six tabs) · new “Recipe how to write” (the action table of A.2, the three examples) · when something goes wrong (new rows: board not
  found, wrong device, RPi still old, syringe error, scale silent) · References (files under `pc\data`, `settings.json`,
  how to bring in a V7 change with `check_vendor.py`, third-party licences) · Verification status (everything of §J marked
  “has not been verified on real devices”) · removal of every “fast/original” and “background 3 open” passage.
- Seven screenshots retaken in practice mode (`shots_app.py`), `docs\guide-source\README.txt` updated.
- `READ-ME-FIRST.txt`: version, one-paragraph description, the “fast” lines removed, pump board line added.
- `historical-development-notes.md`: architecture picture and file table; "pumps were removed" replaced; rule 2 extended (no pump, syringe
  or valve motion on the real rig; never open COM3; never start `Start.cmd`; simulators only); rule 4 (`plasma_measure.py`
  instead of `plasma_quick.py`; `pc\vendor\**` never edited, `check_vendor.py`); rule 9 (`settings.json`, `mega_port`);
  test commands and counts; the note on `PLASMIC_NO_REAL_SERIAL`.
- `docs\work record.md`: new section 13 (2.0.0: what changed, what is unverified, the "not built" list of §K).
- `docs\1-How to continue on another computer.txt`: the pump board is on this PC's USB; on another PC choose the port again.
- `pc\vendor\THIRD-PARTY-SOURCES.txt`: sources, versions, licences, date of the copy.
- `Set-PIN.cmd`: the two comment lines.

---

## J. Risks and what stays unverified until the owner tries it on the real rig

| # | Item | Why it is open | What limits the damage |
|---|---|---|---|
| 1 | **PIN when none is set**: this design leaves maintenance open with a notice (1.3.0 behaviour, zero test churn). The original locks maintenance until a PIN exists | the owner's sentence allows both readings | switching = `PinLock.allowed` returns False when unconfigured (3 lines) + the test harness sets a PIN; decide before WP4 starts |
| 2 | Opening COM3 resets the Mega; the `READY` line arrives inside the 2.5 s wait | inferred from standard Mega behaviour, never tested on this board | identity also accepts a well-formed `STATE` |
| 3 | Syringe "finished" = `ready=1` from `Y,ST` | how the SY-01B sets that bit during a stroke is in no file | every syringe wait also has the computed time as a floor and a hard limit; `error≠0` always fails the step |
| 4 | Real durations of `P` doses versus `eta_s` (acceleration, serial work in the firmware loop) | [INFER] in the V7 notes | limit `eta_s × 1.5 + 10 s`; first rig run measures it |
| 5 | A `V` after a `P` in the same group may be speed-limited by the earlier move | [INFER] in the V7 notes | not worked around; listed in the step-5 checks |
| 6 | No watchdog in the Mega: PC crash, power cut or a pulled USB cable leaves a continuous run going | firmware is not changed | cap 600 s is host-side only; guide + alarm text; doses and cleaning end by themselves; console-close handler |
| 7 | The console-close handler (`SetConsoleCtrlHandler` through `ctypes`) | cannot be exercised by `unittest` | `Application.close()` is tested; the handler is 15 lines calling `stop_all` |
| 8 | `X` waits behind a pending serial command (≤ 3 s, `Y,INIT` ≤ 5 s with the wrapper's shorter wait) | V7's `_tx_lock` | accepted; STOP text says it is a software stop |
| 9 | pyserial's wheel carries no licence file | only METADATA (`License: BSD`) is in V7's `.venv`, and nothing may be downloaded | BSD-3-Clause text written out with pyserial's copyright line; the lead should confirm this is acceptable |
| 10 | The saved scale calibration (2026-08-26) is seeded as the starting point | nobody knows whether it still fits | Maintenance › Pump and Scale shows its date; recalibration is two taps |
| 11 | Firmware in the Mega versus the vendored `hw_config.h` | the board reports no version | fingerprint alarm; cleaning timings shown on screen come from the YAML and are labelled as such |
| 12 | TB6600 or DM542J on the rig, highest reliable step rate, liquids on motors 3–7, syringe valve liquids | not in any file | nothing in the program depends on them; labels come from the YAML only |
| 13 | The unified RPi flow (`plasma_measure.py`) has never run on the rig; two of eight classic runs failed with `UAI_SpectrometerOpen returned 6` | RPi review, outside this design | a failed discharge ends the round and the schedule; never repeated |
| 14 | Whole chain on the rig: schedule with pumps, pause/resume, STOP during a pump step, STOP during a discharge, both watchdogs, reconnect after a pulled cable | simulators only | step-5 checklist in the guide; Verification status says so |
| 15 | PC 2.0.0 with RPi 1.3.0 between the two updates | by design plasma steps are refused | one clear message with the fix |
| 16 | WP5/WP6 size (≈ 3900 lines of page code) is the schedule risk | a whole page is rebuilt in the original's look | frame and dialogs first; pages are independent files; Maintenance tabs reuse 1.3.0 code |
| 17 | `tare` inside a Recipe rewrites the saved zero each time (V7 behaviour) | V7 is the authority | the file is EC's private copy under `pc\data\mega` |
| 18 | The RPi review is editing `rpi\**` and three test files at the same time | no git | those files are in nobody else's package |

---

## K. Not built in 2.0.0 — and why that is still what was asked

| Not built | Reason |
|---|---|
| Correction, Trend, concentration, blank check, blank solution tile | fixed decision 10. Round records keep `sample_kind`, the Recipe text, the parameters and the job ids so they can be added later without touching old data |
| A "measure once" / "fast" button, any sequence choice | “Unified Process”: one way to measure — Schedule measurement; `common 1 Second-rate` is the default form |
| A separate “Stop schedule” button, a “synchronous Nano” button beside “Save and sync”, a `motors.select` button, a syringe "valve only" button | “Don’t have extra buttons”: footer STOP stops everything; one parameter button changes its label; the motor is chosen by starting it; the valve turns with the move |
| Flow diagram, faceplates, level/valve placeholders | fixed decision 10: no invented plumbing |
| Language switch, hidden long-press entry, on-screen keyboard, second PIN prompt for delete | Traditional Chinese only; the nav item is the entry; text fields take a real keyboard; one PIN entry is the model |
| Recipe editor on the screen | the original imports CSV files too; a table editor is a second way to do one thing |
| Reverse dosing in a Recipe, dosing while another pump runs, `scale_record`, `pump_direction` / `pump_speed` rows | direction and flow are per start on V7; reverse = `pump_reverse` + `delay` + `pump_off`; nobody asked for the others |
| Weight chart, weight CSV, scale diagnose / Modbus tools | V7's own page has no buttons for the tools; the number and the stable flag are what the operator needs |
| Editing `hardware.yaml` on the screen; regenerating `hw_config.h`; flashing the Mega | V7 is the authority and the firmware is not changed; a V7 change comes in with `check_vendor.py --update` |
| Import of the original HMI's Recipes, operators or PIN | its data folder is off limits, and its Recipes are timed for the PLC pumps |
| Automatic resume after a restart | never (kept from both programs) |
| Operator passwords, user accounts | the original has none; the PIN is the password |
| FastAPI, a second server, SSE | fixed decision 1; the 1 s poll already drives the screen |
