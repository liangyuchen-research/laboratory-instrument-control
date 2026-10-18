#!/usr/bin/env python3
"""Plasmic Electronic control - Who uses the equipment? The pump and high pressure cannot start and stop at the same time (SPEC B.4) .

This file manages three things:

  1. The device can only be used by one person at a time (owner) : one round process (round) , manual operation (manual) , maintenance tool (tool) .
     Scheduled measurement is in progress, manual and maintenance tools have to wait; stop (STOP) Never have to wait.
  2. pump/When the syringe pump and high pressure are absolutely different:
       * Ask first before starting the pump permit_motion(); Every time the pump control panel sends a moving command, it will ask again.
         hv_blocks_motion() (This level is connected to PumpBoard.set_gate , cannot be bypassed). Any action command issued by
       * Enter before discharging hv_session(): First set up the ”Ready to Discharge” flag (from this moment on, no pump instructions can be sent out),
         Confirm again that the pump has really stopped before turning on the high pressure. After the discharge is completed, wait until ”High Voltage is Off” is read back before lowering the flag.
       * The high voltage is not turned on by this program, but it is displayed as ON,And the pump is still moving: background every 0.5 View once per second (devices-watch) ,
         If you see it, stop all pumps and sound the alarm. High pressure doesn’t do this when it just ”doesn’t know” (it just doesn’t let new actions start at that time).
       * When the high voltage status cannot be read (RPi is not connected; or it is connected, but it cannot tell: the status is too old, and the high-voltage power supply does not respond.——
         For example, if the main switch of the high-voltage power supply is turned off), only the operator’s ”I have gone to the site to confirm that the high-voltage power supply is turned off” can make the pump operate.
         When you can read it, just read what you read: if it is off, you can move it; if it is not off, it will not work, and it is useless if it is checked.
         This tick is a sentence at a certain moment: If the stop of high voltage is not confirmed later, or RPi reports over-voltage is ON,
         The previous check will not count and needs to be re-checked.
  3. stop: stop_everything() (on the screen“stop STOP”) and all_off() (after one round of process failed or was stopped).
     pump control board with RPi Send to both sides at the same time and report back separately; if one side does not receive confirmation, an alarm will be sent.
     There is only one situation that is considered mild (the alarm is ”medium” and the result is silent) : RPi responded, but the high-voltage power supply did not respond at all (normally its
     The main switch is turned off),Nano can be read and confirmed that there is no output, and the program is not discharging when the transmission is stopped.

The lock here (Devices.lock) only protects owner, The two high voltage flags and the hook, never touch the device or hard drive while holding it.
The ”stop” of the software is not an emergency stop: the screen display is turned off, which does not mean that the electrode is out of power.
"""
from __future__ import annotations

import contextlib
import threading
import time

import rpilink
from store import now_iso

# Times. Module attributes on purpose: they are read when used, so tests can shorten them.
HV_SETTLE_SECONDS = 30.0     # hv_session(): how long the high voltage gets to read back OFF after a discharge
HV_POLL_SECONDS = 0.2
VALVE_WAIT_SECONDS = 3.0     # hv_session(): a linked valve that is still closing gets this long
STOP_JOIN_SECONDS = 8.0      # how long a stop waits for each half (pump board / RPi) to answer
# ... and for the RPi half when nothing suggests that the high voltage is on (no discharge of ours, the last
# word of the RPi not "on"). An RPi whose supply is switched off at the mains needs 10.4 s to answer a stop
# (measured on the rig, 2026-10-07: the retries of the verified tool), and that — the usual state between
# measurements — has to come back as `silent`, not as `pending`. A second press of STOP meanwhile waits for
# the RPi's bus behind the first: up to 18.4 s. The page waits 30 s for /api/stop.
STOP_QUIET_JOIN_SECONDS = 22.0
SCALE_FRESH_SECONDS = 1.0    # start checks: the newest weight may be this old
WATCH_SECONDS = 0.5          # devices-watch: how often it looks for a pump that moves while the high voltage is ON
WATCH_JOIN_SECONDS = 2.0     # close(): how long the watch gets to end

KINDS = ('round', 'manual', 'tool')
PRIORITY_URGENT, PRIORITY_HIGH, PRIORITY_MEDIUM = 1, 2, 3

