//calibration.js — Calibration (AMENDMENTS M5.5; ported from the original HMI's web/calibration/app.js).
//
// Three tabs under one status line (the coefficients in use):
//calibration settings the standards table, the two Recipes, the discard rounds; the program checks every edit (400 ms) and
//keeps the draft (1200 ms). "Start calibration" opens the high-voltage start dialog.
//executes the bottle-swap prompt with "Confirm fluid change", the standards as cards, the events of this calibration.
//Stopping is the footer's “STOP”.
//the records and, for the chosen one, the regression per metal, the two tables (a standard can be left
//out of one metal's fit), "reanalysis" and "apply coefficient" (maintenance session).
// Data: GET /api/calibration when state.revs.calibrations (or .recipes) changes and every 2 s while a calibration runs;
// GET /api/calibrations/<id> for the record on screen; GET /api/recipes for what the start dialog must ask;
//GET /api/events for the execution tab. Every value is a button that opens a dialog; nothing scrolls.

const METALS = ['Cu', 'Zn', 'Ni'];
const STATUS = {running: "is executing", analyzing: "Under analysis", analyzed: "analyzed", analysis_failed: "analysis failed",
  failed: "failed", stopped: "has stopped", interrupted: "interrupt", applied: "has been applied"};
const ACTIVE = ['waiting', 'running', 'pausing', 'paused', 'stopping'];
const TABS = [['setup', "calibration settings"], ['run', "execution"], ['results', "results and records"]];
const VIEWS = [['fit', "returns"], ['levels', "standard product"], ['points', "round"]];
const SUMMARY = ["concentration number (including blank)", "The number of rounds included in the regression", "Total number of measurement rounds (including pre-run)", "Waiting time per round", "fluid replacement and draining", "Estimated total time (excluding fluid change)"];
const MAX_LEVELS = 12;
const LEVEL_ROWS = 8;            // rows per page: the standards table
const RECORD_ROWS = 6;           // ... the record list
const STAT_ROWS = 7;             // ... the table of standards of a record
const POINT_ROWS = 11;           // ... the table of rounds of a record
const EVENT_ROW_PX = 26;
const EVENT_KEEP = 30;
const CHECK_MS = 400;
const SAVE_MS = 1200;
const RUN_POLL_MS = 2000;
const RETRY_MS = 5000;
const TAP_GUARD_MS = 500;        //“Confirm fluid change” does not take a tap in the first moment after it appeared
const CLEAR = 'clear';
const NEED_PIN = "application requires maintenance PIN: please log in at \"🔧 Maintenance\" on the left and then click Apply";
const NO_OPERATOR = "Please select the operator in the upper right corner first";
const T_STALE = "The program did not respond";
const HV_TEXT = "activates high-voltage plasma every round. Please confirm that the detector cover is closed, no one is touching the high-voltage parts on site, and a physical stop method is in place. \"Stop\" on the screen means the software stops, not an emergency stop. In an emergency, please press the physical emergency stop button.";
const ACK_FALLBACK = "RPi is not connected, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.";

let el = null;
let page = null;                 // the ctx of this module
let shown = false;
let tab = 'setup';

let data = null;                 // reply of GET /api/calibration
let dataRevs = '';               // the revs it answers
let dataAt = 0;
let loading = false;
let failed = {at: 0, text: ''};
const recipes = {rev: null, rows: null, pending: null, failedAt: 0};      // GET /api/recipes: uses_pump, step_count
const cache = new Map();         // record id -> {record, rev, loading, error, failedAt, n}
const events = {rev: null, rows: [], loading: false, failedAt: 0};

let plan = null;                 // the draft on screen: {recipe_id, prime_recipe_id, discard_rounds, levels}
let check = {info: null, error: '', pending: true, turn: 0};   // what the program says about the draft
let checkTimer = 0;
let saveTimer = 0;
let dirty = false;
let jumpToLast = false;          // a standard was added: show the page it is on
let starting = false;            //“Start calibration” was pressed: its dialogs are open or its request is on the way
let launching = false;           // ... the request is on the way

let promptKey = '';              //the prompt the execution tab was brought forward for
let promptAt = 0;                // when the prompt bar appeared (performance.now)
let confirming = false;
let eventRows = 8;

let selectedId = '';
let metal = 'Cu';
let view = 'fit';
let fitKey = '';                 // what the regression chart shows
let busyAction = '';             // 'analyze' | 'apply' | 'Cu:2' (a tick that is being sent)

const levelIndex = new WeakMap();    // row of the standards table -> index in plan.levels
const levelParts = new WeakMap();
const recordParts = new WeakMap();
const recordIds = new WeakMap();
const statParts = new WeakMap();
const statFacts = new WeakMap();
const noteLists = new WeakMap();

// ── small helpers ───────────────────────────────────────────────────────────────────────────────────────────
const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
// the original's number format: fixed digits, exponential from 1e5 and under 1e-3
const fmt = (value, digits = 4) => (isNumber(value)
  ? (Math.abs(value) >= 1e5 || (value !== 0 && Math.abs(value) < 1e-3) ? value.toExponential(3) : value.toFixed(digits)) : '—');
const sig = (value, digits) => (value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value).toPrecision(digits) : '—');
const concText = (value) => (value === null || value === undefined ? '—' : String(value));
const levelConc = (level) => METALS.filter((m) => level.conc[m] !== null && level.conc[m] !== undefined).map((m) => `${m} ${level.conc[m]}`).join(' / ') || '—';

function setHidden(node, hidden) {
  const value = Boolean(hidden);
  if (node.hidden !== value) node.hidden = value;
}

function setDisabled(node, disabled) {
  const value = Boolean(disabled);
  if (node.disabled !== value) node.disabled = value;
}

function setTitle(node, text) {
  const value = text || '';
  if ((node.getAttribute('title') || '') === value) return;
  if (value) node.setAttribute('title', value);
  else node.removeAttribute('title');
}

function setCls(node, cls) {
  if (node.className !== cls) node.className = cls;
}

function again(ctx) {
  const c = ctx || page;
  if (shown && el && c && c.store.state) render(c.store.state, c);
}

function refusal(error) {
  const text = error && error.message ? String(error.message) : String(error);
  const reasons = ((error && error.reasons) || []).filter((item) => item && item.text);
  const lines = reasons.some((item) => item.text === text) ? [] : [text];
  for (const item of reasons) lines.push(item.fix ? `${item.text}
Processing method: ${item.fix}` : item.text);
  return lines.join('\n\n');
}

const stamp = (ctx, iso) => ctx.labels.fmtTime(iso, 'YYYY-MM-DD HH:MM');

// ── data ────────────────────────────────────────────────────────────────────────────────────────────────────
async function loadData(ctx, revs) {
  if (loading) return;
  loading = true;
  try {
    const reply = await ctx.api.get('/api/calibration', {timeout: 15000});
    const before = data;
    data = reply;
    dataRevs = revs;
    dataAt = Date.now();
    failed = {at: 0, text: ''};
    if (!plan) {
      plan = toEditable(reply.plan);
      queueCheck(ctx, 0);
    } else if (!before || JSON.stringify(before.recipes) !== JSON.stringify(reply.recipes)) {
      queueCheck(ctx, 0);              // a Recipe was published, changed or deleted: the draft may mean something else now
    }
  } catch (error) {
    failed = {at: Date.now(), text: error.message};
  }
  loading = false;
  again(ctx);
}

// The published Recipes with what GET /api/calibration does not say about them (uses_pump, step_count).
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
  if (recipes.pending || (recipes.rows && recipes.rev === rev) || ctx.store.stale) return;
  if (Date.now() - recipes.failedAt < RETRY_MS) return;
  loadRecipes(ctx, rev).catch(() => {});
}

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

const recipeMore = (id) => (recipes.rows || []).find((row) => row.id === id) || null;
const recipeRow = (id) => ((data && data.recipes) || []).find((row) => row.id === id) || null;

function recipeFacts(row) {
  const more = recipeMore(row.id);
  const parts = [more && more.step_count ? `${more.step_count} steps` : null,
    row.plasma_count ? "contains high voltage plasma" : "does not contain plasma"];
  if (more) parts.push(more.uses_pump ? "uses pump" : "does not require a pump");
  return parts.filter(Boolean).join(' · ');
}

