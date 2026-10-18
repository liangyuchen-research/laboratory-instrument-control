"""Plasmic Electronic control — Recipe Language: one copy CSV describes what to do in ”one round”.

The method remains the same as the original HMI of csv_workflow.py: The header is fixed action,target,value,unit,note,One action in a row,
repeat_start/repeat_end can wrap a section and do it repeatedly; there are no variables and no branches. Repeated measurement is a matter of ”scheduled measurement”.
The actions are replaced by the current set of equipment:Arduino Mega pump control board (independent laboratory v7) and RPi . Static check for

This file has only pure functions: no touching the device, no reading of any configuration files, no loading yaml/pumpboard/app.Pump Control Board Facts
 (Motor number and name, grouping, flow limit, cleaning time...) used by the caller `hardware` is passed in, and the shape is
/api/bootstrap of `hardware` (API.md C.3.1) .`hardware` yes None express pc\\vendor incomplete: 
Only without pump control board Recipe will pass. Every word on the screen is reflected SPEC A and AMENDMENTS M4.1 writes.

English notes for the other modules of 2.0.0 (SPEC B.13, AMENDMENTS M4.1):

    decode(data: bytes) -> (text, encoding)
        UTF-8 (with or without BOM), else cp950. encoding is 'utf-8' or 'cp950'. The text has no BOM
        and LF line ends. ValueError(T_UNREADABLE) when the bytes are neither.
    validate(text, hardware, syringe_ul=5000, encoding='utf-8') -> dict
        Never raises for anything a file can hold. Keys: valid, error, error_line, warnings, step_count,
        rounds, delay_seconds, phases, uses_pump, uses_motors, uses_syringe, uses_scale, uses_rpi,
        uses_plasma, plasma_count, liquids, steps, rows. Pass the encoding decode() returned and the
        cp950 warning (W6) is added. `csv`, `encoding` and `estimate_seconds` of the HTTP reply are the
        route's job.
        Not valid because of a row or of the file (R1, R2): steps is empty and the counts are zero.
        Not valid because of the order of the steps (R3-R10, syringe size): steps and all facts are there.
        rows (one item per source row, for the step editor) is filled in both cases; only the first
        problem is reported, and the row of error_line is the one with ok = False.
    estimate(result, hardware, rpi_estimate_s, syringe_speed, integration_ms=None) -> float
        Seconds of one round (SPEC A.5). For the dose time it wants hardware['pulses_per_ml'] and
        hardware['dose_pulses_per_ml'] (PumpBoard.facts() has them); without them it assumes they are equal.
    compose(rows) -> str            rows of the step editor -> CSV text; ValueError(T_ROWS_FORMAT).
    actions_view(hardware, syringe_ul=5000) -> list      bootstrap.recipe.actions.
    parse(text, hardware, syringe_ul), expand(tree)      the two halves validate() is made of.
    ACTIONS                         the action table of SPEC A.2, in that order (a dict keyed by action).
    flow_csv(flow, hardware) -> (csv text, the flow as it is kept)
        The four stages of the schedule page (Add liquid → Measure → Empty → Cleaning, AMENDMENTS M16) as a Recipe.
        ValueError(text) for a flow that cannot be one. flow_default() is the empty one.
    flow_steps_csv(steps) -> (csv_text, rows)            the step list of the schedule page (AMENDMENTS M23): rows of
        this language, only the actions of FLOW_ACTIONS, at most FLOW_STEPS_MAX. flow_stage_steps() reads the four
        stages of before as such rows; as_flow() / flow_words() say validate()'s sentences in the words of that page.

Pumps (AMENDMENTS M16): people count them Pump 1-8 in the order of the wiring; a target is written P1-P8.
The older spelling M2-M9 (the pin of the board) is still read. A validated step carries `motor` (the pin,
what PumpBoard wants) and, for dose_together, `motors` and `amounts`.
    EXAMPLES, EXAMPLES_DIR          the three example files of SPEC A.6 (pc\\recipes).
    LIMITS                          bootstrap.recipe.limits.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

HEADER = ('action', 'target', 'value', 'unit', 'note')
# bootstrap.recipe.limits (API.md C.3.1). The texts below spell the same numbers out.
LIMITS = {'bytes': 200000, 'rows': 500, 'steps': 2000, 'nesting': 5, 'repeat': 20, 'delay_total_s': 7200}
NOTE_MAX = 200
RUN_MIN_S = Decimal('0.1')            # R6: shortest planned on-time of a continuous run
RUN_MAX_S = Decimal(300)              # R6: longest (bootstrap.limits.run_max_s)
DEFAULT_SYRINGE_UL = 5000
DEFAULT_SYRINGE_SPEED_ML_S = 0.4      # the saved default of Maintenance › Pump and Scale (SPEC 0.2)
SYRINGE_STROKE_STEPS = 12000          # SY-01B full stroke (V7 hardware.yaml, syringe_pump.full_stroke_steps)
SYRINGE_RAW_SPEED_MAX = 6000          # the pump's fastest raw speed, steps per second (V7 functions/pump.py)
DEFAULT_PLASMA_S = 30.0               # A.5: one plasma_run while the RPi's own estimate is not known
DEFAULT_INTEGRATION_MS = 1000.0       # A.5: spectrum_capture while the integration time is not known
EXAMPLES_DIR = Path(__file__).resolve().parent / 'recipes'

PHASE_FEED, PHASE_CLEAN, PHASE_MEASURE, PHASE_OFF, PHASE_WAIT = 'Add liquid', 'Cleaning', 'Measure', 'Turn off high voltage', 'Waiting'
PHASE_DRAIN = 'Empty'                  # a pump step on the board's OUT pump (cleaning.out_motor)
TOGETHER_MAX = 8                      # pumps in one dose_together
GROUP_GENERAL, GROUP_PUMP, GROUP_SYRINGE, GROUP_SCALE, GROUP_PLASMA = 'generally', 'pump', 'syringe pump', 'scales', 'plasma'


def _range(low, high, decimals, unit):
    return {'min': low, 'max': high, 'decimals': decimals, 'unit': unit}


def _action(name, group, target='', value=None, phase=None, board=False, rpi=False, alias=None):
    """One row of SPEC A.2.

    name, target ('' | 'motor' | 'valve'), value (None | {min, max, decimals, unit}) and group are what a
    form needs (M4.1). phase is the display class of the step, board / rpi say which side carries it out,
    alias names the action this one is a second spelling of.
    """
    return {'name': name, 'target': target, 'value': value, 'group': group, 'phase': phase,
            'board': board, 'rpi': rpi, 'alias': alias}


# Order = SPEC A.2 = the order of the “Select action” dialog. The upper limits of the pump flow, the syringe
# volume and the syringe speed written here are today's; _Board.top() gives the ones that apply.
ACTIONS = {
    'delay': _action('Waiting', GROUP_GENERAL, value=_range(0, 3600, 1, 's')),
    'pump_on': _action('Start pump (forward rotation)', GROUP_PUMP, 'motor', _range(0.1, 80, 1, 'mL/min'), PHASE_FEED, board=True),
    'pump_reverse': _action('Start pump (reverse)', GROUP_PUMP, 'motor', _range(0.1, 80, 1, 'mL/min'), PHASE_FEED, board=True),
    'pump_off': _action('stop pump', GROUP_PUMP, 'motor', None, PHASE_FEED, board=True),
    'dose_volume': _action('Quantitative (volume)', GROUP_PUMP, 'motor', _range(0.01, 1000, 2, 'mL'), PHASE_FEED, board=True),
    'dose_reverse': _action('Quantification (volume, inversion)', GROUP_PUMP, 'motor', _range(0.01, 1000, 2, 'mL'), PHASE_FEED,
                            board=True),
    # Several pumps in one step: P1+P8 / 10+2. One pump per driver group turns at a time (PumpBoard.dose_many).
    'dose_together': _action('Simultaneous quantitative', GROUP_PUMP, 'motors', _range(0.01, 1000, 2, 'mL'), PHASE_FEED, board=True),
    'dose_weight': _action('Quantitative (weight)', GROUP_PUMP, 'motor', _range(0.01, 1000, 2, 'g'), PHASE_FEED, board=True),
    'clean': _action('automatic cleaning', GROUP_PUMP, phase=PHASE_CLEAN, board=True),
    'syringe_init': _action('syringe pump initialization', GROUP_SYRINGE, phase=PHASE_FEED, board=True),
    # Its display class is the one of the next device step (SPEC A.3), so it has none of its own.
    'syringe_speed': _action('Syringe pump speed', GROUP_SYRINGE, value=_range(0.001, 2.5, 3, 'mL/s'), board=True),
    'syringe_aspirate': _action('syringe pump for pumping liquid', GROUP_SYRINGE, 'valve', _range(0.001, 5, 3, 'mL'), PHASE_FEED, board=True),
    'syringe_dispense': _action('syringe pump injection', GROUP_SYRINGE, 'valve', _range(0.001, 5, 3, 'mL'), PHASE_FEED, board=True),
    'scale_read': _action('Record weight', GROUP_SCALE, phase=PHASE_FEED, board=True),
    'rpi_status': _action('Check that the high voltage is off', GROUP_PLASMA, phase=PHASE_MEASURE, rpi=True),
    'nano_prepare': _action('Synchronization parameters arrive Nano', GROUP_PLASMA, phase=PHASE_MEASURE, rpi=True),
    'plasma_run': _action('discharge＋capture spectrum', GROUP_PLASMA, phase=PHASE_MEASURE, rpi=True),
    'spectrum_capture': _action('Spectrometer inspection (no discharge)', GROUP_PLASMA, phase=PHASE_MEASURE, rpi=True),
    # Two names, one action: nano_stop is kept so that the original's CSV files load.
    'nano_stop': _action('Turn off high voltage', GROUP_PLASMA, phase=PHASE_OFF, rpi=True, alias='power_off'),
    'power_off': _action('Turn off high voltage', GROUP_PLASMA, phase=PHASE_OFF, rpi=True),
    'repeat_start': _action('Repeat start', GROUP_GENERAL, value=_range(1, 20, 0, '')),
    'repeat_end': _action('Repeat end', GROUP_GENERAL),
}
RUN_ACTIONS = ('pump_on', 'pump_reverse')
DOSE_ACTIONS = ('dose_volume', 'dose_reverse')
SYRINGE_MOVES = ('syringe_aspirate', 'syringe_dispense')
BOARD_ACTIONS = tuple(action for action, facts in ACTIONS.items() if facts['board'])
RPI_ACTIONS = tuple(action for action, facts in ACTIONS.items() if facts['rpi'])
# Seconds of the steps whose estimate does not depend on anything (SPEC A.5).
FIXED_S = {'syringe_init': 6.0, 'scale_read': 1.0, 'rpi_status': 3.0, 'nano_prepare': 6.0, 'nano_stop': 5.0,
           'power_off': 5.0}

EXAMPLES = [
    {'file': 'plasma-only.csv', 'name': 'Only discharge measurement', 'note': 'only needs RPi; can be used without pump control board'},
    {'file': 'dose-measure-drain.csv', 'name': 'Example: Add liquid＋Measure＋discharge',
     'note': 'is for maintenance personnel to modify; only used Pump 1 (water),Pump 7 (OUT) , Pump 8 (CuSO₄) '},
    {'file': 'cleaning.csv', 'name': 'Example: automatic cleaning', 'note': 'only requires pump control board'},
]

# ---- texts (SPEC A.1, A.3; AMENDMENTS M4.1). {line} is the CSV row number, the header being row 1. ----
T_UNREADABLE = 'CSV cannot be read: please Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)” and then import.'
CP950_WARNING = ('This file is not UTF-8 (Excel ). The special words in the remarks may become ”?”.'
                 'It is recommended to Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)”.')
T_ROWS_FORMAT = 'The step data format is incorrect.'
# R1
T_HEADER = 'CSV The first column of must be action,target,value,unit,note'
T_TOO_BIG = 'CSV is too large (upper limit 200 KB) '
T_TOO_MANY_ROWS = 'CSV most 500 List'
T_COLUMNS = 'No. {line} Column: The number of fields is incorrect (required 5 column) '
T_TOO_MANY_STEPS = 'Expand beyond 2000 action'
T_NESTING = 'repeat most 5 layer'
T_REPEAT_COUNT = 'No. {line} column:repeat_start If the number of 1–20 integer'
T_REPEAT_END = 'No. {line} column:repeat_end has no corresponding repeat_start'
T_REPEAT_OPEN = 'No. {line} column:repeat_start less repeat_end'
T_EMPTY = 'CSV There is no action that can be performed'
# R2
T_ACTION = 'No. {line} Column: Unsupported action ”{action}”'
T_TARGET = 'No. {line} column:{name} The object to be written {targets}'
T_TARGET_EMPTY = 'No. {line} column:{name} must be blank The working period of'
T_TOGETHER_TARGET = 'No. {line} column:{name} needs to write more than two different pumps, use + Connect together (example: P1+P8) '
T_TOGETHER_VALUE = 'No. {line} column:{name} should be the same as the pump, use Nothing is opened when + Connect together (example: 10+2) '
T_TOGETHER_DRAIN = 'No. {line} column:{label} is a pump for emptying and cannot be used for dosing with other pumps.'
T_RANGE = 'No. {line} column:{name} The value should be within {low}–{high} between'
T_STEP = 'No. {line} column:{name} is The new entry point of {step}'
T_VALUE_EMPTY = 'No. {line} column:{name} should be blank'
T_UNIT = 'No. {line} column:{name} should be written The four lines of {unit}'
T_UNIT_EMPTY = 'No. {line} column:{name} should be blank The readback of'
# Not in the specification: hardware is None (pc\vendor incomplete) and the row needs the pump board.
T_NO_BOARD = 'No. {line} Column: Program folder is incomplete (missing pc\\vendor) ,can’t do“{name}”.Please copy the entire folder again.'
# R3 - R10
T_GROUP = 'No. {line} column:{group}can only be transferred one at a time ({motor} is still transferring)'
T_RUNNING = 'No. {line} column:{motor} is already transferring'
T_NOT_RUNNING = 'No. {line} column:{motor} is not transferring'
T_PUMPS_FIRST = 'No. {line} column: do ”{name}”All pumps must be stopped before'
T_RUN_LONG = 'No. {line} column:{motor} will transfer continuously {seconds} seconds, exceeding the upper limit 300 Second'
T_RUN_ZERO = 'No. {line} column:{motor} Turn it off when it is turned on, there must be something in between delay'
T_LEFT_ON = 'The pump is still running at the end of the process:{pumps}'
T_LEFT_ON_PUMP = '{motor} (No. {line} column startup)'
T_SYRINGE_INIT = 'No. {line} column: Before using the syringe pump, syringe_init'
T_SYRINGE_NOT_EMPTY = 'No. {line} Column: There is still something in the syringe {have} mL,initialization will push it out; please first syringe_dispense'
T_SYRINGE_OVER = 'No. {line} column: The syringe cannot be loaded (existing {have} mL,capacity {size} mL) '
T_SYRINGE_UNDER = 'No. {line} column: There are only {have} mL,Can’t push it out {want} mL'
T_SYRINGE_LEFT = 'There is still something left in the syringe at the end of the process. {have} mL,Please launch first'
T_SYRINGE_SPEED = 'No. {line} column: Syringe pump speed exceeds the upper limit {top} mL/s'
T_WEIGHT_OFF = 'No. {line} Column: Profile does not enable weighing and quantification'
T_DELAY_TOTAL = 'The total waiting time exceeds 7200 Second'
# Warnings (W1 was dropped by AMENDMENTS M8; W6 is CP950_WARNING)
W_PLASMA_COUNT = 'this Recipe One round will discharge {count} Second-rate'
W_REVERSE = 'No. {line} column:{motor} reverse'
W_NO_FLUID = 'No. {line} column:{motor} No liquid is marked, please make sure its outlet is on the scale The'
W_NO_PLASMA = 'this Recipe has no measurement steps (plasma_run) '
# Step sentences
S_DELAY = 'Waiting {amount} Second'
S_RUN = '{motor} {way} {amount} mL/min'
S_RUN_LIMIT = ' (longest running time {seconds} seconds)'
S_FORWARD, S_REVERSE = 'Forward rotation', 'reverse'
S_STOP = '{motor} Stop'
S_DOSE = '{motor} quantitative {amount} mL'
S_DOSE_REVERSE = '{motor} inversion quantitative {amount} mL'
S_DOSE_WEIGHT = '{motor} scale weight added to {amount} g'
S_CLEAN = 'automatic cleaning ({water} Water ingress,{out} discharge, approx. {seconds} seconds)'
S_TOGETHER = 'Simultaneous quantification:{parts}'
S_TOGETHER_PART = '{motor}: {amount} mL'      # never “Pump 1 5 mL”: that reads as 15 mL (the person, 2026-10-07)
S_SYRINGE_SPEED = 'Syringe pump speed {amount} mL/s'
S_ASPIRATE = 'Syringe pump slave valve position {position} smoke {amount} mL'
S_DISPENSE = 'Syringe pump moves to valve position {position} Note {amount} mL'
S_SCALE = 'Record weight'
S_SCALE_NAMED = 'Record weight: {note}'
S_FIXED = {'clean': 'automatic cleaning', 'syringe_init': 'syringe pump initialization', 'rpi_status': 'Check that the high voltage is off',
           'nano_prepare': 'Synchronization parameters arrive Nano', 'plasma_run': 'discharge＋capture spectrum',
           'spectrum_capture': 'Spectrometer inspection (no discharge, reading 3 open) ', 'nano_stop': 'Turn off high voltage', 'power_off': 'Turn off high voltage'}
S_REPEAT_START = 'Repeat start ×{count}'
S_REPEAT_END = 'Repeat end'
S_VALVE = 'Valve position {position}'
RAW_JOIN = ' · '

NUMBER = re.compile(r'[0-9]+(?:\.[0-9]*)?|\.[0-9]+')   # plain decimal: no sign, no exponent, ASCII digits only
COUNT = re.compile(r'[0-9]{1,4}')
CONTROL = re.compile(r'[\x00-\x1f\x7f]+')
BOM = chr(0xFEFF)                      # the byte-order mark Excel puts in front of "CSV UTF-8"


# ---------------------------------------------------------------------------------------------------------
# Small things
# ---------------------------------------------------------------------------------------------------------
def _dec(value, default=None):
    """A finite Decimal from a number of the hardware dict or of a setting (int, float, text), else ``default``."""
    if value is None or isinstance(value, bool):
        return default
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return number if number.is_finite() else default


def _fmt(number):
    """A number the way the texts show it: no trailing zeros, no exponent (40.0 -> 40, 0.50 -> 0.5)."""
    number = _dec(number, Decimal(0))
    return '0' if number == 0 else format(number.normalize(), 'f')


def _num(number):
    """A number for JSON: an int when it is whole, else a float."""
    number = _dec(number, Decimal(0))
    return int(number) if number == number.to_integral_value() else float(number)


def _positive(value, default):
    """A float above zero, else ``default`` (the estimate must never fail on a missing number)."""
    number = _dec(value)
    return float(number) if number is not None and number > 0 else default


def _number(text):
    """The value cell as a Decimal, or None when it is not a plain decimal number. Nothing is rounded."""
    return Decimal(text) if NUMBER.fullmatch(text) else None


def _clean(cell):
    """A cell as it is kept: control characters (a line break inside quotes) become one space, ends stripped."""
    return CONTROL.sub(' ', cell).strip()


class _Check:
    """Keeps the first thing that is wrong; the walk goes on so that every row can still be described."""

    def __init__(self, error='', line=None):
        self.error, self.line = error, line

    def fail(self, text, line=None):
        if not self.error:
            self.error, self.line = text, line


class _Board:
    """What the checks need to know about the pump board: bootstrap.hardware plus the saved syringe size."""

    def __init__(self, hardware, syringe_ul=DEFAULT_SYRINGE_UL):
        hardware = hardware if isinstance(hardware, dict) else {}
        self.motors = {}          # id -> {label, fluid, valve, group, group_label}, in the order of the YAML
        self.positions = 0        # head-valve positions of the syringe pump
        options = []
        try:
            for group in hardware.get('groups') or ():
                group_label = str(group.get('label') or '%s Group' % group.get('id'))
                for motor in group.get('motors') or ():
                    number = int(motor['id'])
                    pump = int(motor.get('pump') or len(self.motors) + 1)
                    self.motors[number] = {'label': str(motor.get('label') or 'Pump %d' % pump), 'pump': pump,
                                           'short': str(motor.get('short') or 'Pump %d' % pump),
                                           'fluid': motor.get('fluid') or None, 'valve': motor.get('valve'),
                                           'group': group.get('id'), 'group_label': group_label,
                                           'dose_pulses': _positive(motor.get('dose_pulses_per_ml'), 0.0)}
            syringe = hardware.get('syringe') or {}
            self.positions = int(syringe.get('valve_positions') or 0)
            options = [_dec(value) for value in syringe.get('options_ul') or ()]
            weighing = hardware.get('gravimetric') or {}
            cleaning = hardware.get('cleaning') or {}
            self.weighing = bool(weighing.get('enabled'))
            self.fast_flow = _dec(weighing.get('fast_flow_ml_min'), Decimal(40))
            self.clean = None
            if cleaning.get('water_motor') is not None and cleaning.get('out_motor') is not None:
                self.clean = {'water_motor': int(cleaning['water_motor']), 'out_motor': int(cleaning['out_motor']),
                              'total_s': _dec(cleaning.get('total_s'), Decimal(0))}
            stroke = _dec(syringe.get('full_stroke_steps'), Decimal(SYRINGE_STROKE_STEPS))
        except (AttributeError, KeyError, TypeError, ValueError):
            self.motors, self.positions, options = {}, 0, []
            self.weighing, self.fast_flow, self.clean, stroke = False, Decimal(40), None, Decimal(SYRINGE_STROKE_STEPS)
        # The board exists for a Recipe only when its motors and the syringe's valve positions are known.
        self.ok = bool(self.motors) and self.positions >= 1
        # P1-P8 is how a pump is written; M2-M9 (the pin of the board) is the spelling of older files.
        self.targets = {'M%d' % number: number for number in self.motors}
        self.targets.update(('P%d' % facts['pump'], number) for number, facts in self.motors.items())
        self.valves = {'CH%d' % number: number for number in range(1, self.positions + 1)}
        self.max_flow = _dec(hardware.get('max_flow_ml_min'), Decimal(str(ACTIONS['pump_on']['value']['max'])))
        self.dose_flow = _dec(hardware.get('dose_flow_ml_min'), _dec(hardware.get('default_flow_ml_min'), Decimal(40)))
        self.open_s = _positive(hardware.get('valve_open_before_ms'), 0.0) / 1000.0
        self.close_s = _positive(hardware.get('valve_close_after_ms'), 0.0) / 1000.0
        # Not part of bootstrap.hardware today; PumpBoard.facts() has both (see estimate()).
        self.pulses = _positive(hardware.get('pulses_per_ml'), 0.0)
        self.dose_pulses = _positive(hardware.get('dose_pulses_per_ml'), 0.0)
        # Syringe: the size is a setting (Maintenance › Pump and Scale). Top speed as V7 computes it (functions/pump.py).
        if stroke is None or stroke <= 0:
            stroke = Decimal(SYRINGE_STROKE_STEPS)
        size = _dec(syringe_ul)
        if size is None or size <= 0:
            size = Decimal(DEFAULT_SYRINGE_UL)
        largest = max([size] + [option for option in options if option is not None and option > 0])
        self.size_ml = size / 1000
        self.speed_max = self._speed(size, stroke)
        # No syringe the YAML offers takes more than this: a value above it is refused as a bad row (R2),
        # one above the saved size only by the walk (R8, R11), so that the steps can still be listed.
        self.size_cap_ml = largest / 1000
        self.speed_cap = self._speed(largest, stroke)

    @staticmethod
    def _speed(size_ul, stroke):
        return (size_ul * SYRINGE_RAW_SPEED_MAX / (1000 * stroke)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)

    def top(self, action):
        """The upper limit of the value where it follows the YAML or the syringe size, else None."""
        if action in RUN_ACTIONS:
            return self.max_flow
        if action in SYRINGE_MOVES:
            return self.size_ml
        if action == 'syringe_speed':
            return self.speed_max
        return None

    def bounds(self, action):
        """(lowest, highest, smallest step, highest for any syringe) of the action's value."""
        spec = ACTIONS[action]['value']
        top = self.top(action)
        high = top if top is not None else Decimal(str(spec['max']))
        cap = self.size_cap_ml if action in SYRINGE_MOVES else self.speed_cap if action == 'syringe_speed' else high
        return Decimal(str(spec['min'])), high, Decimal(1).scaleb(-spec['decimals']), cap

    def motor_range(self):
        numbers = sorted(facts['pump'] for facts in self.motors.values())
        if len(numbers) > 1 and numbers == list(range(numbers[0], numbers[-1] + 1)):
            return 'P%d–P%d' % (numbers[0], numbers[-1])
        return ', '.join('P%d' % number for number in numbers)

    def target(self, motor):
        """How this pump is written in a Recipe: 'P7'."""
        return 'P%d' % self.motors[motor]['pump']

    def valve_range(self):
        return 'CH1' if self.positions == 1 else 'CH1–CH%d' % self.positions

    def dose_seconds(self, millilitres, motor=None):
        """|steps| / sps of a volume dose, the way V7's motors.dose counts them (a calibrated pump has its
        own steps per mL)."""
        flow = _positive(self.dose_flow, 40.0)
        own = self.motors[motor]['dose_pulses'] if motor in self.motors else 0.0
        pulses = own or self.dose_pulses
        if self.pulses and pulses:
            return max(1, round(millilitres * pulses)) / max(1, round(flow * self.pulses / 60.0))
        return millilitres / (flow / 60.0)


