# HMI v1.1 — controller subsystem map

Subsystem: `hmi-controller`. Source folder: `C:/Users/PlasmaLab/Desktop/UI/UI/hmi_v1_1` (called HMI below).
All line numbers are `file:line` in that folder. Chinese texts are copied verbatim.

## 0. Scope, method, caveats

Read completely (every line): `controller.py` (2052 lines), `plc.py` (486), `scheduler.py` (131), `csv_workflow.py` (211),
`workflow_config.py` (42), `workflow_simulation.py` (95), `lite/index.html`, `lite/app.js`, `lite/workflow_template.csv`,
`web/workflow_template.csv`, `LITE_WORKFLOW.zh-TW.md`, `WORKFLOW_PLAN.zh-TW.md`.

Also read, because `controller.py` cannot be understood without them (they are probably another reader's primary files, so I only
cite what the controller depends on): `server.py` (whole), `backend.py` (whole), `calibration.py` (whole), `measurements.py` (whole),
`spectrum_result.py`, `spectrum_store.py`, `auth.py`, `calibration_config.py`, `remote_read.py`, `config.example.json`,
`ui_features.json`, `Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`, `Set-Admin-Password.cmd`, `README.md`,
`web/calibration/standard_round_template.csv`, `web/calibration/prime_template.csv`, and lines 1040–1069 of `web/index.html`
(the status label tables). Two greps into `rpi_tools/plasma_job.py` (lines 35, 395–396, 429, 497, 513–514, 522, 642).

Not done: nothing was run, no serial port, no network, nothing written inside the three project folders.
The `data` folder was never opened or listed; every persistence shape below is derived from the code that writes it.

Disclosure of two slips: one `Glob` and one `Grep` that I meant to be top-level recursed into `offline_bundle/` and returned
file names / a few matching lines of third-party Python packages (pip, cffi, invoke, cryptography, pycparser). Nothing from `data`,
no keys, no hashes. I did not use that output. After that I only used explicit file paths.

## 1. File map and entry points

| File | Role |
|---|---|
| `controller.py` | `IntegratedApplication`: the single owner of PLC, RPi link, CSV flows, schedules, calibration runs, measurements, operators, audit log. Also contains an older, smaller HTTP handler and `main()` (default port 8765). |
| `server.py` | The production entry point. `Application` wraps `IntegratedApplication` as `self.core`; `make_handler` exposes the full HTTP API. `Start.cmd` runs `server.py --port 8766`; `Start-Demo.cmd` runs `server.py --demo --port 8766`; `Start-Kiosk.cmd` runs `server.py --port 8766 --no-browser` then Edge `--kiosk http://127.0.0.1:8766/`; `Set-Admin-Password.cmd` runs `server.py --set-admin-password`. |
| `plc.py` | Modbus TCP client + `PLCMonitor` worker thread. The only code that talks to the PLC. |
| `csv_workflow.py` | CSV recipe language: parse, expand repeats, assign display phases, preflight. Imports `PUMP_COUNT`, `PUMP_TARGETS` from `plc.py` (`csv_workflow.py:12`). |
| `scheduler.py` | Pure time arithmetic + validation for interval schedules. No threads, no I/O. |
| `workflow_config.py` | Validation of the 7-field "Simple process" draft settings. Not imported by `controller.py` or `server.py`. |
| `workflow_simulation.py` | `WorkflowSimulation`: no-hardware countdown preview of the 12-phase simple workflow. Not imported by `controller.py` or `server.py`. |
| `lite/index.html`, `lite/app.js` | The earlier "HM Simple control" two-tab page (Manual / CSV Process). No current entry point serves `lite/` (see §10). |
| `lite/workflow_template.csv`, `web/workflow_template.csv` | Identical 28-line fill-in template (§3.6). `server.py:221-222` and `controller.py:1936-1938` both serve `web/workflow_template.csv` at `/workflow_template.csv`. |

`controller.py:1871-2010` (`make_handler`) and `controller.py:2013-2048` (`main`, "HM HMI v2", port 8765) are an older surface:
it serves `web/index.html`, `web/app.js`, `web/style.css` (`controller.py:1939-1945`) but `web/app.js` and `web/style.css` do not exist
in the tree (I listed `web/`: `index.html`, `shell.css`, `i18n.js`, sub-folders). Treat `server.py` as the real API.

## 2. Architecture (a)

### 2.1 Classes

| Class | Where | Purpose |
|---|---|---|
| `IntegratedApplication(demo=False, data_dir=None)` | `controller.py:117` | Everything. One instance per process. |
| `ScheduleEnded(Exception)` | `controller.py:67-71` | Internal: leaves the schedule loop with `.status` and message. |
| `ExclusiveHTTPServer(ThreadingHTTPServer)` | `controller.py:103-114` | `allow_reuse_address = False` + `SO_EXCLUSIVEADDRUSE` so a second HMI cannot bind the same port on Windows. |
| `PLCMonitor(config, reader=read_registers, writer=write_register, start=True)` | `plc.py:117` | Poller + serialized command queue + per-pump auto-off deadlines. `reader`/`writer` are injectable (this is the test seam and the natural re-mapping seam). |
| `Application` | `server.py:33` | Compatibility wrapper: `.core`, `.state()`, `.physical_start_blockers()`. |
| `SSHAdapter(config, password='')` | `backend.py:92` | Paramiko SSH to the RPi: `connect`, `close`, `_exec(args, source=None, timeout=20)`, `status()`, `capture(job_id)`. |
| `AdminAuth(path)` | `auth.py:33` | Maintenance PIN/password, sessions (§7.6). |
| `meas.Store(data_dir)` | `measurements.py:261` | Append-only `measurements.jsonl` + last 5000 in memory. |
| `WorkflowSimulation` | `workflow_simulation.py:33` | Unused by the controller. |

Module-level constants in `controller.py`:
- `ACTIVE_FLOW = ('running', 'pausing', 'paused', 'stopping')` (`:58`) — "A flow is in progress (PLC reserved, manual start locked)".
- `SCHEDULE_BUSY = ('waiting', 'running', 'stopping')` (`:60`) — schedule owns the equipment unless **paused**.
- `FLOW_ROW_KEYS = ('id','started_at','finished_at','status','mode','recipe_name','step_count','error','operator','schedule_id','slot','slots_planned','sample_kind','measurements')` (`:61-62`).
- `MAX_OPERATORS = 50` (`:63`), `SCHEDULE_START_FAILURES = 3` (`:64`).
- `now()` = `datetime.now(timezone.utc).isoformat()` (`:38-39`) — all stored timestamps are UTC ISO strings, except schedule slot times which are local-time ISO with offset (`scheduler.local_now()`, `scheduler.py:29-30`).
- `write_json(path, value)` (`:42-54`): write `<name>.tmp` then `replace`; retries `PermissionError` 10 × 0.1 s (antivirus/indexer on Windows, "Errno 13" seen on site).

### 2.2 Threads

| Thread | Created | Loop | Notes |
|---|---|---|---|
| HTTP server | `server.py:451` / `controller.py:2031` | `serve_forever`, one thread per request, `daemon_threads = True` | Binds `127.0.0.1` only. |
| `poll_thread` | `controller.py:207-208` | `_poll` (`:351-377`): every 5 s (`self.closed.wait(5)`), tries `remote_lock.acquire(blocking=False)`; if no adapter → `_auto_connect()`; else `adapter.status()` → `remote_status`. On exception clears `remote_status`, sets `remote_error`; if the SSH transport is dead closes the adapter and audits `rpi_link_lost`. | The interval is hard-coded 5 s; `config['poll_seconds']` is validated (`backend.py:32`) but not used by `controller.py`. |
| `PLCMonitor.thread` | `plc.py:134-136` (not started in demo: `controller.py:129` `start=not demo`) | `_work` (`plc.py:417-460`): `_auto_off()` → take one queued command and `_execute` it → else poll when due → sleep until next poll or nearest auto-off deadline or `wake`. | The only thread that touches the PLC socket. |
| `flow_thread` | `controller.py:887-888` | `_run(steps, uses_plc)` — one per run. | Daemon. |
| `schedule_thread` | `controller.py:1585-1586` | `_schedule_loop` — one per schedule. | Daemon. Starts each slot's flow through `self.start(...)` and joins `flow_thread`. |
| ad-hoc `_cancel_remote` | `controller.py:618, 688, 982, 1071` | Opens a **second** SSH session and sends `plasma_job.py cancel --outdir <dir>` (§5.6). | Daemon. |
| ad-hoc `_analyze_quietly` | `controller.py:1132, 1230` | Calibration analysis on the RPi. | Daemon. |

### 2.3 Locks and events

- `self.lock` (`RLock`, `:151`): all in-memory state (`flow`, `schedule`, `history`, `operator`, `remote_status`, …).
- `self.remote_lock` (`RLock`, `:152`): serializes every RPi SSH command. Poll uses non-blocking acquire; manual actions use non-blocking or 5 s timeout and answer `'RPi is executing the command'` / `'RPi The command is being executed, please try again later.'`; a flow step blocks (`with self.remote_lock`, `:934`).
- `self.analysis_lock` (`Lock`, `:144`): one calibration analysis at a time.
- `stop_event` (`:188`), `pause_event` (`:189`, "set = operator asked the flow to pause"), `prompt_event` (`:136`, "operator confirmed the bottle swap"), `schedule_stop`, `schedule_pause` (`:203-204`), `closed` (`:206`).
- `PLCMonitor.lock` (`RLock`), `wake`, `done`, `commands` (`queue.Queue`) (`plc.py:122-129`).

### 2.4 In-memory state of `IntegratedApplication` (`controller.py:118-208`)

`demo`, `data_dir` (`ROOT/'demo-data'` in demo, else `ROOT/'data'`, or the `data_dir` argument), `auth` (always `ROOT/data/admin.json` unless `data_dir` given, `:122`), `recipe_dir = data_dir/'recipes'`, `config` (RPi SSH), `plc_config`, `plc` (`PLCMonitor`), `calibration` (coefficients), `measure_store`, `frames_per_sum`, `blank_zero`, `_cal_context` (cache), `adapter` (`SSHAdapter` or `None`), `password` (in memory only), `remote_status`, `remote_error`, `remote_updated` (ISO), `remote_updated_mono`, `flow` (dict, starts `{'status': 'idle', 'events': []}`), `history` (`deque(maxlen=300)` of `FLOW_ROW_KEYS` rows), `latest_result_dir`, `latest_result_flow_id`, `latest_result_data`, `latest_raw_id`, `flow_thread`, `active_remote_dir` (RPi folder of the plasma job in progress, else `None`), `operator` (current name, `''` at start — not persisted), `auto_connect`, `link_retry_at`, `link_retry_delay` (5 → 60 s back-off), `schedule` (dict or `None`), `schedule_csv`, `schedule_blank_csv` (CSV text in memory only), `schedule_history` (list ≤ 20), `schedule_thread`.

Start-up recovery in `__init__`:
- Calibration records with status `running`/`analyzing` become `interrupted`, error `'HMI Closed during calibration execution or analysis'` (`:145-150`).
- The newest 300 `HMI-FLOW-*.json` by mtime are read; only records with status `completed`/`failed`/`stopped` are kept (`:160-182`). The newest one with an `events` list becomes `self.flow` (so the screen shows the last run after a restart).
- `_load_schedules()` (`:1448-1472`) — see §6.6.

### 2.5 Persistence files (all under `data_dir`; shapes from the writing code)

