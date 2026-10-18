#!/usr/bin/env python3
"""Plasmic electronic control - the overall manager of the entire program (SPEC B.1, B.10, B.11; API.md; AMENDMENTS M5.3, M9, M11, M13) .

Application Connect each completed piece and do not touch the equipment yourself:

    Store (data upgrade)→ Analyst (calibration, concentration)→ Alarms, PinLock → RPi simulator (only practice mode)
    → RpiLink (starts connecting)→ pump control board (don’t start yet)→ Devices (Leave the high-pressure level to the pump control board)
    → Pump control board starts → Runner (Anything that was not completed last time will be recorded as an interruption) A list on→ ManualOps
    → Devices.bind (Devices ’s background check starts here)

Each URL in the screen corresponds to a method here (the URL table is in httpd.py of ROUTES) .This file takes care of four other things:

  1. Maintenance PIN: Which actions are required? PIN written below PIN_ACTIONS,Check only in post() Do it in one place.
     “Stop STOP”Never use PIN,will never be rejected either.
  2. The equipment can only be used by one person at a time: If the equipment or set maintenance actions are to be moved, please contact the Devices Obtain the right to use ”Maintenance Tools”
      (will be rejected if the scheduled measurement is in progress, the process is suspended, or the manual operation is in progress).
  3. program ends (close) : Devices ’s background check ends first. Please schedule a stop, cancel manual operation, and then turn off the pump control panel and
      ((when needed) please RPi Turn off high voltage.
  4. Please Windows don’t fall asleep (M9) ; practice mode replacement RPi The simulator is replaced with an imaginary sample (M11) .

is not transmitted pump_mode of Application(folder) (Old test writing method) does not use the pump control board; practice mode uses the built-in simulation control board.
The real port is only allowed to be opened when the program’s entry point is in official mode. This file does not have that permission.
RPi of token, Maintenance PIN and its hashes will not appear in any replies or events. The only difference between
"""
from __future__ import annotations

import base64
import contextlib
import json
import math
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import alarms as alarms_module
import analyst as analyst_module
import devices as devices_module
import manual as manual_module
import migrate
import netcheck
import pinlock
import pumpboard
import recipe
import rpilink
import runner as runner_module
import schedule
import steps
import store as store_module
from plasmic_client import PlasmicError

VERSION = migrate.PROGRAM
ROOT = Path(__file__).resolve().parent
TCPD = ROOT.parent / 'rpi' / 'tcpd' / 'plasmic_tcpd.py'
PRACTICE_PIN = '000000'
MAINTENANCE = 'Maintenance'                 # who did it, in the event log, for a request with a valid PIN session
SIM_START_TRIES = 50                 # x 0.1 s: how long the RPi simulator gets to start listening
TOOL_JOB_LOST_SECONDS = 30.0
# M9. Module attributes on purpose: read when used, so a test can look at them without Windows.
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
KEEP_AWAKE_POLL_SECONDS = 2.0
# Limits the page shows next to its inputs (bootstrap.limits, API.md C.3.1).
SCALE_GRAMS_MIN, SCALE_GRAMS_MAX = 0.1, 20000

# ---- texts (API.md C.1, C.4; SPEC B.7; AMENDMENTS M3, M5.3) ----------------------------------------------
T_BODY = 'need JSON object'
T_LOCKED = 'This operation is only for maintenance personnel (maintenance PIN) '
T_PIN_UNSET = 'has not been set for maintenance PIN'
T_OPERATOR = 'operator is not in the list'
T_TOOL = 'Unsupported command'
T_DEMO_CONFIG = 'simulation mode is fixedly connected to the local simulator'
T_NO_RECIPE = 'Recipe does not exist'
T_RECIPE_NAME = 'Recipe The name must be 1–60 characters'
T_RECIPE_INVALID = 'Recipe failed the inspection'
T_RECIPE_IN_USE = 'this Recipe is being used in scheduling'
T_FLOW_IN_USE = 'Scheduling is in progress: the process can only be modified after stopping.'
E_FLOW_SAVED = 'Saved process ({count} step):{steps}'
E_FLOW_SAVED_OPEN = 'Saved process ({count} step, not yet available:{error}) '
FLOW_EVENT_STEPS = 12                # steps an event names; the rest is “…wait n step”
T_PUMP_CAL = 'The content of pump calibration is incorrect.'
E_PUMP_SETUP = 'Pump settings saved:{summary}'
E_SETUP_FLOW = 'The process of the schedule page has been rechecked according to the new settings:{steps}'
E_SETUP_FLOW_OPEN = 'The process on the schedule page cannot be used according to the new settings ({detail}) : Please go to ”Scheduling Measurement” to change the steps'
T_FLOW_RECIPE = 'Scheduling page process'
PUMP_CAL_ML = 10.0                   # what “beat 10 mL” of Maintenance › Pump and Scale doses
T_RECIPE_SOURCE = 'please provide file_b64, csv or rows one of them'
T_NO_PARAMETERS = 'Unable to read plasma parameters'
T_NO_LINK = 'Not connected yet RPi: '
T_ALARM_IDS = 'Please specify the alarm to be acknowledged'
T_SCALE_GRAMS = pumpboard.T_SCALE_GRAMS
T_BLANK_ZERO = 'Blank zero point setting format is incorrect'
T_ROUND_UNREADABLE = 'The record file of this round cannot be read.'
L_TOOL = {'prepare': 'Maintenance: Sync to Nano', 'clear_fault': 'Repair: Clear Nano fault', 'spectrum': 'Maintenance: Capture spectrum (no discharge)'}
L_PARAMETERS = 'Maintenance: Storing plasma parameters'
L_RPI_CONFIG = 'repair: RPi connection settings'
L_PUMP_CONFIG = 'Repair: Pump Control Board Connection Port'
L_PUMP_RECONNECT = 'Repair: Pump control board rewired'
L_PUMP_SETTINGS = 'Maintenance: Syringe pump settings'
L_SCALE = 'Maintenance: Scale reset to zero/Correction'
L_PUMP_CAL = 'Maintenance: Pump Calibration'
L_PUMP_SETUP = 'Maintenance: What to connect to the pump The'
L_COEFFICIENTS = 'Maintenance: Correction coefficient'
E_UNLOCK = 'Maintenance PIN Unlock'
E_LOCK = 'Maintenance mode has been logged out'
E_OPERATOR = 'Select operator:{name}'
E_OPERATORS = 'Operator list updated: Total {count} people'
E_PUBLISHED = 'Published Recipe: {name}'
E_DELETED = 'Deleted Recipe: {name}'
E_CONFIG = 'Change the connection settings to {host}:{port}'
E_PREPARED = 'Synced Nano parameter'
E_CHECK = 'Recheck the device:{rows}'
E_CLEARED = 'Clear Nano Fault: {what}'
E_CAPTURE = 'Start capturing spectrum (no discharge)'
E_PARAMETERS = 'Stored plasma parameters:{values}'
E_SYNC_FAILED = 'Parameters have been saved but synchronized Nano failed:{detail}'
E_PUMP_ON = 'pump control board is set to use {device}'
E_PUMP_OFF = 'Pump control board is set to not use'
E_PUMP_RECONNECT = 'The pump control board has been asked to rewire'
E_PUMP_SETTINGS = 'Syringe pump settings: Syringe {size:g} mL, speed {speed:g} mL/s'
E_TARE = 'The scale has returned to zero'
E_SCALE = 'The scale has been calibrated (weight {grams:g} g) '
E_APPLIED = 'Correction applied {id}: {metals}'
E_COEFFICIENTS = 'The correction coefficient has been stored'
E_BLANK_QC = 'Blank threshold has been stored'
E_BLANK_ZERO = 'Blank zero setting has been stored'
E_REANALYZE = 'reanalysis calibration {id}'
E_EXCLUDE = 'Correction {id} ’s inclusion settings have been changed For'
E_SIM_DEAD = 'RPi emulator does not start (port {port} may have been used by other programs); recorded in {log}'
E_STOP_BROKEN = 'Program error when stopping ({detail}) : has directly connected the pump control board and RPi Sending stopped'
E_CLOSE_STOP = 'Program end: requested RPi Turn off high voltage'
E_CLOSE_STOP_FAILED = 'The stop command was not sent at the end of the program. RPi: {detail}'

