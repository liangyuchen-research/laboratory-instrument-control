"""pc/pumpboard.py (Automatic testing of the packaging layer of the pump control board).

Use all V7 your own emulator, or use a simulated port (pump_testing.Script) .will not open or list any real
port:pump_testing Set up as soon as you import it PLASMIC_NO_SERIAL=1,and install the tripwire of ”logging and reporting errors as soon as the port is opened”.

    python tests/test_pumpboard.py
    python tests/test_pumpboard.py DoseTests
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pump_testing as pt  # noqa: E402  (sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import pumpboard  # noqa: E402
import megasim  # noqa: E402
from pumpboard import PumpBoardError  # noqa: E402

HARDWARE_YAML = pt.VENDOR / 'v7' / 'config' / 'hardware.yaml'
YAML_HASH = None

# What a connect writes, in this order (SPEC B.3.3 / B.3.4): V7's own `?`, the identity check, X first.
CONNECT_TRAFFIC = ['?', '?', 'X', 'W,STREAM,0', '?', 'W,DIV,287703.031', 'W,OFFSET,-1', 'W,AVG,1', 'W,STABLE,0.015',
                   'W,STREAM,10', '?', 'Y,SYR,5000', '?', 'Y,DETECT']

# bootstrap.hardware exactly as written in API.md C.3.1, plus the three numbers of amendment M10
# (pulses_per_ml, dose_pulses_per_ml, syringe.full_stroke_steps: the Recipe module's dose estimate reads them).
# AMENDMENTS M20: what each pump carries (`use`), the two pumps of the cleaning and the valves are this program's
# setup. Nothing saved: V7's names and cleaning pumps, and no valve in use (the person's decision of 2026-10-07).
API_HARDWARE = {
    'groups': [{'id': 'A', 'label': 'A Group', 'motors': [{'id': 2, 'label': 'Pump 1 · water', 'fluid': 'water', 'use': 'water', 'valve': None},
                                                      {'id': 3, 'label': 'Pump 2', 'fluid': None, 'use': 'none', 'valve': None},
                                                      {'id': 4, 'label': 'Pump 3', 'fluid': None, 'use': 'none', 'valve': None},
                                                      {'id': 5, 'label': 'Pump 4', 'fluid': None, 'use': 'none', 'valve': None}]},
               {'id': 'B', 'label': 'B Group', 'motors': [{'id': 6, 'label': 'Pump 5', 'fluid': None, 'use': 'none', 'valve': None},
                                                      {'id': 7, 'label': 'Pump 6', 'fluid': None, 'use': 'none', 'valve': None},
                                                      {'id': 8, 'label': 'Pump 7 · OUT', 'fluid': 'OUT', 'use': 'out', 'valve': None},
                                                      {'id': 9, 'label': 'Pump 8 · CuSO₄', 'fluid': 'CuSO₄', 'use': 'chemical',
                                                       'valve': None}]}],
    'valves': [{'id': 1, 'label': 'solenoid valve 1', 'motor': None}, {'id': 2, 'label': 'solenoid valve 2', 'motor': None}],
    'valve_open_before_ms': 1000, 'valve_close_after_ms': 1000,
    'max_flow_ml_min': 80, 'default_flow_ml_min': 40, 'dose_flow_ml_min': 40,
    'pulses_per_ml': 4237.00761337, 'dose_pulses_per_ml': 4558.96147256,
    'gravimetric': {'enabled': True, 'fast_flow_ml_min': 40, 'approach_flow_ml_min': 10, 'trim_flow_ml_min': 3,
                    'tolerance_percent': 0.5, 'timeout_s': 300},
    'cleaning': {'water_motor': 2, 'out_motor': 8, 'flow_ml_min': 40, 'water_lead_s': 30, 'overlap_s': 45,
                 'out_tail_s': 45, 'total_s': 120},
    'syringe': {'options_ul': [5000, 1250], 'valve_positions': 3, 'full_stroke_steps': 12000},
    'scale': {'decimals': 3, 'calibration_presets_g': [10, 50, 100, 200], 'default_calibration_g': 100},
    'rev': 1}
# What a board whose firmware cannot be told (V7's own, mega/ p1) does: V7's valve links, and the valve times in the cleaning.
V7_LINKS = {2: 1, 9: 2}

# The keys of state.pump in API.md C.6.
VIEW_KEYS = {'mode', 'link', 'text', 'device', 'description', 'error', 'since', 'generation', 'banner_seen', 'firmware_ok',
             'firmware_match', 'firmware_tag', 'hardware_rev', 'motion_uncertain', 'cleaning', 'clean_phase', 'groups', 'motors',
             'valves', 'syringe',
             'scale', 'op', 'weight_dose', 'last_result', 'settings'}
MOTOR_KEYS = {'id', 'label', 'fluid', 'group', 'valve', 'state', 'flow_ml_min', 'direction', 'left_s', 'by', 'blocked'}
SYRINGE_KEYS = {'online', 'initialised', 'busy', 'size_ul', 'position', 'volume_ul', 'speed_ml_s', 'max_speed_ml_s', 'error'}
SCALE_KEYS = {'online', 'grams', 'stable', 'age_s', 'calibrated', 'calibrated_at', 'reference_g'}
OP_KEYS = {'id', 'kind', 'motor', 'by', 'started_at', 'eta_s', 'elapsed_s', 'text'}

T_LOST = 'The pump control board is lost and the stop command cannot be sent. The pump may still be spinning.'
T_BUSY = 'The pump control board is performing another action'
T_HV = 'The high pressure has not been turned off and the pump cannot be started.'


def state_line(a=-1, sa='idle', b=-1, sb='idle', valves='00', cal='4237.01', stream=10, cleaning=0):
    return ('STATE,a=%s,sa=%s,b=%s,sb=%s,pump=unknown,proto=ascii,baud=9600,addr=0,syringe_uL=5000,valves=%s,'
            'cal_pulses_per_ml=%s,max_flow_uL_min=80000,w_stream_hz=%s,cleaning=%s' % (a, sa, b, sb, valves, cal, stream, cleaning))


def setUpModule():
    global YAML_HASH
    pt.install_tripwire()
    YAML_HASH = pt.sha256(HARDWARE_YAML)


def tearDownModule():
    assert pumpboard.PumpBoard._live is None, 'a test left a board running'
    assert pt.sha256(HARDWARE_YAML) == YAML_HASH, 'the vendored hardware.yaml was changed by a test'
    manifest = (pt.VENDOR / 'MANIFEST.sha256').read_text(encoding='utf-8')
    assert YAML_HASH in manifest, 'the vendored hardware.yaml is not the one of the manifest'
    assert not [item for item in pt.VENDOR.rglob('__pycache__')], 'bytecode was written into pc/vendor'
    assert pumpboard._real_ports_allowed is False
    pt.assert_nothing_opened()


class Case(unittest.TestCase):
    def rig(self, **options):
        rig = pt.Rig(**options)
        self.addCleanup(rig.close)
        return rig

    def plan(self):
        plan = pt.FaultPlan()
        self.addCleanup(plan.restore)
        return plan

    def patched(self, **values):
        context = pt.patched(**values)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def refused(self, code, text, call, *args):
        with self.assertRaises(PumpBoardError) as caught:
            call(*args)
        self.assertEqual((caught.exception.code, caught.exception.text), (code, text))

    def background(self, call, *args):
        """A blocking operation on a thread of its own. The box gets 'result' or 'error' (code, text)."""
        box = {}

        def work():
            try:
                box['result'] = call(*args)
            except PumpBoardError as exc:
                box['error'] = (exc.code, exc.text)
            except BaseException as exc:      # shown by the assertion that looks into the box
                box['crash'] = repr(exc)

        box['thread'] = threading.Thread(target=work, daemon=True)
        box['thread'].start()
        return box

    def finished(self, box, timeout=8.0):
        box['thread'].join(timeout)
        self.assertFalse(box['thread'].is_alive(), 'the operation did not end')
        self.assertNotIn('crash', box, box.get('crash'))
        return box


class LoaderTests(Case):
    def test_the_board_starts_on_the_simulator_and_says_so(self):
        rig = self.rig()
        view = rig.board.view()
        self.assertEqual((view['mode'], view['link'], view['text'], view['device']), ('sim', 'connected', 'simulation', 'simulation'))
        self.assertTrue(rig.raw.simulated)
        self.assertEqual(rig.events, [('Pump control board wired (analog)', 'info')])
        self.assertTrue(rig.board.in_use and rig.board.connected and not rig.board.motion_uncertain)
        self.assertEqual(rig.board.ports(), {'simulated': True, 'ports': [], 'other_count': 0,
                                             'current': {'enabled': True, 'device': 'simulation', 'description': 'built-in analog control board'}})
        with self.assertRaises(ValueError) as caught:
            rig.board.configure(True, 'COM91')
        self.assertEqual(str(caught.exception), 'Simulation mode uses the built-in simulation control board')
        self.assertEqual(rig.guard.started, [])            # no guard process in practice mode

    def test_events_go_to_the_store_when_no_callback_is_given(self):
        rig = self.rig(store_events=True)
        self.assertEqual(rig.store.events, [{'text': 'Pump control board wired (analog)', 'level': 'info', 'actor': 'system'}])

    def test_the_cached_configuration_never_holds_a_usable_port(self):
        v7 = pumpboard.load_v7()
        self.assertEqual(v7.config.get_config()['link']['port'], pumpboard.NOT_CHOSEN)
        with mock.patch.object(v7.transport, 'get_link', side_effect=AssertionError('get_link() must never be called')):
            rig = self.rig()
            self.assertEqual(v7.config.get_config()['link']['port'], pumpboard.NOT_CHOSEN)
            self.assertEqual(rig.raw._link_cfg['port'], 'simulator')
            generation = rig.board._generation
            self.assertEqual(rig.board.reconnect(), {'accepted': True})
            rig.wait(lambda: rig.board._generation > generation and rig.board.connected)
            rig.board.hardware()
            rig.board.view()
            self.assertEqual(v7.config.get_config()['link']['port'], pumpboard.NOT_CHOSEN)
        self.assertNotEqual(str(v7.config.get_config()['link']['port']).lower(), 'auto')
        self.assertIsNone(v7.transport._link)              # V7's process-wide link was never made

    def test_two_links_do_not_share_their_configuration(self):
        rig = self.rig()
        first, generation = rig.raw, rig.board._generation
        rig.board.reconnect()
        rig.wait(lambda: rig.board._generation > generation and rig.board.connected)
        second = rig.raw
        self.assertIsNot(first, second)
        self.assertIsNot(first.cfg, second.cfg)
        self.assertIsNot(first._link_cfg, second._link_cfg)
        second._link_cfg['port'] = 'changed'
        self.assertEqual(first._link_cfg['port'], 'simulator')
        self.assertFalse(first.connected)                  # the old link was closed
        self.assertEqual(rig.tx()[-14:], CONNECT_TRAFFIC)
        self.assertIn('X', rig.tx()[14:-14])               # and it was sent X before it was closed

    def test_only_one_board_can_be_live(self):
        rig = self.rig()
        other = pumpboard.PumpBoard(rig.folder, 'sim', rig.store, None, None, None)
        with self.assertRaises(RuntimeError):
            other.start()
        other.close()                                      # closing the one that never started frees nothing
        self.assertIs(pumpboard.PumpBoard._live, rig.board)
        self.assertTrue(rig.board.connected)
        rig.board.close()
        self.assertIsNone(pumpboard.PumpBoard._live)
        again = self.rig()
        self.assertTrue(again.board.connected)

    def test_the_parts_of_v7_the_wrapper_relies_on_are_still_there(self):
        source = (pt.VENDOR / 'v7' / 'backend' / 'transport.py').read_text(encoding='utf-8')
        for text in (pumpboard.V7_TIMEOUT, pumpboard.V7_WRITE_FAILED, pumpboard.V7_NOT_CONNECTED):
            self.assertIn(text, source)
        v7 = pumpboard.load_v7()
        for name in ('_cancel_weight_dose', '_reject_during_weight_dose', '_check_flow', 'flow_to_sps'):
            self.assertTrue(callable(getattr(v7.motors, name)), name)
        self.assertEqual([param.name for param in v7.registry.get('motors.run').params], ['motor', 'flow_ml_min', 'direction'])
        self.assertEqual(len(v7.registry.manifest()), 26)
        self.assertTrue(issubclass(v7.LinkTrouble, v7.LinkError) and issubclass(v7.SyringeFault, v7.LinkTrouble))
        self.assertNotIsInstance(v7.transport.serial, type(sys))      # not the pyserial module: the tripwire

    def test_hardware_is_the_shape_of_the_api_document(self):
        folder = Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.addCleanup(shutil.rmtree, folder, True)
        board = pumpboard.PumpBoard(folder, 'off', pt.StubStore(folder), None, None, None)
        # AMENDMENTS M16: every motor also has its number on the screen (Pump 1-8, in the order of the wiring),
        # the short name, and its steps per mL (its own after a calibration, else the YAML's).
        expected = copy.deepcopy(API_HARDWARE)
        factor = pumpboard.load_v7().config.get_config()['motors']['dose_pulses_per_ml']
        for index, motor in enumerate(motor for group in expected['groups'] for motor in group['motors']):
            motor.update(pump=index + 1, short='Pump %d' % (index + 1), dose_pulses_per_ml=factor, factory_pulses_per_ml=factor)
        self.assertEqual(board.hardware(), expected)
        self.assertEqual([motor['label'] for group in expected['groups'] for motor in group['motors']],
                         ['Pump 1 · water', 'Pump 2', 'Pump 3', 'Pump 4', 'Pump 5', 'Pump 6', 'Pump 7 · OUT', 'Pump 8 · CuSO₄'])
        text = json.dumps(board.hardware(), ensure_ascii=False)
        self.assertNotRegex(text, r'\d\.0[,}\]]')          # whole numbers are sent as whole numbers
        # Amendment M10: the two step factors and the syringe's stroke, as the vendored YAML has them.
        hardware, yaml = board.hardware(), pumpboard.load_v7().config.get_config()
        self.assertEqual((hardware['pulses_per_ml'], hardware['dose_pulses_per_ml'], hardware['syringe']['full_stroke_steps']),
                         (yaml['motors']['pulses_per_ml'], yaml['motors']['dose_pulses_per_ml'],
                          yaml['syringe_pump']['full_stroke_steps']))
        self.assertEqual((hardware['pulses_per_ml'], hardware['dose_pulses_per_ml'], hardware['syringe']['full_stroke_steps']),
                         (4237.00761337, 4558.96147256, 12000))
        self.assertIs(type(hardware['syringe']['full_stroke_steps']), int)
        self.assertEqual((board.facts()['pulses_per_ml'], board.facts()['dose_pulses_per_ml']),
                         (hardware['pulses_per_ml'], hardware['dose_pulses_per_ml']))
        hardware['syringe']['full_stroke_steps'] = 1         # what is handed out is a copy
        self.assertEqual(board.hardware()['syringe']['full_stroke_steps'], 12000)
        self.assertEqual(sorted(board.facts()), ['device', 'dose_flow_ml_min', 'dose_pulses_per_ml', 'firmware_banner',
                                                 'firmware_match', 'firmware_tag', 'hardware_sha256', 'pulses_per_ml', 'pump_dose_pulses_per_ml',
                                                 'scale_calibration',
                                                 'simulated', 'syringe_speed_ml_s', 'syringe_ul'])
        facts = board.facts()
        self.assertEqual((facts['pulses_per_ml'], facts['dose_pulses_per_ml'], facts['hardware_sha256']),
                         (4237.00761337, 4558.96147256, pt.sha256(HARDWARE_YAML)))
        board.close()

    def test_the_view_has_the_keys_of_the_api_document(self):
        rig = self.rig()
        view = rig.board.view()
        self.assertEqual(set(view), VIEW_KEYS)
        self.assertEqual([row['id'] for row in view['motors']], [2, 3, 4, 5, 6, 7, 8, 9])
        for row in view['motors']:
            self.assertEqual(set(row), MOTOR_KEYS)
            self.assertEqual((row['state'], row['flow_ml_min'], row['left_s'], row['by'], row['blocked']),
                             ('off', None, None, None, ''))
        self.assertEqual(view['groups'], [{'id': 'A', 'label': 'A Group', 'selected': None, 'state': 'idle'},
                                          {'id': 'B', 'label': 'B Group', 'selected': None, 'state': 'idle'}])
        self.assertEqual(view['valves'], [{'id': 1, 'label': 'solenoid valve 1 · water', 'open': False},
                                          {'id': 2, 'label': 'solenoid valve 2 · CuSO₄', 'open': False}])
        self.assertEqual(set(view['syringe']), SYRINGE_KEYS)
        self.assertEqual(set(view['scale']), SCALE_KEYS)
        self.assertEqual(view['settings'], {'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4})
        self.assertEqual((view['cleaning'], view['clean_phase'], view['op'], view['weight_dose'], view['last_result']),
                         (False, '', None, None, None))
        self.assertEqual((view['banner_seen'], view['firmware_ok'], view['firmware_match'], view['motion_uncertain']),
                         (True, True, True, False))
        json.dumps(view)
        # The view is a copy: changing it changes nothing in the link's own state.
        view['groups'][0]['state'] = 'run'
        self.assertEqual(rig.raw.state['motors']['A']['state'], 'idle')
        # Looking never sends anything to the board.
        sent = rig.tx()
        for _ in range(50):
            rig.board.view()
            rig.board.hardware()
            rig.board.facts()
        self.assertEqual(rig.tx(), sent)
        facts = rig.board.facts()
        self.assertEqual((facts['simulated'], facts['device'], facts['firmware_banner'], facts['firmware_match']),
                         (True, 'simulation', pt.READY, True))
        self.assertEqual(facts['scale_calibration']['divisor'], 287703.03125)


class PortChoiceTests(Case):
    """Real mode. The link is V7's own with a scripted device behind it; nothing real is opened or listed."""

    def real(self, ports, settings=None, kind='mega', open_error=None, **options):
        self.ports = list(ports)
        script = pt.Script(kind, open_error)
        rig = self.rig(mode='real', settings=settings, link_factory=script, port_lister=lambda: list(self.ports), **options)
        return rig, script

    def saved(self, rig):
        return json.loads((rig.folder / 'pumpboard.json').read_text(encoding='utf-8'))

    def test_only_arduino_mega_ports_are_offered(self):
        rig, script = self.real([pt.MEGA, pt.CH340, pt.PLAIN, pt.CLONE])
        listed = rig.board.ports()
        self.assertEqual([row['device'] for row in listed['ports']], ['COM91', 'COM94'])
        self.assertEqual((listed['other_count'], listed['simulated']), (2, False))
        self.assertEqual(listed['current'], {'enabled': None, 'device': '', 'description': ''})
        self.assertEqual(listed['ports'][0], {'device': 'COM91', 'description': 'Arduino Mega 2560 (COM91)', 'vid': '2341',
                                              'pid': '0042', 'serial_number': 'TEST0000000000000091', 'chosen': False})
        for device in ('COM92', 'COM93', 'COM99', ''):       # a CH340, a port without USB id, one that is not there
            with self.assertRaises(ValueError) as caught:
                rig.board.configure(True, device)
            self.assertEqual(str(caught.exception), '%s Not now Arduino Mega (or removed). Didn’t turn it on.' % (device or '?'))
        time.sleep(0.3)
        view = rig.board.view()
        # Two Arduino Megas: nobody can know which one is the pump board, so none is opened.
        self.assertEqual((script.calls, view['link'], view['text'], rig.store.saved),
                         ([], 'unset', 'This computer has more than one connection Arduino Mega: Please go to ”Maintenance › Connect”Choose a piece', 0))
        self.assertFalse(rig.board.in_use)
        with self.assertRaises(ValueError) as caught:
            rig.board.reconnect()
        self.assertEqual(str(caught.exception), 'Pump control board not found')

    def test_the_only_arduino_mega_is_found_and_connected_by_itself(self):
        """Nobody chooses a port: one Arduino Mega on this PC is the pump board."""
        rig, script = self.real([pt.MEGA, pt.CH340, pt.PLAIN])
        rig.wait_connected()
        saved = self.saved(rig)
        self.assertEqual({key: saved[key] for key in ('enabled', 'device', 'vid', 'pid', 'serial_number', 'description',
                                                      'chosen_by')},
                         {'enabled': True, 'device': 'COM91', 'vid': '2341', 'pid': '0042',
                          'serial_number': 'TEST0000000000000091', 'description': 'Arduino Mega 2560 (COM91)',
                          'chosen_by': 'auto'})
        self.assertIn(('Locate the pump control board:COM91 (Arduino Mega 2560 (COM91)) ,Automatically set', 'info'), rig.events)
        self.assertIn(('pump control board is wired (COM91) ', 'info'), rig.events)
        view = rig.board.view()
        self.assertEqual((view['link'], view['text'], view['device']), ('connected', 'Connected COM91', 'COM91'))
        self.assertEqual(script.opened, ['COM91'])            # the CH340 and the port without an id were never touched
        self.assertEqual(script.written[:3], ['?', '?', 'X'])
        self.assertTrue(rig.board.ports()['ports'][0]['chosen'])

    def test_without_any_arduino_mega_it_waits_for_one(self):
        self.patched(RELIST_S=0.2)
        rig, script = self.real([pt.CH340, pt.PLAIN])
        time.sleep(0.5)
        view = rig.board.view()
        self.assertEqual((script.calls, view['link'], view['text'], rig.store.saved),
                         ([], 'unset', 'Pump control board not found: please connect USB Wire', 0))
        self.assertFalse(rig.board.in_use)
        self.ports.append(pt.MEGA)                            # the cable is plugged in
        rig.wait_connected()
        self.assertEqual(script.opened, ['COM91'])
        self.assertEqual(self.saved(rig)['chosen_by'], 'auto')

    def test_a_board_that_was_found_by_itself_may_be_replaced_one_chosen_by_hand_may_not(self):
        self.patched(RELIST_S=0.2)
        other = pt.simulated_port('COM96', 'Arduino Mega 2560 (COM96)', 0x2341, 0x0042, 'ANOTHER-BOARD-0002')
        rig, script = self.real([other, pt.CH340], settings=pt.identity(pt.MEGA, chosen_by='auto'))
        rig.wait_connected()
        self.assertEqual((script.opened, self.saved(rig)['serial_number']), (['COM96'], 'ANOTHER-BOARD-0002'))
        self.assertIn(('The original pump control board is no longer there; there is only one other on this computer Arduino Mega (COM96) ,has been used instead', 'warn'), rig.events)
        rig.close()
        for settings in (pt.identity(pt.MEGA, chosen_by='user'), pt.identity(pt.MEGA)):
            rig, script = self.real([other, pt.CH340], settings=settings)
            rig.wait_link('missing')
            time.sleep(0.5)
            self.assertEqual((script.calls, self.saved(rig)['serial_number']), ([], 'TEST0000000000000091'))
            rig.close()

    def test_choosing_the_port_stores_its_identity_and_connects(self):
        rig, script = self.real([pt.MEGA, pt.CLONE, pt.CH340])      # two boards: this is when somebody chooses
        time.sleep(0.3)
        self.assertEqual(script.calls, [])
        self.assertEqual(rig.board.configure(True, 'COM91'), {'saved': True})
        rig.wait_connected()
        saved = self.saved(rig)
        self.assertEqual(saved['chosen_by'], 'user')
        self.assertEqual({key: saved[key] for key in ('version', 'enabled', 'device', 'vid', 'pid', 'serial_number',
                                                      'description', 'syringe_ul', 'syringe_speed_ml_s')},
                         {'version': 1, 'enabled': True, 'device': 'COM91', 'vid': '2341', 'pid': '0042',
                          'serial_number': 'TEST0000000000000091', 'description': 'Arduino Mega 2560 (COM91)',
                          'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4})
        self.assertIn('chosen_at', saved)
        view = rig.board.view()
        self.assertEqual((view['mode'], view['link'], view['text'], view['device'], view['description']),
                         ('real', 'connected', 'Connected COM91', 'COM91', 'Arduino Mega 2560 (COM91)'))
        self.assertEqual((view['banner_seen'], view['firmware_ok'], view['firmware_match']), (True, True, True))
        self.assertIn(('pump control board is wired (COM91) ', 'info'), rig.events)
        self.assertEqual(rig.guard.started, [('2341', '0042', 'TEST0000000000000091')])
        self.assertEqual(rig.guard.lines, ['port COM91'])
        device, link = script.calls[0]
        self.assertEqual((device, link['port'], link['simulate'], link['baud'], link['boot_delay_s'],
                          link['command_timeout_s'], link['long_command_timeout_s']),
                         ('COM91', 'COM91', False, 115200, 2.5, 3.0, 6.0))
        self.assertEqual(script.written[:3], ['?', '?', 'X'])
        self.assertTrue(rig.board.ports()['ports'][0]['chosen'])
        self.assertEqual(rig.board.facts()['device'], 'COM91')
        self.assertTrue(rig.board.in_use)

    def test_a_saved_port_that_is_no_longer_an_arduino_mega_is_not_opened(self):
        impostor = pt.simulated_port('COM91', 'USB-SERIAL CH340 (COM91)', 0x1A86, 0x7523)
        rig, script = self.real([impostor], settings=pt.identity(pt.MEGA))
        rig.wait_link('missing')
        self.assertEqual(rig.board.view()['text'], 'Pump control board not found: Check USB Wire')
        time.sleep(0.3)
        self.assertEqual((script.calls, script.opened), ([], []))
        self.assertTrue(rig.board.in_use)
        self.assertFalse(rig.board.connected)
        self.refused('link', 'Pump control board not connected (pump control board not found: check USB Wire) ', rig.board.run, 8, 40, 1, 10, 'manual')
        ok, reasons = rig.board.quiescent()
        self.assertEqual((ok, reasons[0]['code']), (False, 'pump_link'))

    def test_an_identity_that_is_not_an_arduino_mega_is_never_opened(self):
        rig, script = self.real([pt.CH340], settings=pt.identity(pt.CH340))      # a hand-edited settings file
        rig.wait_link('missing')
        time.sleep(0.3)
        self.assertEqual((script.calls, script.opened), ([], []))

    def test_the_board_is_followed_to_a_new_port_name(self):
        moved = pt.simulated_port('COM95', 'Arduino Mega 2560 (COM95)', 0x2341, 0x0042, pt.MEGA.serial_number)
        stranger = pt.simulated_port('COM91', 'Arduino Mega 2560 (COM91)', 0x2341, 0x0042, 'ANOTHER-BOARD-0001')
        rig, script = self.real([stranger, moved, pt.CH340], settings=pt.identity(pt.MEGA))
        rig.wait_connected()
        self.assertEqual(script.opened, ['COM95'])           # not the other board that now has the old name
        self.assertEqual(rig.board.view()['text'], 'Connected COM95')
        self.assertIn(('The connection port of the pump control board is from COM91 becomes COM95', 'warn'), rig.events)
        saved = self.saved(rig)
        self.assertEqual((saved['device'], saved['description'], saved['serial_number']),
                         ('COM95', 'Arduino Mega 2560 (COM95)', 'TEST0000000000000091'))
        self.assertEqual(rig.guard.lines, ['port COM95'])

    def test_two_boards_that_cannot_be_told_apart_are_not_opened(self):
        self.patched(RELIST_S=0.2)
        twins = [pt.simulated_port('COM96', 'Arduino Mega 2560 (COM96)', 0x2341, 0x0042),
                 pt.simulated_port('COM97', 'Arduino Mega 2560 (COM97)', 0x2341, 0x0042)]
        chosen = pt.simulated_port('COM91', 'Arduino Mega 2560 (COM91)', 0x2341, 0x0042)      # a clone without a serial number
        rig, script = self.real(twins, settings=pt.identity(chosen))
        rig.wait_link('ambiguous')
        self.assertEqual(rig.board.view()['text'], 'Found more than one identical piece Arduino Mega.Please go to ”Maintenance › Connection” reselect.')
        time.sleep(0.5)
        self.assertEqual((script.calls, script.opened), ([], []))
        self.ports.append(chosen)                            # the stored name decides when it is there
        rig.wait_connected()
        self.assertEqual(script.opened, ['COM91'])

    def test_not_using_the_board(self):
        rig, script = self.real([pt.MEGA], settings=pt.identity(pt.MEGA))
        rig.wait_connected()
        rig.board.run(8, 40, 1, 30, 'manual')
        device = rig.sim
        self.assertEqual(rig.board.configure(False), {'saved': True})
        rig.wait_link('disabled')
        self.assertEqual(rig.board.view()['text'], 'Not used')
        self.assertFalse(rig.board.in_use)
        self.assertTrue(rig.all_idle(device))                # X before the port was closed
        self.assertEqual(script.written[script.written.index('P,8,84750,2825') + 2], 'X')
        self.assertEqual(script.closed, 1)
        saved = self.saved(rig)
        self.assertEqual((saved['enabled'], saved['device'], saved['vid']), (False, 'COM91', '2341'))
        with self.assertRaises(ValueError):
            rig.board.reconnect()
        self.assertEqual(rig.board.stop_all('stop'), {'sent': False, 'confirmed': False, 'error': 'Pump control board is not connected (not in use)'})
        self.assertNotIn('pump_stop_unconfirmed', rig.active)       # a board that is not used raises nothing

    def test_a_port_held_by_another_program(self):
        self.patched(RETRY_S=(0.2,))
        busy = "could not open port 'COM91': PermissionError(13, 'Access denied.', None, 5)"
        rig, script = self.real([pt.MEGA], settings=pt.identity(pt.MEGA), open_error=busy)
        rig.wait(lambda: rig.board.view()['error'] == busy)
        # In the first 15 s a guard of a killed earlier program may still hold the port: shown as connecting.
        view = rig.board.view()
        self.assertEqual((view['link'], view['text']), ('connecting', 'Connecting...'))
        rig.board._born -= 20
        rig.wait_link('port_busy')
        self.assertEqual(rig.board.view()['text'], 'COM91 is occupied by another program. please close Arduino IDE , or the old The instructions of V7 Windows (run.bat) .')
        tried = len(script.opened)
        rig.wait(lambda: len(script.opened) > tried)         # it keeps trying
        script.open_error = None
        rig.wait_connected()
        self.assertEqual(rig.board.view()['error'], '')

    def test_a_port_that_cannot_be_opened(self):
        self.patched(RETRY_S=(0.2,))
        gone = "could not open port 'COM91': FileNotFoundError(2, 'The system cannot find the specified file.', None, 2)"
        rig, script = self.real([pt.MEGA], settings=pt.identity(pt.MEGA), open_error=gone)
        rig.wait_link('error')
        self.assertEqual(rig.board.view()['text'], 'cannot be opened COM91: ' + gone)
        # Windows error 31 is a PermissionError too, but it does not mean "another program has it".
        script.open_error = "could not open port 'COM91': PermissionError(13, 'A device connected to the system is disabled.', None, 31)"
        rig.wait(lambda: rig.board.view()['error'] == script.open_error)
        self.assertEqual(rig.board.view()['link'], 'error')

    def test_a_missing_board_is_looked_for_again_and_nothing_is_opened_meanwhile(self):
        self.patched(RELIST_S=0.2)
        plan = self.plan()
        rig, script = self.real([], settings=pt.identity(pt.MEGA))
        rig.wait_link('missing')
        time.sleep(0.5)
        self.assertEqual((script.calls, rig.alarms), ([], []))       # never connected: no "link lost" alarm
        self.ports.append(pt.MEGA)
        rig.wait_connected()
        self.assertEqual(script.opened, ['COM91'])
        # Unplugged while it was connected and idle: an alarm of the ordinary kind, and it comes back by itself.
        self.ports.clear()
        plan.pull_cable(rig.sim.inner)
        rig.wait_link('missing')
        self.assertEqual(rig.active.get('pump_link_lost'), (3, 'The pump control board connection is interrupted and the program is reconnecting.'))
        self.assertFalse(rig.board.motion_uncertain)
        self.assertEqual(len([call for call in rig.alarms if call[:2] == ('raise', 'pump_link_lost')]), 1)
        self.ports.append(pt.MEGA)
        rig.wait_connected()
        self.assertNotIn('pump_link_lost', rig.active)
        self.assertEqual(script.opened, ['COM91', 'COM91'])
        self.assertEqual(rig.guard.lines, ['port COM91', 'port COM91'])


class IdentityTests(Case):
    WRONG = 'COM91 There is a device, but not the pump control board (correct response not received). This port has been closed.'

    def wrong(self, kind):
        self.patched(IDENTIFY_TIMEOUT_S=0.2, RELIST_S=0.2)
        self.ports = [pt.MEGA]
        script = pt.Script(kind)
        rig = self.rig(mode='real', settings=pt.identity(pt.MEGA), link_factory=script,
                       port_lister=lambda: list(self.ports), link__command_timeout_s=0.2)
        rig.wait_link('wrong_device')
        return rig, script

    def closed_and_left_alone(self, rig, script):
        view = rig.board.view()
        self.assertEqual((view['text'], view['firmware_ok']), (self.WRONG, False))
        self.assertTrue(script.written)
        self.assertEqual(set(script.written), {'?'})         # nothing but `?` was ever written to it
        self.assertEqual(script.closed, 1)                   # the port was closed
        self.assertEqual(rig.active.get('pump_wrong_device'), (2, self.WRONG))
        time.sleep(0.8)                                      # several looks at the port list later
        self.assertEqual(len(script.opened), 1)              # it was not opened again by itself
        self.assertFalse(rig.board.connected)

    def test_a_device_that_says_nothing(self):
        rig, script = self.wrong('silent')
        self.closed_and_left_alone(rig, script)
        self.assertEqual(script.written, ['?'] * 4)          # V7's own one and the three of the identity check

    def test_a_device_that_echoes(self):
        self.closed_and_left_alone(*self.wrong('echo'))

    def test_a_device_whose_state_line_is_not_the_boards(self):
        self.closed_and_left_alone(*self.wrong('short-state'))

    def test_a_board_running_another_program(self):
        rig, script = self.wrong('other-banner')             # it answers `?` like the pump board would
        self.closed_and_left_alone(rig, script)
        self.assertEqual(script.written, ['?', '?'])

    def test_a_wrong_device_is_tried_again_only_when_asked_or_replugged(self):
        rig, script = self.wrong('silent')
        rig.board.reconnect()                                # “Reconnect”
        rig.wait(lambda: len(script.opened) == 2)
        rig.wait_link('wrong_device')
        self.ports.clear()                                   # unplugged …
        rig.wait_link('missing')
        script.kind = 'mega'                                 # … and the right board is plugged in
        self.ports.append(pt.MEGA)
        rig.wait_connected()
        self.assertNotIn('pump_wrong_device', rig.active)
        self.assertEqual(rig.board.view()['firmware_ok'], True)
        self.assertEqual(len(script.opened), 3)

    def test_a_board_that_did_not_restart_is_stopped_all_the_same(self):
        script = pt.Script('mega-no-banner')
        rig = self.rig(mode='real', settings=pt.identity(pt.MEGA), link_factory=script, port_lister=lambda: [pt.MEGA])
        rig.wait_connected()
        self.assertEqual(rig.board.view()['banner_seen'], False)
        self.assertEqual(script.written[:3], ['?', '?', 'X'])
        self.assertIn('Y,SYR,5000', script.written)          # it may hold another syringe size
        self.assertNotIn('Y,INIT', script.written)
        self.assertTrue(any(kind == '--' and 'banner_seen=False' in text for kind, text in rig.log_lines()))


class ConnectTests(Case):
    def test_the_traffic_of_a_connect(self):
        rig = self.rig()
        sent = rig.tx()
        self.assertEqual(sent, CONNECT_TRAFFIC)
        named = ['X', 'W,DIV,287703.031', 'W,OFFSET,-1', 'W,AVG,1', 'W,STABLE,0.015', 'W,STREAM,10', 'Y,SYR,5000', 'Y,DETECT']
        places = [sent.index(command) for command in named]
        self.assertEqual(places, sorted(places))
        self.assertEqual(set(sent[:sent.index('X')]), {'?'})         # nothing but `?` before the identity was known
        self.assertNotIn('Y,INIT', sent)                             # never initialised by the program itself
        view = rig.board.view()
        self.assertEqual((view['syringe']['online'], view['syringe']['initialised'], view['syringe']['volume_ul'],
                          view['syringe']['position']), (True, False, None, None))
        self.assertEqual((view['scale']['calibrated'], view['scale']['calibrated_at'], view['scale']['reference_g']),
                         (True, '2026-08-26T10:16:20.306868+00:00', 200.0))
        self.assertAlmostEqual(rig.sim.scale_div, 287703.031, places=2)
        self.assertIsNone(rig.raw.state['firmware'])                 # the restart detector is armed
        rig.wait(lambda: rig.board.view()['scale']['online'])
        self.assertEqual(rig.alarms, [('clear', 'pump_link_lost', None, None, None, None),
                                      ('clear', 'pump_stop_unconfirmed', None, None, None, None),
                                      ('clear', 'pump_wrong_device', None, None, None, None)])

    def test_without_a_calibration_file_the_scale_counts_as_not_calibrated(self):
        rig = self.rig(calibration=False)
        self.assertEqual([command for command in rig.tx() if command.startswith(('W,DIV', 'W,OFFSET'))], [])
        scale = rig.board.view()['scale']
        self.assertEqual((scale['calibrated'], scale['calibrated_at'], scale['reference_g']), (False, None, None))
        self.assertIsNone(rig.board.facts()['scale_calibration']['divisor'])

    def test_a_calibration_file_that_cannot_be_read_does_not_stop_the_connect(self):
        folder = Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / 'pumpboard').mkdir()
        (folder / 'pumpboard' / 'scale_calibration.json').write_text('{"offset": "x"', encoding='utf-8')
        rig = self.rig(folder=folder, calibration=False)
        self.assertTrue(rig.board.connected)
        self.assertEqual(rig.board.view()['scale']['calibrated'], False)
        self.assertIn('Y,DETECT', rig.tx())

    def test_a_firmware_built_from_other_settings_is_reported_and_used(self):
        plan = self.plan()
        plan.answer('?', [state_line(cal='5614.04')], times=2)
        rig = self.rig()
        view = rig.board.view()
        self.assertEqual((view['link'], view['firmware_ok'], view['firmware_match']), ('connected', True, False))
        self.assertEqual(rig.active.get('firmware_mismatch'),
                         (3, 'The firmware settings of the pump control board are different from those on the computer (the firmware may be an old version). The cleaning sequence is subject to firmware.'))
        self.assertEqual(rig.board.facts()['firmware_match'], False)

    def test_the_saved_syringe_settings_are_applied_at_every_connect(self):
        rig = self.rig(settings={'version': 1, 'enabled': None, 'syringe_ul': 1250, 'syringe_speed_ml_s': 0.3})
        self.assertIn('Y,SYR,1250', rig.tx())
        self.assertEqual(rig.sim.syringe_ul, 1250)
        syringe = rig.board.view()['syringe']
        self.assertEqual((syringe['size_ul'], syringe['max_speed_ml_s'], syringe['speed_ml_s']), (1250, 0.625, 0.3))

    def test_a_board_that_does_not_report_everything_off_is_not_used(self):
        self.patched(RETRY_S=(0.6,))
        plan = self.plan()
        plan.answer('?', [state_line(a=2, sa='run', valves='10', stream=0)], skip=2)      # the `?` after X
        rig = self.rig(start=False)
        rig.board.start()
        rig.wait_link('error')
        self.assertEqual(rig.board.view()['text'], 'The pump control board did not confirm that everything has stopped and the connection has been closed.')
        self.assertEqual(rig.active.get('pump_stop_unconfirmed'), (1, 'The pump stop command was not confirmed. Immediately turn off power to the pump using the physical switch. The settings saved in'))
        self.assertFalse(rig.board.connected)
        self.assertEqual(rig.tx(), ['?', '?', 'X', 'W,STREAM,0', '?'])
        rig.wait_connected()                                 # the next attempt; the board answers properly now
        self.assertNotIn('pump_stop_unconfirmed', rig.active)

    def test_a_syringe_pump_that_is_not_there_does_not_stop_the_connect(self):
        plan = self.plan()
        plan.answer('Y,DETECT', ['ERR,pump_not_found_ascii_9600_addr0'])
        rig = self.rig()
        self.assertTrue(rig.board.connected)
        self.assertEqual(rig.board.view()['syringe']['online'], False)


class RunTests(Case):
    """A run with a time limit is the firmware's finite move `P`, not V7's endless `V` (amendment M8):
    the board itself ends it. The simulator runs such a move `simulation_speedup` times faster."""

    def no_endless_command(self, rig):
        """`V` with a speed — which runs until somebody stops it — was never written."""
        self.assertEqual([command for command in rig.tx() if command.startswith('V,') and not command.endswith(',0')], [])

    def tripped(self, rig, owner):
        found = []
        rig.wait(lambda: found.append(rig.board.deadline_tripped(owner)) or found[-1] is not None, 4)
        return found[-1]

    def hook(self, rig):
        """hook['before'] is called once just before the board writes the move of a run, hook['after'] once
        just after the move was answered."""
        real, hook = rig.board._send, {}

        def send(conn, command):
            if command.startswith('P,') and hook.get('before'):
                hook.pop('before')()
            out = real(conn, command)
            if command.startswith('P,') and hook.get('after'):
                hook.pop('after')()
            return out

        rig.board._send = send
        return hook

    def test_a_run_ends_by_itself_in_the_board(self):
        self.patched(RUN_SECOND_LINE_S=3600.0)               # the host's own deadline is an hour away
        rig = self.rig(link__simulation_speedup=1)
        sent = len(rig.tx())
        started = time.perf_counter()
        out = rig.board.run(8, 40, 1, 1, 'manual')
        self.assertEqual((out['motor'], out['sps'], out['steps'], out['limit_s']), (8, 2825, 2825, 1.0))
        self.assertEqual(rig.tx()[sent:], ['?', 'P,8,2825,2825', '?'])       # V7's speed for 40 mL/min × 1 s
        row = rig.motor(8)
        self.assertEqual((row['state'], row['flow_ml_min'], row['direction'], row['by'], row['left_s']),
                         ('run', 40, 1, 'manual', 1))        # the firmware calls it "move"; for people the pump runs
        self.assertEqual(rig.sim.mstate['B'], 'move')
        self.assertEqual(rig.board.view()['groups'][1], {'id': 'B', 'label': 'B Group', 'selected': 8, 'state': 'run'})
        self.assertGreater(rig.board._runs[8]['deadline'] - rig.board._runs[8]['ends'], 3000)
        rig.wait(lambda: rig.sim.mstate['B'] == 'idle', 3)
        took = time.perf_counter() - started
        self.assertGreaterEqual(took, 0.95)
        self.assertLess(took, 1.6)
        rig.wait(lambda: rig.board.view()['last_result'] is not None)
        self.assertEqual(rig.tx()[sent:], ['?', 'P,8,2825,2825', '?'])       # nothing was sent to stop it
        result = rig.board.view()['last_result']
        self.assertEqual((result['ok'], result['text']), (True, 'Pump 7 · OUT arrived at the setting 1 seconds, stopped automatically'))
        self.assertEqual(rig.events[1:], [('Pump 7 · OUT has automatically stopped when the set time is reached.', 'info')])
        self.assertEqual(rig.board.deadline_tripped('manual'), 'Pump 7 · OUT has automatically stopped when the set time is reached.')
        self.assertIsNone(rig.board.deadline_tripped('manual'))
        row = rig.motor(8)
        self.assertEqual((row['state'], row['by'], row['left_s'], row['flow_ml_min']), ('off', None, None, None))
        self.assertEqual(rig.guard.lines, [])                # nothing endless: the guard is not armed
        self.assertEqual(rig.board.quiescent(), (True, []))
        self.no_endless_command(rig)

    def test_a_reverse_run_in_a_round_and_what_the_round_is_told_when_it_runs_out(self):
        self.patched(RUN_SECOND_LINE_S=3600.0)
        rig = self.rig(link__simulation_speedup=1)
        out = rig.board.run(3, 20, -1, 1, 'round')
        self.assertEqual((out['sps'], out['steps']), (1412, -1412))          # the sign is the direction; the speed stays positive
        self.assertIn('P,3,-1412,1412', rig.tx())
        row = rig.motor(3)
        self.assertEqual((row['state'], row['direction'], row['by'], row['flow_ml_min']), ('run', -1, 'round', 20))
        self.assertIsNone(rig.board.deadline_tripped('round'))
        # The Recipe's pump_off should have come before the time was over: the round is told.
        self.assertEqual(self.tripped(rig, 'round'), 'Pump 2 The planned operation time has been exceeded and has automatically stopped.')
        self.assertIsNone(rig.board.deadline_tripped('round'))
        self.assertEqual(rig.sim.mstate['A'], 'idle')
        self.assertNotIn('V,3,0', rig.tx())                  # the board had stopped by itself
        self.assertIsNone(rig.board.view()['last_result'])   # that line belongs to manual operations
        self.assertEqual(rig.events, [('Pump control board wired (analog)', 'info')])
        self.assertEqual(rig.guard.lines, [])
        self.no_endless_command(rig)

    def test_the_host_stops_a_run_the_board_did_not_end(self):
        """The second line: the time is over (+ a margin) and the board still reports the motor moving."""
        self.patched(RUN_SECOND_LINE_S=0.4, STOP_IDLE_S=0.4)
        plan = self.plan()
        plan.freeze_moves = True                             # a move never reaches its end
        rig = self.rig()
        started = time.perf_counter()
        rig.board.run(8, 40, 1, 2, 'manual')                 # 0.1 s in the simulator
        rig.wait(lambda: rig.sim.mstate['B'] == 'idle', 3)
        took = time.perf_counter() - started
        self.assertGreaterEqual(took, 0.45)
        self.assertLess(took, 1.5)
        rig.wait(lambda: rig.board.view()['last_result'] is not None)
        self.assertEqual(rig.tx()[-2:], ['V,8,0', '?'])
        self.assertEqual(rig.events[1:], [('Pump 7 · OUT has automatically stopped when the set time is reached.', 'info')])
        rig.board.run(3, 40, 1, 2, 'round')
        self.assertEqual(self.tripped(rig, 'round'), 'Pump 2 The planned operation time has been exceeded and has automatically stopped.')
        self.assertIn('V,3,0', rig.tx())
        # A motor that does not obey that stop either: everything is stopped.
        plan.answer('V,6,0', ['OK'], times=9)
        stops = rig.tx().count('X')
        rig.board.run(6, 40, 1, 2, 'manual')
        rig.wait(lambda: rig.sim.mstate['B'] == 'idle', 4)
        self.assertEqual(rig.tx().count('X'), stops + 1)
        self.assertTrue(rig.all_idle())
        self.no_endless_command(rig)

    def test_a_stop_that_comes_after_the_time_was_over(self):
        """A round's pump_off that arrives late (this PC was asleep, say): the pump had stopped by itself, but
        it ran longer than the Recipe planned, so the step fails. By hand it is simply the end."""
        self.patched(RUN_SECOND_LINE_S=3600.0)
        rig = self.rig()
        rig.board.run(3, 20, 1, 2, 'round')                  # 0.1 s in the simulator
        rig.wait(lambda: rig.sim.mstate['A'] == 'idle', 3)
        self.refused('device', 'Pump 2 The planned operation time has been exceeded and has automatically stopped.', rig.board.stop_motor, 3)
        self.assertIsNone(rig.board.deadline_tripped('round'))        # told once, not again in the next round
        self.assertEqual(rig.board.stop_motor(3), {'motor': 3, 'stopped': True, 'ran_s': None})
        rig.board.run(8, 40, 1, 2, 'manual')
        rig.wait(lambda: rig.sim.mstate['B'] == 'idle', 3)
        self.assertEqual(rig.board.stop_motor(8)['stopped'], True)
        time.sleep(0.3)
        self.assertEqual(rig.events[1:], [('Pump 7 · OUT has automatically stopped when the set time is reached.', 'info')])        # written once
        self.assertEqual(rig.board.quiescent(), (True, []))
        self.no_endless_command(rig)

    def test_a_new_run_of_a_motor_voids_what_its_last_run_left_to_tell(self):
        self.patched(RUN_SECOND_LINE_S=3600.0)
        rig = self.rig()
        rig.board.run(3, 20, 1, 2, 'round')                  # 0.1 s in the simulator, and nobody stops it
        rig.wait(lambda: 3 in rig.board._tripped['round'], 3)
        rig.board.run(3, 20, 1, 300, 'manual')               # the round never asked; somebody starts that motor by hand
        self.assertIsNone(rig.board.deadline_tripped('round'))
        self.assertEqual(rig.board.stop_motor(3)['stopped'], True)   # “OFF” is not answered with the old run's overrun
        self.assertEqual(rig.board.quiescent(), (True, []))

    def test_a_run_whose_start_cannot_be_checked_is_not_left_running(self):
        self.patched(HEARTBEAT_S=3600.0, timeouts={'state': 0.3})
        rig = self.rig()
        plan = self.plan()
        plan.drop('?', skip=1)                               # the move is taken; the `?` behind it gets no answer
        sent = len(rig.tx())
        self.refused('timeout', 'Pump control board does not respond (?) ', rig.board.run, 8, 40, 1, 300, 'manual')
        self.assertEqual(rig.tx()[sent:sent + 5], ['?', 'P,8,847500,2825', '?', 'W,CFG', 'X'])
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.board._runs, {})
        self.assertTrue(rig.board.connected)
        self.assertFalse(rig.board.motion_uncertain)         # the stop was answered and checked
        self.assertEqual(rig.board.quiescent(), (True, []))
        self.assertEqual(rig.events, [('Pump control board wired (analog)', 'info')])        # and nobody is told "its time was over"

    def test_a_stop_that_comes_while_a_run_is_being_started(self):
        rig = self.rig()
        hook = self.hook(rig)
        # STOP reaches the board before the move does: the move is not left running behind it.
        hook['before'] = lambda: rig.board.stop_all('stop')
        sent = len(rig.tx())
        self.refused('stopped', 'Stopped', rig.board.run, 8, 40, 1, 300, 'manual')
        written = rig.tx()[sent:]
        self.assertLess(written.index('X'), written.index('P,8,847500,2825'))
        self.assertLess(written.index('P,8,847500,2825'), written.index('V,8,0'))
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.board._runs, {})
        # “OFF” on that row wins the same race, and is not taken for "its time was over".
        off = {}
        hook['before'] = lambda: off.update(rig.board.stop_motor(3))
        sent = len(rig.tx())
        self.refused('stopped', 'Stopped', rig.board.run, 3, 20, 1, 300, 'round')
        self.assertEqual((off['motor'], off['stopped']), (3, True))
        written = rig.tx()[sent:]
        self.assertEqual([command for command in written if command[0] in 'PVX'], ['V,3,0', 'P,3,423600,1412', 'V,3,0'])
        self.assertTrue(rig.all_idle())
        self.assertIsNone(rig.board.deadline_tripped('round'))
        self.assertEqual(rig.events, [('Pump control board wired (analog)', 'info')])
        self.assertFalse(rig.board.op_lock.locked())
        rig.board.run(3, 20, 1, 300, 'round')                # and the next start is an ordinary one
        self.assertEqual(rig.motor(3)['state'], 'run')

    def test_a_cable_pulled_just_after_the_move_was_taken(self):
        self.patched(RETRY_S=(1.5,))
        rig = self.rig()
        plan = self.plan()
        old = rig.sim
        plan.fail_starts = 1                                 # the first try to open it again fails
        self.hook(rig)['after'] = lambda: plan.pull_cable(old)
        self.refused('link', T_LOST, rig.board.run, 8, 40, 1, 300, 'round')
        self.assertTrue(rig.board.motion_uncertain)          # the board had taken the move: that pump may be turning
        self.assertEqual(rig.active.get('pump_stop_unconfirmed'), (1, 'Pump control board lost connection. The pump may still be spinning. Immediately turn off power to the pump using the physical switch.'))
        self.assertEqual(rig.guard.lines, [])                # it ends by itself in the board: nothing for the guard
        rig.wait_connected()
        self.assertFalse(rig.board.motion_uncertain)
        self.assertNotIn('pump_stop_unconfirmed', rig.active)

    def test_one_motor_per_group_and_one_of_each_group_together(self):
        rig = self.rig()
        rig.board.run(6, 40, 1, 300, 'manual')
        self.refused('refused', 'B group can only transfer one at a time (Pump 5 is still transferring)', rig.board.run, 7, 40, 1, 300, 'manual')
        self.refused('refused', 'Pump 5 is already transferring', rig.board.run, 6, 10, 1, 300, 'manual')
        # The firmware would have dropped motor 6 silently for motor 7; it was not disturbed.
        self.assertEqual((rig.sim.sel['B'], rig.sim.mstate['B']), (6, 'move'))
        self.assertEqual([command for command in rig.tx() if command.startswith('P,')], ['P,6,847500,2825'])
        rig.board.run(3, 10, 1, 300, 'round')
        self.assertEqual((rig.sim.mstate['A'], rig.sim.mstate['B']), ('move', 'move'))
        self.assertEqual((rig.motor(7)['blocked'], rig.motor(6)['blocked'], rig.motor(4)['blocked']),
                         ('B group is in use', '', 'A group is in use'))
        self.assertEqual((rig.motor(3)['state'], rig.motor(6)['state']), ('run', 'run'))
        self.assertEqual(rig.board.view()['groups'], [{'id': 'A', 'label': 'A Group', 'selected': 3, 'state': 'run'},
                                                      {'id': 'B', 'label': 'B Group', 'selected': 6, 'state': 'run'}])
        ok, reasons = rig.board.quiescent()
        self.assertEqual((ok, [reason['code'] for reason in reasons], reasons[0]['text']), (False, ['pump_moving'], 'The pump is still running'))
        self.no_endless_command(rig)

    def test_stopping_one_motor_is_checked_and_touches_nothing_else(self):
        rig = self.rig()
        rig.board.run(6, 40, 1, 300, 'manual')
        rig.board.run(3, 40, 1, 300, 'round')
        # OFF on a row that is not running: the firmware answers OK and does nothing; motor 6 goes on.
        self.assertEqual(rig.board.stop_motor(7), {'motor': 7, 'stopped': True, 'ran_s': None})
        self.assertEqual((rig.sim.sel['B'], rig.sim.mstate['B']), (6, 'move'))
        out = rig.board.stop_motor(6)
        self.assertEqual((out['motor'], out['stopped']), (6, True))
        self.assertIsInstance(out['ran_s'], float)
        self.assertEqual((rig.sim.mstate['B'], rig.sim.mstate['A']), ('idle', 'move'))
        self.assertEqual((rig.motor(6)['state'], rig.motor(3)['state'], rig.motor(3)['by']), ('off', 'run', 'round'))
        rig.board.stop_motor(3)
        self.assertEqual(rig.board.quiescent(), (True, []))
        time.sleep(0.3)
        # Stopped on purpose is not "the time ran out": no event, nothing for the round.
        self.assertEqual(rig.events, [('Pump control board wired (analog)', 'info')])
        self.assertIsNone(rig.board.deadline_tripped('round'))
        self.assertIsNone(rig.board.deadline_tripped('manual'))
        self.assertIsNone(rig.board.view()['last_result'])
        self.assertEqual(rig.guard.lines, [])
        self.refused('refused', 'does not have this pump (1) ', rig.board.stop_motor, 1)

    def test_a_motor_that_does_not_stop_brings_everything_down(self):
        self.patched(STOP_IDLE_S=0.5)
        rig = self.rig()
        plan = self.plan()
        rig.board.run(6, 40, 1, 300, 'manual')
        rig.board.run(3, 40, 1, 300, 'manual')
        plan.answer('V,6,0', ['OK'], times=9)                # answered, but not carried out
        self.refused('device', 'Pump 5 Did not stop according to the instruction, all have stopped', rig.board.stop_motor, 6)
        self.assertTrue(rig.all_idle())
        self.assertIn('X', rig.tx())
        self.assertEqual([row['state'] for row in rig.board.view()['motors']], ['off'] * 8)
        self.assertTrue(rig.board.connected)

    def test_a_linked_valve_opens_before_its_motor_and_closes_after(self):
        rig = self.rig(link__simulation_speedup=1, valves__open_before_ms=400, valves__close_after_ms=300)
        rig.board.run(2, 40, 1, 30, 'manual')
        self.assertEqual((rig.motor(2)['state'], rig.motor(2)['left_s']), ('wait', 31))   # the valve's time comes on top
        self.assertEqual(rig.board.view()['valves'][0], {'id': 1, 'label': 'solenoid valve 1 · water', 'open': True})
        self.assertEqual((rig.sim.mstate['A'], rig.sim.valves[1]), ('wait', True))
        sent = len(rig.tx())
        rig.wait(lambda: rig.motor(2)['state'] == 'run', 2)
        self.assertEqual(rig.tx()[sent:], [])                # the board said so itself; nobody asked
        self.assertEqual(rig.sim.mstate['A'], 'move')
        started = time.perf_counter()
        rig.board.stop_motor(2)
        self.assertGreaterEqual(time.perf_counter() - started, 0.25)        # it waited for the valve
        self.assertEqual(rig.sim.valves[1], False)
        self.assertEqual(rig.board.quiescent(), (True, []))

    def test_what_a_run_refuses(self):
        rig = self.rig()
        for limit in (0, 0.5, 307, 'x', None):
            self.refused('refused', 'The continuous operation time must be within 1–306 seconds', rig.board.run, 8, 40, 1, limit, 'manual')
        self.refused('refused', 'does not have this pump (12) ', rig.board.run, 12, 40, 1, 10, 'manual')
        # V7's own limits and V7's own words, as its motors.run gives them.
        self.refused('refused', 'flow must be between 0 and 80.0 mL/min between', rig.board.run, 8, 81, 1, 10, 'manual')
        self.refused('refused', 'parameter flow_ml_min not less than 0.01', rig.board.run, 8, 0, 1, 10, 'manual')
        self.refused('refused', 'parameter flow_ml_min Must be a number', rig.board.run, 8, 'fast', 1, 10, 'manual')
        self.refused('refused', 'parameter direction can only be [1, -1] one of them', rig.board.run, 8, 40, 7, 10, 'manual')
        with self.assertRaises(ValueError):
            rig.board.run(8, 40, 1, 10, 'somebody')
        self.assertEqual([command for command in rig.tx() if command.startswith(('V,', 'P,'))], [])
        self.assertEqual(rig.guard.lines, [])
        v7 = pumpboard.load_v7()
        for flow in (0.5, 3, 10, 40, 77.7, 80):              # the speed is V7's, whatever the flow
            out = rig.board.run(8, flow, 1, 300, 'round')
            self.assertEqual(out['sps'], v7.motors.flow_to_sps(rig.board._conn.cfg, flow, 8))
            self.assertEqual(out['steps'], out['sps'] * 300)
            rig.board.stop_motor(8)
        out = rig.board.run(8, 80, 1, 306, 'round')          # the most a round may ask for
        self.assertEqual((out['sps'], out['steps']), (5649, 5649 * 306))
        self.assertIn('P,8,1728594,5649', rig.tx())
        self.no_endless_command(rig)


