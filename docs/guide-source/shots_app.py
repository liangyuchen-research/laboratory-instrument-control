"""Screenshot for reshooting instructions (2.0.0) : Go through the entire process in practice mode, and then use Edge Take pictures of every page.

    .\\pc\\runtime\\python\\python.exe -B .\\docs\\guide-source\\shots_app.py

are all simulated: for program use --demo Start (RPi, high voltage, spectrometer, and pump control board are all simulators), and set PLASMIC_NO_SERIAL=1,
will not open any ports, nor will it connect to the real You cannot stop one of the RPi.The data is placed in the temporary folder and will be deleted after shooting; it cannot be read or written. pc\\data.
It is also a ”from beginning to end” inspection: select the operator → login maintenance (practice mode PIN) → storage parameters → Calibration for three concentrations
 (Confirm fluid replacement for each concentration)→ Apply → several measurements → The process of writing the steps by yourself in ”Scheduled Measurement” (adding liquid to two pumps) → Move to another slot → Plasma → Drain) →
Manually start a pump → Stop → Calibrate a pump (pump There is no 10 mL, input 9.5 mL, Clear again) → “What is the pump connected to? (Put water and OUT Change to another one and set one
solenoid valve linkage, the name should follow, the process should be listed in ”Please check”, and then change it back). If there is a wrong step in the middle, stop and explain.

screenshots are saved in the same folder shots\\ (1280×720: The shape of the touch screen on the machine). URL band ?fs=0,”Click to start full screen” does not appear.
need Microsoft Edge.approx. 3 minute.
Use after shooting build_guide.py Rebuild instructions.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / 'shots'
PYTHON = str(ROOT / 'pc' / 'runtime' / 'python' / 'python.exe')
PORT, SIM = 8794, 8774
BASE = f'http://127.0.0.1:{PORT}'
PICTURES = ('dash', 'schedule', 'manual', 'runs', 'calibration-run', 'trend', 'maint')   # build_guide.py Seven used
state = {'csrf': '', 'unlock': '', 'profile': None}


def edge():
    for folder in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):
        path = Path(folder or '') / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
        if folder and path.is_file():
            return str(path)
    raise SystemExit('not found Microsoft Edge (msedge.exe) ')


def call(method, path, body=None, timeout=60):
    headers = {'Origin': BASE}
    data = None
    if method == 'POST':
        data = json.dumps(body or {}).encode('utf-8')
        headers.update({'Content-Type': 'application/json', 'X-Token': state['csrf']})
        if state['unlock']:
            headers['X-Unlock'] = state['unlock']
    request = urllib.request.Request(BASE + path, data, headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit('%s %s -> %d %s' % (method, path, error.code, error.read().decode('utf-8', 'replace')[:400]))


def get(path):
    return call('GET', path)


def post(path, body=None):
    return call('POST', path, body)


def wait(test, seconds, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        now = get('/api/state')
        if test(now):
            return now
        time.sleep(.4)
    raise SystemExit('Can’t wait:' + what)


def shots(pairs):
    """Pictures taken at the same moment: [(name, anchor), ...]. And they are really this run's: Edge may hand the
    work to a process of its own and return at once (2026-10-07: three of seven pictures were silently the old ones,
    and one showed a later moment). So the old file is moved away first, every picture gets a browser profile of its
    own (a second start with the same profile is only passed on to the first one, which then takes no picture), and
    each new file is waited for until it stops growing."""
    waiting = []
    for name, anchor in pairs:
        target = OUT / f'guide-{name}.png'
        kept = OUT / f'guide-{name}.previous.png'
        if target.is_file():
            target.replace(kept)
        profile = Path(str(state['profile']) + '-' + name)
        process = subprocess.Popen([edge(), '--headless=new', '--disable-gpu', '--hide-scrollbars', '--window-size=1280,720',
                                    '--force-device-scale-factor=1', f'--user-data-dir={profile}', '--virtual-time-budget=6000',
                                    f'--screenshot={target}', f'{BASE}/?fs=0#{anchor}'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        waiting.append((target, kept, process))
    for target, kept, process in waiting:
        try:
            process.wait(120)
        except subprocess.TimeoutExpired:
            process.kill()
        deadline, size, steady = time.monotonic() + 60, -1, 0
        while time.monotonic() < deadline and steady < 3:
            time.sleep(.3)
            now = target.stat().st_size if target.is_file() else -1
            steady = steady + 1 if now > 0 and now == size else 0
            size = now
        if steady < 3:
            if kept.is_file():
                kept.replace(target)             # the picture of the last run stays, and this run says that it failed
            raise SystemExit(f'not photographed {target.name}')
        if kept.is_file():
            kept.unlink()
        print('screenshot', target.name, size, flush=True)


def shot(name, anchor):
    shots([(name, anchor)])


def idle(now):
    return (now.get('schedule') or {}).get('status') not in ('waiting', 'running', 'pausing', 'paused', 'stopping') and not now.get('owner')


def ended(now):
    return bool(now.get('schedule')) and now['schedule']['status'] in ('completed', 'failed', 'stopped')


def one_measurement(recipe_id=None, liquid=False):
    if recipe_id is None:
        recipe_id = next(row for row in get('/api/recipes')['recipes'] if row['name'] == 'Only discharge measurement')['id']
    body = {'recipe_id': recipe_id, 'schedule': {'start': {'mode': 'now'}, 'interval_minutes': 10, 'end': {'mode': 'count', 'count': 1}}}
    preview = post('/api/schedule/preview', body)
    if preview['blockers']:
        raise SystemExit('The schedule cannot be started:%r' % (preview['blockers'],))
    post('/api/schedule/start', dict(body, confirm={'high_voltage': True, 'liquid': liquid, 'hv_off_checked': False}))


def parameters(**change):
    values = dict({'Ontime': 25, 'Offtime': 5, 'Cycle': 2, 'Loopnum': 2, 'Integration_Time': 100, 'Spectra_Interval': 200, 'Voltage': 400}, **change)
    reply = post('/api/parameters', {'parameters': values})
    if not reply.get('saved'):
        raise SystemExit('parameters are not saved:%r' % (reply,))
    wait(lambda s: (s['rpi']['parameters'] or {}).get('nano_synced') is True, 60, 'Nano received parameters')


def walk():
    for _ in range(100):
        try:
            boot = get('/api/bootstrap')
            break
        except (urllib.error.URLError, ConnectionError, SystemExit):
            time.sleep(.3)
    else:
        raise SystemExit('Practice mode is not started')
    if boot['demo'] is not True:
        raise SystemExit('This is not practice mode, stop')
    state['csrf'] = boot['csrf']
    now = wait(lambda s: s['rpi']['connected'] and s['pump']['link'] == 'connected', 60, 'Two simulators are connected')
    print('Practice mode: Program', now['version'], '· RPi simulator', now['rpi']['server'].get('version'), '· pump', now['pump']['text'], flush=True)
    post('/api/operator', {'name': now['operators'][0]})
    state['unlock'] = post('/api/unlock', {'pin': boot['practice_pin']})['session']
    parameters()

    # Calibration: Blank＋Two concentrations, two rounds each
    recipe = next(row for row in get('/api/recipes')['recipes'] if row['name'] == 'Only discharge measurement')
    plan = {'recipe_id': recipe['id'], 'prime_recipe_id': '', 'discard_rounds': 0, 'levels': [
        {'name': 'Blank', 'conc': {'Cu': 0, 'Zn': 0, 'Ni': 0}, 'rounds': 2},
        {'name': 'STD-1', 'conc': {'Cu': .3, 'Zn': .3, 'Ni': .3}, 'rounds': 2},
        {'name': 'STD-2', 'conc': {'Cu': .8, 'Zn': .8, 'Ni': .8}, 'rounds': 2}]}
    post('/api/calibration/check', {'plan': plan})
    calibration_id = post('/api/calibration/start', {'plan': plan, 'confirm': {'high_voltage': True, 'liquid': False, 'hv_off_checked': False}})['calibration_id']
    prompts, pictured, record = 0, False, {}
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        run = get('/api/state').get('calibration_run')
        if run and run.get('prompt'):
            if not pictured:
                shot('calibration-run', 'calibration')
                pictured = True
            post('/api/calibration/confirm', {})
            prompts += 1
            time.sleep(.6)
            continue
        record = get('/api/calibrations/' + calibration_id)
        if record['status'] in ('analyzed', 'analysis_failed', 'failed', 'stopped', 'interrupted'):
            break
        time.sleep(.5)
    fits = {metal: (fit.get('valid'), None if fit.get('r2') is None else round(fit['r2'], 4))
            for metal, fit in (record.get('analysis') or {}).get('fits', {}).items()}
    print('Correction:', record.get('status'), '· Confirm fluid change', prompts, 'times · Return', fits, flush=True)
    if record.get('status') != 'analyzed' or prompts != 3:
        raise SystemExit('Calibration did not complete as expected')
    wait(idle, 60, 'Calibration release equipment')
    print('Apply:', post('/api/calibration/apply', {'id': calibration_id})['active'].get('metals'), flush=True)

    # Several sample measurements (for trend and execution records), another relatively long measurement, and an overview shot halfway through the measurement
    for _ in range(5):
        one_measurement()
        wait(ended, 90, 'One measurement ends')
        wait(idle, 30, 'Device idle')
    latest = get('/api/state')['measurement']['latest_sample']
    print('Measure 5 times; most recent:', {metal: (None if item.get('conc') is None else round(item['conc'], 3))
                               for metal, item in (latest or {}).get('metals', {}).items()}, flush=True)
    shot('trend', 'trend')

    # “Scheduling Measurement” process of writing steps by yourself (M23) : Two pumps for filling → Move to another slot → Plasma → Drained. Take a photo of the overview and schedule page when the measurement is halfway through
    saved = post('/api/flow', {'steps': [['dose_volume', 'P1', '5', 'mL', ''], ['dose_volume', 'P8', '2', 'mL', ''],
                                         ['dose_volume', 'P3', '7', 'mL', 'moved to plasma tank'], ['plasma_run', '', '', '', ''],
                                         ['dose_volume', 'P7', '10', 'mL', 'eliminate']]})
    print('Process:', saved['name'], '·', [row['text'] for row in saved['view']], flush=True)
    if not saved['valid']:
        raise SystemExit('The steps I wrote did not pass the check:%s' % saved['error'])
    flow_id = saved['recipe_id']
    parameters(Cycle=25, Loopnum=8, Integration_Time=1000, Spectra_Interval=1500, Voltage=300)
    one_measurement(saved['recipe_id'], liquid=True)
    wait(lambda s: (s.get('round') or {}).get('status') == 'running'
         and ((s['rpi'].get('status') or {}).get('job') or {}).get('state') == 'running', 120, 'The liquid addition is completed and discharge begins.')
    time.sleep(7)
    shots([('dash', 'dash'), ('schedule', 'schedule')])      # the same moment of the discharge on both pages
    final = wait(ended, 180, 'That round is over')
    steps = [step['status'] for step in ((get('/api/state').get('round') or {}).get('steps_window') or {}).get('items') or []]
    print('process round:', final['schedule']['status'], '· Each step', steps, flush=True)
    if final['schedule']['status'] != 'completed':
        raise SystemExit('The process you set has not been completed.')
    wait(idle, 30, 'Device idle')

    # Manually start a pump (simulator), then press to stop
    print('Manual start: ', post('/api/manual', {'op': 'pump_on', 'motor': 8, 'flow_ml_min': 40, 'direction': 1, 'seconds': 30}), flush=True)
    time.sleep(2.5)
    shot('manual', 'manual')
    stopped = post('/api/stop', {})
    print('Stop STOP: pump', (stopped.get('pump') or {}).get('result'), '· high pressure', (stopped.get('rpi') or {}).get('result'), flush=True)
    if (stopped.get('pump') or {}).get('result') != 'confirmed':
        raise SystemExit('Stop not confirmed by pump')
    shot('runs', 'runs')
    shot('maint', 'maint')

    # Pump calibration: hit 10 mL (simulator), saying that actually only 9.5 mL,The number of steps should be changed accordingly; then clear The object of the
    wait(idle, 30, 'Device idle')
    post('/api/manual', {'op': 'dose_volume', 'motor': 2, 'volume_ml': 10, 'direction': 1})
    wait(lambda s: not s.get('owner') and not any(motor.get('state') not in (None, 'off') for motor in s['pump'].get('motors') or []),
         60, '10 mL Finished')
    after = post('/api/pump/calibrate', {'motor': 2, 'measured_ml': 9.5})
    print('Pump calibration: every mL %.1f → %.1f step (%+.1f%%) ' % (after['before'], after['dose_pulses_per_ml'], after['change_percent']), flush=True)
    if not 5.0 < after['change_percent'] < 5.6:
        raise SystemExit('The number calculated by the pump calibration is incorrect.')
    back = post('/api/pump/calibrate/reset', {'motor': 2})
    if abs(back['dose_pulses_per_ml'] - after['before']) > 1e-6:
        raise SystemExit('Clearing pump calibration does not return to original numbers')

    # What is connected to the pump: water is changed to Pump 2, OUT Change to Pump 6, solenoid valve 1 follow Pump 2.name and two for cleaning should be followed; the process number of the pump remains unchanged.
    # But it should be listed in ”Please check”; then change it back
    before = get('/api/pump/setup')
    if not before['editable']:
        raise SystemExit('The pump control panel in practice mode should be able to change ”what is connected to the pump”:%s' % before['reason'])
    change = {2: ('none', ''), 3: ('water', ''), 8: ('none', ''), 7: ('out', '')}
    moved = [dict(motor=row['motor'], use=change.get(row['motor'], (row['use'], row['name']))[0],
                  name=change.get(row['motor'], (row['use'], row['name']))[1]) for row in before['pumps']]
    saved = post('/api/pump/setup', {'pumps': moved, 'valves': [{'id': 1, 'motor': 3}, {'id': 2, 'motor': None}]})
    print('What to connect the pump to:', saved['summary'], '· Process', saved['flow'], flush=True)
    hardware = get('/api/bootstrap')['hardware']
    labels = {motor['id']: motor['label'] for group in hardware['groups'] for motor in group['motors']}
    drain = [step['text'] for step in get('/api/recipes/' + flow_id)['steps']][-1]      # Pump 7 no longer OUT,Still the same Pump 7
    if (labels[3], labels[7], labels[2]) != ('Pump 2 · water', 'Pump 6 · OUT', 'Pump 1') or saved['flow'] != 'kept'             or (hardware['cleaning']['water_motor'], hardware['cleaning']['out_motor']) != (3, 7)             or hardware['valves'][0]['motor'] != 3 or drain != 'Pump 7 Quantitative 10 mL' or 'Scheduling page process' not in saved['recipes_to_check']:
        raise SystemExit('“”What is the pump connected to?” After archiving, the name, cleaning pump, and solenoid valve were not changed, or the process was not listed in what needs to be checked. The firmware of')
    restored = post('/api/pump/setup', {'pumps': [dict(motor=row['motor'], use=row['use'], name=row['name']) for row in before['pumps']],
                                        'valves': [dict(id=row['id'], motor=row['motor']) for row in before['valves']]})
    if [row['label'] for row in restored['pumps']] != [row['label'] for row in before['pumps']]:
        raise SystemExit('“What is the pump connected to?” After changing it back, it is different from the original one.')


def main():
    OUT.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='plasmic-shots-'))
    state['profile'] = work / 'edge'
    program = subprocess.Popen([PYTHON, '-B', str(ROOT / 'pc' / 'app.py'), '--demo', '--no-browser', '--port', str(PORT),
                                '--sim-port', str(SIM), '--data', str(work / 'data')],
                               env=dict(os.environ, PLASMIC_NO_SERIAL='1'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        walk()
    finally:
        subprocess.run(['taskkill', '/PID', str(program.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        program.wait(20)
        shutil.rmtree(work, ignore_errors=True)
    missing = [name for name in PICTURES if not (OUT / f'guide-{name}.png').is_file()]
    print('Missing screenshots:%s' % missing if missing else 'All seven screenshots are here shots\\ Inside', flush=True)
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
