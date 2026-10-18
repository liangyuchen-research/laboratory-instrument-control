// dialog.js — the one dialog engine of the program (SPEC E.3). One dialog at a time, built inside .hmi.
// A tap on the scrim or Esc cancels. No browser alert / confirm / prompt anywhere.
//
// Every function returns a Promise that resolves to the value, or to null when the dialog was cancelled or timed out.
//
//   confirm({title, lines, detail, warn, ticks, confirmLabel, variant, timeout})  -> true
//   choose({title, choices, wide})                                                -> the value of the tapped choice
//   pad({title, min, max, decimals, unit, value, signed})                         -> number
//   pin({title})                                                                  -> '246810' (6-8 digits, a string)
//   text({title, value, maxLength, rule, masked})                                 -> string (trimmed)
//   form({title, fields, ticks, confirmLabel})                                    -> {key: value, ...}
//message(title, text, {level, links}) -> true (one button "Close")
//
// Shapes
//   lines    [[label, value], ...]                      grey label left, bold value right
//   warn     'text' or ['text', ...]                    yellow boxes
//   ticks    ['text', ...]                              confirm: every row must be ticked before the confirm button works
//            [{key, label, value}]                      form: plain rows that may stay unticked; their state is in the result
//   variant  '' | 'danger' (orange) | 'hv' (3 px red frame, red title bar: high voltage)
//   timeout  seconds without a key or a tap before the dialog cancels itself (30; messages 120)
//   choices  [{value, label, small, disabled, current}] 8 per page; small = second line; disabled = true or the reason
//            (shown as the second line); current = the present one (drawn selected)
//   fields   [{key, label, kind: 'text' | 'pad' | 'choose', value, empty, ...the options of that dialog}]
//            a field is a line that opens its own dialog and comes back with the new value
//   links    [{label, href, download}]                  download links (a.btn)
// Also accepted by every function: lines, detail, warn, variant, timeout, cancelLabel,
//   actions [{label, value}] = more footer buttons, each closes the dialog with its value.
// More options: confirm {level: 'error', wide} · choose {columns: 1 (one full-width choice per row), detail,
//   left: true (the choices read as a list: their text starts at the left)} ·
//   pad / pin / text {confirmLabel} · text {allowEmpty} · message {lines, wide, timeout}.
//
// How it behaves
//choose a tap on a choice closes the dialog with that value at once (no confirm button; "Cancel" only). An action
//            with consequences therefore gets its own confirm() afterwards.
//   pad      the value that is there is replaced by the first digit typed; keyboard: digits . Backspace Enter Esc
//            (and - with signed). The confirm button works only while the value is inside min-max.
//   text     one native input (so the Windows input method writes Chinese names into it) and, under it, a keyboard
//for a finger: letters, digits, . - _ : / @, blank, uppercase, ⌫, clear. Its keys write into the input at the caret
//            and leave the focus there; a real keyboard works at the same time. Enter confirms.
//   opening a dialog while another is open cancels the open one (it resolves to null); only a field of a form opens
//            above its form and returns to it.
//   a dialog that has just appeared ignores taps for 0.35 s (the second tap of a double tap must not answer it).
//   a dialog too tall for the 768 px frame is set tighter, then smaller: nothing ever scrolls or is cut.
//a tap on“STOP” through the scrim closes the dialog and stops (the button is marked data-through).
//after the time-out the footer says "The confirmation window time-out has been canceled and the command has not been sent" (not for message()).

import { h, setText, setClass, pager } from './dom.js';

const CHOICES_PER_PAGE = 8;
const TIMEOUT_S = 30;
const MESSAGE_TIMEOUT_S = 120;
const MESSAGE_CUT = 700;     // characters of a message that are shown
const FIT_HEIGHT = 752;      // a dialog never grows past the 768 px frame
const GUARD_MS = 350;        // a dialog that has just appeared does not take the second tap of a double tap

const stack = [];            // open dialogs; only the last one is on screen (a field of a form opens above its form)
let ticker = 0;
let focusBack = null;
let wired = false;

