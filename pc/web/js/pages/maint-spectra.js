//maint-spectra.js — Maintenance › Spectrum (SPEC E.14): the stored spectrum sets on the left; on the right the chosen set as a
// chart (mean, one frame, all frames) or frame by frame (peak, total, deviation from the median — the original's
//check). "Capture spectrum (without discharge)" reads three frames without high voltage; the set shows up in the list by itself.
// The list follows state.revs.runs: there is no refresh button.

import { act, report, busyReason, setHidden, setDisabled } from './maint.js';

const LIST_ROWS = 5;
const CHECK_ROWS = 10;
const DEV_LIMIT = 0.2;         // a frame whose total is more than 20 % off the median is marked
const PEAK_HALF_NM = 6;        //“Near the highest point”: this much to each side of the highest point
const CAPTURE_EXTRA_MS = 90000;
const T_NO_LINK = "RPi not connected";
const T_NO_PARAMETERS = "Unable to read plasma parameters";

let ui = null;
let page = null;
let runs = null;               // rows of GET /api/runs; null until the first answer
let runsRev = null;
let listLoading = false;
let selected = '';             // job id of the chosen set
let run = null;                // the loaded set: {job, plasma, axis, mean, frames, checks, info…}
let loading = '';              // job id being read
let problem = '';              // why the chosen set cannot be shown
let view = 'chart';            // chart | check
let mode = 'mean';             // mean | single | overlay
let frame = 0;
let range = 'full';            // full | peak
let drawn = '';                // what the chart shows now
let capture = null;            // {job, since, until}: a capture of this page that is not in the list yet

function again() {
  if (ui && page && page.store.state) render(page.store.state, page);
}

// ── data ────────────────────────────────────────────────────────────────────────────────────────────────────
async function loadList(ctx, rev) {
  if (listLoading) return;
  listLoading = true;
  let arrived = false;
  try {
    const reply = await ctx.api.get('/api/runs');
    runs = Array.isArray(reply.runs) ? reply.runs : [];
    runsRev = rev;
    arrived = true;
  } catch (_) { /* asked again by the next poll */ }
  listLoading = false;
  if (arrived) again();
}

// Per-frame check as in the original: peak, total, and how far the total is from the median of all frames.
function frameChecks(axis, frames, plasma) {
  const rows = frames.map((values) => {
    let peak = -Infinity;
    let at = 0;
    let total = 0;
    values.forEach((value, index) => {
      total += value;
      if (value > peak) { peak = value; at = index; }
    });
    return {peak, nm: axis[at], total};
  });
  const sorted = rows.map((row) => row.total).sort((a, b) => a - b);
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
  return rows.map((row) => {
    const dev = median ? (row.total - median) / Math.abs(median) : 0;
    const flags = [];
    if (Math.abs(dev) > DEV_LIMIT) flags.push("difference > 20%");
    if (plasma && row.total < 0) flags.push("below background");
    return {...row, dev, flag: flags.join(', ')};
  });
}

function shape(data) {
  const plasma = data.kind === 'plasma';
  const axis = Array.isArray(data.axis) ? data.axis : [];
  const mean = (plasma ? data.difference : data.mean) || [];
  const frames = Array.isArray(data.frames) ? data.frames.filter((item) => Array.isArray(item) && item.length === axis.length) : [];
  let top = 0;
  for (let index = 1; index < mean.length; index += 1) if (mean[index] > mean[top]) top = index;
  return {job: data.job, plasma, axis, mean, frames, checks: frameChecks(axis, frames, plasma), peakNm: axis[top],
    started: data.started_at || data.saved_at, signal: data.signal_frames, background: data.background_frames,
    integration: data.integration_ms, voltage: data.voltage_v, wait: data.timing ? data.timing.wait_s : null};
}

async function select(ctx, job) {
  if (!job || job === selected) return;
  selected = job;
  run = null;
  problem = '';
  frame = 0;
  mode = 'mean';
  view = 'chart';
  range = 'full';
  drawn = '';
  loading = job;
  again();
  try {
    const data = await ctx.api.get(`/api/runs/${job}`);
    if (selected === job) run = shape(data);
  } catch (error) {
    if (selected === job) problem = "Failed to read spectrum:" + (error && error.message ? error.message : error);
  }
  if (loading === job) loading = '';
  again();
}

