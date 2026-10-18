# Integrated program 2.0.0 — fixed constraints for the design (decided by the lead, 2026-10-06)

## The owner's request (verbatim, Traditional Chinese)

> It should be enough to take a picture of the background spectrum. Help me update it. There is no need to add ”Quick”. The process must be unified and there should be no redundant buttons. And this AfterUIshould be withpumpcontrol is integrated together,
> refer to the originalUI(on desktop)and unifying it all, the original ForUIis designed forPLCof,Now I switch to usingArduino megacontrol,
> Therefore, it is necessary to use independent laboratoryv7insidecodeshall prevail, butUIThe style should be similar to the original,And to be consistent with the originalUIcan also be setUIoperation password

Earlier standing requests from the same owner: "spectrometer should not need to manually capture spectra, all conditions should be set"; exposure start and
discharge start must coincide and the wait after pressing must be a few seconds, not more than 5; cable network
(not Wi-Fi); never ask for the RPi password again; the owner is not a software person and wants "double-click and it
works" plus step-by-step instructions in Traditional Chinese with short sentences.

## The three code bases

| Name | Where | Role in the merge |
|---|---|---|
| EC ("autonomous control", v1.3.0) | `C:\Users\PlasmaLab\Desktop\autonomous control` | The program that is extended. Becomes 2.0.0. |
| V7 ("independent laboratoryv7", calls itself v5) | `C:\Users\PlasmaLab\Desktop\independent laboratoryv7` | Authoritative for pumps (Arduino Mega 2560 on USB). Read-only source. |
| Original HMI v1.1 | `C:\Users\PlasmaLab\Desktop\UI\UI\hmi_v1_1` | Reference for design logic, page structure, look, password model. Read-only. NEVER open its `data\` folder. NEVER run it (its bundled python loads the original folder). |

Detailed code maps (read these instead of re-reading everything): `...\scratchpad\understand\*.md`
(v7-firmware, v7-backend, v7-frontend, hmi-server-auth, hmi-controller, hmi-analysis, hmi-ui-structure, hmi-ui-style,
current-ec, and the two digests v7-integration-notes.md and hmi-architecture-digest.md when present).
Screenshots of the original HMI running in its demo mode: `...\scratchpad\hmi-shots\orig-*.png`
(dash, schedule, manual, trend, results, calibration, alarms; 1024x768 frame).

## Facts established on this PC (2026-10-06)

- The Arduino Mega is plugged into THIS Windows PC: `COM3 | Arduino Mega 2560 (COM3) | USB VID:PID=2341:0042`.
  It is the only COM port. Nobody may open it during design/implementation/testing (opening resets the board).
- The RPi (plasma side) is at 192.168.1.1 on a direct cable; TCP 8767 JSON lines. Unchanged.
- EC's bundled interpreter `pc\runtime\python\python.exe` (embeddable 3.12, no pip, `._pth` = zip + its own folder,
  ctypes present) CAN import pyserial 3.5 and PyYAML 6.0.2 as pure Python when their folders are put on `sys.path`
  (tested with V7's `.venv\Lib\site-packages\serial` and `\yaml`; `yaml.__with_libyaml__` is False, which is fine).
  The bundled Python zip itself is a protected file and must not be changed.
- PROVEN by experiment (`...\scratchpad\v7try\smoke.py`, `smoke2.py`; simulator only, `serial.Serial` replaced by a
  function that raises): byte-identical copies of V7's `backend\{__init__,config,registry,simulator,transport}.py`
  and `backend\functions\*.py` import and run under EC's bundled interpreter when `sys.path` gets
  `<folder containing backend\>` and `<folder containing serial\ and yaml\>` and the environment variable
  `LAB_CONFIG` points at a hardware.yaml (a private copy with `link.simulate: true`, `link.simulation_speedup: 20`).
  All 26 functions register; `registry.get(name).invoke(Context(link, config), payload)` works;
  `motors.dose` shows group state `move` → `idle`; `motors.run` shows `wait` (valve pre-open) then running;
  over-limit flow raises `FunctionError`; `motors.stop_all` leaves the scale stream off (`stream_hz: 0`).
  V7's `backend\config.py` reads `LAB_CONFIG` at IMPORT time and caches the config; `transport.get_link()` is a
  process-wide singleton whose `simulated` flag is fixed at construction.
- The RPi side already has the single measuring flow (`rpi\tcpd\plasma_measure.py`, config key `flow`,
  `hello.flow`, `status.flow`, `status.start_s`, `status.estimate_s`; `plasma_run` takes no choice). It is under a
  separate review right now; the design must NOT redesign it. The PC side (`pc\app.py`, `pc\web\*`,
  `tests\test_app.py`) still has the removed "sequence quick/classic" choice and must lose it.

## Decisions that are fixed

1. **One program, one page, one port.** EC's `pc\app.py` (standard-library `ThreadingHTTPServer`, 127.0.0.1:8766,
   exclusive port, Host/Origin/token checks, strict CSP `default-src 'self'`, no inline script/style, no CDN) hosts
   everything. No FastAPI, no uvicorn, no second server, no iframe to another port.
2. **V7 pump code is the authority and is reused unchanged.** Vendor byte-identical copies of V7's
   `backend\{__init__,config,registry,transport,simulator}.py`, `backend\functions\{__init__,motors,pump,scale}.py`,
   `config\hardware.yaml`, `config\scale_calibration.json` (and the firmware folder + `tools\gen_firmware_config.py`
   for reference) into the EC tree. `backend\main.py` (FastAPI) is NOT used. Anything V7 lacks (port choice with a
   check that the device really is the pump board, reconnect, waiting for a dose/clean/syringe move to finish,
   a host-side time limit for continuous runs, stop on exit) is added in a NEW wrapper module on the EC side, never by
   editing the vendored files. A small tool must be able to tell whether the vendored copies still equal the V7 folder.
   pyserial and PyYAML (pure-Python parts + licence texts) are vendored from V7's `.venv` — no download.
3. **Pump board port is never guessed.** Use the port saved in settings, or auto-pick only a port whose USB id is an
   Arduino Mega (VID:PID 2341:0042 / 2A03:0042) — never V7's "first port found" fallback, never a CH340 match.
   After opening, verify the `READY,arduino+pump...` banner / a well-formed `STATE,` reply; otherwise close and say so.
4. **RPi link unchanged**: TCP 8767 protocol, allow-list/token, watchdog, `stop` without token. No SSH from the
   program. Nano firmware is not changed. Mega firmware is not changed.
5. **Measurement = the unified flow only** (1 background frame taken first, then discharge + signal frames). No
   sequence choice anywhere in the UI or API. No extra buttons.
6. **Design logic follows the original HMI**: a *Recipe* is a published CSV (`action,target,value,unit,note`) that
   describes ONE round; rounds are run by Schedule measurement (start / interval / end condition), a single measurement is a
   schedule with count 1 (no separate "measure once" button); operators pick their name (no sign-in) and it is
   recorded with every run; manual operation for single devices; execution record; alarm + operation event; Maintenance area behind the PIN.
   The recipe vocabulary is rebuilt for the V7 hardware (8 peristaltic motors 2–9 in groups A/B with per-motor
   direction and flow, dose by volume, dose by weight, auto-clean, syringe pump with valve positions, weighing
   module) plus the plasma steps of the original (`rpi_status`, `nano_prepare`, `plasma_run`, `spectrum_capture`,
   `nano_stop`, `power_off`), `delay`, `repeat_start`/`repeat_end`. Pump roles/labels come from V7's
   `hardware.yaml` (motor 2 = water, 8 = OUT, 9 = CuSO₄; others unnamed) — do not invent plumbing.
7. **Password = the original's model**: a Maintenance PIN (6–8 digits), PBKDF2-HMAC-SHA256 600 000 iterations, set by a
   double-click console script (EC already has `Set-PIN.cmd` and `PinLock` with the same parameters), session 600 s
   sliding, lockout 5 wrong → 30 s doubling to 900 s. Operators work without sign-in. What sits behind the PIN
   follows the original's split (settings, parameters, recipes publish/delete, raw device tools, operator list,
   scale calibration) — the design must list it action by action. Entry like the original (nav item 🔧 Maintenance).
8. **Safety rules carried over and extended**
   - Pumps/syringe and high voltage are never active at the same time (preflight on the recipe AND a runtime check
     right before `plasma_run`).
   - Every continuous pump start has a host-side maximum time; program exit / lost page / recipe stop → stop.
   - While a recipe runs, manual starts are locked; STOP/OFF always works and never needs the PIN.
   - STOP = stop the recipe + pump board stop-all + RPi `stop`. It is NOT an emergency stop: the UI keeps the
     original's reminder to use the physical E-stop and never implies "closure" on screen means the electrodes are dead.
   - High-voltage starts (schedule start with a plasma step, manual plasma) keep the tick-box confirmation.
   - A failed step ends the round with outputs off; a failed round ends the schedule (as the original).
9. **Look = the original's live screen** (`web\index.html` inline style block, ISA-101 style): 1024x768 frame —
   top bar 56, alarm banner 44, nav rail 116 + content, footer action bar 70 with Start scheduling / pause PAUSE / Stop STOP and
   the E-stop reminder; grey ground, square corners, hairline borders, colour only for running (blue #2f5f8f) or
   abnormal; Barlow / Barlow Condensed fonts (copy the TTFs + OFL licence from the original's `web\vendor`).
   Plain HTML/CSS/JS (no React, no eval — EC's CSP stays). The frame is scaled to fit the browser window (keep the
   1024x768 proportions; no scrolling inside pages; lists are paged). Traditional Chinese only.
10. **Pages in 2.0.0**: Overview, Schedule measurement, manual operation, execution record, alarm, 🔧 Maintenance (tabs: Recipe, connection, plasma parameters, Pump and Scale, spectrum, operator).
    The original's Correction / Trend (concentration from spectra, standards calibration, blank QC) are NOT part of 2.0.0 and
    get no nav item (no dead buttons); the design should keep room for them (run records keep what analysis needs).
    Overview shows device status tiles and the current round's steps — no invented plumbing diagram.
11. **Practice mode** (`Start-Demo.cmd`): RPi simulator (`plasmic_tcpd.py --sim`) + V7's own simulator
    (`link.simulate`), everything usable, nothing real touched, clearly labelled simulation mode.
12. **Tests**: `unittest`, standard library, bundled interpreter with `-B`; only 127.0.0.1; never a real COM port
    (tests force the simulator and must fail loudly if a real port would be opened); existing 5 test files stay
    green; `SHA256SUMS.txt` files untouched (`docs\tools\check_sums.py`).
13. **Files/format rules of the EC tree**: `.cmd` ASCII + CRLF; `.sh`/`.service` LF; `.py/.js/.css/.html/.md` LF;
    `READ-ME-FIRST.txt` UTF-8 BOM + CRLF; no `__pycache__` left behind; `pc\data\` is the owner's real data (config points
    at 192.168.1.1) — keep it readable by 2.0.0 (migrate, never delete). Guide text is edited in
    `docs\guide-source\installation-and-usage.src.html` and rebuilt.
14. **Who may do what on the real rig** (for docs and for any agent): review-workspace never sends anything that makes a real
    device act — no discharge, no pump/syringe/valve motion, no opening of COM3. Simulators only. The owner presses.

## Open points the design must settle (and justify briefly)

- Exact recipe vocabulary (actions, targets, units, limits), preflight rules, and how each action maps to V7
  functions and waits for completion.
- Module split of the PC program and the complete HTTP API (routes, request/response shapes, which need the PIN).
- State model sent to the page (`/api/state`) incl. alarms, interlock reasons, round progress.
- Disk layout under `pc\data\` incl. migration from 1.3.0, run record contents.
- Page-by-page UI spec in the original's frame (what is on each page, every button, every dialog, every text).
- How the pump board connection is configured by a non-technical owner (Maintenance → connection).
- Test plan (files, cases) and a build plan split into work packages with DISJOINT file ownership (there is no git;
  parallel implementers cannot share a file).