function host() {
  const overlay = document.getElementById('overlay');
  const box = document.getElementById('dialog');
  if (!overlay || !box) throw new Error('index.html has no #overlay / #dialog');
  if (!wired) {
    wired = true;
    overlay.addEventListener('click', onScrim);
    box.addEventListener('pointerdown', () => touch(), true);
    window.addEventListener('keydown', onKey, true);
  }
  return {overlay, box};
}

const top = () => stack[stack.length - 1] || null;

// False for the first moment a dialog is on screen: the tap that opened it, done twice, must not also answer it.
const settled = (entry) => performance.now() - entry.shownAt >= GUARD_MS;

// ── opening and closing ─────────────────────────────────────────────────────────────────────────────────────
function open(entry, above) {
  return new Promise((resolve) => {
    entry.resolve = resolve;
    host();
    if (!above) closeAll();
    if (!stack.length) focusBack = document.activeElement;
    stack.push(entry);
    show();
  });
}

function closeAll() {
  for (const entry of stack.splice(0).reverse()) entry.resolve(null);
}

// Closes this dialog (and anything opened above it) with the value; the dialog below, if any, comes back.
function finish(entry, value) {
  const at = stack.indexOf(entry);
  if (at < 0) return;
  for (const gone of stack.splice(at).reverse()) gone.resolve(gone === entry ? value : null);
  show();
}

function show() {
  const {overlay, box} = host();
  const entry = top();
  if (!entry) {
    overlay.hidden = true;
    box.hidden = true;
    box.replaceChildren();
    clearInterval(ticker);
    ticker = 0;
    const back = focusBack;
    focusBack = null;
    if (back && back.isConnected && typeof back.focus === 'function') back.focus({preventScroll: true});
    return;
  }
  entry.left = entry.timeout;
  entry.shownAt = performance.now();
  draw(entry, box);
  overlay.hidden = false;
  box.hidden = false;
  fit(box);
  (entry.focus || box).focus({preventScroll: true});
  if (entry.focus && typeof entry.focus.select === 'function') entry.focus.select();
  if (!ticker) ticker = setInterval(tick, 1000);
}

// Everything of a dialog is always on screen and nothing scrolls: a dialog that is too tall for the frame first loses
// its outer margins (snug), then is set tighter (compact), and is made smaller only if that is still not enough.
function fit(box) {
  box.classList.remove('snug', 'compact');
  box.style.setProperty('--fit', '1');
  for (const level of ['snug', 'compact']) {
    if (box.offsetHeight <= FIT_HEIGHT) break;
    box.classList.add(level);
  }
  const height = box.offsetHeight;
  if (height > FIT_HEIGHT) box.style.setProperty('--fit', (FIT_HEIGHT / height).toFixed(4));
}

// ── countdown ───────────────────────────────────────────────────────────────────────────────────────────────
function countdown(entry) {
  if (entry.note) setText(entry.note, entry.silent ? '' : `${entry.left} If not confirmed within seconds, it will be automatically canceled and the command will not be sent.`);
}

function touch() {
  const entry = top();
  if (!entry) return;
  entry.left = entry.timeout;
  countdown(entry);
}

function tick() {
  const entry = top();
  if (!entry) return;
  entry.left -= 1;
  if (entry.left > 0) { countdown(entry); return; }
  const silent = stack.every((item) => item.silent);
  closeAll();
  show();
  //frame.js puts "The confirmation window has timed out and has been canceled, and the command has not been sent" in the footer
  if (!silent) document.dispatchEvent(new CustomEvent('plasmic:dialog-timeout'));
}

// ── scrim and keyboard ──────────────────────────────────────────────────────────────────────────────────────
function onScrim(event) {
  const entry = top();
  if (!entry) return;
  //“STOP” is never blocked by a dialog: a tap on it through the scrim closes the dialog and stops.
  const through = document.elementsFromPoint(event.clientX, event.clientY)
    .find((item) => item instanceof HTMLElement && item.matches('[data-through]') && !item.disabled);
  if (through) {
    closeAll();
    show();
    through.click();
    return;
  }
  if (settled(entry)) finish(entry, null);
}

