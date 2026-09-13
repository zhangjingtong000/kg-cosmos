/* ═══════════════════════════════════════════════════════════════════════════
   KG COSMOS — 滚动驱动的宇宙演化
   ───────────────────────────────────────────────────────────────────────────
   整页是一条时间轴：**滚动进度 = 动画进度**。所有视觉量都由同一个 p∈[0,1]
   插值得到，因此章节之间没有任何硬切。

   五个关键帧（对应五个章节）：
     p=0.00  静谧深空    点云呈均匀星空、相机很远、冷色、无飞线
     p=0.25  靠近        星空开始"认得出来"、轮廓出现、视差明显
     p=0.50  进入星云    形态转为星云结构、飞线浮现、色温转暖
     p=0.75  星云内部    旋臂展开、飞线最亮、雾最浓
     p=1.00  拉远        星云收回成孤岛、色温回落

   三件事让它是"一个动画"而不是"五段动画"：
     ① 相机沿**一条样条**运动（CatmullRomCurve3），不是分段插值；
     ② 形态用 `mix(星空坐标, 星云坐标, shapeOf(p))` 连续形变；
     ③ 进度带**阻尼**（滚动停下后画面还会继续飘一会儿）。

   数据全部来自项目自己的抽取结果，零外部请求。
   ═══════════════════════════════════════════════════════════════════════════ */
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const canvas  = document.getElementById('sky');
const stage   = document.getElementById('stage');
const reel    = document.getElementById('reel');

/* ── 渲染器 ─────────────────────────────────────────────────────────────── */
const renderer = new THREE.WebGLRenderer({
  canvas, antialias: false, alpha: true, powerPreference: 'high-performance'
});
renderer.setClearColor(0x01030a, 0);
const scene  = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(52, 1, 1, 90000);
scene.fog = new THREE.FogExp2(0x03060f, 0.000022);

/* ── 进度：滚动 → 阻尼后的连续值 ───────────────────────────────────────── */
let targetP = 0, curP = 0;
let mx = 0, my = 0;          // 鼠标视差（在 build 之前声明：模块是严格模式，
                             // 未声明的赋值会直接抛错）
function readScroll() {
  const total = Math.max(1, reel.scrollHeight - innerHeight);
  targetP = Math.min(1, Math.max(0, scrollY / total));
}

/* ── 关键帧工具：给一组 (t, v) 做平滑插值 ───────────────────────────────── */
function kf(t, pts) {
  if (t <= pts[0][0]) return pts[0][1];
  for (let i = 0; i < pts.length - 1; i++) {
    const [t0, v0] = pts[i], [t1, v1] = pts[i + 1];
    if (t <= t1) {
      const u = (t - t0) / (t1 - t0);
      return v0 + (v1 - v0) * (u * u * (3 - 2 * u));   // smoothstep，避免生硬
    }
  }
  return pts[pts.length - 1][1];
}

/* ── 点云着色器：同一套代码画星空与星云 ──────────────────────────────────
   每个点有两套坐标：
     aStar  均匀分布的星空（p=0 时用）
     aNeb   真实图谱坐标（p 大时用）
   uShape 在两者之间连续混合 —— 这就是"星空变星云"的实现。 */
const PT_VS = `
  attribute vec3 aStar;        // 星空形态坐标
  attribute vec3 aColor;
  attribute float aSeed;
  attribute float aSize;
  attribute float aCloud;      // 1 = 云雾颗粒（大而淡），0 = 普通星点
  uniform float uPix, uTime, uShape, uSpread, uFade, uBrail;
  varying vec3 vC;
  varying float vA;
  void main(){
    vC = aColor;
    // 形态混合：0 = 纯星空，1 = 纯星云
    vec3 p = mix(aStar, position, uShape);
    // 入场前后有一点整体扩张，暗示"还在成形"
    p *= mix(1.0, uSpread, 1.0 - uFade);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;

    float dist = -mv.z;
    // ⚠️ 上一版这里是"三重衰减"叠加，把点整个吃掉了：
    //     相机 z=21000 → persp=3200/21000≈0.15，fog≈0.13，
    //     最终 vA≈0.02 ⇒ 画面上什么都看不见。
    //   现在：透视下限抬到 0.55，雾只做很轻的远处压暗，且范围拉大。
    float persp = clamp(7000.0 / max(900.0, dist), 0.55, 2.2);
    float fog = clamp(1.0 - (dist - 4000.0) / 40000.0, 0.45, 1.0);

    // 呼吸：极慢、极小幅度，只是让画面活着
    float br = 0.82 + 0.18 * sin(uTime * 0.5 + aSeed * 6.2831);
    vA = aSize * br * persp * fog * uFade * uBrail * 1.35;

    // 云雾颗粒：**大而极淡**，是"云"与"一盘散沙"的分界线。
    // 只在星云成形后放大（乘 uShape），星空阶段保持纯净。
    float cloudMul = 1.0 + aCloud * (2.6 + 8.0 * uShape);
    gl_PointSize = uPix * (1.5 + aSize * 3.0) * cloudMul * persp
                   * (0.7 + 0.3 * uFade);
    vA *= mix(1.0, 0.15, aCloud) * (1.0 + aCloud * 1.6 * uShape);
  }`;
