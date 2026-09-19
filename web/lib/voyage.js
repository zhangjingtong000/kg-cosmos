/* ═══════════════════════════════════════════════════════════════════════════
   KG COSMOS — 背景与交互（2026-09 重做：实时 WebGL 光场）
   ───────────────────────────────────────────────────────────────────────────

   背景的实现方式换过四轮，把结论记在这里，免得以后又绕回去：

     v1–v3  WebGL 点云 / CSS 渐变"自己画星云" → 被否（噪点、彩色沙子）
     v4–v5  FLUX 生成 5 张星云图交叉淡化 → "五张图换着播"，不构成演化
     v6     Wan2.2 I2V 链生成 121 帧序列，canvas drawImage 按滚动驱动
            → 流畅问题解决了，但它只在**滚动时**才动：停下滚动，画面就是
              一张静止的图。这正是"缺一点交互 / 感觉是死的"的根源。
     v7     **现在：单 pass WebGL 着色器，实时渲染的抽象光场**
            · 时间 t 让画面**永远在缓慢流动**——不滚动它也活着；
            · 滚动 p 是旅程参数：曝光、色温、光晕位置随之演化；
            · 鼠标视差：光晕与丝缕层做不同幅度的位移（阻尼跟随）；
            · 零素材、零请求、无级连续，拖进度条不存在换帧。

   设计语言（吸取前面所有被否的教训）：
     · 抽象光场，不是写实星云——大尺度柔和起伏 + 域扭曲后的纤维结构，
       没有高频细节（那会变成"沙子"）；
     · 深靛底（不是纯黑），光晕是**局部**的，暗角把四角留空；
     · 青与暖金**始终共存**，只让比例随滚动变化——"统一"与"变化"同时成立。

   保留的机制（都是踩过坑的，别删）：
     · targetP/curP 阻尼：滚动停下后画面继续飘一会儿；
     · userMoved 吸附：加载/浏览器恢复滚动位置时背景直接落在正确位置，
       不从 0 重播（用户报过这个 bug）；
     · REDUCED：prefers-reduced-motion 时时间冻结、只随滚动重绘；
     · #veil/#grade/#noise/#grain：文字区遮罩、色调、颗粒，保证正文可读。
   ═══════════════════════════════════════════════════════════════════════════ */

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const stage  = document.getElementById('stage');
const canvas = document.getElementById('voyageCanvas');
const reel   = document.getElementById('reel');
const cue    = document.querySelector('.cue');
const chnum  = document.getElementById('chnum');

let targetP = 0, curP = 0;
/* 用户是否已经真正操作过滚动。加载阶段（含浏览器的滚动恢复）为 false，
   此时背景直接吸附到目标位置，而不是从起点演过去。见 tick 里的说明。 */
let userMoved = false;
let mx = 0, my = 0, sx = 0, sy = 0;   // 鼠标原始值 / 阻尼值（-1..1）
let boostTarget = 0, boost = 0;       // 悬停可交互元素 → 光晕增亮
const ripples = [null, null, null];   // 点击涟漪：{x,y,t0,s}

/* ── 着色器 ─────────────────────────────────────────────────────────────── */
const VS = `
attribute vec2 a;
void main(){ gl_Position = vec4(a, 0.0, 1.0); }`;

