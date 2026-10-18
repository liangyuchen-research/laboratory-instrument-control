"""“The test cannot open the real port.” This is an automatic test of itself. The

pump control board (Arduino Mega) is connected to this computer and opening its port will cause the board to restart. Check out the three layers of protection here:
  1. If the function ”Allow real port” has not been called, a connection to the real port cannot be established;
  2. Set PLASMIC_NO_SERIAL=1,Even if it is allowed, it still cannot be built;
  3. Tripwire:V7 in the transport layer serial was replaced by something that ”records and reports an error as soon as it is called”. The firmware hash written in
In addition, confirm by reading the source code: only pc/app.py will allow real ports, only two files to be loaded serial, There is no hard-coded port name in the file.

This file itself will not open or list any real ports.

    python tests/test_port_safety.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pump_testing as pt  # noqa: E402  (sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import pumpboard  # noqa: E402

ROOT = pt.ROOT
PC = ROOT / 'pc'
TESTS = ROOT / 'tests'
# Written in two pieces so that this file passes its own checks.
ALLOW = 'allow_real_' + 'ports'
REAL_PORT = 'COM' + '3'
# Not ours and not about the pump board: the Nano flashing test builds (never runs) an avrdude command line
# with that port name in it. The file belongs to nobody's work package and may not be edited.
PORT_NAME_EXCEPTIONS = {'tests/test_flash_nano.py'}
SKIPPED_FOLDERS = {'vendor', 'runtime', 'data', 'data-demo', '__pycache__'}
TEXT_SUFFIXES = {'.py', '.js', '.css', '.html', '.csv', '.json', '.md', '.txt', '.cmd', '.yaml', '.yml'}

CHILD = r'''
import json, os, sys, tempfile, time
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import pump_testing as pt
import pumpboard
wire = pt.install_tripwire()
out = {'kill_switch': os.environ.get('PLASMIC_NO_SERIAL')}
if sys.argv[2] == 'allowed':
    getattr(pumpboard, sys.argv[3])()                      # what only app.main() may do
    out['allowed'] = pumpboard._real_ports_allowed
    rig = pt.Rig(mode='real', settings=pt.identity(pt.MEGA), port_lister=lambda: [pt.MEGA])
    try:
        rig.wait_link('forbidden')
        view = rig.board.view()
        out.update(link=view['link'], text=view['text'], attempts=rig.board._attempts, guard=rig.guard.started)
        try:
            rig.board._new_link('COM91')
            out['direct'] = 'a link was built'
        except pumpboard.RealPortForbidden as exc:
            out['direct'] = 'RealPortForbidden'
    finally:
        rig.close()
else:                                                      # 'tripwire': something tries to open a port all the same
    v7 = pumpboard.load_v7()
    cfg = v7.config.get_config(reload=True)
    cfg['link'] = dict(cfg['link'], port='COM91', simulate=False, boot_delay_s=0)
    link = v7.transport.Link()
    try:
        link.start()
        out['start'] = 'started'
    except v7.LinkError as exc:
        out['start'] = str(exc)
    out['connected'] = link.connected
    try:
        pt.assert_nothing_opened()
        out['end_check'] = 'passed'
    except AssertionError as exc:
        out['end_check'] = 'failed'
out['opened'] = [[list(args), sorted(kwargs)] for args, kwargs in pt.opened]
out['pyserial_is_the_module'] = type(pumpboard.load_v7().transport.serial).__name__ == 'module'
print(json.dumps(out, ensure_ascii=False))
'''


def setUpModule():
    pt.install_tripwire()


def tearDownModule():
    assert pumpboard.PumpBoard._live is None, 'a test left a board running'
    assert pumpboard._real_ports_allowed is False
    assert os.environ.get('PLASMIC_NO_SERIAL') == '1'
    pt.assert_nothing_opened()             # the tripwire record is empty at the end of this module


def project_files(folder, suffixes):
    """Text files of the program itself: not the vendored copies, not the interpreter, never the owner's data."""
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder).parts
        if path.is_file() and path.suffix.lower() in suffixes and not SKIPPED_FOLDERS.intersection(relative[:-1]):
            yield path