| File | Written by | Shape |
|---|---|---|
| `config.json` | `server.py:303`, `controller.py:493` | Keys of `config.example.json`: `host` ("192.168.1.1"), `port` (22), `username` ("pi"), `key_file` (""), `known_hosts`, `app_dir` ("/home/pi/Desktop/Plasmic/app"), `tools_dir` ("/home/pi/Desktop/Plasmic/nano_lcd_v1_1/rpi_tools"), `serial_port` ("/dev/RS485"), `output_dir` ("/home/pi/Desktop/Plasmic/logs/hmi-captures"), `poll_seconds` (5). Validation `backend.py:16-34`. |
| `plc_config.json` | `server.py:291` | `{'host','port','unit_id','poll_seconds'}`; defaults `{'host': '192.168.0.1', 'port': 502, 'unit_id': 1, 'poll_seconds': 5}` (`plc.py:13`). |
| `calibration_config.json` | `server.py:347`, `controller.py:1320` | `{'Cu': {'a','b','c'}, 'Zn': {...}, 'Ni': {...}}`, each a decimal string or `null`. If the file is absent the controller uses `a='1', b='0', c='1'` (`controller.py:131-134`). |
| `calibration_active.json` | `controller.py:1321-1323` | `{'calibration_id','applied_at','metals':[...],'method_id','unit':'mg/L','previous':{old coefficients}}`. |
| `calibration_plan.json` | `controller.py:1160` | Draft plan as entered: `{'recipe_id','prime_recipe_id','discard_rounds','levels':[{'name','conc':{'Cu','Zn','Ni'},'rounds'}]}` (default `calibration.py:36-40`). |
| `calibrations/CAL-<32hex>.json` | `calibration.py:295-314` | `{'id','created_at','status','operator','plan','recipe':{'id','name','sha256'},'prime_recipe':{...}|null,'warnings':[],'runs':[{'level','level_name','run','discard','round','job','finished_at'}],'flow_id','frames_per_sum','finished_at','error','reference':{'axis','values','blank_count','jobs'},'analysis':{'analyzed_at','method_id','frames_per_sum','config','config_path','jobs','fits'},'exclude_levels','applied_at','applied_metals','previous_config'}`. |
| `measurements.jsonl` | `measurements.py:275-280` | One JSON per line: `{'id','time','flow_id','schedule_id','slot','round','recipe_name','kind':'sample'|'blank','job','method_id','valid','error','shift_nm','metals':{'Cu':{'intensity','conc','norm_area'?,'conc_raw'?,'zero'?,'norm_change_percent'?},...},'calibration_id','conc_reason','qc'?:{'status':'ok'|'fail'|'unchecked','problems':[],'reason'},'frames_per_sum','blank_zero'?}`. |
| `frame_sum.json` | `controller.py:1436` | `{'frames_per_sum': 1..50, 'updated_at'}`. |
| `blank_zero.json` | `controller.py:1429` | `{'enabled':{'Cu':bool,'Zn':bool,'Ni':bool},'window':3..30,'min_blanks':1..30,'mad_reject':bool}`; default `{'enabled': {'Cu': False, 'Zn': False, 'Ni': True}, 'window': 10, 'min_blanks': 3, 'mad_reject': True}` (`measurements.py:35-36`). |
| `blank_qc.json` | `controller.py:1443` | `{'max_shift_nm','max_apparent_conc':{'Cu','Zn','Ni'},'max_norm_change_percent'}`; default `0.05`, `0.05` each, `20` (`measurements.py:32-33`). |
| `recipes/<32hex>.json` | `controller.py:335-349` | `{'id','name','created_at','csv','sha256','step_count','uses_rpi','uses_plasma'}`. `sha256` = SHA-256 of the CSV text; re-checked on every load (`:316-317`, `'Recipe File integrity verification failed'`). |
| `recipes/deleted/<id>.deleted-<YYYYmmdd-HHMMSS>.json` | `controller.py:328-331` | A deleted recipe is moved, not erased. |
| `HMI-FLOW-<32hex>.json` | `controller.py:1007-1012` | One per finished run — see §8. |
| `CAP-<32hex>.json` | `controller.py:726-732` | Manual raw capture: `{'id','created_at','remote_dir','integration_ms','model','frames':[{'file':'spectrum-N.csv','points':[[wavelength_nm, intensity_raw],...]}]}` — exactly 3 frames (`:706`). |
| `SCHEDULE-<32hex>.json` | `controller.py:1474-1480` | See §6.4. |
| `audit.jsonl` | `controller.py:215-221` | One JSON per line: `{'time','action','detail' (≤300 chars),'actor'}`. `events()` returns the last 200 lines, newest first (`:223-229`). |
| `operators.json` | `controller.py:289` | `{'operators': ['name', ...]}`; read with `utf-8-sig` (`:275`). |
| `admin.json` | `auth.py:59-70` | `{'version': 1, 'salt': hex, 'digest': hex, 'iterations': 600000, 'kind': 'pin'|'password'}` (PBKDF2-HMAC-SHA256). Not opened by me. |
| `known_hosts`, client key | `controller.py:476-478` via `rpi_pairing.save_known_host` / `ensure_client_key` | SSH pairing material. README calls the key `data/rpi_key`. Not opened by me; `rpi_pairing.py` not read. |
| `spectra/SPEC-<32hex>.json` + `.meta.json` | `spectrum_store.py:50-73` | Full set: `SUMMARY_KEYS` (`'id','captured_at','saved_at','source','flow_id','recipe_name','round','rounds','remote_dir','model','integration_ms','background_frames','signal_frames','loop_aligned','has_frames'`) + `'axis','mean_difference','frame_differences','background_mean','signal_mean','metals'`. `source` ∈ `'flow'`, `'manual'`, `'rpi_import'`. |
| `logs/ssh.log` | `server.py:415-425` | Paramiko warnings, rotating 1 MB × 3. |

### 2.6 `state()` returned by the core (`controller.py:503-526`)

`{'demo', 'plc': plc.state(), 'rpi_connected', 'rpi_fresh' (status age ≤ 20 s), 'rpi_status' (deep copy when fresh, else None), 'rpi_error', 'rpi_updated', 'flow' (deep copy), 'calibration_complete', 'formal_execution_available': True, 'plasma_production_available': True, 'calibration_provisional', 'calibration_active', 'frames_per_sum', 'latest_result_job', 'latest_result_flow_id', 'latest_raw_id', 'schedule': schedule_view(), 'measurement': {'latest_sample', 'latest_blank'}, 'operator', 'rpi_link': {'paired', 'auto_connect', 'host'}}`.

`server.py:46-70` reshapes it for the web page and adds: `connected`, `quality` (`'good'`/`'unknown'`), `updated_at`, `age_seconds`, `snapshot`, `pending: None`, `error`, `events` (last 50 audit rows as `'[actor] action detail'`), `job`, `workflow` and `flow` (same object), `flow_history` (30 rows), `can_capture`, `manual_interlock`, `plasma_ready_problem`, `plasma_running` (`active_remote_dir is not None`), `operators`.

### 2.7 HTTP endpoint → core method (from `server.py:115-411`)

Every request must have `Host` `127.0.0.1:<port>` or `localhost:<port>`. POST also needs header `X-HMI-Token` (random per start, given by `GET /api/bootstrap`), `Origin` absent or same origin, `Content-Type: application/json`, body 1 byte–220 KB JSON object. Errors: `ValueError` → 400, `RuntimeError`/`TimeoutError` → 409, `OSError` → 500, all as `{'error': text}`.

GET: `/api/bootstrap` (`token`, `config`, `demo`, `plc_config`, `test_page_enabled`, `admin_configured`, `admin_kind`), `/api/auth`, `/api/state`, `/api/workflow-state`, `/api/calibration-config`, `/api/calibration`, `/api/measurements?hours=N`, `/api/calibrations/<id>`, `/api/recipes`, `/api/recipes/<id>` (adds `phases`, `rounds`), `/api/events`, `/api/run-log`, `/api/schedule`, `/api/operators`, `/api/result/latest`, `/api/spectra`, `/api/spectra/<id>`, `/api/rpi-jobs` (these three admin-only), `/api/rpi-tools` (admin-only), `/api/history`, `/api/captures/<id>[/csv/<1-3>]` (test page only), `/workflow_template.csv`, static pages `/`, `/workflow/`, `/test/`, `/calibration/`, `/trend/`, `/custom/...`.

POST (★ = maintenance session required, `server.py:278-285`):

| Path | Core call |
|---|---|
| `/api/admin/login` `{pin}` or `{password}` | `auth.login` → `{'session','expires_in'}`; audit `maintenance_login` |
| `/api/admin/logout` | `auth.logout` |
| `/api/plc-manual`, `/api/plc` | `core.manual_plc(body)`; audit `plc_manual` |
| ★ `/api/plc-config` | `validate_plc_config` → write `plc_config.json` → `plc.configure` |
| `/api/plc-refresh` | `plc.refresh()` |
| ★ `/api/connect` `{password?, config?}` | `core.connect` |
| ★ `/api/disconnect` | `core.disconnect` |
| ★ `/api/rpi/hostkey`, ★ `/api/rpi/pair` `{password, fingerprint}` | `rpi_host_key`, `pair_rpi` |
| ★ `/api/operators` `{operators:[...]}` | `set_operators` |
| `/api/operator` `{name}` | `select_operator` |
| `/api/schedule/preview` `{recipe_id, schedule, blank}` | `schedule_preview` |
| `/api/schedule/start` | `start_schedule(body)` |
| `/api/schedule/pause`, `/resume`, `/stop` | `pause_schedule`, `resume_schedule`, `stop_schedule` |
| ★ `/api/refresh` | immediate `adapter.status()` |
| ★ `/api/capture` | `manual_rpi('spectrum')` |
| `/api/rpi` `{action}` | `status`, `stop` open to operators; ★ `prepare`, `spectrum`, `clear_fault`; ★ `plasma_run` → `manual_plasma(confirm_high_voltage or confirm_hv_load_disconnected)` |
| ★ `/api/parameters` | `save_parameters(body)` |
| ★ `/api/calibration-config` | write coefficients |
| ★ `/api/blank-zero`, ★ `/api/frame-sum`, ★ `/api/blank-qc` | `set_blank_zero`, `set_frame_sum`, `set_blank_qc` |
| `/api/calibration/plan`, `/check`, `/start`, `/confirm`, `/analyze`, `/exclude` | `save_calibration_plan`, `check_calibration_plan(...)[4]`, `start_calibration(plan, confirm_high_voltage)`, `confirm_prompt`, `request_analysis`, `set_calibration_exclusions` |
| ★ `/api/calibration/apply` | `apply_calibration` |
| `/api/validate` `{csv}` | `validate` (steps removed from the reply) |
| ★ `/api/recipes/delete` `{id, pin}` | PIN verified again (`auth.verify`) then `delete_recipe` |
| ★ `/api/recipes/publish` `{name, csv}` | `publish_recipe` |
| ★ `/api/recipes/run` `{id, confirm_high_voltage|isolated}` | `core.start(recipe['csv'], hv, recipe['name'])` |
| ★ `/api/start` `{csv, confirm_high_voltage|isolated}` | `core.start(csv, hv)` |
| ★ `/api/rpi-tools/deploy`, ★ `/api/spectra/import` | `deploy_rpi_tools`, `import_spectrum` |
| `/api/workflow-pause`, `/api/workflow-resume` | `pause`, `resume` |
| `/api/stop`, `/api/workflow-stop` | `stop` |

Operator comment in the code (`server.py:276-277`): "Operators need no sign-in: schedules, pause/stop, manual pumps, HSPY OFF, operator selection. Everything that changes how the instrument measures belongs to maintenance (PIN)."

## 3. Workflow / recipe model (b)

### 3.1 What a recipe is

A recipe is a CSV text published under a name (`publish_recipe`, `controller.py:335-349`; name 1–60 characters, `'Recipe The name must be 1–60 characters'`). It is validated on publish and again on every start. A run executes the expanded step list top to bottom on one thread. "Recipe only describes one round of measurement, and the schedule is responsible for the repetition." (README). There is no branching, no variables, no sensor-conditioned step: every liquid quantity is "pump X for N seconds" or "syringe N mL".

### 3.2 CSV format (`csv_workflow.py`)

- Header must be exactly `action,target,value,unit,note` (`HEADERS`, `:14`; error `'CSV field must be action,target,value,unit,note'`).
- Text ≤ 200 000 bytes UTF-8 (`'CSV must be 200 KB Within UTF-8 text'`); a leading BOM is stripped (`:38-40`).
- `MAX_ROWS = 500` source rows (blank rows count; `'CSV most 500 List'`), `MAX_STEPS = 2000` after expansion (`'Maximum after expansion 2000 action'`), `MAX_NESTING = 5` (`'repeat Most nested 5 layer'`), `MAX_REPEAT = 20`.
- Wrong column count → `'No. N Incorrect number of columns'`. Every field is stripped. A completely empty row is skipped. `note` is cut to 200 characters. `line` = CSV row number (header is row 1).
- Unknown action → `'No. N Column does not support command 'x''`. No valid step → `'CSV No executable action'`. Unclosed block → `'repeat_start Lack repeat_end'`.

`ACTIONS` (`:19-22`) plus the two block markers:

| action | target | value | unit | Validation (error text) |
|---|---|---|---|---|
| `delay` | empty | 0–3600, step 0.1 | `s` | `'No. N List delay must use empty target and s unit'`; `'delay Number of seconds must be between 0–3600'`; `'delay Seconds, the minimum unit is .1'` |
| `pump_on` | `P1`–`P6` | 1–120, step 0.1 = **maximum** run seconds (software auto-off) | `s` | `'No. N column pump shall be P1–P6'`; `'No. N List pump_on of value Must be the longest running seconds'`; `'Maximum running seconds of the pump …'` |
| `pump_off` | `P1`–`P6` | empty | empty | `'No. N List pump_off Leave blank value/unit'` |
| `pump_direction` | `CW` / `CCW` | empty | empty | `'No. N List pump_direction of target Must be CW/CCW,Leave blank value/unit'` |
| `pump_speed` | `CW` / `CCW` | 1–1000, step 1 | `rpm` | `'No. N List pump_speed Must use CW/CCW and rpm'`; `'pump speed …'` |
| `syringe_aspirate` | `CH1`–`CH3` | 0.01–5, step 0.01 | `mL` | `'No. N series syringe pump must be used CH1–CH3 and mL'`; `'Syringe pump volume …'` |
| `syringe_dispense` | `CH1`–`CH3` | 0.01–5, step 0.01 | `mL` | same |
| `rpi_status` | empty | empty | empty | `'No. N List {action} Leave blank target/value/unit'` |
| `nano_prepare` | empty | empty | empty | same |
| `plasma_run` | empty | empty | empty | same |
| `spectrum_capture` | empty | empty | empty | same |
| `nano_stop` | empty | empty | empty | same |
| `power_off` | empty | empty | empty | same |
| `repeat_start` | empty | integer 1–20 | empty | `'No. N List repeat_start of value Must be 1–20 Second-rate'` |
| `repeat_end` | empty | empty | empty | `'No. N List repeat_end has no corresponding block or contains redundant values.'` |

A step that is not a CSV action exists only in generated calibration sequences: `operator_prompt` (`calibration.py:158-161`), handled at `controller.py:916-917`.

Expanded step dict: `{'action','target','value' (string),'unit','note','line','round','rounds','phase'}` (+ `'calibration': {...}` in calibration runs). "Only the outermost repeat counts as a displayed round" (`csv_workflow.py:108-112`): steps inside the outermost `repeat_start` get `(index+1, count)`, steps outside any repeat get `(1, 1)`.

Small inconsistency: `pump_speed` passes CSV validation with `60.0` (Decimal modulo 1) but `plc.flow_step` requires `value.isdigit()` (`plc.py:251`), so `60.0` fails at run time with `'CSV pump_speed Must be specified CW/CCW and 1–1000 RPM'`.

### 3.3 Preflight (`preflight_workflow`, `csv_workflow.py:170-211`) — structural rules checked before any hardware

1. `pump_direction` / `pump_speed` while any pump is on → `'No. N Column execution {action} All must be stopped before PLC pump'`.
2. `pump_on` needs an earlier `pump_direction` **and** a `pump_speed` for that same direction in the same CSV → `'No. N List {target} must be set before starting pump_direction with that direction pump_speed'`.
3. `pump_on` of a pump already on → `'No. N List {target} is already running'`; `pump_off` of a pump not on → `'No. N List {target} has not been started yet'`.
4. Syringe steps and all six RPi steps while any pump is on → `'No. N Column execution {action} All must be stopped before PLC pump'`. (So pumps never run during a syringe move or a discharge. Several pumps may run together.)
5. For each `delay`, the accumulated delay since a pump was switched on must not exceed that pump's max seconds → `'No. N Column wait will exceed {pump} The maximum running time set'`. Only `delay` time is counted.
6. End of flow with a pump still on → `'There are still pumps not queued at the end of the process. pump_off: P…'`.
7. Sum of all delays > 7200 s → `'single expansion process delay The total length shall not exceed 7200 Second'`.