const FS = `
precision highp float;
uniform vec2  u_res;
uniform float u_p;    /* 滚动旅程 0..1 */
uniform float u_t;    /* 秒——时间驱动，让画面不滚动也在流 */
uniform vec2  u_m;    /* 鼠标（已阻尼），-1..1：视差 + 提灯光晕 */
uniform float u_boost;/* 悬停可交互元素时 0..1：光晕增亮变暖 */
uniform vec4  u_r[3]; /* 涟漪：xy=场坐标 z=年龄(秒) w=强度 */

float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }

float noise(vec2 p){
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

/* 只 4 层——再多就是"沙子"，要的是大尺度的柔和起伏 */
float fbm(vec2 p){
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++){ v += a * noise(p); p = p * 2.03 + vec2(1.3, 4.6); a *= 0.5; }
  return v;
}

/* 丝缕场：域扭曲（拿噪声扭噪声坐标）之后过一道 ridge，
   "平的噪声"就变成网络状的细丝——抽象的光纤感，不是照片。 */
float fil(vec2 p, float t){
  vec2 q = vec2(fbm(p + vec2(0.0, t * 0.7)),
                fbm(p + vec2(5.2, 1.3) - vec2(t * 0.55, 0.0)));
  vec2 r = vec2(fbm(p + 1.8 * q + vec2(1.7, 9.2) + vec2(t * 0.30, 0.0)),
                fbm(p + 1.8 * q + vec2(8.3, 2.8) - vec2(0.0, t * 0.26)));
  float f = fbm(p + 2.1 * r);
  f = 1.0 - abs(2.0 * f - 1.0);
  f *= f;
  return f;
}

void main(){
  vec2 c = (gl_FragCoord.xy - 0.5 * u_res) / min(u_res.x, u_res.y);
  float p = u_p;
  float t = u_t * 0.065;

  /* —— 探索感的核心：镜头在大尺度场里**真实地平移 + 推近** ——
     path 的幅度是刻意放大的：滚过一屏，场里的风景真切地换一幕，
     而光源仍是屏幕锚定的同一对（青 + 暖金），视觉语言不断。 */
  vec2 path = vec2(-0.62 + 1.75 * p, 0.38 - 1.15 * p);
  float zoom = mix(1.30, 0.70, p);                         /* 推近（滚动冲刺已按用户要求删除） */
  vec2 par = u_m * vec2(0.050, 0.036);                     /* 视差（阻尼来自 JS） */
  vec2 base = (c + path) * zoom + par * zoom;

  float body = fbm(base * 1.15 + vec2(0.0, t * 0.5));
  float dens = smoothstep(0.34, 0.82, body);

  /* 两层丝缕：远层大而淡、近层细而亮；视差幅度不同 → 纵深 */
  float f1 = fil(base - par * 0.7, t);
  float f2 = fil(base * 1.9 + vec2(3.1, 0.7) + par * 1.4, t * 1.25);
  float l1 = pow(smoothstep(0.55, 0.98, f1), 1.5);
  float l2 = pow(smoothstep(0.66, 0.99, f2), 1.35);

  /* 光源：屏幕锚定（首尾连续），滚动只改强度与色温 */
  vec2 g1 = vec2(-0.34 + 0.30 * p, 0.20 - 0.26 * p) + u_m * 0.055
          + vec2(sin(t * 0.50), cos(t * 0.42)) * 0.012;
  vec2 g2 = vec2(0.44 - 0.14 * p, -0.30 + 0.42 * p) + u_m * 0.075
          + vec2(cos(t * 0.36), 0.0) * 0.014;
  float d1 = length(c - g1);
  float d2 = length(c - g2);
  float glow1 = smoothstep(0.90, 0.05, d1);
  float glow2 = smoothstep(0.66, 0.04, d2);

  float warm = smoothstep(0.12, 0.82, p);

  vec3 baseC = vec3(0.012, 0.016, 0.030);
  vec3 teal  = vec3(0.20, 0.58, 0.62);
  vec3 cyan  = vec3(0.30, 0.78, 0.86);   /* 旅程前半段的更亮青 */
  vec3 indigo= vec3(0.22, 0.28, 0.52);
  vec3 gold  = vec3(1.00, 0.58, 0.20);
  vec3 ember = vec3(1.00, 0.42, 0.16);   /* 深处的余烬橙红 */

  vec3 col = baseC * (0.9 + 0.7 * p);

  /* 青光晕：前半盛、后半让位给暖金——比例变化，两色始终共存 */
  float tealAmt = (0.12 + 0.20 * p) * (1.0 - 0.55 * smoothstep(0.55, 0.90, p));
  col += indigo * glow1 * 0.10;
  col += mix(teal, cyan, smoothstep(0.15, 0.45, p)) * glow1 * tealAmt * (0.40 + 0.60 * dens);

  /* 丝缕被附近的光点亮；暖色空间限位在暖核附近。
     旅程末段丝缕色向"余烬"加深——离开场时颜色也在走。 */
  float wm = smoothstep(0.30, 0.85, glow2);
  float e1 = glow1 * (0.38 + 0.62 * p) + glow2 * (0.10 + 0.60 * warm);
  float e2 = glow1 * (0.28 + 0.50 * p) + glow2 * (0.08 + 0.55 * warm);
  vec3 silkC = mix(mix(teal, cyan, smoothstep(0.15, 0.45, p)),
                   mix(gold, ember, smoothstep(0.75, 1.00, p)), wm);
  col += silkC * l1 * e1 * 0.60;
  col += silkC * l2 * e2 * 0.40;

  /* 丝缕上的高光"结"：沿线偶发的亮点，缓慢明灭——不是撒星星 */
  float glint = pow(f2, 9.0);
  col += silkC * glint * (0.5 + 0.8 * warm)
       * (0.55 + 0.45 * sin(u_t * 1.6 + f1 * 40.0)) * 1.1;

  /* 暖核热中心 */
  col += gold * glow2 * glow2 * glow2 * warm * (0.14 + 0.34 * p);

  /* 鼠标"提灯"：跟随光晕；悬停按钮/入口/进度点时增亮变暖 */
  vec2 mc = u_m * 0.5 * vec2(u_res.x / min(u_res.x, u_res.y), 1.0);
  float aura = exp(-5.5 * length(c - mc));
  col += mix(cyan, gold, 0.4 + 0.5 * u_boost) * aura * (0.035 + 0.075 * u_boost);

  /* 涟漪：点击处向外扩散的柔光环 */
  for (int i = 0; i < 3; i++){
    vec4 r = u_r[i];
    if (r.w > 0.0 && r.z < 2.2){
      float dd = length(c - r.xy);
      float ring = exp(-28.0 * abs(dd - r.z * 0.55)) * exp(-1.8 * r.z) * r.w;
      col += mix(cyan, gold, warm) * ring * 0.5;
    }
  }

  /* 曝光随 p 抬升（滚动冲刺已按用户要求删除） */
  col *= (0.62 + 0.45 * p);

  /* 极细颗粒，防大面积渐变色带；不是"星星点缀" */
  col += (hash(gl_FragCoord.xy + fract(u_t) * 61.7) - 0.5) * 0.011;

  /* 暗角：视线收拢，四角保持"空" */
  float vig = smoothstep(1.30, 0.30, length(c));
  col *= 0.32 + 0.68 * vig;

  /* 软 tonemap，防高光死白 */
  col = 1.0 - exp(-col * 1.7);

  gl_FragColor = vec4(col, 1.0);
}`;

