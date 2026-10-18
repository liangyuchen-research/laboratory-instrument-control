//dash.js — Overview (SPEC E.4, AMENDMENTS M5.4). Read-only: nothing on this page sends a command.
//
//D1 three metal cards (the newest sample's concentration) and the tile "Latest measurement" (opens it in execution log)
//D2 process phases: the phases of the round that runs, of the last one, or of the chosen Recipe
//D3 This round of steps (live, or the round that just ended) / Recipe steps (idle) / Before starting to use (set-up not complete)
//   D4  seven status cells (a tap shows the whole value)        D5  the safety line
//
//Also exported for execution record: stepTable() — the step table of D3 is the table of its "step" view (SPEC E.7).

import { checkNow } from '../check.js';

const ACTIVE = ['waiting', 'running', 'pausing', 'paused', 'stopping'];
const ROUND_ACTIVE = ['running', 'pausing', 'paused', 'stopping'];
const METALS = [['Cu', "Copper Cu"], ['Zn', "Zinc Zn"], ['Ni', "Nickel Ni"]];
const STEP_ROWS = 6;              // steps per page in D3
const PHASE_BOXES = 10;           // phase boxes in D2; a longer Recipe shows the ten around the current phase
const FOLLOW_AFTER_MS = 30000;    // the step table follows the current step again this long after the person turned a page
const RETRY_MS = 5000;
const ID = /^[0-9a-f]{32}$/;
const QUALITY = ['q-ok', 'q-na', 'q-bad', 'q-run', 'q-cal'];
const STEP_CLASS = {running: 'running', done: 'completed', failed: 'failed', stopped: 'stopped', skipped: 'notrun'};
const SAFETY = "The screen display OFF does not mean that the electrode is out of power. Before servicing or wiring, disconnect the high-voltage power supply from the 24 V drive power supply using a physical switch.";

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
const signed = (value, places) => (value < 0 ? '' : '+') + value.toFixed(places);
const sig3 = (value) => String(Number(Number(value).toPrecision(3)));      // 3 significant digits: 0.512 · 12.3 · 0.3
const trim = (value, places) => String(Number(Number(value).toFixed(places)));

//── the step table (D3 here, the "step" view in execution record) ──────────────────────────────────────────────
//The result column. A step of state.round.steps_window brings the sentence (result_text); a step of
// /api/rounds/<id> brings the raw result, and the same sentence is made here (the program's rule: step_result_text).
export function stepResult(step) {
  if (typeof step.result_text === 'string') return step.result_text;
  if (step.status === 'failed') return String(step.error || '');
  const result = step.result || {};
  if (step.action === 'dose_weight' && isNumber(result.dispensed_g)) {
    return `${result.dispensed_g.toFixed(2)} g (error ${signed(Number(result.error_g) || 0, 2)} g)`;
  }
  if (step.action === 'scale_read' && isNumber(result.grams)) return `${result.grams.toFixed(3)} g`;
  if ((step.action === 'plasma_run' || step.action === 'spectrum_capture') && isNumber(result.peak_nm)) {
    return `highest peak ${result.peak_nm.toFixed(1)} nm`;
  }
  return '';
}

