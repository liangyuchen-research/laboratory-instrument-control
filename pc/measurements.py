"""Per-measurement results: concentrations for samples, baseline check for blanks.

Ported from the original HMI (hmi_v1_1/measurements.py). After every plasma_run outside a
calibration the stored spectrum set is analyzed against the reference spectrum of the applied
calibration (analysis.py). This module turns that analysis row into one measurement record:

  sample  concentration = (a * intensity + b) * c with the applied coefficients
  blank   the same numbers plus a baseline check against the calibration's blank:
          spectral shift, apparent concentration and normalization area change.
          A failed check raises an alarm; it never stops the schedule.

Blank zero (optional per metal, data/blank_zero.json): a sample's concentration is reduced by
the median of the last N blanks measured with the same calibration and analysis method (all
blanks, QC pass or fail; values further than 3 x MAD from their median are left out). Nothing is
subtracted until at least ``min_blanks`` blanks exist. The uncorrected value stays in ``conc_raw``.

Records are appended to data/measurements.jsonl (one line each) for the trend page.
Not carried over from the original: frames_per_sum (a pure scale factor of the intensities).
"""
from __future__ import annotations

import json
import math
import statistics
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

METALS = ('Cu', 'Zn', 'Ni')
KINDS = ('sample', 'blank')
DEFAULT_QC = {'max_shift_nm': 0.05, 'max_apparent_conc': {metal: 0.05 for metal in METALS},
              'max_norm_change_percent': 20}
KEEP_IN_MEMORY = 5000
DEFAULT_BLANK_ZERO = {'enabled': {'Cu': False, 'Zn': False, 'Ni': True}, 'window': 10, 'min_blanks': 3,
                      'mad_reject': True}


# ── blank zero ─────────────────────────────────────────────────────────────