class DoseTests(Case):
    def test_a_dose_waits_for_the_valve_the_move_and_the_valve_again(self):
        rig = self.rig(valves__open_before_ms=300, valves__close_after_ms=300)
        seen, over = [], threading.Event()
        sim = rig.sim

        def look():
            with sim._lock:
                now = (sim.mstate['B'], sim.valves[2])
            if not seen or seen[-1] != now:
                seen.append(now)

        def watch():
            while not over.is_set():
                look()
                time.sleep(0.003)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        started = time.perf_counter()
        out = rig.board.dose(9, 5, 1, 'round', threading.Event())
        took = time.perf_counter() - started
        over.set()
        watcher.join(2)
        look()                                               # what it is like at the moment the dose came back
        self.assertEqual(seen, [('idle', False), ('wait', True), ('move', True), ('idle', True), ('idle', False)])
        self.assertEqual((out['steps'], out['sps'], out['eta_s']), (22795, 2825, 8.7))
        self.assertGreaterEqual(took, 0.3 + 22795 / 2825 / 20 + 0.3 - 0.05)
        self.assertLess(took, 4)
        self.assertGreaterEqual(out['elapsed_s'], 0.9)
        self.assertEqual(rig.board.quiescent(), (True, []))  # it came back only when the valve was closed
        self.assertIn('P,9,22795,2825', rig.tx())
        self.assertIsNone(rig.board.view()['op'])
        self.assertIsNone(rig.board.view()['last_result'])   # a round's dose is not a manual result
        self.assertEqual(rig.guard.lines, [])                # a dose ends by itself: the guard stays out of it

    def test_a_dose_that_does_not_end_is_stopped_with_x(self):
        self.patched(DOSE_LIMIT_FACTOR=0.0, DOSE_LIMIT_EXTRA_S=0.6)
        plan = self.plan()
        plan.freeze_moves = True
        rig = self.rig()
        sent = len(rig.tx())
        self.refused('timeout', 'The quantitative exceeding time has not ended yet and all have been stopped.', rig.board.dose, 8, 1, 1, 'round', threading.Event())
        self.assertIn('X', rig.tx()[sent:])
        self.assertTrue(rig.all_idle())
        self.assertTrue(rig.board.connected)
        self.assertFalse(rig.board.op_lock.locked())

    def test_a_dose_that_ends_too_early_counts_as_interrupted(self):
        plan = self.plan()
        plan.instant_moves = True
        rig = self.rig()
        self.refused('device', 'Quantification ended earlier than expected and may have been interrupted.', rig.board.dose, 8, 20, 1, 'manual', threading.Event())
        self.assertIn('X', rig.tx())
        result = rig.board.view()['last_result']
        self.assertEqual((result['ok'], result['text']), (False, 'Operation failed: Quantification ended earlier than expected and may have been interrupted.'))

    def test_a_dose_replaced_by_another_command_counts_as_interrupted(self):
        rig = self.rig()
        box = self.background(rig.board.dose, 8, 50, 1, 'round', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'move')
        rig.raw.send('S,7')                                  # somebody addresses that group behind the wrapper's back
        self.assertEqual(self.finished(box).get('error'), ('device', 'Quantification ended earlier than expected and may have been interrupted.'))
        self.assertTrue(rig.all_idle())

    def test_a_reverse_dose(self):
        rig = self.rig()
        out = rig.board.dose(8, 2, -1, 'manual', threading.Event())
        self.assertEqual(out['steps'], -9118)
        self.assertIn('P,8,-9118,2825', rig.tx())
        result = rig.board.view()['last_result']
        self.assertEqual((result['ok'], result['text']), (True, 'Pump 7 · OUT Quantitative completion:2 mL'))
        self.refused('refused', 'parameter volume_ml cannot be greater than 1000', rig.board.dose, 8, 1001, 1, 'manual', threading.Event())
        self.assertEqual(rig.tx().count('X'), 1)             # V7 said no before anything started: nothing is stopped

    def test_a_dose_needs_everything_else_at_rest_and_holds_the_board_while_it_runs(self):
        rig = self.rig()
        rig.board.run(3, 40, 1, 300, 'manual')
        self.refused('refused', 'The pump is still running', rig.board.dose, 8, 1, 1, 'manual', threading.Event())
        self.assertEqual(rig.sim.mstate['A'], 'move')        # the run goes on
        self.assertEqual(rig.tx().count('X'), 1)
        rig.board.stop_motor(3)
        box = self.background(rig.board.dose, 8, 50, 1, 'manual', threading.Event())
        rig.wait(lambda: (rig.board.view()['op'] or {}).get('eta_s') and rig.sim.mstate['B'] == 'move')
        op = rig.board.view()['op']
        self.assertEqual(set(op), OP_KEYS)
        self.assertEqual((op['kind'], op['motor'], op['by'], op['eta_s']), ('dose_volume', 8, 'manual', 80.7))
        self.assertRegex(op['text'], r'^Pump 7 · OUT Quantitative 50 mL · already \d+ Second/about 81 Second$')
        self.assertEqual((rig.motor(8)['state'], rig.motor(8)['by'], rig.motor(6)['blocked']), ('move', 'manual', 'B group is in use'))
        self.refused('busy', T_BUSY, rig.board.run, 3, 40, 1, 30, 'manual')
        self.refused('busy', T_BUSY, rig.board.dose, 3, 1, 1, 'manual', threading.Event())
        self.refused('busy', T_BUSY, rig.board.clean, 'manual', threading.Event())
        self.refused('busy', T_BUSY, rig.board.scale_tare)
        ok, reasons = rig.board.quiescent()
        self.assertEqual((ok, reasons[0]['code']), (False, 'pump_moving'))
        self.assertEqual(rig.board.stop_all('stop'), {'sent': True, 'confirmed': True, 'error': ''})
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Stopped'))
        result = rig.board.view()['last_result']
        self.assertEqual((result['ok'], result['text']), (False, 'has stopped:Pump 7 · OUT Quantitative 50 mL'))
        self.assertIsNone(rig.board.view()['op'])

    def test_nothing_is_started_when_stop_was_pressed_before(self):
        rig = self.rig()
        rig.board.syringe_init('manual', threading.Event())
        pressed = threading.Event()
        pressed.set()
        sent = len(rig.tx())
        for call, args in ((rig.board.dose, (8, 5, 1)), (rig.board.dose_weight, (8, 5)), (rig.board.clean, ()),
                           (rig.board.syringe_move, ('aspirate', 1, 1.0)), (rig.board.syringe_init, ())):
            self.refused('stopped', 'Stopped', call, *args, 'manual', pressed)
        self.assertEqual(rig.tx()[sent:], [])                # not even a `?`
        self.assertTrue(rig.board.view()['syringe']['initialised'])
        self.assertEqual(rig.board.view()['last_result']['text'], 'Stopped: syringe pump initializing')
        self.assertFalse(rig.board.op_lock.locked())

    def test_the_stop_flag_of_the_caller_ends_a_dose(self):
        rig = self.rig()
        abort = threading.Event()
        box = self.background(rig.board.dose, 8, 50, 1, 'round', abort)
        rig.wait(lambda: rig.sim.mstate['B'] == 'move')
        sent = len(rig.tx())
        abort.set()
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Stopped'))
        self.assertIn('X', rig.tx()[sent:])
        self.assertTrue(rig.all_idle())

    def test_off_on_the_row_ends_a_dose_without_stopping_everything_else(self):
        rig = self.rig()
        rig.board.syringe_init('manual', threading.Event())
        box = self.background(rig.board.dose, 8, 50, 1, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'move')
        stops = rig.tx().count('X')
        rig.board.stop_motor(8)
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Stopped'))
        self.assertEqual(rig.tx().count('X'), stops)         # only that motor
        self.assertTrue(rig.board.view()['syringe']['initialised'])
        self.assertTrue(rig.all_idle())


