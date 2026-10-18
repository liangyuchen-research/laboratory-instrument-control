"""2.0.0 Automatic test of data layer: Frompc/store.py, migrate.py, pinlock.py, alarms.py,and schedule.py New name.

    python tests/test_store.py

Only temporary folder is used.tests/fixtures/data-1.3.0 is simulated 1.3.0 folder (inside token is compiled,pin.json uses
is only for this folder PIN) ,Make a copy before using. Don’t read or write pc\\data,is not connected to the Internet and does not touch any devices.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'pc'), str(ROOT / 'tests')]

import alarms  # noqa: E402
import migrate  # noqa: E402
import pinlock  # noqa: E402
import schedule  # noqa: E402
import store  # noqa: E402

FIXTURE = ROOT / 'tests' / 'fixtures' / 'data-1.3.0'
FIXTURE_PIN = '135790'  # the throw-away PIN the fixture's pin.json was made with
VENDOR = ROOT / 'pc' / 'vendor'
CALIBRATION = VENDOR / 'v7' / 'config' / 'scale_calibration.json'
SEED_CSV = 'action,target,value,unit,note\nplasma_run,,,,discharge＋capture spectrum\n'
SEED_FACTS = {'step_count': 1, 'rounds': 1, 'uses_pump': False, 'uses_rpi': True, 'uses_plasma': True}
JOB_A = 'HMI-0a1b2c3d4e5f60718293a4b5c6d7e8f9'
JOB_B = 'HMI-1b2c3d4e5f60718293a4b5c6d7e8f90a'
JOB_C = 'CAP-2c3d4e5f60718293a4b5c6d7e8f90a1b'
PUMP_CSV = ('action,target,value,unit,note\npump_on,M8,40,mL/min,discharge\ndelay,,5,s,\npump_off,M8,,,\n')
PUMP_FACTS = {'step_count': 3, 'rounds': 1, 'uses_pump': True, 'uses_rpi': False, 'uses_plasma': False}
RUNLOG_KEYS = {'kind', 'time', 'operator', 'recipe_name', 'schedule_id', 'slot', 'slots_planned', 'status',
               'finished_at', 'round_id', 'spectra', 'result_text', 'note'}
_module = {}


def setUpModule():
    """The example Recipes belong to another package and may not be there yet: a folder of our own with
    the one file the migration publishes. No site.json either, so nothing depends on the installer's."""
    folder = tempfile.TemporaryDirectory()
    Path(folder.name, 'plasma-only.csv').write_bytes(SEED_CSV.encode('utf-8'))
    _module.update(folder=folder, recipes=store.RECIPES, site=store.SITE)
    store.RECIPES = Path(folder.name)
    store.SITE = Path(folder.name) / 'no-site.json'


def tearDownModule():
    store.RECIPES, store.SITE = _module['recipes'], _module['site']
    _module['folder'].cleanup()


def files(folder):
    """{relative path: SHA-256} of every file below ``folder``."""
    folder = Path(folder)
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob('*')) if path.is_file()}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_like_1_3_0(path):
    """Schedule._load of pc/app.py as it is in 1.3.0–1.4.1 (copied here: 2.0.0 replaces that file)."""
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get('slots'), list) or 'spec' not in record:
        return None
    if record.get('status') in ('waiting', 'running', 'stopping'):
        counts = record.setdefault('counts', {})
        for slot in record['slots']:
            if slot.get('status') == 'running':
                slot.update(status='failed', note='The program was closed or the computer was restarted during measurement; please go to the site to confirm the equipment status.')
                counts['failed'] = counts.get('failed', 0) + 1
        record.update(status='interrupted', next_slot_at=None,
                      error='The program is restarted, the schedule has been interrupted and will not automatically resume. Please restart the schedule after confirming the device.')
    return record


def old_program():
    """pc/app.py while it still is the 1.x program (it is not imported once it has become 2.0.0)."""
    source = (ROOT / 'pc' / 'app.py').read_text(encoding='utf-8')
    if not re.search(r"^VERSION = '1\.", source, re.M) or 'class Schedule' not in source:
        return None
    import app
    return app


def plasma_result(job, peak=337.1, flow='unified'):
    axis = [round(330.1 + index, 3) for index in range(12)]
    difference = [round(2500.0 if abs(x - peak) < .01 else 4.0, 2) for x in axis]
    background = [700.0] * len(axis)
    return {'ok': True, 'job': job, 'kind': 'plasma', 'started_at': store.now_iso(), 'finished_at': store.now_iso(),
            'flow': flow, 'voltage_v': 100, 'model': 'SIM-OES', 'serial': 'SIM0001', 'integration_ms': 1000,
            'pixels': len(axis), 'background_frames': 1, 'signal_frames': 2, 'steps': [], 'axis': axis,
            'background': background, 'signal': [b + d for b, d in zip(background, difference)],
            'difference': difference, 'frames': [difference, difference],
            'parameters': {'valid': True, 'voltage_v': 100}, 'timing': {'flow': flow, 'wait_s': 4.8, 'frames': []}}


class Case(unittest.TestCase):
    def folder(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def old_folder(self):
        """A copy of the synthetic 1.3.0 data folder."""
        data = self.folder() / 'data'
        shutil.copytree(FIXTURE, data)
        return data

    def store(self):
        return store.Store(self.folder() / 'data')

    def texts(self, kept):
        return [event['text'] for event in kept.events(limit=200)]


class WriteTests(Case):
    def test_a_file_that_is_briefly_held_by_another_program_is_written_after_all(self):
        path = self.folder() / 'value.json'
        store.write_json(path, {'n': 1})
        real, calls = Path.replace, []

        def held_twice(self, target):
            calls.append(target)
            if len(calls) <= 2:
                raise PermissionError(13, 'Access denied When')
            return real(self, target)
        with mock.patch.object(Path, 'replace', held_twice), mock.patch.object(store.time, 'sleep', lambda _s: None):
            store.write_json(path, {'n': 2, 'Character': 'Chinese'})
        self.assertEqual((len(calls), read(path)), (3, {'n': 2, 'Character': 'Chinese'}))
        self.assertEqual([item.name for item in path.parent.iterdir()], ['value.json'])  # no .tmp left behind

    def test_the_old_file_stays_whole_when_the_new_one_cannot_take_its_place(self):
        path = self.folder() / 'value.json'
        store.write_json(path, {'n': 1})
        tries = []

        def always_held(self, target):
            tries.append(1)
            raise PermissionError(13, 'Access denied When')
        with mock.patch.object(Path, 'replace', always_held), mock.patch.object(store.time, 'sleep', lambda _s: None):
            with self.assertRaises(PermissionError):
                store.write_json(path, {'n': 2})
        self.assertEqual((len(tries), read(path)), (10, {'n': 1}))

    def test_two_threads_never_write_one_file_at_the_same_moment(self):
        kept = self.store()
        path, errors = kept.dir / 'shared.json', []

        def writer(number):
            try:
                for turn in range(25):
                    kept.write_json(path, {'writer': number, 'turn': turn, 'pad': 'x' * 2000})
            except Exception as exc:  # the same .tmp used by two writers shows up as an error here
                errors.append(exc)
        threads = [threading.Thread(target=writer, args=(number,)) for number in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(read(path)['turn'], 24)

    def test_what_moved_out_of_the_main_program_is_all_here(self):
        for name in ('write_json', 'now_iso', 'validate_config', 'DEFAULT_CONFIG', 'site_config', 'RUN_ID', 'run_csv',
                     'Store'):
            self.assertTrue(hasattr(store, name), name)
        for raw in ({'rpi_host': ''}, {'rpi_host': 'a b'}, {'rpi_port': '8767'}, {'rpi_port': 0}, {'extra': 1},
                    {'token': 'a\nb'}):
            with self.assertRaises(ValueError):
                store.validate_config(raw)
        self.assertEqual(store.validate_config({}), {'rpi_host': '192.168.1.1', 'rpi_port': 8767, 'token': ''})
        self.assertTrue(store.RUN_ID.fullmatch(JOB_A) and not store.RUN_ID.fullmatch('HMI-../x'))
        self.assertEqual(store.site_config(), {})  # no site.json in this test's folder
        folder = self.folder()
        with mock.patch.object(store, 'SITE', folder / 'site.json'):
            (folder / 'site.json').write_text('{"rpi_host": "192.168.7.7", "rpi_user": "pi"}', encoding='utf-8')
            self.assertEqual(store.site_config(), {'rpi_host': '192.168.7.7'})
            (folder / 'site.json').write_text('{"rpi_host": "nonsense"}', encoding='utf-8')
            self.assertEqual(store.site_config(), {})

    def test_the_new_modules_load_without_the_main_program_and_without_the_recipe_language(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import store, migrate, pinlock, alarms, rpilink, schedule; '
                'print(sorted(set(sys.modules) & {"app", "app2", "application", "httpd", "recipe", "pumpboard", "serial"}))')
        done = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'pc')], capture_output=True, timeout=60,
                              env=dict(os.environ, PYTHONIOENCODING='utf-8', PLASMIC_NO_SERIAL='1'))
        self.assertEqual((done.returncode, done.stdout.decode('utf-8').strip(), done.stderr.decode('utf-8', 'replace')),
                         (0, '[]', ''))

    def test_settings_are_saved_and_a_first_start_takes_the_installers_address(self):
        kept = self.store()
        self.assertEqual(kept.config, store.DEFAULT_CONFIG)
        saved = kept.save_config({'rpi_host': ' 127.0.0.1 ', 'rpi_port': 9, 'token': 'abc'})
        self.assertEqual(saved, {'rpi_host': '127.0.0.1', 'rpi_port': 9, 'token': 'abc'})
        self.assertEqual(store.Store(kept.dir).config, saved)
        with self.assertRaises(ValueError):
            kept.save_config({'rpi_host': 'bad host', 'rpi_port': 1})
        self.assertEqual(read(kept.dir / 'config.json'), saved)


