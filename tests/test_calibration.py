"""pc/calibration.py: calibration plan, run sequence, regression, coefficients.

The cases of the original HMI's tests/test_calibration.py (PlanTests) on the 2.0.0 step dicts,
plus every rule and message of the ported functions. No equipment, no network, no files.

    python -B tests/test_calibration.py
"""
from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pc'))

import calibration as cal  # noqa: E402

STEP_KEYS = {'n', 'line', 'action', 'name', 'target', 'target_label', 'motor', 'motors', 'amounts', 'value', 'unit', 'note',
             'round', 'rounds', 'phase', 'text', 'planned_s'}
NAMES = {'delay': 'Waiting', 'pump_on': 'Start pump (forward rotation)', 'pump_off': 'stop pump', 'syringe_init': 'syringe pump initialization',
         'syringe_aspirate': 'syringe pump for pumping liquid', 'syringe_dispense': 'syringe pump injection', 'plasma_run': 'discharge＋capture spectrum',
         'rpi_status': 'Check that the high voltage is off', 'nano_prepare': 'Synchronization parameters arrive Nano', 'spectrum_capture': 'Spectrometer inspection (no discharge)',
         'nano_stop': 'Turn off high voltage', 'power_off': 'Turn off high voltage', 'clean': 'automatic cleaning', 'scale_read': 'Record weight',
         'dose_volume': 'Quantitative (volume)'}
MEASURE = ('rpi_status', 'nano_prepare', 'plasma_run', 'spectrum_capture')


def recipe(rows, names=False):
    """The 'steps' and 'phases' recipe.validate() gives for one round (SPEC A.3): ``phase`` is an
    index into the phase list, or the name itself with names=True."""
    steps, phases = [], []
    for number, row in enumerate(rows, 1):
        action, target, value, unit = (list(row) + ['', '', ''])[:4]
        kind = 'Measure' if action in MEASURE else 'Turn off high voltage' if action in ('nano_stop', 'power_off') else 'Add liquid'
        if phases and phases[-1]['name'] == kind:
            phases[-1]['to'] = number
        else:
            phases.append({'name': kind, 'from': number, 'to': number})
        steps.append({'n': number, 'line': number + 1, 'action': action, 'name': NAMES[action], 'target': target,
                      'target_label': f'Pump {int(target[1:]) - 1}' if target.startswith('M') else '',
                      'motor': int(target[1:]) if target.startswith('M') else None, 'motors': None, 'amounts': None,
                      'value': value,
                      'unit': unit, 'note': f'note {number}', 'round': 1, 'rounds': 1,
                      'phase': kind if names else len(phases) - 1, 'text': f'{NAMES[action]} {value}'.strip(),
                      'planned_s': 0.1 if action == 'pump_on' else None})
    return steps, phases


ROUND = [('pump_on', 'M8', '40', 'mL/min'), ('delay', '', '0.1', 's'), ('pump_off', 'M8'), ('syringe_init',),
         ('syringe_aspirate', 'CH2', '1', 'mL'), ('syringe_dispense', 'CH1', '1', 'mL'), ('plasma_run',),
         ('pump_on', 'M8', '40', 'mL/min'), ('delay', '', '0.1', 's'), ('pump_off', 'M8')]
PRIME = [('pump_on', 'M9', '20', 'mL/min'), ('delay', '', '0.1', 's'), ('pump_off', 'M9'),
         ('pump_on', 'M8', '40', 'mL/min'), ('delay', '', '0.1', 's'), ('pump_off', 'M8')]


def plan(**overrides):
    raw = {'recipe_id': 'a' * 32, 'discard_rounds': 1, 'levels': [
        {'name': 'STD-2', 'conc': {'Cu': 2, 'Zn': 1, 'Ni': 4}, 'rounds': 2},
        {'name': 'Blank', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 2},
        {'name': 'STD-1', 'conc': {'Cu': '1', 'Zn': 0.5, 'Ni': 2}, 'rounds': 2},
    ]}
    raw.update(overrides)
    return raw


