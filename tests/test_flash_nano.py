"""Nano burning program (installer/flash_nano.py) automated testing.unnecessary Nano: Use a simulated one avrdude replace.

    python tests/test_flash_nano.py
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'rpi' / 'rpi_tools')]

import flash_nano  # noqa: E402
from nano_verify import frame  # noqa: E402  (the field-verified protocol helpers)

# Stands in for avrdude.exe: records how it was called and answers like the real tool does.
SIMULATED_AVRDUDE = r'''
import json, os, sys
args = sys.argv[1:]
log = os.environ['SIMULATED_LOG']
calls = json.load(open(log)) if os.path.exists(log) else []
calls.append({'args': args, 'cwd': os.getcwd()})
json.dump(calls, open(log, 'w'))
baud = args[args.index('-b') + 1]
port = args[args.index('-P') + 1]
mode = os.environ['SIMULATED_MODE']
if mode == 'busy':
    print('Error: cannot open port \\\\.\\%s: Access is denied.' % port)
    print('Error: unable to open port %s for programmer arduino' % port)
    sys.exit(1)
if mode == 'wrong_chip':
    print('Error: expected signature for ATmega328P is 1E 95 0F')
    sys.exit(1)
if mode == 'dead' or (mode == 'old_bootloader' and baud == '115200'):
    print('Warning: attempt 1 of 3: not in sync: resp=0x00')
    print('Error: programmer is not responding')
    print('Error: unable to open port %s for programmer arduino' % port)
    sys.exit(1)
print('Writing 15264 bytes to flash')
print('Verifying 15264 bytes of flash against input file')
print('Avrdude done.  Thank you.')
'''


def status_reply(firmware=0x0121, state=0, fault=0, pin=0):
    """An 85-byte reply as firmware 1.2.1 builds it (words 0-39, big-endian, Modbus CRC)."""
    words = [0] * 40
    words[0], words[1], words[2], words[3] = 0x504e, 1, firmware, 1
    words[4], words[5], words[7] = state, fault, pin
    return frame(bytes([2, 3, 80]) + struct.pack('>40H', *words))


class FlashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        simulated = Path(self.temp.name) / 'simulated_avrdude.py'
        simulated.write_text(SIMULATED_AVRDUDE, encoding='utf-8')
        self.log = Path(self.temp.name) / 'calls.json'
        os.environ['SIMULATED_LOG'] = str(self.log)
        self.addCleanup(os.environ.pop, 'SIMULATED_LOG', None)
        self.addCleanup(os.environ.pop, 'SIMULATED_MODE', None)
        real_command = flash_nano.avrdude_command
        flash_nano.avrdude_command = lambda exe, conf, port, baud: (
            [sys.executable, str(simulated)] + real_command(exe, conf, port, baud)[1:])
        self.addCleanup(setattr, flash_nano, 'avrdude_command', real_command)
        flash_nano.ARGS = types.SimpleNamespace(yes=True, port='')
        self.said = []
        real_say = flash_nano.say
        flash_nano.say = lambda text='': self.said.append(text)
        self.addCleanup(setattr, flash_nano, 'say', real_say)

    def flash(self, mode):
        os.environ['SIMULATED_MODE'] = mode
        result = flash_nano.step_flash(flash_nano.BUNDLED / 'avrdude.exe', flash_nano.BUNDLED / 'avrdude.conf', 'COM7')
        calls = json.loads(self.log.read_text()) if self.log.exists() else []
        return result, calls, '\n'.join(self.said)

    def test_new_bootloader_flashes_at_the_first_speed(self):
        result, calls, text = self.flash('ok')
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        args = calls[0]['args']
        self.assertEqual(args[:2], ['-C', str(flash_nano.BUNDLED / 'avrdude.conf')])
        self.assertEqual(args[2:], ['-p', 'atmega328p', '-c', 'arduino', '-P', 'COM7', '-b', '115200', '-D',
                                    '-x', 'attempts=3', '-U', 'flash:w:nano_rs485_v2.pulse.hex:i'])
        self.assertEqual(Path(calls[0]['cwd']), flash_nano.HEX.parent)  # so the file name needs no drive letter
        self.assertNotIn('-e', args)   # never a chip erase command
        self.assertNotIn('-F', args)   # never override the signature check
        self.assertIn('Burning completed', text)

    def test_old_bootloader_is_tried_automatically(self):
        result, calls, text = self.flash('old_bootloader')
        self.assertTrue(result)
        self.assertEqual([call['args'][call['args'].index('-b') + 1] for call in calls], ['115200', '57600'])
        self.assertIn('Try the old model instead bootloader', text)

    def test_no_answer_at_any_speed_is_explained(self):
        result, calls, text = self.flash('dead')
        self.assertFalse(result)
        self.assertEqual(len(calls), 2)
        self.assertIn('bootloader No response', text)
        self.assertIn('RS485', text)
        self.assertNotIn('Can’t open', text)  # avrdude's closing "unable to open port" line is not a busy port

    def test_wrong_chip_stops_at_once(self):
        result, calls, text = self.flash('wrong_chip')
        self.assertFalse(result)
        self.assertEqual(len(calls), 1)
        self.assertIn('no ATmega328P', text)

    def test_busy_port_stops_at_once(self):
        result, calls, text = self.flash('busy')
        self.assertFalse(result)
        self.assertEqual(len(calls), 1)
        self.assertIn('Can’t open', text)

    def test_nothing_is_flashed_without_consent(self):
        import builtins
        flash_nano.ARGS = types.SimpleNamespace(yes=False, port='')
        original = builtins.input
        builtins.input = lambda prompt='': ''  # the person just presses Enter instead of typing y
        self.addCleanup(setattr, builtins, 'input', original)
        result, calls, text = self.flash('ok')
        self.assertFalse(result)
        self.assertEqual(calls, [])
        self.assertIn('is not burned', text)


class ToolAndFileTests(unittest.TestCase):
    def test_firmware_file_is_the_verified_build(self):
        self.assertEqual(flash_nano.firmware_problem(), '')
        self.assertEqual(hashlib.sha256(flash_nano.HEX.read_bytes()).hexdigest(), flash_nano.HEX_SHA256)
        with tempfile.TemporaryDirectory() as folder:
            changed = Path(folder) / flash_nano.HEX.name
            changed.write_bytes(flash_nano.HEX.read_bytes() + b':00000001FF\n')
            original, flash_nano.HEX = flash_nano.HEX, changed
            self.addCleanup(setattr, flash_nano, 'HEX', original)
            self.assertIn('different', flash_nano.firmware_problem())

    def test_bundled_avrdude_is_found_first(self):
        found = flash_nano.find_avrdude()
        self.assertIsNotNone(found, 'nano/tools/avrdude/avrdude.exe should travel with the folder')
        self.assertEqual(found[0], flash_nano.BUNDLED / 'avrdude.exe')
        self.assertEqual(found[1], flash_nano.BUNDLED / 'avrdude.conf')

    def test_older_avrdude_gets_no_option_it_does_not_know(self):
        command = flash_nano.avrdude_command(Path('C:/Arduino/bin/avrdude.exe'), None, 'COM3', 57600)
        self.assertNotIn('-x', command)
        self.assertNotIn('-C', command)
        self.assertEqual(command[-2:], ['-U', 'flash:w:nano_rs485_v2.pulse.hex:i'])

    def test_serial_devices_are_sorted_into_usable_and_driverless(self):
        devices = [
            {'name': 'USB-SERIAL CH340 (COM7)', 'port': 'COM7', 'vid': '1A86', 'pid': '7523', 'error': 0},
            {'name': 'USB Serial Port (COM4)', 'port': 'COM4', 'vid': '0403', 'pid': '6001', 'error': 0},
            {'name': 'USB2.0-Serial', 'port': '', 'vid': '1A86', 'pid': '7523', 'error': 28},
            {'name': 'Some webcam', 'port': '', 'vid': '046D', 'pid': '0825', 'error': 28},
            {'name': 'Modem (COM9)', 'port': 'COM9', 'vid': '12D1', 'pid': '1001', 'error': 0},
        ]
        usable, broken = flash_nano.classify(devices)
        self.assertEqual([row['port'] for row in usable], ['COM7', 'COM4'])
        self.assertEqual([row['name'] for row in broken], ['USB2.0-Serial'])
        self.assertIn('CH340', flash_nano.describe(usable[0]))

    def test_listing_devices_on_this_computer_works(self):
        rows = flash_nano.list_serial_devices()
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertEqual(set(row), {'name', 'port', 'vid', 'pid', 'error'})

    def test_version_reply_is_decoded_with_the_verified_decoder(self):
        status = flash_nano.decode_reply(status_reply())
        self.assertEqual((status['firmware'], status['state'], status['fault'], status['output_pin_sample']),
                         ('1.2.1', 'IDLE', 'NONE', False))
        self.assertEqual(flash_nano.decode_reply(status_reply(state=5, fault=1))['fault'], 'HEARTBEAT_LOST')
        for bad in (b'', status_reply()[:-1], status_reply()[:-2] + b'\x00\x00', b'\x01' + status_reply()[1:]):
            with self.assertRaises(ValueError):
                flash_nano.decode_reply(bad)

    def test_helper_scripts_are_plain_ascii_and_present(self):
        for name in ('serial-ports.ps1', 'nano-query.ps1'):
            (ROOT / 'installer' / name).read_bytes().decode('ascii')
        self.assertTrue((ROOT / 'Flash-Nano.cmd').is_file())
        self.assertTrue((ROOT / 'nano' / 'drivers' / 'CH341SER.EXE').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
