//maint-link.js — Maintenance › Connection (SPEC E.11): the RPi (address, diagnosis, search) and the pump board (its port).
// Both panels are drawn from /api/state. The three RPi dialogs are the ones of 1.4.1, moved: same texts, same checks.
// Nobody chooses the port of the pump board: the program finds the one Arduino Mega by itself and the panel says
//which port it is (“COMx=Pump Control Board”). The dialog is for the two cases left: several boards, or not using one.
//It lists Arduino Mega ports only; nothing here opens a port (the program does, after "wiring").

import { act, fail, busyReason, gbtn, setGbtn, kvRow, setValue, setHidden, T_STALE } from './maint.js';

const T_DEMO_RPI = "simulation mode is fixedly connected to the local simulator";
const T_DEMO_BOARD = "simulation mode: use the built-in simulation control board";
const T_NOT_SET = "Pump control board not found\nThe pump control board will restart when";
const T_RESTART = "is connected: the pump will stop and the solenoid valve will close. Please close the serial port monitoring window of Autonomous Lab v7 (run.bat) and Arduino IDE first;"
  + "The same port can only be used by one program at a time.";
const T_SETTINGS_NOTE = "If you have used \"One-click installation\" (Install.cmd), this has been filled in automatically and does not need to be changed. If you don’t know the RPi’s IP, or the IP has changed: click “Automatically find RPi”.";
const T_HOST_RULE = "Example: 192.168.0.82 (connected to the router) or 192.168.1.1 (directly connected with one line)";
const T_DIAGNOSE_NOTE = "Check \"Computer → Network Cable → RPi → Device\" from top to bottom. The first place to hit ✗ is the place to be processed. This check does not issue any instructions to the device.";
const T_NO_MEGA = "Arduino Mega not found. Please check the USB cable of the pump control board and click Refresh.";
const T_SEARCHING = "Automatically searching...";
const T_FW_OURS = "(Weighing and quantification are determined by the board itself)";
const T_FW_STOCK = "v7 original version (weighing and quantification are checked by computer)";
const UNKNOWN = "? Unknown";

let ui = null;

// “Arduino Mega 2560 (COMx)” without the port, which the row says already.
function boardName(port) {
  return String((port && port.description) || '').replace(/\s*\(COM\d+\)\s*$/i, '').trim();
}

// The page token, the saved address and "is a token set" come from /api/bootstrap: read it again after a change.
function refreshBoot(ctx) {
  if (typeof ctx.api.bootstrap === 'function') ctx.api.bootstrap().catch(() => {});
}

// ── RPi ─────────────────────────────────────────────────────────────────────────────────────────────────────
async function openSettings(ctx) {
  const config = (ctx.store.boot || {}).config || {};
  const rpi = (ctx.store.state && ctx.store.state.rpi) || {};
  const tokenHint = config.token_set ? "has already saved a set of tokens; leave blank = unchanged" : "has not been set yet; attach the RPi token";
  const out = await ctx.dialog.form({title: "RPi connection settings", confirmLabel: "Save and reconnect", detail: T_SETTINGS_NOTE,
    fields: [
      {key: 'rpi_host', label: "RPi address", kind: 'text', value: rpi.host || config.rpi_host || '', maxLength: 253, rule: T_HOST_RULE},
      {key: 'rpi_port', label: "TCP port", kind: 'pad', value: rpi.port || config.rpi_port || 8767, min: 1, max: 65535, decimals: 0},
      {key: 'token', label: 'token', kind: 'text', masked: true, value: '', allowEmpty: true, maxLength: 256, empty: tokenHint, rule: tokenHint},
    ],
    ticks: [{key: 'clear_token', label: "Clear stored tokens", value: false}]});
  if (!out) return;
  const body = {rpi_host: String(out.rpi_host || '').trim(), rpi_port: Number(out.rpi_port)};
  if (out.token) body.token = String(out.token).trim();
  if (out.clear_token) body.clear_token = true;
  const reply = await act(ctx, () => ctx.api.post('/api/rpi/config', body), "has been saved, reconnecting...");
  if (reply) refreshBoot(ctx);
}

// A dialog that only says "wait": {closed} turns true when the person (or another dialog) closed it.
function waitDialog(ctx, title, text) {
  const state = {closed: false};
  ctx.dialog.message(title, text, {wide: true}).then(() => { state.closed = true; });
  return state;
}