# ---------------------------------------------------------------------------------------------------------
# Reading a file, writing the editor's rows
# ---------------------------------------------------------------------------------------------------------
def normalize(text):
    """The form a CSV text is stored in: no byte-order mark, LF line ends."""
    return text.lstrip(BOM).replace('\r\n', '\n').replace('\r', '\n')


def decode(data):
    """The bytes of an imported file as (text, encoding).

    UTF-8 with or without BOM first; then cp950, which is what Excel's plain “CSV (comma separated) ” writes on
    this PC (give the encoding to validate() and the result carries CP950_WARNING). Anything else, and
    anything with a NUL byte (UTF-16), is refused with ValueError(T_UNREADABLE).
    """
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if not isinstance(data, bytes) or b'\x00' in data:
        raise ValueError(T_UNREADABLE)
    for codec, name in (('utf-8-sig', 'utf-8'), ('cp950', 'cp950')):
        try:
            return normalize(data.decode(codec)), name
        except UnicodeDecodeError:
            continue
    raise ValueError(T_UNREADABLE)


def compose(rows):
    """CSV text for the rows of the step editor: a list of [action, target, value, unit, note] (strings).

    An empty list gives the header alone. ValueError(T_ROWS_FORMAT) for more than 500 rows or a row that is
    not five strings. A line break inside a cell becomes a space, so the text reads back as the same rows.
    """
    if not isinstance(rows, (list, tuple)) or len(rows) > LIMITS['rows']:
        raise ValueError(T_ROWS_FORMAT)
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(HEADER)
    for row in rows:
        if (not isinstance(row, (list, tuple)) or len(row) != len(HEADER)
                or not all(isinstance(cell, str) for cell in row)):
            raise ValueError(T_ROWS_FORMAT)
        cells = [_clean(cell) for cell in row]
        try:
            ','.join(cells).encode('utf-8')
        except UnicodeEncodeError:              # half of a surrogate pair out of a JSON escape
            raise ValueError(T_ROWS_FORMAT) from None
        writer.writerow(cells)
    return buffer.getvalue()


