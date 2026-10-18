#!/usr/bin/env python3
"""Plasmic electronic control - folder upgrade: 1.3.0/1.4.x of data folder patch 2.0.0 look (SPEC D.3) .

Store will be called first when establishing run().version.json has been written data_version 2 does nothing.

Only ”add” here: add files, add schedule.json Riga field, in ”Brand new” in events.jsonl . Don’t delete anything,
Don’t change either config.json, pin.json, runs\\ . If any step fails, only one event will be recorded, and the original file will remain in place.
The program will still start; if it is not finished, it will be done again next time it is started (each step can be redone, and the backup will only be done once).

Only use Python standard function library. For writing store.py of write_json (is only in the function import: store.py also import file).
"""
from __future__ import annotations

import csv
import io
import json
import shutil
from datetime import datetime
from pathlib import Path

DATA_VERSION = 2
PROGRAM = '2.0.0'
EVENTS_TAIL = 2_000_000   # bytes of events.jsonl that go into the backup
HEADER = ['action', 'target', 'value', 'unit', 'note']
SEED_FILE = 'plasma-only.csv'
SEED_NAME = 'Only discharge measurement'
# What recipe.validate() says about that one-line file (SPEC A.6); checked against the file before use.
SEED_FACTS = {'step_count': 1, 'rounds': 1, 'uses_pump': False, 'uses_rpi': True, 'uses_plasma': True}
LEGACY_RECIPE = 'Discharge measurement (1.3.0) '
DONE_TEXT = ('Data upgraded to 2.0.0. Original connection settings, maintenance PIN, '
             'and spectral records were retained.')


def _event(data, text, level='info'):
    """One line at the end of events.jsonl, in the shape Store.event writes."""
    import store
    path = data / 'events.jsonl'
    try:
        cut_off = False  # a last line without its line end: the next one must not be glued to it
        if path.is_file() and path.stat().st_size:
            with path.open('rb') as stream:
                stream.seek(-1, 2)
                cut_off = stream.read(1) != b'\n'
        store.append_jsonl(path, {'time': store.now_iso(), 'level': level, 'text': str(text)[:400],
                                  'actor': store.SYSTEM}, newline_first=cut_off)
    except OSError:
        pass  # a log that cannot be written must not stop the program from starting


def _read_json(path):
    """The file's content; None when there is no such file or it is not JSON."""
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (FileNotFoundError, ValueError):
        return None


def _backup(data, old, **_):
    """1. Copies of what 1.3.0 left, before anything is added."""
    target = data / 'backup'
    if not old or (target.is_dir() and any(not path.name.endswith('.part') for path in target.glob('from-1.3.0-*'))):
        return
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    work = target / f'from-1.3.0-{stamp}.part'  # renamed when complete: a half-made backup never counts as one
    work.mkdir(parents=True, exist_ok=True)
    for name in ('config.json', 'schedule.json'):
        if (data / name).is_file():
            shutil.copy2(data / name, work / name)
    events = data / 'events.jsonl'
    if events.is_file():
        with events.open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - EVENTS_TAIL))
            if size > EVENTS_TAIL:
                stream.readline()  # begin with a whole line
            (work / 'events.jsonl').write_bytes(stream.read())
    work.rename(target / f'from-1.3.0-{stamp}')


def _lists(data, store, **_):
    """2. The operator list (one neutral name, so the first measurement needs no maintenance) and the alarm list."""
    if not (data / 'operators.json').exists():
        store.write_json(data / 'operators.json', {'operators': ['operator']})
    if not (data / 'alarms.json').exists():
        store.write_json(data / 'alarms.json', {'next_id': 1, 'alarms': []})


def _pump(data, store, vendor, **_):
    """3. Pump-board settings ("never chosen") and the first scale calibration, in every data folder."""
    if not (data / 'pumpboard.json').exists():
        store.write_json(data / 'pumpboard.json', dict(store.PUMP_DEFAULTS))
    target = data / 'pumpboard' / 'scale_calibration.json'
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.tmp')
        shutil.copyfile(vendor / 'v7' / 'config' / 'scale_calibration.json', temporary)  # byte for byte: V7 reads it
        temporary.replace(target)


def _recipes(data, store, recipes, **_):
    """4. recipes\\; a folder that never held a Recipe gets “Discharge measurement only”."""
    folder = data / 'recipes'
    folder.mkdir(parents=True, exist_ok=True)
    deleted = folder / 'deleted'
    if any(folder.glob('*.json')) or (deleted.is_dir() and any(deleted.iterdir())):
        return
    text = (recipes / SEED_FILE).read_text(encoding='utf-8-sig')
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if len(rows) != 2 or rows[0] != HEADER or len(rows[1]) != 5 or rows[1][:4] != ['plasma_run', '', '', '']:
        # SEED_FACTS describes exactly this file; anything else must go through the Recipe check first.
        raise ValueError(f'{SEED_FILE} does not contain the expected single plasma_run row')
    record = store.recipe_record(SEED_NAME, text, SEED_FACTS, 'seed')
    store.write_json(folder / (record['id'] + '.json'), record)


