//trend.js — Trends (AMENDMENTS M5.5; ported from the original HMI's web/trend/app.js).
//
// Three stacked charts, one per metal. Samples are a blue line with dots, blanks an amber dashed line with diamonds
// (a blank that failed its baseline check is a bigger, filled magenta diamond); the shaded band is the blank
// threshold. A tap on the chart picks the nearest point: the line at the bottom says what it is.
// Data: GET /api/measurements?hours=N, read again when state.revs.measurements changes and every 60 s while the page
// is on screen. The mode and the range are kept in this browser (localStorage['plasmic.trend']).
//"Blank Threshold/Zero Point" may be read by everybody; saving needs the maintenance session.

const METALS = [['Cu', "Copper Cu"], ['Zn', "Zinc Zn"], ['Ni', "Nickel Ni"]];
const MODES = [['conc', "concentration", ' (mg/L) '], ['raw', "Zero point not deducted", "(mg/L, zero point not deducted)"], ['intensity', "light intensity", "(light intensity)"]];
const RANGES = [[1, "1 hour"], [8, "8 hours"], [24, "24 hours"], [168, "7 days"], [720, "30 days"], [0, "All"]];
const STORE_KEY = 'plasmic.trend';
const RELOAD_MS = 60000;
const RETRY_MS = 5000;
const PICK_PX = 32;              // a tap this close to a point picks it (a finger: 32 px of the frame are about 8 mm)
const HINT = "Click the data point to view details";
const NEED_PIN = "needs maintenance PIN: Please log in first at \"🔧 Maintenance\" on the left";
const NO_CONC = "There is no concentration data for this period (the calibration has not been applied yet, or the analysis settings are different from the calibration). You can switch \"Light Intensity\" to view the original trend.";
const LIMIT_HINT = "Blank threshold: If any item exceeds, an alarm will be issued (scheduling will not be stopped). Compare to the blank in the currently applied correction.";
const ZERO_HINT = "Blank zero point deduction (sample concentration − median of recent blanks): Only the metals that are \"deducted\" are selected; all blanks are included (whether QC is qualified or not does not affect it), and blanks that are more than 3×MAD from the median are not counted."
  + "will not be deducted until the minimum number of times has been accumulated. Only affects subsequent new measurements.";

let el = null;
let page = null;                 // the ctx of this module
let shown = false;
let view = {hours: 24, mode: 'conc'};
let data = null;                 // reply of GET /api/measurements
let got = {rev: null, hours: null, at: 0, n: 0};   // what `data` answers; n grows with every reply
let loading = false;
let failed = {at: 0, text: ''};
let drawnKey = '';               // what the charts show now
let picked = null;               // {metal, key}: the point of the detail line
let detail = '';                 // the text of that point
let pressing = false;            // a finger is on the charts: they are drawn again only after it is up
let saving = false;

const two = (n) => String(n).padStart(2, '0');
const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
// the original's number format: 1 decimal from 100, 3 decimals from 1, else 3 significant digits
const fmt = (v) => (isNumber(v) ? (Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 1 ? v.toFixed(3) : Number(v.toPrecision(3)).toString()) : '—');
const hoursText = (hours) => (!hours ? "All information" : hours % 24 === 0 && hours >= 48 ? `recent ${hours / 24} days` : `Last ${hours} hours`);
const keyOf = (row) => String(row.id || row.time);

function setHidden(node, hidden) {
  const value = Boolean(hidden);
  if (node.hidden !== value) node.hidden = value;
}

function setDisabled(node, disabled) {
  const value = Boolean(disabled);
  if (node.disabled !== value) node.disabled = value;
}

function again() {
  if (shown && el && page && page.store.state) render(page.store.state, page);
}

// ── what this browser remembers ─────────────────────────────────────────────────────────────────────────────
function readView() {
  let raw = null;
  try { raw = JSON.parse(window.localStorage.getItem(STORE_KEY) || 'null'); } catch (_) { raw = null; }
  const source = raw && typeof raw === 'object' ? raw : {};
  return {hours: RANGES.some(([hours]) => hours === source.hours) ? source.hours : 24,
    mode: MODES.some(([id]) => id === source.mode) ? source.mode : 'conc'};
}

function saveView() {
  try { window.localStorage.setItem(STORE_KEY, JSON.stringify(view)); } catch (_) { /* the choice then lasts as long as the page */ }
}