function onKey(event) {
  const entry = top();
  if (!entry) return;
  event.stopPropagation();            // the page under the dialog gets no keys
  touch();
  if (event.isComposing || event.keyCode === 229) return;   // the IME is still putting a character together
  if (event.key === 'Escape') {
    event.preventDefault();
    finish(entry, null);
  } else if (event.key === 'Tab') {
    trap(event);
  } else if (entry.key) {
    entry.key(event);
  }
}

// Tab stays inside the dialog.
function trap(event) {
  const {box} = host();
  const items = [...box.querySelectorAll('button, input, a[href], [tabindex]:not([tabindex="-1"])')]
    .filter((item) => !item.disabled && item.offsetParent !== null);
  if (!items.length) { event.preventDefault(); return; }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  const inside = box.contains(active) && active !== box;
  if (event.shiftKey && (!inside || active === first)) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && (active === last || (!inside && active !== box))) { event.preventDefault(); first.focus(); }
}

// ── building ────────────────────────────────────────────────────────────────────────────────────────────────
const textOf = (value) => (value === null || value === undefined ? '' : String(value));

function lineList(list) {
  return (list || []).filter(Boolean).map((item) => (Array.isArray(item)
    ? [textOf(item[0]), textOf(item[1])]
    : [textOf(item.label !== undefined ? item.label : item.k), textOf(item.value !== undefined ? item.value : item.v)]));
}

function tickList(list, required) {
  return (list || []).filter(Boolean).map((item) => (typeof item === 'string'
    ? {key: '', label: item, required, on: false}
    : {key: item.key || '', label: textOf(item.label), required: item.required === undefined ? required : Boolean(item.required), on: Boolean(item.value)}));
}

// What every kind of dialog shares.
function entryOf(options, more) {
  const o = options || {};
  const entry = {
    title: textOf(o.title),
    variant: o.variant === 'hv' || o.variant === 'danger' ? o.variant : '',
    wide: Boolean(o.wide),
    lines: lineList(o.lines),
    detail: textOf(o.detail),
    level: o.level || '',
    links: (o.links || []).filter(Boolean),
    warn: (Array.isArray(o.warn) ? o.warn : [o.warn]).filter(Boolean).map(textOf),
    ticks: [],
    actions: (o.actions || []).filter(Boolean),
    confirmLabel: textOf(o.confirmLabel) || "OK",
    cancelLabel: textOf(o.cancelLabel) || "Cancel",
    timeout: Number(o.timeout) > 0 ? Math.round(Number(o.timeout)) : TIMEOUT_S,
    noCancel: false, noConfirm: false, silent: false,
    value: () => true,
    ready: () => true,
    key: null, focus: null, ok: null, note: null, resolve: null, left: 0, shownAt: 0,
  };
  return Object.assign(entry, more);
}

function valid(entry) {
  return entry.ticks.every((tick) => tick.on || !tick.required) && entry.ready();
}

function refreshOk(entry) {
  if (entry.ok) entry.ok.disabled = !valid(entry);
}

function accept(entry) {
  if (top() === entry && settled(entry) && valid(entry)) finish(entry, entry.value());
}

// For the buttons that close the dialog with a fixed value (cancel, a choice, an extra footer button).
function closeWith(entry, value) {
  if (top() === entry && settled(entry)) finish(entry, value);
}