def _schedule(data, store, **_):
    """5. The last 1.3.0 schedule gets the keys 2.0.0 reads. Nothing is removed (a "sequence" stays)."""
    path = data / 'schedule.json'
    record = _read_json(path)
    if not isinstance(record, dict) or 'version' in record:
        return
    for key, value in (('operator', ''), ('recipe', {'id': None, 'name': LEGACY_RECIPE, 'sha256': None}),
                       ('uses_plasma', True), ('uses_rpi', True), ('uses_pump', False),
                       ('confirmed', {'high_voltage': True, 'liquid': False, 'hv_off_checked': False})):
        record.setdefault(key, value)
    record['version'] = 2
    store.write_json(path, record)


def _runlog(data, store, **_):
    """6. runlog.jsonl, built once: the old discharges, so that they show in execution record."""
    target = data / 'runlog.jsonl'
    if target.exists():
        return
    rows = []
    for path in sorted((data / 'runs').glob('*.meta.json')) if (data / 'runs').is_dir() else []:
        meta = _read_json(path)
        if not isinstance(meta, dict) or meta.get('kind') != 'plasma':
            continue  # captures without discharge stay visible in Maintenance › spectrum
        job = meta.get('job')
        if not isinstance(job, str) or not store.RUN_ID.fullmatch(job):
            continue
        peak = meta.get('peak_nm')
        rows.append({'kind': 'legacy', 'time': meta.get('started_at') or meta.get('saved_at') or '', 'operator': '',
                     'recipe_name': LEGACY_RECIPE, 'schedule_id': None, 'slot': None, 'slots_planned': None,
                     'status': 'completed', 'finished_at': None, 'round_id': None, 'spectra': [job],
                     'result_text': f'highest peak {peak:.1f} nm' if type(peak) in (int, float) else '', 'note': ''})

    def order(row):  # oldest first; a row without a usable time comes before all others
        when = store.moment(row['time'])
        return (0.0 if when is None else when.timestamp(), row['spectra'][0])
    rows.sort(key=order)
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_bytes(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode('utf-8'))
    temporary.replace(target)


def _folders(data, **_):
    """7. rounds\\ and logs\\ (version.json itself is written by run(), last of all)."""
    for name in ('rounds', 'logs'):
        (data / name).mkdir(parents=True, exist_ok=True)


STEPS = (('Backup', _backup), ('Operator and alarm list', _lists), ('Pump control panel settings', _pump), ('Recipe', _recipes),
         ('Scheduling record', _schedule), ('execution record', _runlog), ('folder', _folders))


def run(data_dir, vendor_dir, recipes_dir):
    """Bring one data folder to layout 2. Never raises; safe to run again.

    Returns {'ran', 'done', 'migrated_from', 'errors'}: ``ran`` False = the folder was at layout 2 already;
    ``done`` False = at least one step failed (it was written to the event log and is tried again at the
    next start).
    """
    import store  # here, not at the top: store.py imports this module
    data = Path(data_dir)
    report = {'ran': False, 'done': False, 'migrated_from': None, 'errors': []}
    try:
        data.mkdir(parents=True, exist_ok=True)
        version = _read_json(data / 'version.json')
        version = version if isinstance(version, dict) else {}
        if type(version.get('data_version')) is int and version['data_version'] >= DATA_VERSION:
            report.update(done=True, migrated_from=version.get('migrated_from'))
            return report
        report['ran'] = True
        if 'migrated_from' in version:
            # An earlier try did not finish. What it found then still holds; meanwhile its own event
            # lines have made events.jsonl, which must not make an empty folder look like an old one.
            old = version['migrated_from'] is not None
        else:
            old = any((data / name).is_file() for name in ('config.json', 'schedule.json', 'events.jsonl'))
        origin = '1.3.0' if old else None
        report['migrated_from'] = origin
        started = {'data_version': 1, 'program': PROGRAM, 'migrated_from': origin, 'migrated_at': None}
        if version != started:
            store.write_json(data / 'version.json', started)
        for number, (name, step) in enumerate(STEPS, 1):
            try:
                step(data=data, store=store, old=old, vendor=Path(vendor_dir), recipes=Path(recipes_dir))
            except Exception as exc:  # one failed step never stops the others, nor the program
                report['errors'].append(f'{name}: {exc}')
                _event(data, f'Data upgrade to 2.0.0 did not finish (step {number}: {name}): {exc}. '
                             'The original files were not moved; the upgrade will retry at the next start.', 'error')
        if not report['errors']:
            store.write_json(data / 'version.json', {'data_version': DATA_VERSION, 'program': PROGRAM,
                                                     'migrated_from': origin, 'migrated_at': store.now_iso()})
            report['done'] = True
            if old:
                _event(data, DONE_TEXT)
    except Exception as exc:  # e.g. a folder that cannot be written at all
        report['errors'].append(str(exc))
    return report
