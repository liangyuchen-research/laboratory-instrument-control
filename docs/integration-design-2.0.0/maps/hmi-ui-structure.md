# HMI v1.1 — UI information architecture (subsystem: hmi-ui-structure)

Source folder: `C:/Users/PlasmaLab/Desktop/UI/UI/hmi_v1_1` (called HMI below). All paths are relative to it. Line numbers are `file:line`.

## 0. What was read, and caveats

Read completely:

- `web/index.html` (2573 lines, 199 KB) — the whole live UI: CSS (lines 18–468), template (471–1039), logic (1042–2571).
- `web/workflow/index.html`, `web/workflow/app.js`
- `web/test/index.html`, `web/test/app.js`
- `web/trend/index.html`, `web/trend/app.js`
- `web/calibration/index.html`, `web/calibration/app.js`
- `shell-extension.js`, `ui_features.json`, `web/shell.css`, `web/original/source.json`
- `web/i18n.js` (all 532 lines)
- Also read because they decide UI behaviour: `README.md`, `auth.py`, `server.py` lines 96–455 (routing and admin gate), `admin-page.html`, `manual-page.html`, `lite/app.js`, the three CSV templates, `web/custom/*.example.json`, launcher `.cmd` files (text only, not run), the head of the design-system CSS and of the sub-page CSS files, and parts of `web/original/support.js` (the template runtime).

Not read: `backend.py`, `controller.py`, `plc.py`, `scheduler.py`, `csv_workflow.py`, `calibration.py`, `measurements.py`, `rpi_pairing.py`, `rpi_tools/`, `tests/`, the remaining 1700 lines of `support.js`.

Rule slip to report: two broad `Glob` calls (`*.{json,js}` and `*.{py,cmd,...}`) recursed and returned **file names** under `data/` and `offline_bundle/`. No file in `data`, `.venv`, `offline_bundle`, `dist`, `__pycache__` was opened. Nothing was modified, run, or connected.

Nothing below is guessed. Section 18 lists what could not be determined.

---

## 1. Technology (item j)

### 1.1 It is NOT in-browser Babel/JSX

- `web/index.html:7-10` loads four scripts: `/vendor/react.production.min.js`, `/vendor/react-dom.production.min.js`, `/original/support.js`, `/i18n.js`. Plus `/shell.css` (line 11).
- The page is a **"Design Component" (`.dc.html`) document** run by `web/original/support.js` ("dc-runtime", header: `// GENERATED from dc-runtime/src/*.ts — do not edit`).
  - Template: everything inside `<x-dc> … </x-dc>` (`web/index.html:14-1041`). Syntax: `{{ expr }}` bindings, `<sc-if value="{{ … }}">`, `<sc-for list="{{ … }}" as="x">`, `<helmet>` for head content, event attributes such as `onClick="{{ fn }}"`, `disabled="{{ bool }}"`.
  - Logic: one `<script type="text/x-dc" data-dc-script data-props='{"$preview":{"width":1024,"height":768}}'>` (`web/index.html:1042`) that must define `class Component extends DCLogic` (`web/index.html:1246`). The runtime evaluates it with `new Function("DCLogic","StreamableLogic","React", src + …)` (`web/original/support.js:842-851`).
  - `DCLogic` gives `this.state`, `setState()`, `componentDidMount/WillUnmount`, and **`renderVals()`** — returns one flat object the template renders against (`support.js:838`). There is no JSX. The template is turned into React elements by the runtime; React (production UMD build) only does the DOM diffing.
  - `vendor/babel.min.js` exists but is only loaded lazily by the runtime for `x-import` of JSX modules (`support.js:1176-1218`). The live page has no such import, so Babel is not used.
  - Because of `new Function`, the server sends `/` with CSP `script-src 'self' 'unsafe-inline' 'unsafe-eval'` (`server.py:105`); every other path gets `script-src 'self'` (`server.py:107`).
- The design system is `web/original/_ds/industry-9d663c7d-…/styles.css` ("Industry — design-system tokens and component classes"), loaded from `<helmet>` (`web/index.html:16-17`). Its bundle JS is an empty stub.
- README quirk worth knowing (`README.md:24`): the runtime wraps every `{{ value }}` in a `<span>`, so CSS selectors like `.x span` hit values too; the CSS uses direct-child selectors (`> span`) for that reason.

### 1.2 Page composition

One shell page plus iframes:

| Piece | File | How it is embedded |
|---|---|---|
| Shell: header, alarm banner, side nav, footer, pages Overview/Schedule measurement/manual operation/execution record/alarm/Maintenance settings, dialogs | `web/index.html` | served at `/` |
| Correction (standard calibration) | `web/calibration/index.html` + `app.js` + `style.css` | `<iframe src="/calibration/">` always present, shown/hidden with `display` (`index.html:512-514`) |
| Trend (trend) | `web/trend/…` | `<iframe src="/trend/">` always present (`index.html:696-698`) |
| test page (test) | `web/test/…` | `<iframe src="/test/">` only when `testEnabled` (`index.html:516-520`) |
| `/workflow/` long admin page | `web/workflow/…` | **not embedded anywhere in the live shell**; still served by the server (`server.py:225`). README line 57: "original `/workflow/` long page no longer exists HMI used in". |

The three sub-pages are plain vanilla JS (`'use strict'`, `document.getElementById`, hand-built DOM / SVG / canvas). They share the admin session through `sessionStorage['hmi_admin_session']` and fetch their own token from `/api/bootstrap`.

### 1.3 Legacy files (not part of the live UI)

- `shell-extension.js`: "Live adapter for the original design". `class Component extends DesignComponent`. Used only by `prepare_original_ui.py`, which renames the V1 design's class to `DesignComponent` and appends this file (`prepare_original_ui.py:82-86`). README line 61: `web/index.html` is now maintained directly; `shell-extension.js`, `manual-page.html`, `admin-page.html` "is for this script only". Useful only as a record of the first V1 wiring (it used `window.confirm`/`window.prompt`, 5 pumps, `/api/recipes/run`, `sessionStorage['hmi_selected_recipe']`, `['hmi_isolated']`).
- `manual-page.html`, `admin-page.html`: V1 template fragments (see 3.10).
- `lite/app.js`: an older engineering page (two tabs `manual` / `flow`, polling `/api/state` every 1000 ms, posting to `/api/plc`, `/api/rpi`, `/api/parameters`, `/api/validate`, `/api/start`, `/api/stop`). `server.py` has no `/lite/` route, so it is not reachable in v1.1.
- `web/original/source.json`: says the page descends from `…\design_handoff_hmi_backend\HM detector HMI.dc.html`.

### 1.4 Feature flag

`ui_features.json` = `{"test_page_enabled": false}`. `server.py:42-44` reads it; `--show-test-page` / `--hide-test-page` override (`server.py:433-448`). When false the server answers 404 `Test page disabled` for `/test/…`, `/api/history`, `/api/captures/…`, `/api/capture` (`server.py:121-122, 265-266`), and the shell hides the nav item.

### 1.5 Start-up

- `Start.cmd`: `offline_bundle\python-runtime\python.exe server.py --port 8766`.
- `Start-Demo.cmd`: same with `--demo` (also port 8766).
- `Start-Kiosk.cmd`: server minimised with `--no-browser`, waits up to 30 s for TCP 8766, then Edge `--kiosk http://127.0.0.1:8766/ --edge-kiosk-type=fullscreen`.
- `Set-Admin-Password.cmd`: `server.py --set-admin-password` (console prompt, see section 6).
- `server.py` default port is 8765 (`server.py:431`); the launchers pass 8766. **Port 8766 is also the port of the EC program (`pc\app.py`), per that project's historical-development-notes.md.**

---

## 2. Overall frame and navigation (item b)

Fixed canvas **1024 × 768 px**, no page scrolling (`.hmi`, `index.html:42`; `shell.css:2` sets `min-width:1024px; min-height:768px`). In demo mode the whole frame gets a 5 px amber inset border (`.hmi.demo`, line 43). Layout is a column:

| Zone | Height / width | Template lines | Content |
|---|---|---|---|
| top bar `.top` | 56 px | 474–488 | brand, mode chip, PLC chip, RPi chip, operator button, maintenance mode button, language switch, clock |
| Alert Banner `.banner` | 44 px | 491–501 | top alarm only, or "No active alarm" |
| body: Left navigation `.nav` | 116 px wide | 505–509 | page buttons |
| body: page area | 908 × 598 px | 512–966 | one page at a time |
| bottom action bar `.foot` | 70 px | 970–989 | Start scheduling / pause / Stop, progress, physical E-stop reminder |

Overlays, positioned inside the 1024×768 frame: faceplate (`.face`, 268 px wide, right side, lines 992–1007), RPi import panel (`.imp`, 760×500, lines 950–962), modal dialog (`.overlay` + `.dialog`, lines 1010–1038).

### 2.1 top bar (header), left to right

1. **Brand box** `.brandbox` (475–479): logo `<img class="brand-logo">` if `web/custom/branding.json` has one; product title — default text **`online metal wastewater detector`**; with branding, the per-language `product_name`. **Pressing and holding it for 3 s opens the maintenance login** (`HOLD_MS = 3000`, `holdStart()` line 1356). No visible hint. Context menu is suppressed.
2. **Mode chip** (480): `official mode`, or in demo `Simulation mode · No device connected` (striped amber).
3. **`PLC {state}` chip** (482): `communication is normal` (green dot) / `? Communication interruption` (magenta dashed) / `? unknown` (data stale) / `simulation`. Tooltip: `Modbus TCP {host} · N Updated seconds ago` or the PLC error.
4. **`RPi {state}` chip** (483): `Connected` / `not connected` (grey dashed, not an error) / `? unknown` / `simulation`. Tooltip: last RPi error, else `RPi is only required in the measurement step`.
5. **Operator button** `.opbtn` (484): small label `operator`, name or `Please select` (brown when none). Tap → dialog `Select operator`. Title `Change operator`.
6. **`maintenance mode`** button (485): only while a maintenance session is active; goes to the admin page.
7. **Language switch** (486): `Traditional` / `simple` / `EN`.
8. **Clock** (487): date `YYYY-MM-DD` above `HH:MM:SS`, updated every second.

### 2.2 Left navigation (side nav)

`NAV` (`index.html:1055`), in order:

| key | Label | Kind |
|---|---|---|
| `dash` | Overview | shell page |
| `schedule` | Schedule measurement | shell page (tap also reloads recipes and the schedule preview) |
| `manual` | manual operation | shell page |
| `trend` | Trend | iframe `/trend/` |
| `results` | execution record | shell page (tap loads `/api/run-log`) |
| `calibration` | Correction | iframe `/calibration/` |
| `alarms` | alarm | shell page |
| `admin` | `🔧 Maintenance` when logged out, `Maintenance settings` when logged in | pinned to the bottom (`.navmaint`: `margin-top:auto`, dashed border) |
| `test` | test page | only if `test_page_enabled`; iframe `/test/` |