// ── data ────────────────────────────────────────────────────────────────────────────────────────────────────
async function load(ctx, rev) {
  if (loading) return;
  loading = true;
  const hours = view.hours;
  try {
    data = await ctx.api.get(hours ? `/api/measurements?hours=${hours}` : '/api/measurements', {timeout: 15000});
    got = {rev, hours, at: Date.now(), n: got.n + 1};
    failed = {at: 0, text: ''};
  } catch (error) {
    failed = {at: Date.now(), text: error.message};
  }
  loading = false;
  again();
}

function valueOf(row, metal) {
  const item = row.metals && row.metals[metal];
  if (!item) return null;
  const v = view.mode === 'conc' ? item.conc : view.mode === 'raw' ? (item.conc_raw !== undefined && item.conc_raw !== null ? item.conc_raw : item.conc) : item.intensity;
  return isNumber(v) ? v : null;
}

function detailText(ctx, row, metal, value) {
  const kind = row.kind === 'blank' ? "blank" : "sample";
  const unit = view.mode === 'intensity' ? "(light intensity)" : ' mg/L';
  const item = (row.metals || {})[metal] || {};
  // only where the number shown is the one the zero was taken from
  const zero = view.mode === 'conc' && isNumber(item.zero) ? `· Zero point deducted ${fmt(item.zero)} (original ${fmt(item.conc_raw)})` : '';
  const check = row.qc || null;
  const qc = !check ? '' : check.status === 'fail' ? ' · ⚠ ' + (check.problems || []).join('; ')
    : check.status === 'ok' ? "· Baseline normal" : check.reason ? ' · ' + check.reason : '';
  const shift = isNumber(row.shift_nm) ? `· Displacement ${row.shift_nm.toFixed(3)} nm` : '';
  return `${ctx.labels.fmtTime(row.time, 'YYYY-MM-DD HH:MM:SS')} · ${kind} · ${metal} ${fmt(value)}${unit}${zero}${shift}${qc}${row.recipe_name ? ' · ' + row.recipe_name : ''}`;
}

