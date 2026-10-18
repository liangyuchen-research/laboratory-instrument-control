//schedule.js — Schedule measurement (SPEC E.5.1, E.5.2; AMENDMENTS M5.4).
//
// Left: the schedule form as six tiles. A tap opens a dialog; the answer goes into store.form (store.setForm), which
// the frame keeps in this PC's browser and asks the program about (store.preview: first slot, count, estimate,
// warnings). While a schedule is active the tiles show that schedule and cannot be changed.
// Right: the slots of the present / last schedule, newest first.
//The start dialog and start schedule / pause / stop belong to the frame; this page starts nothing.
//
//AMENDMENTS M16, M23: besides a Recipe of the maintenance pages, a round can be written here step by step (Step 1,
//Step 2, …: a pump and its mL, two pumps together, a pump that stops on the scale, the discharge, the cleaning, a
// wait, the syringe pump). The program keeps the steps as one Recipe with a fixed id (GET / POST /api/flow); the
// tile under the first one lists them and a tap opens the list (flowsteps.js).

//AMENDMENTS M17: the measurement tile (measurement content; in a round written here, the step “Plasma making” and the “Plasma parameters” button of
// the step list) says the plasma parameters and a tap changes
// them — without the maintenance PIN (the person's decision); the program refuses while a schedule is busy.
import { edit as editPlasma, summary as plasmaSummary, text as plasmaText, valuesOf as plasmaValues } from '../plasma.js';
import { editSteps, stepsLine } from '../flowsteps.js';

const ACTIVE = ['waiting', 'running', 'pausing', 'paused', 'stopping'];
const SLOT_ROWS = 9;
const RETRY_MS = 5000;
const SLOT_CLASS = {running: 'running', done: 'completed', failed: 'failed', not_started: 'failed', stopped: 'stopped',
  skipped_overrun: 'skipped', skipped_paused: 'skipped'};

let el = null;
let shown = false;
let boxes = [];                 // what the boxes under the estimate show now: [{kind, text, all}]
const slotFacts = new WeakMap();   // row of the slot log -> the slot it shows
const recipes = {rev: -1, rows: null, pending: null, failedAt: 0};

const two = (n) => String(n).padStart(2, '0');
const amount = (value) => String(Math.round(Number(value) * 100) / 100);
const intervalText = (minutes) => (minutes >= 60 && minutes % 60 === 0 ? `every ${minutes / 60} hour` : `every ${minutes} minutes`);
// “Pump 1: 5 mL”, never “Pump 1 5 mL” (that reads as 15 mL)
const liquidText = (list) => (list || []).map((item) => (item.g !== null && item.g !== undefined
  ? `${item.label}: ${amount(item.g)} g`
  : `${item.label}: ${item.approx ? "approx." : ''}${amount(item.ml)} mL`)).join(' · ');

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function setHidden(node, hidden) {
  if (node.hidden !== hidden) node.hidden = hidden;
}

function setTitle(node, text) {
  const value = text || '';
  if ((node.getAttribute('title') || '') === value) return;
  if (value) node.setAttribute('title', value);
  else node.removeAttribute('title');
}

function again(ctx) {
  if (shown && ctx.store.state) render(ctx.store.state, ctx);
}

// ── the published Recipes (GET /api/recipes; asked again when state.revs.recipes changes) ────────────────────
function loadRecipes(ctx, rev) {
  if (!recipes.pending) {
    recipes.pending = ctx.api.get('/api/recipes', {timeout: 8000}).then((reply) => {
      recipes.rows = Array.isArray(reply.recipes) ? reply.recipes : [];
      recipes.rev = rev;
      recipes.failedAt = 0;
      return recipes.rows;
    }, (error) => {
      recipes.failedAt = Date.now();
      throw error;
    }).finally(() => {
      recipes.pending = null;
      again(ctx);
    });
  }
  return recipes.pending;
}