// stepTable(ctx, {slim}) -> {thead, tbody, update(steps, total)}
//   steps   the rows of one page: {n, text, status, started_at, finished_at, result_text | result + action, error};
//           {n, loading: true} stands for a step that has not arrived yet
//   total   steps of the whole round (for the title of the dialog a tap on a row opens)
export function stepTable(ctx, options = {}) {
  const {dom, labels, dialog} = ctx;
  const {h, setText, setClass} = dom;
  const facts = new WeakMap();
  let count = 0;
  const thead = h('div.thead', null, h('span.s-no', null, '#'), h('span.s-text', null, "Action"), h('span.s-st', null, "status"),
    h('span.s-res', null, "results"), h('span.s-time', null, "time"));
  const tbody = h('div.tbody');

  const timeOf = (step) => labels.fmtTime(step.finished_at || (step.status === 'running' ? step.started_at : null), 'HH:MM:SS');

  function open(step) {
    if (!step || step.loading) return;
    const status = step.status || 'pending';
    const where = count ? `${step.n} / ${count}` : String(step.n);
    if (status === 'failed') {
      dialog.message("step failed", step.error || stepResult(step) || '—', {level: 'error', lines: [["steps", where], ["Action", step.text || '']]});
      return;
    }
    const result = stepResult(step);
    const time = timeOf(step);
    dialog.message(`Step ${where}`, step.text || '', {lines: [["status", labels.step[status] || status],
      result ? ["results", result] : null, time !== '—' ? ["time", time] : null]});
  }

  function make() {
    const row = h('button', {class: options.slim ? 'trow slim' : 'trow'},
      h('span.s-no'), h('span.s-text'), h('span.s-st', null, h('span')), h('span.s-res'), h('span.s-time'));
    row.addEventListener('click', () => open(facts.get(row)));
    return row;
  }

  function update(row, step) {
    facts.set(row, step);
    const [no, text, state, result, time] = row.children;
    const status = step.status || 'pending';
    const cls = STEP_CLASS[status] || '';
    const pill = state.firstChild;
    setText(no, step.n);
    setText(text, step.loading ? '…' : step.text || '');
    setText(pill, step.loading ? '' : labels.step[status] || status);
    const wanted = cls ? `st ${cls}` : '';
    if (pill.className !== wanted) pill.className = wanted;
    setText(result, step.loading ? '' : stepResult(step));
    setText(time, step.loading ? '' : timeOf(step));
    setClass(row, 'cur', status === 'running');
    setClass(row, 'wait', Boolean(step.loading));
  }

  return {
    thead,
    tbody,
    update(steps, total) {
      count = Number(total) || 0;
      dom.list(tbody, steps, (step) => step.n, make, update);
    },
  };
}

// ── this page ───────────────────────────────────────────────────────────────────────────────────────────────
let el = null;
let table = null;
let shown = false;
let subjectKey = '';
const cellFacts = [];
// What D2 and D3 need besides /api/state: the chosen Recipe (its steps and phases) and, for the pages of the step
// table that state.round.steps_window does not hold, the whole round record.
const recipeCache = {key: '', id: '', data: null, missing: false, pending: false, failedAt: 0};
const roundCache = {key: '', id: '', data: null, missing: false, pending: false, failedAt: 0};

function again(ctx) {
  if (shown && ctx.store.state) render(ctx.store.state, ctx);
}

// One GET per key (id + the revision the state names); a failed one is tried again after a few seconds.
function want(ctx, cache, id, rev, path) {
  if (!ID.test(id)) return;
  const key = `${id}@${rev}`;
  if (cache.pending) return;
  if (cache.key === key && (cache.data || cache.missing) && !cache.failedAt) return;
  if (cache.key === key && Date.now() - cache.failedAt < RETRY_MS) return;
  if (cache.id !== id) { cache.data = null; cache.missing = false; }
  cache.key = key;
  cache.id = id;
  cache.pending = true;
  ctx.api.get(path, {timeout: 8000}).then((data) => {
    cache.data = data;
    cache.missing = false;
    cache.failedAt = 0;
  }, (error) => {
    cache.missing = error.status === 404;
    if (cache.missing) cache.data = null;
    cache.failedAt = cache.missing ? 0 : Date.now();
  }).then(() => {
    cache.pending = false;
    again(ctx);
  });
}

function setQ(dom, node, cls) {
  for (const name of QUALITY) dom.setClass(node, name, name === cls);
}

function setTitle(node, text) {
  const value = text || '';
  if ((node.getAttribute('title') || '') === value) return;
  if (value) node.setAttribute('title', value);
  else node.removeAttribute('title');
}

function setHidden(node, hidden) {
  if (node.hidden !== hidden) node.hidden = hidden;
}

// A finger has no tooltip: what a mouse reads from `title`, a tap opens.
function tappable(node, action) {
  node.setAttribute('role', 'button');
  node.tabIndex = 0;
  node.addEventListener('click', action);
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); action(); }
  });
}

// A one-line text of a panel header that may be cut (an error, a long Recipe name): while it is cut it is underlined,
// and a tap opens all of it.
function cutLine(ctx, node, title) {
  node.addEventListener('click', () => {
    if (node.classList.contains('tap')) ctx.dialog.message(typeof title === 'function' ? title() : title, node.textContent);
  });
}