def read(path):
    return path.read_text(encoding='utf-8', errors='replace')


def run_child(*arguments):
    environment = dict(os.environ, PLASMIC_NO_SERIAL='1', PYTHONIOENCODING='utf-8')
    done = subprocess.run([sys.executable, '-B', '-c', CHILD, str(TESTS), *arguments], capture_output=True, text=True,
                          encoding='utf-8', env=environment, timeout=60)
    if done.returncode:
        raise AssertionError(done.stdout + done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


def rig_folder(test):
    import shutil
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix='plasmic-port-safety-'))
    test.addCleanup(shutil.rmtree, folder, True)
    return folder


class Case(unittest.TestCase):
    def rig(self, **options):
        rig = pt.Rig(**options)
        self.addCleanup(rig.close)
        return rig


class PolicyTests(Case):
    def test_a_real_board_without_permission_is_refused_before_pyserial_is_touched(self):
        self.assertFalse(pumpboard._real_ports_allowed)
        rig = self.rig(mode='real', settings=pt.identity(pt.MEGA), port_lister=lambda: [pt.MEGA])
        rig.wait_link('forbidden')
        view = rig.board.view()
        self.assertEqual((view['mode'], view['link'], view['text']), ('real', 'forbidden', 'This execution does not allow opening of the real port.'))
        self.assertFalse(rig.board.connected)
        time.sleep(0.5)
        self.assertEqual(rig.board._attempts, 1)             # and it does not keep trying
        with self.assertRaises(pumpboard.RealPortForbidden):
            rig.board._new_link('COM91')
        self.assertEqual(pt.opened, [])                      # V7 never got as far as serial.Serial
        self.assertEqual(rig.guard.started, [])              # no guard process either
        # The configuration V7 keeps between connections still has no port in it.
        self.assertEqual(pumpboard.load_v7().config.get_config()['link']['port'], pumpboard.NOT_CHOSEN)

    def test_the_policy_alone_is_enough(self):
        """Without the kill switch (only pretended here; the variable itself stays set) the answer is the same."""
        rig = self.rig(mode='real', settings=pt.identity(pt.MEGA), port_lister=lambda: [pt.MEGA], start=False)
        rig.board._load()
        with mock.patch.object(pumpboard, 'no_serial', lambda: False):
            with self.assertRaises(pumpboard.RealPortForbidden):
                rig.board._new_link('COM91')
        self.assertEqual(pt.opened, [])
        self.assertEqual(os.environ.get('PLASMIC_NO_SERIAL'), '1')

    def test_the_kill_switch_alone_is_enough(self):
        rig = self.rig(mode='real', settings=pt.identity(pt.MEGA), port_lister=lambda: [pt.MEGA], start=False)
        rig.board._load()
        with mock.patch.object(pumpboard, '_real_ports_allowed', True):
            with self.assertRaises(pumpboard.RealPortForbidden):
                rig.board._new_link('COM91')
            # And even with both out of the way, a transport whose serial was replaced is not used.
            with mock.patch.object(pumpboard, 'no_serial', lambda: False):
                with self.assertRaises(pumpboard.RealPortForbidden):
                    rig.board._new_link('COM91')
        self.assertFalse(pumpboard._real_ports_allowed)
        self.assertEqual(pt.opened, [])

    def test_a_missing_pyserial_is_said_in_plain_words_before_anything_is_tried(self):
        rig = self.rig(mode='real', settings=pt.identity(pt.MEGA), port_lister=lambda: [pt.MEGA], start=False)
        rig.board._load()
        v7 = pumpboard.load_v7()
        with mock.patch.object(pumpboard, '_real_ports_allowed', True), mock.patch.object(pumpboard, 'no_serial', lambda: False), \
                mock.patch.object(v7, 'pyserial_ok', False):
            with self.assertRaises(pumpboard.VendorIncomplete) as caught:
                rig.board._new_link('COM91')
        self.assertEqual(str(caught.exception), 'Program folder is incomplete: missing pc\\vendor\\site\\serial.Please copy the entire folder again.')
        self.assertEqual(pt.opened, [])

    def test_the_kill_switch_holds_after_permission_was_given(self):
        """In a process of its own: the real call of the permission function, as app.main() will make it."""
        out = run_child('allowed', ALLOW)
        self.assertEqual((out['kill_switch'], out['allowed']), ('1', True))
        self.assertEqual((out['link'], out['text'], out['attempts'], out['direct']),
                         ('forbidden', 'This execution does not allow opening of the real port.', 1, 'RealPortForbidden'))
        self.assertEqual((out['opened'], out['guard'], out['pyserial_is_the_module']), ([], [], False))

    def test_the_kill_switch_replaces_pyserial_inside_v7(self):
        v7 = pumpboard.load_v7()
        self.assertTrue(pumpboard.no_serial())
        self.assertNotEqual(type(v7.transport.serial).__name__, 'module')
        self.assertIsNone(v7.transport.list_ports)
        # What a board puts there without the tripwire: an object whose Serial() raises RealPortForbidden.
        forbidden = pumpboard._Forbidden()
        with self.assertRaises(pumpboard.RealPortForbidden):
            forbidden.Serial('COM91', 115200, timeout=0.1)
        self.assertEqual(len(forbidden.attempts), 1)
        with mock.patch.object(v7.transport, 'serial', None):
            pumpboard.forbid_serial(v7)
            self.assertIsInstance(v7.transport.serial, pumpboard._Forbidden)
        tripwire = v7.transport.serial
        pumpboard.forbid_serial(v7)                          # a board that starts does not take the tripwire away
        self.assertIs(v7.transport.serial, tripwire)

    def test_the_guard_process_is_not_started_under_the_kill_switch(self):
        import io
        guard = pumpboard._Guard()
        with mock.patch.object(pumpboard.subprocess, 'Popen', side_effect=AssertionError('a test started the guard process')):
            guard.start('2341', '0042', 'TEST0000000000000091', 'unused.log')
        self.assertIsNone(guard.process)
        guard.tell('arm')                                    # with no process these do nothing
        guard.close()
        # What the program writes to a guard that is running (a stand-in for the process).
        pipe = io.BytesIO()
        pipe.close = lambda: None
        guard.process = type('Process', (), {'stdin': pipe})()
        for line in ('port COM91', 'arm', 'disarm'):
            guard.tell(line)
        guard.close(bye=True)
        self.assertEqual(pipe.getvalue(), b'port COM91\narm\ndisarm\nbye\n')
        self.assertIsNone(guard.process)
        # A board gets the real guard only in real mode without a scripted link; every Rig replaces it anyway.
        for mode, factory, kind in (('real', None, pumpboard._Guard), ('real', pt.Script('mega'), pumpboard._NullGuard),
                                    ('sim', None, pumpboard._NullGuard), ('off', None, pumpboard._NullGuard)):
            board = pumpboard.PumpBoard(rig_folder(self), mode, None, None, None, None, link_factory=factory)
            self.assertIsInstance(board._guard, kind)

    def test_the_port_list_is_the_simulated_one(self):
        self.assertEqual([row['device'] for row in pumpboard.system_ports()], ['COM91', 'COM92', 'COM93'])
        self.assertEqual([row['is_mega'] for row in pumpboard.system_ports()], [True, False, False])
        with mock.patch.object(pumpboard, '_comports', None):
            self.assertEqual(pumpboard.system_ports(), [])   # with the kill switch the real list is not even asked for
        self.assertNotIn(REAL_PORT, [row['device'] for row in pumpboard.system_ports()])

    def test_the_tripwire_catches_an_attempt_and_the_end_check_reports_it(self):
        """In a process of its own, so that this module's record stays empty."""
        out = run_child('tripwire')
        self.assertIn('a test tried to open a real serial port', out['start'])      # V7 wraps it into its own error
        self.assertEqual(out['connected'], False)
        self.assertEqual(len(out['opened']), 1)
        self.assertEqual(out['opened'][0][0][:2], ['COM91', 115200])
        self.assertEqual(out['end_check'], 'failed')         # which is why every module asserts the record at its end


