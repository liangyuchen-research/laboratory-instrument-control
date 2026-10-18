//maint.js — 🔧 Maintenance (SPEC E.9): the login panel with the PIN keys, the tab bar, the message chip, "Logout".
//
// main.js gives this module the root div.maint-head. The seven tabs are modules of their own (maint-<tab>.js) with
// roots of their own; main.js loads, shows and hides them while the maintenance session is open. This module never
// mounts them: a tap on the tab bar is ctx.go('maint', '<tab>'), a login is store.setUnlock(session).
// The session ends by itself after 600 s without input (main.js) or when the program no longer knows it (a request
//answered with `locked`): the login panel then says "Maintenance mode has automatically logged out".
//
// For the tab modules (import {…} from './maint.js'):
//act(ctx, work, done) one request with the chip: “Processing...”, then what `done` says, or the refusal.
//                               work() -> Promise(reply); done = a text, or reply -> text | {text, level}.
//                               Resolves to the reply, or to null when the request failed
//   report(text, level)         a line for the chip: level 'ok' (default) | 'busy' | 'err'; '' takes the chip away
//   fail(ctx, error)            the refusal of a request the tab sent itself
//   busyReason(ctx, state)      why the controls of the "busy rule" (API.md C.1) are off now; '' = they are on
//   setHidden · setDisabled · gbtn · setGbtn · kvRow · setValue      the small DOM helpers the tabs share

const TABS = [['recipe', 'Recipe'], ['link', "connection"], ['plasma', "plasma parameters"], ['pumps', "Pump and Scale"], ['spectra', "spectrum"],
  ['coefficients', "correction coefficient"], ['operators', "Operator"]];
const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', "Clear", '0', '⌫'];
const PIN_MIN = 6;
const PIN_MAX = 8;
const SCHEDULE_BUSY = ['waiting', 'running', 'pausing', 'stopping'];
const ROUND_BUSY = ['running', 'pausing', 'paused', 'stopping'];
const QUALITY = ['q-na', 'q-bad', 'q-run', 'txt-warn'];

export const T_BUSY = "The schedule or pump cannot be changed while it is running.";
export const T_TOOL = "Another operation is in progress";
export const T_STALE = "The program did not respond";
const T_AUTO = "Maintenance mode has automatically logged out";
const T_WORKING = "Processing...";
const T_FAILED = "⚠ Operation failed · Click here for details";

let ui = null;                 // the elements of this module
let page = null;               // its ctx
let pin = '';                  // digits typed so far
let waiting = false;           //"Logging in..."
let line = {text: '', wait: false};   // the line under the dots
let chip = null;               // {text, level}: the last result of a maintenance action
let wasOpen = false;           // the session was open at the last render
let leaving = false;           //“Logout” was pressed: the session does not end "by itself"

// ── helpers shared with the tab modules ─────────────────────────────────────────────────────────────────────
export function setHidden(node, hidden) {
  const value = Boolean(hidden);
  if (node.hidden !== value) node.hidden = value;
}

export function setDisabled(node, disabled) {
  const value = Boolean(disabled);
  if (node.disabled !== value) node.disabled = value;
}

//A big button with its small line (.gbtn). gbtn(ctx, 'Connection diagnosis', 'Check the device step by step', action) -> {button, small, hint}
export function gbtn(ctx, label, hint, action) {
  const small = ctx.dom.h('small', null, hint || '');
  const button = ctx.dom.h('button.gbtn', {onclick: action}, ctx.dom.h('span', null, label), small);
  return {button, small, hint: hint || ''};
}

// Off with the reason in its small line, or on with its hint (the one given here, else the one it was made with).
export function setGbtn(ctx, item, reason, hint) {
  setDisabled(item.button, Boolean(reason));
  ctx.dom.setText(item.small, reason || (hint === undefined ? item.hint : hint));
}

//One line of a .kv: gray label, bold value. kvRow(ctx, 'connection') -> {row, value, full}
export function kvRow(ctx, label) {
  const value = ctx.dom.h('b');
  const item = {row: ctx.dom.h('div', null, ctx.dom.h('span', null, label), value), value, full: ''};
  const open = () => { if (item.full) ctx.dialog.message("message", item.full); };
  item.row.addEventListener('click', open);
  item.row.addEventListener('keydown', (event) => {
    if (item.full && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); open(); }
  });
  return item;
}

