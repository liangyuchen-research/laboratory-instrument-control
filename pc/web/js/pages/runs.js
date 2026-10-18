//runs.js — Execution record (SPEC E.7, AMENDMENTS M5.4): the run log, and the detail of one round inside the same page.
//
//   list     GET /api/runlog?limit=200 (asked again when state.revs.runlog changes): one row per finished round and
//            per slot that did not run. A row with a round or a spectrum opens its detail
//detail “Spectrum” the facts of one discharge beside its spectrum (GET /api/runs/<job>, drawn by ctx.chart) ·
//“Step” the step table of Overview D3 (GET /api/rounds/<id>)
// Other pages open a detail with ctx.go('runs', {round_id, job}).

import { stepTable } from './dash.js';

const LIST_ROWS = 9;
const STEP_ROWS = 9;
const LOG_LIMIT = 200;
const RETRY_MS = 5000;
const PEAK_COUNT = 5;
const PEAK_APART_NM = 5;          // two chips are never closer than this
const PEAK_FLOOR = 0.05;          // a peak counts from this part of the highest one
const ZOOM_NM = 10;               // a chip shows its peak ± this much
const ROUND_ID = /^[0-9a-f]{32}$/;
const JOB_ID = /^(?:HMI|CAP|LITE)-[0-9a-f]{32}$/;
const METALS = ['Cu', 'Zn', 'Ni'];
const STATUS_CLASS = {completed: 'completed', done: 'completed', failed: 'failed', not_started: 'failed', running: 'running',
  stopped: 'stopped', interrupted: 'stopped', skipped_overrun: 'skipped', skipped_paused: 'skipped'};
const NO_SPECTRUM = "There is no spectrum this time (the Recipe has no measurement steps, or the measurement has not been completed)";

let el = null;
let table = null;
let shown = false;
let detail = null;                // null: the list is on screen
const log = {rev: -1, rows: null, pending: false, failedAt: 0};
const rowFacts = new WeakMap();
const chipFacts = new WeakMap();

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
const sig3 = (value) => String(Number(Number(value).toPrecision(3)));
const trim = (value, places) => String(Number(Number(value).toFixed(places)));

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

// ── list ────────────────────────────────────────────────────────────────────────────────────────────────────
function wantLog(ctx, rev) {
  if (log.pending || (log.rows && log.rev === rev)) return;
  if (Date.now() - log.failedAt < RETRY_MS) return;
  log.pending = true;
  ctx.api.get(`/api/runlog?limit=${LOG_LIMIT}`, {timeout: 8000}).then((reply) => {
    log.rows = Array.isArray(reply.rows) ? reply.rows : [];
    log.rev = rev;
    log.failedAt = 0;
  }, () => {
    log.failedAt = Date.now();
  }).then(() => {
    log.pending = false;
    again(ctx);
  });
}

const rowKey = (row) => [row.kind, row.round_id || '', row.schedule_id || '', row.slot === null || row.slot === undefined ? '' : row.slot, row.time].join('|');
const opens = (row) => Boolean(row.round_id || (row.spectra && row.spectra.length));
const slotText = (slot, planned) => (slot ? (planned ? `${slot}/${planned}` : String(slot)) : '');
const statusWord = (labels, row) => (row.kind === 'slot' ? labels.slot[row.status] : labels.round[row.status]) || row.status || '';

