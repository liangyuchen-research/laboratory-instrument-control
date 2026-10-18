#!/usr/bin/env python3
"""Plasmic Electronic Control - How to do each action in a round of process (SPEC A.2, B.5; AMENDMENTS M5.2, M8, M11) .

StepExecutor.run(step, ctx) Perform an action and come back after completing it:
  * Always ask first about the action of the pump control board Devices.permit_motion() (The high voltage will not move until it is turned off), then hand it over to PumpBoard; 
  * discharge (plasma_run) Always there Devices.hv_session() Inside: The pump is stopped before sending out, and after the high pressure is read back, it is turned off;
  * Never resend once discharged: only ”read back spectrum” will try again (RpiLink.fetch_result) .

`step` yes recipe.validate() (the calibration process also has Put the shortcut of operator_prompt this kind).
`ctx` is the process itself (runner.py of RoundContext) ,is used here:

    ctx.stop                  Stop flag for this round (threading.Event) 
    ctx.ack                   Check ”I have gone to the site to confirm that the high-voltage power supply is turned off” when starting the schedule.
    ctx.pumps                 The pump that is on this round:motor -> {flow, direction, planned_s, used_s, started, parked}
    ctx.round_id / recipe_name / operator / slot
    ctx.checkpoint()          Stop -> Stopped; There is something wrong with the pump control board -> StepFailed; pause -> Wait to continue; postback paused for a few seconds
    ctx.progress(**facts)     The number to be used in the ”Current Step” line on the screen
    ctx.job_started(step, reply), ctx.add_job(entry), ctx.add_weight(n, name, grams)
    ctx.before_discharge(step), ctx.measured(step, job, meta)
    ctx.alarm(key, priority, area, text, kind), ctx.say(text, level)
    ctx.prompt_open(text) -> Event, ctx.prompt_close()

will be lost if it fails. StepFailed (That round is“fail”) ; Press to stop throwing Stopped (That round is ”stopped”).
"""
from __future__ import annotations

import time

import devices as interlock
import rpilink
from plasmic_client import PlasmicError
from pumpboard import PumpBoardError

# Module attributes on purpose: read when used, so tests can shorten them.
RUN_MARGIN_S = 5.0           # a Recipe's pump run lasts planned_s + this inside the board (SPEC A.2, M8)
RUN_UNPLANNED_S = 300.0      # a run whose planned time is not known gets the longest one allowed
DELAY_TICK_S = 0.1           # how often a wait looks at the stop flag and at a pause
JOB_LIMIT_SECONDS = 360.0    # plasma_run: how long the job may take
JOB_LOST_SECONDS = 30.0      # ... and how long the RPi link may be gone meanwhile
SPECTRUM_FRAMES = 3
SPECTRUM_EXTRA_S = 60.0      # spectrum_capture: 3 x integration time + this
SCALE_SAMPLES = 5

T_UNKNOWN_ACTION = 'Unsupported action ”{action}”'
T_RUN_VANISHED = '{label} has been stopped first (the pump control board has been restarted, or all stops have been received), and the running time and Recipe is written differently.'
T_HV_NOT_OFF = 'High voltage is not turned off'
T_NOT_PREPARED = 'Nano No reported parameters have been synchronized'
T_NO_PARAMETERS = 'Unable to read plasma parameters'
T_JOB_FAILED = 'The measurement did not end normally'
T_JOB_STOPPED_ELSEWHERE = 'The measurement was stopped (not from this screen)'
T_RESULT_UNREAD = 'The discharge is completed, but the spectrum reading fails:{detail} (data is still available RPi) '
T_RESULT_UNSAVED = 'The discharge is completed, but the spectrum is not archived successfully:{detail} (data is still available RPi) '
T_CAPTURE_UNREAD = 'Spectrum acquisition is completed, but reading the spectrum failed:{detail} (data is still available RPi) '
T_CAPTURE_UNSAVED = 'The spectrum acquisition is completed, but the spectrum is not archived successfully:{detail} (data is still available RPi) '
T_STOP_FAILED = 'Turning off high voltage is not confirmed'
A_WEIGHT = ('weight_tolerance:{round}:{n}', 3, 'scales', 'event', 'Weighing quantity exceeds the allowable range: target {target} g,Actual {actual} g')
E_PROMPT_WAIT = 'Waiting for fluid change:{name}'
E_PROMPT_DONE = 'Fluid change confirmed:{name}'


