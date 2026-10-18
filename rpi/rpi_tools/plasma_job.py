#!/usr/bin/env python3
"""Explicit RPi commissioning commands for Nano 1.2.x and HSPY.

Default is read only. The plasma command needs --execute and --confirm-high-voltage
(the HMI confirms high voltage with the operator first). It keeps the serial bus in one process so heartbeat,
power readback and STOP can be ordered while the spectrometer runs separately.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import struct
import subprocess
import sys
import time

from nano_verify import Client, ProtocolError, crc16, encode_parameters, frame, open_port
from pulse_prepare import prepare

OFF_SETTLE_SECONDS = 20
CLEANUP_ATTEMPTS = 5
# Measured on site 2026-09-29: after a Nano 40-register status reply the HSPY does not
# answer a request sent within 10 ms (4/4 timeouts) but always answers after 30 ms.
# HSPY_Read_Status only waits 6 ms, so every HSPY transaction first leaves this gap.
HSPY_BUS_QUIET_SECONDS = .08
# Site maximum HSPY set voltage, confirmed by the user on 2026-09-30 after checking the
# electrode circuit and fluid path. Production plasma is enabled up to this value.
MAX_VOLTAGE = 700


def voltage_window(target):
    """Ramp deadline (s) and accepted deviation (V) for HSPY to reach ``target`` V."""
    return 3 + target / 100, max(5.0, target * .02)


def hspy_turnaround(sleep=time.sleep):
    sleep(HSPY_BUS_QUIET_SECONDS)


class Interrupts:
    """SIGTERM/SIGINT/SIGHUP handling that never splits an RS485 transaction.

    A signal raised in the middle of a Modbus exchange leaves the slave's reply on
    the bus, and the next request (HSPY OFF, Nano STOP) then reads those bytes and
    fails its CRC/address check. So a signal during a transaction is deferred until
    the transaction completes, and once cleanup has started further signals are
    recorded but ignored, so a repeated cancel can never abort the OFF sequence.
    """
    def __init__(self):
        self.busy = 0
        self.pending = None
        self.cleaning = False
        self.received = []

    def handler(self, signum, _frame):
        self.received.append(signum)
        if self.cleaning:
            return
        if self.busy:
            self.pending = signum
            return
        raise RuntimeError('The job received an interrupt signal %d' % signum)

    @contextmanager
    def bus(self):
        self.busy += 1
        try:
            yield
        finally:
            self.busy -= 1
        if not self.busy and self.pending is not None and not self.cleaning:
            signum, self.pending = self.pending, None
            raise RuntimeError('The job received an interrupt signal %d' % signum)


INTERRUPTS = Interrupts()


class GuardedClient(Client):
    """Nano client whose every transaction is atomic with respect to cancel signals."""
    def transaction(self, body, response_size):
        with INTERRUPTS.bus():
            return super().transaction(body, response_size)


def drain_bus(port, quiet=.05, limit=.6, clock=time.monotonic, sleep=time.sleep):
    """Discard late replies until the bus has been silent for `quiet` seconds."""
    deadline = clock() + limit
    last_byte = clock()
    while clock() < deadline:
        if port.in_waiting:
            port.reset_input_buffer()
            last_byte = clock()
        elif clock() - last_byte >= quiet:
            return
        sleep(.01)


def retry(label, action, drain, errors, attempts=CLEANUP_ATTEMPTS, pause=.2, sleep=time.sleep):
    """Run a cleanup action until it succeeds; record the last error if every attempt fails."""
    last = None
    for _ in range(attempts):
        try:
            drain()
            return action()
        except Exception as exc:
            last = exc
            sleep(pause)
    errors.append(f'{label} (Try again {attempts} times):{last}')
    return None


def stop_outputs(power, power_write, nano, drain, trigger=None):
    """D12 LOW, HSPY OFF confirmed by readback, Nano STOP; each retried on a quiet bus."""
    errors = []
    if trigger is not None:
        retry('D12 LOW', trigger.low, lambda: None, errors)

    def hspy_off():
        power_write(4, 0)
        readback = power()
        if readback.get('valid') is not True or readback.get('run_stop_setting') != 0:
            raise RuntimeError(f'HSPY Run/Stop read back {readback.get("run_stop_setting")}')
        return readback
    retry('HSPY OFF', hspy_off, drain, errors)
    retry('Nano STOP', nano.stop, drain, errors)
    return errors


def clear_fault(nano, power):
    """Clear a latched Nano fault (HEARTBEAT_LOST/TRIGGER_TIMEOUT/JOB_TIMEOUT) with HSPY OFF.

    STOP and a new session keep FAULT by design; CLEAR_FAULT needs a fresh lease,
    which OPEN_SESSION provides. The new session drops the active config, so the
    parameters must be synchronized again afterwards.
    """
    p = power()
    if (p.get('valid') is not True or p.get('run_stop_setting') != 0
            or p.get('voltage_v') != 0 or p.get('current_a') != 0):
        raise RuntimeError('Before clearing the fault, you need HSPY OFF and zero output')
    before = nano.status()
    if before['state'] in ('ARMED', 'WAIT_TRIGGER', 'RUNNING'):
        raise RuntimeError('Nano The work is in progress, please STOP')
    if before['state'] != 'FAULT' and before['fault'] == 'NONE':
        return {'valid': True, 'cleared': False, 'nano': before, 'message': 'Nano No fault'}
    session = secrets.randbelow(0xffffffff) + 1
    while session == before['session_id']:
        session = secrets.randbelow(0xffffffff) + 1
    nano.write(0x200, struct.pack('>IIHII', session, 1, 1, 0, 0))
    opened = nano.status()
    if opened['session_id'] != session or opened['last_request_id'] != 1 or opened['command_result'] != 'OK':
        raise ProtocolError('Nano session failed to start, the fault has not been cleared')
    after = command(nano, 6)
    if (after['state'] != 'IDLE' or after['fault'] != 'NONE'
            or after['output_command_high'] or after['output_pin_sample']):
        raise ProtocolError('CLEAR_FAULT back Nano has not returned IDLE/No fault/D13 LOW')
    return {'valid': True, 'cleared': True, 'previous_fault': before['fault'],
            'nano': after, 'nano_sync_required': True}


def wait_power_zero(power, timeout_seconds=OFF_SETTLE_SECONDS,
                    clock=time.monotonic, sleep=time.sleep):
    """Wait for HSPY's own OFF and zero-output readback; never assume FC06 is enough."""
    deadline = clock() + timeout_seconds
    started = clock()
    samples = []
    last_power = None
    while True:
        try:
            last_power = power()
            samples.append({'elapsed_s': round(clock() - started, 2),
                            'valid': last_power.get('valid'),
                            'run_stop_setting': last_power.get('run_stop_setting'),
                            'voltage_v': last_power.get('voltage_v'),
                            'current_a': last_power.get('current_a')})
            if last_power.get('valid') is not True or last_power.get('run_stop_setting') != 0:
                return last_power, samples, False
            if (last_power.get('valid') is True and last_power.get('run_stop_setting') == 0
                    and last_power.get('voltage_v') == 0 and last_power.get('current_a') == 0):
                return last_power, samples, True
        except Exception as exc:
            samples.append({'elapsed_s': round(clock() - started, 2), 'error': str(exc)})
            return last_power, samples, False
        remaining = deadline - clock()
        if remaining <= 0:
            return last_power, samples, False
        sleep(min(.5, remaining))