class MigrationTests(Case):
    def test_an_old_folder_keeps_everything_and_gains_what_2_0_0_needs(self):
        data = self.old_folder()
        before = files(data)
        original = {name: (data / name).read_bytes() for name in ('config.json', 'schedule.json', 'events.jsonl')}
        kept = store.Store(data)
        after = files(data)
        self.assertEqual(kept.migration, {'ran': True, 'done': True, 'migrated_from': '1.3.0', 'errors': []})
        self.assertLessEqual(set(before), set(after))                                  # nothing was deleted
        same = [name for name in before if name not in ('schedule.json', 'events.jsonl')]
        self.assertEqual({name: after[name] for name in same}, {name: before[name] for name in same})
        self.assertIn('config.json', same)
        self.assertIn('pin.json', same)
        self.assertEqual(len([name for name in same if name.startswith('runs/')]), 6)   # spectra: byte for byte
        self.assertTrue((data / 'events.jsonl').read_bytes().startswith(original['events.jsonl']))  # only added to
        # the copies made before anything was touched
        backups = list((data / 'backup').iterdir())
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name, r'^from-1\.3\.0-\d{8}-\d{6}$')
        self.assertEqual({path.name: path.read_bytes() for path in backups[0].iterdir()}, original)
        # what is new
        version = read(data / 'version.json')
        self.assertEqual((version['data_version'], version['program'], version['migrated_from']), (2, '2.0.0', '1.3.0'))
        self.assertIsNotNone(store.moment(version['migrated_at']))
        self.assertEqual(read(data / 'operators.json'), {'operators': ['operator']})
        self.assertEqual(kept.operators(), ['operator'])
        self.assertEqual(read(data / 'alarms.json'), {'next_id': 1, 'alarms': []})
        self.assertEqual(read(data / 'pumpboard.json'),
                         {'version': 1, 'enabled': None, 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4})
        self.assertEqual((data / 'pumpboard' / 'scale_calibration.json').read_bytes(), CALIBRATION.read_bytes())
        self.assertTrue((data / 'rounds').is_dir() and (data / 'logs').is_dir())
        recipes = kept.recipes()
        self.assertEqual(len(recipes), 1)
        seed = recipes[0]
        self.assertEqual((seed['name'], seed['source'], seed['csv'], seed['error']), ('Only discharge measurement', 'seed', SEED_CSV, ''))
        self.assertEqual({key: seed[key] for key in SEED_FACTS}, SEED_FACTS)
        self.assertEqual(seed['sha256'], hashlib.sha256(SEED_CSV.encode('utf-8')).hexdigest())
        self.assertEqual(kept.recipe(seed['id'])['csv'], SEED_CSV)
        # the old discharges show in execution record, newest first; the capture without discharge does not
        rows = kept.runlog()
        self.assertEqual([row['spectra'] for row in rows], [[JOB_B], [JOB_A]])
        self.assertEqual(set(rows[0]), RUNLOG_KEYS)
        self.assertEqual({key: rows[1][key] for key in ('kind', 'time', 'recipe_name', 'status', 'result_text', 'operator',
                                                        'round_id', 'schedule_id', 'finished_at', 'note')},
                         {'kind': 'legacy', 'time': '2026-10-05T12:00:01.050000+00:00', 'recipe_name': 'Discharge measurement (1.3.0) ',
                          'status': 'completed', 'result_text': 'highest peak 337.1 nm', 'operator': '', 'round_id': None,
                          'schedule_id': None, 'finished_at': None, 'note': ''})
        # the old settings, spectra and events are all still there to use
        self.assertEqual(kept.config, {'rpi_host': '127.0.0.1', 'rpi_port': 9, 'token': 'fixture-token-not-a-real-one'})
        self.assertEqual([row['job'] for row in kept.list_runs()], [JOB_B, JOB_A, JOB_C])
        self.assertEqual(kept.run_ids(), {JOB_A, JOB_B, JOB_C})
        run = kept.load_run(JOB_A)
        self.assertEqual((run['sequence'], run['background_frames'], len(run['axis'])), ('verified', 3, 16))
        self.assertTrue(store.run_csv(run).decode('utf-8-sig').startswith('wavelength_nm,background_mean,signal_mean,difference'))
        self.assertTrue(store.run_csv(kept.load_run(JOB_C)).decode('utf-8-sig').startswith('wavelength_nm,mean,frame_1'))
        events = kept.events(limit=200)
        self.assertEqual((events[0]['text'], events[0]['actor'], events[0]['level']),
                         ('Data upgraded to 2.0.0. Original connection settings, maintenance PIN, and spectral records were retained.', 'system', 'info'))
        self.assertEqual(len(events), 7)
        self.assertEqual({event['actor'] for event in events}, {'system'})   # 1.3.0 wrote no actor
        self.assertIn('Scheduled measurement start: every 10 minutes', ' '.join(event['text'] for event in events))
        self.assertTrue(pinlock.PinLock(data / 'pin.json').unlock(FIXTURE_PIN))        # the PIN still opens Maintenance

    def test_the_old_schedule_only_gained_keys_and_both_generations_can_read_it(self):
        data = self.old_folder()
        old = read(data / 'schedule.json')
        kept = store.Store(data)
        new = read(data / 'schedule.json')
        self.assertEqual({key: new[key] for key in old}, old)                # every 1.3.0 key as it was ("sequence" too)
        self.assertEqual({key: new[key] for key in set(new) - set(old)},
                         {'version': 2, 'operator': '', 'recipe': {'id': None, 'name': 'Discharge measurement (1.3.0) ', 'sha256': None},
                          'uses_plasma': True, 'uses_rpi': True, 'uses_pump': False,
                          'confirmed': {'high_voltage': True, 'liquid': False, 'hv_off_checked': False}})
        # 2.0.0 reads it as it is; ending it ("interrupted") is the runner's job at start
        self.assertEqual((kept.load_schedule()['status'], kept.load_schedule()['version']), ('running', 2))
        # 1.3.0's own reading still works: it is taken for the schedule it is, and ended
        again = load_like_1_3_0(data / 'schedule.json')
        self.assertEqual((again['status'], again['counts'], again['slots'][1]['status'], again['sequence']),
                         ('interrupted', {'done': 1, 'failed': 1}, 'failed', 'verified'))
        program = old_program()
        if program is not None:  # and the real thing, as long as pc/app.py is the 1.x program
            shown = program.Schedule(program.Store(data), None).view()
            self.assertEqual((shown['status'], shown['counts'], shown['slots'][1]['status']),
                             ('interrupted', {'done': 1, 'failed': 1}, 'failed'))
            self.assertIn('will not automatically recover', shown['error'])
            older = program.Store(data)
            self.assertEqual((older.config['token'], len(older.list_runs())), ('fixture-token-not-a-real-one', 3))
            self.assertIn('has been upgraded to 2.0.0', ' '.join(event['text'] for event in older.recent_events()))

    def test_a_second_run_changes_nothing(self):
        data = self.old_folder()
        store.Store(data)
        first = files(data)
        report = migrate.run(data, VENDOR, store.RECIPES)
        self.assertEqual(report, {'ran': False, 'done': True, 'migrated_from': '1.3.0', 'errors': []})
        again = store.Store(data)
        self.assertEqual(files(data), first)
        self.assertEqual((len(again.recipes()), len(again.runlog()), len(list((data / 'backup').iterdir()))), (1, 2, 1))

    def test_a_fresh_folder_takes_the_same_path(self):
        data = self.folder() / 'data'
        kept = store.Store(data)
        self.assertEqual(kept.migration, {'ran': True, 'done': True, 'migrated_from': None, 'errors': []})
        self.assertEqual(sorted(path.relative_to(data).as_posix() for path in data.rglob('*') if path.is_file()
                                and path.parent.name != 'recipes'),
                         ['alarms.json', 'operators.json', 'pumpboard.json', 'pumpboard/scale_calibration.json',
                          'runlog.jsonl', 'version.json'])
        self.assertEqual(read(data / 'version.json')['migrated_from'], None)
        self.assertFalse((data / 'backup').exists())
        self.assertEqual((kept.events(), kept.runlog(), kept.operators()), ([], [], ['operator']))
        self.assertEqual([row['name'] for row in kept.recipes()], ['Only discharge measurement'])
        self.assertTrue((data / 'rounds').is_dir() and (data / 'logs').is_dir() and (data / 'runs').is_dir())
        self.assertEqual((data / 'pumpboard' / 'scale_calibration.json').read_bytes(), CALIBRATION.read_bytes())

    def test_a_folder_with_only_the_installers_settings_counts_as_an_old_one(self):
        data = self.folder() / 'data'
        data.mkdir()
        (data / 'config.json').write_text('{"rpi_host": "127.0.0.1", "rpi_port": 9, "token": "t"}', encoding='utf-8')
        kept = store.Store(data)
        self.assertEqual(kept.migration['migrated_from'], '1.3.0')
        backup = next((data / 'backup').iterdir())
        self.assertEqual([path.name for path in backup.iterdir()], ['config.json'])
        self.assertEqual(kept.runlog(), [])

    def test_the_backup_takes_the_end_of_a_long_event_log(self):
        data = self.old_folder()
        line = json.dumps({'time': '2026-10-01T00:00:00+00:00', 'level': 'info', 'text': 'very long record' * 30}, ensure_ascii=False)
        last = json.dumps({'time': '2026-10-05T23:59:59+00:00', 'level': 'warn', 'text': 'last line'}, ensure_ascii=False)
        with (data / 'events.jsonl').open('w', encoding='utf-8', newline='\n') as out:
            for _ in range(3_000_000 // len(line.encode('utf-8')) + 1):
                out.write(line + '\n')
            out.write(last + '\n')
        whole = (data / 'events.jsonl').read_bytes()
        self.assertGreater(len(whole), 3_000_000)
        store.Store(data)
        copy = (next((data / 'backup').iterdir()) / 'events.jsonl').read_bytes()
        self.assertTrue(1_900_000 < len(copy) <= 2_000_000)
        self.assertTrue(whole.endswith(copy) and copy.startswith(b'{"time"'))     # whole lines, the newest ones
        self.assertTrue((data / 'events.jsonl').read_bytes().startswith(whole))    # the log itself was only added to

    def test_a_step_that_fails_is_written_down_and_done_at_the_next_start(self):
        for make, origin, backups in ((self.old_folder, '1.3.0', 1), (lambda: self.folder() / 'data', None, 0)):
            data = make()
            incomplete = self.folder()  # a program folder without the vendored calibration file
            with mock.patch.object(store, 'VENDOR', incomplete):
                kept = store.Store(data)  # the program still starts
            report = kept.migration
            self.assertEqual((report['ran'], report['done'], report['migrated_from'], len(report['errors'])),
                             (True, False, origin, 1))
            self.assertIn('Pump control panel settings', report['errors'][0])
            newest = kept.events(limit=1)[0]
            self.assertEqual((newest['level'], newest['actor']), ('error', 'system'))
            self.assertIn('Data upgrade to 2.0.0 did not finish (step 3: Pump control panel settings)', newest['text'])
            self.assertIn('retry at the next start', newest['text'])
            self.assertEqual(read(data / 'version.json')['data_version'], 1)      # not finished
            self.assertFalse((data / 'pumpboard' / 'scale_calibration.json').exists())
            self.assertEqual((kept.operators(), len(kept.recipes())), (['operator'], 1))  # the other steps were done
            seed = kept.recipes()[0]['id']
            kept = store.Store(data)  # next start, with the folder complete again
            self.assertEqual(kept.migration, {'ran': True, 'done': True, 'migrated_from': origin, 'errors': []})
            self.assertEqual(read(data / 'version.json')['data_version'], 2)
            self.assertEqual((data / 'pumpboard' / 'scale_calibration.json').read_bytes(), CALIBRATION.read_bytes())
            self.assertEqual([row['id'] for row in kept.recipes()], [seed])        # nothing was done twice
            self.assertEqual(len(list((data / 'backup').iterdir())) if (data / 'backup').is_dir() else 0, backups)
            self.assertEqual(sum('Data upgraded to 2.0.0' in text for text in self.texts(kept)), 1 if origin else 0)

    def test_an_example_file_that_is_not_the_one_line_recipe_is_not_published_blindly(self):
        other = self.folder()
        (other / 'plasma-only.csv').write_text(PUMP_CSV, encoding='utf-8')
        data = self.folder() / 'data'
        with mock.patch.object(store, 'RECIPES', other):
            kept = store.Store(data)
        self.assertEqual((kept.migration['done'], kept.recipes()), (False, []))
        self.assertIn('plasma-only.csv does not contain the expected single plasma_run row', kept.events(limit=1)[0]['text'])
        missing = self.folder()
        with mock.patch.object(store, 'RECIPES', missing):
            kept = store.Store(self.folder() / 'data')
        self.assertEqual((kept.migration['done'], kept.recipes()), (False, []))

    def test_a_recipe_the_owner_deleted_does_not_come_back(self):
        data = self.folder() / 'data'
        kept = store.Store(data)
        kept.delete_recipe(kept.recipes()[0]['id'])
        started = dict(read(data / 'version.json'), data_version=1, migrated_at=None)
        store.write_json(data / 'version.json', started)  # as if the first run had not finished
        self.assertEqual(migrate.run(data, VENDOR, store.RECIPES)['done'], True)
        self.assertEqual(store.Store(data).recipes(), [])

    def test_the_example_file_of_the_program_is_the_recipe_that_gets_published(self):
        path = ROOT / 'pc' / 'recipes' / 'plasma-only.csv'
        if not path.is_file():
            self.skipTest('pc/recipes/plasma-only.csv is not there yet (it belongs to the Recipe package)')
        data = self.folder() / 'data'
        with mock.patch.object(store, 'RECIPES', path.parent):
            kept = store.Store(data)
        self.assertEqual(kept.migration['errors'], [])
        self.assertEqual(kept.recipes()[0]['csv'].splitlines(), SEED_CSV.splitlines())
        # The facts stored with it are written down in migrate.py (the store must not need the Recipe
        # language to start). When that language is there, it must say the same about this file.
        try:
            import recipe
            checked = recipe.validate(path.read_text(encoding='utf-8-sig'), None)
        except Exception as exc:
            self.skipTest(f'pc/recipe.py cannot be asked here: {exc!r}')
        self.assertEqual({key: checked.get(key) for key in migrate.SEED_FACTS}, migrate.SEED_FACTS)
        self.assertEqual(checked.get('valid'), True)


class EventTests(Case):
    def test_events_say_who_did_it_and_old_lines_read_as_the_system(self):
        kept = self.store()
        start = kept.events_rev
        kept.event('Connection interrupted', 'warn')
        kept.event('Start pump', 'info', 'Wang Xiaoming')
        kept.event('storage parameters', 'info', 'Maintenance')
        kept.event('Manual operation without operator selection', 'info', '')
        kept.event('x' * 500)
        rows = kept.events()
        self.assertEqual([(row['level'], row['actor']) for row in rows[::-1]],
                         [('warn', 'system'), ('info', 'Wang Xiaoming'), ('info', 'Maintenance'), ('info', ''), ('info', 'system')])
        self.assertEqual((len(rows[0]['text']), set(rows[0]), kept.events_rev - start), (400, {'time', 'level', 'text', 'actor'}, 5))
        lines = (kept.dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(json.loads(lines[1]), {'time': rows[3]['time'], 'level': 'info', 'text': 'Start pump', 'actor': 'Wang Xiaoming'})
        self.assertEqual([row['text'] for row in kept.recent_events(2)], ['x' * 400, 'Manual operation without operator selection'])
        again = store.Store(kept.dir)
        self.assertEqual(again.events(), rows)

    def test_the_list_pages_backwards_and_reaches_into_the_file_when_memory_ends(self):
        data = self.folder() / 'data'
        data.mkdir()
        with (data / 'events.jsonl').open('w', encoding='utf-8', newline='\r\n') as out:   # as 1.3.0 on Windows wrote it
            for number in range(40):
                out.write(json.dumps({'time': f'2026-10-05T10:{number:02d}:00+00:00', 'level': 'info',
                                      'text': f'No. {number} Pen'}, ensure_ascii=False) + '\n')
            out.write('not json at all\n')
        with mock.patch.object(store, 'KEEP_EVENTS', 8):
            kept = store.Store(data)
        texts = lambda rows: [row['text'] for row in rows]  # noqa: E731
        first = kept.events(limit=5)
        self.assertEqual(texts(first)[1:], ['No. 39 Pen', 'No. 38 Pen', 'No. 37 Pen', 'No. 36 Pen'])   # [0] is the upgrade note
        page = kept.events(before=first[-1]['time'], limit=5)                              # still in memory
        self.assertEqual(texts(page), ['No. 35 Pen', 'No. 34 Pen', 'No. 33 Pen', 'No. 32 Pen', 'No. 31 Pen'])
        page = kept.events(before=page[-1]['time'], limit=5)                               # older than the memory
        self.assertEqual(texts(page), ['No. 30 Pen', 'No. 29 Pen', 'No. 28 Pen', 'No. 27 Pen', 'No. 26 Pen'])
        self.assertEqual(texts(kept.events(before='2026-10-05T18:02:30+08:00', limit=6)), ['No. 2 Pen', 'No. 1 Pen', 'No. 0 Pen'])
        self.assertEqual(kept.events(before='2026-10-05T10:00:00+00:00'), [])
        # "is there more": ask for one more than is shown
        self.assertEqual((len(kept.events(limit=41 + 1)), len(kept.events(before='2026-10-05T10:03:00+00:00', limit=3 + 1))),
                         (8, 3))
        for bad in ({'before': 'yesterday'}, {'limit': 0}, {'limit': '5'}):
            with self.assertRaises(ValueError):
                kept.events(**bad)
        # A very long log: only its end is read at start, the rest when somebody asks for it.
        with mock.patch.object(store, 'TAIL_BYTES', 400):
            kept = store.Store(data)
        newest = kept.events()
        self.assertTrue(2 <= len(newest) < 8 and kept.events_rev == len(newest))
        self.assertEqual(texts(newest)[:2], ['Data upgraded to 2.0.0. Original connection settings, maintenance PIN, and spectral records were retained.', 'No. 39 Pen'])
        self.assertEqual(texts(kept.events(before='2026-10-05T10:20:30+00:00', limit=3)), ['No. 20 Pen', 'No. 19 Pen', 'No. 18 Pen'])
        kept.event('One more stroke')
        self.assertEqual((texts(kept.events(limit=2)), kept.events_rev), (['One more stroke', 'Data upgraded to 2.0.0. Original connection settings, maintenance PIN, and spectral records were retained.'],
                                                                           len(newest) + 1))

    def test_a_line_that_was_cut_off_is_skipped_and_the_next_one_is_not_glued_to_it(self):
        data = self.folder() / 'data'
        data.mkdir()
        whole = json.dumps({'time': '2026-10-05T10:00:00+00:00', 'level': 'info', 'text': 'complete line'}, ensure_ascii=False)
        (data / 'events.jsonl').write_bytes((whole + '\n' + '{"time": "2026-10-05T10:00:01+00:00", "le').encode('utf-8'))
        kept = store.Store(data)   # the migration adds its note, then the store reads
        kept.event('a later transaction')
        self.assertEqual(self.texts(kept), ['a later transaction', 'Data upgraded to 2.0.0. Original connection settings, maintenance PIN, and spectral records were retained.', 'complete line'])
        self.assertEqual(self.texts(store.Store(data)), self.texts(kept))   # the file says the same
        runlog = data / 'runlog.jsonl'
        runlog.write_bytes(b'{"kind": "slot", "time": "2026-10-05T10:00:00+00:00"}\n{"kind": "rou')
        kept = store.Store(data)
        kept.append_runlog({'kind': 'round', 'time': '2026-10-05T10:10:00+00:00'})
        self.assertEqual([row['kind'] for row in store.Store(data).runlog()], ['round', 'slot'])

    def test_a_log_that_cannot_be_written_does_not_stop_the_program(self):
        kept = self.store()
        with mock.patch.object(store, 'append_jsonl', side_effect=PermissionError(13, 'Access denied When')):
            kept.event('When the hard disk cannot be written to')
            self.assertFalse(kept.append_runlog({'kind': 'slot', 'time': store.now_iso()}))
        self.assertEqual((self.texts(kept), len(kept.runlog())), (['When the hard disk cannot be written to'], 1))
        self.assertFalse((kept.dir / 'events.jsonl').exists())
        self.assertTrue(kept.append_runlog({'kind': 'slot', 'time': store.now_iso()}))


class OperatorTests(Case):
    def test_the_list_is_checked_as_a_whole(self):
        kept = self.store()
        self.assertEqual(kept.set_operators([' Wang Xiaoming ', 'Li Dahua', 'Wang Xiaoming', 'Dr. O\'Neil']), ['Wang Xiaoming', 'Li Dahua', "Dr. O'Neil"])
        self.assertEqual(read(kept.dir / 'operators.json'), {'operators': ['Wang Xiaoming', 'Li Dahua', "Dr. O'Neil"]})
        self.assertEqual(store.Store(kept.dir).operators(), ['Wang Xiaoming', 'Li Dahua', "Dr. O'Neil"])
        rule = 'Operator names must be 1–20 characters and cannot contain < > " \\'
        for bad in ('', '   ', 'x' * 21, 'a<b', 'a>b', 'a"b', 'a\\b', 'a\nb', 'a\tb', 'a\x00b', 5, None):
            with self.assertRaises(ValueError) as caught:
                kept.set_operators(['Wang Xiaoming', bad])
            self.assertEqual(str(caught.exception), rule, repr(bad))
        with self.assertRaises(ValueError) as caught:
            kept.set_operators([f'operator{number}' for number in range(51)])
        self.assertEqual(str(caught.exception), 'At most 50 operators are allowed.')
        with self.assertRaises(ValueError):
            kept.set_operators('Wang Xiaoming')
        self.assertEqual(kept.operators(), ['Wang Xiaoming', 'Li Dahua', "Dr. O'Neil"])          # a refused list changes nothing
        self.assertEqual(len(kept.set_operators([f'operator{number}' for number in range(50)] + ['operator0'])), 50)
        self.assertEqual(kept.set_operators(['x' * 20]), ['x' * 20])
        self.assertEqual((kept.set_operators([]), read(kept.dir / 'operators.json')), ([], {'operators': []}))
        names = kept.operators()
        names.append('Modified externally')
        self.assertEqual(kept.operators(), [])                                          # callers get a copy

    def test_a_list_edited_by_hand_loses_only_the_names_that_break_the_rules(self):
        kept = self.store()
        store.write_json(kept.dir / 'operators.json', {'operators': ['Wang Xiaoming', 'a<b', 7, 'Wang Xiaoming', ' Li Dahua ']})
        self.assertEqual(store.Store(kept.dir).operators(), ['Wang Xiaoming', 'Li Dahua'])
        (kept.dir / 'operators.json').write_text('[broken', encoding='utf-8')
        self.assertEqual(store.Store(kept.dir).operators(), [])


class PumpSettingsTests(Case):
    def test_pump_board_settings_are_kept_as_given(self):
        kept = self.store()
        default = {'version': 1, 'enabled': None, 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4}
        self.assertEqual(kept.pump_settings(), default)
        chosen = dict(default, enabled=True, device='COM91', vid='2341', pid='0042', serial_number='TEST0091',
                      description='Arduino Mega 2560 (COM91)', chosen_at='2026-10-06T10:00:00+08:00', syringe_ul=1250)
        self.assertEqual(kept.save_pump_settings(chosen), chosen)
        self.assertEqual((read(kept.dir / 'pumpboard.json'), store.Store(kept.dir).pump_settings()), (chosen, chosen))
        with self.assertRaises(ValueError):
            kept.save_pump_settings(['COM91'])
        (kept.dir / 'pumpboard.json').write_text('{not json', encoding='utf-8')
        self.assertEqual(kept.pump_settings(), default)
        kept.pump_settings()['enabled'] = True
        self.assertEqual(kept.pump_settings(), default)  # the defaults themselves cannot be changed from outside


class RecipeTests(Case):
    def test_publishing_stores_the_text_its_hash_and_the_facts(self):
        kept = self.store()
        revision = kept.recipes_rev
        record = kept.publish_recipe('  discharge  ', PUMP_CSV.replace('\n', '\r\n'), dict(PUMP_FACTS, valid=True, steps=[]), 'import')
        self.assertEqual(set(record), {'id', 'name', 'created_at', 'source', 'csv', 'sha256', 'step_count', 'rounds',
                                       'uses_pump', 'uses_rpi', 'uses_plasma'})
        self.assertTrue(re.fullmatch(r'[0-9a-f]{32}', record['id']))
        self.assertEqual((record['name'], record['source'], record['csv']), ('discharge', 'import', PUMP_CSV))   # LF, whatever came in
        self.assertEqual(record['sha256'], hashlib.sha256(PUMP_CSV.encode('utf-8')).hexdigest())
        self.assertEqual({key: record[key] for key in PUMP_FACTS}, PUMP_FACTS)
        self.assertEqual(read(kept.dir / 'recipes' / (record['id'] + '.json')), record)
        self.assertEqual(kept.recipe(record['id']), record)
        self.assertEqual([(row['name'], row['error']) for row in kept.recipes()], [('Only discharge measurement', ''), ('discharge', '')])   # oldest first
        self.assertEqual(kept.recipes_rev, revision + 1)
        self.assertEqual([row['name'] for row in store.Store(kept.dir).recipes()], ['Only discharge measurement', 'discharge'])

    def test_names_are_unique_and_one_to_sixty_characters(self):
        kept = self.store()
        for name in ('Only discharge measurement', ' Only discharge measurement '):
            with self.assertRaises(ValueError) as caught:
                kept.publish_recipe(name, SEED_CSV, SEED_FACTS, 'import')
            self.assertEqual(str(caught.exception), 'A recipe with this name already exists.')
        for name in ('', '   ', 'x' * 61, 'two lines\nname', None, 12):
            with self.assertRaises(ValueError) as caught:
                kept.publish_recipe(name, SEED_CSV, SEED_FACTS, 'import')
            self.assertEqual(str(caught.exception), 'Recipe names must be 1–60 characters.')
        self.assertEqual(kept.publish_recipe('x' * 60, SEED_CSV, SEED_FACTS, 'import')['name'], 'x' * 60)
        self.assertEqual(kept.publish_recipe('Only discharge measurement 2', SEED_CSV, SEED_FACTS, 'import')['name'], 'Only discharge measurement 2')
        for wrong in ((b'bytes', SEED_FACTS, 'import'), (SEED_CSV, None, 'import'), (SEED_CSV, {'step_count': 1}, 'import'),
                      (SEED_CSV, SEED_FACTS, 'upload')):
            with self.assertRaises(TypeError):
                kept.publish_recipe('Wrong call', *wrong)
        self.assertEqual(len(kept.recipes()), 3)

    def test_a_recipe_file_that_was_changed_is_refused_at_every_load(self):
        kept = self.store()
        record = kept.publish_recipe('discharge', PUMP_CSV, PUMP_FACTS, 'import')
        path = kept.dir / 'recipes' / (record['id'] + '.json')
        self.assertEqual(kept.recipe(record['id'])['csv'], PUMP_CSV)
        store.write_json(path, dict(record, csv=PUMP_CSV.replace('40', '80')))   # somebody edits the stored file
        with self.assertRaises(ValueError) as caught:
            kept.recipe(record['id'])
        self.assertEqual(str(caught.exception), 'Recipe file is different from when published, please re-publish.')
        self.assertEqual({row['name']: row['error'] for row in kept.recipes()},
                         {'Only discharge measurement': '', 'discharge': 'Recipe file is different from when published, please re-publish.'})
        path.write_text('{cut off', encoding='utf-8')
        with self.assertRaises(ValueError):
            kept.recipe(record['id'])
        self.assertEqual([row['name'] for row in kept.recipes()], ['Only discharge measurement'])   # nothing readable to list
        store.write_json(path, record)
        self.assertEqual(kept.recipe(record['id']), record)                        # put back: good again
        for unknown in ('0' * 32, 'ABCDEF' + '0' * 26, '../config', record['id'] + '.json', '', None, 7):
            with self.assertRaises(KeyError):
                kept.recipe(unknown)

    def test_deleting_moves_the_file_and_erases_nothing(self):
        kept = self.store()
        record = kept.publish_recipe('discharge', PUMP_CSV, PUMP_FACTS, 'import')
        path = kept.dir / 'recipes' / (record['id'] + '.json')
        stored, revision = path.read_bytes(), kept.recipes_rev
        target = kept.delete_recipe(record['id'])
        self.assertFalse(path.exists())
        self.assertEqual(target.parent, kept.dir / 'recipes' / 'deleted')
        self.assertRegex(target.name, r'^%s\.deleted-\d{8}-\d{6}\.json$' % record['id'])
        self.assertEqual((target.read_bytes(), kept.recipes_rev), (stored, revision + 1))
        self.assertEqual([row['name'] for row in kept.recipes()], ['Only discharge measurement'])      # the deleted folder is not listed
        for call in (kept.recipe, kept.delete_recipe):
            with self.assertRaises(KeyError):
                call(record['id'])
        with self.assertRaises(KeyError):
            kept.delete_recipe('../../config')
        again = kept.publish_recipe('discharge', PUMP_CSV, PUMP_FACTS, 'import')              # the name is free again
        store.write_json(kept.dir / 'recipes' / (again['id'] + '.json'), dict(again, csv='changed'))
        kept.delete_recipe(again['id'])                                               # a damaged one can be removed too
        self.assertEqual(len(list((kept.dir / 'recipes' / 'deleted').iterdir())), 2)
        self.assertEqual([row['name'] for row in kept.recipes()], ['Only discharge measurement'])


class RoundTests(Case):
    def test_round_records_are_kept_one_file_each_and_the_last_written_is_the_newest(self):
        kept = self.store()
        self.assertIsNone(kept.newest_round())
        first = {'id': 'a' * 32, 'version': 1, 'status': 'completed', 'recipe': {'name': 'Only discharge measurement', 'csv': SEED_CSV},
                 'steps': [{'n': 1, 'status': 'done'}]}
        second = {'id': 'b' * 32, 'version': 1, 'status': 'running', 'steps': []}
        kept.save_round(first)
        kept.save_round(second)
        folder = kept.dir / 'rounds'
        self.assertEqual(sorted(path.name for path in folder.iterdir()), [f'ROUND-{"a" * 32}.json', f'ROUND-{"b" * 32}.json'])
        os.utime(folder / f'ROUND-{"a" * 32}.json', (1_700_000_000, 1_700_000_000))
        os.utime(folder / f'ROUND-{"b" * 32}.json', (1_700_000_100, 1_700_000_100))
        self.assertEqual((kept.round('a' * 32), kept.newest_round()), (first, second))
        kept.save_round(dict(second, status='completed'))
        self.assertEqual(kept.round('b' * 32)['status'], 'completed')
        (folder / f'ROUND-{"c" * 32}.json').write_text('{cut off', encoding='utf-8')     # written last, but not readable
        (folder / 'notes.txt').write_text('not a record', encoding='utf-8')
        self.assertEqual(kept.newest_round()['id'], 'b' * 32)
        with self.assertRaises(ValueError):
            kept.round('c' * 32)
        for unknown in ('d' * 32, 'ROUND-' + 'a' * 32, '../schedule', '', None):
            with self.assertRaises(KeyError):
                kept.round(unknown)
        for bad in ({'id': 'A' * 32}, {'id': '../x'}, {}, 'text'):
            with self.assertRaises(ValueError):
                kept.save_round(bad)

    def test_the_run_log_is_appended_and_read_newest_first_whatever_the_time_zone(self):
        kept = self.store()
        revision = kept.runlog_rev
        legacy = {'kind': 'legacy', 'time': '2026-10-05T16:02:11+00:00', 'status': 'completed', 'spectra': [JOB_A]}
        slot = {'kind': 'slot', 'time': '2026-10-06T14:10:00+08:00', 'status': 'not_started', 'spectra': []}
        done = {'kind': 'round', 'time': '2026-10-06T14:20:00+08:00', 'status': 'completed', 'spectra': [JOB_B]}
        for row in (legacy, slot, done):
            self.assertTrue(kept.append_runlog(row))
        self.assertEqual((kept.runlog(), kept.runlog_rev - revision), ([done, slot, legacy], 3))
        self.assertEqual(kept.runlog(limit=2), [done, slot])
        self.assertEqual(kept.runlog(before='2026-10-06T14:20:00+08:00'), [slot, legacy])
        self.assertEqual(kept.runlog(before='2026-10-06T06:15:00+00:00'), [slot, legacy])   # 14:15 local, said in UTC
        self.assertEqual(kept.runlog(before='2026-10-06T00:02:11+08:00'), [])               # the legacy row's own moment
        kept.runlog()[0]['status'] = 'changed outside'
        self.assertEqual(store.Store(kept.dir).runlog(), [done, slot, legacy])
        with self.assertRaises(ValueError):
            kept.append_runlog('row')

    def test_the_schedule_record_is_one_file_that_is_replaced(self):
        kept = self.store()
        self.assertIsNone(kept.load_schedule())
        record = {'version': 2, 'id': 'c' * 32, 'status': 'waiting', 'spec': {'interval_seconds': 600}, 'slots': [],
                  'operator': 'Wang Xiaoming', 'counts': {}}
        kept.save_schedule(record)
        self.assertEqual((kept.load_schedule(), read(kept.dir / 'schedule.json')), (record, record))
        kept.save_schedule(dict(record, status='completed'))
        self.assertEqual(store.Store(kept.dir).load_schedule()['status'], 'completed')
        for nonsense in ('{cut off', '[]', '{"status": "waiting"}', '{"spec": {}, "slots": 3}'):
            (kept.dir / 'schedule.json').write_text(nonsense, encoding='utf-8')
            self.assertIsNone(kept.load_schedule(), nonsense)


class RunTests(Case):
    def test_spectra_are_stored_with_where_they_came_from(self):
        kept = self.store()
        revision = kept.runs_rev
        job = 'HMI-' + '5' * 32
        meta = kept.save_run(plasma_result(job), round_id='7' * 32, recipe_name='Only discharge measurement', operator='Wang Xiaoming', slot=2,
                             source='round')
        self.assertEqual({key: meta[key] for key in ('job', 'kind', 'flow', 'background_frames', 'signal_frames', 'voltage_v',
                                                     'peak_nm', 'peak_value', 'wait_s', 'round_id', 'recipe_name', 'operator',
                                                     'slot', 'source')},
                         {'job': job, 'kind': 'plasma', 'flow': 'unified', 'background_frames': 1, 'signal_frames': 2,
                          'voltage_v': 100, 'peak_nm': 337.1, 'peak_value': 2500.0, 'wait_s': 4.8, 'round_id': '7' * 32,
                          'recipe_name': 'Only discharge measurement', 'operator': 'Wang Xiaoming', 'slot': 2, 'source': 'round'})
        self.assertEqual(read(kept.runs / (job + '.meta.json')), meta)
        run = kept.load_run(job)
        self.assertEqual((run['saved_at'], 'ok' in run, len(run['frames']), run['timing']['wait_s']), (meta['saved_at'], False, 2, 4.8))
        self.assertEqual((kept.list_runs(1), kept.run_ids(), kept.runs_rev), ([meta], {job}, revision + 1))
        capture = {'ok': True, 'job': 'CAP-' + '6' * 32, 'kind': 'capture', 'axis': [330.1, 331.1], 'mean': [700.0, 712.5],
                   'frames': [[700.0, 712.0], [700.0, 713.0]], 'integration_ms': 100, 'pixels': 2, 'signal_frames': 2}
        check = kept.save_run(capture, source='check')
        self.assertEqual((check['kind'], check['peak_nm'], check['source'], check['round_id'], check['flow']),
                         ('capture', 331.1, 'check', None, None))
        self.assertEqual([row['job'] for row in kept.list_runs()], [capture['job'], job])
        self.assertEqual([row['job'] for row in store.Store(kept.dir).list_runs()], [capture['job'], job])
        again = kept.save_run(plasma_result(job, peak=335.1))                # read once more: the row is replaced
        self.assertEqual(([row['job'] for row in kept.list_runs()], again['peak_nm'], again['source']),
                         ([job, capture['job']], 335.1, None))

    def test_what_cannot_be_a_file_name_or_is_not_known_is_refused(self):
        kept = self.store()
        with self.assertRaises(TypeError):
            kept.save_run(plasma_result('HMI-' + '5' * 32), sequence='quick')
        for bad in ('../../config', 'HMI-123', 'XYZ-' + '5' * 32, 5):
            with self.assertRaises(ValueError):
                kept.save_run(plasma_result(bad))
        self.assertEqual((kept.list_runs(), list(kept.runs.iterdir())), ([], []))
        for bad in ('../config', 'HMI-..', 'config'):
            with self.assertRaises(ValueError):
                kept.load_run(bad)
        with self.assertRaises(OSError):
            kept.load_run('HMI-' + '0' * 32)
        (kept.runs / 'HMI-broken.meta.json').write_text('[1, 2]', encoding='utf-8')
        (kept.runs / ('HMI-' + '8' * 32 + '.meta.json')).write_text('{"kind": "plasma"}', encoding='utf-8')
        self.assertEqual(store.Store(kept.dir).run_ids(), set())   # a meta file without its job is not a row


class AlarmTests(Case):
    def setUp(self):
        self.kept = self.store()
        self.alarms = alarms.Alarms(self.kept)

    def test_an_alarm_is_listed_stored_and_written_to_the_event_log(self):
        number = self.alarms.raise_('pump_stop_unconfirmed', 1, 'pump', 'The pump stop command was not confirmed.', 'latched')
        view = self.alarms.view()
        self.assertEqual((view['worst'], view['unacked'], view['counts']), (1, 1, {'p1': 1, 'p2': 0, 'p3': 0}))
        self.assertEqual(len(view['active']), 1)
        row = view['active'][0]
        self.assertEqual(set(row), {'id', 'key', 'priority', 'area', 'text', 'since', 'acked', 'cleared'})
        self.assertEqual((row['id'], row['key'], row['priority'], row['area'], row['text'], row['acked'], row['cleared']),
                         (number, 'pump_stop_unconfirmed', 1, 'pump', 'The pump stop command was not confirmed.', False, False))
        self.assertIsNotNone(store.moment(row['since']))
        saved = read(self.kept.dir / 'alarms.json')
        self.assertEqual((saved['next_id'], len(saved['alarms'])), (number + 1, 1))
        self.assertEqual(set(saved['alarms'][0]), {'id', 'key', 'priority', 'area', 'text', 'kind', 'since', 'acked',
                                                   'acked_at', 'cleared', 'cleared_at'})
        event = self.kept.events(limit=1)[0]
        self.assertEqual((event['text'], event['level'], event['actor']),
                         ('Alarm (Emergency · Pump): The pump stop command was not confirmed.', 'error', 'system'))
        self.alarms.raise_('scale_offline', 3, 'scales', 'Scale does not respond', 'condition')
        self.assertEqual((self.kept.events(limit=1)[0]['text'], self.kept.events(limit=1)[0]['level']),
                          ('Alarm (Medium · Scales): Scale does not respond', 'warn'))
        self.assertEqual(alarms.Alarms(self.kept).raise_('pump_reset', 2, 'pump', 'The pump control board restarted itself.'), number + 2)   # kind: event

    def test_raising_again_is_no_news_unless_the_text_or_the_seriousness_changed(self):
        number = self.alarms.raise_('pump_link_lost', 3, 'pump', 'The pump control board connection is interrupted and the program is reconnecting.', 'condition')
        count = self.kept.events_rev
        self.assertEqual(self.alarms.raise_('pump_link_lost', 3, 'pump', 'The pump control board connection is interrupted and the program is reconnecting.', 'condition'), number)
        self.assertEqual((self.kept.events_rev, len(self.alarms.view()['active'])), (count, 1))
        self.assertEqual(self.alarms.ack([number]), 1)
        self.assertEqual(self.alarms.raise_('pump_link_lost', 2, 'pump', 'The pump control board connection is interrupted and the program is reconnecting.', 'condition'), number)
        row = self.alarms.view()['active'][0]
        self.assertEqual((row['priority'], row['acked']), (2, False))                 # more serious: to be seen again
        self.assertIn('Alarm (High · Pump)', self.kept.events(limit=1)[0]['text'])
        self.alarms.ack()
        count = self.kept.events_rev
        self.alarms.raise_('pump_link_lost', 3, 'pump', 'The pump control board connection is interrupted and the program is reconnecting.', 'condition')
        row = self.alarms.view()['active'][0]
        self.assertEqual((row['priority'], row['acked'], self.kept.events_rev), (3, True, count))   # less serious: no news
        self.alarms.raise_('slot_not_started:' + 'a' * 32, 3, 'Schedule', 'No. 3 times failed to start:RPi is performing other work')
        first = self.alarms.view()['active'][0]
        self.alarms.raise_('slot_not_started:' + 'a' * 32, 3, 'Schedule', 'No. 4 times failed to start:RPi is performing other work')
        second = self.alarms.view()['active'][0]
        self.assertEqual((second['id'], second['since'], second['text']), (first['id'], first['since'], 'No. 4 times failed to start:RPi is performing other work'))
        for wrong in (('k', 0, 'a', 't'), ('k', 4, 'a', 't'), ('k', True, 'a', 't'), ('k', '1', 'a', 't'), ('', 1, 'a', 't'),
                      ('k', 1, 'a', 't', 'sticky')):
            with self.assertRaises(ValueError):
                self.alarms.raise_(*wrong)

    def test_what_acknowledging_and_clearing_do_to_each_kind(self):
        raise_ = self.alarms.raise_
        event = raise_('round_failed:' + 'a' * 32, 2, 'Process', 'Process failed: No. 2 step failed')            # event (the default)
        latched = raise_('hv_stop_unconfirmed', 1, 'high pressure', 'The stop of high voltage has not been confirmed.', 'latched')
        condition = raise_('rpi_link_lost', 3, 'RPi', 'RPi The connection was interrupted and the program is reconnecting.', 'condition')
        state = lambda: {row['key'].split(':')[0]: (row['acked'], row['cleared']) for row in self.alarms.view()['active']}  # noqa: E731
        # an event is over the moment it has been seen
        self.assertEqual(self.alarms.ack([event], actor='Wang Xiaoming'), 1)
        self.assertEqual(state(), {'hv_stop_unconfirmed': (False, False), 'rpi_link_lost': (False, False)})
        newest = self.kept.events(limit=1)[0]
        self.assertEqual((newest['text'], newest['actor'], newest['level']), ('Acknowledged alarm: Process failed: No. 2 step failed', 'Wang Xiaoming', 'info'))
        self.assertEqual(self.alarms.ack([event, 999, 'x', True]), 0)                     # nothing left with that id
        # a condition that ends before anybody saw it stays listed until it is acknowledged
        self.assertTrue(self.alarms.clear('rpi_link_lost'))
        self.assertEqual(state()['rpi_link_lost'], (False, True))
        self.assertEqual(self.kept.events(limit=1)[0]['text'], 'Alarm cleared (Rpi): RPi The connection was interrupted and the program is reconnecting.')
        self.assertFalse(self.alarms.clear('rpi_link_lost'))                              # already cleared: nothing, no event
        self.assertFalse(self.alarms.clear('never_raised'))
        view = self.alarms.view()
        self.assertEqual((view['unacked'], view['worst'], view['counts']), (2, 1, {'p1': 1, 'p2': 0, 'p3': 1}))
        self.assertEqual(self.alarms.ack([condition]), 1)
        self.assertEqual(state(), {'hv_stop_unconfirmed': (False, False)})
        # a latched alarm stays, acknowledged, until what it waits for has happened
        self.assertEqual(self.alarms.ack([latched]), 1)
        view = self.alarms.view()
        self.assertEqual((state(), view['worst'], view['unacked'], view['counts']['p1']),
                         ({'hv_stop_unconfirmed': (True, False)}, None, 0, 1))
        self.assertTrue(self.alarms.clear('hv_stop_unconfirmed'))
        self.assertEqual(self.alarms.view(), {'worst': None, 'unacked': 0, 'counts': {'p1': 0, 'p2': 0, 'p3': 0}, 'active': []})
        self.assertEqual(read(self.kept.dir / 'alarms.json')['alarms'], [])
        # acknowledge everything at once
        for index in range(3):
            raise_(f'sched_failed:{index}', 2, 'Schedule', f'Schedule stopped: reason {index}')
        self.assertEqual((self.alarms.ack(), self.alarms.view()['active']), (3, []))
        newest = self.kept.events(limit=1)[0]
        self.assertEqual((newest['text'], newest['actor']),
                          ('Acknowledged 3 alarms: Schedule stopped: reason 0; Schedule stopped: reason 1; Schedule stopped: reason 2', ''))
        self.assertEqual(self.alarms.ack(), 0)

    def test_an_alarm_that_comes_back_is_a_new_one(self):
        first = self.alarms.raise_('nano_fault', 2, 'high pressure', 'Nano Fault: Heartbeat interrupted.', 'condition')
        self.alarms.clear('nano_fault')
        second = self.alarms.raise_('nano_fault', 2, 'high pressure', 'Nano Fault: Heartbeat interrupted.', 'condition')
        rows = self.alarms.view()['active']
        self.assertEqual((len(rows), rows[0]['id'], rows[0]['cleared'], rows[0]['acked']), (1, second, False, False))
        self.assertNotEqual(first, second)
        self.assertEqual(self.alarms.ack([first]), 0)      # a late "confirm" of the old one does not hide the new one
        self.assertEqual(self.alarms.view()['unacked'], 1)

    def test_the_list_is_ordered_unacknowledged_first_then_by_priority_then_newest(self):
        ids = {}
        for key, priority in (('a', 3), ('b', 1), ('c', 2), ('d', 1), ('e', 3)):
            ids[key] = self.alarms.raise_(key, priority, 'pump', key, 'latched')
            time.sleep(.002)
        self.alarms.ack([ids['b'], ids['e']])
        view = self.alarms.view()
        self.assertEqual([row['key'] for row in view['active']], ['d', 'c', 'a', 'b', 'e'])
        self.assertEqual((view['worst'], view['unacked'], view['counts']), (1, 3, {'p1': 2, 'p2': 1, 'p3': 2}))
        for index in range(120):
            self.alarms.raise_(f'many:{index}', 3, 'Schedule', f'No. {index} indivual')
        view = self.alarms.view()
        self.assertEqual((len(view['active']), view['unacked'], view['counts']['p3']), (100, 123, 122))

    def test_after_a_restart_alarms_are_still_there_and_conditions_start_as_cleared(self):
        raise_ = self.alarms.raise_
        latched = raise_('pump_stop_unconfirmed', 1, 'pump', 'Pump stop not confirmed', 'latched')
        seen = raise_('hv_stop_unconfirmed', 1, 'high pressure', 'High voltage stop not confirmed', 'latched')
        event = raise_('sched_interrupted:' + 'a' * 32, 2, 'Schedule', 'The schedule has been interrupted and will not be automatically restored. Please restart the schedule after confirming the device.')
        unseen = raise_('rpi_link_lost', 3, 'RPi', 'RPi The connection was interrupted and the program is reconnecting.', 'condition')
        scale = raise_('scale_offline', 3, 'scales', 'Scale does not respond', 'condition')
        self.assertEqual(self.alarms.ack([seen, scale]), 2)
        count = self.kept.events_rev
        again = alarms.Alarms(store.Store(self.kept.dir))                                  # the program starts again
        rows = {row['key'].split(':')[0]: row for row in again.view()['active']}
        self.assertEqual({key: (row['id'], row['acked'], row['cleared']) for key, row in rows.items()},
                         {'pump_stop_unconfirmed': (latched, False, False), 'hv_stop_unconfirmed': (seen, True, False),
                          'sched_interrupted': (event, False, False), 'rpi_link_lost': (unseen, False, True)})
        self.assertEqual(store.Store(self.kept.dir).events_rev, count)                    # marking them is not news
        saved = read(self.kept.dir / 'alarms.json')
        self.assertEqual((saved['next_id'], len(saved['alarms'])), (6, 4))
        back = again.raise_('rpi_link_lost', 3, 'RPi', 'RPi The connection was interrupted and the program is reconnecting.', 'condition')   # its owner: it still holds
        row = next(row for row in again.view()['active'] if row['key'] == 'rpi_link_lost')
        self.assertEqual((back, row['id'], row['cleared'], row['acked']), (6, 6, False, False))
        self.assertEqual(alarms.Alarms(self.kept).raise_('pump_reset', 2, 'pump', 'Restart'), 7)          # ids are never used twice

    def test_a_damaged_list_starts_empty_and_a_list_that_cannot_be_saved_goes_on(self):
        (self.kept.dir / 'alarms.json').write_text('{"next_id": "x", "alarms": [{"id": 1}, 5, {"id": 2, "key": "k", '
                                                   '"priority": 9, "kind": "event"}]}', encoding='utf-8')
        fresh = alarms.Alarms(self.kept)
        self.assertEqual((fresh.view()['active'], fresh.next_id), ([], 1))
        (self.kept.dir / 'alarms.json').write_text('not json', encoding='utf-8')
        fresh = alarms.Alarms(self.kept)
        with mock.patch.object(self.kept, 'write_json', side_effect=PermissionError(13, 'Access denied When')):
            number = fresh.raise_('pump_reset', 2, 'pump', 'The pump control board restarted itself.')
        self.assertEqual(fresh.view()['active'][0]['id'], number)
        texts = self.texts(self.kept)
        self.assertIn('Alarm (High · Pump): The pump control board restarted itself.', texts)
        self.assertTrue(any(text.startswith('The alarm record was not archived successfully:') for text in texts))


def fast_pin_file(path, pin):
    """A pin.json in the real format with one PBKDF2 round, so that many tries take no time."""
    salt = bytes(range(32, 64))
    store.write_json(Path(path), {'salt': salt.hex(), 'digest': hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 1).hex(),
                                  'iterations': 1})


class PinTests(Case):
    def test_without_a_pin_file_nothing_is_allowed_and_setting_one_needs_no_restart(self):
        path = self.folder() / 'data' / 'pin.json'
        lock = pinlock.PinLock(path)
        self.assertEqual((lock.configured, lock.allowed(None), lock.allowed('anything'), lock.peek('anything')),
                         (False, False, False, 0))                                   # 1.3.0 allowed everything here
        for call in (lock.unlock, lock.verify):
            with self.assertRaisesRegex(RuntimeError, '^has not been set for maintenance PIN$'):
                call('246810')
        for bad in ('12345', '123456789', 'abcdef', '12345 ', 123456, None):
            with self.assertRaisesRegex(ValueError, '^Maintenance PIN Must be 6–8 digits$'):
                lock.set(bad)
        self.assertFalse(path.exists())
        pinlock.PinLock(path).set('246810')                                           # what Set-PIN.cmd does, in another process
        self.assertTrue(lock.configured)
        record = read(path)
        self.assertEqual((set(record), record['iterations'], len(record['salt']), len(record['digest'])),
                         ({'salt', 'digest', 'iterations'}, 600_000, 64, 64))
        self.assertNotIn('246810', path.read_text(encoding='utf-8'))
        with self.assertRaisesRegex(RuntimeError, '^Maintenance PIN mistake$'):
            lock.unlock('000000')
        session = lock.unlock('246810')
        self.assertEqual((lock.allowed(session), lock.allowed('other'), lock.allowed(None), lock.peek(session)),
                         (True, False, False, 600))
        lock.lock_session(session)
        self.assertEqual((lock.allowed(session), lock.peek(session)), (False, 0))

    def test_looking_does_not_renew_a_session_and_using_it_does(self):
        path = self.folder() / 'pin.json'
        fast_pin_file(path, '246810')
        lock = pinlock.PinLock(path)
        session = lock.unlock('246810')
        lock.sessions[session] = time.monotonic() + 100.9      # as if 500 s had passed
        for _ in range(3):
            self.assertEqual(lock.peek(session), 101)
        self.assertLess(lock.sessions[session], time.monotonic() + 101)
        self.assertTrue(lock.allowed(session))                 # an action that needs the PIN
        self.assertEqual(lock.peek(session), 600)
        lock.sessions[session] = time.monotonic() - .1         # ten minutes without one
        self.assertEqual((lock.peek(session), lock.allowed(session), lock.allowed(session)), (0, False, False))
        self.assertEqual(lock.sessions, {})

    def test_five_wrong_tries_lock_for_thirty_seconds_and_every_further_lockout_doubles(self):
        path = self.folder() / 'pin.json'
        fast_pin_file(path, '246810')
        lock = pinlock.PinLock(path)
        waits = []
        for _round in range(7):
            for attempt in range(5):
                with self.assertRaisesRegex(RuntimeError, '^Maintenance PIN mistake$'):
                    (lock.verify if attempt % 2 else lock.unlock)('000000')
            waits.append(round(lock.next_try - time.monotonic()))
            with self.assertRaisesRegex(RuntimeError, r'^Too many attempts, please \d+ Try again in seconds$'):
                lock.unlock('246810')                           # the right PIN does not help during a lockout
            lock.next_try = time.monotonic() - 1                # ...time passes
        self.assertEqual(waits, [30, 60, 120, 240, 480, 900, 900])
        self.assertTrue(lock.verify('246810'))
        self.assertEqual((lock.failures, lock.lockouts, lock.sessions), (0, 0, {}))   # verify opens no session
        for _ in range(4):
            with self.assertRaises(RuntimeError):
                lock.unlock('111111')
        self.assertTrue(lock.unlock('246810'))                  # four wrong tries are forgiven by the right one
        self.assertEqual(lock.failures, 0)

    def test_a_pin_file_that_was_replaced_ends_every_session(self):
        path = self.folder() / 'pin.json'
        fast_pin_file(path, '246810')
        lock = pinlock.PinLock(path)
        session = lock.unlock('246810')
        self.assertEqual(lock.peek(session), 600)
        fast_pin_file(path, '135246')                           # Set-PIN.cmd was run again
        self.assertEqual((lock.peek(session), lock.allowed(session)), (0, False))
        with self.assertRaisesRegex(RuntimeError, 'Maintenance PIN mistake'):
            lock.unlock('246810')
        session = lock.unlock('135246')
        content = path.read_bytes()
        path.write_bytes(content)                               # the same content written again is a new file too
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 5_000_000))
        self.assertEqual(lock.peek(session), 0)
        session = lock.unlock('135246')
        for _ in range(5):
            with self.assertRaises(RuntimeError):
                lock.unlock('000000')
        with self.assertRaisesRegex(RuntimeError, 'Too many attempts'):
            lock.unlock('135246')
        fast_pin_file(path, '777777')                           # a forgotten PIN: the new file is usable at once
        self.assertTrue(lock.unlock('777777'))
        session = lock.unlock('777777')
        path.unlink()
        self.assertEqual((lock.configured, lock.peek(session), lock.allowed(session)), (False, 0, False))
        path.write_text('{"salt": "zz"}', encoding='utf-8')     # not a PIN file
        with self.assertRaisesRegex(RuntimeError, '^has not been set for maintenance PIN$'):
            lock.unlock('777777')

    def test_a_pin_that_is_not_text_is_a_wrong_pin(self):
        """A request body may carry a number or nothing where the PIN belongs: that is a wrong PIN
        (and counts as a try), not "no PIN has been set"."""
        path = self.folder() / 'pin.json'
        fast_pin_file(path, '246810')
        lock = pinlock.PinLock(path)
        for bad in (246810, None, ['246810'], 2468.10, True):
            for call in (lock.unlock, lock.verify):
                with self.assertRaisesRegex(RuntimeError, '^(Maintenance PIN mistake|Too many attempts, please \\d+ Try again in seconds)$'):
                    call(bad)
        self.assertGreaterEqual(lock.lockouts, 1)   # they were counted: five wrong tries lock

    def test_the_practice_pin_is_fixed_and_no_file_is_ever_looked_at(self):
        path = self.folder() / 'pin.json'
        fast_pin_file(path, '246810')

        def forbidden(*_args, **_kwargs):
            raise AssertionError('practice mode looked at a file')
        with mock.patch.object(Path, 'read_text', forbidden), mock.patch.object(Path, 'is_file', forbidden), \
                mock.patch.object(Path, 'stat', forbidden), mock.patch.object(Path, 'open', forbidden), \
                mock.patch.object(pinlock, 'write_json', forbidden):
            for lock in (pinlock.PinLock(None, practice_pin='000000'), pinlock.PinLock(path, practice_pin='000000')):
                self.assertTrue(lock.configured)
                with self.assertRaisesRegex(RuntimeError, 'Maintenance PIN mistake'):
                    lock.unlock('246810')                       # the PIN of the real file means nothing here
                session = lock.unlock('000000')
                self.assertEqual((lock.allowed(session), lock.peek(session), lock.verify('000000')), (True, 600, True))
                with self.assertRaisesRegex(RuntimeError, 'Practice mode maintenance PIN fixed yes 000000'):
                    lock.set('123456')
        self.assertEqual(hashlib.pbkdf2_hmac('sha256', b'246810', bytes(range(32, 64)), 1).hex(), read(path)['digest'])
        for bad in ('12345', 'abcdef', 0):
            with self.assertRaises(ValueError):
                pinlock.PinLock(None, practice_pin=bad)
        nothing = pinlock.PinLock(None)                          # neither a file nor a practice PIN
        self.assertEqual((nothing.configured, nothing.allowed('x'), nothing.peek('x')), (False, False, 0))

    def test_what_set_pin_says_when_it_is_done(self):
        self.assertEqual(pinlock.SAVED_TEXT, 'Maintenance PIN has been saved. forget PIN , double-click again Set-PIN.cmd Set new.')
        self.assertNotIn('pin.json', pinlock.SAVED_TEXT)       # 1.3.0 said "delete pin.json": that would now close Maintenance


