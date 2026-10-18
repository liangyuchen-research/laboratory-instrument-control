"""pc/analysis.py: area analysis with shift correction, on synthetic spectra.

The cases of the original HMI's tests/test_calibration.py (AnalyzerTests) with the original's
synthetic spectra and expected numbers, plus the stored-spectrum-set reader and the settings rules.
No equipment, no network, no files outside the program folder are read.

    python -B tests/test_analysis.py
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pc'))

import analysis as sa  # noqa: E402

PIXEL = 0.0777  # the original spectrometer's pixel pitch (OES-C210-FUV18, 1933 pixels from 200 nm)
AXIS = [200 + i * PIXEL for i in range(1933)]
MATRIX = [(261.30, 3000, .05), (262.42, 2500, .06), (263.15, 1800, .05), (305.99, 4000, .05),
          (306.43, 3500, .05), (281.17, 5000, .06)]
LINES = {'Cu': 327.40, 'Ni': 231.65, 'Zn': 213.86}
# The spectrometer on this rig, as its stored sets show it: 1862 pixels, 179.991 to 500.126 nm.
RIG_AXIS = [round(179.991 + i * (500.126 - 179.991) / 1861, 3) for i in range(1862)]
DEFAULT_FILE = ROOT / 'pc' / 'analysis_default.json'


def gauss(x, center, height, sigma):
    """Mean of a Gaussian line over one pixel, as a detector pixel collects it."""
    scale = sigma * math.sqrt(2)
    low, high = (x - PIXEL / 2 - center) / scale, (x + PIXEL / 2 - center) / scale
    return height * sigma * math.sqrt(math.pi / 2) * (math.erf(high) - math.erf(low)) / PIXEL


def synthetic(conc, shift=0.0, gain=1.0):
    """Continuum 1000 + matrix bands + metal lines (500 counts per mg/L), all moved by ``shift``."""
    values = []
    for x in AXIS:
        v = 1000.0
        for center, height, sigma in MATRIX:
            v += gauss(x, center + shift, height, sigma)
        for metal, center in LINES.items():
            v += gauss(x, center + shift, 500 * conc.get(metal, 0), .04)
        values.append(v * gain)
    return values


def job_id(number):
    return 'HMI-%032x' % number


def stored_run(job, values, signal_frames=2, background_frames=1, dark=200.0, axis=AXIS):
    """A spectrum set as the program stores it after a plasma_run (runs/<job>.json): the keys and
    the rounding of the RPi's "result" reply; one background frame, taken before the discharge."""
    background = [dark] * len(axis)
    signal = [v + dark for v in values]
    return {'job': job, 'kind': 'plasma', 'started_at': '2026-10-06T06:00:00+00:00',
            'finished_at': '2026-10-06T06:00:20+00:00', 'flow': 'unified', 'voltage_v': 300, 'model': 'TEST',
            'serial': 'T0', 'integration_ms': 1000, 'pixels': len(axis), 'background_frames': background_frames,
            'signal_frames': signal_frames, 'steps': [], 'axis': [round(x, 3) for x in axis],
            'background': [round(v, 2) for v in background], 'signal': [round(v, 2) for v in signal],
            'difference': [round(s - b, 2) for s, b in zip(signal, background)], 'frames': None,
            'saved_at': '2026-10-06T06:00:21+00:00'}


def copy_of(value):
    return json.loads(json.dumps(value))


