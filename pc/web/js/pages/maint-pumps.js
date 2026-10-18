//maint-pumps.js — Maintenance › Pumps and Scales (SPEC E.13). Left: what the pump board's settings file says (read-only, from
//bootstrap.hardware), the pump setup (AMENDMENTS M20:     = what each pump carries, water and OUT being the two
//pumps of the cleaning; “solenoid valve linkage” = which pump each solenoid valve opens with, none unless it is set here) and the
//two settings of the syringe pump. Right: the two calibrations — the scale (its reading, "zero" and "correction") and,
//AMENDMENTS M16, each pump: "Pump Calibration" doses 10 mL with the chosen pump (the one thing on this page that starts a pump,
// after a confirmation of its own) and takes the volume that really came out.

import { act, fail, busyReason, gbtn, setGbtn, kvRow, setValue } from './maint.js';

const T_NO_BOARD = "pump control board is not connected";
const T_MOVING = "There is a pump that is still running";
const T_NO_READING = "scale has no reading value\nThe reading value of";
const T_UNSTABLE = "is still changing, wait for it to stabilize.";
const OTHER = 'other';
const ACK_TEXT = "I have gone to the site to confirm that the high voltage power supply is turned off.";
const SEND_TIMEOUT_MS = 20000;

let ui = null;

const num = (value) => (typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value * 1000) / 1000) : '—');
const ml = (microlitres) => num(Number(microlitres) / 1000);

function refreshBoot(ctx) {
  // the limits of the Recipe editor (syringe volume and speed) follow the syringe size: they come with the bootstrap
  if (typeof ctx.api.bootstrap === 'function') ctx.api.bootstrap().catch(() => {});
}

// The read-only lines of the left panel: [label, value].
function facts(ctx) {
  const boot = ctx.store.boot || {};
  const hardware = boot.hardware;
  if (!hardware) return [];
  const motors = [];
  for (const group of hardware.groups || []) motors.push(...(group.motors || []));
  const weigh = hardware.gravimetric || {};
  const clean = hardware.cleaning || {};
  return [
    ["peristaltic pump", `${motors.length} pcs · Maximum ${num(hardware.max_flow_ml_min)} mL/min`],
    ["quantitative speed", `${num(hardware.dose_flow_ml_min)} mL/min (fixed)`],
    ["weighing and quantitative", weigh.enabled
      ? `${num(weigh.fast_flow_ml_min)} → ${num(weigh.approach_flow_ml_min)} → ${num(weigh.trim_flow_ml_min)} mL/min, tolerance ± ${num(weigh.tolerance_percent)} %`
      : "is not enabled"],
    ["Continuous operation upper limit", `${num((boot.limits || {}).run_max_s)} seconds`],
    ["automatic cleaning", `water in ${num(clean.water_lead_s)} seconds → water and OUT together ${num(clean.overlap_s)} seconds → emptying ${num(clean.out_tail_s)} seconds (subject to firmware)`],
  ];
}

// ── what each pump carries, and which pump each valve opens with (AMENDMENTS M20) ───────────────────────────
// GET /api/pump/setup: {editable, reason, source, pumps: [{motor, pump, short, label, group, half, use, name, valve}],
// valves: [{id, label, motor}], name_max, names: {water, out}, valve_open_before_ms, valve_close_after_ms}.
// POST /api/pump/setup takes the whole setup at once: {pumps: [{motor, use, name}], valves: [{id, motor}]}.
const setup = {data: null};
const SAVE = 'save';
const NO_VALVE = 0;
const T_NOT_READ = "has not read the settings yet";
const T_RECIPES = "The published recipes are written with pump numbers and will not be changed accordingly: please check the recipes that use these pumps after archiving.";

async function loadSetup(ctx) {
  setup.data = await ctx.api.get('/api/pump/setup', {timeout: 8000});
  if (ui && ctx.store.state) render(ctx.store.state, ctx);
  return setup.data;
}

function useText(row, names) {
  if (row.use === 'water') return names.water;
  if (row.use === 'out') return names.out;
  return row.use === 'chemical' && row.name ? row.name : "did not answer";
}

