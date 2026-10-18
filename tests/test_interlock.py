"""pc/devices.py : Who uses the equipment? The pump and high pressure cannot be started and stopped at the same time (SPEC B.4, A.4, B.12) .

    python tests/test_interlock.py
    python tests/test_interlock.py RaceTests

all on both emulators (V7 own pump control board simulator,test_tcpd of leave RPi,only connect 127.0.0.1) .
will not open or list any ports on this computer.
"""
from __future__ import annotations

import random
import re
import sys
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_testing as ct  # noqa: E402  (sets PLASMIC_NO_SERIAL=1 before anything else is loaded)
import devices  # noqa: E402
import rpilink  # noqa: E402
from devices import Blocked, Busy  # noqa: E402
from pumpboard import PumpBoardError  # noqa: E402

FIX_STOP = 'Press ”Stop” at the bottom STOP”'
FIX_TICK = 'After confirming that the high-voltage power supply is turned off, check the box in the confirmation window'
T_GATE = 'The high pressure has not been turned off and the pump cannot be started.'
T_NO_RPI = 'RPi There is no connection and it is impossible to confirm that the high voltage is off.'
T_UNREACHABLE = 'The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm.'
T_UNCONFIRMED = 'The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately.'
T_SILENT = 'The high voltage power supply does not respond and the program cannot confirm that it is off;Nano Confirmed no output. If the power is turned off from the switch, this is normal.'
T_PUMP_WITH_HV = 'High voltage is displayed as ON,But the pump is still running: All pumps have been stopped. Please go to the site to confirm.'


def setUpModule():
    ct.set_up()


def tearDownModule():
    ct.tear_down()


class StubRunner:
    """What Devices asks of the runner."""

    def __init__(self):
        self.state = ''                  # '' | 'busy' | 'paused' | 'paused_round'
        self.stops = 0
        self.rpi_round = False           # a round whose Recipe has a step for the RPi holds the equipment

    def ownership(self):
        return self.state

    def round_uses_rpi(self):
        return self.rpi_round

    def request_stop(self):
        self.stops += 1
        return {'schedule': bool(self.state), 'round': self.state == 'paused_round'}


class StubManual:
    """What Devices asks of the manual operations."""

    def __init__(self, devices_object=None):
        self.devices = devices_object
        self.running = True              # a pump started by hand is still turning
        self.cancels = 0

    def settle(self):
        if not self.running and self.devices is not None:
            self.devices.release('manual')

    def cancel(self):
        self.cancels += 1
        return self.running


def fresh(rig):
    """Have the pretend RPi read its pretend equipment now, and wait until this side has that reading."""
    rig.rpi.command('status', fresh=True)
    seen = rig.rpi.status_mono
    ct.wait_for(lambda: rig.rpi.status_mono != seen, 5, 'a new status of the RPi')


def supply(rig, on):
    """Switch the pretend high-voltage supply by hand (as if somebody else had done it)."""
    rig.sim.backend.run_stop = 1 if on else 0
    fresh(rig)
    ct.wait_for(lambda: rig.rpi.hv_view()['idle'] is (not on), 5, 'the supply to read %s' % ('ON' if on else 'OFF'))


