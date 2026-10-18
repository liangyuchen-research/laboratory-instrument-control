//maint-recipe.js — Maintenance › Recipe (SPEC E.10, AMENDMENTS M4.2): the published Recipes on the left; on the right the
// chosen one, or the one draft of the page with its step editor.
//
//A draft comes from "Add Recipe" (empty), from "Import CSV file" (the file's rows; the file goes to the program as bytes,
//base64, and is decoded there) or from "Copy to modify". Its rows live here until "Publish" or "Discard". Every change sends all
// rows to /api/recipes/validate and the view is drawn from the reply: this module judges nothing about a Recipe itself.
// A published Recipe is never changed in place: publish under a new name, then delete the old one.

import { act, fail, report, setHidden, setDisabled, kvRow, setValue } from './maint.js';

const LIST_ROWS = 6;           // Recipes per page
const TABLE_ROWS = 7;          // CSV rows per page of a published Recipe
const DRAFT_ROWS = 6;          // ... of a draft (the editor's bar takes one row)
const NAME_MAX = 60;
const NOTE_MAX = 200;
const ACTIVE = ['waiting', 'running', 'pausing', 'paused', 'stopping'];
const NUMBER = /^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/;

const T_DRAFT_OPEN = "Publish or discard the Recipe being edited first";
const T_UNREADABLE = "CSV cannot be read in";
const T_PLASMA_WARN = "This Recipe contains high-voltage plasma: confirm it once at the beginning of the schedule, and it will automatically discharge for each measurement thereafter.\nAfter downloading";
const T_EXAMPLES = ", use Excel or Notepad to modify it, and then click \"Import CSV file\". Please select \"CSV UTF-8 (comma separated)\" when archiving in Excel.";

let ui = null;
let page = null;               // ctx
let recipes = null;            // rows of GET /api/recipes; null until the first answer
let listRev = null;            // state.revs.recipes that list belongs to
let listLoading = false;
let selected = '';             // id of the published Recipe shown on the right
let detail = null;             // reply of GET /api/recipes/<id>
let detailKey = '';            // '<id>|<rev>' of that reply
let detailLoading = false;
let draft = null;              // {name, suggest, rows: [[action, target, value, unit, note]], view, sel, pending, follow}
let sending = false;           // a publish or a delete is on its way
let turn = 0;                  // answers of an older request are dropped

// ── texts ───────────────────────────────────────────────────────────────────────────────────────────────────
const amount = (value) => String(Math.round(Number(value) * 100) / 100);
const stepsText = (item) => `${item.step_count} step · ${item.rounds} wheel`;
const plasmaText = (item) => (item.uses_plasma ? "contains high voltage plasma" : "does not contain plasma");

function estimateText(item) {
  const seconds = item.estimate_seconds;
  return seconds === null || seconds === undefined ? '—' : `about ${page.labels.fmtDuration(seconds)}`;
}

function liquidText(list) {
  return (list || []).map((item) => (item.g !== null && item.g !== undefined
    ? `${item.label}: ${amount(item.g)} g`
    : `${item.label}: ${item.approx ? "approx." : ''}${amount(item.ml)} mL`)).join(' · ') || '—';
}

const cellsOf = (row) => [row.action, row.target, row.value, row.unit, row.note]
  .map((cell) => (cell === null || cell === undefined ? '' : String(cell)));

function again() {
  if (ui && page && page.store.state) render(page.store.state, page);
}

// ── data ────────────────────────────────────────────────────────────────────────────────────────────────────
// The list is read again whenever state.revs.recipes changes (a publish, a delete, a setting that changes what a
// Recipe may do). A request that got no answer is sent again by the next poll, not at once.
async function loadList(ctx, rev) {
  if (listLoading) return;
  listLoading = true;
  let arrived = false;
  try {
    const reply = await ctx.api.get('/api/recipes');
    recipes = Array.isArray(reply.recipes) ? reply.recipes : [];
    listRev = rev;
    arrived = true;
    if (!recipes.some((row) => row.id === selected)) {
      selected = recipes.length ? recipes[0].id : '';
      detail = null;
      detailKey = '';
      ui.rewind = true;
    }
  } catch (_) { /* see above */ }
  listLoading = false;
  if (arrived) again();
}

