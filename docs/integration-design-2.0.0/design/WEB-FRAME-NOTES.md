# The web frame as built (WP6, 2026-10-06) — what a page module codes against

From the report of the engineer who built `pc\web2\` (frame only). Read with SPEC E.17 and AMENDMENTS M5.4 / M7.
The code is the reference: `pc\web2\js\{api,store,dom,dialog,labels,chart,main,frame}.js`.

## Exports

- **api.js**: `get(path, {timeout}?)` · `post(path, body, {timeout}?)` · `class ApiError {status, code, message, reasons}`.
  A 403 `locked` / `pin_unset` reply clears `store.unlock`. (Internal: `bootstrap()`, `intercept(fn)`, `useFixture(fn)`.)
- **store.js**: `store {boot, state, stale, unlock, form, preview, subscribe(fn) -> unsubscribe, setForm(patch), setUnlock(session|null)}` · `DEFAULT_FORM`.
  `store.preview` is `{data, error, reasons, pending}`; `data` is the `/api/schedule/preview` reply or null. `setForm` merges `start` / `end` / `blank` one level deep.
- **dom.js**: `h(tag, props, ...children)` · `setText(el, text)` · `setClass(el, name, on)` · `list(container, rows, keyFn, makeFn, updateFn)` ·
  `pager(container, total, perPage, onChange) -> {page, pages, set(page), slice(rows), touchedAt}`.
  `h` takes `'button.btn.primary#id'`; `'svg'` and `'svg:polyline'` make SVG elements; `style: {..}` goes through the CSSOM.
- **dialog.js** (all resolve `null` on cancel or time-out):
  `confirm({title, lines, detail, warn, ticks, confirmLabel, variant, timeout}) -> true` · `choose({title, choices, wide}) -> value` ·
  `pad({title, min, max, decimals, unit, value, signed}) -> number` · `pin({title}) -> string` ·
  `text({title, value, maxLength, rule, masked}) -> trimmed string` · `form({title, fields, ticks, confirmLabel}) -> {key: value}` ·
  `message(title, text, {level, links}) -> true`.
  - Shapes: `lines [[label, value]]` · `variant '' | 'danger' | 'hv'` · `choices [{value, label, small, disabled (true or the reason), current}]` ·
    `fields [{key, label, kind 'text'|'pad'|'choose', value, empty, ...}]` · form `ticks [{key, label, value}]` (optional rows) · `links [{label, href, download}]`.
  - Optional extras everywhere: `lines`, `detail`, `warn`, `variant`, `timeout`, `cancelLabel`, `actions [{label, value}]` (extra footer buttons).
    Also `choose {columns: 1}`, `text {allowEmpty}`, `confirm {level: 'error'}`. `actions` is how “Refresh” (port dialog) and the pad's “Clear” are done.
- **labels.js**: `round`, `slot`, `schedule`, `step` (filled from `bootstrap.labels`) · `nano`, `fault`, `priority`, `group` ·
  `fmtTime(iso, pattern = 'MM-DD HH:MM')` · `fmtDuration(seconds)` · `motorLabel(id)` · `pumpText(pump)` · `pumpClass(pump)` · `stopMessage(reply) -> {text, level}`.
  Since 2026-10-07 (AMENDMENTS M15.4) also `pumpChip(pump)` (the word of the top bar: the port name when the board is connected, so the screen
  says “pump control board COM3”) and `pumpPort(pump)` (“COM3 = pump control board (Arduino Mega 2560) ”; '' when no port is known).
  `fmtTime` takes the patterns as the spec writes them (`'HH:MM'` is minutes, `'MM-DD'` is month).
  Since AMENDMENTS M16.1 `motorLabel(id)` gives “Pump 8 · CuSO₄” and `motorShort(id)` “Pump 8” (the id is still the pin, 2–9);
  no file of `pc\web` writes “motor” (a test checks it).
- **chart.js**: `drawSpectrum(svgBox, {axis, series: [{values, cls}], range}) -> {ok, from, to, low, high, first, last} | {ok: false, text}` · `niceTicks` · `tickText`.
- **Page module**: `mount(root, ctx)` · `render(state, ctx)` · `show(ctx, arg)` · `hide(ctx)`.
  `ctx = {api, store, dialog, dom, labels, chart, go(page, arg), message(text, level), unlocked(), arg}`.
  `mount` is called after bootstrap arrived; `render` only with a non-null state.
- `frame.js` and `fixtures.js` are internal to the frame.

## Four things a page engineer must know

1. **Maintenance roots.** `#page-maint` holds `div.maint-head` (root of `maint.js`) and one `div.maint-tab[data-tab]` per tab module, all `display: contents`.
   `main.js` shows the chosen tab root only while unlocked. Switch with `ctx.go('maint', 'plasma')`; `ctx.arg` is the tab id; log in with
   `store.setUnlock(session)`. `maint.js` must not import or mount the tab modules itself.
