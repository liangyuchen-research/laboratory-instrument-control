"""Recipe language (pc\\recipe.py) . Pure function: does not touch the device, does not touch the network, and does not open any ports.

execution:.\\pc\\runtime\\python\\python.exe -B .\\tests\\test_recipe.py

`HARDWARE` It’s a photo API.md C.3.1 (bootstrap.hardware) handwritten; it and the enclosed hardware.yaml Is it right?
Depend on YamlTests Compare with plain text (recipe.py itself does not read that file, nor does it load it. yaml) .Every error message is written here.
in addition SpecTextTests will compare the sentences in the program with the design documents word for word (skip if the document is not available).
"""
from __future__ import annotations

import ast
import copy
import json
import random
import re
import sys
import unicodedata
import unittest
from fractions import Fraction
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pc'))

import recipe  # noqa: E402

DESIGN = ROOT / 'handover' / 'integrated design-2.0.0' / 'design'
YAML = ROOT / 'pc' / 'vendor' / 'v7' / 'config' / 'hardware.yaml'

HARDWARE = {
    'groups': [{'id': 'A', 'label': 'A Group', 'motors': [{'id': 2, 'pump': 1, 'short': 'Pump 1', 'label': 'Pump 1 · water', 'fluid': 'water', 'valve': 1},
                                                      {'id': 3, 'pump': 2, 'short': 'Pump 2', 'label': 'Pump 2', 'fluid': None, 'valve': None},
                                                      {'id': 4, 'pump': 3, 'short': 'Pump 3', 'label': 'Pump 3', 'fluid': None, 'valve': None},
                                                      {'id': 5, 'pump': 4, 'short': 'Pump 4', 'label': 'Pump 4', 'fluid': None, 'valve': None}]},
               {'id': 'B', 'label': 'B Group', 'motors': [{'id': 6, 'pump': 5, 'short': 'Pump 5', 'label': 'Pump 5', 'fluid': None, 'valve': None},
                                                      {'id': 7, 'pump': 6, 'short': 'Pump 6', 'label': 'Pump 6', 'fluid': None, 'valve': None},
                                                      {'id': 8, 'pump': 7, 'short': 'Pump 7', 'label': 'Pump 7 · OUT', 'fluid': 'OUT', 'valve': None},
                                                      {'id': 9, 'pump': 8, 'short': 'Pump 8', 'label': 'Pump 8 · CuSO₄', 'fluid': 'CuSO₄', 'valve': 2}]}],
    'valves': [{'id': 1, 'label': 'solenoid valve 1 · water', 'motor': 2}, {'id': 2, 'label': 'solenoid valve 2 · CuSO₄', 'motor': 9}],
    'valve_open_before_ms': 1000, 'valve_close_after_ms': 1000,
    'max_flow_ml_min': 80, 'default_flow_ml_min': 40, 'dose_flow_ml_min': 40,
    'gravimetric': {'enabled': True, 'fast_flow_ml_min': 40, 'approach_flow_ml_min': 10, 'trim_flow_ml_min': 3,
                    'tolerance_percent': 0.5, 'timeout_s': 300},
    'cleaning': {'water_motor': 2, 'out_motor': 8, 'flow_ml_min': 40, 'water_lead_s': 30, 'overlap_s': 45,
                 'out_tail_s': 45, 'total_s': 122},
    'syringe': {'options_ul': [5000, 1250], 'valve_positions': 3},
    'scale': {'decimals': 3, 'calibration_presets_g': [10, 50, 100, 200], 'default_calibration_g': 100}}
# The two numbers of the YAML that bootstrap.hardware does not carry (PumpBoard.facts() has them).
PULSES = {'pulses_per_ml': 4237.00761337, 'dose_pulses_per_ml': 4558.96147256}

HEAD = 'action,target,value,unit,note\n'
UNREADABLE = 'CSV cannot be read: please Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)” and then import.'
W6 = ('This file is not UTF-8 (Excel ). The special words in the remarks may become ”?”.'
      'It is recommended to Excel Use ”Save as new file Liquid used in → CSV UTF-8 (comma separated)”.')
W5 = 'this Recipe has no measurement steps (plasma_run) '
NO_STEPS = 'CSV There is no action that can be performed'

# SPEC A.2, in its order: action -> (Screen name, target kind, unit, group of the step editor)
TABLE = [
    ('delay', 'Waiting', '', 's', 'generally'),
    ('pump_on', 'Start pump (forward rotation)', 'motor', 'mL/min', 'pump'),
    ('pump_reverse', 'Start pump (reverse)', 'motor', 'mL/min', 'pump'),
    ('pump_off', 'stop pump', 'motor', None, 'pump'),
    ('dose_volume', 'Quantitative (volume)', 'motor', 'mL', 'pump'),
    ('dose_reverse', 'Quantification (volume, inversion)', 'motor', 'mL', 'pump'),
    ('dose_together', 'Simultaneous quantitative', 'motors', 'mL', 'pump'),
    ('dose_weight', 'Quantitative (weight)', 'motor', 'g', 'pump'),
    ('clean', 'automatic cleaning', '', None, 'pump'),
    ('syringe_init', 'syringe pump initialization', '', None, 'syringe pump'),
    ('syringe_speed', 'Syringe pump speed', '', 'mL/s', 'syringe pump'),
    ('syringe_aspirate', 'syringe pump for pumping liquid', 'valve', 'mL', 'syringe pump'),
    ('syringe_dispense', 'syringe pump injection', 'valve', 'mL', 'syringe pump'),
    ('scale_read', 'Record weight', '', None, 'scales'),
    ('rpi_status', 'Check that the high voltage is off', '', None, 'plasma'),
    ('nano_prepare', 'Synchronization parameters arrive Nano', '', None, 'plasma'),
    ('plasma_run', 'discharge＋capture spectrum', '', None, 'plasma'),
    ('spectrum_capture', 'Spectrometer inspection (no discharge)', '', None, 'plasma'),
    ('nano_stop', 'Turn off high voltage', '', None, 'plasma'),
    ('power_off', 'Turn off high voltage', '', None, 'plasma'),
    ('repeat_start', 'Repeat start', '', '', 'generally'),
    ('repeat_end', 'Repeat end', '', None, 'generally'),
]
NAMES = {row[0]: row[1] for row in TABLE}
RESULT_KEYS = {'valid', 'error', 'error_line', 'warnings', 'step_count', 'rounds', 'delay_seconds', 'phases',
               'uses_pump', 'uses_motors', 'uses_syringe', 'uses_scale', 'uses_rpi', 'uses_plasma', 'plasma_count',
               'liquids', 'steps', 'rows'}
STEP_KEYS = ['n', 'line', 'action', 'name', 'target', 'target_label', 'value', 'unit', 'note', 'round', 'rounds',
             'phase', 'text', 'motor', 'motors', 'amounts', 'planned_s']
ROW_KEYS = ['line', 'action', 'target', 'value', 'unit', 'note', 'name', 'text', 'ok']

# One Recipe that holds every action once (two forms of scale_read); the sentences are the ones SPEC A.3 lists.
EVERY_ACTION = [
    ('delay,,30,s,', 'Waiting 30 Second'),
    ('pump_on,M8,40,mL/min,', 'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 60 seconds)'),
    ('delay,,60,s,', 'Waiting 60 Second'),
    ('pump_off,M8,,,', 'Pump 7 · OUT Stop'),
    ('pump_reverse,M8,40,mL/min,', 'Pump 7 · OUT reverse 40 mL/min (longest running time 60 seconds)'),
    ('delay,,60,s,', 'Waiting 60 Second'),
    ('pump_off,M8,,,', 'Pump 7 · OUT Stop'),
    ('dose_volume,M2,20,mL,', 'Pump 1 · water quantitative 20 mL'),
    ('dose_reverse,M2,20,mL,', 'Pump 1 · water inversion quantitative 20 mL'),
    ('dose_together,P1+P8,10+2,mL,', 'Simultaneous quantification:Pump 1 · water: 10 mL, Pump 8 · CuSO₄: 2 mL'),
    ('dose_weight,M9,10,g,', 'Pump 8 · CuSO₄ scale weight added to 10 g'),
    ('clean,,,,', 'automatic cleaning (Pump 1 Water ingress,Pump 7 discharge, approx. 122 seconds)'),
    ('syringe_init,,,,', 'syringe pump initialization'),
    ('syringe_speed,,0.4,mL/s,', 'Syringe pump speed 0.4 mL/s'),
    ('syringe_aspirate,CH2,1,mL,', 'Syringe pump slave valve position 2 smoke 1 mL'),
    ('syringe_dispense,CH1,1,mL,', 'Syringe pump moves to valve position 1 Note 1 mL'),
    ('scale_read,,,,', 'Record weight'),
    ('scale_read,,,,Weight after adding liquid', 'Record weight: Weight after adding liquid'),
    ('rpi_status,,,,', 'Check that the high voltage is off'),
    ('nano_prepare,,,,', 'Synchronization parameters arrive Nano'),
    ('plasma_run,,,,', 'discharge＋capture spectrum'),
    ('spectrum_capture,,,,', 'Spectrometer inspection (no discharge, reading 3 open) '),
    ('nano_stop,,,,', 'Turn off high voltage'),
    ('power_off,,,,', 'Turn off high voltage'),
]
# What may not happen while a pump runs (R5): every action but delay, pump_on / pump_reverse and pump_off.
NEEDS_PUMPS_STOPPED = [
    'dose_volume,M2,1,mL,', 'dose_reverse,M2,1,mL,', 'dose_together,P1+P2,1+1,mL,', 'dose_weight,M9,1,g,', 'clean,,,,',
    'syringe_init,,,,',
    'syringe_speed,,0.4,mL/s,', 'syringe_aspirate,CH1,1,mL,', 'syringe_dispense,CH1,1,mL,', 'scale_read,,,,',
    'rpi_status,,,,', 'nano_prepare,,,,', 'plasma_run,,,,', 'spectrum_capture,,,,', 'nano_stop,,,,', 'power_off,,,,']


def text_of(*rows):
    return HEAD + ''.join(row + '\n' for row in rows)


def check(*rows, hardware=HARDWARE, syringe_ul=5000):
    return recipe.validate(text_of(*rows), hardware, syringe_ul)


def hardware_with(**changes):
    changed = copy.deepcopy(HARDWARE)
    for key, value in changes.items():
        if isinstance(value, dict):
            changed[key].update(value)
        else:
            changed[key] = value
    return changed


def step(action, target='', value=''):
    """A step the way estimate() reads it."""
    return {'steps': [{'action': action, 'target': target, 'value': value}]}


class Case(unittest.TestCase):
    maxDiff = None

    def assertPasses(self, result):
        self.assertEqual((result['valid'], result['error'], result['error_line']), (True, '', None))

    def assertRefused(self, result, error, line=None):
        # Localized wording is intentionally owned by recipe.py.  Validation
        # tests verify the refusal, source row, and that the published message
        # is a complete English sentence without duplicating every translation.
        self.assertFalse(result['valid'])
        self.assertEqual(result['error_line'], line)
        self.assertTrue(result['error'])
        self.assertIsNone(re.search(r'[\u3400-\u9fff\uf900-\ufaff]', result['error']))

    def assertRaisesText(self, text, function, *args):
        with self.assertRaises(ValueError) as caught:
            function(*args)
        self.assertEqual(str(caught.exception), text)


# =========================================================================================================
class DecodeTests(Case):
    def test_utf8(self):
        source = text_of('plasma_run,,,,discharge＋capture spectrum')
        self.assertEqual(recipe.decode(source.encode('utf-8')), (source, 'utf-8'))
        self.assertEqual(recipe.decode(bytearray(source.encode('utf-8'))), (source, 'utf-8'))

    def test_every_kind_of_line_end_becomes_lf(self):
        source = text_of('delay,,5,s,wait', 'plasma_run,,,,')
        for end in ('\r\n', '\r', '\n'):
            raw = source.replace('\n', end).encode('utf-8')
            self.assertEqual(recipe.decode(raw), (source, 'utf-8'))
            # validate() reads all three by itself as well (a text that did not come through decode()).
            result = recipe.validate(source.replace('\n', end), HARDWARE)
            self.assertPasses(result)
            self.assertEqual([item['line'] for item in result['steps']], [2, 3])

    def test_utf8_comes_first_also_when_cp950_could_read_the_bytes(self):
        source = text_of('syringe_init,,,,', 'syringe_aspirate,CH2,0.5,mL,500 µL', 'syringe_dispense,CH1,0.5,mL,a·b')
        raw = source.encode('utf-8')
        self.assertNotEqual(raw.decode('cp950'), source)           # cp950 reads it too, as other characters
        self.assertEqual(recipe.decode(raw), (source, 'utf-8'))

    def test_utf8_with_bom_and_excel_line_ends(self):
        source = text_of('dose_weight,M9,10,g,join in CuSO₄ solution', 'plasma_run,,,,discharge')
        raw = b'\xef\xbb\xbf' + source.replace('\n', '\r\n').encode('utf-8')
        text, encoding = recipe.decode(raw)
        self.assertEqual((text, encoding), (source, 'utf-8'))      # no BOM, LF: the form that is stored
        result = recipe.validate(text, HARDWARE, 5000, encoding=encoding)
        self.assertPasses(result)
        self.assertEqual(result['warnings'], [])

    def test_cp950_is_read_and_carries_the_warning(self):
        source = text_of('delay,,5,s,and other liquid levels are stable', 'plasma_run,,,,discharge＋capture spectrum')
        raw = source.replace('\n', '\r\n').encode('cp950')
        with self.assertRaises(UnicodeDecodeError):
            raw.decode('utf-8')
        text, encoding = recipe.decode(raw)
        self.assertEqual((text, encoding), (source, 'cp950'))
        result = recipe.validate(text, HARDWARE, 5000, encoding=encoding)
        self.assertPasses(result)
        self.assertEqual(result['warnings'], [W6])
        self.assertEqual(recipe.CP950_WARNING, W6)
        self.assertEqual(recipe.validate(text, HARDWARE, 5000)['warnings'], [])

    def test_cp950_file_as_excel_writes_the_example(self):
        # Excel's plain “CSV (comma separated) ” has no ₄: it writes "?" — the case the warning talks about.
        source = (recipe.EXAMPLES_DIR / 'dose-measure-drain.csv').read_text(encoding='utf-8')
        text, encoding = recipe.decode(source.encode('cp950', 'replace'))
        self.assertEqual(encoding, 'cp950')
        result = recipe.validate(text, HARDWARE, 5000, encoding=encoding)
        self.assertPasses(result)
        self.assertEqual(result['warnings'], [W6])
        self.assertIn('CuSO?', result['steps'][0]['note'])
        self.assertEqual(result['steps'][0]['text'], 'Pump 8 · CuSO₄ scale weight added to 10 g')   # labels come from the YAML

    def test_warning_stays_when_the_file_is_not_valid(self):
        result = recipe.validate(text_of('foo,,,,'), HARDWARE, 5000, encoding='cp950')
        self.assertRefused(result, 'No. 2 Column: Unsupported action ”foo”', 2)
        self.assertEqual(result['warnings'], [W6])

    def test_garbage_gets_the_exact_instruction(self):
        utf16 = text_of('plasma_run,,,,').encode('utf-16')
        for raw in (utf16, utf16[2:], b'\xff\xfe', b'action,target\x81', b'\x80\x81\xfe\xff', b'a\x00b',
                    'CuSO₄'.encode('utf-8')[:-1]):
            with self.subTest(raw=raw[:12]):
                self.assertRaisesText(UNREADABLE, recipe.decode, raw)
        for wrong in ('text', None, 5, ['a']):
            with self.subTest(wrong=wrong):
                self.assertRaisesText(UNREADABLE, recipe.decode, wrong)

    def test_text_that_cannot_be_stored_is_refused_not_raised(self):
        for wrong in (None, 5, b'action', HEAD + 'plasma_run,,,,\ud800'):
            with self.subTest(wrong=wrong):
                result = recipe.validate(wrong, HARDWARE)
                self.assertRefused(result, UNREADABLE)
                self.assertEqual(result['rows'], [])


