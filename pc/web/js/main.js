// main.js — start of the page: bootstrap, scaling (the frame follows the shape of the window), the 1 s poll, the hash
// router, the activity timer of the maintenance PIN, and the loading of the page modules.
//
// A page module is js/pages/<name>.js with
//   export function mount(root, ctx)     once, after the bootstrap reply arrived (ctx.store.boot is set): build the
//                                        static DOM inside root
//   export function render(state, ctx)   after every poll and after every change of the store, only while visible and
//                                        only with a state (never null)
//   export function show(ctx, arg)       optional: the page became visible (arg = second argument of ctx.go, else null)
//   export function hide(ctx)            optional
// ctx = {api, store, dialog, dom, labels, chart, go(page, arg), message(text, level), unlocked(), arg}
//   root            <section class="page" id="page-<name>">: a flex column, 8 px between its panels. 582 px high inside,
//                   always; its width follows the window: 888 px inside at 4:3, 1229 px at 16:9, 1544 px at most.
//                   A page lays itself out for any width in between (flex / grid; var(--extra) and @container hmi in
//                   the style sheets) and never for more height
//   ctx.go          go('alarms') · go('runs', {…}) (the argument reaches show() and ctx.arg) · go('maint', 'plasma')
//   ctx.message     a line for the footer; level 'info' (default) | 'warn' | 'error'
//   ctx.unlocked    true while the maintenance session of this tab is open
// A module touches only its own root. A module that is not there yet (or does not load) gets a placeholder panel,
// so pages can be added one by one; an error thrown by a module is caught and shown in the footer.
//
//Repair: #page-maint holds one root for maint.js (login panel / tab bar) and one root per tab module maint-<tab>.js
// (both are display: contents, so their children lay out as children of the page). main.js shows the root of the
// chosen tab only while the maintenance session is open. ctx.go('maint', '<tab>') changes the tab; ctx.arg of
// maint.js and of the tab modules is the id of the chosen tab. Log in with store.setUnlock(session) and the tab appears.

import * as api from './api.js';
import { store } from './store.js';
import * as dom from './dom.js';
import * as dialog from './dialog.js';
import * as labels from './labels.js';
import * as chart from './chart.js';
import * as frame from './frame.js';

const PAGES = ['dash', 'schedule', 'manual', 'trend', 'runs', 'calibration', 'alarms', 'maint'];
const TABS = ['recipe', 'link', 'plasma', 'pumps', 'spectra', 'coefficients', 'operators'];
const PLACEHOLDER = "This page is still under development";

const POLL_MS = 1000;          // /api/state every second
const ABORT_MS = 4000;         // a poll that takes longer is given up
const STALE_MS = 5000;         // no fresh state for this long: the data on screen is not trustworthy
const IDLE_LOCK_S = 600;       //repair locks itself after this long without a tap or a key

const FIXTURE_FLAG = 'dev-fixture';   // development aid, see boot()

const records = new Map();     // module name -> {name, root, box, ctx, module, loading, loaded, visible}
const startedAt = performance.now();
let page = '';
let tab = 'recipe';
let nextArg = null;
let raw = null;                // newest /api/state as it came
let lastGood = 0;
let polling = false;
let pollAgain = false;
let lastInput = performance.now();
let sessionAt = 0;             // when this page got its maintenance session
let sessionSeen = false;       // the program has confirmed that session at least once
let knownSession = store.unlock;
let renderQueued = false;
let locking = false;
let labelled = null;           // the bootstrap reply the words of labels.js were made from
let rebooting = false;         // bootstrap is being read again (the pump setup or a calibration was saved)
let fixtureOn = false;
let fixtureTried = false;
let bootArrived = null;
const booted = new Promise((resolve) => { bootArrived = resolve; });   // page modules are mounted after this
const complained = new Map();