// ── capture without discharge ───────────────────────────────────────────────────────────────────────────────
async function startCapture(ctx) {
  const yes = await ctx.dialog.confirm({title: "capture spectrum (no discharge)", confirmLabel: "capture",
    detail: "only checks the spectrometer: read 3 images using the integration time of plasma parameters, without turning on high voltage. This is not a measurement background."});
  if (!yes) return;
  const since = Date.now();
  const reply = await act(ctx, () => ctx.api.post('/api/rpi/tool', {cmd: 'spectrum'}), {text: "is capturing the spectrum. It will appear in the list after completion.", level: 'busy'});
  if (!reply || !reply.job) return;
  const seconds = typeof reply.estimate_s === 'number' ? reply.estimate_s : 15;
  capture = {job: reply.job, since, until: since + seconds * 1000 + CAPTURE_EXTRA_MS};
}

// The capture runs on the RPi; this PC stores the set when it has ended (revs.runs changes) or writes an event.
function watchCapture(ctx, state) {
  if (!capture) return;
  if (runs && runs.some((row) => row.job === capture.job)) {
    const job = capture.job;
    capture = null;
    report("acquisition completed, spectrum has been saved in the list");
    ctx.dom.pager(ui.listPager, runs.length, LIST_ROWS, again).set(0);
    select(ctx, job);
    return;
  }
  const message = state.message;
  if (message && message.level === 'error' && String(message.text).startsWith("spectrum acquisition") && Date.parse(message.time) >= capture.since - 2000) {
    capture = null;
    report(message.text, 'err');
  } else if (Date.now() > capture.until) {
    capture = null;
    report("The spectrum acquisition was not completed within the expected time.", 'err');
  }
}

// ── drawing ─────────────────────────────────────────────────────────────────────────────────────────────────
function drawChart(ctx) {
  const key = [run.job, mode, frame, range].join('|');
  if (key === drawn) return;
  drawn = key;
  let series;
  if (mode === 'single' && run.frames[frame]) series = [{values: run.frames[frame], cls: 'sl-frame'}];
  else if (mode === 'overlay' && run.frames.length) series = [...run.frames.map((values) => ({values, cls: 'sl-over'})), {values: run.mean, cls: 'sl-mean'}];
  else series = [{values: run.mean, cls: 'sl-mean'}];
  const axis = run.axis;
  const peak = run.peakNm;
  const span = range === 'peak' && typeof peak === 'number'
    ? [Math.max(axis[0], peak - PEAK_HALF_NM), Math.min(axis[axis.length - 1], peak + PEAK_HALF_NM)] : null;
  ctx.chart.drawSpectrum(ui.chart, {axis, series, range: span});
  const tail = run.plasma ? "(button background)" : '';
  ctx.dom.setText(ui.legend, mode === 'single' ? `Blue: No. ${frame + 1} ${tail}`
    : mode === 'overlay' ? `Light Blue: Each · Black: Average ${run.plasma ? "(all with background)" : ''}`
      : `Black: Average ${run.signal === null || run.signal === undefined ? run.frames.length : run.signal} Zhang ${tail}`);
}

function infoText() {
  const parts = [];
  if (run.plasma) {
    parts.push(`Background ${run.background === null || run.background === undefined ? '—' : run.background} Zhang`, `signal ${run.signal}`);
  } else {
    parts.push("spectrometer inspection", `${run.signal} Zhang`);
  }
  if (typeof run.integration === 'number') parts.push(`points ${run.integration} ms`);
  if (run.plasma && typeof run.voltage === 'number') parts.push(`${run.voltage} V`);
  if (run.plasma && typeof run.wait === 'number') parts.push(`starts discharging in ${run.wait.toFixed(1)} seconds after pressing`);
  if (!run.plasma) parts.push("does not turn on high voltage");
  if (!run.frames.length) parts.push("No single information");
  return parts.join(' · ');
}

function makeItem(row) {
  const {h} = page.dom;
  const time = h('b');
  const text = h('span');
  const button = h('button.li', {onclick: () => { select(page, row.job); }}, time, text);
  button.parts = {time, text};
  return button;
}