function renderList(state, ctx) {
  const {dom, labels} = ctx;
  wantLog(ctx, (state.revs && state.revs.runlog) || 0);
  const rows = log.rows || [];
  const pager = dom.pager(el.listPager, rows.length, LIST_ROWS, () => again(ctx));
  const empty = log.rows !== null && !rows.length;
  setHidden(el.listBody, empty);
  setHidden(el.listEmpty, !empty);
  dom.list(el.listBody, pager.slice(rows), rowKey, (row) => {
    const note = dom.h('span.c-err');
    const cells = [dom.h('span.c-time'), dom.h('span.c-op'), dom.h('span.c-recipe'), dom.h('span.c-slot'), dom.h('span.c-hm'),
      dom.h('span.c-status', null, dom.h('span')), dom.h('span.c-conc'), note];
    // a round or a stored spectrum: the whole row opens it; a slot that did not run has only its note to show
    if (opens(row)) {
      const line = dom.h('button.trow', null, cells);
      line.addEventListener('click', () => { const now = rowFacts.get(line); if (now) openDetail(ctx, now); });
      return line;
    }
    const line = dom.h('div.trow', null, cells);
    const tell = () => { const now = rowFacts.get(line); if (now && now.note) ctx.dialog.message("Remarks", now.note); };
    note.addEventListener('click', tell);
    note.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); tell(); } });
    return line;
  }, (line, row) => {
    rowFacts.set(line, row);
    const [time, operator, recipe, slot, finished, status, result, note] = line.children;
    const word = statusWord(labels, row);
    dom.setText(time, labels.fmtTime(row.time, 'MM-DD HH:MM:SS'));
    dom.setText(operator, row.operator || '');
    dom.setText(recipe, row.recipe_name || '');
    setTitle(recipe, row.recipe_name || '');
    dom.setText(slot, slotText(row.slot, row.slots_planned));
    dom.setText(finished, row.finished_at ? labels.fmtTime(row.finished_at, 'HH:MM') : '');
    const pill = status.firstChild;
    dom.setText(pill, word);
    const cls = 'st ' + (STATUS_CLASS[row.status] || '');
    if (pill.className !== cls) pill.className = cls;
    dom.setText(result, row.result_text || '');
    setTitle(result, row.result_text || '');
    dom.setText(note, row.note || '');
    setTitle(note, row.note || '');
    const tappable = Boolean(row.note) && line.tagName !== 'BUTTON';      // the note of a row that opens nothing else
    dom.setClass(note, 'tap', tappable);
    if (tappable !== (note.getAttribute('role') === 'button')) {
      if (tappable) { note.setAttribute('role', 'button'); note.tabIndex = 0; }
      else { note.removeAttribute('role'); note.removeAttribute('tabindex'); }
    }
    dom.setClass(line, 'long-st', word.length > 4);
  });
}

// ── detail: what is known about it ──────────────────────────────────────────────────────────────────────────
const validJobs = (list) => (list || []).filter((job) => typeof job === 'string' && JOB_ID.test(job));

// arg: a row of the run log, or {round_id, job} from another page.
function openDetail(ctx, arg) {
  const roundId = typeof arg.round_id === 'string' && ROUND_ID.test(arg.round_id) ? arg.round_id : null;
  const jobs = validJobs(arg.spectra && arg.spectra.length ? arg.spectra : [arg.job]);
  if (!roundId && !jobs.length) return;
  const wanted = typeof arg.job === 'string' ? jobs.indexOf(arg.job) : -1;
  detail = {
    row: arg.kind ? arg : null,
    roundId,
    jobs,
    fromRow: Boolean(arg.spectra),      // the run log named the spectra; else the round record will
    wanted: typeof arg.job === 'string' ? arg.job : null,
    index: wanted >= 0 ? wanted : Math.max(0, jobs.length - 1),
    view: 'spectrum',
    zoom: null,
    round: null, roundRev: null, roundError: '', roundPending: false, roundFailedAt: 0,
    runs: new Map(),                    // job -> {data, error, pending}
    peaks: new Map(),                   // job -> [nm, ...]
    drawn: '',
  };
  again(ctx);
}

function closeDetail(ctx) {
  detail = null;
  again(ctx);
}

function wantRound(ctx, state) {
  const d = detail;
  if (!d.roundId || d.roundPending) return;
  const live = state.round && state.round.id === d.roundId ? state.round.rev : null;     // it may still be running
  if (d.round && (live === null || live === d.roundRev)) return;
  if (d.roundError === 'missing' || Date.now() - d.roundFailedAt < RETRY_MS) return;
  d.roundPending = true;
  ctx.api.get(`/api/rounds/${d.roundId}`, {timeout: 8000}).then((record) => {
    d.round = record;
    d.roundRev = live === null ? record.rev : Math.max(live, record.rev || 0);
    d.roundError = '';
    d.roundFailedAt = 0;
    if (!d.fromRow) {
      const saved = validJobs(((record.plasma && record.plasma.jobs) || []).filter((job) => job.saved).map((job) => job.job));
      const now = d.jobs[d.index];
      d.jobs = saved.length ? saved : d.jobs;
      const keep = d.jobs.indexOf(d.wanted || now);
      d.index = keep >= 0 ? keep : Math.max(0, d.jobs.length - 1);
    }
  }, (error) => {
    d.roundError = error.status === 404 ? 'missing' : error.message || 'failed';
    d.roundFailedAt = Date.now();
  }).then(() => {
    d.roundPending = false;
    if (detail === d) again(ctx);
  });
}

