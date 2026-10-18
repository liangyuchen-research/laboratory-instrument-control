# HMI v1.1 — spectrum analysis, calibration, measurement records (subsystem: hmi-analysis)

Read-only investigation. All paths are relative to `C:/Users/PlasmaLab/Desktop/UI/UI/hmi_v1_1/` unless stated.

## 0. What was read

Assigned, read completely:
`measurements.py`, `calibration.py`, `calibration_config.py`, `spectrum_result.py`, `spectrum_store.py`,
`rpi_tools/spectrum_analysis.py`, `rpi_tools/analysis_config.json`, `rpi_tools/manifest.json`,
`CALIBRATION.zh-TW.md`, `tests/test_calibration.py`, `tests/test_spectrum_result.py`,
`web/calibration/prime_template.csv`, `web/calibration/standard_round_template.csv`.

Read in addition, only where needed to answer (a)–(f) (callers and the acquisition side):
`remote_result.py` (whole), `web/calibration/app.js` (whole), `web/trend/app.js` (whole),
`controller.py` lines 120–149, 255–284, 625–704, 770–859, 900–1009, 1100–1459,
`server.py` lines 130–194 and 270–409, `rpi_tools/plasma_job.py` lines 385–750.
One grep of `C:/Users/PlasmaLab/Desktop/autonomous control/rpi/tcpd/plasmic_tcpd.py` for the job-folder layout (not read in full).

Caveat about my own work: one `Glob("*.py")` on `hmi_v1_1` recursed and printed file names under `offline_bundle\python-runtime\Lib\site-packages\pip\...` (names only, pip library files; nothing was opened). All later searches used explicit file paths. No `data`, `.venv`, `dist`, `__pycache__`, `runtime` or `pc/data` content was listed or opened.

Nothing was run, nothing was modified.

---

## 1. Big picture

```
RPi job folder (one per plasma_run)                HMI (Windows)
  <output_dir>/HMI-<32 hex>/
    job.json            (valid, started_at, signal_target_frames, background{}, signal{} ...)
    background/spectrum-1.csv .. spectrum-3.csv    <- taken BEFORE the discharge, HSPY OFF
    signal/spectrum-1.csv .. spectrum-<Loopnum>.csv <- one triggered frame per loop

  rpi_tools/spectrum_analysis.py  (runs ON the RPi, pure Python, config in analysis_config.json)
      config | reference | analyze      JSON on stdin -> JSON on stdout
                                                   controller._analysis_exec (SSH)
                                                     -> measurements.scale_for_frame_sum (x N)
                                                     -> calibration.regress        (calibration run)
                                                     -> measurements.evaluate      (every other plasma_run)
                                                     -> data/measurements.jsonl, data/calibrations/CAL-*.json
  remote_result.py (sent over SSH stdin, runs on the RPi) -> spectrum_result.load_result
                                                     -> spectrum_store.save_set -> data/spectra/SPEC-*.json
```

Two independent paths consume a job folder:

1. **Concentration path**: `spectrum_analysis.py` on the RPi → intensities → HMI converts to mg/L.
2. **Spectrum-review path**: `remote_result.py` on the RPi → `spectrum_result.load_result` → archive for the spectrum page (mean background, mean signal, difference, per-frame differences, legacy Cu/Ni "readback" numbers). It never produces a concentration (`'concentration_available': False`, `spectrum_result.py:136`).

Three metals everywhere: `METALS = ('Cu', 'Zn', 'Ni')` (`measurements.py:30`, `calibration_config.py:6`). Unit everywhere: `UNIT = 'mg/L'` (`calibration.py:28`).

---

## 2. (a) What is computed from spectra

### 2.1 Acquisition as the analysis sees it (`rpi_tools/plasma_job.py`)

- Frame file format (checked by every reader): CSV, header exactly `wavelength_nm,intensity_raw`, one row per pixel.
- **Background: exactly 3 frames, taken before the discharge.**
  - `start_spectrum(app_dir, tools_dir, output_dir, integration_ms=100, sdk_settle_ms=500, frames=3, frame_interval_ms=0, triggered=False)` (`plasma_job.py:395-396`); the background call passes no `frames` → 3 (`plasma_job.py:513`).
  - `collect_spectrum(child, deadline, expected_frames=3)` (`plasma_job.py:429`) raises `'Spectrum capture report is incomplete'` unless `report['frames_expected'] == 3` and the file list is exactly `spectrum-1.csv..spectrum-3.csv` (`plasma_job.py:443-446`). Deadline `clock() + 40` s (`plasma_job.py:514`).
  - Order inside `run_plasma` (`plasma_job.py:505-582`): `trigger.low()` → `require_off` → HSPY setpoint check → `prepare` (Nano config) → **background capture** (`:513-515`, step text `'background spectrum completed'`) → Nano ARM (`:518`) → start the signal SDK child with `frames=loops, frame_interval_ms=interval_ms, triggered=True` (`:521-522`) → `power_write(4, 1)` = HSPY ON (`:525`) → voltage ramp → Nano START → trigger loop → `report['signal']`.
  - Background and signal use the same `integration_ms = int(parameters['Integration_Time'])` (`plasma_job.py:482, 513, 521`).
- **Signal**: `loops = int(parameters['Loopnum'])` frames, one per trigger (`plasma_job.py:485, 560-564`); `report['signal_target_frames'] = loops` (`:497`).
- The standalone no-discharge capture (`spectrum` action) also takes 3 frames: `start_spectrum(...)` default, deadline `max(40, 3 * args.integration_ms / 1000 + 15)` (`plasma_job.py:727-731`).
- The spectrometer SDK wrapper `hardware_verify.py spectrum` (`plasma_job.py:398-402`) is **not** in `hmi_v1_1/rpi_tools/` (that folder holds `nano_verify.py`, `pulse_prepare.py`, `plasma_job.py`, `spectrum_analysis.py`, `analysis_config.json`, `manifest.json`). Its frame-capture behaviour could not be read here.

### 2.2 One job → one spectrum (`spectrum_analysis.py:240-259`, `job_spectrum`)

1. `job.json` must exist, be ≤ 300 000 bytes, and have `valid is True` (else `'plasma job not valid: <dir>'`).
2. `_mean_group(directory, 'signal')` (`:224-237`): reads `signal/spectrum-1.csv`, `spectrum-2.csv`, … until the next number is missing (max `MAX_FRAMES = 1000`), sums per pixel, divides by the count. Zero frames → `'no signal frames in <dir>'`.
3. If `config['subtract_dark']`: the same for `background/`; axes must match within 0.001 nm (`'signal and background axes differ in <dir>'`); **`signal[i] = mean_signal[i] − mean_background[i]`** (`:251-255`).
   If `subtract_dark` is false the background folder is not read and `background_frames = 0`.
4. Axis must be strictly increasing (`'wavelength axis not increasing in <dir>'`).
5. Returns `{'axis', 'values', 'signal_frames', 'background_frames'}`.

Frame reader limits (`_read_frame`, `:198-221`): file ≤ 2 000 000 bytes, 8 ≤ pixels ≤ 8192, finite numbers, every frame's axis equal to the first within 0.001 nm.

**The analyzer does not use the frame count for anything except the mean and the reported `background_frames`. It needs ≥ 1 background frame named `background/spectrum-1.csv`.**

### 2.3 Spectral shift correction (`spectrum_analysis.py:301-354`)

