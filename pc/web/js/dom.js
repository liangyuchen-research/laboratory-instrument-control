// dom.js — the small DOM helpers every page uses. Text always goes in as text (textContent), never as markup,
// and styles go through the CSSOM (style.setProperty): the program runs under a strict Content-Security-Policy.

const SVG_NS = 'http://www.w3.org/2000/svg';
const SKIP = new Set([null, undefined, false, true]);

function append(parent, child) {
  if (SKIP.has(child)) return;
  if (Array.isArray(child)) { for (const item of child) append(parent, item); return; }
  parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
}

//h('button.btn.primary', {disabled: true, onclick: fn}, 'save')
// tag: name plus optional .class and #id parts. 'svg' and 'svg:<name>' make SVG elements.
// props: class, text, dataset {..}, attrs {..}, style {..} (CSSOM), on {..} or on<event> functions, hidden / disabled /
//        checked / value / type / href / title ... (set as properties when the element has them, else as attributes).
// children: nodes, strings, numbers, arrays of these; null / false are skipped.
export function h(tag, props, ...children) {
  const parts = String(tag).split(/(?=[.#])/);
  let name = parts.shift() || 'div';
  const svg = name === 'svg' || name.startsWith('svg:');
  if (name.startsWith('svg:')) name = name.slice(4);
  const el = svg ? document.createElementNS(SVG_NS, name) : document.createElement(name);
  for (const part of parts) {
    if (part[0] === '.') el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  if (name === 'button' && !svg) el.type = 'button';
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined) continue;
    if (key === 'class' || key === 'className') {
      for (const item of String(value).split(/\s+/)) if (item) el.classList.add(item);
    } else if (key === 'text') {
      el.textContent = String(value);
    } else if (key === 'dataset') {
      for (const [name2, item] of Object.entries(value)) el.dataset[name2] = String(item);
    } else if (key === 'attrs') {
      for (const [name2, item] of Object.entries(value)) if (item !== null && item !== undefined && item !== false) el.setAttribute(name2, item === true ? '' : String(item));
    } else if (key === 'style') {
      for (const [name2, item] of Object.entries(value)) el.style.setProperty(name2, String(item));
    } else if (key === 'on') {
      for (const [type, handler] of Object.entries(value)) el.addEventListener(type, handler);
    } else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (!svg && key in el && key !== 'list' && key !== 'form') {
      el[key] = value;
    } else if (value !== false) {
      el.setAttribute(key, value === true ? '' : String(value));
    }
  }
  append(el, children);
  return el;
}

// Writes only when the text really changed, so a render every second does not disturb the page.
export function setText(el, text) {
  const value = text === null || text === undefined ? '' : String(text);
  if (el.textContent !== value) el.textContent = value;
  return el;
}

export function setClass(el, name, on) {
  const want = Boolean(on);
  if (el.classList.contains(name) !== want) el.classList.toggle(name, want);
  return el;
}

// ── keyed list ──────────────────────────────────────────────────────────────────────────────────────────────
// list(container, rows, keyFn, makeFn, updateFn)
//   keyFn(row, index) -> key        the same key keeps the same element (it is updated, never built again)
//   makeFn(row, index) -> element   called once per key
//   updateFn(el, row, index)        called for every row at every call (optional)
// While a finger or the mouse button is down inside the container, rows are neither added, removed nor moved: the
// row under the pointer stays where it is until the press ends, then the waiting change is applied.
const lists = new WeakMap();
const pressed = new Set();

function release() {
  for (const state of [...pressed]) {
    pressed.delete(state);
    clearTimeout(state.timer);
    state.pressed = false;
    if (state.waiting) {
      const [container, args] = state.waiting;
      state.waiting = null;
      setTimeout(() => list(container, ...args), 0);   // after the click that belongs to this press
    }
  }
}
window.addEventListener('pointerup', release, true);
window.addEventListener('pointercancel', release, true);
window.addEventListener('blur', release);

export function list(container, rows, keyFn, makeFn, updateFn) {
  let state = lists.get(container);
  if (!state) {
    state = {items: new Map(), pressed: false, waiting: null, timer: 0};
    lists.set(container, state);
    container.addEventListener('pointerdown', () => {
      state.pressed = true;
      pressed.add(state);
      clearTimeout(state.timer);
      state.timer = setTimeout(release, 5000);   // a press that never ends must not freeze the list
    }, true);
  }
  const data = Array.isArray(rows) ? rows : [];
  const next = new Map();
  const order = [];
  let structural = false;
  data.forEach((row, index) => {
    let key = String(keyFn ? keyFn(row, index) : index);
    if (next.has(key)) key += '#' + index;
    let el = state.items.get(key);
    if (!el) {
      structural = true;
      if (state.pressed) return;
      el = makeFn(row, index);
    }
    if (updateFn) updateFn(el, row, index);
    next.set(key, el);
    order.push(el);
  });
  if (!structural) {
    if (next.size !== state.items.size) structural = true;
    else {
      let at = 0;
      for (const el of state.items.values()) if (el !== order[at++]) { structural = true; break; }
    }
  }
  if (!structural) return order;
  if (state.pressed) {
    state.waiting = [container, [rows, keyFn, makeFn, updateFn]];
    return order;
  }
  for (const [key, el] of state.items) if (!next.has(key)) el.remove();
  let last = null;
  for (const el of order) {
    const expected = last ? last.nextSibling : container.firstChild;
    if (el !== expected) container.insertBefore(el, expected);
    last = el;
  }
  state.items = next;
  return order;
}

// ── pager ───────────────────────────────────────────────────────────────────────────────────────────────────
// pager(container, total, perPage, onChange) -> {page, pages, set(page), slice(rows), touchedAt}
// Call it at every render with the current number of rows; the same container keeps its page. Lists never scroll:
//“◀ Previous page” Page a / b “Next page ▶”, both disabled on a single page. onChange(page) runs when the person turns the page.
const pagers = new WeakMap();

export function pager(container, total, perPage, onChange) {
  let p = pagers.get(container);
  if (!p) {
    p = {page: 0, pages: 1, perPage: 1, onChange: null, touchedAt: 0};
    const turn = (step) => {
      const target = Math.min(p.pages - 1, Math.max(0, p.page + step));
      if (target === p.page) return;
      p.page = target;
      p.touchedAt = Date.now();
      paint(p);
      if (p.onChange) p.onChange(p.page);
    };
    p.prev = h('button.btn', {onclick: () => turn(-1)}, "◀ Previous page");
    p.label = h('b');
    p.next = h('button.btn', {onclick: () => turn(1)}, "next page ▶");
    container.classList.add('pager');
    container.replaceChildren(p.prev, p.label, p.next);
    p.api = {
      get page() { return p.page; },
      get pages() { return p.pages; },
      get touchedAt() { return p.touchedAt; },      // when the person last turned the page (ms), 0 = never
      set(page) { p.page = Math.min(p.pages - 1, Math.max(0, Math.floor(Number(page)) || 0)); paint(p); return p.page; },
      slice(rows) { return (rows || []).slice(p.page * p.perPage, (p.page + 1) * p.perPage); },
    };
    pagers.set(container, p);
  }
  p.onChange = onChange || null;
  p.perPage = Math.max(1, Math.floor(Number(perPage)) || 1);
  p.pages = Math.max(1, Math.ceil((Number(total) || 0) / p.perPage));
  p.page = Math.min(p.page, p.pages - 1);
  paint(p);
  return p.api;
}

function paint(p) {
  setText(p.label, `Page ${p.page + 1} / ${p.pages}`);
  const first = p.page <= 0;
  const last = p.page >= p.pages - 1;
  if (p.prev.disabled !== first) p.prev.disabled = first;
  if (p.next.disabled !== last) p.next.disabled = last;
}
