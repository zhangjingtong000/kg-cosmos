/* ============================================================================
   web/lib/guide.js —— 全站「引导条」（2026-09-20）

   ## 它要解决什么（用户原话）
   > 「如何让用户快速理解当前项目是干嘛的，以及每个功能分别是干什么的、
   >   有什么作用、展示的内容是什么，这些都要在引导中告诉用户，
   >   **而不是生硬地以文本形式贴在页面里，让用户自己去读**……
   >   整个流程需要有一个引导的作用，**特别是后面那六个独立的**。」

   拆成两件具体的事：
     ① **每页一条结构化引导**：三行带标签的短句 ——
        「这页回答什么 / 先做什么 / 看到什么算正常」。不是一整段散文。
     ② **把 12 页接成一条可走的动线**：每页底部给「上一站 / 下一站」。
        原先只有 `demo-rail.js` 的 6 步动线，**后六个支线页完全没有流程引导**
        —— 这正是用户说的"独立"。

   ## 为什么做成"文档流里的一条"，而不是浮层/弹窗导览
   浮层导览（spotlight / 弹窗）在这个项目里风险很高，逐条都是踩过的坑：
     · 会遮挡其它 `position:fixed` 面板（`#detail` / `#legend` / `#kgExport` /
       `#a11y-home` / `#demoRail`）—— 本项目为这类遮挡吃过亏（C4 分享按钮点不到）；
     · 会挡住自动化门禁点击（`cdp_verify_nav.py` 点进度点、`cdp_verify_a11y.py` 走键盘）；
     · 关掉之后什么也不剩，用户想重看找不到。
   ⇒ 所以引导条**插在正文容器的第一个子元素之前**，参与正常布局：
     不遮挡任何东西、不影响任何门禁、也不改变既有元素的位置关系。

   ## 失败时的表现（比功能本身重要）
   · 注入**全部由 JS 完成** ⇒ 脚本没加载/报错时，页面与没有这个文件时**完全一样**；
   · `localStorage` 不可用（file:// 或隐私模式）⇒ try/catch 包住，退化成"每次都展开"；
   · 找不到正文容器 ⇒ 直接不注入，不猜；
   · `prefers-reduced-motion` ⇒ 不做展开动画。
   ============================================================================ */
