#!/usr/bin/env python3
"""Bounded commissioning tools. No Nano ARM/START or GPIO HIGH commands.

power explicitly energizes an unloaded supply, at 5/10/25 V and a 10 mA limit.
Always attempts OFF and zero setpoint in finally; a lost bus cannot prove OFF.
display only reads HSPY and sends display snapshots to Nano 1.1 (no power writes).
Keep the old controller service stopped: this process must own the RS485 bus.
"""
import argparse
import csv
import ctypes as ct
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import struct
import sys
import time

from nano_verify import Client, crc16, encode_parameters, frame, open_port

MAX_TEST_VOLTAGE = 25
TEST_CURRENT_RAW = 100  # Site profile: 100 / 10000 A = 10 mA current LIMIT.


def load_hspy(app_dir):
    sys.path.insert(0, str(app_dir.resolve()))
    from HSPY_Read_Status import read_status
    return read_status


class Power:
    def __init__(self, port, reader):
        self.port, self.reader = port, reader

    def read(self):
        return self.reader(self.port, address=1, timeout=1.0)

    def write(self, register, value):
        # Every physical write passes this allowlist, including failure cleanup.
        if type(value) is not int or not (
                register == 0 and 0 <= value <= MAX_TEST_VOLTAGE or
                register == 1 and value == TEST_CURRENT_RAW or
                register == 4 and value in (0, 1)):
            raise ValueError('Write outside commissioning allowlist (maximum 25 V)')
        request = frame(struct.pack('>BBHH', 1, 6, register, value))
        old_timeout = self.port.timeout
        try:
            self.port.reset_input_buffer()
            time.sleep(0.006)
            if self.port.write(request) != len(request):
                raise IOError('Incomplete HSPY write')
            data = bytearray()
            deadline = time.monotonic() + 1.0
            length = 8
            while len(data) < length and time.monotonic() < deadline:
                self.port.timeout = min(0.05, max(0.001, deadline - time.monotonic()))
                data.extend(self.port.read(length - len(data)))
                if len(data) >= 2 and data[1] & 0x80:
                    length = 5
            if bytes(data) != request or crc16(data):
                raise IOError('HSPY FC06 echo missing, invalid or exception: ' + data.hex())
        finally:
            self.port.timeout = old_timeout


def require_locked(nano):
    status = nano.status()
    if (status['outputs_allowed'] or status['state'] not in ('IDLE', 'DONE')
            or status['output_command_high'] or status['output_pin_sample']):
        raise RuntimeError('Test requires Nano output lock, IDLE/DONE and D13 LOW')
    return status