//The small lines of the two buttons: "Water Pump 1 · OUT Pump 7" · "Valve 1 follows Pump 1" / "Not used". One line each, also in
// the narrow (4:3) frame.
function rolesText(data) {
  const water = data.pumps.find((row) => row.use === 'water');
  const out = data.pumps.find((row) => row.use === 'out');
  return `${data.names.water} ${water ? water.short : '?'} · ${data.names.out} ${out ? out.short : '?'}`;
}

function valvesText(data) {
  const short = (motor) => { const row = data.pumps.find((item) => item.motor === motor); return row ? row.short : '?'; };
  const linked = data.valves.filter((valve) => valve.motor !== null && valve.motor !== undefined)
    .map((valve) => `valve ${valve.id} followed by ${short(valve.motor)}`);
  return linked.length ? linked.join(' · ') : "Not used";
}

//One water and one OUT, one on each half (they turn together in the cleaning). The pump that had the role gives it up;
// when the two would land on the same half, the other role moves to where this one was.
function assign(draft, motor, use, name) {
  const row = draft.find((item) => item.motor === motor);
  if (use !== 'water' && use !== 'out') {
    row.use = use;
    row.name = use === 'chemical' ? name : '';
    return;
  }
  const other = use === 'water' ? 'out' : 'water';
  const old = draft.find((item) => item.use === use);
  const partner = draft.find((item) => item.use === other);
  if (old && old !== row) { old.use = 'none'; old.name = ''; }
  if (partner && (partner === row || partner.half === row.half)) {
    if (partner !== row) { partner.use = 'none'; partner.name = ''; }
    if (old && old !== row) { old.use = other; old.name = ''; }
  }
  row.use = use;
  row.name = '';
}

async function sendSetup(ctx, body, title) {
  let reply;
  try {
    reply = await ctx.api.post('/api/pump/setup', body, {timeout: SEND_TIMEOUT_MS});
  } catch (error) {
    fail(ctx, error);
    if (!(error && error.status === 403)) {
      const reasons = (error.reasons || []).filter((item) => item && item.text && item.text !== error.message).map((item) => item.text).join('; ');
      await ctx.dialog.message("not saved", error.message + (reasons ? `: ${reasons}` : ''), {level: 'error'});
    }
    return;
  }
  setup.data = reply;
  refreshBoot(ctx);
  if (ctx.store.state) render(ctx.store.state, ctx);
  const lines = [reply.summary || ''];
  // the steps of the schedule page name their pumps by number, as every Recipe does (AMENDMENTS M23)
  if (reply.flow === 'open') lines.push("The process of \"Setting Yourself\" on the schedule page cannot be used according to the new settings: please go to \"Scheduling Measurement\" to change the steps.");
  if ((reply.recipes_to_check || []).length) lines.push(`Please check these Recipes (they use modified pumps): ${reply.recipes_to_check.join(', ')}`);
  await ctx.dialog.message(title, lines.filter(Boolean).join('\n\n'));
}

async function editOnePump(ctx, data, draft, row) {
  const role = row.use === 'water' || row.use === 'out';
  const hold = role ? `This one is now connected to ${row.use === 'water' ? data.names.water : data.names.out}: choose another one to connect first` : false;
  const pick = await ctx.dialog.choose({title: `${row.short} What to connect to`, wide: true, columns: 1, timeout: 120,
    detail: `now: ${useText(row, data.names)}`,
    choices: [{value: 'water', label: data.names.water, small: "The one that got water during cleaning", current: row.use === 'water'},
      {value: 'out', label: `${data.names.out} (empty)`, small: "The one that draws out the liquid during emptying and cleaning", current: row.use === 'out'},
      {value: 'chemical', label: row.use === 'chemical' ? `Chemical: ${row.name} (name changed)` : "Chemicals…", small: "Enter the name, such as CuSO₄, standard solution",
        current: row.use === 'chemical', disabled: hold},
      {value: 'none', label: "did not answer", small: "This one has no receiver", current: row.use === 'none', disabled: hold}]});
  if (pick === null) return;
  if (pick !== 'chemical') { assign(draft, row.motor, pick, ''); return; }
  const name = await ctx.dialog.text({title: `${row.short} Connected chemicals`, value: row.use === 'chemical' ? row.name : '', maxLength: data.name_max,
    timeout: 120});
  if (name === null || !name) return;
  assign(draft, row.motor, 'chemical', name);
}