// One full record; read again when state.revs.calibrations has changed since.
async function fetchRecord(ctx, id, rev, entry) {
  entry.loading = true;
  try {
    entry.record = await ctx.api.get('/api/calibrations/' + id, {timeout: 15000});
    entry.rev = rev;
    entry.error = '';
    entry.failedAt = 0;
    entry.n += 1;
  } catch (error) {
    entry.error = error.message;
    entry.failedAt = Date.now();
    if (error.status === 404) entry.rev = rev;      // there is no such record: asking again changes nothing
  }
  entry.loading = false;
  again(ctx);
}

function wantRecord(ctx, id, rev) {
  let entry = cache.get(id);
  if (!entry) {
    entry = {record: null, rev: null, loading: false, error: '', failedAt: 0, n: 0};
    cache.set(id, entry);
  }
  if (!entry.loading && entry.rev !== rev && !ctx.store.stale && Date.now() - entry.failedAt >= RETRY_MS) fetchRecord(ctx, id, rev, entry);
  return entry;
}

function refresh(ctx, id) {
  const entry = cache.get(id);
  if (entry) entry.rev = null;
  dataRevs = '';
  again(ctx);
}

function wantEvents(ctx, rev) {
  if (events.loading || events.rev === rev || ctx.store.stale || Date.now() - events.failedAt < RETRY_MS) return;
  events.loading = true;
  ctx.api.get('/api/events?limit=120', {timeout: 10000}).then((reply) => {
    events.rows = Array.isArray(reply.events) ? reply.events : [];
    events.rev = rev;
    events.failedAt = 0;
  }, () => {
    events.failedAt = Date.now();
  }).finally(() => {
    events.loading = false;
    again(ctx);
  });
}

// ── the draft ───────────────────────────────────────────────────────────────────────────────────────────────
function toConc(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = typeof value === 'number' ? value : Number(String(value).trim());
  return Number.isFinite(number) && number >= 0 && number <= 100000 ? number : null;
}

function toCount(value, low, high, fallback) {
  const number = typeof value === 'string' && /^\d+$/.test(value.trim()) ? Number(value.trim()) : value;
  return Number.isInteger(number) && number >= low && number <= high ? number : fallback;
}

// The stored draft as this page edits it: numbers or null (= this bottle does not hold the metal).
function toEditable(raw) {
  const source = raw && Array.isArray(raw.levels) ? raw : {levels: []};
  return {
    recipe_id: typeof source.recipe_id === 'string' ? source.recipe_id : '',
    prime_recipe_id: typeof source.prime_recipe_id === 'string' ? source.prime_recipe_id : '',
    discard_rounds: toCount(source.discard_rounds, 0, 3, 1),
    levels: source.levels.slice(0, MAX_LEVELS).map((level) => {
      const item = level && typeof level === 'object' ? level : {};
      const conc = item.conc && typeof item.conc === 'object' ? item.conc : {};
      return {name: String(item.name === null || item.name === undefined ? '' : item.name).slice(0, 20),
        conc: Object.fromEntries(METALS.map((m) => [m, toConc(conc[m])])),
        rounds: toCount(item.rounds, 1, 10, 3)};
    }),
  };
}

const payload = () => ({recipe_id: plan.recipe_id, prime_recipe_id: plan.prime_recipe_id, discard_rounds: plan.discard_rounds,
  levels: plan.levels.map((level) => ({name: level.name, conc: {...level.conc}, rounds: level.rounds}))});

function queueCheck(ctx, delay) {
  clearTimeout(checkTimer);
  check = {...check, pending: true};
  checkTimer = setTimeout(() => { checkPlan(ctx); }, delay);
}

// Asks the program what the draft means (order, rounds, estimate, warnings) or why it cannot run.
async function checkPlan(ctx) {
  clearTimeout(checkTimer);
  if (!plan) return null;
  const turn = check.turn + 1;
  check = {...check, pending: true, turn};
  let next;
  if (!plan.recipe_id) {
    next = {info: null, error: "Please select the standard Recipe"};
  } else {
    try {
      next = {info: await ctx.api.post('/api/calibration/check', {plan: payload()}, {timeout: 10000}), error: ''};
    } catch (error) {
      next = {info: null, error: error.message};
      if (!error.status && turn === check.turn) checkTimer = setTimeout(() => { checkPlan(ctx); }, RETRY_MS);   // no answer: asked again
    }
  }
  if (turn !== check.turn) return null;        // the draft was changed meanwhile: a newer question is on its way
  check = {...next, pending: false, turn};
  again(ctx);
  return check.info;
}

async function savePlan(ctx) {
  clearTimeout(saveTimer);
  saveTimer = 0;
  if (!plan) return;
  const sent = JSON.stringify(payload());
  try {
    await ctx.api.post('/api/calibration/plan', {plan: JSON.parse(sent)}, {timeout: 10000});
    if (JSON.stringify(payload()) === sent) dirty = false;
  } catch (_) {
    if (dirty && !saveTimer) saveTimer = setTimeout(() => { savePlan(ctx); }, RETRY_MS);     // the draft stays on this page; tried again
  }
}

function changed(ctx) {
  dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { savePlan(ctx); }, SAVE_MS);
  queueCheck(ctx, CHECK_MS);
  again(ctx);
}

// ── editing: every value through a dialog ───────────────────────────────────────────────────────────────────
async function editName(ctx, index) {
  const level = plan && plan.levels[index];
  if (!level) return;
  const name = await ctx.dialog.text({title: "standard product name", value: level.name, maxLength: 20, rule: "1–20 characters, name cannot be repeated"});
  if (name === null || !plan.levels.includes(level) || name === level.name) return;
  level.name = name;
  changed(ctx);
}

async function editConc(ctx, index, m) {
  const level = plan && plan.levels[index];
  if (!level) return;
  const value = await ctx.dialog.pad({title: `${level.name || "standard product"}: ${m} Concentration`, min: 0, max: 100000, decimals: 4, unit: 'mg/L',
    value: level.conc[m] === null ? '' : level.conc[m], actions: [{label: "Clear", value: CLEAR}], detail: `Clear = This bottle does not contain ${m}`});
  if (value === null || !plan.levels.includes(level)) return;
  const next = value === CLEAR ? null : value;
  if (next === level.conc[m]) return;
  level.conc[m] = next;
  changed(ctx);
}

async function editRounds(ctx, index) {
  const level = plan && plan.levels[index];
  if (!level) return;
  const value = await ctx.dialog.pad({title: `${level.name || "standard product"}: Number of rounds`, min: 1, max: 10, decimals: 0, unit: "wheel", value: level.rounds});
  if (value === null || !plan.levels.includes(level) || value === level.rounds) return;
  level.rounds = value;
  changed(ctx);
}

async function removeLevel(ctx, index) {
  const level = plan && plan.levels[index];
  if (!level) return;
  const yes = await ctx.dialog.confirm({title: "Delete this concentration", variant: 'danger', confirmLabel: "Delete",
    lines: [["Name", level.name || '—'], ["concentration (mg/L)", levelConc(level)], ["Round number", String(level.rounds)]]});
  if (!yes) return;
  const at = plan.levels.indexOf(level);
  if (at < 0) return;
  plan.levels.splice(at, 1);
  changed(ctx);
}

function addLevel(ctx) {
  if (!plan) return;
  if (plan.levels.length >= MAX_LEVELS) {
    ctx.message("up to 12 concentrations\nThere is no recipe yet for", 'warn');
    return;
  }
  const names = new Set(plan.levels.map((level) => level.name));
  let n = plan.levels.length;
  while (names.has(`STD-${n}`)) n += 1;
  plan.levels.push({name: `STD-${n}`, conc: {Cu: null, Zn: null, Ni: null}, rounds: 3});
  jumpToLast = true;
  changed(ctx);
}

