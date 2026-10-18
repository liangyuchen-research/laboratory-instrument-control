# HMI v1.1 visual style guide (subsystem: hmi-ui-style)

Read-only investigation of `C:/Users/PlasmaLab/Desktop/UI/UI/hmi_v1_1`. Nothing was modified, run, or connected.
Goal of this document: let an implementer rebuild the look of the original HMI in plain HTML + CSS (no React), for the merged EC + V7 program.

All paths below are relative to `C:/Users/PlasmaLab/Desktop/UI/UI/hmi_v1_1/`. Short names used for references:

| Short | File | Lines |
|---|---|---|
| IDX | `web/index.html` | 2573 (style block 18-468, template 471-1039, logic 1042-2571) |
| DS | `web/original/_ds/industry-9d663c7d-84bf-476c-8738-ab0ad0637c7d/styles.css` | 286 |
| SHELL | `web/shell.css` | 6 |
| FONTS | `web/vendor/fonts.css` | 35 |
| CAL | `web/calibration/style.css` (+ `web/calibration/index.html`) | 128 (+98) |
| TREND | `web/trend/style.css` (+ `web/trend/index.html`) | 56 (+73) |
| WF | `web/workflow/styles.css` (+ `web/workflow/index.html`) | 51 (+60) |
| TEST | `web/test/style.css` (+ `web/test/index.html`) | 24 (+92) |
| LITE | `lite/style.css` (+ `lite/index.html`) | 3 (+25) |
| ADMINFRAG | `admin-page.html` | 92 |
| MANUALFRAG | `manual-page.html` | 90 |
| SUPPORT | `web/original/support.js` (skimmed) | 1911 |
| I18N | `web/i18n.js` (only lines 1-30 and 410-532 read) | 532 |

Files read completely: IDX, DS, SHELL, FONTS, CAL, TREND, WF, TEST, LITE (css and their index.html), ADMINFRAG, MANUALFRAG, `web/original/source.json`, `web/original/_ds/.../_ds_bundle.js`, `web/custom/branding.example.json`, `web/custom/texts.example.json`, `Start-Kiosk.cmd`, `README.md` lines 1-75. Skimmed: SUPPORT (lines 1-200, 360-500, 575-610, 1020-1060, 1360-1625), I18N (head + engine). Only grep'd (not read): `web/trend/app.js`, `web/calibration/app.js` (for chart colours). Not opened, per the rules: `data/`, `.venv`, `offline_bundle/`, `dist/`, `__pycache__/`.

---

## 1. Big picture: three visual generations live in this folder

1. **The live operator HMI = the inline `<style>` block of IDX (IDX:18-468).** This is what the owner sees today. It is an ISA-101 "high-performance HMI" look: grey ground, square corners, hairline borders, colour reserved for "running" (blue) and "abnormal" (magenta / red / orange / yellow). Fixed 1024 x 768 frame. All class names are short (`.top`, `.banner`, `.nav`, `.page`, `.panel`, `.ph`, `.btn`, `.seg`, `.tag`, `.trow`, `.dialog`, ...).
2. **The "Industry" design system = DS.** Tokens (`--color-*`, `--font-*`, `--space-*`, `--radius-*`, `--shadow-*`) and generic component classes (`.btn`, `.input`, `.card`, `.tag`, `.nav`, `.table`, `.dialog`, `.seg`, `.blueprint`). It came with the original design hand-off (`web/original/source.json:2` names the source `"...design_handoff_hmi_backend\\HM detector HMI.dc.html"`). The live HMI still loads it and depends on a few of its declarations (fonts, `--color-bg`, reset, and some residue described in section 7), but almost none of its component looks.
3. **Legacy / side pages**: ADMINFRAG and MANUALFRAG (the first-generation "blueprint" look, only used by `prepare_original_ui.py`, see `README.md:61`), WF (old long admin page; `README.md:57` says it is no longer used in the HMI), TEST (the hidden seventh "test page", older blue palette, `ui_features.json` gates it), LITE (a separate dark-theme "HM Simple control" page). CAL and TREND are current: they are separate documents shown inside the shell in `<iframe>`s and re-declare the live palette.

**Recommendation for "same style as the original"**: rebuild generation 1 (IDX style block). CAL and TREND show how the same look was re-done in plain HTML/CSS without React (useful templates for a no-React rebuild).

### 1.1 How the page is assembled (matters for the cascade)

- IDX is a "Design Component" document: `<x-dc>` holds a template with `{{ value }}` interpolation, `<sc-if value="...">`, `<sc-for list="..." as="...">`, and `<helmet>` (IDX:14-17, 469-1041). SUPPORT (generated "dc-runtime", SUPPORT:1) replaces `<x-dc>` by `<div id="dc-root">` (SUPPORT:165-168) and mounts React into it; the DOM is `#dc-root > div.sc-host > div.hmi` (SUPPORT:1031).
- Every `{{ value }}` text is rendered as `<span class="sc-interp">value</span>` (SUPPORT:607). This is why the CSS uses direct-child selectors such as `.kv div > span`, `.pbtn > span`, `.dlg-line > span` (`README.md:24`). In plain HTML there is no such wrapper span.
- `<helmet>` children are appended to `document.head` in source order (SUPPORT:1433-1489): first the DS `<link>` (IDX:16), then the DS bundle script (IDX:17, an empty namespace stub), then the inline `<style>` (IDX:18).
- **Stylesheet order in the live page: SHELL (IDX:11) -> DS (which `@import`s FONTS, DS:2) -> inline style block.** Same-specificity rules in the inline block win over DS property by property; DS declarations that the inline block does not override still apply (list in section 7).
- `data-props="{&quot;$preview&quot;:{&quot;width&quot;:1024,&quot;height&quot;:768}}"` (IDX:1042): because a preview size is present the runtime does **not** inject its full-page CSS (SUPPORT:132, 169-173). There is no scaling, centring or responsive code anywhere in the files read.
- `style-hover="..."` / `style-focus="..."` attributes (only in ADMINFRAG / MANUALFRAG) are compiled by the runtime into generated `:hover` / `:focus` rules with `!important` (SUPPORT:428-431, 1567-1588). The live IDX template has none.

---

## 2. Design tokens

### 2.1 Colour

#### 2.1.1 Live HMI tokens (IDX:29-35, verbatim)

```css
  :root {
    --run: #2f5f8f; --run-bg: #dce9f6;
    --bad: #a3168e; --bad-bg: #fbe8f7;
    --p1: #c62828; --p2: #e06a00; --p3: #e0b400;
    --ink: #1d1f20; --ink-2: #45484b; --ink-3: #6b6e72;
    --line: #b9bcc0; --panel: #fbfbfb;
  }
```

| Token | Hex | Role |
|---|---|---|
| `--run` | `#2f5f8f` | "Running / command ON" blue. Also: primary action buttons, selected nav item, selected segment/tab, selected list row, current step, focus ring, input-buffer border |
| `--run-bg` | `#dce9f6` | Pale blue: changed value tile, running countdown button, selected spectrum row; also used as secondary text colour on blue (`.step.cur .no`, `.li.sel > span`, `.gbtn.primary small`) |
| `--bad` | `#a3168e` | Magenta = communication failure / value not trustworthy (always with "?" prefix and dashed border) |
| `--bad-bg` | `#fbe8f7` | Background for the above |
| `--p1` | `#c62828` | Alarm priority 1 "Emergency" (red). Also everything high-voltage: HV button frame, HV dialog, E-stop notice frame |
| `--p2` | `#e06a00` | Alarm priority 2 "high" (orange). Also: failed step/status, danger dialog frame + confirm button, error left bar |
| `--p3` | `#e0b400` | Alarm priority 3 "middle" (yellow, with `#111` text). Also warning left bar |
| `--ink` | `#1d1f20` | Primary text; heavy 2 px frames (dialog, faceplate, STOP button); active pipe / running pump fill in the flow diagram |
| `--ink-2` | `#45484b` | Secondary text; table header rule (2 px); selected language button |
| `--ink-3` | `#6b6e72` | Tertiary text: labels, units, hints, N/A state |
| `--line` | `#b9bcc0` | Standard 1 px border of panels, chrome, chips, nav buttons, tags |
| `--panel` | `#fbfbfb` | Panel background |

The state rule, verbatim from the style header (IDX:19-28):

```
   * status display rules (consistent across the entire screen)
   *   The color is only used to indicate ”abnormality” or ”operation”, and the normal state remains grayscale.
   *   operation/command ON ........ blue solid          .q-run
   *   normal value/command OFF ...... dark gray text          .q-ok
   *   communication failure (value cannot be trusted).. magenta＋“?”dashed box  .q-bad
   *   not installed/not connected ........ light gray dotted frame        .q-na
   *   Uncorrected ................ slash shading          .q-uncal
   *   Alarm priority (only used for alarms) Emergency Red .p1 / high Orange .p2 / middle yellow .p3
```

The logic helper that produces these (IDX:1231-1232): `Q.ok(t)` -> `q-ok`; `Q.run(t)` -> `q-run`; `Q.bad(t)` -> text `'? ' + (t || 'communication failure')`, class `q-bad`; `Q.na(t)` -> text `t || 'not connected'`, class `q-na`. Priorities (IDX:1043): `PRI = {1:{name:'Emergency',cls:'p1'}, 2:{name:'high',cls:'p2'}, 3:{name:'middle',cls:'p3'}}`.

#### 2.1.2 Literal colours in the live HMI, by role (all from IDX:36-467)

Grounds and surfaces
- `#d9dadc` page background outside the 1024 x 768 frame (IDX:36, SHELL:2)
- `var(--color-bg)` = `#f2f2f3` background of `.hmi` (IDX:42, DS:5)
- `#fff` top bar, footer, chips, all buttons, dialogs, faceplate, value boxes, chart plot
- `#f7f7f7` nav rail (IDX:92); `#fbfbfb` panels; `#eceded` "no alarm" banner (IDX:78); `#e6e7e9` flow-diagram ground (IDX:156); `#f4f4f5` equipment fill (IDX:163); `#f6f6f7` dialog detail box (IDX:414); `#f6f6f6` locked tile (IDX:449); `#e4e8ec` completed step (IDX:139)

Borders and dividers
- `#b9bcc0` (`--line`) panel / chrome; `#8b8f94` button and input border (darker than panels); `#dcdee0` row dividers; `#d0d2d5` dotted key/value dividers and disabled border; `#c9ccd0` chart frame and disabled frames; `#9a9a9a` uncalibrated tag border

Disabled
- text `#a3a6aa`, border `#d0d2d5`, fill `#f3f3f3` (IDX:222); primary disabled fill `#b9c6d3` (IDX:224); selected-segment disabled `#8aa3bd` (IDX:220); START disabled `#e4e6e8` / `#c9ccd0` / `#8b8f94` (IDX:388); dialog OK disabled `#c9ccd0` (IDX:436)

Flow diagram greys
- pipe `#a4a7ab`; equipment stroke `#3a3d40`; N/A stroke `#9a9da1`, N/A text `#8a8d91`, N/A fill `#eceded`; hatch `#b5b7ba` on `#eceded`; uncalibrated hatch `#e2e2e2` / `#f6f6f6`; faceplate header `#3a3d40`

Green (the only "good" colour, used sparingly)
- `#3d7a52` OK dot in a chip, calibrated tag, completed status border; `#2b5a3b` completed status text; `#2f7a4b` RESUME button fill (IDX:393)

Amber / warning family
- `#d9a300` demo-mode inner ring (IDX:43); `#b88a00` amber border (demo chip, PAUSE button, "needs input" tile, skipped status); demo stripes `#ffe7a3` / `#fff3cc`
- `#ffe9a8` paused step fill, `#5a4100` its text; `#6b4d00` pause text; `#8a5b00` warning text (list warn, QC flag, chart reference lines, needs-input value); `#8a6d00` p3 text in status strip; `#ffe08a` warn text on a selected (blue) row
- `#fff7d6` warning box fill (`.dlg-warn`, `.sch-warn`, skipped status); `#fff4d6` / `#c99a2e` / `#7a5200` blank-sample tag
- `#fff6e5` unacknowledged alarm row, flagged row; `#f5dcae` flagged + selected row

Orange-brown / error family
- `#8a3d00` error text (`.notice.err`, `.login-err`, `.txt-warn`, `.msgchip.err`, `.sch-err`, operator-not-chosen); `#fff4ea` error fill; `#fff1e3` danger dialog header

High voltage
- hazard stripes `#ffd000` / `#1d1f20` at 135 deg, 9 px bands (IDX:327-329, 450); `#8a1c1c` small text on HV button; `#fdecec` ticked acknowledgement row

Chart
- mean line `var(--ink)`; single frame `var(--run)`; overlay frames `#8fb0d0`; grid `#eceded`; zero line `#b9bcc0` dashed; reference lines `#8a5b00` dashed

Overlays
- modal scrim `rgba(20,22,24,.45)` (IDX:405); dialog shadow `rgba(0,0,0,.35)`; faceplate shadow `rgba(0,0,0,.28)`; banner button `rgba(255,255,255,.92)` with border `rgba(0,0,0,.35)`

#### 2.1.3 DS tokens (DS:4-64). Only `--color-bg`, `--font-heading`, `--font-body`, and (through residue) `--color-divider`, `--color-accent`, `--space-3`, `--space-4` actually reach the live look.

| Token | Value | Role in DS |
|---|---|---|
| `--color-bg` | `#f2f2f3` | page ground (used by `.hmi`) |
| `--color-surface` | `#e9e9ea` | cards, inputs |
| `--color-text` | `#1d1f20` | text |
| `--color-accent` | `#5980a6` | accent (links, primary button, default focus ring) |
| `--color-accent-2` | `#728fab` | second accent |
| `--color-divider` | `color-mix(in srgb, #1d1f20 16%, transparent)` = `rgba(29,31,32,.16)` | hairlines |
| neutral 100-900 | `#f5f5f8 #e7e7ea #d4d4d7 #b7b7ba #98989b #7a7a7d #5d5d60 #424244 #2b2b2d` | ramp |
| accent 100-900 | `#eef6ff #d6ebff #b5d9fd #94bce3 #749dc4 #597ea3 #416180 #2c455d #1d2d3d` | ramp |
| accent-2 100-900 | `#eef6ff #d6ebff #bdd8f2 #9ebbd8 #7e9cb8 #627d98 #486077 #314457 #1f2d3a` | ramp |

#### 2.1.4 Sub-page palettes

- CAL (CAL:1-5): `--run #2f5f8f; --run-bg #dce9f6; --bad #a3168e; --bad-bg #fbe8f7; --warn #8a5b00; --warn-bg #fff4d6; --ink #1d1f20; --ink-2 #45484b; --ink-3 #6b6e72; --line #b9bcc0; --panel #fbfbfb; --bg #eef0f2; --ok #3d7a52`. Extra literals: `#e06a00` round badge and prompt frame, `#c62828` danger button, `#e6c56b` warning border, `#2b2e31` toast, `#f2f5f8` blank row, `#a34d00`.
- TREND (TREND:1-4): same `--run/--bad/--ink*/--line/--panel`, plus `--blank #c27a00`, `--bg #eef0f2`. Notice box `#7a5200` on `#fff4d6`, border `#e6c56b`.
- WF (WF:2-3): `--run #2f5f8f; --ink #1d1f20; --ink-2 #45484b; --ink-3 #6b6e72; --line #b9bcc0; --p2 #e06a00; --p3 #e0b400`, background `#fbfbfb`. Comment WF:1: "Same palette and type scale as the main HMI (web/index.html)."
- TEST (TEST:1), older palette: `--bg #f2f2f3; --surface #e9e9ea; --ink #1d1f20; --muted #71777d; --blue #5980a6; --darkblue #416180; --line #ccd0d4; --green #387357; --red #aa5148; --amber #967021`, page ground `#d9dadc`.
- LITE (LITE:1), dark: ground `#101820`, text `#eef5f8`, header `#172530`, card `#1a2a36`, borders `#344553 / #344c5b / #507087`, button `#253b4b` (hover `#315268`), primary/selected `#157c91` border `#26adc3`, danger border `#b45757` text `#ffb8b8`, link `#7bd2e6`, inputs `#0e1b25`.

### 2.2 Typography

Families (DS:44-46):

```css
  --font-heading: "Barlow Condensed", system-ui, sans-serif;
  --font-heading-weight: 600;
  --font-body: "Barlow", system-ui, sans-serif;
```

Root of the live HMI (IDX:42): `font-family:var(--font-body), "Microsoft JhengHei", sans-serif; font-size:15px;` -> resolved list `"Barlow", system-ui, sans-serif, "Microsoft JhengHei", sans-serif`. Line-height is inherited from DS `body { ... line-height: 1.55; font-weight: 400; }` (DS:108).

The bundled Barlow files are 81-87 KB each (Latin only), so Chinese text is drawn by the fallback (`system-ui` comes before "Microsoft JhengHei" in the list). Which font `system-ui` resolves to on the panel was not determinable from code.

Where each family is used in the live HMI:

| Family | Used for |
|---|---|
| Barlow (body) | everything not listed below; `button { font-family:inherit; }` (IDX:37) |
| Barlow Condensed (heading) | product title `.title` 22px/600 (IDX:47); clock 19px (IDX:70); banner time (IDX:86); big readings `.metal-val b` 30px (IDX:128); syringe volume `.vol` 24px (IDX:243); table time/number columns `.c-time` 16px, `.c-num` 14px, `.c-hm` / `.c-plan` 15px; parameter value `.pbtn b` 26px; calibration cell `.cbtn` 24px; keypad buffer `.dlg-buf` 32px. **Also every `.btn`** (weight 600) through DS residue, see section 7 |
| `Consolas, monospace` | masked/plain text entry buffer `.login-buf` 22px (IDX:299); CSV action column `.c-act2` 12px (IDX:319); RPi folder name `.c-job` 12px (IDX:383) |

Side pages do not use Barlow: CAL and TREND use `15px "Microsoft JhengHei", "Noto Sans TC", sans-serif` (CAL:8-9, TREND:7); WF uses `"Barlow","Microsoft JhengHei","Noto Sans TC",sans-serif` (WF:3) and loads FONTS itself; TEST uses `'Segoe UI','Microsoft JhengHei',sans-serif` 14px; LITE uses `system-ui,"Microsoft JhengHei",sans-serif`.

Font-size scale in the live HMI (px): 11 (chart ticks, tiny notes) - 12 (small labels, units, tags in SVG) - 13 (sub text, tags, table header, status pills) - 14 (chips, secondary rows, warnings) - **15 (base)** - 16 (nav, buttons, banner, dialog lines) - 17 (cell values, grid buttons) - 18 (panel titles `.ph h2`, pump names) - 19 (metal name, clock, HV button) - 20 (dialog title, STOP) - 21 (START, tile value, empty-state title) - 22 (title, text buffer) - 24 (keypad keys, volume, calibration cells) - 26 (parameter values, PIN keys) - 30 (concentration readings, PIN dots) - 32 (keypad buffer).

Weights: 400 normal, 600 (`.title`, `.banner .msg`, DS headings / `.btn`), 700 (all "bold" emphasis: selected states, values, titles). Loaded faces: Barlow 400 / 500 / 700, Barlow Condensed 400 / 600 (FONTS:1-35).

Other text rules: `html[lang="en"] .nm { font-size:12px; }` (IDX:105) shrinks equipment names in English; `white-space:nowrap` + `text-overflow:ellipsis` almost everywhere (fixed layout, long text opens in a dialog); CAL/TREND use `font-variant-numeric: tabular-nums` for numbers.

DS heading scale (DS:109-119), only relevant if h1-h6 are used unstyled: h1 42, h2 32, h3 25, h4 20, h5 16, h6 13 (uppercase, 0.08em); `line-height:1.12; letter-spacing:-0.015em; margin:0 0 6.8px`. The live `.ph h2` overrides size/weight/family (`font-size:18px; font-family:inherit; font-weight:700;`, IDX:103) but keeps the DS line-height and letter-spacing.

### 2.3 Spacing

- DS scale (DS:48-53): `--space-1 3.4px; --space-2 6.8px; --space-3 10.2px; --space-4 13.6px; --space-6 20.4px; --space-8 27.2px`. Not used by the live rules (only reaches the page via the `.dialog` residue).
- The live HMI uses literal pixels. Recurring values: gaps 3 / 5 / 6 / 8 / 10 / 12 / 14 px; page padding `8px 10px` with `gap:8px` between panels (IDX:100); panel header padding `6px 12px` (IDX:102); row horizontal padding `0 12px` (IDX:254); dialog parts `12px 16px` / `10px 16px` (IDX:408-431); top bar and footer `0 12px` with `gap:10px`.
- Touch target heights: nav 54 (IDX:93), standard `.btn` 40 (IDX:221), tab 44 (IDX:281), list row 58 (IDX:283), table row 44 (IDX:257), pager button 40, keypad key 52 (IDX:428), on-screen key 56 x 50 (IDX:292), PIN key 104 x 62 (IDX:465-466), dialog footer buttons 52 (IDX:432), START/STOP 54 (IDX:387-391), pump ON/OFF 64 x 46 (IDX:236), smallest: table `.ack` 34 and language buttons 36 x 34.

### 2.4 Radii, borders, shadows

- **Radius: 0 everywhere in the live HMI.** DS defines `--radius-sm 2px; --radius-md 4px; --radius-lg 7px` (DS:55-57) but then forces `.card, .btn, .input, .tag, .seg, .dialog { border-radius: 0; }` (DS:281, the "blueprint frame"). The only round things: P&ID hit targets and legend dots (`border-radius:50%`), CAL's notification badge (`border-radius:10px`, CAL:30). TEST uses 2 px; LITE 6-10 px.
- Borders: 1 px solid `--line` for panels/chrome; 1 px solid `#8b8f94` for buttons and inputs; 2 px solid `--ink` for dialog, faceplate, STOP; 2 px solid `--run` for input buffers, "changed" tiles and running countdown; 2-3 px solid `--p1` for high-voltage items; 1 px **dashed** for N/A (`--line` or `#9a9da1`), comm-failure (`--bad`), maintenance nav entry, locally-saved setting; 2 px dashed `#b88a00` for "must fill in"; left accent bars 5-6 px on notices; dotted `#d0d2d5` under key/value lines; table header rule `2px solid var(--ink-2)`.
- Shadows: dialogs `0 12px 40px rgba(0,0,0,.35)` (IDX:406, 381); faceplate `0 8px 28px rgba(0,0,0,.28)` (IDX:186); demo mode `inset 0 0 0 5px #d9a300` on the whole frame (IDX:43). DS shadows `--shadow-sm/md/lg` (DS:61-63) are unused by the live look. No gradients except hazard / hatch patterns; no animation except the alarm blink (`@keyframes blink { 50% { opacity:.35; } }`, IDX:40, applied as `animation:blink 1s steps(1) infinite` on the unacknowledged banner priority box, IDX:84).

---

## 3. Page frame

### 3.1 Target screen and input

- Fixed **1024 x 768** frame: `.hmi { position:relative; width:1024px; height:768px; ... display:flex; flex-direction:column; overflow:hidden; }` (IDX:42); `html,body{margin:0;min-width:1024px;min-height:768px;background:#d9dadc}` with the comment "Keep the supplied 1024 x 768 machine layout." (SHELL:1-2); `$preview` 1024 x 768 (IDX:1042). The frame is not centred or scaled: on a larger window it sits top-left on a `#d9dadc` ground.
- **Touch panel in kiosk mode**: `Start-Kiosk.cmd:14` starts Edge with `--kiosk http://127.0.0.1:8766/ --edge-kiosk-type=fullscreen --no-first-run --disable-pinch --overscroll-history-navigation=0`. CSS comments: "Touch operation: All lists are paged with a fixed number of pens, and no scroll appears." (IDX:275), "On-screen keyboard" (IDX:289); `README.md:56`. Consequences baked into the style: no scrollbars in the shell (`overflow:hidden` on lists, pagination with "◀ Previous page / Next page ▶"), numeric keypad and on-screen keyboard instead of native inputs, every value is a tap-to-edit button, long texts are truncated and open in a dialog, no hover-only affordances.
- Rows per page are tuned to the frame height (IDX:1161): `PER_PAGE = {runs: 9, alarms: 8, events: 12, recipes: 6, csv: 7, specs: 5, qc: 10, jobs: 8, slots: 9, ops: 8, choices: 8}`.
- The actual physical panel resolution was not determinable from the files read.

### 3.2 Skeleton and dimensions

```
.hmi  1024 x 768, column flex, overflow hidden            (.hmi.demo adds a 5 px amber inner ring)
+-- .top      height 56   white, border-bottom 1px --line          product title | mode | (spacer) | link chips | operator | [maintenance] | language | clock
+-- .banner   height 44   alarm banner (grey when none)
+-- .body     flex 1  (= 768 - 56 - 44 - 70 = 598 px)     row flex
|   +-- .nav  width 116   #f7f7f7, border-right 1px --line          vertical buttons, maintenance entry pinned to the bottom
|   +-- .page flex 1  (= 908 px wide)  padding 8px 10px, gap 8px    -> usable 888 x 582; one or more .panel
|       (or an <iframe class="test-frame"> inside .test-extension for Correction / Trend / test page)
+-- .foot     height 70   white, border-top 1px --line              START | PAUSE | STOP | progress text | E-stop notice
overlays (absolute, inside .hmi): .face (faceplate, z 30), .imp (import popup, z 35), .overlay (z 40) + .dialog (z 41)
```

### 3.3 Top bar `.top` (IDX:46-74, template IDX:474-488)

Order left to right: `.brandbox` (optional `img.brand-logo` + `.title`) - `span.mode` - `span style="flex:1"` - `span.chip` "PLC ..." - `span.chip` "RPi ..." - `button.opbtn` (operator) - `button.userbtn.admin` "maintenance mode" (only when logged in) - `div.lang` (three buttons Traditional / simple / EN) - `div.clock` (date small above, `HH:MM:SS` below).

- `.top { flex:0 0 56px; display:flex; align-items:center; gap:10px; padding:0 12px; border-bottom:1px solid var(--line); background:#fff; }`
- Texts: default title "online metal wastewater detector" (IDX:478); mode "official mode" / "Simulation mode · No device connected" (IDX:1835); chips "PLC communication is normal / ? Communication interruption / ? unknown / simulation", "RPi Connected / not connected / ? unknown / simulation" (IDX:1832-1833); operator button `<small>operator</small><b>name</b>` or "Please select" (IDX:484, 1839), title "Change operator".
- The brand area is the hidden maintenance entry: long-press 3 s (`HOLD_MS = 3000`, IDX:1065, 1356) -> `.brandbox { ... user-select:none; -webkit-user-select:none; touch-action:none; }` (IDX:65).

### 3.4 Alarm banner `.banner` (IDX:77-88, 416; template IDX:491-501)

Always present, 44 px. Content: `.pri` (boxed priority name) - `.time` - `.msg` (dotted underline, tap opens the full text) - `.more` ("Also N item") - button "confirm ACK" (if unacknowledged) - button "Alarm list". Class is `banner none` | `banner p1 unacked` | `banner p2 unacked` | `banner p3 unacked` | `banner acked` (IDX:1848-1854). Empty text: "No active alarm".

### 3.5 Navigation rail `.nav` (IDX:92-98; template IDX:505-509)

116 px wide column, buttons full width, `min-height:54px`, `font-size:16px`, left-aligned text, white with `--line` border; selected = solid `--run`, white bold text. Items (IDX:1055, 1858-1863): Overview, Schedule measurement, manual operation, Trend, execution record, Correction, alarm, then the maintenance entry `navmaint` ("🔧 Maintenance" when logged out, "Maintenance settings" when logged in; dashed border, `margin-top:auto`, 46 px), then "test page" only when enabled. Badge: 20 px square top-right, coloured by worst unacknowledged priority (`.badge.p1/.p2/.p3/.acked`); on "Correction" a "!" badge in `p2` when the flow waits for the user.

### 3.6 Content area: `.page`, `.panel`, `.ph` (IDX:100-107)

```css
  .page { flex:1; min-width:0; display:flex; flex-direction:column; gap:8px; padding:8px 10px; }
  .panel { background:var(--panel); border:1px solid var(--line); }
  .ph { display:flex; align-items:center; gap:10px; padding:6px 12px; border-bottom:1px solid var(--line); min-height:34px; }
  .ph h2 { margin:0; font-size:18px; font-family:inherit; font-weight:700; white-space:nowrap; }
  .ph .sub { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  html[lang="en"] .nm { font-size:12px; }
  .ph .sub { font-size:13px; color:var(--ink-3); }
  .ph .sp { flex:1; }
```

(Cross-check: lines IDX:104-105 — the ellipsis rule for `.ph .sub` and the English `.nm` rule — had been left out of this "verbatim" block; restored. Appendix A.4 was already complete.)

A panel = bordered box with an optional header row `.ph` (title `h2`, grey subtitle `.sub`, spacer `.sp`, then right-aligned controls). No card shadows, no rounded corners, no coloured headers.

### 3.7 Footer action bar `.foot` (IDX:386-402; template IDX:970-989)

`.foot { flex:0 0 70px; display:flex; align-items:center; gap:10px; padding:0 12px; border-top:1px solid var(--line); background:#fff; }`

- `.start` 140 x 54, 21px bold, solid `--run`; label "Start scheduling" / "Scheduled" / "is executing" / "continue RESUME" (then `.start.resume`, green `#2f7a4b`) (IDX:2104-2105).
- `.pause-btn` 120 x 54, 18px bold, white with 2 px `#b88a00` border, text `#6b4d00`; "pause PAUSE" / "Pausing...".
- `.stop` 120 x 54, 20px bold, white with 2 px `--ink` border; "Stop STOP".
- `.prog` flexible info area: pairs of `<small>label</small><b>value</b>` ("Schedule", "Next measurement" / "at present", "stage", "Not measured"), or "cannot start" + reason, or "message" + last message; `.hvtag` "⚡ contains high voltage" (2 px red outline tag).
- `.estop` 188 px box with 2 px `--p1` border: inline SVG (red disc `#c62828` r14 + yellow disc `#e0b400` r8) and the text "For emergency stop, please press on the device**Physical emergency stop button**"; `title="STOP is a software stop, not a safety level emergency stop."`. There is deliberately no software E-stop button (`README.md:45`).

### 3.8 Embedded sub-pages