function markCut(dom, node) {
  dom.setClass(node, 'tap', node.scrollWidth > node.clientWidth + 1);
}

// A sub-line of D1, “a · b · c”: a short part (a time, “100 V”) is never broken in two; the line turns between parts.
function setParts(dom, node, parts) {
  const text = parts.join(' · ');
  if (node.textContent === text) return;
  const nodes = [];
  parts.forEach((part, index) => {
    if (index) nodes.push(' · ');
    nodes.push(part.length <= 14 ? dom.h('span.nb', null, part) : part);
  });
  node.replaceChildren(...nodes);
}

export function mount(root, ctx) {
  const {h} = ctx.dom;

  // D1
  const metals = METALS.map(([key, name]) => {
    const tag = h('span.tag');
    const value = h('b');
    const sub = h('span');
    const val = h('div.metal-val', null, value, sub);
    const box = h('div.panel.metal', null, h('div.metal-head', null, h('span.metal-name', null, name), tag), val);
    const item = {key, name, box, tag, val, value, sub, full: ''};
    tappable(box, () => { if (item.full) ctx.dialog.message(name, item.full); });     // the sub-line shows three lines at most
    return item;
  });
  const last = {tag: h('span.tag.q-cal', {hidden: true}, "Complete"), value: h('b'), sub: h('span')};
  last.val = h('div.metal-val', null, last.value, last.sub);
  last.box = h('button.panel.metal', {disabled: true}, h('div.metal-head', null, h('span.metal-name', null, "latest measurement"), last.tag), last.val);
  last.box.addEventListener('click', () => {
    const run = ctx.store.state && ctx.store.state.latest_run;
    if (run) ctx.go('runs', {round_id: run.round_id || null, job: run.job || null});
  });

  // D2
  const phSub = h('span.sub');
  const phMeta = h('span.meta');
  const phRow = h('div.steps-row');
  const phEmpty = h('div.steps-empty', {hidden: true}, "has not selected a process yet: please go to \"Scheduling Measurement\" and click \"Process\"");

  // D3
  table = stepTable(ctx, {slim: true});
  const d3Title = h('h2');
  const d3Sub = h('span.sub');
  const pager = h('div');
  const stepsBox = h('div.d3-steps', null, table.thead, table.tbody, pager);
  const lines = [1, 2, 3, 4].map((n) => {
    const title = h('b');
    const sub = h('small');
    const tag = h('span.tag');
    return {title, sub, tag, box: h('div.setup-line', null, h('span.no', null, n), h('div.txt', null, title, sub), tag)};
  });
  const setup = h('div.setup', {hidden: true}, lines.map((line) => line.box));
  const empty = h('div.empty', {hidden: true}, h('b', null, "There is no process yet"), h('p', null, "Go to \"Scheduling Measurement\" and click \"Process\" to select it, then click \"Start Scheduling\" at the bottom."));

  // D4
  const cells = ["RPi wiring", "HSPY high voltage power supply", "Nano controller", "pump", "syringe pump", "Weight", "active alarm"].map((label, index) => {
    const value = h('b');
    const box = h('button.cell', null, h('small', null, label), value);
    box.addEventListener('click', async () => {
      const fact = cellFacts[index];
      if (!fact) return;
      // the supply and the Nano: the dialog also offers a reading taken now (AMENDMENTS M18)
      const checkable = index === 1 || index === 2;
      const picked = await ctx.dialog.message(label, fact.detail ? `${fact.value}\n${fact.detail}` : fact.value,
        checkable ? {actions: [{label: "Recheck", value: 'check'}]} : {});
      if (picked === 'check') checkNow(ctx.api, ctx.dialog);
    });
    return {label, value, box};
  });

  cutLine(ctx, phSub, "process stage");
  cutLine(ctx, d3Sub, () => d3Title.textContent);
  el = {metals, last, phSub, phMeta, phRow, phEmpty, d3Title, d3Sub, pager, stepsBox, lines, setup, empty, cells};
  root.replaceChildren(
    h('div.metals', null, metals.map((item) => item.box), last.box),
    h('div.panel.steps', null, h('div.ph', null, h('h2', null, "process stage"), phSub, h('span.sp'), phMeta), phRow, phEmpty),
    h('div.panel.table.d3', null, h('div.ph', null, d3Title, d3Sub), stepsBox, setup, empty),
    h('div.panel.strip', null, cells.map((cell) => cell.box)),
    h('div.dash-note', null, SAFETY),
  );
}

