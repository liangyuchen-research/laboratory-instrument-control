"""Area-based metal intensities with spectral shift correction.

Ported from the original HMI (hmi_v1_1/rpi_tools/spectrum_analysis.py), where it ran on the
RPi over the job folders. Here it runs on this PC over the stored spectrum sets
(runs/<job>.json). Pure functions, standard library only; the numerical code is unchanged.
Every tunable choice (integration windows, normalization, shift windows) comes from the
analysis settings (data/analysis_config.json, see validate_config).

Definitions (every window is [low_nm, high_nm] on the reference axis):
  spectrum    signal (mean of the signal frames) - background when subtract_dark
  shift d     maximizes the Pearson correlation between the reference and the
              sample evaluated at lambda + d inside each shift window (a gain
              and offset between them do not matter). One window -> one shift for
              the whole spectrum; two or more -> d(lambda) = p0 + p1 * lambda.
  windows     are moved by d instead of resampling the spectrum, then integrated
              on the original pixels with partial edge pixels (area kept exact).
              A natural cubic spline is used only to compare spectra between pixels
              during the shift search and to average the blanks into the reference.
  baseline    straight line through the mean levels of bg_left and bg_right
              (one side only -> flat line; both null -> no baseline)
  net_area    integral over peak of (spectrum - baseline)
  intensity   net_area / area(normalize), or net_area when normalize is null

Optional per metal (omitted = the area method above, unchanged method_id):
  baseline    lin2 | min2 | const | poly1 | poly2 | poly3, fitted to the pixels whose
              centres lie in bg_left/bg_right: lin2 = line through the two window means,
              min2 = line through the lowest pixel of each window, const = mean of all
              background pixels, polyN = least-squares polynomial of degree N
  metric      area | peak | mean of (spectrum - baseline) over the pixels whose centres
              lie in the peak window (area = trapezoid over those pixel centres)
  normalize   same metric over the raw spectrum in the normalize window

Several lines of one metal (instead of peak/bg_left/bg_right):
  "lines": [{"peak", "bg_left", "bg_right", "baseline", "metric", "weight"}, ...]
              each line is evaluated as above on its own windows; the metal's intensity
              is sum(weight * line signal). normalize must be null.

Error texts are the original's English ones; the pages show them after “Analysis failed: ”.
"""
import bisect
import hashlib
import json
import math

ANALYSIS_VERSION = 'area-v1'
BASELINES = ('lin2', 'min2', 'const', 'poly1', 'poly2', 'poly3')
METRICS = ('area', 'peak', 'mean')
LINE_KEYS = {'peak', 'bg_left', 'bg_right', 'baseline', 'metric', 'weight'}
MAX_LINES = 5
MIN_PIXELS = 8
MAX_PIXELS = 8192

# The original's built-in default (its tests use it). The settings a new data folder starts
# with are in analysis_default.json, not here.
DEFAULT_CONFIG = {
    'subtract_dark': True,
    'shift': {
        'enabled': True,
        'windows_nm': [[260.8, 263.8], [305.6, 307.0]],
        'search_nm': 0.5,
        'step_nm': 0.005,
        'max_shift_nm': 0.3,
        'min_correlation': 0.95,
    },
    'metals': {
        'Cu': {'peak': [327.15, 327.55], 'bg_left': [325.70, 326.05], 'bg_right': [327.85, 328.20],
               'normalize': [325.70, 326.05]},
        'Ni': {'peak': [231.45, 231.85], 'bg_left': [230.90, 231.25], 'bg_right': [232.25, 232.60],
               'normalize': [233.15, 233.50]},
        'Zn': {'peak': [213.75, 213.97], 'bg_left': [212.90, 213.25], 'bg_right': [214.30, 214.65],
               'normalize': [214.30, 214.65]},
    },
}


# ── configuration ──────────────────────────────────────────────────────────

def _window(value, label, optional=False):
    if value is None and optional:
        return None
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)
            or not 100 <= value[0] < value[1] <= 1200):
        raise ValueError(label + ' must be [low_nm, high_nm] with low < high')
    return [float(value[0]), float(value[1])]


