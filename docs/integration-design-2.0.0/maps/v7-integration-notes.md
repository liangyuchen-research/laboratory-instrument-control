# V7 integration notes — driving the Arduino Mega from another Python program

Digest of the three V7 reports (`v7-firmware.md`, `v7-backend.md`, `v7-frontend.md`) after a line-by-line cross-check against the sources on 2026-10-06. Nothing was run, no port was opened.

Sources, with the abbreviations used below (all under `C:/Users/PlasmaLab/Desktop/independent laboratoryv7/`):
`INO` = `firmware/Arduino/Arduino.ino` · `HW` = `firmware/Arduino/hw_config.h` · `YAML` = `config/hardware.yaml` · `TR` = `backend/transport.py` · `MO` = `backend/functions/motors.py` · `PU` = `backend/functions/pump.py` · `SC` = `backend/functions/scale.py` · `MAIN` = `backend/main.py` · `CFG` = `backend/config.py` · `REG` = `backend/registry.py` · `SIM` = `backend/simulator.py` · `GEN` = `tools/gen_firmware_config.py` · `FE` = `frontend/index.html`.

Markers: **[FACT]** read in the code. **[INFER]** follows from the code plus general knowledge of Arduino, AccelStepper, pyserial or CPython; not provable from these files. **[DOC]** stated only in `docs/wiring-*.html`. **[V7 host]** = what the existing V7 backend does (a choice, not a firmware requirement).

---

## 1. The link

| Item | Value | Source |
|---|---|---|
| Physical | USB serial of the Mega 2560 | INO:1080 |
| Settings | 115200 baud, 8N1, no flow control | INO:1080; YAML:31 |
| Host → Mega | one ASCII command per line, ended by `\n` (`\r` ignored); at most 95 characters | INO:1114-1122 |
| Mega → host | text lines ended by `\r\n`; a few contain UTF-8 Chinese | `Serial.println` throughout |
| Case | only the text before the first comma is case-insensitive; for `Y,` and `W,` also the sub-key | INO:496-503, 782, 853 |
| Command ids, checksums, echo | none | — |
| Handshake | none. Boot banner `READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0`, printed once per reset; no command repeats it; it has no version | INO:1110 |
| Liveness | `?` → `STATE,…` | INO:1071-1074 |
| Fingerprint of the flashed build | only `cal_pulses_per_ml=4237.01` and `max_flow_uL_min=80000` inside `STATE` | INO:954-957; HW:27, 29 |
| Reset on port open | **[INFER]** opening the port pulses DTR and resets the Mega. **[V7 host]** sleeps `boot_delay_s` 2.5 s after opening and does not look for `READY` | YAML:34-35; TR:153-155 |
| After any reset | relays off, drivers released, valves closed, no motor selected, weight stream **on at 10 Hz**, scale divisor 1.0 / offset 0 / avg 1 / stable 0.015 g, syringe volume 5000 µL, `pump=unknown`. Nothing is sent to the syringe pump | INO:1079-1111, 507-520, 90-91 |
| Exclusive port | one program at a time: the V7 service (`run.bat`), the Arduino IDE monitor and a merged program cannot share it | TR:1-6 |

The firmware is one loop without interrupts of its own. A command is read only when the loop is free; while a handler waits (table in §2.1) the next command — `X` included — sits in the UART buffer (**[INFER]** 64 bytes).

---

## 2. Wire protocol

### 2.1 Every command

"Blocks" = how long the firmware does not read the next command. "Ends with" = the line a host should wait for (prefix match). m = motor 2..9.

**System and peristaltic pumps**

| Command | Arguments | Success reply | Error replies | Blocks | Ends with |
|---|---|---|---|---|---|
| `?` | — | one `STATE,…` line | — | ≈0 | `STATE,` |
| `S,<m>` | m 2..9 | `DONE,S` | `ERR,idx(2~9)`; `ERR,cleaning_active` | ≈125 ms if the selection changes, else 0 | `DONE,S` / `ERR` |
| `V,<m>,<sps>` | signed float, `abs ≤ 8000`; sign = direction | `OK` (accepted) | `ERR,arg`; `ERR,cleaning_active` | ≈125 ms if a relay must switch | `OK` / `ERR` |
| `V,<m>,0` | — | `OK` — stops m **only if m is the selected motor of its group**, otherwise does nothing | same | 0 | `OK` / `ERR` |
| `P,<m>,<steps>,<sps>` | steps signed 32-bit, relative, **no range check**; `0 < sps ≤ 8000` | `OK` (accepted, not finished) | `ERR,arg`; `ERR,cleaning_active` | ≈125 ms if a relay must switch | `OK` / `ERR` |
| `C` | — | `OK,cleaning_started` | `ERR,cleaning_active` | ≈250 ms | `OK` / `ERR` |
| `X` or `STOP` | — | `OK,all_stopped` | — | a few ms | `OK` |
| empty line | | no reply | | | |
| anything else | | | `ERR,bad_cmd` | | `ERR` |

Sources: INO:966-1077, 222-264, 281-297.

**Syringe pump (`Y,`)** — the Mega forwards to the RUNZE SY-01B on Serial1 and returns after the pump's first reply; it never waits for a stroke to finish.

