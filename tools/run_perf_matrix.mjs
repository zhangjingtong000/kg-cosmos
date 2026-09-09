/**
 * run_perf_matrix.mjs — Clean-ceiling performance matrix driver.
 *
 * Drives the REAL production page (web/index3d-bounce.html) per
 * docs/KG_PERFORMANCE_TEST_PROTOCOL.md: fixed-seed tier data via the
 * ?benchmark= URL, real canvas mouse events, 6 independent loads per cell
 * (first is warmup and excluded from summaries), 10s sampling windows.
 *
 * Environment gate: capture-benchmark-environment.ps1 runs before/after each
 * cell; samples are tagged with the gate verdict instead of silently dropped.
 *
 * Output: output/perf/raw.json (per-iteration records) + summary to stdout.
 *
 * Usage: node tools/run_perf_matrix.mjs [--cell profile:scenario:dpr ...]
 */

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 52731;
const SAMPLE_MS = 10_000;
const ITERS = 6;
const SEED = 20260730;

const MATRIX = [
  ...['S0', 'S1', 'S2', 'S3', 'S4'].flatMap((p) =>
    ['idle', 'focus', 'drag-return'].map((s) => ({ profile: p, scenario: s, dpr: 1 }))),
  ...['S0', 'S2'].flatMap((p) =>
    ['idle', 'orbit', 'focus', 'drag-return'].map((s) => ({ profile: p, scenario: s, dpr: 2 }))),
];

const argv = process.argv.slice(2);
const onlyArg = argv.indexOf('--cell');
const cells = onlyArg >= 0
  ? argv[onlyArg + 1].split(',').map((token) => {
      const [profile, scenario, dpr] = token.split(':');
      return { profile, scenario, dpr: Number(dpr) };
    })
  : MATRIX;

// ── static server (repo root) ────────────────────────────
const MIME = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg' };
const server = createServer(async (req, res) => {
  try {
    const urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const file = path.join(ROOT, urlPath === '/' ? 'web/index.html' : urlPath.slice(1));
    const body = await readFile(file);
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404); res.end('not found');
  }
});
await new Promise((resolve) => server.listen(PORT, '127.0.0.1', resolve));

// ── playwright ───────────────────────────────────────────
const { chromium } = await import('playwright');
let browser;
try {
  browser = await chromium.launch({ channel: 'chrome', headless: false });
} catch {
  browser = await chromium.launch({ headless: false });
}
const browserVersion = browser.version();
console.log(`browser: ${browserVersion}`);

// ── hub index per profile (deterministic fixture) ────────
const { createGraphProfile } = await import(
  pathToFileURL(path.join(ROOT, 'web/kg-data/graph-profiles.mjs'))
);
function hubIndex(profile) {
  const g = createGraphProfile(profile, SEED);
  const degree = new Array(g.nodes.length).fill(0);
  for (const [from, to] of g.edges) {
    degree[from] += 1;
    degree[to] += 1;
  }
  return degree.indexOf(Math.max(...degree));
}
const HUBS = Object.fromEntries(['S0', 'S1', 'S2', 'S3', 'S4'].map((p) => [p, hubIndex(p)]));

// ── environment snapshots ────────────────────────────────
function envSnapshot(label) {
  try {
    const raw = execFileSync('powershell', [
      '-NoProfile', '-File', path.join(ROOT, 'tools/capture-benchmark-environment.ps1'),
      '-DurationSeconds', '6',
    ], { encoding: 'utf8', timeout: 60_000 });
    return { label, at: new Date().toISOString(), raw: raw.trim() };
  } catch (exc) {
    return { label, at: new Date().toISOString(), error: String(exc).slice(0, 300) };
  }
}

// ── page session helpers ─────────────────────────────────
const INIT_SCRIPT = `
  window.__kgFrames = [];
  window.__kgLongTasks = [];
  window.__kgSampling = false;
  window.__kgLastT = 0;
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) window.__kgLongTasks.push(e.duration);
  }).observe({ entryTypes: ['longtask'] });
  (function loop(t) {
    if (window.__kgLastT && window.__kgSampling) window.__kgFrames.push(t - window.__kgLastT);
    window.__kgLastT = t;
    requestAnimationFrame(loop);
  })(0);
`;

