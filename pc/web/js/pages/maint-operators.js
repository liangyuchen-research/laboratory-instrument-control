//maint-operators.js — Maint › Operators (SPEC E.15): the list of names an operator is chosen from before a schedule
// starts. Adding and deleting both send the whole list (POST /api/operators); the list itself comes with /api/state.

import { act, report, setHidden, setDisabled } from './maint.js';

const ROWS = 8;
const NAME_MAX = 20;
const LIST_MAX = 50;

let ui = null;
let page = null;
let sending = false;

function again() {
  if (ui && page && page.store.state) render(page.store.state, page);
}

const names = (ctx) => ((ctx.store.state && ctx.store.state.operators) || []).slice();

async function send(ctx, list, done) {
  sending = true;
  again();
  await act(ctx, () => ctx.api.post('/api/operators', {operators: list}), done);
  sending = false;
  again();
}

async function add(ctx) {
  const name = await ctx.dialog.text({title: "New operator", maxLength: NAME_MAX, rule: "1–20 characters, Chinese can be typed directly"});
  if (name === null) return;
  const list = names(ctx);
  if (list.includes(name)) { report(`“ ${name} ” is already in the list`); return; }
  if (list.length >= LIST_MAX) { report(`most ${LIST_MAX} people`, 'err'); return; }
  await send(ctx, [...list, name], `has added " ${name} "`);
}

async function remove(ctx, name) {
  const yes = await ctx.dialog.confirm({title: "Delete operator", variant: 'danger', confirmLabel: "Delete", lines: [["Name", name]]});
  if (!yes) return;
  await send(ctx, names(ctx).filter((item) => item !== name), `has deleted " ${name} "`);
}

function makeRow(name) {
  const {h} = page.dom;
  const no = h('span.c-no');
  const text = h('span.c-msg.one');
  const del = h('button.ack', {onclick: () => { remove(page, name); }}, "Delete");
  const node = h('div.trow', null, no, text, h('span.c-del', null, del));
  node.parts = {no, text, del};
  return node;
}

function updateRow(node, name) {
  page.dom.setText(node.parts.no, ui.numbers.get(name));
  page.dom.setText(node.parts.text, name);
  setDisabled(node.parts.del, sending);
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  page = ctx;
  const plus = h('button.btn', {onclick: () => { add(ctx); }}, "New operator");
  const tbody = h('div.tbody');
  const empty = h('div.empty', {hidden: true}, h('b', null, "No operator yet"), h('p', null, "At least one operator is required to start scheduling."));
  const pager = h('div');
  root.replaceChildren(h('div.panel.adm-full.table', null,
    h('div.ph', null, h('h2', null, "operator list"), h('span.sub', null, "You need to select a name before starting the schedule; each measurement and operation event will be recorded."), h('span.sp'), plus),
    h('div.thead', null, h('span.c-no', null, '#'), h('span.c-msg', null, "Name"), h('span.c-del', null, "Delete")),
    tbody, empty, pager));
  ui = {plus, tbody, empty, pager, numbers: new Map()};
}

export function render(state, ctx) {
  const list = state.operators || [];
  ui.numbers = new Map(list.map((name, index) => [name, index + 1]));
  setHidden(ui.tbody, list.length === 0);
  setHidden(ui.empty, list.length > 0);
  const turn = ctx.dom.pager(ui.pager, list.length, ROWS, again);
  ctx.dom.list(ui.tbody, turn.slice(list), (name) => name, makeRow, updateRow);
  setDisabled(ui.plus, sending);
}