function wantRecipes(ctx, rev) {
  if (recipes.pending || (recipes.rows && recipes.rev === rev)) return;
  if (Date.now() - recipes.failedAt < RETRY_MS) return;
  loadRecipes(ctx, rev).catch(() => {});
}

// The list for a dialog: what is known when it is current, else asked now.
async function recipesNow(ctx) {
  const rev = (ctx.store.state && ctx.store.state.revs && ctx.store.state.revs.recipes) || 0;
  if (recipes.rows && recipes.rev === rev && !recipes.pending) return recipes.rows;
  try {
    return await loadRecipes(ctx, rev);
  } catch (error) {
    ctx.message("Recipe list reading failed:" + error.message, 'error');
    return null;
  }
}

const recipeRow = (id) => (recipes.rows || []).find((row) => row.id === id) || null;

// ── the flow of this page (GET / POST /api/flow) ────────────────────────────────────────────────────────────
const FLOW_ID = 'f10' + '0'.repeat(29);          // the Recipe the program keeps for it (store.FLOW_RECIPE_ID)
const FLOW_CHOICE = 'flow';
const T_FLOW = "Set by yourself";
const T_FLOW_SMALL = "Write step by step on this page: pump a few mL, pump plasma, clean, syringe pump";
const T_STEPS_NONE = "No steps yet";
const T_STEPS_TAP = "Click here to write the steps";
const flow = {data: null, rev: -1, pending: null, failedAt: 0};

function loadFlow(ctx, rev) {
  if (!flow.pending) {
    flow.pending = ctx.api.get('/api/flow', {timeout: 8000}).then((reply) => {
      flow.data = reply;
      flow.rev = rev;
      flow.failedAt = 0;
      return reply;
    }, (error) => {
      flow.failedAt = Date.now();
      throw error;
    }).finally(() => {
      flow.pending = null;
      again(ctx);
    });
  }
  return flow.pending;
}

function wantFlow(ctx, rev) {
  if (flow.pending || (flow.data && flow.rev === rev)) return;
  if (Date.now() - flow.failedAt < RETRY_MS) return;
  loadFlow(ctx, rev).catch(() => {});
}

async function flowNow(ctx) {
  if (flow.data && !flow.pending) return flow.data;
  try {
    return await loadFlow(ctx, (ctx.store.state && ctx.store.state.revs && ctx.store.state.revs.recipes) || 0);
  } catch (error) {
    ctx.message("Process reading failed:" + error.message, 'error');
    return null;
  }
}

// Saves the steps; the program keeps them as the Recipe with the fixed id and this form chooses it.
// -> what the program says about them now (the reply of GET /api/flow), or null when they were not saved.
async function saveFlow(ctx, steps) {
  let reply;
  try {
    reply = await ctx.api.post('/api/flow', {steps});
  } catch (error) {
    await ctx.dialog.message("process is not saved", error.message, {level: 'error'});
    return null;
  }
  flow.data = reply;
  // The same Recipe with other steps: chosen again, so that the frame asks the program what it means now.
  if (ctx.store.form.recipe_id === reply.recipe_id) ctx.store.setForm({recipe_id: ''});
  ctx.store.setForm({recipe_id: reply.recipe_id});
  again(ctx);
  return reply;
}

//What one discharge is now, for a step that says "plasma": "30 V · 10 sheets". '' while the parameters are not known.
function plasmaShort(ctx) {
  const values = plasmaValues(ctx.store.state, ctx.store.stale);
  return values ? `${plasmaText(values.Voltage)} V · ${plasmaText(values.Loopnum)} Zhang` : '';
}

// The seven plasma parameters (M17). After a change the program is asked again what the form means (its estimate).
async function editParameters(ctx) {
  const reply = await editPlasma(ctx);
  if (!reply) return;
  const id = ctx.store.form.recipe_id;
  if (id) {
    ctx.store.setForm({recipe_id: ''});
    ctx.store.setForm({recipe_id: id});
  }
  again(ctx);
}