class WeightDoseTests(Case):
    def test_a_weight_dose_reaches_its_target(self):
        rig = self.rig(link__simulation_speedup=4)
        out = rig.board.dose_weight(9, 5, 'manual', threading.Event())
        self.assertEqual(set(out), {'target_g', 'dispensed_g', 'error_g', 'within_tolerance', 'topups', 'elapsed_s',
                                    'baseline_g', 'warning'})
        self.assertEqual(out['target_g'], 5.0)
        self.assertGreaterEqual(out['dispensed_g'], 5 * 0.995 - 0.001)
        self.assertLess(abs(out['error_g']), 0.3)
        sent = rig.tx()
        speeds = [command for command in sent if command.startswith('V,9,')]
        self.assertEqual(speeds[:3], ['V,9,2825', 'V,9,706', 'V,9,212'])     # V7's three speeds: 40 → 10 → 3 mL/min
        # Before V7's dose: a move of zero steps at the fast speed, so that an earlier slower move cannot cap `V`.
        self.assertEqual([command for command in sent if command.startswith('P,')], ['P,9,0,2825'])
        self.assertLess(sent.index('P,9,0,2825'), sent.index('V,9,2825'))
        # Motor 9 has a valve: it opened for that move and was closed again before V7 began.
        rows = rig.log_lines()
        between = rows[rows.index(('tx', 'P,9,0,2825')):rows.index(('tx', 'V,9,2825'))]
        valve = [text.split('valves=')[1][:2] for kind, text in between if kind == 'rx' and text.startswith('STATE,')]
        self.assertEqual((valve[0], valve[-1]), ('01', '00'))
        self.assertEqual(speeds[-1], 'V,9,0')
        self.assertEqual(rig.board.quiescent(), (True, []))
        result = rig.board.view()['last_result']
        self.assertTrue(result['ok'])
        self.assertRegex(result['text'], r'^Pump 8 · CuSO₄ Quantitative completion:\d+\.\d\d g (error [+-]\d+\.\d\d g) $')
        self.assertEqual(rig.guard.lines, ['arm', 'disarm'])  # no end of its own while it runs
        self.assertIsNone(rig.board.view()['weight_dose'])

    def test_a_weight_dose_is_refused_without_a_loaded_calibration(self):
        rig = self.rig(calibration=False)
        self.refused('refused', 'The scale has not been calibrated yet: go to ”Maintenance › Pump and Scale” do a calibration', rig.board.dose_weight, 9, 5, 'manual', threading.Event())
        self.assertEqual([command for command in rig.tx() if command.startswith('V,')], [])
        self.assertEqual(rig.guard.lines, [])
        rig.wait(lambda: (rig.board._weight[0] or {}).get('stable'))
        rig.board.scale_tare()                               # Return to zero makes a calibration (the simulator's divisor)
        self.assertTrue(rig.board.view()['scale']['calibrated'])

    def test_stop_all_cancels_a_weight_dose_without_waiting_for_it(self):
        rig = self.rig(link__simulation_speedup=1)
        box = self.background(rig.board.dose_weight, 8, 500, 'round', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')
        self.assertTrue(rig.board.op_lock.locked())
        view = rig.board.view()
        self.assertEqual(set(view['weight_dose']), {'motor', 'target_g', 'gain_g', 'elapsed_s'})
        self.assertEqual((view['weight_dose']['motor'], view['weight_dose']['target_g']), (8, 500.0))
        self.assertEqual((rig.motor(8)['state'], rig.motor(8)['by']), ('weigh', 'round'))
        self.assertRegex(view['op']['text'], r'^Pump 7 · OUT Weighing Quantification · at present [+-]\d+\.\d\d g/Target 500 g$')
        self.assertEqual(rig.guard.lines, ['arm'])
        started = time.perf_counter()
        report = rig.board.stop_all('stop')
        took = time.perf_counter() - started
        self.assertEqual(report, {'sent': True, 'confirmed': True, 'error': ''})
        self.assertLess(took, 1.5)                           # it did not wait for the dose to come back
        self.assertTrue(rig.all_idle())
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Weighing and rationing has been stopped by the user'))
        self.assertFalse(rig.board.op_lock.locked())
        self.assertEqual(rig.guard.lines, ['arm', 'disarm'])

    def test_the_stop_flag_alone_also_ends_a_weight_dose(self):
        rig = self.rig(link__simulation_speedup=1)
        abort = threading.Event()
        box = self.background(rig.board.dose_weight, 8, 500, 'round', abort)
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')
        abort.set()                                          # the dose sits inside V7; the monitor acts for it
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Weighing and rationing has been stopped by the user'))
        self.assertTrue(rig.all_idle())
        self.assertIn('X', rig.tx())

    def test_the_hard_limit_of_a_weight_dose(self):
        self.patched(WEIGHT_HARD_LIMIT_S=0.8)
        rig = self.rig(link__simulation_speedup=1)
        started = time.perf_counter()
        self.refused('timeout', 'The weighing quantity exceeds 0.8 seconds, all stopped', rig.board.dose_weight, 8, 500, 'manual', threading.Event())
        self.assertLess(time.perf_counter() - started, 4)
        self.assertTrue(rig.all_idle())
        self.assertIn('X', rig.tx())
        self.assertEqual(rig.board.view()['last_result']['text'], 'Operation failed: The weighing quantity exceeds 0.8 seconds, all stopped')

    def test_off_on_the_row_ends_a_weight_dose(self):
        rig = self.rig(link__simulation_speedup=1)
        box = self.background(rig.board.dose_weight, 8, 500, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')
        stops = rig.tx().count('X')
        rig.board.stop_motor(8)
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Weighing and rationing has been stopped by the user'))
        self.assertEqual(rig.tx().count('X'), stops)
        self.assertTrue(rig.all_idle())

    def test_weight_dosing_switched_off_in_the_settings_file(self):
        rig = self.rig(motors__gravimetric_dose__enabled=False)
        self.assertEqual(rig.board.hardware()['gravimetric']['enabled'], False)
        self.refused('refused', 'configuration file does not enable weighing and quantification', rig.board.dose_weight, 8, 5, 'manual', threading.Event())
        self.assertEqual([command for command in rig.tx() if command.startswith(('V,', 'P,'))], [])

    def test_a_weight_dose_needs_a_fresh_reading_and_v7s_own_limits_apply(self):
        rig = self.rig()
        self.refused('refused', 'parameter target_g cannot be greater than 1000', rig.board.dose_weight, 8, 2000, 'manual', threading.Event())
        self.refused('refused', 'parameter target_g not less than 0.01', rig.board.dose_weight, 8, 0, 'manual', threading.Event())
        self.refused('refused', 'The target weight must be between 0 and 1000 g between', rig.board.dose_weight, 8, None, 'manual', threading.Event())
        self.assertEqual(rig.tx().count('X'), 1)             # refused before anything started: no stop needed
        self.assertEqual([command for command in rig.tx() if command.startswith('P,')], [])
        rig.raw.send('W,STREAM,0')                           # the scale goes silent
        time.sleep(1.2)
        self.refused('refused', 'scale has no reading value', rig.board.dose_weight, 8, 5, 'manual', threading.Event())
        self.refused('refused', 'scale has no reading value', rig.board.scale_read)
        self.assertEqual([command for command in rig.tx() if command.startswith('V,')], [])


class BoardDoseTests(Case):
    """The weight dose with the firmware of mega/ (pc/megasim.py speaks for it): the board watches the scale and
    stops the pump itself. The stock tests above stay: a board with V7's own firmware still works as it did."""

    def board(self, **options):
        return self.rig(sim_firmware='plasmic', **options)

    def test_the_firmware_is_recognised_and_its_watchdog_switched_on(self):
        rig = self.board()
        view = rig.board.view()
        self.assertEqual((view['firmware_tag'], view['firmware_ok'], view['firmware_match']), ('p2', True, True))
        self.assertEqual(rig.board.facts()['firmware_tag'], 'p2')
        self.assertTrue(rig.board.facts()['firmware_banner'].endswith(',fw=p2'))
        self.assertIn('K,10000', rig.tx())
        self.assertEqual(rig.sim.wd_ms, 10000)
        # ... and on V7's own firmware none of that is asked for.
        stock = self.rig_after(rig)
        self.assertEqual(stock.board.view()['firmware_tag'], '')
        self.assertFalse([command for command in stock.tx() if command.startswith(('K,', 'G,', 'GP,', 'GN,', 'CM,', 'VL,'))])

    def rig_after(self, rig):
        rig.close()
        return self.rig()

    def test_the_board_doses_and_this_pc_only_asks_how_it_goes(self):
        rig = self.board(link__simulation_speedup=4)
        out = rig.board.dose_weight(9, 5, 'manual', threading.Event())
        self.assertEqual(set(out), {'target_g', 'dispensed_g', 'error_g', 'within_tolerance', 'topups', 'elapsed_s',
                                    'baseline_g', 'warning'})
        self.assertEqual(out['target_g'], 5.0)
        self.assertGreaterEqual(out['dispensed_g'], 5 * 0.995 - 0.001)
        self.assertLess(abs(out['error_g']), 0.3)
        sent = rig.tx()
        # V7's numbers, handed over in three short lines: 40 / 10 / 3 mL/min, 0.05 g, settle, 30 s, 300 s;
        # 5 and 7 readings, 4 s; then motor 9, 5 g, the two zones and 0.5 %.
        self.assertEqual([command for command in sent if command.startswith(('GP,', 'GN,', 'G,')) and command != 'G,?'],
                         ['GP,2825,706,212,50,200,30000,300000', 'GN,5,7,4000', 'G,9,5000,1250,250,25'])
        self.assertGreater(sent.count('G,?'), 2)
        # Nothing endless was ever started by this PC, and nothing had to be prepared for it.
        self.assertEqual([command for command in sent if command.startswith(('V,', 'P,'))], [])
        self.assertEqual(rig.guard.lines, [])
        self.assertEqual(rig.board.quiescent(), (True, []))
        result = rig.board.view()['last_result']
        self.assertTrue(result['ok'])
        self.assertRegex(result['text'], r'^Pump 8 · CuSO₄ Quantitative completion:\d+\.\d\d g (error [+-]\d+\.\d\d g) $')
        self.assertIsNone(rig.board.view()['weight_dose'])
        notes = [text for kind, text in rig.log_lines() if kind == 'rx' and text.startswith('DOSE,')]
        self.assertEqual([text.split('phase=')[1].split(',')[0] for text in notes if 'phase=' in text],
                         ['priming', 'coarse', 'approach', 'trim', 'settle'])
        self.assertTrue(notes[-1].startswith('DOSE,end,result=done,'))

    def test_what_the_page_sees_while_the_board_doses(self):
        rig = self.board(link__simulation_speedup=1)
        box = self.background(rig.board.dose_weight, 8, 500, 'round', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')
        rig.wait(lambda: ((rig.board.view()['weight_dose'] or {}).get('gain_g') or 0) > 0.2)
        view = rig.board.view()
        self.assertEqual((view['weight_dose']['motor'], view['weight_dose']['target_g']), (8, 500.0))
        self.assertEqual((rig.motor(8)['state'], rig.motor(8)['by']), ('weigh', 'round'))
        self.assertRegex(view['op']['text'], r'^Pump 7 · OUT Weighing Quantification · at present [+-]\d+\.\d\d g/Target 500 g$')
        self.assertEqual(rig.guard.lines, [])                # it has an end of its own: the guard is not needed
        started = time.perf_counter()
        report = rig.board.stop_all('stop')
        self.assertEqual(report, {'sent': True, 'confirmed': True, 'error': ''})
        self.assertLess(time.perf_counter() - started, 1.5)
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.sim.gd_result, megasim.GR_CANCELLED)
        self.assertEqual(self.finished(box).get('error')[0], 'stopped')
        self.assertFalse(rig.board.op_lock.locked())

    def test_the_stop_flag_and_off_on_the_row_end_it(self):
        rig = self.board(link__simulation_speedup=1)
        abort = threading.Event()
        box = self.background(rig.board.dose_weight, 8, 500, 'round', abort)
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')
        abort.set()
        self.assertEqual(self.finished(box).get('error')[0], 'stopped')
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.sim.gd_result, megasim.GR_CANCELLED)
        rig.wait(lambda: rig.board._weight[0] is not None and time.perf_counter() - rig.board._weight[1] < 0.5, 5.0,
                 'the scale to report again after the stop')
        box = self.background(rig.board.dose_weight, 8, 500, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')
        stops = rig.tx().count('X')
        rig.board.stop_motor(8)                               # “OFF” on that row
        self.assertEqual(self.finished(box).get('error')[0], 'stopped')
        self.assertEqual(rig.tx().count('X'), stops)          # only that motor: V,8,0 ends the dose on the board
        self.assertIn('V,8,0', rig.tx())
        self.assertTrue(rig.all_idle())

    def test_the_board_gives_up_by_itself_and_says_why(self):
        # (two seconds is the least the firmware takes: a linked valve opens for one before its pump turns)
        cases = (('no_gain', dict(motors__gravimetric_dose__no_gain_timeout_s=2.0), lambda rig: pt.FaultPlan.no_weight_gain(rig.sim),
                  'weight has been continuous 2 seconds do not continue to rise; please check the pipeline, direction and weighing position'),
                 ('timeout', dict(motors__gravimetric_dose__timeout_s=2.0, motors__gravimetric_dose__minimum_gain_g=0.001),
                  lambda rig: setattr(rig.sim, '_simulation_speedup', 0.05), 'The weighing quantity has exceeded 2 The target has not been reached in seconds and the peristaltic pump has been stopped.'),
                 ('scale', {}, lambda rig: setattr(rig.sim, 'scale_silent', True), 'The weight module did not provide a new valid reading and the peristaltic pump has been stopped.'))
        for name, options, fault, sentence in cases:
            with self.subTest(case=name):
                rig = self.board(link__simulation_speedup=1, **options)
                box = self.background(rig.board.dose_weight, 8, 500, 'manual', threading.Event())
                rig.wait(lambda: rig.sim.mstate['B'] == 'run')
                fault(rig)
                self.assertEqual(self.finished(box).get('error'), ('device', sentence))
                self.assertTrue(rig.all_idle())
                self.assertEqual(megasim.RESULT_NAMES[rig.sim.gd_result], name)
                self.assertEqual(rig.board.view()['last_result']['text'], 'Operation failed: ' + sentence)
                rig.close()

    def test_what_the_board_refuses_is_passed_on_with_nothing_started(self):
        rig = self.board()
        rig.sim.scale_calibrated = False                      # (as after a restart of the board that nobody noticed)
        self.refused('device', 'The scale has not been calibrated yet: go to ”Maintenance › Pump and Scale” do a calibration', rig.board.dose_weight, 8, 5, 'manual', threading.Event())
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.sim.gd_phase, megasim.GD_IDLE)
        rig.sim.scale_calibrated = True
        # The same limits as with V7's firmware apply before anything is sent.
        self.refused('refused', 'parameter target_g cannot be greater than 1000', rig.board.dose_weight, 8, 2000, 'manual', threading.Event())
        self.assertEqual(len([command for command in rig.tx() if command.startswith('G,') and command != 'G,?']), 1)
        # Numbers the firmware does not take are refused by the board itself, before anything moves.
        rig.close()
        rig = self.board(motors__gravimetric_dose__no_gain_timeout_s=1.0)
        self.refused('device', 'The pump control board does not accept this value.', rig.board.dose_weight, 8, 5, 'manual', threading.Event())
        self.assertEqual([command for command in rig.tx() if command.startswith('G,') and command != 'G,?'], [])
        self.assertTrue(rig.all_idle())

    def test_no_line_longer_than_the_boards_buffer_is_sent(self):
        rig = self.board()
        v7 = pumpboard.load_v7()
        checked = rig.board._conn.checked
        sent = len(rig.tx())
        self.assertEqual(pumpboard.LINE_MAX, 62)              # 63 bytes in the Mega's buffer, one is the line feed
        for command in ('W,OFFSET,' + '1' * 54, 'G,8,5000,1250,250', 'G,8,5000,1250,250,25,1', 'G,8,5.0,1250,250,25',
                        'G,8,5000,1250,250,-25', 'GP,2825,706,212,50,200,30000', 'GP,2825,706,212,50,200,30000,300000,1',
                        'GN,5,7', 'GN,5,7,x', 'K,-1', 'K,1,2', 'G,?,1'):
            with self.assertRaises(v7.LinkTrouble) as caught:
                checked.send(command)
            self.assertEqual(caught.exception.code, 'blocked', command)
        self.assertEqual(rig.tx()[sent:], [])
        # The largest numbers the firmware accepts still fit.
        for command in ('GP,8000,8000,8000,100000,60000,600000,1800000', 'G,9,1000000,1000000,1000000,999999'):
            self.assertLessEqual(len(command), pumpboard.LINE_MAX)

    def test_the_second_line_of_this_pc_behind_the_boards_own_limit(self):
        self.patched(BOARD_DOSE_EXTRA_S=0.5)
        rig = self.board(link__simulation_speedup=1, motors__gravimetric_dose__timeout_s=2.0)
        # A board that never ends its dose (it must not happen; this PC still does not wait for ever).
        rig.sim._service_dose = lambda: None
        rig.sim._dose_sample = lambda grams, stable: None
        started = time.perf_counter()
        self.refused('timeout', 'The weighing quantity exceeds 2.5 seconds, all stopped', rig.board.dose_weight, 8, 500, 'manual', threading.Event())
        self.assertLess(time.perf_counter() - started, 6)
        self.assertGreaterEqual(rig.tx().count('X'), 2)       # the one at the start, and this one



class PumpSetupTests(Case):
    """AMENDMENTS M20: what each pump carries, the two pumps of the cleaning and the valve links are a setting of
    this program (Maintenance › Pump and Scale). The firmware of mega/ p2 is told with CM and VL; a board that cannot be told keeps
    V7's layout, and the screens then show that one."""

    T_FIRMWARE = ('The firmware of this pump control board cannot change these settings (requires Plasmic p2 above) : water, OUT and the solenoid valve are linked according to the original board.'
                  'To change, please update the firmware first (mega folder Flash-Mega.cmd) ')

    def board(self, **options):
        return self.rig(sim_firmware='plasmic', **options)

    def labels(self, rig):
        return [motor['label'] for group in rig.board.hardware()['groups'] for motor in group['motors']]

    def valves(self, rig):
        return {motor['id']: motor['valve'] for group in rig.board.hardware()['groups'] for motor in group['motors']
                if motor['valve'] is not None}

    def test_nothing_saved_no_valve_is_used_and_the_board_is_told_before_anything_else(self):
        rig = self.board()
        tx = rig.tx()
        self.assertEqual([command for command in tx if command.startswith(('CM,', 'VL,'))], ['CM,2,8', 'VL,0,0,0,0,0,0,0,0'])
        self.assertLess(tx.index('X'), tx.index('CM,2,8'))
        self.assertFalse([command for command in tx[:tx.index('VL,0,0,0,0,0,0,0,0')]
                          if command == 'C' or command.startswith(('V,', 'P,', 'S,', 'G,', 'Y,'))])
        self.assertEqual(rig.sim.cfg['valves']['motor_links'], [])
        self.assertEqual(self.labels(rig), ['Pump 1 · water', 'Pump 2', 'Pump 3', 'Pump 4', 'Pump 5', 'Pump 6', 'Pump 7 · OUT',
                                            'Pump 8 · CuSO₄'])
        self.assertEqual(self.valves(rig), {})
        setup = rig.board.pump_setup()
        self.assertEqual((setup['editable'], setup['reason'], setup['source']), (True, '', 'default'))
        self.assertEqual([(row['pump'], row['use'], row['name'], row['half'], row['valve']) for row in setup['pumps']],
                         [(1, 'water', 'water', 'Pump 1–4', None), (2, 'none', '', 'Pump 1–4', None), (3, 'none', '', 'Pump 1–4', None),
                          (4, 'none', '', 'Pump 1–4', None), (5, 'none', '', 'Pump 5–8', None), (6, 'none', '', 'Pump 5–8', None),
                          (7, 'out', 'OUT', 'Pump 5–8', None), (8, 'chemical', 'CuSO₄', 'Pump 5–8', None)])
        self.assertEqual(setup['valves'], [{'id': 1, 'label': 'solenoid valve 1', 'motor': None}, {'id': 2, 'label': 'solenoid valve 2', 'motor': None}])
        self.assertEqual((setup['name_max'], setup['names']), (16, {'water': 'water', 'out': 'OUT'}))
        # The pump that carries water turns without a valve opening first.
        rig.board.run(2, 40, 1, 2, 'manual')
        self.assertFalse(any(rig.sim.valves.values()))
        self.assertNotIn('wait', rig.sim.mstate.values())
        rig.board.stop_all('test')
        self.assertEqual(rig.active, {})
        self.assertNotIn('pump_setup', rig.store.pump_settings())

    def test_a_saved_setup_is_told_to_the_board_and_shown_everywhere(self):
        rig = self.board()
        before = rig.board.hardware()['rev']
        wanted = pt.setup_of(water=3, out=6, names={2: 'HNO₃ 1%', 9: 'CuSO₄'}, valves={1: 3})
        reply = rig.board.save_pump_setup(wanted)
        self.assertEqual(reply['summary'], 'water=Pump 2, OUT=Pump 5; Pump 1=HNO₃ 1%, Pump 8=CuSO₄; solenoid valve 1 follow Pump 2')
        self.assertEqual((reply['source'], reply['editable']), ('saved', True))
        self.assertEqual([command for command in rig.tx() if command.startswith(('CM,', 'VL,'))][-2:],
                         ['CM,3,6', 'VL,0,1,0,0,0,0,0,0'])
        saved = rig.store.pump_settings()['pump_setup']
        self.assertEqual({key: saved[key] for key in ('pumps', 'valves')},
                         {'pumps': [{'motor': 2, 'use': 'chemical', 'name': 'HNO₃ 1%'}, {'motor': 3, 'use': 'water', 'name': 'water'},
                                    {'motor': 4, 'use': 'none', 'name': ''}, {'motor': 5, 'use': 'none', 'name': ''},
                                    {'motor': 6, 'use': 'out', 'name': 'OUT'}, {'motor': 7, 'use': 'none', 'name': ''},
                                    {'motor': 8, 'use': 'none', 'name': ''}, {'motor': 9, 'use': 'chemical', 'name': 'CuSO₄'}],
                          'valves': [{'id': 1, 'motor': 3}, {'id': 2, 'motor': None}]})
        self.assertRegex(saved['saved_at'], r'^\d{4}-\d\d-\d\dT')
        hardware = rig.board.hardware()
        self.assertEqual(self.labels(rig), ['Pump 1 · HNO₃ 1%', 'Pump 2 · water', 'Pump 3', 'Pump 4', 'Pump 5 · OUT', 'Pump 6',
                                            'Pump 7', 'Pump 8 · CuSO₄'])
        self.assertEqual(self.valves(rig), {3: 1})
        self.assertEqual(hardware['valves'], [{'id': 1, 'label': 'solenoid valve 1 · water', 'motor': 3},
                                              {'id': 2, 'label': 'solenoid valve 2', 'motor': None}])
        self.assertEqual((hardware['cleaning']['water_motor'], hardware['cleaning']['out_motor']), (3, 6))
        self.assertEqual(hardware['cleaning']['total_s'], 1.1)       # 0.9 s of pumps, and the water valve opens and closes
        self.assertEqual((hardware['rev'], rig.board.view()['hardware_rev']), (before + 1, before + 1))
        self.assertEqual([row['label'] for row in rig.board.view()['motors']], self.labels(rig))
        self.assertEqual(rig.board.view()['valves'][0]['label'], 'solenoid valve 1 · water')
        # The valve opens with the pump it was given to, and with no other.
        rig.board.run(3, 40, 1, 2, 'manual')
        self.assertEqual(rig.sim.valves, {1: True, 2: False})
        rig.board.stop_all('test')
        rig.wait(lambda: rig.all_idle(), 3.0, 'the valve to close')
        rig.board.run(2, 40, 1, 2, 'manual')
        self.assertFalse(any(rig.sim.valves.values()))
        rig.board.stop_all('test')
        # The cleaning turns the two pumps of the setup.
        rig.wait(lambda: rig.board.quiescent()[0], 3.0, 'the board to be at rest')
        out = rig.board.clean('manual', threading.Event())
        self.assertEqual(out['started'], True)
        self.assertEqual(rig.sim.sel, {'A': 3, 'B': 6})
        self.assertEqual(rig.tx().count('C'), 1)
        self.assertTrue(rig.all_idle())

    def test_the_setup_is_told_again_after_every_restart_of_the_board_and_of_the_program(self):
        folder = Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.addCleanup(shutil.rmtree, folder, True)
        rig = self.board(folder=folder)
        rig.board.save_pump_setup(pt.setup_of(water=3, out=6, valves={2: 9}))
        count = rig.tx().count('VL,0,0,0,0,0,0,0,2')
        pt.FaultPlan.reset_board(rig.sim)        # the board restarts by itself: it holds V7's layout again
        rig.wait(lambda: rig.tx().count('VL,0,0,0,0,0,0,0,2') == count + 1, 6.0, 'the setup to be sent again')
        self.assertEqual(rig.tx().count('CM,3,6'), count + 1)
        rig.wait(lambda: rig.board.connected, 6.0, 'the board to be connected again')
        self.assertEqual(rig.sim._state_line().split(',clean=')[1], '3:6,links=00000002')
        rig.close()
        again = self.board(folder=folder)        # the program is started again: the file is read, the board is told
        self.assertEqual([command for command in again.tx() if command.startswith(('CM,', 'VL,'))][-2:],
                         ['CM,3,6', 'VL,0,0,0,0,0,0,0,2'])
        self.assertEqual(again.board.pump_setup()['source'], 'saved')
        self.assertEqual(self.valves(again), {9: 2})
        self.assertEqual(again.board.hardware()['valves'][1], {'id': 2, 'label': 'solenoid valve 2 · CuSO₄', 'motor': 9})

    def test_a_setup_that_cannot_be_is_refused_and_nothing_is_changed(self):
        rig = self.board()
        sent = len(rig.tx())
        good = pt.setup_of()
        cases = [
            ({'pumps': good['pumps'][:7], 'valves': good['valves']}, 'The setting content is incomplete: every pump and every solenoid valve must have'),
            ({'pumps': good['pumps'], 'valves': good['valves'][:1]}, 'The setting content is incomplete: every pump and every solenoid valve must have'),
            ({'pumps': good['pumps'] + [{'motor': 2, 'use': 'none'}], 'valves': good['valves']}, 'The setting content is incomplete: every pump and every solenoid valve must have'),
            ({'pumps': 'x', 'valves': []}, 'The setting content is incomplete: every pump and every solenoid valve must have'),
            (None, 'The setting content is incomplete: every pump and every solenoid valve must have'),
            (pt.setup_of(water=None), 'requires a pump to receive ”water”, and there can only be one (for cleaning)'),
            (pt.setup_of(out=None), 'requires a pump to connectOUT”,And there can only be one (for emptying)'),
            (pt.setup_of(water=2, out=5), '“water” and ”OUT”Want one in Pump 1–4, One in Pump 5–8: These two should be rotated together during cleaning. Only one on the same side can be rotated at a time.'),
            (pt.setup_of(water=6, out=9, names={}), '“water” and ”OUT”Want one in Pump 1–4, One in Pump 5–8: These two should be rotated together during cleaning. Only one on the same side can be rotated at a time.'),
            (pt.setup_of(names={3: ''}), 'Pump 2 The name of 1–16 characters, no commas, semicolons, quotation marks, backslashes,< or >'),
            (pt.setup_of(names={3: 'x' * 17}), 'Pump 2 The name of 1–16 characters, no commas, semicolons, quotation marks, backslashes,< or >'),
            (pt.setup_of(names={3: 'a,b'}), 'Pump 2 The name of 1–16 characters, no commas, semicolons, quotation marks, backslashes,< or >'),
            (pt.setup_of(names={3: '<b>'}), 'Pump 2 The name of 1–16 characters, no commas, semicolons, quotation marks, backslashes,< or >'),
            (pt.setup_of(names={3: 5}), 'Pump 2 The name of 1–16 characters, no commas, semicolons, quotation marks, backslashes,< or >'),
            (pt.setup_of(names={3: 'water'}), 'Pump 2: “water” and ”OUT”Please select directly, do not write it in the name'),
            (pt.setup_of(names={3: 'out'}), 'Pump 2: “water” and ”OUT”Please select directly, do not write it in the name'),
            (pt.setup_of(valves={1: 12}), 'solenoid valve 1 To select a pump, or select ”Do not use”'),
            (pt.setup_of(valves={1: 9, 2: 9}), 'Pump 8 cannot link two solenoid valves at the same time'),
        ]
        two_waters = pt.setup_of()
        two_waters['pumps'][2] = {'motor': 4, 'use': 'water'}
        cases.append((two_waters, 'requires a pump to receive ”water”, and there can only be one (for cleaning)'))
        for wanted, sentence in cases:
            with self.assertRaises(ValueError) as caught:
                rig.board.save_pump_setup(wanted)
            self.assertEqual(str(caught.exception), sentence)
        self.assertEqual(len(rig.tx()), sent)
        self.assertNotIn('pump_setup', rig.store.pump_settings())
        # A name is tidied, not refused, for its spaces.
        rig.board.save_pump_setup(pt.setup_of(names={3: '  standard solution   10 ppm '}))
        self.assertEqual(self.labels(rig)[1], 'Pump 2 · standard solution 10 ppm')

    def test_not_while_something_turns(self):
        rig = self.board()
        rig.board.run(2, 40, 1, 30, 'manual')
        self.refused('refused', 'The pump is still running', rig.board.save_pump_setup, pt.setup_of(valves={1: 2}))
        rig.board.stop_all('test')
        self.assertNotIn('pump_setup', rig.store.pump_settings())
        self.assertFalse([command for command in rig.tx() if command.startswith('VL,1')])
        box = self.background(rig.board.dose, 9, 20, 1, 'manual', threading.Event())
        rig.wait(lambda: rig.board.view()['op'] is not None, 3.0, 'the dose to start')
        self.refused('busy', T_BUSY, rig.board.save_pump_setup, pt.setup_of(valves={1: 2}))
        rig.board.stop_all('test')
        self.finished(box)
        self.assertNotIn('pump_setup', rig.store.pump_settings())

    def test_a_board_that_does_not_take_the_setup_is_not_used(self):
        """The answer to CM or VL is not the one that confirms it: nothing may move on a board whose valves and
        cleaning pumps are not known. While saving: nothing is kept, and the link is made again."""
        plan = self.plan()
        rig = self.board()
        generation = rig.board.view()['generation']
        plan.answer('VL,', ['ERR,busy'])
        with self.assertRaises(PumpBoardError) as caught:
            rig.board.save_pump_setup(pt.setup_of(valves={1: 2}))
        self.assertEqual((caught.exception.code, caught.exception.text), ('device', 'The pump is still running'))
        self.assertNotIn('pump_setup', rig.store.pump_settings())
        rig.wait(lambda: rig.board.view()['generation'] > generation and rig.board.connected, 8.0, 'a new connection')
        self.assertEqual(rig.sim._state_line().split(',clean=')[1], '2:8,links=00000000')      # the file's (default) setup again
        self.assertEqual(self.valves(rig), {})
        rig.close()
        # At the start of a connection: the link is not taken into use.
        plan.answer('CM,', ['OK,clean=9:9'], times=50)
        other = self.board(start=False)
        other.board.start()
        other.wait_link('error', 8.0)
        self.assertIn('The pump control board did not accept the pump settings (CM,2,8 → OK,clean=9:9) ', other.board.view()['text'])
        self.assertFalse(other.board.connected)

    def test_a_board_that_cannot_be_told_keeps_v7s_layout_and_says_so(self):
        rig = self.rig()                         # V7's own firmware
        self.assertEqual(self.valves(rig), V7_LINKS)
        self.assertEqual(rig.board.hardware()['valves'], [{'id': 1, 'label': 'solenoid valve 1 · water', 'motor': 2},
                                                          {'id': 2, 'label': 'solenoid valve 2 · CuSO₄', 'motor': 9}])
        self.assertEqual(rig.board.hardware()['cleaning']['total_s'], 1.1)
        setup = rig.board.pump_setup()
        self.assertEqual((setup['editable'], setup['reason'], setup['source']), (False, self.T_FIRMWARE, 'board'))
        self.refused('refused', self.T_FIRMWARE, rig.board.save_pump_setup, pt.setup_of(valves={1: 3}))
        self.assertNotIn('pump_setup', rig.store.pump_settings())
        self.assertEqual(rig.active, {})         # nothing was asked for, so nothing is missing
        rig.board.run(2, 40, 1, 2, 'manual')     # its valve opens first, as it always did
        self.assertEqual(rig.sim.valves, {1: True, 2: False})
        rig.board.stop_all('test')
        rig.close()
        # A setup was saved (with the new firmware) and the board now holds an older one: an alarm says what the board does.
        folder = Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.addCleanup(shutil.rmtree, folder, True)
        settings = {'version': 1, 'enabled': None, 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4,
                    'pump_setup': pt.setup_of(water=3, out=6, valves={1: 3})}
        old = self.rig(folder=folder, settings=settings)
        self.assertEqual(self.labels(old)[:2], ['Pump 1 · water', 'Pump 2'])
        self.assertEqual(self.valves(old), V7_LINKS)
        self.assertEqual(old.active['pump_setup_ignored'], (
            3, 'The firmware of the pump control board is old, and the settings of ”what to connect to the pump” are not applied. Now the original part of the board: water=Pump 1, OUT=Pump 7; Pump 8=CuSO₄; '
               'solenoid valve 1 follow Pump 1, solenoid valve 2 follow Pump 8.Please update the firmware (mega folder Flash-Mega.cmd) .'))
        old.close()
        # ... and with the firmware that takes it, the same file is simply used.
        new = self.board(folder=folder, settings=settings)
        self.assertEqual(self.labels(new)[:2], ['Pump 1', 'Pump 2 · water'])
        self.assertEqual(new.active, {})

    def test_a_setup_in_the_file_that_cannot_be_read_is_left_out_and_said_once(self):
        folder = Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.addCleanup(shutil.rmtree, folder, True)
        settings = {'version': 1, 'enabled': None, 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4,
                    'pump_setup': {'pumps': [{'motor': 2, 'use': 'water'}], 'valves': 'none'}}
        rig = self.board(folder=folder, settings=settings)
        self.assertEqual(rig.board.pump_setup()['source'], 'default')
        self.assertEqual(self.valves(rig), {})
        said = [text for text, _level in rig.events if 'Can’t read' in text]
        self.assertEqual(said, ['The content of ”What is connected to the pump” in the configuration file cannot be read, so it is not used: use the default instead (the solenoid valve is not used). Please go to ”Maintenance › Pump and Scale” reset'])
        rig.board.save_pump_setup(pt.setup_of(valves={2: 9}))           # saving a good one ends it
        self.assertEqual(rig.board.pump_setup()['source'], 'saved')

    def test_only_whole_tables_of_numbers_are_written(self):
        rig = self.board()
        checked = rig.board._conn.checked
        sent = len(rig.tx())
        for command in ('CM,2', 'CM,2,8,9', 'CM,a,8', 'CM,2,-8', 'VL,0,0,0', 'VL,0,0,0,0,0,0,0,0,0', 'VL,0,0,0,0,0,0,0,x', 'CM', 'VL'):
            with self.assertRaises(pumpboard.load_v7().LinkTrouble) as caught:
                checked.send(command)
            self.assertEqual(caught.exception.code, 'blocked', command)
        self.assertEqual(len(rig.tx()), sent)
        self.assertEqual([line for line in checked.send('CM,2,8') if line.startswith('OK')], ['OK,clean=2:8'])


class CleanTests(Case):
    def test_cleaning_runs_to_its_end(self):
        rig = self.rig()
        phases, over = [], threading.Event()

        def watch():
            while not over.is_set():
                phase = rig.board.view()['clean_phase']
                if phase and (not phases or phases[-1] != phase):
                    phases.append(phase)
                time.sleep(0.01)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        out = rig.board.clean('manual', threading.Event())
        over.set()
        watcher.join(2)
        self.assertEqual(out['started'], True)
        self.assertAlmostEqual(out['total_pump_timeline_s'], 0.9)
        self.assertGreaterEqual(out['elapsed_s'], 0.9)
        self.assertEqual([phase for phase in ('valve', 'water', 'both', 'drain') if phase in phases], phases)
        self.assertTrue({'water', 'both', 'drain'} <= set(phases), phases)
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.board.quiescent(), (True, []))
        self.assertEqual(rig.tx().count('C'), 1)
        self.assertEqual(rig.board.view()['last_result']['text'], 'Automatic cleaning completed')
        self.assertEqual(rig.guard.lines, [])

    def test_stop_all_ends_cleaning_and_a_single_motor_cannot_be_stopped(self):
        rig = self.rig(cleaning__water_lead_s=5, cleaning__overlap_s=5, cleaning__out_tail_s=5)
        box = self.background(rig.board.clean, 'round', threading.Event())
        rig.wait(lambda: rig.board.view()['cleaning'] and (rig.board.view()['op'] or {}).get('eta_s'))
        view = rig.board.view()
        self.assertTrue(rig.sim.cleaning)
        self.assertEqual((rig.motor(2)['state'], rig.motor(8)['state'], rig.motor(2)['by']), ('clean', 'clean', 'round'))
        self.assertEqual((rig.motor(3)['blocked'], rig.motor(9)['blocked']), ('A group is in use', 'B group is in use'))
        self.assertRegex(view['op']['text'], r'^automatic cleaning · (water valve|Water ingress) · already \d+ Second/about 16 Second$')
        self.refused('refused', 'A single pump cannot be stopped during automatic cleaning. To abort, click ”Stop” STOP”.', rig.board.stop_motor, 2)
        self.assertTrue(rig.sim.cleaning)
        ok, reasons = rig.board.quiescent()
        self.assertEqual((ok, reasons[0]['code'], reasons[0]['text']), (False, 'cleaning', 'Automatic cleaning in progress'))
        self.assertEqual(rig.board.stop_all('stop'), {'sent': True, 'confirmed': True, 'error': ''})
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Stopped'))
        self.assertFalse(rig.sim.cleaning)
        self.assertTrue(rig.all_idle())

    def test_cleaning_that_does_not_end_is_stopped_with_x(self):
        self.patched(CLEAN_EXTRA_S=0.3)
        rig = self.rig(cleaning__water_lead_s=0.2, cleaning__overlap_s=0.2, cleaning__out_tail_s=0.2)
        sim = rig.sim

        def never_end():
            pt.wait_for(lambda: sim.cleaning, 3)
            sim._clean_out_stop_at = time.monotonic() + 3600

        threading.Thread(target=never_end, daemon=True).start()
        self.refused('timeout', 'The automatic cleaning has exceeded the time and has not ended yet. All have been stopped.', rig.board.clean, 'manual', threading.Event())
        self.assertFalse(sim.cleaning)
        self.assertTrue(rig.all_idle())
        self.assertIn('X', rig.tx())


