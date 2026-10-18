# V7 firmware (Arduino Mega 2560) — reference for the merge

Scope: `C:/Users/PlasmaLab/Desktop/independent laboratoryv7/firmware/Arduino/Arduino.ino` (1132 lines, read completely), `firmware/Arduino/hw_config.h` (66 lines, read completely), `tools/gen_firmware_config.py` (170 lines, read completely), `config/hardware.yaml` (source of hw_config.h, read completely), and the body text of `docs/wiring-motors.html`, `wiring-valves.html`, `wiring-pump.html`, `wiring-scale.html`. For host-side ordering I also looked at `backend/transport.py` (all), `backend/main.py:1-80`, `backend/functions/scale.py:120-168`. Nothing was run, no port opened, nothing modified.

Abbreviations used for references:
`INO` = `firmware/Arduino/Arduino.ino`, `HW` = `firmware/Arduino/hw_config.h`, `GEN` = `tools/gen_firmware_config.py`, `YAML` = `config/hardware.yaml`.

Markers: **[FACT]** = read directly in the code. **[INFER]** = my inference from code structure plus general Arduino / AccelStepper knowledge, not verifiable from the repository. **[DOC]** = stated in the wiring docs only.

---

## 0. One-paragraph picture

One sketch, one MCU, single-threaded `loop()` with no interrupts of its own, no EEPROM, no watchdog. The PC talks to it over the USB serial port with one ASCII line per command. The firmware drives: 8 peristaltic pumps (stepper motors numbered **2..9**) through **two** step/dir drivers, each driver multiplexed to 4 motors by relays (so at most one motor per group runs, two motors in total); 2 normally-closed solenoid valves that the firmware opens/closes **only** as a side effect of motors 2 and 9; a RUNZE SY-01B syringe pump on `Serial1` (the Mega is just a protocol bridge with range checks); and an RS485 Modbus-RTU weighing transmitter on `Serial2` that the firmware polls itself and reports as a `WEIGHT,...` stream (10 Hz by default, starting at boot without any command). All closed-loop logic (gravimetric dosing, sequencing, timeouts) is on the host — the firmware never uses weight for control.

---

## 1. USB serial link (a)

| Item | Value | Ref |
|---|---|---|
| Port | `Serial` (Mega USB, D0/D1) | INO:1080 |
| Baud | **115200** (Arduino default framing 8N1) | INO:1080; YAML:31 `baud: 115200` |
| Direction host→MCU | ASCII text line terminated by `\n`. `\r` is discarded, so `\n` or `\r\n` both work | INO:1114-1122 |
| Max command length | 95 characters. Characters beyond the 95th are silently dropped; the truncated line is still executed when `\n` arrives | INO:1119, buffer reserved 96 at INO:1086 |
| Direction MCU→host | Every line is emitted with `Serial.println` → terminated by `\r\n` | throughout |
| Encoding | Mostly ASCII; some replies contain Chinese text (UTF-8 bytes from the source file), e.g. `ERR,timeout(module did not respond)` | INO:651 |
| Command IDs / checksums | None. Replies are recognised only by their prefix | — |
| Flow control / handshake | None | — |

Command pre-processing (INO:966-969, 496-503):
1. `command.trim()`; an empty line is ignored **with no reply**.
2. `uppercaseKey()` upper-cases only the characters **before the first comma** — so the top-level key is case-insensitive (`x`, `stop`, `c`, `v,2,300`).
3. For `Y,` and `W,` commands the sub-key (the text after the first comma, up to the next comma) is upper-cased again (INO:782, 853), so `y,init` and `w,read` work. Arguments are never upper-cased.
4. Number parsing is lenient (`String.toInt()` / `toFloat()`): non-numeric text parses as 0, trailing junk is ignored. Only the syringe-pump sub-commands that take a number use the strict parser `parseLongStrict` (INO:480-494: optional sign, digits only).

Commands are read **only** in `loop()` (INO:1114). While a command handler is blocking (see §6) further USB bytes just sit in the Mega's hardware RX buffer **[INFER: 64 bytes on a stock Mega core]** and are processed afterwards — this includes `X`.

### 1.1 Top-level dispatch order (INO:966-1077)

Evaluated in this order; first match wins:

| # | Match | Action | Reply |
|---|---|---|---|
| 1 | starts with `Y,` | syringe pump handler (§3) | see §3 |
| 2 | starts with `W,` | weight handler (§5) | see §5 |
| 3 | exactly `C` | if cleaning already active → error; else `startCleaning()` | `OK,cleaning_started` or `ERR,cleaning_active` |
| 4 | cleaning active **and** starts with `S,` / `P,` / `V,` | rejected | `ERR,cleaning_active` |
| 5 | starts with `S,` | select motor | `DONE,S` or `ERR,idx(2~9)` |
| 6 | starts with `P,` | finite move | `OK` or `ERR,arg` |
| 7 | starts with `V,` | continuous run / stop | `OK` or `ERR,arg` |
| 8 | exactly `X` or `STOP` | stop everything | `OK,all_stopped` |
| 9 | exactly `?` | print state | one `STATE,...` line |
| 10 | anything else | — | `ERR,bad_cmd` |

Note: `Y,*`, `W,*`, `?`, `X` are all accepted during cleaning.

### 1.2 Motor / system commands — exact syntax

**`S,<motor>`** (INO:997-1006)
- `<motor>` integer 2..9 (`FIRST_MOTOR`..`LAST_MOTOR`, HW:18-19). Out of range → `ERR,idx(2~9)` (literal text, not derived from config).
- Effect: `selectMotor()` — makes that motor the selected one in its group (relay switching sequence, §2.2). Does **not** move the motor, does not open a valve. If another motor of the same group was active it is stopped (and its linked valve is scheduled to close).
- Blocks ≈125 ms if the selection changes (5 + 60 + 60 ms), 0 ms if already selected.
- Reply: `DONE,S`.
- Not required before `V`/`P` — those select automatically.

**`P,<motor>,<steps>,<sps>`** (INO:1008-1028) — finite move ("Quantitative steps")
- `<motor>` 2..9. `<steps>` signed 32-bit integer, **relative**, sign = direction; **no range check** (0 is accepted and completes at once). `<sps>` float, steps (pulses) per second, must satisfy `0 < sps <= MAX_SPS` (8000.0, HW:22).
- Fewer than 3 comma-separated fields, bad motor, `sps <= 0` or `sps > 8000` → `ERR,arg`.
- Reply `OK` is sent **immediately after queuing**, not on completion. Completion is signalled only by an asynchronous `STATE` line in which that group's state returns to `idle` (§1.4).
- Motion profile: AccelStepper `run()` with acceleration, max speed = `|sps|` (§2.3).
- For valve-linked motors (2, 9): valve opens, motor starts 1000 ms later, valve closes 1000 ms after the last step.

**`V,<motor>,<sps>`** (INO:1030-1057) — continuous run ("continuous rotation")
- `<motor>` 2..9. `<sps>` float, signed (negative = reverse), `|sps| <= 8000`. Missing field / bad motor / too fast → `ERR,arg`.
- `sps == 0` → stop **only if that motor is the currently selected one of its group** (`stopGroupMotion(group, true)`, linked valve closes 1000 ms later); otherwise it is a deliberate no-op (comment INO:1047-1048: stopping an unselected motor must not switch relays). Reply `OK` in both cases.
- `sps != 0` → `queueMotorStart(motor, true, 0, sps)`. Reply `OK` immediately.
- There is **no time limit**: the motor runs until `V,<motor>,0`, `X`, another command for the same group, or an MCU reset.
- Speed change on the fly: if the same motor is already running continuously, a new `V` only calls `setSpeed` — no stop, no new valve pre-open delay (INO:241-244, comment: "Weighing mode speed reduction/Fine processing: no interruption, no re-run and pre-start waiting"). If the same motor is still in its 1 s valve pre-open wait, the new speed just replaces the queued one (INO:245-249).
- (cross-check addition) The same shortcut applies to a **sign change**: `V,n,-2825` while motor n runs at `+2825` reverses the motor at once, with no stop, no ramp and no relay or valve action (INO:241-243).
- (cross-check addition) A `V,n,<sps>` for motor n while that same motor is in a finite `P` move (`move`) does **not** take the shortcut (`running[group]` is false): the move is aborted by `stopGroupMotion` (INO:252), no `STATE … idle` is printed for the aborted move, and the continuous run starts (after a new 1 s pre-open for motors 2 / 9). A second `P,n,…` during a `P` move likewise discards the remaining steps and starts a new relative move. A host that is waiting for "`idle` after `P`" must treat any later `V`/`P`/`S`/`X`/`C` on that group as the end of its wait.

