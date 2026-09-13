/* ═══════════════════════════════════════════════════════════════════════════
   KG COSMOS — 背景与交互（2026-09 重做：实时 WebGL 深空）
   ───────────────────────────────────────────────────────────────────────────

   背景的实现方式换过五轮，把结论记在这里，免得以后又绕回去：

     v1–v3  WebGL 点云 / CSS 渐变"自己画星云" → 被否（噪点、彩色沙子）
     v4–v5  FLUX 生成 5 张星云图交叉淡化 → "五张图换着播"，不构成演化
     v6     Wan2.2 I2V 链生成 121 帧序列按滚动驱动 → 只在滚动时才动，
            停下就是一张死图；且"素材是不是真的"帮不了交互感
     v7     抽象光场着色器（域扭曲丝缕）→ 被否：丝缕/绸缎方向整个不对，
            用户要的是**深空**——星云 + 星空 + 探索感
     v8     **现在：深空。绸缎全部删掉，语言回到"星云 + 星空"**
            · 三大云系（青蓝 / 紫品红 / 暖金余烬）随滚动先后登场换景，
              尘埃暗纹给云结构；云系核心带柔光；
            · 四层视差星空：滚动时星层按深度错动 = 在深空里**飞行**；
              少数带光晕的锚点星；约 8 秒一颗的偶发流星；
            · 时间 t 让画面不滚动也在活（云漂 + 星星明灭 + 流星）；
            · 鼠标提灯光晕 / hover 增亮 / 点击涟漪。

   保留的机制（都是踩过坑的，别删）：
     · targetP/curP 阻尼：滚动停下后画面继续飘一会儿；
     · userMoved 吸附：加载/浏览器恢复滚动位置时背景直接落在正确位置，
       不从 0 重播（用户报过这个 bug）；
     · REDUCED：prefers-reduced-motion 时时间冻结、只随滚动重绘；
     · #veil/#grade/#noise/#grain：文字区遮罩、色调、颗粒，保证正文可读。
   （v7 的教训：滚动"冲刺"反馈——按滚动速度推近+增亮——被否，
    上下滚动都显得突兀，已删，别再加回来。）
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
uniform float u_p;    /* 滚动旅程 0..1：滚动 = 在深空里飞行 */
uniform float u_t;    /* 秒——时间驱动，让画面不滚动也在活 */
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

/* 云气：4 层，平滑蓬松——星云的"气体感"，绝不做高频细节（那会变沙子） */
float fbm(vec2 p){
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++){ v += a * noise(p); p = p * 2.03 + vec2(1.3, 4.6); a *= 0.5; }
  return v;
}

/* 星空层：每格至多一颗星，位置/亮度/色温由 hash 决定；
   不同层用不同视差系数 → 滚动时层层错动，飞行感来自这里。
   闪烁是 2~5 秒级的缓慢明灭，不是乱闪。 */
vec3 starLayer(vec2 uv, float scale, float thr, float size, float time){
  vec2 g = uv * scale;
  vec2 id = floor(g);
  vec2 f = fract(g) - 0.5;
  float h = hash(id);
  if (h < thr) return vec3(0.0);
  vec2 sp = (vec2(hash(id + 19.7), hash(id + 47.3)) - 0.5) * 0.8;
  float d = length(f - sp);
  float b = smoothstep(size, 0.0, d);
  float lum = 0.30 + 0.70 * fract(h * 91.7);
  float twk = 0.72 + 0.28 * sin(time * (0.6 + h * 2.4) + h * 61.0);
  vec3 tint = mix(vec3(0.78, 0.86, 1.00), vec3(1.00, 0.82, 0.58),
                  step(0.75, fract(h * 37.1)));            /* 少数暖星 */
  return tint * b * lum * twk;
}