Returns `{'steps','step_count','delay_seconds' (string),'phases','rounds','uses_plasma','uses_rpi'}`. `uses_plasma` = any `plasma_run`; `uses_rpi` = any of the six RPi actions.

### 3.4 Display phases (`assign_phases`, `csv_workflow.py:123-162`) — display only

`PUMP_PHASES = {'P1': 'sampling', 'P2': 'Sample delivery', 'P3': 'pure water', 'P4': 'Submit for inspection', 'P5': 'Waste discharge', 'P6': 'standard product'}`; `SYRINGE_PHASES = {'CH2': 'add acid', 'CH3': 'air push'}`.
Rules: `pump_speed`/`pump_direction` → `Prepare`; `pump_on` → the pump's phase, except `P4` while `P5` is on → `Waste discharge`; `pump_off` → the phase that pump had; `syringe_aspirate` → CH phase (CH1 keeps the previous phase); `syringe_dispense` → the phase of the last aspirate; any RPi action → `Measure`; a `delay` while pumps run → `Waste discharge` if any running pump is in Waste discharge else the last started pump's phase; `delay` after a dispense → `Mix`; `delay` after a `pump_off` → `standby`; initial phase `Prepare`. `phase_order` gives the distinct phases in first-seen order for the step bar.

Pump names shown on the main page (`web/index.html:1044-1049`): P1 `sampling circulation pump` (40008), P2 `Sample sent to mixing tank` (40009), P3 `pure water pump` (40010), P4 `Send detector` (40011), P5 `waste pump` (40012), P6 `standard pump` (40013); syringe `CHANNELS = {1: 'Drainage', 2: 'Nitric acid', 3: 'Air'}`.

### 3.5 The intended measurement sequence (from the templates and `WORKFLOW_PLAN.zh-TW.md`)

pure water (P3 for N s) → Nitric acid (CH2 aspirate X mL, CH1 dispense X mL) → air push tube (CH3 aspirate 5 mL, CH1 dispense 5 mL) → Mix (delay only; "The valve and agitator are not installed. The mixing step is currently only timed, not written. `40028`") → Submit for inspection (P4 for N s) → `rpi_status` → `nano_prepare` → `plasma_run` → Empty (P5 on, delay, P4 on, delay, P4 off, P5 off) → standby (delay).
For standards the sample pump is replaced by P6 (`web/calibration/standard_round_template.csv:11`, "P6 Standard product (replacing the sample P3) "). Example values in that template: 200 rpm CW; drain P5 20 s then P4+P5 40 s; P6 40 s; acid 1 mL; air 5 mL; mix 30 s; P4 50 s; then measure; drain again.

### 3.6 `workflow_template.csv` (both copies identical; 27 data rows, values left blank on purpose)

```
action,target,value,unit,note
pump_speed,CW,,rpm,Fill in the shared forward speed 1–1000 RPM; If you want to reverse, please change to CCW
pump_direction,CW,,,clearly specifies the shared direction; if you need to reverse it, please change it to CCW
repeat_start,,1,,outer round; modify value controls consecutive rounds
pump_on,P3,,s,Fill in P3 The longest running seconds must be greater than the next queue waiting time
delay,,,s,Enter the pure water running seconds
pump_off,P3,,,
syringe_aspirate,CH2,,mL,Fill in the volume of nitric acid, up to 5 mL
syringe_dispense,CH1,,mL,Fill in the same volume as the previous column
repeat_start,,1,,Number of inner air pushes; default 1 Second-rate
syringe_aspirate,CH3,5,mL,pump air 5 mL
syringe_dispense,CH1,5,mL,is pushed from the drainage channel to the mixing tank
repeat_end,,,,
delay,,,s,Fill in the mixing waiting seconds
pump_on,P4,,s,Fill in P4 Maximum running seconds
delay,,,s,Enter the seconds for inspection
pump_off,P4,,,
rpi_status,,,,read HSPY and Nano
nano_prepare,,,,synchronous parameter.json,LCD Displays the applied parameters
plasma_run,,,,Isolation Load Test: Pulse and Background/signal raw spectrum
pump_on,P5,,s,Fill in P5 Total operation upper limit
delay,,,s,Fill in P5 lookahead seconds
pump_on,P4,,s,Fill in P4 Same opening limit
delay,,,s,Fill in P4＋P5 Same as opening seconds
pump_off,P4,,,
pump_off,P5,,,
delay,,,s,Fill in the waiting seconds between rounds
repeat_end,,,,
```

`web/calibration/prime_template.csv` (Fluid replacement and draining): 200 rpm CW; P6 on (max 20 s), delay 10 s, P6 off; P5 on, delay 20, P4 on, delay 40, P4 off, P5 off, delay 10.

### 3.7 `workflow_config.py` and `workflow_simulation.py` (the earlier fixed "Simple process"; not wired into the controller)

`FIELDS` (`workflow_config.py:9-17`): `p3_seconds` (`'pure water P3 Running seconds'`, 1–3600), `acid_ml` (`'Amount of nitric acid mL'`, 0.01–5.00, step 0.01), `mix_seconds` (`'Mixing seconds'`, 0–3600), `p4_seconds` (`'Submit for inspection P4 Running seconds'`, 1–3600), `p5_lead_seconds` (`'P5 lookahead seconds'`, 0–3600), `p4_p5_overlap_seconds` (`'P4/P5 Same as opening seconds'`, 1–3600), `standby_seconds` (`'Waiting seconds between rounds'`, 0–86400). All default `None`; "An incomplete draft is allowed; it cannot start a run." Errors: `'Simple process setting field is incorrect'`, `'{label}Must be a number'`, `'{label}Need to be 0.01–5.00,smallest unit 0.01 mL'`, `'{label}Need to be {min}–{max} integer'`.

`workflow_simulation.phases(config)` (`:9-26`) — 12 labels: `'Precheck (simulation)'` 1 s, `'P3 pure water (simulation)'`, `'CH2 Pumping nitric acid X.XX mL (simulation)'` 1 s, `'CH1 Exclude nitric acid X.XX mL (simulation)'` 1 s, `'CH3 pump air 5.00 mL (simulation)'` 1 s, `'CH1 Exhaust air 5.00 mL (simulation)'` 1 s, `'Mixed waiting (simulation)'`, `'P4 Submit for inspection (simulation) '`, `'RPi pulse/spectrum/conversion (simulation only)'` 1 s, `'P5 Empty first (simulation)'`, `'P4＋P5 Simultaneous opening and emptying (simulation)'`, `'Standby between rounds (simulation)'`. State: `{'status','mode','cycle','phase','remaining_seconds','started_at','finished_at','simulation': True}`; status `idle`/`running`/`stopping`/`stopped`/`completed`/`failed`; mode `single`/`continuous`. Errors: `'Please fill in and save all seven settings first'`, `'mode can only be single round or continuous'`, `'The simulation process is already being executed.'`, `'There is currently no simulation process being executed.'`.

## 4. PLC — everything that must be re-mapped to the Arduino Mega (c)

### 4.1 Protocol (`plc.py`)

- Modbus TCP, default `192.168.0.1:502`, unit id 1, `poll_seconds` 5 (validated 2–60; port 1–65535; unit 1–247; `'PLC IP must be IPv4 address'`, `'PLC {key} Must be between {min}–{max}'`, `'PLC The connection setting field is incorrect'`).
- A **new TCP connection per request**, `timeout=2` (`:55-56`, `:74-75`).
- Read: FC03, start offset 0, `REGISTER_COUNT = 30` holding registers (40001–40030), transaction id 1 (`read_registers`, `:51-67`). Rejects bad header (`'PLC Modbus Incorrect reply header'`), exception `0x83` (`'PLC Modbus Read exception code N'`), wrong length (`'PLC Modbus Data length is incorrect Starting from'`), early close (`'PLC Close connection before reply completes'`).
- Write: FC06 single register, transaction id 2, strict echo check (`write_register`, `:70-85`; `'PLC Incorrect write reply header'`, `'PLC Modbus write exception code N'`, `'PLC The write reply is inconsistent with the command'`).
- `_checked_write(config, offset, value)` (`:293-305`): FC06, then a full FC03 read, compare; mismatch → `'PLC {40001+offset} Read back after writing {actual},expected {value}'`; on success the snapshot is refreshed.
- All registers are **command read-back only**: `'feedback_valid': False`, `'feedback_unavailable': '40042–40044'` (`:108-109`). LITE doc: "PLC The instruction register only provides command readback and does not provide actual operation feedback.". No flow, weight, level, or motor feedback anywhere.

### 4.2 Register map

| Offset | Register | Meaning | Values |
|---|---|---|---|
| 0 | 40001 | syringe `reset` trigger | pulse 0→1→0 |
| 1 | 40002 | syringe `channel_1` trigger | pulse |
| 2 | 40003 | syringe `channel_2` trigger | pulse |
| 3 | 40004 | syringe `channel_3` trigger | pulse |
| 4 | 40005 | syringe `aspirate` trigger | pulse |
| 5 | 40006 | syringe `dispense` trigger | pulse |
| 6 | 40007 | syringe volume | raw = mL × 100 (unit 0.01 mL), 1–500 |
| 7–12 | 40008–40013 | P1–P6 run command (`PUMP_ADDRESSES = {str(i): i + 6}`) | 0 = OFF, 1 = ON |
| 26 | 40027 | shared pump direction | 0 = CW (Forward rotation), 1 = CCW (reverse), other = `UNKNOWN` |
| 28 | 40029 | shared CW speed | RPM, 1–1000 |
| 29 | 40030 | shared CCW speed | RPM, 1–1000 |
| 27 | 40028 | named only in `WORKFLOW_PLAN.zh-TW.md` as the (not installed) stirrer; never read or written by code | — |

Constants: `PUMP_COUNT = 6` ("P1–P5 process pumps, P6 calibration standard (40013)"), `PUMP_TARGETS = ('P1',…,'P6')`, `PUMP_RUN_LIMIT_SECONDS = 30` (manual default), `MANUAL_RUN_MAX_SECONDS = 120`, `SYRINGE_MAX_ML = Decimal('5.00')`, `SYRINGE_PULSE_SECONDS = 1.0`, `SYRINGE_ADDRESSES` as in the table.

Key property: **direction and speed are shared by all pumps** (one direction register, one RPM per direction). "Use the same direction and speed when multiple units are started at the same time" (`lite/index.html:13`).

### 4.3 Snapshot and `PLCMonitor.state()` (`plc.py:98-114`, `:462-472`)

`decode_registers` → `{'register_range': '40001–40030', 'pumps': {'1'..'6': {'command': bool, 'raw': int, 'register': int}}, 'syringe': {'triggers': {name: {'raw','register'}}, 'volume_ml', 'volume_raw', 'volume_register': 40007, 'feedback_valid': False}, 'feedback_unavailable': '40042–40044', 'pump_control': {'valid': True, 'direction': 'CW'|'CCW'|'UNKNOWN', 'direction_raw', 'rpm_cw', 'rpm_ccw', 'direction_register': 40027, 'rpm_cw_register': 40029, 'rpm_ccw_register': 40030}}`.

`state()` → `{'connected': valid, 'valid': valid, 'updated_at', 'age_seconds', 'error', 'config', 'snapshot' (None when stale), 'pending', 'last_command', 'auto_off_seconds': {pump: seconds left}}`. **Valid** = a snapshot exists and its age ≤ `max(15, poll_seconds * 3)` seconds. A failed poll clears the snapshot at once (`poll_once`, `:398-415`).

`last_command` = `{'action','ok','error'?,'elapsed_ms','queue_ms','pump'?,'pumps'?,'direction'?,'rpm'?,'time'}` or the auto-off form `{'action': 'auto_off', 'pump', 'ok', 'error'?, 'time'}`.

### 4.4 Each action: parameters, sequence, timing

| Action | Sequence in `_execute` (`plc.py:317-378`) | Timing |
|---|---|---|
| `pump_direction` (`direction` CW/CCW) | fresh read; any pump register non-zero → `'Before changing the shared direction,P1–P6 All required OFF'`; `_checked_write(26, 0 or 1)` | 1 read + 1 write + 1 read |
| `pump_speed` (`direction`, `rpm` 1–1000) | fresh read; any pump on → `'Before changing the common speed,P1–P6 All required OFF'`; `_checked_write(28 or 29, rpm)` | same |
| `pump_on` / `pump_on_many` | for each pump: `_checked_write(addr, 1)`; then `owned_pumps[pump] = now + max_seconds` (CSV value, or manual `seconds`, else 30) | per pump 1 write + 1 read |
| start failure | every pump of the command is written 0; a failed OFF keeps the pump in `owned_pumps` with deadline now + 2 s and raises `'The pump failed to start and the stop command failed:…'` | |
| `pump_off` / `pump_off_many` | for each pump `_checked_write(addr, 0)`; success removes it from `owned_pumps`; failure → deadline now + 2 s; single → re-raise the first error; many → `'Pump stop not confirmed:P…'` | |
| `aspirate` / `dispense` (`channel` 1–3, `volume_raw`) | (1) `_checked_write(6, volume_raw)`; (2) `_pulse(channel_N)`; (3) `_pulse(aspirate or dispense)` | 2 × 1.0 s pulse hold + 12 Modbus transactions (2 for the volume, 5 per pulse) |
| `syringe_reset` | `_pulse(reset)` | 1.0 s + 5 transactions |
| `_pulse(offset)` (`:307-315`) | `_checked_write(offset, 0)` → `writer(offset, 1)` (echo only) → `sleep(1.0)` → `finally: _checked_write(offset, 0)` | "Confirm LOW before the edge and after the pulse." |
| auto-off (`_auto_off`, `:380-396`) | every loop pass: each pump whose deadline passed gets `_checked_write(addr, 0)`; failure sets `error = 'P{n} automatic stop failed:{exc}; Please use physical stopping device'` and retries every 2 s forever | |

