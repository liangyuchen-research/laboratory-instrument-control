//flowsteps.js — the step list of scheduled measurements (AMENDMENTS M23): what a round does, written one step after the other.
//
//The person, 2026-10-07: "It should be possible to set step 1, step 2... so that I can write it down myself, and also include the syringe pump in the step options." A step is one
// row of a Recipe ([action, target, value, unit, note], strings); the program keeps the list as the Recipe with the
// fixed id (GET / POST /api/flow) and says what it thinks of it: `view` (a sentence per step), `valid`, `error`,
// `error_step`. Every change is saved at once, also while the list does not make a round yet (a syringe that was
// filled and is not emptied so far): the list dialog then says what is missing, and nothing starts it.
//
// A pump and its amount are always written “Pump 1: 5 mL” — “Pump 1 5 mL” reads as 15 mL (the same person).

const MARKS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳';
const ADD = 'add';
const PARAMETERS = 'parameters';
const T_TITLE = "process";
const T_HINT = "Click to modify, move, copy or delete. Every modification will be saved immediately.";
const T_NONE = "has no steps yet. Click \"+ Add Step\" to get started.";
const T_NOT_YET = "is not available yet:";
const T_FULL = "process has at most {count} steps";
const T_NO_PUMPS = "Unable to read the settings of the pump control panel";
const T_DRAIN_ALONE = "Pump for emptying: do not rotate together with other pumps";

export const mark = (n) => MARKS[n - 1] || `(${n})`;

const num = (value) => {
  const n = Number(value);
  return value !== '' && Number.isFinite(n) ? String(Math.round(n * 1000) / 1000) : '—';
};
const pumps = (data) => (data && data.pumps) || [];
const pumpAt = (data, target) => pumps(data).find((pump) => `P${pump.pump}` === target || `M${pump.motor}` === target) || null;
const pumpName = (data, target) => { const pump = pumpAt(data, target); return pump ? pump.label : target || '?'; };
const valveName = (target) => `valve position ${String(target || '').replace(/^CH/, '') || '?'}`;
const limit = (data, action) => ((data && data.limits) || {})[action] || {min: 0, max: 1000, decimals: 2, unit: ''};

//One step in the words of this page. `plasma`: what a discharge is now (“30 V · 10 sheets”), '' while not known.
export function stepText(row, data, plasma) {
  const [action, target, value] = row;
  if (action === 'dose_volume') return `${pumpName(data, target)}: ${num(value)} mL`;
  if (action === 'dose_weight') return `${pumpName(data, target)}: Stop with scale ${num(value)} g`;
  if (action === 'dose_together') {
    const amounts = String(value).split('+');
    return "fight together" + String(target).split('+').map((name, index) => `${pumpName(data, name)}: ${num(amounts[index])} mL`).join('＋');
  }
  if (action === 'plasma_run') return `⚡ Make plasma + take spectrum ${plasma ? ` (${plasma}) ` : ''}`;
  if (action === 'clean') return "automatic cleaning";
  if (action === 'delay') return `wait ${num(value)} seconds`;
  if (action === 'syringe_init') return "syringe pump: initialization";
  if (action === 'syringe_speed') return `syringe pump: speed ${num(value)} mL/s`;
  if (action === 'syringe_aspirate') return `syringe pump: pump ${valveName(target)}${num(value)} mL from `;
  if (action === 'syringe_dispense') return `syringe pump: Inject ${valveName(target)}${num(value)} mL into `;
  return String(action);
}

//All steps on one line for the tile: “① Pump 1 · Water: 5 mL | ② Pump 2: 25 mL | ③ ⚡ Plasma + spectrum (30 V · 10 pictures)”.
export function stepsLine(data, plasma) {
  return ((data && data.steps) || []).map((row, index) => `${mark(index + 1)} ${stepText(row, data, plasma)}`).join(' ');
}

