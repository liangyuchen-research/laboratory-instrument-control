#!/usr/bin/env python3
"""Plasmic electronic control - pump control panel (Arduino Mega 2560) . The name and water of

independent laboratory v7 is left intact The background thread of pc/vendor/v7 (is copied bit by bit and cannot be changed). This file is included in the entire program
The only place where it is loaded and the serial port is encountered;V7 Everything that is not available is added here:

  * Port is not required: the only one on this computer Arduino Mega will be found by itself (compare USB of VID:PID,When looking for
    does not open anything) and remember its serial number; when more than one piece is connected, go to ”Maintenance › Connection” selection;
  * After opening, first confirm that the other party is really the pump control board. Before confirming, only send `?`; 
  * Every time you connect, reconnect, or the board restarts itself, it will be sent first. X (Stop all) before doing anything else;
  * Every command confirms that ”the reply has really arrived”; use it if the reply is not correct. W,CFG realignment;
  * Dosing, cleaning, and syringe pump actions will wait until they are actually completed;
  * “continuous operation” always has a time limit, and it is left to the control board to count (quantitative step instructions of the firmware). P) : It’s time board
    Stop by yourself, computer crashes, falls asleep,USB will stop even if it is dropped. Weighing Quantification: What is on the board mega/ firmware (STATE have fw=p…) hour
    Also leave it to the board to read the scale yourself (command G) ; yes V7 is only used with the original firmware. V7 ’s method (the computer looks at the scale and has a guard program); Allowlist for
  * When the high pressure is not turned off, any command that would cause the pump to move cannot be sent;
  * What is connected to each pump (water,OUT, The name of the chemical (not connected), which two are used for cleaning, which pump is connected to which solenoid valve, are all settings of this program
     (pumpboard.json of pump_setup; is not set:V7 The unit of/OUT,solenoid valves are not used). On the board is mega/ firmware p2
    above,Use it every time you connect CM, VL tells the board two commands; when the board does not recognize these two commands, everything will follow the original version of the board firmware (V7 of) ; 
  * Delivered before the end of the program X; If the connection is disconnected, it will reconnect itself.

English notes for the other modules of 2.0.0 (the interface is frozen in SPEC B.13):

    PumpBoard(data_dir, mode, store, on_event, on_alarm, ui_age,
              overrides=None, link_factory=None, port_lister=None, sim_firmware=None)

    mode       'real' (the Mega on USB) · 'sim' (V7's own simulator) · 'off' (the board does not exist).
    store      object with pump_settings() -> dict and save_pump_settings(dict)   (pumpboard.json, SPEC B.3.2).
    on_event   callable(text, level) or None. One line for the event log ('info' / 'warn' / 'error').
               None: store.event(text, level, 'system') is used when the store has it.
    on_alarm   callable(action, key, priority=None, area=None, text=None, kind=None) or None.
               action is 'raise' or 'clear'; priority 1 = Emergency, 2 = high, 3 = middle; kind as in SPEC B.9.
    ui_age     callable() -> seconds since the page last asked for /api/state (None = unknown), or None.
    overrides, link_factory, port_lister
               test seams. Production passes none of them.
    sim_firmware
               which firmware the simulator of mode 'sim' speaks: 'plasmic' (mega/, the default: what the rig
               runs) or 'stock' (V7's own). None = SIM_FIRMWARE.

Every operation raises PumpBoardError(code, text). Codes: busy · refused · blocked · link · timeout ·
device · stopped. configure(), reconnect() and apply_settings() raise ValueError for a wrong request
(the HTTP layer turns that into 400).

    run(motor, flow_ml_min, direction, limit_s, owner)      a timed run (amendment M8): the board ends it
        by itself after limit_s. view() shows it as 'run' with left_s. When its time runs out:
        owner 'manual' -> event “motor N The set time has arrived and it has automatically stopped” and last_result; owner 'round' ->
        deadline_tripped('round') gives “motor N The planned operation time has been exceeded and it has automatically stopped.” once, and a stop_motor()
        that comes after the run was already over raises that sentence (code 'device'). A stop that
        arrives while run() is still starting the motor makes run() raise ('stopped', “has been stopped”).
    hardware()  bootstrap.hardware of API.md C.3.1, plus pulses_per_ml, dose_pulses_per_ml and
                syringe.full_stroke_steps (amendment M10: the Recipe module's dose estimate reads them).
    facts()     the `board` block of a round record (SPEC D.2), with pulses_per_ml and dose_pulses_per_ml.
    quiescent() -> (ok, [reason]); a reason is {code, text, fix} as in API.md.

Nothing in this file may ever be run against the real board by a test: tests use mode 'sim' or a
scripted link, set PLASMIC_NO_SERIAL=1 and install the tripwire of tests/pump_testing.py.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import types
import uuid
from datetime import datetime
from pathlib import Path

# Nothing may appear inside pc/vendor: its files are checked against a list of hashes.
sys.dont_write_bytecode = True

PC = Path(__file__).resolve().parent
VENDOR = PC / 'vendor'
GUARD_SCRIPT = PC / 'pump_guard.py'

# Fixed decision 3: only these USB ids are ever opened. A constant, not a setting.
MEGA_USB_IDS = frozenset({('2341', '0042'), ('2A03', '0042')})
NOT_CHOSEN = 'PLASMIC-NOT-CHOSEN'
SIM_DEVICE = 'simulator'
OWNERS = ('round', 'manual')
DEFAULT_SETTINGS = {'version': 1, 'enabled': None, 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4}
STATE_FIELDS = ('a', 'sa', 'b', 'sb', 'valves', 'w_stream_hz', 'cleaning')
READY_BANNER = 'READY,arduino+pump'
# The firmware of mega/ (this project's build of V7's firmware) names itself in every STATE line: fw=p<n>.
# With it the board runs a weight dose by itself (G) and stops an endless run when this PC falls silent (K).
PLASMIC_FW = re.compile(r'p\d+\Z')
WATCHDOG_MS = 10000              # K: how long the board waits for a line of this PC before it ends an endless run
SETUP_FW = 2                     # CM and VL (the pumps of the cleaning, the valve of each pump) exist from p2 on
# What a pump carries (Maintenance › Pump and Scale › What is the pump connected to?). water and OUT are the two pumps of the cleaning: one of each, always.
USE_WATER, USE_OUT, USE_CHEMICAL, USE_NONE = 'water', 'out', 'chemical', 'none'
USES = (USE_WATER, USE_OUT, USE_CHEMICAL, USE_NONE)
NAME_WATER, NAME_OUT = 'water', 'OUT'
SETUP_NAME_MAX = 16
SETUP_NAME_BAD = frozenset(',;"\'\\<>')
SIM_FIRMWARE = 'plasmic'         # the simulator of mode 'sim': 'plasmic' (pc/megasim.py) or 'stock' (V7's own)
KV = re.compile(r'([A-Za-z_]+)=([^,]*)')

# Limits and times. Module attributes on purpose: they are read when used, so tests can shorten them.
UI_WATCHDOG_SECONDS = 10.0       # page silent this long during a manual pump operation -> everything stops
RUN_LIMIT_MIN_S = 1.0
RUN_LIMIT_MAX_S = 306.0          # 300 s cap + the round's 5 s margin + 1 s valve time
RUN_SECOND_LINE_S = 2.0          # a timed run that the board has not ended this long after its time: the host stops it
RUN_SLOW_SHARE = 0.15            # ... plus this share of its length: on the real board a finite move takes longer
                                 # than steps / speed (stepping pauses while it talks to the scale; firmware notes
                                 # 2.3-2), and the board is to finish its own count of steps, not the host's clock
TIMEOUT_S = {'state': 1.5, 'motor': 3.0, 'select': 3.0, 'init': 5.0, 'syringe': 3.0, 'cfg': 3.0, 'scale': 3.0}
REAL_LINK = {'simulate': False, 'boot_delay_s': 2.5, 'command_timeout_s': 3.0, 'long_command_timeout_s': 6.0}
IDENTIFY_TRIES = 3
IDENTIFY_TIMEOUT_S = 1.5
RETRY_S = (2.0, 5.0, 10.0, 30.0)  # after an open or link error; the last value repeats
RELIST_S = 3.0                    # how often the port list is read while the board is missing
STARTUP_BUSY_GRACE_S = 15.0       # a guard of a killed earlier program may still hold the port
MONITOR_S = 0.1
HEARTBEAT_S = 2.0
STOP_IDLE_S = 2.0
VALVE_CLOSE_S = 3.0
STATE_POLL_S = 1.0                # `?` during a long wait: every `?` pauses stepping for about 10 ms
STOP_POLL_S = 0.3
DOSE_LIMIT_FACTOR = 1.5
DOSE_MANY_LIMIT_FACTOR = 2.5     # two finite moves at once: the board computes both, each may turn slower
CAL_NOMINAL_ML = (1.0, 100.0)    # the volume a calibration doses
CAL_RATIO = (0.5, 2.0)           # measured / nominal outside this is taken for a slip of the finger
DOSE_LIMIT_EXTRA_S = 10.0
EARLY_FACTOR = 0.8
WEIGHT_HARD_LIMIT_S = 420.0
BOARD_DOSE_POLL_S = 0.5          # G,? while the board runs a weight dose by itself
LINE_MAX = 62                    # the Mega's serial buffer holds 63 bytes, the line feed is one of them
BOARD_DOSE_SAMPLES_MAX = 9       # the firmware's buffer for the baseline and the final weight
BOARD_DOSE_EXTRA_S = 30.0        # the host's second line behind the board's own time limit (settling, top-ups)
CLEAN_EXTRA_S = 20.0
SYRINGE_INIT_LIMIT_S = 20.0
SYRINGE_POLL_S = 0.3
SYRINGE_VALVE_LIMIT_S = 5.0
SYRINGE_MOVE_FACTOR = 1.5
SYRINGE_MOVE_EXTRA_S = 5.0
WEIGHT_FRESH_S = 1.0
SCALE_READ_LIMIT_S = 5.0
SCALE_SILENT_S = 3.0
SCALE_ALARM_S = 10.0
SCALE_RESTART_AFTER_STOP_S = 1.0
RECONNECT_BUDGET_S = 8.0
RESYNC_TRIES = 2
RESYNC_SETTLE_S = 1.0
X_RESYNC_WAIT_S = 10.0
LOG_MAX_BYTES = 1_000_000
LOG_KEEP = 3

# Texts of V7's transport that tell a missing reply from a dead link (a test checks they are still there).
V7_TIMEOUT = 'command timeout'
V7_WRITE_FAILED = 'failed to send'
V7_NOT_CONNECTED = 'Not connected yet Arduino'

T_BUSY = 'The pump control board is performing another action'
T_HV = 'The high pressure has not been turned off and the pump cannot be started.'
T_NO_GATE = 'The high-voltage interlock has not been connected and the pump cannot be started.'
T_NOT_ALLOWED = 'This command is not in the allowed list and has not been sent:{command}'
T_NO_REPLY = 'Pump control board does not respond ({command}) '
T_NOT_CONNECTED = 'Pump control board is not connected ({text}) '
T_UNCERTAIN = 'The status of the pump control board is unknown. Please go to the site to confirm first.'
T_MOVING = 'The pump is still running'
T_CLEANING = 'Automatic cleaning in progress'
T_SYRINGE_MOVING = 'The syringe pump is still moving'
T_LOST = 'The pump control board is lost and the stop command cannot be sent. The pump may still be spinning.'
T_RESET = 'The pump control board restarted on its own'
T_STOPPED = 'Stopped'
T_RUN_NOT_STARTED = '{label} did not start according to the instructions.'
T_STOP_LATE = '{label} Did not stop according to the instruction, all have stopped'
T_RUN_LIMIT = 'The continuous operation time must be within 1–306 seconds'
T_GROUP_BUSY = '{group}can only be transferred one at a time ({label} is still transferring)'
T_ALREADY_RUNNING = '{label} is already transferring'
T_CLEANING_NO = 'This action cannot be performed during automatic cleaning.'
T_CLEAN_NO_SINGLE_STOP = 'A single pump cannot be stopped during automatic cleaning. To abort, click ”Stop” STOP”.'
T_NO_MOTOR = 'does not have this pump ({motor}) '
T_DOSE_EARLY = 'Quantification ended earlier than expected and may have been interrupted.'
T_DOSE_LATE = 'The quantitative exceeding time has not ended yet and all have been stopped.'
T_DOSE_NONE = 'No pump to be quantitatively selected'
T_DOSE_TWICE = '{label} selected twice'
T_DOSE_LIST = 'The simultaneous quantitative content format is incorrect.'
# Calibration of one pump (Maintenance › Pump and Scale): 10 mL are dosed, the person says what really came out.
T_CAL_NOMINAL = 'The volume used for calibration should be within {low}–{high} mL between'
T_CAL_MEASURED = 'The measured volume sum {nominal} mL Too much difference (should be in {low}–{high} mL ). Please confirm before entering'
E_CAL_SAVED = 'Corrected {label}: beat {nominal} mL,Actual {measured} mL; Every mL number of steps {before} → {after} ({change}) '
E_CAL_RESET = '{label} corrections cleared,Return to original settings (Every mL {after} step)'
T_NOT_CALIBRATED = 'The scale has not been calibrated yet: go to ”Maintenance › Pump and Scale” do a calibration'
T_WEIGHT_OFF = 'configuration file does not enable weighing and quantification'
T_WEIGHT_TARGET = 'The target weight must be between 0 and 1000 g between'      # V7's own sentence for a target that is out of range
T_WEIGHT_LATE = 'The weighing quantity exceeds {seconds:g} seconds, all stopped'
T_CLEAN_NOT_STARTED = 'Automatic cleaning does not start'
T_CLEAN_LATE = 'The automatic cleaning has exceeded the time and has not ended yet. All have been stopped.'
T_SYRINGE_NOT_INIT = 'The syringe pump has not been initialized yet.'
T_SYRINGE_INIT_LATE = 'Syringe pump initialization exceeded {seconds:g} seconds not completed'
T_SYRINGE_VALVE_LATE = 'The valve of the syringe pump exceeds {seconds:g} seconds did not go to position'
T_SYRINGE_MOVE_LATE = 'The action of the syringe pump has exceeded the time and has not yet ended, and has all stopped.'
T_SYRINGE_FULL = 'The syringe cannot be installed (existing {have} mL,capacity {size} mL) '
T_SYRINGE_SHORT = 'There are only {have} mL,Can’t push it out {want} mL'
T_SYRINGE_SPEED = 'Syringe pump speed exceeds the upper limit {top:g} mL/s'
T_SYRINGE_POSITION = 'The valve position should be at 1–{top} between'
T_SYRINGE_VOLUME = 'The volume must be at least 0.001 mL'
T_SCALE_NONE = 'scale has no reading value'
T_SCALE_MOVING = 'There is a pump that is still running'
T_SCALE_UNSTABLE = 'The reading value is still changing, wait for it to stabilize.'
T_SCALE_GRAMS = 'The weight of the weight should be 0.1–20000 g between'
T_SIZE = 'syringe capacity only supports 5 mL or 1.25 mL'
T_SPEED_RANGE = 'The syringe pump speed should be at 0.001–{top:g} mL/s between'
T_SIM_CONFIG = 'Simulation mode uses the built-in simulation control board'
T_NOT_MEGA = '{device} Not now Arduino Mega (or removed). Didn’t turn it on.'
T_NOT_SET = 'Pump control board not found'
T_VENDOR = 'Program folder is incomplete: missing pc\\vendor\\{what}.Please copy the entire folder again.'
T_FAILED = 'Operation failed: {text}'
T_SETUP_SHAPE = 'The setting content is incomplete: every pump and every solenoid valve must have'
T_SETUP_ONE_WATER = 'requires a pump to receive ”water”, and there can only be one (for cleaning)'
T_SETUP_ONE_OUT = 'requires a pump to connectOUT”,And there can only be one (for emptying)'
T_SETUP_HALVES = '“water” and ”OUT”Want one in {a}, One in {b}: These two should be rotated together during cleaning. Only one on the same side can be rotated at a time.'
T_SETUP_NAME = '{pump} The name of 1–{most} characters, no commas, semicolons, quotation marks, backslashes,< or >'
T_SETUP_RESERVED = '{pump}: “water” and ”OUT”Please select directly, do not write it in the name'
T_SETUP_VALVE = 'solenoid valve {valve} To select a pump, or select ”Do not use”'
T_SETUP_VALVE_TWICE = '{pump} cannot link two solenoid valves at the same time'
T_SETUP_FIRMWARE = 'The firmware of this pump control board cannot change these settings (requires Plasmic p2 above) : water, OUT and the solenoid valve are linked according to the original board. To change, please update the firmware first (mega folder Flash-Mega.cmd) '
T_SETUP_REFUSED = 'The pump control board did not accept the pump settings ({command} → {reply}) '
T_SETUP_NO_VALVE = 'solenoid valve: not used'
E_SETUP_BAD = 'The content of ”What is connected to the pump” in the configuration file cannot be read, so it is not used: use the default instead (the solenoid valve is not used). Please go to ”Maintenance › Pump and Scale” reset'
T_OP_STOPPED = 'has stopped:{text}'

LINK_TEXT = {
    'unset': 'Pump control board not found: please connect USB Wire',
    'unset_many': 'This computer has more than one connection Arduino Mega: Please go to ”Maintenance › Connect”Choose a piece',
    'disabled': 'Not used',
    'connecting': 'Connecting...',
    'sim': 'simulation',
    'connected': 'Connected {device}',
    'missing': 'Pump control board not found: Check USB Wire',
    'ambiguous': 'Found more than one identical piece Arduino Mega.Please go to ”Maintenance › Connection” reselect.',
    'port_busy': '{device} is occupied by another program. please close Arduino IDE , or the old The instructions of V7 Windows (run.bat) .',
    'open_failed': 'cannot be opened {device}: {detail}',
    'wrong_device': '{device} There is a device, but not the pump control board (correct response not received). This port has been closed.',
    'lost': 'The connection was interrupted and is being reconnected.',
    'forbidden': 'This execution does not allow opening of the real port.',
    'not_stopped': 'The pump control board did not confirm that everything has stopped and the connection has been closed.',
    'bad_start': 'The pump control board did not respond correctly:{detail}',
    'sim_failed': 'Analog control board failed to start:{detail}',
    'list_failed': 'Unable to list ports:{detail}',
}
SIM_NAME = 'simulation'
SIM_DESCRIPTION = 'built-in analog control board'
E_CONNECTED = 'pump control board is wired ({device}) '
E_PORT_MOVED = 'The connection port of the pump control board is from {old} becomes {new}'
E_ADOPTED = 'Locate the pump control board:{device} ({description}) ,Automatically set'
E_REPLACED = 'The original pump control board is no longer there; there is only one other on this computer Arduino Mega ({device}) ,has been used instead'
E_DEADLINE_MANUAL = '{label} has automatically stopped when the set time is reached.'
E_DEADLINE_ROUND = '{label} The planned operation time has been exceeded and has automatically stopped.'
E_MONITOR = 'The monitoring program of the pump control panel has an error:{detail}'

# (key, priority, area, kind, text). Priority 1 = Emergency, 2 = high, 3 = middle (SPEC B.9).
A_STOP = ('pump_stop_unconfirmed', 1, 'pump', 'latched', 'The pump stop command was not confirmed. Immediately turn off power to the pump using the physical switch. The settings saved in')
A_LOST_MOVING = ('pump_stop_unconfirmed', 1, 'pump', 'latched', 'Pump control board lost connection. The pump may still be spinning. Immediately turn off power to the pump using the physical switch.')
A_LINK_LOST = ('pump_link_lost', 3, 'pump', 'condition', 'The pump control board connection is interrupted and the program is reconnecting.')
A_RESET = ('pump_reset', 2, 'pump', 'event', 'The pump control board restarted itself. It could be the power supply or USB is unstable. The syringe pump needs to be reinitialized.')
A_WRONG_DEVICE = ('pump_wrong_device', 2, 'pump', 'condition', '')
A_FIRMWARE = ('firmware_mismatch', 3, 'pump', 'event', 'The firmware settings of the pump control board are different from those on the computer (the firmware may be an old version). The cleaning sequence is subject to firmware.')
A_SETUP = ('pump_setup_ignored', 3, 'pump', 'condition',
           'The firmware of the pump control board is old, and the settings of ”what to connect to the pump” are not applied. Now here is the original version of the board:{now}.Please update the firmware (mega folder Flash-Mega.cmd) .')
A_SCALE = ('scale_offline', 3, 'scales', 'condition', 'Scale does not respond')
A_PAGE = ('page_lost', 3, 'picture', 'event', 'The operation screen exceeds {seconds:g} No response for seconds, manually started pump stopped')
A_SYRINGE = ('syringe_fault', 2, 'syringe pump', 'latched', 'Syringe pump report error {number}')

# A weight dose that the board runs by itself (firmware of mega/): the sentences are V7's own.
T_DOSE_ACTIVE = 'Weighing and quantification are in progress. This action cannot be performed.'
T_DOSE_PROFILE = 'The pump control board has just been restarted, and the weighing and quantification has not started; please press again.'
T_WEIGHT_TIMEOUT = 'The weighing quantity has exceeded {seconds:g} The target has not been reached in seconds and the peristaltic pump has been stopped.'
T_WEIGHT_NO_GAIN = 'weight has been continuous {seconds:g} seconds do not continue to rise; please check the pipeline, direction and weighing position'
T_WEIGHT_NO_READING = 'The weight module did not provide a new valid reading and the peristaltic pump has been stopped.'
T_WEIGHT_CANCELLED = 'Weighing and rationing has been stopped by the user'
T_WEIGHT_UNKNOWN = 'Pump control board ended weighing and quantification, but did not explain the result ({result}) '
W_WEIGHT_OUTSIDE = 'The final weight exceeds the allowable range; please reduce the finishing flow rate, confirm the vibration isolation of the scale platform and recalibrate'

SYRINGE_ERRORS = {2: 'value is out of range', 3: 'value is out of range', 7: 'The syringe pump has not been initialized yet.', 9: 'Piston overload', 10: 'Valve overload'}
T_SYRINGE_OTHER = 'Syringe pump report error {number}'
T_SYRINGE_SILENT = 'Syringe pump does not respond'
DEVICE_ERRORS = (
    ('ERR,cleaning_active', T_CLEANING_NO),
    ('ERR,arg', 'The pump control board does not accept this value.'),
    ('ERR,pump_timeout', T_SYRINGE_SILENT),
    ('ERR,pump_bad_reply', T_SYRINGE_SILENT),
    ('ERR,pump_echo_only', T_SYRINGE_SILENT),
    ('ERR,pump_not_found', T_SYRINGE_SILENT),
    ('ERR,scale_timeout', 'Scale does not respond'),
    ('ERR,no_change', 'The weight has not changed and cannot be corrected.'),
    ('ERR,bad_cmd', 'The pump control board does not recognize this command (different firmware versions?)'),
    ('ERR,dose_active', T_DOSE_ACTIVE),
    ('ERR,busy', T_MOVING),
    ('ERR,scale_uncalibrated', T_NOT_CALIBRATED),
    ('ERR,dose_profile', T_DOSE_PROFILE),
)
T_DEVICE_OTHER = 'Pump control board reports error:{line}'

# What V7's functions may put on the wire (SPEC B.3.5). W,SCAN / W,SNIFF / W,SET / W,BAUD and the rest
# are refused before they are written: they block the firmware for up to a minute, and X is not read
# meanwhile.
ALLOWED_Y = frozenset({'DETECT', 'INIT', 'SYR', 'VALVE', 'SPEED', 'ASPUL', 'DISPUL', 'STOP', 'ST'})
MOTION_Y = frozenset({'INIT', 'VALVE', 'ASPUL', 'DISPUL'})
ALLOWED_W = frozenset({'DIV', 'OFFSET', 'AVG', 'STABLE', 'STREAM', 'TARE', 'CAL', 'CFG'})
Y_TERMS = ('PUMP,', 'FOUND,', 'OK,', 'ERR')

_real_ports_allowed = False
_comports = None                 # the system's port lister; tests put a simulated here (pump_testing.install_tripwire)
_v7 = None
_v7_lock = threading.RLock()


def allow_real_ports():
    """Only app.main() calls this. Without it no link to a real port is ever built."""
    global _real_ports_allowed
    _real_ports_allowed = True


class RealPortForbidden(RuntimeError):
    """A real serial port would have been opened where that is not allowed."""


class VendorIncomplete(RuntimeError):
    """A file of pc/vendor is missing or cannot be loaded."""

    def __init__(self, what):
        super().__init__(T_VENDOR.format(what=what))
        self.what = what


class PumpBoardError(RuntimeError):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code
        self.text = text


class _Forbidden:
    """Takes the place of the pyserial module inside V7's transport: opening anything raises."""

    def __init__(self):
        self.attempts = []

    def Serial(self, *args, **kwargs):
        self.attempts.append(args)
        raise RealPortForbidden('this process may not open a real serial port')


