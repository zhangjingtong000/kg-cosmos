/* ============================================================================
   web/lib/motion.js —— 「稳妥版」滚入视野淡入（只动 opacity）

   配合 web/lib/motion.css 使用。导航页 index.html 不引本文件（它有自己的一套）。

   ── 四条设计约束（来自 2026-09-20 对 12 个官方页的只读审计）─────────────
   1) 只改 opacity，绝不碰 transform / filter / will-change / contain。
      它们会新建 containing block：kg-nebula 有 17 个 position:fixed 浮层，
      paper-table 的 th 与 method-limits 的 .bar 是 position:sticky，
      祖先一旦成为 containing block / 层叠上下文，压盖关系就会变。
   2) 只挑「静态内容块」。明确排除：
      · body 的直接子元素（analytics / kg-nebula / method-limits / pathqa 四页
        有一条 `body > *:where(...){position:relative;z-index:1}` 兜底规则，
        再加 opacity 会新建层叠上下文，动到 fixed 浮层的叠放）；
      · position:fixed / sticky 元素本身；
      · [hidden] 及其后代（display:none 不产生交叉，解除后未必重新触发）；
      · svg / canvas / dialog / 脚本注入的 fixed 浮层（#demoRail #a11y-home
        #a11y-skip #tip #veil）；
      · a / button / input / select / textarea / summary / label
        （opacity:0 时 a11y.css 的焦点环也被一起透明，键盘用户会 Tab 到看不见的地方）；
      · 计算样式 opacity 已经不是 1 的元素
        （说明该页自己的 JS 正管着它的 opacity，例如 upload 的 .stage、bipartite 的 #tip）。
   3) **只对「此刻已经在视口下方」的元素加隐藏态**。
      视口内 / 视口上方的元素一律不碰 ⇒ 不可能出现"先显示、再被藏起来、再淡入"
      的闪烁。JS 没跑、报错、或浏览器不支持 IntersectionObserver 时，
      HTML 里本来就没有任何隐藏态，页面与加这个文件之前**完全一致**。
   4) 三重兜底（任何一条命中都会把内容揭回来，绝不允许"永久看不见"）：
      a. 初始化整体包在 try/catch 里，抛错即 restoreAll()；
      b. window 的 error 事件 → restoreAll()；
      c. 2.5s 定时器：IO 回调一次都没发生（说明 IO 根本没工作）→ 全部揭开；
         IO 在工作、但某个元素此刻明明该可见却还藏着 → 只揭开那一个。

   ── 为什么需要 MutationObserver ────────────────────────────────────────
   本项目的页面大多是"骨架先出、数据后填"（analytics 的 16 个 section、
   papers 的各容器、method-limits 的 #grid 由 JS 注入 section.group）。
   DOMContentLoaded 那一刻页面还很短，很多东西都还在视口内甚至不存在。
   所以除了首次扫描，还要在数据注入后按需重扫；12s 后自动断开，
   避免长期挂着一个观察器。
   ============================================================================ */

