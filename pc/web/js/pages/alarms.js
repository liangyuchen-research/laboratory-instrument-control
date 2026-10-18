//alarms.js — Alarms (SPEC E.8): two tabs in one panel.
//
//Alarm list (n) state.alarms.active (the program keeps it; while the page gets no answer the frame adds its own
//alarm ui_stale). "Confirm" and "Confirm All" -> POST /api/alarms/ack
//Operation event GET /api/events?limit=200, asked again when state.revs.events changes while this tab is open

const ALARM_ROWS = 8;
const EVENT_ROWS = 12;
const EVENT_LIMIT = 200;
const RETRY_MS = 5000;

let el = null;
let shown = false;
let tab = 'alarms';
let acking = false;
const alarmFacts = new WeakMap();
const eventFacts = new WeakMap();
const events = {rev: -1, rows: null, pending: false, failedAt: 0};

function setHidden(node, hidden) {
  if (node.hidden !== hidden) node.hidden = hidden;
}

function again(ctx) {
  if (shown && ctx.store.state) render(ctx.store.state, ctx);
}

const stateWord = (alarm) => `${alarm.cleared ? "has been restored" : "is active"} · ${alarm.acked ? "confirmed" : "Not confirmed"}`;

function openAlarm(ctx, alarm) {
  if (!alarm) return;
  const {labels} = ctx;
  ctx.dialog.message(`Alarm · ${labels.priority[alarm.priority] || ''}`, alarm.text || '', {
    level: alarm.priority <= 2 ? 'error' : '',
    lines: [["occurrence time", labels.fmtTime(alarm.since, 'MM-DD HH:MM:SS')], ["area", alarm.area || '—'], ["status", stateWord(alarm)]],
  });
}

async function acknowledge(ctx, body) {
  if (acking) return;
  acking = true;
  try {
    await ctx.api.post('/api/alarms/ack', body, {timeout: 8000});
  } catch (error) {
    ctx.message("Alarm acknowledgment failed:" + error.message, 'error');
  }
  acking = false;
}

function wantEvents(ctx, rev) {
  if (events.pending || (events.rows && events.rev === rev)) return;
  if (Date.now() - events.failedAt < RETRY_MS) return;
  events.pending = true;
  ctx.api.get(`/api/events?limit=${EVENT_LIMIT}`, {timeout: 8000}).then((reply) => {
    events.rows = Array.isArray(reply.events) ? reply.events : [];
    events.rev = rev;
    events.failedAt = 0;
  }, () => {
    events.failedAt = Date.now();
  }).then(() => {
    events.pending = false;
    again(ctx);
  });
}

export function mount(root, ctx) {
  const {h} = ctx.dom;
  const choose = (name) => () => { tab = name; again(ctx); };
  const tabAlarms = h('button', {onclick: choose('alarms')});
  const tabEvents = h('button', {onclick: choose('events')}, "operation event");
  const counts = [1, 2, 3].map((priority) => {
    const text = h('span');
    return {priority, text, box: h('span.count', null, h('i', {class: `p${priority}`}), text)};
  });
  const countBox = h('div.counts', null, counts.map((item) => item.box));
  const eventSub = h('span.sub', {hidden: true}, `Most recent ${EVENT_LIMIT} pen`);
  const ackAll = h('button.btn', {onclick: () => { acknowledge(ctx, {all: true}); }}, "Confirm all");

  const alarmBody = h('div.tbody');
  const alarmEmpty = h('div.empty', {hidden: true}, h('b', null, "No active alarm"),
    h('p', null, "Alarm conditions: Program not responding, RPi or pump control board disconnected, process failure, pump or high pressure stop not confirmed, schedule interrupted or skipped period, Nano failure."));
  const alarmPager = h('div');
  const alarmView = h('div.tab-view', null,
    h('div.thead', null, h('span.c-time', null, "occurrence time"), h('span.c-pri', null, "grade"), h('span.c-area', null, "area"),
      h('span.c-msg', null, "message"), h('span.c-state', null, "status"), h('span.c-act', null, "Action")),
    alarmBody, alarmEmpty, alarmPager);

  const eventBody = h('div.tbody');
  const eventPager = h('div');
  const eventView = h('div.tab-view', {hidden: true},
    h('div.thead', null, h('span.c-time', null, "time"), h('span.c-who', null, "who"), h('span.c-msg', null, "event")),
    eventBody, eventPager);

  el = {tabAlarms, tabEvents, counts, countBox, eventSub, ackAll, alarmBody, alarmEmpty, alarmPager, alarmView, eventBody, eventPager, eventView};
  root.replaceChildren(h('div.panel.table', null,
    h('div.ph', null, h('div.seg.tabs', null, tabAlarms, tabEvents), countBox, eventSub, h('span.sp'), ackAll),
    alarmView, eventView));
}