// ── one chart ───────────────────────────────────────────────────────────────────────────────────────────────
function drawChart(ctx, item, rows, t0, t1) {
  const {h} = ctx.dom;
  const width = item.box.clientWidth;
  const height = item.box.clientHeight;
  if (width < 120 || height < 70) return false;        // not laid out yet
  const mode = MODES.find(([id]) => id === view.mode);
  const root = h('svg', {viewBox: `0 0 ${width} ${height}`});
  const L = 58;
  const R = width - 12;
  const T = 28;
  const B = height - 20;
  root.append(h('svg:text.tr-title', {x: 8, y: 15}, item.label + mode[2]));
  item.svg = root;
  item.width = width;
  item.points = [];
  item.ring = null;
  const points = data ? rows.map((row) => ({row, t: Date.parse(row.time), v: valueOf(row, item.metal)})).filter((p) => p.v !== null && !Number.isNaN(p.t)) : [];
  if (!points.length) {
    root.append(h('svg:text.tr-empty', {x: (L + R) / 2, y: (T + B) / 2, 'text-anchor': 'middle'},
      data ? "There is no data during this period" : failed.text ? "read failed" : "Reading..."));
    item.box.replaceChildren(root);
    return true;
  }
  const qc = data.qc && data.qc.max_apparent_conc ? data.qc.max_apparent_conc[item.metal] : null;
  const limit = view.mode !== 'intensity' && isNumber(qc) && qc > 0 ? qc : null;
  let lo = Math.min(...points.map((p) => p.v));
  let hi = Math.max(...points.map((p) => p.v));
  if (view.mode !== 'intensity') {
    lo = Math.min(lo, 0, limit ? -limit : 0);
    hi = Math.max(hi, limit || 0);
  }
  const span = hi - lo || Math.abs(hi) || 1;
  lo -= span * 0.08;
  hi += span * 0.08;
  const sx = (t) => L + (t - t0) / Math.max(1, t1 - t0) * (R - L);
  const sy = (v) => B - (v - lo) / (hi - lo) * (B - T);
  root.append(h('svg:rect.tr-plot', {x: L, y: T, width: R - L, height: B - T}));
  for (let i = 0; i <= 4; i += 1) {
    const v = lo + (hi - lo) * i / 4;
    root.append(h('svg:line.tr-grid', {x1: L, x2: R, y1: sy(v), y2: sy(v)}),
      h('svg:text', {x: L - 4, y: sy(v) + 4, 'text-anchor': 'end'}, fmt(v)));
  }
  if (limit) {
    root.append(h('svg:rect.tr-band', {x: L, y: sy(limit), width: R - L, height: Math.max(1, sy(-limit) - sy(limit))}),
      h('svg:text', {x: R - 4, y: sy(limit) - 3, 'text-anchor': 'end'}, `blank threshold ± ${limit}`));
  }
  if (view.mode !== 'intensity' && lo < 0 && hi > 0) root.append(h('svg:line.tr-zero', {x1: L, x2: R, y1: sy(0), y2: sy(0)}));
  const long = t1 - t0 > 36 * 3600e3;
  for (let i = 0; i <= 5; i += 1) {
    const t = t0 + (t1 - t0) * i / 5;
    const d = new Date(t);
    root.append(h('svg:text', {x: sx(t), y: B + 14, 'text-anchor': i === 0 ? 'start' : i === 5 ? 'end' : 'middle'},
      long ? `${d.getMonth() + 1}/${d.getDate()} ${two(d.getHours())}h` : `${two(d.getHours())}:${two(d.getMinutes())}`));
  }
  for (const kind of ['sample', 'blank']) {
    const series = points.filter((p) => (p.row.kind === 'blank' ? 'blank' : 'sample') === kind);
    if (series.length > 1) root.append(h('svg:polyline', {class: `tr-line ${kind}`, points: series.map((p) => `${sx(p.t).toFixed(1)},${sy(p.v).toFixed(1)}`).join(' ')}));
    for (const p of series) {
      const x = sx(p.t);
      const y = sy(p.v);
      if (kind === 'blank') {
        const bad = Boolean(p.row.qc && p.row.qc.status === 'fail');
        const r = bad ? 6 : 4.5;
        root.append(h('svg:rect', {class: 'tr-dia' + (bad ? ' bad' : ''), x: x - r, y: y - r, width: 2 * r, height: 2 * r, transform: `rotate(45 ${x} ${y})`}));
      } else {
        root.append(h('svg:circle.tr-dot', {cx: x, cy: y, r: 3.5}));
      }
      item.points.push({x, y, v: p.v, row: p.row});
    }
  }
  item.box.replaceChildren(root);
  return true;
}

// The ring around the picked point and the text of the detail line; a point that is no longer on screen is let go.
function showPick(ctx) {
  for (const item of el.charts) {
    if (item.ring) { item.ring.remove(); item.ring = null; }
  }
  if (!picked) { detail = ''; return; }
  const item = el.charts.find((one) => one.metal === picked.metal);
  const point = item ? item.points.find((p) => keyOf(p.row) === picked.key) : null;
  if (!point) { picked = null; detail = ''; return; }
  item.ring = ctx.dom.h('svg:circle.tr-pick', {cx: point.x, cy: point.y, r: 9});
  item.svg.append(item.ring);
  detail = detailText(ctx, point.row, picked.metal, point.v);
}

function pick(ctx, item, event) {
  if (!item.svg || !item.points.length) return;
  const rect = item.svg.getBoundingClientRect();
  if (!rect.width) return;
  const k = item.width / rect.width;                   // the frame is scaled as a whole: back to the chart's own pixels
  const x = (event.clientX - rect.left) * k;
  const y = (event.clientY - rect.top) * k;
  let best = null;
  let nearest = PICK_PX * PICK_PX;
  for (const point of item.points) {
    const d = (point.x - x) ** 2 + (point.y - y) ** 2;
    if (d <= nearest) { best = point; nearest = d; }
  }
  if (!best) return;
  picked = {metal: item.metal, key: keyOf(best.row)};
  showPick(ctx);
  again();
}

function drawAll(ctx, now) {
  const rows = data ? data.rows || [] : [];
  const times = rows.map((row) => Date.parse(row.time)).filter((t) => !Number.isNaN(t));
  const hours = view.hours;
  const t1 = hours ? now : Math.max(now, ...times);
  const t0 = hours ? now - hours * 3600e3 : Math.min(...times, now - 3600e3);
  let all = true;
  for (const item of el.charts) all = drawChart(ctx, item, rows, t0, t1) && all;
  showPick(ctx);
  return all;
}