async function editRecipe(ctx) {
  if (!plan || !data) return;
  const rows = data.recipes || [];
  if (!rows.length) {
    await ctx.dialog.message("No recipes yet", "Please ask the maintenance personnel to post it in \"Maintenance › Recipe\".");
    return;
  }
  const id = await ctx.dialog.choose({title: "Standard Recipe", wide: true, value: plan.recipe_id,
    detail: "Recipe only writes \"one round\", which must contain exactly 1 plasma_run; the number of rounds is set on this page. Posted by maintenance personnel.",
    choices: rows.map((row) => ({value: row.id, label: row.name, small: recipeFacts(row),
      disabled: !row.valid ? "Recipe failed the check" : row.plasma_count !== 1 ? `must contain exactly 1 plasma_run (currently ${row.plasma_count})` : false}))});
  if (id === null || !plan || id === plan.recipe_id) return;
  plan.recipe_id = id;
  if (plan.prime_recipe_id === id) plan.prime_recipe_id = '';
  changed(ctx);
}

async function editPrime(ctx) {
  if (!plan || !data) return;
  const rows = (data.recipes || []).filter((row) => !row.uses_rpi && row.id !== plan.recipe_id);
  const id = await ctx.dialog.choose({title: "Fluid replacement and emptying Recipe (optional)", wide: true, value: plan.prime_recipe_id,
    detail: "Run once every time after pressing \"Confirm Fluid Change\": for example, pump out new fluid and push out the remaining fluid in the tube, and then empty it. Recipes for Assay or RPi steps are not available and are not listed.",
    choices: [{value: '', label: "Not used"}, ...rows.map((row) => ({value: row.id, label: row.name, small: recipeFacts(row),
      disabled: row.valid ? false : "Recipe failed the check"}))]});
  if (id === null || !plan || id === plan.prime_recipe_id) return;
  plan.prime_recipe_id = id;
  changed(ctx);
}

function step(ctx, delta) {
  if (!plan) return;
  const next = Math.max(0, Math.min(3, plan.discard_rounds + delta));
  if (next === plan.discard_rounds) return;
  plan.discard_rounds = next;
  changed(ctx);
}

//──Start calibration─────────────────────────────────────────────────────────────────────────────────
// Why a calibration cannot start now ('' = it can, as far as this page knows; the program checks the equipment).
function startBlock(ctx, state) {
  if (ctx.store.stale) return T_STALE;
  const sched = state.schedule;
  if (state.calibration_run || (sched && sched.kind === 'calibration' && ACTIVE.includes(sched.status))) return "calibration in progress";
  const reasons = (state.interlock && state.interlock.start && state.interlock.start.reasons) || [];
  const first = reasons.find((item) => item && item.text);
  if (first) return first.text;
  if (sched && ACTIVE.includes(sched.status)) return "already has a schedule in progress";
  // Every round of a calibration measures: without the RPi the program refuses the start (its own words).
  return state.rpi && state.rpi.connected === false ? "RPi not connected" : '';
}

function durationText(seconds) {
  if (!isNumber(seconds)) return '—';
  return seconds < 60 ? `about ${Math.round(seconds)} seconds` : `about ${Math.round(seconds / 60)} minutes`;
}

async function startCalibration(ctx) {
  if (starting || !plan) return;
  starting = true;
  again(ctx);
  try {
    let state = ctx.store.state;
    if (!state || ctx.store.stale) return;
    let operator = state.operator;
    if (!operator) {
      // The frame's operator picker is not part of ctx (and a page may not import frame.js): say where it is.
      if (typeof ctx.pickOperator === 'function') operator = await ctx.pickOperator();
      else await ctx.dialog.message("No operator selected yet", NO_OPERATOR);
      if (!operator) return;
    }
    const info = await checkPlan(ctx);           //"Start calibration" is the moment of the confirmation
    if (!info) return;                           // the reason is on the page
    const list = await recipesNow(ctx);
    if (!list) return;
    state = ctx.store.state;
    const block = startBlock(ctx, state);
    if (block) {
      await ctx.dialog.message("currently cannot start calibration", block);
      return;
    }
    const body = payload();
    const used = [body.recipe_id, body.prime_recipe_id].filter(Boolean).map((id) => list.find((row) => row.id === id) || null);
    const liquid = used.some((row) => row && row.uses_pump);
    const manual = (state.interlock && state.interlock.manual) || {};
    const needAck = liquid && Boolean(manual.need_hv_ack);
    const values = (state.rpi && state.rpi.parameters && state.rpi.parameters.values) || {};
    const volts = values.Voltage === null || values.Voltage === undefined ? '—' : values.Voltage;
    const limit = (state.rpi && state.rpi.server && state.rpi.server.limits && state.rpi.server.limits.max_voltage_v) || 700;
    const order = (info.plan && info.plan.levels ? info.plan.levels : body.levels).map((level) => level.name).join(' → ');
    const lines = [
      ["Standard Recipe", info.recipe_name || ''],
      ["Operator", operator],
      ["Measurement rounds", `total ${info.runs} rounds (among which ${info.kept_runs} rounds are included in the regression)`],
      ["estimated time", `${durationText(info.estimated_seconds)} + fluid replacement time`],
      ["execution sequence", order],
      ["fluid replacement and draining", info.prime_recipe_name || "Not used"],
      ["HSPY set voltage", `${volts} V (upper limit ${limit} V)`],
    ];
    const ticks = [`I have confirmed that every measurement will automatically output ${volts} V high voltage and will not ask again.`, "The electrode and high-voltage line have been connected; people have stayed away and the upper cover has been closed"];
    if (liquid) ticks.push("pipelines, reagents and waste drain are in place");
    // The sentence is the program's (AMENDMENTS M13): the RPi may be away, or connected with a supply it cannot read.
    if (needAck) ticks.push(manual.hv_ack_text || ACK_FALLBACK);
    ticks.push("physical emergency stop/power-off switch is ready for use at any time");
    const notes = info.warnings || [];
    const warn = [HV_TEXT,
      "will pause every time a concentration is changed, waiting to press \"Confirm Liquid Change\"" + (info.prime_recipe_name ? `; After confirmation, first execute "${info.prime_recipe_name}" to drain the residue in the tube, and then start pre-run and measurement.` : '.'),
      ...notes.slice(0, 1)];
    if (notes.length > 1) warn.push(`and ${notes.length - 1} are reminded that you can see them all in "Calibration Settings"`);
    const yes = await ctx.dialog.confirm({title: "⚠ Start standard calibration", variant: 'hv', timeout: 90, lines, warn, ticks, confirmLabel: "Confirm high voltage and start"});
    if (!yes) return;
    launching = true;
    again(ctx);
    await savePlan(ctx);
    try {
      await ctx.api.post('/api/calibration/start', {plan: body, confirm: {high_voltage: true, liquid, hv_off_checked: needAck}}, {timeout: 20000});
      ctx.message("Calibration has started");
      tab = 'run';
      dataRevs = '';
    } catch (error) {
      launching = false;
      await ctx.dialog.message("Calibration did not start", refusal(error), {level: 'error'});
    }
  } finally {
    starting = false;
    launching = false;
    again(ctx);
  }
}

//──Execute ───────────────────────────────────────────────────────────────────────────────────
async function confirmSwap(ctx) {
  if (confirming || performance.now() - promptAt < TAP_GUARD_MS) return;
  confirming = true;
  again(ctx);
  try {
    await ctx.api.post('/api/calibration/confirm', {}, {timeout: 10000});
    //While the calibration is paused the program keeps the confirmation and goes on only after "CONTINUE RESUME".
    const run = ctx.store.state && ctx.store.state.calibration_run;
    const held = Boolean(run && (run.status === 'paused' || run.status === 'pausing'));
    ctx.message(held ? "The fluid change has been confirmed; click \"RESUME\" before proceeding." : "Fluid replacement confirmed, continue execution", held ? 'warn' : 'info');
  } catch (error) {
    ctx.message(error.message, 'error');
  }
  confirming = false;
  dataRevs = '';
  again(ctx);
}

//──Results and records──────────────────────────────────────────────────────────────────────────────
function select(ctx, id) {
  if (!id || id === selectedId) return;
  selectedId = id;
  fitKey = '';
  again(ctx);
}

const selected = () => (selectedId && cache.get(selectedId) ? cache.get(selectedId).record : null);

