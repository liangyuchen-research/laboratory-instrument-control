# current-ec — "Plasmic Electronic control" (EC) as found in `C:/Users/PlasmaLab/Desktop/autonomous control`

Read-only investigation, 2026-10-06 04:05–04:15 local time. Nothing in the three folders was changed, run, or connected to.
All paths below are relative to `C:/Users/PlasmaLab/Desktop/autonomous control/` unless stated.

Disclosure: my first directory glob of the project root returned some file *names* under `pc/data/runs/` and `pc/runtime/python/` before I narrowed the pattern. No file in `pc/data` or `pc/runtime` was opened, and nothing in this report is taken from them.

---

## 0. READ THIS FIRST — the folder was being edited by someone else while I read it

The EC folder is **not** in the state its own documents describe. Somebody (another session or agent; I could not determine who) was converting it from "two discharge sequences, 3 background frames" to "one flow, 1 background frame" during my read. I made none of these changes.

### 0.1 What I observed (file times from the PC clock)

| Time | Observation |
|---|---|
| 04:09:07 | `rpi/tcpd/plasma_quick.py` and `tests/test_quick.py` **do not exist** (historical-development-notes.md, the work log, the guide and `READ-ME-FIRST.txt` all still describe them). New files: `rpi/tcpd/plasma_measure.py` (04:04:20), `tests/test_measure.py` (04:05:24); `tests/simulated_rig.py` touched 04:04:20. `rpi/tcpd/plasmic_tcpd.py` was still the 1.3.0 two-sequence version (78306 bytes, dated 10-05 23:28). I read that version completely. |
| 04:10:47 | `rpi/tcpd/plasmic_tcpd.py` rewritten at 04:10:12 (78806 bytes): single `flow`. Also rewritten at 04:10:29–04:10:43: `rpi/tcpd/plasma_measure.py`, `rpi/tcpd/config.example.json`, `rpi/install_rpi.sh`, `installer/install.py`, `tests/test_measure.py`, `tests/test_installer.py`. I re-read the changed parts. |
| 04:12:27 | `tests/test_tcpd.py` rewritten (04:11:36, again 04:12:25, 44150 bytes). |
| 04:19:16 | No file changed after 04:12:25 (last check). |

**Not yet touched at 04:19:16** (still the 1.3.0 two-sequence version): `pc/app.py` (10-05 23:32), `pc/web/app.js` (23:39), `pc/web/index.html` and `style.css` (10-06 00:03), `pc/schedule.py`, `pc/plasmic_client.py`, `pc/netcheck.py`, `tests/test_app.py` (23:34), `docs/guide-source/installation-and-usage.src.html` (00:05), the built `detailed-guide.html` (00:09), `READ-ME-FIRST.txt` (00:06), `historical-development-notes.md` (00:09), `docs/work record.md` (00:09), `docs/guide-source/README.txt` (00:05), the seven screenshots.

Anyone using this report must re-check with a grep for `sequence|quick|classic|flow` before relying on a line number in the files listed above; the conversion may have continued.

### 0.2 Three states to keep apart

1. **"1.3.0 as documented and as installed on the RPi"** (installed 2026-10-05 23:58 per `docs/work record.md:302`): two sequences `classic` / `quick`, both with 3 background frames; `plasma_quick.py` on the RPi at `/home/wow/Desktop/Plasmic/tcpd/`. The RPi still runs this.
2. **Working tree, RPi side + its tests (04:10–04:12)**: one measuring flow chosen by a server config key `flow` (`unified` = `plasma_measure.py`, 1 background frame taken *before* the discharge; `verified` = the untouched `plasma_job.py`, 3 background frames, fallback by editing `config.json`). No command or UI can choose.
3. **Working tree, PC side + UI + `test_app.py` + all documents**: still state 1.

### 0.3 Consequences of the mixed state (derived from reading the code; nothing was run)

- `VERSION` is still `'1.3.0'` in both `rpi/tcpd/plasmic_tcpd.py:46` and `pc/app.py:46`. The installer compares versions (`installer/install.py:495-503`): with equal versions it only asks "To reinstall RPi end? (Generally not required)" with default **No**, so `Install.cmd --yes` (the documented update command, `historical-development-notes.md:88`) would **not** push the new server to the RPi, which already reports 1.3.0. The version has to change (or `--reinstall` be used) for the update path to work.
- New server + old PC app: `hello` no longer carries `sequences`, so `app.js:179` `knownSequences()` yields `['classic']`; the “fast” button is disabled and the note says “RPi is an old version, only the original sequence...” (`app.js:526`), which is false. `sequence()` returns `'classic'`, `Link.sequence_fields('classic')` returns `{}` (`app.py:498-499`), so a plain `plasma_run` is sent and the unified flow runs. `status.estimates` is gone, so `estimate()` (`app.js:182-186`) falls back to `{total_s: now.estimate_s}` and the "Click to approx. N starts discharging in seconds" text disappears (the new server puts it in `status.start_s`). The banner uses `PHASE_STEPS[job.sequence] || PHASE_STEPS.classic` (`app.js:307`): with the new phases `ramp` and `off` the classic table has no entry, `?? 0` puts the marker back on step 1. `Store.save_run` keeps `sequence` (now absent), not `flow` (`app.py:236-238`).
- `tests/test_app.py:132-171` and `:314-337` assert `sequences == ['classic', 'quick']`, `estimates`, `started['sequence']`; they cannot pass against the new server.
- `historical-development-notes.md:80,91` and the guide (`…src.html:1006`) tell people to run `tests\test_quick.py`, which no longer exists. The test count "149" (`historical-development-notes.md:80,82`) is stale; by counting `def test_` at 04:12: `test_tcpd.py` 48, `test_app.py` 37, `test_installer.py` 37, `test_flash_nano.py` 13, `test_measure.py` 16 = 151 (not run by me, and `test_tcpd.py` was still changing).
- The source of the 1.3.0 `plasma_quick.py` is no longer in this folder. `docs/backup/` holds only `before-schedule-20261005.zip` and `plasmic_tcpd-1.2.0-from-rpi-20261005.py` (names seen, not opened). A copy should still be on the RPi.

---

## 1. What the system is

```
Windows PC ── TCP 8767, one JSON object per line ──► RPi (Advantech UNO-220, 32-bit userland, Python 3.13)
 pc/app.py  (local web UI 127.0.0.1:8766)             rpi/tcpd/plasmic_tcpd.py  (systemd unit plasmic-tcpd)
                                                       ├─ starts the tools as separate processes:
                                                       │    rpi_tools/plasma_job.py <action>      (verified, protected)
                                                       │    tcpd/read_status.py                   (verified, protected)
                                                       │    tcpd/plasma_measure.py                (new, 2026-10-06; never run on the rig)
                                                       ├─ USB-RS485 /dev/RS485 9600 8N1: HSPY supply (addr 1, limits 700 V / 0.1 A), Arduino Nano fw 1.2.1 (addr 2)
                                                       ├─ GPIO pin 3 → Nano D12 (trigger); Nano D13 = pulse output
                                                       └─ USB spectrometer (vendor SDK lib_linux_ARM_2.7.11.so through rpi_tools/hardware_verify.py)
```

There is **no pump, PLC, valve or balance code anywhere** in EC (`historical-development-notes.md:15`, `docs/work record.md:21`). The only trace is the empty interlock hook `Controller.permit_problems()` (`rpi/tcpd/plasmic_tcpd.py:1071-1078`).

Verified on the real rig so far (`historical-development-notes.md:57`): flashing the Nano, the direct cable network (RPi `192.168.1.1` user `wow`, PC `192.168.1.2`), the installer (full and tcpd-only), status reads, the no-discharge capture, 30 V discharges with the *classic* order. **Not verified on the rig:** stop during a discharge, the watchdogs, the schedule, the quick order, and everything written on 2026-10-06 (`plasma_measure.py`).

---

## 2. (a) The PC program — `pc/app.py` (1131 lines, standard library only)

Imports its neighbours after `sys.path.insert(0, <its own folder>)` (`app.py:17`; the embeddable Python does not add the script folder itself): `netcheck`, `plasmic_client`, `schedule`.

### 2.1 Constants (`app.py:46-69`)

| Name | Value |
|---|---|
| `VERSION` | `'1.3.0'` |
| `TCPD` | `../rpi/tcpd/plasmic_tcpd.py` (started with `--sim` in demo mode) |
| `GUIDE` | `../detailed-guide.html` |
| `SITE` | `../installer/site.json` |
| `RUN_ID` | `(?:HMI|CAP|LITE)-[0-9a-f]{32}` |
| `DEFAULT_CONFIG` | `{'rpi_host': '192.168.1.1', 'rpi_port': 8767, 'token': ''}` |
| `POLL_SECONDS` | `1.0` |
| `SEARCH_SECONDS` | `20.0` |
| `UI_WATCHDOG_SECONDS` | `10.0` |
| `SCHEDULE_JOB_SECONDS` / `SCHEDULE_LOST_SECONDS` | `360.0` / `30.0` |
| `CANCEL_REASONS` | `stop`→“Stop pressed”, `watchdog_timeout`→“The control connection timed out and the server automatically stopped.”, `owner_disconnected`→“The control connection is interrupted and the server automatically stops”, `shutdown`→“RPi Server is down” |
| `SEQUENCES` | `{'quick': 'fast', 'classic': 'original'}` (still present at 04:19) |
| `STATIC` | `'/'`→`index.html`, `'/app.js'`, `'/style.css'` — **the only static files served**; a new JS/CSS/image file needs an entry here |

