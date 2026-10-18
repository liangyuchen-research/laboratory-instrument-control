# V7 backend (independent laboratoryv7) — code map for the merge

Scope: `C:/Users/PlasmaLab/Desktop/independent laboratoryv7` — `README.md`, `requirements.txt`, `run.bat`, `selftest.bat`,
`config/hardware.yaml`, `config/scale_calibration.json`, `backend/*.py`, `backend/functions/*.py`,
`tools/selftest.py`, `tools/probe.py`, `tools/url.py`. All read completely.

Additionally read (read-only, to cross-check what the backend expects against what the firmware really replies):
`tools/gen_firmware_config.py`, `tools/__init__.py` (empty), `.gitignore`, `firmware/Arduino/hw_config.h`,
`firmware/Arduino/Arduino.ino` (lines 1-747 and 780-1133; lines 748-779 = `scalePrintCfg`/`scalePrintHelp`, seen through a grep only).

Nothing was executed, no serial port opened, no network connection made, nothing written inside the three folders.

**Disclosure of one rule slip.** A `Glob` with pattern `*` on the V7 root recursed unexpectedly and returned 100 file
*paths* inside `.venv` (names of third-party packages `annotated_types`, `anyio`, `certifi`). No file in `.venv` was
opened. The only fact visible from those names: the `.pyc` files are tagged `cpython-313`, i.e. the existing `.venv`
was created by a Python 3.13. I did not list `.venv` again.

Paths below are relative to the V7 root. `file:line` refers to the files as they are on disk on 2026-10-06.

---

## 0. Things that surprised me (read these first)

1. **Everything in the code calls itself "v5".** Folder is `independent laboratoryv7`, but `README.md:1` "# independent laboratory v5",
   `backend/__init__.py:11` `__version__ = "5.0.0"`, `backend/main.py:83` `FastAPI(title="independent laboratory v5", version="5.0.0", …)`,
   `config/hardware.yaml:14` `name: independent laboratory v5`, `run.bat:4` `title Lab v5 - Control Service`. The strings "v7"/"v6"
   do not occur in README, bat files, backend, tools, config or firmware. What distinguishes "v7" from "v5" cannot be
   determined from the code.
2. **There is no authentication of any kind** in the V7 backend (no password, no session, no token) and CORS is `*`
   (`hardware.yaml:23`, `main.py:85-89`). The operator password has to come entirely from the HMI design.
3. **The backend never waits for a pump action to finish**, with one exception (`motors.dose_by_weight`, which blocks
   the HTTP request for the whole dose). `motors.dose`, `motors.clean`, `pump.aspirate/dispense` return as soon as the
   Arduino acknowledges the command. Completion is only visible as a change in the state (`motors.<group>.state` back
   to `"idle"`, `cleaning` back to `false`). A workflow engine has to poll/wait on state itself.
4. **`motors.stop_all` also switches the weight stream off** (`X` sets `wStreamHz = 0` in firmware and the backend
   additionally sends `W,STREAM,0`). After every "stop all" the scale shows offline until `scale.restart` is called.
5. **Firmware errors come back as HTTP 503, not 400.** Any reply line starting with `ERR` raises `LinkError`
   (`transport.py:354-356`) which `main.py:159-160` maps to 503 with `detail` = the raw firmware line
   (e.g. `ERR,cleaning_active`, `ERR,pump_timeout`, `ERR,scale_timeout`).
6. **`hardware.yaml` says the drivers are TB6600** (`hardware.yaml:60-61`, 6 DIP switches), **README and
   `tools/selftest.py:170-173` still say DM542J** (8 switches). By reading, the self-test check
   "DM542J dialing corresponding 8 subdivision / 1600 pulse per revolution" compares `driver_switches` with
   `["ON","OFF","ON","OFF","OFF","OFF","ON","ON"]` and must fail against the current YAML
   (`["OFF","ON","OFF","ON","ON","OFF"]`). I did not run it.
7. **Port auto-detection keys on "ch340"** (`transport.py:135`). The EC rig's Arduino Nano is a CH340 device (COM9
   when it was flashed on a PC, according to the EC project's `historical-development-notes.md`). With `link.port: auto`, if a Nano is plugged into this PC the backend may open it
   instead of the Mega; and if nothing matches it opens the *first* COM port it finds, whatever it is.
8. **No automatic reconnect.** If the USB cable drops, the reader thread ends and the state shows
   `connected: false`; only `POST /api/reconnect` brings it back.
9. `scale.modbus` always takes the full `command_timeout_s` (3 s): firmware answers `W,SET` with a `CFG,…` line but
   the backend waits for `OK`/`ERR` (details in §4).

---

## (a) Process model

### Start-up chain
`run.bat` (ASCII, CRLF per README:229):
1. Finds a Python ≥ 3.9: `python-path.txt` → `py -3` → `py` → `python` → `python3` → `%LOCALAPPDATA%\Programs\Python\Python3*`
   → `%ProgramFiles%\Python3*` → `%ProgramFiles(x86)%\Python3*` → `C:\Python3*` → Anaconda/Miniconda locations
   (`run.bat:18-49`). Uses `tools\probe.py` (prints `sys.executable`; with `--require` exits 1 if `< (3, 9)`, `probe.py:15-26`).
2. Creates `.venv` on first run, `pip install --upgrade pip`, `pip install -r requirements.txt` (needs internet)
   (`run.bat:59-79`); afterwards checks `import fastapi,serial,yaml` and reinstalls if missing (`run.bat:83-88`).
3. **Regenerates `firmware/Arduino/hw_config.h` from `config/hardware.yaml` on every launch**
   (`run.bat:95`, `"%VENV%" -X utf8 tools\gen_firmware_config.py`). The header can therefore silently get ahead of what
   is flashed on the Mega.
4. Gets the console URL from `tools\url.py` (reads `server.host`/`server.port`; `0.0.0.0`/`::` is shown as `127.0.0.1`;
   falls back to `http://127.0.0.1:8000`, `url.py:10-18`).
5. Opens the browser after 4 s (`run.bat:113`) and runs `"%VENV%" -X utf8 -m backend.main` in the foreground
   (`run.bat:114`). Stop = Ctrl+C.

`backend/main.py:226-230` `run()`: `uvicorn.run(app, host=cfg.get("host","127.0.0.1"), port=int(cfg.get("port",8000)), log_level="info")`.
One process, one uvicorn worker, no reload.

### Import-time side effects
- `main.py:87` calls `get_config()` at import → `hardware.yaml` is loaded and validated when the module is imported;
  a `ConfigError` aborts start-up.
- `main.py:28` `from .functions import MODULES` → importing `backend.functions` registers all 26 functions
  (`functions/__init__.py:6`).
- Config path: `backend/config.py:16` `CONFIG_PATH = Path(os.environ.get("LAB_CONFIG", ROOT / "config" / "hardware.yaml"))`.
  Config is cached in a module global (`config.py:234-239`); changes need a service restart.

### Lifespan (`main.py:48-80`)
On start:
1. `link = get_link()` (singleton, `transport.py:414-419`), `link.start()`.
2. `scale_fn.restore_calibration(link)` (inner `try/except Exception` → only a warning).
3. `_sync_scale_runtime(link, cfg)` (`main.py:39-45`) sends `W,AVG,<average_samples>`, `W,STABLE,<stable_threshold_g:g>`,
   `W,STREAM,<stream_hz>`, then `?`.
4. `except LinkError` → `log.warning("not connected yet Arduino:%s(The interface can still be opened)")`; **the service still starts** (README:258).
5. `link.weight_hooks.append(scale_fn.record)`; `app.state.link`, `app.state.config` set.
On shutdown: `link.stop()` (`main.py:80`). **(cross-check addition) No `X` and no `V,<m>,0` is sent on shutdown** — `Link.stop()` (`transport.py:174-188`) only stops the reader thread and closes the port. A pump started with `motors.run`, a running `P` dose, a cleaning run and the weight stream all continue in the Mega after Ctrl+C; the firmware has no watchdog. They end only when the port is opened again (if the board auto-resets on open) or power is removed.

Resulting serial traffic on a normal start with the current files:
`?` → `W,DIV,287703.031` → `W,OFFSET,-1` → `W,AVG,1` → `W,STABLE,0.015` → `W,STREAM,10` → `?`.

Note: `link.start()` is called synchronously inside the async lifespan and contains `time.sleep(boot_delay_s)` (2.5 s).

### Threads
| Thread | Created at | Does |
|---|---|---|
| uvicorn main thread (asyncio loop) | `uvicorn.run` | HTTP, the two `async def` endpoints |
| `serial-reader` (daemon) | `transport.py:165-167` | `Link._read_loop`: reads lines, updates state, broadcasts events |
| `sim` (daemon) | `simulator.py:61` | simulator tick every 10 ms (only when `link.simulate`) |
| asyncio default executor threads | `asyncio.to_thread` at `main.py:156` and `main.py:187` | every `POST /fn/{name}` handler; every SSE client's blocking `queue.get(True, 1.0)` |
| Starlette/anyio worker threads | FastAPI, for plain `def` endpoints | `/api/config`, `/fn`, `/api/state`, `/api/log`, `/api/reconnect`, `/`, `/docs…` |

Consequence: each open SSE connection occupies one default-executor thread almost permanently, and every function
call takes another one (a `motors.dose_by_weight` call holds one for minutes). The default executor size is Python's
default (not configured in the code).

### Who owns the COM port
Only `backend/transport.py` `class Link` (`transport.py:79`). One instance per process (`get_link()`).
- Open: `serial.Serial(self.port_name, int(link.baud), timeout=0.1)` then `time.sleep(link.boot_delay_s)`
  (`transport.py:152-155`). Opening the port resets the Mega (YAML comment `hardware.yaml:34`); no explicit DTR handling.
- Read: only the `serial-reader` thread, `self._ser.readline()` → `decode("utf-8", errors="replace").strip()`
  (`transport.py:193-201`). **Lines are stripped**, so firmware lines that start with spaces lose them.
  (cross-check addition) The port is opened with `timeout=0.1` and read with pyserial's `readline()`. **[INFER, pyserial
  behaviour, not in this repo]** `readline()` returns whatever it has when 0.1 s pass without a byte, so a line whose
  bytes are separated by a pause longer than 0.1 s reaches `_handle_line` as two fragments. The firmware prints normal
  lines in one go, so in practice this concerns the `W,SNIFF` hex rows and a stalled USB link; the code has no
  re-assembly. In simulator mode lines are **not** stripped (`transport.py:194-195`).
- Write: `Link.send()` under `self._tx_lock` (`transport.py:331-350`): `(command + "\n").encode("utf-8")`,
  `write` + `flush`. The lock is held until the reply arrives or the timeout expires → **strictly one command at a time**.
- Correlation: a single `self._pending` slot (`Pending`, `transport.py:42-49`). The reader appends every line that is
  not filtered to `pending.lines` and sets `done` on the first line that `startswith` one of the terminators
  (`transport.py:231-244`).

