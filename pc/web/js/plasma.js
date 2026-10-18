// plasma.js — the seven plasma parameters: their table (SPEC E.12), one line that says them, and the dialog that
// changes them.
//
//Maintenance › Plasma parameters shows them as tiles (pages/maint-plasma.js). Scheduled Measurements opens the dialog of this file from its
// measurement tile (AMENDMENTS M17: the person sets them where the schedule is set, without the maintenance PIN).
// Nothing here switches the high voltage on: saving writes the values to the RPi and loads them into the Nano.
// The cross rules (pulses x loops, interval against integration time, total time) are the RPi's: when it refuses,
// its sentence is shown.

// [key sent, label, unit of the pad, lowest, highest, decimals] — the table of SPEC E.12 (keys of 1.4.1's PARAMS).
export const PARAMS = [
  {key: 'Ontime', label: "pulse ON time (ms)", unit: 'ms', min: 1, max: 1000, decimals: 1},
  {key: 'Offtime', label: "pulse OFF time (ms)", unit: 'ms', min: 1, max: 1000, decimals: 1},
  {key: 'Cycle', label: "Number of pulses per round", unit: '', min: 1, max: 10000, decimals: 0},
  {key: 'Loopnum', label: "Number of rounds = number of signal spectra", unit: '', min: 1, max: 1000, decimals: 0},
  {key: 'Integration_Time', label: "Spectral integration time (ms)", unit: 'ms', min: 1, max: 10000, decimals: 0},
  {key: 'Spectra_Interval', label: "spectral interval (ms)", unit: 'ms', min: 1, max: 60000, decimals: 0},
  {key: 'Voltage', label: "discharge voltage (V)\nThe parameters of", unit: 'V', min: 1, max: 700, decimals: 0},
];
export const T_NOT_READ = "RPi have not been read yet: please confirm that the RPi is connected first";
const T_TITLE = "plasma parameters";
const T_SAVED = "saved and synced to Nano";

export const text = (value) => (typeof value === 'number' ? String(Math.round(value * 1000) / 1000) : '—');

// The values the RPi holds, or null while they are not known (no link, or the program does not answer).
export function valuesOf(state, stale) {
  const parameters = state && state.rpi && state.rpi.parameters;
  return !stale && parameters && parameters.values ? parameters.values : null;
}

// The highest voltage the pad takes: 700 V, or less when the RPi says its own limit is lower.
export function topVoltage(state) {
  const limit = state && state.rpi && state.rpi.server && state.rpi.server.limits && state.rpi.server.limits.max_voltage_v;
  return typeof limit === 'number' && limit >= 1 && limit < 700 ? limit : 700;
}

//One line for a tile: "30 V · ON 25/OFF 5 ms × 25 · 5 rounds · Integration 1000 ms · Interval 1500 ms". '' while nothing is known.
export function summary(values) {
  if (!values) return '';
  return `${text(values.Voltage)} V · ON ${text(values.Ontime)}/OFF ${text(values.Offtime)} ms × ${text(values.Cycle)} · `
    + `${text(values.Loopnum)} wheel · integral ${text(values.Integration_Time)} ms · interval ${text(values.Spectra_Interval)} ms`;
}

//The RPi names a parameter by its key ("Spectra_Interval must be an integer millisecond and not less than Integration_Time..."): the same sentence
//with the names of this screen ("The spectral interval must be...not less than the spectral integration time...").
export function inWords(sentence) {
  let out = String(sentence || '');
  for (const item of PARAMS) out = out.split(item.key).join(item.label.replace(/ ([^) ]*) $/, ''));
  return out;
}

//The dialog of scheduled measurement: the seven values in one form, each changed with the number pad; "Save and Sync" sends them.
// -> the program's reply, or null (nothing changed, given up, or refused — the reason was shown).
export async function edit(ctx) {
  const state = ctx.store.state;
  const values = valuesOf(state, ctx.store.stale);
  if (!values) {
    await ctx.dialog.message(T_TITLE, T_NOT_READ, {level: 'warn'});
    return null;
  }
  const top = topVoltage(state);
  const out = await ctx.dialog.form({title: `${T_TITLE}: Click an item to change
Storing`, wide: true, timeout: 120, confirmLabel: "Save and sync\nAfter",
    warn: `will not activate the high voltage; these values will be used for every subsequent measurement (voltage upper limit ${top} V). If the parameters are changed, the concentration calibration done before will no longer be accurate and needs to be recalibrated.`,
    fields: PARAMS.map((item) => ({key: item.key, label: item.label, kind: 'pad', value: values[item.key], min: item.min,
      max: item.key === 'Voltage' ? top : item.max, decimals: item.decimals, unit: item.unit}))});
  if (out === null) return null;
  const parameters = {};
  for (const item of PARAMS) parameters[item.key] = out[item.key];
  if (PARAMS.every((item) => parameters[item.key] === values[item.key])) return null;      // nothing was changed
  let reply;
  try {
    reply = await ctx.api.post('/api/parameters', {parameters}, {timeout: 30000});
  } catch (error) {
    await ctx.dialog.message("Plasma parameters are not saved", inWords(error.message), {level: 'error'});
    return null;
  }
  if (reply.synced === false) ctx.message(`saved, but failed to sync Nano: ${reply.sync_error || ''}`, 'warn');
  else ctx.message(T_SAVED);
  return reply;
}