# ---- texts (SPEC A.4, B.4; API.md C.1, C.5) -------------------------------------------------------------
T_SCHEDULE_BUSY = 'Scheduled measurement is in progress: press ”Stop” first. STOP”,or wait it out'
T_ROUND_PAUSED = 'The process is paused: Please click ”Continue” at the bottom RESUME”or“stop STOP”'
T_OTHER_BUSY = 'Another operation is in progress'
T_NO_MOTION = 'Cannot start pump now'
T_NO_PLASMA = 'The pump is still moving and cannot discharge. There are two options for ”'
T_SCHEDULE_ACTIVE = 'Already has a schedule in progress'
T_MANUAL_ACTIVE = 'Manual operation is not over yet'
FIX_MANUAL = 'Click on that column first OFF,or wait for it to finish'
T_TOOL_ACTIVE = 'Repair tool is using the device'
T_PUMP_UNSET = 'Pump control board not found'
T_PUMP_DISABLED = 'Pump control board is set to not use'
FIX_PUMP_PLUG = 'Please connect the USB line; after connecting, the program will connect itself'
FIX_PUMP_ENABLE = 'Ask maintenance personnel to go to ”Repair › Connection” Change the pump control board back to use'
T_PUMP_LINK = 'Pump control board is not connected ({text}) '
FIX_USB = 'examine USB Wire'
T_PUMP_UNCERTAIN = 'The status of the pump control board is unknown. Please go to the site to confirm first.'
T_PUMP_MOVING = 'The pump is still running'
T_SCALE_OFFLINE = 'scale has no reading value'
T_SCALE_UNCALIBRATED = 'The scale has not been calibrated yet.'
FIX_SCALE = 'Ask maintenance personnel to go to ”Repair › Pump and Scale” do a calibration'
T_HV_NO_RPI = 'RPi There is no connection and it is impossible to confirm that the high voltage is off.'
# The RPi is connected but cannot tell ({reason}: the sentence of the SPEC B.4 row with `known` false).
T_HV_UNKNOWN = '{reason},Unable to confirm that the high voltage is off'
FIX_HV_TICK = 'After confirming that the high-voltage power supply is turned off, check the box in the confirmation window'
# The sentence of the tick row in a start dialog (interlock.manual.hv_ack_text, the preview's needs.hv_ack_text).
T_ACK_NO_RPI = 'RPi There is no connection, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.'
T_ACK_UNKNOWN = 'Unable to read the status of the high-voltage power supply ({reason}) ,The program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.'
T_HV_SESSION = 'is discharging'
FIX_STOP = 'Press ”Stop” at the bottom STOP”'
T_RPI_LINK = 'RPi Not connected'
T_RPI_BUSY = 'RPi is performing other work'
T_RPI_OLD = 'RPi is still an old version: please double-click first Install.cmd renew RPi'
T_RPI_NOT_READY = 'The pre-discharge inspection failed:'
T_RPI_NO_STATUS = 'The device status has not been read yet'
T_PLASMA_PUMP_LINK = 'The pump control board has been set but not connected, and the pump cannot be confirmed to have stopped.'
FIX_PLASMA_PUMP = 'Connected USB,Or ask maintenance personnel to go to ”Repair › Connection” was changed to ”Do not use pump control board”'
# Alarms (SPEC B.9)
A_HV_KEY, A_HV_AREA, A_HV_KIND = 'hv_stop_unconfirmed', 'high pressure', 'latched'
T_HV_UNCONFIRMED = 'The stop of high voltage has not been confirmed. Please turn off the high voltage with a physical switch immediately.'
T_HV_UNREACHABLE = 'The stop command cannot be sent RPi.RPi will turn off the high voltage automatically after finding that the connection is interrupted, but please go to the site to confirm.'
# The RPi answered the stop, could not confirm it, and reports right after it: the supply does not answer, the
# Nano is read as quiet. (The usual state of the rig between measurements: the supply is off at its mains switch.)
T_HV_SILENT = 'The high voltage power supply does not respond and the program cannot confirm that it is off;Nano Confirmed no output. If the power is turned off from the switch, this is normal.'
A_PUMP_KEY, A_PUMP_AREA, A_PUMP_KIND = 'pump_stop_unconfirmed', 'pump', 'latched'
T_PUMP_UNCONFIRMED = 'The pump stop command was not confirmed. Immediately turn off power to the pump using the physical switch. The settings saved in'
# The watch: the high voltage is ON from elsewhere while something of the pump board moves.
A_WATCH_KEY, A_WATCH_AREA, A_WATCH_KIND = 'pump_with_hv', 'pump', 'event'
T_PUMP_WITH_HV = 'High voltage is displayed as ON,But the pump is still running: All pumps have been stopped. Please go to the site to confirm.'
T_PUMP_WITH_HV_UNSURE = 'High voltage is displayed as ON,But the pump is still moving: the pump stop command was not acknowledged. Immediately turn off power to the pump using the physical switch.'
E_WATCH_FAILED = '“High voltage is ON ”The pump cannot move” has an error. There is no check this time:{detail}'
# The line in the event log after “Stop STOP”: “Stop pressed: pump...; high pressure …”
E_STOP = 'Stop pressed: Pump {pump}; high pressure {rpi}'
PUMP_WORDS = {'confirmed': 'Stopped', 'unconfirmed': 'not confirmed', 'pending': 'No reply yet', 'none': 'not used'}
RPI_WORDS = {'confirmed': 'read back OFF', 'cancel_sent': 'The discharge work has been sent to stop, etc. RPi read back', 'unreachable': 'The stop command cannot be sent RPi',
             'unconfirmed': 'not confirmed', 'silent': 'The power supply does not respond (Nano Confirmed no output)', 'pending': 'No reply yet',
             'none': 'No need to send'}
# A half that counts as "not confirmed" (SPEC B.4: such a half raises an alarm and fails the round).
# ('silent' is not one of them: the supply does not answer and the Nano is confirmed quiet. It has its own,
# lesser alarm, and a round that was stopped stays stopped.)
PUMP_UNCONFIRMED = ('unconfirmed', 'pending')
RPI_UNCONFIRMED = ('unreachable', 'unconfirmed', 'pending')
NANO_QUIET_STATES = ('IDLE', 'DONE', 'FAULT')


class Busy(RuntimeError):
    """Somebody else holds the equipment (HTTP 409 `busy`). ``code`` names who."""

    def __init__(self, text, code='busy'):
        super().__init__(text)
        self.code = code


class Blocked(RuntimeError):
    """An interlock or a readiness check says no (HTTP 409 `blocked`). ``reasons`` = [{code, text, fix}]."""

    def __init__(self, text, reasons=None):
        super().__init__(text)
        self.reasons = list(reasons or [])


class Tick:
    """The tick “…The program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. ” The kept for later (Devices.tick()
    makes one).

    A schedule keeps the tick of its start dialog for all its rounds. It is a statement about one moment:
    it stops counting when, after it was given, a high-voltage stop went unconfirmed or the RPi reported
    the high voltage ON. Somebody then has to look on site and tick again."""
    __slots__ = ('mark',)

    def __init__(self, mark):
        self.mark = mark


def reason(code, text, fix=''):
    return {'code': code, 'text': text, 'fix': fix}


def reasons_text(reasons):
    """One sentence for a list of reasons (a slot's note, an error line). Several “The pre-discharge inspection failed:”
    rows become one, as the 1.4.1 sentence was."""
    parts, ready = [], False
    for row in reasons or []:
        text = str(row.get('text', ''))
        if row.get('code') == 'rpi_not_ready' and text.startswith(T_RPI_NOT_READY):
            if ready:
                text = text[len(T_RPI_NOT_READY):]
            ready = True
        if text:
            parts.append(text)
    return '; '.join(parts)