2. **`choose` resolves on the tap.** There is no confirm button, only “Cancel”. An action with consequences needs its own `confirm()` afterwards.
3. **`ui_stale` is in `state.alarms`.** While stale, `state.alarms.active` holds it with id `'ui_stale'` (a string); acknowledging it through the
   normal `/api/alarms/ack` call is handled locally.
4. **`components.css` holds only the shared classes**: panel, ph, q-*, tag, chip, btn, seg, ack, fnav, pri, st, empty, table / thead / tbody / trow,
   c-time, c-msg, c-no, c-num, pager, list, li, notice, kv, stile, pbtn, gbtn, btn-grid, vol, the dialog parts, chart and sl-*.
   Everything page-specific goes in the page stylesheets (`pages.css`, `maint.css`, `analysis.css`).

## What the frame already does (do not build it again)

- Top bar, banner, nav (eight items, 50 px / gap 4; the “!” badge on Correction), footer with Start scheduling / pause / Stop and the `.prog` cases, the
  operator picker, the start dialog (also for a schedule with a blank), the resume dialog, the `ui_stale` rule, the 600 s idle logout.
- The footer shows a page's own message (`ctx.message(text, level)`) and `pump.last_result` for 12 s (an error for 30 s while no schedule runs).
- A dialog that just appeared ignores taps for 0.35 s. The pad's first digit replaces the value that was there. A tap on “Stop STOP”
  through a dialog's scrim closes the dialog and stops.

## Development aid

Serve `pc\web2` with any file server and open `…/#dev-fixture` or `#dev-fixture=running` (also `waiting`, `pausing`, `paused`, `stopping`, `alarm`,
`acked`, `calibration`, `unlocked`, `silent`). In the console: `plasmicDev.use('paused')`, `plasmicDev.state`, `plasmicDev.store / dialog / go`.
It loads only with that hash and only when `/api/bootstrap` cannot be reached. With the practice-mode server running it is not needed.

## The server as built (WP5) — for page work in practice mode

- Start: `.\pc\runtime\python\python.exe -B .\pc\app2.py --demo --no-browser --port 879N --sim-port 877N --data $env:TEMP\plasmic-dev-N`; open
  `http://127.0.0.1:879N/`; PIN 000000; choose “Operator” before any schedule. New js / css files are served without a restart (practice mode only).
- One measurement = Scheduled measurement total 1 Second-rate with “Discharge measurement only”: about 20 s (the simulator runs in real time). With plasma parameters Cycle 2 / Loopnum 1 /
  Integration_Time 100 / Spectra_Interval 200 it is about 5 s.
