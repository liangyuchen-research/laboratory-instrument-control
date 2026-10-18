"""AMENDMENTS M16 ’s automatic test: two (or more) pumps are quantified at the same time, each pump has its own calibration, and the process of the schedule page (liquid addition The configuration file of → Measure → Empty → cleaning).

Three layers are tested: the packaging layer of the pump control board ( Existing data onpc/pumpboard.py,(for the simulated board), one round of process (runner/steps,For analog boards and analog RPi) , 
HTTP (/api/flow, /api/pump/calibration…) .are all emulators: no real ports are opened, only 127.0.0.1.

    python tests/test_flow.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'pc')]

import core_testing as ct  # noqa: E402  (first: it sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import pump_testing as pt  # noqa: E402
import pumpboard  # noqa: E402
import recipe  # noqa: E402
import store as store_module  # noqa: E402
import test_app  # noqa: E402
import test_pumpboard  # noqa: E402
from pumpboard import PumpBoardError  # noqa: E402

FACTOR = 4558.96147256               # dose_pulses_per_ml of the vendored hardware.yaml
SPS = 2825                           # 40 mL/min with V7's speed factor: the same for every pump
FLOW_ID = store_module.FLOW_RECIPE_ID
# The four stages of AMENDMENTS M16, as a flow.json of before M23 holds them ...
STAGES = {'fill': [{'pump': 1, 'mode': 'ml', 'amount': 2}, {'pump': 8, 'mode': 'ml', 'amount': 1}],
          'measure': True, 'drain': {'on': True, 'ml': 3}, 'clean': False}
# ... and the same round as the step list of M23: rows of a Recipe.
STEPS = [['dose_together', 'P1+P8', '2+1', 'mL', 'Add liquid'], ['plasma_run', '', '', '', 'discharge＋capture spectrum'],
         ['dose_volume', 'P7', '3', 'mL', 'Empty']]
STEP_TEXTS = ['Simultaneous quantification:Pump 1 · Water:2 mL, Pump 8 · CuSO₄: 1 mL', 'discharge＋capture spectrum', 'Pump 7 · OUT Quantitative 3 mL']


def setUpModule():
    ct.set_up()


def tearDownModule():
    ct.tear_down()


def steps_of(volume, factor=FACTOR):
    return max(1, round(volume * factor))


def doses_sent(rig):
    return [command for command in rig.tx() if command.startswith('P,')]


class BoardCase(test_pumpboard.Case):
    def board(self, **options):
        return self.rig(sim_firmware='plasmic', **options)

    def code(self, expected, call, *args):
        with self.assertRaises(PumpBoardError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, expected, caught.exception.text)
        return caught.exception.text


# =========================================================================================================
class DoseManyTests(BoardCase):
    def test_two_driver_groups_turn_together_and_one_group_takes_its_pumps_in_turn(self):
        rig = self.board(link__simulation_speedup=4)
        doses = [(2, 4.0), (9, 2.0), (3, 2.0), (8, 4.0)]            # group A: 2 then 3; group B: 9 then 8
        box = self.background(rig.board.dose_many, doses, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['A'] != 'idle' and rig.sim.mstate['B'] != 'idle', what='both groups to turn at once')
        view = rig.board.view()
        self.assertEqual((view['op']['kind'], view['op']['by'], view['op']['motor']), ('dose_many', 'manual', None))
        self.assertRegex(view['op']['text'], r'^Simultaneous quantification:Pump 1 · Water:4 mL, Pump 8 · CuSO₄: 2 mL, Pump 2: 2 mL, Pump 7 · OUT: 4 mL'
                                             r' · already \d+ Second/about \d+ Second$')
        for motor in (2, 9):                                         # the first of each group, both for this operation
            row = rig.motor(motor)
            self.assertIn(row['state'], ('wait', 'move'))
            self.assertEqual(row['by'], 'manual')
        for motor in (3, 8):                                         # the second of each group waits for its driver
            self.assertEqual(rig.motor(motor)['state'], 'off')
        out = self.finished(box, 20).get('result')
        self.assertIsNotNone(out, box)
        self.assertEqual([(row['motor'], row['volume_ml'], row['steps'], row['sps']) for row in out['doses']],
                         [(motor, volume, steps_of(volume), SPS) for motor, volume in doses])
        sent = doses_sent(rig)
        self.assertEqual(sorted(sent), sorted('P,%d,%d,%d' % (motor, steps_of(volume), SPS) for motor, volume in doses))
        first = {motor: next(index for index, command in enumerate(sent) if command.startswith('P,%d,' % motor))
                 for motor, _ in doses}
        self.assertLess(first[2], first[3])                          # in the order given, inside a group
        self.assertLess(first[9], first[8])
        self.assertLess(max(first[2], first[9]), min(first[3], first[8]))    # the two first ones went out side by side
        # Side by side is what it saves: the whole took less than the four one after the other.
        alone = sum(steps_of(volume) / SPS / 4 for _, volume in doses)
        self.assertLess(out['elapsed_s'], alone - 0.5)
        self.assertTrue(rig.all_idle())
        self.assertEqual(rig.board.quiescent(), (True, []))
        result = rig.board.view()['last_result']
        self.assertEqual((result['ok'], result['text']),
                         (True, 'Quantitative completion:Pump 1 · Water:4 mL, Pump 8 · CuSO₄: 2 mL, Pump 2: 2 mL, Pump 7 · OUT: 4 mL'))
        self.assertEqual(rig.guard.lines, [])                        # every dose has an end of its own in the board

    def test_one_pump_and_every_pump(self):
        rig = self.board()
        out = rig.board.dose_many([(5, 0.5)], 'round', threading.Event())
        self.assertEqual([(row['motor'], row['steps']) for row in out['doses']], [(5, steps_of(0.5))])
        self.assertIsNone(rig.board.view()['last_result'])           # a round's operation is the round's to report
        out = rig.board.dose_many([(motor, 0.2) for motor in range(2, 10)], 'manual', threading.Event())
        self.assertEqual([row['motor'] for row in out['doses']], list(range(2, 10)))
        self.assertTrue(rig.all_idle())

    def test_everything_is_checked_before_anything_moves(self):
        rig = self.board()
        board, stop = rig.board, threading.Event()
        self.refused('refused', 'No pump to be quantitatively selected', board.dose_many, [], 'manual', stop)
        self.refused('refused', 'Pump 1 · Water selected twice', board.dose_many, [(2, 1), (3, 1), (2, 2)], 'manual', stop)
        self.refused('refused', 'does not have this pump (1) ', board.dose_many, [(2, 1), (1, 1)], 'manual', stop)
        self.refused('refused', 'does not have this pump (8) ', board.dose_many, [('8', 1)], 'manual', stop)
        self.refused('refused', 'The simultaneous quantitative content format is incorrect.', board.dose_many, [2, 3], 'manual', stop)
        self.refused('refused', 'The simultaneous quantitative content format is incorrect.', board.dose_many, None, 'manual', stop)
        for volume in (0, 0.001, 1000.5, -1, 'x', None):             # V7's own limits of a dose, for every one of them
            self.code('refused', board.dose_many, [(2, 1), (9, volume)], 'manual', stop)
        with self.assertRaises(ValueError):
            board.dose_many([(2, 1)], 'somebody', stop)
        self.assertEqual(doses_sent(rig), [])
        self.assertFalse(board.op_lock.locked())
        # A pump that still turns: not at rest, so nothing is added to it.
        board.run(8, 40, 1, 30, 'manual')
        self.code('refused', board.dose_many, [(2, 1), (3, 1)], 'manual', stop)
        board.stop_motor(8)
        # High voltage on (or not known to be off): no motion command leaves this PC.
        board.set_gate(lambda: True)
        self.refused('blocked', test_pumpboard.T_HV, board.dose_many, [(2, 1), (9, 1)], 'manual', stop)
        self.assertEqual([command for command in doses_sent(rig) if not command.startswith('P,8,')], [])
        self.assertTrue(rig.all_idle())

    def test_a_stop_ends_all_of_it(self):
        for way in ('stop_all', 'abort', 'off'):
            with self.subTest(way=way):
                rig = self.board(link__simulation_speedup=1)
                abort = threading.Event()
                box = self.background(rig.board.dose_many, [(3, 20.0), (8, 20.0), (4, 5.0)], 'manual', abort)
                rig.wait(lambda: rig.sim.mstate['A'] == 'move' and rig.sim.mstate['B'] == 'move')
                if way == 'stop_all':
                    self.assertEqual(rig.board.stop_all('stop'), {'sent': True, 'confirmed': True, 'error': ''})
                elif way == 'abort':
                    abort.set()
                else:
                    rig.board.stop_motor(8)                          # “OFF” on one of them: the other one stops as well
                self.assertEqual(self.finished(box).get('error'), ('stopped', 'Stopped'))
                rig.wait(rig.all_idle, what='everything to stand still')
                self.assertNotIn('P,4,%d,%d' % (steps_of(5.0), SPS), doses_sent(rig))     # the queued one never started
                self.assertEqual(rig.board.view()['last_result']['ok'], False)
                self.assertFalse(rig.board.op_lock.locked())
                rig.close()

    def test_a_dose_that_does_not_end_brings_everything_down(self):
        self.patched(DOSE_MANY_LIMIT_FACTOR=1.0, DOSE_LIMIT_EXTRA_S=0.6)
        rig = self.board()
        plan = self.plan()
        plan.freeze_moves = True                                     # the board never reports the moves as done
        started = time.perf_counter()
        self.refused('timeout', 'The quantitative exceeding time has not ended yet and all have been stopped.', rig.board.dose_many, [(3, 1.0), (8, 1.0)], 'manual',
                     threading.Event())
        self.assertLess(time.perf_counter() - started, 6)
        self.assertIn('X', rig.tx()[-6:])
        self.assertTrue(rig.all_idle())


# =========================================================================================================
class PumpCalibrationTests(BoardCase):
    def factors(self, board):
        return {row['motor']: row['dose_pulses_per_ml'] for row in board.pump_calibration()}

    def test_ten_millilitres_that_came_out_as_less_make_that_pump_step_more(self):
        rig = self.board()
        board = rig.board
        rows = board.pump_calibration()
        self.assertEqual([(row['motor'], row['pump'], row['label']) for row in rows][:3],
                         [(2, 1, 'Pump 1 · water'), (3, 2, 'Pump 2'), (4, 3, 'Pump 3')])
        self.assertEqual({(row['dose_pulses_per_ml'], row['factory_pulses_per_ml'], row['calibrated'], row['measured_ml'])
                          for row in rows}, {(FACTOR, FACTOR, False, None)})
        self.assertEqual(board.dose(3, 10, 1, 'manual', threading.Event())['steps'], steps_of(10))
        out = board.calibrate_pump(3, 9.5)                           # 10 mL were asked for, 9.5 came out
        new = round(FACTOR * 10 / 9.5, 4)
        self.assertEqual(out, {'motor': 3, 'dose_pulses_per_ml': new, 'before': FACTOR, 'change_percent': 5.26})
        self.assertEqual(self.factors(board), {**{motor: FACTOR for motor in range(2, 10)}, 3: new})
        row = board.pump_calibration()[1]
        self.assertEqual((row['calibrated'], row['nominal_ml'], row['measured_ml'], row['factory_pulses_per_ml']),
                         (True, 10.0, 9.5, FACTOR))
        self.assertRegex(row['saved_at'], r'^20\d\d-\d\d-\d\dT')
        # The very next dose of that pump counts with it; the others, and the speed of all of them, are as before.
        self.assertEqual(board.dose(3, 10, 1, 'manual', threading.Event())['steps'], steps_of(10, new))
        self.assertEqual(board.dose(4, 10, 1, 'manual', threading.Event())['steps'], steps_of(10))
        self.assertEqual(doses_sent(rig)[-2:], ['P,3,%d,%d' % (steps_of(10, new), SPS), 'P,4,%d,%d' % (steps_of(10), SPS)])
        out = board.dose_many([(3, 1.0), (8, 1.0)], 'manual', threading.Event())
        self.assertEqual([row['steps'] for row in out['doses']], [steps_of(1, new), steps_of(1)])
        self.assertEqual(board.hardware()['groups'][0]['motors'][1]['dose_pulses_per_ml'], new)
        self.assertEqual(board.facts()['pump_dose_pulses_per_ml'], {'3': new})
        self.assertEqual(rig.events[-1], ('Corrected Pump 2: beat 10 mL,Actual 9.5 mL; Every mL number of steps 4559 → 4798.9 (+5.3%) ', 'info'))
        # A second calibration starts from the first: 10 mL asked, 10.2 measured with the new factor.
        again = board.calibrate_pump(3, 10.2)
        self.assertEqual(again['dose_pulses_per_ml'], round(new * 10 / 10.2, 4))
        self.assertEqual(again['before'], new)
        # It is kept in the settings file, and a program that starts later reads it there.
        saved = json.loads((rig.folder / 'pumpboard.json').read_text(encoding='utf-8'))['pump_calibration']
        self.assertEqual(sorted(saved), ['3'])
        self.assertEqual((saved['3']['dose_pulses_per_ml'], saved['3']['measured_ml'], saved['3']['before']),
                         (again['dose_pulses_per_ml'], 10.2, new))
        folder = Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.addCleanup(shutil.rmtree, folder, True)
        shutil.copy(rig.folder / 'pumpboard.json', folder / 'pumpboard.json')
        rig.close()
        later = self.board(folder=folder)
        self.assertEqual(self.factors(later.board)[3], again['dose_pulses_per_ml'])
        self.assertEqual(later.board.dose(3, 10, 1, 'manual', threading.Event())['steps'],
                         steps_of(10, again['dose_pulses_per_ml']))

    def test_another_volume_than_ten(self):
        rig = self.board()
        out = rig.board.calibrate_pump(9, 21.0, 20)
        self.assertEqual(out['dose_pulses_per_ml'], round(FACTOR * 20 / 21.0, 4))
        self.assertEqual(rig.events[-1][0], 'Corrected Pump 8 · CuSO₄: beat 20 mL,Actual 21 mL; Every mL number of steps 4559 → 4341.9 (-4.8%) ')

    def test_reset_goes_back_to_the_settings_file_of_the_board(self):
        rig = self.board()
        board = rig.board
        board.calibrate_pump(2, 11)
        board.calibrate_pump(8, 9)
        self.assertEqual(board.reset_pump_calibration(2), {'motor': 2, 'dose_pulses_per_ml': FACTOR})
        self.assertEqual(self.factors(board)[2], FACTOR)
        self.assertNotEqual(self.factors(board)[8], FACTOR)          # the other calibration stays
        self.assertEqual(board.dose(2, 1, 1, 'manual', threading.Event())['steps'], steps_of(1))
        self.assertEqual(rig.events[-1], ('Pump 1 · Water calibration has been cleared and returned to the original setting (every mL 4559 step)', 'info'))
        self.assertEqual(board.reset_pump_calibration(5), {'motor': 5, 'dose_pulses_per_ml': FACTOR})     # nothing to clear

    def test_what_is_not_a_measurement(self):
        rig = self.board()
        board = rig.board
        far = 'The measured volume sum 10 mL Too much difference (should be in 5–20 mL ). Please confirm before entering'
        for measured in (0, 4.99, 20.01, 100, -9.5, float('nan'), float('inf')):
            self.refused('refused', far, board.calibrate_pump, 3, measured)
        for measured in ('9.5', None, [], {}):
            self.code('refused', board.calibrate_pump, 3, measured)
        for nominal in (0.5, 101, float('nan')):
            self.refused('refused', 'The volume used for calibration should be within 1–100 mL between', board.calibrate_pump, 3, 9.5, nominal)
        self.refused('refused', 'does not have this pump (1) ', board.calibrate_pump, 1, 9.5)
        self.refused('refused', 'does not have this pump (10) ', board.reset_pump_calibration, 10)
        self.assertEqual(set(self.factors(board).values()), {FACTOR})
        self.assertFalse((rig.folder / 'pumpboard.json').exists() and 'pump_calibration' in
                         (rig.folder / 'pumpboard.json').read_text(encoding='utf-8'))
        # Not while a dose is being counted with the factor it started with.
        box = self.background(board.dose, 3, 50, 1, 'manual', threading.Event())
        rig.wait(lambda: rig.sim.mstate['A'] == 'move')
        self.refused('busy', 'The pump control board is performing another action', board.calibrate_pump, 3, 9.5)
        self.refused('busy', 'The pump control board is performing another action', board.reset_pump_calibration, 3)
        board.stop_all('stop')
        self.finished(box)
        # The edges themselves are measurements.
        self.assertEqual(board.calibrate_pump(3, 5)['dose_pulses_per_ml'], round(FACTOR * 2, 4))
        self.assertEqual(board.calibrate_pump(4, 20)['dose_pulses_per_ml'], round(FACTOR / 2, 4))

    def test_a_board_that_is_not_connected_keeps_the_calibration_for_later(self):
        folder = Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.addCleanup(shutil.rmtree, folder, True)
        board = pumpboard.PumpBoard(folder, 'off', pt.StubStore(folder), None, None, None)
        self.addCleanup(board.close)
        new = board.calibrate_pump(6, 10.5)['dose_pulses_per_ml']
        self.assertEqual({row['motor']: row['dose_pulses_per_ml'] for row in board.pump_calibration()}[6], new)


# =========================================================================================================
class RoundTests(ct.Case):
    """One round of a schedule with the new step, on the simulated board and the simulated RPi."""

    def test_the_step_that_doses_several_pumps(self):
        rig = self.rig()
        chosen = rig.publish('Two together', ct.csv('dose_together,P1+P8+P2,2+1+0.5,mL,Add liquid', 'plasma_run,,,,',
                                             'dose_volume,P7,1,mL,Empty'))
        rig.start(chosen)
        done = rig.finished(60)
        self.assertEqual(done['status'], 'completed', done)
        record = rig.round()
        self.assertEqual([phase['name'] for phase in record['phases']], ['Add liquid', 'Measure', 'Empty'])
        first = record['steps'][0]
        self.assertEqual((first['action'], first['status'], first['text'], first['motors'], first['amounts']),
                         ('dose_together', 'done', 'Simultaneous quantification:Pump 1 · Water:2 mL, Pump 8 · CuSO₄: 1 mL, Pump 2: 0.5 mL', [2, 9, 3], [2, 1, 0.5]))
        self.assertEqual([(row['motor'], row['volume_ml'], row['steps']) for row in first['result']['doses']],
                         [(2, 2, steps_of(2)), (9, 1, steps_of(1)), (3, 0.5, steps_of(0.5))])
        sent = [command for command in rig.tx() if command.startswith('P,')]
        self.assertEqual(sorted(sent), sorted(['P,2,%d,%d' % (steps_of(2), SPS), 'P,9,%d,%d' % (steps_of(1), SPS),
                                               'P,3,%d,%d' % (steps_of(0.5), SPS), 'P,8,%d,%d' % (steps_of(1), SPS)]))
        self.assertEqual(record['steps'][2]['motor'], 8)             # P7 on the screen is pin 8 of the board
        self.assertTrue(rig.pumps_off() and rig.hv_off())

    def test_stop_during_it_stops_both_pumps_and_the_round(self):
        rig = self.rig()
        chosen = rig.publish('Two together (long)', ct.csv('dose_together,P2+P6,50+50,mL,', 'plasma_run,,,,'))
        rig.start(chosen)
        rig.wait_step('dose_together')
        ct.wait_for(lambda: rig.board.mstate['A'] == 'move' and rig.board.mstate['B'] == 'move', 8, 'both pumps to turn')
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(report['pump'], {'result': 'confirmed', 'error': ''})
        done = rig.finished(20)
        self.assertEqual(done['status'], 'stopped', done)
        self.assertEqual([step['status'] for step in rig.round()['steps']], ['stopped', 'skipped'])
        self.assertTrue(rig.pumps_off())


# =========================================================================================================
class FlowApiTests(test_app.Base):
    """/api/flow (AMENDMENTS M23): the round of the schedule page is a list of steps, kept as the Recipe with the fixed
    id. The person, 2026-10-07: “should be able to set the steps1, Steps2...,allows me to write it down myself and also include the syringe pump in the step options.”."""
    options = {'pump': 'sim'}

    def flow_recipe(self):
        return next((row for row in self.h.get('/api/recipes')['recipes'] if row['id'] == FLOW_ID), None)

    def test_the_list_before_anything_was_saved(self):
        reply = self.h.get('/api/flow')
        self.assertEqual(sorted(reply), ['clean_seconds', 'dose_flow_ml_min', 'error', 'error_step', 'estimate_seconds', 'limits',
                                         'max_steps', 'name', 'pumps', 'recipe_id', 'steps', 'uses_plasma', 'uses_syringe',
                                         'valid', 'valve_positions', 'view', 'warnings', 'weighing'])
        # Only the measurement, as the Recipe “Discharge measurement only”.
        self.assertEqual((reply['steps'], reply['recipe_id'], reply['name']), ([['plasma_run', '', '', '', '']], None, ''))
        self.assertEqual((reply['valid'], reply['error'], reply['error_step'], reply['view']),
                         (True, '', None, [{'text': 'discharge＋capture spectrum', 'ok': True}]))
        self.assertEqual((reply['uses_plasma'], reply['uses_syringe'], reply['max_steps']), (True, False, 60))
        self.assertEqual([(row['pump'], row['motor'], row['label'], row['group'], row['drain'], row['water']) for row in reply['pumps']],
                         [(1, 2, 'Pump 1 · water', 'A', False, True), (2, 3, 'Pump 2', 'A', False, False),
                          (3, 4, 'Pump 3', 'A', False, False), (4, 5, 'Pump 4', 'A', False, False),
                          (5, 6, 'Pump 5', 'B', False, False), (6, 7, 'Pump 6', 'B', False, False),
                          (7, 8, 'Pump 7 · OUT', 'B', True, False), (8, 9, 'Pump 8 · CuSO₄', 'B', False, False)])
        # The range of every number a step dialog asks for; the syringe's follow its size.
        self.assertEqual(sorted(reply['limits']), ['delay', 'dose_together', 'dose_volume', 'dose_weight', 'syringe_aspirate',
                                                   'syringe_dispense', 'syringe_speed'])
        self.assertEqual(reply['limits']['dose_volume'], {'min': 0.01, 'max': 1000, 'decimals': 2, 'unit': 'mL'})
        self.assertEqual(reply['limits']['delay'], {'min': 0, 'max': 3600, 'decimals': 1, 'unit': 's'})
        self.assertEqual(reply['limits']['syringe_aspirate'], {'min': 0.001, 'max': 5, 'decimals': 3, 'unit': 'mL'})
        self.assertEqual((reply['weighing'], reply['dose_flow_ml_min']), (True, 40))
        self.assertGreaterEqual(reply['valve_positions'], 2)
        self.assertIsNone(self.flow_recipe())

    def test_saving_makes_the_recipe_and_a_schedule_runs_it(self):
        self.h.connected()
        self.h.settled()
        code, reply = self.h.post('/api/flow', {'steps': STEPS}, unlock='')            # no PIN: the operator writes it
        self.assertEqual(code, 200, reply)
        self.assertEqual((reply['recipe_id'], reply['name'], reply['steps']), (FLOW_ID, 'Process: Set by yourself (3 step)', STEPS))
        self.assertEqual(([row['text'] for row in reply['view']], reply['valid'], reply['error']), (STEP_TEXTS, True, ''))
        self.assertGreater(reply['estimate_seconds'], 5)
        row = self.flow_recipe()
        self.assertEqual((row['name'], row['source'], row['valid'], row['uses_pump'], row['uses_plasma'], row['step_count']),
                         ('Process: Set by yourself (3 step)', 'flow', True, True, True, 3))
        self.assertEqual(self.h.get('/api/flow')['steps'], STEPS)
        self.assertIn('Saved process (3 step):' + '; '.join(STEP_TEXTS), self.h.texts())
        self.assertEqual(json.loads((self.h.app.store.dir / 'flow.json').read_text(encoding='utf-8')),
                         {'version': 2, 'flow': {'steps': STEPS}})
        # Started like any other Recipe.
        code, started = self.h.start(recipe_id=FLOW_ID)
        self.assertEqual(code, 200, started)
        # While the schedule uses it, it cannot be changed.
        self.assertEqual(self.h.post('/api/flow', {'steps': STEPS + [['clean', '', '', '', '']]}),
                         (409, {'error': 'Scheduling is in progress: the process can only be modified after stopping.', 'code': 'state'}))
        done = self.h.finished(90)
        self.assertEqual(done['status'], 'completed', done)
        record = self.h.app.store.round(done['slots'][0]['round_id'])
        self.assertEqual((record['recipe']['id'], record['recipe']['name']), (FLOW_ID, 'Process: Set by yourself (3 step)'))
        self.assertEqual([phase['name'] for phase in record['phases']], ['Add liquid', 'Measure', 'Empty'])
        self.assertEqual([step['status'] for step in record['steps']], ['done', 'done', 'done'])
        # Saved again: the same Recipe, with the steps of now (the list still has one flow).
        code, reply = self.h.post('/api/flow', {'steps': [['dose_weight', 'P3', '1.5', 'g', ''], ['clean', '', '', '', '']]})
        self.assertEqual((code, reply['recipe_id'], reply['name']), (200, FLOW_ID, 'Process: Set by yourself (2 step)'))
        # (0.9 s of pumps; nothing saved in “What to connect to the pump?” means no valve is used, so no valve time comes on top)
        self.assertEqual([row['text'] for row in reply['view']],
                         ['Pump 3 scale weight added to 1.5 g', 'automatic cleaning (Pump 1 Water ingress,Pump 7 discharge, approx. 0.9 seconds)'])
        self.assertEqual(len([row for row in self.h.get('/api/recipes')['recipes'] if row['source'] == 'flow']), 1)
        self.assertEqual((self.flow_recipe()['uses_plasma'], reply['uses_plasma']), (False, False))
        self.assertEqual(reply['warnings'], ['Steps 1: Pump 3 No liquid is marked, please make sure its outlet is on the scale The', 'There is no ”plasma” step in this process'])

    def test_the_round_the_person_described(self):
        """2026-10-07: “Call firstpump 1: 5ml,pump 2: 25ml,Exploitpump 3Empty to another tank and make plasma When(10Zhang Pupu),pump 4Let him leave the plasma tank and clean it”,
        with the syringe pump among the steps."""
        self.h.connected()
        self.h.settled()
        steps = [['dose_volume', 'P1', '5', 'mL', ''], ['dose_volume', 'P2', '25', 'mL', ''],
                 ['dose_volume', 'P3', '30', 'mL', 'moved to plasma tank'], ['syringe_init', '', '', '', ''],
                 ['syringe_aspirate', 'CH1', '0.5', 'mL', ''], ['syringe_dispense', 'CH2', '0.5', 'mL', ''],
                 ['plasma_run', '', '', '', ''], ['dose_volume', 'P4', '30', 'mL', 'Leaving the plasma tank'], ['clean', '', '', '', ''],
                 ['delay', '', '2', 's', '']]
        code, reply = self.h.post('/api/flow', {'steps': steps})
        self.assertEqual((code, reply['valid'], reply['error'], reply['name']), (200, True, '', 'Process: Set by yourself (10 step)'))
        self.assertEqual([row['text'] for row in reply['view']],
                         ['Pump 1 · water quantitative 5 mL', 'Pump 2 Quantitative 25 mL', 'Pump 3 Quantitative 30 mL', 'syringe pump initialization', 'Syringe pump slave valve position 1 smoke 0.5 mL',
                          'Syringe pump moves to valve position 2 Note 0.5 mL', 'discharge＋capture spectrum', 'Pump 4 Quantitative 30 mL',
                          'automatic cleaning (Pump 1 Water ingress,Pump 7 discharge, approx. 0.9 seconds)', 'Waiting 2 Second'])
        self.assertEqual((reply['uses_plasma'], reply['uses_syringe']), (True, True))
        self.assertEqual(self.h.get('/api/flow')['steps'], steps)                 # the notes are kept as written
        row = self.flow_recipe()
        self.assertEqual((row['valid'], row['step_count'], row['uses_plasma']), (True, 10, True))

    def test_a_list_that_is_not_finished_is_kept_and_says_what_is_missing(self):
        """Written one step at a time: the syringe is filled in one step and emptied in a later one. In between the list
        is kept as it stands; the Recipe says what is wrong in the words of the page, and nothing starts it."""
        self.h.connected()
        self.h.settled()
        code, reply = self.h.post('/api/flow', {'steps': [['plasma_run', '', '', '', ''], ['syringe_aspirate', 'CH1', '1', 'mL', '']]})
        self.assertEqual(code, 200, reply)
        self.assertEqual((reply['valid'], reply['error'], reply['error_step']),
                         (False, 'Steps 2: Before using the syringe pump, there is a step ”Syringe Pump: Initialization”', 2))
        self.assertEqual([row['ok'] for row in reply['view']], [True, False])
        self.assertIn('Saved process (2 step, not available yet: step 2: Before using the syringe pump, there is a step ”Syringe Pump: Initialization”)', self.h.texts())
        row = self.flow_recipe()
        self.assertEqual((row['valid'], row['error']), (False, 'Steps 2: Before using the syringe pump, there is a step ”Syringe Pump: Initialization”'))
        # The schedule page asks what its form means, and is told the same; a start is refused.
        code, preview = self.h.post('/api/schedule/preview', self.h.body(1, 2, recipe_id=FLOW_ID))
        self.assertEqual((code, preview['error']), (400, 'Steps 2: Before using the syringe pump, there is a step ”Syringe Pump: Initialization”'))
        self.assertEqual(self.h.start(recipe_id=FLOW_ID)[0], 400)
        # One more step and the syringe still holds its liquid at the end ...
        steps = [['plasma_run', '', '', '', ''], ['syringe_init', '', '', '', ''], ['syringe_aspirate', 'CH1', '1', 'mL', '']]
        code, reply = self.h.post('/api/flow', {'steps': steps})
        self.assertEqual((code, reply['valid'], reply['error'], reply['error_step']),
                         (200, False, 'There is still something left in the syringe at the end of the process. 1 mL,Please launch first', None))
        # ... and with the last one it is a round.
        code, reply = self.h.post('/api/flow', {'steps': steps + [['syringe_dispense', 'CH2', '1', 'mL', '']]})
        self.assertEqual((code, reply['valid'], reply['error']), (200, True, ''))
        self.assertEqual(self.h.post('/api/schedule/preview', self.h.body(1, 2, recipe_id=FLOW_ID))[0], 200)
        # An empty list is kept too.
        code, reply = self.h.post('/api/flow', {'steps': []})
        self.assertEqual((code, reply['valid'], reply['error'], reply['steps'], reply['name']),
                         (200, False, 'No steps yet: Click ”＋ New step”', [], 'Process: Set by yourself (0 step)'))
        # The OUT pump never turns together with another one (the person's rule of M16).
        code, reply = self.h.post('/api/flow', {'steps': [['dose_together', 'P1+P7', '1+1', 'mL', '']]})
        self.assertEqual((code, reply['valid'], reply['error']),
                         (200, False, 'Steps 1: Pump 7 · OUT is a pump for emptying and cannot be used for dosing with other pumps.'))

    def test_what_is_not_a_step_list_is_said_and_nothing_is_kept(self):
        row = ['plasma_run', '', '', '', '']
        for steps, text in ((None, 'The content format of the process is incorrect.'), ({'fill': []}, 'The content format of the process is incorrect.'), ([row[:4]], 'The content format of the process is incorrect.'),
                            ([['plasma_run', '', '', '', 5]], 'The content format of the process is incorrect.'), ([row] * 61, 'has the most processes 60 step'),
                            ([row, ['pump_on', 'P1', '10', 'mL/min', '']], 'Steps 2: There is no such step on this page'),
                            ([['power_off', '', '', '', '']], 'Steps 1: There is no such step on this page'),
                            ([['repeat_start', '', '3', '', '']], 'Steps 1: There is no such step on this page')):
            self.assertEqual(self.h.post('/api/flow', {'steps': steps}), (400, {'error': text, 'code': 'bad_request'}), steps)
        self.assertEqual(self.h.post('/api/flow', {'flow': STAGES})[0], 400)          # the body of before M23
        self.assertIsNone(self.flow_recipe())
        self.assertEqual(self.h.get('/api/flow')['steps'], [row])
        self.assertFalse((self.h.app.store.dir / 'flow.json').exists())

    def test_the_four_stages_of_an_older_file_are_read_as_steps(self):
        """A flow.json written before M23 (the person's own, 2026-10-07 evening: Pump 1 5 mL, then the discharge)."""
        self.h.app.store.save_flow(STAGES)
        self.assertEqual(json.loads((self.h.app.store.dir / 'flow.json').read_text(encoding='utf-8'))['version'], 1)
        reply = self.h.get('/api/flow')
        self.assertEqual((reply['steps'], [row['text'] for row in reply['view']], reply['valid']),
                         ([row[:4] + [''] for row in STEPS], STEP_TEXTS, True))         # the same steps, without the stage names
        self.h.app.store.save_flow({'fill': [{'pump': 1, 'mode': 'ml', 'amount': 5}], 'measure': True,
                                    'drain': {'on': False, 'ml': 20}, 'clean': False})
        reply = self.h.get('/api/flow')
        self.assertEqual(reply['steps'], [['dose_volume', 'P1', '5', 'mL', ''], ['plasma_run', '', '', '', '']])
        # Saved from the page, it is a file of today.
        code, reply = self.h.post('/api/flow', {'steps': reply['steps']})
        self.assertEqual(code, 200, reply)
        self.assertEqual(json.loads((self.h.app.store.dir / 'flow.json').read_text(encoding='utf-8'))['version'], 2)
        # A file nobody can read: only the measurement.
        self.h.app.store.save_flow({'fill': 'nonsense'})
        self.assertEqual(self.h.get('/api/flow')['steps'], [['plasma_run', '', '', '', '']])
        self.h.app.store.save_flow({'steps': [['pump_on', 'P1', '10', 'mL/min', '']]})
        self.assertEqual(self.h.get('/api/flow')['steps'], [['plasma_run', '', '', '', '']])


class PumpCalibrationApiTests(test_app.Base):
    options = {'pump': 'sim'}

    def test_the_table_and_a_calibration_from_the_maintenance_page(self):
        reply = self.h.get('/api/pump/calibration')
        self.assertEqual((reply['nominal_ml'], len(reply['pumps'])), (10.0, 8))
        self.assertEqual(reply['pumps'][6], {'motor': 8, 'pump': 7, 'label': 'Pump 7 · OUT', 'dose_pulses_per_ml': FACTOR,
                                             'factory_pulses_per_ml': FACTOR, 'calibrated': False, 'nominal_ml': None,
                                             'measured_ml': None, 'saved_at': None})
        body = {'motor': 8, 'measured_ml': 10.4}
        self.assertEqual(self.h.post('/api/pump/calibrate', body, unlock='')[0], 403)            # maintenance only
        self.h.unlock()
        for bad in ({'motor': '8', 'measured_ml': 10.4}, {'motor': 8}, {'motor': 8, 'measured_ml': '10.4'},
                    {'motor': 8, 'measured_ml': 10.4, 'nominal_ml': 'x'}, {'motor': True, 'measured_ml': 10.4}):
            self.assertEqual(self.h.post('/api/pump/calibrate', bad), (400, {'error': 'The content of pump calibration is incorrect.', 'code': 'bad_request'}), bad)
        code, out = self.h.post('/api/pump/calibrate', body)
        new = round(FACTOR * 10 / 10.4, 4)
        self.assertEqual((code, out['dose_pulses_per_ml'], out['change_percent']), (200, new, -3.85))
        row = self.h.get('/api/pump/calibration')['pumps'][6]
        self.assertEqual((row['dose_pulses_per_ml'], row['calibrated'], row['measured_ml']), (new, True, 10.4))
        self.assertIn('Corrected Pump 7 · OUT: beat 10 mL,Actual 10.4 mL; Every mL number of steps 4559 → 4383.6 (-3.8%) ', self.h.texts())
        # The page's own picture of the board follows at once (bootstrap is read again by the pages that need it).
        motor = self.h.get('/api/bootstrap')['hardware']['groups'][1]['motors'][2]
        self.assertEqual((motor['id'], motor['dose_pulses_per_ml'], motor['factory_pulses_per_ml']), (8, new, FACTOR))
        self.assertEqual(self.h.post('/api/pump/calibrate', {'motor': 8, 'measured_ml': 30})[0], 409)
        code, out = self.h.post('/api/pump/calibrate/reset', {'motor': 8})
        self.assertEqual((code, out), (200, {'motor': 8, 'dose_pulses_per_ml': FACTOR}))
        self.assertEqual(self.h.post('/api/pump/calibrate/reset', {'motor': 'x'}),
                         (400, {'error': 'The content of pump calibration is incorrect.', 'code': 'bad_request'}))


class PumpSetupApiTests(test_app.Base):
    """/api/pump/setup (AMENDMENTS M20): Maintenance › Pump and Scale › “What to connect to the pump?” and “solenoid valve linkage”."""
    options = {'pump': 'sim'}
    DEFAULT = [(1, 'water', 'water'), (2, 'none', ''), (3, 'none', ''), (4, 'none', ''), (5, 'none', ''), (6, 'none', ''),
               (7, 'out', 'OUT'), (8, 'chemical', 'CuSO₄')]

    def labels(self):
        return [motor['label'] for group in self.h.get('/api/bootstrap')['hardware']['groups'] for motor in group['motors']]

    def sent(self):
        """Every command written to the (simulated) board so far."""
        self.h.app.pump._flush_serial_log()
        lines = (self.h.folder / 'logs' / 'pumpboard.log').read_text(encoding='utf-8').splitlines()
        return [parts[2] for parts in (line.split(' ', 2) for line in lines) if len(parts) == 3 and parts[1] == 'tx']

    def test_the_setup_from_the_maintenance_page(self):
        self.h.connected()
        before = self.h.get('/api/pump/setup')
        self.assertEqual(sorted(before), ['editable', 'name_max', 'names', 'pumps', 'reason', 'source', 'valve_close_after_ms',
                                          'valve_open_before_ms', 'valves'])
        self.assertEqual((before['editable'], before['source'], before['reason']), (True, 'default', ''))
        self.assertEqual([(row['pump'], row['use'], row['name']) for row in before['pumps']], self.DEFAULT)
        self.assertEqual(before['valves'], [{'id': 1, 'label': 'solenoid valve 1', 'motor': None}, {'id': 2, 'label': 'solenoid valve 2', 'motor': None}])
        rev = self.h.get('/api/bootstrap')['hardware']['rev']
        self.assertEqual(self.h.state()['pump']['hardware_rev'], rev)
        wanted = pt.setup_of(water=3, out=6, names={2: 'HNO₃', 9: 'CuSO₄'}, valves={1: 3})
        self.assertEqual(self.h.post('/api/pump/setup', wanted, unlock='')[0], 403)          # maintenance only
        self.h.unlock()
        for body, sentence in (({'pumps': [], 'valves': []}, 'The setting content is incomplete: every pump and every solenoid valve must have'),
                               (pt.setup_of(water=2, out=5), '“water” and ”OUT”Want one in Pump 1–4, One in Pump 5–8: '
                                                             'These two should be rotated together during cleaning. Only one on the same side can be rotated at a time.'),
                               (pt.setup_of(names={4: 'a;b'}), 'Pump 3 The name of 1–16 characters, no commas, semicolons, quotation marks, backslashes,< or >')):
            self.assertEqual(self.h.post('/api/pump/setup', body), (400, {'error': sentence, 'code': 'bad_request'}), body)
        self.assertEqual(self.h.get('/api/pump/setup')['source'], 'default')
        code, reply = self.h.post('/api/pump/setup', wanted)
        self.assertEqual(code, 200, reply)
        summary = 'water=Pump 2, OUT=Pump 5; Pump 1=HNO₃, Pump 8=CuSO₄; solenoid valve 1 follow Pump 2'
        self.assertEqual((reply['summary'], reply['source'], reply['flow'], reply['recipes_to_check']), (summary, 'saved', '', []))
        saved = next(row for row in self.h.app.store.events(limit=50) if row['text'] == 'Pump settings saved:' + summary)
        self.assertEqual(saved['actor'], 'Maintenance')
        # Everything that names a pump follows: bootstrap (with a new number), the state, the form of the schedule page.
        hardware = self.h.get('/api/bootstrap')['hardware']
        self.assertEqual((hardware['rev'], self.h.state()['pump']['hardware_rev']), (rev + 1, rev + 1))
        self.assertEqual(self.labels(), ['Pump 1 · HNO₃', 'Pump 2 · water', 'Pump 3', 'Pump 4', 'Pump 5 · OUT', 'Pump 6', 'Pump 7',
                                         'Pump 8 · CuSO₄'])
        self.assertEqual((hardware['cleaning']['water_motor'], hardware['cleaning']['out_motor']), (3, 6))
        self.assertEqual(hardware['valves'][0], {'id': 1, 'label': 'solenoid valve 1 · water', 'motor': 3})
        self.assertEqual([row['label'] for row in self.h.state()['pump']['motors']], self.labels())
        self.assertEqual([row['pump'] for row in self.h.get('/api/flow')['pumps'] if row['drain']], [5])
        self.assertEqual(self.h.get('/api/pump/setup')['pumps'][1], {
            'motor': 3, 'pump': 2, 'short': 'Pump 2', 'label': 'Pump 2 · water', 'group': 'A', 'half': 'Pump 1–4', 'use': 'water',
            'name': 'water', 'valve': 1})
        # The board was told, and the settings file keeps it for the next start.
        self.assertEqual([command for command in self.sent() if command.startswith(('CM,', 'VL,'))],
                         ['CM,2,8', 'VL,0,0,0,0,0,0,0,0', 'CM,3,6', 'VL,0,1,0,0,0,0,0,0'])
        self.assertEqual(self.h.app.store.pump_settings()['pump_setup']['valves'], [{'id': 1, 'motor': 3}, {'id': 2, 'motor': None}])

    def test_the_flow_of_the_schedule_page_is_checked_again_and_the_recipes_to_look_at_are_named(self):
        """The steps of the schedule page name their pumps by number, as every Recipe does: after “What to connect to the pump?” they
        turn the same pumps, are checked with the new layout, and are named among the Recipes to look at."""
        self.h.connected()
        self.h.settled()
        code, reply = self.h.post('/api/flow', {'steps': STEPS}, unlock='')
        self.assertEqual((code, reply['view'][-1]['text']), (200, 'Pump 7 · OUT Quantitative 3 mL'))
        self.h.unlock()
        header = 'action,target,value,unit,note\n'
        for name, rows in (('Used Pump 6', 'dose_volume,P6,1,mL,\n'), ('Only use Pump 2', 'dose_volume,P2,1,mL,\n')):
            code, out = self.h.post('/api/recipes/publish', {'name': name, 'csv': header + rows})
            self.assertEqual(code, 200, out)
        # OUT moves from Pump 7 to Pump 6: Pump 7 is an ordinary pump now, and the flow still doses 3 mL with it.
        code, reply = self.h.post('/api/pump/setup', pt.setup_of(water=2, out=7))
        self.assertEqual(code, 200, reply)
        self.assertEqual((reply['flow'], reply['recipes_to_check']), ('kept', ['Used Pump 6', 'Scheduling page process']))
        detail = self.h.get('/api/recipes/' + FLOW_ID)
        self.assertEqual([step['text'] for step in detail['steps']],
                         ['Simultaneous quantification:Pump 1 · Water:2 mL, Pump 8 · CuSO₄: 1 mL', 'discharge＋capture spectrum', 'Pump 7 Quantitative 3 mL'])
        self.assertIn('The process on the schedule page has been rechecked according to the new settings: Simultaneous quantification:Pump 1 · Water:2 mL, Pump 8 · CuSO₄: 1 mL; discharge＋Capture spectrum; Pump 7 Quantitative 3 mL',
                      self.h.texts())
        self.assertEqual(self.h.get('/api/flow')['steps'], STEPS)
        # OUT moves to Pump 8, which the first step turns together with Pump 1: the steps do not make a round any
        # more. They are kept; the reply and the page say so, and nothing starts them.
        code, reply = self.h.post('/api/pump/setup', pt.setup_of(water=2, out=9, names={}))
        self.assertEqual(code, 200, reply)
        self.assertEqual((reply['flow'], reply['recipes_to_check']), ('open', ['Used Pump 6', 'Scheduling page process']))
        flow = self.h.get('/api/flow')
        self.assertEqual((flow['steps'], flow['valid'], flow['error']),
                         (STEPS, False, 'Steps 1: Pump 8 · OUT is a pump for emptying and cannot be used for dosing with other pumps.'))
        self.assertIn('The process on the schedule page cannot be used according to the new settings (step 1: Pump 8 · OUT is a pump for emptying and cannot be used for metering with other pumps): Please go to ”Schedule Measurement” to change the steps.', self.h.texts())
        self.assertEqual(self.h.start(recipe_id=FLOW_ID)[0], 400)
        # Back to a layout it runs with: the drain step turns Pump 7 (pin 8), as it is written.
        code, reply = self.h.post('/api/pump/setup', pt.setup_of(water=2, out=8))
        self.assertEqual((code, reply['flow']), (200, 'kept'))
        code, started = self.h.start(recipe_id=FLOW_ID)
        self.assertEqual(code, 200, started)
        done = self.h.finished(90)
        self.assertEqual(done['status'], 'completed', done)
        doses = [command for command in self.sent() if command.startswith('P,')]
        self.assertEqual(doses[-1], 'P,8,%d,%d' % (steps_of(3), SPS))


if __name__ == '__main__':
    unittest.main(verbosity=2)