# The four stages of the schedule page (AMENDMENTS M16). Kept as numbers people typed: mL, g.
FLOW_NAME = 'Add liquid → Measure → Empty → Cleaning'
T_FLOW_FORMAT = 'The content format of the process is incorrect.'
T_FLOW_EMPTY = 'All four stages are off: at least one must be on.'
T_FLOW_PUMP = 'The pump for adding liquid is wrong:{pump}'
T_FLOW_PUMP_TWICE = 'Pump {pump} selected twice'
T_FLOW_DRAIN_PUMP = 'Pump {pump} is a pump for emptying and cannot be used for adding liquid.'
T_FLOW_AMOUNT = '{label} The quantity should be within {low}–{high} {unit} , the smallest unit {step}'
T_FLOW_NO_DRAIN = 'There is no pump for emptying in the setting file'
T_FLOW_NO_BOARD = 'program folder is incomplete (missing pc\\vendor) ,The pump flow cannot be set.'
N_FILL, N_FILL_WEIGHT, N_MEASURE, N_DRAIN, N_CLEAN = 'Add liquid', 'Add liquid (stop with scale)', 'discharge＋capture spectrum', 'Empty', 'Cleaning'


def flow_default():
    """The flow before anybody set one: only the measurement, as the Recipe “Discharge measurement only”."""
    return {'fill': [], 'measure': True, 'drain': {'on': False, 'ml': 20}, 'clean': False}