async function loadDetail(ctx, id, key) {
  if (detailLoading) return;
  detailLoading = true;
  let arrived = false;
  try {
    const reply = await ctx.api.get(`/api/recipes/${id}`);
    arrived = true;
    if (id === selected) { detail = reply; detailKey = key; }
  } catch (error) {
    if (error && error.status === 404) {                   // it is gone: the list is read again
      arrived = true;
      if (id === selected) { detail = null; detailKey = key; listRev = null; }
    }
  }
  detailLoading = false;
  if (arrived) again();
}

function select(id) {
  if (draft || id === selected) return;
  selected = id;
  detail = null;
  detailKey = '';
  ui.rewind = true;
  again();
}

// ── draft ───────────────────────────────────────────────────────────────────────────────────────────────────
// Sends these rows to the program; when it answers, they are the draft's rows and the answer is what is shown.
async function check(ctx, rows, sel) {
  if (!draft) return false;
  const mine = ++turn;
  draft.pending = true;
  again();
  let done = false;
  try {
    const reply = await ctx.api.post('/api/recipes/validate', {rows});
    if (draft && mine === turn) {
      draft.rows = rows;
      draft.view = reply;
      draft.sel = sel;
      draft.follow = sel >= 0;
      done = true;
    }
  } catch (error) {
    if (mine === turn) fail(ctx, error);
  }
  if (draft && mine === turn) draft.pending = false;
  again();
  return done;
}

async function start(ctx, name, suggest, rows) {
  draft = {name, suggest, rows: [], view: null, sel: -1, pending: true, follow: false};
  ui.rewind = true;
  const done = await check(ctx, rows, -1);
  if (!done && draft && !draft.view) draft = null;      // the program did not answer: there is no draft
  again();
}

function newDraft(ctx) {
  if (draft) return;
  start(ctx, '', '', []);
}

function copyDraft(ctx) {
  if (draft || !detail || detail.id !== selected || !(detail.rows || []).length) return;
  start(ctx, '', detail.name || '', detail.rows.map(cellsOf));
}

function toBase64(bytes) {
  let text = '';
  for (let at = 0; at < bytes.length; at += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(at, at + 0x8000));
  return btoa(text);
}

// The file is read as bytes and sent as it is: the program finds out the encoding and reads the rows.
async function importFile(ctx, file) {
  if (!file || draft) return;
  const limit = Number((((ctx.store.boot || {}).recipe || {}).limits || {}).bytes) || 200000;
  if (file.size > limit) {
    ctx.dialog.message(T_UNREADABLE, `CSV too large (limit ${Math.round(limit / 1000)} KB)`, {level: 'error'});
    return;
  }
  const mine = ++turn;
  let reply;
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    reply = await ctx.api.post('/api/recipes/validate', {file_b64: toBase64(bytes)});
  } catch (error) {
    if (error && error.status === 400) ctx.dialog.message(T_UNREADABLE, error.message, {level: 'error'});
    else if (error && error.status !== undefined) fail(ctx, error);
    else ctx.dialog.message(T_UNREADABLE, "This file cannot be opened.", {level: 'error'});
    return;
  }
  if (draft || mine !== turn) return;
  const rows = Array.isArray(reply.rows) ? reply.rows : [];
  if (!rows.length) {                              // nothing of the file can be used: nothing is kept
    ctx.dialog.message(T_UNREADABLE, reply.error || "CSV has no action to perform.", {level: 'error'});
    return;
  }
  draft = {name: String(file.name || '').replace(/\.csv$/i, '').trim().slice(0, NAME_MAX), suggest: '',
    rows: rows.map(cellsOf), view: reply, sel: -1, pending: false, follow: false};
  ui.rewind = true;
  again();
}

async function rename(ctx) {
  if (!draft) return;
  const name = await ctx.dialog.text({title: "Recipe name", value: draft.name || draft.suggest, maxLength: NAME_MAX,
    rule: "1–60 characters, Chinese can be typed directly"});
  if (name === null || !draft) return;
  draft.name = name;
  again();
}

async function discard(ctx) {
  if (!draft) return;
  const yes = await ctx.dialog.confirm({title: "Discard this Recipe", detail: "The steps that have not yet been released will disappear.", confirmLabel: "discard", variant: 'danger'});
  if (!yes) return;
  draft = null;
  turn += 1;
  ui.rewind = true;
  again();
}

