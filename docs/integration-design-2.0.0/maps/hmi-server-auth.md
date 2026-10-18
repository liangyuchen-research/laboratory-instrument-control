# HMI v1.1 — server, HTTP API, authorisation, demo, kiosk (subsystem: hmi-server-auth)

Source folder: `C:/Users/PlasmaLab/Desktop/UI/UI/hmi_v1_1` (called HMI below). Read-only investigation; nothing was run, no `data` folder was opened or listed.

Files read completely: `server.py`, `auth.py`, `backend.py`, `rpi_pairing.py`, `remote_read.py`, `remote_result.py`, `config.example.json`, `ui_features.json`, `admin-page.html`, `manual-page.html`, `Install.cmd`, `Install.ps1`, `Install-Autostart.cmd`, `Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`, `Set-Admin-Password.cmd`, `build_release.py`, `README.md`, `VALIDATION.zh-TW.md`, `tests/test_v1_1.py`, `tests/test_production.py`, `requirements.txt`.

Files read in part, only to resolve what `server.py` calls: `controller.py` (lines 1-890, 1040-1100, 1149-1250, 1287-1336, 1405-1604, 1705-2052), `plc.py` (1-479), `spectrum_result.py` (grep), `web/index.html` (login/operator/boot code only: lines 56-69, 456-499, 774-873, 1052-1071, 1196-1202, 1233-1489, 1556-1575, 1704-1802, 1824-1864, 2182-2207, 2258-2307), `web/workflow/app.js` (1-80), `web/i18n.js` (grep).

---

## 0. The ten facts that matter most

1. **There is no operator password in the original HMI.** Operators use the HMI with no sign-in at all; they only pick their name from a list (`server.py:276-277`, `README.md:13`). The only secret is the **Maintenance PIN** (maintenance PIN, 6–8 digits; a legacy 12–256 character password is still accepted). File and option names still say "admin" (`Set-Admin-Password.cmd`, `--set-admin-password`, `data/admin.json`, header `X-Admin-Session`).
2. PIN storage: `data/admin.json` = `{"version":1,"salt":<64 hex>,"digest":<hex>,"iterations":600000,"kind":"pin"|"password"}`, PBKDF2-HMAC-SHA256, 32 random salt bytes, 600 000 iterations (`auth.py:59-70`).
3. Sessions: random `secrets.token_urlsafe(32)`, kept only in server memory, 600 s sliding expiry, sent by the browser in header `X-Admin-Session`, stored in the browser's `sessionStorage['hmi_admin_session']` (`auth.py:34,83-88,115-129`; `web/index.html:1348,1368`).
4. Lockout: 5 wrong entries → 30 s lock, doubling on each further lockout, capped at 900 s; counters are in memory, global (not per client), reset on a correct entry or a server restart (`auth.py:35-37,94-113`). While the lock runs, **every** entry is refused before the hash is even computed — a correct PIN too — and such refused entries do not count as failures (`auth.py:94-97`). [cross-check addition]
5. Every POST needs header `X-HMI-Token` (random per server start, handed out by `GET /api/bootstrap`), a `Host` of `127.0.0.1:<port>` or `localhost:<port>`, and an `Origin` that is absent or the same origin (`server.py:93-95,254-257`). The server binds `127.0.0.1` only (`server.py:451`).
6. One Python process, standard library `ThreadingHTTPServer` subclass `ExclusiveHTTPServer` (refuses a port already in use, `controller.py:103-114`). Default port in code is 8765; every `.cmd` passes `--port 8766` — **the same port the current EC program uses** (EC historical-development-notes.md: "Local web page 127.0.0.1:8766").
7. The PC talked to the RPi over **SSH (paramiko)**: `exec_command` of `python3`/`plasma_job.py` on the RPi, JSON on stdout, SFTP for spectrum CSVs (`backend.py:92-210`, `controller.py:655-677`). The EC program instead uses TCP 8767 JSON lines. The PC talked to the PLC over **Modbus TCP** (FC03 read / FC06 write, default `192.168.0.1:502`, unit 1) (`plc.py:13,51-85`).
8. Demo mode (`--demo`) in v1.1 simulates **nothing**: no PLC thread, no RPi link, every device command is refused with 409 and a "Demonstration mode…" message; the UI shows a yellow frame and "Simulation mode · No device connected". The synthetic `DemoAdapter` in `backend.py:213-238` is not used by `server.py`/`controller.py`.
9. High voltage is **not** gated by the PIN in every path: `POST /api/schedule/start` and `POST /api/calibration/start` are operator endpoints and can start runs containing `plasma_run` after a confirmation flag in the body. The PIN gates direct `plasma_run`, `/api/recipes/run`, `/api/start`, parameters, Recipes, calibration apply, RPi link/pairing/tools, operator list, raw spectra.
10. Audit log: `data/audit.jsonl`, one JSON object per line `{time, action, detail (≤300 chars), actor}`; actor is the current operator name, `Maintenance`, `system`, or `''` (`controller.py:215-221`).

---

## (a) Process model, command line, HTTP server, static routing

### a.1 Entry point and process model

- Entry: `server.py` → `main()` (`server.py:428-469`). Docstring: "Original V1 HMI pages backed by the integrated V1.1 device controller." (`server.py:1`).
- One process. Objects:
  - `Application` (`server.py:33-85`) — a thin "compatibility surface for V1"; it owns `self.core = IntegratedApplication(demo=demo, data_dir=data_dir)` (`server.py:37`) from `controller.py`. "All equipment I/O belongs to one core."
  - `IntegratedApplication` (`controller.py:117-208`) owns: `AdminAuth`, `PLCMonitor`, the SSH adapter, flow/schedule state, audit log.
- Threads (all daemon):
  - one thread per HTTP request (`ThreadingHTTPServer`; `server.daemon_threads = True`, `server.py:456`);
  - `IntegratedApplication._poll` every 5 s: reads RPi status, or tries key-based auto-connect (`controller.py:351-377`);
  - `PLCMonitor._work` (started only when not demo: `PLCMonitor(self.plc_config, start=not demo)`, `controller.py:129`) — polls the PLC, executes queued commands, auto-off timers (`plc.py:417-460`);
  - flow thread (`_run`), schedule thread (`_schedule_loop`), short-lived cancel threads (`_cancel_remote`).
- Locks in the core: `self.lock` (RLock, state), `self.remote_lock` (RLock, one RPi command at a time), `self.analysis_lock`.
- Shutdown: `KeyboardInterrupt` → `app.close()` → stop schedule and flow (join up to 240 s each), `plc.stop()`, close SSH; then `server.server_close()` (`server.py:463-469`, `controller.py:1854-1868`). If the flow does not confirm: prints `RPi/PLC flow did not confirm STOP; use the physical stop device.`
- Logging: `setup_link_log(app.data_dir)` routes paramiko WARNING+ to `data/logs/ssh.log` (rotating, 1 000 000 bytes × 3 backups) (`server.py:415-425`). HTTP request logging is silenced (`log_message` is a no-op, `server.py:90-91`).

### a.2 Command-line options (`server.py:429-437`)

| Option | Meaning |
|---|---|
| `--demo` | demo mode (see section d). Data folder becomes `demo-data` (`controller.py:120`). |
| `--port N` | default `8765`. All shipped `.cmd` files pass `8766`. |
| `--no-browser` | do not call `webbrowser.open(url)`. |
| `--hide-test-page` / `--show-test-page` | mutually exclusive; override `ui_features.json` `"test_page_enabled"` (shipped value `false`). |
| `--set-admin-password` | interactive: set the maintenance PIN, then exit (section c.2). |

Startup prints `HM HMI v1.1: http://127.0.0.1:<port>/`; if no PIN file: `Maintenance controls locked. Run Set-Admin-Password.cmd to set the maintenance PIN.` (`server.py:457-460`). If the port is taken: `SystemExit('Port {port} is already used by another program (possibly another HMI). Close it or choose another --port.')` (`server.py:452-455`).

`argparse` description: `'HM HMI v1.1, original V1 interface'`.

### a.3 How the HTTP server is built

- `ExclusiveHTTPServer(('127.0.0.1', args.port), make_handler(app, secrets.token_urlsafe(32)))` (`server.py:451`). `ExclusiveHTTPServer(ThreadingHTTPServer)` sets `allow_reuse_address = False` and `SO_EXCLUSIVEADDRUSE` so a second instance cannot bind the same port on Windows (`controller.py:103-114`). Test: `test_second_server_on_same_port_is_refused` (`tests/test_v1_1.py:219-221`).
- `make_handler(app, token)` returns a `BaseHTTPRequestHandler` subclass with `do_GET` and `do_POST` only (`server.py:88-412`). No PUT/DELETE/OPTIONS.
- `respond(code, value, content_type)` (`server.py:97-113`): dict/list → JSON (`ensure_ascii=False, allow_nan=False`, UTF-8); bytes passed through. Headers on every response: `Content-Type`, `Content-Length`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy`.
  - CSP for path `/`: `default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'`
  - CSP for every other path: `default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'`
  - No CORS headers are ever sent.
- JSON errors always have the shape `{"error": "<text>"}`.