//Step 1, Step 2, …: the list and its dialogs are flowsteps.js.
function editFlow(ctx) {
  return editSteps(ctx, {load: () => flowNow(ctx), save: (steps) => saveFlow(ctx, steps),
    editParameters: () => editParameters(ctx), plasma: () => plasmaShort(ctx)});
}

function recipeFacts(steps, plasma, pump) {
  return [steps ? `${steps} steps` : null, plasma ? "contains high voltage plasma" : "does not contain plasma", pump ? "uses pump" : "does not require a pump"].filter(Boolean).join(' · ');
}

// ── editing: every value through a dialog ───────────────────────────────────────────────────────────────────
async function clock(ctx, word, time) {
  const [was, wasMinute] = String(time || '00:00').split(':').map(Number);
  const hour = await ctx.dialog.pad({title: `${word} Time: hour`, min: 0, max: 23, decimals: 0, value: was});
  if (hour === null) return null;
  const minute = await ctx.dialog.pad({title: `${word} Time: minutes`, min: 0, max: 59, decimals: 0, value: wasMinute});
  if (minute === null) return null;
  return `${two(hour)}:${two(minute)}`;
}

async function editRecipe(ctx) {
  const rows = await recipesNow(ctx);
  if (!rows) return;
  const chosen = ctx.store.form.recipe_id;
  const id = await ctx.dialog.choose({title: "What to do this round", wide: true, value: chosen === FLOW_ID ? FLOW_CHOICE : chosen,
    choices: [{value: FLOW_CHOICE, label: T_FLOW, small: T_FLOW_SMALL},
      ...rows.filter((row) => row.id !== FLOW_ID).map((row) => ({value: row.id, label: row.name,
        small: recipeFacts(row.step_count, row.uses_plasma, row.uses_pump),
        disabled: row.valid ? false : row.error || "Recipe failed the check"}))]});
  if (id === null) return;
  if (id !== FLOW_CHOICE) { ctx.store.setForm({recipe_id: id}); return; }
  const data = await flowNow(ctx);
  if (!data) return;
  if (data.recipe_id) ctx.store.setForm({recipe_id: data.recipe_id});
  else await saveFlow(ctx, data.steps);            // the first time: the steps as they stand (only the measurement)
}

async function editStart(ctx) {
  const form = ctx.store.form;
  const mode = await ctx.dialog.choose({title: "Start method", value: form.start.mode,
    choices: [{value: 'now', label: "Start now"}, {value: 'at', label: "specified time"}]});
  if (mode === null) return;
  if (mode === 'now') { ctx.store.setForm({start: {mode: 'now'}}); return; }
  const time = await clock(ctx, "Start", form.start.time);
  if (time !== null) ctx.store.setForm({start: {mode: 'at', time}});
}

async function editInterval(ctx) {
  const minutes = await ctx.dialog.pad({title: "measurement interval", min: 1, max: 1440, decimals: 0, unit: "minutes", value: ctx.store.form.interval_minutes});
  if (minutes !== null) ctx.store.setForm({interval_minutes: minutes});
}

async function editEnd(ctx) {
  const form = ctx.store.form;
  const mode = await ctx.dialog.choose({title: "end condition", wide: true, value: form.end.mode,
    choices: [{value: 'count', label: "specified times"}, {value: 'until', label: "to the specified time"}, {value: 'continuous', label: "continues until stop is pressed"}]});
  if (mode === null) return;
  if (mode === 'count') {
    const count = await ctx.dialog.pad({title: "Measurement times", min: 1, max: 1000, decimals: 0, value: form.end.count});
    if (count !== null) ctx.store.setForm({end: {mode: 'count', count}});
  } else if (mode === 'until') {
    const time = await clock(ctx, "End", form.end.time);
    if (time !== null) ctx.store.setForm({end: {mode: 'until', time}});
  } else {
    ctx.store.setForm({end: {mode: 'continuous'}});
  }
}