// ── the step dialogs (cancel anywhere = nothing changes) ────────────────────────────────────────────────────
const specName = (action) => (action === 'nano_stop' ? 'power_off' : action);     //the old spelling of“turn off high voltage”

function specOf(ctx, action) {
  const list = ((ctx.store.boot || {}).recipe || {}).actions || [];
  return list.find((item) => item.action === specName(action)) || null;
}

function targetsOf(ctx, kind) {
  const hardware = (ctx.store.boot || {}).hardware || {};
  if (kind === 'motor' || kind === 'motors') {
    // A pump is written by its number on the screen (P1–P8); `alias` is the older spelling (the pin of the board).
    const out = [];
    for (const group of hardware.groups || []) {
      for (const motor of group.motors || []) {
        out.push({value: motor.pump ? `P${motor.pump}` : `M${motor.id}`, alias: `M${motor.id}`, label: motor.label, id: motor.id});
      }
    }
    return out;
  }
  if (kind === 'valve') {
    const count = Number((hardware.syringe || {}).valve_positions) || 0;
    return Array.from({length: count}, (_, index) => ({value: `CH${index + 1}`, label: `valve position ${index + 1}`}));
  }
  return [];
}

function askAction(ctx, current) {
  const list = ((ctx.store.boot || {}).recipe || {}).actions || [];
  return ctx.dialog.choose({title: "Select action", wide: true, value: current ? specName(current) : undefined,
    choices: list.map((item) => ({value: item.action, label: item.name, small: item.group,
      disabled: item.target && !targetsOf(ctx, item.target).length ? "Unable to read the settings of the pump control panel" : false}))});
}

//Simultaneous quantification: several pumps, each with its own amount. -> [target, value] as the row holds them ('P1+P8', '10+2'), or null.
async function askPumps(ctx, spec, target, value) {
  const DONE = 'done';
  const pumps = targetsOf(ctx, 'motors');
  const range = spec.value;
  const out = ((((ctx.store.boot || {}).hardware || {}).cleaning) || {}).out_motor;      // the program refuses it in this step
  const amounts = String(value || '').split('+');
  const chosen = new Map();
  String(target || '').split('+').forEach((name, index) => {
    const pump = pumps.find((item) => item.value === name || item.alias === name);
    if (pump && pump.id !== out && NUMBER.test(amounts[index] || '')) chosen.set(pump.value, Number(amounts[index]));
  });
  for (;;) {
    const picked = await ctx.dialog.choose({title: `${spec.name}: Click a pump to set`, wide: true, columns: 2, timeout: 120,
      detail: "At least two. Pumps 1 to 4 and Pumps 5 to 8 will rotate together; those in the same group will rotate next.",
      choices: pumps.map((pump) => ({value: pump.value, label: pump.label, current: chosen.has(pump.value),
        disabled: pump.id === out ? "Pump for emptying: do not rotate together with other pumps" : false,
        small: chosen.has(pump.value) ? `${chosen.get(pump.value)} ${range.unit}` : "Not added"})),
      actions: [{label: "Complete", value: DONE}]});
    if (picked === null) return null;
    if (picked === DONE) {
      if (chosen.size < 2) {
        await ctx.dialog.message(spec.name, "At least two pumps must be selected. Please use \"quantity (volume)\" if there is only one.");
        continue;
      }
      const order = pumps.filter((pump) => chosen.has(pump.value));
      return [order.map((pump) => pump.value).join('+'), order.map((pump) => String(chosen.get(pump.value))).join('+')];
    }
    const pump = pumps.find((item) => item.value === picked);
    if (chosen.has(picked)) {
      const what = await ctx.dialog.choose({title: pump.label, value: 'set',
        choices: [{value: 'set', label: "Change quantity"}, {value: 'off', label: "Do not add this one"}]});
      if (what === null) continue;
      if (what === 'off') { chosen.delete(picked); continue; }
    }
    const amount = await ctx.dialog.pad({title: pump.label, min: range.min, max: range.max, decimals: range.decimals, unit: range.unit,
      value: chosen.has(picked) ? chosen.get(picked) : ''});
    if (amount !== null) chosen.set(picked, amount);
  }
}