class SyringeTests(Case):
    def test_initialise_draw_in_push_out_and_the_bookkeeping(self):
        rig = self.rig()
        calls = rig.spy()
        blank = threading.Event
        self.refused('refused', 'The syringe pump has not been initialized yet.', rig.board.syringe_move, 'aspirate', 1, 1.0, 'manual', blank())
        self.refused('refused', 'The syringe pump has not been initialized yet.', rig.board.syringe_speed, 0.4)
        sent = len(rig.tx())
        self.assertEqual(rig.board.syringe_init('manual', blank()), {'initialised': True, 'speed_ml_s': 0.4})
        self.assertIn(('Y,INIT', pumpboard.Y_TERMS, 5.0), calls)     # V7 itself would wait 60 s for it
        self.assertEqual(rig.tx()[sent:][-3:], ['Y,INIT', 'Y,ST', 'Y,SPEED,960'])
        syringe = rig.board.view()['syringe']
        self.assertEqual((syringe['initialised'], syringe['volume_ul'], syringe['position'], syringe['busy']),
                         (True, 0, None, False))
        sent = len(rig.tx())
        out = rig.board.syringe_move('aspirate', 2, 1.5, 'manual', blank())
        self.assertEqual(out, {'position': 2, 'volume_ul': 1500, 'speed_ml_s': 0.4, 'contents_ul': 1500})
        commands = [command for command in rig.tx()[sent:] if command not in ('Y,ST', '?')]
        self.assertEqual(commands, ['Y,VALVE,2', 'Y,ASPUL,1500'])
        sent = len(rig.tx())
        self.assertEqual(rig.board.syringe_move('aspirate', 2, 3.5, 'manual', blank())['contents_ul'], 5000)
        self.assertNotIn('Y,VALVE,2', rig.tx()[sent:])       # the valve is already there
        self.refused('refused', 'The syringe cannot be installed (existing 5 mL,capacity 5 mL) ', rig.board.syringe_move, 'aspirate', 2, 0.001, 'manual', blank())
        sent = len(rig.tx())
        self.assertEqual(rig.board.syringe_move('dispense', 1, 4.5, 'manual', blank())['contents_ul'], 500)
        commands = [command for command in rig.tx()[sent:] if command not in ('Y,ST', '?')]
        self.assertEqual(commands, ['Y,VALVE,1', 'Y,DISPUL,4500'])
        self.refused('refused', 'There are only 0.5 mL,Can’t push it out 1 mL', rig.board.syringe_move, 'dispense', 1, 1.0, 'manual', blank())
        self.assertEqual(rig.board.syringe_move('dispense', 1, 0.5, 'manual', blank())['contents_ul'], 0)
        syringe = rig.board.view()['syringe']
        self.assertEqual((syringe['volume_ul'], syringe['position'], syringe['initialised']), (0, 1, True))
        self.assertEqual(rig.board.view()['last_result']['text'], 'Syringe pump injection completed:0.5 mL')
        self.refused('refused', 'The valve position should be at 1–3 between', rig.board.syringe_move, 'aspirate', 4, 1.0, 'manual', blank())
        self.refused('refused', 'The volume must be at least 0.001 mL', rig.board.syringe_move, 'aspirate', 1, 0, 'manual', blank())
        with self.assertRaises(ValueError):
            rig.board.syringe_move('suck', 1, 1.0, 'manual', blank())
        self.assertEqual(rig.board.quiescent(), (True, []))

    def test_a_move_is_not_finished_before_most_of_its_time_has_passed(self):
        rig = self.rig(link__simulation_speedup=2)
        rig.board.syringe_init('round', threading.Event())
        started = time.perf_counter()
        rig.board.syringe_move('aspirate', 1, 1.0, 'round', threading.Event())      # 2.5 s at 0.4 mL/s
        self.assertGreaterEqual(time.perf_counter() - started, 0.8 * 2.5 / 2 - 0.02)  # the simulator says "ready" at once

    def test_an_error_of_the_syringe_pump_is_a_failure_and_an_alarm(self):
        rig = self.rig()
        plan = self.plan()
        rig.board.syringe_init('manual', threading.Event())
        plan.answer('Y,ASPUL', ['INFO,pump_steps=2400', 'PUMP,proto=ascii,ready=1,error=7'])
        sent = len(rig.tx())
        self.refused('device', 'The syringe pump has not been initialized yet.', rig.board.syringe_move, 'aspirate', 1, 1.0, 'manual', threading.Event())
        self.assertEqual(rig.active.get('syringe_fault'), (2, 'Syringe pump report error 7 (The syringe pump has not been initialized yet)'))
        syringe = rig.board.view()['syringe']
        self.assertEqual((syringe['initialised'], syringe['volume_ul'], syringe['busy'], syringe['error']),
                         (False, None, False, 7))
        after = rig.tx()[sent:]
        self.assertLess(after.index('X'), after.index('Y,STOP'))     # X, then the stop that is answered
        self.assertEqual(rig.board.view()['last_result']['text'], 'Operation failed: the syringe pump has not been initialized yet')
        # V7 itself takes such a line for a success.
        for number, text, alarm in ((9, 'Piston overload', 'Syringe pump report error 9 (piston overload)'), (10, 'Valve overload', 'Syringe pump report error 10 (valve overload)'),
                                    (2, 'value is out of range', 'Syringe pump report error 2 (value is out of range)'), (12, 'Syringe pump report error 12', 'Syringe pump report error 12')):
            plan.answer('Y,INIT', ['PUMP,proto=ascii,ready=0,error=%d' % number])
            self.refused('device', text, rig.board.syringe_init, 'manual', threading.Event())
            self.assertEqual(rig.active.get('syringe_fault'), (2, alarm))
        rig.board.syringe_init('manual', threading.Event())           # a good initialise clears it
        self.assertNotIn('syringe_fault', rig.active)
        self.assertIsNone(rig.board.view()['syringe']['error'])

    def test_a_syringe_pump_that_does_not_get_ready(self):
        self.patched(SYRINGE_INIT_LIMIT_S=0.7, SYRINGE_POLL_S=0.1)
        rig = self.rig()
        plan = self.plan()
        plan.answer('Y,ST', ['PUMP,proto=ascii,ready=0,error=0'], times=99)
        self.refused('timeout', 'Syringe pump initialization exceeded 0.7 seconds not completed', rig.board.syringe_init, 'manual', threading.Event())
        self.assertIn('X', rig.tx())
        self.assertFalse(rig.board.view()['syringe']['initialised'])
        plan.clear()
        abort = threading.Event()
        plan.answer('Y,ST', ['PUMP,proto=ascii,ready=0,error=0'], times=999)
        box = self.background(rig.board.syringe_init, 'manual', abort)
        rig.wait(lambda: rig.board.view()['syringe']['busy'])
        abort.set()
        self.assertEqual(self.finished(box).get('error'), ('stopped', 'Stopped'))

    def test_the_bookkeeping_is_void_after_every_stop(self):
        rig = self.rig()
        self.assertEqual(rig.board.stop_all('stop')['confirmed'], True)
        self.assertNotIn('Y,STOP', rig.tx())                 # the syringe was never used: nothing to confirm
        rig.board.syringe_init('manual', threading.Event())
        rig.board.syringe_move('aspirate', 1, 1.0, 'manual', threading.Event())
        sent = len(rig.tx())
        self.assertEqual(rig.board.stop_all('stop'), {'sent': True, 'confirmed': True, 'error': ''})
        self.assertEqual(rig.tx()[sent:sent + 5], ['X', 'W,STREAM,0', '?', 'Y,STOP', '?'])
        syringe = rig.board.view()['syringe']
        self.assertEqual((syringe['initialised'], syringe['volume_ul']), (False, None))
        self.refused('refused', 'The syringe pump has not been initialized yet.', rig.board.syringe_move, 'dispense', 1, 1.0, 'manual', threading.Event())

    def test_size_and_speed(self):
        rig = self.rig()
        rig.board.syringe_init('manual', threading.Event())
        for size in (2000, True, '5000', None):
            with self.assertRaises(ValueError) as caught:
                rig.board.apply_settings(size, 0.4)
            self.assertEqual(str(caught.exception), 'syringe capacity only supports 5 mL or 1.25 mL')
        for size, speed, top in ((5000, 3, '2.5'), (5000, 0, '2.5'), (5000, 'fast', '2.5'), (1250, 0.7, '0.625')):
            with self.assertRaises(ValueError) as caught:
                rig.board.apply_settings(size, speed)
            self.assertEqual(str(caught.exception), 'The syringe pump speed should be at 0.001–%s mL/s between' % top)
        self.assertEqual(rig.store.saved, 0)
        sent = len(rig.tx())
        self.assertEqual(rig.board.apply_settings(5000, 1.0),
                         {'saved': True, 'settings': {'syringe_ul': 5000, 'syringe_speed_ml_s': 1.0}})
        self.assertEqual([command for command in rig.tx()[sent:] if command != '?'], ['Y,SYR,5000', 'Y,SPEED,2400'])
        syringe = rig.board.view()['syringe']
        self.assertEqual((syringe['initialised'], syringe['speed_ml_s']), (True, 1.0))   # the same size: still initialised
        saved = json.loads((rig.folder / 'pumpboard.json').read_text(encoding='utf-8'))
        self.assertEqual((saved['version'], saved['syringe_ul'], saved['syringe_speed_ml_s'], saved['enabled']), (1, 5000, 1.0, None))
        rig.board.apply_settings(1250, 0.5)
        self.assertIn('Y,SYR,1250', rig.tx())
        syringe = rig.board.view()['syringe']
        self.assertEqual((syringe['initialised'], syringe['size_ul'], syringe['max_speed_ml_s'], syringe['speed_ml_s']),
                         (False, 1250, 0.625, 0.5))          # another syringe: initialise again
        self.assertEqual(rig.board.view()['settings'], {'syringe_ul': 1250, 'syringe_speed_ml_s': 0.5})
        rig.board.syringe_init('manual', threading.Event())
        self.assertEqual(rig.board.syringe_speed(0.2), {'speed_ml_s': 0.2})
        self.refused('refused', 'Syringe pump speed exceeds the upper limit 0.625 mL/s', rig.board.syringe_speed, 0.7)
        self.assertEqual(rig.board.view()['syringe']['speed_ml_s'], 0.2)
        rig.board.syringe_init('manual', threading.Event())
        self.assertEqual(rig.board.view()['syringe']['speed_ml_s'], 0.5)    # the saved speed comes back
        rig.board.run(8, 40, 1, 300, 'manual')
        self.refused('refused', 'The pump is still running', rig.board.apply_settings, 5000, 0.4)
        self.assertEqual(rig.board.view()['settings']['syringe_ul'], 1250)