class ScheduleNameTests(unittest.TestCase):
    """What 2.0.0 added to pc/schedule.py (SPEC B.6). Nothing the 1.x program uses changed its meaning."""

    def test_states_and_their_words(self):
        self.assertEqual(schedule.ACTIVE, ('waiting', 'running', 'pausing', 'paused', 'stopping'))
        self.assertEqual(schedule.BUSY, ('waiting', 'running', 'pausing', 'stopping'))
        self.assertEqual(schedule.STATUS_LABEL, {
            'waiting': 'Waiting for the next time', 'running': 'under measurement', 'pausing': 'Paused', 'paused': 'Suspended', 'stopping': 'Stopping',
            'completed': 'Completed', 'stopped': 'Stopped', 'failed': 'stopped due to failure', 'interrupted': 'Interrupted'})
        self.assertEqual(schedule.SLOT_LABEL, {
            'running': 'under measurement', 'done': 'Complete', 'failed': 'failed', 'stopped': 'Stopped', 'not_started': 'not started',
            'skipped_overrun': 'Skip (last timeout)', 'skipped_paused': 'skip (scheduling pause)'})
        self.assertEqual(schedule.ROUND_LABEL, {
            'running': 'is executing', 'pausing': 'Paused', 'paused': 'Suspended', 'stopping': 'Stopping', 'completed': 'Complete',
            'failed': 'failed', 'stopped': 'Stopped', 'interrupted': 'Interrupted'})
        self.assertEqual(schedule.STEP_LABEL, {'pending': '—', 'running': 'is executing', 'done': 'Complete', 'failed': 'failed',
                                               'stopped': 'Stopped', 'skipped': 'not executed'})
        self.assertTrue(set(schedule.BUSY) < set(schedule.ACTIVE) <= set(schedule.STATUS_LABEL))


if __name__ == '__main__':
    unittest.main(verbosity=2)
