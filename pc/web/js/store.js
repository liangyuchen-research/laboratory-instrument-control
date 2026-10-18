// store.js — what the whole page shares: the bootstrap reply, the newest /api/state, whether it is stale,
// the maintenance session, the schedule form and its preview.
//
//   store.boot      reply of /api/bootstrap, null until it arrived
//   store.state     newest reply of /api/state, null until the first one. While stale it is the last one received,
//                   with the page's own alarm ui_stale added to state.alarms
//store.stale true = no fresh state for 5 s: show device values as “? Unknown” and disable start controls
//   store.unlock    maintenance session ('' = locked); kept in sessionStorage['plasmic.unlock']
//   store.form      schedule form; kept in localStorage['plasmic.schedule2']
//   store.preview   {data, error, reasons, pending}: data = reply of /api/schedule/preview for the form (null when no
//                   Recipe is chosen, while the first answer is on its way, or when it was refused); error = the
//                   refusal text. frame.js keeps it current
//   store.subscribe(fn) -> unsubscribe     fn(store) after every change
//   store.setForm(patch)                   merges (start / end / blank one level deep), saves, tells the subscribers
//   store.setUnlock(session | null)        saves or forgets the maintenance session

const FORM_KEY = 'plasmic.schedule2';
const UNLOCK_KEY = 'plasmic.unlock';

export const DEFAULT_FORM = Object.freeze({
  recipe_id: '',
  start: Object.freeze({mode: 'now', time: '08:00'}),
  interval_minutes: 10,
  end: Object.freeze({mode: 'count', count: 1, time: '17:00'}),
  blank: Object.freeze({enabled: false, recipe_id: '', every: 4}),
});

const plain = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const clock = (value, fallback) => (typeof value === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(value) ? value : fallback);
const whole = (value, low, high, fallback) => (Number.isInteger(value) && value >= low && value <= high ? value : fallback);

// Whatever is in the browser's storage, the form the pages get always has every key with a sane value.
function cleanForm(raw) {
  const source = plain(raw) ? raw : {};
  const start = plain(source.start) ? source.start : {};
  const end = plain(source.end) ? source.end : {};
  const blank = plain(source.blank) ? source.blank : {};
  return {
    recipe_id: typeof source.recipe_id === 'string' ? source.recipe_id : '',
    start: {mode: start.mode === 'at' ? 'at' : 'now', time: clock(start.time, DEFAULT_FORM.start.time)},
    interval_minutes: whole(source.interval_minutes, 1, 1440, DEFAULT_FORM.interval_minutes),
    end: {mode: ['count', 'until', 'continuous'].includes(end.mode) ? end.mode : 'count',
          count: whole(end.count, 1, 1000, DEFAULT_FORM.end.count), time: clock(end.time, DEFAULT_FORM.end.time)},
    blank: {enabled: blank.enabled === true, recipe_id: typeof blank.recipe_id === 'string' ? blank.recipe_id : '',
            every: whole(blank.every, 1, 100, DEFAULT_FORM.blank.every)},
  };
}

// The browser may refuse its storage (private mode, a full store): the value then lives only as long as the page.
function read(where, key) {
  try { return window[where].getItem(key); } catch (_) { return null; }
}
function write(where, key, value) {
  try {
    if (value === null) window[where].removeItem(key);
    else window[where].setItem(key, value);
  } catch (_) { /* see above */ }
}
function readForm() {
  try { return cleanForm(JSON.parse(read('localStorage', FORM_KEY) || 'null')); } catch (_) { return cleanForm(null); }
}

const subscribers = new Set();
let telling = false;
let again = false;

function tell() {
  if (telling) { again = true; return; }
  telling = true;
  try {
    do {
      again = false;
      for (const fn of [...subscribers]) {
        try { fn(store); } catch (error) { console.error(error); }
      }
    } while (again);
  } finally {
    telling = false;
  }
}

export const store = {
  boot: null,
  state: null,
  stale: false,
  unlock: read('sessionStorage', UNLOCK_KEY) || '',
  form: readForm(),
  preview: {data: null, error: '', reasons: [], pending: false},

  subscribe(fn) {
    subscribers.add(fn);
    return () => subscribers.delete(fn);
  },

  setForm(patch) {
    const merged = {...store.form};
    for (const [key, value] of Object.entries(patch || {})) {
      merged[key] = plain(value) && plain(merged[key]) ? {...merged[key], ...value} : value;
    }
    store.form = cleanForm(merged);
    write('localStorage', FORM_KEY, JSON.stringify(store.form));
    tell();
    return store.form;
  },

  setUnlock(session) {
    const value = typeof session === 'string' ? session : '';
    if (value === store.unlock) return;
    store.unlock = value;
    write('sessionStorage', UNLOCK_KEY, value || null);
    tell();
  },

  // For main.js and frame.js only: several fields at once, one round of telling.
  apply(patch) {
    Object.assign(store, patch);
    tell();
  },
};