export function show() { shown = true; }
export function hide() { shown = false; }

export function render(state, ctx) {
  if (!el) return;
  const s = subjectOf(state, ctx.store.form);
  if (s.chosen && !s.running) want(ctx, recipeCache, s.chosen, (state.revs && state.revs.recipes) || 0, `/api/recipes/${s.chosen}`);
  renderMetals(state, ctx);
  renderPhases(state, ctx, s);
  renderSteps(state, ctx, s);
  renderStrip(state, ctx);
}

// Which round or Recipe D2 and D3 are about.
function subjectOf(state, form) {
  const sched = state.schedule || null;
  const active = Boolean(sched && ACTIVE.includes(sched.status));
  const round = state.round || null;
  const running = Boolean(round && ROUND_ACTIVE.includes(round.status));
  const named = active ? (sched.recipe && sched.recipe.id) || '' : form.recipe_id || '';
  const chosen = ID.test(named) ? named : '';
  // The round that ended stays on screen as long as nothing else is chosen: it belongs to the schedule that is
  //still active, or (idle) no other Recipe has been chosen on scheduled measurement since.
  const roundRecipe = round && round.recipe ? round.recipe.id : '';
  const ended = Boolean(round) && !running && (active ? round.schedule_id === sched.id : !chosen || chosen === roundRecipe);
  const recipe = chosen && recipeCache.id === chosen && recipeCache.data ? recipeCache.data : null;
  return {sched, active, round, running, chosen, ended, recipe, calibration: Boolean(active && sched.kind === 'calibration')};
}

// ── D1 ──────────────────────────────────────────────────────────────────────────────────────────────────────
function renderMetals(state, ctx) {
  const {dom, labels} = ctx;
  const sample = (state.measurement && state.measurement.latest_sample) || null;
  const applied = state.calibration_active || null;
  for (const item of el.metals) {
    const calibrated = Boolean(applied && (applied.metals || []).includes(item.key));
    const metal = (sample && sample.metals && sample.metals[item.key]) || {};
    const has = isNumber(metal.conc);
    let sub;
    if (has && isNumber(metal.zero)) sub = ['mg/L', labels.fmtTime(sample.time), `Blank zero point deducted ${sig3(metal.zero)}`];
    else if (has) sub = ['mg/L', labels.fmtTime(sample.time)];
    else if (sample && sample.conc_reason) sub = ['mg/L', String(sample.conc_reason)];
    else if (calibrated) sub = ['mg/L', `has applied ${labels.fmtTime(applied.applied_at, 'MM-DD')} calibration and is waiting for the next sample measurement`];
    else sub = ['mg/L', "has not been calibrated by the standard and does not display the concentration."];
    dom.setText(item.tag, calibrated ? "corrected" : "uncorrected");
    setQ(dom, item.tag, calibrated ? 'q-cal' : 'q-na');
    dom.setText(item.value, has ? sig3(metal.conc) : '—');
    dom.setClass(item.val, 'has', has);
    setParts(dom, item.sub, sub);
    setTitle(item.box, `${item.name}: ${has ? sig3(metal.conc) : '—'} ${sub.join(' · ')}`);
    item.full = [`${has ? sig3(metal.conc) : '—'} ${sub[0]}`, ...sub.slice(1), calibrated ? "corrected" : "uncorrected"].join('\n');
  }
  const run = state.latest_run || null;
  const peak = run && isNumber(run.peak_nm);
  dom.setText(el.last.value, peak ? `${run.peak_nm.toFixed(1)} nm` : '—');
  dom.setClass(el.last.val, 'has', Boolean(peak));
  let sub = ["has not been measured yet"];
  if (run) {
    sub = [labels.fmtTime(run.finished_at || run.saved_at || run.started_at),
      run.kind === 'plasma' && isNumber(run.voltage_v) ? `${run.voltage_v} V` : null,
      isNumber(run.signal_frames) ? `signal ${run.signal_frames}` : null].filter(Boolean);
  }
  setParts(dom, el.last.sub, sub);
  setHidden(el.last.tag, !run);
  setTitle(el.last.box, peak ? `Last measurement: highest peak ${run.peak_nm.toFixed(1)} nm` : '');
  if (el.last.box.disabled !== !run) el.last.box.disabled = !run;
}

