"""Measure the discharge program once (rpi/tcpd/plasma_measure.py) . Design files of

The program code of the tool itself runs on the simulated RS485/Nano/HSPY/Trigger pin/on the spectrometer (tests/simulated_rig.py) ,don’t touch any
Devices and Networks. On-site verified plasma_job.run_plasma was also run on the same simulated test bench as a control.

    python tests/test_measure.py [category[.test name] ...]
"""
from __future__ import annotations

import contextlib
import io
import json
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'rpi' / 'tcpd')]

import simulated_rig  # noqa: E402
import plasma_measure  # noqa: E402

# A short job: pulse trains of 60 ms, three frames 0.4 s apart.
SMALL = {'Ontime': 5, 'Offtime': 5, 'Cycle': 6, 'Loopnum': 3,
         'Integration_Time': 100, 'Spectra_Interval': 400, 'Voltage': 30}
# The pulse settings the owner uses: 25 ms on, 5 ms off, 25 times = a pulse train of 0.75 s.
OWNER = dict(SMALL, Ontime=25, Offtime=5, Cycle=25)
OPEN, HEARTBEAT, ARM, START, STOP = simulated_rig.OPEN_SESSION, simulated_rig.HEARTBEAT, simulated_rig.ARM, simulated_rig.START, simulated_rig.STOP
TERM = 'The job received an interrupt signal'
UNCONFIRMED = 'Work stop not confirmed'
KILLED = 'The spectrum program did not respond to the stop signal and has been forced to terminate. After all'


def send_stop():
    """One SIGTERM to the thread the tool runs in, as the TCP server's stop sends one to the tool."""
    if hasattr(signal, 'pthread_kill'):
        signal.pthread_kill(threading.main_thread().ident, signal.SIGTERM)
    else:
        signal.raise_signal(signal.SIGTERM)


def stage_of(rig):
    """What the tool's stage file says right now ('' when there is none, or it is being written)."""
    try:
        return (rig.output_dir / 'stage').read_text()
    except OSError:
        return ''


def after_stage(name, seconds=0.0):
    """For run_tool(during=...): true ``seconds`` after the tool's stage file first said ``name``."""
    seen = []

    def check(rig, _session):
        if not seen and stage_of(rig) == name:
            seen.append(simulated_rig.clock())
        return bool(seen) and simulated_rig.clock() - seen[0] >= seconds
    return check


def once(condition, action):
    """For run_tool(during=...): do ``action(rig)`` the first time ``condition(rig)`` holds. Asks for no stop."""
    done = []

    def check(rig, _session):
        if not done and condition(rig):
            done.append(1)
            action(rig)
        return False
    return check


def after_cleanup_began(seconds=0.0):
    """For run_tool(during=...): true ``seconds`` after the tool began its OFF sequence."""
    seen = []

    def check(_rig, session):
        if not seen and session.verified.INTERRUPTS.cleaning:
            seen.append(simulated_rig.clock())
        return bool(seen) and simulated_rig.clock() - seen[0] >= seconds
    return check