class AnalyzerTests(unittest.TestCase):
    """Ported from the original: the numbers are the original's."""

    def setUp(self):
        self.config = sa.validate_config(sa.DEFAULT_CONFIG)

    def test_integration_counts_partial_pixels(self):
        axis = [float(i) for i in range(10)]
        edges = sa.pixel_edges(axis)
        self.assertAlmostEqual(sa.integrate(axis, edges, [2.0] * 10, 2.25, 5.75), 2.0 * 3.5)

    def test_recovers_known_shift_despite_gain_change(self):
        reference = {'axis': AXIS, 'values': synthetic({})}
        for shift in (-0.031, 0.0, 0.0123, 0.045):
            sample = {'axis': AXIS, 'values': synthetic({'Cu': 1}, shift, gain=1.3)}
            result = sa.fit_shift(reference, sample, self.config['shift'])
            self.assertTrue(result['valid'], result['problems'])
            for window in result['windows']:
                self.assertAlmostEqual(window['shift_nm'], shift, delta=0.002)

    def test_shifted_spectrum_gives_same_intensity_as_unshifted(self):
        reference = {'axis': AXIS, 'values': synthetic({})}
        plain = sa.metal_intensities({'axis': AXIS, 'values': synthetic({'Cu': 2, 'Ni': 1, 'Zn': 1})},
                                     self.config['metals'], sa._no_shift('test'))
        shifted_values = synthetic({'Cu': 2, 'Ni': 1, 'Zn': 1}, 0.06)
        shift = sa.fit_shift(reference, {'axis': AXIS, 'values': shifted_values}, self.config['shift'])
        moved = sa.metal_intensities({'axis': AXIS, 'values': shifted_values}, self.config['metals'], shift)
        for metal in LINES:
            self.assertAlmostEqual(moved[metal]['intensity'], plain[metal]['intensity'],
                                   delta=abs(plain[metal]['intensity']) * 0.01)

    def test_shift_beyond_limit_marks_job_invalid(self):
        reference = {'axis': AXIS, 'values': synthetic({})}
        result = sa.fit_shift(reference, {'axis': AXIS, 'values': synthetic({}, 0.4)}, self.config['shift'])
        self.assertFalse(result['valid'])
        self.assertTrue(any('exceeds 0.3 nm' in problem for problem in result['problems']), result['problems'])

    def test_config_rejects_bad_windows(self):
        bad = copy_of(sa.DEFAULT_CONFIG)
        bad['metals']['Cu']['peak'] = [327.5, 327.1]
        with self.assertRaisesRegex(ValueError, 'low < high'):
            sa.validate_config(bad)
        changed = copy_of(sa.DEFAULT_CONFIG)
        changed['metals']['Zn']['bg_left'] = None
        self.assertNotEqual(sa.method_id(sa.validate_config(changed)), sa.method_id(self.config))

    def test_baseline_and_metric_options(self):
        # Omitted options keep the area method and its method_id.
        self.assertEqual(sa.method_id(sa.validate_config(copy_of(sa.DEFAULT_CONFIG))), sa.method_id(self.config))
        raw = copy_of(sa.DEFAULT_CONFIG)
        raw['metals']['Cu'].update(normalize=None, baseline='poly2', metric='peak',
                                   bg_left=[326.2, 326.9], bg_right=[327.9, 328.6], peak=[327.2, 327.6])
        config = sa.validate_config(raw)
        self.assertNotEqual(sa.method_id(config), sa.method_id(self.config))
        # Curved background under the line: a quadratic fitted to both sides follows it exactly.
        curve = [1000 + 40 * (x - 327.4) ** 2 for x in AXIS]
        for conc in (0, 1, 2):
            values = [c + gauss(x, 327.40, 500 * conc, .04) for x, c in zip(AXIS, curve)]
            item = sa.metal_intensities({'axis': AXIS, 'values': values}, config['metals'], sa._no_shift('t'))['Cu']
            self.assertEqual((item['baseline'], item['metric']), ('poly2', 'peak'))
            peak_pixel = max((i for i, x in enumerate(AXIS) if 327.2 <= x <= 327.6), key=lambda i: values[i])
            self.assertAlmostEqual(item['intensity'], values[peak_pixel] - curve[peak_pixel], delta=0.5)
        for kind in sa.BASELINES:
            raw['metals']['Cu']['baseline'] = kind
            flat = [1000.0 + gauss(x, 327.40, 500, .04) for x in AXIS]
            item = sa.metal_intensities({'axis': AXIS, 'values': flat}, sa.validate_config(raw)['metals'],
                                        sa._no_shift('t'))['Cu']
            self.assertTrue(item['valid'], kind)
        raw['metals']['Cu']['metric'] = 'median'
        with self.assertRaisesRegex(ValueError, 'metric'):
            sa.validate_config(raw)

    def test_several_lines_of_one_metal_are_added_with_weights(self):
        raw = copy_of(sa.DEFAULT_CONFIG)
        single = {'peak': [231.45, 231.85], 'bg_left': [230.90, 231.25], 'bg_right': [232.25, 232.60],
                  'baseline': 'const', 'metric': 'area'}
        other = {'peak': [327.15, 327.55], 'bg_left': [326.2, 326.9], 'bg_right': [327.9, 328.6],
                 'baseline': 'lin2', 'metric': 'peak'}
        raw['metals']['Ni'] = {'normalize': None, 'lines': [dict(single, weight=1), dict(other, weight=0.5)]}
        config = sa.validate_config(raw)
        values = synthetic({'Cu': 1, 'Ni': 2})
        result = sa.metal_intensities({'axis': AXIS, 'values': values}, config['metals'], sa._no_shift('t'))['Ni']
        parts = []
        for line in (single, other):
            alone = copy_of(sa.DEFAULT_CONFIG)
            alone['metals'] = {'X': dict(line, normalize=None)}
            parts.append(sa.metal_intensities({'axis': AXIS, 'values': values}, sa.validate_config(alone)['metals'],
                                              sa._no_shift('t'))['X']['intensity'])
        self.assertAlmostEqual(result['intensity'], parts[0] + 0.5 * parts[1])
        self.assertEqual([line['weight'] for line in result['lines']], [1.0, 0.5])
        self.assertEqual((result['baseline'], result['metric']), ('lines', 'weighted_sum'))
        self.assertEqual(result['windows_used'], {'peak': [231.45, 231.85]})
        for bad in ({'lines': []}, {'lines': [dict(single, weight=0)]}, {'lines': [single], 'normalize': [233.1, 233.5]},
                    {'lines': [single], 'peak': [231.4, 231.8]}, {'lines': [dict(single, metric='median')]}):
            raw['metals']['Ni'] = bad
            with self.assertRaises(ValueError):
                sa.validate_config(raw)

    def test_intensities_scale_with_the_spectrum(self):
        """The original's frame-sum test without frames_per_sum: every metric is linear in the
        spectrum, so adding frames only scales the intensities (a normalized one not at all).
        That is why the setting was not carried over - the calibration absorbs the factor."""
        raw = copy_of(sa.DEFAULT_CONFIG)
        raw['metals']['Cu'].update(normalize=None, baseline='lin2', metric='area')
        raw['metals']['Zn'].update(normalize=None, baseline='poly2', metric='peak')
        config = sa.validate_config(raw)  # Ni keeps the default area method with normalization
        first = synthetic({'Cu': 1, 'Zn': .5, 'Ni': 2}, gain=1.0)
        second = synthetic({'Cu': 1, 'Zn': .5, 'Ni': 2}, gain=1.1)

        def analyze(values):
            return sa.metal_intensities({'axis': AXIS, 'values': values}, config['metals'], sa._no_shift('t'))
        summed = analyze([a + b for a, b in zip(first, second)])
        averaged = analyze([(a + b) / 2 for a, b in zip(first, second)])
        for metal in ('Cu', 'Zn'):
            self.assertAlmostEqual(2 * averaged[metal]['intensity'], summed[metal]['intensity'],
                                   delta=abs(summed[metal]['intensity']) * 1e-9)
        self.assertAlmostEqual(averaged['Ni']['intensity'], summed['Ni']['intensity'])  # a ratio
        self.assertAlmostEqual(2 * averaged['Ni']['norm_area'], summed['Ni']['norm_area'], delta=1e-6)

    def test_reference_and_rows_from_stored_runs(self):
        """The original's command-line test (reference, then analyze), on stored spectrum sets."""
        runs = [stored_run(job_id(index), synthetic(conc, shift))
                for index, (conc, shift) in enumerate([({}, 0.0), ({}, 0.01), ({'Cu': 1}, -0.02)])]
        spectra = [(run['job'], sa.spectrum_of(run, self.config)) for run in runs]
        reference = sa.build_reference(spectra[:2], self.config)
        self.assertEqual(reference['blank_count'], 2)
        self.assertEqual(reference['method_id'], sa.method_id(self.config))
        self.assertEqual([(row['job'], row['used']) for row in reference['jobs']],
                         [(job_id(0), True), (job_id(1), True)])
        self.assertEqual(reference['jobs'][0]['shift']['reason'], 'first blank')
        self.assertAlmostEqual(reference['jobs'][1]['shift']['p0'] + reference['jobs'][1]['shift']['p1'] * 280,
                               0.01, delta=0.003)
        self.assertEqual(len(reference['values']), len(AXIS))
        base = {'axis': reference['axis'], 'values': reference['values']}
        rows = [sa.analyze_one(spectrum, base, self.config) for _job, spectrum in spectra]
        for row in rows:
            self.assertEqual(set(row), {'valid', 'shift', 'metals', 'signal_frames', 'background_frames', 'error'})
            self.assertTrue(row['valid'], row['error'])
            self.assertEqual((row['signal_frames'], row['background_frames'], row['error']), (2, 1, ''))
        self.assertGreater(rows[2]['metals']['Cu']['intensity'], rows[0]['metals']['Cu']['intensity'])
        json.dumps(rows, allow_nan=False)  # a row must fit into a record file