// ── D2 ──────────────────────────────────────────────────────────────────────────────────────────────────────
// How the phase boxes of a round that is over are marked: all done, or done up to the phase where it ended.
function endedMark(round) {
  if (round.status === 'completed') return () => 'done';
  const at = round.phase || 0;
  return (index) => (index < at ? 'done' : index === at ? 'fail' : '');
}

function renderPhases(state, ctx, s) {
  const {dom, labels} = ctx;
  const round = s.round;
  let sub = '';
  let meta = '';
  let phases = null;
  let mark = () => '';
  let current = 0;
  if (s.running) {
    const paused = round.status === 'paused';
    const calibration = round.mode === 'calibration';
    sub = (paused ? "has been suspended:" : "is executing:") + (calibration ? "Correction:" : '') + ((round.recipe && round.recipe.name) || '');
    const steps = `Steps ${round.step || 0} / ${round.step_count || 0}`;
    if (calibration) {
      const run = state.calibration_run || {};
      meta = run.runs ? `Round ${run.run || '—'} / ${run.runs} · ${steps}` : steps;
    } else {
      meta = (round.slots_planned ? `schedule ${round.slot} / ${round.slots_planned}` : `schedule ${round.slot}`) + ` · ${steps}`;
    }
    phases = round.phases || [];
    current = round.phase || 0;
    mark = (index) => (index < current ? 'done' : index === current ? (paused ? 'pause' : 'cur') : '');
  } else if (s.active) {
    const name = (s.calibration ? "Correction:" : '') + ((s.sched.recipe && s.sched.recipe.name) || '');
    const word = labels.schedule[s.sched.status] || s.sched.status;
    meta = `schedule ${word}` + (s.sched.next_slot_at ? `· Next time ${labels.fmtTime(s.sched.next_slot_at, 'HH:MM')}` : '');
    if (s.ended) {
      // the Recipe that round ran (a blank slot runs the blank Recipe)
      const ran = (s.calibration ? "Correction:" : '') + ((round.recipe && round.recipe.name) || '');
      sub = `Last executed: ${labels.round[round.status] || round.status} · ${ran}`;
      phases = round.phases || [];
      current = round.phase || 0;
      mark = endedMark(round);
    } else {
      sub = `Selected: ${name}`;
      phases = s.recipe ? s.recipe.phases || [] : [];
    }
  } else if (s.chosen && s.recipe) {
    sub = `Selected: ${s.recipe.name || ''}`;
    meta = `total ${s.recipe.step_count || 0} steps · ${s.recipe.rounds || 1} rounds`;
    phases = s.recipe.phases || [];
    // the last round ran this very Recipe (not a calibration, whose steps are its own): its boxes show how it ended
    if (s.ended && round.mode !== 'calibration' && (round.phases || []).length === phases.length) {
      current = round.phase || 0;
      mark = endedMark(round);
    }
  } else if (s.chosen && !recipeCache.missing) {
    phases = [];                                    //the Recipe is on its way: no box yet, and not “Not selected yet” either
  }
  dom.setText(el.phSub, sub);
  setTitle(el.phSub, sub);
  markCut(dom, el.phSub);
  dom.setText(el.phMeta, meta);
  setHidden(el.phEmpty, phases !== null);
  setHidden(el.phRow, phases === null);
  const all = (phases || []).map((phase, index) => ({index, name: phase.name || '', cls: mark(index)}));
  let from = 0;
  if (all.length > PHASE_BOXES) from = Math.min(Math.max(0, current - Math.floor(PHASE_BOXES / 2)), all.length - PHASE_BOXES);
  dom.list(el.phRow, all.slice(from, from + PHASE_BOXES), (item) => item.index,
    () => dom.h('div.step', null, dom.h('span.no'), dom.h('span.nm')),
    (box, item) => {
      dom.setText(box.firstChild, item.index + 1);
      dom.setText(box.lastChild, item.name);
      setTitle(box, item.name);
      for (const name of ['done', 'cur', 'pause', 'fail']) dom.setClass(box, name, name === item.cls);
    });
}

