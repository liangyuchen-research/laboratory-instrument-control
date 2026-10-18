#!/usr/bin/env python3
"""Plasmic Electronic control — Schedule measurement and each round of process (SPEC B.5, B.6; AMENDMENTS M5.2, M11) .

Runner replace 1.4.1 of Schedule: Time grid, period rules, continuity 3 times, leaving only the most recent 2000 periods,
Every sentence written into the event record remains the same; the difference is that each time period now runs ”one round” Recipe”,is not just a discharge.

  schedule (one thread runner) 
    Wait until the period → Get the device (Devices.acquire('round')) → Check before starting → Step by step (steps.StepExecutor) 
    → Closing: All pumps will be shut down if they are to stop, fail or are stopped (Devices.all_off) → write record → also equipment.
  Pause: Stop between two steps or while waiting; the pump that is on during the process stops first, and uses the remaining time to restart when continuing.
  Stop: Any time; a stopped or failed round will not be continued and will never be re-discharged.
  blank period (M5.2) : Blank first in the first period, then every N completed samples are printed again.
  standard calibration (M5.2) : A schedule with only one period, the steps of that round are Analyst.check_plan() discharge.

When the program is restarted, the schedules and processes in progress will be recorded as ”interrupted” and will never be continued by themselves.

Lock: Runner.lock only protects two records (schedule and this round); do not touch the device or write to the hard disk when holding it.
Make a copy before writing to the hard disk. Only one person can write the same file at a time (and 1.4.1 of Schedule._save Same) .
"""
from __future__ import annotations

import copy
import sys
import threading
import time
import uuid

import devices as interlock
import measurements
import migrate
import recipe
import schedule
import steps
from devices import Blocked, Busy, reason
from pumpboard import PumpBoardError
from store import FLOW_RECIPE_ID, now_iso

# Module attributes on purpose: read when used, so tests can shorten them.
ROUND_SAVE_SECONDS = 1.0     # during a wait the round record is written at most this often
PAUSE_GRACE_SECONDS = 5      # a slot that came due this long ago while the schedule was paused is skipped
HOLD_TICK_S = 0.05
CLOSE_JOIN_SECONDS = 5.0
KEEP_MESSAGES = 200
WINDOW_PAGE, WINDOW_ROWS = 6, 12     # state.round.steps_window: the page of the current step and the next one

ROUND_ACTIVE = ('running', 'pausing', 'paused', 'stopping')
SLOT_OF_ROUND = {'completed': 'done', 'stopped': 'stopped', 'failed': 'failed'}

# ---- texts kept from 1.4.1 (pc/app.py, class Schedule) ---------------------------------------------------
T_ALREADY = 'Already has a schedule in progress'
T_STOPPED = 'Schedule stopped'
T_RESTART = 'The program is restarted, the schedule has been interrupted and will not automatically resume. Please restart the schedule after confirming the device.'
T_RESTART_SLOT = 'The program was closed or the computer was restarted during measurement; please go to the site to confirm the equipment status.'
E_RESTART = 'The last scheduled measurement was interrupted due to program restart and will not be automatically restored.'
E_UNSAVED = 'The schedule record was not archived successfully:{detail}'
E_NOT_STARTED = 'Schedule No. {n} times failed to start:{detail}'
T_START_FAILURES = 'Continuous {count} times: Read back{detail}'
T_ROUND_FAILED = 'No. {n} measurements did not end normally and the schedule has been stopped:{detail}'
T_OVERRUN = 'No. {n} times have not ended yet. There is no measurement during this period.'
E_OVERRUN = 'Schedule No. {n} times skipped: The last measurement timed out'
T_INTERNAL = 'Scheduling internal error:{detail}'
T_INTERNAL_SLOT = 'There is an internal error in the program. This measurement did not end normally. Please go to the site to confirm the equipment status.'
E_END = 'Scheduled measurement ends ({status}) : Complete {done} Second-rate'
W_ESTIMATE = 'One measurement is estimated to be approximately {seconds} seconds, longer than the interval: some periods will be skipped because the last time has not ended yet'
# ---- texts of SPEC A.5, B.5, B.6, B.9 and AMENDMENTS M5.2 -------------------------------------------------
T_NO_OPERATOR = 'Please select the operator first'
T_TICKS = 'You must first check each item in the confirmation window'
T_NO_START = 'Cannot start scheduling now'
T_NO_RECIPE = 'Recipe does not exist'
T_RECIPE_INVALID = 'Recipe failed the inspection'
T_FORMAT = 'The schedule setting format is incorrect.'
T_INTERVAL = 'interval {minutes} minute ratio Recipe waiting time {seconds} Seconds are still short, and it times out every time'
T_BLANK_FORMAT = 'Blank setting format is incorrect'
T_BLANK_MISSING = 'Blank Recipe does not exist'
T_BLANK_SAME = 'Blank Recipe Cannot be compared with samples Recipe same'
T_BLANK_EVERY = 'Insert blanks for every few samples: must be 1–100 integer'
T_BLANK_NO_PLASMA = 'Blank Recipe Must content measurement steps (plasma_run) '
E_START = 'Scheduled measurement starts:{recipe},Every {minutes} minutes, first time {first},{end}'
E_START_BLANK = ',Blank {recipe} Every {every} samples'
E_START_HV = ',{voltage} V (High voltage confirmed)'
T_NO_PAUSE = 'There is currently no schedule that can be paused'
T_NOT_PAUSED = 'Schedule is not in pause state'
T_NO_RESUME = 'The schedule cannot be continued now'
T_MANUAL_PUMP = 'The manually started pump is still running, please stop it first.'
T_NO_PROMPT = 'There are currently no fluid changes waiting for confirmation.'
T_PAUSED_SLOT = 'The schedule is in paused state when the period arrives.'
E_PAUSED_SLOT = 'Schedule No. {n} times skipped: schedule is paused'
E_PAUSE = 'Pause pressed'
E_PAUSE_STEP = 'Pause has been pressed: Pause after completing this step'
E_RESUME = 'Pressed to continue'
E_HELD = 'Process has been suspended'
E_HELD_PUMPS = '; Stopped {motors},will restart when continuing'
E_RESUMED = 'Process continues'
T_STEP_FAILED = 'No. {n} step ({name}) failed:{detail}'
T_PUMPS_LEFT = 'The pumps did not stop at the end of one round, they all stopped. The program on'
T_OFF_UNCONFIRMED = 'Stop not confirmed ({who}) — Please use the physical switch to cut off the power'
T_CLOSING_ERROR = 'An error occurred at the end of the process:{detail}'
E_ROUND = 'Process{word}: {recipe} ({position}) '
ROUND_WORD = {'completed': 'Complete', 'failed': 'failed', 'stopped': 'Stopped'}
T_ROUND_INTERRUPTED = 'The program is closed or the computer is restarted during execution; please go to the site to confirm the device status.'
E_ROUND_UNSAVED = 'The process record was not archived successfully:{detail}'
T_BOARD_LOST = 'Pump control board is not connected ({text}) '
T_BOARD_RESTARTED = 'The pump control board was restarted or reconnected during this round of process.'
E_RESTART_STOP = 'The previous round of process was interrupted: Requested Answer a question on RPi Turn off high voltage (HSPY OFF＋Nano STOP) '
E_RESTART_STOP_FAILED = 'The last round of process was interrupted, but the stop command was not sent If there is important data on RPi: {detail}'
E_HOOK = 'Error in preparation before discharge (does not affect measurement):{detail}'
E_MEASURE_FAILED = 'The measurement result is not calculated (does not affect the process):{detail}'
# alarms: (key, priority, area, kind, text); priority 1 = Emergency, 2 = high, 3 = middle
A_ROUND_FAILED = ('round_failed:{id}', 2, 'Process', 'event', 'process failed:{detail}')
A_SCHED_FAILED = ('sched_failed:{id}', 2, 'Schedule', 'event', 'Schedule has stopped:{detail}')
A_SCHED_INTERRUPTED = ('sched_interrupted:{id}', 2, 'Schedule', 'event', 'The schedule has been interrupted and will not be automatically restored. Please restart the schedule after confirming the device.')
A_SCHED_SKIPPED = ('sched_skipped:{id}', 3, 'Schedule', 'event', 'Sometimes a segment is skipped because the last one has not ended yet.')
A_SLOT_NOT_STARTED = ('slot_not_started:{id}', 3, 'Schedule', 'event', 'No. {n} times failed to start:{detail}')
A_BLANK_QC = ('blank_qc:{id}', 3, 'Measure', 'event', 'Blank solution exceeds baseline threshold:{detail}')
E_BLANK_UNCHECKED = 'Blank is not compared:{reason}'
E_SAMPLE_CONC = 'sample concentration:{values} mg/L'
# run-log result texts (SPEC D.2, AMENDMENTS M5.2)
R_NOT_MEASURED = 'Not measured'
R_NO_PLASMA = 'No measurement steps'
R_PEAK = 'highest peak {nm:.1f} nm'
R_PEAK_FRAMES = 'highest peak {nm:.1f} nm · signal {frames} open'
R_BLANK = {'ok': 'Blank ✓ baseline is normal', 'fail': 'Blank ⚠ Beyond baseline threshold'}
R_BLANK_UNCHECKED = 'Blank (not compared)'
R_UNCALIBRATED, R_ANALYSIS_FAILED, R_NO_CONC = 'Uncorrected', 'Analysis failed', 'Unconverted concentration'
R_CALIBRATION = 'Calibration · Completed {done}/{total} wheel'
# calibration (AMENDMENTS M5.2)
T_NO_ANALYST = 'This program does not have the correction function enabled'
T_NO_CALIBRATION_START = 'Cannot start calibration now'
E_CAL_START = 'Standard calibration starts:{recipe},{levels} concentration,common {runs} wheel (high pressure confirmed)'
E_CAL_DISCARD = 'Correction {level} Pre-run round completed (not included in regression)'
E_CAL_ROUND = 'Correction {level} No. {n} round completed'
E_CAL_ANALYZED = 'Calibration analysis completed:{fits}'
E_CAL_NO_FIT = 'No metal has made a valid return'
E_CAL_ANALYSIS_FAILED = 'Calibration analysis failed:{detail}'
E_CAL_UNSAVED = 'The calibration record was not updated successfully:{detail}'
T_CAL_NOT_STARTED = 'Calibration did not start:{detail}'
T_CAL_FAILED = 'Calibration did not end normally:{detail}'
E_CAL_END = 'Standard calibration completed ({status}) '
E_CAL_ROUND_END = 'standard calibration{word}: {recipe}'
# round.current.detail of a plasma_run, by the RPi's job.progress.phase (SPEC B.5, AMENDMENTS M1)
PLASMA_DETAIL = {'prepare': 'Prepare', 'ramp': 'High voltage is on (Not discharged yet) ,Wait for the voltage to arrive',
                 'background': 'captures the background spectrum (high voltage is on, but not discharged yet) After', 'off': 'Turn off high voltage and confirm',
                 'stopping': 'Stopping, waiting for readback of high voltage shutdown', 'finishing': 'Read spectrum'}