const PT_FS = `
  varying vec3 vC;
  varying float vA;
  void main(){
    vec2 d = gl_PointCoord - 0.5;
    float r = length(d) * 2.0;
    if (r > 1.0) discard;
    // 三层衰减：核心很亮、中段柔和、外缘几乎不可见 —— 真实光斑的观感
    float core = pow(1.0 - r, 3.4);
    float halo = pow(1.0 - r, 1.05) * 0.36;
    gl_FragColor = vec4(mix(vC, vec3(1.0), core * 0.8), (core + halo) * vA);
  }`;

/* 飞线：沿边奔跑的光脉冲。只在进入星云后才亮起。 */
const LN_VS = `
  attribute vec3 aColor;
  attribute float aSeed;
  uniform float uTime, uFlow, uAlpha;
  varying vec3 vC;
  varying float vA;
  void main(){
    vC = aColor;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    // 脉冲沿线段流动（用两边顶点不同的相位近似）
    float pulse = pow(0.5 + 0.5 * sin(uTime * 1.6 + aSeed * 20.0), 4.0);
    float dist = -mv.z;
    float fog = clamp(1.0 - (dist - 1200.0) / 18000.0, 0.0, 1.0);
    vA = (0.10 + 0.90 * pulse) * uFlow * uAlpha * fog;
  }`;
const LN_FS = `
  varying vec3 vC;
  varying float vA;
  void main(){ gl_FragColor = vec4(vC, vA * 0.5); }`;

/* ── 构建 ───────────────────────────────────────────────────────────────── */
let cloudMat = null, glowMats = [], lineMat = null, composer = null, bloom = null;
let heroH = 1;

fetch('./kg-data/nebula.json')
  .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(build)
  .catch(err => {
    console.warn('[voyage] nebula.json 不可用，退化为纯星空：', err.message);
    build({ nodes: [], edges: [], communities: [], type_colors: {} });
  });