def power_test(power, nano, report, sleep=time.sleep):
    """Physical test, not a calibration. All measurements are HSPY internal values."""
    report.update(valid=False, steps=[], maximum_set_voltage_v=25,
                  current_limit_a=0.010, voltage_acceptance_v=1.0,
                  measurement_source='HSPY internal readback; no independent meter')
    status = require_locked(nano)  # Refuse before any power writes.
    report['nano_before'] = status
    lcd_supported = status['firmware'] == '1.1.0' and bool(status['session_id'])

    def snapshot(s):
        if lcd_supported:
            return nano.report_power(s['run_stop_setting'])
        return {'available': False, 'reason': 'LCD firmware 1.1.0 not installed/synced'}

    try:
        report['before'] = power.read()
        power.write(4, 0)
        if power.read()['run_stop_setting'] != 0:
            raise RuntimeError('Initial OFF not confirmed')
        power.write(0, 0)
        power.write(1, TEST_CURRENT_RAW)
        for voltage in (5, 10, 25):
            require_locked(nano)
            if power.read()['run_stop_setting'] != 0:
                raise RuntimeError('Expected OFF before changing setpoint')
            power.write(0, voltage)
            configured = power.read()
            if configured['raw_registers'][:2] != [voltage, TEST_CURRENT_RAW] or configured['run_stop_setting']:
                raise RuntimeError('Setpoint/current limit readback mismatch; will not turn ON')
            entry = {'requested_v': voltage, 'configured': configured, 'samples': []}
            report['steps'].append(entry)
            power.write(4, 1)
            # Bounded settling, with every read checked. Two consecutive matching
            # readings required; any >25 V reading immediately aborts further steps.
            consecutive = 0
            for _ in range(6):
                sleep(0.5)
                sample = power.read()
                entry['samples'].append(sample)
                if (sample['run_stop_setting'] != 1 or sample['set_voltage_v'] != voltage
                        or sample['raw_registers'][1] != TEST_CURRENT_RAW):
                    raise RuntimeError('Supply ON/setpoint changed unexpectedly')
                if sample['voltage_v'] > 25 or sample['current_a'] > 0.011:
                    raise RuntimeError('Measured voltage/current exceeds test limits')
                entry['lcd'] = snapshot(sample)
                consecutive = consecutive + 1 if abs(sample['voltage_v'] - voltage) <= 1.0 else 0
                if consecutive >= 2:
                    break
            if consecutive < 2:
                raise RuntimeError('Measured voltage did not settle within 1 V of setpoint')
            entry['voltage_match'] = True
            entry['unloaded_current_near_zero'] = sample['current_a'] <= 0.002
            if not entry['unloaded_current_near_zero']:
                raise RuntimeError('Unexpected unloaded current above 2 mA')
            power.write(4, 0)
            stopped = power.read()
            if stopped['run_stop_setting'] != 0:
                raise RuntimeError('OFF between steps not confirmed')
            snapshot(stopped)
        report['nano_after'] = require_locked(nano)
        report['valid'] = True
    finally:
        # Attempt both OFF and zero voltage even if one operation fails. Retrying
        # OFF is idempotent. Never restore an old, potentially high setpoint.
        report['cleanup_errors'] = []
        for register, value in ((4, 0), (0, 0), (4, 0)):
            try:
                power.write(register, value)
            except Exception as exc:
                report['cleanup_errors'].append(str(exc))
        report['off_confirmed'] = False
        try:
            for _ in range(10):
                final = power.read()
                report['final'] = final
                report['off_confirmed'] = final['run_stop_setting'] == 0
                if report['off_confirmed'] and final['voltage_v'] == 0:
                    break
                sleep(0.5)
            report['final_lcd'] = snapshot(final)
            report['zero_setpoint_confirmed'] = final['set_voltage_v'] == 0
            report['zero_readback_confirmed'] = final['voltage_v'] == 0 and final['current_a'] == 0
        except Exception as exc:
            report['cleanup_errors'].append(str(exc))
        if (not report['off_confirmed'] or not report.get('zero_setpoint_confirmed')
                or not report.get('zero_readback_confirmed') or report['cleanup_errors']):
            report['valid'] = False


def parameter_test(nano, parameters, report):
    """Change pulse timing/counts, read every byte, then restore original values."""
    original = encode_parameters(parameters)  # Validate first.
    alternate = dict(parameters, Ontime=12.5, Offtime=7.5, Cycle=7, Loopnum=3)
    changed = encode_parameters(alternate)
    if changed == original:
        raise ValueError('Alternate parameters must differ from original')
    require_locked(nano)
    report['valid'] = False
    try:
        report['original'] = nano.sync_inhibited(original)
        report['changed'] = nano.sync_inhibited(changed)
    finally:
        report['restored'] = nano.sync_inhibited(original)
    report['valid'] = True


def spectrum_progress(output_dir, stage, **details):
    """Leave a small durable breadcrumb if the native SDK blocks and is killed."""
    event = {'utc': datetime.now(timezone.utc).isoformat(), 'stage': stage, **details}
    with (output_dir / 'progress.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')


