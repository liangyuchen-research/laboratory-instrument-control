// labels.js — the words and formats every page shares.
//
// The status words of schedules, slots, rounds and steps come from /api/bootstrap (one source for every status
// word); the four tables below are empty until main.js has called setup(boot).
//   round[status]  slot[status]  schedule[status]  step[status]
//   nano[state]  fault[code]  priority[1|2|3]  group[state]
//   fmtTime(iso, pattern)  fmtDuration(seconds)  motorLabel(id)  motorShort(id)  pumpText(pump)  pumpClass(pump)  pumpChip(pump)
//   pumpPort(pump)  stopMessage(reply)

export const round = {};
export const slot = {};
export const schedule = {};
export const step = {};

export const nano = Object.freeze({IDLE: "standby", ARMED: "is ready", WAIT_TRIGGER: "Waiting for trigger", RUNNING: "is outputting", DONE: "Complete", FAULT: "fault"});
export const fault = Object.freeze({NONE: "None", HEARTBEAT_LOST: "Heartbeat interrupted", TRIGGER_TIMEOUT: "trigger timeout", JOB_TIMEOUT: "work timeout"});
export const priority = Object.freeze({1: "Emergency", 2: "high", 3: "medium"});
export const group = Object.freeze({idle: "idle", wait: "Valve opening", run: "is running", move: "Quantification in progress"});

let hardware = null;

// main.js calls this once with the bootstrap reply.
export function setup(boot) {
  const tables = (boot && boot.labels) || {};
  for (const [target, name] of [[round, 'round'], [slot, 'slot'], [schedule, 'schedule'], [step, 'step']]) {
    for (const key of Object.keys(target)) delete target[key];
    Object.assign(target, tables[name] || {});
  }
  hardware = (boot && boot.hardware) || null;
}

const two = (n) => String(n).padStart(2, '0');

// fmtTime('2026-10-06T14:20:07+08:00', 'MM-DD HH:MM') -> '10-06 14:20' (this PC's local time; “—” without a time).
// Patterns are written as in the specification: YYYY, MM (month), DD, HH, MM right after “HH:” (minutes), SS.
// Also accepted: mm (minutes), ss (seconds). The first argument may be an ISO text, a Date or milliseconds.
export function fmtTime(iso, pattern = 'MM-DD HH:MM') {
  if (iso === null || iso === undefined || iso === '') return '—';
  const d = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const hour = two(d.getHours());
  const minute = two(d.getMinutes());
  const second = two(d.getSeconds());
  return String(pattern).replace(/YYYY|HH:MM:SS|HH:MM|MM|DD|HH|mm|SS|ss/g, (token) => {
    switch (token) {
      case 'YYYY': return String(d.getFullYear());
      case 'HH:MM:SS': return `${hour}:${minute}:${second}`;
      case 'HH:MM': return `${hour}:${minute}`;
      case 'MM': return two(d.getMonth() + 1);
      case 'DD': return two(d.getDate());
      case 'HH': return hour;
      case 'mm': return minute;
      default: return second;
    }
  });
}

//fmtDuration(139) -> '2 minutes and 19 seconds' · fmtDuration(21) -> '21 seconds'. The caller writes "about" in front where it is an estimate.
export function fmtDuration(seconds) {
  const value = Number(seconds);
  if (seconds === null || seconds === undefined || !Number.isFinite(value)) return '—';
  const total = Math.max(0, Math.round(value));
  return total >= 60 ? `${Math.floor(total / 60)} minutes ${total % 60} seconds` : `${total} seconds`;
}

// The name of a pump as the program gives it (“Pump 8 · CuSO₄”: its number on the screen and what it carries; the id is the
// pin of the board, 2–9); names are never copied into JavaScript.
export function motorLabel(id) {
  for (const item of (hardware && hardware.groups) || []) {
    for (const motor of item.motors || []) if (String(motor.id) === String(id)) return motor.label;
  }
  return `pump (${id})`;
}

// The short name of a pump (“Pump 7”), for a line that names several.
export function motorShort(id) {
  for (const item of (hardware && hardware.groups) || []) {
    for (const motor of item.motors || []) if (String(motor.id) === String(id)) return motor.short || motor.label;
  }
  return `pump (${id})`;
}