### 2.2 Classes

- **`PinLock`** (`:118-178`) — see 2.6.
- **`Store`** (`:181-256`) — everything on disk under the data folder. `config` (validated by `validate_config`, `:92-106`; unknown keys rejected); `run_rows` (list of `.meta.json` contents, newest first); `events` (`deque(maxlen=200)`, loaded from the tail of `events.jsonl`). Methods: `save_config`, `event(text, level='info')` (text cut at 400 chars, appended to `events.jsonl`), `recent_events(60)` newest first, `run_ids()`, `save_run(result)` (writes `runs/<job>.json` compact and `runs/<job>.meta.json`; meta keys `job, kind, started_at, finished_at, voltage_v, model, integration_ms, pixels, signal_frames, background_frames, parameters, sequence` plus `saved_at, peak_nm, peak_value, wait_s`), `list_runs(200)`, `load_run(id)` (id checked against `RUN_ID`). `write_json` (`:76-89`) writes `.tmp` then renames, retrying 10× on `PermissionError`.
- **`run_csv(run)`** (`:259-268`) — plasma: columns `wavelength_nm, background_mean, signal_mean, difference`; capture: `wavelength_nm, mean, frame_1…`; UTF-8 with BOM.
- **`Link`** (`:271-557`) — the one TCP connection to the RPi.
  - Own daemon thread `_loop` (`:369-390`): connect (`_connect`, timeout 3 s, retry 2 s growing ×1.5 to 10 s), then `status` once per `POLL_SECONDS`.
  - `request_lock` serialises requests on the connection; `lock` guards fields.
  - `_on_status` (`:393-419`): keeps the newest status; on the first status marks old non-done jobs as handled; calls `_sync_new_nano`; **screen watchdog**: when the running job is high voltage, is `own_job`, and `/api/state` has not been asked for more than `UI_WATCHDOG_SECONDS`, it calls `stop()` once and logs “The operation screen exceeds 10 has not responded for seconds and has automatically stopped discharging.”; when a job ends and was not handled, `_finished` fetches `result` (`frames=True`) and saves it.
  - `_sync_new_nano` (`:421-447`): auto-`prepare` once per Nano power-up when the Nano has no `active_parameters`, firmware is `1.2.0`/`1.2.1`, state `IDLE`, fault `NONE`, supply OFF at 0 V, parameter file valid. Can be disabled with `Application(..., auto_sync=False)`.
  - `_look_around` (`:328-345`): while disconnected, every 20 s one UDP discovery (not for 127.x / localhost; not in demo).
  - `view()` (`:478-487`) → `{connected, host, port, error, server (the hello reply), pending, status, status_age_s, own_job, found, addresses}`.
  - `sequence_fields(name)` (`:490-503`), `command(cmd, attended=True, **fields)` (`:505-526`; sets `own_job` only for an attended `plasma_run`), `raw(line)` (`:528-536`), `stop()` (`:538-549`; **always over a fresh connection** via `plasmic_client.stop`, needs no token, never waits behind another request), `reconnect()`, `close()`.
- **`Schedule`** (`:566-825`) with `ScheduleEnded` (`:560-563`) — Schedule measurement. One slot = one `plasma_run` with the parameters in the RPi's `parameter.json`. `START_FAILURES = 3`, `KEEP_SLOTS = 2000`. Record persisted to `schedule.json`: `{id, status, created_at, finished_at, spec, planned_total, last_slot_at, estimate_seconds, voltage_v, sequence, next_index, next_slot_at, slots[], counts{}, error}`; slot: `{index, planned_at, status, job, started_at, finished_at, note}`. Methods: `_load` (a schedule found active at start is marked `interrupted`, never resumed), `view(slots=60)`, `preview(raw, sequence)`, `_ready()`, `start(raw, sequence)` (spawns the daemon thread `_loop`), `request_end()`, `stop()`, `close()`, `_wait_until`, `_wait_for_job`, `_loop` (`:769-825`). A scheduled discharge is sent with `attended=False`, so it is **not** tied to the screen watchdog (`:786`).
- **`Application`** (`:828-951`) — owns `Store`, `PinLock(data/pin.json)`, `Link`, `Schedule`; in demo mode starts the simulator as a subprocess (`[python, plasmic_tcpd.py, '--sim', '--bind', '127.0.0.1', '--port', '8767', '--sim-dir', data/sim]`, log in `sim/tcpd.log`) and forces the config to 127.0.0.1. Methods `state()`, `command(body, unlocked)`, `diagnose()`, `find()`, `raw(line)`, `close()`.
- **`ExclusiveHTTPServer`** (`:954-962`) — `ThreadingHTTPServer`, `allow_reuse_address = False`, `SO_EXCLUSIVEADDRUSE`, daemon request threads.
- **`make_handler(app, csrf)`** (`:965-1083`) — the HTTP handler class.
- **`main()`** (`:1086-1127`) — options `--demo`, `--port` (default 8766), `--data`, `--no-browser`, `--set-pin`. Data folder: `pc/data` (or `pc/data-demo` with `--demo`). Binds `127.0.0.1` only. A taken port ends with “port 8766 is already used by another program (maybe a Plasmic electronic control). Please close it first. ”. Prints “Plasmic Electronic control 1.3.0: http://127.0.0.1:8766/” and “Closing this window will end the program. ”, opens the browser, serves forever.

### 2.3 Threads

Main thread: `server.serve_forever()`. One daemon thread per HTTP request. `Link._loop` (daemon, lifetime of the program). `Schedule._loop` (daemon, one per started schedule). Demo: one child process (the simulator). Locks: `Link.request_lock`, `Link.lock`, `Store.lock`, `PinLock.lock`, `Schedule.lock`; events `Link.wake`, `Link.closed`, `Schedule.stop_request`.

### 2.4 Local HTTP API

**GET** (only the Host check applies; no token):

| Path | Reply |
|---|---|
| `/`, `/app.js`, `/style.css` | files from `pc/web/` |
| `/guide` | `detailed-guide.html`, served with `style-src 'self' 'unsafe-inline'` |
| `/api/bootstrap` | `{csrf, version, demo, config: {rpi_host, rpi_port, token_set}, ui_watchdog_s}` — the RPi token itself is never sent to the page |
| `/api/state` | `{version, demo, link, latest_run, events, pin_set, schedule, unlocked}`; **each call also feeds the screen watchdog** (`Application.state` → `link.ui_alive()`, `:852`); `unlocked` is computed from the `X-Unlock` header |
| `/api/runs` | `{runs: [meta…]}` (max 200) |
| `/api/runs/<job>` | the full stored run |
| `/api/runs/<job>.csv` | CSV as a download (`Content-Disposition: attachment`) |
| anything else | 404 `{error}`; `ValueError`/`OSError` also become 404 |

**POST** (body: JSON object, `Content-Length` ≤ 100 000):

| Path | Body → reply |
|---|---|
| `/api/command` | `{cmd, …}` → see the command table below |
| `/api/raw` | `{line}` → `{reply}`; one line forwarded verbatim to the RPi (60 s timeout). Refuses `plasma_run`, `set_parameters`, `auth` (“… Please use the button on the screen (requires confirmation window), the terminal will not forward this command.”) and, while a schedule is active, `prepare`, `clear_fault`, `spectrum` (`:927-940`) |
| `/api/diagnose` | `{}` → `{steps: [{key, title, ok, detail, fix}]}`, keys `ip, ping, port, hello, token, device` (`netcheck.diagnose`) |
| `/api/find` | `{}` → `{found: [{host, port, version, name, sim, allowed}], host, port, addresses}` (`netcheck.find_servers(deep=True)`) |
| `/api/unlock` | `{pin}` → `{session}`; logs “Maintenance PIN Unlock” |
| `/api/lock` | `{}` → `{locked: true}` (drops the session in `X-Unlock`) |
| `/api/config` | `{rpi_host, rpi_port, token, clear_token?}` → `{saved: true}`; PIN-gated; refused in demo (“simulation mode is fixedly connected to the local simulator”); an empty `token` keeps the saved one; then `link.reconnect()` |

Error mapping (`:1073-1082`): `PermissionError` → 403 `{error, code: 'locked'}`; `PlasmicError` → 409 `{error, code}`; `ValueError` → 400; `RuntimeError` → 409; `OSError` → 500.

**Commands of `/api/command`** (`Application.command`, `:858-913`):

| `cmd` | Fields | What happens |
|---|---|---|
| `stop` | — | `schedule.request_end()` first, then `link.stop()` (fresh connection). Event “has sent stop(HSPY OFF＋Nano STOP) …” |
| `refresh` | — | TCP `status` with `fresh: true` |
| `schedule_preview` | `schedule`, `sequence` | plan without starting: `{spec, planned_total, last_slot_at, estimate_seconds, sequence, voltage_v, warnings}` |
| `schedule_start` | `schedule`, `sequence`, `confirm_high_voltage: true` | 400 “You must first check the high voltage confirmation in the confirmation window.” without the confirmation; → `{accepted, schedule}` |
| `schedule_stop` | — | `{accepted, stop}`; also stops the running discharge |
| `prepare` | — | TCP `prepare`; event “Synced Nano parameter” |
| `clear_fault` | — | TCP `clear_fault` |
| `spectrum` | `integration_ms` (default 100) | TCP `spectrum`; event “Start capturing spectrum (no discharge)” |
| `plasma_run` | `confirm_high_voltage: true`, `sequence` | TCP `plasma_run`; event “Start plasma:N V,Estimate N seconds (quick sequence)” |
| `set_parameters` | `parameters` (7 fields) | **PIN-gated** (“Modifying plasma parameters requires maintenance PIN”); TCP `set_parameters` then `prepare`; reply gains `synced` / `sync_error` |
| `sim_fault` | `fault` | demo only |
| other | | 400 “Unsupported command” |

