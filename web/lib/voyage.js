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
const layers = [...document.querySelectorAll('#voyageFrames .frm')];
const reel   = document.getElementById('reel');
const N = Math.max(1, layers.length);

let targetP = 0, curP = 0;
/* 用户是否已经真正操作过滚动。加载阶段（含浏览器的滚动恢复）为 false，
   此时背景直接吸附到目标位置，而不是从第 0 帧演过去。见 tick 里的说明。 */
let userMoved = false;
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
/* 镜头：每层都从 1.00 缓慢推到 1.08（Ken Burns）。
   第一版给 5 层各手写了一组起止值，其实彼此差别很小；
   12 层用同一条曲线更好维护，视觉上没有区别。 */
const zoomAt = (local) => 1.0 + 0.08 * local;

let last = 0;
function tick(now) {
  const dt = last ? (now - last) / 1000 : 0.016;
  last = now;
  /* 用户还没真正动过滚动条之前，curP 直接吸附 targetP，不做阻尼。
     浏览器的滚动恢复（刷新时跳回原位置）发生在脚本执行之后，而且可能很晚
     （图片加载改变布局时还会再来一次）。缺了这个判断，那些迟到的 scroll 事件
     会让 curP 从 0 一路阻尼上去 —— 用户看到的就是"整段背景被重播一遍"。
     一旦用户按下滚轮 / 触摸 / 键盘，就切换成阻尼模式，那才是想要的平滑过渡。 */
  if (!userMoved) { curP = targetP; }
  else { curP += (targetP - curP) * (REDUCED ? 1 : Math.min(1, dt * 3.4)); }
  const p = curP;

  {
    /* 回退模式：交叉淡化。把 p 映射到 [0, N-1]，第 i 张的可见度 = 三角权重。
       这样任何时刻最多两张同时可见，过渡必然连续。 */
    const pos = p * (N - 1);
    for (let i = 0; i < N; i++) {
      // 可见度 = 三角权重：任何时刻最多相邻两张同时可见，过渡必然连续
      const w = Math.max(0, 1 - Math.abs(pos - i));
      // 图内局部进度：进入时为 0，离开时为 1
      const local = Math.min(1, Math.max(0, pos - i + 0.5));
      const s = zoomAt(local);
      layers[i].style.opacity = w.toFixed(4);
      layers[i].style.transform =
        'scale(' + s.toFixed(4) + ') translate3d('
        + (-sx * 1.2).toFixed(2) + '%, ' + (-sy * 0.9).toFixed(2) + '%, 0)';
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
/* 一旦用户真正操作过滚动，就切到阻尼模式。在此之前（页面加载、浏览器恢复
   滚动位置、图片加载引起的重排）背景一律直接吸附。 */
['wheel', 'touchstart', 'keydown', 'mousedown'].forEach(t =>
  addEventListener(t, () => { userMoved = true; }, { passive: true, once: true }));

readScroll();

/* 用 background-image 铺层（比 <img> 更好控制 object-fit 与缩放中心） */
layers.forEach(el => {
  if (el.dataset.src) el.style.backgroundImage = 'url("' + el.dataset.src + '")';
});

/* 2026-09：背景的实现方式换过三轮，把结论记在这里，免得以后又绕回去。
   第一版 5 张独立生成的星云图交叉淡化 —— 过渡本身是流畅的（常驻图层只改
   opacity/transform，不重新解码），但那 5 张图彼此不构成演化链，
   用户看到的是"五张图换着播"，且亮度跨度只有 6/255，滑完几乎没变化。
   第二版换成 <video>+currentTime 做滚动擦洗 —— 实测这个环境根本不执行 video
   seek（裸设 currentTime=5 读回来还是 0），t 恒为 0，画面一动不动。
   第三版切 93 个 background-image URL —— 每换一次就重新解码一次，直接闪成一片。
   现在这一版：6 个关键帧重新生成（亮度 17 → 79，跨度 61.7，中间有橙金爆亮星核），
   插值成 93 帧再抽出 12 张常驻图层，回到第一版那套**只改 opacity/transform**的
   机制 —— 素材是新的、连成一条旅程，渲染方式则是被验证过流畅的那一种。 */

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