def checked_power_write(port, register, value):
    if not (register == 4 and value in (0, 1) or register == 0 and type(value) is int and 0 <= value <= MAX_VOLTAGE):
        raise ValueError('This tool only allows HSPY Run/Stop 0/1 with reviewed OFF voltage setting')
    request = frame(struct.pack('>BBHH', 1, 6, register, value))
    old_timeout = port.timeout
    with INTERRUPTS.bus():
        hspy_turnaround()
        _power_write_exchange(port, request, old_timeout)


def _power_write_exchange(port, request, old_timeout):
    try:
        port.reset_input_buffer()
        time.sleep(.006)
        if port.write(request) != len(request):
            raise IOError('HSPY The command was not sent completely')
        response = bytearray()
        deadline = time.monotonic() + 1
        length = 8
        while len(response) < length and time.monotonic() < deadline:
            port.timeout = min(.05, max(.001, deadline - time.monotonic()))
            response.extend(port.read(length - len(response)))
            if len(response) >= 2 and response[1] & 0x80:
                length = 5
        if bytes(response) != request or crc16(response):
            raise IOError('HSPY FC06 Reply/CRC Incorrect')
    finally:
        port.timeout = old_timeout


def off_check_reader(power, drain, attempts=3, pause=.2, sleep=time.sleep, log=None):
    """HSPY readback for checks made while HSPY is required to be OFF.

    FC03 has no side effects, so a missed/garbled reply is retried on a drained
    bus. Only transport failures are retried; a valid readback is returned as is
    and the caller still rejects ON or non-zero output. Never use after HSPY ON.
    """
    def read():
        for attempt in range(1, attempts + 1):
            try:
                return power()
            except Exception as exc:
                if log is not None:
                    log.append({'attempt': attempt, 'error': str(exc)})
                if attempt == attempts:
                    raise
                sleep(pause)
                drain()
    return read