def _flow_amount(value, action, label, unit):
    spec = ACTIONS[action]['value']
    low, high, step = Decimal(str(spec['min'])), Decimal(str(spec['max'])), Decimal(1).scaleb(-spec['decimals'])
    number = None if isinstance(value, bool) else _dec(value)
    if number is None or number < low or number > high or number % step:
        raise ValueError(T_FLOW_AMOUNT.format(label=label, low=_fmt(low), high=_fmt(high), unit=unit, step=_fmt(step)))
    return number


def flow_csv(flow, hardware):
    """(CSV text of the Recipe, the flow as it is kept) for the four stages of the schedule page:

        {'fill': [{'pump': 1, 'mode': 'ml' | 'g', 'amount': 10}, …],     pumps by their number on the screen
         'measure': True, 'drain': {'on': True, 'ml': 20}, 'clean': False}

    Add liquid: the pumps that stop on the scale go first, one after the other (nothing else may turn while the scale
    decides); then the pumps with a volume, as one Simultaneous quantitative step. Empty is a volume dose of the board's OUT pump.
    ValueError(text) says what is wrong; the text is checked again by validate() like any other Recipe.
    """
    board = _Board(hardware)
    if not isinstance(flow, dict) or set(flow) - {'fill', 'measure', 'drain', 'clean'}:
        raise ValueError(T_FLOW_FORMAT)
    fill, drain = flow.get('fill', []), flow.get('drain', {})
    if (not isinstance(fill, list) or len(fill) > TOGETHER_MAX or not isinstance(drain, dict)
            or not all(isinstance(flow.get(key, False), bool) for key in ('measure', 'clean'))
            or not isinstance(drain.get('on', False), bool)):
        raise ValueError(T_FLOW_FORMAT)
    by_pump = {facts['pump']: motor for motor, facts in board.motors.items()}
    out_motor = board.clean['out_motor'] if board.clean else None
    kept, weighed, volumes = [], [], []
    for entry in fill:
        if not isinstance(entry, dict) or set(entry) != {'pump', 'mode', 'amount'} or entry['mode'] not in ('ml', 'g'):
            raise ValueError(T_FLOW_FORMAT)
        pump = entry['pump']
        if not board.ok:
            raise ValueError(T_FLOW_NO_BOARD)
        if isinstance(pump, bool) or not isinstance(pump, int) or pump not in by_pump:
            raise ValueError(T_FLOW_PUMP.format(pump=pump))
        motor = by_pump[pump]
        if motor == out_motor:
            raise ValueError(T_FLOW_DRAIN_PUMP.format(pump=pump))
        if any(row['pump'] == pump for row in kept):
            raise ValueError(T_FLOW_PUMP_TWICE.format(pump=pump))
        weight = entry['mode'] == 'g'
        number = _flow_amount(entry['amount'], 'dose_weight' if weight else 'dose_volume', board.motors[motor]['label'],
                              'g' if weight else 'mL')
        kept.append({'pump': pump, 'mode': entry['mode'], 'amount': _num(number)})
        (weighed if weight else volumes).append((board.target(motor), _fmt(number)))
    kept.sort(key=lambda row: row['pump'])
    weighed.sort(key=lambda pair: int(pair[0][1:]))
    volumes.sort(key=lambda pair: int(pair[0][1:]))
    rows = [['dose_weight', target, amount, 'g', N_FILL_WEIGHT] for target, amount in weighed]
    if len(volumes) == 1:
        rows.append(['dose_volume', volumes[0][0], volumes[0][1], 'mL', N_FILL])
    elif volumes:
        rows.append(['dose_together', '+'.join(target for target, _ in volumes),
                     '+'.join(amount for _, amount in volumes), 'mL', N_FILL])
    if flow.get('measure', False):
        rows.append(['plasma_run', '', '', '', N_MEASURE])
    drain_on = drain.get('on', False)
    drain_ml = _flow_amount(drain.get('ml', 20), 'dose_volume', N_DRAIN, 'mL')
    if drain_on:
        if not board.ok:
            raise ValueError(T_FLOW_NO_BOARD)
        if out_motor is None or out_motor not in board.motors:
            raise ValueError(T_FLOW_NO_DRAIN)
        rows.append(['dose_volume', board.target(out_motor), _fmt(drain_ml), 'mL', N_DRAIN])
    if flow.get('clean', False):
        if not board.ok:
            raise ValueError(T_FLOW_NO_BOARD)
        rows.append(['clean', '', '', '', N_CLEAN])
    if not rows:
        raise ValueError(T_FLOW_EMPTY)
    normal = {'fill': kept, 'measure': bool(flow.get('measure', False)),
              'drain': {'on': bool(drain_on), 'ml': _num(drain_ml)}, 'clean': bool(flow.get('clean', False))}
    return compose(rows), normal