class ScaleTests(Case):
    def test_reading_the_scale_is_the_median_of_new_readings_and_sends_nothing(self):
        rig = self.rig()
        rig.wait(lambda: rig.board._weight[0] is not None)
        sent = len(rig.tx())
        first = rig.board._weight[0]['sequence']
        started = time.perf_counter()
        out = rig.board.scale_read()
        self.assertGreaterEqual(time.perf_counter() - started, 0.35)        # five new readings at 10 Hz
        self.assertEqual((out['samples'], out['stable']), (5, True))
        self.assertAlmostEqual(out['grams'], 0.0, places=2)
        self.assertGreaterEqual(rig.board._weight[0]['sequence'], first + 5)
        self.assertEqual(rig.tx()[sent:], [])
        self.assertEqual(rig.board.scale_read(samples=2)['samples'], 2)
        scale = rig.board.view()['scale']
        self.assertEqual((scale['online'], scale['stable']), (True, True))
        self.assertIsInstance(scale['grams'], float)
        self.assertLess(scale['age_s'], 0.5)

    def test_zero_and_calibration_need_a_quiet_and_steady_scale(self):
        rig = self.rig()
        v7 = pumpboard.load_v7()
        rig.wait(lambda: (rig.board._weight[0] or {}).get('stable'))
        rig.board.run(8, 40, 1, 300, 'manual')
        self.refused('blocked', 'There is a pump that is still running', rig.board.scale_tare)
        self.refused('blocked', 'There is a pump that is still running', rig.board.scale_calibrate, 100)
        rig.board.stop_motor(8)
        from backend import simulator
        with mock.patch.object(simulator.SimulatedMega, '_weight_line', lambda sim: 'WEIGHT,1.234,RAW,355025.6,ST,0'):
            rig.wait(lambda: rig.board._weight[0].get('stable') is False)
            self.refused('blocked', 'The reading value is still changing, wait for it to stabilize.', rig.board.scale_tare)
            self.refused('blocked', 'The reading value is still changing, wait for it to stabilize.', rig.board.scale_calibrate, 100)
        rig.raw.send('W,STREAM,0')
        time.sleep(1.2)
        self.refused('blocked', 'scale has no reading value', rig.board.scale_tare)
        self.refused('blocked', 'scale has no reading value', rig.board.scale_calibrate, 100)
        for grams in (0.05, 30000, 'x'):
            self.refused('refused', 'The weight of the weight should be 0.1–20000 g between', rig.board.scale_calibrate, grams)
        self.assertEqual([command for command in rig.tx() if command.startswith(('W,TARE', 'W,CAL'))], [])
        self.assertIsNotNone(v7)

    def test_zero_and_calibration_are_saved_in_the_data_folder(self):
        rig = self.rig()
        vendored = pt.VENDOR / 'v7' / 'config' / 'scale_calibration.json'
        before = pt.sha256(vendored)
        rig.wait(lambda: (rig.board._weight[0] or {}).get('stable'))
        sent = len(rig.tx())
        out = rig.board.scale_tare()
        on_disk = json.loads((rig.folder / 'pumpboard' / 'scale_calibration.json').read_text(encoding='utf-8'))
        self.assertEqual(set(out['calibration']), {'divisor', 'offset', 'reference_grams', 'saved_at'})
        self.assertEqual(out['calibration'], {key: on_disk[key] for key in out['calibration']})
        self.assertEqual(on_disk['reference_grams'], 200.0)  # kept from the calibration before
        rig.wait(lambda: (rig.board._weight[0] or {}).get('stable'))
        out = rig.board.scale_calibrate(100)
        self.assertEqual(out['calibration']['reference_grams'], 100.0)
        scale = rig.board.view()['scale']
        self.assertEqual((scale['calibrated'], scale['reference_g'], scale['calibrated_at']),
                         (True, 100.0, out['calibration']['saved_at']))
        self.assertEqual(rig.tx()[sent:], ['?', 'W,TARE', 'W,CFG', '?', 'W,CAL,100', 'W,CFG'])
        self.assertEqual(pt.sha256(vendored), before)        # V7's own file is only read, never written
        self.assertFalse(vendored.with_name(vendored.name + '.tmp').exists())

    def test_a_scale_that_went_silent_is_restarted_once_and_then_reported(self):
        self.patched(SCALE_SILENT_S=0.5, SCALE_ALARM_S=1.5)
        rig = self.rig()
        plan = self.plan()
        rig.wait(lambda: rig.board._weight[0] is not None)
        sent = len(rig.tx())
        rig.raw.send('W,STREAM,0')                           # as after three failed reads of the weighing module
        plan.answer('W,STREAM', ['OK,stream_hz=0'], times=99)        # and it cannot be switched on again
        rig.wait(lambda: 'scale_offline' in rig.active, 5)
        self.assertEqual(rig.active['scale_offline'], (3, 'Scale does not respond'))
        self.assertEqual(rig.tx()[sent:].count('W,STREAM,10'), 1)    # one restart, not a stream of them
        self.assertEqual(rig.board.view()['scale']['online'], False)
        plan.clear()
        rig.raw.send('W,STREAM,10')
        rig.wait(lambda: 'scale_offline' not in rig.active, 3)
        self.assertTrue(rig.board.view()['scale']['online'])


