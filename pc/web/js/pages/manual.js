//manual.js — Manual operation (SPEC E.6; AMENDMENTS M12: a pump stops by itself after its seconds; M13: the tick row).
//
//   notice bar      why starting is not possible now, or what runs, or the standing warning (first match of E.6.1)
//peristaltic pump eight motors (ON · Quantitative · OFF) and the automatic rinse; direction, flow and seconds of the next “ON”
//syringe pump · weight valve position and volume of the next move, pumping · injecting · initialization; the weight (read-only)
// Every start goes through a confirm dialog and POST /api/manual; the program checks again and its answer is the
// footer message. “OFF” is sent at once, without a dialog. The result of an operation is state.pump.last_result,
// which the frame shows in the footer.

const KEY = 'plasmic.manual';          // {flow, direction, seconds, syringe: {position, volume_ml}} in this PC's browser
const BUSY = ['waiting', 'running', 'pausing', 'stopping'];
const CLEAN_WORD = {valve: "water valve", water: "in water", both: "water inlet and discharge", drain: "last emptied"};
const WAY = {1: "Forward rotation", '-1': "reverse"};
const QUALITY = ['q-ok', 'q-na', 'q-bad', 'q-run'];
const STEP_ML = 0.1;                   // the − / + beside the syringe volume
const SEND_TIMEOUT_MS = 20000;
const ACK_TEXT = "RPi is not connected, and the program cannot confirm that the high voltage is off. I have gone to the site to confirm that the high voltage power supply is turned off.";   // only if the program sent no sentence

let el = null;
let prefs = null;
let sending = false;                   // a start is on its way: a second tap is not a second start

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
const trim = (value, places) => String(Number(Number(value).toFixed(places)));
const signed = (value, places) => (value < 0 ? '' : '+') + value.toFixed(places);
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

function setHidden(node, hidden) {
  if (node.hidden !== hidden) node.hidden = hidden;
}

function setDisabled(node, disabled) {
  if (node.disabled !== disabled) node.disabled = disabled;
}

function setTag(dom, node, text, cls) {
  dom.setText(node, text);
  for (const name of QUALITY) dom.setClass(node, name, name === cls);
}

// ── what the settings file of the pump board and the program allow ──────────────────────────────────────────
function hardwareOf(ctx) {
  return (ctx.store.boot && ctx.store.boot.hardware) || {};
}

function limitsOf(ctx) {
  const hardware = hardwareOf(ctx);
  const limits = (ctx.store.boot && ctx.store.boot.limits) || {};
  return {
    flowMin: limits.flow_min_ml_min || 0.1, flowMax: hardware.max_flow_ml_min || 80, flowDefault: hardware.default_flow_ml_min || 40,
    secondsMax: limits.run_max_s || 300, doseMin: limits.dose_min || 0.01, doseMax: limits.dose_max || 1000,
    positions: (hardware.syringe && hardware.syringe.valve_positions) || 3,
  };
}

function motorsOf(ctx) {
  const list = [];
  for (const group of hardwareOf(ctx).groups || []) {
    for (const motor of group.motors || []) list.push({id: motor.id, label: motor.label, valve: motor.valve, group: group.label || `${group.id} group`});
  }
  return list;
}

function syringeSizeMl(state) {
  const pump = (state && state.pump) || {};
  const size = (pump.syringe && pump.syringe.size_ul) || (pump.settings && pump.settings.syringe_ul) || 5000;
  return size / 1000;
}

