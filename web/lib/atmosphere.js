/* ═══════════════════════════════════════════════════════════════════════════
   KG Cosmos — 全站统一光场（atmosphere.js）
   ───────────────────────────────────────────────────────────────────────────

   一个模块，全站共用。**同一套材质，只调速度与增益**（见 design.css 的
   .atmo-l0 / .atmo-l1 / .atmo-l2）。这样"看起来是一家的"来自同一套光，
   而不是来自把同一个背景硬贴到每个页面上。

   它有什么（对齐"绸缎"那版的能力，不能更差）：
     · **真实动效**：WebGL 片元着色器实时生成，时间驱动，不滚动也在流；
     · **真实交互**：鼠标视差（阻尼跟随），随光场层级缩放；
     · **滚动响应**：光斑位置与色调随滚动进度缓慢位移；
     · 域扭曲（domain warping）出纤维状结构，不是糊成一团。

   工程上的几条纪律：
     · **零外部请求**，无构建步骤，ES module 直接 import；
     · **DPR 上限 1.5**（3× 屏看不出差别，像素少一半多）；
     · **页面不可见时停 rAF**（省电，也避免后台标签占 GPU）；
     · **reduced-motion 直接不启动**，交给 CSS 渐降级；
     · **WebGL 不可用时静默降级**到 CSS 渐变，绝不留白屏。

   用法：
     import { startAtmosphere } from './lib/atmosphere.js';
     startAtmosphere();                 // 读 body 上的 .atmo-lN 决定强度
   ═══════════════════════════════════════════════════════════════════════════ */

const VS = `attribute vec2 a; void main(){ gl_Position = vec4(a, 0.0, 1.0); }`;

const FS = `
precision highp float;
uniform vec2  u_res;
uniform float u_t;      /* 秒 */
uniform vec2  u_m;      /* 鼠标，已阻尼，-1..1 */
uniform float u_p;      /* 滚动进度 0..1 */
uniform float u_gain;   /* 层级增益：氛围页 1，阅读页 .3 */

float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }

float noise(vec2 p){
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

/* 只用 3 层：层数一多就变成"沙子"，这里要的是大尺度柔和起伏 */
float fbm(vec2 p){
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 3; i++){ v += a * noise(p); p *= 2.07; a *= 0.5; }
  return v;
}

/* 域扭曲：拿噪声去扭曲噪声的采样坐标 → 纤维状结构，而不是模糊光斑 */
vec2 warp(vec2 p, float t){
  vec2 q = vec2(fbm(p + vec2(0.0, t)),
                fbm(p + vec2(5.2, 1.3) - vec2(t * 0.8, 0.0)));
  vec2 r = vec2(fbm(p + 2.2 * q + vec2(1.7, 9.2) + vec2(t * 1.1, 0.0)),
                fbm(p + 2.2 * q + vec2(8.3, 2.8) - vec2(0.0, t * 0.9)));
  return p + 2.4 * r;
}

void main(){
  vec2 c = (gl_FragCoord.xy - 0.5 * u_res) / min(u_res.x, u_res.y);
  float t = u_t;

  /* 两个光斑：位置随滚动缓缓平移（不是换一张图），并跟随鼠标做极轻微视差 */
  vec2 g1 = vec2(-0.52 + 0.42 * u_p, 0.10 - 0.30 * u_p) + u_m * 0.055;
  vec2 g2 = vec2( 0.60 - 0.34 * u_p, -0.08 + 0.34 * u_p) + u_m * 0.040;

  float fib  = fbm(warp(c * 1.05, t * 0.05));
  float body = smoothstep(0.34, 0.78, fib);

  float l1 = smoothstep(1.16, 0.05, length(c - g1) + (fib - 0.5) * 0.55);
  float l2 = smoothstep(1.16, 0.02, length(c - g2) + (fib - 0.5) * 0.50);

  /* 底色：深靛，不是纯黑。阅读页把增益压到 .3，正文才压得住。 */
  vec3 col = vec3(0.010, 0.013, 0.023);
  vec3 teal = vec3(0.11, 0.45, 0.53);
  vec3 gold = vec3(0.78, 0.55, 0.30);

  col += teal * l1 * (0.60 + 0.40 * body) * u_gain;
  col += gold * l2 * (0.50 + 0.50 * body) * u_gain;
  col += teal * pow(body, 3.0) * l1 * 0.24 * u_gain;
  col += gold * pow(body, 3.0) * l2 * 0.24 * u_gain;

  /* 极细颗粒，防大面积渐变色带（不是"星星点缀"） */
  col += (hash(gl_FragCoord.xy * 0.7) - 0.5) * 0.009 * u_gain;

  /* 暗角：把视线收拢到中心，四角保持空 */
  col *= 0.40 + 0.60 * smoothstep(1.35, 0.20, length(c));

  gl_FragColor = vec4(col, 1.0);
}`;

function compile(gl, type, src){
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(s);
    gl.deleteShader(s);
    throw new Error('shader: ' + log);
  }
  return s;
}