function wantRun(ctx, job) {
  const d = detail;
  let entry = d.runs.get(job);
  if (!entry) { entry = {data: null, error: '', pending: false, failedAt: 0}; d.runs.set(job, entry); }
  if (entry.data || entry.pending || entry.error === 'missing' || Date.now() - entry.failedAt < RETRY_MS) return entry;
  entry.pending = true;
  ctx.api.get(`/api/runs/${job}`, {timeout: 15000}).then((data) => {
    entry.data = data;
    entry.error = '';
  }, (error) => {
    entry.error = error.status === 404 ? 'missing' : error.message || 'failed';
    entry.failedAt = Date.now();
  }).then(() => {
    entry.pending = false;
    if (detail === d) again(ctx);
  });
  return entry;
}

// The curve of the chart: the mean of the signal frames minus the background, as the program stored it.
function curveOf(data) {
  const axis = Array.isArray(data.axis) ? data.axis : [];
  if (Array.isArray(data.difference) && data.difference.length === axis.length) return {axis, values: data.difference, dark: true};
  const signal = Array.isArray(data.signal) ? data.signal : [];
  const background = Array.isArray(data.background) && data.background.length === signal.length ? data.background : null;
  return {axis, values: background ? signal.map((v, i) => v - background[i]) : signal, dark: Boolean(background)};
}

// Up to five peaks, the strongest first, no two closer than PEAK_APART_NM.
function strongest(axis, values) {
  let top = -Infinity;
  for (const v of values) if (isNumber(v) && v > top) top = v;
  if (!(top > 0)) return [];
  const found = [];
  for (let i = 1; i < values.length - 1; i += 1) {
    const v = values[i];
    if (isNumber(v) && v >= top * PEAK_FLOOR && v > values[i - 1] && v >= values[i + 1]) found.push(i);
  }
  found.sort((a, b) => values[b] - values[a]);
  const picked = [];
  for (const i of found) {
    if (picked.every((j) => Math.abs(axis[i] - axis[j]) >= PEAK_APART_NM)) picked.push(i);
    if (picked.length === PEAK_COUNT) break;
  }
  return picked.map((i) => axis[i]);
}

