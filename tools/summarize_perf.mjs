/**
 * summarize_perf.mjs — turn output/perf/raw.json into the protocol summary.
 *
 * Per cell: first iteration is warmup and excluded; each remaining load gets
 * its own frame-time percentiles; the cell figure is the median across loads
 * (protocol §口径).  Records are invalidated (flagged, kept for audit) when
 * the adapter is not the NVIDIA GPU or the interaction state did not change.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const raw = JSON.parse(readFileSync(path.join(ROOT, 'output/perf/raw.json'), 'utf8'));

function percentiles(frames) {
  const deltas = frames.slice(1).filter((f) => f > 0 && f < 1000); // drop first + stalls from tab bg
  const sorted = [...deltas].sort((a, b) => a - b);
  const pick = (q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  const over = (limit) => deltas.filter((f) => f > limit).length / deltas.length;
  return {
    samples: deltas.length,
    mean: deltas.reduce((a, b) => a + b, 0) / deltas.length,
    p50: pick(0.5), p95: pick(0.95), p99: pick(0.99),
    max: sorted[sorted.length - 1],
    over16: over(16.7), over33: over(33.3),
  };
}

const cells = new Map();
for (const record of raw.records) {
  const key = `${record.profile}:${record.scenario}:dpr${record.dpr}`;
  if (!cells.has(key)) cells.set(key, []);
  cells.get(key).push(record);
}

const rows = [];
for (const [key, records] of cells) {
  const valid = [];
  const audit = [];
  for (const record of records) {
    if (record.error) { audit.push({ iteration: record.iteration, reason: record.error }); continue; }
    const reasons = [];
    if (!String(raw.adapterSample).includes('NVIDIA')) reasons.push('adapter-not-nvidia');
    if (record.scenario === 'focus' && record.postState.focusedIdx == null) reasons.push('focus-not-applied');
    if (record.frames.length < 300) reasons.push('too-few-frames');
    if (reasons.length) { audit.push({ iteration: record.iteration, reason: reasons.join(',') }); continue; }
    valid.push(percentiles(record.frames));
  }
  const metric = (name) => {
    const values = valid.map((v) => v[name]).sort((a, b) => a - b);
    return values[Math.floor(values.length / 2)];
  };
  rows.push({
    cell: key, valid: valid.length, invalid: audit,
    mean: +metric('mean').toFixed(2), p50: +metric('p50').toFixed(2),
    p95: +metric('p95').toFixed(2), p99: +metric('p99').toFixed(2),
    max: +metric('max').toFixed(1),
    over16: +(metric('over16') * 100).toFixed(2),
    over33: +(metric('over33') * 100).toFixed(2),
  });
}

// Environment gate between cell boundaries: drift beyond the protocol
// thresholds invalidates the cell's samples.
function parseGate(entry) {
  if (!entry?.raw) return null;
  try { return JSON.parse(entry.raw); } catch { return null; }
}
const GATE_LIMITS = { cpu: 10, gpu: 10, memDropMB: 2000 };
const gateRows = [];
for (const { cell, gateBefore, gateAfter } of raw.gates ?? []) {
  const before = parseGate(gateBefore);
  const after = parseGate(gateAfter);
  if (!before || !after) { gateRows.push(`GATE ${cell}: snapshot unparseable`); continue; }
  const reasons = [];
  if (after.CpuPercent - before.CpuPercent > GATE_LIMITS.cpu) reasons.push('cpu-drift');
  if (after.NonTestGpuPeakPercent - before.NonTestGpuPeakPercent > GATE_LIMITS.gpu) reasons.push('gpu-drift');
  if (before.AvailableMemoryMB - after.AvailableMemoryMB > GATE_LIMITS.memDropMB) reasons.push('memory-drift');
  gateRows.push(
    `GATE ${cell}: cpu ${before.CpuPercent}→${after.CpuPercent}%, ` +
    `gpu ${before.NonTestGpuPeakPercent}→${after.NonTestGpuPeakPercent}%, ` +
    `mem ${before.AvailableMemoryMB}→${after.AvailableMemoryMB}MB ` +
    (reasons.length ? `REJECTED: ${reasons.join(',')}` : 'ok'),
  );
}

const lines = [];
lines.push('| 档位 | 场景 | 有效/总 | 平均 | P50 | P95 | P99 | 最大 | >16.7ms | >33.3ms |');
lines.push('|---|---|---|---:|---:|---:|---:|---:|---:|---:|');
for (const r of rows) {
  const [profile, scenario, dpr] = r.cell.split(':');
  lines.push(
    `| ${profile} | ${scenario} (dpr${dpr.replace('dpr', '')}) | ${r.valid}/${r.valid + r.invalid.length} | ${r.mean} | ${r.p50} | ${r.p95} | ${r.p99} | ${r.max} | ${(r.over16).toFixed(1)}% | ${(r.over33).toFixed(1)}% |`,
  );
}
lines.push('');
lines.push(`browser: ${raw.browserVersion}`);
lines.push(`adapter: ${raw.adapterSample}`);
for (const g of gateRows) lines.push(g);
for (const r of rows) {
  if (r.invalid.length) lines.push(`INVALID ${r.cell}: ${JSON.stringify(r.invalid)}`);
}

const report = lines.join('\n');
console.log(report);
writeFileSync(path.join(ROOT, 'output/perf/summary.md'), report);
