#!/usr/bin/env python3
"""Measure once: first turn on the high voltage (no discharge) and take the background spectrum 1 , then discharge, each round of exposure and discharge start together.

rpi_tools/plasma_job.py is a field-proven tool and the content has not been modified. it shoots first 3 Zhang background, turn on the spectrometer again,
and then increase the voltage, from pressing to the first pulse it takes about 11 seconds; in each round, the trigger is sent first and then the spectrometer is notified, and the exposure is later than the discharge
0.01–0.03 seconds to start.

This program uses the same batch of functions (each RS485 transaction, shutdown process, spectrometer program are the same), just change the steps
Arranged like this: 

  1. First check whether the parameters can be done (see pace()) ; If you can’t do it, just reject it and don’t touch anything;
  2. confirm HSPY OFF/output 0, Nano is idle. When the set voltage on the high-voltage power supply is different from the setting file, the OFF/In zero output state
     use RS485 Write it as the voltage of the configuration file and read it back (user 2026-10-07 ’s requirement: do not report an error, let the program correct it); the readback is incorrect The design logic architecture of
     will end and nothing will open;
  3. Start the spectrometer program immediately (only once: Chapter 1 Zhang is the background, and every subsequent round 1 Zhang). It takes nearly a second to turn on, which
     Time check Nano parameters (if they are exactly the same as the configuration file, they will be used, otherwise they will be resynchronized in the original way) and ARM; 
  4. After the spectrometer is ready, HSPY ON (does not send pulses), wait for the voltage to arrive. If the spectrometer cannot be turned on, it will not turn on high pressure;
  5. When the high voltage is on, the voltage has reached, and there are no pulses yet, take a picture of the background (user 2026-10-06 of
     Requirement: The background must be shot in the state of ”high voltage is on but not discharging”). while it is exposed START: Nano Wait from now on
     trigger,D12 always LOW,So there will be no pulse until the background is shot;
  6. Notifies the spectrometer of the exposure first in each round, confirming that it takes over a few milliseconds (--lead-ms,default 5) Just sent D12 trigger. of each round
     The interval is timed here, and the heartbeat is arranged so that it will not block the trigger;
  7. Nano DONE immediately after HSPY OFF/Nano STOP and read back to 0 V ((the same shutdown process as the original one), then collect the spectrum, What is sent and received between
     It is finally confirmed that the spectrometer program has ended.

Each period of waiting when the high voltage may be turned on has its own period. After that, it will go directly to the shutdown process: the background one, the spectrum of each round, It should not happen on
Spectrometer takes over, Nano Has the trigger been received? Stop signal (SIGTERM) Will not be missed: I will check it again before going to the next step.
has not been received.

job.json Write two things separately:off_confirmed (HSPY OFF/0 V and Nano D13 LOW Is there a readback confirmation) and Has
spectrometer_ended (The spectrometer program has not ended).valid Only after the entire process is completed, the readback confirmation is turned off, and each spectrum
is when everyone is here true.

Same as original: must bring --execute and --confirm-high-voltage; received SIGTERM will complete the shutdown process.
The software stop is not a safety level emergency stop.

This process is 2026-10-06 is written and modified according to the inspection results on the same day; in the afternoon of the same day, the background picture is changed according to the user’s request.
was taken when the change from ”high voltage has not been turned on” to ”high voltage has been turned on and has not been discharged yet”. Only in simulation RS485/Nano/HSPY/on the spectrometer
tested (tests/test_measure.py) ,has not been run on real devices.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKGROUND_FRAMES = 1  # frame 1 of the one capture; the frame of loop k is frame BACKGROUND_FRAMES + k

# ---- What can work at all (pace). plasmic_tcpd.check_parameters keeps its own copy of this rule.
# One loop on the 9600-baud bus is a pulse train, a status read that shows it has ended (0.11 s)
# and a share of the heartbeats (0.26 s each). Measured on the emulated bus in the review of
# 2026-10-06: a train needs 0.15 s after it before the next can begin, and no loop is shorter
# than 0.25 s. A shorter interval is not refused: the loops then come at this pace.
LOOP_OVERHEAD_US = 150000
SHORTEST_PERIOD_US = 250000
# After a pulse train the Nano waits 30 s for the next trigger and then latches TRIGGER_TIMEOUT;
# the spectrometer program gives up 30 s after it is ready for a frame.
LONGEST_PERIOD_US = 28000000
# All loops together; the Nano ends a job (JOB_TIMEOUT) 120 s after START. The background frame
# (10 s at most) lies between START and the first trigger: 110 s at most, inside the 120.
LONGEST_SIGNAL_US = 100000000
MOST_SIGNAL_FRAMES = 999  # the spectrometer program takes at most 1000 frames in one session

# ---- Exposure and trigger
# D12 rises this long after the spectrometer program has taken the go mark (it calls the SDK about
# 1 ms after taking it), so the exposure is open when the first pulse comes. How long the real
# sensor needs to start is not known before the rig has been tried: --lead-ms sets it on site.
EXPOSURE_LEAD_SECONDS = .005
LONGEST_LEAD_SECONDS = .2
# The program looks for the go mark every 10 ms. No heartbeat is sent while it is waited for (one
# would put 0.26 s between exposure and discharge), so the wait is short enough for the lease.
GO_TAKEN_SECONDS = .5
FRAME_OVERHEAD_SECONDS = .03  # that program is ready again this long after an exposure (site log 2026-10-05)

# ---- Waits with the supply ON. Each has its own limit and expiry goes straight to the OFF
# sequence, long before the Nano's own 30 s trigger timeout (which latches a fault).
# The background frame is taken with the supply ON and at its voltage, before any pulse (the owner's
# rule of 2026-10-06). The Nano has been STARTed by then and waits for D12: it allows 30 s for the
# first trigger, and the longest exposure is 10 s.
BACKGROUND_GRACE_SECONDS = 2.0  # the background frame is finished this long after it should be, or the run ends
FRAME_GRACE_SECONDS = 3.0       # the same for each loop's frame, counted from the end of its exposure
PACE_SLACK = 1.1                # all loops together may take this much longer than planned,
SIGNAL_SPARE_SECONDS = 15       # plus this
NANO_JOB_SECONDS = 123          # as in the verified tool: just beyond the Nano's own 120 s
COLLECT_SECONDS = 10            # after the last exposure: closing the SDK and writing the report

# ---- Heartbeats. The Nano's lease lasts 2 s; a heartbeat occupies the bus for about 0.26 s and a
# status read for about 0.11 s. Nothing may be on the bus at the moment a frame is due.
HEARTBEAT_SECONDS = .5   # the regular beat while there is no reason to keep the bus quiet
QUIET_SECONDS = .9       # the bus may stay quiet this long after a heartbeat ...
BRIDGE_SECONDS = 1.25    # ... and for once this long, when no heartbeat fits in before a frame is due
HEARTBEAT_ROOM = .30     # a heartbeat started this long before a moment is over by then
STATUS_ROOM = .12        # the same for a status read
HEARTBEAT_ANSWERS = .15  # a heartbeat's closing status shows a train as ended that ends this soon after it began
READY_SLACK = .25        # a quiet stretch must last this long past the moment a frame is expected

# ---- Ending the spectrometer program
PROGRAM_TERM_SECONDS = 2  # after SIGTERM it closes the spectrometer itself, unless it is inside the SDK
PROGRAM_KILL_SECONDS = 5  # after SIGKILL
LOCK_BUSY = 75            # "flock -n -E 75": another program holds the spectrometer

# What the verified modules say when the Nano no longer has the session this run opened or kept.
SESSION_GONE = ('Nano session/serial number is invalid', 'LCD reporting requires firmware 1.1.0+ and an existing synced session')

verified = None  # rpi_tools/plasma_job.py, bound by load_tools()


def load_tools(tools_dir, app_dir):
    """Bind the field-verified modules this program is built from. They are used, never changed."""
    global verified
    for folder in (str(app_dir), str(tools_dir)):
        if folder not in sys.path:
            sys.path.insert(0, folder)
    import plasma_job
    verified = plasma_job
    return plasma_job


def utc():
    return datetime.now(timezone.utc)


def explained(error):
    """The text of an error for the person. Two messages of the verified modules mean the same
    thing and say it in a way nobody at the rig can use: the Nano has restarted in mid-run."""
    if error in SESSION_GONE:
        return 'Nano There is no work this time session (may have been restarted at work):' + error
    return error


def write_set_voltage(power, power_write, target):
    """Write the supply's set voltage and read it back. The caller has just seen the supply OFF with nothing
    at its output (verified.require_off); `power` is its reader for that state.

    The person's decision of 2026-10-07: the program sets the voltage over RS485 instead of refusing because
    the panel says something else. The verified tool's set_voltage_off cannot be used for it: it writes nothing
    when the set voltage it finds is above 700 V or not a whole number (the rig's supply stood at 1000.0 V), since
    it wants to be able to put that value back. Nothing is put back here: the supply is OFF, a failed write is
    followed by one more OFF, and the caller ends what it was doing.

    Returns the read-back, which says: valid, OFF, 0 V, 0 A, set voltage == target. Else RuntimeError."""
    v = verified
    if type(target) is not int or not 1 <= target <= v.MAX_VOLTAGE:
        raise ValueError(f'HSPY The set voltage must be 1–{v.MAX_VOLTAGE} V integer')
    try:
        power_write(0, target)
        after = power()
        if (after.get('valid') is not True or after.get('set_voltage_v') != target
                or after.get('run_stop_setting') != 0 or after.get('voltage_v') != 0
                or after.get('current_a') != 0):
            raise RuntimeError('The high voltage power supply does not accept the written set voltage. The set voltage on the %d V (Readback: Setting %s V, Run/Stop %s, output %s V) '
                               % (target, after.get('set_voltage_v'), after.get('run_stop_setting'),
                                  after.get('voltage_v')))
        return after
    except Exception as exc:
        try:
            power_write(4, 0)  # it was OFF and nothing here switches it on: said once more all the same
        except Exception as again:
            raise RuntimeError('HSPY The setting voltage is not written (%s) ,Will be sent later OFF has also not been confirmed (%s) ' % (exc, again)) from exc
        raise


def stop_requested():
    """Raise the usual interrupt error when a stop signal has been received.

    plasma_job.Interrupts holds a signal back while an RS485 exchange runs and raises it when the
    exchange is over. Inside plasma_job.off_check_reader that raise is taken for a failed read and
    the read is simply repeated: the one SIGTERM the TCP server sends was lost and the job went on
    to switch the high voltage on (review of 2026-10-06). Every signal is also written down in
    INTERRUPTS.received, so that list is looked at wherever the run is about to go a step further.
    """
    interrupts = verified.INTERRUPTS
    if interrupts.received and not interrupts.cleaning:
        raise RuntimeError('The job received an interrupt signal %d' % interrupts.received[0])


def on_signal(signum, frame):
    """SIGTERM/SIGINT/SIGHUP. The first one goes the verified way; later ones are only written down.

    The TCP server repeats its SIGTERM when the first seems to have had no effect. The first is
    already on its way to the OFF sequence, as an exception or through stop_requested(). A second
    exception could arrive in the instant after the first has been caught and before the OFF
    sequence has shielded itself, and that sequence would then be skipped.
    """
    interrupts = verified.INTERRUPTS
    if interrupts.received:
        interrupts.received.append(signum)
    else:
        interrupts.handler(signum, frame)


def pace(parameters):
    """(train_s, period_s): how long one pulse train lasts and how far apart the loops really are.

    Refuses (ValueError) what cannot work, before anything is switched. In whole microseconds, so
    that this and the TCP server's copy of the rule agree to the last digit. ``parameters`` must
    already have passed plasma_job.encode_parameters.
    """
    train_us = int(parameters['Cycle']) * int(round((float(parameters['Ontime']) + float(parameters['Offtime'])) * 1000))
    loops = int(parameters['Loopnum'])
    period_us = max(int(parameters['Spectra_Interval']) * 1000, train_us + LOOP_OVERHEAD_US, SHORTEST_PERIOD_US)
    if loops >= 2 and period_us > LONGEST_PERIOD_US:
        raise ValueError('cannot exceed between two rounds 28 seconds (Nano The upper limit of waiting for triggering is 30 seconds); currently each round %.2f Second' % (period_us / 1e6))
    if loops * period_us > LONGEST_SIGNAL_US:
        raise ValueError('Number of rounds × Each round time cannot exceed 100 seconds (Nano is The value of 120 seconds); currently %d wheel × %.2f Second = %.1f Second.'
                         'Each round time is ”spectral interval” and ”single round pulse time”＋0.15 Second”“0.25 seconds” the largest of the three'
                         % (loops, period_us / 1e6, loops * period_us / 1e6))
    if loops > MOST_SIGNAL_FRAMES:
        raise ValueError('spectrometer program at most once 1000 open (background 1 open＋Most signals 999 open) ')
    return train_us / 1e6, period_us / 1e6


def synchronized(status, session_id, data):
    """Does a Nano status show the session ``session_id`` holding exactly the parameters ``data``, idle?"""
    revision, crc = struct.unpack('>I', data[:4])[0], struct.unpack('>I', data[-4:])[0]
    return (bool(session_id) and status['session_id'] == session_id and status['config_valid']
            and (status['active_revision'], status['active_crc32']) == (revision, crc)
            and status['state'] in ('IDLE', 'DONE') and status['fault'] == 'NONE'
            and not status['output_command_high'] and not status['output_pin_sample'])


def synchronized_revision(nano, status, data):
    """The revision of the Nano's active parameters when they are exactly ``data``, else None.

    Reads only. It is the comparison pulse_prepare.prepare() ends with; when it holds, the session
    that put the parameters there is kept and only its lease is renewed before ARM.
    """
    if not status['session_id'] or not status['config_valid']:
        return None
    words = nano.read(0x100, 18)
    if struct.pack('>18H', *words) != data or not synchronized(nano.status(), status['session_id'], data):
        return None
    return struct.unpack('>I', data[:4])[0]


def child_error(child):
    """What the spectrometer program itself reported as the reason, or ''."""
    try:
        report = json.loads((child.spectrum_output_dir / 'spectrum.json').read_text(encoding='utf-8'))
        return str(report.get('error') or '')
    except (OSError, ValueError, AttributeError):
        return ''


def ended(child):
    """Why the spectrometer program is no longer running, in its own words when it left any."""
    reason = child_error(child)
    if not reason and child.poll() == LOCK_BUSY:
        return 'spectrometer is being used by another program'
    return reason or verified.spectrum_last_stage(child)


def collect(child, deadline, frames):
    """verified.collect_spectrum; a failure names the spectrometer program's own reason."""
    try:
        return verified.collect_spectrum(child, deadline, frames)
    except RuntimeError as exc:
        reason = child_error(child) if str(exc).startswith('Spectrum acquisition failed') else ''
        if reason:
            raise RuntimeError('Spectrum acquisition failed:' + reason) from exc
        raise


def set_aside(directory):
    """Keep what a failed spectrometer start wrote, out of the way of the next start."""
    for number in range(1, 10):
        target = directory.with_name(f'{directory.name}-failed-{number}')
        if not target.exists():
            directory.rename(target)
            return


def spectrometer_ready(child, restart, notes, clock, sleep, seconds=10.0, idle=lambda: None):
    """The spectrometer program once it has opened the spectrometer and waits for frame 1.

    The supply has not been switched on yet, so a program that ended early (the spectrometer did
    not open) is started once more after a second. ``restart`` makes the new program known to the
    caller before it returns: whatever happens here, the caller can end it. ``idle`` is called all
    the while: the Nano may be ARMED by now, and its lease has to be kept.
    """
    for attempt in (1, 2):
        deadline = clock() + seconds
        while child.poll() is None:
            if verified.frame_ready(child, 1):
                return child
            if clock() >= deadline:
                raise TimeoutError('spectrometer is not ready before turning on high voltage:' + verified.spectrum_last_stage(child))
            idle()
            sleep(.01)
        reason = ended(child)
        if attempt == 2:
            raise RuntimeError('The spectrometer did not turn on successfully (tried 2 times, high voltage is not turned on):' + reason)
        notes.append('Spectrometer No. 1 times failed to open successfully ( Unable to start measurement%s) ,1 Try again in seconds Discharge after' % reason)
        child.communicate()
        if child.spectrum_output_dir.exists():
            set_aside(child.spectrum_output_dir)
        again = clock() + 1.0
        while clock() < again:
            idle()
            sleep(.02)
        child = restart()
    return child


def end_program(child):
    """End one spectrometer program for certain and say how it went: 'ended' (it had ended by
    itself), 'stopped' (after SIGTERM), 'killed' (it needed SIGKILL) or 'left: why'.

    SIGTERM goes to its process group first: the program then closes the spectrometer itself. It
    can act on that only between two SDK calls, so one that is stuck inside the SDK, or in a long
    exposure, gets SIGKILL after 2 s. Whether it has ended is read from its pipes: on the RPi the
    process known here is the "flock" in front, which a SIGTERM ends at once while the program
    proper still runs.
    """
    def send(signum):
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass  # it ended this instant

    if child.poll() is not None:
        return 'ended'
    try:
        send(signal.SIGTERM)
        try:
            child.communicate(timeout=PROGRAM_TERM_SECONDS)
            return 'stopped'
        except subprocess.TimeoutExpired:
            send(signal.SIGKILL)
        try:
            child.communicate(timeout=PROGRAM_KILL_SECONDS)
            return 'killed'
        except subprocess.TimeoutExpired:
            return 'left: SIGKILL after %g The seconds are not over yet' % PROGRAM_KILL_SECONDS
    except Exception as exc:
        return 'left: ' + (str(exc) or type(exc).__name__)


def quiet_until(now, last_heartbeat, due):
    """Until when the bus may stay quiet while the Nano waits for D12.

    ``due`` is about when the next frame will be started. A heartbeat occupies the bus for about
    0.26 s: none may be running at that moment, and the last one before it must be recent enough
    to wait through it.
    """
    quiet_end = last_heartbeat + QUIET_SECONDS
    if quiet_end >= due + READY_SLACK:
        return quiet_end                                # this quiet stretch already lasts through the moment
    if now >= due + READY_SLACK:
        return quiet_end                                # the moment has passed (the frame is late): the lease rule
    if now >= due - HEARTBEAT_ROOM:
        # A heartbeat started now would be on the bus at the moment. Stay quiet through it for
        # once, when the lease allows; otherwise the lease comes first, and the sooner the better.
        return due + READY_SLACK if due + READY_SLACK - last_heartbeat <= BRIDGE_SECONDS else now
    covering = due + READY_SLACK - QUIET_SECONDS        # a heartbeat from this time on lasts through the moment
    return covering if covering <= quiet_end else last_heartbeat + HEARTBEAT_SECONDS


def beat_in_train(now, last_heartbeat, train_end, due):
    """When to send the next heartbeat while a pulse train runs; None: not before it has ended.

    The Nano must be asked right when the train ends (is it waiting for the next trigger, or
    DONE?), so the last heartbeat of a train is timed to be over by then. ``due`` is about when
    the next frame will be started, None after the last. A time that has passed means: now.
    """
    latest = last_heartbeat + QUIET_SECONDS
    if due is not None and now >= due + READY_SLACK:
        # The train runs on past the moment the next frame was due: that frame is late whatever is
        # done now, so nothing is gained by keeping the bus quiet for it. Only the lease counts.
        # (Without this the lease lapsed when a train did not end: the Nano latched HEARTBEAT_LOST.)
        return latest
    if due is not None and latest >= due + READY_SLACK:
        return None                                     # this quiet stretch lasts through the next frame
    aligned = train_end - HEARTBEAT_ROOM
    if now <= aligned:
        return aligned if aligned <= latest else last_heartbeat + HEARTBEAT_SECONDS
    # Too close to the end of the train for a heartbeat that is over by then (or the train is short).
    if due is None:
        return latest                                   # the last train: only what the lease asks for
    waiting = max(now, train_end) + STATUS_ROOM         # when the Nano is known to wait for D12 again
    if waiting + HEARTBEAT_ROOM <= due and latest >= waiting:
        return None                                     # there is room for one between the train and the frame
    done = now + HEARTBEAT_ROOM                         # when a heartbeat sent now, and what must follow, is over
    if train_end > now + HEARTBEAT_ANSWERS:
        done = max(done, train_end) + STATUS_ROOM       # its closing status comes too early to show the train's end
    if done <= due or due + READY_SLACK - last_heartbeat > BRIDGE_SECONDS:
        return now                                      # it fits in before the frame, or the lease needs it anyway
    return None                                         # it would delay the frame: stay quiet through it for once


def watch(child, number, not_before, until, clock, sleep):
    """True as soon as the spectrometer program is ready for frame ``number`` and ``not_before`` has
    come; False at ``until`` (time for something else) or when that program has ended."""
    while True:
        now = clock()
        if now >= not_before and verified.frame_ready(child, number):
            return True
        if now >= until or child.poll() is not None:
            return False
        sleep(.001)


def expose(child, number, what, clock, sleep):
    """Tell the spectrometer program to take frame ``number`` (``what`` names it for the person) and
    wait until it has taken the word.

    It looks for the go mark every 10 ms, removes it and calls the SDK about 1 ms later. Returns
    (time of day the mark was written, the same on ``clock``, when it was seen to be taken).
    """
    marker = child.spectrum_output_dir / f'frame-{number}.go'
    go_utc, go = utc(), clock()
    verified.release_frame(child, number)
    while marker.exists():
        if child.poll() is not None:
            raise RuntimeError(f'Spectral program in{what}ends before exposure starts:' + ended(child))
        if clock() - go >= GO_TAKEN_SECONDS:
            try:
                marker.unlink()  # so that no exposure begins after the run has given up
            except OSError:
                pass
            raise RuntimeError(f'Spectrum program is not available {GO_TAKEN_SECONDS * 1000:.0f} ms Start within{what}exposure')
        sleep(.0005)
    return go_utc, go, clock()


def fire(trigger, child, number, what, lead, clock, sleep):
    """Open one exposure and start one pulse train, in that order and a few milliseconds apart.

    D12 rises ``lead`` seconds after the spectrometer program took the word; the Nano answers D12
    within 0.2 ms. Nothing is triggered when the program does not take it. Returns the times for
    the report, the moment the word was given and the moment D12 rose.
    """
    value = trigger.pin / 'value'
    trigger.low()  # it has been LOW since the last pulse; make sure before anything starts
    go_utc, go, taken = expose(child, number, what, clock, sleep)  # raises, and nothing is triggered
    sleep(lead)
    value.write_text('1\n')
    edge_utc, edge = utc(), clock()
    if value.read_text().strip() != '1':
        raise RuntimeError('D12 trigger HIGH readback failed')
    sleep(.01)
    trigger.low()
    return {'go_utc': go_utc.isoformat(), 'edge_utc': edge_utc.isoformat(),
            'taken_ms': round((taken - go) * 1000, 1), 'lead_ms': round((edge - taken) * 1000, 1)}, go, edge


def add_exposure_times(capture_dir, triggers, background):
    """From the spectrometer program's own log: when it called the SDK for each loop's frame.
    Returns the loops whose call was logged only after D12 had risen."""
    calls = {}
    try:
        lines = (capture_dir / 'progress.jsonl').read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            event = json.loads(line)
            if event.get('function') == 'UAI_SpectrometerDataOneshot' and event.get('stage', '').startswith('sdk_call_'):
                calls.setdefault(event.get('frame'), {})[event['stage']] = datetime.fromisoformat(event['utc'])
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    for shot in triggers:
        call = calls.get(shot['frame'] + background, {})
        if 'sdk_call_start' in call:
            edge = datetime.fromisoformat(shot['edge_utc'])
            # positive: the exposure was asked for first, D12 rose this much later
            shot['exposure_before_pulse_ms'] = round((edge - call['sdk_call_start']).total_seconds() * 1000, 1)
            if 'sdk_call_done' in call:
                shot['exposure_ms'] = round((call['sdk_call_done'] - call['sdk_call_start']).total_seconds() * 1000, 1)
    return [shot['frame'] for shot in triggers if shot.get('exposure_before_pulse_ms', 0) < 0]


def split_frames(capture, capture_dir, output_dir, background):
    """background/spectrum-N.csv and signal/spectrum-N.csv with a report each, the layout the
    verified tool leaves and the TCP server's result loader reads. The first ``background`` frames
    of the one capture are the background. The list of every SDK call stays where the spectrometer
    program wrote it (frames/spectrum.json): twice in job.json it made that file too big to load."""
    frames = capture['frames']
    shared = {key: value for key, value in capture.items() if key != 'sdk_returns'}
    groups = {}
    for name, members in (('background', frames[:background]), ('signal', frames[background:])):
        folder = output_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        renamed = []
        for index, frame in enumerate(members, 1):
            target = f'spectrum-{index}.csv'
            (capture_dir / frame['file']).replace(folder / target)
            renamed.append(dict(frame, file=target, captured_as=frame['file']))
        groups[name] = dict(shared, frames=renamed, frames_expected=len(renamed))
    return groups


class Measurement:
    """One bounded background + pulse/spectrum job. run() always tries to stop Nano and HSPY."""

    def __init__(self, nano, power, power_write, trigger, parameters, app_dir, tools_dir, output_dir,
                 clock, drain, sleep, lead):
        v = verified
        self.nano, self.power, self.power_write, self.trigger = nano, power, power_write, trigger
        self.parameters, self.app_dir, self.tools_dir, self.output_dir = parameters, app_dir, tools_dir, output_dir
        self.clock, self.drain, self.sleep, self.lead = clock, drain, sleep, lead
        # Everything that can be refused is refused here, before a file or the bus is touched.
        self.voltage = int(parameters['Voltage'])
        if not 1 <= self.voltage <= v.MAX_VOLTAGE:
            raise ValueError(f'Voltage Must be 1–{v.MAX_VOLTAGE} V')
        self.data = v.encode_parameters(parameters)
        self.integration_ms = int(parameters['Integration_Time'])
        if not 1 <= self.integration_ms <= 10000:
            raise ValueError('Integration time limit 1–10000 ms')
        self.loops = int(parameters['Loopnum'])
        interval_ms = parameters['Spectra_Interval']
        if type(interval_ms) not in (int, float) or interval_ms != int(interval_ms):
            raise ValueError('Spectral interval must be an integer millisecond')
        self.train_s, self.period_s = pace(parameters)
        if type(lead) not in (int, float) or not 0 <= lead <= LONGEST_LEAD_SECONDS:
            raise ValueError('exposure advance amount (--lead-ms) Must be 0–200 ms')
        self.integration_s = self.integration_ms / 1000
        self.again_s = self.integration_s + FRAME_OVERHEAD_SECONDS  # go mark to "ready for the next frame"
        self.report = {'valid': False, 'started_at': utc().isoformat(), 'mode': 'production', 'flow': 'unified',
                       'voltage_v': self.voltage, 'output_dir': str(output_dir), 'steps': [],
                       'background_frames': BACKGROUND_FRAMES, 'signal_target_frames': self.loops,
                       'spectra_interval_ms': int(interval_ms), 'loop_period_ms': round(self.period_s * 1000),
                       'exposure_lead_ms': round(lead * 1000, 1), 'hv_on_attempted': False, 'off_confirmed': False,
                       'power_read_retries': [], 'timeline_s': {}, 'triggers': [],
                       'heartbeats': {'count': 0, 'longest_s': 0.0, 'longest_gap_s': 0.0}}
        self.steps = self.report['steps']
        if self.period_s * 1000 > int(interval_ms):
            self.steps.append('Spectral interval %d ms is shorter than possible, and the actual round %d ms' % (interval_ms, round(self.period_s * 1000)))
        self.capture_dir = output_dir / 'frames'
        self.programs = []          # every spectrometer program this run started; the last is the one in use
        self.began = clock()
        self.last_heartbeat = None
        self.kept_session = None    # the Nano session that already held these parameters, while it is relied on
        self.revision = self.job_id = None
        self.lcd_told = False       # POWER ON has been put on the Nano's LCD
        self.triggered = 0          # loops whose trigger has been sent
        self.due = self.ready_by = None
        self.completed = False      # True only once the Nano has reported DONE with every loop counted
        reader = v.off_check_reader(power, drain, log=self.report['power_read_retries'])

        def off_power():
            """The supply's status, for the checks made while it must be OFF: a lost reply is asked for
            again (verified.off_check_reader), and a stop that came during the read is not lost with it."""
            reading = reader()
            stop_requested()
            return reading
        self.off_power = off_power  # never after HSPY ON: the ON readback and the cleanup do not retry

    # ---- small things every stage uses

    @property
    def child(self):
        return self.programs[-1]

    def mark(self, name):
        self.report['timeline_s'][name] = round(self.clock() - self.began, 3)

    def stage(self, name):
        """For whoever watches the job from outside (the TCP server shows it)."""
        try:
            (self.output_dir / 'stage').write_text(name, encoding='ascii')
        except OSError:
            pass

    def save(self):
        (self.output_dir / 'job.json').write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def beat(self):
        """Renew the Nano's lease. Counted from before the exchange, so never later than it really was."""
        sent = self.clock()
        status = verified.heartbeat(self.nano)
        beats = self.report['heartbeats']
        beats['count'] += 1
        beats['longest_s'] = round(max(beats['longest_s'], self.clock() - sent), 3)
        if self.last_heartbeat is not None:
            beats['longest_gap_s'] = round(max(beats['longest_gap_s'], sent - self.last_heartbeat), 3)
        self.last_heartbeat = sent
        return status

    def start_program(self):
        """Start the spectrometer program and put it where the cleanup finds it. A stop signal that
        comes while it is being started is held back until it is on the list."""
        v = verified
        with v.INTERRUPTS.bus():
            # Interval 0: the pace of the frames is kept here alone; each waits for its go mark.
            self.programs.append(v.start_spectrum(
                self.app_dir, self.tools_dir, self.capture_dir, self.integration_ms,
                frames=BACKGROUND_FRAMES + self.loops, frame_interval_ms=0, triggered=True))
        return self.child

    def program_alive(self, when):
        if self.child.poll() is not None:
            raise RuntimeError('Spectral program in%sEnd:' % when + ended(self.child))

    def frame_in_time(self):
        """The exposure that was started last must be over by ``ready_by`` (the spectrometer program
        then says it is ready for the next frame). A stalled exposure never is: without this limit
        the supply stayed ON until the Nano's own 30 s trigger timeout."""
        if (self.triggered < self.loops and self.clock() >= self.ready_by
                and not verified.frame_ready(self.child, BACKGROUND_FRAMES + self.triggered + 1)):
            what = 'background spectrum' if not self.triggered else 'No. %d wheel spectrum Exposure of' % self.triggered
            raise TimeoutError('%swas not taken within the deadline; the final stage of the spectrum program:%s'
                               % (what, verified.spectrum_last_stage(self.child)))

    def synchronize(self):
        """The verified way: a new session, the parameters written and read back."""
        self.revision = verified.prepare(self.nano, self.off_power, self.data)['parameters']['revision']
        self.kept_session = None

    # ---- the stages, in their order

    def prepare(self):
        """HSPY OFF and Nano idle; the spectrometer program started; the Nano's parameters right."""
        v = verified
        self.stage('prepare')
        self.trigger.low()
        initial, idle = v.require_off(self.off_power, self.nano)
        # No limit on the supply's current-limit setting (the person's decision, 2026-10-07): it is whatever
        # the panel of the supply says. plasma_job.py, the verified fall-back, still wants 0.1 A at most.
        if initial['set_voltage_v'] != self.voltage:
            # The person's decision of 2026-10-07 (“There should be no error message, just useRS485Change it and enter itvoltage”): the set
            # voltage is written, not complained about. The supply is OFF with nothing at its output (require_off,
            # two lines up); it is not switched on unless it then reads back the voltage of the parameters, and
            # ramp() looks at the set voltage again with every reading.
            found = initial['set_voltage_v']
            write_set_voltage(self.off_power, self.power_write, self.voltage)
            self.report['set_voltage_written'] = {'from_v': found, 'to_v': self.voltage}
            self.mark('set_voltage_written')
            was = '%g' % found if type(found) in (int, float) else str(found)      # 1000.0 reads “1000”
            self.steps.append('HSPY The set voltage was originally %s V,Already in OFF The login password of %d V and read back' % (was, self.voltage))
        # The spectrometer needs most of a second to open: start it first, see to the Nano meanwhile.
        self.start_program()
        self.mark('spectrometer_started')
        self.revision = synchronized_revision(self.nano, idle, self.data)
        if self.revision is not None:
            self.kept_session = idle['session_id']
            self.steps.append('Nano config checked, same as the configuration file')
        else:
            self.synchronize()
            self.steps.append('Nano config Read back')
        self.mark('nano_ready')

    def spectrometer(self):
        """Wait until the spectrometer program has opened the spectrometer and is ready for its first
        frame. The Nano was ARMED meanwhile (so that half second is not waited a second time); its
        lease is kept here. The supply is still OFF and is not switched on before this returns:
        a spectrometer that does not open never gets high voltage."""
        def keep_lease():
            if self.clock() - self.last_heartbeat >= HEARTBEAT_SECONDS:
                self.beat()
        spectrometer_ready(self.child, self.start_program, self.steps, self.clock, self.sleep, idle=keep_lease)
        self.mark('spectrometer_ready')
        self.steps.append(f'spectrometer is ready (background {BACKGROUND_FRAMES} open＋signal {self.loops} open) ')

    def arm(self):
        """ARM the Nano with a fresh lease, while the spectrometer program is still opening the
        spectrometer. HSPY is OFF, and ARMED gives no pulse: only START and then D12 do."""
        v = verified
        stop_requested()  # the look before ARM
        if self.kept_session and not synchronized(self.nano.status(), self.kept_session, self.data):
            # The Nano restarted, or lost the session, since it was checked: it has to be told again.
            self.steps.append('Nano is no longer in the state just checked (it may have been restarted) and has been resynchronized.')
            self.synchronize()
        job_id = secrets.randbelow(0xffffffff) + 1
        try:
            self.beat()
            armed = v.command(self.nano, 3, job_id, self.revision)
        except v.ProtocolError as exc:
            if not self.kept_session:
                raise
            # The kept session did not answer as expected: synchronize the verified way, ask once more.
            self.steps.append('Nano has not been accepted session (%s) ,Resynced' % exc)
            self.synchronize()
            self.beat()
            armed = v.command(self.nano, 3, job_id, self.revision)
        if armed['state'] != 'ARMED' or armed['output_pin_sample']:
            raise RuntimeError('Nano ARM Incorrect readback')
        self.job_id = job_id
        self.mark('armed')

    def ramp(self):
        """HSPY ON and wait for the voltage. The Nano stays ARMED: it has not been STARTed and gives
        no pulse. No frame has been asked for yet: the background comes after this, with the voltage there."""
        v, clock = verified, self.clock
        self.program_alive('before turning on high voltage')
        self.stage('ramp')
        stop_requested()  # the look immediately before the supply is switched on
        # Written down before the write: an ON that was sent but interrupted, or not echoed, still shows.
        self.report['hv_on_attempted'] = True
        self.mark('hv_on_attempted')
        self.power_write(4, 1)
        self.mark('hspy_on')
        ramp_seconds, tolerance = v.voltage_window(self.voltage)
        voltage_deadline = clock() + ramp_seconds
        self.lcd_told = False
        while True:
            powered = self.power()
            measured = powered.get('voltage_v')
            # The output current is not judged here any more (the person's decision, 2026-10-07; it was 0.11 A):
            # the supply limits it to what its panel is set to.
            if (powered.get('valid') is not True or powered.get('run_stop_setting') != 1
                    or powered.get('set_voltage_v') != self.voltage):
                raise RuntimeError('HSPY ON Incorrect readback')
            self.program_alive('When boosting')
            if isinstance(measured, (int, float)) and abs(measured - self.voltage) <= tolerance:
                break
            if clock() >= voltage_deadline:
                raise RuntimeError(f'HSPY {ramp_seconds:.0f} The set voltage has not been reached within seconds. {self.voltage} V'
                                   f' (allowed ±{tolerance:.0f} V) ,last reading {measured} V')
            if clock() - self.last_heartbeat >= HEARTBEAT_SECONDS:
                self.beat()
            elif not self.lcd_told:
                # The supply has read back ON. The ramp is waiting time anyway: use it to put POWER ON
                # on the LCD, which the firmware wants to show before the first trigger.
                self.nano.report_power(1)
                self.lcd_told = True
            else:
                self.sleep(.05)
        self.mark('voltage_reached')
        self.steps.append('HSPY ON,voltage has arrived (no pulse yet)')

    def background(self):
        """Start the background frame. The supply is ON and at its voltage and the Nano has not been
        given a single pulse (it is ARMED, not even STARTed): the frame shows what the spectrometer
        sees with the high voltage on and no discharge. start() and pulse_trains() go on while it
        exposes; the first trigger waits for it to finish, and it has its own time limit."""
        self.program_alive('Before taking the background')
        self.stage('background')
        stop_requested()  # the look before the background frame
        _go_utc, go, _taken = expose(self.child, BACKGROUND_FRAMES, 'background spectrum', self.clock, self.sleep)
        self.mark('background_started')
        self.steps.append('The background spectrum begins to be exposed (high voltage is on, but there is no pulse yet)')
        self.due = go + self.again_s                           # about when the program is ready for loop 1
        self.ready_by = self.due + BACKGROUND_GRACE_SECONDS    # ... and when it must be

    def start(self):
        """START: from here on the Nano waits for D12 (30 s at most for the first trigger, and the
        longest exposure is 10 s). Sent while the background frame exposes: the exchange, the LCD
        report and a heartbeat take about half a second that would otherwise be waited after it.
        D12 has been LOW all the time and stays LOW until that frame is finished."""
        v = verified
        # The lease first: the last heartbeat may be 0.8 s old after the ramp, and up to 0.5 s more
        # went by while the background's go mark was taken (2 s is the limit).
        if self.clock() - self.last_heartbeat >= HEARTBEAT_SECONDS:
            self.beat()
        if not self.lcd_told:
            self.nano.report_power(1)
            self.lcd_told = True
        stop_requested()  # the look before START
        status = v.command(self.nano, 4, self.job_id, self.revision)
        if status['state'] != 'WAIT_TRIGGER':
            raise RuntimeError('Nano START not entered WAIT_TRIGGER')
        self.mark('start')
        self.steps.append('Nano WAIT_TRIGGER')
        return status

    def done(self, status):
        """Has the Nano finished the whole job? Raises when it says DONE with anything missing."""
        if status['state'] != 'DONE':
            return False
        if (status['pulses_completed'] != int(self.parameters['Cycle']) * self.loops
                or status['loops_completed'] != self.loops
                or status['output_pin_sample'] or self.triggered != self.loops):
            raise RuntimeError('Nano DONE pulse/Loop readback is incomplete')
        self.report['nano_done'] = status
        return True

    def pulse_trains(self, status):
        """One exposure and one pulse train per loop, each loop one period after the one before,
        until the Nano reports DONE. ``status`` is the Nano's last answer; None right after a
        trigger, when nothing has been read since."""
        v, clock, sleep = verified, self.clock, self.sleep
        # The background frame first (it has just been started), then the loops.
        signal_deadline = (clock() + self.again_s + self.loops * self.period_s * PACE_SLACK
                           + self.integration_s + SIGNAL_SPARE_SECONDS)
        job_deadline = clock() + NANO_JOB_SECONDS
        start_at = 0.0       # the earliest the next frame may be started: one period after the last
        train_end = None     # when the pulse train that was triggered last ends
        next_due = self.due  # about when the next frame is started; None after the last one
        while True:
            now = clock()
            if now >= job_deadline:
                raise TimeoutError('Nano works over %g Second' % NANO_JOB_SECONDS)
            if now >= signal_deadline:
                raise TimeoutError('Spectrum acquisition exceeds the period set by the number of rounds; final stage:' + v.spectrum_last_stage(self.child))
            if self.triggered < self.loops:
                self.program_alive('Before triggering in all rounds')
                self.frame_in_time()
            if status is not None:
                if status['fault'] != 'NONE' or status['state'] == 'FAULT':
                    raise RuntimeError('Nano Fault: ' + status['fault'])
                if self.done(status):
                    return
                if status['state'] not in ('WAIT_TRIGGER', 'RUNNING') or (status['state'] == 'RUNNING' and train_end is None):
                    raise RuntimeError('Nano is not in the proper state for this job (%s) ' % status['state'])
                if status['state'] == 'WAIT_TRIGGER':
                    # This answer is newer than the last trigger. Had the Nano seen that edge, it would
                    # be running or have counted the loop: a D12 wire that is off shows here at once.
                    if status['loops_completed'] < self.triggered:
                        raise RuntimeError('D12 The trigger has not been Nano received (No. %d wheel)' % self.triggered)
                    if status['loops_completed'] > self.triggered or self.triggered >= self.loops:
                        raise RuntimeError('Nano ’s round number readback does not match the sent trigger Part of')
                    # The Nano waits for D12 and stays that way: leave the bus alone until the frame is due.
                    until = quiet_until(now, self.last_heartbeat, next_due)
                    if now >= until:
                        status = self.beat()
                        continue
                    number = BACKGROUND_FRAMES + self.triggered + 1
                    # (ready_by has not come, or the frame is ready: frame_in_time() saw to that above)
                    limit = until if v.frame_ready(self.child, number) else min(until, self.ready_by)
                    if not watch(self.child, number, start_at, limit, clock, sleep):
                        continue
                    stop_requested()  # the look before every trigger
                    if not self.triggered:
                        self.stage('signal')  # the background frame is finished: from here on, pulses
                    shot, go, edge = fire(self.trigger, self.child, number, f'No. {self.triggered + 1} wheel',
                                          self.lead, clock, sleep)
                    self.triggered += 1
                    self.report['triggers'].append(dict(shot, frame=self.triggered))
                    if self.triggered == 1:
                        self.mark('first_pulse')
                    start_at, train_end = go + self.period_s, edge + self.train_s
                    self.ready_by = go + self.integration_s + FRAME_GRACE_SECONDS
                    next_due = max(go + self.again_s, start_at) if self.triggered < self.loops else None
                    status = None
                    continue
            # A pulse train runs, or its end has not been seen yet: ask right when it should have
            # ended, and send what heartbeats are needed so that none is in the way then.
            beat_at = beat_in_train(now, self.last_heartbeat, train_end, next_due)
            wake = train_end + .002 if beat_at is None else min(beat_at, train_end + .002)
            if beat_at is not None and now >= beat_at:
                status = self.beat()
            elif now < wake:
                sleep(min(.05, wake - now))
            else:
                status = self.nano.status()

    def measure(self):
        self.prepare()
        self.arm()
        self.spectrometer()               # ready for its first frame; only now may the supply come on
        self.ramp()                       # the supply is ON and at its voltage; no pulse yet
        self.background()                 # only now the background frame is started
        self.pulse_trains(self.start())   # the Nano waits for D12; the first trigger waits for that frame
        self.mark('nano_done')

    # ---- the end of every run, whatever happened

    def switch_off(self):
        """D12 LOW, HSPY OFF with readback, Nano STOP, then the wait for 0 V: the verified OFF
        sequence. Returns what could not be confirmed (empty: confirmed off)."""
        v, report = verified, self.report
        errors = v.stop_outputs(self.power, self.power_write, self.nano, self.drain, self.trigger)
        # Only now, when the outputs have been told to go off: the TCP server takes "off" in this
        # file as the sign that a stop has arrived, and no file write may come before the OFF.
        self.stage('off')
        try:
            self.drain()
            final_power, off_samples, power_zero = v.wait_power_zero(self.power)
            report['off_settle_samples'] = off_samples
            report['final_power'] = final_power
            final_nano = self.nano.status()
            report['final_nano'] = final_nano
            if (not power_zero or final_nano.get('valid') is not True
                    or final_nano.get('output_command_high') is not False
                    or final_nano.get('output_pin_sample') is not False):
                voltage = None if final_power is None else final_power.get('voltage_v')
                run_stop = None if final_power is None else final_power.get('run_stop_setting')
                errors.append(f'final OFF/D13 LOW Readback failed:HSPY Run/Stop={run_stop}, '
                              f'output={voltage} V; Nano D13={final_nano.get("output_pin_sample")}; '
                              f'Already waiting {off_samples[-1]["elapsed_s"]} Second')
        except Exception as exc:
            errors.append('Final readback:' + str(exc))
        report['off_confirmed'] = not errors
        self.mark('off_confirmed' if not errors else 'off_not_confirmed')
        return errors

    def collect_frames(self):
        """After a completed run, with the outputs already off: wait for the last exposure and sort
        the frames into background/ and signal/."""
        try:
            left = max(0.0, self.ready_by - self.clock())  # the last exposure may still be running
            capture = collect(self.child, time.monotonic() + left + COLLECT_SECONDS, BACKGROUND_FRAMES + self.loops)
            self.report.update(split_frames(capture, self.capture_dir, self.output_dir, BACKGROUND_FRAMES))
            self.steps.append('Nano DONE, Spectrum completed')
        except Exception as exc:
            self.report['error'] = str(exc)

    def end_programs(self):
        """No spectrometer program may outlive the run: it would hold the spectrometer. Returns the
        sentence for the person when one did not end as it should, else ''."""
        results = [end_program(program) for program in self.programs]
        left = [result[6:] for result in results if result.startswith('left: ')]
        if left:
            note = 'The spectrum program cannot be ended and the spectrometer is still occupied (%s) ' % '; '.join(left)
        elif 'killed' in results:
            note = 'The spectrum program did not respond to the stop signal and has been forced to terminate. After all'
        else:
            note = ''
        self.report['spectrometer_ended'] = not left
        self.report['spectrometer_end'] = note or 'ended'
        if note:
            self.steps.append(note)
        return note

    def finish(self):
        """The OFF sequence first, then the frames and the spectrometer program, then the report.
        Returns the text of what went wrong, '' when nothing did."""
        report = self.report
        cleanup_errors = self.switch_off()
        if self.completed:
            self.collect_frames()
        note = self.end_programs()
        report['cleanup_errors'] = cleanup_errors
        report['finished_at'] = utc().isoformat()
        behind = add_exposure_times(self.capture_dir, report['triggers'], BACKGROUND_FRAMES)
        if behind:
            # The spectrometer program logged its SDK call after D12 had risen: the lead is too short.
            report['exposure_after_trigger'] = behind
            self.steps.append('Note: No. %s wheel is later than the trigger, and the spectrum may not capture the beginning of the pulse (--lead-ms too small)'
                              % ', '.join(str(frame) for frame in behind))
        try:
            (self.output_dir / 'job.pid').unlink()
        except OSError:
            pass
        # No error means: the run was completed (anything else, an interrupt included, leaves one)
        # and every frame was there (collect_frames leaves one otherwise).
        report['valid'] = not cleanup_errors and 'error' not in report
        self.save()
        cause = report.get('error', '')
        if cleanup_errors:
            # Only what concerns the outputs is called "not confirmed"; the reason for the stop follows.
            return ('Work stop not confirmed:' + '; '.join(cleanup_errors) + ('; cause: ' + cause if cause else '')
                    + ('; ' + note if note else ''))
        return cause + ('; ' + note if cause and note else note)

    def run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / 'job.pid').write_text(str(os.getpid()), encoding='ascii')
        failure = None
        try:
            try:
                self.measure()
                self.completed = True
            except BaseException as exc:
                failure = exc
            # From here on, cancel signals are recorded but cannot interrupt the OFF sequence.
            verified.INTERRUPTS.cleaning = True
        except BaseException as exc:
            # The stop signal, in the instant after something else had ended the run and before the
            # line above. Signals raise at most once (on_signal), so nothing can hit these two lines.
            failure = exc
            verified.INTERRUPTS.cleaning = True
        if isinstance(failure, Exception):
            self.report['error'] = explained(str(failure) or type(failure).__name__)
        elif failure is not None:  # KeyboardInterrupt, SystemExit: the same OFF sequence, then it goes on up
            self.report['error'] = 'work was interrupted (%s) ' % type(failure).__name__
        problem = self.finish()
        if failure is not None and not isinstance(failure, Exception):
            raise failure
        if problem:
            raise RuntimeError(problem)
        return self.report


