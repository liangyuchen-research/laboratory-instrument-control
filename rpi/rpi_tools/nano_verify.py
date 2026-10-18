#!/usr/bin/env python3
"""Nano v2 commissioning: default FC03 only; optional inhibited config sync/STOP.

Never sends ARM, START, HSPY commands or GPIO pulses. Requires sole RS485 owner.
Run only after the user has flashed nano_rs485_v2; not compatible with old ASCII.
"""
import argparse
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import secrets
import struct
import time
import zlib

ADDRESS = 2
STATES = ('IDLE', 'ARMED', 'WAIT_TRIGGER', 'RUNNING', 'DONE', 'FAULT')
FAULTS = ('NONE', 'HEARTBEAT_LOST', 'TRIGGER_TIMEOUT', 'JOB_TIMEOUT')
RESULTS = {0: 'OK', 1: 'CONFIG_APPLIED', 2: 'STOPPED', 10: 'BAD_SESSION',
           11: 'BAD_SEQUENCE', 12: 'SEQUENCE_CONFLICT', 13: 'BAD_STATE',
           14: 'BAD_CONFIG', 15: 'REVISION_MISMATCH', 16: 'OUTPUT_INHIBITED',
           17: 'BAD_COMMAND', 18: 'LEASE_EXPIRED', 19: 'BAD_JOB'}
CONFIG_FIELDS = ('revision', 'on_time_us', 'off_time_us', 'cycles', 'loop_count',
                 'integration_time_us', 'spectra_interval_us', 'voltage_display_v')


class ProtocolError(ValueError):
    pass


def crc16(data):
    crc = 0xffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc


def frame(body):
    return body + struct.pack('<H', crc16(body))


def exact_integer(value, multiplier=1):
    try:
        number = Decimal(str(value)) * multiplier
    except (InvalidOperation, ValueError):
        raise ValueError('Parameter must be numeric') from None
    if not number.is_finite() or number != number.to_integral_value() or not 0 <= number <= 0xffffffff:
        raise ValueError('Parameter must encode exactly as a uint32 integer')
    return int(number)


def encode_parameters(parameters, revision=1):
    """Old JSON uses milliseconds. Do not round or silently truncate fields."""
    values = [exact_integer(revision), exact_integer(parameters['Ontime'], 1000),
              exact_integer(parameters['Offtime'], 1000), exact_integer(parameters['Cycle']),
              exact_integer(parameters['Loopnum']), exact_integer(parameters['Integration_Time'], 1000),
              exact_integer(parameters['Spectra_Interval'], 1000), exact_integer(parameters['Voltage'])]
    rev, on, off, cycles, loops, integration, interval, voltage = values
    if not rev or not 1000 <= on <= 1000000 or not 1000 <= off <= 1000000 or on % 100 or off % 100:
        raise ValueError('Pulse times require 1..1000 ms in 0.1 ms steps, nonzero revision')
    if not 1 <= cycles <= 10000 or not 1 <= loops <= 1000 or cycles * loops > 10000:
        raise ValueError('Invalid cycle/loop count')
    if (on + off) * cycles * loops > 120000000:
        raise ValueError('Programmed pulse time exceeds 120 s')
    if not 0 < integration <= interval <= 60000000 or voltage > 1000:
        raise ValueError('Invalid spectrometer metadata or voltage display value')
    data = struct.pack('>8I', *values)
    return data + struct.pack('>I', zlib.crc32(data))


def decode_config(words):
    if len(words) != 18:
        raise ProtocolError('Expected 18 config registers')
    data = struct.pack('>18H', *words)
    values = struct.unpack('>9I', data)
    if zlib.crc32(data[:32]) != values[-1]:
        raise ProtocolError('Active config CRC32 mismatch')
    return dict(zip(CONFIG_FIELDS + ('crc32',), values))