| Command | Arguments | Sent to pump | Success reply | Error replies | Blocks at most |
|---|---|---|---|---|---|
| `Y,DETECT` / `Y,SCAN` | — | `/1Q\r` | `FOUND,proto=ascii,baud=9600,addr=0` | [`ASCIIRX,…`] `ERR,pump_not_found_ascii_9600_addr0` | 500 ms |
| `Y,INIT` | — | `/1ZR\r` | `PUMP,…` | [`ASCIIRX,…`] `ERR,pump_timeout` / `ERR,pump_bad_reply` / `ERR,pump_echo_only` | 2500 ms |
| `Y,Q` | — | `/1?\r` (plunger position) | `PUMP,…,data=<position>` | same three | 1500 ms |
| `Y,ST` | — | `/1Q\r` (status) | `PUMP,…` | same | 1500 ms |
| `Y,STOP` | — | `/1T\r` | `PUMP,…` | same | 1500 ms |
| `Y,SYR,<uL>` | integer 1..100000 | nothing (RAM only) | `OK,syringe_uL=<n>` | `ERR,arg` | 0 |
| `Y,VALVE,<n>` | 1..15 | `/1I<n>R\r` | `PUMP,…` | `ERR,arg`; the three | 1500 ms |
| `Y,SPEED,<n>` | 1..6000 raw units | `/1V<n>R\r` | `PUMP,…` | same | 1500 ms |
| `Y,ASP,<steps>` / `Y,DISP,<steps>` | 1..12000 | `/1P<n>R\r` / `/1D<n>R\r` | `PUMP,…` | same | 1500 ms |
| `Y,ASPUL,<uL>` / `Y,DISPUL,<uL>` | integer µL with `round(uL × 12000 / syringe_uL)` in 1..12000 | `P` / `D` as above | `INFO,pump_steps=<n>` then `PUMP,…` | `ERR,arg`; the three | 1500 ms |
| `Y,POS,<steps>` | 0..12000, absolute | `/1A<n>R\r` | `PUMP,…` | same | 1500 ms |
| `Y,ADDR,<n>` / `Y,BAUD,<n>` | must be 0 / 9600 | nothing | `OK,addr=0` / `OK,pump_baud=9600` | `ERR,fixed_addr_0` / `ERR,fixed_baud_9600` | 0 |
| other `Y,<key>` | | | | `ERR,arg` if the argument is missing or not a plain integer, else `ERR,bad_pump_cmd` | 0 |

`PUMP` line: `PUMP,proto=ascii,ready=<0|1>,error=<0..15>[,data=<text>]`. `ready` is bit 0x20 and `error` the low 4 bits of the pump's status byte. Numeric arguments must be plain integers (optional sign, digits only). Ends with: `PUMP,` / `FOUND,` / `OK,` / `ERR`. Sources: INO:330-478, 851-937.

**Weighing module (`W,`)**

| Command | Arguments | Success reply | Error replies | Blocks | Ends with |
|---|---|---|---|---|---|
| `W,READ` | — | `WEIGHT,<g>,RAW,<raw>,ST,<1 or 0>` | `ERR,timeout(module did not respond)` | one read: ≤ ≈260 ms | `WEIGHT,` / `ERR` |
| `W,STREAM,<hz>` | 0..20 (missing or text = 0 = off) | `OK,stream_hz=<n>` | `ERR,arg` | 0 | `OK` / `ERR` |
| `W,TARE` | — | `OK,tare_raw=<1 decimal>` | `ERR,scale_timeout` | one read | `OK` / `ERR` |
| `W,CAL,<grams>` | float > 0 | `OK,div=<6 dec>,offset=<3 dec>` | `ERR,arg`; `ERR,scale_timeout`; `ERR,no_change` | one read | `OK` / `ERR` |
| `W,DIV,<divisor>` | float ≠ 0 | `OK,div=<6 dec>` | `ERR,arg` | 0 | `OK` / `ERR` |
| `W,OFFSET,<raw>` | float, must be present | `OK,offset=<3 dec>` | `ERR,arg` | 0 | `OK` / `ERR` |
| `W,AVG,<n>` | 1..16 | `OK,avg=<n>` | `ERR,arg` | 0 | `OK` / `ERR` |
| `W,STABLE,<g>` | float > 0 | `OK,stable_g=<3 dec>` | `ERR,arg` | 0 | `OK` / `ERR` |
| `W,BAUD,<baud>` | 1200..115200 | `OK,baud=<n>` | `ERR,arg` | 20 ms, steppers not serviced | `OK` / `ERR` |
| `W,SET,<addr>,<fc>,<reg>,<fmt>` | four integers, not range-checked | a `CFG,…` line — **no `OK`** | `ERR,arg` | 0 | `CFG,` / `ERR` |
| `W,CFG` | — | `CFG,baud=…,addr=…,fc=…,reg=…,fmt=…,div=…,offset=…,avg=…,stable_g=…,stream_hz=…` | — | 0 | `CFG,` |
| `W,RD,<addr>,<fc>,<reg>,<count>` | four integers | `REGS,<HH HH …>` | `ERR,scale_timeout`; `ERR,arg` | ≤ 300 ms | `REGS,` / `ERR` |
| `W,HELP` | — | 16 lines, last `  W,CFG / W,HELP        display settings / This instruction` | — | 0 | that last line |
| `W,LOOP` | — | `LOOP,received="<text>"` then one `HINT,…` | — | ≥ 300 ms | `HINT,` |
| `W,SNIFF[,<sec>]` | default 3, no upper limit | `SNIFF,monitor <n> Second…`, hex rows, `SNIFF,common <n> byte`, then `SNIFF,text=…` and/or one `HINT,…` | — | the whole listening time | `SNIFF,common` (lines follow it) |
| `W,SCAN` | — | `SCAN,Start scanning(about 40 Second)…`, `SCAN,baud=<b>` ×4, `  FOUND,baud=… address=… function code=… scratchpad=… information Starting from=…` per hit, `SCAN,Complete,found <n>`, then 1–4 `HINT,…` lines | — | up to ≈60 s | `SCAN,Complete` (lines follow it) |
| other `W,<key>` | | | `ERR,bad_scale_cmd` | | `ERR` |

Sources: INO:505-849. `W,TARE`, `W,CAL`, `W,DIV`, `W,OFFSET`, `W,AVG`, `W,STABLE`, `W,SET`, `W,BAUD` change RAM only; a reset loses them.