async function editBlank(ctx) {
  const rows = await recipesNow(ctx);
  if (!rows) return;
  const form = ctx.store.form;
  const usable = rows.filter((row) => row.uses_plasma && row.id !== form.recipe_id);
  const id = await ctx.dialog.choose({title: "Blank Recipe", wide: true, value: form.blank.enabled ? form.blank.recipe_id : '',
    choices: [{value: '', label: "does not insert blanks"}, ...usable.map((row) => ({value: row.id, label: row.name,
      small: recipeFacts(row.step_count, row.uses_plasma, row.uses_pump),
      disabled: row.valid ? false : row.error || "Recipe failed the check"}))]});
  if (id === null) return;
  if (id === '') { ctx.store.setForm({blank: {enabled: false}}); return; }
  const every = await ctx.dialog.pad({title: "Insert blanks every few samples", min: 1, max: 100, decimals: 0, value: form.blank.every});
  if (every !== null) ctx.store.setForm({blank: {enabled: true, recipe_id: id, every}});
}

// ── mount ───────────────────────────────────────────────────────────────────────────────────────────────────
function tile(h, tag, label, extra) {
  const value = h('b');
  const small = h('small');
  const box = h(tag, {class: 'stile' + (extra ? ` ${extra}` : '')}, h('span', null, label), value, small);
  return {box, value, small, label};
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  const edit = (action) => () => { action(ctx); };
  const tiles = {
    recipe: tile(h, 'button', "process", 'both'),
    steps: tile(h, 'button', "steps", 'stage steps'),
    start: tile(h, 'button', "Start"),
    interval: tile(h, 'button', "measurement interval"),
    end: tile(h, 'button', "end condition"),
    blank: tile(h, 'button', "blank solution"),
    content: tile(h, 'button', "measurement content", 'both'),
  };
  tiles.recipe.box.addEventListener('click', edit(editRecipe));
  tiles.steps.box.addEventListener('click', edit(editFlow));
  tiles.content.box.addEventListener('click', edit(editParameters));
  const stages = h('div.sch-stages', {hidden: true}, tiles.steps.box);
  tiles.start.box.addEventListener('click', edit(editStart));
  tiles.interval.box.addEventListener('click', edit(editInterval));
  tiles.end.box.addEventListener('click', edit(editEnd));
  tiles.blank.box.addEventListener('click', edit(editBlank));

  const formSub = h('span.sub');
  const est = h('div.sch-est');
  const notes = h('div.sch-notes');
  const logTitle = h('h2');
  const logSub = h('span.sub');
  const logCounts = h('span.meta');
  const tbody = h('div.tbody');
  const emptyTitle = h('b');
  const emptyText = h('p');
  const empty = h('div.empty', {hidden: true}, emptyTitle, emptyText);
  const pager = h('div');

  const grid = h('div.sch-grid', null, tiles.recipe.box, stages, tiles.start.box, tiles.interval.box, tiles.end.box, tiles.blank.box,
    tiles.content.box);
  el = {tiles, stages, grid, formSub, est, notes, logTitle, logSub, logCounts, tbody, empty, emptyTitle, emptyText, pager};
  root.replaceChildren(h('div.sch-body', null,
    h('div.panel.sch-left', null,
      h('div.ph', null, h('h2', null, "schedule setting"), formSub),
      grid,
      est, notes),
    h('div.panel.sch-right.table', null,
      h('div.ph', null, logTitle, logSub, h('span.sp'), logCounts),
      h('div.thead', null, h('span.c-slot', null, "sequence"), h('span.c-plan', null, "scheduled time"), h('span.c-sst', null, "status"),
        h('span.c-hm', null, "Start"), h('span.c-hm', null, "End"), h('span.c-msg', null, "Remarks")),
      tbody, empty, pager)));
}

export function show() { shown = true; }
export function hide() { shown = false; }