// base = the cells to start from, or null. from: 'action' (all four dialogs) | 'value' (target and number) | 'note'.
// -> [action, target, value, unit, note], or null when a dialog was cancelled.
async function askStep(ctx, base, from) {
  let [action, target, value, unit, note] = base || ['', '', '', '', ''];
  if (from === 'action') {
    const picked = await askAction(ctx, action);
    if (picked === null) return null;
    if (picked !== specName(action)) { action = picked; value = ''; }      // another action: its number is asked anew
  }
  const spec = specOf(ctx, action);
  if (!spec) return null;
  if (from !== 'note' && spec.target === 'motors') {
    const picked = await askPumps(ctx, spec, target, value);
    if (picked === null) return null;
    [target, value] = picked;
    unit = spec.value.unit || '';
  } else if (from !== 'note') {
    if (spec.target) {
      const choices = targetsOf(ctx, spec.target);
      const now = choices.find((item) => item.value === target || item.alias === target);
      const picked = await ctx.dialog.choose({title: spec.target === 'motor' ? "Select Pump" : "select valve position", wide: true,
        value: now ? now.value : undefined, choices});
      if (picked === null) return null;
      target = picked;
    } else {
      target = '';
    }
    if (spec.value) {
      const range = spec.value;
      const picked = await ctx.dialog.pad({title: spec.name, min: range.min, max: range.max, decimals: range.decimals,
        unit: range.unit, value: NUMBER.test(value) ? Number(value) : ''});
      if (picked === null) return null;
      value = String(picked);
      unit = range.unit || '';
    } else {
      value = '';
      unit = '';
    }
  }
  if (from !== 'value') {
    const picked = await ctx.dialog.text({title: "Remarks", value: note, maxLength: NOTE_MAX, rule: "can be left blank", allowEmpty: true});
    if (picked === null) return null;
    note = picked;
  }
  return [action, target, value, unit, note];
}

const editable = () => Boolean(draft && draft.view && !draft.pending && !sending);

async function addStep(ctx) {
  if (!editable()) return;
  const limit = Number((((ctx.store.boot || {}).recipe || {}).limits || {}).rows) || 500;
  if (draft.rows.length >= limit) { report(`CSV up to ${limit} columns`, 'err'); return; }
  const cells = await askStep(ctx, null, 'action');
  if (!cells || !editable()) return;
  const rows = draft.rows.slice();
  const at = draft.sel >= 0 && draft.sel < rows.length ? draft.sel + 1 : rows.length;
  rows.splice(at, 0, cells);
  check(ctx, rows, at);
}

async function editStep(ctx) {
  if (!editable() || draft.sel < 0 || draft.sel >= draft.rows.length) return;
  const at = draft.sel;
  const shown = (draft.view.rows || [])[at] || {};
  const base = draft.rows[at];
  const spec = specOf(ctx, base[0]);
  const noValue = !spec ? "Unsupported action" : !spec.target && !spec.value ? "This action has no value" : false;
  const what = await ctx.dialog.choose({title: `Column ${shown.line || at + 2}: ${shown.name || base[0]}`, columns: 1, choices: [
    {value: 'value', label: "change value", disabled: noValue},
    {value: 'note', label: "Change remarks", disabled: spec ? false : "Unsupported action"},
    {value: 'action', label: "Change to another action"}]});
  if (what === null || !editable() || draft.rows[at] !== base) return;
  const cells = await askStep(ctx, base, what);
  if (!cells || !editable() || draft.rows[at] !== base) return;
  const rows = draft.rows.slice();
  rows[at] = cells;
  check(ctx, rows, at);
}

function moveStep(ctx, by) {
  if (!editable()) return;
  const from = draft.sel;
  const to = from + by;
  if (from < 0 || to < 0 || to >= draft.rows.length) return;
  const rows = draft.rows.slice();
  [rows[from], rows[to]] = [rows[to], rows[from]];
  check(ctx, rows, to);
}

function deleteStep(ctx) {
  if (!editable() || draft.sel < 0 || draft.sel >= draft.rows.length) return;
  const rows = draft.rows.slice();
  rows.splice(draft.sel, 1);
  check(ctx, rows, Math.min(draft.sel, rows.length - 1));
}