# The step list of the schedule page (AMENDMENTS M23): rows of this language, written there one step at a time
# without the maintenance PIN. Only these actions; everything else stays with Maintenance › Recipe.
FLOW_ACTIONS = ('dose_volume', 'dose_together', 'dose_weight', 'plasma_run', 'clean', 'delay',
                'syringe_init', 'syringe_speed', 'syringe_aspirate', 'syringe_dispense')
FLOW_STEPS_MAX = 60
FLOW_STEPS_NAME = 'Process: Set by yourself ({count} step)'
T_FLOW_STEPS_MANY = 'has the most processes {count} step'
T_FLOW_STEP_ACTION = 'Steps {step}: There is no such step on this page'
T_FLOW_NO_STEPS = 'No steps yet: Click ”＋ New step”'
LINE_WORDS = re.compile(r'No. ([0-9]+) List')
# What validate() says, in the words of that page (the names of its steps instead of the names of the actions).
FLOW_WORDS = (
    (T_EMPTY, T_FLOW_NO_STEPS),
    ('Before using the syringe pump, you must first syringe_init', 'Before using the syringe pump, there is a step ”Syringe Pump: Initialization”'),
    ('please first syringe_dispense', 'Please add the step ”Syringe Pump: Inject Liquid” first to push it out'),
    ('this Recipe has no measurement steps (plasma_run) ', 'There is no ”plasma” step in this process'),
    ('this Recipe One round will discharge', 'This process will discharge in one round'),
)


def flow_default_steps():
    """The step list before anybody wrote one: only the measurement, as the Recipe “Discharge measurement only”."""
    return [['plasma_run', '', '', '', '']]


def flow_steps_csv(steps):
    """(CSV text of the Recipe, the rows as they are kept) for the step list of the schedule page:

        [[action, target, value, unit, note], …]        the five cells of a Recipe row, as strings

    ValueError(text) for what is not a step list: not a list of such rows, more than FLOW_STEPS_MAX of them, an
    action that page does not offer. Whether the steps make a round that can run (a pump that is not there, a
    syringe that is pushed before it was filled) is validate()'s to say: a list that is being written is kept
    as it stands, and the page says what is still wrong.
    """
    if not isinstance(steps, (list, tuple)):
        raise ValueError(T_FLOW_FORMAT)
    if len(steps) > FLOW_STEPS_MAX:
        raise ValueError(T_FLOW_STEPS_MANY.format(count=FLOW_STEPS_MAX))
    rows = []
    for index, row in enumerate(steps):
        if (not isinstance(row, (list, tuple)) or len(row) != len(HEADER)
                or not all(isinstance(cell, str) for cell in row)):
            raise ValueError(T_FLOW_FORMAT)
        cells = [_clean(cell) for cell in row]
        if cells[0] not in FLOW_ACTIONS:
            raise ValueError(T_FLOW_STEP_ACTION.format(step=index + 1))
        cells[4] = cells[4][:NOTE_MAX]
        rows.append(cells)
    return compose(rows), rows


def flow_stage_steps(flow, hardware):
    """The step list for the four stages of AMENDMENTS M16 (a flow.json written before M23). ValueError as flow_csv()."""
    text, _kept = flow_csv(flow, hardware)
    rows = [list(row) for row in csv.reader(io.StringIO(text))][1:]
    # the note of a stage only said its name (Add liquid, empty…): a step says what it does by itself
    return [row[:4] + [''] for row in rows if len(row) == len(HEADER)]


def flow_words(text):
    """A sentence of validate() about the step list of the schedule page, in the words of that page: “No. 6 column” (the
    row of the CSV, whose first row is the header) is “Steps 5” there."""
    text = LINE_WORDS.sub(lambda found: 'Steps %d' % (int(found.group(1)) - 1), str(text or ''))
    for old, new in FLOW_WORDS:
        text = text.replace(old, new)
    return text


def as_flow(checked):
    """validate()'s result for the Recipe of the schedule page's step list, its sentences in the words of that page."""
    return dict(checked, error=flow_words(checked.get('error')),
                warnings=[flow_words(text) for text in checked.get('warnings') or []])


def actions_view(hardware, syringe_ul=DEFAULT_SYRINGE_UL):
    """bootstrap.recipe.actions (M4.1): what the step editor needs per action, in the order of SPEC A.2.

    The upper limits of the pump flow, the syringe volume and the syringe speed are the ones that apply now
    (validate() checks again). nano_stop is left out: it is power_off under its old name.
    """
    board = _Board(hardware, syringe_ul)
    view = []
    for action, facts in ACTIONS.items():
        if facts['alias']:
            continue
        value = facts['value']
        if value is not None:
            top = board.top(action)
            value = dict(value, max=_num(top) if top is not None else value['max'])
        view.append({'action': action, 'name': facts['name'], 'target': facts['target'], 'value': value,
                     'group': facts['group']})
    return view