// ── the dialogs of one step (cancel anywhere = nothing changes) ─────────────────────────────────────────────
async function askPump(ctx, data, title, current) {
  const list = pumps(data);
  if (!list.length) { await ctx.dialog.message(title, T_NO_PUMPS, {level: 'error'}); return null; }
  const now = pumpAt(data, current);
  return ctx.dialog.choose({title, wide: true, columns: 2, value: now ? `P${now.pump}` : undefined,
    choices: list.map((pump) => ({value: `P${pump.pump}`, label: pump.label, small: pump.drain ? "for emptying" : pump.water ? "water for cleaning" : ''}))});
}

async function askAmount(ctx, data, action, title, current) {
  const range = limit(data, action);
  const value = await ctx.dialog.pad({title, min: range.min, max: range.max, decimals: range.decimals, unit: range.unit,
    value: current !== undefined && current !== '' && Number.isFinite(Number(current)) ? Number(current) : ''});
  return value === null ? null : String(value);
}

//Use two pumps together: several pumps, each with its own mL -> ['P1+P8', '10+2'], or null.
async function askTogether(ctx, data, target, value) {
  const DONE = 'done';
  const list = pumps(data);
  const range = limit(data, 'dose_together');
  const amounts = String(value || '').split('+');
  const chosen = new Map();
  String(target || '').split('+').forEach((name, index) => {
    const pump = pumpAt(data, name);
    if (pump && !pump.drain && Number.isFinite(Number(amounts[index])) && amounts[index] !== '') chosen.set(pump.pump, Number(amounts[index]));
  });
  for (;;) {
    const picked = await ctx.dialog.choose({title: "play together: click a pump to set", wide: true, columns: 2, timeout: 120,
      detail: "At least two. Pumps 1 to 4 and Pumps 5 to 8 will rotate together; those in the same group will rotate next.",
      choices: list.map((pump) => ({value: pump.pump, label: pump.label, current: chosen.has(pump.pump),
        disabled: pump.drain ? T_DRAIN_ALONE : false,
        small: chosen.has(pump.pump) ? `${num(chosen.get(pump.pump))} mL` : "Do not fight"})),
      actions: [{label: "Complete", value: DONE}]});
    if (picked === null) return null;
    if (picked === DONE) {
      if (chosen.size < 2) {
        await ctx.dialog.message("fight together", "At least two pumps must be selected. There is only one pill, please use \"Pump: How many mL\".");
        continue;
      }
      const order = list.filter((pump) => chosen.has(pump.pump));
      return [order.map((pump) => `P${pump.pump}`).join('+'), order.map((pump) => String(chosen.get(pump.pump))).join('+')];
    }
    const pump = list.find((item) => item.pump === picked);
    if (chosen.has(picked)) {
      const what = await ctx.dialog.choose({title: pump.label, value: 'set',
        choices: [{value: 'set', label: "Change quantity"}, {value: 'off', label: "Don’t hit this one"}]});
      if (what === null) continue;
      if (what === 'off') { chosen.delete(picked); continue; }
    }
    const amount = await ctx.dialog.pad({title: `${pump.label}: How many mL should be given?`, min: range.min, max: range.max, decimals: range.decimals,
      unit: 'mL', value: chosen.has(picked) ? chosen.get(picked) : ''});
    if (amount !== null) chosen.set(picked, amount);
  }
}

async function askValve(ctx, data, title, current) {
  const count = Number(data.valve_positions) || 0;
  if (!count) { await ctx.dialog.message(title, T_NO_PUMPS, {level: 'error'}); return null; }
  return ctx.dialog.choose({title, wide: true, columns: 2, value: current || undefined,
    choices: Array.from({length: count}, (_, index) => ({value: `CH${index + 1}`, label: `valve position ${index + 1}`}))});
}