**`C`** (INO:981-989, 281-322) — firmware-timed auto-clean, see §2.5.

**`X`** or **`STOP`** (INO:1059-1069) — "stop all"
1. `cancelCleaningSchedule()`
2. `stopAllMotors()` — both groups: stop stepping at once (no deceleration), driver released (ENA HIGH), all 8 relay pins LOW, selected motor = none (`cur = -1`)
3. `closeAllValves()` — both valves closed **immediately**, pending delayed closes cancelled
4. `sendPumpStopNoWait()` — writes `/1T\r` to the syringe pump and does **not** wait for or check a reply
5. `wStreamHz = 0` — **the weight stream is switched off**; `wFailCount = 0`
6. Reply `OK,all_stopped`. No `STATE` line is emitted.

**`?`** (INO:1071-1074) → one `STATE` line (§1.4).

### 1.3 Every line the firmware can send

Synchronous replies / asynchronous events / periodic lines, complete list:

| Line (exact format) | When | Ref |
|---|---|---|
| `READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0` | once at the end of `setup()` (every boot/reset) | INO:1110 |
| `STATE,a=…,sa=…,b=…,sb=…,pump=…,proto=ascii,baud=9600,addr=0,syringe_uL=…,valves=…,cal_pulses_per_ml=…,max_flow_uL_min=…,w_stream_hz=…,cleaning=…` | reply to `?`; also asynchronous (§1.4) | INO:939-963 |
| `OK` | `P`, `V` accepted | INO:1026, 1055 |
| `DONE,S` | `S` done | INO:1004 |
| `OK,cleaning_started` | `C` accepted | INO:987 |
| `OK,all_stopped` | `X` / `STOP` | INO:1067 |
| `ERR,bad_cmd` | unknown top-level command | INO:1076 |
| `ERR,arg` | bad argument (motor, pump, weight commands) | many |
| `ERR,idx(2~9)` | `S` motor out of range | INO:1000 |
| `ERR,cleaning_active` | `C`, `S`, `P`, `V` during cleaning | INO:983, 993 |
| `PUMP,proto=ascii,ready=<0\|1>,error=<0..15>[,data=<text>]` | valid reply from the syringe pump | INO:434-442 |
| `INFO,pump_steps=<n>` | before the `PUMP` line of `Y,ASPUL` / `Y,DISPUL` | INO:919-920 |
| `FOUND,proto=ascii,baud=9600,addr=0` | `Y,DETECT` / `Y,SCAN` succeeded | INO:457 |
| `ASCIIRX,baud=9600,addr=0,count=<n>,hex=<HH HH …>` | diagnostic dump (max 64 bytes, upper-case hex, space separated) when the pump sent bytes but no valid reply; precedes the `ERR` line | INO:400-409 |
| `ERR,pump_timeout` | nothing received from the pump | INO:414 |
| `ERR,pump_bad_reply` | bytes received but no valid `/0…` frame | INO:413 |
| `ERR,pump_echo_only` | only a frame starting with `/` but not `/0` was seen (own echo) | INO:412 |
| `ERR,pump_not_found_ascii_9600_addr0` | `Y,DETECT` failed | INO:452 |
| `ERR,fixed_addr_0` / `OK,addr=0` | `Y,ADDR,<n>` | INO:888-889 |
| `ERR,fixed_baud_9600` / `OK,pump_baud=9600` | `Y,BAUD,<n>` | INO:891-892 |
| `OK,syringe_uL=<n>` | `Y,SYR` | INO:899-900 |
| `ERR,bad_pump_cmd` | unknown `Y,` key (with a valid integer argument) | INO:935 |
| `WEIGHT,<grams, 3 decimals>,RAW,<raw, 1 decimal>,ST,<1\|0>` | `W,READ`, and periodically while streaming | INO:663-665 |
| `ERR,timeout(module did not respond)` | a weight read failed (`W,READ` or a stream tick) | INO:651 |
| `ERR,stream_stopped_no_reply` | 3 consecutive failed stream reads → stream switched off | INO:655 |
| `ERR,scale_timeout` | `W,TARE`, `W,CAL`, `W,RD` could not read the module | INO:795, 802, 843 |
| `ERR,no_change` | `W,CAL` with reading equal to tare | INO:805 |
| `ERR,bad_scale_cmd` | unknown `W,` key | INO:848 |
| `OK,tare_raw=<x.x>` | `W,TARE` | INO:798 |
| `OK,div=<x.xxxxxx>,offset=<x.xxx>` | `W,CAL` | INO:807-808 |
| `OK,div=<x.xxxxxx>` | `W,DIV` | INO:811 |
| `OK,offset=<x.xxx>` | `W,OFFSET` | INO:814 |
| `OK,avg=<n>` | `W,AVG` | INO:816 |
| `OK,stable_g=<x.xxx>` | `W,STABLE` | INO:819 |
| `OK,stream_hz=<n>` | `W,STREAM` | INO:822 |
| `OK,baud=<n>` | `W,BAUD` | INO:825 |
| `CFG,baud=…,addr=…,fc=…,reg=…,fmt=…,div=…,offset=…,avg=…,stable_g=…,stream_hz=…` | `W,CFG`, and as the reply of `W,SET` | INO:748-759 |
| `REGS,<HH HH … >` (each byte followed by a space) | `W,RD` | INO:844-847 |
| `SCAN,…`, `  FOUND,baud=…` (two leading spaces), `SNIFF,…`, `LOOP,…`, `HINT,…`, help lines | diagnostics, §5.4 | INO:669-778 |

Async lines that can appear **between a command and its reply** or at any idle moment: `WEIGHT,…`, `STATE,…`, `ERR,timeout(module did not respond)`, `ERR,stream_stopped_no_reply`. A host parser must not treat those `ERR,…` lines as the failure of an unrelated pending command (the V7 backend filters exactly this: `backend/transport.py:235-241`).

Float formatting caveat **[INFER, Arduino core behaviour]**: `Serial.print(float)` prints `nan`, `inf` or `ovf` (for |value| > 4294967040) instead of digits; a host parser for `WEIGHT` should tolerate that.

### 1.4 The `STATE` line (INO:939-963)

Example right after boot:
```
STATE,a=-1,sa=idle,b=-1,sb=idle,pump=unknown,proto=ascii,baud=9600,addr=0,syringe_uL=5000,valves=00,cal_pulses_per_ml=4237.01,max_flow_uL_min=80000,w_stream_hz=10,cleaning=0
```

| Field | Meaning |
|---|---|
| `a` | selected motor of group A (2..5) or `-1` = none |
| `sa` | group A state: `wait` (valve pre-open delay running, motor not yet started), `run` (continuous), `move` (finite move in progress), `idle` (INO:324-328) |
| `b`, `sb` | same for group B (6..9) |
| `pump` | `online` if the last exchange with the syringe pump produced a valid reply, else `unknown` (never "offline"); starts `unknown` |
| `proto=ascii,baud=9600,addr=0` | literal text |
| `syringe_uL` | current syringe volume used for µL→step conversion (RAM, default 5000) |
| `valves` | one character per valve, valve 1 first; `1` = open (output HIGH) |
| `cal_pulses_per_ml` | compile-time `PULSES_PER_ML` printed with 2 decimals → `4237.01` (HW:27 is `4237.007613F`) |
| `max_flow_uL_min` | compile-time `MAX_FLOW_UL_MIN` → `80000` (HW:29). **Informational only — the firmware does not enforce it**; it enforces only `MAX_SPS` |
| `w_stream_hz` | current weight stream rate, 0 = off |
| `cleaning` | `1` while the auto-clean schedule is active |

`STATE` is emitted without being asked when:
- a delayed valve close fires (`serviceValves`, INO:152-163);
- a queued (valve-pre-open) start fires and the motor actually starts (INO:197-200);
- a finite `P` move reaches its target (INO:206-210);
- each of the three auto-clean transitions (INO:304-321).

`STATE` is **not** emitted after `S`, `V`, `P`, `X`, `C` themselves. Consequences: a `V` on a motor without a linked valve (3..8) starts instantly and produces no `STATE` at all; after `X` nothing announces `a=-1,b=-1,valves=00,w_stream_hz=0`. A host that needs a fresh picture must send `?`.

Two `STATE` lines can come back to back (valve close and motor event in the same pass, INO:213-214).

---

## 2. Peristaltic pumps, relays, valves, auto-clean (b, c)

### 2.1 Hardware arrangement