class ChoiceTests(Case):
    def test_where_ports_cannot_be_opened_no_board_is_taken_either(self):
        """Nobody chooses a port any more: the one Arduino Mega of the PC is taken by itself — by a process that
        may open real ports. This one may not: it sees the board in the list and still writes and opens nothing."""
        rig = self.rig(mode='real', port_lister=lambda: [pt.MEGA])      # enabled: null, and no way to open anything
        time.sleep(0.6)
        view = rig.board.view()
        self.assertEqual((view['link'], view['text'], view['firmware_ok']), ('unset', 'Pump control board not found: please connect USB Wire', None))
        self.assertEqual(rig.board._attempts, 0)
        self.assertFalse(rig.board.in_use)
        self.assertFalse((rig.folder / 'pumpboard.json').exists())   # and nothing was written for it
        self.assertEqual(pt.opened, [])

    def test_a_board_set_to_not_used_opens_nothing(self):
        script = pt.Script('mega')
        rig = self.rig(mode='real', settings=pt.identity(pt.MEGA, enabled=False), link_factory=script,
                       port_lister=lambda: [pt.MEGA])
        time.sleep(0.6)
        self.assertEqual((rig.board.view()['link'], rig.board.view()['text']), ('disabled', 'Not used'))
        self.assertEqual((script.calls, rig.board._attempts), ([], 0))

    def test_in_off_mode_the_board_does_not_exist(self):
        rig = self.rig(mode='off', settings=pt.identity(pt.MEGA), port_lister=lambda: [pt.MEGA])
        self.assertIsNone(pumpboard.PumpBoard._live)         # it takes no part in "one board per process"
        self.assertIsNone(rig.board._thread)
        view = rig.board.view()
        self.assertEqual((view['mode'], view['link'], view['text']), ('off', 'disabled', 'Not used'))
        self.assertEqual((view['groups'], view['motors'], view['valves']), ([], [], []))
        self.assertFalse(rig.board.in_use or rig.board.connected or rig.board.motion_uncertain)
        self.assertEqual(rig.board.stop_all('stop')['sent'], False)
        self.assertEqual(rig.alarms, [])
        with self.assertRaises(pumpboard.PumpBoardError) as caught:
            rig.board.run(8, 40, 1, 10, 'manual')
        self.assertEqual((caught.exception.code, caught.exception.text), ('link', 'Pump control board is not connected (not in use)'))
        self.assertEqual(rig.board.ports(), {'simulated': False, 'ports': [], 'other_count': 0,
                                             'current': {'enabled': True, 'device': 'COM91',
                                                         'description': 'Arduino Mega 2560 (COM91)'}})
        self.assertIsInstance(rig.board.hardware(), dict)    # Recipes are still checked against hardware.yaml
        self.assertEqual(pt.opened, [])

    def test_practice_mode_never_builds_a_real_link(self):
        rig = self.rig(mode='sim', link__simulate=False, start=False)        # a wrong override must not get through
        rig.board.start()
        rig.wait_link('forbidden')
        self.assertEqual(pt.opened, [])

    def test_the_old_harness_form_of_the_application_leaves_the_board_off(self):
        if not (PC / 'application.py').is_file():
            self.skipTest('pc/application.py does not exist yet (WP5); the check must run at integration')
        sys.path.insert(0, str(PC))
        import application
        import tempfile
        folder = tempfile.mkdtemp(prefix='plasmic-port-safety-')
        self.addCleanup(shutil.rmtree, folder, True)
        # Without a config.json the program would take the RPi of installer/site.json (the real one on
        # this rig) and try to reach it. A test never does: the address below is this computer, on a
        # port where nothing listens, and a local address is never searched for on the network.
        Path(folder, 'config.json').write_text('{"rpi_host": "127.0.0.1", "rpi_port": 9, "token": ""}', encoding='utf-8')
        app = application.Application(folder)
        try:
            self.assertEqual(app.pump.view()['mode'], 'off')
            self.assertEqual(app.store.config['rpi_host'], '127.0.0.1')
        finally:
            app.close()
        self.assertEqual(pt.opened, [])


