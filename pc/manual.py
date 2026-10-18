#!/usr/bin/env python3
"""Plasmic Electronic control - manual operation (SPEC B.7, API.md C.5) .

“”Manual Operation” page is here: start a pump (limited time, the pump control panel stops itself when the time is up), stop,
quantitative (volume/weight), automatic cleaning, syringe pump initialization/Liquid extraction/Liquid injection.

  * Only do one thing at a time: get the equipment before doing it (Devices.acquire('manual')) ; Scheduled measurement is in progress and the process is suspended.
    Repair tools will be rejected while in use. Another pump in another group can be turned on at the same time (that is what the pump control panel inherently allows).
  * Ask first every time you start Devices.permit_motion(): The high voltage is not turned off, or I don’t know whether it is turned off or not, and it won’t move.
  * “Stop” (pump_off) is always accepted, regardless of who is currently using the device.
  * Actions that take time (quantification, cleaning, syringe pump) are executed in another thread (manual-op) ; button will get a reply immediately, Execute on
    Check the progress state.pump.op,See the results state.pump.last_result.
  * The screen is missing (more than The 10 did not ask the status for seconds), the pump control board will stop all manually started things (PumpBoard of
    monitors the execution thread); here you just need to regard that end as your own end.

Manual operation does not require selecting an operator; if selected, the name will be recorded in the event record.
"""
from __future__ import annotations

import threading
import time
import uuid

import devices as interlock
from devices import Busy
from pumpboard import PumpBoardError

OWNER_ID = 'manual'              # all manual operations share one hold on the equipment
ACCEPT_WAIT_SECONDS = 4.0        # how long a start waits to learn whether the board took the operation
RUN_MIN_S, RUN_MAX_S = 1, 300    # “n Automatically stop in seconds”
FLOW_MIN = 0.1
DOSE_MIN, DOSE_MAX = 0.01, 1000

T_FORMAT = 'Manual operation format is incorrect'
T_UNKNOWN = 'Unsupported manual operation'
T_MOTOR = 'The pump number is incorrect'
T_FLOW = 'traffic should be in {low:g}–{high:g} mL/min between'
T_DIRECTION = 'direction must be 1 (forward) or -1 (reverse)'
T_SECONDS = 'The number of seconds for automatic stop should be within {low}–{high} integer between'
T_VOLUME = 'The quantitative volume should be within {low:g}–{high:g} mL between'
T_WEIGHT = 'The quantitative weight should be in {low:g}–{high:g} g between'
T_KIND = 'The action of the syringe pump must be aspirate (aspiration) or dispense (injection)'
T_POSITION = 'The valve position should be at 1–{top} between'
T_SYRINGE_VOLUME = 'The injection volume should be within 0.001–{top:g} mL between'
L_MOTOR = 'Manual: {label}'
L_CLEAN = 'Manual: automatic cleaning'
L_SYRINGE = 'Manual: Syringe pump Only'
E_PUMP_ON = 'Manual start {label}: {way} {flow:g} mL/min,{seconds} Automatically stop in seconds'
E_PUMP_OFF = 'manual stop {label}'
E_OP_START = 'Manual operation starts:{text}'
E_OP_DONE = 'Manual operation completed:{text}'
E_OP_STOPPED = 'Manual operation has been stopped:{text}'
E_OP_FAILED = 'Manual operation failed:{text} ({detail}) '
WAY = {1: 'Forward rotation', -1: 'reverse'}