function draw(entry, box) {
  box.className = 'dialog' + (entry.variant === 'hv' ? ' danger hv' : entry.variant === 'danger' ? ' danger' : '') + (entry.wide ? ' wide' : '');
  box.setAttribute('aria-label', entry.title);
  entry.focus = null;
  entry.key = null;
  const body = h('div.dlg-b');
  for (const [label, value] of entry.lines) body.append(h('div.dlg-line', null, h('span', null, label), h('b', null, value)));
  if (entry.fields) drawFields(entry, body);
  if (entry.choices) drawChoices(entry, body, box);
  if (entry.pad) drawPad(entry, body);
  if (entry.text) drawText(entry, body);
  if (entry.detail) body.append(h('div', {class: 'dlg-detail' + (entry.level === 'error' ? ' err' : '')}, entry.detail));
  if (entry.links.length) {
    body.append(h('div.dlg-links', null, entry.links.map((link) => h('a.btn', {href: link.href, attrs: {download: link.download === undefined ? null : link.download}}, textOf(link.label)))));
  }
  for (const text of entry.warn) body.append(h('div.dlg-warn', null, text));
  for (const item of entry.ticks) {
    const mark = h('span.box', null, item.on ? '✓' : '');
    const row = h('button', {class: 'ack-row' + (item.required ? '' : ' plain') + (item.on ? ' on' : ''), attrs: {'aria-pressed': String(item.on)}}, mark, h('span', null, item.label));
    row.addEventListener('click', () => {
      item.on = !item.on;
      setClass(row, 'on', item.on);
      setText(mark, item.on ? '✓' : '');
      row.setAttribute('aria-pressed', String(item.on));
      refreshOk(entry);
    });
    body.append(row);
  }
  const foot = h('div.dlg-f');
  if (!entry.noCancel) foot.append(h('button.cancel', {onclick: () => closeWith(entry, null)}, entry.cancelLabel));
  for (const action of entry.actions) foot.append(h('button.extra', {onclick: () => closeWith(entry, action.value)}, textOf(action.label)));
  entry.ok = entry.noConfirm ? null : h('button.ok', {onclick: () => accept(entry)}, entry.confirmLabel);
  if (entry.ok) foot.append(entry.ok);
  entry.note = h('div.dlg-timeout');
  box.replaceChildren(h('div.dlg-h', null, entry.title), body, foot, entry.note);
  if (!body.firstChild) body.hidden = true;
  refreshOk(entry);
  countdown(entry);
}

// ── choices ─────────────────────────────────────────────────────────────────────────────────────────────────
function drawChoices(entry, body, box) {
  const c = entry.choices;
  // .paged keeps the height of a full page, so the turn buttons stay where they are on a shorter last page
  const grid = h('div', {class: 'dlg-choices grid' + (c.columns === 1 ? ' one' : '') + (c.left ? ' left' : '')
    + (c.items.length > CHOICES_PER_PAGE ? ' paged' : '')});
  const paint = () => {
    const shown = c.items.slice(c.page * CHOICES_PER_PAGE, (c.page + 1) * CHOICES_PER_PAGE);
    grid.replaceChildren(...shown.map((item) => {
      const reason = typeof item.disabled === 'string' ? item.disabled : '';
      const small = reason || textOf(item.small);
      return h('button', {class: item.current ? 'sel' : '', disabled: Boolean(item.disabled), onclick: () => closeWith(entry, item.value)},
        h('span', null, textOf(item.label)), small ? h('small', null, small) : null);
    }));
  };
  body.append(grid);
  if (c.items.length > CHOICES_PER_PAGE) {
    const turn = h('div');
    body.append(turn);
    pager(turn, c.items.length, CHOICES_PER_PAGE, (page) => { c.page = page; paint(); fit(box); }).set(c.page);
  }
  paint();
}

// ── number pad ──────────────────────────────────────────────────────────────────────────────────────────────
function padValue(p) {
  if (p.pin) return /^\d{6,8}$/.test(p.buf) ? p.buf : null;     // a PIN stays text (leading zeros)
  if (!/^-?\d+(\.\d+)?$/.test(p.buf)) return null;
  const value = Number(p.buf);
  const places = (p.buf.split('.')[1] || '').length;
  if (value < p.min || value > p.max || places > p.decimals) return null;
  return value;
}