async function runIteration(ctx, cell, iteration) {
  const page = await ctx.newPage();
  await page.addInitScript(INIT_SCRIPT);
  const url = `http://127.0.0.1:${PORT}/web/index3d-bounce.html?benchmark=${cell.profile}&seed=${SEED}`;
  await page.goto(url, { waitUntil: 'load', timeout: 60_000 });
  await page.waitForFunction(() => window.__kgBenchmark && window.__kgBenchmark.getState().nodes > 0, null, { timeout: 60_000 });
  await page.waitForTimeout(2_500); // settle entry animation before idle/focus sampling

  const state = await page.evaluate(() => window.__kgBenchmark.getState());

  if (cell.scenario === 'idle') {
    await page.evaluate(() => { window.__kgFrames = []; window.__kgSampling = true; });
    await page.waitForTimeout(SAMPLE_MS);
  } else if (cell.scenario === 'focus') {
    const hub = HUBS[cell.profile];
    const pos = await page.evaluate((i) => window.__kgBenchmark.getNodeScreenPosition(i), hub);
    await page.evaluate(() => { window.__kgFrames = []; window.__kgSampling = true; });
    await page.mouse.click(pos.x, pos.y);
    await page.waitForTimeout(SAMPLE_MS);
  } else if (cell.scenario === 'drag-return') {
    const hub = HUBS[cell.profile];
    const pos = await page.evaluate((i) => window.__kgBenchmark.getNodeScreenPosition(i), hub);
    await page.evaluate(() => { window.__kgFrames = []; window.__kgSampling = true; });
    await page.mouse.move(pos.x, pos.y);
    await page.mouse.down();
    for (let step = 1; step <= 16; step += 1) {
      await page.mouse.move(pos.x + step * 10, pos.y + step * 5);
      await page.waitForTimeout(30);
    }
    await page.mouse.up();
    await page.waitForTimeout(SAMPLE_MS);
  } else if (cell.scenario === 'orbit') {
    await page.evaluate(() => { window.__kgFrames = []; window.__kgSampling = true; });
    await page.mouse.move(960, 540);
    await page.mouse.down();
    for (let step = 0; step < 60; step += 1) {
      await page.mouse.move(960 + 200 * Math.cos(step / 9), 540 + 120 * Math.sin(step / 9));
      await page.waitForTimeout(100);
    }
    await page.mouse.up();
    await page.waitForTimeout(4_000);
  }

  const sample = await page.evaluate(() => {
    const frames = window.__kgFrames.slice();
    return { frames, longTasks: window.__kgLongTasks.slice() };
  });
  const postState = await page.evaluate(() => window.__kgBenchmark.getState());
  await page.close();
  return { ...cell, iteration, warmup: iteration === 1, state, postState, frames: sample.frames, longTasks: sample.longTasks };
}

// ── main ─────────────────────────────────────────────────
mkdirSync(path.join(ROOT, 'output/perf'), { recursive: true });
const records = [];

for (const cell of cells) {
  const gateBefore = envSnapshot(`before ${cell.profile}:${cell.scenario}:dpr${cell.dpr}`);
  const ctx = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: cell.dpr,
  });
  // Confirm the WebGL adapter once per context (protocol hard requirement)
  const adapterPage = await ctx.newPage();
  const adapter = await adapterPage.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl');
    const ext = gl?.getExtension('WEBGL_debug_renderer_info');
    return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
  }).catch(() => null);
  await adapterPage.close();

  for (let iteration = 1; iteration <= ITERS; iteration += 1) {
    const started = Date.now();
    try {
      const record = await runIteration(ctx, cell, iteration);
      record.adapter = adapter;
      record.ms = Date.now() - started;
      records.push(record);
      console.log(
        `done ${cell.profile}:${cell.scenario}:dpr${cell.dpr} #${iteration} ` +
        `frames=${record.frames.length}`,
      );
    } catch (exc) {
      records.push({ ...cell, iteration, error: String(exc).slice(0, 400) });
      console.log(`ERROR ${cell.profile}:${cell.scenario}:dpr${cell.dpr} #${iteration}: ${exc}`);
    }
  }
  await ctx.close();
  const gateAfter = envSnapshot(`after ${cell.profile}:${cell.scenario}:dpr${cell.dpr}`);
  writeFileSync(
    path.join(ROOT, 'output/perf/raw.json'),
    JSON.stringify({ browserVersion, adapterSample: adapter, gateBefore, gateAfter, records }, null, 1),
  );
}

console.log(`ALL DONE: ${records.length} records`);
await browser.close();
server.close();
