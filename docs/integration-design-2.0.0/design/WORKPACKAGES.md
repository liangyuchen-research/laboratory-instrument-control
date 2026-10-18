# Plasmic Electronic control 2.0.0 — build plan (WORKPACKAGES.md)

Section H of the specification. Read with `SPEC.md` (A, B, D, E, F, G, I, J, K) and `API.md` (C). Eight work packages with
**disjoint file ownership**: there is no git and the implementers work in parallel in one shared folder, so **a file belongs
to exactly one package and nobody edits a file of another package**. A package that needs something from a neighbour builds
against the frozen interface (SPEC B.13, API.md, the web contract in WP6 below) and tells the lead when the interface is
not enough — it does not edit the neighbour's file.

EC = `C:\Users\PlasmaLab\Desktop\autonomous control` (all paths below are relative to it) · V7 = `C:\Users\PlasmaLab\Desktop\independent laboratoryv7` ·
HMI = `C:\Users\PlasmaLab\Desktop\UI\UI\hmi_v1_1` · maps = `…\scratchpad\understand\*.md` · screenshots = `…\scratchpad\hmi-shots\orig-*.png`.

---

## H.0 Rules for every implementer

| Rule | Detail |
|---|---|
| Real equipment | **Never** start `Start.cmd` or `Start-Kiosk.cmd`. Never run `pc\app.py` without `--demo`. Never open a COM port (the live Arduino Mega is on COM3 of this PC; opening it resets the board). Never connect to `192.168.1.1`. No ssh, no scp, no download |
| Kill switch | set `PLASMIC_NO_SERIAL=1` in every shell you work in (PowerShell: `$env:PLASMIC_NO_SERIAL = '1'`) |
| Interpreter | only `.\pc\runtime\python\python.exe -B …` (embeddable 3.12, standard library only; run `.\installer\unpack-python.cmd` once if `pc\runtime\python\` is missing). No `__pycache__` may be left behind |
| Practice mode for page work | `.\pc\runtime\python\python.exe -B .\pc\app.py --demo --no-browser --port 879N --sim-port 877N --data $env:TEMP\plasmic-dev-N` — each implementer takes his own N (1–9) so two instances never share a port or a data folder. Only possible once WP5 is done |
| Folders that are off limits | `pc\data\` (the owner's real data; `config.json` there must still point at `192.168.1.1` at the end), `pc\runtime\`, HMI `data\` (never open or list), V7 `.venv` except for WP1's copy. Never run the HMI, never run V7 (`run.bat`, `backend.main`) |
| Protected files | the 22 files of `SHA256SUMS.txt` and everything under `pc\vendor\` (after WP1a) are never edited. After any change: `…python.exe -B .\docs\tools\check_sums.py` and `…\check_v7_sync.py` |
| RPi side | `rpi\**`, `tests\test_tcpd.py`, `tests\test_measure.py`, `tests\simulated_rig.py`, `tests\simulated_spectrometer.py` belong to a separate review that is running now. Read them, import `test_tcpd.SimServer` in tests, never edit them |
| Formats | `.py` `.js` `.css` `.html` `.md` `.csv` `.json` UTF-8 + LF · `.cmd` ASCII + CRLF · `READ-ME-FIRST.txt` UTF-8 with BOM + CRLF · vendored files keep their own bytes |
| Texts | every on-screen text exactly as written in `SPEC.md` / `API.md` (Traditional Chinese, full-width punctuation as shown). A text that is missing is asked for, not invented |
| Tests | `unittest`, each file runnable by itself, only `127.0.0.1`, `netcheck.local_ipv4` replaced by `lambda: []` in every harness, temp folders for data. Measure short times with `time.perf_counter()` (the bundled Python's `time.monotonic()` ticks in 16 ms steps on Windows). A child process that prints Chinese gets its encoding set explicitly |
| Hand-over | a package is "done" when its own test files are green with the command in its brief, the two check tools pass, and no `__pycache__` exists |

---

## H.1 Packages and files

| WP | Name | Files it owns (creates or changes — nobody else touches these) | Depends on | Effort | Risk |
|---|---|---|---|---|---|
| **WP1** | Vendoring (phase a) · packaging and documents (phase b) | **a:** `pc\vendor\**` (V7 copies, `site\serial\**`, `site\yaml\**`, `licenses\**`, `README.txt`, `MANIFEST.sha256`) · `pc\web\fonts\**` · `docs\tools\check_v7_sync.py` · `tests\test_vendor.py`. **b:** `installer\install.py` · `tests\test_installer.py` · `Set-PIN.cmd` · `docs\guide-source\installation-and-usage.src.html` · `detailed-guide.html` (built) · `docs\guide-source\README.txt` · `docs\guide-source\shots_app.py` · `docs\guide-source\shots\*.png` · `READ-ME-FIRST.txt` · `historical-development-notes.md` · `docs\work record.md` · `docs\1-How to continue on another computer.txt` | a: — · b: everything | ½ + 2 days | low |
| **WP2** | Pump board | `pc\pumpboard.py` · `pc\pump_guard.py` · `tests\pump_testing.py` · `tests\test_pumpboard.py` · `tests\test_pump_guard.py` · `tests\test_port_safety.py` | WP1a | 3 days | **high** |
| **WP3** | Foundation (no pump board) | `pc\recipe.py` · `pc\recipes\plasma-only.csv` · `pc\recipes\dose-measure-drain.csv` · `pc\recipes\cleaning.csv` · `pc\store.py` · `pc\migrate.py` · `pc\pinlock.py` · `pc\alarms.py` · `pc\rpilink.py` · `pc\schedule.py` · `tests\test_recipe.py` · `tests\test_store.py` · `tests\test_rpilink.py` · `tests\fixtures\data-1.3.0\**` | — (reads `pc\vendor\v7\config\hardware.yaml` in tests after WP1a) | 3 days | medium |
| **WP4** | Core: devices, runner, manual | `pc\devices.py` · `pc\steps.py` · `pc\runner.py` · `pc\manual.py` · `tests\core_testing.py` · `tests\test_interlock.py` · `tests\test_runner.py` · `tests\test_manual.py` | WP2, WP3 (stubs allowed to start) | 3 days | **high** |
| **WP5** | Application and HTTP | `pc\app.py` · `pc\application.py` · `pc\httpd.py` · `tests\test_app.py` · `tests\test_api_pin.py` | WP2, WP3, WP4 | 2 days | medium |
| **WP6** | Web frame | `pc\web\index.html` · `pc\web\css\base.css` · `pc\web\css\components.css` · `pc\web\js\main.js` · `api.js` · `store.js` · `dom.js` · `dialog.js` · `labels.js` · `frame.js` · `chart.js` · `tests\test_web_static.py` · **removal** of `pc\web\app.js` and `pc\web\style.css` (last, at integration) | API.md (starts at once); WP5 for real data | 3 days | medium |
| **WP7** | Web operator pages | `pc\web\css\pages.css` · `pc\web\js\pages\dash.js` · `schedule.js` · `manual.js` · `runs.js` · `alarms.js` | WP6's frozen contract | 3 days | medium (size) |
| **WP8** | Web Maintenance | `pc\web\css\maint.css` · `pc\web\js\pages\maint.js` · `maint-recipe.js` · `maint-link.js` · `maint-plasma.js` · `maint-pumps.js` · `maint-spectra.js` · `maint-operators.js` | WP6's frozen contract | 2½ days | low |

Files of the tree that **no package touches**: `rpi\**`, `tests\test_tcpd.py`, `tests\test_measure.py`, `tests\simulated_rig.py`,
`tests\simulated_spectrometer.py` (RPi review — it must also set `VERSION = '2.0.0'` in `rpi\tcpd\plasmic_tcpd.py`, or `Install.cmd`
will not push the RPi programs); `tests\test_flash_nano.py`, `pc\netcheck.py`, `pc\plasmic_client.py`, `pc\examples\*`,
`pc\runtime\**`, `pc\data\**`, `nano\**`, `SHA256SUMS.txt`, `Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`, `Install.cmd`,
`Flash-Nano.cmd`, `installer\unpack-python.cmd`, `installer\*.ps1`, `installer\flash_nano.py`, `installer\site.json`,
`docs\tools\check_sums.py`, `docs\tools\simulated_rpi_ssh.py`, `docs\guide-source\build_guide.py`, `docs\guide-source\split_guide.py`,
`docs\2-first sentence.txt`, `docs\backup\`, `docs\Inbox\`.
Every other file of the 2.0.0 build is in exactly one row of the table above.

---

## H.2 Order

```
day 0   WP1a ──────────┐                 WP6 step 1: index.html, base.css, components.css, dom.js, dialog.js, api.js,
        WP3 ───────┐   │                             store.js, labels.js, main.js skeleton — against the JSON examples of
                   │   ▼                             API.md, no server needed. Contract of H.3-WP6 frozen at the end of day 2.
                   │  WP2                 WP7, WP8 start on day 3 against that contract (static DOM + CSS first).
                   ▼   ▼
                  WP4 ───► WP5 ───► WP6 step 2 ∥ WP7 ∥ WP8 on the practice mode ───► WP1b ───► lead: integration ───► owner
```

- WP1a first (half a day): everything that imports the vendored code waits for it.
- WP2 and WP3 in parallel. WP4 starts with stubs of `PumpBoard` / `RpiLink` (signatures of SPEC B.13) and finishes on the
  real ones. WP5 after WP4.
- WP1b last: the guide needs the final test count and screenshots of the finished pages.
- With four people: about 8 working days, then the owner's rig checks (SPEC section J, in that order).

**Integration by the lead** (nobody else): all test files; `check_sums.py`; `check_v7_sync.py`; delete `pc\web\app.js`,
`pc\web\style.css` and any `__pycache__` folder (one exists today under `pc\`); a walk through the practice mode (simulators
only) page by page with SPEC E beside it; `pc\data\config.json` still points at `192.168.1.1`; `git`-less diff of the launchers
(only the two `rem` lines of `Set-PIN.cmd` changed). No step of the integration touches COM3 or the RPi.

---

## H.3 Briefs

### WP1 — Vendoring (a) · packaging and documents (b)

**Read**: SPEC F (all), G rows `test_vendor.py` / `test_installer.py`, I, J; `installer\install.py` (how it imports `app` and
its file list, lines 36, 132, 217-228, 371-385); `docs\guide-source\README.txt`; `historical-development-notes.md`.

**Build — phase a**
1. Copy **byte for byte (binary mode)** the files of SPEC F.1 and F.2 to their destinations. No `.pyd`, no `__pycache__`,
   no `backend\main.py`. Copy every `*.py` found under V7's `.venv\Lib\site-packages\serial` and `…\yaml` (sub-folders
   included); copy the licence / metadata files that exist in the two `dist-info` folders and write down in
   `pc\vendor\README.txt` exactly which were found. Do not write any licence text yourself.
2. Copy the five font files and the two OFL texts from HMI `web\vendor\` under the new names (SPEC F.2).
3. `docs\tools\check_v7_sync.py` (SPEC F.3); run it with `--write-manifest`, then without arguments, then with `--v7`.
4. `tests\test_vendor.py` (SPEC G).

**Build — phase b**
5. `installer\install.py`: SPEC F.5 only. `tests\test_installer.py`: add the cases of SPEC G; do not edit the RPi cases.
6. `Set-PIN.cmd`: the two `rem` lines (ASCII, CRLF).
7. Guide: edit `installation-and-usage.src.html` by the list of SPEC I; the step-5 checklist is the "first check" column of SPEC J;
   retake the screenshots in practice mode (update `shots_app.py` to the new routes and the README list); rebuild with
   `build_guide.py <test count>`. No `<script>` in the guide. Headings that programs cite stay unchanged.
8. `READ-ME-FIRST.txt`, `historical-development-notes.md`, `docs\work record.md` (new section 13), `docs\1-How to continue on another computer.txt` by SPEC I. Nothing is called
   "verified" that was only simulated.

**Test alone**: `…python.exe -B .\tests\test_vendor.py` · `…\tests\test_installer.py` · both check tools.
**Done when**: the manifest matches; the vendored modules import under the bundled interpreter with the simulator;
`check_sums.py` reports nothing changed; the guide builds; every launcher except `Set-PIN.cmd` is byte-identical.

### WP2 — Pump board

**Read**: SPEC 0.2, 0.3, B.3 (all twelve parts), B.4 (the gate), B.13, D.1 (`pumpboard.json`, logs), G rows of this package;
`understand\v7-integration-notes.md` (all), `v7-backend.md`, `v7-firmware.md`; the vendored sources
`pc\vendor\v7\backend\transport.py`, `simulator.py`, `functions\motors.py`, `pump.py`, `scale.py`, `config\hardware.yaml`.

**Build**: `pc\pumpboard.py` (`PumpBoard`, `CheckedLink`, `PumpBoardError`, `RealPortForbidden`, `allow_real_ports`, port
listing, V7 loader, monitor thread, `view()` in the shape of API.md `state.pump`, `hardware()` in the shape of
`bootstrap.hardware`); `pc\pump_guard.py`; the test helper and three test files.

**Must know**
- V7 files are never edited. `transport.get_link()` is never called. The cached config must never hold a usable port.
- `OK` to `V` / `P` / `C` means accepted, not finished. `V,<m>,0` is a no-op for a motor that is not the selected one of its
  group. A `P` / `V` to another motor of a group silently replaces the running one. `X` switches the weight stream off.
  `PUMP,…,error=n` is not an exception in V7. `motors.dose_by_weight` blocks its caller and is cancelled by `motors.stop` /
  `motors.stop_all`. `Link.snapshot()` is a shallow copy. `Link.send` returns stray lines as success when the terminator
  never came.
- Reset detection uses `link.state['firmware']`, not the subscriber queue. `view()` never sends a serial command.
- Stops never take `op_lock`. Every wait checks `abort`, the link and the reset flag at least every 0.1 s.
- The simulator does not scale cleaning times, valve delays or `settle_s`: tests pass overrides through `overrides`.
- `pump_guard.py` is tested with a simulated `serial` module injected through `sys.modules`; it is never started by a test.
- The static checks of `test_port_safety.py` skip a file that does not exist yet (`pc\app.py` of 2.0.0, `pc\application.py`);
  at integration none may be skipped.

**Test alone**: `…python.exe -B .\tests\test_port_safety.py` · `test_pumpboard.py` · `test_pump_guard.py`
(`PumpBoard` is built with a stub store exposing `pump_settings()`, `save_pump_settings()`, `event()` until WP3 is there).
**Done when**: every case of SPEC G for these files passes; the tripwire record is empty; the vendored `hardware.yaml` hash is
unchanged after the run; no test takes longer than 20 s.

### WP3 — Foundation (Recipe, store, migration, PIN, alarms, RPi link, schedule arithmetic)

**Read**: SPEC A (all), B.2, B.6, B.8, B.9, B.10, B.13, D (all), G rows of this package; `pc\app.py` in full (the code to
move: `write_json`, `validate_config`, `site_config`, `PinLock`, `Store`, `run_csv`, `Link`, `Schedule._ready`,
`Schedule._wait_for_job`); `pc\schedule.py`; `rpi\tcpd\plasmic_tcpd.py` lines 279-336 (the texts `rewrite_fix` must cover)
and 971-1025 (status shape); `understand\hmi-controller.md` §csv workflow for the parser shape of the original.

**Build**
- `pc\recipe.py`: pure functions, no import of `pumpboard`, `app` or vendored code; `hardware` comes in as a dict
  (shape of `bootstrap.hardware`). Messages exactly as SPEC A.3. The three CSV files of A.6.
- `pc\store.py`, `pc\migrate.py`, `pc\pinlock.py`, `pc\alarms.py`: SPEC D, B.9, B.10. **Move code, do not reword it**: event
  texts and error texts of 1.3.0 are asserted by tests. `store.py` must import without `app.py`.
- `pc\rpilink.py`: `Link` moved as it is and renamed `RpiLink` (`Link = RpiLink`), `sequence_fields` / `SEQUENCES` / `raw()`
  removed, the additions of SPEC B.2. `_ready` → `rpi_ready()` and `_wait_for_job` → `wait_job()` keep their texts
  (“The pre-discharge inspection failed:”, “and RPi ’s connection is interrupted and the results of this measurement cannot be confirmed; please go to the site to confirm the equipment.” For, “exceeds 360 seconds haven’t ended yet…”).
  `UI_WATCHDOG_SECONDS = 10.0` lives here and is read at use time (tests patch `rpilink.UI_WATCHDOG_SECONDS`).
- `pc\schedule.py`: only the additions of SPEC B.6.

**Must know**: tests never open the real `pc\data` (the fixture folder is synthetic and is copied to a temp folder before
use); nothing is ever deleted; `pin.json` and `config.json` keep their formats; the TCP protocol is not changed; the old
`pc\app.py` keeps working beside the new modules until WP5 replaces it (do not edit it).

**Test alone**: `…python.exe -B .\tests\test_recipe.py` · `test_store.py` · `test_rpilink.py` (uses `test_tcpd.SimServer`).
**Done when**: the cases of SPEC G pass; `recipe.validate` on the three examples gives the facts written in SPEC A.6;
a migrated copy of the fixture can still be loaded by the unchanged 1.3.0 `Schedule._load` logic (keys only added).

### WP4 — Core: devices, steps, runner, manual

**Read**: SPEC A.2 (completion and limits), A.4, B.4, B.5, B.6, B.7, B.11 (locks), B.12 (the acceptance table), B.13, D.2
(round, schedule, run-log shapes), API.md C.4.1, C.4.2, C.5, C.6 (`round`, `schedule`, `interlock`, `owner`);
`pc\app.py` lines 566-826 (the 1.3.0 `Schedule` whose loop structure and texts are kept); `understand\hmi-controller.md`
(runner, pause / resume of the original).

**Build**: `pc\devices.py`, `pc\steps.py`, `pc\runner.py`, `pc\manual.py`; `tests\core_testing.py` (one helper that builds
Store + Alarms + RpiLink on `SimServer` + PumpBoard in sim mode with the tripwire + Devices + Runner + ManualOps in a temp
folder, with `schedule.MINUTE` shrunk as the 1.3.0 tests do); the three test files.

**Must know**
- No device I/O under `Runner.lock` or `Devices.lock`. Lock order `Runner.lock` → `Devices.lock` → `PumpBoard.lock`.
- `finally` always releases ownership and leaves outputs off. A discharge is never repeated; only the result read is tried twice.
- `hv_pending` is set **before** the pump check (the race test runs 200 rounds).
- The round record is written at the start, at each device step, at most once a second during a `delay`, and at the end.
- Slot rules, `START_FAILURES = 3`, `KEEP_SLOTS = 2000` and the event sentences of the 1.3.0 loop are kept
  (“Schedule No. N times failed to start:…”, “Continuous 3 times:…”, “No. N measurements did not end normally and the schedule has been stopped:…”, “Scheduled measurement end (…): Completed N times”).
- A schedule keeps running when nobody polls `/api/state`; only manual operations and the maintenance discharge are tied to the page.

**Test alone**: `…python.exe -B .\tests\test_interlock.py` · `test_runner.py` · `test_manual.py`.
**Done when**: every row of SPEC B.12 has a passing test; after every test the simulator shows both groups idle, valves
closed, and the RPi simulator shows `run_stop == 0`.

### WP5 — Application and HTTP

**Read**: API.md (all), SPEC B.1, B.10, B.11, B.13, F.4, G rows of this package; `pc\app.py` (the handler, `main()`, the
practice-mode start of the RPi simulator); `tests\test_app.py` (the cases to port); `installer\install.py` lines 36, 132,
228, 378-385 (what it imports from `app`).

**Build**
- `pc\app.py`: `sys.dont_write_bytecode = True` right after the `sys.path` line; `VERSION = '2.0.0'`; the import surface of
  SPEC B.1; `main()` with `--set-pin` (new closing sentence, SPEC B.10), the new option `--sim-port` (practice mode only,
  default 8767), `pumpboard.allow_real_ports()` only without `--demo`, `SetConsoleCtrlHandler` (SPEC B.11), console lines
  as 1.3.0.
- `pc\application.py`: `Application(data_dir, demo=False, auto_sync=True, pump_mode=None, pump_overrides=None, sim_port=8767)`;
  start order of SPEC B.11; `state()` exactly as API.md C.6; one thin method per route; the PIN table; `close(fast)`.
  The RPi simulator child is started with `-B`.
- `pc\httpd.py`: `ExclusiveHTTPServer`, `make_handler(app, csrf)`, a route table (method, path pattern, PIN yes / no, busy
  rule, body limit) that `tests\test_api_pin.py` and `tests\test_web_static.py` read; the static table of API.md C.2; the
  error mapping of C.1.
- `tests\test_app.py`: **ported, not rewritten** (SPEC G). `tests\test_api_pin.py`: table-driven from the route table.

**Must know**: `import app as pc` must keep working for the installer (`write_json`, `VERSION`, `validate_config`,
`DEFAULT_CONFIG`) and must not open anything at import; `/api/stop` is always 200; every GET is open; `/api/state` never
renews a PIN session; the token never appears in a reply; `Application(tmp)` (the old harness form) leaves the board `off`.

**Test alone**: `…python.exe -B .\tests\test_app.py` · `test_api_pin.py`.
**Done when**: both files pass; `Application(demo=True)` in a temp folder shows both simulators connected and
`backend.transport.serial` is the forbidden object; every key of API.md C.6 is present in `/api/state`.

### WP6 — Web frame

**Read**: SPEC E.1–E.3, E.5.3, E.8 (words), E.17; API.md C.1, C.2, C.3.1, C.4.1, C.4.2, C.6; `understand\hmi-ui-style.md`
(§2 tokens, §3 frame, §4 components, Appendix A.4 = the original CSS to start from, §7.1); `understand\hmi-ui-structure.md`
(frame logic of the original); the screenshots; 1.3.0 `pc\web\app.js` (the chart code to move into `chart.js`).

**Build**: `index.html`, `css\base.css`, `css\components.css`, the eight `js\*.js` files, `tests\test_web_static.py`.
`main.js` loads each page module with a dynamic `import('/js/pages/<name>.js')` and shows a placeholder panel when a module
is not there yet, so WP7 and WP8 never edit a WP6 file. Page ids: `dash`, `schedule`, `manual`, `runs`, `alarms`, `maint`;
tab ids: `recipe`, `link`, `plasma`, `pumps`, `spectra`, `operators` (modules `maint-<id>.js`).

**Contract frozen for WP7 / WP8 at the end of day 2** (exports; change only through the lead):

```js
// api.js
get(path) -> Promise<object>;  post(path, body) -> Promise<object>;  class ApiError { status, code, message, reasons }
// store.js
store.boot, store.state, store.stale, store.unlock, store.form, store.preview;  store.subscribe(fn);  store.setForm(patch);  store.setUnlock(session|null)
// dom.js
h(tag, props, ...children);  setText(el, text);  setClass(el, name, on);  list(container, rows, keyFn, makeFn, updateFn);  pager(container, total, perPage, onChange) -> {page, set(page)}
// dialog.js — all return a Promise that resolves to the value, or null when cancelled or timed out
confirm({title, lines, detail, warn, ticks, confirmLabel, variant, timeout});  choose({title, choices, wide});
pad({title, min, max, decimals, unit, value});  pin({title});  text({title, value, maxLength, rule, masked});
form({title, fields, ticks, confirmLabel});  message(title, text, {level, links})
// labels.js
labels.round/slot/schedule/step (from bootstrap), labels.nano, labels.fault, labels.priority, labels.group;
fmtTime(iso, pattern);  fmtDuration(seconds);  motorLabel(id);  pumpText(pump);  stopMessage(reply)
// chart.js
drawSpectrum(svgBox, {axis, series: [{values, cls}], range: [nmFrom, nmTo] | null})
// page module (WP7, WP8)
export function mount(root, ctx);  export function render(state, ctx);  export function show(ctx);  export function hide(ctx)
// ctx
{api, store, dialog, dom, labels, chart, go(page, arg), message(text, level), unlocked()}
```

**Must know**: CSP — no inline script or style attribute, no `innerHTML` with data, no `eval`; styles at run time through
`style.setProperty`. One dialog at a time. `render` is idempotent and never replaces a control under the pointer.
`pc\web\app.js` and `style.css` are deleted only at integration (WP8 reads them until then).

**Test alone**: `…python.exe -B .\tests\test_web_static.py`; open `index.html` through the practice mode once WP5 exists
(before that: a local file server is not needed — feed `store` with the JSON of API.md C.6 from the browser console).
**Done when**: the frame matches the geometry in SPEC E.2–E.3 (regions, sizes, fonts); top bar, banner, nav, footer and every
dialog variant of SPEC E.2–E.3 work against the practice mode; the static test passes over all web files present.

### WP7 — Web operator pages

**Read**: SPEC E.4–E.8, E.16 (rows Overview … Manual operation), E.17; API.md C.3 (recipes, runlog, rounds, runs, events), C.4.2, C.5, C.6;
the page layouts documented in the generated English guide for Overview, Schedule, Manual Control, Run History, and Maintenance;
`understand\hmi-ui-style.md` §3.10 (page layouts) and §4.

**Build**: `css\pages.css` and the five page modules. Overview: D1–D5 with the three bodies of D3. Schedule measurement: tiles, estimate
lines, slot log (the start dialog itself is WP6's `frame.js`; this page only edits `store.form`). manual operation: notice bar, nine
rows, syringe and weight panels, all dialogs of E.6 with the `hv_off_checked` tick when `interlock.manual.need_hv_ack`.
execution record: list + in-page detail (spectrum / Steps). alarm: both tabs.

**Must know**: only files of this package; shared things through `ctx`; lists never scroll (pager); a disabled control
always shows its reason; no control without a job (no refresh buttons — everything follows the 1 s poll and `revs`).

**Test alone**: `tests\test_web_static.py` (WP6's file covers these modules); walk each page in practice mode against SPEC E.
**Done when**: every text, state and dialog of SPEC E.4–E.8 is on screen in practice mode; a count-1 schedule of “Discharge measurement only”
and of the pump example (imported through Maintenance once WP8 exists, or published by a test call) can be followed on Overview and
opened in execution record.

### WP8 — Web Maintenance

**Read**: SPEC E.9–E.15, E.16 (row Maintenance), E.17, B.10 (PIN rules on the page); API.md C.3.2, C.4 (all gated routes), C.4.3,
C.6 (`rpi`, `pump`, `interlock.plasma`, `unlocked`); 1.3.0 `pc\web\app.js` — the settings / diagnose / find dialogs
(about lines 1283-1403) and the parameter table (lines 13-22) are **moved, not redesigned**;
`understand\hmi-ui-structure.md` (maintenance pages of the original).

**Build**: `css\maint.css`, `maint.js` (login panel, tab bar, message chip, self-lock after 600 s without input) and the six
tab modules.

**Must know**: the unlock session lives in `sessionStorage['plasmic.unlock']` (through `store.setUnlock`); a `locked` answer
returns to the login panel with “Maintenance mode has automatically logged out”; the Recipe tab reads the chosen file as bytes and sends base64 to
`/api/recipes/validate` — no decoding in the browser; there is no step editor; the port dialog lists only Arduino Mega ports;
the plasma parameters button follows the state (SPEC E.12); deleting a Recipe asks for the PIN again.

**Test alone**: `tests\test_web_static.py`; walk the six tabs in practice mode (PIN 000000).
**Done when**: every text, state and dialog of SPEC E.9–E.15 is on screen in practice mode; importing each of the three
example files, publishing one and deleting it works; a maintenance discharge runs on the simulator with the tick box and is
refused while a simulated pump runs.