def no_serial():
    """The kill switch: with PLASMIC_NO_SERIAL=1 no real port is opened, whatever the mode says."""
    return os.environ.get('PLASMIC_NO_SERIAL') == '1'


def now_local():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _number(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def _g(value):
    try:
        return '%g' % float(value)
    except (TypeError, ValueError):
        return str(value)


def _ml(microlitres):
    return '%g' % (round(float(microlitres)) / 1000.0)


def _hex4(value):
    if value is None or value == '':
        return ''
    if isinstance(value, int):
        return '%04X' % value
    return str(value).strip().upper()


def port_row(item):
    """One row of the port list from a pyserial ListPortInfo (or from a dict with the same names)."""
    get = item.get if isinstance(item, dict) else lambda name: getattr(item, name, None)
    vid, pid = _hex4(get('vid')), _hex4(get('pid'))
    return {'device': str(get('device') or ''), 'description': str(get('description') or ''), 'vid': vid, 'pid': pid,
            'serial_number': str(get('serial_number') or ''), 'is_mega': (vid, pid) in MEGA_USB_IDS}


def system_ports():
    """Every serial port of this PC. Listing opens nothing."""
    if _comports is not None:
        found = _comports()
    elif no_serial():
        found = []
    else:
        load_v7()                                    # puts pc/vendor/site on sys.path
        from serial.tools import list_ports
        found = list_ports.comports()
    return [port_row(item) for item in found]


def load_v7():
    """Import the vendored V7 code, once per process. Importing opens nothing."""
    global _v7
    with _v7_lock:
        if _v7 is not None:
            return _v7
        for name in ('v7/backend/__init__.py', 'v7/backend/config.py', 'v7/backend/registry.py', 'v7/backend/transport.py',
                     'v7/backend/simulator.py', 'v7/backend/functions/__init__.py', 'v7/backend/functions/motors.py',
                     'v7/backend/functions/pump.py', 'v7/backend/functions/scale.py', 'v7/config/hardware.yaml',
                     'site/yaml/__init__.py'):
            if not (VENDOR / name).is_file():
                raise VendorIncomplete(name.replace('/', '\\'))
        yaml_file = VENDOR / 'v7' / 'config' / 'hardware.yaml'
        os.environ['LAB_CONFIG'] = str(yaml_file)            # V7's config module reads this when it is imported
        wanted = [str(VENDOR / 'site'), str(VENDOR / 'v7')]
        for folder in wanted:
            while folder in sys.path:
                sys.path.remove(folder)
        sys.path[:0] = wanted
        try:
            from backend import config, registry, transport
            from backend import functions  # noqa: F401  (registers the 26 functions; a second copy would raise)
            from backend.functions import motors, pump, scale
            cached = config.get_config()
        except Exception as exc:
            raise VendorIncomplete('v7 (%s) ' % exc) from exc
        if (Path(transport.__file__).resolve().parent != (VENDOR / 'v7' / 'backend').resolve()
                or Path(config.CONFIG_PATH).resolve() != yaml_file.resolve()):
            raise VendorIncomplete('v7 (has already loaded another copy backend) ')
        cached['link'] = dict(cached['link'], port=NOT_CHOSEN)      # V7's "auto" can never run

        class LinkTrouble(transport.LinkError):
            """What CheckedLink raises. A LinkError, so V7 code that catches LinkError behaves as before."""

            def __init__(self, code, text, raw=''):
                super().__init__(text)
                self.code, self.text, self.raw = code, text, raw

        class SyringeFault(LinkTrouble):
            def __init__(self, number, text):
                super().__init__('device', text, 'PUMP,error=%s' % number)
                self.number = number

        _v7 = types.SimpleNamespace(
            config=config, registry=registry, transport=transport, motors=motors, pump=pump, scale=scale,
            LinkError=transport.LinkError, FunctionError=registry.FunctionError, LinkTrouble=LinkTrouble,
            SyringeFault=SyringeFault, pyserial_ok=transport.serial is not None, yaml_file=yaml_file)
        if no_serial():
            forbid_serial(_v7)           # the kill switch: whoever loads V7 in this process cannot open a port
        return _v7


def forbid_serial(v7):
    """Make V7's transport unable to open a port. Never undone; an object put there by a test stays."""
    if v7.transport.serial is None or isinstance(v7.transport.serial, types.ModuleType):
        v7.transport.serial = _Forbidden()
    v7.transport.list_ports = None


class _TimedOut(Exception):
    """Inside CheckedLink: the command was written and its terminator did not arrive."""

    def __init__(self, mark):
        super().__init__('timed out')
        self.mark = mark


class _Unverified(Exception):
    """X was sent and the board does not report everything off."""


class _Spec:
    __slots__ = ('kind', 'terminators', 'timeout', 'motion', 'stop', 'motor')

    def __init__(self, kind, terminators, timeout, motion=False, stop=False, motor=None):
        self.kind, self.terminators, self.timeout = kind, terminators, timeout
        self.motion, self.stop, self.motor = motion, stop, motor


class CheckedLink:
    """What V7's functions get as ctx.link: send / snapshot / refresh_state with the checks V7 lacks."""

    def __init__(self, board, raw, v7):
        self.board, self.raw, self.v7 = board, raw, v7
        self._io = threading.RLock()     # one command and, after a missing reply, its resynchronisation
        self.dead = False                # set when the link is given up; nothing more is written
        self.desync = False              # a reply is missing: the next command resynchronises first
        self.pending_motion = False      # a motion command was written and not answered
        self.pending_stop = False
        self.motion_written = 0          # how many motion commands went onto the wire
        self.x_written = 0
        self.last_command_at = 0.0
        self.last_state_at = 0.0
        self.motion_groups = set()       # groups that were sent a motion command since the last X

    @property
    def weight_hooks(self):
        return self.raw.weight_hooks

    def snapshot(self):
        return self.raw.snapshot()

    def refresh_state(self):
        self.send('?')
        return self.snapshot()

    def send(self, command, terminators=None, timeout=None):
        """The terminators and the time limit are always this class's own; the two arguments are ignored."""
        command = str(command).strip()
        if not command:
            return []
        spec = self._classify(command)
        if spec.motion:
            self._check_gate()
        if spec.kind == 'X':
            # A stop never waits for anything but V7's own lock (the one command in flight).
            try:
                return self._exchange(command, spec)
            except _TimedOut as late:
                if self._io.acquire(timeout=X_RESYNC_WAIT_S):
                    try:
                        self._resync(late.mark)
                    finally:
                        self._io.release()
                raise self.v7.LinkTrouble('timeout', T_NO_REPLY.format(command=command))
        with self._io:
            if self.desync:
                self._resync(self._mark())
            try:
                return self._exchange(command, spec)
            except _TimedOut as late:
                self._resync(late.mark)
        # Outside the lock: the board may now send X.
        if spec.motion:
            self.board._motion_unanswered(self, command)
        raise self.v7.LinkTrouble('timeout', T_NO_REPLY.format(command=command))

    # -- one command ----------------------------------------------------------------------------
    def _classify(self, command):
        refused = self.v7.LinkTrouble('blocked', T_NOT_ALLOWED.format(command=command[:60]))
        # A longer line can lose its tail on the board (it does not read while it talks to the scale), and
        # what is left of it would be glued to the next command.
        if len(command) > LINE_MAX or any(ord(char) < 32 for char in command):
            raise refused
        head, comma, rest = command.partition(',')
        key = head.strip().upper()
        if command == '?':
            return _Spec('?', ('STATE,',), TIMEOUT_S['state'])
        if key == 'X' and not comma:
            return _Spec('X', ('OK', 'ERR'), TIMEOUT_S['motor'], stop=True)
        if key == 'C' and not comma:
            return _Spec('C', ('OK', 'ERR'), TIMEOUT_S['motor'], motion=True)
        parts = [part.strip() for part in rest.split(',')] if comma else []
        if key == 'S' and len(parts) == 1:
            return _Spec('S', ('DONE,S', 'ERR'), TIMEOUT_S['select'], motor=self._motor(parts[0]))
        if key == 'V' and len(parts) == 2:
            try:
                moving = float(parts[1]) != 0
            except ValueError:
                moving = True
            return _Spec('V', ('OK', 'ERR'), TIMEOUT_S['motor'], motion=moving, stop=not moving, motor=self._motor(parts[0]))
        if key == 'P' and len(parts) == 3:
            return _Spec('P', ('OK', 'ERR'), TIMEOUT_S['motor'], motion=True, motor=self._motor(parts[0]))
        if key == 'Y' and parts and parts[0].upper() in ALLOWED_Y:
            sub = parts[0].upper()
            return _Spec('Y,' + sub, Y_TERMS, TIMEOUT_S['init' if sub == 'INIT' else 'syringe'],
                         motion=sub in MOTION_Y, stop=sub == 'STOP')
        if key == 'W' and parts and parts[0].upper() in ALLOWED_W:
            sub = parts[0].upper()
            if sub == 'CFG':
                return _Spec('W,CFG', ('CFG,',), TIMEOUT_S['cfg'])
            return _Spec('W,' + sub, ('OK', 'ERR'), TIMEOUT_S['scale'])
        # The firmware of mega/ only (V7's answers ERR,bad_cmd): the weight dose on the board, its state, the watchdog.
        if key == 'G' and parts == ['?']:
            return _Spec('G?', ('GSTAT,', 'ERR'), TIMEOUT_S['state'])
        if key in ('GP', 'GN') and len(parts) == (7 if key == 'GP' else 3) and all(part.isdigit() for part in parts):
            return _Spec(key, ('OK', 'ERR'), TIMEOUT_S['state'])      # numbers for the next dose: nothing moves
        if key == 'G' and len(parts) == 5 and all(part.isdigit() for part in parts):
            return _Spec('G', ('OK', 'ERR'), TIMEOUT_S['motor'], motion=True, motor=self._motor(parts[0]))
        if key == 'K' and len(parts) == 1 and parts[0].isdigit():
            return _Spec('K', ('OK', 'ERR'), TIMEOUT_S['state'])
        # From p2 on: the two pumps of the cleaning, and the valve of each of the eight pumps. Numbers only: nothing moves.
        if key in ('CM', 'VL') and len(parts) == (2 if key == 'CM' else 8) and all(part.isdigit() for part in parts):
            return _Spec(key, ('OK', 'ERR'), TIMEOUT_S['state'])
        raise refused

    @staticmethod
    def _motor(text):
        try:
            return int(text)
        except ValueError:
            return None

    def _check_gate(self):
        gate = self.board._gate
        if gate is None:
            raise self.v7.LinkTrouble('blocked', T_NO_GATE)
        try:
            blocked = bool(gate())
        except Exception:
            blocked = True               # an interlock that cannot answer counts as "high voltage on"
        if blocked:
            raise self.v7.LinkTrouble('blocked', T_HV)

    def _mark(self):
        log = self.raw.log
        return log[-1] if log else None

    def since(self, mark):
        """The entries of V7's link log after `mark` (all of them when the mark is gone)."""
        entries = list(self.raw.log)
        if mark is not None:
            for index in range(len(entries) - 1, -1, -1):
                if entries[index] is mark:
                    return entries[index + 1:]
        return entries

    def _exchange(self, command, spec):
        if self.dead:
            raise self.v7.LinkTrouble('link', T_LOST)
        mark = self._mark()
        if spec.motion:
            self.pending_motion = True
        if spec.stop:
            self.pending_stop = True
        try:
            lines = self.raw.send(command, spec.terminators, spec.timeout)
        except self.v7.LinkError as exc:
            text = str(exc)
            if text.startswith('ERR'):               # the board answered, and said no: nothing was started
                self._written(spec, started=False)
                self._answered(spec)
                raise self._device_error(text)
            if text.startswith(V7_TIMEOUT):          # written, never answered
                self._written(spec)
                self.desync = True
                raise _TimedOut(mark)
            self.board._link_lost(self, text)        # write failure, or V7's reader has ended
            raise self.v7.LinkTrouble('link', T_LOST)
        self._written(spec)
        if not any(line.startswith(spec.terminators) for line in lines):
            self.desync = True                       # V7 hands stray lines back as a success
            raise _TimedOut(mark)
        self._answered(spec)
        self._digest(spec, lines)
        return lines

    def _written(self, spec, started=True):
        if spec.motion and started:
            self.motion_written += 1
        if spec.kind == 'X':
            self.x_written += 1

    def _answered(self, spec):
        self.last_command_at = time.perf_counter()
        if spec.motion:
            self.pending_motion = False
        if spec.stop:
            self.pending_stop = False

    def _digest(self, spec, lines):
        board = self.board
        if spec.kind == 'X':
            self.motion_groups.clear()
        elif spec.kind == 'C':
            self.motion_groups.update(board._group_ids())
        elif spec.motion and spec.motor is not None:
            self.motion_groups.add(board._group_of(spec.motor))
        for line in lines:
            if line.startswith('STATE,'):
                self.last_state_at = time.perf_counter()
            elif line.startswith('FOUND'):
                board._syringe_seen(None)
            elif line.startswith('PUMP,'):
                fields = dict(KV.findall(line))
                try:
                    number = int(fields.get('error', '0') or 0)
                except ValueError:
                    number = -1
                board._syringe_seen(number)
                if number:
                    text = SYRINGE_ERRORS.get(number) or T_SYRINGE_OTHER.format(number=number)
                    board._syringe_fault(number, text)
                    raise self.v7.SyringeFault(number, text)

    def _device_error(self, line):
        if line.startswith('ERR,pump_'):
            self.board._syringe_silent()
        for prefix, text in DEVICE_ERRORS:
            if line.startswith(prefix):
                return self.v7.LinkTrouble('device', text, line)
        return self.v7.LinkTrouble('device', T_DEVICE_OTHER.format(line=line[:80]), line)

    # -- back in step -----------------------------------------------------------------------------
    def _resync(self, mark):
        """After a missing reply: W,CFG, whose answer (CFG,…) the board never sends unasked. The firmware
        answers in order, so when every W,CFG written since `mark` has had its CFG line, all late replies
        are gone. Failure means the link is given up."""
        if self.dead:
            raise self.v7.LinkTrouble('link', T_LOST)
        for attempt in range(RESYNC_TRIES):
            try:
                self.raw.send('W,CFG', ('CFG,',), TIMEOUT_S['cfg'])
            except self.v7.LinkError as exc:
                if str(exc).startswith((V7_WRITE_FAILED, V7_NOT_CONNECTED)):
                    break
            if self._in_step(mark, RESYNC_SETTLE_S if attempt else 0.0):
                self.desync = False
                return
        self.board._link_lost(self, 'resync')
        raise self.v7.LinkTrouble('link', T_LOST)

    def _in_step(self, mark, patience):
        limit = time.perf_counter() + patience
        while True:
            written = answered = 0
            for entry in self.since(mark):
                if entry['kind'] == 'tx' and entry['text'] == 'W,CFG':
                    written += 1
                elif entry['kind'] == 'rx' and entry['text'].startswith('CFG,'):
                    answered += 1
            if written and answered >= written:
                return True
            if time.perf_counter() >= limit:
                return False
            time.sleep(0.02)


class _Connection:
    """Everything that belongs to one open link. A new connection never reuses any of it."""

    def __init__(self, board, raw, cfg, device, generation):
        self.raw, self.cfg, self.device, self.generation = raw, cfg, device, generation
        self.checked = CheckedLink(board, raw, board._v7)
        self.ctx = board._v7.registry.Context(link=self.checked, config=cfg)
        self.simulated = bool(getattr(raw, 'simulated', False))
        speed = float(cfg['link'].get('simulation_speedup', 1) or 1) if self.simulated else 1.0
        self.k = max(speed, 1.0)         # the simulator's speed-up; 1 on the real board
        self.live = False                # True once the safe-state sequence has passed
        self.since = time.perf_counter()
        self.heartbeat_failures = 0
        self.scale_kicked = None
        self.log_mark = None
        self.caps = {'tag': '', 'dose': False, 'watchdog': False}      # what the firmware of this board can do


class _NullGuard:
    """No guard process (practice mode, tests, scripted links)."""

    def start(self, vid, pid, serial_number, log_path):
        pass

    def tell(self, line):
        pass

    def close(self, bye=True):
        pass


class _Guard:
    """Parent side of pump_guard.py: a child that reopens the port and sends X if this program dies
    while a pump runs without an end of its own (SPEC B.3.10)."""

    def __init__(self):
        self.process = None
        self.lock = threading.Lock()

    def start(self, vid, pid, serial_number, log_path):
        with self.lock:
            if self.process is not None or no_serial():
                return
            command = [sys.executable, '-B', str(GUARD_SCRIPT), '--vid', vid, '--pid', pid,
                       '--serial', serial_number or '', '--log', str(log_path)]
            try:
                self.process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError:
                self.process = None

    def tell(self, line):
        with self.lock:
            if self.process is None or self.process.stdin is None:
                return
            try:
                self.process.stdin.write((line + '\n').encode('ascii', 'replace'))
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass

    def close(self, bye=True):
        if bye:
            self.tell('bye')
        with self.lock:
            if self.process is not None and self.process.stdin is not None:
                try:
                    self.process.stdin.close()      # without "bye" the child acts as if this program had died
                except OSError:
                    pass
            self.process = None


class PumpBoard:
    _live = None                         # exactly one live board per process (V7 keeps process-wide state)
    _live_lock = threading.Lock()

    def __init__(self, data_dir, mode, store, on_event, on_alarm, ui_age, overrides=None, link_factory=None,
                 port_lister=None, sim_firmware=None):
        if mode not in ('real', 'sim', 'off'):
            raise ValueError("pump board mode must be 'real', 'sim' or 'off'")
        if sim_firmware not in (None, 'plasmic', 'stock'):
            raise ValueError("sim_firmware must be 'plasmic' or 'stock'")
        self._sim_firmware = sim_firmware
        self._adopt_checked = None
        self.data_dir = Path(data_dir)
        self.mode = mode
        self.store = store
        self._on_event, self._on_alarm, self._ui_age = on_event, on_alarm, ui_age
        self._overrides, self._link_factory, self._port_lister = overrides, link_factory, port_lister
        self.lock = threading.RLock()            # the wrapper's own fields; never held across a command
        self.op_lock = threading.Lock()          # one motion-starting operation at a time; stops never take it
        self._stop_lock = threading.Lock()       # one stop-all sequence at a time
        self._changed = threading.Condition(self.lock)
        self._log_lock = threading.RLock()
        self._gate = None
        self._v7 = None
        self._vendor_error = ''
        self._hardware = None
        self._hardware_rev = 0                   # grows whenever the hardware view changes: the page reads bootstrap again
        self._facts = {}
        # The pump setup in force, where it comes from ('saved' · 'default' · 'board'), and V7's own layout.
        self._setup, self._setup_source, self._stock = None, '', None
        self._setup_complained = False
        self._setup_alarm = False
        # Does the board take a pump setup (CM, VL)? None until a board has answered; V7's own simulator never does.
        self._board_setup = (False if mode == 'sim' and link_factory is None
                             and (sim_firmware or SIM_FIRMWARE) == 'stock' else None)
        self._guard = _Guard() if mode == 'real' and link_factory is None else _NullGuard()
        self._guard_started = False
        self._guard_armed = False
        self._thread = None
        self._started = False
        self._closed = False
        self._closing = threading.Event()
        self._kick = threading.Event()
        self._born = time.perf_counter()
        self._conn = None
        self._generation = 0
        self._attempts = 0                       # how many links were built (tests count these)
        self._attempts_done = 0
        self._attempting = False
        self._tries = 0
        self._next_try = 0.0
        self._immediate = True
        self._hold = False                       # wrong device / forbidden: no automatic retry
        self._hold_checked = 0.0
        self._reconnect_wanted = False
        self._was_connected = False
        self._lost_alarm = False
        self._scale_alarm = False
        self._trouble = ''
        self._banner = None
        self._banner_seen = None
        self._firmware_ok = None
        self._firmware_match = None
        self._uncertain = False
        self._endless_risk = False               # a weight dose may still be driving a pump nobody can reach
        self._runs = {}                          # motor -> timed run: when the board ends it, and the host's second line
        self._tripped = {owner: {} for owner in OWNERS}
        self._op = None
        self._last_result = None
        self._weight = (None, 0.0)               # newest sample of the scale and when it arrived
        self._calibration_loaded = False
        self._calibration = None
        self._scale_restart_at = None
        self._settings = self._read_settings()
        self._syringe = {'online': None, 'initialised': False, 'busy': False, 'position': None, 'volume_ul': None,
                         'speed_ml_s': float(self._settings['syringe_speed_ml_s']), 'error': None}
        self._link, self._text, self._error, self._since = 'disabled', LINK_TEXT['disabled'], '', now_local()
        if mode == 'sim' or (mode == 'real' and not self._idle_state()):
            self._link, self._text = 'connecting', LINK_TEXT['connecting']

    # ============================================================================================
    # Public: life cycle, settings, ports
    # ============================================================================================
    def set_gate(self, hv_blocks_motion):
        """`hv_blocks_motion()` -> True while high voltage is on, pending or unknown. Until a gate is set
        every motion command is refused."""
        self._gate = hv_blocks_motion

    def start(self):
        if self.mode == 'off' or self._started or self._closed:
            return
        with PumpBoard._live_lock:
            if PumpBoard._live is not None and PumpBoard._live is not self:
                raise RuntimeError('another PumpBoard is live in this process; close() it first')
            PumpBoard._live = self
        self._started = True
        try:
            self._load()
        except VendorIncomplete as exc:
            self._vendor_error = str(exc)
            self._set_link('disabled', LINK_TEXT['disabled'], str(exc))
            return
        (self.data_dir / 'pumpboard').mkdir(parents=True, exist_ok=True)
        (self.data_dir / 'logs').mkdir(parents=True, exist_ok=True)
        os.environ['SCALE_CALIBRATION_FILE'] = str(self.data_dir / 'pumpboard' / 'scale_calibration.json')
        self._born = time.perf_counter()
        self._thread = threading.Thread(target=self._monitor, daemon=True, name='pump-monitor')
        self._thread.start()

    def close(self, budget_s=2):
        """X first, then the link is closed, then the guard is told. Leaves the outputs stopped."""
        if self._closed:
            return
        self._closed = True
        bye = True
        if self._started and self._v7 is not None and not self._vendor_error:
            limit = time.perf_counter() + float(budget_s)
            with self.lock:
                at_risk = self._guard_armed
            report = {}

            def leave():
                report.update(self._stop_all('exit', reconnect=False))
                self._release_watchdog()

            stopper = threading.Thread(target=leave, daemon=True, name='pump-close')
            stopper.start()
            stopper.join(max(0.0, float(budget_s)))
            # Something without an end of its own may still be running and the stop was not confirmed:
            # no "bye", so the guard acts as it would after a crash (it reopens the port and sends X).
            bye = bool(report.get('confirmed')) or not at_risk
            self._closing.set()
            self._kick.set()
            if self._thread is not None and self._thread is not threading.current_thread():
                self._thread.join(max(0.2, limit - time.perf_counter()))
            # A weight dose runs inside V7 on its caller's thread: give it the rest of the time to return.
            if self.op_lock.acquire(timeout=max(0.0, limit - time.perf_counter())):
                self.op_lock.release()
            self._drop_connection()
        self._closing.set()
        self._guard.close(bye=bye)
        with PumpBoard._live_lock:
            if PumpBoard._live is self:
                PumpBoard._live = None

    def _release_watchdog(self):
        """Leaving: the watchdog of the firmware of mega/ is switched off again, so that a program that opens
        the port next without restarting the board (V7's own) finds it as V7's firmware would be."""
        with self.lock:
            conn = self._conn if self._link == 'connected' and self._conn is not None and self._conn.live else None
        if conn is None or not conn.caps['watchdog']:
            return
        try:
            conn.checked.send('K,0')
        except Exception:
            pass                             # the board restarts when the port is opened again anyway

    @property
    def in_use(self):
        if self.mode == 'off' or self._vendor_error:
            return False
        return self.mode == 'sim' or self._settings.get('enabled') is True

    @property
    def connected(self):
        with self.lock:
            return self._link == 'connected' and self._conn is not None and self._conn.live

    @property
    def motion_uncertain(self):
        return self._uncertain

    def ports(self):
        """The Arduino Mega ports of this PC (nothing else is ever listed), in the shape of API.md C.3.2."""
        with self.lock:
            settings = dict(self._settings)
        current = {'enabled': settings.get('enabled'), 'device': settings.get('device') or '',
                   'description': settings.get('description') or ''}
        if self.mode != 'real':
            if self.mode == 'sim':
                current = {'enabled': True, 'device': SIM_NAME, 'description': SIM_DESCRIPTION}
            return {'simulated': self.mode == 'sim', 'current': current, 'ports': [], 'other_count': 0}
        rows = self._list_ports()
        megas = []
        for row in rows:
            if row['is_mega']:
                chosen = (settings.get('enabled') is True and self._same_identity(row, settings)
                          and row['device'] == settings.get('device'))
                megas.append({'device': row['device'], 'description': row['description'], 'vid': row['vid'],
                              'pid': row['pid'], 'serial_number': row['serial_number'], 'chosen': chosen})
        return {'simulated': False, 'current': current, 'ports': megas, 'other_count': len(rows) - len(megas)}

    def configure(self, enabled, device=None):
        """Maintenance › connection: “does not use the pump control board” (False), or one board out of several (enabled=True, device). With one
        Arduino Mega on this PC nobody has to call this: it is taken by itself (_adopt)."""
        if self.mode != 'real':
            raise ValueError(T_SIM_CONFIG)
        if enabled is not True and enabled is not False:
            raise ValueError('enabled Must be true or false')
        with self.lock:
            settings = dict(self._settings)
        if enabled:
            device = str(device or '')
            row = next((item for item in self._list_ports() if item['device'] == device), None)
            if row is None or not row['is_mega']:
                raise ValueError(T_NOT_MEGA.format(device=device or '?'))
            settings.update(enabled=True, device=row['device'], vid=row['vid'], pid=row['pid'],
                            serial_number=row['serial_number'], description=row['description'], chosen_at=now_local(),
                            chosen_by='user')        # chosen by hand: never swapped for another board by itself
        else:
            settings['enabled'] = False
        self._write_settings(settings)
        self._clear_alarm(A_WRONG_DEVICE)
        self._request_reconnect()
        return {'saved': True}

    def reconnect(self):
        """“Reconnect”: the link is closed (after X) and opened again; a real board restarts."""
        if self.mode == 'off' or self._vendor_error:
            raise ValueError(T_NOT_SET)
        if self.mode == 'real' and self._settings.get('enabled') is not True:
            raise ValueError(T_NOT_SET)
        self._request_reconnect()
        return {'accepted': True}

    # ---- the dose factor of each pump (Maintenance › Pump and Scale) ----------------------------------------------------
    def _saved_calibration(self):
        """{motor: {'dose_pulses_per_ml', 'nominal_ml', 'measured_ml', 'saved_at'}} out of the settings file."""
        out = {}
        saved = self._settings.get('pump_calibration')
        for key, row in (saved.items() if isinstance(saved, dict) else ()):
            try:
                motor, factor = int(key), float(row['dose_pulses_per_ml'])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(factor) and factor > 0:
                out[motor] = dict(row, dose_pulses_per_ml=factor)
        return out

    def _apply_pump_calibration(self, cfg):
        """V7 reads a channel's own `dose_pulses_per_ml` when it has one (functions/motors.py): the saved
        calibrations go there. The speed factor (`pulses_per_ml`) stays V7's, the same for every pump."""
        saved = self._saved_calibration()
        for group in cfg['motors']['groups']:
            for row in group['channels']:
                if 'plasmic_base' not in row:    # what the settings file itself says for this channel (None: nothing)
                    row['plasmic_base'] = row.get('dose_pulses_per_ml')
                if int(row['id']) in saved:
                    row['dose_pulses_per_ml'] = saved[int(row['id'])]['dose_pulses_per_ml']
                elif row['plasmic_base'] is None:
                    row.pop('dose_pulses_per_ml', None)
                else:
                    row['dose_pulses_per_ml'] = row['plasmic_base']

    def _use_calibration(self):
        """The saved calibrations into what is in use now: the hardware view and the live connection."""
        self._load()
        self._read_hardware()                    # the view, with the factors of this moment
        with self.lock:
            conn = self._conn
        if conn is not None:
            self._apply_pump_calibration(conn.cfg)   # V7 reads the factor when a dose is asked for

    def pump_calibration(self):
        """One row per pump for Maintenance › Pump and Scale: its factor now, the factory one, and what the last calibration measured."""
        hardware = self.hardware() or {}
        with self.lock:
            saved = self._saved_calibration()
        rows = []
        for group in hardware.get('groups', []):
            for motor in group['motors']:
                row = saved.get(motor['id']) or {}
                rows.append({'motor': motor['id'], 'pump': motor['pump'], 'label': motor['label'],
                             'dose_pulses_per_ml': motor['dose_pulses_per_ml'],
                             'factory_pulses_per_ml': motor['factory_pulses_per_ml'],
                             'calibrated': bool(row), 'nominal_ml': row.get('nominal_ml'),
                             'measured_ml': row.get('measured_ml'), 'saved_at': row.get('saved_at')})
        return rows

    def calibrate_pump(self, motor, measured_ml, nominal_ml=10.0):
        """`nominal_ml` were dosed with the factor in use and `measured_ml` came out: the pump's own factor
        becomes factor × nominal / measured. Sends nothing to the board."""
        motor, _group = self._known_motor(motor)

        def number(value):
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float('nan')

        nominal, measured = number(nominal_ml), number(measured_ml)
        low, high = CAL_NOMINAL_ML
        if not (math.isfinite(nominal) and low <= nominal <= high):
            raise PumpBoardError('refused', T_CAL_NOMINAL.format(low=_g(low), high=_g(high)))
        if not (math.isfinite(measured) and nominal * CAL_RATIO[0] <= measured <= nominal * CAL_RATIO[1]):
            raise PumpBoardError('refused', T_CAL_MEASURED.format(nominal=_g(nominal), low=_g(nominal * CAL_RATIO[0]),
                                                                  high=_g(nominal * CAL_RATIO[1])))
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)     # not while something is being dosed with the old factor
        try:
            before = next(row['dose_pulses_per_ml'] for row in self.pump_calibration() if row['motor'] == motor)
            after = round(float(before) * nominal / measured, 4)
            with self.lock:
                settings = dict(self._settings)
            saved = dict(settings.get('pump_calibration') or {})
            saved[str(motor)] = {'dose_pulses_per_ml': after, 'nominal_ml': nominal, 'measured_ml': measured,
                                 'before': float(before), 'saved_at': now_local()}
            settings['pump_calibration'] = saved
            self._write_settings(settings)
            self._use_calibration()
        finally:
            self.op_lock.release()
        change = '%+.1f%%' % ((after / float(before) - 1.0) * 100.0)
        self._event(E_CAL_SAVED.format(label=self._label(motor), nominal=_g(nominal), measured=_g(measured),
                                       before=_g(round(float(before), 1)), after=_g(round(after, 1)), change=change), 'info')
        return {'motor': motor, 'dose_pulses_per_ml': after, 'before': float(before), 'change_percent': round(
            (after / float(before) - 1.0) * 100.0, 2)}

    def reset_pump_calibration(self, motor):
        """Back to the factor of the settings file of the board."""
        motor, _group = self._known_motor(motor)
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)
        try:
            with self.lock:
                settings = dict(self._settings)
            saved = dict(settings.get('pump_calibration') or {})
            saved.pop(str(motor), None)
            settings['pump_calibration'] = saved
            self._write_settings(settings)
            self._use_calibration()
        finally:
            self.op_lock.release()
        after = next(row['dose_pulses_per_ml'] for row in self.pump_calibration() if row['motor'] == motor)
        self._event(E_CAL_RESET.format(label=self._label(motor), after=_g(round(float(after), 1))), 'info')
        return {'motor': motor, 'dose_pulses_per_ml': after}

    # ---- what each pump carries, the two pumps of the cleaning, the valves (Maintenance › Pump and Scale; AMENDMENTS M20) ----
    @staticmethod
    def _stock_setup(cfg):
        """V7's own layout, read from a cfg before anything of it is changed and kept inside it."""
        stock = cfg.get('plasmic_stock')
        if stock is None:
            numbers, groups, fluids, halves = {}, {}, {}, {}
            for group in cfg['motors']['groups']:
                first = len(numbers) + 1
                for row in group['channels']:
                    motor = int(row['id'])
                    numbers[motor] = len(numbers) + 1
                    groups[motor] = group['id']
                    fluids[motor] = str(row.get('fluid') or '')
                halves[group['id']] = 'Pump %d–%d' % (first, len(numbers))
            stock = cfg['plasmic_stock'] = {
                'numbers': numbers, 'groups': groups, 'fluids': fluids, 'halves': halves,
                'water_motor': int(cfg['cleaning']['water_motor']), 'out_motor': int(cfg['cleaning']['out_motor']),
                'links': {int(link['valve']): int(link['motor']) for link in cfg['valves']['motor_links']},
                'valves': [int(row['id']) for row in cfg['valves']['channels']]}
        return stock

    @staticmethod
    def _factory_setup(stock, valves):
        """V7's names and its two pumps of the cleaning; with V7's valve links (what a board that cannot be told
        does) or with none (this program's default: no valve is used until somebody sets one)."""
        pumps = {}
        for motor, fluid in stock['fluids'].items():
            if motor == stock['water_motor']:
                pumps[motor] = {'use': USE_WATER, 'name': NAME_WATER}
            elif motor == stock['out_motor']:
                pumps[motor] = {'use': USE_OUT, 'name': NAME_OUT}
            else:
                pumps[motor] = {'use': USE_CHEMICAL if fluid else USE_NONE, 'name': fluid}
        return {'pumps': pumps, 'valves': {valve: (stock['links'].get(valve) if valves else None) for valve in stock['valves']}}

    @staticmethod
    def _clean_setup(wanted, stock):
        """A setup as the page sends it and as the settings file keeps it ({'pumps': [{motor, use, name}],
        'valves': [{id, motor}]}), checked: {'pumps': {motor: {'use', 'name'}}, 'valves': {valve: motor or None}}.
        ValueError(sentence) for what cannot be."""
        numbers = stock['numbers']
        if not isinstance(wanted, dict) or not isinstance(wanted.get('pumps'), list) or not isinstance(wanted.get('valves'), list):
            raise ValueError(T_SETUP_SHAPE)
        pumps = {}
        for row in wanted['pumps']:
            if not isinstance(row, dict) or type(row.get('motor')) is not int or row['motor'] not in numbers or row['motor'] in pumps:
                raise ValueError(T_SETUP_SHAPE)
            motor, use = row['motor'], row.get('use')
            shown = 'Pump %d' % numbers[motor]
            if use not in USES:
                raise ValueError(T_SETUP_SHAPE)
            name = {USE_WATER: NAME_WATER, USE_OUT: NAME_OUT}.get(use, '')
            if use == USE_CHEMICAL:
                name = row.get('name')
                if not isinstance(name, str):
                    raise ValueError(T_SETUP_NAME.format(pump=shown, most=SETUP_NAME_MAX))
                name = ' '.join(name.split())
                if (not 1 <= len(name) <= SETUP_NAME_MAX
                        or any(char in SETUP_NAME_BAD or ord(char) < 32 or ord(char) == 127 for char in name)):
                    raise ValueError(T_SETUP_NAME.format(pump=shown, most=SETUP_NAME_MAX))
                if name == NAME_WATER or name.upper() == NAME_OUT:
                    raise ValueError(T_SETUP_RESERVED.format(pump=shown))
            pumps[motor] = {'use': use, 'name': name}
        if set(pumps) != set(numbers):
            raise ValueError(T_SETUP_SHAPE)
        water = [motor for motor, row in pumps.items() if row['use'] == USE_WATER]
        out = [motor for motor, row in pumps.items() if row['use'] == USE_OUT]
        if len(water) != 1:
            raise ValueError(T_SETUP_ONE_WATER)
        if len(out) != 1:
            raise ValueError(T_SETUP_ONE_OUT)
        if stock['groups'][water[0]] == stock['groups'][out[0]]:
            halves = list(stock['halves'].values())
            raise ValueError(T_SETUP_HALVES.format(a=halves[0], b=halves[-1]))
        valves = {}
        for row in wanted['valves']:
            if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] not in stock['valves'] or row['id'] in valves:
                raise ValueError(T_SETUP_SHAPE)
            motor = row.get('motor')
            if motor is not None and (type(motor) is not int or motor not in numbers):
                raise ValueError(T_SETUP_VALVE.format(valve=row['id']))
            if motor is not None and motor in valves.values():
                raise ValueError(T_SETUP_VALVE_TWICE.format(pump='Pump %d' % numbers[motor]))
            valves[row['id']] = motor
        if set(valves) != set(stock['valves']):
            raise ValueError(T_SETUP_SHAPE)
        return {'pumps': pumps, 'valves': valves}

    @staticmethod
    def _setup_shape(setup):
        """The checked setup in the shape of the settings file and of POST /api/pump/setup."""
        return {'pumps': [{'motor': motor, 'use': row['use'], 'name': row['name']} for motor, row in sorted(setup['pumps'].items())],
                'valves': [{'id': valve, 'motor': motor} for valve, motor in sorted(setup['valves'].items())]}

    @staticmethod
    def _setup_roles(setup):
        """(the pump that carries water, the pump that drains): the two of the cleaning."""
        return (next(motor for motor, row in setup['pumps'].items() if row['use'] == USE_WATER),
                next(motor for motor, row in setup['pumps'].items() if row['use'] == USE_OUT))

    @staticmethod
    def _setup_words(setup, stock):
        """One line for the event log and the alarm: “water=Pump 1, OUT=Pump 7; Pump 8=CuSO₄; solenoid valve: not used”."""
        numbers = stock['numbers']
        water, out = PumpBoard._setup_roles(setup)
        parts = ['%s=Pump %d, %s=Pump %d' % (NAME_WATER, numbers[water], NAME_OUT, numbers[out])]
        named = ['Pump %d=%s' % (numbers[motor], row['name']) for motor, row in sorted(setup['pumps'].items())
                 if row['use'] == USE_CHEMICAL]
        if named:
            parts.append(', '.join(named))
        linked = ['solenoid valve %d follow Pump %d' % (valve, numbers[motor]) for valve, motor in sorted(setup['valves'].items())
                  if motor is not None]
        parts.append(', '.join(linked) if linked else T_SETUP_NO_VALVE)
        return '; '.join(parts)

    def _setup_in_force(self, stock, board_takes_it):
        """(the setup, where it comes from). A board that cannot be told: the layout of its firmware ('board').
        Else what was saved ('saved'), else V7's names and cleaning pumps with no valve in use ('default')."""
        if not board_takes_it:
            return self._factory_setup(stock, True), 'board'
        with self.lock:
            saved = self._settings.get('pump_setup')
        if saved is not None:
            try:
                return self._clean_setup(saved, stock), 'saved'
            except ValueError:
                if not self._setup_complained:
                    self._setup_complained = True
                    self._event(E_SETUP_BAD, 'warn')
        return self._factory_setup(stock, False), 'default'

    def _apply_setup(self, cfg, board_takes_it=None):
        """The setup in force written into a cfg of V7 (its functions and its simulator read it there): the name of
        what each pump carries, the two pumps of the cleaning, the valve of each pump. Returns (setup, source)."""
        stock = self._stock_setup(cfg)
        if board_takes_it is None:
            board_takes_it = self._board_setup is not False
        setup, source = self._setup_in_force(stock, board_takes_it)
        names = {motor: row['name'] for motor, row in setup['pumps'].items()}
        for group in cfg['motors']['groups']:
            for row in group['channels']:
                motor = int(row['id'])
                if names[motor]:
                    row['fluid'] = names[motor]
                else:
                    row.pop('fluid', None)
                row['label'] = 'motor %d' % motor + (' · %s' % names[motor] if names[motor] else '')
        water, out = self._setup_roles(setup)
        # New dicts and lists, never changed in place: V7's reader thread may be looking at the old ones.
        cfg['cleaning'] = dict(cfg['cleaning'], water_motor=water, out_motor=out)
        owners = setup['valves']
        cfg['valves'] = dict(
            cfg['valves'],
            motor_links=[{'motor': motor, 'valve': valve, 'fluid': names[motor]} for valve, motor in sorted(owners.items())
                         if motor is not None],
            channels=[dict(row, label='solenoid valve %d' % int(row['id']) + (
                ' · %s' % names[owners[int(row['id'])]] if owners.get(int(row['id'])) is not None and names[owners[int(row['id'])]]
                else '')) for row in cfg['valves']['channels']])
        return setup, source

    @staticmethod
    def _setup_commands(setup):
        """((command, the answer that confirms it), …): CM and VL of the firmware of mega/."""
        water, out = PumpBoard._setup_roles(setup)
        valve_of = {motor: valve for valve, motor in setup['valves'].items() if motor is not None}
        digits = [str(valve_of.get(motor, 0)) for motor in sorted(setup['pumps'])]
        return (('CM,%d,%d' % (water, out), 'OK,clean=%d:%d' % (water, out)),
                ('VL,' + ','.join(digits), 'OK,links=' + ''.join(digits)))

    def _board_setup_send(self, conn, setup):
        """Tell the board (CM, VL). Both only store numbers; the board takes VL only while nothing moves and
        every valve is closed. From here until its next restart the board holds this setup."""
        for command, wanted in self._setup_commands(setup):
            lines = conn.checked.send(command)
            if wanted not in [str(line).strip() for line in lines]:
                reply = next((str(line) for line in lines if str(line).startswith(('OK', 'ERR'))), '')
                raise self._v7.LinkTrouble('device', T_SETUP_REFUSED.format(command=command, reply=reply[:40] or '?'))

    def _board_setup_apply(self, conn):
        """Part of every start of a connection, right after X: the board gets the setup in force (when its firmware
        takes one) and this connection's cfg and the hardware view say what the board now does."""
        takes = bool(conn.caps.get('setup'))
        stock = self._stock_setup(conn.cfg)
        if takes:
            self._board_setup_send(conn, self._setup_in_force(stock, True)[0])
        self._apply_setup(conn.cfg, takes)
        with self.lock:
            before, self._board_setup = self._board_setup, takes
            saved = self._settings.get('pump_setup')
        if (before is not False) != takes or self._hardware is None:
            self._read_hardware()                # the names, the cleaning pumps and the valves on the screens follow
        ignored = False
        if not takes and saved is not None:
            try:
                ignored = self._clean_setup(saved, stock) != self._factory_setup(stock, True)
            except ValueError:
                ignored = False
        if ignored:
            key, priority, area, kind, sentence = A_SETUP
            self._setup_alarm = True
            self._alarm((key, priority, area, kind, sentence.format(now=self._setup_words(self._factory_setup(stock, True), stock))))
        elif self._setup_alarm:
            self._setup_alarm = False
            self._clear_alarm(A_SETUP)

    def pump_setup(self):
        """GET /api/pump/setup: one row per pump and per valve, as they are in force, and whether they can be changed."""
        hardware = self.hardware()
        with self.lock:
            setup, source, takes = self._setup, self._setup_source, self._board_setup
        if not hardware or setup is None:
            raise PumpBoardError('refused', self._vendor_error or T_NOT_SET)
        rows = []
        for group in hardware['groups']:
            numbers = [motor['pump'] for motor in group['motors']]
            half = 'Pump %d–%d' % (min(numbers), max(numbers))
            for motor in group['motors']:
                row = setup['pumps'][motor['id']]
                rows.append({'motor': motor['id'], 'pump': motor['pump'], 'short': motor['short'], 'label': motor['label'],
                             'group': group['id'], 'half': half, 'use': row['use'], 'name': row['name'],
                             'valve': motor['valve']})
        return {'editable': takes is not False, 'reason': '' if takes is not False else T_SETUP_FIRMWARE, 'source': source,
                'pumps': rows, 'valves': [dict(valve) for valve in hardware['valves']],
                'name_max': SETUP_NAME_MAX, 'names': {USE_WATER: NAME_WATER, USE_OUT: NAME_OUT},
                'valve_open_before_ms': hardware['valve_open_before_ms'],
                'valve_close_after_ms': hardware['valve_close_after_ms']}

    def check_pump_setup(self, wanted):
        """The setup as it would be saved, checked and in the shape of pump_setup()['pumps'] / ['valves'], without
        saving or sending anything: {'pumps': {motor: {'use', 'name'}}, 'valves': {valve: motor or None}}."""
        self.hardware()
        with self.lock:
            stock = self._stock
        if stock is None:
            raise PumpBoardError('refused', self._vendor_error or T_NOT_SET)
        return self._clean_setup(wanted, stock)

    def save_pump_setup(self, wanted):
        """Maintenance › Pump and Scale › What is the pump connected to?. The setup goes into the settings file and a connected board is told at once (CM, VL:
        numbers only, nothing moves); a board that does not take it leaves the file as it was and the link is made
        again. ValueError for a setup that cannot be."""
        setup = self.check_pump_setup(wanted)
        with self.lock:
            stock, takes = self._stock, self._board_setup
        if takes is False:
            raise PumpBoardError('refused', T_SETUP_FIRMWARE)
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)     # not while something is being dosed with the old layout
        try:
            with self.lock:
                conn = self._conn if self._link == 'connected' and self._conn is not None and self._conn.live else None
            if conn is not None:
                self._need_quiescent(conn)
            with self.lock:
                before = dict(self._settings)
            # The file first: a board that restarts in this very moment is told what the file says, and that must be
            # the setup this call is about to send. When the board then does not take it, the file is put back.
            self._write_settings(dict(before, pump_setup=dict(self._setup_shape(setup), saved_at=now_local())))
            try:
                if conn is not None:
                    self._board_setup_send(conn, setup)
            except Exception as exc:
                try:
                    self._write_settings(before)
                finally:
                    self._request_reconnect()    # the board may hold half of the new setup: X, then what the file says
                if isinstance(exc, self._v7.LinkTrouble):
                    raise PumpBoardError(exc.code, exc.text)
                if isinstance(exc, self._v7.LinkError):
                    raise PumpBoardError('link', str(exc))
                raise
            with self.lock:
                self._setup_complained = False
            if conn is not None:
                self._apply_setup(conn.cfg, True)
            self._read_hardware()
        finally:
            self.op_lock.release()
        reply = self.pump_setup()
        reply['summary'] = self._setup_words(setup, stock)
        return reply

    def apply_settings(self, syringe_ul, syringe_speed_ml_s):
        """Maintenance › Pump and Scale: syringe size and the speed applied after every initialise."""
        hardware = self.hardware() or {}
        options = (hardware.get('syringe') or {}).get('options_ul') or [5000, 1250]
        if isinstance(syringe_ul, bool) or syringe_ul not in options:
            raise ValueError(T_SIZE)
        syringe_ul = int(syringe_ul)
        top = self._max_speed(syringe_ul)
        try:
            speed = float(syringe_speed_ml_s)
        except (TypeError, ValueError):
            raise ValueError(T_SPEED_RANGE.format(top=top))
        if isinstance(syringe_speed_ml_s, bool) or not math.isfinite(speed) or not 0.001 <= speed <= top:
            raise ValueError(T_SPEED_RANGE.format(top=top))
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)
        try:
            with self.lock:
                conn = self._conn if self._link == 'connected' and self._conn is not None and self._conn.live else None
                old_size = int(self._settings.get('syringe_ul') or 0)
                initialised = self._syringe['initialised']
            if conn is not None:
                self._need_quiescent(conn)
            self._write_settings(dict(self._settings, syringe_ul=syringe_ul, syringe_speed_ml_s=speed))
            with self.lock:
                if syringe_ul != old_size:
                    self._syringe.update(initialised=False, volume_ul=None, position=None)
                self._syringe['speed_ml_s'] = speed
            if conn is not None:
                self._invoke(conn, 'pump.set_syringe', {'volume_ul': syringe_ul})
                if syringe_ul == old_size and initialised:
                    self._invoke(conn, 'pump.set_speed', {'speed_ml_s': speed})
        finally:
            self.op_lock.release()
        return {'saved': True, 'settings': {'syringe_ul': syringe_ul, 'syringe_speed_ml_s': speed}}

    def deadline_tripped(self, owner):
        """The sentence of a timed run of this owner whose time ran out before it was stopped, once; else None.
        For a round that is a failure (its pump_off should have come first); by hand it is the normal end."""
        with self.lock:
            flags = self._tripped.get(owner)
            if not flags:
                return None
            text = next(iter(flags.values()))
            flags.clear()
            return text

    # ============================================================================================
    # Public: views
    # ============================================================================================
    def hardware(self):
        """The vendored hardware.yaml in the shape of bootstrap.hardware (API.md C.3.1 and amendment M10);
        None when pc/vendor is incomplete."""
        with self.lock:
            if self._hardware is not None:
                return copy.deepcopy(self._hardware)
        try:
            self._load()
        except VendorIncomplete as exc:
            self._vendor_error = str(exc)
            if self.mode != 'off':
                self._set_link('disabled', LINK_TEXT['disabled'], str(exc))
            return None
        with self.lock:
            return copy.deepcopy(self._hardware)

    def facts(self):
        """The `board` block of a round record (SPEC D.2): what a later analysis needs to know."""
        self.hardware()
        with self.lock:
            conn, calibration = self._conn, self._calibration or {}
            facts = {key: self._facts.get(key) for key in ('hardware_sha256', 'pulses_per_ml', 'dose_pulses_per_ml',
                                                           'dose_flow_ml_min')}
            facts['pump_dose_pulses_per_ml'] = {str(motor): row['dose_pulses_per_ml']
                                                for motor, row in sorted(self._saved_calibration().items())}
            facts.update(simulated=self.mode == 'sim',
                         device=conn.device if conn is not None else (self._settings.get('device') or ''),
                         firmware_banner=self._banner, firmware_match=self._firmware_match,
                         firmware_tag=conn.caps['tag'] if conn is not None else '',
                         syringe_ul=self._settings['syringe_ul'], syringe_speed_ml_s=self._syringe['speed_ml_s'],
                         scale_calibration={key: calibration.get(key) for key in ('divisor', 'offset', 'saved_at')})
            return facts

    def view(self):
        """state.pump of API.md C.6. Never sends anything to the board."""
        hardware = self.hardware() if self.mode != 'off' else None
        now = time.perf_counter()
        with self.lock:
            settings = dict(self._settings)
            conn = self._conn if self._link == 'connected' and self._conn is not None and self._conn.live else None
            state = self._copy_state(conn)
            runs = {motor: dict(run) for motor, run in self._runs.items()}
            op = dict(self._op) if self._op is not None else None
            syringe = dict(self._syringe)
            sample, sample_at = self._weight
            calibration = dict(self._calibration or {})
            calibrated = bool(self._calibration_loaded)
            out = {'mode': self.mode, 'link': self._link, 'text': self._text, 'device': settings.get('device') or '',
                   'description': settings.get('description') or '', 'error': self._error, 'since': self._since,
                   'generation': self._generation,   # grows with every new connection and every restart of the board
                   'banner_seen': self._banner_seen, 'firmware_ok': self._firmware_ok,
                   'firmware_match': self._firmware_match, 'motion_uncertain': self._uncertain,
                   # '' = V7's own firmware (or not connected); 'p1'… = the firmware of mega/ (weight dose on the board)
                   'firmware_tag': conn.caps['tag'] if conn is not None else '',
                   # the number of bootstrap.hardware.rev that goes with this state: when the page holds another
                   # one (the pump setup or a calibration was saved), it reads bootstrap again
                   'hardware_rev': self._hardware_rev,
                   'cleaning': False, 'clean_phase': '', 'groups': [], 'motors': [], 'valves': []}
            last_result = dict(self._last_result) if self._last_result else None
        if self.mode == 'sim':
            out['device'], out['description'] = SIM_NAME, SIM_DESCRIPTION
        size = int(settings['syringe_ul'])
        out['syringe'] = {'online': syringe['online'] if conn is not None else None,
                          'initialised': bool(syringe['initialised']) and conn is not None,
                          'busy': bool(syringe['busy']) and conn is not None, 'size_ul': size,
                          'position': syringe['position'], 'volume_ul': syringe['volume_ul'],
                          'speed_ml_s': syringe['speed_ml_s'], 'max_speed_ml_s': self._max_speed(size),
                          'error': syringe['error']}
        age = now - sample_at if sample is not None else None
        fresh = conn is not None and age is not None and age <= SCALE_SILENT_S
        out['scale'] = {'online': fresh, 'grams': sample.get('grams') if fresh else None,
                        'stable': bool(sample.get('stable')) if fresh else False,
                        'age_s': round(age, 1) if fresh else None, 'calibrated': calibrated and conn is not None,
                        'calibrated_at': calibration.get('saved_at'), 'reference_g': calibration.get('reference_grams')}
        out.update(op=None, weight_dose=None, last_result=last_result,
                   settings={'syringe_ul': size, 'syringe_speed_ml_s': float(settings['syringe_speed_ml_s'])})
        if not hardware:
            return out
        cleaning = bool(state.get('cleaning'))
        clean = hardware['cleaning']
        weighing = op['motor'] if op and op['kind'] == 'dose_weight' else None
        for group in hardware['groups']:
            slot = (state.get('motors') or {}).get(group['id']) or {}
            selected, group_state = slot.get('selected'), slot.get('state') or 'idle'
            # A timed run is a finite move for the firmware ("move"); for people it is a pump that runs.
            timed = runs.get(selected) if group_state != 'idle' else None
            shown = 'run' if timed is not None and group_state == 'move' else group_state
            out['groups'].append({'id': group['id'], 'label': group['label'], 'selected': selected, 'state': shown})
            for motor in group['motors']:
                number = motor['id']
                row = {'id': number, 'label': motor['label'], 'fluid': motor['fluid'], 'group': group['id'],
                       'valve': motor['valve'], 'state': 'off', 'flow_ml_min': None, 'direction': 1, 'left_s': None,
                       'by': None, 'blocked': ''}
                if cleaning and number in (clean['water_motor'], clean['out_motor']):
                    row['state'] = 'clean'
                    row['by'] = op['by'] if op and op['kind'] == 'clean' else None
                elif weighing == number:
                    row['state'], row['by'] = 'weigh', op['by']
                elif selected == number and group_state != 'idle':
                    row['state'] = shown
                    if timed is not None:
                        row.update(flow_ml_min=timed['flow_ml_min'], direction=timed['direction'], by=timed['owner'],
                                   left_s=max(0, int(math.ceil(timed['ends'] - now))))
                    elif op and (op.get('motor') == number or number in (op.get('motors') or ())):
                        row['by'], row['direction'] = op['by'], -1 if op.get('direction') == -1 else 1
                elif group_state != 'idle' or (cleaning and selected is not None):
                    row['blocked'] = '%sIn use' % group['label']
                out['motors'].append(row)
        valves = state.get('valves') or {}
        out['valves'] = [{'id': valve['id'], 'label': valve['label'], 'open': bool(valves.get(valve['id']))}
                         for valve in hardware['valves']]
        out['cleaning'] = cleaning
        out['clean_phase'] = self._clean_phase(state, clean) if cleaning else ''
        if op is not None:
            elapsed = now - op['t0']
            gain = None
            if op['kind'] == 'dose_weight':
                grams = sample.get('grams') if sample is not None else None
                if op.get('board_gain') is not None:
                    gain = round(float(op['board_gain']), 3)             # what the board itself measures against
                elif isinstance(grams, (int, float)) and op.get('start_g') is not None:
                    gain = round(float(grams) - op['start_g'], 3)
                out['weight_dose'] = {'motor': op['motor'], 'target_g': op['target_g'], 'gain_g': gain,
                                      'elapsed_s': round(elapsed, 1)}
            out['op'] = {'id': op['id'], 'kind': op['kind'], 'motor': op.get('motor'), 'by': op['by'],
                         'started_at': op['started_at'], 'eta_s': op.get('eta_s'), 'elapsed_s': round(elapsed, 1),
                         'text': self._op_text(op, elapsed, gain, out['clean_phase'])}
        return out

    def quiescent(self, fresh=True):
        """(True, []) when nothing of the board moves or is about to: both groups idle, not cleaning, all
        valves closed, no operation, no run with a deadline, syringe not busy. Else (False, [reason]);
        a reason is {code, text, fix}."""
        with self.lock:
            conn = self._conn if self._link == 'connected' else None
            if conn is None or not conn.live or conn.checked.dead:
                return False, [self._reason('pump_link', T_NOT_CONNECTED.format(text=self._text), 'examine USB Wire')]
        return self._quiescent(conn, fresh, in_operation=False)

    # ============================================================================================
    # Public: operations (SPEC B.3.6)
    # ============================================================================================
    def run(self, motor, flow_ml_min, direction, limit_s, owner):
        """Start a run that lasts `limit_s` seconds. Returns at once.

        It is not V7's endless `V` (motors.run) but the firmware's finite move `P` with
        steps = speed × time, so the board itself stops the motor when the time is over — also when
        this PC is asleep, frozen or gone. The speed, its sign and the flow limits are V7's own.
        """
        self._check_owner(owner)
        try:
            limit = float(limit_s)
        except (TypeError, ValueError):
            raise PumpBoardError('refused', T_RUN_LIMIT)
        if not RUN_LIMIT_MIN_S <= limit <= RUN_LIMIT_MAX_S:
            raise PumpBoardError('refused', T_RUN_LIMIT)
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)
        try:
            conn = self._session()
            motor, group = self._known_motor(motor)
            self._send(conn, '?')
            state = conn.raw.state
            if state.get('cleaning'):
                raise PumpBoardError('refused', T_CLEANING_NO)
            slot = state['motors'][group['id']]
            if slot.get('state') != 'idle':
                busy = slot.get('selected')
                if busy == motor:
                    raise PumpBoardError('refused', T_ALREADY_RUNNING.format(label=self._label(motor)))
                raise PumpBoardError('refused', T_GROUP_BUSY.format(group=group['label'], label=self._label(busy)))
            v7 = self._v7
            try:
                # What V7's motors.run checks and computes, in its order, without its endless `V`.
                values = self._coerce('motors.run', {'motor': motor, 'flow_ml_min': flow_ml_min, 'direction': direction})
                v7.motors._reject_during_weight_dose()
                v7.motors._check_flow(conn.cfg, values['flow_ml_min'], motor)
                sps = v7.motors.flow_to_sps(conn.cfg, values['flow_ml_min'], motor)
            except v7.FunctionError as exc:
                raise PumpBoardError('refused', self._words(exc))
            sign = 1 if values['direction'] > 0 else -1
            steps = max(1, int(round(sps * limit))) * sign       # the sign is the direction; the speed stays positive
            # How long the board will take by itself (the simulator runs its moves `k` times faster).
            valve_s = int(conn.cfg['valves']['open_before_ms']) / 1000.0 if self._valve_of(motor) else 0.0
            span = valve_s + limit / conn.k
            run = {'owner': owner, 'flow_ml_min': _number(values['flow_ml_min']), 'limit_s': limit, 'direction': sign,
                   'sps': sps, 'steps': steps, 'confirmed': False}
            self._clock_run(run, span)
            with self.lock:
                # Known before the command goes out: a stop, a lost link or a restart of the board that comes
                # while it is being started must find it. What an earlier run of this motor left to tell is void.
                for flags in self._tripped.values():
                    flags.pop(motor, None)
                self._runs[motor] = run
            written = conn.checked.motion_written
            try:
                self._send(conn, 'P,%d,%d,%d' % (motor, steps, sps))
                with self.lock:
                    self._clock_run(run, span)
                self._send(conn, '?')
            except PumpBoardError:
                with self.lock:
                    if self._runs.get(motor) is run:
                        del self._runs[motor]
                if conn.checked.motion_written > written and self._alive(conn):
                    self._stop_all('run')        # the move was taken and could not be checked: nothing is left running
                raise
            slot = conn.raw.state['motors'][group['id']]
            moving = slot.get('selected') == motor and slot.get('state') in ('wait', 'move')
            with self.lock:
                mine = self._runs.get(motor) is run
                if mine and moving:
                    run['confirmed'] = True
                elif mine:
                    del self._runs[motor]
            if not mine:
                # Stopped while it was being started (“OFF” on that row, STOP, the page watchdog, a restart of
                # the board). The stop may have reached the board before the move did.
                if moving:
                    self._halt(conn, motor, 'run')
                raise PumpBoardError('stopped', T_STOPPED)
            if not moving:
                self._stop_all('run')
                raise PumpBoardError('device', T_RUN_NOT_STARTED.format(label=self._label(motor)))
            left = run['ends'] - time.perf_counter()
            until = datetime.fromtimestamp(time.time() + left).astimezone().isoformat(timespec='seconds')
            return {'motor': motor, 'sps': sps, 'steps': steps, 'until': until, 'limit_s': limit}
        finally:
            self.op_lock.release()

    @staticmethod
    def _clock_run(run, span):
        """When the board will have ended this run by itself, and the host's second line behind that."""
        run['started'] = time.perf_counter()
        run['ends'] = run['started'] + span
        run['deadline'] = run['ends'] + span * RUN_SLOW_SHARE + RUN_SECOND_LINE_S

    def stop_motor(self, motor):
        """Stop one motor. Never refused for ownership and never waits for a running operation."""
        conn = self._session(stopping=True)
        motor, _ = self._known_motor(motor)
        if conn.raw.state.get('cleaning'):
            raise PumpBoardError('refused', T_CLEAN_NO_SINGLE_STOP)
        with self.lock:
            run = self._runs.pop(motor, None)    # stopped on purpose: its end is not "the time ran out"
            late = self._tripped['round'].pop(motor, None)
            self._tripped['manual'].pop(motor, None)
            op = self._op
            if op is not None and op.get('motor') == motor and op['kind'] in ('dose_volume', 'dose_weight'):
                op['soft_stop'] = True           # “OFF” on that row: the dose ends as stopped; nothing else is touched
                op['cancel'].set()
            elif op is not None and op['kind'] == 'dose_many' and motor in (op.get('motors') or ()):
                op['cancel'].set()               # “OFF” on one of them ends the whole of it: everything stops
        ran_out = False
        try:
            if run is not None:
                self._send(conn, '?')
                # The board had ended it before this stop came. (A run that is only just being started has
                # not run out: its motor is at rest because the move has not reached the board yet.)
                ran_out = run['confirmed'] and not self._motor_active(conn, motor)
            self._invoke(conn, 'motors.stop', {'motor': motor})          # also cancels V7's weight dose on that motor
            self._await(conn, lambda: not self._motor_active(conn, motor), STOP_IDLE_S)
            valve = self._valve_of(motor)
            if valve:
                self._await(conn, lambda: not conn.raw.state['valves'].get(valve), VALVE_CLOSE_S)
        except PumpBoardError as exc:
            if exc.code in ('refused', 'link'):
                raise
            self._stop_all('stop_motor')
            raise PumpBoardError('device', T_STOP_LATE.format(label=self._label(motor)))
        if ran_out and run['owner'] == 'manual':
            self._run_over(motor, run)           # by hand that is simply how it ends
        elif ran_out:
            late = E_DEADLINE_ROUND.format(label=self._label(motor))
        if late:
            # A round's run was over before its pump_off came: the pump ran longer than the Recipe planned.
            raise PumpBoardError('device', late)
        ran = round(time.perf_counter() - run['started'], 1) if run else None
        return {'motor': motor, 'stopped': True, 'ran_s': ran}

    def dose(self, motor, volume_ml, direction, owner, abort):
        """Dose by step count and wait until the firmware has finished (and the linked valve is closed)."""
        motor, group = self._known_motor(motor)
        label = self._label(motor)
        text = '%s %s %s mL' % (label, 'reverse quantitative' if direction == -1 else 'Quantitative', _g(volume_ml))
        with self._operation('dose_volume', owner, abort, motor=motor, text=text, direction=direction) as op:
            conn = op['conn']
            self._need_quiescent(conn)
            started = time.perf_counter()
            out = self._invoke(conn, 'motors.dose', {'motor': motor, 'volume_ml': volume_ml, 'direction': direction})
            steps, sps, eta = out['steps'], out['sps'], float(out['eta_s'])
            op['eta_s'] = eta
            slot = conn.raw.state['motors'][group['id']]
            self._wait(op, lambda: slot.get('state') == 'idle' or slot.get('selected') != motor,
                       eta * DOSE_LIMIT_FACTOR + DOSE_LIMIT_EXTRA_S, STATE_POLL_S, T_DOSE_LATE)
            elapsed = time.perf_counter() - started
            if slot.get('selected') != motor or elapsed < EARLY_FACTOR * abs(steps) / max(abs(sps), 1) / conn.k:
                raise PumpBoardError('device', T_DOSE_EARLY)
            valve = self._valve_of(motor)
            if valve:
                self._wait(op, lambda: not conn.raw.state['valves'].get(valve), VALVE_CLOSE_S, STOP_POLL_S, T_DOSE_LATE)
            op['done_text'] = '%s Quantitative completion:%s mL' % (label, _g(volume_ml))
            return {'steps': steps, 'sps': sps, 'eta_s': eta, 'elapsed_s': round(time.perf_counter() - started, 2)}

    def dose_many(self, doses, owner, abort):
        """Several volume doses as one operation (“Simultaneous Quantification”): `doses` = [(motor, volume_ml), …].

        A driver group turns one motor at a time, so the motors of different groups run together and those of
        one group one after the other, in the order given. Every dose is the firmware's own finite move (V7's
        motors.dose): the board counts the steps and stops each by itself. Waits until all have ended and
        their linked valves are closed; a stop, or “OFF” on one of them, ends all of it."""
        items, seen = [], set()
        try:
            for motor, volume in doses:
                motor, group = self._known_motor(motor)
                if motor in seen:
                    raise PumpBoardError('refused', T_DOSE_TWICE.format(label=self._label(motor)))
                seen.add(motor)
                items.append((motor, group['id'], volume))
        except (TypeError, ValueError):
            raise PumpBoardError('refused', T_DOSE_LIST)
        if not items:
            raise PumpBoardError('refused', T_DOSE_NONE)
        text = 'Simultaneous quantification:' + ', '.join('%s: %s mL' % (self._label(motor), _g(volume)) for motor, _, volume in items)
        v7 = self._v7
        with self._operation('dose_many', owner, abort, text=text) as op:
            conn = op['conn']
            self._need_quiescent(conn)
            queues, total = {}, {}
            try:
                for motor, gid, volume in items:     # V7's own limits for every one of them, before anything moves
                    values = self._coerce('motors.dose', {'motor': motor, 'volume_ml': volume, 'direction': 1})
                    steps = v7.motors.volume_to_steps(conn.cfg, values['volume_ml'], motor)
                    sps = v7.motors.flow_to_sps(conn.cfg, float(conn.cfg['motors'].get(
                        'dose_flow_ml_min', conn.cfg['motors']['default_flow_ml_min'])), motor)
                    queues.setdefault(gid, []).append((motor, values['volume_ml']))
                    total[gid] = total.get(gid, 0.0) + steps / max(sps, 1)
            except v7.FunctionError as exc:
                raise PumpBoardError('refused', self._words(exc))
            op['motors'] = [motor for motor, _, _ in items]
            op['eta_s'] = max(total.values())
            began = time.perf_counter()
            active, closing, done = {}, {}, []
            next_poll = 0.0
            while queues or active or closing:
                self._check(op)
                for gid in [gid for gid in queues if gid not in active and gid not in closing]:
                    motor, volume = queues[gid].pop(0)
                    if not queues[gid]:
                        del queues[gid]
                    out = self._invoke(conn, 'motors.dose', {'motor': motor, 'volume_ml': volume, 'direction': 1})
                    eta = float(out['eta_s'])
                    now = time.perf_counter()
                    active[gid] = {'motor': motor, 'volume_ml': volume, 'steps': out['steps'], 'sps': out['sps'],
                                   'eta_s': eta, 'started': now,
                                   'limit': now + eta * DOSE_MANY_LIMIT_FACTOR + DOSE_LIMIT_EXTRA_S}
                state, now = conn.raw.state, time.perf_counter()
                for gid, job in list(active.items()):
                    slot = state['motors'][gid]
                    if slot.get('state') == 'idle' or slot.get('selected') != job['motor']:
                        elapsed = now - job['started']
                        if (slot.get('selected') != job['motor']
                                or elapsed < EARLY_FACTOR * abs(job['steps']) / max(abs(job['sps']), 1) / conn.k):
                            raise PumpBoardError('device', T_DOSE_EARLY)
                        del active[gid]
                        job['elapsed_s'] = round(elapsed, 2)
                        valve = self._valve_of(job['motor'])
                        if valve and state['valves'].get(valve):
                            closing[gid] = dict(job, valve=valve, limit=now + VALVE_CLOSE_S)
                        else:
                            done.append(job)
                    elif now >= job['limit']:
                        raise PumpBoardError('timeout', T_DOSE_LATE)
                for gid, job in list(closing.items()):
                    if not state['valves'].get(job['valve']):
                        del closing[gid]
                        done.append(job)
                    elif now >= job['limit']:
                        raise PumpBoardError('timeout', T_DOSE_LATE)
                if not (queues or active or closing):
                    break
                if any(gid not in active and gid not in closing for gid in queues):
                    continue                         # a group has become free: its next pump starts at once
                if now >= next_poll:
                    self._send(conn, '?')
                    next_poll = time.perf_counter() + (STOP_POLL_S if closing else STATE_POLL_S)
                else:
                    time.sleep(0.02)
            order = {motor: index for index, (motor, _, _) in enumerate(items)}
            done.sort(key=lambda job: order[job['motor']])
            op['done_text'] = 'Quantitative completion:' + ', '.join('%s: %s mL' % (self._label(job['motor']), _g(job['volume_ml']))
                                                    for job in done)
            return {'doses': [{key: job[key] for key in ('motor', 'volume_ml', 'steps', 'sps', 'eta_s', 'elapsed_s')}
                              for job in done], 'elapsed_s': round(time.perf_counter() - began, 2)}

    def dose_weight(self, motor, target_g, owner, abort):
        """V7's closed loop on the scale. Blocks inside V7; a stop cancels it from another thread."""
        motor, group = self._known_motor(motor)
        label = self._label(motor)
        v7 = self._v7
        with self._operation('dose_weight', owner, abort, motor=motor, text='%s Weighing and quantitative' % label) as op:
            conn = op['conn']
            self._need_quiescent(conn)
            weighing = conn.cfg['motors'].get('gravimetric_dose') or {}
            try:
                # V7's own limits for the target, before anything is sent.
                values = self._coerce('motors.dose_by_weight', {'motor': motor, 'target_g': target_g, 'direction': 1})
            except v7.FunctionError as exc:
                raise PumpBoardError('refused', self._words(exc))
            if values['target_g'] is None:
                raise PumpBoardError('refused', T_WEIGHT_TARGET)
            if not weighing.get('enabled', False):
                raise PumpBoardError('refused', T_WEIGHT_OFF)
            sample, at = self._weight
            if sample is None or time.perf_counter() - at > WEIGHT_FRESH_S:
                raise PumpBoardError('refused', T_SCALE_NONE)
            if not self._calibration_loaded:
                # Without the saved calibration (and after every restart of the board) grams are raw counts.
                raise PumpBoardError('refused', T_NOT_CALIBRATED)
            op['target_g'] = values['target_g']
            slot = conn.raw.state['motors'][group['id']]
            valve = self._valve_of(motor)
            if conn.caps['dose']:
                return self._dose_weight_on_board(op, motor, label, float(values['target_g']), weighing, slot, valve)

            def at_rest():
                return (slot.get('state') == 'idle' and slot.get('selected') == motor
                        and not (valve and conn.raw.state['valves'].get(valve)))

            # A finite move lowers the driver's top speed until a faster one is commanded (how the firmware
            # is written; not measured). A move of zero steps at the fast weighing speed sets it for V7's `V`.
            fast = v7.motors.flow_to_sps(conn.cfg, float(weighing['fast_flow_ml_min']), motor)
            self._send(conn, 'P,%d,0,%d' % (motor, fast))
            self._send(conn, '?')
            valves = conn.cfg['valves']
            self._wait(op, at_rest, (int(valves['open_before_ms']) + int(valves['close_after_ms'])) / 1000.0
                       + STOP_IDLE_S + VALVE_CLOSE_S, STOP_POLL_S, T_MOVING)
            op['motion_mark'] = conn.checked.motion_written      # from here on "started" means V7's own dose
            sample, _ = self._weight
            op['start_g'] = float(sample['grams']) if sample and isinstance(sample.get('grams'), (int, float)) else None
            limit = WEIGHT_HARD_LIMIT_S
            timer = threading.Timer(limit, self._stop_all, args=('weight-limit',),
                                    kwargs={'why': ('timeout', T_WEIGHT_LATE.format(seconds=limit))})
            timer.daemon = True
            op['endless'] = True                 # V7 drives `V`: the one thing left that has no end of its own
            self._guard_update()
            timer.start()
            try:
                out = v7.registry.get('motors.dose_by_weight').invoke(
                    conn.ctx, {'motor': motor, 'target_g': target_g, 'direction': 1})
            except (v7.FunctionError, v7.LinkError) as exc:
                raise self._weight_failure(op, exc)
            finally:
                timer.cancel()
            self._check(op)
            self._wait(op, lambda: slot.get('state') == 'idle' and not (valve and conn.raw.state['valves'].get(valve)),
                       STOP_IDLE_S + VALVE_CLOSE_S, STOP_POLL_S, T_MOVING)
            op['done_text'] = '%s Quantitative completion:%.2f g (error %+.2f g) ' % (label, out['dispensed_g'], out['error_g'])
            return {key: out.get(key) for key in ('target_g', 'dispensed_g', 'error_g', 'within_tolerance', 'topups',
                                                    'elapsed_s', 'baseline_g', 'warning')}

    def _dose_weight_on_board(self, op, motor, label, target, weighing, slot, valve):
        """The weight dose as the firmware of mega/ runs it (GP, GN, G): the board watches the scale and stops
        the pump itself — on the target, on its time limits, when the weight stops rising, when the scale falls
        silent. This PC hands over V7's numbers, asks how it goes (G,?) and reports. Nothing here keeps the
        pump running: a PC that freezes or a cable that falls out changes nothing about when it stops."""
        conn, v7 = op['conn'], self._v7
        tolerance = target * float(weighing['tolerance_percent']) / 100.0
        coarse = float(weighing['fast_flow_ml_min'])
        approach = min(coarse, float(weighing['approach_flow_ml_min']))
        trim = min(approach, float(weighing['trim_flow_ml_min']))
        try:
            v7.motors._check_flow(conn.cfg, coarse, motor)
        except v7.FunctionError as exc:
            raise PumpBoardError('refused', self._words(exc))
        speeds = [v7.motors.flow_to_sps(conn.cfg, flow, motor) for flow in (coarse, approach, trim)]
        slow_zone = min(float(weighing['slow_zone_g']), target * 0.25)
        trim_zone = min(float(weighing['trim_zone_g']), target * 0.05)
        timeout_s, no_gain_s = float(weighing['timeout_s']), float(weighing['no_gain_timeout_s'])

        def mg(grams):
            return int(round(float(grams) * 1000))

        def ms(seconds):
            return int(round(float(seconds) * 1000))

        def count(name):
            return min(BOARD_DOSE_SAMPLES_MAX, max(1, int(weighing[name])))

        op['board_gain'] = 0.0
        # Three short lines (LINE_MAX). The first two only store numbers; the third starts the dose.
        self._send(conn, 'GP,%d,%d,%d,%d,%d,%d,%d' % (
            speeds[0], speeds[1], speeds[2], max(1, mg(weighing['minimum_gain_g'])), ms(weighing['settle_s']),
            ms(no_gain_s), ms(timeout_s)))
        self._send(conn, 'GN,%d,%d,%d' % (count('baseline_samples'), count('final_samples'),
                                          ms(weighing['scale_ready_timeout_s'])))
        # OK,dose_started — or the board's refusal, with nothing started.
        self._send(conn, 'G,%d,%d,%d,%d,%d' % (motor, mg(target), mg(slow_zone), mg(trim_zone), mg(tolerance)))
        limit = time.perf_counter() + timeout_s + BOARD_DOSE_EXTRA_S
        while True:
            self._check(op)
            fields = {}
            for line in self._send(conn, 'G,?'):
                if line.startswith('GSTAT,'):
                    fields = dict(KV.findall(line[len('GSTAT,'):]))
            try:
                op['board_gain'] = float(fields.get('gain'))
            except (TypeError, ValueError):
                pass
            if fields.get('active') == '0':
                break
            if time.perf_counter() >= limit:
                # The board's own limits should have ended it long ago.
                raise PumpBoardError('timeout', T_WEIGHT_LATE.format(seconds=timeout_s + BOARD_DOSE_EXTRA_S))
            pause = time.perf_counter() + BOARD_DOSE_POLL_S
            while time.perf_counter() < pause:
                self._check(op)
                time.sleep(0.02)
        result = fields.get('result')
        if result != 'done':
            if result == 'cancelled':
                raise PumpBoardError('stopped', T_WEIGHT_CANCELLED)
            words = {'timeout': T_WEIGHT_TIMEOUT.format(seconds=timeout_s),
                     'no_gain': T_WEIGHT_NO_GAIN.format(seconds=no_gain_s), 'scale': T_WEIGHT_NO_READING}
            raise PumpBoardError('device', words.get(result) or T_WEIGHT_UNKNOWN.format(result=result))
        try:
            dispensed = float(fields['gain'])
            baseline = float(fields.get('baseline'))
            topups = int(fields.get('topups') or 0)
            elapsed = int(fields.get('ms') or 0) / 1000.0
        except (KeyError, TypeError, ValueError):
            raise PumpBoardError('device', T_WEIGHT_UNKNOWN.format(result='done'))
        self._wait(op, lambda: slot.get('state') == 'idle' and not (valve and conn.raw.state['valves'].get(valve)),
                   STOP_IDLE_S + VALVE_CLOSE_S, STOP_POLL_S, T_MOVING)
        error = dispensed - target
        within = target - tolerance <= dispensed <= target + tolerance
        op['done_text'] = '%s Quantitative completion:%.2f g (error %+.2f g) ' % (label, dispensed, error)
        return {'target_g': round(target, 4), 'dispensed_g': round(dispensed, 4), 'error_g': round(error, 4),
                'within_tolerance': within, 'topups': topups, 'elapsed_s': round(elapsed, 2),
                'baseline_g': round(baseline, 4), 'warning': None if within else W_WEIGHT_OUTSIDE}

    def clean(self, owner, abort):
        """The firmware's own rinse; waits until the firmware reports it over."""
        with self._operation('clean', owner, abort, text='automatic cleaning') as op:
            conn = op['conn']
            self._need_quiescent(conn)
            started = time.perf_counter()
            out = self._invoke(conn, 'motors.clean', {})
            if not out.get('started'):
                raise PumpBoardError('device', T_CLEAN_NOT_STARTED)
            valves = conn.cfg['valves']
            total = float(out['total_pump_timeline_s'])
            op['eta_s'] = total + (int(valves['open_before_ms']) + int(valves['close_after_ms'])) / 1000.0
            state = conn.raw.state
            self._wait(op, lambda: self._all_idle(state), op['eta_s'] + CLEAN_EXTRA_S, STATE_POLL_S, T_CLEAN_LATE)
            op['done_text'] = 'Automatic cleaning completed'
            return {'started': True, 'total_pump_timeline_s': total, 'elapsed_s': round(time.perf_counter() - started, 2)}

    def syringe_init(self, owner, abort):
        """Home the plunger, then apply the saved speed. Never done by the program on its own."""
        with self._operation('syringe_init', owner, abort, text='Syringe pump initializing') as op:
            conn = op['conn']
            self._need_quiescent(conn)
            speed = float(self._settings['syringe_speed_ml_s'])
            with self.lock:
                op['syringe_before'] = dict(self._syringe)
                self._syringe.update(busy=True, initialised=False, volume_ul=None, position=None, error=None)
            self._invoke(conn, 'pump.init', {})
            self._syringe_ready(op, SYRINGE_INIT_LIMIT_S, 0.0, T_SYRINGE_INIT_LATE.format(seconds=SYRINGE_INIT_LIMIT_S))
            self._invoke(conn, 'pump.set_speed', {'speed_ml_s': speed})
            with self.lock:
                self._syringe.update(busy=False, initialised=True, volume_ul=0, position=None, speed_ml_s=speed, error=None)
            self._clear_alarm(A_SYRINGE)
            op['done_text'] = 'Syringe pump initialization completed'
            return {'initialised': True, 'speed_ml_s': speed}

    def syringe_speed(self, speed_ml_s):
        """Speed of the following syringe moves (the saved speed comes back with the next initialise)."""
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)
        try:
            conn = self._session()
            with self.lock:
                initialised, busy = self._syringe['initialised'], self._syringe['busy']
            if not initialised:
                raise PumpBoardError('refused', T_SYRINGE_NOT_INIT)
            if busy:
                raise PumpBoardError('refused', T_SYRINGE_MOVING)
            top = self._max_speed(int(self._settings['syringe_ul']))
            try:
                speed = float(speed_ml_s)
            except (TypeError, ValueError):
                raise PumpBoardError('refused', T_SYRINGE_SPEED.format(top=top))
            if not 0 < speed <= top:
                raise PumpBoardError('refused', T_SYRINGE_SPEED.format(top=top))
            self._invoke(conn, 'pump.set_speed', {'speed_ml_s': speed})
            with self.lock:
                self._syringe['speed_ml_s'] = speed
            return {'speed_ml_s': speed}
        finally:
            self.op_lock.release()

    def syringe_move(self, kind, position, volume_ml, owner, abort):
        """Turn the head valve when needed, then draw in ('aspirate') or push out ('dispense')."""
        if kind not in ('aspirate', 'dispense'):
            raise ValueError("kind must be 'aspirate' or 'dispense'")
        hardware = self.hardware() or {}
        top = int((hardware.get('syringe') or {}).get('valve_positions') or 0)
        if isinstance(position, bool) or not isinstance(position, int) or not 1 <= position <= top:
            raise PumpBoardError('refused', T_SYRINGE_POSITION.format(top=top))
        try:
            microlitres = int(round(float(volume_ml) * 1000))
        except (TypeError, ValueError):
            microlitres = 0
        if microlitres < 1:
            raise PumpBoardError('refused', T_SYRINGE_VOLUME)
        word = 'Liquid extraction' if kind == 'aspirate' else 'Liquid injection'
        text = 'syringe pump %s Valve position %d · %s mL' % (word, position, _ml(microlitres))
        with self._operation('syringe_move', owner, abort, text=text) as op:
            conn = op['conn']
            with self.lock:
                syringe = dict(self._syringe)
                size = int(self._settings['syringe_ul'])
            if not syringe['initialised'] or syringe['volume_ul'] is None:
                raise PumpBoardError('refused', T_SYRINGE_NOT_INIT)
            self._need_quiescent(conn)
            have = int(syringe['volume_ul'])
            if kind == 'aspirate' and have + microlitres > size:
                raise PumpBoardError('refused', T_SYRINGE_FULL.format(have=_ml(have), size=_ml(size)))
            if kind == 'dispense' and microlitres > have:
                raise PumpBoardError('refused', T_SYRINGE_SHORT.format(have=_ml(have), want=_ml(microlitres)))
            speed = float(syringe['speed_ml_s'])
            nominal = microlitres / 1000.0 / speed
            op['eta_s'] = round(nominal, 1)
            with self.lock:
                op['syringe_before'] = dict(self._syringe)
                self._syringe['busy'] = True
            if position != syringe['position']:
                self._invoke(conn, 'pump.set_valve', {'position': position})
                with self.lock:
                    self._syringe['position'] = None     # unknown while the valve turns
                self._syringe_ready(op, SYRINGE_VALVE_LIMIT_S, 0.0, T_SYRINGE_VALVE_LATE.format(seconds=SYRINGE_VALVE_LIMIT_S))
                with self.lock:
                    self._syringe['position'] = position
            self._invoke(conn, 'pump.aspirate' if kind == 'aspirate' else 'pump.dispense', {'volume_ul': microlitres})
            with self.lock:
                self._syringe['volume_ul'] = None        # unknown while the plunger moves
            self._syringe_ready(op, nominal * SYRINGE_MOVE_FACTOR + SYRINGE_MOVE_EXTRA_S, EARLY_FACTOR * nominal / conn.k,
                                T_SYRINGE_MOVE_LATE)
            now_in = have + microlitres if kind == 'aspirate' else have - microlitres
            with self.lock:
                self._syringe.update(busy=False, volume_ul=now_in)
            op['done_text'] = 'syringe pump%sComplete:%s mL' % (word, _ml(microlitres))
            return {'position': position, 'volume_ul': microlitres, 'speed_ml_s': speed, 'contents_ul': now_in}

    def scale_read(self, samples=5):
        """Median of that many new readings. Sends nothing."""
        conn = self._session()
        sample, at = self._weight
        if sample is None or time.perf_counter() - at > WEIGHT_FRESH_S:
            raise PumpBoardError('refused', T_SCALE_NONE)
        last = int(sample.get('sequence') or 0)
        values, stable = [], True
        limit = time.perf_counter() + SCALE_READ_LIMIT_S
        while len(values) < int(samples):
            if self._conn is not conn or conn.checked.dead:
                raise PumpBoardError('link', T_NOT_CONNECTED.format(text=self._text))
            sample, at = self._weight
            sequence = int(sample.get('sequence') or 0) if sample else 0
            grams = sample.get('grams') if sample else None
            if sequence > last and isinstance(grams, (int, float)) and math.isfinite(float(grams)):
                last = sequence
                values.append(float(grams))
                stable = stable and bool(sample.get('stable'))
            elif time.perf_counter() > limit:
                raise PumpBoardError('device', T_SCALE_NONE)
            else:
                time.sleep(0.01)
        return {'grams': round(statistics.median(values), 4), 'samples': len(values), 'stable': stable}

    def scale_tare(self):
        """Empty cup returns to zero. V7 writes the result into <data>/pumpboard/scale_calibration.json."""
        return self._calibrate('scale.tare', {})

    def scale_calibrate(self, grams):
        """Correction with a known weight on the scale."""
        try:
            grams = float(grams)
        except (TypeError, ValueError):
            raise PumpBoardError('refused', T_SCALE_GRAMS)
        if not 0.1 <= grams <= 20000:
            raise PumpBoardError('refused', T_SCALE_GRAMS)
        return self._calibrate('scale.calibrate', {'grams': grams})

    def stop_all(self, reason):
        """Everything off, now. Never waits for a running operation. -> {sent, confirmed, error}"""
        return self._stop_all(str(reason))

    # ============================================================================================
    # Operations: the shared machinery
    # ============================================================================================
    @staticmethod
    def _check_owner(owner):
        if owner not in OWNERS:
            raise ValueError("owner must be 'round' or 'manual'")

    def _session(self, stopping=False):
        with self.lock:
            conn = self._conn
            if self._link != 'connected' or conn is None or not conn.live or conn.checked.dead:
                raise PumpBoardError('link', T_NOT_CONNECTED.format(text=self._text))
            if self._uncertain and not stopping:
                raise PumpBoardError('refused', T_UNCERTAIN)
            return conn

    @contextlib.contextmanager
    def _operation(self, kind, owner, abort, motor=None, text='', direction=1):
        """One blocking operation on the caller's thread: it holds op_lock and is visible as state.pump.op."""
        self._check_owner(owner)
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)
        op = None
        try:
            conn = self._session()
            op = {'id': uuid.uuid4().hex, 'kind': kind, 'motor': motor, 'by': owner, 'text': text, 'direction': direction,
                  'started_at': now_local(), 't0': time.perf_counter(), 'eta_s': None, 'conn': conn,
                  'motion_mark': conn.checked.motion_written,
                  'abort': abort if abort is not None else threading.Event(), 'cancel': threading.Event(),
                  'fault': None, 'monitor': False, 'soft_stop': False, 'endless': False, 'done_text': '',
                  'syringe_before': None, 'target_g': None, 'start_g': None}
            with self.lock:
                self._op = op
            try:
                if op['abort'].is_set():
                    raise PumpBoardError('stopped', T_STOPPED)   # STOP was pressed before this began: nothing is started
                yield op
            except PumpBoardError as exc:
                self._operation_failed(op, exc)
                raise
            self._result(op, True, op['done_text'])
        finally:
            with self.lock:
                if op is not None and self._op is op:
                    self._op = None
            self.op_lock.release()
            self._guard_update()

    @staticmethod
    def _moved(op):
        """Did this operation put a motion command on the wire?"""
        return op['conn'].checked.motion_written > op['motion_mark']

    def _operation_failed(self, op, exc):
        """An operation that has started something and fails leaves nothing running."""
        conn = op['conn']
        moved = self._moved(op)
        if moved and self._alive(conn) and not op['soft_stop'] and not op['monitor']:
            self._stop_all(op['kind'])           # X, and Y,STOP while the syringe still counts as busy
        if op['syringe_before'] is not None:
            with self.lock:
                if moved:
                    self._syringe.update(busy=False, initialised=False, volume_ul=None)
                else:
                    self._syringe.update(op['syringe_before'])     # nothing was sent: as it was
        self._result(op, False, exc.text, stopped=exc.code == 'stopped')

    def _result(self, op, ok, text, stopped=False):
        if op['by'] != 'manual':
            return
        if not ok:
            text = T_OP_STOPPED.format(text=op['text']) if stopped else T_FAILED.format(text=text)
        with self.lock:
            self._last_result = {'time': now_local(), 'ok': bool(ok), 'text': text}

    def _check(self, op):
        """Called at least every 0.1 s inside every wait: STOP, a stop-all, a lost link, a board restart."""
        conn = op['conn']
        if op['abort'].is_set():
            raise PumpBoardError('stopped', T_STOPPED)
        if op['fault'] is not None:
            raise PumpBoardError(*op['fault'])
        if op['cancel'].is_set():
            raise PumpBoardError('stopped', T_STOPPED)
        if conn.raw.state.get('firmware') is not None:
            op['monitor'] = True                 # the monitor thread repeats the safe-state sequence
            raise PumpBoardError('device', T_RESET)
        if self._conn is not conn or conn.checked.dead or self._link != 'connected':
            op['monitor'] = True
            raise PumpBoardError('link', T_LOST)

    def _wait(self, op, done, limit_s, poll_s, late_text):
        """Wait for `done()`. The state follows the board's own STATE lines; `?` is only the fallback."""
        conn = op['conn']
        end = time.perf_counter() + limit_s
        next_poll = time.perf_counter() + min(poll_s, 0.2)
        while True:
            self._check(op)
            if done():
                return
            now = time.perf_counter()
            if now >= end:
                raise PumpBoardError('timeout', late_text)
            if now >= next_poll:
                self._send(conn, '?')
                next_poll = time.perf_counter() + poll_s
                continue
            time.sleep(0.02)

    def _await(self, conn, done, limit_s):
        """The same for the stop paths, which have no operation: poll `?` until `done()`."""
        end = time.perf_counter() + limit_s
        next_poll = 0.0
        while not done():
            if self._conn is not conn or conn.checked.dead:
                raise PumpBoardError('link', T_LOST)
            now = time.perf_counter()
            if now >= end:
                raise PumpBoardError('timeout', T_MOVING)
            if now >= next_poll:
                self._send(conn, '?')
                next_poll = time.perf_counter() + STOP_POLL_S
                continue
            time.sleep(0.02)

    def _syringe_ready(self, op, limit_s, least_s, late_text):
        """Poll Y,ST until the syringe pump reports ready. `least_s`: how the pump shows a stroke in
        progress is not known, so a move does not count as finished before most of its time has passed."""
        conn = op['conn']
        started = time.perf_counter()
        while True:
            self._check(op)
            lines = self._send(conn, 'Y,ST')
            ready = any(dict(KV.findall(line)).get('ready') == '1' for line in lines if line.startswith('PUMP,'))
            elapsed = time.perf_counter() - started
            if ready and elapsed >= least_s:
                return
            if elapsed >= limit_s:
                raise PumpBoardError('timeout', late_text)
            pause = time.perf_counter() + min(SYRINGE_POLL_S, max(0.02, (least_s if ready else limit_s) - elapsed))
            while time.perf_counter() < pause:
                self._check(op)
                time.sleep(0.02)

    def _weight_failure(self, op, exc):
        """What V7's weight dose ended with, as the wrapper's error."""
        conn = op['conn']
        if self._conn is not conn or conn.checked.dead or not conn.raw.connected:
            # V7 says "pump stopped" although its stop could not be sent.
            op['monitor'] = True
            with self.lock:
                self._uncertain = True
                self._endless_risk = True
            return PumpBoardError('link', T_LOST)
        if op['fault'] is not None:
            return PumpBoardError(*op['fault'])
        if conn.raw.state.get('firmware') is not None:
            op['monitor'] = True
            return PumpBoardError('device', T_RESET)
        if isinstance(exc, self._v7.LinkTrouble):
            return PumpBoardError(exc.code, exc.text)
        if op['abort'].is_set() or op['cancel'].is_set():
            return PumpBoardError('stopped', self._words(exc))
        if isinstance(exc, self._v7.FunctionError) and not self._moved(op):
            return PumpBoardError('refused', self._words(exc))       # V7 said no before anything was started
        return PumpBoardError('device', self._words(exc))

    def _calibrate(self, name, payload):
        if not self.op_lock.acquire(blocking=False):
            raise PumpBoardError('busy', T_BUSY)
        try:
            conn = self._session()
            ok, reasons = self._quiescent(conn, fresh=True, in_operation=True)
            if not ok:
                if reasons[0]['code'] == 'pump_link':
                    raise PumpBoardError('link', reasons[0]['text'])
                raise PumpBoardError('blocked', T_SCALE_MOVING)
            sample, at = self._weight
            if sample is None or time.perf_counter() - at > WEIGHT_FRESH_S:
                raise PumpBoardError('blocked', T_SCALE_NONE)
            if not sample.get('stable'):
                # The firmware takes one single reading, without any check of its own.
                raise PumpBoardError('blocked', T_SCALE_UNSTABLE)
            out = self._invoke(conn, name, payload)
            saved = out.get('calibration') or {}
            calibration = {key: saved.get(key) for key in ('divisor', 'offset', 'reference_grams', 'saved_at')}
            with self.lock:
                self._calibration_loaded = True
                self._calibration = calibration
            return {'calibration': dict(calibration)}
        finally:
            self.op_lock.release()

    def _coerce(self, name, payload):
        """V7's own checking of a function's parameters: what Function.invoke does before it calls the
        handler (types, ranges, choices, defaults). Raises V7's FunctionError with V7's text."""
        function = self._v7.registry.get(name)
        values = {}
        for param in function.params:
            if payload.get(param.name) is not None:
                values[param.name] = param.coerce(payload[param.name])
            elif param.default is not None:
                values[param.name] = param.default
            elif param.required:
                raise self._v7.FunctionError('Missing necessary parameters:%s' % param.name)
            else:
                values[param.name] = None
        return values

    def _words(self, exc):
        """V7's sentence with its “motor 8” (the pin) replaced by the name of the screen (“Pump 7 · OUT”)."""
        return re.sub(r'motor ?(\d+)', lambda found: self._label(int(found.group(1))), str(exc))

    def _invoke(self, conn, name, payload):
        """One V7 function through the checked link, with V7's errors turned into ours."""
        try:
            return self._v7.registry.get(name).invoke(conn.ctx, payload)
        except self._v7.FunctionError as exc:
            raise PumpBoardError('refused', self._words(exc))
        except self._v7.LinkTrouble as exc:
            raise PumpBoardError(exc.code, exc.text)
        except self._v7.LinkError as exc:
            raise PumpBoardError('link', str(exc))

    def _send(self, conn, command):
        try:
            return conn.checked.send(command)
        except self._v7.LinkTrouble as exc:
            raise PumpBoardError(exc.code, exc.text)
        except self._v7.LinkError as exc:
            raise PumpBoardError('link', str(exc))

    def _need_quiescent(self, conn):
        ok, reasons = self._quiescent(conn, fresh=True, in_operation=True)
        if not ok:
            raise PumpBoardError('link' if reasons[0]['code'] == 'pump_link' else 'refused', reasons[0]['text'])

    def _quiescent(self, conn, fresh, in_operation):
        if self._uncertain:
            return False, [self._reason('pump_uncertain', T_UNCERTAIN)]
        if fresh:
            try:
                conn.checked.send('?')
            except self._v7.LinkError:
                return False, [self._reason('pump_link', T_NOT_CONNECTED.format(text=self._text), 'examine USB Wire')]
        state = conn.raw.state
        reasons = []
        if state.get('cleaning'):
            reasons.append(self._reason('cleaning', T_CLEANING, 'Wait for it to end, or press ”Stop” at the bottom STOP”'))
        self._reap_runs(conn)                    # a timed run the board has just finished is not "moving" any more
        with self.lock:
            busy = bool(self._runs) or (not in_operation and (self._op is not None or self.op_lock.locked()))
            syringe = self._syringe['busy']
        if busy or any(slot.get('state') != 'idle' for slot in state['motors'].values()) or any(state['valves'].values()):
            reasons.append(self._reason('pump_moving', T_MOVING))
        if syringe:
            reasons.append(self._reason('syringe_not_ready', T_SYRINGE_MOVING))
        return not reasons, reasons

    @staticmethod
    def _reason(code, text, fix=''):
        return {'code': code, 'text': text, 'fix': fix}

    @staticmethod
    def _all_idle(state):
        return (not state.get('cleaning') and all(slot.get('state') == 'idle' for slot in state['motors'].values())
                and not any(state['valves'].values()))

    @classmethod
    def _all_off(cls, state):
        return cls._all_idle(state) and all(slot.get('selected') is None for slot in state['motors'].values())

    def _motor_active(self, conn, motor):
        slot = conn.raw.state['motors'][self._group_of(motor)]
        return slot.get('selected') == motor and slot.get('state') != 'idle'

    def _alive(self, conn):
        """Is this still the connection in use, and can commands be sent on it?"""
        return self._conn is conn and conn.live and not conn.checked.dead and self._link == 'connected'

    # ============================================================================================
    # Stop all (SPEC B.3.9)
    # ============================================================================================
    def _stop_all(self, reason, why=None, reconnect=True):
        report = {'sent': False, 'confirmed': False, 'error': ''}
        if self.mode == 'off' or self._vendor_error or self._v7 is None:
            report['error'] = T_NOT_CONNECTED.format(text=self._text)
            return report
        with self.lock:
            op = self._op
            endless = op is not None and op['endless']
            if op is not None:
                if why is not None and op['fault'] is None:
                    op['fault'] = why
                op['cancel'].set()               # the running operation ends; its own thread reports that
            self._runs.clear()
            for flags in self._tripped.values():
                flags.clear()
            conn = self._conn if self._link == 'connected' and self._conn is not None and self._conn.live else None
            generation = self._generation
            uncertain = self._uncertain
        if conn is not None:
            with self._stop_lock:
                self._stop_connected(conn, report)
            if not report['confirmed'] and reason != 'exit' and not self._closing.is_set():
                # Nobody knows what the board is doing now: reopening it is the only way left to stop a pump.
                with self.lock:
                    self._uncertain = True
                    self._endless_risk = self._endless_risk or endless
                self._link_lost(conn.checked, report['error'] or 'stop not confirmed', quiet=True)
        else:
            report['error'] = T_NOT_CONNECTED.format(text=self._text)
        if not report['confirmed'] and reconnect and self.in_use and not self._closing.is_set():
            if self._reconnect_once(generation):
                report.update(sent=True, confirmed=True, error='')      # the new connection began with X
        self._guard_update()
        if not report['confirmed'] and (self.in_use or uncertain):
            self._alarm(A_STOP)
        return report

    def _stop_connected(self, conn, report):
        v7, state = self._v7, conn.raw.state
        with self.lock:
            syringe = dict(self._syringe)
        written = conn.checked.x_written
        syringe_stopped = True
        try:
            v7.registry.get('motors.stop_all').invoke(conn.ctx, {})     # V7's cancel flag, X, W,STREAM,0, ?
            if syringe['busy'] or syringe['initialised']:
                try:
                    v7.registry.get('pump.stop').invoke(conn.ctx, {})   # X only fires the stop; this one is answered
                except v7.LinkTrouble as exc:
                    if exc.code == 'link':
                        raise
                    syringe_stopped = False
                    report['error'] = exc.text
            conn.checked.send('?')
            report['confirmed'] = self._all_off(state) and (syringe_stopped or not syringe['busy'])
            if not report['confirmed'] and not report['error']:
                report['error'] = T_MOVING
        except (v7.LinkError, v7.FunctionError) as exc:
            report['error'] = getattr(exc, 'text', None) or str(exc)
        report['sent'] = conn.checked.x_written > written
        with self.lock:
            if report['sent']:
                self._syringe.update(initialised=False, volume_ul=None, busy=False)
                self._scale_restart_at = time.perf_counter() + SCALE_RESTART_AFTER_STOP_S     # X switched the stream off

    def _reconnect_once(self, generation):
        """With the link down, one immediate attempt: the safe-state sequence of a new connection sends X."""
        if self._hold or threading.current_thread() is self._thread:
            return False
        with self.lock:
            # An attempt that is already under way does not count as the one asked for here.
            enough = self._attempts_done + (2 if self._attempting else 1)
            self._immediate = True
        self._kick.set()
        limit = time.perf_counter() + RECONNECT_BUDGET_S
        with self._changed:
            while True:
                if self._generation != generation and self._link == 'connected':
                    return True
                left = limit - time.perf_counter()
                if left <= 0 or self._closing.is_set() or self._attempts_done >= enough:
                    return False             # that one attempt is over and the board is not there
                self._changed.wait(min(left, 0.1))

    # ============================================================================================
    # What CheckedLink reports
    # ============================================================================================
    def _group_ids(self):
        return [group['id'] for group in (self._hardware or {}).get('groups', [])]

    def _group_of(self, motor):
        for group in (self._hardware or {}).get('groups', []):
            if any(row['id'] == motor for row in group['motors']):
                return group['id']
        return None

    def _known_motor(self, motor):
        self.hardware()
        if not isinstance(motor, bool) and isinstance(motor, int):
            for group in (self._hardware or {}).get('groups', []):
                if any(row['id'] == motor for row in group['motors']):
                    return motor, group
        raise PumpBoardError('refused', T_NO_MOTOR.format(motor=motor))

    def _label(self, motor):
        for group in (self._hardware or {}).get('groups', []):
            for row in group['motors']:
                if row['id'] == motor:
                    return row['label']
        return 'pump (%s) ' % motor

    def _valve_of(self, motor):
        for group in (self._hardware or {}).get('groups', []):
            for row in group['motors']:
                if row['id'] == motor:
                    return row['valve']
        return None

    def _max_speed(self, syringe_ul):
        stroke = float(self._facts.get('full_stroke_steps') or 12000)
        return round(syringe_ul / 1000.0 * 6000.0 / stroke, 3)

    def _syringe_seen(self, error):
        with self.lock:
            self._syringe['online'] = True
            if error:
                self._syringe['error'] = error       # stays until an initialise has succeeded

    def _syringe_silent(self):
        with self.lock:
            self._syringe['online'] = False

    def _syringe_fault(self, number, text):
        key, priority, area, kind, line = A_SYRINGE
        line = line.format(number=number)
        if number in SYRINGE_ERRORS:
            line += ' (%s) ' % text
        self._alarm((key, priority, area, kind, line))

    def _motion_unanswered(self, checked, command):
        """A motion command was written and never answered: stop, and take nothing for granted until the
        board has been opened again."""
        with self.lock:
            conn = self._conn
            if conn is None or conn.checked is not checked or not conn.live:
                return
            self._uncertain = True
        self._stop_all('unanswered', why=('timeout', T_NO_REPLY.format(command=command)), reconnect=False)
        self._link_lost(checked, 'no reply to %s' % command, quiet=True)

    def _link_lost(self, checked, detail, quiet=False):
        """The link is given up (any thread). The monitor closes it and reconnects at once."""
        with self.lock:
            conn = self._conn
            if conn is None or conn.checked is not checked or checked.dead:
                return
            checked.dead = True
            if not conn.live:
                return                           # still connecting: the attempt itself reports the failure
            op = self._op
            moving = (bool(self._runs) or self._syringe['busy'] or not self._all_idle(conn.raw.state)
                      or (op is not None and self._moved(op)))
            uncertain = moving or checked.pending_motion or checked.pending_stop
            important = any(run['owner'] == 'round' for run in self._runs.values()) or (op is not None and op['by'] == 'round')
            if op is not None:
                if op['fault'] is None:
                    op['fault'] = ('link', T_LOST)
                op['monitor'] = True
                op['cancel'].set()
                if op['endless']:
                    self._endless_risk = True
            self._runs.clear()
            self._syringe.update(busy=False, initialised=False, volume_ul=None, position=None)
            if uncertain:
                self._uncertain = True
            self._immediate = True
            self._tries = 0
            self._link, self._text, self._error, self._since = 'lost', LINK_TEXT['lost'], str(detail)[:200], now_local()
            self._changed.notify_all()
        cancel = getattr(self._v7.motors, '_cancel_weight_dose', None)
        if cancel is not None:
            cancel()                             # a weight dose inside V7 must not sit out its 4 s for a reading
        if uncertain and not quiet:
            self._alarm(A_LOST_MOVING)
        else:
            key, priority, area, kind, text = A_LINK_LOST
            self._lost_alarm = True
            self._alarm((key, 2 if important else priority, area, kind, text))
        self._guard_update()
        self._kick.set()

    # ============================================================================================
    # The monitor thread (SPEC B.3.7)
    # ============================================================================================
    def _monitor(self):
        while not self._closing.is_set():
            try:
                self._tick()
            except Exception as exc:             # this thread must survive anything
                text = '%s: %s' % (type(exc).__name__, exc)
                if text != self._trouble:
                    self._trouble = text
                    self._event(E_MONITOR.format(detail=text[:200]), 'error')
            self._kick.wait(MONITOR_S)
            self._kick.clear()

    def _tick(self):
        if self._reconnect_wanted:
            self._serve_reconnect()
        with self.lock:
            conn = self._conn
            connected = self._link == 'connected' and conn is not None and conn.live
        if connected:
            self._watch(conn)
        else:
            self._connect_step()
        self._flush_serial_log()

    def _request_reconnect(self):
        with self.lock:
            self._reconnect_wanted = True
        self._kick.set()

    def _serve_reconnect(self):
        with self.lock:
            self._reconnect_wanted = False
            conn = self._conn if self._link == 'connected' else None
        if conn is not None:
            self._stop_all('reconnect', reconnect=False)
        self._drop_connection()
        with self.lock:
            self._hold = False
            self._immediate = True
            self._tries = 0
            if self.mode == 'sim' or self._settings.get('enabled') is True:
                self._link, self._text, self._error, self._since = 'connecting', LINK_TEXT['connecting'], '', now_local()
            self._changed.notify_all()

    def _drop_connection(self):
        with self.lock:
            conn, self._conn = self._conn, None
        if conn is None:
            return
        conn.checked.dead = True
        self._flush_serial_log(conn)
        try:
            conn.raw.stop()                      # V7's stop sends nothing to the board
        except Exception:
            pass

    def _idle_state(self, adopt=False):
        """Real mode with nothing to connect to. True when that is the case.

        Nobody has to choose a port. While no board has been set, the one Arduino Mega of this PC is taken
        by itself (`adopt`; found by its USB ids — looking opens nothing). “does not use the pump control board” stays what it is."""
        enabled = self._settings.get('enabled')
        if enabled is True:
            return False
        text = LINK_TEXT['unset' if enabled is None else 'disabled']
        if enabled is None and adopt:
            found = self._adopt()
            if found is True:
                return False
            if found == 'many':
                text = LINK_TEXT['unset_many']
            elif found is None:
                text = self._text if self._link == 'unset' else text     # not looked this time: as it was
        state = 'unset' if enabled is None else 'disabled'
        if self._link != state or self._text != text:
            self._set_link(state, text)
        return True

    def _can_open(self):
        """Could this process build a link to a real port at all? (Else nothing is set by itself either.)"""
        return self._link_factory is not None or (_real_ports_allowed and not no_serial())

    def _adopt(self, replaced=False):
        """Take the only Arduino Mega of this PC as the pump board. True: taken (the settings are written).
        'none' / 'many': how many there are instead. None: not looked this time (the list is read every RELIST_S)."""
        now = time.perf_counter()
        if not self._can_open() or (self._adopt_checked is not None and now - self._adopt_checked < RELIST_S):
            return None
        self._adopt_checked = now
        try:
            megas = [row for row in self._list_ports() if row['is_mega']]
        except Exception:
            return None
        if len(megas) != 1:
            return 'many' if megas else 'none'
        row = megas[0]
        with self.lock:
            settings = dict(self._settings)
        settings.update(enabled=True, device=row['device'], vid=row['vid'], pid=row['pid'],
                        serial_number=row['serial_number'], description=row['description'], chosen_at=now_local(),
                        chosen_by='auto')
        self._write_settings(settings)
        self._event((E_REPLACED if replaced else E_ADOPTED).format(device=row['device'], description=row['description']),
                    'warn' if replaced else 'info')
        return True

    def _connect_step(self):
        if self._conn is not None:
            self._drop_connection()
        if self.mode == 'real' and self._idle_state(adopt=True):
            return
        now = time.perf_counter()
        with self.lock:
            immediate, self._immediate = self._immediate, False
        if self._hold and not immediate:
            if self._link == 'wrong_device' and now - self._hold_checked >= RELIST_S:
                self._hold_checked = now
                # Not tried again by itself (every open restarts the device) until the port has gone and come back.
                if not self._matching_ports():
                    self._hold = False
            return
        if not immediate and now < self._next_try:
            return
        self._hold = False
        with self.lock:
            self._attempting = True
        try:
            self._attempt()
        finally:
            with self.lock:
                self._attempting = False
                self._attempts_done += 1
                self._changed.notify_all()

    def _backoff(self):
        self._next_try = time.perf_counter() + RETRY_S[min(self._tries, len(RETRY_S) - 1)]
        self._tries += 1

    def _attempt(self):
        v7 = self._v7
        device = SIM_DEVICE
        if self.mode == 'real':
            try:
                row = self._find_port()
            except Exception as exc:
                self._set_link('error', LINK_TEXT['list_failed'].format(detail=exc), str(exc))
                row = None
            if row is None:
                self._next_try = time.perf_counter() + RELIST_S
                return False
            device = row['device']
        shown = SIM_NAME if self.mode == 'sim' else device
        self._set_link('connecting', LINK_TEXT['connecting'])
        self._attempts += 1
        try:
            raw, cfg = self._new_link(device)
        except RealPortForbidden as exc:
            self._hold = True
            self._set_link('forbidden', LINK_TEXT['forbidden'], str(exc))
            return False
        except VendorIncomplete as exc:
            self._hold = True
            self._set_link('error', str(exc), str(exc))
            return False
        try:
            raw.start()                          # real: V7 opens at 115200, sleeps 2.5 s, starts its reader, sends `?`
        except v7.LinkError as exc:
            self._open_failed(shown, exc)
            return False
        if self._closing.is_set():
            raw.stop()
            return False
        banner_seen = raw.state.get('firmware') is not None
        fields = self._identify(raw)
        if fields is None:
            try:
                raw.stop()
            except Exception:
                pass
            text = LINK_TEXT['wrong_device'].format(device=shown)
            with self.lock:
                self._firmware_ok = False
            if self.mode == 'real':
                self._hold, self._hold_checked = True, time.perf_counter()
            else:
                self._backoff()
            self._set_link('wrong_device', text, text)
            key, priority, area, kind, _ = A_WRONG_DEVICE
            self._alarm((key, priority, area, kind, text))
            return False
        # From here on a READY line means "the board restarted": the detector is armed before anything is commanded.
        banner, raw.state['firmware'] = raw.state.get('firmware'), None
        with self.lock:
            self._generation += 1
            conn = _Connection(self, raw, cfg, shown, self._generation)
            conn.caps = self._caps(fields)
            self._conn = conn
            self._weight = (None, 0.0)
        try:
            for _ in range(3):
                self._safe_state(conn)
                if raw.state.get('firmware') is None:
                    break
                banner, raw.state['firmware'] = raw.state.get('firmware'), None    # it restarted meanwhile: once more
            else:
                raise v7.LinkTrouble('device', T_RESET)
        except _Unverified:
            self._drop_connection()
            self._set_link('error', LINK_TEXT['not_stopped'], LINK_TEXT['not_stopped'])
            self._alarm(A_STOP)
            self._backoff()
            return False
        except (v7.LinkError, v7.FunctionError) as exc:
            detail = getattr(exc, 'text', None) or str(exc)
            self._drop_connection()
            self._set_link('error', LINK_TEXT['bad_start'].format(detail=detail), detail)
            self._backoff()
            return False
        match = self._fingerprint(fields, cfg)
        with self.lock:
            conn.live = True
            conn.since = time.perf_counter()
            self._banner, self._banner_seen = banner, banner_seen
            self._firmware_ok, self._firmware_match = True, match
            self._uncertain = False
            self._endless_risk = False
            self._was_connected = True
            self._lost_alarm = False
            self._tries = 0
            self._runs.clear()
        text = LINK_TEXT['sim'] if self.mode == 'sim' else LINK_TEXT['connected'].format(device=shown)
        self._set_link('connected', text)
        self._clear_alarm(A_LINK_LOST)
        self._clear_alarm(A_STOP)
        self._clear_alarm(A_WRONG_DEVICE)
        if not match:
            self._alarm(A_FIRMWARE)
        self._guard_port(shown)
        self._guard_update()
        self._log_line('-- connected %s banner_seen=%s firmware_match=%s banner=%s' % (shown, banner_seen, match, banner))
        self._event(E_CONNECTED.format(device=shown), 'info')
        return True

    def _open_failed(self, shown, exc):
        text = str(exc)
        if isinstance(exc.__cause__, RealPortForbidden):
            self._hold = True
            self._set_link('forbidden', LINK_TEXT['forbidden'], text)
            return
        if self.mode == 'sim':
            self._set_link('error', LINK_TEXT['sim_failed'].format(detail=text), text)
        elif self._looks_busy(text):
            if time.perf_counter() - self._born < STARTUP_BUSY_GRACE_S:
                self._set_link('connecting', LINK_TEXT['connecting'], text)
            else:
                self._set_link('port_busy', LINK_TEXT['port_busy'].format(device=shown), text)
        else:
            self._set_link('error', LINK_TEXT['open_failed'].format(device=shown, detail=text), text)
        self._backoff()

    @staticmethod
    def _looks_busy(text):
        """Windows answers "access denied" (error 5) when another program has the port."""
        return (('PermissionError' in text and (', 5)' in text or 'Access denied When' in text))
                or 'Access denied' in text or 'Access is denied' in text)

    def _new_link(self, device):
        """A fresh V7 config and a fresh V7 Link for one connection; the cached config never keeps a port."""
        v7 = self._v7
        with _v7_lock:
            try:
                cfg = v7.config.get_config(reload=True)
                if self.mode == 'sim':
                    patch = {'port': SIM_DEVICE, 'simulate': True, 'simulation_speedup': 1, 'boot_delay_s': 0}
                else:
                    patch = dict(REAL_LINK, port=device)
                cfg['link'] = dict(cfg['link'], **patch)
                if self._overrides is not None:
                    self._overrides(cfg)
                self._apply_pump_calibration(cfg)
                self._apply_setup(cfg, False)    # what a board holds after its restart; _safe_state tells it the rest
                simulated = bool(cfg['link'].get('simulate', False))
                if self.mode == 'sim' and not simulated:
                    raise RealPortForbidden('practice mode never builds a link to a real port')
                if not simulated and self._link_factory is None:
                    # Decided here, before pyserial is touched.
                    if not _real_ports_allowed or no_serial():
                        raise RealPortForbidden('real serial ports are not allowed in this process')
                    if not v7.pyserial_ok:
                        raise VendorIncomplete('site\\serial')
                    if not isinstance(v7.transport.serial, types.ModuleType):
                        raise RealPortForbidden('real serial ports were switched off in this process')
                if str(cfg['link'].get('port', 'auto')).strip().lower() in ('auto', ''):
                    raise RealPortForbidden('the port is never guessed')
                raw = self._link_factory(device, cfg) if self._link_factory is not None else v7.transport.Link()
                if raw.cfg is not cfg:
                    raise RuntimeError('the link does not use the configuration made for it')
                if simulated and self._link_factory is None and (self._sim_firmware or SIM_FIRMWARE) == 'plasmic':
                    import megasim                                   # V7's simulator, speaking as the firmware of mega/
                    megasim.use(v7, raw)
                return raw, cfg
            finally:
                scrub = v7.config.get_config(reload=True)
                scrub['link'] = dict(scrub['link'], port=NOT_CHOSEN)

    def _identify(self, raw):
        """Is it the pump board? Only `?` is written here. Returns the fields of its STATE line, or None."""
        v7 = self._v7
        for _ in range(IDENTIFY_TRIES):
            if not raw.connected:
                return None
            try:
                lines = raw.send('?', ('STATE,',), IDENTIFY_TIMEOUT_S)
            except v7.LinkError:
                lines = []
            banner = raw.state.get('firmware')
            if banner is not None and not str(banner).startswith(READY_BANNER):
                return None
            for line in lines:
                if line.startswith('STATE,'):
                    fields = dict(KV.findall(line[len('STATE,'):]))
                    if all(name in fields for name in STATE_FIELDS):
                        return fields
        return None

    @staticmethod
    def _caps(fields):
        """What this board's firmware can do, from its STATE line (fw=p<n>: the firmware of mega/)."""
        tag = str((fields or {}).get('fw') or '')
        ours = bool(PLASMIC_FW.match(tag))
        return {'tag': tag if ours else '', 'dose': ours, 'watchdog': ours,
                'setup': ours and int(tag[1:]) >= SETUP_FW}      # CM, VL: the pumps of the cleaning, the valve links

    @staticmethod
    def _fingerprint(fields, cfg):
        """The only two numbers of the flashed build that the board reports."""
        try:
            return (round(float(fields['cal_pulses_per_ml']), 2) == round(float(cfg['motors']['pulses_per_ml']), 2)
                    and int(float(fields['max_flow_uL_min'])) == int(round(float(cfg['motors']['max_flow_ml_min']) * 1000)))
        except (KeyError, TypeError, ValueError):
            return False

    def _safe_state(self, conn):
        """SPEC B.3.4: after every identity check, every reconnect and every unexpected READY."""
        v7, raw, checked, cfg = self._v7, conn.raw, conn.checked, conn.cfg
        with self.lock:
            self._runs.clear()
            self._syringe.update(initialised=False, volume_ul=None, position=None, busy=False)
            size = int(self._settings['syringe_ul'])
        v7.registry.get('motors.stop_all').invoke(conn.ctx, {})          # X first; also W,STREAM,0 and `?`
        if not self._all_off(raw.state):
            raise _Unverified()
        self._board_setup_apply(conn)            # the pumps of the cleaning and the valve links, before anything can move
        v7.scale.clear_history()
        try:
            restored = v7.scale.restore_calibration(checked)             # W,DIV, and W,OFFSET when it is not 0
        except v7.FunctionError:
            restored = None                                              # the file cannot be read: not calibrated
        with self.lock:
            self._calibration_loaded = restored is not None
            self._calibration = ({key: restored.get(key) for key in ('divisor', 'offset', 'reference_grams', 'saved_at')}
                                 if restored else None)
        checked.send('W,AVG,%d' % int(cfg['scale']['average_samples']))
        checked.send('W,STABLE,%g' % float(cfg['scale']['stable_threshold_g']))
        checked.send('W,STREAM,%d' % int(cfg['scale']['stream_hz']))
        checked.send('?')
        if v7.scale.record not in raw.weight_hooks:
            raw.weight_hooks.append(v7.scale.record)
            raw.weight_hooks.append(lambda sample, conn=conn: self._on_weight(conn, sample))
        self._weight = (None, 0.0)               # only readings taken with the calibration in the board count
        self._scale_restart_at = None
        # Always: a board that did not restart when the port was opened may hold another size.
        v7.registry.get('pump.set_syringe').invoke(conn.ctx, {'volume_ul': size})
        try:
            v7.registry.get('pump.detect').invoke(conn.ctx, {})          # and never an automatic Y,INIT
            online = True
        except v7.LinkTrouble as exc:
            if not exc.raw.startswith('ERR,pump_'):
                raise
            online = False
        with self.lock:
            self._syringe['online'] = online
        if conn.caps['watchdog']:
            # Nothing this program starts is endless on this firmware; should a `V` ever run, it ends when the
            # board has not heard a line from this PC for this long (the heartbeat `?` comes every HEARTBEAT_S).
            checked.send('K,%d' % WATCHDOG_MS)

    def _on_weight(self, conn, sample):
        """Runs on V7's reader thread for every WEIGHT line."""
        if self._conn is conn:
            self._weight = (sample, time.perf_counter())

    def _watch(self, conn):
        v7, raw, checked = self._v7, conn.raw, conn.checked

        def alive():
            return self._alive(conn)

        if raw.state.get('firmware') is not None:
            return self._board_reset(conn)       # cannot be missed: the flag stays until it is taken here
        if not raw.connected:
            return self._link_lost(checked, raw.last_error or 'reader ended')
        now = time.perf_counter()
        with self.lock:
            op = self._op
            due = [(motor, run) for motor, run in self._runs.items() if now >= run['deadline']]
            manual = any(run['owner'] == 'manual' for run in self._runs.values()) or (op is not None and op['by'] == 'manual')
        if op is not None and op['kind'] == 'dose_weight' and op['abort'].is_set() and not op['cancel'].is_set():
            self._stop_all('abort')              # the dose blocks inside V7 and cannot look at its stop flag
        self._reap_runs(conn)                    # timed runs the board has ended by itself: the normal case
        for motor, run in due:
            if alive():
                self._deadline(conn, motor, run)  # the second line: the board should have ended it by now
        if manual and self._ui_age is not None and alive():
            try:
                age = self._ui_age()
            except Exception:
                age = None
            if age is not None and age > UI_WATCHDOG_SECONDS:
                key, priority, area, kind, text = A_PAGE
                text = text.format(seconds=UI_WATCHDOG_SECONDS)
                self._stop_all('page', why=('stopped', text))
                self._alarm((key, priority, area, kind, text))
        if alive() and time.perf_counter() - max(checked.last_state_at, conn.since) > HEARTBEAT_S:
            try:
                checked.send('?')
                conn.heartbeat_failures = 0
            except v7.LinkError:
                conn.heartbeat_failures += 1
                if conn.heartbeat_failures >= 2:
                    self._link_lost(checked, 'heartbeat')
        if alive():
            self._scale_duty(conn)

    def _board_reset(self, conn):
        """The Mega printed READY again: its settings are gone and its outputs are off."""
        v7, raw = self._v7, conn.raw
        banner, raw.state['firmware'] = raw.state.get('firmware'), None
        with self.lock:
            op = self._op
            if op is not None:
                if op['fault'] is None:
                    op['fault'] = ('device', T_RESET)
                op['monitor'] = True
                op['cancel'].set()
            self._runs.clear()
            self._syringe.update(busy=False, initialised=False, volume_ul=None, position=None)
            conn.live = False
            self._link, self._text, self._since = 'connecting', LINK_TEXT['connecting'], now_local()
        self._alarm(A_RESET)
        self._log_line('-- READY again: the board restarted (%s)' % banner)
        try:
            for _ in range(3):
                self._safe_state(conn)
                if raw.state.get('firmware') is None:
                    break
                banner, raw.state['firmware'] = raw.state.get('firmware'), None
            else:
                raise v7.LinkTrouble('device', T_RESET)
        except (_Unverified, v7.LinkError, v7.FunctionError) as exc:
            unverified = isinstance(exc, _Unverified)
            self._drop_connection()
            with self.lock:
                self._uncertain = self._uncertain or unverified
                self._immediate = True
            self._set_link('lost', LINK_TEXT['lost'], getattr(exc, 'text', None) or str(exc) or LINK_TEXT['not_stopped'])
            self._alarm(A_STOP if unverified else A_LINK_LOST)
            self._lost_alarm = self._lost_alarm or not unverified
            return
        with self.lock:
            conn.live = True
            conn.since = time.perf_counter()
            conn.heartbeat_failures = 0
            self._banner = banner
            self._uncertain = False
            self._endless_risk = False
            self._generation += 1
            conn.generation = self._generation
        self._set_link('connected', LINK_TEXT['sim'] if self.mode == 'sim' else LINK_TEXT['connected'].format(device=conn.device))
        self._clear_alarm(A_STOP)
        self._guard_update()

    def _reap_runs(self, conn):
        """Timed runs whose motor the board reports at rest although nobody stopped it: their time is over
        and the board has ended them by itself."""
        with self.lock:
            over = [(motor, run) for motor, run in self._runs.items()
                    if run['confirmed'] and not self._motor_active(conn, motor)]
            for motor, _ in over:
                del self._runs[motor]
        for motor, run in over:
            self._run_over(motor, run)

    def _run_over(self, motor, run):
        """The time of a run is over. By hand that is how it should end; in a round the Recipe's pump_off
        should have come first, so the round is told (deadline_tripped)."""
        if run['owner'] == 'manual':
            text = E_DEADLINE_MANUAL.format(label=self._label(motor))
            with self.lock:
                self._tripped['manual'][motor] = text
                self._last_result = {'time': now_local(), 'ok': True,
                                     'text': '%s arrived at the setting %s seconds, stopped automatically' % (self._label(motor), _g(run['limit_s']))}
            self._event(text, 'info')
        else:
            with self.lock:
                self._tripped['round'][motor] = E_DEADLINE_ROUND.format(label=self._label(motor))

    def _deadline(self, conn, motor, run):
        """The second line: the board should have ended this run by itself and still reports it moving."""
        with self.lock:
            if self._runs.get(motor) is not run:
                return
            del self._runs[motor]
        self._halt(conn, motor, 'deadline')
        self._run_over(motor, run)

    def _halt(self, conn, motor, reason):
        """Stop one motor and see that it has stopped; a motor that does not obey brings everything down."""
        v7 = self._v7
        try:
            v7.registry.get('motors.stop').invoke(conn.ctx, {'motor': motor})
            self._await(conn, lambda: not self._motor_active(conn, motor), STOP_IDLE_S)
            return True
        except (v7.LinkError, v7.FunctionError, PumpBoardError):
            pass
        if self._conn is conn and not conn.checked.dead:
            self._stop_all(reason)
        return False

    def _scale_duty(self, conn):
        now = time.perf_counter()
        sample, at = self._weight
        if self._scale_restart_at is not None:
            if now < self._scale_restart_at:
                return
            self._scale_restart_at = None        # X switched the stream off
            conn.scale_kicked = now
            self._scale_restart(conn)
            return
        age = now - (at if sample is not None else conn.since)
        if sample is not None and age <= WEIGHT_FRESH_S:
            conn.scale_kicked = None
            if self._scale_alarm:
                self._scale_alarm = False
                self._clear_alarm(A_SCALE)
            return
        if conn.scale_kicked is None:
            if age >= SCALE_SILENT_S:
                conn.scale_kicked = now
                self._scale_restart(conn)        # one restart; there is no “Reread” button
        elif age >= SCALE_ALARM_S and not self._scale_alarm:
            self._scale_alarm = True
            self._alarm(A_SCALE)

    def _scale_restart(self, conn):
        try:
            self._v7.registry.get('scale.restart').invoke(conn.ctx, {})
        except (self._v7.LinkError, self._v7.FunctionError):
            pass

    # ============================================================================================
    # Ports and settings
    # ============================================================================================
    def _list_ports(self):
        if self._port_lister is not None:
            return [port_row(item) for item in self._port_lister()]
        return system_ports()

    @staticmethod
    def _same_identity(row, settings):
        wanted = (str(settings.get('vid') or '').upper(), str(settings.get('pid') or '').upper())
        serial_number = str(settings.get('serial_number') or '')
        return (row['is_mega'] and wanted in MEGA_USB_IDS and (row['vid'], row['pid']) == wanted
                and (not serial_number or row['serial_number'] == serial_number))

    def _matching_ports(self):
        with self.lock:
            settings = dict(self._settings)
        return [row for row in self._list_ports() if self._same_identity(row, settings)]

    def _find_port(self):
        """The stored port if it still is the stored board; else the one port that is. Opens nothing."""
        with self.lock:
            settings = dict(self._settings)
        rows = self._matching_ports()
        name = settings.get('device') or ''
        for row in rows:
            if row['device'] == name:
                return row
        if len(rows) == 1:
            row = rows[0]
            settings.update(device=row['device'], description=row['description'])
            self._write_settings(settings)
            self._event(E_PORT_MOVED.format(old=name or '?', new=row['device']), 'warn')
            return row
        if not rows and settings.get('chosen_by') == 'auto':
            # The board that was found by itself is gone. When exactly one other Arduino Mega is there now (the
            # board was replaced), that one is taken; a board that somebody chose by hand is never swapped.
            self._adopt_checked = None
            if self._adopt(replaced=True) is True:
                rows = self._matching_ports()
                if len(rows) == 1:
                    return rows[0]
        state = 'ambiguous' if rows else 'missing'
        if self._link != state:
            self._set_link(state, LINK_TEXT[state], LINK_TEXT[state])
            if self._was_connected and not self._lost_alarm:
                self._lost_alarm = True
                self._alarm(A_LINK_LOST)
        return None

    def _read_settings(self):
        settings = dict(DEFAULT_SETTINGS)
        try:
            saved = self.store.pump_settings() if self.store is not None else None
        except Exception:
            saved = None
        if isinstance(saved, dict):
            settings.update(saved)
        if settings.get('enabled') not in (None, True, False):
            settings['enabled'] = None
        try:
            settings['syringe_ul'] = int(settings['syringe_ul'])
            settings['syringe_speed_ml_s'] = float(settings['syringe_speed_ml_s'])
        except (TypeError, ValueError):
            settings.update(syringe_ul=DEFAULT_SETTINGS['syringe_ul'], syringe_speed_ml_s=DEFAULT_SETTINGS['syringe_speed_ml_s'])
        return settings

    def _write_settings(self, settings):
        settings = dict(settings, version=1)
        if self.store is not None:
            self.store.save_pump_settings(dict(settings))
        with self.lock:
            self._settings = settings

    # ============================================================================================
    # V7, hardware, small things
    # ============================================================================================
    def _load(self):
        if self._v7 is None:
            self._v7 = load_v7()
        if self.mode != 'real' or no_serial():
            forbid_serial(self._v7)
        if self._hardware is None:
            self._read_hardware()

    def _read_hardware(self):
        v7 = self._v7
        with _v7_lock:
            try:
                cfg = v7.config.get_config(reload=True)
                cfg['link'] = dict(cfg['link'], port=NOT_CHOSEN)
                if self._overrides is not None:
                    self._overrides(cfg)
                self._apply_pump_calibration(cfg)
                setup, source = self._apply_setup(cfg)
                stock = cfg['plasmic_stock']
            except Exception as exc:
                raise VendorIncomplete('v7\\config\\hardware.yaml (%s) ' % exc) from exc
            finally:
                try:
                    scrub = v7.config.get_config(reload=True)
                    scrub['link'] = dict(scrub['link'], port=NOT_CHOSEN)
                except Exception:
                    pass
        links = {int(link['motor']): int(link['valve']) for link in cfg['valves']['motor_links']}
        owners = {valve: motor for motor, valve in links.items()}
        motors, clean, syringe, scale = cfg['motors'], cfg['cleaning'], cfg['syringe_pump'], cfg['scale']
        # People count the pumps 1-8 in the order of the wiring; the board and V7 know them by pin (2-9).
        numbers = {}
        for group in motors['groups']:
            for row in group['channels']:
                numbers[int(row['id'])] = len(numbers) + 1
        factory = float(motors['dose_pulses_per_ml'])

        def pump(row):
            number = numbers[int(row['id'])]
            return {'id': int(row['id']), 'pump': number, 'short': 'Pump %d' % number,
                    'label': 'Pump %d' % number + (' · %s' % row['fluid'] if row.get('fluid') else ''),
                    'fluid': row.get('fluid'), 'use': setup['pumps'][int(row['id'])]['use'],
                    'valve': links.get(int(row['id'])),
                    'dose_pulses_per_ml': _number(row.get('dose_pulses_per_ml', factory)),
                    'factory_pulses_per_ml': _number(factory if row.get('plasmic_base') is None else row['plasmic_base'])}
        weighing = motors.get('gravimetric_dose') or {}
        before, after = int(cfg['valves']['open_before_ms']), int(cfg['valves']['close_after_ms'])
        times = [float(clean[name]) for name in ('water_lead_s', 'overlap_s', 'out_tail_s')]
        # The valve of the water pump opens before the cleaning starts and the one of the OUT pump closes after its end.
        cleaners = [motor for motor in (int(clean['water_motor']), int(clean['out_motor'])) if motor in links]
        valve_time = ((before if int(clean['water_motor']) in links else 0) + (after if cleaners else 0)) / 1000.0
        hardware = {
            'groups': [{'id': group['id'], 'label': group.get('label') or str(group['id']),
                        'motors': [pump(row) for row in group['channels']]} for group in motors['groups']],
            'valves': [{'id': int(row['id']), 'label': row.get('label') or 'solenoid valve %d' % int(row['id']),
                        'motor': owners.get(int(row['id']))} for row in cfg['valves']['channels']],
            'valve_open_before_ms': before, 'valve_close_after_ms': after,
            'max_flow_ml_min': _number(motors['max_flow_ml_min']),
            'default_flow_ml_min': _number(motors['default_flow_ml_min']),
            'dose_flow_ml_min': _number(motors.get('dose_flow_ml_min', motors['default_flow_ml_min'])),
            # Amendment M10: the Recipe module's estimate of a dose needs the two step factors and the stroke.
            'pulses_per_ml': _number(motors['pulses_per_ml']),
            'dose_pulses_per_ml': _number(motors['dose_pulses_per_ml']),
            'gravimetric': {'enabled': bool(weighing.get('enabled', False)),
                            'fast_flow_ml_min': _number(weighing.get('fast_flow_ml_min', 0)),
                            'approach_flow_ml_min': _number(weighing.get('approach_flow_ml_min', 0)),
                            'trim_flow_ml_min': _number(weighing.get('trim_flow_ml_min', 0)),
                            'tolerance_percent': _number(weighing.get('tolerance_percent', 0)),
                            'timeout_s': _number(weighing.get('timeout_s', 0))},
            'cleaning': {'water_motor': int(clean['water_motor']), 'out_motor': int(clean['out_motor']),
                         'flow_ml_min': _number(clean['flow_ml_min']), 'water_lead_s': _number(times[0]),
                         'overlap_s': _number(times[1]), 'out_tail_s': _number(times[2]),
                         'total_s': _number(round(sum(times) + valve_time, 3))},
            'syringe': {'options_ul': [int(value) for value in syringe['syringe_options_ul']],
                        'valve_positions': int(syringe['valve_positions']),
                        'full_stroke_steps': int(syringe['full_stroke_steps'])},
            'scale': {'decimals': int(scale.get('display_decimals', 3)),
                      'calibration_presets_g': [_number(value) for value in scale.get('calibration_presets_g', [])],
                      'default_calibration_g': _number(scale.get('default_calibration_g', 100))},
        }
        facts = {'hardware_sha256': hashlib.sha256(v7.yaml_file.read_bytes()).hexdigest(),
                 'pulses_per_ml': float(motors['pulses_per_ml']), 'dose_pulses_per_ml': float(motors['dose_pulses_per_ml']),
                 'dose_flow_ml_min': hardware['dose_flow_ml_min'], 'full_stroke_steps': int(syringe['full_stroke_steps'])}
        with self.lock:
            old = self._hardware
            if old is None or {key: value for key, value in old.items() if key != 'rev'} != hardware:
                self._hardware_rev += 1
            hardware['rev'] = self._hardware_rev
            self._hardware, self._facts = hardware, facts
            self._setup, self._setup_source, self._stock = setup, source, stock

    @staticmethod
    def _copy_state(conn):
        """V7's snapshot() shares its inner dicts with the reader thread: copy what the view needs."""
        if conn is None:
            return {}
        state = conn.raw.state
        return {'cleaning': bool(state.get('cleaning')),
                'motors': {key: dict(slot) for key, slot in list((state.get('motors') or {}).items())},
                'valves': dict(state.get('valves') or {})}

    @staticmethod
    def _clean_phase(state, clean):
        states = {slot.get('selected'): slot.get('state') for slot in (state.get('motors') or {}).values()}
        water, out = states.get(clean['water_motor']), states.get(clean['out_motor'])
        if water == 'wait':
            return 'valve'
        if water == 'run':
            return 'both' if out == 'run' else 'water'
        return 'drain'

    @staticmethod
    def _op_text(op, elapsed, gain, phase):
        text = op['text']
        if op['kind'] in ('dose_volume', 'dose_many') and op.get('eta_s'):
            return '%s · already %d Second/about %d Second' % (text, elapsed, math.ceil(op['eta_s']))
        if op['kind'] == 'dose_weight' and op.get('target_g') is not None:
            return '%s · at present %+.2f g/Target %s g' % (text, gain or 0.0, _g(op['target_g']))
        if op['kind'] == 'clean' and op.get('eta_s'):
            name = {'valve': 'water valve', 'water': 'Water ingress', 'both': 'Water inlet and discharge', 'drain': 'finally emptied'}.get(phase, 'water valve')
            return '%s · %s · already %d Second/about %d Second' % (text, name, elapsed, math.ceil(op['eta_s']))
        return text

    def _set_link(self, state, text, error=''):
        with self.lock:
            if state != self._link:
                self._since = now_local()
            self._link, self._text, self._error = state, text, error
            self._changed.notify_all()

    def _event(self, text, level='info'):
        try:
            if self._on_event is not None:
                self._on_event(text, level)
            elif self.store is not None and hasattr(self.store, 'event'):
                try:
                    self.store.event(text, level, 'system')
                except TypeError:
                    self.store.event(text, level)
        except Exception:
            pass

    def _alarm(self, alarm):
        key, priority, area, kind, text = alarm
        if self._on_alarm is None:
            return
        try:
            self._on_alarm('raise', key, priority, area, text, kind)
        except Exception:
            pass

    def _clear_alarm(self, alarm):
        if self._on_alarm is None:
            return
        try:
            self._on_alarm('clear', alarm[0])
        except Exception:
            pass

    # ============================================================================================
    # Guard and serial log
    # ============================================================================================
    def _guard_port(self, device):
        if self.mode != 'real':
            return
        if not self._guard_started:
            self._guard_started = True
            with self.lock:
                settings = dict(self._settings)
            self._guard.start(str(settings.get('vid') or ''), str(settings.get('pid') or ''),
                              str(settings.get('serial_number') or ''), self.data_dir / 'logs' / 'guard.log')
        self._guard.tell('port %s' % device)

    def _guard_update(self):
        """Armed only while something may run that has no end of its own. Since timed runs are finite moves
        that is V7's weight dose alone — while it runs, and after it while nobody knows whether its pump stopped."""
        with self.lock:
            endless = self._endless_risk or (self._op is not None and self._op['endless'])
            if endless == self._guard_armed:
                return
            self._guard_armed = endless
        self._guard.tell('arm' if endless else 'disarm')

    def _log_line(self, text):
        self._write_log(['%s %s' % (datetime.now().astimezone().isoformat(timespec='milliseconds'), text)])

    def _flush_serial_log(self, conn=None):
        """New entries of V7's link log (lines sent and received; V7 keeps WEIGHT lines out of it) go to disk."""
        conn = conn or self._conn
        if conn is None:
            return
        with self._log_lock:             # taking the new entries and writing them is one step (two threads flush)
            entries = conn.checked.since(conn.log_mark)
            if not entries:
                return
            conn.log_mark = entries[-1]
            lines = []
            for entry in entries:
                text = str(entry.get('text', ''))
                if entry.get('kind') == 'rx' and text.startswith('STATE,'):
                    conn.checked.last_state_at = time.perf_counter()    # the board's own STATE lines count as a heartbeat
                stamp = datetime.fromtimestamp(entry.get('t') or time.time()).astimezone().isoformat(timespec='milliseconds')
                lines.append('%s %s %s' % (stamp, entry.get('kind', '?'), text))
            self._write_log(lines)

    def _write_log(self, lines):
        path = self.data_dir / 'logs' / 'pumpboard.log'
        with self._log_lock:
            try:
                if path.is_file() and path.stat().st_size >= LOG_MAX_BYTES:
                    for index in range(LOG_KEEP, 0, -1):
                        older = path.with_name('%s.%d' % (path.name, index))
                        newer = path if index == 1 else path.with_name('%s.%d' % (path.name, index - 1))
                        if newer.is_file():
                            newer.replace(older)
                with path.open('a', encoding='utf-8', newline='\n') as out:
                    out.write('\n'.join(lines) + '\n')
            except OSError:
                pass