# ---------------------------------------------------------------------------------------------------------
# Rows: R1 and R2
# ---------------------------------------------------------------------------------------------------------
def _check_cells(row, facts, board):
    """R2 for one row: target, value, unit as in SPEC A.2. Returns the message ('' = fine).

    Fills row['motor'] / ['position'] / ['number'] / ['count'] on the way.
    """
    line, action, name = row['line'], row['action'], facts['name']
    target, value, unit = row['target'], row['value'], row['unit']
    if facts['board'] and not board.ok:
        return T_NO_BOARD.format(line=line, name=name)
    if facts['target'] == 'motors':
        low, high, step, _cap = board.bounds(action)
        names, amounts = target.split('+'), value.split('+')
        motors = [board.targets.get(item.strip()) for item in names]
        if len(names) < 2 or len(names) > TOGETHER_MAX or None in motors or len(set(motors)) != len(motors):
            return T_TOGETHER_TARGET.format(line=line, name=name)
        if len(amounts) != len(names):
            return T_TOGETHER_VALUE.format(line=line, name=name)
        out = board.clean['out_motor'] if board.clean else None
        if out in motors:                  # the pump that empties the cell turns alone
            return T_TOGETHER_DRAIN.format(line=line, label=board.motors[out]['label'])
        numbers = [_number(item.strip()) for item in amounts]
        if any(number is None or number < low or number > high for number in numbers):
            return T_RANGE.format(line=line, name=name, low=_fmt(low), high=_fmt(high))
        if any(number % step for number in numbers):
            return T_STEP.format(line=line, name=name, step=_fmt(step))
        if unit != facts['value']['unit']:
            return T_UNIT.format(line=line, name=name, unit=facts['value']['unit'])
        row['motors'], row['numbers'] = motors, numbers
        return ''
    if facts['target'] == 'motor':
        if target not in board.targets:
            return T_TARGET.format(line=line, name=name, targets=board.motor_range())
        row['motor'] = board.targets[target]
    elif facts['target'] == 'valve':
        if target not in board.valves:
            return T_TARGET.format(line=line, name=name, targets=board.valve_range())
        row['position'] = board.valves[target]
    elif target:
        return T_TARGET_EMPTY.format(line=line, name=name)
    spec = facts['value']
    if spec is None:
        if value:
            return T_VALUE_EMPTY.format(line=line, name=name)
    elif action == 'repeat_start':
        if not COUNT.fullmatch(value) or not 1 <= int(value) <= LIMITS['repeat']:
            return T_REPEAT_COUNT.format(line=line)
        row['count'] = int(value)
    else:
        low, high, step, cap = board.bounds(action)
        number = _number(value)
        if number is None or number < low or (number > cap and action != 'syringe_speed'):
            return T_RANGE.format(line=line, name=name, low=_fmt(low), high=_fmt(high))
        if number > cap:               # too fast for any syringe: the sentence of R8
            return T_SYRINGE_SPEED.format(line=line, top=_fmt(high))
        if number % step:              # the number is within its limits here: the remainder always exists
            return T_STEP.format(line=line, name=name, step=_fmt(step))
        row['number'] = number
    want = spec['unit'] if spec else ''
    if unit != want:
        return (T_UNIT if want else T_UNIT_EMPTY).format(line=line, name=name, unit=want)
    return ''


def _parse(text, board):
    check = _Check()
    rows, tree = [], []
    size = None
    if isinstance(text, str):
        try:
            size = len(text.encode('utf-8'))
        except UnicodeEncodeError:
            size = None
    if size is None:
        check.fail(T_UNREADABLE)
    elif size > LIMITS['bytes']:
        check.fail(T_TOO_BIG)
    else:
        stack = [tree]     # the item lists of the open repeat blocks; stack[0] is the Recipe itself
        opened = []        # lines of the repeat_start rows that are still open
        counted = line = 0
        try:
            for line, record in enumerate(csv.reader(io.StringIO(text.lstrip(BOM), newline='')), 1):
                cells = [_clean(cell) for cell in record]
                if line == 1:
                    if tuple(cells) != HEADER:
                        check.fail(T_HEADER)
                        break
                    continue
                if not record:                      # a blank line: not a row (as the original)
                    continue
                counted += 1
                if counted > LIMITS['rows']:
                    check.fail(T_TOO_MANY_ROWS)
                    break
                if not any(cells):                  # an empty row is skipped
                    continue
                row = dict(zip(HEADER, (cells + [''] * len(HEADER))[:len(HEADER)]))
                row.update(line=line, note=row['note'][:NOTE_MAX], name='', kind='step', valid=False, raw=cells,
                           motor=None, motors=None, numbers=None, position=None, number=None, count=1)
                rows.append(row)
                if len(cells) != len(HEADER):
                    check.fail(T_COLUMNS.format(line=line), line)
                    continue
                row['raw'] = cells[:4]
                action = row['action']
                facts = ACTIONS.get(action)
                if facts is None:
                    shown = action if len(action) <= 40 else action[:40] + '…'
                    check.fail(T_ACTION.format(line=line, action=shown), line)
                    continue
                row['name'] = facts['name']
                message = _check_cells(row, facts, board)
                row['valid'] = not message
                if message:
                    check.fail(message, line)
                if action == 'repeat_start':
                    row['kind'] = 'start'
                    if len(stack) > LIMITS['nesting']:
                        # Too deep: refused, and the tree does not grow deeper than the limit either.
                        check.fail(T_NESTING, line)
                        stack.append(stack[-1])
                    else:
                        block = {'kind': 'block', 'count': row['count'], 'line': line, 'items': []}
                        stack[-1].append(block)
                        stack.append(block['items'])
                    opened.append(line)
                elif action == 'repeat_end':
                    row['kind'] = 'end'
                    if len(stack) > 1:
                        stack.pop()
                        opened.pop()
                    elif not message:
                        check.fail(T_REPEAT_END.format(line=line), line)
                elif not message:
                    stack[-1].append(row)
            else:
                if line == 0:
                    check.fail(T_HEADER)            # nothing at all in the file
                elif opened:
                    check.fail(T_REPEAT_OPEN.format(line=opened[0]), opened[0])
        except csv.Error:                           # e.g. one cell of more than 131072 characters
            check.fail(T_UNREADABLE)
    return {'rows': rows, 'tree': tree, 'error': check.error, 'error_line': check.line}


def parse(text, hardware=None, syringe_ul=DEFAULT_SYRINGE_UL):
    """The source rows and the repeat tree of a CSV text (R1, R2). Never raises for what a file holds.

    Returns {'rows', 'tree', 'error', 'error_line'}. A row is a dict: line, action, target, value, unit,
    note, name, kind ('step' | 'start' | 'end'), valid, raw, motor, position, number (Decimal), count.
    The tree holds the valid step rows and {'kind': 'block', 'count', 'line', 'items'} for a repeat.
    validate() is what the program uses; this is its first half.
    """
    return _parse(text, _Board(hardware, syringe_ul))


def _count(nodes):
    return sum(node['count'] * _count(node['items']) if node['kind'] == 'block' else 1 for node in nodes)


def expand(tree):
    """The flat step list of a repeat tree. ValueError(T_TOO_MANY_STEPS) beyond 2000 steps.

    Every step is a copy of its row plus n (1…), round and rounds. Only the outermost repeat counts as a
    displayed round, as in the original: steps outside any repeat are round 1 of 1.
    """
    if _count(tree) > LIMITS['steps']:
        raise ValueError(T_TOO_MANY_STEPS)
    steps = []

    def walk(nodes, info):
        for node in nodes:
            if node['kind'] == 'block':
                for index in range(node['count']):
                    walk(node['items'], info or (index + 1, node['count']))
            else:
                step = dict(node, n=len(steps) + 1, planned=None, phase=0, text='')
                step['round'], step['rounds'] = info or (1, 1)
                steps.append(step)

    walk(tree, None)
    return steps