async function editPumps(ctx) {
  let data;
  try { data = await loadSetup(ctx); } catch (error) { fail(ctx, error); return; }
  if (!data.editable) { await ctx.dialog.message("cannot be changed now", data.reason || ''); return; }
  const draft = data.pumps.map((row) => ({motor: row.motor, short: row.short, half: row.half, use: row.use, name: row.name}));
  const halves = [...new Set(data.pumps.map((row) => row.half))];
  for (;;) {
    const pick = await ctx.dialog.choose({title: "What is connected to the pump: click one to change it", wide: true, columns: 2, timeout: 120, cancelLabel: "Cancel",
      detail: `"${data.names.water}" and "${data.names.out}" are two for cleaning, one in ${halves[0]} and one in ${halves[halves.length - 1]}. Click "Save" after making changes.`,
      choices: draft.map((row) => ({value: row.motor, label: row.short, small: useText(row, data.names),
        current: row.use === 'water' || row.use === 'out'})),
      actions: [{label: "storage", value: SAVE}]});
    if (pick === null) return;
    if (pick !== SAVE) { await editOnePump(ctx, data, draft, draft.find((row) => row.motor === pick)); continue; }
    const changed = draft.filter((row) => { const was = data.pumps.find((item) => item.motor === row.motor); return was.use !== row.use || was.name !== row.name; });
    if (!changed.length) { ctx.message("What is connected to the pump: No change"); return; }
    const yes = await ctx.dialog.confirm({title: "stores \"What to connect to the pump\"", confirmLabel: "storage", timeout: 120,
      lines: changed.map((row) => [row.short, `${useText(data.pumps.find((item) => item.motor === row.motor), data.names)} → ${useText(row, data.names)}`]),
      warn: [T_RECIPES]});
    if (!yes) continue;
    await sendSetup(ctx, {pumps: draft.map((row) => ({motor: row.motor, use: row.use, name: row.name})),
      valves: data.valves.map((valve) => ({id: valve.id, motor: valve.motor}))}, "What to connect to the pump: saved");
    return;
  }
}