/**
 * 启动全站光场。
 * @param {object} [opts]
 * @param {string} [opts.canvasId='atmo'] 画布 id
 * @param {boolean} [opts.parallax=true]  是否启用鼠标视差
 * @returns {{stop:Function, setGain:Function}|null} null 表示未启动（降级）
 */
export function startAtmosphere(opts = {}) {
  const canvasId = opts.canvasId || 'atmo';
  const wantParallax = opts.parallax !== false;

  const reduced = typeof matchMedia === 'function'
    && matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) return null;                       // 交给 CSS 降级

  let canvas = document.getElementById(canvasId);
  if (!canvas) {                                  // 页面没写画布就自己建一个
    canvas = document.createElement('canvas');
    canvas.id = canvasId;
    canvas.setAttribute('aria-hidden', 'true');
    document.body.insertBefore(canvas, document.body.firstChild);
  }

  let gl = null;
  try {
    gl = canvas.getContext('webgl', {
      antialias: false, alpha: false, powerPreference: 'low-power',
      preserveDrawingBuffer: true,   // 供 CDP 回归读像素
    }) || canvas.getContext('experimental-webgl');
  } catch (e) { gl = null; }
  if (!gl) { canvas.style.display = 'none'; return null; }   // 静默降级

  let prog;
  try {
    prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(prog));
    }
  } catch (e) {
    canvas.style.display = 'none';
    return null;
  }
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'a');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  const U = {
    res: gl.getUniformLocation(prog, 'u_res'),
    t: gl.getUniformLocation(prog, 'u_t'),
    m: gl.getUniformLocation(prog, 'u_m'),
    p: gl.getUniformLocation(prog, 'u_p'),
    gain: gl.getUniformLocation(prog, 'u_gain'),
  };

  /* 层级参数从 CSS 变量读，这样"分级"只有一处定义（design.css） */
  function readCfg() {
    const cs = getComputedStyle(document.documentElement);
    const body = getComputedStyle(document.body);
    const pick = (name, dflt) => {
      const v = (body.getPropertyValue(name) || cs.getPropertyValue(name) || '').trim();
      const n = parseFloat(v);
      return isNaN(n) ? dflt : n;
    };
    return { speed: pick('--atmo-speed', 1), gain: pick('--atmo-gain', 1) };
  }
  let cfg = readCfg();

  const dpr = Math.min(1.5, window.devicePixelRatio || 1);
  let W = 0, H = 0;
  function resize() {
    W = Math.max(1, Math.round(innerWidth * dpr));
    H = Math.max(1, Math.round(innerHeight * dpr));
    canvas.width = W;
    canvas.height = H;
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    gl.viewport(0, 0, W, H);
  }
  resize();
  addEventListener('resize', resize, { passive: true });

  /* 鼠标视差：阻尼跟随，绝不硬跟 */
  let mx = 0, my = 0, sx = 0, sy = 0;
  if (wantParallax) {
    addEventListener('pointermove', (e) => {
      mx = (e.clientX / innerWidth) * 2 - 1;
      my = -((e.clientY / innerHeight) * 2 - 1);
    }, { passive: true });
  }

  let p = 0, targetP = 0, raf = 0, alive = true;
  function onScroll() {
    const max = Math.max(1, document.documentElement.scrollHeight - innerHeight);
    targetP = Math.min(1, Math.max(0, scrollY / max));
  }
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* 页面不可见时停 rAF —— 后台标签不该占 GPU */
  let visible = !document.hidden;
  document.addEventListener('visibilitychange', () => {
    visible = !document.hidden;
    if (visible && alive && !raf) raf = requestAnimationFrame(loop);
  });

  const t0 = performance.now();
  let last = t0;

  function loop(now) {
    raf = 0;
    if (!alive) return;
    if (!visible) return;                       // 等 visibilitychange 叫醒
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;

    p += (targetP - p) * Math.min(1, dt * 3.4);          // 滚动阻尼
    sx += (mx - sx) * Math.min(1, dt * 2.2);             // 视差阻尼
    sy += (my - sy) * Math.min(1, dt * 2.2);

    gl.uniform2f(U.res, W, H);
    gl.uniform1f(U.t, ((now - t0) / 1000) * cfg.speed);  // 时间驱动（按层级缩放）
    gl.uniform2f(U.m, sx, sy);
    gl.uniform1f(U.p, p);
    gl.uniform1f(U.gain, cfg.gain);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    raf = requestAnimationFrame(loop);
  }

  raf = requestAnimationFrame(loop);
  canvas.classList.add('on');

  return {
    stop() {
      alive = false;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    },
    /** 运行中切换层级（比如打开阅读面板时压暗背景） */
    setGain(g) {
      cfg = { ...cfg, gain: g };
    },
    /** 重新读取 CSS 层级变量 */
    refresh() { cfg = readCfg(); },
  };
}

export default startAtmosphere;