// ── what the tiles show ─────────────────────────────────────────────────────────────────────────────────────
// The schedule that is running, as the same fields the form has.
function scheduleView(sched) {
  const spec = sched.spec || {};
  const start = spec.start || {};
  const end = spec.end || {};
  const row = recipeRow(sched.recipe && sched.recipe.id);
  return {
    recipe: (sched.recipe && sched.recipe.name) || '',
    flow: Boolean(sched.recipe && sched.recipe.id === FLOW_ID),
    steps: row ? row.step_count : null,
    plasma: Boolean(sched.uses_plasma), rpi: Boolean(sched.uses_rpi), pump: Boolean(sched.uses_pump),
    volts: sched.voltage_v,
    start: {mode: start.mode === 'at' ? 'at' : 'now', time: start.time || ''},
    interval: spec.interval_minutes,
    end: {mode: end.mode || 'count', count: end.count, time: end.time || ''},
    blank: sched.blank ? {name: sched.blank.recipe_name || '', every: sched.blank.every} : null,
  };
}

// The form, with what is known about its Recipe (the program's preview first, else the list).
function formView(ctx, state) {
  const form = ctx.store.form;
  const preview = ctx.store.preview.data;
  const row = recipeRow(form.recipe_id);
  const told = preview && preview.recipe && preview.recipe.id === form.recipe_id ? preview.recipe : null;
  const blankRow = form.blank.enabled ? recipeRow(form.blank.recipe_id) : null;
  const known = Boolean(told || row);
  const needs = (told && preview.needs) || {};
  const values = (state.rpi && state.rpi.parameters && state.rpi.parameters.values) || {};
  return {
    recipe: form.recipe_id ? (told && told.name) || (row && row.name) || '' : '',
    flow: form.recipe_id === FLOW_ID,
    steps: (told && told.step_count) || (row && row.step_count) || null,
    known,
    // a Recipe is chosen and the program has not said yet what it is (the page has just opened)
    loading: Boolean(form.recipe_id) && !known && (recipes.rows === null || ctx.store.preview.pending),
    plasma: Boolean((told && (told.uses_plasma || needs.high_voltage)) || (row && row.uses_plasma) || (blankRow && blankRow.uses_plasma)),
    rpi: Boolean((told && told.uses_rpi) || (row && row.uses_rpi) || (blankRow && blankRow.uses_rpi)),
    pump: Boolean((told && told.uses_pump) || (row && row.uses_pump)),
    volts: told && preview.voltage_v !== null && preview.voltage_v !== undefined ? preview.voltage_v : values.Voltage,
    start: form.start,
    interval: form.interval_minutes,
    end: form.end,
    blank: form.blank.enabled && form.blank.recipe_id
      ? {name: (preview && preview.blank && preview.blank.recipe_name) || (blankRow && blankRow.name) || '', every: form.blank.every} : null,
  };
}

function fill(ctx, item, value, small, cls) {
  const {dom} = ctx;
  dom.setText(item.value, value);
  dom.setText(item.small, small);
  setTitle(item.box, `${item.label}: ${value}` + (small ? `\n${small}` : ''));
  for (const name of ['need', 'hv', 'na']) dom.setClass(item.box, name, name === cls);
}

