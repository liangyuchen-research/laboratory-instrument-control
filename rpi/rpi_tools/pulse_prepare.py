#!/usr/bin/env python3
"""Prepare Nano 1.2.x parameters while HSPY is confirmed OFF.

This program sends only OPEN_SESSION, SET_CONFIG, POWER_REPORT OFF and, on
failure, STOP. It never sends ARM/START, toggles D12, or turns HSPY ON.
"""
import argparse
import json
from pathlib import Path
import secrets
import struct
import sys

from nano_verify import Client, ProtocolError, decode_config, encode_parameters, open_port


def prepare(nano, read_power, data):
    power = read_power()
    if (not power['valid'] or power['run_stop_setting'] != 0
            or power['voltage_v'] != 0 or power['current_a'] != 0):
        raise RuntimeError('HSPY must be confirmed OFF and at zero output')
    before = nano.status()
    if (before['firmware'] not in ('1.2.0', '1.2.1') or not before['outputs_allowed']
            or before['state'] not in ('IDLE', 'DONE') or before['fault'] != 'NONE'
            or before['output_command_high'] or before['output_pin_sample']):
        raise RuntimeError('Expected Nano 1.2.x idle, fault-free and D13 LOW')
    session = secrets.randbelow(0xffffffff) + 1
    while session == before['session_id']:
        session = secrets.randbelow(0xffffffff) + 1
    try:
        nano.write(0x200, struct.pack('>IIHII', session, 1, 1, 0, 0))
        opened = nano.status()
        if (opened['session_id'] != session or opened['last_request_id'] != 1
                or opened['command_result'] != 'OK'):
            raise ProtocolError('Nano session handshake failed')
        nano.write(0x100, struct.pack('>II', session, 2) + data)
        status = nano.status()
        if (status['session_id'] != session or status['last_request_id'] != 2
                or status['command_result'] != 'CONFIG_APPLIED' or not status['config_valid']):
            raise ProtocolError('Nano config was not applied')
        actual = struct.pack('>18H', *nano.read(0x100, 18))
        if actual != data:
            raise ProtocolError('Nano active config differs from RPi request')
        final_power = read_power()
        if (final_power['run_stop_setting'] != 0 or final_power['voltage_v'] != 0
                or final_power['current_a'] != 0):
            raise RuntimeError('HSPY changed during parameter synchronization')
        lcd = nano.report_power(0)
        final = nano.status()
        if (final['session_id'] != session or final['state'] != 'IDLE'
                or not final['config_valid'] or not final['outputs_allowed']
                or final['output_command_high'] or final['output_pin_sample']):
            raise ProtocolError('Nano left the safe prepared state')
        return {'valid': True, 'firmware': final['firmware'],
                'state': final['state'], 'parameters': decode_config(list(struct.unpack('>18H', actual))),
                'lcd_power_report': lcd, 'hspy_run_stop': final_power['run_stop_setting']}
    except Exception:
        nano.stop()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, default=Path.home() / 'Desktop/Plasmic/app')
    parser.add_argument('--port', default='/dev/RS485')
    args = parser.parse_args()
    sys.path.insert(0, str(args.app_dir))
    from HSPY_Read_Status import read_status
    data = encode_parameters(json.loads((args.app_dir / 'parameter.json').read_text(encoding='utf-8-sig')))
    try:
        with open_port(args.port, 1.0) as port:
            result = prepare(Client(port), lambda: read_status(port, address=1, timeout=1.0), data)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'valid': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