def decode_status(words):
    if len(words) != 40 or words[:2] != [0x504e, 1] or words[2] not in (0x0100, 0x0110, 0x0120, 0x0121):
        raise ProtocolError('Nano magic/protocol/firmware mismatch; expected v1.0.0, v1.1.0, v1.2.0 or v1.2.1')
    if words[4] >= len(STATES) or words[5] >= len(FAULTS) or words[9] not in RESULTS:
        raise ProtocolError('Invalid Nano state/fault/result')
    if any(words[i] not in (0, 1) for i in (6, 7, 8, 39)) or words[33] not in (0, 1, 2):
        raise ProtocolError('Invalid Nano output/phase fields')
    u32 = lambda i: (words[i] << 16) | words[i + 1]
    result = {'valid': True, 'firmware': {0x100: '1.0.0', 0x110: '1.1.0', 0x120: '1.2.0', 0x121: '1.2.1'}[words[2]], 'protocol': 1, 'address': ADDRESS,
              'outputs_allowed': bool(words[3] & 1), 'config_valid': bool(words[3] & 2),
              'session_valid': bool(words[3] & 4), 'heartbeat_fresh': bool(words[3] & 8),
              'trigger_seen': bool(words[3] & 16), 'state': STATES[words[4]],
              'fault': FAULTS[words[5]], 'output_command_high': bool(words[6]),
              'output_pin_sample': bool(words[7]), 'trigger_input': bool(words[8]),
              'command_result': RESULTS[words[9]], 'session_id': u32(10),
              'last_request_id': u32(12), 'active_revision': u32(14), 'active_crc32': u32(16),
              'job_id': u32(18), 'pulses_completed': u32(20), 'loops_completed': u32(22),
              'uptime_ms_mod_micros_wrap': u32(24), 'heartbeat_age_ms': u32(26),
              'running_job_elapsed_ms': u32(28), 'status_seq': u32(30),
              'reset_cause_raw': words[32], 'phase': ('NONE', 'HIGH', 'LOW')[words[33]],
              'pulses_aborted': u32(34), 'last_edge_us': u32(36), 'timer_resolution_us': words[38],
              'pulse_train_active': bool(words[39]),
              'mosfet_state': 'UNKNOWN', 'hv_state': 'UNKNOWN'}
    if not result['outputs_allowed'] and (result['output_command_high'] or result['output_pin_sample']):
        raise ProtocolError('Output lock is reported but D13 is HIGH')
    if result['heartbeat_age_ms'] == 0xffffffff:
        result['heartbeat_age_ms'] = None
    return result


