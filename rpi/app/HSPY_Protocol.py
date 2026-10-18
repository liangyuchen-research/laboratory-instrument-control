"""HSPY register units and read-only Modbus codec; no hardware side effects."""

from decimal import Decimal, InvalidOperation
import struct


# Installation-specific scale, corrected against the user's panel on 2026-09-20:
# raw Set-U=10 -> 10 V; raw Set-I=9990 -> 999.0 mA.
# Not a universal HSPY scale. Nonzero measured U/I and writes remain untested.
VOLTAGE_UNITS_PER_VOLT = 1
CURRENT_UNITS_PER_AMP = 10000
MAX_VOLTAGE_V = 1000  # User-reported supply rating; not permission to energize.
MAX_REGISTER_VALUE = 0xFFFF


class ProtocolError(ValueError):
    """The reply cannot be used as valid telemetry."""


def voltage_to_register(voltage):
    """Encode this installation's integer volts, limited to its 1000 V rating."""
    try:
        value = Decimal(str(voltage))
    except (InvalidOperation, ValueError):
        raise ValueError('Voltage must be a finite number in volts')
    if not value.is_finite() or not Decimal('0') <= value <= MAX_VOLTAGE_V:
        raise ValueError('Voltage must be between 0 and 1000 V for this installation')
    raw = value * VOLTAGE_UNITS_PER_VOLT
    if raw != raw.to_integral_value():
        raise ValueError('Voltage must use increments of 1 V for this installation')
    if raw > MAX_REGISTER_VALUE:
        raise ValueError('Voltage exceeds the 16-bit register')
    return int(raw)


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _validate_address(address):
    # The supplied manual's device-specific address range is 1..15.
    if isinstance(address, bool) or not isinstance(address, int) or not 1 <= address <= 15:
        raise ValueError('HSPY slave address must be an integer from 1 to 15')


def build_status_request(address=1):
    """FC03: read Set-U, Set-I, U, I and Run-Stop (0x0000..0x0004)."""
    _validate_address(address)
    body = struct.pack('>BBHH', address, 0x03, 0x0000, 5)
    return body + struct.pack('<H', crc16(body))


def parse_status_response(response, address=1):
    """Validate an entire FC03 response before exposing any measurements."""
    _validate_address(address)
    if len(response) < 5:
        raise ProtocolError('Truncated Modbus response')
    if crc16(response[:-2]) != int.from_bytes(response[-2:], 'little'):
        raise ProtocolError('Modbus CRC mismatch')
    if response[0] != address:
        raise ProtocolError('Unexpected slave address')
    if response[1] == 0x83:
        if len(response) != 5:
            raise ProtocolError('Invalid Modbus exception length')
        raise ProtocolError('Modbus exception 0x{:02X}'.format(response[2]))
    if response[1] != 0x03:
        raise ProtocolError('Unexpected Modbus function code')
    if len(response) != 15 or response[2] != 10:
        raise ProtocolError('Expected five registers (10 data bytes)')
    set_u, set_i, actual_u, actual_i, run_stop = struct.unpack('>5H', response[3:-2])
    if run_stop not in (0, 1):
        raise ProtocolError('Invalid Run-Stop setting')
    return {
        'valid': True,
        'source': 'power_supply_display',
        'hv_state': 'UNKNOWN',
        'conversion_profile': 'site_20260920_1V_0.1mA',
        'conversion_basis': 'user_panel_setpoints_single_point',
        'measurement_scale_verified': False,
        'set_voltage_v': set_u / VOLTAGE_UNITS_PER_VOLT,
        'set_current_a': set_i / CURRENT_UNITS_PER_AMP,
        'voltage_v': actual_u / VOLTAGE_UNITS_PER_VOLT,
        'current_a': actual_i / CURRENT_UNITS_PER_AMP,
        'run_stop_setting': run_stop,
        'raw_registers': [set_u, set_i, actual_u, actual_i, run_stop],
    }