- Concentrations: run a calibration with “Discharge measurement only” as the standard Recipe (the simulator's sample follows each standard by itself), apply it
  (log in on Maintenance first); afterwards every sample shows values drifting around Cu 0.5 / Zn 0.3 / Ni 0.4, a blank gives zeros. Set Voltage 400
  first: at 100 V the fit scatters, at 400 V it is within 0.02.
- The pump simulator runs at real speed (automatic cleaning 122 s). Records and alarms persist in the `--data` folder between runs.
- Unknown ids: GET → 404; a POST that names an unknown Recipe → 404 “Recipe does not exist”. `/api/recipes/validate` takes exactly one of `file_b64` / `csv` / `rows`.
- The tick row for an unreadable high voltage shows the server's sentence: `state.interlock.manual.hv_ack_text` (manual starts) and
  `preview.needs.hv_ack_text` (schedule start) — never a text of the page's own (AMENDMENTS M13).

## The wide touch frame (2026-10-07, AMENDMENTS M16.6)

The person asked for a screen that is used with a finger only and that fills the monitor (the rig: 1920×1080 at 150 %, so 1280×720
CSS pixels). The folder is `pc\web` (the `web2` of the lines above was its name while it was built).

**Size.** `main.js` `fitWindow()`: `FRAME_W = 1024`, `FRAME_H = 768`, `FRAME_MAX_W = 1680`. The logical height stays 768; the logical
width follows the window. A window 4:3 or wider: `k = innerHeight / 768`, `w = clamp(round(innerWidth / k), 1024, 1680)`; a narrower
one: `w = 1024`, `k = innerWidth / 1024`. It sets on `:root` `--k` (a number), `--w` (px) and `--wx` (`w − 1024`, a plain number);
`base.css` makes `--extra: calc(var(--wx, 0) * 1px)` of it: 0 at 4:3, 341px at 16:9, 656px at most.
`.hmi` is `width: var(--w, 1024px); height: 768px`, centred, `transform: scale(var(--k, 1))`, and a container
(`container: hmi / inline-size`): rules for a wide frame only are written `@container hmi (min-width: 1280px) { … }`.

| Window | w | k | Usable page area |
|---|---|---|---|
| 1024×768 | 1024 | 1 | 888×582 |
| 1280×720 and 1920×1080 | 1365 | 0.9375 / 1.40625 | 1229×582 |
| 1920×1200 | 1229 | 1.5625 | 1093×582 |
| 2560×1080 | 1680 (the cap; bars at the sides) | 1.40625 | 1544×582 |
| 1280×1024 | 1024 (bars above and below) | 1.25 | 888×582 |

A page section is `(w − 116) × 598`, usable `(w − 136) × 582`: **the height never changes, so the row counts of every list are what
they were**; a page gives the extra width away with `--extra` (`flex: 0 0 calc(392px + var(--extra) * .32)`) or with flex. Unchanged:
top 56, banner 44, nav 116, foot 70.

**A finger.** `html { touch-action: none }`, `overscroll-behavior: none`, no text selection except in inputs, no context menu on a
long press, no `:hover` in any style sheet (pressed state: `:active`). What can be tapped is 44 px high or more (`.btn`, `.seg button`,
pager buttons, tiles, choices); where the fixed height leaves no room it is lower: step rows of Overview (32), Recipe rows (32), Single sheet inspection
rows (36), operation event rows (36), and in a 4:3 window the message boxes of Schedule measurement (about 23; 44 in a wide frame). Things that were told
by a tooltip only are now reachable by a tap (the two chips of the top bar, the footer cells of a running schedule, a metal card, a
cut header line).

**Keyboard.** Every text dialog (and a text field of `form`) shows keys under its input: digits, a–z, `. - _ : / @`, Blank, in uppercase
(a toggle), ⌫, Clear. The keys write at the caret and never take the focus, so a hardware keyboard and an input method (Chinese) work
as before; a masked field stays masked. Text dialogs are always 720 px wide. Numbers go through `pad` as before.

**Full screen** (`frame.js`, `#fs-gate` / `#fsbtn` of `index.html`). Only the Fullscreen API, which a browser grants on a tap and on
nothing else: at the start of the page a layer over the window says “Click the screen to start (full screen)” — a tap anywhere enters; “does not require full screen”
only takes the layer away. The top bar has “full screen” / “Leave full screen”. “does not require full screen” and “Leave full screen” write
`localStorage['plasmic.fullscreen'] = 'off'` (the layer then stays away), “full screen” clears it. No layer with `?fs=0` in the address
(the screenshot tool), with the development fixture, or in a window that fills the screen already (F11, `Start-Kiosk.cmd`: there is no
button either, the page cannot leave that). A tap on “Stop STOP” through the layer takes the layer away and stops.

**Schedule measurement with the four stages (M16.4).** `schedule.js` builds `div.sch-stages` (four `button.stile.stage`, the first also `.fill`)
inside `.sch-grid` and puts the class `staged` on the grid while they show. `pages.css`: ① Add liquid has a row of its own (every pump and
its amount, two lines at most), ②③④ share the next; the staged grid has lower tiles (Process 48, stages 60, settings 72) so that it is
344 px high like the four 80-px rows of a Recipe — the estimate and the messages keep their room (measured at 1024×768 and 1280×720:
nothing overflows; 70 px and 96 px are left for messages).

**Seen, and not seen.** Every page was looked at in headless Edge at 1280×720 and 1024×768 (and three other shapes) with synthetic
mouse and touch events, and the schedule and maintenance pages in the browser pane of the session. **Not seen: a real finger on the
rig's monitor**, pinch / overscroll / long press there, the Windows touch keyboard over a text dialog (no `inputmode="none"` is set, so
Windows may show its own when no keyboard is attached), what Esc does in full screen with a dialog open, and that “illustrate” (a new tab)
ends full screen.

