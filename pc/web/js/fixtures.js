// fixtures.js — development aid, never used by the real program (see boot() in main.js).
// When the page is opened as  …/index.html#dev-fixture  from a plain file server (no /api at all), the examples of
// API.md (C.3.1, C.4.1, C.4.2, C.6, with the additions of AMENDMENTS M1, M3 and M5.3) stand in for the program,
// so the frame and the pages can be looked at before the program exists.
//
//   #dev-fixture            the idle example
//   #dev-fixture=running    one of: idle · waiting · running · pausing · paused · stopping · alarm · acked ·
//                           calibration · unlocked · silent (the "program" stops answering -> stale after 5 s)
// In the browser console:  plasmicDev.use('paused')   plasmicDev.state (change it; the next poll shows it)
//                          plasmicDev.store / dialog / frame / go
// Nothing here talks to a device or to the network.

const RECIPE_ID = '0d6c3d8e2b6a4b0f9a51c8f6a2e4b7d1';
const PUMP_RECIPE_ID = 'c41d7e0a9b3f4d6e8a2b1c0d9e8f7a6b';
const CLEAN_RECIPE_ID = '5e0f2a7b6c1d4e3f8a9b0c1d2e3f4a5b';
const FLOW = {name: 'unified', ok: true, background_frames: 1, text: "Turn on the high voltage first, take 1 picture of the background, and discharge in about 5 seconds after pressing"};