async function openDiagnose(ctx) {
  for (;;) {
    const wait = waitDialog(ctx, "connection diagnosis", "Checking, it will take about a few seconds...");
    let steps;
    try {
      steps = (await ctx.api.post('/api/rpi/diagnose', {})).steps || [];
    } catch (error) {
      if (error && error.status === 403) { fail(ctx, error); return; }
      if (wait.closed) { fail(ctx, error); return; }
      const retry = await ctx.dialog.message("connection diagnosis", "check failed:" + error.message, {wide: true, level: 'error',
        actions: [{label: "Recheck", value: 'again'}]});
      if (retry !== 'again') return;
      continue;
    }
    if (wait.closed) return;                               // closed while it was checking: nothing more to show
    const lines = steps.map((step) => {
      const mark = step.ok === true ? '✓' : step.ok === false ? '✗' : '–';
      const fix = step.fix && step.ok !== true ? `How to deal with: ${step.fix}` : '';
      return [`${mark} ${step.title}`, `${step.detail || ''}${fix}`];
    });
    const answer = await ctx.dialog.message("connection diagnosis", T_DIAGNOSE_NOTE, {wide: true, lines, actions: [{label: "Recheck", value: 'again'}]});
    if (answer !== 'again') return;
  }
}

async function openFind(ctx) {
  const wait = waitDialog(ctx, "automatically finds RPi", "Looking for... (up to 20 seconds)");
  let reply;
  try {
    reply = await ctx.api.post('/api/rpi/find', {});
  } catch (error) {
    if (!wait.closed && !(error && error.status === 403)) ctx.dialog.message("automatically finds RPi", "search failed:" + error.message, {wide: true, level: 'error'});
    else fail(ctx, error);
    return;
  }
  if (wait.closed) return;
  const found = reply.found || [];
  if (!found.length) {
    ctx.dialog.message("automatically finds RPi", "not found. Please confirm the network cable, RPi power supply, and try again.", {wide: true});
    return;
  }
  const row = await ctx.dialog.choose({title: "automatically finds RPi", wide: true, columns: 1, choices: found.map((item) => {
    const same = item.host === reply.host;
    const notes = [same && "Currently", item.sim && "simulator", item.allowed === false && "This computer is not in its allowed list: please re-execute Install.cmd"].filter(Boolean);
    return {value: item, current: same, small: notes.join(' · '),
      label: [item.host, item.name, item.version && `version ${item.version}`].filter(Boolean).join(' · ')};
  })});
  if (!row || row.host === reply.host) return;
  const yes = await ctx.dialog.confirm({title: `uses ${row.host} instead`,
    lines: [["address", `${row.host}:${row.port || reply.port}`], ["Name", row.name || '—'], ["version", row.version || '—']],
    warn: row.allowed === false ? "This computer is not in its allowed list: please re-execute Install.cmd" : ''});
  if (!yes) return;
  // The same port and no token field: the saved token stays.
  const done = await act(ctx, () => ctx.api.post('/api/rpi/config', {rpi_host: row.host, rpi_port: Number(row.port || reply.port)}),
    `has been changed to connect to ${row.host}, reconnecting...`);
  if (done) refreshBoot(ctx);
}