While a schedule is active `prepare, clear_fault, spectrum, plasma_run, set_parameters` are refused with 409 “Scheduled measurement is in progress: press ”Stop Schedule” first before manual operation” (`:877-878`).

### 2.5 Protections

- **Bind**: `127.0.0.1` only; exclusive port.
- **Host check** on every request (`trusted()`, `:970-972`): the `Host` header must be exactly `127.0.0.1:<port>` or `localhost:<port>`; otherwise 403 `{'error': 'Invalid host'}` (GET) / `'Invalid local request'` (POST).
- **Origin check** on POST (`:1032-1036`): `Origin` must be absent, `http://127.0.0.1:<port>` or `http://localhost:<port>`.
- **Page token** on POST: header `X-Token` must equal `csrf` (`secrets.token_urlsafe(32)`, new at every program start, compared with `hmac.compare_digest`). The page gets it from `/api/bootstrap`. After a restart the page's old token gives 403 “Invalid local request”; `app.js:101-108` then re-fetches bootstrap and retries once.
- **Headers on every reply** (`respond`, `:974-992`): `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and
  `Content-Security-Policy: default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'`
  (for `/guide` only: `style-src 'self' 'unsafe-inline'`). Consequences for anything merged into this page: no inline `<script>`, no inline event handlers, no `style="…"` attributes in the markup, no CDN scripts/fonts/styles, no `fetch`/WebSocket to another origin or port (`connect-src` falls back to `default-src 'self'`), no framing. The guide may carry inline styles but **no script** (`docs/guide-source/README.txt:16`).
- `log_message` is silenced; there is no access log.
- Not present: TLS, user accounts, any authentication of the person at the screen other than the optional PIN below.

### 2.6 The PIN lock (`PinLock`, `app.py:118-178`)

- It is a **maintenance PIN**, optional, 6–8 digits (`\d{6,8}`, else “Maintenance PIN Must be 6–8 digits”).
- Stored in `pc/data/pin.json` as `{salt (32 random bytes, hex), digest (hex), iterations: 600000}`; PBKDF2-HMAC-SHA256.
- **Set only from the command line**: `app.py --set-pin` (asked twice with `getpass`), started by `Set-PIN.cmd`. There is no screen to set or change it. Removing it = deleting `pc\data\pin.json` (message printed at `:1108`).
- `unlock(pin)` → a session string (`secrets.token_urlsafe(32)`), valid `SESSION_SECONDS = 600`, renewed at each use (`allowed`, `:165-174`). Sessions live in memory only. The page keeps it in the JS variable `unlockSession` and sends it as header `X-Unlock`.
- Wrong PIN: “Maintenance PIN error”; after 5 failures a lockout of `min(900, 30·2^n)` seconds (“Too many attempts, please N Try again in seconds”); not configured: “has not been set for maintenance PIN”.
- **What it guards: only two things** — `set_parameters` (`:899-900`) and `/api/config` (`:1061-1062`). `allowed()` returns True when no PIN file exists. Starting a discharge, starting a schedule, capture, prepare, clear fault, the TCP terminal and stop are all open without it (`tests/test_app.py:505` "operating stays open").
- There are no operators, roles or names anywhere in EC (`docs/work record.md:94` lists "Operator list" among the things not done).

### 2.7 What is kept under `pc/data` (from the code and the guide `…src.html:975-982`; the folder was not opened)

| File | Written by | Content |
|---|---|---|
| `config.json` | installer, “connection setting”, “Use this address instead” | `{rpi_host, rpi_port, token}`. Absent → `installer/site.json`'s `rpi_host` → `192.168.1.1` |
| `events.jsonl` | `Store.event` | one `{time, level, text}` per line; `level` is `info` / `warn` / `error` |
| `runs/<job>.json`, `runs/<job>.meta.json` | `Store.save_run` | the full `result` reply (compact) and its summary |
| `pin.json` | `--set-pin` | PIN hash (only when set) |
| `schedule.json` | `Schedule` | the latest schedule only, at most 2000 slots |
| demo: `pc/data-demo/…` with `sim/tcpd.log`, `sim/parameter.json`, `sim/captures/` | | |

The folder is the user's real data since 2026-10-05 and points at `192.168.1.1`; it must not be deleted (`historical-development-notes.md:69`).

### 2.8 `pc/schedule.py` (110 lines) — time arithmetic only

`MINUTE = 60` (tests set it to 1), `MIN_INTERVAL_MINUTES = 1`, `MAX_INTERVAL_MINUTES = 1440`, `MAX_COUNT = 1000`, `MAX_SPAN = 31 days`, `ACTIVE = ('waiting', 'running', 'stopping')`, `STATUS_LABEL`, `SLOT_LABEL` (Chinese labels, `:22-25`). Functions `local_now`, `next_clock_time(hhmm, after)`, `validate(raw)` → spec `{first_slot, interval_seconds, interval_minutes, start: {mode: now|at, time}, end: {mode: count|until|continuous, …}}`, `slot_time`, `last_index`, `planned_total`, `next_due_index(spec, after_index, now, grace_seconds=5)`. Slot *i* is due at `first + i × interval`; slots that pass during a run are reported as `skipped_overrun`, never queued. The docstring says it follows the old HMI v1.1 `scheduler.py`.

### 2.9 `pc/plasmic_client.py` (199 lines)

`PROTOCOL = 1`, `DEFAULT_PORT = 8767`, `REPLY_SECONDS = {stop: 120, prepare: 60, clear_fault: 60, set_parameters: 60, status: 40, spectrum: 40, plasma_run: 40, result: 60}` (default 15). `PlasmicError(code, message)`. `Client(host, port, token, timeout)`: `connect()` (sends `hello`, checks `protocol`, sends `auth` when `auth_required`), `raw(line)`, `request(cmd, **fields)`, `wait_job(on_progress, poll_seconds)`, `run_plasma()` (plain `plasma_run`, no sequence). Module function `stop(host, port)` (own short connection, literally `{"cmd":"stop"}`). Also a command line tool.

### 2.10 `pc/netcheck.py` (370 lines, skimmed in full)

`local_ipv4()`, `same_subnet`, `discover(port, timeout, hosts)` (UDP broadcast `b'PLASMIC-DISCOVER 1'` out of every local address plus direct questions), `port_open`, `sweep` (TCP knock on x.x.x.1–254 of at most three private /24 networks), `arp_table()` (`arp -a`), `find_servers(port, deep, hosts)`, `find_ssh_hosts`, `ping` (the `ping` program), `probe(host, port, token)` → `{tcp, hello, authed, code, error}`, `diagnose(host, port, token, device)`. It never sends a device command. Tests must replace `netcheck.local_ipv4` by an empty list (`historical-development-notes.md:68`; done at `tests/test_app.py:531`).

---

## 3. (b) The web UI — `pc/web/` (plain HTML/JS/CSS, no dependency, no build step)

### 3.1 `index.html` (335 lines) — structure and exact texts

- `<header class="top">`: brand “Plasmic electronic control” + version; `#simBadge` “Practice mode · No real device connected”; `#btnLock` “🔒 Lock” (shown only when a PIN is set and unlocked); `#btnDiagnose` “connection diagnosis”; `#btnSettings` “connection setting”; link “illustrate” → `/guide` (new tab); `#btnStop` “■ stop” with “Turn off high voltage (HSPY OFF＋Nano STOP) ”.
- `#banner` (title, detail, `ol#bannerSteps.phases`, progress bar, “knew”).
- `section.tiles`: “① connection”, “② high pressure”, “③ What can you do now?.
- `main.grid`, three columns:
  - Left: `#cardPower` “HSPY high voltage power supply”/“supplies high voltage for discharge” (Set voltage, current limit setting, output voltage, output current); `#cardNano` “Nano Pulse Controller”/“Cut high voltage into pulses one by one” (fault, pulse output (D13) , Pulse completed/wheel, firmware, LCD The first line should show); the safety note “”Stop” on the screen is a software stop, not a safety-level emergency stop. Before repairing or wiring, please use a physical switch to disconnect the high-voltage power supply from the Every instruction of 24 V drive power supply. ”
  - Middle: `#welcome` “Not connected yet RPi” (three cases, buttons “automatic search RPi”“Connection Diagnosis” ”Connection Settings” ”Opening Instructions”); the chart card “spectrum” (`#seriesSeg`, “Return to full spectrum”, “download CSV (Excel can be opened)”, `<canvas id="chart">`, `#chartEmpty`, `#chartFoot`, `#chartTiming`, `#peaks` “The strongest peak (click to enlarge):”); a tab card with “Spectrum Record” ”Scheduling Record” ”Event Record” ”Advanced:TCP Terminal”.
  - Right, numbered step cards: **1 Inspection before discharge** (`#btnRefresh` “Reread”, `ul#checks`); **2 plasma parameters** (`#btnParams` “modified…”, `#paramLine`, `#btnPrepare` “synchronized to Nano (No discharge) ”, `<details>` “Look at the value of each item” with columns project/Profile/Nano); **3 Measure once** (row “order” with `#seqSeg` buttons “”Quick” ”Original”, `#seqNote`, `#btnPlasma` “⚡ Start Plasma”/“high voltage discharge＋Capture spectrum”, note “will automatically capture the background and signal spectrum when discharging, no need to click to capture separately. The results are displayed in the middle spectrogram and automatically archived. steps 4 uses the same sequence. ” The value of); **4 Schedule measurement**/“Automatically repeats measurement after setting (start: “Immediately” ”Specified time”; interval: “Every [ ] measures once every minute”; End: “How many times?” ”To what time” ”Until you press stop”; `#btnSchedule` “⚡ Start scheduling”/“Automatic discharge in each period＋Capture spectrum”; live view with `#btnScheduleStop` “■ Stop schedule”/“will not start the next one; the one that is being discharged will also be stopped.”); `<details class="card tools">` “Maintenance Tool”: `#btnSpectrum` “capture spectrum (no discharge)”/“Check spectrometer alone: read only 3 Zhang, do not turn on high voltage”, `#btnClear` “Clear Nano Fault”, `#btnPrepare2` “synchronous Nano parameter”, `#btnSimFault` “injection Nano Fault” (demo only).
