"""Analysis settings, calibrations and measurement records: the `Analyst`.

The one object the rest of the program talks to for Correction, Trend and concentrations
(AMENDMENTS M5.1). It ports what the original HMI's controller did around
spectrum_analysis.py, calibration.py and measurements.py; the analysis itself now runs on
this PC, on the stored spectrum sets that ``load_run(job)`` returns.

Files under the data folder, all written through the injected ``write_json`` (measurements.jsonl
is appended): analysis_config.json, calibration_plan.json, calibrations/CAL-<32 hex>.json,
calibration_config.json, calibration_active.json, blank_qc.json, blank_zero.json.

calibration_active.json is a snapshot of everything a measurement needs from the applied
calibration (reference spectrum, blank levels, method). Re-analysing a record or changing its
exclusions never changes measurements until the record is applied again.

For the callers:
  runner   after a round's plasma_run: measure(job, kind, info). It never raises and announces
           nothing: the events and the blank alarm of AMENDMENTS M5.2 are the runner's, made
           from the record that comes back.
           calibration: check_plan() -> its 'steps' and 'phases' are the round; create();
           add_run() after every plasma_run that carries a 'calibration' tag (never raises);
           finish() at the end (it analyses a completed run before it returns: seconds).
  routes   ValueError = the request is wrong (400), RuntimeError = not now (409), KeyError = no
           such record (404; the text is exc.args[0]). OSError = a settings file could not be written.

Standard library only. Thread-safe: ``_lock`` guards the files and what is cached from them,
``_analysis`` lets one calibration analysis run at a time.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import analysis
import calibration as cal
import measurements as meas

INTERRUPTED = 'program closed during calibration execution or analysis'
BUSY = 'Calibration analysis is in progress, please wait.'
STILL_RUNNING = 'This correction is still in progress'
NOT_FOUND = 'Calibration record does not exist'
NO_BLANK = 'There is no measuring wheel of blank standard, and the reference spectrum cannot be established.'
RECORD_UNSAVED = 'Calibration record failed to update:'
MEASUREMENT_UNSAVED = 'The measurement result failed to be archived:'
FINISH_STATUSES = ('completed', 'stopped', 'failed', 'interrupted')
# finish('completed') waits this long for a re-analysis that happens to run at that moment.
ANALYSIS_WAIT_S = 60.0


def now():
    return datetime.now(timezone.utc).isoformat()


def _text(exc):
    """The message of an exception (str() of a KeyError would add quotes)."""
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or type(exc).__name__


def _file_error(name, exc):
    """Why a settings file cannot be used (English, like the analysis' own error texts)."""
    if isinstance(exc, json.JSONDecodeError):
        return f'{name}: not valid JSON ({exc})'
    if isinstance(exc, UnicodeError):
        return f'{name}: not UTF-8 text'
    if isinstance(exc, OSError):
        return f'{name}: cannot be read or written ({exc.strerror or exc})'
    return f'{name}: {_text(exc)}'


class Analyst:
    UNIT = cal.UNIT
    METALS = meas.METALS
    # The settings a new data folder starts with (copied to <data>/analysis_config.json).
    DEFAULT_SETTINGS = Path(__file__).resolve().parent / 'analysis_default.json'

    def __init__(self, data_dir, load_run, write_json, on_event=None, on_alarm=None):
        """load_run(job) -> the stored spectrum set; write_json(path, value) writes one JSON file;
        on_event(text, level); on_alarm(key, priority, area, text, kind)."""
        self.dir = Path(data_dir)
        self.load_run = load_run
        self.write_json = write_json
        self.on_event = on_event
        # Not used here: the runner raises the blank-check alarm from the record measure() returns.
        self.on_alarm = on_alarm
        self.revs = {'measurements': 0, 'calibrations': 0}  # each grows when its list changes
        self._lock = threading.RLock()
        self._analysis = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._folder = self.dir / 'calibrations'
        self._folder.mkdir(exist_ok=True)
        self._settings_path = self.dir / 'analysis_config.json'
        self._settings_key = None
        self._settings_value = (None, None, '')
        self._coefficients = self._read_coefficients()
        self._qc = self._read_checked('blank_qc.json', meas.validate_qc)
        self._zero = self._read_checked('blank_zero.json', meas.validate_blank_zero)
        self._plan = self._read_plan()
        self._active = self._read_active()
        self.log = meas.Log(self.dir / 'measurements.jsonl')
        self._running = None  # the record of the calibration that runs now (the live object)
        self._index = {}      # record id -> summary
        self._scan_records()
        self._settings()      # creates analysis_config.json in a new data folder

    # ── helpers ────────────────────────────────────────────────────────────
    def _event(self, text, level='info'):
        if self.on_event is not None:
            try:
                self.on_event(text, level)
            except Exception:
                pass

    def _bump(self, *names):
        for name in names:
            self.revs[name] += 1

    def _read_json(self, name):
        return json.loads((self.dir / name).read_text(encoding='utf-8'))

    def _read_checked(self, name, validate):
        try:
            return validate(self._read_json(name))
        except (OSError, ValueError):
            return validate({})

    def _read_coefficients(self):
        path = self.dir / 'calibration_config.json'
        if not path.exists():
            return copy.deepcopy(cal.DEFAULT_COEFFICIENTS)
        try:
            return cal.validate_calibration_config(self._read_json(path.name))
        except (OSError, ValueError) as exc:
            # Unknown coefficients give no concentration at all, which is safer than a wrong one.
            self._event('correction coefficient file calibration_config.json cannot be read, and the concentration is not converted temporarily:' + _text(exc), 'error')
            return {metal: {key: None for key in cal.COEFFICIENTS} for metal in cal.METALS}

    def _read_plan(self):
        try:
            plan = self._read_json('calibration_plan.json')
            if isinstance(plan, dict):
                return plan
        except (OSError, ValueError):
            pass
        return copy.deepcopy(cal.DEFAULT_PLAN)

    def _read_active(self):
        try:
            active = self._read_json('calibration_active.json')
        except (OSError, ValueError):
            return None
        if (not isinstance(active, dict) or not isinstance(active.get('calibration_id'), str)
                or not isinstance(active.get('applied_at'), str)):
            return None
        return active

    def _scan_records(self):
        for path in self._folder.glob('CAL-*.json'):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                if not cal.RECORD_ID.fullmatch(path.stem) or record.get('id') != path.stem:
                    continue
                summary = cal.summary(record)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
            if record.get('status') in ('running', 'analyzing'):
                # The program stopped during the run or the analysis: keep the rounds already measured.
                record.update({'status': 'interrupted', 'error': INTERRUPTED})
                summary = cal.summary(record)
                try:
                    self.write_json(path, record)
                except Exception as exc:
                    self._event(RECORD_UNSAVED + _text(exc), 'error')
            self._index[record['id']] = summary

    # ── analysis settings ──────────────────────────────────────────────────
    def settings_view(self):
        """{'method_id', 'config', 'error', 'path'} of analysis_config.json ('error' is '' when it loads)."""
        config, method, error = self._settings()
        return {'method_id': method, 'config': copy.deepcopy(config), 'error': error,
                'path': str(self._settings_path)}

    def _settings(self):
        """(config, method_id, error); the file is read again whenever it has changed."""
        with self._lock:
            path, key = self._settings_path, None
            try:
                key = self._file_key(path)
                if key is None:
                    self._create_settings()
                    key = self._file_key(path)
                if key is not None and key == self._settings_key:
                    return self._settings_value
                config = analysis.validate_config(json.loads(path.read_text(encoding='utf-8-sig')))
                value = (config, analysis.method_id(config), '')
            except _SettingsError as exc:
                value = (None, None, str(exc))
            except Exception as exc:  # whatever is wrong with the file, the program keeps running
                value = (None, None, _file_error(path.name, exc))
            if key is not None and self._settings_key is not None and key != self._settings_key:
                self._bump('measurements', 'calibrations')  # the pages show the method and its error
            self._settings_key, self._settings_value = key, value
            return value

    @staticmethod
    def _file_key(path):
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _create_settings(self):
        source = self.DEFAULT_SETTINGS
        try:
            raw = json.loads(source.read_text(encoding='utf-8-sig'))
            analysis.validate_config(raw)
        except (OSError, ValueError) as exc:
            raise _SettingsError(_file_error(source.name, exc)) from exc
        self.write_json(self._settings_path, raw)

    # ── coefficients, blank thresholds, blank zero ─────────────────────────
    def coefficients(self):
        with self._lock:
            return copy.deepcopy(self._coefficients)

    def set_coefficients(self, raw):
        config = cal.validate_calibration_config(raw)
        with self._lock:
            self.write_json(self.dir / 'calibration_config.json', config)
            self._coefficients = config
            self._bump('measurements', 'calibrations')
            return copy.deepcopy(config)

    def qc(self):
        with self._lock:
            return copy.deepcopy(self._qc)

    def set_qc(self, raw):
        qc = meas.validate_qc(raw)
        with self._lock:
            self.write_json(self.dir / 'blank_qc.json', qc)
            self._qc = qc
            self._bump('measurements')
            return copy.deepcopy(qc)

    def blank_zero(self):
        """{'settings', 'status'}; status is None while no calibration is applied."""
        with self._lock:
            active, status = self._active, None
            if active:
                status = meas.zero_status(self._zero_blanks(active['calibration_id'], active.get('method_id')),
                                          self._zero)
            return {'settings': copy.deepcopy(self._zero), 'status': status}

    def set_blank_zero(self, raw):
        settings = meas.validate_blank_zero(raw)
        with self._lock:
            self.write_json(self.dir / 'blank_zero.json', settings)
            self._zero = settings
            self._bump('measurements')
            return self.blank_zero()

    def _zero_blanks(self, calibration_id, method_id):
        return [item for item in self.log.rows()
                if item.get('kind') == 'blank' and item.get('calibration_id') == calibration_id
                and item.get('method_id') == method_id]

    # ── calibration plan ───────────────────────────────────────────────────
    def plan(self):
        with self._lock:
            return copy.deepcopy(self._plan)

    def save_plan(self, raw):
        """Store the draft as entered (incomplete rows allowed) so the table survives a restart."""
        try:
            size = len(json.dumps(raw))
        except (TypeError, ValueError):
            size = None
        if not isinstance(raw, dict) or size is None or size > 20_000:
            raise ValueError('Calibration plan format is incorrect')
        with self._lock:
            self.write_json(self.dir / 'calibration_plan.json', raw)
            self._plan = copy.deepcopy(raw)
        try:
            plan = cal.validate_plan(raw)
            return {'saved': True, 'valid': True, 'plan': plan, 'warnings': cal.plan_warnings(plan)}
        except ValueError as exc:
            return {'saved': True, 'valid': False, 'error': str(exc), 'warnings': []}

    @staticmethod
    def _recipe_check(recipe, recipe_id):
        if (not isinstance(recipe, dict) or recipe.get('id') != recipe_id
                or not isinstance(recipe.get('validated'), dict)):
            raise ValueError('Recipe does not exist')
        check = recipe['validated']
        if check.get('valid') is False or not isinstance(check.get('steps'), list):
            raise ValueError(str(check.get('error') or 'Recipe failed the inspection'))
        return check

    @staticmethod
    def _estimate(recipe, check):
        """Seconds of one run of a Recipe: its estimate when the caller has one, else its delays."""
        value = recipe.get('estimate_seconds')
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            return float(value)
        return float(check.get('delay_seconds') or 0)

    def check_plan(self, raw, recipe, prime):
        """Validate a plan against its two Recipes; ValueError with the reason.

        recipe / prime: {'id', 'name', 'sha256', 'validated': recipe.validate() result,
        'estimate_seconds'} or None (not chosen, or not found). 'steps' / 'phases' of the reply
        are what the runner executes as one round.
        """
        plan = cal.validate_plan(raw)
        if not plan['recipe_id']:
            raise ValueError('Please select the standard product Recipe')
        check = self._recipe_check(recipe, plan['recipe_id'])
        prime_check = self._recipe_check(prime, plan['prime_recipe_id']) if plan['prime_recipe_id'] else None
        sequence = cal.build_sequence(plan, check['steps'], prime_check['steps'] if prime_check else None,
                                      check.get('phases'))
        levels = len(plan['levels'])
        runs = sum(level['rounds'] + plan['discard_rounds'] for level in plan['levels'])
        prime_seconds = float(prime_check.get('delay_seconds') or 0) if prime_check else 0.0
        estimate = self._estimate(recipe, check) * runs
        if prime_check:
            estimate += self._estimate(prime, prime_check) * levels
        return {'plan': plan, 'warnings': cal.plan_warnings(plan), 'recipe_name': recipe.get('name'),
                'prime_recipe_name': prime.get('name') if prime_check else None,
                'prime_delay_seconds': prime_seconds, 'runs': runs,
                'kept_runs': sum(level['rounds'] for level in plan['levels']),
                'round_delay_seconds': check.get('delay_seconds', 0), 'estimated_seconds': round(estimate),
                'steps': sequence['steps'], 'phases': sequence['phases']}

    # ── calibration records ────────────────────────────────────────────────
    def _load_record(self, cal_id):
        """The stored record (for the running calibration: the live object). Caller holds _lock."""
        if not isinstance(cal_id, str) or not cal.RECORD_ID.fullmatch(cal_id):
            raise ValueError('calibration record ID format is incorrect')
        if self._running is not None and self._running['id'] == cal_id:
            return self._running
        try:
            record = json.loads((self._folder / (cal_id + '.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise KeyError(NOT_FOUND) from exc
        if not isinstance(record, dict) or record.get('id') != cal_id:
            raise KeyError(NOT_FOUND)
        return record

    def _store(self, record):
        """Write one record; its summary and the rev follow even when the write fails. Caller holds _lock."""
        try:
            self.write_json(self._folder / (record['id'] + '.json'), record)
        finally:
            self._index[record['id']] = cal.summary(record)
            self._bump('calibrations')

    def _store_quietly(self, record):
        with self._lock:
            try:
                self._store(record)
                return True
            except Exception as exc:
                self._event(RECORD_UNSAVED + _text(exc), 'error')
                return False

    def create(self, plan, recipe, prime, operator, schedule_id):
        """A new record with status 'running' for the calibration that starts now."""
        plan = cal.validate_plan(plan)
        if not isinstance(recipe, dict):
            raise ValueError('Recipe does not exist')
        keys = ('id', 'name', 'sha256')
        record = {'id': 'CAL-' + uuid.uuid4().hex, 'created_at': now(), 'status': 'running', 'operator': operator,
                  'plan': plan, 'recipe': {key: recipe.get(key) for key in keys},
                  'prime_recipe': {key: prime.get(key) for key in keys} if prime and plan['prime_recipe_id'] else None,
                  'warnings': cal.plan_warnings(plan), 'runs': [], 'schedule_id': schedule_id}
        with self._lock:
            stale = self._running
            if stale is not None:  # its finish() never came
                stale.update({'status': 'interrupted', 'error': INTERRUPTED})
                self._running = None
                self._store_quietly(stale)
            try:
                self._store(record)
            except Exception as exc:
                self._index.pop(record['id'], None)
                raise RuntimeError(RECORD_UNSAVED + _text(exc)) from exc
            self._running = record
            return copy.deepcopy(record)

    def add_run(self, cal_id, info):
        """One finished discharge of the running calibration:
        info = {level, level_name, run, discard, round, job, finished_at}.
        Never raises: the spectra are stored already, a failed bookkeeping write must not end the run.
        """
        try:
            with self._lock:
                record = self._running
                if record is None or record['id'] != cal_id:
                    raise KeyError(NOT_FOUND)
                level, job = info['level'], info['job']
                if (isinstance(level, bool) or not isinstance(level, int)
                        or not 0 <= level < len(record['plan']['levels']) or not isinstance(job, str)):
                    raise ValueError('The calibration round data is incorrect')
                record['runs'].append({'level': level, 'level_name': info.get('level_name'), 'run': info.get('run'),
                                       'discard': bool(info.get('discard')), 'round': info.get('round'), 'job': job,
                                       'finished_at': info.get('finished_at') or now()})
                self._store(record)
            return True
        except Exception as exc:
            self._event(RECORD_UNSAVED + _text(exc), 'error')
            return False

    def finish(self, cal_id, status, error=''):
        """End of the calibration's round. 'completed' is analysed here (the record comes back
        'analyzed' or 'analysis_failed'); 'stopped' / 'failed' keep the rounds already measured."""
        if status not in FINISH_STATUSES:
            raise ValueError('The calibration status is incorrect')
        with self._lock:
            record = self._load_record(cal_id)
            if record.get('status') != 'running':
                return copy.deepcopy(record)
            record['finished_at'] = now()
            record['status'] = 'analyzing' if status == 'completed' else status
            if error:
                record['error'] = str(error)[:400]
            self._running = None
            self._store_quietly(record)
            # Taken before _lock is let go, so nothing can change the record in between.
            held = status == 'completed' and self._analysis.acquire(blocking=False)
        if status == 'completed':
            if held or self._analysis.acquire(timeout=ANALYSIS_WAIT_S):
                try:
                    self._run_analysis(record)
                finally:
                    self._analysis.release()
            else:
                record.update({'status': 'analysis_failed', 'error': BUSY})
                self._store_quietly(record)
        return copy.deepcopy(record)

    def _begin_analysis(self, cal_id):
        """Checks of a re-analysis; returns the record marked 'analyzing' with _analysis held."""
        with self._lock:
            record = self._load_record(cal_id)
            if record is self._running or record.get('status') == 'running':
                raise RuntimeError(STILL_RUNNING)
            if not any(run['level'] == 0 and not run['discard'] for run in record['runs']):
                raise ValueError(NO_BLANK)
            if not self._analysis.acquire(blocking=False):
                raise RuntimeError(BUSY)
            record.update(status='analyzing', error=None)
            self._store_quietly(record)
            return record

    def analyze(self, cal_id):
        """Reanalysis: rebuild the blank reference and every intensity from the stored spectra, then
        regress. Returns the record ('analyzed' or 'analysis_failed'). RuntimeError when an analysis
        runs already or the calibration is still running; ValueError without kept blank rounds."""
        record = self._begin_analysis(cal_id)
        try:
            return copy.deepcopy(self._run_analysis(record))
        finally:
            self._analysis.release()

    def request_analysis(self, cal_id):
        """The same checks as analyze(), then the analysis in a thread 'cal-analysis'; the record
        goes 'analyzing' -> 'analyzed' / 'analysis_failed'."""
        record = self._begin_analysis(cal_id)

        def work():
            try:
                self._run_analysis(record)
            finally:
                self._analysis.release()
        threading.Thread(target=work, name='cal-analysis', daemon=True).start()
        return {'accepted': True, 'id': cal_id}

    def _spectrum_row(self, job, reference, config, spectra=None):
        """The analysis row of one stored run; a run that cannot be read gives an invalid row."""
        spectrum = self._spectrum(job, config, spectra)
        if isinstance(spectrum, str):
            return {'job': job, 'valid': False, 'error': spectrum}
        return {'job': job, **analysis.analyze_one(spectrum, reference, config)}

    def _spectrum(self, job, config, spectra=None):
        """spectrum_of() the stored run, or the reason (a text) why it cannot be used."""
        if spectra is not None and job in spectra:
            return spectra[job]
        try:
            result = analysis.spectrum_of(self.load_run(job), config)
        except OSError as exc:
            result = f'cannot read the spectrum set {job}: {exc.strerror or exc}'[:300]
        except ValueError as exc:
            result = _text(exc)[:300]
        if spectra is not None:
            spectra[job] = result
        return result

    def _run_analysis(self, record):
        """Reference from the kept blank rounds, the intensities of every kept round, the
        regression. Changes and stores the record; never raises. Caller holds _analysis."""
        try:
            kept = [run for run in record['runs'] if not run['discard']]
            blanks = [run['job'] for run in kept if run['level'] == 0]
            if not blanks:
                raise ValueError(NO_BLANK)
            config, method, error = self._settings()
            if error:
                raise RuntimeError(error)
            spectra, named, rows = {}, [], []
            for job in blanks:
                spectrum = self._spectrum(job, config, spectra)
                if isinstance(spectrum, str):
                    rows.append({'job': job, 'used': False, 'error': spectrum})
                else:
                    named.append((job, spectrum))
            reference = analysis.build_reference(named, config)
            base = {'axis': reference['axis'], 'values': reference['values']}
            jobs = [self._spectrum_row(run['job'], base, config, spectra) for run in kept]
            fits = cal.regress(record['plan'], record['runs'], jobs, record.get('exclude_levels'))
            result = {'status': 'analyzed', 'error': None,
                      'reference': dict(base, blank_count=reference['blank_count'], jobs=rows + reference['jobs']),
                      'analysis': {'analyzed_at': now(), 'method_id': method, 'config': copy.deepcopy(config),
                                   'config_path': str(self._settings_path), 'jobs': jobs, 'fits': fits}}
            json.dumps(result, allow_nan=False)  # a number no file can hold fails the analysis, not the record
            record.update(result)
        except Exception as exc:
            record.update({'status': 'analysis_failed', 'error': _text(exc)[:400]})
        self._store_quietly(record)
        return record

    def set_exclusions(self, cal_id, exclude):
        """Leave standards out of one metal's fit and regress again from the stored intensities."""
        with self._lock:
            record = self._load_record(cal_id)
            clean = cal.validate_exclusions(record['plan'], exclude)
            if self._analysis.locked():
                raise RuntimeError(BUSY)
            record['exclude_levels'] = clean
            result = record.get('analysis')
            if result and record.get('status') == 'analyzed':
                result['fits'] = cal.regress(record['plan'], record['runs'], result['jobs'], clean)
            try:
                self._store(record)
            except Exception as exc:
                raise RuntimeError(RECORD_UNSAVED + _text(exc)) from exc
            return copy.deepcopy(record)

    def apply(self, cal_id):
        """Use the fitted a and b; c returns to 1. Metals without a valid fit keep their values.
        Also takes the snapshot (reference spectrum, blank levels, method) measurements use from now on."""
        with self._lock:
            record = self._load_record(cal_id)
            if record.get('status') != 'analyzed':
                raise RuntimeError('Calibration analysis has not been completed yet')
            result = record['analysis']
            fits = result['fits']
            applied = [metal for metal, fit in fits.items() if fit.get('valid')]
            if not applied:
                raise ValueError('No metal has made a valid return')
            before = copy.deepcopy(self._coefficients)
            config = copy.deepcopy(self._coefficients)
            for metal in applied:
                config[metal] = {'a': format(fits[metal]['a'], '.10g'), 'b': format(fits[metal]['b'], '.10g'),
                                 'c': '1'}
            config = cal.validate_calibration_config(config)
            reference = record.get('reference') or {}
            blank_jobs = {run['job'] for run in record['runs'] if run['level'] == 0 and not run['discard']}
            blank_norm = {}
            for metal in cal.METALS:
                areas = [row['metals'][metal]['norm_area'] for row in result.get('jobs', [])
                         if row.get('job') in blank_jobs and row.get('valid')
                         and (row.get('metals') or {}).get(metal, {}).get('norm_area')]
                if areas:
                    blank_norm[metal] = sum(areas) / len(areas)
            active = {'calibration_id': cal_id, 'applied_at': now(), 'metals': applied,
                      'method_id': result['method_id'], 'unit': cal.UNIT, 'previous': before,
                      'reference': {'axis': list(reference['axis']), 'values': list(reference['values'])}
                      if reference.get('axis') else None,
                      'blank_norm': blank_norm}
            try:
                self.write_json(self.dir / 'calibration_config.json', config)
                try:
                    self.write_json(self.dir / 'calibration_active.json', active)
                except Exception:
                    try:  # keep the two files a pair: the old coefficients go back
                        self.write_json(self.dir / 'calibration_config.json', before)
                    except Exception:
                        pass
                    raise
            except Exception as exc:
                raise RuntimeError(RECORD_UNSAVED + _text(exc)) from exc
            self._coefficients, self._active = config, active
            record.update(applied_at=active['applied_at'], applied_metals=applied, previous_config=before)
            self._store_quietly(record)
            self._bump('measurements', 'calibrations')
            return {'applied': list(applied), 'config': copy.deepcopy(config), 'active': self._active_view()}

    def records(self, limit=50):
        """Summaries, newest first."""
        with self._lock:
            rows = sorted(self._index.values(), key=lambda row: row.get('created_at') or '', reverse=True)
            return copy.deepcopy(rows[:limit])

    def record(self, cal_id):
        """The full record. KeyError(text) when there is none; ValueError for a malformed id."""
        with self._lock:
            return copy.deepcopy(self._load_record(cal_id))

    def _active_view(self):
        if not self._active:
            return None
        return {key: copy.deepcopy(value) for key, value in self._active.items() if key != 'reference'}

    def active(self):
        """The applied calibration without its reference spectrum, or None."""
        with self._lock:
            return self._active_view()

    def running(self):
        """Summary of the calibration that is running now, or None."""
        with self._lock:
            return cal.summary(self._running) if self._running is not None else None

    # ── measurements ───────────────────────────────────────────────────────
    def _context(self):
        """What a measurement is evaluated against: the snapshot of the applied calibration, or None."""
        active = self._active
        if not active:
            return None
        reference = active.get('reference')
        return {'calibration_id': active['calibration_id'], 'applied_at': active['applied_at'],
                'method_id': active.get('method_id'), 'metals': active.get('metals') or [],
                'blank_norm': active.get('blank_norm') or {},
                'reference': reference if isinstance(reference, dict) and reference.get('axis') else None}

    def measure(self, job, kind, info=None):
        """Analyze the stored spectrum set of one plasma_run: a sample gets concentrations, a blank
        its baseline check. info = {round_id, schedule_id, slot, recipe_name, operator}. Appends the
        record to measurements.jsonl and returns it. Never raises; a failed analysis gives a record
        with conc_reason 'Analysis failed'."""
        kind = kind if kind in meas.KINDS else 'sample'
        # The record's own id and time are given when it is stored.
        info = {key: value for key, value in info.items() if key not in ('id', 'time')} if isinstance(info, dict) else {}
        context = None
        try:
            with self._lock:
                context = self._context()
                # These objects are replaced, never changed in place.
                coefficients, qc, zero = self._coefficients, self._qc, self._zero
            config, method, error = self._settings()
            if error:
                raise RuntimeError(error)
            row = self._spectrum_row(job, context['reference'] if context else None, config)
            record = meas.evaluate(kind, row, method, context, coefficients, qc)
            if kind == 'sample' and context and record['valid']:
                meas.apply_zero(record, meas.zero_status(
                    self._zero_blanks(context['calibration_id'], record['method_id']), zero))
            json.dumps(record, allow_nan=False)  # a number the log cannot hold counts as a failed analysis
        except Exception as exc:
            record = meas.failed(kind, job, _text(exc), context)
        try:
            stored = self.log.add({**info, **record})
        except Exception as exc:  # the disk, or a value in info that JSON cannot hold
            self._event(MEASUREMENT_UNSAVED + _text(exc), 'error')
            return {'id': uuid.uuid4().hex, 'time': now(), **copy.deepcopy(record)}
        with self._lock:
            self._bump('measurements')
        return copy.deepcopy(stored)

    def measurements(self, hours=None):
        """Data of the trend page; hours = None gives everything kept in memory.
        The rows are the stored records themselves: read them, do not change them."""
        cutoff = None
        if hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._lock:
            active = self._active
            return {'rows': self.log.since(cutoff), 'qc': copy.deepcopy(self._qc),
                    'calibration': {key: copy.deepcopy(active.get(key))
                                    for key in ('calibration_id', 'applied_at', 'metals')} if active else None,
                    'coefficients': copy.deepcopy(self._coefficients),
                    'blank_zero': self.blank_zero(), 'unit': cal.UNIT}

    def latest(self):
        return {'latest_sample': copy.deepcopy(self.log.latest('sample')),
                'latest_blank': copy.deepcopy(self.log.latest('blank'))}


class _SettingsError(Exception):
    """A settings problem whose text names the file already."""