class PlanTests(unittest.TestCase):
    """Ported from the original."""

    def test_sorted_low_to_high_with_blank_first(self):
        clean = cal.validate_plan(plan())
        self.assertEqual([level['name'] for level in clean['levels']], ['Blank', 'STD-1', 'STD-2'])
        self.assertEqual(clean['levels'][1]['conc']['Cu'], 1.0)
        self.assertEqual(cal.plan_warnings(clean), [])
        self.assertEqual(clean, {'recipe_id': 'a' * 32, 'prime_recipe_id': '', 'discard_rounds': 1, 'levels': [
            {'name': 'Blank', 'conc': {'Cu': 0.0, 'Zn': 0.0, 'Ni': 0.0}, 'rounds': 2},
            {'name': 'STD-1', 'conc': {'Cu': 1.0, 'Zn': 0.5, 'Ni': 2.0}, 'rounds': 2},
            {'name': 'STD-2', 'conc': {'Cu': 2.0, 'Zn': 1.0, 'Ni': 4.0}, 'rounds': 2}]})
        self.assertEqual(cal.validate_plan(clean), clean)  # a clean plan stays as it is

    def test_blank_is_required(self):
        raw = plan()
        raw['levels'][1]['conc']['Zn'] = None
        with self.assertRaisesRegex(ValueError, 'Blank'):
            cal.validate_plan(raw)

    def test_metal_needs_two_concentrations_and_names_unique(self):
        raw = plan()
        raw['levels'][2]['name'] = 'STD-2'
        with self.assertRaisesRegex(ValueError, 'repeat'):
            cal.validate_plan(raw)
        raw = {'recipe_id': '', 'discard_rounds': 0, 'levels': [
            {'name': 'B', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 1},
            {'name': 'S', 'conc': {'Cu': 1, 'Zn': None, 'Ni': None}, 'rounds': 1}]}
        self.assertEqual(len(cal.validate_plan(raw)['levels']), 2)  # Cu: 0 and 1 -> two values

    def test_decreasing_metal_is_a_warning(self):
        raw = plan()
        raw['levels'][0]['conc']['Zn'] = 0.2  # STD-2 sorts last but its Zn is below STD-1
        warnings = cal.plan_warnings(cal.validate_plan(raw))
        self.assertTrue(any(w.startswith('Zn') for w in warnings))
        self.assertEqual(warnings, ['Zn: STD-2 (0.2) lower than the previous one STD-1 (0.5) ,The order from low to high will be disrupted, and the residue may be high.'])

    def test_steps_prompt_each_standard_and_tag_discard_rounds(self):
        clean = cal.validate_plan(plan())
        steps, _phases = recipe(ROUND)
        built = cal.build_steps(clean, steps)
        prompts = [s for s in built if s['action'] == 'operator_prompt']
        self.assertEqual(len(prompts), 3)
        runs = [s['calibration'] for s in built if s['action'] == 'plasma_run']
        self.assertEqual(len(runs), 9)
        self.assertEqual([r['discard'] for r in runs[:3]], [True, False, False])
        self.assertEqual([r['round'] for r in runs[:3]], [None, 1, 2])
        prime, _prime_phases = recipe(PRIME)
        primed = cal.build_steps(clean, steps, prime)
        after_prompt = [i for i, step in enumerate(primed) if step['action'] == 'operator_prompt']
        for index in after_prompt:  # each bottle swap is followed by the whole prime Recipe
            self.assertEqual([step['action'] for step in primed[index + 1:index + 1 + len(prime)]],
                             [step['action'] for step in prime])
            self.assertTrue(all(step['calibration'].get('prime') is True
                                for step in primed[index + 1:index + 1 + len(prime)]))
        self.assertEqual(len(primed), len(built) + 3 * len(prime))
        self.assertEqual(sum(1 for step in primed if step['action'] == 'plasma_run'), 9)
        with self.assertRaisesRegex(ValueError, 'Fluid replacement and draining'):
            cal.build_steps(clean, steps, steps)
        with self.assertRaisesRegex(ValueError, 'cannot be used with standard products Recipe same'):
            cal.validate_plan(plan(prime_recipe_id='a' * 32))
        double, _ = recipe(ROUND + [('plasma_run',)])
        with self.assertRaisesRegex(ValueError, 'just contains 1 indivual plasma_run'):
            cal.build_steps(clean, double)

    def test_regression_uses_every_kept_round(self):
        clean = cal.validate_plan(plan())
        runs, rows = [], []
        for index, level in enumerate(clean['levels']):
            for attempt in range(3):
                job = f'/j/{index}-{attempt}'
                runs.append({'level': index, 'discard': attempt == 0, 'round': attempt or None, 'job': job})
                rows.append({'job': job, 'valid': True, 'metals': {
                    m: {'valid': True, 'intensity': 0.01 + 0.2 * (level['conc'][m] or 0) + (attempt - 1.5) * 1e-4}
                    for m in ('Cu', 'Zn', 'Ni')}})
        rows[4]['valid'], rows[4]['error'] = False, 'shift too large'
        fits = cal.regress(clean, runs, rows)
        cu = fits['Cu']
        self.assertTrue(cu['valid'])
        self.assertEqual(cu['n'], 5)  # 6 kept rounds, one excluded
        self.assertEqual(len(cu['excluded']), 1)
        self.assertAlmostEqual(cu['a'], 5.0, delta=0.01)
        self.assertAlmostEqual(cu['b'], -0.05, delta=0.002)
        self.assertGreater(cu['r2'], 0.9999)
        self.assertEqual(cu['excluded'], [{'level': 'STD-1', 'round': 1, 'reason': 'shift too large'}])

    def test_level_excluded_for_one_metal_only(self):
        clean = cal.validate_plan(plan())
        runs, rows = [], []
        for index, level in enumerate(clean['levels']):
            for attempt in range(2):
                job = f'/j/{index}-{attempt}'
                saturated = index == len(clean['levels']) - 1
                runs.append({'level': index, 'discard': False, 'round': attempt + 1, 'job': job})
                rows.append({'job': job, 'valid': True, 'metals': {
                    m: {'valid': True, 'intensity': 0.2 * (level['conc'][m] or 0) * (0.5 if saturated and m == 'Cu' else 1)
                        + attempt * 1e-4} for m in ('Cu', 'Zn', 'Ni')}})
        last = len(clean['levels']) - 1
        exclude = cal.validate_exclusions(clean, {'Cu': [last]})
        fits = cal.regress(clean, runs, rows, exclude)
        self.assertGreater(fits['Cu']['r2'], 0.9999)
        self.assertEqual([p['reason'] for p in fits['Cu']['excluded']], ['Manually exclude this concentration'] * 2)
        self.assertEqual(fits['Zn']['n'], len(runs))
        self.assertLess(cal.regress(clean, runs, rows)['Cu']['r2'], 0.999)
        for bad in ({'Fe': [0]}, {'Cu': [99]}, {'Cu': 'x'}):
            with self.assertRaises(ValueError):
                cal.validate_exclusions(clean, bad)