// ── publish, delete ─────────────────────────────────────────────────────────────────────────────────────────
async function publish(ctx) {
  if (!editable() || !draft.view.valid || !draft.name) return;
  const view = draft.view;
  const name = draft.name;
  const yes = await ctx.dialog.confirm({title: "posted Recipe\nPosted by", confirmLabel: "release",
    lines: [["Name", name], ["steps/round", stepsText(view)], ["estimated time", estimateText(view)], ["measurement", plasmaText(view)],
      ["pump control board", view.uses_pump ? "used" : "Not needed"]],
    warn: view.uses_plasma ? T_PLASMA_WARN : ''});
  if (!yes || !draft || draft.view !== view) return;
  sending = true;
  again();
  const reply = await act(ctx, () => ctx.api.post('/api/recipes/publish', {name, csv: view.csv}), (out) => `Published Recipe: ${out.name || name}`);
  sending = false;
  if (reply) {
    draft = null;
    turn += 1;
    selected = reply.id || selected;
    detail = null;
    detailKey = '';
    listRev = null;                // read the list again: the new Recipe is in it
    ui.rewind = true;
  }
  again();
}

async function remove(ctx) {
  const row = (recipes || []).find((item) => item.id === selected);
  if (!row || draft || sending) return;
  const yes = await ctx.dialog.confirm({title: "Delete Recipe", variant: 'danger', confirmLabel: "Next step",
    lines: [["Name", row.name], ["steps", `${row.step_count} steps`]],
    warn: "After deleting the schedule, it cannot be selected again. The file will be moved to data\\recipes\\deleted."});
  if (!yes) return;
  const pin = await ctx.dialog.pin({title: "Enter service PIN to delete", variant: 'danger'});
  if (pin === null) return;
  sending = true;
  again();
  const reply = await act(ctx, () => ctx.api.post('/api/recipes/delete', {id: row.id, pin}), `Deleted Recipe: ${row.name}`);
  sending = false;
  if (reply) {
    if (selected === row.id) { selected = ''; detail = null; detailKey = ''; }
    listRev = null;
  }
  again();
}

function showExamples(ctx) {
  const list = ((ctx.store.boot || {}).recipe || {}).examples || [];
  ctx.dialog.message("Example CSV file", T_EXAMPLES, {
    links: list.map((item) => ({label: item.name, href: `/api/recipes/examples/${item.file}`, download: item.file}))});
}

function showWarnings(ctx, list) {
  if (list.length) ctx.dialog.message("message", list.join('\n\n'));
}

// ── mount ───────────────────────────────────────────────────────────────────────────────────────────────────
function makeItem(row) {
  const {h} = page.dom;
  const name = h('b');
  const text = h('span');
  const warn = h('span.warn');
  const button = h('button.li', {onclick: () => select(row.id)}, name, h('span', null, text, warn));
  button.parts = {name, text, warn};
  return button;
}

function updateItem(button, row) {
  const {setText, setClass} = page.dom;
  setText(button.parts.name, row.name);
  setText(button.parts.text, `${row.step_count} step · ${row.uses_plasma ? "contains plasma" : "does not contain plasma"} · ${row.uses_pump ? "uses pump" : "does not require a pump"}`);
  setText(button.parts.warn, row.valid === false ? "⚠ Currently unavailable" : '');
  setClass(button, 'sel', !draft && row.id === selected);
  setDisabled(button, Boolean(draft));
}

function makeRow(_row, index) {
  const {h} = page.dom;
  const line = h('span.c-row');
  const text = h('span.c-msg.one');
  const note = h('span.c-note');
  const node = h('div.trow.slim', {attrs: {role: 'button'}, tabIndex: 0}, line, text, note);
  node.parts = {line, text, note};
  const press = () => tapRow(index);
  node.addEventListener('click', press);
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); press(); }
  });
  return node;
}

// A tap on a row: in a draft it selects the row; of a published Recipe it opens the whole line.
function tapRow(index) {
  const shown = ui.shown[index];
  if (!shown) return;
  if (draft) {
    if (draft.pending) return;
    draft.sel = draft.sel === shown.at ? -1 : shown.at;     // a second tap lets go of the row
    again();
    return;
  }
  page.dialog.message("message", `Column ${shown.row.line}: ${shown.row.text}` + (shown.row.note ? `Remarks: ${shown.row.note}` : ''));
}