class RigCase(unittest.TestCase):
    def setUp(self):
        # A SIGTERM that a test sends a moment too late must not end the test run itself.
        self.addCleanup(signal.signal, signal.SIGTERM, signal.signal(signal.SIGTERM, signal.SIG_IGN))

    def rig_for(self, parameters=SMALL, ramp=.4, speed=4, wishes=None, **options):
        """A pretend rig. ``speed`` shortens the spectrometer program's fixed waits (1 = as on the RPi);
        ``wishes`` is what should go wrong in that program (see simulated_spectrometer.py)."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rig = simulated_rig.Rig(self.temp.name, parameters, ramp_seconds=ramp, **options)
        self.addCleanup(self.rig.close)
        self.rig.set_spectrometer(speed=speed, **(wishes or {}))
        self.runs = 0
        return self.rig

    def run_tool(self, tool='measure', during=None, interrupts=None, **options):
        """One run. Returns (report, error text); after an error the report is the job.json it left,
        None when it left none.

        ``during(rig, session)`` runs in another thread while the tool works; what it returns True
        for is answered with one SIGTERM to this process, like the TCP server's stop. ``interrupts``
        makes this run's plasma_job.Interrupts (a test may want a special one).
        """
        rig = self.rig
        self.runs += 1
        rig.output_dir = Path(self.temp.name) / f'job{self.runs}'
        session = self.session = simulated_rig.Session(rig)
        verified = plasma_measure.verified = session.verified
        if interrupts is not None:
            verified.INTERRUPTS = interrupts(verified)
        self.interrupts = verified.INTERRUPTS  # this run's own; Session.close() puts the old one back
        seen = len(rig.nano.commands), len(rig.nano.trains), len(rig.hspy.writes), len(rig.signals)
        rig.nano.longest_gap = 0.0
        finished = threading.Event()
        # what each tool's own main() installs
        handler = plasma_measure.on_signal if tool == 'measure' else verified.INTERRUPTS.handler
        previous = signal.signal(signal.SIGTERM, handler)
        self.signalled_at = None

        def watch():
            while not finished.wait(.01):
                if during(rig, session):
                    self.signalled_at = simulated_rig.clock()
                    send_stop()
                    return
        watcher = threading.Thread(target=watch, daemon=True) if during else None
        self.began = simulated_rig.clock()
        report, error, self.exception = None, '', None
        try:
            if watcher:
                watcher.start()
            if tool == 'measure':
                report = plasma_measure.run_measure(*session.arguments(), clock=simulated_rig.clock, drain=session.drain,
                                                    **options)
            else:
                report = verified.run_plasma(*session.arguments(), drain=session.drain)
        except (Exception, KeyboardInterrupt) as exc:
            error, self.exception = str(exc), exc
            try:
                report = json.loads((rig.output_dir / 'job.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                report = None
        finally:
            self.ended = simulated_rig.clock()
            finished.set()
            if watcher:
                watcher.join(2)
            signal.signal(signal.SIGTERM, previous)
            session.close()
        self.report = report
        self.commands = [(op, result) for _when, op, result in rig.nano.commands[seen[0]:]]
        self.command_at = [(rig.nano.start + when, op, result) for when, op, result in rig.nano.commands[seen[0]:]]
        self.trains = rig.train_times()[seen[1]:]                             # on the wall clock, like 'opened_at'
        self.train_at = [rig.nano.start + when for when in rig.nano.trains[seen[1]:]]  # on simulated_rig.clock
        self.first_pulse = self.train_at[0] - self.began if self.train_at else None
        self.supply = [(register, value) for _when, register, value in rig.hspy.writes[seen[2]:]]
        self.supply_at = [rig.hspy.start + when for when, _r, _v in rig.hspy.writes[seen[2]:]]
        self.supply_times = [rig.hspy.start + rig.nano.wall + when for when, _r, _v in rig.hspy.writes[seen[2]:]]
        self.killed = [signum for _pid, signum in rig.signals[seen[3]:]]
        return report, error

    def ops(self):
        return [op for op, _result in self.commands]

    def hv_on_seconds(self):
        """From the first ON write to the last OFF write of this run; 0 when the supply never came on."""
        on = [when for when, write in zip(self.supply_at, self.supply) if write == (4, 1)]
        off = [when for when, write in zip(self.supply_at, self.supply) if write == (4, 0)]
        return off[-1] - on[0] if on else 0.0

    def late_ms(self, report):
        """By how much each frame after the first was started later than one period after the one
        before: what the tool itself controls (when the spectrometer program picks the word up, it
        does not)."""
        goes = [datetime.fromisoformat(shot['go_utc']) for shot in report['triggers']]
        period = report['loop_period_ms'] / 1000
        return [round(((later - earlier).total_seconds() - period) * 1000, 1) for earlier, later in zip(goes, goes[1:])]

    def assert_no_spectrometer_left(self):
        """Every spectrometer program that was started has ended by the time the tool returns."""
        alive = [pid for pid, child in self.rig.children.items() if child.poll() is None]
        self.assertEqual(alive, [], 'a spectrometer program is still running after the tool returned')

    def assert_everything_off(self, confirmed=True):
        """The rig itself is off and quiet, and the report says what the rig shows."""
        rig, report = self.rig, self.report
        self.assertEqual((rig.hspy.run_stop, rig.hspy.voltage()), (0, 0))
        self.assertFalse(rig.nano.core.outputHigh)
        self.assertFalse(rig.nano.core.active())
        self.assertFalse(rig.pin_level)
        self.assertEqual(self.supply[-1], (4, 0))
        self.assertEqual(self.commands[-1], (STOP, simulated_rig.STOPPED))
        self.assert_no_spectrometer_left()
        if confirmed and report is not None:
            final = report['final_power']
            self.assertEqual((final['run_stop_setting'], final['voltage_v'], final['current_a']), (0, 0, 0))
            self.assertEqual((report['cleanup_errors'], report['off_confirmed']), ([], True))
            self.assertIn('off_confirmed', report['timeline_s'])
            self.assertNotIn('off_not_confirmed', report['timeline_s'])
            self.assertTrue(report['spectrometer_ended'])

    def assert_stopped(self, report, error):
        """A stop request ended the run: the usual error, nothing delivered, everything off and confirmed."""
        self.assertIn(TERM, error)
        self.assertNotIn(UNCONFIRMED, error)
        self.assertFalse(report['valid'])
        self.assertNotIn('signal', report)
        self.assert_everything_off()


class WholeRunTests(RigCase):
    def test_order_of_the_steps_and_what_the_spectra_see(self):
        rig = self.rig_for(speed=1)
        off_seen = []  # what are the supply and the Nano doing when the stage file first says "off"?
        report, error = self.run_tool(during=once(lambda rig: stage_of(rig) == 'off', lambda rig: off_seen.append(
            (rig.hspy.run_stop, rig.nano.core.active()))))
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        self.assertEqual((report['flow'], report['cleanup_errors'], report['background_frames']), ('unified', [], 1))
        self.assertEqual(report['steps'], ['Nano config Read back', 'spectrometer is ready (background 1 open＋signal 3 open) ',
                                           'HSPY ON,voltage has arrived (no pulse yet)', 'The background spectrum begins to be exposed (high voltage is on, but there is no pulse yet)',
                                           'Nano WAIT_TRIGGER', 'Nano DONE, Spectrum completed'])
        marks = report['timeline_s']
        # the supply is on and at its voltage BEFORE the background frame is started; START comes
        # while that frame exposes, the first pulse after it
        order = ['spectrometer_started', 'nano_ready', 'armed', 'spectrometer_ready', 'hv_on_attempted', 'hspy_on',
                 'voltage_reached', 'background_started', 'start', 'first_pulse', 'nano_done', 'off_confirmed']
        self.assertEqual(sorted(marks, key=marks.__getitem__), order)
        # the report says separately: ON was sent, OFF was confirmed, the spectrometer program has ended
        self.assertEqual((report['hv_on_attempted'], report['off_confirmed'], report['spectrometer_ended'],
                          report['spectrometer_end']), (True, True, True, 'ended'))
        self.assertEqual((report['loop_period_ms'], report['exposure_lead_ms']), (400, 5.0))
        # the stage file says "off" only once the outputs have been switched off
        self.assertEqual(off_seen, [(0, False)])
        # the spectrometer is opened once and never holds a frame back itself; the supply is switched
        # on once, after the Nano is armed, and off once
        self.assertEqual(len(rig.started), 1)
        args = rig.started[0]
        self.assertEqual((args[args.index('--frame-interval-ms') + 1], args[args.index('--frames') + 1]), ('0', '4'))
        self.assertIn('--triggered', args)
        self.assertEqual(self.supply, [(4, 1), (4, 0)])
        self.assertEqual([op for op in self.ops() if op in (OPEN, ARM, START, STOP)], [OPEN, ARM, START, STOP])
        self.assertTrue(all(result in (simulated_rig.OK, simulated_rig.CONFIG_APPLIED, simulated_rig.STOPPED)
                            for _op, result in self.commands), self.commands)
        # one background frame: taken with the supply ON and at its voltage (the rig's supply needs
        # 0.4 s and is "there" within 5 V a little earlier), over before the first pulse, and it has
        # seen no pulse at all
        background = report['background']['frames']
        self.assertEqual([(frame['file'], frame['light_ms']) for frame in background], [('spectrum-1.csv', 0.0)])
        self.assertGreater(background[0]['opened_at'], self.supply_times[0] + .3)
        self.assertGreaterEqual(rig.voltage_seen_at(background[0]['opened_at']), 25)  # 'there' = within 5 V, as the verified tool counts it
        self.assertLess(background[0]['opened_at'] + .1, self.trains[0])
        # START (two status reads and a write: about half a second on this bus) is sent while the
        # frame exposes. This exposure lasts only 0.1 s, so the first pulse waits for START; with an
        # exposure longer than that it follows the frame at once (the "long background" test).
        self.assertLess(self.trains[0] - (background[0]['opened_at'] + .1), 1.0)
        # every signal exposure is open before its pulse train begins, by a few ms, and catches all of it
        frames = report['signal']['frames']
        self.assertEqual([frame['file'] for frame in frames], ['spectrum-1.csv', 'spectrum-2.csv', 'spectrum-3.csv'])
        self.assertEqual((len(self.trains), rig.nano.core.pulses), (3, 18))
        for frame, train in zip(frames, self.trains):
            self.assertGreater(train, frame['opened_at'])
            self.assertLess(train - frame['opened_at'], .03)
            self.assertAlmostEqual(frame['light_ms'], 6 * 5, delta=.3)
        self.assertEqual([shot['frame'] for shot in report['triggers']], [1, 2, 3])
        for shot in report['triggers']:
            self.assertTrue(2 <= shot['exposure_before_pulse_ms'] <= 30, shot)
            self.assertTrue(100 <= shot['exposure_ms'] <= 140, shot)
            self.assertLess(shot['taken_ms'], plasma_measure.GO_TAKEN_SECONDS * 1000)
        self.assertNotIn('exposure_after_trigger', report)
        self.assertLess(rig.nano.longest_gap, 1.3)  # the Nano's lease lasts 2 s
        self.assertLess(self.first_pulse, 5.0)      # what this order is for
        self.assert_everything_off()
        # the files lie where the TCP server's result loader (and the older tools) look for them
        self.assertEqual(sorted(path.name for path in (rig.output_dir / 'background').glob('*.csv')), ['spectrum-1.csv'])
        self.assertEqual(len(list((rig.output_dir / 'signal').glob('*.csv'))), 3)
        self.assertEqual(list((rig.output_dir / 'frames').glob('*.csv')), [])
        saved = json.loads((rig.output_dir / 'job.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['valid'])
        self.assertNotIn('sdk_returns', saved['signal'])  # kept once, in frames/spectrum.json
        self.assertTrue((rig.output_dir / 'frames' / 'spectrum.json').is_file())
        self.assertEqual((rig.output_dir / 'stage').read_text(), 'off')
        self.assertFalse((rig.output_dir / 'job.pid').exists())

    def test_the_verified_tool_on_the_same_rig_for_comparison(self):
        self.rig_for()
        _report, error = self.run_tool('measure')
        self.assertEqual(error, '')
        first = self.first_pulse
        classic, error = self.run_tool('classic')
        self.assertEqual(error, '')
        self.assertTrue(classic['valid'])
        self.assertEqual(classic['steps'], ['Nano config Read back', 'background spectrum completed', 'Signal spectrum SDK Prepared 3 frame',
                                            'HSPY ON, Nano WAIT_TRIGGER', 'Nano DONE, signal Spectrum completed'])
        # what this program is for: the verified order starts its first pulse much later ...
        self.assertGreater(self.first_pulse, first + 1.0)
        # ... and opens each exposure only after the pulse train has begun
        for frame, train in zip(classic['signal']['frames'], self.trains):
            self.assertGreater(frame['opened_at'], train + .005)
            self.assertLess(frame['light_ms'], 6 * 5 - 4)
        self.assert_everything_off(confirmed=False)  # that tool's report has no such fields

    def test_the_real_spectrometer_program_on_a_pretend_sdk(self):
        """rpi_tools/hardware_verify.py itself, unchanged: only the vendor library under it is pretended."""
        rig = self.rig_for(real_program=True)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        self.assertIn('real_hardware_verify.py', (rig.tools_dir / 'hardware_verify.py').read_text(encoding='utf-8'))
        self.assertEqual((report['background']['model'], report['signal']['pixels']), ('PRETEND', 1862))
        self.assertEqual([frame['captured_as'] for frame in report['background']['frames']], ['spectrum-1.csv'])
        self.assertEqual([frame['captured_as'] for frame in report['signal']['frames']],
                         ['spectrum-2.csv', 'spectrum-3.csv', 'spectrum-4.csv'])
        for shot in report['triggers']:
            self.assertTrue(1 <= shot['exposure_before_pulse_ms'] <= 30, shot)
            self.assertLess(shot['taken_ms'], 60)  # it looks for the word every 10 ms
        self.assertEqual((len(self.trains), self.supply), (3, [(4, 1), (4, 0)]))
        self.assertLess(max(self.late_ms(report)), 30)
        self.assert_everything_off()

    def test_a_second_run_keeps_the_session_the_nano_already_has(self):
        rig = self.rig_for()
        _first, error = self.run_tool()
        self.assertEqual(error, '')
        first_pulse = self.first_pulse
        time.sleep(2.2)  # the lease of the first run has expired by now
        session = simulated_rig.Session(rig)
        try:  # what the TCP server's status poll does between two runs
            self.assertEqual(session.nano.report_power(0)['power_snapshot'], 'OFF')
        finally:
            session.close()
        second, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertEqual(second['steps'][0], 'Nano config checked, same as the configuration file')
        self.assertNotIn(OPEN, self.ops())
        self.assertNotIn('config', self.ops())
        self.assertLess(self.first_pulse, first_pulse - .6)
        self.assertEqual(len(self.trains), 3)
        rig.parameters['Cycle'] = 4  # other parameters than the Nano holds: synchronized the verified way
        third, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertEqual(third['steps'][0], 'Nano config Read back')
        self.assertEqual(self.ops()[:2], [OPEN, 'config'])
        self.assertEqual(rig.nano.core.pulses, 12)
        self.assert_everything_off()

    def test_a_kept_session_that_is_gone_or_does_not_answer_when_it_is_needed(self):
        rig = self.rig_for()
        self.assertEqual(self.run_tool()[1], '')
        # The Nano restarts (a power dip) after its parameters were checked and before it is armed:
        # no session, no parameters.
        original = plasma_measure.Measurement.arm

        def arm(measurement):
            rig.nano.reset()
            return original(measurement)
        plasma_measure.Measurement.arm = arm
        try:
            report, error = self.run_tool()
        finally:
            plasma_measure.Measurement.arm = original
        self.assertEqual(error, '')
        self.assertEqual(rig.nano.resets, 1)
        self.assertEqual(report['steps'][0], 'Nano config checked, same as the configuration file')
        self.assertIn('Nano is no longer in the state just checked (it may have been restarted) and has been resynchronized.', report['steps'])
        self.assertEqual([op for op in self.ops() if op in (OPEN, ARM, START)], [OPEN, ARM, START])
        self.assertEqual((self.supply, len(self.trains)), ([(4, 1), (4, 0)], 3))
        self.assert_everything_off()
        # A reply that arrives damaged when the kept session's lease is renewed for ARM.
        original, beats = simulated_rig.load_verified().heartbeat, []

        def heartbeat(nano):
            if not beats:
                rig.bus.damage_nano_replies = 1
            beats.append(1)
            return original(nano)
        simulated_rig.load_verified().heartbeat = heartbeat
        self.addCleanup(setattr, simulated_rig.load_verified(), 'heartbeat', original)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(any(step.startswith('Nano has not been accepted session') for step in report['steps']), report['steps'])
        self.assertEqual([op for op in self.ops() if op in (OPEN, ARM, START)], [OPEN, ARM, START])
        self.assertEqual((self.supply, len(self.trains)), ([(4, 1), (4, 0)], 3))
        self.assert_everything_off()

    def test_a_nano_that_restarts_while_the_supply_is_on(self):
        """A power dip at the Nano with the high voltage on, while the background is taken or the
        voltage is waited for. Nothing is synchronised again with the supply on: the run ends, the
        supply goes off and is confirmed off, and the error says what it probably was."""
        for stage, started in (('background', True), ('ramp', False)):
            with self.subTest(during=stage):
                rig = self.rig_for(ramp=1.2)
                self.assertEqual(self.run_tool()[1], '')
                resets = rig.nano.resets
                report, error = self.run_tool(during=once(lambda rig, stage=stage: stage_of(rig) == stage,
                                                          lambda rig: rig.nano.reset()))
                self.assertEqual(rig.nano.resets, resets + 1)
                self.assertIn('may have been restarted at work', error)
                self.assertNotIn(UNCONFIRMED, error)
                self.assertFalse(report['valid'])
                self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 1), (4, 0)], [], True))
                self.assertEqual('background_started' in report['timeline_s'], started)
                self.assertNotIn(START, self.ops())
                self.assertNotIn(OPEN, self.ops())      # no new session with the supply on
                self.assertLess(self.hv_on_seconds(), 3.0)
                self.assert_everything_off()
                self.rig.close()

    def test_one_loop(self):
        rig = self.rig_for(dict(SMALL, Loopnum=1))
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        self.assertEqual((len(report['background']['frames']), len(report['signal']['frames'])), (1, 1))
        self.assertEqual((len(self.trains), rig.nano.core.pulses, self.supply), (1, 6, [(4, 1), (4, 0)]))
        self.assertAlmostEqual(report['signal']['frames'][0]['light_ms'], 30, delta=.3)
        self.assert_everything_off()

    def test_outputs_go_off_before_the_last_frame_is_waited_for(self):
        """Nano DONE -> OFF first -> only then collect: with a 2 s exposure the train (0.06 s) is long
        over while the last frame is still exposing."""
        rig = self.rig_for(dict(SMALL, Integration_Time=2000, Spectra_Interval=2000, Loopnum=1))
        rig.hspy.fall_seconds = .8  # and the output needs a moment to fall: the tool waits until it reads 0 V
        report, error = self.run_tool()
        self.assertEqual(error, '')
        last = report['signal']['frames'][-1]
        exposure_ends = last['opened_at'] + 2.0
        self.assertLess(self.supply_times[-1], exposure_ends - 1.0)   # OFF was written >= 1 s before the frame ended
        marks = report['timeline_s']
        self.assertLess(marks['nano_done'], marks['first_pulse'] + .4)
        self.assertLess(marks['off_confirmed'], marks['first_pulse'] + 1.9)
        stop_at = [when for when, op, _result in self.command_at if op == STOP][-1]
        self.assertLess(stop_at + rig.nano.wall, exposure_ends - 1.0)
        self.assertGreater(len(report['off_settle_samples']), 1)      # more than one look: it waited for 0 V
        self.assertGreater(report['off_settle_samples'][0]['voltage_v'], 0)
        self.assert_everything_off()

    def test_first_discharge_within_five_seconds_with_everyday_values(self):
        """What the owner asked for, with values of the example parameter file's kind (1000 ms
        integration), a supply that needs 1.5 s, and the spectrometer program at its real pace."""
        parameters = dict(OWNER, Loopnum=2, Integration_Time=1000, Spectra_Interval=1500)
        rig = self.rig_for(parameters, ramp=1.5, speed=1)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertLess(self.first_pulse, 5.0)
        background = report['background']['frames'][0]
        self.assertEqual(background['light_ms'], 0.0)
        self.assertGreaterEqual(rig.voltage_seen_at(background['opened_at']), 25)  # the supply was there before the frame opened
        for shot in report['triggers']:
            self.assertTrue(2 <= shot['exposure_before_pulse_ms'] <= 30, shot)
        self.assertLess(rig.nano.longest_gap, 1.3)
        self.assert_everything_off()

    def test_the_exposure_lead_can_be_set_for_a_sensor_that_starts_later(self):
        """The pretend sensor starts 2 ms after the SDK is called; nobody knows what the real one
        needs. With a sensor that needs 12 ms the usual 5 ms lead loses the beginning of every pulse
        train, and nothing in the report can show it; a longer lead (--lead-ms) catches it all again."""
        self.rig_for(wishes={'sensor_delay_ms': 12})
        report, error = self.run_tool()
        self.assertEqual(error, '')
        for frame, shot in zip(report['signal']['frames'], report['triggers']):
            self.assertLess(frame['light_ms'], 30 - 3)                 # several ms of the first pulse are missing
            self.assertGreater(shot['exposure_before_pulse_ms'], 2)   # yet the SDK was called before the trigger
        report, error = self.run_tool(lead=.02)
        self.assertEqual(error, '')
        self.assertEqual(report['exposure_lead_ms'], 20.0)
        for frame, shot in zip(report['signal']['frames'], report['triggers']):
            self.assertAlmostEqual(frame['light_ms'], 30, delta=.3)
            self.assertTrue(20 <= shot['lead_ms'] <= 40, shot)
        self.assert_everything_off()


class TimingTests(RigCase):
    def test_no_frame_waits_for_a_heartbeat_and_the_lease_stays_fresh(self):
        """Each frame is started one period after the one before, whatever the heartbeats need.
        The cases the review found: a pulse train of 0.6-0.75 s with an interval 0.25-0.45 s longer
        (every second loop came 0.19 s late), and intervals of 400-450 ms."""
        sizes = {}
        for change, loops in (
                (dict(OWNER, Spectra_Interval=1000), 5), (dict(OWNER, Spectra_Interval=1100), 4),
                (dict(OWNER, Spectra_Interval=1200), 4), ({'Cycle': 60, 'Spectra_Interval': 1000}, 4),
                ({'Spectra_Interval': 400}, 6), ({'Spectra_Interval': 450}, 6),
                ({'Spectra_Interval': 1300}, 3),                             # long waits between frames
                ({'Spectra_Interval': 700, 'Integration_Time': 300}, 4),
                ({'Cycle': 60, 'Spectra_Interval': 620}, 4)):                # the 0.6 s train sets the pace: 0.75 s
            with self.subTest(change=change):
                rig = self.rig_for(dict(dict(SMALL, **change), Loopnum=loops))
                report, error = self.run_tool()
                self.assertEqual(error, '')
                late = self.late_ms(report)
                self.assertEqual(len(late), loops - 1)
                self.assertTrue(-5 <= min(late) and max(late) <= 30, late)
                gaps = [later - earlier for earlier, later in zip(self.train_at, self.train_at[1:])]
                for gap in gaps:  # the pulse trains themselves: plus what the spectrometer program's pick-up varies
                    self.assertAlmostEqual(gap, report['loop_period_ms'] / 1000, delta=.06, msg=gaps)
                self.assertLess(rig.nano.longest_gap, 1.3)
                self.assertLess(report['heartbeats']['longest_gap_s'], 1.3)
                for frame, train in zip(report['signal']['frames'], self.trains):
                    self.assertGreater(train, frame['opened_at'])
                self.assertEqual(report['background']['frames'][0]['light_ms'], 0.0)
                self.assert_everything_off()
                sizes[loops] = (rig.output_dir / 'job.json').stat().st_size
                self.rig.close()
        # job.json of the longest job there can be (400 loops) stays far below what the TCP server reads
        per_loop = (sizes[6] - sizes[3]) / 3
        self.assertLess(sizes[3] + 397 * per_loop, 400000, (sizes, per_loop))

    def test_a_shorter_interval_than_the_bus_allows_runs_at_an_even_pace(self):
        """Trains of 10 ms asked for every 50 ms: one loop takes 0.25 s on this bus. The report says
        so, the loops come evenly (a heartbeat costs one loop in three about 45 ms), and one D12
        pulse starts one train although the train is over before the pulse is."""
        parameters = {'Ontime': 1, 'Offtime': 1, 'Cycle': 5, 'Loopnum': 24,
                      'Integration_Time': 20, 'Spectra_Interval': 50, 'Voltage': 30}
        rig = self.rig_for(parameters)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        self.assertEqual(report['loop_period_ms'], 250)
        self.assertEqual(report['steps'][0], 'Spectral interval 50 ms is shorter than possible, and the actual round 250 ms')
        self.assertEqual((len(self.trains), rig.nano.core.pulses), (24, 120))
        late = self.late_ms(report)
        self.assertTrue(-5 <= min(late) and max(late) <= 70, late)
        # 400 such loops (the most that are accepted) fit into the Nano's 120 s with room to spare
        self.assertLess(sum(late) / len(late), 25, late)
        self.assertLess(rig.nano.longest_gap, 1.6)
        self.assert_everything_off()

    def test_the_pace_holds_with_a_slower_adapter_and_a_nano_clock_that_is_off(self):
        """Bytes handed over 12 ms late (a USB adapter's latency timer) and a Nano that counts half
        a percent fast: the frames still come on time and the lease is not at risk."""
        for options in ({'latency': .012}, {'nano_rate': 1.005}, {'nano_rate': .995}):
            with self.subTest(options=options):
                rig = self.rig_for(dict(OWNER, Spectra_Interval=1000, Loopnum=4), **options)
                report, error = self.run_tool()
                self.assertEqual(error, '')
                late = self.late_ms(report)
                self.assertTrue(-5 <= min(late) and max(late) <= 30, late)
                self.assertLess(rig.nano.longest_gap, 1.3)
                self.assertEqual((len(self.trains), rig.nano.core.pulses), (4, 100))
                self.assert_everything_off()
                self.rig.close()

    def test_a_long_background_is_taken_with_the_supply_on_and_before_any_pulse(self):
        rig = self.rig_for(dict(SMALL, Integration_Time=2500, Spectra_Interval=2600, Loopnum=2))
        report, error = self.run_tool()
        self.assertEqual(error, '')
        background = report['background']['frames'][0]
        ends = background['opened_at'] + 2.5
        self.assertGreater(background['opened_at'], self.supply_times[0] + .3)   # the supply was on first ...
        self.assertGreaterEqual(rig.voltage_seen_at(background['opened_at']), 25)   # ... and at its voltage (within 5 V)
        self.assertGreater(self.trains[0], ends)                # no pulse before the frame is over
        self.assertEqual(background['light_ms'], 0.0)
        self.assertLess(self.trains[0] - ends, .3)              # and the first discharge right after it
        self.assertLess(rig.nano.longest_gap, 1.3)              # the lease is kept while it exposes
        self.assertIn(START, self.ops())
        self.assert_everything_off()

    def test_when_the_bus_may_stay_quiet_while_the_nano_waits(self):
        quiet = plasma_measure.quiet_until
        self.assertEqual((plasma_measure.QUIET_SECONDS, plasma_measure.BRIDGE_SECONDS, plasma_measure.HEARTBEAT_ROOM,
                          plasma_measure.READY_SLACK, plasma_measure.HEARTBEAT_SECONDS), (.9, 1.25, .3, .25, .5))
        self.assertAlmostEqual(quiet(10.0, 9.8, 15.0), 10.3)    # the frame is far away: the regular beat
        self.assertAlmostEqual(quiet(10.0, 9.8, 11.2), 10.55)   # renew as early as still lasts through 11.2 + slack
        self.assertAlmostEqual(quiet(10.0, 9.8, 10.4), 10.7)    # this quiet stretch already covers the moment
        self.assertAlmostEqual(quiet(10.0, 9.8, 10.6), 9.95)    # it does not: a heartbeat right now still fits before it
        # too late for a heartbeat before the moment: quiet through it for once, when the lease allows ...
        self.assertAlmostEqual(quiet(10.0, 9.2, 10.1), 10.35)
        self.assertAlmostEqual(quiet(10.0, 9.0, 10.1), 10.0)    # ... and at once when it does not
        self.assertAlmostEqual(quiet(10.5, 9.8, 10.1), 10.7)    # the moment has passed, the frame is late: as usual
        for last in (9.0, 9.3, 9.7):                             # never without a heartbeat for longer than the bridge
            for due in (9.5, 10.0, 10.2, 10.9, 11.4, 12.0, 30.0):
                self.assertLessEqual(quiet(10.0, last, due), last + plasma_measure.BRIDGE_SECONDS + 1e-9)

    def test_when_a_heartbeat_is_sent_while_a_pulse_train_runs(self):
        beat = plasma_measure.beat_in_train
        # the owner's train, 0.75 s every 1 s, right after the trigger at 0: the lease first, ...
        self.assertAlmostEqual(beat(.026, -.534, .766, 1.0), -.034)
        # ... then one that is over when the train ends, so the Nano can be asked at once
        self.assertAlmostEqual(beat(.30, .026, .766, 1.0), .466)
        self.assertIsNone(beat(.74, .466, .766, 1.0))            # and that one lasts through the next frame
        # a 60 ms train every 0.4 s: no room after it, but one sent at once is over before the frame
        self.assertAlmostEqual(beat(.026, -.55, .076, .4), .026)
        self.assertIsNone(beat(.026, -.6, .076, 1.3))            # room for one between the train and the frame
        self.assertAlmostEqual(beat(.026, -.8, .076, 1.3), .026)  # unless the lease cannot wait for that
        # every 0.25 s nothing fits: quiet through the frame while the lease allows, then it comes first
        self.assertIsNone(beat(.026, -.2, .046, .25))
        self.assertIsNone(beat(.026, -.45, .046, .25))
        self.assertAlmostEqual(beat(.026, -.8, .046, .25), .026)
        # a train too long for the heartbeat's own answer to show its end: a status read follows, both must fit
        self.assertIsNone(beat(.026, -.6, .276, .4))
        self.assertAlmostEqual(beat(.026, -.6, .276, .5), .026)
        # the last train: the regular beat, the last of them timed to end with the train, then only the lease
        self.assertAlmostEqual(beat(1.0, .8, 3.0, None), 1.3)
        self.assertAlmostEqual(beat(2.3, 2.2, 3.0, None), 2.7)
        self.assertAlmostEqual(beat(2.9, 2.7, 3.0, None), 3.6)
        # a train that runs on past the moment the next frame was due: only the lease counts then
        self.assertAlmostEqual(beat(1.5, .9, .766, 1.0), 1.8)
        self.assertAlmostEqual(beat(5.0, 4.6, .766, 1.0), 5.5)
        # Whatever the train and the next frame: never without a heartbeat for longer than the bridge.
        # (None means "not yet"; a time that has passed means "now".)
        bridge = plasma_measure.BRIDGE_SECONDS
        for last in (8.0, 8.5, 8.74, 8.76, 9.0, 9.5, 9.9):
            for train_end in (9.0, 9.9, 10.0, 10.05, 10.2, 10.6, 12.0, 40.0):
                for due in (None, 9.2, 9.8, 10.0, 10.1, 10.3, 10.8, 11.5, 13.0, 41.0):
                    when = beat(10.0, last, train_end, due)
                    if 10.0 - last >= bridge:
                        self.assertTrue(when is not None and when <= 10.0, (last, train_end, due, when))
                    elif when is not None:
                        self.assertLessEqual(when, last + bridge + 1e-9, (last, train_end, due, when))


class ParameterTests(RigCase):
    def test_what_one_loop_takes_and_what_is_refused(self):
        pace = plasma_measure.pace
        for change, expected in (
                ({}, (.06, .4)),
                ({'Spectra_Interval': 100}, (.06, .25)),                    # nothing is faster than 0.25 s
                ({'Cycle': 60, 'Spectra_Interval': 620}, (.6, .75)),        # a train needs 0.15 s after it
                (dict(OWNER, Spectra_Interval=1000), (.75, 1.0)),
                (dict(OWNER, Spectra_Interval=800), (.75, .9)),
                ({'Ontime': 1.1, 'Offtime': 2.2, 'Cycle': 100}, (.33, .48)),
                ({'Loopnum': 2, 'Spectra_Interval': 28000}, (.06, 28.0)),
                ({'Loopnum': 1, 'Spectra_Interval': 60000}, (.06, 60.0)),   # one loop waits for no next trigger
                (dict(OWNER, Cycle=1100, Loopnum=1), (33.0, 33.15)),
                ({'Loopnum': 4, 'Spectra_Interval': 25000}, (.06, 25.0)),
                ({'Loopnum': 100, 'Spectra_Interval': 1000, 'Integration_Time': 100}, (.06, 1.0)),
                ({'Loopnum': 400, 'Cycle': 3, 'Integration_Time': 20, 'Spectra_Interval': 50}, (.03, .25))):
            with self.subTest(change=change):
                train, period = pace(dict(SMALL, **change))
                self.assertAlmostEqual(train, expected[0], places=9)
                self.assertAlmostEqual(period, expected[1], places=9)
        for change, text in (
                ({'Loopnum': 2, 'Spectra_Interval': 28001}, 'cannot exceed between two rounds 28 seconds (Nano The upper limit of waiting for triggering is 30 seconds); currently each round 28.00 Second'),
                ({'Loopnum': 2, 'Spectra_Interval': 31000}, 'Currently every round 31.00 Second'),
                (dict(OWNER, Cycle=1100, Loopnum=2), 'Currently every round 33.15 Second'),  # a train of 33 s, twice
                ({'Loopnum': 4, 'Spectra_Interval': 26000}, 'at present 4 wheel × 26.00 Second = 104.0 Second'),
                ({'Loopnum': 101, 'Spectra_Interval': 1000}, 'Number of rounds × Each round time cannot exceed 100 Second'),
                ({'Loopnum': 401, 'Cycle': 3, 'Integration_Time': 20, 'Spectra_Interval': 50}, 'at present 401 wheel × 0.25 Second = 100.2 Second'),
                ({'Loopnum': 999, 'Cycle': 3, 'Integration_Time': 20, 'Spectra_Interval': 50}, 'at present 999 wheel × 0.25 Second = 249.8 Second'),
                ({'Loopnum': 1000, 'Cycle': 3, 'Integration_Time': 20, 'Spectra_Interval': 50}, 'at present 1000 wheel × 0.25 Second = 250.0 Second')):
            with self.subTest(change=change):
                with self.assertRaises(ValueError) as refused:
                    pace(dict(SMALL, **change))
                self.assertIn(text, str(refused.exception))
        # the spectrometer program's own limit, were the others ever loosened
        self.addCleanup(setattr, plasma_measure, 'LONGEST_SIGNAL_US', plasma_measure.LONGEST_SIGNAL_US)
        plasma_measure.LONGEST_SIGNAL_US = 10 ** 12
        with self.assertRaises(ValueError) as refused:
            pace(dict(SMALL, Loopnum=1000, Cycle=3))
        self.assertEqual(str(refused.exception), 'spectrometer program at most once 1000 open (background 1 open＋Most signals 999 open) ')
        self.assertEqual(pace(dict(SMALL, Loopnum=999, Cycle=3)), (.03, .4))

    def test_parameters_that_cannot_work_touch_nothing(self):
        for change in ({'Voltage': 701}, {'Voltage': 0}, {'Integration_Time': 10001, 'Spectra_Interval': 10001},
                       {'Spectra_Interval': 400.5}, {'Loopnum': 1001}, {'Ontime': .05},
                       {'Spectra_Interval': 31000, 'Loopnum': 2},                      # over the Nano's trigger wait
                       dict(OWNER, Cycle=1100, Loopnum=2),                             # a train longer than that
                       {'Loopnum': 401, 'Cycle': 3, 'Integration_Time': 20, 'Spectra_Interval': 50},
                       {'Loopnum': 1000, 'Cycle': 3, 'Integration_Time': 20, 'Spectra_Interval': 50}):
            with self.subTest(change=change):
                rig = self.rig_for(dict(SMALL, **change))
                before = rig.bus.exchanges
                report, _error = self.run_tool()
                self.assertIsInstance(self.exception, ValueError)
                self.assertIsNone(report)
                self.assertEqual((rig.bus.exchanges, rig.started, self.supply), (before, [], []))
                self.assertFalse(rig.output_dir.exists())
                self.rig.close()
        self.rig_for()
        self.run_tool(lead=.25)
        self.assertIsInstance(self.exception, ValueError)
        self.assertFalse(self.rig.output_dir.exists())

    def test_another_set_voltage_on_the_supply_is_written_before_anything_is_switched_on(self):
        """The person, 2026-10-07, with 1000 V on the supply's panel and 30 V in the program: “There should be no error message, just use
        RS485Change it and enter itvoltage”. Whatever the supply says (the verified tool writes nothing above 700 V or for a
        fraction), the voltage of the parameters is written while it is OFF, read back, and only then is it switched on."""
        for found in (1000, 50, 701, 0):
            with self.subTest(found=found):
                rig = self.rig_for()
                rig.hspy.set_voltage = found
                report, error = self.run_tool()
                self.assertEqual(error, '')
                self.assertEqual(self.supply, [(0, 30), (4, 1), (4, 0)])           # the voltage first, then ON, then OFF
                self.assertEqual((rig.hspy.set_voltage, len(self.trains)), (30, 3))
                self.assertEqual(report['set_voltage_written'], {'from_v': found, 'to_v': 30})
                self.assertIn('HSPY The set voltage was originally %d V,Already in OFF The login password of 30 V and read back' % found, report['steps'])
                marks = report['timeline_s']
                self.assertLess(marks['set_voltage_written'], marks['spectrometer_started'])
                self.assertLess(marks['set_voltage_written'], marks['hspy_on'])
                self.assert_everything_off()
                self.rig.close()
        # The same voltage: nothing is written (and the report says nothing about it).
        self.rig_for()
        report, error = self.run_tool()
        self.assertEqual((error, self.supply), ('', [(4, 1), (4, 0)]))
        self.assertNotIn('set_voltage_written', report)

    def test_a_supply_that_does_not_take_the_set_voltage_is_never_switched_on(self):
        rig = self.rig_for()
        rig.hspy.set_voltage = 1000
        original = rig.hspy.handle

        def deaf(frame):
            """Answers the write as if it had taken it, and keeps what its panel says."""
            before = rig.hspy.set_voltage
            reply = original(frame)
            if len(frame) == 8 and frame[1] == 6 and struct.unpack('>H', frame[2:4])[0] == 0:
                rig.hspy.set_voltage = before
            return reply
        rig.hspy.handle = deaf
        self.addCleanup(setattr, rig.hspy, 'handle', original)
        report, error = self.run_tool()
        self.assertIn('The high voltage power supply does not accept the written set voltage. The set voltage on the 30 V (Readback: Setting 1000', error)
        self.assertEqual(self.supply[0], (0, 30))
        self.assertNotIn((4, 1), self.supply)
        self.assertEqual((self.trains, rig.started, report['hv_on_attempted']), ([], [], False))
        self.assertNotIn(ARM, self.ops())
        self.assert_everything_off()

    def test_a_write_of_the_set_voltage_that_gets_no_answer_ends_the_run_with_everything_off(self):
        rig = self.rig_for()
        rig.hspy.set_voltage = 1000
        original = rig.hspy.handle

        def mute(frame):
            if len(frame) == 8 and frame[1] == 6 and struct.unpack('>H', frame[2:4])[0] == 0:
                rig.hspy.writes.append((simulated_rig.clock() - rig.hspy.start, 0, struct.unpack('>H', frame[4:6])[0]))
                return None                      # the request is on the wire; no echo comes back
            return original(frame)
        rig.hspy.handle = mute
        self.addCleanup(setattr, rig.hspy, 'handle', original)
        report, error = self.run_tool()
        self.assertIn('HSPY FC06 Reply/CRC Incorrect', error)
        self.assertEqual(self.supply[:2], [(0, 30), (4, 0)])       # the write, and an OFF said once more right after it
        self.assertNotIn((4, 1), self.supply)
        self.assertEqual((self.trains, rig.started, report['hv_on_attempted']), ([], [], False))
        self.assert_everything_off()

    def test_the_tool_that_only_writes_the_set_voltage(self):
        """write_set_voltage(): what `plasma_measure.py --set-voltage N --execute` does after its look at OFF."""
        rig = self.rig_for()
        rig.hspy.set_voltage = 1000
        session = simulated_rig.Session(rig)
        self.addCleanup(session.close)
        plasma_measure.verified = verified = session.verified
        before = len(rig.hspy.writes)
        reader = verified.off_check_reader(session.power, session.drain)
        found = verified.require_off(reader, session.nano)[0]
        self.assertEqual(found['set_voltage_v'], 1000)
        with self.assertRaises(RuntimeError):     # the verified tool's own way stops at what it finds
            verified.set_voltage_off(session.nano, reader, session.power_write, 30)
        self.assertEqual(len(rig.hspy.writes), before)
        after = plasma_measure.write_set_voltage(reader, session.power_write, 30)
        self.assertEqual((after['set_voltage_v'], after['run_stop_setting'], after['voltage_v']), (30, 0, 0))
        self.assertEqual([(register, value) for _when, register, value in rig.hspy.writes[before:]], [(0, 30)])
        for bad in (0, 701, 30.0, '30', True):
            with self.assertRaises(ValueError):
                plasma_measure.write_set_voltage(reader, session.power_write, bad)
        self.assertEqual(len(rig.hspy.writes), before + 1)
        self.assertEqual(rig.nano.core.pulses, 0)

    def test_refused_before_anything_when_the_supply_or_the_nano_is_not_as_expected(self):
        for what, change, text in (('already ON', lambda rig: setattr(rig.hspy, 'run_stop', 1), 'need HSPY OFF'),
                                   ('nano fault', lambda rig: rig.nano.core.fail(simulated_rig.JOB_TIMEOUT, 0), 'need HSPY OFF')):
            with self.subTest(what=what):
                rig = self.rig_for()
                change(rig)
                _report, error = self.run_tool()
                self.assertIn(text, error)
                self.assertEqual(self.trains, [])
                self.assertNotIn((4, 1), self.supply)
                self.assertNotIn(ARM, self.ops())
                self.assertEqual(rig.started, [])   # not even the spectrometer was started
                self.assertFalse(self.report['hv_on_attempted'])
                rig.hspy.run_stop = 0
                self.rig.close()


class StopTests(RigCase):
    """A stop request (the TCP server's SIGTERM) at every stage."""

    def test_a_stop_during_a_supply_read_before_on_is_not_lost(self):
        """The three HSPY reads made before anything is switched on (require_off, and two inside
        prepare) go through plasma_job.off_check_reader, which takes the interrupt for a failed read
        and reads again. The stop must still end the run, with the supply never switched on."""
        for which in (1, 2, 3):
            with self.subTest(read=which):
                rig = self.rig_for()
                original, reads = rig.hspy.handle, []

                def handle(frame, original=original, reads=reads, which=which):
                    if len(frame) == 8 and frame[1] == 3:
                        reads.append(1)
                        if len(reads) == which:
                            send_stop()   # in the middle of this exchange
                    return original(frame)
                rig.hspy.handle = handle
                report, error = self.run_tool()
                self.assertEqual(self.interrupts.received, [signal.SIGTERM])
                self.assertEqual(error, TERM + ' %d' % signal.SIGTERM)
                self.assertEqual(report['power_read_retries'], [{'attempt': 1, 'error': error}])
                self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 0)], [], False))
                self.assertNotIn(ARM, self.ops())
                # the run ended with that very read: nothing that follows it was begun
                self.assertEqual(len(rig.started), 0 if which == 1 else 1)
                self.assertNotIn('nano_ready', report['timeline_s'])
                self.assertNotIn(simulated_rig.POWER_REPORT, self.ops())
                self.assert_stopped(report, error)
                self.rig.close()

    def test_every_look_for_a_stop_that_was_only_written_down(self):
        """A stop that left nothing but its entry in INTERRUPTS.received (as after the swallowed
        interrupt above) is acted on at each step: before ARM, before HSPY ON, before the background
        frame, before START and before each trigger."""
        def written_down():   # nothing raised: what a swallowed interrupt leaves
            plasma_measure.verified.INTERRUPTS.received.append(signal.SIGTERM)

        def after(owner, name, when=lambda *arguments: True):
            """Write a stop down right after ``owner.name(...)`` has returned (when ``when(result, *arguments)``)."""
            original = getattr(owner, name)

            def wrapped(*arguments):
                result = original(*arguments)
                if when(result, *arguments):
                    written_down()
                return result
            setattr(owner, name, wrapped)
            return lambda: setattr(owner, name, original)
        Measurement = plasma_measure.Measurement
        steps = {
            'arm': (lambda: after(Measurement, 'prepare'), [(4, 0)]),
            'on': (lambda: after(Measurement, 'stage', lambda _result, _self, name: name == 'ramp'), [(4, 0)]),
            'background': (lambda: after(Measurement, 'mark', lambda _result, _self, name: name == 'voltage_reached'),
                           [(4, 1), (4, 0)]),
            'start': (lambda: after(Measurement, 'background'), [(4, 1), (4, 0)]),
            'trigger': (lambda: after(plasma_measure, 'watch', lambda result, *_rest: result), [(4, 1), (4, 0)])}
        for step, (patch, supply) in steps.items():
            with self.subTest(before=step):
                self.rig_for()
                undo = patch()
                try:
                    report, error = self.run_tool()
                finally:
                    undo()
                self.assertEqual((self.supply, self.trains), (supply, []))
                self.assertEqual(report['hv_on_attempted'], step in ('background', 'start', 'trigger'))
                self.assertEqual('hv_on_attempted' in report['timeline_s'], step in ('background', 'start', 'trigger'))
                self.assertEqual(ARM in self.ops(), step != 'arm')
                self.assertEqual(START in self.ops(), step == 'trigger')
                self.assertEqual('background_started' in report['timeline_s'], step in ('start', 'trigger'))
                self.assert_stopped(report, error)
                self.rig.close()

    def test_only_the_first_signal_raises(self):
        """The TCP server repeats its SIGTERM when the first seems lost. The first is handled the
        verified way; later ones are written down and nothing else, so that none can arrive as an
        exception in the instant before the OFF sequence has shielded itself."""
        verified = simulated_rig.load_verified()
        saved, plasma_measure.verified = plasma_measure.verified, verified
        self.addCleanup(setattr, plasma_measure, 'verified', saved)
        self.addCleanup(setattr, verified, 'INTERRUPTS', verified.INTERRUPTS)
        verified.INTERRUPTS = verified.Interrupts()
        with self.assertRaises(RuntimeError):
            plasma_measure.on_signal(15, None)
        plasma_measure.on_signal(15, None)
        plasma_measure.on_signal(1, None)
        self.assertEqual(verified.INTERRUPTS.received, [15, 15, 1])
        with self.assertRaises(RuntimeError):   # and every look for a stop finds it
            plasma_measure.stop_requested()
        verified.INTERRUPTS = verified.Interrupts()
        with verified.INTERRUPTS.bus():          # during an exchange the first is held back, as verified
            plasma_measure.on_signal(15, None)
            plasma_measure.on_signal(15, None)
            self.assertEqual((verified.INTERRUPTS.pending, verified.INTERRUPTS.received), (15, [15, 15]))
            verified.INTERRUPTS.cleaning = True
        plasma_measure.stop_requested()          # the OFF sequence itself is never interrupted by a look

    def test_a_signal_in_the_instant_before_the_off_sequence_shields_itself(self):
        """The run has ended by itself and the stop signal raises exactly where the OFF sequence is
        about to begin: the OFF sequence must run all the same."""
        def unlucky(verified):
            class Unlucky(verified.Interrupts):
                hits = 0

                @property
                def cleaning(self):
                    return self._cleaning

                @cleaning.setter
                def cleaning(self, value):
                    if value and not self.hits:
                        Unlucky.hits += 1
                        self.received.append(signal.SIGTERM)
                        raise RuntimeError(TERM + ' %d' % signal.SIGTERM)
                    self._cleaning = value
            return Unlucky()
        self.rig_for()
        report, error = self.run_tool(interrupts=unlucky)
        self.assertEqual(self.supply, [(4, 1), (4, 0)])
        self.assertTrue(self.interrupts.cleaning)
        self.assertIn(TERM, error)
        self.assertFalse(report['valid'])
        self.assert_everything_off()

    def test_a_stop_anywhere_before_arm(self):
        """Swept through the first checks and the Nano's synchronisation."""
        for delay in (.05, .5, 1.1):
            with self.subTest(delay=delay):
                self.rig_for()
                report, error = self.run_tool(during=after_stage('prepare', delay))
                self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 0)], [], False))
                self.assertNotIn(ARM, self.ops())
                self.assert_stopped(report, error)
                self.rig.close()

    def test_a_stop_while_the_armed_nano_waits_for_the_spectrometer(self):
        """The spectrometer needs 3 s to open. The Nano is ARMED meanwhile and its lease is kept; the
        supply is not on and never comes on, and the stop puts the Nano back to idle."""
        rig = self.rig_for(wishes={'slow_open': 3.0})
        armed = []

        def armed_for_a_while(rig, _session):
            if not armed and any(entry[1:] == (ARM, simulated_rig.OK) for entry in rig.nano.commands):
                armed.append(simulated_rig.clock())
            return bool(armed) and simulated_rig.clock() - armed[0] >= 1.2
        report, error = self.run_tool(during=armed_for_a_while)
        self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 0)], [], False))
        self.assertIn(ARM, self.ops())
        self.assertNotIn(START, self.ops())
        self.assertGreaterEqual(self.ops().count(HEARTBEAT), 3)   # the one before ARM and at least two while waiting
        self.assertLess(rig.nano.longest_gap, 1.3)
        self.assertNotIn('spectrometer_ready', report['timeline_s'])
        self.assertNotIn(simulated_rig.LEASE_EXPIRED, [result for _op, result in self.commands])
        self.assert_stopped(report, error)

    def test_a_stop_while_the_background_is_taken(self):
        """The supply is on by then (no pulse has been sent): it is switched off at once and confirmed.
        The spectrometer program is in the middle of an exposure: it can act on the stop only when
        the SDK call returns."""
        for integration, forced in ((1500, False), (4000, True)):
            with self.subTest(integration=integration):
                rig = self.rig_for(dict(SMALL, Integration_Time=integration, Spectra_Interval=integration, Loopnum=1))
                report, error = self.run_tool(during=after_stage('background', .3))
                self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 1), (4, 0)], [], True))
                self.assertIn('background_started', report['timeline_s'])
                self.assertLess(self.supply_at[-1] - self.signalled_at, 1.0)  # OFF written promptly
                self.assertLess(self.ended - self.signalled_at, 5.0)
                if forced:
                    # more than 2 s of exposure left: it is ended by force, and the report says so,
                    # apart from the high voltage, which was confirmed off
                    self.assertEqual(self.killed, [signal.SIGTERM, signal.SIGKILL])
                    self.assertEqual(error, TERM + ' %d; ' % signal.SIGTERM + KILLED)
                    self.assertEqual((report['spectrometer_ended'], report['spectrometer_end']), (True, KILLED))
                    self.assertEqual(report['steps'][-1], KILLED)
                else:
                    self.assertEqual(self.killed, [signal.SIGTERM])
                    self.assertEqual(error, TERM + ' %d' % signal.SIGTERM)
                    self.assertEqual(report['spectrometer_end'], 'ended')
                    spectrum = json.loads((rig.output_dir / 'frames' / 'spectrum.json').read_text(encoding='utf-8'))
                    self.assertEqual(spectrum['error'], 'Interrupted by signal 15')  # it closed the spectrometer itself
                self.assert_stopped(report, error)
                self.rig.close()

    def test_a_stop_anywhere_in_the_ramp(self):
        for delay in (.2, 1.5):
            with self.subTest(delay=delay):
                self.rig_for(ramp=2.5)
                report, error = self.run_tool(during=after_stage('ramp', delay))
                self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 1), (4, 0)], [], True))
                self.assertNotIn(START, self.ops())
                self.assertLess(self.supply_at[-1] - self.signalled_at, 1.0)  # OFF written promptly
                self.assert_stopped(report, error)
                self.rig.close()

    def test_a_stop_while_the_on_command_is_on_the_bus(self):
        """The ON was sent, the stop comes before its echo is checked: the report must still show
        that the supply was told to switch on."""
        rig = self.rig_for()
        original = rig.hspy.handle

        def handle(frame):
            if len(frame) == 8 and frame[1] == 6 and frame[2:6] == b'\x00\x04\x00\x01':
                send_stop()
            return original(frame)
        rig.hspy.handle = handle
        report, error = self.run_tool()
        self.assertEqual((self.supply, self.trains), ([(4, 1), (4, 0)], []))
        self.assertTrue(report['hv_on_attempted'])
        self.assertIn('hv_on_attempted', report['timeline_s'])
        self.assertNotIn('hspy_on', report['timeline_s'])
        self.assertNotIn(START, self.ops())
        self.assert_stopped(report, error)

    def test_a_stop_between_the_go_mark_and_the_trigger(self):
        rig = self.rig_for()
        original = plasma_measure.expose

        def expose(child, number, what, clock, sleep):
            result = original(child, number, what, clock, sleep)
            if number == 3:  # the second signal frame: its exposure has been taken up, D12 has not risen yet
                send_stop()
            return result
        plasma_measure.expose = expose
        self.addCleanup(setattr, plasma_measure, 'expose', original)
        report, error = self.run_tool()
        self.assertEqual(len(self.trains), 1)   # the second train was never triggered
        self.assertFalse(rig.pin_level)
        self.assert_stopped(report, error)

    def stop_at_the_second_d12_high(self, held_for):
        """A stop signal ``held_for`` seconds after D12 has risen for the second pulse train (the tool keeps
        it HIGH for 10 ms; the first thing the OFF sequence does is lower it)."""
        original = simulated_rig.PinValue.write_text
        highs = []

        def write_text(pin, text):
            original(pin, text)
            if text.strip() == '1':
                highs.append(1)
                if len(highs) == 2:
                    time.sleep(held_for)
                    send_stop()
        simulated_rig.PinValue.write_text = write_text
        self.addCleanup(setattr, simulated_rig.PinValue, 'write_text', original)

    def test_a_stop_while_d12_is_high(self):
        rig = self.rig_for(dict(SMALL, Cycle=100))  # trains of 1 s
        self.stop_at_the_second_d12_high(.002)       # the Nano answers D12 within 0.2 ms: it has seen this edge
        report, error = self.run_tool()
        self.assertFalse(rig.pin_level)              # D12 was HIGH when the signal came
        self.assertEqual(len(self.trains), 2)
        self.assertLess(rig.nano.core.pulses, 200)  # the second train was cut short
        self.assert_stopped(report, error)

    def test_a_stop_in_the_instant_d12_rises(self):
        """Signal and edge together: D12 is lowered again within microseconds, and whether the Nano saw the
        edge is a matter of chance (here: whether one of its 100 us ticks fell in between). The run ends
        stopped either way — with the second train cut short, or never begun."""
        rig = self.rig_for(dict(SMALL, Cycle=100))
        self.stop_at_the_second_d12_high(0)
        report, error = self.run_tool()
        self.assertFalse(rig.pin_level)
        self.assertIn(len(self.trains), (1, 2))
        self.assertLess(rig.nano.core.pulses, 200)
        self.assert_stopped(report, error)

    def test_a_stop_during_the_last_pulse_train(self):
        rig = self.rig_for(dict(SMALL, Cycle=60, Loopnum=2, Spectra_Interval=800))  # 0.6 s trains
        started = len(rig.nano.trains)
        report, error = self.run_tool(during=lambda rig, _session: len(rig.nano.trains) >= started + 2)
        self.assertLess(rig.nano.core.pulses, 120)
        self.assertLess(self.supply_at[-1] - self.signalled_at, 1.0)
        self.assert_stopped(report, error)

    def test_a_stop_anywhere_in_the_off_sequence_cannot_interrupt_it(self):
        """Swept through HSPY OFF / Nano STOP / the wait for 0 V / the wait for the last frame."""
        for delay in (0, .1, .25, .45, .8):
            with self.subTest(delay=delay):
                self.rig_for(dict(SMALL, Integration_Time=1000, Spectra_Interval=1000, Loopnum=1))
                report, error = self.run_tool(during=after_cleanup_began(delay))
                self.assertEqual(self.interrupts.received, [signal.SIGTERM])
                self.assertEqual(error, '')
                self.assertTrue(report['valid'])
                self.assertEqual(len(report['signal']['frames']), 1)
                self.assertEqual(self.supply, [(4, 1), (4, 0)])
                self.assert_everything_off()
                self.rig.close()

    def test_a_second_stop_signal_changes_nothing(self):
        """The server's repeated SIGTERM, at different distances from the first."""
        for apart in (.005, .05, .3):
            with self.subTest(apart=apart):
                rig = self.rig_for(dict(SMALL, Loopnum=6))
                started = len(rig.nano.trains)
                sent = []

                def twice(rig, _session, apart=apart, sent=sent, started=started):
                    if not sent and len(rig.nano.trains) >= started + 2:
                        sent.append(threading.Timer(apart, send_stop))
                        sent[0].start()
                        return True
                    return False
                report, error = self.run_tool(during=twice)
                sent[0].join(2)
                # two signals that close together may reach Python as one
                self.assertIn(self.interrupts.received, ([signal.SIGTERM] * 2, [signal.SIGTERM] * (1 if apart < .1 else 2)))
                self.assertLess(len(self.trains), 6)
                self.assert_stopped(report, error)
                self.rig.close()

    def test_a_stop_while_the_spectrometer_is_started_a_second_time(self):
        """The first start failed to open (as on site, 2 of 8 times). No spectrometer program may be
        left behind, whether the stop comes in the pause before the second start or while that one
        is coming up."""
        def pausing(rig, _session):
            return len(rig.started) == 1 and (rig.output_dir / 'frames-failed-1').exists()

        def coming_up(rig, _session):
            return len(rig.children) >= 2
        for when, speed in ((pausing, 4), (coming_up, 1)):
            with self.subTest(when=when.__name__):
                rig = self.rig_for(speed=speed, wishes={'open_failures': 1})
                report, error = self.run_tool(during=when)
                self.assertEqual((self.supply, self.trains), ([(4, 0)], []))
                self.assertLessEqual(len(rig.started), 2)
                self.assert_stopped(report, error)
                self.rig.close()

    def test_an_interrupt_that_is_no_ordinary_error(self):
        """KeyboardInterrupt after the last trigger: everything is switched off, the interrupt goes
        on up, and the report does not call the cut-off run valid."""
        self.rig_for()
        original = plasma_measure.fire

        def fire(trigger, child, number, *more):
            result = original(trigger, child, number, *more)
            if number == 4:
                raise KeyboardInterrupt()
            return result
        plasma_measure.fire = fire
        self.addCleanup(setattr, plasma_measure, 'fire', original)
        report, _error = self.run_tool()
        self.assertIsInstance(self.exception, KeyboardInterrupt)
        self.assertEqual((report['valid'], report['error']), (False, 'work was interrupted (KeyboardInterrupt) '))
        self.assertNotIn('signal', report)      # nothing was collected, although every frame had been asked for
        self.assertNotIn('nano_done', report)
        self.assertEqual(len(self.trains), 3)
        self.assertEqual(self.supply, [(4, 1), (4, 0)])
        self.assert_everything_off()


