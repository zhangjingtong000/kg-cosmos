/* ═══════════════════════════════════════════════════════════════════════════
   KG COSMOS — 滚动驱动的深空（纯 CSS 场，不做星云）
   ───────────────────────────────────────────────────────────────────────────

   这是一次**方向修正**，写在最前面，免得后来的人再走一遍弯路。

   前三个版本都试图"画出星云"，全部失败：
     v1  WebGL 点云（1 万点）  → 三重衰减叠加，几乎全黑
     v2  WebGL 点云（加云雾层）→ 变成"彩色沙子"，密集处过曝成白盘
     v3  Canvas 2D 径向渐变    → 变成一个个模糊圆斑，尘埃带挖出"甜甜圈"

   失败的原因是共通的：**星云的观感来自连续介质的高频细节**（气体湍流、
   尘埃带、星芒），它不是"点"也不是"几个渐变圆"能合成的。
   用有限的手段硬凑，只会得到噪点或圆斑——**比不做更糟**。

   所以这一版**放弃画星云**，改做一件确定能做好的事：
   **一个克制、精密、有纵深的深空背景**。它的高级感来自：
     · 极深的空间底色 + 三层缓慢漂移的冷色光晕（不是星云，只是"环境光"）
     · 三档亮度的星点（共约 140 颗，用 box-shadow 一次性画出，**刻意稀疏**）
     · 一层极细的粒子噪声（CSS 生成的 SVG 噪点，抗"纯色塑料感"）
     · 滚动驱动的色温偏移（冷蓝 → 微暖）与光晕位移
     · 与文字排版形成对比：背景安静，信息清楚

   "少即是多"在这里不是退让，而是正确的判断：
   **20 颗位置讲究的星，胜过 18780 个噪点。**

   数据仍在页面上（18780 实体 / 29120 关系 / 60 社区），
   但它以**文字与数字**呈现，而不是假装成一张星云图。
   ═══════════════════════════════════════════════════════════════════════════ */

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const stage = document.getElementById('stage');
const reel  = document.getElementById('reel');

let targetP = 0, curP = 0;

function readScroll() {
  const total = Math.max(1, reel.scrollHeight - innerHeight);
  targetP = Math.min(1, Math.max(0, scrollY / total));
}

/* 关键帧插值（smoothstep） */
function kf(t, pts) {
  if (t <= pts[0][0]) return pts[0][1];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    if (t <= b[0]) {
      const u = (t - a[0]) / (b[0] - a[0]);
      return a[1] + (b[1] - a[1]) * (u * u * (3 - 2 * u));
    }
  }
  return pts[pts.length - 1][1];
}

/* 生成星点：三档亮度，刻意稀疏。用 box-shadow 一次性绘制，省 DOM。 */
function buildStars() {
  const box = document.getElementById('stars');
  if (!box) return;
  const layers = [
    { n: 220, size: 1,   a: 0.48, spread: 100 },  // 暗、密、铺满
    { n: 90,  size: 1.7, a: 0.76, spread: 100 },  // 中等
    { n: 26,  size: 2.6, a: 1.0,  spread: 100 }   // 亮、少、带柔光
  ];
  let html = '';
  for (const L of layers) {
    const shadows = [];
    for (let i = 0; i < L.n; i++) {
      const x = (Math.random() * L.spread).toFixed(2);
      const y = (Math.random() * L.spread).toFixed(2);
      shadows.push(`${x}vw ${y}vh 0 ${L.size > 2 ? 0.6 : 0}px rgba(214,230,255,${L.a})`);
    }
    html += `<i style="width:${L.size}px;height:${L.size}px;`
          + `box-shadow:${shadows.join(',')}"></i>`;
  }
  box.innerHTML = html;
}