function padPress(p, key) {
  let buf = p.buf;
  if (key === 'C') buf = '';
  else if (key === '⌫') buf = buf.slice(0, -1);
  else if (key === '±') { if (p.signed) buf = buf.startsWith('-') ? buf.slice(1) : '-' + buf; }
  else if (key === '.') {
    if (!p.decimals) return;
    if (p.fresh) buf = '';
    if (!buf.includes('.')) buf = (buf.replace('-', '') ? buf : buf + '0') + '.';
  } else if (p.pin) {
    buf += key;                                  // every digit of a PIN counts, leading zeros too
  } else {
    if (p.fresh) buf = '';                       // the first digit replaces the value that was there
    buf = (buf === '0' ? '' : buf === '-0' ? '-' : buf) + key;
  }
  p.fresh = false;
  p.buf = buf.slice(0, p.maxLength);
}

function padKeys(p) {
  const digits = ['7', '8', '9', '4', '5', '6', '1', '2', '3'];
  const extra = p.pin ? [] : [p.decimals ? '.' : '', p.signed ? '±' : ''].filter(Boolean);
  if (!extra.length) return [...digits, 'C', '0', '⌫'];
  return [...digits, extra[0], '0', '⌫', 'C', ...extra.slice(1)];
}

function drawPad(entry, body) {
  const p = entry.pad;
  const number = h('span');
  const buffer = h('div.dlg-buf', null, number, p.unit ? h('small', null, p.unit) : null);
  const error = h('span.err');
  const range = p.pin ? "Please enter 6–8 digit repair PIN"
    : `Range ${p.min} – ${p.max}` + (p.decimals ? `, decimal place ${p.decimals}` : ", integer");
  const paint = () => {
    setText(number, p.pin ? '●'.repeat(p.buf.length) || ' ' : p.buf || '0');
    setClass(buffer, 'fresh', p.fresh && p.buf !== '');
    setText(error, p.buf && !p.pin && padValue(p) === null ? "is out of range or does not match the format." : '');
    refreshOk(entry);
  };
  const press = (key) => { padPress(p, key); paint(); };
  const keys = h('div.keys', null, padKeys(p).map((key) => h('button', {
    onpointerdown: (event) => event.preventDefault(),      // the keys never take the focus away from the dialog
    onclick: () => press(key),
  }, key)));
  body.append(buffer, h('div.dlg-range', null, h('span', null, range), error), keys);
  entry.key = (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    let key = null;
    if (/^[0-9]$/.test(event.key)) key = event.key;
    else if (event.key === '.') key = '.';
    else if (event.key === 'Backspace') key = '⌫';
    else if (event.key === 'Delete') key = 'C';
    else if (event.key === '-' && p.signed) key = '±';
    else if (event.key === 'Enter') { event.preventDefault(); accept(entry); return; }
    if (key === null) return;
    event.preventDefault();
    press(key);
  };
  entry.ready = () => padValue(p) !== null;
  entry.value = () => padValue(p);
  paint();
}

function padEntry(o) {
  const decimals = Math.max(0, Math.floor(Number(o.decimals) || 0));
  const min = Number(o.min);
  const max = Number(o.max);
  const signed = Boolean(o.signed);
  const whole = Math.max(String(Math.trunc(Math.abs(min))).length, String(Math.trunc(Math.abs(max))).length);
  let buf = '';
  if (o.value !== null && o.value !== undefined && o.value !== '' && Number.isFinite(Number(o.value))) {
    buf = Number(o.value).toFixed(decimals);
    if (buf.includes('.')) buf = buf.replace(/0+$/, '').replace(/\.$/, '');
  }
  return entryOf(o, {pad: {pin: false, min, max, decimals, signed, unit: textOf(o.unit), buf, fresh: buf !== '',
    maxLength: Math.max(1, whole + (decimals ? decimals + 1 : 0) + (signed || min < 0 ? 1 : 0))}});
}

// ── text ────────────────────────────────────────────────────────────────────────────────────────────────────
// The keyboard on the screen: letters, digits and the signs of a name, an address or a token. Its keys write into the
// native input at its caret and never take the focus from it, so a real keyboard and the Windows input method
//(Chinese names) work as they did; a masked field stays masked. "uppercase" stays on until it is tapped again.
const KB_SHIFT = "uppercase";
const KB_BACK = '⌫';
const KB_SPACE = "blank";
const KB_CLEAR = "Clear";
const KB_ROWS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
  [KB_SHIFT, 'z', 'x', 'c', 'v', 'b', 'n', 'm', KB_BACK],
  ['.', '-', '_', ':', '/', '@', KB_SPACE, KB_CLEAR],
];
const KB_WORDS = [KB_SHIFT, KB_BACK, KB_SPACE, KB_CLEAR];

