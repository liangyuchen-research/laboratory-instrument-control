"""Standalone maintenance reader. Never run alongside another RS485 owner.

Only sends Modbus FC03; does not set voltage/current or start/stop the power.
Run only after the equipment is independently stopped and the bus is released.
"""

import argparse
from datetime import datetime, timezone
import json
import math
import time

from HSPY_Protocol import ProtocolError, build_status_request, parse_status_response


def read_status(port, address=1, timeout=1.0):
    """One bounded transaction; caller must have exclusive ownership of port.

    Reply time is not the instrument's internal measurement time. This result
    must not be interpreted as verification of electrode voltage or safety.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Timeout must be finite and greater than zero')
    request = build_status_request(address)
    original_timeout, original_write_timeout = port.timeout, port.write_timeout
    started = time.monotonic()
    try:
        port.write_timeout = timeout
        port.reset_input_buffer()
        # Assumes no other bus owner; exceeds the manual's >5 ms idle time.
        time.sleep(0.006)
        if port.write(request) != len(request):
            raise ProtocolError('Incomplete serial write of read request')
        deadline = time.monotonic() + timeout

        def read_exact(count):
            data = bytearray()
            while len(data) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Timed out waiting for HSPY status')
                port.timeout = min(remaining, 0.1)
                chunk = port.read(count - len(data))
                if not chunk:
                    # Also avoids a busy loop with nonblocking test adapters.
                    time.sleep(min(0.001, remaining))
                data.extend(chunk)
            return bytes(data)

        header = read_exact(3)
        if header[0] != address:
            raise ProtocolError('Unexpected slave address')
        if header[1] == 0x83:
            response = header + read_exact(2)
        elif header[1] == 0x03 and header[2] == 10:
            response = header + read_exact(12)
        else:
            raise ProtocolError('Unexpected function or byte count')
        result = parse_status_response(response, address)
        result['received_at_utc'] = datetime.now(timezone.utc).isoformat()
        result['transaction_ms'] = round((time.monotonic() - started) * 1000, 3)
        result['measurement_age_ms'] = None  # Not supplied by this protocol.
        return result
    finally:
        port.timeout = original_timeout
        port.write_timeout = original_write_timeout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/RS485')
    parser.add_argument('--address', type=int, default=1, choices=range(1, 16))
    parser.add_argument('--stopbits', type=int, required=True, choices=(1, 2),
                        help='Use the actual device setting; the manual lists both values.')
    parser.add_argument('--timeout', type=float, default=1.0)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and greater than zero')
    try:
        import serial
        with serial.Serial(args.port, 9600, bytesize=8, parity='N',
                           stopbits=args.stopbits, timeout=0.1,
                           write_timeout=args.timeout) as port:
            result = read_status(port, args.address, args.timeout)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'valid': False, 'source': 'power_supply_display',
                          'hv_state': 'UNKNOWN', 'voltage_v': None,
                          'current_a': None, 'error': str(exc)}, ensure_ascii=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
