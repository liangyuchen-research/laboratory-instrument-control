// chart.js — the spectrum chart: plain SVG, 1-2-5 ticks, min / max decimation of long spectra.
// The drawing follows the original HMI (plot 566 x 282 at 52, 4 inside a .chart box of 620 x 308; horizontal grid
// only; tick texts are HTML beside the plot); the choice of the shown range follows the 1.4.1 screen.
//
//   drawSpectrum(svgBox, {axis, series: [{values, cls}], range: [nmFrom, nmTo] | null})
//     svgBox   the .chart element. Everything this function draws is replaced at every call; other children (a
//              legend .lgd the page put there) stay
//     axis     wavelengths in nm, rising
//     series   lines, drawn in this order; cls = sl-mean | sl-frame | sl-over | 'sl-over sel'
//     range    part of the axis to show, null = all of it
//   -> {ok: true, from, to, low, high, first, last}   what is on screen (nm, counts, axis indexes)
//    | {ok: false, text}                             nothing was drawn; text is on the plot when there is one

import { h } from './dom.js';

const WIDTH = 566;
const HEIGHT = 282;
const LEFT = 52;
const TOP = 4;
const MARK = 'chart';     // data-chart on every node this module made

export function niceTicks(min, max, count) {
  const raw = (max - min) / count;
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw) || raw;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
  return {ticks, step};
}

export function tickText(value, step) {
  if (Math.abs(value) >= 10000) return (value / 1000).toFixed(step >= 1000 ? 0 : 1) + 'k';
  return value.toFixed(step >= 1 ? 0 : Math.min(3, Math.ceil(-Math.log10(step))));
}

function clear(box) {
  for (const node of [...box.children]) if (node.dataset && node.dataset[MARK]) node.remove();
}

function message(box, text) {
  clear(box);
  if (text) box.append(h('div.msg', {dataset: {[MARK]: '1'}}, text));
  return {ok: false, text: text || ''};
}

export function drawSpectrum(svgBox, {axis, series, range = null} = {}) {
  const lines = (series || []).filter((item) => item && Array.isArray(item.values) && item.values.length);
  if (!Array.isArray(axis) || axis.length < 2 || !lines.length) return message(svgBox, '');

  // The part of the axis that is asked for.
  let first = 0;
  let last = axis.length - 1;
  if (range) {
    const [from, to] = range;
    while (first < axis.length - 1 && axis[first] < from) first += 1;
    while (last > first && axis[last] > to) last -= 1;
    if (axis[first] < from || axis[last] > to || last <= first) return message(svgBox, "This wavelength range is not within the measurement range of the spectrometer");
  }

  let low = Infinity;
  let high = -Infinity;
  for (const line of lines) {
    for (let i = first; i <= last; i += 1) {
      const v = line.values[i];
      if (typeof v !== 'number' || !Number.isFinite(v)) continue;
      if (v < low) low = v;
      if (v > high) high = v;
    }
  }
  if (!Number.isFinite(low)) return message(svgBox, '');
  if (low === high) { low -= 1; high += 1; }
  const margin = (high - low) * 0.06;
  low -= margin;
  high += margin;
  const from = axis[first];
  const to = axis[last] === axis[first] ? axis[first] + 1 : axis[last];
  const px = (nm) => (nm - from) / (to - from) * WIDTH;
  const py = (value) => HEIGHT - (value - low) / (high - low) * HEIGHT;

  const svg = h('svg', {viewBox: `0 0 ${WIDTH} ${HEIGHT}`, width: WIDTH, height: HEIGHT, dataset: {[MARK]: '1'}});
  const made = [svg];
  const yTicks = niceTicks(low, high, 5);
  const xTicks = niceTicks(from, to, 6);
  for (const value of yTicks.ticks) {
    const y = py(value).toFixed(1);
    svg.append(h('svg:line', {class: 'sl-grid', x1: 0, x2: WIDTH, y1: y, y2: y}));
    made.push(h('div.ytk', {dataset: {[MARK]: '1'}, style: {top: `${TOP + py(value)}px`}}, tickText(value, yTicks.step)));
  }
  if (low < 0 && high > 0) {
    const y = py(0).toFixed(1);
    svg.append(h('svg:line', {class: 'sl-zero', x1: 0, x2: WIDTH, y1: y, y2: y}));
  }
  for (const value of xTicks.ticks) {
    made.push(h('div.xtk', {dataset: {[MARK]: '1'}, style: {left: `${LEFT + px(value)}px`}}, tickText(value, xTicks.step)));
  }
  made.push(h('div.unit', {dataset: {[MARK]: '1'}}, 'nm'));

  // About 1.5 points per pixel are enough: each bucket of a long spectrum keeps its lowest and its highest point.
  const bucket = Math.max(1, Math.ceil((last - first + 1) / (WIDTH * 1.5)));
  for (const line of lines) {
    const values = line.values;
    const points = [];
    const put = (i) => {
      const v = values[i];
      if (typeof v === 'number' && Number.isFinite(v)) points.push(px(axis[i]).toFixed(1) + ',' + py(v).toFixed(1));
    };
    for (let start = first; start <= last; start += bucket) {
      const end = Math.min(last, start + bucket - 1);
      if (bucket === 1) { put(start); continue; }
      let lowest = start;
      let highest = start;
      for (let i = start; i <= end; i += 1) {
        if (values[i] < values[lowest]) lowest = i;
        if (values[i] > values[highest]) highest = i;
      }
      if (lowest === highest) put(lowest);
      else if (lowest < highest) { put(lowest); put(highest); }
      else { put(highest); put(lowest); }
    }
    svg.append(h('svg:polyline', {class: line.cls || 'sl-mean', points: points.join(' ')}));
  }

  clear(svgBox);
  svgBox.prepend(...made);
  return {ok: true, from, to, low, high, first, last};
}