//── "Display range..." and "Blank threshold/zero point" ────────────────────────────────────────────────────────────
async function editRange(ctx) {
  const hours = await ctx.dialog.choose({title: "display range", value: view.hours,
    choices: RANGES.map(([value, label]) => ({value, label}))});
  if (hours === null || hours === view.hours) return;
  view = {...view, hours};
  saveView();
  again();
}

function setMode(mode) {
  if (mode === view.mode) return;
  view = {...view, mode};
  saveView();
  again();
}

function zeroLabel(metal, settings, status) {
  const s = status ? status[metal] : null;
  if (!s) return `${metal} Zero point · No correction has been applied yet`;
  const used = `${s.n} times ${s.rejected ? `, eliminate ${s.rejected}` : ''}`;
  return s.ready ? `${metal} Zero point · Current ${fmt(s.zero)} mg/L ( ${used} )` : `${metal} Zero point · Insufficient blank (${s.n} / ${settings.min_blanks})`;
}

async function editBlank(ctx) {
  if (!data || !data.qc || saving) return;
  const qc = data.qc;
  const limits = qc.max_apparent_conc || {};
  const zero = (data.blank_zero && data.blank_zero.settings) || {enabled: {}, window: 10, min_blanks: 3, mad_reject: true};
  const status = (data.blank_zero && data.blank_zero.status) || null;
  // Eleven lines fill the dialog: what a line means is said in the dialog that the line opens.
  const limit = {detail: LIMIT_HINT};
  const sub = {detail: ZERO_HINT};
  const fields = [
    {key: 'shift', label: "upper limit of spectral shift (nm)", kind: 'pad', min: 0.001, max: 1, decimals: 3, value: qc.max_shift_nm, ...limit},
    ...METALS.map(([metal]) => ({key: `conc_${metal}`, label: `${metal} Apparent upper limit of concentration (± mg/L)`, kind: 'pad', min: 0, max: 1000, decimals: 3, value: limits[metal], ...limit})),
    {key: 'norm', label: "Normalized area change upper limit (± %)", kind: 'pad', min: 1, max: 100, decimals: 1, value: qc.max_norm_change_percent, ...limit},
    ...METALS.map(([metal]) => ({key: `zero_${metal}`, label: zeroLabel(metal, zero, status), title: `${metal} Blank zero point deduction`, kind: 'choose',
      value: Boolean(zero.enabled && zero.enabled[metal]), choices: [{value: true, label: "Deduction"}, {value: false, label: "No deduction"}], ...sub})),
    {key: 'window', label: "takes the last few blanks", kind: 'pad', min: 3, max: 30, decimals: 0, value: zero.window, ...sub},
    {key: 'min', label: "must be blank for at least several times before starting to deduct", kind: 'pad', min: 1, max: 30, decimals: 0, value: zero.min_blanks, ...sub},
    {key: 'mad', label: "remove outlier blanks (3×MAD)", kind: 'choose', value: Boolean(zero.mad_reject),
      choices: [{value: true, label: "eliminated"}, {value: false, label: "not eliminated"}], ...sub},
  ];
  const values = await ctx.dialog.form({title: "blank threshold and zero point", wide: true, timeout: 120, confirmLabel: "storage", fields,
    warn: ctx.unlocked() ? null : NEED_PIN});
  if (values === null) return;
  if (!ctx.unlocked()) {
    await ctx.dialog.message("blank threshold and zero point", NEED_PIN, {level: 'error'});
    return;
  }
  const next = {max_shift_nm: values.shift, max_norm_change_percent: values.norm,
    max_apparent_conc: Object.fromEntries(METALS.map(([metal]) => [metal, values[`conc_${metal}`]]))};
  const settings = {enabled: Object.fromEntries(METALS.map(([metal]) => [metal, values[`zero_${metal}`] === true])),
    window: values.window, min_blanks: values.min, mad_reject: values.mad === true};
  saving = true;
  again();
  try {
    await ctx.api.post('/api/blank-qc', {qc: next});
    await ctx.api.post('/api/blank-zero', {settings});
    ctx.message("Blank threshold and zero point have been stored");
  } catch (error) {
    if (error.status === 403 && error.code === 'locked') await ctx.dialog.message("blank threshold and zero point", NEED_PIN, {level: 'error'});
    else ctx.message("Storage failed:" + error.message, 'error');
  }
  saving = false;
  got = {...got, rev: null};           // read the settings back
  again();
}