# ---------------------------------------------------------------------------------------------------------
# The walk over the expanded steps: R3 - R10, liquids, warnings
# ---------------------------------------------------------------------------------------------------------
def _walk(steps, board, check):
    """Preflight state of SPEC A.3: which motors run (and for how long), syringe initialised, syringe content.

    After the first problem the walk continues with the most plausible state, so that the on-times, the
    liquids and the sentences of the later rows are still there for the screen. Only the first is reported.
    """
    running = {}                       # motor -> {'line', 'planned', 'step', 'flow'}
    initialised, held = False, Decimal(0)        # syringe: content in mL
    waited = Decimal(0)
    planned = {}                       # line -> longest planned on-time of that source row
    amounts = {}                       # motor -> {'ml', 'g', 'approx'} in the order of first use
    reverse, no_fluid = {}, {}         # line -> motor label (W3, W4), once per source row

    def amount(motor):
        return amounts.setdefault(motor, {'ml': None, 'g': None, 'approx': False})

    def add(motor, key, number):
        entry = amount(motor)
        entry[key] = (entry[key] or Decimal(0)) + number

    for step in steps:
        action, line, number = step['action'], step['line'], step['number']
        motor = step['motor']
        label = board.motors[motor]['label'] if motor is not None else ''
        if action == 'delay':
            waited += number
            for run in running.values():
                run['planned'] += number
            continue
        if action in RUN_ACTIONS:
            if action == 'pump_reverse':
                reverse.setdefault(line, label)
            if motor in running:                                                    # R4
                check.fail(T_RUNNING.format(line=line, motor=label), line)
                continue
            group = board.motors[motor]['group']
            for other in running:                                                   # R3
                if board.motors[other]['group'] == group:
                    check.fail(T_GROUP.format(line=line, group=board.motors[motor]['group_label'],
                                              motor=board.motors[other]['label']), line)
                    break
            running[motor] = {'line': line, 'planned': Decimal(0), 'step': step, 'flow': number}
            amount(motor)
            continue
        if action == 'pump_off':
            run = running.pop(motor, None)
            if run is None:                                                         # R4
                check.fail(T_NOT_RUNNING.format(line=line, motor=label), line)
                continue
            seconds = run['planned']
            run['step']['planned'] = seconds
            planned[run['line']] = max(seconds, planned.get(run['line'], seconds))
            if seconds > RUN_MAX_S:                                                 # R6
                check.fail(T_RUN_LONG.format(line=run['line'], motor=label, seconds=_fmt(seconds)), run['line'])
            elif seconds < RUN_MIN_S:
                check.fail(T_RUN_ZERO.format(line=run['line'], motor=label), run['line'])
            add(motor, 'ml', run['flow'] * seconds / 60)
            amount(motor)['approx'] = True
            continue
        # Everything else needs every pump stopped: the static half of "pumps and high voltage never together".
        if running:                                                                 # R5
            check.fail(T_PUMPS_FIRST.format(line=line, name=step['name']), line)
        if action in DOSE_ACTIONS:
            add(motor, 'ml', number)
            if action == 'dose_reverse':
                reverse.setdefault(line, label)
        elif action == 'dose_together':
            for one, millilitres in zip(step['motors'], step['numbers']):
                add(one, 'ml', millilitres)
        elif action == 'dose_weight':
            if not board.weighing:                                                  # R9
                check.fail(T_WEIGHT_OFF.format(line=line), line)
            add(motor, 'g', number)
            if not board.motors[motor]['fluid']:
                no_fluid.setdefault(line, label)
        elif action == 'syringe_init':                                              # R8 from here
            if held > 0:
                check.fail(T_SYRINGE_NOT_EMPTY.format(line=line, have=_fmt(held)), line)
            initialised, held = True, Decimal(0)
        elif action == 'syringe_speed':
            if not initialised:
                check.fail(T_SYRINGE_INIT.format(line=line), line)
            elif number > board.speed_max:
                check.fail(T_SYRINGE_SPEED.format(line=line, top=_fmt(board.speed_max)), line)
        elif action in SYRINGE_MOVES:
            size = board.size_ml
            if not initialised:
                check.fail(T_SYRINGE_INIT.format(line=line), line)
            elif number > size:        # the range of R2 for the saved syringe size (R11)
                low = board.bounds(action)[0]
                check.fail(T_RANGE.format(line=line, name=step['name'], low=_fmt(low), high=_fmt(size)), line)
            elif action == 'syringe_aspirate' and held + number > size:
                check.fail(T_SYRINGE_OVER.format(line=line, have=_fmt(held), size=_fmt(size)), line)
            elif action == 'syringe_dispense' and number > held:
                check.fail(T_SYRINGE_UNDER.format(line=line, have=_fmt(held), want=_fmt(number)), line)
            held = min(size, held + number) if action == 'syringe_aspirate' else max(Decimal(0), held - number)
    if running:                                                                     # R7
        left = [T_LEFT_ON_PUMP.format(motor=board.motors[motor]['label'], line=run['line'])
                for motor, run in running.items()]
        check.fail(T_LEFT_ON.format(pumps=', '.join(left)), next(iter(running.values()))['line'])
    if held > 0:                                                                    # R8
        check.fail(T_SYRINGE_LEFT.format(have=_fmt(held)))
    if waited > LIMITS['delay_total_s']:                                            # R10
        check.fail(T_DELAY_TOTAL)
    thousandth = Decimal('0.001')
    liquids = [{'motor': motor, 'label': board.motors[motor]['label'],
                'ml': None if entry['ml'] is None else float(entry['ml'].quantize(thousandth, rounding=ROUND_HALF_UP)),
                'g': None if entry['g'] is None else float(entry['g']), 'approx': entry['approx']}
               for motor, entry in amounts.items() if entry['ml'] is not None or entry['g'] is not None]
    return {'planned': planned, 'delay': waited, 'liquids': liquids, 'reverse': reverse, 'no_fluid': no_fluid}


def _phases(steps, board=None):
    """Display phases: neighbouring steps of one class form one phase; sets step['phase'] (index).
    A pump step of the board's OUT pump is Empty, of every other pump Add liquid."""
    out = board.clean['out_motor'] if board is not None and board.clean else None
    classes = [PHASE_DRAIN if out is not None and step.get('motor') == out and ACTIONS[step['action']]['phase'] == PHASE_FEED
               else ACTIONS[step['action']]['phase'] for step in steps]
    upcoming = None
    for index in range(len(steps) - 1, -1, -1):      # syringe_speed takes the class of the next device step
        action = steps[index]['action']
        if action == 'syringe_speed':
            classes[index] = upcoming or PHASE_FEED
        elif action != 'delay':
            upcoming = classes[index]
    previous = None
    for index, step in enumerate(steps):             # delay takes the class of the previous step
        if step['action'] == 'delay':
            classes[index] = previous or PHASE_WAIT
        previous = classes[index]
    phases = []
    for index, step in enumerate(steps):
        if phases and phases[-1]['name'] == classes[index]:
            phases[-1]['to'] = index + 1
        else:
            phases.append({'name': classes[index], 'from': index + 1, 'to': index + 1})
        step['phase'] = len(phases) - 1
    return phases


def _sentence(item, board, planned=None):
    """The step sentence of SPEC A.3 for a checked row or an expanded step."""
    action = item['action']
    amount = _fmt(item['number']) if item['number'] is not None else ''
    motor = board.motors[item['motor']]['label'] if item['motor'] is not None else ''
    if action == 'delay':
        return S_DELAY.format(amount=amount)
    if action in RUN_ACTIONS:
        text = S_RUN.format(motor=motor, way=S_FORWARD if action == 'pump_on' else S_REVERSE, amount=amount)
        # The on-time is only known when the walk reached the pump_off of this run.
        return text + (S_RUN_LIMIT.format(seconds=_fmt(planned)) if planned is not None else '')
    if action == 'pump_off':
        return S_STOP.format(motor=motor)
    if action == 'dose_volume':
        return S_DOSE.format(motor=motor, amount=amount)
    if action == 'dose_reverse':
        return S_DOSE_REVERSE.format(motor=motor, amount=amount)
    if action == 'dose_weight':
        return S_DOSE_WEIGHT.format(motor=motor, amount=amount)
    if action == 'dose_together':
        return S_TOGETHER.format(parts=', '.join(
            S_TOGETHER_PART.format(motor=board.motors[one]['label'], amount=_fmt(millilitres))
            for one, millilitres in zip(item['motors'], item['numbers'])))
    if action == 'clean' and board.clean:
        names = [board.motors[key]['short'] if key in board.motors else '?'
                 for key in (board.clean['water_motor'], board.clean['out_motor'])]
        return S_CLEAN.format(water=names[0], out=names[1], seconds=_fmt(board.clean['total_s']))
    if action == 'syringe_speed':
        return S_SYRINGE_SPEED.format(amount=amount)
    if action == 'syringe_aspirate':
        return S_ASPIRATE.format(position=item['position'], amount=amount)
    if action == 'syringe_dispense':
        return S_DISPENSE.format(position=item['position'], amount=amount)
    if action == 'scale_read':
        return S_SCALE_NAMED.format(note=item['note']) if item['note'] else S_SCALE
    return S_FIXED[action]