### How the port is chosen (`transport.py:126-139`)
1. `link.port` from YAML; anything other than `auto` (case-insensitive) is used verbatim (e.g. `COM5`, `/dev/ttyACM0`).
2. `auto`: `serial.tools.list_ports.comports()`; first port whose
   `f"{p.description} {p.manufacturer or ''}".lower()` contains any of
   `("arduino", "mega", "ch340", "usb-serial", "wch")` → `p.device`.
3. Otherwise the **first port in the list**.
4. No ports → `LinkError("No serial port found,please hardware.yaml of link.port Fill in directly COM serial number")`.
No handshake verifies that the device is the Mega: after opening, `send("?")` is tried and any failure is swallowed
(`transport.py:169-172`). A wrong device just shows `connected: true` with commands timing out.

### Reconnect / failure behaviour
- Read exception → `last_error = f"read interrupt:{exc}"`, `connected = False`, state pushed, **reader thread exits**
  (`transport.py:207-211`). The serial handle is not closed there.
- Write exception → `LinkError(f"failed to send:{exc}")`; `connected` is **not** changed (`transport.py:343-346`).
- There is no retry loop or watchdog. Recovery = `POST /api/reconnect` (`main.py:125-138`): `link.stop()` (joins reader
  ≤ 1.5 s, closes port), `link.start()`, `restore_calibration`, `_sync_scale_runtime`.
- `Link.start()` is a no-op when `connected` is already true (`transport.py:142-143`).
- If pyserial is missing: import guarded (`transport.py:27-32`); real mode raises
  `LinkError("is not installed pyserial,Please execute first run.bat Installation dependency kit")`.

### `Link.send(command, terminators=None, timeout=None)` (`transport.py:318-357`)
- empty command → `[]`; not connected → `LinkError("Not connected yet Arduino")`.
- timeout default: `link.long_command_timeout_s` (60.0) if the command starts with `W,SCAN`, `W,SNIFF` or `Y,INIT`
  (`_is_slow`, `transport.py:74-76`), else `link.command_timeout_s` (3.0). Both keys are read with `[]` and are **not**
  covered by config validation.
- After the wait: `if not ok and not pending.lines: raise LinkError(f"command timeout({command})")`. **If the terminator never
  came but at least one other line was collected, the call returns those lines as success.**
- Any collected line starting with `ERR` → `LinkError(line)`.
- Returns the list of collected lines (including the terminator line).

Default terminators (`_default_terminators`, `transport.py:52-71`; matching is `line.startswith(t)`):

| Command | Terminators |
|---|---|
| `?` | `("STATE,",)` |
| `W,READ` | `("WEIGHT,", "ERR")` |
| `W,CFG` | `("CFG,",)` |
| `W,HELP` | `("  W,CFG",)` (two leading spaces — can never match on real hardware because lines are stripped; no function sends it) |
| `W,SCAN` | `("SCAN,Complete", "SCAN,Complete")` (the two entries are identical) |
| `W,SNIFF` | `("SNIFF,common", "SNIFF,common")` (identical) |
| `W,LOOP` | `("HINT,",)` |
| `W,RD` | `("REGS,", "ERR")` |
| any other `W,…` | `("OK", "ERR")` |
| `Y,…` | `("PUMP,", "FOUND,", "OK,", "ERR")` |
| `S,…` | `("DONE,S", "ERR")` |
| everything else (`V`, `P`, `C`, `X`) | `("OK", "ERR", "DONE")` |

Line filtering while a command is pending (`transport.py:231-244`):
- `WEIGHT,` lines are ignored unless `"WEIGHT,"` is one of the terminators.
- `ERR,timeout…` and `ERR,stream_stopped…` (asynchronous scale-stream errors) are ignored unless the pending command
  starts with `W,READ`.
- Everything else — including unsolicited `STATE,` lines — is appended to the pending command's lines.

### Line handling by the reader (`transport.py:216-229`)
| Line prefix | Effect |
|---|---|
| `WEIGHT,` | `_apply_weight` (state + hooks + `weight` event). Never written to the log. |
| `STATE,` | `_apply_state` + `state` event |
| `READY` | `state["firmware"] = line` (no event) |
| `ERR,stream_stopped` / `ERR,timeout` | `state["scale"]["online"] = False` + `state` event |
| any non-WEIGHT line | appended to log as `rx` + `log` event |

---

## (b) HTTP endpoints and the event stream

Server: `http://127.0.0.1:8000` (`hardware.yaml:21-22`). No authentication. CORS: `allow_origins = server.cors_origins`
(`["*"]`), all methods, all headers. FastAPI error bodies are `{"detail": "<text>"}`.

| # | Method + path | Source | Request | Response (200) | Errors |
|---|---|---|---|---|---|
| 1 | `GET /api/config` | `main.py:99-107` | – | `{"config": <whole hardware.yaml as JSON>, "link": {"connected": bool, "simulated": bool, "port": str, "error": str\|null}, "calibration": {"attempted": bool, "file_exists": bool, "loaded": bool, "offset_loaded": bool, "message": str, "file": str}}` | – |
| 2 | `GET /fn` | `main.py:110-112` | – | `{"functions": [ {"name","summary","group","danger","params":[{"name","type","required","label","unit", optional "default","minimum","maximum","choices"}]} … ]}` sorted by name | – |
| 3 | `GET /api/state` | `main.py:115-117` | – | state snapshot (see (e)) | – |
| 4 | `GET /api/log?limit=200` | `main.py:120-122` | query `limit:int` (default 200) | `{"log": [{"t": epoch_float, "kind": "tx"\|"rx", "text": str}, …]}` = `link.log[-limit:]`; buffer holds max `LOG_MAX = 400` entries (`transport.py:35`). `limit=0` returns everything. | 422 if `limit` is not an int |
| 5 | `POST /api/reconnect` | `main.py:125-138` | no body | `{"connected": bool, "port": str, "calibration_restored": bool}` | 503 `detail=str(LinkError)`; 500 `detail="Reconnected, but calibration failed to load:{exc}"` |
| 6 | `POST /fn/{name}` | `main.py:144-168` | JSON object of parameters. A missing/invalid JSON body is treated as `{}`. | `{"ok": true, "function": "<name>", "result": <function result>, "elapsed_ms": float}` | 400 `"The request content must be JSON object"` (body is JSON but not an object); 400 = `FunctionError` text; 503 = `LinkError` text; 500 = `"{ExceptionType}: {message}"` |
| 7 | `GET /api/stream` | `main.py:174-197` | – | `text/event-stream` (see below) | – |
| 8 | `GET /` | `main.py:207-209` | – | `frontend/index.html`, header `Cache-Control: no-store` | – |
| 9 | `GET /docs`, `GET /docs/` | `main.py:212-215` | – | `docs/index.html` | – |
| 10 | `GET /docs/{name}` | `main.py:218-223` | – | file directly inside `docs/` | 404 `"This document cannot be found"` (also for path traversal: `target.parent != DOCS.resolve()`) |
| 11 | `GET /api/docs` | `main.py:83-84` (`docs_url="/api/docs"`) | – | Swagger UI generated by FastAPI | – |
| 12 | `GET /openapi.json` | FastAPI default (not overridden) | – | OpenAPI schema | – |

`redoc_url=None`. No other routes, no WebSocket, no static directory mount.

### `POST /fn/{name}` rules (registry, `registry.py:87-103` and `44-64`)
- Unknown function → 400 `does not have this function:{name}`.
- Unknown key in the body → 400 `Unrecognized parameter:{key}`.
- For each declared parameter: present and not `null` → coerced; else if the declared default is not `None` → default;
  else if required → 400 `Missing necessary parameters:{name}`; else `None`.
- `number`/`integer`: `float(raw)` (strings like `"3"` and booleans are accepted); failure → `parameter {name} Must be a number`;
  `integer` must be integral → `parameter {name} Must be an integer`; `minimum`/`maximum` → `parameter {name} not less than {min}` /
  `parameter {name} cannot be greater than {max}`.
- `boolean`: true iff raw is one of `True, 1, "1", "true", "True", "on"` (no boolean parameter is currently declared).
- other types (`string`, `enum`): passed through.
- `choices` → `parameter {name} can only be {choices} one of them`.
- The handler runs in a worker thread (`asyncio.to_thread`), so long calls do not block the event loop.