// ── pump board ──────────────────────────────────────────────────────────────────────────────────────────────
async function openPorts(ctx) {
  const OFF = {off: true};
  const AGAIN = {again: true};
  for (;;) {
    let reply;
    try {
      reply = await ctx.api.get('/api/pump/ports');
    } catch (error) {
      fail(ctx, error);
      return;
    }
    const ports = reply.ports || [];
    const current = reply.current || {};
    const choices = ports.map((port) => ({value: port, current: Boolean(port.chosen),
      label: [port.device, boardName(port)].filter(Boolean).join(' · '), small: port.chosen ? "pump control board · In use" : "Use this piece instead"}));
    const unused = current.enabled === false;
    choices.push({value: OFF, label: "does not use pump control board", small: unused ? "only does discharge measurement · Currently" : "only does discharge measurement", current: unused});
    const other = Number(reply.other_count) || 0;
    const detail = ports.length ? '' : T_NO_MEGA + (other > 0 ? `There are ${other} serial devices that are not Arduino Megas on this computer and the program will not open them.` : '');
    const picked = await ctx.dialog.choose({title: "pump control board: replace it with another one, or don’t use it", wide: true, columns: 1, timeout: 120, choices, detail,
      actions: [{label: "Refresh", value: AGAIN}]});
    if (picked === AGAIN) continue;
    if (picked === null) return;
    if (picked === OFF) {
      const yes = await ctx.dialog.confirm({title: "does not use pump control board",
        detail: "Manually operated pumps, syringe pumps, and scales are deactivated; recipes containing pump steps cannot be executed. Discharge-only recipes are not affected."});
      if (yes) await act(ctx, () => ctx.api.post('/api/pump/config', {enabled: false}), "pump control board set to not used");
      return;
    }
    const yes = await ctx.dialog.confirm({title: `wired to ${picked.device}`, variant: 'danger', confirmLabel: "connection",
      lines: [["device", picked.description || picked.device]], warn: T_RESTART});
    if (yes) await act(ctx, () => ctx.api.post('/api/pump/config', {enabled: true, device: picked.device}), `pump control board is set to use ${picked.device}, connecting...`);
    return;
  }
}

async function reconnect(ctx) {
  const yes = await ctx.dialog.confirm({title: "Rewire Pump Control Board", variant: 'danger', confirmLabel: "Reconnect", warn: T_RESTART});
  if (yes) await act(ctx, () => ctx.api.post('/api/pump/reconnect', {}), "The pump control board has been asked to rewire");
}

// ── the module ──────────────────────────────────────────────────────────────────────────────────────────────
export function mount(root, ctx) {
  const {h} = ctx.dom;
  const rpiSub = h('span.sub');
  const rpi = {link: kvRow(ctx, "connection"), version: kvRow(ctx, "RPi program version"), flow: kvRow(ctx, "measurement process"), ip: kvRow(ctx, "The IP of this computer"),
    token: kvRow(ctx, 'token'), age: kvRow(ctx, "last updated"), error: kvRow(ctx, "Recent errors")};
  const settings = gbtn(ctx, "connection settings", "RPi address, port and token", () => { openSettings(ctx); });
  const diagnose = gbtn(ctx, "connection diagnosis", "Check the device step by step", () => { openDiagnose(ctx); });
  const find = gbtn(ctx, "automatically finds RPi", "Search on this computer's network", () => { openFind(ctx); });
  const left = h('div.panel.adm-half', null,
    h('div.ph', null, h('h2', null, "RPi (plasma)"), rpiSub),
    h('div.kv.big', null, Object.values(rpi).map((item) => item.row)),
    h('div.btn-grid', null, settings.button, diagnose.button, find.button));

  const pumpSub = h('span.sub');
  const pump = {state: kvRow(ctx, "status"), port: kvRow(ctx, "port"), firmware: kvRow(ctx, "firmware"),
    syringe: kvRow(ctx, "syringe pump"), scale: kvRow(ctx, "scale"), error: kvRow(ctx, "Recent errors")};
  const again = gbtn(ctx, "Reconnect", "pump control board will restart", () => { reconnect(ctx); });
  const port = gbtn(ctx, "Replace it/not use it", "Normally, you don’t need to press it: the program will find it by itself.", () => { openPorts(ctx); });
  const note = h('div.adm-note', {hidden: true}, T_DEMO_BOARD);
  const right = h('div.panel.adm-half', null,
    h('div.ph', null, h('h2', null, "Pump Control Board (Arduino Mega)"), pumpSub),
    h('div.kv.big', null, Object.values(pump).map((item) => item.row)),
    h('div.btn-grid', null, again.button, port.button), note);

  root.replaceChildren(h('div.adm-body', null, left, right));
  ui = {rpiSub, rpi, settings, diagnose, find, pumpSub, pump, port, again, note};
}