// ── scaling: the frame is 768 high and as wide as the shape of the window asks for; it fills the window ─────
// A window of 4:3 or wider: the height decides the scale and the frame takes the width that is left, 1024 to 1680
// (1280 x 720 -> 1365 wide at 0.9375). A narrower window: the 1024 x 768 frame, scaled to the width.
// For the style sheets: --k the scale, --w the width of the frame, --wx how much wider than 1024 it is (a plain number).
const FRAME_W = 1024;
const FRAME_H = 768;
const FRAME_MAX_W = 1680;
let scale = '';
let mounted = false;

function fitWindow() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  let k = 1;
  let w = FRAME_W;
  if (width > 0 && height > 0) {
    if (width * FRAME_H >= height * FRAME_W) {
      k = height / FRAME_H;
      w = Math.min(FRAME_MAX_W, Math.max(FRAME_W, Math.round(width / k)));
    } else {
      k = width / FRAME_W;
    }
  }
  const value = `${k}|${w}`;
  if (value === scale) return;
  scale = value;
  const style = document.documentElement.style;
  style.setProperty('--k', String(k));
  style.setProperty('--w', `${w}px`);
  style.setProperty('--wx', String(w - FRAME_W));
  if (mounted) queueRender();                      //a page that draws to its size (trend) draws again
}

// ── maintenance session ─────────────────────────────────────────────────────────────────────────────────────
function unlocked() {
  if (!store.unlock) return false;
  if (store.state && store.state.unlocked) return true;
  return !sessionSeen && sessionAt > 0;      // just logged in: the next poll will confirm it
}

function maintLeft() {
  if (!unlocked()) return 0;
  const idle = IDLE_LOCK_S - (performance.now() - lastInput) / 1000;
  const state = store.state;
  const server = state && state.unlocked ? state.unlock_left_s - (performance.now() - lastGood) / 1000 : IDLE_LOCK_S;
  return Math.max(1, Math.ceil(Math.min(idle, server)));
}

async function lock(text) {
  if (locking) return;
  locking = true;
  try {
    await api.post('/api/lock', {});
  } catch (_) { /* the session ends on its own on the program's side */ }
  locking = false;
  store.setUnlock(null);
  if (text) frame.message(text, 'warn');
}

function watchSession(began) {
  if (store.unlock !== knownSession) {             // a login or a logout happened on this page
    knownSession = store.unlock;
    sessionAt = store.unlock ? performance.now() : 0;
    sessionSeen = false;
    return;
  }
  const state = store.state;
  if (!store.unlock || !state || began === undefined) return;
  if (state.unlocked) { sessionSeen = true; return; }
  if (began < sessionAt) return;                   // this answer is older than the login
  const told = sessionSeen;
  store.setUnlock(null);                           // the program no longer knows the session
  if (told) frame.message("Maintenance mode has automatically logged out", 'warn');
}

// ── page modules ────────────────────────────────────────────────────────────────────────────────────────────
const base = {api, store, dialog, dom, labels, chart, go, message: (text, level) => frame.message(text, level), unlocked,
              pickOperator: () => frame.pickOperator()};

// root = where the module builds; box = what is shown and hidden with it (the same element, except for maint.js)
function record(name, root, box) {
  const item = {name, root, box: box || root, ctx: {...base, arg: null}, module: null, loading: false, loaded: false, visible: false};
  records.set(name, item);
  return item;
}

function placeholder(item) {
  item.root.replaceChildren(dom.h('div.panel.table', null,
    dom.h('div.empty', null, dom.h('b', null, PLACEHOLDER), dom.h('p', null, `js/pages/${item.name}.js`))));
}

function complain(item, error) {
  console.error(`[${item.name}]`, error);
  const text = `An error occurred while displaying this page (${item.name}): ${error && error.message ? error.message : error}`;
  const now = Date.now();
  if (now - (complained.get(text) || 0) < 30000) return;
  complained.set(text, now);
  frame.message(text, 'error');
}