function updateRow(node, shown) {
  const {setText, setClass} = page.dom;
  setText(node.parts.line, shown.row.line);
  setText(node.parts.text, shown.row.text);
  setText(node.parts.note, shown.row.note);
  setClass(node, 'flag', shown.row.ok === false);
  setClass(node, 'sel', Boolean(draft) && shown.at === draft.sel);
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;

  // left: the published Recipes
  const hint = h('div.adm-hint', {hidden: true}, T_DRAFT_OPEN);
  const list = h('div.list');
  const listEmpty = h('div.empty', {hidden: true}, h('b', null, "No recipes yet"), h('p', null, "Click \"Import CSV file\", or download one from \"Sample...\" to modify."));
  const listPager = h('div');
  const add = h('button.btn', {onclick: () => newDraft(ctx)}, "New Recipe");
  const file = h('input', {type: 'file', accept: '.csv'});
  file.addEventListener('change', () => {
    const chosen = file.files && file.files[0];
    file.value = '';                                       // the same file can be chosen again
    importFile(ctx, chosen);
  });
  const fileButton = h('label.btn.file-btn', null, "Import CSV file", file);
  const left = h('div.panel.adm-left', null, h('div.ph', null, h('h2', null, "has published Recipe")), hint, list, listEmpty, listPager,
    h('div.adm-foot', null, add, fileButton, h('button.btn', {onclick: () => showExamples(ctx)}, "Example…")));

  // right: a published Recipe, or the draft
  const title = h('h2');
  const sub = h('span.sub');
  const none = h('div.empty', {hidden: true}, h('p', null, "To create a new recipe: click \"Add Recipe\" to edit it step by step, or click \"Import CSV file\"."));

  const facts = {steps: kvRow(ctx, "steps/round"), plasma: kvRow(ctx, "measurement"), pump: kvRow(ctx, "pump control board"), time: kvRow(ctx, "estimated time"),
    liquids: kvRow(ctx, "liquid used"), status: kvRow(ctx, "status")};
  const factBox = h('div.kv', null, Object.values(facts).map((item) => item.row));

  const nameValue = h('b');
  const nameButton = h('button', {onclick: () => rename(ctx)}, h('span', null, "Name"), nameValue);
  const errorValue = h('b');
  const errorButton = h('button', {onclick: () => { if (draft && draft.view && draft.view.error) ctx.dialog.message("message", draft.view.error, {level: 'error'}); }},
    h('span', null, "error"), errorValue);
  const drafts = {check: kvRow(ctx, "check"), steps: kvRow(ctx, "steps/round"), time: kvRow(ctx, "estimated time"), uses: kvRow(ctx, "Measurement/Pump")};
  const draftBox = h('div.kv', {hidden: true}, nameButton, drafts.check.row, drafts.steps.row, drafts.time.row, drafts.uses.row, errorButton);

  const warnings = [0, 1].map(() => h('button.sch-warn', {hidden: true, onclick: () => showWarnings(ctx, ui.warningList)}));
  const warnBox = h('div.adm-warns', {hidden: true}, warnings);

  const thead = h('div.thead', null, h('span.c-row', null, "column"), h('span.c-msg', null, "Action"), h('span.c-note', null, "Remarks"));
  const tbody = h('div.tbody');
  const noSteps = h('div.empty', {hidden: true}, h('b', null, "No steps yet"), h('p', null, "Click \"+ Add Step\" to start."));
  const tablePager = h('div');

  const edit = {
    add: h('button.btn', {onclick: () => { addStep(ctx); }}, "+ New steps"),
    change: h('button.btn', {onclick: () => { editStep(ctx); }}, "modified"),
    up: h('button.btn', {onclick: () => moveStep(ctx, -1)}, "Move up"),
    down: h('button.btn', {onclick: () => moveStep(ctx, 1)}, "Move down"),
    remove: h('button.btn', {onclick: () => deleteStep(ctx)}, "Delete"),
  };
  const editBar = h('div.ed-bar', {hidden: true}, Object.values(edit));

  const exportLink = h('a.btn', {href: '#', attrs: {download: ''}}, "Export CSV");
  const copy = h('button.btn', {onclick: () => copyDraft(ctx)}, "Copy to modify");
  const inUse = h('span.why', {hidden: true}, "schedule is in use");
  const del = h('button.btn', {onclick: () => { remove(ctx); }}, "Delete Recipe");
  const pubFoot = h('div.adm-foot', null, exportLink, copy, h('span.sp'), inUse, del);

  const drop = h('button.btn', {onclick: () => { discard(ctx); }}, "discard");
  const send = h('button.btn.primary', {onclick: () => { publish(ctx); }}, "release");
  const draftFoot = h('div.adm-foot', {hidden: true}, drop, h('span.sp'), send);

  const view = h('div.adm-view', {hidden: true}, factBox, draftBox, warnBox, thead, tbody, noSteps, tablePager, editBar, pubFoot, draftFoot);
  const right = h('div.panel.adm-right', null, h('div.ph', null, title, sub), none, view);

  root.replaceChildren(h('div.adm-body', null, left, right));
  ui = {hint, list, listEmpty, listPager, add, file, fileButton, title, sub, none, view, facts, factBox, drafts, draftBox,
    nameButton, nameValue, errorButton, errorValue, warnings, warnBox, warningList: [], thead, tbody, noSteps, tablePager,
    edit, editBar, exportLink, copy, inUse, del, pubFoot, drop, send, draftFoot, shown: [], linked: '', rewind: false};
}