### Event stream `GET /api/stream`
- Response headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`.
- Each event is one line `data: <json>\n\n` (`_sse`, `main.py:200-201`; `ensure_ascii=False`, `default=str`).
  No `event:` or `id:` fields.
- First event immediately: `{"type":"state","data":<snapshot>}`.
- Then, from the subscriber queue (max 200 queued events per client; when full, new events are **dropped silently**,
  `transport.py:389-407`):
  - `{"type":"state","data":<snapshot>}` — on every `STATE,` line from the Arduino, on connect/disconnect, on scale
    stream errors.
  - `{"type":"weight","data":{"grams","raw","stable","online","stream_hz","updated","sequence"}}` — once per `WEIGHT,`
    line (10 Hz).
  - `{"type":"log","data":{"t","kind","text"}}` — once per sent command (`tx`) and per received non-weight line (`rx`).
- Keep-alive comment `: ping\n\n` when the queue has been empty and more than 15 s passed since the last ping.

---

## (c) Function registry — all 26 functions

Declared with `@function(name, summary, group, params=[…], danger=False)` (`registry.py:109-117`). `danger` is only a
flag in the manifest; the backend does not enforce anything with it. `Param` fields: `name, type, required=True,
default, minimum, maximum, choices, label, unit` (`registry.py:23-33`).

Shared parameter objects (`motors.py:162-163`):
`_MOTOR_PARAM = Param("motor", "integer", label="Motor number")` (no min/max; checked against the YAML channel list →
`No number {motor} motor`), `_FLOW_PARAM = Param("flow_ml_min", "number", label="flow", unit="mL/min", minimum=0.01)`.

### motors.* (`backend/functions/motors.py`)

| Name | Summary (verbatim) | Parameters | danger |
|---|---|---|---|
| `motors.list` | Lists all peristaltic pump channels and currently selected motors | – | no |
| `motors.clean` | Automatic cleaning: advanced water 30 seconds, simultaneous water supply and drainage 45 seconds, final emptying 45 Second | – | no |
| `motors.select` | switching relay,Select which motor to drive in a certain group | `motor` integer, required | no |
| `motors.run` | operates continuously at the specified flow rate; the linkage channel will automatically open the valve before starting | `motor`; `flow_ml_min` number ≥ 0.01, required (label flow, mL/min); `direction` integer, default `1`, choices `[1, -1]` (label direction) | no |
| `motors.stop` | Stop the specified motor; the linkage valve will automatically close after a delay | `motor` | no |
| `motors.dose` | quantitatively delivers the specified volume; the linkage valve will automatically open and close in sequence | `motor`; `volume_ml` number 0.01…1000, required (label volume, mL); `flow_ml_min` number ≥ 0.01, optional (label "flow (if you don’t fill it in, use the configuration file)") — **accepted but ignored**; `direction` default 1, choices `[1,-1]` | no |
| `motors.dose_by_weight` | Take the moment of pressing as the zero point, and continue to rotate until the specified weight is added. | `motor`; `target_g` number 0.01…1000, optional (label target weight, g); `volume_ml` number 0.01…1000, optional (label target volume; legacy: target = volume × `density_g_ml`); `flow_ml_min` number ≥ 0.01, optional (label Old version fast transfer traffic) — **ignored**; `direction` default 1, choices `[1,-1]` | no |
| `motors.stop_all` | Stop the motor, syringe pump, weight reading and close the solenoid valve | – | **yes** |

Additional run-time checks (all → HTTP 400):
- `_check_flow` (`motors.py:79-86`): `0 < ml_min <= motors.max_flow_ml_min` else `flow must be between 0 and {top} mL/min between`;
  computed sps > `motors.max_sps` → `{ml_min} mL/min after conversion is {sps} step/Second,exceeds the drive upper limit`.
- `_reject_during_cleaning` (cached state): `Automatic cleaning is being executed; please wait for completion or click ”Stop All”` — applies to `select`, `run`,
  `stop`, `dose`, `dose_by_weight`.
- `_reject_during_weight_dose`: `motor {motor} is weighing and quantifying; please stop or wait for it to complete` — applies to `clean`, `select`, `run`,
  `dose` (for **every** motor, both groups).
- `motors.clean`: `Automatic cleaning is already being executed`; `There is currently a peristaltic pump running; please stop it before starting cleaning.` (any group state other than `idle`).
- `motors.dose_by_weight`: `hardware.yaml Weighing and quantification has not been enabled yet`; `Weighing and quantitative requirements target_g (Old versions can also be transferred volume_ml) `;
  `The target weight must be between 0 and 1000 g between`; `motor {active} is using the weight module for quantification` (only one weight dose at a time, system-wide).

### pump.* (`backend/functions/pump.py`) — syringe pump RUNZE SY-01B

| Name | Summary | Parameters | danger |
|---|---|---|---|
| `pump.status` | Syringe pump current connection status and conversion parameters | – | no |
| `pump.detect` | Detect whether the syringe pump is online | – | no |
| `pump.init` | initialization / Return to zero(Must be done when using it for the first time or when it gets stuck.) | – | **yes** |
| `pump.set_syringe` | Set syringe capacity,after µL shall be used for conversion. | `volume_ul` integer, required (label Syringe capacity, µL); must be in `syringe_pump.syringe_options_ul` (`[5000, 1250]`) else `syringe capacity only supports {options} µL` | no |
| `pump.set_valve` | Switch the diverter valve position on the pump head | `position` integer 1…15 (label Valve position); > `syringe_pump.valve_positions` (3) → `This pump only has {top} valve positions` | no |
| `pump.set_speed` | Set push speed(mL/s,will be converted into the original speed of the pump) | `speed_ml_s` number ≥ 0.001 (label speed, mL/s); > max → `The current upper speed limit of the syringe is {top} mL/s` | no |
| `pump.aspirate` | Inhale the specified volume | `volume_ul` number ≥ 1 (label volume, µL) | no |
| `pump.dispense` | launches the specified volume | `volume_ul` number ≥ 1 | no |
| `pump.stop` | Stop the syringe pump immediately | – | **yes** |

Conversions (`pump.py:20-35`, `93`, `98-106`):
- syringe volume = `state.pump.syringe_ul` or `syringe_pump.default_syringe_ul` (5000).
- `max_speed_ml_s = round(syringe_ul / 1000 × 6000 / full_stroke_steps, 3)` → 2.5 mL/s (5000 µL), 0.625 mL/s (1250 µL).
- raw speed = `max(1, min(6000, round(speed_ml_s × full_stroke_steps / (syringe_ul / 1000))))`.
- `steps = round(ul × full_stroke_steps / syringe_ul)` (12000 full stroke → 2.4 steps/µL at 5000 µL, 9.6 at 1250 µL).
- `_move`: `0 < volume_ul <= syringe` else `Volume must be between 0 and syringe capacity {syringe} µL between`; `1 <= steps <= stroke` else
  `The converted number of steps exceeds the stroke range`. The command carries `int(volume_ul)` (fractions of a µL are truncated); the firmware
  recomputes the steps itself from its own syringe setting.
- The backend keeps **no plunger position**; over-travel is left to the pump.

### scale.* (`backend/functions/scale.py`) — weighing module

| Name | Summary | Parameters | danger |
|---|---|---|---|
| `scale.read` | Current weight, statistics and curve information | `points` integer 0…6000, optional, default 300 (label curve points; 0 = all) | no |
| `scale.tare` | Empty cup returns to zero(Peel) | – | no |
| `scale.calibrate` | Calibrate after placing a weight of known weight | `grams` number 0.1…20000, required (label Weight weight, g) | no |
| `scale.set_stream` | Set the frequency of continuous reporting(0 = Stop) | `hz` integer 0…20, required (label frequency, Hz) | no |
| `scale.restart` | Restart continuous reading(cannot read the value and automatically stops.) | – | no |
| `scale.clear` | Clear curves and statistics(Does not affect calibration) | – | no |
| `scale.csv` | Export the curve data into CSV text | `points` integer 0…6000, optional, default 0 (label "Points(0 = All)") | no |
| `scale.diagnose` | diagnosis:loop loop test / sniff monitor / scan scan | `mode` enum `["loop","sniff","scan"]` (label model) | no |
| `scale.modbus` | Apply Modbus address / function code / scratchpad / Format | `address` integer 1…247 (station number); `function_code` integer choices `[3,4]` (function code); `register` integer ≥ 0 (scratchpad); `format` integer choices `[0,1,2,3,4]` (Format) | no |

No valve function exists (`valves.*` was removed; README:162, selftest.py:126-127).

---

## (d) Function → firmware command mapping

Cross-checked against `firmware/Arduino/Arduino.ino` (USB 115200 baud, text lines ending `\n`, firmware buffer max 95
characters, `Arduino.ino:1113-1122`). "Timeout" is the backend wait; 3.0 s = `link.command_timeout_s`,
60 s = `link.long_command_timeout_s`.

| Function | Strings sent, in order | Reply awaited (terminator) | Real firmware reply | Timeout |
|---|---|---|---|---|
| `motors.list` | none | – | – | – |
| `motors.clean` | `?`, `C`, `?` | `STATE,` / `OK`,`ERR`,`DONE` / `STATE,` | `OK,cleaning_started` or `ERR,cleaning_active` (`Arduino.ino:981-989`) | 3 s each |
| `motors.select` | `S,<motor>`, `?` | `DONE,S` or `ERR` | `DONE,S`; `ERR,idx(2~9)`; `ERR,cleaning_active`. Relay cold-switch blocks the firmware ≈125 ms (5 + 2×`SETTLE_MS` 60) | 3 s |
| `motors.run` | `V,<motor>,<sps>`, `?` — sps = `max(1, round(flow × pulses_per_ml / 60))`, negative for direction −1 | `OK`/`ERR`/`DONE` | `OK`; `ERR,arg` (motor out of 2…9 or \|sps\| > `MAX_SPS` 8000); `ERR,cleaning_active` | 3 s |
| `motors.stop` | `V,<motor>,0`, `?` | same | `OK` (no-op if that motor is not the selected one of its group, `Arduino.ino:1046-1051`) | 3 s |
| `motors.dose` | `P,<motor>,<steps>,<sps>`, `?` — steps = `max(1, round(volume_ml × dose_pulses_per_ml))`, negative for direction −1; sps always positive, from `dose_flow_ml_min` | `OK`/`ERR`/`DONE` | `OK` immediately (`Arduino.ino:1008-1028`); `ERR,arg` if sps ≤ 0 or > `MAX_SPS`. **No `DONE` is ever sent when the move finishes** — only an unsolicited `STATE,` line (`Arduino.ino:206-214`) | 3 s |
| `motors.dose_by_weight` | optionally `W,STREAM,<hz>` + `?`; `V,<m>,<fast sps>`; later `V,<m>,<approach sps>`, `V,<m>,<trim sps>`; `V,<m>,0`; top-ups `V,<m>,<trim sps>` / `V,<m>,0`; `?`; and always one more `V,<m>,0` in the `finally` block | `OK`/`ERR` | `OK` | 3 s per command; procedure timeouts in (f) |
| `motors.stop_all` | `X`, `W,STREAM,0`, `?` | `OK`… / `OK`,`ERR` / `STATE,` | `OK,all_stopped` (`Arduino.ino:1059-1069`); `OK,stream_hz=0` | 3 s each |
| `pump.status` | none | – | – | – |
| `pump.detect` | `Y,DETECT` | `PUMP,`/`FOUND,`/`OK,`/`ERR` | `FOUND,proto=ascii,baud=9600,addr=0` or `ERR,pump_not_found_ascii_9600_addr0` (firmware waits 500 ms). The error becomes HTTP 503, so `{"online": false}` is never returned on real hardware. | 3 s |
| `pump.init` | `Y,INIT` | same | firmware sends `ZR` to the pump, waits ≤ 2500 ms; `PUMP,proto=ascii,ready=<0\|1>,error=<n>[,data=…]` or `ERR,pump_timeout` / `ERR,pump_bad_reply` / `ERR,pump_echo_only` (possibly preceded by an `ASCIIRX,…` line) | 60 s |
| `pump.set_syringe` | `Y,SYR,<volume_ul>`, `?` | same | `OK,syringe_uL=<n>` (firmware RAM only; reverts to 5000 on Arduino reset) | 3 s |
| `pump.set_valve` | `Y,VALVE,<position>` | same | pump command `I<pos>R`, ≤ 1500 ms; `PUMP,…` or `ERR,…` | 3 s |
| `pump.set_speed` | `Y,SPEED,<raw 1…6000>` | same | pump command `V<raw>R`; `PUMP,…` | 3 s |
| `pump.aspirate` | `Y,ASPUL,<int µL>` | same | `INFO,pump_steps=<n>` then pump command `P<steps>R`; `PUMP,…`; `ERR,arg` if steps outside 1…12000 | 3 s |
| `pump.dispense` | `Y,DISPUL,<int µL>` | same | `INFO,pump_steps=<n>`, pump command `D<steps>R`; `PUMP,…` | 3 s |
| `pump.stop` | `Y,STOP` | same | pump command `T`; `PUMP,…` | 3 s |
| `scale.read` | none (cache) | – | – | – |
| `scale.tare` | `W,TARE`, `W,CFG` | `OK`/`ERR`; `CFG,` | `OK,tare_raw=<x>` or `ERR,scale_timeout`; `CFG,baud=…,addr=…,fc=…,reg=…,fmt=…,div=…,offset=…,avg=…,stable_g=…,stream_hz=…` | 3 s |
| `scale.calibrate` | `W,CAL,<grams:g>`, `W,CFG` | same | `OK,div=<x>,offset=<y>`; `ERR,arg`; `ERR,scale_timeout`; `ERR,no_change` | 3 s |
| `scale.set_stream` | `W,STREAM,<hz>`, `?` | `OK`/`ERR` | `OK,stream_hz=<n>`; `ERR,arg` | 3 s |
| `scale.restart` | `W,STREAM,<scale.stream_hz>`, `?` | same | same | 3 s |
| `scale.clear`, `scale.csv` | none | – | – | – |
| `scale.diagnose` loop | `W,LOOP` | `HINT,` | `LOOP,received="…"` then `HINT,…` | 3 s |
| `scale.diagnose` sniff | `W,SNIFF,3` | `SNIFF,common` | `SNIFF,monitor 3 Second…`, hex lines, `SNIFF,common <n> byte` (later `HINT` lines are not part of the result) | 60 s |
| `scale.diagnose` scan | `W,SCAN` | `SCAN,Complete` | `SCAN,Start scanning(about 40 Second)…`, `SCAN,baud=…`, `FOUND,baud=… address=… function code=… scratchpad=… information Starting from=…`, `SCAN,Complete,found <n>`. The firmware does not read USB commands while scanning. | 60 s — (cross-check addition) the scan itself lasts ≈60.0 s when no module answers (768 probes × 70 ms + request transmit time + 180 ms), so the wait can expire just before `SCAN,Complete`; because `SCAN,…` lines were already collected, `send()` then returns them **as success without the final line**, and the late `SCAN,Completed…` / `HINT,…` lines fall into whatever command is sent next |
| `scale.modbus` | `W,SET,<address>,<function_code>,<register>,<format>` | `OK`/`ERR` | **only a `CFG,…` line, no `OK`** (`Arduino.ino:826-833`) → the call waits the full 3 s and then returns the `CFG` line as success. RAM only; not written back to `hardware.yaml`. | 3 s (always fully used) |

Internal (not a registered function):
- `restore_calibration` (`scale.py:131-167`): `W,DIV,<divisor:.9g>` first, then (if offset ≠ 0) `W,OFFSET,<offset:.9g>`;
  an `OFFSET` failure is only a warning `The correction divisor has been loaded, but the zero offset has not yet been loaded:{exc}`.
- `_sync_scale_runtime` (`main.py:39-45`): `W,AVG,<n>`, `W,STABLE,<g>`, `W,STREAM,<hz>`, `?`.
- `Link.start` → `?`.

Firmware commands that exist but have **no** backend function: `Y,Q` (position), `Y,ST` (status), `Y,ASP`, `Y,DISP`,
`Y,POS`, `Y,ADDR`, `Y,BAUD`, `Y,SCAN`, `W,READ`, `W,HELP`, `W,RD`, `W,BAUD`, `STOP` (alias of `X`).
So the backend cannot ask the syringe pump whether it is still moving.

Details worth knowing:
- A `PUMP,…,error=<n>` line with n ≠ 0 is **not** treated as an error by the backend (it does not start with `ERR`);
  the caller gets it inside `result.lines`.
- Syringe commands return when the pump acknowledges the command (≤ 1.5 s), not when the plunger has finished.
- `X` in firmware: cancels the cleaning schedule, stops both groups, disables both drivers, all relays off
  (`cur = -1`), closes both valves **immediately**, sends `T` to the syringe pump without waiting, sets the weight
  stream to 0.
- Because of `_tx_lock`, `motors.stop_all` queues behind a command that is still waiting for its reply (normally
  ≤ 3 s; up to 60 s behind `W,SCAN` / `W,SNIFF` / `Y,INIT`).

---

## (e) State model the frontend sees

`Link.state` (`transport.py:97-115`), returned by `GET /api/state`, sent as `state` events, and embedded in several
function results (`snapshot()`, `transport.py:372-377`).

```json
{
  "connected": false,
  "simulated": false,
  "port": "-",
  "cleaning": false,
  "motors": { "A": {"selected": null, "state": "idle"},
              "B": {"selected": null, "state": "idle"} },
  "valves": { "1": false, "2": false },
  "pump":   { "online": false, "syringe_ul": 5000 },
  "scale":  { "grams": null, "raw": null, "stable": false, "online": false,
              "stream_hz": 0, "updated": 0.0, "sequence": 0 },
  "firmware": null,
  "last_error": null
}
```

| Field | Type / values | Source |
|---|---|---|
| `connected` | bool | `Link.connected` |
| `simulated` | bool | `link.simulate` |
| `port` | `"-"`, `"simulator"`, or the device name (`COM5`) | `Link.port_name` |
| `cleaning` | bool | `STATE … cleaning=1` |
| `motors.<group id>.selected` | motor id (int) or `null` | `STATE a=` (first group) / `b=` (second group); `-1` or an id not in that group → `null` |
| `motors.<group id>.state` | `"idle"`, `"wait"` (valve pre-open delay), `"run"` (continuous), `"move"` (finite dose) | `STATE sa=` / `sb=` (`Arduino.ino:324-328`) |
| `valves.<id>` | bool; keys are ints in Python and therefore the **strings** `"1"`, `"2"` in JSON | `STATE valves=<bits>` in channel order |
| `pump.online` | bool | `STATE pump=online` (else `unknown`) |
| `pump.syringe_ul` | int | `STATE syringe_uL=` |
| `scale.grams` | float or `null` | `WEIGHT,<g>,RAW,<raw>,ST,<0\|1>` field 1 |
| `scale.raw` | **string** (e.g. `"12345.0"`) or `null` | field 3, not converted |
| `scale.stable` | bool | field 5 == `"1"` |
| `scale.online` | bool: true on every weight line; false on `ERR,timeout…`, `ERR,stream_stopped…`, or `w_stream_hz=0` | reader |
| `scale.stream_hz` | int | `STATE w_stream_hz=` |
| `scale.updated` | epoch seconds (float) of the last weight | `time.time()` |
| `scale.sequence` | int, +1 per weight line (never reset while the process lives) | reader |
| `firmware` | the `READY…` line, e.g. `READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0`, or `null` | reader |
| `last_error` | string or `null` (`read interrupt:…`, or the text of the failed open) | `Link.last_error` |

`STATE` line format (firmware `printState`, `Arduino.ino:939-963`):
`STATE,a=<n|-1>,sa=<state>,b=<n|-1>,sb=<state>,pump=<online|unknown>,proto=ascii,baud=9600,addr=0,syringe_uL=<n>,valves=<bits>,cal_pulses_per_ml=<x>,max_flow_uL_min=<n>,w_stream_hz=<n>,cleaning=<0|1>`
Parsed with regex `([A-Za-z_]+)=([^,]*)` (`transport.py:273`). `proto`, `baud`, `addr`, `cal_pulses_per_ml`,
`max_flow_uL_min` are ignored by the backend.

The firmware sends `STATE` **unsolicited** when: a queued start becomes active, a finite move ends, a delayed valve
close happens, a cleaning phase changes (`Arduino.ino:162, 214, 321`). So `state` events arrive without polling.

Other state held in the backend:
- Weight history: `scale._history`, `deque(maxlen=6000)` of `{"t": epoch, "g": grams, "raw": str, "stable": bool}`
  (`scale.py:27, 170-175`); 10 minutes at 10 Hz; lost on restart; cleared by `scale.tare`, `scale.calibrate`,
  `scale.clear`, `scale.modbus`.
- Serial log: `Link.log`, last 400 `{"t","kind","text"}`.
- Calibration status dict (`scale.py:33-39`), exposed in `GET /api/config` under `calibration`. Messages:
  `No attempt has been made to load the calibration file yet`, `Reading calibration file`, `The calibration file was not found. It will be created automatically after the calibration is completed.`, `The last calibration conditions have been loaded`, `Calibration conditions have been accessed`.

Result shapes of the read-style functions:
- `motors.list` → `{"groups":[{"id","label","color","driver":{"pul","dir","ena"},"channels":[…],"selected","state"}],
  "pulses_per_ml","dose_pulses_per_ml","pulses_per_rev","driver_model","driver_switches","pump_head_model",
  "gravimetric_dose":{…},"cleaning":{…cleaning section…,"active":bool},
  "valve_sequence":{"open_before_ms","close_after_ms","motor_links"},"max_flow_ml_min"}` (`motors.py:167-197`).
- `motors.select`, `motors.stop` → the `motors` part of the state.
- `motors.run` → `{"motor","sps","flow_ml_min","pulses_per_ml","valve_sequence","state"}`; `valve_sequence` is `null`
  for unlinked motors, else `{"motor","valve","fluid","open_before_ms","close_after_ms"}`.
- `motors.dose` → `{"motor","steps","sps","flow_ml_min","pulses_per_ml","dose_pulses_per_ml","eta_s","valve_sequence","state"}`.
- `motors.clean` → `{"started","water_motor","out_motor","flow_ml_min","water_lead_s","overlap_s","out_tail_s","total_pump_timeline_s","state"}` (`state` = full snapshot).
- `motors.stop_all` → full snapshot.
- `motors.dose_by_weight` → see (f).
- `pump.status` → `{"online","syringe_ul","syringe_options_ul","full_stroke_steps","valve_positions","max_speed_ml_s","serial":{"port","tx","rx","baud","address"}}`.
- `pump.detect` → `{"online","lines"}`; `pump.init`, `pump.stop` → `{"lines"}`; `pump.set_syringe` →
  `{"syringe_ul","max_speed_ml_s","lines"}`; `pump.set_valve` → `{"position","lines"}`; `pump.set_speed` →
  `{"speed_ml_s","raw","lines"}`; `pump.aspirate/dispense` → `{"volume_ul","steps","lines"}`.
- `scale.read` → `{"grams","raw","stable","online","stream_hz","updated","stats":{"count","min","max","span","avg"},"series":[{"t","g","raw","stable"}]}`;
  here `online` means "a weight arrived within the last 3.0 s" (`scale.py:201`).
- `scale.tare` → `{"lines","calibration"}`; `scale.calibrate` → `{"grams","lines","calibration"}` with
  `calibration = {"version":1,"saved_at":ISO-UTC,"offset","divisor","reference_grams","file"}`.
- `scale.set_stream`, `scale.restart` → `{"hz","lines"}`; `scale.clear` → `{"cleared": true}`;
  `scale.csv` → `{"filename":"weight_%Y%m%d_%H%M%S.csv","content":"time,weight_g,raw,stable\n…"}` (time
  `%Y-%m-%dT%H:%M:%S` local); `scale.diagnose` → `{"mode","lines"}`; `scale.modbus` → `{"lines"}`.

---

## (f) Higher-level procedures

### f.1 Unit conversions (`motors.py:36-65`)
- Speed: `sps = max(1, round(mL/min × pulses_per_ml / 60))`, `pulses_per_ml` = `motors.pulses_per_ml` = 4237.00761337
  (a per-channel `pulses_per_ml` may exist but validation forces it to equal the global value).
- Dose length: `steps = max(1, round(mL × dose_pulses_per_ml))`, `motors.dose_pulses_per_ml` = 4558.96147256 (same rule).
- With the current YAML: 40 mL/min → 2825 sps; 10 → 706; 3 → 212; 80 (max) → 5649; 10 mL → 45590 steps (≈16.1 s);
  50 mL → 227948 steps (≈80.69 s).

### f.2 Dose by revolutions — `motors.dose` (`motors.py:270-301`)
1. Checks: motor exists; not cleaning; no weight dose active.
2. Flow is **forced** to `motors.dose_flow_ml_min` (40.0; fallback `motors.default_flow_ml_min`); a `flow_ml_min` in
   the request is ignored (`motors.py:284-287`).
3. Sends `P,<motor>,<±steps>,<sps>`; returns at once.
4. `eta_s = round(|steps| / max(|sps|,1) + (open_before_ms + close_after_ms)/1000 if the motor has a linked valve, 1)`.
5. The firmware does the rest: selects the relay, for linked motors opens the valve and waits
   `VALVE_OPEN_BEFORE_MS`, runs the steps, then schedules the valve close after `VALVE_CLOSE_AFTER_MS`.
   Group state goes `wait` → `move` → `idle`. No completion message other than `STATE`.
6. A new `P`/`V` for a motor in the same group replaces whatever that group was doing (one driver per group;
   `queueMotorStart` stops the group first). Groups A and B can run at the same time.

### f.3 Dose by weight, closed loop — `motors.dose_by_weight` (`motors.py:304-516`)
Blocking: the HTTP request stays open until the dose ends. All constants from `motors.gravimetric_dose` in the YAML.

| Constant (YAML key) | Value | Use |
|---|---|---|
| `enabled` | true | must be true |
| `fast_flow_ml_min` | 40.0 → 2825 sps | first stage |
| `approach_flow_ml_min` | 10.0 → 706 sps | when remaining ≤ slow zone (`min(fast, approach)`) |
| `trim_flow_ml_min` | 3.0 → 212 sps | when remaining ≤ trim zone, and for top-ups (`min(approach, trim)`) |
| `slow_zone_g` | 2.0 | effective `min(slow_zone_g, target × 0.25)` |
| `trim_zone_g` | 0.5 | effective `min(trim_zone_g, target × 0.05)` |
| `tolerance_percent` | 0.5 | `tolerance_g = target × 0.5 / 100`; `lower = target − tol`, `upper = target + tol` |
| `baseline_samples` | 5 | median of 5 new readings = zero point |
| `final_samples` | 7 | median of 7 new readings after settling |
| `settle_s` | 5.0 | wait after stopping the pump |
| `scale_ready_timeout_s` | 4.0 | maximum wait for **each** new reading |
| `no_gain_timeout_s` | 30.0 | abort if the gain has not risen by `minimum_gain_g` for this long |
| `minimum_gain_g` | 0.05 | "liquid has arrived" and "progress" threshold |
| `timeout_s` | 300.0 | overall limit, counted from pump start |
| `density_g_ml` | 1.0 | only for legacy `volume_ml` → grams |
| `default_target_g` | 10.0 | not used by the backend (frontend default) |
| `trigger_volume_ml` | 10.0 | not used by the backend |

Sequence:
1. Checks; take the global lock (`_weight_dose_lock`, non-blocking); register a cancel `Event` and the motor.
2. If the cached `scale.stream_hz` differs from `scale.stream_hz` in the YAML → `W,STREAM,<hz>` and `?`.
3. Baseline: 5 samples (a sample counts when `scale.online` is true, `sequence` increased and grams is finite;
   polled every 10 ms) → `baseline_g` = median. (cross-check correction: `_collect_weights` starts from
   `last_sequence = 0`, `motors.py:143`, so the **first** of the 5 is the reading already in the cache — up to one
   stream period old — and only the other 4 are new. The same holds for the 7 final samples.)
4. `V,<motor>,<fast sps × direction>`; phase `"priming"`.
5. For every new sample: `gain = grams − baseline`; `measured_gain` = median of the last 3 gains.
   - first time `peak gain ≥ minimum_gain_g` → phase `"coarse"`, `liquid_detected_s` recorded;
   - `measured_gain ≥ lower` → leave the loop (the pump is stopped at **target − tolerance**);
   - no progress for `no_gain_timeout_s` → error `weight has been continuous {n} seconds do not continue to rise; please check the pipeline, direction and weighing position`;
   - `remaining ≤ trim zone` and not yet trim → `V,<motor>,<trim sps>`, phase `"trim"`;
   - else `remaining ≤ slow zone` and phase is `"coarse"` → `V,<motor>,<approach sps>`, phase `"approach"`;
   - overall limit → `The weighing quantity has exceeded {timeout_s} The target has not been reached in seconds and the peristaltic pump has been stopped.`;
   - no new reading within 4 s → `The weight module did not provide a new valid reading and the peristaltic pump has been stopped.`.
6. `V,<motor>,0`; wait `settle_s` (checks cancel every 20 ms); 7 final samples → `dispensed_g`.
7. Top-up loop while `dispensed_g < lower`: `V,<motor>,<trim sps>` until a single reading reaches `lower`
   (same no-gain rule, message `The weight has been continued during rehydration. Checklist in {n} seconds did not continue to rise and the peristaltic pump was stopped.`), `V,<motor>,0`,
   `time.sleep(settle_s)`, 7 final samples again; `topups` counts the rounds.
8. Result (HTTP 200 even when out of tolerance):
   `{"motor","mode":"gravimetric","valve_sequence","volume_ml","density_g_ml","target_g","tolerance_g",
   "tolerance_percent","fast_flow_ml_min","approach_flow_ml_min","trim_flow_ml_min","fast_sps","approach_sps",
   "trim_sps","liquid_detected_s","baseline_g","dispensed_g","error_g","within_tolerance","stable","topups",
   "elapsed_s","warning","state"}`; `warning` = `The final weight exceeds the allowable range; please reduce the finishing flow rate, confirm the vibration isolation of the scale platform and recalibrate` when
   `within_tolerance` is false, else `null`.
9. Cancel: `motors.stop` for the same motor or `motors.stop_all` sets the event → the dose call ends with HTTP 400
   `Weighing and rationing has been stopped by the user`.
10. `finally`: always `V,<motor>,0` (errors ignored), clear the session, release the lock.

Behaviour to be aware of:
- While a weight dose runs, `motors.clean/select/run/dose` are refused for every motor, but `scale.tare`,
  `scale.calibrate`, `scale.set_stream`, `scale.modbus` and all `pump.*` are **not** blocked. A tare in the middle
  would shift the zero under the running dose.
- `direction: -1` is accepted, but the loop only ends on a weight *increase*, so a reverse run would end in the
  30 s no-gain error.
- Lines `motors.py:346-358` run after the lock is taken but before the `try/finally`. `scale_ready_timeout_s` is read
  there and is not covered by config validation; if that key were missing the lock would never be released (all later
  weight doses and `clean/select/run/dose` refused until restart). With the current YAML this cannot happen.
- For linked motors (2, 9) each start includes the 1 s valve pre-open; speed changes during the wait only update the
  queued speed (`Arduino.ino:245-249`).

### f.4 Automatic cleaning — `motors.clean` (`motors.py:200-230`)
The backend only checks preconditions and sends `C`; **the whole sequence runs in the firmware** with constants
compiled into `hw_config.h` from the `cleaning` section (`CLEAN_WATER_MOTOR 2`, `CLEAN_OUT_MOTOR 8`,
`CLEAN_WATER_SPS 2825.0F`, `CLEAN_OUT_SPS 2825.0F`, `CLEAN_WATER_LEAD_MS 30000UL`, `CLEAN_OVERLAP_MS 45000UL`,
`CLEAN_OUT_TAIL_MS 45000UL`). Changing the YAML without re-flashing changes what the backend *reports*, not what the
Mega does.

Firmware sequence (`Arduino.ino:281-322`): stop everything and close valves → select relay of motor 8 → start motor 2
(valve 1 opens, 1 s wait) → at water start + 30 s start motor 8 → + 45 s stop motor 2 (valve 1 closes 1 s later) →
+ 45 s stop motor 8, `cleaning=0`. Pump timeline 120 s (`total_pump_timeline_s`) plus the 1 s valve pre-open.

During cleaning: firmware refuses `S`, `P`, `V` with `ERR,cleaning_active`; backend refuses
`select/run/stop/dose/dose_by_weight` with 400. **Only `motors.stop_all` aborts a cleaning run.** The end of cleaning
is visible only as `state.cleaning` becoming `false`.

YAML constraints for cleaning (`config.py:95-120, 161-164`): both motors exist and differ; they must be in different
driver groups; the water motor must have a valve link, the OUT motor must not; `0 < flow_ml_min ≤ max_flow_ml_min`;
`0 ≤ water_lead_s ≤ 3600`; `0 < overlap_s ≤ 3600`; `0 ≤ out_tail_s ≤ 3600`; flow in sps ≤ `max_sps`.

### f.5 Valve interlocks
Entirely in firmware (`MOTOR_VALVE_LIST { 1, 0, 0, 0, 0, 0, 0, 2 }`: motor 2 ↔ valve 1, motor 9 ↔ valve 2;
`VALVE_OPEN_BEFORE_MS 1000UL`, `VALVE_CLOSE_AFTER_MS 1000UL`), generated from `valves.motor_links`,
`valves.open_before_ms`, `valves.close_after_ms`. The backend has no command to open or close a valve; it only
mirrors `valves` in the state, reports `valve_sequence`, and adds the two delays to `eta_s`.

### f.6 Stop all — `motors.stop_all` (`motors.py:519-526`)
`_cancel_weight_dose()` (any motor) → `X` → `W,STREAM,0` → `?` → returns the full state. Not blocked by cleaning or a
weight dose. Effects in firmware listed in (d). Afterwards `scale.stream_hz = 0`, `scale.online = false`; resume with
`scale.restart`. This is a software stop over USB; it depends on the PC, the USB link and the firmware loop.

### f.7 Scale calibration persistence (`scale.py:42-167`)
- File: `config/scale_calibration.json` (override with env `SCALE_CALIBRATION_FILE`). Current content:
  `{"version": 1, "saved_at": "2026-08-26T10:16:20.306868+00:00", "offset": -1.0, "divisor": 287703.03125, "reference_grams": 200.0}`.
- After `W,TARE` or `W,CAL` the backend reads the applied values back with `W,CFG` and writes the file atomically
  (`.tmp` + `os.replace`). `reference_grams` is kept from the previous file on a tare.
  (cross-check addition) What is stored is the **printed** value: the firmware prints `div` with 6 decimals and
  `offset` with 3 decimals (`Arduino.ino:754-755`), and both are 32-bit floats in the Mega. The file therefore cannot
  hold more precision than that.
- On start and on reconnect the values are pushed into Arduino RAM (`W,DIV` then `W,OFFSET`); the firmware's own
  defaults are `scaleDiv = 1.0`, `scaleOffset = 0` (`Arduino.ino:514-515`).
- Errors: `The weight correction file cannot be read:{path}`, `The content of the weight correction file is invalid:{path}`, `The format of the weight correction data returned by the firmware is incorrect.`,
  `The firmware returned an invalid weight correction value.`, `Firmware does not return weight correction data`, `Calibration has been applied, but the calibration file cannot be written:{path}`.
- `.gitignore` excludes this file.

---

## (g) Simulator (`backend/simulator.py`)

Switched on with `link.simulate: true` in the YAML (`transport.py:94, 145-148`); then `port` is `"simulator"` and
pyserial is not needed. The self-test switches it on through a copied YAML and env `LAB_CONFIG`.
`link.simulation_speedup` (default 1) speeds up finite moves and simulated weight gain.

Emulates the same text protocol:
- Boot line `READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0`.
- `?`, `X`/`STOP`, `C`, `S,<m>`, `V,<m>,<sps>`, `P,<m>,<steps>,<sps>` with the same replies; group states
  `idle/wait/run/move`; valve pre-open and delayed close from `valves.*`; the cleaning schedule from `cleaning.*`
  (read live from the config, not from `hw_config.h`); unsolicited `STATE` lines on transitions.
- `Y,…`: `DETECT`/`SCAN` → `FOUND,proto=ascii,baud=9600,addr=0`; `SYR`, `INIT`, `STOP`, `Q`, `ST`, `VALVE`, `SPEED`,
  `ASP`, `DISP`, `ASPUL`, `DISPUL`, `POS` → `PUMP,proto=ascii,ready=1,error=0[,data=<arg>]`; otherwise
  `ERR,bad_pump_cmd`. No plunger model, never an error.
- `W,…`: `READ`, `STREAM`, `TARE`, `CAL`, `DIV`, `OFFSET`, `AVG`, `STABLE`, `CFG`, `SCAN`, `LOOP`, `SNIFF`, `SET`;
  otherwise `ERR,bad_scale_cmd` (`HELP`, `RD`, `BAUD` are not emulated).
- Weight: every `1/stream_hz` s a line `WEIGHT,<g:.3f>,RAW,<raw:.1f>,ST,<0|1>`.
  `raw = offset + payload_g × div + sin drift (±0.5 raw) + gaussian noise (σ 0.5 raw)`, `div` starts at 250.0;
  `payload_g` grows by `Σ|sps|/pulses_per_ml / stream_hz × speedup` per sample while a group is in `run`
  (continuous only; finite `P` moves add no weight); density 1 g/mL; `ST` is 0 while any group is in `run`.

Differences from the real firmware: `Y,SYR` answers with a `PUMP,` line instead of `OK,syringe_uL=`; `W,CAL` does not
change the divisor; no range checks on `V`/`P` (no `ERR,arg` for too-high sps); pump never offline until first `Y`
command and never fails; scale never times out; a non-numeric argument raises a Python exception inside
`Link.send` (surfaces as `failed to send:…`).

---

## (h) Third-party dependencies, module by module

`requirements.txt`: `fastapi==0.115.6`, `uvicorn[standard]==0.34.0`, `pyserial==3.5`, `PyYAML==6.0.2`, `httpx==0.28.1`.

| Module | Third-party imports | How deep | Runs without it? |
|---|---|---|---|
| `backend/main.py` | `fastapi` (`FastAPI`, `HTTPException`, `Request`, `CORSMiddleware`, `FileResponse`, `JSONResponse`, `StreamingResponse`); `uvicorn` (imported inside `run()`) | The whole file is a FastAPI app: 10 handler functions (11 route decorators) plus FastAPI's generated Swagger/OpenAPI routes, lifespan, SSE via `StreamingResponse`, `asyncio.to_thread`. No device logic. | No. It is ~235 lines of routing that would be rewritten for another server. Nothing else in the backend imports it. |
| `backend/config.py` | `yaml` (PyYAML) | One call: `yaml.safe_load` (`config.py:227`). The import is unconditional at the top. | Not as written — and because `transport.py` imports `config`, the import failure propagates. The only thing needed from PyYAML is turning the file into a dict; validation is plain Python. The YAML file uses comments, nested mappings, flow mappings/sequences and unquoted Chinese strings. |
| `backend/registry.py` | none | – | Yes, unchanged. |
| `backend/transport.py` | `serial`, `serial.tools.list_ports` (pyserial) inside `try/except` → `None` | `serial.Serial(port, baud, timeout=0.1)`, `.readline()`, `.write()`, `.flush()`, `.close()`; `list_ports.comports()` with `.description`, `.manufacturer`, `.device`. | Imports and runs in simulator mode without it. Real hardware needs it (or another way to open a COM port). This is the one dependency with no standard-library equivalent on Windows. |
| `backend/simulator.py` | none | – | Yes. |
| `backend/functions/motors.py` | none (`math`, `statistics`, `threading`, `time`, `collections`) | – | Yes. Needs only `..registry` and the injected `ctx.link` / `ctx.config`. |
| `backend/functions/pump.py` | none | – | Yes. |
| `backend/functions/scale.py` | none (`io`, `json`, `math`, `os`, `threading`, `time`, `collections`, `datetime`, `pathlib`) | – | Yes. |
| `tools/selftest.py` | `yaml`, `httpx`, `uvicorn` (+ `fastapi` through `backend.main`) | Test client and server | No. `httpx` is used nowhere else. |
| `tools/gen_firmware_config.py` | none directly (`backend.config` → `yaml`) | – | Only if config loading works. |
| `tools/url.py` | none directly (`backend.config` → `yaml`; any failure prints the default URL) | – | Degrades to the default URL. |
| `tools/probe.py` | none | – | Yes. |

What the function modules need from a host program (the injection surface, `registry.Context`):
`ctx.config` (the dict) and `ctx.link` with `send(cmd) -> list[str]`, `snapshot() -> dict`, `refresh_state() -> dict`,
and `weight_hooks` (list of callables receiving the scale dict). `scale.restore_calibration(link)` and
`scale.record(sample)` are called by the host (`main.py:56, 75-76, 131`).

Language level: `asyncio.to_thread` (3.9+, only in `main.py`), `Path.unlink(missing_ok=True)` (3.8+),
`from __future__ import annotations` everywhere. Nothing that conflicts with Python 3.12.

Not determined (the `runtime` folder is off-limits and no file states it): whether the embeddable Python 3.12 of the
EC program contains `ctypes`, which pyserial uses on Windows.

---

## (i) What the self-test covers (`tools/selftest.py`, run by `selftest.bat`)

`selftest.bat` requires `.venv\Scripts\python.exe` (else "Environment not built yet. Please run run.bat once first.")
and runs `tools\selftest.py` with `-X utf8`. It needs no Arduino. It starts a **real uvicorn** on a random free
127.0.0.1 port in a thread, with a copy of the YAML where `link.simulate = True`, `boot_delay_s = 0`,
`simulation_speedup = 5`, cleaning times `0.3 / 0.5 / 0.4` s. Temporary files it creates and deletes in `config/`:
`.selftest.yaml`, `.bad.yaml`, `.selftest-scale-calibration.json`. About 100 `check(` call sites (some inside loops,
so the number of results at run time is higher).
Exit code 1 if anything failed; prints `passed N item,failed M item`.

1. **[1] Profile and IaC** — YAML validates; a deliberately conflicting pin (valve 1 pin = driver A `pul`) is rejected;
   `hw_config.h` equals `render(cfg)`.
2. **[2] server** — uvicorn started; `GET /api/config` (`link.simulated`, `calibration.attempted` true and
   `file_exists` false); `GET /fn` lists ≥ 26 functions with `motors.`, `pump.`, `scale.` and no `valves.`.
3. **[3] relay + peristaltic pump** — `motors.list` has 2 groups; fixed numbers (dose flow 40; weight stages 40/10/3;
   tolerance 0.5 %; settle 5; timeout 300; default target 10 g); fluids 2=water, 8=OUT, 9=CuSO₄;
   `pulses_per_ml = reference × 10 / 13.25`; `dose_pulses_per_ml = pulses_per_ml × 50 / 46.469`; all channels share
   both coefficients; stage speeds `[2825, 706, 212]`; `pulses_per_rev == 1600` **and the 8-switch DM542J list (see §0
   item 6)**; `motors.select`; `motors.run` sps; reverse `motors.dose` gives negative steps and the expected ETA;
   too-high flow → 400; unknown motor → 400; direction 7 → 400; stopping a non-selected motor of the same group does
   not disturb the running one; `motors.stop`; `motors.dose_by_weight` 10 g in the simulator within ±0.5 %;
   50 mL on motor 2 = 227948 steps at 2825 sps; a `flow_ml_min` argument is ignored; motors 2…9 all give
   `(200, 227948, 2825, 80.689558)`.
4. **[4] Top automatic cleaning** — production timings are exactly `{2, 8, 40.0, 30.0, 45.0, 45.0}`; the five `CLEAN_*` defines
   are in the rendered header; `motors.clean` → `cleaning` true, group A `wait`, group B selected 8, valve `"1"` open;
   `motors.run` refused (400) during cleaning; phases water only → both → OUT only → finished.
5. **[5] solenoid valve automatic linkage** — links `{(2,1),(9,2)}`; delays 1000/1000; motor 2: valve opens first and state is `wait`,
   after 1.1 s `run`; after stop the valve stays open, closed after 1.1 s; motor 9 opens valve 2.
6. **[6] syringe pump** — max speed 2.5 mL/s (5 mL syringe); `pump.detect`; 1.25 mL syringe → 0.625 mL/s;
   625 µL = 6000 steps; over-volume, over-speed, valve position 99 → 400.
7. **[7] weight module** — `average_samples == 1`, `display_decimals == 3`; `scale.read` has weight and history; stream
   frequency; `sequence` increases; `W,AVG,1` was sent at start and `W,CFG` shows `avg=1`; `scale.tare` clears
   history; `scale.calibrate` writes the file and a second calibration overwrites it; negative grams → 400;
   `scale.csv` header; `scale.diagnose scan` returns a `FOUND` line; invalid mode → 400; `hz=99` → 400.
8. **[8] global and error handling** — `motors.stop_all` sets `stream_hz` 0 and sends both `X` and `W,STREAM,0`;
   `scale.restart`; an asynchronous `ERR,timeout(module did not respond)` is not attributed to a pending motor command; unknown
   parameter → 400; unknown function → 400; `GET /api/state`; `GET /`; `GET /docs` and six doc pages; path traversal
   blocked; `GET /api/docs`; `POST /api/reconnect` reloads the calibration file (`div=333.2500`); links between doc
   pages exist; the folder `Reference Document(Old version)` is absent.
9. **[9] SSE** — content type `text/event-stream`; at least 1 `state` and 8 `weight` events within 5 s.
10. **[10] Consistency between front-end and back-end** — every `call("x.y"` in the page exists in the registry; page uses `/api/config`,
    `CFG.motors.groups`, `CFG.valves.channels`; no valve controls; specific strings/markup of the current console
    (`weightTarget`, `[10, 5, 1, -10, -5, -1]` twice, `class="presets six"` twice, `automatic cleaning · water → OUT`,
    `main.appendChild(cleaningCard())`, `Calibration conditions have been accessed`, …). These checks are tied to the existing `frontend/index.html`.

Not covered: real serial I/O, port auto-detection, loss of connection, syringe pump errors, scale time-outs,
cancelling a weight dose, top-up rounds, the no-gain and overall time-outs.

---

## (j) Full schema of `config/hardware.yaml`

"Req." = listed in `_validate`'s required paths (`config.py:36-48`) or accessed with `[]` during validation (missing →
`ConfigError` or `KeyError` at start). "Used by": B = backend code, G = `gen_firmware_config.py` → `hw_config.h`,
S = simulator, – = not read by any of the files I read (frontend/docs may use it; not checked here).

### `meta`
| Key | Current value | Req. | Used by |
|---|---|---|---|
| `name` | `independent laboratory v5` | G reads with `[]` | G (header comment) |
| `description` | `8 grain RZ1030B-8 peristaltic pump + automatic cleaning + syringe pump + 2 solenoid valve + 10 Hz Weight closed loop` | G `[]` | G |

### `server`
| Key | Value | Req. | Used by |
|---|---|---|---|
| `host` | `127.0.0.1` | no (default `127.0.0.1`) | B (`main.run`), `tools/url.py` |
| `port` | `8000` | **yes** | B, `url.py` |
| `cors_origins` | `["*"]` | no (default `["*"]`) | B |

### `link`
| Key | Value | Req. | Used by |
|---|---|---|---|
| `port` | `auto` | no (default `auto`) | B |
| `baud` | `115200` | **yes** | B (firmware has 115200 hard-coded, `Arduino.ino:1080`) |
| `simulate` | `false` | no | B |
| `boot_delay_s` | `2.5` | no (default 2.5) | B |
| `command_timeout_s` | `3.0` | read with `[]` at first command, not validated | B |
| `long_command_timeout_s` | `60.0` | same | B |
| `simulation_speedup` | `1` | no | S |

### `motors`
| Key | Value | Req. / rule | Used by |
|---|---|---|---|
| `driver_model` | `TB6600` | default `DM542J`; known models fix the switch count: `DM542J`/`DM542` = 8, `TB6600` = 6 | B (validation, `motors.list`), G (comment) |
| `driver_switches` | `["OFF","ON","OFF","ON","ON","OFF"]` | count must match the model; each `ON`/`OFF` | B |
| `driver_current_a` | `1.5` | – | – |
| `driver_current_peak_a` | `1.7` | – | – |
| `min_pulse_us` | `5` | 1…100 and `2 × min_pulse_us ≤ 1e6 / max_sps` (default 3) | B (validation), G (`MIN_PULSE_US`) |
| `motor_full_steps_per_rev` | `200` | `[]`; × `microsteps` must equal `pulses_per_rev` | B (validation) |
| `microsteps` | `8` | `[]` | B (validation) |
| `pulses_per_rev` | `1600` | **yes** | B, G (`PULSES_PER_REV`) |
| `pump_head_model` | `RZ1030B-8` | – | B (`motors.list`) |
| `rollers` | `8` | – | – |
| `tube_inner_diameter_mm` | `3.0` | – | – |
| `tube_wall_thickness_mm` | `1.0` | – | – |
| `reference_max_rpm` | `400` | – | – |
| `reference_max_flow_ml_min` | `114` | – | – |
| `driver_settle_ms` | `60` | G `[]` | G (`SETTLE_MS`) |
| `max_sps` | `8000` | `[]` | B, G (`MAX_SPS`) |
| `reference_pulses_per_ml` | `5614.03508772` | **yes**, > 0 | validation and self-test only |
| `pulses_per_ml` | `4237.00761337` | **yes**, > 0 | B (speed), G (`PULSES_PER_ML`), S |
| `dose_pulses_per_ml` | `4558.96147256` | **yes**, > 0 | B (dose steps) |
| `max_flow_ml_min` | `80` | `[]`; `max_flow × pulses_per_ml / 60 ≤ max_sps` | B, G (`MAX_FLOW_UL_MIN`), S |
| `default_flow_ml_min` | `40` | effectively required: it is the fallback argument of `motors.get("dose_flow_ml_min", motors["default_flow_ml_min"])` and Python evaluates that argument even when `dose_flow_ml_min` exists (`config.py:210-211`, `motors.py:286-287`) | B (fallback for the dose speed) |
| `dose_flow_ml_min` | `40.0` | `0 < x ≤ max_flow_ml_min` | B (`motors.dose`) |
| `default_dose_ml` | `10.0` | – | – |
| `gravimetric_dose` | mapping | **yes** | see below |
| `groups` | list | **yes**; exactly 2 for G | B, G, S |

`motors.gravimetric_dose` (values and use in (f.3)); validated > 0: `default_target_g`, `density_g_ml`,
`tolerance_percent` (also ≤ 100), `slow_zone_g`, `trim_zone_g`, `timeout_s`, `fast_flow_ml_min`,
`approach_flow_ml_min`, `trim_flow_ml_min` (each ≤ `max_flow_ml_min`, and trim ≤ approach ≤ fast).
**Not validated but read with `[]` at run time:** `scale_ready_timeout_s`, `baseline_samples`, `final_samples`,
`settle_s`, `no_gain_timeout_s`, `minimum_gain_g`. `enabled` read with `.get(…, False)`. Unused by the backend:
`trigger_volume_ml`, `default_target_g`.

`motors.groups[]`:
| Key | Values | Rule |
|---|---|---|
| `id` | `A`, `B` | key of `state.motors`; first group ↔ firmware `a/sa`, second ↔ `b/sb` |
| `label` | `A Group`, `B Group` | – |
| `color` | `blue`, `purple` | default `blue` in `motors.list` |
| `driver` | `{pul: 22, dir: 23, ena: 24}`, `{pul: 25, dir: 26, ena: 27}` | pins unique across the whole file |
| `channels[]` | A: `{id: 2, pin: 2, label: motor 2 · water, fluid: water}`, `{id: 3, pin: 3, label: motor 3}`, `{id: 4, pin: 4, label: motor 4}`, `{id: 5, pin: 5, label: motor 5}`; B: `{id: 6, pin: 6, label: motor 6}`, `{id: 7, pin: 7, label: motor 7}`, `{id: 8, pin: 8, label: motor 8 · OUT, fluid: OUT}`, `{id: 9, pin: 9, label: motor 9 · CuSO₄, fluid: CuSO₄}` | `pin` unique; G requires `id == pin` and contiguous ids; firmware assumes 4 relays per group; optional per-channel `pulses_per_ml` / `dose_pulses_per_ml` must equal the global values |

### `cleaning` (all **required**; rules in f.4)
`water_motor: 2`, `out_motor: 8`, `flow_ml_min: 40.0`, `water_lead_s: 30.0`, `overlap_s: 45.0`, `out_tail_s: 45.0`.
Used by B (checks, reporting), G (`CLEAN_*`), S.

### `valves`
| Key | Value | Req. / rule | Used by |
|---|---|---|---|
| `open_before_ms` | `1000` | **yes**, 0…60000 | B (report, ETA), G, S |
| `close_after_ms` | `1000` | **yes**, 0…60000 | B, G, S |
| `motor_links[]` | `{motor: 2, valve: 1, fluid: water}`, `{motor: 9, valve: 2, fluid: CuSO₄}` | **yes**; motor and valve must exist; each at most once | B, G (`MOTOR_VALVE_LIST`), S |
| `channels[]` | `{id: 1, pin: 10, label: solenoid valve 1 · water}`, `{id: 2, pin: 11, label: solenoid valve 2 · CuSO₄}` | **yes**; pins unique | B (state keys), G (`NUM_VALVES`, `VALVE_PIN_LIST`), S |

### `syringe_pump`
| Key | Value | Req. | Used by |
|---|---|---|---|
| `serial` | `Serial1` | G `[]` | B (`pump.status`), G |
| `tx_pin` / `rx_pin` | `18` / `19` | pins unique | B (validation, status), G (comment) |
| `baud` | `9600` | **yes** | B (status), G |
| `address` | `0` | – | B (status), G |
| `full_stroke_steps` | `12000` | `[]` at run time | B, G |
| `default_syringe_ul` | `5000` | `[]` | B, G, S |
| `syringe_options_ul` | `[5000, 1250]` | `[]` | B |
| `valve_positions` | `3` | `[]` | B |

### `scale`
| Key | Value | Req. / rule | Used by |
|---|---|---|---|
| `serial` | `Serial2` | G `[]` | G |
| `tx_pin` / `rx_pin` / `de_pin` | `16` / `17` / `36` | pins unique | B (validation), G |
| `stream_hz` | `10` | **yes**, 1…20 | B, G, S |
| `average_samples` | `1` | **yes**, 1…16 | B (`W,AVG`), G, S |
| `display_decimals` | `3` | 1…4 (default 3) | – (frontend) |
| `stable_threshold_g` | `0.015` | **yes**, 0 < x ≤ 10 | B (`W,STABLE`), G, S |
| `chart_points` | `300` | – | – |
| `calibration_presets_g` | `[10, 50, 100, 200]` | – | – |
| `default_calibration_g` | `100` | – | – |
| `modbus.baud` | `9600` | G `[]` | G, S |
| `modbus.address` | `1` | G `[]` | G, S |
| `modbus.function_code` | `3` (3 = holding, 4 = input) | G `[]` | G, S |
| `modbus.register` | `0` | G `[]` | G, S |
| `modbus.format` | `1` (0 int16 / 1 int32 high word first / 2 int32 low word first / 3 float ABCD / 4 float CDAB) | G `[]` | G, S |

Global validation rule: every pin in `motors.groups[].driver.{pul,dir,ena}`, `motors.groups[].channels[].pin`,
`valves.channels[].pin`, `syringe_pump.tx_pin/rx_pin`, `scale.tx_pin/rx_pin/de_pin` must be unique, else
`foot position D{pin} Specified repeatedly:{a} and {b}` (`config.py:51-59`). Other messages: `hardware.yaml Missing required fields:{path}`,
`Profile not found:{path}`, `hardware.yaml format is incorrect,The outermost layer must be the corresponding table`.

---

## Could not determine

- Which firmware build is on the Mega and whether it matches the current `hw_config.h` (the `READY` line carries no
  version; `cal_pulses_per_ml`/`max_flow_uL_min` in `STATE` are not compared by the backend).
- Whether the rig really has TB6600 drivers now (YAML) or DM542J (README, self-test, a firmware comment).
- Which COM port the Mega has on this PC, and what other serial devices are present.
- Whether the self-test passes today (not run; one check contradicts the YAML by reading).
- What "v7" changed relative to "v5".
- What the syringe pump's `error=<n>` codes mean; the backend passes them through uninterpreted.
- What liquids motors 3-7 carry (only 2, 8, 9 have a `fluid`).
- Whether the saved scale calibration (2026-08-26, divisor 287703.03125, offset -1.0) is valid for the present set-up.
- Whether the EC program's embeddable Python can load pyserial (needs `ctypes`); `runtime` was off-limits.
- Anything about `frontend/index.html` and `docs/` beyond the strings the self-test looks for (not in my assignment).

---

## Cross-check

Done 2026-10-06 by a second reader who re-read every line of `backend/__init__.py`, `config.py`, `registry.py`, `transport.py`, `simulator.py`, `main.py`, `functions/__init__.py`, `functions/motors.py`, `functions/pump.py`, `functions/scale.py`, `config/hardware.yaml`, `config/scale_calibration.json`, `requirements.txt`, `run.bat`, `tools/gen_firmware_config.py`, `firmware/Arduino/Arduino.ino`, `firmware/Arduino/hw_config.h` and `frontend/index.html`. Nothing was run. `tools/selftest.py`, `tools/probe.py`, `tools/url.py`, `README.md` and `selftest.bat` were **not** re-read, so §(i) and item 6 of §0 are unchecked.

Result: **no wrong command string, terminator, timeout, endpoint or constant was found.** One statement was imprecise (item 1 below) and several facts that matter for a new host were missing.

### Corrections made in the text above

1. (f.3 step 3) "5 new samples" — the first sample of every `_collect_weights` call is the reading already cached, not a new one (`motors.py:143`, `last_sequence = 0`). Text corrected.

### Additions made in the text above

2. (a) Lifespan: shutdown sends nothing to the Mega; pumps keep running after the service stops.
3. (a) Who owns the COM port: `readline()` with `timeout=0.1` can hand over line fragments [INFER]; simulator lines are not stripped.
4. (d) `scale.diagnose scan`: the 60 s wait is the same length as the firmware's worst-case scan; on expiry the partial output is returned as success.
5. (f.7) The calibration file holds the values as printed by `W,CFG` (6 and 3 decimals).

### Claims verified against the source

| # | Claim in this report | Verdict | Where |
|---|---|---|---|
| 1 | 26 registered functions (8 `motors.*`, 9 `pump.*`, 9 `scale.*`) | correct, counted | `functions/*.py` |
| 2 | Start-up traffic `?` → `W,DIV,287703.031` → `W,OFFSET,-1` → `W,AVG,1` → `W,STABLE,0.015` → `W,STREAM,10` → `?` | correct (`.9g` of 287703.03125 is `287703.031`, of −1.0 is `-1`; `g` of 0.015 is `0.015`) | `transport.py:170`; `scale.py:148-153`; `main.py:42-45` |
| 3 | Port opened as `serial.Serial(port, 115200, timeout=0.1)`, then `time.sleep(2.5)`, before the reader thread exists | correct | `transport.py:153-167` |
| 4 | Auto port: first port whose description/manufacturer contains `arduino`, `mega`, `ch340`, `usb-serial` or `wch`; else the first port; no identity check | correct | `transport.py:126-139` |
| 5 | One command at a time; `_tx_lock` held until terminator or timeout | correct | `transport.py:331-350` |
| 6 | Terminator table, including `("OK", "ERR", "DONE")` for `V`/`P`/`C`/`X` and `("PUMP,", "FOUND,", "OK,", "ERR")` for `Y,` | correct | `transport.py:52-71` |
| 7 | Timeout 3.0 s, 60.0 s for `W,SCAN` / `W,SNIFF` / `Y,INIT` | correct | `transport.py:74-76, 327-329`; `hardware.yaml:36, 38` |
| 8 | Timeout with at least one collected line returns as success | correct | `transport.py:352-357` |
| 9 | `WEIGHT,` and the two async scale errors are kept out of a pending command's lines (except `W,READ`) | correct | `transport.py:231-244` |
| 10 | Any collected line starting with `ERR` → `LinkError` → HTTP 503 | correct | `transport.py:354-356`; `main.py:159-160` |
| 11 | `motors.run` → `V,<m>,<±sps>` with `sps = max(1, round(flow × 4237.00761337 / 60))`; 40 → 2825, 10 → 706, 3 → 212, 80 → 5649 | correct, recomputed | `motors.py:56-59, 253-254` |
| 12 | `motors.dose` → `P,<m>,<±steps>,<sps>`, steps from `dose_pulses_per_ml`, flow forced to `dose_flow_ml_min`; 10 mL → 45590, 50 mL → 227948 | correct, recomputed | `motors.py:62-65, 286-292` |
| 13 | `motors.stop_all` → `X`, `W,STREAM,0`, `?` after cancelling a weight dose | correct | `motors.py:519-526` |
| 14 | `motors.clean` → `?`, checks, `C`, `?` | correct | `motors.py:203-217` |
| 15 | Weight-dose constants and sequence (median of 3 gains, stop at target − tolerance, settle 5 s, 7 final samples, trim top-ups, 300 s / 30 s / 4 s limits, `finally` always sends `V,<m>,0`) | correct, except item 1 above | `motors.py:318-516`; `hardware.yaml:110-133` |
| 16 | `pump.*` mapping: `Y,DETECT`, `Y,INIT`, `Y,SYR,<n>` + `?`, `Y,VALVE,<n>`, `Y,SPEED,<raw>`, `Y,ASPUL,<int>`, `Y,DISPUL,<int>`, `Y,STOP`; raw speed and step formulas | correct | `pump.py:20-123` |
| 17 | `scale.tare` → `W,TARE` + `W,CFG`; `scale.calibrate` → `W,CAL,<g>` + `W,CFG`; file written with `os.replace` | correct | `scale.py:53-128, 214-231` |
| 18 | `restore_calibration`: `W,DIV` first, `W,OFFSET` only when the saved offset is not 0 | correct | `scale.py:146-156` |
| 19 | `scale.modbus` waits the whole 3 s because the firmware answers `W,SET` with `CFG,` | correct | `scale.py:284`; `Arduino.ino:826-833` |
| 20 | State model, including int valve keys and `raw` kept as a string | correct | `transport.py:97-115, 246-313` |
| 21 | Twelve HTTP routes; error mapping 400 / 503 / 500 | correct | `main.py:99-223` |
| 22 | SSE: first event is a state snapshot; queue of 200, silently dropped when full; `: ping` after 15 s | correct | `main.py:174-201`; `transport.py:389-407` |
| 23 | Simulator differences (`Y,SYR` answers `PUMP,`; `W,CAL` leaves the divisor; no range checks; `HELP`/`RD`/`BAUD` missing) | correct | `simulator.py:377-451` |
| 24 | Third-party imports per module; `requirements.txt` content | correct | file heads; `requirements.txt:1-5` |
| 25 | `run.bat` regenerates `hw_config.h` on every launch (line 95) and starts `-m backend.main` (line 114) | correct | `run.bat:95, 114` |
| 26 | `default_flow_ml_min` is effectively required | correct | `config.py:210-211` |
| 27 | Saved calibration `offset −1.0`, `divisor 287703.03125`, `reference_grams 200.0`, saved 2026-08-26 | correct | `config/scale_calibration.json` |

### Frontend calls compared with this report

Everything `frontend/index.html` requests exists here: `GET /api/config` (FE:244), `GET /fn` (FE:266), `GET /api/state` (FE:269), `EventSource /api/stream` (FE:814), `POST /api/reconnect` (FE:873), link `/docs` (FE:183), and `POST /fn/<name>` for 22 of the 26 functions. **No endpoint or function is called that this report lacks.** Not called by the page: `motors.list`, `scale.set_stream`, `scale.diagnose`, `scale.modbus`, `GET /api/log`, `GET /api/docs`.

### Further additions (not in the text above)

- **Lost USB link while a pump runs.** A read error ends the reader thread and sets `connected = False` (`transport.py:207-211`). From then on `send()` raises `Not connected yet Arduino` at once (`transport.py:323-324`) — including the `V,<m>,0` in the weight-dose `finally` block and the `X` of `motors.stop_all`. The Mega keeps doing what it was doing. Nothing in the backend can stop it until `POST /api/reconnect` reopens the port.
- **No reply identifiers, so a late reply is taken for the next command's reply.** If a command times out with no line collected (`command timeout(<cmd>)`), its `OK…` arriving later ends whichever command is pending then (terminators match by prefix `OK`). From there every reply is attributed one command late until the stream of replies happens to realign. The backend has no resynchronisation step.
- **`PUMP,…,error=<n>` is not an error for the backend.** `pump.init`, `pump.aspirate`, `pump.dispense`, `pump.set_valve`, `pump.set_speed`, `pump.stop` return HTTP 200 with the line inside `result.lines` whatever `n` is (e.g. `error=7` The device is not initialized, `docs/wiring-pump.html:400`). A host must read `error=` and `ready=` itself.
- **`state.pump.online` is refreshed only by a `STATE` line.** `pump.detect`, `pump.init`, `pump.set_valve`, `pump.set_speed`, `pump.aspirate`, `pump.dispense`, `pump.stop` send no `?` afterwards, so the flag (and `pump.status.online`) stays as it was until the next `STATE` arrives for another reason. Only `pump.set_syringe` sends `?`.
- **`motors.dose_by_weight` does not look at any motor's state** before starting; it only refuses during cleaning and while another weight dose runs (`motors.py:324-339`). A continuous run started earlier on the other group keeps running through the dose (and its liquid lands on the same scale if it feeds the same vessel). A motor already running in the dose motor's own group is silently replaced by the first `V`. If the dose motor itself is already running, the baseline is taken while liquid is flowing and the first `V` only changes its speed.
- **The dose loop's own latency:** samples are polled every 10 ms (`motors.py:136`), the stop decision uses the median of the last 3 gains (`motors.py:386, 401`), and each `V` waits for its `OK` under `_tx_lock`. For motors 2 and 9 the firmware keeps the valve open 1 s after each stop and waits 1 s before each restart, including every top-up.
- **The 5 s settle after a top-up is `time.sleep(settle_s)`** (`motors.py:468`) with no cancel check; the first settle loop checks cancel every 20 ms (`motors.py:430-433`).
- **`Link.snapshot()` copies one level only** (`transport.py:377`): the per-group dicts inside `motors` are the live objects. State is written by the reader thread and read by request threads without a lock.
- **Weight hooks run on the reader thread** (`transport.py:265-269`); a slow hook delays every line.
- **`nan` / `inf` / `ovf` in a `WEIGHT` line:** `float("ovf")` raises and the line is dropped silently; `nan` and `inf` are stored in the state and rejected later by the dose loop's `math.isfinite` (`transport.py:250-253`; `motors.py:133-134`).
- **Two coefficients on purpose.** Speed uses `pulses_per_ml` 4237.00761337, dose length uses `dose_pulses_per_ml` 4558.96147256 (`hardware.yaml:90, 94`). A "40 mL/min" dose is 2825 steps/s, and 50 mL is 227948 steps, i.e. 80.69 s — a real delivery rate of about 37.2 mL/min if the dose coefficient is the true one. The code comments ask for exactly this ("Same cadence, same total number of steps and same rotation time If the request is not confirmed within", `hardware.yaml:105-107`); it is not a bug to fix during the merge.
- **`eta_s` is steps ÷ sps plus the two valve delays.** It ignores acceleration and the time the firmware spends in blocking serial work (see the firmware report §2.3 [INFER]); the real duration can only be longer.
- Rule slip by the cross-checker, same kind as the first reader's: a `Glob` for `*.{txt,bat,md}` on the V7 root returned file **paths** inside `.venv` (names of `dist-info` folders). No file in `.venv` was opened. The names confirm only that `pyserial 3.5` and `PyYAML 6.0.2` are installed in that environment.