### a.4 Static routing (`server.py:221-244`)

| URL | Served file | Notes |
|---|---|---|
| `/` | `web/index.html` | the main single-page HMI (title "HM detector HMI"; React + `web/original/support.js` template engine). Hash deep links: `/#admin`, `/#<admin tab key>` (`recipe`, `rpi`, `plasma`, `cal`, `spectra`, `operators`), `/#<nav key>` (`dash`, `schedule`, `manual`, `trend`, `results`, `calibration`, `alarms`) (`web/index.html:1270-1273`). `?lang=en|zh-CN|zh-TW` (README:55). |
| `/workflow/` | `web/workflow/index.html` | older "V1.1 detection process and Recipe" page with its own password login box. README:57 says "original `/workflow/` long page no longer exists HMI used in", but the route is still served. |
| `/test/` | `web/test/index.html` | "HMI Temporary test page"; whole `/test/` tree returns 404 `Test page disabled` unless the test page is enabled (`server.py:121-122`). Embedded as an iframe (`web/index.html:518`). |
| `/calibration/` | `web/calibration/index.html` | "standard calibration" sub-app, embedded as an iframe (`web/index.html:513`). |
| `/trend/` | `web/trend/index.html` | "concentration trend" sub-app, iframe (`web/index.html:697`). |
| `/shell.css`, `/i18n.js` | `web/shell.css`, `web/i18n.js` | |
| `/original/…`, `/vendor/…`, `/test/…`, `/workflow/…`, `/calibration/…`, `/trend/…` | file under `web/` | only suffixes `.js .css .woff .woff2 .ttf .txt .csv`; path must stay inside `web/`; otherwise 404 `Not found`. |
| `/custom/<name>` | `web/custom/<name>` | branding and text overrides; only `.json .png .jpg .jpeg .svg .webp`; must stay inside `web/custom`. `branding.json` and `texts.json` are optional (absent → 404). Test: `tests/test_v1_1.py:193-199`. |
| `/workflow_template.csv` | `web/workflow_template.csv` | `text/csv; charset=utf-8`. |
| anything else | 404 `{"error":"Not found"}` | |

Content type for static files: `mimetypes.guess_type(name)[0] or 'text/plain'` + `; charset=utf-8` (`server.py:244`).
Path hardening: any `\` in the path, or a `.`/`..` segment → 404 (`server.py:119-120`).
Sub-apps share the admin session through `sessionStorage['hmi_admin_session']` (same origin, same tab): `web/calibration/app.js:34`, `web/trend/app.js:19`, `web/test/app.js:14`, `web/workflow/app.js:4`.

### a.5 A second, older handler exists

`controller.py:1871-2052` contains its own `make_handler`/`main` ("HM HMI v2 integrated controller") with a smaller API (`/api/raw`, `/api/plc`, admin-only set `{'/api/connect','/api/parameters','/api/start','/api/recipes/publish'}`, denial text `This operation requires administrator login`, `Include RPi/Plasma action Recipe requires administrator login`). It serves `web/app.js` and `web/style.css`, which do not exist in `web/`. None of the `.cmd` files start it. Treat it as leftover; `server.py` is the real entry. There is also `server_v1_reference.py` (V1 reference, not assigned, not read).

---

## (b) Every HTTP endpoint

Roles: **anyone** = any local client passing the Host check (GET) or Host+Origin+token checks (POST) — this is what an operator at the panel can do; **admin** = additionally a valid `X-Admin-Session` (maintenance PIN login).

### b.1 Protections common to all requests

GET (`server.py:115-122`):
- `Host` header must equal `127.0.0.1:<port>` or `localhost:<port>` → else `403 {"error":"Invalid host"}`.
- No token and no Origin check on GET.
- Unhandled `ValueError/KeyError/OSError/RuntimeError` → `404 {"error": str(exc)}` (`server.py:245-246`).

POST (`server.py:248-411`), in this order:
1. Read body only if `0 < Content-Length <= 220_000`.
2. `403 {"error":"Invalid local request"}` unless: Host trusted **and** header `X-HMI-Token` == the per-start token **and** `Origin` is absent, `http://127.0.0.1:<port>` or `http://localhost:<port>`.
3. `400 need JSON,maximum 220 KB` unless length in range and `Content-Type` (part before `;`) is `application/json`.
4. `400 need JSON object` unless the body is a JSON object.
5. `404 Test page disabled` for `/api/capture` when the test page is off.
6. `admin = app.auth.allowed(X-Admin-Session)` — evaluated on every POST, and it **renews** the session.
7. `/api/admin/login` and `/api/admin/logout` are handled before the gate.
8. Admin gate: path in `admin_only`, or `/api/rpi` with `action` not in `('stop','status')` → `403 {"error":"This operation is only for maintenance personnel (maintenance PIN) "}` when not admin (`server.py:278-285`).

`admin_only` set, verbatim (`server.py:278-282`):
```
'/api/plc-config', '/api/connect', '/api/disconnect', '/api/refresh',
'/api/calibration-config', '/api/parameters', '/api/recipes/publish', '/api/recipes/run', '/api/recipes/delete',
'/api/start', '/api/capture', '/api/spectra/import', '/api/rpi-tools/deploy',
'/api/rpi/hostkey', '/api/rpi/pair', '/api/operators', '/api/calibration/apply',
'/api/blank-qc', '/api/frame-sum', '/api/blank-zero'
```
Code comment above it: "Operators need no sign-in: schedules, pause/stop, manual pumps, HSPY OFF, operator selection. Everything that changes how the instrument measures belongs to maintenance (PIN)."

POST status codes: `202` for `/api/recipes/run`, `/api/start`, `/api/calibration/start`, `/api/calibration/analyze`, `/api/stop`, `/api/workflow-stop`, `/api/workflow-pause`, `/api/workflow-resume`, `/api/schedule/start|pause|resume|stop`; `200` for the rest (`server.py:403-405`). Errors: `ValueError`/`JSONDecodeError` → 400; `RuntimeError`/`TimeoutError` → 409; `OSError` → 500 (`server.py:406-411`). Other exception types are not caught by the handler (no JSON reply is produced by this code).

The browser recovers from a server restart: on `403` + `Invalid local request` it re-fetches `/api/bootstrap` once and retries (`web/index.html:1712-1723`).

### b.2 GET endpoints

| Path | Who | Response (keys) | Ref |
|---|---|---|---|
| `/api/bootstrap` | anyone | `{token, config, demo, plc_config, test_page_enabled, admin_configured, admin_kind}`. `config` = full RPi SSH config (host, port, username, key_file path, known_hosts path, app_dir, tools_dir, serial_port, output_dir, poll_seconds; no password). `admin_kind` = `'pin'`, `'password'` or `''`. | `server.py:124-128` |
| `/api/auth` | anyone; reads `X-Admin-Session` | `{admin: bool, configured: bool}`. Uses `renew=False`, so polling does not keep a session alive. UI polls it every 10 s (`web/index.html:1261`). | `server.py:129-131` |
| `/api/state` | anyone | `Application.state()`: `demo, connected, quality ('good'|'unknown'), updated_at, age_seconds, snapshot (RPi status or {}), pending (None), error, events (last 50 audit rows as {id,time,level:'INFO',message}), job, plc, workflow, flow, flow_history, can_capture, calibration_provisional, calibration_active, plasma_production_available (True), frames_per_sum, manual_interlock, plasma_ready_problem, plasma_running, schedule, operator, operators, measurement, rpi_link {paired, auto_connect, host}`. UI polls every 1.5 s. | `server.py:46-70,132-133` |
| `/api/workflow-state` | anyone | `{workflow, physical_start_available: False, physical_start_reason}` (reasons joined with `; ` from `physical_start_blockers`, e.g. `PLC status has no valid readback`, `RPi/HSPY/Nano status has no valid readback`, `STOP/The disconnection timeout has not yet completed the on-site verification. The`, `Cu/Zn/Ni Currently using uncorrected default constants`). | `server.py:72-82,134-137` |
| `/api/calibration-config` | anyone | `{config, complete, provisional: True, x_definition, formula, calculation_available: False}` | `server.py:138-142` |
| `/api/calibration` | anyone | `{plan, records, active, coefficients, recipes, unit, metals, flow{id,status,mode,calibration_id,prompt,round,rounds,phase,current_step,step_count,error,recipe_name,events(last 30)}, rpi_connected, demo}` | `server.py:143-153` |
| `/api/measurements?hours=N` | anyone | `{rows, qc, calibration{calibration_id,applied_at,metals}|None, coefficients, blank_zero, unit}`; `hours` accepted only as digits `1..8784`. | `server.py:154-164` |
| `/api/calibrations/<id>` | anyone | one calibration record (`cal.load`). | `server.py:165-166` |
| `/api/recipes` | anyone | `{recipes:[{id,name,created_at,step_count,uses_rpi,uses_plasma,sha256}]}` newest first. | `server.py:167-168`, `controller.py:231-239` |
| `/api/recipes/<id>` | anyone | stored recipe (`id,name,created_at,csv,sha256,step_count,uses_rpi,uses_plasma`) plus `phases, rounds`. `<id>` = 32 lowercase hex. | `server.py:169-172`, `controller.py:309-318` |
| `/api/events` | anyone | `{events:[…]}` last 200 lines of `audit.jsonl`, newest first. | `server.py:173-174`, `controller.py:223-229` |
| `/api/run-log` | anyone | `{rows}` runs plus skipped/not-started schedule slots, newest first, max 200. | `server.py:175-176` |
| `/api/schedule` | anyone | `{schedule: <full record or null>}` | `server.py:177-178` |
| `/api/operators` | anyone | `{operators:[names], current:<name or ''>}` | `server.py:179-180` |
| `/api/result/latest` | anyone | latest plasma result (`core.latest_result()`); errors surface as 404 here. | `server.py:181-182` |
| `/api/spectra` | **admin** | `{sets}`; else `403 spectrum original data is only for maintenance personnel (maintenance personnel PIN) ` | `server.py:183-188` |
| `/api/spectra/<set id>` | **admin** | one spectrum set; same 403 text. | `server.py:189-190` |
| `/api/rpi-jobs` | **admin** | `{jobs, root}`; same 403 text; `RuntimeError/TimeoutError` → 409. | `server.py:198-203` |
| `/api/rpi-tools` | **admin** | `{version, files{name: current|outdated|missing|unknown}, tools_dir, up_to_date, has_unknown}`; `403 This operation is only for maintenance personnel (maintenance PIN) `; 409 on RPi errors. | `server.py:191-197` |
| `/api/history` | anyone, test page enabled only | list of raw captures `{id,created_at,remote_dir,frames,status:'completed',demo:False}` | `server.py:204-206` |
| `/api/captures/<CAP-id>` | anyone, test page enabled only | capture record + `status, demo, report{model,integration_ms}` | `server.py:207-213` |
| `/api/captures/<CAP-id>/csv/<1|2|3>` | anyone, test page enabled only | CSV `wavelength_nm,intensity_raw` (UTF-8 with BOM) | `server.py:214-220` |
| static routes | anyone | see a.4 | |