### 2.2 Lines the Mega sends without being asked

| Line | When |
|---|---|
| `READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0` | once after every reset (INO:1110) |
| `WEIGHT,<grams 3 dec>,RAW,<raw 1 dec>,ST,<1 or 0>` | every `1000 / hz` ms while the stream is on (INO:663-665, 1125-1128) |
| `ERR,timeout(module did not respond)` | a stream read failed (INO:651) |
| `ERR,stream_stopped_no_reply` | third consecutive failed stream read; the stream is now off and stays off (INO:653-656) |
| `STATE,…` | a delayed valve close fired; a queued start (after the valve pre-open) fired; a finite `P` move reached its target; each of the three cleaning transitions (INO:162, 197-214, 304-321) |

These can arrive between a command and its own reply. `STATE` is **not** printed by `S`, `V`, `P`, `C`, `X` themselves: a `V` on motors 3..8 starts and stops without any `STATE`, and nothing announces the result of `X`. Send `?` to see.

### 2.3 `STATE`

```
STATE,a=<m|-1>,sa=<st>,b=<m|-1>,sb=<st>,pump=<online|unknown>,proto=ascii,baud=9600,addr=0,syringe_uL=<n>,valves=<bits>,cal_pulses_per_ml=4237.01,max_flow_uL_min=80000,w_stream_hz=<n>,cleaning=<0|1>
```

| Field | Meaning |
|---|---|
| `a`, `b` | selected motor of group A (2..5) / B (6..9); `-1` = none |
| `sa`, `sb` | `wait` (valve pre-open running), `run` (continuous), `move` (finite move), `idle` — the complete set (INO:324-328) |
| `pump` | `online` if the last *awaited* exchange with the syringe pump got a valid reply, else `unknown` |
| `syringe_uL` | syringe volume the Mega uses for µL → steps |
| `valves` | one character per valve, valve 1 first; `1` = open |
| `w_stream_hz` | weight stream rate, 0 = off |
| `cleaning` | 1 while the firmware's cleaning schedule runs |

**[V7 host]** parses it with the regex `([A-Za-z_]+)=([^,]*)` and ignores `proto`, `baud`, `addr`, `cal_pulses_per_ml`, `max_flow_uL_min` (TR:273-313). `WEIGHT` is parsed by splitting on commas: field 1 grams (float), field 3 raw (kept as text), field 5 `1` = stable (TR:246-263). **[INFER]** the Arduino core prints `nan`, `inf` or `ovf` instead of digits for such floats.

### 2.4 Rules a host must follow

1. One command at a time; wait for its ending line before sending the next (**[V7 host]** a lock around send-and-wait, TR:331-350).
2. While waiting, ignore `WEIGHT,` lines and the two asynchronous scale errors `ERR,timeout…` / `ERR,stream_stopped…` — they are not the failure of the pending command (TR:231-241). Keep unsolicited `STATE,` lines as state updates.
3. Any other line starting with `ERR` = the command failed (TR:354-356).
4. `PUMP,…,error=<n>` with n ≠ 0 is a pump fault even though the line does not start with `ERR`. **[V7 host]** does not check it.
5. Timeouts. **[V7 host]** 3.0 s per command, 60.0 s for `W,SCAN`, `W,SNIFF`, `Y,INIT` (YAML:36, 38; TR:74-76). All firmware waits fit inside 3 s except `W,SNIFF,<sec>` and `W,SCAN`; `W,SCAN` needs more than 60 s when nothing answers.
6. There are no reply ids. After a timeout the late reply will be taken for the next command's reply. **[V7 host]** has no resynchronisation.
7. `W,SET` ends with `CFG,`, not `OK` (**[V7 host]** waits for `OK` and therefore always sits out the full 3 s, SC:284).
8. Read by `\n`. **[V7 host]** uses pyserial `readline()` with a 0.1 s timeout and strips each line (TR:198-201); **[INFER]** that can deliver a fragment when the link stalls.
9. An unexpected `READY` means the Mega reset: all RAM settings are gone and all outputs are off. **[V7 host]** only stores the text (TR:222-223).

---

## 3. Procedures

Motor map (YAML:134-152, 176-181): group A = motors 2 `motor 2 · water`, 3, 4, 5; group B = 6, 7, 8 `motor 8 · OUT`, 9 `motor 9 · CuSO₄`. Valve 1 `solenoid valve 1 · water` follows motor 2, valve 2 `solenoid valve 2 · CuSO₄` follows motor 9. One motor per group can be connected at a time; one of each group can run together. Addressing a different motor of a group whose motor is running **silently stops the running one**.

### 3.1 Connect and initialise — [V7 host], MAIN:48-80, TR:141-172, SC:131-167

1. Open the port at 115200 with a 0.1 s read timeout; sleep 2.5 s.
2. Start the reader; send `?`.
3. If `config/scale_calibration.json` exists: `W,DIV,<divisor>` (format `%.9g`), then — only if the saved offset is not 0 — `W,OFFSET,<offset>`. An `OFFSET` failure is only a warning.
4. `W,AVG,1`, `W,STABLE,0.015`, `W,STREAM,10`, `?`.
5. Nothing is sent to the syringe pump. `Y,SYR` is **not** re-applied: after any reset the Mega is back at 5000 µL and the V7 page follows it.

With today's files the traffic is `?`, `W,DIV,287703.031`, `W,OFFSET,-1`, `W,AVG,1`, `W,STABLE,0.015`, `W,STREAM,10`, `?`. Steps 3–4 are repeated only by `POST /api/reconnect`, never automatically.

### 3.2 Continuous run — MO:243-267, INO:1030-1057