function build(D) {
  const nodes = D.nodes || [];
  const edges = D.edges || [];
  const comms = D.communities || [];
  const colors = D.type_colors || {};
  const N = nodes.length;

  /* 质心：把图谱坐标搬到原点附近 */
  let cx = 0, cy = 0, cz = 0;
  for (const n of nodes) { cx += n.x; cy += n.y; cz += n.z; }
  if (N) { cx /= N; cy /= N; cz /= N; }

  const typeNames = Object.keys(colors);
  const tIdx = {}; typeNames.forEach((t, i) => tIdx[t] = i);
  const palette = typeNames.map(t => new THREE.Color(colors[t]));
  const FALLBACK = new THREE.Color(0x8fb6ff);

  /* 60 个社区的色调：深空摄影的色温层次，不是一片蓝 */
  const HUE = [0x6fa8ff,0x9d7bff,0x66e0c8,0xffb066,0xff7ea8,0x7fd8ff,0xb8ff7a,
               0xffd166,0x8f9cff,0x5fd0a8,0xff9a6b,0xa8b8ff,0x74e6ff,0xd18cff,
               0xffe08a,0x7fffd4,0xff8fb0,0x9fd0ff,0xc0a8ff,0x88e0a0];
  const commColor = c => {
    const base = new THREE.Color(HUE[Math.abs((c * 7 + 3) % HUE.length)]);
    const hsl = {}; base.getHSL(hsl);
    base.setHSL((hsl.h + c * 0.012) % 1, Math.min(1, hsl.s * 1.1), hsl.l * 1.08);
    return base;
  };

  /* ── 点属性 ── */
  const pos   = new Float32Array(N * 3);   // 星云形态（真实坐标）
  const star  = new Float32Array(N * 3);   // 星空形态（均匀球壳）
  const col   = new Float32Array(N * 3);
  const seed  = new Float32Array(N);
  const size  = new Float32Array(N);
  const cloud = new Float32Array(N);   // 1 = 云雾颗粒（大而淡）

  let maxD = 1;
  for (const n of nodes) maxD = Math.max(maxD, n.d || 1);

  const at = new Map();
  const tmp = new THREE.Color();

  for (let i = 0; i < N; i++) {
    const n = nodes[i];
    // 星云形态：真实坐标 + 按距离的螺旋缠绕（出现旋臂）
    let x = (n.x - cx) * 0.9, y = (n.y - cy) * 0.9, z = (n.z - cz) * 0.9;
    const r = Math.hypot(x, z);
    const a = (r / 1600) * 0.5;
    const ca = Math.cos(a), sa = Math.sin(a);
    pos[i*3]   = x * ca - z * sa;
    pos[i*3+1] = y * 0.34;                      // 压扁成盘状
    pos[i*3+2] = x * sa + z * ca;
    at.set(n.id, i);

    // 星空形态：**不要均匀球壳**。均匀分布看上去就是一层噪声。
    // 改成"沿几条旋臂聚簇 + 少量弥散"，星空才有结构感。
    const arm = Math.floor(Math.random() * 3);          // 3 条旋臂
    const along = Math.random();                         // 沿臂的位置
    const spread = (Math.random() - 0.5) * (0.35 + 0.5 * along);
    const th = arm * (Math.PI * 2 / 3) + along * 2.4 + spread;
    const ph = Math.acos(1 - 2 * (0.15 + 0.7 * along))
               + (Math.random() - 0.5) * 0.5;
    const R = 2400 + Math.random() * 2600;
    // 15% 完全弥散（不归属任何旋臂），避免结构太"人造"
    const strayed = Math.random() < 0.15;
    const th2 = strayed ? Math.random() * Math.PI * 2 : th;
    const ph2 = strayed ? Math.acos(2 * Math.random() - 1) : ph;
    star[i*3]   = R * Math.sin(ph2) * Math.cos(th2);
    star[i*3+1] = R * Math.cos(ph2) * 0.75;
    star[i*3+2] = R * Math.sin(ph2) * Math.sin(th2);

    // 颜色：社区色 → 枢纽混暖白（深空照片里核心发白）
    if (N && colors[n.t]) tmp.set(colors[n.t]); else tmp.copy(FALLBACK);
    if (colors[n.t] === undefined && comms.length) tmp.copy(commColor(n.c ?? 0));
    const dnorm = Math.pow((n.d || 1) / maxD, 0.42);
    tmp.lerp(new THREE.Color(0xffeedd), dnorm * 0.30);
    col[i*3] = tmp.r; col[i*3+1] = tmp.g; col[i*3+2] = tmp.b;

    seed[i] = Math.random();
    // 大小：度数拉开档次；再加"暗尘"制造层次（均匀亮度会像沙子）
    let w = 0.26 + dnorm * 1.9;
    if (Math.random() < 0.34) w *= 0.10 + Math.random() * 0.22;
    size[i] = Math.min(1.6, w);
    // 约 28% 的点承担"云雾"角色：着色器里会被放大 2.6–10 倍、
    // 透明度降到 0.15 倍，靠大量半透明叠加形成体积感。
    // 这是"云"与"一盘散沙"的分界线。
    cloud[i] = Math.random() < 0.28 ? 1 : 0;
  }

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('aStar',    new THREE.BufferAttribute(star, 3));
  g.setAttribute('aColor',   new THREE.BufferAttribute(col, 3));
  g.setAttribute('aSeed',    new THREE.BufferAttribute(seed, 1));
  g.setAttribute('aSize',    new THREE.BufferAttribute(size, 1));
  g.setAttribute('aCloud',   new THREE.BufferAttribute(cloud, 1));

  cloudMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: {
      uPix: { value: 1 }, uTime: { value: 0 }, uShape: { value: 0 },
      uSpread: { value: 1.6 }, uFade: { value: 0 }, uBrail: { value: 1 }
    },
    vertexShader: PT_VS, fragmentShader: PT_FS
  });
  scene.add(new THREE.Points(g, cloudMat));

  /* ── 飞线：采样。优先保留端点度数高的边（它们构成骨架） ── */
  const scored = [];
  for (const e of edges) {
    const a = at.get(e[0]), b = at.get(e[1]);
    if (a === undefined || b === undefined) continue;
    scored.push({ a, b, s: (nodes[a].d || 1) + (nodes[b].d || 1) });
  }
  scored.sort((p, q) => q.s - p.s);
  const TARGET = 5000;
  const pick = scored.length <= TARGET ? scored
    : scored.slice(0, Math.floor(TARGET * 0.7))
        .concat(scored.slice(Math.floor(TARGET * 0.7))
          .sort(() => Math.random() - 0.5).slice(0, TARGET - Math.floor(TARGET * 0.7)));

  if (pick.length) {
    const lp = new Float32Array(pick.length * 6);
    const lc = new Float32Array(pick.length * 6);
    const ls = new Float32Array(pick.length * 2);
    pick.forEach((p, k) => {
      lp[k*6]   = pos[p.a*3];   lp[k*6+1] = pos[p.a*3+1]; lp[k*6+2] = pos[p.a*3+2];
      lp[k*6+3] = pos[p.b*3];   lp[k*6+4] = pos[p.b*3+1]; lp[k*6+5] = pos[p.b*3+2];
      const ca = [col[p.a*3], col[p.a*3+1], col[p.a*3+2]];
      const cb = [col[p.b*3], col[p.b*3+1], col[p.b*3+2]];
      lc[k*6]   = ca[0]*0.5; lc[k*6+1] = ca[1]*0.5; lc[k*6+2] = ca[2]*0.5;
      lc[k*6+3] = cb[0]*0.5; lc[k*6+4] = cb[1]*0.5; lc[k*6+5] = cb[2]*0.5;
      const sd = Math.random();
      ls[k*2] = sd; ls[k*2+1] = sd + 0.5;      // 两端相位错开 → 脉冲沿边流动
    });
    const lg = new THREE.BufferGeometry();
    lg.setAttribute('position', new THREE.BufferAttribute(lp, 3));
    lg.setAttribute('aColor',   new THREE.BufferAttribute(lc, 3));
    lg.setAttribute('aSeed',    new THREE.BufferAttribute(ls, 1));
    lineMat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uTime: { value: 0 }, uFlow: { value: 0 }, uAlpha: { value: 0 } },
      vertexShader: LN_VS, fragmentShader: LN_FS
    });
    scene.add(new THREE.LineSegments(lg, lineMat));
  }

  /* ── 社区核：柔和大光晕，进入后才显形 ── */
  const tex = (() => {
    const s = 256, cv = document.createElement('canvas');
    cv.width = cv.height = s;
    const c2 = cv.getContext('2d');
    const gr = c2.createRadialGradient(s/2, s/2, 0, s/2, s/2, s/2);
    gr.addColorStop(0,    'rgba(255,248,236,.92)');
    gr.addColorStop(0.16, 'rgba(200,220,255,.40)');
    gr.addColorStop(0.5,  'rgba(150,190,255,.10)');
    gr.addColorStop(1,    'rgba(120,160,255,0)');
    c2.fillStyle = gr; c2.fillRect(0, 0, s, s);
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
    return t;
  })();
  comms.forEach(cm => {
    const c = cm.center || [0, 0, 0];
    const m = new THREE.SpriteMaterial({
      map: tex, color: commColor(cm.id ?? 0), transparent: true,
      depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0
    });
    const sp = new THREE.Sprite(m);
    // 位置要与点云一致：同样减去质心、同样压扁、同样缠绕
    let x = (c[0] - cx) * 0.9, y = (c[1] - cy) * 0.9, z = (c[2] - cz) * 0.9;
    const r = Math.hypot(x, z), a = (r / 1600) * 0.5;
    sp.position.set(x * Math.cos(a) - z * Math.sin(a), y * 0.34,
                    x * Math.sin(a) + z * Math.cos(a));
    const rad = Math.max(150, (cm.radius || 240) * 1.15);
    sp.scale.set(rad, rad, 1);
    scene.add(sp); glowMats.push(m);
  });

  /* ── 相机：**一条样条**贯穿全程（不是分段插值） ── */
  const CAM = new THREE.CatmullRomCurve3([
    new THREE.Vector3(    0,   420, 8600),    // p=0   静观（原来 21000 太远，点被衰减吃掉）
    new THREE.Vector3( -1400,  300, 6200),    // p=.25 侧向靠近
    new THREE.Vector3( 1600,   140,  3000),   // p=.5  进入星云
    new THREE.Vector3( -400,   800,  1700),   // p=.75 内部穿行
    new THREE.Vector3(  700,  2200,  9800)    // p=1   拉远成孤岛
  ], false, 'catmullrom', 0.4);
  const LOOK = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0,    0, 0),
    new THREE.Vector3(-300, 0, 0),
    new THREE.Vector3( 200, 0, 0),
    new THREE.Vector3(-100, 300, 0),
    new THREE.Vector3(0,    0, 0)
  ], false, 'catmullrom', 0.4);

  /* ── 后处理：Bloom 是"发光"与"一堆亮点"的分界线 ── */
  composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight),
                              0.72, 0.82, 0.38);  // 阈值抬高：压住核心过曝
  composer.addPass(bloom);

  function layout() {
    const w = stage.clientWidth, h = stage.clientHeight;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    renderer.setPixelRatio(dpr);
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    heroH = h || 1;
    if (cloudMat) cloudMat.uniforms.uPix.value = dpr * (h < 700 ? 1.0 : 1.25);
  }
  layout();
  addEventListener('resize', layout);

  readScroll();
  addEventListener('scroll', readScroll, { passive: true });
  addEventListener('pointermove', e => {
    mx = (e.clientX / innerWidth - 0.5) * 2;
    my = (e.clientY / innerHeight - 0.5) * 2;
  }, { passive: true });

  const clock = new THREE.Clock();
  const t0 = performance.now();
  const camPos = new THREE.Vector3(), camLook = new THREE.Vector3();

  (function frame() {
    const t = clock.getElapsedTime();
    const el = performance.now() - t0;

    // ── 关键：进度带阻尼。滚动停下后画面还会继续飘一会儿 ──
    curP += (targetP - curP) * (REDUCED ? 1 : 0.055);
    const p = curP;

    // ── 入场（0–2.4s）：整体从扩张状态收敛 ──
    const eIn = Math.min(1, el / 2400);
    const ease = 1 - Math.pow(1 - eIn, 3);

    /* 六条同时被 p 驱动的曲线 */
    const shape   = kf(p, [[0, 0.0], [0.18, 0.05], [0.42, 0.55], [0.62, 0.95], [0.85, 1.0], [1, 0.86]]);
    const flow    = kf(p, [[0, 0.0], [0.34, 0.0], [0.52, 0.85], [0.78, 1.0], [1, 0.25]]);
    const glow    = kf(p, [[0, 0.0], [0.30, 0.0], [0.55, 0.9], [0.8, 1.0], [1, 0.35]]);
    const warm    = kf(p, [[0, 0.0], [0.3, 0.15], [0.55, 0.7], [0.8, 1.0], [1, 0.42]]);
    const fogD    = kf(p, [[0, 0.000010], [0.5, 0.000030], [0.8, 0.000050], [1, 0.000022]]);
    // ③ 进入星云内部（p>0.7）后整体降亮：相机贴近时大量半透明光斑会
    //    叠加成一个白球，必须主动压下去，才能保住细节。
    const brail   = kf(p, [[0, 0.55], [0.25, 0.9], [0.5, 1.0], [0.7, 0.82], [0.88, 0.42], [1, 0.3]]);

    if (cloudMat) {
      cloudMat.uniforms.uTime.value  = t;
      cloudMat.uniforms.uShape.value = shape * ease;
      cloudMat.uniforms.uFade.value  = ease;
      cloudMat.uniforms.uSpread.value = 1 + (1 - ease) * 0.75;
      cloudMat.uniforms.uBrail.value = brail;
    }
    if (lineMat) {
      lineMat.uniforms.uTime.value  = t;
      lineMat.uniforms.uFlow.value  = flow;
      lineMat.uniforms.uAlpha.value = ease * shape;   // 飞线只在星云成形后可见
    }
    glowMats.forEach((m, i) => {
      // 社区核依次点亮：错开相位，形成"一个个亮起来"的顺序感
      const seq = Math.min(1, Math.max(0, (el - 900 - i * 260) / 1400));
      m.opacity = glow * seq * 0.26;
    });
    scene.fog.density = fogD;
    if (bloom) bloom.strength = 0.62 + warm * 0.5;

    /* 色温：冷蓝 → 暖金。用 CSS 变量驱动叠加层，与 WebGL 同步 */
    const r0 = 127, g0 = 179, b0 = 255;
    const r1 = 255, g1 = 206, b1 = 150;
    const cr = Math.round(r0 + (r1 - r0) * warm);
    const cg = Math.round(g0 + (g1 - g0) * warm);
    const cb = Math.round(b0 + (b1 - b0) * warm);
    document.documentElement.style.setProperty('--warmRGB', `${cr},${cg},${cb}`);

    /* 相机沿样条；鼠标只做很轻的偏移，不抢主运动 */
    const q = Math.min(0.999, Math.max(0.001, p));
    CAM.getPointAt(q, camPos);
    LOOK.getPointAt(q, camLook);
    if (!REDUCED) {
      camPos.x += mx * 260;
      camPos.y += -my * 180;
      // 极轻的手持噪声，避免"机器臂"般的僵硬
      camPos.x += Math.sin(t * 0.37) * 34;
      camPos.y += Math.cos(t * 0.29) * 26;
      camLook.y += Math.sin(t * 0.21) * 40;
    }
    camera.position.copy(camPos);
    camera.lookAt(camLook);
    if (!REDUCED) camera.rotation.z = Math.sin(t * 0.23) * 0.012 + mx * 0.008;

    composer.render();
    requestAnimationFrame(frame);
  })();

  window.__ready = true;
}