async function toggleLevel(ctx, fact) {
  const rec = selected();
  if (!rec || !fact || busyAction || rec.id !== fact.id) return;
  const include = !fact.included;
  const exclude = JSON.parse(JSON.stringify(rec.exclude_levels || {}));
  const list = new Set(exclude[fact.metal] || []);
  if (include) list.delete(fact.index);
  else list.add(fact.index);
  exclude[fact.metal] = [...list].sort((a, b) => a - b);
  busyAction = `${fact.metal}:${fact.index}`;
  again(ctx);
  try {
    await ctx.api.post('/api/calibration/exclude', {id: rec.id, exclude}, {timeout: 15000});
    ctx.message(`${fact.metal} ${include ? "included" : "excluded"} ${fact.name} , has returned`);
  } catch (error) {
    ctx.message(error.message, 'error');
  }
  busyAction = '';
  refresh(ctx, rec.id);
}

async function reanalyze(ctx) {
  const id = selectedId;
  if (!id || busyAction) return;
  busyAction = 'analyze';
  again(ctx);
  try {
    await ctx.api.post('/api/calibration/analyze', {id}, {timeout: 15000});
    ctx.message("Reanalysis has started");
  } catch (error) {
    ctx.message(error.message, 'error');
  }
  busyAction = '';
  refresh(ctx, id);
}

async function applyRecord(ctx) {
  const rec = selected();
  if (!rec || !rec.analysis || busyAction) return;
  if (!ctx.unlocked()) {
    await ctx.dialog.message("Apply correction coefficient", NEED_PIN, {level: 'error'});
    return;
  }
  const fits = rec.analysis.fits || {};
  if (!METALS.some((m) => fits[m] && fits[m].valid)) {
    await ctx.dialog.message("Apply correction coefficient", "No metal has a valid return", {level: 'error'});
    return;
  }
  const lines = METALS.map((m) => {
    const fit = fits[m];
    return [m, fit && fit.valid ? `a = ${sig(fit.a, 6)},b = ${sig(fit.b, 6)},c = 1 (R² ${fmt(fit.r2, 4)}) ` : "has no valid regression and maintains the current coefficient."];
  });
  const yes = await ctx.dialog.confirm({title: "Apply correction coefficient", wide: true, confirmLabel: "Apply\nAfter", lines,
    detail: "is applied, subsequent measurements will use this set of coefficients to convert concentration. The old coefficients are saved in this correction record."});
  if (!yes) return;
  busyAction = 'apply';
  again(ctx);
  try {
    await ctx.api.post('/api/calibration/apply', {id: rec.id}, {timeout: 15000});
    ctx.message("New coefficient has been applied");
  } catch (error) {
    if (error.status === 403 && error.code === 'locked') await ctx.dialog.message("Apply correction coefficient", NEED_PIN, {level: 'error'});
    else await ctx.dialog.message("is not applied", refusal(error), {level: 'error'});
  }
  busyAction = '';
  refresh(ctx, rec.id);
}

function coefficientsText(coef, digits) {
  return METALS.map((m) => {
    const k = coef && coef[m];
    return k && k.a !== null && k.a !== undefined ? `${m} a=${sig(k.a, digits)} b=${sig(k.b, digits)} c=${k.c}` : `${m} —`;
  });
}

function showCoefficients(ctx) {
  if (!data) return;
  const coef = data.coefficients || {};
  const active = data.active;
  ctx.dialog.message("current coefficient", `Concentration = (a × light intensity + b) × c Unit ${data.unit || 'mg/L'}`, {wide: true,
    lines: [["calibration", active ? `${ctx.labels.fmtTime(active.applied_at, 'YYYY/MM/DD HH:MM')} Correction (${(active.metals || []).join(', ')})` : "uncorrected page"],
      ...METALS.map((m) => {
        const k = coef[m];
        return [m, k && k.a !== null && k.a !== undefined ? `a = ${k.a},b = ${k.b},c = ${k.c}` : '—'];
      })]});
}

// ── the regression chart (360 x 240, as the original) ───────────────────────────────────────────────────────
function drawFit(ctx, fit) {
  const {h} = ctx.dom;
  const root = h('svg', {viewBox: '0 0 360 240'});
  const pts = ((fit && fit.points) || []).filter((p) => isNumber(p.x) && isNumber(p.y));
  if (!pts.length) {
    root.append(h('svg:text', {x: 180, y: 124, 'text-anchor': 'middle'}, "points not included in the regression"));
    return root;
  }
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const pad = (lo, hi) => { const span = hi - lo || Math.abs(hi) || 1; return [lo - span * 0.08, hi + span * 0.08]; };
  const [x0, x1] = pad(Math.min(...xs), Math.max(...xs));
  const [y0, y1] = pad(Math.min(0, ...ys), Math.max(...ys));
  const L = 52;
  const R = 336;
  const T = 12;
  const B = 206;
  const sx = (x) => L + (x - x0) / (x1 - x0) * (R - L);
  const sy = (y) => B - (y - y0) / (y1 - y0) * (B - T);
  root.append(h('svg:rect.cf-frame', {x: L, y: T, width: R - L, height: B - T}));
  for (let i = 0; i <= 4; i += 1) {
    const yv = y0 + (y1 - y0) * i / 4;
    const xv = x0 + (x1 - x0) * i / 4;
    root.append(h('svg:line.cf-grid', {x1: L, x2: R, y1: sy(yv), y2: sy(yv)}),
      h('svg:text', {x: L - 4, y: sy(yv) + 4, 'text-anchor': 'end'}, String(Number(yv.toPrecision(3)))),
      h('svg:text', {x: sx(xv), y: B + 14, 'text-anchor': 'middle'}, String(Number(xv.toPrecision(3)))));
  }
  root.append(h('svg:text', {x: (L + R) / 2, y: 236, 'text-anchor': 'middle'}, "Light intensity x (area method)"),
    h('svg:text', {x: 12, y: (T + B) / 2, 'text-anchor': 'middle', transform: `rotate(-90 12 ${(T + B) / 2})`}, "concentration y (mg/L)"));
  if (fit.valid && isNumber(fit.a) && isNumber(fit.b)) {
    root.append(h('svg:line.cf-line', {x1: sx(x0), y1: sy(fit.a * x0 + fit.b), x2: sx(x1), y2: sy(fit.a * x1 + fit.b)}));
  }
  for (const p of pts) root.append(h('svg:circle.cf-dot', {cx: sx(p.x), cy: sy(p.y), r: 4}));
  return root;
}

// ── message boxes: one per message, as many lines as the room allows; a tap shows all of it ─────────────────
function renderNotes(ctx, container, messages, most) {
  const {dom} = ctx;
  let list = messages.map((item) => ({...item, all: item.text}));
  if (list.length > most) {
    const kept = list.slice(0, most - 1);
    const rest = list.slice(most - 1);
    list = [...kept, {kind: rest.some((item) => item.kind === 'err') ? 'err' : 'warn', text: `and ${rest.length}, click here to view`,
      all: rest.map((item) => item.text).join('\n\n')}];
  }
  noteLists.set(container, list);
  dom.setClass(container, 'k4', list.length === 1);
  dom.setClass(container, 'k2', list.length === 2);
  setHidden(container, !list.length);
  dom.list(container, list, (item, index) => index, (item, index) => {
    const box = dom.h('button', null, dom.h('span'));
    box.addEventListener('click', () => {
      const now = (noteLists.get(container) || [])[index];
      if (now) ctx.dialog.message("message", now.all, {level: now.kind === 'err' ? 'error' : ''});
    });
    return box;
  }, (box, item) => {
    setCls(box, item.kind === 'err' ? 'cal-err' : 'cal-warn');
    dom.setText(box.firstChild, item.text);
  });
}