def validate_config(raw):
    if not isinstance(raw, dict) or set(raw) - {'subtract_dark', 'shift', 'metals', 'comment'}:
        raise ValueError('analysis config keys: subtract_dark, shift, metals')
    shift = raw.get('shift', DEFAULT_CONFIG['shift'])
    if not isinstance(shift, dict) or set(shift) - set(DEFAULT_CONFIG['shift']):
        raise ValueError('shift keys: ' + ', '.join(DEFAULT_CONFIG['shift']))
    shift = dict(DEFAULT_CONFIG['shift'], **shift)
    if not isinstance(shift['enabled'], bool):
        raise ValueError('shift.enabled must be true or false')
    if not isinstance(shift['windows_nm'], list) or not 1 <= len(shift['windows_nm']) <= 5:
        raise ValueError('shift.windows_nm needs 1-5 windows')
    windows = [_window(w, 'shift window') for w in shift['windows_nm']]
    limits = {'search_nm': (0.01, 3), 'step_nm': (0.0005, 0.05), 'max_shift_nm': (0.001, 3),
              'min_correlation': (0, 1)}
    for key, (low, high) in limits.items():
        value = shift[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f'shift.{key} must be {low}-{high}')
    if shift['max_shift_nm'] > shift['search_nm']:
        raise ValueError('shift.max_shift_nm cannot exceed shift.search_nm')
    metals = raw.get('metals')
    if not isinstance(metals, dict) or not 1 <= len(metals) <= 10:
        raise ValueError('metals needs 1-10 entries')
    clean_metals = {}
    for name, item in metals.items():
        if not isinstance(name, str) or not name.isalnum() or len(name) > 8:
            raise ValueError('metal names must be short alphanumeric symbols')
        if isinstance(item, dict) and 'lines' in item:
            clean_metals[name] = _validate_lines(name, item)
            continue
        if not isinstance(item, dict) or set(item) - {'peak', 'bg_left', 'bg_right', 'normalize', 'baseline', 'metric'}:
            raise ValueError(name + ' keys: peak, bg_left, bg_right, normalize, baseline, metric (or lines)')
        clean_metals[name] = {'peak': _window(item.get('peak'), name + '.peak')}
        for key in ('bg_left', 'bg_right', 'normalize'):
            clean_metals[name][key] = _window(item.get(key), f'{name}.{key}', optional=True)
        # Only stored when given, so configs without them keep their method_id.
        for key, allowed in (('baseline', BASELINES), ('metric', METRICS)):
            if key in item:
                if item[key] not in allowed:
                    raise ValueError(f'{name}.{key} must be one of ' + ', '.join(allowed))
                clean_metals[name][key] = item[key]
    subtract = raw.get('subtract_dark', True)
    if not isinstance(subtract, bool):
        raise ValueError('subtract_dark must be true or false')
    return {'subtract_dark': subtract,
            'shift': dict(shift, windows_nm=windows, search_nm=float(shift['search_nm']),
                          step_nm=float(shift['step_nm']), max_shift_nm=float(shift['max_shift_nm']),
                          min_correlation=float(shift['min_correlation'])),
            'metals': clean_metals}


def _validate_lines(name, item):
    if set(item) - {'lines', 'normalize'} or item.get('normalize') is not None:
        raise ValueError(name + ': with "lines" only normalize: null may be given beside it')
    lines = item['lines']
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise ValueError(f'{name}.lines needs 1-{MAX_LINES} lines')
    clean = []
    for index, line in enumerate(lines):
        label = f'{name}.lines[{index}]'
        if not isinstance(line, dict) or set(line) - LINE_KEYS:
            raise ValueError(label + ' keys: ' + ', '.join(sorted(LINE_KEYS)))
        weight = line.get('weight', 1)
        if (isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight)
                or weight == 0 or abs(weight) > 1e6):
            raise ValueError(label + '.weight must be a finite non-zero number')
        entry = {'peak': _window(line.get('peak'), label + '.peak'),
                 'bg_left': _window(line.get('bg_left'), label + '.bg_left', optional=True),
                 'bg_right': _window(line.get('bg_right'), label + '.bg_right', optional=True),
                 'baseline': line.get('baseline', 'lin2'), 'metric': line.get('metric', 'area'),
                 'weight': float(weight)}
        if entry['baseline'] not in BASELINES or entry['metric'] not in METRICS:
            raise ValueError(f'{label}: baseline one of {", ".join(BASELINES)}; metric one of {", ".join(METRICS)}')
        clean.append(entry)
    return {'lines': clean, 'normalize': None}