// What a key does to the input. The selection (the whole value, right after the dialog opened) is replaced.
function kbType(input, key, maxLength) {
  const value = input.value;
  let from = input.selectionStart;
  let to = input.selectionEnd;
  if (typeof from !== 'number' || typeof to !== 'number') { from = value.length; to = value.length; }
  if (key === KB_CLEAR) {
    input.value = '';
    return;
  }
  if (key === KB_BACK) {
    if (from === to) {
      if (from === 0) return;
      from -= 1;
      const low = value.charCodeAt(from);              // one tap takes a whole character, also one made of two code units
      const high = from > 0 ? value.charCodeAt(from - 1) : 0;
      if (low >= 0xdc00 && low <= 0xdfff && high >= 0xd800 && high <= 0xdbff) from -= 1;
    }
    input.setRangeText('', from, to, 'end');
    return;
  }
  const text = key === KB_SPACE ? ' ' : key;
  if (value.length - (to - from) + text.length > maxLength) return;
  input.setRangeText(text, from, to, 'end');
}

function drawKeyboard(input, t) {
  const letters = [];
  let shift = null;
  const press = (key) => {
    if (document.activeElement !== input) input.focus({preventScroll: true});
    if (key === KB_SHIFT) {
      t.upper = !t.upper;
      setClass(shift, 'sel', t.upper);
      shift.setAttribute('aria-pressed', String(t.upper));
      for (const [button, letter] of letters) setText(button, t.upper ? letter.toUpperCase() : letter);
      return;
    }
    kbType(input, /^[a-z]$/.test(key) && t.upper ? key.toUpperCase() : key, t.maxLength);
    input.dispatchEvent(new Event('input', {bubbles: true}));     // the dialog hears it as it hears a real key
  };
  const rows = KB_ROWS.map((keys, at) => h('div', {class: 'kb-row' + (at === 2 ? ' in' : '')}, keys.map((key) => {
    const word = KB_WORDS.includes(key);
    const button = h('button', {
      class: (word ? 'word' : '') + (key === KB_SPACE ? ' space' : '') + (key === KB_SHIFT && t.upper ? ' sel' : ''),
      tabIndex: -1,                                          //for fingers: Tab on a real keyboard goes from the input to Cancel / OK
      onpointerdown: (event) => event.preventDefault(),      // the keys never take the focus (and the caret) from the input
      onclick: () => press(key),
    }, /^[a-z]$/.test(key) && t.upper ? key.toUpperCase() : key);
    if (/^[a-z]$/.test(key)) letters.push([button, key]);
    if (key === KB_SHIFT) { shift = button; button.setAttribute('aria-pressed', String(Boolean(t.upper))); }
    return button;
  })));
  return h('div.kb', null, rows);
}

