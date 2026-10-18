// frame.js — what is on screen on every page (SPEC E.2): top bar, alarm banner, navigation rail, footer;
// the operator picker; the start and resume dialogs of a schedule (E.5.3); the preview of the schedule form;
// the page's own alarm ui_stale; full screen (the layer at the start and the button of the top bar).
//
// Used by main.js only:  mount(hooks) · render() · tick() · message(text, level) · decorate(state, stale) · pickOperator()
// refreshPreview().  Pages reach the footer message through ctx.message(text, level).

import * as api from './api.js';
import { store } from './store.js';
import * as dialog from './dialog.js';
import { h, setText, setClass } from './dom.js';
import * as labels from './labels.js';
import { checkNow } from './check.js';

const ACTIVE = ['waiting', 'running', 'pausing', 'paused', 'stopping'];
const ROUND_ACTIVE = ['running', 'pausing', 'paused', 'stopping'];
const FRESH_MS = 12000;        // an “info” line leaves the footer after 12 s; what the person just did shows for 12 s
const ERROR_MS = 30000;        // ... and for 30 s when it failed (while no schedule is running)
const PREVIEW_MS = 10000;      // the preview is asked again every 10 s while no schedule is active
const STALE_TEXT = "The program did not respond: the data on the screen has expired and the actual status of the device is unknown.";
const STALE_SHORT = "The program did not respond";
const NO_ALARMS = Object.freeze({worst: null, unacked: 0, counts: {p1: 0, p2: 0, p3: 0}, active: []});

let el = null;
let hooks = {go() {}, pollNow() {}, republish() {}, maintLeft: () => 0, unlocked: () => false, page: () => 'dash', gate: true};
let note = null;               // the page's own footer line: {text, level, at}
const chips = {rpi: '', pump: ''};   // what a tap on the two status words of the top bar says
let staleInfo = {since: 0, acked: false};
let previewKey = '';
let previewAt = 0;
let previewTurn = 0;
let starting = false;
let shownAlarm = null;
let shownMessage = null;
let shownReasons = [];

const $ = (id) => document.getElementById(id);
const parse = (iso) => { const ms = Date.parse(iso); return Number.isNaN(ms) ? 0 : ms; };
const two = (n) => String(n).padStart(2, '0');

function setAttr(node, name, value) {
  const text = value === null || value === undefined ? '' : String(value);
  if ((node.getAttribute(name) || '') === text) return;
  if (text) node.setAttribute(name, text);
  else node.removeAttribute(name);
}

function setQ(node, cls) {
  for (const name of ['q-ok', 'q-na', 'q-bad', 'q-run']) setClass(node, name, name === cls);
}

function setHidden(node, hidden) {
  if (node.hidden !== hidden) node.hidden = hidden;
}

function setDisabled(node, disabled) {
  if (node.disabled !== disabled) node.disabled = disabled;
}

// ── the page's own alarm: no fresh /api/state for 5 s ───────────────────────────────────────────────────────
function withStale(alarms) {
  const base = alarms || NO_ALARMS;
  const own = {id: 'ui_stale', key: 'ui_stale', priority: 1, area: "screen", text: STALE_TEXT,
    since: new Date(staleInfo.since).toISOString(), acked: staleInfo.acked, cleared: false};
  const active = [own, ...(base.active || []).filter((item) => item.id !== 'ui_stale')]
    .sort((a, b) => (Number(a.acked) - Number(b.acked)) || (a.priority - b.priority) || (parse(b.since) - parse(a.since)));
  const open = active.filter((item) => !item.acked);
  const counts = {...{p1: 0, p2: 0, p3: 0}, ...(base.counts || {})};
  counts.p1 += 1;
  return {...base, active, counts, unacked: open.length, worst: open.length ? Math.min(...open.map((item) => item.priority)) : null};
}

// main.js passes every state through here before the pages see it: while stale, state.alarms also holds ui_stale
//(id 'ui_stale'), so the banner, the nav badge and the alert page show the same list.
export function decorate(state, stale) {
  if (!stale) {
    staleInfo = {since: 0, acked: false};
    return state;
  }
  if (!staleInfo.since) staleInfo = {since: Date.now(), acked: false};
  return state ? {...state, alarms: withStale(state.alarms)} : state;
}

function alarmsNow() {
  if (store.state) return store.state.alarms || NO_ALARMS;
  return store.stale && staleInfo.since ? withStale(null) : NO_ALARMS;
}