The LITE doc measured one read-only round trip at about 0.54 s ("PLC Read-only readback measurement multiple appointments 0.54 Second"). `scheduler.estimate_run_seconds` budgets 12 s per syringe step and 1 s per pump/direction/speed step (`scheduler.py:119-121`).

The syringe step returns when the two trigger pulses are done. **The code has no information about when the mechanical stroke finishes** (no feedback register). Whether the PLC itself blocks/queues is not visible here.

Volume: `syringe_volume_raw` (`:88-95`): 0.01–5.00 mL, multiples of 0.01; errors `'Syringe pump volume must be a number'`, `'The test volume of the syringe pump needs to be 0.01–5.00 mL,smallest unit 0.01 mL'`.

### 4.5 Two command entry points

**Manual — `PLCMonitor.submit(payload)` (`plc.py:151-224`), called by `controller.manual_plc` (`controller.py:576-588`).** Payloads:
- `{'action': 'pump_on', 'pump': 1..6, 'seconds'?: int 1..120}` — `'Only supports P1–P6'`, `'The number of seconds to automatically stop must be 1–120 integer'`
- `{'action': 'pump_off', 'pump': n}`
- `{'action': 'pump_on_many' | 'pump_off_many', 'pumps': [unique ints]}` — `'Please select unique ones P1–P6 pump'`
- `{'action': 'pump_direction', 'direction': 'CW'|'CCW'}` — `'direction must be CW or CCW'`
- `{'action': 'pump_speed', 'direction', 'rpm': int 1..1000}` — `'The speed must be 1–1000 RPM,The direction is CW or CCW'`
- `{'action': 'aspirate' | 'dispense', 'channel': 1..3, 'volume_ml'}` — `'Syringe pump channel must be CH1–CH3'`
- `{'action': 'syringe_reset'}` — `'Reset command format is incorrect'`
- else `'Not supported PLC manual command'`; non-dict `'PLC command format is incorrect'`.

Returns `{'accepted': True}` immediately (asynchronous; the page watches `pending` / `last_command`).

**Flow — `PLCMonitor.flow_step(action, target='', value='')` (`plc.py:236-291`), called by `_run` and `_pause_point`.** Synchronous: queues the command, waits up to **25 s** (`'PLC command exceeds 25 Second; Please check the physical device'`), raises `RuntimeError(result['error'])` on failure. Accepts `pump_on` (`P1`–`P6`, float 1–120), `pump_off`, `pump_direction` (target CW/CCW), `pump_speed` (target CW/CCW, digit string 1–1000), `syringe_aspirate`/`syringe_dispense` (`CH1`–`CH3`, volume). Errors: `'CSV pump only supports P1–P6'`, `'CSV The upper limit of the pump is 1–120 Second'`, `'CSV pump_direction of target Must be CW or CCW'`, `'CSV pump_speed Must be specified CW/CCW and 1–1000 RPM'`, `'CSV syringe pump only supports CH1–CH3'`, `'Not supported CSV PLC action'`. There is no `syringe_reset` in the CSV language.

### 4.6 Interlocks

In `submit` (manual), in this order (`plc.py:194-219`):
1. Flow has reserved the PLC and the action is not an OFF → `'CSV Process execution, manual start is locked'`.
2. Another command pending → `'PLC command is being executed, please wait.'`.
3. Not an OFF and no fresh snapshot → `'PLC status has not been obtained and the device cannot be started.'`.
4. Pump ON: a selected pump's read-back is not 0 → `'The selected pump command value is not OFF,Please confirm the device status first'`; direction read-back invalid/unknown → `'The pump direction and speed have not yet obtained valid readback.'`; RPM of the current direction not 1–1000 → `'The rotational speed in the current direction must be 1–1000 RPM,Please set it first'`.
5. Direction/speed: read-back invalid → `'The pump direction and speed have not yet obtained valid readback.'`; any pump owned or on → `'All pumps must be stopped before changing the common direction or speed.'`.
6. Syringe actions while any pump is owned → `'There is already a pump test running, please stop it first.'`.
OFF commands skip checks 1, 3–6 ("OFF Always available").

In `flow_step` (`plc.py:263-282`): `'CSV process not obtained PLC control'`; `'PLC command not completed yet'`; stale snapshot (not for `pump_off`) → `'PLC status expired, process stopped'`; `'{target} The pre-start command readback is not OFF'`; `'Pump direction and speed readback is invalid'`; `'The rotational speed in the current direction must be 1–1000 RPM'`; `'All pumps must be stopped before changing the common direction or speed.'`.

Reservation: `reserve_flow()` (`:226-230`) fails with `'PLC There is a manual command or the pump is running and cannot be started. CSV Process'` if already reserved, a command is pending, or a pump is owned; `release_flow()` clears it. `configure()` refuses while pending/owned: `'cannot be changed during pump operation or command execution. PLC settings'`.

In the controller, around the PLC:
- `manual_plc` (`controller.py:576-588`): demo → `'Demonstration mode does not control PLC'`; flow active and not an OFF → `'CSV process is being executed'`; schedule busy and not an OFF → `'Scheduling is in progress: Please pause or stop the schedule first'`; **high-voltage interlock** for non-OFF: `manual_interlock()` not ok → `'High voltage safety conditions are not met:' + reasons`.
- `manual_interlock()` (`:528-540`): no RPi connection → allowed, with warning `'RPi Not connected:HMI Unable to confirm the high voltage status, please confirm on site HSPY Closed'`; status older than 20 s → blocked, reason `'RPi Status exceeds 20 Not updated in seconds'`, fix `'Please ask maintenance personnel to go to ”Maintenance Settings › RPi Connection” click ”Read Status”; if it still fails, please check RPi Internet and SSH'`; else `hv_idle_problems` (`backend.py:60-85`): `'HSPY Power readback is invalid'`, `'HSPY power supply is ON'`, `'Nano Invalid readback'`, `'Nano status is {state} (is ready or being exported)'`, `'Nano fault lock {fault} (STOP cannot be cleared)'`, `'Nano D13 The high voltage trigger output is not LOW'`; fixes `ACTION_READ = 'Please ask maintenance personnel to go to ”Maintenance Settings › RPi Connection” click ”Read Status”'`, `ACTION_STOP = 'Please ask maintenance personnel to go to ”Maintenance Settings › RPi Connect” click ”HSPY OFF＋Nano STOP”,Click ”Read Status” again'`, `'Please ask maintenance personnel to go to ”Maintenance Settings › RPi Connection” click ”Clear” Nano fault”, then click ”Synchronize Nano parameter”'`.
- `plc_pumps_commanded_on()` (`:649-653`): true if any auto-off timer is running or any pump read-back is non-zero. Blocks manual plasma (`'PLC The pump is still running. Stop it first and then start the plasma.'`), `prepare`/`spectrum` (`'PLC The pump is still running. Stop it first and then operate it again. RPi'`), parameter save (`'PLC The pump is still running. Stop it first and then modify the plasma parameters.'`), flow launch (`'The pump is running manually (or has not stopped yet), and the process has not started.'`), schedule resume (`'The manual pump is still running, please stop it before continuing the schedule.'`).
- `_launch` (`:857-876`): `'PLC status did not obtain valid readback'` when the recipe uses the PLC and the snapshot is not valid; when it uses the PLC but **not** the RPi and an RPi is connected, the RPi status must be fresh and HV idle → `'RPi power supply and Nano status is not confirmed safe,PLC process not started'`.

Net rule: **pumps and high voltage never run at the same time** (preflight rule 4 + the checks above), and every pump start has a software time limit.

### 4.7 Error handling summary

- Any exception in `_execute` → `last_command.ok = False`, `error` text stored, an immediate re-poll (`plc.py:430-451`).
- In a flow the exception propagates → the run ends `failed`; the `finally` block switches off every pump still owned (§7.3).
- `PLCMonitor.stop()` (`:474-486`): stop the thread (join 5 s), then write 0 to every owned pump, ignoring errors.
- Limits stated in the docs: "When the computer is powered off and the network is interrupted, the software cannot guarantee that the pump will stop."; "existing HMI 30 Stop timing in seconds cannot be replaced PLC independent timeout within/communication protection;PLC cannot be modified at this time". The auto-off lives in the PC process only.

### 4.8 The seam the controller depends on

`validate_plc_config`, `PLCMonitor(config, start=)`, `.state()` (keys used by the controller: `valid`, `auto_off_seconds`, `snapshot['pumps'][n]['raw']`), `.submit(payload)`, `.reserve_flow()`, `.release_flow()`, `.flow_step(action, target, value)`, `.configure(config)`, `.refresh()`, `.stop()`; and `PUMP_COUNT`, `PUMP_TARGETS` used by `csv_workflow.py`. Call sites: `controller.py:129, 509, 588, 650, 861-876, 919, 991-996, 1025, 1035, 1566, 1728, 1866`; `server.py:286-297`.

## 5. Plasma discharge and spectrum capture inside a workflow (d)

### 5.1 RPi step mapping (`controller.py:925-927`)

`{'rpi_status': 'status', 'nano_prepare': 'prepare', 'nano_stop': 'stop', 'power_off': 'stop', 'spectrum_capture': 'spectrum', 'plasma_run': 'plasma_run'}`.

`_remote_command(action, output_dir=None, hv_confirmed=False)` (`:655-677`) runs over SSH:
`timeout -k 3s 215s python3 -B <tools_dir>/plasma_job.py <action> --app-dir <app_dir> --tools-dir <tools_dir> --port <serial_port> [--outdir <dir>]`, plus `--execute --confirm-high-voltage` for `plasma_run`, `--execute` for `clear_fault`. SSH timeout 225 s. Allowed actions: `status`, `prepare`, `stop`, `spectrum`, `plasma_run`, `clear_fault` (`'Not supported RPi command'`). Reply is JSON; `valid is False` → `RuntimeError(result['error'] or 'RPi report failed')`. No adapter → `'RPi SSH not connected yet'`. `plasma_run` without confirmation → `'High voltage discharge must be confirmed by the operator first.'`.

Output folder for `spectrum` and `plasma_run` steps: `<output_dir>/HMI-<uuid4 hex>` (`:928-929`); log `'RPi Work/raw data Table of contents: ' + outdir`.

### 5.2 Parameters

The CSV carries **no** plasma parameters. They live in the RPi's `parameter.json` and are edited through `save_parameters` (`controller.py:741-773`: `plasma_job.py set_parameters … --execute` with the JSON body on stdin, 20 s remote / 25 s SSH timeout; `'RPi Parameter storage failed'`). Keys (from `lite/app.js:31-33` and `remote_read.py:20-24`): `Ontime` → `on_time_ms`, `Offtime` → `off_time_ms`, `Cycle` → `cycles`, `Loopnum` → `loops`, `Integration_Time` → `integration_ms`, `Spectra_Interval` → `spectra_interval_ms`, `Voltage` → `voltage_v`. Lite page input ranges: ON/OFF 1–1000 ms step 0.1; cycles 1–10000; loops 1–1000; integration 1–10000 ms; interval 1–60000 ms; voltage 1–100 V (the later main page uses `MAX_VOLTAGE = 700`, `web/index.html:1053`, `rpi_tools/plasma_job.py:35`).

### 5.3 Preconditions before a run starts — `rpi_ready_problem(uses_plasma)` (`controller.py:542-566`)

Returns the first problem, `''` when ready:
1. Status older than 20 s, or `power.valid` / `nano.valid` not true → `'RPi/HSPY/Nano Readback is incomplete or expired'`.
2. `power.run_stop_setting != 0`, `voltage_v != 0`, `current_a != 0`, `nano.firmware` not in `('1.2.0', '1.2.1')`, `outputs_allowed` not true, `nano.state` not in `('IDLE', 'DONE')`, `fault != 'NONE'`, or `output_pin_sample` not false → `'RPi Required before starting HSPY OFF/zero output,Nano 1.2.x pulse firmware is idle and D13 LOW'`.
3. With plasma: `parameter.valid` not true or `parameter.voltage_v != power.set_voltage_v` → `'RPi parameter.json voltage and HSPY The setting value is inconsistent'`; `integration_ms > 10000` → `'The spectrum integration time exceeds the current SDK 10 Second limit'`; non-integer interval → `'Spectral interval must be an integer millisecond'`; `loops * max(interval, (on+off)*cycles) > 100000` ms → `'Number of rounds/Spectral interval exceeded Nano 120 seconds work deadline'`.

`_launch` appends `',process not started'`. Start also requires `hv_confirmed is True` when the recipe has `plasma_run`: `'Processes containing high-voltage plasma must first confirm the high-voltage prompt'` (`:847-848`).

### 5.4 Sequence of a `plasma_run` step (`controller.py:930-964`)

1. `remote_dir_for_recovery = outdir`; take `remote_lock`; `active_remote_dir = outdir`.
2. `_remote_command('plasma_run', outdir, hv_confirmed=True)` — one blocking SSH command, up to 215 s on the RPi. What the RPi does is in `rpi_tools/plasma_job.py` (not my file); the LITE doc describes it: background frames first, sync Nano, check HSPY set voltage against `parameter.json`, HSPY ON, confirm voltage, Nano ARM/START, D12 trigger, D13 pulses with one signal frame per loop, then D12 LOW, HSPY OFF, Nano STOP and re-read to zero for up to 20 s. `plasma_job.py:395-396` shows `start_spectrum(..., frames=3, ...)` as the default and `:513-514` calls it for `background` without a frame count, `:522` uses `frames=loops` for the signal.
3. `_fetch_result({'id': flow_id, 'latest_plasma_job': outdir})` → `spectrum_result.load_result`: runs `remote_result.py` on the RPi, returns base64+zlib JSON; checks both groups; accepts **1–1000 frames per group** (`spectrum_result.py:95`), so the HMI side does not require 3 background frames.
4. Update `latest_result_dir`, `latest_result_flow_id`, `latest_result_data`, `flow['latest_plasma_job']`, `flow['plasma_runs_completed'] += 1`.
5. Calibration step → `_record_calibration_run`.
6. Log `'Spectrum page has been updated: Signal N open, background M open'`.
7. `save_set(data_dir, spectrum, 'flow', recipe_name, round, rounds)`; a failure is only logged (`'Spectrum group failed to archive:…'`).
8. `remote_dir_for_recovery = None`.
9. Not a calibration step → `_measure(outdir, step)` (§5.7).
10. `finally: active_remote_dir = None`.