class SourceTests(unittest.TestCase):
    """What the source files say. Files of other work packages that do not exist yet are skipped."""

    def test_no_test_file_allows_real_ports(self):
        found = [path.name for path in project_files(TESTS, {'.py'}) if ALLOW + '(' in read(path)]
        self.assertEqual(found, [])

    def test_only_the_entry_point_allows_real_ports(self):
        """pc/app.py, and it calls the function in exactly one place."""
        call = re.compile(r'(?<!def )\b' + ALLOW + r'\(')
        callers = [path.relative_to(PC).as_posix() for path in project_files(PC, {'.py'}) if call.search(read(path))]
        self.assertEqual(callers, ['app.py'])
        self.assertEqual(len(re.findall(r'^def ' + ALLOW + r'\(\):', read(PC / 'pumpboard.py'), re.M)), 1)
        self.assertEqual(len(call.findall(read(PC / 'app.py'))), 1)

    def test_only_the_wrapper_and_the_guard_load_pyserial(self):
        pattern = re.compile(r'^\s*(?:import\s+serial\b|from\s+serial\b)', re.M)
        users = [path.relative_to(PC).as_posix() for path in project_files(PC, {'.py'}) if pattern.search(read(path))]
        self.assertEqual(users, ['pump_guard.py', 'pumpboard.py'])
        # And the vendored V7 code is loaded by the wrapper only.
        backend = re.compile(r'^\s*(?:import\s+backend\b|from\s+backend\b|import\s+yaml\b|from\s+yaml\b)', re.M)
        users = [path.relative_to(PC).as_posix() for path in project_files(PC, {'.py'}) if backend.search(read(path))]
        self.assertEqual(users, ['pumpboard.py'])

    def test_no_file_names_the_real_port(self):
        found = []
        for folder in (PC, TESTS):
            for path in project_files(folder, TEXT_SUFFIXES):
                name = path.relative_to(ROOT).as_posix()
                if REAL_PORT in read(path) and name not in PORT_NAME_EXCEPTIONS:
                    found.append(name)
        self.assertEqual(found, [])
        # The exception really is only that one command line.
        for name in PORT_NAME_EXCEPTIONS:
            lines = [line for line in read(ROOT / name).splitlines() if REAL_PORT in line]
            self.assertEqual(len(lines), 1, name)
            self.assertIn('avrdude_command(', lines[0])

    def test_the_application_passes_no_test_seam_to_the_board(self):
        path = PC / 'application.py'
        if not path.is_file():
            self.skipTest('pc/application.py does not exist yet (WP5); the check must run at integration')
        text = read(path)
        for seam in ('link_factory', 'port_lister'):
            self.assertNotIn(seam, text, seam)
        calls = re.findall(r'PumpBoard\(([^)]*)\)', text, re.S)
        self.assertEqual(len(calls), 1)
        self.assertNotRegex(calls[0], r'overrides\s*=\s*(?!pump_overrides\b)')     # only what the caller of Application gave

    def test_the_helper_sets_the_kill_switch_before_it_loads_anything(self):
        code = [line.split('#')[0].strip() for line in read(TESTS / 'pump_testing.py').splitlines()
                if line.startswith(('import ', 'from ', 'os.environ'))]
        self.assertEqual(code[:3], ['from __future__ import annotations', 'import os', "os.environ['PLASMIC_NO_SERIAL'] = '1'"])
        for name in ('test_pumpboard.py', 'test_port_safety.py'):
            text = read(TESTS / name)
            self.assertLess(text.index('import pump_testing'), text.index('import pumpboard'), name)
        self.assertIn("os.environ['PLASMIC_NO_SERIAL'] = '1'", read(TESTS / 'test_pump_guard.py'))

    def test_our_files_are_utf8_with_unix_line_ends(self):
        for path in (PC / 'pumpboard.py', PC / 'pump_guard.py', TESTS / 'pump_testing.py', TESTS / 'test_pumpboard.py',
                     TESTS / 'test_pump_guard.py', TESTS / 'test_port_safety.py'):
            data = path.read_bytes()
            self.assertNotIn(b'\r', data, path.name)
            self.assertFalse(data.startswith(b'\xef\xbb\xbf'), path.name)
            data.decode('utf-8')


if __name__ == '__main__':
    unittest.main(verbosity=2)
