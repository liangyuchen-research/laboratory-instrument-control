"""pc/megasim.py: Extension of Pump Control Board Simulator (mega/ The firmware of GP, GN, G, K, CM, VL and STATE field) automatic test.

speaks directly to the simulator, without the computer-side packaging layer in between: what is measured is what the ”board itself” will do. The most important thing: weighing and quantification
is started, the computer will not send a word, and the board will stop the pump by itself when the amount is reached, timeout occurs, the weight no longer increases, and the scale does not read.

What is tested here is the model written according to the firmware, not the program burned into the board; the real board depends on the steps explained 5 Checklist.
will not open or list any real ports.

    python tests/test_megasim.py
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import queue
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'pc')]

import pump_testing as pt  # noqa: E402  (first: it sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import megasim  # noqa: E402
import pumpboard  # noqa: E402

# GP,<fast>,<approach>,<trim sps>,<least gain mg>,<settle ms>,<no gain ms>,<time limit ms>
# GN,<baseline n>,<final n>,<one reading ms>          (5, 7, 4000 when it is not sent)
# G,<motor>,<target mg>,<slow zone mg>,<trim zone mg>,<tolerance mg>
PROFILE = 'GP,2825,706,212,50,200,30000,300000'
SAMPLES = 'GN,5,7,4000'
DOSE = 'G,8,5000,1250,250,25'
LONG = 'G,8,500000,1250,250,25'          # half a kilogram: never reached in a test
STOCK_STATE = 'STATE,a=-1,sa=idle,b=-1,sb=idle,pump=unknown,proto=ascii,baud=9600,addr=0,syringe_uL=5000,valves=00,'


def setUpModule():
    pt.install_tripwire()


def tearDownModule():
    pt.assert_nothing_opened()


class Board:
    """One simulator of the firmware of mega/, spoken to line by line."""

    def __init__(self, speedup=4.0, calibrated=True, **dotted):
        v7 = pumpboard.load_v7()
        cfg = copy.deepcopy(v7.config.get_config(reload=True))
        values = dict(pt.FAST)
        values['link.simulation_speedup'] = speedup
        values.update({name.replace('__', '.'): value for name, value in dotted.items()})
        pt.overrides_from(values)(cfg)
        self.cfg = cfg
        self.sim = megasim.simulator_class(v7.transport.SimulatedMega)(cfg)
        self.seen = []
        self.sim.start()
        if calibrated:
            self.ask('W,DIV,250')

    def close(self):
        self.sim.stop()

    def read(self, timeout=0.05):
        """Take every line the board has sent so far."""
        limit = time.perf_counter() + timeout
        while True:
            try:
                line = self.sim._out.get(timeout=max(0.0, limit - time.perf_counter()))
            except queue.Empty:
                return
            if line is not None:
                self.seen.append(line)

    def ask(self, command, timeout=2.0):
        """Send one line; the first line of its answer (weights and STATE lines that were on their way are skipped)."""
        self.read(0.0)
        mark = len(self.seen)
        self.sim.write((command + '\n').encode('utf-8'))
        limit = time.perf_counter() + timeout
        up = command.upper()
        wanted = (('STATE,',) if up == '?' else ('GSTAT,', 'ERR') if up == 'G,?' else ('CFG,',) if up == 'W,CFG'
                  else ('WEIGHT,', 'ERR') if up == 'W,READ' else ('PUMP,', 'FOUND,', 'OK,', 'ERR') if up.startswith('Y,')
                  else ('OK', 'ERR', 'DONE'))
        while time.perf_counter() < limit:
            self.read(0.02)
            for line in self.seen[mark:]:
                # (what the weight stream says on its own is no answer: V7's transport leaves it out as well)
                if line.startswith(('ERR,timeout', 'ERR,stream_stopped')) and up != 'W,READ':
                    continue
                if line.startswith(wanted):
                    return line
        raise AssertionError('no answer to %s; got %r' % (command, self.seen[mark:]))

    def wait(self, prefix, timeout=10.0):
        limit = time.perf_counter() + timeout
        while time.perf_counter() < limit:
            self.read(0.05)
            for line in self.seen:
                if line.startswith(prefix):
                    return line
        raise AssertionError('no line %s within %.0f s; the last lines: %r' % (prefix, timeout, [
            line for line in self.seen if not line.startswith('WEIGHT,')][-8:]))

    def dose(self, command=DOSE, profile=PROFILE, samples=None):
        """The numbers, then the order itself; what the board answers to the order."""
        if profile:
            answer = self.ask(profile)
            assert answer == 'OK,dose_profile', answer
        if samples:
            answer = self.ask(samples)
            assert answer == 'OK,dose_samples', answer
        return self.ask(command)

    def status(self):
        return dict(pumpboard.KV.findall(self.ask('G,?')))

    def idle(self):
        sim = self.sim
        return all(state == 'idle' for state in sim.mstate.values()) and not any(sim._pending_start.values())

    def notes(self):
        self.read(0.0)
        return [line for line in self.seen if not line.startswith('WEIGHT,')]


def tool(name):
    """One of the programs of mega/tools, loaded as a module (loading runs nothing)."""
    spec = importlib.util.spec_from_file_location('mega_' + name, ROOT / 'mega' / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Case(unittest.TestCase):
    def board(self, **options):
        board = Board(**options)
        self.addCleanup(board.close)
        return board


class IdentityTests(Case):
    def test_the_banner_and_the_state_line_name_the_firmware(self):
        board = self.board(calibrated=False)
        self.assertEqual(board.wait('READY'), 'READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0,fw=p2')
        state = board.ask('?')
        # Everything V7's firmware says is still there, in the same order; the five fields come after it. After a
        # restart the two pumps of the cleaning and the valve of each motor are V7's (hw_config.h).
        self.assertTrue(state.startswith(STOCK_STATE), state)
        self.assertTrue(state.endswith(',w_stream_hz=10,cleaning=0,fw=p2,wd=0,dose=0,clean=2:8,links=10000002'), state)
        fields = dict(pumpboard.KV.findall(state))
        self.assertEqual((fields['fw'], fields['wd'], fields['dose']), ('p2', '0', '0'))
        caps = pumpboard.PumpBoard._caps
        self.assertEqual(caps(fields), {'tag': 'p2', 'dose': True, 'watchdog': True, 'setup': True})
        self.assertEqual(caps({'fw': 'p1'}), {'tag': 'p1', 'dose': True, 'watchdog': True, 'setup': False})   # before CM and VL
        self.assertEqual(caps({'fw': 'p10'})['setup'], True)
        self.assertEqual(caps({'a': '-1'}), {'tag': '', 'dose': False, 'watchdog': False, 'setup': False})
        self.assertEqual(caps({'fw': 'v9'}), {'tag': '', 'dose': False, 'watchdog': False, 'setup': False})

    def test_what_v7_understands_is_answered_as_before(self):
        board = self.board(calibrated=False)
        self.assertEqual(board.ask('S,8'), 'DONE,S')
        self.assertEqual(board.ask('V,8,2825'), 'OK')
        self.assertEqual(board.ask('V,8,0'), 'OK')
        self.assertEqual(board.ask('P,8,100,2825'), 'OK')
        self.assertEqual(board.ask('X'), 'OK,all_stopped')
        self.assertEqual(board.ask('nonsense'), 'ERR,bad_cmd')
        self.assertTrue(board.ask('W,CFG').startswith('CFG,'))
        self.assertEqual(board.status()['active'], '0')
        self.assertEqual(board.status()['result'], 'none')


class DoseRulesTests(Case):
    def test_what_is_not_a_complete_and_sensible_order_is_refused(self):
        board = self.board()
        self.assertEqual(board.ask(DOSE), 'ERR,dose_profile')            # the speeds and times were never sent
        for bad in ('GP,', 'GP,x', 'GP,2825,706,212,50,200,30000', PROFILE + ',1', PROFILE.replace('2825', '2825.5'),
                    PROFILE.replace('2825', '8001'), PROFILE.replace('706', '3000'), PROFILE.replace(',212,', ',0,'),
                    PROFILE.replace(',212,', ',707,'), PROFILE.replace(',50,', ',0,'), PROFILE.replace(',50,', ',100001,'),
                    PROFILE.replace(',200,', ',60001,'), PROFILE.replace(',30000,', ',1999,'),
                    PROFILE.replace(',30000,', ',600001,'), PROFILE.replace(',300000', ',1999'),
                    PROFILE.replace(',300000', ',1800001'), PROFILE.replace(',300000', ',4294967596'),
                    'GN,', 'GN,5,7', SAMPLES + ',1', 'GN,0,7,4000', 'GN,5,10,4000', 'GN,5,7,499', 'GN,5,7,30001'):
            self.assertEqual(board.ask(bad), 'ERR,arg', bad)
        self.assertEqual(board.ask(DOSE), 'ERR,dose_profile')            # none of those counted
        self.assertEqual(board.ask(PROFILE), 'OK,dose_profile')
        for bad in ('G,', 'G,x', 'G,8', 'G,8,5000', 'G,8,5000,1250,250', DOSE + ',1', 'G,8,5.0,1250,250,25',
                    DOSE.replace('G,8,', 'G,1,'), DOSE.replace('G,8,', 'G,10,'), DOSE.replace(',5000,', ',9,'),
                    DOSE.replace(',5000,', ',1000001,'), 'G,8,5000,100,250,25', 'G,8,5000,1250,-1,25',
                    'G,8,5000,1250,250,5000', 'G,8,5000,1250,250,-1', 'G,8,4294972296,1250,250,25'):
            self.assertEqual(board.ask(bad), 'ERR,arg', bad)
        self.assertTrue(board.idle())
        self.assertEqual(board.status()['active'], '0')
        # Without GN the three counts are 5, 7 and 4 s.
        self.assertEqual(board.ask(DOSE), 'OK,dose_started')
        self.assertEqual(board.ask('X'), 'OK,all_stopped')
        self.assertEqual(board.ask(SAMPLES), 'OK,dose_samples')
        self.assertEqual(board.ask(DOSE), 'OK,dose_started')
        self.assertEqual(board.ask('X'), 'OK,all_stopped')

    def test_no_line_is_longer_than_the_board_can_take(self):
        # The Mega's serial buffer holds 63 bytes and the board does not read while it talks to the scale:
        # the largest numbers that are allowed still fit, line feed included.
        longest = ('GP,8000,8000,8000,100000,60000,600000,1800000', 'GN,9,9,30000', 'G,9,1000000,1000000,1000000,999999',
                   'K,60000')
        for line in longest:
            self.assertLessEqual(len(line) + 1, 63, line)
            self.assertLessEqual(len(line), pumpboard.LINE_MAX, line)
        board = self.board()
        self.assertEqual(board.ask(longest[0]), 'OK,dose_profile')
        self.assertEqual(board.ask(longest[1]), 'OK,dose_samples')
        self.assertEqual(board.ask(longest[2]), 'OK,dose_started')
        self.assertEqual(board.ask('X'), 'OK,all_stopped')

    def test_what_stands_in_the_way_is_said_before_anything_moves(self):
        board = self.board(calibrated=False)
        self.assertEqual(board.dose(), 'ERR,scale_uncalibrated')         # grams mean nothing yet
        self.assertEqual(board.ask('W,DIV,250'), 'OK,div=250.0000')
        board.ask('V,3,500')
        self.assertEqual(board.ask(DOSE), 'ERR,busy')                    # a pump of the other group turns
        board.ask('X')
        board.ask('W,STREAM,10')
        board.sim.scale_silent = True
        self.assertEqual(board.ask(DOSE), 'ERR,scale_timeout')
        board.sim.scale_silent = False
        board.ask('C')
        self.assertEqual(board.ask(DOSE), 'ERR,cleaning_active')
        board.ask('X')
        self.assertTrue(board.idle())

    def test_only_questions_and_stops_get_through_while_it_runs(self):
        board = self.board(speedup=1.0)
        self.assertEqual(board.dose(LONG), 'OK,dose_started')
        board.wait('DOSE,phase=priming')
        for refused in ('P,8,100,500', 'P,3,100,500', 'S,3', 'V,3,500', 'V,8,500', 'C', DOSE, PROFILE, SAMPLES, 'Y,INIT',
                        'Y,ST', 'W,TARE', 'W,STREAM,0', 'W,DIV,1', 'W,OFFSET,5', 'W,AVG,4'):
            self.assertEqual(board.ask(refused), 'ERR,dose_active', refused)
        for refused in ('CM,3,6', 'VL,0,0,0,0,0,0,0,0'):          # the setup is not changed under a running dose
            self.assertEqual(board.ask(refused), 'ERR,dose_active', refused)
        self.assertIn(',dose=1,', board.ask('?'))
        self.assertEqual(board.status()['active'], '1')
        self.assertTrue(board.ask('W,CFG').startswith('CFG,'))
        self.assertTrue(board.ask('W,READ').startswith('WEIGHT,'))
        self.assertEqual(board.ask('K,0'), 'OK,watchdog_ms=0')
        # Every kind of stop gets through. The syringe's is sent on without waiting for its answer, and
        # switching off a pump that is not the dosing one changes nothing.
        self.assertEqual(board.ask('Y,STOP'), 'OK,pump_stop_sent')
        self.assertEqual(board.ask('V,3,0'), 'OK')
        self.assertEqual(board.sim.mstate['B'], 'run')                   # none of that disturbed it
        self.assertEqual(board.status()['active'], '1')
        self.assertEqual(board.ask('X'), 'OK,all_stopped')
        self.assertEqual((board.status()['active'], board.status()['result']), ('0', 'cancelled'))


class DoseTests(Case):
    def test_the_board_doses_by_itself_and_nobody_has_to_say_another_word(self):
        board = self.board()
        self.assertEqual(board.dose(samples=SAMPLES), 'OK,dose_started')
        end = board.wait('DOSE,end', 20)            # ... and from here on this test sends nothing at all
        fields = dict(pumpboard.KV.findall(end))
        self.assertEqual(fields['result'], 'done')
        self.assertGreaterEqual(float(fields['gain']), 5.0 - 0.025 - 0.0005)
        self.assertLess(float(fields['gain']), 5.3)
        phases = [line.split('phase=')[1].split(',')[0] for line in board.notes() if line.startswith('DOSE,phase=')]
        self.assertEqual(phases, ['priming', 'coarse', 'approach', 'trim', 'settle'])
        self.assertTrue(board.idle())
        board.read(0.3)
        self.assertFalse(any(board.sim.valves.values()))
        status = board.status()
        self.assertEqual((status['active'], status['phase'], status['result'], status['motor'], status['target']),
                         ('0', 'idle', 'done', '8', '5.000'))
        self.assertEqual(status['gain'], fields['gain'])
        self.assertIn(',dose=0,', board.ask('?'))
        # No line of it can be taken for the answer to another command, or for its failure (V7's rule).
        for line in board.notes():
            if line.startswith(('DOSE,', 'WATCHDOG,', 'GSTAT,')):
                self.assertFalse(line.startswith(('OK', 'ERR', 'DONE')), line)
        # Every change of phase is followed by the state of the motors.
        notes = board.notes()
        for index, line in enumerate(notes):
            if line.startswith('DOSE,'):
                self.assertTrue(notes[index + 1].startswith('STATE,'), notes[index:index + 2])

    def test_the_numbers_stay_for_the_next_dose(self):
        board = self.board()
        self.assertEqual(board.dose(), 'OK,dose_started')
        self.assertEqual(dict(pumpboard.KV.findall(board.wait('DOSE,end', 20)))['result'], 'done')
        del board.seen[:]
        self.assertEqual(board.ask('G,8,2000,500,100,10'), 'OK,dose_started')     # no GP this time
        end = dict(pumpboard.KV.findall(board.wait('DOSE,end', 20)))
        self.assertEqual((end['result'], end['target']), ('done', '2.000'))
        self.assertGreaterEqual(float(end['gain']), 2.0 - 0.010 - 0.0005)

    def test_a_pump_with_a_valve_opens_it_first_and_closes_it_afterwards(self):
        board = self.board()
        self.assertEqual(board.dose(DOSE.replace('G,8,', 'G,9,')), 'OK,dose_started')
        board.wait('DOSE,phase=priming')
        self.assertTrue(board.sim.valves[2])
        self.assertEqual(dict(pumpboard.KV.findall(board.wait('DOSE,end', 20)))['result'], 'done')
        deadline = time.perf_counter() + 2.0
        while board.sim.valves[2] and time.perf_counter() < deadline:
            time.sleep(0.02)
        self.assertFalse(board.sim.valves[2])
        self.assertTrue(board.idle())

    def test_stop_and_off_end_it_at_once(self):
        for stop in ('X', 'V,8,0'):
            with self.subTest(stop=stop):
                board = self.board(speedup=1.0)
                board.dose(LONG)
                board.wait('DOSE,phase=priming')
                self.assertEqual(board.sim.mstate['B'], 'run')
                board.ask(stop)
                self.assertTrue(board.idle())
                end = dict(pumpboard.KV.findall(board.wait('DOSE,end')))
                self.assertEqual(end['result'], 'cancelled')
                self.assertEqual(board.status()['active'], '0')
                board.close()

    def test_it_ends_when_the_weight_no_longer_rises(self):
        board = self.board(speedup=0.0)             # the pump turns, nothing arrives on the scale
        board.dose(profile=PROFILE.replace(',30000,', ',2000,'))
        board.wait('DOSE,phase=priming')
        end = dict(pumpboard.KV.findall(board.wait('DOSE,end', 6)))
        self.assertEqual(end['result'], 'no_gain')
        self.assertTrue(board.idle())
        self.assertLess(int(end['ms']), 4000)

    def test_it_ends_at_its_time_limit(self):
        board = self.board(speedup=0.05)            # liquid arrives, far too slowly
        board.dose('G,8,900000,1250,250,25', 'GP,2825,706,212,1,200,30000,2000')
        end = dict(pumpboard.KV.findall(board.wait('DOSE,end', 6)))
        self.assertEqual(end['result'], 'timeout')
        self.assertTrue(board.idle())

    def test_it_ends_when_the_scale_falls_silent(self):
        board = self.board(speedup=1.0)
        board.dose(LONG)
        board.wait('DOSE,phase=priming')
        self.assertEqual(board.sim.mstate['B'], 'run')
        board.sim.scale_silent = True
        started = time.perf_counter()
        end = dict(pumpboard.KV.findall(board.wait('DOSE,end', 5)))
        self.assertEqual(end['result'], 'scale')
        self.assertLess(time.perf_counter() - started, 1.5)      # three failed readings, not the four seconds
        self.assertTrue(board.idle())
        self.assertIn('ERR,stream_stopped_no_reply', board.notes())

    def test_it_ends_when_no_reading_comes_at_all(self):
        board = self.board(speedup=1.0)
        board.dose(LONG, samples='GN,5,7,600')
        board.wait('DOSE,phase=priming')
        board.sim.stream_hz = 0                     # (the stream cannot be switched off by a command meanwhile)
        end = dict(pumpboard.KV.findall(board.wait('DOSE,end', 5)))
        self.assertEqual(end['result'], 'scale')
        self.assertTrue(board.idle())

    def test_a_reading_that_falls_back_after_the_stop_is_topped_up(self):
        board = self.board()
        board.dose(profile=PROFILE.replace(',200,30000,', ',600,30000,'))
        board.wait('DOSE,phase=settle', 20)
        with board.sim._lock:
            board.sim._payload_g -= 0.2             # drops that hung on the outlet were counted, and are gone
        board.wait('DOSE,phase=topup', 5)
        end = dict(pumpboard.KV.findall(board.wait('DOSE,end', 20)))
        self.assertEqual((end['result'], end['topups']), ('done', '1'))
        self.assertGreaterEqual(float(end['gain']), 5.0 - 0.025 - 0.0005)
        self.assertTrue(board.idle())

    def test_a_top_up_is_not_ended_by_a_reading_from_before_its_pump_turns(self):
        board = self.board(valves__open_before_ms=900)
        board.dose(DOSE.replace('G,8,', 'G,9,'), PROFILE.replace(',200,30000,', ',600,30000,'))
        board.wait('DOSE,phase=settle', 20)
        with board.sim._lock:
            board.sim._payload_g -= 0.2
        board.wait('DOSE,phase=topup', 5)
        with board.sim._lock:
            opening = board.sim._pending_start['B'] is not None
            board.sim._payload_g += 0.2             # the reading is back while the valve is still opening
        self.assertTrue(opening)
        time.sleep(0.35)
        self.assertEqual(board.status()['phase'], 'topup')       # readings from before the pump turns do not count
        end = dict(pumpboard.KV.findall(board.wait('DOSE,end', 20)))
        self.assertEqual((end['result'], end['topups']), ('done', '1'))
        self.assertTrue(board.idle())


class WatchdogTests(Case):
    def test_an_endless_run_ends_when_the_pc_falls_silent(self):
        board = self.board(calibrated=False)
        for bad in ('K,', 'K,x', 'K,1999', 'K,60001', 'K,-5', 'K,1,2', 'K,4294969296'):
            self.assertEqual(board.ask(bad), 'ERR,arg', bad)
        self.assertEqual(board.ask('K,2000'), 'OK,watchdog_ms=2000')
        self.assertIn(',wd=2000,dose=0,', board.ask('?'))
        board.ask('V,8,2825')
        for _ in range(6):                          # a PC that keeps talking keeps it running
            time.sleep(0.5)
            self.assertIn('sb=run', board.ask('?'))
        started = time.perf_counter()
        self.assertEqual(board.wait('WATCHDOG,', 5), 'WATCHDOG,host_silent,ms=2000')
        self.assertTrue(1.5 < time.perf_counter() - started < 3.0)
        self.assertTrue(board.idle())
        self.assertIn('sb=idle', board.ask('?'))
        self.assertEqual(board.sim.sel['B'], 8)                     # stopped as `V,8,0` would: still selected

    def test_it_is_off_until_it_is_asked_for_and_leaves_what_ends_by_itself_alone(self):
        board = self.board(calibrated=False, speedup=1.0)
        board.ask('V,8,2825')
        time.sleep(2.5)
        self.assertEqual(board.sim.mstate['B'], 'run')              # V7's firmware: nothing watches
        board.ask('X')
        board.ask('K,2000')
        board.ask('P,8,%d,2825' % (2825 * 4))                        # a finite move of four seconds
        time.sleep(2.6)
        self.assertEqual(board.sim.mstate['B'], 'move')             # it has an end of its own
        self.assertFalse([line for line in board.notes() if line.startswith('WATCHDOG,')])
        board.ask('X')

    def test_it_stops_the_endless_run_only(self):
        board = self.board(calibrated=False, speedup=1.0)
        board.ask('K,2000')
        board.ask('P,3,%d,2825' % (2825 * 6))                        # group A: six seconds, ends by itself
        board.ask('V,9,2825')                                        # group B: endless, behind its valve
        board.wait('WATCHDOG,', 5)
        self.assertEqual(board.sim.mstate['B'], 'idle')
        self.assertEqual(board.sim.mstate['A'], 'move')             # the finite dose goes on
        deadline = time.perf_counter() + 2.0
        while board.sim.valves[2] and time.perf_counter() < deadline:
            time.sleep(0.02)
        self.assertFalse(board.sim.valves[2])                       # the valve closed after its delay
        board.ask('X')

    def test_a_weight_dose_is_not_its_business(self):
        board = self.board(speedup=1.0)
        board.ask('K,2000')
        board.dose(LONG)
        board.wait('DOSE,phase=priming')
        time.sleep(2.6)                                              # nobody says a word
        self.assertEqual(board.sim.mstate['B'], 'run')              # the dose has ends of its own
        self.assertFalse([line for line in board.notes() if line.startswith('WATCHDOG,')])
        board.ask('X')


class SetupTests(Case):
    """CM and VL (p2): which two pumps the cleaning uses and which valve opens with which motor. They store numbers."""

    def fields(self, board):
        return dict(pumpboard.KV.findall(board.ask('?')))

    def test_after_a_restart_the_board_is_v7s(self):
        board = self.board(calibrated=False)
        self.assertEqual((self.fields(board)['clean'], self.fields(board)['links']), ('2:8', '10000002'))
        self.assertEqual(board.ask('V,2,2825'), 'OK')            # motor 2 has valve 1: it opens first
        self.assertEqual((board.sim.valves, board.sim.mstate['A']), ({1: True, 2: False}, 'wait'))
        board.ask('X')

    def test_the_valve_of_every_motor_can_be_set_and_none_is_one_of_the_settings(self):
        board = self.board(calibrated=False)
        self.assertEqual(board.ask('VL,0,0,0,0,0,0,0,0'), 'OK,links=00000000')
        self.assertEqual(self.fields(board)['links'], '00000000')
        self.assertEqual(board.ask('V,2,2825'), 'OK')            # no valve: it turns at once
        self.assertEqual((board.sim.valves, board.sim.mstate['A']), ({1: False, 2: False}, 'run'))
        self.assertEqual(board.ask('V,2,0'), 'OK')
        self.assertEqual(board.ask('vl,0,2,0,0,0,0,1,0'), 'OK,links=02000010')       # valve 2 with motor 3, valve 1 with motor 8
        self.assertEqual(board.ask('P,3,200,2825'), 'OK')
        self.assertEqual(board.sim.valves, {1: False, 2: True})
        pt.wait_for(lambda: board.idle() and not any(board.sim.valves.values()), 5.0, 'the dose to end and its valve to close')
        self.assertEqual(board.ask('V,8,2825'), 'OK')
        self.assertEqual(board.sim.valves, {1: True, 2: False})
        self.assertEqual(board.ask('X'), 'OK,all_stopped')       # a stop closes the valves and leaves the settings alone
        self.assertEqual(self.fields(board)['links'], '02000010')

    def test_a_table_of_valves_that_cannot_be_is_refused(self):
        board = self.board(calibrated=False)
        for bad in ('VL,', 'VL,0,0,0,0,0,0,0', 'VL,0,0,0,0,0,0,0,0,0', 'VL,3,0,0,0,0,0,0,0', 'VL,-1,0,0,0,0,0,0,0',
                    'VL,1,1,0,0,0,0,0,0', 'VL,0,2,0,0,0,0,0,2', 'VL,a,0,0,0,0,0,0,0', 'VL,0,0,0,0,0,0,0,0.5',
                    'VL,0 0,0,0,0,0,0,0,0'):
            self.assertEqual(board.ask(bad), 'ERR,arg', bad)
        self.assertEqual(self.fields(board)['links'], '10000002')
        self.assertLessEqual(len('VL,2,2,2,2,2,2,2,2'), pumpboard.LINE_MAX)

    def test_the_valves_are_not_changed_while_something_turns_or_a_valve_is_open(self):
        board = self.board(calibrated=False, valves__close_after_ms=600)
        self.assertEqual(board.ask('V,2,2825'), 'OK')            # the valve opens, then the pump turns
        self.assertEqual(board.ask('VL,0,0,0,0,0,0,0,0'), 'ERR,busy')
        pt.wait_for(lambda: board.sim.mstate['A'] == 'run', 3.0, 'the pump to turn')
        self.assertEqual(board.ask('VL,0,0,0,0,0,0,0,0'), 'ERR,busy')
        self.assertEqual(board.ask('V,2,0'), 'OK')               # stopped: its valve closes a moment later
        self.assertTrue(board.sim.valves[1])
        self.assertEqual(board.ask('VL,0,0,0,0,0,0,0,0'), 'ERR,busy')
        pt.wait_for(lambda: not board.sim.valves[1], 3.0, 'the valve to close')
        self.assertEqual(board.ask('VL,0,0,0,0,0,0,0,0'), 'OK,links=00000000')
        self.assertEqual(board.ask('P,9,60000,2825'), 'OK')      # a finite move, no valve any more
        self.assertEqual(board.ask('VL,1,0,0,0,0,0,0,2'), 'ERR,busy')
        board.ask('X')
        self.assertEqual(board.ask('VL,1,0,0,0,0,0,0,2'), 'OK,links=10000002')

    def test_the_two_pumps_of_the_cleaning_can_be_set(self):
        board = self.board(calibrated=False)
        for bad in ('CM,', 'CM,2', 'CM,2,8,3', 'CM,1,8', 'CM,2,10', 'CM,2,3', 'CM,5,2', 'CM,6,9', 'CM,8,8', 'CM,x,8', 'CM,2,-8'):
            self.assertEqual(board.ask(bad), 'ERR,arg', bad)
        self.assertEqual(self.fields(board)['clean'], '2:8')
        self.assertEqual(board.ask('VL,0,0,0,0,0,0,0,0'), 'OK,links=00000000')
        self.assertEqual(board.ask('CM,6,3'), 'OK,clean=6:3')    # water on the second driver, OUT on the first
        self.assertEqual(self.fields(board)['clean'], '6:3')
        self.assertEqual(board.ask('C'), 'OK,cleaning_started')
        sim = board.sim
        self.assertEqual((sim.sel, sim.mstate), ({'A': 3, 'B': 6}, {'A': 'idle', 'B': 'run'}))        # water first
        for refused in ('CM,2,8', 'VL,0,0,0,0,0,0,0,0'):
            self.assertEqual(board.ask(refused), 'ERR,cleaning_active', refused)
        pt.wait_for(lambda: sim.mstate == {'A': 'run', 'B': 'run'}, 3.0, 'water and OUT together')
        pt.wait_for(lambda: sim.mstate == {'A': 'run', 'B': 'idle'}, 3.0, 'OUT alone')
        pt.wait_for(lambda: not sim.cleaning and board.idle(), 3.0, 'the cleaning to end')
        self.assertEqual(sim.sel, {'A': 3, 'B': 6})
        self.assertEqual(board.ask('X'), 'OK,all_stopped')
        self.assertEqual(self.fields(board)['clean'], '6:3')     # a stop is not a restart

    def test_a_restart_brings_v7s_layout_back(self):
        board = self.board(calibrated=False)
        board.ask('CM,3,6')
        board.ask('VL,0,0,0,0,0,0,0,0')
        pt.FaultPlan.reset_board(board.sim)
        self.assertEqual((self.fields(board)['clean'], self.fields(board)['links']), ('2:8', '10000002'))

    def test_the_settings_of_this_pc_are_not_the_boards(self):
        """The model keeps the two settings for itself: what the PC writes into its own cfg changes nothing on the board."""
        board = self.board(calibrated=False)
        shared = board.sim.cfg
        self.assertIsNot(shared['cleaning'], board.cfg['cleaning'])
        board.cfg['cleaning']['water_motor'] = 5
        board.cfg['valves']['motor_links'] = []
        self.assertEqual((self.fields(board)['clean'], self.fields(board)['links']), ('2:8', '10000002'))


class FirmwareFilesTests(unittest.TestCase):
    """What is in mega/: the source the generator makes, and the build that was written to the board."""
    # p2, written to the board of the rig on 2026-10-07 at about 17:35 (verified byte by byte; the board answered fw=p2).
    # (p1 was 4b8c1996…15a8930, written at 03:48 the same day; it is kept as mega/backup/plasmic-mega-p1.hex.)
    FLASHED = 'c9863b173cae45dad0ee193454433002c08f2cae26f99f71bde3f749ea47ad07'

    def test_the_source_in_the_folder_is_what_the_generator_makes_from_v7s(self):
        make = tool('make_firmware')
        text, stock = make.generate()
        folder = ROOT / 'mega' / 'source' / 'PlasmicMega'
        self.assertEqual((folder / 'PlasmicMega.ino').read_bytes(), text.encode('utf-8'))
        self.assertEqual((folder / 'hw_config.h').read_bytes(), (make.STOCK / 'hw_config.h').read_bytes())
        self.assertEqual(stock, (ROOT / 'pc' / 'vendor' / 'v7' / 'firmware' / 'Arduino' / 'Arduino.ino').read_bytes())
        # The model names itself as the firmware does.
        self.assertEqual(make.FW_TAG, megasim.FW_TAG)
        self.assertIn('#define FW_TAG "%s"' % megasim.FW_TAG, text)
        self.assertTrue(megasim.READY.endswith(',fw=' + megasim.FW_TAG))
        # Everything V7's firmware had is still there. Only lines were added, and twelve were changed: five were
        # extended (p1); the valve table lost its `const` and the six places that named the two pumps of the cleaning
        # name two variables (p2).
        kept = sum(1 for line in stock.decode('utf-8').splitlines() if line in set(text.splitlines()))
        self.assertGreaterEqual(kept, len(stock.splitlines()) - 12)
        for line in ('uint8_t MOTOR_VALVE[MOTOR_COUNT] = MOTOR_VALVE_LIST;', 'uint8_t cleanWaterMotor = CLEAN_WATER_MOTOR;',
                     'uint8_t cleanOutMotor = CLEAN_OUT_MOTOR;'):
            self.assertIn(line, text)
        body = text.split('#include "hw_config.h"', 1)[1]
        self.assertEqual((body.count('CLEAN_WATER_MOTOR'), body.count('CLEAN_OUT_MOTOR')), (1, 1))     # only where they start

    def test_the_model_starts_from_what_the_firmware_is_built_with(self):
        header = (ROOT / 'mega' / 'source' / 'PlasmicMega' / 'hw_config.h').read_text(encoding='utf-8')

        def define(name):
            return next(line.split(None, 2)[2].strip() for line in header.splitlines() if line.startswith('#define %s ' % name))

        self.assertEqual((int(define('FIRST_MOTOR')), int(define('LAST_MOTOR'))), (megasim.FIRST_MOTOR, megasim.LAST_MOTOR))
        self.assertEqual(int(define('GROUP_B_FIRST')), megasim.GROUP_B_FIRST)
        self.assertEqual(int(define('NUM_VALVES')), megasim.NUM_VALVES)
        self.assertEqual((int(define('CLEAN_WATER_MOTOR')), int(define('CLEAN_OUT_MOTOR'))),
                         (megasim.CLEAN_WATER_MOTOR, megasim.CLEAN_OUT_MOTOR))
        self.assertEqual(tuple(int(item) for item in define('MOTOR_VALVE_LIST').strip('{} ').split(',')), megasim.MOTOR_VALVE_LIST)
        # ... and the words of the two commands are the firmware's.
        text, _ = tool('make_firmware').generate()
        for words in ('Serial.print(F("OK,clean="));', 'Serial.print(F("OK,links="));', 'Serial.print(F(",clean="));',
                      'Serial.print(F(",links="));', 'command.startsWith("CM,") || command.startsWith("VL,")'):
            self.assertIn(words, text)

    def test_no_command_of_the_firmware_is_longer_than_its_buffer(self):
        text, _ = tool('make_firmware').generate()
        # serialBuffer takes 95 characters, the Mega's own receive buffer 63 with the line feed: the host stays below.
        self.assertIn('serialBuffer.length() < 95', text)
        self.assertLessEqual(pumpboard.LINE_MAX + 1, 63)

    def test_the_build_that_was_flashed_is_in_the_folder(self):
        for name in ('hex/plasmic-mega.hex', 'hex/v7-stock.hex', 'backup/board-flash-20261007.hex', 'backup/plasmic-mega-p1.hex',
                     'tools/build_firmware.py',
                     'tools/flash_mega.py', 'Flash-Mega.cmd', 'README.txt'):
            self.assertTrue((ROOT / 'mega' / name).is_file(), name)
        self.assertEqual(hashlib.sha256((ROOT / 'mega' / 'hex' / 'plasmic-mega.hex').read_bytes()).hexdigest(), self.FLASHED)
        launcher = (ROOT / 'mega' / 'Flash-Mega.cmd').read_bytes()
        launcher.decode('ascii')
        self.assertNotIn(b'\n', launcher.replace(b'\r\n', b''))            # CRLF throughout
        readme = (ROOT / 'mega' / 'README.txt').read_bytes()
        self.assertTrue(readme.startswith(b'\xef\xbb\xbf'))
        self.assertIn(self.FLASHED, readme.decode('utf-8'))


class FlashToolTests(unittest.TestCase):
    """mega/tools/flash_mega.py with everything that would touch a port replaced: what it refuses, what it reports."""

    NOW = megasim.FW_TAG

    def run_tool(self, arguments, boards, ui=False, burn=True, answer=('READY,x,fw=' + megasim.FW_TAG, {
            'fw': megasim.FW_TAG, 'sa': 'idle', 'sb': 'idle', 'valves': '00', 'cleaning': '0', 'clean': '2:8',
            'links': '10000002'}, 'GSTAT,active=0')):
        flash = tool('flash_mega')
        calls = []
        flash.megas = lambda: list(boards)
        flash.ui_running = lambda: ui
        flash.burn = lambda device, image: calls.append(('burn', device, image.name)) or burn
        flash.ask_board = lambda device, wait_s=8.0: calls.append(('ask', device)) or answer
        out = io.StringIO()
        argv = sys.argv
        sys.argv = ['flash_mega.py'] + list(arguments)
        try:
            with contextlib.redirect_stdout(out):
                code = flash.main()
        finally:
            sys.argv = argv
        return code, calls, out.getvalue()

    MEGA = ('COM91', 'Arduino Mega 2560 (COM91)', 'TEST0000000000000091')

    def test_it_writes_the_firmware_of_this_folder_to_the_only_board_and_asks_it(self):
        code, calls, text = self.run_tool(['--yes'], [self.MEGA])
        self.assertEqual((code, calls), (0, [('burn', 'COM91', 'plasmic-mega.hex'), ('ask', 'COM91')]))
        self.assertIn('Plasmic ' + self.NOW, text)
        self.assertIn('Water ingress 2, OUT 8', text)
        self.assertIn('10000002', text)
        self.assertIn(hashlib.sha256((ROOT / 'mega' / 'hex' / 'plasmic-mega.hex').read_bytes()).hexdigest(), text)
        code, calls, _text = self.run_tool(['--yes', '--stock'], [self.MEGA],
                                           answer=('READY,x', {'sa': 'idle', 'sb': 'idle', 'valves': '00'}, ''))
        self.assertEqual((code, calls), (0, [('burn', 'COM91', 'v7-stock.hex'), ('ask', 'COM91')]))

    def test_it_does_nothing_when_it_is_not_sure_which_board_or_the_port_is_taken(self):
        other = ('COM95', 'Arduino Mega 2560 (COM95)', 'TEST0000000000000095')
        for arguments, boards, ui, words in ((['--yes'], [], False, 'not found Arduino Mega'),
                                             (['--yes'], [self.MEGA, other], False, 'more than one piece'),
                                             (['--yes', '--port', 'COM92'], [self.MEGA], False, 'COM92 Not now Arduino Mega'),
                                             (['--yes'], [self.MEGA], True, 'The operation screen is being executed')):
            code, calls, text = self.run_tool(arguments, boards, ui=ui)
            self.assertEqual((code, calls), (2, []), arguments)
            self.assertIn(words, text)
        code, calls, _text = self.run_tool(['--yes', '--port', 'com95'], [self.MEGA, other])      # named: that one
        self.assertEqual(calls[0], ('burn', 'COM95', 'plasmic-mega.hex'))

    def test_a_failed_write_and_an_unexpected_answer_are_said(self):
        code, calls, text = self.run_tool(['--yes'], [self.MEGA], burn=False)
        self.assertEqual((code, calls), (3, [('burn', 'COM91', 'plasmic-mega.hex')]))      # and the board is not asked
        self.assertIn('burning failed', text)
        for answer in (('', {}, ''), ('READY,x', {'sa': 'idle', 'sb': 'idle', 'valves': '00'}, ''),          # still V7's firmware
                       ('READY,x,fw=p1', {'fw': 'p1', 'sa': 'idle', 'sb': 'idle', 'valves': '00'}, ''),      # an older build of ours
                       ('READY,x,fw=' + self.NOW, {'fw': self.NOW, 'sa': 'run', 'sb': 'idle', 'valves': '00'}, ''),       # something turns
                       ('READY,x,fw=' + self.NOW, {'fw': self.NOW, 'sa': 'idle', 'sb': 'idle', 'valves': '01'}, '')):     # a valve is open
            code, _calls, text = self.run_tool(['--yes'], [self.MEGA], answer=answer)
            self.assertEqual(code, 5, answer)
            self.assertIn('is different from expected', text)

    def test_checking_only_asks(self):
        code, calls, text = self.run_tool(['--check'], [self.MEGA])
        self.assertEqual((code, calls), (0, [('ask', 'COM91')]))
        self.assertNotIn('SHA-256', text)
        for answer in (('READY,x', {'sa': 'idle', 'sb': 'idle', 'valves': '00'}, ''),
                       ('READY,x,fw=p1', {'fw': 'p1', 'sa': 'idle', 'sb': 'idle', 'valves': '00'}, '')):
            code, calls, _text = self.run_tool(['--check'], [self.MEGA], answer=answer)
            self.assertEqual((code, calls), (0, [('ask', 'COM91')]))                         # any firmware is an answer


if __name__ == '__main__':
    unittest.main(verbosity=2)