Steps 2–9 all run inside `with self.remote_lock` (`:934-964`), so the RPi link stays locked from the start of the discharge until the analysis has returned (up to 215 s for the job + 45 s per result read + 150 s for the analysis). During that time the 5 s status poll is skipped (non-blocking acquire), and because RPi status is "fresh" for only 20 s, `rpi_fresh` goes false and the page shows the RPi values as unknown for the rest of the step. `active_remote_dir` is cleared only in the `finally`, i.e. `plasma_running` in `/api/state` stays true through the result read and the analysis as well. (Cross-check addition.)

After every RPi step: reply must be a dict (`'RPi The reply format is incorrect'`). After `status`: `'RPi status step readback is invalid'` if `power`/`nano` not valid; `'RPi status step discovery HSPY ON or D13 HIGH'` if `run_stop_setting != 0` or `output_pin_sample` not false (`:965-972`).

A `spectrum_capture` step inside a flow only runs the RPi command into an `HMI-<id>` folder; its frames are **not** downloaded or archived by the controller. (The manual `spectrum` action is different: `CAP-<id>`, exactly 3 frames fetched by SFTP, ≤ 2 MB each, same pixel count and wavelength axis; errors `'RPi raw Spectrum response is incomplete'`, `'RPi raw Spectrum file name is incorrect'`, `'RPi raw Spectrum file too large'`, `'RPi raw The number of spectral pixels is inconsistent'`, `'RPi raw The spectrum wavelength axis is inconsistent'`; `controller.py:701-736`.)

### 5.5 Retries and failures

- A `plasma_run` is **never repeated automatically**. `backend.py:154`: `'RPi command timeout; do not automatically repeat acquisition'`.
- Only the read of the finished result is tried a second time (`_fetch_result`, `:630-647`): log `'Failed to read the spectrum, read it again (the measurement has been RPi Completed, will not be re-discharged):…'`, audit `rpi_result_retry`; reconnects first when the link is down or the error is a connection error.
- If the second read also fails the step raises → the run is `failed`, and because `remote_dir_for_recovery` is still set the controller logs `'RPi The plasma work has not been confirmed to be completed and is being re-delivered. cancel; Please confirm on site HSPY OFF and D13 LOW'` and starts `_cancel_remote(dir, True)`.
- Any failure of the plasma command itself → same path.
- A failed run inside a schedule ends the whole schedule (§6.5).

### 5.6 Cancel (`_cancel_remote(remote_dir, force_retry=False)`, `controller.py:1830-1852`)

New `SSHAdapter` (second session, because the running job holds the first), then up to 40 times every 0.25 s: `python3 -B <tools_dir>/plasma_job.py cancel --outdir <dir>` until the reply has `cancel_sent` → log `'RPi Plasma work has received a stop signal'`, audit `rpi_cancel_sent`. Without `force_retry` it gives up as soon as `active_remote_dir` is no longer that folder. After 40 tries: `'RPi No stopplasma found PID; Please use onsite entity to stop'` (audit `rpi_cancel_unconfirmed`). On exception: `'RPi Stop signal failed; please use on-site entity to stop:…'` (audit `rpi_cancel_failed`).

### 5.7 Analysis after each non-calibration `plasma_run` (`_measure`, `controller.py:1366-1407`) — "Never fails the run"

`_analysis_exec('analyze', {'jobs': [outdir], 'reference': <applied calibration reference or None>})` = `timeout -k 3s 150s python3 -B <tools_dir>/spectrum_analysis.py analyze` with JSON on stdin (SSH timeout 160 s; `'RPi Spectral analysis failed:…'`). Then `meas.scale_for_frame_sum`, `meas.evaluate(kind, row, method_id, context, coefficients, qc)`, and for valid samples `meas.apply_zero`. Any exception → `meas.failed(...)` record. The record is appended to `measurements.jsonl` and its summary to `flow['measurements']`. Logs: `'{sample|Blank}Analysis not completed:…'`, `'{sample|Blank}Concentration:Cu 1.234, … mg/L'`, `'{sample|Blank}Light intensity has been calculated; concentration has not been converted:{reason}'`, `'Blank exceeds baseline threshold:…'` (+ audit `blank_qc_fail`), `'Blank is not compared:…'`, `'The measurement result failed to be archived:…'`. Concentration = `(a * intensity + b) * c` (`measurements.py:178-183`). Reasons for no concentration: `'Correction has not been applied yet'`, `'RPi The analysis settings are different from the applied calibration, please recalibrate.'`, `'This analysis is invalid'`, `'Analysis failed'`.

### 5.8 Manual plasma (maintenance only) — `manual_plasma(confirmed)` (`controller.py:590-628`)

Checks: `'Demonstration mode does not control RPi'`, `'You must first confirm the high voltage prompt in the confirmation window'`, `'CSV process is being executed'`, schedule busy, `'RPi SSH not connected yet'`, `'PLC The pump is still running. Stop it first and then start the plasma.'`, `rpi_ready_problem(True)`, `'RPi is executing the command'`. Runs the same `plasma_run`, fetches the result, `save_set(..., 'manual')`. No measurement record. Audit `manual_plasma_start` / `manual_plasma_done`. While it runs, `manual_rpi('stop')` sends the cancel over a second session and returns `{'accepted': True, 'cancel_sent_to': dir}` (audit `manual_plasma_cancel`, `:686-690`).

## 6. Scheduling (e)

### 6.1 Model

"Interval measurement schedule: one Recipe run per slot on a fixed time grid. Slot `i` is planned at `first_slot + i × interval`." (`scheduler.py:1-8`). One schedule at a time. Optional blank recipe interleaved.

Request body of `start_schedule` (`controller.py:1547-1595`): `{'operator': name, 'recipe_id', 'schedule': {'interval_minutes': int, 'start': {'mode': 'now'|'at', 'time': 'HH:MM'}, 'end': {'mode': 'count'|'until'|'continuous', 'count': int, 'time': 'HH:MM'}}, 'blank': {'recipe_id', 'every': int} | null, 'confirm_hv': bool}`. Default form on the page (`web/index.html:1063-1064`): start now, interval 10 min, end count 6, blank disabled, every 4.

### 6.2 Validation (`scheduler.validate_schedule`, `scheduler.py:42-77`)

- Request not a JSON object → `'The schedule setting format is incorrect.'` (`scheduler.py:45-46`; added in the cross-check).
- `interval_minutes` integer 1–1440 → `'The measurement interval must be 1–1440 Integer of minutes'`.
- start `now` → first slot = now (no microseconds); `at` → next local `HH:MM` not before now (`'The time format must be HH:MM (00:00–23:59) '`); else `'The start method must be ”immediately” or ”specified time”'`.
- end `count` 1–1000 (`'The number of measurements must be 1–1000 Second-rate'`); `until` → next `HH:MM` after first slot + 1 s; `continuous`; else `'The end condition must be ”number of times”, ”to the specified time” or ”continue until stop is pressed”'`.
- Span of a bounded schedule > 31 days → `'The total schedule length cannot exceed 31 days; for long-term operation, please select ”Continue until you press stop” After'`.
- `MAX_BLANK_EVERY = 100`.

Normalised `spec`: `{'first_slot': ISO local, 'interval_seconds', 'interval_minutes', 'start': {'mode','time'}, 'end': {'mode':'count','count'} | {'mode':'until','time','until': ISO} | {'mode':'continuous'}}`. `last_index`: count−1; until → `floor(span / interval)`; continuous → `None`. `planned_total` = last+1 or `None`.

### 6.3 Preview and start rules (`schedule_preview`, `controller.py:1496-1545`)

- Blank setting must be exactly `{'recipe_id','every'}` (`'Blank setting format is incorrect'`), `every` 1–100 (`'The blank interval must be every 1–100 samples'`), and not the sample recipe (`'Blank Recipe Cannot be used with samples Recipe same'`).
- Estimate per run = `scheduler.estimate_run_seconds` (`scheduler.py:113-131`): sum of delays + per step: syringe 12 s, pump/direction/speed 1 s, `rpi_status` 6, `nano_prepare` 10, `spectrum_capture` 40, `nano_stop`/`power_off` 5, `plasma_run` 90 s (with valid parameters: `+ loops × max(spectra_interval_ms, (on+off) × cycles) / 1000 − 60`).
- Hard error when the interval is not longer than the recipe's total delay: `'interval N minutes shorter than{sample|Blank} Recipe The total waiting time of S Second,Every transaction will time out'`.
- Warnings: `'{label}Estimated single appointment M point S seconds, longer than the interval: some periods will be skipped due to timeout'`; `'{label} Recipe executed every time N wheel; it is recommended to use single wheel in scheduling mode Recipe'`; `'Blank Recipe None plasma_run,Unable to check baseline'`; `'Calibration has not been applied: the blank will be measured but cannot be compared with the calibrated blank, and no baseline alarm will be generated.'`.
- Returns `{'spec','estimate_seconds','planned_total','last_slot_at','uses_plasma','uses_rpi','rounds','blank','warnings'}`.

`start_schedule` refusals: `'Demonstration mode does not start the real machine process'`; `'Please select the operator first (the list is provided by the maintenance personnel in ”Maintenance Settings” › Operator maintenance)'`; `'Schedules containing high-voltage plasma must first check the high-voltage safety item in the confirmation window'`; `'The process is already being executed'`; `'is already scheduled and is in progress'`; `'PLC status did not obtain valid readback'`; `'RPi Not connected: The schedule of the content measurement step cannot be started.'`. The high-voltage confirmation is given **once** at schedule start; each slot then calls `self.start(csv, sched['uses_plasma'], …)` (`:1659-1663`).

Audit `schedule_start` detail example: `'{name} Every N point · first HH:MM · common N Second-rate | arrive HH:MM | Continuous · Blank {name} Every N samples · Contains high voltage (safety items confirmed)'`.

### 6.4 Schedule record (`SCHEDULE-<id>.json`, `controller.py:1573-1580`)

`{'id', 'status', 'created_at', 'recipe_id', 'recipe_name', 'recipe_sha256', 'operator', 'spec', 'planned_total', 'last_slot_at', 'estimate_seconds', 'uses_plasma', 'uses_rpi', 'hv_confirmed', 'next_index', 'next_slot_at', 'slots': [...], 'counts': {...}, 'error', 'blank': {'recipe_id','recipe_name','recipe_sha256','every','estimate_seconds'} | None, 'finished_at'}`. (`finished_at` does not exist in the record while the schedule is active; it is added when the loop ends, `:1706`, or by `_load_schedules` after a restart, `:1464`. `estimate_seconds` and `uses_plasma` / `uses_rpi` cover the sample **and** the blank Recipe: the larger estimate, and "either uses it", `:1522,1543-1544`. Cross-check additions.)

Slot: `{'index' (0-based), 'planned_at', 'status', 'kind'?: 'sample'|'blank', 'flow_id'?, 'started_at'?, 'finished_at'?, 'note'?}`. `counts[status]` is incremented on every slot update whose status is not `running`. Only the last 2000 slots are kept in the record (`del slots[:-2000]`, `:1609`); the screen gets the last 60 (`schedule_view`, `:1482-1490`).

The CSV text of the recipe is held in memory (`schedule_csv`, `schedule_blank_csv`) and is **not** in the schedule file. Deleting a recipe used by an active schedule is refused (`'this Recipe is being used in the schedule. Please stop the schedule before deleting it.'`).

Saved after: start, every slot status change, missed slots, and at the end.

### 6.5 The loop (`_schedule_loop`, `controller.py:1635-1709`)

For each index while `last is None or index <= last`:
1. Publish `next_index`, `next_slot_at`; status `waiting`.
2. `_wait_for_slot(due)` (`:1621-1633`, polls every 0.5 s): stop requested → end `stopped` (`'Operator stops scheduling'`); **paused** and now > due + 5 s → slot `skipped_paused`, note `'The schedule is in paused state when the period arrives.'`, go to the next index; not paused and now ≥ due → run.
3. Kind: blank when a blank is configured and (`samples_since_blank is None` or `>= every`); so **the first slot that actually runs is a blank**, then one blank after every N samples.
4. `self.start(...)`. If it raises: slot `not_started` with the error text (≤ 200 chars), audit `schedule_slot_not_started` `'No. N times:{exc}'`; after `SCHEDULE_START_FAILURES = 3` consecutive → schedule `failed`, `'Continuous 3 times: Read back{exc}'`.
5. Started: slot `running` (`flow_id`, `started_at`, `kind`), status `running`; join the flow thread; outcome `completed` → `done`, `stopped` → `stopped`, anything else → `failed` (note = flow error). Stop requested → end `stopped`. Not `done` → end `failed`: `'No. N measurements failed and the schedule has been stopped:{error}'` ("Equipment state after a failed run is uncertain: do not continue unattended.").
6. `next_due_index(spec, index, now, grace_seconds=5)`: every slot whose time is more than 5 s in the past is **missed** → `skipped_overrun`, note `'No. N measurements have not ended yet, no measurements were taken during this period'`, audit `schedule_slot_skipped` `'No. N times: The last transaction timed out'`. The next run is the next future slot. Missed slots are never run late.

End: status `completed` / `stopped` / `failed` (or `'Scheduling internal error:…'`), `finished_at`, `error`, `next_slot_at = None`; audit `schedule_end`.

Visible status is computed by `_schedule_status(phase)` (`:1611-1619`): stop requested → `stopping`; pause requested → `paused`; else the phase (`waiting` / `running`).

### 6.6 After a restart or power loss (`_load_schedules`, `controller.py:1448-1472`)

The 20 newest `SCHEDULE-*.json` are loaded. Any with a status in `scheduler.ACTIVE = ('waiting','running','paused','stopping')`: its `running` slots become `failed` with note `'was interrupted during execution (HMI Restart or power outage), please confirm the equipment status on site'` (and `counts['failed'] += 1`); the schedule becomes `interrupted`, `finished_at` = now, `error = 'HMI Restart or power outage, the schedule has been interrupted and will not automatically resume; please restart the schedule after confirming the device'`. Comment: "Power loss or HMI restart: never resume high voltage by itself." The newest becomes `self.schedule`, the rest `schedule_history`. Nothing is restarted.