`.test-extension` + `iframe.test-frame` (SHELL:5-6; IDX:512-520, 696-698): the calibration, trend and test pages are separate documents kept mounted and shown/hidden with `display:flex|none`. Inside, CAL and TREND use `.shell { height:100%; display:flex; flex-direction:column; padding:8px 10px; gap:8px (6px in TREND); position:relative; }` on a `#eef0f2` ground, i.e. they fill the 908 x 598 content area.

### 3.9 Grid

There is no grid system. Layout is nested flexbox with fixed pixel bases. CSS grid is used only for small matrices: `.kv` 3 columns (IDX:312), `.btn-grid` 2 columns (IDX:320), `.param-grid` 4 columns gap 10 (IDX:341), `.cal-grid` `120px repeat(3, 1fr)` gap 14 (IDX:347), `.sch-grid` 2 columns gap 8 (IDX:445), keypad `.keys` 3 columns gap 6 (IDX:427), `.pin-keys` `repeat(3, 104px)` gap 8 (IDX:465), `.dlg-choices.grid` 2 per row (IDX:438).

### 3.10 Page layouts (inside the 888 x 582 area)

| Page | Structure | Key sizes |
|---|---|---|
| Overview (IDX:523-636) | `.metals` (3 reading panels) / `.panel.steps` (phase strip) / `.panel.pid` (flow diagram) / `.panel.strip` (6 status cells) | metals row 84 px; `.pid-box` 880 x 300; strip 56 px |
| manual operation (IDX:639-693) | `.notice` on top; `.manual` = `.panel.pumps` (flex 1, one `.prow` per pump) + `.side` (330 px: syringe panel + "No device installed" panel) | `.prow` rows share the height; `.sec` 96 x 46; ON/OFF 64 x 46 |
| Schedule measurement (IDX:701-724) | `.sch-body` = `.panel.sch-left` (392 px: 2-column tile grid + estimate lines + warnings) + `.panel.sch-right.table` (slot log + pager) | tiles 80 px high |
| execution record (IDX:727-741) | one `.panel.table`: `.ph`, `.thead`, `.tbody`, `.pager` | rows 44 px, 9 per page |
| alarm (IDX:744-774) | one `.panel.table`; `.ph` holds `.seg.tabs` (alarm list (n) / operation event), priority counters, "Confirm all" | 8 alarms or 12 slim event rows per page |
| Maintenance settings locked (IDX:780-800) | `.panel.login` with `.pin-wrap` (dots, error line, PIN keys or keyboard, 3 buttons) | see 4.7 |
| Maintenance settings open (IDX:803-964) | `.adm-bar` (`.seg.tabs` + message chip + "Sign out") then per tab: Recipe = `.adm-left` 300 px list + `.adm-right` detail; RPi connection = two `.adm-half` (key/value list, button grid); operator = `.adm-full.table`; plasma parameters = `.param-grid` of value tiles; correction coefficient = `.cal-grid`; spectrum = `.spec-list` 250 px + `.spec-main` (controls + chart 620 x 308 or QC table) | admin tabs (IDX:1162): Recipe, RPi connection, plasma parameters, correction coefficient, spectrum, operator |

---

## 4. Components

Unless stated, CSS is quoted verbatim from IDX; the full block is in Appendix A.4.

### 4.1 Buttons

Base and primary (IDX:221-224):

```css
  .btn { height:40px; padding:0 14px; border:1px solid #8b8f94; background:#fff; font-size:16px; cursor:pointer; color:var(--ink); white-space:nowrap; }
  .btn:disabled { color:#a3a6aa; border-color:#d0d2d5; background:#f3f3f3; }
  .btn.primary { background:var(--run); border-color:var(--run); color:#fff; font-weight:700; }
  .btn.primary:disabled { background:#b9c6d3; border-color:#b9c6d3; color:#fff; }
```

Global button rules (IDX:37-39): `button { font-family:inherit; }`, `button:focus-visible { outline:3px solid var(--run); outline-offset:2px; }`, `button:disabled { cursor:not-allowed; }`. There are no `:hover` or `:active` styles in the live HMI (touch).

Effective `.btn` after the DS residue (derived, see section 7): additionally `display:inline-flex; align-items:center; justify-content:center; gap:6px; font-family:"Barlow Condensed"...; font-weight:600; line-height:1.2; border-radius:0; text-decoration:none`, and disabled also gets `opacity:.45`.

Variants (all square, white, 1 px `#8b8f94` unless noted):

| Class | Where | Look |
|---|---|---|
| `.btn` / `.btn.primary` | general | 40 px; primary = solid blue |
| `.start`, `.start.resume`, `.pause-btn`, `.stop` | footer | see 3.7; disabled START `#e4e6e8`, disabled STOP/PAUSE `#f3f3f3` with `#c9ccd0` border and normal weight |
| `.nav button`, `.sel`, `.navmaint` | nav | see 3.5 |
| `.seg button`, `.seg.tabs button` | segmented / tabs | 36 px (tabs 44 px), joined with `margin-left:-1px`, selected solid blue |
| `.gbtn`, `.gbtn.primary`, `.gbtn.hv` | maintenance action grid (IDX:320-331) | big 2-line button (label 17px bold + `small` 12px), 66-86 px high; `.hv` = red 3 px frame, red text, yellow/black hazard stripes 22 px wide on both sides, spans 2 columns |
| `.pbtn`, `.cbtn`, `.stile`, `.vol`, `.sec` | "value tiles" (tap to edit) | see 4.5 |
| `.ack` | in-table action (confirm / delete / Import) | 34 px, 15px bold |
| `.userbtn`, `.userbtn.admin`, `.opbtn`, `.opbtn.none` | top bar | 36 px / 44 px; `.admin` blue outline + blue bold text; `.opbtn.none b` in `#8a3d00` |
| `.lang button`, `.sel` | top bar | 36 x 34, selected `--ink-2` fill with white bold text |
| `.banner button` | banner | 34 px, translucent white, 15px bold |
| `.kb-key`, `.keys button`, `.pin-keys button`, `.dlg-choices button`, `.dlg-f .cancel` / `.ok` | dialogs | see 4.6-4.8 |
| `.hit` | invisible tap target over the flow diagram | transparent; `outline:2px solid var(--run)` when hovered/selected |
| `.file-btn` | `<label class="btn file-btn">Import CSV files<input type="file">` | label styled as button, input hidden |
| `button[data-unconnected]` (SHELL:3) | not-wired controls | `opacity:.65; cursor:not-allowed!important; pointer-events:none` |

CAL adds `.btn.danger { border-color:#c62828; color:#c62828; font-weight:700; }`, `.btn.big { height:56px; font-size:20px; padding:0 26px; }`, `.btn:disabled { opacity:.5; }` (CAL:15-21). TREND's `.btn` is 38 px / 15 px (TREND:13).

### 4.2 Segmented control and tabs (IDX:216-220, 281)

```css
  .seg { display:flex; }
  .seg button { height:36px; min-width:58px; padding:0 10px; border:1px solid var(--line); background:#fff; font-size:15px; cursor:pointer; margin-left:-1px; white-space:nowrap; }
  .seg button.sel { background:var(--run); color:#fff; border-color:var(--run); font-weight:700; position:relative; }
  .seg button:disabled { color:#9a9da1; }
  .seg button.sel:disabled { background:#8aa3bd; color:#fff; }
  .seg.tabs button { height:44px; font-size:16px; padding:0 16px; }
```

Used for: pump direction CW / CCW, syringe channel (two-line buttons 50 px, `.syr .seg button`, IDX:241), alarm tabs, maintenance tabs, spectrum view / mode / range. CAL uses separate `.tab` buttons with a 6 px gap (CAL:27-29); TREND uses `.seg .btn + .btn { border-left:0; }` (TREND:15-17).

### 4.3 Status pills, tags, chips, badges

```css
  .chip { display:flex; align-items:center; gap:6px; font-size:14px; padding:5px 9px; border:1px solid var(--line); white-space:nowrap; background:#fff; }
  .chip i { width:11px; height:11px; display:block; background:var(--ink-3); }
  .chip.q-ok i { background:#3d7a52; }
  .chip.q-bad { color:var(--bad); border:1px dashed var(--bad); background:var(--bad-bg); font-weight:700; }
  .chip.q-bad i { background:var(--bad); }
  .chip.q-na { color:var(--ink-3); border-style:dashed; }
```

```css
  .q-ok { color:var(--ink); }
  .q-run { color:var(--run); font-weight:700; }
  .q-bad { color:var(--bad); font-weight:700; }
  .q-na { color:var(--ink-3); }
  .q-uncal { color:var(--ink-2); }
  .q-cal { color:#3d7a52; }
  .tag { display:inline-block; font-size:13px; padding:2px 7px; border:1px solid var(--line); white-space:nowrap; }
  .tag.q-run { background:var(--run); color:#fff; border-color:var(--run); }
  .tag.q-bad { border:1px dashed var(--bad); background:var(--bad-bg); }
  .tag.q-na { border-style:dashed; }
  .tag.q-uncal { background:repeating-linear-gradient(135deg,#e2e2e2 0 4px,#f6f6f6 4px 8px); border-color:#9a9a9a; }
```

plus (IDX:130-132) `.tag.q-cal { background:#fff; border-color:#3d7a52; color:#3d7a52; }`, `.tag.q-blank { background:#fff4d6; border-color:#c99a2e; color:#7a5200; }`, `.tag.q-blankbad { background:var(--bad-bg); border:1px dashed var(--bad); color:var(--bad); font-weight:700; }`.

Other pills:
- Priority `.pri` (IDX:265-266): 13px bold white on `--p1` / `--p2`; `--p3` with `#111` text.
- Run status `.st` (IDX:267-268, 460): outlined 13px; `.completed` green outline, `.failed` solid orange, `.running` solid blue, `.skipped` dashed amber on `#fff7d6`, `.stopped` grey text.
- Counter `.count` (IDX:271-273): white pill with a 10 px colour square.
- Mode `.mode` / `.mode.demo` (IDX:50-51): outlined 14px; demo = amber diagonal stripes, bold.
- Nav `.badge` (IDX:97-98): 20 px square, white bold 13px.
- `.hvtag` (IDX:461): 2 px red outline, red bold 14px.
- `.msgchip` (IDX:418-421): 40 px one-line message button in the maintenance bar; `.err` (2 px orange frame, `#8a3d00` on `#fff4ea`, bold), `.ok`, `.busy` (blue text).
- CAL `.st` (CAL:92-96) and round `.badge` (CAL:30-31); TREND `.view-chip` (TREND:54-55).

### 4.4 Tables, lists, pager, empty state, key/value

Tables are flex rows, not `<table>` (IDX:253-264):

```css
  .table { flex:1; min-height:0; display:flex; flex-direction:column; }
  .thead, .trow { display:flex; align-items:center; gap:8px; padding:0 12px; }
  .thead { flex:0 0 34px; font-size:13px; color:var(--ink-3); border-bottom:2px solid var(--ink-2); }
  .tbody { flex:1; min-height:0; overflow:hidden; }
  .trow { min-height:44px; border-bottom:1px solid #dcdee0; font-size:15px; }
  .trow.unacked { background:#fff6e5; }
```

Columns are fixed-basis cells `c-*` (IDX:259-264, 319, 380, 383, 456-459), e.g. `.c-time { flex:0 0 126px; font-family:var(--font-heading); font-size:16px; }`, `.c-msg { flex:1; min-width:0; }`, `.c-num { flex:0 0 96px; text-align:right; font-family:var(--font-heading); font-size:14px; }`. Variants: `.trow.slim` 32 px / 13px; selected row `#dce9f6`; flagged row `#fff6e5`.

Pager (IDX:276-278): `.pager { flex:0 0 auto; display:flex; align-items:center; justify-content:center; gap:14px; padding:6px 8px; border-top:1px solid #dcdee0; }`, buttons "◀ Previous page" / "Next page ▶" (or just "◀" / "▶" in narrow lists) and `<b>` "No. N / M Page" (IDX:1342).

Selectable list (IDX:282-287): `.li` full-width button rows 58 px, bold 16px title + 13px grey sub line; `.li.sel` solid blue with white text.

Empty state (IDX:250-252): centred `.empty` with `<b>` 21px title and a `<p>` 15px (max-width 560), optionally a button.

Key/value (IDX:312-318): `.kv` = 3-column grid of `<div><span>label 12px grey</span><b>value 15px</b></div>`; `.kv.big` = one row per item with dotted dividers (label left 15px, value right 17px). Dialog lines `.dlg-line` and faceplate rows `.face-row` use the same "grey label left, value right, dotted rule" pattern.

Status strip (IDX:198-203): `.strip` 56 px row of `.cell` (12px grey label over 17px bold value), separated by 1 px rules.

`<table>` is used only in CAL (`.tbl`, CAL:108-112: 13px, 1 px `#dfe1e3` cell borders, right-aligned tabular numbers, header `#f2f3f4`), TEST and LITE.

### 4.5 Form fields = tap-to-edit value tiles (no native inputs in the shell)

The shell contains exactly one native input (the hidden file input). Every setting is a button that opens the keypad / keyboard / choice dialog:

- `.pbtn` parameter tile (IDX:342-346): 112 px high, `<span>` label 14px, `<b>` value 26px Barlow Condensed, `<small>` range 12px; `.changed` = 2 px blue frame on `--run-bg`; `.local` dashed border; `.local.warn` 2 px dashed magenta.
- `.cbtn` calibration cell (IDX:349-350): 78 px, 24px Barlow Condensed; `.empty` grey 18px; `.changed` as above.
- `.stile` schedule tile (IDX:446-451): 80 px, label 13px / value 21px bold / sub 12px; `.need` 2 px dashed amber (must be chosen), `.hv` red frame + hazard stripe on the left, `:disabled` `#f6f6f6`.
- `.vol` (IDX:243-244): value button 46 px, 24px Barlow Condensed + unit; with `−` / `+` 48 x 46 stepper buttons beside it.
- `.sec` (IDX:231-235): 96 x 46 two-line button (value + "automatically stop after"); `.run` = 2 px blue frame on `--run-bg` showing the countdown "left N Second / Stopping automatically".

Native inputs appear only in the side pages:
- CAL (CAL:49-52, 57): `.lvl input { width:100%; height:38px; border:1px solid #8b8f94; padding:0 8px; font-size:16px; background:#fff; }`, `.num` right-aligned, `.bad` magenta border + `--bad-bg`; `select` 40 px / 16px; `.stepper` (−, value 20px, ＋).
- TREND (TREND:38, 45, 53): `.dlg input { width:120px; height:36px; border:1px solid #8b8f94; font-size:16px; text-align:right; padding:0 8px; }`, checkboxes 22 x 22.
- WF (WF:23): `input, select {height:40px;padding:0 10px;font:15px ...;border:1px solid #8b8f94}`; focus `outline:3px solid var(--run);outline-offset:1px` (WF:19).
- DS `.input`, `.radio`, `.seg-opt`, `.field > label` (DS:165-203) exist but are not used by any live page.

### 4.6 Numeric keypad (dialog body) (IDX:423-428; template IDX:1019-1023; logic IDX:1686-1704, 2171-2178)

```css
  .dlg-buf { height:54px; border:2px solid var(--run); display:flex; align-items:center; justify-content:flex-end; gap:6px; padding:0 12px; font-family:var(--font-heading); font-size:32px; }
  .dlg-buf small { font-size:16px; color:var(--ink-3); font-family:var(--font-body); }
  .dlg-range { font-size:14px; color:var(--ink-3); display:flex; justify-content:space-between; }
  .dlg-range .err { color:var(--p2); font-weight:700; }
  .keys { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }
  .keys button { height:52px; font-size:24px; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
```

- Structure: title bar, optional context lines, right-aligned buffer with unit, range line (left: allowed range, right: error), 3-column key grid, optional warning box, footer Cancel / confirm.
- Key order (IDX:2172-2177): `7 8 9 / 4 5 6 / 1 2 3 / C 0 ⌫` for integers; with decimals `... / . 0 ⌫ / C`; with decimals and sign `... / . 0 ⌫ / C ±` (the only signed use is the calibration coefficient, IDX:2369).
- Buffer shows `0` when empty; max length 8 unless `maxLen` given (IDX:1703). Range text (IDX:2174): "range {min}–{max},integer" or "range {min}–{max},decimal {n} Bit"; error text "is out of range or the format does not match"; the confirm button is disabled until the value is valid.
- PIN variant (`pad.pin`): buffer shows one "●" per digit, range text "Please enter 6–8 digital repair PIN", valid when `/^\d{6,8}$/` (IDX:1687).
- Time entry is two keypad dialogs in a row ("…: hour" 0-23 then "…: point" 0-59) (IDX:1435-1441).

### 4.7 Maintenance PIN login (the "operator password" screen) (IDX:296-300, 463-467; template IDX:780-800; logic IDX:2188-2207)

```css
  .pin-wrap { flex:1; display:flex; flex-direction:column; align-items:center; gap:8px; padding:14px; }
  .pin-dots { width:360px; height:56px; border:2px solid var(--run); display:flex; align-items:center; justify-content:center; font-size:30px; letter-spacing:8px; background:#fff; }
  .pin-keys { display:grid; grid-template-columns:repeat(3, 104px); gap:8px; }
  .pin-keys button { height:62px; font-size:26px; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .pin-foot { display:flex; gap:10px; } .pin-foot .btn { height:52px; min-width:120px; font-size:17px; }
  .login-err { min-height:22px; padding:2px 16px; color:#8a3d00; font-weight:700; font-size:14px; }
```

- Panel header: `<h2>Maintenance login</h2>` + sub "calibration, spectral raw data, plasma parameters,Recipe, RPi with operator list for maintenance personnel only".
- `.pin-dots` shows "●" per typed character. Keys (IDX:2197): `1 2 3 / 4 5 6 / 7 8 9 / Clear 0 ⌫`; at most 8 digits in PIN mode.
- Footer buttons: "Return", a toggle "English keyboard" / "Numeric keys" (switches the PIN grid for the on-screen keyboard of 4.8, for old alphanumeric passwords), and primary "log in" (disabled while empty). Messages: "Logging in…", "Login failed:…".
- Not-configured state (IDX:783-785): empty-state block "has not been set for maintenance PIN" / "Please execute on the computer Set-Admin-Password.cmd settings 6–8 Digital repair PIN , restart HMI." / button "Return to overview".
- Entry points: long-press on the brand area for 3 s, the dashed nav button "🔧 Maintenance", or URL hash `#admin` (IDX:1270-1273). When logged in the top bar shows the blue-outlined "maintenance mode" button and the nav entry reads "Maintenance settings"; the maintenance bar has "Sign out".
- A second PIN prompt (keypad dialog, danger style) guards deletion: title "input repair PIN to delete" (IDX:1480-1481).

### 4.8 On-screen keyboard (IDX:290-295, 297-299; logic IDX:1191-1207)

```css
  .kb { display:flex; flex-direction:column; gap:6px; padding:8px 12px 12px; }
  .kb-row { display:flex; gap:6px; justify-content:center; }
  .kb-key { width:56px; height:50px; font-size:20px; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .kb-key.mid { width:92px; font-size:16px; } .kb-key.wide { width:210px; font-size:16px; }
  .kb-key.on { background:var(--run); color:#fff; border-color:var(--run); }
  .dialog.wide { width:720px; }
```

Rows (IDX:1191-1193): `1234567890` / `qwertyuiop` / `asdfghjkl-` / `⇧ zxcvbnm _ .` / `@ # ! Blank ⌫ Clear`. `Blank` is `.wide`; `⇧ ⌫ Clear` are `.mid`; active shift is `.on`. The text buffer is `.login-buf` (52 px, 2 px blue frame, 22px Consolas, letter-spacing 1 px) with an optional "show" / "hide" button for masked input ("•" per character).

### 4.9 Dialogs (IDX:405-439, 333-340; template IDX:1010-1038; logic IDX:1657-1709, 2143-2185)

```css
  .overlay { position:absolute; inset:0; background:rgba(20,22,24,.45); z-index:40; }
  .dialog { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); width:440px; z-index:41; background:#fff; border:2px solid var(--ink); box-shadow:0 12px 40px rgba(0,0,0,.35); display:flex; flex-direction:column; }
  .dialog.danger { border-color:var(--p2); }
  .dlg-h { padding:12px 16px; font-size:20px; font-weight:700; border-bottom:1px solid var(--line); }
  .dialog.danger .dlg-h { background:#fff1e3; }
  .dlg-b { padding:10px 16px; display:flex; flex-direction:column; gap:6px; }
  .dlg-line { display:flex; justify-content:space-between; gap:12px; font-size:16px; padding:3px 0; border-bottom:1px dotted #d0d2d5; }
  .dlg-line > span { color:var(--ink-3); } .dlg-line b { text-align:right; }
  .dlg-warn { margin-top:6px; padding:8px 10px; font-size:14px; line-height:1.5; background:#fff7d6; border-left:5px solid var(--p3); overflow-wrap:anywhere; }
  .dlg-detail { margin-top:4px; padding:10px 12px; font-size:15px; line-height:1.55; background:#f6f6f7; border:1px solid var(--line); overflow-wrap:anywhere; white-space:pre-wrap; max-height:330px; overflow:hidden; }
  .dlg-detail.err { background:#fff4ea; border-left:5px solid var(--p2); }
```

```css
  .dlg-f { display:flex; gap:10px; padding:12px 16px; border-top:1px solid var(--line); }
  .dlg-f button { flex:1; height:52px; font-size:18px; cursor:pointer; }
  .dlg-f .cancel { background:#fff; border:1px solid #8b8f94; }
  .dlg-f .ok { background:var(--run); border:1px solid var(--run); color:#fff; font-weight:700; }
  .dialog.danger .dlg-f .ok { background:var(--p2); border-color:var(--p2); }
  .dlg-f .ok:disabled { background:#c9ccd0; border-color:#c9ccd0; }
  .dlg-timeout { font-size:12px; color:var(--ink-3); padding:0 16px 8px; }
```

- One dialog component with optional parts, in this order: title `.dlg-h`; body `.dlg-b` = key/value lines `.dlg-line`, choices `.dlg-choices`, keypad, keyboard, detail text `.dlg-detail`, warning `.dlg-warn`, acknowledgement rows `.ack-row`; footer `.dlg-f` (Cancel + confirm, equal width); timeout note `.dlg-timeout`.
- Class composition (IDX:2153): `'dialog' + (danger ? ' danger' : '') + (text || choices ? ' wide' : '') + (hv ? ' hv' : '')`. Widths: 440 default, 720 `.wide`, 580 `.danger.hv`.
- Danger = orange frame, pale orange title bar, orange confirm button. High-voltage = `.dialog.danger.hv`: 3 px red frame, red title bar with white text, red confirm button (IDX:333-336), title prefixed "⚠".
- Acknowledgement rows (IDX:337-340, 439): full-width 54 px buttons with a 2 px red frame and a 28 px tick box; ticked = `#fdecec` fill and solid red box with a white "✓". The confirm button stays disabled until every row is ticked.
- Choice list (IDX:429-430, 438): `.dlg-choices.grid`, two 52 px buttons per row, 8 per page, selected solid blue; extra "◀ Previous page" / "Next page ▶" buttons page inside the dialog.
- Auto-cancel: `DIALOG_MS = 30000` (IDX:1065); note text "{n} seconds, it will be automatically canceled and no command will be sent." (IDX:2157); on timeout the message "The confirmation window timed out and was canceled without sending the command.". Message-only dialogs (`showMessage`, IDX:1659-1663) use 120 s, a single "closure" button and no timeout note unless a cancel button is requested. Tapping the scrim or pressing Escape cancels (IDX:1011, 1262-1265).
- Default confirm label "Sure", cancel "Cancel".
- Side-page dialogs are simpler: scrim `.dialog` (absolute, `rgba(20,22,24,.45)`, centred flex) containing `.dlg` (white, 1 px `--line`, padding 16px 18px, width 520 in CAL / 440 or 560 in TREND), `h3` 18px, right-aligned footer `.dlg-foot` with `.btn` min-width 100-110 (CAL:118-125, TREND:34-43). WF uses the native `<dialog>` with an orange 2 px frame (WF:45-51).
- Derived, not visually verified: through DS residue the live `.dialog` also has `padding:13.6px` and `gap:10.2px` (section 7).

### 4.10 Notices, banners, alarms

Inline notice (IDX:206-213):

```css
  .notice { flex:0 0 auto; display:flex; align-items:center; gap:10px; padding:8px 12px; font-size:15px; border:1px solid var(--line); background:#fff; }
  .notice.warn { border-left:6px solid var(--p3); }
  .notice.block { border-left:6px solid var(--ink-3); color:var(--ink-2); }
  .notice.ok { border-left:6px solid var(--run); }
  .notice.err { border-left:6px solid var(--p2); color:#8a3d00; background:#fff4ea; }
  .notice .n-body { flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
  .notice .n-fix { font-size:14px; color:var(--ink); font-weight:400; }
  .notice .btn { height:40px; flex:0 0 auto; }
```

A notice is one white bar with a 6 px coloured left edge: yellow = caution, grey = blocked, blue = information/accepted, orange = error (with a "how to fix" line and an optional jump button).

Alarm banner (IDX:77-88):

```css
  .banner { flex:0 0 44px; display:flex; align-items:center; gap:10px; padding:0 12px; font-size:16px; border-bottom:1px solid var(--line); }
  .banner.none { background:#eceded; color:var(--ink-3); font-size:15px; }
  .banner.p1 { background:var(--p1); color:#fff; }
  .banner.p2 { background:var(--p2); color:#fff; }
  .banner.p3 { background:var(--p3); color:#111; }
  .banner.acked { background:#fff; color:var(--ink); }
  .banner .pri { font-weight:700; padding:3px 9px; border:2px solid currentColor; white-space:nowrap; }
  .banner.unacked .pri { animation:blink 1s steps(1) infinite; }
```

Alarm conventions: three priorities with name + colour (Emergency red, high orange, middle yellow); unacknowledged = full-colour banner with a blinking priority box and a pale orange table row; acknowledged = white banner; cleared-but-unacknowledged shows " · Restored". On the flow diagram alarms are marked with shape + colour + digit (`.amark`, IDX:177-183): p1 = red square, p2 = orange diamond (square rotated 45 deg), p3 = yellow triangle, each containing the priority number.

Other message boxes: `.dlg-warn`, `.sch-warn` (yellow left bar on `#fff7d6`), `.sch-err`, `.dlg-detail.err` (orange left bar on `#fff4ea`), CAL `.warnings div`, `.error` (magenta dashed, CAL:64-65), CAL `.prompt` (CAL:68-71: `#fff4d6` box with a 2 px orange frame, 19px bold text and a big primary button, for "Waiting for fluid change"), TREND `.notice` (TREND:23).

Toast: only in the side pages. CAL `.toast` (CAL:126-127): bottom-centre, `#2b2e31`, white 14.5px, padding 10px 16px. TEST `#toast`: bottom-right, `#3b4249`. The shell has no toast; it shows the last message in the footer `.prog` (fades after 12 s unless it is an error, IDX:1291) or in the maintenance `.msgchip`.

### 4.11 Progress and state indicators

- Phase strip (IDX:135-145, 394): `.steps-row` of equal `.step` boxes 36 px (`<span class="no">n</span>label`); `.done` grey `#e4e8ec`, `.cur` solid blue bold, `.fail` solid orange, `.pause` amber `#ffe9a8`. Right of the title, `.meta` text such as "Schedule No. 2/6 times · steps 5/14".
- Footer `.prog` text columns (3.7); countdown text "HH:MM (MM:SS) ".
- Countdown on the pump's `.sec.run` button; `.vbox` value box next to a running pump in the diagram (IDX:175-176).
- CAL run view (CAL:73-82): `.run-lvl` cards (`.now` 2 px blue on `--run-bg`, `.done` green border), and a `.run-log` list with a time column.
- There are no progress bars and no spinners anywhere. "Busy" is text only ("Processing…", "Logging in…", "…middle…").

### 4.12 Charts

Spectrum chart in the shell (IDX:362-376; template IDX:922-935; builder IDX:1084-1158):

```css
  .chart { position:relative; width:620px; height:308px; margin:2px 0 0 8px; }
  .chart svg { position:absolute; left:52px; top:4px; background:#fff; border:1px solid #c9ccd0; }
  .chart .ytk { position:absolute; left:0; width:48px; text-align:right; font-size:11px; color:var(--ink-3); transform:translateY(-50%); }
  .chart .xtk { position:absolute; top:291px; font-size:11px; color:var(--ink-3); transform:translateX(-50%); }
  .chart .unit { position:absolute; left:14px; top:291px; font-size:11px; color:var(--ink-3); }
  .chart .lgd { position:absolute; right:8px; top:8px; font-size:12px; color:var(--ink-2); background:rgba(255,255,255,.9); padding:2px 6px; border:1px solid #dcdee0; }
  .chart .ref { position:absolute; top:6px; font-size:11px; font-weight:700; color:#8a5b00; transform:translateX(4px); }
  .chart .msg { position:absolute; left:52px; top:4px; width:566px; height:282px; display:flex; align-items:center; justify-content:center; color:var(--ink-3); font-size:15px; }
  .sl-mean { stroke:var(--ink); stroke-width:1.6; fill:none; }
  .sl-frame { stroke:var(--run); stroke-width:1.3; fill:none; }
  .sl-over { stroke:#8fb0d0; stroke-width:.8; fill:none; opacity:.8; }
  .sl-over.sel { stroke:var(--run); stroke-width:1.6; opacity:1; }
  .sl-zero { stroke:#b9bcc0; stroke-width:1; stroke-dasharray:3 3; }
  .sl-grid { stroke:#eceded; stroke-width:1; }
  .sl-ref { stroke:#8a5b00; stroke-width:1; stroke-dasharray:4 3; }
```