- Motors are numbered **2..9** and "motor n's relay is driven by D<n>" (HW:17-20; GEN:38-41 enforces `id == pin` and contiguous numbering; INO:232 literally does `digitalWrite(motor, HIGH)`).
- Group A = motors 2,3,4,5 on driver 1 (PUL=D22, DIR=D23, ENA=D24). Group B = motors 6,7,8,9 on driver 2 (PUL=D25, DIR=D26, ENA=D27). `GROUP_B_FIRST 6` (HW:20). The firmware hard-codes 4 relays per group (`groupFirst(group) + 4`, INO:102) and exactly 2 groups (arrays of size 2; GEN:33-34).
- Labels from YAML:140-152: `motor 2 · water`, `motor 3`, `motor 4`, `motor 5`, `motor 6`, `motor 7`, `motor 8 · OUT`, `motor 9 · CuSO₄`.
- **[DOC wiring-motors.html §3]** Electrically: D<n> → ULN2803A input → relay coil (MY4N-GS, 24 V). The relay's four COM contacts carry the driver's A+/A−/B+/B− (daisy-chained through the 4 relays of the group), the NO contacts go to motor n. So a HIGH on D<n> connects motor n's windings to its group's driver. Driver inputs are wired common-cathode (PUL−/DIR−/ENA− to Mega GND). The doc insists COM jumpers must never cross groups (would short two driver outputs).
- Drivers: the YAML and hw_config.h say **TB6600** (`driver_model: TB6600`, YAML:60; HW:9 "two TB6600"; switches S1–S6 = OFF,ON,OFF,ON,ON,OFF → 8 microsteps, 1.5 A, YAML:50-63). The sketch comments and `wiring-motors.html` still say **DM542J** (INO:96, doc §1). Both give 8 microsteps = **1600 pulses/rev**. Which driver is physically installed cannot be determined from the code.
- Enable polarity (INO:96-99): `digitalWrite(ENA, LOW)` = enabled, `HIGH` = released.

### 2.2 Motor selection = "cold" relay switching (INO:222-237)

`selectMotor(motor)`, when the motor is not already the selected one of its group:
1. `stopGroupMotion(group, true)` — stop stepping; if the group was active and its motor has a linked valve, schedule that valve to close after 1000 ms
2. driver released (ENA HIGH)
3. wait 5 ms
4. all 4 relay pins of that group LOW
5. wait `SETTLE_MS` = 60 ms (HW:23)
6. target relay pin HIGH
7. wait 60 ms
8. driver enabled (ENA LOW); `cur[group] = motor`

Total ≈125 ms, blocking for USB commands, but the waits call `serviceMotors()` so the *other* group keeps stepping. **[DOC]** "switching sequence is always:Release this group of drives → This group of relays are all off → connect to target → Re-enable,And it only affects your own group. This is forced by firmware".

Consequences:
- Only one motor per group can be connected at a time; two motors in total can run simultaneously (one of 2..5 and one of 6..9).
- Commanding a different motor in a group whose motor is running **silently stops the running one** (no error) and switches.
- After a motor stops, its relay stays energised and its driver stays **enabled** indefinitely. The only ways to release are `X`/`STOP` (releases both groups) or an MCU reset. There is no per-group "release" command.

### 2.3 How step pulses are generated

- Library: `AccelStepper` in `DRIVER` mode, two instances `drv[0]` (D22/D23) and `drv[1]` (D25/D26) (INO:50, 63-66).
- Setup per driver (INO:1097-1108): `setMaxSpeed(MAX_SPS)` = 8000, `setAcceleration(4 * MAX_SPS)` = 32000 steps/s², `setMinPulseWidth(MIN_PULSE_US)` = 5 µs (HW:24; default 3 if undefined).
- Pulses are produced by **polling**: `serviceMotors()` (INO:193-215) calls `runSpeed()` (continuous mode, constant speed, **no acceleration ramp**) or `run()` (finite mode, accelerated) for each group that has a selected motor. `serviceMotors()` is called from `loop()` and from inside every waiting loop (`serviceDelay`, pump reply wait, Modbus reply wait, sniff, loop-test). No timer interrupt is used.
- Continuous `V`: `drv.setSpeed(sps)` → runs at that speed immediately; sign = direction.
- Finite `P`: `drv.setMaxSpeed(fabs(sps)); drv.move(steps)` (INO:186-189). Ramp at 32000 steps/s²: reaching 2825 sps takes ≈88 ms / ≈125 steps.
- Stop (`stopGroupMotion`, INO:165-177): `moveTo(currentPosition()); setSpeed(0)` — instantaneous, no deceleration.
- Limits enforced by the firmware: motor 2..9; `|sps| ≤ 8000` (`V`), `0 < sps ≤ 8000` (`P`). Nothing else: no step-count limit, no run-time limit, no flow limit.
- `PULSES_PER_REV 1600L` (HW:28) is defined but **not used** by the sketch. `PULSES_PER_ML` and `MAX_FLOW_UL_MIN` are only echoed in `STATE`. All mL ↔ steps conversion is the host's job.

Conversion numbers the host uses (YAML:84-103, doc wiring-motors §1): speed coefficient `pulses_per_ml: 4237.00761337` (sps = mL/min × 4237.0076 / 60 → 40 mL/min = 2825 sps, 10 = 706, 3 = 212, 80 = 5649); dose coefficient `dose_pulses_per_ml: 4558.96147256` (steps = mL × 4558.96; 50 mL = 227948 steps); `max_flow_ml_min: 80`. 8000 sps = 300 rpm.