function renderTiles(ctx, view, locked) {
  const {tiles} = el;
  const once = view.end.mode === 'count' && view.end.count === 1;
  if (view.loading) fill(ctx, tiles.recipe, '…', '');
  else {
    //the flow's own name is “Flow: Add liquid → Measure...”: the tile is called flow already
    fill(ctx, tiles.recipe, view.recipe ? view.recipe.replace(/^process: /, '') : "Please select",
      view.recipe && view.known !== false ? recipeFacts(view.steps, view.plasma, view.pump) : '', view.recipe ? '' : 'need');
  }
  // The steps, when the round is the one written on this page.
  const staged = Boolean(view.flow && flow.data);
  setHidden(el.stages, !staged);
  setHidden(tiles.content.box, staged);
  el.grid.classList.toggle('staged', staged);       // lower tiles: the form keeps its height
  if (staged) {
    const data = flow.data;
    const count = (data.steps || []).length;
    // every step in its order on up to four lines; the line under them says how many, or what is still wrong
    fill(ctx, tiles.steps, count ? stepsLine(data, plasmaShort(ctx)) : T_STEPS_NONE,
      !count ? T_STEPS_TAP : !data.valid ? `is not available yet: ${data.error}` : locked ? `total ${count} steps` : `Total ${count} steps · Click here to modify`,
      !count || !data.valid ? 'need' : '');
  }
  fill(ctx, tiles.start, view.start.mode === 'at' ? `${view.start.time} Start` : "Start now", view.start.mode === 'at' ? "If this time has passed, it will be tomorrow" : '');
  fill(ctx, tiles.interval, intervalText(view.interval), once ? "Not used when measuring only once" : "is calculated based on the starting time of each time");
  fill(ctx, tiles.end,
    view.end.mode === 'count' ? `Total ${view.end.count} times` : view.end.mode === 'until' ? `to ${view.end.time}` : "continues until stop is pressed",
    view.end.mode === 'count' ? "Just measure once and choose 1 time" : view.end.mode === 'until' ? '' : "Press STOP to end");
  fill(ctx, tiles.blank, view.blank ? `per ${view.blank.every} samples` : "not inserted", view.blank ? `${view.blank.name} · Enter blank first time` : "Regularly confirm the baseline");
  if (!view.recipe || view.known === false) fill(ctx, tiles.content, '—', '', 'na');
  else if (view.plasma) {
    const volts = view.volts === null || view.volts === undefined ? '—' : view.volts;
    // the parameters of the RPi as they are now; the voltage is the one this schedule was started with
    const params = view.params ? plasmaSummary({...view.params, Voltage: typeof view.volts === 'number' ? view.volts : view.params.Voltage}) : '';
    fill(ctx, tiles.content, "⚡ Contains high-voltage plasma", params || `${volts} V`, 'hv');
  } else fill(ctx, tiles.content, view.rpi ? "includes spectrum measurement" : "Only add liquid", "does not output high voltage");
  for (const key of ['recipe', 'steps', 'start', 'interval', 'end', 'blank', 'content']) {
    if (tiles[key].box.disabled !== locked) tiles[key].box.disabled = locked;
  }
}

// ── estimate lines and message boxes ────────────────────────────────────────────────────────────────────────
function estimateRows(ctx, state, sched, active, calibration) {
  const {labels} = ctx;
  if (calibration) return [];
  if (active) {
    const first = sched.spec && sched.spec.first_slot ? new Date(sched.spec.first_slot) : null;
    return [["status", labels.schedule[sched.status] || sched.status],
      ["first measurement", first ? labels.fmtTime(first, sameDay(first, new Date()) ? 'HH:MM' : 'MM-DD HH:MM') : '—'],
      ["Estimated number of times", sched.planned_total ? `${sched.planned_total} times` : "continues"],
      ["last time", sched.last_slot_at ? labels.fmtTime(sched.last_slot_at) : "until pressing STOP"]];
  }
  const preview = ctx.store.preview.data;
  if (!preview || !ctx.store.form.recipe_id) return [["Estimated single time", '—'], ["first measurement", '—'], ["Estimated number of times", '—'], ["last time", '—']];
  const first = preview.spec && preview.spec.first_slot ? new Date(preview.spec.first_slot) : null;
  const rows = [["Estimated single time", "approx." + labels.fmtDuration(preview.estimate_seconds)],
    ["first measurement", first ? labels.fmtTime(first, 'HH:MM') + (sameDay(first, new Date()) ? '' : "(tomorrow)") : '—'],
    ["Estimated number of times", preview.planned_total ? `${preview.planned_total} times` : "continues"],
    ["last time", preview.last_slot_at ? labels.fmtTime(preview.last_slot_at) : "until pressing STOP"]];
  const liquids = preview.recipe && preview.recipe.uses_pump ? liquidText(preview.recipe.liquids) : '';
  if (liquids) rows.push(["liquid used", liquids]);
  return rows;
}