def validate_blank_zero(raw):
    raw = raw if isinstance(raw, dict) else {}
    if set(raw) - set(DEFAULT_BLANK_ZERO):
        raise ValueError('Blank zero point setting field:enabled, window, min_blanks, mad_reject')
    merged = {**DEFAULT_BLANK_ZERO, **raw}
    enabled = merged['enabled']
    if not isinstance(enabled, dict) or set(enabled) - set(METALS) or not all(isinstance(v, bool) for v in enabled.values()):
        raise ValueError('enabled Need to be {Metal: true/false}')
    enabled = {metal: enabled.get(metal, DEFAULT_BLANK_ZERO['enabled'][metal]) for metal in METALS}
    window, minimum = merged['window'], merged['min_blanks']
    for value, label, low, high in ((window, 'Use blank times', 3, 30), (minimum, 'Minimum number of blanks', 1, 30)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{label}Must be {low}–{high} integer')
    if minimum > window:
        raise ValueError('The minimum number of blanks cannot be greater than the number of blanks taken')
    if not isinstance(merged['mad_reject'], bool):
        raise ValueError('mad_reject Need to be true/false')
    return {'enabled': enabled, 'window': window, 'min_blanks': minimum, 'mad_reject': merged['mad_reject']}


def zero_status(blanks, settings):
    """Zero per metal from blank records (oldest first) of one calibration and method."""
    status = {}
    for metal in METALS:
        values = []
        for blank in blanks:
            item = (blank.get('metals') or {}).get(metal) or {}
            value = item.get('conc_raw', item.get('conc'))
            if blank.get('valid') and isinstance(value, (int, float)) and math.isfinite(value):
                values.append(value)
        values = values[-settings['window']:]
        used = values
        if settings['mad_reject'] and len(values) >= 3:
            middle = statistics.median(values)
            spread = statistics.median(abs(v - middle) for v in values) * 1.4826
            if spread > 0:
                used = [v for v in values if abs(v - middle) <= 3 * spread]
        ready = len(used) >= settings['min_blanks']
        status[metal] = {'enabled': settings['enabled'][metal], 'ready': ready,
                         'zero': statistics.median(used) if ready else None,
                         'n': len(used), 'rejected': len(values) - len(used)}
    return status


def apply_zero(record, status):
    """Subtract the blank zero from a sample record in place; conc_raw keeps the uncorrected value."""
    applied = {}
    for metal in METALS:
        item = record['metals'][metal]
        item['conc_raw'] = item['conc']
        state = status[metal]
        use = state['enabled'] and state['ready'] and item['conc'] is not None
        if use:
            item['conc'] = item['conc'] - state['zero']
            item['zero'] = state['zero']
        applied[metal] = {'applied': use, 'zero': state['zero'], 'n': state['n'], 'enabled': state['enabled']}
    record['blank_zero'] = applied
    return record


# ── blank check thresholds ─────────────────────────────────────────────────

def now():
    return datetime.now(timezone.utc).isoformat()


def _limit(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise ValueError(f'{label} Must be between {low}–{high}')
    return float(value)


def validate_qc(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_QC):
        raise ValueError('Blank threshold field:max_shift_nm, max_apparent_conc, max_norm_change_percent')
    merged = {**DEFAULT_QC, **raw}
    conc = merged['max_apparent_conc']
    if not isinstance(conc, dict) or set(conc) - set(METALS):
        raise ValueError('The blank apparent concentration threshold must be Cu/Zn/Ni')
    return {'max_shift_nm': _limit(merged['max_shift_nm'], 'displacement threshold (nm)', 0.001, 1),
            'max_apparent_conc': {m: _limit(conc.get(m, DEFAULT_QC['max_apparent_conc'][m]),
                                            f'{m} apparent concentration threshold (mg/L)', 0, 1000) for m in METALS},
            'max_norm_change_percent': _limit(merged['max_norm_change_percent'], 'Normalized area change threshold (%)', 1, 100)}


# ── one record ─────────────────────────────────────────────────────────────

def concentration(coefficients, intensity):
    try:
        a, b, c = (Decimal(str(coefficients[key])) for key in ('a', 'b', 'c'))
        return float((a * Decimal(str(intensity)) + b) * c)
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None


def _shift_at_peaks(row):
    shift = row.get('shift') or {}
    if not shift.get('applied'):
        return None
    values = []
    for item in (row.get('metals') or {}).values():
        peak = (item.get('windows_used') or {}).get('peak')
        if peak:
            middle = (peak[0] + peak[1]) / 2
            values.append(abs(shift['p0'] + shift['p1'] * middle))
    return max(values) if values else abs(shift['p0'])


def evaluate(kind, row, method_id, context, coefficients, qc):
    """One measurement record from one analysis row.

    ``context`` describes the applied calibration (None when nothing was applied):
    calibration_id, method_id, metals (applied), blank_norm {metal: mean norm area}.
    """
    record = {'kind': kind, 'job': row.get('job'), 'method_id': method_id, 'valid': row.get('valid') is True,
              'error': row.get('error') or '', 'shift_nm': _shift_at_peaks(row), 'metals': {},
              'calibration_id': context['calibration_id'] if context else None}
    usable = context is not None and context.get('method_id') == method_id and record['valid']
    if context is None:
        reason = 'Correction has not been applied yet'
    elif context.get('method_id') != method_id:
        reason = 'The analysis settings are different from the applied calibration, please recalibrate.'
    elif not record['valid']:
        reason = 'This analysis is invalid'
    else:
        reason = ''
    record['conc_reason'] = reason
    for metal in METALS:
        value = (row.get('metals') or {}).get(metal) or {}
        item = {'intensity': value.get('intensity') if value.get('valid') else None, 'conc': None}
        if value.get('valid') and value.get('norm_area') is not None:
            item['norm_area'] = value['norm_area']
        if usable and value.get('valid') and metal in context.get('metals', ()):
            item['conc'] = concentration(coefficients[metal], value['intensity'])
        record['metals'][metal] = item
    if kind == 'blank':
        record['qc'] = blank_check(record, context, qc, reason)
    return record


def blank_check(record, context, qc, reason):
    if reason:
        return {'status': 'unchecked', 'problems': [], 'reason': reason + ',Unable to determine baseline'}
    problems = []
    if record['shift_nm'] is not None and record['shift_nm'] > qc['max_shift_nm']:
        problems.append(f'spectral shift {record["shift_nm"]:.3f} nm (threshold {qc["max_shift_nm"]:g}) ')
    for metal in METALS:
        item = record['metals'][metal]
        limit = qc['max_apparent_conc'][metal]
        if item['conc'] is not None and abs(item['conc']) > limit:
            problems.append(f'{metal} apparent concentration {item["conc"]:.4g} mg/L (threshold ±{limit:g}) ')
        reference = (context.get('blank_norm') or {}).get(metal)
        if reference and item.get('norm_area') is not None:
            change = (item['norm_area'] / reference - 1) * 100
            item['norm_change_percent'] = change
            if abs(change) > qc['max_norm_change_percent']:
                problems.append(f'{metal} Normalized area change {change:+.1f}% (threshold ±{qc["max_norm_change_percent"]:g}%) ')
    return {'status': 'fail' if problems else 'ok', 'problems': problems, 'reason': ''}


def failed(kind, job, error, context):
    """Record for a run whose analysis could not be obtained at all."""
    record = {'kind': kind, 'job': job, 'method_id': None, 'valid': False, 'error': error[:300], 'shift_nm': None,
              'metals': {metal: {'intensity': None, 'conc': None} for metal in METALS},
              'calibration_id': context['calibration_id'] if context else None, 'conc_reason': 'Analysis failed'}
    if kind == 'blank':
        record['qc'] = {'status': 'unchecked', 'problems': [], 'reason': 'Analysis failed, unable to determine baseline'}
    return record


def summary(record):
    """Short form kept with the run-log row."""
    return {'id': record['id'], 'kind': record['kind'], 'valid': record['valid'],
            'conc': {metal: record['metals'][metal]['conc'] for metal in METALS},
            'conc_reason': record.get('conc_reason', ''),
            'qc': (record.get('qc') or {}).get('status')}


# ── the log ────────────────────────────────────────────────────────────────

class Log:
    """Append-only log of measurements (JSON lines) plus the most recent ones in memory."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.recent = deque(maxlen=KEEP_IN_MEMORY)
        self._unfinished = False  # the file ends in the middle of a line (power loss while writing)
        try:
            with self.path.open('r', encoding='utf-8', errors='replace', newline='') as source:
                last = ''
                for line in deque(source, maxlen=KEEP_IN_MEMORY):
                    last = line
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        self.recent.append(item)
                self._unfinished = bool(last) and not last.endswith('\n')
        except OSError:  # no file yet (or it cannot be read): start empty
            pass

    def add(self, record):
        record = {'id': uuid.uuid4().hex, 'time': now(), **record}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n'
        with self.lock:
            with self.path.open('a', encoding='utf-8', newline='') as out:
                out.write(('\n' if self._unfinished else '') + line)
                out.flush()
            self._unfinished = False
            self.recent.append(record)
        return record

    def rows(self):
        with self.lock:
            return list(self.recent)

    def latest(self, kind):
        return next((item for item in reversed(self.rows()) if item.get('kind') == kind), None)

    def since(self, cutoff_iso=None, limit=KEEP_IN_MEMORY):
        rows = [item for item in self.rows() if not cutoff_iso or item.get('time', '') >= cutoff_iso]
        return rows[-limit:]