function drawText(entry, body) {
  const t = entry.text;
  const input = h('input.dlg-input', {type: t.masked && !t.shown ? 'password' : 'text', value: t.value,
    attrs: {maxlength: t.maxLength, autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false'}});
  input.addEventListener('input', () => { t.value = input.value; refreshOk(entry); touch(); });
  const row = h('div.dlg-input-row', null, input);
  if (t.masked) {
    const toggle = h('button.btn', null, t.shown ? "Hide" : "display");
    toggle.addEventListener('click', () => {
      t.shown = !t.shown;
      input.type = t.shown ? 'text' : 'password';
      setText(toggle, t.shown ? "Hide" : "display");
      input.focus();
    });
    row.append(toggle);
  }
  body.append(row);
  if (t.rule) body.append(h('div.dlg-rule', null, t.rule));
  body.append(drawKeyboard(input, t));
  entry.focus = input;
  entry.key = (event) => {
    if (event.key === 'Enter' && document.activeElement === input) { event.preventDefault(); accept(entry); }
  };
  entry.ready = () => t.allowEmpty || t.value.trim() !== '';
  entry.value = () => t.value.trim();
}

function textEntry(o) {
  const maxLength = Number(o.maxLength) > 0 ? Math.floor(Number(o.maxLength)) : 200;
  // always wide: the keyboard needs the 720 px (ten keys of 60 px in a row)
  return entryOf({...o, wide: true}, {text: {value: textOf(o.value).slice(0, maxLength), maxLength,
    rule: textOf(o.rule), masked: Boolean(o.masked), shown: false, upper: false, allowEmpty: Boolean(o.allowEmpty)}});
}

function chooseEntry(o) {
  const items = (o.choices || []).filter(Boolean);
  const at = items.findIndex((item) => item.current || (o.value !== undefined && item.value === o.value));
  if (at >= 0 && o.value !== undefined) items[at] = {...items[at], current: true};
  return entryOf(o, {noConfirm: true, choices: {items, columns: o.columns === 1 ? 1 : 2, left: Boolean(o.left),
    page: at > 0 ? Math.floor(at / CHOICES_PER_PAGE) : 0}});
}

// ── fields of a form ────────────────────────────────────────────────────────────────────────────────────────
function fieldText(field, value) {
  const empty = value === null || value === undefined || value === '';
  if (empty) return textOf(field.empty) || '—';
  if (field.kind === 'choose') {
    const hit = (field.choices || []).find((item) => item && item.value === value);
    return hit ? textOf(hit.label) : textOf(value);
  }
  if (field.masked) return '●'.repeat(Math.min(12, String(value).length));
  return textOf(value) + (field.kind === 'pad' && field.unit ? ` ${field.unit}` : '');
}

function drawFields(entry, body) {
  for (const field of entry.fields) {
    body.append(h('button.dlg-line.tap', {onclick: () => editField(entry, field)},
      h('span', null, textOf(field.label)), h('b', null, fieldText(field, entry.values[field.key]))));
  }
}

async function editField(entry, field) {
  const options = {...field, title: field.title || field.label, value: entry.values[field.key]};
  const sub = field.kind === 'pad' ? padEntry(options) : field.kind === 'choose' ? chooseEntry(options) : textEntry(options);
  const value = await open(sub, true);
  if (value !== null) entry.values[field.key] = value;
  if (top() === entry) show();
}

// ── the seven dialogs ───────────────────────────────────────────────────────────────────────────────────────
export function confirm(options = {}) {
  const entry = entryOf(options);
  entry.ticks = tickList(options.ticks, true);
  return open(entry);
}

export function choose(options = {}) {
  return open(chooseEntry(options));
}

export function pad(options = {}) {
  return open(padEntry(options));
}

export function pin(options = {}) {
  return open(entryOf(options, {pad: {pin: true, min: 0, max: 0, decimals: 0, signed: false, unit: '', buf: '', fresh: false, maxLength: 8}}));
}

export function text(options = {}) {
  return open(textEntry(options));
}

export function form(options = {}) {
  const fields = (options.fields || []).filter(Boolean);
  const values = {};
  for (const field of fields) values[field.key] = field.value === undefined ? '' : field.value;
  const entry = entryOf(options, {fields, values});
  entry.ticks = tickList(options.ticks, false);
  entry.value = () => {
    const result = {...values};
    for (const tick of entry.ticks) if (tick.key) result[tick.key] = tick.on;
    return result;
  };
  return open(entry);
}

export function message(title, body, options = {}) {
  const full = textOf(body);
  const shown = full.length > MESSAGE_CUT ? `${full.slice(0, MESSAGE_CUT)}…(the remaining ${full.length - MESSAGE_CUT} characters have been omitted)` : full;
  return open(entryOf({...options, title, detail: shown, confirmLabel: options.confirmLabel || "Close", timeout: options.timeout || MESSAGE_TIMEOUT_S},
    {noCancel: true, silent: true}));
}