// The numbers of a step, asked anew (base = the row as it stands, or a row with only its action).
// -> the row, or null when a dialog was cancelled.
async function askRow(ctx, data, base) {
  const [action, target, value, , note] = base;
  if (action === 'dose_volume' || action === 'dose_weight') {
    const weigh = action === 'dose_weight';
    const pump = await askPump(ctx, data, weigh ? "Stop with scale: choose a pump" : "How many mL to give: Choose a pump", target);
    if (pump === null) return null;
    const amount = await askAmount(ctx, data, action, `${pumpName(data, pump)}: ${weigh ? "How many more grams does the scale stop?" : "How many mL?"}`, value);
    return amount === null ? null : [action, pump, amount, weigh ? 'g' : 'mL', note];
  }
  if (action === 'dose_together') {
    const picked = await askTogether(ctx, data, target, value);
    return picked === null ? null : [action, picked[0], picked[1], 'mL', note];
  }
  if (action === 'delay') {
    const amount = await askAmount(ctx, data, action, "Wait a few seconds", value);
    return amount === null ? null : [action, '', amount, 's', note];
  }
  if (action === 'syringe_speed') {
    const amount = await askAmount(ctx, data, action, "syringe pump speed", value);
    return amount === null ? null : [action, '', amount, 'mL/s', note];
  }
  if (action === 'syringe_aspirate' || action === 'syringe_dispense') {
    const pull = action === 'syringe_aspirate';
    const valve = await askValve(ctx, data, pull ? "Liquid pumping: from which valve position" : "Liquid injection: to which valve position", target);
    if (valve === null) return null;
    const amount = await askAmount(ctx, data, action, `${valveName(valve)}: ${pull ? "pump" : "Note"} How many mL`, value);
    return amount === null ? null : [action, valve, amount, 'mL', note];
  }
  return [action, '', '', '', note];           //plasma pump, automatic cleaning, syringe pump initialization: nothing to ask
}

//“＋Add step”: which kind -> the new row, or null.
async function askNew(ctx, data, plasma) {
  const seconds = data.clean_seconds ? `· About ${Math.round(data.clean_seconds)} seconds` : '';
  const none = pumps(data).length ? false : T_NO_PUMPS;
  const kind = await ctx.dialog.choose({title: "New step: What to do", wide: true, columns: 1, choices: [
    {value: 'dose_volume', label: "pump: how many mL to dispense", small: "Select a pump and turn a fixed amount. Use this to add liquid, move to another tank, and drain.", disabled: none},
    {value: 'dose_together', label: "two pumps at the same time", small: "Each mL of each, transferred at the same time", disabled: none},
    {value: 'dose_weight', label: "pump: stop with scale", small: "Stopped when there were so many extra grams on the scale.", disabled: none || (data.weighing ? false : "configuration file does not enable weighing and quantification")},
    {value: 'plasma_run', label: "⚡ Make plasma + take spectrum", small: plasma || "contains high voltage"},
    {value: 'clean', label: "automatic cleaning", small: `Water in and then discharged ${seconds}`, disabled: none},
    {value: 'delay', label: "waiting", small: "Wait a few seconds before doing the next step"},
    {value: 'syringe', label: "syringe pump", small: "pumping, filling, initialization, speed", disabled: Number(data.valve_positions) ? false : T_NO_PUMPS}]});
  if (kind === null) return null;
  let action = kind;
  if (kind === 'syringe') {
    action = await ctx.dialog.choose({title: "Syringe Pump: What to do", wide: true, choices: [
      {value: 'syringe_aspirate', label: "Liquid extraction", small: "draws the syringe from one valve position"},
      {value: 'syringe_dispense', label: "injection", small: "is pushed out from the syringe to a valve position"},
      {value: 'syringe_init', label: "initialization", small: "Do this before using the syringe pump for the first time in each round"},
      {value: 'syringe_speed', label: "speed\nUse this speed for liquid extraction and injection after", small: "Use this speed for subsequent pumping and injection."}]});
    if (action === null) return null;
  }
  return askRow(ctx, data, [action, '', '', '', '']);
}

//The rows with `row` put in at `at`; a move of the syringe that has no initialization before it gets one.
function insert(rows, at, row) {
  const next = rows.slice();
  const moves = row[0] === 'syringe_aspirate' || row[0] === 'syringe_dispense';
  if (moves && !next.slice(0, at).some((item) => item[0] === 'syringe_init')) next.splice(at, 0, ['syringe_init', '', '', '', '']);
  next.splice(moves && next.length > rows.length ? at + 1 : at, 0, row);
  return next;
}