export function show() { shown = true; }
export function hide() { shown = false; }

function renderAlarms(state, ctx) {
  const {dom, labels} = ctx;
  const alarms = state.alarms || {};
  const active = alarms.active || [];
  const pager = dom.pager(el.alarmPager, active.length, ALARM_ROWS, () => again(ctx));
  setHidden(el.alarmBody, !active.length);
  setHidden(el.alarmEmpty, active.length > 0);
  dom.list(el.alarmBody, pager.slice(active), (alarm) => alarm.id, () => {
    const message = dom.h('span.c-msg.one.tap', {title: "Click to see the complete message", attrs: {role: 'button', tabindex: '0'}});
    const ack = dom.h('button.ack', null, "Confirm");
    const row = dom.h('div.trow', null, dom.h('span.c-time'), dom.h('span.c-pri', null, dom.h('span')), dom.h('span.c-area'), message,
      dom.h('span.c-state'), dom.h('span.c-act', null, ack));
    message.addEventListener('click', () => openAlarm(ctx, alarmFacts.get(row)));
    message.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openAlarm(ctx, alarmFacts.get(row)); }
    });
    ack.addEventListener('click', () => {
      const alarm = alarmFacts.get(row);
      if (alarm && !alarm.acked) acknowledge(ctx, {ids: [alarm.id]});
    });
    return row;
  }, (row, alarm) => {
    alarmFacts.set(row, alarm);
    const [time, priority, area, message, status, action] = row.children;
    dom.setText(time, labels.fmtTime(alarm.since, 'MM-DD HH:MM:SS'));
    const pill = priority.firstChild;
    dom.setText(pill, labels.priority[alarm.priority] || '');
    const cls = `pri p${alarm.priority}`;
    if (pill.className !== cls) pill.className = cls;
    dom.setText(area, alarm.area || '');
    dom.setText(message, alarm.text || '');
    dom.setText(status, stateWord(alarm));
    dom.setClass(row, 'unacked', !alarm.acked);
    setHidden(action.firstChild, Boolean(alarm.acked));
  });
}

function renderEvents(state, ctx) {
  const {dom, labels} = ctx;
  wantEvents(ctx, (state.revs && state.revs.events) || 0);
  const rows = events.rows || [];
  const pager = dom.pager(el.eventPager, rows.length, EVENT_ROWS, () => again(ctx));
  const first = pager.page * EVENT_ROWS;
  // Newest first, and no id: a row is known by its place in the list (a new event moves every line one down).
  dom.list(el.eventBody, pager.slice(rows), (event, index) => first + index, () => {
    const row = dom.h('button.trow.slim', null, dom.h('span.c-time'), dom.h('span.c-who'), dom.h('span.c-msg.one'));
    row.addEventListener('click', () => {
      const event = eventFacts.get(row);
      if (!event) return;
      ctx.dialog.message("operation event", event.text || '', {level: event.level === 'error' ? 'error' : '',
        lines: [["time", labels.fmtTime(event.time, 'MM-DD HH:MM:SS')], ["who", event.actor || '—']]});
    });
    return row;
  }, (row, event) => {
    eventFacts.set(row, event);
    const [time, who, text] = row.children;
    dom.setText(time, labels.fmtTime(event.time, 'MM-DD HH:MM:SS'));
    dom.setText(who, event.actor || '');
    dom.setText(text, event.text || '');
    dom.setClass(text, 'txt-warn', event.level === 'error');
  });
}

export function render(state, ctx) {
  if (!el) return;
  const {dom, labels} = ctx;
  const alarms = state.alarms || {};
  const active = alarms.active || [];
  const counts = alarms.counts || {};
  const isAlarms = tab === 'alarms';
  dom.setText(el.tabAlarms, `alarm list (${active.length})`);
  dom.setClass(el.tabAlarms, 'sel', isAlarms);
  dom.setClass(el.tabEvents, 'sel', !isAlarms);
  for (const item of el.counts) dom.setText(item.text, `${labels.priority[item.priority]} ${counts[`p${item.priority}`] || 0}`);
  setHidden(el.countBox, !isAlarms);
  setHidden(el.ackAll, !isAlarms);
  setHidden(el.eventSub, isAlarms);
  const none = !alarms.unacked;
  if (el.ackAll.disabled !== none) el.ackAll.disabled = none;
  setHidden(el.alarmView, !isAlarms);
  setHidden(el.eventView, isAlarms);
  if (isAlarms) renderAlarms(state, ctx);
  else renderEvents(state, ctx);
}