// ── D3 ──────────────────────────────────────────────────────────────────────────────────────────────────────
// The rows of one page of a round: the window of /api/state first (it is fresh), the stored record for the rest.
function roundRows(ctx, round, page) {
  const total = round.step_count || 0;
  const from = page * STEP_ROWS + 1;
  const to = Math.min(total, from + STEP_ROWS - 1);
  const items = (round.steps_window && round.steps_window.items) || [];
  const byNumber = new Map();
  if (roundCache.id === round.id && roundCache.data) for (const step of roundCache.data.steps || []) byNumber.set(step.n, step);
  for (const item of items) byNumber.set(item.n, item);
  const inWindow = new Set(items.map((item) => item.n));
  const rows = [];
  let outside = false;
  for (let n = from; n <= to; n += 1) {
    if (!inWindow.has(n)) outside = true;
    rows.push(byNumber.get(n) || {n, loading: true});
  }
  if (outside) want(ctx, roundCache, round.id, round.rev, `/api/rounds/${round.id}`);
  return rows;
}

function renderSteps(state, ctx, s) {
  const {dom, labels} = ctx;
  const round = s.round;
  let kind = 'none';
  if (s.running) kind = 'live';
  else if (!(state.setup && state.setup.complete)) kind = 'setup';
  else if (s.ended) kind = 'ended';
  else if (s.chosen && !recipeCache.missing) kind = 'recipe';

  let title = "Steps in this round";
  let sub = '';
  if (kind === 'live') {
    sub = (round.prompt && round.prompt.text) || (round.current && round.current.detail) || '';
  } else if (kind === 'ended') {
    sub = `${labels.fmtTime(round.finished_at, 'HH:MM')} ${labels.round[round.status] || round.status}` + (round.error ? `: ${round.error}` : '');
  } else if (kind === 'recipe') {
    title = "Recipe Steps";
    sub = "will be executed in this order after the schedule starts.";
  } else if (kind === 'setup') {
    title = "Before you start using";
    sub = "After completing these four things in order, you can start measuring";
  }
  dom.setText(el.d3Title, title);
  dom.setText(el.d3Sub, sub);
  setTitle(el.d3Sub, sub);
  markCut(dom, el.d3Sub);
  const steps = kind === 'live' || kind === 'ended' || kind === 'recipe';
  setHidden(el.stepsBox, !steps);
  setHidden(el.setup, kind !== 'setup');
  setHidden(el.empty, kind !== 'none');
  if (kind === 'setup') renderSetup(state, ctx);
  if (!steps) { subjectKey = kind; return; }

  const ofRound = kind !== 'recipe';
  const total = ofRound ? round.step_count || 0 : s.recipe ? (s.recipe.steps || []).length : 0;
  const pager = dom.pager(el.pager, total, STEP_ROWS, () => again(ctx));
  const key = ofRound ? `round:${round.id}` : `recipe:${s.chosen}`;
  const home = ofRound && (kind === 'live' || round.status !== 'completed') ? Math.floor((Math.max(round.step || 1, 1) - 1) / STEP_ROWS) : 0;
  if (key !== subjectKey) {
    subjectKey = key;
    pager.set(home);                                 // another round or Recipe: start where it is (or at its first step)
  } else if (kind === 'live' && Date.now() - pager.touchedAt > FOLLOW_AFTER_MS) {
    pager.set(home);                                 // the page that holds the current step, unless the person just turned a page
  }
  const rows = ofRound ? roundRows(ctx, round, pager.page) : pager.slice(s.recipe ? s.recipe.steps || [] : []);
  table.update(rows, total);
}