1. Checks **[V7 host]**: motor exists; not cleaning; no weight dose running; `0 < flow ≤ 80 mL/min`.
2. `sps = max(1, round(flow_ml_min × 4237.00761337 / 60))`; multiply by −1 for reverse (the V7 page calls +1 `Forward rotation`, −1 `reverse`). 40 → 2825, 10 → 706, 3 → 212, 80 → 5649.
3. Send `V,<m>,<sps>` → `OK`; then `?`.
4. Motors 3..8 turn at once (`run`). Motors 2 / 9: valve opens, state `wait`, the motor starts 1000 ms later and a `STATE` with `run` arrives.
5. Change speed or direction while running: send another `V,<m>,<sps>` — applied immediately, no stop, no new valve wait.
6. Stop: `V,<m>,0` → `OK`; then `?`. Motors 2 / 9: the valve closes 1000 ms later (a `STATE` arrives then).
7. There is no time limit anywhere. The pump runs until told to stop.

### 3.3 Dose by volume (step count) — MO:270-301, INO:1008-1028

1. Checks **[V7 host]**: as 3.2; volume 0.01..1000 mL.
2. `steps = max(1, round(volume_ml × 4558.96147256))`, negative for reverse. `sps` = the fixed dose speed: `dose_flow_ml_min` 40 → **2825**, always positive. A flow passed by the caller is ignored. 10 mL → 45590 steps; 50 mL → 227948 steps.
3. Send `P,<m>,<steps>,<sps>` → `OK` means accepted. Then `?`.
4. The firmware does the rest. Motors 3..8: `move` → `idle`, one `STATE` at the end. Motors 2 / 9: valve opens → `wait` 1000 ms → `STATE` `move` → `STATE` `idle` (valve still open) → 1000 ms → `STATE` with the valve bit 0.
5. `eta_s = abs(steps) / sps` (16.1 s for 10 mL, 80.7 s for 50 mL), plus 2.0 s of valve time for motors 2 / 9. **[INFER]** the real time is longer (acceleration; stepping pauses during the firmware's serial work).
6. **[V7 host]** does not wait for the end. A host that sequences steps must wait for the group's `idle` (and for motors 2 / 9 the closed valve), with a time limit above `eta_s`, and poll `?` as a fallback.
7. The dose ends early, without any message and without an `idle` event for it, on: any later `V` or `P` in that group (same motor or another), `S` for another motor of the group, `X`, or `C`.

### 3.4 Dose by weight (closed loop on the host) — MO:304-516, YAML:110-133

Uses only `V` commands and the `WEIGHT` stream; the firmware does not know a dose is in progress. One at a time for the whole system.

| Constant | Value | Use |
|---|---|---|
| `fast_flow_ml_min` / `approach_flow_ml_min` / `trim_flow_ml_min` | 40 / 10 / 3 → 2825 / 706 / 212 sps | the three speeds |
| `slow_zone_g`, `trim_zone_g` | 2.0, 0.5 | effective `min(2.0, target × 0.25)`, `min(0.5, target × 0.05)` |
| `tolerance_percent` | 0.5 | `lower = target × 0.995`, `upper = target × 1.005` |
| `baseline_samples`, `final_samples` | 5, 7 | medians |
| `settle_s` | 5.0 | wait after stopping |
| `scale_ready_timeout_s` | 4.0 | longest wait for each new reading |
| `minimum_gain_g`, `no_gain_timeout_s` | 0.05, 30.0 | "liquid arrived" / "still rising" |
| `timeout_s` | 300.0 | whole operation, from pump start |
| `density_g_ml` | 1.0 | only for the legacy `volume_ml` argument |
| target range | 0 < target ≤ 1000 g | |

1. Checks: motor exists; not cleaning; `gravimetric_dose.enabled`; target in range; no other weight dose.
2. If the cached stream rate differs from 10: `W,STREAM,10`, `?`.
3. Baseline = median of 5 readings (the first is the cached one). A reading counts when the scale is online, its sequence number is new and grams is finite; polled every 10 ms.
4. `V,<m>,2825` (× direction).
5. For each new reading: `gain = grams − baseline`; `measured` = median of the last 3 gains.
   - `measured ≥ lower` → go to 6;
   - no rise of 0.05 g for 30 s → error `weight has been continuous 30 seconds do not continue to rise; please check the pipeline, direction and weighing position`;
   - `target − measured ≤ trim zone` → `V,<m>,212` (once);
   - else `≤ slow zone` and liquid already detected → `V,<m>,706` (once);
   - 300 s over → `The weighing quantity has exceeded 300 The target has not been reached in seconds and the peristaltic pump has been stopped.`; no new reading for 4 s → `The weight module did not provide a new valid reading and the peristaltic pump has been stopped.`.
6. `V,<m>,0`; wait 5 s; `dispensed` = median of 7 readings − baseline.
7. While `dispensed < lower`: `V,<m>,212` until one reading reaches `lower` (same 30 s rule, text `The weight has been continued during rehydration. Checklist in 30 seconds did not continue to rise and the peristaltic pump was stopped.`), `V,<m>,0`, wait 5 s, 7 readings again.
8. Result fields include `dispensed_g`, `error_g = dispensed − target`, `within_tolerance`, `topups`, `elapsed_s`, `liquid_detected_s`. Out of tolerance is still a normal return, with `warning` = `The final weight exceeds the allowable range; please reduce the finishing flow rate, confirm the vibration isolation of the scale platform and recalibrate`.
9. Cancel: a stop for that motor or a stop-all sets a flag; the dose ends with `Weighing and rationing has been stopped by the user`.
10. Always, also on error: one more `V,<m>,0`.

For motors 2 / 9 every start includes the 1 s valve wait and every stop leaves the valve open 1 s. A reverse direction is accepted but can only end in the 30 s error. Tare, calibration and syringe-pump calls are not blocked during a dose.

### 3.5 Cleaning — MO:200-230, INO:281-322, HW:32-38

The sequence is in the firmware with compiled-in numbers: motor 2 (water) and motor 8 (OUT), 2825 sps, 30 s / 45 s / 45 s. Changing them means editing the YAML, regenerating `hw_config.h` and re-flashing.

1. Checks **[V7 host]**: no weight dose; `?`; not already cleaning (`Automatic cleaning is already being executed`); both groups `idle` (`There is currently a peristaltic pump running; please stop it before starting cleaning.`).
2. Send `C` → `OK,cleaning_started` (≈0.25 s). Then `?` → `a=2,sa=wait,b=8,sb=idle,valves=10,cleaning=1`.
3. Firmware timeline, T0 = water motor start ≈1.25 s after the command; a `STATE` at each point: T0 motor 2 runs · T0+30 s motor 8 starts · T0+75 s motor 2 stops · T0+76 s valve 1 closes · T0+120 s motor 8 stops, `cleaning=0`.
4. During cleaning `S`, `P`, `V`, `C` → `ERR,cleaning_active` — a single motor cannot be stopped. `?`, `Y,…`, `W,…` work.
5. Abort: `X` only.
6. Do not send long-blocking commands (`W,SCAN`, `W,SNIFF`) meanwhile: the firmware advances the schedule only between commands, and the pumps keep running.

`C` itself first stops every motor and closes both valves at once.

### 3.6 Syringe pump — PU:1-123, INO:851-937

| Step | Command | Notes |
|---|---|---|
| Detect | `Y,DETECT` → `FOUND,…` | not found = `ERR,pump_not_found_ascii_9600_addr0` |
| Initialise | `Y,INIT` | the plunger moves (**[DOC]** "The piston moves up to the top and then retreats a little"). Required before any move, else **[DOC]** `error=7` |
| Syringe size | `Y,SYR,<uL>` | **[V7 host]** allows 5000 or 1250 (`syringe capacity only supports [5000, 1250] µL`). RAM only |
| Speed | `Y,SPEED,<raw>` | `raw = max(1, min(6000, round(mL_per_s × 12000 / syringe_mL)))`; maximum 2.5 mL/s (5 mL), 0.625 mL/s (1.25 mL) |
| Head valve | `Y,VALVE,<n>` | **[V7 host]** 1..3 (`This pump only has 3 valve positions`); firmware 1..15 |
| Aspirate / dispense | `Y,ASPUL,<int µL>` / `Y,DISPUL,<int µL>` | **[V7 host]** `0 < µL ≤ syringe`, fractions truncated; steps = `round(µL × 12000 / syringe_µL)` |
| Stop | `Y,STOP` | awaited; `X` also sends the stop but does not wait |
| Status / position | `Y,ST` / `Y,Q` | not used by V7 |

Nobody tracks the plunger position: neither firmware nor backend refuses an aspirate beyond the remaining stroke; the pump answers with an error code (**[DOC]** 2 / 3 = Parameter out of range, 9 = Piston overload, 10 = Valve overload). The reply to a move arrives when the pump has *accepted* it. V7 never asks whether the stroke has finished; a sequencing host has to poll `Y,ST` and read `ready` — how the SY-01B sets that bit during a stroke is not in these files.

### 3.7 Valves

There is no valve command. A valve moves only with its motor: open 1000 ms before motor 2 / 9 starts, closed 1000 ms after it stops; closed immediately by `X`, by `C` at its start, and by a reset (INO:116-163, 253-260; HW:42-45). The host can only read `valves=` in `STATE`. Nothing reads back the real valve position.

### 3.8 Tare and calibration — SC:42-231, INO:794-808

Formula in the Mega: `grams = (average of last avg raw readings − offset) / div`.

1. Tare (empty vessel): `W,TARE` → `OK,tare_raw=…` (offset = current raw). Then `W,CFG`, take `offset` and `div` from the `CFG,` line, write `config/scale_calibration.json` (`{"version": 1, "saved_at": ISO-UTC, "offset", "divisor", "reference_grams"}`, via a `.tmp` file and `os.replace`). Clear the weight history.
2. Calibrate (known weight on the scale): `W,CAL,<grams>` → `OK,div=…,offset=…` (div = (raw − offset) / grams). Then `W,CFG` and save as above with `reference_grams`. **[V7 host]** accepts 0.1..20000 g.
3. Restore after every connect: `W,DIV` then `W,OFFSET` (§3.1).

With `avg` = 1 each of `W,TARE` and `W,CAL` is one single reading, with no stability check. Order for the operator (FE:669): `Empty cup returns to zero → put weight → Select grams → Correction`. Current file: offset −1.0, divisor 287703.03125, reference 200.0 g, saved 2026-08-26.

### 3.9 Stop all — MO:519-526, INO:1059-1069

1. **[V7 host]** set the cancel flag of a running weight dose.
2. Send `X` → `OK,all_stopped`: cleaning cancelled; both steppers stopped without ramp; drivers released; all 8 relays off; no motor selected; both valves closed **immediately**; `/1T\r` written to the syringe pump without waiting for an answer; **weight stream switched off**.
3. **[V7 host]** `W,STREAM,0`, then `?`.
4. To get weights again: `W,STREAM,10` (V7: `scale.restart`). Until then every consumer sees the scale as offline.

`X` is processed when the firmware loop is free: at worst after a pending `Y` reply wait (1.5 s; `Y,INIT` 2.5 s), a failed weight read (0.26 s), or the whole of `W,SNIFF` / `W,SCAN`.

### 3.10 Weight stream upkeep

- After `ERR,stream_stopped_no_reply` or any `X`: `W,STREAM,10`.
- Diagnostics (no button in the V7 page): `W,LOOP`, `W,SNIFF,3`, `W,SCAN`, then `W,SET,<addr>,<fc>,<reg>,<fmt>` — RAM only, not written back to the YAML.

---

## 4. Constants

### 4.1 Compiled into the firmware (`hw_config.h`, generated from `hardware.yaml` by `gen_firmware_config.py`)

Checked by hand: every value below equals what the generator produces from today's YAML.

| Constant | Value | YAML key |
|---|---|---|
| `PUL1` `DIR1` `ENA1` / `PUL2` `DIR2` `ENA2` | 22 23 24 / 25 26 27 | `motors.groups[].driver` |
| `FIRST_MOTOR` `LAST_MOTOR` `GROUP_B_FIRST` | 2, 9, 6 (motor n's relay is pin D n) | `motors.groups[].channels` |
| `MAX_SPS` | 8000.0 | `motors.max_sps` |
| `SETTLE_MS` | 60 | `motors.driver_settle_ms` |
| `MIN_PULSE_US` | 5 | `motors.min_pulse_us` |
| `PULSES_PER_ML` | 4237.007613F (only echoed in `STATE`) | `motors.pulses_per_ml` |
| `PULSES_PER_REV` | 1600L (unused by the sketch) | `motors.pulses_per_rev` |
| `MAX_FLOW_UL_MIN` | 80000L (only echoed, **not enforced**) | `motors.max_flow_ml_min` × 1000 |
| `CLEAN_WATER_MOTOR` / `CLEAN_OUT_MOTOR` | 2 / 8 | `cleaning.water_motor` / `out_motor` |
| `CLEAN_WATER_SPS` / `CLEAN_OUT_SPS` | 2825.0F | `round(cleaning.flow_ml_min × pulses_per_ml / 60)` |
| `CLEAN_WATER_LEAD_MS` / `CLEAN_OVERLAP_MS` / `CLEAN_OUT_TAIL_MS` | 30000 / 45000 / 45000 | `cleaning.*_s` × 1000 |
| `NUM_VALVES`, `VALVE_PIN_LIST` | 2, `{ 10, 11 }` | `valves.channels` |
| `MOTOR_VALVE_LIST` | `{ 1, 0, 0, 0, 0, 0, 0, 2 }` (index = motor − 2) | `valves.motor_links` |
| `VALVE_OPEN_BEFORE_MS` / `VALVE_CLOSE_AFTER_MS` | 1000 / 1000 | `valves.open_before_ms` / `close_after_ms` |
| `PUMP_SERIAL`, `PUMP_BAUD`, `PUMP_ADDR` | Serial1 (D18 TX, D19 RX), 9600, 0 | `syringe_pump.*` |
| `PUMP_FULL_STROKE`, `PUMP_DEFAULT_SYRINGE_UL` | 12000, 5000 | `syringe_pump.*` |
| `SCALE_SERIAL`, `SCALE_DE_PIN` | Serial2 (D16 TX, D17 RX), 36 | `scale.*` |
| `SCALE_STREAM_HZ`, `SCALE_AVG_SAMPLES`, `SCALE_STABLE_G` | 10, 1, 0.015 | `scale.*` |
| `SCALE_BAUD`, `SCALE_ADDR`, `SCALE_FUNC`, `SCALE_REG`, `SCALE_FMT` | 9600, 1, 3, 0, 1 (int32, high word first) | `scale.modbus.*` |

A YAML change to any of these has no effect on the Mega until it is re-flashed. `run.bat:95` rewrites `hw_config.h` on every start of the V7 service, so the header on disk can be newer than the firmware in the board; the board cannot report which it has.

### 4.2 Written in the sketch only (not configurable)

Acceleration for `P` moves `4 × MAX_SPS` = 32000 steps/s² (INO:1101) · driver release delay 5 ms (INO:228) · command buffer 95 characters (INO:1119) · syringe pump timeouts 500 / 1500 / 2500 ms (INO:450, 865-877) · syringe ranges: speed 1..6000, valve 1..15, volume 1..100000 µL (INO:894-929) · one weight read 250 ms (INO:608), `W,RD` 300 ms, scan probe 70 ms · stream 0..20 Hz, average 1..16, history 16, stability over the last 6 readings (at least 3) · 3 failed stream reads switch the stream off (INO:653) · literal texts `baud=9600,addr=0` and `idx(2~9)`.

### 4.3 Host only (the firmware never sees them)

| Constant | Value | Source |
|---|---|---|
| speed coefficient `pulses_per_ml` | 4237.00761337 (`sps = flow × this / 60`) | YAML:90; MO:56-59 |
| dose coefficient `dose_pulses_per_ml` | 4558.96147256 (`steps = mL × this`) | YAML:94; MO:62-65 |
| `max_flow_ml_min` / `default_flow_ml_min` / `dose_flow_ml_min` / `default_dose_ml` | 80 / 40 / 40.0 / 10.0 | YAML:97-103 |
| dose volume limit, weight target limit | 0.01..1000 mL, 0.01..1000 g | MO:272-273, 308-309, 335 |
| `gravimetric_dose.*` | table in §3.4 (`default_target_g` 10.0 and `trigger_volume_ml` 10.0 are not used by the backend) | YAML:110-133 |
| `syringe_options_ul`, `valve_positions` | [5000, 1250], 3 | YAML:194-195 |
| `boot_delay_s`, `command_timeout_s`, `long_command_timeout_s` | 2.5, 3.0, 60.0 | YAML:35-38 |
| serial read timeout, reader join | 0.1 s, 1.5 s | TR:154, 177 |
| weight history, serial log, SSE queue | 6000 samples, 400 lines, 200 events | SC:27; TR:35, 390 |
| scale "online" in `scale.read` | a weight within the last 3.0 s | SC:201 |
| display: `display_decimals`, `chart_points`, `calibration_presets_g`, `default_calibration_g` | 3, 300, [10, 50, 100, 200], 100 | YAML:208-212 |
| calibration file | `config/scale_calibration.json` (env `SCALE_CALIBRATION_FILE` overrides) | SC:30-45 |
| config file | `config/hardware.yaml` (env `LAB_CONFIG` overrides, read at import) | CFG:15-16 |

The two coefficients differ on purpose (YAML:84-94, 105-107): 2825 steps/s is called 40 mL/min, while a 50 mL dose is 227948 steps and takes 80.7 s. Do not unify them during the merge.

---

## 5. Safety behaviour

**Enforced by the firmware [FACT]**
- Motor 2..9; step rate at most 8000 per second.
- One motor per driver group; relays switch only with the driver released, 60 ms settle on each side.
- Motors 2 and 9 cannot turn with their valve closed; a valve cannot be operated alone.
- During cleaning `S`, `P`, `V`, `C` are refused.
- Syringe pump argument ranges; scale stream stops itself after three failed reads.
- `X`: everything listed in §3.9.

**Added by the V7 backend [V7 host]**
- Flow at most 80 mL/min; dose 0.01..1000 mL; weight target at most 1000 g.
- Refuses `select` / `run` / `stop` / `dose` / `dose_by_weight` during cleaning (`Automatic cleaning is being executed; please wait for completion or click ”Stop All”`) and `clean` / `select` / `run` / `dose` during a weight dose (`motor N is weighing and quantifying; please stop or wait for it to complete`).
- Weight dose limits: 300 s total, 30 s without gain, 4 s without a reading; always a final `V,<m>,0`.
- The `danger` flag on `motors.stop_all`, `pump.init`, `pump.stop` is only a label; nothing checks it. The V7 page asks for no confirmation anywhere.

**Not provided by anything in V7**
- No watchdog or heartbeat. If the PC, the program or the USB link stops, the Mega carries on: a continuous run never ends, a dose and a cleaning run finish on their own.
- The V7 service sends nothing on exit (MAIN:80, TR:174-188). After a lost link every send fails at once, so neither the weight dose's final stop nor stop-all reaches the Mega (TR:207-211, 323-324).
- No limit on `P` steps or on run time in the firmware.
- No sensor on the liquid side: no flow, level, overflow, leak or valve feedback, no emergency-stop input. The weight is reported, never acted on, by the firmware.
- `X` is a software stop that waits for the firmware loop; it does not confirm that the syringe pump stopped.
- After any movement the driver stays enabled and the relay closed until `X` or a reset.
- **[INFER]** Reopening the port resets the Mega, which stops pumps and closes valves — but not a syringe stroke in progress. Whether this board auto-resets was not tested.
- Whether a hardware cut-off exists for the 24 V of the pumps and valves is not mentioned in the code or the wiring pages.

---

## 6. Reusing the V7 backend modules inside another program

### 6.1 Import graph

```
backend/__init__.py            no imports; __version__ = "5.0.0"
backend/registry.py            stdlib only (dataclasses, typing)
backend/simulator.py           stdlib only (math, queue, random, threading, time, typing)
backend/config.py              stdlib (os, threading, pathlib, typing) + PyYAML: import yaml   (line 13, unconditional)
backend/transport.py           stdlib (queue, re, threading, time, dataclasses, typing)
                               + .config.get_config  + .simulator.SimulatedMega
                               + pyserial inside try/except: serial, serial.tools.list_ports
backend/functions/__init__.py  from . import motors, pump, scale
backend/functions/motors.py    stdlib (math, statistics, threading, time, collections, typing) + ..registry
backend/functions/pump.py      stdlib (typing) + ..registry
backend/functions/scale.py     stdlib (io, json, math, os, threading, time, collections, datetime, pathlib, typing) + ..registry
backend/main.py                stdlib (asyncio, json, logging, queue, time, contextlib, pathlib, typing)
                               + fastapi (FastAPI, HTTPException, Request, CORSMiddleware,
                                 FileResponse, JSONResponse, StreamingResponse) + uvicorn (inside run())
                               + .registry, .config, .functions, .transport
tools/gen_firmware_config.py   backend.config.load_config (→ PyYAML)
```

The function modules do not import `transport`, `config` or each other. They receive everything through `registry.Context(link, config)`.

### 6.2 What happens at import time

| Module | At import |
|---|---|
| `registry` | creates an empty registry dict |
| `functions.motors` / `pump` / `scale` | register 8 + 9 + 9 functions (a second import under another package name raises `Duplicate registration of function name End by itself in`); `motors` creates two locks; `scale` creates the 6000-sample history and computes the default calibration path from its own file location (`parents[2] / "config" / "scale_calibration.json"`). No file or port I/O |
| `config` | imports `yaml`; fixes `CONFIG_PATH` from env `LAB_CONFIG` or `<package parent>/config/hardware.yaml`. The file is read and validated on the first `get_config()` |
| `simulator` | nothing |
| `transport` | tries to import pyserial (failure tolerated). No port is opened. `get_link()` builds the single `Link`, whose constructor calls `get_config()` |
| `main` | calls `get_config()` (line 87) — a missing or invalid YAML stops the import; builds the FastAPI app. The port is opened later, in the lifespan |

### 6.3 Threads and asyncio

Only `main.py` uses asyncio (`async def` for `/fn/{name}` and `/api/stream`, `asyncio.to_thread`). Everything else is plain blocking code:
- `Link.start()` blocks 2.5 s plus one command; it starts one daemon thread `serial-reader` (and `sim` in simulator mode).
- `Link.send()` blocks the caller until the reply or the timeout, under one lock.
- Every function is a normal call `fn.invoke(ctx, payload)`. `motors.dose_by_weight` blocks its caller for the whole dose (minutes); all others return within a few command round trips.
- Events for a UI come from `Link.subscribe()` → a `queue.Queue` (max 200) of `{"type": "state" | "weight" | "log", "data": …}`; weights can also be taken with `link.weight_hooks.append(callable)` (called on the reader thread).

So a host built on the standard library's threaded HTTP server can call these modules directly, one thread per request; no event loop is required.

What a host must do itself (it is in `main.py` today): create the link and call `start()`; after each successful start call `scale.restore_calibration(link)` and send `W,AVG`, `W,STABLE`, `W,STREAM`, `?`; append `scale.record` to `link.weight_hooks`; map `registry.FunctionError` to "bad request" and `transport.LinkError` to "device error"; call `link.stop()` at exit.

What `ctx.link` must offer if `transport.py` is replaced: `send(cmd) -> list[str]` (raising on `ERR` lines and on timeout), `snapshot() -> dict` with `cleaning`, `motors`, `pump`, `scale` as in the state model, `refresh_state() -> dict`, and a `weight_hooks` list.

### 6.4 Module by module

| Module | Needs FastAPI | uvicorn | PyYAML | httpx | pyserial | Usable unchanged with the standard library only? |
|---|---|---|---|---|---|---|
| `registry.py` | no | no | no | no | no | **yes** |
| `simulator.py` | no | no | no | no | no | **yes** |
| `functions/__init__.py`, `motors.py`, `pump.py`, `scale.py` | no | no | no | no | no | **yes**, kept as a sub-package beside `registry.py` (they use `from ..registry import …`) |
| `transport.py` | no | no | only through `.config` | no | for real hardware (`Serial(port, baud, timeout=0.1)`, `readline`, `write`, `flush`, `close`, `list_ports.comports()`) | the file itself yes; it needs a sibling module `config` with `get_config()`, and pyserial to reach a real COM port. Simulator mode works without pyserial |
| `config.py` | no | no | **yes** — one call, `yaml.safe_load` (line 227) | no | no | **no**. The validation (lines 35-219) is plain Python |
| `main.py` | **yes** — the whole file | **yes** (`run()`) | through `config` | no | through `transport` | **no**; about 235 lines of routing with no device logic |
| `tools/gen_firmware_config.py` | no | no | through `backend.config` | no | no | only where `backend.config` imports |
| `tools/selftest.py` | yes | yes | yes | yes (the only user of httpx, per the backend report; not re-read here) | no | no |

Facts that bear on "standard library plus possibly pyserial":
- `hardware.yaml` needs a real YAML parser as it stands: comments, nested block mappings, lists of flow mappings (`- { id: 2, pin: 2, label: motor 2 · water, fluid: water }`), flow lists, unquoted non-ASCII text. `config.py` does nothing with PyYAML except load this file into a dict.
- **[INFER]** PyYAML and pyserial are pure-Python packages that also work when copied as folders (PyYAML's C accelerator is optional and `safe_load` does not use it); pyserial on Windows uses `ctypes`. Whether the EC program's bundled Python has `ctypes` could not be checked (`runtime` is off-limits; the EC sources `pc/app.py`, `pc/netcheck.py`, `installer/*.py` do not import it). Nothing was installed or copied.
- `transport.py` behaviours that come along if it is reused unchanged: port `auto` picks the first port whose description contains `arduino`, `mega`, `ch340`, `usb-serial` or `wch`, else simply the first port, with no identity check (the EC rig's Nano is a CH340 device); no automatic reconnect; nothing sent to the Mega on `stop()`; `W,SET` always takes 3 s; reads `link.command_timeout_s` and `link.long_command_timeout_s` without defaults.
- `simulator.py` gives a hardware-free link (`link.simulate: true`, port name `simulator`, optional `link.simulation_speedup`). It differs from the firmware: `Y,SYR` answers with a `PUMP,` line; `W,CAL` does not change the divisor and prints no offset; no `ERR,arg` range checks on `V` / `P`; the syringe pump and the scale never fail; `Y,ADDR`, `Y,BAUD`, `W,HELP`, `W,RD`, `W,BAUD` are missing; finite `P` moves add no weight; relay and reply delays are not modelled (SIM:272-451).
- All backend files start with `from __future__ import annotations`; the only version-sensitive call is `asyncio.to_thread` in `main.py`. Nothing conflicts with Python 3.12.
- Paths tied to the V7 folder layout: `config.py:15-16`, `scale.py:30-32`, `main.py:34-36` (`frontend/`, `docs/`), `gen_firmware_config.py:22-27`.
- The page `frontend/index.html` needs from a server exactly: `GET /api/config`, `GET /fn`, `GET /api/state`, `GET /api/stream` (SSE), `POST /api/reconnect`, `GET /docs`, and `POST /fn/<name>` for 22 functions (list in `v7-frontend.md` §5).

---

## 7. Not determinable from the files

- Which firmware build is in the Mega and with which AccelStepper version.
- Whether the Mega's USB cable goes to the Windows PC or elsewhere, and its COM port name.
- Whether this Mega resets when the port is opened.
- **[INFER]** items that need a bench test: the speed ceiling left behind by a `P` move (a later faster `V` in the same group may be clamped to the dose speed while still answering `OK`); how much slower than commanded the steppers really turn with the 10 Hz stream on; the highest reliable step rate.
- SY-01B behaviour: when it answers relative to the stroke, what `ready` shows during a stroke, the full error list.
- Whether the saved scale calibration is still valid; the transmitter's model and register meaning.
- TB6600 (YAML, `hw_config.h`) or DM542J (sketch comment, wiring page) on the rig.
- What liquids motors 3–7 carry.
- Whether a hardware cut-off for the liquid-handling 24 V exists.