// ── mount / render ──────────────────────────────────────────────────────────────────────────────────────────
export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;
  view = readView();
  const modes = MODES.map(([id, label]) => [id, h('button', {onclick: () => setMode(id), title: id === 'raw' ? "Concentration without blank zero point deduction" : null}, label)]);
  const range = h('button.btn', {onclick: () => { editRange(ctx); }}, "Display range…");
  const chip = h('span.tr-chip');
  const notice = h('div.tr-notice', {hidden: true}, NO_CONC);
  const charts = METALS.map(([metal, label]) => {
    const item = {metal, label, box: h('div.tr-chart'), svg: null, width: 0, points: [], ring: null};
    item.box.addEventListener('click', (event) => pick(ctx, item, event));
    return item;
  });
  const chartsBox = h('div.tr-charts', null, charts.map((item) => item.box));
  chartsBox.addEventListener('pointerdown', () => { pressing = true; }, true);
  const release = () => {
    if (!pressing) return;
    pressing = false;
    setTimeout(again, 0);                // after the click that belongs to this press
  };
  window.addEventListener('pointerup', release, true);
  window.addEventListener('pointercancel', release, true);
  const line = h('button.tr-detail', {onclick: () => { if (detail) ctx.dialog.message("data point", detail.split(' · ').join('\n')); }}, HINT);
  const blank = h('button.btn', {onclick: () => { editBlank(ctx); }}, "blank threshold/zero point");
  root.replaceChildren(
    h('div.tr-bar', null, h('h2', null, "concentration trend"), h('div.seg', null, modes.map(([, button]) => button)), range, chip, h('span.sp'),
      h('span.tr-legend', null, h('i.lg-sample'), "sample", h('i.lg-blank'), "blank", h('i.lg-bad'), "Blank exceeds standard")),
    notice, chartsBox,
    h('div.tr-foot', null, line, blank));
  el = {modes, chip, notice, charts, line, blank};
}

export function render(state, ctx) {
  if (!el) return;
  const {setText, setClass} = ctx.dom;
  const now = Date.now();
  const rev = state.revs ? state.revs.measurements : 0;
  const old = data === null || got.rev !== rev || got.hours !== view.hours || now - got.at >= RELOAD_MS;
  if (old && !loading && !ctx.store.stale && now - failed.at >= RETRY_MS) load(ctx, rev);

  for (const [id, button] of el.modes) setClass(button, 'sel', id === view.mode);
  const range = hoursText(view.hours);
  setText(el.chip, range);
  if (el.chip.title !== `Currently showing: ${range}`) el.chip.title = `Currently showing: ${range}`;

  const rows = data ? data.rows || [] : [];
  const hasConc = rows.some((row) => METALS.some(([metal]) => row.metals && row.metals[metal] && isNumber(row.metals[metal].conc)));
  setHidden(el.notice, !(view.mode !== 'intensity' && rows.length > 0 && !hasConc));

  // The charts are drawn again only when what they show has changed (new data, another mode or range, another size).
  const first = el.charts[0].box;
  const key = `${got.n}|${data ? 1 : failed.text ? 2 : 0}|${view.mode}|${view.hours}|${first.clientWidth}x${first.clientHeight}`;
  if (key !== drawnKey && !pressing && drawAll(ctx, now)) drawnKey = key;

  const text = detail || (failed.text ? `Read failed: ${failed.text}` : data ? HINT : "Reading...");
  setText(el.line, text);
  setClass(el.line, 'err', !detail && Boolean(failed.text));
  setDisabled(el.line, !detail);
  //The settings come with the data: until it has arrived the line beside the button says why (Reading... / Failed to read).
  setDisabled(el.blank, !data || saving);
}

export function show(ctx) {
  shown = true;
  drawnKey = '';
  got = {...got, at: 0};               // what was read before the page was left is old now
  if (ctx.store.state) render(ctx.store.state, ctx);
}

export function hide() {
  shown = false;
  pressing = false;
}