Admin GETs (`/api/spectra*`, `/api/rpi-jobs`, `/api/rpi-tools`) call `allowed()` with the default `renew=True`, so they extend the session.

### b.3 POST endpoints

All take a JSON object. "Who" as defined above.

| Path | Who | Request body | Response / effect | Ref |
|---|---|---|---|---|
| `/api/admin/login` | anyone | `{pin}` or `{password}` (`body.get('pin', body.get('password'))`) | `200 {session, expires_in: 600}`; audit `maintenance_login` actor `Maintenance`. Failures → 409 with auth message. | `server.py:268-272` |
| `/api/admin/logout` | anyone | `{}` (session in header) | `200 {logged_out: true}` | `server.py:273-275` |
| `/api/plc-manual`, `/api/plc` | anyone | PLC manual command, exact key sets enforced: `{action:'pump_on', pump:1..6, seconds?:1..120}`, `{action:'pump_off', pump}`, `{action:'pump_on_many'|'pump_off_many', pumps:[…]}`, `{action:'pump_direction', direction:'CW'|'CCW'}`, `{action:'pump_speed', direction, rpm:1..1000}`, `{action:'aspirate'|'dispense', channel:1..3, volume_ml:0.01..5.00}`, `{action:'syringe_reset'}` | `{accepted: true}`; audit `plc_manual` (detail = `str(body)`). Refused in demo, during a flow or busy schedule (except pump_off*), or when the HV interlock fails. | `server.py:286-288`, `controller.py:576-588`, `plc.py:151-224` |
| `/api/plc-config` | **admin** | `{host, port, unit_id, poll_seconds}` (subset allowed) | writes `data/plc_config.json`; `{accepted, config}` | `server.py:289-294` |
| `/api/plc-refresh` | anyone | `{}` | wakes the PLC poller; `{accepted: true}` | `server.py:295-297` |
| `/api/connect` | **admin** | `{password?: str, config?: {…}}` | optional `config` is validated and written to `data/config.json` (refused while connected: `Please disconnect first before changing settings`); then `core.connect(password)` → `{connected: true}`; audit `rpi_connect` actor `Maintenance` | `server.py:298-306`, `controller.py:418-434` |
| `/api/disconnect` | **admin** | `{}` | `{accepted: true}`; sets `auto_connect=False`; audit `rpi_disconnect` | `server.py:307-309`, `controller.py:436-448` |
| `/api/rpi/hostkey` | **admin** | `{}` | `{host, port, key_type, fingerprint, already_trusted, paired}` | `server.py:310-311`, `controller.py:450-459` |
| `/api/rpi/pair` | **admin** | `{password, fingerprint}` | `{paired: true, fingerprint}`; audit `rpi_paired` | `server.py:312-313`, `controller.py:461-501` |
| `/api/operators` | **admin** | `{operators: [names]}` | `{operators: [cleaned]}`; writes `data/operators.json`; audit `operators_update` actor `Maintenance` | `server.py:314-316`, `controller.py:280-293` |
| `/api/operator` | anyone | `{name}` | `{operator: name}`; 400 `The operator is not in the list, please contact the maintenance personnel to add it.`; audit `operator_select` (actor = the name, detail `previous → name`) | `server.py:317-318`, `controller.py:295-301` |
| `/api/schedule/preview` | anyone | `{recipe_id, schedule, blank}` | `{spec, estimate_seconds, planned_total, last_slot_at, uses_plasma, uses_rpi, rounds, blank, warnings}` | `server.py:319-320`, `controller.py:1496-1545` |
| `/api/schedule/start` | anyone | `{recipe_id, operator, schedule, blank?, confirm_hv?}` | `202 {accepted, schedule}`; needs `operator` in the list; needs `confirm_hv: true` when a Recipe uses plasma | `server.py:321-322`, `controller.py:1547-1595` |
| `/api/schedule/pause` | anyone | `{}` | `202 {accepted}` | `controller.py:1711-1721` |
| `/api/schedule/resume` | anyone | `{}` | `202 {accepted}` | `controller.py:1723-1736` |
| `/api/schedule/stop` | anyone | `{}` | `202 {accepted}` | `controller.py:1738-1748` |
| `/api/refresh` | **admin** | `{}` | immediate RPi status read; `{accepted: true}`; 409 `RPi not connected yet` | `server.py:329-335` |
| `/api/capture` | **admin**, test page enabled only | `{}` | `core.manual_rpi('spectrum')` → `{id, created_at, remote_dir, frames}` | `server.py:336-337` |
| `/api/rpi` with `action:'plasma_run'` | **admin** | `{action:'plasma_run', confirm_high_voltage:true}` (or `confirm_hv_load_disconnected:true`) | `core.manual_plasma(confirmed)` → spectrum set summary; audit `manual_plasma_start/done` | `server.py:338-339`, `controller.py:590-628` |
| `/api/rpi` other actions | `status`, `stop`: anyone. `prepare`, `spectrum`, `clear_fault`: **admin**. A body without `action` (or with any other value) also needs admin, then fails with 400 `Not supported RPi command`. | `{action}` | `core.manual_rpi(action)`. [cross-check additions] `status` is refused with 409 `CSV process is being executed` while a flow is active (only `stop` passes, `controller.py:691-693`). `status` returns the fresh RPi reply to the caller but does **not** store it: `remote_status` / `remote_updated_mono` (what the interlocks and `/api/state` use) are written only by the 5 s poll, `/api/refresh`, `connect` / pairing and `save_parameters` (`controller.py:360-365,412-416,765-770`, `server.py:332-334`). So the maintenance button `read status` (which posts `{action:'status'}`, `web/index.html:2290`) does not by itself refresh the screen or clear a "Status exceeds 20 Not updated in seconds" interlock; the poll does, within 5 s. | `server.py:340-341`, `controller.py:679-739` |
| `/api/parameters` | **admin** | plasma parameter object (passed as JSON on stdin to `plasma_job.py set_parameters`) | RPi result; audit `plasma_parameters` (detail = body JSON, 200 chars) | `server.py:342-344`, `controller.py:741-773` |
| `/api/calibration-config` | **admin** | calibration coefficient object | writes `data/calibration_config.json`; `{accepted, config, complete, provisional: True}`; audit `calibration_update` | `server.py:345-351` |
| `/api/blank-zero` | **admin** | `{settings}` | blank zero status | `server.py:352-353` |
| `/api/frame-sum` | **admin** | `{frames_per_sum}` | `{frames_per_sum}` | `server.py:354-355` |
| `/api/blank-qc` | **admin** | `{qc}` | `{qc}` | `server.py:356-357` |
| `/api/calibration/plan` | anyone | `{plan}` | `{saved, valid, plan|error, warnings}` | `server.py:358-359` |
| `/api/calibration/check` | anyone | `{plan}` | plan info (`plan, warnings, recipe_name, runs, kept_runs, estimated_seconds, …`) | `server.py:360-361` |
| `/api/calibration/start` | anyone | `{plan, confirm_high_voltage: true}` | `202 {accepted, flow, calibration_id}` | `server.py:362-363`, `controller.py:1185-1206` |
| `/api/calibration/confirm` | anyone | `{}` | `{accepted}` (operator confirmed bottle swap) | `server.py:364-365` |
| `/api/calibration/analyze` | anyone | `{id}` | `202 {accepted, id}` | `server.py:366-367` |
| `/api/calibration/apply` | **admin** | `{id}` | `{applied, config, active}` | `server.py:368-369` |
| `/api/calibration/exclude` | anyone | `{id, exclude}` | `{accepted, exclude_levels}` | `server.py:370-371` |
| `/api/validate` | anyone | `{csv}` | CSV check result without `steps` | `server.py:372-374` |
| `/api/recipes/delete` | **admin + PIN again** | `{id, pin}` | `app.auth.verify(body.get('pin'))` first ("deleting needs the PIN again, not only a session"); `{deleted: true, id, name}`; file moved to `recipes/deleted/<id>.deleted-<stamp>.json` | `server.py:375-377`, `controller.py:320-333` |
| `/api/recipes/publish` | **admin** | `{name (1–60 chars), csv}` | recipe item without `csv` | `server.py:378-379` |
| `/api/recipes/run` | **admin** | `{id, confirm_high_voltage?|isolated?}` | `202 {accepted, flow}`; audit `recipe_run` | `server.py:380-383` |
| `/api/start` | **admin** | `{csv, confirm_high_voltage?|isolated?}` | `202 {accepted, flow}`; audit `csv_start` | `server.py:384-386` |
| `/api/rpi-tools/deploy` | **admin** | `{accept_unknown?: true}` | `{accepted, updated, backups, status}` | `server.py:387-388` |
| `/api/spectra/import` | **admin** | `{remote_dir}` | spectrum set summary | `server.py:389-390` |
| `/api/workflow-pause` | anyone | `{}` | `202 {accepted}`; audit `workflow_pause` | `server.py:391-393` |
| `/api/workflow-resume` | anyone | `{}` | `202 {accepted}`; audit `workflow_resume`. Comment: "The operator confirms the resume dialog on the HMI; no sign-in needed." | `server.py:394-397` |
| `/api/stop`, `/api/workflow-stop` | anyone | `{}` | `202 {accepted}`; audit `workflow_stop` | `server.py:398-400` |
| anything else | | | `404 {"error":"Not found"}` | `server.py:401-402` |