function renderEstimate(ctx, rows) {
  const {dom} = ctx;
  dom.list(el.est, rows, (row) => row[0], (row) => {
    const value = dom.h('b');
    //“Liquid used” is one line here; a tap shows the whole list
    if (row[0] === "liquid used") {
      const line = dom.h('button.dlg-line', null, dom.h('span', null, row[0]), value);
      line.addEventListener('click', () => ctx.dialog.message(row[0], value.textContent));
      return line;
    }
    return dom.h('div.dlg-line', null, dom.h('span', null, row[0]), value);
  }, (line, row) => {
    dom.setText(line.lastChild, row[1]);
    setTitle(line, row[1]);
  });
}

// One box per message; at most two boxes with a text, a third (or, when the estimate has a fifth line, the second)
//reads "There are n more, click here to see". Short of room a box shows one line; a tap shows all of it.
function renderNotes(ctx, messages, estimateLines) {
  const {dom} = ctx;
  const most = estimateLines >= 5 ? 2 : 3;
  let list = messages.map((item) => ({...item, all: item.text}));
  if (list.length > most) {
    const kept = list.slice(0, most - 1);
    const rest = list.slice(most - 1);
    list = [...kept, {kind: 'warn', text: `and ${rest.length}, click here to view`, all: messages.map((item) => item.text).join('\n\n'), more: true}];
  }
  boxes = list;
  const lines = list.length === 1 ? 3 : list.length === 2 && most === 3 ? 2 : 1;
  for (const [name, on] of [['k2', lines === 2], ['k3', lines === 3]]) dom.setClass(el.notes, name, on);
  dom.list(el.notes, list, (item, index) => index, (item, index) => {
    const box = dom.h('button', null, dom.h('span'));
    box.addEventListener('click', () => {
      const now = boxes[index];
      if (!now) return;
      ctx.dialog.message(now.kind === 'err' && !now.more && now.blocking ? "The schedule cannot be started at this time" : "message", now.all, {level: now.kind === 'err' ? 'error' : ''});
    });
    return box;
  }, (box, item) => {
    const cls = item.kind === 'err' ? 'sch-err' : 'sch-warn';
    if (box.className !== cls) box.className = cls;
    dom.setText(box.firstChild, item.text);
    setTitle(box, item.all);
  });
}