def method_id(config):
    canonical = json.dumps({'version': ANALYSIS_VERSION, 'config': config}, sort_keys=True,
                           separators=(',', ':'))
    return ANALYSIS_VERSION + '-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]


# ── spectra ────────────────────────────────────────────────────────────────

def _numbers(values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _frames(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def spectrum_of(run, config):
    """Signal (minus the background) of one stored spectrum set of a plasma_run.

    run: the dict kept in runs/<job>.json - 'axis', 'signal' (mean of the signal frames),
    'background' (the background frame, or the mean of several). Replaces the original's
    job_spectrum, which read the frame files on the RPi.
    """
    name = str(run.get('job') or '?') if isinstance(run, dict) else '?'
    if not isinstance(run, dict) or not isinstance(run.get('axis'), list) or not isinstance(run.get('signal'), list):
        raise ValueError('no signal frames in ' + name)
    axis, signal = run['axis'], run['signal']
    if len(axis) > MAX_PIXELS:
        raise ValueError('spectrum too long ' + name)
    if len(axis) < MIN_PIXELS:
        raise ValueError('spectrum too short ' + name)
    if len(signal) != len(axis):
        raise ValueError('signal length differs from the axis in ' + name)
    dark = None
    if config['subtract_dark']:
        dark = run.get('background')
        if not isinstance(dark, list):
            raise ValueError('no background frames in ' + name)
        if len(dark) != len(axis):
            raise ValueError('signal and background axes differ in ' + name)
    if not _numbers(axis) or not _numbers(signal) or (dark is not None and not _numbers(dark)):
        raise ValueError('nonfinite spectrum ' + name)
    if any(b <= a for a, b in zip(axis, axis[1:])):
        raise ValueError('wavelength axis not increasing in ' + name)
    if dark is None:
        values = [float(s) for s in signal]
    else:
        values = [s - d for s, d in zip(signal, dark)]
    return {'axis': [float(x) for x in axis], 'values': values,
            'signal_frames': _frames(run.get('signal_frames')),
            'background_frames': _frames(run.get('background_frames')) if dark is not None else 0}


# ── numerics ───────────────────────────────────────────────────────────────

class Spline:
    """Natural cubic spline. Used only to evaluate a spectrum between pixels while searching
    for the shift and to average blanks into the reference; areas are never taken from it.
    (A monotone PCHIP flattens peak tops, which biased the shift on narrow lines.)"""

    def __init__(self, xs, ys):
        n = len(xs)
        self.xs, self.ys = xs, ys
        h = [xs[i + 1] - xs[i] for i in range(n - 1)]
        # Tridiagonal system for the second derivatives, natural ends (M0 = Mn-1 = 0).
        lower, diag, upper, rhs = [0.0] * n, [1.0] * n, [0.0] * n, [0.0] * n
        for i in range(1, n - 1):
            lower[i], diag[i], upper[i] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
            rhs[i] = 6 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])
        for i in range(1, n):
            factor = lower[i] / diag[i - 1]
            diag[i] -= factor * upper[i - 1]
            rhs[i] -= factor * rhs[i - 1]
        second = [0.0] * n
        second[-1] = rhs[-1] / diag[-1]
        for i in range(n - 2, -1, -1):
            second[i] = (rhs[i] - upper[i] * second[i + 1]) / diag[i]
        self.h, self.second = h, second

    def __call__(self, x):
        xs = self.xs
        if x <= xs[0]:
            return self.ys[0]
        if x >= xs[-1]:
            return self.ys[-1]
        i = bisect.bisect_right(xs, x) - 1
        h = self.h[i]
        a, b = (xs[i + 1] - x) / h, (x - xs[i]) / h
        return (a * self.ys[i] + b * self.ys[i + 1]
                + ((a ** 3 - a) * self.second[i] + (b ** 3 - b) * self.second[i + 1]) * h * h / 6)