// ── mount ───────────────────────────────────────────────────────────────────────────────────────────────────
function mountSetup(ctx) {
  const {h} = ctx.dom;
  const add = h('button.btn', {onclick: () => addLevel(ctx)}, "+ new concentration");
  const levels = h('div.cal-levels');
  const levelPager = h('div');
  const std = h('div.panel.cal-std', null,
    h('div.ph', null, h('h2', null, "standard (mixed standard, mg/L)"), h('span.sp'), add),
    h('div.cal-ghead', null, h('span.c-name', null, "Name"), METALS.map((m) => h('span.c-num', null, m)), h('span.c-rounds', null, "Round number"), h('span.c-del')),
    levels, levelPager,
    h('div.cal-stdfoot', null, h('span', null, "Leave blank = this bottle does not contain the metal; blank is fixed and ranked first"), h('span', null, "system automatically sorts from low to high concentration")));

  const tile = (label, action) => {
    const value = h('b');
    const small = h('small');
    return {box: h('button.stile', {onclick: action}, h('span', null, label), value, small), value, small, label};
  };
  const recipe = tile("Standard Recipe", () => { editRecipe(ctx); });
  const prime = tile("Fluid replacement and emptying Recipe (optional)", () => { editPrime(ctx); });
  const discard = h('b');
  const stepper = h('div.cal-step', null,
    h('span', null, "Number of pre-run rounds for each concentration (not included in regression)", h('small', null, "0–3 rounds")),
    h('div.cal-stepper', null, h('button.btn', {onclick: () => step(ctx, -1)}, '−'), discard, h('button.btn', {onclick: () => step(ctx, 1)}, '＋')));
  const sum = SUMMARY.map((label) => {
    const value = h('b');
    return {row: h('div', null, h('span', null, label), value), value};
  });
  const notes = h('div.cal-notes');
  const why = h('span.sub');
  const start = h('button.btn.primary', {onclick: () => { startCalibration(ctx); }}, "Start calibration");
  const side = h('div.panel.cal-side', null,
    h('div.ph', null, h('h2', null, "Each round process")),
    h('div.cal-form', null, recipe.box, prime.box, stepper, h('div.cal-sum', null, sum.map((item) => item.row)), notes),
    h('div.cal-sidefoot', null, why, start));
  return {pane: h('div.cal-pane', {hidden: true}, std, side), add, levels, levelPager, recipe, prime, discard, sum, notes, why, start};
}

function mountRun(ctx) {
  const {h} = ctx.dom;
  const promptText = h('b');
  const promptBox = h('div', {onclick: () => { if (promptText.textContent) ctx.dialog.message("Waiting for fluid change", promptText.textContent); }},
    h('small', null, "Waiting for fluid change"), promptText);
  const confirm = h('button.btn.primary', {onclick: () => { confirmSwap(ctx); }}, "Confirm fluid change");
  const prompt = h('div.cal-prompt', {hidden: true}, promptBox, confirm);
  const title = h('h2');
  const sub = h('span.sub');
  const cards = h('div.cal-cards');
  // one line, cut when it is long (an error): a tap opens all of it
  const stateLine = h('div.cal-state', {hidden: true, attrs: {role: 'button', tabindex: '0'}});
  const tellState = () => {
    if (stateLine.textContent) ctx.dialog.message("latest calibration", stateLine.textContent, {level: stateLine.classList.contains('err') ? 'error' : ''});
  };
  stateLine.addEventListener('click', tellState);
  stateLine.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); tellState(); } });
  const log = h('div.cal-log');
  const empty = h('div.cal-empty', {hidden: true});
  const logPager = h('div');
  const panel = h('div.panel.cal-runp', null, h('div.ph', null, title, sub), cards, stateLine, log, empty, logPager);
  return {pane: h('div.cal-pane.col', {hidden: true}, prompt, panel), prompt, promptText, confirm, title, sub, cards, stateLine, log, empty, logPager};
}

function mountResults(ctx) {
  const {h} = ctx.dom;
  const records = h('div.cal-records');
  const recEmpty = h('div.cal-empty', {hidden: true});
  const recPager = h('div');
  const list = h('div.panel.cal-reclist', null, h('div.ph', null, h('h2', null, "calibration record")), records, recEmpty, recPager);

  const title = h('h2', null, "Select the record on the left");
  const sub = h('span.sub');
  const redo = h('button.btn', {hidden: true, onclick: () => { reanalyze(ctx); }}, "reanalysis");
  const apply = h('button.btn.primary', {hidden: true, onclick: () => { applyRecord(ctx); }}, "application coefficient");

  const metals = METALS.map((m) => [m, h('button', {onclick: () => { if (metal !== m) { metal = m; fitKey = ''; again(ctx); } }}, m)]);
  const fitLine = h('span.cal-fitline');
  const views = VIEWS.map(([id, label]) => [id, h('button', {onclick: () => { if (view !== id) { view = id; again(ctx); } }}, label)]);
  const ctl = h('div.cal-ctl', {hidden: true}, h('div.seg', null, metals.map(([, b]) => b)), fitLine, h('div.seg', null, views.map(([, b]) => b)));

  //returns
  const chart = h('div.cal-fitchart');
  const coef = {};
  for (const [key, label] of [['a', "a (slope)"], ['b', "b (intercept)"], ['c', "c (fine adjustment, reset when applied)"], ['r2', 'R²'], ['n', "points"]]) {
    const value = h('b');
    coef[key] = {row: h('div', null, h('span', null, label), value), value};
  }
  coef.formula = h('div.formula');
  coef.low = h('div.cal-warn', {hidden: true}, "R² is less than 0.995: Please confirm the standard and measurement conditions before application");
  coef.error = h('div.cal-err', {hidden: true});
  const coefBox = h('div.cal-coef', null, coef.a.row, coef.b.row, coef.c.row, coef.r2.row, coef.n.row, coef.formula, coef.low, coef.error);
  const fitNotes = h('div.cal-notes');
  const fitNote = h('button.cal-note', {onclick: () => { if (fitNote.textContent) ctx.dialog.message("Analysis", fitNote.textContent); }});
  const fitView = h('div.cal-view', {hidden: true}, h('div.cal-fit', null, chart, coefBox), fitNotes, fitNote);

  //standard product
  const statRows = h('div.cal-tbody.cal-levelrows');
  const statHint = h('div.cal-hint');
  const statPager = h('div');
  const statView = h('div.cal-view', {hidden: true},
    h('div.cal-thead', null, h('span.t-in', null, "included"), h('span.t-name', null, "standard product"), h('span.t-num', null, "concentration"), h('span.t-n', null, 'n'),
      h('span.t-num', null, "average light intensity"), h('span.t-num', null, 'SD'), h('span.t-num', null, 'RSD %')),
    statRows, statHint, statPager);

  //round
  const pointRows = h('div.cal-tbody.cal-pointrows');
  const pointPager = h('div');
  const pointView = h('div.cal-view', {hidden: true},
    h('div.cal-thead', null, h('span.t-name', null, "round"), h('span.t-num', null, "concentration"), h('span.t-num', null, "Light intensity x"),
      h('span.t-num', null, "regression value"), h('span.t-num', null, "residual")),
    pointRows, pointPager);

  // a record without an analysis: what happened to it
  const msgNotes = h('div.cal-notes');
  const msgNote = h('div.cal-note');
  const msgs = h('div.cal-msgs', {hidden: true}, msgNotes, msgNote);
  const empty = h('div.cal-empty', null, "Select the record on the left");

  const detail = h('div.panel.cal-detail', null, h('div.ph', null, title, sub, h('span.sp'), redo, apply),
    h('div.cal-body', null, ctl, fitView, statView, pointView, msgs, empty));
  return {pane: h('div.cal-pane', {hidden: true}, list, detail), records, recEmpty, recPager, title, sub, reanalyze: redo, apply, metals, fitLine,
    views, ctl, chart, coef, fitNotes, fitNote, fitView, statRows, statHint, statPager, statView, pointRows, pointPager, pointView, msgNotes, msgNote, msgs, empty};
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;
  const badge = h('span.cal-badge', {hidden: true}, '!');
  const tabs = TABS.map(([id, label]) => [id, h('button', {onclick: () => { tab = id; again(ctx); }}, label, id === 'run' ? badge : null)]);
  const active = h('button.cal-active', {onclick: () => showCoefficients(ctx)}, "Current coefficient: —");
  const setup = mountSetup(ctx);
  const run = mountRun(ctx);
  const results = mountResults(ctx);
  root.replaceChildren(
    h('div.cal-top', null, h('h2', null, "standard calibration"), h('div.seg.tabs', null, tabs.map(([, button]) => button)), active),
    setup.pane, run.pane, results.pane);
  el = {tabs, badge, active, setup, run, results};
}