async function editValves(ctx) {
  let data;
  try { data = await loadSetup(ctx); } catch (error) { fail(ctx, error); return; }
  if (!data.editable) { await ctx.dialog.message("cannot be changed now", data.reason || ''); return; }
  const draft = data.valves.map((valve) => ({id: valve.id, motor: valve.motor === undefined ? null : valve.motor}));
  const label = (motor) => { const row = data.pumps.find((item) => item.motor === motor); return row ? row.label : '?'; };
  const words = (motor) => (motor === null ? "Not used" : `follows ${label(motor)}`);
  const seconds = (ms) => num(Number(ms) / 1000);
  for (;;) {
    const pick = await ctx.dialog.choose({title: "solenoid valve linkage: click one to change\nThe", wide: true, columns: 1, timeout: 120, cancelLabel: "Cancel",
      detail: `linked solenoid valve will open ${seconds(data.valve_open_before_ms)} seconds before the pump rotates, and then close ${seconds(data.valve_close_after_ms)} seconds after the pump stops.`
        + "The solenoid valve set to \"not used\" will not operate. Click \"Save\" after making changes.",
      choices: draft.map((valve) => ({value: valve.id, label: `solenoid valve ${valve.id}`, small: words(valve.motor), current: valve.motor !== null})),
      actions: [{label: "storage", value: SAVE}]});
    if (pick === null) return;
    if (pick !== SAVE) {
      const valve = draft.find((item) => item.id === pick);
      const motor = await ctx.dialog.choose({title: `solenoid valve ${valve.id} follow which pump`, wide: true, columns: 2, timeout: 120,
        detail: `now: ${words(valve.motor)}`,
        choices: data.pumps.map((row) => {
          const taken = draft.find((item) => item.id !== valve.id && item.motor === row.motor);
          return {value: row.motor, label: row.label, current: valve.motor === row.motor, disabled: taken ? `has been linked to the solenoid valve ${taken.id}` : false};
        }),
        actions: [{label: "Not used", value: NO_VALVE}]});
      if (motor !== null) valve.motor = motor === NO_VALVE ? null : motor;
      continue;
    }
    const changed = draft.filter((valve) => { const was = data.valves.find((item) => item.id === valve.id); return (was.motor === undefined ? null : was.motor) !== valve.motor; });
    if (!changed.length) { ctx.message("solenoid valve linkage: no change"); return; }
    const yes = await ctx.dialog.confirm({title: "stores \"solenoid valve linkage\"", confirmLabel: "storage", timeout: 120,
      lines: changed.map((valve) => { const was = data.valves.find((item) => item.id === valve.id); return [`solenoid valve ${valve.id}`, `${words(was.motor === undefined ? null : was.motor)} → ${words(valve.motor)}`]; }),
      warn: ["The solenoid valve set to \"not in use\" will always be closed: if it is really installed on the pipeline, liquid cannot be pumped out of that pipeline."]});
    if (!yes) continue;
    await sendSetup(ctx, {pumps: data.pumps.map((row) => ({motor: row.motor, use: row.use, name: row.name})),
      valves: draft.map((valve) => ({id: valve.id, motor: valve.motor}))}, "solenoid valve linkage: stored");
    return;
  }
}

// ── syringe pump settings ───────────────────────────────────────────────────────────────────────────────────
function saved(ctx) {
  const pump = (ctx.store.state && ctx.store.state.pump) || {};
  const settings = pump.settings || {};
  const syringe = pump.syringe || {};
  return {size: settings.syringe_ul, speed: settings.syringe_speed_ml_s, top: syringe.max_speed_ml_s};
}

async function post(ctx, size, speed) {
  const reply = await act(ctx, () => ctx.api.post('/api/pump/settings', {syringe_ul: size, syringe_speed_ml_s: speed}),
    `syringe pump settings: syringe ${ml(size)} mL, speed ${num(speed)} mL/s`);
  if (reply) refreshBoot(ctx);
}

async function chooseSize(ctx) {
  const now = saved(ctx);
  const options = (((ctx.store.boot || {}).hardware || {}).syringe || {}).options_ul || [];
  const size = await ctx.dialog.choose({title: "syringe capacity", value: now.size, detail: "The syringe pump needs to be reinitialized after changing the syringe.",
    choices: options.map((item) => ({value: item, label: `${ml(item)} mL`}))});
  if (size === null || size === now.size) return;
  // The fastest speed follows the size: a speed that is too fast for the new syringe is lowered with it.
  let speed = now.speed;
  if (typeof now.top === 'number' && typeof now.size === 'number' && now.size > 0) {
    const top = Math.floor(now.top * size / now.size * 1000 + 1e-6) / 1000;
    if (speed > top) speed = top;
  }
  const lines = [["Currently", `${ml(now.size)} mL`], ["changed to", `${ml(size)} mL`]];
  if (speed !== now.speed) lines.push(["syringe pump speed", `${num(now.speed)} → ${num(speed)} mL/s`]);
  const yes = await ctx.dialog.confirm({title: "syringe capacity", confirmLabel: "storage", lines, warn: "The syringe pump needs to be reinitialized after changing the syringe."});
  if (yes) await post(ctx, size, speed);
}

async function chooseSpeed(ctx) {
  const now = saved(ctx);
  const speed = await ctx.dialog.pad({title: "syringe pump speed", min: 0.001, max: typeof now.top === 'number' ? now.top : 2.5, decimals: 3,
    unit: 'mL/s', value: now.speed});
  if (speed === null || speed === now.speed) return;
  await post(ctx, now.size, speed);
}

