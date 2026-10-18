//maint-plasma.js — Maintenance › Plasma parameters (SPEC E.12, AMENDMENTS M1, M3): the seven plasma parameters on the RPi, the
//background-frame tile, "Clear Nano Fault", and one primary button whose label follows the state.
//Nothing here switches the high voltage on: the only way to discharge is scheduled measurement.
// The cross rules (pulses x loops, interval against integration time, total time) are the RPi's: when it refuses,
// its sentence goes to the message chip.

import { act, busyReason, gbtn, setGbtn, setDisabled } from './maint.js';
//The table of the seven parameters, and what scheduled measurement uses of them too (AMENDMENTS M17).
import { PARAMS, T_NOT_READ, text, topVoltage } from '../plasma.js';

const T_NO_LINK = "RPi not connected";
const T_NO_FAULT = "currently has no faults";

let ui = null;
let page = null;
let edits = {};                // key -> the number typed here and not saved yet
let saving = false;

function again() {
  if (ui && page && page.store.state) render(page.store.state, page);
}

function valuesOf(state, ctx) {
  const parameters = state.rpi && state.rpi.parameters;
  return !ctx.store.stale && parameters && parameters.values ? parameters.values : null;
}

async function editValue(ctx, item) {
  const state = ctx.store.state;
  const values = state && valuesOf(state, ctx);
  if (!values) return;
  const max = item.key === 'Voltage' ? topVoltage(state) : item.max;
  const current = item.key in edits ? edits[item.key] : values[item.key];
  const value = await ctx.dialog.pad({title: item.label, min: item.min, max, decimals: item.decimals, unit: item.unit, value: current});
  if (value === null) return;
  edits[item.key] = value;
  again();
}

async function save(ctx) {
  const state = ctx.store.state;
  const values = state && valuesOf(state, ctx);
  if (!values || saving) return;
  const changed = PARAMS.filter((item) => item.key in edits && edits[item.key] !== values[item.key]);
  if (!changed.length) return;
  const next = {...values, ...edits};
  const yes = await ctx.dialog.confirm({title: "stores plasma parameters", confirmLabel: "Save and sync\nAfter",
    lines: changed.map((item) => [item.label, `${text(values[item.key])} → ${text(edits[item.key])}`]),
    warn: `, each measurement will be discharged at ${text(next.Voltage)} V (upper limit ${topVoltage(state)} V). Storage does not initiate high pressure.`});
  if (!yes) return;
  const parameters = {};
  for (const item of PARAMS) parameters[item.key] = next[item.key];
  saving = true;
  again();
  const reply = await act(ctx, () => ctx.api.post('/api/parameters', {parameters}),
    (out) => (out.synced === false ? `saved, but failed to sync Nano: ${out.sync_error || ''}` : "saved and synced to Nano"));
  saving = false;
  if (reply) edits = {};
  again();
}

async function sync(ctx) {
  if (saving) return;
  const yes = await ctx.dialog.confirm({title: "sync to Nano", detail: "writes the parameters of the RPi configuration file into Nano and reads them back. High voltage will not start."});
  if (!yes) return;
  saving = true;
  again();
  await act(ctx, () => ctx.api.post('/api/rpi/tool', {cmd: 'prepare'}), "synced to Nano");
  saving = false;
  again();
}

function faultOf(state, ctx) {
  const snapshot = state.rpi && state.rpi.status && state.rpi.status.snapshot;
  const nano = snapshot && snapshot.nano;
  if (!nano || nano.valid !== true || !(nano.state === 'FAULT' || nano.fault !== 'NONE')) return '';
  return nano.fault && nano.fault !== 'NONE' ? ctx.labels.fault[nano.fault] || String(nano.fault) : "fault";
}

async function clearFault(ctx) {
  const state = ctx.store.state;
  const name = state ? faultOf(state, ctx) : '';
  if (!name) return;
  const yes = await ctx.dialog.confirm({title: "Clear Nano fault\nAfter clearing", confirmLabel: "Clear", lines: [["fault", name]], detail: ", please click \"Sync to Nano\" again."});
  if (!yes) return;
  await act(ctx, () => ctx.api.post('/api/rpi/tool', {cmd: 'clear_fault'}),
    (out) => (out.cleared === false ? "Nano currently has no faults" : `Nano fault cleared: ${name}`));
}

