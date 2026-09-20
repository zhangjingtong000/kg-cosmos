/* live-numbers.js —— 让统计数字「活」起来：滚进视野时从 0 滚到真值。
 * ============================================================================
 * 【为什么不是简单的 el.textContent = 中间值】
 *
 * 这个项目里的数字有两条读取路径，它们互相冲突：
 *
 *   ① 属性为真（`data-count`）：`web/lib/voyage.js` 的首页大数字。
 *      HTML 里写 `data-count="20785"`、屏上初始是 `0`，由 JS 滚上去。
 *      读它的是 `tools/check_doc_numbers.py` —— 它读**属性**，不读屏上文本，
 *      所以动画改文本是安全的。
 *
 *   ② 文本为真：`web/papers.html` 的六张统计卡、`web/analytics.html` 的读数。
 *      值由 JS 直接写进 DOM 文本，读它的是 `tools/cdp_verify_papers.py`
 *      （逐字比对语料数字）与 `tools/cdp_verify_analytics.py`。
 *      对这些元素做「原地滚数」，等于让闸门在某次读取时拿到一个中间值 ——
 *      结局是假红（数字读到一半）或者更糟：假绿（动画恰好滚到了对的值）。
 *
 * 所以这一版的办法是：**原元素一个字符都不改**。
 *
 *   · 原元素始终持有权威值 ⇒ 闸门无论在第几毫秒读 textContent，读到的都是真值；
 *     `aria-hidden` 的克隆不参与无障碍树，屏读器也听不到中间值；
 *   · 滚动动画画在一个绝对定位的**克隆层**上，位置与原元素逐像素重合
 *     ⇒ 布局零位移；动画期间把原元素 `color` 置透明避免叠影，结束还原；
 *   · 于是「视觉上在滚」与「DOM 里从来没错过」同时成立。
 *
 * 代价是多一个克隆节点和一次 getBoundingClientRect，换来的是
 * **不可能被动画拖坏的闸门**。这笔账在这个项目里是划算的。
 *
 * 【怎么用】给纯数字（或「数字 + 非数字后缀」）的元素加 `data-live`：
 *
 *   <div class="v" data-live>20785</div>
 *   <b data-live>40.0<i>%</i></b>          ← 后缀里有 <i> 也行，后缀里不能有数字
 *
 * 不用 `data-count` 是因为两者的契约正相反（那边属性为真、这边文本为真），
 * 共用一个属性名会让「谁是权威值」变得含糊。
 *
 * 【不做的事】
 *   · 不滚区间（`42.9–57.1%` 这种）：后缀里还有数字，滚一半会读成假话 ⇒ 整条跳过；
 *   · 不滚带单位的复合值（`12.3%` 可以，`3/8` 不行）：解析不出来就跳过，不猜；
 *   · 不滚 `—` / `N/A` / 空值 ⇒ 跳过；
 *   · `prefers-reduced-motion: reduce`（且没带 `?motion=force`）⇒ 什么都不做，
 *     原元素本来就是真值，所以「降级」在这里等于「本来就对」。
 *
 * 无依赖（不用 GSAP）：本项目只有 3 个页面引了 GSAP，而需要数字活化的是
 * papers / analytics 这些没引 GSAP 的页面。为一个 1400ms 的缓动去给它们各加
 * 两个 <script>，不如这 30 行 requestAnimationFrame。
 * ============================================================================
 */