// ── scale ───────────────────────────────────────────────────────────────────────────────────────────────────
function reading(ctx) {
  const state = ctx.store.state;
  const scale = (state && state.pump && state.pump.scale) || {};
  const decimals = Number(((((ctx.store.boot || {}).hardware || {}).scale) || {}).decimals);
  if (ctx.store.stale || !scale.online || typeof scale.grams !== 'number') return '—';
  return `${scale.grams.toFixed(Number.isInteger(decimals) && decimals >= 0 ? decimals : 3)} g`;
}

async function tare(ctx) {
  const yes = await ctx.dialog.confirm({title: "scale reset to zero", confirmLabel: "reset to zero", lines: [["current reading value", reading(ctx)]],
    warn: "Please confirm that the cup on the scale is empty. The current weight will be treated as 0 and saved."});
  if (yes) await act(ctx, () => ctx.api.post('/api/scale/tare', {}), "The scale has returned to zero");
}

async function calibrate(ctx) {
  const boot = ctx.store.boot || {};
  const scale = (boot.hardware || {}).scale || {};
  const limits = boot.limits || {};
  const presets = scale.calibration_presets_g || [];
  const picked = await ctx.dialog.choose({title: "weight weight", value: scale.default_calibration_g,
    choices: [...presets.map((grams) => ({value: grams, label: `${num(grams)} g`})), {value: OTHER, label: "Others…"}]});
  if (picked === null) return;
  let grams = picked;
  if (picked === OTHER) {
    grams = await ctx.dialog.pad({title: "weight weight", min: typeof limits.calibration_min_g === 'number' ? limits.calibration_min_g : 0.1,
      max: typeof limits.calibration_max_g === 'number' ? limits.calibration_max_g : 20000, decimals: 1, unit: 'g'});
    if (grams === null) return;
  }
  const yes = await ctx.dialog.confirm({title: "scale calibration", confirmLabel: "calibration", lines: [["Weight", `${num(grams)} g`], ["current reading value", reading(ctx)]],
    warn: "Please confirm that the weights have been placed. Calibration will overwrite the last result."});
  if (yes) await act(ctx, () => ctx.api.post('/api/scale/calibrate', {grams}), `scale calibrated (weight ${num(grams)} g)`);
}

// ── the calibration of each pump (AMENDMENTS M16) ───────────────────────────────────────────────────────────
// GET /api/pump/calibration: one row per pump {motor, pump, label, dose_pulses_per_ml, factory_pulses_per_ml,
// calibrated, nominal_ml, measured_ml, saved_at}. The 10 mL are an ordinary manual dose (POST /api/manual).
const cal = {rows: null, nominal: 10};
const CAL_WAIT_S = 600;
const one = (value) => (typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value * 10) / 10) : '—');
const two = (value) => (typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value * 100) / 100) : '—');

async function loadCal(ctx) {
  const reply = await ctx.api.get('/api/pump/calibration', {timeout: 8000});
  cal.rows = Array.isArray(reply.pumps) ? reply.pumps : [];
  if (typeof reply.nominal_ml === 'number') cal.nominal = reply.nominal_ml;
  if (ui && ctx.store.state) render(ctx.store.state, ctx);
  return cal.rows;
}

function calText(row) {
  if (!row.calibrated) return `per mL ${one(row.dose_pulses_per_ml)} steps · Original setting`;
  const change = (row.dose_pulses_per_ml / row.factory_pulses_per_ml - 1) * 100;
  return `steps per mL ${one(row.dose_pulses_per_ml)} · Corrected (% ${change >= 0 ? '+' : ''} ${change.toFixed(1)})`;
}

// Why the 10 mL cannot be dosed now ('' = they can). The program checks again when it is asked.
function doseBlocked(ctx) {
  const state = ctx.store.state;
  const busy = busyReason(ctx, state);
  if (busy) return busy;
  const pump = state.pump || {};
  if (pump.link !== 'connected') return T_NO_BOARD;
  if (pump.cleaning || (pump.motors || []).some((motor) => motor.state && motor.state !== 'off') || (pump.syringe && pump.syringe.busy)) return T_MOVING;
  const manual = (state.interlock && state.interlock.manual) || {};
  const reasons = (manual.reasons || []).filter((item) => item && item.text);
  return manual.ok === false && reasons.length ? reasons.map((item) => item.text).join('; ') : '';
}

