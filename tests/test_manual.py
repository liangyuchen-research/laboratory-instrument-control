"""pc/manual.py ’s automatic test: manual operation ( Determined by the setting ofSPEC B.7, B.12, G; API.md C.5) .

    python tests/test_manual.py
    python tests/test_manual.py WatchdogTests

all on both emulators (V7 own pump control board simulator,test_tcpd of leave RPi,only connect 127.0.0.1) .
will not open or list any ports on this computer.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_testing as ct  # noqa: E402  (sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import devices  # noqa: E402
from devices import Blocked, Busy  # noqa: E402
from pumpboard import PumpBoardError  # noqa: E402

T_SCHEDULE = 'Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out'
T_PAUSED_ROUND = 'The process is paused: Please click ”Continue” at the bottom RESUME”or“stop STOP”'
T_OTHER = 'Another operation is in progress'
TICK = 'RPi There is no connection and it is impossible to confirm that the high voltage is off.'
ASK_NO_RPI = 'RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.'
ASK_SILENT = 'The status of the high-voltage power supply cannot be read (the high-voltage power supply/Nano is invalid), the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. After the firmware of'
NO_SILENT = [{'code': 'hv_unknown', 'text': 'high voltage power supply/Nano is invalid and it cannot be confirmed that the high voltage is off. The object of', 'fix': 'After confirming that the high-voltage power supply is turned off, check the box in the confirmation window'}]


def setUpModule():
    ct.set_up()


def tearDownModule():
    ct.tear_down()


def on(motor=8, seconds=30, flow=40, direction=1, **more):
    return dict({'op': 'pump_on', 'motor': motor, 'flow_ml_min': flow, 'direction': direction, 'seconds': seconds}, **more)


def off(motor=8):
    return {'op': 'pump_off', 'motor': motor}


class Base(ct.Case):
    def refused(self, rig, body, code, text):
        """The board (or the link to it) says no: PumpBoardError with this code and text."""
        with self.assertRaises(PumpBoardError) as caught:
            rig.manual.submit(body)
        self.assertEqual((caught.exception.code, caught.exception.text), (code, text))

    def busy(self, rig, body, text):
        with self.assertRaises(Busy) as caught:
            rig.manual.submit(body)
        self.assertEqual(str(caught.exception), text)

    def blocked(self, rig, body):
        with self.assertRaises(Blocked) as caught:
            rig.manual.submit(body)
        self.assertEqual(str(caught.exception), 'Cannot start pump now')
        return caught.exception.reasons

    def idle(self, rig, timeout=10.0):
        """Wait until nothing started by hand is left; the equipment is then free again."""
        ct.wait_for(lambda: not rig.manual.active, timeout, 'the manual operation to end')
        self.assertIsNone(rig.devices.owner)

    def motor(self, rig, number):
        return next(row for row in rig.pump.view()['motors'] if row['id'] == number)


class PumpTests(Base):
    def test_a_pump_started_by_hand_runs_for_its_seconds_and_stops_by_itself(self):
        rig = self.rig()
        self.assertFalse(rig.manual.active)
        self.assertEqual(rig.manual.submit(on(8, seconds=2)), {'accepted': True, 'motor': 8, 'until_s': 2})
        self.assertIn('P,8,%d,2825' % (2 * 2825), rig.tx())          # the whole run is given to the board (M8)
        self.assertEqual([c for c in rig.tx() if c.startswith('V,') and not c.endswith(',0')], [])
        row = self.motor(rig, 8)
        self.assertEqual((row['state'], row['by'], row['flow_ml_min'], row['direction']), ('run', 'manual', 40, 1))
        self.assertIn(row['left_s'], (1, 2))
        owner = rig.devices.owner
        self.assertEqual((owner['kind'], owner['id'], owner['label']), ('manual', 'manual', 'Manual: Pump 7 · OUT'))
        self.assertTrue(rig.manual.active)
        self.assertEqual(rig.devices.view()['owner']['kind'], 'manual')
        # A pump of the other group may run beside it; one of the same group may not.
        self.assertEqual(rig.manual.submit(on(3, seconds=30, flow=20, direction=-1)), {'accepted': True, 'motor': 3, 'until_s': 30})
        self.assertEqual((self.motor(rig, 3)['state'], self.motor(rig, 3)['direction']), ('run', -1))
        self.assertIn('P,3,-%d,1412' % (30 * 1412), rig.tx())
        self.refused(rig, on(6), 'refused', 'B group can only transfer one at a time (Pump 7 · OUT is still transferring)')
        self.refused(rig, on(8), 'refused', 'Pump 7 · OUT is already transferring')
        self.assertEqual(rig.devices.owner['label'], 'Manual: Pump 7 · OUT')
        # OFF.
        self.assertEqual(rig.manual.submit(off(3)), {'stopped': True})
        self.assertEqual(self.motor(rig, 3)['state'], 'off')
        self.assertEqual(rig.devices.owner['kind'], 'manual')        # motor 8 still runs
        # Its seconds are over: the board has stopped it, nobody pressed anything.
        ct.wait_for(lambda: rig.board.mstate['B'] == 'idle', 5, 'the board to end the run')
        ct.wait_for(lambda: rig.pump.view()['last_result'], 3, 'the result line')
        self.assertEqual(rig.pump.view()['last_result']['text'], 'Pump 7 · OUT arrived at the setting 2 seconds, stopped automatically')
        self.assertNotIn('V,8,0', rig.tx())
        self.idle(rig)
        texts = rig.texts()
        for text in ('Manual start Pump 7 · OUT: Forward rotation 40 mL/min,2 Automatically stop in seconds', 'Manual start Pump 2: reverse 20 mL/min,30 Automatically stop in seconds', 'manual stop Pump 2',
                     'Pump 7 · OUT has automatically stopped when the set time is reached.'):
            self.assertIn(text, texts)
        started = next(row for row in rig.store.events(limit=50) if row['text'].startswith('Manual start Pump 7'))
        self.assertEqual(started['actor'], ct.OPERATOR)
        rig.operator = ''                # manual work needs no operator
        self.assertEqual(rig.manual.submit(on(8, seconds=1))['accepted'], True)
        self.assertEqual(rig.store.events(limit=1)[0]['actor'], '')
        self.assertEqual(rig.manual.submit(off(8)), {'stopped': True})
        self.idle(rig)

    def test_what_a_request_must_hold(self):
        rig = self.rig()
        sent = len(rig.tx())
        for body, text in (
                (None, 'Manual operation format is incorrect'), ([], 'Manual operation format is incorrect'), ({}, 'Unsupported manual operation'), ({'op': 'plasma_run'}, 'Unsupported manual operation'),
                (on(1), 'The pump number is incorrect'), (on('8'), 'The pump number is incorrect'), (on(True), 'The pump number is incorrect'), (off(10), 'The pump number is incorrect'),
                (on(flow=0), 'traffic should be in 0.1–80 mL/min between'), (on(flow=80.5), 'traffic should be in 0.1–80 mL/min between'),
                (on(flow='40'), 'traffic should be in 0.1–80 mL/min between'), (on(flow=float('nan')), 'traffic should be in 0.1–80 mL/min between'),
                (on(seconds=0), 'The number of seconds for automatic stop should be within 1–300 integer between'), (on(seconds=301), 'The number of seconds for automatic stop should be within 1–300 integer between'),
                (on(seconds=1.5), 'The number of seconds for automatic stop should be within 1–300 integer between'), (on(seconds=None), 'The number of seconds for automatic stop should be within 1–300 integer between'),
                (on(direction=0), 'direction must be 1 (forward) or -1 (reverse)'), (on(direction=2), 'direction must be 1 (forward) or -1 (reverse)'),
                ({'op': 'dose_volume', 'motor': 8, 'volume_ml': 0}, 'The quantitative volume should be within 0.01–1000 mL between'),
                ({'op': 'dose_volume', 'motor': 8, 'volume_ml': 1000.5}, 'The quantitative volume should be within 0.01–1000 mL between'),
                ({'op': 'dose_volume', 'motor': 8, 'volume_ml': 5, 'direction': 3}, 'direction must be 1 (forward) or -1 (reverse)'),
                ({'op': 'dose_weight', 'motor': 8, 'target_g': 0}, 'The quantitative weight should be in 0.01–1000 g between'),
                ({'op': 'dose_weight', 'motor': 8}, 'The quantitative weight should be in 0.01–1000 g between'),
                ({'op': 'syringe_move', 'kind': 'push', 'position': 1, 'volume_ml': 1}, 'The action of the syringe pump must be aspirate (aspiration) or dispense (injection)'),
                ({'op': 'syringe_move', 'kind': 'aspirate', 'position': 4, 'volume_ml': 1}, 'The valve position should be at 1–3 between'),
                ({'op': 'syringe_move', 'kind': 'aspirate', 'position': 1, 'volume_ml': 5.5}, 'The injection volume should be within 0.001–5 mL between'),
                ({'op': 'syringe_move', 'kind': 'dispense', 'position': 1, 'volume_ml': 0}, 'The injection volume should be within 0.001–5 mL between')):
            with self.assertRaises(ValueError) as caught:
                rig.manual.submit(body)
            self.assertEqual(str(caught.exception), text, body)
        self.assertEqual([command for command in rig.tx()[sent:] if command != '?'], [])
        self.assertIsNone(rig.devices.owner)
        self.assertFalse(rig.manual.active)

    def test_off_is_always_accepted(self):
        rig = self.rig(cleaning__water_lead_s=3, cleaning__overlap_s=3, cleaning__out_tail_s=3)
        self.assertEqual(rig.manual.submit(off(5)), {'stopped': True})       # nothing runs: still fine
        # Whoever holds the equipment.
        rig.devices.acquire('tool', 't1', 'Clear Nano fault')
        self.busy(rig, on(8), T_OTHER)
        self.assertEqual(rig.manual.submit(off(8)), {'stopped': True})
        rig.devices.release('t1')
        recipe_id = rig.publish('discharge', ct.csv('pump_on,M8,40,mL/min,', 'delay,,20,s,', 'pump_off,M8,,,'))
        rig.start(recipe_id)
        rig.wait_step('delay')
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the round to run its pump')
        self.busy(rig, on(3), T_SCHEDULE)
        self.busy(rig, {'op': 'clean'}, T_SCHEDULE)
        self.assertEqual(rig.manual.submit(off(8)), {'stopped': True})       # the round's own pump
        self.assertEqual(rig.board.mstate['B'], 'idle')
        rig.devices.stop_everything('test')
        rig.finished()
        # Only the board itself refuses: during cleaning a single pump cannot be stopped.
        rig.manual.submit({'op': 'clean'})
        ct.wait_for(lambda: rig.board.cleaning, 5, 'cleaning to begin')
        self.refused(rig, off(2), 'refused', 'A single pump cannot be stopped during automatic cleaning. To abort, click ”Stop” STOP”.')
        self.assertTrue(rig.board.cleaning)
        self.assertEqual(rig.devices.stop_everything('test')['manual_cancelled'], True)
        self.idle(rig)

    def test_stop_ends_what_was_started_by_hand(self):
        rig = self.rig()
        self.assertFalse(rig.manual.cancel())
        rig.manual.submit(on(8, seconds=120))
        rig.manual.submit(on(2, seconds=120))
        report = rig.devices.stop_everything(ct.OPERATOR)
        self.assertEqual((report['manual_cancelled'], report['schedule_ended'], report['pump']['result']), (True, False, 'confirmed'))
        self.assertTrue(rig.pumps_off())
        self.idle(rig)
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 8, 'volume_ml': 50, 'direction': 1})
        self.assertTrue(reply['accepted'])
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        self.assertEqual(rig.devices.stop_everything(ct.OPERATOR)['manual_cancelled'], True)
        self.idle(rig)
        self.assertTrue(rig.pumps_off())
        self.assertEqual(rig.pump.view()['last_result']['text'], 'has stopped:Pump 7 · OUT Quantitative 50 mL')
        self.assertIn('Manual operation has been stopped:Pump 7 · OUT Quantitative 50 mL', rig.texts())
        self.assertEqual(rig.devices.stop_everything(ct.OPERATOR)['manual_cancelled'], False)


class BlockingTests(Base):
    def test_a_dose_answers_at_once_and_runs_on(self):
        rig = self.rig()
        started = time.perf_counter()
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 8, 'volume_ml': 5, 'direction': 1})
        self.assertLess(time.perf_counter() - started, 1.5)
        self.assertEqual(set(reply), {'accepted', 'op_id', 'eta_s'})
        self.assertEqual((reply['accepted'], reply['eta_s']), (True, 8.1))
        op = rig.pump.view()['op']
        self.assertEqual((op['id'], op['kind'], op['by'], op['motor']), (reply['op_id'], 'dose_volume', 'manual', 8))
        self.assertEqual(rig.board.mstate['B'], 'move')              # it goes on after the answer
        self.assertEqual(rig.devices.owner['label'], 'Manual: Pump 7 · OUT')
        self.assertTrue(rig.manual.active)
        # One at a time: nothing else may start meanwhile, on any motor.
        self.busy(rig, {'op': 'dose_volume', 'motor': 3, 'volume_ml': 1, 'direction': 1}, T_OTHER)
        self.busy(rig, {'op': 'clean'}, T_OTHER)
        self.busy(rig, on(3), T_OTHER)
        self.assertEqual(rig.pump.view()['op']['id'], reply['op_id'])
        # OFF on its row ends it.
        self.assertEqual(rig.manual.submit(off(8)), {'stopped': True})
        self.idle(rig)
        self.assertEqual((rig.pump.view()['last_result']['ok'], rig.pump.view()['last_result']['text']),
                         (False, 'has stopped:Pump 7 · OUT Quantitative 5 mL'))
        # A short one, to its end; reverse.
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 3, 'volume_ml': 0.5, 'direction': -1})
        self.assertTrue(reply['accepted'])
        self.idle(rig)
        self.assertEqual((rig.pump.view()['last_result']['ok'], rig.pump.view()['last_result']['text']), (True, 'Pump 2 Quantitative completion:0.5 mL'))
        self.assertIn('P,3,-2279,2825', rig.tx())
        texts = rig.texts()
        for text in ('Manual operation starts:Pump 7 · OUT Quantitative 5 mL', 'Manual operation has been stopped:Pump 7 · OUT Quantitative 5 mL', 'Manual operation starts:Pump 2 reverse quantitative 0.5 mL',
                     'Manual operation completed:Pump 2 reverse quantitative 0.5 mL'):
            self.assertIn(text, texts)
        self.assertLess(texts.index('Manual operation starts:Pump 2 reverse quantitative 0.5 mL'), texts.index('Manual operation completed:Pump 2 reverse quantitative 0.5 mL'))

    def test_weight_dose_and_cleaning(self):
        rig = self.rig(link__simulation_speedup=4)
        reply = rig.manual.submit({'op': 'dose_weight', 'motor': 9, 'target_g': 2})
        self.assertEqual(set(reply), {'accepted', 'op_id'})
        ct.wait_for(lambda: rig.pump.view()['weight_dose'], 5, 'the weight dose to show')
        self.assertEqual((rig.pump.view()['weight_dose']['motor'], rig.pump.view()['weight_dose']['target_g']), (9, 2.0))
        self.busy(rig, {'op': 'syringe_init'}, T_OTHER)
        self.idle(rig, 20)
        result = rig.pump.view()['last_result']
        self.assertTrue(result['ok'])
        self.assertRegex(result['text'], r'^Pump 8 · CuSO₄ Quantitative completion:\d+\.\d\d g (error [+-]\d+\.\d\d g) $')
        self.assertIn('Manual operation completed:Pump 8 · CuSO₄ Weighing and quantitative 2 g', rig.texts())
        reply = rig.manual.submit({'op': 'clean'})
        self.assertEqual(set(reply), {'accepted', 'op_id', 'eta_s'})
        self.assertAlmostEqual(reply['eta_s'], 1.1, places=1)        # the short times of this test rig
        self.assertEqual(rig.devices.owner['label'], 'Manual: automatic cleaning')
        self.idle(rig)
        self.assertEqual(rig.pump.view()['last_result']['text'], 'Automatic cleaning completed')
        self.assertEqual(rig.tx().count('C'), 1)

    def test_the_syringe_pump(self):
        rig = self.rig()
        move = {'op': 'syringe_move', 'kind': 'aspirate', 'position': 2, 'volume_ml': 0.4}
        # What the board refuses comes back with the request, and nothing is left held.
        self.refused(rig, move, 'refused', 'The syringe pump has not been initialized yet.')
        self.assertIsNone(rig.devices.owner)
        self.assertFalse(rig.manual.active)
        reply = rig.manual.submit({'op': 'syringe_init'})
        self.assertEqual(set(reply), {'accepted', 'op_id'})
        self.idle(rig)
        self.assertTrue(rig.pump.view()['syringe']['initialised'])
        reply = rig.manual.submit(move)
        self.assertEqual((reply['accepted'], reply['eta_s']), (True, 1.0))
        self.assertEqual(rig.devices.owner['label'], 'Manual: Syringe pump Only')
        self.idle(rig)
        syringe = rig.pump.view()['syringe']
        self.assertEqual((syringe['volume_ul'], syringe['position']), (400, 2))
        self.refused(rig, dict(move, volume_ml=4.8), 'refused', 'The syringe cannot be installed (existing 0.4 mL,capacity 5 mL) ')
        self.refused(rig, dict(move, kind='dispense', volume_ml=1), 'refused', 'There are only 0.4 mL,Can’t push it out 1 mL')
        self.assertIsNone(rig.devices.owner)
        rig.manual.submit(dict(move, kind='dispense', position=1))
        self.idle(rig)
        self.assertEqual(rig.pump.view()['syringe']['volume_ul'], 0)
        self.assertEqual(rig.pump.view()['last_result']['text'], 'Syringe pump injection completed:0.4 mL')
        texts = rig.texts()
        for text in ('Manual operation starts: syringe pump initialization', 'Manual operation completed: syringe pump initialization', 'Manual operation completed: syringe pump pumping valve position 2 · 0.4 mL',
                     'Manual operation completed: syringe pump injection valve position Only 1 · 0.4 mL'):
            self.assertIn(text, texts)
        self.assertEqual([text for text in texts if text.startswith('Manual operation failed')], [])     # a refusal is the request's answer, not an event

    def test_an_operation_that_fails_after_it_was_accepted_is_told_in_the_event_log(self):
        rig = self.rig()
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 8, 'volume_ml': 5, 'direction': 1})
        self.assertTrue(reply['accepted'])
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        ct.pt.FaultPlan.reset_board(rig.board)       # the board restarts by itself in the middle
        self.idle(rig)
        self.assertEqual(rig.pump.view()['last_result']['text'], 'Operation failed: The pump control board restarted itself')
        failed = next(row for row in rig.store.events(limit=50) if row['text'].startswith('Manual operation failed'))
        self.assertEqual((failed['text'], failed['level']), ('Manual operation failed:Pump 7 · OUT Quantitative 5 mL (The pump control board restarted itself)', 'error'))


class InterlockTests(Base):
    def test_without_the_rpi_every_start_needs_the_tick(self):
        rig = self.rig(rpi=False)
        reason = [{'code': 'hv_unknown', 'text': TICK, 'fix': ''}]
        self.assertEqual(rig.devices.view()['interlock']['manual'],
                         {'ok': True, 'reasons': [], 'need_hv_ack': True, 'hv_ack_text': ASK_NO_RPI})
        sent = len(rig.tx())
        for body in (on(8), {'op': 'dose_volume', 'motor': 8, 'volume_ml': 1, 'direction': 1}, {'op': 'dose_weight', 'motor': 8, 'target_g': 1},
                     {'op': 'clean'}, {'op': 'syringe_init'}, {'op': 'syringe_move', 'kind': 'aspirate', 'position': 1, 'volume_ml': 1},
                     on(8, hv_off_checked='yes'), on(8, hv_off_checked=1)):
            self.assertEqual(self.blocked(rig, body), reason)
            self.assertIsNone(rig.devices.owner)
        self.assertEqual([command for command in rig.tx()[sent:] if command != '?'], [])
        self.assertEqual(rig.manual.submit(off(8)), {'stopped': True})       # a stop needs no tick
        # With the tick of the start dialog (“I have gone to the site to confirm that the high-voltage power supply is turned off.”).
        self.assertEqual(rig.manual.submit(on(8, seconds=60, hv_off_checked=True))['accepted'], True)
        self.assertEqual(rig.board.mstate['B'], 'move')
        self.assertEqual(self.blocked(rig, on(3)), reason)                   # each start asks again
        self.assertEqual(rig.manual.submit(on(3, hv_off_checked=True))['accepted'], True)
        rig.manual.submit(off(8))
        rig.manual.submit(off(3))
        self.idle(rig)
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 8, 'volume_ml': 0.5, 'direction': 1, 'hv_off_checked': True})
        self.assertTrue(reply['accepted'])
        self.idle(rig)
        self.assertEqual(rig.pump.view()['last_result']['text'], 'Pump 7 · OUT Quantitative completion:0.5 mL')

    def test_with_the_high_voltage_on_nothing_starts_by_hand(self):
        rig = self.rig()
        rig.supply(True)
        for body in (on(8), on(8, hv_off_checked=True), {'op': 'clean'}, {'op': 'syringe_init', 'hv_off_checked': True}):
            self.assertEqual(self.blocked(rig, body), [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}])
        self.assertIsNone(rig.devices.owner)
        self.assertEqual(rig.manual.submit(off(8)), {'stopped': True})
        rig.supply(False)
        self.assertEqual(rig.manual.submit(on(8, seconds=1))['accepted'], True)
        self.idle(rig)
        # While a round discharges (its high-voltage session is open).
        with rig.devices.hv_session():
            self.assertEqual(self.blocked(rig, on(8)), [{'code': 'hv_on', 'text': 'is discharging', 'fix': 'Press ”Stop” at the bottom STOP”'}])
        self.assertTrue(rig.pumps_off())

    def test_what_was_started_by_hand_ends_when_the_supply_is_switched_on_elsewhere(self):
        """The watch of Devices: a pump must not keep running once the high voltage is seen ON."""
        text = 'High voltage is displayed as ON,But the pump is still running: All pumps have been stopped. Please go to the site to confirm.'
        rig = self.rig()
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 8, 'volume_ml': 50, 'direction': 1})
        self.assertTrue(reply['accepted'])
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        rig.supply(True)                 # not by this program
        self.idle(rig)
        self.assertTrue(rig.pumps_off())
        self.assertEqual(rig.pump.view()['last_result']['text'], 'has stopped:Pump 7 · OUT Quantitative 50 mL')
        self.assertIn('Manual operation has been stopped:Pump 7 · OUT Quantitative 50 mL', rig.texts())
        alarm = ct.wait_for(lambda: rig.alarm('pump_with_hv'), 3, 'the alarm')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (1, 'pump', text))
        ct.wait_for(lambda: [row for row in rig.store.events(limit=100) if (row['text'], row['level']) == (text, 'error')], 3,
                    'the line in the event log')
        self.assertEqual(self.blocked(rig, on(8)), [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}])
        rig.supply(False)
        self.assertEqual(rig.manual.submit(on(8, seconds=1))['accepted'], True)      # and by hand again
        self.idle(rig)

    def test_after_a_high_voltage_stop_that_was_never_confirmed_the_tick_on_site_lets_a_pump_start(self):
        self.patch(devices, 'HV_SETTLE_SECONDS', 0.4)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        rig = self.rig()
        with rig.devices.hv_session():
            rig.rpi_gone()               # the RPi goes away in the middle of a discharge: its end is never read back
        self.assertTrue(rig.devices.hv_active)
        self.assertEqual(rig.devices.view()['interlock']['manual'],
                         {'ok': True, 'reasons': [], 'need_hv_ack': True, 'hv_ack_text': ASK_NO_RPI})
        for body in (on(8), on(8, hv_off_checked='yes'), {'op': 'clean'}):
            self.assertEqual(self.blocked(rig, body), [{'code': 'hv_unknown', 'text': TICK, 'fix': ''}])
        self.assertTrue(rig.devices.hv_active)
        # Somebody has been on site and ticks “I have gone to the site to confirm that the high-voltage power supply is turned off.”: as after a restart of the program.
        self.assertEqual(rig.manual.submit(on(8, seconds=60, hv_off_checked=True))['accepted'], True)
        self.assertFalse(rig.devices.hv_active)
        self.assertEqual(rig.board.mstate['B'], 'move')
        self.assertEqual(self.blocked(rig, on(3)), [{'code': 'hv_unknown', 'text': TICK, 'fix': ''}])    # each start asks again
        rig.manual.submit(off(8))
        self.idle(rig)
        alarm = rig.alarm('hv_stop_unconfirmed')     # it stays until the supply is read back as off
        self.assertEqual((alarm['priority'], alarm['cleared']), (1, False))

    def test_with_the_supply_switched_off_at_its_mains_the_tick_lets_a_pump_run(self):
        """7h. The RPi is connected, the high-voltage supply does not answer: the usual state of the real rig
        between measurements. Refused without the tick, works with it; a supply that reads ON is still refused."""
        rig = self.rig()
        self.assertEqual(rig.devices.view()['interlock']['manual'], {'ok': True, 'reasons': [], 'need_hv_ack': False, 'hv_ack_text': ''})
        rig.supply_answers(False)
        self.assertTrue(rig.rpi.connected)
        self.assertEqual(rig.devices.view()['interlock']['manual'],
                         {'ok': True, 'reasons': [], 'need_hv_ack': True, 'hv_ack_text': ASK_SILENT})
        sent = len(rig.tx())
        for body in (on(8), on(8, hv_off_checked='yes'), on(8, hv_off_checked=1), {'op': 'dose_volume', 'motor': 8, 'volume_ml': 1, 'direction': 1},
                     {'op': 'clean'}, {'op': 'syringe_init'}):
            self.assertEqual(self.blocked(rig, body), NO_SILENT)
            self.assertIsNone(rig.devices.owner)
        self.assertEqual([command for command in rig.tx()[sent:] if command != '?'], [])
        # With the tick: a pump runs for its seconds and stops by itself, another one beside it, then a dose.
        self.assertEqual(rig.manual.submit(on(8, seconds=2, hv_off_checked=True)), {'accepted': True, 'motor': 8, 'until_s': 2})
        self.assertEqual(rig.board.mstate['B'], 'move')
        self.assertEqual(self.blocked(rig, on(3)), NO_SILENT)                # each start asks again
        self.assertEqual(rig.manual.submit(on(3, hv_off_checked=True))['accepted'], True)
        rig.manual.submit(off(3))
        ct.wait_for(lambda: rig.board.mstate['B'] == 'idle', 5, 'the board to end the run')
        ct.wait_for(lambda: (rig.pump.view()['last_result'] or {}).get('text') == 'Pump 7 · OUT arrived at the setting 2 seconds, stopped automatically', 3,
                    'the result line of the run that ended by itself')
        self.idle(rig)
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 8, 'volume_ml': 0.5, 'direction': 1, 'hv_off_checked': True})
        self.assertTrue(reply['accepted'])
        self.idle(rig)
        self.assertEqual(rig.pump.view()['last_result']['text'], 'Pump 7 · OUT Quantitative completion:0.5 mL')
        self.assertEqual(rig.alarm_keys(), [])
        # The supply answers again and reads ON: what was read decides, and the tick is ignored.
        rig.supply_answers(True)
        rig.supply(True)
        for body in (on(8), on(8, hv_off_checked=True)):
            self.assertEqual(self.blocked(rig, body), [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': 'Press ”Stop” at the bottom STOP”'}])
        view = rig.devices.view()['interlock']['manual']
        self.assertEqual((view['ok'], view['need_hv_ack'], view['hv_ack_text']), (False, False, ''))
        rig.supply(False)
        self.assertEqual(rig.manual.submit(on(8, seconds=1))['accepted'], True)      # reads off: no tick is asked for
        self.idle(rig)

    def test_stop_with_the_supply_switched_off_at_its_mains(self):
        """7i, the usual state of the real rig: STOP ends what was started by hand, and the RPi's half is `silent`
        (the supply does not answer, the Nano is confirmed quiet) — a lesser alarm, not an emergency."""
        silent = 'The high voltage power supply does not respond and the program cannot confirm that it is off;Nano Confirmed no output. If the power is turned off from the switch, this is normal.'
        rig = self.rig()
        rig.supply_answers(False)
        rig.manual.submit(on(8, seconds=60, hv_off_checked=True))
        self.assertEqual(rig.board.mstate['B'], 'move')
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual((report['manual_cancelled'], report['pump']['result'], report['rpi']),
                         (True, 'confirmed', {'result': 'silent', 'error': silent}))
        self.idle(rig)
        self.assertTrue(rig.pumps_off())
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (3, 'high pressure', silent))
        self.assertIn('Stop pressed: pump stopped; high voltage power supply not responding (Nano Confirmed no output)', rig.texts())
        self.assertEqual(rig.manual.submit(on(8, seconds=1, hv_off_checked=True))['accepted'], True)     # and by hand again
        self.idle(rig)

    def test_stop_on_a_rig_without_an_rpi(self):
        rig = self.rig(rpi=False)
        rig.manual.submit(on(8, seconds=60, hv_off_checked=True))
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual((report['manual_cancelled'], report['pump']['result'], report['rpi']['result']),
                         (True, 'confirmed', 'unreachable'))
        self.idle(rig)
        self.assertTrue(rig.pumps_off())
        alarm = rig.alarm('hv_stop_unconfirmed')     # middle: nothing says that the high voltage is on
        self.assertEqual((alarm['priority'], alarm['text']), (3, 'The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm.'))
        self.assertEqual(rig.manual.submit(on(8, seconds=1, hv_off_checked=True))['accepted'], True)
        self.idle(rig)

    def test_manual_work_and_the_schedule(self):
        rig = self.rig(speed=100)
        rig.start(count=4, interval=3)
        ct.wait_for(lambda: rig.schedule()['counts'].get('done') == 1 and rig.schedule()['status'] == 'waiting', 10, 'the first slot')
        # The schedule waits for its next slot: nothing by hand.
        self.busy(rig, on(8), T_SCHEDULE)
        self.busy(rig, {'op': 'syringe_init'}, T_SCHEDULE)
        self.assertEqual(rig.devices.view()['interlock']['manual']['reasons'][0], {'code': 'schedule_active', 'text': T_SCHEDULE, 'fix': ''})
        # Paused between two rounds: manual work is possible (as in the original).
        self.assertEqual(rig.runner.pause()['status'], 'paused')
        self.assertEqual(rig.devices.view()['interlock']['manual']['ok'], True)
        self.assertEqual(rig.manual.submit(on(8, seconds=60))['accepted'], True)
        self.assertEqual(rig.board.mstate['B'], 'move')
        # RESUME while that pump still runs: refused.
        with self.assertRaises(Blocked) as caught:
            rig.runner.resume()
        self.assertEqual((str(caught.exception), caught.exception.reasons),
                         ('The schedule cannot be continued now', [{'code': 'manual_active', 'text': 'The manually started pump is still running, please stop it first.', 'fix': ''}]))
        self.assertEqual(rig.schedule()['status'], 'paused')
        self.assertEqual(rig.board.mstate['B'], 'move')
        rig.manual.submit(off(8))
        self.assertEqual(rig.runner.resume()['status'], 'waiting')
        self.busy(rig, on(8), T_SCHEDULE)
        rig.devices.stop_everything('test')
        self.assertEqual(rig.finished()['status'], 'stopped')
        self.assertEqual(rig.manual.submit(on(8, seconds=1))['accepted'], True)      # and by hand again
        self.idle(rig)

    def test_a_paused_round_keeps_the_equipment(self):
        rig = self.rig()
        recipe_id = rig.publish('discharge', ct.csv('pump_on,M8,40,mL/min,', 'delay,,20,s,', 'pump_off,M8,,,'))
        rig.start(recipe_id)
        rig.wait_step('delay')
        self.busy(rig, on(3), T_SCHEDULE)
        rig.runner.pause()
        rig.wait_status('paused')
        self.busy(rig, on(3), T_PAUSED_ROUND)
        self.busy(rig, {'op': 'clean'}, T_PAUSED_ROUND)
        self.assertEqual(rig.manual.submit(off(3)), {'stopped': True})
        rig.devices.stop_everything('test')
        rig.finished()
        self.assertIsNone(rig.devices.owner)

    def test_scale_zero_is_a_tool_and_needs_a_steady_reading(self):
        """Return to zero is not on manual operation (it lives behind the PIN): it takes the equipment as a tool."""
        rig = self.rig()
        rig.manual.submit(on(8, seconds=60))
        with self.assertRaises(Busy) as caught:
            rig.devices.acquire('tool', 'tare', 'Scale reset to zero')
        self.assertEqual(str(caught.exception), T_OTHER)
        rig.manual.submit(off(8))
        self.idle(rig)
        rig.devices.acquire('tool', 'tare', 'Scale reset to zero')
        self.busy(rig, on(8), T_OTHER)
        from backend import simulator
        with mock.patch.object(simulator.SimulatedMega, '_weight_line', lambda sim: 'WEIGHT,1.234,RAW,355025.6,ST,0'):
            ct.wait_for(lambda: rig.pump.view()['scale']['stable'] is False and rig.pump.view()['scale']['grams'] == 1.234, 5,
                        'a reading that still changes')
            with self.assertRaises(PumpBoardError) as refused:
                rig.pump.scale_tare()
            self.assertEqual((refused.exception.code, refused.exception.text), ('blocked', 'The reading value is still changing, wait for it to stabilize.'))
        ct.wait_for(lambda: rig.pump.view()['scale']['stable'], 5, 'a steady reading')
        self.assertEqual(set(rig.pump.scale_tare()['calibration']), {'divisor', 'offset', 'reference_grams', 'saved_at'})
        self.assertEqual([command for command in rig.tx() if command.startswith('W,TARE')], ['W,TARE'])
        rig.devices.release('tare')
        self.assertIsNone(rig.devices.owner)


class WatchdogTests(Base):
    """SPEC B.12 "Page closes during a manual continuous run": manual work is tied to the page."""

    def test_a_page_that_went_silent_stops_what_was_started_by_hand(self):
        self.pump_patched(UI_WATCHDOG_SECONDS=0.6)
        rig = self.rig()
        text = 'The operation screen exceeds 0.6 No response for seconds, manually started pump stopped'
        rig.manual.submit(on(8, seconds=120))
        rig.manual.submit(on(2, seconds=120))
        rig.page_age = 0.4
        time.sleep(0.5)
        self.assertEqual((rig.board.mstate['A'], rig.board.mstate['B']), ('move', 'move'))
        rig.page_age = 0.9               # the browser was closed
        ct.wait_for(rig.pumps_off, 5, 'the pumps to stop')
        alarm = ct.wait_for(lambda: rig.alarm('page_lost'), 3, 'the alarm')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (3, 'picture', text))
        self.assertIn('X', rig.tx()[-12:])
        self.idle(rig)                   # nobody is left holding the equipment
        # A dose that runs when the page goes away ends as stopped, with that reason.
        rig.page_age = 0.0
        reply = rig.manual.submit({'op': 'dose_volume', 'motor': 8, 'volume_ml': 50, 'direction': 1})
        self.assertTrue(reply['accepted'])
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        rig.page_age = 5.0
        self.idle(rig)
        self.assertTrue(rig.pumps_off())
        self.assertEqual(rig.pump.view()['last_result']['text'], 'has stopped:Pump 7 · OUT Quantitative 50 mL')
        self.assertIn('Manual operation has been stopped:Pump 7 · OUT Quantitative 50 mL', rig.texts())
        # The page is back: by hand again.
        rig.page_age = 0.0
        self.assertEqual(rig.manual.submit(on(8, seconds=1))['accepted'], True)
        self.idle(rig)

    def test_two_pages_press_at_the_same_moment(self):
        """B.12 "Two browser tabs": commands are serialised; one of two equal starts is refused."""
        rig = self.rig()
        answers = []

        def press(body):
            try:
                answers.append(rig.manual.submit(body)['accepted'])
            except (Busy, PumpBoardError, Blocked) as exc:
                answers.append(type(exc).__name__)

        for body in (on(8, seconds=30), {'op': 'dose_volume', 'motor': 3, 'volume_ml': 4, 'direction': 1}):
            del answers[:]
            tabs = [threading.Thread(target=press, args=(body,)) for _ in range(2)]
            for tab in tabs:
                tab.start()
            for tab in tabs:
                tab.join(10)
            self.assertEqual(answers.count(True), 1, answers)
            self.assertEqual(len(answers), 2)
            self.assertEqual(len([c for c in rig.tx() if c.startswith('P,%d,' % body['motor'])]), 1)
            self.assertEqual(rig.devices.view()['owner'], rig.devices.view()['owner'])       # one state for both pages
            # STOP from either page works, and twice does no harm.
            first, second = rig.devices.stop_everything('Wang Xiaoming'), rig.devices.stop_everything('Li Dahua')
            self.assertEqual((first['manual_cancelled'], first['pump']['result'], second['pump']['result']),
                             (True, 'confirmed', 'confirmed'))
            self.idle(rig)
            self.assertTrue(rig.pumps_off())


if __name__ == '__main__':
    unittest.main(verbosity=2)