Behaviour guaranteed by tests:
- Without a session: `/api/calibration-config`, `/api/connect`, `/api/recipes/publish`, `/api/recipes/run`, `/api/operators`, `/api/rpi/pair`, `/api/rpi/hostkey`, `/api/parameters`, `/api/spectra/import`, `/api/rpi` plasma_run → 403; `/api/spectra`, `/api/spectra/<id>`, `/api/rpi-jobs` → 403 (`tests/test_v1_1.py:105-122,180-183,212-217`; `tests/test_production.py:378-381`).
- Schedule endpoints are operator actions: in demo they return 409, never 403 (`tests/test_production.py:389-396`).
- `/api/plc-manual` in demo → 409 (`tests/test_v1_1.py:110`).
- Recipe delete: no session → 403; session + wrong PIN → 409; session + no PIN → 409; session + right PIN → 200 (`tests/test_production.py:397-404`).
- `/api/operator` with a name not in the list → 400 (`tests/test_production.py:388`).
- Main page contains `ctl.start`, `ctl.stop`, the text `Physical emergency stop button`, no `E-STOP`, no `window.confirm`/`window.prompt` (`tests/test_v1_1.py:105-131`).

---

## (c) Password and authorisation model

### c.1 What exists

- **Operators: no password, no sign-in.** They select a name. (`auth.py:3-6`: "Operators use the HMI without signing in. Calibration, raw spectra, plasma parameters, Recipes, RPi pairing and tools belong to maintenance, unlocked with a 6–8 digit PIN on the touch keypad (a 12+ character password is still accepted for sites that already set one).")
- **Maintenance ("Maintenance", formerly "Administrator")**: one shared secret, no user names, no per-user accounts, no roles beyond admin / not admin.
- The V1 design fragment `admin-page.html:51-83` shows a planned user-accounts table (name, role, level buttons, state, "New account ADD USER", "restore REVERT", "Storage SAVE") but every control is `disabled` with `title="has not yet been connected to the device/Source"` and `data-unconnected="true"`. README:60 says user management was removed from the screen. So per-user accounts were designed and never implemented.

### c.2 How the PIN is set

- Only from the PC console: `Set-Admin-Password.cmd` → `offline_bundle\python-runtime\python.exe server.py --set-admin-password` → `pause`.
- `server.py:438-447`: `getpass.getpass('New maintenance PIN (6-8 digits; input is hidden): ')`, then `getpass.getpass('Confirm PIN: ')`. Mismatch → `parser.error('PINs do not match')`. Invalid → `parser.error('The PIN must be 6-8 digits (a 12+ character password is also accepted)')`. Success prints `Maintenance PIN saved; previous sessions invalidated.`
- It always writes `ROOT / 'data/admin.json'` (`server.py:443`), and the core always reads `ROOT/'data'/'admin.json'` unless an explicit `data_dir` is passed (`controller.py:122`). So **demo mode uses the same PIN file as production** even though its other data goes to `demo-data`.
- There is no HTTP endpoint and no UI to set or change the PIN. To change it, run the `.cmd` again. To remove it, the file would have to be deleted by hand (nothing in the code does that).
- Until a PIN is set, `auth.configured` is `False` (`auth.py:55-57`, checks that the file exists, evaluated live) and login fails with `has not been set for maintenance PIN`.

### c.3 Validation rules (`auth.py:19-30`)

- `PIN_PATTERN = re.compile(r'\d{6,8}')`.
- `check_secret(secret)`:
  - not a string → `Maintenance PIN format is incorrect`
  - 6–8 digits → accepted (kind `pin`)
  - all digits but wrong length → `Maintenance PIN Must be 6–8 digits`
  - otherwise length must be 12–256 → else `Maintenance PIN Must be 6–8 digits (or 12 (a password containing more than ZXQ characters)` (kind `password`)

### c.4 Storage (`auth.py:59-73`)

```python
salt = secrets.token_bytes(32)
digest = hashlib.pbkdf2_hmac('sha256', secret.encode('utf-8'), salt, 600_000)
{'version': 1, 'salt': salt.hex(), 'digest': digest.hex(), 'iterations': 600_000,
 'kind': 'pin' if PIN_PATTERN.fullmatch(secret) else 'password'}
```
Written to `<path>.tmp` then atomically renamed to `admin.json`. `set_pin = set_password` (alias). After writing, all in-memory sessions are cleared. `kind()` returns the stored `kind`, defaulting to `'password'` for old files, `''` when unreadable (`auth.py:77-81`).

### c.5 Login, verification, lockout (`auth.py:83-113`)

- `login(secret)` = `verify(secret)` then create `session = secrets.token_urlsafe(32)`, `sessions[session] = time.monotonic() + 600`.
- `verify(secret)`:
  - not a string or not configured → `RuntimeError('has not been set for maintenance PIN')`
  - locked → `RuntimeError(f'Too many attempts, please {int(wait) + 1} Try again in seconds')`
  - recompute PBKDF2 with the stored salt and iterations; compare with `hmac.compare_digest`
  - wrong → `failures += 1`; when `failures >= 5`: lock for `min(900, 30 * 2 ** lockouts)` seconds, `lockouts += 1`, `failures = 0`; raise `RuntimeError('Maintenance PIN mistake')`
  - right → `failures = 0`, `lockouts = 0`
- Constants: `SESSION_SECONDS = 600`, `FAILURES_PER_LOCK = 5`, `FIRST_LOCK_SECONDS = 30`, `MAX_LOCK_SECONDS = 900`.
- `verify` is also used for the "PIN again" check on Recipe delete, and wrong entries there count toward the same lockout.
- All these are `RuntimeError` → HTTP 409.
- Lockout counters live in memory only and are shared by all clients. Restarting the server clears them.
- Failed logins are not written to the audit log. Successful ones are (`maintenance_login`, actor `Maintenance`).
- Test: `tests/test_production.py:70-84` (5 wrong → lock; `auth.lockouts == 1`; `set_pin('12345')` rejected).

### c.6 Session lifetime (`auth.py:115-133`)

- `allowed(session, renew=True)`: false for empty; if `admin.json` changed on disk (`(st_mtime_ns, st_size)` differs) → **all sessions are dropped**; expired → removed, false; otherwise true and, when `renew`, expiry pushed to now + 600 s.
- So: 10 minutes of inactivity ends the session (README:31 "Login idle 10 Minutes expire"). "Activity" = any POST carrying the header (the main UI attaches it to every POST, `web/index.html:1714`) or any admin GET. `/api/auth` polling does not renew.
- **[cross-check correction] "Inactivity" is not "nobody touched the screen".** The shell sends renewing requests by itself on two pages: on `Schedule measurement` it re-posts `/api/schedule/preview` every 30 s whenever a Recipe is selected (`web/index.html:1315,1418-1422`), and on `Maintenance settings › spectrum` it re-reads `GET /api/spectra` every 10 s (`web/index.html:1317,1523`). A panel left on either page with a logged-in session therefore never times out. The 10-minute expiry only happens on pages without periodic POST / admin GET (Overview, manual operation, execution record, alarm, the other maintenance tabs), or after the browser tab is closed. A rebuild that wants a real idle timeout must count operator input, not requests.
- Sessions do not survive a server restart (memory only). Several sessions can exist at once.
- `logout(session)` removes that one session.
- Changing the PIN with `Set-Admin-Password.cmd` while the server runs invalidates all sessions through the file-version check.