class FaultTests(Case):
    def test_a_lost_reply_is_a_timeout_and_w_cfg_puts_the_link_back_in_step(self):
        self.patched(timeouts={'scale': 0.3})
        rig = self.rig()
        plan = self.plan()
        v7 = pumpboard.load_v7()
        checked = rig.board._conn.checked
        plan.drop('W,AVG')
        sent = len(rig.tx())
        with self.assertRaises(v7.LinkTrouble) as caught:
            checked.send('W,AVG,1')
        self.assertEqual((caught.exception.code, caught.exception.text), ('timeout', 'Pump control board does not respond (W,AVG,1) '))
        self.assertIsInstance(caught.exception, v7.LinkError)        # V7 code that catches LinkError still does
        self.assertEqual(rig.tx()[sent:], ['W,AVG,1', 'W,CFG'])
        self.assertTrue(rig.board.connected)
        self.assertFalse(checked.desync)
        self.assertFalse(rig.board.motion_uncertain)
        self.assertEqual(checked.send('W,AVG,1'), ['OK,avg=1'])

    def test_an_unanswered_motion_command_stops_everything_and_reopens_the_board(self):
        self.patched(timeouts={'motor': 0.3})
        rig = self.rig()
        plan = self.plan()
        plan.drop('P,8')
        old, generation, sent = rig.sim, rig.board._generation, len(rig.tx())
        self.refused('timeout', 'Pump control board does not respond (P,8,847500,2825) ', rig.board.run, 8, 40, 1, 300, 'manual')
        rig.wait(lambda: rig.board._generation > generation and rig.board.connected)
        self.assertEqual(rig.tx()[sent:sent + 4], ['?', 'P,8,847500,2825', 'W,CFG', 'X'])
        self.assertTrue(rig.all_idle(old))                   # the motor had started; X stopped it
        self.assertIsNot(rig.sim, old)
        self.assertFalse(rig.board.motion_uncertain)         # known again after the new connection
        raised = [call[1] for call in rig.alarms if call[0] == 'raise']
        self.assertEqual(raised, ['pump_link_lost'])         # the stop was confirmed: no "cut the power" alarm
        self.assertNotIn('pump_link_lost', rig.active)
        self.assertEqual(rig.motor(8)['state'], 'off')
        self.assertEqual(rig.guard.lines, [])                # a finite move: nothing for the guard

    def test_a_stray_line_is_not_taken_for_the_reply(self):
        self.patched(timeouts={'motor': 0.3, 'state': 0.3})
        rig = self.rig()
        plan = self.plan()
        v7 = pumpboard.load_v7()
        checked = rig.board._conn.checked
        plan.answer('?', ['HELLO'])
        sent = len(rig.tx())
        with self.assertRaises(v7.LinkTrouble) as caught:    # V7 would hand ['HELLO'] back as a success
            checked.send('?')
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertEqual(rig.tx()[sent:], ['?', 'W,CFG'])
        self.assertTrue(rig.board.connected)
        generation = rig.board._generation
        plan.answer('P,8', ['HELLO'])
        self.refused('timeout', 'Pump control board does not respond (P,8,84750,2825) ', rig.board.run, 8, 40, 1, 30, 'manual')
        rig.wait(lambda: rig.board._generation > generation and rig.board.connected)

    def test_the_boards_refusals_are_given_in_plain_words(self):
        rig = self.rig()
        plan = self.plan()
        v7 = pumpboard.load_v7()
        checked = rig.board._conn.checked
        sent = len(rig.tx())
        for command, line, text in (
                ('V,8,0', 'ERR,cleaning_active', 'This action cannot be performed during automatic cleaning.'),
                ('V,8,0', 'ERR,arg', 'The pump control board does not accept this value.'),
                ('Y,ST', 'ERR,pump_timeout', 'Syringe pump does not respond'),
                ('Y,ST', 'ERR,pump_bad_reply', 'Syringe pump does not respond'),
                ('Y,ST', 'ERR,pump_echo_only', 'Syringe pump does not respond'),
                ('W,TARE', 'ERR,scale_timeout', 'Scale does not respond'),
                ('W,CAL,100', 'ERR,no_change', 'The weight has not changed and cannot be corrected.'),
                ('V,8,0', 'ERR,bad_cmd', 'The pump control board does not recognize this command (different firmware versions?)'),
                ('V,8,0', 'ERR,something_new', 'Pump control board reports error:ERR,something_new')):
            plan.answer(command, [line])
            with self.assertRaises(v7.LinkTrouble) as caught:
                checked.send(command)
            self.assertEqual((caught.exception.code, caught.exception.text, caught.exception.raw), ('device', text, line))
        self.assertNotIn('W,CFG', rig.tx()[sent:])           # an answer is an answer: nothing to put back in step
        self.assertTrue(rig.board.connected)
        self.assertEqual(rig.board.view()['syringe']['online'], False)
        rig.wait(lambda: (rig.board._weight[0] or {}).get('stable'))
        plan.answer('W,TARE', ['ERR,scale_timeout'])
        self.refused('device', 'Scale does not respond', rig.board.scale_tare)
        stops = rig.tx().count('X')
        plan.answer('C', ['ERR,cleaning_active'])
        self.refused('device', 'This action cannot be performed during automatic cleaning.', rig.board.clean, 'manual', threading.Event())
        self.assertEqual(rig.tx().count('X'), stops)         # the board refused: nothing had started

    def test_a_pulled_cable_during_a_dose(self):
        self.patched(RETRY_S=(1.5,))
        rig = self.rig()
        plan = self.plan()
        box = self.background(rig.board.dose, 8, 50, 1, 'round', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'move')
        old = rig.sim
        plan.fail_starts = 1                                 # the first try to open it again fails
        plan.pull_cable(old)
        self.assertEqual(self.finished(box).get('error'), ('link', T_LOST))
        self.assertTrue(rig.board.motion_uncertain)
        self.assertFalse(rig.board.connected)
        self.assertEqual(rig.active.get('pump_stop_unconfirmed'), (1, 'Pump control board lost connection. The pump may still be spinning. Immediately turn off power to the pump using the physical switch.'))
        self.assertNotIn('pump_link_lost', rig.active)
        self.assertEqual(old.mstate['B'], 'move')            # nothing could tell the board to stop
        with self.assertRaises(PumpBoardError) as caught:
            rig.board.run(3, 40, 1, 10, 'round')
        self.assertEqual(caught.exception.code, 'link')
        ok, reasons = rig.board.quiescent()
        self.assertEqual((ok, reasons[0]['code']), (False, 'pump_link'))
        self.assertEqual(rig.guard.lines, [])                # a dose ends by itself in the board: no guard
        rig.wait_connected()                                 # the second try
        self.assertFalse(rig.board.motion_uncertain)
        self.assertNotIn('pump_stop_unconfirmed', rig.active)
        self.assertEqual(rig.tx()[-14:], CONNECT_TRAFFIC)    # the new connection begins with X
        self.assertEqual(rig.guard.lines, [])
        self.assertEqual(rig.board.quiescent(), (True, []))

    def test_a_pulled_cable_during_a_weight_dose(self):
        self.patched(RETRY_S=(1.5,))
        rig = self.rig(link__simulation_speedup=1)
        plan = self.plan()
        box = self.background(rig.board.dose_weight, 8, 500, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')
        plan.fail_starts = 1
        plan.pull_cable(rig.sim)
        # V7 says "…Peristaltic pump stopped" here although its stop could not be sent.
        self.assertEqual(self.finished(box).get('error'), ('link', T_LOST))
        self.assertTrue(rig.board.motion_uncertain)
        self.assertEqual(rig.board.view()['last_result']['text'], 'Operation failed: ' + T_LOST)
        self.assertEqual(rig.active.get('pump_stop_unconfirmed')[0], 1)
        self.assertEqual(rig.guard.lines, ['arm'])           # V7's `V` may still be running: the guard stays armed
        rig.wait_connected()
        self.assertFalse(rig.board.motion_uncertain)
        self.assertFalse(rig.board.op_lock.locked())
        self.assertEqual(rig.guard.lines, ['arm', 'disarm'])

    def reset_during_a_dose(self, flood):
        rig = self.rig()
        rig.board.syringe_init('round', threading.Event())
        box = self.background(rig.board.dose, 8, 50, 1, 'round', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'move')
        sim, generation, sent = rig.sim, rig.board._generation, len(rig.tx())
        if flood:
            pt.FaultPlan.flood(sim, 500)                     # V7's event queue holds 200 and drops the rest silently
        pt.FaultPlan.reset_board(sim)
        self.assertEqual(self.finished(box).get('error'), ('device', 'The pump control board restarted on its own'))
        rig.wait(lambda: rig.board._generation > generation and rig.board.connected)
        self.assertIs(rig.sim, sim)                          # the same link: nothing was reopened
        self.assertEqual(rig.tx()[sent:][-12:], CONNECT_TRAFFIC[2:])        # the safe-state sequence again, X first
        self.assertEqual(rig.active.get('pump_reset'), (2, 'The pump control board restarted itself. It could be the power supply or USB is unstable. The syringe pump needs to be reinitialized.'))
        self.assertAlmostEqual(sim.scale_div, 287703.031, places=2)         # the calibration is in the board again
        view = rig.board.view()
        self.assertEqual((view['syringe']['initialised'], view['scale']['calibrated'], view['link']), (False, True, 'connected'))
        self.assertIsNone(rig.raw.state['firmware'])         # armed for the next time
        self.assertEqual(rig.board.quiescent(), (True, []))
        self.assertTrue(any('READY again' in text for kind, text in rig.log_lines() if kind == '--'))

    def test_a_board_that_restarts_during_an_operation(self):
        self.reset_during_a_dose(flood=False)

    def test_a_restart_is_noticed_behind_500_weight_lines(self):
        self.reset_during_a_dose(flood=True)

    def test_a_restart_of_an_idle_board_brings_the_settings_back(self):
        rig = self.rig(settings={'version': 1, 'enabled': None, 'syringe_ul': 1250, 'syringe_speed_ml_s': 0.3})
        generation = rig.board._generation
        pt.FaultPlan.reset_board(rig.sim)
        rig.wait(lambda: rig.board._generation > generation and rig.board.connected)
        self.assertEqual(rig.sim.syringe_ul, 1250)           # a restarted board is back at 5 mL by itself
        self.assertEqual(rig.tx()[-12:], CONNECT_TRAFFIC[2:11] + ['Y,SYR,1250', '?', 'Y,DETECT'])
        self.assertIn('pump_reset', rig.active)

    def test_a_board_that_stops_answering_is_given_up_and_opened_again(self):
        self.patched(HEARTBEAT_S=0.4, timeouts={'state': 0.3, 'cfg': 0.3})
        rig = self.rig()
        plan = self.plan()
        generation = rig.board._generation
        plan.swallow('?', times=2)                           # two heartbeats in a row get no answer
        rig.wait(lambda: rig.board._generation > generation and rig.board.connected, 8)
        raised = [call for call in rig.alarms if call[0] == 'raise']
        self.assertEqual([call[1:5] for call in raised], [('pump_link_lost', 3, 'pump', 'The pump control board connection is interrupted and the program is reconnecting.')])
        self.assertNotIn('pump_link_lost', rig.active)       # cleared by the new connection
        self.assertEqual(rig.events, [('Pump control board wired (analog)', 'info')] * 2)
        self.assertFalse(rig.board.motion_uncertain)         # nothing was moving

    def test_a_dead_board_is_given_up_when_it_cannot_be_put_back_in_step(self):
        self.patched(HEARTBEAT_S=0.3, RETRY_S=(0.3,), IDENTIFY_TIMEOUT_S=0.2, timeouts={'state': 0.2, 'cfg': 0.2})
        rig = self.rig(link__command_timeout_s=0.2)
        plan = self.plan()
        rig.board.run(8, 40, 1, 300, 'round')
        sent = len(rig.tx())
        plan.swallow('', times=10 ** 6)                      # nothing is answered any more
        rig.wait(lambda: 'pump_stop_unconfirmed' in rig.active, 6)
        self.assertEqual(rig.tx()[sent:sent + 3], ['?', 'W,CFG', 'W,CFG'])
        self.assertTrue(rig.board.motion_uncertain)          # a pump was running when it went
        self.assertEqual(rig.active['pump_stop_unconfirmed'][0], 1)
        self.assertFalse(rig.board.connected)
        plan.clear()
        rig.wait_connected(8)
        self.assertFalse(rig.board.motion_uncertain)


class StopTests(Case):
    def test_stop_all_stops_everything_and_the_scale_comes_back(self):
        rig = self.rig()
        rig.board.run(2, 40, 1, 300, 'manual')
        rig.board.run(8, 40, 1, 300, 'round')
        rig.wait(lambda: rig.sim.mstate['A'] == 'move')
        sent = len(rig.tx())
        self.assertEqual(rig.board.stop_all('stop'), {'sent': True, 'confirmed': True, 'error': ''})
        self.assertEqual(rig.tx()[sent:sent + 4], ['X', 'W,STREAM,0', '?', '?'])
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.sim.sel, {'A': None, 'B': None})
        self.assertEqual(rig.sim.stream_hz, 0)               # X switched the weight stream off …
        self.assertEqual([row['state'] for row in rig.board.view()['motors']], ['off'] * 8)
        self.assertEqual(rig.guard.lines, [])
        rig.wait(lambda: rig.sim.stream_hz == 10, 3)         # … and it is switched on again by itself
        rig.wait(lambda: rig.board.view()['scale']['online'], 3)
        self.assertEqual(rig.tx()[sent:].count('W,STREAM,10'), 1)
        self.assertIsNone(rig.board.deadline_tripped('manual'))
        self.assertEqual(rig.board.stop_all('stop')['confirmed'], True)      # with nothing running it works as well
        self.assertEqual(rig.alarms[3:], [])

    def test_closing_leaves_the_outputs_stopped(self):
        rig = self.rig()
        rig.board.run(8, 40, 1, 300, 'manual')
        sim = rig.sim
        started = time.perf_counter()
        rig.board.close()
        self.assertLess(time.perf_counter() - started, 2.5)
        self.assertTrue(rig.all_idle(sim))
        self.assertEqual(sim.sel, {'A': None, 'B': None})
        self.assertEqual(rig.tx()[-4:], ['X', 'W,STREAM,0', '?', '?'])
        self.assertEqual(rig.guard.lines, [])
        self.assertEqual(rig.guard.closed, [True])           # a clean end: the guard is told bye
        self.assertIsNone(pumpboard.PumpBoard._live)
        self.assertFalse(rig.board.connected)
        with self.assertRaises(PumpBoardError) as caught:
            rig.board.run(8, 40, 1, 10, 'manual')
        self.assertEqual(caught.exception.code, 'link')
        rig.board.close()                                    # closing twice is harmless
        self.assertEqual(rig.guard.closed, [True])
        self.assertFalse([thread for thread in threading.enumerate() if thread.name in ('pump-monitor', 'serial-reader', 'sim')])

    def test_closing_without_a_confirmed_stop_leaves_the_guard_to_act(self):
        self.patched(RETRY_S=(30.0,))
        rig = self.rig(link__simulation_speedup=1)
        plan = self.plan()
        box = self.background(rig.board.dose_weight, 8, 500, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'run')       # V7's `V`: the one thing with no end of its own
        plan.fail_starts = 99
        plan.pull_cable(rig.sim)
        self.assertEqual(self.finished(box).get('error'), ('link', T_LOST))
        rig.board.close(budget_s=0.5)
        self.assertEqual(rig.guard.lines, ['arm'])
        self.assertEqual(rig.guard.closed, [False])          # no bye: the guard reopens the port and sends X

    def test_closing_with_only_a_timed_run_at_risk_needs_no_guard(self):
        self.patched(RETRY_S=(30.0,))
        rig = self.rig()
        plan = self.plan()
        rig.board.run(8, 40, 1, 300, 'manual')
        plan.fail_starts = 99
        plan.pull_cable(rig.sim)
        rig.wait(lambda: rig.board.motion_uncertain)         # it may still be turning, until its own time is over
        rig.board.close(budget_s=0.5)
        self.assertEqual(rig.guard.lines, [])
        self.assertEqual(rig.guard.closed, [True])

    def test_stop_all_with_the_link_down_tries_once_to_open_it_again(self):
        self.patched(RETRY_S=(60.0,), RECONNECT_BUDGET_S=6.0)
        rig = self.rig()
        plan = self.plan()
        rig.board.run(8, 40, 1, 300, 'round')
        plan.fail_starts = 1
        plan.pull_cable(rig.sim)
        rig.wait_link('error')                               # the immediate attempt failed; the next is a minute away
        attempts = rig.board._attempts
        self.assertEqual(rig.board.stop_all('stop'), {'sent': True, 'confirmed': True, 'error': ''})
        self.assertEqual(rig.board._attempts, attempts + 1)
        self.assertTrue(rig.board.connected)
        self.assertFalse(rig.board.motion_uncertain)
        self.assertEqual(rig.tx()[-14:], CONNECT_TRAFFIC)    # X came with the new connection
        self.assertNotIn('pump_stop_unconfirmed', rig.active)
        # … and when that one attempt fails as well, the answer says so at once.
        plan.fail_starts = 2
        plan.pull_cable(rig.sim)
        rig.wait_link('error')
        attempts = rig.board._attempts
        started = time.perf_counter()
        report = rig.board.stop_all('stop')
        self.assertLess(time.perf_counter() - started, 3)    # when the attempt was over, not after the whole budget
        self.assertEqual((report['sent'], report['confirmed'], rig.board._attempts), (False, False, attempts + 1))
        self.assertTrue(report['error'].startswith('Pump control board is not connected ('))
        self.assertEqual(rig.active.get('pump_stop_unconfirmed'), (1, 'The pump stop command was not confirmed. Immediately turn off power to the pump using the physical switch. The settings saved in'))

    def test_a_stop_that_is_not_confirmed(self):
        self.patched(RETRY_S=(60.0,), RECONNECT_BUDGET_S=6.0)
        rig = self.rig()
        plan = self.plan()
        rig.board.run(8, 40, 1, 300, 'manual')
        old = rig.sim
        plan.answer('X', ['OK,all_stopped'])                 # answered, and the motor keeps turning
        plan.answer('?', [state_line(b=8, sb='move', stream=0)], times=2)
        plan.fail_starts = 1                                 # and the board cannot be opened again either
        report = rig.board.stop_all('stop')
        self.assertEqual(report, {'sent': True, 'confirmed': False, 'error': 'The pump is still running'})
        self.assertEqual(rig.active.get('pump_stop_unconfirmed'), (1, 'The pump stop command was not confirmed. Immediately turn off power to the pump using the physical switch. The settings saved in'))
        self.assertTrue(rig.board.motion_uncertain)
        self.assertFalse(rig.board.connected)
        self.assertEqual(old.mstate['B'], 'move')
        self.assertEqual(rig.guard.lines, [])
        self.assertEqual(rig.board.stop_all('stop'), {'sent': True, 'confirmed': True, 'error': ''})    # opened again: X first
        self.assertFalse(rig.board.motion_uncertain)
        self.assertNotIn('pump_stop_unconfirmed', rig.active)


