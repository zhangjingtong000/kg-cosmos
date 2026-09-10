/**
 * compare_perf.mjs — paired before/after comparison for the M4 optimization.
 *
 * Reads output/perf/raw-before.json and output/perf/raw.json (same session,
 * same environment per protocol), computes per-cell medians for non-warmup
 * loads, and emits a delta table focused on the optimized paths.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const before = JSON.parse(readFileSync(path.join(ROOT, 'output/perf/raw-before.json'), 'utf8'));
const after = JSON.parse(readFileSync(path.join(ROOT, 'output/perf/raw.json'), 'utf8'));

function percentiles(frames) {
  const deltas = frames.slice(1).filter((f) => f > 0 && f < 1000);
  const sorted = [...deltas].sort((a, b) => a - b);
  const pick = (q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  return {
    mean: deltas.reduce((a, b) => a + b, 0) / deltas.length,
    p50: pick(0.5), p95: pick(0.95), p99: pick(0.99),
  };
}

function cellMedians(data) {
  const cells = new Map();
  for (const r of data.records) {
    if (r.error || r.warmup) continue;
    const key = `${r.profile}:${r.scenario}:dpr${r.dpr}`;
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(percentiles(r.frames));
  }
  const out = new Map();
  for (const [key, list] of cells) {
    const med = (name) => {
      const v = list.map((x) => x[name]).sort((a, b) => a - b);
      return v[Math.floor(v.length / 2)];
    };
    out.set(key, { mean: med('mean'), p50: med('p50'), p95: med('p95'), p99: med('p99') });
  }
  return out;
}

const B = cellMedians(before);
const A = cellMedians(after);

const lines = [];
lines.push('# M4 优化前后成对对比（同日同环境成对矩阵）');
lines.push('');
lines.push(`- 基线：${before.records.length} 记录；优化后：${after.records.length} 记录（各含 1 轮预热已剔除）`);
lines.push(`- 浏览器：${before.browserVersion} / 适配器：${before.adapterSample}`);
lines.push('- 优化内容：边刷新去临时对象分配、顶点颜色仅构建期写入、拖拽仅刷新移动节点的相邻边、BFS 距离拓扑缓存、标签类切换仅在变化时执行');
lines.push('');
lines.push('| 档位 | 场景 | 指标 | 优化前 (ms) | 优化后 (ms) | Δ |');
lines.push('|---|---|---|---:|---:|---:|');
for (const [key, b] of [...B].sort()) {
  const a = A.get(key);
  if (!a) continue;
  for (const m of ['mean', 'p50', 'p95', 'p99']) {
    const delta = b[m] > 0 ? ((a[m] - b[m]) / b[m] * 100) : 0;
    lines.push(`| ${key.split(':').join(' / ')} | ${m} | ${b[m].toFixed(2)} | ${a[m].toFixed(2)} | ${delta >= 0 ? '+' : ''}${delta.toFixed(1)}% |`);
  }
}

const report = lines.join('\n');
console.log(report);
writeFileSync(path.join(ROOT, 'output/perf/m4-comparison.md'), report);
