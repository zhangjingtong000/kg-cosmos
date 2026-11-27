/* ═══════════════════════════════════════════════════════════════════════════
   演示动线条（demo-rail.js）
   ───────────────────────────────────────────────────────────────────────────

   **要解决什么**

   先前一次演示动线实测：29 个页面里，除导航页与 `new-graph.html`，
   **其余 25 个页面的出链都是 0**（那是该审计写作时的页数；2026-09-19 复核
   `web/*.html` 已 **37 个**，本段是**历史实测的引用**，不改写，
   但引用时不要当成当前页数）。也就是说演示时走完一步，只能点左下角那个
   不起眼的「← 导航页」回到列表，再从列表点进下一站 ——
   **一条 6 步的动线要多点 7 次，而且叙事被列表页打断。**
   （原写「**7 步**的动线要多点 **8** 次」—— 那是 `FLOW` 尚有 7 项时的读数；
    现 `FLOW` 只有 01–06 共 **6** 项，见下方 `FLOW` 数组。2026-09-19 更正。）

   这个条子把动线本身放进每个页面：当前站高亮、可直接跳到任意一站、
   右下角一个「下一站 →」顺着走。

   **为什么做成注入而不是逐页写**

   6 个正式页面各自嵌一段冗长的导航 HTML，以后改动就要改 6 处 ——
   （原写「**7** 个正式页面…要改 **7** 处」：`FLOW` 现为 **6** 项（01 上传 →
    02 星云 → 03 提问 → 04 分析 → 05 PathQA → 06 局限），故 2026-09-19 更正为 6。
    与 `web/index.html:283` 的「六步演示动线 · 另有 6 项功能」一致。）
   这个项目已经在「同一条规则写两遍」上栽过三次（见 `README §5`）。
   所以只在这里定义一次，各页加一行 `<script src="./lib/demo-rail.js"></script>`。

   **行为约定**

   · 只在动线内的页面显示；不在动线上的页面（原型、实验页）不注入。
   · 当前站高亮且不可点（点了也没意义）。
   · 右下角「×」可收起，收起状态写进 localStorage，**刷新后保持** ——
     正式浏览时不想看到它。
   · 条子固定在底部，同时给 `body` 加 padding-bottom，**不遮内容**。
   · fixed 定位 + `z-index: 60`（低于 a11y 的返回链接，不抢它的位置）。
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* 演示动线。顺序即叙事：先展示能力（上传→成图→全库），
     再展示验证（问答→分析），最后展示诚实（局限）。 */
  var FLOW = [
    { href: './upload.html',        no: '01', name: '上传一篇论文' },
    { href: './kg-nebula.html',     no: '02', name: '社区星云 · 主视图' },
    { href: './ask.html',           no: '03', name: '提问 · 支持与反方' },
    { href: './analytics.html',     no: '04', name: '图谱分析面板' },
    { href: './pathqa.html',        no: '05', name: '问题子图 PathQA' },
    { href: './method-limits.html', no: '06', name: '方法能力与局限' }
  ];

  var KEY = 'kg-demo-rail-hidden';
  function hidden() {
    try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; }
  }
  function setHidden(v) {
    try { localStorage.setItem(KEY, v ? '1' : '0'); } catch (e) {}
  }

  /* 当前页：用文件名匹配，忽略查询串与目录 */
  var here = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  var idx = -1;
  for (var i = 0; i < FLOW.length; i++) {
    if (FLOW[i].href.replace('./', '').toLowerCase() === here) { idx = i; break; }
  }
  if (idx < 0) return;          // 不在动线上，不注入

  function build() {
    var bar = document.createElement('nav');
    bar.id = 'demoRail';
    bar.setAttribute('aria-label', '演示动线');

    var css = document.createElement('style');
    css.textContent = [
      '#demoRail{position:fixed;left:0;right:0;bottom:0;z-index:60;',
      'display:flex;align-items:center;gap:6px;flex-wrap:wrap;',
      'padding:7px 14px;font:12.5px/1.4 var(--font-sans,system-ui,sans-serif);',
      'background:rgba(4,7,14,.90);border-top:1px solid var(--line,rgba(178,180,186,.13));',
      'backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);',
      // ★ 条子本身**不拦截指针**，只有条子上的控件拦截（2026-09-19 修）。
      //   它是 `position:fixed` 且**横跨全宽**，而页面底部常有同样是 fixed 的浮层
      //   （如 `kg-nebula.html` 的 `#kgExport`：`right:18px;bottom:18px;z-index:40`）
      //   —— 本条的 `z-index:60` 压在它们上面 ⇒ **人和自动化都点不到那些按钮**。
      //   实测：C4 分享链接的 `btn.click()` 因此超时 30 秒（Playwright 报
      //   `<nav id="demoRail"> intercepts pointer events`）。
      //   `pointer-events:none` 让点击**穿透条子的空白处**；条子上的链接与按钮
      //   再逐个打开 —— 这样"有没有压到别的东西"就只剩**视觉**问题，不再是**功能**问题。
      'pointer-events:none}',
      '#demoRail a,#demoRail button{pointer-events:auto}',
      '#demoRail .lb{color:var(--ink-3,rgba(193,195,201,.46));letter-spacing:.14em;',
      'font-size:10.5px;margin-right:6px;white-space:nowrap}',
      '#demoRail a{color:var(--ink-2,rgba(203,218,241,.76));text-decoration:none;',
      'padding:4px 9px;border-radius:7px;border:1px solid transparent;white-space:nowrap;',
      'transition:color .3s cubic-bezier(.19,1,.22,1),border-color .3s cubic-bezier(.19,1,.22,1)}',
      '#demoRail a:hover{color:#fff;border-color:rgba(224,164,105,.42)}',
      '#demoRail a:hover .n{color:var(--accent,#e0a469)}',
      '#demoRail .n{color:var(--ink-3,rgba(193,195,201,.46));margin-right:5px;',
      'font-variant-numeric:tabular-nums}',
      '#demoRail .cur{color:#fff;border-color:rgba(224,164,105,.55);',
      'background:rgba(224,164,105,.10);cursor:default}',
      '#demoRail .cur .n{color:var(--accent,#e0a469)}',
      '#demoRail .sp{flex:1 1 auto;min-width:8px}',
      '#demoRail .next{color:var(--warm,#ffcfa8)}',
      '#demoRail .x{background:none;border:0;color:var(--ink-3,rgba(193,195,201,.46));',
      'font:inherit;cursor:pointer;padding:4px 8px}',
      '#demoRail .x:hover{color:#fff}',
      '@media(max-width:900px){#demoRail .nm{display:none}}',
      '@media(prefers-reduced-motion:reduce){#demoRail *{transition:none!important}}'
    ].join('');
    document.head.appendChild(css);

    var html = '<span class="lb">演示动线</span>';
    for (var j = 0; j < FLOW.length; j++) {
      var f = FLOW[j];
      if (j === idx) {
        html += '<span class="cur"><span class="n">' + f.no + '</span>' +
                '<span class="nm">' + f.name + '</span></span>';
      } else {
        html += '<a href="' + f.href + '"><span class="n">' + f.no + '</span>' +
                '<span class="nm">' + f.name + '</span></a>';
      }
    }
    html += '<span class="sp"></span>';
    if (idx < FLOW.length - 1) {
      var nx = FLOW[idx + 1];
      html += '<a class="next" href="' + nx.href + '">下一站 ' + nx.no + ' ' +
              nx.name + ' →</a>';
    } else {
      html += '<a class="next" href="./index.html">演示结束 · 回导航 →</a>';
    }
    html += '<button class="x" type="button" aria-label="收起演示动线">×</button>';
    bar.innerHTML = html;

    /* 别遮住内容：给 body 补一条与条子等高的下边距 */
    function pad() {
      var h = bar.offsetHeight + 'px';
      // 流内元素：靠 body 的下边距让位。
      document.body.style.paddingBottom = h;
      // ★ 而 `position:fixed` 的底部浮层**不受 body 下边距影响**（2026-09-19 修）
      //   ⇒ 把条子高度也暴露成 CSS 变量，让这类浮层自己抬起来：
      //     `bottom:calc(18px + var(--demo-rail-h,0px))`
      //   实测没抬的那一个：`kg-nebula.html` 的 `#kgExport`（分享按钮在里面），
      //   它的 z-index 40 < 本条的 60 ⇒ 被压在下面、点不到。
      document.documentElement.style.setProperty('--demo-rail-h', h);
    }
    bar.querySelector('.x').addEventListener('click', function () {
      setHidden(true);
      bar.remove();
      document.body.style.paddingBottom = '';
      document.documentElement.style.removeProperty('--demo-rail-h');
    });
    document.body.appendChild(bar);
    pad();
    addEventListener('resize', pad);

    /* 键盘：Alt+← / Alt+→ 在动线里前后走 —— 演示时不用找鼠标 */
    addEventListener('keydown', function (e) {
      if (!e.altKey) return;
      if (e.key === 'ArrowRight' && idx < FLOW.length - 1) location.href = FLOW[idx + 1].href;
      if (e.key === 'ArrowLeft' && idx > 0) location.href = FLOW[idx - 1].href;
    });
  }

  if (hidden()) return;         // 用户收起过，就不再显示（刷新后保持）
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
