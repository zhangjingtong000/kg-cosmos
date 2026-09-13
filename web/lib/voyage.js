/* ═══════════════════════════════════════════════════════════════════════════
   KG COSMOS — 滚动驱动的星云旅程
   ───────────────────────────────────────────────────────────────────────────

   这一版是**方向性修正的终点**，把前面的弯路记在这里：

     v1–v3  用 WebGL 点云 / Canvas 渐变"自己画星云" → 全部失败
            （噪点、彩色沙子、甜甜圈）
     v4–v5  纯 CSS 深空 → 干净但平淡，看不出"旅程"
     v6     **本地 ComfyUI + FLUX.1-schnell 生成 5 张星云图** ← 就是这里

   结论：星云的观感来自**连续介质的高频细节**（气体湍流、尘埃带、发光边缘），
   这类东西代码合成不出来，而**生成模型做得很轻松**。本机本来就有 ComfyUI
   和 FLUX，绕了一大圈才用上——记下来，避免后来者重复这段路。

   实现：
     · 五张图（web/assets/sky-*.png）按滚动进度**交叉淡化**，
       同一时刻最多两张可见，因此过渡连续、没有硬切；
     · 每张图配一条**缓慢缩放/位移**曲线（Ken Burns），
       让"推进"与"拉远"有真实镜头感，而不是单纯换图；
     · 进度带阻尼（滚动停下后画面继续飘一会儿）；
     · 色温、文字区遮罩、暗角全部由同一条 p 驱动。

   离线约束：图在本地（5 张 PNG 约 4.9 MB），页面零外部请求。
   ═══════════════════════════════════════════════════════════════════════════ */

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const stage  = document.getElementById('stage');
const layers = [...document.querySelectorAll('#skyLayers .sky')];
const reel   = document.getElementById('reel');
const N = Math.max(1, layers.length);

let targetP = 0, curP = 0;
let mx = 0, my = 0, sx = 0, sy = 0;

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

/* 每张图的镜头曲线。同一时刻只有相邻两张在动：
   一张继续推进、一张开始拉远，交叠处自然形成"穿过"的观感。 */
const LENS = [
  { s0: 1.00, s1: 1.18, x0: 0,  x1: 0,  y0: 0,  y1: -2 },  // 远景 → 推近
  { s0: 1.02, s1: 1.24, x0: 2,  x1: -1, y0: 1,  y1: -3 },  // 继续靠近
  { s0: 1.04, s1: 1.30, x0: -2, x1: 2,  y0: 2,  y1: -2 },  // 进入
  { s0: 1.06, s1: 1.34, x0: 0,  x1: 0,  y0: 2,  y1: -1 },  // 深处（扩张最猛）
  { s0: 1.26, s1: 1.04, x0: -1, x1: 0,  y0: -1, y1: 0 }    // 拉远（反过来）
];

let last = 0;
function tick(now) {
  const dt = last ? (now - last) / 1000 : 0.016;
  last = now;
  curP += (targetP - curP) * (REDUCED ? 1 : Math.min(1, dt * 3.4));
  const p = curP;

  if (videoOK) {
    /* 视频模式：滚动直接驱动时间轴。素材末尾是溶回开头的，所以取
       [0, duration - 1帧)，避免停在尾帧上。 */
    if (video.readyState >= 2 && video.duration) {
      const t = p * (video.duration - 1 / 30);
      if (Math.abs(video.currentTime - t) > 1 / 60) video.currentTime = t;
    }
  } else {
    /* 回退模式：交叉淡化。把 p 映射到 [0, N-1]，第 i 张的可见度 = 三角权重。
       这样任何时刻最多两张同时可见，过渡必然连续。 */
    const pos = p * (N - 1);
    for (let i = 0; i < N; i++) {
      const w = Math.max(0, 1 - Math.abs(pos - i));
      const L = LENS[i] || LENS[0];
      // 图内局部进度：进入时为 0，离开时为 1
      const local = Math.min(1, Math.max(0, pos - i + 0.5));
      const s = L.s0 + (L.s1 - L.s0) * local;
      const x = L.x0 + (L.x1 - L.x0) * local;
      const y = L.y0 + (L.y1 - L.y0) * local;
      layers[i].style.opacity = w.toFixed(4);
      layers[i].style.transform =
        'scale(' + s.toFixed(4) + ') translate3d('
        + (x - sx * 1.6).toFixed(2) + '%, '
        + (y - sy * 1.2).toFixed(2) + '%, 0)';
    }
  }
  sx += (mx - sx) * 0.045;
  sy += (my - sy) * 0.045;

  /* 色温与文字遮罩：与图的色彩走向同步（冷蓝 → 暖金 → 回落） */
  const warm = kf(p, [[0, 0], [0.34, 0.10], [0.62, 0.95], [0.86, 1.0], [1, 0.34]]);
  stage.style.setProperty('--warmRGB',
    Math.round(150 + 100 * warm) + ',' + Math.round(180 - 6 * warm) + ','
    + Math.round(255 - 130 * warm));
  const veil = kf(p, [[0, 0.34], [0.3, 0.46], [0.62, 0.62], [0.86, 0.68], [1, 0.48]]);
  stage.style.setProperty('--veil', veil.toFixed(3));

  requestAnimationFrame(tick);
}