// ── what the next start uses (kept in the browser) ──────────────────────────────────────────────────────────
function readPrefs(ctx) {
  const limits = limitsOf(ctx);
  let raw = {};
  try { raw = JSON.parse(window.localStorage.getItem(KEY) || '{}') || {}; } catch (_) { raw = {}; }
  const syringe = raw.syringe && typeof raw.syringe === 'object' ? raw.syringe : {};
  const flow = isNumber(raw.flow) && raw.flow >= limits.flowMin && raw.flow <= limits.flowMax ? Number(raw.flow.toFixed(1)) : limits.flowDefault;
  const seconds = Number.isInteger(raw.seconds) && raw.seconds >= 1 && raw.seconds <= limits.secondsMax ? raw.seconds : Math.min(30, limits.secondsMax);
  const position = Number.isInteger(syringe.position) && syringe.position >= 1 && syringe.position <= limits.positions ? syringe.position : 1;
  const biggest = Math.max(5000, ...((hardwareOf(ctx).syringe && hardwareOf(ctx).syringe.options_ul) || [])) / 1000;   // render() cuts it to the syringe in use
  const volume = isNumber(syringe.volume_ml) && syringe.volume_ml >= 0.001 && syringe.volume_ml <= biggest ? Number(syringe.volume_ml.toFixed(3)) : 1;
  return {flow, direction: raw.direction === -1 ? -1 : 1, seconds, syringe: {position, volume_ml: volume}};
}