export function render(state, ctx) {
  const {setText} = ctx.dom;
  const stale = ctx.store.stale;
  const boot = ctx.store.boot || {};
  const demo = Boolean(state.demo || boot.demo);
  const busy = busyReason(ctx, state);

  // RPi
  const rpi = state.rpi || {};
  const sim = Boolean(rpi.server && rpi.server.sim);
  const flow = rpi.flow || {};
  setText(ui.rpiSub, rpi.host ? `${rpi.host}:${rpi.port}` : '');
  if (stale) {
    for (const key of ['link', 'version', 'flow', 'age']) setValue(ctx, ui.rpi[key], UNKNOWN, 'q-bad');
  } else {
    setValue(ctx, ui.rpi.link, rpi.connected ? (sim ? "simulator" : "is connected") : "not connected", rpi.connected ? '' : 'q-na');
    setValue(ctx, ui.rpi.version, (rpi.connected && rpi.server && rpi.server.version) || '—');
    setValue(ctx, ui.rpi.flow, flow.text || '—', !rpi.connected ? 'q-na' : flow.ok === false ? 'q-bad' : '', flow.text);
    const age = rpi.status_age_s;
    setValue(ctx, ui.rpi.age, typeof age === 'number' ? `${Math.round(age)} seconds ago` : '—');
  }
  const addresses = (rpi.addresses || []).join(', ');
  setValue(ctx, ui.rpi.ip, addresses || '—', '', addresses);
  const tokenSet = Boolean(boot.config && boot.config.token_set);
  setValue(ctx, ui.rpi.token, tokenSet ? "has been set" : "Not set", tokenSet ? '' : 'q-na');
  setValue(ctx, ui.rpi.error, rpi.error || '—', '', rpi.error);
  setGbtn(ctx, ui.settings, demo ? T_DEMO_RPI : busy);
  setGbtn(ctx, ui.diagnose, stale ? T_STALE : '');
  setGbtn(ctx, ui.find, demo ? T_DEMO_RPI : stale ? T_STALE : '');

  // pump board
  const pump = state.pump || {};
  const link = pump.link;
  const connected = link === 'connected';
  const quality = (ctx.labels.pumpClass ? ctx.labels.pumpClass(pump) : '') || '';
  setText(ui.pumpSub, link === 'disabled' ? "Not used" : pump.device || T_SEARCHING);
  if (stale) {
    for (const key of ['state', 'firmware', 'syringe', 'scale']) setValue(ctx, ui.pump[key], UNKNOWN, 'q-bad');
  } else {
    setValue(ctx, ui.pump.state, pump.text || ctx.labels.pumpText(pump), quality === 'q-ok' ? '' : quality, pump.text);
    // One line for the firmware: which one answers, and a warning when its settings are not this PC's.
    const wrong = pump.firmware_ok !== true && (pump.firmware_ok === false || link === 'wrong_device');
    const kind = pump.firmware_ok === true ? (pump.firmware_tag ? `Plasmic ${pump.firmware_tag}${T_FW_OURS}` : T_FW_STOCK) : wrong ? "is not a pump control board" : '—';
    const differs = pump.firmware_ok === true && pump.firmware_match === false;
    setValue(ctx, ui.pump.firmware, differs ? `${kind} · ⚠ The settings are different from those on the computer` : kind, wrong ? 'q-bad' : differs ? 'txt-warn' : '');
    const syringe = (pump.syringe || {}).online;
    setValue(ctx, ui.pump.syringe, syringe === true ? "responded" : syringe === false ? "No response" : '—', syringe === false ? 'q-bad' : '');
    const scale = Boolean((pump.scale || {}).online);
    setValue(ctx, ui.pump.scale, connected ? (scale ? "has a reading value" : "has no reading value") : '—', connected && !scale ? 'q-bad' : '');
  }
  //What the port is, said by the screen itself: "COMx = Pump Control Board (Arduino Mega 2560)".
  const where = demo ? [pump.device, pump.description].filter(Boolean).join(' · ') : ctx.labels.pumpPort(pump);
  setValue(ctx, ui.pump.port, link === 'unset' ? T_SEARCHING : link === 'disabled' ? "Not used" : where || T_SEARCHING,
    link === 'unset' || link === 'disabled' || !where ? 'q-na' : '', where);
  setValue(ctx, ui.pump.error, pump.error || '—', '', pump.error);
  // Practice mode: both are off and the note of the panel says why; the small lines keep their hints.
  setGbtn(ctx, ui.port, demo ? ui.port.hint : busy);
  setGbtn(ctx, ui.again, demo ? ui.again.hint : busy || (link === 'unset' || link === 'disabled' ? T_NOT_SET : ''));
  setHidden(ui.note, !demo);
}