// setValue(ctx, item, text, cls, full): cls = '' | 'q-na' | 'q-bad' | 'q-run' | 'txt-warn'. `full` is the whole text
// of a value that may not fit its line: when it is cut, a tap on the line opens it in a message dialog.
export function setValue(ctx, item, text, cls, full) {
  ctx.dom.setText(item.value, text);
  for (const name of QUALITY) ctx.dom.setClass(item.value, name, name === cls);
  const cut = Boolean(full) && item.value.scrollWidth > item.value.clientWidth + 1;
  item.full = cut ? String(full) : '';
  ctx.dom.setClass(item.row, 'tap', cut);
  if (cut) {
    if (item.row.getAttribute('role') !== 'button') { item.row.setAttribute('role', 'button'); item.row.tabIndex = 0; }
  } else if (item.row.hasAttribute('role')) {
    item.row.removeAttribute('role');
    item.row.removeAttribute('tabindex');
  }
}

// Why a control that falls under the busy rule is off now ('' = it is on). The program checks again when the
// request arrives and its answer goes to the chip.
export function busyReason(ctx, state) {
  if (ctx.store.stale || !state) return T_STALE;
  const owner = state.owner;
  if (owner && owner.kind === 'tool') return T_TOOL;
  const schedule = state.schedule;
  const round = state.round;
  if (owner || (schedule && SCHEDULE_BUSY.includes(schedule.status)) || (round && ROUND_BUSY.includes(round.status))) return T_BUSY;
  return '';
}

// ── the message chip ────────────────────────────────────────────────────────────────────────────────────────
function paintChip() {
  if (!ui) return;
  setHidden(ui.chip, !chip);
  if (!chip) return;
  page.dom.setText(ui.chip, chip.level === 'busy' ? T_WORKING : chip.level === 'err' ? T_FAILED : chip.text);
  for (const name of ['ok', 'busy', 'err']) page.dom.setClass(ui.chip, name, chip.level === name);
}

export function report(text, level) {
  const value = text === null || text === undefined ? '' : String(text);
  chip = value ? {text: value, level: level === 'busy' || level === 'err' ? level : 'ok'} : null;
  paintChip();
}

function refusal(error) {
  const text = error && error.message ? String(error.message) : String(error);
  const reasons = ((error && error.reasons) || []).filter((item) => item && item.text);
  const lines = reasons.some((item) => item.text === text) ? [] : [text];
  for (const item of reasons) lines.push(item.fix ? `${item.text}
Processing method: ${item.fix}` : item.text);
  return lines.join('\n\n');
}

export function fail(ctx, error) {
  if (error && error.status === 403 && (error.code === 'locked' || error.code === 'pin_unset')) {
    // api.js has forgotten the session already: main.js hides the tab and render() below shows the login panel.
    report('');
    if (error.code === 'locked') ctx.message(T_AUTO, 'warn');
    return;
  }
  report(refusal(error), 'err');
}

export async function act(ctx, work, done) {
  report(T_WORKING, 'busy');
  let reply;
  try {
    reply = await work();
  } catch (error) {
    fail(ctx, error);
    return null;
  }
  let said = '';
  try {
    said = typeof done === 'function' ? done(reply) : done;
  } catch (error) {
    console.error(error);
  }
  if (said && typeof said === 'object') report(said.text, said.level);
  else report(said || '');
  return reply;
}

// ── login panel ─────────────────────────────────────────────────────────────────────────────────────────────
function again() {
  if (ui && page) draw(page, page.store.state);
}

function press(key) {
  if (waiting) return;
  if (key === "Clear") pin = '';
  else if (key === '⌫') pin = pin.slice(0, -1);
  else if (pin.length < PIN_MAX) pin += key;
  line = {text: '', wait: false};
  again();
}

async function login() {
  const ctx = page;
  if (waiting || pin.length < PIN_MIN || ctx.unlocked()) return;
  //"Login" is switched off while it waits and after a failure: a button that is off must not keep the focus,
  // or the browser sends the next keys nowhere.
  if (ui.wrap.contains(document.activeElement)) document.activeElement.blur();
  waiting = true;
  line = {text: "Logging in...", wait: true};
  again();
  try {
    const reply = await ctx.api.post('/api/unlock', {pin});
    line = {text: '', wait: false};
    pin = '';
    waiting = false;
    ctx.store.setUnlock(reply.session);        // main.js shows the tab that was asked for (Recipe by default)
  } catch (error) {
    line = {text: "Login failed:" + (error && error.message ? error.message : error), wait: false};
    pin = '';
    waiting = false;
  }
  again();
}

