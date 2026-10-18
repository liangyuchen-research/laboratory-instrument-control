#!/usr/bin/env python3
"""Plasmic Electronic control TCP server (RPi terminal resident program).

allows computers or other devices to use TCP/IP Control electronic control three-piece set:HSPY high voltage power supply,Nano pulse control, spectrometer.

protocol: one per line JSON (UTF-8,ends with a newline), one question and one answer. For example
    Send  {"cmd":"status","token":"..."}
    received  {"ok":true,"busy":false,"snapshot":{...}}

Do not touch this program yourself RS485, Not directly HSPY/Nano issues the command. All hardware movements are performed on the same machine RPi superior
Field proven tool (rpi_tools/plasma_job.py , etc., the content has not been modified), this program is only responsible for:
  * Receive instructions and check the source IP and token, Only one job is allowed at a time;
  * starts the tool stroke, reads back the results and spectral files;
  * watchdog: Down plasma_run is interrupted, or exceeds The firmware of watchdog_s seconds without any message, right
    Discharge stroke free SIGTERM,The tool will do it by itself HSPY OFF/Nano STOP shutdown (3 It can’t be seen after 2 seconds that it has
    starts to shut down, send it again, up to two times);
  * Every time someone is connected poll_s Read the device status once every second (update at the same time Nano LCD on POWER show) ; 
  * answered ”Automatic search RPi”of UDP broadcast (same port number), only reply who you are, not including token.

The software stop is not a safety level emergency stop. Please physically disconnect before repairing or wiring. 24 V and high voltage power supply.

use --sim can be executed as an emulated device on any computer (no need RPi and hardware) for practice and testing.
"""
from __future__ import annotations

import argparse
import csv
import hmac
import json
import math
import os
import random
import re
import secrets
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = '2.0.0'
# 2: plasma_run There is no longer an ”order” option, it will always follow the configuration file "flow" measurement (background spectrum 1 open) .protocol 1 computer version
#    program will also let you select ”Quick/”Original”, when it connects, it will say that the versions on both sides are different, please update, and it will not discharge as usual.
PROTOCOL = 2
# There is only one process for measuring once, which is determined by the configuration file. "flow" decided, neither the command nor the screen can be selected:
#   unified (Default): Same as the folder plasma_measure.py.Turn on the high voltage first (no discharge), then take the background spectrum when the voltage reaches 1 open,
#                    is discharged after shooting; the exposure and discharge of each round are aligned.
#   verified       : rpi_tools/plasma_job.py,has been verified on site and the content has not been modified (background 3 open,about 11 will be discharged after 2 seconds. Give up after
#                    Exposure is later than discharge 0.01–0.03 seconds). Keep it as a retreat: keep it config.json of "flow" changed to "verified"
#                    Restart the service. It will be used only if the configuration file is written like this;plasma_measure.py If it disappears, it will not be automatically reused..
FLOWS = ('unified', 'verified')
UNIFIED_BACKGROUND_FRAMES = 1  # as in plasma_measure.py
# How long does one round actually take and which parameters cannot be achieved. If the tools onplasma_measure.pace() Another copy with the same rule (check that program yourself
# once) ; tests/test_tcpd.py Check that the answers on both sides are the same one by one. The origin of the numbers is written in plasma_measure.py.
LOOP_OVERHEAD_US = 150000      # From the end of one pulse to the start of the next round,RS485 It will take at least this long
SHORTEST_PERIOD_US = 250000    # No matter how short the interval is, it cannot be closer than this.
LONGEST_PERIOD_US = 28000000   # Nano Waiting for the next trigger at most 30 seconds, if exceeded, it will be locked. TRIGGER_TIMEOUT
LONGEST_SIGNAL_US = 100000000  # Total of all rounds;Nano is The value of 120 Second
MOST_SIGNAL_FRAMES = 999       # spectrometer program at most once 1000 open,in 1 Zhang is the background
# After so long after the stop signal is sent, the tool is still executing and it cannot be seen that it has started to shut down, so send it again (only this time).
REPEAT_TERM_SECONDS = 3.0
# “automatic search RPi”: Computer-to-area network broadcast line (UDP,port number and TCP is the same), the server replies who it is.
DISCOVER_REQUEST = b'PLASMIC-DISCOVER 1'
# and rpi_tools/plasma_job.py of MAX_VOLTAGE is the same; the real control is still plasma_job.py.
MAX_VOLTAGE = 700
# has no upper limit of current (user 2026-10-07 ’s decision): The current limit is the value set on the high-voltage power supply panel and is not checked here. Automatic test of
PARAMETER_FIELDS = ('Ontime', 'Offtime', 'Cycle', 'Loopnum',
                    'Integration_Time', 'Spectra_Interval', 'Voltage')
JOB_ID = re.compile(r'(?:HMI|CAP|LITE)-[0-9a-f]{32}')
MAX_LINE = 65536
MAX_CONNECTIONS = 4
IDLE_SECONDS = 300
MAX_FRAME_VALUES = 250_000
# unnecessary token ’s instructions: query identity, stay connected, and stop ”in a safe direction”.
OPEN_COMMANDS = ('hello', 'ping', 'auth', 'stop')