class SpectrometerTests(RigCase):
    def test_a_spectrometer_that_opens_only_at_the_second_try(self):
        rig = self.rig_for(wishes={'open_failures': 1})
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertIn('Spectrometer No. 1 times failed to open successfully ( Unable to start measurementUAI_SpectrometerOpen returned 6) ,1 Try again in seconds Discharge after', report['steps'])
        self.assertTrue((rig.output_dir / 'frames-failed-1' / 'spectrum.json').is_file())
        self.assertEqual((len(self.trains), self.supply), (3, [(4, 1), (4, 0)]))
        self.assertEqual(len(report['background']['frames']), 1)
        # the Nano was armed once and stayed armed through the pause and the second start: its lease was kept
        self.assertEqual([op for op in self.ops() if op in (OPEN, ARM, START)], [OPEN, ARM, START])
        self.assertLess(rig.nano.longest_gap, 1.3)
        self.assertNotIn(simulated_rig.LEASE_EXPIRED, [result for _op, result in self.commands])
        marks = report['timeline_s']
        self.assertLess(marks['armed'], marks['spectrometer_ready'])
        self.assertLessEqual(marks['spectrometer_ready'], marks['hv_on_attempted'])   # ON only after it was ready
        self.assert_everything_off()

    def test_a_spectrometer_that_does_not_open_never_gets_high_voltage(self):
        self.rig_for(wishes={'open_failures': 2})
        report, error = self.run_tool()
        self.assertEqual(error, 'The spectrometer did not turn on successfully (tried 2 times, high voltage is not turned on):UAI_SpectrometerOpen returned 6')
        self.assertFalse(report['valid'])
        self.assertEqual((self.supply, report['hv_on_attempted']), ([(4, 0)], False))  # only the OFF of the closing sequence
        # the Nano had been armed while the spectrometer was tried (armed gives no pulse); it was
        # never started and is idle again
        self.assertNotIn(START, self.ops())
        self.assertEqual(self.trains, [])
        self.assert_everything_off()

    def test_a_spectrometer_that_another_program_holds(self):
        rig = self.rig_for()
        rig.lock_held = True
        _report, error = self.run_tool()
        self.assertEqual(error, 'The spectrometer did not turn on successfully (tried 2 times, high voltage is not turned on): The spectrometer is being used by another program')
        self.assertEqual((self.supply, self.trains, len(rig.started)), ([(4, 0)], [], 2))
        self.assert_everything_off()

    def test_a_program_that_ends_in_the_middle(self):
        for wish, text, trains in (
                ({'die_after': 1}, 'spectrum program ends before all rounds are triggered', 0),  # after the background frame: the supply is on
                ({'die_after': 2}, 'spectrum program ends before all rounds are triggered', 1),  # after the first signal frame
                ({'bad_frame': 3}, 'The spectrum program ends before all rounds are triggered:Nonfinite, missing or constant spectrum data', 2)):
            with self.subTest(wish=wish):
                self.rig_for(wishes=wish)
                report, error = self.run_tool()
                self.assertIn(text, error)
                self.assertEqual(len(self.trains), trains)
                self.assertNotIn('signal', report)
                self.assertEqual(self.supply, [(4, 1), (4, 0)])
                self.assertLess(self.hv_on_seconds(), 4.0)
                self.assert_everything_off()
                self.rig.close()

    def test_a_program_that_has_ended_before_the_supply_is_switched_on(self):
        """It was ready for its first frame and is gone a moment later: no high voltage at all."""
        rig = self.rig_for()
        original = plasma_measure.Measurement.ramp

        def lose_the_program_then_ramp(measurement):
            rig.children[measurement.child.pid].kill()
            measurement.child.wait()
            return original(measurement)
        plasma_measure.Measurement.ramp = lose_the_program_then_ramp
        self.addCleanup(setattr, plasma_measure.Measurement, 'ramp', original)
        report, error = self.run_tool()
        self.assertIn('Spectrum program ends before turning on high pressure', error)
        self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 0)], [], False))
        self.assertNotIn(START, self.ops())
        self.assert_everything_off()

    def test_a_calibration_that_is_found_bad_after_the_last_frame(self):
        """The real program looks at the whole wavelength table only when the SDK is closed: the run
        is complete and off, and it is not delivered."""
        self.rig_for(wishes={'bad_calibration': True})
        report, error = self.run_tool()
        self.assertEqual(error, 'Spectrum acquisition failed:Invalid wavelength calibration')
        self.assertEqual((report['valid'], len(self.trains), self.supply), (False, 3, [(4, 1), (4, 0)]))
        self.assertNotIn('signal', report)
        self.assert_everything_off()

    def test_a_background_exposure_that_never_comes_back(self):
        """The SDK call for the one background frame blocks for good (hardware_verify.py names such
        a case). The supply is on and the Nano waits for D12: this must not last the Nano's 30 s."""
        rig = self.rig_for(wishes={'hang_frame': 1})
        report, error = self.run_tool()
        self.assertIn('The background spectrum was not taken within the deadline.', error)
        self.assertIn(KILLED, error)
        self.assertNotIn(UNCONFIRMED, error)
        self.assertEqual((self.supply, self.trains), ([(4, 1), (4, 0)], []))
        self.assertIn(START, self.ops())
        # the frame is given up 2 s after it should have ended (0.13 s after it was asked for); before
        # that the supply needed its 0.4 s
        self.assertLess(self.hv_on_seconds(), .4 + .13 + plasma_measure.BACKGROUND_GRACE_SECONDS + 1.0)
        self.assertEqual((rig.nano.core.state, rig.nano.core.fault), (simulated_rig.IDLE, simulated_rig.NO_FAULT))
        self.assertEqual(self.killed, [signal.SIGTERM, signal.SIGKILL])  # a program inside the SDK ends only by force
        self.assertEqual((report['off_confirmed'], report['spectrometer_ended'], report['spectrometer_end']),
                         (True, True, KILLED))
        self.assert_everything_off()

    def test_a_signal_exposure_that_never_comes_back(self):
        rig = self.rig_for(wishes={'hang_frame': 3})  # the frame of the second loop
        report, error = self.run_tool()
        self.assertIn('No. 2 wheel was not taken within the deadline.', error)
        self.assertEqual(len(self.trains), 2)
        second_go = self.train_at[1]
        self.assertLess(self.supply_at[-1] - second_go, .1 + plasma_measure.FRAME_GRACE_SECONDS + .5)
        self.assertEqual((rig.nano.core.state, rig.nano.core.fault), (simulated_rig.IDLE, simulated_rig.NO_FAULT))
        self.assertEqual(report['spectrometer_end'], KILLED)
        self.assert_everything_off()

    def test_an_exposure_that_comes_back_late_but_in_time(self):
        self.rig_for(wishes={'slow_frame': [3, 1.2]})
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        late = self.late_ms(report)
        self.assertLess(late[0], 30)
        self.assertTrue(800 <= late[1] <= 1300, late)  # the third frame had to wait for the second
        self.assertLess(self.rig.nano.longest_gap, 1.3)
        self.assert_everything_off()

    def test_a_second_start_that_hangs_is_ended_by_force(self):
        """The first start failed to open, the second never comes back from the SDK: it gets
        SIGTERM, then SIGKILL, and the error names the cause, not an unconfirmed stop."""
        rig = self.rig_for(wishes={'open_failures': 1, 'hang_open': 2})
        original = plasma_measure.spectrometer_ready
        plasma_measure.spectrometer_ready = lambda *arguments, **more: original(*arguments, seconds=1.5, **more)
        self.addCleanup(setattr, plasma_measure, 'spectrometer_ready', original)
        report, error = self.run_tool()
        self.assertIn('Spectrometer is not ready before turning on high voltage', error)
        self.assertIn(KILLED, error)
        self.assertNotIn(UNCONFIRMED, error)
        self.assertEqual((self.supply, self.trains, len(rig.started)), ([(4, 0)], [], 2))
        second = list(rig.children)[1]
        self.assertEqual([(pid, signum) for pid, signum in rig.signals if pid == second],
                         [(second, signal.SIGTERM), (second, signal.SIGKILL)])
        self.assertEqual((report['off_confirmed'], report['spectrometer_end']), (True, KILLED))
        self.assert_everything_off()

    def test_a_program_that_cannot_be_ended_is_reported_as_that(self):
        """Not as an unconfirmed stop: the supply was confirmed off. It still holds the spectrometer."""
        rig = self.rig_for(wishes={'hang_open': 1})
        original = plasma_measure.spectrometer_ready
        plasma_measure.spectrometer_ready = lambda *arguments, **more: original(*arguments, seconds=1.0, **more)
        self.addCleanup(setattr, plasma_measure, 'spectrometer_ready', original)
        self.addCleanup(setattr, plasma_measure, 'PROGRAM_KILL_SECONDS', plasma_measure.PROGRAM_KILL_SECONDS)
        plasma_measure.PROGRAM_KILL_SECONDS = .5
        rig.killpg = lambda pid, signum: rig.signals.append((pid, signum))  # no signal has any effect
        report, error = self.run_tool()
        self.assertIn('Spectrometer is not ready before turning on high voltage', error)
        self.assertIn('The spectrum program cannot be ended and the spectrometer is still occupied (SIGKILL after 0.5 seconds have not ended yet)', error)
        self.assertNotIn(UNCONFIRMED, error)
        self.assertEqual(self.killed, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual((report['off_confirmed'], report['spectrometer_ended'], report['cleanup_errors']), (True, False, []))
        self.assertEqual(self.supply, [(4, 0)])
        self.assertEqual([child.poll() for child in rig.children.values()], [None])  # Rig.close() ends it

    def test_a_go_mark_that_is_not_taken(self):
        for frame, what, trains in ((3, 'No. 2 wheel', 1), (1, 'background spectrum', 0)):
            with self.subTest(frame=frame):
                rig = self.rig_for(wishes={'slow_take': [frame, 3600]})
                report, error = self.run_tool()
                self.assertIn('Spectrum program is not available 500 ms Start within%sexposure' % what, error)
                self.assertNotIn(UNCONFIRMED, error)
                self.assertEqual(len(self.trains), trains)
                self.assertFalse(rig.pin_level)
                self.assertFalse((rig.output_dir / 'frames' / ('frame-%d.go' % frame)).exists())  # taken back
                # either way the supply was on by then, and it is switched off within the half second
                self.assertEqual((self.supply, report['hv_on_attempted']), ([(4, 1), (4, 0)], True))
                if not trains:
                    self.assertNotIn(START, self.ops())
                    self.assertLess(self.hv_on_seconds(), .4 + plasma_measure.GO_TAKEN_SECONDS + 1.0)
                self.assert_everything_off()
                self.rig.close()

    def test_a_go_mark_that_is_taken_late_but_in_time(self):
        """The program is stalled for 0.4 s when the word for the second loop is given: the trigger
        waits for it, no heartbeat goes between the two, and the lease holds."""
        rig = self.rig_for(dict(SMALL, Spectra_Interval=100), wishes={'slow_take': [3, .4]})
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        taken = [shot['taken_ms'] for shot in report['triggers']]
        self.assertTrue(150 <= taken[1] <= 480, taken)
        self.assertLess(max(taken[0], taken[2]), 60)
        for frame in report['signal']['frames']:
            self.assertAlmostEqual(frame['light_ms'], 30, delta=.3)
        self.assertLess(rig.nano.longest_gap, 1.6)
        self.assert_everything_off()

    def test_exposures_asked_for_after_the_trigger_are_named(self):
        with tempfile.TemporaryDirectory() as folder:
            lines = [{'utc': '2026-10-06T01:00:00.010000+00:00', 'stage': 'sdk_call_start', 'function': 'UAI_SpectrometerDataOneshot', 'frame': 2},
                     {'utc': '2026-10-06T01:00:00.120000+00:00', 'stage': 'sdk_call_done', 'function': 'UAI_SpectrometerDataOneshot', 'frame': 2},
                     {'utc': '2026-10-06T01:00:01.020000+00:00', 'stage': 'sdk_call_start', 'function': 'UAI_SpectrometerDataOneshot', 'frame': 3}]
            (Path(folder) / 'progress.jsonl').write_text('\n'.join(json.dumps(line) for line in lines), encoding='utf-8')
            triggers = [{'frame': 1, 'edge_utc': '2026-10-06T01:00:00.016000+00:00'},
                        {'frame': 2, 'edge_utc': '2026-10-06T01:00:01.016000+00:00'},
                        {'frame': 3, 'edge_utc': '2026-10-06T01:00:02.016000+00:00'}]
            self.assertEqual(plasma_measure.add_exposure_times(Path(folder), triggers, 1), [2])
            self.assertEqual([shot.get('exposure_before_pulse_ms') for shot in triggers], [6.0, -4.0, None])
            self.assertEqual(triggers[0]['exposure_ms'], 110.0)


class SupplyAndNanoTests(RigCase):
    def test_a_voltage_that_never_arrives(self):
        self.rig_for(reach=.5)
        _report, error = self.run_tool()
        self.assertIn('Not reaching the set voltage 30 V', error)
        self.assertEqual((self.supply, self.trains), ([(4, 1), (4, 0)], []))
        self.assertNotIn(START, self.ops())
        self.assert_everything_off()

    def test_a_supply_that_does_not_switch_off(self):
        """It acknowledges OFF and stays ON: the report must not say "off confirmed" anywhere."""
        rig = self.rig_for()
        original = rig.hspy.handle

        def handle(frame):
            if len(frame) == 8 and frame[1] == 6 and frame[2:6] == b'\x00\x04\x00\x00':
                rig.hspy.writes.append((simulated_rig.clock() - rig.hspy.start, 4, 0))
                return bytes(frame)
            return original(frame)
        rig.hspy.handle = handle
        report, error = self.run_tool()
        self.assertIn(UNCONFIRMED, error)
        self.assertIn('HSPY OFF (Try again 5 times)', error)
        self.assertIn('final OFF/D13 LOW Readback failed:HSPY Run/Stop=1', error)
        self.assertEqual((report['valid'], report['off_confirmed']), (False, False))
        self.assertIn('off_not_confirmed', report['timeline_s'])
        self.assertNotIn('off_confirmed', report['timeline_s'])
        self.assertEqual(report['final_power']['run_stop_setting'], 1)
        self.assertEqual(self.supply.count((4, 0)), 5)
        self.assertEqual(self.commands[-1], (STOP, simulated_rig.STOPPED))   # the Nano is stopped all the same
        self.assertFalse(rig.nano.core.active())
        self.assertEqual(len(report['signal']['frames']), 3)            # the frames are kept all the same
        self.assertEqual(rig.hspy.run_stop, 1)
        self.assert_no_spectrometer_left()
        rig.hspy.run_stop = 0

    def silent_once(self, rig, wanted):
        """The supply does not answer the status read ``wanted(run_stop, number of that read)`` picks."""
        original, reads = rig.hspy.handle, []

        def handle(frame):
            if len(frame) == 8 and frame[1] == 3:
                reads.append(rig.hspy.run_stop)
                if wanted(rig.hspy.run_stop, reads.count(rig.hspy.run_stop)):
                    return None
            return original(frame)
        rig.hspy.handle = handle

    def test_a_missed_reply_before_on_is_asked_for_again(self):
        rig = self.rig_for()
        self.silent_once(rig, lambda on, number: not on and number == 1)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertEqual(len(report['power_read_retries']), 1)
        self.assertEqual((len(self.trains), self.supply), (3, [(4, 1), (4, 0)]))
        self.assert_everything_off()

    def test_a_missed_reply_during_the_ramp_ends_the_run_with_everything_off(self):
        rig = self.rig_for(ramp=1.5)
        self.silent_once(rig, lambda on, number: on and number == 2)
        report, error = self.run_tool()
        self.assertNotEqual(error, '')
        self.assertEqual((self.supply, self.trains, report['hv_on_attempted']), ([(4, 1), (4, 0)], [], True))
        self.assertNotIn(START, self.ops())
        self.assert_everything_off()

    def reads_while_on(self, rig, change):
        """The supply's status replies while it is ON, with their five words changed by `change(words)`."""
        original = rig.hspy.handle

        def handle(frame):
            reply = original(frame)
            if reply and rig.hspy.run_stop and len(frame) == 8 and frame[1] == 3:
                words = list(struct.unpack('>5H', reply[3:13]))
                change(words)
                reply = simulated_rig.framed(reply[:3] + struct.pack('>5H', *words))
            return reply
        rig.hspy.handle = handle
        self.addCleanup(setattr, rig.hspy, 'handle', original)

    def test_the_current_during_the_ramp_is_not_judged(self):
        """The person's decision of 2026-10-07: “There shouldn’t be0.1Arestrictions”. The supply limits its current to what its panel
        is set to; a reading of 0.12 A while the voltage rises (it used to end the run) or of 0.8 A is no reason to stop."""
        for raw in (1200, 8000):                 # 0.12 A, 0.8 A
            with self.subTest(raw=raw):
                rig = self.rig_for(ramp=1.5)

                def more(words, raw=raw):
                    words[3] = raw
                self.reads_while_on(rig, more)
                _report, error = self.run_tool()
                self.assertEqual(error, '')
                self.assertEqual((len(self.trains), self.supply), (3, [(4, 1), (4, 0)]))

    def test_the_current_limit_set_on_the_supply_is_not_judged(self):
        rig = self.rig_for()
        rig.hspy.set_current = 9990              # 0.999 A: what the panel of the rig showed on 2026-09-20
        _report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertEqual((len(self.trains), self.supply), (3, [(4, 1), (4, 0)]))
        self.assert_everything_off()

    def test_a_wrong_read_back_during_the_ramp_ends_the_run_with_everything_off(self):
        """What the ramp still judges: the supply must read back ON and the voltage that was asked for."""
        rig = self.rig_for(ramp=1.5)

        def other_voltage(words):
            words[0] += 5                        # the set voltage is not the one of parameter.json
        self.reads_while_on(rig, other_voltage)
        _report, error = self.run_tool()
        self.assertEqual(error, 'HSPY ON Incorrect readback')
        self.assertEqual((self.supply, self.trains), ([(4, 1), (4, 0)], []))
        self.assertNotIn(START, self.ops())
        self.assertLess(self.hv_on_seconds(), 1.0)
        self.assert_everything_off()

    def test_the_lease_is_renewed_before_start_when_the_last_heartbeat_is_old(self):
        """Up to half a second can go by while the background's go mark is taken, on top of what the
        ramp left; START and the report to the LCD take another half second, and none of that renews
        the Nano's lease (2 s). So a heartbeat goes first whenever the last one is not fresh."""
        self.rig_for()
        original = plasma_measure.Measurement.background

        def background(measurement):
            original(measurement)
            measurement.last_heartbeat -= 1.2   # as if that go mark had been taken this slowly
        plasma_measure.Measurement.background = background
        self.addCleanup(setattr, plasma_measure.Measurement, 'background', original)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        exposing = self.began + report['timeline_s']['background_started']
        start_at = next(when for when, op, _result in self.command_at if op == START)
        beats = [when for when, op, _result in self.command_at if op == HEARTBEAT and exposing - .05 < when < start_at]
        self.assertTrue(beats, 'no heartbeat between the background frame and START')
        self.assertEqual(len(self.trains), 3)
        self.assert_everything_off()

    def test_the_lease_stays_fresh_through_a_slow_ramp(self):
        rig = self.rig_for(ramp=2.9)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        self.assertLess(rig.nano.longest_gap, 1.3)
        self.assertEqual(len(self.trains), 3)
        self.assertNotIn(simulated_rig.LEASE_EXPIRED, [result for _op, result in self.commands])
        self.assert_everything_off()

    def test_a_nano_fault_in_the_middle(self):
        rig = self.rig_for(dict(SMALL, Loopnum=5))
        started = len(rig.nano.trains)
        injected = threading.Event()

        def fault():
            while len(rig.nano.trains) < started + 2:
                time.sleep(.005)
            with rig.nano.lock:
                rig.nano.core.fail(simulated_rig.HEARTBEAT_LOST, rig.nano.micros())
            injected.set()
        threading.Thread(target=fault, daemon=True).start()
        report, error = self.run_tool()
        self.assertTrue(injected.is_set())
        self.assertEqual(error, 'Nano Fault: HEARTBEAT_LOST')
        self.assertEqual((self.supply, report['cleanup_errors'], report['off_confirmed']), ([(4, 1), (4, 0)], [], True))
        self.assertEqual(rig.nano.core.state, simulated_rig.FAULT)
        self.assertEqual((rig.hspy.run_stop, rig.hspy.voltage(), rig.nano.core.outputHigh), (0, 0, False))
        self.assert_no_spectrometer_left()

    def test_a_nano_that_restarts_in_the_middle(self):
        """A power dip: the Nano is idle again with no job. The tool must not wait for it."""
        rig = self.rig_for(dict(SMALL, Loopnum=5))
        started = len(rig.nano.trains)
        _report, error = self.run_tool(during=once(lambda rig: len(rig.nano.trains) >= started + 2,
                                                   lambda rig: rig.nano.reset()))
        self.assertNotEqual(error, '')
        self.assertNotIn(TERM, error)
        self.assertEqual(self.supply, [(4, 1), (4, 0)])
        self.assertLess(len(self.trains), 5)
        self.assertLess(self.supply_at[-1] - self.train_at[1], 2.0)
        self.assert_everything_off()

    def test_a_nano_that_is_idle_in_the_middle_of_the_job(self):
        """It answers, its session is good, but it is no longer waiting or running (as after a STOP
        from elsewhere): no trigger can do anything, and the supply must not stay on for it."""
        rig = self.rig_for(dict(SMALL, Loopnum=5))
        started = len(rig.nano.trains)

        def stop(rig):
            with rig.nano.lock:
                rig.nano.core.stop(rig.nano.micros())
        _report, error = self.run_tool(during=once(lambda rig: len(rig.nano.trains) >= started + 2, stop))
        self.assertEqual(error, 'Nano is not in the proper state for this job (IDLE) ')
        self.assertEqual((self.supply, len(self.trains)), ([(4, 1), (4, 0)], 2))
        self.assertLess(self.supply_at[-1] - self.train_at[1], 1.0)
        self.assert_everything_off()

    def test_a_nano_that_says_done_with_pulses_missing(self):
        """DONE is taken at its word only when every pulse and every loop was counted. Here the Nano
        gives 3 pulses to a train instead of 6: the spectra must not be kept as a full measurement."""
        def fewer(rig):
            with rig.nano.lock:
                rig.nano.core.config.cycles = 3
        rig = self.rig_for()
        report, error = self.run_tool(during=once(lambda rig: stage_of(rig) == 'background', fewer))
        self.assertIn('Nano DONE pulse/Loop readback is incomplete', error)
        self.assertFalse(report['valid'])
        self.assertNotIn('signal', report)
        self.assertEqual(len(self.trains), 3)
        self.assertLess(rig.nano.core.pulses, 18)
        self.assertEqual(self.supply, [(4, 1), (4, 0)])
        self.assert_everything_off()

    def test_the_time_limits_of_the_whole_job(self):
        """A pulse train that does not end when it should (here: the Nano runs 10000 pulses instead
        of 6). Each frame was in time, the Nano answers and reports no fault: only the limits on the
        whole job end this, the one from the loops' own length and the one just beyond the Nano's 120 s."""
        def endless(rig):
            with rig.nano.lock:
                rig.nano.core.config.cycles = 10000
        for name, value, text in (('SIGNAL_SPARE_SECONDS', 1.0, 'Spectrum acquisition exceeds the period set by the number of rounds.'),
                                  ('NANO_JOB_SECONDS', 2.5, 'Nano works over 2.5 Second')):
            with self.subTest(limit=name):
                rig = self.rig_for()
                usual = getattr(plasma_measure, name)
                self.addCleanup(setattr, plasma_measure, name, usual)
                setattr(plasma_measure, name, value)
                _report, error = self.run_tool(during=once(lambda rig: stage_of(rig) == 'background', endless))
                setattr(plasma_measure, name, usual)
                self.assertIn(text, error)
                self.assertEqual((self.supply, len(self.trains)), ([(4, 1), (4, 0)], 1))
                self.assertLess(self.supply_at[-1] - self.train_at[0], 3.5)
                self.assertLess(rig.nano.core.pulses, 10000)
                self.assertLess(rig.nano.longest_gap, 1.3)  # the lease was kept all that time
                self.assert_everything_off()
                self.rig.close()

    def test_a_nano_that_stops_answering_in_the_middle(self):
        rig = self.rig_for(dict(SMALL, Loopnum=6))
        started = len(rig.nano.trains)
        silent = []

        def cut():
            while len(rig.nano.trains) < started + 2:
                time.sleep(.005)
            rig.nano.handle = lambda frame: None
            silent.append(simulated_rig.clock())
        threading.Thread(target=cut, daemon=True).start()
        report, error = self.run_tool()
        self.assertTrue(silent)
        self.assertIn(UNCONFIRMED, error)
        self.assertIn('Nano STOP (Try again 5 times)', error)
        self.assertIn('; cause: ', error)  # what ended the run is still told
        self.assertEqual((report['valid'], report['off_confirmed']), (False, False))
        self.assertNotIn('off_confirmed', report['timeline_s'])
        self.assertEqual((rig.hspy.run_stop, rig.hspy.voltage()), (0, 0))
        self.assertEqual(self.supply, [(4, 1), (4, 0)])
        # the supply is switched off first, without waiting for the Nano that no longer answers
        self.assertLess(self.supply_at[-1] - silent[0], 3.0)
        self.assertEqual(report['off_settle_samples'][-1]['voltage_v'], 0)
        self.assertEqual(report['final_power']['run_stop_setting'], 0)  # kept although the Nano's last answer is missing
        # the Nano's own lease ended meanwhile: it has stopped by itself
        self.assertFalse(rig.nano.core.outputHigh)
        self.assertFalse(rig.nano.core.active())
        self.assert_no_spectrometer_left()

    def test_a_trigger_edge_that_never_reaches_the_nano(self):
        """The D12 wire is off: the pin reads back HIGH on the RPi side, the Nano never sees it."""
        rig = self.rig_for()
        rig.nano.d12 = lambda: False
        report, error = self.run_tool()
        self.assertEqual(error, 'D12 The trigger has not been Nano received (No. 1 wheel)')
        self.assertEqual(self.trains, [])
        self.assertLess(self.supply_at[-1] - (self.began + report['timeline_s']['first_pulse']), 1.0)
        self.assertEqual((rig.nano.core.state, rig.nano.core.fault), (simulated_rig.IDLE, simulated_rig.NO_FAULT))
        self.assert_everything_off()

    def test_d12_that_does_not_read_back_high(self):
        rig = self.rig_for()
        rig.set_pin = lambda high: None
        _report, error = self.run_tool()
        self.assertEqual(error, 'D12 trigger HIGH readback failed')
        self.assertEqual((self.supply, self.trains), ([(4, 1), (4, 0)], []))
        self.assert_everything_off()


class OutputFolderTests(RigCase):
    def test_job_json_cannot_be_written(self):
        rig = self.rig_for()
        (Path(self.temp.name) / 'job1' / 'job.json').mkdir(parents=True)
        self.run_tool()
        self.assertIsInstance(self.exception, OSError)
        self.assertEqual(self.supply, [(4, 1), (4, 0)])
        self.assert_everything_off()
        self.assertFalse((rig.output_dir / 'job.pid').exists())

    def test_output_folder_cannot_be_made(self):
        rig = self.rig_for()
        (Path(self.temp.name) / 'job1').write_text('in the way')
        before = rig.bus.exchanges
        self.run_tool()
        self.assertIsInstance(self.exception, OSError)
        self.assertEqual((rig.bus.exchanges, rig.started, self.supply), (before, [], []))

    def test_frames_folder_cannot_be_made(self):
        rig = self.rig_for()
        (Path(self.temp.name) / 'job1').mkdir()
        (Path(self.temp.name) / 'job1' / 'frames').write_text('in the way')
        report, error = self.run_tool()
        self.assertNotEqual(error, '')
        self.assertFalse(report['valid'])
        self.assertEqual((self.supply, self.trains, rig.started), ([(4, 0)], [], []))
        self.assertNotIn(ARM, self.ops())
        self.assert_everything_off()

    def test_go_mark_cannot_be_written(self):
        """As when the disk fills up between two frames."""
        rig = self.rig_for()
        verified = simulated_rig.load_verified()
        original = verified.release_frame

        def release_frame(child, number):
            if number == 3:
                raise OSError(28, 'No space left on device')
            return original(child, number)
        verified.release_frame = release_frame
        self.addCleanup(setattr, verified, 'release_frame', original)
        _report, error = self.run_tool()
        self.assertIn('No space left on device', error)
        self.assertEqual((self.supply, len(self.trains)), ([(4, 1), (4, 0)], 1))
        self.assertFalse(rig.pin_level)
        self.assert_everything_off()

    def test_stage_file_cannot_be_written(self):
        self.rig_for()
        (Path(self.temp.name) / 'job1' / 'stage').mkdir(parents=True)
        report, error = self.run_tool()
        self.assertEqual(error, '')
        self.assertTrue(report['valid'])
        self.assertEqual(len(self.trains), 3)
        self.assert_everything_off()

    def test_frames_cannot_be_sorted_into_their_folders(self):
        rig = self.rig_for()
        (Path(self.temp.name) / 'job1').mkdir()
        (Path(self.temp.name) / 'job1' / 'signal').write_text('in the way')
        report, error = self.run_tool()
        self.assertNotEqual(error, '')
        self.assertFalse(report['valid'])
        self.assertEqual((self.supply, len(self.trains)), ([(4, 1), (4, 0)], 3))
        self.assert_everything_off()
        # nothing of the capture is lost: every frame is still in one of the two places
        kept = (len(list((rig.output_dir / 'frames').glob('spectrum-*.csv')))
                + len(list((rig.output_dir / 'background').glob('spectrum-*.csv'))))
        self.assertEqual(kept, 4)


class ServerReadsTheToolsFiles(RigCase):
    """What rpi/tcpd/plasmic_tcpd.py makes of the files a real tool run leaves."""

    def test_result_and_timing_of_a_tool_run(self):
        import plasmic_tcpd as tcpd
        rig = self.rig_for()
        _report, error = self.run_tool()
        self.assertEqual(error, '')
        result = tcpd.load_plasma_result(rig.output_dir, with_frames=True)
        self.assertEqual((result['flow'], result['background_frames'], result['signal_frames'], result['pixels']),
                         ('unified', 1, 3, 1862))
        self.assertEqual(len(result['frames']), 3)
        peak = result['axis'][max(range(result['pixels']), key=lambda i: result['difference'][i])]
        self.assertAlmostEqual(peak, 337.13, delta=.3)
        saved = json.loads((rig.output_dir / 'job.json').read_text(encoding='utf-8'))
        timing = tcpd.job_timing(rig.output_dir, saved, saved['started_at'])
        self.assertEqual((timing['flow'], [row['frame'] for row in timing['frames']]), ('unified', [1, 2, 3]))
        self.assertTrue(2 <= timing['lead_ms'][0] <= timing['lead_ms'][1] <= 30, timing)
        seen = [round((later - earlier) * 1000) for earlier, later in zip(self.train_at, self.train_at[1:])]
        for row, period in zip(timing['frames'][1:], seen):
            self.assertAlmostEqual(row['period_ms'], period, delta=12, msg=(timing['frames'], seen))
        self.assertAlmostEqual(timing['wait_s'], self.first_pulse, delta=.1)
        self.assertEqual(timing['steps_s'], saved['timeline_s'])

    def test_progress_while_the_tool_runs(self):
        import plasmic_tcpd as tcpd
        self.rig_for(dict(SMALL, Loopnum=4))
        seen = []
        job = types.SimpleNamespace(state='running', runner=types.SimpleNamespace(term_sent=False), finishing=False,
                                    kind='plasma', flow='unified', frames_total=4, outdir=None)

        def look(rig, _session):
            job.outdir = rig.output_dir
            progress = tcpd.Controller._progress(None, job)
            row = (progress['phase'], progress['frames_done'])
            if not seen or seen[-1] != row:
                seen.append(row)
            return False
        _report, error = self.run_tool(during=look)
        self.assertEqual(error, '')
        phases = [phase for index, (phase, _done) in enumerate(seen) if index == 0 or phase != seen[index - 1][0]]
        self.assertEqual(phases, [phase for phase in ('prepare', 'ramp', 'background', 'signal', 'off') if phase in phases])
        self.assertIn('signal', phases)
        counts = [done for phase, done in seen if phase == 'signal']
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts[0], 0, seen)
        self.assertGreaterEqual(counts[-1], 2, seen)
        self.assertLessEqual(counts[-1], 4, seen)