// Acknowledging ui_stale never goes to the program (it is not answering): it is ticked off here.
api.intercept((method, path, body) => {
  if (path !== '/api/alarms/ack' || !staleInfo.since || !body) return undefined;
  const ids = Array.isArray(body.ids) ? body.ids : [];
  if (!body.all && !ids.includes('ui_stale')) return undefined;
  staleInfo.acked = true;
  hooks.republish();
  if (body.all) return undefined;
  const rest = ids.filter((id) => id !== 'ui_stale');
  return rest.length ? {body: {...body, ids: rest}} : {reply: {acked: 1}};
});

// ── footer message ──────────────────────────────────────────────────────────────────────────────────────────
function levelOf(level) {
  if (level === 'error' || level === 'err') return 'error';
  return level === 'warn' ? 'warn' : 'info';
}

// The line of the page itself (the result of what the person just did). It is shown for 12 s in front of whatever
// the footer shows otherwise (the reason why a schedule cannot start, the cells of a running schedule); a failure stays
// in front for 30 s while no schedule runs. After that an error or a warning is the footer message until the next line,
// as far as the footer has nothing else to say.
export function message(text, level) {
  note = text ? {text: String(text), level: levelOf(level), at: Date.now()} : null;
  if (el) renderFoot(store.state, store.stale, Date.now());
}

function footerLines(state) {
  const list = [];
  if (note) list.push({...note, mine: true});
  if (state && state.message && state.message.text) list.push({text: String(state.message.text), level: levelOf(state.message.level), at: parse(state.message.time), mine: false});
  const result = state && state.pump && state.pump.last_result;
  if (result && result.text) list.push({text: String(result.text), level: result.ok === false ? 'error' : 'info', at: parse(result.time), mine: true});
  return list.sort((a, b) => b.at - a.at);
}

// ── schedule form -> request, preview ───────────────────────────────────────────────────────────────────────
function requestOf(form) {
  const start = form.start.mode === 'at' ? {mode: 'at', time: form.start.time} : {mode: 'now'};
  const end = form.end.mode === 'count' ? {mode: 'count', count: form.end.count}
    : form.end.mode === 'until' ? {mode: 'until', time: form.end.time} : {mode: 'continuous'};
  const blank = form.blank && form.blank.enabled && form.blank.recipe_id ? {recipe_id: form.blank.recipe_id, every: form.blank.every} : null;
  return {recipe_id: form.recipe_id, schedule: {start, interval_minutes: form.interval_minutes, end}, blank};
}

const NO_PREVIEW = {data: null, error: '', reasons: [], pending: false};

// Asks the program what the form means now (first slot, count, estimate, blockers, which tick rows). store.preview
// follows: {data, error, reasons, pending}.
export async function refreshPreview() {
  const form = store.form;
  if (!form.recipe_id) {
    previewKey = '';
    previewTurn += 1;
    const p = store.preview;
    if (p.data || p.error || p.pending) store.apply({preview: {...NO_PREVIEW}});
    return;
  }
  if (!store.boot || store.stale) return;
  const body = requestOf(form);
  const key = JSON.stringify(body);
  const turn = ++previewTurn;
  previewAt = Date.now();
  if (key !== previewKey) {
    previewKey = key;                                // another form: the old answer does not describe it
    store.apply({preview: {...NO_PREVIEW, pending: true}});
  }
  try {
    const data = await api.post('/api/schedule/preview', body, {timeout: 8000});
    if (turn === previewTurn) store.apply({preview: {data, error: '', reasons: [], pending: false}});
  } catch (error) {
    if (turn !== previewTurn) return;
    const text = error.status === 404 ? "The selected process no longer exists: please go to \"Schedule Measurement\" and select again." : error.message;
    store.apply({preview: {data: null, error: text, reasons: error.reasons || [], pending: false}});
  }
}

function previewIsOld() {
  return Boolean(store.form.recipe_id) && JSON.stringify(requestOf(store.form)) !== previewKey;
}

//Why “Start Schedule” cannot be pressed now, first match first; empty = it can.
function startReasons(state, stale) {
  if (stale) return [{text: "The program did not respond", fix: ''}];
  if (!state) return [{text: "Schedule setting is being calculated...", fix: ''}];          // the first second after the page opened
  if (!store.form.recipe_id) return [{text: "Please go to the \"Scheduled Measurement\" selection process first", fix: ''}];
  const preview = store.preview;
  if (preview.error) {
    const more = (preview.reasons || []).filter((item) => item && item.text && item.text !== preview.error);
    return [{text: preview.error, fix: ''}, ...more];
  }
  if (!preview.data) return [{text: "Schedule setting is being calculated...", fix: ''}];
  const seen = new Set();
  const list = [];
  const live = (state.interlock && state.interlock.start && state.interlock.start.reasons) || [];
  for (const item of [...live, ...(preview.data.blockers || [])]) {
    if (!item || !item.text || seen.has(item.text)) continue;
    seen.add(item.text);
    list.push({text: item.text, fix: item.fix || '', code: item.code || ''});
  }
  return list;
}