class SpectrumOfTests(unittest.TestCase):
    """spectrum_of replaces the original's reading of the job folder."""

    def setUp(self):
        self.config = sa.validate_config(sa.DEFAULT_CONFIG)
        self.plain = dict(self.config, subtract_dark=False)

    def small(self, **changes):
        run = {'job': job_id(7), 'kind': 'plasma', 'axis': [200.0 + i for i in range(8)],
               'signal': [110.0, 120.5, 130.0, 140.0, 150.0, 160.0, 170.0, 180.25],
               'background': [10.0, 20.5, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0],
               'signal_frames': 40, 'background_frames': 1}
        run.update(changes)
        return run

    def test_one_background_frame_is_subtracted(self):
        run = self.small()
        before = copy_of(run)
        spectrum = sa.spectrum_of(run, self.config)
        self.assertEqual(spectrum['values'], [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.25])
        self.assertEqual(spectrum['axis'], run['axis'])
        self.assertEqual((spectrum['signal_frames'], spectrum['background_frames']), (40, 1))
        self.assertEqual(set(spectrum), {'axis', 'values', 'signal_frames', 'background_frames'})
        self.assertEqual(run, before)  # the stored set is not changed
        self.assertIsNot(spectrum['axis'], run['axis'])

    def test_a_real_shaped_set_with_one_background_frame(self):
        values = synthetic({'Cu': 1})
        run = stored_run(job_id(1), values, signal_frames=8, background_frames=1)
        spectrum = sa.spectrum_of(run, self.config)
        self.assertEqual((spectrum['signal_frames'], spectrum['background_frames']), (8, 1))
        self.assertEqual(len(spectrum['values']), 1933)
        for got, expected in zip(spectrum['values'][::97], values[::97]):
            self.assertAlmostEqual(got, expected, delta=0.011)  # the set is stored with 2 decimals

    def test_subtract_dark_false_ignores_the_background(self):
        run = self.small()
        spectrum = sa.spectrum_of(run, self.plain)
        self.assertEqual(spectrum['values'], run['signal'])
        self.assertEqual(spectrum['background_frames'], 0)
        for background in (None, 'x', [1.0], [float('nan')] * 8):  # not looked at
            self.assertEqual(sa.spectrum_of(self.small(background=background), self.plain)['values'], run['signal'])
        run.pop('background')
        self.assertEqual(sa.spectrum_of(run, self.plain)['values'], run['signal'])
        with self.assertRaisesRegex(ValueError, 'no background frames in ' + job_id(7)):
            sa.spectrum_of(run, self.config)

    def test_the_checks_of_the_original_reader(self):
        cases = [
            (dict(axis=[200.0 + i for i in range(7)], signal=[1.0] * 7, background=[0.0] * 7), 'spectrum too short'),
            (dict(axis=[200.0 + i * .01 for i in range(8193)], signal=[1.0] * 8193, background=[0.0] * 8193),
             'spectrum too long'),
            (dict(signal=[1.0] * 7), 'signal length differs from the axis'),
            (dict(background=[1.0] * 9), 'signal and background axes differ'),
            (dict(signal=[1.0] * 7 + [float('nan')]), 'nonfinite spectrum'),
            (dict(signal=[1.0] * 7 + [float('inf')]), 'nonfinite spectrum'),
            (dict(signal=[1.0] * 7 + ['8']), 'nonfinite spectrum'),
            (dict(signal=[1.0] * 7 + [True]), 'nonfinite spectrum'),
            (dict(background=[1.0] * 7 + [None]), 'nonfinite spectrum'),
            (dict(axis=[200.0, 201.0, 202.0, 203.0, 204.0, 205.0, 206.0, float('inf')]), 'nonfinite spectrum'),
            (dict(axis=[200.0, 201.0, 202.0, 202.0, 204.0, 205.0, 206.0, 207.0]), 'wavelength axis not increasing'),
            (dict(axis=[207.0 - i for i in range(8)]), 'wavelength axis not increasing'),
            (dict(signal=None), 'no signal frames'),
            (dict(axis='200'), 'no signal frames'),
            (dict(background=None), 'no background frames'),
        ]
        for changes, text in cases:
            with self.assertRaisesRegex(ValueError, text, msg=text):
                sa.spectrum_of(self.small(**changes), self.config)
        for not_a_set in (None, [], 'HMI-1', 5):
            with self.assertRaisesRegex(ValueError, 'no signal frames'):
                sa.spectrum_of(not_a_set, self.config)
        # A capture (no discharge) is not a measurement: it has no signal.
        capture = {'job': 'CAP-' + '0' * 32, 'kind': 'capture', 'axis': [200.0 + i for i in range(8)],
                   'mean': [1.0] * 8, 'frames': [[1.0] * 8] * 3}
        with self.assertRaisesRegex(ValueError, 'no signal frames in CAP-'):
            sa.spectrum_of(capture, self.config)
        # 8 and 8192 pixels are allowed; frame counts that are missing are reported as 0.
        self.assertEqual(len(sa.spectrum_of(self.small(), self.config)['values']), 8)
        long = dict(axis=[200.0 + i * .01 for i in range(8192)], signal=[1.0] * 8192, background=[0.0] * 8192)
        self.assertEqual(len(sa.spectrum_of(self.small(**long), self.config)['values']), 8192)
        loose = sa.spectrum_of(self.small(signal_frames=None, background_frames='1'), self.config)
        self.assertEqual((loose['signal_frames'], loose['background_frames']), (0, 0))