Things to be aware of **[INFER]** (from the code structure and AccelStepper's published behaviour; the library source is not in the repo and its installed version is unknown):
1. **Speed clamp after a finite move.** `startMotorNow` lowers the driver's max speed to the `P` command's `sps` (INO:188) and nothing ever raises it again except another `P` with a higher `sps` or a reset. AccelStepper's `setSpeed()` constrains to ±maxSpeed, so after e.g. `P,n,…,2825` any later `V,m,5649` on a motor of the **same group** would run at 2825 sps while the firmware still answers `OK`. Needs a bench check before the merged UI offers manual flows above the dose flow.
2. **Stepping pauses during blocking serial output.** `HardwareSerial::flush()` in `scaleSend` blocks ≈8.3 ms per weight request at 9600 baud (INO:551), and a `STATE` line (≈165 chars) overflows the 64-byte TX buffer and blocks ≈9 ms. `runSpeed()` does not catch up missed time, so with the 10 Hz weight stream on, the real continuous step rate is somewhat lower than commanded; a finite `P` move still delivers exactly the commanded number of steps but takes longer than `steps / sps`. A host must therefore wait for the `STATE … idle` event, not for a computed duration.
3. AccelStepper's documentation quotes ≈4000 steps/s as the reliable maximum on a 16 MHz AVR; the config allows 5649 sps (80 mL/min) and the firmware 8000. The real achievable rate on this rig is not determinable from the code.

### 2.4 Solenoid valves and the motor↔valve interlock

- 2 valves: `VALVE_PIN_LIST { 10, 11 }` (HW:42) → valve 1 = D10, valve 2 = D11. Output HIGH = energised = **open**; they are normally-closed valves (INO:17, 116-122). Labels YAML:180-181: `solenoid valve 1 · water`, `solenoid valve 2 · CuSO₄`.
- **[DOC wiring-valves.html]** D10/D11 → ULN2803A #1 IN5/IN6 → LB2HN-24DS relay coil → relay contact switches +24 V to the valve (MUPH DC24V Normally closed type), a 1N4007 across each valve. No position feedback: the optional D32/D33 read-back is explicitly **not implemented** ("The current firmware does not have this function").
- Link table `MOTOR_VALVE_LIST { 1, 0, 0, 0, 0, 0, 0, 2 }` (HW:43), indexed by `motor − 2`: **motor 2 ↔ valve 1 (water)**, **motor 9 ↔ valve 2 (CuSO₄)**, all other motors have no valve.
- **There is no valve command.** Valves move only as a side effect of their motor. **[DOC]** "Manual valve control is no longer available. The console is not on/close/pulse button,HTTP API Neither `valves.*` function"; the old `E,<valve>,<0|1>` command "This version has been removed".

Timing (HW:44-45: `VALVE_OPEN_BEFORE_MS 1000UL`, `VALVE_CLOSE_AFTER_MS 1000UL`):

| Event | Sequence | Ref |
|---|---|---|
| Start linked motor (`V` or `P`, also inside `C`) | valve opens now → group state `wait` → 1000 ms later the motor starts → async `STATE` | INO:253-260, 197-200 |
| Stop (`V,n,0`), finite move complete, or the group being switched to another motor | motor stops now → valve stays open 1000 ms → valve closes → async `STATE` | INO:139-150, 174-176, 208 |
| Restart the same linked motor while its close is pending | pending close is cancelled, valve stays open, new 1000 ms pre-open wait | INO:132-137 |
| `X` / `STOP` | valves close **immediately**, no delay | INO:124-130, 1062 |
| MCU reset | pins go to input, then `setup()` drives them LOW → closed, no delay | INO:1092-1095 |

If `VALVE_CLOSE_AFTER_MS` were 0 the valve would close at once (INO:143-145). All timing uses wrap-safe `millis()` comparison (INO:107-109).

I found no path in which a linked motor steps while its valve output is LOW, nor one in which a valve stays open for more than 1 s after its motor stopped (short of a firmware hang).

### 2.5 Auto-clean `C` (INO:281-322, HW:32-38)

Constants are compiled in: `CLEAN_WATER_MOTOR 2`, `CLEAN_OUT_MOTOR 8`, `CLEAN_WATER_SPS 2825.0F`, `CLEAN_OUT_SPS 2825.0F`, `CLEAN_WATER_LEAD_MS 30000UL`, `CLEAN_OVERLAP_MS 45000UL`, `CLEAN_OUT_TAIL_MS 45000UL`. Changing them means editing `hardware.yaml`, regenerating `hw_config.h` and re-flashing.

`startCleaning()`: cancel any schedule → `stopAllMotors()` → `closeAllValves()` → pre-select motor 8 (≈125 ms) → queue motor 2 continuous at 2825 sps (select ≈125 ms, valve 1 opens, start in 1000 ms) → compute the three deadlines → `cleaningActive = true` → reply `OK,cleaning_started` (≈0.25 s after the command).

Timeline, T0 = the moment the water motor actually starts (≈1.25 s after the command):

| Time | Event | Async line |
|---|---|---|
| T0 | motor 2 (water) starts, valve 1 already open | `STATE … a=2,sa=run … valves=10 … cleaning=1` |
| T0 + 30 s | motor 8 (OUT) starts | `STATE` |
| T0 + 75 s | motor 2 stops | `STATE` |
| T0 + 76 s | valve 1 closes | `STATE` |
| T0 + 120 s | motor 8 stops, `cleaningActive = false` | `STATE … cleaning=0` |

During cleaning: `C`, `S,…`, `P,…`, `V,…` → `ERR,cleaning_active`. `X` aborts it. After it ends motors 2 and 8 remain selected with their drivers enabled.

The cleaning schedule is advanced only from `loop()` (`serviceCleaning`, INO:1130) — **not** from inside blocking waits. A long blocking command issued during cleaning (worst: `W,SCAN`, `W,SNIFF,<long>`) postpones the transitions while the pumps keep running.

---

## 3. Syringe pump (b)

- Device: RUNZE SY-01B (YAML:184, doc wiring-pump.html). UART: `PUMP_SERIAL Serial1` — TX1 = D18, RX1 = D19 (HW:48), `PUMP_BAUD 9600UL` (8N1), `PUMP_ADDR 0`, `PUMP_FULL_STROKE 12000L`, `PUMP_DEFAULT_SYRINGE_UL 5000L` (HW:49-52). The header and the sketch only say "12000"; that the unit is half-steps is **[DOC]** (`docs/wiring-pump.html:82` "full stroke 12000 half step", `:356` "ASCII(DT)protocol uses half step,full stroke 12000 step").
- **[DOC]** Through a MAX13487 auto-direction TTL↔RS485 board to the pump's DB15 (pin 11 = A, 12 = B, 10 = signal ground, 1 = +24 V, 9 = −V). The firmware has **no DE/RE pin** for the pump (INO:346).
- The firmware is a thin bridge. It does not track pump position, does not wait for a stroke to finish, and does not queue.

### 3.1 Wire protocol to the pump (INO:335-417)

Request: `/` + address character `'1' + PUMP_ADDR` (= `1`) + body + `\r`. The RX buffer is flushed before each send. Bodies used:

| Host command | Body sent | Frame on the wire | Timeout |
|---|---|---|---|
| `Y,DETECT` or `Y,SCAN` | `Q` | `/1Q\r` | 500 ms |
| `Y,INIT` | `ZR` | `/1ZR\r` | 2500 ms |
| `Y,Q` (position) | `?` | `/1?\r` | 1500 ms |
| `Y,ST` (status) | `Q` | `/1Q\r` | 1500 ms |
| `Y,STOP` | `T` | `/1T\r` | 1500 ms |
| `Y,VALVE,<n>` | `I<n>R` | `/1I2R\r` | 1500 ms |
| `Y,ASP,<steps>` | `P<steps>R` | `/1P1200R\r` | 1500 ms |
| `Y,DISP,<steps>` | `D<steps>R` | | 1500 ms |
| `Y,ASPUL,<uL>` | `P<steps>R` | | 1500 ms |
| `Y,DISPUL,<uL>` | `D<steps>R` | | 1500 ms |
| `Y,POS,<steps>` | `A<steps>R` | | 1500 ms |
| `Y,SPEED,<n>` | `V<n>R` | | 1500 ms |
| `X` / `STOP` | `T` | `/1T\r` | not awaited |

Naming trap: `Y,Q` sends the pump's `?` (position) and `Y,ST` sends the pump's `Q` (status).

Reply parsing: resynchronises on `/`; a frame ends at `\r`, `\n` or `0x03`; it is valid if it starts with `/0` and has at least 3 characters. Character 3 is the status byte: `error = status & 0x0F`, `ready = (status & 0x20) != 0`; anything after it is `data`. The handler returns as soon as a valid frame is parsed (normally a few tens of ms), otherwise after the timeout.

### 3.2 `Y,` commands — syntax, ranges, replies (INO:851-937)

| Command | Argument | Reply on success | Errors |
|---|---|---|---|
| `Y,DETECT` / `Y,SCAN` | — | `FOUND,proto=ascii,baud=9600,addr=0` | `ERR,pump_not_found_ascii_9600_addr0` (possibly preceded by `ASCIIRX,…`) |
| `Y,INIT` | — | `PUMP,proto=ascii,ready=…,error=…` | `ERR,pump_timeout` / `ERR,pump_bad_reply` / `ERR,pump_echo_only` |
| `Y,Q` | — | `PUMP,…,data=<position>` | same |
| `Y,ST` | — | `PUMP,…` | same |
| `Y,STOP` | — | `PUMP,…` | same |
| `Y,ADDR,<n>` | must be 0 | `OK,addr=0` | `ERR,fixed_addr_0` |
| `Y,BAUD,<n>` | must be 9600 | `OK,pump_baud=9600` | `ERR,fixed_baud_9600` |
| `Y,SYR,<uL>` | 1..100000 | `OK,syringe_uL=<n>` (RAM only, no pump traffic) | `ERR,arg` |
| `Y,VALVE,<n>` | 1..15 | `PUMP,…` | `ERR,arg` |
| `Y,ASP,<steps>` / `Y,DISP,<steps>` | 1..12000 | `PUMP,…` | `ERR,arg` |
| `Y,ASPUL,<uL>` / `Y,DISPUL,<uL>` | µL such that steps is 1..12000 | `INFO,pump_steps=<n>` then `PUMP,…` | `ERR,arg` |
| `Y,POS,<steps>` | 0..12000 (absolute) | `PUMP,…` | `ERR,arg` |
| `Y,SPEED,<n>` | 1..6000 (raw pump speed units) | `PUMP,…` | `ERR,arg` |
| other key | | | `ERR,bad_pump_cmd` — but if the argument is not a strict integer the answer is `ERR,arg` first (INO:881-885) |

- µL → steps: `steps = round(uL × 12000 / syringe_uL)` (INO:461-465). With the 5000 µL default: 1 step = 0.4167 µL.
- **[DOC wiring-pump §8]** `original speed = mL/s × 12000 ÷ Syringe capacity(mL)`; raw 6000 = 2.5 mL/s with a 5 mL syringe. YAML:194-195: `syringe_options_ul: [5000, 1250]`, `valve_positions: 3` (the firmware accepts up to 15).
- `error=<n>` is passed through untouched. **[DOC wiring-pump §10]** meanings: `error=7` The device is not initialized, `error=9` Piston overload, `error=10` Valve overload, `error=2`/`3` Parameter out of range.
- `syringe_uL` lives in RAM only → back to 5000 after every Mega reset.
- `pump=online|unknown` in `STATE` reflects only the last awaited exchange.
- Whether the SY-01B answers a motion command immediately (and then reports `ready=0` until done) is a property of the pump, not visible in this code. The firmware returns after the first reply, so a host has to poll `Y,ST` and look at `ready` before the next move.

---

## 4. Timing, blocking, interruption (c)

Everything runs in one thread. "Blocking" below means: no USB command is read (so **`X` is not seen**), no weight stream tick, no auto-clean transition — but stepping and valve timers continue, because the waits call `serviceMotors()`.

| Operation | Blocks for | Steppers serviced meanwhile? |
|---|---|---|
| `S` / implicit selection change in `V`/`P` | ≈125 ms | yes |
| `C` | ≈250 ms | yes |
| Any awaited `Y,` command | until the pump replies; on no reply 1.5 s (`Y,INIT` 2.5 s, `Y,DETECT` 0.5 s) | yes |
| One weight read (`W,READ`, each stream tick, `W,TARE`, `W,CAL`) | request transmit ≈8.3 ms (not serviced) + until reply; 250 ms on no reply | yes while waiting, **no** during the ≈8.3 ms transmit |
| `W,RD` | up to 300 ms | yes |
| `W,LOOP` | ≥300 ms | yes |
| `W,BAUD` | 20 ms `delay()` | **no** |
| `W,SNIFF[,sec]` | 3 s by default; `<sec>` has **no upper limit** | yes |
| `W,SCAN` | text says "about 40 Second"; by my arithmetic up to ≈60 s when nothing answers (768 probes × (70 ms + request transmit time)) | mostly; four `delay(40)` + one `delay(20)` are not serviced |

Other facts:
- **No watchdog, no heartbeat, no host-presence check** (no `wdt`, no timeout logic anywhere). If the PC stops talking, the firmware simply keeps doing what it was doing: a continuous `V` runs forever, a `P` finishes, auto-clean finishes, the weight stream keeps printing.
- **No persistent storage** (no EEPROM). Every setting changed by command is lost at reset.
- An `X` sent during a blocking operation is executed only after that operation returns. Worst practical cases: `Y,INIT` with an unresponsive pump (2.5 s), `W,SNIFF`, `W,SCAN`.
- `X` does not wait for the syringe pump to acknowledge the `T`.

### Boot / reset sequence (INO:1079-1111)

1. `Serial` 115200, `Serial1` 9600, `Serial2` 9600; D36 LOW (RS485 receive)
2. D2..D9 outputs LOW (all motor relays off)
3. D10, D11 outputs LOW (both valves closed)
4. D24, D27 HIGH (both drivers released); AccelStepper configured
5. prints `READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0`
6. `loop()` starts — **weight streaming at 10 Hz starts by itself** (`wStreamHz = SCALE_STREAM_HZ`, INO:520). If the transmitter does not answer, three `ERR,timeout(module did not respond)` lines and then `ERR,stream_stopped_no_reply` appear within about a second and the stream turns itself off.

Nothing is sent to the syringe pump at boot. State after reset: no motor selected, `pump=unknown`, `syringe_uL=5000`, scale divisor 1.0 / offset 0 / avg 1 / stable 0.015 g / stream 10 Hz / Modbus defaults.

**[INFER, standard Mega behaviour]** Opening the USB serial port toggles DTR and resets the Mega (the backend waits `boot_delay_s: 2.5` after opening, YAML:35, `backend/transport.py:153-155`). So (re)connecting from the PC stops all pumps and closes the valves as a side effect — but a syringe-pump stroke in progress is not stopped by that. Closing the port does not reset the board, so a host crash during a continuous `V` leaves the pump running.

**[DOC wiring-motors §5/§9, wiring-valves §11]** Poor 24 V wiring can make the Arduino reboot spontaneously ("Random out of sync or Arduino Reboot"). A host should treat an unexpected `READY` line as "all RAM settings were lost". The current backend only records the banner (`backend/transport.py:222-223`) and does not re-apply settings in that case.

---

## 5. Weighing module (b)

- UART: `SCALE_SERIAL Serial2` — TX2 = D16, RX2 = D17 (HW:55). Direction pin `SCALE_DE_PIN 36` (HW:56): HIGH while transmitting with 60 µs guard on each side, LOW otherwise (INO:528-531). **[DOC]** DE and RE of the RS485 board are bridged and wired to D36; an auto-direction board can leave D36 unconnected.
- Chain **[DOC wiring-scale.html]**: load cell (red E+, black E−, green S+, white S−) → weighing transmitter (12–24 V, Modbus RTU slave) → RS485 → TTL board → Mega. A separate RS485 board from the syringe pump's.
- Protocol: Modbus RTU master, CRC16 poly 0xA001 init 0xFFFF, CRC low byte first (INO:534-553). Request = `addr, fc, regHi, regLo, cntHi, cntLo, crcLo, crcHi`. Reply validated on address, exception bit, byte count and CRC (INO:578-584).
- Defaults (HW:60-64, all changeable at run time, RAM only): `SCALE_BAUD 9600L`, `SCALE_ADDR 1`, `SCALE_FUNC 3` (3 = holding, 4 = input registers), `SCALE_REG 0`, `SCALE_FMT 1`.
- Formats (INO:590-603): `0` int16, `1` int32 high word first, `2` int32 low word first, `3` float ABCD, `4` float CDAB. Format 0 reads 1 register, the others 2. The value is carried as a 32-bit `float` named raw.

### 5.1 Conversion, averaging, stability

- Ring buffer of the last 16 raw readings (INO:524-525).
- `grams = (average of the last avgN raw readings − scaleOffset) / scaleDiv` (INO:661-662). Defaults: `avgN = SCALE_AVG_SAMPLES = 1` (no averaging), `scaleOffset = 0`, **`scaleDiv = 1.0`** — so an uncalibrated board reports raw counts as "grams". (The wiring doc's remark that the value is "is always 0.000" before calibration does not match the code.)
- Stable flag `ST` (INO:632-644): over the last up to 6 readings (at least 3 needed), `(max − min) / scaleDiv ≤ stableG`; default `SCALE_STABLE_G 0.015000F` g.
- The saved calibration on this PC (`config/scale_calibration.json`): `"offset": -1.0`, `"divisor": 287703.03125`, `"reference_grams": 200.0`. It is held by the host and pushed to the firmware after each start.

### 5.2 Sampling / streaming

- Stream rate `wStreamHz`, default `SCALE_STREAM_HZ 10` (HW:57), range 0..20. Period = integer `1000 / wStreamHz` ms (10 Hz → 100 ms) (INO:1125-1128). Each tick performs one Modbus read and prints one `WEIGHT` line.
- Failure handling (INO:648-658): each failed read prints `ERR,timeout(module did not respond)`; three consecutive failures while streaming set `wStreamHz = 0` and print `ERR,stream_stopped_no_reply`. It does not restart by itself — the host must send `W,STREAM,<hz>` again.
- `X` / `STOP` also sets the stream to 0.
- The transmitter's own conversion rate and internal filtering are not visible in the firmware.

### 5.3 `W,` commands (INO:780-849)

| Command | Argument / range | Effect | Reply |
|---|---|---|---|
| `W,READ` | — | one read, pushed into the history | `WEIGHT,…` or `ERR,timeout(module did not respond)` |
| `W,STREAM,<Hz>` | 0..20; missing or non-numeric = 0 | set stream rate, next tick fires at once | `OK,stream_hz=<n>` / `ERR,arg` |
| `W,TARE` | — | one read; `scaleOffset` = average of last `avgN` raw; history cleared | `OK,tare_raw=<x.x>` / `ERR,scale_timeout` |
| `W,CAL,<grams>` | float > 0 | one read; `scaleDiv = (avg − offset) / grams` | `OK,div=<6 dec>,offset=<3 dec>` / `ERR,arg` / `ERR,scale_timeout` / `ERR,no_change` |
| `W,DIV,<divisor>` | float ≠ 0 | set `scaleDiv` directly | `OK,div=<6 dec>` / `ERR,arg` |
| `W,OFFSET,<raw>` | float, must be present | set `scaleOffset`, clear history | `OK,offset=<3 dec>` / `ERR,arg` |
| `W,AVG,<n>` | 1..16 | moving-average length | `OK,avg=<n>` / `ERR,arg` |
| `W,STABLE,<g>` | float > 0 | stability threshold | `OK,stable_g=<3 dec>` / `ERR,arg` |
| `W,BAUD,<baud>` | 1200..115200 | re-open Serial2 | `OK,baud=<n>` / `ERR,arg` |
| `W,SET,<addr>,<fc>,<reg>,<fmt>` | 4 integers, not range-checked | set Modbus parameters, clear history | a `CFG,…` line (not `OK`) / `ERR,arg` |
| `W,RD,<addr>,<fc>,<reg>,<count>` | 4 integers | raw register read, max 16 data bytes | `REGS,<HH HH … >` / `ERR,scale_timeout` / `ERR,arg` |
| `W,CFG` | — | print settings | `CFG,baud=9600,addr=1,fc=3,reg=0,fmt=1,div=1.000000,offset=0.000,avg=1,stable_g=0.015,stream_hz=10` (boot values) |
| `W,HELP` | — | 16 help lines | first `-- weight module command(All with W, beginning)--`, last `  W,CFG / W,HELP        display settings / This instruction` |
| `W,LOOP` | — | loop-back test (D16–D17 bridged) | `LOOP,received="<text>"` then one `HINT,…` |
| `W,SNIFF[,<sec>]` | default 3 | listen and hex-dump | §5.4 |
| `W,SCAN` | — | scan baud × address × function × register | §5.4 |
| other | | | `ERR,bad_scale_cmd` |

`W,TARE` and `W,CAL` only change firmware RAM. They report the new values (`tare_raw`, `div`, `offset`) so that the host can store them; the host restores them with `W,DIV` then `W,OFFSET`.

### 5.4 Diagnostic output (verbatim texts)

`W,SNIFF` (INO:669-690): `SNIFF,monitor <N> Second…`; hex rows (each row is opened by a `println()` plus two spaces, 16 bytes per row — so an empty line precedes the first row; with no data there is just one empty line); then `SNIFF,common <n> byte`; if bytes arrived `SNIFF,text=<ascii>` and `HINT,module will automatically send data The optocoupler of the → continuous output type`; otherwise `HINT,has no active output → Modbus Q&A type,Please execute SCAN`. Async `STATE` lines can land in the middle of a hex row.

`W,SCAN` (INO:692-726): `SCAN,Start scanning(about 40 Second)…`; for each baud in 9600, 19200, 38400, 4800: `SCAN,baud=<b>`; for each hit `  FOUND,baud=<b> address=<a> function code=<fc> scratchpad=<r> information Starting from=<HH HH … >`; then `SCAN,Complete,found <n>`; then `HINT,use SET,<address>,<function code>,<scratchpad>,<Format> Apply` or the four lines `HINT,None,Check in sequence:`, `HINT,① A, B Debugging try  ② module power supply and GND common connection`, `HINT,③ DE, RE Short-circuit and then receive D36  ④ Use first LOOP test Mega This end`. Scans addresses 1..16, functions 3 and 4, registers 0, 1, 2, 4, 8, 16; restores the configured baud afterwards.

`W,LOOP` (INO:728-745): hints `HINT,Mega of Serial2 normal → The problem is 485 module or subsequent lines` or `HINT,not received. Please put it first D16 and D17 Use a Dupont line to short-circuit directly and try again.`.

---

## 6. Safety-relevant behaviour and limits (d)

Enforced by the firmware:
- Motor index 2..9; step rate ≤ 8000 sps.
- One motor per driver group, switched only with the driver released (cold switching), 60 ms settle each side.
- Valve 1 / valve 2 open 1 s before and close 1 s after motors 2 / 9; no way to operate a valve on its own.
- During auto-clean, `S`/`P`/`V`/`C` are refused.
- Syringe pump arguments: steps 1..12000, absolute position 0..12000, speed 1..6000, valve 1..15, syringe volume 1..100000 µL; address and baud are fixed.
- Scale: stream 0..20 Hz, average 1..16, baud 1200..115200; the stream stops itself after 3 failed reads.
- `X`/`STOP`: steppers stop without ramp, drivers released, relays off, valves closed at once, `T` sent to the syringe pump, weight stream off.

Not provided by the firmware (the host or the hardware has to cover these):
- No watchdog / heartbeat / dead-man: a continuous `V` has no time limit and survives a PC crash or a closed port.
- No limit on `P` step count (volume) and no limit on total dispensed volume.
- No sensing of any kind for the peristaltic side: open-loop steppers, no valve read-back, no level / overflow / pressure / leak input, no emergency-stop input pin. The weight is only reported, never acted upon.
- `X` is a software stop that is only processed when the main loop is free (see §4); it is not an emergency stop. It also does not confirm that the syringe pump stopped.
- After any pump movement the driver stays enabled and the relay stays closed until `X` or reset.
- `max_flow_uL_min=80000` is advertised but not enforced.
- No acknowledgement that a motor actually turned or that liquid moved.

**[DOC]** electrical cautions worth carrying into the merged manual: never cross-jumper relay COM terminals between groups; both ULN2803A need pin 10 on +24 V; valves need their 1N4007 or the weight reading jumps when a valve closes; 24 V should be star-wired, ≥8 A recommended; "The pipeline must be filled with liquid before it is powered on.(Dry rotation will damage the valve seat)".

---

## 7. Pin map (e) — from hw_config.h

| Pin | Symbol | Function | Active level |
|---|---|---|---|
| D0 / D1 | `Serial` | USB link to the PC, 115200 | — |
| D2, D3, D4, D5 | motor number = pin | relays for motors 2..5 (group A) via ULN2803A #1 IN1..IN4 | HIGH = motor connected |
| D6, D7, D8, D9 | motor number = pin | relays for motors 6..9 (group B) via ULN2803A #2 IN1..IN4 | HIGH = motor connected |
| D10 | `VALVE_PIN_LIST[0]` | solenoid valve 1 (water, follows motor 2) via ULN2803A #1 IN5 | HIGH = open |
| D11 | `VALVE_PIN_LIST[1]` | solenoid valve 2 (CuSO₄, follows motor 9) via ULN2803A #1 IN6 | HIGH = open |
| D16 | TX2 (`SCALE_SERIAL Serial2`) | to scale RS485 board DI | — |
| D17 | RX2 | from scale RS485 board RO | — |
| D18 | TX1 (`PUMP_SERIAL Serial1`) | to syringe-pump RS485 board DI | — |
| D19 | RX1 | from syringe-pump RS485 board RO | — |
| D22 | `PUL1` | step pulses, driver 1 (group A) | — |
| D23 | `DIR1` | direction, driver 1 | — |
| D24 | `ENA1` | enable, driver 1 | LOW = enabled, HIGH = released |
| D25 | `PUL2` | step pulses, driver 2 (group B) | — |
| D26 | `DIR2` | direction, driver 2 | — |
| D27 | `ENA2` | enable, driver 2 | LOW = enabled, HIGH = released |
| D36 | `SCALE_DE_PIN` | RS485 DE+RE of the scale board | HIGH = transmit |
| D32 / D33 | — | optional valve read-back in the doc; **not used by the firmware** | — |

Other constants in hw_config.h: `FIRST_MOTOR 2`, `LAST_MOTOR 9`, `GROUP_B_FIRST 6`, `MAX_SPS 8000.0`, `SETTLE_MS 60`, `MIN_PULSE_US 5`, `PULSES_PER_ML 4237.007613F`, `PULSES_PER_REV 1600L`, `MAX_FLOW_UL_MIN 80000L`, `NUM_VALVES 2`, plus the cleaning, pump and scale constants quoted above. By reading, every value in hw_config.h matches what `gen_firmware_config.py` would produce from the current `hardware.yaml` (I did not run the `--check` mode).

### gen_firmware_config.py

- Usage (GEN:8-9): `python tools/gen_firmware_config.py` writes the header; `--check` only compares and returns 1 with `hw_config.h Expired,Please execute python tools/gen_firmware_config.py` when stale.
- It imports `backend.config.load_config` (GEN:25) — it depends on the V7 backend package and on `config/hardware.yaml` staying where they are (or being moved together).
- Hard requirements it enforces: exactly two motor groups (`The current firmware is fixed to two sets of drives.,motors.groups must be exactly two`), every channel `id == pin` (`firmware assumes ”motor n Depend on D<n> Control”…`), contiguous ids (`Motor numbers must be consecutive,For example 2~9`).
- Cleaning step rates are derived: `round(flow_ml_min × pulses_per_ml / 60)` (GEN:58-62).
- Workflow after a pin / coefficient change (GEN:11-14): run the generator → re-flash with the Arduino IDE → restart the backend. hw_config.h says "automatically generated,Please do not modify by hand".

---

## 8. Identification and handshake (f)

- The only identification is the boot banner `READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0`. It contains **no version number, build date or config hash**, and there is no command that repeats it.
- The generated header's comment carries the YAML `meta` text: "independent laboratory v5 — 8 grain RZ1030B-8 peristaltic pump + automatic cleaning + syringe pump + 2 solenoid valve + 10 Hz Weight closed loop" (HW:4) — "v5", although the folder is named v7. This text is not sent over serial.
- No handshake is required. Liveness check = send `?`, expect `STATE,`. The two compiled-in numbers in `STATE` (`cal_pulses_per_ml=4237.01`, `max_flow_uL_min=80000`) are the only way to notice that the flashed firmware was built from a different config.
- Whether the firmware currently flashed in the Mega equals this source cannot be determined from the files.

---

## 9. What a host must do, and in what order (g)

1. Open the port at 115200 8N1; wait for the board to boot (the backend sleeps 2.5 s) or wait for the `READY` line.
2. Send `?` and parse `STATE`.
3. Restore the weighing calibration, because the firmware forgets it at every reset: `W,DIV,<divisor>` first, then `W,OFFSET,<offset>` (backend: `backend/functions/scale.py:146-153`).
4. Apply runtime scale settings: `W,AVG,<n>`, `W,STABLE,<g>`, `W,STREAM,<hz>` (backend: `backend/main.py:39-45`).
5. Syringe pump, if used: `Y,DETECT` → `Y,INIT` (before any move; otherwise the pump reports error 7 per the doc) → `Y,SYR,<uL>` if the syringe is not 5000 µL (again after every reset) → `Y,SPEED,<n>` → `Y,VALVE,<n>` → `Y,ASPUL` / `Y,DISPUL`; poll `Y,ST` until `ready=1` between moves.
6. Send **one command at a time** and wait for its terminating line before the next. Terminators used by the backend (`backend/transport.py:52-71`): `?` → `STATE,`; `S,` → `DONE,S` or `ERR`; `Y,` → `PUMP,` / `FOUND,` / `OK,` / `ERR`; `W,READ` → `WEIGHT,` / `ERR`; `W,CFG` → `CFG,`; `W,RD` → `REGS,` / `ERR`; `W,SCAN` → `SCAN,Complete`; `W,SNIFF` → `SNIFF,common`; `W,LOOP` → `HINT,`; everything else → `OK` / `ERR` / `DONE`. Ignore stream lines while waiting (§1.3). Note `W,SET` answers with `CFG,…`, which the backend's default terminators for `W,` (`OK`/`ERR`) do not match — the existing call at `backend/functions/scale.py:284` therefore sits out the full 3 s command timeout before returning the collected line. A new host should terminate `W,SET` on `CFG,` / `ERR`.
7. `P` (dose): `OK` means "accepted". Wait for the async `STATE` with the group back to `idle`; for motors 2 / 9 the sequence is `wait` → `move` → `idle` (valve still open) → valve closed one second later. Poll `?` as a fallback.
8. `V` on motors 3..8 gives no `STATE` at start; send `?` if the UI needs to show `run`.
9. After `X` / `STOP`: send `?`, and send `W,STREAM,<hz>` again — the stream was turned off. Motors are deselected; the syringe pump was told to stop without confirmation.
10. After `ERR,stream_stopped_no_reply`: fix the cause, then `W,STREAM,<hz>`.
11. Do not send `W,SCAN`, `W,SNIFF`, `W,BAUD` while pumps run or during auto-clean; give `W,SCAN` a timeout above 60 s (the backend's `long_command_timeout_s` is exactly 60.0, YAML:38).
12. Remember that starting another motor of the same group stops the one that is running, and that `V,<n>,0` for a motor that is not the selected one does nothing but still answers `OK`.
13. On an unexpected `READY`: redo steps 2–5.
14. Timeouts: backend uses 3.0 s per command (YAML:36); the longest normal firmware-side wait is `Y,INIT` at 2.5 s (the backend classes it as slow).

### Example exchanges

```
> ?
< STATE,a=-1,sa=idle,b=-1,sb=idle,pump=unknown,…,valves=00,…,w_stream_hz=10,cleaning=0

> V,2,2825            (water pump, linked valve 1)
< OK                  (≈125 ms later: relay switched, valve 1 open, state "wait")
< STATE,a=2,sa=run,…,valves=10,…          (≈1 s later, motor starts)
> V,2,706             (slow down on the fly)
< OK
> V,2,0
< OK
< STATE,a=2,sa=idle,…,valves=00,…         (≈1 s later, valve closes)

> P,9,45590,2825      (≈10 mL of CuSO₄ with the dose coefficient)
< OK
< STATE,…,b=9,sb=move,…,valves=01,…       (after the 1 s pre-open)
< STATE,…,b=9,sb=idle,…,valves=01,…       (move finished)
< STATE,…,b=9,sb=idle,…,valves=00,…       (1 s later)

> Y,ASPUL,500
< INFO,pump_steps=1200
< PUMP,proto=ascii,ready=<0|1>,error=<n>     (values come from the pump's status byte)

> X
< OK,all_stopped
```
(`WEIGHT,…` lines arrive every 100 ms in between and are omitted here.)

---

## 10. Inconsistencies noticed between files

| Topic | Says A | Says B |
|---|---|---|
| Stepper driver model | `hardware.yaml:60` and `hw_config.h:9`: TB6600 | `Arduino.ino:96` comment and `wiring-motors.html`: DM542J (with DM542J DIP settings) |
| System name | `hw_config.h:4` / `hardware.yaml:14`: "independent laboratory v5" | folder name: independent laboratoryv7 |
| Uncalibrated scale reading | `wiring-scale.html` §11: "value is always 0.000 … scaleDiv By default, it is calculated 0" | `Arduino.ino:514`: default `scaleDiv = 1.0`, so grams = raw counts |
| `W,SCAN` duration | `Arduino.ino:695` text and YAML:37: about 40 s | computed worst case ≈ 60 s |
| `W,SET` example | `wiring-scale.html` §9 example `W,SET,1,3,0,2` (format 2) | configured default format is 1 (HW:64) |
| Header comment of the sketch | `Arduino.ino:9-10` lists the pins as fixed | actual values come from hw_config.h (they agree today) |

---

## 11. Not determinable from these files

- Which firmware build is actually in the Mega, and which AccelStepper version it was compiled with.
- Whether the speed clamp after a `P` move (§2.3 item 1) and the reduced real step rate (§2.3 items 2–3) occur on the bench.
- The real maximum reliable step rate / flow.
- SY-01B behaviour beyond what the doc table says: when it replies relative to motion, what `T` does to its initialised state, full error-code list, meaning of the speed unit beyond the doc's formula.
- The weighing transmitter's model, register map, internal sample rate and filter; whether register 0 / int32 is net or gross weight.
- Which stepper driver model is installed and whether ENA polarity (HIGH = released) is correct for it as wired.
- Whether a hardware emergency stop or 24 V cut-off switch exists for the liquid-handling side (none is mentioned in the four wiring docs).
- Whether the Mega on this PC auto-resets on port open (assumed by the backend's boot delay).

---

## Cross-check

Done 2026-10-06 by a second reader who re-read `Arduino.ino` (all 1132 lines), `hw_config.h`, `config/hardware.yaml`, `tools/gen_firmware_config.py`, and all of `backend/*.py`, `backend/functions/*.py`, `frontend/index.html`. Nothing was run. Result: **no wrong statement about a command, a reply, a range or a timeout was found in this report.** Every `INO:` / `HW:` / `YAML:` line reference that was spot-checked (about 90) points at the right line.

### Corrections made in the text above

1. §3: "`PUMP_FULL_STROKE 12000L` (half-steps)" — the unit is not in the code. Reworded: the code says 12000; "half step" is from `docs/wiring-pump.html:82, 356`.
2. §5.4 `W,SNIFF`: the order of the empty line and the hex rows was described slightly wrong; reworded (the host skips empty lines anyway).

### Additions made in the text above

3. §1.2 `V`: a sign change on a running motor reverses at once without stop (same shortcut as a speed change).
4. §1.2 `V`/`P`: a `V` or `P` for the same motor during a finite `P` move aborts the move and prints **no** `STATE … idle` for it.

### Claims verified against the source (the ones a new host program depends on)

| # | Claim | Verdict | Where |
|---|---|---|---|
| 1 | USB 115200; command ends with `\n`, `\r` dropped, 95 characters max | correct | INO:1080, 1114-1122 |
| 2 | Only the text before the first comma is upper-cased; `Y,`/`W,` sub-key upper-cased again | correct | INO:496-503, 782, 853 |
| 3 | Dispatch order `Y,` → `W,` → `C` → cleaning lock for `S,`/`P,`/`V,` → `S,` → `P,` → `V,` → `X`/`STOP` → `?` → `ERR,bad_cmd` | correct | INO:966-1077 |
| 4 | `S,<m>` → `DONE,S` / `ERR,idx(2~9)`; ≈125 ms when the selection changes | correct | INO:997-1006, 222-237 |
| 5 | `P,<m>,<steps>,<sps>`: `0 < sps ≤ 8000`, no limit on steps, `OK` at once, completion only as `STATE` | correct | INO:1008-1028, 206-210 |
| 6 | `V,<m>,<sps>`: `abs(sps) ≤ 8000`; `V,<m>,0` is a no-op (still `OK`) when m is not the selected motor of its group | correct | INO:1030-1057 |
| 7 | `X`/`STOP`: cleaning cancelled, both groups stopped and released, relays off, valves closed at once, `/1T\r` to the syringe pump not awaited, `wStreamHz = 0`, reply `OK,all_stopped`, no `STATE` | correct | INO:1059-1069 |
| 8 | `C` → `OK,cleaning_started` after ≈250 ms; timeline T0 / +30 s / +75 s / +76 s / +120 s; `S`/`P`/`V`/`C` refused meanwhile, `Y`/`W`/`?`/`X` accepted | correct | INO:281-322, 981-995 |
| 9 | `STATE` field list and order; `cal_pulses_per_ml` prints as `4237.01` | correct | INO:939-963 |
| 10 | Unsolicited `STATE` only on: delayed valve close, queued start firing, finite move done, the three cleaning transitions | correct | INO:162, 197-214, 304-321 |
| 11 | Motor↔valve table motor 2 ↔ valve 1, motor 9 ↔ valve 2; 1000 ms before / 1000 ms after; `X` closes at once | correct | HW:43-45; INO:132-150, 124-130 |
| 12 | Syringe pump frames `/1…\r`; `Y,Q` → `?`, `Y,ST` → `Q`; timeouts 500 / 1500 / 2500 ms | correct | INO:335-347, 859-879, 450 |
| 13 | `Y,` ranges: SYR 1..100000, VALVE 1..15, ASP/DISP 1..12000, POS 0..12000, SPEED 1..6000; strict integer parser; ADDR/BAUD fixed | correct | INO:881-936 |
| 14 | `PUMP,proto=ascii,ready=<0 or 1>,error=<n>[,data=…]`; `ready = status & 0x20`, `error = status & 0x0F` | correct | INO:430-442 |
| 15 | `WEIGHT,<g 3 dec>,RAW,<raw 1 dec>,ST,<1 or 0>`; grams = (avg − offset) / div; defaults div 1.0, offset 0 | correct | INO:514-515, 661-665 |
| 16 | Stream: 10 Hz at boot, 0..20, three consecutive failures → `ERR,stream_stopped_no_reply` and stream off | correct | INO:520, 648-658, 820-822, 1125-1128 |
| 17 | `W,TARE` → `OK,tare_raw=<1 dec>`; `W,CAL,<g>` → `OK,div=<6 dec>,offset=<3 dec>`; `W,SET` answers with a `CFG,` line and no `OK` | correct | INO:794-808, 826-833 |
| 18 | One weight read waits at most 250 ms; `W,RD` 300 ms; `W,SCAN` 768 probes × 70 ms | correct | INO:608, 842, 693-704 |
| 19 | No watchdog, no EEPROM, no host-presence check anywhere in the sketch | correct | whole file |
| 20 | `hw_config.h` equals what `gen_firmware_config.py` renders from the current `hardware.yaml` | correct by hand: every `#define` recomputed from GEN:68-140 (e.g. `PULSES_PER_ML` = `4237.00761337` with `.6f`, `CLEAN_*_SPS` = `round(40 × 4237.00761337 / 60)` = 2825, `SCALE_STABLE_G` = `0.015` with `.6f`) | GEN:58-136 |
| 21 | Backend terminators quoted in §9 step 6 | correct | `backend/transport.py:52-71` |
| 22 | `W,SCAN` worst case ≈60 s | correct: 192 probes per baud × (70 ms + 8 bytes at that baud) = 15.07 + 14.26 + 13.86 + 16.66 s, plus 180 ms of `delay()` ≈ 60.0 s — the same as the backend's `long_command_timeout_s: 60.0`, so with no module answering, the backend call times out at about the moment the scan ends | INO:692-726; YAML:38 |

### Commands the backend sends, compared with this report

Complete list of what `backend/` puts on the wire (from `backend/main.py:42-45`, `functions/motors.py`, `functions/pump.py`, `functions/scale.py`, `transport.py:170`): `?`, `C`, `X`, `S,<m>`, `V,<m>,<int sps>`, `V,<m>,0`, `P,<m>,<int steps>,<int sps>`, `Y,DETECT`, `Y,INIT`, `Y,SYR,<int>`, `Y,VALVE,<int>`, `Y,SPEED,<int>`, `Y,ASPUL,<int>`, `Y,DISPUL,<int>`, `Y,STOP`, `W,DIV,<%.9g>`, `W,OFFSET,<%.9g>`, `W,AVG,<int>`, `W,STABLE,<%g>`, `W,STREAM,<int>`, `W,TARE`, `W,CAL,<%g>`, `W,CFG`, `W,LOOP`, `W,SNIFF,3`, `W,SCAN`, `W,SET,<a>,<fc>,<reg>,<fmt>`. **Every one is listed in this report with the same syntax.** Nothing is sent that the firmware does not implement. Not used by the backend at all: `STOP`, `Y,SCAN`, `Y,Q`, `Y,ST`, `Y,ASP`, `Y,DISP`, `Y,POS`, `Y,ADDR`, `Y,BAUD`, `W,READ`, `W,HELP`, `W,RD`, `W,BAUD`.

What the backend actually parses out of the replies: `STATE,` (key=value pairs `a`, `sa`, `b`, `sb`, `pump`, `syringe_uL`, `valves`, `cleaning`, `w_stream_hz`), `WEIGHT,` (fields 1, 3, 5), `READY…` (stored whole), `ERR,timeout…` / `ERR,stream_stopped…` (scale offline), `CFG,` (`offset`, `div`), `FOUND` (pump online), and "any line starting with `ERR`" (failure). Everything else is only passed through as text — in particular **`PUMP,…,error=<n>` and `ready=<0 or 1>` are never interpreted** by the backend.

### Further additions (not in the text above)

- **A stop does not always produce a `STATE`.** `V,<m>,0` on a motor without a linked valve (3..8) prints nothing but `OK` — the `idle` state is only visible through `?`. (With a linked valve a `STATE` follows one second later, when the valve closes.) The backend always sends `?` after `V`/`P`/`S`/`C`/`X`.
- **`W,TARE` and `W,CAL` use a single reading** when `avgN` is 1 (the configured value): one Modbus read, `histAvg(1)`, no averaging and no stability check (INO:794-808). A tare taken while the reading is still moving is stored as it is.
- **After `W,TARE`, `W,OFFSET` or `W,SET` the stable flag is 0 for the next two readings** (history cleared, `histStable()` needs 3; INO:797, 813, 833, 634).
- **`STATE` is about 175 bytes** including CR LF (counted on the boot example), longer than the 64-byte TX buffer.
- **`READY`, `STATE`, `FOUND`, `ASCIIRX` carry the literal text `baud=9600,addr=0`** and `S` errors the literal `idx(2~9)`; they do not follow `PUMP_BAUD`, `PUMP_ADDR`, `FIRST_MOTOR`, `LAST_MOTOR` if those were ever changed in the YAML.
- **Limits that exist only in the sketch, not in `hardware.yaml` / `hw_config.h`:** syringe speed 1..6000, syringe valve 1..15, syringe volume 1..100000 µL, pump timeouts 500/1500/2500 ms, scale read timeout 250 ms, stream 0..20 Hz, average 1..16, history 16, stability window 6 (min 3), 3 failures to stop the stream, acceleration `4 × MAX_SPS`, 5 ms release delay, 95-character command buffer.
- **Host-only constants (not known to the firmware):** `dose_pulses_per_ml` 4558.96147256, `dose_flow_ml_min`, everything under `gravimetric_dose`, `syringe_options_ul`, `valve_positions`, `boot_delay_s`, both command timeouts.
- Speed clamp after `P` (§2.3 item 1): agrees with how `AccelStepper::setSpeed()` is written in the published library (it constrains to ±`_maxSpeed`), so the inference stands; still unverified on the bench. With today's numbers it can only bite a manual run above 40 mL/min in a group that has done a `P` dose since the last reset.
- Answers to questions left open by the frontend reader: the only group states are `wait`, `run`, `move`, `idle` (INO:324-328); `C` does not look at or touch the syringe pump, so cleaning can start while the syringe pump moves; there is no command that reads back the syringe pump's valve position or speed (`Y,Q` = plunger position, `Y,ST` = status byte only).
