//maint-coefficients.js — Maintenance › Correction coefficients (AMENDMENTS M5.5): the nine numbers of concentration = (a × light intensity + b) × c for
// Cu / Zn / Ni, changed with the number pad (it has a “±” key here) and saved together; below them, read-only, the
// analysis method, the settings file and the calibration that is applied.
// Data: GET /api/calibration (coefficients, settings, active), read again when state.revs.calibrations changes.

import { act, busyReason, kvRow, setValue, setDisabled } from './maint.js';

const COEFFICIENTS = ['a', 'b', 'c'];
const FALLBACK_METALS = ['Cu', 'Zn', 'Ni'];
const PAD_MIN = -1000000;
const PAD_MAX = 1000000;
const PAD_DECIMALS = 6;

let ui = null;
let page = null;
let data = null;               // reply of GET /api/calibration
let dataRev = null;
let loading = false;
let edits = {};                // 'Cu.a' -> the text that will be saved
let saving = false;

function again() {
  if (ui && page && page.store.state) render(page.store.state, page);
}

async function load(ctx, rev) {
  if (loading) return;
  loading = true;
  let arrived = false;
  try {
    data = await ctx.api.get('/api/calibration');
    dataRev = rev;
    arrived = true;
  } catch (_) { /* asked again by the next poll */ }
  loading = false;
  if (arrived) again();
}

const stored = (metal, name) => {
  const value = data && data.coefficients && data.coefficients[metal] ? data.coefficients[metal][name] : null;
  return value === undefined ? null : value;
};

// A coefficient the way the tile shows it: as it is stored when that is short, else with 8 significant digits.
function shown(value) {
  if (value === null || value === undefined || value === '') return "Not set";
  const text = String(value);
  const number = Number(text);
  if (!Number.isFinite(number)) return text;
  return text.length <= 12 && !/e/i.test(text) ? text : String(Number(number.toPrecision(8)));
}

// What the pad starts with for a stored value, and what it gives back when nothing is typed.
const padStart = (value) => Number(Number(value).toFixed(PAD_DECIMALS));

async function editCell(ctx, metal, name) {
  if (!data) return;
  const key = `${metal}.${name}`;
  const before = stored(metal, name);
  const current = key in edits ? edits[key] : before;
  const known = current !== null && current !== '' && Number.isFinite(Number(current));
  const value = await ctx.dialog.pad({title: `${metal} coefficient ${name}`, min: PAD_MIN, max: PAD_MAX, decimals: PAD_DECIMALS, signed: true,
    value: known ? Number(current) : ''});
  if (value === null) return;
  const same = before !== null && Number.isFinite(Number(before)) && (value === Number(before) || value === padStart(before));
  if (same) delete edits[key];           // the stored number, only shown with six decimals: nothing changes
  else edits[key] = String(value);
  again();
}

async function save(ctx) {
  const keys = Object.keys(edits);
  if (!data || !keys.length || saving) return;
  const metals = data.metals || FALLBACK_METALS;
  const yes = await ctx.dialog.confirm({title: "storage correction coefficient", confirmLabel: "storage",
    lines: keys.map((key) => {
      const [metal, name] = key.split('.');
      return [`${metal} coefficient ${name}`, `${shown(stored(metal, name))} → ${shown(edits[key])}`];
    })});
  if (!yes) return;
  const config = {};
  for (const metal of metals) {
    config[metal] = {};
    for (const name of COEFFICIENTS) {
      const key = `${metal}.${name}`;
      config[metal][name] = key in edits ? edits[key] : stored(metal, name);
    }
  }
  saving = true;
  again();
  const reply = await act(ctx, () => ctx.api.post('/api/calibration-config', {config}), "Calibration coefficients stored");
  saving = false;
  if (reply) {
    if (reply.config && data) data = {...data, coefficients: reply.config};
    edits = {};
  }
  again();
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;
  const metals = FALLBACK_METALS;
  const cells = {};
  const grid = [h('span'), ...COEFFICIENTS.map((name) => h('span.cal-h', null, name))];
  for (const metal of metals) {
    grid.push(h('b.cal-m', null, metal));
    for (const name of COEFFICIENTS) {
      const value = h('b');
      const small = h('small');
      const button = h('button.pbtn', {onclick: () => { editCell(ctx, metal, name); }}, value, small);
      cells[`${metal}.${name}`] = {button, value, small, metal, name};
      grid.push(button);
    }
  }
  const facts = {method: kvRow(ctx, "Analysis method"), file: kvRow(ctx, "analysis profile"), active: kvRow(ctx, "Calibration in application")};
  const note = h('span.sub');
  const drop = h('button.btn', {onclick: () => { edits = {}; again(); }}, "Give up modification");
  const keep = h('button.btn.primary', {onclick: () => { save(ctx); }}, "storage");
  root.replaceChildren(h('div.panel.adm-full', null,
    h('div.ph', null, h('h2', null, "Cu/Zn/Ni correction coefficient"), h('span.sub', null, "concentration = (a × light intensity + b) × c; when applying correction, a and b will be overwritten, and c will be reset to 1")),
    h('div.cal-grid', null, grid),
    h('div.kv', null, Object.values(facts).map((item) => item.row)),
    h('div.adm-fill'),
    h('div.adm-foot', null, note, drop, keep)));
  ui = {cells, facts, note, drop, keep};
}

export function render(state, ctx) {
  const {setText, setClass} = ctx.dom;
  const rev = state.revs ? state.revs.calibrations : 0;
  if (dataRev !== rev) load(ctx, rev);
  const busy = busyReason(ctx, state);
  const off = busy || (data ? '' : "Reading...");

  for (const [key, cell] of Object.entries(ui.cells)) {
    const before = data ? stored(cell.metal, cell.name) : null;
    const changed = key in edits;
    const value = changed ? edits[key] : before;
    setText(cell.value, data ? shown(value) : '—');
    setText(cell.small, changed ? `currently ${shown(before)}` : '');
    setClass(cell.button, 'changed', changed);
    setClass(cell.button, 'q-na', Boolean(data) && !changed && (before === null || before === ''));
    setDisabled(cell.button, Boolean(off) || saving);
  }

  const settings = (data && data.settings) || {};
  setValue(ctx, ui.facts.method, data ? settings.method_id || '—' : '—', '', settings.method_id);
  const fileText = 'data\\analysis_config.json' + (settings.error ? `: ${settings.error}` : '');
  setValue(ctx, ui.facts.file, fileText, settings.error ? 'q-bad' : '', fileText);
  const active = data ? data.active : null;
  const activeText = active ? `${ctx.labels.fmtTime(active.applied_at, 'YYYY-MM-DD HH:MM')} · ${(active.metals || []).join(', ')}` : "has not been applied yet";
  setValue(ctx, ui.facts.active, data ? activeText : '—', active || !data ? '' : 'q-na', activeText);

  const dirty = Object.keys(edits).length > 0;
  setText(ui.note, off || (dirty ? "will be written only after pressing \"Save\" after modification." : ''));
  setClass(ui.note, 'txt-warn', Boolean(busy));
  setDisabled(ui.drop, !dirty || saving);
  setDisabled(ui.keep, !dirty || Boolean(off) || saving);
}

export function show(ctx) {
  if (ctx.store.state) render(ctx.store.state, ctx);
}