//── render: Calibration settings ────────────────────────────────────────────────────────────────────────────
function makeLevelRow(ctx) {
  const {h} = ctx.dom;
  const cell = (cls, action) => h('button', {class: `cal-cell ${cls}`, onclick: () => { action(levelIndex.get(row)); }});
  const name = cell('name', (index) => editName(ctx, index));
  const metals = METALS.map((m) => cell('num', (index) => editConc(ctx, index, m)));
  const rounds = cell('num', (index) => editRounds(ctx, index));
  const del = h('button.btn.cal-del', {title: "Delete this concentration", onclick: () => { removeLevel(ctx, levelIndex.get(row)); }}, '✕');
  const row = h('div.cal-lvl', null, h('span.c-name', null, name), metals.map((button) => h('span.c-num', null, button)),
    h('span.c-rounds', null, rounds), h('span.c-del', null, del));
  levelParts.set(row, {name, metals, rounds});
  return row;
}

function updateLevelRow(ctx, row, item) {
  const {setText, setClass} = ctx.dom;
  const parts = levelParts.get(row);
  const level = item.level;
  levelIndex.set(row, item.index);
  setText(parts.name, level.name || "Name");
  setClass(parts.name, 'none', !level.name);
  setTitle(parts.name, level.name);
  METALS.forEach((m, i) => {
    const value = level.conc[m];
    setText(parts.metals[i], value === null ? '—' : String(value));
    setClass(parts.metals[i], 'none', value === null);
  });
  setText(parts.rounds, String(level.rounds));
  setClass(row, 'blank', METALS.every((m) => level.conc[m] === 0));
}

function renderSetup(ctx, state) {
  const {dom} = ctx;
  const {setText, setClass} = dom;
  const s = el.setup;
  const rows = plan ? plan.levels.map((level, index) => ({level, index})) : [];
  const pager = dom.pager(s.levelPager, rows.length, LEVEL_ROWS, () => again(ctx));
  if (jumpToLast) {
    jumpToLast = false;
    pager.set(pager.pages - 1);
  }
  dom.list(s.levels, pager.slice(rows), (item) => item.index, () => makeLevelRow(ctx), (row, item) => updateLevelRow(ctx, row, item));
  setDisabled(s.add, !plan);

  const chosen = plan && plan.recipe_id ? recipeRow(plan.recipe_id) : null;
  setText(s.recipe.value, !plan ? '…' : chosen ? chosen.name : "Please select");
  setText(s.recipe.small, !plan ? '' : chosen ? recipeFacts(chosen) : plan.recipe_id && data ? "The originally selected Recipe no longer exists." : "only writes \"one round\", which must contain exactly 1 plasma_run");
  setClass(s.recipe.box, 'need', Boolean(plan) && !chosen);
  setDisabled(s.recipe.box, !plan);
  const primed = plan && plan.prime_recipe_id ? recipeRow(plan.prime_recipe_id) : null;
  setText(s.prime.value, !plan ? '…' : primed ? primed.name : "Not used");
  setText(s.prime.small, !plan ? '' : plan.prime_recipe_id && !primed && data ? "The originally selected Recipe no longer exists." : "Run once every time after pressing \"Confirm Fluid Change\"");
  setDisabled(s.prime.box, !plan);
  setText(s.discard, plan ? String(plan.discard_rounds) : '—');

  const info = check.info;
  const count = rows.length;
  const kept = rows.reduce((total, item) => total + item.level.rounds, 0);
  const values = [plan ? String(count) : '—', plan ? String(kept) : '—', plan ? String(kept + count * plan.discard_rounds) : '—',
    info ? `${Math.round(info.round_delay_seconds || 0)} seconds` : '—',
    !plan ? '—' : !plan.prime_recipe_id ? "Not used" : info && info.prime_recipe_name ? `${Math.round(info.prime_delay_seconds || 0)} seconds per fluid change` : '—',
    info ? durationText(info.estimated_seconds) : '—'];
  s.sum.forEach((item, index) => setText(item.value, values[index]));

  const block = startBlock(ctx, state);
  const messages = [];
  if (!plan && failed.text) messages.push({kind: 'err', text: `Read failed: ${failed.text}`});
  if (plan && block) messages.push({kind: 'err', text: block});
  if (plan && check.error && check.error !== block) messages.push({kind: 'err', text: check.error});
  for (const text of (info && info.warnings) || []) messages.push({kind: 'warn', text: String(text)});
  renderNotes(ctx, s.notes, messages, 2);

  //Off: the boxes above say why (the reason of the program, or what is wrong with the plan); “Under inspection...” is the short
  // moment between an edit and the program's answer.
  setText(s.why, !plan ? (failed.text ? '' : "Reading...") : check.pending ? "Checking..." : '');
  setText(s.start, launching ? "Starting…" : "Start calibration");
  setDisabled(s.start, !plan || Boolean(block) || check.pending || !info || starting);
}

//── render: execute ─────────────────────────────────────────────────────────────────────────────
function promptOf(state) {
  const run = state.calibration_run;
  if (!run) return null;
  const round = state.round;
  return run.prompt || (round && round.mode === 'calibration' ? round.prompt : null) || null;
}

function renderRun(ctx, state, rev) {
  const {dom, labels} = ctx;
  const {setText, setClass} = dom;
  const r = el.run;
  const run = state.calibration_run || null;
  const prompt = promptOf(state);
  if (prompt && r.prompt.hidden) promptAt = performance.now();
  setHidden(r.prompt, !prompt);
  if (prompt) setText(r.promptText, prompt.text || '');
  setText(r.confirm, confirming ? "Confirming..." : "Confirm fluid change");
  setDisabled(r.confirm, confirming);

  const latest = data && data.records && data.records[0] ? data.records[0] : null;
  const id = run ? run.id : latest ? latest.id : '';
  const entry = id ? wantRecord(ctx, id, rev) : null;
  const rec = entry && entry.record && entry.record.id === id ? entry.record : null;

  if (run) {
    const round = state.round;
    const phase = round && round.mode === 'calibration' ? (round.phases || [])[round.phase] : null;
    const word = run.status && run.status !== 'running' ? labels.round[run.status] || '' : '';
    setText(r.title, `Calibration in progress: ${run.recipe_name || ''}`);
    setText(r.sub, [`Round ${run.run || 0} / ${run.runs || 0}`, phase && phase.name ? phase.name : '', word].filter(Boolean).join(' · '));
  } else if (rec) {
    setText(r.title, `Last calibration: ${STATUS[rec.status] || rec.status}`);
    setText(r.sub, [stamp(ctx, rec.created_at), rec.recipe && rec.recipe.name].filter(Boolean).join(' · '));
  } else {
    setText(r.title, !data && !failed.text ? "Reading..." : id ? "latest calibration" : "There are currently no calibrations in progress.");
    setText(r.sub, '');
  }

  // the standards
  const levels = rec ? rec.plan.levels : [];
  const runs = rec ? rec.runs || [] : [];
  const cards = levels.map((level, index) => {
    const done = runs.filter((item) => item.level === index);
    const kept = done.filter((item) => !item.discard).length;
    const complete = kept >= level.rounds;
    return {index, level, complete, now: Boolean(run) && !complete && run.level === index,
      text: complete ? `Complete ${kept} wheel` : `Measurement ${kept} / ${level.rounds} · Pre-run ${done.length - kept} / ${rec.plan.discard_rounds}`};
  });
  setHidden(r.cards, !cards.length);
  dom.list(r.cards, cards, (card) => card.index, () => dom.h('div.cal-card', null, dom.h('b'), dom.h('small'), dom.h('small')), (node, card) => {
    setCls(node, 'cal-card' + (card.complete ? ' done' : card.now ? ' now' : ''));
    setText(node.children[0], card.level.name);
    setText(node.children[1], levelConc(card.level));
    setText(node.children[2], card.text);
    setTitle(node, `${card.level.name} (${levelConc(card.level)} mg/L) `);
  });

  // how the last one ended (while one runs, the footer and the panel title say what it is doing)
  let line = '';
  let bad = false;
  if (!run && rec) {
    const word = STATUS[rec.status] || rec.status;
    if (rec.status === 'analyzed') line = "The analysis has been completed. Please go to \"Results and Records\" to view the regression and apply it.";
    else if (rec.status === 'analysis_failed') line = "analysis failed:" + (rec.error || '');
    else if (rec.error) line = `status: ${word}; ${rec.error}`;
    else line = `Status: ${word}`;
    bad = ['analysis_failed', 'failed', 'interrupted'].includes(rec.status);
  } else if (!run && id && entry && entry.error) {
    line = `Read failed: ${entry.error}`;
    bad = true;
  }
  setHidden(r.stateLine, !line);
  setText(r.stateLine, line);
  setClass(r.stateLine, 'err', bad);
  setTitle(r.stateLine, line);

  // the events of this calibration, newest first
  let rows = [];
  if (rec) {
    wantEvents(ctx, state.revs ? state.revs.events : 0);
    const from = Date.parse(rec.created_at) - 2000;
    const to = run ? Infinity : rec.finished_at ? Date.parse(rec.finished_at) + 60000 : from;
    rows = events.rows.filter((item) => {
      const t = Date.parse(item.time);
      return t >= from && t <= to;
    }).slice(0, EVENT_KEEP);
  }
  setHidden(r.empty, Boolean(id));
  if (!id) setText(r.empty, !data && failed.text ? `Read failed: ${failed.text}` : !data ? "Reading..." : "Click \"Start Calibration\" after setting the standard in \"Calibration Settings\".");
  setHidden(r.log, !id);
  setHidden(r.logPager, !id);
  if (!id) return;
  if (r.log.clientHeight > 0) eventRows = Math.max(1, Math.floor(r.log.clientHeight / EVENT_ROW_PX));
  const pager = dom.pager(r.logPager, rows.length, eventRows, () => again(ctx));
  dom.list(r.log, pager.slice(rows), (item) => `${item.time}|${item.text}`, () => {
    const node = dom.h('div.cal-ev', null, dom.h('time'), dom.h('span'));
    // a line that is cut: a tap opens it (a finger has no tooltip)
    node.addEventListener('click', () => ctx.dialog.message("calibration event", node.children[1].textContent, {lines: [["time", node.children[0].textContent]]}));
    return node;
  }, (node, item) => {
    setCls(node, 'cal-ev' + (item.level === 'error' ? ' error' : item.level === 'warn' ? ' warn' : ''));
    setText(node.children[0], labels.fmtTime(item.time, 'HH:MM:SS'));
    setText(node.children[1], item.text);
    setTitle(node, item.text);
  });
}