/* ── WebGL 装配 ─────────────────────────────────────────────────────────── */
let gl = null, U = {};
function failGL(msg) {
  if (msg) console.error('[voyage] ' + msg);
  gl = null;
  if (stage) stage.classList.add('nogl');
  canvas && (canvas.style.display = 'none');
  window.__ready = true;          // 降级模式也算就绪，别让回归卡住
}
(function initGL() {
  if (!canvas) { failGL('没有 #voyageCanvas'); return; }
  /* preserveDrawingBuffer：帧呈现后不清空绘制缓冲——CDP 回归要在
     rAF 之外异步读像素（readPixels），默认 false 会读到全 0，
     像素级断言全部失效。单 pass 全屏三角形，这点开销可以忽略。 */
  gl = canvas.getContext('webgl', { antialias: false, alpha: false,
                                    preserveDrawingBuffer: true,
                                    powerPreference: 'high-performance' })
    || canvas.getContext('experimental-webgl');
  if (!gl) { failGL('WebGL 不可用，走 CSS 渐变降级'); return; }

  function sh(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw gl.getShaderInfoLog(s);
    return s;
  }
  try {
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw gl.getProgramInfoLog(prog);
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'a');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    ['res', 'p', 't', 'm', 'boost', 'r'].forEach(n => {
      U[n] = gl.getUniformLocation(prog, 'u_' + n);
    });
  } catch (e) { failGL('着色器装配失败: ' + e); }
})();