def spectrum_test(sdk_path, output_dir, report, integration_ms=100, sdk_settle_ms=500,
                  frames=3, frame_interval_ms=0, triggered=False):
    """Only USB SDK calls; no power/GPIO/RS485 access."""
    if type(integration_ms) is not int or not 1 <= integration_ms <= 10000:
        raise ValueError('Integration time must be 1–10000 ms')
    if type(sdk_settle_ms) is not int or not 0 <= sdk_settle_ms <= 2000:
        raise ValueError('SDK settle time must be 0–2000 ms')
    if type(frames) is not int or not 1 <= frames <= 1000:
        raise ValueError('Frame count must be 1–1000')
    if type(frame_interval_ms) is not int or not 0 <= frame_interval_ms <= 60000:
        raise ValueError('Frame interval must be 0–60000 ms')
    spectrum_progress(output_dir, 'library_load_start')
    dll = ct.CDLL(str(sdk_path.resolve()))
    spectrum_progress(output_dir, 'library_load_done')
    signatures = {
        'UAI_SpectrometerOpen': [ct.c_int, ct.POINTER(ct.c_void_p), ct.c_int, ct.c_int],
        'UAI_SpectrometerClose': [ct.c_void_p],
        'UAI_SpectrometerGetSerialNumber': [ct.c_void_p, ct.c_void_p],
        'UAI_SpectrometerGetModelName': [ct.c_void_p, ct.c_void_p],
        'UAI_SpectromoduleGetFrameSize': [ct.c_void_p, ct.POINTER(ct.c_short)],
        'UAI_SpectrometerWavelengthAcquire': [ct.c_void_p, ct.c_void_p],
        'UAI_SpectrometerDataOneshot': [ct.c_void_p, ct.c_int, ct.c_void_p, ct.c_int],
    }
    for name, args in signatures.items():
        getattr(dll, name).argtypes = args
        getattr(dll, name).restype = ct.c_int

    frame_number = 0

    def call(name, *args):
        spectrum_progress(output_dir, 'sdk_call_start', function=name, frame=frame_number)
        rc = getattr(dll, name)(*args)
        spectrum_progress(output_dir, 'sdk_call_done', function=name, frame=frame_number, code=rc)
        report.setdefault('sdk_returns', []).append({'function': name, 'code': rc})
        if rc != 0:
            raise RuntimeError('%s returned %s' % (name, rc))

    handle = ct.c_void_p()
    report['valid'] = False
    call('UAI_SpectrometerOpen', 0, ct.byref(handle), 0x0638, 0x0aac)
    if not handle.value:
        raise RuntimeError('SDK returned a null handle')
    try:
        serial, model = ct.create_string_buffer(64), ct.create_string_buffer(64)
        call('UAI_SpectrometerGetSerialNumber', handle, serial)
        call('UAI_SpectrometerGetModelName', handle, model)
        report.update(serial=serial.value.decode(errors='replace'), model=model.value.decode(errors='replace'))
        size = ct.c_short()
        call('UAI_SpectromoduleGetFrameSize', handle, ct.byref(size))
        if not 1 <= size.value <= 8192:
            raise RuntimeError('Invalid frame size')
        wavelength = (ct.c_float * size.value)()
        call('UAI_SpectrometerWavelengthAcquire', handle, wavelength)
        report.update(pixels=size.value, wavelength_range_nm=[wavelength[0], wavelength[-1]],
                      integration_ms=integration_ms, sdk_settle_ms=sdk_settle_ms,
                      frames_expected=frames, frame_interval_ms=frame_interval_ms,
                      triggered=triggered, frames=[])
        if sdk_settle_ms:
            spectrum_progress(output_dir, 'sdk_settle_start', milliseconds=sdk_settle_ms)
            time.sleep(sdk_settle_ms / 1000)
            spectrum_progress(output_dir, 'sdk_settle_done', milliseconds=sdk_settle_ms)
        previous_start = None
        for index in range(frames):
            frame_number = index + 1
            if previous_start is not None and frame_interval_ms:
                time.sleep(max(0, previous_start + frame_interval_ms / 1000 - time.monotonic()))
            if triggered:
                ready = output_dir / f'frame-{frame_number}.ready'
                go = output_dir / f'frame-{frame_number}.go'
                if ready.exists() or go.exists():
                    raise RuntimeError('Stale frame trigger marker')
                ready.write_text(str(frame_number), encoding='ascii')
                spectrum_progress(output_dir, 'frame_ready', frame=frame_number)
                trigger_deadline = time.monotonic() + 30
                while not go.is_file():
                    if time.monotonic() >= trigger_deadline:
                        raise TimeoutError(f'Frame {frame_number} trigger not received within 30 s')
                    time.sleep(.01)
                go.unlink()
                spectrum_progress(output_dir, 'frame_triggered', frame=frame_number)
            # On this device, NaN-initialized output can leave DataOneshot
            # spinning indefinitely. A zeroed ctypes buffer was verified live.
            values = (ct.c_float * size.value)()
            started = time.monotonic()
            previous_start = started
            call('UAI_SpectrometerDataOneshot', handle, integration_ms * 1000, values, 1)
            elapsed = time.monotonic() - started
            if not all(math.isfinite(x) for x in values) or len(set(values)) < 2:
                raise RuntimeError('Nonfinite, missing or constant spectrum data')
            filename = 'spectrum-%d.csv' % (index+1)
            with (output_dir / filename).open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream)
                writer.writerow(['wavelength_nm', 'intensity_raw'])
                writer.writerows(zip(wavelength, values))
            report['frames'].append({'file': filename, 'elapsed_s': elapsed,
                                     'minimum': min(values), 'maximum': max(values)})
        report['valid'] = True
    finally:
        call('UAI_SpectrometerClose', handle)
    return wavelength