class Stopped(Exception):
    """STOP was pressed: the round ends `stopped`. The text (may be empty) says what was found on the way."""


class StepFailed(Exception):
    """The step could not be carried out: the round ends `failed` with this text."""


def blocked_text(exc):
    """The sentence of a Blocked exception with its reasons: “The pump cannot be started now (the high voltage power supply is ON) ”."""
    detail = interlock.reasons_text(getattr(exc, 'reasons', None))
    return '%s (%s) ' % (exc, detail) if detail else str(exc)


def _g(value):
    try:
        return '%g' % float(value)
    except (TypeError, ValueError):
        return str(value)


def _motor(step):
    """The pin of the board the step means. recipe.validate() gives it (`motor`); a step of an older round
    record only has its target, written the old way: 'M8' -> 8."""
    motor = step.get('motor')
    if isinstance(motor, int) and not isinstance(motor, bool):
        return motor
    target = str(step.get('target', ''))
    try:
        if not target.startswith('M'):
            raise ValueError(target)
        return int(target[1:])
    except ValueError:
        raise StepFailed('The target of the step is not a pump:%s' % step.get('target')) from None


def _label(step):
    return str(step.get('target_label') or step.get('target') or '')


def _position(step):
    """'CH2' -> 2"""
    try:
        return int(str(step.get('target', ''))[2:])
    except ValueError:
        raise StepFailed('step is not the valve position:%s' % step.get('target')) from None


def _number(step):
    try:
        return float(step.get('value'))
    except (TypeError, ValueError):
        raise StepFailed('The value of the step is not a number:%s' % step.get('value')) from None