def _row_text(row, board, planned):
    """What the step editor shows for a source row: its sentence, or what the row holds when it is wrong."""
    if not row['valid']:
        text = RAW_JOIN.join(cell for cell in row['raw'] if cell)
        return text if len(text) <= NOTE_MAX else text[:NOTE_MAX] + '…'
    if row['kind'] == 'start':
        return S_REPEAT_START.format(count=row['count'])
    if row['kind'] == 'end':
        return S_REPEAT_END
    return _sentence(row, board, planned)


def _blank():
    return {'valid': False, 'error': '', 'error_line': None, 'warnings': [], 'step_count': 0, 'rounds': 0,
            'delay_seconds': 0, 'phases': [], 'uses_pump': False, 'uses_motors': [], 'uses_syringe': False,
            'uses_scale': False, 'uses_rpi': False, 'uses_plasma': False, 'plasma_count': 0, 'liquids': [],
            'steps': [], 'rows': []}


def validate(text, hardware, syringe_ul=DEFAULT_SYRINGE_UL, encoding='utf-8'):
    """Preflight of a Recipe (SPEC A.3): no hardware is touched. See the module notes for the result.

    ``hardware`` = bootstrap.hardware (None: pc\\vendor incomplete), ``syringe_ul`` = the saved syringe size.
    Called at import, publish, list, preview and again at every start: what depends on the syringe size is
    judged with the size given now (R11).
    """
    board = _Board(hardware, syringe_ul)
    parsed = _parse(text, board)
    check = _Check(parsed['error'], parsed['error_line'])
    steps = []
    if not check.error:
        try:
            steps = expand(parsed['tree'])
        except ValueError as exc:
            check.fail(str(exc))
        else:
            if not steps:
                check.fail(T_EMPTY)
    result = _blank()
    planned, warnings = {}, []
    if steps:
        walked = _walk(steps, board, check)
        planned = walked['planned']
        phases = _phases(steps, board)
        for step in steps:
            step['text'] = _sentence(step, board, step['planned'])
            step['target_label'] = (board.motors[step['motor']]['label'] if step['motor'] is not None
                                    else ', '.join(board.motors[one]['label'] for one in step['motors']) if step['motors']
                                    else S_VALVE.format(position=step['position']) if step['position'] is not None
                                    else '')
        actions = [step['action'] for step in steps]
        plasma = actions.count('plasma_run')
        motors = {step['motor'] for step in steps if step['motor'] is not None}
        for step in steps:
            motors.update(step['motors'] or ())
        if 'clean' in actions and board.clean:
            motors.update((board.clean['water_motor'], board.clean['out_motor']))
        if plasma > 1:                                                              # W2
            warnings.append(W_PLASMA_COUNT.format(count=plasma))
        warnings.extend(W_REVERSE.format(line=line, motor=motor)                    # W3
                        for line, motor in sorted(walked['reverse'].items()))
        warnings.extend(W_NO_FLUID.format(line=line, motor=motor)                   # W4
                        for line, motor in sorted(walked['no_fluid'].items()))
        if not plasma:                                                              # W5
            warnings.append(W_NO_PLASMA)
        result.update(
            step_count=len(steps), rounds=max(step['rounds'] for step in steps), delay_seconds=_num(walked['delay']),
            phases=phases, uses_pump=any(ACTIONS[action]['board'] for action in actions), uses_motors=sorted(motors),
            uses_syringe=any(action.startswith('syringe_') for action in actions),
            uses_scale=any(action in ('dose_weight', 'scale_read') for action in actions),
            uses_rpi=any(ACTIONS[action]['rpi'] for action in actions), uses_plasma=plasma > 0, plasma_count=plasma,
            liquids=walked['liquids'],
            steps=[{'n': step['n'], 'line': step['line'], 'action': step['action'], 'name': step['name'],
                    'target': step['target'], 'target_label': step['target_label'], 'value': step['value'],
                    'unit': step['unit'], 'note': step['note'], 'round': step['round'], 'rounds': step['rounds'],
                    'phase': step['phase'], 'text': step['text'],
                    # the pin of the board (what PumpBoard wants); for dose_together the pins and their mL
                    'motor': step['motor'], 'motors': step['motors'],
                    'amounts': None if step['numbers'] is None else [_num(number) for number in step['numbers']],
                    'planned_s': None if step['planned'] is None else _num(step['planned'])} for step in steps])
    if str(encoding).lower() == 'cp950':                                            # W6
        warnings.append(CP950_WARNING)
    # One item per source row (M4.1), whether the Recipe is valid or not; empty rows are not rows.
    rows = [{'line': row['line'], 'action': row['action'], 'target': row['target'], 'value': row['value'],
             'unit': row['unit'], 'note': row['note'], 'name': row['name'],
             'text': _row_text(row, board, planned.get(row['line'])), 'ok': row['line'] != check.line}
            for row in parsed['rows']]
    result.update(valid=not check.error, error=check.error, error_line=check.line, warnings=warnings, rows=rows)
    return result


def estimate(result, hardware, rpi_estimate_s, syringe_speed, integration_ms=None):
    """Seconds one round is expected to take (SPEC A.5), for the preview and the interval check.

    ``result`` = what validate() returned (an invalid one counts the steps it has), ``rpi_estimate_s`` = the
    RPi's status.estimate_s (None: 30 s per plasma_run), ``syringe_speed`` = the saved speed in mL/s (a
    syringe_speed row replaces it for the moves that follow), ``integration_ms`` = integration time for
    spectrum_capture (None: 1000 ms). A motor with a linked valve adds the valve's open / close time.
    """
    board = _Board(hardware)
    plasma = _positive(rpi_estimate_s, DEFAULT_PLASMA_S)
    speed = _positive(syringe_speed, DEFAULT_SYRINGE_SPEED_ML_S)
    capture = 3.0 * _positive(integration_ms, DEFAULT_INTEGRATION_MS) / 1000.0 + 8.0
    total = 0.0
    for step in (result or {}).get('steps') or ():
        action = step.get('action')
        amount = _positive(step.get('value') or None, 0.0)
        motor = board.motors.get(board.targets.get(step.get('target')))
        valve = motor is not None and motor['valve'] is not None
        open_s, close_s = (board.open_s, board.close_s) if valve else (0.0, 0.0)
        if action == 'delay':
            total += amount
        elif action in RUN_ACTIONS:
            total += 1.5 + open_s
        elif action == 'pump_off':
            total += 1.0 + close_s
        elif action in DOSE_ACTIONS:
            total += board.dose_seconds(amount, board.targets.get(step.get('target'))) * 1.1 + 1.0 + open_s + close_s
        elif action == 'dose_together':
            # The pumps of one driver group follow one another; the groups run side by side.
            groups = {}
            for one, millilitres in zip(step.get('motors') or (), step.get('amounts') or ()):
                facts = board.motors.get(one)
                if facts is None:
                    continue
                valve_s = (board.open_s + board.close_s) if facts['valve'] is not None else 0.0
                groups[facts['group']] = (groups.get(facts['group'], 0.0)
                                          + board.dose_seconds(_positive(millilitres, 0.0), one) * 1.1 + 1.0 + valve_s)
            total += max(groups.values(), default=0.0)
        elif action == 'dose_weight':
            total += amount / (_positive(board.fast_flow, 40.0) / 60.0) * 1.3 + 12.0
        elif action == 'clean':
            total += (float(board.clean['total_s']) if board.clean else 0.0) + 2.0
        elif action == 'syringe_speed':
            total += 0.5
            speed = amount or speed
        elif action in SYRINGE_MOVES:
            total += amount / speed + 2.0
        elif action == 'plasma_run':
            total += plasma
        elif action == 'spectrum_capture':
            total += capture
        else:
            total += FIXED_S.get(action, 0.0)
    return round(total, 1)