def display_loop(power, nano, seconds):
    require_locked(nano)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        error = None
        try:
            status = power.read()
        except Exception as exc:
            status, error = None, str(exc)
        result = nano.report_power(None if status is None else status['run_stop_setting'])
        if error:
            result['hspy_read_error'] = error
        print(json.dumps(result), flush=True)
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('spectrum', 'parameters', 'power', 'display'))
    parser.add_argument('--port', default='/dev/RS485')
    parser.add_argument('--app-dir', type=Path, default=Path.home() / 'Desktop/Plasmic/app')
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=30, choices=range(1, 301))
    parser.add_argument('--integration-ms', type=int, default=100)
    parser.add_argument('--frames', type=int, default=3)
    parser.add_argument('--frame-interval-ms', type=int, default=0)
    parser.add_argument('--triggered', action='store_true')
    parser.add_argument('--sdk-settle-ms', type=int, default=500,
                        help='wait after opening SDK device (0–2000 ms; default 500)')
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    report = {'action': args.action, 'started_utc': datetime.now(timezone.utc).isoformat(), 'valid': False}
    # SIGTERM/SIGHUP route through finally for normal shutdown/SSH disconnect.
    # SIGKILL, power loss and a disconnected bus cannot be handled by software.
    def interrupted(signum, _frame):
        raise RuntimeError('Interrupted by signal %d' % signum)
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupted)
    try:
        if args.action == 'spectrum':
            spectrum_progress(args.outdir, 'process_start', pid=os.getpid(),
                              integration_ms=args.integration_ms, frames=args.frames,
                              triggered=args.triggered)
            wavelength = spectrum_test(args.app_dir / 'lib_linux_ARM_2.7.11.so', args.outdir, report,
                                       integration_ms=args.integration_ms,
                                       sdk_settle_ms=args.sdk_settle_ms, frames=args.frames,
                                       frame_interval_ms=args.frame_interval_ms,
                                       triggered=args.triggered)
            # Reading every wavelength before DataOneshot can stall this SDK.
            # Validate the complete calibration after the SDK handle is closed.
            if (not all(math.isfinite(x) and x > 0 for x in wavelength)
                    or not all(wavelength[i] < wavelength[i+1] for i in range(len(wavelength)-1))):
                raise RuntimeError('Invalid wavelength calibration')
        else:
            with open_port(args.port, 1.0) as port:
                nano = Client(port)
                power = Power(port, load_hspy(args.app_dir))
                if args.action == 'parameters':
                    parameter_test(nano, json.loads((args.app_dir / 'parameter.json').read_text(encoding='utf-8-sig')), report)
                elif args.action == 'power':
                    power_test(power, nano, report)
                else:
                    display_loop(power, nano, args.seconds)
                    report['valid'] = True
    except Exception as exc:
        report.update(valid=False, error=str(exc))
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    (args.outdir / (args.action + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