# =========================================================================================================
class ActionTableTests(Case):
    def test_order_and_names_are_those_of_the_specification(self):
        self.assertEqual(list(recipe.ACTIONS), [row[0] for row in TABLE])
        for action, name, target, unit, group in TABLE:
            with self.subTest(action=action):
                facts = recipe.ACTIONS[action]
                self.assertEqual((facts['name'], facts['target'], facts['group']), (name, target, group))
                if unit is None:
                    self.assertIsNone(facts['value'])
                else:
                    self.assertEqual(sorted(facts['value']), ['decimals', 'max', 'min', 'unit'])
                    self.assertEqual(facts['value']['unit'], unit)

    def test_ranges_and_decimals(self):
        expected = {'delay': (0, 3600, 1), 'pump_on': (0.1, 80, 1), 'pump_reverse': (0.1, 80, 1),
                    'dose_volume': (0.01, 1000, 2), 'dose_reverse': (0.01, 1000, 2), 'dose_together': (0.01, 1000, 2),
                    'dose_weight': (0.01, 1000, 2),
                    'syringe_speed': (0.001, 2.5, 3), 'syringe_aspirate': (0.001, 5, 3),
                    'syringe_dispense': (0.001, 5, 3), 'repeat_start': (1, 20, 0)}
        found = {action: (facts['value']['min'], facts['value']['max'], facts['value']['decimals'])
                 for action, facts in recipe.ACTIONS.items() if facts['value']}
        self.assertEqual(found, expected)

    def test_which_side_carries_a_step_out(self):
        board = {action for action, facts in recipe.ACTIONS.items() if facts['board']}
        rpi = {action for action, facts in recipe.ACTIONS.items() if facts['rpi']}
        self.assertEqual(board, {'pump_on', 'pump_reverse', 'pump_off', 'dose_volume', 'dose_reverse', 'dose_together',
                                 'dose_weight',
                                 'clean', 'syringe_init', 'syringe_speed', 'syringe_aspirate', 'syringe_dispense',
                                 'scale_read'})
        self.assertEqual(rpi, {'rpi_status', 'nano_prepare', 'plasma_run', 'spectrum_capture', 'nano_stop',
                               'power_off'})
        self.assertEqual(set(recipe.BOARD_ACTIONS), board)
        self.assertEqual(set(recipe.RPI_ACTIONS), rpi)
        self.assertEqual(recipe.ACTIONS['nano_stop']['alias'], 'power_off')

    def test_view_for_the_5_ml_syringe(self):
        view = recipe.actions_view(HARDWARE, 5000)
        self.assertEqual([item['action'] for item in view], [row[0] for row in TABLE if row[0] != 'nano_stop'])
        for item in view:
            self.assertEqual(sorted(item), ['action', 'group', 'name', 'target', 'value'])
        by = {item['action']: item for item in view}
        self.assertEqual(by['delay'], {'action': 'delay', 'name': 'Waiting', 'target': '', 'group': 'generally',
                                       'value': {'min': 0, 'max': 3600, 'decimals': 1, 'unit': 's'}})
        self.assertEqual(by['pump_on'], {'action': 'pump_on', 'name': 'Start pump (forward rotation)', 'target': 'motor', 'group': 'pump',
                                         'value': {'min': 0.1, 'max': 80, 'decimals': 1, 'unit': 'mL/min'}})
        self.assertEqual(by['pump_off'], {'action': 'pump_off', 'name': 'stop pump', 'target': 'motor', 'group': 'pump',
                                          'value': None})
        self.assertEqual(by['syringe_speed']['value'], {'min': 0.001, 'max': 2.5, 'decimals': 3, 'unit': 'mL/s'})
        self.assertEqual(by['syringe_aspirate'], {'action': 'syringe_aspirate', 'name': 'syringe pump for pumping liquid', 'target': 'valve',
                                                  'group': 'syringe pump',
                                                  'value': {'min': 0.001, 'max': 5, 'decimals': 3, 'unit': 'mL'}})
        self.assertEqual(by['syringe_dispense']['value'], by['syringe_aspirate']['value'])
        self.assertEqual(by['repeat_start'], {'action': 'repeat_start', 'name': 'Repeat start', 'target': '', 'group': 'generally',
                                              'value': {'min': 1, 'max': 20, 'decimals': 0, 'unit': ''}})
        self.assertEqual(by['repeat_end']['value'], None)
        self.assertEqual(by['power_off'], {'action': 'power_off', 'name': 'Turn off high voltage', 'target': '', 'group': 'plasma',
                                           'value': None})
        self.assertEqual([item['name'] for item in view].count('Turn off high voltage'), 1)
        self.assertEqual({item['group'] for item in view}, {'generally', 'pump', 'syringe pump', 'scales', 'plasma'})
        self.assertEqual(json.loads(json.dumps(view, ensure_ascii=False)), view)

    def test_view_for_the_1_25_ml_syringe(self):
        by = {item['action']: item for item in recipe.actions_view(HARDWARE, 1250)}
        self.assertEqual(by['syringe_speed']['value'], {'min': 0.001, 'max': 0.625, 'decimals': 3, 'unit': 'mL/s'})
        self.assertEqual(by['syringe_aspirate']['value'], {'min': 0.001, 'max': 1.25, 'decimals': 3, 'unit': 'mL'})
        self.assertEqual(by['syringe_dispense']['value'], {'min': 0.001, 'max': 1.25, 'decimals': 3, 'unit': 'mL'})
        self.assertEqual(by['pump_on']['value']['max'], 80)          # not a matter of the syringe
        self.assertEqual(by['dose_volume']['value']['max'], 1000)

    def test_view_follows_the_yaml_and_leaves_the_table_alone(self):
        before = copy.deepcopy(recipe.ACTIONS)
        by = {item['action']: item for item in recipe.actions_view(hardware_with(max_flow_ml_min=60.0), 5000)}
        self.assertEqual((by['pump_on']['value']['max'], by['pump_reverse']['value']['max']), (60, 60))
        by['pump_on']['value']['max'] = 1
        self.assertEqual(recipe.ACTIONS, before)
        without = recipe.actions_view(None, 1250)                    # pc\vendor incomplete: today's numbers
        self.assertEqual(len(without), 21)
        self.assertEqual({item['action']: item for item in without}['pump_on']['value']['max'], 80)
        self.assertEqual({item['action']: item for item in without}['syringe_aspirate']['value']['max'], 1.25)

    def test_limits_for_bootstrap(self):
        self.assertEqual(recipe.LIMITS, {'bytes': 200000, 'rows': 500, 'steps': 2000, 'nesting': 5, 'repeat': 20,
                                         'delay_total_s': 7200})
        self.assertEqual(recipe.HEADER, ('action', 'target', 'value', 'unit', 'note'))


# =========================================================================================================
class GoodRowTests(Case):
    def test_every_action_with_its_sentence(self):
        result = check(*[row for row, _ in EVERY_ACTION])
        self.assertPasses(result)
        self.assertEqual([item['text'] for item in result['steps']], [text for _, text in EVERY_ACTION])
        self.assertEqual([item['text'] for item in result['rows']], [text for _, text in EVERY_ACTION])
        self.assertEqual({item['action'] for item in result['steps']}, set(NAMES) - {'repeat_start', 'repeat_end'})
        for item in result['steps']:
            self.assertEqual(item['name'], NAMES[item['action']])
            self.assertEqual(list(item), STEP_KEYS)
        self.assertEqual(result['step_count'], len(EVERY_ACTION))
        self.assertEqual(result['warnings'], ['No. 6 column:Pump 7 · OUT reverse', 'No. 10 column:Pump 1 · water reverse'])
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)

    def test_lowest_and_highest_value_of_every_action(self):
        tail = ('pump_off,M8,,,', 'plasma_run,,,,')
        for rows in (('delay,,0,s,',), ('delay,,3600,s,',), ('delay,,0.1,s,',),
                     ('pump_on,M8,0.1,mL/min,', 'delay,,1,s,') + tail, ('pump_on,M8,80,mL/min,', 'delay,,1,s,') + tail,
                     ('pump_reverse,M8,0.1,mL/min,', 'delay,,1,s,') + tail,
                     ('pump_reverse,M8,80,mL/min,', 'delay,,1,s,') + tail,
                     ('dose_volume,M2,0.01,mL,',), ('dose_volume,M2,1000,mL,',), ('dose_reverse,M2,0.01,mL,',),
                     ('dose_reverse,M2,1000,mL,',), ('dose_weight,M9,0.01,g,',), ('dose_weight,M9,1000,g,',),
                     ('syringe_init,,,,', 'syringe_speed,,0.001,mL/s,'), ('syringe_init,,,,', 'syringe_speed,,2.5,mL/s,'),
                     ('syringe_init,,,,', 'syringe_aspirate,CH3,0.001,mL,', 'syringe_dispense,CH3,0.001,mL,'),
                     ('syringe_init,,,,', 'syringe_aspirate,CH1,5,mL,', 'syringe_dispense,CH2,5,mL,'),
                     ('repeat_start,,1,,', 'delay,,1,s,', 'repeat_end,,,,'),
                     ('repeat_start,,20,,', 'delay,,1,s,', 'repeat_end,,,,')):
            with self.subTest(rows=rows):
                self.assertPasses(check(*rows))

    def test_every_motor_and_valve_position_of_the_yaml_is_a_target(self):
        for motor in range(2, 10):
            result = check('dose_volume,M%d,1,mL,' % motor)
            self.assertPasses(result)
            self.assertEqual(result['uses_motors'], [motor])
        self.assertEqual(check('dose_volume,M2,1,mL,')['steps'][0]['target_label'], 'Pump 1 · water')
        self.assertEqual(check('dose_volume,M5,1,mL,')['steps'][0]['text'], 'Pump 4 quantitative 1 mL')
        for position in (1, 2, 3):
            result = check('syringe_init,,,,', 'syringe_aspirate,CH%d,1,mL,' % position, 'syringe_dispense,CH1,1,mL,')
            self.assertPasses(result)
            self.assertEqual(result['steps'][1]['target_label'], 'Valve position %d' % position)
            self.assertEqual(result['steps'][1]['text'], 'Syringe pump slave valve position %d smoke 1 mL' % position)

    def test_numbers_are_shown_without_trailing_zeros_and_kept_as_written(self):
        result = check('delay,,5.0,s,', 'delay,,0.50,s,', 'delay,,.5,s,', 'delay,,5.,s,', 'dose_volume,M2,020.10,mL,')
        self.assertPasses(result)
        self.assertEqual([item['text'] for item in result['steps']],
                         ['Waiting 5 Second', 'Waiting 0.5 Second', 'Waiting 0.5 Second', 'Waiting 5 Second', 'Pump 1 · water quantitative 20.1 mL'])
        self.assertEqual([item['value'] for item in result['steps']], ['5.0', '0.50', '.5', '5.', '020.10'])
        self.assertEqual(result['delay_seconds'], 11)


# =========================================================================================================
def bad_rows():
    """(row, message) — the row stands alone on line 2 and breaks one column (R2)."""
    rows = [('foo,,,,', 'No. 2 Column: Unsupported action ”foo”'), (',M2,,,', 'No. 2 Column: Unsupported action ””'),
            ('Delay,,5,s,', 'No. 2 Column: Unsupported action ”Delay”'), ('pump_speed,CW,200,rpm,', 'No. 2 Column: Unsupported action ”pump_speed”'),
            ('scale_tare,,,,', 'No. 2 Column: Unsupported action ”scale_tare”'),
            ('operator_prompt,,,,', 'No. 2 Column: Unsupported action ”operator_prompt”'),     # generated only, never in a CSV
            ('delay,M2,5,s,', 'No. 2 column: The waiting object must be blank'),
            ('delay,,0.05,s,', 'No. 2 column: The minimum unit of waiting value is 0.1'),
            ('delay,,5.55,s,', 'No. 2 column: The minimum unit of waiting value is 0.1')]
    for value in ('', 'abc', '-1', '3600.1', '3601', '1e2', '+5', '5,0', '５', '1_0', 'NaN', 'Infinity', '9' * 40):
        rows.append(('delay,,"%s",s,' % value, 'No. 2 column: The waiting value must be within 0–3600 between'))
    for unit in ('', 'sec', 'S', 'mL'):
        rows.append(('delay,,5,%s,' % unit, 'No. 2 Column: Waiting unit to be written s'))
    for action in ('pump_on', 'pump_reverse'):
        name = NAMES[action]
        for target in ('', 'M1', 'M10', 'CH1', 'm2', 'M02', '2', 'M'):
            rows.append(('%s,%s,40,mL/min,' % (action, target), 'No. 2 column:%s The object to be written P1–P8' % name))
        for value in ('', '0', '0.05', '80.1', '81', 'fast', '-40'):
            rows.append(('%s,M8,%s,mL/min,' % (action, value), 'No. 2 column:%s The value should be within 0.1–80 between' % name))
        rows.append(('%s,M8,40.05,mL/min,' % action, 'No. 2 column:%s is The new entry point of 0.1' % name))
        for unit in ('', 'mL', 'ml/min', 's'):
            rows.append(('%s,M8,40,%s,' % (action, unit), 'No. 2 column:%s should be written The four lines of mL/min' % name))
    for target in ('', 'M1', 'CH2'):
        rows.append(('pump_off,%s,,,' % target, 'No. 2 Column: The object to stop the pump needs to be written P1–P8'))
    rows.append(('pump_off,M8,5,,', 'No. 2 column: The value for stopping the pump should be blank'))
    rows.append(('pump_off,M8,,s,', 'No. 2 column: The unit for stopping the pump should be blank'))
    for action, unit, other in (('dose_volume', 'mL', 'g'), ('dose_reverse', 'mL', 'L'), ('dose_weight', 'g', 'mL')):
        name = NAMES[action]
        for target in ('', 'M10', 'CH1'):
            rows.append(('%s,%s,10,%s,' % (action, target, unit), 'No. 2 column:%s The object to be written P1–P8' % name))
        for value in ('', '0', '0.001', '1000.01', '1001', 'x'):
            rows.append(('%s,M9,%s,%s,' % (action, value, unit), 'No. 2 column:%s The value should be within 0.01–1000 between' % name))
        rows.append(('%s,M9,10.005,%s,' % (action, unit), 'No. 2 column:%s is The new entry point of 0.01' % name))
        for wrong in ('', other):
            rows.append(('%s,M9,10,%s,' % (action, wrong), 'No. 2 column:%s should be written The four lines of %s' % (name, unit)))
    for target in ('', 'P1', 'M2', 'P1+P1', 'P1+M2', 'P1+P9', 'P1+CH1', 'P1+', '+'.join('P%d' % n for n in range(1, 9)) + '+P1'):
        rows.append(('dose_together,%s,1+1,mL,' % target,
                     'No. 2 Column: If the object of simultaneous quantification is to write two or more different pumps, use + Connect together (example: P1+P8) '))
    for value in ('', '10', '10+2+3'):
        rows.append(('dose_together,P1+P8,%s,mL,' % value, 'No. 2 Column: Simultaneous quantitative values must be as many as the pump, use + Connect together (example: 10+2) '))
    for value in ('10+', '10+0', '0.001+1', '10+1000.01', '10+x', '-1+1'):
        rows.append(('dose_together,P1+P8,%s,mL,' % value, 'No. 2 column: The value of simultaneous quantification must be within 0.01–1000 between'))
    rows.append(('dose_together,P1+P8,10+1.005,mL,', 'No. 2 Column: The minimum unit of simultaneous quantitative value is 0.01'))
    for unit in ('', 'g', 'ml'):
        rows.append(('dose_together,P1+P8,10+2,%s,' % unit, 'No. 2 Column: The unit of simultaneous quantification should be written mL'))
    for action in ('clean', 'syringe_init', 'scale_read', 'rpi_status', 'nano_prepare', 'plasma_run',
                   'spectrum_capture', 'nano_stop', 'power_off', 'repeat_end'):
        name = NAMES[action]
        rows.append(('%s,M2,,,' % action, 'No. 2 column:%s must be blank The working period of' % name))
        rows.append(('%s,,1,,' % action, 'No. 2 column:%s should be blank' % name))
        rows.append(('%s,,,s,' % action, 'No. 2 column:%s should be blank The readback of' % name))
    rows.append(('syringe_speed,CH1,0.4,mL/s,', 'No. 2 Column: The object of syringe pump speed should be blank'))
    for value in ('', '0', '0.0005', 'slow'):
        rows.append(('syringe_speed,,%s,mL/s,' % value, 'No. 2 column: The value of syringe pump speed should be within 0.001–2.5 between'))
    rows.append(('syringe_speed,,0.4005,mL/s,', 'No. 2 Column: The minimum unit of the syringe pump speed is 0.001'))
    for unit in ('', 'mL', 'mL/min'):
        rows.append(('syringe_speed,,0.4,%s,' % unit, 'No. 2 Column: The unit of syringe pump speed should be written mL/s'))
    for action in ('syringe_aspirate', 'syringe_dispense'):
        name = NAMES[action]
        for target in ('', 'CH0', 'CH4', 'M2', 'ch1', '1'):
            rows.append(('%s,%s,1,mL,' % (action, target), 'No. 2 column:%s The object to be written CH1–CH3' % name))
        for value in ('', '0', '0.0005', '5.001', '6', 'all'):
            rows.append(('%s,CH1,%s,mL,' % (action, value), 'No. 2 column:%s The value should be within 0.001–5 between' % name))
        rows.append(('%s,CH1,1.0005,mL,' % action, 'No. 2 column:%s is The new entry point of 0.001' % name))
        for unit in ('', 'uL', 'ml'):
            rows.append(('%s,CH1,1,%s,' % (action, unit), 'No. 2 column:%s should be written The four lines of mL' % name))
    rows.append(('repeat_start,M2,3,,', 'No. 2 Column: The object to be repeated must be blank'))
    for value in ('', '0', '21', '1.5', '3.0', 'x', '-1', '99999'):
        rows.append(('repeat_start,,%s,,' % value, 'No. 2 column:repeat_start If the number of 1–20 integer'))
    rows.append(('repeat_start,,3,Second-rate,', 'No. 2 column: The unit of repeat start should be blank'))
    return rows


