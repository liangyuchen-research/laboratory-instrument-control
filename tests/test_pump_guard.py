"""pc/pump_guard.py (Automatic test of the guard program of the pump control board).

only uses ”simulated serial module test: put the simulated module in sys.modules,and then call directly pump_guard.run().
Guard programs are never started as programs; true pyserial will not be loaded, nor will any real ports be opened or listed.

    python tests/test_pump_guard.py
"""
from __future__ import annotations

import os

os.environ['PLASMIC_NO_SERIAL'] = '1'      # stays set for the whole process

import io  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import types  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pc'))

import pump_guard  # noqa: E402

MEGA = types.SimpleNamespace(device='COM91', description='Arduino Mega 2560 (COM91)', vid=0x2341, pid=0x0042,
                             serial_number='TEST0000000000000091')
ARGS = ['--vid', '2341', '--pid', '0042', '--serial', 'TEST0000000000000091']


class Bench:
    """A simulated pyserial (serial, serial.tools, serial.tools.list_ports) on a clock of its own."""

    def __init__(self, ports, busy_for=0.0, answers=True, write_fails=False):
        self.ports, self.busy_until, self.answers, self.write_fails = list(ports), busy_for, answers, write_fails
        self.clock = 0.0
        self.listed = 0
        self.attempts = []           # the clock at every Serial(...) call
        self.opened = []             # (port, baud, timeout) of every successful open
        self.written = []            # (clock, bytes)
        self.closed = 0
        bench = self

        class Serial:
            def __init__(self, port, baudrate, timeout=None):
                bench.attempts.append(bench.clock)
                if bench.clock < bench.busy_until:
                    raise OSError("could not open port %r: PermissionError(13, 'access denied', None, 5)" % port)
                bench.opened.append((port, baudrate, timeout))
                self.timeout = timeout or 0.1
                self.pending = []

            def write(self, data):
                if bench.write_fails:
                    raise OSError('write failed')
                bench.written.append((bench.clock, data))
                if data == b'X\n' and bench.answers:
                    self.pending.append(b'WEIGHT,0.000,RAW,0.0,ST,1\r\n')
                    self.pending.append(b'OK,all_stopped\r\n')

            def flush(self):
                pass

            def readline(self):
                if self.pending:
                    return self.pending.pop(0)
                bench.clock += self.timeout          # a real read waits for its timeout
                return b''

            def close(self):
                bench.closed += 1

        def comports():
            bench.listed += 1
            return list(bench.ports)

        serial = types.ModuleType('serial')
        tools = types.ModuleType('serial.tools')
        list_ports = types.ModuleType('serial.tools.list_ports')
        serial.Serial, serial.tools = Serial, tools
        tools.list_ports = list_ports
        list_ports.comports = comports
        self.modules = {'serial': serial, 'serial.tools': tools, 'serial.tools.list_ports': list_ports}

    def sleep(self, seconds):
        self.clock += seconds

    def now(self):
        return self.clock


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='plasmic-guard-'))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.log = self.folder / 'logs' / 'guard.log'

    def run_guard(self, bench, said, environ=None, args=ARGS):
        """Run the guard on what the program said before it ended (or before it died)."""
        with mock.patch.dict(sys.modules, bench.modules), \
                mock.patch.object(pump_guard, '_sleep', bench.sleep), mock.patch.object(pump_guard, '_now', bench.now):
            return pump_guard.run(list(args) + ['--log', str(self.log)], io.StringIO(said),
                                  environ={} if environ is None else environ)

    def log_text(self):
        return self.log.read_text(encoding='utf-8') if self.log.is_file() else ''

    def untouched(self, bench):
        self.assertEqual((bench.listed, bench.attempts, bench.opened, bench.written), (0, [], [], []))

    def test_bye_means_the_port_is_never_touched(self):
        for said in ('port COM91\nbye\n', 'port COM91\narm\nbye\n', 'bye\n'):
            bench = Bench([MEGA])
            self.assertEqual(self.run_guard(bench, said), 0, said)
            self.untouched(bench)
        self.assertEqual(self.log_text(), '')

    def test_the_program_gone_while_nothing_ran_means_the_port_is_never_touched(self):
        for said in ('', 'port COM91\n', 'port COM91\narm\ndisarm\n', 'port COM91\narm\ndisarm\narm\ndisarm\n'):
            bench = Bench([MEGA])
            self.assertEqual(self.run_guard(bench, said), 0, said)
            self.untouched(bench)
        self.assertEqual(self.log_text(), '')

    def test_the_program_gone_while_a_pump_ran_opens_the_port_and_sends_x(self):
        bench = Bench([MEGA])
        self.assertEqual(self.run_guard(bench, 'port COM91\narm\n'), 0)
        self.assertEqual(bench.opened, [('COM91', 115200, pump_guard.READ_TIMEOUT_S)])
        self.assertEqual([data for _, data in bench.written], [b'X\n'])
        # It waited before listing, and it let the board boot before it wrote (opening restarts the board).
        self.assertAlmostEqual(bench.attempts[0], pump_guard.SETTLE_S)
        self.assertGreaterEqual(bench.written[0][0], bench.attempts[0] + pump_guard.BOOT_S)
        self.assertEqual(bench.closed, 1)
        text = self.log_text()
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn('COM91', text)
        self.assertIn('OK,all_stopped', text)

    def test_binary_input_and_a_changed_port_are_understood(self):
        bench = Bench([MEGA, types.SimpleNamespace(device='COM95', description='Arduino Mega 2560 (COM95)', vid=0x2341,
                                                   pid=0x0042, serial_number='TEST0000000000000091')])
        with mock.patch.dict(sys.modules, bench.modules), \
                mock.patch.object(pump_guard, '_sleep', bench.sleep), mock.patch.object(pump_guard, '_now', bench.now):
            code = pump_guard.run(ARGS + ['--log', str(self.log)], io.BytesIO(b'port COM91\r\narm\r\nport COM95\r\n'),
                                  environ={})
        self.assertEqual(code, 0)
        self.assertEqual([port for port, _, _ in bench.opened], ['COM95'])

    def test_a_port_that_is_not_the_board_any_more_is_not_opened(self):
        ch340 = types.SimpleNamespace(device='COM91', description='USB-SERIAL CH340 (COM91)', vid=0x1A86, pid=0x7523,
                                      serial_number=None)
        other = types.SimpleNamespace(device='COM91', description='Arduino Mega 2560 (COM91)', vid=0x2341, pid=0x0042,
                                      serial_number='SOMEBODY-ELSES-BOARD')
        plain = types.SimpleNamespace(device='COM91', description='Communications Port (COM91)', vid=None, pid=None,
                                      serial_number=None)
        elsewhere = types.SimpleNamespace(device='COM96', description='Arduino Mega 2560 (COM96)', vid=0x2341,
                                          pid=0x0042, serial_number='TEST0000000000000091')
        for ports in ([ch340], [other], [plain], [elsewhere], []):
            bench = Bench(ports)
            self.assertEqual(self.run_guard(bench, 'port COM91\narm\n'), 2)
            self.assertEqual((bench.listed, bench.attempts, bench.opened, bench.written), (1, [], [], []))
        self.assertEqual(len(self.log_text().splitlines()), 5)
        self.assertIn('not opened', self.log_text())
        # Armed without ever having been told a port: nothing to open either.
        bench = Bench([MEGA])
        self.assertEqual(self.run_guard(bench, 'arm\n'), 2)
        self.assertEqual((bench.attempts, bench.opened), ([], []))

    def test_an_identity_that_is_not_an_arduino_mega_is_never_opened(self):
        ch340 = types.SimpleNamespace(device='COM91', description='USB-SERIAL CH340 (COM91)', vid=0x1A86, pid=0x7523,
                                      serial_number=None)
        bench = Bench([ch340])
        self.assertEqual(self.run_guard(bench, 'port COM91\narm\n', args=['--vid', '1A86', '--pid', '7523']), 2)
        self.assertEqual((bench.attempts, bench.opened), ([], []))

    def test_a_busy_port_is_tried_again_and_given_up_after_ten_seconds(self):
        bench = Bench([MEGA], busy_for=10_000)
        self.assertEqual(self.run_guard(bench, 'port COM91\narm\n'), 3)
        self.assertEqual((bench.opened, bench.written), ([], []))
        self.assertGreater(len(bench.attempts), 10)
        self.assertGreaterEqual(bench.attempts[-1] - bench.attempts[0], pump_guard.OPEN_RETRY_S)
        self.assertLess(bench.attempts[-1] - bench.attempts[0], pump_guard.OPEN_RETRY_S + 2 * pump_guard.OPEN_PAUSE_S)
        text = self.log_text()
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn('X NOT sent', text)

    def test_a_port_that_becomes_free_is_opened(self):
        bench = Bench([MEGA], busy_for=4.0)          # the dead program's handle is released after 4 s
        self.assertEqual(self.run_guard(bench, 'port COM91\narm\n'), 0)
        self.assertGreater(len(bench.attempts), 3)
        self.assertEqual([data for _, data in bench.written], [b'X\n'])
        self.assertIn('OK,all_stopped', self.log_text())

    def test_no_answer_to_x_is_logged_as_such(self):
        bench = Bench([MEGA], answers=False)
        self.assertEqual(self.run_guard(bench, 'port COM91\narm\n'), 5)
        self.assertEqual([data for _, data in bench.written], [b'X\n'])
        self.assertEqual(bench.closed, 1)
        self.assertIn('no answer', self.log_text())

    def test_a_failing_write_still_closes_the_port(self):
        bench = Bench([MEGA], write_fails=True)
        self.assertEqual(self.run_guard(bench, 'port COM91\narm\n'), 4)
        self.assertEqual(bench.closed, 1)
        self.assertIn('failed', self.log_text())

    def test_the_kill_switch_ends_it_at_once(self):
        bench = Bench([MEGA])
        said = io.StringIO('port COM91\narm\n')
        with mock.patch.dict(sys.modules, bench.modules):
            code = pump_guard.run(ARGS + ['--log', str(self.log)], said, environ={'PLASMIC_NO_SERIAL': '1'})
        self.assertEqual(code, 0)
        self.untouched(bench)
        self.assertEqual(said.tell(), 0)             # it did not even listen
        self.assertIn('PLASMIC_NO_SERIAL', self.log_text())
        # Without an explicit environment it reads the real one, where the switch is set for every test.
        bench = Bench([MEGA])
        with mock.patch.dict(sys.modules, bench.modules):
            self.assertEqual(pump_guard.run(ARGS, io.StringIO('port COM91\narm\n')), 0)
        self.untouched(bench)


