"""copied program (pc/vendor: V7 pump program, pyserial, PyYAML) and fonts.

Read-only file and use it in another process V7 Load your own simulator once. No ports are opened, no devices are touched.

    python tests/test_vendor.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'pc' / 'vendor'
FONTS = ROOT / 'pc' / 'web' / 'fonts'
TOOL = ROOT / 'handover' / 'tools' / 'check_v7_sync.py'

# Run in a process of its own: importing V7's modules registers its functions once per process, and
# nothing of this may stay loaded in the test process. serial.Serial is replaced before anything
# else is imported, so a real port cannot be opened even by a mistake in this test.
LOAD = r'''
import json, os, sys, tempfile
from pathlib import Path
vendor = Path(sys.argv[1])
sys.path[:0] = [str(vendor / 'site'), str(vendor / 'v7')]
import serial, yaml
opened = []
def refuse(*args, **kwargs):
    opened.append(args)
    raise RuntimeError('a real serial port must not be opened in a test')
serial.Serial = refuse
config = yaml.safe_load((vendor / 'v7' / 'config' / 'hardware.yaml').read_text(encoding='utf-8'))
config['link'].update(simulate=True, port='SIMULATED', simulation_speedup=50)
with tempfile.TemporaryDirectory() as folder:
    private = Path(folder) / 'hardware.sim.yaml'
    private.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
    os.environ['LAB_CONFIG'] = str(private)
    os.environ['SCALE_CALIBRATION_FILE'] = str(Path(folder) / 'scale_calibration.json')
    import backend
    from backend import registry, transport
    from backend.config import get_config
    from backend import functions
    link = transport.Link()
    assert link.simulated is True
    link.start()
    context = registry.Context(link=link, config=get_config())
    listed = registry.get('motors.list').invoke(context, {})
    state = link.snapshot()
    link.stop()
    print(json.dumps({
        'version': backend.__version__, 'libyaml': yaml.__with_libyaml__, 'pyserial': serial.__version__,
        'functions': sorted(row['name'] for row in registry.manifest()),
        'groups': [[group['id'], [channel['id'] for channel in group['channels']]] for group in listed['groups']],
        'connected': state['connected'], 'simulated': state['simulated'], 'opened': opened,
        'modules': sorted(name for name in sys.modules if name.split('.')[0] in ('fastapi', 'uvicorn', 'starlette'))}))
'''


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ManifestTests(unittest.TestCase):
    def test_every_copied_file_is_as_it_was_copied(self):
        rows = {}
        for line in (VENDOR / 'MANIFEST.sha256').read_text(encoding='utf-8').splitlines():
            digest, size, name = line.split('  ', 2)
            rows[name] = (digest, int(size))
        present = {item.relative_to(ROOT / 'pc').as_posix(): item
                   for base in (VENDOR, FONTS) for item in base.rglob('*')
                   if item.is_file() and item.name != 'MANIFEST.sha256'}
        self.assertEqual(sorted(present), sorted(rows))
        for name, path in present.items():
            self.assertEqual((sha256(path), path.stat().st_size), rows[name], name)
        self.assertGreaterEqual(len(rows), 70)

    def test_the_check_tool_agrees_and_notices_a_change(self):
        done = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(TOOL)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('None', done.stdout)

    def test_nothing_is_here_that_must_not_be(self):
        for item in VENDOR.rglob('*'):
            self.assertNotEqual(item.suffix, '.pyd', item)
            self.assertNotIn(item.name, ('main.py', '__pycache__'), item)
        for path in (VENDOR / 'v7').rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            for word in ('fastapi', 'uvicorn', 'httpx'):
                self.assertNotIn('import ' + word, text, path)
                self.assertNotIn('from ' + word, text, path)

    def test_what_the_program_needs_is_there(self):
        for name in ('v7/backend/__init__.py', 'v7/backend/config.py', 'v7/backend/registry.py', 'v7/backend/transport.py',
                     'v7/backend/simulator.py', 'v7/backend/functions/__init__.py', 'v7/backend/functions/motors.py',
                     'v7/backend/functions/pump.py', 'v7/backend/functions/scale.py', 'v7/config/hardware.yaml',
                     'v7/config/scale_calibration.json', 'v7/firmware/Arduino/Arduino.ino', 'v7/firmware/Arduino/hw_config.h',
                     'site/serial/__init__.py', 'site/serial/serialwin32.py', 'site/serial/tools/list_ports.py',
                     'site/yaml/__init__.py', 'licenses/PyYAML-6.0.2/LICENSE', 'licenses/pyserial-3.5/METADATA', 'README.txt'):
            self.assertTrue((VENDOR / name).is_file(), name)
        for name in ('barlow-400.ttf', 'barlow-500.ttf', 'barlow-700.ttf', 'barlow-condensed-400.ttf',
                     'barlow-condensed-600.ttf', 'Barlow-OFL.txt', 'BarlowCondensed-OFL.txt'):
            self.assertTrue((FONTS / name).is_file(), name)
        self.assertIn('BSD', (VENDOR / 'licenses' / 'pyserial-3.5' / 'METADATA').read_text(encoding='utf-8', errors='replace'))
        self.assertIn('pyserial', (VENDOR / 'README.txt').read_text(encoding='utf-8'))

    def test_the_protected_list_does_not_mention_these_files(self):
        """SHA256SUMS.txt is the list of the 22 site-verified files; the copied code has its own manifest."""
        self.assertNotIn('vendor', (ROOT / 'SHA256SUMS.txt').read_text(encoding='utf-8', errors='replace'))


class LoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PLASMIC_NO_SERIAL='1')
        environment.pop('LAB_CONFIG', None)
        done = subprocess.run([sys.executable, '-B', '-c', LOAD, str(VENDOR)], capture_output=True, text=True,
                              encoding='utf-8', env=environment, timeout=120)
        if done.returncode:
            raise AssertionError(done.stdout + done.stderr)
        cls.loaded = json.loads(done.stdout.strip().splitlines()[-1])

    def test_the_copied_code_runs_on_the_bundled_interpreter_with_its_own_simulator(self):
        loaded = self.loaded
        self.assertEqual((loaded['version'], loaded['pyserial'], loaded['libyaml']), ('5.0.0', '3.5', False))
        self.assertEqual((loaded['connected'], loaded['simulated'], loaded['opened'], loaded['modules']), (True, True, [], []))

    def test_all_26_functions_are_there(self):
        names = self.loaded['functions']
        self.assertEqual(len(names), 26)
        for name in ('motors.run', 'motors.stop', 'motors.dose', 'motors.dose_by_weight', 'motors.clean', 'motors.stop_all',
                     'pump.detect', 'pump.init', 'pump.stop', 'pump.set_syringe', 'pump.set_valve', 'pump.set_speed',
                     'pump.aspirate', 'pump.dispense', 'scale.tare', 'scale.calibrate', 'scale.restart'):
            self.assertIn(name, names)

    def test_the_settings_file_has_what_the_program_reads(self):
        self.assertEqual(self.loaded['groups'], [['A', [2, 3, 4, 5]], ['B', [6, 7, 8, 9]]])
        text = (VENDOR / 'v7' / 'config' / 'hardware.yaml').read_text(encoding='utf-8')
        for key in ('max_flow_ml_min', 'pulses_per_ml', 'dose_pulses_per_ml', 'dose_flow_ml_min', 'gravimetric_dose',
                    'cleaning', 'valves', 'motor_links', 'syringe_pump', 'syringe_options_ul', 'valve_positions',
                    'full_stroke_steps', 'scale', 'stream_hz', 'boot_delay_s', 'command_timeout_s', 'simulate'):
            self.assertIn(key, text, key)


if __name__ == '__main__':
    unittest.main(verbosity=2)