class Client:
    def __init__(self, port, timeout=1.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Positive finite timeout required')
        self.port, self.timeout = port, timeout

    def _read_exact(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Nano reply missing/incomplete; state UNKNOWN')
            self.port.timeout = min(remaining, 0.05)
            data.extend(self.port.read(size - len(data)))
        return bytes(data)

    def transaction(self, body, response_size):
        old_timeout = self.port.timeout
        try:
            self.port.reset_input_buffer()
            time.sleep(0.006)
            request = frame(body)
            if self.port.write(request) != len(request):
                raise ProtocolError('Incomplete serial write')
            deadline = time.monotonic() + self.timeout
            header = self._read_exact(3, deadline)
            if header[0] != ADDRESS or header[1] not in (body[1], body[1] | 0x80):
                raise ProtocolError('Unexpected slave/function; shared bus owner or old firmware?')
            length = 5 if header[1] & 0x80 else response_size
            response = header + self._read_exact(length - 3, deadline)
            if crc16(response):
                raise ProtocolError('Nano reply CRC16 mismatch')
            if response[1] & 0x80:
                raise ProtocolError('Nano Modbus exception %d' % response[2])
            return response
        finally:
            self.port.timeout = old_timeout

    def read(self, start, count):
        if not 1 <= count <= 40:
            raise ValueError('Read count must be 1..40')
        response = self.transaction(struct.pack('>BBHH', ADDRESS, 3, start, count), 5 + 2 * count)
        if response[2] != count * 2:
            raise ProtocolError('Unexpected register byte count')
        return list(struct.unpack('>%dH' % count, response[3:-2]))

    def write(self, start, data):
        if (start, len(data)) not in ((0x100, 44), (0x200, 18)):
            raise ValueError('Only defined Nano config/command frames are allowed')
        body = struct.pack('>BBHHB', ADDRESS, 16, start, len(data) // 2, len(data)) + data
        response = self.transaction(body, 8)
        if response[:6] != body[:6]:
            raise ProtocolError('FC16 acknowledgement mismatch')

    def status(self):
        return decode_status(self.read(0, 40))

    def stop(self):
        self.write(0x200, struct.pack('>IIHII', 0, 0, 5, 0, 0))
        status = self.status()
        if (status['command_result'] != 'STOPPED' or status['state'] not in ('IDLE', 'FAULT')
                or status['output_command_high'] or status['output_pin_sample']):
            raise ProtocolError('STOP not confirmed by Nano status')
        return status

    def report_power(self, run_stop):
        """Display-only snapshot from a just-completed HSPY read. None = UNKNOWN.

        Does not open/replace a session, ARM, START or renew the pulse heartbeat.
        The sole bus owner must serialize this with other Nano commands.
        """
        if run_stop is not None and (type(run_stop) is not int or run_stop not in (0, 1)):
            raise ValueError('Power report must be None, 0=OFF or 1=ON')
        before = self.status()
        if before['firmware'] not in ('1.1.0', '1.2.0', '1.2.1') or not before['session_id']:
            raise ProtocolError('LCD reporting requires firmware 1.1.0+ and an existing synced session')
        request = before['last_request_id'] + 1
        if request > 0xffffffff:
            raise ProtocolError('Request counter exhausted; open a new session while idle')
        code = 0 if run_stop is None else run_stop + 1
        self.write(0x200, struct.pack('>IIHII', before['session_id'], request, 7, code, 0))
        after = self.status()
        words = self.read(0x300, 8)
        if (after['session_id'] != before['session_id'] or after['last_request_id'] != request
                or after['command_result'] != 'OK' or words[0] != code or words[1] != code
                or (words[4] << 16 | words[5]) != request):
            raise ProtocolError('Nano power snapshot was not confirmed')
        return {'power_snapshot': ('UNKNOWN', 'OFF', 'ON')[code],
                'source': 'RPi_HSPY_RunStop_readback', 'expires_after_ms': 5000,
                'lcd_frozen': bool(words[6]), 'request_id': request}

    def sync_inhibited(self, config_data):
        """Only commissioning build. Does not ARM or START, even with good config."""
        before = self.status()
        if (before['outputs_allowed'] or before['state'] not in ('IDLE', 'DONE')
                or before['output_command_high'] or before['output_pin_sample']):
            raise ProtocolError('Config commissioning requires locked outputs and IDLE/DONE')
        session = secrets.randbelow(0xffffffff) + 1
        while session == before['session_id']:
            session = secrets.randbelow(0xffffffff) + 1
        self.write(0x200, struct.pack('>IIHII', session, 1, 1, 0, 0))
        opened = self.status()
        if opened['session_id'] != session or opened['last_request_id'] != 1 or opened['command_result'] != 'OK':
            raise ProtocolError('Session handshake failed')
        self.write(0x100, struct.pack('>II', session, 2) + config_data)
        status = self.status()
        if (status['session_id'] != session or status['last_request_id'] != 2
                or status['command_result'] != 'CONFIG_APPLIED' or not status['config_valid']):
            raise ProtocolError('Nano rejected config: ' + status['command_result'])
        actual = struct.pack('>18H', *self.read(0x100, 18))
        if actual != config_data:
            raise ProtocolError('Nano active parameters differ from requested parameters')
        final = self.status()
        if (final['session_id'] != session or not final['config_valid']
                or final['active_revision'] != struct.unpack('>I', config_data[:4])[0]
                or final['active_crc32'] != struct.unpack('>I', config_data[-4:])[0]
                or final['outputs_allowed'] or final['state'] != 'IDLE'
                or final['output_command_high'] or final['output_pin_sample']):
            raise ProtocolError('State/config changed during verification')
        final['active_parameters'] = decode_config(list(struct.unpack('>18H', actual)))
        final['parameters_match'] = True
        return final


@contextmanager
def open_port(path, timeout):
    import fcntl
    import serial
    import termios
    # pyserial advisory flock + TIOCEXCL prevents new serial opens. Existing
    # non-cooperating owners must already be stopped; neither mechanism kills them.
    port = serial.Serial(port=None, baudrate=9600, bytesize=8, parity='N', stopbits=1,
                         timeout=0.05, write_timeout=timeout, exclusive=True)
    port.dtr = False
    port.rts = False
    port.port = os.path.realpath(path)
    port.open()
    try:
        fcntl.ioctl(port.fileno(), termios.TIOCEXCL)
        yield port
    finally:
        try:
            fcntl.ioctl(port.fileno(), termios.TIOCNXCL)
        finally:
            port.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/RS485')
    parser.add_argument('--timeout', type=float, default=1.0)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--sync-parameters', type=Path, help='Write/read back config; requires HARD_OUTPUT_ENABLE=0')
    actions.add_argument('--stop', action='store_true', help='Explicitly request Nano STOP, never HSPY OFF')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be finite and > 0')
    try:
        # Validate all values before opening the physical port.
        data = encode_parameters(json.loads(args.sync_parameters.read_text(encoding='utf-8-sig'))) if args.sync_parameters else None
        with open_port(args.port, args.timeout) as port:
            client = Client(port, args.timeout)
            if data is not None:
                result = client.sync_inhibited(data)
            elif args.stop:
                result = client.stop()
            else:
                result = client.status()
                if result['config_valid']:
                    config = decode_config(client.read(0x100, 18))
                    after = client.status()
                    if (result['session_id'], result['active_revision'], result['active_crc32']) != (
                            after['session_id'], after['active_revision'], after['active_crc32']) or not after['config_valid']:
                        raise ProtocolError('Configuration changed while reading')
                    if (config['revision'], config['crc32']) != (after['active_revision'], after['active_crc32']):
                        raise ProtocolError('Configuration differs from status snapshot')
                    result = after
                    result['active_parameters'] = config
            result['received_at_unix_s'] = time.time()
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'valid': False, 'state': 'UNKNOWN', 'mosfet_state': 'UNKNOWN',
                          'hv_state': 'UNKNOWN', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