// ── render ──────────────────────────────────────────────────────────────────────────────────────────────────
function drawWarnings(ctx, list) {
  const {setText} = ctx.dom;
  ui.warningList = list;
  const lines = list.length > 2 ? [list[0], `and ${list.length - 1}, click here to view`] : list;
  setHidden(ui.warnBox, !lines.length);
  ui.warnings.forEach((node, index) => {
    setHidden(node, index >= lines.length);
    setText(node, lines[index] || '');
  });
}

function drawTable(ctx, rows, perPage, selectedAt) {
  const pager = ctx.dom.pager(ui.tablePager, rows.length, perPage, again);
  if (ui.rewind) {                                         // another Recipe, or a draft begins or ends: first page
    ui.rewind = false;
    pager.set(0);
  }
  if (draft && draft.follow) {                             // the row that was just added or moved stays in view
    draft.follow = false;
    if (selectedAt >= 0) pager.set(Math.floor(selectedAt / perPage));
  }
  const first = pager.page * perPage;
  ui.shown = pager.slice(rows).map((row, index) => ({row, at: first + index}));
  ctx.dom.list(ui.tbody, ui.shown, (_item, index) => index, makeRow, updateRow);
}

function drawPublished(ctx, state, row) {
  const {setText, setClass} = ctx.dom;
  const full = detail && detail.id === row.id ? detail : null;
  setText(ui.title, row.name);
  setText(ui.sub, `Posted by ${ctx.labels.fmtTime(row.created_at, 'YYYY-MM-DD HH:MM')}`);
  setValue(ctx, ui.facts.steps, stepsText(row));
  setValue(ctx, ui.facts.plasma, plasmaText(row));
  setValue(ctx, ui.facts.pump, row.uses_pump ? "used" : "Not needed");
  setValue(ctx, ui.facts.time, estimateText(row));
  const liquids = full ? liquidText(full.liquids) : '…';
  setValue(ctx, ui.facts.liquids, liquids, '', liquids);
  const problem = row.valid === false ? row.error || "is currently unavailable" : '';
  setValue(ctx, ui.facts.status, problem || "can be used", problem ? 'txt-warn' : '', problem);
  drawWarnings(ctx, full ? full.warnings || [] : []);
  const rows = full ? full.rows || [] : [];
  setClass(ui.view, 'draft', false);
  setHidden(ui.noSteps, true);
  setHidden(ui.tbody, false);
  drawTable(ctx, rows, TABLE_ROWS, -1);

  const href = `/api/recipes/${row.id}.csv`;
  if (ui.linked !== href) { ui.linked = href; ui.exportLink.setAttribute('href', href); }
  setHidden(ui.exportLink, Boolean(full && !full.csv));
  setDisabled(ui.copy, !full || !rows.length || sending);
  const schedule = state.schedule;
  const used = Boolean(schedule && ACTIVE.includes(schedule.status) && [(schedule.recipe || {}).id, (schedule.blank || {}).recipe_id,
    (schedule.prime_recipe || {}).id].includes(row.id));
  setHidden(ui.inUse, !used);
  setDisabled(ui.del, used || sending);
}