let needResize = true;
function sizeCanvas() {
  if (!canvas || !gl) return;
  /* DPR 上限 1.5：3× 屏上看不出差别，但每帧少画一半以上的像素 */
  const dpr = Math.min(1.5, devicePixelRatio || 1);
  canvas.width = Math.max(2, Math.round(innerWidth * dpr));
  canvas.height = Math.max(2, Math.round(innerHeight * dpr));
  needResize = true;
}

/* ── 滚动读取与关键帧插值 ───────────────────────────────────────────────── */
function readScroll() {
  const total = Math.max(1, reel.scrollHeight - innerHeight);
  targetP = Math.min(1, Math.max(0, scrollY / total));
  if (cue) cue.classList.toggle('gone', scrollY > innerHeight * 0.4);
}

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

/* ── 主循环 ─────────────────────────────────────────────────────────────── */
const t0 = performance.now();
let last = 0, lastWarm = -1, lastVeil = -1, drawnP = -1, started = false;

function tick(now) {
  const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016;
  last = now;
  /* 用户还没真正动过滚动条之前，curP 直接吸附 targetP，不做阻尼。
     浏览器的滚动恢复（刷新时跳回原位置）发生在脚本执行之后，可能很晚。
     缺了这个判断，迟到的 scroll 事件会让 curP 从 0 阻尼爬升——
     用户看到的就是"整段背景被重播一遍"。 */
  if (!userMoved) { curP = targetP; }
  else { curP += (targetP - curP) * (REDUCED ? 1 : Math.min(1, dt * 3.4)); }
  const p = curP;

  if (!REDUCED) {
    /* 视差阻尼跟随：比硬跟多一层"重量感" */
    sx += (mx - sx) * Math.min(1, dt * 4.2);
    sy += (my - sy) * Math.min(1, dt * 4.2);
    boost += (boostTarget - boost) * Math.min(1, dt * 5.5);
  } else {
    boost = 0;
  }

  /* 色温与文字遮罩（DOM 叠层）：只在变化时写，不做每帧样式抖动 */
  const warm = kf(p, [[0, 0], [0.34, 0.10], [0.62, 0.95], [0.86, 1.0], [1, 0.34]]);
  const veil = kf(p, [[0, 0.34], [0.3, 0.46], [0.62, 0.62], [0.86, 0.68], [1, 0.50]]);
  if (Math.abs(warm - lastWarm) > 0.008) {
    lastWarm = warm;
    stage.style.setProperty('--warmRGB',
      Math.round(150 + 100 * warm) + ',' + Math.round(180 - 6 * warm) + ','
      + Math.round(255 - 130 * warm));
  }
  if (Math.abs(veil - lastVeil) > 0.002) {
    lastVeil = veil;
    stage.style.setProperty('--veil', veil.toFixed(3));
  }

  if (gl) {
    if (needResize) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      needResize = false;
    }
    if (!REDUCED) {
      drawFrame(p, (now - t0) / 1000);
    } else if (Math.abs(p - drawnP) > 0.0008 || drawnP < 0) {
      drawFrame(p, 0);              /* 减少动效：时间冻结，只随滚动重绘 */
      drawnP = p;
    }
  }
  requestAnimationFrame(tick);
}