Purpose: the sample is compared with a reference (mean of the calibration's blank rounds); integration windows are moved instead of resampling the spectrum.

- Sample is wrapped in a natural cubic spline (`Spline`, `:264-298`; clamps to the end values outside the axis).
- Per shift window `[lo, hi]` (`window_shift`, `:312-332`):
  - `xs` = reference pixels with `lo ≤ x ≤ hi`; fewer than 5 → `'shift window [..] has fewer than 5 pixels'`.
  - grid `d = i·step_nm`, `i = −count..count`, `count = round(search_nm / step_nm)`.
  - `score(d) = Pearson(reference[xs], sample(xs + d))` (`_correlation`, `:301-309`; returns 0.0 when either variance ≤ 0).
  - best grid point; if not at the grid edge, parabola refinement: `curvature = y0 − 2·y1 + y2`; if `curvature < 0`: `d += step · 0.5 · (y0 − y2) / curvature`.
  - returns `{'window_nm', 'center_nm', 'shift_nm', 'correlation' (recomputed at refined d), 'at_search_edge'}`.
- Model (`fit_shift`, `:335-354`): one window → `p0 = shift, p1 = 0`; two or more → least squares `shift = p1·center + p0`. Result `{'applied': True, 'p0', 'p1', 'windows', 'valid', 'problems'}`.
- The job is **invalid** (`valid = not problems`) when any window has:
  - `at_search_edge` → `'shift at search limit in [..]'`
  - `abs(shift_nm) > max_shift_nm` → `'shift X nm exceeds Y nm'`
  - `correlation < min_correlation` → `'correlation X below Y'`
- No shift is applied (`_no_shift`, `:557-559`: `applied False, p0 0, p1 0, valid True`) with reason `'shift disabled'`, `'no reference spectrum'` or `'first blank'`.
- Window movement (`metal_intensities.moved`, `:497-500`): each bound `w → w + p0 + p1·w`.

### 2.4 Metal intensity — three methods (`metal_intensities`, `spectrum_analysis.py:493-552`)

Chosen per metal by the config keys present.

**(A) Area method** — neither `baseline` nor `metric` key present (the code default, `:526-549`):
- `pixel_edges` (`:373-377`): edges halfway between pixel centres, first/last extrapolated by half a pitch.
- `integrate(low, high)` (`:380-393`) = Σ `values[i] × overlap(pixel i, [low, high])`; window outside the spectrum → `'window L-H nm is outside the spectrum'`.
- level of each present background window = `area(w) / (w[1] − w[0])` at its centre.
- baseline at the peak centre: two windows → straight line between the two levels; one → that level; none → `0.0`.
- `peak_area = area(peak)`; `net = peak_area − baseline × (peak[1] − peak[0])`.
- `normalize` window given: `norm_area = area(normalize)` on the dark-subtracted spectrum (no baseline); `≤ 0` → `'normalization area is not positive'`; `intensity = net / norm_area`. Otherwise `intensity = net`.
- item: `{'valid': True, 'peak_area', 'baseline_level', 'net_area', 'windows_used': {'peak','bg_left','bg_right'[,'normalize']}, ['norm_area'], 'intensity'}`.

**(B) Pixel method** — `baseline` or `metric` key present (`_pixel_method`, `:471-490`); matches the standalone `spectrum_viewer/optimizer.js` per the docstring (`:37`):
- pixels of a window = indices whose centre satisfies `lo ≤ x ≤ hi` (`_pixels`, `:415-416`).
- `baseline_function(axis, values, [left, right], kind)` (`:419-457`); a background window with no pixel → `'background window holds no pixel'`; no windows → 0.
  - `lin2`: line through (mean λ, mean value) of each window; one window or equal x → flat mean.
  - `min2`: line through the lowest pixel of each window.
  - `const`: mean of all background pixels.
  - `poly1|poly2|poly3`: least-squares polynomial on `t = (λ − centre) / scale`; needs ≥ degree+1 pixels (`'polyN background needs at least N+1 pixels'`); singular → `'background fit is singular (too few background pixels)'`.
- `net[i] = values[i] − base(axis[i])` for the peak pixels.
- `window_metric` (`:460-468`): `peak` = max, `mean` = mean, `area` = trapezoid over the pixel centres `Σ (x_b − x_a)(v_a + v_b)/2`; empty window → `'window holds no pixel'`.
- item: `{'valid': True, 'baseline': kind, 'metric': metric, 'net_area': signal, 'signal': signal, 'baseline_level': base(peak centre), 'windows_used': {...}}`; with `normalize`: `norm_area = window_metric(raw values in normalize window, same metric)`, must be `> 0` (`'normalization value is not positive'`), `intensity = signal / norm_area`; else `intensity = signal`.

**(C) Several lines of one metal** — `lines` key present (`:508-519`):
- each line evaluated with (B) on its own moved windows, no normalization; `total = Σ weight × signal`.
- item: `{'valid': True, 'baseline': 'lines', 'metric': 'weighted_sum', 'net_area': total, 'signal': total, 'intensity': total, 'lines': [{'peak', 'signal', 'weight'}], 'windows_used': {'peak': <first line's peak>}}`.

Any `ValueError`/`ZeroDivisionError` for a metal → `{'valid': False, 'error': str}` for that metal only (`:550-551`).

### 2.5 Configuration schema (`analysis_config.json`, validated by `validate_config`, `spectrum_analysis.py:98-172`)

Top-level keys allowed: `subtract_dark`, `shift`, `metals`, `comment` (comment is dropped).

| Key | Rule |
|---|---|
| `subtract_dark` | bool, default `true` |
| `shift.enabled` | bool |
| `shift.windows_nm` | 1–5 windows |
| `shift.search_nm` | 0.01–3 |
| `shift.step_nm` | 0.0005–0.05 |
| `shift.max_shift_nm` | 0.001–3 and ≤ `search_nm` |
| `shift.min_correlation` | 0–1 |
| `metals` | 1–10 entries; name alphanumeric, ≤ 8 chars |
| metal (single line) | keys `peak` (required), `bg_left`, `bg_right`, `normalize` (each optional/null), `baseline` ∈ `('lin2','min2','const','poly1','poly2','poly3')`, `metric` ∈ `('area','peak','mean')` |
| metal (multi line) | `lines`: 1–5 entries (`MAX_LINES = 5`), each with keys `peak, bg_left, bg_right, baseline (default 'lin2'), metric (default 'area'), weight (default 1; finite, non-zero, |w| ≤ 1e6)`; beside `lines` only `normalize: null` is allowed |
| any window | `[low_nm, high_nm]`, `100 ≤ low < high ≤ 1200` |

`baseline`/`metric` are stored only when given, so configs without them keep their `method_id` (`:133-138`).

`method_id` (`:175-178`): `'area-v1-' + sha256(json.dumps({'version': 'area-v1', 'config': <validated config>}, sort_keys=True, separators=(',', ':')))[:12]`. `ANALYSIS_VERSION = 'area-v1'`. **It encodes the analysis config only — not pulses, frame counts, integration time, `frames_per_sum` or the number of background frames.**

`load_config` (`:181-193`): reads `analysis_config.json` beside the script (`utf-8-sig`); if missing, writes `DEFAULT_CONFIG` there first.

**Deployed config** (`rpi_tools/analysis_config.json`, the one `manifest.json` ships):

```
subtract_dark: true
shift: enabled true, windows_nm [[277.5, 280.0], [330.5, 333.0]], search_nm 0.5, step_nm 0.005, max_shift_nm 0.1, min_correlation 0.95
Cu: peak [324.5484, 325.1032], bg_left [323.0181, 323.7146], bg_right [325.1032, 325.7262], normalize null, baseline "poly3", metric "peak"
Ni: normalize null, lines:
    peak [231.3899, 232.1667], bg_left [228.8788, 229.746],  bg_right [234.0608, 234.4045], baseline "const", metric "area", weight 1
    peak [230.5253, 231.3035], bg_left [228.0971, 228.4447], bg_right [233.8029, 234.0608], baseline "const", metric "area", weight 0.4359
    peak [341.126, 341.5213],  bg_left [340.5322, 340.7302], bg_right [341.9161, 342.7041], baseline "lin2",  metric "area", weight 1.0887
Zn: peak [213.1156, 213.7405], bg_left [212.2215, 212.5793], bg_right [213.8298, 214.2757], normalize null, baseline "poly1", metric "mean"
```
Its `comment`: "2026-10-02 re-optimized on CAL-42b09c4d (30 pulses x 40 frames, frames_per_sum 2, 0-0.8 mg/L). Cu: 324.7 nm (no longer saturated, peak pixels <= 45k counts); Ni: three lines 231.7 + 230.8 + 341.4 nm, weighted by their noise; Zn 213.25 nm overlaps Cu 213.60 nm; shift windows 277.5-280 / 330.5-333 nm (off the saturated OH 306 nm band)."

**Code default** (`DEFAULT_CONFIG`, `spectrum_analysis.py:63-83`; used only when the JSON file is absent, and by the tests):

```
subtract_dark True
shift: enabled True, windows_nm [[260.8, 263.8], [305.6, 307.0]], search_nm 0.5, step_nm 0.005, max_shift_nm 0.3, min_correlation 0.95
Cu: peak [327.15, 327.55], bg_left [325.70, 326.05], bg_right [327.85, 328.20], normalize [325.70, 326.05]
Ni: peak [231.45, 231.85], bg_left [230.90, 231.25], bg_right [232.25, 232.60], normalize [233.15, 233.50]
Zn: peak [213.75, 213.97], bg_left [212.90, 213.25], bg_right [214.30, 214.65], normalize [214.30, 214.65]
```

Consequence of the deployed config: **no metal has a `normalize` window, so no `norm_area` is produced**, so the blank QC "Normalized area change" check never runs (see 4.4).

Spectrometer stated in `CALIBRATION.zh-TW.md:60`: OES-C210-FUV18, 200–350 nm, 1933 pixels, about 0.078 nm/pixel. (The EC project's historical-development-notes.md names the spectrometer "OES-C225"; whether the windows above fit that instrument is not answered by this code.)

### 2.6 Analyzer commands and replies (`spectrum_analysis.py:562-646`)

Invoked by the HMI as `timeout -k 3s 150s python3 -B <tools_dir>/spectrum_analysis.py <command>`, request JSON on stdin, SSH timeout 160 s (`controller.py:1208-1215`). Non-valid reply → `RuntimeError('RPi Spectral analysis failed:' + error[:300])`.

| Command | Request | Reply (always plus `'valid': True, 'method_id', 'version': 'area-v1'`; on any exception `{'valid': False, 'error': '<Type>: <msg>'}`) |
|---|---|---|
| `config` | none | `{'config', 'config_path'}` |
| `reference` | `{"jobs": [dir, ...]}` (1–200) | `{'axis', 'values', 'blank_count', 'jobs': [{'job','used',['shift'],['error']}], 'method_id'}` |
| `analyze` | `{"jobs": [dir, ...], "reference": {"axis","values"} \| null}` | `{'method_id', 'jobs': [row]}` |

`build_reference` (`:562-591`): first readable blank is the base; every other blank is shift-fitted to it; blanks whose shift is invalid are left out (`'blank not aligned: ...'`); the rest are evaluated by spline at `x + p0 + p1·x` on the base axis and averaged. No readable blank → `'no readable blank job for the reference spectrum'`.

`analyze` row (`:611-616`): `{'job', 'valid': shift['valid'], 'shift', 'metals', 'signal_frames', 'background_frames', 'error': '; '.join(shift problems)}`; unreadable job → `{'job', 'valid': False, 'error'}`. A malformed reference → `'reference spectrum is malformed'` (needs `axis`/`values` lists of equal length ≥ 8).

### 2.7 Frame grouping and summing (`measurements.py:109-144`)

- Setting `frames_per_sum`, integer 1–`FRAME_SUM_MAX = 50`; message `'The number of added sheets in each group must be 1–50 integer'`. File `data/frame_sum.json` = `{'frames_per_sum': N, 'updated_at': iso}`; missing/invalid → 1 (`load_frame_sum`).
- **No frames are actually regrouped.** `scale_for_frame_sum(row, N)` multiplies the RPi reply: for each valid metal, keys in `SUMMED_KEYS = ('peak_area', 'net_area', 'signal', 'norm_area', 'baseline_level')` × N; `intensity` × N **only when there is no `norm_area`** (a normalized intensity is a ratio); each `lines[].signal` × N; the row gains `'frames_per_sum': N`. `N == 1` returns the same object untouched.
- Justification in the docstring (`:124-129`): the mean of per-group sums = N × the mean of all frames and every metric is linear in the spectrum. (Since spectrum = mean signal − mean background, this also scales the background by N.)
- Where applied:
  - calibration analysis uses the value stored on the calibration record at start, `record.get('frames_per_sum', 1)` (`controller.py:1194, 1265-1266`);
  - each measurement uses the **current** setting `self.frames_per_sum` and stores it on the record (`controller.py:1378-1381`).
- **Nothing checks that the current `frames_per_sum` equals the one of the applied calibration.** `method_id` does not cover it.
- Endpoint: `POST /api/frame-sum {frames_per_sum}` (maintenance only), audit text `Every {previous} Zhang Xianjia → Every {value} Zhang Xianjia`; shown in `/api/state` as `frames_per_sum` (`controller.py:517`). UI location per the doc: Maintenance settings › plasma parameters (`CALIBRATION.zh-TW.md:64`).

### 2.8 Spectrum-review path (`remote_result.py`, `spectrum_result.py`, `spectrum_store.py`)

`remote_result.py` runs on the RPi (`python3 - <dir> <LINES json>`, source on stdin, timeout 45 s; `spectrum_result.py:78-80`):
- `summarize_group(directory, group, report, conditions)` (`remote_result.py:38-75`): requires `report[group]['valid'] is True`, `1 ≤ count ≤ 1000`, `frames_expected == count` when present, file names `spectrum-1.csv..spectrum-count.csv`; returns `{'count', 'mean': [[wavelength, mean]], ['frames': [[rounded values]]]}`. Per-frame rows are kept only for `signal` and only when `count × pixels ≤ MAX_FRAME_VALUES = 250_000`.
- For `signal` it also returns `features[name]` = per frame, the raw `[wavelength, intensity]` points at the pixels nearest the legacy wavelengths.
- Payload `{'job', 'background', 'signal', 'features'}` → `base64(zlib(json))`. Fails with `'Signal frame count does not match Loopnum'` when `signal_target_frames` differs.

`spectrum_result.load_result` (`spectrum_result.py:76-136`):
- decompressed payload ≤ 13 000 000 bytes (`'RPi The result packet is too large or incomplete'`); `job.valid` and `output_dir` must match (`'RPi The work record is invalid or the directory does not match'`).
- for both groups: `1 ≤ count ≤ 1000`, metadata length = count, file names in order, pixel count = `pixels` (`'<group> Spectral frame is incomplete'`, `'<group> The number of spectral pixels does not match the record'`).
- `'The number of signal spectra does not match the number of rounds'`, `'The wavelength axis of the background and the signal are inconsistent'` (0.001 nm).
- `difference = mean signal − mean background`; `frame_differences[k] = round(signal frame k − mean background, 2)` (or `None` when per-frame rows were not sent).
- Result keys: `flow_id, remote_dir, axis, mean_difference (2 dp), frame_differences, started_at, model, integration_ms, background_frames, signal_frames, loop_aligned, signal_files, background, signal, difference, metals, readback_method, concentration_available`.
  - `loop_aligned = (signal_target_frames == signal count) and signal.triggered is True`.
  - `readback_method = 'legacy_normalized_local_baseline_x100000'`, `concentration_available = False`.

Legacy readback (`metal_readback`, `spectrum_result.py:41-61`), only Cu and Ni:
```
LINES = {
 'Cu': {'peak': 327.278, 'normalize': 325.866, 'bg_left': 325.866, 'bg_right': 327.983},
 'Ni': {'peak': 231.664, 'normalize': 233.326, 'bg_left': 227.411, 'bg_right': 232.402},
}
```
Per signal frame (raw, **not** background-subtracted), nearest pixels: `peak/den`, `left/den`, `right/den` with `den` = value at `normalize`; `baseline = left + (right − left)·(x_peak − x_left)/(x_right − x_left)`; `value = |peak − baseline|`; frames with `den == 0` or `x_left == x_right` are skipped. Readback = `abs(round(mean(values) × 100000))` as an 8-digit zero-padded string; `> 99999999` → `'The metal readback value exceeds the eight-digit format of the old system'`; no usable frame → `'Signal spectrum cannot calculate metal readback value'`.

Job directory rule (`job_directory`, `:20-34`): `<output_root>/(LITE|HMI)-<32 hex>`; taken from `flow['latest_plasma_job']` or from an event message starting `'RPi raw data: '` or `'RPi Work/raw data Table of contents: '`. Errors `'process record RPi The spectrum directory is incorrect:… (must be located in …/ Down) '`, `'There is no readable data in this round of process RPi Spectral Catalog'`.

`spectrum_store` (`data/spectra/`): `SPEC-<32 hex>.json` + `SPEC-<32 hex>.meta.json`.
- Full record: `id, captured_at (= started_at or now), saved_at, source ('flow' | 'manual' | 'rpi_import'), flow_id, recipe_name, round, rounds, remote_dir, model, integration_ms, background_frames, signal_frames, loop_aligned, has_frames, axis, mean_difference, frame_differences, background_mean (2 dp), signal_mean (2 dp), metals`.
- Meta = `SUMMARY_KEYS` (`spectrum_store.py:16-18`).
- A job folder already archived returns the existing set (dedupe on `remote_dir`). `get_set` rejects ids not matching `SPEC-[0-9a-f]{32}` (`'spectral group ID format is incorrect'`).
- Endpoints: `GET /api/result/latest`; `GET /api/spectra`, `GET /api/spectra/<id>`, `GET /api/rpi-jobs` (maintenance session, else 403 `'spectrum original data is only for maintenance personnel (maintenance personnel PIN) '`); `POST /api/spectra/import {remote_dir}` (maintenance).

### 2.9 `rpi_tools/manifest.json`

Deployed by "Maintenance settings › RPi connection › renew RPi tool". `version`: "2026-10-02 Ni three-line weighted sum; windows re-optimized for 30-pulse x 40-frame acquisition". Each file has `sha256` and `known_previous` hashes: `plasma_job.py`, `nano_verify.py`, `pulse_prepare.py`, `spectrum_analysis.py`, `analysis_config.json`. `analysis_config.json` edits made on the RPi are backed up and overwritten by the next update.

---

## 3. (b) Calibration

### 3.1 Plan (`calibration.py:28-119`)

Constants: `MAX_LEVELS = 12`, `MAX_ROUNDS = 10`, `MAX_DISCARD = 3`, `MAX_RUNS = 80`, `MAX_CONCENTRATION = 100000`, `RECORD_ID = CAL-[0-9a-f]{32}`, `NAME_PATTERN = [^\x00-\x1f<>"\\]{1,20}`.

```
DEFAULT_PLAN = {'recipe_id': '', 'prime_recipe_id': '', 'discard_rounds': 1, 'levels': [
  {'name': 'Blank', 'conc': {Cu: 0, Zn: 0, Ni: 0}, 'rounds': 3},
  {'name': 'STD-1', 'conc': {Cu: None, Zn: None, Ni: None}, 'rounds': 3},
  {'name': 'STD-2', 'conc': {Cu: None, Zn: None, Ni: None}, 'rounds': 3}]}
```

`validate_plan` rules and messages:
- keys ⊆ `{recipe_id, prime_recipe_id, discard_rounds, levels}` → `'Calibration plan fields are incorrect'`
- `recipe_id` empty or 32 hex → `'Please select the standard product Recipe'`
- `prime_recipe_id` empty or 32 hex → `'Fluid replacement and draining Recipe Incorrect'`; equal to `recipe_id` → `'Fluid replacement and draining Recipe cannot be used with standard products Recipe same'`
- `discard_rounds` int 0–3 → `'The number of pre-running rounds for each concentration must be 0–3 integer'`
- 2–12 levels → `'standard product must have 2–12 concentrations (including blank)'`
- level keys ⊆ `{name, conc, rounds}` → `'No. N standard product fields are incorrect'`; name 1–20 chars → `'No. N standard product names must be 1–20 characters'`; duplicate → `'The standard product name is repeated:<name>'`
- conc keys ⊆ metals → `'<name> can only be Cu, Zn, Ni'`; each value `None`/`''` (metal not in this bottle) or number 0–100000 → `'<name> of <metal> must be a number or left blank'`, `'… Must be between 0–100000 mg/L'`
- rounds int 1–10 → `'<name> must be 1–10 integer'`
- levels are **sorted by total concentration** (stable); the first must have Cu = Zn = Ni = 0 → `'The lowest concentration standard must be blank:Cu, Zn, Ni Fill in all 0 (blank spectrum is used as a reference spectrum for displacement correction)'`; more than one zero-total level → `'can only have one blank standard'`
- a metal that appears in a non-blank standard needs ≥ 2 distinct values → `'<metal> At least required 2 different concentrations can be used for regression'` (cross-check: by reading, this message cannot be reached. The check is skipped unless some non-blank level has a non-zero value for the metal, and the blank — which must be exactly 0 for all three metals two lines earlier — is included in the value set, so the set always has at least `{0, x}`. The effective rule is therefore only "the blank plus at least one non-zero standard"; a metal with a single non-zero standard is accepted and fitted through two concentrations.)
- `Σ(rounds + discard_rounds) ≤ 80` → `'Total number of measurements N exceeds the upper limit 80'`

`plan_warnings` (non-blocking): `'<metal>: <level> (v) lower than the previous one <prev> (pv) ,The order from low to high will be disrupted, and the residue may be high.'`.

Draft persistence: `data/calibration_plan.json` stores the plan **as entered** (≤ 20 000 chars of JSON; `'Calibration plan format is incorrect'`), returns `{'saved': True, 'valid': bool, 'plan'|'error', 'warnings'}` (`controller.py:1156-1165`).

CSV import/export of the standards table (browser side, `web/calibration/app.js:224-246`): header `Name,Cu (mg/L),Zn (mg/L),Ni (mg/L),Number of rounds`, file name `calibration_standards.csv`, UTF-8 BOM + CRLF; import needs ≥ 5 columns (`'need 5 Column: Name,Cu,Zn,Ni,Number of rounds'`), ≤ 12 rows (`'most 12 concentration'`).

### 3.2 Run sequence (`build_steps`, `calibration.py:143-172`)

- The standard Recipe must contain exactly one `plasma_run` → `'standard product Recipe must contain exactly 1 indivual plasma_run (at present N ); the number of rounds can be changed on the calibration page.'`.
- The prime Recipe may not contain any of `REMOTE_ACTIONS = ('rpi_status', 'nano_prepare', 'plasma_run', 'spectrum_capture', 'nano_stop', 'power_off')` → `'Fluid replacement and draining Recipe can only have pump, syringe pump and waiting steps (cannot measure content or RPi steps)'`.
- Per level, in order:
  1. `{'action': 'operator_prompt', 'target': '', 'value': '', 'unit': '', 'line': 0, 'note': 'Please replace with standard product <describe(level)>,P6 Put the pipette into the new bottle and press ”Confirm Liquid Change”', 'phase': 'Fluid change', 'round': run+1, 'rounds': total, 'calibration': {'level': index, 'level_name'}}` — `describe` = `'<name> (Cu x / Zn y / Ni z mg/L) '`. (Cross-check: the four empty keys and `line: 0` were missing here; the runner reads `step["line"]`, `step["target"]`, `step["value"]`, `step["unit"]` of every step for its log line, `controller.py:905`, so a generated step without them would crash the run. The text names pump `P6` literally.)
  2. every prime step with `'phase': 'Fluid replacement and draining'` and `'calibration': {..., 'prime': True}`.
  3. `discard_rounds + rounds` copies of the Recipe; each step tagged `'calibration': {'level', 'level_name', 'run', 'discard': bool, 'round': None for a discard round else 1..rounds}`.
- Controller side (`controller.py:1185-1206`): refuses in demo (`'Demonstration mode does not start the real machine process'`), requires the HV confirmation (`'Calibration contains high-voltage plasma, you must first confirm the high-voltage prompt'`), flow name `'Correction:<recipe name>'`, `flow_extra = {'mode': 'calibration', 'calibration_id'}`, phases `['Fluid change', ('Fluid replacement and draining'), ...recipe phases]`.
- After each calibration `plasma_run` the job folder is appended to the record: `{'level', 'level_name', 'run', 'discard', 'round', 'job', 'finished_at'}` (`controller.py:1106-1117`); log `'Correction <level> Pre-run round completed (not included in regression)'` / `'Correction <level> No. N round completed'`. Calibration runs are **not** sent through `_measure` (`controller.py:960-961`).
- Estimated time: `round(delay_seconds × runs + prime_seconds × levels)` (`controller.py:1183`).

Recipe templates (PLC-era pump names; the PLC is gone, so targets must be remapped to V7):

`web/calibration/standard_round_template.csv` (header `action,target,value,unit,note`):
```
pump_speed,CW,200,rpm        shares forward speed; with HNO3 Recipe same
pump_direction,CW
pump_on,P5,120,s             Empty detector:P5 go first
delay,,20,s
pump_on,P4,120,s             P4＋P5 Open and empty at the same time
delay,,40,s
pump_off,P4 / pump_off,P5
delay,,10,s
pump_on,P6,50,s              P6 Standard product (replacing the sample P3) ; The upper limit must be greater than the number of seconds in the next column
delay,,40,s                  standard sample injection 40 Second
pump_off,P6
syringe_aspirate,CH2,1,mL    add acid 1 mL
syringe_dispense,CH1,1,mL
syringe_aspirate,CH3,5,mL    air push 5 mL
syringe_dispense,CH1,5,mL
delay,,30,s                  Mix
pump_on,P4,60,s              Submit for inspection
delay,,50,s
pump_off,P4
rpi_status                   read HSPY and Nano
nano_prepare                 synchronous parameter.json
plasma_run                   measurement (exactly once per round; the number of rounds is set on the calibration page) After
pump_on,P5,120,s             Empty after measurement
delay,,20,s
pump_on,P4,120,s
delay,,40,s
pump_off,P4 / pump_off,P5
delay,,10,s
```
`web/calibration/prime_template.csv`:
```
pump_speed,CW,200,rpm        with standard Recipe same
pump_direction,CW
pump_on,P6,20,s              P6 Pump out the new standard and push out the previous bottle remaining in the tube (approximately 10 mL) ; The upper limit must be greater than the number of seconds in the next column
delay,,10,s                  Draw new fluid 10 Second
pump_off,P6
pump_on,P5,120,s             empty: P5 go first
delay,,20,s
pump_on,P4,120,s             P4＋P5 Simultaneously open emptying mixing tank and detector
delay,,40,s
pump_off,P4 / pump_off,P5
delay,,10,s
```
`pump_on` value = a maximum on-time (auto-off limit), the following `delay` is the real duration. Doc: "P6 = PLC `40013`" (`CALIBRATION.zh-TW.md:31`); blank Recipe = sample Recipe with the sample pump replaced by P1 (`:84`); sample pump is P3 (template note).

### 3.3 Analysis of a calibration (`controller.py:1234-1285`)

1. `kept` = runs with `discard` false; `blanks` = kept runs with `level == 0`; none → `'There is no measuring wheel of blank standard, and the reference spectrum cannot be established.'`.
2. Under `analysis_lock` (`'Calibration analysis is in progress, please wait.'`) and `remote_lock` (`'RPi The command is being executed, please analyze again later.'`): `config`, then `reference {'jobs': blanks}`, then `analyze {'jobs': all kept, 'reference': {'axis','values'}}`.
3. The three `method_id`s must be equal → else `'analysis period RPi The analysis settings have been modified, please reanalyze.'`.
4. `analysis['jobs']` scaled by the record's `frames_per_sum`.
5. `fits = cal.regress(plan, runs, analysis['jobs'], record.get('exclude_levels'))`.
6. Record updated: `status 'analyzed'`, `reference = {'axis','values','blank_count','jobs'}`, `analysis = {'analyzed_at', 'method_id', 'frames_per_sum', 'config', 'config_path', 'jobs', 'fits'}`. Failure → `status 'analysis_failed'`, `error[:400]`.

Read-only on the RPi → "Reanalysis" (`POST /api/calibration/analyze {id}`, 202) re-runs it on the same raw spectra after a config change.

### 3.4 Curve fitting (`calibration.py:177-270`)

- **Inverse regression, one straight line per metal: concentration (y) on intensity (x).** `linear_fit`: `a = Σ(x−x̄)(y−ȳ)/Σ(x−x̄)²`, `b = ȳ − a·x̄`, `R² = 1 − Σ(y − (a·x+b))² / Σ(y−ȳ)²` (1.0 when the total is 0); all x equal → `'The light intensity is all the same and cannot be returned.'`.
- Points: every kept run of every level whose `conc[metal]` is not `None` (the blank is a point at y = 0), one point per round (not level means), unweighted.
- A run is left out of a metal's fit, with a reason, when:
  - the level is in `exclude[metal]` → `{'reason': 'Manually exclude this concentration', 'manual': True}`
  - the analysis row is missing/invalid or the metal item invalid → row error, metal error or `'No analysis results'`
- Fewer than 2 distinct concentrations among the points → `fit['error'] = 'Insufficient effective points Is 2 different concentrations'`, `valid False`.
- Fit object: `{'unit': 'mg/L', 'points': [{'level','round','x','y','job','fitted','residual'}], 'excluded': [{'level','round','reason'[,'manual']}], 'valid', ['error'], 'a', 'b', 'c': 1, 'r2', 'n', 'levels': [{'level','conc','n','mean_intensity','sd','rsd_percent'}]}`.
  - `residual = y − fitted`; `sd` = sample SD (n−1), `None` for n = 1; `rsd_percent = |sd/mean|×100`, `None` for the blank or when the mean is 0.
- Per-metal exclusion of a level (`validate_exclusions`, `set_calibration_exclusions`): `{metal: [level index]}`, messages `'The exclusion setting needs to be {Metal: [concentration serial number]}'`, `'<metal> is incorrect.'`; re-regresses from the stored intensities without the RPi; stored as `exclude_levels` and reused by later re-analyses. Endpoint `POST /api/calibration/exclude {id, exclude}`.

### 3.5 Acceptance rules

Enforced in code:
- Apply needs record `status == 'analyzed'` (`'Calibration analysis has not been completed yet'`) and at least one metal with `fit.valid` (`'No metal has made a valid return'`), plus a maintenance session (`/api/calibration/apply` is in `admin_only`, 403 `'This operation is only for maintenance personnel (maintenance PIN) '`).
- A run is rejected by the analyzer when the shift is at the search edge, exceeds `max_shift_nm`, or the correlation is below `min_correlation` (2.3).

Not enforced (advice only):
- **R² < 0.995 shows a warning and does not block**: `'R² lower than 0.995: Please confirm the standard product and measurement status before application'` (`web/calibration/app.js:376`).
- No limit on RSD, on residuals, on slope sign, or on the number of points beyond "2 distinct concentrations".

### 3.6 Applying, the active calibration (`controller.py:1305-1364`)

`apply_calibration(record_id)`:
- for each metal with a valid fit: `config[metal] = {'a': format(a, '.10g'), 'b': format(b, '.10g'), 'c': '1'}`; other metals keep their coefficients.
- writes `data/calibration_config.json` and `data/calibration_active.json = {'calibration_id', 'applied_at', 'metals': [applied], 'method_id', 'unit': 'mg/L', 'previous': <coefficients before>}`.
- record gains `applied_at`, `applied_metals`, `previous_config`; audit `calibration_apply`.
- returns `{'applied', 'config', 'active'}`.

`_calibration_context()` (cached per `calibration_id` + `applied_at`) → what every measurement is evaluated against:
```
{'calibration_id', 'applied_at',
 'method_id': active.method_id, or 'reanalysed-not-applied' when the record was re-analysed with another method after it was applied,
 'metals': active.metals,
 'blank_norm': {metal: mean norm_area over the valid kept blank rows of the calibration analysis},
 'reference': {'axis', 'values'} of the calibration's blank reference, or None}
```

Manual coefficients: `POST /api/calibration-config` (maintenance) validates and writes `calibration_config.json` directly (`server.py:345-351`); `calibration_active.json` is not touched by it.

### 3.7 `calibration_config.py`

- `COEFFICIENTS = ('a', 'b', 'c')`; `DEFAULT_CALIBRATION_CONFIG` = all `None`; **but** when `calibration_config.json` is missing the controller starts with `a '1', b '0', c '1'` for each metal (`controller.py:131-133`).
- `validate_calibration_config`: exactly the three metals (`'calibration parameters need to include Cu, Zn, Ni'`), each exactly `a,b,c` (`'<metal> calibration parameters need to include a, b, c'`); value `None` or a number/string ≤ 64 chars parsed as `Decimal`, finite, `|v| ≤ 1e12`; stored as `str(Decimal)`. Messages `'<metal> of <k> must be a finite value or left blank'`, `'… must be a finite value'`, `'… beyond the allowed range'`.
- `X_DEFINITION = 'background_corrected_peak'`, `FORMULA = 'y = (a * x + b) * c'`.
- `GET /api/calibration-config` → `{'config', 'complete', 'provisional': True, 'x_definition', 'formula', 'calculation_available': False}` — these two flags are stale relative to `measurements.py`, which does compute concentrations.
- `calculate_concentration(config, metal, peak)` is not imported by `controller.py` (line 28) or `server.py` (line 22); whether anything else uses it was not checked.

### 3.8 Calibration record shape (`data/calibrations/CAL-<32 hex>.json`)

```
id, created_at, status, operator, plan, recipe {id, name, sha256}, prime_recipe {id, name, sha256} | null,
warnings [..], runs [{level, level_name, run, discard, round, job, finished_at}], flow_id,
frames_per_sum, finished_at, error,
reference {axis, values, blank_count, jobs},
analysis {analyzed_at, method_id, frames_per_sum, config, config_path, jobs [analyze rows, scaled], fits {metal: fit}},
exclude_levels {metal: [index]}, applied_at, applied_metals, previous_config
```
Statuses: `running`, `analyzing`, `analyzed`, `analysis_failed`, `interrupted` (HMI closed mid-run or mid-analysis, error `'HMI Closed during calibration execution or analysis'`), `stopped`, `failed` (copied from the flow). UI labels (`web/calibration/app.js:5-6`): `is executing, Analyzing, Analyzed, Analysis failed, failed, Stopped, interrupt, Applied` (`applied` is a display state: the record id equals the active calibration).

`summary(record)`: `id, created_at, status, operator, error, applied_at, finished_at, recipe_name, levels, runs, method_id, fits {metal: {valid, a, b, r2, n}}`. `list_records` newest first, limit 50. `write_json` writes `.tmp` then replaces, retrying `PermissionError` 10 × 0.1 s.

### 3.9 Calibration endpoints (`server.py`)

| Method, path | Who | Body → result |
|---|---|---|
| GET `/api/calibration` | anyone | `{plan, records, active, coefficients, recipes, unit, metals, flow{id,status,mode,calibration_id,prompt,round,rounds,phase,current_step,step_count,error,recipe_name,events[-30:]}, rpi_connected, demo}` |
| GET `/api/calibrations/<id>` | anyone | full record |
| POST `/api/calibration/plan` | anyone | `{plan}` → save draft |
| POST `/api/calibration/check` | anyone | `{plan}` → `{plan, warnings, recipe_name, prime_recipe_name, prime_delay_seconds, runs, kept_runs, round_delay_seconds, estimated_seconds}` |
| POST `/api/calibration/start` | anyone | `{plan, confirm_high_voltage: true}` → 202 `{..., calibration_id}`; 409 without the confirmation |
| POST `/api/calibration/confirm` | anyone | `{}` → continue after the bottle swap; 409 when no prompt |
| POST `/api/calibration/analyze` | anyone | `{id}` → 202 `{accepted, id}` |
| POST `/api/calibration/exclude` | anyone | `{id, exclude}` → `{accepted, exclude_levels}` |
| POST `/api/calibration/apply` | maintenance | `{id}` → `{applied, config, active}` |
| GET/POST `/api/calibration-config` | GET anyone / POST maintenance | coefficients |

Errors: `ValueError` → 400, `RuntimeError`/`TimeoutError` → 409 (`server.py:406-409`). Stop = `POST /api/workflow-stop`; a stopped run keeps its finished rounds (`status 'stopped'`).

Calibration page texts worth keeping (`web/calibration/app.js`): tabs `setup` / `run` / `results`; summary rows `concentration number (including blank)`, `The number of rounds included in the regression`, `Total number of measurement rounds (including pre-run)`, `Waiting time for each round`, `Fluid replacement and draining`, `Estimated total time (excluding fluid change)`; start dialog title `Start standard calibration`, button `Confirm high voltage and start`, HV text `activates high-voltage plasma every round. Please confirm that the detector cover is closed, no one is touching the high-voltage parts on site, and a physical stop method is in place.`; result labels `a (slope) `, `b (intercept) `, `c (fine-tuning, reset when applied)`, `R²`, `Points`, formula line `concentration = (a × light intensity + b) × c unit mg/L`; table headers `included, standard product, concentration, n, average light intensity, SD, RSD %` and `round, concentration, light intensity x, regression value, residual`; chart axes `light intensity x (RPi area method)`, `concentration y (mg/L)`; buttons `application coefficient` / `Reapply`, dialog `Apply correction coefficient` with `needs repair PIN Login. The old coefficients are kept in the audit log.`; header line `Current coefficient (YYYY/MM/DD correction):…` or `Current coefficient (uncorrected page):…`.

---

## 4. (c) Measurement records and trend data

### 4.1 When a record is made (`controller.py:938-961, 1366-1407`)

After every `plasma_run` that is **not** part of a calibration: spectra are fetched and archived (2.8), then `_measure(outdir, step)`:
1. `kind = flow['sample_kind'] or 'sample'` (`KINDS = ('sample', 'blank')`; `'The measurement type must be sample or blank'`).
2. `analyze {'jobs': [outdir], 'reference': context.reference or null}`.
3. `record = evaluate(kind, scale_for_frame_sum(row, current N), reply.method_id, context, coefficients, load_qc())`; `record['frames_per_sum'] = N`.
4. if `kind == 'sample'` and a calibration is applied and the record is valid: `apply_zero(...)`.
5. any exception → `failed(kind, outdir, str(exc), context)`. **A failed analysis never fails the run.**
6. `measure_store.add(info | record)` where `info = {flow_id, schedule_id, slot, round, recipe_name}`; a short `summary` is appended to `flow['measurements']`.

### 4.2 Record fields (`data/measurements.jsonl`, one JSON object per line)

```
id            uuid hex (Store.add)
time          UTC ISO (Store.add)
flow_id, schedule_id, slot, round, recipe_name
kind          'sample' | 'blank'
job           RPi job folder
method_id     analysis method of this reply (None when the analysis failed)
valid         row.valid is True (i.e. the shift was acceptable)
error         row error text ('' when none; first 300 chars for failed())
shift_nm      max over metals of |p0 + p1 × centre of the moved peak window|; |p0| when no peak window; None when no shift was applied
calibration_id   applied calibration or None
conc_reason   '' | 'Correction has not been applied yet' | 'RPi The analysis settings are different from the applied calibration, please recalibrate.' | 'This analysis is invalid' | 'Analysis failed'
frames_per_sum
metals {Cu|Zn|Ni: {
    intensity            None when the metal item is invalid
    conc                 mg/L or None
    norm_area            only when the analyzer returned one
    norm_change_percent  blanks only, when a blank_norm reference exists
    conc_raw, zero       samples after apply_zero (zero only when subtracted)
}}
qc           blanks only: {'status': 'ok' | 'fail' | 'unchecked', 'problems': [..], 'reason': ''}
blank_zero   samples only: {metal: {'applied', 'zero', 'n', 'enabled'}}
```
`conc` is set only when: a calibration is applied, `context.method_id == method_id`, the row is valid, the metal item is valid, and the metal is in `context['metals']` (`measurements.py:208, 223-224`).

`Store` (`measurements.py:261-287`): append-only file, last `KEEP_IN_MEMORY = 5000` lines in memory, `latest(kind)`, `since(cutoff_iso, limit)`.

`summary(record)` kept on the run-log row: `{'id', 'kind', 'valid', 'conc': {metal: conc}, 'conc_reason', 'qc': status}`. Run-log rows carry `sample_kind` and `measurements` (`controller.py:62`).

### 4.3 Blank zero (`measurements.py:35-106`, `data/blank_zero.json`)

- Settings: `{'enabled': {Cu, Zn, Ni: bool}, 'window': 3–30, 'min_blanks': 1–30 (≤ window), 'mad_reject': bool}`; default `{'enabled': {'Cu': False, 'Zn': False, 'Ni': True}, 'window': 10, 'min_blanks': 3, 'mad_reject': True}`. Messages: `'Blank zero point setting field:enabled, window, min_blanks, mad_reject'`, `'enabled Need to be {Metal: true/false}'`, `'The number of times to take blanks must be 3–30 integer'`, `'The minimum number of blanks must be 1–30 integer'`, `'The minimum number of blanks cannot be greater than the number of blanks taken'`, `'mad_reject Need to be true/false'`.
- Blanks used: in-memory records with `kind == 'blank'`, the same `calibration_id` and the same `method_id` (`controller.py:1415-1418`), `valid` true, finite `conc_raw`/`conc`; QC pass or fail does not matter.
- Per metal: last `window` values → if `mad_reject` and ≥ 3 values: `middle = median`, `spread = median(|v − middle|) × 1.4826`, and when `spread > 0` keep `|v − middle| ≤ 3 × spread` → `ready = len(used) ≥ min_blanks` → `zero = median(used)` when ready.
- Status per metal: `{'enabled', 'ready', 'zero', 'n', 'rejected'}`.
- `apply_zero`: `conc_raw = conc` always; subtract when `enabled and ready and conc is not None`: `conc = conc − zero`, `item['zero'] = zero`. Only new sample measurements are affected.
- Endpoint: `POST /api/blank-zero {settings}` (maintenance) → `{'settings', 'status'}`.

### 4.4 Blank QC (`measurements.py:32-33, 158-175, 231-248`, `data/blank_qc.json`)

- Thresholds: `{'max_shift_nm': 0.05, 'max_apparent_conc': {Cu: 0.05, Zn: 0.05, Ni: 0.05}, 'max_norm_change_percent': 20}`; limits: shift 0.001–1 nm, apparent conc 0–1000 mg/L, norm change 1–100 %. Messages: `'Blank threshold field:max_shift_nm, max_apparent_conc, max_norm_change_percent'`, `'The blank apparent concentration threshold must be Cu/Zn/Ni'`, `'displacement threshold (nm) Must be between 0.001–1'`, `'<m> apparent concentration threshold (mg/L) Must be between 0–1000'`, `'Normalized area change threshold (%) Must be between 1–100'`.
- When there is a `conc_reason`: `{'status': 'unchecked', 'problems': [], 'reason': reason + ',Unable to determine baseline'}` (failed analysis: `'Analysis failed, unable to determine baseline'`).
- Otherwise problems:
  - `shift_nm > max_shift_nm` → `'spectral shift 0.080 nm (threshold 0.05) '`
  - `abs(conc) > limit` → `'Cu apparent concentration 0.19 mg/L (threshold ±0.05) '` (uses the blank's own uncorrected concentration)
  - `change = (norm_area / blank_norm − 1) × 100`, `abs(change) > max_norm_change_percent` → `'Cu Normalized area change +30.0% (threshold ±20%) '` — **only when both a `norm_area` and a `blank_norm` exist; with the deployed config (no `normalize` windows) this check is never evaluated.**
  - `status = 'fail'` when any problem, else `'ok'`.
- A failed check never stops a schedule: log `'Blank exceeds baseline threshold:…'`, audit `blank_qc_fail`; unchecked: log `'Blank is not compared:…'`. UI strings (`web/i18n.js:44-45`): `Blank ✓ baseline is normal`, `Blank ⚠ Beyond baseline threshold`, `Blank (not compared)`, `Analysis failed`, `Blank solution exceeds baseline threshold:`. The doc calls it a medium-priority alarm (`CALIBRATION.zh-TW.md:74`); where the alarm itself is raised was not located in the lines read.
- Interaction with the analyzer limit: deployed `max_shift_nm` is 0.1, QC limit 0.05 → a blank shifted 0.05–0.1 nm fails QC; beyond 0.1 nm the analysis itself is invalid → `'This analysis is invalid'` → QC `unchecked`.
- Endpoint: `POST /api/blank-qc {qc}` (maintenance) → `{'qc'}`.

### 4.5 Blanks in a schedule

`start(csv, hv, name, schedule_slot, kind)` sets `flow_extra = {'sample_kind': kind}` (`controller.py:855`). Schedule field `'blank': {'recipe_id', 'recipe_name', 'every': N}`: **the first slot is a blank, then one blank after every N samples**; skipped slots do not count (test `ScheduleBlankTests`: N = 2, 7 slots → blank, sample, sample, blank, sample, sample, blank). Doc: "Not inserted" disables it (`CALIBRATION.zh-TW.md:84`).

### 4.6 Trend data (`GET /api/measurements[?hours=N]`, `server.py:154-164`; page `web/trend/app.js`)

- `hours` integer 1–8784, else all in-memory rows.
- Reply: `{'rows', 'qc', 'calibration': {calibration_id, applied_at, metals} | null, 'coefficients', 'blank_zero': {'settings', 'status'}, 'unit': 'mg/L'}`.
- Page: one chart per metal (`copper Cu`, `zinc Zn`, `nickel Ni`); three modes: `conc` (title suffix ` (mg/L) `), `raw` = `conc_raw` when present (` (mg/L,(zero point not deducted)`), `intensity` (` (light intensity) `).
- Series: samples solid line + filled circle `#2f5f8f`; blanks dashed line + diamond `#c27a00`; a blank with `qc.status == 'fail'` is a larger filled diamond `#a3168e`; band `±max_apparent_conc[metal]` labelled `blank threshold ±<limit>` in the concentration modes.
- Time windows: rolling (`recent N Hour` / `recent N sky` / `All information`) or fixed from–to (max one year); optional per-metal y limits (converted to intensity with `(conc / c − b) / a`). Stored in `localStorage` keys `hmi_trend_hours`, `hmi_trend_mode`, `hmi_trend_view`.
- Click on a point: `<time> · sample|Blank · <metal> <value> mg/L| (light intensity) · Zero point has been deducted z (original r)  · Displacement 0.012 nm · baseline is normal | ⚠ <problems> · <recipe>`.
- Notice when no concentration exists: `There is no concentration data for this period (calibration has not been applied, or RPi Analysis settings and calibration are different). You can switch ”Light Intensity” to view the original trend.`
- Threshold/zero dialog (maintenance): per metal shows the zero, `Correction has not been applied yet`, or `Insufficient blank space (n/min) `, and `n times, eliminated r`. Error `needs repair PIN: Please click on the left first🔧 Maintenance” login`.
- `/api/state` → `measurement: {latest_sample, latest_blank}`, `calibration_active: {calibration_id, applied_at, metals, method_id}`, `frames_per_sum` (`controller.py:504-523`).

---

## 5. (d) How concentration is reported

- Formula: **concentration = (a × intensity + b) × c**, computed with `Decimal(str(...))` and returned as float; `None` on any conversion error (`measurements.concentration`, `:178-183`).
- Unit: `mg/L` (hard-coded; no unit conversion anywhere).
- `c` is a manual trim factor; applying a calibration resets it to `'1'`.
- Reported value for samples is the zero-corrected `conc`; the uncorrected one is `conc_raw`.
- Log lines (`controller.py:1394-1407`): `'sample concentration:Cu 0.1234, Zn …, Ni … mg/L'` (`.4g`), `'<sample|Blank>Light intensity has been calculated; concentration has not been converted:<reason>'`, `'<sample|Blank>Analysis not completed: <error or reason>'`.
- Number formats in the UI: calibration page `fmt` = fixed digits, exponential (3) for `|v| ≥ 1e5` or `< 1e-3`; a, b with `toPrecision(6)`; R² 4–5 decimals; trend `fmt` = 1 decimal for ≥ 100, 3 decimals for ≥ 1, else 3 significant digits.
- Negative concentrations are not clipped anywhere. No detection limit, no "below LOD" flag, no upper range flag.

Flags that qualify a reported number:

| Flag | Meaning |
|---|---|
| `valid` | the shift fit was acceptable (analyzer) |
| `error` | analyzer problem text |
| `conc_reason` | why no concentration was computed |
| `method_id` vs active `method_id` | mismatch → no concentration (`'RPi The analysis settings are different from the applied calibration, please recalibrate.'`) |
| `calibration_id` | which calibration was applied |
| `shift_nm` | spectral shift at the analysis lines |
| `qc.status / problems / reason` | blank baseline check |
| `blank_zero[metal].applied / zero / n / enabled` | zero subtraction on a sample |
| `frames_per_sum` | intensity scale used |
| `loop_aligned`, `has_frames`, `background_frames`, `signal_frames` | spectrum sets only |
| `concentration_available: False`, `provisional: True`, `calculation_available: False` | legacy/stale markers (2.8, 3.7) |

---

## 6. (e) Dependence on the number of background frames and on its timing

The merged program will take ONE background frame. What in the HMI code is affected:

**Hard dependencies on "3" (acquisition side, not the analysis):**
1. `rpi_tools/plasma_job.py:395-396` `start_spectrum(..., frames=3, ...)` and `:429` `collect_spectrum(child, deadline, expected_frames=3)`; the background call at `:513-514` relies on both defaults. With these unchanged a 1-frame background is rejected with `'Spectrum capture report is incomplete'`.
2. `rpi_tools/plasma_job.py:731`: the no-discharge capture deadline `3 * args.integration_ms / 1000 + 15`.
   - [cross-check addition] The Windows side also insists on 3 for this **no-discharge** capture: `manual_rpi('spectrum')` rejects any reply whose `frames` list is not exactly three long (`RPi raw Spectrum response is incomplete`, `controller.py:705-707`), reads `spectrum-1..3.csv` by SFTP, and the CSV download route accepts only frame numbers `1|2|3` (`server.py:214`); the unused V1 path `SSHAdapter.capture` has the same `!= 3` check (`backend.py:193`). This is the stand-alone `capture raw spectrum` / test-page capture (`CAP-<hex>` records), not the background group of a `plasma_run` job, so it is outside the "one background frame" change unless that capture is changed too.
3. Seen by grep in the EC copy (`autonomous control/rpi/tcpd/plasmic_tcpd.py:183, 983, 1097`): `background_s = 3 * parameter['integration_ms'] / 1000 + 6`, `'frames_total': 3`. Not read in full; the EC historical-development-notes.md lists `rpi\rpi_tools\` among files that must not be changed.

**Count-agnostic (work unchanged with one frame):**
4. `spectrum_analysis._mean_group` (`:224-237`) reads however many `spectrum-N.csv` exist (1–1000) and divides by the count; needs at least `background/spectrum-1.csv`, else `'no background frames in <dir>'`. `background_frames` is only reported.
5. `remote_result.summarize_group` (`:42-46`) and `spectrum_result.load_result` (`:95-98`) accept `1 ≤ count ≤ 1000`, but require `job.json`'s `background.valid is True`, `frames` metadata of the same length with names `spectrum-1.csv…`, `frames_expected == count` when present, and `pixels` equal to the data.
6. `tests/test_spectrum_result.py:94-121` already exercises **one** background frame (`'background': {'valid': True, 'pixels': 3, 'frames': [{'file': 'spectrum-1.csv'}]}`, `count: 1`); `tests/test_calibration.py` writes 2 or 3 background frames per job. No test asserts "3".
7. `method_id` does not include the background frame count → a calibration made with 3 background frames still "matches" measurements made with 1; nothing forces a recalibration.
8. `frame_differences` and `mean_difference` subtract the mean background → with one frame they subtract that frame; `background_mean` stored in the spectrum set is that frame.
9. `frames_per_sum` scaling multiplies the already-subtracted result; it does not depend on the background count.
10. Shift correction uses Pearson correlation (offset and gain do not matter) → insensitive to the dark level.
11. The legacy Cu/Ni readback uses raw signal frames only → independent of the background.

**Structural dependency (per-job background):**
12. With `subtract_dark: true`, **every job folder must contain its own `background/` group**. The analyzer has no notion of a background shared between jobs. If the merged program took one background per session or per schedule instead of per discharge, each job folder would still need a `background/spectrum-1.csv` (copy or link) or the analysis fails.
13. Setting `subtract_dark: false` changes `method_id` → applied calibrations stop matching (`'RPi The analysis settings are different from the applied calibration, please recalibrate.'`).

**Timing:**
14. No file read here checks when the background was taken relative to the discharge (no timestamp comparison, no ordering check). The "before the discharge, HSPY OFF" order exists only in `plasma_job.run_plasma` (`:507-525`). The log text is `'background spectrum completed'` before `'Signal spectrum SDK Prepared N frame'`.
15. The grep of the EC tcpd shows a `plasma_quick.py` path whose "background comes after the supply is OFF" (`plasmic_tcpd.py:985-987`, phase `background_after`). The HMI analysis would read such a folder the same way; whether an after-discharge background is physically equivalent is not something the code answers.

**Numerical effect (my inference, not stated in the code):**
16. Subtracting one frame instead of the mean of three leaves that frame's pixel noise in every difference spectrum as a fixed pattern (it does not average down with more signal frames). Local baselines (`bg_left`/`bg_right`) remove an offset but not pixel-to-pixel noise. The deployed Cu method is `metric: "peak"` (maximum over the peak pixels), which is biased upward by noise; Ni/Zn use area/mean and are less sensitive. Blank apparent concentrations, blank zero and the fitted `b` were all established with 3-frame backgrounds, so a recalibration after the change is the safe assumption. No measurement of the size of this effect exists in the files read.
17. Area-method `norm_area` (when a `normalize` window is used) integrates the dark-subtracted spectrum without a baseline, so it depends on the absolute dark level; irrelevant for the deployed config (all `normalize: null`).

---

## 7. (f) Formulas, exactly

Notation: `S` = mean of signal frames per pixel, `B` = mean of background frames per pixel, `λ_i` pixel centres.

1. Spectrum: `V_i = S_i − B_i` (`subtract_dark`), else `V_i = S_i`. (`spectrum_analysis.py:249-255`)
2. Shift per window: `d* = argmax_d Pearson( R(x_k), V(x_k + d) )` over `d = i·step`, `|i| ≤ round(search/step)`; refinement `d ← d* + step·0.5·(y0 − y2)/(y0 − 2y1 + y2)` when `y0 − 2y1 + y2 < 0` and not at the edge. (`:312-332`)
3. Shift model: one window `p0 = d, p1 = 0`; several: least squares `d = p1·centre + p0`. Window bound `w' = w + p0 + p1·w`. (`:335-354, 497-500`)
4. Pixel edges: `e_0 = λ_0 − (λ_1 − λ_0)/2`, `e_i = (λ_{i−1} + λ_i)/2`, `e_n = λ_{n−1} + (λ_{n−1} − λ_{n−2})/2`; `area(lo, hi) = Σ_i V_i · max(0, min(hi, e_{i+1}) − max(lo, e_i))`. (`:373-393`)
5. Area method: `L_left = area(bg_left)/width`, `L_right = area(bg_right)/width`, at centres `x1`, `x2`; `baseline = L_left + (L_right − L_left)(m − x1)/(x2 − x1)` with `m` = peak centre (one side → that level; none → 0); `net = area(peak) − baseline·(peak_hi − peak_lo)`; `intensity = net / area(normalize)` or `net`. (`:526-548`)
6. Pixel method: `net_i = V_i − base(λ_i)` for peak pixels; `signal = max(net)` | `mean(net)` | `Σ (λ_b − λ_a)(net_a + net_b)/2`; `intensity = signal / metric(V over normalize pixels)` or `signal`. Baselines: lin2 / min2 / const / polyN as in 2.4. (`:419-490`)
7. Multi-line: `intensity = Σ_j weight_j · signal_j`. (`:508-519`)
8. Reference: `R_i = (1/n) Σ_blanks spline_blank(λ_i + p0 + p1·λ_i)`, first blank unshifted. (`:562-591`)
9. Frame sum: `peak_area, net_area, signal, norm_area, baseline_level` × N; `intensity` × N when there is no `norm_area`; `lines[].signal` × N. (`measurements.py:123-144`)
10. Regression (per metal, x = intensity, y = mg/L): `a = Σ(x−x̄)(y−ȳ)/Σ(x−x̄)²`, `b = ȳ − a·x̄`, `R² = 1 − Σ(y − a·x − b)²/Σ(y − ȳ)²`; `fitted = a·x + b`, `residual = y − fitted`. (`calibration.py:177-188, 256-258`)
11. Level statistics: `mean`, `sd = sqrt(Σ(v − mean)²/(n−1))`, `rsd% = |sd/mean|·100`. (`:191-196`)
12. Concentration: `conc = (a·intensity + b)·c`. (`measurements.py:178-183`)
13. Shift at the lines: `shift_nm = max_metal |p0 + p1·(peak'_lo + peak'_hi)/2|`. (`:186-196`)
14. Blank QC: `shift_nm > max_shift_nm`; `|conc| > max_apparent_conc[metal]`; `|(norm_area/blank_norm − 1)·100| > max_norm_change_percent`. (`:231-248`)
15. Blank zero: `zero = median(used)`, `used` = last `window` blank values after rejecting `|v − median| > 3·1.4826·MAD`; `conc = conc_raw − zero`. (`:69-106`)
16. `blank_norm[metal]` = mean of `norm_area` over the calibration's valid kept blank rows. (`controller.py:1349-1356`)
17. Legacy readback: `abs(round( mean_frames | P/D − (L/D + (R/D − L/D)(x_P − x_L)/(x_R − x_L)) | × 100000 ))`, 8 digits. (`spectrum_result.py:41-61`)
18. Trend y-limit conversion: `intensity = (conc / c − b) / a`. (`web/trend/app.js:139-145`)
19. `method_id = 'area-v1-' + sha256(canonical JSON of {'version','config'})[:12]`. (`spectrum_analysis.py:175-178`)

---

## 8. Persistence inventory (files under the HMI data folder; names from code, contents not opened)

| File | Written by | Shape |
|---|---|---|
| `calibration_config.json` | apply / manual edit | `{metal: {'a','b','c'}}` decimal strings or null |
| `calibration_active.json` | apply | `{calibration_id, applied_at, metals, method_id, unit, previous}` |
| `calibration_plan.json` | plan draft | plan as entered |
| `calibrations/CAL-<hex>.json` | calibration run | 3.8 |
| `measurements.jsonl` | every non-calibration plasma_run | 4.2 |
| `blank_qc.json` | maintenance | 4.4 |
| `blank_zero.json` | maintenance | 4.3 |
| `frame_sum.json` | maintenance | `{frames_per_sum, updated_at}` |
| `spectra/SPEC-<hex>.json`, `.meta.json` | every plasma_run / import | 2.8 |

On the RPi: `<tools_dir>/analysis_config.json`, job folders under `output_dir` (HMI names them `HMI-<uuid hex>`, captures `CAP-<uuid hex>`).

## 9. What the tests pin down

`tests/test_calibration.py` (synthetic axis: 1933 pixels from 200 nm, pitch 0.0777 nm; `write_job` writes equal numbers of flat-200 background and signal frames plus `job.json {'valid': True, 'output_dir'}`):
- partial-pixel integration; shift recovery within 0.002 nm for −0.031…0.045 nm under a 1.3× gain; shifted and unshifted intensities equal within 1 %; 0.4 nm shift → invalid.
- config validation and `method_id` stability; every baseline kind valid; multi-line sum = `parts[0] + 0.5·parts[1]`.
- frame-sum scaling equals summing two frames (relative 1e-9); normalized intensity unchanged; `validate_frame_sum` rejects `0, 51, 1.5, True, '2'`.
- CLI: `config` writes the default file; `reference` on two blanks → `blank_count 2`; missing folder → row invalid.
- plan sorting, blank rule, duplicate names, warnings; 3 levels × (1 discard + 2 kept) → 9 `plasma_run`, 3 prompts.
- full run over HTTP: start without HV → 409; `check` → `runs 9, kept_runs 6`; three prompts confirmed; record `analyzed`; 3 discard runs; `reference.blank_count 2`; each fit `n 6`, `r2 > 0.999`; apply 403 without a session, 200 with; `config.Cu.c == '1'`.
- after calibration: clean blank → `qc ok`; sample within 5 %; no `qc` on samples; drifted blank (Cu 0.3 mg/L, 0.12 nm) → `fail` with a Displacement problem and `Cu apparent concentration`; `/api/state`, `/api/measurements?hours=24`, `/api/run-log` contents; `/api/blank-qc`, `/api/blank-zero`, `/api/frame-sum` → 403 without a session.
- blank zero: fewer than 3 blanks → not ready; `[.9, .06, .26, .05, .07, .065]` with window 5 → `n 4, rejected 1, zero 0.0625`.

`tests/test_spectrum_result.py`: readback `'00100000'` for the worked example; job-directory validation; 16 signal frames summarised; 3 background + 16 signal → `loop_aligned False` (not triggered); 1 background + 2 signal → `mean_difference [10, 20, 10]`, `frame_differences [[9, 18, 9], [11, 22, 11]]`, archive dedupe on the job folder.

## 10. Not determined

- `hardware_verify.py spectrum` (the SDK capture) is not in `hmi_v1_1/rpi_tools/`; how it handles `--frames 1` was not read.
- Whether the EC RPi currently has `spectrum_analysis.py`/`analysis_config.json` installed. The EC `rpi/rpi_tools/` folder lists only `hardware_verify.py`, `nano_verify.py`, `plasma_job.py`, `pulse_prepare.py`, so the concentration analysis is not part of EC as shipped; where it will run in the merged program (RPi over the TCP link, or on the PC after fetching frames) is a design decision.
- Whether the deployed windows suit the spectrometer now on the rig (doc: OES-C210-FUV18, 200–350 nm; EC historical-development-notes.md: OES-C225). The Ni line at 341 nm and the 330.5–333 nm shift window sit near the top of a 200–350 nm range.
- Whether "one background frame" means one per discharge or one per session/schedule (see 6.12).
- Where the "Blank solution exceeds baseline threshold" alarm is raised (only the i18n strings and the log/audit lines were seen).
- The size of the numerical effect of a single background frame on the blank level and on the Cu `peak` metric (6.16) — inference only.
- The existing calibration's acquisition settings (30 pulses × 40 frames, `frames_per_sum 2`) come from a config comment; the current values in the HMI data folder were not opened by rule.

---

## Cross-check

Done 2026-10-06 by a second reader. Read completely and compared with this report: `calibration.py` (339 lines), `measurements.py` (295), `calibration_config.py` (61), `spectrum_result.py` (136), `spectrum_store.py` (73), `controller.py` (2052), `server.py` (473), `rpi_tools/analysis_config.json`. Read in part: `rpi_tools/spectrum_analysis.py` lines 170-260 and 554-647, `rpi_tools/plasma_job.py` lines 390-447 and 472-586; searches in `web/calibration/app.js`. The file list of `rpi_tools/` was taken (its `__pycache__` was not opened). Nothing was run. No `data` folder was opened.

### Claims checked against the source and found correct

1. Background = 3 frames taken before the discharge with the supply OFF: `start_spectrum(..., frames=3, ...)`, the background call passes no frame count, `collect_spectrum(child, deadline, expected_frames=3)` raises `Spectrum capture report is incomplete` unless `frames_expected == 3` and the files are `spectrum-1..3.csv`; deadline `clock() + 40` (`rpi_tools/plasma_job.py:395-396,429,443-446,513-515`).
2. Order inside `run_plasma`: `trigger.low()` → `require_off` → set-point check → `prepare` → background → ARM → signal child (`frames=loops`, triggered) → `power_write(4, 1)` (`plasma_job.py:506-525`).
3. Signal frames = `Loopnum`; `signal_target_frames = loops` (`plasma_job.py:485,497,521-522,581`).
4. `hardware_verify.py` is not in `hmi_v1_1/rpi_tools/` (the folder holds `analysis_config.json`, `manifest.json`, `nano_verify.py`, `plasma_job.py`, `pulse_prepare.py`, `spectrum_analysis.py`).
5. `_mean_group` reads `spectrum-1.csv`, `-2`, … until one is missing and divides by the count; zero frames → `no <group> frames in <dir>`; `job_spectrum` subtracts the mean background only when `subtract_dark` (`spectrum_analysis.py:224-259`).
6. `method_id` = `'area-v1-' + sha256(canonical {'version','config'})[:12]` — the analysis config only (`spectrum_analysis.py:175-178`).
7. `build_reference`, the `analyze` row shape, the `main` reply wrapper and its catch-all `{'valid': False, 'error': '<Type>: <msg>'}` (`spectrum_analysis.py:562-646`).
8. The deployed `analysis_config.json` as quoted in 2.5: `subtract_dark: true`, shift windows, `max_shift_nm 0.1`, Cu `poly3` / `peak`, Ni three weighted lines, Zn `poly1` / `mean`, every `normalize` null.
9. `scale_for_frame_sum`: `SUMMED_KEYS` × N, `intensity` × N only without `norm_area`, `lines[].signal` × N; N = 1 returns the row untouched (`measurements.py:123-144`).
10. Calibration analysis uses the record's own `frames_per_sum`, each measurement the current setting; nothing compares the two (`controller.py:1194,1265-1266,1378-1381`).
11. `load_result`: both groups `1 ≤ count ≤ 1000`, metadata / file-name / pixel checks, `The number of signal spectra does not match the number of rounds`, axis check, `frame_differences` against the mean background, all result keys (`spectrum_result.py:76-136`).
12. Legacy `LINES` table and `metal_readback` arithmetic (`spectrum_result.py:10-13,41-61`).
13. `job_directory` rule `(LITE|HMI)-<32 hex>` under the output root and its two error texts (`spectrum_result.py:17-34`).
14. `spectrum_store`: `SUMMARY_KEYS`, dedupe on `remote_dir`, `SPEC-….json` + `.meta.json`, id check text (`spectrum_store.py:15-73`).
15. Every `validate_plan` rule and message in 3.1, the constants, `DEFAULT_PLAN`, the `plan_warnings` text (`calibration.py:28-135`).
16. `build_steps`: exactly one `plasma_run`, prime Recipe without RPi steps, per-level order prompt → prime → discard + kept rounds, the `calibration` tag (`calibration.py:146-172`).
17. `linear_fit`, point selection, exclusion reasons, `Insufficient effective points Is 2 different concentrations`, fit object keys, level statistics, blank RSD = `None` (`calibration.py:177-270`).
18. `apply_calibration`: `format(x, '.10g')`, `c = '1'`, the two files written, the record fields added, the two error texts (`controller.py:1305-1330`).
19. `_calibration_context` including the `'reanalysed-not-applied'` marker and `blank_norm` (`controller.py:1339-1364`).
20. `validate_calibration_config` rules and texts; `X_DEFINITION`, `FORMULA`; `calculate_concentration` is not imported by `controller.py` or `server.py` (`calibration_config.py`).
21. `evaluate`: the four `conc_reason` values and the five conditions for a concentration; `failed()`; `Store` (`measurements.py:199-287`).
22. Blank zero: settings limits and texts, selection of blanks, MAD rule with 1.4826 and 3 ×, `apply_zero` (`measurements.py:42-106`; `controller.py:1415-1418`).
23. Blank QC: defaults, limits, the three checks with their texts, the `unchecked` reasons (`measurements.py:158-175,231-248`).
24. First slot is a blank, then one blank after every N samples; slots that did not finish as `done` do not count (`controller.py:1642,1655-1656,1690`).
25. `GET /api/measurements` reply and the `hours` rule 1…8784 (`server.py:154-164`).
26. R² < 0.995 is a page warning only (`web/calibration/app.js:376`); the start body is `{plan, confirm_high_voltage: true}` (`app.js:216`).

No statement of this report was found to be wrong.

### Corrections made in the text above

- 3.2 item 1: the generated `operator_prompt` step also has `'target': ''`, `'value': ''`, `'unit': ''`, `'line': 0`; the report's rendering dropped them. The runner's log line needs them.
- 3.1: marked the rule "a metal needs ≥ 2 distinct values" as unreachable by reading (the mandatory all-zero blank is always one of the values).
- Section 6, item 2: the list of hard dependencies on "3" named only RPi code. Added the PC-side checks for the no-discharge capture (`controller.py:705-707`, `server.py:214`, `backend.py:193`). The statement that a `plasma_run` job's background count is free on the PC side stays correct.

### Additions (not in the report before)

- `calibration.load` has two more texts: `calibration record ID format is incorrect`, `Calibration record does not exist` (`calibration.py:304-310`); they reach the page as 404 for `GET /api/calibrations/<id>` and as 400 for the POST endpoints.
- `request_analysis` (the `Reanalysis` button) can be pressed by an operator without a PIN and overwrites `reference` and `analysis` of a record, including the record that is currently **applied** (`controller.py:1217-1285`). What that does to later measurements depends on a cache: `_calibration_context` keeps the context per `(calibration_id, applied_at)` in memory (`controller.py:1344-1346`), so while the HMI keeps running and a context was already built, nothing changes. After the next HMI start (or when no measurement had been evaluated yet) the context is rebuilt from the record: if the re-analysis used another analysis config, the method becomes `'reanalysed-not-applied'` and every measurement gets "no concentration" (`RPi The analysis settings are different from the applied calibration, please recalibrate.`) until maintenance applies again; if the config is the same, the coefficients stay but the reference spectrum used for shift correction is the re-analysed one (`controller.py:1347-1363`). So the same applied calibration can behave differently before and after a restart.
- `set_calibration_exclusions` (operator, no PIN) rewrites `analysis.fits` of a record, also of the applied one. It does not change the coefficients in use (`calibration_config.json` is written only by apply or by the manual coefficient page); it changes what the results page shows and what a later `application coefficient` / `Reapply` would write.
- `blank_norm` is built only from `norm_area` values that are truthy (`controller.py:1352-1356`), and the deployed config has no `normalize` window, so `blank_norm` is `{}` for a calibration analysed with it — consistent with 4.4.
- The measurement record's `time` is the moment the analysis result was stored, not the discharge time (`measurements.py:275-276`); the discharge start is only in the spectrum set (`captured_at` = `job.started_at`) and on the RPi.
- `measurements.jsonl` is append-only without rotation; only the newest 5000 lines are loaded at start (`measurements.py:264-273`). With a 10-minute schedule that is about 35 days; older points silently leave the trend page.
- For the "one background frame" change: the Windows side needs no change (items 5 and 11 above accept a count of 1), and no stored number depends on the count. The only code that insists on 3 is on the RPi (`plasma_job.py:395-396,429,513-514`, and the capture deadline at `:731` per this report). Whether `hardware_verify.py spectrum --frames 1` works could not be read here.

### Still open after the cross-check

- Sections 2.3, 2.4 and the validation table in 2.5 (`spectrum_analysis.py:98-172, 264-552`) were not re-read line by line; only the commands, the frame reader and `method_id` were.
- `remote_result.py` was not re-read.
- Everything in section 10.