/* 滚动驱动：色温、光晕位移、星点亮度 */
let last = 0;
function tick(now) {
  const dt = last ? (now - last) / 1000 : 0.016;
  last = now;
  curP += (targetP - curP) * (REDUCED ? 1 : Math.min(1, dt * 3.2));
  const p = curP;

  // 冷蓝 → 微暖（幅度很小：这是"氛围位移"，不是变色）
  const warm = kf(p, [[0, 0], [0.3, 0.32], [0.6, 1.0], [1, 0.46]]);
  const r = Math.round(126 + 78 * warm);
  const g = Math.round(176 + 18 * warm);
  const b = Math.round(255 - 92 * warm);
  stage.style.setProperty('--warmRGB', `${r},${g},${b}`);

  // 光晕：随滚动缓慢上移并放大（三层错开速度 → 纵深）
  const drift = kf(p, [[0, 0], [0.35, -12], [0.7, 9], [1, -5]]);
  stage.style.setProperty('--drift', drift.toFixed(2) + 'vh');
  const grow = kf(p, [[0, 0.86], [0.45, 1.38], [1, 0.96]]);
  stage.style.setProperty('--grow', grow.toFixed(3));

  // 星点整体亮度：深处稍暗，让前景文字更清
  const sl = kf(p, [[0, 1.05], [0.35, 0.70], [0.7, 0.40], [1, 0.85]]);
  stage.style.setProperty('--starA', sl.toFixed(3));

  requestAnimationFrame(tick);
}

addEventListener('scroll', readScroll, { passive: true });
addEventListener('resize', readScroll);
readScroll();
buildStars();
requestAnimationFrame(tick);

window.__ready = true;

/* ── 章节入场与进度点 ───────────────────────────────────────────────────── */
const chs = [...document.querySelectorAll('.ch')];
const dots = [...document.querySelectorAll('#rail .dot')];
const cio = new IntersectionObserver(es => {
  es.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.classList.add('seen');
    const i = chs.indexOf(e.target);
    dots.forEach((d, k) => d.classList.toggle('on', k === i));
  });
}, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
chs.forEach(c => cio.observe(c));

/* ── 数字滚动 ───────────────────────────────────────────────────────────── */
function countUp(el) {
  const to = parseFloat(el.dataset.count);
  const dec = parseInt(el.dataset.dec || '0', 10);
  const u = el.querySelector('u');
  const tail = u ? u.outerHTML : '';
  const dur = 1400, s = performance.now();
  (function step() {
    const k = Math.min(1, (performance.now() - s) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.innerHTML = (dec ? (to * e).toFixed(dec) : Math.round(to * e).toLocaleString()) + tail;
    if (k < 1) requestAnimationFrame(step);
  })();
}
const nums = [...document.querySelectorAll('[data-count]')];
if (REDUCED) {
  nums.forEach(el => {
    const to = parseFloat(el.dataset.count);
    const dec = parseInt(el.dataset.dec || '0', 10);
    const u = el.querySelector('u');
    el.innerHTML = (dec ? to.toFixed(dec) : Math.round(to).toLocaleString())
                   + (u ? u.outerHTML : '');
  });
} else {
  const nio = new IntersectionObserver(es => {
    es.forEach(e => { if (e.isIntersecting) { countUp(e.target); nio.unobserve(e.target); } });
  }, { threshold: 0.4 });
  nums.forEach(el => nio.observe(el));
}
if (REDUCED) document.querySelectorAll('.ch').forEach(c => c.classList.add('seen'));

/* ── 页面上仍如实展示数据规模 ───────────────────────────────────────────── */
fetch('./kg-data/nebula.json')
  .then(r => r.ok ? r.json() : null)
  .then(D => {
    if (!D) return;
    const lf = document.getElementById('liveFacts');
    if (!lf) return;
    const n = (D.nodes || []).length;
    const e = (D.edges || []).length;
    const c = (D.communities || []).length;
    lf.innerHTML =
      `<span class="it"><span class="v">${n.toLocaleString()}</span>`
      + `<span class="l">实体 · 真实抽取</span></span>`
      + `<span class="it"><span class="v">${e.toLocaleString()}</span>`
      + `<span class="l">关系 · 评测全集 43867</span></span>`
      + `<span class="it"><span class="v">${c}</span>`
      + `<span class="l">社区 · Louvain</span></span>`;
  })
  .catch(() => {});