(function () {
  'use strict';

  var win = window;
  var doc = document;
  if (!win || !doc || !doc.body) return;

  /* ① 尊重「减少动效」偏好：直接不启动。
        a11y.css / design.css 的降级规则只盖得住 CSS transition / animation，
        盖不住 JS 改 opacity，所以这里必须自己判一次。
        ★ 例外：地址上显式写了 `?motion=force` 就照做 —— 用来二分
          "代码没生效" 与 "偏好按设计把动效关掉了"（两者在页面上看起来一样）。 */
  try {
    var FORCE = /[?&]motion=force\b/.test(win.location.search);
    if (!FORCE && win.matchMedia && win.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    /* 强制模式下给 <html> 挂个类，让 motion.css 的降级分支整体让路
       （否则 CSS 那条 opacity:1 !important 会把淡入直接抹平 —— JS 放了、CSS 没放）。 */
    if (FORCE) doc.documentElement.classList.add('mo-force');
  } catch (e) {
    return;
  }

  /* ② 没有 IntersectionObserver 就什么都不做（宁可不做，也不能藏了揭不开） */
  if (!('IntersectionObserver' in win)) return;

  var base = (win.location.pathname.split('/').pop() || 'index.html').toLowerCase();

  /* 逐页显式列目标。**没有列到的页面 = 完全不动**。
     所有选择器都只指向静态的块级容器；容器内部 subsequent 被 innerHTML
     重建不影响：我们只改容器自身的 opacity，不碰它里面的节点。 */
  var PAGES = {
    'ask.html': ['#modes', '#ctx', '#ask', '#chips', '#how', '#how-note'],
    'analytics.html': ['.wrap > section:not(#s-top):not([hidden])'],
    'method-limits.html': ['#grid > section.group'],
    'papers.html': [
      'main > h2',
      'main > p.hint',
      'main > p.note',
      'main > .warnbox',
      'main > #kpis',
      'main > #ltabs'
    ],
    'temporal.html': ['main > .panel', 'main > p'],
    'diff.html': ['main > .panel', 'main > p'],
    /* #out 内含 sticky 的 th，故意不列；warnbox / note 是纯静态块 */
    'paper-table.html': ['main > .warnbox', 'main > p.note'],
    /* #rows 里是 details/summary（可交互），但这里只动整个容器的 opacity，
       且动画结束回到 1，交互元素本身不受影响 */
    'stance.html': ['main > .dist', 'main > #rows', 'main > p.note']
  };

  var SELECTORS = PAGES[base];
  if (!SELECTORS) return;

  var HIDDEN = [];   /* 已加隐藏态、等待揭开的元素 */
  var cbCount = 0;   /* IntersectionObserver 回调次数：>0 说明它真的在工作 */
  var io = null;

  function reveal(el) {
    if (!el.classList.contains('mo') || el.classList.contains('mo-in')) return;
    el.classList.add('mo-in');
    /* 动画结束后把两个类都摘掉：元素回到自然样式（opacity:1），
       不给页面留下任何长期的层叠上下文 / transition 声明。 */
    win.setTimeout(function () {
      el.classList.remove('mo');
      el.classList.remove('mo-in');
    }, 700);
  }

  function revealAll() {
    for (var i = 0; i < HIDDEN.length; i++) reveal(HIDDEN[i]);
  }

  function restoreAll() {
    for (var i = 0; i < HIDDEN.length; i++) {
      HIDDEN[i].classList.remove('mo');
      HIDDEN[i].classList.remove('mo-in');
    }
  }

  /* 元素是否适合承接淡入。任何一条不满足就跳过（跳过 = 保持现在的外观）。 */
  function suitable(el) {
    if (el.hasAttribute('hidden')) return false;
    if (el.closest('[hidden]')) return false;
    if (el.closest('svg, canvas, dialog, #tip, #veil, #demoRail, #a11y-home, #a11y-skip')) return false;

    var tag = el.tagName;
    if (tag === 'A' || tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' ||
        tag === 'TEXTAREA' || tag === 'SUMMARY' || tag === 'LABEL' ||
        tag === 'CANVAS' || tag === 'SVG') {
      return false;
    }

    var cs = win.getComputedStyle(el);
    if (cs.position === 'fixed' || cs.position === 'sticky') return false;
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    /* 该页自己的 JS 已经在管这个元素的 opacity ⇒ 不插手 */
    if (cs.opacity !== '1') return false;

    return true;
  }

  function pass() {
    var vh = win.innerHeight || 800;
    for (var s = 0; s < SELECTORS.length; s++) {
      var found;
      try {
        found = doc.querySelectorAll(SELECTORS[s]);
      } catch (e) {
        continue;
      }
      for (var i = 0; i < found.length; i++) {
        var el = found[i];
        if (el.__mo) continue;                       /* 本文件已处理过 */
        var r = el.getBoundingClientRect();
        if (r.height < 8) continue;                  /* 空容器 / 还没渲染，下一轮再看 */
        if (r.top <= vh * 0.88) continue;            /* 视口内或上方 ⇒ 不动它 */
        if (!suitable(el)) { el.__mo = 1; continue; } /* 不合格就不再重复检查 */
        el.__mo = 1;
        el.classList.add('mo');
        HIDDEN.push(el);
        io.observe(el);
      }
    }
  }

  try {
    io = new win.IntersectionObserver(function (entries) {
      cbCount++;
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].isIntersecting) {
          reveal(entries[i].target);
          io.unobserve(entries[i].target);
        }
      }
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0 });

    pass();
  } catch (e) {
    try { restoreAll(); } catch (e2) { /* 兜底本身失败也不抛 */ }
    return;
  }

  /* 兜底 b：任何未捕获错误发生，立刻把已藏起来的内容全部揭开 */
  win.addEventListener('error', function () {
    try { restoreAll(); } catch (e) { /* 同上 */ }
  });

  /* 兜底 c：IO 一次回调都没有 ⇒ 它实际没工作，2.5s 后全量揭开；
     否则只揭开"此刻本应可见却还藏着"的个别元素（长页面下方的块不动）。 */
  win.setTimeout(function () {
    try {
      if (cbCount === 0) { revealAll(); return; }
      var vh = win.innerHeight || 800;
      for (var i = 0; i < HIDDEN.length; i++) {
        var el = HIDDEN[i];
        if (el.classList.contains('mo-in')) continue;
        var r = el.getBoundingClientRect();
        if (r.top < vh && r.bottom > 0) reveal(el);
      }
    } catch (e) { /* 兜底失败不影响页面 */ }
  }, 2500);

  /* 数据注入后重扫；12s 后断开，不留长期观察器。 */
  try {
    var timer = 0;
    var mo = new win.MutationObserver(function () {
      if (timer) return;
      timer = win.setTimeout(function () {
        timer = 0;
        try { pass(); } catch (e) { /* 单次扫描失败不影响页面 */ }
      }, 200);
    });
    mo.observe(doc.body, { childList: true, subtree: true });
    win.setTimeout(function () { mo.disconnect(); }, 12000);
  } catch (e) {
    /* 没有 MutationObserver：只靠首扫 + 兜底，不影响正确性 */
  }
})();