async function load(item) {
  if (item.loaded || item.loading) return;
  item.loading = true;
  await booted;
  try {
    const module = await import(`/js/pages/${item.name}.js`);
    if (typeof module.mount !== 'function' || typeof module.render !== 'function') throw new Error('mount / render are not exported');
    module.mount(item.root, item.ctx);
    item.module = module;
  } catch (error) {
    console.warn(`[${item.name}] ${PLACEHOLDER}:`, error && error.message ? error.message : error);
    item.module = null;
    placeholder(item);
  }
  item.loading = false;
  item.loaded = true;
  if (item.visible) {
    call(item, 'show', item.ctx.arg);
    queueRender();
  }
}

function call(item, name, arg) {
  if (!item.module || typeof item.module[name] !== 'function') return;
  try {
    item.module[name](item.ctx, arg);
  } catch (error) {
    complain(item, error);
  }
}

function setVisible(item, visible, arg) {
  if (item.box.hidden === visible) item.box.hidden = !visible;
  if (visible) item.ctx.arg = arg === undefined ? item.ctx.arg : arg;
  if (item.visible === visible) return;
  item.visible = visible;
  if (!visible) { call(item, 'hide'); return; }
  if (item.loaded) call(item, 'show', item.ctx.arg);
  else load(item);
}

//Which tab root of maintenance is on screen: the chosen one while the session is open, none otherwise.
function syncTabs() {
  const open = page === 'maint' && unlocked();
  for (const id of TABS) {
    const item = records.get(`maint-${id}`);
    setVisible(item, open && id === tab, tab);
  }
  const head = records.get('maint');
  if (head) head.ctx.arg = tab;
}

function renderAll() {
  renderQueued = false;
  frame.render();
  syncTabs();
  const state = store.state;
  if (!state) return;
  for (const item of records.values()) {
    if (!item.visible || !item.module) continue;
    try {
      item.module.render(state, item.ctx);
    } catch (error) {
      complain(item, error);
    }
  }
}

function queueRender() {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(renderAll);
}