//── render: results and records ──────────────────────────────────────────────────────────────────────────
function makeRecordRow(ctx) {
  const {h} = ctx.dom;
  const time = h('b');
  const chip = h('span.cst');
  const counts = h('span');
  const fits = h('small');
  const row = h('button.cal-rec', {onclick: () => select(ctx, recordIds.get(row))}, time, h('small', null, chip, counts), fits);
  recordParts.set(row, {time, chip, counts, fits});
  return row;
}

function updateRecordRow(ctx, row, item, activeId) {
  const {setText, setClass} = ctx.dom;
  const parts = recordParts.get(row);
  recordIds.set(row, item.id);
  const status = item.id === activeId ? 'applied' : item.status;
  setClass(row, 'sel', item.id === selectedId);
  setText(parts.time, stamp(ctx, item.created_at));
  setCls(parts.chip, `cst ${status}`);
  setText(parts.chip, STATUS[status] || status);
  setText(parts.counts, `${item.levels} concentration · ${item.runs} wheel`);
  const fits = Object.entries(item.fits || {}).filter(([, fit]) => fit && fit.valid).map(([m, fit]) => `${m} R² ${fmt(fit.r2, 4)}`).join(' · ');
  setText(parts.fits, fits);
  setTitle(row, fits);
}

function makeStatRow(ctx) {
  const {h} = ctx.dom;
  const tick = h('button.cal-tick', {onclick: () => { toggleLevel(ctx, statFacts.get(row)); }}, '✓');
  const cells = [h('span.t-name'), h('span.t-num'), h('span.t-n'), h('span.t-num'), h('span.t-num'), h('span.t-num')];
  const row = h('div.cal-trow', null, h('span.t-in', null, tick), cells);
  statParts.set(row, {tick, cells});
  return row;
}

function updateStatRow(ctx, row, fact) {
  const {setText, setClass} = ctx.dom;
  const {tick, cells} = statParts.get(row);
  statFacts.set(row, fact);
  setClass(row, 'bad', !fact.included);
  setClass(tick, 'on', fact.included);
  tick.setAttribute('aria-pressed', String(fact.included));
  setTitle(tick, "Uncheck = Regression of this metal does not use this concentration (e.g. detector saturation)");
  setDisabled(tick, !fact.can || Boolean(busyAction));
  const stats = fact.stats;
  setText(cells[0], fact.name);
  setText(cells[1], concText(fact.conc));
  setText(cells[2], stats ? String(stats.n) : '—');
  setText(cells[3], stats ? fmt(stats.mean_intensity, 5) : fact.included ? '—' : "excluded");
  setText(cells[4], stats ? fmt(stats.sd, 5) : '—');
  setText(cells[5], stats && isNumber(stats.rsd_percent) ? stats.rsd_percent.toFixed(2) : '—');
}

function analysisNote(ctx, rec) {
  const analysis = rec.analysis;
  const shifts = (analysis.jobs || []).filter((job) => job && job.shift && job.shift.applied).map((job) => Math.abs(job.shift.p0 + job.shift.p1 * 280));
  return `Analysis method ${analysis.method_id} · ${stamp(ctx, analysis.analyzed_at)} · Blank reference spectrum ${rec.reference ? rec.reference.blank_count : 0} Wheel average`
    + (shifts.length ? `· Maximum displacement ${Math.max(...shifts).toFixed(4)} nm` : '')
    + ". Analysis configuration file: data\\analysis_config.json. After modifying the settings, click \"Reanalyze\" to recalculate using the same batch of original spectra without reprinting the standards.";
}