# The PIN table (SPEC B.10, API.md C.4, AMENDMENTS M5.3): the methods behind the POST routes that need a
# maintenance session. Everything else is open, and every GET is. Checked in post(), nowhere else.
PIN_ACTIONS = frozenset({
    'set_operators',
    'recipe_validate', 'recipe_publish', 'recipe_delete',          # delete also wants the PIN typed again
    'rpi_config', 'rpi_find', 'rpi_diagnose', 'rpi_tool',
    'pump_config', 'pump_reconnect', 'pump_settings', 'scale_tare', 'scale_calibrate',
    'pump_calibrate', 'pump_calibrate_reset', 'pump_setup_save',
    'calibration_apply', 'set_coefficients', 'set_blank_qc', 'set_blank_zero',
})
OPEN_ACTIONS = frozenset({
    'unlock', 'lock', 'set_operator', 'stop', 'pause', 'resume', 'schedule_preview', 'schedule_start',
    'alarms_ack', 'manual_op', 'flow_save',
    'set_parameters',            # M17: the schedule page sets them too; still refused while a schedule is busy
    'rpi_check',                 # M18: “Recheck” — a reading of the supply and the Nano taken now; nothing is switched
    'calibration_plan', 'calibration_check', 'calibration_start', 'calibration_confirm', 'calibration_analyze',
    'calibration_exclude',
})
POST_ACTIONS = PIN_ACTIONS | OPEN_ACTIONS


class ApiError(RuntimeError):
    """A request that is answered with something other than 200: HTTP status, code and the text for the
    screen (API.md C.1). For the cases a plain ValueError / RuntimeError / KeyError cannot express."""

    def __init__(self, status, code, text, reasons=None):
        super().__init__(text)
        self.status, self.code, self.text, self.reasons = status, code, text, reasons


def set_execution_state(flags):
    """M9: ask Windows to stay awake (ES_CONTINUOUS | ES_SYSTEM_REQUIRED) or give that back (ES_CONTINUOUS).
    A request of the calling thread of this process only; no setting of the computer is changed."""
    if sys.platform != 'win32':
        return
    import ctypes
    call = ctypes.windll.kernel32.SetThreadExecutionState
    call.argtypes, call.restype = [ctypes.c_uint], ctypes.c_uint
    call(flags)


def parameters_text(values):
    """The seven plasma parameters as one line of the event log."""
    if not isinstance(values, dict):
        return '—'
    parts = (('Ontime', 'ON {} ms'), ('Offtime', 'OFF {} ms'), ('Cycle', 'every round {} pulses'), ('Loopnum', '{} wheel'),
             ('Integration_Time', 'points {} ms'), ('Spectra_Interval', 'interval {} ms'), ('Voltage', '{} V'))
    return ', '.join(text.format('%g' % values[key] if isinstance(values.get(key), (int, float)) else '—')
                    for key, text in parts)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class _Hold:
    """The equipment taken for one maintenance tool. keep(): somebody else gives it back later."""

    def __init__(self, devices, token):
        self.devices, self.token, self.kept = devices, token, False

    def keep(self):
        self.kept = True

    def release(self):
        self.devices.release(self.token)


