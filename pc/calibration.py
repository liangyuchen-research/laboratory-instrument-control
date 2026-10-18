"""Standard-curve calibration: plan, run sequence, regression, coefficients.

Ported from the original HMI (hmi_v1_1/calibration.py and calibration_config.py); pure functions.

A plan lists mixed standards (Cu/Zn/Ni in mg/L) measured low to high. Every round runs the
same published standard Recipe, which must contain exactly one plasma_run. Before each
standard the run waits for the operator to swap the bottle; an optional prime Recipe then
runs once to push out what the tubing still holds; the first ``discard_rounds`` rounds of
each standard flush the previous concentration and are not used.

Intensities come from analysis.py. The blank's rounds build the reference spectrum for shift
correction, so the first standard must be a blank. Each metal gets one least-squares line over
every kept round: concentration = a * intensity + b, and c resets to 1 when the result is applied.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation

from measurements import METALS

UNIT = 'mg/L'
MAX_LEVELS = 12
MAX_ROUNDS = 10
MAX_DISCARD = 3
MAX_RUNS = 80
MAX_CONCENTRATION = 100000
RECORD_ID = re.compile(r'CAL-[0-9a-f]{32}')
NAME_PATTERN = re.compile(r'[^\x00-\x1f<>"\\]{1,20}')
DEFAULT_PLAN = {'recipe_id': '', 'prime_recipe_id': '', 'discard_rounds': 1, 'levels': [
    {'name': 'Blank', 'conc': {metal: 0 for metal in METALS}, 'rounds': 3},
    {'name': 'STD-1', 'conc': {metal: None for metal in METALS}, 'rounds': 3},
    {'name': 'STD-2', 'conc': {metal: None for metal in METALS}, 'rounds': 3},
]}

COEFFICIENTS = ('a', 'b', 'c')
# What the program starts with while calibration_config.json does not exist (as the original).
DEFAULT_COEFFICIENTS = {metal: {'a': '1', 'b': '0', 'c': '1'} for metal in METALS}
FORMULA = 'y = (a * x + b) * c'


# ── plan ───────────────────────────────────────────────────────────────────

def _concentration(value, label):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(label + ' must be a number or left blank')
    try:
        number = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(label + ' must be a number or left blank') from exc
    if not math.isfinite(number) or not 0 <= number <= MAX_CONCENTRATION:
        raise ValueError(f'{label} Must be between 0–{MAX_CONCENTRATION} {UNIT}')
    return number


def _count(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label} Must be {low}–{high} integer')
    return value


def level_total(level):
    return sum(value for value in level['conc'].values() if value is not None)


def validate_plan(raw):
    """Clean a plan; standards are sorted low to high and the first must be the blank."""
    if not isinstance(raw, dict) or set(raw) - {'recipe_id', 'prime_recipe_id', 'discard_rounds', 'levels'}:
        raise ValueError('Calibration plan fields are incorrect')
    recipe_id = raw.get('recipe_id', '')
    if not isinstance(recipe_id, str) or (recipe_id and not re.fullmatch(r'[0-9a-f]{32}', recipe_id)):
        raise ValueError('Please select the standard product Recipe')
    prime_id = raw.get('prime_recipe_id') or ''
    if not isinstance(prime_id, str) or (prime_id and not re.fullmatch(r'[0-9a-f]{32}', prime_id)):
        raise ValueError('Fluid replacement and draining Recipe Incorrect')
    if prime_id and prime_id == recipe_id:
        raise ValueError('Fluid replacement and draining Recipe cannot be used with standard products Recipe same')
    discard = _count(raw.get('discard_rounds', 1), 'Number of pre-run rounds for each concentration', 0, MAX_DISCARD)
    levels = raw.get('levels')
    if not isinstance(levels, list) or not 2 <= len(levels) <= MAX_LEVELS:
        raise ValueError(f'standard product must have 2–{MAX_LEVELS} concentrations (including blank)')
    clean, names = [], set()
    for index, level in enumerate(levels, 1):
        if not isinstance(level, dict) or set(level) - {'name', 'conc', 'rounds'}:
            raise ValueError(f'No. {index} standard product fields are incorrect')
        name = level.get('name')
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name.strip() or '\x00'):
            raise ValueError(f'No. {index} standard product names must be 1–20 characters')
        name = name.strip()
        if name in names:
            raise ValueError(f'The standard product name is repeated:{name}')
        names.add(name)
        conc = level.get('conc')
        if not isinstance(conc, dict) or set(conc) - set(METALS):
            raise ValueError(f'{name} can only be ' + ', '.join(METALS))
        clean.append({'name': name,
                      'conc': {metal: _concentration(conc.get(metal), f'{name} of {metal}') for metal in METALS},
                      'rounds': _count(level.get('rounds'), f'{name} number of rounds', 1, MAX_ROUNDS)})
    clean.sort(key=level_total)  # stable: equal totals keep the entered order
    blank = clean[0]
    if any(blank['conc'][metal] != 0 for metal in METALS):
        raise ValueError('The lowest concentration standard must be blank:Cu, Zn, Ni Fill in all 0 (blank spectrum is used as a reference spectrum for displacement correction)')
    if sum(1 for level in clean if level_total(level) == 0) > 1:
        raise ValueError('can only have one blank standard')
    for metal in METALS:
        if not any(level['conc'][metal] for level in clean[1:]):
            continue
        values = [level['conc'][metal] for level in clean if level['conc'][metal] is not None]
        if len(set(values)) < 2:
            raise ValueError(f'{metal} At least required 2 different concentrations can be used for regression')
    runs = sum(level['rounds'] + discard for level in clean)
    if runs > MAX_RUNS:
        raise ValueError(f'Total number of measurements {runs} exceeds the upper limit {MAX_RUNS}')
    return {'recipe_id': recipe_id, 'prime_recipe_id': prime_id, 'discard_rounds': discard, 'levels': clean}


def plan_warnings(plan):
    """Order problems that do not block a run: a metal that goes down between standards."""
    warnings = []
    for metal in METALS:
        previous = None
        for level in plan['levels']:
            value = level['conc'][metal]
            if value is None:
                continue
            if previous is not None and value < previous[1]:
                warnings.append(f'{metal}: {level["name"]} ({value:g}) lower than the previous one {previous[0]} ({previous[1]:g}) ,'
                                f'The order from low to high will be disrupted, and the residue may be high.')
            previous = (level['name'], value)
    return warnings


def describe(level):
    parts = [f'{metal} {level["conc"][metal]:g}' for metal in METALS if level['conc'][metal] is not None]
    return f'{level["name"]} ({" / ".join(parts)} {UNIT}) '


# ── run sequence ───────────────────────────────────────────────────────────

REMOTE_ACTIONS = ('rpi_status', 'nano_prepare', 'plasma_run', 'spectrum_capture', 'nano_stop', 'power_off')
PROMPT_ACTION = 'operator_prompt'
PROMPT_NAME = 'Fluid change'
PRIME_PHASE = 'Fluid replacement and draining'


def prompt_text(level):
    return f'Please replace with standard product {describe(level)},Put the pipette into the new bottle and click ”Confirm Liquid Change”'


def _phase_name(step, phases):
    """A step's phase as a name: it is a name already, or an index into the Recipe's phase list."""
    phase = step.get('phase')
    if isinstance(phase, str):
        return phase
    if (isinstance(phase, int) and not isinstance(phase, bool) and isinstance(phases, list)
            and 0 <= phase < len(phases)):
        item = phases[phase]
        return str(item.get('name', '')) if isinstance(item, dict) else str(item)
    return ''


def build_sequence(plan, steps, prime_steps=None, phases=None):
    """Runner steps of one whole calibration, and their display phases.

    Per standard: bottle-swap prompt, prime Recipe (optional), then discard + kept rounds.
    ``steps`` / ``prime_steps``: the step dicts of recipe.validate() for the two Recipes;
    ``phases``: the standard Recipe's phase list (it gives the names when a step's ``phase`` is an
    index; the prompt is the phase Fluid change, the whole prime Recipe the phase Fluid replacement and draining).
    Returns {'steps', 'phases': [{'name', 'from', 'to'}]}: the steps are renumbered (``n`` from 1),
    ``round`` / ``rounds`` = run number / total runs, ``phase`` points into the returned list (or
    stays a name when the Recipe's steps carry names), and every step has
    ``calibration``: {level, level_name, run, discard, round[, prime]}.
    """
    plasma_runs = sum(1 for step in steps if step['action'] == 'plasma_run')
    if plasma_runs != 1:
        raise ValueError(f'standard product Recipe must contain exactly 1 indivual plasma_run (at present {plasma_runs} indivual) ; '
                         'The number of rounds is changed on the calibration page.')
    if prime_steps and any(step['action'] in REMOTE_ACTIONS for step in prime_steps):
        raise ValueError('Fluid replacement and draining Recipe can only have pump, syringe pump and waiting steps (cannot measure content or RPi steps)')
    total = sum(level['rounds'] + plan['discard_rounds'] for level in plan['levels'])
    by_name = all(isinstance(step.get('phase'), str) for step in list(steps) + list(prime_steps or []))
    out, names, run = [], [], 0

    def add(step, name, tag, number):
        out.append(dict(step, n=len(out) + 1, round=number, rounds=total, calibration=tag))
        names.append(name)

    for index, level in enumerate(plan['levels']):
        text = prompt_text(level)
        before = {'level': index, 'level_name': level['name'], 'run': run + 1, 'discard': None, 'round': None}
        add({'n': 0, 'line': 0, 'action': PROMPT_ACTION, 'name': PROMPT_NAME, 'target': '', 'target_label': '',
             'motor': None, 'motors': None, 'amounts': None,
             'value': '', 'unit': '', 'note': text, 'round': 0, 'rounds': 0, 'phase': 0, 'text': text,
             'planned_s': None}, PROMPT_NAME, dict(before), run + 1)
        for step in prime_steps or []:
            add(step, PRIME_PHASE, dict(before, prime=True), run + 1)
        for attempt in range(plan['discard_rounds'] + level['rounds']):
            run += 1
            discard = attempt < plan['discard_rounds']
            tag = {'level': index, 'level_name': level['name'], 'run': run, 'discard': discard,
                   'round': None if discard else attempt - plan['discard_rounds'] + 1}
            for step in steps:
                add(step, _phase_name(step, phases), dict(tag), run)
    sequence = []  # neighbouring steps of one name form one phase
    for step, name in zip(out, names):
        if sequence and sequence[-1]['name'] == name:
            sequence[-1]['to'] = step['n']
        else:
            sequence.append({'name': name, 'from': step['n'], 'to': step['n']})
        step['phase'] = name if by_name else len(sequence) - 1
    return {'steps': out, 'phases': sequence}


def build_steps(plan, steps, prime_steps=None):
    """The steps of build_sequence(); raises ValueError when a Recipe does not fit a calibration."""
    return build_sequence(plan, steps, prime_steps)['steps']


# ── regression ─────────────────────────────────────────────────────────────

def linear_fit(xs, ys):
    """Least squares y = a*x + b with R²."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError('The light intensity is all the same and cannot be returned.')
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - a * mx
    total = sum((y - my) ** 2 for y in ys)
    residual = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    return a, b, (1 - residual / total) if total > 0 else 1.0


def _stats(values):
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else None
    rsd = abs(sd / mean) * 100 if sd is not None and mean else None
    return mean, sd, rsd


def validate_exclusions(plan, raw):
    """{metal: [level index, ...]} of standards left out of one metal's fit (e.g. detector saturation)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) - set(METALS):
        raise ValueError('The exclusion setting needs to be {Metal: [concentration serial number]}')
    clean = {}
    for metal, levels in raw.items():
        if (not isinstance(levels, list) or len(levels) > len(plan['levels'])
                or any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(plan['levels'])
                       for i in levels)):
            raise ValueError(f'{metal} is incorrect.')
        if levels:
            clean[metal] = sorted(set(levels))
    return clean


def regress(plan, runs, analysis_rows, exclude=None):
    """Per metal: line through every kept, valid round of every standard containing it.

    exclude: {metal: [level index]} leaves those standards out of that metal's fit only.
    """
    by_job = {row['job']: row for row in analysis_rows}
    exclude = exclude or {}
    fits = {}
    for metal in METALS:
        points, excluded = [], []
        for run in runs:
            if run.get('discard'):
                continue
            level = plan['levels'][run['level']]
            conc = level['conc'][metal]
            if conc is None:
                continue
            if run['level'] in exclude.get(metal, ()):
                excluded.append({'level': level['name'], 'round': run.get('round'), 'reason': 'Manually exclude this concentration',
                                 'manual': True})
                continue
            row = by_job.get(run['job'])
            value = (row or {}).get('metals', {}).get(metal, {})
            if not row or row.get('valid') is not True or value.get('valid') is not True:
                reason = (row or {}).get('error') or value.get('error') or 'No analysis results'
                excluded.append({'level': level['name'], 'round': run.get('round'), 'reason': reason})
                continue
            points.append({'level': level['name'], 'round': run.get('round'), 'x': value['intensity'],
                           'y': conc, 'job': run['job']})
        fit = {'unit': UNIT, 'points': points, 'excluded': excluded, 'valid': False}
        if len({p['y'] for p in points}) < 2:
            fit['error'] = 'Insufficient effective points Is 2 different concentrations'
            fits[metal] = fit
            continue
        try:
            a, b, r2 = linear_fit([p['x'] for p in points], [p['y'] for p in points])
        except ValueError as exc:
            fit['error'] = str(exc)
            fits[metal] = fit
            continue
        for point in points:
            point['fitted'] = a * point['x'] + b
            point['residual'] = point['y'] - point['fitted']
        levels = []
        for level in plan['levels']:
            xs = [p['x'] for p in points if p['level'] == level['name']]
            if xs:
                mean, sd, rsd = _stats(xs)
                if not level['conc'][metal]:
                    rsd = None  # the blank's mean is near zero: a percentage of it means nothing
                levels.append({'level': level['name'], 'conc': level['conc'][metal], 'n': len(xs),
                               'mean_intensity': mean, 'sd': sd, 'rsd_percent': rsd})
        fit.update({'valid': True, 'a': a, 'b': b, 'c': 1, 'r2': r2, 'n': len(points), 'levels': levels})
        fits[metal] = fit
    return fits


# ── records ────────────────────────────────────────────────────────────────

SUMMARY_KEYS = ('id', 'created_at', 'status', 'operator', 'error', 'applied_at', 'finished_at')


def summary(record):
    row = {key: record.get(key) for key in SUMMARY_KEYS}
    row['recipe_name'] = record['recipe']['name']
    row['levels'] = len(record['plan']['levels'])
    row['runs'] = len(record['runs'])
    analysis = record.get('analysis') or {}
    row['method_id'] = analysis.get('method_id')
    row['fits'] = {metal: {key: fit.get(key) for key in ('valid', 'a', 'b', 'r2', 'n')}
                   for metal, fit in (analysis.get('fits') or {}).items()}
    return row


# ── coefficients ───────────────────────────────────────────────────────────

def validate_calibration_config(raw):
    if not isinstance(raw, dict) or set(raw) != set(METALS):
        raise ValueError('calibration parameters need to include Cu, Zn, Ni')
    clean = {}
    for metal in METALS:
        values = raw[metal]
        if not isinstance(values, dict) or set(values) != set(COEFFICIENTS):
            raise ValueError(f'{metal} calibration parameters need to include a, b, c')
        clean[metal] = {}
        for coefficient in COEFFICIENTS:
            value = values[coefficient]
            if value is None:
                clean[metal][coefficient] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f'{metal} of {coefficient} must be a finite value or left blank')
            raw_value = str(value).strip()
            if not raw_value or len(raw_value) > 64:
                raise ValueError(f'{metal} of {coefficient} must be a finite value or left blank')
            try:
                decimal_value = Decimal(raw_value)
            except InvalidOperation as exc:
                raise ValueError(f'{metal} of {coefficient} must be a finite value') from exc
            if not decimal_value.is_finite() or abs(decimal_value) > Decimal('1e12'):
                raise ValueError(f'{metal} of {coefficient} beyond the allowed range')
            clean[metal][coefficient] = str(decimal_value)
    return clean