- Dialogs (native `<dialog>` with `<form method="dialog">`): `#dlgPlasma` “⚡ is about to output high voltage” (big voltage, rows, a confirmation checkbox, “Cancel (N) ”, “Start Plasma”); `#dlgSchedule` “⚡ is about to start scheduled measurement”; `#dlgParams` “Modify plasma parameters” (an inline SVG timing diagram, seven fields, an on-screen number pad, “Save and sync”); `#dlgSettings` “connection setting” (RPi of IP address, TCP port, token (passcode), “Save and reconnect”); `#dlgDiagnose` “connection diagnosis”; `#dlgFind` “automatic search RPi”; `#dlgPin` “input repair PIN” (dots, number pad 1–9, “Clear”, 0, “⌫”, “Unlock”).
- `#toast`; one script `/app.js`.

### 3.2 `app.js` (1559 lines) — how state is rendered

- Helpers `$`(id) and `make(tag, class, text)`; everything is built with `textContent` / `replaceChildren` (no `innerHTML`).
- Tables at the top: `PARAMS` (7 rows: field, label, unit, hint, status key, Nano key, divisor; `:14-22`), `SEQUENCE_NAMES`, `PHASE_STEPS`, `PHASES`, `CANCEL`, `NANO_STATES`, `FAULTS`, `SERIES`, `PRESETS`, `SCHED_STATUS`, `SLOT_STATUS`, `SCHED_ACTIVE`, `SCHED_DEFAULT`.
- Module state (`:65-90`): `csrf`, `boot`, `state` (the latest `/api/state`), `unlockSession`, `appDown`, `busy`, `runs`, `shownRun`, `seriesMode`, `zoom`, `schedForm`, `sequenceChoice`, `schedRequest`, …
- `api(path, body)` (`:93-115`): GET with `X-Unlock`; POST with `X-Token` and `X-Unlock`; one automatic re-bootstrap after “Invalid local request”.
- **One poll drives the whole screen**: `setInterval(refresh, 1000)` (`:1548`); `refresh()` fetches `/api/state` and calls `render()` (`:202-224`), which calls `renderWelcome, renderBanner, renderTiles, renderPower, renderNano, renderParams, renderActions, renderChecks, renderSchedule, renderEvents`, and reloads the run list when `state.latest_run.job` changes. Every render function is idempotent (sets text, class, `hidden`, `disabled`).
- Things deliberately **not** re-rendered every second: the schedule form (`renderSchedForm` is called only when a choice changes, so typing is not overwritten, `:607-620`); lists are rebuilt only when a key changes so a row is never replaced under the pointer (`:255-260`, `:722-747`).
- `command(body, doneText)` (`:136-155`): one command at a time (`busy`); on error code `locked` it opens the PIN dialog and retries.
- Banner priority (`renderBanner`, `:286-335`): program down → not connected → reading → running job (high voltage: class `hv`, phase chips) → failed/interrupted schedule → active schedule → last job failed/cancelled (dismissable) → no snapshot → stale status.
- Confirmation dialogs: the go button stays disabled until the checkbox is ticked; a countdown closes the dialog as cancelled (30 s for a discharge `:1271`, 60 s for a schedule `:785`). Checkbox texts: “I have confirmed that it will be output soon N V High voltage, personnel are far away from the electrode, physical emergency stop/Power off switch is at hand. ” (`:1270`) and “I have confirmed: Each time period will be automatically output during the schedule N V High voltage, no further inquiries; personnel will not approach the electrode, physical emergency stop/power-off switch is ready for use. ” (`:783-784`).
- Chart: own canvas code (`:941-1145`): series switch, drag to zoom, double click to reset, hover read-out, the five strongest peaks as chips.
- `localStorage` keys: `plasmic.sequence` (`:574`), `plasmic.schedule` (`:592`).
- Hash links: `#diagnose`, `#params`, `#settings`, `#find`, `#schedule` (`:1549-1555`).
- `wire()` (`:1404-1534`) attaches every handler; `start()` (`:1536-1556`).

### 3.3 `style.css` (264 lines)

Comment at line 1: “The gray scale is the base, and the colors are only reserved for ”operation” and ”abnormal”. ” Variables (`:2-20`): `--bg #e7e9ec`, `--panel #f7f8f9`, `--ink #1b222b`, `--muted #5a6572`, `--line #c8cdd5`, `--soft #e3e6ea`, `--active #1d5fc2`, `--hv #9a4a00`, `--hv-bg #ffe9c2`, `--alarm #b3261e`, `--alarm-bg #fde3e0`, `--unknown #8a2c8f`, `--series #1d5fc2`, `--radius 6px`; font "Segoe UI", "Microsoft JhengHei UI"…, 15 px. Top bar `#2a323c`. Grid `290px minmax(0,1fr) 330px` (`:79`), breakpoints 1180 px and 760 px. Component classes: `.tile(.ok|.go|.hv|.bad|.unknown)`, `.card`, `.step .num`, `.big(.on|.fault|.unknown)`, `.kv`, `.note(.bad)`, `.act(.hv|.slim|.wait)`, `.seg`, `.tabs/.tab`, `.list`, `.events`, `.term`, `.keypad`, `.confirm`, `.volt`, `.toast`, `.banner(.hv|.alarm|.warn|.info)`, `.phases`.

---

## 4. (c) The TCP protocol to the RPi — `rpi/tcpd/plasmic_tcpd.py`

Line numbers are those of the file as rewritten at 04:10:12. Where the installed 1.3.0 differs it is marked **[1.3.0]**.

### 4.1 Transport and gatekeeping

- TCP port 8767, UTF-8, **one JSON object per line, one reply per request**; `PROTOCOL = 1`. A bare word is accepted as shorthand (`ping`, `stop`; `dispatch`, `:1355`). A request `id` is echoed. Max line 65536 bytes; max 4 connections (`MAX_CONNECTIONS`); idle timeout 300 s.
- Reply: `{"ok": true, …}` or `{"ok": false, "code", "error"}`. Codes: `bad_request`, `auth_failed`, `auth_required`, `busy`, `not_ready`, `tool_failed`, `not_found`, `unknown_command`, `internal`, and `forbidden` (source IP not in `allow`; the server says why, then closes — `_turn_away`, `:1396`).
- `allow` list: exact IP, `192.168.0.*`, or `*` (`allowed`, `:129`).
- Token: `OPEN_COMMANDS = ('hello', 'ping', 'auth', 'stop')` need none. Others need `auth` once on the connection or a `token` field in the request. The token travels in clear text.
- UDP discovery on the same port number: request `PLASMIC-DISCOVER 1` → the `identity()` JSON plus `allowed`; at most 20 replies per second; config `discovery: false` switches it off.
- Config file keys (`DEFAULTS`, `:71-85`; unknown keys are refused): `bind`, `port`, `allow`, `token`, `watchdog_s` (2–60, default 5), `flow` (new; `unified` | `verified`), `poll_s` (3–60, default 3), `discovery`, `app_dir`, `tools_dir`, `serial_port`, `output_dir`.
- Command line: `--config`, `--sim`, `--sim-dir`, `--sim-speed`, `--bind`, `--port`, `--token`. Real mode runs only on Linux and needs `tools_dir/plasma_job.py`.

### 4.2 Commands

