#!/usr/bin/env python3
"""Plasmic Electronic Control — Everything this program puts on the hard drive (data folder).

connection settings, event records, operator list, pump control panel settings,Recipe, Records and execution records of each round of process,
schedule record, spectrum. What the file looks like is written in Handover\\integrated design-2.0.0\\design\\SPEC.md of D .

There are only three rules:
  1. each JSON files are used write_json write (write first .tmp and then replace it), the half-written file will not be left.
  2. .jsonl Only adds one line to the end; skips the broken line when reading.
  3. data The program will never delete the items in the folder. Delete Starting from Recipe is to move the file to recipes\\deleted.

Only use Python standard function library; no import app.py.Store , do a data upgrade first (migrate.py) .
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import migrate
import netcheck

ROOT = Path(__file__).resolve().parent
SITE = ROOT.parent / 'installer' / 'site.json'
VENDOR = ROOT / 'vendor'    # the copied V7 files: the migration takes the first scale calibration from here
RECIPES = ROOT / 'recipes'  # the example Recipes: the migration publishes plasma-only.csv from here
RUN_ID = re.compile(r'(?:HMI|CAP|LITE)-[0-9a-f]{32}')
HEX_ID = re.compile(r'[0-9a-f]{32}')
ROUND_FILE = re.compile(r'ROUND-[0-9a-f]{32}\.json')
DEFAULT_CONFIG = {'rpi_host': '192.168.1.1', 'rpi_port': 8767, 'token': ''}
PUMP_DEFAULTS = {'version': 1, 'enabled': None, 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4}
# What a spectrum set may be told about where it came from (kept in its .meta.json).
RUN_EXTRA = ('round_id', 'recipe_name', 'operator', 'slot', 'source')
RECIPE_FACTS = ('step_count', 'rounds', 'uses_pump', 'uses_rpi', 'uses_plasma')
RECIPE_SOURCES = ('seed', 'import', 'flow')      # flow: made by the schedule page out of its four stages
FLOW_RECIPE_ID = 'f10' + '0' * 29                # the one Recipe the schedule page keeps up to date (AMENDMENTS M16)
RECIPE_CHANGED = 'Recipe file is different from when published, please re-publish.'
OPERATOR_RULE = 'Operator names must be 1–20 characters and cannot contain < > " \\'
SYSTEM = 'system'           # who did it, when the program did it by itself
KEEP_EVENTS = 2000        # lines of events.jsonl kept in memory (the file keeps all of them)
KEEP_RUNLOG = 5000        # the same for runlog.jsonl
TAIL_BYTES = 4_000_000    # how much of the end of a .jsonl file is read at start
PAGE_LIMIT = 1000         # most rows one question may ask for


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value, compact=False):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    indent=None if compact else 2,
                                    separators=(',', ':') if compact else None), encoding='utf-8')
    # Windows refuses the rename while antivirus or an editor briefly holds the target.
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.1)


def validate_config(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_CONFIG):
        raise ValueError('Connection settings contain unknown fields')
    config = dict(DEFAULT_CONFIG)
    config.update(raw)
    host = config['rpi_host']
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.\-]{1,253}', host.strip()):
        raise ValueError('RPi The address must be IP or host name, for example 192.168.1.1')
    config['rpi_host'] = host.strip()
    if type(config['rpi_port']) is not int or not 1 <= config['rpi_port'] <= 65535:
        raise ValueError('TCP The port must be 1–65535')
    if not isinstance(config['token'], str) or len(config['token']) > 256 or any(c in config['token'] for c in '\r\n'):
        raise ValueError('token format is incorrect')
    config['token'] = config['token'].strip()
    return config


def site_config():
    """Before Install.cmd has written data/config.json: the RPi address this folder is set up for."""
    try:
        host = json.loads(SITE.read_text(encoding='utf-8-sig')).get('rpi_host')
    except (OSError, ValueError, AttributeError):
        return {}
    return {'rpi_host': host} if isinstance(host, str) and netcheck.IPV4.fullmatch(host) else {}


def run_csv(run):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    if run['kind'] == 'plasma':
        writer.writerow(['wavelength_nm', 'background_mean', 'signal_mean', 'difference'])
        writer.writerows(zip(run['axis'], run['background'], run['signal'], run['difference']))
    else:
        writer.writerow(['wavelength_nm', 'mean'] + [f'frame_{i}' for i in range(1, len(run['frames']) + 1)])
        writer.writerows(zip(run['axis'], run['mean'], *run['frames']))
    return out.getvalue().encode('utf-8-sig')


def append_jsonl(path, record, newline_first=False):
    """One more line at the end of a .jsonl file: one write, then flush."""
    with path.open('a', encoding='utf-8', errors='replace') as out:
        out.write(('\n' if newline_first else '') + json.dumps(record, ensure_ascii=False) + '\n')
        out.flush()


def moment(text):
    """An ISO time as written by this program -> a datetime that can be compared with any other; else None."""
    try:
        value = datetime.fromisoformat(text)
        value = value if value.tzinfo is not None else value.astimezone()
        value.timestamp()  # a year the clock cannot count is no time either
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return value


def _plain_name(text, longest):
    """True for 1..longest characters on one line: no control characters, no line breaks."""
    return (1 <= len(text) <= longest
            and not any(unicodedata.category(c) in ('Cc', 'Zl', 'Zp') for c in text))


def validate_operators(names):
    """The whole operator list as it may be saved (SPEC B.8); ValueError carries the message to show."""
    if not isinstance(names, (list, tuple)):
        raise ValueError('The operator list format is incorrect.')
    checked = []
    for name in names:
        if not isinstance(name, str) or not _plain_name(name.strip(), 20) or any(c in '<>"\\' for c in name):
            raise ValueError(OPERATOR_RULE)
        if name.strip() not in checked:  # the same name twice is one person
            checked.append(name.strip())
    if len(checked) > 50:
        raise ValueError('At most 50 operators are allowed.')
    return checked


def recipe_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def recipe_record(name, text, summary, source):
    """A Recipe as it is kept on disk (SPEC D.2). ValueError carries the message to show.

    ``summary`` is the result of recipe.validate() (or any dict with the five facts of RECIPE_FACTS);
    the stored text is UTF-8 with LF, whatever came in.
    """
    if not isinstance(name, str) or not _plain_name(name.strip(), 60):
        raise ValueError('Recipe names must be 1–60 characters.')
    if not isinstance(text, str) or not isinstance(summary, dict) or source not in RECIPE_SOURCES:
        raise TypeError('publish_recipe(name, csv, summary, source): csv is text, summary a dict, '
                        'source "seed" or "import"')
    text = text.lstrip('').replace('\r\n', '\n').replace('\r', '\n')
    record = {'id': uuid.uuid4().hex, 'name': name.strip(), 'created_at': now_iso(), 'source': source,
              'csv': text, 'sha256': recipe_sha256(text)}
    for key in RECIPE_FACTS:
        if key not in summary:
            raise TypeError('publish_recipe: the summary has no "%s"' % key)
        record[key] = summary[key]
    return record


def recipe_intact(record):
    """True when a stored Recipe still is what was published: the SHA-256 of its CSV text matches."""
    return (isinstance(record, dict) and isinstance(record.get('csv'), str) and isinstance(record.get('name'), str)
            and record.get('sha256') == recipe_sha256(record['csv']))


class Journal:
    """One append-only .jsonl file. Its newest lines are kept in memory; older ones are read when asked for."""

    def __init__(self, path, keep):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.rows = deque(maxlen=keep)
        self.complete = True     # the memory holds every line of the file
        self.unfinished = False  # the file ends in the middle of a line (the program died while writing)
        self.rev = 0             # grows with every line; the page reloads its list when this changes
        try:
            size = self.path.stat().st_size
            with self.path.open('rb') as source:
                if size > TAIL_BYTES:
                    source.seek(size - TAIL_BYTES)
                    source.readline()  # the rest of a line that began before this part
                    self.complete = False
                lines = source.read().split(b'\n')
        except OSError:
            return
        self.unfinished = bool(lines[-1])
        newest = []
        while lines and len(newest) < keep:
            row = self._parse(lines.pop())
            if row is not None:
                newest.append(row)
        self.complete = self.complete and not any(line.strip() for line in lines)
        self.rows.extend(reversed(newest))
        self.rev = len(self.rows)

    @staticmethod
    def _parse(line):
        try:
            row = json.loads(line.decode('utf-8', 'replace'))
        except ValueError:
            return None  # an empty line, or one that was cut off
        return row if isinstance(row, dict) else None

    def append(self, record):
        """Add one line. A file that cannot be written does not stop the program: the line stays in memory."""
        with self.lock:
            if len(self.rows) == self.rows.maxlen:
                self.complete = False
            self.rows.append(record)
            self.rev += 1
            try:
                append_jsonl(self.path, record, newline_first=self.unfinished)
                self.unfinished = False
            except OSError:
                return False
        return True

    def page(self, before=None, limit=50):
        """Up to ``limit`` rows, newest first; with ``before`` (an ISO time) only rows older than that."""
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        limit = min(limit, PAGE_LIMIT)
        cut = None
        if before is not None:
            cut = moment(before)
            if cut is None:
                raise ValueError('before is incorrect Checklist for')
        with self.lock:
            rows, complete = list(self.rows), self.complete
        found = [row for row in reversed(rows) if self._older(row, cut)][:limit]
        if len(found) == limit or complete or cut is None:
            return found
        # The question reaches further back than the memory does.
        found = deque(maxlen=limit)
        try:
            with self.path.open('rb') as source:
                for line in source:
                    row = self._parse(line)
                    if row is not None and self._older(row, cut):
                        found.append(row)
        except OSError:
            pass
        return list(found)[::-1]

    @staticmethod
    def _older(row, cut):
        if cut is None:
            return True
        when = moment(row.get('time'))
        return when is None or when < cut


class Store:
    """Everything this program keeps on disk: settings, logs, Recipes, records and spectra."""

    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        # 1.3.0 -> 2.0.0, once per data folder; never raises, never deletes (SPEC D.3).
        self.migration = migrate.run(self.dir, VENDOR, RECIPES)
        self.runs = self.dir / 'runs'
        self.runs.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.recipe_lock = threading.Lock()  # publish and delete, one at a time
        self._gates = [threading.Lock() for _ in range(16)]  # one writer per JSON file at a time
        self.config_path = self.dir / 'config.json'
        self.config = validate_config(json.loads(self.config_path.read_text(encoding='utf-8-sig'))
                                      if self.config_path.is_file() else site_config())
        self.run_rows = []  # newest first; read once, then kept current by save_run
        for meta_path in self.runs.glob('*.meta.json'):
            try:
                row = json.loads(meta_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(row, dict) and isinstance(row.get('job'), str) and RUN_ID.fullmatch(row['job']):
                self.run_rows.append(row)
        self.run_rows.sort(key=lambda row: row.get('saved_at') or '', reverse=True)
        self.runs_rev = len(self.run_rows)
        self._events = Journal(self.dir / 'events.jsonl', KEEP_EVENTS)
        self._runlog = Journal(self.dir / 'runlog.jsonl', KEEP_RUNLOG)
        self._operators = self._read_operators()
        self.recipes_rev = sum(1 for _ in self._recipe_files())

    # ---- write file
    def write_json(self, path, value, compact=False):
        """write_json for a file of this data folder: two threads never write the same file at once."""
        path = Path(path)
        with self._gates[hash(os.path.normcase(os.path.abspath(path))) % len(self._gates)]:
            write_json(path, value, compact)

    # ---- connection settings
    def save_config(self, raw):
        config = validate_config(raw)
        with self.lock:
            write_json(self.config_path, config)
            self.config = config
        return config

    # ---- event record
    def event(self, text, level='info', actor=None):
        """One line of events.jsonl. ``actor``: the operator's name, “Maintenance”, “system” On (None) or ''."""
        record = {'time': now_iso(), 'level': level, 'text': str(text)[:400],
                  'actor': SYSTEM if actor is None else str(actor)[:40]}
        self._events.append(record)

    def events(self, before=None, limit=50):
        """The newest events first: [{time, level, text, actor}]. Lines written by 1.3.0 have no actor: “system” On.

        ``before`` (an ISO time) gives only older lines. Ask for one more than you show to know whether
        there are more.
        """
        return [{'time': row.get('time'), 'level': row.get('level', 'info'), 'text': str(row.get('text', '')),
                 'actor': row.get('actor', SYSTEM)} for row in self._events.page(before, limit)]

    @property
    def events_rev(self):
        return self._events.rev

    def recent_events(self, count=60):
        return self.events(limit=count)

    # ---- operator
    def _read_operators(self):
        """operators.json; a name that breaks the rules (the file was edited by hand) is left out."""
        try:
            names = json.loads((self.dir / 'operators.json').read_text(encoding='utf-8-sig')).get('operators')
        except (OSError, ValueError, AttributeError):
            return []
        kept = []
        for name in names if isinstance(names, list) else []:
            try:
                kept += [found for found in validate_operators([name]) if found not in kept]
            except ValueError:
                continue
        return kept[:50]

    def operators(self):
        with self.lock:
            return list(self._operators)

    def set_operators(self, names):
        """Replace the whole list; ValueError says what is wrong with it."""
        names = validate_operators(names)
        with self.lock:
            self.write_json(self.dir / 'operators.json', {'operators': names})
            self._operators = names
        return list(names)

    # ---- pump control panel setting (pumpboard.json,SPEC B.3.2) 
    def pump_settings(self):
        try:
            saved = json.loads((self.dir / 'pumpboard.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            saved = None
        return saved if isinstance(saved, dict) else dict(PUMP_DEFAULTS)

    def save_pump_settings(self, settings):
        if not isinstance(settings, dict):
            raise ValueError('Pump control panel setting format is incorrect')
        settings = dict(settings)
        self.write_json(self.dir / 'pumpboard.json', settings)
        return settings

    # ---- Recipe
    def _recipe_files(self):
        """Every stored Recipe that can be read at all, in no particular order."""
        try:
            paths = [path for path in (self.dir / 'recipes').glob('*.json') if HEX_ID.fullmatch(path.stem)]
        except OSError:
            paths = []
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and isinstance(record.get('name'), str):
                record['id'] = path.stem  # the file name is the id: nothing inside the file can point elsewhere
                yield record

    def _recipe_path(self, recipe_id):
        if not isinstance(recipe_id, str) or not HEX_ID.fullmatch(recipe_id):
            raise KeyError(recipe_id)
        path = self.dir / 'recipes' / (recipe_id + '.json')
        if not path.is_file():
            raise KeyError(recipe_id)
        return path

    def recipes(self):
        """The published Recipes, oldest first: the stored records (SPEC D.2) plus 'error'.

        'error' is '' or, for a file that no longer matches its SHA-256, the text to show; such a Recipe
        cannot be loaded with recipe() and must not be run.
        """
        rows = []
        for record in self._recipe_files():
            record['error'] = '' if recipe_intact(record) else RECIPE_CHANGED
            rows.append(record)
        rows.sort(key=lambda row: (str(row.get('created_at') or ''), row['name'], row['id']))
        return rows

    def recipe(self, recipe_id):
        """One published Recipe. KeyError: there is none with this id. ValueError: its file was changed."""
        path = self._recipe_path(recipe_id)
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise KeyError(recipe_id) from None
        except (OSError, ValueError):
            raise ValueError(RECIPE_CHANGED) from None
        if not recipe_intact(record):  # checked at every load, not only at start
            raise ValueError(RECIPE_CHANGED)
        record['id'] = recipe_id
        return record

    def publish_recipe(self, name, csv, summary, source):
        """Store a checked Recipe under a new id and return its record. Names are unique (exact match)."""
        record = recipe_record(name, csv, summary, source)
        folder = self.dir / 'recipes'
        with self.recipe_lock:
            if any(row['name'] == record['name'] for row in self._recipe_files()):
                raise ValueError('A recipe with this name already exists.')
            folder.mkdir(parents=True, exist_ok=True)
            self.write_json(folder / (record['id'] + '.json'), record)
            with self.lock:
                self.recipes_rev += 1
        return record

    def put_recipe(self, recipe_id, name, csv, summary, source):
        """Store a checked Recipe under a given id, replacing what is there (the flow of the schedule page
        keeps its id, so a schedule or a calibration plan that names it goes on naming it)."""
        if not HEX_ID.fullmatch(str(recipe_id)):
            raise ValueError('Recipe The number is wrong')
        record = dict(recipe_record(name, csv, summary, source), id=recipe_id)
        folder = self.dir / 'recipes'
        with self.recipe_lock:
            if any(row['name'] == record['name'] and row['id'] != recipe_id for row in self._recipe_files()):
                raise ValueError('A recipe with this name already exists.')
            folder.mkdir(parents=True, exist_ok=True)
            self.write_json(folder / (recipe_id + '.json'), record)
            with self.lock:
                self.recipes_rev += 1
        return record

    def flow(self):
        """The flow of the schedule page as it was saved, or None: {'version': 2, 'flow': {'steps': [rows]}} since
        AMENDMENTS M23, {'version': 1, 'flow': {the four stages of M16}} from before."""
        try:
            saved = json.loads((self.dir / 'flow.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return None
        return saved if isinstance(saved, dict) and isinstance(saved.get('flow'), dict) else None

    def save_flow(self, flow):
        if not isinstance(flow, dict):
            raise ValueError('The content format of the process is incorrect.')
        self.write_json(self.dir / 'flow.json', {'version': 2 if 'steps' in flow else 1, 'flow': flow})

    def delete_recipe(self, recipe_id):
        """Move the Recipe's file to recipes\\deleted (nothing is erased); returns where it is now."""
        with self.recipe_lock:
            path = self._recipe_path(recipe_id)
            folder = self.dir / 'recipes' / 'deleted'
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            target, again = folder / f'{recipe_id}.deleted-{stamp}.json', 1
            while target.exists():
                again += 1
                target = folder / f'{recipe_id}.deleted-{stamp}-{again}.json'
            try:
                path.rename(target)
            except FileNotFoundError:
                raise KeyError(recipe_id) from None
            with self.lock:
                self.recipes_rev += 1
        return target

    # ---- Records of each round of process
    def save_round(self, record):
        """Write rounds\\ROUND-<id>.json. The caller hands over a copy nobody changes any more."""
        round_id = record.get('id') if isinstance(record, dict) else None
        if not isinstance(round_id, str) or not HEX_ID.fullmatch(round_id):
            raise ValueError('Process record number format is incorrect')
        folder = self.dir / 'rounds'
        folder.mkdir(parents=True, exist_ok=True)
        self.write_json(folder / f'ROUND-{round_id}.json', record)

    def round(self, round_id):
        """One round record. KeyError: there is none with this id."""
        if not isinstance(round_id, str) or not HEX_ID.fullmatch(round_id):
            raise KeyError(round_id)
        try:
            record = json.loads((self.dir / 'rounds' / f'ROUND-{round_id}.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise KeyError(round_id) from None
        except ValueError:
            raise ValueError('The record file of this round cannot be read.') from None
        return record

    def newest_round(self):
        """The round record that was written last, or None. The program reads it once, when it starts."""
        folder = self.dir / 'rounds'
        try:
            with os.scandir(folder) as entries:
                files = sorted(((entry.stat().st_mtime_ns, entry.name) for entry in entries
                                if ROUND_FILE.fullmatch(entry.name)), reverse=True)
        except OSError:
            return None
        for _written, name in files:
            try:
                record = json.loads((folder / name).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                return record
        return None

    # ---- execution record
    def append_runlog(self, row):
        """One line of runlog.jsonl: a finished round, or a slot that did not run (SPEC D.2).

        False when the file could not be written (the row is then only in memory until the program ends).
        """
        if not isinstance(row, dict):
            raise ValueError('The execution record format is incorrect')
        return self._runlog.append(dict(row))

    def runlog(self, before=None, limit=50):
        """The newest rows first; ``before`` and "is there more" as for events()."""
        return [dict(row) for row in self._runlog.page(before, limit)]

    @property
    def runlog_rev(self):
        return self._runlog.rev

    # ---- Scheduling record
    def save_schedule(self, record):
        """Write schedule.json. The caller hands over a copy nobody changes any more."""
        self.write_json(self.dir / 'schedule.json', record)

    def load_schedule(self):
        """schedule.json as it is, or None: no file, not readable, or not a schedule record."""
        try:
            record = json.loads((self.dir / 'schedule.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or not isinstance(record.get('slots'), list) or 'spec' not in record:
            return None
        return record

    # ---- spectrum
    def run_ids(self):
        with self.lock:
            return {row['job'] for row in self.run_rows}

    def save_run(self, result, **origin):
        """Store one "result" reply; the small .meta.json keeps the list view fast.

        Keywords (all optional) say where the spectra came from and go into the .meta.json:
        round_id, recipe_name, operator, slot, source ('round' / 'manual' / 'check').
        """
        unknown = set(origin) - set(RUN_EXTRA)
        if unknown:
            raise TypeError('save_run: unknown keyword ' + ', '.join(sorted(unknown)))
        run_id = result['job']
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError('Spectrum number format is incorrect')  # it becomes a file name
        values = result['difference'] if result['kind'] == 'plasma' else result['mean']
        top = max(range(len(values)), key=values.__getitem__)
        meta = {key: result.get(key) for key in ('job', 'kind', 'started_at', 'finished_at', 'voltage_v', 'model',
                                                 'integration_ms', 'pixels', 'signal_frames', 'background_frames',
                                                 'parameters', 'flow')}
        meta.update(saved_at=now_iso(), peak_nm=result['axis'][top], peak_value=values[top],
                    wait_s=(result.get('timing') or {}).get('wait_s'))
        meta.update({key: origin.get(key) for key in RUN_EXTRA})
        record = dict(result, saved_at=meta['saved_at'])
        record.pop('ok', None)
        self.write_json(self.runs / (run_id + '.json'), record, compact=True)
        self.write_json(self.runs / (run_id + '.meta.json'), meta)
        with self.lock:
            self.run_rows = [meta] + [row for row in self.run_rows if row['job'] != run_id]
            self.runs_rev += 1
        return meta

    def list_runs(self, limit=200):
        with self.lock:
            return self.run_rows[:limit]

    def load_run(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise ValueError('Spectrum number format is incorrect')
        return json.loads((self.runs / (run_id + '.json')).read_text(encoding='utf-8'))