class PlanRuleTests(unittest.TestCase):
    def test_every_rule_has_its_message(self):
        def levels(*changes):
            rows = copy.deepcopy(plan()['levels'])
            for index, change in changes:
                rows[index].update(change)
            return rows
        twelve = [{'name': 'B', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 10}] + [
            {'name': f'S{i}', 'conc': {'Cu': i}, 'rounds': 10} for i in range(1, 9)]
        cases = [
            ([], 'Calibration plan fields are incorrect'),
            (plan(extra=1), 'Calibration plan fields are incorrect'),
            (plan(recipe_id='xyz'), 'Please select the standard product Recipe'),
            (plan(recipe_id=None), 'Please select the standard product Recipe'),
            (plan(recipe_id='A' * 32), 'Please select the standard product Recipe'),
            (plan(prime_recipe_id='short'), 'Fluid replacement and draining Recipe Incorrect'),
            (plan(prime_recipe_id=5), 'Fluid replacement and draining Recipe Incorrect'),
            (plan(prime_recipe_id='a' * 32), 'Fluid replacement and draining Recipe cannot be used with standard products Recipe same'),
            (plan(discard_rounds=4), 'The number of pre-running rounds for each concentration must be 0–3 integer'),
            (plan(discard_rounds=True), 'The number of pre-running rounds for each concentration must be 0–3 integer'),
            (plan(discard_rounds=1.0), 'The number of pre-running rounds for each concentration must be 0–3 integer'),
            (plan(levels=None), 'standard product must have 2–12 concentrations (including blank)'),
            (plan(levels=levels()[:1]), 'standard product must have 2–12 concentrations (including blank)'),
            (plan(levels=levels() * 5), 'standard product must have 2–12 concentrations (including blank)'),
            (plan(levels=levels((2, {'extra': 1}))), 'No. 3 standard product fields are incorrect'),
            (plan(levels=['x', 'y']), 'No. 1 standard product fields are incorrect'),
            (plan(levels=levels((0, {'name': '   '}))), 'No. 1 standard product names must be 1–20 characters'),
            (plan(levels=levels((0, {'name': 'x' * 21}))), 'No. 1 standard product names must be 1–20 characters'),
            (plan(levels=levels((1, {'name': 'a<b'}))), 'No. 2 standard product names must be 1–20 characters'),
            (plan(levels=levels((1, {'name': 7}))), 'No. 2 standard product names must be 1–20 characters'),
            (plan(levels=levels((2, {'name': ' Blank '}))), 'The standard product name is repeated:Blank'),
            (plan(levels=levels((0, {'conc': {'Fe': 1}}))), 'STD-2 can only be Cu, Zn, Ni'),
            (plan(levels=levels((0, {'conc': [1, 2, 3]}))), 'STD-2 can only be Cu, Zn, Ni'),
            (plan(levels=levels((0, {'conc': {'Cu': 'abc'}}))), 'STD-2 of Cu must be a number or left blank'),
            (plan(levels=levels((0, {'conc': {'Cu': True}}))), 'STD-2 of Cu must be a number or left blank'),
            (plan(levels=levels((0, {'conc': {'Zn': [1]}}))), 'STD-2 of Zn must be a number or left blank'),
            (plan(levels=levels((0, {'conc': {'Ni': -1}}))), 'STD-2 of Ni Must be between 0–100000 mg/L'),
            (plan(levels=levels((0, {'conc': {'Ni': 100000.5}}))), 'STD-2 of Ni Must be between 0–100000 mg/L'),
            (plan(levels=levels((0, {'conc': {'Ni': 'nan'}}))), 'STD-2 of Ni Must be between 0–100000 mg/L'),
            (plan(levels=levels((0, {'rounds': 0}))), 'STD-2 must be 1–10 integer'),
            (plan(levels=levels((0, {'rounds': 11}))), 'STD-2 must be 1–10 integer'),
            (plan(levels=levels((0, {'rounds': '2'}))), 'STD-2 must be 1–10 integer'),
            (plan(levels=levels((1, {'conc': {'Cu': 0, 'Zn': 0.1, 'Ni': 0}}))),
             'The lowest concentration standard must be blank:Cu, Zn, Ni Fill in all 0 (blank spectrum is used as a reference spectrum for displacement correction)'),
            (plan(levels=levels((0, {'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}}))), 'can only have one blank standard'),
            (plan(levels=levels((0, {'conc': {}}))), 'The lowest concentration standard must be blank'),  # an empty standard sorts first
            (plan(levels=twelve, discard_rounds=0), 'Total number of measurements 90 exceeds the upper limit 80'),
        ]
        for raw, text in cases:
            with self.assertRaises(ValueError, msg=text) as caught:
                cal.validate_plan(raw)
            self.assertIn(text, str(caught.exception))

    def test_what_is_allowed(self):
        raw = plan(prime_recipe_id=None, levels=[
            {'name': ' Blank ', 'conc': {'Cu': 0, 'Zn': '0', 'Ni': 0.0}, 'rounds': 1},
            {'name': 'Cu only', 'conc': {'Cu': ' 0.5 ', 'Zn': '', 'Ni': None}, 'rounds': 10}])
        clean = cal.validate_plan(raw)
        self.assertEqual(clean['prime_recipe_id'], '')
        self.assertEqual(clean['levels'][0], {'name': 'Blank', 'conc': {'Cu': 0.0, 'Zn': 0.0, 'Ni': 0.0}, 'rounds': 1})
        self.assertEqual(clean['levels'][1]['conc'], {'Cu': 0.5, 'Zn': None, 'Ni': None})
        self.assertEqual(cal.validate_plan({'levels': clean['levels']})['discard_rounds'], 1)  # the default
        self.assertEqual(cal.validate_plan({'levels': clean['levels']})['recipe_id'], '')
        eighty = [{'name': 'B', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 10}] + [
            {'name': f'S{i}', 'conc': {'Cu': i}, 'rounds': 10} for i in range(1, 8)]
        self.assertEqual(len(cal.validate_plan(plan(levels=eighty, discard_rounds=0))['levels']), 8)
        equal = plan(levels=[{'name': 'B', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 1},
                             {'name': 'second', 'conc': {'Cu': 1}, 'rounds': 1},
                             {'name': 'first', 'conc': {'Zn': 1}, 'rounds': 1}])
        self.assertEqual([level['name'] for level in cal.validate_plan(equal)['levels']], ['B', 'second', 'first'])
        self.assertEqual(cal.level_total({'conc': {'Cu': 1.5, 'Zn': None, 'Ni': 2}}), 3.5)

    def test_default_plan_and_describe(self):
        self.assertEqual(cal.DEFAULT_PLAN['discard_rounds'], 1)
        self.assertEqual([level['name'] for level in cal.DEFAULT_PLAN['levels']], ['Blank', 'STD-1', 'STD-2'])
        self.assertEqual(cal.DEFAULT_PLAN['levels'][1]['conc'], {'Cu': None, 'Zn': None, 'Ni': None})
        with self.assertRaisesRegex(ValueError, 'can only have one blank standard'):  # the draft has to be filled in first
            cal.validate_plan(copy.deepcopy(cal.DEFAULT_PLAN))
        clean = cal.validate_plan(plan())
        self.assertEqual(cal.describe(clean['levels'][1]), 'STD-1 (Cu 1 / Zn 0.5 / Ni 2 mg/L) ')
        self.assertEqual(cal.describe({'name': 'X', 'conc': {'Cu': None, 'Zn': 0.25, 'Ni': None}}), 'X (Zn 0.25 mg/L) ')
        self.assertEqual((cal.UNIT, cal.METALS), ('mg/L', ('Cu', 'Zn', 'Ni')))
        self.assertEqual((cal.MAX_LEVELS, cal.MAX_ROUNDS, cal.MAX_DISCARD, cal.MAX_RUNS), (12, 10, 3, 80))


class SequenceTests(unittest.TestCase):
    """build_steps on the step dicts of 2.0.0 (AMENDMENTS M5.1)."""

    def setUp(self):
        self.plan = cal.validate_plan(plan())
        self.steps, self.phases = recipe(ROUND)
        self.prime, _prime_phases = recipe(PRIME)

    def test_same_shape_renumbered(self):
        source = copy.deepcopy(self.steps)
        built = cal.build_steps(self.plan, self.steps, self.prime)
        self.assertEqual(self.steps, source)  # the Recipe's own steps are not touched
        self.assertEqual([step['n'] for step in built], list(range(1, len(built) + 1)))
        self.assertEqual(len(built), 3 * (1 + len(PRIME)) + 9 * len(ROUND))
        for step in built:
            self.assertEqual(set(step), STEP_KEYS | {'calibration'}, step)
            self.assertEqual(step['rounds'], 9)
            self.assertEqual(set(step['calibration']) - {'prime'}, {'level', 'level_name', 'run', 'discard', 'round'})
        json.dumps(built, allow_nan=False)
        # A Recipe step keeps everything but n, round, rounds, phase.
        first = next(step for step in built if step['action'] == 'pump_on' and not step['calibration'].get('prime'))
        for key in STEP_KEYS - {'n', 'round', 'rounds', 'phase'}:
            self.assertEqual(first[key], self.steps[0][key], key)
        # Tags are not shared between steps.
        runs = [step for step in built if step['calibration']['run'] == 1 and step['calibration']['discard'] is True]
        runs[0]['calibration']['run'] = 99
        self.assertEqual(runs[1]['calibration']['run'], 1)

    def test_prompt_step(self):
        built = cal.build_steps(self.plan, self.steps)
        text = 'Please replace with standard product Blank (Cu 0 / Zn 0 / Ni 0 mg/L) ,Put the pipette into the new bottle and click ”Confirm Liquid Change”'
        # the keys of a validated step (recipe.validate), the pump ones empty: it names no pump
        self.assertEqual(built[0], {'n': 1, 'line': 0, 'action': 'operator_prompt', 'name': 'Fluid change', 'target': '',
                                    'target_label': '', 'motor': None, 'motors': None, 'amounts': None,
                                    'value': '', 'unit': '', 'note': text, 'round': 1, 'rounds': 9,
                                    'phase': 0, 'text': text, 'planned_s': None,
                                    'calibration': {'level': 0, 'level_name': 'Blank', 'run': 1, 'discard': None,
                                                    'round': None}})
        prompts = [step for step in built if step['action'] == 'operator_prompt']
        self.assertEqual([step['text'] for step in prompts[1:]], [
            'Please replace with standard product STD-1 (Cu 1 / Zn 0.5 / Ni 2 mg/L) ,Put the pipette into the new bottle and click ”Confirm Liquid Change”',
            'Please replace with standard product STD-2 (Cu 2 / Zn 1 / Ni 4 mg/L) ,Put the pipette into the new bottle and click ”Confirm Liquid Change”'])
        self.assertEqual([step['round'] for step in prompts], [1, 4, 7])  # the run that follows the swap
        self.assertEqual([step['calibration']['level'] for step in prompts], [0, 1, 2])
        self.assertFalse(any('P6' in step['text'] for step in prompts))  # the original named a PLC pump
        self.assertEqual(cal.prompt_text(self.plan['levels'][0]), text)

    def test_runs_and_tags(self):
        built = cal.build_steps(self.plan, self.steps, self.prime)
        plasma = [step for step in built if step['action'] == 'plasma_run']
        self.assertEqual([step['calibration'] for step in plasma], [
            {'level': 0, 'level_name': 'Blank', 'run': 1, 'discard': True, 'round': None},
            {'level': 0, 'level_name': 'Blank', 'run': 2, 'discard': False, 'round': 1},
            {'level': 0, 'level_name': 'Blank', 'run': 3, 'discard': False, 'round': 2},
            {'level': 1, 'level_name': 'STD-1', 'run': 4, 'discard': True, 'round': None},
            {'level': 1, 'level_name': 'STD-1', 'run': 5, 'discard': False, 'round': 1},
            {'level': 1, 'level_name': 'STD-1', 'run': 6, 'discard': False, 'round': 2},
            {'level': 2, 'level_name': 'STD-2', 'run': 7, 'discard': True, 'round': None},
            {'level': 2, 'level_name': 'STD-2', 'run': 8, 'discard': False, 'round': 1},
            {'level': 2, 'level_name': 'STD-2', 'run': 9, 'discard': False, 'round': 2}])
        self.assertEqual([step['round'] for step in plasma], list(range(1, 10)))  # round = run number
        primes = [step for step in built if step['calibration'].get('prime')]
        self.assertEqual(len(primes), 3 * len(PRIME))
        self.assertEqual({step['round'] for step in primes}, {1, 4, 7})
        self.assertEqual(primes[0]['calibration'], {'level': 0, 'level_name': 'Blank', 'run': 1, 'discard': None,
                                                    'round': None, 'prime': True})
        no_discard = cal.build_steps(cal.validate_plan(plan(discard_rounds=0)), self.steps)
        tags = [step['calibration'] for step in no_discard if step['action'] == 'plasma_run']
        self.assertEqual(len(tags), 6)
        self.assertFalse(any(tag['discard'] for tag in tags))
        self.assertEqual([tag['round'] for tag in tags], [1, 2] * 3)

    def test_phases_follow_the_steps(self):
        sequence = cal.build_sequence(self.plan, self.steps, self.prime, self.phases)
        steps, phases = sequence['steps'], sequence['phases']
        unnamed = cal.build_sequence(self.plan, self.steps, self.prime)['steps']  # the phase list only gives names
        self.assertEqual([dict(step, phase=None) for step in steps], [dict(step, phase=None) for step in unnamed])
        self.assertEqual(phases[:5], [{'name': 'Fluid change', 'from': 1, 'to': 1}, {'name': 'Fluid replacement and draining', 'from': 2, 'to': 7},
                                      {'name': 'Add liquid', 'from': 8, 'to': 13}, {'name': 'Measure', 'from': 14, 'to': 14},
                                      {'name': 'Add liquid', 'from': 15, 'to': 23}])  # drain of run 1 + feed of run 2
        self.assertEqual(phases[-1]['to'], len(steps))
        self.assertEqual([phase['name'] for phase in phases].count('Fluid change'), 3)
        covered = []
        for index, phase in enumerate(phases):
            covered += list(range(phase['from'], phase['to'] + 1))
            for step in steps[phase['from'] - 1:phase['to']]:
                self.assertEqual(step['phase'], index)
        self.assertEqual(covered, list(range(1, len(steps) + 1)))
        self.assertEqual(steps[13]['action'], 'plasma_run')
        # Without the Recipe's phase list the names are unknown; the steps are the same.
        self.assertEqual({phase['name'] for phase in cal.build_sequence(self.plan, self.steps)['phases']}, {'Fluid change', ''})
        # One Recipe step per run ("Only discharge measurement"): one prompt, then one measuring phase per standard.
        single, single_phases = recipe([('plasma_run',)])
        short = cal.build_sequence(self.plan, single, None, single_phases)
        self.assertEqual(short['phases'], [{'name': 'Fluid change', 'from': 1, 'to': 1}, {'name': 'Measure', 'from': 2, 'to': 4},
                                           {'name': 'Fluid change', 'from': 5, 'to': 5}, {'name': 'Measure', 'from': 6, 'to': 8},
                                           {'name': 'Fluid change', 'from': 9, 'to': 9}, {'name': 'Measure', 'from': 10, 'to': 12}])
        self.assertEqual([step['phase'] for step in short['steps']], [0, 1, 1, 1, 2, 3, 3, 3, 4, 5, 5, 5])

    def test_phase_names_stay_names(self):
        steps, phases = recipe(ROUND, names=True)
        prime, _prime_phases = recipe(PRIME, names=True)
        sequence = cal.build_sequence(self.plan, steps, prime, phases)
        self.assertEqual([step['phase'] for step in sequence['steps'][:9]],
                         ['Fluid change'] + ['Fluid replacement and draining'] * 6 + ['Add liquid'] * 2)
        self.assertEqual(sequence['phases'][:3], [{'name': 'Fluid change', 'from': 1, 'to': 1},
                                                  {'name': 'Fluid replacement and draining', 'from': 2, 'to': 7},
                                                  {'name': 'Add liquid', 'from': 8, 'to': 13}])
        self.assertEqual(sequence['steps'][13]['phase'], 'Measure')

    def test_which_recipes_fit(self):
        none, _ = recipe([('pump_on', 'M8', '40', 'mL/min'), ('delay', '', '1', 's'), ('pump_off', 'M8')])
        with self.assertRaises(ValueError) as caught:
            cal.build_steps(self.plan, none)
        self.assertEqual(str(caught.exception), 'standard product Recipe must contain exactly 1 indivual plasma_run (at present 0 ); the number of rounds can be changed on the calibration page.')
        two, _ = recipe([('plasma_run',), ('plasma_run',)])
        with self.assertRaisesRegex(ValueError, 'at present 2 indivual'):
            cal.build_steps(self.plan, two)
        self.assertEqual(cal.REMOTE_ACTIONS, ('rpi_status', 'nano_prepare', 'plasma_run', 'spectrum_capture',
                                              'nano_stop', 'power_off'))
        for action in cal.REMOTE_ACTIONS:
            bad, _ = recipe(PRIME + [(action,)])
            with self.assertRaises(ValueError, msg=action) as caught:
                cal.build_steps(self.plan, self.steps, bad)
            self.assertEqual(str(caught.exception), 'Fluid replacement and draining Recipe can only have pump, syringe pump and waiting steps (cannot measure content or RPi steps)')
        fine, _ = recipe([('clean',), ('scale_read',), ('dose_volume', 'M2', '5', 'mL')])
        self.assertEqual(len(cal.build_steps(self.plan, self.steps, fine)), 3 * 4 + 9 * len(ROUND))
        self.assertEqual(cal.build_steps(self.plan, self.steps, []), cal.build_steps(self.plan, self.steps))
        only, _ = recipe([('plasma_run',)])  # "Only discharge measurement" qualifies as a standard Recipe
        self.assertEqual([step['action'] for step in cal.build_steps(self.plan, only)],
                         (['operator_prompt'] + ['plasma_run'] * 3) * 3)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.plan = cal.validate_plan(plan())

    def data(self, intensity):
        runs, rows = [], []
        for index, level in enumerate(self.plan['levels']):
            for attempt in range(2):
                job = f'HMI-{index}-{attempt}'
                runs.append({'level': index, 'level_name': level['name'], 'run': len(runs) + 1, 'discard': False,
                             'round': attempt + 1, 'job': job})
                rows.append({'job': job, 'valid': True, 'error': '', 'metals': {
                    m: {'valid': True, 'intensity': intensity(m, level['conc'][m], attempt)}
                    for m in ('Cu', 'Zn', 'Ni')}})
        return runs, rows

    def test_fit_object(self):
        runs, rows = self.data(lambda m, conc, attempt: 10.0 + 4.0 * conc + (0.5 if attempt else -0.5))
        fit = cal.regress(self.plan, runs, rows)['Cu']
        self.assertEqual(set(fit), {'unit', 'points', 'excluded', 'valid', 'a', 'b', 'c', 'r2', 'n', 'levels'})
        self.assertEqual((fit['unit'], fit['valid'], fit['c'], fit['n'], fit['excluded']), ('mg/L', True, 1, 6, []))
        # x = 10 + 4 y +- 0.5 (symmetric): concentration on intensity.
        xs, ys = [9.5, 10.5, 13.5, 14.5, 17.5, 18.5], [0, 0, 1, 1, 2, 2]
        mx, my = sum(xs) / 6, sum(ys) / 6
        a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        self.assertAlmostEqual(fit['a'], a)
        self.assertAlmostEqual(fit['b'], my - a * mx)
        total = sum((y - my) ** 2 for y in ys)
        self.assertAlmostEqual(fit['r2'], 1 - sum((y - (a * x + my - a * mx)) ** 2 for x, y in zip(xs, ys)) / total)
        point = fit['points'][2]
        self.assertEqual(set(point), {'level', 'round', 'x', 'y', 'job', 'fitted', 'residual'})
        self.assertEqual((point['level'], point['round'], point['x'], point['y'], point['job']),
                         ('STD-1', 1, 13.5, 1.0, 'HMI-1-0'))
        self.assertAlmostEqual(point['fitted'], fit['a'] * 13.5 + fit['b'])
        self.assertAlmostEqual(point['residual'], 1.0 - point['fitted'])
        self.assertEqual([level['level'] for level in fit['levels']], ['Blank', 'STD-1', 'STD-2'])
        blank, std = fit['levels'][0], fit['levels'][1]
        self.assertEqual((blank['conc'], blank['n'], blank['mean_intensity']), (0.0, 2, 10.0))
        self.assertAlmostEqual(blank['sd'], math.sqrt(0.5))
        self.assertIsNone(blank['rsd_percent'])  # a percentage of the blank means nothing
        self.assertAlmostEqual(std['rsd_percent'], math.sqrt(0.5) / 14.0 * 100)
        json.dumps(fit, allow_nan=False)

    def test_points_that_are_left_out(self):
        runs, rows = self.data(lambda m, conc, attempt: 1.0 + conc + attempt * 0.01)
        runs.insert(0, {'level': 0, 'discard': True, 'round': None, 'job': 'HMI-discard'})  # never used
        rows[1]['metals']['Zn'] = {'valid': False, 'error': 'window holds no pixel'}
        rows[2] = {'job': rows[2]['job'], 'valid': False, 'error': 'no signal frames in HMI-1-0'}
        missing = rows.pop(5)
        fits = cal.regress(self.plan, runs, rows)
        self.assertEqual(fits['Cu']['n'], 4)
        self.assertEqual(fits['Cu']['excluded'], [
            {'level': 'STD-1', 'round': 1, 'reason': 'no signal frames in HMI-1-0'},
            {'level': 'STD-2', 'round': 2, 'reason': 'No analysis results'}])
        self.assertEqual(fits['Zn']['excluded'][0], {'level': 'Blank', 'round': 2, 'reason': 'window holds no pixel'})
        self.assertEqual(fits['Zn']['n'], 3)
        self.assertNotIn(missing['job'], [point['job'] for point in fits['Ni']['points']])
        self.assertEqual(next(level for level in fits['Zn']['levels'] if level['level'] == 'Blank')['sd'], None)  # n = 1

    def test_fits_that_cannot_be_made(self):
        runs, rows = self.data(lambda m, conc, attempt: 7.0)
        same = cal.regress(self.plan, runs, rows)['Cu']
        self.assertEqual((same['valid'], same['error']), (False, 'The light intensity is all the same and cannot be returned.'))
        self.assertNotIn('a', same)
        runs, rows = self.data(lambda m, conc, attempt: 1.0 + conc)
        fits = cal.regress(self.plan, runs, rows, {'Ni': [1, 2]})
        self.assertEqual((fits['Ni']['valid'], fits['Ni']['error']), (False, 'Insufficient effective points Is 2 different concentrations'))
        self.assertEqual(len(fits['Ni']['excluded']), 4)
        self.assertTrue(all(item == {'level': item['level'], 'round': item['round'], 'reason': 'Manually exclude this concentration',
                                     'manual': True} for item in fits['Ni']['excluded']))
        self.assertTrue(fits['Cu']['valid'])
        # A metal that no standard holds has no points at all.
        only_cu = cal.validate_plan(plan(levels=[{'name': 'B', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 1},
                                                 {'name': 'S', 'conc': {'Cu': 1}, 'rounds': 1}]))
        runs = [{'level': 0, 'discard': False, 'round': 1, 'job': 'b'},
                {'level': 1, 'discard': False, 'round': 1, 'job': 's'}]
        rows = [{'job': job, 'valid': True, 'metals': {m: {'valid': True, 'intensity': x} for m in cal.METALS}}
                for job, x in (('b', 1.0), ('s', 3.0))]
        fits = cal.regress(only_cu, runs, rows)
        self.assertEqual((fits['Cu']['a'], fits['Cu']['b'], fits['Cu']['r2']), (0.5, -0.5, 1.0))
        self.assertEqual((fits['Zn']['valid'], fits['Zn']['error'], len(fits['Zn']['points'])),
                         (False, 'Insufficient effective points Is 2 different concentrations', 1))
        with self.assertRaisesRegex(ValueError, 'The light intensity is all the same and cannot be returned.'):
            cal.linear_fit([2.0, 2.0], [0.0, 1.0])
        self.assertEqual(cal.linear_fit([0.0, 2.0], [5.0, 5.0]), (0.0, 5.0, 1.0))  # no spread in y: R² is 1

    def test_exclusions(self):
        self.assertEqual(cal.validate_exclusions(self.plan, None), {})
        self.assertEqual(cal.validate_exclusions(self.plan, {'Cu': [], 'Zn': [2, 1, 2]}), {'Zn': [1, 2]})
        cases = [([], 'The exclusion setting needs to be {Metal: [concentration serial number]}'), ({'Fe': [0]}, 'The exclusion setting needs to be {Metal: [concentration serial number]}'),
                 ({'Cu': 'x'}, 'Cu is incorrect.'), ({'Cu': [3]}, 'Cu is incorrect.'),
                 ({'Zn': [-1]}, 'Zn is incorrect.'), ({'Ni': [True]}, 'Ni is incorrect.'),
                 ({'Ni': [0.0]}, 'Ni is incorrect.'), ({'Ni': [0, 1, 2, 0]}, 'Ni is incorrect.')]
        for raw, text in cases:
            with self.assertRaises(ValueError) as caught:
                cal.validate_exclusions(self.plan, raw)
            self.assertEqual(str(caught.exception), text)


class RecordAndCoefficientTests(unittest.TestCase):
    def test_summary(self):
        record = {'id': 'CAL-' + '1' * 32, 'created_at': '2026-10-06T01:00:00+00:00', 'status': 'analyzed',
                  'operator': 'Wang Xiaoming', 'plan': cal.validate_plan(plan()), 'recipe': {'id': 'a' * 32, 'name': 'standard product'},
                  'runs': [{}, {}, {}], 'finished_at': '2026-10-06T02:00:00+00:00', 'error': None,
                  'analysis': {'method_id': 'area-v1-x', 'fits': {
                      'Cu': {'valid': True, 'a': 5.0, 'b': -0.05, 'c': 1, 'r2': 0.9999, 'n': 6, 'points': [1, 2]},
                      'Zn': {'valid': False, 'error': 'Insufficient effective points Is 2 different concentrations', 'points': []}}}}
        self.assertEqual(cal.summary(record), {
            'id': 'CAL-' + '1' * 32, 'created_at': '2026-10-06T01:00:00+00:00', 'status': 'analyzed',
            'operator': 'Wang Xiaoming', 'error': None, 'applied_at': None, 'finished_at': '2026-10-06T02:00:00+00:00',
            'recipe_name': 'standard product', 'levels': 3, 'runs': 3, 'method_id': 'area-v1-x',
            'fits': {'Cu': {'valid': True, 'a': 5.0, 'b': -0.05, 'r2': 0.9999, 'n': 6},
                     'Zn': {'valid': False, 'a': None, 'b': None, 'r2': None, 'n': None}}})
        running = {'id': 'CAL-' + '2' * 32, 'status': 'running', 'plan': {'levels': [1, 2]},
                   'recipe': {'name': 'r'}, 'runs': []}
        row = cal.summary(running)
        self.assertEqual((row['method_id'], row['fits'], row['levels'], row['runs']), (None, {}, 2, 0))
        self.assertTrue(cal.RECORD_ID.fullmatch('CAL-' + 'f' * 32))
        for bad in ('CAL-' + 'F' * 32, 'CAL-' + 'f' * 31, 'cal-' + 'f' * 32, '../CAL-' + 'f' * 32):
            self.assertIsNone(cal.RECORD_ID.fullmatch(bad))

    def test_coefficients(self):
        good = {m: {'a': ' 1.5 ', 'b': 0, 'c': 1e-3} for m in cal.METALS}
        self.assertEqual(cal.validate_calibration_config(good),
                         {m: {'a': '1.5', 'b': '0', 'c': '0.001'} for m in cal.METALS})
        empty = {m: {'a': None, 'b': None, 'c': None} for m in cal.METALS}
        self.assertEqual(cal.validate_calibration_config(empty), empty)
        self.assertEqual(cal.validate_calibration_config(cal.DEFAULT_COEFFICIENTS),
                         {m: {'a': '1', 'b': '0', 'c': '1'} for m in cal.METALS})
        self.assertEqual((cal.COEFFICIENTS, cal.FORMULA), (('a', 'b', 'c'), 'y = (a * x + b) * c'))

        def changed(metal, key, value):
            raw = copy.deepcopy(good)
            raw[metal][key] = value
            return raw
        cases = [(None, 'calibration parameters need to include Cu, Zn, Ni'), ({'Cu': good['Cu'], 'Zn': good['Zn']}, 'calibration parameters need to include Cu, Zn, Ni'),
                 (dict(good, Fe=good['Cu']), 'calibration parameters need to include Cu, Zn, Ni'),
                 (dict(good, Zn={'a': 1, 'b': 0}), 'Zn calibration parameters need to include a, b, c'),
                 (dict(good, Ni=[1, 0, 1]), 'Ni calibration parameters need to include a, b, c'),
                 (changed('Cu', 'a', True), 'Cu of a must be a finite value or left blank'),
                 (changed('Cu', 'b', [1]), 'Cu of b must be a finite value or left blank'),
                 (changed('Zn', 'c', '   '), 'Zn of c must be a finite value or left blank'),
                 (changed('Zn', 'c', '1' * 65), 'Zn of c must be a finite value or left blank'),
                 (changed('Ni', 'a', 'abc'), 'Ni of a must be a finite value'),
                 (changed('Ni', 'a', 'NaN'), 'Ni of a beyond the allowed range'),
                 (changed('Ni', 'b', float('inf')), 'Ni of b beyond the allowed range'),
                 (changed('Ni', 'c', 1e13), 'Ni of c beyond the allowed range')]
        for raw, text in cases:
            with self.assertRaises(ValueError) as caught:
                cal.validate_calibration_config(raw)
            self.assertEqual(str(caught.exception), text)
        self.assertEqual(cal.validate_calibration_config(changed('Cu', 'a', '-1e12'))['Cu']['a'], '-1E+12')
        self.assertEqual(cal.validate_calibration_config(changed('Cu', 'a', -1e12))['Cu']['a'], '-1000000000000.0')


if __name__ == '__main__':
    unittest.main()