class PretendRigTests(unittest.TestCase):
    """The pretend rig must not be kinder than the real thing where that hides a defect."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rig = simulated_rig.Rig(self.temp.name, SMALL)
        self.addCleanup(self.rig.close)
        self.session = simulated_rig.Session(self.rig)
        self.addCleanup(self.session.close)

    def test_the_nano_loses_a_request_that_follows_other_traffic_too_closely(self):
        """It takes all it hears for one frame until the line has been silent for 5 ms."""
        session, bus = self.session, self.rig.bus
        self.assertTrue(session.power()['valid'])
        request = simulated_rig.framed(struct.pack('>BBHH', 2, 3, 0, 40))
        bus.reset_input_buffer()
        bus.last_byte = simulated_rig.clock() - .002  # the supply's last byte was on the wire 2 ms ago
        bus.write(request)
        bus.timeout = .3
        self.assertEqual(bus.read(85), b'')
        time.sleep(.006)
        bus.write(request)                       # after 6 ms of silence, as the verified client waits
        self.assertEqual(len(bus.read(85)), 85)
        self.assertEqual(session.nano.status()['state'], 'IDLE')

    def test_a_nano_that_restarts_has_forgotten_everything(self):
        session, rig = self.session, self.rig
        verified = session.verified
        verified.prepare(session.nano, session.power, verified.encode_parameters(SMALL))
        self.assertTrue(session.nano.status()['config_valid'])
        rig.nano.reset()
        status = session.nano.status()
        self.assertEqual((status['session_id'], status['config_valid'], status['state']), (0, False, 'IDLE'))
        with self.assertRaises(RuntimeError):    # no session: not even a heartbeat can be sent
            verified.heartbeat(session.nano)

    def test_the_spectrometer_program_keeps_the_real_limits(self):
        program = str(self.rig.tools_dir / 'hardware_verify.py')
        for extra, text in ((['--frames', '1001'], 'Frame count must be'), (['--integration-ms', '10001'], 'Integration time must be'),
                            (['--frame-interval-ms', '60001'], 'Frame interval must be')):
            outdir = Path(self.temp.name) / ('limits' + extra[1])
            done = subprocess.run([sys.executable, '-B', program, 'spectrum', '--outdir', str(outdir), '--triggered', *extra],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace')
            self.assertEqual(done.returncode, 2, extra)
            report = json.loads(done.stdout)
            self.assertIn(text, report['error'])
            self.assertFalse(report['valid'])
            self.assertFalse((outdir / 'frame-1.ready').exists())

    def test_a_sigterm_reaches_the_spectrometer_program_only_between_two_sdk_calls(self):
        rig, verified = self.rig, self.session.verified
        rig.set_spectrometer(speed=4)
        child = verified.start_spectrum(rig.app_dir, rig.tools_dir, rig.output_dir / 'frames', 1500, frames=2, triggered=True)
        deadline = time.monotonic() + 5
        while not verified.frame_ready(child, 1) and time.monotonic() < deadline:
            time.sleep(.01)
        verified.release_frame(child, 1)         # a 1.5 s exposure begins
        time.sleep(.3)
        sent = time.monotonic()
        rig.killpg(child.pid, signal.SIGTERM)
        child.communicate(timeout=5)
        self.assertTrue(.9 <= time.monotonic() - sent <= 2.0)   # not before the SDK call came back
        self.assertEqual(child.returncode, 2)
        report = json.loads((rig.output_dir / 'frames' / 'spectrum.json').read_text(encoding='utf-8'))
        self.assertEqual(report['error'], 'Interrupted by signal 15')
        self.assertEqual(report['sdk_returns'][-1]['function'], 'UAI_SpectrometerClose')  # it closed the spectrometer


class CommandLineTests(RigCase):
    def test_nothing_happens_without_both_confirmations_or_with_a_lead_out_of_range(self):
        tool = str(ROOT / 'rpi' / 'tcpd' / 'plasma_measure.py')
        both = ['--execute', '--confirm-high-voltage']
        with tempfile.TemporaryDirectory() as folder:
            for extra, text in (([], '--execute'), (['--execute'], '--execute'), (['--confirm-high-voltage'], '--execute'),
                                (both + ['--lead-ms', '201'], '--lead-ms'), (both + ['--lead-ms', '-1'], '--lead-ms')):
                done = subprocess.run([sys.executable, '-B', tool, '--outdir', folder, *extra],
                                      capture_output=True, text=True, encoding='utf-8', errors='replace')
                self.assertEqual(done.returncode, 2, extra)
                self.assertIn(text, done.stderr)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_main_runs_a_whole_job_with_the_parts_it_builds_itself(self):
        import plasmic_tcpd as tcpd
        rig = self.rig_for()
        session = simulated_rig.Session(rig)
        self.addCleanup(session.close)
        verified = session.verified
        saved = {name: getattr(verified, name) for name in ('open_port', 'Trigger')}
        self.addCleanup(lambda: [setattr(verified, name, value) for name, value in saved.items()])
        verified.open_port = lambda path, timeout: contextlib.nullcontext(rig.bus)
        verified.Trigger = lambda: session.trigger
        fcntl = sys.modules['fcntl']
        added = [name for name in ('flock', 'LOCK_EX', 'LOCK_NB') if not hasattr(fcntl, name)]
        for name in added:
            setattr(fcntl, name, (lambda *_args: None) if name == 'flock' else 1)
        self.addCleanup(lambda: [delattr(fcntl, name) for name in added])
        # /tmp/hm-hmi-rs485.lock: a file of this test's own instead. A real file, because on the RPi
        # fcntl.flock is the real one and wants something it can lock.
        locks = []

        def lock_file(_path, mode):
            locks.append(open(Path(self.temp.name) / 'rs485.lock', mode))
            return locks[-1]
        plasma_measure.open = lock_file
        self.addCleanup(delattr, plasma_measure, 'open')
        self.addCleanup(lambda: [stream.close() for stream in locks])
        handlers = {name: signal.getsignal(getattr(signal, name)) for name in ('SIGTERM', 'SIGINT') if hasattr(signal, name)}
        self.addCleanup(lambda: [signal.signal(getattr(signal, name), handler) for name, handler in handlers.items()])
        path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), path))
        out = Path(self.temp.name) / 'HMI-main'
        argv, sys.argv = sys.argv, ['plasma_measure.py', '--app-dir', str(rig.app_dir), '--tools-dir', str(rig.tools_dir),
                                    '--port', '/dev/RS485', '--outdir', str(out), '--lead-ms', '12',
                                    '--execute', '--confirm-high-voltage']
        self.addCleanup(setattr, sys, 'argv', argv)
        seen = len(rig.hspy.writes)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = plasma_measure.main()
        self.assertEqual(code, 0)
        self.assertIs(signal.getsignal(signal.SIGTERM), plasma_measure.on_signal)
        result = tcpd._last_json(printed.getvalue())
        self.assertTrue(result['valid'])
        self.assertEqual((result['flow'], result['exposure_lead_ms']), ('unified', 12.0))
        # main() keeps time with time.monotonic, as on the RPi. On Windows that clock moves in steps
        # of 16 ms, so the lead it notes there is 0, 16 or 31 ms: only looked at where the clock is fine.
        if time.get_clock_info('monotonic').resolution < .002:
            self.assertTrue(all(12 <= shot['lead_ms'] <= 30 for shot in result['triggers']), result['triggers'])
        # From the two programs' own times of day: every exposure was asked for well before its trigger.
        self.assertTrue(all(5 <= shot['exposure_before_pulse_ms'] <= 60 for shot in result['triggers']), result['triggers'])
        self.assertEqual([(register, value) for _when, register, value in rig.hspy.writes[seen:]], [(4, 1), (4, 0)])
        self.assertEqual(len(result['signal']['frames']), 3)
        self.assertEqual((rig.hspy.run_stop, rig.hspy.voltage(), rig.pin_level), (0, 0, False))
        self.assertEqual(tcpd.load_plasma_result(out)['signal_frames'], 3)

    def test_main_only_writes_the_set_voltage_when_that_is_what_it_is_asked_for(self):
        """AMENDMENTS M22: `plasma_measure.py --set-voltage N --execute`, the whole of main() on the pretend rig. It
        looks at OFF first, writes once, prints one JSON line, and touches neither the trigger pin nor the Nano."""
        import plasmic_tcpd as tcpd
        rig = self.rig_for()
        rig.hspy.set_voltage = 1000
        session = simulated_rig.Session(rig)
        self.addCleanup(session.close)
        verified = session.verified
        saved = {name: getattr(verified, name) for name in ('open_port', 'Trigger')}
        self.addCleanup(lambda: [setattr(verified, name, value) for name, value in saved.items()])
        verified.open_port = lambda path, timeout: contextlib.nullcontext(rig.bus)

        def no_trigger():
            raise AssertionError('the trigger pin is not for this')
        verified.Trigger = no_trigger
        fcntl = sys.modules['fcntl']
        added = [name for name in ('flock', 'LOCK_EX', 'LOCK_NB') if not hasattr(fcntl, name)]
        for name in added:
            setattr(fcntl, name, (lambda *_args: None) if name == 'flock' else 1)
        self.addCleanup(lambda: [delattr(fcntl, name) for name in added])
        locks = []

        def lock_file(_path, mode):
            locks.append(open(Path(self.temp.name) / 'rs485.lock', mode))
            return locks[-1]
        plasma_measure.open = lock_file
        self.addCleanup(delattr, plasma_measure, 'open')
        self.addCleanup(lambda: [stream.close() for stream in locks])
        handlers = {name: signal.getsignal(getattr(signal, name)) for name in ('SIGTERM', 'SIGINT') if hasattr(signal, name)}
        self.addCleanup(lambda: [signal.signal(getattr(signal, name), handler) for name, handler in handlers.items()])
        path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), path))
        argv = sys.argv
        self.addCleanup(setattr, sys, 'argv', argv)

        def run(volts, *more):
            sys.argv = ['plasma_measure.py', '--app-dir', str(rig.app_dir), '--tools-dir', str(rig.tools_dir),
                        '--port', '/dev/RS485', '--set-voltage', str(volts), '--execute', *more]
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                code = plasma_measure.main()
            return code, tcpd._last_json(printed.getvalue())

        def written(since):
            return [(register, value) for _when, register, value in rig.hspy.writes[since:]]
        seen, pulses = len(rig.hspy.writes), rig.nano.core.pulses
        code, result = run(30)
        self.assertEqual((code, result['valid'], result['changed'], result['previous_set_voltage_v'], result['set_voltage_v']),
                         (0, True, True, 1000, 30))
        self.assertEqual((result['hspy']['set_voltage_v'], result['hspy']['run_stop_setting'], result['hspy']['voltage_v']), (30, 0, 0))
        self.assertEqual(written(seen), [(0, 30)])                     # one write, and never register 4
        self.assertEqual(rig.hspy.set_voltage, 30)
        # The same voltage again: a look, no write.
        seen = len(rig.hspy.writes)
        code, result = run(30)
        self.assertEqual((code, result['changed'], result['previous_set_voltage_v'], written(seen)), (0, False, 30, []))
        # A supply that is ON is left alone: nothing is written, and it stays as it is.
        rig.hspy.run_stop = 1
        with self.assertRaisesRegex(Exception, 'need HSPY OFF'):
            run(55)
        self.assertEqual((written(seen), rig.hspy.set_voltage, rig.hspy.run_stop), ([], 30, 1))
        rig.hspy.run_stop = 0
        # Nothing of the measurement happened on the way: no output folder, no pulse, the pin never driven.
        self.assertEqual((rig.nano.core.pulses, rig.pin_level, rig.started), (pulses, False, []))
        self.assertNotIn(ARM, [op for _when, op, _result in rig.nano.commands])


if __name__ == '__main__':
    unittest.main(verbosity=2)
