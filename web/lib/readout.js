/* readout.js —— 把「贴在页面上的表」变成「看得见的形状」
 * ============================================================================
 * 【它解决的问题】
 *
 * analytics 页上有一批**结论写对了、但形状看不出来**的地方。最典型的两处：
 *
 *   ① 阈值敏感性：一张 5 行的表 + 一句话
 *      「实测阈值 0.5→0.8 时通过率降 27.6pp 而 PPV 只升 8.8pp
 *        ⇒ 它是数量旋钮，不是质量旋钮」。
 *      话是对的，但读表读不出来 —— 因为**两条曲线的斜率差**才是论据，
 *      而表把斜率藏起来了。画出来，这句话就变成一眼可见的东西。
 *
 *   ② 关系召回：42.9%–57.1% 是一个**区间**（判据宽严不同），
 *      现在排成 4 行表。"它是个区间而不是一个数"这件事，表说不出来。
 *
 *   ③ 40.0% 与 49.7% 都带 95% 置信区间（`ci.true_support_rate` = [0.3407, 0.4597]），
 *      但屏上只有一个点估计。**只报点估计会让读者以为它是个精确值。**
 *
 * 【三条和闸门有关的纪律】（踩过才知道）
 *
 *   · **只加，不替换**。`cdp_verify_analytics.py` 会读那张阈值表和四个档位表，
 *     所以表格**原样保留**，图表画在它旁边。撤掉表会误伤闸门。
 *   · **SVG 里的文字不参与 DOM 文本检查**，但图本身要能给屏读器交代 ⇒
 *     每张图都带 `role="img"` + `aria-label`，把数据讲成人话。
 *   · **不做"插值出来的假曲线"**。阈值只有 5 个实测点，滑块就只在这 5 档之间走，
 *     并在界面上写明原因；不画样条把中间"补"出来 —— 那是编数据。
 *
 * 【依赖】零。自己算坐标、自己拼 SVG 字符串，不用 GSAP、不用任何图表库。
 *   宽度用 `clientWidth` 实测后重写 viewBox（这是本项目 temporal.html 里
 *   已经验证过的做法），配 ResizeObserver 重画。
 * ============================================================================
 */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var registry = [];   /* 重画登记表：窗口尺寸变了全部重画 */

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function pct(x, dec) { return (x * 100).toFixed(dec == null ? 1 : dec) + '%'; }
  function num(x) { return Number(x).toLocaleString('en-US'); }

  /* 建一个 SVG 并登记重画。fn(W, H) 返回 SVG 内部字符串。 */
  function make(host, H, label, fn) {
    if (!host) return null;
    var svg = host.querySelector('svg.ro-svg');
    if (!svg) {
      svg = document.createElementNS(NS, 'svg');
      svg.setAttribute('class', 'ro-svg');
      svg.setAttribute('role', 'img');
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      host.appendChild(svg);   /* 追加而不是插到最前：调用方总是先把说明文字放进 host，
                                  这样顺序恒为「说明 → 图 → 滑块」 */
    }
    svg.setAttribute('aria-label', label);
    svg.style.height = H + 'px';
    function draw() {
      /* 用实测像素宽做坐标系：这样文字不会被 viewBox 拉伸变形 */
      var W = Math.max(280, Math.round(svg.clientWidth) || host.clientWidth || 640);
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
      svg.innerHTML = fn(W, H);
    }
    draw();
    registry.push(draw);
    return svg;
  }

  if (typeof ResizeObserver === 'function') {
    var ro = new ResizeObserver(function () { registry.forEach(function (f) { f(); }); });
    window.addEventListener('load', function () {
      document.querySelectorAll('.ro-host').forEach(function (h) { ro.observe(h); });
    });
  } else {
    window.addEventListener('resize', function () { registry.forEach(function (f) { f(); }); });
  }

  /* 折线：把点数组变成 "M x y L x y …" */
  function line(pts) {
    return pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); })
      .join(' ');
  }

  /* ── ① 点估计 + 95% 置信区间 ─────────────────────────────────────────────
     一个孤零零的 "40.0%" 会让人以为它是精确的；把区间画出来，
     "这个数还有 ±6pp 的余地" 就变成了看得见的东西。 */
  function ci(host, o) {
    var lo = o.lo, hi = o.hi, pt = o.point;
    var l = Math.min(lo, pt, hi), h = Math.max(lo, pt, hi);
    var label = (o.label || '读数') + ' ' + pct(pt)
      + '，95% 置信区间 ' + pct(l) + ' 到 ' + pct(h);
    return make(host, 62, label, function (W) {
      var padL = 6, padR = 6, y = 26;
      var x0 = l - (h - l) * 0.18, x1 = h + (h - l) * 0.18;   /* 两端留白，别贴边 */
      var X = function (v) { return padL + (v - x0) / (x1 - x0) * (W - padL - padR); };
      var out = '';
      /* 轴：一条极淡的底线，给"区间"一个落脚的地方 */
      out += '<line x1="' + X(x0) + '" y1="' + y + '" x2="' + X(x1) + '" y2="' + y
        + '" stroke="var(--line)" stroke-width="1"/>';
      /* 区间须线 */
      out += '<line x1="' + X(l) + '" y1="' + y + '" x2="' + X(h) + '" y2="' + y
        + '" stroke="var(--cyan)" stroke-width="6" stroke-linecap="round" opacity=".55"/>';
      [l, h].forEach(function (v) {
        out += '<line x1="' + X(v) + '" y1="' + (y - 7) + '" x2="' + X(v) + '" y2="' + (y + 7)
          + '" stroke="var(--cyan)" stroke-width="1.5" opacity=".8"/>';
      });
      /* 点估计 */
      out += '<circle cx="' + X(pt) + '" cy="' + y + '" r="4.5" fill="var(--accent)"/>';
      /* 数字：只标点估计与两端，不做刻度尺（这里要的是"范围感"，不是查表） */
      out += '<text x="' + X(l) + '" y="' + (y + 22) + '" text-anchor="middle" class="ro-t ro-t3">'
        + pct(l) + '</text>';
      out += '<text x="' + X(h) + '" y="' + (y + 22) + '" text-anchor="middle" class="ro-t ro-t3">'
        + pct(h) + '</text>';
      out += '<text x="' + X(pt) + '" y="' + (y - 12) + '" text-anchor="middle" class="ro-t ro-t1">'
        + pct(pt) + (o.unit ? esc(o.unit) : '') + '</text>';
      return out;
    });
  }

  /* ── ② 阈值权衡：两条斜率不同的曲线 + 只能落在实测档位上的滑块 ──────────────
     "通过率随阈值下降是恒等式，PPV 才是质量" —— 这句话要靠**两条线张开的角度**
     来说，而不是靠表里两列数字。 */
  function tradeoff(host, o) {
    var rows = o.rows || [];
    if (!rows.length) return null;
    var TH = rows.map(function (r) { return +r.threshold; });
    var cur = o.current != null ? o.current : TH[Math.floor(TH.length / 2)];
    var mks = o.marks || [];   /* 额外的参考竖线，例如"模型卡写的那个阈值" */
    var curI = 0;
    var best = 1e9;
    TH.forEach(function (t, i) { var d = Math.abs(t - cur); if (d < best) { best = d; curI = i; } });

    function rate(r) { var v = r.pass_rate; return v > 1 ? v / 100 : v; }

    var label = '阈值敏感性：阈值从 ' + TH[0] + ' 到 ' + TH[TH.length - 1] + '，'
      + '通过率从 ' + pct(rate(rows[0])) + ' 降到 ' + pct(rate(rows[rows.length - 1])) + '，'
      + '而 PPV 只从 ' + pct(rows[0].ppv) + ' 变到 ' + pct(rows[rows.length - 1].ppv)
      + '。结论：阈值是数量旋钮，不是质量旋钮。';

    var svg = make(host, 230, label, function (W) {
      var padL = 46, padR = 96, padT = 16, padB = 44;
      var iw = W - padL - padR, ih = 230 - padT - padB;
      var x0 = TH[0], x1 = TH[TH.length - 1];
      var X = function (t) { return padL + (t - x0) / (x1 - x0) * iw; };
      var Y = function (v) { return padT + (1 - v) / 1 * ih; };
      var out = '';
      /* 横向网格 + y 轴刻度：0/25/50/75/100% */
      [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {
        out += '<line x1="' + padL + '" y1="' + Y(v) + '" x2="' + (padL + iw) + '" y2="' + Y(v)
          + '" stroke="var(--line)" stroke-width="1"'
          + (v === 0 ? '' : ' opacity=".55"') + '/>';
        out += '<text x="' + (padL - 8) + '" y="' + (Y(v) + 4) + '" text-anchor="end"'
          + ' class="ro-t ro-t3">' + (v * 100) + '%</text>';
      });
      /* 现行阈值竖线：先画，让曲线压在它上面 */
      out += '<line x1="' + X(cur) + '" y1="' + padT + '" x2="' + X(cur) + '" y2="' + (padT + ih)
        + '" stroke="var(--accent)" stroke-width="1" stroke-dasharray="3 3" opacity=".7"/>';
      /* 两条曲线 */
      var pass = rows.map(function (r) { return [X(+r.threshold), Y(rate(r))]; });
      var ppv = rows.map(function (r) { return [X(+r.threshold), Y(+r.ppv)]; });
      out += '<path d="' + line(pass) + '" fill="none" stroke="var(--clay)" stroke-width="2"/>';
      out += '<path d="' + line(ppv) + '" fill="none" stroke="var(--cyan)" stroke-width="2"/>';
      /* 点是"实测到的"，所以画成实心点；不画点会被误读成连续函数 */
      function dots(pts, col) {
        return pts.map(function (p) {
          return '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="3" fill="' + col + '"/>';
        }).join('');
      }
      out += dots(pass, 'var(--clay)') + dots(ppv, 'var(--cyan)');
      /* 直接标注（不用图例）：右端把两条线各自是什么写在旁边 */
      var last = rows[rows.length - 1];
      out += '<text x="' + (X(x1) + 10) + '" y="' + (Y(rate(last)) + 4) + '" class="ro-t ro-t2">'
        + '通过率 ' + pct(rate(last)) + '</text>';
      out += '<text x="' + (X(x1) + 10) + '" y="' + (Y(+last.ppv) + 4) + '" class="ro-t ro-t2">'
        + 'PPV ' + pct(+last.ppv) + '</text>';
      /* x 轴刻度 */
      rows.forEach(function (r) {
        out += '<text x="' + X(+r.threshold) + '" y="' + (padT + ih + 18) + '" text-anchor="middle"'
          + ' class="ro-t ro-t3">' + (+r.threshold).toFixed(2) + '</text>';
      });
      out += '<text x="' + (padL + iw / 2) + '" y="' + (padT + ih + 36) + '" text-anchor="middle"'
        + ' class="ro-t ro-t3">判别阈值（只有这 5 档是实测的）</text>';
      /* 现行阈值标签 */
      out += '<text x="' + X(cur) + '" y="' + (padT - 4) + '" text-anchor="middle"'
        + ' class="ro-t ro-t2">现行 ' + cur.toFixed(2) + '</text>';
      /* 额外汇总参考线：画得比"现行"更弱，只作对照，不抢主角 */
      mks.forEach(function (mk) {
        if (+mk.t < x0 || +mk.t > x1) return;
        var mx = X(+mk.t);
        out += '<line x1="' + mx + '" y1="' + (padT + 6) + '" x2="' + mx + '" y2="' + (padT + ih)
          + '" stroke="var(--ink-3)" stroke-width="1" stroke-dasharray="2 4"/>';
        out += '<text x="' + mx + '" y="' + (padT + 18) + '" text-anchor="middle"'
          + ' class="ro-t ro-t3">' + esc(mk.label) + '</text>';
      });
      return out;
    });
    if (!svg) return null;

    /* ── 滑块：只在实测档位之间走（不插值，也不假装有中间值）── */
    var wrap = document.createElement('div');
    wrap.className = 'ro-slide';
    var out2 = document.createElement('div');
    out2.className = 'ro-slide-out';
    var inp = document.createElement('input');
    inp.type = 'range';
    inp.min = '0'; inp.max = String(rows.length - 1); inp.step = '1'; inp.value = String(curI);
    inp.className = 'ro-range';

    function refresh(i) {
      var r = rows[i];
      inp.setAttribute('aria-valuetext',
        '阈值 ' + (+r.threshold).toFixed(2) + '，全图通过 ' + num(r.population_pass)
        + ' 条，通过率 ' + pct(rate(r)) + '，PPV ' + pct(+r.ppv));
      out2.innerHTML =
        '<span class="ro-k">阈值 <b>' + (+r.threshold).toFixed(2) + '</b></span>'
        + '<span class="ro-k">全图通过 <b>' + num(r.population_pass) + '</b> 条</span>'
        + '<span class="ro-k">通过率 <b class="c-clay">' + pct(rate(r)) + '</b></span>'
        + '<span class="ro-k">PPV <b class="c-cyan">' + pct(+r.ppv) + '</b></span>'
        + '<span class="ro-hint">拖动看两条线怎么走：通过率掉得快，PPV 几乎不动</span>';
    }
    inp.setAttribute('aria-label', '判别阈值：拖动查看该阈值下的通过率与 PPV');
    inp.addEventListener('input', function () { refresh(+inp.value); });
    wrap.appendChild(inp);
    wrap.appendChild(out2);
    host.appendChild(wrap);
    refresh(curI);
    return svg;
  }

  /* ── ③ 区间条：把"它是个区间，不是一个数"直接画出来 ──────────────────────── */
  function range(host, o) {
    var lo = o.lo, hi = o.hi, ticks = o.ticks || [];
    var label = (o.label || '读数') + ' 是一个区间：' + pct(lo) + ' 到 ' + pct(hi)
      + (ticks.length ? '，由 ' + ticks.length + ' 种判据档位测得：'
        + ticks.map(function (t) { return t.name + ' ' + pct(t.v); }).join('；') : '');
    return make(host, 74, label, function (W) {
      var padL = 10, padR = 10, y = 24;
      var pad = (hi - lo) * 0.10;
      var x0 = lo - pad, x1 = hi + pad;
      var X = function (v) { return padL + (v - x0) / (x1 - x0) * (W - padL - padR); };
      var out = '';
      out += '<line x1="' + X(lo) + '" y1="' + y + '" x2="' + X(hi) + '" y2="' + y
        + '" stroke="var(--cyan)" stroke-width="7" stroke-linecap="round" opacity=".45"/>';
      /* 档位刻度：每一个都标出来，让"宽严不同"可见 */
      ticks.forEach(function (t) {
        var cx = X(t.v);
        out += '<line x1="' + cx + '" y1="' + (y - 11) + '" x2="' + cx + '" y2="' + (y + 11)
          + '" stroke="var(--ink-3)" stroke-width="1"/>';
        out += '<circle cx="' + cx + '" cy="' + y + '" r="2.5" fill="var(--cyan)"/>';
      });
      out += '<text x="' + X(lo) + '" y="' + (y + 26) + '" text-anchor="middle" class="ro-t ro-t2">'
        + '最严 ' + pct(lo) + '</text>';
      out += '<text x="' + X(hi) + '" y="' + (y + 26) + '" text-anchor="middle" class="ro-t ro-t2">'
        + '最松 ' + pct(hi) + '</text>';
      return out;
    });
  }

  window.Readout = { ci: ci, tradeoff: tradeoff, range: range, pct: pct, num: num, esc: esc };
})();