function renderSetup(state, ctx) {
  const {dom, labels} = ctx;
  const stale = ctx.store.stale;
  const setup = state.setup || {};
  const rpi = state.rpi || {};
  const demo = Boolean(state.demo);
  const fill = (line, title, sub, tag, cls) => {
    dom.setText(line.title, title);
    dom.setText(line.sub, sub);
    setTitle(line.sub, sub);
    dom.setText(line.tag, tag);
    setQ(dom, line.tag, cls);
  };
  fill(el.lines[0], "Set Maintenance PIN\nThe maintenance PIN of",
    demo ? "practice mode is fixed at 000000" : "Double-click Set-PIN.cmd in the folder and enter 6–8 digits. No need to restart the program after setting.",
    setup.pin ? "has been set" : "Not set", setup.pin ? 'q-ok' : 'q-na');
  if (stale) fill(el.lines[1], "connected to RPi (plasma)", "The program did not respond", "? Unknown", 'q-bad');
  else {
    fill(el.lines[1], "connected to RPi (plasma)", rpi.connected ? `is connected to ${rpi.host || ''}` : rpi.error || "is not connected to the RPi yet",
      rpi.connected ? "is connected" : "not connected", rpi.connected ? 'q-ok' : 'q-na');
  }
  const board = {ok: ["is connected", 'q-ok'], off: ["Not used", 'q-ok'], unset: ["not connected", 'q-na']}[setup.board] || ["? Not connected", 'q-bad'];
  //Nobody chooses a port: the line says what the program found ("Connected COMx") or what it waits for.
  const found = setup.board === 'off' ? "is set to not used. When you want to use it, go to \"Maintenance › Connection\" to change it back.\nConnect the"
    : (!stale && (state.pump || {}).text) || "USB cable to this computer, and the program will find it by itself, without selecting a port.";
  fill(el.lines[2], "Pump Control Board (Arduino Mega)", found, stale ? "? Unknown" : board[0], stale ? 'q-bad' : board[1]);
  const ready = setup.operators > 0 && setup.recipes > 0;
  fill(el.lines[3], "Operator and Recipe",
    `${setup.operators || 0} operators · ${setup.recipes || 0} Recipes. The "Operator" in the list can be replaced with the real name by going to "Maintenance › Operator".`,
    ready ? "can start" : "Not completed", ready ? 'q-ok' : 'q-na');
}

// ── D4 ──────────────────────────────────────────────────────────────────────────────────────────────────────
function pumpActivity(pump, labels) {
  if (pump.cleaning) return {text: "Cleaning", run: true};
  const busy = (pump.motors || []).filter((motor) => motor.state && motor.state !== 'off');
  if (!busy.length) return {text: "Stop all", run: false};
  const dosing = busy.every((motor) => motor.state === 'move' || motor.state === 'weigh');
  return {text: `${busy.map((motor) => labels.motorShort(motor.id)).join('＋')} ${dosing ? "Quantification in progress" : "is running"}`, run: true};
}