### c.7 What the PIN protects, and what works without it

Needs the PIN (server-enforced): PLC connection settings; RPi connect/disconnect/refresh; RPi host-key read and pairing; RPi tool update; plasma parameters; `prepare`, `spectrum`, `clear_fault`, manual `plasma_run`; publishing, running directly, and deleting Recipes (delete also needs the PIN typed again); starting an ad-hoc CSV (`/api/start`); calibration coefficients and "apply calibration"; blank QC / frame sum / blank zero settings; operator list; all raw spectra (list, read, import, RPi job list); test-page capture.

Works without the PIN: all status reads; Recipe list and content; audit events; run log; measurements; schedule preview/start/pause/resume/stop (including Recipes with high voltage, with `confirm_hv`); flow pause/resume/stop; manual pumps and syringe (`/api/plc-manual`); PLC refresh; RPi `status` and `stop` (HSPY OFF + Nano STOP); choosing the operator; calibration plan save/check/**start**/confirm/analyze/exclude; CSV validation.

UI consequence: the admin page shows only the login panel until logged in; after login six tabs: `Recipe`, `RPi connection`, `plasma parameters`, `correction coefficient`, `spectrum`, `operator` (`ADMIN_TABS`, `web/index.html:1162`).

### c.8 Exact UI texts — maintenance login (`web/index.html`)

Entry points:
- Long-press the brand box (top-left product name / logo) for 3 s: `HOLD_MS = 3000` (`:1065`), handlers on `.brandbox` `onPointerDown/Up/Leave/Cancel`, context menu suppressed (`:475`, `:1355-1357`, `:1842`). CSS comment: "Brand area: long press 3 Open maintenance login in seconds (no prompt will be displayed, general operators will not see the entrance)" (`:64`).
- URL hash `/#admin` or an admin tab key (`:1270-1272`).
- A nav item at the bottom of the left navigation: label `🔧 Maintenance` when not logged in, `Maintenance settings` when logged in (`:1857-1858`). Note: README:14 says "There is no entrance on the screen", but the shipped page does have this nav item.
- When logged in, the top bar shows a button `maintenance mode` with tooltip `Maintenance settings` (`:485`).

Login panel (`:780-799`, view-model `:2187-2207`):
- Heading: `Maintenance login`
- Sub-heading: `calibration, spectral raw data, plasma parameters,Recipe, RPi with operator list for maintenance personnel only`
- When no PIN file: bold `has not been set for maintenance PIN`, paragraph `Please execute on the computer Set-Admin-Password.cmd settings 6–8 Digital repair PIN , restart HMI.`, button `Return to overview`
- Entry display: one `●` per typed character (`pwShown: '●'.repeat(pw.length) || ' '`)
- Keypad keys in order: `1 2 3 4 5 6 7 8 9 Clear 0 ⌫`; keypad stops at 8 characters; `_pw` capped at 64
- Footer buttons: `Return`, toggle `English keyboard` ↔ `Numeric keys` (on-screen keyboard for legacy passwords), `log in` (disabled while empty)
- Status line: `Logging in…`; on failure `Login failed:` + server message. Server messages: `Maintenance PIN mistake`, `Too many attempts, please N Try again in seconds`, `has not been set for maintenance PIN`
- Logged-in bar: tabs + button `Sign out`. Logout clears `sessionStorage`, cached spectra and Recipe draft, returns to page `dash` (`:1375-1381`).
- Login request: `POST /api/admin/login {pin: <typed>}` (`:1367`).

Denial texts (server):
- `This operation is only for maintenance personnel (maintenance PIN) ` (`server.py:193,285`)
- `spectrum original data is only for maintenance personnel (maintenance personnel PIN) ` (`server.py:186`)
- Request-level: `Invalid host`, `Invalid local request`, `Test page disabled`, `Not found`, `need JSON,maximum 220 KB`, `need JSON object`
- In the UI, a failed maintenance action shows a chip `⚠ Operation failed · Click here for details`; tapping opens a dialog titled `Maintenance operation message` with `failed:` + server text (`web/index.html:1382-1386,2203-2205`).

Recipe delete (PIN again) (`:1475-1488`): dialog `delete Recipe`, confirm `Next step: Enter PIN`, warning `After deletion, the operator can no longer select this Recipe; Past measurement records will not be affected. The file will be moved to data folder recipes/deleted,It can be restored by maintenance personnel if necessary.`; second dialog `input repair PIN to delete`, confirm `Confirm deletion`, numeric pad accepting only `^\d{6,8}$`, hint `Please enter 6–8 digital repair PIN` (`:1687,2174`). The numeric pad cannot enter a legacy 12-character password; the server would accept one.

English equivalents exist in `web/i18n.js` (e.g. `'Maintenance login': 'Service sign-in'`, `'has not been set for maintenance PIN': 'No service PIN set'`, `'Maintenance PIN mistake': 'Wrong service PIN'`, `'This operation is only for maintenance personnel (maintenance PIN) ': 'Service staff only (service PIN)'`, `'maintenance mode': 'Service mode'`; lines 273-349).

Older `/workflow/` sub-app texts (`web/workflow/app.js:18,69`): status `Administrator has logged in` / `Operator mode` / `The administrator password has not been set yet`; it posts `{password: …}` from a plain password field.

### c.9 Operator identities

- List stored in `data/operators.json` as `{"operators": [names]}` (`controller.py:272-293`). Max `MAX_OPERATORS = 50`. Name rule `valid_operator_name`: string, 1–20 characters after strip, no control characters and none of `< > " \` (`controller.py:74-76`). Duplicates removed. Errors: `has the largest list of operators 50 people`, `The operator name must be 1–20 characters without control characters or < > " \`.
- Only maintenance edits the list (`POST /api/operators`, whole list replaced). If the current operator is removed, the current operator becomes `''`.
- Anyone selects the current operator: `POST /api/operator {name}`; held in memory as `core.operator` (default `''`, not persisted across restarts) (`controller.py:192,295-301`).
- Starting a schedule requires an operator from the list: `Please select the operator first (the list is provided by the maintenance personnel in ”Maintenance Settings” › Operator maintenance)` (`controller.py:1550-1552`); `start_schedule` sets `core.operator` to it. Each flow record stores `'operator': self.operator` (`controller.py:884`).
- UI texts: top-bar button label `operator`, tooltip `Change operator`, shows the name or `Please select` (`web/index.html:484,1839`). Picker dialog `Select operator`, confirm `Sure`, warning `For personnel not on the list, please ask the maintenance personnel to › Operator” is added.`; success `Operator:<name>`; failure `Operator setting failed:…`. Empty list dialog: title `No operator list yet`, line `Current roster` / `0 people`, warning `The operator list is determined by the maintenance personnel in ”Maintenance Settings” › Operator” is added; you can select it here after adding it.`, confirm `Go to Add` (logged in) or `Maintenance login and new addition` (`:1442-1451`).
- Maintenance tab `Operator list`: sub `The operator needs to select a name before starting the schedule. Each measurement and operation event will be recorded who performed it.`; empty `No operator yet` / `Please add at least one operator, otherwise the schedule cannot be started.`; footer `On-screen keyboard can input job number/English; please use the physical keyboard to enter or edit the Chinese name data\operators.json.`; button `New operator`; add dialog `Add new operator (job number or English name) What is` (max 20), delete dialog `Delete operator` with `After deletion, this person can no longer be selected as an operator; past records will still retain the name.` (`:858-869,2440-2447`).

### c.10 Audit log (`controller.py:215-229`)

- File `<data_dir>/audit.jsonl`, append-only, UTF-8, one JSON object per line: `{"time": <UTC ISO 8601>, "action": <str>, "detail": <str, cut to 300 chars>, "actor": <str>}`.
- `actor` defaults to the current operator name or `''`; maintenance actions pass `actor='Maintenance'`; background actions pass `actor='system'`.
- Read back by `events()`: last 200 lines, newest first → `GET /api/events`; `GET /api/state` includes the newest 50 as `events` with message `[actor] action detail`.
- No rotation or size limit in the code.
- Actions written from `server.py`: `maintenance_login` (Maintenance), `plc_manual`, `rpi_connect` (Maintenance), `rpi_disconnect` (Maintenance), `operators_update` (Maintenance), `plasma_parameters` (Maintenance), `calibration_update` (Maintenance), `recipe_run`, `csv_start`, `workflow_pause`, `workflow_resume`, `workflow_stop`.
- Actions written from `controller.py`: `operator_select` (actor = chosen name), `recipe_delete` (Maintenance), `recipe_publish` (Maintenance), `rpi_link_lost` (system), `rpi_auto_connect` (system), `rpi_paired` (Maintenance), `manual_plasma_start` / `manual_plasma_done` (Maintenance), `manual_plasma_cancel`, `rpi_result_retry` (system), `spectrum_import` (Maintenance), `calibration_swap_confirm`, `calibration_start`, `calibration_analyze`, `calibration_exclude`, `calibration_apply` (Maintenance), `blank_qc_fail` (system), `blank_zero_update` / `frame_sum_update` / `blank_qc_update` (Maintenance), `schedule_start` (actor = operator), `schedule_slot_not_started` / `schedule_slot_skipped` / `schedule_end` (system), `schedule_pause`, `schedule_resume`, `schedule_stop`, `rpi_tools_deploy` (Maintenance), `rpi_cancel_sent`, `rpi_cancel_unconfirmed`, `rpi_cancel_failed`.
- Test: `tests/test_production.py:405-408` checks actors (`recipe_delete` → `Maintenance`, `operator_select` → the name, `recipe_publish` → `Maintenance`).