// ── mount ───────────────────────────────────────────────────────────────────────────────────────────────────
export function mount(root, ctx) {
  const {h} = ctx.dom;

  const listBody = h('div.tbody');
  const listEmpty = h('div.empty', {hidden: true}, h('b', null, "has no measurement record yet"), h('p', null, "After starting the schedule, each measurement and the period not measured due to timeout or suspension will be listed here."));
  const listPager = h('div');
  const listView = h('div.list-view', null,
    h('div.ph', null, h('h2', null, "execution record"), h('span.sub', null, `measured and unmeasured periods (most recent ${LOG_LIMIT} pen)`)),
    h('div.thead', null, h('span.c-time', null, "time"), h('span.c-op', null, "Operator"), h('span.c-recipe', null, 'Recipe'),
      h('span.c-slot', null, "sequence"), h('span.c-hm', null, "End"), h('span.c-status', null, "status"), h('span.c-conc', null, "Concentration/Result"),
      h('span.c-err', null, "Remarks")),
    listBody, listEmpty, listPager);

  // detail
  const title = h('h2');
  const sub = h('span.sub');
  const setView = (name) => () => { if (detail) { detail.view = name; again(ctx); } };
  const segSpectrum = h('button', {onclick: setView('spectrum')}, "spectrum");
  const segSteps = h('button', {onclick: setView('steps')}, "steps");
  const seg = h('div.seg', null, segSpectrum, segSteps);

  const facts = {};
  const fact = (key, label, stack) => {
    const value = h('b');
    facts[key] = {value, box: h('div', {class: stack ? 'stack' : ''}, h('span', null, label), value)};
    return facts[key].box;
  };
  const left = h('div.det-left', null, h('div.kv.big', null,
    fact('status', "status"), fact('kind', "Category"), fact('started', "Start"), fact('finished', "End"), fact('volts', "set voltage"),
    fact('frames', "background/signal"), fact('wait', "discharges a few seconds after pressing"), fact('peak', "highest peak"), fact('conc', "concentration", true), fact('note', "Remarks", true)));
  facts.note.box.setAttribute('role', 'button');
  facts.note.box.tabIndex = 0;
  const tellNote = () => { const text = facts.note.value.textContent; if (text) ctx.dialog.message("Remarks", text); };
  facts.note.box.addEventListener('click', tellNote);
  facts.note.box.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); tellNote(); } });

  const legend = h('div.lgd');
  const chartNote = h('div.msg', {hidden: true});
  const chart = h('div.chart', null, legend, chartNote);
  const peaks = h('div.peaks');
  const whole = h('button.btn', {onclick: () => { if (detail) { detail.zoom = null; again(ctx); } }}, "full spectrum");
  const under = h('div.det-under', null, h('span', null, "Strongest peak:"), peaks, whole);
  const turn = (step) => () => {
    if (!detail) return;
    const next = Math.min(detail.jobs.length - 1, Math.max(0, detail.index + step));
    if (next === detail.index) return;
    detail.index = next;
    detail.zoom = null;
    again(ctx);
  };
  const before = h('button.btn', {onclick: turn(-1)}, '◀');
  const after = h('button.btn', {onclick: turn(1)}, '▶');
  const which = h('b');
  const fnav = h('div.fnav', {hidden: true}, before, which, after);
  const csv = h('a.btn', {href: '#'}, "Download CSV (Excel can be opened)");
  const foot = h('div.det-foot', null, fnav, h('span.sp'), csv);
  const noSpectrum = h('div.empty', {hidden: true}, h('p'));
  const right = h('div.det-right', null, chart, under, foot, noSpectrum);
  const spectrumView = h('div.det-body', null, left, right);

  table = stepTable(ctx);
  const stepsPager = h('div');
  const stepsEmpty = h('div.empty', {hidden: true}, h('p'));
  const stepsBox = h('div.det-steps', null, table.thead, table.tbody, stepsPager);
  const stepsView = h('div.det-steps', {hidden: true}, stepsBox, stepsEmpty);

  const detailView = h('div.detail-view', {hidden: true},
    h('div.ph', null, h('button.btn', {onclick: () => closeDetail(ctx)}, "◀ Return to list"), title, sub, h('span.sp'), seg),
    spectrumView, stepsView);

  el = {listView, listBody, listEmpty, listPager, detailView, title, sub, seg, segSpectrum, segSteps, facts, legend, chart, chartNote, peaks, whole, under,
    foot, fnav, before, after, which, csv, noSpectrum, spectrumView, stepsView, stepsBox, stepsEmpty, stepsPager};
  root.replaceChildren(h('div.panel.table', null, listView, detailView));
}

// arg = {round_id, job} when another page asks for one round; without it the page opens on the list.
export function show(ctx, arg) {
  shown = true;
  if (arg && typeof arg === 'object' && (arg.round_id || arg.job)) openDetail(ctx, arg);
  else if (!arg) detail = null;
}

export function hide() { shown = false; }

// ── detail: render ──────────────────────────────────────────────────────────────────────────────────────────
function setFact(ctx, key, text) {
  const item = el.facts[key];
  const value = text === null || text === undefined ? '' : String(text);
  setHidden(item.box, value === '');
  ctx.dom.setText(item.value, value);
  setTitle(item.value, value);
}

function concentration(summary) {
  const conc = (summary && summary.conc) || {};
  const parts = METALS.filter((metal) => isNumber(conc[metal])).map((metal) => `${metal} ${sig3(conc[metal])}`);
  return parts.length ? `${parts.join(' · ')} mg/L` : '';
}