- Selected item: solid blue (`--run` #2f5f8f), white bold text.
- Badges (`index.html:1860-1862`): on alarm the number of alarms, coloured by the worst unacknowledged priority (grey when all acknowledged); on Correction a `!` (orange) while the calibration flow waits for "Confirm fluid change" (`s.flow.prompt`).
- Deep links (`index.html:1271-1273`): `/#alarms` etc. open a page; `/#admin` or an admin tab key (`/#recipe`, `/#rpi`, `/#plasma`, `/#cal`, `/#spectra`, `/#operators`) opens the admin page on that tab (login first if needed). `?lang=en|zh-CN|zh-TW` sets the language.
- `Escape` closes the dialog, else the faceplate (`index.html:1262-1265`).

### 2.3 bottom action bar (footer) — present on every page

`index.html:970-989`, logic 2094–2141.

| Element | Text | Behaviour |
|---|---|---|
| Start button `.start` (140×54, blue; green when it means resume) | `Start scheduling` / `Scheduled` / `is executing` / `continue RESUME` | idle → `openScheduleStart()`; schedule paused → dialog `Continue scheduling` → `POST /api/schedule/resume`; single flow paused → dialog `Continue the process` → `POST /api/workflow-resume`. Disabled while running or when `startWhy` is non-empty. |
| Pause `.pause-btn` (amber outline) | `pause PAUSE` / `Pausing...` | schedule active → `POST /api/schedule/pause`; else `POST /api/workflow-pause`. No confirmation dialog. |
| Stop `.stop` (black outline) | `Stop STOP` | schedule active → dialog `Stop scheduling` → `POST /api/schedule/stop`; else immediately `POST /api/workflow-stop` (no dialog). |
| Progress area `.prog` | one of three states | (a) cannot start: label `cannot start` + reason, tap → dialog `The schedule cannot be started at this time`; (b) schedule active: four cells `Schedule` (`No. n / N Second-rate`), `Next measurement` or `at present` (`HH:MM (reciprocal) ` / `under measurement` / `Suspended` / `Stopping`), `stage`, `Not measured` (`n Second-rate`); (c) otherwise `message` + last message, tap → dialog `message`. |
| HV tag `.hvtag` | `⚡ contains high voltage` | only while an active schedule uses plasma. Tooltip `Schedules to automatically output high voltage for each measurement`. |
| E-stop notice `.estop` (red frame, red/yellow button icon) | `For emergency stop, please press the physical emergency stop button on the device.` | **not a button**. Tooltip `STOP is a software stop, not a safety level emergency stop.`. |

`startWhy` texts (`index.html:2095-2098`), first match wins: `Simulation mode does not start the device`; `HMI No response from server`; `Please go to ”Scheduling Measurement” to select Recipe`; `Recipe reading failed`; the preview error; `Schedule setting is being calculated...`; `PLC Communication interruption`; `RPi is not connected (maintenance personnel need to complete the RPi pairing)`.

---

## 3. Screens (item a)

### 3.1 Overview (`dash`) — `index.html:523-636`

Read-only monitoring page, four stacked blocks.

1. **Metal cards** (3, 84 px high): `copper Cu`, `zinc Zn`, `nickel Ni`. Each: tag `Corrected` (green outline) / `Uncorrected` (hatched), big value (`—` or number), sub-line. Sub-line variants (`index.html:2049-2053`): `mg/L · {time} · Blank zero point has been deducted {x}`; `mg/L · {conc_reason}`; `mg/L · Applied {MM-DD} Calibration, waiting for the next sample measurement`; `mg/L · has not been calibrated by the standard and does not display the concentration.`. Data: `s.measurement.latest_sample`, `s.calibration_active`.
2. **`process stage`** panel: header shows source (`Selected:` / `is executing:` / `Suspended:` / `last executed: {state}`) + recipe name, right side meta (`Schedule No. n/N times · steps a/b`, `Schedule {state} · next time HH:MM`, `No. r/R wheel · step a/b`, `common N step · R wheel`). Body: a row of numbered phase boxes; done = grey, current = blue, paused = amber, failed/stopped = orange. Empty text: `Not selected yet Recipe: Please go to ”Scheduling Measurement” to select Recipe`. Phases come from the server (`flow.phases` / `recipe.phases`), derived from the CSV (README line 59).
3. **`flow path diagram`** (P&ID, SVG 880×300, ISA-101 style, lines 551–627). Legend: `current path`, `operation(command)`, `Stop`, `communication failure`, `not connected`. Sub-header: `current path: {stage} — {illustrate}` or `No process is being executed · Click the device to open the detailed panel`.
   - Equipment drawn: `Wastewater sampling T-01` → **P1** `sampling cycle` → `circulating cup T-02` → **P2** `comes with mixing tank` → `mixing tank T-03` → **P4** `Send detector` → `plasma detector` (`AE 101`) → **P5** `waste pump` → `Waste discharge`; above the mixing tank `pure water DI` → **P3** `pure water pump`; below it `Nitric acid CH2` and `Air CH3` feeding `SP syringe pump · CH1 Drainage → mixing tank`.
   - P6 (`standard pump`) is **not** drawn (`index.html:1047` comment).
   - Three hatched bars titled `Liquid level is not connected` / `weight is not connected` and a label `weight is not connected` at the mixing tank: placeholders for sensors that never existed.
   - Pump symbol: filled = `command ON`, hollow = `command OFF`, magenta dashed = comm failure, grey dashed = no source. A running pump shows a value box `{direction} {rpm} rpm` + `{n} s automatically stop after` or `process control`.
   - Active path = thick dark line. Path per phase is hard-coded: `PHASE_PATH` and `PHASE_DESC` (`index.html:1075-1080`); phases are `Prepare Sampling Send Sample Pure Water Add Acid Air Push Mix Send for Inspection Measure Waste Discharge Standby`.
   - Detector status text under the detector: `HSPY OFF` / `HSPY ON` / `? HSPY Invalid readback` / `RPi not connected` / `simulation mode`.
   - Alarm markers on equipment (shape + colour + priority number): `EQUIP_MARK` positions for `DET`, `P1`–`P5` (`index.html:1224-1225`).
   - Clickable hit areas open a **faceplate** (non-modal, toggles): P1–P5, the detector (`plasma detector AE-101 detailed status`), the syringe pump (`Syringe pump detailed status`).
4. **Status strip** (6 cells, 56 px): `PLC Communication` (`normal · N seconds ago`), `pump direction/speed` (`CW · 200 rpm`), `Syringe pump setting amount` (`x.xx mL`), `HSPY high voltage power supply`, `Nano controller` (`{state} · {fault}`), `active alert` (`N (unconfirmed M) ` / `none`).

**Faceplates** (`index.html:2528-2554`):

- Pump `P{n} {Name}`: rows `bit number/scratchpad`, `state`, `direction/speed`, `automatic stop` (`left n Second` / `settings n Second`), `Operation restrictions`. Note `status is PLC Command register readback does not represent actual traffic.` Buttons `ON` / `OFF` (same handlers and confirm dialog as the manual page) and `Manual operation page`.
- `SP syringe pump`: rows `channel` (`CH1 Drainage · CH2 Nitric acid · CH3 Air`), `PLC Set amount`, `Location/Readback completed` (`Not provided (40042–40044) `), `Operation restrictions`. Button `Manual operation page`.
- `AE-101 plasma detector`: rows `RPi connection`, `HSPY high voltage power supply`, `Nano controller`, `concentration` (`corrected (MM-DD Apply:Cu, Zn…) ` / `Uncorrected`). Note `RPi connections, plasma parameters and correction coefficients are managed by maintenance personnel. The ”Measurement Process” of ”` Button `Schedule measurement`.

### 3.2 Schedule measurement (`schedule`) — `index.html:701-724`, logic 2451–2510

See section 10 for full detail. Left panel `Schedule setting` (7 tiles + estimate + warnings), right panel slot log table.

### 3.3 manual operation (`manual`) — `index.html:639-693`, logic 1952–2036

See section 8.

### 3.4 Trend (`trend`, iframe `/trend/`) — see section 11.2

### 3.5 execution record (`results`) — `index.html:727-741`, see section 11.1

### 3.6 Correction (`calibration`, iframe `/calibration/`)

Title `standard calibration`. **Not behind the PIN to open or to run**; only "application coefficient" needs a maintenance session. Three tabs plus a status line `Current coefficient (YYYY/M/D correction):Cu a=… b=… c=…; …` (`calibration/index.html:11-17`).

**Tab `Calibration setting`**

- Panel `standard (mixed standard,mg/L) `, sub-text `Leave blank=This bottle does not contain this metal; blank fixed number one`. Table columns `Name | Cu | Zn | Ni | Number of rounds | ✕`. Buttons `＋ New concentration` (max 12, default name `STD-n`, rounds 3), `Import CSV`, `remit CSV` (file `calibration_standards.csv`, header `Name,Cu (mg/L),Zn (mg/L),Ni (mg/L),Number of rounds`). Note `system automatically sorts from low to high concentration`.
- Panel `Each round process`: select `standard product Recipe`, select `Fluid replacement and draining Recipe (optional)` (recipes with RPi steps are not offered), stepper `Number of pre-running rounds for each concentration (not included in regression)` 0–3, summary lines (`concentration number (including blank)`, `The number of rounds included in the regression`, `Total number of measurement rounds (including pre-run)`, `Waiting time for each round`, `Fluid replacement and draining`, `Estimated total time (excluding fluid change)`), warnings, error. Template downloads `/calibration/standard_round_template.csv`, `/calibration/prime_template.csv`.
- Buttons `Save draft` → `POST /api/calibration/plan {plan}`; `Start calibration` → dialog `Start standard calibration` (confirm label `Confirm high voltage and start`, red line `activates high-voltage plasma every round. Please confirm that the detector cover is closed, no one is touching the high-voltage parts on site, and a physical stop method is in place.`) → `POST /api/calibration/start {plan, confirm_high_voltage:true}`.
- Editing auto-validates after 400 ms (`POST /api/calibration/check {plan}`) and auto-saves after 1200 ms.

**Tab `execution`** (badge `!` when waiting)

- Prompt bar `Waiting for fluid change` + server message + big button `Confirm fluid change` → `POST /api/calibration/confirm {}`.
- Panel title `Calibration in progress:{Recipe}` / `Last calibration:{state}` / `There are currently no calibrations in progress.`; sub `No. r/R wheel · {stage}`; button `Stop calibration` → dialog → `POST /api/workflow-stop {}`.
- Level cards (name, concentrations, `Measure k/n · pre-run d/D` or `Complete n wheel`), event log (last 30 flow events).
- The page switches itself to this tab when a prompt appears (`calibration/app.js:474`).

**Tab `results and records`**

- Left list `calibration record`: time, status chip (`Executing Analyzing Analyzed Analysis failed Failed Stopped Interrupted Applied`), `{n} concentration · {m} wheel`, `Cu R² …`.
- Right detail: metal tabs `Cu` / `Zn` / `Ni` (with `✕` if no valid fit); regression chart (x `light intensity x (RPi area method)`, y `concentration y (mg/L)`); coefficient list `a (slope) `, `b (intercept) `, `c (fine-tuning, reset when applied)` = 1, `R²`, `Points`, formula `concentration = (a × light intensity + b) × c`; warning when R² < 0.995; table `included | standard product | concentration | n | average light intensity | SD | RSD %` with a checkbox per level → `POST /api/calibration/exclude {id, exclude}`; table `round | concentration | light intensity x | regression value | residual`; analysis note.
- Buttons `Reanalysis` → `POST /api/calibration/analyze {id}`; `application coefficient` / `Reapply` → dialog `Apply correction coefficient` (`needs repair PIN Login. The old coefficients are kept in the audit log.`) → `POST /api/calibration/apply {id}`. Without a session the toast says `application needs maintenance PIN: Please click on the left first🔧 Maintenance” Log in and click Apply`.

Polling: `GET /api/calibration` every 2 s while visible, every 10 s while its iframe is hidden (`calibration/app.js:484-488`).

### 3.7 alarm (`alarms`) — `index.html:744-774`

Two tabs (segmented buttons): `alarm list (N) ` and `operation event`.

- **Alarm list**: counters `Emergency n`, `high n`, `middle n`; button `Confirm all`. Table `Occurrence time | level | area | message | state | action`. State text `in action/Restored · Confirmed/Not confirmed`. Row button `confirm`. Tapping the message opens the detail dialog. Unacknowledged rows have a pale amber background. 8 rows per page. Empty text lists the alarm conditions.
- **operation event**: sub-title `audit.jsonl recent 50 Pen`; table `time | event`; 12 rows per page. Data = `s.events` from `/api/state`.

### 3.8 Maintenance settings (`admin`) — `index.html:777-966`

**Locked state** — panel `Maintenance login`, sub-title `calibration, spectral raw data, plasma parameters,Recipe, RPi with operator list for maintenance personnel only`.

- If no PIN file exists: `has not been set for maintenance PIN` + `Please execute on the computer Set-Admin-Password.cmd settings 6–8 Digital repair PIN , restart HMI.` + button `Return to overview`.
- Else: a dots field (`●` per digit), an error line, a 3×4 PIN keypad `1 2 3 / 4 5 6 / 7 8 9 / Clear 0 ⌫` (max 8 digits), buttons `Return`, `English keyboard` ⇄ `Numeric keys` (switches to the full on-screen keyboard for legacy 12+ character passwords), `log in`.
- Login: `POST /api/admin/login {pin}` → `{session, expires_in}`; the session string goes to `sessionStorage['hmi_admin_session']` and is sent as header `X-Admin-Session` on every later request (`index.html:1364-1374, 1348`). Failure text `Login failed:{server message}`.

**Open state** — bar with six tabs (`ADMIN_TABS`, `index.html:1162`), a message chip (tap → dialog `Maintenance operation message`; on failure it reads `⚠ Operation failed · Click here for details`), and `Sign out` (`POST /api/admin/logout`, then back to Overview).

#### Tab `Recipe` (`recipe`)

- Left `Published Recipe`: list (6 per page), each `name` + `{n} step · with plasma|Contains RPi · For scheduling`. Footer `Import CSV files` (file input, `.csv`).
- Right, when a published recipe is selected: facts `steps/wheel`, `RPi/plasma`, `For scheduling`, `stage`, `SHA-256` (first 12 chars), `High voltage confirmation` (`Confirm when starting schedule` / `unnecessary`); CSV table `List | action | target | value | note` (7 rows per page); buttons `delete Recipe` (disabled while that recipe is in an active schedule) and `Set as schedule Recipe` / `Already scheduled Recipe`.
- Right, after importing a file ("new Recipe (not released)"): `POST /api/validate {csv}`; facts `Name`, `verify` (`passed` / `failed`), `steps/wheel`, `Total waiting time`, `RPi/plasma`, `stage` or `mistake`; buttons `Modify name` (on-screen keyboard, max 60), `discard`, `release` → dialog `release Recipe` → `POST /api/recipes/publish {name, csv}`.
- Delete: dialog `delete Recipe` → second dialog `input repair PIN to delete` (PIN pad) → `POST /api/recipes/delete {id, pin}`.
- `Set as schedule Recipe` only writes the id into the local schedule form (`useRecipe`, `index.html:1407-1410`); it is not a server call.
- CSV action vocabulary seen in the templates: `pump_speed`, `pump_direction`, `pump_on`, `pump_off`, `delay`, `syringe_aspirate`, `syringe_dispense`, `repeat_start`, `repeat_end`, `rpi_status`, `nano_prepare`, `plasma_run` (plus `spectrum_capture`, `nano_stop`, `power_off` listed in `lite/app.js:36`). Columns: `action,target,value,unit,note`.

#### Tab `RPi connection` (`rpi`) — `index.html:2255-2316`

Left panel `RPi state` (sub-title `user@host:port`), rows:
`SSH connection`, `connection method` (`pairing key · automatic reconnection` / `pairing key · Manually interrupted` / `Not yet paired`), `HSPY high voltage power supply`, `Nano controller`, `D13 output` (`LOW` / `HIGH`), `Nano firmware output capability` (`pulse version (can output high voltage)` / `security version (output locked)`), `manual pump interlock` (`allowed` / `Deactivated:…` / `RPi Not connected, not checked`), `RPi tool` (`is the latest` / `needs to be updated:…`), `Last updated`, `Recent errors`.

Right panel `Operation` — a 2-column grid of large buttons (label + small sub-text):

| Button | Sub-text | Call |
|---|---|---|
| `pair RPi (first) ` / `re-pairing RPi` | `Verify fingerprint＋One-time password, then automatically connect` / `replace RPi or use after refilling` | `POST /api/rpi/hostkey {}` → dialog `pair RPi: Verify host fingerprint` → dialog `RPi account password (only use this once)` (masked on-screen keyboard) → `POST /api/rpi/pair {password, fingerprint}` |
| `Disconnect` / `Reconnect` | `Process/The schedule cannot be interrupted while it is in progress.` / `Use pairing key` / `Please pair first` | `POST /api/disconnect {}` / `POST /api/connect {}` |
| `read status` | `HSPY/Nano/parameter` | `POST /api/rpi {action:'status'}` — message `status updated`. [cross-check] On the server this call returns the RPi reply but does not store it (`controller.py:679-739`); what the page shows afterwards still comes from the server's own 5 s poll. It is also refused while a flow is active. |
| `synchronous Nano parameter` | `write parameter.json` | dialog → `POST /api/rpi {action:'prepare'}` |
| `capture raw spectrum` | `spectrometer single acquisition The driver of` | dialog → `POST /api/rpi {action:'spectrum'}` |
| `HSPY OFF＋Nano STOP` | `Stop output` / `Stop plasma (open another connection to send cancellation)` | `POST /api/rpi {action:'stop'}` — **no confirmation dialog** |
| `Clear Nano fault` | `at present {fault}` / `Need first HSPY OFF` / `No fault at present` | dialog → `POST /api/rpi {action:'clear_fault'}` |
| `renew RPi tool` | `n files need to be updated` / `is the latest` | dialog → `POST /api/rpi-tools/deploy {accept_unknown}` |
| `⚠ manual start plasma` / `⚠ Plasma execution...` (full width, red frame, yellow-black hazard stripes) | `will output high voltage · Current setting N V` or the blocking reason | dialog (section 4.3) → `POST /api/rpi {action:'plasma_run', confirm_high_voltage:true}` |

Blocking reasons for manual plasma (`index.html:2268-2269`): `Plasma is executing`, `RPi not connected`, `process is being executed`, `Scheduling in progress`, `The pump is running, please stop it first.`, or the server's `s.plasma_ready_problem`.

#### Tab `plasma parameters` (`plasma`) — see section 9

#### Tab `correction coefficient` (`cal`) — `index.html:2361-2381`

Title `Cu/Zn/Ni correction coefficient`, sub `y = (a × x + b) × c; is currently not verified as a standard and does not display the official concentration.`. A 3×3 grid of buttons (rows Cu/Zn/Ni, columns a/b/c); tap → number pad (−1e6…1e6, 6 decimals, `±` key). Buttons `Give up modification`, `Storage` → dialog `Store correction coefficients` → `POST /api/calibration-config {Cu:{a,b,c}, Zn:{…}, Ni:{…}}`. Loaded with `GET /api/calibration-config`.

#### Tab `spectrum` (`spectra`) — `index.html:894-963`, logic 2384–2438

- Left `spectral group`: list (5 per page) from `GET /api/spectra` (admin session required): time, recipe name, `RPi Import` / `manual plasma` / `Process`, `No. r/R wheel`, `signal N open`; warning `The number of signals does not match the number of rounds` / `No leaflet information`. Buttons `Refresh`, `from RPi Import…`.
- Right: view switch `Spectrum` / `Single sheet inspection ⚠`; info line `background N Zhang · Signal N Zhang · Points N ms · {folder}`.
  - Spectrum: mode `average` / `leaflet` / `All overlays`, frame navigator `◀ No. i / N open ▶`, range `full spectrum` / `Cu 327 nm` / `Ni 232 nm`. SVG 566×282, reference lines `Cu 327.28`, `Ni 231.66`. All curves are background-subtracted.
  - Single sheet inspection: table `Zhang Ci | peak | Peak wavelength | total intensity | difference from median | remind (difference > 20% or below background)`, 10 rows per page; tap a row to see that frame.
- Import panel `from RPi Import spectrum`: `GET /api/rpi-jobs` → table `folder time | folder | signal | state | Import`; `POST /api/spectra/import {remote_dir}`.
- One set: `GET /api/spectra/{id}` → `{id, axis[], mean_difference[], frame_differences[][], background_frames, signal_frames, integration_ms, remote_dir, captured_at, source, recipe_name, round, rounds}`.

#### Tab `operator` (`operators`) — `index.html:859-870`, logic 2441–2448

Panel `Operator list`, sub `The operator needs to select a name before starting the schedule. Each measurement and operation event will be recorded who performed it.`. Table `# | Name | delete`, 8 per page. Button `New operator` → dialog `Add new operator (job number or English name) What is` (on-screen keyboard, max 20). Both add and delete send the whole list: `POST /api/operators {operators:[…]}`. Footer note: `On-screen keyboard can input job number/English; please use the physical keyboard to enter or edit the Chinese name data\operators.json.`

### 3.9 test page (`/test/`, hidden by default)

Title `HMI Temporary test page`. Its own mini-shell: header (`online metal wastewater detector / HEAVY METAL ANALYZER · HMI v1`, connection badge, clock), banner, left nav, footer (`PLC … / RPi …`, `Spectrum acquisition: on standby`, button `Read status immediately` → `POST /api/refresh`).

| Nav | Page | Content |
|---|---|---|
| `01 Device status` | `device connection test` | cards `power output status`, `Power supply readback voltage` (V), `power supply readback current` (mA), `Nano pulse status`; panel `Communications and Equipment` (`RPi SSH`, `HSPY / RS485 · 1`, `Nano / RS485 · 2`, `Nano firmware version`, `PLC / Modbus TCP`, `spectrometer`); panel `Accepted device parameters` (read-only); button `capture spectrum →` |
| `02 pump monitoring` | `Syringe pump and peristaltic pump` | read-only PLC view: `communication status`, `Peristaltic pump command` (count ON of P1–P5), `Syringe pump setting amount` (40007), `Actual status of syringe pump` (always `unknown`); lists `peristaltic pump command register 40008–40012` and `Syringe pump trigger register 40001–40006` (`reset`, `channel 1–3`, `Liquid extraction`, `Liquid injection`); button `Reread PLC` → `POST /api/plc-refresh` |
| `03 spectrum` | `Raw spectrum test` | button `capture 3 Zhang Pupu` → `POST /api/capture`; frame buttons `No. 1/2/3 open`; canvas chart `strength / Wavelength`; `Download this CSV` → `GET /api/captures/{id}/csv/{n}` |
| `04 results` | `Retrieve results` | `GET /api/history` table |
| `05 event` | `Connection and retrieval events` | `s.events` |
| `06 connection settings` | forms `PLC · Modbus TCP (read only)` (`host`, `port`, `unit_id`, `poll_seconds`) → `POST /api/plc-config`; `RPi connection` (`host`, `port`, `username`, `password`, `key_file`, `known_hosts`, `app_dir`, `tools_dir`, `serial_port`, `output_dir`, `poll_seconds`) → `POST /api/connect {config, password}`; `Disconnect` |

It polls `/api/state` every 1000 ms and marks data unknown after 5 s without an answer (`test/app.js:120-123`). The section with id `manual` in the HTML has no nav button.

### 3.10 Legacy V1 pages (for the "original logic" only)

- `/workflow/` (`web/workflow/index.html`): one long page titled `Administrator settings`, sections `Administrator` (password field + `log in` / `Sign out`; posts `{password}`), `CSV Recipe` (textarea editor, `verify CSV`, `Administrator released`, recipe select, `Loading content`, `Download template`), `process execution` (checkbox `The high voltage load is indeed disconnected (isolation test)`, `execution has been released Recipe` → `POST /api/recipes/run {id, isolated}`, `Stop process`), `RPi/Plasma Project Settings · Administrator` (RPi SSH password field, `connection RPi`, `read status`, `synchronous Nano`, `capture raw spectrum`, `HSPY OFF＋Nano STOP`, 7 plain `<input type=number>` parameter fields — voltage max there is still 100), `Cu/Zn/Ni Calibration parameters · Administrator`. Polls `/api/workflow-state` every 1500 ms.
- `admin-page.html` (V1 design fragment): tab row `adminTabs`, generic 5-column table of parameter rows (every editable cell `disabled` with title `has not yet been connected to the device/Source`), a **users table** (name, role, level buttons, state, delete; `New account ADD USER`, `restore REVERT`, `Storage SAVE`) — all disabled. `shell-extension.js:218-229` names two of the V1 tabs: `detection method`, `plasma parameters`. README line 60: "Export of data not yet provided/User management/Alarm threshold table has been removed from the screen".
- `manual-page.html` (V1 design fragment): panel `peristaltic pump PUMPS · 40008–40012`, panel **`solenoid valve VALVES · 40016 / 40017 / 40033`** (3 valves, toggle button each, disabled), panel `syringe pump SYRINGE · 40001–40007` with `Liquid extraction ASPIRATE` / `Liquid injection DISPENSE` / `RESET`, and `Mixing Tank Agitator 40028`. This is the only place the original design shows valve controls.

---

## 4. Dialog system

One generic modal, `this.state.dlg`, opened with `openDialog({...})` (`index.html:1664-1685`). Parts, all optional: key/value `lines`, a choice grid (`choices`, 8 per page), a number pad (`pad`), an on-screen keyboard (`text`), a `detail` block, a yellow `warn` block, and one or more **acknowledge rows** (`ack`/`acks`: big checkbox rows that must all be ticked before the confirm button enables). Footer: `Cancel` + confirm button with a specific label. Clicking the grey overlay cancels.

- **Auto-cancel**: every dialog closes after 30 s (`DIALOG_MS`) with the message `The confirmation window timed out and was canceled without sending the command.`; the countdown restarts on every key press. Overrides: 90 s for Start scheduling, 120 s for messages and RPi pairing. The dialog prints `N seconds, it will be automatically canceled and no command will be sent.`
- `danger` → orange border/header; `hv` → red 3 px border, red header, red confirm button, 580 px wide.
- Number pad: keys `7 8 9 4 5 6 1 2 3 [.|C] 0 ⌫` (+ `C`, `±` when decimals / negatives are allowed); shows `range min–max,decimal n Bit` / `,integer`; error `is out of range or the format does not match`. PIN variant shows `●` and `Please enter 6–8 digital repair PIN`.
- Keyboard rows (`KB_ROWS`, `index.html:1191-1193`): digits, `qwertyuiop`, `asdfghjkl-`, `⇧ zxcvbnm _ .`, `@ # ! Blank ⌫ Clear`. Masked entry has a `show` / `hide` toggle.
- `showMessage(title, text)` shows long text (truncated at 700 chars) — used for alarms, `message`, `The schedule cannot be started at this time`, `Maintenance operation message`.

### 4.1 Complete list of dialogs in the shell

Operator side: `Select operator`; `No operator list yet`; `Select Recipe`; `Start method`; `Start time: hour` → `Start time: minutes`; `Measurement interval`; `end condition`; `Measurement times`; `end time: hour/point`; `Blank Recipe`; `Make a blank every few samples`; `Start scheduling` / `⚠ Start schedule: Each measurement will automatically output high voltage`; `Stop scheduling`; `Continue scheduling`; `Continue the process`; `Change pump common direction`; `settings {direction} speed`; `P{n} Automatic stop seconds`; `start P{n} {Name}`; `injection volume`; `syringe pump for pumping liquid` / `syringe pump injection` / `Syringe pump reset`; `alarm · {level}`; `message`; `The schedule cannot be started at this time`.

Maintenance side: `Recipe Name`; `release Recipe`; `delete Recipe` → `input repair PIN to delete`; `pair RPi: Verify host fingerprint` → `RPi account password (only use this once)`; `synchronous Nano parameter`; `capture raw spectrum`; `Clear Nano fault`; `renew RPi tool`; `⚠ Manually start plasma: high voltage will be output`; one pad per plasma parameter; `Store plasma parameters`; `Number of sheets added in each group` → `Stored spectrum group addition`; `{Metal} coefficient {a|b|c}`; `Store correction coefficients`; `Add new operator (job number or English name) What is`; `Delete operator`; `Maintenance operation message`.

### 4.2 Start scheduling dialog (`index.html:1734-1757`)

Lines: `Recipe`, `operator`, `first measurement`, `interval` (`Every N minutes`), `End`, `Estimated single time` (`about …`), `blank solution`, and with plasma `HSPY Set voltage` (`N V (upper limit 700 V) `).
Ack rows — always: `The liquid lines, reagents and waste are in place, and the physical stop method is available.`. With plasma, two more first: `I have confirmed that each measurement will be automatically output N V high pressure` and `The electrodes, water lines and high-voltage lines have been connected; personnel have stayed away and the upper cover has been closed`.
Warn text with plasma: `Each measurement during scheduling will be automatically output. N V High voltage, no longer confirmed one after another. Please confirm the upper cover when no one is on duty/Door and emergency stop are available. In an emergency, please press the physical emergency stop button.` Without: `schedule will automatically start the pump and syringe pump according to time.STOP is a software stop, not a safety level emergency stop.`
Confirm `Start scheduling` → `POST /api/schedule/start {recipe_id, operator, schedule, blank, confirm_hv}`.

### 4.3 manual start plasma dialog (`index.html:1496-1507`)

Title `⚠ Manually start plasma: high voltage will be output`. Lines `HSPY Set voltage`, `Pulse ON/OFF`, `every round pulse times`, `Number of rounds`, `integration time`, `Spectral interval`, `Estimated pulse time`, `HSPY at present`. Ack `I have confirmed that it will be output soon N V high pressure,People have stayed away`. Confirm `Start plasma (output high voltage)`. While running the message chip reads `Plasma is executing... High voltage output is in progress. Please press ”HSPY OFF＋Nano STOP”`; on success `The plasma measurement is completed and the spectrum has been saved in the ”Spectrum” tab.`.

---

## 5. The operator's path, opening the UI → finished measurement (item c)

One-time set-up by maintenance (README lines 72–80):

1. Run `Install.cmd`, then `Set-Admin-Password.cmd` (6–8 digit PIN), then `Start.cmd` / `Start-Kiosk.cmd`. Browser opens `http://127.0.0.1:8766/` on **Overview**.
2. Long-press the top-left name 3 s (or tap `🔧 Maintenance`) → PIN → **RPi connection** → `pair RPi (first) `.
3. **operator** tab → add names.
4. **Recipe** tab → `Import CSV files` → `release` → `Set as schedule Recipe`. A Recipe describes **one round**; repetition is the schedule's job (README line 16).
5. **plasma parameters** tab → set and `save to RPi`.
6. Optional: **Correction** page → run standards → `application coefficient`.

Daily operator path (no login):

1. Top bar `operator` → pick a name (`POST /api/operator {name}`). Starting a schedule without one opens the picker first (`index.html:1735-1736`).
2. `Schedule measurement` → tiles: Recipe, start, Measurement interval, end condition, blank solution. The server returns a preview (first slot, count, last slot, estimated run time, warnings).
3. Footer `Start scheduling` → confirmation with tick boxes (4.2) → schedule runs. **There is no single "START one measurement" button**; one measurement = a schedule with `specified times` 1. ("single" appears in the run log only for flows not started by a schedule.)
4. Watch **Overview** (phase row, thick path on the flow diagram, metal cards) and the footer (`Schedule No. n / N Second-rate`, `Next measurement HH:MM (reciprocal) `, `stage`, `Not measured`, `⚡ contains high voltage`).
5. `pause PAUSE` / `continue RESUME` / `Stop STOP` in the footer.
6. Results: metal cards on Overview, **execution record** (one row per measurement and per missed slot), **Trend** (charts), **alarm**.

---

## 6. What sits behind the admin password (item d)

### 6.1 The password itself

- It is a **maintenance PIN, 6–8 digits** (`auth.py:19`); a legacy password of 12–256 characters is still accepted (`auth.py:22-30`).
- **It cannot be set or changed from the UI.** It is set on the PC console with `Set-Admin-Password.cmd` → `server.py --set-admin-password` → two hidden `getpass` prompts (`server.py:438-447`). Stored as PBKDF2-HMAC-SHA256, 600 000 iterations, 32-byte salt, in `data/admin.json` with a `kind` of `pin` or `password` (`auth.py:59-70`). The UI only shows the "not configured" hint.
- Session: random token, **600 s idle timeout** (`auth.py:34, 115-129`). It is renewed by every POST that carries the `X-Admin-Session` header (`server.py:267` — the shell's `post()` always sends it, so operator-level POSTs renew it too) and by the admin-only GETs (`server.py:185, 192`). The 10-second `/api/auth` poll and the 1.5-second `/api/state` poll do not renew it (`server.py:130`, `renew=False`). Sessions live in server memory only and are cleared when the PIN file changes or the server restarts.
  - **[cross-check correction] The "idle" timeout is defeated by the shell's own timers on two pages**: with a Recipe selected, the Schedule measurement page posts `/api/schedule/preview` every 30 s (`index.html:1315`), and Maintenance settings › spectrum re-reads `GET /api/spectra` every 10 s (`index.html:1317`). Both renew the session, so a logged-in panel left on either page stays logged in indefinitely. On the other pages the session does end after 600 s without a POST.
- Lockout: 5 wrong entries → 30 s lock, doubling each further lockout, max 900 s (`auth.py:35-37, 102-110`). Messages `Maintenance PIN mistake`, `Too many attempts, please N Try again in seconds`.
- One role only. **Operators have no password** — an operator is a name picked from a list.
- There is exactly one extra PIN prompt inside a session: deleting a Recipe (`server.py:376`).

### 6.2 Gated in the UI

Whole admin page (six tabs): Recipe import/publish/delete, RPi pairing/connect/disconnect and RPi actions, plasma parameters, Spectral group addition, calibration coefficients, raw spectra and RPi import, operator list.
Outside the admin page: `application coefficient` on the Correction page, and `blank threshold/zero point` → `Storage` on the Trend page.

### 6.3 Gated by the server (`server.py:278-285`)

`admin_only = {/api/plc-config, /api/connect, /api/disconnect, /api/refresh, /api/calibration-config, /api/parameters, /api/recipes/publish, /api/recipes/run, /api/recipes/delete, /api/start, /api/capture, /api/spectra/import, /api/rpi-tools/deploy, /api/rpi/hostkey, /api/rpi/pair, /api/operators, /api/calibration/apply, /api/blank-qc, /api/frame-sum, /api/blank-zero}` plus `/api/rpi` for every action except `stop` and `status`. GET `/api/spectra`, `/api/spectra/{id}`, `/api/rpi-jobs`, `/api/rpi-tools` also need the session (`server.py:184-193`). Error text: `This operation is only for maintenance personnel (maintenance PIN) `.

### 6.4 Open to the operator (no login)

Schedule preview/start/pause/resume/stop, `workflow-pause/resume/stop`, manual pumps and syringe (`/api/plc-manual`), operator selection, `HSPY OFF＋Nano STOP` and status (`/api/rpi` `stop`/`status`), viewing every non-admin page, **starting a calibration run** (`/api/calibration/start`, `/confirm`, `/analyze`, `/exclude`, `/plan`, `/check`), `POST /api/validate`. (Server comment, `server.py:276-277`: "Operators need no sign-in… Everything that changes how the instrument measures belongs to maintenance (PIN).")

---

## 7. Status indicators, alarms, interlocks, stop buttons (item e)

### 7.1 One visual rule for all states (`index.html:19-28`, README line 43)

Colour is only for "abnormal" or "running"; normal stays grey.

| State | Class | Look |
|---|---|---|
| running / command ON | `.q-run` | solid blue #2f5f8f |
| normal value / command OFF | `.q-ok` | dark grey text |
| communication failed (value untrustworthy) | `.q-bad` | magenta #a3168e, `?` prefix, dashed frame |
| not installed / no source | `.q-na` | light grey dashed frame |
| not calibrated | `.q-uncal` | diagonal hatch |
| calibrated | `.q-cal` | green #3d7a52 outline |
| alarm priority only | `.p1` Emergency red #c62828, `.p2` high orange #e06a00, `.p3` middle yellow #e0b400 | |

Helper `Q.ok/run/bad/na` (`index.html:1231-1232`). Rule (`index.html:1828-1829`): BAD when data should exist but cannot be trusted; N/A when no source exists (demo mode → `simulation mode`).

**Staleness**: snapshot older than 5 s (`STALE_MS`) → everything becomes `? unknown` and alarm `hmi_comm` fires.

### 7.2 Alarms are computed in the browser (`updateAlarms`, `index.html:1584-1636`)

Run every second and after each poll. Acknowledgement is stored in `localStorage['hmi_acked_alarms']` (per browser). An alarm that clears while unacknowledged stays listed as `Restored` until acknowledged.

| id | Pri | area | Condition | Message |
|---|---|---|---|---|
| `hmi_comm` | 1 | HMI | no fresh `/api/state` for 5 s | `HMI No response from the server: The screen data has expired and the actual status of the device is unknown.` |
| `plc_comm` | 2 | PLC | `!plc.valid` (not demo) | `PLC Communication interruption: pump status cannot be confirmed ({error}) ` |
| `stop_err:{flow}` | 1 | pump | `flow.stop_errors` not empty | `The pump stop command is not confirmed, please use the physical stop method immediately` |
| `flow_failed:{flow}` | 2 | Process | `flow.status === 'failed'` | `process failed:{error}` |
| `plc_cmd:{time}` | 3 | PLC | `plc.last_command.ok === false` | `PLC command failed ({action}) : {error}` |
| `rpi_comm` | 2 if schedule active else 3 | RPi | not demo, RPi needed (active schedule's `uses_rpi`, else running flow's `uses_rpi`, else the selected Recipe's `uses_rpi`) and `!s.connected` | `RPi Not connected: Schedule content measurement steps,HMI is automatically reconnecting` / `RPi Not connected: selected Recipe content measurement step cannot be executed.` |
| `blank_qc:{id}` | 3 | Blank | latest blank `qc.status === 'fail'` | `Blank solution exceeds baseline threshold:…` |
| `sched_int:{id}` | 2 | Schedule | schedule `interrupted` | `The schedule has been interrupted and will not be automatically restored.` or server text |
| `sched_fail:{id}` | 2 | Schedule | schedule `failed` | `Schedule has stopped:{error}` |
| `sched_skip:{id}` | 3 | Schedule | `counts.skipped_overrun > 0` | `Already N periods were not measured due to the previous measurement timeout.` |
| `sched_ns:{id}:{i}` | 3 | Schedule | a slot with status `not_started` | `No. N times failed to start:{note}` |

Display: banner (worst first: unacknowledged, then priority, then newest), blinking priority tag while unacknowledged, `Also N item`, buttons `confirm ACK` and `Alarm list`; nav badge; markers on the flow diagram; `active alert` cell; the alarm page. **There is no HSPY/Nano fault alarm in this list** — a Nano fault appears only in the `Nano controller` cell and through the manual interlock.

### 7.3 Interlock display

- `s.manual_interlock = {ok, reasons[], fixes[], warn}` is evaluated by the server. When not ok: the manual page's notice turns orange `Manual start and deactivation: High voltage safety conditions are not met (…)`, with `Processing method:….OFF Still available.` and (only for a logged-in maintainer) a button `Go to RPi connection`. All start buttons are disabled; OFF stays enabled.
- README line 48 states the conditions: with the RPi connected, pump ON / syringe / direction / speed need RPi status ≤ 20 s old, HSPY OFF, Nano IDLE or DONE without fault, D13 and output command LOW.
- The RPi tab row `manual pump interlock` shows the same result.
- `manualBlock` reasons in order (`index.html:1956-1959`): `Simulation mode: no control PLC`; `HMI The server does not respond, manual operation is disabled.`; `PLC Communication interruption: startup operations are disabled;OFF You can still try to send it`; `Scheduling in progress: manual start and deactivation; if manual operation is required, please pause the schedule first.`; `The process is paused: the pump is still retained by the process, please click ”Continue” or ”Stop” at the bottom`; `The process is being executed: the pump is controlled by the process and can be started and deactivated manually.`; the HV interlock; `PLC command is being executed, please wait.`; `not connected yet HMI server`.

### 7.4 Stop controls

| Control | Where | Effect | Confirm? | Login? |
|---|---|---|---|---|
| `Stop STOP` | footer, every page | schedule: `POST /api/schedule/stop`; single flow: `POST /api/workflow-stop` | dialog only for a schedule | no |
| `pause PAUSE` | footer | `POST /api/schedule/pause` or `/api/workflow-pause` | no | no |
| Pump `OFF` | manual page rows, pump faceplate | `POST /api/plc-manual {action:'pump_off', pump}` | no | no |
| `HSPY OFF＋Nano STOP` | admin › RPi connection only | `POST /api/rpi {action:'stop'}` | no | the server allows it without a session, but **the button is only on the admin page** |
| `Stop calibration` | Correction › execution | `POST /api/workflow-stop` | yes | no |
| Physical E-stop | footer reminder box | none — text only | — | — |

The UI states repeatedly that STOP is a software stop, not an emergency stop; `HOLD` and an on-screen `E-STOP` from the V1 design were removed (README lines 28, 45).

---

## 8. Pump and PLC state: display and manual operation (item f)

### 8.1 Where pump/PLC state is shown

- Top bar `PLC` chip.
- Overview: flow diagram (P1–P5 symbols, value boxes, thick active path), status strip (`PLC Communication`, `pump direction/speed`, `Syringe pump setting amount`), faceplates.
- manual operation page.
- Hidden test page › `02 pump monitoring` (read-only registers).
- Everything shown is the PLC **command register readback**: `command ON` (`raw === 1`), `command OFF` (`raw === 0`), `? original value N` otherwise. The UI says so: `status is PLC Command register readback does not represent actual traffic.` The syringe pump has no position/done feedback (`No position readback`).

### 8.2 manual operation page layout (`index.html:639-693`)

Top: a notice bar (yellow / grey / blue / orange) — default text `P1–P6 After startup HMI at the respective set seconds (1–120 Second) Send stop command; HMI or communication interruption is not guaranteed to stop, please prepare a physical stop method.`

Left panel **`peristaltic pump P1–P6`** (sub `40008–40013`):

- Header controls, shared by all six pumps: `direction` segmented `CW` / `CCW` → dialog `Change pump common direction` → `{action:'pump_direction', direction}`; button `speed N rpm` → pad 1–1000 → `{action:'pump_speed', direction, rpm}`. Both disabled while any pump is ON.
- Six rows (`PUMPS`, `index.html:1044-1048`):

| No | Name | Register |
|---|---|---|
| P1 | sampling circulation pump | 40008 |
| P2 | Sample sent to mixing tank | 40009 |
| P3 | pure water pump | 40010 |
| P4 | Send detector | 40011 |
| P5 | waste pump | 40012 |
| P6 | standard pump | 40013 |

  Each row: `P{n} {Name}` + `scratchpad {addr}`; state tag; **auto-stop button** (`30 Second / automatically stop after`, while running `left N Second / Stopping automatically`) → pad 1–120 s, saved per pump in `localStorage['hmi_pump_seconds']` (default 30, `PUMP_DEFAULT_SECONDS`, max `PUMP_MAX_SECONDS = 120`); `ON` → dialog `start P{n} {Name}` (lines `equipment`, `direction/speed`, `automatic stop`; warn `HMI, The pump will not stop automatically when the computer or communication is interrupted. Please confirm that the fluid circuit is correct and the physical stop method is available before starting.`) → `{action:'pump_on', pump, seconds}`; `OFF` → `{action:'pump_off', pump}` immediately.
  Only one pump is started per command; there is no multi-select in the live page.

Right column:

- Panel **`syringe pump`** (sub `40001–40007`, tag `No position readback`): `channel` segmented `CH1 Drainage` / `CH2 Nitric acid` / `CH3 Air`; `volume` with `−` / value button / `+` (step 0.1, pad 0.01–5.00 mL, 2 decimals); buttons `Liquid extraction` → `{action:'aspirate', channel, volume_ml}`, `Liquid injection` → `{action:'dispense', channel, volume_ml}`, `reset` → `{action:'syringe_reset'}` (each through a dialog; warn `Syringe pump has no position/Readback completed, action results must be confirmed on site.`). Footer text `PLC Set amount: {x.xx mL}.40042–40044 Location/Completion readback has not been provided, and whether the action is completed must be confirmed on site.`
- Panel **`No device installed`**: `V1 Wastewater inlet valve,V2 pure water valve,V3 The waste valve and mixing tank agitator have not been installed yet.HMI does not provide operations. Install and complete PLC Read back before adding.`

All manual commands go to one endpoint: `POST /api/plc-manual` with body `{action, …}` (`manualCommand`, `index.html:1724-1732`). Messages: `PLC Command is being sent...` → `Command accepted, waiting PLC Read back; please confirm on-site for actual operation` or `Operation failed:…`. The older alias `/api/plc` also exists (`server.py:286`).

There is **no manual control of valves, stirrer, weight or level** anywhere in the live UI, and **no manual plasma on the operator's manual page** (it is on admin › RPi connection).

---

## 9. How plasma parameters are edited (item g)

Admin tab `plasma parameters` (`index.html:873-879`, logic 2319–2358). Sub-title `Click to modify the value; when saving HSPY Must OFF, zero output,Nano is idle and will not start high voltage`.

A 4-column grid of large tiles (112 px high): label with unit, big value, small line `range min–max` (or `RPi at present N` once changed; changed tiles get a blue frame). `PARAMS` (`index.html:1182-1190`):

| API key (sent) | Readback field (`s.snapshot.parameter`) | Label | Unit | Min | Max | Decimals |
|---|---|---|---|---|---|---|
| `Ontime` | `on_time_ms` | Pulse ON | ms | 1 | 1000 | 1 |
| `Offtime` | `off_time_ms` | Pulse OFF | ms | 1 | 1000 | 1 |
| `Cycle` | `cycles` | every round pulse times | Second-rate | 1 | 10000 | 0 |
| `Loopnum` | `loops` | Number of rounds | wheel | 1 | 1000 | 0 |
| `Integration_Time` | `integration_ms` | integration time | ms | 1 | 10000 | 0 |
| `Spectra_Interval` | `spectra_interval_ms` | Spectral interval | ms | 1 | 60000 | 0 |
| `Voltage` | `voltage_v` | HSPY Set voltage | V | 1 | 700 (`MAX_VOLTAGE`) | 0 |

- Tap a tile → number pad → the value is held locally (`this._paramEdit`).
- Footer: note `After modification, click ”Save to RPi”will be written` (or `RPi parameters have not been read yet: please firstRPi Connect”Connect and read the status`); buttons `Give up modification`, `save to RPi` → dialog `Store plasma parameters` listing `old → new unit` for each changed item, warn `, each measurement will be N V discharge (upper limit 700 V) .Must HSPY OFF, zero output,Nano is idle and no process is executed; this operation does not start high voltage.` → `POST /api/parameters` with **all seven keys**.
- An eighth tile, dashed frame = "HMI analysis setting, saved on its own": **`Spectral grouping addition (photo)`** (`fsum`), range 1–50, two-step dialog `Number of sheets added in each group` → `Stored spectrum group addition` → `POST /api/frame-sum {frames_per_sum}`. Shows `Number of rounds N → M Group` or `⚠ Number of rounds N cannot be k Divisible`.
- After saving, the parameters still have to reach the Nano: admin › RPi connection › `synchronous Nano parameter` (`prepare`), or the Recipe step `nano_prepare`.
- **There is no background-frame count parameter in this UI.** Background and signal frame counts are only displayed (`background N Zhang · Signal N open`) on the spectrum tab.

---

## 10. The schedule UI (item h)

Page `Schedule measurement` (`index.html:701-724`, logic 2451–2510). The form is kept in the browser: `localStorage['hmi_sched_form']`, default `DEFAULT_FORM = {recipe_id:'', start:{mode:'now', time:'08:00'}, interval_minutes:10, end:{mode:'count', count:6, time:'17:00'}, blank:{enabled:false, recipe_id:'', every:4}}` (`index.html:1063-1064`).

Left panel `Schedule setting` (392 px). Sub-title `Click each item to modify; the settings will be recorded in this panel`, or while active `Scheduling is in progress: modification can only be made after stopping` (tiles disabled, showing the running schedule's values). A 2-column grid of tiles (80 px high; an unset required tile has an amber dashed frame):

| Tile | Value text | Sub-text | Editing |
|---|---|---|---|
| `Recipe` | name or `Please select` | `N step · with plasma` | choice dialog `Select Recipe` |
| `operator` | name or `Please select` | `Each measurement will be recorded` | `Select operator` |
| `start` | `Start now` / `HH:MM start` | `If this time has passed, it will be tomorrow` | `Start method` → `Start now` / `specified time` → hour pad 0–23 → minute pad 0–59 |
| `Measurement interval` | `Every N minutes` / `Every N Hour` | `Calculated based on each start time` | pad 1–1440 minutes |
| `end condition` | `common N Second-rate` / `arrive HH:MM until` / `Continue until stop is pressed` | `according to STOP Just ended` | `specified times` (pad 1–1000) / `to the specified time` / `Continue until stop is pressed` |
| `blank solution` | `Every N samples` / `Not inserted` | `{Recipe} · Enter blank first time` / `Periodic confirmation of baseline (e.g. P1 hit blank) ` | `Blank Recipe` choice (`Do not insert blanks` + other recipes) → pad 1–100 `samples` |
| `measurement content` (read-only) | `⚡ contains high voltage plasma` / `includes spectrum measurement` / `Only liquid line` | `N V · Confirm once at the beginning, then automatically discharge` | — (red frame + hazard stripe when HV) |

Below: estimate lines — before start `Estimated single time`, `first measurement`, `Estimated times` (`N Second-rate` / `Continuous`), `last time` (or `according to STOP until`); while active `state`, `first measurement`, `Estimated times`, `last time`. Then an error box (preview error or `sched.error`) and warning boxes (`pv.warnings`).

Right panel: title `This schedule` / `Last scheduled` / `Scheduling record`, sub `{Recipe} · {operator} · {establishment time}`, right `Complete n · Skip n · failed/not started n`. Table `sequence | scheduled time | state | start | End | Remarks`, newest first, 9 per page; blank slots are prefixed `［Blank］`. Empty text `Scheduling has not started yet …`.

Requests:

- Preview: `POST /api/schedule/preview {recipe_id, schedule, blank}` on every form change, and every 30 s while the page is open ("start now" moves with the clock). Response fields used: `spec.first_slot`, `planned_total`, `last_slot_at`, `estimate_seconds`, `uses_plasma`, `uses_rpi`, `warnings[]`, `blank{recipe_name, every}`.
- `schedule` body (`scheduleRequest`, `index.html:1167-1171`): `{start:{mode:'now'} | {mode:'at', time:'HH:MM'}, interval_minutes, end:{mode:'count', count} | {mode:'until', time} | {mode:'continuous'}}`.
- `blank` body: `{recipe_id, every}` or `null`.
- Start / pause / resume / stop: section 2.3.

Status vocabularies: schedule `SCHED_ZH` = `waiting Wait for the next period`, `running under measurement`, `paused Suspended`, `stopping Stopping`, `completed Completed`, `stopped Stopped`, `failed has stopped (error)`, `interrupted has been interrupted (not automatically restored)`; active set `['waiting','running','paused','stopping']`. Slot `SLOT_ZH` = `running under measurement`, `done Complete`, `failed failed`, `stopped Stopped`, `not_started not started`, `skipped_overrun Skip (last timeout)`, `skipped_paused skip (scheduling pause)`. Flow `STATUS_ZH` = `idle standby`, `running is executing`, `pausing Paused`, `paused Suspended`, `stopping Stopping`, `completed Complete`, `failed failed`, `stopped Stopped`.

Behaviour rules stated in README lines 16–21: overrun slots are skipped, not made up; a failed measurement stops the schedule; three consecutive "not started" stop it; after a power cut or restart the schedule is `interrupted` and never resumes by itself; while a schedule runs, manual start, plasma parameters, manual plasma, RPi tool update, disconnect and pairing are refused.

---

## 11. Results and trends (item i)

### 11.1 execution record (`index.html:727-741`, logic 2056–2078)

Title `execution record`, sub `Measured and unmeasured periods (recent 200 pen)`. Data `GET /api/run-log` → `{rows}`; refreshed when the page opens and every 10 s while open; a running flow not yet in the log is put on top.
Columns `time | operator | Recipe | sequence | End | state | concentration | Remarks`, 9 per page.
- `sequence`: `n/N` or `single`.
- `concentration` cell: sample → `Cu x · Zn y · Ni z` (green tag) or `Uncorrected` / `Analysis failed`; blank → `Blank ✓ baseline is normal` / `Blank ⚠ Beyond baseline threshold` / `Blank (not compared)`; missed slot → `Not measured`.
- No export, no detail view, no spectrum link from this page.

### 11.2 Trend (`/trend/`)

Title `concentration trend`. Three stacked SVG charts, one per metal (`copper Cu`, `zinc Zn`, `nickel Ni`).

- Mode switch `concentration` / `Zero point not deducted` / `light intensity`.
- `Display range…` dialog: quick ranges `1 Hour / 8 Hour / 24 Hour / 7 sky / 30 sky / All`, or custom start/end (`datetime-local`, max one year), and per-metal concentration limits with the equivalent intensity range. Stored in this browser only. A chip shows the current window.
- Series: samples = blue line and dots; blanks = amber dashed line with diamonds; failed blank = magenta filled diamond; shaded band `blank threshold ±x`. Tap a point → detail line at the bottom.
- `blank threshold/zero point` dialog (saving needs the PIN): `Spectral shift upper limit (nm)`, `Cu/Zn/Ni Upper limit of apparent concentration (± mg/L)`, `Upper limit of normalized area change (± %)`, blank-zero subtraction per metal, `Get the last few blanks`, `The deduction will start after at least several blanks.`, `remove outlier blanks (3×MAD) ` → `POST /api/blank-qc {qc}` then `POST /api/blank-zero {settings}`.
- Data: `GET /api/measurements?hours=N` → `{rows[{time, kind:'sample'|'blank', metals{Cu:{conc, conc_raw, zero, intensity}}, qc, shift_nm, recipe_name}], qc, coefficients, blank_zero, calibration, unit}`. Reload every 10 s visible, every 60 s hidden.

### 11.3 Other result views

Metal cards on Overview (latest sample); raw spectra on admin › spectrum (PIN); regression results on Correction › results and records.

---

## 12. How the page talks to the server (item j)

### 12.1 Transport

- Same-origin `fetch`, JSON both ways, `cache: 'no-store'`. No WebSocket, no SSE.
- `GET /api/bootstrap` once → `{token, config, demo, plc_config, test_page_enabled, admin_configured, admin_kind}` (`server.py:124-128`).
- Every POST carries `X-HMI-Token: {token}` and `Content-Type: application/json`; admin requests add `X-Admin-Session`. The server rejects a POST with a wrong token or foreign `Origin` with 403 `Invalid local request` (`server.py:254-257`); the shell then re-reads the token once and retries (`index.html:1716-1720`) because the token changes on each server restart.
- Errors are `{error: "text"}` with 400 / 403 / 404 / 409 / 500; the UI shows `error` verbatim.
- Body limit 220 KB (`need JSON,maximum 220 KB`).

### 12.2 Timers in the shell (`index.html:1259-1261, 1297-1324`)

| Interval | Action |
|---|---|
| 1000 ms | clock, dialog timeout, message fade (non-error messages vanish after 12 s), alarm evaluation |
| 1500 ms | `GET /api/state` (4000 ms abort; skipped if the previous call is still running) |
| 10 000 ms | `GET /api/auth` → `{admin, configured}` |
| ≥10 s, page execution record open | `GET /api/run-log` |
| ≥30 s, page Schedule measurement open | `POST /api/schedule/preview` |
| ≥10 s, admin › spectrum open | `GET /api/spectra` |

Sub-pages: calibration 2 s / 10 s hidden; trend 10 s / 60 s hidden; test 1 s; legacy workflow 1.5 s.

### 12.3 Endpoints used by the UI

GET: `/api/bootstrap`, `/api/state`, `/api/auth`, `/api/recipes`, `/api/recipes/{id}`, `/api/run-log`, `/api/calibration-config`, `/api/calibration`, `/api/calibrations/{id}`, `/api/measurements?hours=`, `/api/spectra`, `/api/spectra/{id}`, `/api/rpi-jobs`, `/api/rpi-tools`, `/custom/branding.json`, `/custom/texts.json`; test page: `/api/history`, `/api/captures/{id}`, `/api/captures/{id}/csv/{1-3}`; legacy: `/api/workflow-state`.
Served but not called by any page read: `/api/events`, `/api/schedule`, `/api/operators` (GET), `/api/result/latest` (only `lite/app.js`).

POST (body) — shell: `/api/admin/login {pin}`, `/api/admin/logout {}`, `/api/operator {name}`, `/api/operators {operators}`, `/api/schedule/preview`, `/api/schedule/start`, `/api/schedule/pause {}`, `/api/schedule/resume {}`, `/api/schedule/stop {}`, `/api/workflow-pause {}`, `/api/workflow-resume {}`, `/api/workflow-stop {}`, `/api/plc-manual {action,…}`, `/api/validate {csv}`, `/api/recipes/publish {name,csv}`, `/api/recipes/delete {id,pin}`, `/api/rpi/hostkey {}`, `/api/rpi/pair {password,fingerprint}`, `/api/connect {}`, `/api/disconnect {}`, `/api/rpi {action}` with `status | prepare | spectrum | stop | clear_fault | plasma_run (+confirm_high_voltage:true)`, `/api/rpi-tools/deploy {accept_unknown}`, `/api/parameters {7 keys}`, `/api/frame-sum {frames_per_sum}`, `/api/calibration-config {Cu,Zn,Ni}`, `/api/spectra/import {remote_dir}`.
Calibration page: `/api/calibration/plan`, `/check`, `/start`, `/confirm`, `/analyze`, `/apply`, `/exclude`, `/api/workflow-stop`.
Trend page: `/api/blank-qc`, `/api/blank-zero`.
Test page: `/api/plc-config`, `/api/plc-refresh`, `/api/connect {config,password}`, `/api/disconnect`, `/api/refresh`, `/api/capture`.
Legacy only: `/api/recipes/run {id, isolated}`, `/api/start {csv, isolated}`, `/api/stop`, `/api/plc`.

`/api/plc-manual` actions sent by the shell: `pump_on {pump, seconds}`, `pump_off {pump}`, `pump_direction {direction:'CW'|'CCW'}`, `pump_speed {direction, rpm}`, `aspirate {channel, volume_ml}`, `dispense {channel, volume_ml}`, `syringe_reset`. (`lite/app.js` also used `pump_on_many` / `pump_off_many {pumps}`.)

---

## 13. Shape of the global state the shell consumes

Not a store: instance fields on the component plus `this.state`.

`this.state` (`index.html:1249`): `{page:'dash', face:null, adminTab:'recipe', alarmTab:'alarms', pages:{}, kbShift:false, pwShow:false, specView:'chart', specId:null, specMode:'mean', specFrame:0, specRange:'full', imp:false, clock:'', dlg:null, syringeCh:2, syringeMl:1, rev:0}` (+ `pinKb`, `clockDate` added later). `rev` is bumped by `refresh()` to force a render after instance fields change.

Instance fields: `_snapshot` (last `/api/state`), `_lastStatus`, `_token`, `_demo`, `_testEnabled`, `_rpiCfg`, `_admin`, `_adminConfigured`, `_form`, `_preview`, `_previewErr`, `_recipes`, `_recipe`, `_recipeView`, `_recipeDraft`, `_runLog`, `_alarms` (Map), `_acked` (Set), `_msg`/`_msgKind`, `_admMsg`, `_busy`, `_plasmaBusy`, `_paramEdit`, `_cal`/`_calEdit`, `_specSets`/`_specData`/`_specQc`/`_chart`, `_imp`, `_rpiTools`, `_brand`, `_pw`, `_pumpSecs`.

Fields of `/api/state` that the shell reads (`s`):

- `s.connected` (RPi link), `s.error`, `s.updated_at`
- `s.snapshot.power {valid, run_stop_setting}` (0 = OFF); `s.snapshot.nano {valid, state, fault, output_pin_sample, outputs_allowed}`; `s.snapshot.parameter {valid, on_time_ms, off_time_ms, cycles, loops, integration_ms, spectra_interval_ms, voltage_v}`
- `s.plc {valid, error, pending, age_seconds, config{host}, auto_off_seconds{"n": sec}, last_command{ok, action, pump, error, time}, snapshot{pumps{"1".."6": {raw}}, pump_control{valid, direction, rpm_cw, rpm_ccw}, syringe{volume_ml}}}`
- `s.flow {id, status, mode, recipe_name, phases[], phase, current_step, step_count, round, rounds, slot, error, stop_errors[], started_at, finished_at, uses_rpi, paused_pumps[], prompt, calibration_id}`
- `s.schedule {id, status, recipe_id, recipe_name, operator, uses_rpi, uses_plasma, error, counts{done, failed, not_started, skipped_overrun, skipped_paused}, slots[{index, kind, status, note, planned_at, started_at, finished_at}], next_slot_at, next_index, planned_total, last_slot_at, created_at, finished_at, spec{start, interval_minutes, end, first_slot}, blank{recipe_name, every}}`
- `s.operator`, `s.operators[]`, `s.events[{time, message}]`
- `s.manual_interlock {ok, reasons[], fixes[], warn}`
- `s.measurement {latest_sample{time, metals{Cu:{conc, zero}}, conc_reason}, latest_blank{id, time, qc{status, problems[]}}}`
- `s.calibration_active {applied_at, metals[]}`, `s.frames_per_sum`
- `s.plasma_running`, `s.plasma_ready_problem`, `s.rpi_link {paired, auto_connect}`

The test page reads an older shape as well: `s.quality`, `s.pending`, `s.can_capture`, `s.job`, `s.age_seconds`, `s.snapshot.nano.active_parameters {on_time_us, …}`.

## 14. Browser storage keys

`localStorage`: `hmi_sched_form`, `hmi_acked_alarms`, `hmi_pump_seconds`, `hmi_lang`, `hmi_trend_hours`, `hmi_trend_mode`, `hmi_trend_view`.
`sessionStorage`: `hmi_admin_session`; legacy `hmi_selected_recipe`, `hmi_isolated`.

---

## 15. Visual style

- Canvas 1024×768, page background `#d9dadc` outside, frame `--color-bg #f2f2f3`.
- Fonts (local TTF, `web/vendor/fonts.css`): body **Barlow** 400/500/700, headings and all numbers **Barlow Condensed** 400/600 (`--font-heading`); CJK fallback `"Microsoft JhengHei"`. Base 15 px. The sub-pages use `15px "Microsoft JhengHei", "Noto Sans TC", sans-serif` only.
- Shell tokens (`index.html:29-35`): `--run #2f5f8f`, `--run-bg #dce9f6`, `--bad #a3168e`, `--bad-bg #fbe8f7`, `--p1 #c62828`, `--p2 #e06a00`, `--p3 #e0b400`, `--ink #1d1f20`, `--ink-2 #45484b`, `--ink-3 #6b6e72`, `--line #b9bcc0`, `--panel #fbfbfb`. Sub-pages add `--bg #eef0f2`, `--ok #3d7a52`, `--warn #8a5b00`, `--warn-bg #fff4d6`, `--blank #c27a00`.
- Flat and square: 1 px grey borders, no rounded corners (only pump circles), no gradients except hatch patterns, shadows only on overlays. White header/footer, `#f7f7f7` nav, `#fbfbfb` panels.
- Panel = `.panel` with a header `.ph` (bold 18 px `h2`, 13 px grey sub-title).
- Buttons: white with grey border, 40–54 px high (touch); primary = solid blue; segmented controls `.seg`; selected = blue.
- High-voltage controls: red 3 px frame with yellow-black diagonal hazard stripes (`.gbtn.hv`, `.stile.hv`), red dialog header.
- Touch rules (README line 56): **no scroll bars anywhere**; every list is paged with `◀ Previous page` / `No. a / b Page` / `Next page ▶` (`PER_PAGE`, `index.html:1161`: `runs 9, alarms 8, events 12, recipes 6, csv 7, specs 5, qc 10, jobs 8, slots 9, ops 8, choices 8`); numbers through the number pad, text through the on-screen keyboard; no browser `alert/confirm/prompt`.
- Flow diagram: grey lines, ISA pump symbols, tag numbers, no animation.

## 16. Languages (`web/i18n.js`)

- Three: `zh-TW` (source text), `zh-CN`, `en` (`LANGS`, line 14). Stored in `localStorage['hmi_lang']`; `?lang=` forces one.
- The pages are written in Traditional Chinese. `i18n.js` translates text nodes and `title` / `aria-label` at display time through a `MutationObserver`. English: whole-string table `EN`, then regex rules `EN_RULES`, then phrase replacement (longest first), then punctuation. Simplified: word table `CN_WORDS`, then a character map.
- Elements with `data-notr` are skipped (user data such as recipe and operator names).
- `EN` key groups (comments in the file): top bar, guided tour, bottom / alarm / Overview / Device panel / manual operation / Trend / execution record / paging / dialog box / Administrator / spectrum / Nano Fault clearing and RPi tool update / process paused/continue / Manual pump automatic stop seconds / High voltage interlock and manual plasma / Common backend messages / official version: schedule measurement, operator, maintenance / Maintenance login and operator list / RPi pair / Scheduled alarms and backend messages. The table still holds many strings from earlier versions (e.g. `Administrator login`, `start START`, `Enter password and connect`).
- Site overrides: `web/custom/texts.json` (`{"zh-TW": {original: new text}, …}`), `web/custom/branding.json` (`company`, `logo`, `product_name`).
- The calibration and trend sub-pages do not load `i18n.js` (their HTML has no such script tag), so they stay in Traditional Chinese.

---

## 17. PLC-specific parts of the UI (facts the merge has to deal with)

These are the places where the original UI names the PLC or its registers. Listed as facts, not as a design.

1. Top bar chip `PLC {state}`; status cell `PLC Communication`; alarms `plc_comm`, `plc_cmd`; start blocker `PLC Communication interruption`; messages `PLC Command is being sent...`, `Command accepted, waiting PLC Read back…`; demo text `Simulation mode: no control PLC`.
2. Register numbers in visible text: `40008–40013` (pumps), `40001–40007` (syringe), `40042–40044` (missing feedback), `scratchpad {addr}` per pump row, faceplate row `bit number/scratchpad`.
3. Pump model: **6 pumps**, one **shared** direction and one **shared** speed (1–1000 rpm), individual ON/OFF only, HMI-side auto-stop timer 1–120 s. State = command readback `raw` 0/1.
4. Syringe model: 3 channels with fixed meanings (`CH1 Drainage`, `CH2 Nitric acid`, `CH3 Air`), volume 0.01–5.00 mL, actions aspirate / dispense / reset, no feedback.
5. Flow diagram geometry, tags and `PHASE_PATH` are hard-coded for P1–P5 + syringe; P6 is absent.
6. Slots that exist in the layout but are empty today: three sensor bars `Liquid level is not connected` / `weight is not connected`; panel `No device installed` (valves V1–V3, stirrer); the V1 design's `solenoid valve VALVES` panel in `manual-page.html`.
7. Manual endpoint `POST /api/plc-manual` and the hidden test page's PLC config form (`host`, `port`, `unit_id`, `poll_seconds`) and register monitor.
8. Recipe CSV vocabulary uses `P1`–`P6`, `CH1`–`CH3`, `pump_speed`, `pump_direction`.
9. RPi link in this UI is SSH with key pairing (`pair RPi`), `RPi tool` upload, `from RPi Import…`; the EC program uses TCP 8767 instead (per its historical-development-notes.md), so the whole `RPi connection` tab's pairing/tools rows describe a transport EC does not have.

---

## 18. Not determined

- Whether the owner means the live `web/index.html` (2026-09/10) or the first V1 design when saying "original UI". Both are in the folder; only the first is served.
- Exact server-side conditions behind `manual_interlock`, `plasma_ready_problem`, `physical_start_blockers`, schedule validation, and the precise `/api/state` producer — `backend.py`, `controller.py`, `scheduler.py`, `plc.py` were not read by this reader.
- What the demo mode (`--demo`) feeds the pages (the folder `demo-data/` was not opened).
- The full V1 design (`HM detector HMI.dc.html` named in `source.json`) is not in this folder; only its two fragments and the adapter were seen, so the V1 tab names beyond `detection method` and `plasma parameters`, and its trend / results layout, are unknown.
- Whether a restart is really needed after `Set-Admin-Password.cmd` (the UI says so; `auth.py:55-57` checks the file on every call, which suggests it is not, but this was not tested).
- How the template runtime treats `disabled="{{ false }}"` and similar edge cases (the rest of `support.js` was not read).
- Whether anything in `tests/` pins UI text or structure beyond what README line 69 mentions.

---

## Cross-check

Done 2026-10-06 by a second reader. `web/index.html` was read at lines 1-140 (head, tokens, top bar CSS), 204-343, 384-503 (footer, dialog, PIN CSS; top bar and banner template), 1042-1874 (all constants, start-up, polling, login, alarms, dialogs, `post`, schedule start, pairing, top bar / nav view-model), 1950-2571 (manual page, metal cards, run log, footer, dialog view-model, every maintenance tab, schedule page, faceplates). Not re-read: the template lines 504-1039 and the flow-diagram view-model 1875-1949. `server.py`, `auth.py`, `controller.py`, `plc.py`, `scheduler.py`, `backend.py` (which this reader had not read) were read completely. API paths in `web/index.html` and in the four sub-apps were listed with a search and compared with section 12.3. Nothing was run. No `data` folder was opened.

### Claims checked against the source and found correct

1. Four script tags, `<x-dc>` template, one `text/x-dc` script with `class Component extends DCLogic`, `$preview` 1024 × 768 (`index.html:7-17,1042,1246`).
2. `NAV` order and labels; the maintenance entry appended last with `🔧 Maintenance` / `Maintenance settings`; `test page` only when enabled; badges on alarm and Correction (`index.html:1055,1857-1863`).
3. Deep links and `Escape` handling (`index.html:1262-1273`).
4. `STALE_MS 5000`, `DIALOG_MS 30000`, `HOLD_MS 3000`, `PUMP_DEFAULT_SECONDS 30`, `PUMP_MAX_SECONDS 120`, `MAX_VOLTAGE 700` (`index.html:1053,1065-1066`).
5. `PUMPS` (six, names and registers), `CHANNELS`, `STATUS_ZH`, `MODE_ZH`, `SCHED_ZH`, `SLOT_ZH`, `PHASE_PATH`, `PHASE_DESC` (`index.html:1044-1080`).
6. `DEFAULT_FORM`, `scheduleRequest`, `blankRequest`, and the body of `POST /api/schedule/start` `{recipe_id, operator, schedule, blank, confirm_hv}` (`index.html:1063-1064,1167-1176,1761-1762`).
7. `PARAMS` table (seven keys, sources, ranges, decimals); `POST /api/parameters` sends all seven keys; the frame-sum tile and its two-step dialog (`index.html:1182-1190,2319-2358`).
8. `ADMIN_TABS` and `PER_PAGE` (`index.html:1161-1162`).
9. Timers: 1000 ms clock / dialog timeout / alarms, 1500 ms `/api/state` with a 4000 ms abort, 10 000 ms `/api/auth`; conditional reloads for run log (10 s), schedule preview (30 s), spectrum list (10 s) (`index.html:1259-1261,1297-1324`).
10. `post()`: headers `Content-Type`, `X-HMI-Token`, `X-Admin-Session`; one retry after 403 `Invalid local request` (`index.html:1712-1723`).
11. Login: `POST /api/admin/login {pin}`, session into `sessionStorage['hmi_admin_session']`, failure text `Login failed:…`; logout clears the session and returns to Overview (`index.html:1364-1381`).
12. PIN keypad `1 2 3 4 5 6 7 8 9 Clear 0 ⌫`, 8-digit stop in PIN mode, toggle `English keyboard` / `Numeric keys` (`index.html:2188-2199`).
13. All eleven alarm rows of 7.2 — ids, priorities, areas, conditions, texts — and the acknowledge logic kept in `localStorage['hmi_acked_alarms']` (`index.html:1584-1655`).
14. Dialog engine: acks must all be ticked, pad validation, PIN pad `^\d{6,8}$`, 30 s auto-cancel with the 90 s / 120 s overrides, message truncation at 700 characters (`index.html:1657-1709,2143-2185`).
15. Start scheduling dialog: lines, the one or three acknowledge rows, both warning texts, 90 s timeout; it first forces the operator picker when none is chosen (`index.html:1734-1757`).
16. Footer: labels, disabled rules, pause without a dialog, stop with a dialog only for a schedule, the three progress states, `⚡ contains high voltage`; `startWhy` texts in order (`index.html:2094-2141`).
17. Manual page: `manualBlock` texts in order, notice variants, direction / speed dialogs, per-pump auto-stop setting in `localStorage['hmi_pump_seconds']`, ON through a dialog and OFF without, syringe dialogs; everything goes to `POST /api/plc-manual` (`index.html:1724-1732,1951-2036`).
18. Metal cards and their five sub-line variants; run-log cell rules (`index.html:2038-2078`).
19. RPi connection tab: the ten fact rows and the nine action buttons with their calls and blocking rules; manual plasma dialog and messages (`index.html:1496-1517,2254-2316`).
20. Recipe tab: validate → publish, delete in two dialogs with the PIN pad, `Set as schedule Recipe` is local only (`index.html:1402-1410,1455-1488,2209-2252`).
21. correction coefficient, spectrum, operator tabs and the schedule page tiles, estimate lines and slot table (`index.html:2360-2510`).
22. Faceplates (pump, syringe, detector) (`index.html:2527-2554`).
23. Section 12.3: every path listed as used is present in the file named; `/api/events`, `/api/schedule`, `GET /api/operators`, `/api/result/latest` are in no served page; `/api/recipes/run` only in `web/workflow/app.js:110`.
24. Section 6 against `server.py:278-285` and `auth.py` (set verbatim, PIN rules, lockout, one extra PIN prompt for Recipe delete).
25. Launchers and port 8766 (`Start.cmd`, `Start-Demo.cmd`, `Start-Kiosk.cmd`, `Set-Admin-Password.cmd`).

No statement of this report was found to be wrong apart from the one sentence corrected below.

### Corrections made in the text above

- 6.1: "600 s idle timeout" was stated without its exception. Added that the shell renews the session by itself on Schedule measurement (preview every 30 s) and on Maintenance settings › spectrum (list every 10 s), so the session never expires while one of those pages is shown.
- 7.2, alarm `rpi_comm`: the condition also requires "not demo", and "RPi needed" is taken from the active schedule, else the running flow, else the selected Recipe.
- 3.8, button `read status`: added that the server does not store the reply of `{action:'status'}`.

### Additions and items of section 18 that the other reports settle

- Server-side conditions (18, second bullet) are now documented in `hmi-controller.md` §4.6 and §5.3 (`manual_interlock`, `rpi_ready_problem`), `hmi-server-auth.md` b.2 (`physical_start_blockers`, `/api/state` producer) and `hmi-controller.md` §6 (schedule validation).
- Demo mode (18, third bullet): the server simulates nothing. No PLC thread, no RPi link, every device command answers 409 with a `Demonstration mode…` text; pages show `simulation` / `simulation mode` because the shell checks its own `demo` flag from `/api/bootstrap` first (`controller.py:129,385`; `index.html:1829-1833`; `hmi-server-auth.md` (d)). By the code, `demo-data/` is simply the data folder a demo session reads and writes (recipes, operators, schedules published in demo); what is in it on this PC was not opened by either reader.
- Restart after `Set-Admin-Password.cmd` (18, fifth bullet): by reading it is not needed — `/api/auth` reports `configured` live and the shell re-reads it every 10 s (`auth.py:55-57`, `index.html:1562-1566`). Not tested.
- The live shell offers no way to run one Recipe once outside a schedule (no call to `/api/recipes/run` or `/api/start`). "One measurement" is a schedule with specified times 1; a calibration is the only other way a flow starts.
- `HSPY OFF＋Nano STOP` exists only on Maintenance settings › RPi connection. An operator without the PIN has no on-screen control that switches the high-voltage supply off other than the footer `Stop STOP` (which cancels a running plasma job and thereby makes the RPi tool switch HSPY off, but does nothing when no flow is active). The server would accept `{action:'stop'}` from an operator (`server.py:283`).
- Operator names are not checked in the browser; the server applies the rule (1–20 characters, no control characters, none of `< > " \`, at most 50 names) and answers 400 (`controller.py:74-76,280-293`).
- The schedule form (`hmi_sched_form`), the acknowledged alarms and the per-pump auto-stop seconds live in the browser's `localStorage`, i.e. per panel and per browser profile; a second browser on the same PC starts with defaults, and alarms acknowledged on one are unacknowledged on the other.
- Alarms exist only in the browser: nothing is written to the audit log when an alarm appears or is acknowledged (the server has no alarm concept; only `blank_qc_fail`, `schedule_*`, `rpi_*` events are audited).

### Still open after the cross-check

- Template lines 504-1039 (exact markup of each page) and the flow-diagram view-model were not re-read.
- 18, first bullet (which "original UI" the owner means) and fourth bullet (the full V1 design is not in the folder).