### 6.7 Pause / resume / stop of a schedule

- `pause_schedule` (`:1711-1721`): only from `waiting`/`running` (`'There are currently no ongoing schedules that can be paused.'`); sets `schedule_pause`, status `paused`; if a flow is `running` it also calls `pause()`. Audit `schedule_pause`.
- `resume_schedule` (`:1723-1736`): only from `paused` (`'Schedule is not in pause state'`); if no flow is paused and a manual pump is still on → `'The manual pump is still running, please stop it before continuing the schedule.'`; resumes the paused flow; status `running` (flow resumed) or `waiting`. Audit `schedule_resume`.
- `stop_schedule` (`:1738-1748`): from any active status (`'There are currently no ongoing schedules'`); sets `schedule_stop`, status `stopping`; stops the active flow. Audit `schedule_stop`.
- While the schedule is **paused between slots** the equipment is free (`SCHEDULE_BUSY` excludes `paused`): manual pumps, parameters etc. are allowed. While a **flow** is paused the PLC stays reserved and manual starts stay locked.
- `_refuse_when_scheduled()` → `'Scheduling is in progress: Please pause or stop the schedule first'` guards: manual start of a recipe (`start` without `schedule_slot`), manual PLC non-OFF, manual plasma, `prepare`/`spectrum`/`clear_fault`, parameters, calibration start, RPi tool deploy. `disconnect` → `'The schedule is in progress. Please pause or stop the schedule first.'`; pairing → `'Scheduling is in progress, please stop scheduling first'`.

## 7. Blank / standard / sample, operators, pause, resume, abort, stop (f)

### 7.1 Sample and blank

`meas.KINDS = ('sample', 'blank')`. `start(csv_text, hv_confirmed, recipe_name=None, schedule_slot=None, kind='sample')` (`controller.py:840-855`; `'The measurement type must be sample or blank'`) stores `flow['sample_kind']`. A manual recipe run is always `sample`; only the schedule sets `blank`. A blank is a **separate recipe** (README: "For example P1 hit blank"); the controller has no notion of which pump carries blank liquid.
- Sample: concentration from the applied coefficients; optional blank-zero subtraction (median of the last `window` blanks of the same calibration and method, MAD rejection, needs `min_blanks`); uncorrected value kept in `conc_raw`.
- Blank: same numbers plus a baseline check against the calibration blank: shift (`'spectral shift X nm (threshold Y) '`), apparent concentration (`'{metal} apparent concentration X mg/L (threshold ±Y) '`), normalization area change (`'{metal} Normalized area change +X% (threshold ±Y%) '`). "A failed check raises an HMI alarm; it never stops the schedule."

### 7.2 Standards and calibration (`calibration.py` + `controller.py:1074-1336`)

- Plan: 2–12 levels (`MAX_LEVELS`), each `{'name' (1–20 chars), 'conc': {Cu,Zn,Ni} mg/L or null, 'rounds' 1–10}`, `discard_rounds` 0–3, `recipe_id` (standard recipe), optional `prime_recipe_id` (Fluid replacement and draining recipe). Sorted low → high; the lowest must be the blank (all three 0; `'The lowest concentration standard must be blank:Cu, Zn, Ni Fill in all 0 (blank spectrum is used as a reference spectrum for displacement correction)'`); only one blank; each metal needs ≥ 2 distinct concentrations; total runs ≤ 80. Unit `mg/L`.
- The standard recipe must contain exactly one `plasma_run` (`'standard product Recipe must contain exactly 1 indivual plasma_run (at present N ); the number of rounds can be changed on the calibration page.'`). The prime recipe may contain only pump/syringe/delay steps (`'Fluid replacement and draining Recipe can only have pump, syringe pump and waiting steps (cannot measure content or RPi steps)'`).
- Generated sequence per level (`build_steps`, `calibration.py:146-172`): one `operator_prompt` step (`note` = `'Please replace with standard product {name} (Cu x / Zn y / Ni z mg/L) ,P6 Put the pipette into the new bottle and press ”Confirm Liquid Change”'`, phase `Fluid change`) → prime recipe steps (phase `Fluid replacement and draining`) → `discard_rounds` discarded rounds + `rounds` kept rounds of the standard recipe; every step tagged `calibration = {'level','level_name','run','discard','round'}`.
- `start_calibration(raw, hv_confirmed)` (`:1185-1206`): `'Calibration contains high-voltage plasma, you must first confirm the high-voltage prompt'`; launches with `uses_plc=True`, recipe name `'Correction:{name}'`, `flow_extra={'mode': 'calibration', 'calibration_id': id}`; phases `['Fluid change'] (+ 'Fluid replacement and draining') + recipe phases`; audit `calibration_start`.
- Operator prompt (`_wait_for_operator`, `:1075-1088`): all pumps must be off (`'All pumps must be stopped before the fluid change prompt:P…'`); sets `flow['prompt'] = {'message','since','level'}`; log `'Waiting for operator:…'`; waits for `confirm_prompt()` (`'There are currently no fluid replacement prompts waiting for confirmation.'`; audit `calibration_swap_confirm`); log `'The operator has confirmed the fluid change'`. Stop works during the wait; **pause is not checked during the wait**.
- Each finished round is appended to the record's `runs`; logs `'Correction {level} Pre-run round completed (not included in regression)'` / `'Correction {level} No. N round completed'`.
- When the flow ends: record status = `analyzing` if the flow completed, else the flow status (`failed`/`stopped`); analysis runs on the RPi (`config`, `reference` from the blank rounds, `analyze`), then `cal.regress` (least squares `concentration = a × intensity + b`, `c = 1`) → `analyzed`, or `analysis_failed`.
- `apply_calibration(record_id)` (maintenance): writes the fitted `a`, `b` and `c = '1'` for every metal with a valid fit into `calibration_config.json`, writes `calibration_active.json`; errors `'Calibration analysis has not been completed yet'`, `'No metal has made a valid return'`.
- Calibration rounds do not create measurement records (`controller.py:960-961`).

### 7.3 Stop / abort of a flow

`stop()` (`controller.py:1063-1072`): only when a flow is active (`'There is currently no process in execution.'`); status `stopping`; `stop_event.set()`; if a plasma job is in progress (`active_remote_dir`) → `_cancel_remote(dir)` on a new thread. Returns `{'accepted': True}` at once.

Where the flow thread notices: before every step (`:897-898`), every ≤ 0.2 s inside a `delay` (`:911`), every 0.2 s while paused (`:1030`) and while waiting for the operator (`:1082-1084`), and once more after the last step (`:973-974`). It raises `RuntimeError('Operator requested to stop')`. A PLC step in progress is **not** interrupted (it finishes or times out at 25 s). An RPi step in progress ends when the RPi command returns (the cancel makes `plasma_run` return).

Then (`:977-1016`):
- Log `'Process aborted:' + error`. Status = `stopped` if `stop_event` is set, else `failed`. `flow['error'] = error`.
- If a plasma job had started and its result was not confirmed: log `'RPi The plasma work has not been confirmed to be completed and is being re-delivered. cancel; Please confirm on site HSPY OFF and D13 LOW'` and `_cancel_remote(dir, True)`.
- `finally`: `pause_event.clear()`; when the flow used the PLC: for every pump that still has an auto-off timer → `plc.flow_step('pump_off', 'P'+n)`; errors collected; `plc.release_flow()`. `finished_at` set. If any pump-off failed: status forced to `failed`, `flow['stop_errors'] = [...]`, log `'Pump stop not confirmed; please use entity stop:… How does the'`.
- The record is written (§8). A calibration run then updates its record.

What stop does **not** do: it does not reset or return the syringe; it does not send an RPi `stop` unless a plasma job was running; it does not restore direction/speed. A stopped flow cannot be continued: "Start from the next 1 step starts" (README).

**Emergency stop:** there is none in software. README: "The screen does not provide a software emergency stop button, but only prompts to use a physical emergency stop.STOP is software stop."; "`HOLD` with screen `E-STOP` remains deactivated; physical stopping device must be used on site"; "`STOP` is not a safety level emergency stop". The manual "HSPY OFF＋Nano STOP" (`manual_rpi('stop')`) is open to operators at all times (also during a flow: `:692`).

`close()` on program exit (`:1854-1868`): stop the schedule (join ≤ 240 s), stop the flow (join ≤ 240 s; prints `'RPi/PLC flow did not confirm STOP; use the physical stop device.'` if still alive), `plc.stop()` (switch off owned pumps), close SSH.

### 7.4 Pause and resume of a flow

`pause()` (`:1043-1052`): only from `running` (`'There is no currently executing process that can be paused.'`); status `pausing`; `pause_event.set()`; log `'Suspension request received'` + `'; Pause after completion of current step'` when the current step is anything other than `delay` / `pump_on` / `pump_off` / `pump_direction` / `pump_speed` — i.e. a syringe step, an RPi step, or the calibration `operator_prompt` (`:1049-1050`; corrected in the cross-check: the prompt step was missing here). During an `operator_prompt` the status stays `pausing` until the operator presses Confirm fluid change, because `_wait_for_operator` never looks at `pause_event`.

The hold happens in `_pause_point(pumps_on)` (`:1018-1041`), which is called before each step and inside `delay`:
1. For every pump the flow switched on: add the elapsed time to `used`, send `pump_off`.
2. Status `paused`, `flow['paused_at']`, `flow['paused_pumps'] = sorted names`; log `'Process has been suspended'` + `'; Stopped P3, P4,Restart when continuing'`.
3. Wait (stop is honoured).
4. On resume: each pump is switched on again with budget `min(120, max(1, max − used))` seconds; status `running`; `paused_at`/`paused_pumps` removed; log `'Process continues'` + `'; Restarted P3, P4'`.

A `delay` keeps its remaining time: "Paused time does not count: the remaining wait resumes where it stopped" (`:906-915`).

`resume()` (`:1054-1061`): from `paused` or `pausing` (`'The process is not in paused state'`); from `pausing` it just cancels the request (status back to `running`).

While paused the PLC stays reserved, so manual pump starts remain locked. If a paused flow is stopped, `paused_at`/`paused_pumps` stay in the saved record; likewise `prompt` if stopped during a bottle-swap wait.

### 7.5 Operators (`controller.py:271-301`)