class NumericsTests(unittest.TestCase):
    """Small spectra whose results can be worked out by hand: axis 100..119 nm, one pixel per nm,
    a ramp of 10 + pixel number with 5 counts added on the three pixels 109, 110, 111."""

    AXIS = [100.0 + i for i in range(20)]
    VALUES = [10.0 + i + (5.0 if 9 <= i <= 11 else 0.0) for i in range(20)]
    WINDOWS = {'peak': [108.5, 111.5], 'bg_left': [103.5, 105.5], 'bg_right': [114.5, 116.5]}

    def one(self, values=None, **metal):
        config = sa.validate_config({'shift': {'enabled': False}, 'metals': {'X': dict(self.WINDOWS, **metal)}})
        spectrum = {'axis': self.AXIS, 'values': values or self.VALUES}
        return sa.metal_intensities(spectrum, config['metals'], sa._no_shift('t'))['X']

    def test_area_method(self):
        item = self.one()
        self.assertTrue(item['valid'])
        self.assertAlmostEqual(item['peak_area'], 24 + 25 + 26)
        self.assertAlmostEqual(item['baseline_level'], 20.0)  # the line between 14.5 @104.5 and 25.5 @115.5
        self.assertAlmostEqual(item['net_area'], 15.0)
        self.assertAlmostEqual(item['intensity'], 15.0)
        self.assertNotIn('norm_area', item)
        self.assertEqual(item['windows_used'], {'peak': [108.5, 111.5], 'bg_left': [103.5, 105.5],
                                                'bg_right': [114.5, 116.5]})
        normalized = self.one(normalize=[117.5, 118.5])
        self.assertAlmostEqual(normalized['norm_area'], 28.0)
        self.assertAlmostEqual(normalized['intensity'], 15.0 / 28.0)
        self.assertEqual(normalized['windows_used']['normalize'], [117.5, 118.5])
        partial = self.one(peak=[108.75, 111.25])  # three quarters of the two edge pixels
        self.assertAlmostEqual(partial['peak_area'], 0.75 * 24 + 25 + 0.75 * 26)
        self.assertAlmostEqual(partial['net_area'], 62.5 - 20.0 * 2.5)
        one_side = self.one(bg_right=None)
        self.assertAlmostEqual(one_side['baseline_level'], 14.5)
        self.assertAlmostEqual(one_side['net_area'], 75 - 14.5 * 3)
        no_baseline = self.one(bg_left=None, bg_right=None)
        self.assertAlmostEqual(no_baseline['baseline_level'], 0.0)
        self.assertAlmostEqual(no_baseline['intensity'], 75.0)

    def test_area_method_failures_stay_with_the_metal(self):
        outside = self.one(peak=[118.0, 120.5])
        self.assertEqual(outside, {'valid': False, 'error': 'window 118.000-120.500 nm is outside the spectrum'})
        dark = self.one(values=[0.0] * 20, normalize=[117.5, 118.5])
        self.assertEqual(dark, {'valid': False, 'error': 'normalization area is not positive'})

    def test_pixel_method_baselines_and_metrics(self):
        for baseline in ('lin2', 'min2', 'poly1', 'poly2', 'poly3'):
            for metric, expected in (('area', 10.0), ('peak', 5.0), ('mean', 5.0)):
                item = self.one(baseline=baseline, metric=metric)
                self.assertTrue(item['valid'], (baseline, metric))
                self.assertAlmostEqual(item['intensity'], expected, places=6, msg=(baseline, metric))
                self.assertAlmostEqual(item['baseline_level'], 20.0, places=6)
                self.assertEqual((item['baseline'], item['metric']), (baseline, metric))
                self.assertEqual(item['signal'], item['net_area'])
        for metric, expected in (('area', 10.0), ('peak', 6.0), ('mean', 5.0)):  # flat 20 under a ramp
            self.assertAlmostEqual(self.one(baseline='const', metric=metric)['intensity'], expected)
        # Given alone, each option selects the pixel method with the other one's default.
        self.assertEqual(self.one(metric='peak')['baseline'], 'lin2')
        self.assertEqual(self.one(baseline='const')['metric'], 'area')
        normalized = self.one(baseline='lin2', metric='mean', normalize=[117.5, 118.5])
        self.assertAlmostEqual(normalized['norm_area'], 28.0)
        self.assertAlmostEqual(normalized['intensity'], 5.0 / 28.0)

    def test_pixel_method_failures(self):
        cases = [(dict(baseline='lin2', bg_left=[103.1, 103.4]), 'background window holds no pixel'),
                 (dict(baseline='lin2', peak=[109.1, 109.4]), 'window holds no pixel'),
                 (dict(baseline='poly3', bg_left=[103.5, 104.5], bg_right=[114.5, 115.5]),
                  'poly3 background needs at least 4 pixels'),
                 (dict(baseline='const', metric='mean', normalize=[117.5, 118.5]), 'normalization value is not positive')]
        for metal, text in cases:
            values = [0.0] * 20 if 'normalize' in metal else None
            self.assertEqual(self.one(values=values, **metal), {'valid': False, 'error': text})
        with self.assertRaisesRegex(ValueError, 'background fit is singular'):
            sa._solve([[1.0, 1.0], [1.0, 1.0]], [1.0, 2.0])

    def test_one_bad_metal_does_not_spoil_the_others(self):
        config = sa.validate_config({'shift': {'enabled': False}, 'metals': {
            'Good': dict(self.WINDOWS), 'Bad': dict(self.WINDOWS, peak=[150.0, 151.0])}})
        result = sa.metal_intensities({'axis': self.AXIS, 'values': self.VALUES}, config['metals'], sa._no_shift('t'))
        self.assertTrue(result['Good']['valid'])
        self.assertFalse(result['Bad']['valid'])

    def test_windows_move_with_the_shift(self):
        config = sa.validate_config({'shift': {'enabled': False}, 'metals': {'X': dict(self.WINDOWS)}})
        shift = dict(sa._no_shift('t'), applied=True, p0=1.0, p1=0.0)
        moved_values = [10.0 + i + (5.0 if 10 <= i <= 12 else 0.0) for i in range(20)]  # the line one pixel later
        item = sa.metal_intensities({'axis': self.AXIS, 'values': moved_values}, config['metals'], shift)['X']
        self.assertEqual(item['windows_used']['peak'], [109.5, 112.5])
        self.assertAlmostEqual(item['net_area'], 15.0)
        sloped = dict(shift, p0=-10.4, p1=0.1)  # d = 0.1 * lambda - 10.4: a bound w moves to 1.1 w - 10.4
        item = sa.metal_intensities({'axis': self.AXIS, 'values': moved_values}, config['metals'], sloped)['X']
        for got, expected in zip(item['windows_used']['bg_left'] + item['windows_used']['peak'],
                                 [103.45, 105.65, 108.95, 112.25]):
            self.assertAlmostEqual(got, expected)

    def test_spline(self):
        xs = [0.0, 1.0, 2.5, 4.0, 7.0]
        line = sa.Spline(xs, [3 + 2 * x for x in xs])
        for x in (0.0, 0.3, 1.0, 2.0, 3.9, 6.5, 7.0):
            self.assertAlmostEqual(line(x), 3 + 2 * x)  # a natural spline reproduces a straight line
        self.assertEqual((line(-5.0), line(99.0)), (3.0, 17.0))  # clamped to the end values
        ys = [1.0, 4.0, 2.0, 8.0, 5.0]
        curve = sa.Spline(xs, ys)
        for x, y in zip(xs, ys):
            self.assertAlmostEqual(curve(x), y)
        self.assertEqual((curve.second[0], curve.second[-1]), (0.0, 0.0))

    def test_shift_fit_rules(self):
        settings = sa.validate_config(sa.DEFAULT_CONFIG)['shift']
        reference = {'axis': AXIS, 'values': synthetic({})}
        sample = {'axis': AXIS, 'values': synthetic({}, 0.02)}
        one = sa.fit_shift(reference, sample, dict(settings, windows_nm=[[260.8, 263.8]]))
        self.assertEqual((one['applied'], one['p1'], len(one['windows'])), (True, 0.0, 1))
        self.assertAlmostEqual(one['p0'], 0.02, delta=0.002)
        self.assertEqual(one['windows'][0]['center_nm'], (260.8 + 263.8) / 2)
        self.assertGreater(one['windows'][0]['correlation'], 0.99)
        two = sa.fit_shift(reference, sample, settings)
        self.assertAlmostEqual(two['p0'] + two['p1'] * 280.0, 0.02, delta=0.002)  # a line through both windows
        # The best grid point at the end of the search range is not trusted.
        edge = sa.fit_shift(reference, sample, dict(settings, search_nm=0.01, max_shift_nm=0.01))
        self.assertFalse(edge['valid'])
        self.assertTrue(all(window['at_search_edge'] for window in edge['windows']))
        self.assertIn('shift at search limit in [260.8, 263.8]', edge['problems'])
        # A spectrum without the matrix bands does not correlate.
        ramp = sa.fit_shift(reference, {'axis': AXIS, 'values': [1000.0 + x for x in AXIS]}, settings)
        self.assertFalse(ramp['valid'])
        self.assertTrue(any(problem.startswith('correlation ') and problem.endswith(' below 0.95')
                            for problem in ramp['problems']), ramp['problems'])
        self.assertEqual(sa._correlation([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]), 0.0)  # no variance: no correlation
        with self.assertRaisesRegex(ValueError, r'shift window \[200.0, 200.2\] has fewer than 5 pixels'):
            sa.fit_shift(reference, sample, dict(settings, windows_nm=[[200.0, 200.2]]))
        with self.assertRaisesRegex(ValueError, 'at least 2 points'):
            sa.linear_fit([1.0], [1.0])
        with self.assertRaisesRegex(ValueError, '2 different x values'):
            sa.linear_fit([1.0, 1.0], [1.0, 2.0])
        self.assertEqual(sa.linear_fit([0.0, 1.0, 2.0], [1.0, 3.0, 5.0]), (2.0, 1.0, 1.0))

    def test_reference_rules(self):
        config = sa.validate_config(sa.DEFAULT_CONFIG)
        with self.assertRaisesRegex(ValueError, 'no readable blank job for the reference spectrum'):
            sa.build_reference([], config)
        blank = {'axis': AXIS, 'values': synthetic({}), 'signal_frames': 2, 'background_frames': 1}
        far = dict(blank, values=synthetic({}, 0.4))
        reference = sa.build_reference([('a', blank), ('b', far)], config)
        self.assertEqual(reference['blank_count'], 1)
        self.assertEqual(reference['jobs'][1]['used'], False)
        self.assertTrue(reference['jobs'][1]['error'].startswith('blank not aligned: shift'))
        self.assertEqual(reference['values'], [float(v) for v in blank['values']])
        self.assertIs(reference['axis'], blank['axis'])
        off = dict(config, shift=dict(config['shift'], enabled=False))
        plain = sa.build_reference([('a', blank), ('b', far)], off)
        self.assertEqual(plain['blank_count'], 2)
        self.assertEqual([row['shift']['reason'] for row in plain['jobs']], ['first blank', 'shift disabled'])

    def test_rows(self):
        config = sa.validate_config(sa.DEFAULT_CONFIG)
        spectrum = {'axis': AXIS, 'values': synthetic({'Cu': 1}), 'signal_frames': 3, 'background_frames': 1}
        row = sa.analyze_one(spectrum, None, config)
        self.assertEqual((row['valid'], row['shift']['applied'], row['shift']['reason']),
                         (True, False, 'no reference spectrum'))
        self.assertEqual((row['signal_frames'], row['background_frames'], row['error']), (3, 1, ''))
        self.assertEqual(set(row['metals']), {'Cu', 'Ni', 'Zn'})
        off = dict(config, shift=dict(config['shift'], enabled=False))
        reference = {'axis': AXIS, 'values': synthetic({})}
        self.assertEqual(sa.analyze_one(spectrum, reference, off)['shift']['reason'], 'shift disabled')
        moved = dict(spectrum, values=synthetic({'Cu': 1}, 0.4))
        invalid = sa.analyze_one(moved, reference, config)
        self.assertFalse(invalid['valid'])
        self.assertIn('exceeds 0.3 nm', invalid['error'])
        self.assertIn('Cu', invalid['metals'])  # the intensities are still reported
        for malformed in ({}, [], {'axis': [1.0] * 8}, {'axis': [1.0] * 7, 'values': [1.0] * 7},
                          {'axis': [1.0] * 8, 'values': [1.0] * 9}, {'axis': 'x', 'values': 'y'}):
            with self.assertRaisesRegex(ValueError, 'reference spectrum is malformed'):
                sa.analyze_one(spectrum, malformed, config)
        # A spectrum the shift windows do not fit gives an invalid row, not an exception.
        short = {'axis': [200.0 + i for i in range(8)], 'values': [1.0] * 8, 'signal_frames': 1,
                 'background_frames': 1}
        row = sa.analyze_one(short, {'axis': short['axis'], 'values': short['values']}, config)
        self.assertEqual(set(row), {'valid', 'error'})
        self.assertIn('fewer than 5 pixels', row['error'])