function renderDetail(ctx, state, rev, activeId) {
  const {dom} = ctx;
  const {setText, setClass} = dom;
  const d = el.results;
  const entry = selectedId ? wantRecord(ctx, selectedId, rev) : null;
  const rec = entry && entry.record && entry.record.id === selectedId ? entry.record : null;
  const parts = [d.ctl, d.fitView, d.statView, d.pointView, d.msgs, d.empty];
  const only = (...keep) => { for (const node of parts) setHidden(node, !keep.includes(node)); };

  if (!rec) {
    setText(d.title, selectedId ? "calibration record" : "Select the record on the left");
    setText(d.sub, '');
    setHidden(d.reanalyze, true);
    setHidden(d.apply, true);
    setText(d.empty, selectedId ? entry.error || "Reading..." : data && !(data.records || []).length ? '' : "Select the record on the left");
    only(d.empty);
    return;
  }

  const applied = activeId === rec.id;
  const status = applied ? 'applied' : rec.status;
  const run = state.calibration_run;
  const running = Boolean(run && run.id === rec.id) || rec.status === 'running';
  const title = `${stamp(ctx, rec.created_at)} · ${(rec.recipe && rec.recipe.name) || ''}`;
  setText(d.title, title);
  setTitle(d.title, title);
  setText(d.sub, (STATUS[status] || status) + (rec.operator ? ` · ${rec.operator}` : ''));
  setHidden(d.reanalyze, running || rec.status === 'analyzing');
  setText(d.reanalyze, busyAction === 'analyze' ? "is being sent out…" : "reanalysis");
  setDisabled(d.reanalyze, Boolean(busyAction));
  setHidden(d.apply, rec.status !== 'analyzed');
  setText(d.apply, busyAction === 'apply' ? "Applying..." : applied ? "Reapply" : "application coefficient");
  setDisabled(d.apply, Boolean(busyAction));

  const messages = [];
  if (rec.error) messages.push({kind: 'err', text: String(rec.error)});
  if (rec.status === 'analyzing') messages.push({kind: 'warn', text: "is calculating light intensity and regression, and will automatically update after completion..."});
  for (const text of rec.warnings || []) messages.push({kind: 'warn', text: String(text)});

  const analysis = rec.analysis;
  if (!analysis || !analysis.fits) {
    const runs = rec.runs || [];
    const kept = runs.filter((item) => !item.discard).length;
    renderNotes(ctx, d.msgNotes, messages, 3);
    setText(d.msgNote, rec.status === 'analyzing' ? '' : `has completed the ${kept} round of measurement (another ${runs.length - kept} round of pre-run). Not analyzed yet.`);
    only(d.msgs);
    return;
  }

  const fits = analysis.fits;
  const fit = fits[metal] || {};
  for (const [m, button] of d.metals) {
    setClass(button, 'sel', m === metal);
    setText(button, m + (fits[m] && fits[m].valid ? '' : ' ✕'));
  }
  for (const [id, button] of d.views) setClass(button, 'sel', id === view);
  setText(d.fitLine, fit.valid ? `R² ${fmt(fit.r2, 5)} · ${fit.n} points` : fit.error || "No regression results\nThe current status of");
  setClass(d.fitLine, 'q-bad', !fit.valid);

  if (view === 'fit') {
    const key = `${rec.id}|${entry.n}|${metal}`;
    if (key !== fitKey) {
      d.chart.replaceChildren(drawFit(ctx, fit));
      fitKey = key;
    }
    const c = d.coef;
    for (const key2 of ['a', 'b', 'c', 'r2', 'n']) setHidden(c[key2].row, !fit.valid);
    setHidden(c.formula, !fit.valid);
    setHidden(c.low, !(fit.valid && isNumber(fit.r2) && fit.r2 < 0.995));
    setHidden(c.error, Boolean(fit.valid));
    if (fit.valid) {
      setText(c.a.value, sig(fit.a, 6));
      setText(c.b.value, sig(fit.b, 6));
      setText(c.c.value, '1');
      setText(c.r2.value, fmt(fit.r2, 5));
      setText(c.n.value, String(fit.n));
      setText(c.formula, `Concentration = (a × light intensity + b) × c Unit ${fit.unit || 'mg/L'}`);
    } else {
      setText(c.error, fit.error || "No regression results\nThe current status of");
    }
    renderNotes(ctx, d.fitNotes, messages, 2);
    setText(d.fitNote, analysisNote(ctx, rec));
    only(d.ctl, d.fitView);
    return;
  }

  if (view === 'levels') {
    const skipped = (rec.exclude_levels || {})[metal] || [];
    const can = rec.status === 'analyzed';
    const rows = rec.plan.levels.map((level, index) => ({level, index}))
      .filter(({level}) => level.conc[metal] !== null && level.conc[metal] !== undefined)
      .map(({level, index}) => ({id: rec.id, metal, index, name: level.name, conc: level.conc[metal], can,
        included: !skipped.includes(index), stats: (fit.levels || []).find((item) => item.level === level.name) || null}));
    const pager = dom.pager(d.statPager, rows.length, STAT_ROWS, () => again(ctx));
    dom.list(d.statRows, pager.slice(rows), (fact) => `${fact.metal}:${fact.index}`, () => makeStatRow(ctx), (row, fact) => updateStatRow(ctx, row, fact));
    setText(d.statHint, can ? "Uncheck = Regression of this metal does not use this concentration (e.g. detector saturation)" : `is " ${STATUS[rec.status] || rec.status} ": only the "analyzed" records can be adjusted to include the concentration.`);
    setClass(d.statHint, 'why', !can);
    only(d.ctl, d.statView);
    return;
  }

  const points = (fit.points || []).map((p, index) => ({key: `p${index}`, bad: false,
    cells: [`${p.level} #${p.round}`, concText(p.y), fmt(p.x, 5), fmt(p.fitted, 4), fmt(p.residual, 4)]}));
  const excluded = (fit.excluded || []).map((p, index) => ({key: `x${index}`, bad: true,
    cells: [`${p.level} #${p.round === null || p.round === undefined ? '' : p.round}`, "excluded", p.reason || '']}));
  const rows = [...points, ...excluded];
  const pager = dom.pager(d.pointPager, rows.length, POINT_ROWS, () => again(ctx));
  dom.list(d.pointRows, pager.slice(rows), (row) => `${metal}:${row.key}`, (row) => {
    const node = dom.h('div', null,
      row.bad ? [dom.h('span.t-name'), dom.h('span.t-num'), dom.h('span.t-wide')]
        : [dom.h('span.t-name'), dom.h('span.t-num'), dom.h('span.t-num'), dom.h('span.t-num'), dom.h('span.t-num')]);
    // why a round was left out may be longer than its cell: a tap opens it
    if (row.bad) node.addEventListener('click', () => ctx.dialog.message("excluded rounds", node.children[2].textContent, {lines: [["round", node.children[0].textContent]]}));
    return node;
  }, (node, row) => {
    setCls(node, 'cal-trow' + (row.bad ? ' bad' : ''));
    row.cells.forEach((text, index) => setText(node.children[index], text));
    if (row.bad) setTitle(node, row.cells[2]);
  });
  only(d.ctl, d.pointView);
}

function renderResults(ctx, state, rev) {
  const {dom} = ctx;
  const d = el.results;
  const rows = data ? data.records || [] : [];
  if (!selectedId && rows[0]) selectedId = rows[0].id;
  const activeId = data && data.active ? data.active.calibration_id : '';
  const pager = dom.pager(d.recPager, rows.length, RECORD_ROWS, () => again(ctx));
  setHidden(d.records, !rows.length);
  setHidden(d.recEmpty, rows.length > 0);
  dom.setText(d.recEmpty, data ? "has no calibration record yet" : failed.text ? `Read failed: ${failed.text}` : "Reading...");
  dom.list(d.records, pager.slice(rows), (row) => row.id, () => makeRecordRow(ctx), (row, item) => updateRecordRow(ctx, row, item, activeId));
  renderDetail(ctx, state, rev, activeId);
}

// ── render ──────────────────────────────────────────────────────────────────────────────────────────────────
export function render(state, ctx) {
  if (!el) return;
  const {setText, setClass} = ctx.dom;
  const now = Date.now();
  const revs = state.revs || {};
  const rev = revs.calibrations || 0;
  const run = state.calibration_run || null;
  const want = `${rev}|${revs.recipes || 0}`;
  const old = data === null || dataRevs !== want || (run && now - dataAt >= RUN_POLL_MS);
  if (old && !loading && !ctx.store.stale && now - failed.at >= RETRY_MS) loadData(ctx, want);
  wantRecipes(ctx, revs.recipes || 0);

  //a prompt brings the execute tab forward, once
  const prompt = promptOf(state);
  const key = prompt ? String(prompt.since || prompt.text || '1') : '';
  if (key && key !== promptKey) tab = 'run';
  promptKey = key;

  // only the record on screen and the one that runs are kept
  const latest = data && data.records && data.records[0] ? data.records[0].id : '';
  const keep = new Set([selectedId, run ? run.id : latest]);
  for (const id of [...cache.keys()]) if (!keep.has(id)) cache.delete(id);

  for (const [id, button] of el.tabs) setClass(button, 'sel', id === tab);
  setHidden(el.badge, !prompt);
  let line = "Current coefficient: —";
  if (data) {
    const active = data.active;
    line = (active ? `Current coefficient (${ctx.labels.fmtTime(active.applied_at, 'YYYY/MM/DD')} correction):` : "Current coefficient (uncorrected page):")
      + coefficientsText(data.coefficients, 4).join('; ');
  } else if (failed.text) {
    line = `Read failed: ${failed.text}`;
  }
  setText(el.active, line);
  setTitle(el.active, line);
  setClass(el.active, 'err', !data && Boolean(failed.text));
  setDisabled(el.active, !data);

  setHidden(el.setup.pane, tab !== 'setup');
  setHidden(el.run.pane, tab !== 'run');
  setHidden(el.results.pane, tab !== 'results');
  if (tab === 'setup') renderSetup(ctx, state);
  else if (tab === 'run') renderRun(ctx, state, rev);
  else renderResults(ctx, state, rev);
}

export function show(ctx, arg) {
  shown = true;
  if (typeof arg === 'string' && TABS.some(([id]) => id === arg)) tab = arg;
  else if (arg && typeof arg === 'object') {
    if (TABS.some(([id]) => id === arg.tab)) tab = arg.tab;
    if (typeof arg.id === 'string' && arg.id) { selectedId = arg.id; tab = 'results'; }
  }
  dataRevs = '';                 // what was read before the page was left is old now
  promptKey = '';                //a prompt that is waiting brings the execute tab forward again
  fitKey = '';
  if (plan) queueCheck(ctx, 0);
  if (ctx.store.state) render(ctx.store.state, ctx);
}

export function hide(ctx) {
  shown = false;
  if (dirty) savePlan(ctx);      // the draft is kept without waiting for the 1.2 s
}