def _correlation(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sab = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    saa = sum((x - ma) ** 2 for x in a)
    sbb = sum((y - mb) ** 2 for y in b)
    if saa <= 0 or sbb <= 0:
        return 0.0
    return sab / math.sqrt(saa * sbb)


def window_shift(ref_axis, ref_values, sample, window, search, step):
    """Shift d (nm) of the sample relative to the reference inside one window."""
    xs = [x for x in ref_axis if window[0] <= x <= window[1]]
    if len(xs) < 5:
        raise ValueError(f'shift window {window} has fewer than 5 pixels')
    lo = bisect.bisect_left(ref_axis, xs[0])
    ref = ref_values[lo:lo + len(xs)]
    count = int(round(search / step))
    grid = [i * step for i in range(-count, count + 1)]
    scores = [_correlation(ref, [sample(x + d) for x in xs]) for d in grid]
    best = max(range(len(grid)), key=scores.__getitem__)
    d = grid[best]
    on_edge = best in (0, len(grid) - 1)
    if not on_edge:
        # Parabola through the best grid point and its neighbours: sub-step refinement.
        y0, y1, y2 = scores[best - 1], scores[best], scores[best + 1]
        curvature = y0 - 2 * y1 + y2
        if curvature < 0:
            d += step * 0.5 * (y0 - y2) / curvature
    return {'window_nm': window, 'center_nm': (window[0] + window[1]) / 2, 'shift_nm': d,
            'correlation': _correlation(ref, [sample(x + d) for x in xs]), 'at_search_edge': on_edge}


def fit_shift(reference, spectrum, settings):
    """Shift model d(lambda) = p0 + p1*lambda from the configured windows."""
    sample = Spline(spectrum['axis'], spectrum['values'])
    windows = [window_shift(reference['axis'], reference['values'], sample, window,
                            settings['search_nm'], settings['step_nm'])
               for window in settings['windows_nm']]
    problems = []
    for item in windows:
        if item['at_search_edge']:
            problems.append(f'shift at search limit in {item["window_nm"]}')
        if abs(item['shift_nm']) > settings['max_shift_nm']:
            problems.append(f'shift {item["shift_nm"]:.4f} nm exceeds {settings["max_shift_nm"]} nm')
        if item['correlation'] < settings['min_correlation']:
            problems.append(f'correlation {item["correlation"]:.3f} below {settings["min_correlation"]}')
    if len(windows) == 1:
        p0, p1 = windows[0]['shift_nm'], 0.0
    else:
        p1, p0, _ = linear_fit([w['center_nm'] for w in windows], [w['shift_nm'] for w in windows])
    return {'applied': True, 'p0': p0, 'p1': p1, 'windows': windows,
            'valid': not problems, 'problems': problems}


def linear_fit(xs, ys):
    """Least squares y = a*x + b; returns (a, b, r2)."""
    n = len(xs)
    if n < 2:
        raise ValueError('linear fit needs at least 2 points')
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError('linear fit needs at least 2 different x values')
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - a * mx
    total = sum((y - my) ** 2 for y in ys)
    residual = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    return a, b, (1 - residual / total) if total > 0 else 1.0


def pixel_edges(axis):
    edges = [axis[0] - (axis[1] - axis[0]) / 2]
    edges += [(a + b) / 2 for a, b in zip(axis, axis[1:])]
    edges.append(axis[-1] + (axis[-1] - axis[-2]) / 2)
    return edges


def integrate(axis, edges, values, low, high):
    """Area over [low, high] counting edge pixels by their covered fraction."""
    if low < edges[0] or high > edges[-1]:
        raise ValueError(f'window {low:.3f}-{high:.3f} nm is outside the spectrum')
    first = max(0, bisect.bisect_right(edges, low) - 1)
    total = 0.0
    for i in range(first, len(axis)):
        left, right = edges[i], edges[i + 1]
        if left >= high:
            break
        overlap = min(high, right) - max(low, left)
        if overlap > 0:
            total += values[i] * overlap
    return total


def _solve(matrix, rhs):
    """Gaussian elimination with partial pivoting for the small normal equations."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError('background fit is singular (too few background pixels)')
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


def _pixels(axis, window):
    return [i for i, x in enumerate(axis) if window[0] <= x <= window[1]]


def baseline_function(axis, values, windows, kind):
    """Baseline f(lambda) fitted to the pixels of the background windows (see module doc)."""
    groups = [_pixels(axis, w) for w in windows if w]
    if any(not g for g in groups):
        raise ValueError('background window holds no pixel')
    if not groups:
        return lambda x: 0.0
    if kind in ('lin2', 'min2'):
        if kind == 'min2':
            points = [(axis[i], values[i]) for i in (min(g, key=values.__getitem__) for g in groups)]
        else:
            points = [(sum(axis[i] for i in g) / len(g), sum(values[i] for i in g) / len(g)) for g in groups]
        if len(points) == 1 or points[0][0] == points[1][0]:
            level = sum(p[1] for p in points) / len(points)
            return lambda x: level
        (x1, y1), (x2, y2) = points
        slope = (y2 - y1) / (x2 - x1)
        return lambda x: y1 + slope * (x - x1)
    pixels = [i for g in groups for i in g]
    if kind == 'const':
        level = sum(values[i] for i in pixels) / len(pixels)
        return lambda x: level
    degree = int(kind[-1])
    if len(pixels) < degree + 1:
        raise ValueError(f'{kind} background needs at least {degree + 1} pixels')
    centre = sum(axis[i] for i in pixels) / len(pixels)
    scale = max(abs(axis[i] - centre) for i in pixels) or 1.0
    size = degree + 1
    normal = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size
    for i in pixels:
        t = (axis[i] - centre) / scale
        powers = [t ** k for k in range(2 * degree + 1)]
        for r in range(size):
            rhs[r] += powers[r] * values[i]
            for c in range(size):
                normal[r][c] += powers[r + c]
    coefficients = _solve(normal, rhs)
    return lambda x: sum(c * ((x - centre) / scale) ** k for k, c in enumerate(coefficients))


def window_metric(axis, values, pixels, kind):
    """values may be a list or a {pixel: value} dict."""
    if not pixels:
        raise ValueError('window holds no pixel')
    if kind == 'peak':
        return max(values[i] for i in pixels)
    if kind == 'mean':
        return sum(values[i] for i in pixels) / len(pixels)
    return sum((axis[b] - axis[a]) * (values[a] + values[b]) / 2 for a, b in zip(pixels, pixels[1:]))


def _pixel_method(axis, values, windows, peak, left, right, norm):
    """Baseline function + area/peak/mean on pixel centres (viewer-compatible)."""
    kind, metric = windows.get('baseline', 'lin2'), windows.get('metric', 'area')
    base = baseline_function(axis, values, [left, right], kind)
    pixels = _pixels(axis, peak)
    net = {i: values[i] - base(axis[i]) for i in pixels}
    signal = window_metric(axis, net, pixels, metric)
    item = {'valid': True, 'baseline': kind, 'metric': metric, 'net_area': signal, 'signal': signal,
            'baseline_level': base((peak[0] + peak[1]) / 2),
            'windows_used': {'peak': peak, 'bg_left': left, 'bg_right': right}}
    if norm is not None:
        norm_value = window_metric(axis, values, _pixels(axis, norm), metric)
        item['windows_used']['normalize'] = norm
        item['norm_area'] = norm_value
        if norm_value <= 0:
            raise ValueError('normalization value is not positive')
        item['intensity'] = signal / norm_value
    else:
        item['intensity'] = signal
    return item


def metal_intensities(spectrum, metals, shift):
    axis, values = spectrum['axis'], spectrum['values']
    edges = pixel_edges(axis)

    def moved(window):
        if window is None:
            return None
        return [w + shift['p0'] + shift['p1'] * w for w in window]

    def area(window):
        return integrate(axis, edges, values, window[0], window[1])

    results = {}
    for name, windows in metals.items():
        try:
            if 'lines' in windows:
                parts, total = [], 0.0
                for line in windows['lines']:
                    one = _pixel_method(axis, values, line, moved(line['peak']), moved(line['bg_left']),
                                        moved(line['bg_right']), None)
                    parts.append({'peak': one['windows_used']['peak'], 'signal': one['signal'],
                                  'weight': line['weight']})
                    total += line['weight'] * one['signal']
                results[name] = {'valid': True, 'baseline': 'lines', 'metric': 'weighted_sum',
                                 'net_area': total, 'signal': total, 'intensity': total, 'lines': parts,
                                 'windows_used': {'peak': parts[0]['peak']}}
                continue
            peak = moved(windows['peak'])
            left, right = moved(windows['bg_left']), moved(windows['bg_right'])
            if 'baseline' in windows or 'metric' in windows:
                results[name] = _pixel_method(axis, values, windows, peak, left, right,
                                              moved(windows['normalize']))
                continue
            levels = [((w[0] + w[1]) / 2, area(w) / (w[1] - w[0])) for w in (left, right) if w]
            middle = (peak[0] + peak[1]) / 2
            if len(levels) == 2:
                (x1, y1), (x2, y2) = levels
                baseline = y1 + (y2 - y1) * (middle - x1) / (x2 - x1)
            elif levels:
                baseline = levels[0][1]
            else:
                baseline = 0.0
            peak_area = area(peak)
            net = peak_area - baseline * (peak[1] - peak[0])
            item = {'valid': True, 'peak_area': peak_area, 'baseline_level': baseline, 'net_area': net,
                    'windows_used': {'peak': peak, 'bg_left': left, 'bg_right': right}}
            norm = moved(windows['normalize'])
            if norm is not None:
                norm_area = area(norm)
                item['windows_used']['normalize'] = norm
                item['norm_area'] = norm_area
                if norm_area <= 0:
                    raise ValueError('normalization area is not positive')
                item['intensity'] = net / norm_area
            else:
                item['intensity'] = net
            results[name] = item
        except (ValueError, ZeroDivisionError) as exc:
            results[name] = {'valid': False, 'error': str(exc)}
    return results


# ── reference and rows ─────────────────────────────────────────────────────

def _no_shift(reason):
    return {'applied': False, 'p0': 0.0, 'p1': 0.0, 'windows': [], 'valid': True, 'problems': [],
            'reason': reason}


def build_reference(named_spectra, config):
    """Align every blank spectrum to the first and average them on the first one's axis.

    named_spectra: [(job, spectrum)] of the readable blanks, in measuring order
    (spectrum = what spectrum_of returns).
    """
    spectra = list(named_spectra)
    rows = []
    if not spectra:
        raise ValueError('no readable blank job for the reference spectrum')
    base = spectra[0][1]
    axis = base['axis']
    total = [0.0] * len(axis)
    used = 0
    for job, spectrum in spectra:
        if spectrum is base or not config['shift']['enabled']:
            shift = _no_shift('first blank' if spectrum is base else 'shift disabled')
        else:
            shift = fit_shift(base, spectrum, config['shift'])
        if not shift['valid']:
            rows.append({'job': job, 'used': False, 'shift': shift,
                         'error': 'blank not aligned: ' + '; '.join(shift['problems'])})
            continue
        sample = Spline(spectrum['axis'], spectrum['values'])
        for i, x in enumerate(axis):
            total[i] += sample(x + shift['p0'] + shift['p1'] * x)
        used += 1
        rows.append({'job': job, 'used': True, 'shift': shift})
    return {'axis': axis, 'values': [value / used for value in total], 'blank_count': used,
            'jobs': rows, 'method_id': method_id(config)}


def check_reference(reference):
    """None, or {'axis': [...], 'values': [...]} of equal length (8 or more)."""
    if reference is not None:
        if (not isinstance(reference, dict) or not isinstance(reference.get('axis'), list)
                or not isinstance(reference.get('values'), list)
                or len(reference['axis']) != len(reference['values']) or len(reference['axis']) < 8):
            raise ValueError('reference spectrum is malformed')


def analyze_one(spectrum, reference, config):
    """Shift and metal intensities of one spectrum: the original's "analyze" row without 'job'.

    A spectrum that cannot be analyzed gives {'valid': False, 'error'}; a malformed reference raises.
    """
    check_reference(reference)
    try:
        if not config['shift']['enabled']:
            shift = _no_shift('shift disabled')
        elif reference is None:
            shift = _no_shift('no reference spectrum')
        else:
            shift = fit_shift(reference, spectrum, config['shift'])
        metals = metal_intensities(spectrum, config['metals'], shift)
        return {'valid': shift['valid'], 'shift': shift, 'metals': metals,
                'signal_frames': spectrum['signal_frames'],
                'background_frames': spectrum['background_frames'],
                'error': '; '.join(shift['problems'])}
    except ValueError as exc:
        return {'valid': False, 'error': str(exc)}