function renderDetail(state, ctx) {
  const {dom, labels} = ctx;
  const d = detail;
  wantRound(ctx, state);
  const round = d.round;
  const row = d.row;
  const job = d.jobs[d.index] || null;
  const run = job ? wantRun(ctx, job) : null;
  const data = run && run.data;
  const legacy = Boolean(row && row.kind === 'legacy');
  const calibration = (round && round.mode === 'calibration') || (row && row.mode === 'calibration');

  // header
  const name = (round && round.recipe && round.recipe.name) || (row && row.recipe_name) || (data && data.recipe_name) || '';
  const slot = (round && round.slot) || (row && row.slot) || null;
  const planned = (round && round.slots_planned) || (row && row.slots_planned) || null;
  const head = calibration ? `Correction: ${name}` : slot ? `No. ${slotText(slot, planned)} · ${name}
There is no step record for the measurement of` : name || (job || '');
  dom.setText(el.title, head);
  setTitle(el.title, head);
  const began = (round && round.started_at) || (row && row.time) || (data && data.started_at) || null;
  const operator = (round && round.operator) || (row && row.operator) || (data && data.operator) || '';
  dom.setText(el.sub, [began ? labels.fmtTime(began, 'YYYY-MM-DD HH:MM') : '', operator].filter(Boolean).join(' · '));
  const hasSteps = Boolean(d.roundId) || legacy;
  if (!hasSteps) d.view = 'spectrum';                       // a spectrum that belongs to no round has nothing else to show
  setHidden(el.seg, !hasSteps);
  dom.setClass(el.segSpectrum, 'sel', d.view === 'spectrum');
  dom.setClass(el.segSteps, 'sel', d.view === 'steps');
  setHidden(el.spectrumView, d.view !== 'spectrum');
  setHidden(el.stepsView, d.view !== 'steps');

  if (d.view === 'steps') {
    let why = '';
    if (legacy || !d.roundId) why = "1.3.0";
    else if (!round) why = d.roundError ? (d.roundError === 'missing' ? "The record for this round is no longer there" : `Record reading failed: ${d.roundError}`) : "Reading...";
    setHidden(el.stepsBox, Boolean(why));
    setHidden(el.stepsEmpty, !why);
    dom.setText(el.stepsEmpty.firstChild, why);
    if (!why) {
      const steps = round.steps || [];
      const pager = dom.pager(el.stepsPager, steps.length, STEP_ROWS, () => again(ctx));
      table.update(pager.slice(steps), steps.length);
    }
    return;
  }

  //spectrum: the facts of this discharge, from the round record first, else from the stored spectrum
  const entry = round ? ((round.plasma && round.plasma.jobs) || []).find((item) => item.job === job) || null : null;
  const from = (key) => (entry && entry[key] !== undefined && entry[key] !== null ? entry[key] : data && data[key] !== undefined ? data[key] : null);
  const status = round ? round.status : row ? row.status : data ? 'completed' : '';
  setFact(ctx, 'status', status ? labels.round[status] || status : '');
  const kind = (round && round.sample_kind) || (row && row.sample_kind) || '';
  setFact(ctx, 'kind', calibration ? "standard product" : kind === 'blank' ? "blank" : kind === 'sample' ? "sample" : '');
  const startedAt = (round && round.started_at) || (data && data.started_at) || (row && row.time) || null;
  const finishedAt = (round && round.finished_at) || (data && data.finished_at) || (row && row.finished_at) || null;
  setFact(ctx, 'started', startedAt ? labels.fmtTime(startedAt, 'HH:MM:SS') : '');
  setFact(ctx, 'finished', finishedAt ? labels.fmtTime(finishedAt, 'HH:MM:SS') : '');
  const volts = from('voltage_v');
  setFact(ctx, 'volts', isNumber(volts) ? `${trim(volts, 1)} V` : '');
  const dark = from('background_frames');
  const signal = from('signal_frames');
  setFact(ctx, 'frames', isNumber(dark) || isNumber(signal) ? `${isNumber(dark) ? dark : '—'} sheets/${isNumber(signal) ? signal : '—'} sheets` : '');
  const wait = entry && isNumber(entry.wait_s) ? entry.wait_s : data && data.timing && isNumber(data.timing.wait_s) ? data.timing.wait_s : null;
  setFact(ctx, 'wait', wait === null ? '' : `${trim(wait, 1)} seconds`);
  const curve = data ? curveOf(data) : null;
  let peakNm = entry && isNumber(entry.peak_nm) ? entry.peak_nm : null;
  if (curve && !d.peaks.has(job)) d.peaks.set(job, strongest(curve.axis, curve.values));
  const chips = (job && d.peaks.get(job)) || [];
  if (peakNm === null && chips.length) peakNm = chips[0];
  setFact(ctx, 'peak', peakNm === null ? '' : `${peakNm.toFixed(1)} nm`);
  const measured = (round && round.measurements) || (row && row.measurements) || [];
  const summary = measured.length ? measured[Math.min(d.index, measured.length - 1)] : null;
  setFact(ctx, 'conc', concentration(summary));
  setFact(ctx, 'note', (round && round.error) || (row && row.note) || '');

  // the chart
  const none = !job;
  setHidden(el.chart, none);
  setHidden(el.under, none || !curve);
  setHidden(el.foot, none);
  setHidden(el.noSpectrum, !none);
  if (none) {
    dom.setText(el.noSpectrum.firstChild, d.roundId && !round && !d.roundError ? "Reading..." : NO_SPECTRUM);
    d.drawn = '';
    return;
  }
  const many = d.jobs.length > 1;
  setHidden(el.fnav, !many);
  dom.setText(el.which, `${d.index + 1} / ${d.jobs.length} discharge`);
  if (el.before.disabled !== (d.index <= 0)) el.before.disabled = d.index <= 0;
  if (el.after.disabled !== (d.index >= d.jobs.length - 1)) el.after.disabled = d.index >= d.jobs.length - 1;
  const href = `/api/runs/${job}.csv`;
  if (el.csv.getAttribute('href') !== href) {
    el.csv.setAttribute('href', href);
    el.csv.setAttribute('download', `${job}.csv`);
  }

  dom.list(el.peaks, chips, (nm, index) => index, () => {
    const chip = dom.h('button.btn');
    chip.addEventListener('click', () => {
      if (!detail || !chipFacts.has(chip)) return;
      detail.zoom = chipFacts.get(chip);
      again(ctx);
    });
    return chip;
  }, (chip, nm) => {
    chipFacts.set(chip, nm);
    dom.setText(chip, nm.toFixed(1));
    dom.setClass(chip, 'sel', d.zoom === nm);
  });
  dom.setClass(el.whole, 'sel', d.zoom === null);
  setHidden(el.legend, !curve);
  if (curve) {
    const frames = isNumber(signal) ? signal : '—';
    dom.setText(el.legend, curve.dark ? `black: average ${frames} (background)` : `black: average ${frames} sheets`);
  }
  // nothing to draw yet: one line of text where the plot will be
  setHidden(el.chartNote, Boolean(curve));
  if (!curve) dom.setText(el.chartNote, run.error ? (run.error === 'missing' ? "This spectrum is no longer there" : `Spectrum reading failed: ${run.error}`) : "Reading...");
  // drawn again only when the curve or the range changed (not at every poll)
  const key = [job, curve ? 'curve' : '', d.zoom === null ? '' : d.zoom].join('|');
  if (key === d.drawn) return;
  d.drawn = key;
  if (curve) {
    ctx.chart.drawSpectrum(el.chart, {axis: curve.axis, series: [{values: curve.values, cls: 'sl-mean'}],
      range: d.zoom === null ? null : [d.zoom - ZOOM_NM, d.zoom + ZOOM_NM]});
  } else {
    ctx.chart.drawSpectrum(el.chart, {axis: [], series: []});       // takes away the plot of the discharge shown before
  }
}

export function render(state, ctx) {
  if (!el) return;
  setHidden(el.listView, Boolean(detail));
  setHidden(el.detailView, !detail);
  if (detail) renderDetail(state, ctx);
  else renderList(state, ctx);
}