class StepExecutor:
    """One function per action of SPEC A.2, plus the bottle-swap prompt of a calibration."""

    def __init__(self, devices, pump, rpi, store):
        self.devices, self.pump, self.rpi, self.store = devices, pump, rpi, store
        self._actions = {
            'delay': self._delay,
            'pump_on': self._pump_on, 'pump_reverse': self._pump_on, 'pump_off': self._pump_off,
            'dose_volume': self._dose, 'dose_reverse': self._dose, 'dose_weight': self._dose_weight,
            'dose_together': self._dose_together,
            'clean': self._clean,
            'syringe_init': self._syringe_init, 'syringe_speed': self._syringe_speed,
            'syringe_aspirate': self._syringe_move, 'syringe_dispense': self._syringe_move,
            'scale_read': self._scale_read,
            'rpi_status': self._rpi_status, 'nano_prepare': self._nano_prepare,
            'plasma_run': self._plasma_run, 'spectrum_capture': self._spectrum_capture,
            'nano_stop': self._power_off, 'power_off': self._power_off,
            'operator_prompt': self._operator_prompt,
        }

    def run(self, step, ctx):
        """Carry out one step; returns its result (a dict for the round record)."""
        handler = self._actions.get(step.get('action'))
        if handler is None:
            raise StepFailed(T_UNKNOWN_ACTION.format(action=step.get('action')))
        try:
            return handler(step, ctx) or {}
        except (Stopped, StepFailed):
            raise
        except PumpBoardError as exc:
            if exc.code == 'stopped' and ctx.stop.is_set():
                raise Stopped('') from None
            raise StepFailed(exc.text) from None
        except interlock.Blocked as exc:
            raise StepFailed(blocked_text(exc)) from None
        except (interlock.Busy, PlasmicError, RuntimeError) as exc:    # the sentences of the RPi link
            raise StepFailed(str(exc) or type(exc).__name__) from None

    # ---- generally ---------------------------------------------------------------------------------------
    def _delay(self, step, ctx):
        """Paused time does not count: the rest of the wait goes on where it stopped."""
        total = _number(step)
        started, paused = time.perf_counter(), 0.0
        while True:
            paused += ctx.checkpoint() or 0.0
            waited = time.perf_counter() - started - paused
            ctx.progress(waited=min(max(waited, 0.0), total), total=total)
            if waited >= total:
                return {'waited_s': total}
            ctx.stop.wait(min(DELAY_TICK_S, total - waited))

    def _operator_prompt(self, step, ctx):
        """Calibration: wait until the operator has swapped the bottle and pressed “Confirm fluid change”."""
        name = str((step.get('calibration') or {}).get('level_name') or '')
        confirmed = ctx.prompt_open(str(step.get('text') or step.get('note') or ''))
        ctx.say(E_PROMPT_WAIT.format(name=name))
        try:
            while not confirmed.is_set():
                ctx.checkpoint()                     # stop, and a pause as in a wait
                confirmed.wait(DELAY_TICK_S)
        finally:
            ctx.prompt_close()
        ctx.say(E_PROMPT_DONE.format(name=name))
        return {'confirmed': True}

    # ---- pump -----------------------------------------------------------------------------------------
    def _pump_on(self, step, ctx):
        motor, flow = _motor(step), _number(step)
        direction = -1 if step['action'] == 'pump_reverse' else 1
        planned = step.get('planned_s')
        planned = float(planned) if isinstance(planned, (int, float)) and not isinstance(planned, bool) else RUN_UNPLANNED_S
        self.devices.permit_motion(ctx.ack)
        # A finite move inside the board (M8): it stops by itself 5 s after the planned time at the latest.
        out = self.pump.run(motor, flow, direction, planned + RUN_MARGIN_S, 'round')
        ctx.pumps[motor] = {'flow': flow, 'direction': direction, 'planned_s': planned, 'used_s': 0.0,
                            'started': time.perf_counter(), 'parked': False}
        return {'sps': out.get('sps'), 'flow_ml_min': flow, 'direction': direction, 'planned_s': planned}

    def _pump_off(self, step, ctx):
        motor = _motor(step)
        entry = ctx.pumps.pop(motor, None)
        out = self.pump.stop_motor(motor)            # raises when the run had already run out (M12)
        ran = out.get('ran_s')
        if entry is not None and not entry.get('parked') and ran is None:
            # The board no longer knew of this run: it restarted, or everything was stopped meanwhile.
            raise StepFailed(T_RUN_VANISHED.format(label=_label(step)))
        return {'ran_s': round((entry['used_s'] if entry else 0.0) + (ran or 0.0), 1)}

    def _dose(self, step, ctx):
        direction = -1 if step['action'] == 'dose_reverse' else 1
        motor, volume = _motor(step), _number(step)
        self.devices.permit_motion(ctx.ack)
        out = self.pump.dose(motor, volume, direction, 'round', ctx.stop)
        return {key: out.get(key) for key in ('steps', 'sps', 'eta_s', 'elapsed_s')}

    def _dose_together(self, step, ctx):
        """Several pumps, each its own volume, as one operation of the board (one pump per driver group at a time)."""
        motors, amounts = step.get('motors'), step.get('amounts')
        if not isinstance(motors, list) or not isinstance(amounts, list) or len(motors) != len(amounts) or not motors:
            raise StepFailed('The target of the step is not a pump:%s' % step.get('target'))
        self.devices.permit_motion(ctx.ack)
        out = self.pump.dose_many(list(zip(motors, amounts)), 'round', ctx.stop)
        return {'doses': out.get('doses'), 'elapsed_s': out.get('elapsed_s')}

    def _dose_weight(self, step, ctx):
        motor, target = _motor(step), _number(step)
        self.devices.permit_motion(ctx.ack)
        out = self.pump.dose_weight(motor, target, 'round', ctx.stop)
        result = {key: out.get(key) for key in ('target_g', 'dispensed_g', 'error_g', 'within_tolerance', 'topups',
                                                 'elapsed_s', 'baseline_g')}
        if out.get('warning'):
            result['warning'] = out['warning']
        if out.get('within_tolerance') is False:     # not a failure: the numbers are recorded and an alarm says so
            key, priority, area, kind, text = A_WEIGHT
            actual = out.get('dispensed_g')
            ctx.alarm(key.format(round=ctx.round_id, n=step.get('n')), priority, area,
                      text.format(target=_g(target), actual='%.2f' % actual if isinstance(actual, (int, float)) else '?'),
                      kind)
        return result

    def _clean(self, step, ctx):
        self.devices.permit_motion(ctx.ack)
        out = self.pump.clean('round', ctx.stop)
        return {key: out.get(key) for key in ('started', 'total_pump_timeline_s', 'elapsed_s')}

    # ---- Syringe pump, scale ---------------------------------------------------------------------------------
    def _syringe_init(self, step, ctx):
        self.devices.permit_motion(ctx.ack)
        out = self.pump.syringe_init('round', ctx.stop)
        return {key: out.get(key) for key in ('initialised', 'speed_ml_s')}

    def _syringe_speed(self, step, ctx):
        return {'speed_ml_s': self.pump.syringe_speed(_number(step)).get('speed_ml_s')}

    def _syringe_move(self, step, ctx):
        kind = 'aspirate' if step['action'] == 'syringe_aspirate' else 'dispense'
        position, volume = _position(step), _number(step)
        self.devices.permit_motion(ctx.ack)
        out = self.pump.syringe_move(kind, position, volume, 'round', ctx.stop)
        return {key: out.get(key) for key in ('position', 'volume_ul', 'speed_ml_s', 'contents_ul')}

    def _scale_read(self, step, ctx):
        out = self.pump.scale_read(SCALE_SAMPLES)
        name = str(step.get('note') or '')
        ctx.add_weight(step.get('n'), name, out.get('grams'))
        return {'grams': out.get('grams'), 'name': name}

    # ---- RPi: High voltage and spectrum ----------------------------------------------------------------------------
    def _rpi_status(self, step, ctx):
        """A fresh read of HSPY and Nano; fails unless the high voltage is known to be off."""
        reply = self.rpi.command('status', fresh=True)
        view = rpilink.hv_state(True, reply, 0.0)    # judged on this very reply, not on an older one
        if not (view['known'] and view['idle']):
            raise StepFailed('; '.join(view['reasons']) or T_HV_NOT_OFF)
        power = (reply.get('snapshot') or {}).get('power') or {}
        return {'hv_off': True, 'voltage_v': power.get('voltage_v')}

    def _nano_prepare(self, step, ctx):
        reply = self.rpi.command('prepare')
        if reply.get('prepared') is not True:
            raise StepFailed(T_NOT_PREPARED)
        return {'prepared': True}

    def _power_off(self, step, ctx):
        try:
            reply = self.rpi.stop()                  # over a fresh connection; HSPY OFF + Nano STOP with read-back
        except RuntimeError as exc:
            raise StepFailed(str(exc) or T_STOP_FAILED) from None
        if reply.get('cancel_sent'):
            return {'cancel_sent': True, 'job': reply.get('job')}
        return {'stopped': True}

    def _job_post_stop_failed(self, job):
        """True when the RPi says that the OFF read-back after this job's stop was not confirmed."""
        status = self.rpi.latest() or {}
        row = status.get('job') or {}
        post = row.get('post_stop') if row.get('id') == job else None
        return bool(post) and not post.get('ok')

    def _job_ended_badly(self, ctx, outcome, note):
        if ctx.stop.is_set():
            raise Stopped(note or '')
        if outcome == 'stopped':
            raise StepFailed(' '.join(part for part in (T_JOB_STOPPED_ELSEWHERE, note) if part))
        raise StepFailed(note or T_JOB_FAILED)

    def _plasma_run(self, step, ctx):
        """The RPi's measuring flow. The discharge is sent once and never again."""
        flow = self.rpi.flow_view()
        if not flow.get('ok'):
            old = flow.get('name') is None and self.rpi.latest() is not None
            raise StepFailed(interlock.T_RPI_OLD if old else str(flow.get('text') or interlock.T_RPI_LINK))
        with self.devices.hv_session():              # pumps at rest, or Blocked “The pump is still moving and cannot discharge.”
            self.rpi.rpi_ready()                     # RuntimeError says why a discharge cannot start now
            ctx.before_discharge(step)
            reply = self.rpi.command('plasma_run', attended=False, claim=True, confirm_high_voltage=True)
            job = reply['job']
            ctx.job_started(step, reply)
            outcome, note = self.rpi.wait_job(job, ctx.stop, limit_s=JOB_LIMIT_SECONDS, lost_s=JOB_LOST_SECONDS)
            unconfirmed = outcome != 'done' and self._job_post_stop_failed(job)
        # Here the high voltage has been read back as off — or Devices has raised its alarm.
        if unconfirmed:
            self.devices.raise_hv_unconfirmed()
        if outcome != 'done':
            ctx.add_job({'job': job, 'kind': 'plasma', 'state': outcome, 'voltage_v': reply.get('voltage_v'),
                         'saved': False, 'note': note})
            self._job_ended_badly(ctx, outcome, note)
        try:
            result = self.rpi.fetch_result(job)      # tried twice; only the read
        except (RuntimeError, PlasmicError) as exc:
            ctx.add_job({'job': job, 'kind': 'plasma', 'state': 'done', 'voltage_v': reply.get('voltage_v'),
                         'saved': False, 'note': str(exc)})
            raise StepFailed(T_RESULT_UNREAD.format(detail=exc)) from None
        try:
            meta = self.store.save_run(result, round_id=ctx.round_id, recipe_name=ctx.recipe_name,
                                       operator=ctx.operator, slot=ctx.slot, source='round')
        except Exception as exc:
            ctx.add_job({'job': job, 'kind': 'plasma', 'state': 'done', 'voltage_v': reply.get('voltage_v'),
                         'saved': False, 'note': str(exc)})
            raise StepFailed(T_RESULT_UNSAVED.format(detail=exc)) from None
        timing = result.get('timing') or {}
        ctx.add_job({'job': job, 'kind': 'plasma', 'state': 'done', 'voltage_v': meta.get('voltage_v'),
                     'wait_s': meta.get('wait_s'), 'lead_ms': timing.get('lead_ms'),
                     'background_frames': meta.get('background_frames'), 'signal_frames': meta.get('signal_frames'),
                     'integration_ms': meta.get('integration_ms'), 'peak_nm': meta.get('peak_nm'),
                     'peak_value': meta.get('peak_value'), 'saved': True})
        out = {'job': job, 'voltage_v': meta.get('voltage_v'), 'flow': meta.get('flow'),
               'background_frames': meta.get('background_frames'), 'signal_frames': meta.get('signal_frames'),
               'wait_s': meta.get('wait_s'), 'peak_nm': meta.get('peak_nm')}
        measurement = ctx.measured(step, job, meta)  # concentration / blank check / calibration run; never raises
        if measurement is not None:
            out['measurement'] = measurement
        return out

    def _spectrum_capture(self, step, ctx):
        """Three frames without high voltage, at the integration time of plasma parameters. Not the background of a measurement."""
        status = self.rpi.latest() or {}
        parameter = (status.get('snapshot') or {}).get('parameter') or {}
        ms = parameter.get('integration_ms')
        if parameter.get('valid') is not True or isinstance(ms, bool) or not isinstance(ms, int):
            raise StepFailed(T_NO_PARAMETERS)
        reply = self.rpi.command('spectrum', claim=True, integration_ms=ms)
        job = reply['job']
        ctx.job_started(step, reply)
        outcome, note = self.rpi.wait_job(job, ctx.stop, limit_s=SPECTRUM_FRAMES * ms / 1000.0 + SPECTRUM_EXTRA_S,
                                          lost_s=JOB_LOST_SECONDS)
        if outcome != 'done':
            ctx.add_job({'job': job, 'kind': 'capture', 'state': outcome, 'saved': False, 'note': note})
            self._job_ended_badly(ctx, outcome, note)
        try:
            result = self.rpi.fetch_result(job)
        except (RuntimeError, PlasmicError) as exc:
            ctx.add_job({'job': job, 'kind': 'capture', 'state': 'done', 'saved': False, 'note': str(exc)})
            raise StepFailed(T_CAPTURE_UNREAD.format(detail=exc)) from None
        try:
            meta = self.store.save_run(result, round_id=ctx.round_id, recipe_name=ctx.recipe_name,
                                       operator=ctx.operator, slot=ctx.slot, source='round')
        except Exception as exc:
            ctx.add_job({'job': job, 'kind': 'capture', 'state': 'done', 'saved': False, 'note': str(exc)})
            raise StepFailed(T_CAPTURE_UNSAVED.format(detail=exc)) from None
        ctx.add_job({'job': job, 'kind': 'capture', 'state': 'done', 'integration_ms': meta.get('integration_ms'),
                     'peak_nm': meta.get('peak_nm'), 'peak_value': meta.get('peak_value'), 'saved': True})
        return {'job': job, 'integration_ms': ms, 'frames': SPECTRUM_FRAMES, 'peak_nm': meta.get('peak_nm')}