### c.11 CSRF / origin summary

- Token: `secrets.token_urlsafe(32)` created at each server start (`server.py:451`), returned by `GET /api/bootstrap`, required as `X-HMI-Token` on every POST.
- Host allow-list on every request; Origin allow-list on POST; no CORS headers; loopback bind only.
- The admin session is a header, not a cookie, so it is not sent automatically by the browser.
- Body limit 220 000 bytes; `Content-Type: application/json` required.

---

## (d) Demo mode

- Started by `Start-Demo.cmd` → `server.py --demo --port 8766`.
- Data folder: `ROOT/'demo-data'` (config, recipes, audit, schedules, calibration all separate) — but the PIN file stays in `ROOT/'data'/'admin.json'` (`controller.py:120-122`).
- `PLCMonitor` thread is not started (`start=not demo`), so PLC state is always invalid/empty.
- No RPi link: `_auto_connect` returns immediately in demo (`controller.py:385`).
- Every device action is refused (HTTP 409) with these messages:
  - `Demonstration mode does not connect to the real machine` — `connect`, `rpi_host_key`, `pair_rpi` (`controller.py:421,453,464`)
  - `Demonstration mode does not control PLC` — `manual_plc` (`:578`)
  - `Demonstration mode does not control RPi` — `manual_plasma`, `manual_rpi`, `deploy_rpi_tools` (`:593,681,1787`)
  - `Demonstration mode does not set the actual machine` — `save_parameters` (`:743`)
  - `Demonstration mode does not start the real machine process` — `start`, `start_calibration`, `start_schedule` (`:845,1187,1549`)
- Still works in demo: login/logout, operator list and selection, Recipe publish/validate/delete, schedule preview, calibration plan save/check, all reads.
- UI: root class `hmi demo` draws an inset yellow frame (`box-shadow: inset 0 0 0 5px #d9a300`, `web/index.html:43`); mode chip `Simulation mode · No device connected` with striped yellow background (vs `official mode`) (`:51,1835`); PLC and RPi chips show `simulation`; values show `simulation mode`; manual page shows `Simulation mode: no control PLC`; start button reason `Simulation mode does not start the device` (`:1829-1833,1956,2095`). Communication alarms are suppressed in demo (`:1592,1605`).
- **Nothing synthetic is generated in v1.1 demo.** `backend.DemoAdapter` (synthetic status and 3 synthetic 801-point spectra, model `DEMO · synthetic spectrum`) exists but is referenced only by `server_v1_reference.py` and `legacy_tests/`. Its docstring: "Explicit --demo only; synthetic data must never be presented as live."
- Tests always construct `Application(demo=True, data_dir=<temp>)` and then set `core.demo = False` with a `SimulatedPLC` when they need flows.

---

## (e) How the PC talked to the RPi and to the PLC

### e.1 RPi — SSH with paramiko (`backend.py`, `rpi_pairing.py`, `controller.py`)