- Plain inline SVG 566 x 282 (`CHART_W`, `CHART_H`), placed at 52,4 (`CHART_X0`, `CHART_Y0`); white plot with a hairline frame; horizontal grid only; tick labels are HTML `div`s (11px grey) outside the SVG; unit "nm" bottom-left; legend box top-right as a sentence ("black: average N Zhang (button background)", "blue: No. n Zhang (button background)", "Light Blue: Each · Blue: No. n Zhang · Black: Average (all backgrounds are deducted)", IDX:2427-2429); element reference lines dashed brown with labels "Cu 327.28", "Ni 231.66".
- Ticks: `niceTicks(min,max,5)` for y and `(…,6)` for x with 1-2-5 steps; values >= 10000 shown as "12.3k" (IDX:1087-1097). 6 % vertical padding. Long spectra are min/max decimated to about 1.5 points per pixel (IDX:1137-1148).
- Controls above the chart are `.seg` groups: view "Spectrum / Single sheet inspection", mode "average / leaflet / All overlays", range "full spectrum / Cu 327 nm / Ni 232 nm" (IDX:1082, 2417-2423), and a frame navigator `.fnav` ("◀ No. n / N open ▶").
- Trend charts (TREND:24-30 plus colours grep'd from `web/trend/app.js:5,96-129`): stacked full-width SVG charts in `.chart` panels; plot rect white with `#b9bcc0` stroke; grid `#eceded`; zero line `#9a9da1`; sample series `#2f5f8f` solid 2 px with filled circles r 3.5; blank series `#c27a00` dashed `6 4` with white diamonds (stroke `#c27a00`), out-of-limit blanks filled magenta `#a3168e`; tolerance band `rgba(194,122,0,.12)`; text 11px grey, chart title 13px bold. Legend swatches are CSS (`.lg-sample`, `.lg-blank`, `.lg-bad`).
- Calibration fit chart (CAL:103; `web/calibration/app.js:334-344`): SVG 360 x 240, frame `#b9bcc0`, grid `#eceded`, fit line `#2f5f8f` 2 px, points r 4 `#e06a00` with white stroke.
- TEST and LITE draw on `<canvas>`; their drawing colours are in JS that was not read.

### 4.13 Flow diagram (P&ID) and faceplate (IDX:147-196; template IDX:546-628, 992-1007)

- Comment IDX:147: "flow path diagram:ISA-101 High-performance graphics. Mainly gray scale, line width and solid/Hollow indicates status without animation."
- `.pid-wrap` grey `#e6e7e9`; `.pid-box` 880 x 300 holding an SVG `viewBox="0 0 880 300"` plus absolutely positioned HTML overlays.
- Symbols: pipes `.ln` (`#a4a7ab`, 2.5 px; `.thin` 1.8; `.air` dashed `9 5`; `.act` = `--ink` 4.5 px) with arrowheads `.ah`; vessels/equipment `.eq` (`#3a3d40` 2 px, fill `#f4f4f5`; `.act` 3.5 px); pumps `.pmp` = circle r 18 + tangent outlet line (running = solid `--ink` fill with white tag; comm failure = magenta dashed; N/A = grey dashed); tag text `.ptag` 14px bold, names `.nm` 15px bold, tag numbers `.tg` 12px grey.
- Overlays: hatched analog bar `.bar` for "sensor not connected"; state text `.pst` (88 px wide, 13px, centred) under idle pumps; value box `.vbox` (white, 1.5 px ink frame) next to running pumps; alarm markers `.amark`; round invisible `.hit` buttons 44 x 44 over each pump.
- Legend in the panel header (IDX:549): "current path", "operation(command)", "Stop", "communication failure", "not connected" with CSS swatches `.lg-act/.lg-run/.lg-off/.lg-bad/.lg-na`.
- Faceplate `.face`: 268 px wide popup at `right:14px; top:112px`, white, 2 px ink frame, header bar `#3a3d40` with white title and a 32 px "✕" button, dotted key/value rows, a note, and 46 px buttons (ON / OFF / jump to page).

---

## 5. Iconography, logo, branding hooks

- **No icon font and no image assets.** Icons are (a) Unicode characters in labels: ◀ ▶ ✕ ⚠ ⚡ 🔧 ✓ ⇧ ⌫ ● • − + ＋ ±; (b) CSS shapes: 11 px status squares in chips, 10 px squares in counters, alarm markers (square / diamond / triangle), legend swatches, hatch patterns; (c) inline SVG: the E-stop symbol (IDX:986), the P&ID symbols.
- The legacy MANUALFRAG has three 24-26 px outline icons (stroke 1.5, colour bound to state): pump `<circle cx="12" cy="12" r="8"/><path d="M9 8l7 4-7 4z"/>` (MANUALFRAG:21), valve `<path d="M4 7l8 5-8 5z"/><path d="M20 7l-8 5 8 5z"/><path d="M12 12V5M9 4h6"/>` (MANUALFRAG:42), mixer `<path d="M5 6h14v10a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z"/><path d="M12 6v8M9 14h6"/>` (MANUALFRAG:80).
- Document title: `<title>HM detector HMI</title>` (IDX:5). No favicon reference in IDX.
- Branding hook (IDX:1268-1269, 2512-2518; `web/custom/branding.example.json`): optional `web/custom/branding.json` with `company`, `logo` (file in the same folder: png / jpg / svg / webp), `product_name` (string, or object keyed `zh-TW` / `zh-CN` / `en`). Rendering: `img.brand-logo { height:38px; max-width:150px; object-fit:contain; }` (IDX:48) and `.title.branded { font-size:17px; line-height:1.1; white-space:normal; max-width:200px; ... -webkit-line-clamp:2; }` (IDX:49). Without the file: text title "online metal wastewater detector" in `.title` (22px Barlow Condensed 600), no logo. The example says: "Top bar display height 38 px, widest 150 px; recommends transparent background and original image height 76 px above".
- Text override hook (`web/custom/texts.example.json`, I18N:445-455, 513-519): optional `web/custom/texts.json` = `{ "zh-TW": {original Traditional Chinese text: replacement}, "zh-CN": {...}, "en": {...} }`; keys must equal the whole on-screen text.
- TEST page brand block (TEST index:10): boxed `<b>HM</b>` + "online metal wastewater detector" + small caps line "HEAVY METAL ANALYZER · HMI v1".
- Demo/simulation marking: amber 5 px inner ring around the whole frame (`.hmi.demo`) + striped chip "Simulation mode · No device connected".

---

## 6. Themes and language

- **Theme: light only.** No dark variant, no `prefers-color-scheme` query, no theme switch in any file read. (DS:59-60 mentions dark elevation in a comment but defines only light tokens. SUPPORT:1364-1383 has light/dark canvas colours for the design tool's preview mode, not for the HMI.) LITE is a separate always-dark page (`color-scheme:dark`).
- **Language switch**: three buttons "Traditional", "simple", "EN" in the top bar (`.lang`, titles "Traditional Chinese", "Simplified Chinese", "English"; IDX:486, 2520-2525).
  - Mechanism (I18N): the UI is authored in Traditional Chinese; `web/i18n.js` rewrites text nodes and `title` / `aria-label` attributes at display time with a `MutationObserver` (I18N:457-512). English = dictionary `EN` + regex rules; Simplified = word table + per-character map. No reload needed.
  - Persistence `localStorage['hmi_lang']`; URL `?lang=en|zh-CN|zh-TW` forces and stores it (I18N:13, 440-444). Sets `<html lang>` to `en` / `zh-Hans` / `zh-Hant` (I18N:494).
  - API: `window.hmiI18n = { lang, languages, setLang(next), t(text) }` (I18N:521-531).
  - Opt-out: any element with `data-notr` (user data such as recipe names, operator names) is not translated (I18N:460-461).
  - If the script failed to load the buttons show the error "language file i18n.js is not loaded and cannot switch languages; please refresh or notify maintenance personnel" (IDX:2525).
  - The embedded pages (calibration, trend, test, workflow) do not reference `i18n.js` (no match for `i18n` / `hmiI18n` / `hmi_lang` in their js/html), so they stay in Traditional Chinese.
  - Only one CSS rule depends on language: `html[lang="en"] .nm { font-size:12px; }`.

---

## 7. Cascade facts a rebuild must know

### 7.1 DS residue in the live shell (derived from the stylesheet order in 1.1; not verified on a running screen)

| Live class | DS declarations that still apply (not overridden by the inline block) | Source |
|---|---|---|
| everything | `*, *::before, *::after { box-sizing: border-box; }`; `body { margin:0; font-size:15px; line-height:1.55; font-weight:400; }`; `:focus { outline:none; }`; `:focus-visible { outline:2px solid var(--color-accent); outline-offset:2px; }` (buttons override with the 3 px blue ring); `::selection`; `img { display:block; max-width:100%; }`; `p { margin:0 0 10.2px; }` | DS:107-131 |
| `.btn` | `display:inline-flex; align-items:center; justify-content:center; gap:6px; text-decoration:none; font-family:var(--font-heading); font-weight:600; line-height:1.2; border-radius:0`; `.btn:disabled { opacity:0.45; cursor:not-allowed; }` | DS:140-151, 281 |
| `.seg` | `overflow:hidden; border:1px solid var(--color-divider); border-radius:0` | DS:192-195, 281 |
| `.tag` | `letter-spacing:0.02em; border-radius:0` | DS:225-229, 281 |
| `.dialog` | `gap:var(--space-3)` (10.2 px); `padding:var(--space-4)` (13.6 px); `border-radius:0` | DS:267-271, 281 |
| `.table` | `width:100%; font-size:14px` | DS:249 |
| `.nav` | `align-items:center` (no visible effect: buttons are `width:100%`) | DS:236-240 |
| `.ph h2` | `line-height:1.12; letter-spacing:-0.015em` | DS:109-112 |

The most visible consequences: `.btn` labels are Barlow Condensed semibold while other buttons (nav, tabs, keypad, tiles, START/STOP) are Barlow; disabled `.btn` is additionally 45 % transparent; the dialog content is inset 13.6 px from its frame with 10.2 px between header, body and footer.

Starter for a plain rebuild (DERIVED, not verbatim; put it before the verbatim block of Appendix A.4 and drop the DS file):

```css
:root{
  --color-bg:#f2f2f3;
  --font-heading:"Barlow Condensed",system-ui,sans-serif;
  --font-body:"Barlow",system-ui,sans-serif;
}
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;min-width:1024px;min-height:768px;background:#d9dadc}
body{color:#1d1f20;font-family:var(--font-body);font-size:15px;line-height:1.55;font-weight:400}
h1,h2,h3,h4,h5,h6{line-height:1.12;letter-spacing:-0.015em}
p{margin:0 0 10.2px}
img{display:block;max-width:100%}
:focus{outline:none}
:focus-visible{outline:2px solid #5980a6;outline-offset:2px}
::selection{background:rgba(89,128,166,.3)}
/* residue the live classes inherit from the design system */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;text-decoration:none;
     font-family:var(--font-heading);font-weight:600;line-height:1.2;border-radius:0}
.btn:disabled{opacity:.45;cursor:not-allowed}
.seg{overflow:hidden;border:1px solid rgba(29,31,32,.16)}
.tag{letter-spacing:.02em}
.dialog{gap:10.2px;padding:13.6px}
.table{width:100%;font-size:14px}
```

### 7.2 Class names that mean different things in different documents

Today these are isolated by iframes. If the merged program becomes one document they collide:

| Class | Shell (IDX) | CAL | TREND |
|---|---|---|---|
| `.dialog` | the dialog box itself (IDX:406) | the full-screen scrim; the box is `.dlg` (CAL:118-120) | scrim; box `.dlg` (TREND:34-35) |
| `.btn` | 40 px / 16px | 40 px / 16px, `:disabled{opacity:.5}` | 38 px / 15px |
| `.ph` / `.ph h2` | min-height 34 / 18px | min-height 44 / 17px | n/a |
| `.panel` | block | `display:flex; flex-direction:column; min-height:0` | n/a |
| `.side` | 330 px | 320 px | n/a |
| `.c-num` | 96 px, right, Barlow Condensed 14px | 92 px, centred | n/a |
| `.badge` | 20 px square in nav | 20 px round, orange | n/a |
| `.st` | 13px, padding 2px 8px | 12px, padding 1px 6px, different state names | n/a |
| `.bar` | hatched level bar in the flow diagram | n/a | the toolbar row |
| `.chart` | fixed 620 x 308 box | n/a | flexible panel |
| `.legend`, `.notice`, `.empty`, `.foot`, `.seg`, `.error`, `.hint`, `.sub`, `.sp`, `.shell` | differ in details | | |

Inside IDX itself `.nm` is both the SVG equipment name (IDX:171) and the pump-row name block (IDX:227-229).

---

## 8. Legacy and side pages

### 8.1 ADMINFRAG and MANUALFRAG: the first-generation "blueprint" look

Template fragments with inline styles only (no class CSS besides `.blueprint` / `.corner` from DS:73-95). `README.md:61`: they are used only by `prepare_original_ui.py`. Their visual language differs from the live shell:

- Panels are `.blueprint` wireframes: 1 px `--color-divider` border, transparent background, four `.corner` registration crosses (11 px) just outside the corners.
- Buttons are transparent with a 1 px divider border, Barlow Condensed 17-19px; the "on"/primary action uses `background:var(--color-accent-100); border:1px solid var(--color-accent)` (ON button, MANUALFRAG:30) or solid `--color-accent` with `--color-accent-700` border and white text (SAVE, ADMINFRAG:85). Hover styles are given as `style-hover="border-color:var(--color-accent); background:var(--color-accent-100)"`.
- Bilingual labels and uppercase English sub-labels with `letter-spacing:0.08em-0.09em`: "peristaltic pump PUMPS · 40008–40012", "solenoid valve VALVES · 40016 / 40017 / 40033", "syringe pump SYRINGE · 40001–40007", "Second SEC", "Liquid extraction ASPIRATE", "Liquid injection DISPENSE", "RESET", "New account ADD USER", "restore REVERT", "Storage SAVE".
- Caution colour `oklch(0.62 0.10 78)` (amber) for the notice bar and the RESET border (MANUALFRAG:4-5, 77).
- Muted text via `color-mix(in srgb, var(--color-text) 68-80%, transparent)`.
- Manual page layout (MANUALFRAG): notice bar 34 px; left panel with one row per pump (icon 26 px, name 19px, register line 12px, seconds, ON 48 x 46, OFF 48 x 46); right column 318 px with a **valve panel** (one row per valve: icon, name, register, one 88 x 44 state button, MANUALFRAG:37-50) and a syringe panel (channel buttons 44 px, `−` value `+`, ASPIRATE / DISPENSE / RESET, mixer row). The valve rows are the only valve design in the folder; in the live shell valves were removed ("No device installed" panel, IDX:686-689).
- Admin page layout (ADMINFRAG): tab buttons 44 px (colours bound to data), a table with a 42 px uppercase header and 52 px rows whose editable cells are right-aligned 40 px value buttons, a users view (name input, role input, four level buttons, state button, ✕), and a 48 px footer with ADD USER / REVERT / SAVE. All controls carry `disabled` + `data-unconnected="true"` + `title="has not yet been connected to the device/Source"`.

### 8.2 WF (`/workflow/`)

Scrolling document page, palette of the live shell, sections as white bordered boxes (`section {border:1px solid var(--line);background:#fff;margin-top:10px;padding:10px 12px}`), header with a 2 px `--ink-2` rule and an amber status tag, native inputs 40 px, monospace textarea, native `<dialog>`. Contains a password field ("Administrator password", log in / Sign out). `README.md:57` says this page is no longer used in the HMI.

### 8.3 TEST (`/test/`, shown only with `test_page_enabled`)

Older accent-blue look (`--blue #5980a6`), 'Segoe UI'. Own header / nav / footer are hidden when embedded (TEST:2-9: "Embedded test extension: original HMI owns the header, main navigation and footer."); its nav becomes a horizontal tab row. Components: `.metric` cards with a 3 px blue top border and 25-29px value, `.panel` + `.panel-head`, `dl` key/value rows, `.callout`, `<table>`, `.form-grid` two-column labelled inputs, `#toast`.

### 8.4 LITE (`lite/`)

Standalone dark page "HM · Simple control" with rounded cards (`article`, radius 10 px), rounded buttons (radius 7 px), native inputs, `.pump-grid` of 5 pump cards, `<pre>` logs, canvas spectrum. Not part of the shell look.

---

## 9. Bundled files under `web/vendor/` and licences

| File | Content | Licence |
|---|---|---|
| `font-a35caa4687f434d6.ttf` (82,624 B) | Barlow Regular 400, Version 1.408 | SIL Open Font License 1.1 (`Barlow-OFL.txt`: "Copyright 2017 The Barlow Project Authors (https://github.com/jpt/barlow)") |
| `font-91c841fdfa8e7b94.ttf` (81,924 B) | Barlow Medium 500 | same |
| `font-74103fd6d41bc32c.ttf` (85,776 B) | Barlow Bold 700 | same |
| `font-c82d65280835cb3c.ttf` (80,968 B) | Barlow Condensed Regular 400 | SIL OFL 1.1 (`BarlowCondensed-OFL.txt`, same copyright line) |
| `font-0d85af813fc3ed87.ttf` (86,636 B) | Barlow Condensed SemiBold 600 | same |
| `fonts.css` | the five `@font-face` rules (`font-display: swap`, `format('truetype')`, URLs `/vendor/font-*.ttf`) | n/a |
| `react.production.min.js`, `react-dom.production.min.js` | React runtime | MIT (`React-LICENSE.txt`, `ReactDOM-LICENSE.txt`: "Copyright (c) Facebook, Inc. and its affiliates.") |
| `babel.min.js` (3.1 MB) | Babel standalone; not referenced by IDX (IDX:7-10 load only React, ReactDOM, support.js, i18n.js) | MIT (`Babel-LICENSE.txt`: "Copyright (c) 2014-present Sebastian McKenzie and other contributors") |

Font names were read from the name tables of the TTF files. `prepare_vendor.py:33` shows the origin: Google Fonts `family=Barlow:wght@400;500;700&family=Barlow+Condensed:wght@400;600`. The OFL requires the licence text to travel with the fonts; a plain rebuild that keeps Barlow should copy the two OFL files. There is no CJK font bundled.

---

## 10. Not determined

- The physical panel (size, resolution, touch technology). Code only fixes the layout at 1024 x 768 and starts Edge in kiosk mode.
- Which font `system-ui` resolves to on the panel, i.e. the actual Chinese typeface in the shell.
- Whether the dialog really renders with the 13.6 px inner padding / 10.2 px gaps and whether `.btn` really renders in Barlow Condensed (both follow from the cascade; nothing was run, no screenshot was available).
- Canvas chart colours of TEST and LITE (in `web/test/app.js` and `lite/app.js`, not read) and any styling done from JS in `web/calibration/app.js` / `web/trend/app.js` beyond the grep'd colour lines.
- Whether a real `web/custom/branding.json` / `texts.json` / logo exists (only the `.example.json` files were listed by the glob).
- No design rationale document (the DS comment mentions a `readme.md`, `foundations/` and `components/` which are not in the `_ds` folder).

---

# Appendix A. Verbatim CSS of the live shell

## A.1 `web/shell.css` (whole file)

```css
/* Keep the supplied 1024 x 768 machine layout. Only extension/disabled styles. */
html,body{margin:0;min-width:1024px;min-height:768px;background:#d9dadc}
button[data-unconnected]{opacity:.65;cursor:not-allowed!important;pointer-events:none}
[aria-disabled="true"]{cursor:default!important}
.test-frame{display:block;border:0;width:100%;height:100%;min-width:0;background:var(--color-bg)}
.test-extension{overflow:hidden}
```

## A.2 `web/vendor/fonts.css` (whole file)

```css
@font-face {
  font-family: 'Barlow';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url(/vendor/font-a35caa4687f434d6.ttf) format('truetype');
}
@font-face {
  font-family: 'Barlow';
  font-style: normal;
  font-weight: 500;
  font-display: swap;
  src: url(/vendor/font-91c841fdfa8e7b94.ttf) format('truetype');
}
@font-face {
  font-family: 'Barlow';
  font-style: normal;
  font-weight: 700;
  font-display: swap;
  src: url(/vendor/font-74103fd6d41bc32c.ttf) format('truetype');
}
@font-face {
  font-family: 'Barlow Condensed';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url(/vendor/font-c82d65280835cb3c.ttf) format('truetype');
}
@font-face {
  font-family: 'Barlow Condensed';
  font-style: normal;
  font-weight: 600;
  font-display: swap;
  src: url(/vendor/font-0d85af813fc3ed87.ttf) format('truetype');
}
```

## A.3 Design system `web/original/_ds/industry-9d663c7d-84bf-476c-8738-ab0ad0637c7d/styles.css` (whole file)

```css
/* Industry — design-system tokens and component classes. This file is the source of truth for the system's look; retune it here and see readme.md. */
@import url('/vendor/fonts.css');

:root {
  --color-bg: #f2f2f3;
  --color-surface: #e9e9ea;
  --color-text: #1d1f20;
  --color-accent: #5980a6;
  --color-accent-2: #728fab;
  --color-divider: color-mix(in srgb, #1d1f20 16%, transparent);

  /* Tonal ramps — generated in OKLCH on one shared lightness scale, so the
     same step of any role matches the others in visual value. */
  --color-neutral-100: #f5f5f8;
  --color-neutral-200: #e7e7ea;
  --color-neutral-300: #d4d4d7;
  --color-neutral-400: #b7b7ba;
  --color-neutral-500: #98989b;
  --color-neutral-600: #7a7a7d;
  --color-neutral-700: #5d5d60;
  --color-neutral-800: #424244;
  --color-neutral-900: #2b2b2d;

  --color-accent-100: #eef6ff;
  --color-accent-200: #d6ebff;
  --color-accent-300: #b5d9fd;
  --color-accent-400: #94bce3;
  --color-accent-500: #749dc4;
  --color-accent-600: #597ea3;
  --color-accent-700: #416180;
  --color-accent-800: #2c455d;
  --color-accent-900: #1d2d3d;

  --color-accent-2-100: #eef6ff;
  --color-accent-2-200: #d6ebff;
  --color-accent-2-300: #bdd8f2;
  --color-accent-2-400: #9ebbd8;
  --color-accent-2-500: #7e9cb8;
  --color-accent-2-600: #627d98;
  --color-accent-2-700: #486077;
  --color-accent-2-800: #314457;
  --color-accent-2-900: #1f2d3a;

  --font-heading: "Barlow Condensed", system-ui, sans-serif;
  --font-heading-weight: 600;
  --font-body: "Barlow", system-ui, sans-serif;

  --space-1: 3.4px;
  --space-2: 6.8px;
  --space-3: 10.2px;
  --space-4: 13.6px;
  --space-6: 20.4px;
  --space-8: 27.2px;

  --radius-sm: 2px;
  --radius-md: 4px;
  --radius-lg: 7px;

  /* Elevation — derived from the ground: soft ink-tinted shadows on a
     light theme, a hairline edge + ambient darkness on a dark one. */
  --shadow-sm: 0 1px 2px color-mix(in srgb, #2b2b2d 14%, transparent);
  --shadow-md: 0 3px 10px color-mix(in srgb, #2b2b2d 16%, transparent);
  --shadow-lg: 0 12px 32px color-mix(in srgb, #2b2b2d 22%, transparent);
}

body {
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-body);
}
h1, h2, h3, h4 { font-family: var(--font-heading); font-weight: var(--font-heading-weight); }

.blueprint {
  position: relative;
  border: 1px solid var(--color-divider);
  border-radius: 0;
}
/* The overlay image treatments (halftone, duotone) clip their overlay
   (overflow:hidden); a blueprint wrapper draws its registration marks
   outside the box, so when both classes share a wrapper the frame must
   win. */
.blueprint.halftone, .blueprint.plate, .blueprint.duotone { overflow: visible; }
.blueprint > .corner {
  position: absolute; width: 11px; height: 11px;
  color: color-mix(in srgb, var(--color-text) 55%, transparent);
}
.blueprint > .corner::before, .blueprint > .corner::after {
  content: ""; position: absolute; background: currentColor;
}
.blueprint > .corner::before { left: 5px; top: 0; width: 1px; height: 100%; }
.blueprint > .corner::after  { top: 5px; left: 0; width: 100%; height: 1px; }
.blueprint > .corner.tl { top: -6px; left: -6px; }
.blueprint > .corner.tr { top: -6px; right: -6px; }
.blueprint > .corner.bl { bottom: -6px; left: -6px; }
.blueprint > .corner.br { bottom: -6px; right: -6px; }

.duotone{position:relative;overflow:hidden}
.duotone::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:var(--color-accent);mix-blend-mode:color}

/* ══════════════════════════════════════════════════════════════════════════
   Components — built with the tokens above. Plain CSS
   on plain HTML: no JavaScript, no build step. Each class is documented in
   readme.md and demonstrated in foundations/ and components/.
   ══════════════════════════════════════════════════════════════════════ */

*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; font-size: 15px; line-height: 1.55; font-weight: 400; }
h1, h2, h3, h4, h5, h6 {
  font-family: var(--font-heading); font-weight: var(--font-heading-weight);
  line-height: 1.12; letter-spacing: -0.015em; margin: 0 0 var(--space-2);
}
h1 { font-size: 42px; }
h2 { font-size: 32px; }
h3 { font-size: 25px; }
h4 { font-size: 20px; }
h5 { font-size: 16px; }
h6 { font-size: 13px; }
h6 { letter-spacing: 0.08em; text-transform: uppercase; }
p { margin: 0 0 var(--space-3); }
a { color: var(--color-accent); text-underline-offset: 3px; }
img { display: block; max-width: 100%; }
figure { margin: 0; }
figcaption {
  font-size: 11px; margin-top: var(--space-1);
  color: color-mix(in srgb, var(--color-text) 55%, transparent);
}
.text-muted { color: color-mix(in srgb, var(--color-text) 55%, transparent); }
:focus { outline: none; }
:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }
::selection { background: color-mix(in srgb, var(--color-accent) 30%, transparent); }

/* — rules — */
.hr {
  height: 1px; border: 0; margin: var(--space-4) 0;
  background: var(--color-divider);
}

/* — buttons — */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  cursor: pointer; text-decoration: none;
  font-family: var(--font-heading); font-weight: var(--font-heading-weight);
  font-size: 14px; line-height: 1.2; color: var(--color-text); /* matches the .input's 14px —
     the pair sits side by side in sign-up rows */
  background: transparent; border: 1px solid transparent;
  padding: var(--space-2) calc(var(--space-3) * 1.2);
  border-radius: var(--radius-md);
}
.btn svg { display: block; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-primary { background: var(--color-accent); color: var(--color-bg); }
.btn-primary:hover { background: var(--color-accent-600); }
.btn-primary:active { background: var(--color-accent-700); }
.btn-secondary { border-color: var(--color-divider); }
.btn-secondary:hover { background: color-mix(in srgb, var(--color-text) 7%, transparent); }
.btn-secondary:active { background: color-mix(in srgb, var(--color-text) 14%, transparent); }
.btn-ghost { color: var(--color-accent); padding-inline: var(--space-1); }
.btn-ghost:hover { background: color-mix(in srgb, var(--color-accent) 10%, transparent); }
.btn-ghost:active { background: color-mix(in srgb, var(--color-accent) 18%, transparent); }
.btn-icon { width: 36px; height: 36px; padding: 0; }
.btn-block { width: 100%; margin-top: var(--space-2); }

/* — forms — */
.field > label {
  display: block; font-size: 12px; margin-bottom: 5px;
  color: color-mix(in srgb, var(--color-text) 70%, transparent);
}
.input {
  width: 100%; min-height: 36px; padding: 6px 10px; font: inherit;
  font-size: 14px; color: var(--color-text); caret-color: var(--color-accent);
  background: var(--color-surface);
  border: 1px solid var(--color-divider); border-radius: var(--radius-md);
}
.input:hover { border-color: color-mix(in srgb, var(--color-text) 45%, transparent); }
.input:focus-visible { border-color: var(--color-accent); outline-offset: 0; }
textarea.input { min-height: 90px; resize: vertical; }
.radio { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; }
.radio input, .seg-opt input {
  position: absolute; opacity: 0; width: 0; height: 0; pointer-events: none;
}
.radio .dot {
  width: 16px; height: 16px; flex: none; border-radius: 50%;
  border: 1.5px solid var(--color-divider);
}
.radio:hover .dot { border-color: var(--color-accent); }
.radio input:checked + .dot {
  border-color: var(--color-accent); background: var(--color-accent);
  box-shadow: inset 0 0 0 4px var(--color-bg);
}
.radio input:focus-visible + .dot { outline: 2px solid var(--color-accent); outline-offset: 2px; }
.seg {
  display: inline-flex; overflow: hidden;
  border: 1px solid var(--color-divider); border-radius: var(--radius-md);
}
.seg-opt {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 12px; font-size: 13px; cursor: pointer;
}
.seg-opt + .seg-opt { border-left: 1px solid var(--color-divider); }
.seg-opt:has(input:checked) { background: var(--color-accent); color: var(--color-bg); }
.seg-opt:not(:has(input:checked)):hover { background: color-mix(in srgb, var(--color-text) 7%, transparent); }
.seg-opt:has(input:focus-visible) { outline: 2px solid var(--color-accent); outline-offset: -2px; }

/* — cards — */
.card {
  display: flex; flex-direction: column; gap: var(--space-2);
  padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-surface);
}
.card-kicker { font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--color-accent); }
.card-title {
  font-family: var(--font-heading); font-weight: var(--font-heading-weight);
  font-size: 17px; line-height: 1.2;
}
.card-body { margin: 0; font-size: 13px; opacity: 0.8; flex: 1; }
.card-meta {
  display: flex; align-items: center; gap: 6px; font-size: 11px;
  color: color-mix(in srgb, var(--color-text) 50%, transparent);
}
.elev-sm { box-shadow: var(--shadow-sm); }
.elev-md { box-shadow: var(--shadow-md); }
.elev-lg { box-shadow: var(--shadow-lg); }

/* — tags — */
.tag {
  display: inline-flex; align-items: center; font-size: 11px;
  letter-spacing: 0.02em; padding: 3px 10px;
  border-radius: calc(var(--radius-md) * 0.75);
}
.tag-accent { background: var(--color-accent-100); color: var(--color-accent-800); }
.tag-accent-2 { background: var(--color-accent-2-100); color: var(--color-accent-2-800); }
.tag-neutral { background: var(--color-neutral-100); color: var(--color-neutral-800); }
.tag-outline { border: 1px solid var(--color-accent); color: var(--color-accent); }

/* — navigation — */
.nav {
  display: flex; align-items: center; gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-bottom: none;
}
.nav-brand {
  font-family: var(--font-heading); font-weight: var(--font-heading-weight);
  font-size: 18px; margin-right: auto;
}
.nav a { color: inherit; text-decoration: none; font-size: 14px; }
.nav a:hover, .nav a[aria-current='page'] { color: var(--color-accent); }

/* — tables — */
.table { width: 100%; border-collapse: collapse; font-size: 14px; }
.table th {
  text-align: left; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
  color: color-mix(in srgb, var(--color-text) 60%, transparent);
  padding: var(--space-2); border-bottom: 1px solid var(--color-divider);
}
.table td {
  padding: var(--space-2);
  border-bottom: 1px solid color-mix(in srgb, var(--color-text) 8%, transparent);
}
.table tbody tr:hover { background: color-mix(in srgb, var(--color-text) 4%, transparent); }

/* — dialog — */
.dialog-backdrop {
  position: fixed; inset: 0; display: grid; place-items: center;
  padding: var(--space-4);
  background: color-mix(in srgb, var(--color-neutral-900) 50%, transparent);
}
.dialog {
  width: min(440px, 100%); display: flex; flex-direction: column; gap: var(--space-3);
  padding: var(--space-4); border-radius: var(--radius-lg);
  background: var(--color-surface); box-shadow: var(--shadow-lg);
}
.dialog-title {
  font-family: var(--font-heading); font-weight: var(--font-heading-weight);
  font-size: 20px;
}
.dialog-body { font-size: 14px; opacity: 0.85; }
.dialog-actions { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-2); }

/* — blueprint frame: components are wireframe objects (see .blueprint
     and .corner above) — square, transparent, hairline-bordered — */
.card, .btn, .input, .tag, .seg, .dialog { border-radius: 0; }
.card, .dialog { background: transparent; border: 1px solid var(--color-divider); }
.btn { border: 1px solid var(--color-divider); }
.btn-primary { border-color: var(--color-accent); }
.btn-ghost { border-color: transparent; }

```

## A.4 Inline style block of `web/index.html`, lines 18-468 (the live look)

```html
<style>
  /*
   * status display rules (consistent across the entire screen)
   *   The color is only used to indicate ”abnormality” or ”operation”, and the normal state remains grayscale.
   *   operation/command ON ........ blue solid          .q-run
   *   normal value/command OFF ...... dark gray text          .q-ok
   *   communication failure (value cannot be trusted).. magenta＋“?”dashed box  .q-bad
   *   not installed/not connected ........ light gray dotted frame        .q-na
   *   Uncorrected ................ slash shading          .q-uncal
   *   Alarm priority (only used for alarms) Emergency Red .p1 / high Orange .p2 / middle yellow .p3
   */
  :root {
    --run: #2f5f8f; --run-bg: #dce9f6;
    --bad: #a3168e; --bad-bg: #fbe8f7;
    --p1: #c62828; --p2: #e06a00; --p3: #e0b400;
    --ink: #1d1f20; --ink-2: #45484b; --ink-3: #6b6e72;
    --line: #b9bcc0; --panel: #fbfbfb;
  }
  body { margin:0; background:#d9dadc; }
  button { font-family:inherit; }
  button:focus-visible { outline:3px solid var(--run); outline-offset:2px; }
  button:disabled { cursor:not-allowed; }
  @keyframes blink { 50% { opacity:.35; } }

  .hmi { position:relative; width:1024px; height:768px; background:var(--color-bg); color:var(--ink); font-family:var(--font-body), "Microsoft JhengHei", sans-serif; font-size:15px; display:flex; flex-direction:column; overflow:hidden; }
  .hmi.demo { box-shadow: inset 0 0 0 5px #d9a300; }

  /* ── top bar ── */
  .top { flex:0 0 56px; display:flex; align-items:center; gap:10px; padding:0 12px; border-bottom:1px solid var(--line); background:#fff; }
  .title { font-family:var(--font-heading); font-weight:600; font-size:22px; white-space:nowrap; min-width:0; overflow:hidden; text-overflow:ellipsis; }
  .brand-logo { height:38px; max-width:150px; object-fit:contain; flex:0 0 auto; }
  .title.branded { font-size:17px; line-height:1.1; white-space:normal; max-width:200px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
  .mode { font-size:14px; padding:4px 9px; border:1px solid var(--line); white-space:nowrap; }
  .mode.demo { background:repeating-linear-gradient(135deg,#ffe7a3 0 8px,#fff3cc 8px 16px); border-color:#b88a00; font-weight:700; }
  .chip { display:flex; align-items:center; gap:6px; font-size:14px; padding:5px 9px; border:1px solid var(--line); white-space:nowrap; background:#fff; }
  .chip i { width:11px; height:11px; display:block; background:var(--ink-3); }
  .chip.q-ok i { background:#3d7a52; }
  .chip.q-bad { color:var(--bad); border:1px dashed var(--bad); background:var(--bad-bg); font-weight:700; }
  .chip.q-bad i { background:var(--bad); }
  .chip.q-na { color:var(--ink-3); border-style:dashed; }
  .recipe { display:flex; flex-direction:column; line-height:1.15; min-width:0; }
  .recipe small { font-size:12px; color:var(--ink-3); }
  .recipe b { font-size:15px; max-width:190px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .recipe b.none { color:var(--ink-3); font-weight:400; }
  .userbtn { height:36px; padding:0 10px; background:#fff; border:1px solid var(--line); font-size:14px; cursor:pointer; white-space:nowrap; }
  .userbtn.admin { border-color:var(--run); color:var(--run); font-weight:700; }
  /* Brand area: long press 3 Open maintenance login in seconds (no prompt will be displayed, general operators will not see the entrance) */
  .brandbox { display:flex; align-items:center; gap:10px; min-width:0; user-select:none; -webkit-user-select:none; touch-action:none; }
  .opbtn { height:44px; min-width:118px; max-width:170px; padding:0 10px; display:flex; flex-direction:column; justify-content:center; align-items:flex-start;
    line-height:1.15; background:#fff; border:1px solid var(--line); cursor:pointer; text-align:left; }
  .opbtn small { font-size:12px; color:var(--ink-3); } .opbtn b { font-size:15px; max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .opbtn.none b { color:#8a3d00; }
  .clock { display:flex; flex-direction:column; align-items:flex-end; font-family:var(--font-heading); font-size:19px; line-height:1.05; min-width:74px; white-space:nowrap; }
  .clock small { font-size:12px; color:var(--ink-3); font-family:var(--font-body); }
  .lang { display:flex; }
  .lang button { width:36px; height:34px; margin-left:-1px; border:1px solid var(--line); background:#fff; font-size:14px; cursor:pointer; padding:0; }
  .lang button.sel { background:var(--ink-2); border-color:var(--ink-2); color:#fff; font-weight:700; }

  /* ── Alert banner: Show only alerts ── */
  .banner { flex:0 0 44px; display:flex; align-items:center; gap:10px; padding:0 12px; font-size:16px; border-bottom:1px solid var(--line); }
  .banner.none { background:#eceded; color:var(--ink-3); font-size:15px; }
  .banner.p1 { background:var(--p1); color:#fff; }
  .banner.p2 { background:var(--p2); color:#fff; }
  .banner.p3 { background:var(--p3); color:#111; }
  .banner.acked { background:#fff; color:var(--ink); }
  .banner .pri { font-weight:700; padding:3px 9px; border:2px solid currentColor; white-space:nowrap; }
  .banner.unacked .pri { animation:blink 1s steps(1) infinite; }
  .banner .msg { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:600; }
  .banner .time { font-family:var(--font-heading); white-space:nowrap; }
  .banner .more { font-size:14px; white-space:nowrap; }
  .banner button { height:34px; padding:0 14px; background:rgba(255,255,255,.92); color:var(--ink); border:1px solid rgba(0,0,0,.35); font-size:15px; font-weight:700; cursor:pointer; }

  /* ── Left navigation ── */
  .body { flex:1; display:flex; min-height:0; }
  .nav { flex:0 0 116px; display:flex; flex-direction:column; gap:5px; padding:8px 6px; border-right:1px solid var(--line); background:#f7f7f7; }
  .nav button { position:relative; width:100%; min-height:54px; display:flex; align-items:center; justify-content:flex-start; text-align:left; padding:0 10px; font-size:16px; white-space:nowrap; background:#fff; border:1px solid var(--line); color:var(--ink); cursor:pointer; }
  .nav button.sel { background:var(--run); border-color:var(--run); color:#fff; font-weight:700; }
  .nav button.navmaint { margin-top:auto; min-height:46px; font-size:15px; color:var(--ink-2); border-style:dashed; }
  .nav button.navmaint.sel { color:#fff; border-style:solid; }
  .nav .badge { position:absolute; right:4px; top:4px; min-width:20px; height:20px; padding:0 5px; font-size:13px; line-height:20px; text-align:center; color:#fff; font-weight:700; }
  .badge.p1 { background:var(--p1); } .badge.p2 { background:var(--p2); } .badge.p3 { background:var(--p3); color:#111; } .badge.acked { background:var(--ink-3); }

  .page { flex:1; min-width:0; display:flex; flex-direction:column; gap:8px; padding:8px 10px; }
  .panel { background:var(--panel); border:1px solid var(--line); }
  .ph { display:flex; align-items:center; gap:10px; padding:6px 12px; border-bottom:1px solid var(--line); min-height:34px; }
  .ph h2 { margin:0; font-size:18px; font-family:inherit; font-weight:700; white-space:nowrap; }
  .ph .sub { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  html[lang="en"] .nm { font-size:12px; }
  .ph .sub { font-size:13px; color:var(--ink-3); }
  .ph .sp { flex:1; }

  /* quality status */
  .q-ok { color:var(--ink); }
  .q-run { color:var(--run); font-weight:700; }
  .q-bad { color:var(--bad); font-weight:700; }
  .q-na { color:var(--ink-3); }
  .q-uncal { color:var(--ink-2); }
  .q-cal { color:#3d7a52; }
  .tag { display:inline-block; font-size:13px; padding:2px 7px; border:1px solid var(--line); white-space:nowrap; }
  .tag.q-run { background:var(--run); color:#fff; border-color:var(--run); }
  .tag.q-bad { border:1px dashed var(--bad); background:var(--bad-bg); }
  .tag.q-na { border-style:dashed; }
  .tag.q-uncal { background:repeating-linear-gradient(135deg,#e2e2e2 0 4px,#f6f6f6 4px 8px); border-color:#9a9a9a; }

  /* ── Overview ── */
  .metals { flex:0 0 84px; display:flex; gap:8px; }
  .metal { flex:1; padding:7px 12px; display:flex; flex-direction:column; justify-content:space-between; }
  .metal-head { display:flex; align-items:center; justify-content:space-between; }
  .metal-name { font-size:19px; font-weight:700; }
  .metal-val { display:flex; align-items:baseline; gap:6px; }
  .metal-val b { font-family:var(--font-heading); font-size:30px; line-height:1; color:var(--ink-3); }
  .metal-val.has b { color:var(--ink); }
  .tag.q-cal { background:#fff; border-color:#3d7a52; color:#3d7a52; }
  .tag.q-blank { background:#fff4d6; border-color:#c99a2e; color:#7a5200; }
  .tag.q-blankbad { background:var(--bad-bg); border:1px dashed var(--bad); color:var(--bad); font-weight:700; }
  .metal-val > span { font-size:13px; color:var(--ink-3); }

  .steps { flex:0 0 auto; }
  .steps-row { display:flex; gap:3px; padding:6px 8px 7px; }
  .step { flex:1; height:36px; display:flex; align-items:center; justify-content:center; gap:5px; border:1px solid var(--line); background:#fff; font-size:15px; color:var(--ink-2); white-space:nowrap; }
  .step .no { font-size:12px; color:var(--ink-3); }
  .step.done { background:#e4e8ec; color:var(--ink-2); }
  .step.cur { background:var(--run); border-color:var(--run); color:#fff; font-weight:700; }
  .step.cur .no { color:#dce9f6; }
  .step.fail { background:var(--p2); border-color:var(--p2); color:#fff; font-weight:700; }
  .step.fail .no { color:#fff; }
  .steps-empty { padding:10px 12px; font-size:15px; color:var(--ink-3); }
  .steps .meta { font-size:14px; color:var(--ink-2); white-space:nowrap; }

  /* flow path diagram:ISA-101 High-performance graphics. Mainly gray scale, line width and solid/Hollow indicates status without animation. */
  .pid { flex:1; min-height:0; display:flex; flex-direction:column; }
  .legend { display:flex; gap:10px; align-items:center; font-size:12px; color:var(--ink-2); white-space:nowrap; }
  .legend i { display:inline-block; vertical-align:middle; margin-right:4px; }
  .legend .lg-act { width:22px; border-top:4px solid var(--ink); }
  .legend .lg-run { width:12px; height:12px; border-radius:50%; background:var(--ink); }
  .legend .lg-off { width:12px; height:12px; border-radius:50%; border:2px solid #3a3d40; box-sizing:border-box; }
  .legend .lg-bad { width:12px; height:12px; border-radius:50%; border:2px dashed var(--bad); box-sizing:border-box; background:var(--bad-bg); }
  .legend .lg-na { width:12px; height:12px; background:repeating-linear-gradient(45deg,#b5b7ba 0 2px,#eceded 2px 5px); }
  .pid-wrap { flex:1; min-height:0; display:flex; align-items:center; justify-content:center; background:#e6e7e9; }
  .pid-box { position:relative; width:880px; height:300px; }
  .pid-box svg { position:absolute; inset:0; }
  .ln { stroke:#a4a7ab; stroke-width:2.5; fill:none; }
  .ln.thin { stroke-width:1.8; } .ln.air { stroke-width:1.8; stroke-dasharray:9 5; }
  .ln.act { stroke:var(--ink); stroke-width:4.5; }
  .ah { fill:#a4a7ab; } .ah.act { fill:var(--ink); }
  .eq { stroke:#3a3d40; stroke-width:2; fill:#f4f4f5; }
  .eq.act { stroke:var(--ink); stroke-width:3.5; }
  .pmp { stroke:#3a3d40; stroke-width:2; fill:#f4f4f5; }
  .pmp.q-run { fill:var(--ink); stroke:var(--ink); }
  .pmp.q-bad { stroke:var(--bad); stroke-dasharray:5 3; fill:var(--bad-bg); }
  .pmp.q-na { stroke:#9a9da1; stroke-dasharray:4 3; fill:#eceded; }
  .ptag { font-size:14px; font-weight:700; text-anchor:middle; fill:var(--ink); }
  .ptag.q-run { fill:#fff; } .ptag.q-bad { fill:var(--bad); } .ptag.q-na { fill:#8a8d91; }
  .nm { font-size:15px; font-weight:700; text-anchor:middle; fill:var(--ink); }
  .tg { font-size:12px; text-anchor:middle; fill:var(--ink-3); }
  .bar { position:absolute; width:7px; border:1px solid #9a9da1; background:repeating-linear-gradient(45deg,#b5b7ba 0 2px,#eceded 2px 5px); }
  .pst { position:absolute; width:88px; text-align:center; font-size:13px; line-height:1.15; }
  .vbox { position:absolute; width:96px; padding:3px 6px; background:#fff; border:1.5px solid var(--ink); font-size:12px; line-height:1.25; }
  .vbox b { display:block; font-size:14px; }
  .amark { position:absolute; display:flex; align-items:center; gap:4px; font-size:12px; font-weight:700; white-space:nowrap; }
  .amark i { width:18px; height:18px; display:flex; align-items:center; justify-content:center; font-style:normal; font-size:11px; color:#fff; }
  .amark i.p1 { background:var(--p1); }
  .amark i.p2 { background:var(--p2); transform:rotate(45deg) scale(.85); } .amark i.p2 b { transform:rotate(-45deg); }
  .amark i.p3 { width:0; height:0; border-left:10px solid transparent; border-right:10px solid transparent; border-bottom:18px solid var(--p3); position:relative; }
  .amark i.p3 b { position:absolute; top:5px; left:-3px; color:#111; }
  .amark.acked i { opacity:.55; }
  .hit { position:absolute; background:transparent; border:0; cursor:pointer; padding:0; }
  .hit:hover, .hit.sel { outline:2px solid var(--run); outline-offset:2px; }
  .face { position:absolute; right:14px; top:112px; width:268px; z-index:30; background:#fff; border:2px solid var(--ink); box-shadow:0 8px 28px rgba(0,0,0,.28); }
  .face-h { display:flex; align-items:center; gap:8px; padding:8px 10px; background:#3a3d40; color:#fff; }
  .face-h b { flex:1; font-size:17px; }
  .face-h button { width:32px; height:32px; background:transparent; border:1px solid rgba(255,255,255,.6); color:#fff; font-size:18px; cursor:pointer; }
  .face-b { padding:8px 12px; display:flex; flex-direction:column; gap:5px; }
  .face-row { display:flex; justify-content:space-between; gap:10px; font-size:14px; padding:3px 0; border-bottom:1px dotted #d0d2d5; }
  .face-row > span:first-child { color:var(--ink-3); white-space:nowrap; }
  .face-row > span:last-child { text-align:right; }
  .face-note { font-size:13px; color:var(--ink-2); line-height:1.45; padding-top:2px; }
  .face-f { display:flex; gap:8px; padding:8px 12px 12px; }
  .face-f .btn { flex:1; height:46px; justify-content:center; }

  .strip { flex:0 0 56px; display:flex; }
  .cell { flex:1; padding:5px 10px; border-right:1px solid var(--line); display:flex; flex-direction:column; justify-content:center; gap:3px; min-width:0; }
  .cell:last-child { border-right:0; }
  .cell small { font-size:12px; color:var(--ink-3); white-space:nowrap; }
  .cell b { font-size:17px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .cell b.p1 { color:var(--p1); } .cell b.p2 { color:var(--p2); } .cell b.p3 { color:#8a6d00; }

  /* ── manual operation ── */
  .notice { flex:0 0 auto; display:flex; align-items:center; gap:10px; padding:8px 12px; font-size:15px; border:1px solid var(--line); background:#fff; }
  .notice.warn { border-left:6px solid var(--p3); }
  .notice.block { border-left:6px solid var(--ink-3); color:var(--ink-2); }
  .notice.ok { border-left:6px solid var(--run); }
  .notice.err { border-left:6px solid var(--p2); color:#8a3d00; background:#fff4ea; }
  .notice .n-body { flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
  .notice .n-fix { font-size:14px; color:var(--ink); font-weight:400; }
  .notice .btn { height:40px; flex:0 0 auto; }
  .manual { flex:1; display:flex; gap:8px; min-height:0; }
  .pumps { flex:1; display:flex; flex-direction:column; min-width:0; }
  .seg { display:flex; }
  .seg button { height:36px; min-width:58px; padding:0 10px; border:1px solid var(--line); background:#fff; font-size:15px; cursor:pointer; margin-left:-1px; white-space:nowrap; }
  .seg button.sel { background:var(--run); color:#fff; border-color:var(--run); font-weight:700; position:relative; }
  .seg button:disabled { color:#9a9da1; }
  .seg button.sel:disabled { background:#8aa3bd; color:#fff; }
  .btn { height:40px; padding:0 14px; border:1px solid #8b8f94; background:#fff; font-size:16px; cursor:pointer; color:var(--ink); white-space:nowrap; }
  .btn:disabled { color:#a3a6aa; border-color:#d0d2d5; background:#f3f3f3; }
  .btn.primary { background:var(--run); border-color:var(--run); color:#fff; font-weight:700; }
  .btn.primary:disabled { background:#b9c6d3; border-color:#b9c6d3; color:#fff; }
  .prow { flex:1; display:flex; align-items:center; gap:12px; padding:0 12px; border-bottom:1px solid #dcdee0; }
  .prow:last-child { border-bottom:0; }
  .prow .nm { flex:1; min-width:0; }
  .prow .nm b { display:block; font-size:18px; }
  .prow .nm small { font-size:13px; color:var(--ink-3); }
  .prow .tag { font-size:14px; min-width:88px; text-align:center; }
  .prow .sec { width:96px; height:46px; display:flex; flex-direction:column; align-items:center; justify-content:center; line-height:1.1;
    border:1px solid #8b8f94; background:#fff; font-size:16px; font-weight:700; cursor:pointer; padding:0; }
  .prow .sec small { font-size:11px; font-weight:400; color:var(--ink-3); }
  .prow .sec.run { border:2px solid var(--run); color:var(--run); background:var(--run-bg); }
  .prow .sec:disabled { cursor:default; }
  .prow .btn { width:64px; height:46px; font-size:17px; }
  .side { flex:0 0 330px; display:flex; flex-direction:column; gap:8px; min-height:0; }
  .syr { padding:10px 12px; display:flex; flex-direction:column; gap:10px; }
  .syr .row { display:flex; align-items:center; gap:8px; }
  .syr .row > span { flex:0 0 44px; font-size:15px; color:var(--ink-2); }
  .syr .seg { flex:1; } .syr .seg button { flex:1; height:50px; font-size:16px; display:flex; flex-direction:column; align-items:center; justify-content:center; line-height:1.1; }
  .syr .seg button small { font-size:13px; }
  .vol { flex:1; height:46px; border:1px solid #8b8f94; background:#fff; font-size:24px; font-family:var(--font-heading); cursor:pointer; }
  .vol small { font-size:14px; margin-left:4px; font-family:var(--font-body); color:var(--ink-3); }
  .syr .acts { display:flex; gap:8px; } .syr .acts .btn { flex:1; height:46px; }
  .fb { font-size:13px; color:var(--ink-3); line-height:1.4; }
  .na-list { padding:8px 12px; font-size:14px; color:var(--ink-2); line-height:1.55; }

  /* ── empty status/sheet ── */
  .empty { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; text-align:center; color:var(--ink-2); padding:20px; }
  .empty b { font-size:21px; color:var(--ink); }
  .empty p { margin:0; font-size:15px; max-width:560px; line-height:1.6; }
  .table { flex:1; min-height:0; display:flex; flex-direction:column; }
  .thead, .trow { display:flex; align-items:center; gap:8px; padding:0 12px; }
  .thead { flex:0 0 34px; font-size:13px; color:var(--ink-3); border-bottom:2px solid var(--ink-2); }
  .tbody { flex:1; min-height:0; overflow:hidden; }
  .trow { min-height:44px; border-bottom:1px solid #dcdee0; font-size:15px; }
  .trow.unacked { background:#fff6e5; }
  .c-time { flex:0 0 126px; font-family:var(--font-heading); font-size:16px; }
  .c-pri { flex:0 0 58px; } .c-area { flex:0 0 60px; } .c-state { flex:0 0 118px; font-size:14px; } .c-act { flex:0 0 72px; text-align:center; }
  .c-msg { flex:1; min-width:0; }
  .c-recipe { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .c-mode { flex:0 0 96px; font-size:14px; } .c-steps { flex:0 0 56px; text-align:right; } .c-status { flex:0 0 76px; text-align:center; } .c-conc { flex:0 0 190px; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .c-err { flex:0 0 70px; font-size:13px; color:var(--ink-2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .pri { display:inline-block; font-size:13px; font-weight:700; padding:2px 8px; color:#fff; }
  .pri.p1 { background:var(--p1); } .pri.p2 { background:var(--p2); } .pri.p3 { background:var(--p3); color:#111; }
  .st { display:inline-block; font-size:13px; padding:2px 8px; border:1px solid var(--line); }
  .st.completed { border-color:#3d7a52; color:#2b5a3b; } .st.failed { background:var(--p2); color:#fff; border-color:var(--p2); } .st.running { background:var(--run); color:#fff; border-color:var(--run); }
  .ack { height:34px; padding:0 12px; border:1px solid #8b8f94; background:#fff; font-size:15px; font-weight:700; cursor:pointer; }
  .counts { display:flex; gap:6px; }
  .count { display:flex; align-items:center; gap:6px; font-size:14px; padding:3px 8px; border:1px solid var(--line); background:#fff; }
  .count i { width:10px; height:10px; display:block; }
  .count i.p1 { background:var(--p1); } .count i.p2 { background:var(--p2); } .count i.p3 { background:var(--p3); }

  /* ── Touch operation: All lists are paged with a fixed number of pens, and no scroll appears. ── */
  .pager { flex:0 0 auto; display:flex; align-items:center; justify-content:center; gap:14px; padding:6px 8px; border-top:1px solid #dcdee0; }
  .pager .btn { height:40px; min-width:52px; justify-content:center; }
  .pager b { min-width:96px; text-align:center; font-size:15px; }
  .trow.slim { min-height:32px; font-size:13px; }
  .c-msg.one { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .seg.tabs button { height:44px; font-size:16px; padding:0 16px; }
  .list { flex:1; min-height:0; overflow:hidden; }
  .li { display:block; width:100%; min-height:58px; text-align:left; padding:7px 12px; border:0; border-bottom:1px solid #dcdee0; background:#fff; cursor:pointer; }
  .li b { display:block; font-size:16px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .li > span { display:block; font-size:13px; color:var(--ink-3); }
  .li .warn { color:#8a5b00; font-weight:700; }
  .li.sel { background:var(--run); color:#fff; } .li.sel > span { color:#dce9f6; } .li.sel .warn { color:#ffe08a; }

  /* ── On-screen keyboard ── */
  .kb { display:flex; flex-direction:column; gap:6px; padding:8px 12px 12px; }
  .kb-row { display:flex; gap:6px; justify-content:center; }
  .kb-key { width:56px; height:50px; font-size:20px; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .kb-key.mid { width:92px; font-size:16px; } .kb-key.wide { width:210px; font-size:16px; }
  .kb-key.on { background:var(--run); color:#fff; border-color:var(--run); }
  .dialog.wide { width:720px; }
  .login { flex:1; display:flex; flex-direction:column; }
  .login-row { display:flex; align-items:center; gap:10px; padding:14px 16px 4px; }
  .login-lbl { font-size:17px; font-weight:700; }
  .login-buf { flex:1; height:52px; border:2px solid var(--run); display:flex; align-items:center; padding:0 12px; font-size:22px; font-family:Consolas,monospace; letter-spacing:1px; background:#fff; overflow:hidden; white-space:nowrap; }
  .login-err { min-height:22px; padding:2px 16px; color:#8a3d00; font-weight:700; font-size:14px; }

  /* ── Administrator page ── */
  .adm-bar { flex:0 0 auto; display:flex; align-items:center; gap:10px; }
  .adm-body { flex:1; min-height:0; display:flex; gap:8px; }
  .adm-left { flex:0 0 300px; display:flex; flex-direction:column; min-height:0; }
  .adm-right, .adm-half, .adm-full { flex:1; min-width:0; min-height:0; display:flex; flex-direction:column; }
  .adm-foot { flex:0 0 auto; display:flex; align-items:center; gap:8px; padding:8px 10px; border-top:1px solid #dcdee0; }
  .adm-foot .btn { height:44px; }
  .adm-note { padding:8px 12px; font-size:13px; color:var(--ink-2); line-height:1.5; }
  .file-btn { display:flex; align-items:center; justify-content:center; flex:1; cursor:pointer; }
  .file-btn input { display:none; }
  .kv { display:grid; grid-template-columns:repeat(3, 1fr); gap:6px 16px; padding:8px 12px; border-bottom:1px solid #dcdee0; }
  .kv div { display:flex; flex-direction:column; min-width:0; }
  .kv div > span { font-size:12px; color:var(--ink-3); }
  .kv b { font-size:15px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .kv.big { grid-template-columns:1fr; gap:0; border-bottom:0; }
  .kv.big div { flex-direction:row; justify-content:space-between; align-items:center; padding:9px 0; border-bottom:1px dotted #d0d2d5; }
  .kv.big div > span { font-size:15px; } .kv.big b { font-size:17px; }
  .c-act2 { flex:0 0 140px; font-family:Consolas,monospace; font-size:12px; } .c-tgt { flex:0 0 58px; } .c-val { flex:0 0 78px; }
  .btn-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; padding:10px 12px; }
  .btn-grid .gbtn { height:66px; } .btn-grid .gbtn.hv { height:72px; }
  .gbtn { height:86px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px; font-size:17px; font-weight:700; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .gbtn small { font-size:12px; font-weight:400; color:var(--ink-3); }
  .gbtn.primary { background:var(--run); border-color:var(--run); color:#fff; } .gbtn.primary small { color:#dce9f6; }
  .gbtn:disabled { color:#a3a6aa; border-color:#d0d2d5; background:#f3f3f3; } .gbtn:disabled small { color:#b9bcc0; }
  /* Button that will output high voltage: yellow and black warning strips on both sides, red frame */
  .gbtn.hv { grid-column:span 2; height:80px; padding:0 32px; border:3px solid var(--p1); color:var(--p1); font-size:19px;
    background:repeating-linear-gradient(135deg,#ffd000 0 9px,#1d1f20 9px 18px) left / 22px 100% no-repeat,
      repeating-linear-gradient(135deg,#ffd000 0 9px,#1d1f20 9px 18px) right / 22px 100% no-repeat, #fff; }
  .gbtn.hv small { color:#8a1c1c; }
  .gbtn.hv:disabled { opacity:.55; color:#8a8d91; border-color:#c9ccd0; } .gbtn.hv:disabled small { color:#6b6e72; }
  .txt-warn { color:#8a3d00; font-weight:700; }
  .dialog.danger.hv { border:3px solid var(--p1); width:580px; }
  .dialog.danger.hv .dlg-h { background:var(--p1); color:#fff; }
  .dialog.danger.hv .dlg-f .ok { background:var(--p1); border-color:var(--p1); }
  .dialog.danger.hv .dlg-f .ok:disabled { background:#c9ccd0; border-color:#c9ccd0; }
  .ack-row { display:flex; align-items:center; gap:12px; width:100%; min-height:54px; margin-top:6px; padding:0 12px;
    border:2px solid var(--p1); background:#fff; font-size:16px; font-weight:700; text-align:left; cursor:pointer; }
  .ack-row .box { flex:0 0 28px; height:28px; border:2px solid var(--ink); display:flex; align-items:center; justify-content:center; font-size:20px; }
  .ack-row.on { background:#fdecec; } .ack-row.on .box { background:var(--p1); border-color:var(--p1); color:#fff; }
  .param-grid { flex:1; display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; padding:12px; align-content:start; }
  .pbtn { height:112px; display:flex; flex-direction:column; align-items:flex-start; justify-content:center; gap:3px; padding:10px 14px; border:1px solid #8b8f94; background:#fff; text-align:left; cursor:pointer; }
  .pbtn > span { font-size:14px; color:var(--ink-2); } .pbtn b { font-size:26px; font-family:var(--font-heading); } .pbtn small { font-size:12px; color:var(--ink-3); }
  .pbtn.changed, .cbtn.changed { border:2px solid var(--run); background:var(--run-bg); }
  .pbtn.local { border-style:dashed; }  /* HMI analysis setting, saved on its own (not written to the RPi) */
  .pbtn.local.warn { border:2px dashed var(--bad, #a3168e); }
  .cal-grid { flex:1; display:grid; grid-template-columns:120px repeat(3, 1fr); gap:14px; padding:22px; align-items:center; align-content:start; }
  .cal-h { font-size:18px; font-weight:700; text-align:center; } .cal-m { font-size:22px; }
  .cbtn { height:78px; font-size:24px; font-family:var(--font-heading); border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .cbtn.empty { color:var(--ink-3); font-size:18px; }

  /* ── Spectrum View (Administrator)── */
  .spec-list { flex:0 0 250px; display:flex; flex-direction:column; min-height:0; }
  .spec-main { flex:1; min-width:0; display:flex; flex-direction:column; min-height:0; }
  .spec-info { font-size:13px; color:var(--ink-2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
  .spec-info b { color:var(--ink); }
  .spec-ctl { display:flex; align-items:center; gap:8px; padding:6px 10px; }
  .spec-ctl .seg button { height:36px; min-width:0; font-size:14px; padding:0 10px; }
  .fnav { display:flex; align-items:center; gap:4px; font-size:14px; }
  .fnav .btn { height:36px; width:40px; padding:0; justify-content:center; font-size:16px; }
  .fnav b { min-width:78px; text-align:center; }
  .chart { position:relative; width:620px; height:308px; margin:2px 0 0 8px; }
  .chart svg { position:absolute; left:52px; top:4px; background:#fff; border:1px solid #c9ccd0; }
  .chart .ytk { position:absolute; left:0; width:48px; text-align:right; font-size:11px; color:var(--ink-3); transform:translateY(-50%); }
  .chart .xtk { position:absolute; top:291px; font-size:11px; color:var(--ink-3); transform:translateX(-50%); }
  .chart .unit { position:absolute; left:14px; top:291px; font-size:11px; color:var(--ink-3); }
  .chart .lgd { position:absolute; right:8px; top:8px; font-size:12px; color:var(--ink-2); background:rgba(255,255,255,.9); padding:2px 6px; border:1px solid #dcdee0; }
  .chart .ref { position:absolute; top:6px; font-size:11px; font-weight:700; color:#8a5b00; transform:translateX(4px); }
  .chart .msg { position:absolute; left:52px; top:4px; width:566px; height:282px; display:flex; align-items:center; justify-content:center; color:var(--ink-3); font-size:15px; }
  .sl-mean { stroke:var(--ink); stroke-width:1.6; fill:none; }
  .sl-frame { stroke:var(--run); stroke-width:1.3; fill:none; }
  .sl-over { stroke:#8fb0d0; stroke-width:.8; fill:none; opacity:.8; }
  .sl-over.sel { stroke:var(--run); stroke-width:1.6; opacity:1; }
  .sl-zero { stroke:#b9bcc0; stroke-width:1; stroke-dasharray:3 3; }
  .sl-grid { stroke:#eceded; stroke-width:1; }
  .sl-ref { stroke:#8a5b00; stroke-width:1; stroke-dasharray:4 3; }
  .spec-main .trow { min-height:32px; font-size:13px; cursor:pointer; }
  .spec-main .trow.sel { background:#dce9f6; }
  .spec-main .trow.flag { background:#fff6e5; } .spec-main .trow.flag.sel { background:#f5dcae; }
  .c-no { flex:0 0 56px; } .c-num { flex:0 0 96px; text-align:right; font-family:var(--font-heading); font-size:14px; } .c-flag { flex:1; color:#8a5b00; font-weight:700; padding-left:10px; }
  .imp { position:absolute; left:130px; top:112px; width:760px; height:500px; z-index:35; display:flex; flex-direction:column; background:#fff; border:2px solid var(--ink); box-shadow:0 12px 40px rgba(0,0,0,.35); }
  .imp .tbody .trow { min-height:38px; font-size:14px; }
  .c-job { flex:1; min-width:0; font-family:Consolas,monospace; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

  /* ── bottom action bar ── */
  .foot { flex:0 0 70px; display:flex; align-items:center; gap:10px; padding:0 12px; border-top:1px solid var(--line); background:#fff; }
  .start { flex:0 0 140px; height:54px; font-size:21px; font-weight:700; background:var(--run); border:1px solid var(--run); color:#fff; cursor:pointer; }
  .start:disabled { background:#e4e6e8; border-color:#c9ccd0; color:#8b8f94; }
  .stop { flex:0 0 120px; height:54px; font-size:20px; font-weight:700; background:#fff; border:2px solid var(--ink); color:var(--ink); cursor:pointer; }
  .stop:disabled { border-color:#c9ccd0; color:#a3a6aa; background:#f3f3f3; font-weight:400; }
  .pause-btn { flex:0 0 120px; height:54px; font-size:18px; white-space:nowrap; padding:0 4px; font-weight:700; background:#fff; border:2px solid #b88a00; color:#6b4d00; cursor:pointer; }
  .pause-btn:disabled { border-color:#c9ccd0; color:#a3a6aa; background:#f3f3f3; font-weight:400; }
  .start.resume { background:#2f7a4b; border-color:#2f7a4b; }
  .step.pause { background:#ffe9a8; border-color:#b88a00; color:#5a4100; font-weight:700; }
  .prog .paused { color:#6b4d00; }
  .prog { flex:1; min-width:0; display:flex; gap:14px; align-items:center; }
  .prog > div { display:flex; flex-direction:column; line-height:1.2; min-width:0; }
  .prog small { font-size:12px; color:var(--ink-3); white-space:nowrap; }
  .prog b { font-size:16px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .prog .why { color:var(--ink-2); font-weight:400; font-size:14px; }
  .estop { flex:0 0 188px; display:flex; align-items:center; gap:8px; padding:6px 8px; border:2px solid var(--p1); background:#fff; font-size:13px; line-height:1.3; color:var(--ink); }
  .estop svg { flex:0 0 30px; }

  /* ── confirm/Numeric Keyboard Dialog Box ── */
  .overlay { position:absolute; inset:0; background:rgba(20,22,24,.45); z-index:40; }
  .dialog { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); width:440px; z-index:41; background:#fff; border:2px solid var(--ink); box-shadow:0 12px 40px rgba(0,0,0,.35); display:flex; flex-direction:column; }
  .dialog.danger { border-color:var(--p2); }
  .dlg-h { padding:12px 16px; font-size:20px; font-weight:700; border-bottom:1px solid var(--line); }
  .dialog.danger .dlg-h { background:#fff1e3; }
  .dlg-b { padding:10px 16px; display:flex; flex-direction:column; gap:6px; }
  .dlg-line { display:flex; justify-content:space-between; gap:12px; font-size:16px; padding:3px 0; border-bottom:1px dotted #d0d2d5; }
  .dlg-line > span { color:var(--ink-3); } .dlg-line b { text-align:right; }
  .dlg-warn { margin-top:6px; padding:8px 10px; font-size:14px; line-height:1.5; background:#fff7d6; border-left:5px solid var(--p3); overflow-wrap:anywhere; }
  .dlg-detail { margin-top:4px; padding:10px 12px; font-size:15px; line-height:1.55; background:#f6f6f7; border:1px solid var(--line); overflow-wrap:anywhere; white-space:pre-wrap; max-height:330px; overflow:hidden; }
  .dlg-detail.err { background:#fff4ea; border-left:5px solid var(--p2); }
  .banner .msg { cursor:pointer; text-decoration:underline dotted; text-underline-offset:4px; }
  .trow .c-msg.tap { cursor:pointer; }
  .msgchip { height:40px; max-width:330px; padding:0 12px; border:1px solid var(--line); background:#fff; font-size:14px; cursor:pointer;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; flex:0 1 auto; min-width:0; }
  .msgchip.err { border:2px solid var(--p2); color:#8a3d00; background:#fff4ea; font-weight:700; }
  .msgchip.ok { color:var(--ink-2); } .msgchip.busy { color:var(--run); }
  .prog > div.tap { cursor:pointer; }
  .dlg-buf { height:54px; border:2px solid var(--run); display:flex; align-items:center; justify-content:flex-end; gap:6px; padding:0 12px; font-family:var(--font-heading); font-size:32px; }
  .dlg-buf small { font-size:16px; color:var(--ink-3); font-family:var(--font-body); }
  .dlg-range { font-size:14px; color:var(--ink-3); display:flex; justify-content:space-between; }
  .dlg-range .err { color:var(--p2); font-weight:700; }
  .keys { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }
  .keys button { height:52px; font-size:24px; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .dlg-choices { display:flex; gap:8px; } .dlg-choices button { flex:1; height:60px; font-size:22px; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .dlg-choices button.sel { background:var(--run); color:#fff; border-color:var(--run); font-weight:700; }
  .dlg-f { display:flex; gap:10px; padding:12px 16px; border-top:1px solid var(--line); }
  .dlg-f button { flex:1; height:52px; font-size:18px; cursor:pointer; }
  .dlg-f .cancel { background:#fff; border:1px solid #8b8f94; }
  .dlg-f .ok { background:var(--run); border:1px solid var(--run); color:#fff; font-weight:700; }
  .dialog.danger .dlg-f .ok { background:var(--p2); border-color:var(--p2); }
  .dlg-f .ok:disabled { background:#c9ccd0; border-color:#c9ccd0; }
  .dlg-timeout { font-size:12px; color:var(--ink-3); padding:0 16px 8px; }
  .dlg-choices.grid { flex-wrap:wrap; } .dlg-choices.grid button { flex:0 0 calc(50% - 4px); height:52px; font-size:18px; }
  .dialog.hv .ack-row { margin-top:4px; min-height:48px; font-size:15px; }

  /* ── Schedule measurement ── */
  .sch-body { flex:1; min-height:0; display:flex; gap:8px; }
  .sch-left { flex:0 0 392px; display:flex; flex-direction:column; min-height:0; }
  .sch-right { flex:1; min-width:0; }
  .sch-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; padding:10px 12px; }
  .stile { height:80px; display:flex; flex-direction:column; align-items:flex-start; justify-content:center; gap:2px; padding:8px 12px; border:1px solid #8b8f94; background:#fff; text-align:left; cursor:pointer; min-width:0; }
  .stile > span { font-size:13px; color:var(--ink-2); } .stile > b { font-size:21px; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .stile > small { font-size:12px; color:var(--ink-3); max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .stile:disabled { cursor:default; background:#f6f6f6; color:var(--ink); } .stile.need { border:2px dashed #b88a00; } .stile.need b { color:#8a5b00; }
  .stile.hv { border:3px solid var(--p1); background:repeating-linear-gradient(135deg,#ffd000 0 9px,#1d1f20 9px 18px) left / 12px 100% no-repeat, #fff; padding-left:22px; }
  .stile.hv b { color:var(--p1); }
  .sch-est { padding:4px 12px 8px; }
  .sch-est .dlg-line { font-size:15px; }
  .sch-warn { margin:0 12px 10px; padding:8px 10px; font-size:14px; line-height:1.45; background:#fff7d6; border-left:5px solid var(--p3); }
  .sch-err { margin:0 12px 10px; padding:8px 10px; font-size:14px; line-height:1.45; background:#fff4ea; border-left:5px solid var(--p2); color:#8a3d00; font-weight:700; }
  .c-slot { flex:0 0 52px; } .c-sst { flex:0 0 136px; font-size:14px; } .c-hm { flex:0 0 48px; font-family:var(--font-heading); font-size:15px; }
  .c-plan { flex:0 0 104px; font-family:var(--font-heading); font-size:15px; }
  .sch-right .thead, .sch-right .trow { gap:6px; padding:0 8px; }
  .c-op { flex:0 0 80px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .st.skipped { border:1px dashed #b88a00; color:#6b4d00; background:#fff7d6; } .st.stopped { color:var(--ink-2); }
  .hvtag { flex:0 0 auto; padding:4px 8px; border:2px solid var(--p1); color:var(--p1); font-weight:700; font-size:14px; white-space:nowrap; }
  /* ── Maintenance login:PIN Numeric keys ── */
  .pin-wrap { flex:1; display:flex; flex-direction:column; align-items:center; gap:8px; padding:14px; }
  .pin-dots { width:360px; height:56px; border:2px solid var(--run); display:flex; align-items:center; justify-content:center; font-size:30px; letter-spacing:8px; background:#fff; }
  .pin-keys { display:grid; grid-template-columns:repeat(3, 104px); gap:8px; }
  .pin-keys button { height:62px; font-size:26px; border:1px solid #8b8f94; background:#fff; cursor:pointer; }
  .pin-foot { display:flex; gap:10px; } .pin-foot .btn { height:52px; min-width:120px; font-size:17px; }
</style>
```

# Appendix B. Verbatim template markup of `web/index.html`, lines 471-1039

`{{ x }}` = interpolation, `<sc-if value>` = conditional, `<sc-for list as>` = loop (see 1.1). Class strings such as `{{ sys.modeCls }}` are composed in the logic part; the compositions are listed in the sections above with their line numbers.

```html
<div class="{{ rootCls }}">

  <!-- ── top bar ── -->
  <div class="top">
    <div class="brandbox" onPointerDown="{{ hold.down }}" onPointerUp="{{ hold.up }}" onPointerLeave="{{ hold.up }}" onPointerCancel="{{ hold.up }}" onContextMenu="{{ hold.menu }}">
      <sc-if value="{{ brand.logo }}"><img class="brand-logo" src="{{ brand.logo }}" alt="{{ brand.company }}" draggable="false"></sc-if>
      <sc-if value="{{ brand.custom }}"><div class="title branded" data-notr="1" title="{{ brand.company }}">{{ brand.title }}</div></sc-if>
      <sc-if value="{{ brand.standard }}"><div class="title">online metal wastewater detector</div></sc-if>
    </div>
    <span class="{{ sys.modeCls }}">{{ sys.mode }}</span>
    <span style="flex:1;"></span>
    <span class="{{ sys.plcCls }}" title="{{ sys.plcTitle }}"><i></i>PLC {{ sys.plc }}</span>
    <span class="{{ sys.rpiCls }}" title="{{ sys.rpiTitle }}"><i></i>RPi {{ sys.rpi }}</span>
    <button class="{{ sys.opCls }}" onClick="{{ sys.opTap }}" title="Change operator"><small>operator</small><b data-notr="{{ sys.opNotr }}">{{ sys.opName }}</b></button>
    <sc-if value="{{ sys.maint }}"><button class="userbtn admin" onClick="{{ sys.maintTap }}" title="Maintenance settings">maintenance mode</button></sc-if>
    <div class="lang" data-notr="1"><sc-for list="{{ langs }}" as="l"><button class="{{ l.cls }}" onClick="{{ l.tap }}" title="{{ l.title }}">{{ l.label }}</button></sc-for></div>
    <div class="clock"><small>{{ clockDate }}</small>{{ clock }}</div>
  </div>

  <!-- ── Alert Banner ── -->
  <div class="{{ banner.cls }}">
    <sc-if value="{{ banner.has }}">
      <span class="pri">{{ banner.pri }}</span>
      <span class="time">{{ banner.time }}</span>
      <span class="msg" onClick="{{ banner.detail }}" title="Click to see the complete message">{{ banner.msg }}</span>
      <span class="more">{{ banner.more }}</span>
      <sc-if value="{{ banner.canAck }}"><button onClick="{{ banner.ack }}">confirm ACK</button></sc-if>
      <button onClick="{{ banner.view }}">Alarm list</button>
    </sc-if>
    <sc-if value="{{ banner.none }}"><span>No active alarm</span></sc-if>
  </div>

  <div class="body">
    <!-- ── Left navigation ── -->
    <div class="nav">
      <sc-for list="{{ navItems }}" as="n">
        <button class="{{ n.cls }}" onClick="{{ n.tap }}">{{ n.label }}<sc-if value="{{ n.badge }}"><span class="{{ n.badgeCls }}">{{ n.badge }}</span></sc-if></button>
      </sc-for>
    </div>

    <!-- Standard calibration: independent page (web/calibration) ,Only hides and does not reload when switching -->
    <div class="test-extension" style="display:{{ calDisplay }}; flex:1; min-width:0; min-height:0;">
      <iframe src="/calibration/" title="standard calibration" class="test-frame"></iframe>
    </div>

    <sc-if value="{{ testEnabled }}">
      <div class="test-extension" style="display:{{ testDisplay }}; flex:1; min-width:0; min-height:0;">
        <iframe src="/test/" title="equipment connection and spectrum test" class="test-frame"></iframe>
      </div>
    </sc-if>

    <!-- ══════════════ Overview ══════════════ -->
    <sc-if value="{{ page.dash }}">
      <div class="page">
        <div class="metals">
          <sc-for list="{{ metals }}" as="m">
            <div class="panel metal">
              <div class="metal-head"><span class="metal-name">{{ m.name }}</span><span class="{{ m.tagCls }}">{{ m.tag }}</span></div>
              <div class="{{ m.valCls }}"><b>{{ m.value }}</b><span>{{ m.sub }}</span></div>
            </div>
          </sc-for>
        </div>

        <div class="panel steps">
          <div class="ph"><h2>process stage</h2><span class="sub">{{ steps.source }}<span data-notr="1">{{ steps.sourceName }}</span></span><span class="sp"></span><span class="meta">{{ steps.meta }}</span></div>
          <sc-if value="{{ steps.has }}">
            <div class="steps-row">
              <sc-for list="{{ steps.list }}" as="s">
                <div class="{{ s.cls }}"><span class="no">{{ s.no }}</span>{{ s.label }}</div>
              </sc-for>
            </div>
          </sc-if>
          <sc-if value="{{ steps.empty }}"><div class="steps-empty">{{ steps.emptyText }}</div></sc-if>
        </div>

        <div class="panel pid">
          <div class="ph">
            <h2>flow path diagram</h2><span class="sub">{{ pid.path }}</span><span class="sp"></span>
            <div class="legend"><span><i class="lg-act"></i>current path</span><span><i class="lg-run"></i>operation(command)</span><span><i class="lg-off"></i>Stop</span><span><i class="lg-bad"></i>communication failure</span><span><i class="lg-na"></i>not connected</span></div>
          </div>
          <div class="pid-wrap">
            <div class="pid-box">
              <svg viewBox="0 0 880 300" width="880" height="300">
                <!-- pipeline: generally light gray thin line; current stage path and command ON is a dark thick line The pump program of -->
                <line class="{{ pid.s01 }}" x1="88" y1="170" x2="113" y2="170"></line><polygon class="{{ pid.a01 }}" points="104,165 113,170 104,175"></polygon>
                <line class="{{ pid.s02 }}" x1="150" y1="170" x2="175" y2="170"></line><polygon class="{{ pid.a02 }}" points="166,165 175,170 166,175"></polygon>
                <line class="{{ pid.s03 }}" x1="246" y1="170" x2="271" y2="170"></line><polygon class="{{ pid.a03 }}" points="262,165 271,170 262,175"></polygon>
                <line class="{{ pid.s04 }}" x1="308" y1="170" x2="333" y2="170"></line><polygon class="{{ pid.a04 }}" points="324,165 333,170 324,175"></polygon>
                <line class="{{ pid.s05 }}" x1="384" y1="38" x2="384" y2="51"></line>
                <line class="{{ pid.s06 }}" x1="384" y1="88" x2="384" y2="117"></line><polygon class="{{ pid.a06 }}" points="379,108 384,117 389,108"></polygon>
                <line class="{{ pid.s07 }}" x1="434" y1="170" x2="461" y2="170"></line><polygon class="{{ pid.a07 }}" points="452,165 461,170 452,175"></polygon>
                <line class="{{ pid.s08 }}" x1="498" y1="170" x2="525" y2="170"></line><polygon class="{{ pid.a08 }}" points="516,165 525,170 516,175"></polygon>
                <line class="{{ pid.s09 }}" x1="636" y1="170" x2="663" y2="170"></line><polygon class="{{ pid.a09 }}" points="654,165 663,170 654,175"></polygon>
                <line class="{{ pid.s10 }}" x1="700" y1="170" x2="727" y2="170"></line><polygon class="{{ pid.a10 }}" points="718,165 727,170 718,175"></polygon>
                <line class="{{ pid.s11 }}" x1="320" y1="264" x2="355" y2="264"></line><polygon class="{{ pid.a11 }}" points="346,259 355,264 346,269"></polygon>
                <line class="{{ pid.s12 }}" x1="448" y1="264" x2="413" y2="264"></line><polygon class="{{ pid.a12 }}" points="422,259 413,264 422,269"></polygon>
                <line class="{{ pid.s13 }}" x1="384" y1="244" x2="384" y2="223"></line><polygon class="{{ pid.a13 }}" points="379,232 384,223 389,232"></polygon>

                <!-- Containers and equipment: dark gray frame, no three-dimensional effect -->
                <path class="eq" d="M8 124 H88 V202 Q88 214 76 214 H20 Q8 214 8 202 Z"></path>
                <path class="eq" d="M176 124 H246 V202 Q246 214 234 214 H188 Q176 214 176 202 Z"></path>
                <path class="{{ pid.eqMix }}" d="M334 118 H434 V208 Q434 222 420 222 H348 Q334 222 334 208 Z"></path>
                <rect class="{{ pid.eqDet }}" x="526" y="134" width="110" height="72"></rect>
                <circle class="eq" cx="581" cy="98" r="19"></circle><line class="eq" x1="562" y1="98" x2="600" y2="98"></line><line class="eq" x1="581" y1="117" x2="581" y2="134"></line>
                <rect class="eq" x="728" y="140" width="68" height="60"></rect>
                <rect class="eq" x="344" y="2" width="80" height="36"></rect>
                <rect class="eq" x="250" y="248" width="70" height="34"></rect>
                <rect class="eq" x="448" y="248" width="70" height="34"></rect>
                <rect class="{{ pid.eqSp }}" x="356" y="244" width="56" height="40"></rect><path class="eq" d="M363 255 H405 M384 255 V278"></path>

                <!-- pump:ISA pump symbol (circle＋tangent outlet), running=solid, stop=hollow -->
                <circle class="{{ pid.c1 }}" cx="132" cy="170" r="18"></circle><line class="eq" x1="132" y1="152" x2="150" y2="152"></line>
                <circle class="{{ pid.c2 }}" cx="290" cy="170" r="18"></circle><line class="eq" x1="290" y1="152" x2="308" y2="152"></line>
                <circle class="{{ pid.c3 }}" cx="384" cy="70" r="18"></circle><line class="eq" x1="402" y1="70" x2="402" y2="88"></line>
                <circle class="{{ pid.c4 }}" cx="480" cy="170" r="18"></circle><line class="eq" x1="480" y1="152" x2="498" y2="152"></line>
                <circle class="{{ pid.c5 }}" cx="682" cy="170" r="18"></circle><line class="eq" x1="682" y1="152" x2="700" y2="152"></line>
                <text class="{{ pid.l1 }}" x="132" y="175">P1</text><text class="{{ pid.l2 }}" x="290" y="175">P2</text>
                <text class="{{ pid.l3 }}" x="384" y="75">P3</text><text class="{{ pid.l4 }}" x="480" y="175">P4</text><text class="{{ pid.l5 }}" x="682" y="175">P5</text>

                <!-- name and serial number -->
                <text class="nm" x="44" y="160">Wastewater sampling</text><text class="tg" x="44" y="178">T-01</text>
                <text class="nm" x="207" y="160">circulating cup</text><text class="tg" x="207" y="178">T-02</text>
                <text class="nm" x="380" y="158">mixing tank</text><text class="tg" x="380" y="176">T-03</text>
                <text class="nm" x="581" y="166">plasma detector</text>
                <text class="ptag" x="581" y="94" style="font-size:12px;">AE</text><text class="tg" x="581" y="112">101</text>
                <text class="nm" x="762" y="175">Waste discharge</text>
                <text class="nm" x="384" y="25">pure water DI</text>
                <text class="nm" x="285" y="263">Nitric acid</text><text class="tg" x="285" y="277">CH2</text>
                <text class="nm" x="483" y="263">Air</text><text class="tg" x="483" y="277">CH3</text>
                <text class="tg" x="132" y="140">sampling cycle</text><text class="tg" x="290" y="140">comes with mixing tank</text>
                <text class="tg" x="480" y="140">Send detector</text><text class="tg" x="682" y="140">waste pump</text>
                <text class="tg" x="360" y="74" style="text-anchor:end;">pure water pump</text>
                <text class="tg" x="384" y="298" style="fill:#1d1f20;">SP syringe pump · CH1 Drainage → mixing tank</text>
              </svg>

              <!-- weight/Liquid level analog strip: It is a slash when the sensor is not connected. -->
              <div class="bar" style="left:74px; top:136px; height:64px;" title="Liquid level is not connected"></div>
              <div class="bar" style="left:232px; top:136px; height:64px;" title="weight is not connected"></div>
              <div class="bar" style="left:420px; top:130px; height:78px;" title="weight is not connected"></div>
              <div class="pst q-na" style="left:336px; top:186px;">weight is not connected</div>
              <div class="{{ pid.detSt }}" style="left:528px; top:176px; width:106px;">{{ pid.det }}</div>

              <!-- Pump status: Display value box when running, display status text at other times -->
              <sc-for list="{{ pid.pumps }}" as="p">
                <sc-if value="{{ p.running }}"><div class="vbox" style="left:{{ p.vx }}; top:{{ p.vy }};"><b>{{ p.speed }}</b>{{ p.auto }}</div></sc-if>
                <sc-if value="{{ p.idle }}"><div class="{{ p.stCls }}" style="left:{{ p.sx }}; top:{{ p.sy }};">{{ p.state }}</div></sc-if>
                <button class="{{ p.hitCls }}" style="left:{{ p.hx }}; top:{{ p.hy }}; width:44px; height:44px; border-radius:50%;" onClick="{{ p.open }}" title="{{ p.title }}"></button>
              </sc-for>
              <button class="{{ pid.hitDet }}" style="left:524px; top:76px; width:114px; height:132px;" onClick="{{ pid.openDet }}" title="plasma detector AE-101 detailed status"></button>
              <button class="{{ pid.hitSp }}" style="left:354px; top:242px; width:60px; height:44px;" onClick="{{ pid.openSp }}" title="Syringe pump detailed status"></button>

              <!-- Alert mark: shape＋color＋priority number -->
              <sc-for list="{{ pid.marks }}" as="m">
                <div class="{{ m.cls }}" style="left:{{ m.x }}; top:{{ m.y }};"><i class="{{ m.pri }}"><b>{{ m.no }}</b></i>{{ m.text }}</div>
              </sc-for>
            </div>
          </div>
        </div>

        <div class="panel strip">
          <sc-for list="{{ cells }}" as="c">
            <div class="cell" title="{{ c.title }}"><small>{{ c.label }}</small><b class="{{ c.cls }}">{{ c.value }}</b></div>
          </sc-for>
        </div>
      </div>
    </sc-if>

    <!-- ══════════════ manual operation ══════════════ -->
    <sc-if value="{{ page.manual }}">
      <div class="page">
        <div class="{{ manual.noticeCls }}"><div class="n-body"><b>{{ manual.notice }}</b><sc-if value="{{ manual.noticeFix }}"><span class="n-fix">{{ manual.noticeFix }}</span></sc-if></div><sc-if value="{{ manual.noticeGo }}"><button class="btn" onClick="{{ manual.noticeGo }}">{{ manual.noticeGoLabel }}</button></sc-if></div>
        <div class="manual">
          <div class="panel pumps">
            <div class="ph">
              <h2>peristaltic pump P1–P6</h2><span class="sub">40008–40013</span><span class="sp"></span>
              <span class="sub">direction</span>
              <div class="seg">
                <button class="{{ manual.cwCls }}" onClick="{{ manual.setCW }}" disabled="{{ manual.settingsDisabled }}">CW</button>
                <button class="{{ manual.ccwCls }}" onClick="{{ manual.setCCW }}" disabled="{{ manual.settingsDisabled }}">CCW</button>
              </div>
              <button class="btn" style="height:36px;" onClick="{{ manual.setSpeed }}" disabled="{{ manual.settingsDisabled }}">speed {{ manual.speed }} rpm</button>
            </div>
            <sc-for list="{{ pumps }}" as="p">
              <div class="prow">
                <div class="nm"><b>{{ p.label }}</b><small>{{ p.addr }}</small></div>
                <span class="{{ p.tagCls }}">{{ p.state }}</span>
                <button class="{{ p.secCls }}" onClick="{{ p.setSecs }}" disabled="{{ p.secDisabled }}" title="{{ p.secTitle }}">{{ p.secLabel }}<small>{{ p.secSub }}</small></button>
                <button class="btn" onClick="{{ p.on }}" disabled="{{ p.onDisabled }}" title="{{ p.onTitle }}">ON</button>
                <button class="btn" onClick="{{ p.off }}" disabled="{{ p.offDisabled }}">OFF</button>
              </div>
            </sc-for>
          </div>

          <div class="side">
            <div class="panel">
              <div class="ph"><h2>syringe pump</h2><span class="sub">40001–40007</span><span class="sp"></span><span class="tag q-na">No position readback</span></div>
              <div class="syr">
                <div class="row"><span>channel</span>
                  <div class="seg">
                    <sc-for list="{{ channels }}" as="c"><button class="{{ c.cls }}" onClick="{{ c.tap }}" disabled="{{ c.disabled }}"><span>CH{{ c.no }}</span><small>{{ c.name }}</small></button></sc-for>
                  </div>
                </div>
                <div class="row"><span>volume</span>
                  <button class="btn" style="width:48px; height:46px; font-size:22px;" onClick="{{ syringe.minus }}" disabled="{{ syringe.disabled }}">−</button>
                  <button class="vol" onClick="{{ syringe.pad }}" disabled="{{ syringe.disabled }}">{{ syringe.ml }}<small>mL</small></button>
                  <button class="btn" style="width:48px; height:46px; font-size:22px;" onClick="{{ syringe.plus }}" disabled="{{ syringe.disabled }}">+</button>
                </div>
                <div class="acts">
                  <button class="btn" onClick="{{ syringe.aspirate }}" disabled="{{ syringe.disabled }}">Liquid extraction</button>
                  <button class="btn" onClick="{{ syringe.dispense }}" disabled="{{ syringe.disabled }}">Liquid injection</button>
                  <button class="btn" onClick="{{ syringe.reset }}" disabled="{{ syringe.disabled }}">reset</button>
                </div>
                <div class="fb">PLC Set amount: {{ syringe.plcVolume }}.40042–40044 Location/Completion readback has not been provided, and whether the action is completed must be confirmed on site.</div>
              </div>
            </div>
            <div class="panel" style="flex:1;">
              <div class="ph"><h2>No device installed</h2></div>
              <div class="na-list">V1 Wastewater inlet valve,V2 pure water valve,V3 The waste valve and mixing tank agitator have not been installed yet.HMI does not provide operations. Install and complete PLC Read back before adding.</div>
            </div>
          </div>
        </div>
      </div>
    </sc-if>

    <!-- ══════════════ Trend: Independent Page (web/trend) ,Sample and blank drawing ══════════════ -->
    <div class="test-extension" style="display:{{ trendDisplay }}; flex:1; min-width:0; min-height:0;">
      <iframe src="/trend/" title="concentration trend" class="test-frame"></iframe>
    </div>

    <!-- ══════════════ Schedule measurement ══════════════ -->
    <sc-if value="{{ page.schedule }}">
      <div class="page">
        <div class="sch-body">
          <div class="panel sch-left">
            <div class="ph"><h2>Schedule setting</h2><span class="sub">{{ sch.formSub }}</span></div>
            <div class="sch-grid">
              <sc-for list="{{ sch.tiles }}" as="t"><button class="{{ t.cls }}" onClick="{{ t.tap }}" disabled="{{ t.off }}"><span>{{ t.label }}</span><b data-notr="{{ t.notr }}">{{ t.value }}</b><small>{{ t.sub }}</small></button></sc-for>
            </div>
            <div class="sch-est"><sc-for list="{{ sch.est }}" as="l"><div class="dlg-line"><span>{{ l.k }}</span><b>{{ l.v }}</b></div></sc-for></div>
            <sc-if value="{{ sch.err }}"><div class="sch-err">{{ sch.err }}</div></sc-if>
            <sc-for list="{{ sch.warns }}" as="w"><div class="sch-warn">{{ w }}</div></sc-for>
          </div>
          <div class="panel sch-right table">
            <div class="ph"><h2>{{ sch.logTitle }}</h2><span class="sub" data-notr="{{ sch.logNotr }}">{{ sch.logSub }}</span><span class="sp"></span><span class="meta">{{ sch.counts }}</span></div>
            <div class="thead"><span class="c-slot">sequence</span><span class="c-plan">scheduled time</span><span class="c-sst">state</span><span class="c-hm">start</span><span class="c-hm">End</span><span class="c-msg">Remarks</span></div>
            <div class="tbody">
              <sc-for list="{{ sch.rows }}" as="r"><div class="trow"><span class="c-slot">{{ r.no }}</span><span class="c-plan">{{ r.planned }}</span><span class="c-sst"><span class="{{ r.stCls }}">{{ r.status }}</span></span><span class="c-hm">{{ r.started }}</span><span class="c-hm">{{ r.finished }}</span><span class="c-msg one" title="{{ r.note }}">{{ r.note }}</span></div></sc-for>
              <sc-if value="{{ sch.empty }}"><div class="empty" style="padding:30px;"><b>Scheduling has not started yet</b><p>Set on the left Recipe, Start time, interval and end conditions, then click ”Start Scheduling” at the bottom. Results for each period (including periods skipped due to timeouts) are listed here.</p></div></sc-if>
            </div>
            <div class="pager"><button class="btn" onClick="{{ sch.pg.prev }}" disabled="{{ sch.pg.prevOff }}">◀ Previous page</button><b>{{ sch.pg.label }}</b><button class="btn" onClick="{{ sch.pg.next }}" disabled="{{ sch.pg.nextOff }}">Next page ▶</button></div>
          </div>
        </div>
      </div>
    </sc-if>

    <!-- ══════════════ execution record (paging, no scrolling)══════════════ -->
    <sc-if value="{{ page.results }}">
      <div class="page">
        <div class="panel table">
          <div class="ph"><h2>execution record</h2><span class="sub">Measured and unmeasured periods (recent 200 pen)</span></div>
          <div class="thead"><span class="c-time">time</span><span class="c-op">operator</span><span class="c-recipe">Recipe</span><span class="c-slot">sequence</span><span class="c-hm">End</span><span class="c-sst">state</span><span class="c-conc">concentration</span><span class="c-err">Remarks</span></div>
          <div class="tbody">
            <sc-for list="{{ runs.items }}" as="r">
              <div class="trow"><span class="c-time">{{ r.time }}</span><span class="c-op" data-notr="1">{{ r.operator }}</span><span class="c-recipe" data-notr="1">{{ r.recipe }}</span><span class="c-slot">{{ r.slot }}</span><span class="c-hm">{{ r.finished }}</span><span class="c-sst"><span class="{{ r.stCls }}">{{ r.status }}</span></span><span class="c-conc"><span class="{{ r.concCls }}">{{ r.conc }}</span></span><span class="c-err" title="{{ r.error }}">{{ r.error }}</span></div>
            </sc-for>
            <sc-if value="{{ runs.empty }}"><div class="empty" style="padding:40px;"><b>There is no measurement record yet</b><p>After starting the schedule, each measurement and the period of time that was not measured due to timeout or suspension will be listed here.</p></div></sc-if>
          </div>
          <div class="pager"><button class="btn" onClick="{{ runs.pg.prev }}" disabled="{{ runs.pg.prevOff }}">◀ Previous page</button><b>{{ runs.pg.label }}</b><button class="btn" onClick="{{ runs.pg.next }}" disabled="{{ runs.pg.nextOff }}">Next page ▶</button></div>
        </div>
      </div>
    </sc-if>

    <!-- ══════════════ alarm (alarm list/operation event (two pages)══════════════ -->
    <sc-if value="{{ page.alarms }}">
      <div class="page">
        <div class="panel table">
          <div class="ph">
            <div class="seg tabs"><sc-for list="{{ alarmTabs }}" as="t"><button class="{{ t.cls }}" onClick="{{ t.tap }}">{{ t.label }}</button></sc-for></div>
            <sc-if value="{{ al.isAlarms }}">
              <div class="counts"><sc-for list="{{ alarmCounts }}" as="c"><span class="count"><i class="{{ c.cls }}"></i>{{ c.label }} {{ c.count }}</span></sc-for></div>
              <span class="sp"></span>
              <button class="btn" onClick="{{ ackAll }}" disabled="{{ ackAllDisabled }}">Confirm all</button>
            </sc-if>
            <sc-if value="{{ al.isEvents }}"><span class="sub">audit.jsonl recent 50 Pen</span></sc-if>
          </div>
          <sc-if value="{{ al.isAlarms }}">
            <div class="thead"><span class="c-time">Occurrence time</span><span class="c-pri">level</span><span class="c-area">area</span><span class="c-msg">message</span><span class="c-state">state</span><span class="c-act">action</span></div>
            <div class="tbody">
              <sc-for list="{{ al.rows }}" as="a">
                <div class="{{ a.rowCls }}"><span class="c-time">{{ a.time }}</span><span class="c-pri"><span class="{{ a.priCls }}">{{ a.pri }}</span></span><span class="c-area">{{ a.area }}</span><span class="c-msg one tap" title="Click to see the complete message" onClick="{{ a.detail }}">{{ a.msg }}</span><span class="c-state">{{ a.state }}</span><span class="c-act"><sc-if value="{{ a.canAck }}"><button class="ack" onClick="{{ a.ack }}">confirm</button></sc-if></span></div>
              </sc-for>
              <sc-if value="{{ alarmsEmpty }}"><div class="empty" style="padding:30px;"><b>No active alarm</b><p>Alarm conditions:HMI The server does not respond,PLC Communication interruption,PLC Command failure, process failure, pump stop not confirmed, execution of content measurement steps Recipe hour RPi is not connected.</p></div></sc-if>
            </div>
          </sc-if>
          <sc-if value="{{ al.isEvents }}">
            <div class="thead"><span class="c-time">time</span><span class="c-msg">event</span></div>
            <div class="tbody">
              <sc-for list="{{ al.rows }}" as="e"><div class="trow slim"><span class="c-time">{{ e.time }}</span><span class="c-msg one" title="{{ e.msg }}" data-notr="1">{{ e.msg }}</span></div></sc-for>
            </div>
          </sc-if>
          <div class="pager"><button class="btn" onClick="{{ al.pg.prev }}" disabled="{{ al.pg.prevOff }}">◀ Previous page</button><b>{{ al.pg.label }}</b><button class="btn" onClick="{{ al.pg.next }}" disabled="{{ al.pg.nextOff }}">Next page ▶</button></div>
        </div>
      </div>
    </sc-if>

    <!-- ══════════════ Administrator settings: log in first, then perform paging operations after logging in The official reference starting value for ══════════════ -->
    <sc-if value="{{ page.admin }}">
      <div class="page">
        <!-- Not logged in: only the login area is displayed -->
        <sc-if value="{{ adm.locked }}">
          <div class="panel login">
            <div class="ph"><h2>Maintenance login</h2><span class="sub">calibration, spectral raw data, plasma parameters,Recipe, RPi with operator list for maintenance personnel only</span></div>
            <sc-if value="{{ adm.notConfigured }}">
              <div class="empty"><b>has not been set for maintenance PIN</b><p>Please execute on the computer Set-Admin-Password.cmd settings 6–8 Digital repair PIN , restart HMI.</p><button class="btn" onClick="{{ adm.cancel }}">Return to overview</button></div>
            </sc-if>
            <sc-if value="{{ adm.canLogin }}">
              <div class="pin-wrap">
                <div class="pin-dots">{{ adm.pwShown }}</div>
                <div class="login-err">{{ adm.loginMsg }}</div>
                <sc-if value="{{ adm.pinMode }}"><div class="pin-keys"><sc-for list="{{ adm.pinKeys }}" as="k"><button onClick="{{ k.tap }}">{{ k.label }}</button></sc-for></div></sc-if>
                <sc-if value="{{ adm.kbMode }}"><div class="kb"><sc-for list="{{ adm.kb.rows }}" as="row"><div class="kb-row"><sc-for list="{{ row.keys }}" as="k"><button class="{{ k.cls }}" onClick="{{ k.tap }}">{{ k.label }}</button></sc-for></div></sc-for></div></sc-if>
                <div class="pin-foot">
                  <button class="btn" onClick="{{ adm.cancel }}">Return</button>
                  <button class="btn" onClick="{{ adm.toggleKb }}">{{ adm.kbLabel }}</button>
                  <button class="btn primary" onClick="{{ adm.login }}" disabled="{{ adm.loginOff }}">log in</button>
                </div>
              </div>
            </sc-if>
          </div>
        </sc-if>

        <!-- Login: Pagination -->
        <sc-if value="{{ adm.open }}">
          <div class="adm-bar">
            <div class="seg tabs"><sc-for list="{{ adm.tabs }}" as="t"><button class="{{ t.cls }}" onClick="{{ t.tap }}">{{ t.label }}</button></sc-for></div>
            <span class="sp" style="flex:1;"></span>
            <sc-if value="{{ adm.hasMsg }}"><button class="{{ adm.msgCls }}" onClick="{{ adm.msgTap }}" title="Click to see the complete message">{{ adm.msgShort }}</button></sc-if>
            <button class="btn" onClick="{{ adm.logout }}">Sign out</button>
          </div>

          <!-- Recipe -->
          <sc-if value="{{ adm.isRecipe }}">
            <div class="adm-body">
              <div class="panel adm-left">
                <div class="ph"><h2>Published Recipe</h2></div>
                <div class="list">
                  <sc-for list="{{ rcp.items }}" as="r"><button class="{{ r.cls }}" onClick="{{ r.tap }}"><b data-notr="1">{{ r.name }}</b><span>{{ r.sub }}</span></button></sc-for>
                  <sc-if value="{{ rcp.empty }}"><div class="empty" style="padding:16px;"><p>No published yet Recipe,Please import CSV.</p></div></sc-if>
                </div>
                <div class="pager"><button class="btn" onClick="{{ rcp.pg.prev }}" disabled="{{ rcp.pg.prevOff }}">◀</button><b>{{ rcp.pg.label }}</b><button class="btn" onClick="{{ rcp.pg.next }}" disabled="{{ rcp.pg.nextOff }}">▶</button></div>
                <div class="adm-foot"><label class="btn file-btn">Import CSV files<input type="file" accept=".csv,text/csv" onChange="{{ rcp.onFile }}"></label></div>
              </div>
              <div class="panel adm-right">
                <div class="ph"><h2 data-notr="{{ rcp.titleNotr }}">{{ rcp.title }}</h2><span class="sub">{{ rcp.titleSub }}</span></div>
                <sc-if value="{{ rcp.hasDetail }}">
                  <div class="kv">
                    <sc-for list="{{ rcp.facts }}" as="f"><div><span>{{ f.k }}</span><b class="{{ f.cls }}" data-notr="{{ f.notr }}">{{ f.v }}</b></div></sc-for>
                  </div>
                  <div class="thead"><span class="c-no">List</span><span class="c-act2">action</span><span class="c-tgt">target</span><span class="c-val">value</span><span class="c-msg">note</span></div>
                  <div class="tbody">
                    <sc-for list="{{ rcp.rows }}" as="c"><div class="trow slim" data-notr="1"><span class="c-no">{{ c.no }}</span><span class="c-act2">{{ c.action }}</span><span class="c-tgt">{{ c.target }}</span><span class="c-val">{{ c.value }}</span><span class="c-msg one" title="{{ c.note }}">{{ c.note }}</span></div></sc-for>
                  </div>
                  <div class="pager"><button class="btn" onClick="{{ rcp.rowPg.prev }}" disabled="{{ rcp.rowPg.prevOff }}">◀ Previous page</button><b>{{ rcp.rowPg.label }}</b><button class="btn" onClick="{{ rcp.rowPg.next }}" disabled="{{ rcp.rowPg.nextOff }}">Next page ▶</button></div>
                  <div class="adm-foot">
                    <sc-for list="{{ rcp.actions }}" as="a"><button class="{{ a.cls }}" onClick="{{ a.tap }}" disabled="{{ a.off }}">{{ a.label }}</button></sc-for>
                  </div>
                </sc-if>
                <sc-if value="{{ rcp.noDetail }}"><div class="empty"><b>Please select from the left Recipe</b><p>can be set as schedule after selection. Recipe”or delete (need to re-enter for maintenance PIN) ; new Recipe Please use ”Import CSV file” will be released after passing the verification.</p></div></sc-if>
              </div>
            </div>
          </sc-if>

          <!-- RPi connection -->
          <sc-if value="{{ adm.isRpi }}">
            <div class="adm-body">
              <div class="panel adm-half">
                <div class="ph"><h2>RPi state</h2><span class="sub">{{ rpi.host }}</span></div>
                <div class="kv big"><sc-for list="{{ rpi.facts }}" as="f"><div><span>{{ f.k }}</span><b class="{{ f.cls }}">{{ f.v }}</b></div></sc-for></div>
              </div>
              <div class="panel adm-half">
                <div class="ph"><h2>Operation</h2></div>
                <div class="btn-grid"><sc-for list="{{ rpi.actions }}" as="a"><button class="{{ a.cls }}" onClick="{{ a.tap }}" disabled="{{ a.off }}">{{ a.label }}<small>{{ a.sub }}</small></button></sc-for></div>
                <div class="adm-note">{{ rpi.note }}</div>
              </div>
            </div>
          </sc-if>

          <!-- Operator list -->
          <sc-if value="{{ adm.isOps }}">
            <div class="panel adm-full table">
              <div class="ph"><h2>Operator list</h2><span class="sub">The operator needs to select a name before starting the schedule. Each measurement and operation event will be recorded who performed it.</span></div>
              <div class="thead"><span class="c-slot">#</span><span class="c-msg">Name</span><span class="c-act">action</span></div>
              <div class="tbody">
                <sc-for list="{{ ops.items }}" as="o"><div class="trow"><span class="c-slot">{{ o.no }}</span><span class="c-msg" data-notr="1">{{ o.name }}</span><span class="c-act"><button class="ack" onClick="{{ o.remove }}">delete</button></span></div></sc-for>
                <sc-if value="{{ ops.empty }}"><div class="empty" style="padding:24px;"><b>No operator yet</b><p>Please add at least one operator, otherwise the schedule cannot be started.</p></div></sc-if>
              </div>
              <div class="pager"><button class="btn" onClick="{{ ops.pg.prev }}" disabled="{{ ops.pg.prevOff }}">◀ Previous page</button><b>{{ ops.pg.label }}</b><button class="btn" onClick="{{ ops.pg.next }}" disabled="{{ ops.pg.nextOff }}">Next page ▶</button></div>
              <div class="adm-foot"><span class="sub" style="flex:1;">On-screen keyboard can input job number/English; please use the physical keyboard to enter or edit the Chinese name data\operators.json.</span><button class="btn primary" onClick="{{ ops.add }}">New operator</button></div>
            </div>
          </sc-if>

          <!-- plasma parameters -->
          <sc-if value="{{ adm.isPlasma }}">
            <div class="panel adm-full">
              <div class="ph"><h2>plasma parameters</h2><span class="sub">Click to modify the value; when saving HSPY Must OFF, zero output,Nano is idle and will not start high voltage</span></div>
              <div class="param-grid"><sc-for list="{{ prm.items }}" as="p"><button class="{{ p.cls }}" onClick="{{ p.tap }}"><span>{{ p.label }}</span><b>{{ p.value }}</b><small>{{ p.sub }}</small></button></sc-for><button class="{{ fsum.cls }}" onClick="{{ fsum.tap }}" title="{{ fsum.note }}"><span>Spectral grouping addition (photo)</span><b>{{ fsum.value }}</b><small>{{ fsum.sub }}</small></button></div>
              <div class="adm-foot"><span class="sub" style="flex:1;">{{ prm.note }}</span><sc-for list="{{ prm.actions }}" as="a"><button class="{{ a.cls }}" onClick="{{ a.tap }}" disabled="{{ a.off }}">{{ a.label }}</button></sc-for></div>
            </div>
          </sc-if>

          <!-- correction coefficient -->
          <sc-if value="{{ adm.isCal }}">
            <div class="panel adm-full">
              <div class="ph"><h2>Cu/Zn/Ni correction coefficient</h2><span class="sub">y = (a × x + b) × c; is currently not verified as a standard and does not display the official concentration.</span></div>
              <div class="cal-grid">
                <span></span><span class="cal-h">a</span><span class="cal-h">b</span><span class="cal-h">c</span>
                <sc-for list="{{ cal.rows }}" as="r"><b class="cal-m">{{ r.metal }}</b><sc-for list="{{ r.cells }}" as="c"><button class="{{ c.cls }}" onClick="{{ c.tap }}">{{ c.value }}</button></sc-for></sc-for>
              </div>
              <div class="adm-foot"><span class="sub" style="flex:1;">{{ cal.note }}</span><sc-for list="{{ cal.actions }}" as="a"><button class="{{ a.cls }}" onClick="{{ a.tap }}" disabled="{{ a.off }}">{{ a.label }}</button></sc-for></div>
            </div>
          </sc-if>

          <!-- spectrum -->
          <sc-if value="{{ adm.isSpec }}">
            <div class="adm-body">
              <div class="panel spec-list">
                <div class="ph"><h2>spectral group</h2><span class="sp"></span><button class="btn" style="height:34px;" onClick="{{ spec.reload }}">Refresh</button></div>
                <div class="list">
                  <sc-for list="{{ spec.items }}" as="s">
                    <button class="{{ s.cls }}" onClick="{{ s.tap }}"><b>{{ s.time }}</b><span><span data-notr="1">{{ s.name }}</span>{{ s.sub }}</span><sc-if value="{{ s.warn }}"><span class="warn">{{ s.warn }}</span></sc-if></button>
                  </sc-for>
                  <sc-if value="{{ spec.empty }}"><div class="empty" style="padding:16px 10px;"><p>plasma_run will be automatically deposited after completion;RPi can be imported using the button below. Is the</p></div></sc-if>
                </div>
                <div class="pager"><button class="btn" onClick="{{ spec.pg.prev }}" disabled="{{ spec.pg.prevOff }}">◀</button><b>{{ spec.pg.label }}</b><button class="btn" onClick="{{ spec.pg.next }}" disabled="{{ spec.pg.nextOff }}">▶</button></div>
                <div class="adm-foot"><button class="btn" style="flex:1; justify-content:center;" onClick="{{ spec.openImport }}">from RPi Import…</button></div>
              </div>

              <div class="panel spec-main">
                <div class="ph"><h2>{{ spec.title }}</h2><span class="sub"><span data-notr="1">{{ spec.titleName }}</span>{{ spec.titleSub }}</span></div>
                <sc-if value="{{ spec.has }}">
                  <div class="spec-ctl">
                    <div class="seg"><sc-for list="{{ spec.views }}" as="v"><button class="{{ v.cls }}" onClick="{{ v.tap }}">{{ v.label }}</button></sc-for></div>
                    <span class="spec-info">background <b>{{ spec.bgN }}</b> Zhang · Signal <b>{{ spec.sigN }}</b> Zhang · Points <b>{{ spec.integ }}</b> · <b>{{ spec.dir }}</b></span>
                  </div>
                  <sc-if value="{{ spec.isChart }}">
                    <div class="spec-ctl">
                      <div class="seg"><sc-for list="{{ spec.modes }}" as="m"><button class="{{ m.cls }}" onClick="{{ m.tap }}" disabled="{{ m.disabled }}">{{ m.label }}</button></sc-for></div>
                      <sc-if value="{{ spec.showNav }}"><div class="fnav"><button class="btn" onClick="{{ spec.prev }}">◀</button><b>No. {{ spec.frameNo }} / {{ spec.sigN }} open</b><button class="btn" onClick="{{ spec.next }}">▶</button></div></sc-if>
                      <span class="sp" style="flex:1;"></span>
                      <div class="seg"><sc-for list="{{ spec.ranges }}" as="r"><button class="{{ r.cls }}" onClick="{{ r.tap }}">{{ r.label }}</button></sc-for></div>
                    </div>
                    <div class="chart">
                      <sc-for list="{{ chart.yt }}" as="t"><div class="ytk" style="top:{{ t.y }};">{{ t.label }}</div></sc-for>
                      <svg viewBox="0 0 566 282" width="566" height="282">
                        <sc-for list="{{ chart.grid }}" as="g"><line class="sl-grid" x1="0" x2="566" y1="{{ g }}" y2="{{ g }}"></line></sc-for>
                        <sc-if value="{{ chart.zero }}"><line class="sl-zero" x1="0" x2="566" y1="{{ chart.zeroY }}" y2="{{ chart.zeroY }}"></line></sc-if>
                        <sc-for list="{{ chart.refs }}" as="f"><line class="sl-ref" x1="{{ f.x }}" x2="{{ f.x }}" y1="0" y2="282"></line></sc-for>
                        <sc-for list="{{ chart.lines }}" as="l"><polyline class="{{ l.cls }}" points="{{ l.pts }}"></polyline></sc-for>
                      </svg>
                      <sc-for list="{{ chart.refs }}" as="f"><div class="ref" style="left:{{ f.left }};">{{ f.label }}</div></sc-for>
                      <sc-for list="{{ chart.xt }}" as="t"><div class="xtk" style="left:{{ t.x }};">{{ t.label }}</div></sc-for>
                      <div class="unit">nm</div>
                      <div class="lgd">{{ chart.legend }}</div>
                      <sc-if value="{{ chart.msg }}"><div class="msg">{{ chart.msg }}</div></sc-if>
                    </div>
                  </sc-if>
                  <sc-if value="{{ spec.isQc }}">
                    <div class="thead"><span class="c-no">Zhang Ci</span><span class="c-num">peak</span><span class="c-num">Peak wavelength</span><span class="c-num">total intensity</span><span class="c-num">difference from median</span><span class="c-flag">remind (difference &gt; 20% or below background)</span></div>
                    <div class="tbody">
                      <sc-for list="{{ spec.qc }}" as="q"><div class="{{ q.cls }}" onClick="{{ q.tap }}"><span class="c-no">{{ q.no }}</span><span class="c-num">{{ q.peak }}</span><span class="c-num">{{ q.peakNm }}</span><span class="c-num">{{ q.total }}</span><span class="c-num">{{ q.dev }}</span><span class="c-flag">{{ q.flag }}</span></div></sc-for>
                      <sc-if value="{{ spec.noFrames }}"><div class="empty" style="padding:14px;"><p>This group does not have single data (old version of records or too many pictures), and only the average spectrum can be viewed.</p></div></sc-if>
                    </div>
                    <div class="pager"><button class="btn" onClick="{{ spec.qcPg.prev }}" disabled="{{ spec.qcPg.prevOff }}">◀ Previous page</button><b>{{ spec.qcPg.label }}</b><button class="btn" onClick="{{ spec.qcPg.next }}" disabled="{{ spec.qcPg.nextOff }}">Next page ▶</button></div>
                  </sc-if>
                </sc-if>
                <sc-if value="{{ spec.none }}"><div class="empty"><b>{{ spec.noneTitle }}</b><p>{{ spec.noneText }}</p></div></sc-if>
              </div>
            </div>

            <sc-if value="{{ imp.open }}">
              <div class="imp" role="dialog" aria-label="from RPi Import spectrum">
                <div class="ph"><h2>from RPi Import spectrum</h2><span class="sub">{{ imp.root }}</span><span class="sp"></span><button class="btn" style="height:36px;" onClick="{{ imp.reload }}">Reread</button><button class="btn" style="height:36px;" onClick="{{ imp.close }}">closure</button></div>
                <sc-if value="{{ imp.msg }}"><div class="notice" style="margin:8px 10px 0;">{{ imp.msg }}</div></sc-if>
                <div class="thead"><span class="c-time">folder time</span><span class="c-job">folder</span><span class="c-steps">signal</span><span class="c-status">state</span><span class="c-act"></span></div>
                <div class="tbody">
                  <sc-for list="{{ imp.jobs }}" as="j">
                    <div class="trow"><span class="c-time">{{ j.time }}</span><span class="c-job" title="{{ j.dir }}">{{ j.name }}</span><span class="c-steps">{{ j.frames }}</span><span class="c-status">{{ j.state }}</span><span class="c-act"><sc-if value="{{ j.can }}"><button class="ack" onClick="{{ j.tap }}" disabled="{{ j.busy }}">Import</button></sc-if></span></div>
                  </sc-for>
                </div>
                <div class="pager"><button class="btn" onClick="{{ imp.pg.prev }}" disabled="{{ imp.pg.prevOff }}">◀ Previous page</button><b>{{ imp.pg.label }}</b><button class="btn" onClick="{{ imp.pg.next }}" disabled="{{ imp.pg.nextOff }}">Next page ▶</button></div>
              </div>
            </sc-if>
          </sc-if>
        </sc-if>
      </div>
    </sc-if>
  </div>

  <!-- ── bottom action bar ── -->
  <div class="foot">
    <button class="{{ ctl.startCls }}" onClick="{{ ctl.start }}" disabled="{{ ctl.startDisabled }}" title="{{ ctl.startWhy }}">{{ ctl.startLabel }}</button>
    <button class="pause-btn" onClick="{{ ctl.pause }}" disabled="{{ ctl.pauseDisabled }}">{{ ctl.pauseLabel }}</button>
    <button class="stop" onClick="{{ ctl.stop }}" disabled="{{ ctl.stopDisabled }}">Stop STOP</button>
    <div class="prog">
      <sc-if value="{{ ctl.showWhy }}"><div class="tap" onClick="{{ ctl.whyTap }}"><small>cannot start</small><b class="why">{{ ctl.startWhy }}</b></div></sc-if>
      <sc-if value="{{ ctl.showRun }}">
        <div><small>Schedule</small><b>{{ run.sched }}</b></div>
        <div><small>{{ run.nextLabel }}</small><b>{{ run.next }}</b></div>
        <div><small>stage</small><b>{{ run.phase }}</b></div>
        <div><small>Not measured</small><b>{{ run.skipped }}</b></div>
      </sc-if>
      <sc-if value="{{ ctl.hv }}"><span class="hvtag" title="Schedules to automatically output high voltage for each measurement">⚡ contains high voltage</span></sc-if>
      <sc-if value="{{ ctl.showMsg }}"><div class="tap" onClick="{{ ctl.msgTap }}"><small>message</small><b class="why">{{ ctl.msg }}</b></div></sc-if>
    </div>
    <div class="estop" title="STOP is a software stop, not a safety level emergency stop.">
      <svg viewBox="0 0 32 32" width="30" height="30"><circle cx="16" cy="16" r="14" fill="#c62828"></circle><circle cx="16" cy="16" r="8" fill="#e0b400"></circle></svg>
      <span>For emergency stop, please press on the device<b>Physical emergency stop button</b></span>
    </div>
  </div>

  <!-- ── Device panel (Faceplate) : Point flow diagram device is enabled ── -->
  <sc-if value="{{ face.open }}">
    <div class="face" role="dialog" aria-label="{{ face.title }}">
      <div class="face-h"><b>{{ face.title }}</b><button onClick="{{ face.close }}" title="closure">✕</button></div>
      <div class="face-b">
        <sc-for list="{{ face.rows }}" as="r"><div class="face-row"><span>{{ r.k }}</span><span class="{{ r.cls }}">{{ r.v }}</span></div></sc-for>
        <sc-if value="{{ face.note }}"><div class="face-note">{{ face.note }}</div></sc-if>
      </div>
      <div class="face-f">
        <sc-if value="{{ face.isPump }}">
          <button class="btn" onClick="{{ face.on }}" disabled="{{ face.onDisabled }}" title="{{ face.onTitle }}">ON</button>
          <button class="btn" onClick="{{ face.off }}" disabled="{{ face.offDisabled }}">OFF</button>
        </sc-if>
        <button class="btn" onClick="{{ face.go }}">{{ face.goLabel }}</button>
      </div>
    </div>
  </sc-if>

  <!-- ── confirm/input dialog box ── -->
  <sc-if value="{{ dlg.open }}">
    <div class="overlay" onClick="{{ dlg.cancel }}"></div>
    <div class="{{ dlg.cls }}" role="dialog" aria-modal="true" aria-label="{{ dlg.title }}">
      <div class="dlg-h">{{ dlg.title }}</div>
      <div class="dlg-b">
        <sc-for list="{{ dlg.lines }}" as="l"><div class="dlg-line"><span>{{ l.k }}</span><b data-notr="{{ l.notr }}">{{ l.v }}</b></div></sc-for>
        <sc-if value="{{ dlg.isChoice }}">
          <div class="{{ dlg.choiceCls }}"><sc-for list="{{ dlg.choices }}" as="c"><button class="{{ c.cls }}" onClick="{{ c.tap }}" data-notr="{{ c.notr }}">{{ c.label }}</button></sc-for></div>
        </sc-if>
        <sc-if value="{{ dlg.isPad }}">
          <div class="dlg-buf">{{ dlg.buf }}<small>{{ dlg.unit }}</small></div>
          <div class="dlg-range"><span>{{ dlg.range }}</span><span class="err">{{ dlg.err }}</span></div>
          <div class="keys"><sc-for list="{{ dlg.keys }}" as="k"><button onClick="{{ k.tap }}">{{ k.label }}</button></sc-for></div>
        </sc-if>
        <sc-if value="{{ dlg.isText }}">
          <div class="login-row" style="padding:0;"><div class="login-buf">{{ dlg.textShown }}</div><sc-if value="{{ dlg.canShow }}"><button class="btn" onClick="{{ dlg.toggleShow }}">{{ dlg.showLabel }}</button></sc-if></div>
          <div class="kb"><sc-for list="{{ dlg.kb.rows }}" as="row"><div class="kb-row"><sc-for list="{{ row.keys }}" as="k"><button class="{{ k.cls }}" onClick="{{ k.tap }}">{{ k.label }}</button></sc-for></div></sc-for></div>
        </sc-if>
        <sc-if value="{{ dlg.detail }}"><div class="{{ dlg.detailCls }}">{{ dlg.detail }}</div></sc-if>
        <sc-if value="{{ dlg.warn }}"><div class="dlg-warn">{{ dlg.warn }}</div></sc-if>
        <sc-for list="{{ dlg.acks }}" as="a"><button class="{{ a.cls }}" onClick="{{ a.tap }}"><span class="box">{{ a.mark }}</span>{{ a.label }}</button></sc-for>
      </div>
      <div class="dlg-f">
        <sc-if value="{{ dlg.showCancel }}"><button class="cancel" onClick="{{ dlg.cancel }}">Cancel</button></sc-if>
        <button class="ok" onClick="{{ dlg.confirm }}" disabled="{{ dlg.confirmDisabled }}">{{ dlg.confirmLabel }}</button>
      </div>
      <div class="dlg-timeout">{{ dlg.timeoutText }}</div>
    </div>
  </sc-if>
</div>
```

# Appendix C. Verbatim CSS of the side pages

## C.1 `web/calibration/style.css`

```css
:root {
  --run: #2f5f8f; --run-bg: #dce9f6; --bad: #a3168e; --bad-bg: #fbe8f7; --warn: #8a5b00; --warn-bg: #fff4d6;
  --ink: #1d1f20; --ink-2: #45484b; --ink-3: #6b6e72; --line: #b9bcc0; --panel: #fbfbfb; --bg: #eef0f2;
  --ok: #3d7a52;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink);
  font: 15px "Microsoft JhengHei", "Noto Sans TC", sans-serif; }
button { font-family: inherit; }
button:focus-visible { outline: 3px solid var(--run); outline-offset: 2px; }
.shell { height: 100%; display: flex; flex-direction: column; padding: 8px 10px; gap: 8px; position: relative; }
.sp { flex: 1; }
.sub { font-size: 13px; color: var(--ink-3); }
.btn { height: 40px; padding: 0 14px; border: 1px solid #8b8f94; background: #fff; font-size: 16px; cursor: pointer;
  color: var(--ink); white-space: nowrap; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn.primary { background: var(--run); border-color: var(--run); color: #fff; font-weight: 700; }
.btn.primary:disabled { background: #b9c6d3; border-color: #b9c6d3; opacity: 1; }
.btn.danger { border-color: #c62828; color: #c62828; font-weight: 700; }
.btn.big { height: 56px; font-size: 20px; padding: 0 26px; }
.panel { background: var(--panel); border: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
.ph { display: flex; align-items: center; gap: 10px; padding: 6px 12px; border-bottom: 1px solid var(--line); min-height: 44px; }
.ph h2 { margin: 0; font-size: 17px; white-space: nowrap; }
.ph .sub { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }

.tabs { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
.tab { position: relative; height: 40px; padding: 0 18px; border: 1px solid var(--line); background: #fff; font-size: 16px; cursor: pointer; }
.tab.sel { background: var(--run); border-color: var(--run); color: #fff; font-weight: 700; }
.badge { position: absolute; right: -6px; top: -6px; width: 20px; height: 20px; line-height: 20px; border-radius: 10px;
  background: #e06a00; color: #fff; font-size: 13px; font-weight: 700; text-align: center; }
.active-line { font-size: 13px; color: var(--ink-2); max-width: 470px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pane { display: none; flex: 1; min-height: 0; flex-direction: column; gap: 8px; }
.pane.sel { display: flex; }

/* setup */
.setup { flex: 1; min-height: 0; display: flex; gap: 8px; }
.std { flex: 1 1 auto; min-width: 0; }
.side { flex: 0 0 320px; }
.grid-head, .lvl { display: flex; align-items: center; gap: 6px; padding: 0 10px; }
.grid-head { height: 32px; font-size: 13px; color: var(--ink-3); border-bottom: 1px solid var(--line); }
.levels { flex: 1; overflow-y: auto; }
.lvl { height: 50px; border-bottom: 1px solid #e3e4e6; }
.lvl.blank { background: #f2f5f8; }
.c-name { flex: 1 1 120px; min-width: 0; }
.c-num { flex: 0 0 92px; text-align: center; }
.c-rounds { flex: 0 0 64px; text-align: center; }
.c-del { flex: 0 0 40px; }
.lvl input { width: 100%; height: 38px; border: 1px solid #8b8f94; padding: 0 8px; font-size: 16px; background: #fff; }
.lvl input.num { text-align: right; }
.lvl input.bad { border-color: var(--bad); background: var(--bad-bg); }
.lvl .del { width: 40px; height: 38px; padding: 0; font-size: 18px; }
.std-foot, .side-foot { display: flex; gap: 8px; align-items: center; padding: 8px 10px; border-top: 1px solid var(--line); }
.side-foot .btn { flex: 1; }
.form { flex: 1; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 10px; }
.form label { display: flex; flex-direction: column; gap: 4px; font-size: 14px; color: var(--ink-2); }
.form select { height: 40px; font-size: 16px; border: 1px solid #8b8f94; background: #fff; }
.hint { margin: 0; font-size: 12.5px; color: var(--ink-3); line-height: 1.45; }
.stepper { display: flex; align-items: center; gap: 0; }
.stepper .btn { width: 46px; padding: 0; font-size: 20px; }
.stepper b { width: 56px; text-align: center; font-size: 20px; }
.summary { border: 1px solid var(--line); background: #fff; padding: 6px 10px; font-size: 14px; }
.summary div { display: flex; justify-content: space-between; padding: 2px 0; }
.warnings div { font-size: 13px; color: var(--warn); background: var(--warn-bg); border: 1px solid #e6c56b; padding: 5px 8px; margin-bottom: 4px; }
.error { font-size: 13.5px; color: var(--bad); background: var(--bad-bg); border: 1px dashed var(--bad); padding: 6px 8px; }

/* run */
.prompt { display: flex; align-items: center; gap: 16px; padding: 12px 16px; background: var(--warn-bg); border: 2px solid #e06a00; }
.prompt > div { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.prompt small { color: #a34d00; font-weight: 700; }
.prompt b { font-size: 19px; line-height: 1.35; }
.run-panel { flex: 1; }
.run-levels { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.run-lvl { min-width: 128px; border: 1px solid var(--line); background: #fff; padding: 6px 10px; display: flex; flex-direction: column; gap: 2px; }
.run-lvl b { font-size: 15px; }
.run-lvl small { font-size: 12.5px; color: var(--ink-3); }
.run-lvl.now { border: 2px solid var(--run); background: var(--run-bg); }
.run-lvl.done { border-color: var(--ok); }
.run-lvl.done small { color: var(--ok); }
.run-log { flex: 1; overflow-y: auto; padding: 6px 12px; font-size: 13.5px; }
.run-log div { padding: 3px 0; border-bottom: 1px solid #eceded; display: flex; gap: 10px; }
.run-log time { color: var(--ink-3); flex: 0 0 64px; font-variant-numeric: tabular-nums; }

/* results */
.results { flex: 1; min-height: 0; display: flex; gap: 8px; }
.rec-list { flex: 0 0 236px; }
.records { flex: 1; overflow-y: auto; }
.rec { display: block; width: 100%; text-align: left; padding: 8px 10px; border: 0; border-bottom: 1px solid #e3e4e6; background: #fff; cursor: pointer; }
.rec.sel { background: var(--run-bg); box-shadow: inset 4px 0 0 var(--run); }
.rec b { display: block; font-size: 14px; }
.rec small { font-size: 12px; color: var(--ink-3); }
.st { display: inline-block; font-size: 12px; padding: 1px 6px; border: 1px solid var(--line); margin-right: 4px; }
.st.analyzed { border-color: var(--ok); color: var(--ok); }
.st.applied { background: var(--ok); border-color: var(--ok); color: #fff; }
.st.analysis_failed, .st.failed, .st.interrupted { border-color: var(--bad); color: var(--bad); }
.st.running, .st.analyzing { border-color: var(--run); color: var(--run); }
.rec-detail { flex: 1; min-width: 0; }
.rec-body { flex: 1; overflow-y: auto; padding: 8px 12px; display: flex; flex-direction: column; gap: 10px; }
.metal-tabs { display: flex; gap: 6px; }
.metal-tabs .btn { min-width: 76px; }
.metal-tabs .btn.sel { background: var(--run); border-color: var(--run); color: #fff; font-weight: 700; }
.fit { display: flex; gap: 12px; }
.fit svg { flex: 0 0 360px; height: 240px; background: #fff; border: 1px solid var(--line); }
.coef { flex: 1; display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
.coef div { display: flex; justify-content: space-between; border-bottom: 1px solid #eceded; padding: 3px 0; }
.coef b { font-variant-numeric: tabular-nums; }
.coef .formula { font-size: 13px; color: var(--ink-2); border: 0; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }
.tbl th, .tbl td { border: 1px solid #dfe1e3; padding: 3px 6px; text-align: right; font-variant-numeric: tabular-nums; }
.tbl th { background: #f2f3f4; color: var(--ink-2); font-weight: 600; }
.tbl td.l, .tbl th.l { text-align: left; }
.tbl tr.bad td { color: var(--bad); }
.note { font-size: 12.5px; color: var(--ink-3); line-height: 1.5; }
.empty { padding: 30px 16px; color: var(--ink-3); text-align: center; }
svg text { font: 11px "Microsoft JhengHei", sans-serif; fill: var(--ink-3); }

/* dialog / toast */
.dialog { position: absolute; inset: 0; background: rgba(20, 22, 24, .45); display: flex; align-items: center; justify-content: center; z-index: 10; }
.dialog[hidden] { display: none; }
.dlg { width: 520px; max-width: 94%; background: #fff; border: 1px solid var(--line); padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.dlg h3 { margin: 0; font-size: 18px; }
#dlg-body { font-size: 14.5px; line-height: 1.55; }
#dlg-body .hv { color: #c62828; font-weight: 700; }
.dlg-foot { display: flex; gap: 10px; justify-content: flex-end; }
.dlg-foot .btn { min-width: 110px; }
.toast { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); max-width: 80%; background: #2b2e31; color: #fff;
  padding: 10px 16px; font-size: 14.5px; z-index: 20; }
.toast[hidden] { display: none; }
```

## C.2 `web/trend/style.css`

```css
:root {
  --run: #2f5f8f; --blank: #c27a00; --bad: #a3168e; --bad-bg: #fbe8f7; --ink: #1d1f20; --ink-2: #45484b; --ink-3: #6b6e72;
  --line: #b9bcc0; --panel: #fbfbfb; --bg: #eef0f2;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink); font: 15px "Microsoft JhengHei", "Noto Sans TC", sans-serif; }
button { font-family: inherit; }
.shell { height: 100%; display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; position: relative; }
.bar { display: flex; align-items: center; gap: 10px; }
.bar h2 { margin: 0; font-size: 18px; white-space: nowrap; }
.sp { flex: 1; }
.btn { height: 38px; padding: 0 12px; border: 1px solid #8b8f94; background: #fff; font-size: 15px; cursor: pointer; color: var(--ink); white-space: nowrap; }
.btn.primary { background: var(--run); border-color: var(--run); color: #fff; font-weight: 700; }
.seg { display: inline-flex; }
.seg .btn + .btn { border-left: 0; }
.seg .btn.sel { background: var(--run); border-color: var(--run); color: #fff; font-weight: 700; }
.legend { font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 4px; white-space: nowrap; }
.legend i { display: inline-block; width: 18px; height: 10px; margin-left: 8px; }
.lg-sample { border-top: 3px solid var(--run); }
.lg-blank { border-top: 3px dashed var(--blank); }
.lg-bad { width: 10px !important; height: 10px; background: var(--bad); transform: rotate(45deg); }
.notice { font-size: 13.5px; color: #7a5200; background: #fff4d6; border: 1px solid #e6c56b; padding: 6px 10px; }
.charts { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; }
.chart { flex: 1; min-height: 0; background: var(--panel); border: 1px solid var(--line); position: relative; }
.chart svg { position: absolute; inset: 0; width: 100%; height: 100%; }
svg text { font: 11px "Microsoft JhengHei", sans-serif; fill: var(--ink-3); }
svg .title { font-size: 13px; font-weight: 700; fill: var(--ink); }
svg .title.warn { fill: var(--bad); }
svg .empty { font-size: 13px; fill: var(--ink-3); }
.foot { display: flex; align-items: center; gap: 10px; }
.detail { flex: 1; min-width: 0; font-size: 13.5px; background: #fff; border: 1px solid var(--line); padding: 8px 10px; min-height: 38px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dialog { position: absolute; inset: 0; background: rgba(20, 22, 24, .45); display: flex; align-items: center; justify-content: center; z-index: 10; }
.dlg { max-height: calc(100% - 20px); overflow-y: auto; width: 440px; background: #fff; border: 1px solid var(--line); padding: 14px 18px; display: flex; flex-direction: column; gap: 8px; }
.dlg h3 { margin: 0; font-size: 18px; }
.dlg label { display: flex; justify-content: space-between; align-items: center; gap: 10px; font-size: 14px; color: var(--ink-2); }
.dlg input { width: 120px; height: 36px; border: 1px solid #8b8f94; font-size: 16px; text-align: right; padding: 0 8px; }
.hint { margin: 0; font-size: 13px; color: var(--ink-3); line-height: 1.5; }
.error { font-size: 13.5px; color: var(--bad); background: var(--bad-bg); border: 1px dashed var(--bad); padding: 6px 8px; }
.dlg-foot { display: flex; justify-content: flex-end; gap: 10px; }
.dlg-foot .btn { min-width: 100px; }
.dlg.wide { width: 560px; }
.dlg .sec { font-size: 14px; font-weight: 700; margin-top: 4px; border-bottom: 1px solid #e3e5e7; padding-bottom: 2px; }
.dlg input.dt { width: 240px; text-align: left; font-size: 15px; }
.quick { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink-3); }
.quick .btn { height: 32px; font-size: 13.5px; padding: 0 9px; }
.vgrid { display: grid; grid-template-columns: 40px 110px 110px 1fr; gap: 6px 8px; align-items: center; }
.vgrid input { width: 110px; }
.vgrid .vh { font-size: 12.5px; color: var(--ink-3); }
.vgrid .vi { font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.zgrid { display: grid; grid-template-columns: 40px 60px 1fr 1fr; gap: 6px 8px; align-items: center; }
.zgrid input[type=checkbox], .dlg input.cb { width: 22px; height: 22px; }
.view-chip { flex: 0 1 auto; min-width: 0; max-width: 460px; font-size: 13.5px; color: var(--run); border: 1px solid var(--run);
  padding: 6px 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.quick .btn.sel { background: var(--run); border-color: var(--run); color: #fff; font-weight: 700; }
```

## C.3 `web/workflow/styles.css`

```css
/* Same palette and type scale as the main HMI (web/index.html). */
:root {--run:#2f5f8f;--ink:#1d1f20;--ink-2:#45484b;--ink-3:#6b6e72;--line:#b9bcc0;--p2:#e06a00;--p3:#e0b400;
  font-family:"Barlow","Microsoft JhengHei","Noto Sans TC",sans-serif;color:var(--ink);background:#fbfbfb;font-size:15px}
* {box-sizing:border-box}
body {margin:0;padding:12px 16px 20px}
header {display:flex;align-items:center;justify-content:space-between;gap:14px;border-bottom:2px solid var(--ink-2);padding-bottom:8px}
h1 {font-size:21px;margin:0}
header p {margin:2px 0 0;color:var(--ink-3);font-size:13px}
header strong {font-size:13px;font-weight:600;color:#6b4d00;border:1px solid #b88a00;background:#fff7d6;padding:6px 10px;white-space:nowrap}
#notice {min-height:22px;margin:8px 0;color:var(--ink-2);font-size:14px}
#notice.error {color:#8a3d00;font-weight:700;border-left:5px solid var(--p2);padding-left:8px}
section {border:1px solid var(--line);background:#fff;margin-top:10px;padding:10px 12px}
section h2 {font-size:17px;margin:0 0 6px}
section p {margin:6px 0;line-height:1.5}
.hint {font-size:13px;color:var(--ink-3)}
.run-actions {display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0}
button, .btn {height:40px;padding:0 14px;border:1px solid #8b8f94;background:#fff;font:15px "Barlow","Microsoft JhengHei",sans-serif;color:var(--ink);cursor:pointer;display:inline-flex;align-items:center;text-decoration:none;white-space:nowrap}
button:disabled {color:#a3a6aa;border-color:#d0d2d5;background:#f3f3f3;cursor:not-allowed}
button:focus-visible, .btn:focus-within, input:focus, select:focus, textarea:focus {outline:3px solid var(--run);outline-offset:1px}
.primary, #publish, #run, #calibration-save {background:var(--run);border-color:var(--run);color:#fff;font-weight:700}
.primary:disabled, #publish:disabled, #run:disabled, #calibration-save:disabled {background:#b9c6d3;border-color:#b9c6d3;color:#fff}
#stop {border:2px solid var(--ink);font-weight:700}
input, select {height:40px;padding:0 10px;font:15px "Barlow","Microsoft JhengHei",sans-serif;color:var(--ink);background:#fff;border:1px solid #8b8f94}
input[type=file] {display:none}
#file-name {font-size:14px;color:var(--ink-3)}
#password, #recipe-name, #rpi-password {width:min(260px,100%)}
#recipes {min-width:260px;max-width:100%}
#isolated {width:20px;height:20px;vertical-align:middle;margin:0 6px 0 0}
label {font-size:15px}
#auth-state {font-weight:700}
textarea {display:block;width:100%;min-height:150px;resize:vertical;border:1px solid #8b8f94;background:#fff;padding:9px;font:13px/1.45 Consolas,monospace;color:var(--ink)}
#physical-reason {color:#6b4d00;background:#fff7d6;border-left:5px solid var(--p3);padding:6px 10px}
#run-status {font-weight:700;color:var(--run)}
#run-events {max-height:120px;overflow:auto;background:#f1f2f3;padding:5px 8px;font:13px Consolas,monospace}
#run-events p {margin:3px 0}
.parameter-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:8px 0}
.parameter-grid label {font-size:13px;color:var(--ink-2)}
.parameter-grid input {display:block;width:100%;margin-top:4px}
.calibration-grid {display:grid;grid-template-columns:56px repeat(3,minmax(110px,1fr));align-items:center;gap:10px;padding:5px 0;border-top:1px solid #dcdee0}
.calibration-grid strong {font:16px Consolas,monospace}
.calibration-grid input {width:100%}
.calibration-heading {font-size:13px;color:var(--ink-3)}
.actions {display:flex;align-items:center;justify-content:space-between;gap:8px;padding-top:10px}
#calibration-notice.error {color:#8a3d00}
dialog {border:2px solid var(--p2);padding:0;width:440px;box-shadow:0 12px 40px rgba(0,0,0,.35)}
dialog::backdrop {background:rgba(20,22,24,.45)}
dialog h3 {margin:0;padding:12px 16px;font-size:19px;background:#fff1e3;border-bottom:1px solid var(--line)}
dialog p {margin:0;padding:12px 16px;font-size:15px;line-height:1.55}
dialog .dlg-f {display:flex;gap:10px;padding:12px 16px;border-top:1px solid var(--line)}
dialog .dlg-f button {flex:1;height:50px;justify-content:center;font-size:17px}
dialog #confirm-ok {background:var(--p2);border-color:var(--p2);color:#fff;font-weight:700}
```

## C.4 `web/test/style.css` (line 1 is minified in the source)

```css
:root{--bg:#f2f2f3;--surface:#e9e9ea;--ink:#1d1f20;--muted:#71777d;--blue:#5980a6;--darkblue:#416180;--line:#ccd0d4;--green:#387357;--red:#aa5148;--amber:#967021;font-family:'Segoe UI','Microsoft JhengHei',sans-serif;color:var(--ink);background:#d9dadc;font-size:14px}*{box-sizing:border-box}body{margin:0}button,input,.button{font:inherit}button,.button{min-height:43px;border:1px solid var(--line);padding:9px 15px;background:transparent;color:var(--ink);cursor:pointer;border-radius:2px;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:7px}button:hover:not(:disabled),.button:hover{background:#e1eaf2;border-color:var(--blue)}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #84afd4;outline-offset:2px}button:disabled,.disabled{opacity:.45;cursor:not-allowed;pointer-events:none}.primary{background:var(--blue);border-color:var(--darkblue);color:white}.primary:hover:not(:disabled){background:var(--darkblue);color:white}.shell{max-width:1440px;min-width:760px;min-height:768px;height:100dvh;margin:auto;background:var(--bg);display:flex;flex-direction:column;border-left:1px solid var(--line);border-right:1px solid var(--line)}header{height:64px;flex-shrink:0;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 18px;gap:20px}.brand{display:flex;gap:13px;align-items:center;font-size:18px;font-weight:600}.brand>b{font-size:29px;border:1px solid var(--blue);padding:1px 6px;color:var(--darkblue);font-weight:400}.brand small{display:block;font-size:9px;letter-spacing:1.3px;margin-top:3px;font-weight:400;color:var(--muted)}.badge{border:1px solid var(--line);padding:5px 10px;font-size:13px;white-space:nowrap}.badge.good{color:var(--green);border-color:#a0b9ac}.badge.bad{color:var(--red);border-color:#cfa8a3}.header-right{margin-left:auto;text-align:right;font-size:11px;color:var(--muted)}#clock{display:block;font-size:19px;letter-spacing:1px;color:var(--ink);font-variant-numeric:tabular-nums}.banner{min-height:34px;padding:7px 18px;background:#e4ecf3;border-bottom:1px solid var(--line);color:var(--darkblue);font-size:12px;overflow-wrap:anywhere}.banner.error{background:#f5e6e3;color:#8e3c35}.banner.demo{background:#f1e9d7;color:#765a18}.body{display:flex;flex:1;min-height:0}nav{flex:0 0 136px;background:#e9e9eb;border-right:1px solid var(--line);display:flex;flex-direction:column;padding-top:14px}.nav{border:0;border-left:3px solid transparent;display:block;width:100%;text-align:left;padding:13px 11px 13px 19px;border-radius:0;font-size:17px;min-height:67px;position:relative}.nav>span{font-size:9px;color:var(--muted);position:absolute;right:12px;top:17px}.nav small{display:block;font-size:9px;letter-spacing:1.3px;color:var(--muted);margin-top:4px}.nav.active{background:#dce6ef;border-left-color:var(--blue);color:var(--darkblue)}.nav-foot{margin-top:auto;padding:17px;font-size:10px;line-height:1.8;color:var(--muted);border-top:1px solid var(--line)}main{flex:1;min-width:0;overflow:auto;padding:20px 22px}.page{display:none}.page.active{display:block}.page-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:17px}.page-head small,.callout small{color:var(--muted);font-size:9px;letter-spacing:1.8px}.page-head h1{font-size:24px;font-weight:500;margin:4px 0 0}.page-head>span{font-size:11px;color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:15px}.metric{padding:15px 13px 13px;background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--blue)}.metric label{display:block;font-size:12px;color:#555d65}.metric strong{display:block;font-size:29px;font-weight:400;line-height:1.6;font-variant-numeric:tabular-nums;white-space:nowrap}.metric em{font-style:normal;font-size:13px;margin-left:7px;color:var(--muted)}.metric small{font-size:10px;color:var(--muted)}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px}.panel{border:1px solid var(--line);margin-bottom:15px;background:#f7f7f8;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:center;padding:11px 14px;border-bottom:1px solid var(--line);background:#e9e9ec}.panel-head h2{font-size:14px;margin:0;font-weight:600}.panel-head>span{font-size:9px;letter-spacing:1px;color:var(--muted)}dl{margin:0;padding:3px 14px}dl>div{display:flex;justify-content:space-between;gap:10px;align-items:center;min-height:34px;border-bottom:1px solid #e2e3e5;font-size:11px}dl>div:last-child{border-bottom:0}dt{color:var(--muted)}dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}.muted{color:var(--muted)}.callout{border:1px solid #b2c5d7;background:#e7eef5;padding:15px 17px;display:flex;align-items:center;justify-content:space-between;gap:12px}.callout h2{font-size:17px;margin:4px 0 5px;font-weight:500}.callout p{font-size:11px;margin:0;color:#65707c}.note{font-size:11px;line-height:1.8;color:var(--muted);margin:12px 0;overflow-wrap:anywhere}.padded{padding:18px}.padded h2{font-size:17px;margin:0 0 12px}.padded p{line-height:1.8;font-size:13px}.disabled-actions{display:flex;gap:9px;flex-wrap:wrap}.spectrum-toolbar{display:flex;justify-content:space-between;margin-bottom:12px;gap:10px}#frame-buttons{display:flex;gap:5px}.selected{background:#e1eaf3;border-color:var(--blue);color:var(--darkblue)}.chart-panel{margin-bottom:0}.chart-wrap{height:310px;position:relative;padding:8px}canvas{width:100%;height:100%;display:block}#chart-empty{position:absolute;inset:0;display:flex;justify-content:center;align-items:center;flex-direction:column;color:var(--muted);font-size:18px}#chart-empty small{font-size:12px;margin-top:10px}.spectrum-summary{display:flex;justify-content:space-between;padding:14px 0;font-size:11px;gap:8px}.spectrum-summary b{margin-left:5px;font-weight:500;font-variant-numeric:tabular-nums}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}th{text-align:left;background:#e9e9ec;color:var(--muted);font-size:11px;font-weight:500;white-space:nowrap}td,th{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}td{overflow-wrap:anywhere}td.error{color:var(--red)}.empty{text-align:center;padding:45px;color:var(--muted)}.job-id{font-family:Consolas,monospace;font-size:10px;max-width:170px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 18px}.form-grid label{font-size:11px;color:#515c68}.form-grid input{display:block;width:100%;margin-top:5px;height:36px;min-height:36px;padding:7px 9px;background:#fff;border:1px solid var(--line);color:var(--ink);font-size:12px}.form-actions{display:flex;gap:10px;align-items:center;margin-top:18px}.form-actions>span{font-size:11px;color:var(--muted)}footer{flex-shrink:0;min-height:61px;border-top:1px solid var(--line);display:flex;align-items:center;gap:18px;padding:8px 18px;background:#e9e9eb;font-size:11px}.footer-status{display:flex;align-items:center;gap:7px;min-width:145px}.dot{width:8px;height:8px;background:#979b9e;display:inline-block}.dot.good{background:var(--green)}#job-status{flex:1;overflow-wrap:anywhere}#toast{position:fixed;right:24px;bottom:78px;max-width:520px;padding:16px 22px;background:#3b4249;color:white;box-shadow:0 5px 20px #0003;z-index:10;overflow-wrap:anywhere;font-size:13px;line-height:1.6}#toast[hidden]{display:none}@media(max-width:900px){header{gap:10px}.brand{font-size:15px}.header-right>span{font-size:9px}main{padding:15px}nav{flex-basis:115px}.metric{padding:10px}.metric strong{font-size:23px}.two-col{gap:10px}.panel-head{padding:10px}dl{padding:3px 10px}dl>div{font-size:10px}}@media(min-height:900px){.chart-wrap{height:420px}.metric{padding-top:22px;padding-bottom:22px}dl>div{min-height:40px}}
/* Embedded test extension: original HMI owns the header, main navigation and footer. */
body{background:#f2f2f3}
.shell{min-width:0;min-height:0;max-width:none;height:100dvh;border:0}
.shell>header{display:none}
.body{flex-direction:column}
nav{flex:0 0 auto;flex-direction:row;padding:6px 10px;gap:5px;border-right:0;border-bottom:1px solid var(--line)}
.nav{min-height:43px;width:auto;flex:1;padding:6px 10px;font-size:14px;border:1px solid var(--line);text-align:center}
.nav>span,.nav-foot{display:none}
.nav small{font-size:9px;margin-top:2px}
.nav.active{border:1px solid var(--blue)}
main{padding:13px 15px}
.page-head{margin-bottom:12px}
.page-head h1{font-size:21px}
.metric{padding:9px 11px}
.metric strong{font-size:25px}
.panel{margin-bottom:10px}
.callout{padding:10px 14px}
.chart-wrap{height:255px}
footer{min-height:46px;padding:5px 12px}
.form-grid{gap:9px 14px}
.form-grid input{height:33px;min-height:33px}
.form-actions{margin-top:12px}
.padded{padding:14px}
```

## C.5 `lite/style.css`

```css
:root{color-scheme:dark;font-family:system-ui,"Microsoft JhengHei",sans-serif;background:#101820;color:#eef5f8}*{box-sizing:border-box}body{margin:0}header{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:16px 28px;border-bottom:1px solid #344553;background:#172530}header strong{font-size:21px}header small{display:block;color:#91aabb;font-size:12px}nav{display:flex;gap:8px}button{border:1px solid #507087;border-radius:7px;padding:9px 14px;background:#253b4b;color:#edf7fc;font-size:14px;cursor:pointer}button:hover{background:#315268}button.selected,button.primary{background:#157c91;border-color:#26adc3;color:#fff}.danger{border-color:#b45757;color:#ffb8b8}a{color:#7bd2e6}main{max-width:1180px;margin:auto;padding:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}article{background:#1a2a36;border:1px solid #344c5b;border-radius:10px;padding:20px;margin-bottom:18px}h2{font-size:18px;margin:0 0 15px}.actions,.inline{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.actions{margin:12px 0}label{display:inline-flex;gap:8px;align-items:center}input,select,textarea{background:#0e1b25;color:#f3fbff;border:1px solid #577184;border-radius:6px;padding:8px;font:inherit}input[type=number]{width:100px}input[type=password]{width:180px}input[type=file]{max-width:280px}.hint{font-size:13px;color:#9cb1be;line-height:1.5}.status{line-height:1.8;white-space:pre-line}.pump-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.pump{border:1px solid #39505e;padding:12px;border-radius:8px}.pump b{display:block;font-size:17px}.pump span{display:block;margin:5px 0 10px;color:#b9cbd5}.pump button{padding:7px 10px;margin:2px}pre{background:#0f1b23;border-radius:7px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto}#notice{min-height:27px;margin-bottom:10px;color:#9de0e9}#notice.error{color:#ffb3b3}textarea{display:block;width:100%;height:310px;margin-top:12px;font-family:Consolas,monospace;font-size:13px;line-height:1.45}.check{margin-top:8px;color:#ffd89b}.check input{accent-color:#d99b42}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;border-bottom:1px solid #344a58;padding:7px}code{color:#95d9e9}#flow-events{max-height:520px}@media(max-width:800px){.grid{grid-template-columns:1fr}.pump-grid{grid-template-columns:repeat(2,1fr)}header{flex-wrap:wrap}main{padding:12px}}
.pump-settings{margin:12px 0 6px}
.parameter-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}.parameter-grid label{display:flex;flex-direction:column;align-items:flex-start;gap:5px;color:#c5dae5}.parameter-grid input{width:100%;max-width:210px}#spectrum-canvas{display:block;width:100%;height:auto;background:#0f1b23;border:1px solid #39505e;border-radius:7px;margin-top:14px}.metal-readbacks{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px}.metal{min-width:180px;background:#112633;border:1px solid #42687c;border-radius:8px;padding:13px}.metal b,.metal strong,.metal span{display:block}.metal strong{font:700 29px Consolas,monospace;color:#81ddec;margin:5px 0}.metal span{font-size:12px;color:#b8ccd7}
```

# Appendix D. Legacy fragments and customisation examples (verbatim)

## D.1 `manual-page.html`

```html
    <!-- ══════════════ manual mode ══════════════ -->
    <sc-if value="{{ page.manual }}" hint-placeholder-val="{{ true }}">
      <div style="flex:1; display:flex; flex-direction:column; min-width:0; padding:9px 12px; gap:8px;">
        <div style="flex:0 0 34px; display:flex; align-items:center; gap:8px; border:1px solid oklch(0.62 0.10 78); padding:0 10px;">
          <span style="width:10px; height:10px; display:block; background:oklch(0.62 0.10 78);"></span>
          <span style="font-size:12px;">{{ manualNotice }}</span>
        </div>

        <div style="flex:1; display:flex; gap:9px; min-height:0;">
          <div class="blueprint" style="flex:1; border:1px solid var(--color-divider); display:flex; flex-direction:column; min-width:0; overflow:hidden;">
            <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
            <div style="padding:8px 16px 6px; border-bottom:1px solid var(--color-divider); display:flex; justify-content:space-between; align-items:baseline;">
              <span style="font-family:var(--font-heading); font-size:19px;">peristaltic pump <span style="font-size:13px; font-family:var(--font-body); letter-spacing:0.09em; color:color-mix(in srgb, var(--color-text) 70%, transparent);">PUMPS · 40008–40012</span></span>
              <div style="display:flex; align-items:center; gap:5px; font-size:12px;">
                <button style="height:29px; padding:0 7px; background:transparent; border:1px solid var(--color-divider); color:var(--color-text); cursor:pointer;" onClick="{{ manualSetDirection }}" disabled="{{ manualSettingsDisabled }}">direction {{ manualDirection }}</button>
                <button style="height:29px; padding:0 7px; background:transparent; border:1px solid var(--color-divider); color:var(--color-text); cursor:pointer;" onClick="{{ manualSetSpeed }}" disabled="{{ manualSettingsDisabled }}">speed {{ manualSpeed }} RPM</button>
              </div>
            </div>
            <sc-for list="{{ manualPumps }}" as="p" hint-placeholder-count="5">
              <div style="flex:1; display:flex; align-items:center; gap:12px; padding:0 16px; border-bottom:1px solid var(--color-divider);">
                <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="{{ p.icon }}" stroke-width="1.5" style="flex:0 0 26px;"><circle cx="12" cy="12" r="8"></circle><path d="M9 8l7 4-7 4z"></path></svg>
                <div style="flex:1; min-width:0;">
                  <div style="font-family:var(--font-heading); font-size:19px; line-height:1.15;">{{ p.name }}</div>
                  <div style="font-size:12px; letter-spacing:0.08em; color:color-mix(in srgb, var(--color-text) 70%, transparent);">{{ p.addr }} · {{ p.btn }}</div>
                </div>
                <div style="flex:0 0 58px; text-align:right;">
                  <div style="font-family:var(--font-heading); font-size:19px; line-height:1;">{{ p.secs }}</div>
                  <div style="font-size:11px; color:color-mix(in srgb, var(--color-text) 70%, transparent);">Second SEC</div>
                </div>
                <button style="flex:0 0 48px; height:46px; background:var(--color-accent-100); border:1px solid var(--color-accent); color:var(--color-text); font-family:var(--font-heading); font-size:17px; cursor:pointer;" onClick="{{ p.on }}" disabled="{{ p.onDisabled }}">ON</button>
                <button style="flex:0 0 48px; height:46px; background:transparent; border:1px solid var(--color-divider); color:var(--color-text); font-family:var(--font-heading); font-size:17px; cursor:pointer;" onClick="{{ p.off }}" disabled="{{ p.offDisabled }}">OFF</button>
              </div>
            </sc-for>
          </div>

          <div style="flex:0 0 318px; display:flex; flex-direction:column; gap:9px; min-height:0; overflow:hidden;">
            <div class="blueprint" style="border:1px solid var(--color-divider); display:flex; flex-direction:column;">
              <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
              <div style="padding:8px 16px 6px; border-bottom:1px solid var(--color-divider); font-family:var(--font-heading); font-size:19px;">solenoid valve <span style="font-size:13px; font-family:var(--font-body); letter-spacing:0.09em; color:color-mix(in srgb, var(--color-text) 70%, transparent);">VALVES · 40016 / 40017 / 40033</span></div>
              <sc-for list="{{ manualValves }}" as="v" hint-placeholder-count="3">
                <div style="display:flex; align-items:center; gap:11px; padding:6px 16px; border-bottom:1px solid var(--color-divider);">
                  <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="{{ v.icon }}" stroke-width="1.5" style="flex:0 0 24px;"><path d="M4 7l8 5-8 5z"></path><path d="M20 7l-8 5 8 5z"></path><path d="M12 12V5M9 4h6"></path></svg>
                  <div style="flex:1;">
                    <div style="font-family:var(--font-heading); font-size:18px; line-height:1.15;">{{ v.name }}</div>
                    <div style="font-size:12px; letter-spacing:0.08em; color:color-mix(in srgb, var(--color-text) 70%, transparent);">{{ v.addr }}</div>
                  </div>
                  <button style="flex:0 0 88px; height:44px; background:{{ v.bg }}; border:1px solid {{ v.bd }}; color:{{ v.fg }}; font-family:var(--font-heading); font-size:17px; letter-spacing:0.04em; cursor:pointer;" style-hover="border-color:var(--color-accent)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">{{ v.btn }}</button>
                </div>
              </sc-for>
            </div>

            <div class="blueprint" style="flex:1; border:1px solid var(--color-divider); display:flex; flex-direction:column; min-height:0;">
              <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
              <div style="padding:8px 16px 6px; border-bottom:1px solid var(--color-divider); display:flex; align-items:baseline; gap:10px;">
                <span style="font-family:var(--font-heading); font-size:19px;">syringe pump <span style="font-size:13px; font-family:var(--font-body); letter-spacing:0.09em; color:color-mix(in srgb, var(--color-text) 70%, transparent);">SYRINGE · 40001–40007</span></span>
                <span style="flex:1;"></span>
                <span style="font-family:var(--font-heading); font-size:17px; color:var(--color-accent-700); white-space:nowrap;">{{ syringe.status }}</span>
              </div>
              <div style="padding:10px 16px; display:flex; flex-direction:column; gap:8px;">
                <div style="display:flex; gap:8px;">
                  <sc-for list="{{ syringeChannels }}" as="c" hint-placeholder-count="3">
                    <button style="flex:1; height:44px; background:{{ c.bg }}; border:1px solid {{ c.bd }}; color:{{ c.fg }}; font-family:var(--font-heading); font-size:18px; cursor:pointer; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:1px;" style-hover="border-color:var(--color-accent)" onClick="{{ c.tap }}" disabled="{{ c.disabled }}">
                      <span>{{ c.name }}</span>
                      <span style="font-family:var(--font-body); font-size:11px; letter-spacing:0.08em;">CH{{ c.no }}</span>
                    </button>
                  </sc-for>
                </div>
                <div style="display:flex; align-items:center; gap:10px;">
                  <span style="font-size:14px; flex:0 0 58px;">volume</span>
                  <button style="width:44px; height:44px; background:transparent; border:1px solid var(--color-divider); font-family:var(--font-heading); font-size:24px; color:var(--color-text); cursor:pointer;" onClick="{{ syringe.minus }}" disabled="{{ syringe.disabled }}">−</button>
                  <button style="flex:1; height:44px; border:1px solid var(--color-divider); background:transparent; display:flex; align-items:center; justify-content:center; gap:5px; cursor:pointer;" onClick="{{ syringe.pad }}" disabled="{{ syringe.disabled }}"><span style="font-family:var(--font-heading); font-size:24px; color:var(--color-accent-700);">{{ syringe.ml }}</span><span style="font-size:13px; color:color-mix(in srgb, var(--color-text) 68%, transparent);">mL</span></button>
                  <button style="width:44px; height:44px; background:transparent; border:1px solid var(--color-divider); font-family:var(--font-heading); font-size:24px; color:var(--color-text); cursor:pointer;" onClick="{{ syringe.plus }}" disabled="{{ syringe.disabled }}">+</button>
                </div>
                <div style="display:flex; gap:8px;">
                  <button style="flex:1; height:44px; background:transparent; border:1px solid var(--color-divider); font-family:var(--font-heading); font-size:19px; color:var(--color-text); cursor:pointer;" onClick="{{ syringe.aspirate }}" disabled="{{ syringe.disabled }}">Liquid extraction ASPIRATE</button>
                  <button style="flex:1; height:44px; background:transparent; border:1px solid var(--color-divider); font-family:var(--font-heading); font-size:19px; color:var(--color-text); cursor:pointer;" onClick="{{ syringe.dispense }}" disabled="{{ syringe.disabled }}">Liquid injection DISPENSE</button>
                  <button style="flex:0 0 78px; height:44px; background:transparent; border:1px solid oklch(0.62 0.10 78); font-family:var(--font-heading); font-size:19px; color:var(--color-text); cursor:pointer;" onClick="{{ syringe.reset }}" disabled="{{ syringe.disabled }}">RESET</button>
                </div>
                <div style="display:flex; align-items:center; gap:10px; border-top:1px solid var(--color-divider); padding-top:8px;">
                  <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="{{ dev.MIX.dot }}" stroke-width="1.5" style="flex:0 0 24px;"><path d="M5 6h14v10a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z"></path><path d="M12 6v8M9 14h6"></path></svg>
                  <span style="font-size:15px; flex:1;">Mixing Tank Agitator 40028</span>
                  <button style="flex:0 0 88px; height:44px; background:{{ dev.MIX.btnBg }}; border:1px solid {{ dev.MIX.bd }}; color:{{ dev.MIX.btnFg }}; font-family:var(--font-heading); font-size:19px; cursor:pointer;" style-hover="border-color:var(--color-accent)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">{{ dev.MIX.btn }}</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </sc-if>

```

## D.2 `admin-page.html`

```html
    <!-- ══════════════ Administrator settings ══════════════ -->
    <sc-if value="{{ page.admin }}" hint-placeholder-val="{{ true }}">
      <div style="flex:1; display:flex; flex-direction:column; min-width:0; padding:9px 12px; gap:8px;">
        <div style="flex:0 0 52px; display:flex; align-items:center; gap:8px;">
          <sc-for list="{{ adminTabs }}" as="t" hint-placeholder-count="4">
            <button onClick="{{ t.tap }}" style="height:44px; padding:0 13px; white-space:nowrap; background:{{ t.bg }}; border:1px solid {{ t.bd }}; color:{{ t.fg }}; font-family:var(--font-heading); font-size:17px; cursor:pointer;" style-hover="border-color:var(--color-accent)">{{ t.label }}</button>
          </sc-for>
          <sc-if value="{{ adminRecipe }}">
            <button onClick="{{ workflowToggle }}" style="height:44px; padding:0 13px; white-space:nowrap; background:var(--color-accent-100); border:1px solid var(--color-accent); color:var(--color-text); font-family:var(--font-heading); font-size:16px; cursor:pointer;">{{ workflowToggleLabel }}</button>
          </sc-if>
          <span style="flex:1;"></span>
          <span style="font-size:12px; color:color-mix(in srgb, var(--color-text) 74%, transparent);">CSV Recipe · Administrator permissions are verified by the backend</span>
        </div>

        <sc-if value="{{ adminWorkflow }}">
          <div class="blueprint" style="flex:1; min-height:0; border:1px solid var(--color-divider); overflow:hidden;">
            <iframe src="/workflow/" title="CSV Recipe and testing process" style="width:100%; height:100%; border:0;"></iframe>
          </div>
        </sc-if>
        <sc-if value="{{ adminStandard }}">
        <div class="blueprint" style="flex:1; min-height:0; border:1px solid var(--color-divider); display:flex; flex-direction:column; overflow:hidden;">
          <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
          <div style="flex:0 0 42px; display:flex; align-items:center; padding:0 16px; border-bottom:1px solid var(--color-text); font-size:12px; letter-spacing:0.09em; text-transform:uppercase; color:color-mix(in srgb, var(--color-text) 72%, transparent); gap:12px;">
            <span style="flex:1.15;">{{ adminH.a }}</span>
            <span style="flex:1.7;">{{ adminH.b }}</span>
            <span style="flex:1;">{{ adminH.c }}</span>
            <span style="flex:1.05;">{{ adminH.d }}</span>
            <span style="flex:0.62;">{{ adminH.u }}</span>
          </div>
          <div style="flex:1; min-height:0; overflow-y:auto;">
            <sc-if value="{{ adminIsTable }}" hint-placeholder-val="{{ true }}">
              <sc-for list="{{ adminRows }}" as="r" hint-placeholder-count="7">
                <div style="height:52px; display:flex; align-items:center; padding:0 12px; border-bottom:1px solid var(--color-divider); gap:8px;">
                  <span style="flex:1.15; font-family:var(--font-heading); font-size:19px;">{{ r.a }}</span>
                  <span style="flex:1.7; font-size:14px;">{{ r.b }}</span>
                  <div style="flex:1;">
                    <button style="width:100%; height:40px; padding:0 9px; background:transparent; border:1px solid var(--color-divider); color:var(--color-accent-700); font-family:var(--font-heading); font-size:19px; text-align:right; cursor:pointer;" style-hover="border-color:var(--color-accent); background:var(--color-accent-100)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">{{ r.c }}</button>
                  </div>
                  <div style="flex:1.05;">
                    <sc-if value="{{ r.dEdit }}" hint-placeholder-val="{{ true }}">
                      <button style="width:100%; height:40px; padding:0 9px; background:transparent; border:1px solid var(--color-divider); color:var(--color-accent-700); font-family:var(--font-heading); font-size:19px; text-align:right; cursor:pointer;" style-hover="border-color:var(--color-accent); background:var(--color-accent-100)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">{{ r.d }}</button>
                    </sc-if>
                    <sc-if value="{{ r.dLock }}" hint-placeholder-val="{{ false }}">
                      <span style="font-size:14px; color:color-mix(in srgb, var(--color-text) 80%, transparent);">{{ r.d }}</span>
                    </sc-if>
                  </div>
                  <span style="flex:0.62; font-size:14px; color:color-mix(in srgb, var(--color-text) 68%, transparent);">{{ r.u }}</span>
                </div>
              </sc-for>
            </sc-if>
            <sc-if value="{{ adminIsUsers }}" hint-placeholder-val="{{ false }}">
              <sc-for list="{{ userRows }}" as="u" hint-placeholder-count="7">
                <div style="height:52px; display:flex; align-items:center; padding:0 12px; border-bottom:1px solid var(--color-divider); gap:8px; opacity:{{ u.op }};">
                  <div style="flex:1.15;">
                    <input value="{{ u.name }}" onChange="{{ u.onName }}" style="width:100%; height:40px; padding:0 8px; background:transparent; border:1px solid var(--color-divider); color:var(--color-text); font-family:var(--font-heading); font-size:18px;" style-hover="border-color:var(--color-accent)" style-focus="outline:2px solid var(--color-accent); outline-offset:2px;" />
                  </div>
                  <div style="flex:1.7;">
                    <input value="{{ u.role }}" onChange="{{ u.onRole }}" style="width:100%; height:40px; padding:0 8px; background:transparent; border:1px solid var(--color-divider); color:var(--color-text); font-size:14px;" style-hover="border-color:var(--color-accent)" style-focus="outline:2px solid var(--color-accent); outline-offset:2px;" />
                  </div>
                  <div style="flex:1; display:flex; gap:3px;">
                    <sc-for list="{{ u.levels }}" as="lv" hint-placeholder-count="4">
                      <button style="flex:1; height:40px; background:{{ lv.bg }}; border:1px solid {{ lv.bd }}; color:{{ lv.fg }}; font-family:var(--font-heading); font-size:16px; cursor:pointer; padding:0;" style-hover="border-color:var(--color-accent)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">{{ lv.label }}</button>
                    </sc-for>
                  </div>
                  <div style="flex:1.05; display:flex; align-items:center; gap:7px;">
                    <button style="height:40px; padding:0 9px; background:transparent; border:1px solid {{ u.stateBd }}; color:{{ u.stateFg }}; font-family:var(--font-heading); font-size:16px; cursor:pointer; white-space:nowrap;" style-hover="background:var(--color-accent-100)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">{{ u.stateLabel }}</button>
                    <span style="font-size:11px; color:color-mix(in srgb, var(--color-text) 70%, transparent); line-height:1.15;">{{ u.last }}</span>
                  </div>
                  <div style="flex:0.62; display:flex; justify-content:flex-end;">
                    <button style="width:40px; height:40px; background:transparent; border:1px solid var(--color-divider); color:color-mix(in srgb, var(--color-text) 70%, transparent); font-size:17px; cursor:pointer;" style-hover="border-color:var(--color-accent); color:var(--color-accent-700)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">✕</button>
                  </div>
                </div>
              </sc-for>
            </sc-if>
          </div>
        </div>

        <div style="flex:0 0 48px; display:flex; align-items:center; gap:8px;">
          <span style="font-size:13px; color:color-mix(in srgb, var(--color-text) 78%, transparent);">{{ adminDirtyText }}</span>
          <span style="flex:1;"></span>
          <sc-if value="{{ adminIsUsers }}" hint-placeholder-val="{{ false }}">
            <button style="height:42px; padding:0 14px; background:transparent; border:1px solid var(--color-divider); font-family:var(--font-heading); font-size:17px; color:var(--color-text); cursor:pointer;" style-hover="background:var(--color-accent-100); border-color:var(--color-accent)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">New account ADD USER</button>
          </sc-if>
          <button style="height:42px; padding:0 14px; background:transparent; border:1px solid var(--color-divider); font-family:var(--font-heading); font-size:17px; color:var(--color-text); cursor:pointer;" style-hover="background:var(--color-accent-100); border-color:var(--color-accent)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">restore REVERT</button>
          <button class="blueprint" style="height:42px; padding:0 18px; background:var(--color-accent); border:1px solid var(--color-accent-700); font-family:var(--font-heading); font-size:18px; color:#fff; cursor:pointer;" style-hover="background:var(--color-accent-600)" disabled="{{ true }}" title="has not yet been connected to the device/Source" data-unconnected="true">
            <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
            Storage SAVE
          </button>
        </div>
        </sc-if>
      </div>
    </sc-if>
```

## D.3 `web/custom/branding.example.json`

```json
{
  "_illustrate": "Copy to branding.json will take effect.logo picture files are placed in this folder (png/jpg/svg/webp) ,Top bar display height 38 px, widest 150 px; recommends transparent background and original image height 76 px above.product_name can write only one string (same in all languages).",
  "company": "OOO Co., Ltd.",
  "logo": "logo.png",
  "product_name": {
    "zh-TW": "online metal wastewater detector",
    "zh-CN": "online metal wastewater detector",
    "en": "Online Metal Wastewater Analyzer"
  }
}
```

## D.4 `web/custom/texts.example.json`

```json
{
  "_illustrate": "Copy to texts.json will take effect.Always fill in the original traditional Chinese text on the screen on the left side (The entire text is exactly the same) ,Fill in the text to be displayed on the right; Write each language separately,Languages not listed will maintain built-in translation.Just refresh the screen after saving it.",
  "zh-TW": {
    "execution record": "test record",
    "No active alarm": "System is normal, no alarm"
  },
  "zh-CN": {
    "execution record": "detection record",
    "No active alarm": "System is normal, no alarm"
  },
  "en": {
    "execution record": "Test Log",
    "No active alarm": "System normal, no alarms"
  }
}
```

---

## Cross-check

(This section is not part of Appendix D; it follows the whole report.)

Done 2026-10-06 by a second reader. Compared with the source: `web/index.html` lines 1-140, 204-343 and 384-503 of the style block and template (tokens, frame, top bar, banner, nav, panels, quality classes, metal cards, manual page, tables, pager, lists, keyboard, maintenance page, grid buttons, high-voltage styles, footer, dialogs, schedule tiles, PIN login), the constants and view-model code at 1042-1250 and 2143-2207, and the design-system stylesheet `web/original/_ds/industry-…/styles.css` (rule list and the `.btn`, `.seg`, `.card`, `.tag`, `.nav`, `.table`, `.dialog` blocks, lines 2, 66-71, 108-112, 140-162, 192-203, 225-233, 236-246, 249-259, 262-286). Of this report, sections 1-3, 4.6-4.9 and 7 were read in full and section 10; sections 4.1-4.5, 4.10-4.13, 5, 6, 8, 9 and the verbatim appendices A-D were not read through and were compared only where the source lines above overlap them. Nothing was run; no screenshot exists, so nothing here is a statement about rendered pixels.

### Claims checked against the source and found correct

1. Live tokens `--run … --panel`, verbatim (`index.html:29-35`) and the state-rule comment (`index.html:19-28`).
2. Frame: `.hmi` 1024 × 768, column flex, `overflow:hidden`, base 15 px, font stack `var(--font-body), "Microsoft JhengHei", sans-serif`; demo ring `inset 0 0 0 5px #d9a300` (`index.html:42-43`).
3. Zone sizes: `.top` 56 px, `.banner` 44 px, `.nav` 116 px, `.foot` 70 px; page padding `8px 10px`, gap 8 (`index.html:46,77,92,100,386`).
4. Top bar pieces and their order in the template; texts `online metal wastewater detector`, `operator`, `Change operator`, `maintenance mode`; `.opbtn` 44 px, `.lang button` 36 × 34, `.clock` 19 px heading font (`index.html:62-74,474-488`).
5. Banner classes `none / p1 / p2 / p3 / acked`, blinking `.pri` only while unacknowledged, button style (`index.html:77-88`).
6. Nav button: min-height 54, 16 px, selected = solid `--run`; `.navmaint` dashed, 46 px, `margin-top:auto`; badge 20 px (`index.html:92-98`).
7. Quality classes `.q-ok / .q-run / .q-bad / .q-na / .q-uncal / .q-cal` and the tag variants, including the hatch `#e2e2e2 / #f6f6f6` (`index.html:110-132`).
8. Buttons: `.btn` 40 px / 16 px / border `#8b8f94`; disabled colours; `.btn.primary`; `.seg button` 36 px, selected blue; `.seg.tabs button` 44 px (`index.html:216-224,281`).
9. Manual page sizes: `.prow .sec` 96 × 46, `.prow .btn` 64 × 46, `.side` 330 px, `.vol` 24 px heading font (`index.html:225-246`).
10. Tables: `.thead` 34 px with a 2 px `--ink-2` rule, `.trow` min-height 44, slim 32; pager; list row `.li` min-height 58 (`index.html:253-287`).
11. On-screen keyboard: `.kb-key` 56 × 50, `.mid` 92, `.wide` 210, `.dialog.wide` 720 px; `.login-buf` Consolas 22 px (`index.html:290-300`); key rows (`index.html:1191-1193`).
12. Maintenance page: `.adm-left` 300 px, `.kv` 3 columns, `.btn-grid` 2 columns, `.gbtn` 86 / 66 px, `.gbtn.hv` with 22 px hazard stripes `#ffd000 / #1d1f20` and a 3 px `--p1` frame (`index.html:303-331`).
13. High-voltage dialog `.dialog.danger.hv` 580 px, red header and confirm button; `.ack-row` 54 px with 28 px box, ticked fill `#fdecec` (`index.html:333-340`).
14. Parameter tiles `.param-grid` 4 columns, `.pbtn` 112 px, value 26 px heading font (`index.html:341-343`).
15. Footer: `.start` 140 × 54 / 21 px, `.stop` 120 × 54 / 20 px / 2 px `--ink`, `.pause-btn` 120 × 54 / 18 px / 2 px `#b88a00`, `.start.resume` `#2f7a4b`, `.estop` 188 px with a 2 px `--p1` frame (`index.html:386-402`).
16. Dialog: scrim `rgba(20,22,24,.45)`, box 440 px, 2 px `--ink`, shadow `0 12px 40px rgba(0,0,0,.35)`; `.danger`; `.dlg-h / .dlg-b / .dlg-line / .dlg-warn / .dlg-detail / .dlg-f / .dlg-timeout`; keypad `.keys` 3 columns, key 52 px / 24 px; choice grid two per row (`index.html:405-439`).
17. Schedule tiles: `.sch-left` 392 px, `.stile` 80 px, `.need` dashed `#b88a00`, `.stile.hv` 3 px red with a 12 px stripe; `.hvtag` (`index.html:442-461`).
18. PIN login CSS, verbatim: `.pin-dots` 360 × 56 / 30 px / letter-spacing 8, `.pin-keys` 3 × 104 px, key 62 px / 26 px, `.pin-foot .btn` 52 px (`index.html:463-467`); key order and the 8-digit limit (`index.html:2197-2198`).
19. Keypad key order and the `C` / `.` / `±` variants; PIN pad text `Please enter 6–8 digital repair PIN`; class composition of the dialog (`index.html:2153,2172-2177`).
20. `PER_PAGE` values (`index.html:1161`).
21. Design-system residue of section 7.1, checked rule by rule against the DS file: DS `.btn` sets `font-family: var(--font-heading)` and weight 600 and the inline block does not override them (the inline `button { font-family:inherit }` has lower specificity); DS `.btn:disabled { opacity: 0.45 }` stays; DS `.dialog` `gap` and `padding` stay; DS `.seg` border and `overflow:hidden` stay; DS `.tag` `letter-spacing` stays; DS `.table` `font-size:14px` stays; line 281 forces `border-radius: 0` on `.card, .btn, .input, .tag, .seg, .dialog`.
22. DS loads `/vendor/fonts.css` by `@import` (DS:2); DS body `font-size:15px; line-height:1.55` (DS:108).

No statement of this report was found to be wrong.

### Corrections made in the text above

- Section 3.6: the block presented as verbatim `IDX:100-107` had dropped two lines (`.ph .sub { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }` and `html[lang="en"] .nm { font-size:12px; }`). Restored.

### Additions (not in the report before)

- The visible texts in the top bar name the PLC: the chip reads `PLC {state}` and its tooltip `Modbus TCP {host} · N Updated seconds ago` (`index.html:482,1837`). With the PLC removed, the same chip position and style can carry the Mega link; only the label and tooltip change. The second chip `RPi {state}` keeps its meaning.
- "Same style as the original" has one hidden dependency for a plain rebuild: several looks come from the design-system file, not from the inline block (item 21). Dropping the DS file without the starter rules of 7.1 changes button typography (Barlow Condensed → Barlow), disabled-button opacity and dialog spacing.
- The state rule (colour only for "running" or "abnormal") has no slot for a *measured* liquid value: the original never had flow, weight or level readings, only command read-back. A weight reading from the Mega's scale would be a normal value (`.q-ok`, grey) by this rule, with `.q-bad` when the scale stream is stale; the three hatched `not connected` bars of the flow diagram are the places the original reserved for such sensors (`hmi-ui-structure.md` §3.1).

### Still open after the cross-check

- Sections 4.1-4.5 and 4.10-4.13 were compared only through the overlapping CSS lines listed above; the flow-diagram CSS (`index.html:147-196`) and the template markup of Appendix B were not re-read.
- The side-page stylesheets (Appendix C) and the legacy fragments (Appendix D) were not re-read.
- Everything in section 10 (no running screen, no screenshot).