DEFAULTS = {
    'bind': '0.0.0.0',
    'port': 8767,
    'allow': ['127.0.0.1', '192.168.1.2'],
    'token': '',
    'watchdog_s': 5,
    'flow': 'unified',
    # unified Process: After the spectrometer program took over the exposure, it took so long to send it D12 trigger (ms,0–200) .real sensor
    # to start to be exposed? You have to try it on the device to know; you can adjust it here without changing the program.
    'exposure_lead_ms': 5,
    # Nano of LCD exist 5 If no new power supply is received within seconds, the readback will POWER displayed as“?”,So than 5 seconds short.
    'poll_s': 3,
    'discovery': True,
    'app_dir': '/home/pi/Desktop/Plasmic/app',
    'tools_dir': '/home/pi/Desktop/Plasmic/nano_lcd_v1_1/rpi_tools',
    'serial_port': '/dev/RS485',
    'output_dir': '/home/pi/Desktop/Plasmic/logs/hmi-captures',
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Failure(Exception):
    """An error reported to the client as {"ok": false, "code": ..., "error": ...}."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def load_config(path, overrides=None):
    raw = {}
    if path:
        raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
            raise ValueError('configuration file contains unknown fields:' + ', '.join(sorted(set(raw) - set(DEFAULTS))))
    config = dict(DEFAULTS)
    config.update(raw)
    config.update(overrides or {})
    if type(config['port']) is not int or not 1 <= config['port'] <= 65535:
        raise ValueError('port Must be 1–65535')
    if (not isinstance(config['allow'], list) or not config['allow']
            or not all(isinstance(item, str) and item for item in config['allow'])):
        raise ValueError('allow Must be IP string list, for example ["127.0.0.1", "192.168.1.2"]')
    if not isinstance(config['token'], str) or len(config['token']) > 256:
        raise ValueError('token must be a string')
    if type(config['discovery']) is not bool:
        raise ValueError('discovery Must be true or false')
    if config['flow'] not in FLOWS:
        raise ValueError('flow Must be "unified" or "verified"')
    lead = config['exposure_lead_ms']
    if isinstance(lead, bool) or not isinstance(lead, (int, float)) or not 0 <= lead <= 200:
        raise ValueError('exposure_lead_ms Must be 0–200 (milliseconds)')
    for key, low, high in (('watchdog_s', 2, 60), ('poll_s', 3, 60)):
        if type(config[key]) not in (int, float) or not low <= config[key] <= high:
            raise ValueError(f'{key} Must be {low}–{high} Second')
    for key in ('bind', 'app_dir', 'tools_dir', 'serial_port', 'output_dir'):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f'{key} Cannot be blank')
    return config


def allowed(ip, allow):
    """True when ``ip`` may connect: an exact address, "192.168.0.*" for a whole network, or "*"."""
    return any(item == '*' or item == ip or (item.endswith('.*') and ip.startswith(item[:-1]))
               for item in allow)


# ---------------------------------------------------------------- Parameter and readiness check

def _exact(value, name, multiplier=1):
    """Same exactness rule as nano_verify.exact_integer: never round or truncate silently."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number')
    try:
        number = Decimal(str(value)) * multiplier
    except (InvalidOperation, ValueError):
        raise ValueError(f'{name} must be a number') from None
    if not number.is_finite() or number != number.to_integral_value() or not 0 <= number <= 0xffffffff:
        raise ValueError(f'{name} The precision or range is incorrect')
    return int(number)


def loop_period_us(on_us, off_us, cycles, interval_us):
    """What one loop really takes on the 9600-baud bus, in microseconds (plasma_measure.pace())."""
    return max(interval_us, (on_us + off_us) * cycles + LOOP_OVERHEAD_US, SHORTEST_PERIOD_US)


def check_parameters(raw):
    """Friendly pre-check of the seven plasma parameters (times in ms, as in parameter.json).

    plasma_job.py validates again before anything is written; this must never be looser. What
    cannot work is refused here already, so that it cannot even be saved: plasma_measure.py makes
    the same three checks (pace()) before it touches anything.
    """
    if not isinstance(raw, dict) or set(raw) != set(PARAMETER_FIELDS):
        raise ValueError('Plasma parameters must contain exactly:' + ', '.join(PARAMETER_FIELDS))
    on = _exact(raw['Ontime'], 'Ontime', 1000)
    off = _exact(raw['Offtime'], 'Offtime', 1000)
    cycles = _exact(raw['Cycle'], 'Cycle')
    loops = _exact(raw['Loopnum'], 'Loopnum')
    integration = _exact(raw['Integration_Time'], 'Integration_Time', 1000)
    interval = _exact(raw['Spectra_Interval'], 'Spectra_Interval', 1000)
    for name, value in (('Ontime', on), ('Offtime', off)):
        if not 1000 <= value <= 1000000 or value % 100:
            raise ValueError(f'{name} Must be 1–1000 ms (minimum scale 0.1 ms) ')
    if not 1 <= cycles <= 10000 or not 1 <= loops <= 1000 or cycles * loops > 10000:
        raise ValueError('Cycle Must be 1–10000, Loopnum Must be 1–1000,and Cycle × Loopnum not to exceed 10000')
    if (on + off) * cycles * loops > 120_000_000:
        raise ValueError('total pulse time ((Ontime+Offtime) × Cycle × Loopnum) not to exceed 120 Second')
    if integration % 1000 or not 1000 <= integration <= 10_000_000:
        raise ValueError('Integration_Time Must be 1–10000 ms integer')
    if interval % 1000 or not integration <= interval <= 60_000_000:
        raise ValueError('Spectra_Interval must be an integer millisecond, and not less than Integration_Time, no greater than 60000')
    period = loop_period_us(on, off, cycles, interval)
    if loops >= 2 and period > LONGEST_PERIOD_US:
        raise ValueError('cannot exceed between two rounds 28 seconds (Nano The upper limit of waiting for triggering is 30 seconds); currently each round %.2f Second' % (period / 1e6))
    if loops * period > LONGEST_SIGNAL_US:
        raise ValueError('Number of rounds × Each round time cannot exceed 100 seconds (Nano is The value of 120 seconds); currently %d wheel × %.2f Second = %.1f Second.'
                         'Each round time is ”spectral interval” and ”single round pulse time”＋0.15 Second”“0.25 seconds” the largest of the three'
                         % (loops, period / 1e6, loops * period / 1e6))
    if loops > MOST_SIGNAL_FRAMES:
        raise ValueError('spectrometer program at most once 1000 open (background 1 open＋Most signals 999 open) ')
    voltage = raw['Voltage']
    if type(voltage) is not int or not 1 <= voltage <= MAX_VOLTAGE:
        raise ValueError(f'Voltage Must be 1–{MAX_VOLTAGE} V integer')
    return {name: raw[name] for name in PARAMETER_FIELDS}


def estimate_start_seconds(parameter, flow='unified'):
    """Roughly how long after the command the first pulse comes.

    unified: the supply is switched on first (no pulse), and the one background frame is taken once
    the voltage is there, so the whole integration time is part of the wait. On the pretend rig of
    tests/test_measure.py, at 30 V and 1000 ms integration: 3.6 s when the supply needs 0.4 s to
    reach its voltage, 4.4 s when it needs 1.5 s, 5.0 s when it needs 2.0 s. Not yet measured on
    the equipment; the supply may ramp longer at a higher voltage. START is sent while the frame
    exposes and takes about 0.6 s, so a shorter frame does not shorten the wait further.
    verified: measured on site on 2026-10-05 (10.3 s with 1000 ms integration; three background
    frames come first).
    """
    try:
        if flow == 'unified':
            return int(round(3.6 + max(parameter['integration_ms'] / 1000, .6)))
        return int(round(3 * parameter['integration_ms'] / 1000 + 8))
    except (KeyError, TypeError):
        return None


def estimate_seconds(parameter, flow='unified'):
    """Rough duration of one plasma_run from the status 'parameter' block."""
    try:
        pulse_ms = (parameter['on_time_ms'] + parameter['off_time_ms']) * parameter['cycles']
        signal_s = parameter['loops'] * max(parameter['spectra_interval_ms'], pulse_ms) / 1000
        if flow == 'unified':
            # The loops come one period apart: the interval, or what the bus allows when that is longer.
            period_s = loop_period_us(parameter['on_time_ms'] * 1000, parameter['off_time_ms'] * 1000,
                                      parameter['cycles'], parameter['spectra_interval_ms'] * 1000) / 1e6
            return int(round(estimate_start_seconds(parameter, flow) + parameter['loops'] * period_s + 3))
        background_s = 3 * parameter['integration_ms'] / 1000 + 6
        ramp_s = 3 + parameter['voltage_v'] / 100
        return int(round(background_s + ramp_s + signal_s + 8))
    except (KeyError, TypeError):
        return None


def _log_events(path):
    """The events of a spectrometer program's progress.jsonl; [] when there is none."""
    events = []
    try:
        if path.stat().st_size > 2_000_000:
            return events
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
                event['at'] = datetime.fromisoformat(event['utc'])
                events.append(event)
            except (ValueError, KeyError, TypeError):
                continue
    except OSError:
        pass
    return events


def job_timing(directory, report, received_at=None):
    """How long the discharge took to begin and how every exposure lay to its pulse train.

    Only what the tools wrote down themselves. plasma_measure.py notes the moment it raised D12
    for each frame, so "lead_ms" is measured: by how much the request for the exposure came before
    the trigger. plasma_job.py (verified) notes nothing: there the spectrometer program is told
    only after D12 has been HIGH for 10 ms, and it looks for that word every 10 ms, so the exposure
    begins 0.01–0.03 s after the pulses do; "lead_ms" stays None and the times come from that
    program's own log.
    """
    shots = {shot.get('frame'): shot for shot in report.get('triggers') or [] if isinstance(shot, dict)}
    flow = report.get('flow') or ('unified' if shots else 'verified')
    calls, told = {}, {}
    for event in ([] if shots else _log_events(Path(directory) / 'signal' / 'progress.jsonl')):
        if event.get('function') == 'UAI_SpectrometerDataOneshot' and str(event.get('stage', '')).startswith('sdk_call_'):
            calls.setdefault(event.get('frame'), {})[event['stage']] = event['at']
        elif event.get('stage') == 'frame_triggered':
            told[event.get('frame')] = event['at']
    frames, first, previous = [], None, None
    for number in sorted(number for number in set(shots) | set(told) if type(number) is int):
        shot, call = shots.get(number, {}), calls.get(number, {})
        try:
            begun = datetime.fromisoformat(shot['edge_utc']) if 'edge_utc' in shot else told.get(number)
        except (ValueError, TypeError):
            begun = told.get(number)
        row = {'frame': number, 'lead_ms': shot.get('exposure_before_pulse_ms'), 'exposure_ms': shot.get('exposure_ms'),
               'period_ms': None if begun is None or previous is None
               else round((begun - previous).total_seconds() * 1000)}
        if row['exposure_ms'] is None and {'sdk_call_start', 'sdk_call_done'} <= set(call):
            row['exposure_ms'] = round((call['sdk_call_done'] - call['sdk_call_start']).total_seconds() * 1000, 1)
        frames.append(row)
        first, previous = first or begun, begun or previous
    timing = {'flow': flow, 'wait_s': None, 'frames': frames}
    try:
        if first is not None:
            origin = datetime.fromisoformat(received_at or report['started_at'])
            timing['wait_s'] = round((first - origin).total_seconds(), 2)
    except (ValueError, TypeError, KeyError):
        pass
    leads = [row['lead_ms'] for row in frames if isinstance(row['lead_ms'], (int, float))]
    if leads:
        timing['lead_ms'] = [min(leads), max(leads)]
    marks = report.get('timeline_s')
    if isinstance(marks, dict):
        timing['steps_s'] = marks
    return timing


def ready_problems(snapshot, age, flow='unified'):
    """Why a plasma_run cannot start now, as {'reason', 'fix'} rows; empty when ready.

    A convenience for people; plasma_job.py makes the same checks itself before any output.
    A set voltage on the supply that is not the one of parameter.json is a reason only in the verified
    fall-back flow: plasma_measure.py writes it before it switches anything on (the person, 2026-10-07).
    """
    problems = []

    def add(reason, fix):
        problems.append({'reason': reason, 'fix': fix})

    snapshot = snapshot or {}
    power, nano, parameter = (snapshot.get(key) or {} for key in ('power', 'nano', 'parameter'))
    if age is None or age > 20:
        add('Status exceeds 20 Not updated in seconds', 'Click ”Read Status”; if it still fails, please check RS485 USB and RPi')
        return problems
    if power.get('valid') is not True:
        add('HSPY Power readback is invalid:' + str(power.get('error', ''))[:120], 'examine HSPY Is the power on?RS485 Wiring and Addressing 1')
    else:
        if power.get('run_stop_setting') != 0:
            add('HSPY power supply is ON', 'Press ”HSPY OFF＋Nano STOP”')
        elif power.get('voltage_v') != 0 or power.get('current_a') != 0:
            add('HSPY The output has not yet returned to zero (%s V/%s A) ' % (power.get('voltage_v'), power.get('current_a')),
                'Wait for the voltage to drop to 0 and then click ”Read Status”')
    if nano.get('valid') is not True:
        add('Nano Invalid readback:' + str(nano.get('error', ''))[:120], 'examine Nano powered by, RS485 address 2 and firmware 1.2.1')
    else:
        if nano.get('firmware') not in ('1.2.0', '1.2.1'):
            add('Nano firmware %s no 1.2.x pulse version' % nano.get('firmware'),
                'Bundle Nano Remove it and just connect it USB,Double-click on the computer Flash-Nano.cmd Burn 1.2.1 firmware (illustrate“step 1”) ')
        if nano.get('outputs_allowed') is not True:
            add('Nano The firmware is an export-prohibited version.',
                'Bundle Nano Remove it and just connect it USB,Double-click on the computer Flash-Nano.cmd Burn 1.2.1 pulse version firmware (explanation of ”steps” 1”) ')
        if nano.get('state') == 'FAULT' or nano.get('fault') != 'NONE':
            add('Nano fault lock %s' % nano.get('fault'), 'Click Clear Nano fault”, then click ”Synchronize Nano parameter”')
        elif nano.get('state') not in ('IDLE', 'DONE'):
            add('Nano status is %s' % nano.get('state'), 'Press ”HSPY OFF＋Nano STOP”')
        if nano.get('output_command_high') is not False or nano.get('output_pin_sample') is not False:
            add('Nano D13 pulse output is not LOW', 'Press ”HSPY OFF＋Nano STOP”')
    if parameter.get('valid') is not True:
        add('RPi of parameter.json is invalid:' + str(parameter.get('error', ''))[:120], 'Re-save it in ”Plasma Parameters”')
    else:
        if not 1 <= parameter.get('voltage_v', 0) <= MAX_VOLTAGE:
            add('Set voltage %s V Not here 1–%d V' % (parameter.get('voltage_v'), MAX_VOLTAGE), 'Save after setting the voltage in ”Plasma Parameters”')
        elif (flow != 'unified' and power.get('valid') is True
              and parameter.get('voltage_v') != power.get('set_voltage_v')):
            add('parameter.json Voltage %s V and HSPY set value %s V inconsistent'
                % (parameter.get('voltage_v'), power.get('set_voltage_v')), 're-save it in ”Plasma Parameters” (will write HSPY set value) ')
        try:
            check_parameters({'Ontime': parameter['on_time_ms'], 'Offtime': parameter['off_time_ms'],
                              'Cycle': parameter['cycles'], 'Loopnum': parameter['loops'],
                              'Integration_Time': parameter['integration_ms'],
                              'Spectra_Interval': parameter['spectra_interval_ms'],
                              'Voltage': max(1, min(MAX_VOLTAGE, int(parameter.get('voltage_v') or 1)))})
        except (ValueError, KeyError, TypeError) as exc:
            add('Plasma parameters do not meet the limits:' + str(exc), 'Save after correction of ”Plasma Parameters”')
    return problems


# ---------------------------------------------------------------- Read working folder

def _read_frame(path, pixels=None):
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError('Spectral file does not exist or is too large:' + path.name)
    axis, values = [], []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        rows = csv.reader(stream)
        if next(rows, None) != ['wavelength_nm', 'intensity_raw']:
            raise ValueError('The spectrum file field is incorrect:' + path.name)
        for row in rows:
            if len(row) != 2:
                raise ValueError('The spectrum file format is incorrect:' + path.name)
            x, y = float(row[0]), float(row[1])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('spectrum file contains non-numeric values:' + path.name)
            axis.append(x)
            values.append(y)
            if len(axis) > 8192:
                raise ValueError('Spectral file is too long: ' + path.name)
    if len(axis) < 2 or (pixels is not None and len(axis) != pixels):
        raise ValueError('The number of spectral pixels does not match:' + path.name)
    return axis, values


def _read_group(directory, count, pixels, keep_frames):
    axis, sums, frames = None, None, []
    for index in range(1, count + 1):
        frame_axis, values = _read_frame(directory / f'spectrum-{index}.csv', pixels)
        if axis is None:
            axis, sums = frame_axis, [0.0] * len(values)
        elif len(frame_axis) != len(axis) or any(abs(a - b) > .001 for a, b in zip(frame_axis, axis)):
            raise ValueError('The spectrum wavelength axis is inconsistent:' + directory.name)
        for pixel, value in enumerate(values):
            sums[pixel] += value
        if keep_frames:
            frames.append(values)
    return axis, [value / count for value in sums], frames


def _frame_count(info, group):
    metadata = info.get('frames') or []
    count = len(metadata)
    if (info.get('valid') is not True or not 1 <= count <= 1000
            or [item.get('file') for item in metadata] != [f'spectrum-{i}.csv' for i in range(1, count + 1)]):
        raise ValueError(group + ' Spectral record is incomplete')
    return count


def load_plasma_result(directory, with_frames=False):
    """Mean background, mean signal and their difference for one finished plasma_run folder."""
    job_path = directory / 'job.json'
    if not job_path.is_file():
        raise ValueError('not found job.json (The job is not completed or the folder is not a plasma job)')
    # The largest report a legal job writes (400 loops, a few hundred bytes for each) is well under 1 MB.
    if job_path.stat().st_size > 2_000_000:
        raise ValueError('job.json is too large to read')
    report = json.loads(job_path.read_text(encoding='utf-8-sig'))
    if report.get('valid') is not True:
        raise ValueError('This plasma job was not successfully completed:' + str(report.get('error', ''))[:200])
    background_info, signal_info = report.get('background') or {}, report.get('signal') or {}
    background_count = _frame_count(background_info, 'background')
    signal_count = _frame_count(signal_info, 'signal')
    pixels = signal_info.get('pixels')
    keep = with_frames and signal_count * (pixels or 4096) <= MAX_FRAME_VALUES
    axis, background, _ = _read_group(directory / 'background', background_count, background_info.get('pixels'), False)
    signal_axis, signal_mean, frames = _read_group(directory / 'signal', signal_count, pixels, keep)
    if len(signal_axis) != len(axis) or any(abs(a - b) > .001 for a, b in zip(signal_axis, axis)):
        raise ValueError('The wavelength axis of the background and the signal are inconsistent')
    result = {
        'kind': 'plasma', 'started_at': report.get('started_at'), 'finished_at': report.get('finished_at'),
        'flow': report.get('flow', 'verified'),
        'voltage_v': report.get('voltage_v'), 'model': signal_info.get('model'), 'serial': signal_info.get('serial'),
        'integration_ms': signal_info.get('integration_ms'), 'pixels': len(axis),
        'background_frames': background_count, 'signal_frames': signal_count,
        'steps': report.get('steps', []),
        'axis': [round(x, 3) for x in axis],
        'background': [round(v, 2) for v in background],
        'signal': [round(v, 2) for v in signal_mean],
        'difference': [round(s - b, 2) for s, b in zip(signal_mean, background)],
    }
    if with_frames:
        # Each single signal frame minus the mean background, for frame-by-frame review.
        result['frames'] = [[round(v - b, 2) for v, b in zip(frame, background)] for frame in frames] if keep else None
    return result


def load_capture_result(directory):
    """The three raw frames of one no-discharge spectrum capture."""
    report_path = directory / 'spectrum.json'
    report = json.loads(report_path.read_text(encoding='utf-8-sig')) if report_path.is_file() else {}
    count = _frame_count(report, 'capture')
    axis, mean, frames = _read_group(directory, count, report.get('pixels'), True)
    return {'kind': 'capture', 'started_at': report.get('started_utc'), 'finished_at': report.get('finished_utc'),
            'model': report.get('model'), 'serial': report.get('serial'),
            'integration_ms': report.get('integration_ms'), 'pixels': len(axis), 'signal_frames': count,
            'axis': [round(x, 3) for x in axis], 'mean': [round(v, 2) for v in mean],
            'frames': [[round(v, 2) for v in frame] for frame in frames]}


# ---------------------------------------------------------------- tool stroke

class Runner:
    """One tool run that another thread may stop — with one SIGTERM, and at most one more.

    The tools turn the first SIGTERM into their full OFF sequence and ignore later ones once that
    sequence has begun. A first signal can be lost, though: plasma_job.py's off-check reader takes
    the interrupt for a failed read and reads again (found 2026-10-06; that file is not changed).
    So when a discharge is still running REPEAT_TERM_SECONDS after the signal and nothing shows
    that it has begun to switch off (``repeat`` says so), the signal is sent once more — never a
    third time: a tool that takes two signals to heart is in its OFF sequence, which later ones
    must not disturb.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.term_sent = False
        self.term_mono = None
        self.term_repeated = False

    def terminate(self):
        with self.lock:
            if self.term_sent:
                return False
            self.term_sent = True
            self.term_mono = time.monotonic()
            if self.proc is not None and self.proc.poll() is None:
                self.proc.send_signal(signal.SIGTERM)
            return True

    def run(self, args, stdin=None, timeout=30, grace=10, tick=None, repeat=None):
        """Returns (exit code, stdout, stderr, timed_out, killed).

        ``repeat``: None for a tool that is never signalled twice; else a function that is asked,
        REPEAT_TERM_SECONDS after the SIGTERM, whether to send it once more (True unless the tool
        has shown that it is switching off).
        """
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace')
        with self.lock:
            self.proc = proc
            if self.term_sent:  # asked to stop before the process existed
                proc.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + timeout
        kill_at, timed_out, killed, first = None, False, False, True
        while True:
            try:
                out, err = proc.communicate(stdin if first else None, timeout=.25)
                break
            except subprocess.TimeoutExpired:
                first = False
            if tick:
                tick()
            current = time.monotonic()
            if current >= deadline and self.terminate():
                timed_out = True
            if self.term_sent and kill_at is None:
                kill_at = current + grace
            if (repeat is not None and self.term_sent and not self.term_repeated and not killed
                    and current - self.term_mono >= REPEAT_TERM_SECONDS):
                with self.lock:
                    self.term_repeated = True
                    if repeat() and proc.poll() is None:
                        proc.send_signal(signal.SIGTERM)
            if kill_at is not None and current >= kill_at and not killed:
                # Last resort after the grace period: the OFF sequence could not be confirmed.
                proc.kill()
                killed = True
        return proc.returncode, out, err, timed_out, killed


def _last_json(text):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                continue
    try:
        return json.loads(text)
    except ValueError:
        return None


def _hint(text):
    if 'Resource temporarily unavailable' in text or 'BlockingIOError' in text:
        return 'RS485 is being used by another program (such as an old HMI or manually executed tool), please close it first'
    if 'could not open port' in text or "No such file or directory: '/dev/" in text:
        return 'not found RS485 serial port: please confirm RS485 USB adapter is plugged in (ls -l /dev/RS485) '
    if "No module named 'serial'" in text:
        return 'Lack python3-serial: Please execute sudo apt install python3-serial'
    if 'Permission denied' in text:
        return 'Insufficient permissions: Please confirm RPi The login account belongs to dialout, plugdev, gpio group and reboot'
    return ''


class RealBackend:
    """Runs the field-verified RPi tools as separate processes, unchanged."""
    sim = False

    def __init__(self, config):
        self.app_dir = config['app_dir']
        self.tools_dir = config['tools_dir']
        self.serial_port = config['serial_port']
        self.python = sys.executable or 'python3'
        self.read_status = str(Path(__file__).resolve().parent / 'read_status.py')
        self.measure_tool = Path(__file__).resolve().parent / 'plasma_measure.py'
        self.lead_ms = config.get('exposure_lead_ms', DEFAULTS['exposure_lead_ms'])
        self.flow_errors = {}
        try:
            # Read and parsed here, by the interpreter that will run it: a missing or damaged file is
            # found when the service starts, not when somebody presses the button.
            compile(self.measure_tool.read_text(encoding='utf-8'), str(self.measure_tool), 'exec')
        except (OSError, ValueError, SyntaxError) as exc:
            missing = isinstance(exc, FileNotFoundError)
            self.flow_errors['unified'] = (
                'RPi superior%smeasurement program plasma_measure.py (%s) ' % ('not found' if missing else 'cannot be loaded', type(exc).__name__)
                + '.Please re-execute on the computer Install.cmd Bundle RPi end installed')

    def _finish(self, label, code, out, err, timed_out, killed):
        result = _last_json(out)
        if killed:
            raise RuntimeError(f'{label} has timed out and has not ended within the time limit, and has been forcibly terminated; the equipment status has not been confirmed, please use physical means to confirm that the high voltage has been turned off.')
        if isinstance(result, dict) and result.get('valid') is False:
            raise RuntimeError(str(result.get('error') or 'RPi tool report failed') + (' (timeout)' if timed_out else ''))
        if result is None or code != 0:
            detail = (err or out).strip()[-400:]
            hint = _hint(err + out)
            raise RuntimeError(f'{label} failed (end code {code}) ' + (': ' + hint if hint else ': ' + detail if detail else ''))
        return result

    def _tool(self, action, outdir=None, extra=(), stdin=None, timeout=40, grace=10, runner=None, tick=None,
              repeat=None):
        args = [self.python, '-B', str(Path(self.tools_dir) / 'plasma_job.py'), action,
                '--app-dir', self.app_dir, '--tools-dir', self.tools_dir, '--port', self.serial_port]
        if outdir:
            args += ['--outdir', str(outdir)]
        args += list(extra)
        return self._finish('plasma_job.py ' + action,
                            *(runner or Runner()).run(args, stdin=stdin, timeout=timeout, grace=grace, tick=tick,
                                                      repeat=repeat))

    def status(self):
        args = [self.python, '-B', self.read_status, self.app_dir, self.tools_dir, self.serial_port]
        result = self._finish('read status', *Runner().run(args, timeout=20))
        if not all(isinstance(result.get(key), dict) for key in ('power', 'nano')):
            raise RuntimeError('Status reply format is incorrect')
        return result

    def prepare(self):
        return self._tool('prepare')

    def stop(self):
        return self._tool('stop', timeout=60, grace=45)

    def clear_fault(self):
        return self._tool('clear_fault', extra=['--execute'])

    def write_set_voltage(self, voltage):
        """The supply's set voltage, written with the supply OFF and read back (plasma_measure.py --set-voltage).
        Whatever it was before: the verified tool writes nothing when it finds more than 700 V or a fraction."""
        if 'unified' in self.flow_errors:
            raise RuntimeError(self.flow_errors['unified'])
        args = [self.python, '-B', str(self.measure_tool), '--app-dir', self.app_dir, '--tools-dir', self.tools_dir,
                '--port', self.serial_port, '--set-voltage', str(int(voltage)), '--execute']
        return self._finish('plasma_measure.py --set-voltage', *Runner().run(args, timeout=30, grace=10))

    def set_parameters(self, parameters):
        # First the supply, then the file: the verified tool does both, but refuses when the set voltage it finds
        # on the supply is above 700 V or not a whole number (2026-10-07: 1000.0 V on the panel, and no way to
        # save anything). After write_set_voltage() it finds the new voltage there already and only writes the file.
        if 'unified' not in self.flow_errors:
            self.write_set_voltage(parameters['Voltage'])
        return self._tool('set_parameters', extra=['--execute'],
                          stdin=json.dumps(parameters, allow_nan=False), timeout=30)

    def spectrum(self, outdir, integration_ms, runner, tick):
        return self._tool('spectrum', outdir, extra=['--integration-ms', str(integration_ms)],
                          timeout=max(60, 3 * integration_ms / 1000 + 35), runner=runner, tick=tick)

    def plasma_run(self, outdir, runner, tick, flow='unified'):
        # 215 s is the limit the HMI has always used. After a SIGTERM the tool gets 60 s for its
        # OFF sequence (HSPY OFF readback, Nano STOP, wait for 0 V) before the last-resort kill.
        if flow == 'unified':
            if 'unified' in self.flow_errors:
                raise RuntimeError(self.flow_errors['unified'])
            args = [self.python, '-B', str(self.measure_tool), '--app-dir', self.app_dir, '--tools-dir', self.tools_dir,
                    '--port', self.serial_port, '--outdir', str(outdir), '--lead-ms', '%g' % self.lead_ms,
                    '--execute', '--confirm-high-voltage']

            def not_switching_off():
                # plasma_measure.py writes "off" into this file once its OFF sequence has told the
                # outputs to go off. Until then a stop that got lost may be sent once more.
                try:
                    return (Path(outdir) / 'stage').read_text(encoding='ascii').strip() != 'off'
                except (OSError, ValueError):
                    return True
            return self._finish('plasma_measure.py',
                                *runner.run(args, timeout=215, grace=60, tick=tick, repeat=not_switching_off))
        # plasma_job.py leaves no such sign; a second signal after its OFF sequence has begun is ignored by it.
        return self._tool('plasma_run', outdir, extra=['--execute', '--confirm-high-voltage'],
                          timeout=215, grace=60, runner=runner, tick=tick, repeat=lambda: True)

    def sim_fault(self, _fault):
        raise RuntimeError('Only simulation mode supports this command')

    def sim_sample(self, _sample):
        raise RuntimeError('Only simulation mode supports this command')


# ---------------------------------------------------------------- analog device (--sim) 

# Lines the pretend plasma always shows: (nm, width, height at full strength).
SIM_PEAKS = ((278.8, .45, 1400), (308.9, 1.2, 2500), (331.8, .5, 1200), (337.13, .6, 3000),
             (357.69, .6, 2200), (486.13, .5, 1600))
# Lines that grow with what the pretend sample holds: height per mg/L. They lie inside the windows
# of pc/analysis_default.json (the PC's concentration analysis), and the lines at 278.8 and 331.8 nm
# above inside its two shift windows, so that Correction and Trend can be tried in practice mode.
SIM_METAL_LINES = {'Zn': ((213.43, .30, 1800),),
                   'Ni': ((231.78, .30, 1040), (230.91, .30, 450), (341.32, .25, 600)),
                   'Cu': ((324.83, .30, 3600), (327.40, .35, 2200))}
SIM_SAMPLE = {'Cu': .5, 'Zn': .5, 'Ni': .5}  # what the pretend sample holds until sim_sample says otherwise
SIM_FULL_SCALE = 65535.0


class SimBackend:
    """A pretend HSPY + Nano + spectrometer with the same replies and files as the real tools."""
    sim = True
    flow_errors = {}  # the simulator plays both flows itself

    def __init__(self, data_dir, speed=1.0):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.speed = max(.1, float(speed))
        self.random = random.Random()
        self.parameter_path = self.dir / 'parameter.json'
        if not self.parameter_path.is_file():
            self._save({'Ontime': 25.0, 'Offtime': 5, 'Cycle': 25, 'Integration_Time': 1000,
                        'Spectra_Interval': 1500, 'Voltage': 100, 'Loopnum': 8, 'Metal': 'Pd'})
        self.set_voltage = self._load()['Voltage']
        self.run_stop = 0
        self.voltage = 0.0
        self.state, self.fault = 'IDLE', 'NONE'
        self.session = 0
        self.active = None
        self.pulses = self.loops = 0
        self.axis = [179.86 + i * (500.11 - 179.86) / 1879 for i in range(1880)]
        self.sample = dict(SIM_SAMPLE)

    def _load(self):
        return json.loads(self.parameter_path.read_text(encoding='utf-8'))

    def _save(self, value):
        self.parameter_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def _sleep(self, seconds, runner=None, tick=None):
        end = time.monotonic() + seconds / self.speed
        while time.monotonic() < end:
            if tick:
                tick()
            if runner is not None and runner.term_sent:
                raise InterruptedError
            time.sleep(min(.05, max(0, end - time.monotonic())))

    def _power(self):
        return {'valid': True, 'source': 'simulation', 'set_voltage_v': self.set_voltage, 'set_current_a': .1,
                'voltage_v': self.voltage, 'current_a': .02 if self.run_stop else 0, 'run_stop_setting': self.run_stop}

    def _nano(self):
        nano = {'valid': True, 'firmware': '1.2.1', 'protocol': 1, 'address': 2, 'outputs_allowed': True,
                'config_valid': self.active is not None, 'session_valid': bool(self.session),
                'heartbeat_fresh': False, 'state': self.state, 'fault': self.fault,
                'output_command_high': False, 'output_pin_sample': False, 'trigger_input': False,
                'command_result': 'OK', 'session_id': self.session, 'pulses_completed': self.pulses,
                'loops_completed': self.loops}
        if self.active is not None:
            nano['active_parameters'] = dict(self.active)
        if self.session:  # like read_status.py: the LCD's POWER line needs a synced session
            nano['lcd_power_report'] = {'power_snapshot': 'ON' if self.run_stop else 'OFF', 'source': 'simulation',
                                        'expires_after_ms': 5000, 'lcd_frozen': self.state == 'RUNNING'}
        return nano

    def status(self):
        time.sleep(.05)
        settings = self._load()
        return {'parameter': {'valid': True, 'voltage_v': int(settings['Voltage']),
                              'integration_ms': int(settings['Integration_Time']),
                              'on_time_ms': settings['Ontime'], 'off_time_ms': settings['Offtime'],
                              'cycles': settings['Cycle'], 'loops': settings['Loopnum'],
                              'spectra_interval_ms': settings['Spectra_Interval']},
                'power': self._power(), 'nano': self._nano(), 'received_at_unix_s': time.time()}

    def _require_off(self):
        if (self.run_stop or self.voltage or self.state not in ('IDLE', 'DONE') or self.fault != 'NONE'):
            raise RuntimeError('need HSPY OFF/output zero,Nano 1.2.x idle, no fault,D13 LOW')

    def prepare(self):
        self._require_off()
        settings = self._load()
        self.session = secrets.randbelow(0xffffffff) + 1
        self.state = 'IDLE'
        self.active = {'revision': 1, 'on_time_us': int(settings['Ontime'] * 1000),
                       'off_time_us': int(settings['Offtime'] * 1000), 'cycles': settings['Cycle'],
                       'loop_count': settings['Loopnum'],
                       'integration_time_us': int(settings['Integration_Time'] * 1000),
                       'spectra_interval_us': int(settings['Spectra_Interval'] * 1000),
                       'voltage_display_v': settings['Voltage']}
        return {'valid': True, 'firmware': '1.2.1', 'state': 'IDLE', 'parameters': dict(self.active)}

    def stop(self):
        self.run_stop, self.voltage = 0, 0.0
        if self.state != 'FAULT':
            self.state = 'IDLE'
        return {'nano': self._nano(), 'power': self._power()}

    def clear_fault(self):
        if self.run_stop or self.voltage:
            raise RuntimeError('Before clearing the fault, you need HSPY OFF and zero output')
        if self.state != 'FAULT' and self.fault == 'NONE':
            return {'valid': True, 'cleared': False, 'message': 'Nano No fault'}
        previous, self.state, self.fault, self.active = self.fault, 'IDLE', 'NONE', None
        self.session = secrets.randbelow(0xffffffff) + 1
        return {'valid': True, 'cleared': True, 'previous_fault': previous, 'nano_sync_required': True}

    def set_parameters(self, parameters):
        self._require_off()
        settings = self._load()
        settings.update(parameters)
        self._save(settings)
        self.set_voltage = parameters['Voltage']
        return {'valid': True, 'parameter': dict(parameters), 'nano_sync_required': True}

    def write_set_voltage(self, voltage):
        """As plasma_measure.py --set-voltage: the pretend supply takes the voltage while it is OFF."""
        self._require_off()
        previous, self.set_voltage = self.set_voltage, int(voltage)
        return {'valid': True, 'changed': previous != self.set_voltage, 'previous_set_voltage_v': previous,
                'set_voltage_v': self.set_voltage, 'hspy': self._power()}

    def sim_fault(self, fault):
        if fault not in ('HEARTBEAT_LOST', 'TRIGGER_TIMEOUT', 'JOB_TIMEOUT'):
            raise ValueError('fault Must be HEARTBEAT_LOST, TRIGGER_TIMEOUT or JOB_TIMEOUT')
        self.state, self.fault = 'FAULT', fault
        return {'injected': fault}

    def sim_sample(self, sample):
        """What the pretend sample holds, mg/L of Cu / Zn / Ni (a metal left out: 0). None: the default."""
        if sample is None:
            self.sample = dict(SIM_SAMPLE)
            return {'sample': dict(self.sample)}
        if not isinstance(sample, dict) or set(sample) - set(SIM_METAL_LINES):
            raise ValueError('sample Must be {"Cu": number, "Zn": number, "Ni": number} (mg/L) ')
        cleaned = {}
        for metal in SIM_METAL_LINES:
            value = sample.get(metal, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100000:
                raise ValueError(f'sample of {metal} Must be 0–100000 number (mg/L) ')
            cleaned[metal] = float(value)
        self.sample = cleaned
        return {'sample': dict(self.sample)}

    def _frame(self, directory, index, strength):
        directory.mkdir(parents=True, exist_ok=True)
        peaks = list(SIM_PEAKS) + [(center, width, height * self.sample[metal])
                                   for metal, lines in SIM_METAL_LINES.items()
                                   for center, width, height in lines if self.sample[metal]]
        values = []
        for x in self.axis:
            y = 700 + 30 * math.sin(x / 23) + self.random.uniform(-8, 8)
            if strength:
                y += strength * sum(height * math.exp(-((x - center) / width) ** 2)
                                    for center, width, height in peaks) * self.random.uniform(.95, 1.05)
            values.append(min(y, SIM_FULL_SCALE))
        name = f'spectrum-{index}.csv'
        with (directory / name).open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['wavelength_nm', 'intensity_raw'])
            writer.writerows(zip(self.axis, values))
        return {'file': name, 'elapsed_s': 0, 'minimum': min(values), 'maximum': max(values)}

    def _group(self, integration_ms, frames, triggered=False):
        return {'action': 'spectrum', 'valid': True, 'model': 'SIM-OES (simulation)', 'serial': 'SIM0001',
                'pixels': len(self.axis), 'integration_ms': integration_ms, 'frames_expected': len(frames),
                'triggered': triggered, 'frames': frames}

    def spectrum(self, outdir, integration_ms, runner, tick):
        self._require_off()
        outdir = Path(outdir)
        started = now_iso()
        try:
            frames = []
            for index in (1, 2, 3):
                self._sleep(integration_ms / 1000 + .3, runner, tick)
                frames.append(self._frame(outdir, index, 0))
        except InterruptedError:
            raise RuntimeError('The job received an interrupt signal 15') from None
        report = self._group(integration_ms, frames)
        report.update(started_utc=started, finished_utc=now_iso())
        (outdir / 'spectrum.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        return report

    def _log(self, directory, stage, **details):
        """One line of the spectrometer program's progress.jsonl, for job_timing()."""
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'utc': now_iso(), 'stage': stage, **details}) + '\n')

    def plasma_run(self, outdir, runner, tick, flow='unified'):
        """The steps of plasma_measure.py (unified) or plasma_job.py (verified), in their order and pace."""
        outdir = Path(outdir)
        unified = flow == 'unified'
        settings = self._load()
        voltage, loops = int(settings['Voltage']), int(settings['Loopnum'])
        integration_ms = int(settings['Integration_Time'])
        began = time.monotonic()
        report = {'valid': False, 'started_at': now_iso(), 'mode': 'simulation', 'flow': flow,
                  'voltage_v': voltage, 'output_dir': str(outdir), 'steps': [], 'signal_target_frames': loops,
                  'spectra_interval_ms': settings['Spectra_Interval']}
        steps = report['steps']
        pulse_ms = (float(settings['Ontime']) + float(settings['Offtime'])) * int(settings['Cycle'])
        period_ms = max(float(settings['Spectra_Interval']), pulse_ms)
        if unified:
            # As plasma_measure.py: the loops come at the interval, or at what the bus allows when that is longer.
            period_ms = loop_period_us(round(float(settings['Ontime']) * 1000), round(float(settings['Offtime']) * 1000),
                                       int(settings['Cycle']), int(settings['Spectra_Interval']) * 1000) / 1000
            report.update(background_frames=UNIFIED_BACKGROUND_FRAMES, timeline_s={}, triggers=[],
                          loop_period_ms=round(period_ms), exposure_lead_ms=5, hv_on_attempted=False,
                          off_confirmed=False)
            if period_ms > float(settings['Spectra_Interval']):
                steps.append('Spectral interval %d ms is shorter than possible, and the actual round %d ms' % (settings['Spectra_Interval'], round(period_ms)))

        def stage(name):  # plasma_measure.py tells the server where it is this way
            if unified:
                (outdir / 'stage').write_text(name, encoding='ascii')
                report['timeline_s'][name] = round((time.monotonic() - began) * self.speed, 3)
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            stage('prepare')
            self._require_off()
            if self.set_voltage != voltage:
                if not unified:          # plasma_job.py, the verified fall-back, ends here
                    raise RuntimeError('HSPY Set voltage and parameter.json Does not match')
                steps.append('HSPY The set voltage was originally %g V,Already in OFF The login password of %d V and read back' % (self.set_voltage, voltage))
                report['set_voltage_written'] = {'from_v': self.set_voltage, 'to_v': voltage}
                self.set_voltage = voltage
            held = dict(self.active) if self.active is not None and self.session else None
            session = self.session
            self.prepare()
            if unified and held == self.active:
                # The Nano already held these parameters: plasma_measure.py keeps its session, and only
                # the spectrometer has to open.
                self.session = session
                steps.append('Nano config checked, same as the configuration file')
                self._sleep(.6, runner, tick)
            else:
                steps.append('Nano config Read back')
                if unified:
                    self._sleep(1.0, runner, tick)
            if unified:
                # As plasma_measure.py: the Nano is armed while the spectrometer opens. The supply
                # comes on (no pulse) and reaches its voltage; only then the one background frame is
                # taken, and START is sent while it exposes. No pulse before that frame is finished.
                self.state = 'ARMED'
                self._sleep(.5, runner, tick)
                steps.append(f'spectrometer is ready (background {UNIFIED_BACKGROUND_FRAMES} open＋signal {loops} open) ')
                stage('ramp')
                report['hv_on_attempted'] = True
                self.run_stop = 1
                self._sleep(1.5, runner, tick)
                self.voltage = float(voltage)
                steps.append('HSPY ON,voltage has arrived (no pulse yet)')
                stage('background')
                steps.append('The background spectrum begins to be exposed (high voltage is on, but there is no pulse yet)')
                self._sleep(.3, runner, tick)
                self.state = 'WAIT_TRIGGER'
                steps.append('Nano WAIT_TRIGGER')
                self._sleep(max(integration_ms / 1000 + .03, .6) - .3, runner, tick)
                report['background'] = self._group(integration_ms, [self._frame(outdir / 'background', 1, 0)])
            else:
                frames = []
                for index in (1, 2, 3):
                    self._sleep(integration_ms / 1000 + .3, runner, tick)
                    frames.append(self._frame(outdir / 'background', index, 0))
                report['background'] = self._group(integration_ms, frames)
                steps.append('background spectrum completed')
                self.state = 'ARMED'
                self.run_stop = 1
                self._sleep(1.0, runner, tick)
                self.voltage = float(voltage)
                self.state = 'WAIT_TRIGGER'
                steps.append('HSPY ON, Nano WAIT_TRIGGER')
            stage('signal')
            strength = max(.05, min(2.0, (voltage - 50) / 350))
            self.pulses = self.loops = 0
            frames = []
            for index in range(1, loops + 1):
                self.state = 'RUNNING'
                if unified:  # the exposure is asked for first, D12 rises a few ms later
                    report['triggers'].append({'frame': index, 'edge_utc': now_iso(),
                                               'exposure_before_pulse_ms': round(self.random.uniform(5.0, 6.5), 1),
                                               'exposure_ms': integration_ms + self.random.choice((10.9, 21.6))})
                else:      # plasma_job.py triggers first; its spectrometer program hears of it afterwards
                    self._log(outdir / 'signal', 'frame_triggered', frame=index)
                self._sleep(period_ms / 1000, runner, tick)
                frames.append(self._frame(outdir / 'signal', index, strength))
                self.pulses += int(settings['Cycle'])
                self.loops = index
                self.state = 'WAIT_TRIGGER'
            report['signal'] = self._group(integration_ms, frames, triggered=True)
            steps.append('Nano DONE, Spectrum completed' if unified else 'Nano DONE, signal Spectrum completed')
        except InterruptedError:
            report['error'] = 'The job received an interrupt signal 15'
        except Exception as exc:
            report['error'] = str(exc)
        finally:
            # Like the real tools: always OFF, then wait for the readback to reach zero.
            stage('off')
            self.run_stop = 0
            self.state = 'FAULT' if self.fault != 'NONE' else ('DONE' if 'error' not in report else 'IDLE')
            time.sleep(min(1.0, .6 / self.speed))
            self.voltage = 0.0
            if unified:
                report.update(off_confirmed=True, spectrometer_ended=True, spectrometer_end='ended')
            report['cleanup_errors'] = []
            report['finished_at'] = now_iso()
            report['valid'] = 'error' not in report
            (outdir / 'job.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report.get('error'):
            raise RuntimeError(report['error'])
        return report


# ---------------------------------------------------------------- controller

class Connection:
    def __init__(self, ip):
        self.ip = ip
        self.alive = True
        self.authed = False
        self.last_seen = time.monotonic()


class Job:
    def __init__(self, kind, outdir, owner, flow='unified', received_at=None):
        self.id = Path(outdir).name
        self.kind = kind
        self.flow = flow
        self.received_at = received_at or now_iso()  # when the command arrived, before any check
        self.timing = None
        self.outdir = Path(outdir)
        self.owner = owner
        self.runner = Runner()
        self.state = 'running'
        self.started_at = now_iso()
        self.started_mono = time.monotonic()
        self.finished_at = None
        self.finished_mono = None
        self.error = ''
        self.cancel_reason = ''
        self.estimate_s = None
        self.voltage_v = None
        self.parameters = None
        self.frames_total = None
        self.steps = []
        self.post_stop = None
        self.finishing = False


class Controller:
    def __init__(self, config, backend):
        self.config = config
        self.backend = backend
        self.bus = threading.Lock()    # one user of RS485 / spectrometer at a time
        self.lock = threading.RLock()  # protects the fields below
        self.snapshot = None
        self.snapshot_mono = None
        self.snapshot_time = None
        self.status_error = ''
        self.job = None
        self.job_thread = None
        self.connections = 0
        self.last_activity = float('-inf')
        self.read_started = self.read_finished = float('-inf')
        self.shutdown = threading.Event()
        self.shutdown_mono = None
        self.output_dir = Path(config['output_dir'])
        self.audit_path = self.output_dir.parent / 'tcpd-audit.jsonl'
        # The one way a measurement is made here: what the settings file says, and nothing else. When
        # that way cannot be used (plasma_measure.py missing or damaged) every discharge is refused
        # with the reason; the other flow is never taken instead without anybody having chosen it.
        self.flow = config['flow']
        self.flow_error = backend.flow_errors.get(self.flow, '')
        self.stop_requests = 0  # counted under self.lock: a start that sees it change does not go on
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ---- record
    def audit(self, connection, command, ok, detail=''):
        record = {'time': now_iso(), 'ip': connection.ip if connection else '-', 'cmd': command,
                  'ok': ok, 'detail': str(detail)[:300]}
        try:
            print(json.dumps(record, ensure_ascii=False), flush=True)
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            if self.audit_path.is_file() and self.audit_path.stat().st_size > 2_000_000:
                self.audit_path.replace(self.audit_path.with_suffix('.jsonl.1'))
            with self.audit_path.open('a', encoding='utf-8') as out:
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
        except (OSError, ValueError):
            pass  # a full disk or a closed console must never fail a command

    # ---- state
    def _read_status(self):
        """Caller holds self.bus. Never raises: an error becomes status_error."""
        self.read_started = time.monotonic()
        try:
            snapshot = self.backend.status()
            with self.lock:
                self.snapshot, self.status_error = snapshot, ''
                self.snapshot_mono, self.snapshot_time = time.monotonic(), now_iso()
        except Exception as exc:
            with self.lock:
                self.snapshot, self.status_error = None, str(exc)[:400]
                self.snapshot_mono, self.snapshot_time = None, None
        finally:
            self.read_finished = time.monotonic()

    def _poll_loop(self):
        """Keep the snapshot fresh only while somebody is connected and asking.

        Reads are spaced poll_s apart from start to start, however long one read takes: every
        read also renews the POWER line on the Nano's LCD, which the firmware turns into "?"
        when 5 s pass without one. The bus is left free for at least 1 s between two reads.
        """
        while not self.shutdown.wait(.25):
            current = time.monotonic()
            with self.lock:
                wanted = current - self.last_activity < 15
            if (not wanted or current - self.read_started < self.config['poll_s']
                    or current - self.read_finished < 1):
                continue
            if self.bus.acquire(blocking=False):
                try:
                    self._read_status()
                finally:
                    self.bus.release()
                if self.status_error:
                    self.shutdown.wait(self.config['poll_s'])

    def _age(self):
        return None if self.snapshot_mono is None else time.monotonic() - self.snapshot_mono

    def running_job(self):
        with self.lock:
            return self.job if self.job is not None and self.job.state == 'running' else None

    def job_view(self, job):
        if job is None:
            return None
        end = job.finished_mono if job.finished_mono is not None else time.monotonic()
        view = {'id': job.id, 'kind': job.kind, 'state': job.state, 'high_voltage': job.kind == 'plasma',
                'flow': job.flow if job.kind == 'plasma' else None, 'timing': job.timing,
                'client': job.owner.ip, 'started_at': job.started_at, 'finished_at': job.finished_at,
                'elapsed_s': round(end - job.started_mono, 1), 'estimate_s': job.estimate_s,
                'voltage_v': job.voltage_v, 'error': job.error, 'cancel_reason': job.cancel_reason,
                'steps': job.steps, 'post_stop': job.post_stop, 'progress': self._progress(job)}
        return view

    def _progress(self, job):
        if job.state != 'running':
            return {'phase': job.state, 'frames_done': None, 'frames_total': job.frames_total}
        if job.runner.term_sent or job.finishing:
            return {'phase': 'stopping' if job.runner.term_sent else 'finishing',
                    'frames_done': None, 'frames_total': job.frames_total}
        try:
            if job.kind == 'capture':
                return {'phase': 'capture', 'frames_done': len(list(job.outdir.glob('spectrum-*.csv'))),
                        'frames_total': 3}
            if job.flow == 'unified':
                # plasma_measure.py says where it is. Its frames lie in one folder until the end; the
                # first of them is the background.
                stage = (job.outdir / 'stage').read_text(encoding='ascii').strip() if (job.outdir / 'stage').is_file() else ''
                phase = stage if stage in ('background', 'ramp', 'signal', 'off') else 'prepare'
                done = None
                if phase == 'signal':
                    done = (len(list((job.outdir / 'signal').glob('spectrum-*.csv'))) if (job.outdir / 'signal').is_dir()
                            else max(0, len(list((job.outdir / 'frames').glob('spectrum-*.csv'))) - UNIFIED_BACKGROUND_FRAMES))
                return {'phase': phase, 'frames_done': done, 'frames_total': job.frames_total}
            if (job.outdir / 'signal').is_dir():
                return {'phase': 'signal', 'frames_done': len(list((job.outdir / 'signal').glob('spectrum-*.csv'))),
                        'frames_total': job.frames_total}
            if (job.outdir / 'background').is_dir():
                return {'phase': 'background', 'frames_done': None, 'frames_total': job.frames_total}
        except OSError:
            pass
        return {'phase': 'prepare', 'frames_done': None, 'frames_total': job.frames_total}

    def status_view(self):
        with self.lock:
            age = self._age()
            snapshot = self.snapshot
            problems = ready_problems(snapshot, age, self.flow)
            if self.flow_error:
                problems.append({'reason': self.flow_error, 'fix': 'Double-click on the computer Install.cmd,Reinstall RPi end'})
            running = self.job is not None and self.job.state == 'running'
            parameter = (snapshot or {}).get('parameter') or {}
            return {'busy': running, 'job': self.job_view(self.job),
                    'age_s': None if age is None else round(age, 1), 'updated_at': self.snapshot_time,
                    'snapshot': snapshot, 'error': self.status_error,
                    'ready': {'plasma': not problems and not running, 'problems': problems},
                    'estimate_s': estimate_seconds(parameter, self.flow),
                    'start_s': estimate_start_seconds(parameter, self.flow),  # until the first pulse
                    'flow': self.flow, 'sim': self.backend.sim}

    # ---- General command (short time, synchronous reply)
    def _acquire_bus(self, wait=8):
        job = self.running_job()
        if job:
            raise Failure('busy', f'Work {job.id} is executing; please wait for the end, or send it first ” stop')
        if not self.bus.acquire(timeout=wait):
            raise Failure('busy', 'RS485 is executing other commands, please try again later.')

    def _with_bus(self, action):
        self._acquire_bus()
        try:
            try:
                return action()
            finally:
                self._read_status()
        except Failure:
            raise
        except ValueError as exc:
            raise Failure('bad_request', str(exc)) from exc
        except Exception as exc:
            raise Failure('tool_failed', str(exc)) from exc
        finally:
            self.bus.release()

    def refresh(self):
        if self.running_job() is None and self.bus.acquire(timeout=8):
            try:
                self._read_status()
            finally:
                self.bus.release()
        return self.status_view()

    def _stop_job(self, job):
        if job.runner.terminate():
            with self.lock:
                job.cancel_reason = job.cancel_reason or 'stop'
        return {'cancel_sent': True, 'job': job.id,
                'message': 'A stop signal has been sent to the job being executed, and the tool will complete it on its own HSPY OFF/Nano STOP'}

    def stop(self):
        with self.lock:
            # A discharge that is being started right now (bus taken, status being read, no job to
            # signal yet) sees this number change and does not go on: see start_job().
            self.stop_requests += 1
        job = self.running_job()
        if job:
            return self._stop_job(job)
        try:
            result = self._with_bus(self.backend.stop)
        except Failure as exc:
            # The bus was busy: a job may have got going while this stop waited for it.
            job = self.running_job() if exc.code == 'busy' else None
            if job is None:
                raise
            return self._stop_job(job)
        return {'cancel_sent': False, 'stopped': True, 'power': result.get('power'), 'nano': result.get('nano')}

    # ---- job (non-synchronous: reply the job number immediately and use it later job query)
    def permit_problems(self):
        """Interlock seam, checked right before every discharge.

        Nothing is checked today (the PLC and pumps are removed). When liquid handling returns,
        add its conditions here (flow established, lid closed, ...) so both the UI and any TCP
        client get the same refusal.
        """
        return []

    def start_job(self, connection, kind, integration_ms=100):
        received_at = now_iso()
        flow = self.flow
        if kind == 'plasma' and self.flow_error:
            raise Failure('not_ready', self.flow_error)
        with self.lock:
            stops = self.stop_requests
        self._acquire_bus()
        started = False
        try:
            age = self._age()
            # A measurement saves this second when the regular poll has just read everything:
            # plasma_measure.py makes the same checks itself before any output, like plasma_job.py.
            # Never older than 10 s, whatever poll_s is: ready_problems() refuses a status of 20 s.
            if not (kind == 'plasma' and flow == 'unified' and age is not None
                    and age <= min(self.config['poll_s'] + 1.5, 10)):
                self._read_status()
            view = self.status_view()
            snapshot = view['snapshot'] or {}
            if kind == 'plasma':
                problems = view['ready']['problems'] + self.permit_problems()
                if problems:
                    raise Failure('not_ready', 'Not ready:' + '; '.join(row['reason'] for row in problems))
            elif view['snapshot'] is None:
                raise Failure('not_ready', 'Unable to read device status:' + view['error'])
            name = ('HMI-' if kind == 'plasma' else 'CAP-') + uuid.uuid4().hex
            job = Job(kind, self.output_dir / name, connection, flow, received_at)
            if kind == 'plasma':
                parameter = snapshot['parameter']
                job.voltage_v = parameter['voltage_v']
                job.parameters = parameter
                job.frames_total = parameter['loops']
                job.estimate_s = estimate_seconds(parameter, flow)
            else:
                job.frames_total = 3
                job.estimate_s = int(3 * integration_ms / 1000 + 8)
            with self.lock:
                if self.stop_requests != stops:
                    # Somebody pressed stop while this was being started (the status read above can
                    # take a second): there was no job yet for that stop to signal. Do not go on.
                    raise Failure('cancelled', 'A stop command was received during startup and did not start.' + ('discharge' if kind == 'plasma' else 'capture'))
                self.job = job
            self.job_thread = threading.Thread(target=self._run_job, args=(job, integration_ms), daemon=True)
            self.job_thread.start()
            started = True
            return job
        finally:
            if not started:
                self.bus.release()

    def _tick(self, job):
        """Called every 0.25 s while the tool runs: decide whether to send the one SIGTERM."""
        reason = ''
        if self.shutdown.is_set():
            # systemd signals the tool too; only forward after 5 s, when a tool that did get the
            # signal is already in its OFF sequence (which ignores further signals).
            if time.monotonic() - self.shutdown_mono >= 5:
                reason = 'shutdown'
        elif job.kind == 'plasma':
            owner = job.owner
            if not owner.alive:
                reason = 'owner_disconnected'
            # Counted from the start of the job when that is later: the owner cannot say anything
            # while it waits for the answer to its plasma_run, and a start may take several seconds.
            elif time.monotonic() - max(owner.last_seen, job.started_mono) > self.config['watchdog_s']:
                reason = 'watchdog_timeout'
        if reason and job.runner.terminate():
            with self.lock:
                job.cancel_reason = job.cancel_reason or reason
            self.audit(None, 'watchdog', True, f'{job.id} {reason}')

    def _run_job(self, job, integration_ms):
        """Runs with self.bus held by start_job; releases it at the end.

        The job stays 'running' until the equipment state has been read back, so a client that
        sees done / failed / cancelled can act on it at once.
        """
        state, error, steps = 'failed', '', []
        try:
            tick = lambda: self._tick(job)  # noqa: E731
            if job.kind == 'plasma':
                steps = self.backend.plasma_run(job.outdir, job.runner, tick, job.flow).get('steps', [])
            else:
                self.backend.spectrum(job.outdir, integration_ms, job.runner, tick)
                steps = ['Spectrum acquisition completed (not discharged)']
            state = 'done'
        except Exception as exc:
            error = str(exc)[:600]
        try:
            timing = self._timing(job) if job.kind == 'plasma' else None
            with self.lock:
                job.finishing, job.steps, job.error, job.timing = True, steps, error, timing
            if job.kind == 'plasma' and state != 'done' and not self.shutdown.is_set():
                # The tool already ran its own OFF sequence; confirm once more with readback.
                try:
                    self.backend.stop()
                    post = {'ok': True, 'error': ''}
                except Exception as exc:
                    post = {'ok': False, 'error': str(exc)[:400]}
                with self.lock:
                    job.post_stop = post
            self._read_status()
        finally:
            with self.lock:
                job.state = 'cancelled' if state != 'done' and job.cancel_reason else state
                job.finished_at, job.finished_mono = now_iso(), time.monotonic()
            try:
                self._persist(job)
                self.audit(job.owner, 'job_end', state == 'done',
                           f'{job.id} {job.state} {job.cancel_reason} {job.error}'.strip())
            finally:
                self.bus.release()

    def _timing(self, job):
        """The short form of job_timing() for the job view; None when the tool left nothing to read."""
        try:
            report = json.loads((job.outdir / 'job.json').read_text(encoding='utf-8-sig'))
            timing = job_timing(job.outdir, report, job.received_at)
        except (OSError, ValueError, AttributeError):
            return None
        return {key: timing.get(key) for key in ('flow', 'wait_s', 'lead_ms')}

    def _persist(self, job):
        try:
            job.outdir.mkdir(parents=True, exist_ok=True)
            (job.outdir / 'tcpd.json').write_text(json.dumps({
                'id': job.id, 'kind': job.kind, 'state': job.state, 'error': job.error,
                'flow': job.flow, 'received_at': job.received_at,
                'cancel_reason': job.cancel_reason, 'started_at': job.started_at,
                'finished_at': job.finished_at, 'client': job.owner.ip, 'parameters': job.parameters,
                'post_stop': job.post_stop}, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass

    # ---- results
    def _job_dir(self, name):
        if name is None:
            with self.lock:
                if self.job is None:
                    raise Failure('not_found', 'Currently there is no work record; please specify job')
                name = self.job.id
        if not isinstance(name, str) or not JOB_ID.fullmatch(name):
            raise Failure('bad_request', 'job format is incorrect (example:HMI-＋32 hexadecimal characters)')
        running = self.running_job()
        if running and running.id == name:
            raise Failure('busy', 'This work is still in progress')
        directory = self.output_dir / name
        if not directory.is_dir():
            raise Failure('not_found', 'The work folder cannot be found ' + name)
        return name, directory

    def result(self, name, with_frames):
        name, directory = self._job_dir(name)
        try:
            data = load_capture_result(directory) if name.startswith('CAP-') else load_plasma_result(directory, with_frames)
        except (ValueError, OSError, KeyError) as exc:
            raise Failure('not_found', str(exc)) from exc
        data['job'] = name
        try:
            record = json.loads((directory / 'tcpd.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            record = {}
        data['parameters'] = record.get('parameters')
        if data['kind'] == 'plasma':
            try:
                report = json.loads((directory / 'job.json').read_text(encoding='utf-8-sig'))
                data['timing'] = job_timing(directory, report, record.get('received_at'))
            except (OSError, ValueError, AttributeError):
                data['timing'] = None
        return data

    def jobs(self, limit=50):
        rows = []
        try:
            entries = list(self.output_dir.iterdir()) if self.output_dir.is_dir() else []
        except OSError:
            entries = []
        for path in entries:
            if not JOB_ID.fullmatch(path.name) or not path.is_dir():
                continue
            row = {'job': path.name, 'kind': 'capture' if path.name.startswith('CAP-') else 'plasma',
                   'modified': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
            try:
                source = path / ('spectrum.json' if row['kind'] == 'capture' else 'job.json')
                report = json.loads(source.read_text(encoding='utf-8-sig'))
                signal_info = report if row['kind'] == 'capture' else (report.get('signal') or {})
                row.update(valid=report.get('valid') is True, voltage_v=report.get('voltage_v'),
                           started_at=report.get('started_at') or report.get('started_utc'),
                           frames=len(signal_info.get('frames') or []))
            except (OSError, ValueError):
                row.update(valid=False, frames=0)
            rows.append(row)
        rows.sort(key=lambda row: row['modified'], reverse=True)
        return rows[:limit]

    # ---- instruction dispatch
    def identity(self):
        """Who this server is. Also the whole reply to a discovery broadcast: nothing secret."""
        return {'server': 'plasmic-tcpd', 'version': VERSION, 'protocol': PROTOCOL, 'sim': self.backend.sim,
                'name': socket.gethostname()[:64], 'port': self.config['port']}

    def hello(self, connection):
        return {**self.identity(),
                'auth_required': bool(self.config['token']), 'authed': connection.authed or not self.config['token'],
                'watchdog_s': self.config['watchdog_s'], 'time': now_iso(),
                'flow': self.flow, 'flow_error': self.flow_error,
                'limits': {'max_voltage_v': MAX_VOLTAGE, 'max_job_s': 215}}

    def handle(self, connection, request):
        command = request.get('cmd')
        if not isinstance(command, str):
            raise Failure('bad_request', 'Lack cmd,For example {"cmd":"status"}')
        command = command.lower()
        required = self.config['token']
        if required and ('token' in request or command == 'auth'):
            token = request.get('token')
            if not isinstance(token, str) or not hmac.compare_digest(token.encode(), required.encode()):
                self.audit(connection, command, False, 'token mistake')
                raise Failure('auth_failed', 'token error (RPi superior tcpd folder config.json has the correct token) ')
            connection.authed = True
        if command not in OPEN_COMMANDS and required and not connection.authed:
            raise Failure('auth_required', 'This command requires token: first delivery {"cmd":"auth","token":"..."},or add to each instruction "token"')
        if command == 'hello':
            return self.hello(connection)
        if command == 'auth':
            return {'authed': True}
        if command == 'ping':
            return {'time': now_iso()}
        if command == 'status':
            with self.lock:
                never_read = self.snapshot is None and not self.status_error
            # The first question after a quiet period reads the equipment right away.
            return self.refresh() if request.get('fresh') is True or never_read else self.status_view()
        if command == 'job':
            if request.get('job') is not None:
                with self.lock:
                    if self.job is None or self.job.id != request['job']:
                        raise Failure('not_found', 'The server only remembers the latest job; please use the older job jobs/result')
            with self.lock:
                return {'job': self.job_view(self.job)}
        if command == 'jobs':
            return {'jobs': self.jobs()}
        if command == 'result':
            return self.result(request.get('job'), request.get('frames') is True)
        if command == 'stop':
            try:
                result = self.stop()
            except Failure as exc:
                self.audit(connection, 'stop', False, str(exc))  # a stop that did not get through is on record too
                raise
            self.audit(connection, 'stop', True, result.get('job') or 'idle')
            return result
        if command == 'prepare':
            result = self._with_bus(self.backend.prepare)
            self.audit(connection, 'prepare', True)
            return {'prepared': True, 'parameters': result.get('parameters')}
        if command == 'clear_fault':
            result = self._with_bus(self.backend.clear_fault)
            self.audit(connection, 'clear_fault', True, result.get('previous_fault', ''))
            return {'cleared': result.get('cleared') is True, 'previous_fault': result.get('previous_fault'),
                    'nano_sync_required': result.get('nano_sync_required', False),
                    'message': result.get('message', '')}
        if command == 'sync_voltage':
            # The supply's set voltage becomes the voltage of parameter.json (supply OFF, read back). For a client
            # that has seen the two differ; the measurement does the same by itself before it switches anything on.
            voltage = ((self.status_view().get('snapshot') or {}).get('parameter') or {}).get('voltage_v')
            if type(voltage) is not int or not 1 <= voltage <= MAX_VOLTAGE:
                raise Failure('not_ready', 'parameter.json The voltage cannot be read or is not in the 1–%d V' % MAX_VOLTAGE)
            result = self._with_bus(lambda: self.backend.write_set_voltage(voltage))
            self.audit(connection, 'sync_voltage', True, '%s -> %s' % (result.get('previous_set_voltage_v'), voltage))
            return {'changed': result.get('changed') is True, 'set_voltage_v': voltage,
                    'previous_set_voltage_v': result.get('previous_set_voltage_v')}
        if command == 'set_parameters':
            try:
                parameters = check_parameters(request.get('parameters'))
            except ValueError as exc:
                raise Failure('bad_request', str(exc)) from exc
            self._with_bus(lambda: self.backend.set_parameters(parameters))
            self.audit(connection, 'set_parameters', True, json.dumps(parameters))
            return {'saved': True, 'parameters': parameters, 'nano_sync_required': True}
        if command == 'spectrum':
            integration = request.get('integration_ms', 100)
            if type(integration) is not int or not 1 <= integration <= 10000:
                raise Failure('bad_request', 'integration_ms Must be 1–10000 integer')
            job = self.start_job(connection, 'capture', integration)
            self.audit(connection, 'spectrum', True, job.id)
            return {'job': job.id, 'estimate_s': job.estimate_s}
        if command == 'plasma_run':
            if request.get('confirm_high_voltage') is not True:
                raise Failure('bad_request', 'Discharge will output high voltage: the command must "confirm_high_voltage": true')
            if 'sequence' in request:
                # Written for the 1.3.0 server, which let the sender choose an order. Refused rather
                # than measured in a way the sender did not ask for.
                raise Failure('bad_request', 'The computer program is an old version (Still choosing“fast/original”). Please update the computer program and try again.')
            job = self.start_job(connection, 'plasma')
            self.audit(connection, 'plasma_run', True, f'{job.id} {job.voltage_v} V {job.flow}')
            return {'job': job.id, 'voltage_v': job.voltage_v, 'estimate_s': job.estimate_s,
                    'flow': job.flow, 'watchdog_s': self.config['watchdog_s']}
        if command == 'sim_fault':
            try:
                return self._with_bus(lambda: self.backend.sim_fault(request.get('fault', 'HEARTBEAT_LOST')))
            except Failure as exc:
                raise Failure('bad_request', str(exc)) from exc
        if command == 'sim_sample':
            try:
                return self._with_bus(lambda: self.backend.sim_sample(request.get('sample')))
            except Failure as exc:
                raise Failure('bad_request', str(exc)) from exc
        raise Failure('unknown_command', 'Unrecognized command:' + command[:40])

    def dispatch(self, connection, text):
        request_id = None
        try:
            if text.startswith('{'):
                try:
                    request = json.loads(text)
                except ValueError:
                    raise Failure('bad_request', 'not legal JSON') from None
                if not isinstance(request, dict):
                    raise Failure('bad_request', 'need JSON object')
            else:
                request = {'cmd': text.split()[0]}  # abbreviation: send only one word, for example ping, stop
            request_id = request.get('id')
            response = {'ok': True}
            response.update(self.handle(connection, request))
        except Failure as exc:
            response = {'ok': False, 'code': exc.code, 'error': str(exc)}
        except Exception as exc:  # never let one bad request take the server down
            response = {'ok': False, 'code': 'internal', 'error': f'{type(exc).__name__}: {exc}'[:300]}
        if request_id is not None:
            response['id'] = request_id
        return response

    def begin_shutdown(self):
        self.shutdown_mono = time.monotonic()
        self.shutdown.set()

    def wait_for_job(self, seconds=80):
        thread = self.job_thread
        if thread is not None and thread.is_alive():
            thread.join(seconds)


# ---------------------------------------------------------------- Network

class Handler(socketserver.StreamRequestHandler):
    def _send(self, value):
        self.wfile.write((json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                          + '\n').encode('utf-8'))
        self.wfile.flush()

    def _turn_away(self, value):
        """Say why, then wait (a second at most) for the other side to hang up first.

        Its first line is usually still on the way. Hanging up before that line has been read
        resets the connection, and Windows then drops our answer before the program there
        has read it: the person would see "connection lost" instead of the reason.
        """
        try:
            self._send(value)
            self.request.shutdown(socket.SHUT_WR)
            self.request.settimeout(1.0)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and self.request.recv(4096):
                pass
        except OSError:
            pass  # it has gone already, or stayed silent for the whole second

    def handle(self):
        controller = self.server.controller
        ip = self.client_address[0]
        connection = Connection(ip)
        if not allowed(ip, controller.config['allow']):
            controller.audit(connection, 'connect', False, 'source IP Not here allow List')
            self._turn_away({'ok': False, 'code': 'forbidden',
                             'error': f'This computer’s IP {ip} Not here RPi ( Analysis window oftcpd folder config.json of allow) '})
            return
        with controller.lock:
            full = controller.connections >= MAX_CONNECTIONS
            if not full:
                controller.connections += 1
        if full:  # answered outside the lock: _turn_away waits for the other side
            self._turn_away({'ok': False, 'code': 'busy', 'error': f'The number of connections has reached the upper limit. {MAX_CONNECTIONS}'})
            return
        try:
            self.request.settimeout(IDLE_SECONDS)
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            while not controller.shutdown.is_set():
                line = self.rfile.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    self._send({'ok': False, 'code': 'bad_request', 'error': 'command is too long'})
                    break
                connection.last_seen = time.monotonic()
                with controller.lock:
                    controller.last_activity = connection.last_seen
                text = line.decode('utf-8', 'replace').strip()
                if text:
                    self._send(controller.dispatch(connection, text))
        except (OSError, ValueError):
            pass  # peer went away or idle timeout
        finally:
            connection.alive = False
            with controller.lock:
                controller.connections -= 1


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # Linux: restart without waiting for old sockets. Windows: SO_REUSEADDR would let two
    # servers share the port silently, so ask for exclusive use there instead.
    allow_reuse_address = sys.platform != 'win32'

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Discovery(threading.Thread):
    """Answers the "automatic search RPi" broadcast, so a computer can find this server without its IP.

    One UDP datagram in, one out. The reply only says who the server is (what ``hello`` says
    before any token); nothing can be commanded over UDP. With ``bind`` set to one address
    instead of 0.0.0.0, only questions sent straight to that address are answered.
    """

    def __init__(self, controller):
        super().__init__(daemon=True)
        self.controller = controller
        bind, port = controller.config['bind'], controller.config['port']
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.socket.bind(('' if bind == '0.0.0.0' else bind, port))
            self.socket.settimeout(.5)
        except OSError:
            self.socket.close()
            raise

    def run(self):
        controller = self.controller
        window, sent = 0.0, 0
        while not controller.shutdown.is_set():
            try:
                data, address = self.socket.recvfrom(128)
            except socket.timeout:
                continue
            except OSError:
                # Windows reports an earlier reply that nobody was listening for here.
                time.sleep(.05)
                continue
            if data.strip() != DISCOVER_REQUEST:
                continue
            current = time.monotonic()
            if current - window >= 1:
                window, sent = current, 0
            if sent >= 20:
                continue  # a forged sender address must never turn this into a flood
            sent += 1
            reply = dict(controller.identity(), allowed=allowed(address[0], controller.config['allow']))
            try:
                self.socket.sendto(json.dumps(reply, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
                                   address)
            except OSError:
                pass
        self.socket.close()


def main():
    parser = argparse.ArgumentParser(description='Plasmic Electronic control TCP server')
    parser.add_argument('--config', help='config.json path (if omitted, the built-in default value will be used)')
    parser.add_argument('--sim', action='store_true', help='simulates the device without touching any hardware')
    parser.add_argument('--sim-dir', default=str(Path(__file__).resolve().parent / 'sim-data'))
    parser.add_argument('--sim-speed', type=float, default=1.0, help='Simulation time acceleration rate')
    parser.add_argument('--bind')
    parser.add_argument('--port', type=int)
    parser.add_argument('--token')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:  # the log lines are Chinese; never depend on the console's code page
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    overrides = {key: value for key, value in (('bind', args.bind), ('port', args.port), ('token', args.token))
                 if value is not None}
    if args.sim:
        overrides['output_dir'] = str(Path(args.sim_dir) / 'captures')
    config = load_config(args.config, overrides)
    if args.sim:
        backend = SimBackend(args.sim_dir, args.sim_speed)
    elif sys.platform != 'linux':
        raise SystemExit('real machine mode can only be used in RPi (Linux) ; please add Manually execute the tool on --sim')
    else:
        backend = RealBackend(config)
        if not (Path(config['tools_dir']) / 'plasma_job.py').is_file():
            raise SystemExit('not found ' + config['tools_dir'] + '/plasma_job.py; Please execute first install_rpi.sh')
    controller = Controller(config, backend)
    try:
        server = Server((config['bind'], config['port']), Handler)
    except OSError as exc:
        raise SystemExit(f'cannot be used {config["bind"]}:{config["port"]} ({exc}) ; Is there another server running?')
    server.controller = controller
    discovery = 'Closed'
    if config['discovery']:
        try:
            Discovery(controller).start()
            discovery = 'Enabled'
        except OSError as exc:  # never a reason to refuse TCP service
            discovery = f'is not enabled (UDP port {config["port"]} cannot be used:{exc}) '

    def on_signal(_signum, _frame):
        controller.begin_shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)
    print(f'plasmic-tcpd {VERSION} monitor {config["bind"]}:{config["port"]}'
          f'{" (simulation mode)" if args.sim else ""}; token {"Already set" if config["token"] else "Not set"}; '
          f'allowed {", ".join(config["allow"])}; automatic search {discovery}', flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        controller.begin_shutdown()
        controller.wait_for_job()
        server.server_close()


if __name__ == '__main__':
    main()