class OwnershipTests(ct.Case):
    """SPEC B.4 "Ownership": one owner at a time; who is refused, and with which sentence."""

    def refused(self, text, code, call, *args):
        with self.assertRaises(Busy) as caught:
            call(*args)
        self.assertEqual((str(caught.exception), caught.exception.code), (text, code))

    def test_the_matrix(self):
        rig = self.rig(pump='off', rpi=False, analyst=False)
        box, schedule_, by_hand = rig.devices, StubRunner(), StubManual()
        box.bind(schedule_, by_hand)
        self.assertIsNone(box.owner)
        with self.assertRaises(ValueError):
            box.acquire('page', 'x', 'x')
        # A round takes the equipment.
        owner = box.acquire('round', 'r1', 'Discharge measurement only 2/6 Second-rate')
        self.assertEqual((owner['kind'], owner['id'], owner['label']), ('round', 'r1', 'Discharge measurement only 2/6 Second-rate'))
        self.assertTrue(owner['since'])
        self.assertEqual(box.owner, owner)
        self.assertEqual(box.view()['owner'], {'kind': 'round', 'label': 'Discharge measurement only 2/6 Second-rate', 'since': owner['since']})
        schedule_.state = 'busy'
        for kind in ('manual', 'tool'):
            self.refused('Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out', 'schedule_active', box.acquire, kind, 'm', 'x')
        # ... and keeps it while it is paused: manual starts stay locked, with the other sentence.
        schedule_.state = 'paused_round'
        for kind in ('manual', 'tool'):
            self.refused('The process is paused: Please click ”Continue” at the bottom RESUME”or“stop STOP”', 'round_active', box.acquire, kind, 'm', 'x')
        self.assertFalse(box.release('somebody-else'))
        self.assertEqual(box.owner['id'], 'r1')
        self.assertTrue(box.release('r1'))
        self.assertIsNone(box.owner)
        # The schedule waits for its next slot: nobody holds the equipment, and still nothing by hand.
        schedule_.state = 'busy'
        for kind in ('manual', 'tool'):
            self.refused('Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out', 'schedule_active', box.acquire, kind, 'm', 'x')
        self.assertEqual(box.acquire('round', 'r2', 'x')['id'], 'r2')    # its own round may of course begin
        box.release('r2')
        # Paused between two rounds: the equipment is free for manual work (as the original).
        schedule_.state = 'paused'
        self.assertEqual(box.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')['kind'], 'manual')
        self.assertEqual(box.acquire('manual', 'manual', 'Manual: Pump 1 · water')['label'], 'Manual: Pump 7 · OUT')    # a second pump
        self.refused('Another operation is in progress', 'manual_active', box.acquire, 'tool', 't1', 'Return to zero')
        self.refused('Another operation is in progress', 'manual_active', box.acquire, 'manual', 'other', 'x')
        self.refused('Manual operation is not over yet', 'manual_active', box.acquire, 'round', 'r3', 'x')        # the slot is not started
        self.assertEqual(box.start_problems({})[0], {'code': 'manual_active', 'text': 'Manual operation is not over yet',
                                                      'fix': 'Click on that column first OFF,or wait for it to finish'})
        box.release('manual')
        # A maintenance tool.
        schedule_.state = ''
        self.assertEqual(box.acquire('tool', 't1', 'Clear Nano fault')['kind'], 'tool')
        self.refused('Another operation is in progress', 'tool_active', box.acquire, 'manual', 'manual', 'x')
        self.refused('Another operation is in progress', 'tool_active', box.acquire, 'tool', 't2', 'x')
        self.refused('Repair tool is using the device', 'tool_active', box.acquire, 'round', 'r4', 'x')
        self.assertEqual(box.start_problems({}), [{'code': 'tool_active', 'text': 'Repair tool is using the device', 'fix': ''}])
        self.assertEqual(box.view()['interlock']['manual']['reasons'][0]['code'], 'tool_active')
        box.release('t1')
        self.assertEqual(box.start_problems({}), [])

    def test_a_pump_started_by_hand_that_has_ended_gives_the_equipment_back(self):
        rig = self.rig(pump='off', rpi=False, analyst=False)
        box = rig.devices
        by_hand = StubManual(box)
        box.bind(StubRunner(), by_hand)
        box.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')
        self.refused('Manual operation is not over yet', 'manual_active', box.acquire, 'round', 'r1', 'x')
        by_hand.running = False          # its time was over: nobody pressed anything
        self.assertIsNone(box.owner)
        self.assertEqual(box.acquire('round', 'r1', 'x')['kind'], 'round')
        box.release('r1')

    def test_a_runner_that_cannot_answer_counts_as_busy(self):
        rig = self.rig(pump='off', rpi=False, analyst=False)
        broken = StubRunner()
        broken.ownership = lambda: 1 / 0
        rig.devices.bind(broken, StubManual())
        self.refused('Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out', 'schedule_active', rig.devices.acquire, 'manual', 'm', 'x')


class MotionTests(ct.Case):
    """SPEC B.4, "before any pump / syringe motion": permit_motion() and the gate of every wire command."""

    def blocked(self, call, *args):
        with self.assertRaises(Blocked) as caught:
            call(*args)
        self.assertEqual(str(caught.exception), 'Cannot start pump now')
        return caught.exception.reasons

    def gate_refuses(self, rig, motor=8):
        sent = len(rig.tx())
        with self.assertRaises(PumpBoardError) as caught:
            rig.pump.run(motor, 40, 1, 5, 'manual')
        self.assertEqual((caught.exception.code, caught.exception.text), ('blocked', T_GATE))
        self.assertEqual([command for command in rig.tx()[sent:] if command != '?'], [])     # nothing was written

    def test_with_the_rpi_connected_the_high_voltage_must_be_known_to_be_off(self):
        rig = self.rig()
        box = rig.devices
        box.permit_motion()              # HSPY off, Nano idle, a status of this second
        self.assertFalse(box.hv_blocks_motion())
        self.assertEqual(box.motion_problems(), [])
        # HV on (somebody else switched it on): refused, with what to do.
        supply(rig, True)
        self.assertEqual(self.blocked(box.permit_motion), [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': FIX_STOP}])
        self.assertEqual(self.blocked(box.permit_motion, True), [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': FIX_STOP}])
        self.assertTrue(box.hv_blocks_motion())
        self.gate_refuses(rig)           # whoever calls the board directly is stopped at the wire
        view = box.view()['interlock']['manual']
        self.assertEqual((view['ok'], view['need_hv_ack'], view['reasons']),
                         (False, False, [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': FIX_STOP}]))
        self.assertEqual(box.start_problems({'uses_pump': True})[-1]['code'], 'hv_on')
        supply(rig, False)
        box.permit_motion()
        # A tick that nobody had to give (the RPi answered "off") is worth nothing when the RPi goes away later.
        box.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')
        box.permit_motion(True)
        real = rig.rpi.hv_view
        rig.rpi.hv_view = lambda: {'known': False, 'idle': False, 'reasons': ['RPi Not connected']}
        self.patch(box, 'rpi_connected', lambda: False)
        self.assertTrue(box.hv_blocks_motion())
        self.gate_refuses(rig)
        box.permit_motion(True)          # ... a tick given now, with no RPi, does count
        self.assertFalse(box.hv_blocks_motion())
        rig.rpi.hv_view = real
        box.rpi_connected = lambda: True
        box.release('manual')
        # A status that is too old says nothing: unknown is not "off". (With the tick: the next test.)
        self.patch(rpilink, 'HV_RECEIVED_SECONDS', -1.0)
        self.assertEqual(self.blocked(box.permit_motion), [{'code': 'hv_unknown', 'text': 'RPi status exceeds 5 has not been received for seconds, and it cannot be confirmed that the high voltage is off.',
                                                            'fix': FIX_TICK}])
        self.assertTrue(box.hv_blocks_motion())
        self.gate_refuses(rig)
        self.assertTrue(rig.pumps_off())

    def test_the_tick_also_counts_while_a_connected_rpi_cannot_tell(self):
        """7h. The rows of SPEC B.4 with `known` false: no status for 5 s, a reading of the equipment older than
        10 s, a read-back that is not valid (the supply switched off at its mains switch — the usual state of
        the real rig between measurements)."""
        rig = self.rig()
        box = rig.devices
        ask = 'Unable to read the status of the high-voltage power supply (%s) ,The program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.'
        pump = {'uses_pump': True}

        def cannot_tell(row):
            """Refused without the tick, with the sentence of that row; asked for in the dialog; passes with it."""
            self.assertTrue(box.rpi_connected())
            no = [{'code': 'hv_unknown', 'text': row + ',Unable to confirm that the high voltage is off', 'fix': FIX_TICK}]
            self.assertEqual(self.blocked(box.permit_motion), no)
            for ack in (False, 'yes', 1):
                self.assertEqual(box.motion_problems(ack), no)
                self.assertEqual(box.start_problems(pump, ack), no)
            view = box.view()['interlock']['manual']
            self.assertEqual(view, {'ok': True, 'reasons': [], 'need_hv_ack': True, 'hv_ack_text': ask % row})
            self.assertEqual(box.hv_ack(), {'need': True, 'text': ask % row})
            self.assertTrue(box.hv_blocks_motion())
            self.gate_refuses(rig)
            for ack in (True, box.tick()):
                self.assertEqual((box.motion_problems(ack), box.start_problems(pump, ack)), ([], []))
            self.assertTrue(box.hv_blocks_motion())              # asking opens nothing
            box.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')
            box.permit_motion(True)
            self.assertFalse(box.hv_blocks_motion())             # the gate honours it for the one who holds the equipment
            rig.pump.run(8, 40, 1, 5, 'manual')
            self.assertEqual(rig.board.mstate['B'], 'move')
            rig.pump.stop_motor(8)
            box.release('manual')
            self.assertTrue(box.hv_blocks_motion())              # ... and for nobody after that
            self.gate_refuses(rig)

        self.assertEqual(box.hv_ack(), {'need': False, 'text': ''})
        self.assertEqual(box.view()['interlock']['manual'], {'ok': True, 'reasons': [], 'need_hv_ack': False, 'hv_ack_text': ''})
        for name, row in (('HV_RECEIVED_SECONDS', 'RPi status exceeds 5 Not received in seconds No update for'),
                          ('HV_SNAPSHOT_SECONDS', 'RPi The device status read exceeds 10 seconds')):
            limit = getattr(rpilink, name)
            setattr(rpilink, name, -1.0)
            try:
                cannot_tell(row)
            finally:
                setattr(rpilink, name, limit)
        rig.supply_answers(False)
        cannot_tell('high voltage power supply/Nano is invalid Press the password of')
        # A Recipe that measures cannot start like this, tick or not: the RPi itself says why.
        found = box.start_problems({'uses_pump': True, 'uses_rpi': True, 'uses_plasma': True}, True)
        self.assertEqual([row['code'] for row in found], ['rpi_not_ready'])
        # … in the words of someone at the rig: the supply is switched off at its mains switch (AMENDMENTS M17)
        self.assertEqual(found[0]['text'], 'Failed the pre-discharge inspection: high voltage power supply (HSPY) No response: The power is not on, or its RS485 line is not connected properly')
        self.assertTrue(found[0]['fix'].startswith('Turn on the high voltage power supply (HSPY) ’s power switch, and then click ”Recheck”.'), found[0]['fix'])
        # The supply answers again. Known and off: nobody looks at a tick. Known and ON: refused, tick or not.
        early = box.tick()
        rig.supply_answers(True)
        self.assertEqual(box.hv_ack(), {'need': False, 'text': ''})
        box.permit_motion()
        supply(rig, True)
        on = [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': FIX_STOP}]
        for ack in (False, True, early, box.tick()):
            self.assertEqual(self.blocked(box.permit_motion, ack), on)
            self.assertEqual(box.start_problems(pump, ack), on)
        view = box.view()['interlock']['manual']
        self.assertEqual(view, {'ok': False, 'reasons': on, 'need_hv_ack': False, 'hv_ack_text': ''})
        self.assertTrue(box.hv_blocks_motion())
        # ... and once it was seen ON, a tick from before that is void when it cannot be read again.
        supply(rig, False)
        rig.supply_answers(False)
        self.assertEqual(self.blocked(box.permit_motion, early)[0]['code'], 'hv_unknown')
        box.permit_motion(box.tick())
        rig.supply_answers(True)
        self.assertTrue(rig.pumps_off())

    def test_one_device_that_is_silent_does_not_hide_the_other_one_reading_on(self):
        """A device that answers and reads ON decides, whatever the other one does (hv_view(), SPEC B.4): ON is
        ON, no tick opens anything, and the watch stops a pump that is moving."""
        self.patch(devices, 'WATCH_SECONDS', 0.1)
        rig = self.rig()
        box, backend = rig.devices, rig.sim.backend
        pump = {'uses_pump': True}
        early = box.tick()
        box.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        # The Nano does not answer: that alone cannot be read, and stops nothing that runs.
        rig.nano_answers(False)
        ct.wait_for(lambda: rig.rpi.hv_view() == {'known': False, 'idle': False, 'reasons': ['high voltage power supply/Nano is invalid Press the password of']}, 5,
                    'the read-back of the Nano to be invalid')
        time.sleep(0.4)
        self.assertEqual(rig.board.mstate['B'], 'move')
        # ... and now the supply, which does answer, reads ON.
        backend.run_stop = 1
        fresh(rig)
        ct.wait_for(lambda: rig.rpi.hv_view() == {'known': True, 'idle': False, 'reasons': ['high voltage power supply is ON']}, 5, 'the supply to read ON')
        ct.wait_for(rig.pumps_off, 3, 'the watch to stop the pump')
        self.assertEqual(ct.wait_for(lambda: rig.alarm('pump_with_hv'), 3, 'the alarm')['priority'], 1)
        on = [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': FIX_STOP}]
        for ack in (False, True, early, box.tick()):
            self.assertEqual(self.blocked(box.permit_motion, ack), on)
            self.assertEqual(box.start_problems(pump, ack), on)
        self.assertEqual(box.view()['interlock']['manual'], {'ok': False, 'reasons': on, 'need_hv_ack': False, 'hv_ack_text': ''})
        self.assertTrue(box.hv_blocks_motion())
        self.gate_refuses(rig)
        # all_off does not spare the RPi its stop either, tick or not: something reads ON, and the RPi can be reached.
        self.assertEqual(box.all_off(False, False, True)['rpi'], {'result': 'confirmed', 'error': ''})
        self.assertEqual(backend.run_stop, 0)
        # Now the supply reads off and the Nano is still silent: that cannot be read. The tick decides — one that
        # is newer than the "ON".
        fresh(rig)
        ct.wait_for(lambda: box.hv_ack()['need'], 5, 'the supply to read off')
        self.assertEqual(self.blocked(box.permit_motion, early)[0]['code'], 'hv_unknown')
        box.permit_motion(True)
        rig.nano_answers(True)
        # The other way round: the supply does not answer, and the Nano reads as not idle.
        rig.supply_answers(False)
        self.assertTrue(box.hv_ack()['need'])
        backend.state = 'RUNNING'
        fresh(rig)
        ct.wait_for(lambda: rig.rpi.hv_view() == {'known': True, 'idle': False, 'reasons': ['Nano is still outputting or is ready']}, 5,
                    'the Nano to read as running')
        self.assertEqual(box.hv_ack(), {'need': False, 'text': ''})
        busy = [{'code': 'hv_on', 'text': 'Nano is still outputting or is ready', 'fix': FIX_STOP}]
        for ack in (False, True, box.tick()):
            self.assertEqual(self.blocked(box.permit_motion, ack), busy)
        self.assertTrue(box.hv_blocks_motion())
        backend.state = 'IDLE'
        rig.supply_answers(True)
        ct.wait_for(lambda: rig.rpi.hv_view()['idle'], 5, 'everything to read off again')
        box.permit_motion()

    def test_without_the_rpi_only_the_tick_of_the_start_dialog_lets_a_pump_move(self):
        rig = self.rig(rpi=False)
        box = rig.devices
        self.assertFalse(box.rpi_connected())
        reasons = self.blocked(box.permit_motion)
        self.assertEqual(reasons, [{'code': 'hv_unknown', 'text': 'RPi There is no connection and it is impossible to confirm that the high voltage is off.', 'fix': ''}])
        self.assertTrue(box.hv_blocks_motion())
        self.gate_refuses(rig)
        view = box.view()['interlock']['manual']
        self.assertEqual(view, {'ok': True, 'reasons': [], 'need_hv_ack': True,
                                'hv_ack_text': 'RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.'})
        self.assertEqual(box.start_problems({'uses_pump': True}),
                         [{'code': 'hv_unknown', 'text': 'RPi There is no connection and it is impossible to confirm that the high voltage is off.', 'fix': ''}])
        self.assertEqual(box.start_problems({'uses_pump': True}, ack=True), [])
        # The tick belongs to the one who gave it: it is void once the equipment changes hands.
        box.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')
        box.permit_motion(True)
        self.assertFalse(box.hv_blocks_motion())
        rig.pump.run(8, 40, 1, 5, 'manual')
        self.assertEqual(rig.board.mstate['B'], 'move')
        rig.pump.stop_motor(8)           # a stop is never held back
        box.release('manual')
        self.assertTrue(box.hv_blocks_motion())
        self.gate_refuses(rig, 3)
        box.acquire('round', 'r1', 'x')
        self.assertTrue(box.hv_blocks_motion())      # the next owner did not tick anything
        self.blocked(box.permit_motion, False)
        box.release('r1')
        self.assertEqual([command for command in rig.tx() if command.startswith('P,3,')], [])

    def test_a_tick_that_is_kept_for_later_is_void_once_something_speaks_against_it(self):
        """Devices.tick(): what a schedule keeps of its start dialog. It is a statement about one moment."""
        rig = self.rig()
        box = rig.devices
        no_rpi = [{'code': 'hv_unknown', 'text': T_NO_RPI, 'fix': ''}]
        self.assertIs(box.tick(False), False)
        self.assertIs(box.tick('yes'), False)
        early = box.tick()
        self.assertIsInstance(early, devices.Tick)
        box.permit_motion(early)         # the RPi says "off": nobody looks at a tick
        # The RPi reports the high voltage ON, then off again, then it is gone. The last anybody saw was "off",
        # but the tick is older than that "ON": somebody has to look again.
        supply(rig, True)
        self.assertEqual(self.blocked(box.permit_motion, early)[0]['code'], 'hv_on')
        supply(rig, False)
        box.permit_motion(early)
        rig.rpi_gone()
        self.assertEqual(self.blocked(box.permit_motion, early), no_rpi)
        self.assertEqual(box.motion_problems(early), no_rpi)
        self.assertEqual(box.start_problems({'uses_pump': True}, early), no_rpi)
        self.assertTrue(box.hv_blocks_motion())
        later = box.tick()               # ticked now
        self.assertEqual(box.motion_problems(later), [])
        self.assertEqual(box.start_problems({'uses_pump': True}, later), [])
        box.acquire('round', 'r1', 'x')
        box.permit_motion(later)
        self.assertFalse(box.hv_blocks_motion())
        rig.pump.run(8, 40, 1, 5, 'round')
        self.assertEqual(rig.board.mstate['B'], 'move')
        rig.pump.stop_motor(8)
        box.permit_motion(later)         # ... and it goes on counting for the rounds that follow
        box.release('r1')

    def test_a_weight_dose_that_is_running_ends_when_the_high_voltage_is_no_longer_known(self):
        """The gate sits in front of every wire command: V7's weight dose changes its speed with new
        commands, and the first one after the RPi went away is refused."""
        rig = self.rig()
        rig.devices.permit_motion()
        box = self.background(rig.pump.dose_weight, 8, 500, 'round', threading.Event())
        ct.wait_for(lambda: rig.board.mstate['B'] == 'run', 5, 'the weight dose to pump')
        self.patch(rpilink, 'HV_RECEIVED_SECONDS', -1.0)
        self.assertTrue(rig.devices.hv_blocks_motion())
        # Nothing new starts; what runs is stopped by STOP as always.
        with self.assertRaises(PumpBoardError):
            rig.pump.run(3, 40, 1, 5, 'round')
        self.assertEqual(rig.devices.stop_everything('test')['pump']['result'], 'confirmed')
        self.assertIsInstance(self.joined(box).get('error'), PumpBoardError)
        self.assertTrue(rig.pumps_off())


class HighVoltageSessionTests(ct.Case):
    """SPEC B.4, "before plasma_run": hv_session() — `hv_pending` first, then the pumps must be at rest."""

    def no_session(self, rig, code, text=None):
        with self.assertRaises(Blocked) as caught:
            with rig.devices.hv_session():
                self.fail('the session must not open')
        self.assertEqual(str(caught.exception), 'The pump is still moving and cannot discharge. There are two options for ”')
        self.assertEqual(caught.exception.reasons[0]['code'], code)
        if text is not None:
            self.assertEqual(caught.exception.reasons[0]['text'], text)
        self.assertEqual((rig.devices.hv_pending, rig.devices.hv_active), (False, False))

    def test_with_the_board_at_rest_the_session_opens_and_holds_every_pump(self):
        rig = self.rig()
        box = rig.devices
        with box.hv_session():
            self.assertEqual((box.hv_pending, box.hv_active), (False, True))
            self.assertTrue(box.hv_blocks_motion())
            with self.assertRaises(Blocked) as caught:
                box.permit_motion()
            self.assertEqual(caught.exception.reasons, [{'code': 'hv_on', 'text': 'is discharging', 'fix': FIX_STOP}])
            with self.assertRaises(Blocked):
                box.permit_motion(True)
            for call, args in ((rig.pump.run, (8, 40, 1, 5, 'manual')), (rig.pump.dose, (8, 1, 1, 'manual', threading.Event())),
                               (rig.pump.clean, ('manual', threading.Event())),
                               (rig.pump.syringe_init, ('manual', threading.Event()))):
                with self.assertRaises(PumpBoardError) as refused:
                    call(*args)
                self.assertEqual((refused.exception.code, refused.exception.text), ('blocked', T_GATE))
            with self.assertRaises(Blocked):         # one discharge at a time
                with box.hv_session():
                    self.fail('a second session opened')
            self.assertEqual(box.view()['interlock']['manual']['reasons'][0]['text'], 'is discharging')
            rig.pump.stop_motor(8)                   # stops are never gated
        self.assertEqual((box.hv_pending, box.hv_active), (False, False))
        self.assertFalse(box.hv_blocks_motion())
        box.permit_motion()
        self.assertEqual([c for c in rig.tx() if c == 'C' or c.startswith(('P,', 'Y,INIT'))], [])

    def test_a_pump_that_moves_keeps_the_high_voltage_off(self):
        rig = self.rig()
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 20, 'manual')
        self.no_session(rig, 'pump_moving', 'The pump is still running')
        self.assertFalse(rig.devices.hv_blocks_motion())     # the refusal left no flag behind
        rig.pump.stop_motor(8)
        # A blocking operation counts from its first moment (it holds the board).
        box = self.background(rig.pump.dose, 8, 20, 1, 'manual', threading.Event())
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        self.no_session(rig, 'pump_moving')
        rig.pump.stop_all('test')
        self.joined(box)
        with rig.devices.hv_session():
            pass

    def test_a_valve_that_is_still_closing_is_waited_for(self):
        rig = self.rig(valves__close_after_ms=1200)
        rig.pump.save_pump_setup(ct.pt.setup_of(valves={1: 2}))        # Maintenance › Pump and Scale: valve 1 opens with Pump 1 (M20)
        rig.devices.permit_motion()
        rig.pump.run(2, 40, 1, 1, 'manual')          # motor 2 has valve 1; the board ends the run after 1 s
        ct.wait_for(lambda: rig.board.mstate['A'] == 'idle' and rig.board.valves[1], 5, 'the motor at rest, the valve open')
        started = time.perf_counter()
        with rig.devices.hv_session():
            self.assertFalse(rig.board.valves[1])
            self.assertGreater(time.perf_counter() - started, 0.5)
        # ... but not for ever.
        rig.pump.run(2, 40, 1, 1, 'manual')
        ct.wait_for(lambda: rig.board.mstate['A'] == 'idle' and rig.board.valves[1], 5, 'the motor at rest, the valve open')
        self.patch(devices, 'VALVE_WAIT_SECONDS', 0.3)
        self.no_session(rig, 'pump_moving')
        ct.wait_for(lambda: not rig.board.valves[1], 5, 'the valve to close')

    def test_no_board_at_all_is_no_obstacle(self):
        rig = self.rig(pump='off')
        self.assertFalse(rig.pump.in_use)
        with rig.devices.hv_session():
            self.assertTrue(rig.devices.hv_active)
        self.assertEqual(rig.devices.start_problems({'uses_rpi': True, 'uses_plasma': True}), [])

    def test_a_board_that_is_in_use_but_not_connected_cannot_vouch_for_its_pumps(self):
        self.pump_patched(RETRY_S=(0.2, 0.2))
        rig = self.rig()
        plan = self.plan()
        plan.fail_starts = 10 ** 6       # it cannot be opened again
        plan.pull_cable(rig.board)       # ... after the cable was pulled while nothing moved
        ct.wait_for(lambda: not rig.pump.connected, 8, 'the board to be lost')
        self.assertFalse(rig.pump.motion_uncertain)
        self.no_session(rig, 'pump_link', 'The pump control board has been set but not connected, and the pump cannot be confirmed to have stopped.')
        self.assertEqual(rig.devices.start_problems({'uses_rpi': True, 'uses_plasma': True})[0],
                         {'code': 'pump_link', 'text': 'The pump control board has been set but not connected, and the pump cannot be confirmed to have stopped.',
                          'fix': 'Connected USB,Or ask maintenance personnel to go to ”Repair › Connection” was changed to ”Do not use pump control board”'})
        found = rig.devices.start_problems({'uses_pump': True})
        self.assertEqual((found[0]['code'], found[0]['fix']), ('pump_link', 'examine USB Wire'))
        self.assertTrue(found[0]['text'].startswith('Pump control board is not connected ('))
        self.assertEqual(rig.devices.view()['interlock']['manual']['reasons'][0]['code'], 'pump_link')
        plan.fail_starts = 0
        ct.wait_for(lambda: rig.pump.connected, 10, 'the board to come back')
        with rig.devices.hv_session():
            pass

    def test_a_board_whose_pumps_may_still_turn_keeps_the_high_voltage_off(self):
        self.pump_patched(RETRY_S=(0.2, 0.2))
        rig = self.rig()
        plan = self.plan()
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        plan.fail_starts = 10 ** 6
        plan.pull_cable(rig.board)       # the cable is pulled while the pump runs: nobody knows any more
        ct.wait_for(lambda: rig.pump.motion_uncertain, 8, 'the board to be uncertain')
        self.no_session(rig, 'pump_uncertain', 'The status of the pump control board is unknown. Please go to the site to confirm first.')
        self.assertIn({'code': 'pump_uncertain', 'text': 'The status of the pump control board is unknown. Please go to the site to confirm first.', 'fix': ''},
                      rig.devices.start_problems({'uses_rpi': True, 'uses_plasma': True}))
        self.assertIn('pump_uncertain', [row['code'] for row in rig.devices.start_problems({'uses_pump': True})])
        self.assertIn('pump_uncertain', [row['code'] for row in rig.devices.view()['interlock']['start']['reasons']])
        self.assertEqual(rig.alarm('pump_stop_unconfirmed')['priority'], 1)
        plan.fail_starts = 0
        ct.wait_for(lambda: rig.pump.connected and not rig.pump.motion_uncertain, 10, 'the board to come back')
        ct.wait_for(lambda: rig.alarm('pump_stop_unconfirmed') is None or rig.alarm('pump_stop_unconfirmed')['cleared'], 5,
                    'the alarm to clear after the reconnect')

    def test_the_session_ends_only_when_the_high_voltage_reads_off(self):
        rig = self.rig()
        box = rig.devices
        self.patch(devices, 'HV_SETTLE_SECONDS', 1.0)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        # Off again a moment after the discharge: the session waits for it.
        started = time.perf_counter()
        with box.hv_session():
            supply(rig, True)
            threading.Timer(0.4, lambda: supply(rig, False)).start()
        self.assertGreater(time.perf_counter() - started, 0.4)
        self.assertEqual((box.hv_active, rig.alarm('hv_stop_unconfirmed')), (False, None))
        # Still on after the time it gets: the flag stays up, nothing of the board may start, and an alarm says so.
        with box.hv_session():
            supply(rig, True)
        self.assertTrue(box.hv_active)
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']),
                         (1, 'high pressure', 'The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately.'))
        self.assertTrue(box.hv_blocks_motion())
        with self.assertRaises(Blocked):
            box.permit_motion()
        # The moment it is read back as off, both are over.
        supply(rig, False)
        box.permit_motion()
        self.assertFalse(box.hv_active)
        self.assertFalse(box.hv_blocks_motion())
        self.assertTrue(rig.alarm('hv_stop_unconfirmed') is None or rig.alarm('hv_stop_unconfirmed')['cleared'])

    def test_after_a_stop_that_was_never_confirmed_the_read_back_or_a_new_tick_on_site_decides(self):
        """With the RPi there, only its read-back lowers the flag and a tick is ignored. With the RPi gone, the
        tick of somebody who has been on site since then does — as it would after a restart of this program."""
        self.patch(devices, 'HV_SETTLE_SECONDS', 0.6)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        no_rpi = [{'code': 'hv_unknown', 'text': T_NO_RPI, 'fix': ''}]
        with self.one() as rig:
            box = rig.devices
            early = box.tick()
            with box.hv_session():
                supply(rig, True)        # ... and it stays ON
            self.assertTrue(box.hv_active)
            for ack in (False, True, early, box.tick()):         # the RPi is connected and says ON: no tick helps
                with self.assertRaises(Blocked) as caught:
                    box.permit_motion(ack)
                self.assertEqual(caught.exception.reasons, [{'code': 'hv_on', 'text': 'high voltage power supply is ON', 'fix': FIX_STOP}])
            self.assertTrue(box.hv_active)
            rig.rpi_gone()
            for ack in (False, early):                           # no tick, or one from before that stop
                with self.assertRaises(Blocked) as caught:
                    box.permit_motion(ack)
                self.assertEqual(caught.exception.reasons, no_rpi)
            self.assertTrue(box.hv_active and box.hv_blocks_motion())
            view = box.view()['interlock']['manual']             # the start dialog asks for the tick
            self.assertEqual((view['ok'], view['need_hv_ack'], view['reasons']), (True, True, []))
            self.assertTrue(box.hv_active)                       # (looking changes nothing)
            box.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')
            box.permit_motion(True)      # somebody has been on site and says that the supply is off
            self.assertFalse(box.hv_active)
            self.assertFalse(box.hv_blocks_motion())
            rig.pump.run(8, 40, 1, 5, 'manual')
            self.assertEqual(rig.board.mstate['B'], 'move')
            rig.pump.stop_motor(8)
            box.release('manual')
            alarm = rig.alarm('hv_stop_unconfirmed')             # the alarm stays until the supply is read back as off
            self.assertEqual((alarm['priority'], alarm['text'], alarm['cleared']), (1, T_UNCONFIRMED, False))
            rig.sim.backend.run_stop = 0                         # (the pretend supply was switched on by hand: off by hand)
        # The RPi goes away in the middle of the discharge: nothing was ever read back.
        with self.one() as rig:
            box = rig.devices
            early = box.tick()
            with box.hv_session():
                rig.rpi_gone()
            self.assertTrue(box.hv_active)
            self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 1)
            with self.assertRaises(Blocked) as caught:
                box.permit_motion(early)
            self.assertEqual(caught.exception.reasons, no_rpi)
            self.assertEqual(box.motion_problems(box.tick()), [])    # asking lowers nothing
            self.assertTrue(box.hv_active)
            box.permit_motion(box.tick())        # a schedule that is started now, with the tick of its start dialog
            self.assertFalse(box.hv_active)

    def test_a_stop_that_was_never_confirmed_because_the_supply_stopped_answering(self):
        """7c with 7h. The supply is switched off at its mains before the end of the discharge was read back:
        the RPi is connected and cannot tell. The tick of somebody who has been on site decides, and it must
        be newer than that stop."""
        self.patch(devices, 'HV_SETTLE_SECONDS', 0.6)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        rig = self.rig()
        box = rig.devices
        no = [{'code': 'hv_unknown', 'text': 'high voltage power supply/Nano is invalid and it cannot be confirmed that the high voltage is off. The object of', 'fix': FIX_TICK}]
        early = box.tick()
        with box.hv_session():
            rig.supply_answers(False)
        self.assertTrue(box.hv_active)
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text']), (1, T_UNCONFIRMED))
        for ack in (False, early):
            with self.assertRaises(Blocked) as caught:
                box.permit_motion(ack)
            self.assertEqual(caught.exception.reasons, no)
        self.assertTrue(box.hv_active and box.hv_blocks_motion())
        view = box.view()['interlock']['manual']
        self.assertEqual((view['ok'], view['need_hv_ack'], view['reasons']), (True, True, []))
        self.assertTrue(box.hv_active)                           # (looking changes nothing)
        # all_off after a round without a step for the RPi: only a tick that is newer than that stop spares the
        # RPi half. (Sent, it comes back as from the real tool with a supply that is silent: not confirmed.)
        self.assertEqual(box.all_off(False, False, early)['rpi']['result'], 'unconfirmed')
        self.assertEqual(box.all_off(False, False, True)['rpi'], {'result': 'none', 'error': ''})
        self.assertTrue(box.hv_active)
        box.permit_motion(True)          # somebody has been on site and says that the supply is off
        self.assertFalse(box.hv_active)
        self.assertFalse(box.hv_blocks_motion())
        alarm = rig.alarm('hv_stop_unconfirmed')                 # the alarm stays until the supply is read back as off
        self.assertEqual((alarm['priority'], alarm['cleared']), (1, False))
        rig.supply_answers(True)
        box.permit_motion()
        self.assertTrue(rig.alarm('hv_stop_unconfirmed') is None or rig.alarm('hv_stop_unconfirmed')['cleared'])

    def test_an_error_inside_the_session_still_closes_it(self):
        rig = self.rig()
        with self.assertRaises(RuntimeError):
            with rig.devices.hv_session():
                raise RuntimeError('Pre-discharge inspection failed: test')
        self.assertEqual((rig.devices.hv_pending, rig.devices.hv_active), (False, False))
        rig.devices.permit_motion()


class RaceTests(ct.Case):
    """SPEC B.12: a manual start and a round's plasma_run at the same moment — never both."""

    ROUNDS = 200

    def test_two_hundred_rounds_of_a_manual_start_against_a_discharge(self):
        rig = self.rig()
        box, pump = rig.devices, rig.pump
        jitter = random.Random(20261006)
        outcomes = {'pump': 0, 'plasma': 0, 'neither': 0}
        for number in range(self.ROUNDS):
            gate, leave = threading.Barrier(2), threading.Event()
            seen = {}
            pause_a, pause_b = jitter.uniform(0, 0.004), jitter.uniform(0, 0.004)

            def by_hand():
                gate.wait(5)
                time.sleep(pause_a)
                try:
                    box.permit_motion()
                    pump.run(8, 40, 1, 30, 'manual')
                    seen['pump'] = True
                except (Blocked, PumpBoardError) as exc:
                    seen['pump'] = False
                    seen['pump_error'] = exc

            def discharge():
                gate.wait(5)
                time.sleep(pause_b)
                try:
                    with box.hv_session():
                        seen['moving_inside'] = not rig.pumps_off()
                        seen['plasma'] = True
                        leave.wait(10)
                        seen['moving_at_end'] = not rig.pumps_off()
                except Blocked:
                    seen['plasma'] = False

            threads = [threading.Thread(target=by_hand, daemon=True), threading.Thread(target=discharge, daemon=True)]
            for thread in threads:
                thread.start()
            threads[0].join(10)
            ct.wait_for(lambda: 'plasma' in seen, 10, 'the discharge side to decide')
            both = seen.get('pump') and seen.get('plasma')
            leave.set()
            threads[1].join(10)
            self.assertFalse(threads[0].is_alive() or threads[1].is_alive(), 'round %d did not end' % number)
            self.assertFalse(both, 'round %d: a pump was started and the high-voltage session opened' % number)
            if seen.get('plasma'):
                self.assertFalse(seen['moving_inside'] or seen['moving_at_end'],
                                 'round %d: a pump moved while the session was open' % number)
                self.assertFalse(seen['pump'])
                self.assertIn(getattr(seen['pump_error'], 'code', None) or 'blocked', ('blocked', 'busy'))
            outcomes['pump' if seen.get('pump') else 'plasma' if seen.get('plasma') else 'neither'] += 1
            if seen.get('pump'):
                pump.stop_motor(8)
            self.assertEqual((box.hv_pending, box.hv_active), (False, False))
            self.assertTrue(rig.pumps_off())
        self.assertEqual(sum(outcomes.values()), self.ROUNDS)
        # Both sides win some of the rounds (about half each on this PC): the test does meet the race.
        self.assertGreater(outcomes['pump'], 0, outcomes)
        self.assertGreater(outcomes['plasma'], 0, outcomes)


class StopTests(ct.Case):
    """SPEC B.4 `stop_everything` (the STOP button) and `all_off`; B.12 "STOP while the board link is down"."""

    def stubs(self, rig):
        schedule_, by_hand = StubRunner(), StubManual()
        by_hand.running = False
        rig.devices.bind(schedule_, by_hand)
        return schedule_, by_hand

    def test_stop_when_nothing_runs(self):
        rig = self.rig()
        schedule_, by_hand = self.stubs(rig)
        stops = rig.sim.controller.stop_requests
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(report, {'accepted': True, 'schedule_ended': False, 'round_stopping': False, 'manual_cancelled': False,
                                  'pump': {'result': 'confirmed', 'error': ''}, 'rpi': {'result': 'confirmed', 'error': ''}})
        self.assertEqual((schedule_.stops, by_hand.cancels), (1, 1))
        self.assertEqual(rig.sim.controller.stop_requests, stops + 1)
        self.assertEqual(rig.tx().count('X'), 2)             # one at the connect, one now
        row = rig.store.events(limit=1)[0]
        self.assertEqual((row['text'], row['actor'], row['level']), ('Stop pressed: pump stopped; high pressure readback OFF', 'Wang Xiaoming', 'info'))
        self.assertEqual(rig.alarm_keys(), [])
        # Again at once, and from "another tab": never refused.
        self.assertEqual(rig.devices.stop_everything('Li Dahua')['pump']['result'], 'confirmed')

    def test_stop_tells_the_schedule_and_the_manual_operation_first(self):
        rig = self.rig(pump='off')
        schedule_, by_hand = self.stubs(rig)
        schedule_.state, by_hand.running = 'paused_round', True
        report = rig.devices.stop_everything('')
        self.assertEqual((report['schedule_ended'], report['round_stopping'], report['manual_cancelled']), (True, True, True))
        self.assertEqual(report['pump'], {'result': 'none', 'error': ''})        # there is no board
        self.assertEqual(report['rpi']['result'], 'confirmed')
        self.assertIn('Stop pressed: pump not in use; high pressure readback OFF', rig.texts())
        # A runner or a manual object that fails does not keep the stop from going out.
        schedule_.request_stop = lambda: 1 / 0
        by_hand.cancel = lambda: 1 / 0
        stops = rig.sim.controller.stop_requests
        self.assertEqual(rig.devices.stop_everything('')['rpi']['result'], 'confirmed')
        self.assertEqual(rig.sim.controller.stop_requests, stops + 1)

    def test_stop_does_not_wait_for_a_running_operation_and_ends_a_discharge(self):
        rig = self.rig(speed=4)
        self.stubs(rig)
        rig.devices.permit_motion()
        dose = self.background(rig.pump.dose, 8, 50, 1, 'round', threading.Event())      # holds the board for a minute
        ct.wait_for(lambda: rig.board.mstate['B'] == 'move', 5, 'the dose to move')
        started = time.perf_counter()
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertLess(time.perf_counter() - started, 3.0)
        self.assertEqual(report['pump'], {'result': 'confirmed', 'error': ''})
        self.assertEqual(self.joined(dose)['error'].code, 'stopped')
        self.assertTrue(rig.pumps_off())
        # A discharge that runs (started here straight on the simulator): the RPi is told to stop it.
        job = rig.rpi.command('plasma_run', attended=False, claim=True, confirm_high_voltage=True)['job']
        ct.wait_for(lambda: rig.sim.backend.run_stop == 1, 10, 'the pretend supply to come on')
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(report['rpi'], {'result': 'cancel_sent', 'error': '', 'job': job})
        self.assertIn('Stop has been pressed: the pump has stopped; the high voltage has sent a stop to the discharge work, etc. RPi read back', rig.texts())
        self.assertEqual(rig.rpi.wait_job(job, limit_s=20), ('stopped', 'Stop has been pressed. The work received an interruption signal. 15'))
        self.assertEqual(rig.sim.backend.run_stop, 0)

    def test_the_rpi_cannot_be_reached_and_the_pumps_are_stopped_all_the_same(self):
        rig = self.rig()
        schedule_, _by_hand = self.stubs(rig)
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        rig.rpi_gone()
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(report['pump'], {'result': 'confirmed', 'error': ''})
        self.assertEqual(report['rpi']['result'], 'unreachable')
        self.assertTrue(report['rpi']['error'].startswith('The stop command cannot be sent RPi ('))
        self.assertTrue(rig.pumps_off())
        # Nothing says that the high voltage is on (the RPi's last word was "off", no discharge of ours): middle.
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (3, 'high pressure', T_UNREACHABLE))
        row = rig.store.events(limit=40)
        self.assertIn(('Stop has been pressed: the pump has stopped; the high pressure stop command cannot be sent RPi', 'error'), [(item['text'], item['level']) for item in row])
        # Emergency when there is a reason to think that it is on: a round with a step for the RPi holds the equipment,
        schedule_.rpi_round = True
        rig.devices.stop_everything('Wang Xiaoming')
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text']), (1, T_UNREACHABLE))
        schedule_.rpi_round = False
        # ... or a discharge of ours whose end was never read back (the flag is put up by hand here),
        rig.alarms.clear('hv_stop_unconfirmed')
        rig.devices.hv_active = True
        rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 1)
        rig.devices.hv_active = False
        # ... or a runner that cannot say what it is doing.
        rig.alarms.clear('hv_stop_unconfirmed')
        schedule_.round_uses_rpi = lambda: 1 / 0
        rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 1)

    def test_a_stop_that_cannot_reach_an_rpi_whose_last_word_was_on_is_urgent(self):
        rig = self.rig()
        self.stubs(rig)
        early = rig.devices.tick()
        supply(rig, True)                # somebody else switched the supply on ...
        # (Devices keeps the RPi's last word itself, from what it sees when it looks — its watch does every 0.5 s.)
        self.assertEqual(rig.devices.motion_problems()[0]['code'], 'hv_on')
        rig.rpi_gone()                   # ... and then the RPi cannot be reached any more
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(report['rpi']['result'], 'unreachable')
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text']), (1, T_UNREACHABLE))
        # all_off: a tick from before that "ON" does not spare the RPi half; one that was given since then does.
        self.assertEqual(rig.devices.all_off(False, False, early)['rpi']['result'], 'unreachable')
        self.assertEqual(rig.devices.all_off(False, False, True)['rpi'], {'result': 'none', 'error': ''})
        rig.sim.backend.run_stop = 0     # (the pretend supply was switched on by hand: off by hand)

    def test_a_stop_with_the_supply_switched_off_at_its_mains_is_not_an_emergency(self):
        """7i. The RPi answers the stop and cannot confirm it. What it reports right after decides: the supply
        does not answer, the Nano is read as quiet, nothing of ours was under way -> `silent`, a lesser alarm."""
        rig = self.rig()
        self.stubs(rig)
        box = rig.devices
        rig.supply_answers(False)        # the usual state of the real rig between measurements
        stops = rig.sim.controller.stop_requests
        report = box.stop_everything('Wang Xiaoming')
        self.assertEqual(report['pump'], {'result': 'confirmed', 'error': ''})
        self.assertEqual(report['rpi'], {'result': 'silent', 'error': T_SILENT})
        self.assertEqual(rig.sim.controller.stop_requests, stops + 1)        # the stop did go out
        self.assertEqual(devices.unconfirmed(report), [])
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text'], alarm['cleared']), (3, 'high pressure', T_SILENT, False))
        self.assertIn(('Stop pressed: pump stopped; high voltage power supply not responding (Nano Confirmed no output)', 'warn', 'Wang Xiaoming'),
                      [(item['text'], item['level'], item['actor']) for item in rig.store.events(limit=40)])
        # Again: the same alarm, not a new one.
        self.assertEqual(box.stop_everything('Wang Xiaoming')['rpi']['result'], 'silent')
        again = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((again['id'], again['priority'], again['text']), (alarm['id'], 3, T_SILENT))
        # all_off after a round with a step for the RPi that was stopped: the same, and that is not "unconfirmed".
        report = box.all_off(False, True)
        self.assertEqual((report['rpi'], devices.unconfirmed(report)), ({'result': 'silent', 'error': T_SILENT}, []))
        # A Nano in FAULT or DONE is quiet too (its outputs are low).
        rig.sim.backend.state, rig.sim.backend.fault = 'FAULT', 'HEARTBEAT_LOST'
        self.assertEqual(box.stop_everything('Wang Xiaoming')['rpi']['result'], 'silent')
        rig.sim.backend.state, rig.sim.backend.fault = 'IDLE', 'NONE'
        # The supply answers again and reads off: the alarm clears by itself, and a stop is confirmed as always.
        rig.supply_answers(True)
        box.permit_motion()
        self.assertTrue(rig.alarm('hv_stop_unconfirmed') is None or rig.alarm('hv_stop_unconfirmed')['cleared'])
        self.assertEqual(box.stop_everything('Wang Xiaoming')['rpi'], {'result': 'confirmed', 'error': ''})

    def test_a_stop_that_is_not_confirmed_stays_an_emergency_in_every_other_case(self):
        """7i: only "the supply is silent, the Nano is confirmed quiet, nothing of ours under way" is the lesser case."""
        self.patch(devices, 'HV_SETTLE_SECONDS', 0.3)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        urgent = (1, T_UNCONFIRMED)

        def stop(rig):
            report = rig.devices.stop_everything('Wang Xiaoming')
            alarm = rig.alarm('hv_stop_unconfirmed')
            return report['rpi']['result'], (alarm['priority'], alarm['text'])

        with self.one() as rig:          # the Nano cannot be read either
            self.stubs(rig)
            rig.supply_answers(False)
            rig.nano_answers(False)
            self.assertEqual(stop(rig), ('unconfirmed', urgent))
            self.assertIn(('Stop pressed: pump stopped; high pressure not confirmed', 'error'),
                          [(item['text'], item['level']) for item in rig.store.events(limit=40)])
        with self.one() as rig:          # the Nano answers, and is not quiet
            self.stubs(rig)
            rig.supply_answers(False)
            rig.sim.backend.state = 'RUNNING'
            fresh(rig)
            self.assertEqual(stop(rig), ('unconfirmed', urgent))
            rig.sim.backend.state = 'IDLE'
        with self.one() as rig:          # a discharge of ours is open, or its end was never read back
            self.stubs(rig)
            with rig.devices.hv_session():
                rig.supply_answers(False)
                self.assertEqual(stop(rig), ('unconfirmed', urgent))
            self.assertTrue(rig.devices.hv_active)
            self.assertEqual(stop(rig), ('unconfirmed', urgent))
            # After the tick of somebody who has been on site the flag is down, and a stop is `silent` again —
            # the alarm that is open stays what it is: Emergency is never lowered.
            rig.devices.permit_motion(True)
            self.assertEqual(stop(rig), ('silent', urgent))
        with self.one() as rig:          # a high-voltage job was running on the RPi when the stop was asked for
            self.stubs(rig)
            rig.supply_answers(False)
            real = rig.rpi.latest
            rig.rpi.latest = lambda max_age=10.0: dict(real(max_age) or {}, job={'id': 'HMI-x', 'state': 'running', 'high_voltage': True})
            self.assertEqual(stop(rig), ('unconfirmed', urgent))
            rig.rpi.latest = lambda max_age=10.0: None           # ... or nobody knows whether one was
            rig.alarms.clear('hv_stop_unconfirmed')
            self.assertEqual(stop(rig), ('unconfirmed', urgent))
            del rig.rpi.latest

    def test_what_the_rpi_reports_right_after_the_stop_is_what_counts(self):
        """7i: a reading of the equipment that the RPi made after the stop was asked for — nothing older. When
        the newest one is older, a fresh one is asked for, as long as the caller still waits for this half."""
        rig = self.rig()
        self.stubs(rig)
        rig.supply_answers(False)
        real, asked, old = rig.rpi.command, [], {'plain': True, 'fresh': False}

        def command(cmd, **fields):
            reply = real(cmd, **fields)
            if cmd == 'status':
                asked.append(fields)
                if old['fresh' if fields.get('fresh') else 'plain']:
                    reply = dict(reply, age_s=30.0)              # (as if the RPi had not been able to read since)
            return reply

        rig.rpi.command = command
        try:
            # The reading it holds is from before the stop; the fresh one is new: silent.
            self.assertEqual(rig.devices.stop_everything('')['rpi']['result'], 'silent')
            self.assertEqual(asked, [{}, {'fresh': True}])
            self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 3)
            # No reading newer than the stop at all: nothing is known.
            del asked[:]
            old['fresh'] = True
            self.assertEqual(rig.devices.stop_everything('')['rpi']['result'], 'unconfirmed')
            self.assertEqual(asked, [{}, {'fresh': True}])
            alarm = rig.alarm('hv_stop_unconfirmed')
            self.assertEqual((alarm['priority'], alarm['text']), (1, T_UNCONFIRMED))
            # Nobody waits for this half any more: no fresh reading is asked for; it reports later, as always.
            rig.alarms.clear('hv_stop_unconfirmed')
            del asked[:]
            self.patch(devices, 'STOP_JOIN_SECONDS', 0.0)
            self.patch(devices, 'STOP_QUIET_JOIN_SECONDS', 0.0)
            self.assertEqual(rig.devices.stop_everything('')['rpi'], {'result': 'pending', 'error': ''})
            alarm = ct.wait_for(lambda: next((row for row in rig.alarms.view()['active']
                                              if row['key'] == 'hv_stop_unconfirmed' and not row['cleared']), None), 5,
                                'the alarm of the half that reported later')
            self.assertEqual((alarm['priority'], alarm['text'], asked), (1, T_UNCONFIRMED, [{}]))
        finally:
            del rig.rpi.command
        # The RPi cannot be asked at all (the stop went over its own connection; this one is gone): Emergency.
        rig.alarms.clear('hv_stop_unconfirmed')
        self.patch(devices, 'STOP_JOIN_SECONDS', 8.0)
        self.patch(devices, 'STOP_QUIET_JOIN_SECONDS', 8.0)

        def broken(cmd, **fields):
            raise RuntimeError('The command did not receive a reply (the connection was interrupted); please confirm the device status and try again.')

        rig.rpi.command = broken
        try:
            self.assertEqual(rig.devices.stop_everything('')['rpi']['result'], 'unconfirmed')
            self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 1)
        finally:
            del rig.rpi.command

    def test_a_rig_without_an_rpi_is_not_an_emergency_at_every_stop(self):
        self.patch(devices, 'HV_SETTLE_SECONDS', 0.3)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        rig = self.rig(rpi=False)
        self.stubs(rig)
        box = rig.devices
        sent, real = [], rpilink.plasmic_client.stop
        self.patch(rpilink.plasmic_client, 'stop', lambda *args, **more: (sent.append(1), real(*args, **more))[1])
        report = box.stop_everything('Wang Xiaoming')
        self.assertEqual((report['pump']['result'], report['rpi']['result']), ('confirmed', 'unreachable'))
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (3, 'high pressure', T_UNREACHABLE))
        self.assertIn(('Stop has been pressed: the pump has stopped; the high pressure stop command cannot be sent RPi', 'error'),
                      [(item['text'], item['level']) for item in rig.store.events(limit=40)])
        self.assertEqual(len(sent), 1)
        # all_off after a round without a step for the RPi that ran on the tick of its start dialog: there is
        # nobody to send a stop to. (Bare True: ticked with this request; tick(): kept from the start dialog.)
        for ack in (True, box.tick()):
            self.assertEqual(box.all_off(True, False, ack), {'pump': {'result': 'confirmed', 'error': ''},
                                                             'rpi': {'result': 'none', 'error': ''}})
        self.assertEqual(len(sent), 1)
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 3)
        # Without the tick it goes out as before ...
        for ack in (False, 'yes'):
            self.assertEqual(box.all_off(True, False, ack)['rpi']['result'], 'unreachable')
        self.assertEqual(len(sent), 3)
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 3)
        # ... and a Recipe with a step for the RPi is never spared it: that one is urgent.
        self.assertEqual(box.all_off(True, True, True)['rpi']['result'], 'unreachable')
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 1)
        # A discharge whose end was never read back: only a tick that is newer than that counts.
        early = box.tick()
        with box.hv_session():
            pass
        self.assertTrue(box.hv_active)
        self.assertEqual(box.all_off(True, False, early)['rpi']['result'], 'unreachable')
        self.assertEqual(box.all_off(True, False, True)['rpi']['result'], 'none')
        self.assertTrue(box.hv_active)   # (all_off lowers nothing: the next start with a tick does)
        box.permit_motion(True)
        self.assertFalse(box.hv_active)

    def test_a_stop_that_does_not_get_through_while_the_supply_is_known_to_be_off_is_a_lesser_alarm(self):
        rig = self.rig()
        self.stubs(rig)

        def unreachable(host, port, **more):
            raise OSError('Simulation: Unable to connect')

        self.patch(rpilink.plasmic_client, 'stop', unreachable)
        report = rig.devices.stop_everything('')
        self.assertEqual(report['rpi']['result'], 'unreachable')
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 3)
        # The alarm clears by itself once the high voltage is read as off again.
        rig.devices.permit_motion()
        self.assertTrue(rig.alarm('hv_stop_unconfirmed') is None or rig.alarm('hv_stop_unconfirmed')['cleared'])
        # An RPi that answers "not ok": not confirmed, and that is always urgent.
        self.patch(rpilink.plasmic_client, 'stop', lambda host, port, **more: {'ok': False, 'error': 'RS485 is executing other commands'})
        report = rig.devices.stop_everything('')
        self.assertEqual(report['rpi']['result'], 'unconfirmed')
        self.assertIn('RPi Unconfirmed stop:RS485 is executing other commands', report['rpi']['error'])
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text'], alarm['cleared']),
                         (1, 'The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately.', False))
        self.assertEqual(report['pump']['result'], 'confirmed')
        # A later stop that merely does not get through does not turn that alarm into the lesser one.
        self.patch(rpilink.plasmic_client, 'stop', unreachable)
        self.assertEqual(rig.devices.stop_everything('')['rpi']['result'], 'unreachable')
        alarm = rig.alarm('hv_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['text'], alarm['cleared']), (1, T_UNCONFIRMED, False))

    def test_a_half_that_does_not_answer_in_time_is_pending_and_reports_later(self):
        rig = self.rig()
        self.stubs(rig)
        self.patch(devices, 'STOP_JOIN_SECONDS', 0.3)
        self.patch(devices, 'STOP_QUIET_JOIN_SECONDS', 0.3)
        real = rpilink.plasmic_client.stop
        answer = {}

        def slow(host, port, **more):
            time.sleep(1.0)
            if answer.get('fail'):
                raise OSError('Simulation: Unable to connect')
            return real(host, port, **more)

        self.patch(rpilink.plasmic_client, 'stop', slow)
        started = time.perf_counter()
        report = rig.devices.stop_everything('')
        self.assertLess(time.perf_counter() - started, 0.9)
        self.assertEqual((report['pump']['result'], report['rpi']), ('confirmed', {'result': 'pending', 'error': ''}))
        self.assertIn('Stop pressed: Pump stopped; High pressure No reply yet', rig.texts())
        time.sleep(1.0)
        self.assertIsNone(rig.alarm('hv_stop_unconfirmed'))  # it did get through
        answer['fail'] = True
        self.assertEqual(rig.devices.stop_everything('')['rpi']['result'], 'pending')
        self.assertIsNone(rig.alarm('hv_stop_unconfirmed'))
        ct.wait_for(lambda: rig.alarm('hv_stop_unconfirmed'), 5, 'the alarm of the half that failed later')

    def test_a_supply_that_is_switched_off_gets_the_time_the_rpi_needs_to_say_so(self):
        """Measured on the rig (2026-10-07): with the supply off at its mains the RPi answers a stop after
        10.4 s, "could not confirm". That is the usual state between measurements and has to come back as
        `silent`: the RPi half is waited for longer than the pump half — unless something suggests that the
        high voltage is on. Then nobody waits past the short limit, and the half reports later."""
        self.assertGreater(devices.STOP_QUIET_JOIN_SECONDS, 2 * 10.4 - 2.4 + 2.0)   # a second press behind the first
        frame = (ct.ROOT / 'pc' / 'web' / 'js' / 'frame.js').read_text(encoding='utf-8')
        waits = re.search(r"api\.post\('/api/stop', \{\}, \{timeout: (\d+)\}\)", frame)
        self.assertLess(devices.STOP_QUIET_JOIN_SECONDS + 5, int(waits.group(1)) / 1000)   # the page waits longer
        self.patch(devices, 'HV_SETTLE_SECONDS', 0.3)                        # (the end of hv_session() below)
        self.patch(devices, 'HV_POLL_SECONDS', 0.05)
        rig = self.rig()
        self.stubs(rig)
        rig.supply_answers(False)
        self.patch(devices, 'STOP_JOIN_SECONDS', 0.3)
        self.patch(devices, 'STOP_QUIET_JOIN_SECONDS', 6.0)
        real = rpilink.plasmic_client.stop

        def slow(host, port, **more):
            time.sleep(1.0)
            return real(host, port, **more)

        self.patch(rpilink.plasmic_client, 'stop', slow)
        started = time.perf_counter()
        report = rig.devices.stop_everything('')
        took = time.perf_counter() - started
        self.assertEqual((report['pump']['result'], report['rpi']['result']), ('confirmed', 'silent'))
        self.assertTrue(1.0 <= took < 5.0, took)                             # as long as it took, not the limit
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 3)
        self.assertIn(('Stop pressed: pump stopped; high voltage power supply not responding (Nano Confirmed no output)', 'warn'),
                      [(item['text'], item['level']) for item in rig.store.events(limit=40)])
        # A discharge of ours is open: the short limit.
        rig.alarms.clear('hv_stop_unconfirmed')
        with rig.devices.hv_session():
            started = time.perf_counter()
            report = rig.devices.stop_everything('')
            self.assertLess(time.perf_counter() - started, 0.9)
            self.assertEqual(report['rpi'], {'result': 'pending', 'error': ''})
            alarm = ct.wait_for(lambda: next((row for row in rig.alarms.view()['active']
                                              if row['key'] == 'hv_stop_unconfirmed' and not row['cleared']), None), 5,
                                'the alarm of the half that reported later')
            self.assertEqual(alarm['priority'], 1)

    def test_stop_while_the_board_link_is_down(self):
        """B.12: a reconnect is tried inside the stop; not confirmed -> Emergency, telling the operator to cut the power."""
        self.pump_patched(RETRY_S=(0.2, 0.2))
        rig = self.rig()
        self.stubs(rig)
        plan = self.plan()
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 120, 'manual')
        attempts = rig.pump._attempts
        plan.fail_starts = 10 ** 6
        plan.pull_cable(rig.board)
        ct.wait_for(lambda: not rig.pump.connected, 8, 'the board to be lost')
        report = rig.devices.stop_everything('Wang Xiaoming')
        self.assertEqual(report['pump']['result'], 'unconfirmed')
        self.assertTrue(report['pump']['error'])
        self.assertEqual(report['rpi'], {'result': 'confirmed', 'error': ''})    # the other half is not held up
        self.assertGreater(rig.pump._attempts, attempts)                         # it did try to open the board again
        alarm = rig.alarm('pump_stop_unconfirmed')
        self.assertEqual((alarm['priority'], alarm['area']), (1, 'pump'))
        self.assertIn('Please turn off the power of the pump immediately with the physical switch', alarm['text'])
        self.assertIn(('Stop pressed: pump not acknowledged; high pressure readback OFF', 'error'),
                      [(item['text'], item['level']) for item in rig.store.events(limit=40)])
        # The board comes back: a connect begins with X, and then the stop is confirmed.
        plan.fail_starts = 0
        ct.wait_for(lambda: rig.pump.connected, 10, 'the board to come back')
        self.assertEqual(rig.devices.stop_everything('Wang Xiaoming')['pump'], {'result': 'confirmed', 'error': ''})
        self.assertTrue(rig.pumps_off())

    def test_all_off_sends_only_the_halves_that_are_needed(self):
        rig = self.rig()
        self.stubs(rig)
        stops, sent = rig.sim.controller.stop_requests, rig.tx().count('X')
        # A Recipe without pump and RPi steps, everything known to be at rest: nothing is sent.
        self.assertEqual(rig.devices.all_off(False, False),
                         {'pump': {'result': 'none', 'error': ''}, 'rpi': {'result': 'none', 'error': ''}})
        self.assertEqual((rig.sim.controller.stop_requests, rig.tx().count('X')), (stops, sent))
        self.assertEqual(devices.unconfirmed(rig.devices.all_off(False, False)), [])
        report = rig.devices.all_off(True, False)
        self.assertEqual((report['pump']['result'], report['rpi']['result']), ('confirmed', 'none'))
        self.assertEqual((rig.sim.controller.stop_requests, rig.tx().count('X')), (stops, sent + 1))
        report = rig.devices.all_off(False, True)
        self.assertEqual((report['pump']['result'], report['rpi']['result']), ('none', 'confirmed'))
        self.assertEqual((rig.sim.controller.stop_requests, rig.tx().count('X')), (stops + 1, sent + 1))
        # Something of the board still moves although the Recipe had no pump step: stopped all the same.
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        self.assertEqual(rig.devices.all_off(False, False)['pump']['result'], 'confirmed')
        self.assertTrue(rig.pumps_off())
        # The high voltage is not known to be off: the RPi half goes out although the Recipe had no RPi step.
        early = rig.devices.tick()
        rig.rpi_gone()
        report = rig.devices.all_off(False, False)
        self.assertEqual(report['rpi']['result'], 'unreachable')
        self.assertEqual(devices.unconfirmed(report), ['rpi'])
        self.assertEqual(rig.alarm('hv_stop_unconfirmed')['priority'], 3)    # the RPi's last word was "off"
        # ... unless the round ran on the operator's word that the supply is off (the tick of the start dialog).
        for ack in (True, early):
            self.assertEqual(rig.devices.all_off(False, False, ack)['rpi'], {'result': 'none', 'error': ''})
        self.assertEqual(devices.unconfirmed({'pump': {'result': 'pending'}, 'rpi': {'result': 'cancel_sent'}}), ['pump'])