(function () {
  'use strict';

  var doc = document;
  var HERE = (location.pathname.split('/').pop() || 'index.html').toLowerCase();

  /* ── 12 页的引导内容 ──────────────────────────────────────────────────
     三行固定结构（用户一屏扫完，不用读散文）：
       ask  = 这一页回答什么问题
       todo = 先做什么（一个动作）
       read = 看到什么算正常 / 怎么读结论
     另外 `flow` 指出它在哪条线上：'main' = 六步演示动线，'side' = 另有 6 项功能。
     ⚠️ 文案必须与页面真实能力一致：不写页面上没有的操作。 */
  var PAGES = {
    'upload.html': {
      no: '01', name: '上传一篇论文', flow: 'main',
      ask: '一篇新论文能不能自己长进图谱里？',
      todo: '把 PDF 拖进虚线框（也可以点它选文件）',
      read: '四个阶段依次点亮即正常；走完能看到「进图谱」的按钮'
    },
    'kg-nebula.html': {
      no: '02', name: '社区星云 · 主视图', flow: 'main',
      ask: '这张图整体长什么样？哪些社区最密？',
      todo: '先转一下（拖拽/滚轮），再点任意一个节点',
      read: '点节点后右侧出现它的关系；右下角能切换着色维度'
    },
    'ask.html': {
      no: '03', name: '提问 · 支持与反方', flow: 'main',
      ask: '这个问题在图谱里有什么证据？两方分歧在哪？',
      todo: '在输入框问一句，或直接点上面的示例问题',
      read: '每段结论下面都挂着可回溯到论文的证据；没找到会如实说"没找到"'
    },
    'analytics.html': {
      no: '04', name: '图谱分析面板', flow: 'main',
      ask: '六层评测各自的数字，能不能追回到产物文件？',
      todo: '从上往下滚一遍；每块标题下那行灰字写的就是它的口径',
      read: '每个数字都能点出它在仓库里的来源文件路径'
    },
    'pathqa.html': {
      no: '05', name: '问题子图 PathQA', flow: 'main',
      ask: '一个孤立实体是怎么被连进主图谱的？',
      todo: '选一个实体（或直接点示例），看它到核心社区的路径',
      read: '画出来的连线数可能少于显示的数字 —— 那是截断，页脚写了这件事'
    },
    'method-limits.html': {
      no: '06', name: '方法能力与局限', flow: 'main',
      ask: '这张图能做什么？不能做什么？',
      todo: '用顶部检索框查一个方法名，或点维度标签筛',
      read: '每条都标了"能力"与"局限"两面；空结果会说明为什么空'
    },
    'papers.html': {
      no: '07', name: '论文管理 · 语料清单', flow: 'side',
      ask: '语料里到底有哪些论文？各自在什么状态？',
      todo: '看「论文清单」那张表；点表头可以按状态/语言/年份分组',
      read: '四层身份（清单/已下载/已入库/已归档）是分开数的，「主要关系」列没数据的写 —'
    },
    'bipartite.html': {
      no: '08', name: '使用关系二部图', flow: 'side',
      ask: '谁在用什么东西？',
      todo: '点任意一个节点，只看它相关的连线（再点一次取消）',
      read: '数字是<b>全库真实度数</b>，画出来的连线按 top N 截断——两者不等是正常的'
    },
    'temporal.html': {
      no: '09', name: '时间演化', flow: 'side',
      ask: '什么时候首次出现了什么？',
      todo: '拖年份滑块，或点「播放」让它自己走一遍',
      read: '累积值一直在涨是必然的；<b>每年新增</b>那几个数才有信息量'
    },
    'diff.html': {
      no: '10', name: '多图对比', flow: 'side',
      ask: '两篇论文的知识有多少重叠？',
      todo: '左右各选一篇（或直接点下面「值得看的对」）',
      read: '三栏分别是"只在左/共有/只在右"；JACCARD 是归一化后的重叠度'
    },
    'paper-table.html': {
      no: '11', name: '论文对比表', flow: 'side',
      ask: '同一批字段横向摊开是什么样？',
      todo: '用上面的列开关选你要看的字段（默认只开前 4 列）',
      read: '屏上的表与导出的 CSV / Markdown 是同一份数据'
    },
    'stance.html': {
      no: '12', name: '立场聚合', flow: 'side',
      ask: '某个主题上，文献站在哪一边？',
      todo: '看上面四个分布块，再点某一行右边的「看是谁支持 / 谁反对」',
      read: '立场是<b>数出来的</b>，不是问模型问出来的；判据写在这一页上，可复算'
    }
  };

  /* ── 两种形态（按页面类型分）───────────────────────────────────────────
     · `bar`（默认）：正文流里的引导条，首次访问自动展开。
       适用：有正常滚动容器的页面（`main` / `.wrap` / `.inner`）。
     · `button`：只在页面标题下放**一行**「? 这一页是干什么的」，点击才展开。
       适用：满屏应用型页面（`overflow:hidden`、没有滚动容器）——
       `kg-nebula` / `pathqa` / `bipartite` 三页硬塞一条流内引导会挤压画布，
       而这三页的头部本身是 fixed，展开后长高会盖住内容。
       ⇒ 默认**只有一行、不展开**，用户点之前不占空间、不遮任何东西（门禁也不受影响）。
     `host` 指定插入到哪个容器里；找不到就**整页不注入**（不猜）。 */
  var HOST = {
    'upload.html': { sel: 'main' },
    'ask.html': { sel: 'main' },
    'analytics.html': { sel: '.wrap' },
    'method-limits.html': { sel: '.wrap' },   /* 不能用 main#grid：它的内容由 JS 全量重建 */
    'papers.html': { sel: 'main' },
    'temporal.html': { sel: 'main' },
    'diff.html': { sel: 'main' },
    'paper-table.html': { sel: 'main' },
    'stance.html': { sel: 'main' },
    'kg-nebula.html': { sel: '.header', mode: 'button' },
    'pathqa.html': { sel: 'header', mode: 'button' },
    'bipartite.html': { sel: 'header', mode: 'button' }
  };

  var KEY = 'kg-demo-guide-closed';          // 记住"用户已经收起过引导"
  var cfg = PAGES[HERE];
  if (!cfg) return;                          // 未登记的页面：什么都不做
  var place = HOST[HERE] || { sel: 'main' };
  var mode = place.mode || 'bar';
  var host = doc.querySelector(place.sel);
  if (!host) return;

  var reduced = false;
  try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  function closed() {
    try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; }
  }
  function remember() {
    try { localStorage.setItem(KEY, '1'); } catch (e) {}
  }

  /* ── 上一站 / 下一站：把 12 页接成一条可走的动线 ──────────────────────
     六步动线内部按 01→06；支线页则指回导航页（那里才是 12 个入口的家）。 */
  var MAIN = ['upload.html', 'kg-nebula.html', 'ask.html',
              'analytics.html', 'pathqa.html', 'method-limits.html'];
  function flowNav() {
    if (cfg.flow !== 'main') {
      return '<a href="./index.html">回导航页 · 12 个入口</a>';
    }
    var i = MAIN.indexOf(HERE);
    var prev = i > 0 ? PAGES[MAIN[i - 1]] : null;
    var next = i >= 0 && i < MAIN.length - 1 ? PAGES[MAIN[i + 1]] : null;
    var out = [];
    if (prev) out.push('<a href="./' + MAIN[i - 1] + '">← ' + prev.no + ' ' + prev.name + '</a>');
    if (next) out.push('<a href="./' + MAIN[i + 1] + '">' + next.no + ' ' + next.name + ' →</a>');
    if (!next) out.push('<a href="./index.html">动线结束 · 回导航</a>');
    return out.join('<span class="kg-guide-sep">·</span>');
  }

  function build() {
    var box = doc.createElement('section');
    box.id = 'kgGuide';
    box.setAttribute('aria-label', '这一页是干什么的');
    box.innerHTML =
      '<div class="kg-guide-head">' +
        '<span class="kg-guide-no">' + cfg.no + '</span>' +
        '<strong>' + cfg.name + '</strong>' +
        '<span class="kg-guide-kind">' +
          (cfg.flow === 'main' ? '六步演示动线' : '支线功能 · 不在演示动线上') +
        '</span>' +
        '<button type="button" class="kg-guide-x" aria-label="收起引导">收起</button>' +
      '</div>' +
      '<dl class="kg-guide-body">' +
        '<div><dt>这一页回答</dt><dd>' + cfg.ask + '</dd></div>' +
        '<div><dt>先做这个</dt><dd>' + cfg.todo + '</dd></div>' +
        '<div><dt>看到什么算正常</dt><dd>' + cfg.read + '</dd></div>' +
      '</dl>' +
      '<div class="kg-guide-foot">' + flowNav() + '</div>';
    return box;
  }

  /* 收起后留下的那一行："? 这一页是干什么的"——用户随时能再打开 */
  function reopenBar() {
    var b = doc.createElement('button');
    b.type = 'button';
    b.id = 'kgGuideOpen';
    b.innerHTML = '<span aria-hidden="true">?</span> 这一页是干什么的';
    b.addEventListener('click', function () {
      var box = build();
      box.classList.add('kg-guide-in');
      host.insertBefore(box, host.firstElementChild);
      if (!reduced) { box.style.opacity = '0'; box.style.transform = 'translateY(-6px)';
        requestAnimationFrame(function () { box.style.transition = 'opacity .25s, transform .25s';
          box.style.opacity = '1'; box.style.transform = 'none'; }); }
      b.remove();
      bindClose(box);
    });
    return b;
  }

  function bindClose(box) {
    var x = box.querySelector('.kg-guide-x');
    if (!x) return;
    x.addEventListener('click', function () {
      remember();
      box.remove();
      if (!doc.getElementById('kgGuideOpen')) host.insertBefore(reopenBar(), host.firstElementChild);
    });
  }

  if (closed() || mode === 'button') {
    /* `button` 形态**永远从一行开始**（不自动展开）：用户点之前不占空间、不遮东西 */
    host.insertBefore(reopenBar(), host.firstElementChild);
  } else {
    var box = build();
    host.insertBefore(box, host.firstElementChild);
    bindClose(box);
  }
})();