def _number(value):
    """A finite number that is not a bool, as a float; else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value and value not in (float('inf'), float('-inf')) else None


class ManualOps:
    """One manual operation at a time; every start passes the interlock; OFF is always accepted."""

    def __init__(self, store, devices, pump, operator_getter):
        self.store, self.devices, self.pump = store, devices, pump
        self.operator_getter = operator_getter
        self.lock = threading.Lock()         # the fields below
        self._starting = threading.Lock()    # one start at a time
        self._pending = 0                    # starts that are being decided right now
        self._thread = None                  # the manual-op thread of the blocking operation
        self._abort = None                   # ... and its stop flag
        self._starters = {'pump_on': self._pump_on, 'dose_volume': self._dose_volume, 'dose_weight': self._dose_weight,
                          'clean': self._clean, 'syringe_init': self._syringe_init, 'syringe_move': self._syringe_move}

    # ---- state ---------------------------------------------------------------------------------------
    def _blocking(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _board_busy(self):
        """Does the board still show something that was started by hand?"""
        try:
            view = self.pump.view()
        except Exception:
            return True
        op = view.get('op') or {}
        return op.get('by') == 'manual' or any(row.get('by') == 'manual' for row in view.get('motors') or [])

    @property
    def active(self):
        """True while a manual operation runs: a blocking one, or a pump that was started by hand."""
        with self.lock:
            if self._pending:
                return True
        return self._blocking() or self._board_busy()

    def settle(self):
        """Nothing started by hand is left (its time was over, it was stopped, the page watchdog acted):
        the equipment is given back."""
        if not self.active:
            self.devices.release(OWNER_ID)

    def cancel(self):
        """STOP: the stop flag of the running manual operation. Instant. True when something manual was active.
        (The pumps themselves are stopped by the stop-all that follows.)"""
        was = self.active
        with self.lock:
            abort = self._abort
        if abort is not None:
            abort.set()
        return was

    def _event(self, text, level='info'):
        try:
            name = str(self.operator_getter() or '')
        except Exception:
            name = ''
        try:
            self.store.event(text, level, name)
        except Exception:
            pass

    # ---- field ---------------------------------------------------------------------------------------
    def _hardware(self):
        try:
            return self.pump.hardware() or {}
        except Exception:
            return {}

    def _motor(self, body, hardware):
        motor = body.get('motor')
        labels = {row.get('id'): row.get('label') for group in hardware.get('groups') or [] for row in group.get('motors') or []}
        if type(motor) is not int or (labels and motor not in labels):
            raise ValueError(T_MOTOR)
        return motor, labels.get(motor) or 'pump (%d) ' % motor

    @staticmethod
    def _direction(body):
        direction = body.get('direction', 1)
        if type(direction) is not int or direction not in (1, -1):
            raise ValueError(T_DIRECTION)
        return direction

    def _fields(self, op, body):
        """The checked fields of one request (API.md C.5); ValueError carries the message to show."""
        hardware = self._hardware()
        out = {}
        if op in ('pump_on', 'dose_volume', 'dose_weight'):
            out['motor'], out['name'] = self._motor(body, hardware)
            out['label'] = L_MOTOR.format(label=out['name'])
        if op == 'pump_on':
            top = _number(hardware.get('max_flow_ml_min')) or 80.0
            flow = _number(body.get('flow_ml_min'))
            if flow is None or not FLOW_MIN <= flow <= top:
                raise ValueError(T_FLOW.format(low=FLOW_MIN, high=top))
            seconds = body.get('seconds')
            if type(seconds) is not int or not RUN_MIN_S <= seconds <= RUN_MAX_S:
                raise ValueError(T_SECONDS.format(low=RUN_MIN_S, high=RUN_MAX_S))
            out.update(flow_ml_min=flow, direction=self._direction(body), seconds=seconds)
        elif op == 'dose_volume':
            volume = _number(body.get('volume_ml'))
            if volume is None or not DOSE_MIN <= volume <= DOSE_MAX:
                raise ValueError(T_VOLUME.format(low=DOSE_MIN, high=DOSE_MAX))
            out.update(volume_ml=volume, direction=self._direction(body))
        elif op == 'dose_weight':
            target = _number(body.get('target_g'))
            if target is None or not DOSE_MIN <= target <= DOSE_MAX:
                raise ValueError(T_WEIGHT.format(low=DOSE_MIN, high=DOSE_MAX))
            out['target_g'] = target
        elif op == 'clean':
            out['label'] = L_CLEAN
        elif op == 'syringe_init':
            out['label'] = L_SYRINGE
        elif op == 'syringe_move':
            if body.get('kind') not in ('aspirate', 'dispense'):
                raise ValueError(T_KIND)
            positions = int((hardware.get('syringe') or {}).get('valve_positions') or 3)
            position = body.get('position')
            if type(position) is not int or not 1 <= position <= positions:
                raise ValueError(T_POSITION.format(top=positions))
            try:
                size_ml = float((self.pump.view().get('settings') or {}).get('syringe_ul') or 5000) / 1000.0
            except Exception:
                size_ml = 5.0
            volume = _number(body.get('volume_ml'))
            if volume is None or not 0.001 <= volume <= size_ml:
                raise ValueError(T_SYRINGE_VOLUME.format(top=size_ml))
            out.update(kind=body['kind'], position=position, volume_ml=volume, label=L_SYRINGE)
        return out

    # ---- entrance ---------------------------------------------------------------------------------------
    def submit(self, body):
        """One manual operation (POST /api/manual). Raises ValueError (a wrong field), devices.Busy,
        devices.Blocked or PumpBoardError; returns the reply of API.md C.5."""
        if not isinstance(body, dict):
            raise ValueError(T_FORMAT)
        op = body.get('op')
        if op == 'pump_off':
            return self._pump_off(body)
        starter = self._starters.get(op)
        if starter is None:
            raise ValueError(T_UNKNOWN)
        fields = self._fields(op, body)
        ack = body.get('hv_off_checked') is True
        with self.lock:
            self._pending += 1               # from here on the equipment is not given back behind our back
        try:
            with self._starting:
                if self._blocking():
                    raise Busy(interlock.T_OTHER_BUSY, 'manual_active')
                self.devices.acquire('manual', OWNER_ID, fields['label'])
                self.devices.permit_motion(ack)
                return starter(fields)
        finally:
            with self.lock:
                self._pending -= 1
            self.settle()                    # refused, or over already: nothing manual is left

    def _pump_off(self, body):
        """Always accepted, for any owner (the board refuses only a single stop during cleaning)."""
        motor, name = self._motor(body, self._hardware())
        self.pump.stop_motor(motor)
        self._event(E_PUMP_OFF.format(label=name))
        self.settle()
        return {'stopped': True}

    def _pump_on(self, fields):
        """Inline: the board is given the whole run (speed x seconds) and stops by itself (M8)."""
        self.pump.run(fields['motor'], fields['flow_ml_min'], fields['direction'], fields['seconds'], 'manual')
        self._event(E_PUMP_ON.format(label=fields['name'], way=WAY[fields['direction']], flow=fields['flow_ml_min'],
                                     seconds=fields['seconds']))
        return {'accepted': True, 'motor': fields['motor'], 'until_s': fields['seconds']}

    # ---- An action that takes time -----------------------------------------------------------------------------
    def _dose_volume(self, fields):
        word = 'reverse quantitative' if fields['direction'] == -1 else 'Quantitative'
        return self._blocking_start(
            'dose_volume', '%s %s %g mL' % (fields['name'], word, fields['volume_ml']),
            lambda abort: self.pump.dose(fields['motor'], fields['volume_ml'], fields['direction'], 'manual', abort),
            lambda view, op: op.get('eta_s') is not None)

    def _dose_weight(self, fields):
        return self._blocking_start(
            'dose_weight', '%s Weighing and quantitative %g g' % (fields['name'], fields['target_g']),
            lambda abort: self.pump.dose_weight(fields['motor'], fields['target_g'], 'manual', abort),
            lambda view, op: (view.get('weight_dose') or {}).get('target_g') is not None)

    def _clean(self, fields):
        return self._blocking_start('clean', 'automatic cleaning', lambda abort: self.pump.clean('manual', abort),
                                    lambda view, op: op.get('eta_s') is not None)

    def _syringe_init(self, fields):
        return self._blocking_start('syringe_init', 'syringe pump initialization', lambda abort: self.pump.syringe_init('manual', abort),
                                    lambda view, op: bool((view.get('syringe') or {}).get('busy')))

    def _syringe_move(self, fields):
        word = 'Liquid extraction' if fields['kind'] == 'aspirate' else 'Liquid injection'
        return self._blocking_start(
            'syringe_move', 'syringe pump%s Valve position %d · %g mL' % (word, fields['position'], fields['volume_ml']),
            lambda abort: self.pump.syringe_move(fields['kind'], fields['position'], fields['volume_ml'], 'manual', abort),
            lambda view, op: op.get('eta_s') is not None)

    def _ended(self, text, error):
        if error is None:
            self._event(E_OP_DONE.format(text=text))
        elif isinstance(error, PumpBoardError) and error.code == 'stopped':
            self._event(E_OP_STOPPED.format(text=text), 'warn')      # STOP, OFF on its row, or the page watchdog
        else:
            self._event(E_OP_FAILED.format(text=text, detail=getattr(error, 'text', None) or error), 'error')

    def _blocking_start(self, kind, text, call, taken):
        """Run `call(abort)` on the manual-op thread. The request answers as soon as the board has taken the
        operation (`taken(view, op)`), or raises what the board refused it with."""
        box = {'lock': threading.Lock(), 'done': False, 'accepted': False, 'error': None}
        abort = threading.Event()

        def work():
            try:
                call(abort)
            except BaseException as exc:
                box['error'] = exc
            with box['lock']:
                box['done'] = True
                announce = box['accepted']
            if announce:
                self._ended(text, box['error'])
            self.settle()

        thread = threading.Thread(target=work, name='manual-op', daemon=True)
        with self.lock:
            self._thread, self._abort = thread, abort
        thread.start()
        limit = time.perf_counter() + ACCEPT_WAIT_SECONDS
        op_id, eta = None, None
        while True:
            try:
                view = self.pump.view()
            except Exception:
                view = {}
            op = view.get('op') or {}
            mine = op.get('by') == 'manual' and op.get('kind') == kind
            if mine:
                op_id = op.get('id') or op_id
                eta = op.get('eta_s') if op.get('eta_s') is not None else eta
            with box['lock']:
                over = box['done']
                if not over and ((mine and taken(view, op)) or time.perf_counter() >= limit):
                    self._event(E_OP_START.format(text=text))
                    box['accepted'] = True
                accepted = box['accepted']
            if over:
                if box['error'] is not None:
                    raise box['error']       # refused before anything was accepted: the request says why
                self._event(E_OP_START.format(text=text))
                self._ended(text, None)      # a very short one: over before it could be watched
                break
            if accepted:
                break
            time.sleep(0.01)
        reply = {'accepted': True, 'op_id': op_id or uuid.uuid4().hex}
        if eta is not None:
            reply['eta_s'] = eta
        return reply