function drawFrame(p, t) {
  gl.uniform2f(U.res, canvas.width, canvas.height);
  gl.uniform1f(U.p, p);
  gl.uniform1f(U.t, t);
  gl.uniform2f(U.m, sx, sy);
  gl.uniform1f(U.boost, boost);
  const rr = rippleUniforms(now4());
  gl.uniform4fv(U.r, rr);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  /* 给回归用的直接证据：帧计数与当前 u_t。
     回归原先靠"像素变化量"间接推断时间驱动，而着色器的时间系数很小、
     CPU 繁忙时帧率又低，于是会误报（实测两次：变化量 2 < 阈值 3，
     都发生在机器忙的时候，不是产品问题）。这两个数才是"画面确实在
     按时间更新"的直接证据，不受 CPU 负载影响。 */
  window.__frames = (window.__frames || 0) + 1;
  window.__u_t = t;
  if (!started) {
    started = true;
    canvas.classList.add('on');     /* 首帧已画上，淡入（避免开场闪黑） */
    window.__ready = true;
  }
}

/* 涟漪 → uniform 数组（x, y, 年龄秒, 强度；过期的强度归零） */
function now4() { return (performance.now() - t0) / 1000; }
function rippleUniforms(nowSec) {
  const out = new Float32Array(12);
  for (let i = 0; i < 3; i++) {
    const r = ripples[i];
    if (!r) continue;
    const age = nowSec - r.t0;
    out[i * 4] = r.x; out[i * 4 + 1] = r.y;
    out[i * 4 + 2] = age; out[i * 4 + 3] = age < 2.2 ? r.s : 0;
  }
  return out;
}

function spawnRipple(clientX, clientY) {
  if (REDUCED) return;
  const m = Math.min(innerWidth, innerHeight);
  const slot = ripples.findIndex(r => !r || (now4() - r.t0) > 2.2);
  ripples[slot === -1 ? 0 : slot] = {
    x: (clientX - 0.5 * innerWidth) / m,
    y: (0.5 * innerHeight - clientY) / m,   /* GL y 轴朝上 */
    t0: now4(),
    s: 1.0,
  };
}

/* ── 事件 ───────────────────────────────────────────────────────────────── */
addEventListener('scroll', readScroll, { passive: true });
addEventListener('resize', () => { readScroll(); sizeCanvas(); });
addEventListener('pointermove', e => {
  if (REDUCED) return;              /* 减少动效：不要视差 */
  mx = (e.clientX / innerWidth - 0.5) * 2;
  my = (e.clientY / innerHeight - 0.5) * 2;
}, { passive: true });
/* 一旦用户真正操作过滚动，就切到阻尼模式（见 tick 注释） */
['wheel', 'touchstart', 'keydown', 'mousedown'].forEach(t =>
  addEventListener(t, () => { userMoved = true; }, { passive: true, once: true }));

/* 点击涟漪：任意处点一下，光场里荡开一圈柔环 */
addEventListener('pointerdown', e => spawnRipple(e.clientX, e.clientY), { passive: true });
/* 悬停可交互元素：提灯增亮变暖（悬停/离开，配合阻尼） */
if (!REDUCED) {
  ['.btn', '.entry', '#rail .dot'].forEach(sel =>
    document.querySelectorAll(sel).forEach(el => {
      el.addEventListener('pointerenter', () => { boostTarget = 1; });
      el.addEventListener('pointerleave', () => { boostTarget = 0; });
    }));
}

readScroll();
if (gl) sizeCanvas();
requestAnimationFrame(tick);

/* ── 章节入场 / 进度点 / 章节幽灵数字 ───────────────────────────────────── */
const chs = [...document.querySelectorAll('.ch')];
const dots = [...document.querySelectorAll('#rail .dot')];

/* 逐层上浮的延迟：第 N 个子元素晚 N×90ms，比整块淡入更有节奏 */
chs.forEach(c => [...c.querySelectorAll('.in > *')].forEach((el, i) =>
  el.style.setProperty('--i', i)));