void main(){
  vec2 c = (gl_FragCoord.xy - 0.5 * u_res) / min(u_res.x, u_res.y);
  float p = u_p;
  float t = u_t * 0.05;
  vec2 par = u_m * vec2(0.050, 0.036);

  /* 相机：滚动 = 在深空里向前飞。所有层按各自深度取 cam 的不同比例，
     星层最快、云系次之 → 屏幕一滚，整个空间立体地掠过去。 */
  vec2 cam = vec2(0.55 * p, -1.10 * p) + vec2(sin(p * 2.1) * 0.10, 0.0);

  /* 深空底色：深蓝黑，不是纯黑 */
  vec3 col = vec3(0.010, 0.016, 0.036) * (1.0 + 0.5 * p);

  /* —— 云系 A：青蓝星云（旅程前半的主角）——
     尘埃暗纹用第二块 fbm 压暗，云才有结构而不是一团亮气。 */
  vec2 uA = (c + cam * 0.40 + par * 0.35) * 1.05 + vec2(t * 0.30, 0.0);
  float nA = fbm(uA);
  float mA = smoothstep(0.34, 0.90, nA);
  mA *= 0.50 + 0.50 * smoothstep(0.30, 0.75, fbm(uA * 1.8 + 7.3));
  float ampA = 0.95 + 0.85 * (1.0 - smoothstep(0.30, 0.85, p));
  vec3 colA = mix(vec3(0.03, 0.10, 0.28), vec3(0.16, 0.64, 0.75),
                  smoothstep(0.40, 0.88, nA));
  col += colA * mA * ampA;

  /* —— 云系 B：紫品红星云（中段浮现，绚丽色的来源之一）—— */
  vec2 uB = (c + cam * 0.62 + par * 0.65) * 1.25 + vec2(4.7, 0.0) + vec2(-t * 0.25, t * 0.12);
  float nB = fbm(uB);
  float mB = smoothstep(0.44, 0.96, nB);
  mB *= 0.50 + 0.50 * smoothstep(0.35, 0.80, fbm(uB * 1.7 + 13.1));
  float ampB = smoothstep(0.12, 0.50, p) * (1.0 - 0.45 * smoothstep(0.72, 1.0, p));
  vec3 colB = mix(vec3(0.09, 0.03, 0.22), vec3(0.78, 0.30, 0.80),
                  smoothstep(0.46, 0.92, nB));
  col += colB * mB * ampB;

  /* —— 云系 C：暖金余烬云（末段的高潮）—— */
  vec2 uC = (c + cam * 0.85 + par * 1.05) * 1.40 + vec2(9.2, 3.3) + vec2(t * 0.20, -t * 0.15);
  float nC = fbm(uC);
  float mC = smoothstep(0.38, 0.92, nC);
  mC *= 0.55 + 0.45 * smoothstep(0.36, 0.82, fbm(uC * 1.6 + 3.9));
  float ampC = smoothstep(0.35, 0.80, p) * 1.35;
  vec3 colC = mix(vec3(0.30, 0.12, 0.04), vec3(1.00, 0.64, 0.24),
                  smoothstep(0.42, 0.90, nC));
  col += colC * mC * ampC;

  /* 云系核心的柔光：高密度区加一点 bloom 感 */
  col += colA * mA * mA * 0.35 + colB * mB * mB * 0.40 + colC * mC * mC * 0.45;

  /* —— 星空：四层视差。近层又大又亮、带柔光晕 —— */
  float starAmt = 0.80 + 0.55 * p;
  vec2 s1 = c + cam * 0.55 + par * 0.5;
  vec2 s2 = c + cam * 0.90 + par * 0.9;
  vec2 s3 = c + cam * 1.30 + par * 1.4;
  vec2 s4 = c + cam * 1.90 + par * 2.0;
  vec3 st = starLayer(s1, 24.0, 0.90, 0.10, u_t)
          + starLayer(s2, 13.0, 0.88, 0.085, u_t) * 1.6
          + starLayer(s3, 7.0, 0.87, 0.070, u_t) * 2.4;
  col += st * starAmt;
  /* 最亮几颗（近层）：加一圈柔光晕，成为画面的"锚点星" */
  vec3 g4 = vec3(0.0);
  {
    vec2 gg = s4 * 4.2;
    vec2 id = floor(gg);
    vec2 f = fract(gg) - 0.5;
    float h = hash(id);
    if (h > 0.90){
      vec2 sp = (vec2(hash(id + 19.7), hash(id + 47.3)) - 0.5) * 0.8;
      float d = length(f - sp);
      float lum = 0.5 + 0.5 * fract(h * 91.7);
      float twk = 0.75 + 0.25 * sin(u_t * (0.5 + h) + h * 61.0);
      vec3 tint = mix(vec3(0.85, 0.90, 1.00), vec3(1.00, 0.80, 0.55),
                      step(0.6, fract(h * 37.1)));
      g4 = tint * (smoothstep(0.045, 0.0, d) * 1.6 + exp(-14.0 * d) * 0.35) * lum * twk;
    }
  }
  col += g4 * starAmt;

  /* —— 流星：约 8 秒一颗的偶发，短的亮线划过（克制，但"活"）—— */
  {
    float mt = u_t * 0.125;
    float slot = floor(mt);
    float age = fract(mt);
    if (hash(vec2(slot, 7.7)) < 0.55){          /* 有的周期干脆没有流星 */
      vec2 mp = vec2(hash(vec2(slot, 1.3)) - 0.5, hash(vec2(slot, 9.1)) - 0.5) * 1.1;
      vec2 dir = normalize(vec2(hash(vec2(slot, 3.1)) - 0.5,
                                0.35 + hash(vec2(slot, 5.5)) - 0.5));
      float head = age * 1.7 - 0.25;
      vec2 pa = mp + dir * max(head - 0.55, 0.0);
      vec2 pb = mp + dir * head;
      vec2 ab = pb - pa;
      float ab2 = max(dot(ab, ab), 0.0001);
      float dline = length(c - (pa + ab * clamp(dot(c - pa, ab) / ab2, 0.0, 1.0)));
      float life = smoothstep(0.0, 0.12, age) * (1.0 - smoothstep(0.55, 1.0, age));
      col += vec3(0.85, 0.92, 1.00) * exp(-46.0 * dline) * life * 0.5 * starAmt;
    }
  }

  /* 鼠标"提灯"：跟随光晕；悬停按钮/入口/进度点时增亮变暖 */
  vec2 mc = u_m * 0.5 * vec2(u_res.x / min(u_res.x, u_res.y), 1.0);
  float aura = exp(-5.5 * length(c - mc));
  col += vec3(0.30, 0.78, 0.86) * aura * (0.030 + 0.060 * u_boost)
       + vec3(1.00, 0.58, 0.20) * aura * aura * u_boost * 0.10;

  /* 涟漪：点击处向外扩散的柔光环 */
  for (int i = 0; i < 3; i++){
    vec4 r = u_r[i];
    if (r.w > 0.0 && r.z < 2.2){
      float dd = length(c - r.xy);
      float ring = exp(-26.0 * abs(dd - r.z * 0.55)) * exp(-1.8 * r.z) * r.w;
      col += mix(vec3(0.30, 0.78, 0.86), vec3(1.00, 0.64, 0.24),
                 smoothstep(0.2, 0.85, p)) * ring * 0.5;
    }
  }

  /* 曝光随 p 抬升：越深入越亮（cdp_verify_nav 的滚动亮度依据） */
  col *= 0.66 + 0.50 * p;

  /* 高光泛光 + 极细颗粒（防渐变色带，不是星星） */
  col += max(col - 0.42, vec3(0.0)) * 0.55;
  col += (hash(gl_FragCoord.xy + fract(u_t) * 61.7) - 0.5) * 0.011;

  /* 暗角：四角保持"空" */
  float vig = smoothstep(1.32, 0.30, length(c));
  col *= 0.34 + 0.66 * vig;

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
    lf.innerHTML =
      '<span class="it"><span class="v">' + n.toLocaleString() + '</span>'
      + '<span class="l">实体 · 真实抽取</span></span>'
      + '<span class="it"><span class="v">' + e.toLocaleString() + '</span>'
      + '<span class="l">关系 · 评测全集 43867</span></span>'
      + '<span class="it"><span class="v">' + c + '</span>'
      + '<span class="l">社区 · Louvain</span></span>';
  })
  .catch(() => {});