Connection settings — `config.example.json` (defaults; real values in `data/config.json`):
```json
{"host": "192.168.1.1", "port": 22, "username": "pi", "key_file": "",
 "known_hosts": "~/.ssh/known_hosts",
 "app_dir": "/home/pi/Desktop/Plasmic/app",
 "tools_dir": "/home/pi/Desktop/Plasmic/nano_lcd_v1_1/rpi_tools",
 "serial_port": "/dev/RS485",
 "output_dir": "/home/pi/Desktop/Plasmic/logs/hmi-captures",
 "poll_seconds": 5}
```
`validate_config` (`backend.py:16-34`): unknown keys rejected (`Unknown connection setting`); the four RPi paths must be absolute; port 1–65535; `poll_seconds` 3–60 (the core's poll loop is fixed at 5 s regardless).

`SSHAdapter` (`backend.py:92-210`):
- `paramiko.SSHClient`, loads `known_hosts`, `RejectPolicy()` (unknown host keys refused), timeouts 7 s; password or key file; agent/look-for-keys only when neither is given. Missing library → `Lack paramiko,Please execute first Install.cmd`.
- `_exec(args, source=None, timeout=20)`: `exec_command(shlex.join(args))`; optional script/JSON written to stdin; reads until EOF (fix dated 2026-09-30); limits 2 MB stdout / 100 kB stderr; exit −1 → `ConnectionError('RPi SSH The connection was interrupted during command execution (the end code was not received)')`; `flock` exit 75 → `The spectrometer is already capturing data, please wait for it to end.`; `flock` exit 124/137 → `spectrometer SDK exceeds 35 seconds;RPi The acquisition has been stopped, please check the spectrometer USB connection`; other non-zero → `RPi command failed (<code>): <last 400 chars>`; local deadline → `TimeoutError('RPi command timeout; do not automatically repeat acquisition')`.
- `status()`: runs `python3 - <app_dir> <tools_dir> <serial_port>` with `remote_read.py` on stdin; expects JSON with dict `power` and `nano`.
- `capture(job_id)` (V1 path): `flock -n -E 75 /tmp/hm-hmi-spectrometer.lock timeout -k 2s 35s python3 <tools_dir>/hardware_verify.py spectrum --app-dir … --outdir <output_dir>/<job_id>`, then SFTP reads `spectrum-1..3.csv`.

`remote_read.py` (runs on the RPi, `remote_read.py:1-65`): imports `nano_verify` (`Client`, `open_port`, `decode_config`, `encode_parameters`) and `HSPY_Read_Status.read_status`; reads `<app_dir>/parameter.json` (keys `Voltage`, `Integration_Time`, `Ontime`, `Offtime`, `Cycle`, `Loopnum`, `Spectra_Interval`) → `result['parameter']`; takes `/tmp/hm-hmi-rs485.lock` (non-blocking flock); opens the serial port; reads HSPY at `address=1`; reads Nano status and config (`client.read(0x100, 18)`), checks revision/CRC; for firmware `1.1.0`, `1.2.0`, `1.2.1` with a session it calls `client.report_power(...)` to refresh the Nano LCD; prints one JSON object with `power`, `nano`, `parameter`, `received_at_unix_s`.

Commands sent by the core (`controller.py:655-677`): `timeout -k 3s 215s python3 -B <tools_dir>/plasma_job.py <action> --app-dir … --tools-dir … --port <serial_port> [--outdir <dir>] [--execute --confirm-high-voltage]`; allowed actions `status`, `prepare`, `stop`, `spectrum`, `plasma_run`, `clear_fault`; local timeout 225 s. Also `plasma_job.py set_parameters … --execute` with the parameter JSON on stdin (`:758-762`), `plasma_job.py cancel --outdir <dir>` over a **second** SSH session, retried up to 40 times at 0.25 s (`:1830-1852`), and `spectrum_analysis.py <command>` (`:1208-1215`). Job folders are named `HMI-<uuid hex>` under `output_dir`; raw captures `CAP-<uuid hex>`.

Results: `remote_result.py` is piped to `python3 - <job dir> <lines JSON>`; it validates `job.json` and every `background/` and `signal/` `spectrum-N.csv` (columns `wavelength_nm,intensity_raw`, ≤ 8192 points, ≤ 2 MB each, same wavelength axis within 0.001), and prints `base64(zlib(JSON{job, background, signal, features}))`; per-frame signal values are included only when `count × pixels ≤ 250 000` (`remote_result.py:10,53,91-94`).

Health and reconnect: status poll every 5 s; state is "fresh" for 20 s; on a dead transport the adapter is dropped and audit `rpi_link_lost` written; auto-reconnect with the paired key, back-off 5 s doubling to 60 s (`controller.py:351-396`); `Automatic connection failed:…` stored as the error.

Pairing (`rpi_pairing.py`, `controller.py:450-501`): maintenance reads the host key (`fetch_host_key`, no auth, fingerprint `SHA256:<base64 no padding>`), confirms it on screen, types the RPi password once; the HMI saves the host key to `data/known_hosts`, creates an Ed25519 key pair `data/rpi_key` / `data/rpi_key.pub` (comment `plasmic-hmi`), logs in with the password, runs `INSTALL_KEY_SCRIPT` to append `restrict <public key>` to `~/.ssh/authorized_keys`, writes `key_file` and `known_hosts` into `data/config.json`, reconnects with the key. Errors: `Please enter RPi account password (only used this time, will not be stored)`, `RPi The host key is different from the fingerprint confirmed on the screen, pairing has been stopped; please read the fingerprint again and verify`, `The process is being executed and cannot be paired.`, `Scheduling is in progress, please stop scheduling first`. The password is never written to disk (test `tests/test_production.py:139-147`); with a password connect (`/api/connect {password}`) it stays in memory in `core.password` for reconnects (`controller.py:411`).

High-voltage idle checks used as interlocks (`backend.py:56-89`): HSPY `valid` and `run_stop_setting == 0`; Nano `valid`, `state` in `IDLE/DONE/FAULT`, `fault == 'NONE'`, `output_command_high` and `output_pin_sample` both `False`. Texts: `HSPY Power readback is invalid`, `HSPY power supply is ON`, `Nano Invalid readback`, `Nano status is... (ready or exporting)`, `Nano fault lock … (STOP cannot be cleared)`, `Nano D13 The high voltage trigger output is not LOW`; fixes `Please ask maintenance personnel to go to ”Maintenance Settings › RPi Connection” click ”Read Status”` and `…Press ”HSPY OFF＋Nano STOP”,Click ”Read Status” again`.

### e.2 PLC — raw Modbus TCP (`plc.py`), now removed from the rig

- Defaults `{'host': '192.168.0.1', 'port': 502, 'unit_id': 1, 'poll_seconds': 5}`; stored in `data/plc_config.json`; host must be IPv4; `unit_id` 1–247; `poll_seconds` 2–60.
- Hand-built frames over a new TCP socket per request (2 s timeout): FC03 reads 30 holding registers from offset 0 (`REGISTER_COUNT = 30`, "40001–40030"); FC06 writes one register with strict echo check, followed by an FC03 read-back (`_checked_write`).
- Register map: syringe `reset` 40001, `channel_1..3` 40002–40004, `aspirate` 40005, `dispense` 40006, volume 40007 (mL × 100, max 5.00); pumps P1–P6 = 40008–40013 (`PUMP_ADDRESSES = i + 6`); shared direction 40027 (0 = CW, 1 = CCW); RPM CW 40029, RPM CCW 40030 (1–1000). `manual-page.html` labels: `PUMPS · 40008–40012`, `VALVES · 40016 / 40017 / 40033`, `SYRINGE · 40001–40007`, `Mixing Tank Agitator 40028` (valves and stirrer were never wired: buttons disabled, "has not yet been connected to the device/Source").
- `PLCMonitor`: one worker thread; command queue with one pending command; manual pump auto-off (default `PUMP_RUN_LIMIT_SECONDS = 30`, max `MANUAL_RUN_MAX_SECONDS = 120`); syringe pulses of 1.0 s; `reserve_flow`/`release_flow` so a running Recipe owns the PLC; state is valid for `max(15, poll_seconds × 3)` s.
- `state()` shape: `{connected, valid, updated_at, age_seconds, error, config, snapshot, pending, last_command, auto_off_seconds}`.
- README:39: Modbus read-back is mostly command registers and cannot prove physical flow; a PC or network failure cannot guarantee pumps stop.

---

## (f) Kiosk and autostart

`Start.cmd`: `cd /d "%~dp0"`; if `offline_bundle\python-runtime\python.exe` is missing → `Please run Install.cmd first.`; else `python.exe server.py --port 8766`, then `pause`. The server opens the default browser itself.

`Start-Demo.cmd`: same with `--demo --port 8766`.

`Start-Kiosk.cmd` ("Production start: HMI server in the background, Edge full screen (kiosk) on this panel"):
1. `start "HMI server" /min "offline_bundle\python-runtime\python.exe" server.py --port 8766 --no-browser`
2. PowerShell loop: try a TCP connect to `127.0.0.1:8766` every 500 ms for up to 30 s. Its exit code is not checked; Edge is opened either way.
3. Edge path: `%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe`, fallback `%ProgramFiles%\…`.
4. `start "" "%EDGE%" --kiosk http://127.0.0.1:8766/ --edge-kiosk-type=fullscreen --no-first-run --disable-pinch --overscroll-history-navigation=0`

`Install-Autostart.cmd`: creates shortcut `Plasmic HMI.lnk` in the current Windows user's Startup folder (`[Environment]::GetFolderPath('Startup')`), `TargetPath` = `Start-Kiosk.cmd`, `WorkingDirectory` = the HMI folder, `WindowStyle=7` (minimised); prints `Autostart installed: the HMI opens full screen after this user signs in.` With `/remove` it deletes the shortcut and prints `Autostart removed.` Then `pause`.

README:22: leave kiosk with Alt+F4 (needs a physical keyboard). README:19: after a power cut or HMI restart a running schedule is marked `Interrupted` and is **not** resumed automatically.

Touch design that goes with kiosk use (README:46,56): HMI's own confirmation dialogs (30 s timeout, `DIALOG_MS = 30000`), paged lists instead of scroll bars, on-screen keyboard and numeric pad, no browser `confirm`/`prompt`.

Install (`Install.cmd` → `Install.ps1`): fully offline. Verifies every file in `offline_bundle\SHA256SUMS.txt`; unpacks `offline_bundle\python-3.12.10-embed-amd64.zip` to `offline_bundle\python-runtime`; rewrites `python312._pth` to end with `Lib\site-packages`, `..\..`, `import site` (relative path so the folder can be moved); bootstraps pip from `wheelhouse\pip-25.0.1-py3-none-any.whl`; checks Python is exactly 3.12.10 64-bit; `pip install --no-index --find-links wheelhouse -r requirements.txt` (`paramiko==4.0.0`, `bcrypt==5.0.0`, `cffi==2.1.1`, `cryptography==50.0.1`, `invoke==3.0.3`, `PyNaCl==1.6.2`, `pycparser==3.0`); import check; prints `Offline installation complete. Start.cmd = RPi connection. Start-Demo.cmd = offline demonstration.`

`build_release.py`: builds `dist/HM_HMI_v1_1_Windows.zip` with prefix `hmi_v1_1/`: a fixed list of source files, everything under `web/`, everything under `offline_bundle/` except `python-runtime`. It never includes `data/` ("excludes environments, credentials and data").

---

## (g) What the README tells the operator to do

Setup, once (README:72-80):
1. Unzip, keep the whole `hmi_v1_1` folder.
2. Run `Install.cmd`.
3. Run `Set-Admin-Password.cmd`, set a 6–8 digit maintenance PIN.
4. Run `Start.cmd` (or `Start-Kiosk.cmd` / `Install-Autostart.cmd` on the production panel); browser opens `http://127.0.0.1:8766/`. `Start-Demo.cmd` is a preview on the same port — do not run both.
5. Long-press the top-left name for 3 s → maintenance: RPi connection → `pair RPi (first) `; operator → add names; Recipe → import a UTF-8 CSV (single round) and publish.
6. Operator: on `Schedule measurement` choose the Recipe and times, press `Start scheduling` at the bottom.

Day to day (README:13-21,28,37-39,41-58):
- No sign-in. Pick your name with the top-bar `operator` button; every run and event records who did it.
- Work is done by **schedule**, not single START: choose Recipe, start (now / at a time), interval (1–1440 min), end (count / until a time / until stopped). A Recipe describes one round; repeats come from the schedule.
- Recipes with `plasma_run`: confirm high voltage once when starting the schedule (tick boxes, the dialog shows the set voltage); afterwards every slot runs automatically; the bottom bar shows `⚡ contains high voltage`. HSPY limit 700 V.
- Bottom buttons: start / `pause` / `continue RESUME` / `Stop`. Pause stops pumps and resumes from the same place.
- Manual pumps and syringe on `manual operation` only while no schedule is busy (paused is allowed); each pump has an auto-stop time of 1–120 s (default 30); OFF always works; blocked when the RPi reports HV not idle.
- HSPY OFF / STOP is always available.
- Alarms come from live data; acknowledge in the banner. Overrun slots are skipped and logged, a failed run stops the schedule, three consecutive "could not start" stop the schedule.
- After a power cut or restart, check the equipment and start the schedule again by hand.
- Safety wording: there is no software E-STOP; `STOP` "is not a safety level emergency stop"; use the physical stop. "Software inspection cannot replace physical emergency stop and hardware interlocking."
- Language switch `Traditional/simple/EN` in the top bar.
- Maintenance only (PIN): calibration apply, raw spectra, plasma parameters, Recipes, RPi pairing/tools, operator list.

`VALIDATION.zh-TW.md`: dated 2026-09-28; 11 unit tests passed; STOP and timeout were tested only against simulated PLC/communication; no on-rig STOP, cable-pull or reset-transient tests were done ("None currently available SSH certificate"). It lists five field tests still to do. Its last line ("At present, the backend clearly rejects the load. `plasma_run`") is older than README:20, which says production HV was opened on 2026-09-30.

---

## Design fragments: `admin-page.html`, `manual-page.html`

Both are template fragments (custom tags `sc-if`, `sc-for`, `{{ }}` bindings) used only by `prepare_original_ui.py` to rebuild `web/index.html` from the V1 original (README:61); the live page is `web/index.html`.

- `admin-page.html`: header comment `Administrator settings`; tab buttons from `adminTabs`; note text `CSV Recipe · Administrator permissions are verified by the backend`; an iframe to `/workflow/` titled `CSV Recipe and testing process`; a parameter table and a user table whose controls are all disabled (`has not yet been connected to the device/Source`); footer buttons `New account ADD USER`, `restore REVERT`, `Storage SAVE`.
- `manual-page.html`: header comment `manual mode`; a notice bar (`manualNotice`); left panel `peristaltic pump PUMPS · 40008–40012` with shared `direction …` and `speed … RPM` buttons and per-pump rows (name, address, seconds `Second SEC`, `ON`, `OFF`); right panels `solenoid valve VALVES · 40016 / 40017 / 40033` (disabled), `syringe pump SYRINGE · 40001–40007` (channel buttons `CH1–CH3`, `volume` with `−` / value `mL` / `+`, `Liquid extraction ASPIRATE`, `Liquid injection DISPENSE`, `RESET`), `Mixing Tank Agitator 40028` (disabled). Visual language: `.blueprint` panels with four `corner` marks, `var(--font-heading)`, `var(--color-accent)`, 44–48 px touch targets.

---

## Things I could not determine

1. Whether a PIN is currently set on this PC, and whether it is a PIN or a legacy password (that is inside `data/`, which I did not open).
2. What `data/config.json` and `data/plc_config.json` hold on this PC.
3. Internals of `scheduler.py`, `csv_workflow.py`, `calibration.py`, `calibration_config.py`, `measurements.py`, `spectrum_store.py`, `rpi_tools/*` — only their call sites were read.
4. The `/test/` page contents and the template engine in `web/original/support.js`.
5. How the server answers when an exception type outside `ValueError / RuntimeError / TimeoutError / OSError` escapes `do_POST` — the handler has no catch-all, so no JSON reply is produced by this code; the exact client-visible result depends on the standard library.
6. Whether Edge kiosk behaves as intended on this PC (nothing was run).

---

## Cross-check

Done 2026-10-06 by a second reader. Method: `server.py` (473 lines), `auth.py` (133), `backend.py` (238), `rpi_pairing.py` (99), `plc.py` (486), `controller.py` (2052), `README.md`, the five `.cmd` launchers, `config.example.json`, `ui_features.json` were read completely and compared with this report line by line; `web/index.html` lines 1-140, 204-343, 384-503, 1042-1874, 1950-2322 were read for the login / session code. Nothing was run. No `data` folder was opened.

### Claims checked against the source and found correct

1. `admin_only` set, verbatim, and the extra `/api/rpi` rule (`server.py:278-285`).
2. PIN storage shape and algorithm: PBKDF2-HMAC-SHA256, 32 random salt bytes, 600 000 iterations, `kind` field, `.tmp` + rename, sessions cleared on write (`auth.py:59-75`).
3. Validation rules and the three `ValueError` texts of `check_secret` (`auth.py:19-30`).
4. Lockout arithmetic `min(900, 30 * 2 ** lockouts)`, threshold 5, counters reset on success (`auth.py:102-113`).
5. Session: `secrets.token_urlsafe(32)`, in memory, 600 s sliding, dropped when `(st_mtime_ns, st_size)` of the file changes (`auth.py:83-88,115-129`).
6. `/api/auth` uses `renew=False`; every POST and the admin GETs renew (`server.py:130,185,192,267`).
7. POST gate order: body size, Host + `X-HMI-Token` + `Origin`, content type, JSON object, test-page 404, `allowed()`, login / logout before the admin gate (`server.py:248-285`).
8. 202 status list and the exception → status mapping (`server.py:403-411`); GET errors → 404 (`server.py:245-246`).
9. Every row of the GET and POST tables in (b) (paths, who, bodies, core calls).
10. Recipe delete needs a session **and** the PIN again in the body; a wrong entry there counts toward the lockout (`server.py:375-377`, `auth.py:90-91`).
11. `--set-admin-password` flow and all its English console texts; it always writes `ROOT/data/admin.json`; demo mode reads the same file (`server.py:438-447`, `controller.py:120-122`).
12. Default port 8765 in code, 8766 in every launcher (`server.py:431`; `Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`).
13. Demo mode: `PLCMonitor(start=not demo)`, `_auto_connect` returns at once, the five refusal texts and where each is raised (`controller.py:129,385,421,453,464,578,593,681,743,845,1187,1549,1787`).
14. SSH adapter: `RejectPolicy`, 7 s timeouts, `_exec` limits and error texts, `flock` exit codes (`backend.py:98-169`).
15. Pairing: fingerprint format, `known_hosts` and `rpi_key` under the data folder, `restrict` prefix, key comment `plasmic-hmi`, the four error texts (`rpi_pairing.py`, `controller.py:461-501`).
16. The second, older HTTP handler inside `controller.py` (its own `admin_only`, texts `This operation requires administrator login`, `Include RPi/Plasma action Recipe requires administrator login`, assets `app.js` / `style.css` that are not in `web/`) (`controller.py:1871-2052`).
17. UI login code: `HOLD_MS = 3000`, `POST /api/admin/login {pin}`, `sessionStorage['hmi_admin_session']`, keypad order, 8-digit limit in PIN mode, `_pw` cut to 64, logout clears cached spectra and the Recipe draft (`web/index.html:1065,1348-1381,2188-2207`).
18. PLC register map and the labels in `manual-page.html` (`PUMPS · 40008–40012`, `VALVES · 40016 / 40017 / 40033`, `SYRINGE · 40001–40007`, `Mixing Tank Agitator 40028`) (`plc.py:13-23,98-114`; `manual-page.html:13,39,55,81`).
19. README statements quoted in (c), (f), (g) (lines 13-22, 28-33, 37-39, 45-61, 72-80).

No statement of this report was found to be wrong. The items below are corrections of emphasis and additions.

### Corrections made in the text above

- Fact 4: added that during a lock every entry is refused before hashing, the correct PIN included, and that those refusals are not counted (`auth.py:94-97`).
- c.6: the sentence about "10 minutes of inactivity" was misleading for anyone re-implementing it. Added the two pages on which the shell renews the session by itself (schedule preview every 30 s, spectrum list every 10 s), so a logged-in panel left there never expires.
- b.3, row `/api/rpi` other actions: added (1) a missing or unknown `action` needs admin and then gives 400; (2) `status` is refused during a flow; (3) `status` does not update the cached RPi status used by interlocks and `/api/state`, although the interlock "fix" texts tell the user to press `read status`.

### Additions (not in the report before)

- `/api/admin/login` with a `pin` that is not a string (a JSON number, or the key missing) answers 409 `has not been set for maintenance PIN` even when a PIN is configured, because `verify` tests `not isinstance(secret, str) or not self.configured` in one condition (`auth.py:92-93`). The shell always sends a string, so operators do not see this; a new client that sends a number would.
- Failed logins are not audited (already stated); in addition the audit row `maintenance_login` carries no detail, so the log cannot tell which panel logged in.
- Nothing ties a session to a client: any local process that can read the token from `GET /api/bootstrap` (no authentication) and knows the PIN can log in; and because lockout counters are global, any local process can also lock maintenance out (5 wrong POSTs → 30 s, then doubling). This follows from `server.py:124-128` and `auth.py:43-45`; it is by design for a single kiosk panel.
- `GET /api/bootstrap` returns the full RPi connection config (host, user name, key file path, `known_hosts` path, RPi directories) to any local client without a session (`server.py:125-128`). No secret is in it, but a rebuild should decide deliberately what an unauthenticated page may read.
- The UI text for a missing PIN says to restart the HMI after running `Set-Admin-Password.cmd`. By reading, a restart is not needed: `configured` is evaluated on every call (`auth.py:55-57`), `/api/auth` reports it live and the shell re-reads it every 10 s (`web/index.html:1261,1562-1566`). Not tested.
- Operator-level endpoints that can start high voltage are exactly two: `/api/schedule/start` (needs `confirm_hv: true` and an operator name from the list) and `/api/calibration/start` (needs `confirm_high_voltage: true`; no operator name required — `cal.new_record` stores whatever `core.operator` is, possibly `''`) (`controller.py:1185-1206,1547-1556`).
- The live shell never calls `/api/recipes/run` or `/api/start` (checked with a search of `web/index.html` and the three sub-apps; only the legacy `/workflow/` page calls `/api/recipes/run`). So in the shipped UI a Recipe can only be started through a schedule or a calibration, although the server still offers a direct, PIN-gated start.
- Endpoints offered by the server that no shipped page calls: `GET /api/events`, `GET /api/schedule`, `GET /api/operators`, `GET /api/result/latest`, `POST /api/start`, `POST /api/stop`, `POST /api/plc` (alias). `GET /api/workflow-state` and `POST /api/recipes/run` are called only by the legacy `/workflow/` page; `/api/plc-config`, `/api/plc-refresh`, `/api/refresh`, `/api/capture`, `/api/history`, `/api/captures/…` only by the hidden test page.

### Still open after the cross-check

- What the owner means by "operation password": the original has no operator password and no way to set the PIN from the screen (console only). See the digest, open questions.
- Contents of `data/admin.json` (not opened by rule).