const numVal = () => (chnum ? chnum.textContent : '');
let curCh = -1;
function setNum(i) {
  if (!chnum || i === curCh) return;
  curCh = i;
  chnum.classList.remove('in');
  void chnum.offsetWidth;           /* 强制重排，让退场/入场 transition 重新触发 */
  chnum.textContent = '0' + i;
  chnum.classList.add('in');
}

const cio = new IntersectionObserver(es => {
  es.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.classList.add('seen');
    const i = chs.indexOf(e.target);
    dots.forEach((d, k) => d.classList.toggle('on', k === i));
    setNum(i);
  });
}, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
chs.forEach(c => cio.observe(c));

/* ── 交互：进度点可点、键盘跳章 ─────────────────────────────────────────── */
function goChapter(i) {
  i = Math.max(0, Math.min(chs.length - 1, i));
  const c = chs[i];
  if (c) scrollTo({ top: c.offsetTop, behavior: REDUCED ? 'auto' : 'smooth' });
}
dots.forEach(d => d.addEventListener('click', () => goChapter(+d.dataset.i)));

/* 当前章节：以视口中线落在谁的范围里为准 */
function curIndex() {
  const y = scrollY + innerHeight * 0.5;
  let best = 0;
  chs.forEach((c, i) => { if (c.offsetTop <= y) best = i; });
  return best;
}
addEventListener('keydown', e => {
  const t = e.target;
  /* 不劫持输入控件里的按键 */
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
            t.tagName === 'SELECT' || t.isContentEditable)) return;
  const k = e.key;
  if (k === 'ArrowDown' || k === 'PageDown') { e.preventDefault(); goChapter(curIndex() + 1); }
  else if (k === 'ArrowUp' || k === 'PageUp') { e.preventDefault(); goChapter(curIndex() - 1); }
  else if (k === 'Home') { e.preventDefault(); goChapter(0); }
  else if (k === 'End') { e.preventDefault(); goChapter(chs.length - 1); }
});

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
    // ── 2026-09-15：把概览层的两个规模数字也改成**实测值** ────────────────
    // 它们此前写死在 HTML 的 `data-count` 里（`18780` / `29120`），
    // 而概览口径在 P7 之后已变成 `19427` / `30000` —— **界面上印着过期数字**。
    // 这与 `check_doc_numbers.py` 盯 README 的道理一样，只是守门脚本不查 HTML。
    // 动画由 `IntersectionObserver` 在滚到那一屏时才触发，
    // 而这里读 `nebula.json` 只要几百毫秒 ⇒ 更新来得及；
    // 同时直接写 `textContent`，以防该屏已经被播过一遍。
    const setFig = (id, v) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.dataset.count = String(v);
      el.textContent = Number(v).toLocaleString();
    };
    setFig('figNodes', n);
    setFig('figEdges', e);
    lf.innerHTML =
      '<span class="it"><span class="v">' + n.toLocaleString() + '</span>'
      + '<span class="l">实体 · 真实抽取</span></span>'
      + '<span class="it"><span class="v">' + e.toLocaleString() + '</span>'
      // 2026-09-15 改：`e` 是**概览层导出的边数**（受 `--max-edges` 上限约束，
      // 现在是 30000），**不等于评测全集**。
      // 原文案把两个口径并进一个标签，读起来像"30000 就是评测全集"。
      // 2026-09-19 修：这里的评测全集旧值 43867 已改成 45708
      // （`check_doc_numbers.py` 的 DOCS 不含本文件，原来漏了；现由
      //  `tools/verify_firstscreen_numbers.py` 在真实 DOM 上盯住）。
      + '<span class="l">关系 · 概览层导出（评测全集 45708）</span></span>'
      + '<span class="it"><span class="v">' + c + '</span>'
      + '<span class="l">社区 · Louvain</span></span>';
  })
  .catch(() => {});