class WatchTests(ct.Case):
    """A pump must not keep running once the high voltage is seen ON from elsewhere (thread `devices-watch`)."""

    ON = {'op': 'pump_on', 'flow_ml_min': 40, 'direction': 1, 'seconds': 60}

    @staticmethod
    def watches():
        return [thread for thread in threading.enumerate() if thread.name == 'devices-watch']

    @staticmethod
    def lines(rig, text=T_PUMP_WITH_HV):
        return [row['level'] for row in rig.store.events(limit=300) if row['text'] == text]

    def test_pumps_started_by_hand_are_stopped_when_the_supply_is_switched_on_elsewhere(self):
        rig = self.rig()
        self.assertEqual(len(self.watches()), 1)
        rig.manual.submit(dict(self.ON, motor=8))
        rig.manual.submit(dict(self.ON, motor=2))
        time.sleep(0.8)                  # with the high voltage off the watch leaves them alone
        self.assertEqual((rig.board.mstate['A'], rig.board.mstate['B']), ('move', 'move'))
        sent = len(rig.tx())
        supply(rig, True)                # not by this program
        ct.wait_for(lambda: (rig.board.mstate['A'], rig.board.mstate['B']) == ('idle', 'idle'), 3, 'the watch to stop the pumps')
        ct.wait_for(rig.pumps_off, 5, 'the valve to close')
        self.assertIn('X', rig.tx()[sent:])
        alarm = ct.wait_for(lambda: rig.alarm('pump_with_hv'), 3, 'the alarm')
        self.assertEqual((alarm['priority'], alarm['area'], alarm['text'], alarm['cleared']), (1, 'pump', T_PUMP_WITH_HV, False))
        ct.wait_for(lambda: self.lines(rig), 3, 'the line in the event log')
        self.assertEqual(self.lines(rig), ['error'])
        ct.wait_for(lambda: rig.devices.owner is None, 3, 'the equipment to be free again')
        # While it stays ON nothing starts, and the watch has nothing more to say.
        with self.assertRaises(Blocked):
            rig.manual.submit(dict(self.ON, motor=8))
        time.sleep(1.2)
        self.assertEqual(self.lines(rig), ['error'])
        self.assertEqual(rig.alarms.ack([alarm['id']]), 1)       # an `event` alarm: over once it has been seen
        self.assertIsNone(rig.alarm('pump_with_hv'))
        # Off, a pump again, on again: that is a new occurrence.
        supply(rig, False)
        rig.manual.submit(dict(self.ON, motor=8))
        self.assertEqual(rig.board.mstate['B'], 'move')
        supply(rig, True)
        ct.wait_for(rig.pumps_off, 3, 'the watch to stop the pump again')
        ct.wait_for(lambda: len(self.lines(rig)) == 2, 3, 'the second line in the event log')
        self.assertEqual(rig.alarm('pump_with_hv')['priority'], 1)
        supply(rig, False)

    def test_who_is_told_to_stop(self):
        self.patch(devices, 'WATCH_SECONDS', 0.1)
        rig = self.rig()
        box = rig.devices
        schedule_, by_hand = StubRunner(), StubManual()
        by_hand.running = False
        box.bind(schedule_, by_hand)
        self.assertEqual(len(self.watches()), 1)     # bound again: still one watch
        # A round holds the equipment and one of its pumps turns: the runner is told (no further slot, the round stops).
        box.acquire('round', 'r1', 'x')
        box.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'round')
        supply(rig, True)
        ct.wait_for(rig.pumps_off, 3, 'the watch to stop the pump')
        ct.wait_for(lambda: len(self.lines(rig)) == 1, 3, 'the line in the event log')
        self.assertEqual((schedule_.stops, by_hand.cancels), (1, 1))
        supply(rig, False)
        box.release('r1')
        # Nobody holds it (a pump that was started straight at the board): only the manual side and the board.
        box.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        supply(rig, True)
        ct.wait_for(rig.pumps_off, 3, 'the watch to stop the pump')
        ct.wait_for(lambda: len(self.lines(rig)) == 2, 3, 'the second line in the event log')
        self.assertEqual((schedule_.stops, by_hand.cancels), (1, 2))
        supply(rig, False)
        # A runner and a manual side that fail do not keep the pumps running.
        schedule_.request_stop = lambda: 1 / 0
        by_hand.cancel = lambda: 1 / 0
        box.acquire('round', 'r2', 'x')
        box.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'round')
        supply(rig, True)
        ct.wait_for(rig.pumps_off, 3, 'the watch to stop the pump')
        ct.wait_for(lambda: len(self.lines(rig)) == 3, 3, 'the third line in the event log')
        supply(rig, False)
        box.release('r2')

    def test_what_does_not_set_it_off(self):
        self.patch(devices, 'WATCH_SECONDS', 0.1)
        rig = self.rig()
        box = rig.devices
        box.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        # A high voltage that is merely not known (here: a status that is too old). Nothing new starts then,
        # and nothing that runs is stopped.
        limit = rpilink.HV_RECEIVED_SECONDS
        rpilink.HV_RECEIVED_SECONDS = -1.0
        try:
            self.assertFalse(rig.rpi.hv_view()['known'])
            time.sleep(0.6)
            self.assertTrue(box.hv_blocks_motion())
        finally:
            rpilink.HV_RECEIVED_SECONDS = limit
        self.assertEqual(rig.board.mstate['B'], 'move')
        # A discharge of ours: the pumps were seen at rest before it began. (The flag is put up by hand here,
        # with a pump turning, to show that the watch leaves that case to the session.)
        box.hv_active = True
        supply(rig, True)
        time.sleep(0.6)
        self.assertEqual(rig.board.mstate['B'], 'move')
        self.assertEqual((rig.alarm('pump_with_hv'), self.lines(rig)), (None, []))
        box.hv_active = False            # ... not ours any more
        ct.wait_for(rig.pumps_off, 3, 'the watch to stop the pump')
        ct.wait_for(lambda: len(self.lines(rig)) == 1, 3, 'the line in the event log')
        # The high voltage ON with nothing moving: that is no business of the watch.
        time.sleep(0.5)
        self.assertEqual(self.lines(rig), ['error'])
        supply(rig, False)
        # What counts as moving in the view of the board.
        moving = devices.Devices._board_moving
        for view in ({}, {'motors': [{'state': 'off'}, {'state': 'off'}], 'cleaning': False, 'op': None, 'syringe': {'busy': False}}):
            self.assertFalse(moving(view), view)
        for view in ({'motors': [{'state': 'off'}, {'state': 'run'}]}, {'motors': [{'state': 'weigh'}]}, {'cleaning': True},
                     {'op': {'kind': 'syringe_init'}}, {'syringe': {'busy': True}}):
            self.assertTrue(moving(view), view)

    def test_a_discharge_of_ours_that_has_just_ended_is_not_taken_for_a_strangers(self):
        """The watch reads the high voltage, then our flags, then the board. A discharge of ours can end right
        between the first two, and the next pump of the round can be turning already: so it looks twice."""
        self.patch(devices, 'WATCH_SECONDS', 0.1)
        rig = self.rig()
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')         # (the pump that follows the discharge)
        real, looks = rig.rpi.hv_view, []

        def hv_view():
            if threading.current_thread().name == 'devices-watch':
                looks.append(1)
                if len(looks) % 2:                   # what the watch read a moment before the discharge was over
                    return {'known': True, 'idle': False, 'reasons': ['is discharging']}
            return real()

        rig.rpi.hv_view = hv_view
        try:
            time.sleep(0.8)
        finally:
            del rig.rpi.hv_view
        self.assertGreater(len(looks), 4)
        self.assertEqual(rig.board.mstate['B'], 'move')
        self.assertEqual((rig.alarm('pump_with_hv'), self.lines(rig)), (None, []))
        rig.pump.stop_motor(8)

    def test_a_stop_that_is_not_confirmed_is_not_called_stopped(self):
        self.patch(devices, 'WATCH_SECONDS', 0.1)
        rig = self.rig()
        text = 'High voltage is displayed as ON,But the pump is still moving: the pump stop command was not acknowledged. Immediately turn off power to the pump using the physical switch.'
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        calls = []
        rig.pump.stop_all = lambda reason: calls.append(reason) or {'sent': True, 'confirmed': False, 'error': 'Simulation: No reply'}
        try:
            supply(rig, True)
            alarm = ct.wait_for(lambda: rig.alarm('pump_with_hv'), 3, 'the alarm')
            self.assertEqual((alarm['priority'], alarm['area'], alarm['text']), (1, 'pump', text))
            ct.wait_for(lambda: self.lines(rig, text), 3, 'the line in the event log')
            time.sleep(0.5)
            self.assertEqual(calls, ['hv'])          # once for this occurrence, although the pump still turns
            self.assertEqual((self.lines(rig, text), self.lines(rig)), (['error'], []))
        finally:
            del rig.pump.stop_all
        self.assertEqual(rig.board.mstate['B'], 'move')
        self.assertTrue(rig.pump.stop_all('test')['confirmed'])
        supply(rig, False)

    def test_a_look_that_fails_is_told_once_and_the_watch_goes_on(self):
        self.patch(devices, 'WATCH_SECONDS', 0.1)
        rig = self.rig()
        head = '“High voltage is ON ”The pump cannot move” has an error. There is no check this time:'
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')

        def broken():
            raise RuntimeError('Simulation: Program error')

        rig.pump.view = broken           # (the watch reads the board only while the high voltage is ON)
        try:
            supply(rig, True)
            ct.wait_for(lambda: [text for text in rig.texts() if text.startswith(head)], 3, 'the line in the event log')
            time.sleep(0.5)
        finally:
            del rig.pump.view
        told = [row for row in rig.store.events(limit=300) if row['text'].startswith(head)]
        self.assertEqual([(row['level'], row['text']) for row in told], [('error', head + "RuntimeError('Simulation: Program error')")])
        ct.wait_for(rig.pumps_off, 3, 'the watch to stop the pump after all')
        ct.wait_for(lambda: self.lines(rig) == ['error'], 3, 'the line in the event log')
        supply(rig, False)

    def test_close_ends_the_watch(self):
        rig = self.rig(pump='off', rpi=False, analyst=False)
        self.assertEqual(len(self.watches()), 1)
        rig.devices.bind(None, None)
        rig.devices.bind(rig.runner, rig.manual)
        self.assertEqual(len(self.watches()), 1)
        rig.devices.close()
        self.assertEqual(self.watches(), [])
        rig.devices.bind(rig.runner, rig.manual)     # after the end nothing is started again
        self.assertEqual(self.watches(), [])
        rig.devices.close()                          # twice does no harm