function updateItem(button, row) {
  const {setText, setClass} = page.dom;
  setText(button.parts.time, page.labels.fmtTime(row.started_at || row.saved_at, 'MM-DD HH:MM:SS'));
  const frames = row.signal_frames === null || row.signal_frames === undefined ? '—' : row.signal_frames;
  setText(button.parts.text, row.kind === 'capture' ? `Spectrometer Inspection · ${frames} Zhang`
    : `${row.recipe_name || "manual discharge"} · Signal ${frames} sheets · Background ${row.background_frames === null || row.background_frames === undefined ? '—' : row.background_frames} sheets`);
  setClass(button, 'sel', row.job === selected);
}

function makeCheck(_row, index) {
  const {h} = page.dom;
  const cells = {no: h('span.c-no'), peak: h('span.c-num'), nm: h('span.c-num'), total: h('span.c-num'), dev: h('span.c-num'), flag: h('span.c-flag')};
  const node = h('div.trow', {attrs: {role: 'button'}, tabIndex: 0}, Object.values(cells));
  node.parts = cells;
  const press = () => {                                    //a tap shows that frame in spectrogram
    const shown = ui.checks[index];
    if (!shown) return;
    frame = shown.at;
    mode = 'single';
    view = 'chart';
    again();
  };
  node.addEventListener('click', press);
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); press(); }
  });
  return node;
}

function updateCheck(node, shown) {
  const {setText, setClass} = page.dom;
  const row = shown.row;
  setText(node.parts.no, shown.at + 1);
  setText(node.parts.peak, row.peak.toFixed(0));
  setText(node.parts.nm, typeof row.nm === 'number' ? row.nm.toFixed(2) : '—');
  setText(node.parts.total, row.total.toFixed(0));
  setText(node.parts.dev, `${row.dev > 0 ? '+' : ''}${(row.dev * 100).toFixed(1)}%`);
  setText(node.parts.flag, row.flag);
  setClass(node, 'flag', Boolean(row.flag));
  setClass(node, 'sel', shown.at === frame);
}

function seg(ctx, items, onPick) {
  const buttons = items.map(([value, label]) => ctx.dom.h('button', {dataset: {value}, onclick: () => onPick(value)}, label));
  return {box: ctx.dom.h('div.seg', null, buttons), buttons};
}

function setSeg(ctx, item, value, off) {
  for (const button of item.buttons) {
    ctx.dom.setClass(button, 'sel', button.dataset.value === value);
    setDisabled(button, Boolean(off && off(button.dataset.value)));
  }
}

function step(by) {
  const count = run ? run.frames.length : 0;
  if (!count) return;
  frame = (frame + by + count) % count;
  again();
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;
  const list = h('div.list');
  const listEmpty = h('div.empty', {hidden: true}, h('p', null, "has no spectra archived yet. After the scheduled measurement is completed, it will be automatically stored here."));
  const listPager = h('div');
  const why = h('span.why', {hidden: true});
  const grab = h('button.btn', {onclick: () => { startCapture(ctx); }}, "capture spectrum (no discharge)");
  const left = h('div.panel.spec-list', null, h('div.ph', null, h('h2', null, "spectrum")), list, listEmpty, listPager,
    h('div.adm-foot', null, why, grab));

  const title = h('h2');
  const info = h('span.spec-info');
  const none = h('div.empty', null, h('b', null, "Select the set of spectra on the left"));
  const noneText = none.firstChild;
  const views = seg(ctx, [['chart', "Spectrum"], ['check', "single check"]], (value) => { view = value; again(); });
  const csv = h('a.btn', {href: '#', attrs: {download: ''}}, "Download CSV");
  const modes = seg(ctx, [['mean', "Avg."], ['single', "leaflet"], ['overlay', "All overlays"]], (value) => { mode = value; again(); });
  const frameLabel = h('b');
  const nav = h('div.fnav', {hidden: true}, h('button.btn', {onclick: () => step(-1)}, '◀'), frameLabel, h('button.btn', {onclick: () => step(1)}, '▶'));
  const ranges = seg(ctx, [['full', "full spectrum"], ['peak', "Near the highest peak"]], (value) => { range = value; again(); });
  const legend = h('div.lgd');
  const chart = h('div.chart', null, legend);
  const chartBox = h('div.adm-view', null, h('div.spec-ctl', null, modes.box, nav, h('span.sp'), ranges.box), chart);
  const tbody = h('div.tbody');
  const noFrames = h('div.empty', {hidden: true}, h('p', null, "This group does not have single data (old records or too many pictures), and can only see the average spectrum."));
  const checkPager = h('div');
  const checkBox = h('div.adm-view.table', {hidden: true},
    h('div.thead', null, h('span.c-no', null, "Zhang times"), h('span.c-num', null, "peak"), h('span.c-num', null, "peak wavelength"),
      h('span.c-num', null, "Total intensity"), h('span.c-num', null, "differs from the median"), h('span.c-flag', null, "Reminder")),
    tbody, noFrames, checkPager);
  const body = h('div.adm-view', {hidden: true}, h('div.spec-ctl', null, views.box, h('span.sp'), csv), chartBox, checkBox);
  const right = h('div.panel.spec-main', null, h('div.ph', null, title, info), none, body);

  root.replaceChildren(h('div.adm-body', null, left, right));
  ui = {list, listEmpty, listPager, why, grab, title, info, none, noneText, views, csv, modes, nav, frameLabel, ranges, legend, chart,
    chartBox, tbody, noFrames, checkPager, checkBox, body, checks: [], linked: ''};
}