const iso = (offsetSeconds = 0) => {
  const d = new Date(Date.now() + offsetSeconds * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const zone = -d.getTimezoneOffset();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    + `${zone < 0 ? '-' : '+'}${pad(Math.floor(Math.abs(zone) / 60))}:${pad(Math.abs(zone) % 60)}`;
};
const copy = (value) => JSON.parse(JSON.stringify(value));
const refuse = (status, code, error, reasons) => Object.assign(new Error(error), {status, code, error, reasons});

const bootstrap = {
  csrf: 'fixture-token', version: '2.0.0', demo: true, boot_id: 'fixture000000000', pin_set: true, practice_pin: '000000', ui_watchdog_s: 10,
  config: {rpi_host: '127.0.0.1', rpi_port: 8767, token_set: true},
  hardware: {
    groups: [
      {id: 'A', label: "Group A", motors: [{id: 2, label: "Pump 1 · Water", fluid: "water", use: 'water', valve: null},
        {id: 3, label: 'Pump 2', fluid: null, use: 'none', valve: null}, {id: 4, label: 'Pump 3', fluid: null, use: 'none', valve: null},
        {id: 5, label: 'Pump 4', fluid: null, use: 'none', valve: null}]},
      {id: 'B', label: "Group B", motors: [{id: 6, label: 'Pump 5', fluid: null, use: 'none', valve: null},
        {id: 7, label: 'Pump 6', fluid: null, use: 'none', valve: null}, {id: 8, label: 'Pump 7 · OUT', fluid: 'OUT', use: 'out', valve: null},
        {id: 9, label: 'Pump 8 · CuSO₄', fluid: 'CuSO₄', use: 'chemical', valve: null}]}],
    valves: [{id: 1, label: "solenoid valve 1", motor: null}, {id: 2, label: "solenoid valve 2", motor: null}],
    valve_open_before_ms: 1000, valve_close_after_ms: 1000,
    max_flow_ml_min: 80, default_flow_ml_min: 40, dose_flow_ml_min: 40,
    gravimetric: {enabled: true, fast_flow_ml_min: 40, approach_flow_ml_min: 10, trim_flow_ml_min: 3, tolerance_percent: 0.5, timeout_s: 300},
    cleaning: {water_motor: 2, out_motor: 8, flow_ml_min: 40, water_lead_s: 30, overlap_s: 45, out_tail_s: 45, total_s: 120},
    syringe: {options_ul: [5000, 1250], valve_positions: 3},
    scale: {decimals: 3, calibration_presets_g: [10, 50, 100, 200], default_calibration_g: 100}, rev: 1},
  limits: {run_max_s: 300, flow_min_ml_min: 0.1, dose_min: 0.01, dose_max: 1000, calibration_min_g: 0.1, calibration_max_g: 20000},
  recipe: {limits: {bytes: 200000, rows: 500, steps: 2000, nesting: 5, repeat: 20, delay_total_s: 7200},
    examples: [{file: 'plasma-only.csv', name: "only discharge power measurement"}, {file: 'dose-measure-drain.csv', name: "Example: adding liquid + measuring + discharging"}, {file: 'cleaning.csv', name: "Example: automatic cleaning"}]},
  labels: {
    schedule: {waiting: "Waiting for the next time", running: "under measurement", pausing: "Paused", paused: "suspended", stopping: "Stopping", completed: "Completed", stopped: "has stopped", failed: "stopped on failure", interrupted: "Interrupted"},
    slot: {running: "under measurement", done: "Complete", failed: "failed", stopped: "has stopped", not_started: "not started", skipped_overrun: "skip (last timeout)", skipped_paused: "skip (schedule paused)"},
    round: {running: "is executing", pausing: "Paused", paused: "suspended", stopping: "Stopping", completed: "Complete", failed: "failed", stopped: "has stopped", interrupted: "Interrupted"},
    step: {pending: '—', running: "is executing", done: "Complete", failed: "failed", stopped: "has stopped", skipped: "not executed"}},
};

const recipes = [
  {id: RECIPE_ID, name: "only discharge power measurement", created_at: iso(-86400), source: 'seed', step_count: 1, rounds: 1, estimate_seconds: 21,
    uses_pump: false, uses_rpi: true, uses_plasma: true, valid: true, error: ''},
  {id: PUMP_RECIPE_ID, name: "Measurement after adding liquid", created_at: iso(-3600), source: 'import', step_count: 8, rounds: 1, estimate_seconds: 139,
    uses_pump: true, uses_rpi: true, uses_plasma: true, valid: true, error: ''},
  {id: CLEAN_RECIPE_ID, name: "automatic cleaning", created_at: iso(-1800), source: 'import', step_count: 1, rounds: 1, estimate_seconds: 124,
    uses_pump: true, uses_rpi: false, uses_plasma: false, valid: true, error: ''},
];
const recipeFacts = {
  [RECIPE_ID]: {liquids: [], warnings: []},
  [PUMP_RECIPE_ID]: {warnings: ["Column 4: Pump 7 · OUT reverse"],
    liquids: [{motor: 9, label: 'Pump 8 · CuSO₄', ml: null, g: 10.0, approx: false}, {motor: 2, label: "Pump 1 · Water", ml: 20.0, g: null, approx: false},
      {motor: 8, label: 'Pump 7 · OUT', ml: 40.0, g: null, approx: true}]},
  [CLEAN_RECIPE_ID]: {warnings: ["This Recipe does not have a measurement step (plasma_run)"],
    liquids: [{motor: 2, label: "Pump 1 · Water", ml: 50.0, g: null, approx: true}, {motor: 8, label: 'Pump 7 · OUT', ml: 60.0, g: null, approx: true}]},
};

function idleState() {
  const motors = [];
  for (const group of bootstrap.hardware.groups) {
    for (const motor of group.motors) {
      motors.push({id: motor.id, label: motor.label, fluid: motor.fluid, group: group.id, valve: motor.valve, state: 'off',
        flow_ml_min: null, direction: 1, left_s: null, by: null, blocked: ''});
    }
  }
  return {
    version: '2.0.0', boot_id: bootstrap.boot_id, time: iso(), demo: true, pin_set: true, unlocked: false, unlock_left_s: 0,
    operator: "Wang Xiaoming", operators: ["Wang Xiaoming", "Li Dahua"],
    setup: {pin: true, rpi: true, board: 'ok', operators: 2, recipes: 3, complete: true},
    owner: null,
    message: {time: iso(-3), level: 'info', text: "process completed: discharge measurement only (2/6th time)"},
    revs: {events: 412, recipes: 3, runlog: 57, runs: 31, measurements: 4, calibrations: 1},
    rpi: {
      connected: true, host: '127.0.0.1', port: 8767, error: '',
      server: {server: 'plasmic-tcpd', version: '2.0.0', protocol: 2, sim: true, flow: 'unified', watchdog_s: 5,
        limits: {max_voltage_v: 700, max_job_s: 215}},
      pending: '',
      status: {busy: false, job: null, age_s: 1.2, updated_at: iso(-1), error: '',
        snapshot: {parameter: {valid: true, voltage_v: 300, on_time_ms: 25, off_time_ms: 5, cycles: 25, loops: 8, integration_ms: 1000, spectra_interval_ms: 1500},
          power: {valid: true, run_stop_setting: 0, voltage_v: 0, current_a: 0, set_voltage_v: 300, set_current_a: 0.1},
          nano: {valid: true, firmware: '1.2.1', state: 'IDLE', fault: 'NONE', outputs_allowed: true, output_command_high: false, output_pin_sample: false, active_parameters: {}}},
        ready: {plasma: true, problems: []}, estimate_s: 21, start_s: 5, flow: 'unified', sim: true},
      status_age_s: 0.4, hv: {known: true, idle: true, reasons: []}, flow: copy(FLOW),
      parameters: {values: {Ontime: 25, Offtime: 5, Cycle: 25, Loopnum: 8, Integration_Time: 1000, Spectra_Interval: 1500, Voltage: 300}, nano_synced: true},
      own_job: null, found: [], addresses: ['127.0.0.1'],
    },
    pump: {
      mode: 'sim', link: 'connected', text: "Simulation", device: '', description: '', error: '', since: iso(-600),
      banner_seen: true, firmware_ok: true, firmware_match: true, firmware_tag: 'p2', hardware_rev: 1, motion_uncertain: false,
      cleaning: false, clean_phase: '',
      groups: [{id: 'A', label: "Group A", selected: null, state: 'idle'}, {id: 'B', label: "Group B", selected: null, state: 'idle'}],
      motors,
      valves: [{id: 1, label: "solenoid valve 1", open: false}, {id: 2, label: "solenoid valve 2", open: false}],
      syringe: {online: true, initialised: false, busy: false, size_ul: 5000, position: null, volume_ul: null, speed_ml_s: 0.4, max_speed_ml_s: 2.5, error: null},
      scale: {online: true, grams: 12.345, stable: true, age_s: 0.1, calibrated: true, calibrated_at: '2026-08-26T10:16:20+00:00', reference_g: 200.0},
      op: null, weight_dose: null, last_result: null, settings: {syringe_ul: 5000, syringe_speed_ml_s: 0.4},
    },
    interlock: {manual: {ok: true, reasons: [], need_hv_ack: false}, start: {ok: true, reasons: []}},
    schedule: null,
    round: null,
    latest_run: {job: 'HMI-5f2c0000000000000000000000000001', kind: 'plasma', saved_at: iso(-600), voltage_v: 300, peak_nm: 337.1, peak_value: 5120.4,
      signal_frames: 8, background_frames: 1, flow: 'unified', wait_s: 4.6, round_id: '7a900000000000000000000000000001'},
    alarms: {worst: null, unacked: 0, counts: {p1: 0, p2: 0, p3: 0}, active: []},
    measurement: {latest_sample: null, latest_blank: null},
    calibration_active: null,
    calibration_run: null,
  };
}

function scheduleOf(status, recipe, kind = 'schedule') {
  return {
    version: 2, id: '4b1f0000000000000000000000000001', status, created_at: iso(-700), finished_at: null, operator: "Wang Xiaoming",
    kind, calibration_id: kind === 'calibration' ? 'CAL-00000000000000000000000000000001' : null, blank: null,
    recipe: {id: recipe.id, name: recipe.name, sha256: '9b0e'}, uses_pump: recipe.uses_pump, uses_rpi: recipe.uses_rpi, uses_plasma: recipe.uses_plasma,
    confirmed: {high_voltage: recipe.uses_plasma, liquid: recipe.uses_pump, hv_off_checked: false}, voltage_v: 300, estimate_seconds: recipe.estimate_seconds,
    spec: {first_slot: iso(-600), interval_seconds: 600, interval_minutes: 10, start: {mode: 'now', time: null}, end: {mode: 'count', count: 6}},
    planned_total: kind === 'calibration' ? 1 : 6, last_slot_at: iso(2400), next_index: 1, next_slot_at: iso(580),
    slots: [{index: 0, kind: 'sample', planned_at: iso(-600), status: 'done', round_id: '7a900000000000000000000000000001',
      job: 'HMI-5f2c0000000000000000000000000001', started_at: iso(-600), finished_at: iso(-578), note: ''}],
    counts: {done: 1}, error: '',
  };
}

function roundOf(status, recipe) {
  const pump = recipe.uses_pump;
  return {
    id: '8c110000000000000000000000000002', rev: 5, status, schedule_id: '4b1f0000000000000000000000000001', slot: 2, slots_planned: 6, operator: "Wang Xiaoming",
    mode: 'schedule', sample_kind: 'sample', prompt: null, measurements: [],
    recipe: {id: recipe.id, name: recipe.name}, started_at: iso(-8), finished_at: null,
    step: pump ? 5 : 1, step_count: recipe.step_count, round: 1, rounds: 1,
    phase: pump ? 1 : 0, phases: pump ? [{name: "Add liquid", from: 1, to: 4}, {name: "measurement", from: 5, to: 5}, {name: "Add liquid", from: 6, to: 8}] : [{name: "measurement", from: 1, to: 1}],
    current: {n: pump ? 5 : 1, action: 'plasma_run', text: "discharge + spectrum capture", detail: "Discharging·Signal 3/8 pictures", started_at: iso(-6), eta_s: 21},
    uses_plasma: true, uses_pump: pump, paused_pumps: [], plasma_job: 'HMI-77d00000000000000000000000000003', error: '', stop_report: null,
    steps_window: {from: 1, items: [{n: 1, text: "discharge + spectrum capture", phase: 0, status: 'running', started_at: iso(-6), finished_at: null, result_text: '', error: ''}]},
  };
}

const scenarios = {
  idle() {},
  waiting(s) { s.schedule = scheduleOf('waiting', recipes[0]); },
  running(s) {
    s.schedule = scheduleOf('running', recipes[0]);
    s.schedule.slots.push({index: 1, kind: 'sample', planned_at: iso(-8), status: 'running', round_id: '8c110000000000000000000000000002', started_at: iso(-8)});
    s.round = roundOf('running', recipes[0]);
    s.owner = {kind: 'round', label: "only discharge test 2/6th time", since: iso(-8)};
  },
  pausing(s) { scenarios.running(s); s.schedule.status = 'pausing'; s.round.status = 'pausing'; },
  paused(s) {
    s.schedule = scheduleOf('paused', recipes[1]);
    s.round = roundOf('paused', recipes[1]);
    s.round.paused_pumps = ['Pump 7 · OUT'];
    s.owner = {kind: 'round', label: "Measurement after adding liquid 2/6th time", since: iso(-40)};
  },
  stopping(s) { scenarios.running(s); s.schedule.status = 'stopping'; s.round.status = 'stopping'; },
  alarm(s) {
    s.alarms = {worst: 1, unacked: 2, counts: {p1: 1, p2: 1, p3: 1}, active: [
      {id: 17, key: 'hv_stop_unconfirmed', priority: 1, area: "high voltage", text: "The stop of the high-voltage power supply has not been confirmed: please immediately turn off the high-voltage with a physical switch and go to the site to confirm", since: iso(-65), acked: false, cleared: false},
      {id: 18, key: 'rpi_link_lost', priority: 2, area: 'RPi', text: "RPi connection is interrupted, the program is reconnecting", since: iso(-64), acked: false, cleared: false},
      {id: 12, key: 'rpi_flow_fallback', priority: 3, area: 'RPi', text: "RPi is set to the old measurement process: 3 backgrounds, and it takes about 11 seconds to discharge after pressing.", since: iso(-900), acked: true, cleared: false}]};
    s.rpi.connected = false;
    s.rpi.error = "cannot connect to RPi (127.0.0.1:8767)";
    s.interlock.start = {ok: true, reasons: []};
  },
  acked(s) {
    s.alarms = {worst: null, unacked: 0, counts: {p1: 0, p2: 0, p3: 1}, active: [
      {id: 12, key: 'rpi_flow_fallback', priority: 3, area: 'RPi', text: "RPi is set to the old measurement process: 3 backgrounds, and it takes about 11 seconds to discharge after pressing.", since: iso(-900), acked: true, cleared: false}]};
  },
  calibration(s) {
    s.schedule = scheduleOf('running', recipes[0], 'calibration');
    s.round = roundOf('running', recipes[0]);
    s.round.mode = 'calibration';
    s.round.step = 4;
    s.round.step_count = 12;
    s.round.prompt = {text: "Please replace the standard product STD-1 (Cu 1 / Zn 1 / Ni 1 mg/L), put the pipette into the new bottle and click \"Confirm Liquid Change\"", since: iso(-20)};
    s.calibration_run = {id: 'CAL-00000000000000000000000000000001', status: 'running', recipe_name: "only discharge power measurement", level: 1, level_name: 'STD-1', run: 4, runs: 12,
      prompt: copy(s.round.prompt)};
  },
  unlocked(s) { s.unlocked = true; s.unlock_left_s = 600; },
  silent() {},
};

export function start(tools) {
  let name = 'idle';
  let state = idleState();
  let session = '';

  const use = (next) => {
    if (!scenarios[next]) throw new Error(`unknown scenario ${next}: ${Object.keys(scenarios).join(' ')}`);
    name = next;
    state = idleState();
    scenarios[next](state);
    dev.state = state;
    return next;
  };

  const preview = (body) => {
    const recipe = recipes.find((item) => item.id === (body && body.recipe_id));
    if (!recipe) throw refuse(404, 'not_found', 'Not found');
    const schedule = (body && body.schedule) || {};
    const end = schedule.end || {mode: 'count', count: 1};
    const minutes = schedule.interval_minutes || 10;
    const total = end.mode === 'count' ? end.count : null;
    const blank = body.blank ? {recipe_id: body.blank.recipe_id, recipe_name: (recipes.find((item) => item.id === body.blank.recipe_id) || {}).name || '', every: body.blank.every} : null;
    const blockers = [];
    if (recipe.uses_rpi && !state.rpi.connected) blockers.push({code: 'rpi_link', text: "RPi not connected", fix: "Please ask maintenance personnel to check at \"Maintenance › Connection\""});
    return {
      spec: {first_slot: iso(schedule.start && schedule.start.mode === 'at' ? 3600 : 0), interval_seconds: minutes * 60, interval_minutes: minutes,
        start: {mode: (schedule.start && schedule.start.mode) || 'now', time: (schedule.start && schedule.start.time) || null}, end},
      planned_total: total, last_slot_at: total ? iso((total - 1) * minutes * 60) : null, estimate_seconds: recipe.estimate_seconds,
      recipe: {id: recipe.id, name: recipe.name, step_count: recipe.step_count, rounds: recipe.rounds, uses_pump: recipe.uses_pump, uses_rpi: recipe.uses_rpi,
        uses_plasma: recipe.uses_plasma, liquids: recipeFacts[recipe.id].liquids, warnings: recipeFacts[recipe.id].warnings},
      voltage_v: recipe.uses_plasma ? 300 : null, flow: copy(FLOW), warnings: recipeFacts[recipe.id].warnings, blockers, blank,
      needs: {high_voltage: recipe.uses_plasma, liquid: recipe.uses_pump, hv_off_checked: recipe.uses_pump && !state.rpi.connected},
    };
  };

  const post = (path, body) => {
    switch (path) {
      case '/api/stop': {
        const job = state.round && state.round.plasma_job;
        const had = Boolean(state.schedule);
        state.schedule = null;
        state.round = null;
        state.owner = null;
        state.calibration_run = null;
        return {accepted: true, schedule_ended: had, round_stopping: Boolean(job), manual_cancelled: false,
          pump: {result: 'confirmed', error: ''}, rpi: job ? {result: 'cancel_sent', error: '', job} : {result: 'confirmed', error: ''}};
      }
      case '/api/operator':
        if (!state.operators.includes(body.name)) throw refuse(400, 'bad_request', "operator is not in the list");
        state.operator = body.name;
        return {operator: body.name};
      case '/api/alarms/ack': {
        let count = 0;
        for (const alarm of state.alarms.active) {
          if ((body.all || (body.ids || []).includes(alarm.id)) && !alarm.acked) { alarm.acked = true; count += 1; }
        }
        const open = state.alarms.active.filter((alarm) => !alarm.acked);
        state.alarms.unacked = open.length;
        state.alarms.worst = open.length ? Math.min(...open.map((alarm) => alarm.priority)) : null;
        state.alarms.active.sort((a, b) => Number(a.acked) - Number(b.acked) || a.priority - b.priority);
        return {acked: count};
      }
      case '/api/schedule/preview':
        return preview(body);
      case '/api/schedule/start': {
        const facts = preview(body);
        if (!state.operator) throw refuse(400, 'bad_request', "Please select operator first");
        if (state.schedule) throw refuse(409, 'state', "already has a schedule in progress");
        if (facts.blockers.length) throw refuse(409, 'blocked', "Cannot start scheduling now", facts.blockers);
        const confirm = body.confirm || {};
        if ((facts.needs.high_voltage && !confirm.high_voltage) || (facts.needs.liquid && !confirm.liquid) || (facts.needs.hv_off_checked && !confirm.hv_off_checked)) {
          throw refuse(400, 'bad_request', "You must first check each item in the confirmation window");
        }
        const recipe = recipes.find((item) => item.id === body.recipe_id);
        state.schedule = scheduleOf('running', recipe);
        state.schedule.planned_total = facts.planned_total;
        state.schedule.next_index = 0;
        state.schedule.slots = [];
        state.schedule.counts = {};
        state.round = roundOf('running', recipe);
        state.round.slot = 1;
        state.round.uses_plasma = recipe.uses_plasma;
        return {accepted: true, schedule: state.schedule};
      }
      case '/api/pause':
        if (!state.schedule || !['waiting', 'running'].includes(state.schedule.status)) throw refuse(409, 'state', "There is currently no schedule that can be paused");
        state.schedule.status = 'paused';
        if (state.round) state.round.status = 'paused';
        return {accepted: true, status: 'paused'};
      case '/api/resume':
        if (!state.schedule || state.schedule.status !== 'paused') throw refuse(409, 'state', "The schedule is not in the paused state");
        state.schedule.status = 'running';
        if (state.round) state.round.status = 'running';
        return {accepted: true, status: 'running'};
      case '/api/unlock':
        if (body.pin !== bootstrap.practice_pin) throw refuse(409, 'pin', "Repair PIN error");
        session = 'fixture-session';
        state.unlocked = true;
        state.unlock_left_s = 600;
        return {session, expires_in: 600};
      case '/api/lock':
        session = '';
        state.unlocked = false;
        state.unlock_left_s = 0;
        return {locked: true};
      default:
        throw refuse(404, 'not_found', 'Not found');
    }
  };

  const get = (path) => {
    const route = path.split('?')[0];
    if (route === '/api/bootstrap') return copy(bootstrap);
    if (route === '/api/state') {
      if (name === 'silent') throw refuse(0, 'network', "The program did not respond");
      state.time = iso();
      return copy(state);
    }
    if (route === '/api/recipes') return {recipes: copy(recipes)};
    if (route === '/api/runlog') return {rows: [], more: false};
    if (route === '/api/events') return {events: [{time: iso(-3), level: 'info', text: "process completed: discharge measurement only (2/6th time)", actor: "system"}], more: false};
    if (route === '/api/runs') return {runs: [copy(state.latest_run)]};
    throw refuse(404, 'not_found', 'Not found');
  };

  const dev = {...tools, use, state, scenarios: Object.keys(scenarios)};
  window.plasmicDev = dev;
  const asked = decodeURIComponent(location.hash).split('=')[1];
  if (asked && scenarios[asked]) use(asked);
  console.info("[fixture] Plasmic electronic control runs on the examples of API.md; no program, no device. Scenario:", name);

  return async (method, path, body) => (method === 'POST' ? post(path, body || {}) : get(path));
}
