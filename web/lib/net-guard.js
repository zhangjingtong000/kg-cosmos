/* 离线守卫：给「依赖 CDN 的页面」加一层解释，避免断网时只有一个白屏。
 *
 * 【为什么需要它】
 *   项目里有一条硬约束「离线演示铁律」。
 *   演示主线（导航页 + 社区星云 + 方法能力 + 分析面板 + PathQA + 标杆参考）
 *   已全部本地化、断网可用；但仍有 **5 个页面**依赖 esm.sh ——
 *   **4 个历史原型页 + 1 个正式页**：
 *     community-atlas（**正式页** · 社区图鉴）/ graph-2d / force-explorer /
 *     force-graph / official-3-text
 *   它们的三维/图引擎从 CDN 取，断网时**什么都不显示、也不报错**，
 *   用户只会看到一个黑屏，不知道是坏了还是需要联网。
 *
 *   ⚠️ **上面这段改过一次**（2026-09-19）：原文写「有 5 个**历史原型**仍然依赖
 *   esm.sh」，名单本身对、**分类错** —— `community-atlas` 是正式页
 *   （在 `tools/add_legacy_banners.py` 的 `OFFICIAL` 里），历史原型页只有 4 个。
 *   同一个数字/名单在 `README.md §8`、`web/index.html` 页脚与本文件里各有一份，
 *   现在由 `tools/check_doc_numbers.py` 的「依赖 esm.sh 的页面名单」一节统一核对
 *   —— **同一事实抄三遍，就必须三处都守**。
 *
 * 【为什么不做本地化】
 *   esm.sh 返回的是 0.2~0.4 KB 的**转发壳**（如 3d-force-graph 的壳引用
 *   accessor-fn / kapsule / three-forcegraph / three-render-objects，
 *   其中两个又各带一串），本地化等于递归抓整棵依赖树；
 *   而 UMD 路线实测走不通——`web/lib/3d-force-graph.min.js` 内置了自己的
 *   three，`three-spritetext.min.js` 却用全局 THREE，两份实例不兼容（全黑）。
 *   这 5 页里 4 页是"保留备查"的原型、不在演示主线上，`community-atlas` 是正式页；
 *   两者都如实提示，而不是硬做。
 *
 * 用法（放在页面最后，不要阻塞原有脚本）：
 *   <script src="./lib/net-guard.js"></script>
 */
(function () {
  'use strict';

  var PROBE = 'https://esm.sh/';          // 这些页面的共同外部依赖
  var CHECK_DELAY = 4200;                 // 给引擎留出加载时间再判定
  var shown = false;

  function banner(text) {
    if (shown) return;
    shown = true;
    var el = document.createElement('div');
    el.id = 'net-guard';
    el.setAttribute('role', 'status');
    el.style.cssText = [
      'position:fixed', 'left:50%', 'top:50%', 'transform:translate(-50%,-50%)',
      'z-index:2147483647', 'max-width:min(560px,86vw)',
      'background:rgba(10,16,34,.96)', 'color:#dfe8ff',
      'border:1px solid rgba(255,178,127,.5)', 'border-radius:14px',
      'padding:22px 26px', 'line-height:1.85', 'font-size:13px',
      'font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif',
      'box-shadow:0 20px 60px rgba(0,0,0,.7)',
    ].join(';');
    el.innerHTML = text;
    document.body.appendChild(el);
  }

  function msg(reason) {
    return '<b style="display:block;font-size:15px;margin-bottom:8px;color:#ffb27f">'
      + '本页需要联网</b>'
      + '<div style="color:rgba(196,214,240,.8)">' + reason + '</div>'
      + '<div style="margin-top:12px;padding-top:11px;font-size:11.5px;'
      + 'color:rgba(160,190,235,.6);border-top:1px solid rgba(140,170,255,.14)">'
      + '本页引用的图引擎来自 <code>esm.sh</code>（CDN），未做本地化，'
      + '断网时它加载不出来。'
      + '演示主线（导航页、社区星云、方法能力与局限、图谱分析面板、问题子图、'
      + '可视化标杆参考）<b>均可断网运行</b>。<br>'
      + '<a href="./index.html" style="color:#9fc4ff">← 返回导航页</a></div>';
  }

  // ① 主动探测 CDN：拿不到就直接提示（比等超时快）
  var probe = fetch(PROBE, { method: 'HEAD', mode: 'no-cors' });
  if (probe && probe.catch) {
    probe.then(function () { /* 通了，交给 ② 兜底 */ })
      .catch(function () {
        banner(msg('当前无法访问 <code>' + PROBE
          + '</code>，页面里依赖它的图引擎加载失败。'));
      });
  }

  // ② 兜底：CDN 探测可能因 CORS 误判，所以再看一眼实际结果——
  //    到了时间还没有任何 canvas，就说明引擎确实没起来。
  setTimeout(function () {
    if (shown) return;
    var host = document.querySelector('#3d-graph, #graph, #app, #canvas-wrap');
    var hasCanvas = document.querySelector('canvas');
    if (!hasCanvas || (host && !host.querySelector('canvas'))) {
      banner(msg('图引擎没有加载出来（页面上没有生成画布）。'));
    }
  }, CHECK_DELAY);
})();