//"Recheck" (AMENDMENTS M18) stands beside the reason when the reason is about what the RPi reads of the equipment:
// the person has just switched something at the rig and wants to know now whether the program sees it.
const CHECKABLE = ['rpi_not_ready', 'hv_unknown', 'hv_on'];
let checking = false;

async function recheck() {
  if (checking) return;
  checking = true;
  render();
  await checkNow(api, dialog);
  checking = false;
  refreshPreview();
  render();
}

const reasonText = (list) => list.map((item) => (item.fix ? `${item.text}
Processing method: ${item.fix}` : item.text)).join('\n\n');

function refusalText(error) {
  const reasons = (error.reasons || []).filter((item) => item && item.text);
  const head = reasons.some((item) => item.text === error.message) ? [] : [{text: error.message, fix: ''}];
  return reasonText([...head, ...reasons]);
}

// ── texts of a schedule ─────────────────────────────────────────────────────────────────────────────────────
const amount = (value) => String(Math.round(Number(value) * 100) / 100);
const intervalText = (minutes) => (minutes >= 60 && minutes % 60 === 0 ? `every ${minutes / 60} hour` : `every ${minutes} minutes`);
const endText = (end) => (end.mode === 'count' ? `Total ${end.count} times` : end.mode === 'until' ? `to ${end.time}` : "continues until stop is pressed");
const liquidText = (list) => (list || []).map((item) => (item.g !== null && item.g !== undefined
  ? `${item.label}: ${amount(item.g)} g`
  : `${item.label}: ${item.approx ? "approx." : ''}${amount(item.ml)} mL`)).join(' · ');

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function slotNumber(state, sched) {
  const round = state.round;
  const mine = round && ROUND_ACTIVE.includes(round.status) && round.schedule_id === sched.id;
  return mine && round.slot ? round.slot : (sched.next_index || 0) + 1;
}

function scheduleCount(state, sched) {
  const n = slotNumber(state, sched);
  return sched.planned_total ? `No. ${n} / ${sched.planned_total}` : `No. ${n}`;
}

function calibrationCount(state) {
  const run = state.calibration_run || {};
  return run.runs ? `Round ${run.run || '—'} / ${run.runs}` : '—';
}