def require_off(power, nano):
    p, n = power(), nano.status()
    if (not p['valid'] or p['run_stop_setting'] != 0 or p['voltage_v'] != 0
            or p['current_a'] != 0 or n['firmware'] not in ('1.2.0', '1.2.1')
            or not n['outputs_allowed'] or n['state'] not in ('IDLE', 'DONE')
            or n['fault'] != 'NONE' or n['output_command_high'] or n['output_pin_sample']):
        raise RuntimeError('need HSPY OFF/output zero,Nano 1.2.x idle, no fault,D13 LOW')
    return p, n


def set_voltage_off(nano, power, power_write, target):
    if type(target) is not int or not 1 <= target <= MAX_VOLTAGE:
        raise ValueError(f'HSPY The set voltage must be 1–{MAX_VOLTAGE} V integer')
    before, _ = require_off(power, nano)
    previous = before['set_voltage_v']
    if (type(previous) not in (int, float) or previous != int(previous)
            or not 0 <= previous <= MAX_VOLTAGE):
        raise RuntimeError('HSPY The original setting value is not within the expected range and has not been written.')
    previous = int(previous)
    if previous == target:
        return {'valid': True, 'changed': False, 'hspy': before}
    try:
        power_write(0, target)
        after = power()
        if (after.get('valid') is not True or after.get('set_voltage_v') != target
                or after.get('run_stop_setting') != 0 or after.get('voltage_v') != 0
                or after.get('current_a') != 0):
            raise RuntimeError('HSPY After setting OFF/Zero output readback failed')
        return {'valid': True, 'changed': True, 'hspy': after}
    except Exception as exc:
        recovery = []
        for register, value in ((4, 0), (0, previous)):
            try:
                power_write(register, value)
            except Exception as rollback_error:
                recovery.append(str(rollback_error))
        if recovery:
            raise RuntimeError('Setting failed and the reply is not confirmed:' + str(exc) + '; ' + '; '.join(recovery)) from exc
        raise


PARAMETER_FIELDS = ('Ontime', 'Offtime', 'Cycle', 'Loopnum',
                    'Integration_Time', 'Spectra_Interval', 'Voltage')


def validate_new_parameters(raw, old):
    if not isinstance(raw, dict) or set(raw) != set(PARAMETER_FIELDS):
        raise ValueError('The plasma parameter field is incomplete or contains unknown fields.')
    if any(type(raw[name]) not in (int, float) for name in PARAMETER_FIELDS):
        raise ValueError('Plasma parameters must be numbers')
    candidate = old.copy()
    candidate.update(raw)
    encode_parameters(candidate)
    voltage = candidate['Voltage']
    if type(voltage) is not int or not 1 <= voltage <= MAX_VOLTAGE:
        raise ValueError(f'voltage must be 1–{MAX_VOLTAGE} V integer')
    return candidate