| `cmd` | Token | Fields | Reply |
|---|---|---|---|
| `hello` | no | — | `server: 'plasmic-tcpd'`, `version`, `protocol`, `sim`, `name` (hostname), `port`, `auth_required`, `authed`, `watchdog_s`, `time`, `limits: {max_voltage_v: 700, max_current_a: 0.1, max_job_s: 215}`, and **`flow`** (working tree, `:1273`) / **`sequences: ['classic','quick']`** [1.3.0] |
| `ping` | no | — | `time` |
| `auth` | — | `token` | `authed: true` |
| `status` | yes | `fresh: true` re-reads the equipment now | `busy`, `job` (view below), `age_s`, `updated_at`, `snapshot` (`{parameter, power, nano, received_at_unix_s}` or null), `error`, `ready: {plasma, problems: [{reason, fix}]}`, `estimate_s`, `sim`, plus **`start_s`, `flow`** (working tree, `:1023-1025`) / **`estimates: {classic: {total_s, start_s}, quick: {…}}`** with `estimate_s` = classic [1.3.0] |
| `job` | yes | optional `job` (must be the latest) | `job`: the view |
| `jobs` | yes | — | `jobs`: up to 50 rows `{job, kind, modified, valid, voltage_v, started_at, frames}` read from the output folder |
| `result` | yes | `job` (default latest), `frames: true` | see 4.5 |
| `stop` | **no** | — | job running: `{cancel_sent: true, job, message}` (one SIGTERM to the tool); idle: runs the `stop` tool, `{cancel_sent: false, stopped: true, power, nano}` |
| `prepare` | yes | — | `{prepared: true, parameters}` (writes parameter.json's values into the Nano and reads back; never arms) |
| `clear_fault` | yes | — | `{cleared, previous_fault, nano_sync_required, message}` |
| `set_parameters` | yes | `parameters`: exactly `Ontime, Offtime, Cycle, Loopnum, Integration_Time, Spectra_Interval, Voltage` | `{saved: true, parameters, nano_sync_required: true}` |
| `spectrum` | yes | `integration_ms` 1–10000 (default 100) | `{job: 'CAP-…', estimate_s}`; a job (3 frames, no high voltage) |
| `plasma_run` | yes | `confirm_high_voltage: true` (mandatory). [1.3.0] optional `sequence: 'classic'|'quick'`, default `classic`. Working tree: no choice; extra fields are ignored (`tests/test_tcpd.py:648-652`) | `{job: 'HMI-…', voltage_v, estimate_s, watchdog_s}` plus `flow` (working tree) / `sequence` [1.3.0] |
| `sim_fault` | yes | `fault`: `HEARTBEAT_LOST` | `TRIGGER_TIMEOUT` | `JOB_TIMEOUT` | simulator only |

Parameter rules (`check_parameters`, `:150-179`; "must never be looser than plasma_job.py"): Ontime/Offtime 1–1000 ms in 0.1 ms steps; Cycle 1–10000; Loopnum 1–1000; Cycle×Loopnum ≤ 10000; pulse total ≤ 120 s; Integration_Time integer 1–10000 ms; Spectra_Interval integer ms, ≥ integration, ≤ 60000; Loopnum × max(interval, one pulse train) ≤ 100 s; Voltage integer 1–700.

`snapshot` (from `read_status.py`, protected): `parameter {valid, voltage_v, integration_ms, on_time_ms, off_time_ms, cycles, loops, spectra_interval_ms}`; `power {valid, set_voltage_v, set_current_a, voltage_v, current_a, run_stop_setting, …}`; `nano {valid, firmware, state, fault, outputs_allowed, config_valid, session_valid, session_id, output_command_high, output_pin_sample, pulses_completed, loops_completed, active_parameters {revision, on_time_us, off_time_us, cycles, loop_count, integration_time_us, spectra_interval_us, voltage_display_v}, lcd_power_report {power_snapshot …}}`.

`ready_problems` (`:279-337`) produces the Chinese reasons and fixes shown in step 1 of the screen. Its `fix` texts still name buttons of the **old HMI**: “Click ”Read Status”, “Press ”HSPY OFF＋Nano STOP””, “Re-save it in ”Plasma Parameters”; the current screen calls them “Reread” and “■ stop”.

### 4.3 The job model

- One job at a time; a second command while one runs gets `busy`. `Controller.bus` (a lock) is held from `start_job` to the end of `_run_job`.
- `Job` (`:863-886`): `id` = folder name (`HMI-<32 hex>` for plasma, `CAP-<32 hex>` for capture; `LITE-` is accepted by the pattern but never produced), `kind` (`plasma` | `capture`), `flow`/[1.3.0] `sequence`, `received_at`, `state`, `error`, `cancel_reason`, `estimate_s`, `voltage_v`, `parameters`, `frames_total`, `steps`, `post_stop`, `timing`.
- States: `running` → `done` | `failed` | `cancelled` (`cancelled` = not done **and** a `cancel_reason`). `cancel_reason`: `stop`, `watchdog_timeout`, `owner_disconnected`, `shutdown`. The job stays `running` until the equipment has been read back.
- Job view (`job_view`, `:971-981`): `id, kind, state, high_voltage, flow|sequence, timing, client, started_at, finished_at, elapsed_s, estimate_s, voltage_v, error, cancel_reason, steps, post_stop, progress {phase, frames_done, frames_total}`.
- `progress.phase` while running (`_progress`, `:983-1010`): `stopping` (SIGTERM sent), `finishing`; capture: `capture`; working tree `unified`: `prepare` → `background` → `ramp` → `signal` → `off` (read from the tool's `stage` file); `verified`/classic: `prepare` → `background` → `signal` (from which folders exist). [1.3.0] quick: `prepare` → `ramp` → `signal` → `off` → `background_after`.
- **Watchdog** (`_tick`, `:1120-1137`, every 0.25 s): for a plasma job, if the connection that sent `plasma_run` is closed (`owner_disconnected`) or has been silent longer than `watchdog_s` (`watchdog_timeout`), **one** SIGTERM goes to the tool, which runs its own OFF sequence. Any line on that connection counts as life. Tool limit 215 s, then 60 s grace, then kill (`RealBackend.plasma_run`, `:574-582`). After a job that did not end `done`, the server runs the `stop` tool once more and stores `post_stop {ok, error}`.
- Status polling (`_poll_loop`, `:942-962`): only while a client spoke in the last 15 s; every `poll_s`; at least 1 s between reads; each read also refreshes the POWER line of the Nano's LCD.
- Files per job in `output_dir/<job id>/`: `job.json` (the tool's report), `tcpd.json` (server record: `id, kind, state, error, flow|sequence, received_at, cancel_reason, started_at, finished_at, client, parameters, post_stop`), `background/spectrum-N.csv`, `signal/spectrum-N.csv` (header `wavelength_nm,intensity_raw`), `signal/progress.jsonl` (classic), `stage`, `frames/` (unified, while running), `job.pid` (while running); capture: `spectrum.json`, `spectrum-N.csv`. Audit log `output_dir/../tcpd-audit.jsonl` (rotated at 2 MB).
- Interlock hook: `permit_problems()` returns `[]`; its rows are added to the readiness problems right before every discharge (`start_job`, `:1094`).

### 4.4 The discharge sequences

**classic** = `rpi_tools/plasma_job.py plasma_run` (`run_plasma`, `plasma_job.py:475-627`; protected; I read `:385-540` and `:640-748`, the rest is from `docs/work record.md:273-291`):
`require_off` → `prepare()` (always rewrites the Nano's parameters; step “Nano config has been read back”) → **background: a separate spectrometer program run, 3 frames** (`:513-514`, step “background spectrum completed”) → heartbeat, ARM → second spectrometer program for `loops` triggered frames (step “Signal spectrum SDK Prepared N frame”) → HSPY ON, wait for the voltage → START (step “HSPY ON, Nano WAIT_TRIGGER”) → per loop: D12 pulse first, *then* the `frame-n.go` mark → Nano DONE → OFF sequence (step “Nano DONE, signal Spectrum completed”).

**quick** [1.3.0, source no longer in the folder] = `rpi/tcpd/plasma_quick.py`, described in `docs/work record.md:68-75`: `require_off` → start the signal spectrometer program at once → compare the Nano's 18 registers with the file and keep the session when identical (`synchronized_revision`) → wait for `frame-1.ready` (one retry after 1 s when the spectrometer does not open) → heartbeat, ARM → HSPY ON → wait for the voltage → START → per loop `fire()`: write `frame-n.go`, wait until the spectrometer program removes it, wait 5 ms (`EXPOSURE_LEAD_SECONDS`), raise D12 → Nano DONE → **HSPY OFF first** → collect the signal frames → **3 background frames after the supply is off** (one retry).

**unified** (working tree) = `rpi/tcpd/plasma_measure.py` (`run_measure`, `:263-524`; header says written 2026-10-06, tested only on the pretend rig, never on the equipment):
`require_off` → start **one** spectrometer program for `BACKGROUND_FRAMES + loops` triggered frames (`:332-336`) → check or re-sync the Nano → wait for `frame-1.ready` (one retry) → stage `background`: release frame 1 with the supply OFF (step “The background spectrum begins to be exposed (the high voltage is not turned on yet)”) → at most `RAMP_HEADSTART_SECONDS = 1.5` before that exposure ends: heartbeat, ARM, stage `ramp`, HSPY ON, report POWER ON to the LCD, wait for the voltage, START → stage `signal`: per loop `fire()` (exposure first, D12 5 ms later), heartbeats kept away from the due moments (`quiet_until`) → Nano DONE → stage `off`: `stop_outputs`, wait for 0 V → collect all frames and `split_frames()` them into `background/spectrum-1.csv` and `signal/spectrum-N.csv` (`:245-260`) so the result loader finds the usual layout. Report keys: `flow: 'unified'`, `background_frames`, `timeline_s` (`spectrometer_started, nano_ready, spectrometer_ready, background_started, armed, hspy_on, voltage_reached, start, first_pulse, nano_done, off_confirmed`), `triggers` (`frame, capture_frame, go_utc, edge_utc, taken_ms, lead_ms, exposure_call_utc, exposure_before_pulse_ms, exposure_ms`). Needs `--execute --confirm-high-voltage`. Uses only functions of the verified `plasma_job` module (`load_tools`, `:61-69`).

### 4.5 `result` and the timing data

`result` (`Controller.result`, `:1219-1237`):
- plasma (`load_plasma_result`, `:388-420`): `kind: 'plasma'`, `job`, `started_at`, `finished_at`, `flow` (working tree; default `'verified'` when the report has none) / `sequence` [1.3.0], `voltage_v`, `model`, `serial`, `integration_ms`, `pixels`, `background_frames`, `signal_frames`, `steps`, `axis`, `background` (mean), `signal` (mean), `difference` (signal − background), `parameters`, `timing`; with `frames: true` also `frames` = each signal frame minus the mean background (null when larger than 250 000 values).
- capture (`load_capture_result`, `:423-433`): `kind: 'capture'`, `signal_frames`, `axis`, `mean`, `frames`, …
- The loader is generic: it reads the number of frames from `job.json` (`_frame_count`, `:379-385`; 1–1000 frames named `spectrum-1.csv…`), so it works for 1 or 3 background frames.

`timing` (`job_timing`, `:231-276`): `{flow|sequence, wait_s, frames: [{frame, lead_ms, exposure_ms, period_ms}], lead_ms: [min, max] (only when measured), steps_s: timeline_s}`. `wait_s` = first trigger minus the moment the command arrived (`received_at`). `lead_ms` = by how many ms the exposure request preceded D12; measured by `plasma_measure.py`/quick, `None` for `plasma_job.py`. The job view carries the short form `{flow|sequence, wait_s, lead_ms}`.

Estimates (working tree, `:182-210`): unified `start_s = round(4 + max(0, integration_s − 1.5))`, `estimate_s = start_s + signal_s + 3`; verified `start_s = round(3·integration_s + 8)`, `estimate_s = round(3·integration_s + 6 + 3 + V/100 + signal_s + 8)`; `signal_s = loops × max(interval, one pulse train)`. [1.3.0]: `QUICK_START_SECONDS = 4`; quick total `4 + signal_s + 2 + 3·integration_s + 2`.

Measured on the real rig (classic, 30 V, 1000 ms integration, 5 loops, 1500 ms interval; `docs/work record.md:273-291`): command received → tool starts 0.18 s → background program starts 1.82 s → 3 background frames done 5.5 s → signal program ready 6.9 s → **first trigger 10.3 s** → last frame 19.0 s → end 20.0–20.7 s. Exposure begins 0.01–0.03 s after the pulses; loops 1945–1955 ms apart instead of 1500. Two of eight runs failed with `UAI_SpectrometerOpen returned 6` (cause unknown). Quick on the pretend rig: first pulse 3.26–3.61 s after the tool starts, exposure 4.9–5.4 ms before D12, loops 1.502–1.512 s apart. No real-rig number exists for quick or unified.

### 4.6 The simulator (`SimBackend`, `:595-850`; `--sim`)

Pretend HSPY, Nano and spectrometer writing the same files. Default parameters `{Ontime 25.0, Offtime 5, Cycle 25, Integration_Time 1000, Spectra_Interval 1500, Voltage 100, Loopnum 8, Metal 'Pd'}`; 1880 pixels 179.86–500.11 nm; peaks `SIM_PEAKS` (strongest 337.13 nm). `spectrum` writes 3 frames (`:734`). `plasma_run(…, flow)` follows either flow: unified → 1 background frame (`:799`), verified → 3 (`:802`).

---

## 5. (d) Where the number of background frames is decided, and every place that assumes "3 frames" or "two sequences"

Status column: state at 04:19:16 on 2026-10-06. **OLD** = still assumes two sequences / 3 frames. **NEW** = already converted by the concurrent edit. **PROTECTED** = listed in `SHA256SUMS.txt`, must not be edited.

### 5.1 Where the count is actually decided

| Place | What | Status |
|---|---|---|
| `rpi/rpi_tools/plasma_job.py:395-396` | `start_spectrum(…, frames=3, frame_interval_ms=0, triggered=False)` default | PROTECTED |
| `rpi/rpi_tools/plasma_job.py:429` | `collect_spectrum(child, deadline, expected_frames=3)` default | PROTECTED |
| `rpi/rpi_tools/plasma_job.py:513-514` | classic background: `start_spectrum(…, output_dir / 'background', integration_ms)` + `collect_spectrum(background, clock() + 40)` → both defaults → **3** | PROTECTED |
| `rpi/rpi_tools/plasma_job.py:727-731` | the `spectrum` action (no-discharge capture): defaults → **3**, timeout `3 * integration` | PROTECTED |
| `rpi/rpi_tools/hardware_verify.py:191`, `:316` | `frames=3`; `--frames` default 3 | PROTECTED |
| `rpi/tcpd/plasma_measure.py:39` | `BACKGROUND_FRAMES = 1`; used at `:284`, `:291`, `:333`, `:348`, `:352`, `:454`, `:457`, `:503-504` | NEW |
| 1.3.0 `rpi/tcpd/plasma_quick.py` | 3 background frames after the supply is off (`docs/work record.md:70`) | file deleted from the folder; still on the RPi |

So with the protected tools untouched, a single background frame is only possible through a program outside the protected list (as `plasma_measure.py` does). The **maintenance capture still takes 3 frames** because `plasmic_tcpd.py` calls the protected `plasma_job.py spectrum`, which passes no frame count.

### 5.2 `rpi/tcpd/plasmic_tcpd.py` (NEW unless noted)

- `:48-55` comment and `FLOWS = ('unified', 'verified')`, `UNIFIED_BACKGROUND_FRAMES = 1`; `:77` default `'flow': 'unified'`; `:118-119` validation.
- `:193`, `:205` unified estimates; `:194`, `:206` verified estimates use `3 * integration_ms`.
- `:242`, `:263` `job_timing` returns `flow`; `:407` result `flow`.
- `:424` docstring "The three raw frames" (capture; the code is generic).
- `:526-527` `measure_tool`, `flows`; `:572` capture timeout `3 * integration_ms / 1000 + 35`; `:574-582` `plasma_run(…, flow)`.
- `:598` `SimBackend.flows`; `:734` sim capture `for index in (1, 2, 3)`; `:750-850` sim `plasma_run` (`:763`, `:791`, `:799` one frame; `:802` verified three frames).
- `:864-867` `Job.flow`; `:909` `Controller.flow`; `:976` job view; `:992` capture `'frames_total': 3`; `:993-1002` unified progress (`:1001` subtracts `UNIFIED_BACKGROUND_FRAMES`); `:1023-1025` status `estimate_s`, `start_s`, `flow`; `:1082-1109` `start_job` (`:1108-1109` capture `frames_total = 3`, `int(3 * integration_ms / 1000 + 8)`); `:1188`, `:1195`; `:1273` `hello.flow`; `:1341-1347` `plasma_run` without a choice.
- `rpi/tcpd/config.example.json:7` `"flow": "unified"` — NEW.

### 5.3 PC program — all OLD

- `pc/app.py:65-67` comment + `SEQUENCES`; `:236-238` meta key `'sequence'`; `:240` `wait_s`; `:456-458` event “; After pressing N seconds to start discharging”; `:490-503` `sequence_fields` (texts “The measurement sequence must be quick (fast) or classic (original)”, “RPi is an old version, and there is no ”...” sequence yet. Re-execute Install.cmd renew RPi ; please select ”original” now. ” After); `:633-650` `Schedule.preview(raw, sequence)` (reads `status.estimates[sequence].total_s`); `:665-691` `Schedule.start` (record key `sequence`, event “,quick order”); `:786-787` slot command; `:870`, `:874`; `:894-896` `plasma_run` (event “ (quick sequence)”).
- `pc/plasmic_client.py`: no sequence anywhere (`run_plasma` at `:131` sends a plain command). `pc/examples/*`: no occurrence (grep).

### 5.4 Web UI — all OLD

- `pc/web/index.html:167-170` the “order” row with “”Quick” ”Original” and `#seqNote`; `:172` “…Steps 4 uses the same sequence. ” The value of; `:203` “Check spectrometer alone: read only 3 Zhang, do not turn on high voltage”.
- `pc/web/app.js:23-25` comment + `SEQUENCE_NAMES`; `:26-31` `PHASE_STEPS` (classic “Prepare/background spectrum/discharge＋Signal spectrum/Turn off the high voltage and confirm”, quick “Prepare/discharge＋Signal spectrum/Turn off high voltage and confirm/background spectrum”); `:32-37` `PHASES` (`background` “captures the background spectrum (not discharged yet)”, `ramp`, `off`, `background_after` “captures the background spectrum (discharge has ended, high voltage has been read back as off)”); `:86` `sequenceChoice`; `:178-186` `knownSequences`, `sequence`, `estimate`; `:303-309` banner (phase list by `job.sequence`); `:515-518` `plasmaSub`; `:519-528` segment buttons and the three `seqNote` texts; `:529-530` “Check spectrometer alone: read only 3 Zhang, do not turn on high voltage”; `:571-587` `loadSequence` / `chooseSequence`; `:674` schedule row “order”; `:760`, `:765` `schedule_preview` with `sequence`; `:774` “…Add background 3 open”; `:775` “order” row; `:819` “discharge (fast)”; `:875-894` `timingLine` (“Exposure is approximately later than discharge 0.01–0.03 Second (The original sequence does not record the exact time) ”); `:896-903` `sequenceLine`; `:1264` dialog row “order”; `:1421` segment handler; `:1426` `plasma_run` with `sequence`; `:1438` `schedule_start` with `schedRequest`.
- `pc/web/style.css:163` `#seqNote`; `:164` `.chartFoot.measured`.

### 5.5 Installer

- `installer/install.py:220` required file `rpi/tcpd/plasma_measure.py` — NEW.
- `rpi/install_rpi.sh:171-178` installs `plasma_measure.py`, moves an existing `plasma_quick.py` on the RPi into the backup folder — NEW.

### 5.6 Tests

- `tests/test_app.py:132-156` `test_the_chosen_order_is_passed_on_and_its_timing_is_kept`; `:158-171` `test_an_rpi_with_the_older_program_only_has_the_original_order`; `:314-337` `test_a_schedule_uses_the_order_it_was_started_with` — OLD.
- `tests/test_tcpd.py` — NEW: `:271` step “Nano DONE, Spectrum completed”; `:273-274` `background_frames == 1`; `:293` capture 3 frames; `:602-669` `FlowTests`; `:672-702` `VerifiedFlowTests` (`:693` 3 background frames with `flow: verified`); `:705-733` `StopBeforeHighVoltageTests`; `:736-790` `JobTimingTests`; `:792-823` `MeasureToolPlumbingTests`.
- `tests/test_measure.py` — NEW: `:110-113`, `:124-129`, `:145`, `:248-255` (`--frames` = loops + 1), `:281`, `:348`; `:153-169` runs the verified tool on the same rig for comparison.
- `tests/test_installer.py:495`, `:498-509` — NEW.
- `tests/simulated_spectrometer.py:153` `--frames` default 3 (mirrors `hardware_verify.py`).
- `tests/simulated_rig.py`: no frame-count assumption found.

### 5.7 Documents — all OLD

- `docs/guide-source/installation-and-usage.src.html`: `:147`, `:169`, `:479-480` (step 5 checklist items for “original” and “fast”), `:519`, `:540` (figure caption), `:541-554` (section “Two orders: fast, original”; `:542` “background 3 open”, `:546` “Need to shoot first 3 Zhang background”), `:566`, `:594`, `:640-642` (troubleshooting rows), `:691`, `:694`, `:698` (“Capture without discharge 3 open”), `:699-701`, `:710`, `:715-716` (protocol examples), `:882-884`, `:886`, `:891` (“Two discharge sequences”), `:892`, `:898`, `:922`, `:924`, `:926`, `:995-996`, `:1006` (`tests\test_quick.py`).
- The built `detailed-guide.html` (same text; rebuilt from the source).
- The generated English guide documents the order selector and quick phase chips. Legacy interface screenshots were removed during the English-only cleanup.
- `docs/guide-source/README.txt:32`, `:36-37`.
- `READ-ME-FIRST.txt:20-25`, `:70-72`.
- `historical-development-notes.md:35-36`, `:55`, `:57`, `:64`, `:80`, `:82`, `:91`.
- `docs/work record.md`: `:17`, `:30`, `:63-75`, `:86`, `:104`, `:132`, `:172`, `:176-177`, section 12 (`:269-316`).

---

## 6. (e) Installer, launchers, embedded Python, tests, guide build

### 6.1 Launchers (all pure ASCII; each first calls `installer\unpack-python.cmd`)

| File | Runs |
|---|---|
| `Start.cmd` | `cd pc` → `runtime\python\python.exe app.py` → `pause` |
| `Start-Demo.cmd` | `… app.py --demo` (simulator on 127.0.0.1:8767; cannot run together with `Start.cmd`, both use 8766) |
| `Set-PIN.cmd` | `… app.py --set-pin` |
| `Start-Kiosk.cmd` | `start "Plasmic EC" /min … app.py --no-browser`, waits up to 30 s for port **8766 (hard-coded)**, then Edge `--kiosk http://127.0.0.1:8766/ --edge-kiosk-type=fullscreen` |
| `Install.cmd` | `pc\runtime\python\python.exe installer\install.py %*`; pauses only on error |
| `Flash-Nano.cmd` | `installer\flash_nano.py` (not read) |
| `installer\unpack-python.cmd` | if `pc\runtime\python\python.exe` is missing, `Expand-Archive` of `pc\runtime\python-3.12.10-embed-amd64.zip` |

None of the launchers passes `-B`; `pc/__pycache__` exists (dated 10-05 22:03).

### 6.2 Embedded Python

Python 3.12.10 "embeddable" for Windows amd64: **standard library only, no pip** (`historical-development-notes.md:100`). The zip is a protected file (`SHA256SUMS.txt:24`). It does not put the script's folder on `sys.path` (hence `app.py:17`). `time.monotonic()` ticks in 16 ms steps there (`historical-development-notes.md:105`). The RPi programs also use only the standard library, except the verified tools, which need `python3-serial` on the RPi (`install_rpi.sh:70-76`). **There is no serial-port package on the PC side.**

### 6.3 `installer/install.py` (675 lines)

- Target from `installer/site.json` (`{rpi_host, rpi_user}`, no password; currently `192.168.1.1` / `wow` per the guide); built-in fallback `192.168.1.1` / `pi`.
- Five steps (`TOTAL = 5`): 1 Check the programs on this computer Failed to start measurement (`step_computer`, required files at `:218-222`; creates `pc/data`) → 2 Create desktop shortcut (“Plasmic electronic control”→`Start.cmd`, “Plasmic electronic control (practice mode)”→`Start-Demo.cmd`, through `shortcut.ps1`) → 3 Check the network (`step_network`; options R/F/C/W/A/S) → 4 Bundle TCP Server installed RPi (`step_rpi`) → 5 Actual confirmation once (`step_verify`, reads `status` only).
- `install_on_rpi` (`:419-480`): token `secrets.token_hex(8)`; stages a copy of `rpi/` (without `__pycache__`, `sim-data`, `token.txt`; `.sh` forced to LF) plus `tcpd/token.txt`; `scp -r` then `ssh -t … 'bash -n <name>/remote_install.sh && bash <name>/remote_install.sh'`. `key_login()` first tries `ssh -o BatchMode=yes … true`.
- Exit codes of the remote script: 0 done; 10 full install done, RPi reboots in one minute (the installer waits up to 300 s); anything else failed. Only after 0 or 10 is `pc/data/config.json` rewritten (`:530`).
- Options: `--host`, `--user`, `--tcp-port`, `--ssh-port`, `--skip-rpi`, `--reinstall`, `--no-shortcuts`, `--no-launch`, `--yes`; hidden `--shortcut-dir`, `--identity`, `--known-hosts`.
- `rpi/remote_install.sh` (59 lines): `--tcpd-only` when `~/Desktop/Plasmic/nano_lcd_v1_1/rpi_tools/plasma_job.py` and `app/HSPY_Read_Status.py` exist, else full install; adds the calling PC's IP to `allow`; `bash -n install_rpi.sh` first (exit 2 if it fails); deletes the uploaded folder.
- `rpi/install_rpi.sh` (steps 1/11–11/11): full install touches apt, I2C, the UNO220 drivers, udev rules, services, groups; step 10 installs `tcpd/plasmic_tcpd.py`, `read_status.py`, `plasma_measure.py` to `/home/<user>/Desktop/Plasmic/tcpd/`, creates or updates `config.json` (existing keys kept; token replaced; mode 600); step 11 installs and restarts `plasmic-tcpd.service` (`ExecStart=/usr/bin/python3 -B @BASE@/tcpd/plasmic_tcpd.py --config @BASE@/tcpd/config.json`, `Restart=always`, `TimeoutStopSec=90`). Every overwritten file is backed up to `$BASE/backup/install-<time>/`.
- Restarting the service gives the PC a new token, so an open screen loses its connection; a full install reboots the RPi, which restarts the Nano and makes D13 flicker (`historical-development-notes.md:70`).

### 6.4 Tests (`tests/`, `unittest`, each file run by itself)

```
.\pc\runtime\python\python.exe -B .\tests\test_tcpd.py
.\pc\runtime\python\python.exe -B .\tests\test_app.py
.\pc\runtime\python\python.exe -B .\tests\test_installer.py
.\pc\runtime\python\python.exe -B .\tests\test_flash_nano.py
.\pc\runtime\python\python.exe -B .\tests\test_measure.py      (historical-development-notes.md still says test_quick.py)
```

No pytest, no runner script. Each file sets `sys.path` itself and ends with `unittest.main(verbosity=2)`. All tests use simulated equipment and connect only to 127.0.0.1.

| File | Classes | Notes |
|---|---|---|
| `test_tcpd.py` | `SimServer` (shared helper: `SimBackend` + `Controller` + `Server` on a free port, `speed=40`), `wait_until`, `Base`; `WatchdogTests`, `ProtocolTests`, `AccessTests`, `AllowListTests`, `DiscoveryTests`, `LcdPowerLineTests`, `ParameterRuleTests`, `RealBackendPlumbingTests` (stub `plasma_job.py`), `ResultFileTests`, `FlowTests`, `VerifiedFlowTests`, `StopBeforeHighVoltageTests`, `JobTimingTests`, `MeasureToolPlumbingTests` | imports the verified `nano_verify.encode_parameters` |
| `test_app.py` | `Harness` (SimServer + `pc.Application` on a temp data folder + `ExclusiveHTTPServer` on a free port, csrf `'test-csrf-token'`); `ScreenFlowTests`, `ScreenWatchdogTests`, `ScheduleRuleTests`, `ScheduleBase`/`ScheduleTests`/`SlowScheduleTests` (`pc.schedule.MINUTE = 1`), `TokenAndPinTests`, `FindRpiTests`, `MovedRpiTests`, `NewNanoTests`, `OfflineTests` | imports `SimServer` from `test_tcpd` |
| `test_installer.py` | `NetcheckTests`, `FindTests`, `InstallerTargetTests`, `InstallerHelperTests`, `RpiConfigSnippetTests`, `PackagedFileTests` | checks: `.sh` and `.service` LF only; `.cmd`/`.ps1` pure ASCII; launcher file list (`:490-496`); `rpi/tcpd/*.py` must be exactly `['plasma_measure.py', 'plasmic_tcpd.py', 'read_status.py']` and each installed by `install_rpi.sh` (`:498-509`) — **a new program in `rpi/tcpd` breaks this test until both are updated** |
| `test_flash_nano.py` | `FlashTests`, `ToolAndFileTests` | simulated avrdude |
| `test_measure.py` | `RigCase`; `WholeRunTests`, `TimingTests`, `FailureTests`, `CommandLineTests` | runs `plasma_measure.run_measure` on `simulated_rig.py` (a pretend RS485 bus with a Nano model ported from the firmware source, an HSPY, a trigger pin) and `simulated_spectrometer.py` (copied in as `hardware_verify.py`) |

The scp/ssh part of the installer is only covered by `docs/tools/simulated_rpi_ssh.py`, which needs `paramiko` (not in the bundled Python; `docs/work record.md:173`).

### 6.5 Guide build (`docs/guide-source/`)

- Edit `installation-and-usage.src.html`, then `.\pc\runtime\python\python.exe -B .\docs\guide-source\build_guide.py <test count>` writes the root `detailed-guide.html` (LF, about 1.7 MB because seven PNG screenshots are embedded as base64). Never edit the root file.
- Placeholders, each exactly once: `{{IMG_MAIN}}`, `{{IMG_HV}}`, `{{IMG_WELCOME}}`, `{{IMG_MOVED}}`, `{{IMG_DIAG}}`, `{{IMG_PARAMS}}`, `{{IMG_SCHED}}`, `{{TEST_COUNT}}`. The builder refuses unclosed tags, links to missing ids and duplicate ids.
- No `<script>` in the guide (served under the strict policy).
- Programs refer to guide sections **by title**: `installer/install.py:511` “References › Manual installation RPi end”, `:527`, `:553` “When something goes wrong”, `:651` “Steps 5 First power-on check” The first line of ”; `pc/netcheck.py:307` “Steps 3 Connect to the Internet”, `:337` “References › Allow list”; `rpi/tcpd/plasmic_tcpd.py:310`, `:313` “Steps 1”; `pc/web/index.html:137` “References › use TCP/IP Give the order yourself”; four places in `installer/flash_nano.py`. Renaming a heading means changing these too.
- Guide outline: Look here first: six steps / Safety instructions / What does the whole set look like? / steps 1–6 (6.1 The top three spaces … 6.8 Others) / when something goes wrong / References (protocol tables, watchdog rules, error codes, files).
- Screenshots: `shots_app.py <demo|offline|moved> <port> <temp folder>` plus headless Edge (`README.txt:20-45`).
- `READ-ME-FIRST.txt` is UTF-8 with BOM and CRLF.

---

## 7. (f) Rules in `historical-development-notes.md` that constrain changes

1. **Passwords** (rule 1): never ask for, type, store or use the RPi password. This PC logs in with the key `%USERPROFILE%\.ssh\plasmic_rpi_ed25519`; use `ssh -o BatchMode=yes wow@192.168.1.1 …`.
2. **High voltage** (rule 2): never send `plasma_run` or `schedule_start` to the real rig; the user presses those himself. Allowed on the real rig: `status`, `stop`, `schedule_stop`; `prepare`, `set_parameters`, `clear_fault` only when he asks. In the simulator anything is allowed.
3. **Software stop is not an emergency stop** (rule 3): no text may suggest that "closure" on the screen means the electrodes are dead.
4. **Protected files** (rule 4): the 22 files in `SHA256SUMS.txt` — `rpi/rpi_tools/{plasma_job,nano_verify,pulse_prepare,hardware_verify}.py`; `rpi/app/{HSPY_Protocol.py, HSPY_Read_Status.py, lib_linux_ARM_2.7.11.so, parameter.json.example}`; `rpi/tcpd/read_status.py`; `rpi/drivers/uno220gpio_0.1-3_armhf.deb`, `uno220uart_0.1-3_armhf.deb`; `rpi/system/{70-plasmic-spectrometer.rules, 99-usb-serial.rules, plasmic-trigger-low.service, rtc-rx8010.service, uno220-trigger-low.py}`; `nano/hex/nano_rs485_v2.pulse.hex`, `nano_rs485_v2_pulse.with_bootloader.hex`; `nano/source_single/…/nano_rs485_v2_pulse_single.ino`; `nano/source_split/…/ControlCore.h`, `nano_rs485_v2_pulse.ino`; `pc/runtime/python-3.12.10-embed-amd64.zip`. After any change run `docs\tools\check_sums.py`. The discharge flow is changed outside these files (the rule still names `plasma_quick.py` and `tests\test_quick.py`).
5. **No firmware change** (rule 5).
6. **Say what is true** (rule 6): nothing unverified on the rig may be called verified; update the guide's “Verification Status” and `docs\work record.md` when something is run on the rig.
7. **Ask first** (rule 7): any download; running `nano\drivers\CH341SER.EXE`; changing Windows network or system settings; scanning a network.
8. **Tests never touch the real network or rig** (rule 8); replace `netcheck.local_ipv4` in new tests.
9. **`pc\data`** (rule 9): after running the installer check `pc\data\config.json`; the present folder is the user's real one and must not be deleted.
10. **No casual RPi reboot** (rule 10); before a "tcpd only" update: no job running and the user's screen closed.
11. Working rules: PowerShell 5.1 (no `&&`); `.cmd` ASCII + CRLF, `.ps1` ASCII, `.sh` and `.service` LF; always `python -B`; standard library only; the user wants Traditional Chinese, short sentences, things that work by double-click.
12. `historical-development-notes.md:15` says pumps and the PLC "Don’t add him back unless he asks" and `docs/work record.md:21` records the decision "None PLC, No pump". The request relayed to this workflow asks for pump control to be integrated, so both documents will need updating by whoever does the merge.
13. `historical-development-notes.md:5`: at the start of a conversation, read the work log, report progress and next step, and change nothing until the user confirms.

---

## 8. Facts that matter when pump control is merged in

- EC's whole PC side is one stdlib `http.server` process on 127.0.0.1:8766 that serves three static files and a JSON API; the browser page polls `/api/state` once a second. Anything added to the page must live under the same origin and the content security policy of 2.5.
- The PIN is a maintenance lock for two actions, set from a command file. It is not an operator password for the screen.
- The only interlock hook is on the RPi (`permit_problems()`); the PC side has none. The places where a PC-side condition or extra step would attach are `Application.command` (`app.py:858-913`), `Schedule._ready` (`:652-663`) and the slot body in `Schedule._loop` between `_wait_until(due)` and the `plasma_run` (`:782-787`).
- The schedule was written as a cut-down copy of the old HMI's: "The old version runs one every time period Recipe (includes pump steps); this version does not PLC,So each period is once plasma_run" (`docs/work record.md:86`); not carried over: Recipe/CSV, blank samples, pause/resume, concentration, operator list (`:94`).
- A discharge started by hand is tied to the screen (10 s) and to the TCP connection (5 s); a scheduled one only to the TCP connection.
- `Start-Kiosk.cmd` and the guide hard-code port 8766; the simulator hard-codes 8767.
- The RPi has a CH340 on USB that is probably the Nano's own USB plug (`docs/work record.md:50`, an untested guess there).

---

## 9. What I could not determine

- Who is editing the folder, whether that work is finished, and what its final protocol will be (`flow` may still change).
- Whether the 1.3.0 `plasma_quick.py` source survives anywhere on this PC.
- Whether any of the five test files passes in the current mixed state (I ran nothing).
- The contents of `pc/data` (not opened), so the actual connection settings, whether a PIN is set, and what the stored runs look like.
- The body of `plasma_job.run_plasma` after line 540 and of `simulated_rig.py` lines 76–545 (skimmed by definition list only); `installer/flash_nano.py`, `pc/examples/*`, `docs/tools/simulated_rpi_ssh.py`, `split_guide.py` and the two `docs/*.txt` files were not read.
- Whether `tests/test_app.py`, `pc/app.py` and `pc/web/*` were changed after 04:19:16.