## The plasma parameters are one module (2026-10-07, AMENDMENTS M17.3)

`pc\web\js\plasma.js` (not a page): `PARAMS` (the table of SPEC E.12), `valuesOf(state, stale)`, `topVoltage(state)`, `text(value)`,
`summary(values)`, `inWords(sentence)`, `edit(ctx) -> reply | null`. Maintenance › plasma parameters imports the table; Schedule measurement imports `edit`, `summary`
and `valuesOf` — its tile measurement content is a `button.stile` now. A page that needs the parameters imports them from there; no second table
(a test checks that).

## “Recheck” is one function (2026-10-07, AMENDMENTS M18)

`pc\web\js\check.js`: `checkNow(api, dialog) -> reply | null` (POST `/api/rpi/check`, then a dialog with the two rows and the advice).
The footer has `button.btn.recheck` inside `.prog` (shown beside “cannot be started” for the reasons `rpi_not_ready` / `hv_unknown` / `hv_on`);
the cells “HSPY high voltage power supply” and “Nano controller” of Overview offer it as an action of their dialog. A page that wants it imports it from there.

## The pump setup and a hardware view that can change (2026-10-07, AMENDMENTS M20)

`bootstrap.hardware` is no longer the same for the life of the page: Maintenance › Pump and Scale can change what each pump carries, the two pumps of the
cleaning and the valve links, and a pump calibration changes the step factors. `hardware.rev` grows with every change and
`state.pump.hardware_rev` carries the same number; `main.js` reads `/api/bootstrap` again when they differ, and `labels.setup()` runs for
every bootstrap reply (a store subscriber, so it is done before the next render). **A page must not keep anything it read from
`ctx.store.boot.hardware` at mount time**: read it in `render()`, or build the rows again when the object is another one
(`manual.js` mounts itself again; `maint-pumps.js` fills its facts in `render()`). The names of the pumps always come from
`ctx.labels.motorLabel(id)` / `motorShort(id)` or from `state.pump.motors[].label`.

## The looks are one file (2026-10-07, AMENDMENTS M21)

`pc\web\css\polish.css` comes last and sets looks only: corners, lines, fills, shadows. A new page gets them without doing anything
(every `button` inside `.hmi` is rounded, every `.panel` has the rounded frame, the title strip and `overflow: hidden`). What a page must
know: a full-width row inside a panel wants square corners (give it the class `trow` or `li`, or add its selector to the second rule of
that file), and nothing may rely on drawing outside its panel. Sizes and positions stay in the page's own style sheet.

## The step list of Schedule measurement (AMENDMENTS M23)

- `js\flowsteps.js` holds the words of a step (`stepText`, `stepsLine`) and its dialogs (`editSteps`); `pages\schedule.js` gives it four
  functions (`load`, `save`, `editParameters`, `plasma`) and shows one tile (`.sch-stages .stile`, 128 px, four lines of steps).
- A step is a row of a Recipe; the page writes only the actions of `recipe.FLOW_ACTIONS` (a test compares the two lists).
- A pump and its amount are written “Pump 1: 5 mL” in every script: never a label, a space and an amount (a test reads for it).
- `dialog.choose({left: true})`: the choices read as a list (text from the left). Used by the step list only.
- Every change is posted at once; the reply is the new state of the list (`valid`, `error`, `error_step`, `view`).