def save_parameters_off(path, raw, old, nano, power, power_write):
    """Update HSPY setpoint and parameter.json only with verified OFF/zero output."""
    candidate = validate_new_parameters(raw, old)
    before, _ = require_off(power, nano)
    target = candidate['Voltage']
    voltage_changed = before['set_voltage_v'] != target
    if voltage_changed:
        set_voltage_off(nano, power, power_write, target)
    temporary = path.with_name(path.name + '.new-' + secrets.token_hex(8))
    try:
        temporary.write_text(json.dumps(candidate, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                             encoding='utf-8')
        os.replace(temporary, path)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if voltage_changed:
            previous = before['set_voltage_v']
            try:
                power_write(4, 0)
                power_write(0, previous)
                restored = power()
                if (restored.get('valid') is not True or restored.get('set_voltage_v') != previous
                        or restored.get('run_stop_setting') != 0 or restored.get('voltage_v') != 0
                        or restored.get('current_a') != 0):
                    raise RuntimeError('The reply voltage readback does not match')
            except Exception as rollback_error:
                raise RuntimeError('Parameter file storage failed and HSPY reply unconfirmed:' + str(rollback_error)) from exc
        raise
    after = power()
    if (after.get('valid') is not True or after.get('set_voltage_v') != target
            or after.get('run_stop_setting') != 0 or after.get('voltage_v') != 0
            or after.get('current_a') != 0):
        raise RuntimeError('parameter has been written, but HSPY final OFF/Set value readback failed')
    return {'valid': True, 'parameter': {name: candidate[name] for name in PARAMETER_FIELDS},
            'hspy': after, 'nano_sync_required': True}


def command(nano, opcode, job=0, revision=0, expected='OK'):
    before = nano.status()
    request = before['last_request_id'] + 1
    if not before['session_id'] or request > 0xffffffff:
        raise RuntimeError('Nano session/serial number is invalid')
    nano.write(0x200, struct.pack('>IIHII', before['session_id'], request, opcode, job, revision))
    after = nano.status()
    if (after['session_id'] != before['session_id'] or after['last_request_id'] != request
            or after['command_result'] != expected):
        raise ProtocolError(f'Nano command {opcode} Not confirmed:{after["command_result"]}')
    return after


def heartbeat(nano):
    return command(nano, 2)


class Trigger:
    """UNO220 expander pin 3, using the existing sysfs low-default mapping."""
    def __init__(self, root=Path('/sys/class/gpio')):
        chips = [path.parent for path in root.glob('gpiochip*/label')
                 if path.read_text().strip() == '1-0027']
        if len(chips) != 1 or int((chips[0] / 'ngpio').read_text()) != 8:
            raise RuntimeError('UNO220 GPIO chip 1-0027 not uniquely identified')
        number = int((chips[0] / 'base').read_text()) + 3
        self.pin = root / ('gpio' + str(number))
        if not self.pin.exists():
            (root / 'export').write_text(str(number))
        deadline = time.monotonic() + 1
        while not (self.pin / 'direction').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if (self.pin / 'active_low').read_text().strip() != '0':
            raise RuntimeError('UNO220 pin 3 Polarity does not match')
        (self.pin / 'direction').write_text('low\n')
        self.low()

    def low(self):
        (self.pin / 'value').write_text('0\n')
        if (self.pin / 'value').read_text().strip() != '0':
            raise RuntimeError('D12 trigger LOW readback failed')

    def pulse(self):
        self.low()
        time.sleep(.02)
        (self.pin / 'value').write_text('1\n')
        if (self.pin / 'value').read_text().strip() != '1':
            raise RuntimeError('D12 trigger HIGH readback failed')
        time.sleep(.01)
        self.low()


def start_spectrum(app_dir, tools_dir, output_dir, integration_ms=100, sdk_settle_ms=500,
                   frames=3, frame_interval_ms=0, triggered=False):
    output_dir.mkdir(parents=True, exist_ok=True)
    args = ['flock', '-n', '-E', '75', '/tmp/hm-hmi-spectrometer.lock', sys.executable, '-B',
         str(tools_dir / 'hardware_verify.py'), 'spectrum', '--app-dir', str(app_dir),
         '--outdir', str(output_dir), '--integration-ms', str(integration_ms),
         '--sdk-settle-ms', str(sdk_settle_ms), '--frames', str(frames),
         '--frame-interval-ms', str(frame_interval_ms)]
    if triggered:
        args.append('--triggered')
    child = subprocess.Popen(
        args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, text=True)
    child.spectrum_output_dir = output_dir
    return child


def spectrum_last_stage(child):
    path = getattr(child, 'spectrum_output_dir', None)
    if path is None:
        return 'No stage record'
    progress = path / 'progress.jsonl'
    try:
        lines = progress.read_text(encoding='utf-8').splitlines()
        last = json.loads(lines[-1]) if lines else {}
        stage = last.get('stage', 'unknown')
        function = last.get('function')
        frame_number = last.get('frame')
        return f'{stage} {function or ""} frame={frame_number or "—"}'.strip()
    except (OSError, ValueError):
        return 'No stage record'


def collect_spectrum(child, deadline, expected_frames=3):
    try:
        output, errors = child.communicate(timeout=max(.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate()
        raise TimeoutError('spectrum SDK timeout; final stage:' + spectrum_last_stage(child)) from exc
    if child.returncode or not output.strip():
        raise RuntimeError('Spectrum acquisition failed:' + (errors or output)[-1000:])
    report = json.loads(output)
    if (report.get('valid') is not True or report.get('frames_expected') != expected_frames
            or [frame.get('file') for frame in report.get('frames', [])] !=
            [f'spectrum-{i}.csv' for i in range(1, expected_frames + 1)]):
        raise RuntimeError('Spectrum capture report is incomplete')
    return report


def frame_ready(child, number):
    return (child.spectrum_output_dir / f'frame-{number}.ready').is_file()


def release_frame(child, number):
    marker = child.spectrum_output_dir / f'frame-{number}.go'
    with marker.open('x', encoding='ascii') as stream:
        stream.write(str(number))


def wait_first_frame_ready(child, nano, deadline, clock=time.monotonic):
    """Prime the SDK before HSPY ON, keeping Nano's command lease alive."""
    next_heartbeat = clock()
    while clock() < deadline:
        if frame_ready(child, 1):
            return
        if child.poll() is not None:
            raise RuntimeError('signal spectrum subroutine exits when preparing the first frame:' + spectrum_last_stage(child))
        if clock() >= next_heartbeat:
            heartbeat(nano)
            next_heartbeat = clock() + .5
        time.sleep(.02)
    raise TimeoutError('Signal spectrum SDK exist HSPY ON Not prepared before: ' + spectrum_last_stage(child))


def run_plasma(nano, power, power_write, trigger, parameters,
                        app_dir, tools_dir, output_dir, clock=time.monotonic, drain=lambda: None):
    """One bounded pulse/spectrum job; always try to stop Nano and HSPY."""
    expected_voltage = int(parameters['Voltage'])
    if not 1 <= expected_voltage <= MAX_VOLTAGE:
        raise ValueError(f'Voltage Must be 1–{MAX_VOLTAGE} V')
    data = encode_parameters(parameters)
    integration_ms = int(parameters['Integration_Time'])
    if not 1 <= integration_ms <= 10000:
        raise ValueError('Integration time limit 1–10000 ms')
    loops = int(parameters['Loopnum'])
    interval_ms = parameters['Spectra_Interval']
    if type(interval_ms) not in (int, float) or interval_ms != int(interval_ms):
        raise ValueError('Spectral interval must be an integer millisecond')
    interval_ms = int(interval_ms)
    pulse_loop_seconds = (float(parameters['Ontime']) + float(parameters['Offtime'])) * int(parameters['Cycle']) / 1000
    expected_signal_seconds = loops * max(interval_ms / 1000, pulse_loop_seconds)
    if expected_signal_seconds > 100:
        raise ValueError('Number of rounds/Spectral interval exceeded Nano 120 seconds work deadline; Please shorten the interval or reduce the number of rounds')
    report = {'valid': False, 'started_at': datetime.now(timezone.utc).isoformat(),
              'mode': 'production', 'voltage_v': expected_voltage,
              'output_dir': str(output_dir), 'steps': [],
              'signal_target_frames': loops,
              'spectra_interval_ms': interval_ms, 'power_read_retries': []}
    # Pre-ON checks only; HSPY ON readback and cleanup keep the unretried reader.
    off_power = off_check_reader(power, drain, log=report['power_read_retries'])
    output_dir.mkdir(parents=True, exist_ok=True)
    pid_file = output_dir / 'job.pid'
    pid_file.write_text(str(os.getpid()), encoding='ascii')
    child = None
    try:
        trigger.low()
        initial, _ = require_off(off_power, nano)
        if (initial['set_voltage_v'] != expected_voltage or initial['set_current_a'] > .1):
            raise RuntimeError('HSPY Set voltage/current limit and Except parameter.json Does not match')
        prepared = prepare(nano, off_power, data)
        revision = prepared['parameters']['revision']
        report['steps'].append('Nano config Read back')
        background = start_spectrum(app_dir, tools_dir, output_dir / 'background', integration_ms)
        report['background'] = collect_spectrum(background, clock() + 40)
        report['steps'].append('background spectrum completed')
        heartbeat(nano)
        job_id = secrets.randbelow(0xffffffff) + 1
        armed = command(nano, 3, job_id, revision)
        if armed['state'] != 'ARMED' or armed['output_pin_sample']:
            raise RuntimeError('Nano ARM Incorrect readback')
        child = start_spectrum(app_dir, tools_dir, output_dir / 'signal', integration_ms,
                               frames=loops, frame_interval_ms=interval_ms, triggered=True)
        wait_first_frame_ready(child, nano, clock() + 10, clock)
        report['steps'].append(f'Signal spectrum SDK Prepared {loops} frame')
        power_write(4, 1)
        ramp_seconds, tolerance = voltage_window(expected_voltage)
        voltage_deadline = clock() + ramp_seconds
        while True:
            powered = power()
            measured = powered.get('voltage_v')
            if (powered.get('valid') is not True or powered.get('run_stop_setting') != 1
                    or powered.get('set_voltage_v') != expected_voltage
                    or powered.get('current_a', 1) > .11):
                raise RuntimeError('HSPY ON The readback is incorrect or the current exceeds the limit')
            if isinstance(measured, (int, float)) and abs(measured - expected_voltage) <= tolerance:
                break
            if clock() >= voltage_deadline:
                raise RuntimeError(f'HSPY {ramp_seconds:.0f} The set voltage has not been reached within seconds. {expected_voltage} V (allowed ±{tolerance:.0f} V) ,last reading {measured} V')
            heartbeat(nano)
            time.sleep(.15)
        nano.report_power(1)
        heartbeat(nano)
        started = command(nano, 4, job_id, revision)
        if started['state'] != 'WAIT_TRIGGER':
            raise RuntimeError('Nano START not entered WAIT_TRIGGER')
        report['steps'].append('HSPY ON, Nano WAIT_TRIGGER')
        signal_deadline = clock() + max(40, expected_signal_seconds + 15)
        job_deadline = clock() + 123
        next_heartbeat = clock()
        completed_loops = 0
        triggered_loops = 0
        while clock() < job_deadline:
            now = clock()
            if now >= next_heartbeat:
                heartbeat(nano)
                next_heartbeat = now + .5
            status = nano.status()
            if status['fault'] != 'NONE' or status['state'] == 'FAULT':
                raise RuntimeError('Nano Fault: ' + status['fault'])
            if (status['state'] == 'WAIT_TRIGGER' and status['loops_completed'] == triggered_loops
                    and triggered_loops < loops and frame_ready(child, triggered_loops + 1)):
                trigger.pulse()
                triggered_loops += 1
                release_frame(child, triggered_loops)
            elif status['loops_completed'] > completed_loops:
                completed_loops = status['loops_completed']
            if child.poll() is not None and triggered_loops < loops:
                raise RuntimeError('signal spectrum ends before all rounds are triggered:' + spectrum_last_stage(child))
            if clock() >= signal_deadline:
                raise TimeoutError('Spectrum acquisition exceeds the period set by the number of rounds; final stage:' + spectrum_last_stage(child))
            if status['state'] == 'DONE':
                if (status['pulses_completed'] != int(parameters['Cycle']) * int(parameters['Loopnum'])
                        or status['loops_completed'] != int(parameters['Loopnum'])
                        or status['output_pin_sample'] or triggered_loops != loops):
                    raise RuntimeError('Nano DONE pulse/Loop readback is incomplete')
                report['nano_done'] = status
                break
            time.sleep(.05)
        else:
            raise TimeoutError('Nano works over 123 Second')
        report['signal'] = collect_spectrum(child, signal_deadline, loops)
        report['steps'].append('Nano DONE, signal Spectrum completed')
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        # From here on, cancel signals are recorded but cannot interrupt the OFF sequence.
        INTERRUPTS.cleaning = True
        cleanup_errors = []
        if child and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.communicate(timeout=2)
            except Exception as exc:
                cleanup_errors.append('Spectrum work stops:' + str(exc))
        cleanup_errors += stop_outputs(power, power_write, nano, drain, trigger)
        try:
            drain()
            final_power, off_samples, power_zero = wait_power_zero(power)
            report['off_settle_samples'] = off_samples
            final_nano = nano.status()
            report['final_power'] = final_power
            report['final_nano'] = final_nano
            if (not power_zero or final_nano.get('valid') is not True
                    or final_nano.get('output_command_high') is not False
                    or final_nano.get('output_pin_sample') is not False):
                voltage = None if final_power is None else final_power.get('voltage_v')
                run_stop = None if final_power is None else final_power.get('run_stop_setting')
                cleanup_errors.append(f'final OFF/D13 LOW Readback failed:HSPY Run/Stop={run_stop}, '
                                      f'output={voltage} V; Nano D13={final_nano.get("output_pin_sample")}; '
                                      f'Already waiting {off_samples[-1]["elapsed_s"]} Second')
        except Exception as exc:
            cleanup_errors.append('Final readback:' + str(exc))
        report['cleanup_errors'] = cleanup_errors
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        pid_file.unlink(missing_ok=True)
        (output_dir / 'job.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    if report['cleanup_errors']:
        raise RuntimeError('Work stop not confirmed:' + '; '.join(report['cleanup_errors']))
    if report.get('error'):
        raise RuntimeError(report['error'])
    report['valid'] = True
    (output_dir / 'job.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return report


def cancel_job(output_dir):
    pid_file = output_dir / 'job.pid'
    if not pid_file.is_file():
        return {'valid': True, 'cancel_sent': False, 'message': 'There is no plasma job in progress.'}
    pid = int(pid_file.read_text(encoding='ascii').strip())
    command_line = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
    if 'plasma_job.py' not in command_line or 'plasma_run' not in command_line or str(output_dir) not in command_line:
        raise RuntimeError('PID is inconsistent with plasma operation and no stop signal is sent.')
    os.kill(pid, signal.SIGTERM)
    return {'valid': True, 'cancel_sent': True, 'pid': pid}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'prepare', 'stop', 'spectrum', 'plasma_run',
                                           'set_voltage_off', 'set_parameters', 'cancel', 'clear_fault'))
    parser.add_argument('--app-dir', type=Path, default=Path.home() / 'Desktop/Plasmic/app')
    parser.add_argument('--tools-dir', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--port', default='/dev/RS485')
    parser.add_argument('--outdir', type=Path, default=Path.home() / 'Desktop/Plasmic/logs/lite-flow')
    parser.add_argument('--integration-ms', type=int, default=100,
                        help='spectrum action only; 1–10000 ms')
    parser.add_argument('--sdk-settle-ms', type=int, default=500,
                        help='spectrum only; wait after opening SDK device (0–2000 ms; default 500)')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-high-voltage', action='store_true')
    parser.add_argument('--confirm-hv-load-disconnected', action='store_true',
                        help='older HMI releases; same meaning as --confirm-high-voltage')
    args = parser.parse_args()
    if args.action == 'plasma_run' and not (args.execute and (args.confirm_high_voltage or args.confirm_hv_load_disconnected)):
        parser.error('plasma_run requires --execute and --confirm-high-voltage')
    if args.action in ('set_voltage_off', 'set_parameters', 'clear_fault') and not args.execute:
        parser.error(args.action + ' requires --execute')
    if args.action == 'spectrum' and not 1 <= args.integration_ms <= 10000:
        parser.error('spectrum integration must be 1–10000 ms')
    if args.action == 'spectrum' and not 0 <= args.sdk_settle_ms <= 2000:
        parser.error('spectrum SDK settle must be 0–2000 ms')
    if args.action == 'cancel':
        print(json.dumps(cancel_job(args.outdir), ensure_ascii=False))
        return 0
    sys.path.insert(0, str(args.app_dir))
    from HSPY_Read_Status import read_status
    parameter_path = args.app_dir / 'parameter.json'
    parameters = (json.loads(parameter_path.read_text(encoding='utf-8-sig'))
                  if args.action in ('prepare', 'plasma_run', 'set_voltage_off', 'set_parameters') else None)
    new_parameters = None
    if args.action == 'set_parameters':
        source = sys.stdin.read(4097)
        if len(source) > 4096:
            raise ValueError('Parameter data is too large')
        new_parameters = json.loads(source)
        validate_new_parameters(new_parameters, parameters)
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), INTERRUPTS.handler)
    result = None
    with open('/tmp/hm-hmi-rs485.lock', 'w') as serial_lock:
      fcntl.flock(serial_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      with open_port(args.port, 1.0) as port:
        nano = GuardedClient(port)

        def power():
            with INTERRUPTS.bus():
                hspy_turnaround()
                return read_status(port, address=1, timeout=1.0)

        def drain():
            drain_bus(port)
        if args.action == 'status':
            result = {}
            for name, call in (('power', power), ('nano', nano.status)):
                try:
                    result[name] = call()
                except Exception as exc:
                    result[name] = {'valid': False, 'error': str(exc)}
        elif args.action == 'prepare':
            result = prepare(nano, off_check_reader(power, drain), encode_parameters(parameters))
        elif args.action == 'set_voltage_off':
            encode_parameters(parameters)
            result = set_voltage_off(nano, power,
                                     lambda register, value: checked_power_write(port, register, value),
                                     int(parameters['Voltage']))
        elif args.action == 'set_parameters':
            result = save_parameters_off(parameter_path, new_parameters, parameters, nano, power,
                                         lambda register, value: checked_power_write(port, register, value))
        elif args.action == 'stop':
            INTERRUPTS.cleaning = True
            errors = stop_outputs(power, lambda register, value: checked_power_write(port, register, value),
                                  nano, drain)
            drain()
            result = {'nano': nano.status(), 'power': power()}
            if (not result['power']['valid'] or result['power']['run_stop_setting'] != 0
                    or result['power']['voltage_v'] != 0 or result['power']['current_a'] != 0):
                errors.append('HSPY OFF readback failed')
            if errors:
                raise RuntimeError('; '.join(errors))
        elif args.action == 'clear_fault':
            drain()
            result = clear_fault(nano, power)
        elif args.action == 'spectrum':
            require_off(off_check_reader(power, drain), nano)
            child = start_spectrum(args.app_dir, args.tools_dir, args.outdir,
                                   args.integration_ms, args.sdk_settle_ms)
            result = collect_spectrum(child, time.monotonic() + max(40, 3 * args.integration_ms / 1000 + 15))
        else:
            result = run_plasma(nano, power,
                                         lambda register, value: checked_power_write(port, register, value),
                                         Trigger(), parameters, args.app_dir, args.tools_dir, args.outdir,
                                         drain=drain)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


run_isolated_plasma = run_plasma  # name used by earlier tests and tools

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'valid': False, 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)