async function logout() {
  const ctx = page;
  leaving = true;
  try {
    await ctx.api.post('/api/lock', {});
  } catch (_) { /* the session ends on its own on the program's side */ }
  ctx.store.setUnlock(null);
  report('');
  ctx.go('dash');
}

// Keyboard of the login panel: digits, Backspace, Enter. An open dialog keeps its keys to itself (dialog.js).
function onKey(event) {
  if (!ui || !page || page.unlocked() || ui.wrap.hidden) return;
  if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
  if (/^[0-9]$/.test(event.key)) press(event.key);
  else if (event.key === 'Backspace') press('⌫');
  else if (event.key === 'Enter') login();
  else return;
  event.preventDefault();
}

function showChip() {
  if (!chip) return;
  page.dialog.message("Maintenance operation message", chip.text, {level: chip.level === 'err' ? 'error' : ''});
}

// ── the module ──────────────────────────────────────────────────────────────────────────────────────────────
export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;
  const practice = h('div.pin-note', {hidden: true});
  const dots = h('div.pin-dots');
  const err = h('div.login-err');
  const keys = KEYS.map((key) => h('button', {
    onpointerdown: (event) => event.preventDefault(),      //the keys never take the focus: Enter stays “Login”
    onclick: () => press(key),
  }, key));
  const enter = h('button.btn.primary', {onpointerdown: (event) => event.preventDefault(), onclick: () => { login(); }}, "Login");
  const wrap = h('div.pin-wrap', null, practice, dots, err, h('div.pin-keys', null, keys),
    h('div.pin-foot', null, h('button.btn', {onclick: () => ctx.go('dash')}, "Return"), enter));
  const unset = h('div.empty', {hidden: true},
    h('b', null, "Repair PIN has not been set yet"),
    h('p', null, "Please double-click Set-PIN.cmd in the folder on your computer to set a 6–8 digit maintenance PIN. After setting up, come back here without restarting the program."),
    h('button.btn', {onclick: () => ctx.go('dash')}, "Return to overview"));
  const panel = h('div.panel.login', null,
    h('div.ph', null, h('h2', null, "Maintenance login"), h('span.sub', null, "Recipe, connections, plasma parameters, pumps and scales, spectra and operator lists are for maintenance personnel only")),
    unset, wrap);

  const tabs = TABS.map(([id, label]) => h('button', {dataset: {tab: id}, onclick: () => ctx.go('maint', id)}, label));
  const chipButton = h('button.msgchip', {hidden: true, title: "Click to see the complete message", onclick: showChip});
  const bar = h('div.adm-bar', {hidden: true}, h('div.seg.tabs', null, tabs), h('span.sp'), chipButton,
    h('button.btn', {onclick: () => { logout(); }}, "Log out"));

  root.replaceChildren(panel, bar);
  ui = {login: panel, unset, wrap, practice, dots, err, keys, enter, bar, tabs, chip: chipButton};
  draw(ctx, ctx.store.state);
}

function draw(ctx, state) {
  const {setText, setClass} = ctx.dom;
  const open = ctx.unlocked();
  if (wasOpen && !open && !leaving) line = {text: T_AUTO, wait: false};      // the session ended by itself
  if (open !== wasOpen) {
    pin = '';
    waiting = false;
    if (open) line = {text: '', wait: false};
  }
  if (!open) leaving = false;
  wasOpen = open;
  setHidden(ui.login, open);
  setHidden(ui.bar, !open);
  if (open) {
    for (const button of ui.tabs) setClass(button, 'sel', button.dataset.tab === ctx.arg);
    paintChip();
    return;
  }
  const boot = ctx.store.boot || {};
  const pinSet = state ? state.pin_set !== false : boot.pin_set !== false;
  const practice = boot.practice_pin ? String(boot.practice_pin) : '';
  setHidden(ui.unset, pinSet);
  setHidden(ui.wrap, !pinSet);
  setHidden(ui.practice, !practice);
  setText(ui.practice, practice ? `The maintenance PIN of practice mode is fixed to ${practice}` : '');
  setText(ui.dots, '●'.repeat(pin.length));
  setText(ui.err, line.text);
  setClass(ui.err, 'wait', line.wait);
  setDisabled(ui.enter, waiting || pin.length < PIN_MIN);
  for (const key of ui.keys) setDisabled(key, waiting);
}

export function render(state, ctx) {
  draw(ctx, state);
}

export function show() {
  window.addEventListener('keydown', onKey);
}

export function hide() {
  window.removeEventListener('keydown', onKey);
  pin = '';                    // a half-typed PIN does not stay on the screen
  again();
}