async function doseForCalibration(ctx, row) {
  const state = ctx.store.state;
  const manual = (state && state.interlock && state.interlock.manual) || {};
  const ticks = manual.need_hv_ack ? [manual.hv_ack_text || ACK_TEXT] : [];
  const yes = await ctx.dialog.confirm({title: `${row.label}: hit ${two(cal.nominal)} mL`, variant: 'danger', confirmLabel: "start", ticks,
    lines: [["Volume", `${two(cal.nominal)} mL`], ["direction", "Forward rotation"]],
    warn: "The pump will rotate and stop by itself after rotating. Please put the outlet into the measuring cylinder first and make sure the pipe is filled with liquid."});
  if (!yes) return;
  try {
    await ctx.api.post('/api/manual', {op: 'dose_volume', motor: row.motor, volume_ml: cal.nominal, direction: 1,
      ...(ticks.length ? {hv_off_checked: true} : {})}, {timeout: SEND_TIMEOUT_MS});
  } catch (error) {
    const reasons = (error.reasons || []).filter((item) => item && item.text).map((item) => item.text).join('; ');
    await ctx.dialog.message("does not start", error.message + (reasons ? `: ${reasons}` : ''), {level: 'error'});
    return;
  }
  await ctx.dialog.message(`${row.label} is typing ${two(cal.nominal)} mL`,
    "Wait for it to stop by itself. Then read the actual mL on the graduated cylinder, and then press \"② Enter the actual number of mL\". To stop halfway: Press STOP at the bottom.");
}

async function enterMeasured(ctx, row) {
  const measured = await ctx.dialog.pad({title: `${row.label}: How many mL are actually produced?`, min: cal.nominal * 0.5, max: cal.nominal * 2, decimals: 2,
    unit: 'mL', value: cal.nominal, timeout: 120});
  if (measured === null) return;
  const reply = await act(ctx, () => ctx.api.post('/api/pump/calibrate', {motor: row.motor, measured_ml: measured, nominal_ml: cal.nominal}),
    `${row.label} corrected`);
  if (!reply) return;
  refreshBoot(ctx);
  await ctx.dialog.message(`${row.label} corrected`,
    `beats ${two(cal.nominal)} mL, actual ${two(measured)} mL. Number of steps per mL ${one(reply.before)} → ${one(reply.dose_pulses_per_ml)}`
    + `(${reply.change_percent >= 0 ? '+' : ''} ${Number(reply.change_percent).toFixed(1)} %). After that, the new number will be used for the ration of this one. It is recommended to take ${two(cal.nominal)} mL again to confirm.`);
}

async function resetCalibration(ctx, row) {
  const yes = await ctx.dialog.confirm({title: `clears the correction of ${row.label}`, confirmLabel: "Clear",
    lines: [["now", `per mL ${one(row.dose_pulses_per_ml)} steps`], ["Change back\nCorrection for", `per mL ${one(row.factory_pulses_per_ml)} steps`]]});
  if (!yes) return;
  const reply = await act(ctx, () => ctx.api.post('/api/pump/calibrate/reset', {motor: row.motor}), `${row.label} cleared`);
  if (reply) refreshBoot(ctx);
}