def run_measure(nano, power, power_write, trigger, parameters, app_dir, tools_dir, output_dir,
                clock=time.monotonic, drain=lambda: None, sleep=time.sleep, lead=EXPOSURE_LEAD_SECONDS):
    """One bounded background + pulse/spectrum job; always try to stop Nano and HSPY."""
    return Measurement(nano, power, power_write, trigger, parameters, app_dir, tools_dir, output_dir,
                       clock, drain, sleep, lead).run()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app-dir', type=Path, default=Path.home() / 'Desktop/Plasmic/app')
    parser.add_argument('--tools-dir', type=Path, default=Path.home() / 'Desktop/Plasmic/nano_lcd_v1_1/rpi_tools')
    parser.add_argument('--port', default='/dev/RS485')
    parser.add_argument('--outdir', type=Path, default=None, help='where the job writes (required for a measurement)')
    parser.add_argument('--set-voltage', type=int, default=None, metavar='V',
                        help="only this: with the supply OFF, write its set voltage and read it back; needs --execute")
    parser.add_argument('--lead-ms', type=float, default=EXPOSURE_LEAD_SECONDS * 1000,
                        help='D12 rises this long after the spectrometer program has taken the go mark (0-200; default 5)')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-high-voltage', action='store_true')
    args = parser.parse_args()
    setting = args.set_voltage is not None
    if setting and not args.execute:
        parser.error('--set-voltage requires --execute')
    if not setting and not (args.execute and args.confirm_high_voltage):
        parser.error('requires --execute and --confirm-high-voltage')
    if not setting and args.outdir is None:
        parser.error('--outdir is required')
    if not 0 <= args.lead_ms <= LONGEST_LEAD_SECONDS * 1000:
        parser.error('--lead-ms must be 0-200')
    v = load_tools(args.tools_dir, args.app_dir)
    if setting and not 1 <= args.set_voltage <= v.MAX_VOLTAGE:
        parser.error('--set-voltage must be 1-%d' % v.MAX_VOLTAGE)
    import fcntl
    from HSPY_Read_Status import read_status
    parameters = None if setting else json.loads((args.app_dir / 'parameter.json').read_text(encoding='utf-8-sig'))
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)
    with open('/tmp/hm-hmi-rs485.lock', 'w') as serial_lock:
        fcntl.flock(serial_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with v.open_port(args.port, 1.0) as port:
            nano = v.GuardedClient(port)

            def power():
                with v.INTERRUPTS.bus():
                    v.hspy_turnaround()
                    return read_status(port, address=1, timeout=1.0)

            def power_write(register, value):
                return v.checked_power_write(port, register, value)

            if setting:
                # Nothing is switched on in this branch. The supply must be OFF with nothing at its output and the
                # Nano idle (the verified tool's own condition for writing a set voltage), or nothing is written.
                off_power = v.off_check_reader(power, lambda: v.drain_bus(port))
                found = v.require_off(off_power, nano)[0]
                result = {'valid': True, 'changed': found['set_voltage_v'] != args.set_voltage,
                          'previous_set_voltage_v': found['set_voltage_v'], 'set_voltage_v': args.set_voltage,
                          'hspy': found}
                if result['changed']:
                    result['hspy'] = write_set_voltage(off_power, power_write, args.set_voltage)
            else:
                result = run_measure(nano, power, power_write,
                                     v.Trigger(), parameters, args.app_dir, args.tools_dir, args.outdir,
                                     drain=lambda: v.drain_bus(port), lead=args.lead_ms / 1000)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'valid': False, 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