function savePrefs(ctx) {
  try { window.localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (_) { /* the browser may refuse its storage */ }
  if (ctx.store.state) render(ctx.store.state, ctx);
}

// ── sending ─────────────────────────────────────────────────────────────────────────────────────────────────
function refusal(error) {
  const reasons = (error.reasons || []).filter((item) => item && item.text);
  if (!reasons.length) return error.message;
  return `${error.message}: ` + reasons.map((item) => (item.fix ? `${item.text} (${item.fix}) ` : item.text)).join('; ');
}

// The tick row of a start dialog while the high voltage cannot be read; the sentence is the program's.
function ackOf(ctx) {
  const state = ctx.store.state;
  const manual = (state && state.interlock && state.interlock.manual) || {};
  if (!manual.need_hv_ack) return {ticks: [], body: {}};
  return {ticks: [manual.hv_ack_text || ACK_TEXT], body: {hv_off_checked: true}};
}

async function start(ctx, body, done) {
  if (sending) return;
  sending = true;
  try {
    const reply = await ctx.api.post('/api/manual', body, {timeout: SEND_TIMEOUT_MS});
    if (done) ctx.message(done(reply));
  } catch (error) {
    ctx.message("Operation failed:" + refusal(error), 'error');
  } finally {
    sending = false;
  }
}

//── dialogs of peristaltic pump ──────────────────────────────────────────────────────────────────────────
async function editFlow(ctx) {
  const limits = limitsOf(ctx);
  const value = await ctx.dialog.pad({title: "continuous operation flow", min: limits.flowMin, max: limits.flowMax, decimals: 1, unit: 'mL/min', value: prefs.flow});
  if (value === null) return;
  prefs.flow = value;
  savePrefs(ctx);
}

async function editSeconds(ctx) {
  const limits = limitsOf(ctx);
  const value = await ctx.dialog.pad({title: "Automatic stop seconds", min: 1, max: limits.secondsMax, decimals: 0, unit: "seconds", value: prefs.seconds});
  if (value === null) return;
  prefs.seconds = value;
  savePrefs(ctx);
}

async function turnOn(ctx, motor) {
  const hardware = hardwareOf(ctx);
  const {flow, direction, seconds} = prefs;
  const ack = ackOf(ctx);
  const yes = await ctx.dialog.confirm({
    title: `Start ${motor.label}`,
    lines: [["direction/flow", `${WAY[direction]} · ${trim(flow, 1)} mL/min`], ["automatic stop", `${seconds} seconds`],
      motor.valve ? ["solenoid valve", `valve ${motor.valve} will open first ${trim((hardware.valve_open_before_ms || 1000) / 1000, 1)} seconds`] : null],
    warn: "When the set seconds are reached, the pump control panel will stop by itself. Please confirm that the pipeline is correct and the physical stop method is available before starting.",
    ticks: ack.ticks, confirmLabel: "start",
  });
  if (!yes) return;
  await start(ctx, {op: 'pump_on', motor: motor.id, flow_ml_min: flow, direction, seconds, ...ack.body},
    () => `Manually start ${motor.label}: ${WAY[direction]} ${trim(flow, 1)} mL/min, ${seconds} automatically stop in seconds`);
}

// Seconds a volume dose takes, as the pump board's own code counts it (steps ÷ speed, plus the linked valve).
function doseSeconds(hardware, motor, ml) {
  const flow = hardware.dose_flow_ml_min || 40;
  let seconds = ml / flow * 60;
  if (hardware.pulses_per_ml > 0 && hardware.dose_pulses_per_ml > 0) {
    seconds = Math.max(1, Math.round(ml * hardware.dose_pulses_per_ml)) / Math.max(1, Math.round(flow * hardware.pulses_per_ml / 60));
  }
  if (motor.valve) seconds += ((hardware.valve_open_before_ms || 0) + (hardware.valve_close_after_ms || 0)) / 1000;
  return Math.ceil(seconds);            //rounded up, as the notice bar counts it (“3 seconds/about 7 seconds”)
}

async function dose(ctx, motor) {
  const hardware = hardwareOf(ctx);
  const limits = limitsOf(ctx);
  const weighing = hardware.gravimetric || {};
  const scale = (ctx.store.state && ctx.store.state.pump && ctx.store.state.pump.scale) || {};
  let why = '';
  if (!weighing.enabled) why = "configuration file does not enable weighing and quantification";
  else if (!scale.online || !isNumber(scale.grams)) why = "scale has no reading value\nThe reading value of";
  else if (!scale.calibrated) why = "scale has not been calibrated yet";
  const way = await ctx.dialog.choose({title: `${motor.label} Quantitative method`,
    choices: [{value: 'volume', label: "Volume (mL)"}, {value: 'weight', label: "weight (g)", disabled: why || false}]});
  if (way === null) return;
  const byWeight = way === 'weight';
  const amount = await ctx.dialog.pad({title: byWeight ? "quantitative weight" : "quantitative volume", min: limits.doseMin, max: limits.doseMax, decimals: 2, unit: byWeight ? 'g' : 'mL'});
  if (amount === null) return;
  const direction = prefs.direction;
  const ack = ackOf(ctx);
  const stop = "If you want to stop midway, press OFF in this column or \"STOP\" at the bottom.";
  const lines = byWeight
    ? [["method", `Weight ${trim(amount, 2)} g (confirm with scale)`], ["direction", "Forward rotation (fixed)"],
      ["speed\nUse this speed for liquid extraction and injection after", `${trim(weighing.fast_flow_ml_min, 1)} → ${trim(weighing.approach_flow_ml_min, 1)} → ${trim(weighing.trim_flow_ml_min, 1)} mL/min`]]
    : [["method", `Volume ${trim(amount, 2)} mL`], ["direction", WAY[direction]], ["speed\nUse this speed for liquid extraction and injection after", `${trim(hardware.dose_flow_ml_min || 40, 1)} mL/min (fixed)`],
      ["estimated", "approx." + ctx.labels.fmtDuration(doseSeconds(hardware, motor, amount))]];
  const yes = await ctx.dialog.confirm({
    title: `${motor.label} Quantitative`, lines,
    warn: byWeight ? `${stop} Please do not touch the scale. Maximum ${trim((weighing.timeout_s || 300) / 60, 1)} minutes.` : stop,
    ticks: ack.ticks, confirmLabel: "Start quantification",
  });
  if (!yes) return;
  await start(ctx, byWeight ? {op: 'dose_weight', motor: motor.id, target_g: amount, ...ack.body}
    : {op: 'dose_volume', motor: motor.id, volume_ml: amount, direction, ...ack.body});
}

// “OFF”: no dialog, and never held back by a start that is still on its way.
async function turnOff(ctx, motor) {
  try {
    await ctx.api.post('/api/manual', {op: 'pump_off', motor: motor.id}, {timeout: SEND_TIMEOUT_MS});
    ctx.message(`Manual stop ${motor.label}`);
  } catch (error) {
    ctx.message("Operation failed:" + refusal(error), 'error');
  }
}

async function clean(ctx) {
  const cleaning = hardwareOf(ctx).cleaning || {};
  const ack = ackOf(ctx);
  const yes = await ctx.dialog.confirm({
    title: "automatic cleaning",
    lines: [["water intrusion", `${ctx.labels.motorShort(cleaning.water_motor)}, first ${cleaning.water_lead_s} alone seconds`],
      ["at the same time", `and then ${ctx.labels.motorShort(cleaning.out_motor)}${cleaning.overlap_s} seconds together with `],
      ["Empty", `last ${ctx.labels.motorShort(cleaning.out_motor)} alone ${cleaning.out_tail_s} seconds`]],
    warn: "Do not stop a single pump during cleaning. To stop, click STOP at the bottom.",
    ticks: ack.ticks, confirmLabel: "Start cleaning",
  });
  if (!yes) return;
  await start(ctx, {op: 'clean', ...ack.body});
}

//── dialogs of syringe pump ────────────────────────────────────────────────────────────────────────────
async function editVolume(ctx) {
  const value = await ctx.dialog.pad({title: "injection volume", min: 0.001, max: syringeSizeMl(ctx.store.state), decimals: 3, unit: 'mL', value: prefs.syringe.volume_ml});
  if (value === null) return;
  prefs.syringe.volume_ml = value;
  savePrefs(ctx);
}

function nudge(ctx, step) {
  const size = syringeSizeMl(ctx.store.state);
  prefs.syringe.volume_ml = Number(clamp(prefs.syringe.volume_ml + step, 0.001, size).toFixed(3));
  savePrefs(ctx);
}

async function syringeMove(ctx, kind) {
  const word = kind === 'aspirate' ? "Liquid extraction" : "injection";
  const syringe = (ctx.store.state && ctx.store.state.pump && ctx.store.state.pump.syringe) || {};
  const {position, volume_ml: volume} = prefs.syringe;
  const speed = isNumber(syringe.speed_ml_s) && syringe.speed_ml_s > 0 ? syringe.speed_ml_s : null;
  const ack = ackOf(ctx);
  const yes = await ctx.dialog.confirm({
    title: `syringe pump ${word}`,
    lines: [["valve position", `valve position ${position}`], ["Volume", `${volume.toFixed(3)} mL`], ["speed\nUse this speed for liquid extraction and injection after", speed ? `${trim(speed, 3)} mL/s` : '—'],
      ["estimated", speed ? "approx." + ctx.labels.fmtDuration(Math.max(1, volume / speed)) : '—']],
    warn: "syringe pump does not have position readback, and the action results must be confirmed on site.",
    ticks: ack.ticks, confirmLabel: word,
  });
  if (!yes) return;
  await start(ctx, {op: 'syringe_move', kind, position, volume_ml: volume, ...ack.body});
}

async function syringeInit(ctx) {
  const ack = ackOf(ctx);
  const yes = await ctx.dialog.confirm({
    title: "syringe pump initialization", variant: 'danger',
    warn: "The piston will push to the top and then return. The liquid in the syringe will be discharged from the current valve position. Please confirm that the outlet is connected to the right place.",
    ticks: ack.ticks, confirmLabel: "initialization",
  });
  if (!yes) return;
  await start(ctx, {op: 'syringe_init', ...ack.body});
}

// ── mount ───────────────────────────────────────────────────────────────────────────────────────────────────
export function mount(root, ctx) {
  const {h} = ctx.dom;
  prefs = readPrefs(ctx);
  const limits = limitsOf(ctx);
  const cleaning = hardwareOf(ctx).cleaning || {};
  const press = (action, ...args) => () => { action(ctx, ...args); };

  // notice
  const noticeText = h('b');
  const noticeFix = h('span.n-fix', {hidden: true});
  const notice = h('div.notice.warn', null, h('div.n-body', null, noticeText, noticeFix));

  //peristaltic pump
  const forward = h('button', {onclick: () => { prefs.direction = 1; savePrefs(ctx); }}, "Forward rotation");
  const reverse = h('button', {onclick: () => { prefs.direction = -1; savePrefs(ctx); }}, "reverse");
  const flow = h('button.btn', {onclick: press(editFlow)});
  const seconds = h('button.btn', {onclick: press(editSeconds)});
  const motors = motorsOf(ctx).map((motor) => {
    const tag = h('span.tag');
    const on = h('button.btn.b-on', {onclick: () => { if (!sending) turnOn(ctx, motor); }}, 'ON');
    const dosing = h('button.btn.b-dose', {onclick: () => { if (!sending) dose(ctx, motor); }}, "Quantitative");
    const off = h('button.btn.b-off', {onclick: press(turnOff, motor)}, 'OFF');
    const box = h('div.prow', null,
      h('div.nm', null, h('b', null, motor.label), h('small', null, motor.valve ? `${motor.group} · Linked solenoid valve ${motor.valve}` : motor.group)),
      tag, on, dosing, off);
    return {motor, box, tag, on, dose: dosing, off};
  });
  const cleanTag = h('span.tag');
  const cleanButton = h('button.btn.b-clean', {onclick: () => { if (!sending) clean(ctx); }}, "Start cleaning");
  const cleanRow = h('div.prow', null,
    h('div.nm', null, h('b', null, "automatic cleaning"),
      h('small', null, `${ctx.labels.motorShort(cleaning.water_motor)} Water inlet → ${ctx.labels.motorShort(cleaning.out_motor)} discharge · About ${cleaning.total_s} seconds`)),
    cleanTag, cleanButton);
  const noBoard = h('div.empty', {hidden: true}, h('b'), h('p'));
  const pumps = h('div.panel.pumps', null,
    h('div.ph', null, h('h2', null, "peristaltic pump"), h('span.sp'), h('div.seg', null, forward, reverse), flow, seconds),
    motors.map((row) => row.box), cleanRow, noBoard);

  //syringe pump
  const syringeSub = h('span.sub');
  const syringeTag = h('span.tag');
  const positions = [];
  for (let n = 1; n <= limits.positions; n += 1) {
    positions.push(h('button', {onclick: () => { prefs.syringe.position = n; savePrefs(ctx); }}, `valve position ${n}`));
  }
  const volumeText = h('span');
  const volume = h('button.vol', {onclick: press(editVolume)}, volumeText, h('small', null, 'mL'));
  const minus = h('button.btn.nudge', {onclick: () => nudge(ctx, -STEP_ML)}, '−');
  const plus = h('button.btn.nudge', {onclick: () => nudge(ctx, STEP_ML)}, '+');
  const aspirate = h('button.btn', {onclick: () => { if (!sending) syringeMove(ctx, 'aspirate'); }}, "Liquid extraction");
  const dispense = h('button.btn', {onclick: () => { if (!sending) syringeMove(ctx, 'dispense'); }}, "injection");
  const init = h('button.btn', {onclick: () => { if (!sending) syringeInit(ctx); }}, "initialization");
  const feedback = h('span');
  const why = h('span.why', {hidden: true});
  const syringePanel = h('div.panel.syr-panel', null,
    h('div.ph', null, h('h2', null, "syringe pump"), syringeSub, h('span.sp'), syringeTag),
    h('div.syr', null,
      h('div.row', null, h('span', null, "valve position"), h('div.seg', null, positions)),
      h('div.row', null, h('span', null, "Volume"), minus, volume, plus),
      h('div.acts', null, aspirate, dispense, init),
      h('div.fb', null, feedback, why)));

  //Weight
  const scaleTag = h('span.tag');
  const scaleValue = h('div.scale-val');
  const scaleNote = h('div.scale-note');
  const scalePanel = h('div.panel.scale-panel', null, h('div.ph', null, h('h2', null, "Weight"), h('span.sp'), scaleTag), scaleValue, scaleNote);

  el = {notice, noticeText, noticeFix, forward, reverse, flow, seconds, motors, cleanRow, cleanTag, cleanButton, noBoard,
    syringeSub, syringeTag, positions, volume, volumeText, aspirate, dispense, init, feedback, why, scaleTag, scaleValue, scaleNote,
    root, hardware: (ctx.store.boot || {}).hardware || null};
  root.replaceChildren(notice, h('div.manual', null, pumps, h('div.side', null, syringePanel, scalePanel)));
}

// ── notice bar: the first line that applies ─────────────────────────────────────────────────────────────────
// block = no start control of the page may be pressed (the rows 1–8 of SPEC E.6.1).
function noticeOf(state, ctx) {
  const pump = state.pump || {};
  const manual = (state.interlock && state.interlock.manual) || {};
  const sched = state.schedule;
  const round = state.round;
  if (ctx.store.stale) return {cls: 'err', text: "program does not respond, manual operation is disabled", block: true};
  if (pump.link === 'unset') return {cls: 'block', text: pump.text || "Pump control board not found: please connect USB cable", block: true};
  if (pump.link === 'disabled' || pump.mode === 'off') return {cls: 'block', text: "Pump control panel is set to not be used: there are no devices that can be operated on this page", block: true};
  if (pump.link !== 'connected') return {cls: 'err', text: `The pump control board is not connected (${pump.text || ''}): startup operations are disabled`, block: true};
  if (pump.motion_uncertain) return {cls: 'err', text: "The status of the pump control board is unknown: please go to the site to confirm first, and then wait for it to reconnect.", block: true};
  if (sched && BUSY.includes(sched.status)) {
    return {cls: 'block', block: true, text: sched.kind === 'calibration'
      ? "Calibration in progress: manual start and deactivation; if manual operation is required, please pause the calibration first" : "Scheduling is in progress: manual start and deactivation; if manual operation is required, please pause the schedule first."};
  }
  if (round && round.status === 'paused') return {cls: 'block', text: "The process is suspended: the pump is still retained by the process, please click \"RESUME\" or \"STOP\" at the bottom", block: true};
  const reasons = (manual.reasons || []).filter((item) => item && item.text && item.code !== 'cleaning');
  if (manual.ok === false && reasons.length) {
    // the high voltage first; any other reason the program names (a maintenance tool that runs) reads the same way
    const voltage = reasons.filter((item) => item.code === 'hv_on' || item.code === 'hv_unknown');
    const list = voltage.length ? voltage : reasons;
    const fixes = [...new Set(list.map((item) => item.fix).filter(Boolean))];
    return {cls: voltage.length ? 'err' : 'block', block: true, text: `Manual start and deactivation: ${list.map((item) => item.text).join('; ')}`,
      fix: (fixes.length ? `processing method: ${fixes.join('; ')}.` : '') + "OFF is still available."};
  }
  if (pump.cleaning) return {cls: 'ok', text: "Automatic cleaning in progress: wait for it to end, or press \"STOP\" at the bottom"};
  if (pump.weight_dose) return {cls: 'ok', text: `Weighing and quantification in progress (${ctx.labels.motorLabel(pump.weight_dose.motor)}): wait for it to end, or press OFF in that column`};
  if (pump.op) return {cls: 'ok', text: pump.op.text || ''};
  if (manual.need_hv_ack) {
    return {cls: 'warn', text: (state.rpi && state.rpi.connected ? "cannot read the status of high voltage power supply" : "RPi not connected")
      + ": The program cannot confirm the high voltage status. Before starting, go to the site to confirm that the high-voltage power supply is turned off."};
  }
  // firmware_tag: the firmware of mega/ (the board watches the scale itself); '' = V7's own firmware.
  if (pump.firmware_tag) return {cls: 'warn', text: "runs in a limited time to the number of seconds, weighs and quantifies to the weight, and is stopped by the pump control board itself. If it doesn't stop: Use the physical switch on the device.\nAfter starting, the"};
  return {cls: 'warn', text: "pump will stop itself after the set number of seconds (counted by the pump control board). The weighing and rationing is still controlled by this computer: it is not guaranteed to stop when the computer or USB cable is interrupted, please prepare a physical stop method."};
}

// ── render ──────────────────────────────────────────────────────────────────────────────────────────────────
//The tag of a row whose board cannot be asked: not in use (grey) or not answering (“?   ”).
function awayTag(ctx, pump, stale) {
  if (!stale && (pump.link === 'unset' || pump.link === 'disabled' || pump.mode === 'off')) return [ctx.labels.pumpText(pump), 'q-na'];
  return ["? Unknown", 'q-bad'];
}

function renderMotor(ctx, row, live, pump, flags) {
  const {dom, labels} = ctx;
  let tag = "Stop";
  let cls = 'q-ok';
  let free = true;                  //ON / Quantitative of this row
  let stop = flags.linked;          // OFF: only a board that is not connected takes it away
  if (flags.stale || !flags.linked || !live) {
    [tag, cls] = awayTag(ctx, pump, flags.stale);
    free = false;
  } else if (live.state === 'off') {
    if (live.blocked) { tag = live.blocked; cls = 'q-na'; free = false; }
  } else {
    free = false;
    cls = 'q-run';
    if (live.state === 'wait') tag = "Valve opening";
    else if (live.state === 'run') {
      if (live.by === 'round') tag = "process control";
      else if (!isNumber(live.left_s)) tag = labels.group.run;
      else tag = live.left_s > 0 ? `running · ${live.left_s} seconds left` : "is running and about to stop";
    } else if (live.state === 'move') tag = "Quantification in progress";
    else if (live.state === 'weigh') {
      const gain = pump.weight_dose && pump.weight_dose.motor === live.id && isNumber(pump.weight_dose.gain_g) ? pump.weight_dose.gain_g : 0;
      tag = `scale weight ${signed(gain, 2)} g`;
    } else if (live.state === 'clean') { tag = "Cleaning"; stop = false; }
    else tag = labels.group[live.state] || String(live.state);
  }
  setTag(dom, row.tag, tag, cls);
  setDisabled(row.on, !(free && flags.allow));
  setDisabled(row.dose, !(free && flags.allow));
  setDisabled(row.off, !stop);
}

function renderSyringe(ctx, state, pump, flags) {
  const {dom} = ctx;
  const syringe = pump.syringe || {};
  const size = syringeSizeMl(state);
  if (prefs.syringe.volume_ml > size) prefs.syringe.volume_ml = size;        //a smaller syringe was set in repair
  const volume = prefs.syringe.volume_ml;

  dom.setText(el.syringeSub, `Syringe ${trim(size, 2)} mL · Speed ${isNumber(syringe.speed_ml_s) ? trim(syringe.speed_ml_s, 3) : '—'} mL/s`);
  if (flags.stale || !flags.linked) setTag(dom, el.syringeTag, ...awayTag(ctx, pump, flags.stale));
  else if (syringe.online === false) setTag(dom, el.syringeTag, "No response", 'q-bad');
  else if (syringe.error !== null && syringe.error !== undefined && syringe.error !== '') setTag(dom, el.syringeTag, `? Error ${syringe.error}`, 'q-bad');
  else if (syringe.busy) setTag(dom, el.syringeTag, "in action", 'q-run');
  else if (syringe.initialised) setTag(dom, el.syringeTag, "has been initialized", 'q-ok');
  else setTag(dom, el.syringeTag, "has not been initialized yet", 'q-na');

  el.positions.forEach((button, index) => dom.setClass(button, 'sel', index + 1 === prefs.syringe.position));
  dom.setText(el.volumeText, volume.toFixed(3));

  const ready = flags.linked && !flags.stale && Boolean(syringe.initialised) && !syringe.busy;
  const inside = isNumber(syringe.volume_ul) ? syringe.volume_ul / 1000 : null;
  const tooMuch = ready && inside !== null && inside + volume > size + 1e-9;      //would overfill
  const tooLittle = ready && inside !== null && volume > inside + 1e-9;           //injection has not that much
  setDisabled(el.aspirate, !(flags.allow && ready && inside !== null && !tooMuch));
  setDisabled(el.dispense, !(flags.allow && ready && inside !== null && !tooLittle));
  setDisabled(el.init, !(flags.allow && flags.linked && !flags.stale && !syringe.busy));
  dom.setText(el.feedback, syringe.initialised || syringe.busy
    ? `Estimate ${inside === null ? '—' : trim(inside, 3)} mL in syringe. The syringe pump does not have position readback. Please go to the site to confirm the action results.`
    : "Please click \"Initialize\" first. Initialization will push the piston to the top: the liquid in the syringe will be discharged from the current valve position.");
  const reasons = [tooMuch ? "The syringe capacity will be exceeded after withdrawing liquid." : '', tooLittle ? "There are not so many in the syringe" : ''].filter(Boolean).join('; ');
  dom.setText(el.why, reasons);
  setHidden(el.why, !reasons);
}

function renderScale(ctx, pump, flags) {
  const {dom} = ctx;
  const scale = pump.scale || {};
  const decimals = (hardwareOf(ctx).scale && hardwareOf(ctx).scale.decimals) || 3;
  const used = !(pump.link === 'unset' || pump.link === 'disabled' || pump.mode === 'off');
  let value = '—';
  let cls = '';
  if (flags.stale) { setTag(dom, el.scaleTag, "? Unknown", 'q-bad'); cls = 'q-na'; }
  else if (!used) { setTag(dom, el.scaleTag, "is not connected", 'q-na'); cls = 'q-na'; }
  else if (!flags.linked || !scale.online || !isNumber(scale.grams)) { setTag(dom, el.scaleTag, "? No response", 'q-bad'); cls = 'q-na'; }
  else {
    setTag(dom, el.scaleTag, scale.stable ? "stable" : "is changing", 'q-ok');
    value = `${scale.grams.toFixed(decimals)} g`;
  }
  dom.setText(el.scaleValue, value);
  dom.setClass(el.scaleValue, 'q-na', cls === 'q-na');
  const uncalibrated = used && flags.linked && !flags.stale && scale.online && scale.calibrated === false;
  dom.setText(el.scaleNote, (uncalibrated ? "has not been calibrated yet." : '') + "Zeroing and calibration are in \"Maintenance › Pumps and Scales\".");
}

export function render(state, ctx) {
  //The pump setup was saved (Maintenance › Pumps and Scales): other names, valves and cleaning pumps. The rows are built again.
  if (el.root && el.hardware !== ((ctx.store.boot || {}).hardware || null)) mount(el.root, ctx);
  if (!el) return;
  const {dom, labels} = ctx;
  const pump = state.pump || {};
  const stale = ctx.store.stale;
  const linked = pump.link === 'connected';
  const notice = noticeOf(state, ctx);
  // A start control may be pressed when no line of the notice bar forbids it and no operation of this page is
  // still running (the program takes one at a time; the notice bar says which one runs).
  const flags = {stale, linked, allow: !notice.block && !pump.op};

  const cls = `notice ${notice.cls}`;
  if (el.notice.className !== cls) el.notice.className = cls;
  dom.setText(el.noticeText, notice.text);
  dom.setText(el.noticeFix, notice.fix || '');
  setHidden(el.noticeFix, !notice.fix);

  dom.setClass(el.forward, 'sel', prefs.direction === 1);
  dom.setClass(el.reverse, 'sel', prefs.direction === -1);
  dom.setText(el.flow, `${trim(prefs.flow, 1)} mL/min`);
  dom.setText(el.seconds, `${prefs.seconds} Automatically stop in seconds`);

  const live = new Map((pump.motors || []).map((motor) => [motor.id, motor]));
  for (const row of el.motors) renderMotor(ctx, row, live.get(row.motor.id), pump, flags);

  const hasBoard = el.motors.length > 0;
  setHidden(el.cleanRow, !hasBoard);
  setHidden(el.noBoard, hasBoard);
  if (!hasBoard) {
    dom.setText(el.noBoard.firstChild, `pump control board ${labels.pumpText(stale ? null : pump)}`);
    dom.setText(el.noBoard.lastChild, pump.error || pump.text || '');
  }
  if (stale || !linked) setTag(dom, el.cleanTag, ...awayTag(ctx, pump, stale));
  else setTag(dom, el.cleanTag, pump.cleaning ? CLEAN_WORD[pump.clean_phase] || CLEAN_WORD.valve : "standby", pump.cleaning ? 'q-run' : 'q-ok');
  dom.setText(el.cleanButton, pump.cleaning ? "Cleaning…" : "Start cleaning");
  setDisabled(el.cleanButton, !(flags.allow && linked && !stale && !pump.cleaning));

  renderSyringe(ctx, state, pump, flags);
  renderScale(ctx, pump, flags);
}