(function () {
  'use strict';

  var DUR = 1400;                       /* 与 voyage.js 的首页数字保持一致 */

  function reduced() {
    try {
      if (/[?&]motion=force\b/.test(location.search)) return false;
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { return false; }
  }

  /* 把 "40.0%" / "1,234" / "20785" 拆成 {num, dec, head, tail}；拆不动就返回 null */
  function parse(txt) {
    var m = /^(\s*)(-?[\d][\d,]*(?:\.\d+)?)([\s\S]*)$/.exec(txt || '');
    if (!m) return null;
    var raw = m[2];
    if (/\d/.test(m[3])) return null;   /* 尾巴里还有数字 ⇒ 是区间/复合值，不滚 */
    var dot = raw.indexOf('.');
    return {
      head: m[1],
      to: parseFloat(raw.replace(/,/g, '')),
      dec: dot < 0 ? 0 : (raw.length - dot - 1),
      tail: m[3]
    };
  }

  function fmt(to, dec, k) {
    var v = to * k;
    return dec ? v.toFixed(dec) : Math.round(v).toLocaleString('en-US');
  }

  /* 在 el 上滚一次。原元素文本全程不动。 */
  function roll(el) {
    var host = el.parentNode;
    if (!host || !host.getBoundingClientRect) return;
    var fx = el.cloneNode(true);
    fx.setAttribute('aria-hidden', 'true');
    fx.classList.add('live-fx');
    fx.removeAttribute('data-live');    /* 克隆体不能再被扫到 */

    var hp = getComputedStyle(host).position;
    if (hp === 'static') host.style.position = 'relative';
    var hr = host.getBoundingClientRect(), er = el.getBoundingClientRect();

    /* 克隆体按原元素的盒模型逐像素盖上去 */
    fx.style.cssText = [
      'position:absolute',
      'left:' + (er.left - hr.left) + 'px',
      'top:' + (er.top - hr.top) + 'px',
      'width:' + er.width + 'px',
      'height:' + er.height + 'px',
      'margin:0',
      'pointer-events:none',
      'white-space:nowrap',
      'overflow:visible'
    ].join(';');

    /* 只改克隆体里**第一个含数字的文本节点**，其余节点（`<i>%</i>` 之类）原样保留。
       注意：必须按**该文本节点自己**的内容来拆后缀，不能拿整元素的 textContent 去拆 ——
       否则 `40.0<i>%</i>` 会把 `%` 再写回文本节点，屏上变成 `32.1%%`（这个 bug 是被探针
       量出来的，不是看出来的）。 */
    var tn = null;
    (function walk(n) {
      for (var i = 0; i < n.childNodes.length && !tn; i++) {
        var c = n.childNodes[i];
        if (c.nodeType === 3 && /\d/.test(c.nodeValue)) tn = c;
        else if (c.nodeType === 1) walk(c);
      }
    })(fx);
    if (!tn) return;                    /* 找不到可改的文本 ⇒ 干脆不滚 */
    var local = parse(tn.nodeValue);     /* 节点内局部解析：head + 数字 + 节点内后缀 */
    if (!local) return;

    var keepColor = el.style.color;
    host.appendChild(fx);
    el.style.color = 'transparent';     /* 原元素让位：只是不上色，仍在无障碍树里 */

    var t0 = null;
    function step(now) {
      if (t0 === null) t0 = now;
      var k = Math.min(1, (now - t0) / DUR);
      var e = 1 - Math.pow(1 - k, 3);   /* easeOutCubic，与首页一致 */
      tn.nodeValue = local.head + fmt(local.to, local.dec, e) + local.tail;
      if (k < 1) requestAnimationFrame(step);
      else {
        el.style.color = keepColor;
        if (fx.parentNode) fx.parentNode.removeChild(fx);
      }
    }
    tn.nodeValue = local.head + fmt(local.to, local.dec, 0) + local.tail;
    requestAnimationFrame(step);
  }

  /* 扫 root（含 root 自身）下所有还没滚过的 [data-live] */
  function scan(root) {
    var box = root || document;
    var list = [];
    if (box.nodeType === 1 && box.hasAttribute && box.hasAttribute('data-live')) list.push(box);
    var found = box.querySelectorAll ? box.querySelectorAll('[data-live]') : [];
    for (var i = 0; i < found.length; i++) list.push(found[i]);
    list.forEach(function (el) {
      if (el.dataset.liveDone === '1') return;
      if (!parse(el.textContent)) { el.dataset.liveDone = '1'; return; }  /* 解析不出来：标记后不再试 */
      el.dataset.liveDone = '1';
      if (reduced()) return;                             /* 降级＝保持真值，无需补救 */
      observe(el);
    });
  }

  var io = null;
  function observer() {
    if (io) return io;
    if (typeof IntersectionObserver !== 'function') return null;
    io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target);
        roll(e.target);
      });
    }, { threshold: 0.4 });              /* 与 voyage.js 一致：露出四成再滚 */
    return io;
  }

  function observe(el) {
    var o = observer();
    if (!o) { roll(el); return; }        /* 没有 IO：直接滚 */
    o.observe(el);
  }

  window.LiveNumbers = { scan: scan, parse: parse };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { scan(document); });
  } else {
    scan(document);
  }
})();