export function render(state, ctx) {
  const {setText} = ctx.dom;
  const rev = state.revs ? state.revs.runs : 0;
  if (runsRev !== rev) loadList(ctx, rev);
  watchCapture(ctx, state);

  // left
  const rows = runs || [];
  const empty = Boolean(runs) && rows.length === 0;
  setHidden(ui.list, empty);
  setHidden(ui.listEmpty, !empty);
  const pager = ctx.dom.pager(ui.listPager, rows.length, LIST_ROWS, again);
  ctx.dom.list(ui.list, pager.slice(rows), (row) => row.job, makeItem, updateItem);
  const rpi = state.rpi || {};
  const reason = busyReason(ctx, state) || (!rpi.connected ? T_NO_LINK : !(rpi.parameters && rpi.parameters.values) ? T_NO_PARAMETERS : '');
  setDisabled(ui.grab, Boolean(reason));
  setText(ui.why, reason);
  setHidden(ui.why, !reason);

  // right
  const ready = Boolean(run && run.job === selected);
  setHidden(ui.none, ready);
  setHidden(ui.body, !ready);
  if (!ready) {
    setText(ui.title, "spectrum");
    setText(ui.info, '');
    setText(ui.noneText, problem || (loading ? "Reading spectrum..." : "Select the set of spectra on the left"));
    return;
  }
  const count = run.frames.length;
  if (!count) mode = 'mean';
  if (frame >= count) frame = 0;
  setText(ui.title, ctx.labels.fmtTime(run.started, 'MM-DD HH:MM:SS'));
  setText(ui.info, infoText());
  const href = `/api/runs/${run.job}.csv`;
  if (ui.linked !== href) { ui.linked = href; ui.csv.setAttribute('href', href); }
  setSeg(ctx, ui.views, view);
  setHidden(ui.chartBox, view !== 'chart');
  setHidden(ui.checkBox, view !== 'check');
  if (view === 'chart') {
    setSeg(ctx, ui.modes, mode, (value) => value !== 'mean' && !count);
    setSeg(ctx, ui.ranges, range);
    setHidden(ui.nav, mode !== 'single');
    setText(ui.frameLabel, `No. ${frame + 1} / ${count}`);
    drawChart(ctx);
    return;
  }
  setHidden(ui.tbody, !count);
  setHidden(ui.noFrames, count > 0);
  const turn = ctx.dom.pager(ui.checkPager, count, CHECK_ROWS, again);
  const first = turn.page * CHECK_ROWS;
  ui.checks = turn.slice(run.checks).map((row, index) => ({row, at: first + index}));
  ctx.dom.list(ui.tbody, ui.checks, (_item, index) => index, makeCheck, updateCheck);
}

export function show(ctx) {
  if (ctx.store.state) render(ctx.store.state, ctx);
}