function primary(ctx) {
  if (ui.action === 'save') save(ctx);
  else if (ui.action === 'sync') sync(ctx);
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;
  const tiles = PARAMS.map((item) => {
    const value = h('b');
    const small = h('small');
    const button = h('button.pbtn', {onclick: () => { editValue(ctx, item); }}, h('span', null, item.label), value, small);
    return {item, button, value, small};
  });
  const frames = h('b');
  const framesNote = h('small');
  const background = h('div.pbtn.local', null, h('span', null, "background spectrum"), frames, framesNote);
  const clear = gbtn(ctx, "Clear Nano fault\nAfter clearing", '', () => { clearFault(ctx); });
  const note = h('span.sub');
  const drop = h('button.btn', {onclick: () => { edits = {}; again(); }}, "Give up modification");
  const main = h('button.btn.primary', {onclick: () => primary(ctx)}, "synchronized");
  root.replaceChildren(h('div.panel.adm-full', null,
    h('div.ph', null, h('h2', null, "plasma parameters"), h('span.sub', null, "point value modification. The high pressure should be turned off during storage and the high pressure should not be turned on.")),
    h('div.param-grid', null, tiles.map((tile) => tile.button), background),
    h('div.btn-grid', null, clear.button),
    h('div.adm-fill'),
    h('div.adm-foot', null, note, drop, main)));
  ui = {tiles, background, frames, framesNote, clear, note, drop, main, action: ''};
}

export function render(state, ctx) {
  const {setText, setClass} = ctx.dom;
  const rpi = state.rpi || {};
  const values = valuesOf(state, ctx);
  const busy = busyReason(ctx, state);
  const off = busy || (values ? '' : T_NOT_READ);
  if (values) {
    for (const key of Object.keys(edits)) if (edits[key] === values[key]) delete edits[key];    // typed back to what the RPi has
  }
  const dirty = values ? Object.keys(edits).length > 0 : false;

  for (const tile of ui.tiles) {
    const item = tile.item;
    const changed = Boolean(values) && item.key in edits;
    const max = item.key === 'Voltage' ? topVoltage(state) : item.max;
    setText(tile.value, values ? text(changed ? edits[item.key] : values[item.key]) : '—');
    setText(tile.small, changed ? `RPi currently ${text(values[item.key])}` : `Range ${item.min} – ${max}`);
    setClass(tile.button, 'changed', changed);
    setDisabled(tile.button, Boolean(off) || saving);
  }

  //background spectrum: how many frames, by the RPi's measuring flow (not a setting of this page)
  const flow = rpi.flow || {};
  const known = !ctx.store.stale && rpi.connected;
  const count = known && typeof flow.background_frames === 'number' ? flow.background_frames : null;
  setText(ui.frames, count === null ? '—' : `${count} Zhang`);
  setText(ui.framesNote, !known ? (ctx.store.stale ? "The program did not respond" : T_NO_LINK)
    : flow.name === 'unified' && flow.ok !== false ? "high voltage on, shot before discharge"
      : flow.name === 'verified' && flow.ok !== false ? "RPi is set to the old process\nThe program on" : flow.text || "RPi is still an old version\nA measurement of");
  setClass(ui.background, 'warn', Boolean(known && flow.ok === false));

  const fault = values || rpi.connected ? faultOf(state, ctx) : '';
  setGbtn(ctx, ui.clear, busy || (!rpi.connected ? T_NO_LINK : !fault ? T_NO_FAULT : ''), fault ? `currently ${fault}` : '');

  // the line of the footer: why nothing can be changed, or what one measurement takes
  const status = rpi.status || {};
  const seconds = (value) => (typeof value === 'number' ? String(Math.round(value * 10) / 10) : '—');
  setText(ui.note, off || `is estimated to take about ${seconds(status.estimate_s)} seconds. After pressing, it will start discharging in about ${seconds(status.start_s)} seconds.`);
  setClass(ui.note, 'txt-warn', Boolean(off));

  setDisabled(ui.drop, !dirty || saving);
  const synced = rpi.parameters ? rpi.parameters.nano_synced : null;
  //“Synced” only when the Nano is known to hold the same values; unknown is not "synced".
  ui.action = dirty ? 'save' : values && synced === true ? '' : 'sync';
  setText(ui.main, ui.action === 'save' ? "Save and sync\nAfter" : ui.action === 'sync' ? "sync to Nano" : "synchronized");
  setDisabled(ui.main, !ui.action || Boolean(off) || saving);
}