class StartCheckTests(ct.Case):
    """SPEC A.4: every row, with its code, text and fix."""

    PUMP = {'uses_pump': True}
    PLASMA = {'uses_rpi': True, 'uses_plasma': True}

    def codes(self, rig, facts, ack=False):
        return [row['code'] for row in rig.devices.start_problems(facts, ack)]

    def test_everything_ready(self):
        rig = self.rig()
        every = {'uses_pump': True, 'uses_rpi': True, 'uses_plasma': True, 'uses_scale': True, 'uses_weight': True}
        self.assertEqual(rig.devices.start_problems(every), [])
        self.assertEqual(rig.devices.start_problems({'steps': [{'action': 'dose_weight'}], 'uses_pump': True, 'uses_scale': True}), [])
        self.assertEqual(rig.devices.start_problems(None), [])
        self.assertEqual(rig.devices.view(), {'interlock': {'manual': {'ok': True, 'reasons': [], 'need_hv_ack': False,
                                                                       'hv_ack_text': ''},
                                                            'start': {'ok': True, 'reasons': []}}, 'owner': None})

    def test_the_pump_board_rows(self):
        off = self.rig(pump='off', analyst=False)
        self.assertEqual(off.devices.start_problems(self.PUMP),
                         [{'code': 'pump_disabled', 'text': 'Pump control board is set to not use', 'fix': 'Ask maintenance personnel to go to ”Repair › Connection” Change the pump control board back to use'}])
        self.assertEqual(off.devices.view()['interlock']['manual']['reasons'][0]['code'], 'pump_disabled')
        off.close()
        rig = self.rig()
        # A pump that still turns (started by hand a moment ago).
        rig.devices.acquire('manual', 'manual', 'Manual: Pump 7 · OUT')
        rig.devices.permit_motion()
        rig.pump.run(8, 40, 1, 60, 'manual')
        rig.devices.bind(None, None)                 # nobody gives the equipment back for it
        self.assertEqual(self.codes(rig, self.PUMP), ['manual_active', 'pump_moving'])
        self.assertEqual(rig.devices.start_problems(self.PLASMA)[-1],
                         {'code': 'pump_moving', 'text': 'The pump is still moving and cannot discharge. There are two options for ”', 'fix': ''})
        rig.pump.stop_motor(8)
        rig.devices.release('manual')
        self.assertEqual(self.codes(rig, self.PUMP), [])
        # The scale: no reading / not calibrated.
        rig.pump._conn.raw.send('W,STREAM,0')        # the weighing module goes silent (the simulator's own switch)
        time.sleep(1.3)
        self.assertEqual(rig.devices.start_problems({'uses_pump': True, 'uses_scale': True}),
                         [{'code': 'scale_offline', 'text': 'scale has no reading value', 'fix': ''}])
        self.assertEqual(self.codes(rig, self.PUMP), [])     # a Recipe that does not weigh does not care

    def test_a_scale_that_was_never_calibrated(self):
        rig = self.rig(calibration=False)
        weigh = {'uses_pump': True, 'uses_scale': True, 'uses_weight': True}
        self.assertEqual(rig.devices.start_problems(weigh),
                         [{'code': 'scale_uncalibrated', 'text': 'The scale has not been calibrated yet.', 'fix': 'Ask maintenance personnel to go to ”Repair › Pump and Scale” do a calibration'}])
        self.assertEqual(rig.devices.start_problems({'uses_pump': True, 'uses_scale': True}), [])    # scale_read alone may go on

    def test_a_board_that_was_never_chosen(self):
        """Real mode and no board found (this process could not open one, so it takes none either)."""
        rig = self.rig(pump='real', analyst=False)
        self.assertEqual(rig.pump.view()['link'], 'unset')
        self.assertEqual(rig.devices.start_problems(self.PUMP),
                         [{'code': 'pump_unset', 'text': 'Pump control board not found', 'fix': 'Please connect the USB line; after connecting, the program will connect itself'}])
        self.assertEqual(rig.devices.start_problems(self.PLASMA), [])    # measuring alone needs no board
        self.assertEqual(rig.devices.stop_everything('')['pump'], {'result': 'none', 'error': ''})

    def test_the_rpi_rows(self):
        rig = self.rig(speed=4)
        # A Nano fault: the RPi's own reasons, with the names of today's buttons.
        rig.rpi.command('sim_fault', fault='HEARTBEAT_LOST')
        fresh(rig)
        ct.wait_for(lambda: not rig.rpi.latest()['ready']['plasma'], 5, 'the fault to show')
        found = rig.devices.start_problems(self.PLASMA)
        self.assertEqual(found, [{'code': 'rpi_not_ready', 'text': 'The pre-discharge inspection failed:Nano fault lock HEARTBEAT_LOST',
                                  'fix': 'Ask maintenance personnel to go to ”Repair › Plasma Parameters” click ”Clear” Nano fault”, then click ”Sync to Nano”'}])
        self.assertEqual(devices.reasons_text(found + [{'code': 'rpi_not_ready', 'text': 'Pre-discharge inspection failed: Another thing', 'fix': ''}]),
                         'The pre-discharge inspection failed:Nano fault lock HEARTBEAT_LOST; Another thing')
        self.assertEqual(rig.devices.start_problems({'uses_rpi': True}), [])     # a step without discharge may go on
        rig.rpi.command('clear_fault')
        rig.rpi.command('prepare')
        fresh(rig)
        ct.wait_for(lambda: rig.rpi.latest()['ready']['plasma'], 5, 'the RPi to be ready again')
        self.assertEqual(rig.devices.start_problems(self.PLASMA), [])
        # The 1.3.0 program on the RPi says nothing about its measuring flow: every discharge is refused.
        flow = rig.rpi.hello.pop('flow')
        self.assertEqual(rig.devices.start_problems(self.PLASMA),
                         [{'code': 'rpi_old', 'text': 'RPi is still an old version: please double-click first Install.cmd renew RPi', 'fix': ''}])
        rig.rpi.hello['flow'] = flow
        # Busy with another job.
        job = rig.rpi.command('spectrum', claim=True, integration_ms=1000)['job']
        ct.wait_for(lambda: rig.rpi.latest()['busy'], 5, 'the capture to run')
        self.assertEqual(rig.devices.start_problems(self.PLASMA), [{'code': 'rpi_busy', 'text': 'RPi is performing other work', 'fix': ''}])
        self.assertEqual(rig.rpi.wait_job(job, limit_s=20), ('done', ''))
        # No RPi.
        rig.rpi_gone()
        self.assertEqual(rig.devices.start_problems({'uses_rpi': True}), [{'code': 'rpi_link', 'text': 'RPi Not connected', 'fix': ''}])
        self.assertEqual(self.codes(rig, dict(self.PUMP, **self.PLASMA)), ['hv_unknown', 'rpi_link'])
        self.assertEqual(self.codes(rig, dict(self.PUMP, **self.PLASMA), ack=True), ['rpi_link'])
        view = rig.devices.view()['interlock']
        self.assertEqual((view['manual']['need_hv_ack'], view['manual']['ok'], view['start']['ok']), (True, True, True))


if __name__ == '__main__':
    unittest.main(verbosity=2)