def unconfirmed(report):
    """The halves of an all_off() / stop_everything() report that were not confirmed: ['pump'], ['rpi'], both or [].
    (An RPi half 'silent' — the supply does not answer, the Nano is confirmed quiet — is not among them.)"""
    report = report or {}
    found = []
    if (report.get('pump') or {}).get('result') in PUMP_UNCONFIRMED:
        found.append('pump')
    if (report.get('rpi') or {}).get('result') in RPI_UNCONFIRMED:
        found.append('rpi')
    return found


class Devices:
    """One owner at a time, pump / high-voltage mutual exclusion, STOP = everything off."""

    def __init__(self, store, alarms, rpi, pump):
        self.store, self.alarms, self.rpi, self.pump = store, alarms, rpi, pump
        self.lock = threading.Lock()     # the fields below: check-and-set only, never across I/O
        self._owner = None
        self.hv_pending = False          # a round is about to discharge: no pump command goes out any more
        self.hv_active = False           # a discharge runs, or its end was not read back as OFF yet
        self._session = False            # somebody is inside hv_session()
        self._ack = None                 # (owner id) whose “I have gone to the site to confirm that the high-voltage power supply is turned off.” tick is in force
        self._doubts = 0                 # grows with every sign that the high voltage may be on (see Tick)
        self._hv_last = None             # the RPi's last word about the high voltage: None none yet, True off, False on
        self._runner = None
        self._manual = None
        self._watch = None               # the devices-watch thread, once bind() has started it
        self._closed = threading.Event()
        # A latched alarm about the high voltage that was stored by an earlier run of the program is
        # cleared, like any other, once the high voltage is read back as off.
        self._hv_alarm = self._alarm_active(A_HV_KEY)
        pump.set_gate(self.hv_blocks_motion)

    def bind(self, runner, manual):
        """The two users of the equipment this object asks (schedule state, manual activity) and stops.
        The first call also starts the watch (thread `devices-watch`); close() ends it."""
        self._runner, self._manual = runner, manual
        with self.lock:
            if self._watch is None and not self._closed.is_set():
                self._watch = threading.Thread(target=self._watch_loop, name='devices-watch', daemon=True)
                self._watch.start()

    def close(self):
        """The program ends: the watch ends. (Nothing is switched off here: the runner and the two links do
        that when they are closed.)"""
        self._closed.set()
        with self.lock:
            thread = self._watch         # (bind() starts it while it holds the lock: never one that has not begun)
        if thread is not None and thread is not threading.current_thread():
            thread.join(WATCH_JOIN_SECONDS)

    # ============================================================================================
    # Ownership
    # ============================================================================================
    @property
    def owner(self):
        """None, or {kind: 'round' | 'manual' | 'tool', id, label, since}."""
        self._settle_manual()
        with self.lock:
            return dict(self._owner) if self._owner else None

    def acquire(self, kind, id, label):
        """Take the equipment for a round, a manual operation or a maintenance tool; Busy says who has it.

        Asking again with the kind and id one already holds is fine (a second manual pump start)."""
        if kind not in KINDS:
            raise ValueError("kind must be 'round', 'manual' or 'tool'")
        self._settle_manual()
        # Asked before our own lock is taken (lock order: Runner.lock, then Devices.lock).
        held = self._schedule_state() if kind != 'round' else ''
        with self.lock:
            owner = self._owner
            if owner is not None and owner['kind'] == kind and owner['id'] == id:
                return dict(owner)
            if owner is not None:
                if kind == 'round':
                    raise Busy(T_MANUAL_ACTIVE if owner['kind'] == 'manual' else T_TOOL_ACTIVE,
                               'manual_active' if owner['kind'] == 'manual' else 'tool_active')
                if owner['kind'] == 'round':
                    if held == 'paused_round':
                        raise Busy(T_ROUND_PAUSED, 'round_active')
                    raise Busy(T_SCHEDULE_BUSY, 'schedule_active')
                raise Busy(T_OTHER_BUSY, 'manual_active' if owner['kind'] == 'manual' else 'tool_active')
            if held == 'paused_round':
                raise Busy(T_ROUND_PAUSED, 'round_active')
            if held == 'busy':
                raise Busy(T_SCHEDULE_BUSY, 'schedule_active')
            self._owner = {'kind': kind, 'id': id, 'label': str(label or ''), 'since': now_iso()}
            self._ack = None
            return dict(self._owner)

    def release(self, id):
        """Give the equipment back. True when this id held it."""
        with self.lock:
            if self._owner is None or self._owner['id'] != id:
                return False
            self._owner = None
            self._ack = None
            return True

    def _schedule_state(self):
        """'' · 'busy' (a schedule may move something at any moment) · 'paused_round' (a paused round keeps
        the equipment) · 'paused' (paused between rounds: the equipment is free)."""
        runner = self._runner
        if runner is None:
            return ''
        try:
            return runner.ownership() or ''
        except Exception:
            return 'busy'                # a runner that cannot answer counts as busy

    def _settle_manual(self):
        """A manual pump run that has ended by itself (its time was over) gives the equipment back."""
        manual = self._manual
        with self.lock:
            mine = self._owner is not None and self._owner['kind'] == 'manual'
        if manual is not None and mine:
            try:
                manual.settle()
            except Exception:
                pass

    # ============================================================================================
    # Pumps and high voltage never together
    # ============================================================================================
    def rpi_connected(self):
        """Is there a connection to the RPi right now? (Without one the high voltage cannot be read.)"""
        return bool(self.rpi.connected)

    def _hv(self):
        """rpi.hv_view(), and what it says is kept: the RPi's last word about the high voltage (_hv_last),
        and every "ON" is a doubt that voids the ticks given before it (see Tick).

        (A device that answers and reads ON makes the view known, whatever the other one does: rpilink.hv_state.
        "Not known" therefore always means that nothing that can be read says ON.)"""
        view = self.rpi.hv_view()
        if view['known']:
            with self.lock:
                self._hv_last = bool(view['idle'])
                if not view['idle']:
                    self._doubts += 1
                    self._ack = None
        return view

    def hv_ack(self):
        """Must a start be given the tick “I have gone to the site to confirm that the high-voltage power supply is turned off.” right now, and with which sentence?
        -> {'need': bool, 'text': str}; the text is '' when it is not needed.

        It is needed whenever the high voltage cannot be read: no RPi is connected, or the one that is
        connected cannot tell (the rows of SPEC B.4 with `known` false: no status for 5 s, a reading of the
        equipment older than 10 s, a read-back that is not valid — the supply switched off at its mains)."""
        connected = self.rpi_connected()
        view = self._hv()
        if view['known']:
            return {'need': False, 'text': ''}       # it can be read: what is read decides, off or ON
        if not connected:
            return {'need': True, 'text': T_ACK_NO_RPI}
        return {'need': True, 'text': T_ACK_UNKNOWN.format(reason=self._why_unknown(view))}

    @staticmethod
    def _why_unknown(view):
        """The sentence of the SPEC B.4 row that makes this view `known` false."""
        return next((text for text in view['reasons'] if text), T_RPI_NO_STATUS)

    def tick(self, given=True):
        """The tick “…I have gone to the site to confirm that the high-voltage power supply is turned off.” for later use: what this returns is passed
        as ``ack`` each time (a schedule does that for all its rounds). False when it was not ticked."""
        if given is not True:
            return False
        with self.lock:
            return Tick(self._doubts)

    def _ticked(self, ack):
        """Does this tick count? True is a tick that came with this very request. A Tick counts as long as
        nothing has spoken against it since it was given. Anything else is no tick."""
        if ack is True:
            return True
        if isinstance(ack, Tick):
            with self.lock:
                return ack.mark == self._doubts
        return False

    def _settle_hv(self, tell=True):
        """After a discharge whose end could not be read back: the moment the high voltage is known to be
        off, `hv_active` falls and the alarm clears. `tell` False: only the flag (no disk, for the gate)."""
        with self.lock:
            waiting = (self.hv_active and not self._session) or self._hv_alarm
        if not waiting:
            return
        view = self._hv()
        if not (view['known'] and view['idle']):
            return
        with self.lock:
            if self._session:
                return
            self.hv_active = False
            clear = self._hv_alarm and tell
            if clear:
                self._hv_alarm = False
        if clear:
            self._clear(A_HV_KEY)

    def _hv_judge(self, ticked):
        """May a pump or the syringe start now, as far as the high voltage goes?
        -> (reasons, word). reasons [] = it may. ``word`` True: it may on the operator's word alone, because
        the high voltage cannot be read. ``ticked``: a tick that counts (_ticked) came with the request.

        The high voltage can be read (`known`): what was read decides, and a tick is ignored. It cannot (no
        RPi connected, or one that cannot tell): the operator's word decides — also after a discharge whose
        end was never read back (`hv_active` still up), as it would after a restart of this program."""
        with self.lock:
            session, active = self.hv_pending or self._session, self.hv_active
        if session:
            return [reason('hv_on', T_HV_SESSION, FIX_STOP)], False
        connected = self.rpi_connected()
        view = self._hv()
        if view['known']:
            if not view['idle']:
                found = [reason('hv_on', text, FIX_STOP) for text in view['reasons']]
                return found or [reason('hv_on', T_HV_NO_RPI, FIX_STOP)], False
            # `active` here means the end of the last discharge was never read back; _settle_hv() lowers the
            # flag as soon as it is, so this is the moment in between.
            return ([reason('hv_unknown', T_HV_UNCONFIRMED, FIX_STOP)] if active else []), False
        if ticked:
            return [], True
        if not connected:
            return [reason('hv_unknown', T_HV_NO_RPI)], False
        return [reason('hv_unknown', T_HV_UNKNOWN.format(reason=self._why_unknown(view)), FIX_HV_TICK)], False

    def _motion_reasons(self, ticked):
        """Why a pump or the syringe may not start now, as far as the high voltage goes ([] = it may)."""
        return self._hv_judge(ticked)[0]

    def motion_problems(self, ack=False):
        """What permit_motion(ack) would refuse with, as a list of reasons ([] = it would pass). Sends nothing."""
        self._settle_hv()
        return self._motion_reasons(self._ticked(ack))

    def permit_motion(self, ack=False):
        """Called before anything of the pump board is made to move. Raises Blocked with the reasons.

        ``ack``: the tick “…The program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off. ” The — True when it came
        with this very request (a manual start), or what tick() gave when a schedule was started. It counts
        only while the high voltage cannot be read (hv_ack() says when); while it can, only what was read
        decides and a tick is ignored.

        When it passes on a tick, that tick also lowers an `hv_active` that was left up by an unconfirmed
        stop: the operator has been on site after it and says the supply is off."""
        self._settle_hv()
        ticked = self._ticked(ack)
        reasons, word = self._hv_judge(ticked)
        if reasons:
            raise Blocked(T_NO_MOTION, reasons)
        if word:
            # The tick was needed for this start: the wire gate honours it for as long as this owner holds the
            # equipment. (A start that passed because the RPi said "off" has no tick behind it: when the RPi goes
            # away in the middle, or can no longer tell, nothing further is started.)
            with self.lock:
                if ack is not True and ack.mark != self._doubts:
                    return               # a doubt that came up at this very moment: the gate will refuse
                if not (self.hv_pending or self._session):
                    self.hv_active = False
                self._ack = (self._owner['id'] if self._owner else None,)

    def hv_blocks_motion(self):
        """The gate of the pump board (PumpBoard.set_gate): True while no motion command may be written."""
        self._settle_hv(tell=False)
        with self.lock:
            if self.hv_pending or self.hv_active or self._session:
                return True
            ack = self._ack is not None and self._ack == (self._owner['id'] if self._owner else None,)
        view = self._hv()
        if view['known']:
            return not view['idle']
        return not ack                   # it cannot be read: only on the tick of the one who holds the equipment

    @contextlib.contextmanager
    def hv_session(self):
        """Around a round's plasma_run. Entering: `hv_pending` first, then the pumps must be at rest
        (Blocked “The pump is still moving and cannot discharge.” otherwise). Leaving: `hv_active` stays until the high voltage is read
        back as off; when that does not come within HV_SETTLE_SECONDS it stays up and the alarm is raised."""
        self._settle_hv()
        with self.lock:
            if self.hv_pending or self._session:
                raise Blocked(T_NO_PLASMA, [reason('hv_on', T_HV_SESSION, FIX_STOP)])
            # From here on the gate refuses every motion command: a manual start that arrives at this very
            # moment is either refused there, or is seen by the check below.
            self.hv_pending = True
        try:
            problems = self.pumps_moving()
        except BaseException:
            with self.lock:
                self.hv_pending = False
            raise
        with self.lock:
            if problems:
                self.hv_pending = False
            else:
                self.hv_active, self._session, self.hv_pending = True, True, False
        if problems:
            raise Blocked(T_NO_PLASMA, problems)
        try:
            yield self
        finally:
            self._hv_leave()

    def pumps_moving(self):
        """[] when no pump, valve or syringe moves and none can be moving unseen; else the reasons."""
        pump = self.pump
        if pump.motion_uncertain:
            return [reason('pump_uncertain', T_PUMP_UNCERTAIN)]
        if not pump.connected:
            # Without the board nobody can say that the pumps are at rest — unless there is no board.
            return [reason('pump_link', T_PLASMA_PUMP_LINK, FIX_PLASMA_PUMP)] if pump.in_use else []
        limit = time.perf_counter() + VALVE_WAIT_SECONDS
        while True:
            ok, problems = pump.quiescent(fresh=True)
            if ok:
                return []
            if time.perf_counter() >= limit or not self._only_valves_open():
                return problems or [reason('pump_moving', T_PUMP_MOVING)]
            time.sleep(0.1)

    def _only_valves_open(self):
        """True when nothing turns any more and only a linked valve has not closed yet."""
        view = self.pump.view()
        if view.get('link') != 'connected' or view.get('cleaning') or view.get('op') or view.get('motion_uncertain'):
            return False
        if any(group.get('state') != 'idle' for group in view.get('groups') or []):
            return False
        if (view.get('syringe') or {}).get('busy'):
            return False
        return any(valve.get('open') for valve in view.get('valves') or [])

    def _hv_leave(self):
        limit = time.perf_counter() + HV_SETTLE_SECONDS
        while True:
            view = self._hv()
            if view['known'] and view['idle']:
                with self.lock:
                    self.hv_active = self._session = False
                return True
            if time.perf_counter() >= limit:
                break
            time.sleep(HV_POLL_SECONDS)
        with self.lock:
            self._session = False        # hv_active stays: nothing of the pump board may start
            self._doubts += 1            # ... and whoever ticked “The high voltage power supply is off” before this has to look again
            self._ack = None
        self.raise_hv_unconfirmed(T_HV_UNCONFIRMED)
        return False

    # ============================================================================================
    # Alarms of this module
    # ============================================================================================
    def _alarm_row(self, key):
        """The alarm with this key while it is active (not cleared), or None."""
        try:
            return next((row for row in self.alarms.view()['active']
                         if row.get('key') == key and not row.get('cleared')), None)
        except Exception:
            return None

    def _alarm_active(self, key):
        return self._alarm_row(key) is not None

    def raise_hv_unconfirmed(self, text=T_HV_UNCONFIRMED, priority=PRIORITY_URGENT):
        """`hv_stop_unconfirmed`: cleared by this object once the high voltage is read back as off."""
        with self.lock:
            self._hv_alarm = True
        try:
            if priority > PRIORITY_URGENT:
                row = self._alarm_row(A_HV_KEY)
                if row is not None and row.get('priority', priority) < priority:
                    return               # a more serious one is still open: a later, lesser stop does not water it down
            self.alarms.raise_(A_HV_KEY, priority, A_HV_AREA, text, A_HV_KIND)
        except Exception as exc:
            self._event('alarm was not recorded successfully (%s) : %s' % (exc, text), 'error')

    def _clear(self, key):
        try:
            self.alarms.clear(key)
        except Exception:
            pass

    def _event(self, text, level='info', actor=None):
        try:
            self.store.event(text, level, actor)
        except Exception:
            pass

    # ============================================================================================
    # Everything off
    # ============================================================================================
    def _stop_pump(self, box):
        try:
            out = self.pump.stop_all('stop')     # never waits for a running operation
            ok = bool(out.get('confirmed'))
            box.update(result='confirmed' if ok else 'unconfirmed', error='' if ok else str(out.get('error') or ''))
            # Not confirmed: the board has raised `pump_stop_unconfirmed` itself, with what it knows.
        except Exception as exc:
            box.update(result='unconfirmed', error=str(exc) or type(exc).__name__)
            try:
                self.alarms.raise_(A_PUMP_KEY, PRIORITY_URGENT, A_PUMP_AREA, T_PUMP_UNCONFIRMED, A_PUMP_KIND)
            except Exception:
                pass
        finally:
            box['done'] = True

    def _stop_rpi(self, box, serious, ours, limit):
        """``serious``: _hv_serious(), ``ours``: _hv_ours(), both judged when the stop was asked for.
        ``limit``: time.perf_counter() until which the caller waits for this half."""
        asked = time.perf_counter()
        try:
            reply = self.rpi.stop()              # a fresh connection: never behind another command
            if reply.get('cancel_sent'):
                box.update(result='cancel_sent', error='', job=reply.get('job'))
            else:
                box.update(result='confirmed', error='')
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            if isinstance(exc, RuntimeError) and isinstance(exc.__cause__, (OSError, ValueError)):
                box.update(result='unreachable', error=error)
                self.raise_hv_unconfirmed(T_HV_UNREACHABLE, PRIORITY_URGENT if serious else PRIORITY_MEDIUM)
            elif not ours and self._supply_silent(asked, limit):
                # The RPi answered and could not confirm — because the supply does not answer at all, while the
                # Nano is read as quiet and nothing of ours was under way: a lesser alarm, with its own words.
                box.update(result='silent', error=T_HV_SILENT)
                self.raise_hv_unconfirmed(T_HV_SILENT, PRIORITY_MEDIUM)
            else:
                box.update(result='unconfirmed', error=error)
                self.raise_hv_unconfirmed(T_HV_UNCONFIRMED)
        finally:
            box['done'] = True

    def _supply_silent(self, asked, limit):
        """After a stop that the RPi answered but could not confirm: is it "the supply does not answer, and the
        Nano is confirmed quiet"? Judged on what the RPi itself reports after that stop — a reading of the
        equipment that it made since the stop was asked for (``asked``, time.perf_counter(); the RPi makes one
        at the end of every stop) — and on nothing older. Anything that is not exactly that case: False."""
        try:
            status = self.rpi.command('status')
            if not self._read_since(status, asked) and time.perf_counter() < limit:
                status = self.rpi.command('status', fresh=True)      # ... asked for, while the caller still waits
            if not self._read_since(status, asked):
                return False
            job = status.get('job') or {}
            if job.get('state') == 'running' and job.get('high_voltage'):
                return False
            snapshot = status.get('snapshot') or {}
            power, nano = snapshot.get('power'), snapshot.get('nano')
            if not isinstance(power, dict) or not isinstance(nano, dict):
                return False
            return (power.get('valid') is False and nano.get('valid') is True
                    and nano.get('state') in NANO_QUIET_STATES
                    and nano.get('output_command_high') is False and nano.get('output_pin_sample') is False)
        except Exception:
            return False                         # it could not be asked: then nothing is known, and the alarm is Emergency

    @staticmethod
    def _read_since(status, asked):
        """Did the RPi read its equipment after ``asked``? (`age_s` of a status reply: seconds since that
        reading, to a tenth, at the moment of the reply — which is now.)"""
        age = status.get('age_s') if isinstance(status, dict) else None
        if isinstance(age, bool) or not isinstance(age, (int, float)):
            return False
        return age <= time.perf_counter() - asked + 0.05

    def _round_uses_rpi(self):
        """Does a round hold the equipment whose Recipe has a step for the RPi? (A runner that cannot answer: yes.)"""
        runner = self._runner
        if runner is None:
            return False
        try:
            return bool(runner.round_uses_rpi())
        except Exception:
            return True

    def _hv_serious(self):
        """How loud is a stop that cannot reach the RPi? Emergency when there is a reason to think that the high
        voltage is on: a discharge of ours is under way or its end was never read back, a round with a step
        for the RPi holds the equipment, or the last thing the RPi said about the high voltage was not "off".
        Otherwise middle: an RPi that is switched off, or not there at all, is the usual case."""
        self._hv()                       # the RPi's newest word counts
        with self.lock:
            if self.hv_pending or self.hv_active or self._session or self._hv_last is False:
                return True
        return self._round_uses_rpi()    # asked without our lock (lock order: Runner.lock, then Devices.lock)

    def _hv_ours(self):
        """Is a discharge of ours open (or its end not read back yet), or a high-voltage job running on the
        RPi, at this moment? A stop that then comes back unconfirmed is never the harmless "the supply is
        switched off at its mains". Not knowing (no status of the RPi) counts as yes."""
        with self.lock:
            if self.hv_pending or self.hv_active or self._session:
                return True
        status = self.rpi.latest()
        if not isinstance(status, dict):
            return True
        job = status.get('job') or {}
        return job.get('state') == 'running' and bool(job.get('high_voltage'))

    def _halves(self, pump_wanted, rpi_wanted, serious, ours):
        """The two halves at the same time. A half that has not answered in time is 'pending': its thread
        goes on and raises the alarm itself when it turns out not confirmed. (In time: STOP_JOIN_SECONDS;
        for the RPi half, STOP_QUIET_JOIN_SECONDS when neither ``serious`` nor ``ours``.)
        ``serious``: _hv_serious(), ``ours``: _hv_ours() — both as they were when the stop was asked for."""
        boxes = {'pump': {'result': 'none', 'error': '', 'done': True},
                 'rpi': {'result': 'none', 'error': '', 'done': True}}
        threads = []                     # (thread, the moment until which it is waited for)
        began = time.perf_counter()
        limit = began + STOP_JOIN_SECONDS
        # Nothing of ours is discharging and nothing says "on": the RPi gets the time it needs to report a
        # supply that does not answer (STOP_QUIET_JOIN_SECONDS). Otherwise nobody waits past the short limit.
        rpi_limit = limit if serious or ours else began + STOP_QUIET_JOIN_SECONDS
        if pump_wanted:
            boxes['pump'] = {'result': 'pending', 'error': '', 'done': False}
            threads.append((threading.Thread(target=self._stop_pump, args=(boxes['pump'],), name='stop-pump', daemon=True),
                            limit))
        if rpi_wanted:
            boxes['rpi'] = {'result': 'pending', 'error': '', 'done': False}
            threads.append((threading.Thread(target=self._stop_rpi, args=(boxes['rpi'], bool(serious), bool(ours), rpi_limit),
                                             name='stop-rpi', daemon=True), rpi_limit))
        for thread, _ in threads:
            thread.start()
        for thread, until in threads:
            thread.join(max(0.0, until - time.perf_counter()))
        report = {}
        for name, box in boxes.items():
            shown = dict(box) if box.get('done') else {'result': 'pending', 'error': ''}
            shown.pop('done', None)
            report[name] = shown
        return report

    def _pump_wanted(self):
        return bool(self.pump.in_use or self.pump.motion_uncertain)

    def all_off(self, need_pump, need_rpi, ack=False):
        """After a round that failed or was stopped: outputs off, read back. -> {pump{result, error}, rpi{…}}

        The pump half is left out ('none') only when the Recipe has no pump step and the board is at rest (or
        there is no board). The RPi half is left out when the Recipe has no RPi step and either the high
        voltage is known off, or it cannot be read and the round ran on a tick that counts (``ack``, as for
        permit_motion): nothing of ours is to be stopped there, and a stop sent to an RPi that is not there,
        or to a supply that does not answer (switched off at its mains), could only come back unconfirmed."""
        pump_wanted = self._pump_wanted()
        if pump_wanted and not need_pump and not self.pump.motion_uncertain:
            try:
                pump_wanted = not (self.pump.connected and self.pump.quiescent(fresh=False)[0])
            except Exception:
                pump_wanted = True
        view = self._hv()
        with self.lock:
            ours = self.hv_pending or self._session              # a discharge of ours is under way
            stuck = self.hv_active and not ours                  # ... or its end was never read back
        rpi_wanted = bool(need_rpi) or ours or stuck or not (view['known'] and view['idle'])
        if rpi_wanted and not need_rpi and not ours and not view['known'] and self._ticked(ack):
            rpi_wanted = False           # (a tick that counts was given after whatever left `stuck` up)
        if not rpi_wanted:
            return self._halves(pump_wanted, False, False, True)
        return self._halves(pump_wanted, True, bool(need_rpi) or self._hv_serious(), self._hv_ours())

    def stop_everything(self, actor):
        """The STOP button. Never refused, never waits for a step, works when nothing runs."""
        try:
            serious, ours = self._hv_serious(), self._hv_ours()      # judged before anybody is told to stop
        except Exception:
            serious, ours = True, True           # whatever goes wrong here never holds a stop back
        ended = {'schedule': False, 'round': False}
        cancelled = False
        if self._runner is not None:
            try:
                ended.update(self._runner.request_stop() or {})      # no further slot; the round's stop flag
            except Exception as exc:
                self._event('The schedule does not respond when stopped:%r' % (exc,), 'error')
        if self._manual is not None:
            try:
                cancelled = bool(self._manual.cancel())              # the manual operation's stop flag
            except Exception as exc:
                self._event('No response from manual operation when stopped:%r' % (exc,), 'error')
        report = self._halves(self._pump_wanted(), True, serious, ours)
        bad = unconfirmed(report)
        level = 'error' if bad else 'warn' if report['rpi']['result'] == 'silent' else 'info'
        self._event(E_STOP.format(pump=PUMP_WORDS.get(report['pump']['result'], report['pump']['result']),
                                  rpi=RPI_WORDS.get(report['rpi']['result'], report['rpi']['result'])),
                    level, actor)
        return {'accepted': True, 'schedule_ended': bool(ended.get('schedule')),
                'round_stopping': bool(ended.get('round')), 'manual_cancelled': cancelled,
                'pump': report['pump'], 'rpi': report['rpi']}

    # ============================================================================================
    # The watch: no pump keeps moving once the high voltage is seen ON from elsewhere
    # ============================================================================================
    def _watch_loop(self):
        """Thread `devices-watch`. It acts once for each time the two are seen together, and not again
        before they have been seen apart."""
        struck, failed = False, None
        while not self._closed.wait(WATCH_SECONDS):
            try:
                found = self._pump_with_hv()
                if found and not struck:
                    self._strike()
                struck = found
            except Exception as exc:
                # A look that failed says nothing: the next one decides. It is told (once for each kind of
                # fault, not twice a second): a watch that has stopped looking must not go unnoticed.
                if repr(exc) != failed:
                    failed = repr(exc)
                    self._event(E_WATCH_FAILED.format(detail=failed), 'error')

    @staticmethod
    def _board_moving(board):
        """Does this pump.view() show a motor that is not off, the cleaning, a running operation or a busy syringe?"""
        if board.get('cleaning') or board.get('op') or (board.get('syringe') or {}).get('busy'):
            return True
        return any(row.get('state') != 'off' for row in board.get('motors') or [])

    def _pump_with_hv(self):
        """Is the high voltage known to be ON, not by a discharge of ours, while something of the pump board
        moves? A high voltage that is merely not known does not count: that starts nothing new (permit_motion
        and the gate), and stops nothing either."""
        for _ in range(2):
            # Looked at twice. A discharge of ours can end between the reading of the high voltage and the one of
            # our flags (flags down, and the round's next pump turning already): the first look then takes it for a
            # stranger's, and the second one finds the high voltage off.
            view = self._hv()
            if not view['known'] or view['idle']:
                return False
            with self.lock:
                if self.hv_pending or self.hv_active:
                    return False         # our own: the pumps were seen at rest before it began, and the gate is shut
            if not self._board_moving(self.pump.view()):
                return False
        return True

    def _strike(self):
        """Whoever moves the pumps is told to stop, everything of the board is switched off, and an alarm says so."""
        with self.lock:
            in_round = self._owner is not None and self._owner['kind'] == 'round'
        runner, manual = self._runner, self._manual
        if in_round and runner is not None:
            try:
                runner.request_stop()            # no further slot; the round's stop flag
            except Exception as exc:
                self._event('The schedule does not respond when stopped:%r' % (exc,), 'error')
        if manual is not None:
            try:
                manual.cancel()                  # the manual operation's stop flag
            except Exception as exc:
                self._event('No response from manual operation when stopped:%r' % (exc,), 'error')
        try:
            confirmed = bool(self.pump.stop_all('hv').get('confirmed'))
        except Exception:
            confirmed = False
        # Not confirmed: the board has raised `pump_stop_unconfirmed` as well; this one does not say "stopped" then.
        text = T_PUMP_WITH_HV if confirmed else T_PUMP_WITH_HV_UNSURE
        try:
            self.alarms.raise_(A_WATCH_KEY, PRIORITY_URGENT, A_WATCH_AREA, text, A_WATCH_KIND)
        except Exception:
            pass
        self._event(text, 'error')

    # ============================================================================================
    # Start checks (SPEC A.4) and the interlock views
    # ============================================================================================
    def _board_reasons(self, view):
        """Why the pump board cannot be used at all right now ([] = it is connected and its state is known)."""
        link = view.get('link')
        if link == 'unset':
            return [reason('pump_unset', T_PUMP_UNSET, FIX_PUMP_PLUG)]
        if link == 'disabled':
            return [reason('pump_disabled', T_PUMP_DISABLED, FIX_PUMP_ENABLE)]
        found = []
        if link != 'connected':
            found.append(reason('pump_link', T_PUMP_LINK.format(text=view.get('text') or ''), FIX_USB))
        if view.get('motion_uncertain'):
            found.append(reason('pump_uncertain', T_PUMP_UNCERTAIN))
        return found

    def start_problems(self, facts, ack=False):
        """The start checks of SPEC A.4 for a Recipe, evaluated now on what is known (nothing is sent).

        ``facts``: uses_pump, uses_rpi, uses_plasma, uses_scale as recipe.validate() gives them, and either
        uses_weight (bool) or the Recipe's `steps` (to see a dose_weight). ``ack``: the `hv_off_checked` tick.
        Returns [{code, text, fix}]; [] means the Recipe may start."""
        facts = facts if isinstance(facts, dict) else {}
        uses_pump, uses_rpi = bool(facts.get('uses_pump')), bool(facts.get('uses_rpi'))
        uses_plasma, uses_scale = bool(facts.get('uses_plasma')), bool(facts.get('uses_scale'))
        weight = facts.get('uses_weight')
        if weight is None:
            weight = any(isinstance(step, dict) and step.get('action') == 'dose_weight' for step in facts.get('steps') or [])
        self._settle_hv()
        problems = []
        owner = self.owner
        if owner is not None and owner['kind'] == 'manual':
            problems.append(reason('manual_active', T_MANUAL_ACTIVE, FIX_MANUAL))
        elif owner is not None and owner['kind'] == 'tool':
            problems.append(reason('tool_active', T_TOOL_ACTIVE))
        board = self.pump.view()
        if uses_pump:
            found = self._board_reasons(board)
            problems.extend(found)
            if not found:
                problems.extend(self.pump.quiescent(fresh=False)[1])
                scale = board.get('scale') or {}
                if uses_scale:
                    age = scale.get('age_s')
                    if not scale.get('online') or age is None or age > SCALE_FRESH_SECONDS:
                        problems.append(reason('scale_offline', T_SCALE_OFFLINE))
                    elif weight and not scale.get('calibrated'):
                        problems.append(reason('scale_uncalibrated', T_SCALE_UNCALIBRATED, FIX_SCALE))
            problems.extend(self._motion_reasons(self._ticked(ack)))
        if uses_rpi:
            status = self.rpi.latest()
            if status is None:
                problems.append(reason('rpi_link', T_RPI_LINK))
            elif status.get('busy'):
                problems.append(reason('rpi_busy', T_RPI_BUSY))
            elif uses_plasma:
                flow = self.rpi.flow_view()
                ready = status.get('ready') or {}
                if not flow.get('ok'):
                    if flow.get('name') is None:
                        problems.append(reason('rpi_old', T_RPI_OLD))
                    else:
                        problems.append(reason('rpi_not_ready', T_RPI_NOT_READY + str(flow.get('text') or '')))
                elif not ready.get('plasma'):
                    rows = [row for row in ready.get('problems') or [] if isinstance(row, dict)]
                    for row in rows:
                        problems.append(reason('rpi_not_ready', T_RPI_NOT_READY + rpilink.rewrite_reason(str(row.get('reason', ''))),
                                               rpilink.rewrite_fix(str(row.get('fix') or ''))))
                    if not rows:
                        problems.append(reason('rpi_not_ready', T_RPI_NOT_READY + T_RPI_NO_STATUS))
        if uses_plasma and not uses_pump:
            # A Recipe that only measures: no pump may be turning unseen while the high voltage is on.
            if self.pump.motion_uncertain:
                problems.append(reason('pump_uncertain', T_PUMP_UNCERTAIN))
            elif self.pump.in_use:
                if board.get('link') != 'connected':
                    problems.append(reason('pump_link', T_PLASMA_PUMP_LINK, FIX_PLASMA_PUMP))
                elif not self.pump.quiescent(fresh=False)[0]:
                    problems.append(reason('pump_moving', T_NO_PLASMA))
        return problems

    def view(self):
        """`interlock` and `owner` of /api/state (API.md C.6, AMENDMENTS M3). What the server would answer
        now; it checks again when the request arrives. Sends nothing."""
        self._settle_hv()
        owner = self.owner
        held = self._schedule_state()
        board = self.pump.view()
        ack = self.hv_ack()
        manual = []
        if held == 'paused_round':
            manual.append(reason('round_active', T_ROUND_PAUSED))
        elif held == 'busy' or (owner is not None and owner['kind'] == 'round'):
            manual.append(reason('schedule_active', T_SCHEDULE_BUSY))
        elif owner is not None and owner['kind'] == 'tool':
            manual.append(reason('tool_active', T_TOOL_ACTIVE))
        found = self._board_reasons(board)
        manual.extend(found)
        if not found and board.get('cleaning'):
            manual.extend(row for row in self.pump.quiescent(fresh=False)[1] if row.get('code') == 'cleaning')
        # While the high voltage cannot be read the start dialog asks for the tick (need_hv_ack, with its
        # sentence hv_ack_text); that is not a reason by itself.
        manual.extend(self._motion_reasons(True))
        start = []
        if held:
            start.append(reason('schedule_active', T_SCHEDULE_ACTIVE))
        if owner is not None and owner['kind'] == 'manual':
            start.append(reason('manual_active', T_MANUAL_ACTIVE, FIX_MANUAL))
        elif owner is not None and owner['kind'] == 'tool':
            start.append(reason('tool_active', T_TOOL_ACTIVE))
        if board.get('motion_uncertain'):
            start.append(reason('pump_uncertain', T_PUMP_UNCERTAIN))
        return {'interlock': {'manual': {'ok': not manual, 'reasons': manual, 'need_hv_ack': ack['need'],
                                         'hv_ack_text': ack['text']},
                              'start': {'ok': not start, 'reasons': start}},
                'owner': None if owner is None else {key: owner[key] for key in ('kind', 'label', 'since')}}