// What a tap on a step offers. -> the new rows (and the step to show), or null when nothing changes.
async function stepMenu(ctx, io, data, at, plasma) {
  const rows = data.steps;
  const row = rows[at];
  const action = row[0];
  const fixed = action === 'clean' || action === 'syringe_init';
  const full = rows.length >= (data.max_steps || 60) ? T_FULL.replace('{count}', data.max_steps || 60) : false;
  const what = await ctx.dialog.choose({title: `step ${at + 1}: ${stepText(row, data, plasma)}`, wide: true, columns: 2, choices: [
    action === 'plasma_run' ? {value: PARAMETERS, label: "change plasma parameters", small: plasma} : {value: 'edit', label: "modified", disabled: fixed ? "There is no number in this step" : false},
    {value: 'note', label: "Remarks", small: row[4] || "Write down what this step does (optional)"},
    {value: 'up', label: "Move forward", disabled: at === 0}, {value: 'down', label: "Move back", disabled: at === rows.length - 1},
    {value: 'before', label: "insert one step before it", disabled: full}, {value: 'copy', label: "Copy this step", disabled: full},
    {value: 'delete', label: "Delete this step\nRemarks for"}]});
  if (what === null) return null;
  const next = rows.slice();
  if (what === PARAMETERS) { await io.editParameters(); return null; }
  if (what === 'edit') {
    const changed = await askRow(ctx, data, row);
    if (changed === null) return null;
    next[at] = changed;
    return {rows: next, at};
  }
  if (what === 'note') {
    const note = await ctx.dialog.text({title: `step ${at + 1}`, value: row[4], maxLength: 40, rule: "can be left blank; for example: move to the plasma tank", allowEmpty: true});
    if (note === null) return null;
    next[at] = [row[0], row[1], row[2], row[3], note];
    return {rows: next, at};
  }
  if (what === 'up' || what === 'down') {
    const to = at + (what === 'up' ? -1 : 1);
    [next[at], next[to]] = [next[to], next[at]];
    return {rows: next, at: to};
  }
  if (what === 'copy') { next.splice(at + 1, 0, row.slice()); return {rows: next, at: at + 1}; }
  if (what === 'delete') { next.splice(at, 1); return {rows: next, at: Math.min(at, next.length - 1)}; }
  const added = await askNew(ctx, data, plasma);                   // 'before'
  if (added === null) return null;
  const with1 = insert(rows, at, added);
  return {rows: with1, at: at + with1.length - rows.length - 1};
}

//The list: step 1, step 2, … io = {load() -> the reply of GET /api/flow or null, save(steps) -> the reply or null,
//editParameters(), plasma() -> “30 V · 10 pictures”}.
Steps in the
export async function editSteps(ctx, io) {
  let at = -1;                                   // the step touched last: the list opens at its page
  for (;;) {
    const data = await io.load();
    if (!data) return;
    const rows = data.steps || [];
    const plasma = io.plasma();
    const bad = data.valid ? 0 : data.error_step || 0;
    const most = data.max_steps || 60;
    const picked = await ctx.dialog.choose({title: `${T_TITLE} (${rows.length} steps in total)`, wide: true, columns: 1, left: true, timeout: 120,
      cancelLabel: "Complete", detail: rows.length ? T_HINT : T_NONE,
      warn: !data.valid && rows.length ? T_NOT_YET + data.error : '',
      choices: rows.map((row, index) => ({value: index, label: `Step ${index + 1}  ${stepText(row, data, plasma)}`,
        small: index + 1 === bad ? `⚠ This step needs to be changed` + (row[4] ? ` · ${row[4]}` : '') : row[4] || '', current: index === at})),
      actions: [{label: "+ New steps", value: ADD}, data.uses_plasma ? {label: "plasma parameters", value: PARAMETERS} : null]});
    if (picked === null) return;
    if (picked === PARAMETERS) { await io.editParameters(); continue; }
    let change = null;
    if (picked === ADD) {
      if (rows.length >= most) { await ctx.dialog.message(T_TITLE, T_FULL.replace('{count}', most)); continue; }
      const added = await askNew(ctx, data, plasma);
      if (added !== null) { const next = insert(rows, rows.length, added); change = {rows: next, at: next.length - 1}; }
    } else {
      at = picked;
      change = await stepMenu(ctx, io, data, picked, plasma);
    }
    if (change && await io.save(change.rows)) at = change.at;
  }
}
