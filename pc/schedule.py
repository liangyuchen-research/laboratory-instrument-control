"""Time calculation for scheduled measurement: Fixed time grid, one measurement (discharge) for each grid＋capture spectrum).

The method follows the old version HMI v1.1 of scheduler.py: No. i times booked at ”first time + i × interval”. Not last time
The expired period at the end will not be made up and will be recorded as ”skip”. The next time will start from the next period that has not passed, so the result will always be
falls on the time grid set by the operator. Only time calculation and checking are done here; equipment and execution threads are app.py of Schedule Responsible.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

MINUTE = 60  # seconds in one "minute" of the interval; the tests shrink it so a schedule takes seconds
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440
MAX_COUNT = 1000
MAX_SPAN = timedelta(days=31)
HHMM = re.compile(r'([01]\d|2[0-3]):([0-5]\d)')

# A schedule in one of these states still owns the equipment.
ACTIVE = ('waiting', 'running', 'pausing', 'paused', 'stopping')
# ...and in one of these it may move something at any moment (a paused one waits for continue or Stop).
BUSY = ('waiting', 'running', 'pausing', 'stopping')
STATUS_LABEL = {'waiting': 'Waiting for the next time', 'running': 'under measurement', 'pausing': 'Paused', 'paused': 'Suspended',
                'stopping': 'Stopping', 'completed': 'Completed',
                'stopped': 'Stopped', 'failed': 'stopped due to failure', 'interrupted': 'Interrupted'}
SLOT_LABEL = {'running': 'under measurement', 'done': 'Complete', 'failed': 'failed', 'stopped': 'Stopped', 'not_started': 'not started',
              'skipped_overrun': 'Skip (last timeout)', 'skipped_paused': 'skip (scheduling pause)'}
# One round of a Recipe, and one step of it (2.0.0; the page gets all four tables from /api/bootstrap).
ROUND_LABEL = {'running': 'is executing', 'pausing': 'Paused', 'paused': 'Suspended', 'stopping': 'Stopping', 'completed': 'Complete',
               'failed': 'failed', 'stopped': 'Stopped', 'interrupted': 'Interrupted'}
STEP_LABEL = {'pending': '—', 'running': 'is executing', 'done': 'Complete', 'failed': 'failed', 'stopped': 'Stopped',
              'skipped': 'not executed'}


def local_now():
    return datetime.now().astimezone()


def next_clock_time(hhmm, after):
    """First moment at HH:MM (local time) that is not before ``after``."""
    match = HHMM.fullmatch(hhmm if isinstance(hhmm, str) else '')
    if not match:
        raise ValueError('The time format must be HH:MM (00:00–23:59) ')
    moment = after.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
    return moment if moment >= after.replace(microsecond=0) else moment + timedelta(days=1)


def validate(raw, now=None):
    """Check what the operator filled in and turn it into a plan; ValueError carries the message to show."""
    now = now or local_now()
    if not isinstance(raw, dict):
        raise ValueError('The schedule setting format is incorrect.')
    interval = raw.get('interval_minutes')
    if type(interval) is not int or not MIN_INTERVAL_MINUTES <= interval <= MAX_INTERVAL_MINUTES:
        raise ValueError(f'The measurement interval must be {MIN_INTERVAL_MINUTES}–{MAX_INTERVAL_MINUTES} Integer of minutes')
    start = raw.get('start') if isinstance(raw.get('start'), dict) else {}
    if start.get('mode') == 'now':
        first = now.replace(microsecond=0)
    elif start.get('mode') == 'at':
        first = next_clock_time(start.get('time'), now)
    else:
        raise ValueError('The start method must be ”immediately” or ”specified time”')
    end = raw.get('end') if isinstance(raw.get('end'), dict) else {}
    mode = end.get('mode')
    if mode == 'count':
        count = end.get('count')
        if type(count) is not int or not 1 <= count <= MAX_COUNT:
            raise ValueError(f'The number of measurements must be 1–{MAX_COUNT} Second-rate')
        normalized = {'mode': 'count', 'count': count}
    elif mode == 'until':
        until = next_clock_time(end.get('time'), first + timedelta(seconds=1))
        normalized = {'mode': 'until', 'time': end['time'], 'until': until.isoformat()}
    elif mode == 'continuous':
        normalized = {'mode': 'continuous'}
    else:
        raise ValueError('The end condition must be ”number of times”, ”to the specified time” or ”continue until stop is pressed”')
    spec = {'first_slot': first.isoformat(), 'interval_seconds': interval * MINUTE, 'interval_minutes': interval,
            'start': {'mode': start['mode'], 'time': start.get('time') if start['mode'] == 'at' else None},
            'end': normalized}
    last = last_index(spec)
    if last is not None and slot_time(spec, last) - first > MAX_SPAN:
        raise ValueError('The total schedule length cannot exceed 31 days; for long-term operation, please select ”Continue until you press stop” After')
    return spec


def slot_time(spec, index):
    return datetime.fromisoformat(spec['first_slot']) + timedelta(seconds=spec['interval_seconds'] * index)


def last_index(spec):
    """Index of the final slot, or None for a schedule that runs until it is stopped."""
    end = spec['end']
    if end['mode'] == 'count':
        return end['count'] - 1
    if end['mode'] == 'until':
        span = (datetime.fromisoformat(end['until']) - datetime.fromisoformat(spec['first_slot'])).total_seconds()
        return max(0, math.floor(span / spec['interval_seconds']))
    return None


def planned_total(spec):
    last = last_index(spec)
    return None if last is None else last + 1


def next_due_index(spec, after_index, now, grace_seconds=5):
    """First slot after ``after_index`` that has not passed yet (with a small grace).

    Returns (index, missed); ``missed`` lists the slots that came due while the last run was busy.
    """
    index = after_index + 1
    missed = []
    while slot_time(spec, index) < now - timedelta(seconds=grace_seconds):
        missed.append(index)
        index += 1
    return index, missed