class BadRowTests(Case):
    def test_each_bad_column_of_every_action(self):
        seen = set()
        for row, message in bad_rows():
            with self.subTest(row=row):
                # A good row before it and after it: the message is about this row, whatever stands around it.
                result = recipe.validate(HEAD + row + '\nplasma_run,,,,\n', HARDWARE)
                self.assertRefused(result, message, 2)
                self.assertEqual((result['steps'], result['step_count']), ([], 0))
                self.assertEqual([item['ok'] for item in result['rows']], [False, True])
            seen.add(row.split(',')[0])
        self.assertTrue(set(NAMES) <= seen)                     # no action was left out

    def test_columns_are_judged_in_order_target_value_unit(self):
        self.assertRefused(check('pump_on,,,,'), 'No. 2 Column: The object to start the pump (forward rotation) needs to be written P1–P8', 2)
        self.assertRefused(check('pump_on,M8,,x,'), 'No. 2 column: The value for starting the pump (forward rotation) should be within 0.1–80 between', 2)
        self.assertRefused(check('pump_on,M8,40,x,'), 'No. 2 Column: The unit of starting pump (forward rotation) should be written mL/min', 2)
        self.assertRefused(check('delay,M2,0.05,x,'), 'No. 2 column: The waiting object must be blank', 2)

    def test_the_first_bad_row_is_the_one_reported(self):
        result = check('delay,,5,s,', 'delay,,5,min,', 'foo,,,,', 'plasma_run,,,,')
        self.assertRefused(result, 'No. 3 Column: Waiting unit to be written s', 3)

    def test_limits_follow_the_yaml(self):
        slow = hardware_with(max_flow_ml_min=60)
        rows = ('pump_on,M8,60.1,mL/min,', 'delay,,1,s,', 'pump_off,M8,,,')
        self.assertRefused(check(*rows, hardware=slow), 'No. 2 column: The value for starting the pump (forward rotation) should be within 0.1–60 between', 2)
        self.assertPasses(check('pump_on,M8,60,mL/min,', 'delay,,1,s,', 'pump_off,M8,,,', 'plasma_run,,,,',
                                hardware=slow))
        two = hardware_with(syringe={'valve_positions': 2})
        self.assertRefused(check('syringe_aspirate,CH3,1,mL,', hardware=two), 'No. 2 Column: The object to be pumped by the syringe pump should be written CH1–CH2', 2)
        one = hardware_with(syringe={'valve_positions': 1})
        self.assertRefused(check('syringe_dispense,CH2,1,mL,', hardware=one), 'No. 2 Column: The object of syringe pump injection needs to be written CH1', 2)
        few = copy.deepcopy(HARDWARE)
        few['groups'][0]['motors'] = [motor for motor in few['groups'][0]['motors'] if motor['id'] != 4]
        self.assertRefused(check('dose_volume,M4,1,mL,', hardware=few),
                           'No. 2 Column: Quantitative (volume) object to be written P1, P2, P4, P5, P6, P7, P8', 2)

    def test_absurd_numerals_are_judged_by_their_value_and_never_break_the_check(self):
        huge, tiny = '9' * 300, '0.' + '0' * 300 + '1'
        cases = [('delay,,%s,s,', 'No. 2 column: The waiting value must be within 0–3600 between', 'No. 2 column: The minimum unit of waiting value is 0.1'),
                 ('pump_on,M8,%s,mL/min,', 'No. 2 column: The value for starting the pump (forward rotation) should be within 0.1–80 between', None),
                 ('pump_reverse,M8,%s,mL/min,', 'No. 2 column: The value for starting the pump (reverse) must be within 0.1–80 between', None),
                 ('dose_volume,M2,%s,mL,', 'No. 2 column: The value of quantitative (volume) should be within 0.01–1000 between', None),
                 ('dose_reverse,M2,%s,mL,', 'No. 2 Column: The value of quantitative (volume, inversion) should be within 0.01–1000 between', None),
                 ('dose_weight,M9,%s,g,', 'No. 2 column: The value of quantitative (weight) should be within 0.01–1000 between', None),
                 ('syringe_aspirate,CH1,%s,mL,', 'No. 2 column: The value of the syringe pump should be within 0.001–5 between', None),
                 ('syringe_dispense,CH1,%s,mL,', 'No. 2 column: The value of syringe pump injection should be within 0.001–5 between', None),
                 ('repeat_start,,%s,,', 'No. 2 column:repeat_start If the number of 1–20 integer', None)]
        for row, too_much, too_fine in cases:
            with self.subTest(row=row):
                self.assertRefused(check(row % huge), too_much, 2)
                self.assertRefused(check(row % tiny), too_fine or too_much, 2)
        for size, top in ((5000, '2.5'), (1250, '0.625')):
            self.assertRefused(check('syringe_speed,,%s,mL/s,' % huge, syringe_ul=size),
                               'No. 2 column: Syringe pump speed exceeds the upper limit %s mL/s' % top, 2)
            self.assertRefused(check('syringe_speed,,%s,mL/s,' % tiny, syringe_ul=size),
                               'No. 2 column: The value of syringe pump speed should be within 0.001–%s between' % top, 2)
        self.assertRefused(check('delay,,3599.%s,s,' % huge), 'No. 2 column: The minimum unit of waiting value is 0.1', 2)
        # Zeros do not change a number: this is 0.1 s and 20 mL, however it is written.
        result = check('delay,,0.1%s,s,' % ('0' * 300), 'dose_volume,M2,%s20.0,mL,' % ('0' * 300), 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual([item['text'] for item in result['steps']][:2], ['Waiting 0.1 Second', 'Pump 1 · water quantitative 20 mL'])
        self.assertEqual((result['delay_seconds'], result['liquids'][0]['ml']), (0.1, 20.0))

    def test_a_long_unknown_action_is_cut_in_the_message(self):
        result = check('x' * 300 + ',,,,')
        self.assertRefused(result, 'No. 2 Column: Unsupported action ”%s…”' % ('x' * 40), 2)

    def test_without_the_vendored_yaml_only_recipes_without_the_board_pass(self):
        self.assertPasses(check('rpi_status,,,,', 'nano_prepare,,,,', 'plasma_run,,,,', 'delay,,5,s,', 'power_off,,,,',
                                hardware=None))
        for row, name in (('dose_volume,M2,20,mL,', 'Quantitative (volume)'), ('clean,,,,', 'automatic cleaning'),
                          ('scale_read,,,,', 'Record weight'), ('syringe_init,,,,', 'syringe pump initialization'),
                          ('pump_off,M8,,,', 'stop pump')):
            with self.subTest(row=row):
                for hardware in (None, {}, {'groups': []}, {'groups': 'broken'}):
                    result = check(row, 'plasma_run,,,,', hardware=hardware)
                    self.assertRefused(result, 'No. 2 Column: Program folder is incomplete (missing pc\\vendor) ,can’t do“%s”.'
                                               'Please copy the entire folder again.' % name, 2)


# =========================================================================================================
class FileTests(Case):
    """R1: the file itself."""

    def test_header(self):
        message = 'CSV The first column of must be action,target,value,unit,note'
        for text in ('', '\n', 'plasma_run,,,,\n', 'action,target,value,unit\nplasma_run,,,\n',
                     'action,target,value,unit,note,extra\nplasma_run,,,,,\n', 'Action,Target,Value,Unit,Note\n',
                     'action;target;value;unit;note\nplasma_run;;;;\n', 'action\ttarget\tvalue\tunit\tnote\n',
                     '\n' + text_of('plasma_run,,,,'), 'target,action,value,unit,note\n'):
            with self.subTest(text=text):
                result = recipe.validate(text, HARDWARE)
                self.assertRefused(result, message)
                self.assertEqual((result['rows'], result['steps']), ([], []))
        self.assertPasses(recipe.validate('\ufeff' + text_of('plasma_run,,,,'), HARDWARE))
        self.assertPasses(recipe.validate(' action , target,value,unit,note \nplasma_run,,,,\n', HARDWARE))
        self.assertPasses(recipe.validate('"action","target","value","unit","note"\r\n"plasma_run","","","",""\r\n',
                                          HARDWARE))

    def test_size(self):
        note = '€' * 60000                                     # 180,000 bytes in UTF-8
        fits = text_of('plasma_run,,,,' + note)
        self.assertLessEqual(len(fits.encode('utf-8')), 200000)
        result = recipe.validate(fits, HARDWARE)
        self.assertPasses(result)
        self.assertEqual(result['steps'][0]['note'], '€' * 200)
        exact = fits + ' ' * (200000 - len(fits.encode('utf-8')))
        self.assertEqual(len(exact.encode('utf-8')), 200000)
        self.assertPasses(recipe.validate(exact, HARDWARE))
        result = recipe.validate(exact + ' ', HARDWARE)
        self.assertRefused(result, 'CSV is too large (upper limit 200 KB) ')
        self.assertEqual(result['rows'], [])

    def test_row_count(self):
        self.assertPasses(check(*['delay,,1,s,'] * 499, 'plasma_run,,,,'))
        result = check(*['delay,,1,s,'] * 500, 'plasma_run,,,,')
        self.assertRefused(result, 'CSV most 500 List')
        self.assertEqual((len(result['rows']), result['steps']), (500, []))
        # Empty rows count, as in the original; blank lines do not.
        self.assertRefused(check(*[',,,,'] * 500, 'plasma_run,,,,'), 'CSV most 500 List')
        self.assertPasses(recipe.validate(HEAD + '\n' * 600 + 'plasma_run,,,,\n' + '\n' * 600, HARDWARE))

    def test_column_count(self):
        message = 'No. 3 Column: The number of fields is incorrect (required 5 column) '
        for row in ('plasma_run,,,', 'plasma_run,,,,,', 'plasma_run', 'delay,,5,s,Wait a moment,One more column',
                    'delay,,5,s,has a comma, without quotation marks'):
            with self.subTest(row=row):
                self.assertRefused(check('plasma_run,,,,', row), message, 3)
        self.assertPasses(check('plasma_run,,,,', 'delay,,5,s,"has a comma, added quotation marks"'))

    def test_too_many_steps_after_expansion(self):
        block = ['repeat_start,,20,,', 'repeat_start,,20,,'] + ['delay,,0.1,s,'] * 5 + ['repeat_end,,,,'] * 2
        result = check(*block)
        self.assertPasses(result)
        self.assertEqual((result['step_count'], result['rounds'], result['delay_seconds']), (2000, 20, 200))
        result = check(*block, 'plasma_run,,,,')
        self.assertRefused(result, 'Expand beyond 2000 action')
        self.assertEqual((result['steps'], result['step_count'], len(result['rows'])), ([], 0, 10))
        deep = ['repeat_start,,20,,'] * 5 + ['delay,,1,s,'] + ['repeat_end,,,,'] * 5      # 3 200 000 steps
        self.assertRefused(check(*deep), 'Expand beyond 2000 action')

    def test_nesting(self):
        five = ['repeat_start,,1,,'] * 5 + ['plasma_run,,,,'] + ['repeat_end,,,,'] * 5
        self.assertPasses(check(*five))
        six = ['repeat_start,,1,,'] * 6 + ['plasma_run,,,,'] + ['repeat_end,,,,'] * 6
        result = check(*six)
        self.assertRefused(result, 'repeat most 5 layer', 7)
        self.assertEqual([item['text'] for item in result['rows']][:7], ['Repeat start ×1'] * 6 + ['discharge＋capture spectrum'])
        again = ['repeat_start,,2,,', 'delay,,1,s,', 'repeat_end,,,,'] * 6          # one after the other is fine
        self.assertPasses(check(*again))
        # Far too deep: refused the same way, and nothing in the program has to climb that deep.
        abyss = ['repeat_start,,2,,'] * 240 + ['plasma_run,,,,'] + ['repeat_end,,,,'] * 240
        result = check(*abyss)
        self.assertRefused(result, 'repeat most 5 layer', 7)
        self.assertEqual(len(result['rows']), 481)
        self.assertEqual(len(recipe.expand(recipe.parse(text_of(*abyss), HARDWARE)['tree'])), 32)

    def test_repeat_blocks_must_match(self):
        self.assertRefused(check('repeat_end,,,,', 'plasma_run,,,,'), 'No. 2 column:repeat_end has no corresponding repeat_start', 2)
        self.assertRefused(check('repeat_start,,2,,', 'delay,,1,s,', 'repeat_end,,,,', 'repeat_end,,,,'),
                           'No. 5 column:repeat_end has no corresponding repeat_start', 5)
        self.assertRefused(check('repeat_start,,2,,', 'plasma_run,,,,'), 'No. 2 column:repeat_start less repeat_end', 2)
        self.assertRefused(check('plasma_run,,,,', 'repeat_start,,2,,', 'repeat_start,,2,,', 'delay,,1,s,',
                                 'repeat_end,,,,'), 'No. 3 column:repeat_start less repeat_end', 3)
        # Two are open: the outer one is named (the first row that is wrong).
        self.assertRefused(check('repeat_start,,2,,', 'repeat_start,,3,,', 'plasma_run,,,,'),
                           'No. 2 column:repeat_start less repeat_end', 2)

    def test_nothing_to_do(self):
        for text in (HEAD, HEAD.rstrip('\n'), text_of(',,,,', ' , , , , '), HEAD + '\n\n',
                     text_of('repeat_start,,3,,', 'repeat_end,,,,')):
            with self.subTest(text=text):
                result = recipe.validate(text, HARDWARE)
                self.assertRefused(result, NO_STEPS)
                self.assertEqual((result['steps'], result['step_count'], result['rounds']), ([], 0, 0))

    def test_a_file_the_csv_reader_gives_up_on(self):
        result = recipe.validate(text_of('plasma_run,,,,', 'delay,,5,s,"' + 'x' * 140000), HARDWARE)
        self.assertRefused(result, UNREADABLE)
        self.assertEqual([item['action'] for item in result['rows']], ['plasma_run'])


# =========================================================================================================
class RepeatTests(Case):
    ROWS = ('plasma_run,,,,a', 'repeat_start,,3,,', 'delay,,1,s,b', 'repeat_start,,2,,', 'delay,,2,s,c', 'repeat_end,,,,',
            'repeat_end,,,,', 'delay,,3,s,d')

    def test_expansion_order_lines_and_rounds(self):
        result = check(*self.ROWS)
        self.assertPasses(result)
        steps = result['steps']
        self.assertEqual([item['n'] for item in steps], list(range(1, 12)))
        self.assertEqual([item['note'] for item in steps], list('abccbccbccd'))
        self.assertEqual([item['line'] for item in steps], [2, 4, 6, 6, 4, 6, 6, 4, 6, 6, 9])
        # Only the outermost repeat counts as a displayed round; steps outside any repeat are round 1 of 1.
        self.assertEqual([(item['round'], item['rounds']) for item in steps],
                         [(1, 1)] + [(1, 3)] * 3 + [(2, 3)] * 3 + [(3, 3)] * 3 + [(1, 1)])
        self.assertEqual((result['step_count'], result['rounds'], result['delay_seconds']), (11, 3, 18))
        self.assertEqual(len(result['rows']), 8)

    def test_without_a_repeat_there_is_one_round(self):
        result = check('delay,,1,s,', 'plasma_run,,,,')
        self.assertEqual((result['rounds'], [(item['round'], item['rounds']) for item in result['steps']]),
                         (1, [(1, 1), (1, 1)]))

    def test_two_blocks_the_larger_count_is_the_rounds(self):
        result = check('repeat_start,,2,,', 'delay,,1,s,', 'repeat_end,,,,', 'repeat_start,,5,,', 'plasma_run,,,,',
                       'repeat_end,,,,')
        self.assertPasses(result)
        self.assertEqual((result['rounds'], result['step_count'], result['plasma_count']), (5, 7, 5))

    def test_the_rules_run_over_the_expanded_list(self):
        # The second pass of the block starts a pump that is still running.
        result = check('repeat_start,,2,,', 'pump_on,M8,40,mL/min,', 'delay,,5,s,', 'repeat_end,,,,', 'pump_off,M8,,,')
        self.assertRefused(result, 'No. 3 column:Pump 7 · OUT is already transferring', 3)
        result = check('repeat_start,,3,,', 'delay,,3600,s,', 'repeat_end,,,,')
        self.assertRefused(result, 'The total waiting time exceeds 7200 Second')

    def test_parse_and_expand_are_the_two_halves(self):
        parsed = recipe.parse(text_of(*self.ROWS), HARDWARE)
        self.assertEqual((parsed['error'], parsed['error_line'], len(parsed['rows'])), ('', None, 8))
        self.assertEqual([item['kind'] for item in parsed['rows']],
                         ['step', 'start', 'step', 'start', 'step', 'end', 'end', 'step'])
        steps = recipe.expand(parsed['tree'])
        self.assertEqual([item['line'] for item in steps], [2, 4, 6, 6, 4, 6, 6, 4, 6, 6, 9])
        many = recipe.parse(text_of('repeat_start,,20,,', 'repeat_start,,20,,', 'repeat_start,,6,,', 'delay,,1,s,',
                                    'repeat_end,,,,', 'repeat_end,,,,', 'repeat_end,,,,'), HARDWARE)
        self.assertRaisesText('Expand beyond 2000 action', recipe.expand, many['tree'])
        self.assertEqual(recipe.parse(text_of('plasma_run,,,,'))['error'], '')       # no hardware needed for that


# =========================================================================================================
class PreflightTests(Case):
    """R3 – R10, each with the smallest Recipe that breaks it."""

    def test_r3_one_motor_per_driver_group(self):
        self.assertRefused(check('pump_on,M2,40,mL/min,', 'pump_on,M3,40,mL/min,'),
                           'No. 3 column:A group can only transfer one at a time (Pump 1 · water is still transferring)', 3)
        self.assertRefused(check('pump_reverse,M8,40,mL/min,', 'delay,,1,s,', 'pump_on,M9,40,mL/min,'),
                           'No. 4 column:B group can only transfer one at a time (Pump 7 · OUT is still transferring)', 4)

    def test_r3_one_motor_of_each_group_may_run_together(self):
        result = check('pump_on,M2,10,mL/min,', 'pump_on,M8,40,mL/min,', 'delay,,10,s,', 'pump_off,M2,,,', 'delay,,5,s,',
                       'pump_off,M8,,,', 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual([item['planned_s'] for item in result['steps']], [10, 15, None, None, None, None, None])
        self.assertEqual(result['liquids'], [
            {'motor': 2, 'label': 'Pump 1 · water', 'ml': 1.667, 'g': None, 'approx': True},
            {'motor': 8, 'label': 'Pump 7 · OUT', 'ml': 10.0, 'g': None, 'approx': True}])

    def test_r4_already_running_and_not_running(self):
        self.assertRefused(check('pump_on,M8,40,mL/min,', 'pump_on,M8,40,mL/min,'), 'No. 3 column:Pump 7 · OUT is already transferring', 3)
        self.assertRefused(check('pump_on,M8,40,mL/min,', 'delay,,1,s,', 'pump_reverse,M8,40,mL/min,'),
                           'No. 4 column:Pump 7 · OUT is already transferring', 4)
        self.assertRefused(check('pump_off,M8,,,'), 'No. 2 column:Pump 7 · OUT is not transferring', 2)
        self.assertRefused(check('pump_on,M8,40,mL/min,', 'delay,,1,s,', 'pump_off,M9,,,'),
                           'No. 4 column:Pump 8 · CuSO₄ is not transferring', 4)
        self.assertRefused(check('pump_on,M8,40,mL/min,', 'delay,,1,s,', 'pump_off,M8,,,', 'pump_off,M8,,,'),
                           'No. 5 column:Pump 7 · OUT is not transferring', 5)

    def test_r5_pumps_and_high_voltage_never_together(self):
        # The static half of the interlock: nothing but delay, pump_on (other group) and pump_off while a pump runs.
        for row in NEEDS_PUMPS_STOPPED:
            with self.subTest(row=row):
                name = NAMES[row.split(',')[0]]
                result = check('pump_on,M8,40,mL/min,', 'delay,,1,s,', row, 'pump_off,M8,,,')
                self.assertRefused(result, 'No. 4 column: do ”%s”All pumps must be stopped before' % name, 4)
        self.assertEqual({row.split(',')[0] for row in NEEDS_PUMPS_STOPPED} | {'delay', 'pump_on', 'pump_reverse',
                                                                               'pump_off', 'repeat_start', 'repeat_end'},
                         set(NAMES))
        result = check('dose_weight,M9,10,g,', 'dose_volume,M2,20,mL,', 'pump_on,M8,40,mL/min,', 'delay,,5,s,',
                       'plasma_run,,,,', 'delay,,60,s,', 'pump_off,M8,,,')
        self.assertRefused(result, 'No. 6 List: Do“discharge＋Stop all pumps before capturing the spectrum.', 6)
        self.assertEqual((result['step_count'], result['uses_plasma'], result['uses_pump']), (7, True, True))

    def test_r5_is_also_found_inside_a_repeat(self):
        result = check('repeat_start,,2,,', 'plasma_run,,,,', 'pump_on,M8,40,mL/min,', 'delay,,5,s,', 'repeat_end,,,,',
                       'pump_off,M8,,,')
        self.assertRefused(result, 'No. 3 List: Do“discharge＋Stop all pumps before capturing the spectrum.', 3)

    def test_r6_planned_on_time(self):
        run = ('pump_on,M8,40,mL/min,', 'delay,,300,s,')
        result = check(*run, 'pump_off,M8,,,', 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual(result['steps'][0]['planned_s'], 300)
        self.assertEqual(result['steps'][0]['text'], 'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 300 seconds)')
        result = check(*run, 'delay,,0.1,s,', 'pump_off,M8,,,')
        self.assertRefused(result, 'No. 2 column:Pump 7 · OUT will transfer continuously 300.1 seconds, exceeding the upper limit 300 Second', 2)
        self.assertEqual(result['steps'][0]['planned_s'], 300.1)
        result = check('plasma_run,,,,', 'pump_reverse,M2,5,mL/min,', 'delay,,200,s,', 'delay,,200,s,', 'pump_off,M2,,,')
        self.assertRefused(result, 'No. 3 column:Pump 1 · water will continue to turn 400 seconds, exceeding the upper limit 300 Second', 3)
        for rows in (('pump_on,M8,40,mL/min,', 'pump_off,M8,,,'),
                     ('pump_on,M8,40,mL/min,', 'delay,,0,s,', 'pump_off,M8,,,')):
            self.assertRefused(check(*rows), 'No. 2 column:Pump 7 · OUT Turn it off when it is turned on, there must be something in between delay', 2)
        result = check('pump_on,M8,40,mL/min,', 'delay,,0.1,s,', 'pump_off,M8,,,', 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual(result['steps'][0]['planned_s'], 0.1)

    def test_r7_every_pump_is_switched_off_before_the_end(self):
        result = check('pump_on,M8,40,mL/min,', 'delay,,5,s,')
        self.assertRefused(result, 'The pump is still running at the end of the process:Pump 7 · OUT (No. 2 column startup)', 2)
        self.assertEqual(result['steps'][0]['planned_s'], None)
        self.assertEqual(result['steps'][0]['text'], 'Pump 7 · OUT Forward rotation 40 mL/min')
        self.assertEqual(result['liquids'], [])
        result = check('plasma_run,,,,', 'pump_on,M2,40,mL/min,', 'pump_reverse,M8,40,mL/min,', 'delay,,5,s,')
        self.assertRefused(result, 'The pump is still running at the end of the process:Pump 1 · Water (No. 3 column startup),Pump 7 · OUT (No. 4 column startup)', 3)

    def test_r8_syringe(self):
        for row in ('syringe_aspirate,CH2,1,mL,', 'syringe_dispense,CH1,1,mL,', 'syringe_speed,,0.4,mL/s,'):
            self.assertRefused(check('plasma_run,,,,', row), 'No. 3 column: Before using the syringe pump, syringe_init', 3)
        self.assertRefused(check('syringe_init,,,,', 'syringe_aspirate,CH2,1,mL,', 'syringe_init,,,,'),
                           'No. 4 Column: There is still something in the syringe 1 mL,initialization will push it out; please first syringe_dispense', 4)
        self.assertRefused(check('syringe_init,,,,', 'syringe_aspirate,CH2,4.5,mL,', 'syringe_aspirate,CH2,1,mL,'),
                           'No. 4 column: The syringe cannot be loaded (existing 4.5 mL,capacity 5 mL) ', 4)
        self.assertRefused(check('syringe_init,,,,', 'syringe_aspirate,CH2,1,mL,', 'syringe_dispense,CH1,2,mL,'),
                           'No. 4 column: There are only 1 mL,Can’t push it out 2 mL', 4)
        self.assertRefused(check('syringe_init,,,,', 'syringe_dispense,CH1,0.001,mL,'),
                           'No. 3 column: There are only 0 mL,Can’t push it out 0.001 mL', 3)
        self.assertRefused(check('syringe_init,,,,', 'syringe_aspirate,CH2,1,mL,', 'syringe_dispense,CH1,0.75,mL,',
                                 'plasma_run,,,,'), 'There is still something left in the syringe at the end of the process. 0.25 mL,Please launch first')
        for value in ('2.501', '9'):
            self.assertRefused(check('syringe_init,,,,', 'syringe_speed,,%s,mL/s,' % value),
                               'No. 3 column: Syringe pump speed exceeds the upper limit 2.5 mL/s', 3)
        self.assertRefused(check('syringe_init,,,,', 'syringe_speed,,0.626,mL/s,', syringe_ul=1250),
                           'No. 3 column: Syringe pump speed exceeds the upper limit 0.625 mL/s', 3)

    def test_r8_a_complete_syringe_cycle_passes(self):
        result = check('syringe_init,,,,', 'syringe_speed,,2.5,mL/s,', 'syringe_aspirate,CH2,2.5,mL,',
                       'syringe_aspirate,CH3,2.5,mL,', 'syringe_dispense,CH1,5,mL,', 'syringe_init,,,,',
                       'syringe_aspirate,CH2,0.001,mL,', 'syringe_dispense,CH1,0.001,mL,', 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual((result['uses_syringe'], result['uses_pump'], result['uses_motors']), (True, True, []))

    def test_r9_weight_dose_needs_the_setting_of_the_yaml(self):
        off = hardware_with(gravimetric={'enabled': False})
        result = check('plasma_run,,,,', 'dose_weight,M9,10,g,', hardware=off)
        self.assertRefused(result, 'No. 3 Column: Profile does not enable weighing and quantification', 3)
        self.assertEqual(result['step_count'], 2)
        self.assertPasses(check('plasma_run,,,,', 'dose_weight,M9,10,g,'))

    def test_r10_sum_of_the_delays(self):
        self.assertPasses(check('delay,,3600,s,', 'delay,,3600,s,', 'plasma_run,,,,'))
        result = check('delay,,3600,s,', 'delay,,3600,s,', 'delay,,0.1,s,', 'plasma_run,,,,')
        self.assertRefused(result, 'The total waiting time exceeds 7200 Second')
        self.assertEqual(result['delay_seconds'], 7200.1)

    def test_r11_a_new_syringe_size_makes_a_published_recipe_invalid(self):
        published = text_of('syringe_init,,,,', 'syringe_speed,,1,mL/s,', 'syringe_aspirate,CH2,3,mL,',
                            'syringe_dispense,CH1,3,mL,', 'plasma_run,,,,')
        self.assertPasses(recipe.validate(published, HARDWARE, 5000))
        self.assertPasses(recipe.validate(published, HARDWARE))                 # 5 mL is the default
        small = recipe.validate(published, HARDWARE, 1250)
        self.assertRefused(small, 'No. 3 column: Syringe pump speed exceeds the upper limit 0.625 mL/s', 3)
        # Still the same Recipe on the screen: its steps and facts are listed, only the row is marked.
        self.assertEqual((small['step_count'], small['uses_syringe'], small['plasma_count']), (5, True, 1))
        self.assertEqual([item['ok'] for item in small['rows']], [True, False, True, True, True])
        self.assertEqual(small['steps'][2]['text'], 'Syringe pump slave valve position 2 smoke 3 mL')
        self.assertPasses(recipe.validate(published, HARDWARE, 5000))           # and back
        volume = text_of('syringe_init,,,,', 'syringe_aspirate,CH2,3,mL,', 'syringe_dispense,CH1,3,mL,')
        self.assertPasses(recipe.validate(volume, HARDWARE, 5000))
        small = recipe.validate(volume, HARDWARE, 1250)
        self.assertRefused(small, 'No. 3 column: The value of the syringe pump should be within 0.001–1.25 between', 3)
        self.assertEqual(small['step_count'], 3)
        total = text_of('syringe_init,,,,', 'syringe_aspirate,CH2,1,mL,', 'syringe_aspirate,CH3,1,mL,',
                        'syringe_dispense,CH1,1,mL,', 'syringe_dispense,CH1,1,mL,')
        self.assertPasses(recipe.validate(total, HARDWARE, 5000))
        self.assertRefused(recipe.validate(total, HARDWARE, 1250), 'No. 4 column: The syringe cannot be loaded (existing 1 mL,capacity 1.25 mL) ', 4)
        # No syringe the YAML offers takes 6 mL: that is a bad row for any setting.
        never = text_of('syringe_init,,,,', 'syringe_aspirate,CH2,6,mL,')
        self.assertRefused(recipe.validate(never, HARDWARE, 1250), 'No. 3 column: The value of the syringe pump should be within 0.001–1.25 between', 3)
        self.assertRefused(recipe.validate(never, HARDWARE, 5000), 'No. 3 column: The value of the syringe pump should be within 0.001–5 between', 3)
        # A setting that cannot be read is taken as the 5 mL default, never as an error of the file.
        for odd in (None, 0, -5, 'x', True):
            self.assertPasses(recipe.validate(published, HARDWARE, odd))
        self.assertRefused(recipe.validate(published, HARDWARE, '1250'), 'No. 3 column: Syringe pump speed exceeds the upper limit 0.625 mL/s', 3)

    def test_only_the_first_problem_is_reported_and_the_walk_goes_on(self):
        rows = ['pump_off,M8,,,', 'pump_on,M8,40,mL/min,', 'delay,,30,s,', 'pump_off,M8,,,', 'syringe_init,,,,',
                'syringe_aspirate,CH2,1,mL,']                     # three problems: rows 2 and 7, and the end
        result = check(*rows)
        self.assertRefused(result, 'No. 2 column:Pump 7 · OUT is not transferring', 2)
        self.assertEqual(result['steps'][1]['planned_s'], 30)
        self.assertEqual(result['steps'][1]['text'], 'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 30 seconds)')
        self.assertEqual(result['liquids'], [{'motor': 8, 'label': 'Pump 7 · OUT', 'ml': 20.0, 'g': None,
                                              'approx': True}])
        self.assertEqual([item['ok'] for item in result['rows']], [False, True, True, True, True, True])
        self.assertRefused(check(*rows[1:]), 'There is still something left in the syringe at the end of the process. 1 mL,Please launch first')
        # A row that is wrong in itself (R2) comes before anything the order of the steps makes wrong.
        result = check(*rows[:5], 'syringe_aspirate,CH2,9,mL,')
        self.assertRefused(result, 'No. 7 column: The value of the syringe pump should be within 0.001–5 between', 7)
        self.assertEqual(result['steps'], [])


# =========================================================================================================
class PlainReadingTests(Case):
    """R3 – R10 once more, written out plainly with whole numbers, against a few thousand random Recipes."""

    GROUP = {motor['id']: group['id'] for group in HARDWARE['groups'] for motor in group['motors']}
    SINGLE = ('clean', 'scale_read', 'rpi_status', 'nano_prepare', 'plasma_run', 'spectrum_capture', 'nano_stop',
              'power_off', 'syringe_init')

    def rows(self, rng, depth=0):
        rows = []
        for _ in range(rng.randint(1, 6)):
            pick, motor = rng.random(), rng.choice((2, 3, 8, 9))
            if pick < 0.24:
                rows.append('delay,,%s,s,' % rng.choice(('0', '0.1', '5', '12.5', '150', '300', '2400', '3600')))
            elif pick < 0.42:
                rows.append('%s,M%d,%s,mL/min,' % (rng.choice(('pump_on', 'pump_reverse')), motor,
                                                   rng.choice(('0.1', '13.3', '80'))))
            elif pick < 0.60:
                rows.append('pump_off,M%d,,,' % motor)
            elif pick < 0.66:
                rows.append('%s,M%d,%s,mL,' % (rng.choice(('dose_volume', 'dose_reverse')), motor,
                                               rng.choice(('0.01', '20'))))
            elif pick < 0.69:
                rows.append('dose_weight,M%d,10,g,' % motor)
            elif pick < 0.79:
                rows.append('%s,,,,' % rng.choice(self.SINGLE))
            elif pick < 0.82:
                rows.append('syringe_speed,,%s,mL/s,' % rng.choice(('0.001', '0.625', '0.626', '2.5')))
            elif pick < 0.94:
                rows.append('%s,CH%d,%s,mL,' % (rng.choice(('syringe_aspirate', 'syringe_dispense')), rng.randint(1, 3),
                                                rng.choice(('0.001', '0.5', '1.25', '1.251', '2.5', '5'))))
            elif depth < 2:
                rows += ['repeat_start,,%d,,' % rng.randint(1, 3)] + self.rows(rng, depth + 1) + ['repeat_end,,,,']
        return rows

    def keeps_the_rules(self, steps, size_ul):
        """Tenths of a second and microlitres, so that nothing here shares its arithmetic with recipe.py."""
        running, ready, held, waited = {}, False, 0, 0
        for item in steps:
            action, target = item['action'], item['target']
            amount = Fraction(item['value']) if item['value'] else Fraction(0)
            if action == 'delay':
                waited += amount * 10
                running = {motor: tenths + amount * 10 for motor, tenths in running.items()}
            elif action in ('pump_on', 'pump_reverse'):
                motor = int(target[1:])
                if motor in running or any(self.GROUP[other] == self.GROUP[motor] for other in running):
                    return False                                           # R4, R3
                running[motor] = 0
            elif action == 'pump_off':
                if not 1 <= running.pop(int(target[1:]), 0) <= 3000:
                    return False                                           # R4, R6
            elif running:
                return False                                               # R5: nothing else while a pump runs
            elif action == 'syringe_init':
                if held:
                    return False
                ready = True
            elif action == 'syringe_speed':
                if not ready or amount * 1000 > size_ul // 2:
                    return False
            elif action in ('syringe_aspirate', 'syringe_dispense'):
                held += amount * 1000 if action == 'syringe_aspirate' else -amount * 1000
                if not ready or amount * 1000 > size_ul or not 0 <= held <= size_ul:
                    return False
        return not running and not held and waited <= 72000                # R7, R8, R10

    def test_valid_means_the_rules_are_kept(self):
        rng = random.Random(20261006)
        seen = {True: 0, False: 0}
        for _ in range(3000):
            rows, size = self.rows(rng), rng.choice((5000, 1250))
            result = check(*rows, syringe_ul=size)
            if not result['steps']:                                        # only empty repeat blocks
                self.assertRefused(result, NO_STEPS)
                continue
            if result['valid'] != self.keeps_the_rules(result['steps'], size):
                self.fail('%s (%s µL): %s' % (rows, size, result['error'] or 'valid'))
            seen[result['valid']] += 1
            if result['valid']:
                # The static half of the interlock, read off the steps themselves: between a pump start and
                # its stop there are only waits and the start or stop of a pump.
                on = set()
                for item in result['steps']:
                    if item['action'] in ('pump_on', 'pump_reverse'):
                        on.add(item['target'])
                    elif item['action'] == 'pump_off':
                        on.discard(item['target'])
                    elif item['action'] != 'delay':
                        self.assertEqual(on, set(), rows)
                self.assertEqual(on, set(), rows)
        self.assertGreater(seen[True], 200)
        self.assertGreater(seen[False], 1000)


# =========================================================================================================
class WarningTests(Case):
    def test_w2_more_than_one_discharge(self):
        self.assertEqual(check('plasma_run,,,,')['warnings'], [])
        self.assertEqual(check('plasma_run,,,,', 'delay,,5,s,', 'plasma_run,,,,')['warnings'], ['this Recipe One round will discharge 2 Second-rate'])
        result = check('repeat_start,,3,,', 'plasma_run,,,,', 'repeat_end,,,,')
        self.assertPasses(result)
        self.assertEqual((result['warnings'], result['plasma_count']), (['this Recipe One round will discharge 3 Second-rate'], 3))

    def test_w3_reverse(self):
        result = check('pump_reverse,M8,40,mL/min,', 'delay,,5,s,', 'pump_off,M8,,,', 'dose_reverse,M2,1,mL,',
                       'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual(result['warnings'], ['No. 2 column:Pump 7 · OUT reverse', 'No. 5 column:Pump 1 · water reverse'])
        result = check('repeat_start,,4,,', 'dose_reverse,M9,1,mL,', 'repeat_end,,,,', 'plasma_run,,,,')
        self.assertEqual(result['warnings'], ['No. 3 column:Pump 8 · CuSO₄ reverse'])       # once per row, not per pass

    def test_w4_weight_dose_on_a_motor_without_a_liquid(self):
        result = check('dose_weight,M3,5,g,', 'dose_weight,M9,5,g,', 'dose_weight,M2,5,g,', 'dose_weight,M6,5,g,',
                       'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual(result['warnings'], ['No. 2 column:Pump 2 No liquid is marked, please make sure its outlet is on the scale The',
                                              'No. 5 column:Pump 5 No liquid is marked, please make sure its outlet is on the scale The'])

    def test_w5_no_measurement(self):
        self.assertEqual(check('delay,,5,s,')['warnings'], [W5])
        self.assertEqual(check('spectrum_capture,,,,', 'power_off,,,,')['warnings'], [W5])
        self.assertEqual(check('foo,,,,')['warnings'], [])                            # nothing was parsed

    def test_several_warnings_come_in_the_order_of_the_table(self):
        result = recipe.validate(text_of('dose_weight,M3,5,g,', 'dose_reverse,M2,1,mL,', 'plasma_run,,,,',
                                         'plasma_run,,,,'), HARDWARE, 5000, encoding='cp950')
        self.assertEqual(result['warnings'], ['this Recipe One round will discharge 2 Second-rate', 'No. 3 column:Pump 1 · water reverse',
                                              'No. 2 column:Pump 2 No liquid is marked, please make sure its outlet is on the scale The', W6])


# =========================================================================================================
class FactsTests(Case):
    def test_cells_are_stripped_and_the_note_is_cut(self):
        long = 'Remarks' * 150
        result = recipe.validate(HEAD + '  dose_volume , M2 ,  20 , mL ,  add water  \r\n\tplasma_run\t,,,,' + long + '\r\n',
                                 HARDWARE)
        self.assertPasses(result)
        first, second = result['steps']
        self.assertEqual((first['action'], first['target'], first['value'], first['unit'], first['note']),
                         ('dose_volume', 'M2', '20', 'mL', 'add water'))
        self.assertEqual((second['action'], second['note'], len(second['note'])), ('plasma_run', long[:200], 200))
        self.assertEqual(result['rows'][1]['note'], long[:200])

    def test_empty_rows_are_skipped_and_the_line_is_the_row_of_the_file(self):
        text = HEAD + '\n,,,,\ndelay,,1,s,\n   \n\n' + 'plasma_run,,,,"first line\nsecond line"\n' + 'delay,,2,s,\n'
        result = recipe.validate(text, HARDWARE)
        self.assertPasses(result)
        self.assertEqual([item['line'] for item in result['steps']], [4, 7, 8])
        self.assertEqual([item['line'] for item in result['rows']], [4, 7, 8])
        self.assertEqual(result['steps'][1]['note'], 'first line second line')               # a line break in a cell: one space
        result = recipe.validate(HEAD + ',,,,\n\nfoo,,,,\n', HARDWARE)
        self.assertRefused(result, 'No. 4 Column: Unsupported action ”foo”', 4)

    def test_uses(self):
        cases = {
            'plasma_run,,,,': dict(uses_pump=False, uses_syringe=False, uses_scale=False, uses_rpi=True,
                                   uses_plasma=True, uses_motors=[]),
            'rpi_status,,,,': dict(uses_pump=False, uses_rpi=True, uses_plasma=False),
            'nano_prepare,,,,': dict(uses_pump=False, uses_rpi=True, uses_plasma=False),
            'spectrum_capture,,,,': dict(uses_pump=False, uses_rpi=True, uses_plasma=False),
            'nano_stop,,,,': dict(uses_pump=False, uses_rpi=True, uses_plasma=False),
            'power_off,,,,': dict(uses_pump=False, uses_rpi=True, uses_plasma=False),
            'delay,,1,s,': dict(uses_pump=False, uses_rpi=False, uses_plasma=False, uses_scale=False, uses_motors=[]),
            'dose_volume,M5,1,mL,': dict(uses_pump=True, uses_rpi=False, uses_scale=False, uses_syringe=False,
                                         uses_motors=[5]),
            'dose_reverse,M6,1,mL,': dict(uses_pump=True, uses_scale=False, uses_motors=[6]),
            'dose_weight,M9,1,g,': dict(uses_pump=True, uses_scale=True, uses_motors=[9]),
            'scale_read,,,,': dict(uses_pump=True, uses_scale=True, uses_syringe=False, uses_motors=[]),
            'clean,,,,': dict(uses_pump=True, uses_scale=False, uses_rpi=False, uses_motors=[2, 8]),
            'syringe_init,,,,': dict(uses_pump=True, uses_syringe=True, uses_scale=False, uses_motors=[]),
        }
        for row, expected in cases.items():
            with self.subTest(row=row):
                result = check(row)
                self.assertTrue(result['valid'], result['error'])
                self.assertEqual({key: result[key] for key in expected}, expected)
        result = check('pump_on,M7,5,mL/min,', 'pump_reverse,M3,5,mL/min,', 'delay,,1,s,', 'pump_off,M3,,,',
                       'pump_off,M7,,,')
        self.assertEqual((result['uses_motors'], result['uses_pump']), ([3, 7], True))

    def test_liquids_add_up_per_motor_in_the_order_of_first_use(self):
        result = check('dose_weight,M9,10,g,', 'dose_volume,M2,20,mL,', 'dose_reverse,M2,0.25,mL,', 'dose_weight,M9,2.5,g,',
                       'dose_volume,M9,1.5,mL,', 'pump_on,M8,13.3,mL/min,', 'delay,,7,s,', 'pump_off,M8,,,',
                       'pump_on,M2,30,mL/min,', 'delay,,4,s,', 'pump_off,M2,,,', 'clean,,,,', 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual(result['liquids'], [
            {'motor': 9, 'label': 'Pump 8 · CuSO₄', 'ml': 1.5, 'g': 12.5, 'approx': False},
            {'motor': 2, 'label': 'Pump 1 · water', 'ml': 22.25, 'g': None, 'approx': True},       # 20 + 0.25 + 30 × 4 ÷ 60
            {'motor': 8, 'label': 'Pump 7 · OUT', 'ml': 1.552, 'g': None, 'approx': True}])     # 13.3 × 7 ÷ 60
        self.assertEqual(result['uses_motors'], [2, 8, 9])
        for item in result['liquids']:
            self.assertEqual(list(item), ['motor', 'label', 'ml', 'g', 'approx'])

    def test_planned_on_time_counts_only_the_delays_while_the_pump_runs(self):
        result = check('delay,,9,s,', 'pump_on,M8,40,mL/min,', 'delay,,2.5,s,', 'pump_on,M2,10,mL/min,', 'delay,,4,s,',
                       'pump_off,M8,,,', 'delay,,1,s,', 'pump_off,M2,,,', 'delay,,9,s,', 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual([item['planned_s'] for item in result['steps']],
                         [None, 6.5, None, 5, None, None, None, None, None, None])
        self.assertEqual(result['steps'][1]['text'], 'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 6.5 seconds)')
        self.assertEqual(result['delay_seconds'], 25.5)

    def test_phases_of_the_example_of_the_api(self):
        result = check('dose_weight,M9,10,g,', 'dose_volume,M2,20,mL,', 'delay,,5,s,', 'scale_read,,,,', 'plasma_run,,,,',
                       'pump_on,M8,40,mL/min,', 'delay,,60,s,', 'pump_off,M8,,,')
        self.assertEqual(result['phases'], [{'name': 'Add liquid', 'from': 1, 'to': 4}, {'name': 'Measure', 'from': 5, 'to': 5},
                                            {'name': 'Empty', 'from': 6, 'to': 8}])      # what the OUT pump does
        self.assertEqual([item['phase'] for item in result['steps']], [0, 0, 0, 0, 1, 2, 2, 2])

    def test_phase_classes(self):
        def names(*rows):
            result = check(*rows)
            self.assertTrue(result['valid'], result['error'])
            return [result['phases'][item['phase']]['name'] for item in result['steps']], result['phases']

        # delay takes the class of the step before it; Waiting when it is the first.
        found, phases = names('delay,,1,s,', 'delay,,1,s,', 'clean,,,,', 'delay,,1,s,', 'rpi_status,,,,', 'nano_prepare,,,,',
                              'plasma_run,,,,', 'delay,,1,s,', 'spectrum_capture,,,,', 'nano_stop,,,,', 'delay,,1,s,',
                              'power_off,,,,', 'scale_read,,,,')
        self.assertEqual(found, ['Waiting', 'Waiting', 'Cleaning', 'Cleaning', 'Measure', 'Measure', 'Measure', 'Measure', 'Measure', 'Turn off high voltage', 'Turn off high voltage',
                                 'Turn off high voltage', 'Add liquid'])
        self.assertEqual(phases, [{'name': 'Waiting', 'from': 1, 'to': 2}, {'name': 'Cleaning', 'from': 3, 'to': 4},
                                  {'name': 'Measure', 'from': 5, 'to': 9}, {'name': 'Turn off high voltage', 'from': 10, 'to': 12},
                                  {'name': 'Add liquid', 'from': 13, 'to': 13}])
        # syringe_speed takes the class of the next device step (delays do not count as one).
        found, _ = names('syringe_init,,,,', 'plasma_run,,,,', 'syringe_speed,,1,mL/s,', 'delay,,1,s,', 'plasma_run,,,,',
                         'syringe_speed,,1,mL/s,', 'syringe_speed,,2,mL/s,', 'syringe_aspirate,CH1,1,mL,',
                         'syringe_dispense,CH1,1,mL,', 'plasma_run,,,,', 'syringe_speed,,1,mL/s,', 'delay,,1,s,')
        self.assertEqual(found, ['Add liquid', 'Measure', 'Measure', 'Measure', 'Measure', 'Add liquid', 'Add liquid', 'Add liquid', 'Add liquid', 'Measure', 'Add liquid', 'Add liquid'])
        found, _ = names('pump_on,M8,1,mL/min,', 'delay,,1,s,', 'pump_off,M8,,,', 'dose_volume,M2,1,mL,',
                         'dose_reverse,M2,1,mL,', 'dose_weight,M9,1,g,')
        self.assertEqual(found, ['Empty'] * 3 + ['Add liquid'] * 3)      # what the OUT pump does is Empty (AMENDMENTS M16)

    def test_result_shape_when_nothing_could_be_parsed(self):
        result = recipe.validate('nonsense', HARDWARE)
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result, {'valid': False, 'error': 'CSV The first column of must be action,target,value,unit,note',
                                  'error_line': None, 'warnings': [], 'step_count': 0, 'rounds': 0, 'delay_seconds': 0,
                                  'phases': [], 'uses_pump': False, 'uses_motors': [], 'uses_syringe': False,
                                  'uses_scale': False, 'uses_rpi': False, 'uses_plasma': False, 'plasma_count': 0,
                                  'liquids': [], 'steps': [], 'rows': []})

    def test_validate_changes_nothing_it_is_given(self):
        hardware = copy.deepcopy(HARDWARE)
        text = text_of(*[row for row, _ in EVERY_ACTION])
        first = recipe.validate(text, hardware)
        self.assertEqual(hardware, HARDWARE)
        self.assertEqual(recipe.validate(text, hardware), first)


# =========================================================================================================
class ExampleTests(Case):
    FILES = {
        'plasma-only.csv': ('Only discharge measurement', HEAD + 'plasma_run,,,,discharge＋capture spectrum\n'),
        'dose-measure-drain.csv': ('Example: Add liquid＋Measure＋discharge', HEAD + (
            'dose_weight,P8,10,g,join in CuSO₄ solution 10 g (Confirm with scale) .Please modify the value according to actual needs\n'
            'dose_volume,P1,20,mL,add water 20 mL\n'
            'delay,,5,s,and other liquid levels are stable\n'
            'scale_read,,,,Weight after adding liquid\n'
            'plasma_run,,,,discharge＋capture spectrum\n'
            'pump_on,P7,40,mL/min,discharge (OUT)\n'
            'delay,,60,s,Row 60 Second\n'
            'pump_off,P7,,,\n')),
        'cleaning.csv': ('Example: automatic cleaning', HEAD + 'clean,,,,Automatic cleaning: water first; simultaneous rinse; then drain.\n'),
    }

    def load(self, name):
        text, encoding = recipe.decode((recipe.EXAMPLES_DIR / name).read_bytes())
        self.assertEqual(encoding, 'utf-8')
        return recipe.validate(text, HARDWARE, 5000, encoding=encoding)

    def test_list_and_files(self):
        self.assertEqual(recipe.EXAMPLES_DIR, ROOT / 'pc' / 'recipes')
        self.assertEqual([(item['file'], item['name']) for item in recipe.EXAMPLES],
                         [(name, title) for name, (title, _) in self.FILES.items()])
        for item in recipe.EXAMPLES:
            self.assertEqual(sorted(item), ['file', 'name', 'note'])
            self.assertTrue(item['note'])
        self.assertEqual(sorted(path.name for path in recipe.EXAMPLES_DIR.glob('*.csv')), sorted(self.FILES))
        for name, (_, text) in self.FILES.items():
            with self.subTest(name=name):
                raw = (recipe.EXAMPLES_DIR / name).read_bytes()
                self.assertEqual(raw, text.encode('utf-8'))          # UTF-8 without BOM, LF, as SPEC A.6 prints it
                self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
                self.assertNotIn(b'\r', raw)

    def test_example_1_only_the_discharge(self):
        result = self.load('plasma-only.csv')
        self.assertPasses(result)
        self.assertEqual((result['uses_pump'], result['step_count'], result['plasma_count']), (False, 1, 1))
        self.assertEqual((result['uses_rpi'], result['uses_plasma'], result['uses_syringe'], result['uses_scale']),
                         (True, True, False, False))
        self.assertEqual((result['warnings'], result['liquids'], result['uses_motors'], result['rounds'],
                          result['delay_seconds']), ([], [], [], 1, 0))
        self.assertEqual(result['phases'], [{'name': 'Measure', 'from': 1, 'to': 1}])
        # The reply of API.md C.3.3 for this Recipe.
        self.assertEqual(result['steps'], [
            {'n': 1, 'line': 2, 'action': 'plasma_run', 'name': 'discharge＋capture spectrum', 'target': '', 'target_label': '',
             'value': '', 'unit': '', 'note': 'discharge＋capture spectrum', 'round': 1, 'rounds': 1, 'phase': 0,
             'text': 'discharge＋capture spectrum', 'motor': None, 'motors': None, 'amounts': None, 'planned_s': None}])
        self.assertPasses(recipe.validate(self.FILES['plasma-only.csv'][1], None))   # works with no pump board

    def test_example_2_dose_measure_drain(self):
        result = self.load('dose-measure-drain.csv')
        self.assertPasses(result)
        self.assertEqual(result['warnings'], [])
        self.assertEqual((result['step_count'], result['rounds'], result['delay_seconds'], result['plasma_count']),
                         (8, 1, 65, 1))
        self.assertEqual((result['uses_pump'], result['uses_motors'], result['uses_syringe'], result['uses_scale'],
                          result['uses_rpi'], result['uses_plasma']), (True, [2, 8, 9], False, True, True, True))
        self.assertEqual(result['phases'], [{'name': 'Add liquid', 'from': 1, 'to': 4}, {'name': 'Measure', 'from': 5, 'to': 5},
                                            {'name': 'Empty', 'from': 6, 'to': 8}])
        self.assertEqual(result['liquids'], [
            {'motor': 9, 'label': 'Pump 8 · CuSO₄', 'ml': None, 'g': 10.0, 'approx': False},
            {'motor': 2, 'label': 'Pump 1 · water', 'ml': 20.0, 'g': None, 'approx': False},
            {'motor': 8, 'label': 'Pump 7 · OUT', 'ml': 40.0, 'g': None, 'approx': True}])
        self.assertEqual([item['text'] for item in result['steps']], [
            'Pump 8 · CuSO₄ scale weight added to 10 g', 'Pump 1 · water quantitative 20 mL', 'Waiting 5 Second', 'Record weight: Weight after adding liquid', 'discharge＋capture spectrum',
            'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 60 seconds)', 'Waiting 60 Second', 'Pump 7 · OUT Stop'])
        self.assertEqual(result['steps'][0], {
            'n': 1, 'line': 2, 'action': 'dose_weight', 'name': 'Quantitative (weight)', 'target': 'P8',
            'target_label': 'Pump 8 · CuSO₄', 'value': '10', 'unit': 'g',
            'note': 'join in CuSO₄ solution 10 g (Confirm with scale) .Please modify the value according to actual needs', 'round': 1, 'rounds': 1, 'phase': 0,
            'text': 'Pump 8 · CuSO₄ scale weight added to 10 g', 'motor': 9, 'motors': None, 'amounts': None, 'planned_s': None})
        self.assertEqual([item['planned_s'] for item in result['steps']], [None] * 5 + [60, None, None])
        self.assertEqual([item['line'] for item in result['steps']], list(range(2, 10)))

    def test_example_3_cleaning(self):
        result = self.load('cleaning.csv')
        self.assertPasses(result)
        self.assertEqual((result['uses_pump'], result['uses_rpi'], result['uses_plasma'], result['plasma_count'],
                          result['step_count'], result['uses_motors']), (True, False, False, 0, 1, [2, 8]))
        self.assertEqual(result['warnings'], [W5])
        self.assertEqual(result['phases'], [{'name': 'Cleaning', 'from': 1, 'to': 1}])
        self.assertEqual(result['steps'][0]['text'], 'automatic cleaning (Pump 1 Water ingress,Pump 7 discharge, approx. 122 seconds)')

    def test_examples_pass_with_the_small_syringe_too(self):
        for name, (_, text) in self.FILES.items():
            self.assertPasses(recipe.validate(text, HARDWARE, 1250))


# =========================================================================================================
class EstimateTests(Case):
    def one(self, action, target='', value='', hardware=HARDWARE, plasma=21, speed=0.4, **more):
        return recipe.estimate(step(action, target, value), hardware, plasma, speed, **more)

    def test_every_action(self):
        self.assertEqual(self.one('delay', value='12.5'), 12.5)
        self.assertEqual(self.one('delay', value='0'), 0.0)
        for action in ('pump_on', 'pump_reverse'):
            self.assertEqual(self.one(action, 'M8', '40'), 1.5)
            self.assertEqual((self.one(action, 'M2', '40'), self.one(action, 'M9', '40')), (2.5, 2.5))   # valve opens
        self.assertEqual((self.one('pump_off', 'M8'), self.one('pump_off', 'M2'), self.one('pump_off', 'M9')), (1.0, 2.0, 2.0))
        self.assertEqual(self.one('dose_weight', 'M9', '10'), 31.5)          # 10 ÷ (40 ÷ 60) × 1.3 + 12
        self.assertEqual(self.one('clean'), 124.0)                           # the YAML's 122 + 2
        self.assertEqual(self.one('syringe_init'), 6.0)
        self.assertEqual(self.one('syringe_speed', value='1'), 0.5)
        for action in ('syringe_aspirate', 'syringe_dispense'):
            self.assertEqual(self.one(action, 'CH2', '1'), 4.5)              # 1 ÷ 0.4 + 2
            self.assertEqual(self.one(action, 'CH2', '1', speed=2), 2.5)
        self.assertEqual(self.one('scale_read'), 1.0)
        self.assertEqual(self.one('rpi_status'), 3.0)
        self.assertEqual(self.one('nano_prepare'), 6.0)
        self.assertEqual((self.one('nano_stop'), self.one('power_off')), (5.0, 5.0))
        self.assertIsInstance(self.one('delay', value='1'), float)

    def test_volume_dose_counts_steps_as_v7_does(self):
        full = dict(HARDWARE, **PULSES)
        # steps = round(20 × 4558.96147256) = 91179, sps = round(40 × 4237.00761337 ÷ 60) = 2825
        self.assertEqual(self.one('dose_volume', 'M8', '20', hardware=full), round(91179 / 2825 * 1.1 + 1, 1))
        self.assertEqual(self.one('dose_volume', 'M8', '20', hardware=full), 36.5)
        self.assertEqual(self.one('dose_reverse', 'M2', '20', hardware=full), 38.5)   # + valve open and close
        self.assertEqual(self.one('dose_volume', 'M9', '20', hardware=full), 38.5)
        # bootstrap.hardware alone has no pulse numbers: the two are taken as equal.
        self.assertEqual(self.one('dose_volume', 'M8', '20'), 34.0)                   # 20 ÷ (40 ÷ 60) × 1.1 + 1
        self.assertEqual(self.one('dose_reverse', 'M2', '20'), 36.0)

    def test_plasma_and_spectrum(self):
        self.assertEqual(self.one('plasma_run', plasma=21), 21.0)
        self.assertEqual(self.one('plasma_run', plasma=17.4), 17.4)
        for unknown in (None, 0, 'x'):
            self.assertEqual(self.one('plasma_run', plasma=unknown), 30.0)
        self.assertEqual(self.one('spectrum_capture'), 11.0)                          # 3 × 1000 ÷ 1000 + 8
        self.assertEqual(self.one('spectrum_capture', integration_ms=2000), 14.0)
        self.assertEqual(self.one('spectrum_capture', integration_ms=None), 11.0)

    def test_a_syringe_speed_row_applies_to_the_moves_after_it(self):
        result = check('syringe_init,,,,', 'syringe_aspirate,CH2,1,mL,', 'syringe_speed,,1,mL/s,',
                       'syringe_dispense,CH1,1,mL,')
        self.assertPasses(result)
        self.assertEqual(recipe.estimate(result, HARDWARE, 21, 0.4), 6 + (1 / 0.4 + 2) + 0.5 + (1 / 1 + 2))
        self.assertEqual(recipe.estimate(result, HARDWARE, 21, 0.4), 14.0)
        self.assertEqual(recipe.estimate(result, HARDWARE, 21, 2), 12.0)              # 6 + (1 ÷ 2 + 2) + 0.5 + 3
        for unknown in (None, 0, 'x'):
            self.assertEqual(recipe.estimate(result, HARDWARE, 21, unknown), 14.0)    # no saved speed: 0.4 mL/s

    def test_examples(self):
        expected = {'plasma-only.csv': (21.0, 21.0), 'dose-measure-drain.csv': (157.0, 159.5),
                    'cleaning.csv': (124.0, 124.0)}
        for name, (plain, exact) in expected.items():
            with self.subTest(name=name):
                result = recipe.validate((recipe.EXAMPLES_DIR / name).read_text(encoding='utf-8'), HARDWARE)
                self.assertEqual(recipe.estimate(result, HARDWARE, 21, 0.4), plain)
                self.assertEqual(recipe.estimate(result, dict(HARDWARE, **PULSES), 21, 0.4), exact)
        # 31.5 + (34 + 2) + 5 + 1 + 21 + 1.5 + 60 + 1
        self.assertEqual(31.5 + 36 + 5 + 1 + 21 + 1.5 + 60 + 1, 157.0)

    def test_repeats_are_counted_as_often_as_they_run(self):
        result = check('repeat_start,,3,,', 'delay,,10,s,', 'plasma_run,,,,', 'repeat_end,,,,')
        self.assertEqual(recipe.estimate(result, HARDWARE, None, 0.4), 3 * (10 + 30.0))

    def test_never_fails(self):
        self.assertEqual(recipe.estimate(recipe.validate('nonsense', HARDWARE), HARDWARE, 21, 0.4), 0.0)
        self.assertEqual(recipe.estimate({}, None, None, None), 0.0)
        self.assertEqual(recipe.estimate(None, None, None, None), 0.0)
        invalid = check('pump_on,M8,40,mL/min,', 'delay,,5,s,')                  # R7, but its steps are counted
        self.assertFalse(invalid['valid'])
        self.assertEqual(recipe.estimate(invalid, HARDWARE, 21, 0.4), 6.5)
        prompt = {'steps': [{'action': 'operator_prompt', 'target': '', 'value': ''}, {'action': 'delay', 'value': '2'}]}
        self.assertEqual(recipe.estimate(prompt, HARDWARE, 21, 0.4), 2.0)           # a generated step it does not know
        self.assertEqual(recipe.estimate(step('dose_volume', 'M2', '20'), None, 21, 0.4), 34.0)


# =========================================================================================================
class ComposeTests(Case):
    ROWS = [['dose_weight', 'M9', '10', 'g', 'join in CuSO₄ solution, 10 g "Confirm with scale"'],
            ['repeat_start', '', '2', '', 'Do it twice'],
            ['delay', '', '0.5', 's', ''],
            ['repeat_end', '', '', '', ''],
            ['plasma_run', '', '', '', 'discharge＋capture spectrum']]

    def cells(self, result):
        return [[row[key] for key in recipe.HEADER] for row in result['rows']]

    def test_round_trip_through_validate(self):
        text = recipe.compose(self.ROWS)
        self.assertTrue(text.startswith(HEAD))
        self.assertTrue(text.endswith('\n'))
        self.assertNotIn('\r', text)
        self.assertEqual(text.count('\n'), 6)
        result = recipe.validate(text, HARDWARE)
        self.assertPasses(result)
        self.assertEqual(self.cells(result), self.ROWS)
        self.assertEqual([row['line'] for row in result['rows']], [2, 3, 4, 5, 6])
        self.assertEqual(recipe.compose(self.cells(result)), text)                  # and it stays the same text
        self.assertEqual(recipe.compose(tuple(tuple(row) for row in self.ROWS)), text)
        self.assertEqual(recipe.decode(text.encode('utf-8')), (text, 'utf-8'))

    def test_examples_come_back_byte_for_byte(self):
        for name in ExampleTests.FILES:
            with self.subTest(name=name):
                text = (recipe.EXAMPLES_DIR / name).read_text(encoding='utf-8')
                self.assertEqual(recipe.compose(self.cells(recipe.validate(text, HARDWARE))), text)

    def test_an_empty_list_is_a_recipe_without_steps(self):
        self.assertEqual(recipe.compose([]), HEAD)
        result = recipe.validate(recipe.compose([]), HARDWARE)
        self.assertRefused(result, NO_STEPS)
        self.assertEqual(result['rows'], [])

    def test_a_draft_that_is_not_valid_yet_keeps_all_its_rows(self):
        rows = [['pump_on', 'M8', '40', 'mL/min', 'discharge'], ['delay', '', '60', 's', ''], ['plasma_run', '', '', '', '']]
        result = recipe.validate(recipe.compose(rows), HARDWARE)
        self.assertRefused(result, 'No. 4 List: Do“discharge＋Stop all pumps before capturing the spectrum.', 4)
        self.assertEqual(self.cells(result), rows)
        rows[1] = ['delay', '', 'Sixty', 's', '']
        result = recipe.validate(recipe.compose(rows), HARDWARE)
        self.assertRefused(result, 'No. 3 column: The waiting value must be within 0–3600 between', 3)
        self.assertEqual(self.cells(result), rows)

    def test_what_is_not_five_strings_is_refused(self):
        message = 'The step data format is incorrect.'
        good = ['delay', '', '1', 's', '']
        for rows in (None, 'delay', {'rows': []}, 5, [good, None], [good[:4]], [good + ['']], ['delay'], [[]],
                     [['delay', '', 1, 's', '']], [['delay', '', None, 's', '']], [['delay', '', '1', 's', b'']],
                     ['abcde'], [{'action': 'delay'}], [good] * 501, [['delay', '', '1', 's', '\ud800']]):
            with self.subTest(rows=str(rows)[:40]):
                self.assertRaisesText(message, recipe.compose, rows)
        self.assertEqual(recipe.compose([good] * 500).count('\n'), 501)

    def test_line_breaks_and_control_characters_in_a_cell_become_a_space(self):
        text = recipe.compose([['delay', '', '1', 's', 'first line\r\nsecond line\x00\ttail'], [' plasma_run ', '', '', '', '  Remarks  ']])
        self.assertEqual(text, HEAD + 'delay,,1,s,first line second line tail\nplasma_run,,,,Remarks\n')
        result = recipe.validate(text, HARDWARE)
        self.assertPasses(result)
        self.assertEqual(len(result['rows']), 2)


# =========================================================================================================
class RowsTests(Case):
    def test_rows_of_a_valid_recipe(self):
        result = check('dose_volume,M2,20,mL,add water', 'pump_on,M8,40,mL/min,discharge', 'delay,,60,s,', 'pump_off,M8,,,',
                       'plasma_run,,,,Measure')
        self.assertPasses(result)
        self.assertEqual(result['rows'], [
            {'line': 2, 'action': 'dose_volume', 'target': 'M2', 'value': '20', 'unit': 'mL', 'note': 'add water',
             'name': 'Quantitative (volume)', 'text': 'Pump 1 · water quantitative 20 mL', 'ok': True},
            {'line': 3, 'action': 'pump_on', 'target': 'M8', 'value': '40', 'unit': 'mL/min', 'note': 'discharge',
             'name': 'Start pump (forward rotation)', 'text': 'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 60 seconds)', 'ok': True},
            {'line': 4, 'action': 'delay', 'target': '', 'value': '60', 'unit': 's', 'note': '', 'name': 'Waiting',
             'text': 'Waiting 60 Second', 'ok': True},
            {'line': 5, 'action': 'pump_off', 'target': 'M8', 'value': '', 'unit': '', 'note': '', 'name': 'stop pump',
             'text': 'Pump 7 · OUT Stop', 'ok': True},
            {'line': 6, 'action': 'plasma_run', 'target': '', 'value': '', 'unit': '', 'note': 'Measure',
             'name': 'discharge＋capture spectrum', 'text': 'discharge＋capture spectrum', 'ok': True}])
        for row in result['rows']:
            self.assertEqual(list(row), ROW_KEYS)

    def test_a_bad_row_is_marked_and_the_others_are_still_listed(self):
        result = check('delay,,5,s,wait', 'pump_on,M99,40,mL/min,bad', 'plasma_run,,,,Measure')
        self.assertRefused(result, 'No. 3 Column: The object to start the pump (forward rotation) needs to be written P1–P8', 3)
        self.assertEqual(result['rows'], [
            {'line': 2, 'action': 'delay', 'target': '', 'value': '5', 'unit': 's', 'note': 'wait', 'name': 'Waiting',
             'text': 'Waiting 5 Second', 'ok': True},
            {'line': 3, 'action': 'pump_on', 'target': 'M99', 'value': '40', 'unit': 'mL/min', 'note': 'bad',
             'name': 'Start pump (forward rotation)', 'text': 'pump_on · M99 · 40 · mL/min', 'ok': False},
            {'line': 4, 'action': 'plasma_run', 'target': '', 'value': '', 'unit': '', 'note': 'Measure',
             'name': 'discharge＋capture spectrum', 'text': 'discharge＋capture spectrum', 'ok': True}])
        self.assertEqual((result['steps'], result['step_count'], result['phases']), ([], 0, []))

    def test_an_unknown_action_has_no_name(self):
        result = check('plasma_run,,,,', 'wash,M2,,,Wash it', 'delay,,5,min,', 'delay,,5,s,')
        self.assertRefused(result, 'No. 3 Column: Unsupported action ”wash”', 3)
        rows = result['rows']
        self.assertEqual((rows[1]['name'], rows[1]['text'], rows[1]['ok'], rows[1]['note']),
                         ('', 'wash · M2', False, 'Wash it'))
        # A later bad row shows what it holds, but only the reported row is marked.
        self.assertEqual((rows[2]['name'], rows[2]['text'], rows[2]['ok']), ('Waiting', 'delay · 5 · min', True))
        self.assertEqual((rows[3]['text'], rows[3]['ok']), ('Waiting 5 Second', True))

    def test_repeat_rows(self):
        result = check('repeat_start,,3,,Do it three times', 'delay,,1,s,', 'repeat_end,,,,', 'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual(result['rows'], [
            {'line': 2, 'action': 'repeat_start', 'target': '', 'value': '3', 'unit': '', 'note': 'Do it three times',
             'name': 'Repeat start', 'text': 'Repeat start ×3', 'ok': True},
            {'line': 3, 'action': 'delay', 'target': '', 'value': '1', 'unit': 's', 'note': '', 'name': 'Waiting',
             'text': 'Waiting 1 Second', 'ok': True},
            {'line': 4, 'action': 'repeat_end', 'target': '', 'value': '', 'unit': '', 'note': '', 'name': 'Repeat end',
             'text': 'Repeat end', 'ok': True},
            {'line': 5, 'action': 'plasma_run', 'target': '', 'value': '', 'unit': '', 'note': '',
             'name': 'discharge＋capture spectrum', 'text': 'discharge＋capture spectrum', 'ok': True}])
        self.assertEqual((len(result['rows']), result['step_count']), (4, 4))        # rows are not expanded

    def test_repeat_rows_that_are_wrong(self):
        result = check('repeat_start,,30,,', 'delay,,1,s,', 'repeat_end,,,,')
        self.assertRefused(result, 'No. 2 column:repeat_start If the number of 1–20 integer', 2)
        self.assertEqual([(row['name'], row['text'], row['ok']) for row in result['rows']],
                         [('Repeat start', 'repeat_start · 30', False), ('Waiting', 'Waiting 1 Second', True), ('Repeat end', 'Repeat end', True)])
        result = check('delay,,1,s,', 'repeat_end,,,,')
        self.assertRefused(result, 'No. 3 column:repeat_end has no corresponding repeat_start', 3)
        self.assertEqual([(row['text'], row['ok']) for row in result['rows']], [('Waiting 1 Second', True), ('Repeat end', False)])
        result = check('repeat_start,,2,,', 'delay,,1,s,')
        self.assertRefused(result, 'No. 2 column:repeat_start less repeat_end', 2)
        self.assertEqual([(row['text'], row['ok']) for row in result['rows']], [('Repeat start ×2', False), ('Waiting 1 Second', True)])

    def test_a_row_the_order_of_the_steps_makes_wrong_keeps_its_sentence(self):
        result = check('pump_on,M8,40,mL/min,', 'delay,,5,s,', 'plasma_run,,,,', 'pump_off,M8,,,')
        self.assertRefused(result, 'No. 4 List: Do“discharge＋Stop all pumps before capturing the spectrum.', 4)
        self.assertEqual([(row['text'], row['ok']) for row in result['rows']], [
            ('Pump 7 · OUT Forward rotation 40 mL/min (longest running time 5 seconds)', True), ('Waiting 5 Second', True), ('discharge＋capture spectrum', False),
            ('Pump 7 · OUT Stop', True)])
        self.assertEqual(result['step_count'], 4)

    def test_a_pump_row_names_its_on_time_only_when_it_is_known(self):
        result = check('pump_on,M8,40,mL/min,', 'delay,,5,s,')                     # no pump_off yet (a draft)
        self.assertRefused(result, 'The pump is still running at the end of the process:Pump 7 · OUT (No. 2 column startup)', 2)
        self.assertEqual([(row['text'], row['ok']) for row in result['rows']],
                         [('Pump 7 · OUT Forward rotation 40 mL/min', False), ('Waiting 5 Second', True)])
        result = check('pump_reverse,M8,40,mL/min,', 'delay,,5,s,', 'pump_off,M8,,,', 'foo,,,,')
        self.assertEqual(result['rows'][0]['text'], 'Pump 7 · OUT reverse 40 mL/min')   # a bad row: nothing was walked

    def test_a_pump_row_in_a_repeat_names_its_longest_on_time(self):
        result = check('pump_on,M8,40,mL/min,', 'repeat_start,,2,,', 'delay,,10,s,', 'pump_off,M8,,,', 'delay,,1,s,',
                       'pump_on,M8,40,mL/min,', 'delay,,5,s,', 'repeat_end,,,,', 'delay,,7,s,', 'pump_off,M8,,,',
                       'plasma_run,,,,')
        self.assertPasses(result)
        self.assertEqual([item['planned_s'] for item in result['steps'] if item['action'] == 'pump_on'], [10, 15, 12])
        self.assertEqual(result['rows'][0]['text'], 'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 10 seconds)')
        self.assertEqual(result['rows'][5]['text'], 'Pump 7 · OUT Forward rotation 40 mL/min (longest running time 15 seconds)')
        self.assertEqual(result['liquids'][0]['ml'], 24.667)                        # 40 × (10 + 15 + 12) ÷ 60

    def test_what_a_wrong_row_holds_is_shown_cut_to_a_line(self):
        result = check('delay,,' + '9' * 500 + ',s,')
        self.assertRefused(result, 'No. 2 column: The waiting value must be within 0–3600 between', 2)
        self.assertEqual(result['rows'][0]['text'], ('delay · ' + '9' * 500)[:200] + '…')
        self.assertEqual(result['rows'][0]['value'], '9' * 500)                     # the cell itself is kept

    def test_a_row_with_the_wrong_number_of_cells(self):
        result = check('plasma_run,,', 'delay,,5,s,wait,Many,', 'plasma_run,,,,')
        self.assertRefused(result, 'No. 2 Column: The number of fields is incorrect (required 5 column) ', 2)
        self.assertEqual(result['rows'], [
            {'line': 2, 'action': 'plasma_run', 'target': '', 'value': '', 'unit': '', 'note': '', 'name': '',
             'text': 'plasma_run', 'ok': False},
            {'line': 3, 'action': 'delay', 'target': '', 'value': '5', 'unit': 's', 'note': 'wait', 'name': '',
             'text': 'delay · 5 · s · wait · Many', 'ok': True},
            {'line': 4, 'action': 'plasma_run', 'target': '', 'value': '', 'unit': '', 'note': '',
             'name': 'discharge＋capture spectrum', 'text': 'discharge＋capture spectrum', 'ok': True}])


# =========================================================================================================
class ModuleTests(Case):
    SOURCE = ROOT / 'pc' / 'recipe.py'

    def test_standard_library_only_and_nothing_of_the_program(self):
        allowed = {'__future__', 'csv', 'io', 're', 'decimal', 'pathlib'}
        found = set()
        for node in ast.walk(ast.parse(self.SOURCE.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                found.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                found.add(node.module.split('.')[0])
            elif isinstance(node, ast.Call) and getattr(node.func, 'id', '') in ('open', 'exec', 'eval', '__import__'):
                self.fail('recipe.py must stay pure: %s()' % node.func.id)
        self.assertEqual(found - allowed, set())
        for name in ('yaml', 'serial', 'pumpboard', 'app', 'app2', 'backend'):
            self.assertNotIn(name, found)

    def test_files_of_the_package_are_utf8_and_lf(self):
        for path in (self.SOURCE, Path(__file__), *sorted(recipe.EXAMPLES_DIR.glob('*.csv'))):
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                text = raw.decode('utf-8')
                self.assertNotIn(b'\r', raw)
                self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
                self.assertTrue(raw.endswith(b'\n'))
                # Nothing invisible: no control or format character (a byte-order mark inside a text, a
                # zero-width space), no tab, no space other than the plain one.
                odd = {hex(ord(char)) for char in text
                       if char != '\n' and (unicodedata.category(char) in ('Cc', 'Cf', 'Co', 'Cn', 'Zl', 'Zp')
                                            or (unicodedata.category(char) == 'Zs' and char != ' '))}
                self.assertEqual(odd, set())

    def test_frozen_names(self):
        for name in ('decode', 'validate', 'estimate', 'compose', 'actions_view', 'parse', 'expand'):
            self.assertTrue(callable(getattr(recipe, name)), name)
        self.assertIsInstance(recipe.ACTIONS, dict)
        self.assertIsInstance(recipe.EXAMPLES, list)


# =========================================================================================================
@unittest.skipUnless(YAML.is_file(), 'pc\\vendor\\v7\\config\\hardware.yaml is not there')
class YamlTests(Case):
    """HARDWARE above is written by hand; this reads the vendored YAML as plain text to see that it still fits."""

    @classmethod
    def setUpClass(cls):
        cls.text = YAML.read_text(encoding='utf-8')

    def value(self, key):
        match = re.search(r'^\s*%s:\s*([^#\n]+?)\s*(?:#.*)?$' % re.escape(key), self.text, re.M)
        self.assertIsNotNone(match, key)
        return match.group(1)

    def channels(self, text):
        found = re.findall(r'^\s*- \{ id: (\d+), pin: \d+, label: ([^,}]+?)(?:, fluid: ([^}]+?))? \}\s*$', text, re.M)
        return [(int(number), label, fluid or None) for number, label, fluid in found]

    def test_motors_labels_and_liquids(self):
        motors, valves = self.text.split('\nvalves:')
        fixture = [(motor['id'], motor['label'], motor['fluid']) for group in HARDWARE['groups'] for motor in group['motors']]
        self.assertEqual([(number, 'Pump %d' % (index + 1) + (' · %s' % fluid if fluid else ''), fluid)
                          for index, (number, _label, fluid) in enumerate(self.channels(motors))], fixture)
        self.assertEqual([number for number, _label, _fluid in self.channels(motors)], [2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual([motor['pump'] for group in HARDWARE['groups'] for motor in group['motors']], list(range(1, 9)))
        self.assertEqual(self.channels(valves), [(valve['id'], valve['label'], None) for valve in HARDWARE['valves']])
        links = dict((int(motor), int(valve)) for motor, valve in
                     re.findall(r'\{ motor: (\d+), valve: (\d+),', self.text))
        self.assertEqual(links, {motor['id']: motor['valve'] for group in HARDWARE['groups']
                                 for motor in group['motors'] if motor['valve'] is not None})
        self.assertEqual(re.findall(r'^\s*- id: (\w)\n\s*label: (.+)$', self.text, re.M),
                         [(group['id'], group['label']) for group in HARDWARE['groups']])

    def test_numbers(self):
        self.assertEqual(float(self.value('max_flow_ml_min')), HARDWARE['max_flow_ml_min'])
        self.assertEqual(float(self.value('dose_flow_ml_min')), HARDWARE['dose_flow_ml_min'])
        self.assertEqual(float(self.value('fast_flow_ml_min')), HARDWARE['gravimetric']['fast_flow_ml_min'])
        self.assertEqual(self.value('enabled'), 'true')
        self.assertEqual(int(self.value('valve_positions')), HARDWARE['syringe']['valve_positions'])
        self.assertEqual(self.value('syringe_options_ul'), '[5000, 1250]')
        self.assertEqual(int(self.value('full_stroke_steps')), recipe.SYRINGE_STROKE_STEPS)
        self.assertEqual((int(self.value('open_before_ms')), int(self.value('close_after_ms'))),
                         (HARDWARE['valve_open_before_ms'], HARDWARE['valve_close_after_ms']))
        cleaning = HARDWARE['cleaning']
        self.assertEqual((int(self.value('water_motor')), int(self.value('out_motor'))),
                         (cleaning['water_motor'], cleaning['out_motor']))
        seconds = [float(self.value(key)) for key in ('water_lead_s', 'overlap_s', 'out_tail_s')]
        self.assertEqual(seconds, [cleaning['water_lead_s'], cleaning['overlap_s'], cleaning['out_tail_s']])
        self.assertEqual(sum(seconds) + 2, cleaning['total_s'])                      # + valve open and close
        self.assertEqual(float(self.value('pulses_per_ml')), PULSES['pulses_per_ml'])
        self.assertEqual(float(self.value('dose_pulses_per_ml')), PULSES['dose_pulses_per_ml'])


# =========================================================================================================
class PumpNameTests(Case):
    """AMENDMENTS M16: a pump is written P1-P8, the number people see; M2-M9 (the pin) is still read."""

    def test_both_spellings_mean_the_same_pump(self):
        new, old = check('dose_volume,P7,5,mL,'), check('dose_volume,M8,5,mL,')
        self.assertPasses(new)
        self.assertPasses(old)
        for result, target in ((new, 'P7'), (old, 'M8')):
            step = result['steps'][0]
            self.assertEqual((step['target'], step['motor'], step['target_label'], step['text']),
                             (target, 8, 'Pump 7 · OUT', 'Pump 7 · OUT quantitative 5 mL'))
        self.assertEqual(recipe._Board(HARDWARE).target(8), 'P7')
        self.assertEqual(recipe._Board(HARDWARE).motor_range(), 'P1–P8')
        for bad in ('P0', 'P9', 'p1', 'P01', 'P', 'Pump 1'):
            self.assertRefused(check('dose_volume,%s,5,mL,' % bad), 'No. 2 Column: Quantitative (volume) object to be written P1–P8', 2)

    def test_a_hardware_view_without_numbers_counts_them_in_its_order(self):
        plain = copy.deepcopy(HARDWARE)
        for group in plain['groups']:
            for motor in group['motors']:
                del motor['pump'], motor['short']
        step = check('dose_volume,P7,5,mL,', hardware=plain)['steps'][0]
        self.assertEqual((step['motor'], step['target_label']), (8, 'Pump 7 · OUT'))


class TogetherTests(Case):
    def test_several_pumps_in_one_step(self):
        result = check('dose_together,P1+P8+P2,10+2+0.5,mL,Add liquid')
        self.assertPasses(result)
        step = result['steps'][0]
        self.assertEqual((step['text'], step['target_label'], step['motor'], step['motors'], step['amounts']),
                         ('Simultaneous quantification:Pump 1 · water: 10 mL, Pump 8 · CuSO₄: 2 mL, Pump 2: 0.5 mL',
                          'Pump 1 · water, Pump 8 · CuSO₄, Pump 2', None, [2, 9, 3], [10, 2, 0.5]))
        self.assertEqual((result['uses_pump'], result['uses_motors'], result['uses_scale']), (True, [2, 3, 9], False))
        self.assertEqual(result['liquids'], [
            {'motor': 2, 'label': 'Pump 1 · water', 'ml': 10.0, 'g': None, 'approx': False},
            {'motor': 9, 'label': 'Pump 8 · CuSO₄', 'ml': 2.0, 'g': None, 'approx': False},
            {'motor': 3, 'label': 'Pump 2', 'ml': 0.5, 'g': None, 'approx': False}])
        self.assertEqual(result['phases'], [{'name': 'Add liquid', 'from': 1, 'to': 1}])
        self.assertEqual(result['rows'][0]['text'], step['text'])
        self.assertEqual(check('dose_together,M2+P8,1+1,mL,')['steps'][0]['motors'], [2, 9])
        # Two pumps of one driver group are fine in one step: the board takes them one after the other.
        self.assertPasses(check('dose_together,P1+P2+P3+P4,1+1+1+1,mL,'))
        # It is a dose like any other: nothing else may be running (R5), and it may stand inside a repeat.
        self.assertRefused(check('pump_on,P7,40,mL/min,', 'dose_together,P1+P8,1+1,mL,', 'pump_off,P7,,,'),
                           'No. 3 column: All pumps must be stopped before doing ”simultaneous dosing”', 3)
        again = check('repeat_start,,3,,', 'dose_together,P1+P8,1+2,mL,', 'repeat_end,,,,')
        self.assertEqual([entry['ml'] for entry in again['liquids']], [3.0, 6.0])

    def test_the_pump_that_empties_the_cell_turns_alone(self):
        self.assertRefused(check('dose_together,P1+P7,1+1,mL,'), 'No. 2 column:Pump 7 · OUT is a pump for emptying and cannot be used for dosing with other pumps.', 2)
        self.assertRefused(check('dose_together,M8+P2,1+1,mL,'), 'No. 2 column:Pump 7 · OUT is a pump for emptying and cannot be used for dosing with other pumps.', 2)
        self.assertPasses(check('dose_volume,P7,20,mL,'))                       # alone it doses like any other
        # A board without the cleaning sequence names no such pump.
        plain = dict(HARDWARE, cleaning={})
        self.assertPasses(check('dose_together,P1+P7,1+1,mL,', hardware=plain))

    def test_its_time_is_that_of_the_driver_group_that_takes_longest(self):
        hardware = dict(HARDWARE, **PULSES)

        def seconds(*rows):
            return recipe.estimate(check(*rows), hardware, None, None)

        one = seconds('dose_volume,P2,10,mL,')
        self.assertAlmostEqual(seconds('dose_together,P2+P5,10+10,mL,'), one, places=1)       # side by side
        self.assertAlmostEqual(seconds('dose_together,P2+P3,10+10,mL,'), 2 * one, delta=0.2)   # one after the other
        self.assertAlmostEqual(seconds('dose_together,P2+P3+P5,10+10+1,mL,'), 2 * one, delta=0.2)
        # A pump with a linked valve waits for it (P1 and P8 have one).
        self.assertGreater(seconds('dose_together,P1+P5,10+10,mL,'), one + 1.5)
        # A calibrated pump takes as long as its own steps per mL say.
        slow = copy.deepcopy(hardware)
        slow['groups'][0]['motors'][1]['dose_pulses_per_ml'] = 2 * PULSES['dose_pulses_per_ml']
        self.assertGreater(recipe.estimate(check('dose_volume,P2,10,mL,', hardware=slow), slow, None, None), 1.8 * one)


class FlowTests(Case):
    """The four stages of the schedule page (Add liquid → Measure → Empty → Cleaning) as a Recipe."""
    FLOW = {'fill': [{'pump': 8, 'mode': 'ml', 'amount': 2}, {'pump': 1, 'mode': 'ml', 'amount': 10},
                     {'pump': 3, 'mode': 'g', 'amount': 5}],
            'measure': True, 'drain': {'on': True, 'ml': 25}, 'clean': True}

    def test_the_four_stages(self):
        text, kept = recipe.flow_csv(self.FLOW, HARDWARE)
        self.assertEqual(text, HEAD + ('dose_weight,P3,5,g,Add liquid (stop with scale)\n'      # the scale decides alone, first
                                       'dose_together,P1+P8,10+2,mL,Add liquid\n'
                                       'plasma_run,,,,discharge＋capture spectrum\n'
                                       'dose_volume,P7,25,mL,Empty\n'
                                       'clean,,,,Cleaning\n'))
        self.assertEqual(kept, {'fill': [{'pump': 1, 'mode': 'ml', 'amount': 10}, {'pump': 3, 'mode': 'g', 'amount': 5},
                                         {'pump': 8, 'mode': 'ml', 'amount': 2}],
                                'measure': True, 'drain': {'on': True, 'ml': 25}, 'clean': True})
        result = recipe.validate(text, HARDWARE)
        self.assertPasses(result)
        self.assertEqual([phase['name'] for phase in result['phases']], ['Add liquid', 'Measure', 'Empty', 'Cleaning'])
        self.assertEqual((result['uses_pump'], result['uses_scale'], result['uses_plasma'], result['uses_motors']),
                         (True, True, True, [2, 4, 8, 9]))
        self.assertEqual(recipe.flow_csv(kept, HARDWARE), (text, kept))          # what is kept reads back the same

    def test_stages_that_are_off_are_left_out(self):
        text, kept = recipe.flow_csv({'fill': [{'pump': 2, 'mode': 'ml', 'amount': 1.5}], 'measure': True}, HARDWARE)
        self.assertEqual(text, HEAD + 'dose_volume,P2,1.5,mL,Add liquid\nplasma_run,,,,discharge＋capture spectrum\n')
        self.assertEqual(kept, {'fill': [{'pump': 2, 'mode': 'ml', 'amount': 1.5}], 'measure': True,
                                'drain': {'on': False, 'ml': 20}, 'clean': False})
        # The flow nobody has set yet is the measurement alone — the Recipe “Discharge measurement only”, and it needs no pump board.
        self.assertEqual(recipe.flow_default(), {'fill': [], 'measure': True, 'drain': {'on': False, 'ml': 20}, 'clean': False})
        text, _ = recipe.flow_csv(recipe.flow_default(), None)
        self.assertEqual(text, HEAD + 'plasma_run,,,,discharge＋capture spectrum\n')
        self.assertEqual(recipe.flow_csv({'drain': {'on': True, 'ml': 30}}, HARDWARE)[0], HEAD + 'dose_volume,P7,30,mL,Empty\n')
        self.assertEqual(recipe.flow_csv({'clean': True}, HARDWARE)[0], HEAD + 'clean,,,,Cleaning\n')

    def test_what_cannot_be_a_flow(self):
        def refused(flow, sentence, hardware=HARDWARE):
            with self.assertRaises(ValueError) as caught:
                recipe.flow_csv(flow, hardware)
            self.assertEqual(str(caught.exception), sentence)

        fill = lambda pump, mode='ml', amount=1: {'fill': [{'pump': pump, 'mode': mode, 'amount': amount}]}   # noqa: E731
        for flow in (None, [], {'x': 1}, {'fill': 'P1'}, {'fill': [1]}, {'fill': [{'pump': 1}]}, {'measure': 1},
                     {'drain': []}, {'drain': {'on': 'yes'}}, fill(1, 'l'), {'fill': [{'pump': 1, 'mode': 'ml', 'amount': 1, 'x': 2}]},
                     {'fill': [{'pump': n, 'mode': 'ml', 'amount': 1} for n in (1, 2, 3, 4, 5, 6, 8, 1, 2)]}):
            refused(flow, 'The content format of the process is incorrect.')
        refused({}, 'All four stages are off: at least one must be on.')
        refused({'fill': [], 'measure': False, 'drain': {'on': False, 'ml': 20}, 'clean': False}, 'All four stages are off: at least one must be on.')
        for pump in (0, 9, '1', 1.0, True, None):
            refused(fill(pump), 'The pump for adding liquid is wrong:%s' % (pump,))
        refused(fill(7), 'Pump 7 is a pump for emptying and cannot be used for adding liquid.')
        refused({'fill': fill(1)['fill'] + fill(1, 'g')['fill']}, 'Pump 1 selected twice')
        for amount in (0, 0.001, 1000.01, 1.005, 'x', None, True, float('nan')):
            refused(fill(1, 'ml', amount), 'Pump 1 · water The quantity should be within 0.01–1000 mL , the smallest unit 0.01')
            refused(fill(2, 'g', amount), 'Pump 2 The quantity should be within 0.01–1000 g , the smallest unit 0.01')
            refused({'drain': {'on': True, 'ml': amount}}, 'Empty The quantity should be within 0.01–1000 mL , the smallest unit 0.01')
        # Without the pump board's settings only the measurement can be a flow.
        for flow in (fill(1), {'drain': {'on': True, 'ml': 20}}, {'clean': True}):
            refused(flow, 'program folder is incomplete (missing pc\\vendor) ,The pump flow cannot be set.', hardware=None)
        no_out = copy.deepcopy(HARDWARE)
        no_out['cleaning'] = {}
        refused({'drain': {'on': True, 'ml': 20}}, 'There is no pump for emptying in the setting file', hardware=no_out)



# =========================================================================================================
@unittest.skipUnless((DESIGN / 'SPEC.md').is_file() and (DESIGN / 'AMENDMENTS.md').is_file(),
                     'the design documents are not there')
class SpecTextTests(Case):
    """Every sentence of recipe.py, letter for letter, in SPEC.md (A.1 – A.3, A.6) or AMENDMENTS.md (M4.1)."""

    @classmethod
    def setUpClass(cls):
        cls.spec = (DESIGN / 'SPEC.md').read_text(encoding='utf-8')
        cls.amendments = (DESIGN / 'AMENDMENTS.md').read_text(encoding='utf-8')

    def assertQuoted(self, text, where=None):
        self.assertIn('“%s”' % text, self.spec if where is None else where)

    def test_file_and_row_messages(self):
        r = recipe
        name = dict(line='N', name='{Name}')
        for text in (r.T_UNREADABLE, r.CP950_WARNING, r.T_HEADER, r.T_TOO_BIG, r.T_TOO_MANY_ROWS,
                     r.T_COLUMNS.format(line='N'), r.T_TOO_MANY_STEPS, r.T_NESTING, r.T_REPEAT_COUNT.format(line='N'),
                     r.T_REPEAT_END.format(line='N'), r.T_REPEAT_OPEN.format(line='N'), r.T_EMPTY,
                     r.T_ACTION.format(line='N', action='x'), r.T_TARGET.format(targets='P1–P8', **name),
                     r.T_RANGE.format(low='a', high='b', **name), r.T_STEP.format(step='s', **name),
                     r.T_VALUE_EMPTY.format(**name), r.T_UNIT.format(unit='u', **name)):
            with self.subTest(text=text):
                self.assertQuoted(text)
        # Written with “…” for the part that repeats.
        start = 'No. N column:{Name} '
        for text, tail in ((r.T_TARGET.format(targets='CH1–CH3', **name), 'to write CH1–CH3'),
                           (r.T_TARGET_EMPTY.format(**name), 'must be blank The working period of'),
                           (r.T_UNIT_EMPTY.format(unit='', **name), 'should be blank The readback of')):
            with self.subTest(text=text):
                self.assertTrue(text.startswith(start) and text.endswith(tail))
                self.assertQuoted('…' + tail)
        self.assertEqual(r.T_TARGET.format(targets='CH1–CH3', **name), start + 'The object to be written CH1–CH3')

    def test_order_messages_and_warnings(self):
        r = recipe
        motor = dict(line='N', motor='{motor}')
        for text in (r.T_GROUP.format(group='A Group', **motor), r.T_RUNNING.format(**motor), r.T_NOT_RUNNING.format(**motor),
                     r.T_PUMPS_FIRST.format(line='N', name='{Name}'), r.T_RUN_LONG.format(seconds='T', **motor),
                     r.T_RUN_ZERO.format(**motor), r.T_LEFT_ON.format(pumps=r.T_LEFT_ON_PUMP.format(**motor)),
                     r.T_SYRINGE_INIT.format(line='N'), r.T_SYRINGE_NOT_EMPTY.format(line='N', have='a'),
                     r.T_SYRINGE_OVER.format(line='N', have='a', size='c'),
                     r.T_SYRINGE_UNDER.format(line='N', have='a', want='b'), r.T_SYRINGE_LEFT.format(have='a'),
                     r.T_SYRINGE_SPEED.format(line='N', top='x'), r.T_WEIGHT_OFF.format(line='N'), r.T_DELAY_TOTAL,
                     r.W_PLASMA_COUNT.format(count='n'), r.W_REVERSE.format(**motor), r.W_NO_FLUID.format(**motor),
                     r.W_NO_PLASMA):
            with self.subTest(text=text):
                self.assertQuoted(text)

    def test_names_of_the_action_table(self):
        for action, facts in recipe.ACTIONS.items():
            with self.subTest(action=action):
                if action in ('nano_stop', 'power_off'):
                    self.assertIn('| `nano_stop`, `power_off` | %s |' % facts['name'], self.spec)
                else:
                    self.assertIn('| `%s` | %s |' % (action, facts['name']), self.spec)
        self.assertIn('"generally" | "pump" | "syringe pump" | "scales" | "plasma"', self.amendments)
        self.assertEqual({facts['group'] for facts in recipe.ACTIONS.values()}, {'generally', 'pump', 'syringe pump', 'scales', 'plasma'})

    def test_step_sentences_and_phases(self):
        result = check(*[row for row, _ in EVERY_ACTION])
        self.assertPasses(result)
        for item in result['steps']:
            with self.subTest(text=item['text']):
                if item['action'] == 'scale_read' and item['note']:
                    self.assertEqual(item['text'], 'Record weight' + ': {note}'.format(note=item['note']))
                    self.assertQuoted(': {note}')
                elif item['text'] != 'Waiting 60 Second':          # the specification's own example waits 30 s
                    self.assertQuoted(item['text'])
        for phase in ('Add liquid = ', 'Empty = ', 'Cleaning = ', 'Measure = ', 'Turn off high voltage = ', '(Waiting when it is the first)'):
            self.assertIn(phase, self.spec)
        self.assertEqual({recipe.PHASE_FEED, recipe.PHASE_DRAIN, recipe.PHASE_CLEAN, recipe.PHASE_MEASURE, recipe.PHASE_OFF,
                          recipe.PHASE_WAIT}, {'Add liquid', 'Empty', 'Cleaning', 'Measure', 'Turn off high voltage', 'Waiting'})

    def test_texts_of_the_step_editor(self):
        self.assertIn("ValueError('%s')" % recipe.T_ROWS_FORMAT, self.amendments)
        self.assertQuoted(recipe.S_REPEAT_START.format(count='n'), self.amendments)
        self.assertQuoted(recipe.S_REPEAT_END, self.amendments)
        self.assertQuoted(recipe.RAW_JOIN, self.amendments)
        self.assertQuoted(recipe.T_EMPTY, self.amendments)

    def test_the_sentences_of_m16(self):
        r = recipe
        name = dict(line='N', name='{Name}')
        for text in (r.T_TOGETHER_TARGET.format(**name), r.T_TOGETHER_VALUE.format(**name),
                     r.T_TOGETHER_DRAIN.format(line='N', label='{motor}'),
                     r.S_TOGETHER.format(parts='…'), r.T_FLOW_FORMAT, r.T_FLOW_EMPTY, r.T_FLOW_PUMP.format(pump='N'),
                     r.T_FLOW_PUMP_TWICE.format(pump='N'), r.T_FLOW_DRAIN_PUMP.format(pump='N'),
                     r.T_FLOW_AMOUNT.format(label='{motor}', low='a', high='b', unit='u', step='s'), r.T_FLOW_NO_DRAIN,
                     r.T_FLOW_NO_BOARD, r.N_FILL, r.N_FILL_WEIGHT, r.N_DRAIN, r.N_CLEAN):
            with self.subTest(text=text):
                self.assertQuoted(text, self.amendments)

    def test_examples(self):
        for item in recipe.EXAMPLES:
            with self.subTest(file=item['file']):
                self.assertIn('`%s` — “%s”' % (item['file'], item['name']), self.spec)
                text = (recipe.EXAMPLES_DIR / item['file']).read_text(encoding='utf-8')
                self.assertIn('```\n%s```' % text, self.spec)


if __name__ == '__main__':
    unittest.main(verbosity=2)