// ── slot log ────────────────────────────────────────────────────────────────────────────────────────────────
function renderLog(ctx, state, sched, active) {
  const {dom, labels} = ctx;
  const calibration = Boolean(sched && sched.kind === 'calibration');
  dom.setText(el.logTitle, !sched ? "schedule record" : active ? "This schedule" : "last schedule");
  const sub = sched ? [(calibration ? "Correction:" : '') + ((sched.recipe && sched.recipe.name) || ''), sched.operator,
    labels.fmtTime(sched.created_at)].filter(Boolean).join(' · ') : '';
  dom.setText(el.logSub, sub);
  setTitle(el.logSub, sub);
  const counts = (sched && sched.counts) || {};
  dom.setText(el.logCounts, sched
    ? `Completed ${counts.done || 0} · Skip ${(counts.skipped_overrun || 0) + (counts.skipped_paused || 0)} · Failed/Not Started ${(counts.failed || 0) + (counts.not_started || 0)}`
    : '');

  const slots = sched ? [...(sched.slots || [])].sort((a, b) => b.index - a.index) : [];
  const pager = dom.pager(el.pager, slots.length, SLOT_ROWS, () => again(ctx));
  const some = slots.length > 0;
  setHidden(el.tbody, !some);
  setHidden(el.empty, some);
  if (!some) {
    if (!sched) {
      dom.setText(el.emptyTitle, "has not started scheduling yet");
      dom.setText(el.emptyText, "Select the process, start time, interval and end conditions on the left, and then click \"Start Scheduling\" at the bottom. If you only need to measure once, select \"1 time in total\". Results for each period (including skipped periods) are listed here.");
    } else {
      dom.setText(el.emptyTitle, "has not been measured yet");
      dom.setText(el.emptyText, active && sched.next_slot_at ? `Next measurement ${labels.fmtTime(sched.next_slot_at, 'HH:MM')}` : '');
    }
  }
  dom.list(el.tbody, pager.slice(slots), (slot) => slot.index, () => {
    const cells = [dom.h('span.c-slot'), dom.h('span.c-plan'), dom.h('span.c-sst', null, dom.h('span')), dom.h('span.c-hm'), dom.h('span.c-hm'),
      dom.h('span.c-msg.one')];
    //a slot with a round opens it in execution record; one without shows its note in full
    const row = dom.h('button.trow', null, cells);
    row.addEventListener('click', () => {
      const now = slotFacts.get(row);
      if (!now) return;
      if (now.round_id) ctx.go('runs', {round_id: now.round_id, job: now.job || null});
      else ctx.dialog.message("Remarks", now.note || labels.slot[now.status] || now.status || '');
    });
    return row;
  }, (row, slot) => {
    slotFacts.set(row, slot);
    const [no, plan, state2, started, finished, note] = row.children;
    dom.setText(no, (slot.kind === 'blank' ? "[blank]" : '') + (slot.index + 1));
    dom.setText(plan, labels.fmtTime(slot.planned_at));
    const pill = state2.firstChild;
    dom.setText(pill, labels.slot[slot.status] || slot.status || '');
    const cls = 'st ' + (SLOT_CLASS[slot.status] || '');
    if (pill.className !== cls) pill.className = cls;
    dom.setText(started, slot.started_at ? labels.fmtTime(slot.started_at, 'HH:MM') : '');
    dom.setText(finished, slot.finished_at ? labels.fmtTime(slot.finished_at, 'HH:MM') : '');
    dom.setText(note, slot.note || '');
    setTitle(note, slot.note || '');
  });
}

// ── render ──────────────────────────────────────────────────────────────────────────────────────────────────
export function render(state, ctx) {
  if (!el) return;
  const {dom, store} = ctx;
  const sched = state.schedule || null;
  const active = Boolean(sched && ACTIVE.includes(sched.status));
  const calibration = active && sched.kind === 'calibration';
  wantRecipes(ctx, (state.revs && state.revs.recipes) || 0);
  wantFlow(ctx, (state.revs && state.revs.recipes) || 0);

  dom.setText(el.formSub, calibration ? "Calibration in progress: Go to the \"Calibration\" page to view" : active ? "Scheduling is in progress: modification can only be made after stopping" : "Click on each item to modify; the settings will be recorded on this computer");
  const view = active && !calibration ? scheduleView(sched) : formView(ctx, state);
  view.params = plasmaValues(state, store.stale);
  renderTiles(ctx, view, active);

  const rows = estimateRows(ctx, state, sched, active, calibration);
  renderEstimate(ctx, rows);
  const messages = [];
  if (!calibration) {
    const preview = store.preview;
    if (active) {
      if (sched.error) messages.push({kind: 'err', text: sched.error});
    } else {
      if (preview.error) messages.push({kind: 'err', text: preview.error, blocking: true});
      else if (sched && sched.error) messages.push({kind: 'err', text: sched.error});
      for (const text of (preview.data && preview.data.warnings) || []) messages.push({kind: 'warn', text: String(text)});
    }
  }
  renderNotes(ctx, messages, rows.length);
  renderLog(ctx, state, sched, active);
}