// ── router ──────────────────────────────────────────────────────────────────────────────────────────────────
function parseHash() {
  const text = decodeURIComponent(location.hash.replace(/^#/, ''));
  if (PAGES.includes(text)) return {page: text, tab: null};
  if (text.startsWith('maint-') && TABS.includes(text.slice(6))) return {page: 'maint', tab: text.slice(6)};
  return null;
}

function hashOf(name, tabId) {
  return '#' + (name === 'maint' && tabId ? `maint-${tabId}` : name);
}

// go('runs', {round_id}) · go('maint', 'plasma') · go('alarms')
function go(name, arg = null) {
  if (!PAGES.includes(name)) return;
  const wanted = name === 'maint' && TABS.includes(arg) ? arg : null;
  nextArg = arg;
  const hash = hashOf(name, wanted || (name === 'maint' && page === 'maint' ? tab : null));
  if (location.hash === hash || location.hash.includes(FIXTURE_FLAG)) apply({page: name, tab: wanted});
  else location.hash = hash;                       // hashchange calls apply()
}

function apply(route) {
  const target = route || {page: page || 'dash', tab: null};
  const arg = nextArg;
  nextArg = null;
  if (target.page === 'maint' && target.tab) tab = target.tab;
  if (target.page !== page) {
    const old = records.get(page);
    if (old) setVisible(old, false);
    page = target.page;
  }
  const item = records.get(page);
  const already = item.visible;
  setVisible(item, true, page === 'maint' ? tab : arg);
  if (already && item.loaded && arg !== null && page !== 'maint') call(item, 'show', arg);   // same page, new argument
  queueRender();
}

// ── poll ────────────────────────────────────────────────────────────────────────────────────────────────────
function publish(began) {
  const now = performance.now();
  const stale = now - (lastGood || startedAt) > STALE_MS;
  store.apply({state: frame.decorate(raw, stale), stale});
  watchSession(began);
}

async function boot() {
  try {
    await api.bootstrap({timeout: ABORT_MS});
  } catch (error) {
    // Development aid, off by default and never active in the real program: only when the address ends in
    // #dev-fixture AND /api/bootstrap cannot be reached (a plain file server), the examples of API.md from
    // js/fixtures.js stand in for the program.
    if (!location.hash.includes(FIXTURE_FLAG) || fixtureTried) throw error;
    fixtureTried = true;
    const fixtures = await import('./fixtures.js');
    api.useFixture(fixtures.start({store, dialog, frame, go}));
    fixtureOn = true;
    await api.bootstrap();
  }
  labels.setup(store.boot);
  bootArrived();
}

// The timer skips a turn while the previous poll is still on its way; pollNow() asks for one more right after it.
async function poll(again) {
  if (polling) { if (again) pollAgain = true; return; }
  polling = true;
  const began = performance.now();
  let good = false;
  try {
    if (!store.boot) await boot();
    const state = await api.get('/api/state', {timeout: ABORT_MS});
    // A new boot_id = the program was started again: load the page again (new token, maybe new files).
    if (!fixtureOn && state.boot_id && store.boot.boot_id && state.boot_id !== store.boot.boot_id) {
      location.reload();
      return;
    }
    // Another number than the page holds: the pump setup or a calibration was saved (here or in another window).
    // The names of the pumps, the valves and the cleaning pumps come with bootstrap: read it again.
    const rev = state.pump && state.pump.hardware_rev;
    const held = store.boot.hardware && store.boot.hardware.rev;
    if (!fixtureOn && rev && held && rev !== held && !rebooting) {
      rebooting = true;
      api.bootstrap({timeout: ABORT_MS}).catch(() => {}).finally(() => { rebooting = false; });
    }
    raw = state;
    lastGood = performance.now();
    good = true;
  } catch (_) {
    // No answer: the data on screen turns stale after 5 s and the alarm ui_stale says so.
  } finally {
    polling = false;
    publish(good ? began : undefined);
    if (pollAgain) { pollAgain = false; poll(); }
  }
}

function pollNow() {
  poll(true);
}

function everySecond() {
  fitWindow();                                     // a resize that came without its event is caught up here
  const idle = (performance.now() - lastInput) / 1000;
  if (store.unlock && unlocked() && idle >= IDLE_LOCK_S) lock("Maintenance mode has automatically logged out");
  if (store.stale !== (performance.now() - (lastGood || startedAt) > STALE_MS)) publish();
  frame.tick();
  poll();
}

// ── start ───────────────────────────────────────────────────────────────────────────────────────────────────
function start() {
  fitWindow();
  window.addEventListener('resize', fitWindow);
  for (const name of PAGES) {
    const section = document.getElementById(`page-${name}`);
    if (name !== 'maint') { record(name, section); continue; }
    const head = dom.h('div.maint-head');
    section.append(head);
    record('maint', head, section);
    for (const id of TABS) {
      const body = dom.h('div.maint-tab', {hidden: true, dataset: {tab: id}});
      section.append(body);
      record(`maint-${id}`, body);
    }
  }
  // gate: the layer that asks for full screen at the start; never in the development fixture
  frame.mount({go, pollNow, republish: () => publish(), maintLeft, unlocked, page: () => page, gate: !location.hash.includes(FIXTURE_FLAG)});
  mounted = true;
  const note = () => { lastInput = performance.now(); };
  window.addEventListener('pointerdown', note, true);
  window.addEventListener('keydown', note, true);
  //A finger that stays on the screen is not a right click: no browser menu (with "refresh" "previous page") on a touch screen.
  window.addEventListener('contextmenu', (event) => { if (event.pointerType === 'touch') event.preventDefault(); });
  window.addEventListener('hashchange', () => apply(parseHash()));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  store.subscribe(() => {
    if (store.unlock !== knownSession) { watchSession(); pollNow(); }
    if (store.boot && store.boot !== labelled) { labelled = store.boot; labels.setup(store.boot); }   // before the next render
    queueRender();
  });
  apply(parseHash() || {page: 'dash', tab: null});
  poll();
  setInterval(everySecond, POLL_MS);
}

start();