class GateTests(Case):
    def test_nothing_starts_while_high_voltage_is_not_known_to_be_off(self):
        rig = self.rig()
        v7 = pumpboard.load_v7()
        blank = threading.Event
        rig.board.syringe_init('manual', blank())
        rig.board.run(3, 40, 1, 300, 'manual')               # running before the high voltage came on
        rig.hv = True
        self.refused('blocked', T_HV, rig.board.run, 8, 40, 1, 10, 'manual')
        rig.board.stop_motor(3)                              # a stop is never held back
        sent = len(rig.tx())
        self.refused('blocked', T_HV, rig.board.dose, 8, 1, 1, 'manual', blank())
        self.refused('blocked', T_HV, rig.board.dose_weight, 8, 5, 'manual', blank())
        self.refused('blocked', T_HV, rig.board.clean, 'manual', blank())
        self.refused('blocked', T_HV, rig.board.syringe_init, 'manual', blank())
        self.refused('blocked', T_HV, rig.board.syringe_move, 'aspirate', 1, 1.0, 'manual', blank())
        written = rig.tx()[sent:]
        self.assertEqual([command for command in written if command[0] in 'PCX' or command.startswith(('Y,I', 'Y,V', 'Y,A', 'Y,D'))], [])
        self.assertEqual([command for command in written if command.startswith('V,') and not command.endswith(',0')], [])
        self.assertTrue(rig.board.view()['syringe']['initialised'])  # a refusal changes nothing
        self.assertTrue(rig.all_idle())
        checked = rig.board._conn.checked
        for command in ('V,8,100', 'V,8,-100', 'v,8,100', 'P,8,100,100', 'C', 'c', 'Y,INIT', 'y,init', 'Y,VALVE,1',
                        'Y,ASPUL,10', 'Y,DISPUL,10'):
            with self.assertRaises(v7.LinkTrouble) as caught:
                checked.send(command)
            self.assertEqual((caught.exception.code, caught.exception.text), ('blocked', T_HV), command)
        self.assertEqual(rig.tx()[sent:], written)           # none of them was written
        for command in ('?', 'V,8,0', 'Y,STOP', 'Y,ST', 'W,CFG', 'W,STREAM,10', 'X'):
            checked.send(command)                            # these move nothing
        self.assertEqual(rig.board.stop_all('stop')['confirmed'], True)
        rig.hv = False
        rig.board.run(8, 40, 1, 10, 'manual')

    def test_without_a_gate_nothing_moves(self):
        rig = self.rig(gate=False)
        self.refused('blocked', 'The high-voltage interlock has not been connected and the pump cannot be started.', rig.board.run, 8, 40, 1, 10, 'manual')
        rig.board.set_gate(lambda: 1 / 0)                    # an interlock that cannot answer
        self.refused('blocked', T_HV, rig.board.run, 8, 40, 1, 10, 'manual')
        self.assertEqual([command for command in rig.tx() if command.startswith('V,')], [])
        rig.board.set_gate(lambda: False)
        rig.board.run(8, 40, 1, 10, 'manual')

    def test_only_the_listed_commands_reach_the_board(self):
        rig = self.rig()
        v7 = pumpboard.load_v7()
        checked = rig.board._conn.checked
        sent = len(rig.tx())
        for command in ('W,SCAN', 'W,SNIFF,3', 'W,SET,1,3,0,1', 'W,BAUD,9600', 'W,READ', 'W,RD,1,3,0,2', 'W,HELP', 'W,LOOP',
                        'w,scan', 'Y,Q', 'Y,POS,100', 'Y,ASP,10', 'Y,DISP,10', 'Y,ADDR,0', 'Y,BAUD,9600', 'Y,SCAN', 'Y',
                        'STOP', 'Q', 'X,1', 'C,1', 'V,8', 'P,8,100', 'S', 'hello', 'X\nV,8,100', 'V,8,100\rX',
                        'V,8,' + '1' * 100):
            with self.assertRaises(v7.LinkTrouble) as caught:
                checked.send(command)
            self.assertEqual(caught.exception.code, 'blocked', command)
            self.assertTrue(caught.exception.text.startswith('This command is not in the allowed list and has not been sent:'), command)
        # V7's own diagnose and Modbus functions would block the firmware for up to a minute.
        for name, payload in (('scale.diagnose', {'mode': 'scan'}), ('scale.diagnose', {'mode': 'sniff'}),
                              ('scale.modbus', {'address': 1, 'function_code': 3, 'register': 0, 'format': 1})):
            with self.assertRaises(v7.LinkError):
                v7.registry.get(name).invoke(rig.board._conn.ctx, payload)
        self.assertEqual(rig.tx()[sent:], [])
        self.assertEqual(checked.send(''), [])
        for command in ('?', 'S,8', 'W,CFG', 'Y,ST', 'Y,DETECT', 'W,AVG,1', 'x'):
            checked.send(command)
        self.assertEqual(rig.tx()[sent:], ['?', 'S,8', 'W,CFG', 'Y,ST', 'Y,DETECT', 'W,AVG,1', 'x'])
        # The time limits are the wrapper's own, whatever the caller asks for.
        calls = rig.spy()
        checked.send('?', ('NEVER',), 60)
        checked.refresh_state()
        self.assertEqual(calls, [('?', ('STATE,',), 1.5), ('?', ('STATE,',), 1.5)])