class Application:
    def __init__(self, data_dir, demo=False, auto_sync=True, pump_mode=None, pump_overrides=None, sim_port=8767,
                 keep_awake=False):
        self.demo = bool(demo)
        self.data_dir = Path(data_dir)
        self.boot_id = secrets.token_hex(8)      # the page reloads itself when this changes
        self.operator = ''                       # in memory only; shared by every tab; '' after a restart
        self._lock = threading.Lock()            # the operator
        self._close_lock = threading.Lock()
        self._closed = False
        self._ui_seen = time.monotonic()         # when a page last asked for the state
        self._recipes_bump = 0                   # settings that change what the Recipe list says
        self._recipe_count = (None, 0)
        self._sim_samples = 0
        self._awake_stop = threading.Event()
        self._awake_thread = None
        self.simulator = self.simulator_log = None
        self.store = self.analyst = self.alarms = self.pin = self.rpi = None
        self.pump = self.devices = self.runner = self.manual = None
        try:
            self._build(auto_sync, pump_mode, pump_overrides, sim_port, keep_awake)
        except BaseException:
            self.close(fast=True)                # never leave a thread, a board or a simulator behind
            raise

    def _build(self, auto_sync, pump_mode, pump_overrides, sim_port, keep_awake):
        self.store = store_module.Store(self.data_dir)                           # the data migration runs first
        self.analyst = analyst_module.Analyst(self.data_dir, self.store.load_run, self.store.write_json,
                                              self._analyst_event, self._analyst_alarm)
        self.alarms = alarms_module.Alarms(self.store)
        # Practice mode: the fixed PIN, and no file of the data folder is read for it.
        self.pin = (pinlock.PinLock(None, practice_pin=PRACTICE_PIN) if self.demo
                    else pinlock.PinLock(self.data_dir / 'pin.json'))
        if self.demo:
            self.store.config = dict(store_module.DEFAULT_CONFIG, rpi_host='127.0.0.1', rpi_port=int(sim_port))
            self._start_simulator(int(sim_port))
        self.rpi = self.link = rpilink.RpiLink(self.store, search=not self.demo, auto_sync=auto_sync)
        self.rpi.on_alarm = self._device_alarm
        mode = pump_mode or ('sim' if self.demo else 'off')
        if self.demo and mode == 'real':
            mode = 'sim'                         # practice mode never gets near a real port
        self.pump = pumpboard.PumpBoard(self.data_dir, mode, self.store, None, self._device_alarm, self._ui_age,
                                        overrides=pump_overrides)
        self.devices = devices_module.Devices(self.store, self.alarms, self.rpi, self.pump)      # sets the board's gate
        self.pump.start()                        # connects in the background; the page is usable meanwhile
        self.runner = runner_module.Runner(self.store, self.alarms, self.devices, self.rpi, self.pump, self._operator,
                                           analyst=self.analyst,
                                           before_discharge=self._sim_sample if self.demo else None)
        self.manual = manual_module.ManualOps(self.store, self.devices, self.pump, self._operator)
        self.devices.bind(self.runner, self.manual)
        if keep_awake:
            self._awake_thread = threading.Thread(target=self._keep_awake, name='keep-awake', daemon=True)
            self._awake_thread.start()

    # ============================================================================================
    # Wiring
    # ============================================================================================
    def _operator(self):
        return self.operator

    def _ui_age(self):
        """Seconds since a page last asked for the state (the pump board's page watchdog reads this)."""
        return time.monotonic() - self._ui_seen

    def _event(self, text, level='info', actor=None):
        self.store.event(text, level, actor)

    def _analyst_event(self, text, level='info'):
        self.store.event(text, level)

    def _analyst_alarm(self, key, priority, area, text, kind='event'):
        self.alarms.raise_(key, priority, area, text, kind or 'event')

    def _schedule_uses(self, fact):
        """Does a schedule that may move something at any moment use this side of the equipment?"""
        record = getattr(self.runner, 'record', None)
        return bool(record and record.get('status') in schedule.BUSY and record.get(fact))

    def _device_alarm(self, action, key, priority=None, area=None, text=None, kind=None):
        """The alarms of the two links go straight into the alarm list (AMENDMENTS M10, M13). A lost link
        is high while a busy schedule uses that side (SPEC B.9); the links themselves do not know about schedules."""
        if action != 'raise':
            self.alarms.clear(key)
            return
        if key in ('rpi_link_lost', 'pump_link_lost') and type(priority) is int:
            if self._schedule_uses('uses_rpi' if key == 'rpi_link_lost' else 'uses_pump'):
                priority = min(priority, 2)
        self.alarms.raise_(key, priority, area, text, kind or 'event')

    def _start_simulator(self, port):
        """Practice mode: the RPi side is `plasmic_tcpd.py --sim` in a process of its own (as 1.4.1, plus -B)."""
        sim_dir = self.data_dir / 'sim'
        sim_dir.mkdir(parents=True, exist_ok=True)
        log = sim_dir / 'tcpd.log'
        self.simulator_log = log.open('a', encoding='utf-8')
        self.simulator = subprocess.Popen(
            [sys.executable, '-B', str(TCPD), '--sim', '--bind', '127.0.0.1', '--port', str(port), '--sim-dir', str(sim_dir)],
            stdout=self.simulator_log, stderr=subprocess.STDOUT)
        for _ in range(SIM_START_TRIES):         # give the simulator a moment to start listening
            if self.simulator.poll() is not None:
                self._event(E_SIM_DEAD.format(port=port, log=log), 'error')
                return
            try:
                socket.create_connection(('127.0.0.1', port), timeout=.2).close()
                return
            except OSError:
                time.sleep(.1)

    def _sim_sample(self, info):
        """M11, practice mode only: before a discharge the RPi simulator is told what the pretend sample holds.
        A standard of a calibration: its concentrations; a blank: nothing; a sample: one that drifts slowly."""
        hello = self.rpi.hello or {}
        if not self.demo or hello.get('sim') is not True:
            return
        if info.get('conc') is not None:
            sample = {metal: float(info['conc'].get(metal) or 0) for metal in self.analyst.METALS}
        elif info.get('sample_kind') == 'blank':
            sample = {metal: 0.0 for metal in self.analyst.METALS}
        else:
            n = self._sim_samples                # sample rounds since the program started
            self._sim_samples += 1
            sample = {'Cu': round(0.50 + 0.10 * math.sin(n / 3), 4), 'Zn': round(0.30 + 0.05 * math.sin(n / 5), 4),
                      'Ni': round(0.40 + 0.08 * math.cos(n / 4), 4)}
        self.rpi.command('sim_sample', sample=sample)

    # ---- M9 ----------------------------------------------------------------------------------------
    def _working(self):
        """A schedule is active (that covers a calibration that waits), or a round, a manual operation or a
        maintenance tool holds the equipment."""
        return bool(self.runner.active or self.manual.active or self.devices.owner is not None)

    def _keep_awake(self):
        held = False
        try:
            while True:
                try:
                    wanted = self._working()
                except Exception:
                    wanted = held                # whatever went wrong while looking: change nothing
                if wanted != held:
                    set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED if wanted else ES_CONTINUOUS)
                    held = wanted
                if self._awake_stop.wait(KEEP_AWAKE_POLL_SECONDS):
                    break
        finally:
            if held:
                set_execution_state(ES_CONTINUOUS)

    # ============================================================================================
    # PIN, the caller, ownership for maintenance tools
    # ============================================================================================
    def require_pin(self, session):
        """The gate of a PIN route: passing it renews the session (600 s sliding); nothing else does."""
        if not self.pin.configured:
            raise ApiError(403, 'pin_unset', T_PIN_UNSET)
        if not self.pin.allowed(session):
            raise ApiError(403, 'locked', T_LOCKED)

    def post(self, name, body, session=None):
        """One POST as the page sends it: the PIN gate, then the JSON object, then the method of that route."""
        if name not in POST_ACTIONS:
            raise KeyError(name)
        if name == 'stop':                       # nothing stands before STOP: no gate, and nothing of its body is wanted
            return self.stop(body, self._actor(session, False))
        gated = name in PIN_ACTIONS
        if gated:
            self.require_pin(session)
        if not isinstance(body, dict):
            raise ValueError(T_BODY)
        if name == 'lock':
            return self.lock(body, session)
        return getattr(self, name)(body, self._actor(session, gated))

    def _actor(self, session, gated):
        """Who did it, for the event log: “Maintenance” with a valid PIN session, else the operator (may be '')."""
        try:
            return MAINTENANCE if gated or self.pin.peek(session) else self.operator
        except Exception:
            return None                          # “system” On: not knowing who pressed must never stop a request

    @contextlib.contextmanager
    def _tool(self, label):
        """The equipment for the length of one maintenance action (SPEC B.4 "ownership"). devices.Busy when a
        schedule is busy, a round is paused, or a manual operation or another tool runs."""
        token = 'tool-' + uuid.uuid4().hex
        self.devices.acquire('tool', token, label)
        hold = _Hold(self.devices, token)
        try:
            yield hold
        finally:
            if not hold.kept:
                hold.release()

    def _rpi(self, cmd, **fields):
        """One command to the RPi. No connection, or no reply: 503 `rpi_link`; the RPi's own refusal passes."""
        try:
            return self.rpi.command(cmd, **fields)
        except PlasmicError:
            raise
        except RuntimeError as exc:
            raise ApiError(503, 'rpi_link', str(exc)) from exc

    # ============================================================================================
    # GET
    # ============================================================================================
    def bootstrap(self):
        """API.md C.3.1 without `csrf` (the HTTP layer adds the page token)."""
        config = self.store.config
        hardware = self.pump.hardware()
        settings = self._settings()
        return {'version': VERSION, 'demo': self.demo, 'boot_id': self.boot_id, 'pin_set': self.pin.configured,
                'practice_pin': PRACTICE_PIN if self.demo else None,
                'ui_watchdog_s': rpilink.UI_WATCHDOG_SECONDS,
                'config': {'rpi_host': config['rpi_host'], 'rpi_port': config['rpi_port'],
                           'token_set': bool(config['token'])},
                'hardware': hardware,
                'limits': {'run_max_s': manual_module.RUN_MAX_S, 'flow_min_ml_min': manual_module.FLOW_MIN,
                           'dose_min': manual_module.DOSE_MIN, 'dose_max': manual_module.DOSE_MAX,
                           'calibration_min_g': SCALE_GRAMS_MIN, 'calibration_max_g': SCALE_GRAMS_MAX},
                'recipe': {'limits': dict(recipe.LIMITS),
                           'examples': [dict(row) for row in recipe.EXAMPLES],
                           'actions': recipe.actions_view(hardware, settings.get('syringe_ul') or recipe.DEFAULT_SYRINGE_UL)},
                'labels': {'schedule': dict(schedule.STATUS_LABEL), 'slot': dict(schedule.SLOT_LABEL),
                           'round': dict(schedule.ROUND_LABEL), 'step': dict(schedule.STEP_LABEL)}}

    def _recipes_published(self):
        """How many Recipes are published; counted again only when the list changed."""
        rev = self.store.recipes_rev
        if self._recipe_count[0] != rev:
            self._recipe_count = (rev, len(self.store.recipes()))
        return self._recipe_count[1]

    @staticmethod
    def _board_word(pump):
        """setup.board: ok | unset | off (Not used) | problem."""
        link = pump.get('link')
        return {'connected': 'ok', 'unset': 'unset', 'disabled': 'off'}.get(link, 'problem')

    def _measurement(self):
        """state.measurement (AMENDMENTS M5.3): the newest sample and the newest blank, shortened."""
        latest = self.analyst.latest()
        sample, blank = latest.get('latest_sample'), latest.get('latest_blank')
        out = {'latest_sample': None, 'latest_blank': None}
        if isinstance(sample, dict):
            metals = sample.get('metals') or {}
            out['latest_sample'] = {
                'time': sample.get('time'), 'valid': sample.get('valid') is True, 'conc_reason': sample.get('conc_reason', ''),
                'metals': {metal: {'conc': (metals.get(metal) or {}).get('conc'), 'zero': (metals.get(metal) or {}).get('zero')}
                           for metal in self.analyst.METALS}}
        if isinstance(blank, dict):
            check = blank.get('qc') or {}
            out['latest_blank'] = {'id': blank.get('id'), 'time': blank.get('time'),
                                   'qc': {'status': check.get('status'), 'problems': list(check.get('problems') or []),
                                          'reason': check.get('reason', '')}}
        return out

    def state(self, session=None):
        """GET /api/state (API.md C.6, AMENDMENTS M5.3, M13). Feeds the page watchdogs; never renews a session."""
        self._ui_seen = time.monotonic()
        self.rpi.ui_alive()
        left = self.pin.peek(session)
        pump = self.pump.view()
        interlock = self.devices.view()
        operators = self.store.operators()
        recipes = self._recipes_published()
        board = self._board_word(pump)
        pin_set = self.pin.configured
        connected_once = bool(self.rpi.was_connected)
        newest = self.store.events(limit=1)
        runs = self.store.list_runs(1)
        active = self.analyst.active()
        return {
            'version': VERSION, 'boot_id': self.boot_id,
            'time': datetime.now().astimezone().isoformat(timespec='seconds'),
            'demo': self.demo, 'pin_set': pin_set, 'unlocked': left > 0, 'unlock_left_s': left,
            'operator': self.operator, 'operators': operators,
            'setup': {'pin': pin_set, 'rpi': connected_once, 'board': board, 'operators': len(operators),
                      'recipes': recipes,
                      'complete': bool(pin_set and connected_once and board in ('ok', 'off') and operators and recipes)},
            'owner': interlock['owner'],
            'message': ({key: newest[0][key] for key in ('time', 'level', 'text')} if newest else None),
            'revs': {'events': self.store.events_rev, 'recipes': self.store.recipes_rev + self._recipes_bump,
                     'runlog': self.store.runlog_rev, 'runs': self.store.runs_rev,
                     'measurements': self.analyst.revs['measurements'], 'calibrations': self.analyst.revs['calibrations']},
            'rpi': self.rpi.view(),
            'pump': pump,
            'interlock': interlock['interlock'],
            'schedule': self.runner.view(),
            'round': self.runner.round_view(),
            'latest_run': runs[0] if runs else None,
            'alarms': self.alarms.view(),
            'measurement': self._measurement(),
            'calibration_active': (None if not active else
                                   {key: active.get(key) for key in ('calibration_id', 'applied_at', 'metals')}),
            'calibration_run': self.runner.calibration_view(),
        }

    # ---- Recipes ------------------------------------------------------------------------------------
    def _settings(self):
        try:
            return self.pump.view().get('settings') or {}
        except Exception:
            return {}

    def _check(self, text, encoding='utf-8'):
        """(recipe.validate() with the settings of this moment, the estimate of one round in seconds)."""
        hardware = self.pump.hardware()
        settings = self._settings()
        checked = recipe.validate(text, hardware, settings.get('syringe_ul') or recipe.DEFAULT_SYRINGE_UL, encoding)
        status = self.rpi.latest() or {}
        parameter = (status.get('snapshot') or {}).get('parameter') or {}
        integration = parameter.get('integration_ms') if parameter.get('valid') is True else None
        seconds = recipe.estimate(checked, hardware, status.get('estimate_s'), settings.get('syringe_speed_ml_s'), integration)
        return checked, seconds

    def _recipe_entry(self, record):
        """(the row of GET /api/recipes, the check result or None) for one stored Recipe.
        A file that no longer is what was published cannot be checked: its row says so and it never runs."""
        row = {key: record.get(key) for key in ('id', 'name', 'created_at', 'source')}
        if record.get('error') or not isinstance(record.get('csv'), str):
            row.update({key: record.get(key) for key in store_module.RECIPE_FACTS})
            row.update(estimate_seconds=None, valid=False, error=record.get('error') or store_module.RECIPE_CHANGED)
            return row, None
        checked, seconds = self._check(record['csv'])
        if record.get('id') == store_module.FLOW_RECIPE_ID:
            checked = recipe.as_flow(checked)
        row.update({key: checked[key] for key in store_module.RECIPE_FACTS})
        row.update(estimate_seconds=int(round(seconds)), valid=checked['valid'], error=checked['error'])
        return row, checked

    def recipes(self):
        """The published Recipes, checked against the settings of this moment (API.md C.3.3)."""
        return [self._recipe_entry(record)[0] for record in self.store.recipes()]

    def recipe_detail(self, recipe_id):
        record = next((item for item in self.store.recipes() if item['id'] == recipe_id), None)
        if record is None:
            raise KeyError(recipe_id)
        row, checked = self._recipe_entry(record)
        if checked is None:
            checked = dict(recipe.validate('', None), error_line=None)   # the empty shape: nothing of this file is shown
            row.update(csv='', sha256=record.get('sha256'))
        else:
            row.update(csv=record['csv'], sha256=record.get('sha256'))
        for key in ('error_line', 'warnings', 'delay_seconds', 'plasma_count', 'phases', 'liquids', 'uses_motors',
                    'uses_syringe', 'uses_scale', 'steps', 'rows'):
            row[key] = checked[key]
        return row

    def recipe_csv(self, recipe_id):
        """The published CSV as a file for Excel: UTF-8 with BOM."""
        try:
            return self.store.recipe(recipe_id)['csv'].encode('utf-8-sig')
        except ValueError as exc:                # the file is not what was published any more
            raise ApiError(409, 'state', str(exc)) from None

    def example_csv(self, name):
        if name not in {row['file'] for row in recipe.EXAMPLES}:
            raise KeyError(name)
        try:
            return (recipe.EXAMPLES_DIR / name).read_text(encoding='utf-8-sig').encode('utf-8-sig')
        except OSError:
            raise KeyError(name) from None

    # ---- the flow of the schedule page: a list of steps (AMENDMENTS M23; the four stages of M16 before) ----
    def _flow_steps(self):
        """The step list as it is kept: flow.json of today, the four stages of M16 made into steps, or — nothing
        saved yet, or a file nobody can read — only the measurement."""
        saved = (self.store.flow() or {}).get('flow')
        if isinstance(saved, dict) and 'steps' in saved:
            try:
                return recipe.flow_steps_csv(saved['steps'])[1]
            except ValueError:
                return recipe.flow_default_steps()
        if isinstance(saved, dict):
            try:
                return recipe.flow_stage_steps(saved, self.pump.hardware())
            except ValueError:
                pass
        return recipe.flow_default_steps()

    @staticmethod
    def _flow_event(checked):
        texts = [step['text'] for step in checked['steps']]
        more = len(texts) - FLOW_EVENT_STEPS
        return '; '.join(texts[:FLOW_EVENT_STEPS]) + ('…wait %d step' % len(texts) if more > 0 else '')

    def flow(self):
        """GET /api/flow: the steps as saved, what the check says about them, the Recipe they are kept as, and what
        the step dialogs of the page need (the pumps, the valve positions, the range of every number)."""
        steps = self._flow_steps()
        checked, seconds = self._check(recipe.flow_steps_csv(steps)[0])
        checked = recipe.as_flow(checked)
        hardware = self.pump.hardware() or {}
        cleaning = hardware.get('cleaning') or {}
        out_motor, water_motor = cleaning.get('out_motor'), cleaning.get('water_motor')
        pumps = [{'pump': motor.get('pump'), 'motor': motor.get('id'), 'label': motor.get('label'),
                  'short': motor.get('short'), 'group': group.get('id'), 'group_label': group.get('label'),
                  'drain': motor.get('id') == out_motor, 'water': motor.get('id') == water_motor}
                 for group in hardware.get('groups') or [] for motor in group.get('motors') or []]
        record = next((item for item in self.store.recipes() if item['id'] == store_module.FLOW_RECIPE_ID), None)
        settings = self._settings()
        limits = {row['action']: row['value'] for row in recipe.actions_view(self.pump.hardware(), settings.get('syringe_ul')
                                                                             or recipe.DEFAULT_SYRINGE_UL)
                  if row['action'] in recipe.FLOW_ACTIONS and row['value']}
        line = checked['error_line']
        return {'steps': steps,
                # one per step, in the same order: its sentence, and whether the check stopped at it
                'view': [{'text': row['text'], 'ok': row['ok']} for row in checked['rows']],
                'valid': checked['valid'], 'error': checked['error'], 'error_step': None if line is None else line - 1,
                'warnings': checked['warnings'], 'estimate_seconds': int(round(seconds)),
                'uses_plasma': checked['uses_plasma'], 'uses_syringe': checked['uses_syringe'],
                'recipe_id': record['id'] if record else None, 'name': record['name'] if record else '',
                'pumps': pumps, 'weighing': bool((hardware.get('gravimetric') or {}).get('enabled')),
                'clean_seconds': cleaning.get('total_s'), 'dose_flow_ml_min': hardware.get('dose_flow_ml_min'),
                'valve_positions': int((hardware.get('syringe') or {}).get('valve_positions') or 0),
                'limits': limits, 'max_steps': recipe.FLOW_STEPS_MAX}

    def flow_save(self, body, actor=None):
        """The steps are kept as they stand, also while they do not make a round yet (a syringe that is filled and
        not emptied so far): the Recipe with the fixed id then says so, and nothing starts it."""
        text, steps = recipe.flow_steps_csv(body.get('steps'))
        if self.runner.uses_recipe(store_module.FLOW_RECIPE_ID):
            raise RuntimeError(T_FLOW_IN_USE)
        checked, _seconds = self._check(text)
        self.store.put_recipe(store_module.FLOW_RECIPE_ID, recipe.FLOW_STEPS_NAME.format(count=len(steps)), text,
                              checked, 'flow')
        self.store.save_flow({'steps': steps})
        if checked['valid']:
            self._event(E_FLOW_SAVED.format(count=len(steps), steps=self._flow_event(checked)), actor=actor)
        else:
            self._event(E_FLOW_SAVED_OPEN.format(count=len(steps), error=recipe.flow_words(checked['error'])), actor=actor)
        return self.flow()

    # ---- lists --------------------------------------------------------------------------------------
    def runlog(self, before=None, limit=50):
        rows = self.store.runlog(before, limit + 1)          # one more than shown: is there more?
        return {'rows': rows[:limit], 'more': len(rows) > limit}

    def events(self, before=None, limit=50):
        rows = self.store.events(before, limit + 1)
        return {'events': rows[:limit], 'more': len(rows) > limit}

    def round_record(self, round_id):
        try:
            record = self.store.round(round_id)
        except ValueError:
            raise ApiError(404, 'not_found', T_ROUND_UNREADABLE) from None
        # The words for each step's result, as state.round.steps_window carries them: one rule, in the runner.
        for step in record.get('steps') or []:
            if isinstance(step, dict):
                step['result_text'] = runner_module.step_result_text(step)
        return record

    def runs(self, limit=200):
        return {'runs': self.store.list_runs(limit)}

    def run(self, job):
        try:
            return self.store.load_run(job)
        except (OSError, ValueError):
            raise KeyError(job) from None

    def run_csv(self, job):
        return store_module.run_csv(self.run(job))

    def pump_ports(self):
        return self.pump.ports()

    # ---- correction, trend -----------------------------------------------------------------------------------
    def calibration(self):
        """GET /api/calibration (AMENDMENTS M5.3)."""
        settings = self.analyst.settings_view()
        rows = []
        for record in self.store.recipes():
            row, checked = self._recipe_entry(record)
            rows.append({'id': row['id'], 'name': row['name'], 'plasma_count': checked['plasma_count'] if checked else 0,
                         'uses_rpi': bool(row.get('uses_rpi')), 'valid': row['valid']})
        return {'plan': self.analyst.plan(), 'records': self.analyst.records(), 'active': self.analyst.active(),
                'coefficients': self.analyst.coefficients(), 'recipes': rows, 'unit': self.analyst.UNIT,
                'metals': list(self.analyst.METALS),
                'settings': {'method_id': settings['method_id'], 'error': settings['error']},
                'run': self.runner.calibration_view()}

    def _analyst(self, call, *args):
        """A call that names a calibration record: an unknown one is 404 with the Analyst's own words."""
        try:
            return call(*args)
        except KeyError as exc:
            text = exc.args[0] if exc.args and isinstance(exc.args[0], str) else analyst_module.NOT_FOUND
            raise ApiError(404, 'not_found', text) from None

    def calibration_record(self, cal_id):
        return self._analyst(self.analyst.record, cal_id)

    def measurements(self, hours=None):
        return self.analyst.measurements(hours)

    # ============================================================================================
    # POST — open routes
    # ============================================================================================
    def unlock(self, body, actor=None):
        try:
            session = self.pin.unlock(body.get('pin'))
        except RuntimeError as exc:              # wrong PIN, too many tries, no PIN set
            raise ApiError(409, 'pin', str(exc)) from None
        self._event(E_UNLOCK, actor=MAINTENANCE)
        return {'session': session, 'expires_in': self.pin.SESSION_SECONDS}

    def lock(self, body=None, session=None):
        ended = bool(self.pin.peek(session))
        self.pin.lock_session(session)
        if ended:                                # a session that was valid: somebody left Maintenance
            self._event(E_LOCK, actor=MAINTENANCE)
        return {'locked': True}

    def set_operator(self, body, actor=None):
        name = body.get('name')
        with self._lock:
            if not isinstance(name, str) or name not in self.store.operators():
                raise ValueError(T_OPERATOR)
            changed, self.operator = name != self.operator, name
        if changed:
            self._event(E_OPERATOR.format(name=name), actor=actor or name)
        return {'operator': name}

    def stop(self, body=None, actor=None):
        """“Stop STOP”: never refused, never asks, works when nothing runs (API.md C.4.1)."""
        try:
            return self.devices.stop_everything(actor)
        except Exception as exc:                 # a fault of this program must not stand between the button and the stop
            report = {'accepted': True, 'schedule_ended': False, 'round_stopping': False, 'manual_cancelled': False,
                      'pump': {'result': 'unconfirmed', 'error': ''}, 'rpi': {'result': 'unconfirmed', 'error': ''}}
            try:
                ended = self.runner.request_stop() or {}
                report.update(schedule_ended=bool(ended.get('schedule')), round_stopping=bool(ended.get('round')))
            except Exception:
                pass
            try:
                report['manual_cancelled'] = bool(self.manual.cancel())
            except Exception:
                pass
            for half, call in (('pump', lambda: self.pump.stop_all('stop')), ('rpi', self.rpi.stop)):
                try:
                    out = call() or {}
                    if half == 'rpi':
                        report[half] = {'result': 'cancel_sent' if out.get('cancel_sent') else 'confirmed', 'error': ''}
                    elif out.get('confirmed'):
                        report[half] = {'result': 'confirmed', 'error': ''}
                    else:
                        report[half]['error'] = str(out.get('error') or '')
                except Exception as fault:
                    report[half]['error'] = str(fault) or type(fault).__name__
            if not self.pump.in_use and not self.pump.motion_uncertain:
                report['pump'] = {'result': 'none', 'error': ''}
            try:
                self._event(E_STOP_BROKEN.format(detail=repr(exc)), 'error', actor)
                if report['rpi']['result'] == 'unconfirmed':
                    self.devices.raise_hv_unconfirmed()
                if report['pump']['result'] == 'unconfirmed':
                    self.alarms.raise_(devices_module.A_PUMP_KEY, devices_module.PRIORITY_URGENT, devices_module.A_PUMP_AREA,
                                       devices_module.T_PUMP_UNCONFIRMED, devices_module.A_PUMP_KIND)
            except Exception:
                pass
            return report

    def pause(self, body=None, actor=None):
        return self.runner.pause()

    def resume(self, body=None, actor=None):
        return self.runner.resume()

    def schedule_preview(self, body, actor=None):
        try:
            return self.runner.preview(body)
        except KeyError:
            raise ApiError(404, 'not_found', T_NO_RECIPE) from None

    def schedule_start(self, body, actor=None):
        return self.runner.start(body)

    def alarms_ack(self, body, actor=None):
        if body.get('all') is True:
            ids = None
        else:
            ids = body.get('ids')
            if not isinstance(ids, list):
                raise ValueError(T_ALARM_IDS)
        return {'acked': self.alarms.ack(ids, actor)}

    def manual_op(self, body, actor=None):
        if not isinstance(body.get('op'), str):  # (ManualOps looks the name up in a table: it must be a text)
            raise ValueError(manual_module.T_UNKNOWN)
        return self.manual.submit(body)

    def calibration_plan(self, body, actor=None):
        return self.analyst.save_plan(body.get('plan'))

    def _plan_recipe(self, raw, key):
        """One of a plan's two Recipes in the shape Analyst.check_plan() wants, or None (not chosen / not there)."""
        recipe_id = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(recipe_id, str) or not recipe_id:
            return None
        try:
            record = self.store.recipe(recipe_id)
        except (KeyError, ValueError):
            return None
        checked, seconds = self._check(record['csv'])
        return {'id': record['id'], 'name': record['name'], 'sha256': record['sha256'], 'validated': checked,
                'estimate_seconds': seconds}

    def calibration_check(self, body, actor=None):
        plan = body.get('plan')
        reply = self.analyst.check_plan(plan, self._plan_recipe(plan, 'recipe_id'), self._plan_recipe(plan, 'prime_recipe_id'))
        reply.pop('steps', None)                 # the round's steps: of no use to the form
        return reply

    def calibration_start(self, body, actor=None):
        return self.runner.start_calibration(body)

    def calibration_confirm(self, body=None, actor=None):
        return self.runner.confirm_prompt()

    def calibration_analyze(self, body, actor=None):
        reply = self._analyst(self.analyst.request_analysis, body.get('id'))
        self._event(E_REANALYZE.format(id=str(body.get('id'))[:16]), actor=actor)
        return reply

    def calibration_exclude(self, body, actor=None):
        record = self._analyst(self.analyst.set_exclusions, body.get('id'), body.get('exclude'))
        self._event(E_EXCLUDE.format(id=str(body.get('id'))[:16]), actor=actor)
        return {'accepted': True, 'exclude_levels': record.get('exclude_levels')}

    # ============================================================================================
    # POST — maintenance (PIN)
    # ============================================================================================
    def set_operators(self, body, actor=None):
        names = self.store.set_operators(body.get('operators'))
        with self._lock:
            if self.operator not in names:       # removing the current operator clears the selection
                self.operator = ''
            current = self.operator
        self._event(E_OPERATORS.format(count=len(names)), actor=actor)
        return {'operators': names, 'current': current}

    def recipe_validate(self, body, actor=None):
        """A file's bytes, a CSV text or the rows of the step editor -> the check result (API.md C.4.3, M4.1).
        200 also when the Recipe is not valid; 400 only when nothing readable came."""
        given = [key for key in ('file_b64', 'csv', 'rows') if key in body]
        if len(given) != 1:
            raise ValueError(T_RECIPE_SOURCE)
        if given[0] == 'file_b64':
            try:
                data = base64.b64decode(body['file_b64'], validate=True)
            except (ValueError, TypeError):
                raise ValueError(recipe.T_UNREADABLE) from None
            text, encoding = recipe.decode(data)
        elif given[0] == 'csv':
            if not isinstance(body['csv'], str):
                raise ValueError(recipe.T_UNREADABLE)
            text, encoding = recipe.normalize(body['csv']), 'utf-8'
        else:
            text, encoding = recipe.compose(body['rows']), 'utf-8'
        checked, seconds = self._check(text, encoding)
        return dict(checked, csv=text, encoding=encoding, estimate_seconds=int(round(seconds)))

    def recipe_publish(self, body, actor=None):
        name, text = body.get('name'), body.get('csv')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError(T_RECIPE_NAME)
        if not isinstance(text, str):
            raise ValueError(recipe.T_UNREADABLE)
        text = recipe.normalize(text)
        checked, _seconds = self._check(text)
        if not checked['valid']:
            raise ValueError(checked['error'] or T_RECIPE_INVALID)
        record = self.store.publish_recipe(name, text, checked, 'import')
        self._event(E_PUBLISHED.format(name=record['name']), actor=actor)
        return {'id': record['id'], 'name': record['name'], 'sha256': record['sha256']}

    def recipe_delete(self, body, actor=None):
        """The PIN is typed again. The file is moved to recipes\\deleted, never erased."""
        try:
            self.pin.verify(body.get('pin'))
        except RuntimeError as exc:
            raise ApiError(409, 'pin', str(exc)) from None
        recipe_id = body.get('id')
        record = next((item for item in self.store.recipes() if item['id'] == recipe_id), None)
        if record is None:
            raise ApiError(404, 'not_found', T_NO_RECIPE)
        if self.runner.uses_recipe(recipe_id):
            raise RuntimeError(T_RECIPE_IN_USE)
        try:
            self.store.delete_recipe(recipe_id)
        except KeyError:
            raise ApiError(404, 'not_found', T_NO_RECIPE) from None
        self._event(E_DELETED.format(name=record.get('name', '')), actor=actor)
        return {'deleted': True}

    def rpi_config(self, body, actor=None):
        if self.demo:
            raise ValueError(T_DEMO_CONFIG)
        with self._tool(L_RPI_CONFIG):
            update = {'rpi_host': body.get('rpi_host'), 'rpi_port': body.get('rpi_port'),
                      # An empty or missing token field means "keep the saved one".
                      'token': body.get('token') or self.store.config['token']}
            if body.get('clear_token') is True:
                update['token'] = ''
            config = self.store.save_config(update)
            self._event(E_CONFIG.format(host=config['rpi_host'], port=config['rpi_port']), actor=actor)
            self.rpi.reconnect()
        return {'saved': True}

    def rpi_find(self, body=None, actor=None):
        """“automatic search RPi”: where on this computer's networks a Plasmic TCP server answers.
        Practice mode looks at its own simulator only: nothing goes out on the real network."""
        config = self.store.config
        if self.demo:
            hello = netcheck.probe('127.0.0.1', config['rpi_port'], timeout=2.0)['hello'] or {}
            found = [{'host': '127.0.0.1', 'port': config['rpi_port'], 'version': str(hello.get('version', ''))[:20],
                      'name': str(hello.get('name', ''))[:64], 'sim': hello.get('sim') is True, 'allowed': True}] if hello else []
            return {'found': found, 'host': config['rpi_host'], 'port': config['rpi_port'], 'addresses': []}
        direct = [config['rpi_host']] if netcheck.IPV4.fullmatch(config['rpi_host']) else []
        return {'found': netcheck.find_servers(config['rpi_port'], deep=True, hosts=direct),
                'host': config['rpi_host'], 'port': config['rpi_port'], 'addresses': netcheck.local_ipv4()}

    def rpi_diagnose(self, body=None, actor=None):
        """Step-by-step connection check for the screen; sends nothing to the equipment."""
        config = self.store.config
        return {'steps': netcheck.diagnose(config['rpi_host'], config['rpi_port'], config['token'],
                                           self.rpi.view()['status'])}

    def rpi_check(self, body=None, actor=None):
        """“Recheck” (AMENDMENTS M18): the RPi reads the supply and the Nano now, and the answer is put in plain
        words. Read-only (the RPi's `status` with `fresh`): nothing is switched, written or started."""
        view = rpilink.check_view(self._rpi('status', fresh=True))
        if view['rows']:
            self._event(E_CHECK.format(rows='; '.join('%s %s' % (row['name'], row['text']) for row in view['rows'])),
                        'info' if view['ok'] else 'warn', actor)
        return view

    def rpi_tool(self, body, actor=None):
        """synchronized to Nano · Clear Nano fault · Capture spectrum (no discharge). Nothing here switches the high voltage on:
        the only way to discharge is Schedule measurement (AMENDMENTS M3)."""
        cmd = body.get('cmd')
        if not isinstance(cmd, str) or cmd not in L_TOOL:
            raise ValueError(T_TOOL)
        with self._tool(L_TOOL[cmd]) as hold:
            if cmd == 'prepare':
                reply = self._rpi('prepare')
                self._event(E_PREPARED, actor=actor)
                return reply
            if cmd == 'clear_fault':
                reply = self._rpi('clear_fault')
                what = f'cleared {reply.get("previous_fault")}' if reply.get('cleared') else 'No fault'
                self._event(E_CLEARED.format(what=what), actor=actor)
                return reply
            # Three frames at the integration time of plasma parameters. The link's own thread stores the result when the job ends.
            parameter = ((self.rpi.latest() or {}).get('snapshot') or {}).get('parameter') or {}
            ms = parameter.get('integration_ms')
            if parameter.get('valid') is not True or type(ms) is not int:
                if not self.rpi.connected:
                    raise ApiError(503, 'rpi_link', T_NO_LINK + str(self.rpi.error))
                raise RuntimeError(T_NO_PARAMETERS)
            reply = self._rpi('spectrum', integration_ms=ms)
            job = reply.get('job')
            self._event(E_CAPTURE, actor=actor)
            if job:
                self.rpi.own_job = job           # the page's own capture, while it runs
                limit = steps.SPECTRUM_FRAMES * ms / 1000.0 + steps.SPECTRUM_EXTRA_S
                hold.keep()                      # the tool keeps the equipment until the job has ended
                threading.Thread(target=self._tool_job, args=(hold, job, limit), name='tool-job', daemon=True).start()
            return reply

    def _tool_job(self, hold, job, limit_s):
        try:
            self.rpi.wait_job(job, None, limit_s=limit_s, lost_s=TOOL_JOB_LOST_SECONDS)
        except Exception:
            pass
        finally:
            if self.rpi.own_job == job:
                self.rpi.own_job = None
            hold.release()

    def set_parameters(self, body, actor=None):
        """Save the seven plasma parameters on the RPi, then load them into the Nano (as 1.3.0)."""
        with self._tool(L_PARAMETERS):
            reply = self._rpi('set_parameters', parameters=body.get('parameters'))
            self._event(E_PARAMETERS.format(values=parameters_text(reply.get('parameters'))), actor=actor)
            self._recipes_bump += 1              # the estimates of the Recipe list follow the parameters
            try:
                self.rpi.command('prepare')
                reply['synced'] = True
                self._event(E_PREPARED, actor=actor)
            except (RuntimeError, PlasmicError) as exc:
                reply.update(synced=False, sync_error=str(exc))
                self._event(E_SYNC_FAILED.format(detail=exc), 'warn', actor)
            return reply

    def pump_config(self, body, actor=None):
        enabled = body.get('enabled')
        with self._tool(L_PUMP_CONFIG):
            reply = self.pump.configure(enabled, body.get('device'))
        self._event(E_PUMP_ON.format(device=str(body.get('device'))[:40]) if enabled else E_PUMP_OFF, actor=actor)
        return reply

    def pump_reconnect(self, body=None, actor=None):
        with self._tool(L_PUMP_RECONNECT):
            reply = self.pump.reconnect()
        self._event(E_PUMP_RECONNECT, actor=actor)
        return reply

    def pump_settings(self, body, actor=None):
        with self._tool(L_PUMP_SETTINGS):
            reply = self.pump.apply_settings(body.get('syringe_ul'), body.get('syringe_speed_ml_s'))
        self._recipes_bump += 1                  # a Recipe may fit the new syringe or not (rule R11)
        saved = reply.get('settings') or {}
        self._event(E_PUMP_SETTINGS.format(size=saved.get('syringe_ul', 0) / 1000.0, speed=saved.get('syringe_speed_ml_s', 0)),
                    actor=actor)
        return reply

    def scale_tare(self, body=None, actor=None):
        with self._tool(L_SCALE):
            reply = self.pump.scale_tare()
        self._event(E_TARE, actor=actor)
        return reply

    def scale_calibrate(self, body, actor=None):
        grams = body.get('grams')
        if not _finite(grams) or not SCALE_GRAMS_MIN <= grams <= SCALE_GRAMS_MAX:
            raise ValueError(T_SCALE_GRAMS)
        with self._tool(L_SCALE):
            reply = self.pump.scale_calibrate(grams)
        self._event(E_SCALE.format(grams=grams), actor=actor)
        return reply

    def pump_calibration(self):
        """GET /api/pump/calibration: one row per pump (its steps per mL now, and what was measured last)."""
        return {'pumps': self.pump.pump_calibration(), 'nominal_ml': PUMP_CAL_ML}

    def pump_calibrate(self, body, actor=None):
        """The person dosed `nominal_ml` with this pump and says how much came out."""
        motor, measured = body.get('motor'), body.get('measured_ml')
        nominal = body.get('nominal_ml', PUMP_CAL_ML)
        if type(motor) is not int or not _finite(measured) or not _finite(nominal):
            raise ValueError(T_PUMP_CAL)
        with self._tool(L_PUMP_CAL):
            reply = self.pump.calibrate_pump(motor, measured, nominal)
        self._recipes_bump += 1                  # the time a dose takes follows the factor
        return reply

    def pump_calibrate_reset(self, body, actor=None):
        motor = body.get('motor')
        if type(motor) is not int:
            raise ValueError(T_PUMP_CAL)
        with self._tool(L_PUMP_CAL):
            reply = self.pump.reset_pump_calibration(motor)
        self._recipes_bump += 1
        return reply

    def pump_setup(self):
        """GET /api/pump/setup: what each pump carries, which two the cleaning uses, which pump each valve opens with."""
        return self.pump.pump_setup()

    def pump_setup_save(self, body, actor=None):
        """Maintenance › Pump and Scale › What is the pump connected to?. The flow of the schedule page names its drain pump by what the setup says, so it
        is made again; published Recipes name pumps by number and are not touched: the reply lists the ones that
        use a pump whose line was changed."""
        wanted = self.pump.check_pump_setup(body)            # ValueError: what cannot be, before anything is touched
        before = {row['motor']: (row['use'], row['name']) for row in self.pump.pump_setup()['pumps']}
        changed = sorted(motor for motor, row in wanted['pumps'].items() if before.get(motor) != (row['use'], row['name']))
        steps = self._flow_steps()               # read with the hardware of before (the four stages of M16 need it)
        with self._tool(L_PUMP_SETUP):
            if self.runner.uses_recipe(store_module.FLOW_RECIPE_ID):
                raise RuntimeError(T_FLOW_IN_USE)
            reply = self.pump.save_pump_setup(body)
            self._event(E_PUMP_SETUP.format(summary=reply.get('summary', '')), actor=actor)
            reply['flow'] = self._flow_again(steps, actor)
        self._recipes_bump += 1                  # names, the drain pump and the valve times of every Recipe follow
        reply['recipes_to_check'] = self._recipes_using(changed)
        return reply

    def _flow_again(self, steps, actor):
        """The Recipe of the schedule page's step list, checked again with the hardware of this moment (its steps
        name their pumps by number, as every Recipe does). '' when there is none; 'kept' when it still makes a
        round; 'open' when it does not any more — the page then says what to change."""
        record = next((item for item in self.store.recipes() if item['id'] == store_module.FLOW_RECIPE_ID), None)
        if record is None:
            return ''
        text, steps = recipe.flow_steps_csv(steps)
        checked, _seconds = self._check(text)
        self.store.put_recipe(store_module.FLOW_RECIPE_ID, recipe.FLOW_STEPS_NAME.format(count=len(steps)), text,
                              checked, 'flow')
        self.store.save_flow({'steps': steps})
        if checked['valid']:
            self._event(E_SETUP_FLOW.format(steps=self._flow_event(checked)), actor=actor)
            return 'kept'
        self._event(E_SETUP_FLOW_OPEN.format(detail=recipe.flow_words(checked['error'])), 'warn', actor)
        return 'open'

    def _recipes_using(self, motors):
        """The names of the Recipes that use one of these pumps, or do not pass their check (with the new layout, or
        from before); the step list of the schedule page is one of them."""
        names = []
        for record in self.store.recipes():
            if not motors:
                continue
            try:
                checked = self._recipe_entry(record)[1]
            except Exception:
                checked = None
            if checked and (not checked.get('valid') or set(checked.get('uses_motors') or []) & set(motors)):
                flow = record['id'] == store_module.FLOW_RECIPE_ID
                names.append(T_FLOW_RECIPE if flow else record.get('name') or record['id'][:8])
        return names

    def calibration_apply(self, body, actor=None):
        with self._tool(L_COEFFICIENTS):
            reply = self._analyst(self.analyst.apply, body.get('id'))
        self._event(E_APPLIED.format(id=str(body.get('id'))[:16], metals=', '.join(reply.get('applied') or [])), actor=actor)
        return reply

    def set_coefficients(self, body, actor=None):
        with self._tool(L_COEFFICIENTS):
            config = self.analyst.set_coefficients(body.get('config'))
        self._event(E_COEFFICIENTS, actor=actor)
        return {'config': config}

    def set_blank_qc(self, body, actor=None):
        checked = self.analyst.set_qc(body.get('qc'))
        self._event(E_BLANK_QC, actor=actor)
        return {'qc': checked}

    def set_blank_zero(self, body, actor=None):
        settings = body.get('settings')
        if not isinstance(settings, dict):
            raise ValueError(T_BLANK_ZERO)
        reply = self.analyst.set_blank_zero(settings)
        self._event(E_BLANK_ZERO, actor=actor)
        return reply

    # ============================================================================================
    # The end of the program (SPEC B.11)
    # ============================================================================================
    def _hv_needs_stop(self):
        """Only when a job of this program runs or the high voltage is not known to be off. A program that
        never reached the RPi has started nothing there."""
        rpi = self.rpi
        if rpi is None or not rpi.was_connected:
            return False
        job = (rpi.latest() or {}).get('job') or {}
        if job.get('state') == 'running':
            return True
        view = rpi.hv_view()
        return not (view['known'] and view['idle'])

    def _close_rpi_stop(self):
        try:
            self.rpi.stop()
            self._event(E_CLOSE_STOP, 'warn')
        except Exception as exc:
            try:
                self._event(E_CLOSE_STOP_FAILED.format(detail=exc), 'error')
            except Exception:
                pass

    def _close_pump_stop(self):
        try:
            self.pump.stop_all('exit')           # X now; the link stays open until the round has wound down
        except Exception:
            pass

    def close(self, fast=False):
        """Everything off, then the links (SPEC B.11).

        The pump board's stop (X) and — only when needed — the RPi's go out at once, side by side. The pump
        board's link is closed after the schedule's thread had its time: a round that is being stopped
        switches both sides off itself and writes down what it saw; with the link gone under it, it would
        write "stop not confirmed" about pumps that were stopped a moment before.
        `fast`: the console window is being closed and Windows gives about five seconds: the pump board's
        stop within 1.5 s, the RPi's within 3 s, and the round gets a moment instead of its five seconds."""
        with self._close_lock:                   # a second caller waits until the first is done
            if self._closed:
                return
            self._closed = True
            self._close(fast)

    def _close(self, fast):
        began = time.monotonic()
        self._awake_stop.set()
        # The watch of Devices ends first: nothing is judged while the rest is taken down. (It has two seconds
        # for it; when the window is being closed nobody waits for that.)
        ender = getattr(self.devices, 'close', None)
        if ender is not None:
            try:
                if fast:
                    threading.Thread(target=ender, name='close-watch', daemon=True).start()
                else:
                    ender()
            except Exception:
                pass
        for call in (getattr(self.runner, 'request_stop', None), getattr(self.manual, 'cancel', None)):
            try:
                if call is not None:
                    call()                       # instant: no further slot, the stop flags
            except Exception:
                pass
        halves = []
        if self.pump is not None:
            halves.append(threading.Thread(target=self._close_pump_stop, name='close-pump', daemon=True))
        try:
            wanted = self._hv_needs_stop()
        except Exception:
            wanted = self.rpi is not None
        if wanted:
            halves.append(threading.Thread(target=self._close_rpi_stop, name='close-rpi', daemon=True))
        for thread in halves:
            thread.start()
        limit = began + (3.0 if fast else 10.0)
        for thread in halves:
            thread.join(max(0.0, limit - time.monotonic()))
        if self.runner is not None:
            try:
                if fast:                         # a moment for the round, as far as the five seconds allow
                    worker = self.runner.thread
                    if worker is not None and worker is not threading.current_thread():
                        worker.join(max(0.0, min(0.7, began + 3.5 - time.monotonic())))
                else:
                    self.runner.close()          # gives the schedule's thread 5 s to leave everything off
            except Exception:
                pass
        if self.pump is not None:
            try:                                 # X once more, then the link is closed and the guard is told
                self.pump.close(budget_s=max(0.3, min(1.5, began + 4.7 - time.monotonic())) if fast else 2)
            except Exception:
                pass
        if self.rpi is not None:
            self.rpi.close()
        if self.simulator is not None:
            self.simulator.terminate()
            try:
                self.simulator.wait(0.5 if fast else 5)
            except subprocess.TimeoutExpired:
                self.simulator.kill()
        if self.simulator_log is not None:
            self.simulator_log.close()
        thread = self._awake_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)