- A list of names in `operators.json`, maintained by maintenance (`set_operators`: ≤ 50 names, `'has the largest list of operators 50 people'`; each 1–20 characters without control characters or `< > " \`, `'The operator name must be 1–20 characters without control characters or < > " \'`; duplicates dropped; if the current operator is removed the selection is cleared).
- `select_operator(name)` — **no password**; `'The operator is not in the list, please contact the maintenance personnel to add it.'`; audit `operator_select` `'{previous or "—"} → {name}'`.
- The current operator is in memory only (`''` after a restart). It is stored in every flow record (`operator`), in every audit row as default `actor`, in calibration records, and a schedule cannot start without one.
- Actors seen in the audit log: an operator name, `'Maintenance'`, `'system'`, `'operator'` (`:1230` fallback), or `''`.

### 7.6 The "password" of the original (from `auth.py` and `server.py`; outside my assigned files, summarised because the merge needs it)

Operators never sign in. One **maintenance PIN** unlocks engineering functions: 6–8 digits (`PIN_PATTERN = \d{6,8}`) or a legacy password of 12–256 characters. Stored as PBKDF2-HMAC-SHA256, 600 000 iterations, 32-byte salt, in `data/admin.json`. Set from the command line (`server.py --set-admin-password`, prompts hidden). `login` returns a random session token valid `SESSION_SECONDS = 600`, renewed on each use, sent as header `X-Admin-Session`. Lockout: 5 wrong entries → 30 s, doubling each time up to 900 s. Sessions are dropped when the file changes. Texts: `'Maintenance PIN format is incorrect'`, `'Maintenance PIN Must be 6–8 digits'`, `'Maintenance PIN Must be 6–8 digits (or 12 (a password containing more than ZXQ characters)'`, `'has not been set for maintenance PIN'`, `'Too many attempts, please N Try again in seconds'`, `'Maintenance PIN mistake'`, `'This operation is only for maintenance personnel (maintenance PIN) '`, `'spectrum original data is only for maintenance personnel (maintenance personnel PIN) '`. Deleting a recipe asks for the PIN again. README: entry is a 3-second long press on the top-left name, or `/#admin`.

## 8. Run records (g)

One file `HMI-FLOW-<id>.json` per run, written **only when the run ends** (`controller.py:1004-1014`), as `<name>.tmp` then rename. A run cut off by power loss or a crash therefore leaves **no** flow file (the schedule file and, for calibration, the calibration record are the only traces). A write failure is only logged: `'Process record failed to save:…'`.

Fields:

| Field | Set at | Meaning |
|---|---|---|
| `id` | launch (`:880`) | uuid4 hex |
| `status` | | final: `completed`, `failed`, `stopped` |
| `uses_rpi` | launch | recipe contains RPi steps |
| `mode` | launch | `production` (has `plasma_run`), `production_without_plasma`, or `calibration` |
| `recipe_name` | launch | `None` for a raw CSV start; `'Correction:…'` for calibration |
| `started_at`, `finished_at` | launch / end | UTC ISO |
| `step_count`, `current_step` | launch / each step | |
| `phases`, `rounds` | launch | step-bar phases; number of rounds |
| `events` | `_log` | last 120 `{'time','message'}` |
| `operator` | launch | current operator name |
| `schedule_id`, `slot` (1-based), `slots_planned` | launch | `None` when not scheduled |
| `sample_kind` | launch (`start`) | `sample` / `blank`; absent in calibration runs |
| `calibration_id` | launch (calibration) | |
| `phase`, `round`, `current_action`, `current_target` | each step (`:901-903`) | |
| `latest_plasma_job` | after each `plasma_run` | RPi folder `<output_dir>/HMI-<hex>` |
| `plasma_runs_completed` | after each `plasma_run` | count |
| `measurements` | `_measure` | list of `{'id','kind','valid','conc': {Cu,Zn,Ni},'conc_reason','qc'}` |
| `error` | on failure/stop | text |
| `stop_errors` | end | pump-off failures |
| `paused_at`, `paused_pumps`, `prompt` | transient | may remain if stopped while paused / waiting |

Not stored in the flow record: the CSV text, the expanded step list, the plasma parameters, the pump speed used.

Related stores per run: the spectrum set `spectra/SPEC-*.json` (`source: 'flow'`, with `flow_id`, `recipe_name`, `round`, `rounds`, `remote_dir`), the measurement line in `measurements.jsonl` (with `flow_id`, `schedule_id`, `slot`, `round`, `recipe_name`), the audit rows, the raw job folder on the RPi.

Views: `history` = last 300 rows of `FLOW_ROW_KEYS` in memory; `flow_history()` = newest 30; `run_log(limit=200)` (`:257-269`) = flow rows (`kind: 'flow'`, `time` = `started_at`) merged with schedule slots that did not run (`kind: 'slot'`, statuses `skipped_overrun`, `skipped_paused`, `not_started`; fields `time` = `planned_at`, `status`, `recipe_name`, `operator`, `schedule_id`, `slot`, `slots_planned`, `error` = note).

## 9. Every state name and status text (h)

### 9.1 State names (code value → label on the original main page, `web/index.html:1050-1062`)

Flow `status`: `idle` standby, `running` is executing, `pausing` Paused, `paused` Suspended, `stopping` Stopping, `completed` Complete, `failed` failed, `stopped` Stopped.
Flow `mode`: `production` Formal measurement (high voltage), `production_without_plasma` plasma-free process, `isolated_test` isolation test (label exists; the controller no longer produces it), `calibration` (no entry in `MODE_ZH`).
Schedule `status`: `waiting` Wait for the next period, `running` under measurement, `paused` Suspended, `stopping` Stopping, `completed` Completed, `stopped` Stopped, `failed` has stopped (error), `interrupted` has been interrupted (not automatically restored).
Slot `status`: `running` under measurement, `done` Complete, `failed` failed, `stopped` Stopped, `not_started` not started, `skipped_overrun` Skip (last timeout), `skipped_paused` skip (scheduling pause). `scheduler.SLOT_LABEL` (`scheduler.py:24-26`) has the same texts plus `skipped_stopped` is not executed (scheduling has ended), which no code I read produces.
Calibration record `status`: `running`, `analyzing`, `analyzed`, `analysis_failed`, `interrupted`, plus `failed` / `stopped` copied from the flow.
Measurement `kind`: `sample` (sample), `blank` (Blank). Blank QC `status`: `ok`, `fail`, `unchecked`.
RPi tool file state: `current`, `outdated`, `missing`, `unknown`.
PLC direction: `CW`, `CCW`, `UNKNOWN`. Nano states referenced: `IDLE`, `DONE`, `FAULT`, `ARMED`, `WAIT_TRIGGER`, `RUNNING`; fault `NONE` (README also names `HEARTBEAT_LOST`, `TRIGGER_TIMEOUT`, `JOB_TIMEOUT`).
Phases: Prepare, sampling, Sample delivery, pure water, Submit for inspection, Waste discharge, standard product, add acid, air push, Measure, Mix, standby, Fluid change, Fluid replacement and draining.
Alarm priorities on the page (`web/index.html:1043`): 1 Emergency, 2 high, 3 middle. Navigation (`:1055`): Overview, Schedule measurement, manual operation, Trend, execution record, Correction, alarm.

### 9.2 Flow event log texts (`_log`) — `controller.py`

| Line | Text |
|---|---|
| 905 | `'{i}/{n} No. {line} List {action} {target} {value}{unit}'` |
| 933 | `'RPi Work/raw data Table of contents: {outdir}'` |
| 951-952 | `'Spectrum page has been updated: Signal {n} open, background {m} open'` |
| 958 | `'Spectrum group failed to archive:{exc}'` |
| 979 | `'Process aborted:{error}'` |
| 981 | `'RPi The plasma work has not been confirmed to be completed and is being re-delivered. cancel; Please confirm on site HSPY OFF and D13 LOW'` |
| 1003 | `'Pump stop not confirmed; please use entity stop:{errors}'` |
| 1014 | `'Process record failed to save:{exc}'` |
| 1028 | `'Process has been suspended'` (+ `'; Stopped {pumps},Restart when continuing'`) |
| 1041 | `'Process continues'` (+ `'; Restarted {pumps}'`) |
| 1051 | `'Suspension request received'` (+ `'; Pause after completion of current step'`) |
| 1081 | `'Waiting for operator:{note}'` |
| 1088 | `'The operator has confirmed the fluid change'` |
| 1115, 1129 | `'Calibration record failed to update:{exc}'` |
| 1116-1117 | `'Correction {level} Pre-run round completed (not included in regression)'` / `'Correction {level} No. {n} round completed'` |
| 1138 | `'Calibration analysis failed:{exc}'` |
| 643 | `'Failed to read the spectrum, read it again (the measurement has been RPi completed, will not be re-discharged):{first}'` |
| 1390 | `'The measurement result failed to be archived:{exc}'` |
| 1396 | `'{sample|Blank}Analysis not completed: {reason}'` |
| 1398-1399 | `'{sample|Blank}Concentration:Cu x, Zn y, Ni z mg/L'` |
| 1401 | `'{sample|Blank}Light intensity has been calculated; concentration has not been converted:{reason}'` |
| 1404 | `'Blank exceeds baseline threshold:{problems}'` |
| 1407 | `'Blank is not compared:{reason}'` |
| 1480 | `'Schedule record failed to be saved:{exc}'` |
| 1839 | `'RPi Plasma work has received a stop signal'` |
| 1846 | `'RPi No stopplasma found PID; Please use onsite entity to stop'` |
| 1849 | `'RPi Stop signal failed; please use on-site entity to stop:{exc}'` |

### 9.3 Error / refusal texts raised by the controller (sent to the page as `{'error': …}`)

Start and run: `'Demonstration mode does not start the real machine process'` (845, 1187, 1549); `'Processes containing high-voltage plasma must first confirm the high-voltage prompt'` (848); `'The measurement type must be sample or blank'` (854); `'The process is already being executed'` (860, 1563); `'PLC status did not obtain valid readback'` (862, 1567); `'The pump is running manually (or has not stopped yet), and the process has not started.'` (864); `'RPi power supply and Nano status is not confirmed safe,PLC process not started'` (868); `'RPi SSH not connected yet'` (601, 657, 751, 870, 1224, 1244); `'{problem},process not started'` (874); `'Operator requested to stop'` (898, 912, 974, 1031, 1084); `'RPi The reply format is incorrect'` (966); `'RPi status step readback is invalid'` (969); `'RPi status step discovery HSPY ON or D13 HIGH'` (972); `'All pumps must be stopped before the fluid change prompt:…'` (1077).

Pause/stop: `'There is no currently executing process that can be paused.'` (1046); `'The process is not in paused state'` (1057); `'There is currently no process in execution.'` (1066); `'There are currently no fluid replacement prompts waiting for confirmation.'` (1094).

Schedule: `'Scheduling is in progress: Please pause or stop the schedule first'` (574); `'Please select the operator first (the list is provided by the maintenance personnel in ”Maintenance Settings” › Operator maintenance)'` (1552); `'Schedules containing high-voltage plasma must first check the high-voltage safety item in the confirmation window'` (1556); `'is already scheduled and is in progress'` (1565); `'RPi Not connected: The schedule of the content measurement step cannot be started.'` (1569); `'Blank setting format is incorrect'` (1508); `'The blank interval must be every 1–100 samples'` (1511); `'Blank Recipe Cannot be used with samples Recipe same'` (1513); `'interval...every transaction will time out'` (1528-1529); `'There are currently no ongoing schedules that can be paused.'` (1714); `'Schedule is not in pause state'` (1726); `'The manual pump is still running, please stop it before continuing the schedule.'` (1729); `'There are currently no ongoing schedules'` (1741); schedule end messages `'Operator stops scheduling'` (1626, 1686), `'Continuous N times:…'` (1670), `'No. N measurements failed and the schedule has been stopped:…'` (1689), `'Scheduling internal error:…'` (1703); slot notes `'The schedule is in paused state when the period arrives.'` (1651), `'No. N measurements have not ended yet, no measurements were taken during this period'` (1695); restart texts (1461, 1465).

Manual PLC / RPi: `'Demonstration mode does not control PLC'` (578); `'CSV process is being executed'` (581, 598, 693, 746, 757); `'High voltage safety conditions do not meet:…'` (587); `'Demonstration mode does not control RPi'` (593, 681, 1787); `'You must first confirm the high voltage prompt in the confirmation window'` (595); `'PLC The pump is still running. Stop it first and then start the plasma.'` (603); `'RPi is executing the command'` (608, 699, 753, 787); `'Not supported RPi command'` (659, 683); `'High voltage discharge must be confirmed by the operator first.'` (668); `'RPi report failed'` (674); `'PLC The pump is still running. Stop it first and then operate it again. RPi'` (697); raw capture errors (707-724); `'Demonstration mode does not set the actual machine'` (743); `'PLC The pump is still running. Stop it first and then modify the plasma parameters.'` (749); `'RPi Parameter storage failed'` (764); `'There are currently no completed plasma_run spectrum'` (781); `'Please connect first RPi SSH Read spectrum'` (785); `'Please connect first RPi SSH'` (807, 821, 1767, 1789); `'RPi The command is being executed, please try again later.'` (809, 824, 1770, 1804); `'RPi Spectrum record missing field …'` (828).

RPi link: `'Demonstration mode does not connect to the real machine'` (421, 453, 464); `'SSH Password format is incorrect'` (423); `'RPi has not been paired yet: Please go to ”Maintenance Settings” › RPi Connect” to perform ”pairing” RPi”'` (425); `'The process is being executed and cannot be interrupted. RPi connection'` (439); `'The schedule is in progress. Please pause or stop the schedule first.'` (441); `'Please enter RPi account password (only used this time, will not be stored)'` (466); `'The process is being executed and cannot be paired.'` (469); `'Scheduling is in progress, please stop scheduling first'` (471); `'RPi The host key is different from the fingerprint confirmed on the screen, pairing has been stopped; please read the fingerprint again and verify'` (475); `'Automatic connection failed:…'` (391).

Recipes / captures / operators: `'capture ID format is incorrect'` (306); `'Recipe ID format is incorrect'` (311); `'Recipe does not exist'` (315); `'Recipe File integrity verification failed'` (317); `'this Recipe is being used in the schedule. Please stop the schedule before deleting it.'` (325); `'this Recipe is being executed, please stop before deleting it.'` (327); `'Recipe The name must be 1–60 characters'` (337); operator texts (282, 286, 297).

Calibration / measurement: `'Calibration plan format is incorrect'` (1159); `'Please select the standard product Recipe'` (1170); `'Calibration contains high-voltage plasma, you must first confirm the high-voltage prompt'` (1190); `'RPi Spectral analysis failed:…'` (1214); `'This correction is still in progress'` (1222, 1242); `'Calibration analysis is in progress, please wait.'` (1226, 1250, 1292); `'There is no measuring wheel of blank standard, and the reference spectrum cannot be established.'` (1228, 1248); `'RPi The command is being executed, please analyze again later.'` (1254); `'analysis period RPi The analysis settings have been modified, please reanalyze.'` (1264); `'Calibration analysis has not been completed yet'` (1309); `'No metal has made a valid return'` (1313); `'HMI Closed during calibration execution or analysis'` (149).

RPi tools: `'The process or plasma is being executed and cannot be updated. RPi tool'` (1792); `'RPi , you need to confirm the coverage (it will be backed up first)'` (1796); `'Required before updating HSPY OFF (Please read the status first to confirm)'` (1799); `'The hash does not match after uploading: ...; backup in Check the syntax of the script on …'` (1827).

HTTP layer (`server.py`): `'Invalid host'`, `'Not found'`, `'Test page disabled'`, `'Invalid local request'`, `'need JSON,maximum 220 KB'`, `'need JSON object'`, `'Please disconnect first before changing settings'`, `'RPi not connected yet'`, `'Invalid CSV request'`. `physical_start_blockers` texts (`server.py:72-82`): `'PLC status has no valid readback'`, `'RPi/HSPY/Nano status has no valid readback'`, `'STOP/The disconnection timeout has not yet completed the on-site verification. The'`, `'Cu/Zn/Ni Currently using uncorrected default constants'`.

PLC texts: all quoted in §4. CSV texts: all quoted in §3. Scheduler texts: all quoted in §6.2.

### 9.4 Audit actions

From `controller.py`: `operator_select`, `recipe_delete`, `recipe_publish`, `rpi_link_lost`, `rpi_auto_connect`, `rpi_paired`, `manual_plasma_start`, `manual_plasma_done`, `manual_plasma_cancel`, `rpi_result_retry`, `spectrum_import`, `calibration_swap_confirm`, `calibration_start`, `calibration_analyze`, `calibration_exclude`, `calibration_apply`, `blank_qc_fail`, `blank_zero_update`, `frame_sum_update` (`'Every N Zhang Xianjia → Every M Zhang Xianjia'`), `blank_qc_update`, `schedule_start`, `schedule_slot_not_started`, `schedule_slot_skipped`, `schedule_end`, `schedule_pause`, `schedule_resume`, `schedule_stop`, `rpi_tools_deploy`, `rpi_cancel_sent`, `rpi_cancel_unconfirmed`, `rpi_cancel_failed`.
From `server.py`: `maintenance_login`, `plc_manual`, `rpi_connect`, `rpi_disconnect`, `operators_update`, `plasma_parameters`, `calibration_update`, `recipe_run`, `csv_start`, `workflow_pause`, `workflow_resume`, `workflow_stop`.
From the old handler in `controller.py`: `admin_login`, `rpi_manual`, `csv_test_start`.

## 10. The lite page (`lite/index.html`, `lite/app.js`)

Title `HM Simple control`; header `HM · Simple control` / `Local operation interface`; two tabs `Manual` and `CSV Process`; a clock; a notice line. No file I read serves `lite/` any more: `LITE_WORKFLOW.zh-TW.md` names `Start-Lite.cmd` (port 8767) and `legacy_tests/test_csv_lite_v1.py` imports `lite_server`, but neither `Start-Lite.cmd` nor `lite_server.py` is in the folder. It is still the clearest small example of the manual + CSV layout.

Manual tab cards:
1. `connection` — `RPi SSH Password` input, buttons `connection RPi`, `Reread status`; hint `RPi: 192.168.1.1:22/pi; PLC: 192.168.0.1:502.The password is only left in this program memory.` Status line `PLC: Connected|No valid readback obtained ｜ RPi: SSH Connected|SSH not connected`.
2. `Device status` — `HSPY: ON|OFF|unknown / settings N V / output N V`, `RPi Plasma settings:N V (and HSPY inconsistent) `, `Nano: {firmware} {state},D13 HIGH|LOW`, `PLC: command readback is valid|unknown`; hint `PLC Display command readback; the actual operation of the pump still needs to be confirmed on site.`
3. `peristaltic pump P1–P5` — `shared direction` select (`Forward rotation CW` / `reverse CCW`), `Set direction`, `Forward rotation RPM`, `Set forward speed The address set by`, `reverse RPM`, `Set the reverse speed`; per pump a checkbox, `command ON|command OFF|unknown`, `/ about N Automatically after seconds OFF`, buttons `ON` / `OFF`; batch `selected pump ON` / `selected pump OFF`; command line `PLC Executing:…` / `Recent commands:… / Read back successfully|failed:… / execution x.xx Second / Queue x.xx Second` / `PLC Standby`. Only P1–P5 are drawn (`[1,2,3,4,5]`, `lite/app.js:19`).
4. `syringe pump` — `channel` (`CH1 Drainage`, `CH2 Nitric acid`, `CH3 Air`), `volume mL` (0.01–5, default 0.10), `Liquid extraction`, `Liquid injection`, `reset`.
5. `RPi/Nano` — `read status`, `synchronous pulse parameter to Nano LCD`, `capture raw spectrum`, `HSPY OFF＋Nano STOP`.
6. `plasma parameters` — `Pulse ON (ms)`, `Pulse OFF (ms)`, `every round pulse times`, `Number of rounds`, `integration time (ms)`, `Spectral interval (ms)`, `HSPY Set voltage (V)`; `Loading RPi Current settings`, `Save parameters and synchronize HSPY set value`.

CSV tab: file input, `Inspection Process`, `Start isolation load test`, `Stop process`, checkbox `I have confirmed that the high-voltage load is disconnected, someone is monitoring the scene and physical stop is possible`, a text area, a command table (`lite/app.js:36`), `execution status` (`Status:… Steps:i / n Mode:… Error:… Stop failed:…`) with the event list, and `latest plasma results` (`re-read the latest plasma_run spectrum`, view `signal average spectrum` / `background average spectrum` / `signal − background`, a canvas, Cu/Ni read-back tiles).

Requests it makes: `GET /api/bootstrap`, `GET /api/state` every 1 s, `GET /api/result/latest`; `POST /api/connect {password}`, `/api/plc {...}` (the payloads of §4.5), `/api/rpi {action}`, `/api/parameters {Ontime, Offtime, Cycle, Loopnum, Integration_Time, Spectra_Interval, Voltage}`, `/api/validate {csv}`, `/api/start {csv, isolated}`, `/api/stop {}`. Notices: `command has been sent`, `RPi Connected`, `P{n} command has been queued, waiting PLC read back`, `direction command has been queued, waiting PLC read back`, `{dir} {rpm} RPM has been queued, waiting PLC read back`, `Syringe pump command has been sent`, `Syringe pump reset has been sent`, `RPi command completed`, `Loaded RPi Current settings`, `RPi parameters and HSPY The setting value has been saved; please synchronize Nano LCD`, `CSV passed the inspection:N step, waiting S seconds; incl. plasma_run|none plasma_run`, `isolation load process starts`, `Stopping the process; please check the actual machine OFF`, `Please check the pump first`, `The speed must be 1–1000 integer RPM`, `The volume needs to be 0.01–5 mL`, `CSV exceeds 200 KB`, `Loaded {file}`, `Process has started:{id}` / `Process record has been loaded:{id}`.

## 11. Notes for re-mapping to the Arduino Mega (facts from this code; V7 is the authority for the new side)

What the HMI model assumes about liquid handling, and where V7 may differ (to be checked against V7, not decided here):
1. Six on/off pumps `P1`–`P6` with fixed process roles; dosing is purely by **run time at a shared speed**. No volume, flow-rate or weight concept exists anywhere in the controller, the CSV language, or the records.
2. Direction and speed are **global** (one direction, one RPM per direction), changeable only with all pumps off, and must be set inside every recipe before the first `pump_on`.
3. One syringe pump with three selectable channels (`CH1` Drainage to the mixing tank, `CH2` Nitric acid, `CH3` Air), volume 0.01–5.00 mL in 0.01 mL steps, driven as "set volume, pulse channel, pulse aspirate/dispense"; plus a reset pulse (manual only).
4. No valves and no stirrer are driven ("not installed V1–V3 Solenoid valve and stirrer … Removed from view").
5. Every command is verified by read-back and serialized through one worker; the flow waits synchronously (25 s limit); manual commands are asynchronous with `pending` / `last_command` shown on screen.
6. Every pump start carries a maximum run time enforced by the PC process (auto-off), default 30 s manual, 1–120 s.
7. A running flow reserves the pump controller; manual starts are locked, OFF is always allowed.
8. Pumps and high voltage are mutually exclusive.
9. Status validity window: `max(15, 3 × poll_seconds)` seconds.

## 12. What I could not determine

- The actual contents of any file under `data` (by rule): which recipes exist, real pump times, real speeds, the operator names, whether a PIN is set.
- What the PLC program does with each register (how the channel trigger and aspirate/dispense trigger become a physical move, whether moves queue, how long a stroke takes). The code only sees command read-back.
- Any flow-rate calibration (mL per second per RPM). None exists in these files.
- Which physical liquid each pump carries beyond the phase names; README mentions "P1 hit blank" only as an example.
- How many background frames the RPi takes is decided in `rpi_tools/plasma_job.py` (`start_spectrum(..., frames=3, ...)` default at `:395-396`, used for `background` at `:513-514`); I did not read that file in full. The Windows side accepts 1–1000 background frames (`spectrum_result.py:95`).
- `rpi_pairing.py`, `remote_result.py`, `server_v1_reference.py`, `tests/`, the rest of `web/` were not read (other subsystems).
- Whether the lite page was ever shipped with this version; no entry point serves it.
- `scheduler.SLOT_LABEL['skipped_stopped']` has no producer in the files I read.

---

## Cross-check

Done 2026-10-06 by a second reader. `controller.py` (all 2052 lines), `plc.py` (486), `scheduler.py` (131), `csv_workflow.py` (211), `backend.py` (238), `calibration.py` (339), `measurements.py` (295), `spectrum_result.py` (136), `spectrum_store.py` (73), `calibration_config.py` (61), `server.py` (473), `auth.py` (133), `web/workflow_template.csv` were read completely and compared with this report; `rpi_tools/plasma_job.py` lines 390-447 and 472-586; `web/index.html` lines 1042-1874 and 1950-2322. Nothing was run. No `data` folder was opened.

### Claims checked against the source and found correct

1. `ACTIVE_FLOW`, `SCHEDULE_BUSY`, `FLOW_ROW_KEYS`, `MAX_OPERATORS`, `SCHEDULE_START_FAILURES` (`controller.py:58-64`).
2. CSV header, limits (`MAX_ROWS 500`, `MAX_STEPS 2000`, `MAX_NESTING 5`, `MAX_REPEAT 20`, 200 000 bytes) and every row of the action table in §3.2, with the error texts (`csv_workflow.py:14-101`).
3. All seven preflight rules of §3.3 and the returned keys (`csv_workflow.py:170-211`).
4. Phase rules of §3.4 including "P4 while P5 is on → Waste discharge" and the `delay` cases (`csv_workflow.py:123-162`).
5. The `pump_speed` inconsistency: `60.0` passes CSV validation (`Decimal % 1`) and fails in `flow_step` (`value.isdigit()`) (`csv_workflow.py:91`, `plc.py:251`).
6. Register map and constants of §4.2 (`plc.py:13-23,98-114`).
7. `submit` payload shapes, exact-key-set checks and texts; `flow_step` shapes, 25 s wait and texts (`plc.py:151-291`).
8. Interlock order in `submit` and `flow_step`; `reserve_flow` / `release_flow` / `configure` (`plc.py:138-146,194-234,263-285`).
9. `_execute` sequences: direction / speed re-read all pump registers first; pump start failure writes 0 to every pump of the command; syringe = volume write, channel pulse, action pulse; `_pulse` = checked 0, unchecked 1, 1.0 s, checked 0. The "12 Modbus transactions" count is right (2 + 5 + 5) (`plc.py:293-378`).
10. Auto-off: per-pump deadline, retried every 2 s on failure, error text (`plc.py:380-396`).
11. `manual_plc` checks and `manual_interlock` texts; no RPi connection → allowed with a warning (`controller.py:528-540,576-588`; `backend.py:56-89`).
12. `rpi_ready_problem` conditions and texts, including the 100 000 ms limit behind the "120 Second" text (`controller.py:542-566`).
13. `_launch` checks and the flow dict it creates (`controller.py:857-889`).
14. RPi step mapping, `_remote_command` argument list, 215 s / 225 s timeouts, `HMI-<uuid hex>` folders (`controller.py:655-677,925-929`).
15. The ten-step `plasma_run` sequence of §5.4, the post-step checks, the result-read retry and the cancel procedure (`controller.py:630-647,930-983,1830-1852`).
16. `_measure` never fails the run; log texts; concentration formula (`controller.py:1366-1407`; `measurements.py:178-183`).
17. Schedule validation, `last_index`, `next_due_index` (5 s grace), `estimate_run_seconds` numbers (`scheduler.py:42-131`).
18. `schedule_preview` errors and warnings; `start_schedule` refusals; the audit text (`controller.py:1496-1595`).
19. The schedule loop of §6.5 step by step, including "first slot that actually runs is a blank", `not_started` × 3 → `failed`, any non-`done` run → `failed`, missed slots → `skipped_overrun` (`controller.py:1635-1709`).
20. Restart handling of schedules and of calibration records (`controller.py:145-150,1448-1472`).
21. Pause / resume / stop of a schedule and of a flow, the pump budget `min(120, max(1, max − used))`, the `finally` block that switches off owned pumps (`controller.py:987-1072,1711-1748`).
22. Operators: name rule, list limit, selection without a password, audit detail (`controller.py:74-76,272-301`).
23. Run record fields of §8 and "written only when the run ends" (`controller.py:880-885,1004-1014`).
24. The status label tables quoted from `web/index.html:1050-1062`.
25. `web/workflow_template.csv` as quoted in §3.6 (compared line by line).
26. `plasma_job.py`: `start_spectrum(..., frames=3, ...)` default, background call without a frame count, signal call with `frames=loops`, `collect_spectrum(..., expected_frames=3)` (`rpi_tools/plasma_job.py:395-396,429,443-446,513-514,521-522`).

### Corrections made in the text above

- §7.4: the suffix `; Pause after completion of current step` is logged for every current step that is not `delay` / `pump_on` / `pump_off` / `pump_direction` / `pump_speed`; the report said "syringe or RPi step" and left out the calibration `operator_prompt`. Also added that the status stays `pausing` for the whole bottle-swap wait.
- §6.2: added the missing error text `The schedule setting format is incorrect.`.
- §6.4: `finished_at` is not in the record while the schedule is active; `estimate_seconds`, `uses_plasma`, `uses_rpi` combine the sample and the blank Recipe.
- §5.4: added that the whole `plasma_run` step, including result read and analysis, holds `remote_lock`, so the RPi status goes stale (unknown on screen) during the step and `plasma_running` stays true until the analysis is done.

### Additions (not in the report before)

- `manual_rpi('status')` (the maintenance button `read status`) is refused during a flow (`'CSV process is being executed'`, `:691-693`) and never stores its result in `remote_status`; only the poll, `/api/refresh`, `connect` / pairing and `save_parameters` do. The interlock fix texts that say "Click ”Read Status”" therefore rely on the 5 s poll, not on the button.
- `start()` validates and checks `hv_confirmed` **before** `_refuse_when_scheduled()` (`:846-852`); `_launch` is the only place that checks "The process is already being executed". A scheduled slot therefore fails with that text (slot `not_started`) when a maintenance-started flow or a calibration is still running at the slot time — nothing else prevents a calibration from being started while a schedule is `paused` (`start_calibration` only refuses `SCHEDULE_BUSY`, `:1191`), and after resume the slots count toward the three-failures rule.
- `stop()` of a flow does not send an RPi `stop` and does not touch the syringe (stated in §7.3). In addition: when the stop request arrives during `nano_prepare`, `rpi_status` or `spectrum_capture`, that SSH command is simply allowed to finish (up to 215 s); only `plasma_run` has a cancel path.
- A `spectrum_capture` step in a flow produces a job folder on the RPi and nothing on the PC (stated in §5.4). In addition its reply is not checked for `frames`; only "is a dict" and `valid is not False`.
- `_fetch_result` reconnects with `self.password` (`:646`), which is `''` after key pairing — the retry therefore works only when the key link is available; with a password-only link the password is still in memory (`:411`).
- Flow record: the launch dict also carries `uses_rpi`, and `mode` for calibration is set through `flow_extra` after the dict is built, so a calibration record has `mode: 'calibration'`, `calibration_id`, and **no** `sample_kind`; the screen's `MODE_ZH` has no label for it (already noted in §9.1).
- Phase labels are tied to pump numbers, not to liquids: `PUMP_PHASES` keys `P1`…`P6` and `SYRINGE_PHASES` keys `CH2`, `CH3` are literal targets (`csv_workflow.py:123-124`), and the flow diagram's `PHASE_PATH` uses the same phase names (`web/index.html:1075-1080`). Renaming or renumbering pumps for the Mega changes what the phase strip and the highlighted path show unless these three tables are rewritten together.
- The lite page section (§10) was not re-checked (no entry point serves it).

### Still open after the cross-check

- Everything in §12 that depends on the `data` folder or on the PLC program.
- Whether `hardware_verify.py spectrum --frames 1` works (that file is not in `hmi_v1_1/rpi_tools/`).