class ConfigTests(unittest.TestCase):
    def test_every_rule_has_its_message(self):
        good = copy_of(sa.DEFAULT_CONFIG)
        line = {'peak': [231.45, 231.85], 'bg_left': [230.90, 231.25], 'bg_right': [232.25, 232.60]}

        def changed(path, value):
            raw = copy_of(good)
            target = raw
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            return raw
        cases = [
            (None, 'analysis config keys: subtract_dark, shift, metals'),
            (changed(['extra'], 1), 'analysis config keys: subtract_dark, shift, metals'),
            (changed(['shift'], []), 'shift keys: enabled, windows_nm, search_nm, step_nm, max_shift_nm, min_correlation'),
            (changed(['shift', 'speed'], 1), 'shift keys: '),
            (changed(['shift', 'enabled'], 1), 'shift.enabled must be true or false'),
            (changed(['shift', 'windows_nm'], []), 'shift.windows_nm needs 1-5 windows'),
            (changed(['shift', 'windows_nm'], [[260.8, 263.8]] * 6), 'shift.windows_nm needs 1-5 windows'),
            (changed(['shift', 'windows_nm'], [[263.8, 260.8]]), 'shift window must be .low_nm, high_nm. with low < high'),
            (changed(['shift', 'windows_nm'], [[99.0, 120.0]]), 'shift window must be'),
            (changed(['shift', 'windows_nm'], [[1100.0, 1201.0]]), 'shift window must be'),
            (changed(['shift', 'search_nm'], 0.001), 'shift.search_nm must be 0.01-3'),
            (changed(['shift', 'search_nm'], True), 'shift.search_nm must be 0.01-3'),
            (changed(['shift', 'step_nm'], 0.1), 'shift.step_nm must be 0.0005-0.05'),
            (changed(['shift', 'max_shift_nm'], 0), 'shift.max_shift_nm must be 0.001-3'),
            (changed(['shift', 'min_correlation'], 1.5), 'shift.min_correlation must be 0-1'),
            (changed(['shift', 'min_correlation'], '0.9'), 'shift.min_correlation must be 0-1'),
            (changed(['shift', 'max_shift_nm'], 0.6), 'shift.max_shift_nm cannot exceed shift.search_nm'),
            (changed(['metals'], {}), 'metals needs 1-10 entries'),
            (changed(['metals'], {f'M{i}': good['metals']['Cu'] for i in range(11)}), 'metals needs 1-10 entries'),
            (changed(['metals'], {'Cu-1': good['metals']['Cu']}), 'metal names must be short alphanumeric symbols'),
            (changed(['metals'], {'Copper123': good['metals']['Cu']}), 'metal names must be short alphanumeric symbols'),
            (changed(['metals', 'Cu'], [327.1, 327.5]), 'Cu keys: peak, bg_left, bg_right, normalize, baseline, metric'),
            (changed(['metals', 'Cu', 'weight'], 2), 'Cu keys: '),
            (changed(['metals', 'Cu', 'peak'], None), 'Cu.peak must be'),
            (changed(['metals', 'Cu', 'peak'], [327.1, '327.5']), 'Cu.peak must be'),
            (changed(['metals', 'Cu', 'peak'], [327.1, 327.5, 328.0]), 'Cu.peak must be'),
            (changed(['metals', 'Cu', 'bg_left'], [326.0, float('inf')]), 'Cu.bg_left must be'),
            (changed(['metals', 'Cu', 'baseline'], 'spline'),
             'Cu.baseline must be one of lin2, min2, const, poly1, poly2, poly3'),
            (changed(['metals', 'Cu', 'metric'], 'max'), 'Cu.metric must be one of area, peak, mean'),
            (changed(['subtract_dark'], 'yes'), 'subtract_dark must be true or false'),
            (changed(['metals', 'Ni'], {'lines': [line], 'baseline': 'lin2'}),
             'Ni: with "lines" only normalize: null may be given beside it'),
            (changed(['metals', 'Ni'], {'lines': [line] * 6}), 'Ni.lines needs 1-5 lines'),
            (changed(['metals', 'Ni'], {'lines': [dict(line, normalize=None)]}),
             r'Ni.lines\[0\] keys: baseline, bg_left, bg_right, metric, peak, weight'),
            (changed(['metals', 'Ni'], {'lines': [line, dict(line, weight=float('inf'))]}),
             r'Ni.lines\[1\].weight must be a finite non-zero number'),
            (changed(['metals', 'Ni'], {'lines': [dict(line, weight=2e6)]}), 'weight must be a finite non-zero number'),
            (changed(['metals', 'Ni'], {'lines': [dict(line, peak=None)]}), r'Ni.lines\[0\].peak must be'),
            (changed(['metals', 'Ni'], {'lines': [dict(line, baseline='spline')]}),
             r'Ni.lines\[0\]: baseline one of lin2, min2, const, poly1, poly2, poly3; metric one of area, peak, mean'),
        ]
        for raw, text in cases:
            with self.assertRaisesRegex(ValueError, text, msg=text):
                sa.validate_config(raw)

    def test_defaults_and_cleaning(self):
        clean = sa.validate_config({'comment': 'anything', 'metals': {'X': {'peak': [300, 301]}}})
        self.assertEqual(clean, {'subtract_dark': True,
                                 'shift': {'enabled': True, 'windows_nm': [[260.8, 263.8], [305.6, 307.0]],
                                           'search_nm': 0.5, 'step_nm': 0.005, 'max_shift_nm': 0.3,
                                           'min_correlation': 0.95},
                                 'metals': {'X': {'peak': [300.0, 301.0], 'bg_left': None, 'bg_right': None,
                                                  'normalize': None}}})
        self.assertIsInstance(clean['metals']['X']['peak'][0], float)
        partial = sa.validate_config({'shift': {'enabled': False, 'search_nm': 1}, 'metals': {'X': {'peak': [300, 301]}}})
        self.assertEqual((partial['shift']['enabled'], partial['shift']['search_nm'], partial['shift']['step_nm']),
                         (False, 1.0, 0.005))
        lines = sa.validate_config({'metals': {'Ni': {'lines': [{'peak': [231.4, 231.8]}]}}})['metals']['Ni']
        self.assertEqual(lines, {'lines': [{'peak': [231.4, 231.8], 'bg_left': None, 'bg_right': None,
                                            'baseline': 'lin2', 'metric': 'area', 'weight': 1.0}], 'normalize': None})
        self.assertEqual(sa.validate_config(sa.validate_config(sa.DEFAULT_CONFIG)), sa.validate_config(sa.DEFAULT_CONFIG))

    def test_method_id_is_computed_as_in_the_original(self):
        config = sa.validate_config({'metals': {'X': {'peak': [300, 301]}}})
        canonical = ('{"config":{"metals":{"X":{"bg_left":null,"bg_right":null,"normalize":null,"peak":[300.0,301.0]}},'
                     '"shift":{"enabled":true,"max_shift_nm":0.3,"min_correlation":0.95,"search_nm":0.5,'
                     '"step_nm":0.005,"windows_nm":[[260.8,263.8],[305.6,307.0]]},"subtract_dark":true},'
                     '"version":"area-v1"}')
        self.assertEqual(sa.method_id(config), 'area-v1-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12])
        self.assertEqual(sa.ANALYSIS_VERSION, 'area-v1')
        self.assertEqual(sa.method_id(sa.validate_config(sa.DEFAULT_CONFIG)), 'area-v1-ec0e59e287f2')
        # The comment is not part of the method; any window is.
        with_comment = dict(copy_of(sa.DEFAULT_CONFIG), comment='x')
        self.assertEqual(sa.method_id(sa.validate_config(with_comment)), 'area-v1-ec0e59e287f2')
        dark = dict(copy_of(sa.DEFAULT_CONFIG), subtract_dark=False)
        self.assertNotEqual(sa.method_id(sa.validate_config(dark)), 'area-v1-ec0e59e287f2')


class DefaultFileTests(unittest.TestCase):
    """pc/analysis_default.json: what a new data folder starts with."""

    def setUp(self):
        self.text = DEFAULT_FILE.read_text(encoding='utf-8')
        self.raw = json.loads(self.text)

    def test_holds_the_windows_of_the_original_settings(self):
        config = sa.validate_config(self.raw)
        self.assertEqual(config['subtract_dark'], True)
        self.assertEqual(config['shift'], {'enabled': True, 'windows_nm': [[277.5, 280.0], [330.5, 333.0]],
                                           'search_nm': 0.5, 'step_nm': 0.005, 'max_shift_nm': 0.1,
                                           'min_correlation': 0.95})
        self.assertEqual(config['metals']['Cu'], {'peak': [324.5484, 325.1032], 'bg_left': [323.0181, 323.7146],
                                                  'bg_right': [325.1032, 325.7262], 'normalize': None,
                                                  'baseline': 'poly3', 'metric': 'peak'})
        self.assertEqual(config['metals']['Zn'], {'peak': [213.1156, 213.7405], 'bg_left': [212.2215, 212.5793],
                                                  'bg_right': [213.8298, 214.2757], 'normalize': None,
                                                  'baseline': 'poly1', 'metric': 'mean'})
        self.assertEqual(config['metals']['Ni'], {'normalize': None, 'lines': [
            {'peak': [231.3899, 232.1667], 'bg_left': [228.8788, 229.746], 'bg_right': [234.0608, 234.4045],
             'baseline': 'const', 'metric': 'area', 'weight': 1.0},
            {'peak': [230.5253, 231.3035], 'bg_left': [228.0971, 228.4447], 'bg_right': [233.8029, 234.0608],
             'baseline': 'const', 'metric': 'area', 'weight': 0.4359},
            {'peak': [341.126, 341.5213], 'bg_left': [340.5322, 340.7302], 'bg_right': [341.9161, 342.7041],
             'baseline': 'lin2', 'metric': 'area', 'weight': 1.0887}]})
        self.assertEqual(list(config['metals']), ['Cu', 'Ni', 'Zn'])
        self.assertEqual(sa.method_id(config), 'area-v1-07c44d563a4d')

    def test_comment_says_what_is_known(self):
        comment = self.raw['comment']
        for part in ('original HMI', '2026-10-02', 'tuned for the original spectrometer (200-350 nm, 1933 pixels)',
                     'NOT yet checked for the spectrometer on this rig (180-500 nm, 1862 pixels)',
                     'data\\analysis_config.json', 'edit that copy in the data folder'):
            self.assertIn(part, comment)
        self.assertNotIn('CAL-', comment)  # the original's note about its own calibration is not this rig's
        self.assertEqual(list(self.raw), ['comment', 'subtract_dark', 'shift', 'metals'])
        self.assertNotIn('\r', self.text)
        self.assertTrue(self.text.endswith('}\n'))

    def test_windows_on_this_rigs_pixel_grid(self):
        """Not a check of the instrument: only that, on an evenly spaced 1862-pixel axis over
        180-500 nm, no window of the default settings is empty and every metal gives a number."""
        config = sa.validate_config(self.raw)
        self.assertEqual((len(RIG_AXIS), RIG_AXIS[0], RIG_AXIS[-1]), (1862, 179.991, 500.126))
        for window in config['shift']['windows_nm']:
            self.assertGreaterEqual(len(sa._pixels(RIG_AXIS, window)), 5)
        windows = [config['metals'][m][key] for m in ('Cu', 'Zn') for key in ('peak', 'bg_left', 'bg_right')]
        windows += [line[key] for line in config['metals']['Ni']['lines'] for key in ('peak', 'bg_left', 'bg_right')]
        for window in windows:
            self.assertGreaterEqual(len(sa._pixels(RIG_AXIS, window)), 1, window)
        values = [1000.0 + 300.0 * math.exp(-((x - 324.8) / 0.2) ** 2) + 5.0 * math.sin(x) for x in RIG_AXIS]
        run = stored_run(job_id(3), values, axis=RIG_AXIS)
        row = sa.analyze_one(sa.spectrum_of(run, config), None, config)
        self.assertTrue(row['valid'])
        for metal in ('Cu', 'Ni', 'Zn'):
            self.assertTrue(row['metals'][metal]['valid'], row['metals'][metal])
            self.assertTrue(math.isfinite(row['metals'][metal]['intensity']))
        self.assertGreater(row['metals']['Cu']['intensity'], 100.0)
        reference = {'axis': RIG_AXIS, 'values': [v + 3.0 for v in values]}
        self.assertTrue(sa.analyze_one(sa.spectrum_of(run, config), reference, config)['shift']['applied'])


if __name__ == '__main__':
    unittest.main()