class SourceTests(unittest.TestCase):
    def test_the_guard_loads_nothing_but_pyserial_and_names_no_real_port(self):
        text = (ROOT / 'pc' / 'pump_guard.py').read_text(encoding='utf-8')
        imports = sorted({line.split()[1].split('.')[0] for line in text.splitlines()
                          if line.startswith(('import ', 'from ')) or line.startswith(('    import ', '    from '))})
        self.assertEqual(imports, ['__future__', 'argparse', 'datetime', 'os', 'pathlib', 'serial', 'sys', 'time'])
        self.assertNotIn('COM' + '3', text)
        self.assertNotIn('\r', text)

    def test_the_program_and_the_guard_agree_on_the_board_and_on_the_words(self):
        board = (ROOT / 'pc' / 'pumpboard.py').read_text(encoding='utf-8')
        for word in ("'port %s'", "'arm'", "'disarm'", "'bye'", "'--vid'", "'--pid'", "'--serial'", "'--log'"):
            self.assertIn(word, board, word)
        self.assertEqual(pump_guard.MEGA_USB_IDS, frozenset({('2341', '0042'), ('2A03', '0042')}))
        self.assertEqual(pump_guard.BAUD, 115200)


def tearDownModule():
    # The real pyserial was never loaded here, so nothing in this process could open or list a port.
    assert 'serial' not in sys.modules and 'serial.serialwin32' not in sys.modules, 'pyserial was loaded by a guard test'
    assert os.environ.get('PLASMIC_NO_SERIAL') == '1'


if __name__ == '__main__':
    unittest.main(verbosity=2)