function drawDraft(ctx) {
  const {setText, setClass} = ctx.dom;
  const view = draft.view;
  const rows = view ? view.rows || [] : [];
  const ready = Boolean(view) && !draft.pending && !sending;
  setText(ui.title, "New Recipe (not yet released)");
  setText(ui.sub, "Click a column to select it, and then use the buttons below to modify it");
  setText(ui.nameValue, draft.name || "Please name");
  setClass(ui.nameValue, 'txt-warn', !draft.name);
  setDisabled(ui.nameButton, sending);
  setValue(ctx, ui.drafts.check, draft.pending || !view ? "Checking..." : view.valid ? "passed" : "failed", view && !view.valid && !draft.pending ? 'txt-warn' : '');
  setValue(ctx, ui.drafts.steps, view ? stepsText(view) : '—');
  setValue(ctx, ui.drafts.time, view ? estimateText(view) : '—');
  setValue(ctx, ui.drafts.uses, view ? `${plasmaText(view)} · ${view.uses_pump ? "uses pump" : "does not require a pump"}` : '—');
  const error = view ? view.error || '' : '';
  setText(ui.errorValue, error || '—');
  setClass(ui.errorValue, 'txt-warn', Boolean(error));
  setDisabled(ui.errorButton, !error);
  drawWarnings(ctx, view ? view.warnings || [] : []);
  setClass(ui.view, 'draft', true);
  setHidden(ui.noSteps, rows.length > 0);
  setHidden(ui.tbody, rows.length === 0);
  if (draft.sel >= rows.length) draft.sel = rows.length - 1;
  drawTable(ctx, rows, DRAFT_ROWS, draft.sel);

  const chosen = draft.sel >= 0;
  setDisabled(ui.edit.add, !ready);
  setDisabled(ui.edit.change, !ready || !chosen);
  setDisabled(ui.edit.up, !ready || !chosen || draft.sel === 0);
  setDisabled(ui.edit.down, !ready || !chosen || draft.sel >= rows.length - 1);
  setDisabled(ui.edit.remove, !ready || !chosen);
  setDisabled(ui.drop, sending);
  setDisabled(ui.send, !ready || !view.valid || !draft.name);
}

export function render(state, ctx) {
  const rev = state.revs ? state.revs.recipes : 0;
  if (listRev !== rev) loadList(ctx, rev);
  const rows = recipes || [];
  const row = rows.find((item) => item.id === selected) || null;
  const key = `${selected}|${rev}`;
  if (row && !draft && detailKey !== key) loadDetail(ctx, selected, key);

  // left
  const listed = Boolean(recipes) && rows.length === 0;
  setHidden(ui.hint, !draft);
  setHidden(ui.list, listed);
  setHidden(ui.listEmpty, !listed);
  const pager = ctx.dom.pager(ui.listPager, rows.length, LIST_ROWS, again);
  ctx.dom.list(ui.list, pager.slice(rows), (item) => item.id, makeItem, updateItem);
  setDisabled(ui.add, Boolean(draft));
  setDisabled(ui.file, Boolean(draft));
  ctx.dom.setClass(ui.fileButton, 'off', Boolean(draft));

  // right
  const mode = draft ? 'draft' : row ? 'published' : 'none';
  setHidden(ui.none, mode !== 'none' || !recipes);
  setHidden(ui.view, mode === 'none');
  setHidden(ui.factBox, mode !== 'published');
  setHidden(ui.pubFoot, mode !== 'published');
  setHidden(ui.draftBox, mode !== 'draft');
  setHidden(ui.editBar, mode !== 'draft');
  setHidden(ui.draftFoot, mode !== 'draft');
  if (mode === 'draft') drawDraft(ctx);
  else if (mode === 'published') drawPublished(ctx, state, row);
  else {
    ctx.dom.setText(ui.title, 'Recipe');
    ctx.dom.setText(ui.sub, '');
  }
}

export function show(ctx) {
  if (ctx.store.state) render(ctx.store.state, ctx);
}
