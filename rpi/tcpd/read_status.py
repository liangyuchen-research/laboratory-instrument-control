"""Sent over SSH stdin. Reads HSPY/Nano and refreshes Nano's LCD power snapshot."""
import json
import sys
import time
import fcntl
from pathlib import Path

app_dir, tools_dir, serial_port = sys.argv[1:]
sys.path[:0] = [tools_dir, app_dir]
from nano_verify import Client, open_port, decode_config, encode_parameters
from HSPY_Read_Status import read_status

result = {}
try:
    parameter_file = Path(app_dir) / 'parameter.json'
    if parameter_file.stat().st_size > 65536:
        raise ValueError('parameter.json too large')
    settings = json.loads(parameter_file.read_text(encoding='utf-8-sig'))
    encode_parameters(settings)
    result['parameter'] = {'valid': True, 'voltage_v': int(settings['Voltage']),
                           'integration_ms': int(settings['Integration_Time']),
                           'on_time_ms': settings['Ontime'], 'off_time_ms': settings['Offtime'],
                           'cycles': settings['Cycle'], 'loops': settings['Loopnum'],
                           'spectra_interval_ms': settings['Spectra_Interval']}
except Exception as exc:
    result['parameter'] = {'valid': False, 'error': str(exc)}
with open('/tmp/hm-hmi-rs485.lock', 'w') as serial_lock:
  fcntl.flock(serial_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  with open_port(serial_port, 1.0) as port:
    for name in ('power', 'nano'):
        try:
            if name == 'power':
                value = read_status(port, address=1, timeout=1.0)
            else:
                client = Client(port)
                value = client.status()
                if value['config_valid']:
                    config = decode_config(client.read(0x100, 18))
                    after = client.status()
                    if (value['session_id'], value['active_revision'], value['active_crc32']) != (
                            after['session_id'], after['active_revision'], after['active_crc32']):
                        raise RuntimeError('Nano configuration changed during read')
                    if not after['config_valid'] or (config['revision'], config['crc32']) != (
                            after['active_revision'], after['active_crc32']):
                        raise RuntimeError('Nano configuration readback mismatch')
                    value = after
                    value['active_parameters'] = config
                # Display data only: POWER_REPORT cannot ARM, START or renew the
                # pulse lease. A failed HSPY read explicitly reports UNKNOWN.
                if value['firmware'] in ('1.1.0', '1.2.0', '1.2.1') and value['session_id']:
                    power = result.get('power', {})
                    try:
                        lcd = client.report_power(power['run_stop_setting'] if power.get('valid') else None)
                        updated = client.status()
                        if 'active_parameters' in value:
                            updated['active_parameters'] = value['active_parameters']
                        updated['lcd_power_report'] = lcd
                        value = updated
                    except Exception as exc:
                        value['lcd_power_report'] = {'valid': False, 'error': str(exc)}
            result[name] = value
        except Exception as exc:
            result[name] = {'valid': False, 'error': str(exc)}
result['received_at_unix_s'] = time.time()
print(json.dumps(result, allow_nan=False))