async function calibrateOne(ctx, motor) {
  for (;;) {
    let rows;
    try { rows = await loadCal(ctx); } catch (error) { fail(ctx, error); return; }
    const row = rows.find((item) => item.motor === motor);
    if (!row) return;
    const blocked = doseBlocked(ctx);
    const last = row.calibrated ? `Last time: typed ${two(row.nominal_ml)} mL, actual ${two(row.measured_ml)} mL.` : '';
    // Reading a measuring cylinder takes longer than a dialog usually waits.
    const pick = await ctx.dialog.choose({title: `${row.label} Correction`, wide: true, columns: 1, timeout: CAL_WAIT_S, cancelLabel: "Close",
      detail: `${calText(row)}.${last}`,
      choices: [{value: 'dose', label: `① hit ${two(cal.nominal)} mL`, small: "Put the measuring cylinder first at the outlet; the pump will stop automatically after turning.", disabled: blocked || false},
        {value: 'enter', label: "② Enter the actual number of mL to be printed", small: "The number read on the measuring cylinder"},
        {value: 'reset', label: "Clear the calibration of this one", small: "Return to original settings", disabled: row.calibrated ? false : "has not been calibrated yet"}]});
    if (pick === null) return;
    if (pick === 'dose') await doseForCalibration(ctx, row);
    else if (pick === 'enter') await enterMeasured(ctx, row);
    else await resetCalibration(ctx, row);
  }
}

async function choosePump(ctx) {
  for (;;) {
    let rows;
    try { rows = await loadCal(ctx); } catch (error) { fail(ctx, error); return; }
    const motor = await ctx.dialog.choose({title: "pump calibration: choose one", wide: true, columns: 2, timeout: CAL_WAIT_S, cancelLabel: "Close",
      detail: `is individually calibrated: type ${two(cal.nominal)} mL, measure out the actual amount, and enter it.`,
      choices: rows.map((row) => ({value: row.motor, label: row.label, small: calText(row), current: Boolean(row.calibrated)}))});
    if (motor === null) return;
    await calibrateOne(ctx, motor);
  }
}

// ── the module ──────────────────────────────────────────────────────────────────────────────────────────────
export function mount(root, ctx) {
  const {h} = ctx.dom;
  const factRows = facts(ctx).map(([label]) => kvRow(ctx, label));
  const missing = h('div.adm-note', {hidden: factRows.length > 0});
  const size = gbtn(ctx, "syringe capacity", '', () => { chooseSize(ctx); });
  const speed = gbtn(ctx, "syringe pump speed", '', () => { chooseSpeed(ctx); });
  const carries = gbtn(ctx, "What to connect to the pump?", '', () => { editPumps(ctx); });
  const valves = gbtn(ctx, "solenoid valve linkage", '', () => { editValves(ctx); });
  const left = h('div.panel.adm-half', null,
    h('div.ph', null, h('h2', null, "peristaltic pump and syringe pump")),
    h('div.kv.big.facts', {hidden: factRows.length === 0}, factRows.map((item) => item.row)), missing,
    h('div.adm-note', {hidden: factRows.length === 0}, "The above values come from the configuration file of the independent laboratory v7, which can only be viewed here. Use the buttons below to set what is connected to each pump and whether the solenoid valve should be connected."),
    h('div.btn-grid', null, carries.button, valves.button, size.button, speed.button));

  const tag = h('span.tag');
  const weight = h('div.adm-weight');
  const last = kvRow(ctx, "Last calibration");
  const zero = gbtn(ctx, "reset to zero", "Only empty cups are placed on the scale", () => { tare(ctx); });
  const scaleCal = gbtn(ctx, "scale calibration", "Put the weight and press", () => { calibrate(ctx); });
  const pumps = kvRow(ctx, "pump calibration");
  const pumpCal = gbtn(ctx, "pump calibration", "Each pill: dispense 10 mL, enter the actual amount", () => { choosePump(ctx); });
  const right = h('div.panel.adm-half', null,
    h('div.ph', null, h('h2', null, "Calibration: Scales and Pumps"), h('span.sp'), tag),
    weight,
    h('div.kv.big', null, last.row, pumps.row),
    h('div.adm-note', null, "Scale: ① Press \"Reset to Zero\" when the cup is empty ② Put the weight on ③ Press \"Scale Calibration\". Pump: Press \"Pump Calibration\" to select one, dispense 10 mL and enter the actual amount. The results will be saved."),
    h('div.btn-grid', null, zero.button, scaleCal.button, pumpCal.button));

  root.replaceChildren(h('div.adm-body', null, left, right));
  ui = {missing, factRows, carries, valves, size, speed, tag, weight, last, zero, cal: scaleCal, pumps, pumpCal};
  loadCal(ctx).catch(() => {});
  loadSetup(ctx).catch(() => {});
}