const PUMP_PROBLEM = new Set(['missing', 'ambiguous', 'port_busy', 'wrong_device', 'lost', 'error', 'forbidden']);

function pumpKind(pump) {
  if (!pump) return 'unknown';
  if (pump.mode === 'sim') return 'sim';
  if (pump.link === 'connected') return 'connected';
  if (pump.link === 'unset') return 'unset';
  if (pump.link === 'disabled' || pump.mode === 'off') return 'disabled';
  if (pump.link === 'connecting') return 'connecting';
  return PUMP_PROBLEM.has(pump.link) ? 'problem' : 'unknown';
}

const PUMP_TEXT = {connected: "is connected", sim: "Simulation", unset: "not connected", disabled: "Not used", connecting: "Connecting...", problem: "? Connection interrupted", unknown: "? Unknown"};
const PUMP_CLASS = {connected: 'q-ok', sim: 'q-ok', unset: 'q-na', disabled: 'q-na', connecting: 'q-na', problem: 'q-bad', unknown: 'q-bad'};

//The short word for the pump board (state.pump): Connected · Analog · Not connected · Not used · Connecting... · ? The connection is interrupted.
// Nobody chooses its port: the program finds the board by itself, and the screens say which port it is.
//Pass null when the data is stale: “? Unknown”. pumpClass gives the matching color class (q-ok / q-na / q-bad).
export function pumpText(pump) { return PUMP_TEXT[pumpKind(pump)]; }
export function pumpClass(pump) { return PUMP_CLASS[pumpKind(pump)]; }

//The word of the top bar: with the board connected it is its port (“Pump Control Board COMx”), else the short word.
export function pumpChip(pump) {
  return pumpKind(pump) === 'connected' && pump.device ? pump.device : pumpText(pump);
}

//What a port is, in one line: "COMx = Pump Control Board (Arduino Mega 2560)". '' when no port is known.
export function pumpPort(pump) {
  if (!pump || !pump.device) return '';
  const name = String(pump.description || '').replace(/\s*\(COM\d+\)\s*$/i, '').trim();
  return name && name !== pump.device ? `${pump.device} = Pump Control Board (${name})` : `${pump.device} = Pump Control Board`;
}

//The sentence for the footer after“Stop STOP”, built from the reply of POST /api/stop: {text, level}.
// It says only what was read back. Pass null when the request itself got no answer.
export function stopMessage(reply) {
  const tail = "— Please use a physical switch to cut off the power";
  if (!reply) return {text: `⚠ Stop command not acknowledged: program (no response) ${tail}`, level: 'error'};
  const pump = reply.pump || {};
  const rpi = reply.rpi || {};
  const failed = [];
  if (!['confirmed', 'none'].includes(pump.result)) failed.push("pump control board" + (pump.error ? ` (${pump.error}) ` : ''));
  // 'silent' (AMENDMENTS M13): the supply does not answer and the Nano is confirmed quiet, with nothing of ours
  // running: the usual state when the supply is switched off at its mains switch. 'none': the RPi half was not sent.
  if (!['confirmed', 'cancel_sent', 'silent', 'none'].includes(rpi.result)) failed.push('RPi' + (rpi.error ? ` (${rpi.error}) ` : ''));
  if (failed.length) return {text: `⚠ Stop command not acknowledged: ${failed.join(', ')} ${tail}`, level: 'error'};
  const parts = [];
  if (pump.result === 'confirmed') parts.push("Pump has stopped");
  if (rpi.result === 'confirmed') parts.push("high voltage power supply readback OFF");
  else if (rpi.result === 'cancel_sent') parts.push("has sent a stop to the discharge work, waiting for RPi to read back");
  else if (rpi.result === 'silent') parts.push("high voltage power supply does not respond (Nano has confirmed that there is no output)");
  return {text: "has stopped" + (parts.length ? ': ' + parts.join('; ') : '') + (pump.result === 'none' ? "(without pump control board)" : ''),
          level: rpi.result === 'silent' ? 'warn' : 'info'};
}