D_SIGNAL = 'Discharging·Signal {done}/{total} open'
D_DELAY = 'Waiting {waited}/{total} Second'
D_READING = 'Read spectrum'
BOARD_OPS = ('dose_volume', 'dose_reverse', 'dose_weight', 'clean', 'syringe_init', 'syringe_aspirate', 'syringe_dispense')
METALS = measurements.METALS
CALIBRATION_SPEC = {'start': {'mode': 'now'}, 'interval_minutes': 1, 'end': {'mode': 'count', 'count': 1}}


class ScheduleEnded(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class SlotNotStarted(Exception):
    """This slot's round could not begin; nothing has moved."""


def _g(value):
    try:
        return '%g' % float(value)
    except (TypeError, ValueError):
        return str(value)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def recipe_facts(*checked):
    """What a schedule uses, over one or two checked Recipes (uses_* = either)."""
    checked = [row for row in checked if isinstance(row, dict)]
    facts = {key: any(bool(row.get(key)) for row in checked)
             for key in ('uses_pump', 'uses_rpi', 'uses_plasma', 'uses_scale')}
    facts['uses_weight'] = any(step.get('action') == 'dose_weight' for row in checked for step in row.get('steps') or [])
    return facts


def step_result_text(step):
    """The results column of the step table (SPEC E.4 D3)."""
    if step.get('status') == 'failed':
        return str(step.get('error') or '')
    result = step.get('result') or {}
    action = step.get('action')
    if action == 'dose_weight' and _number(result.get('dispensed_g')):
        return '%.2f g (error %+.2f g) ' % (result['dispensed_g'], result.get('error_g') or 0.0)
    if action == 'scale_read' and _number(result.get('grams')):
        return '%.3f g' % result['grams']
    if action in ('plasma_run', 'spectrum_capture') and _number(result.get('peak_nm')):
        return R_PEAK.format(nm=result['peak_nm'])
    return ''


def result_text(record):
    """`result_text` of a round's run-log row (SPEC D.2, AMENDMENTS M5.2)."""
    jobs = [job for job in (record.get('plasma') or {}).get('jobs') or [] if job.get('kind') == 'plasma' and job.get('saved')]
    if record.get('mode') == 'calibration':
        total = max([step.get('rounds') or 0 for step in record.get('steps') or []] or [0])
        return R_CALIBRATION.format(done=len(jobs), total=total)
    peak = jobs[-1].get('peak_nm') if jobs else None
    rows = record.get('measurements') or []
    if rows:
        last = rows[-1]
        if last.get('kind') == 'blank':
            return R_BLANK.get(last.get('qc'), R_BLANK_UNCHECKED)
        values = [(metal, (last.get('conc') or {}).get(metal)) for metal in METALS]
        values = [(metal, value) for metal, value in values if _number(value)]
        if values:
            return ' · '.join('%s %.3g' % pair for pair in values) + ' mg/L'
        why = last.get('conc_reason')
        head = R_UNCALIBRATED if why == 'Correction has not been applied yet' else R_ANALYSIS_FAILED if why == 'Analysis failed' else R_NO_CONC
        return head + (' · ' + R_PEAK.format(nm=peak) if _number(peak) else '')
    if jobs:
        frames = jobs[-1].get('signal_frames')
        return R_PEAK_FRAMES.format(nm=peak, frames=frames) if _number(peak) and frames is not None else R_NOT_MEASURED
    actions = [step.get('action') for step in record.get('steps') or []]
    if 'plasma_run' in actions:
        return R_NOT_MEASURED            # the Recipe measures, but this round did not get that far
    for step in reversed(record.get('steps') or []):
        result = step.get('result') or {}
        if step.get('action') == 'dose_weight' and step.get('status') == 'done' and _number(result.get('dispensed_g')):
            label = str(step.get('target_label') or step.get('target') or '')
            name = label.split(' · ', 1)[1] if ' · ' in label else label
            return '%s %.2f g' % (name, result['dispensed_g'])
    return R_NO_PLASMA


class _Run:
    """What one schedule needs while its thread lives."""

    def __init__(self, record, sample, blank, ack, calibration=None):
        self.record = record
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.sample = sample             # {'id', 'name', 'sha256'}
        self.blank = blank               # the same plus 'every', or None
        self.ack = ack                   # the hv_off_checked tick of the start dialog: False, or Devices.tick()
        self.calibration = calibration   # None, or {'id', 'plan', 'steps', 'phases', 'facts', 'csv', 'prime', 'finished'}


class RoundContext:
    """One running round: what steps.StepExecutor gets as `ctx`."""

    def __init__(self, runner, run, record, facts):
        self.runner, self.run, self.record = runner, run, record
        self.stop = run.stop
        self.ack = run.ack
        self.pumps = {}                  # motor -> {flow, direction, planned_s, used_s, started, parked}
        self.round_id = record['id']
        self.recipe_name = record['recipe']['name']
        self.operator = record['operator']
        self.slot = record['slot']
        self.uses_pump = bool(facts.get('uses_pump'))
        self.uses_rpi = bool(facts.get('uses_rpi'))
        self.current = None              # the step that is being done, or is about to be
        self.live = {}                   # numbers for round.current.detail
        self.prompt_event = None
        self.board_generation = None     # PumpBoard.view()['generation'] when the round began

    # -- what every step looks at --------------------------------------------------------------------
    def checkpoint(self):
        """Stop -> Stopped; the board in trouble -> StepFailed; pause -> hold. Returns the seconds held."""
        if self.stop.is_set():
            raise steps.Stopped('')
        if self.uses_pump:
            pump = self.runner.pump
            late = pump.deadline_tripped('round')
            if late:
                raise steps.StepFailed(late)
            view = pump.view()
            if view.get('link') != 'connected':
                raise steps.StepFailed(T_BOARD_LOST.format(text=view.get('text') or ''))
            if self.board_generation is not None and view.get('generation') != self.board_generation:
                raise steps.StepFailed(T_BOARD_RESTARTED)        # a new connection, or the board restarted
        if self.run.pause.is_set():
            return self.hold()
        return 0.0

    def hold(self):
        """PAUSE: stop this round's pumps, wait for continue or Stop, start the pumps again for the time left."""
        began = time.perf_counter()
        runner, pump = self.runner, self.runner.pump
        stopped = []
        for motor in sorted(self.pumps):
            entry = self.pumps[motor]
            if entry.get('parked'):
                continue
            try:
                out = pump.stop_motor(motor)
            except PumpBoardError as exc:
                raise steps.StepFailed(exc.text) from None
            ran = out.get('ran_s')
            if ran is None:
                raise steps.StepFailed(steps.T_RUN_VANISHED.format(motor=motor))
            entry['used_s'] += ran
            entry['parked'] = True
            stopped.append(motor)
        # A pump whose planned time is used up is not started again: its pump_off is the next thing to come.
        again = [motor for motor in stopped if self.pumps[motor]['planned_s'] - self.pumps[motor]['used_s'] > 0.05]
        runner._held(self, again)
        while self.run.pause.is_set():
            if self.stop.is_set():
                raise steps.Stopped('')
            time.sleep(HOLD_TICK_S)
        if self.stop.is_set():
            raise steps.Stopped('')
        if again:
            try:
                runner.devices.permit_motion(self.ack)
            except Blocked as exc:
                raise steps.StepFailed(steps.blocked_text(exc)) from None
            for motor in again:
                entry = self.pumps[motor]
                left = entry['planned_s'] - entry['used_s']
                try:
                    pump.run(motor, entry['flow'], entry['direction'], left + steps.RUN_MARGIN_S, 'round')
                except PumpBoardError as exc:
                    if exc.code == 'stopped' and self.stop.is_set():
                        raise steps.Stopped('') from None
                    raise steps.StepFailed(exc.text) from None
                entry.update(started=time.perf_counter(), parked=False)
        runner._resumed(self)
        return time.perf_counter() - began

    def progress(self, **facts):
        with self.runner.lock:
            self.live.update(facts)
        self.runner._save_round()        # at most once a second, and only when something changed

    # -- what a step reports --------------------------------------------------------------------------
    def say(self, text, level='info'):
        self.runner._say(self.record, text, level)

    def alarm(self, key, priority, area, text, kind='event'):
        self.runner._alarm_raw(key, priority, area, text, kind)

    def job_started(self, step, reply):
        """The job id goes into the step and the record at once: a crash after this still shows what ran."""
        with self.runner.lock:
            self.live.update(job=reply.get('job'), estimate_s=reply.get('estimate_s'))
            step['result'] = {'job': reply.get('job')}
            self.record['plasma_job'] = reply.get('job')
            self.runner._touch(self.record)
        self.runner._save_round(force=True)

    def add_job(self, entry):
        with self.runner.lock:
            self.record['plasma']['jobs'].append(dict(entry))
            self.runner._touch(self.record)

    def add_weight(self, n, name, grams):
        with self.runner.lock:
            self.record['weights'].append({'n': n, 'name': name, 'grams': grams})
            self.runner._touch(self.record)

    def before_discharge(self, step):
        self.runner._before_discharge(self, step)

    def measured(self, step, job, meta):
        return self.runner._measured(self, step, job)

    def prompt_open(self, text):
        event = threading.Event()
        with self.runner.lock:
            self.prompt_event = event
            self.record['prompt'] = {'text': text, 'since': now_iso()}
            self.runner._touch(self.record)
        self.runner._save_round(force=True)
        return event

    def prompt_close(self):
        with self.runner.lock:
            self.prompt_event = None
            self.record['prompt'] = None
            self.runner._touch(self.record)


class Runner:
    """Schedule measurement: one round of a Recipe per slot on a fixed time grid (see schedule.py).

    The operator confirms once, when the schedule starts; after that every slot starts by itself, also
    while nobody looks at the screen (a round is not tied to the page). What ends a schedule early:
    STOP, a round that does not end cleanly (the state of the equipment is then uncertain), three
    slots in a row that could not start, and this program ending. A schedule is never resumed after
    a restart and a discharge is never repeated.
    """
    START_FAILURES = 3
    KEEP_SLOTS = 2000

    def __init__(self, store, alarms, devices, rpi, pump, operator_getter, analyst=None, before_discharge=None):
        self.store, self.alarms, self.devices, self.rpi, self.pump = store, alarms, devices, rpi, pump
        self.operator_getter = operator_getter
        self.analyst = analyst                       # None: nothing of AMENDMENTS M5.2 runs
        self.before_discharge = before_discharge     # AMENDMENTS M11; production passes nothing
        self.lock = threading.RLock()                # the schedule record and the round record
        self._schedule_write = threading.Lock()      # one writer of schedule.json at a time
        self._round_write = threading.Lock()         # one writer of the round's file at a time
        self.stop_request = threading.Event()        # of the schedule that runs now (a new one per schedule)
        self.pause_request = threading.Event()
        self.thread = None
        self.record = None                           # the current / last schedule
        self.round = None                            # the running round, or the last one of this program run
        self._run = None
        self._live = None                            # RoundContext while a round holds the equipment
        self._round_dirty = False
        self._round_saved = 0.0
        self._round_unsaved = ''
        self.executor = steps.StepExecutor(devices, pump, rpi, store)
        self._restart()

    # ============================================================================================
    # Small things
    # ============================================================================================
    def _operator(self):
        try:
            return str(self.operator_getter() or '')
        except Exception:
            return ''

    def _event(self, text, level='info', actor=None):
        try:
            self.store.event(text, level, actor)
        except Exception:
            pass

    def _say(self, record, text, level='info', actor=None):
        """An event that belongs to a round: also kept in the round's own list (the last 200)."""
        with self.lock:
            record['messages'].append({'time': now_iso(), 'text': text})
            del record['messages'][:-KEEP_MESSAGES]
            self._round_dirty = True
        self._event(text, level, actor)

    def _alarm_raw(self, key, priority, area, text, kind='event'):
        try:
            self.alarms.raise_(key, priority, area, text, kind)
        except Exception as exc:
            self._event('alarm was not recorded successfully (%s) : %s' % (exc, text), 'error')

    def _alarm(self, alarm, **values):
        key, priority, area, kind, text = alarm
        self._alarm_raw(key.format(**values), priority, area, text.format(**values)[:400], kind)

    def _touch(self, record):
        """Caller holds the lock: the round record changed."""
        record['rev'] = record.get('rev', 0) + 1
        self._round_dirty = True

    # ============================================================================================
    # Disk
    # ============================================================================================
    def _write_schedule(self, record):
        try:
            self.store.save_schedule(record)
        except OSError as exc:
            self._event(E_UNSAVED.format(detail=exc), 'warn')

    def _save_schedule(self):
        # The screen's thread and the schedule's own thread both save. One at a time, each with the
        # record as it is when its turn comes (as 1.4.1's Schedule._save).
        with self._schedule_write:
            with self.lock:
                record = copy.deepcopy(self.record)
            if record is not None:
                self._write_schedule(record)

    def _save_round(self, force=False):
        """Write the round record. Not forced: only when it changed and the last write is a second ago."""
        with self.lock:
            if self.round is None:
                return
            if not force and (not self._round_dirty
                              or time.perf_counter() - self._round_saved < ROUND_SAVE_SECONDS):
                return
        with self._round_write:
            with self.lock:
                record = copy.deepcopy(self.round)
                self._round_dirty = False
                self._round_saved = time.perf_counter()
            try:
                self.store.save_round(record)
                self._round_unsaved = ''
            except (OSError, ValueError) as exc:
                if str(exc) != self._round_unsaved:      # said once, not at every step
                    self._round_unsaved = str(exc)
                    self._event(E_ROUND_UNSAVED.format(detail=exc), 'warn')

    # ============================================================================================
    # Program start: what the last run left behind is never resumed
    # ============================================================================================
    def _restart(self):
        found, uses_rpi = None, False
        record = self.store.load_schedule()
        if record is not None and record.get('status') in schedule.ACTIVE:
            counts = record.setdefault('counts', {})
            for slot in record['slots']:
                if slot.get('status') == 'running':
                    slot.update(status='failed', note=T_RESTART_SLOT)
                    counts['failed'] = counts.get('failed', 0) + 1
            record.update(status='interrupted', finished_at=now_iso(), next_slot_at=None, error=T_RESTART)
            self._write_schedule(record)
            self._event(E_RESTART, 'warn')
            found = record.get('id')
        self.record = record
        try:
            last = self.store.newest_round()
        except Exception:
            last = None
        if isinstance(last, dict) and last.get('status') in ROUND_ACTIVE and isinstance(last.get('steps'), list):
            for step in last['steps']:
                if isinstance(step, dict) and step.get('status') == 'running':
                    step.update(status='failed', finished_at=now_iso(), error=T_ROUND_INTERRUPTED)
                elif isinstance(step, dict) and step.get('status') == 'pending':
                    step['status'] = 'skipped'
            last.update(status='interrupted', finished_at=now_iso(), error=T_ROUND_INTERRUPTED, prompt=None)
            last['rev'] = (last.get('rev') or 0) + 1
            try:
                self.store.save_round(last)
                self.store.append_runlog(self._round_row(last))
            except Exception as exc:
                self._event(E_ROUND_UNSAVED.format(detail=exc), 'warn')
            uses_rpi = bool(last.get('uses_rpi')) or any(
                isinstance(step, dict) and step.get('action') in recipe.RPI_ACTIONS for step in last['steps'])
            found = found or last.get('schedule_id') or last.get('id')
        if found:
            self._alarm(A_SCHED_INTERRUPTED, id=found)
        if uses_rpi:
            # One stop, when the RPi can be reached. (The RPi has stopped a discharge by itself when the
            # program that started it went away; the pump board gets its X when it is connected.)
            threading.Thread(target=self._restart_stop, name='runner-restart', daemon=True).start()

    def _restart_stop(self):
        try:
            self.rpi.stop()
            self._event(E_RESTART_STOP, 'warn')
        except Exception as exc:
            self._event(E_RESTART_STOP_FAILED.format(detail=exc), 'warn')

    # ============================================================================================
    # Views
    # ============================================================================================
    @property
    def active(self):
        record = self.record
        return record is not None and record.get('status') in schedule.ACTIVE

    def ownership(self):
        """For Devices: 'busy' (the schedule may move something at any moment) · 'paused_round' (a paused
        round keeps the equipment) · 'paused' (paused between rounds: the equipment is free) · ''."""
        with self.lock:
            if self._live is not None:
                return 'paused_round' if (self.round or {}).get('status') == 'paused' else 'busy'
            record = self.record
            if record is None or record.get('status') not in schedule.ACTIVE:
                return ''
            return 'busy' if record['status'] in schedule.BUSY else 'paused'

    @property
    def busy(self):
        """True while a schedule or a round keeps manual operations and maintenance tools out."""
        return self.ownership() in ('busy', 'paused_round')

    def round_uses_rpi(self):
        """For Devices: does a round hold the equipment whose Recipe has a step for the RPi?"""
        with self.lock:
            live = self._live
            return live is not None and live.uses_rpi

    def uses_recipe(self, recipe_id):
        """Is this Recipe part of the schedule that is active now? (It cannot be deleted then.)"""
        with self.lock:
            record = self.record
            if record is None or record.get('status') not in schedule.ACTIVE:
                return False
            ids = {(record.get('recipe') or {}).get('id'), (record.get('blank') or {}).get('recipe_id'),
                   (record.get('prime_recipe') or {}).get('id')}
            return recipe_id in ids - {None}

    def view(self, slots=60):
        """The schedule for the screen: the record of SPEC D.2 with only the latest slots; None when there is none."""
        with self.lock:
            if self.record is None:
                return None
            shown = {key: value for key, value in self.record.items() if key != 'slots'}
            shown['slots'] = self.record['slots'][-slots:]
            shown = copy.deepcopy(shown)
        # Records written before 2.0.0 (or by 2.0.0 without these keys) read as an ordinary schedule.
        shown.setdefault('kind', 'schedule')
        shown.setdefault('calibration_id', None)
        shown.setdefault('blank', None)
        for slot in shown['slots']:
            if isinstance(slot, dict):
                slot.setdefault('kind', 'sample')
        return shown

    def _current_step(self, record):
        number = record.get('step') or 0
        rows = record.get('steps') or []
        return rows[number - 1] if 0 < number <= len(rows) else None

    def round_view(self):
        """state.round of API.md C.6 (plus the keys of AMENDMENTS M5.3); None when no round has run since start."""
        with self.lock:
            record = self.round
            if record is None:
                return None
            rows = record['steps']
            step = self._current_step(record)
            active = record['status'] in ROUND_ACTIVE
            live = dict(self._live.live) if self._live is not None and self._live.record is record else {}
            number = step['n'] if step else 0
            first = ((max(number, 1) - 1) // WINDOW_PAGE) * WINDOW_PAGE + 1
            items = [{'n': row['n'], 'text': row.get('text', ''), 'phase': row.get('phase'), 'status': row['status'],
                      'started_at': row.get('started_at'), 'finished_at': row.get('finished_at'),
                      'result_text': step_result_text(row), 'error': row.get('error') or ''}
                     for row in rows[first - 1:first - 1 + WINDOW_ROWS]]
            shown = {'id': record['id'], 'rev': record['rev'], 'status': record['status'],
                     'schedule_id': record['schedule_id'], 'slot': record['slot'], 'slots_planned': record['slots_planned'],
                     'operator': record['operator'],
                     'recipe': {'id': record['recipe']['id'], 'name': record['recipe']['name']},
                     'started_at': record['started_at'], 'finished_at': record['finished_at'],
                     'step': number, 'step_count': record['step_count'],
                     'round': step.get('round', 1) if step else 1, 'rounds': record['rounds'],
                     'phase': step.get('phase', 0) if step else 0, 'phases': copy.deepcopy(record['phases']),
                     'current': None, 'uses_plasma': record['uses_plasma'], 'uses_pump': record['uses_pump'],
                     'paused_pumps': list(record.get('paused_pumps') or []),
                     'plasma_job': record.get('plasma_job'), 'error': record['error'],
                     'stop_report': copy.deepcopy(record['stop_report']),
                     'steps_window': {'from': first, 'items': items},
                     'mode': record['mode'], 'calibration_id': record.get('calibration_id'),
                     'sample_kind': record['sample_kind'], 'prompt': copy.deepcopy(record.get('prompt')),
                     'measurements': copy.deepcopy(record['measurements'])}
            current = dict(step) if step is not None and active else None
        if current is not None:
            # The step that is being done; between two steps, and while a round is paused there, the one that
            # comes next (its sentence, no start time yet).
            detail, eta = str(current.get('text') or ''), None
            if current['status'] == 'running':
                detail, eta = self._detail(current, live)    # reads the latest views; nothing is sent
            shown['current'] = {'n': current['n'], 'action': current['action'], 'text': current.get('text', ''),
                                'detail': detail, 'started_at': current.get('started_at'), 'eta_s': eta}
        return shown

    def _detail(self, step, live):
        """(the live line of Overview, seconds this step is expected to take or None)"""
        action, text = step.get('action'), str(step.get('text') or '')
        if action == 'delay':
            total = live.get('total')
            if not _number(total):                   # the wait has only just begun
                try:
                    total = float(step.get('value'))
                except (TypeError, ValueError):
                    return text, None
            return D_DELAY.format(waited=int(live.get('waited') or 0), total=_g(total)), total
        if action in BOARD_OPS:
            op = self.pump.view().get('op') or {}
            if op.get('by') == 'round' and op.get('text'):
                return str(op['text']), op.get('eta_s')
            return text, None
        if action == 'plasma_run' and live.get('job'):
            job = (self.rpi.latest() or {}).get('job') or {}
            eta = live.get('estimate_s')
            if job.get('id') != live['job']:
                return text, eta
            if job.get('state') != 'running':
                return D_READING, eta
            progress = job.get('progress') or {}
            if progress.get('phase') == 'signal':
                done, total = progress.get('frames_done'), progress.get('frames_total')
                return D_SIGNAL.format(done=done if done is not None else 0, total=total if total is not None else '?'), eta
            return PLASMA_DETAIL.get(progress.get('phase'), text), eta
        return text, None

    def calibration_view(self):
        """state.calibration_run (AMENDMENTS M5.3): the calibration that runs now, or None."""
        with self.lock:
            record = self.round
            if record is None or record.get('mode') != 'calibration' or record['status'] not in ROUND_ACTIVE:
                return None
            step = self._current_step(record) or (record['steps'][0] if record['steps'] else {})
            tag = step.get('calibration') or {}
            return {'id': record.get('calibration_id'), 'status': record['status'], 'recipe_name': record['recipe']['name'],
                    'level': tag.get('level'), 'level_name': tag.get('level_name'), 'run': tag.get('run'),
                    'runs': record['rounds'], 'prompt': copy.deepcopy(record.get('prompt'))}

    # ============================================================================================
    # Preview and start
    # ============================================================================================
    def _settings(self):
        try:
            return self.pump.view().get('settings') or {}
        except Exception:
            return {}

    def _checked(self, recipe_id):
        """A published Recipe, checked with the settings of this moment (SPEC A.3, rule R11).
        KeyError: there is none. ValueError: its file was changed. The check result may be not valid."""
        record = self.store.recipe(recipe_id)
        hardware = self.pump.hardware()
        settings = self._settings()
        validated = recipe.validate(record['csv'], hardware, settings.get('syringe_ul') or recipe.DEFAULT_SYRINGE_UL)
        if record['id'] == FLOW_RECIPE_ID:       # the step list of the schedule page: “Steps n”, not “No. n column” (M23)
            validated = recipe.as_flow(validated)
        status = self.rpi.latest() or {}
        parameter = (status.get('snapshot') or {}).get('parameter') or {}
        integration = parameter.get('integration_ms') if parameter.get('valid') is True else None
        estimate = recipe.estimate(validated, hardware, status.get('estimate_s'), settings.get('syringe_speed_ml_s'),
                                   integration)
        return {'id': record['id'], 'name': record['name'], 'sha256': record['sha256'], 'csv': record['csv'],
                'validated': validated, 'estimate_seconds': estimate}

    def _valid(self, recipe_id):
        row = self._checked(recipe_id)
        if not row['validated'].get('valid'):
            raise ValueError(row['validated'].get('error') or T_RECIPE_INVALID)
        return row

    def _blank(self, raw, sample_id):
        """The blank setting of a schedule body: None, or the checked blank Recipe plus `every`."""
        if raw is None:
            return None
        if not isinstance(raw, dict) or set(raw) - {'recipe_id', 'every', 'enabled'}:
            raise ValueError(T_BLANK_FORMAT)
        if raw.get('enabled') is False:
            return None
        every = raw.get('every')
        if type(every) is not int or not 1 <= every <= 100:
            raise ValueError(T_BLANK_EVERY)
        if raw.get('recipe_id') == sample_id:
            raise ValueError(T_BLANK_SAME)
        try:
            row = self._valid(raw.get('recipe_id'))
        except KeyError:
            raise ValueError(T_BLANK_MISSING) from None
        if not row['validated'].get('uses_plasma'):
            raise ValueError(T_BLANK_NO_PLASMA)
        row['every'] = every
        return row

    def _needs(self, facts, ack=None):
        """Which tick rows the start dialog must show (and the server then asks for).
        `hv_off_checked`: the Recipe moves a pump while the high voltage cannot be read (Devices.hv_ack())."""
        ack = self.devices.hv_ack() if ack is None else ack
        return {'high_voltage': bool(facts['uses_plasma']), 'liquid': bool(facts['uses_pump']),
                'hv_off_checked': bool(facts['uses_pump']) and bool(ack['need'])}

    def _timing(self, raw, rows):
        """Stage 6: the form and the interval rule. -> (spec, estimate in seconds, warnings)"""
        spec = schedule.validate(raw)
        estimate = max(row['estimate_seconds'] for row in rows)
        warnings = []
        if schedule.planned_total(spec) != 1:        # with one slot the interval is never used
            delay = max(row['validated'].get('delay_seconds') or 0 for row in rows)
            if spec['interval_seconds'] <= delay:
                raise ValueError(T_INTERVAL.format(minutes=spec['interval_minutes'], seconds=_g(delay)))
            if estimate > spec['interval_seconds']:
                warnings.append(W_ESTIMATE.format(seconds=int(round(estimate))))
        return spec, int(round(estimate)), warnings

    def _voltage(self):
        status = self.rpi.latest() or {}
        parameter = (status.get('snapshot') or {}).get('parameter') or {}
        return parameter.get('voltage_v') if parameter.get('valid') is True else None

    def preview(self, body):
        """What the form on the screen would do, without starting anything (API.md C.4.2)."""
        if not isinstance(body, dict):
            raise ValueError(T_FORMAT)
        sample = self._valid(body.get('recipe_id'))              # KeyError -> 404
        blank = self._blank(body.get('blank'), sample['id'])
        rows = [sample] + ([blank] if blank else [])
        spec, estimate, warnings = self._timing(body.get('schedule'), rows)
        facts = recipe_facts(*[row['validated'] for row in rows])
        last = schedule.last_index(spec)
        checked = sample['validated']
        notes = []
        for row in rows:
            notes.extend(text for text in row['validated'].get('warnings') or [] if text not in notes)
        ack = self.devices.hv_ack()
        needs = self._needs(facts, ack)
        needs['hv_ack_text'] = ack['text'] if needs['hv_off_checked'] else ''    # the sentence of that tick row
        return {'spec': spec, 'planned_total': schedule.planned_total(spec),
                'last_slot_at': None if last is None else schedule.slot_time(spec, last).isoformat(),
                'estimate_seconds': estimate,
                'recipe': {'id': sample['id'], 'name': sample['name'], 'step_count': checked['step_count'],
                           'rounds': checked['rounds'], 'uses_pump': facts['uses_pump'], 'uses_rpi': facts['uses_rpi'],
                           'uses_plasma': facts['uses_plasma'], 'liquids': copy.deepcopy(checked.get('liquids') or []),
                           'warnings': list(checked.get('warnings') or [])},
                'blank': None if blank is None else {'recipe_id': blank['id'], 'recipe_name': blank['name'],
                                                     'every': blank['every']},
                'voltage_v': self._voltage(), 'flow': self.rpi.flow_view(),
                'warnings': notes + warnings,
                # The tick `hv_off_checked` is asked for by `needs`; a blocker here would hide the dialog.
                'blockers': self.devices.start_problems(facts, ack=True),
                'needs': needs}

    def _launch(self, record, run):
        """Caller has checked everything: the record becomes the schedule and its thread starts."""
        with self.lock:
            if self.active:
                raise RuntimeError(T_ALREADY)
            self.stop_request, self.pause_request = run.stop, run.pause
            self.record, self._run = record, run
            self.thread = threading.Thread(target=self._loop, args=(run,), name='runner', daemon=True)
            self.thread.start()
        self._save_schedule()

    def start(self, body):
        """Start scheduling. The stages of SPEC B.5 in their order; the first that fails answers."""
        body = body if isinstance(body, dict) else {}
        operator = self._operator()
        if not operator:                                                             # 1
            raise ValueError(T_NO_OPERATOR)
        with self.lock:
            if self.active:                                                          # 2
                raise RuntimeError(T_ALREADY)
        try:
            sample = self._valid(body.get('recipe_id'))                              # 3
        except KeyError:
            raise ValueError(T_NO_RECIPE) from None
        blank = self._blank(body.get('blank'), sample['id'])
        rows = [sample] + ([blank] if blank else [])
        facts = recipe_facts(*[row['validated'] for row in rows])
        confirm = body.get('confirm') if isinstance(body.get('confirm'), dict) else {}
        needs = self._needs(facts)
        if any(needed and confirm.get(key) is not True for key, needed in needs.items()):     # 4
            raise ValueError(T_TICKS)
        # A tick counts only when the dialog had to ask for it: `hv_off_checked` is for "the high voltage cannot be read".
        confirmed = {key: bool(needs[key]) and confirm.get(key) is True for key in ('high_voltage', 'liquid', 'hv_off_checked')}
        ack = self.devices.tick(confirmed['hv_off_checked'])     # given now; every round of this schedule shows it
        problems = self.devices.start_problems(facts, ack)                           # 5
        if problems:
            raise Blocked(T_NO_START, problems)
        spec, estimate, _warnings = self._timing(body.get('schedule'), rows)        # 6
        last = schedule.last_index(spec)
        voltage = self._voltage()
        record = {'version': 2, 'id': uuid.uuid4().hex, 'kind': 'schedule', 'calibration_id': None,
                  'status': 'waiting', 'created_at': now_iso(), 'finished_at': None, 'operator': operator,
                  'recipe': {key: sample[key] for key in ('id', 'name', 'sha256')},
                  'blank': None if blank is None else {'recipe_id': blank['id'], 'recipe_name': blank['name'],
                                                       'every': blank['every'], 'sha256': blank['sha256']},
                  'uses_pump': facts['uses_pump'], 'uses_rpi': facts['uses_rpi'], 'uses_plasma': facts['uses_plasma'],
                  'confirmed': confirmed,
                  'voltage_v': voltage, 'estimate_seconds': estimate, 'spec': spec,
                  'planned_total': schedule.planned_total(spec),
                  'last_slot_at': None if last is None else schedule.slot_time(spec, last).isoformat(),
                  'next_index': 0, 'next_slot_at': spec['first_slot'], 'slots': [], 'counts': {}, 'error': ''}
        run = _Run(record, {key: sample[key] for key in ('id', 'name', 'sha256')},
                   None if blank is None else {key: blank[key] for key in ('id', 'name', 'sha256', 'every')}, ack)
        self._launch(record, run)
        end = spec['end']
        text = E_START.format(recipe=sample['name'], minutes=spec['interval_minutes'], first=spec['first_slot'][11:16],
                              end={'count': f'common {end.get("count")} Second-rate', 'until': f'arrive {end.get("time")}',
                                   'continuous': 'Continue until stop is pressed'}[end['mode']])
        if blank is not None:
            text += E_START_BLANK.format(recipe=blank['name'], every=blank['every'])
        if facts['uses_plasma']:
            text += E_START_HV.format(voltage=voltage)
        self._event(text, 'warn', operator)
        return {'accepted': True, 'schedule': self.view()}

    # ---- standard calibration (AMENDMENTS M5.2) -----------------------------------------------------------------
    def _calibration_recipe(self, raw, key):
        """One of a plan's two Recipes in the shape Analyst.check_plan() wants, or None (not chosen / not there)."""
        recipe_id = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(recipe_id, str) or not recipe_id:
            return None
        try:
            return self._checked(recipe_id)
        except (KeyError, ValueError):
            return None

    def start_calibration(self, body):
        """Start calibration: body = {plan, confirm: {high_voltage, liquid, hv_off_checked}}."""
        if self.analyst is None:
            raise RuntimeError(T_NO_ANALYST)
        body = body if isinstance(body, dict) else {}
        operator = self._operator()
        if not operator:
            raise ValueError(T_NO_OPERATOR)
        with self.lock:
            if self.active:
                raise RuntimeError(T_ALREADY)
        raw = body.get('plan')
        standard = self._calibration_recipe(raw, 'recipe_id')
        prime = self._calibration_recipe(raw, 'prime_recipe_id')
        check = self.analyst.check_plan(raw, standard, prime)        # ValueError with the original's texts
        plan = check['plan']
        prime = prime if plan.get('prime_recipe_id') else None
        facts = recipe_facts(standard['validated'], prime['validated'] if prime else None)
        facts['uses_rpi'] = facts['uses_plasma'] = True
        confirm = body.get('confirm') if isinstance(body.get('confirm'), dict) else {}
        needs = dict(self._needs(facts), high_voltage=True)
        if any(needed and confirm.get(key) is not True for key, needed in needs.items()):
            raise ValueError(T_TICKS)
        confirmed = {key: bool(needs[key]) and confirm.get(key) is True for key in ('high_voltage', 'liquid', 'hv_off_checked')}
        ack = self.devices.tick(confirmed['hv_off_checked'])
        problems = self.devices.start_problems(facts, ack)
        if problems:
            raise Blocked(T_NO_CALIBRATION_START, problems)
        spec = schedule.validate(CALIBRATION_SPEC)
        schedule_id = uuid.uuid4().hex
        created = self.analyst.create(plan, standard, prime, operator, schedule_id)   # the record, status 'running'
        voltage = self._voltage()
        record = {'version': 2, 'id': schedule_id, 'kind': 'calibration', 'calibration_id': created['id'],
                  'status': 'waiting', 'created_at': now_iso(), 'finished_at': None, 'operator': operator,
                  'recipe': {key: standard[key] for key in ('id', 'name', 'sha256')},
                  'prime_recipe': None if prime is None else {key: prime[key] for key in ('id', 'name', 'sha256')},
                  'blank': None,
                  'uses_pump': facts['uses_pump'], 'uses_rpi': True, 'uses_plasma': True,
                  'confirmed': confirmed,
                  'voltage_v': voltage, 'estimate_seconds': check.get('estimated_seconds'), 'spec': spec,
                  'planned_total': 1, 'last_slot_at': spec['first_slot'], 'next_index': 0,
                  'next_slot_at': spec['first_slot'], 'slots': [], 'counts': {}, 'error': ''}
        run = _Run(record, {key: standard[key] for key in ('id', 'name', 'sha256')}, None, ack,
                   calibration={'id': created['id'], 'plan': plan, 'steps': check['steps'], 'phases': check['phases'],
                                'facts': facts, 'csv': standard['csv'], 'runs': check.get('runs'),
                                'prime': None if prime is None else {key: prime[key] for key in ('id', 'name', 'sha256', 'csv')},
                                'finished': False})
        try:
            self._launch(record, run)
        except RuntimeError as exc:
            self._finish_calibration(run, 'failed', str(exc))
            raise
        self._event(E_CAL_START.format(recipe=standard['name'], levels=len(plan['levels']), runs=check.get('runs')),
                    'warn', operator)
        return {'accepted': True, 'calibration_id': created['id'], 'schedule': self.view()}

    def confirm_prompt(self):
        """“Confirm fluid change”: the bottle-swap prompt of a calibration that waits now."""
        with self.lock:
            live = self._live
            if live is None or live.prompt_event is None or (self.round or {}).get('prompt') is None:
                raise RuntimeError(T_NO_PROMPT)
            live.prompt_event.set()
        return {'accepted': True}

    def _finish_calibration(self, run, status, error=''):
        """Tell the Analyst how the calibration's round ended; a completed one is analysed there."""
        calibration = run.calibration
        if calibration is None or calibration.get('finished') or self.analyst is None:
            return
        calibration['finished'] = True
        try:
            result = self.analyst.finish(calibration['id'], status, error)
        except Exception as exc:
            self._event(E_CAL_UNSAVED.format(detail=exc), 'error')
            return
        state = (result or {}).get('status')
        if state == 'analyzed':
            fits = ((result.get('analysis') or {}).get('fits') or {})
            parts = ['%s R² %.4f' % (metal, fits[metal]['r2']) for metal in METALS
                     if (fits.get(metal) or {}).get('valid') and _number(fits[metal].get('r2'))]
            self._event(E_CAL_ANALYZED.format(fits=', '.join(parts) or E_CAL_NO_FIT), 'info' if parts else 'warn')
        elif state == 'analysis_failed':
            self._event(E_CAL_ANALYSIS_FAILED.format(detail=result.get('error') or ''), 'error')

    # ============================================================================================
    # Pause, resume, stop
    # ============================================================================================
    def pause(self):
        """pause PAUSE. Waiting for a slot: paused at once (the equipment is free). In a round: the round
        pauses at its next checkpoint and keeps the equipment."""
        with self.lock:
            record, run = self.record, self._run
            if record is None or run is None or record.get('status') not in ('waiting', 'running'):
                raise RuntimeError(T_NO_PAUSE)
            run.pause.set()
            in_round = self._live is not None
            record['status'] = 'pausing' if in_round else 'paused'
            if in_round and self.round is not None and self.round['status'] == 'running':
                self.round['status'] = 'pausing'
                self._touch(self.round)
            status = record['status']
        self._save_schedule()
        self._event(E_PAUSE_STEP if in_round else E_PAUSE, 'info', self._operator() or None)
        return {'accepted': True, 'status': status}

    def _resume_problems(self, run_ack, holding):
        problems = []
        owner = self.devices.owner
        if owner is not None and owner['kind'] == 'manual':
            problems.append(reason('manual_active', T_MANUAL_PUMP))
        elif owner is not None and owner['kind'] == 'tool':
            problems.append(reason('tool_active', interlock.T_TOOL_ACTIVE))
        view = self.rpi.hv_view()
        if view['known'] and not view['idle']:
            problems.extend(reason('hv_on', text, interlock.FIX_STOP) for text in view['reasons'])
        elif holding:
            # (RESUME has no dialog: the hint "tick it in the dialog" would point at nothing.)
            problems.extend(dict(row, fix='') if row.get('fix') == interlock.FIX_HV_TICK else row
                            for row in self.devices.motion_problems(run_ack))
        board = self.pump.view()
        if self.pump.motion_uncertain:
            problems.append(reason('pump_uncertain', interlock.T_PUMP_UNCERTAIN))
        elif self.pump.in_use and board.get('link') != 'connected':
            problems.append(reason('pump_link', interlock.T_PUMP_LINK.format(text=board.get('text') or ''), interlock.FIX_USB))
        return problems

    def resume(self):
        """continue RESUME. Refused while a manual pump still runs, while the high voltage is not off, or
        while the pump board is lost."""
        with self.lock:
            record, run = self.record, self._run
            if record is None or run is None or record.get('status') not in ('paused', 'pausing'):
                raise RuntimeError(T_NOT_PAUSED)
            holding = bool(self._live is not None and (self.round or {}).get('paused_pumps'))
        problems = self._resume_problems(run.ack, holding)
        if problems:
            raise Blocked(T_NO_RESUME, problems)
        with self.lock:
            if self.record is not record or record.get('status') not in ('paused', 'pausing'):
                raise RuntimeError(T_NOT_PAUSED)
        # Written before the pause flag falls (and not under the lock: it goes to disk): the round's own line
        # “Process continues” then always comes after this one in the event log.
        self._event(E_RESUME, 'info', self._operator() or None)
        with self.lock:
            if self.record is not record or record.get('status') not in ('paused', 'pausing'):
                raise RuntimeError(T_NOT_PAUSED)     # (stopped in this very instant; STOP's own line follows)
            run.pause.clear()
            in_round = self._live is not None
            record['status'] = 'running' if in_round else 'waiting'
            if in_round and self.round is not None and self.round['status'] == 'pausing':
                self.round['status'] = 'running'
                self._touch(self.round)
            status = record['status']
        self._save_schedule()
        return {'accepted': True, 'status': status}

    def request_stop(self):
        """No further slot will start, and a round that runs is told to stop. Instant; touches no device.
        -> {'schedule': a schedule was active, 'round': a round was running}"""
        with self.lock:
            record, run = self.record, self._run
            active = record is not None and run is not None and record.get('status') in schedule.ACTIVE
            in_round = active and self._live is not None
            if active:
                run.stop.set()
                record['status'] = 'stopping'
                if in_round and self.round is not None and self.round['status'] in ROUND_ACTIVE:
                    self.round['status'] = 'stopping'
                    self._touch(self.round)
        return {'schedule': bool(active), 'round': bool(in_round)}

    def close(self):
        """The program ends: stop, and give the thread a moment to leave everything off."""
        self.request_stop()
        self.stop_request.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(CLOSE_JOIN_SECONDS)

    # ============================================================================================
    # The schedule's thread
    # ============================================================================================
    def _slot(self, run, index, status, **extra):
        with self.lock:
            record = run.record
            slots = record['slots']
            if slots and slots[-1]['index'] == index:
                slots[-1].update(status=status, **extra)
            else:
                slots.append({'index': index, 'planned_at': schedule.slot_time(record['spec'], index).isoformat(),
                              'status': status, **extra})
            if status != 'running':
                counts = record['counts']
                counts[status] = counts.get(status, 0) + 1
            # A months-long schedule keeps its totals in counts and only the recent slots in detail.
            del slots[:-self.KEEP_SLOTS]

    def _phase(self, run, status):
        with self.lock:
            if run.stop.is_set():
                return
            if run.pause.is_set():
                status = 'paused' if status == 'waiting' else 'pausing'
            run.record['status'] = status

    def _wait_until(self, run, due):
        """'run' when the slot is due; 'skipped_paused' when it came due while the schedule was paused."""
        while not run.stop.is_set():
            now = schedule.local_now()
            remaining = (due - now).total_seconds()
            if run.pause.is_set():
                if -remaining > PAUSE_GRACE_SECONDS:
                    return 'skipped_paused'
                run.stop.wait(.1)
                continue
            if remaining <= 0:
                return 'run'
            run.stop.wait(min(.2, remaining))
        raise ScheduleEnded('stopped', T_STOPPED)

    def _slot_row(self, run, index, status, note, kind):
        """The run-log line of a slot that did not run."""
        record = run.record
        ref = run.blank if kind == 'blank' and run.blank else run.sample
        return {'kind': 'slot', 'time': schedule.slot_time(record['spec'], index).isoformat(),
                'operator': record.get('operator', ''), 'recipe_name': ref['name'], 'schedule_id': record['id'],
                'slot': index + 1, 'slots_planned': record.get('planned_total'), 'status': status, 'finished_at': None,
                'round_id': None, 'spectra': [], 'result_text': R_NOT_MEASURED, 'note': note, 'sample_kind': kind,
                'measurements': []}

    def _log_slot(self, run, index, status, note, kind):
        try:
            self.store.append_runlog(self._slot_row(run, index, status, note, kind))
        except Exception as exc:
            self._event(E_ROUND_UNSAVED.format(detail=exc), 'warn')

    def _loop(self, run):
        record = run.record
        spec = record['spec']
        last = schedule.last_index(spec)
        index, failures = 0, 0
        final, message = 'completed', ''
        since_blank = None               # samples that finished since the last blank; None: no blank yet
        skipped_told = False

        def kind_now():
            if run.blank is None:
                return 'sample'
            return 'blank' if since_blank is None or since_blank >= run.blank['every'] else 'sample'

        try:
            while last is None or index <= last:
                due = schedule.slot_time(spec, index)
                with self.lock:
                    record.update(next_index=index, next_slot_at=due.isoformat())
                self._phase(run, 'waiting')
                self._save_schedule()
                if self._wait_until(run, due) == 'skipped_paused':
                    self._slot(run, index, 'skipped_paused', note=T_PAUSED_SLOT, kind=kind_now())
                    self._event(E_PAUSED_SLOT.format(n=index + 1), 'warn')
                    self._log_slot(run, index, 'skipped_paused', T_PAUSED_SLOT, kind_now())
                    index += 1
                    continue
                kind = kind_now()
                try:
                    status, note = self._run_round(run, index, kind)
                except SlotNotStarted as exc:
                    failures += 1
                    self._slot(run, index, 'not_started', note=str(exc)[:200], kind=kind)
                    self._event(E_NOT_STARTED.format(n=index + 1, detail=exc), 'warn')
                    self._alarm(A_SLOT_NOT_STARTED, id=record['id'], n=index + 1, detail=exc)
                    self._log_slot(run, index, 'not_started', str(exc)[:200], kind)
                    if run.calibration is not None:
                        raise ScheduleEnded('failed', T_CAL_NOT_STARTED.format(detail=exc)) from None
                    if failures >= self.START_FAILURES:
                        raise ScheduleEnded('failed', T_START_FAILURES.format(count=failures, detail=exc)) from None
                else:
                    failures = 0
                    if run.stop.is_set():
                        raise ScheduleEnded('stopped', T_STOPPED)
                    if status != 'done':
                        # After a round that did not end cleanly nobody knows the state of the equipment.
                        if run.calibration is not None:
                            raise ScheduleEnded('failed', T_CAL_FAILED.format(detail=note))
                        raise ScheduleEnded('failed', T_ROUND_FAILED.format(n=index + 1, detail=note))
                    if kind == 'blank':
                        since_blank = 0
                    elif since_blank is not None:
                        since_blank += 1
                ran = index
                index, missed = schedule.next_due_index(spec, index, schedule.local_now())
                for skipped in missed:
                    if last is not None and skipped > last:
                        break
                    note = T_OVERRUN.format(n=ran + 1)
                    self._slot(run, skipped, 'skipped_overrun', note=note, kind=kind_now())
                    self._event(E_OVERRUN.format(n=skipped + 1), 'warn')
                    self._log_slot(run, skipped, 'skipped_overrun', note, kind_now())
                    if not skipped_told:
                        skipped_told = True
                        self._alarm(A_SCHED_SKIPPED, id=record['id'])
        except ScheduleEnded as ended:
            final, message = ended.status, str(ended)
        except Exception as exc:  # never leave a schedule that looks active without a thread behind it
            final, message = 'failed', T_INTERNAL.format(detail=repr(exc))
        finally:
            with self.lock:
                record.update(status=final, finished_at=now_iso(), next_slot_at=None,
                              error=message if final == 'failed' else '')
                done = record['counts'].get('done', 0)
            self._save_schedule()
            word = schedule.STATUS_LABEL[final]
            if run.calibration is not None:
                # A calibration whose round never came to an end of its own (stopped while it waited, say).
                self._finish_calibration(run, 'failed' if final == 'failed' else 'stopped', message)
                self._event(E_CAL_END.format(status=word) + (f'.{message}' if final == 'failed' else ''),
                            'error' if final == 'failed' else 'info')
            else:
                self._event(E_END.format(status=word, done=done) + (f'.{message}' if final == 'failed' else ''),
                            'error' if final == 'failed' else 'info')
            if final == 'failed':
                self._alarm(A_SCHED_FAILED, id=record['id'], detail=message)

    # ============================================================================================
    # One round
    # ============================================================================================
    def _round_plan(self, run, ref):
        """What this round is to do, checked now (the Recipe may have been deleted, a setting changed)."""
        calibration = run.calibration
        if calibration is not None:
            return {'steps': calibration['steps'], 'phases': calibration['phases'], 'facts': calibration['facts'],
                    'csv': calibration['csv'], 'rounds': calibration.get('runs') or 1}
        try:
            row = self._checked(ref['id'])
        except KeyError:
            raise SlotNotStarted(T_NO_RECIPE) from None
        except ValueError as exc:
            raise SlotNotStarted(str(exc)) from None
        if row['sha256'] != ref['sha256']:
            raise SlotNotStarted(T_NO_RECIPE)
        checked = row['validated']
        if not checked.get('valid'):
            raise SlotNotStarted(checked.get('error') or T_RECIPE_INVALID)
        return {'steps': checked['steps'], 'phases': checked['phases'], 'facts': recipe_facts(checked),
                'csv': row['csv'], 'rounds': checked['rounds']}

    def _run_round(self, run, index, kind):
        """(slot status, note) of one round: 'done', 'stopped' or 'failed'. SlotNotStarted when it could not begin."""
        record = run.record
        ref = run.blank if kind == 'blank' and run.blank else run.sample
        total = record.get('planned_total')
        position = f'No. {index + 1}/{total} Second-rate' if total else f'No. {index + 1} Second-rate'
        round_id = uuid.uuid4().hex
        try:
            self.devices.acquire('round', round_id, f'{ref["name"]} {position}')
        except Busy as exc:
            raise SlotNotStarted(str(exc)) from None
        ctx = None
        try:
            plan = self._round_plan(run, ref)
            problems = self.devices.start_problems(plan['facts'], run.ack)
            if problems:
                raise SlotNotStarted(interlock.reasons_text(problems))
            ctx = self._begin_round(run, index, kind, ref, plan, round_id)
        finally:
            if ctx is None:
                with self.lock:
                    self._live = None
                self.devices.release(round_id)       # nothing has moved
        ended = False
        try:
            status, error = self._execute(run, ctx, position)
            ended = True
        finally:
            if not ended:
                # A fault of this program while the round was being closed. Whatever it was: outputs off,
                # and the equipment is never left held.
                try:
                    self.devices.all_off(True, True)
                except Exception:
                    self._last_resort_off()
                self._slot(run, index, 'failed', finished_at=now_iso(), note=T_INTERNAL_SLOT)
            with self.lock:
                if self._live is ctx:
                    self._live = None
            self.devices.release(round_id)
        with self.lock:
            jobs = [job['job'] for job in ctx.record['plasma']['jobs'] if job.get('kind') == 'plasma']
        self._slot(run, index, SLOT_OF_ROUND[status], finished_at=now_iso(), note=error[:200],
                   **({'job': jobs[-1]} if jobs else {}))
        return SLOT_OF_ROUND[status], error

    def _begin_round(self, run, index, kind, ref, plan, round_id):
        record = run.record
        calibration = run.calibration
        hello = getattr(self.rpi, 'hello', None) or {}
        try:
            parameters = self.rpi.params_view().get('values')
            flow = self.rpi.flow_view().get('name')
        except Exception:
            parameters, flow = None, None
        facts = plan['facts']
        rows = []
        for step in plan['steps']:
            row = copy.deepcopy(step)
            row.update(status='pending', started_at=None, finished_at=None, result=None, error='')
            rows.append(row)
        backend = sys.modules.get('backend') if facts['uses_pump'] else None
        fresh = {'id': round_id, 'version': 1, 'status': 'pausing' if run.pause.is_set() else 'running',
                 'demo': self.pump.mode == 'sim' or bool(hello.get('sim')),
                 'started_at': now_iso(), 'finished_at': None, 'error': '', 'rev': 1,
                 'schedule_id': record['id'], 'slot': index + 1, 'slots_planned': record.get('planned_total'),
                 'planned_at': schedule.slot_time(record['spec'], index).isoformat(),
                 'operator': self._operator() or record.get('operator') or '',
                 'sample_kind': 'blank' if kind == 'blank' else 'sample',
                 'mode': 'calibration' if calibration is not None else 'schedule',
                 'calibration_id': calibration['id'] if calibration is not None else None,
                 'recipe': {'id': ref['id'], 'name': ref['name'], 'sha256': ref['sha256'], 'csv': plan['csv']},
                 'step_count': len(rows), 'rounds': plan['rounds'], 'phases': copy.deepcopy(plan['phases']),
                 'steps': rows, 'step': 0,
                 'uses_pump': facts['uses_pump'], 'uses_rpi': facts['uses_rpi'], 'uses_plasma': facts['uses_plasma'],
                 'plasma': {'parameters': parameters, 'flow': flow, 'jobs': []}, 'plasma_job': None,
                 'weights': [],
                 'versions': {'pc': migrate.PROGRAM, 'rpi': hello.get('version'),
                              'v7_backend': getattr(backend, '__version__', None)},
                 'stop_report': None, 'paused': [], 'paused_pumps': [], 'messages': [], 'prompt': None,
                 'measurements': []}
        if calibration is not None and calibration.get('prime'):
            fresh['prime_recipe'] = dict(calibration['prime'])
        if facts['uses_pump']:
            fresh['board'] = self.pump.facts()
        ctx = RoundContext(self, run, fresh, facts)
        if facts['uses_pump']:
            ctx.board_generation = self.pump.view().get('generation')
        with self.lock:
            self.round, self._live = fresh, ctx
            self._round_dirty = True
        self._save_round(force=True)
        self._slot(run, index, 'running', round_id=round_id, started_at=fresh['started_at'], kind=kind)
        self._phase(run, 'running')
        self._save_schedule()
        return ctx

    def _step_started(self, ctx, step):
        with self.lock:
            step.update(status='running', started_at=now_iso())
            ctx.live.clear()
            self._touch(ctx.record)
        self._save_round(force=step['action'] != 'delay')

    def _step_done(self, ctx, step, result):
        with self.lock:
            step.update(status='done', finished_at=now_iso(), result=result)
            self._touch(ctx.record)
        self._save_round(force=step['action'] != 'delay')

    def _execute(self, run, ctx, position):
        """The steps one after the other, then the end of the round. -> ('completed' | 'stopped' | 'failed', error)"""
        record = ctx.record
        status, error = 'completed', ''
        try:
            for step in record['steps']:
                with self.lock:
                    ctx.current = step
                    record['step'] = step['n']
                ctx.checkpoint()
                self._step_started(ctx, step)
                self._step_done(ctx, step, self.executor.run(step, ctx))
        except steps.Stopped as exc:
            status, error = 'stopped', str(exc)
        except steps.StepFailed as exc:
            status, error = 'failed', str(exc)
        except Exception as exc:                     # a fault of this program is a failed round, never a hung one
            status, error = 'failed', 'Program internal error %r' % (exc,)
        if status != 'completed':
            step = ctx.current or {}
            if status == 'failed':
                error = T_STEP_FAILED.format(n=step.get('n', '?'), name=step.get('name', ''), detail=error)
                if run.stop.is_set():
                    status = 'stopped'               # STOP was pressed: what the step met on its way stays as the note
            with self.lock:
                if step.get('status') == 'running' or (status == 'failed' and step.get('status') == 'pending'):
                    step.update(status='stopped' if status == 'stopped' else 'failed', finished_at=now_iso(),
                                error='' if status == 'stopped' else error)
                    self._touch(record)
        return self._finish_round(run, ctx, status, error, position)

    def _finish_round(self, run, ctx, status, error, position):
        """Leave everything off, write the record and the run-log line, give the equipment back."""
        record = ctx.record
        report = None
        try:
            if status == 'completed' and ctx.uses_pump:
                try:
                    moving = self.devices.pumps_moving()         # asks the board; a closing valve gets 3 s
                except Exception:
                    moving = [True]
                if moving:
                    # Preflight makes every Recipe stop its pumps. Should one be left turning all the same:
                    try:
                        self.pump.stop_all('round-end')
                    except Exception:
                        pass
                    status, error = 'failed', T_PUMPS_LEFT
            if status != 'completed':
                report = self.devices.all_off(ctx.uses_pump, ctx.uses_rpi, ctx.ack)
                bad = interlock.unconfirmed(report)
                if bad:
                    who = ', '.join({'pump': 'pump control board', 'rpi': 'RPi'}[name] for name in bad)
                    status = 'failed'
                    error = (error + '; ' if error else '') + T_OFF_UNCONFIRMED.format(who=who)
        except Exception as exc:
            self._last_resort_off()
            status = 'failed'
            error = (error + '; ' if error else '') + T_CLOSING_ERROR.format(detail=repr(exc))
        finally:
            with self.lock:
                for step in record['steps']:
                    if step['status'] == 'pending':
                        step['status'] = 'skipped'
                    elif step['status'] == 'running':
                        step.update(status='stopped' if status == 'stopped' else 'failed', finished_at=now_iso())
                record.update(status=status, finished_at=now_iso(), error=error[:400], prompt=None, paused_pumps=[])
                if report is not None:
                    record['stop_report'] = {name: {'result': half.get('result'), 'error': half.get('error', '')}
                                             for name, half in report.items()}
                self._touch(record)
                row = self._round_row(record)
            self._save_round(force=True)
            try:
                self.store.append_runlog(row)
            except Exception as exc:
                self._event(E_ROUND_UNSAVED.format(detail=exc), 'warn')
            with self.lock:
                self._live = None
            self.devices.release(ctx.round_id)
        tail = ': ' + error if error else ''
        level = 'error' if status == 'failed' else 'info'
        if run.calibration is not None:
            self._say(record, E_CAL_ROUND_END.format(word=ROUND_WORD[status], recipe=record['recipe']['name']) + tail, level)
        else:
            self._say(record, E_ROUND.format(word=ROUND_WORD[status], recipe=record['recipe']['name'],
                                             position=position) + tail, level)
        self._save_round(force=True)
        if status == 'failed':
            self._alarm(A_ROUND_FAILED, id=record['id'], detail=error)
        self._finish_calibration(run, status, error)
        return status, error

    def _last_resort_off(self):
        """Both stops, straight to the two links. Only for the case that closing a round went wrong in this
        program itself: the outputs are not left on because of a fault here."""
        for stop in (lambda: self.pump.stop_all('round-end'), self.rpi.stop):
            try:
                stop()
            except Exception:
                pass

    def _round_row(self, record):
        """The run-log line of a round (SPEC D.2, AMENDMENTS M5.2)."""
        plasma = record.get('plasma') or {}
        return {'kind': 'round', 'time': record.get('started_at'), 'operator': record.get('operator', ''),
                'recipe_name': (record.get('recipe') or {}).get('name'), 'schedule_id': record.get('schedule_id'),
                'slot': record.get('slot'), 'slots_planned': record.get('slots_planned'), 'status': record.get('status'),
                'finished_at': record.get('finished_at'), 'round_id': record.get('id'),
                'spectra': [job['job'] for job in plasma.get('jobs') or [] if job.get('saved')],
                'result_text': result_text(record), 'note': record.get('error') or '',
                'sample_kind': record.get('sample_kind', 'sample'), 'mode': record.get('mode', 'schedule'),
                'measurements': copy.deepcopy(record.get('measurements') or [])}

    # ---- Pausing (RoundContext.hold use) -----------------------------------------------------------------
    def _label(self, motor):
        hardware = self.pump.hardware() or {}
        for group in hardware.get('groups') or []:
            for row in group.get('motors') or []:
                if row.get('id') == motor:
                    return row.get('label') or 'pump (%s) ' % motor
        return 'pump (%s) ' % motor

    def _held(self, ctx, motors):
        labels = [self._label(motor) for motor in motors]
        with self.lock:
            record = ctx.record
            if not ctx.stop.is_set():
                ctx.run.record['status'] = 'paused'
                record['status'] = 'paused'
            record['paused'].append({'at': now_iso(), 'resumed_at': None})
            record['paused_pumps'] = labels
            self._touch(record)
        self._say(ctx.record, E_HELD + (E_HELD_PUMPS.format(motors=', '.join(labels)) if motors else ''))
        self._save_round(force=True)
        self._save_schedule()

    def _resumed(self, ctx):
        with self.lock:
            record = ctx.record
            if not ctx.stop.is_set():
                ctx.run.record['status'] = 'running'
                record['status'] = 'running'
            if record['paused']:
                record['paused'][-1]['resumed_at'] = now_iso()
            record['paused_pumps'] = []
            self._touch(record)
        self._say(ctx.record, E_RESUMED)
        self._save_round(force=True)
        self._save_schedule()

    # ---- before and after discharge (AMENDMENTS M5.2, M11) ---------------------------------------------------------------
    def _before_discharge(self, ctx, step):
        hook = self.before_discharge
        if hook is None:
            return
        record = ctx.record
        conc = None
        tag = step.get('calibration')
        if ctx.run.calibration is not None and isinstance(tag, dict):
            try:
                level = ctx.run.calibration['plan']['levels'][tag['level']]
                conc = {metal: level['conc'].get(metal) or 0 for metal in METALS}
            except (KeyError, IndexError, TypeError):
                conc = None
        try:
            hook({'round_id': record['id'], 'mode': record['mode'], 'sample_kind': record['sample_kind'], 'conc': conc})
        except Exception as exc:
            self._say(record, E_HOOK.format(detail=repr(exc)), 'warn')

    def _measured(self, ctx, step, job):
        """After a finished plasma_run whose spectra are stored. Never raises: the step has succeeded."""
        analyst = self.analyst
        if analyst is None:
            return None
        record = ctx.record
        try:
            tag = step.get('calibration')
            if ctx.run.calibration is not None:
                # A calibration's rounds are the standards: they go to the calibration record, never to Trend.
                if isinstance(tag, dict):
                    analyst.add_run(ctx.run.calibration['id'], {
                        'level': tag.get('level'), 'level_name': tag.get('level_name'), 'run': tag.get('run'),
                        'discard': tag.get('discard'), 'round': tag.get('round'), 'job': job, 'finished_at': now_iso()})
                    name = tag.get('level_name')
                    self._say(record, E_CAL_DISCARD.format(level=name) if tag.get('discard')
                              else E_CAL_ROUND.format(level=name, n=tag.get('round')))
                return None
            kind = record['sample_kind']
            measured = analyst.measure(job, kind, {'round_id': record['id'], 'schedule_id': record['schedule_id'],
                                                   'slot': record['slot'], 'recipe_name': record['recipe']['name'],
                                                   'operator': record['operator']})
            summary = measurements.summary(measured)
            with self.lock:
                record['measurements'].append(summary)
                self._touch(record)
            if kind == 'blank':
                check = measured.get('qc') or {}
                if check.get('status') == 'fail':
                    detail = '; '.join(str(item) for item in check.get('problems') or [])
                    self._alarm(A_BLANK_QC, id=record['id'], detail=detail)
                    self._say(record, A_BLANK_QC[4].format(detail=detail), 'warn')
                elif check.get('status') == 'unchecked':
                    self._say(record, E_BLANK_UNCHECKED.format(reason=check.get('reason') or ''))
            else:
                values = [(metal, summary['conc'].get(metal)) for metal in METALS]
                values = ['%s %.4g' % pair for pair in values if _number(pair[1])]
                if values:
                    self._say(record, E_SAMPLE_CONC.format(values=', '.join(values)))
            return summary
        except Exception as exc:
            self._say(record, E_MEASURE_FAILED.format(detail=repr(exc)), 'warn')
            return None