function left(iso, now) {
  const seconds = Math.max(0, Math.round((parse(iso) - now) / 1000));
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} hours ${two(Math.floor(seconds % 3600 / 60))} minutes`;
  return `${two(Math.floor(seconds / 60))}:${two(seconds % 60)}`;
}

// ── operator ────────────────────────────────────────────────────────────────────────────────────────────────
// The only operator picker of the program. Resolves to the chosen name, or null.
export async function pickOperator() {
  const state = store.state;
  if (!state || store.stale) return null;
  const names = state.operators || [];
  if (!names.length) {
    await dialog.message("No operator list yet", "Please ask the maintenance personnel to add it in \"Maintenance › Operator\".");
    return null;
  }
  const name = await dialog.choose({title: "Select operator", wide: true,
    choices: names.map((item) => ({value: item, label: item, current: item === state.operator}))});
  if (name === null) return null;
  try {
    const reply = await api.post('/api/operator', {name});
    const chosen = reply.operator || name;
    message(`Operator: ${chosen}`);
    hooks.pollNow();
    return chosen;
  } catch (error) {
    message("Operator configuration failed:" + error.message, 'error');
    return null;
  }
}

// ── start, resume, pause, stop ──────────────────────────────────────────────────────────────────────────────
async function startSchedule() {
  if (starting) return;
  starting = true;
  try {
    let state = store.state;
    if (!state || store.stale) return;
    if (state.schedule && ACTIVE.includes(state.schedule.status)) {
      if (state.schedule.status === 'paused') await resumeSchedule(state);
      return;
    }
    let operator = state.operator;
    if (!operator) {
      operator = await pickOperator();          // the picker opens first; a missing operator is not a reason
      if (!operator) return;
    }
    await refreshPreview();                     //“Start now” is the moment of the confirmation, not of filling the form
    state = store.state;
    const reasons = startReasons(state, store.stale);
    if (reasons.length) {
      await dialog.message("The schedule cannot be started at this time", reasonText(reasons));
      return;
    }
    const form = store.form;
    const body = requestOf(form);
    const pv = store.preview.data;
    const recipe = pv.recipe || {};
    const needs = pv.needs || {high_voltage: Boolean(recipe.uses_plasma), liquid: Boolean(recipe.uses_pump), hv_off_checked: false};
    const plasma = Boolean(needs.high_voltage || recipe.uses_plasma);
    const volts = pv.voltage_v === null || pv.voltage_v === undefined ? '—' : pv.voltage_v;
    const limit = (state.rpi && state.rpi.server && state.rpi.server.limits && state.rpi.server.limits.max_voltage_v) || 700;
    const flow = (pv.flow && pv.flow.text) || (state.rpi && state.rpi.flow && state.rpi.flow.text) || '';
    const first = pv.spec && pv.spec.first_slot ? new Date(pv.spec.first_slot) : null;
    const once = form.end.mode === 'count' && form.end.count === 1;
    const liquids = liquidText(recipe.liquids);
    const lines = [
      ['Recipe', recipe.name || ''],
      ["Operator", operator],
      ["first measurement", form.start.mode !== 'at' ? "immediately"
        : first ? labels.fmtTime(first, 'HH:MM') + (sameDay(first, new Date()) ? '' : "(tomorrow)") : form.start.time],
      once ? null : ["interval", intervalText(form.interval_minutes)],
      ["End", endText(form.end)],
      ["estimated single time\nLiquid used in", "approx." + labels.fmtDuration(pv.estimate_seconds)],
      recipe.uses_pump && liquids ? ["liquid used", liquids] : null,
      pv.blank ? ["blank solution", `${pv.blank.recipe_name} · per ${pv.blank.every} samples`] : null,
      plasma ? ["HSPY set voltage", `${volts} V (upper limit ${limit} V)`] : null,
      plasma && flow ? ["measurement process", flow] : null,
    ];
    const ticks = [];
    if (needs.high_voltage) {
      ticks.push(`I have confirmed that every measurement will automatically output ${volts} V high voltage and will not ask again.`, "The electrode and high-voltage line have been connected; people have stayed away and the upper cover has been closed");
    }
    if (needs.liquid) ticks.push("pipelines, reagents and waste drain are in place");
    // The sentence comes from the server (AMENDMENTS M13): the RPi may be away, or connected with a supply it cannot read.
    if (needs.hv_off_checked) ticks.push(needs.hv_ack_text || "RPi is not connected, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.");
    ticks.push("physical emergency stop/power-off switch is ready for use at any time");
    const notes = pv.warnings || recipe.warnings || [];
    const warn = [plasma
      ? `will automatically output ${volts} V high voltage for each measurement during the scheduling period, without confirming it one after another. "Stop" on the screen means the software stops, not an emergency stop. In an emergency, please press the physical emergency stop button.`
      : "schedule will automatically start the pump and syringe pump according to time. \"Stop\" on the screen means the software stops, not an emergency stop.",
    ...notes.slice(0, 2)];
    if (notes.length > 2) warn.push(`and ${notes.length - 2} can be seen in "Scheduling Measurement"`);
    const yes = await dialog.confirm({
      title: plasma ? "⚠ Start scheduling: Each measurement will automatically output high voltage" : "Start scheduling",
      variant: plasma ? 'hv' : 'danger', timeout: 90, lines, warn, ticks, confirmLabel: "Start scheduling",
    });
    if (!yes) return;
    try {
      await api.post('/api/schedule/start', {...body, confirm: {high_voltage: Boolean(needs.high_voltage), liquid: Boolean(needs.liquid), hv_off_checked: Boolean(needs.hv_off_checked)}});
      message("schedule has started");
    } catch (error) {
      await dialog.message("The schedule did not start", refusalText(error), {level: 'error'});
    }
    hooks.pollNow();
  } finally {
    starting = false;
  }
}

async function resumeSchedule(state) {
  const sched = state.schedule;
  const calibration = sched.kind === 'calibration';
  const round = state.round;
  const pumps = round && round.status === 'paused' && round.schedule_id === sched.id ? round.paused_pumps || [] : [];
  const lines = [
    ['Recipe', (sched.recipe && sched.recipe.name) || ''],
    calibration ? ["calibration", calibrationCount(state)] : ["schedule", scheduleCount(state, sched)],
    ["Currently", labels.schedule.paused || "suspended"],
    pumps.length ? ["will restart after continuing", pumps.join(', ')] : null,
  ];
  const yes = await dialog.confirm({title: calibration ? "continue calibration" : "Continue scheduling", lines, confirmLabel: "Continue"});
  if (!yes) return;
  try {
    await api.post('/api/resume', {});
  } catch (error) {
    await dialog.message(calibration ? "Calibration did not continue" : "The schedule did not continue", refusalText(error), {level: 'error'});
  }
  hooks.pollNow();
}

async function pauseSchedule() {
  try {
    await api.post('/api/pause', {});
  } catch (error) {
    message("Pause failed:" + error.message, 'error');
  }
  hooks.pollNow();
}

//“STOP”: no dialog, always enabled. The footer then says only what was read back.
async function stopEverything() {
  message("Stop command has been sent, waiting for readback...", 'warn');
  let result;
  try {
    // 30 s: the program waits up to 22 s for an RPi whose supply is switched off (AMENDMENTS M14)
    result = labels.stopMessage(await api.post('/api/stop', {}, {timeout: 30000}));
  } catch (error) {
    result = error.status
      ? {text: `⚠ The stop command was not confirmed: Program (${error.message}) - Please use the physical switch to cut off the power`, level: 'error'}
      : labels.stopMessage(null);
  }
  message(result.text, result.level);
  hooks.pollNow();
}

async function acknowledge(id) {
  try {
    await api.post('/api/alarms/ack', {ids: [id]});
  } catch (error) {
    message("Alarm acknowledgment failed:" + error.message, 'error');
  }
  hooks.pollNow();
}

function alarmDialog(alarm) {
  if (!alarm) return;
  dialog.message(`Alarm · ${labels.priority[alarm.priority] || ''}`, alarm.text, {
    level: alarm.priority <= 2 ? 'error' : '',
    lines: [["occurrence time", labels.fmtTime(alarm.since, 'MM-DD HH:MM:SS')], ["area", alarm.area || '—'],
      ["status", `${alarm.cleared ? "has been restored" : "is active"} · ${alarm.acked ? "confirmed" : "Not confirmed"}`]],
  });
}

// ── full screen ─────────────────────────────────────────────────────────────────────────────────────────────
// Only the Fullscreen API: a page may enter full screen from a tap and from nothing else. So at the start of the
// page, when it is not in full screen, a layer over the whole window asks for that one tap (anywhere on it).
//What the person chose is kept in this PC's browser: "No Full Screen" on the layer and "Leave Full Screen" in the top bar
//write FS_KEY = 'off', and the layer stays away until "Full Screen" is pressed again. Leaving with Esc is not a choice
// (Esc also closes a dialog): the layer is back at the next start.
// No layer and no button in a browser without the API; no layer with ?fs=0 in the address (screenshots), in the
// development fixture (hooks.gate), or in a window that fills the screen already (F11, a kiosk window).
const FS_KEY = 'plasmic.fullscreen';

const fsCan = () => Boolean(document.fullscreenEnabled && document.documentElement.requestFullscreen && document.exitFullscreen);
const fsOn = () => Boolean(document.fullscreenElement);
const fsFilled = () => fsOn() || window.matchMedia('(display-mode: fullscreen)').matches;

function fsDeclined() {
  try { return window.localStorage.getItem(FS_KEY) === 'off'; } catch (_) { return false; }
}

function fsRemember(declined) {
  try {
    if (declined) window.localStorage.setItem(FS_KEY, 'off');
    else window.localStorage.removeItem(FS_KEY);
  } catch (_) { /* the browser may refuse its storage: the choice then lasts as long as the page */ }
}

function fsEnter() {
  fsRemember(false);
  setHidden(el.gate, true);
  if (fsOn()) return;
  const refused = () => message("The browser does not enter full screen", 'warn');
  try {
    const asked = document.documentElement.requestFullscreen({navigationUI: 'hide'});
    if (asked && typeof asked.catch === 'function') asked.catch(refused);
  } catch (_) {
    refused();
  }
}

function fsLeave() {
  fsRemember(true);
  if (!fsOn()) return;
  const asked = document.exitFullscreen();
  if (asked && typeof asked.catch === 'function') asked.catch(() => {});
}

function fsPaint() {
  const on = fsOn();
  // a window that fills the screen without the API (F11, kiosk) cannot be left from here: no button then
  setHidden(el.fs, !fsCan() || (!on && fsFilled()));
  setText(el.fs, on ? "Leave full screen" : "full screen");
  if (fsFilled()) setHidden(el.gate, true);
}

//A tap anywhere on the layer. “STOP” is never blocked: a tap on it through the layer takes the layer away
// and stops (as through the scrim of a dialog).
function onGate(event) {
  const through = document.elementsFromPoint(event.clientX, event.clientY)
    .find((item) => item instanceof HTMLElement && item.matches('[data-through]') && !item.disabled);
  if (through) {
    setHidden(el.gate, true);
    through.click();
    return;
  }
  fsEnter();
}

function mountFullscreen() {
  el.fs = $('fsbtn');
  el.gate = $('fs-gate');
  const skip = $('fs-skip');
  const go = $('fs-go');
  el.fs.addEventListener('click', () => { if (fsOn()) fsLeave(); else fsEnter(); });
  el.gate.addEventListener('click', onGate);
  skip.addEventListener('click', (event) => {
    event.stopPropagation();
    fsRemember(true);
    setHidden(el.gate, true);
  });
  document.addEventListener('fullscreenchange', fsPaint);
  document.addEventListener('fullscreenerror', fsPaint);
  window.matchMedia('(display-mode: fullscreen)').addEventListener('change', fsPaint);
  const asks = fsCan() && !fsFilled() && !fsDeclined() && hooks.gate !== false
    && new URLSearchParams(window.location.search).get('fs') !== '0';
  setHidden(el.gate, !asks);
  fsPaint();
  if (asks) go.focus({preventScroll: true});       // Enter or the space bar start too
}

// ── mount ───────────────────────────────────────────────────────────────────────────────────────────────────
function pressable(node, action) {
  node.addEventListener('click', action);
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); action(); }
  });
}

function cell(extra) {
  const label = h('small');
  const value = h('b');
  return {box: h('div', {class: extra || '', hidden: true}, label, value), label, value};
}

function cellsDialog() {
  const lines = el.cells.filter((item) => !item.box.hidden).map((item) => [item.label.textContent, item.value.textContent]);
  if (lines.length) dialog.message(lines[0][0], '', {lines});      //titled “Scheduling” or “Calibration”, as the first cell
}

export function mount(given) {
  hooks = {...hooks, ...given};
  const banner = $('banner');
  const prog = $('prog');
  el = {
    hmi: $('hmi'), mode: $('mode'),
    rpi: $('chip-rpi'), rpiText: $('chip-rpi').querySelector('span'),
    pump: $('chip-pump'), pumpText: $('chip-pump').querySelector('span'),
    op: $('opbtn'), opName: $('opbtn').querySelector('b'),
    maint: $('maint-mark'),
    date: $('clock').querySelector('small'), time: $('clock').querySelector('span'),
    banner, none: banner.querySelector('.none-text'), pri: banner.querySelector('.pri'), when: banner.querySelector('.time'),
    msg: banner.querySelector('.msg'), more: banner.querySelector('.more'), ack: $('banner-ack'), list: $('banner-list'),
    nav: [...$('nav').querySelectorAll('button[data-page]')],
    start: $('btn-start'), pause: $('btn-pause'), stop: $('btn-stop'), prog, hv: $('hvtag'),
    why: cell('tap'), note: cell('tap'), cells: [cell('keep tap'), cell('tap'), cell('give tap'), cell('tap')],
    recheck: h('button.btn.recheck', {type: 'button', hidden: true, onclick: () => { recheck(); }}, "Recheck"),
  };
  for (const item of [el.why, el.note, ...el.cells]) {
    item.box.setAttribute('role', 'button');
    item.box.tabIndex = 0;
  }
  for (const item of [el.why, el.note]) item.value.classList.add('why');
  // the four cells of a running schedule may be cut in a 4:3 window: a tap on any of them shows all four in full
  for (const item of el.cells) pressable(item.box, cellsDialog);
  setText(el.why.label, "cannot start");
  setText(el.note.label, "message");
  prog.replaceChildren(el.why.box, el.recheck, ...el.cells.map((item) => item.box), el.note.box);

  for (const button of el.nav) button.addEventListener('click', () => hooks.go(button.dataset.page));
  el.op.addEventListener('click', () => { pickOperator(); });
  // the two status words say the rest on a tap (what a mouse reads from their tooltip)
  pressable(el.rpi, () => { if (chips.rpi) dialog.message("RPi (plasma)", chips.rpi); });
  pressable(el.pump, () => { if (chips.pump) dialog.message("pump control board", chips.pump); });
  mountFullscreen();
  pressable(el.msg, () => alarmDialog(shownAlarm));
  el.ack.addEventListener('click', () => { if (shownAlarm) acknowledge(shownAlarm.id); });
  el.list.addEventListener('click', () => hooks.go('alarms'));
  el.start.addEventListener('click', () => { startSchedule(); });
  el.pause.addEventListener('click', () => { pauseSchedule(); });
  el.stop.addEventListener('click', () => { stopEverything(); });
  pressable(el.why.box, () => { if (shownReasons.length) dialog.message("The schedule cannot be started at this time", reasonText(shownReasons)); });
  pressable(el.note.box, () => { if (shownMessage) dialog.message("message", shownMessage.text, {level: shownMessage.level === 'error' ? 'error' : ''}); });
  document.addEventListener('plasmic:dialog-timeout', () => message("Confirmation window timeout has been canceled and no command has been sent.", 'warn'));
  store.subscribe(() => { if (previewIsOld() || (!store.form.recipe_id && previewKey)) refreshPreview(); });
  render();
}

// ── render ──────────────────────────────────────────────────────────────────────────────────────────────────
export function render() {
  if (!el) return;
  const now = Date.now();
  const state = store.state;
  const stale = store.stale;
  renderTop(state, stale, now);
  renderBanner(alarmsNow());
  renderNav(state, alarmsNow());
  renderFoot(state, stale, now);
}

// Once a second: clock, countdowns, fading of the footer line, and the 10 s rhythm of the preview.
export function tick() {
  render();
  const state = store.state;
  const active = Boolean(state && state.schedule && ACTIVE.includes(state.schedule.status));
  if (state && !store.stale && !active && store.form.recipe_id && Date.now() - previewAt >= PREVIEW_MS) refreshPreview();
}

function renderTop(state, stale, now) {
  const demo = Boolean((state && state.demo) || (store.boot && store.boot.demo));
  setClass(el.hmi, 'demo', demo);
  setClass(el.mode, 'demo', demo);
  setText(el.mode, demo ? "simulation mode" : "official mode");
  setAttr(el.mode, 'title', demo ? "does not connect to any device" : '');

  let text = 'RPi …';
  let cls = 'q-na';
  let title = '';
  if (stale) { text = "RPi ? Unknown"; cls = 'q-bad'; }
  else if (state) {
    const rpi = state.rpi || {};
    const sim = Boolean((rpi.server && rpi.server.sim) || state.demo);
    text = rpi.connected ? (sim ? "RPi Analog" : "RPi is connected") : "RPi not connected";
    cls = rpi.connected ? 'q-ok' : 'q-na';
    title = rpi.error || (rpi.host ? `${rpi.host}:${rpi.port}` : '');
  }
  setText(el.rpiText, text);
  setQ(el.rpi, cls);
  setAttr(el.rpi, 'title', title);
  chips.rpi = [text, stale ? STALE_SHORT : title].filter(Boolean).join('\n');

  const pump = stale ? null : state && state.pump;
  if (stale || state) {
    const word = "pump control board" + labels.pumpChip(pump);
    setText(el.pumpText, word);
    setQ(el.pump, labels.pumpClass(pump));
    setAttr(el.pump, 'title', pump ? pump.text || '' : '');
    //which port it is (“COMx=Pump Control Board (Arduino Mega 2560)”); the simulator has no port, only its name
    const more = pump ? [pump.mode === 'sim' ? pump.description || pump.text : labels.pumpPort(pump) || pump.text, pump.error] : [STALE_SHORT];
    chips.pump = [word, ...more].filter((item, index, all) => item && all.indexOf(item) === index).join('\n');
  }

  const operator = state ? state.operator || '' : '';
  setText(el.opName, state ? operator || "Please select" : '…');
  setClass(el.op, 'none', Boolean(state) && !operator);

  const seconds = hooks.unlocked() ? hooks.maintLeft() : 0;
  setHidden(el.maint, !(seconds > 0));
  if (seconds > 0) setText(el.maint, `Repair ${Math.floor(seconds / 60)} : ${two(Math.floor(seconds % 60))}`);

  setText(el.date, labels.fmtTime(now, 'YYYY-MM-DD'));
  setText(el.time, labels.fmtTime(now, 'HH:MM:SS'));
}

function renderBanner(alarms) {
  const first = (alarms.active || [])[0] || null;
  shownAlarm = first;
  const cls = !first ? 'banner none' : first.acked ? 'banner acked' : `banner p${first.priority} unacked`;
  if (el.banner.className !== cls) el.banner.className = cls;
  setHidden(el.none, Boolean(first));
  for (const node of [el.pri, el.when, el.msg, el.list]) setHidden(node, !first);
  if (!first) {
    setHidden(el.more, true);
    setHidden(el.ack, true);
    return;
  }
  const count = alarms.active.length;
  setText(el.pri, (labels.priority[first.priority] || '') + (first.cleared ? "· Restored" : ''));
  setText(el.when, labels.fmtTime(first.since, 'HH:MM:SS'));
  setText(el.msg, first.text);
  setText(el.more, count > 1 ? `and ${count - 1}` : '');
  setHidden(el.more, count <= 1);
  setHidden(el.ack, Boolean(first.acked));
}

function renderNav(state, alarms) {
  const page = hooks.page();
  const unlocked = hooks.unlocked();
  for (const button of el.nav) {
    const name = button.dataset.page;
    setClass(button, 'sel', name === page);
    const badge = button.querySelector('.badge');
    if (name === 'alarms') {
      const count = (alarms.active || []).length;
      setHidden(badge, count === 0);
      setText(badge, count ? String(count) : '');
      const worst = alarms.unacked ? alarms.worst : null;
      for (const cls of ['p1', 'p2', 'p3']) setClass(badge, cls, worst !== null && cls === `p${worst}`);
      setClass(badge, 'acked', count > 0 && worst === null);
    } else if (name === 'calibration') {
      const run = state && state.calibration_run;
      setHidden(badge, !(run && run.prompt));
    } else if (name === 'maint') {
      setText(button.querySelector('.lbl'), unlocked ? "maintenance settings" : "🔧 Maintenance");
    }
  }
}

function showCells(rows) {
  rows.forEach(([label, value, cls], index) => {
    const item = el.cells[index];
    setText(item.label, label);
    setText(item.value, value);
    setAttr(item.box, 'title', value);
    setClass(item.value, 'paused', cls === 'paused');
  });
}

function renderFoot(state, stale, now) {
  const sched = state && state.schedule;
  const active = Boolean(sched && ACTIVE.includes(sched.status));
  const status = active ? sched.status : '';
  const calibration = active && sched.kind === 'calibration';
  const paused = status === 'paused';
  const reasons = active && !stale ? [] : startReasons(state, stale);

  setText(el.start, paused ? "Continue RESUME" : calibration ? "Calibrating" : active ? "is scheduled" : "Start scheduling");
  setClass(el.start, 'resume', paused);
  setDisabled(el.start, stale || !state || (active ? !paused : reasons.length > 0));
  setAttr(el.start, 'title', reasons.length ? reasons[0].text : '');
  setText(el.pause, status === 'pausing' ? "Pausing..." : "PAUSE PAUSE");
  setDisabled(el.pause, stale || !(status === 'waiting' || status === 'running'));
  setHidden(el.hv, !(active && sched.uses_plasma));

  // .prog shows exactly one thing.
  const lines = footerLines(state);
  const mine = lines.find((item) => item.mine && now - item.at < (item.level === 'error' && !active ? ERROR_MS : FRESH_MS)) || null;
  let mode = 'none';
  let shown = null;
  if (mine) { mode = 'note'; shown = mine; }
  else if (active && !stale) mode = 'cells';
  else if (reasons.length) mode = 'why';
  else {
    const newest = lines[0];
    if (newest && (newest.level !== 'info' || now - newest.at < FRESH_MS)) { mode = 'note'; shown = newest; }
  }
  shownMessage = shown;
  shownReasons = mode === 'why' ? reasons : [];
  setClass(el.prog, 'cells', mode === 'cells');
  setHidden(el.why.box, mode !== 'why');
  setHidden(el.recheck, !(mode === 'why' && reasons.some((item) => CHECKABLE.includes(item.code))));
  setText(el.recheck, checking ? "Checking..." : "Recheck");
  setDisabled(el.recheck, checking);
  setHidden(el.note.box, mode !== 'note');
  for (const item of el.cells) setHidden(item.box, mode !== 'cells');

  if (mode === 'why') {
    // the footer is short: the words that say what is wrong come first (the dialog of a tap has the whole sentence)
    setText(el.why.value, reasons[0].text.replace(/^The pre-discharge inspection failed: /, ''));
    setAttr(el.why.box, 'title', reasons[0].text);
  } else if (mode === 'note') {
    setText(el.note.value, shown.text);
    setAttr(el.note.box, 'title', shown.text);
    setClass(el.note.value, 'err', shown.level === 'error');
  } else if (mode === 'cells') {
    const round = state.round;
    const running = round && ROUND_ACTIVE.includes(round.status) && round.schedule_id === sched.id;
    const phase = running ? (round.phases || [])[round.phase] : null;
    const stepText = running ? `${round.step || 0} / ${round.step_count || 0}` + (phase && phase.name ? ` · ${phase.name}` : '') : '—';
    const word = labels.schedule[status] || status;
    const tone = paused || status === 'pausing' ? 'paused' : '';
    if (calibration) {
      const run = state.calibration_run || {};
      const prompt = run.prompt || (round && round.prompt);
      showCells([["calibration", calibrationCount(state)], ["Currently", prompt ? "Waiting for fluid change" : word, prompt ? '' : tone],
        ["steps", stepText], ["standard product", run.level_name || '—']]);
    } else {
      const counts = sched.counts || {};
      const missed = (counts.skipped_overrun || 0) + (counts.skipped_paused || 0) + (counts.not_started || 0);
      const next = status === 'waiting'
        ? ["next measurement", sched.next_slot_at ? `${labels.fmtTime(sched.next_slot_at, 'HH:MM')} (${left(sched.next_slot_at, now)}) ` : '—']
        : ["Currently", word, tone];
      showCells([["schedule", scheduleCount(state, sched)], next, ["steps", stepText], ["Not measured", `${missed} times`]]);
    }
  }
}