function stripValues(state, ctx) {
  const {labels} = ctx;
  const unknown = {value: "? Unknown", cls: 'q-bad', detail: "The program did not respond"};
  const none = {value: '—', cls: 'q-na', detail: ''};
  const stale = ctx.store.stale;
  const rpi = state.rpi || {};
  const snapshot = (rpi.status && rpi.status.snapshot) || {};
  const pump = state.pump || {};
  const linked = pump.link === 'connected';
  const out = [];

  //RPi wiring
  if (stale) out.push(unknown);
  else if (!rpi.connected) out.push({value: "not connected", cls: 'q-na', detail: rpi.error || ''});
  else {
    out.push({value: isNumber(rpi.status_age_s) ? `connected · ${Math.round(rpi.status_age_s)} seconds ago` : "is connected", cls: 'q-ok',
      detail: rpi.host ? `${rpi.host}:${rpi.port}` : ''});
  }

  //HSPY high voltage power supply and Nano controller. While a discharge runs the RPi does not read the two devices back (its snapshot is
  // the one from before the job): the cells then say what the job does, never the old “OFF”. A read-back that both
  // devices gave but that the program no longer trusts (rpi.hv: too old) is not shown as a value either.
  const power = snapshot.power || {};
  const nano = snapshot.nano || {};
  const job = (rpi.status && rpi.status.job) || null;
  const discharging = Boolean(job && job.state === 'running' && job.high_voltage);
  const hv = rpi.hv || {};
  const old = hv.known === false && power.valid === true && nano.valid === true;
  const why = (hv.reasons || []).filter(Boolean).join('; ');
  if (stale) out.push(unknown);
  else if (!rpi.connected) out.push(none);
  else if (discharging) out.push({value: `ON · ${isNumber(job.voltage_v) ? Math.round(job.voltage_v) : '—'} V`, cls: 'q-run', detail: "is discharging, and the reading value will be updated after it is completed."});
  else if (power.valid !== true || old) out.push({value: "? Invalid readback", cls: 'q-bad', detail: why});
  else {
    const on = power.run_stop_setting !== 0;
    const detail = `Setting ${isNumber(power.set_voltage_v) ? trim(power.set_voltage_v, 1) : '—'} V · Output ${isNumber(power.current_a) ? trim(power.current_a * 1000, 1) : '—'} mA`;
    out.push({value: `${on ? 'ON' : 'OFF'} · ${isNumber(power.voltage_v) ? Math.round(power.voltage_v) : '—'} V`, cls: on ? 'q-run' : 'q-ok', detail});
  }

  if (stale) out.push(unknown);
  else if (!rpi.connected) out.push(none);
  else if (discharging) out.push({value: labels.nano.RUNNING, cls: 'q-run', detail: "is discharging, and the reading value will be updated after it is completed."});
  else if (nano.valid !== true || old) out.push({value: "? Invalid readback", cls: 'q-bad', detail: why});
  else if (nano.state === 'FAULT') out.push({value: `Fault: ${labels.fault[nano.fault] || nano.fault || ''}`, cls: 'p2', detail: ''});
  else out.push({value: labels.nano[nano.state] || nano.state || '—', cls: nano.state === 'RUNNING' ? 'q-run' : 'q-ok', detail: nano.firmware ? `Firmware ${nano.firmware}` : ''});

  //pump
  if (stale) out.push(unknown);
  else if (!linked) out.push({value: pump.text || labels.pumpText(pump), cls: labels.pumpClass(pump), detail: pump.error || ''});
  else {
    const activity = pumpActivity(pump, labels);
    out.push({value: `${pump.text || labels.pumpText(pump)} · ${activity.text}`, cls: activity.run ? 'q-run' : 'q-ok', detail: pump.description || ''});
  }

  //syringe pump
  const syringe = pump.syringe || {};
  if (stale) out.push(unknown);
  else if (!linked) out.push(none);
  else if (syringe.online === false) out.push({value: "No response", cls: 'q-bad', detail: ''});
  else if (syringe.busy) out.push({value: "in action", cls: 'q-run', detail: ''});
  else if (syringe.initialised) {
    out.push({value: isNumber(syringe.volume_ul) ? `Initialized · In syringe ${trim(syringe.volume_ul / 1000, 3)} mL` : "has been initialized", cls: 'q-ok', detail: ''});
  } else out.push({value: "has not been initialized yet", cls: 'q-na', detail: ''});

  //Weight
  const scale = pump.scale || {};
  const decimals = (ctx.store.boot && ctx.store.boot.hardware && ctx.store.boot.hardware.scale && ctx.store.boot.hardware.scale.decimals) || 3;
  if (stale) out.push(unknown);
  else if (!linked || !isNumber(scale.grams)) out.push(none);
  else {
    out.push({value: `${scale.grams.toFixed(decimals)} g`, cls: 'q-ok',
      detail: scale.calibrated && scale.calibrated_at ? `Last calibration ${labels.fmtTime(scale.calibrated_at, 'YYYY-MM-DD')}` : "has not been calibrated yet"});
  }

  //active alarm
  const alarms = state.alarms || {};
  const active = alarms.active || [];
  if (!active.length) out.push({value: "None", cls: 'q-ok', detail: ''});
  else {
    const worst = Math.min(...active.map((item) => item.priority));
    out.push({value: `${active.length} item (${alarms.unacked || 0} not confirmed)`, cls: `p${worst}`, detail: active[0].text || ''});
  }
  return out;
}

function renderStrip(state, ctx) {
  const {dom} = ctx;
  stripValues(state, ctx).forEach((fact, index) => {
    const cell = el.cells[index];
    cellFacts[index] = fact;
    dom.setText(cell.value, fact.value);
    if (cell.value.className !== fact.cls) cell.value.className = fact.cls;
    setTitle(cell.box, `${cell.label}: ${fact.value}` + (fact.detail ? `\n${fact.detail}` : ''));
  });
}