addEventListener('scroll', readScroll, { passive: true });
addEventListener('resize', readScroll);
addEventListener('pointermove', e => {
  mx = (e.clientX / innerWidth - 0.5) * 2;
  my = (e.clientY / innerHeight - 0.5) * 2;
}, { passive: true });
readScroll();

/* 用 background-image 铺层（比 <img> 更好控制 object-fit 与缩放中心） */
layers.forEach(el => {
  if (el.dataset.src) el.style.backgroundImage = 'url("' + el.dataset.src + '")';
});

/* ── 背景视频（2026-09）───────────────────────────────────────────────────
   用户提了两次：「导航页后面的这个视频，还是没有做到末帧与首帧去连接」。
   根因不是播放器，是素材 —— 原先那 5 张星云图各自独立生成（sky-3 是爆亮的
   橙核、sky-4 却是暗蓝盘状星系），彼此不构成演化链，再怎么交叉淡化也只是
   "五张图换着播"。现在换成一条用 FLUX img2img 逐帧演化出来的闭环视频。

   这里是**探测式**接入：视频没就绪（文件缺失 / 解码失败 / 用户要求减少动效）
   就完整回退到下面的 5 图层交叉淡化 —— 那套逻辑一行未动。
   4 秒兜底之后永久放弃，不允许它半路插进来打断已经开始的动画。 */
const video = document.getElementById('voyageVideo');
let videoOK = false;

if (video && !REDUCED) {
  const giveUp = () => {
    videoOK = false;
    video.classList.remove('on');
    try { video.removeAttribute('src'); video.load(); } catch (_) {}
  };
  video.addEventListener('loadeddata', () => {
    if (video.duration > 0.5) { videoOK = true; video.classList.add('on'); }
    else giveUp();
  });
  video.addEventListener('error', giveUp);
  setTimeout(() => { if (!videoOK) giveUp(); }, 4000);
  video.src = './assets/voyage.mp4';
  video.load();
}

/* 等首图就绪再起动画，避免开场闪一下黑 */
function preload(list) {
  return Promise.all(list.map(src => new Promise(res => {
    const im = new Image();
    im.onload = im.onerror = () => res();
    im.src = src;
  })));
}
preload(layers.map(el => el.dataset.src).filter(Boolean)).then(() => {
  requestAnimationFrame(tick);
  window.__ready = true;
});

/* ── 章节入场与右侧进度点 ───────────────────────────────────────────────── */
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

/* ── 页面上的数据规模 ───────────────────────────────────────────────────── */
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
      '<span class="it"><span class="v">' + n.toLocaleString() + '</span>'
      + '<span class="l">实体 · 真实抽取</span></span>'
      + '<span class="it"><span class="v">' + e.toLocaleString() + '</span>'
      + '<span class="l">关系 · 评测全集 43867</span></span>'
      + '<span class="it"><span class="v">' + c + '</span>'
      + '<span class="l">社区 · Louvain</span></span>';
  })
  .catch(() => {});