export function show(ctx) {
  if (ctx && ui) {
    loadCal(ctx).catch(() => {});
    loadSetup(ctx).catch(() => {});
  }
}

function setQuality(ctx, node, cls) {
  for (const name of ['q-na', 'q-bad', 'q-run']) ctx.dom.setClass(node, name, name === cls);
}

export function render(state, ctx) {
  const {setText} = ctx.dom;
  const stale = ctx.store.stale;
  const pump = state.pump || {};
  const busy = busyReason(ctx, state);

  // left: the facts follow bootstrap (it is read again after a change), the two setup buttons say what is in force
  const values = facts(ctx);
  ui.factRows.forEach((item, index) => { if (values[index]) setText(item.value, values[index][1]); });
  const rev = ((ctx.store.boot || {}).hardware || {}).rev;
  if (setup.rev !== rev) {                 // the setup was changed somewhere else: what the two buttons say is read again
    setup.rev = rev;
    loadSetup(ctx).catch(() => {});
  }
  const data = setup.data;
  setGbtn(ctx, ui.carries, busy, data ? rolesText(data) : T_NOT_READ);
  setGbtn(ctx, ui.valves, busy, data ? valvesText(data) : T_NOT_READ);
  const now = saved(ctx);
  setGbtn(ctx, ui.size, busy, typeof now.size === 'number' ? `currently ${ml(now.size)} mL` : '');
  setGbtn(ctx, ui.speed, busy, typeof now.speed === 'number' ? `Currently ${num(now.speed)} mL/s` : '');
  if (!ui.missing.hidden) setText(ui.missing, pump.error || "Unable to read the pump control panel configuration file.");

  // right: the scale
  const scale = pump.scale || {};
  const connected = pump.link === 'connected';
  let tagText = "stable";
  let tagClass = '';
  if (stale) { tagText = "? Unknown"; tagClass = 'q-bad'; }
  else if (!connected) { tagText = ctx.labels.pumpText(pump); tagClass = (ctx.labels.pumpClass ? ctx.labels.pumpClass(pump) : '') || 'q-na'; }
  else if (!scale.online) { tagText = "? No response"; tagClass = 'q-bad'; }
  else if (!scale.stable) tagText = "is changing";
  setText(ui.tag, tagText);
  setQuality(ctx, ui.tag, tagClass === 'q-ok' ? '' : tagClass);
  const value = reading(ctx);
  setText(ui.weight, stale ? "? Unknown" : value);
  setQuality(ctx, ui.weight, stale ? 'q-bad' : value === '—' ? 'q-na' : '');
  const when = scale.calibrated_at ? ctx.labels.fmtTime(scale.calibrated_at, 'YYYY-MM-DD') : '';
  setValue(ctx, ui.last, when ? when + (typeof scale.reference_g === 'number' ? `· Weight ${num(scale.reference_g)} g` : '') : "has not been calibrated yet",
    when ? '' : 'q-na');

  const moving = Boolean(pump.cleaning) || (pump.motors || []).some((motor) => motor.state && motor.state !== 'off')
    || Boolean(pump.syringe && pump.syringe.busy);
  const reason = stale ? busy : !connected ? T_NO_BOARD : busy || (moving ? T_MOVING : !scale.online ? T_NO_READING : !scale.stable ? T_UNSTABLE : '');
  setGbtn(ctx, ui.zero, reason);
  setGbtn(ctx, ui.cal, reason);

  // the pumps: how many have a calibration of their own (the list is read when the tab opens and after a change)
  const rows = cal.rows;
  const count = rows ? rows.filter((row) => row.calibrated).length : 0;
  setValue(ctx, ui.pumps, !rows ? '—' : count ? `${rows.length} pixels ${count} pixels corrected` : "are still the original settings.", rows && count ? '' : 'q-na');
  setGbtn(ctx, ui.pumpCal, stale ? busy : '');
}