class WatchdogTests(Case):
    def test_a_silent_page_stops_what_was_started_by_hand(self):
        rig = self.rig()
        text = 'The operation screen exceeds 10 No response for seconds, manually started pump stopped'
        rig.board.run(8, 40, 1, 300, 'manual')
        rig.page_age = 9.0
        time.sleep(0.4)
        self.assertEqual(rig.sim.mstate['B'], 'move')
        rig.page_age = 10.5
        rig.wait(lambda: rig.sim.mstate['B'] == 'idle' and 'page_lost' in rig.active, 3)
        self.assertEqual(rig.active['page_lost'], (3, text))
        self.assertIn('X', rig.tx())
        self.assertEqual(len([call for call in rig.alarms if call[:2] == ('raise', 'page_lost')]), 1)
        rig.page_age = 0.0
        box = self.background(rig.board.dose, 8, 50, 1, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['B'] == 'move')
        rig.page_age = 11.0
        self.assertEqual(self.finished(box).get('error'), ('stopped', text))
        self.assertTrue(rig.all_idle())

    def test_a_round_does_not_depend_on_the_page(self):
        rig = self.rig()
        rig.board.run(8, 40, 1, 300, 'round')
        rig.page_age = 3600.0
        time.sleep(0.5)
        self.assertEqual(rig.sim.mstate['B'], 'move')
        self.assertNotIn('page_lost', rig.active)
        rig.page_age = None                                  # "unknown" is not "silent"
        rig.board.run(3, 40, 1, 300, 'manual')
        time.sleep(0.4)
        self.assertEqual(rig.sim.mstate['A'], 'move')


class LogTests(Case):
    def test_the_serial_log_has_what_was_sent_and_received_but_no_weights(self):
        rig = self.rig()
        time.sleep(0.4)
        rig.board.run(8, 40, 1, 5, 'manual')
        rig.board.stop_all('stop')
        rows = rig.log_lines()
        self.assertEqual({kind for kind, _ in rows}, {'tx', 'rx', '--'})
        self.assertFalse([text for _, text in rows if 'WEIGHT' in text])
        self.assertIn(('rx', 'OK,all_stopped'), rows)
        self.assertIn(('rx', pt.READY), rows)
        self.assertIn(('tx', 'P,8,14125,2825'), rows)
        self.assertTrue(any(kind == '--' and 'banner_seen=True' in text for kind, text in rows))
        raw = (rig.folder / 'logs' / 'pumpboard.log').read_bytes()
        self.assertNotIn(b'\r', raw)
        raw.decode('utf-8')

    def test_the_log_is_rotated(self):
        self.patched(LOG_MAX_BYTES=600)
        rig = self.rig()
        for _ in range(8):
            for _ in range(5):
                rig.board.quiescent()
            rig.log_lines()
        names = sorted(item.name for item in (rig.folder / 'logs').iterdir())
        self.assertEqual(names, ['pumpboard.log', 'pumpboard.log.1', 'pumpboard.log.2', 'pumpboard.log.3'])
        self.assertGreaterEqual((rig.folder / 'logs' / 'pumpboard.log.3').stat().st_size, 600)


if __name__ == '__main__':
    unittest.main(verbosity=2)