/* ── 章节入场（只做一次）与右侧进度点 ───────────────────────────────────── */
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

/* ── 数字滚动（进入视口时） ─────────────────────────────────────────────── */
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
    el.innerHTML = (dec ? to.toFixed(dec) : Math.round(to).toLocaleString()) +
                   (u ? u.outerHTML : '');
  });
} else {
  const nio = new IntersectionObserver(es => {
    es.forEach(e => { if (e.isIntersecting) { countUp(e.target); nio.unobserve(e.target); } });
  }, { threshold: 0.4 });
  nums.forEach(el => nio.observe(el));
}

/* 首屏的实时读数：从数据自身读，保证与背景渲染的是同一份 */
fetch('./kg-data/nebula.json').then(r => r.json()).then(D => {
  const box = document.getElementById('liveFacts');
  if (!box) return;
  const items = [
    [(D.nodes || []).length, '实体', '真实抽取'],
    [(D.edges || []).length, '关系', '评测全集 43867'],
    [(D.communities || []).length, '社区', 'Louvain'],
  ];
  box.innerHTML = items.map(([v, l, d]) =>
    `<span class="it"><span class="v" data-count="${v}">0</span>`
    + `<span class="l">${l} · ${d}</span></span>`).join('')
    + `<span class="it"><span class="l" style="margin-top:6px">`
    + `<span class="pulse"></span>实时渲染中</span></span>`;
  const fresh = [...box.querySelectorAll('[data-count]')];
  if (REDUCED) {
    fresh.forEach(el => el.textContent = Number(el.dataset.count).toLocaleString());
  } else {
    const fio = new IntersectionObserver(es => {
      es.forEach(e => { if (e.isIntersecting) { countUp(e.target); fio.unobserve(e.target); } });
    }, { threshold: 0.3 });
    fresh.forEach(el => fio.observe(el));
  }
}).catch(() => {});

if (REDUCED) {
  document.querySelectorAll('.ch').forEach(c => c.classList.add('seen'));
}
