/* 数据面加载器：优先走后端 API，失败时回退静态 JSON。
 *
 * 【为什么要有这一层】
 *   2026-09-12 的对抗评审把「后端与前端是两份平行世界」列为 blocker：
 *   `src/app.py` 有 5 个 API 端点，而前端全部读预生成的 `web/kg-data/*.json`，
 *   一个端点都没被调用过 —— 也就是说"系统"其实只有展示侧在跑。
 *
 *   现在后端把前端数据面暴露成 `/api/kg/<name>` 与 `/api/quality/<name>`
 *   （见 src/app.py），前端优先走它。这样：
 *     · 前后端真的连通了，不是靠静态文件假装；
 *     · 一旦 API 不可用（用 python -m http.server 起静态站、或断网演示），
 *       自动回退到同目录的 `kg-data/*.json`，**离线演示铁律不受影响**。
 *
 * 【为什么不做成"只用 API"】
 *   现场演示的首要目标是"能演示"。把数据来源绑死在后端上，
 *   会多一个失败点（后端没起、端口被占、Neo4j 没连上）。
 *   "优先 API + 回退静态"两者兼得，且回退是**静默且可见**的：
 *   `KGData.source` 会记录本次实际用的是哪条路径，供验证脚本断言。
 */
(function () {
  'use strict';

  var cache = Object.create(null);

  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var t = setTimeout(function () { reject(new Error('timeout')); }, ms);
      promise.then(function (v) { clearTimeout(t); resolve(v); },
                   function (e) { clearTimeout(t); reject(e); });
    });
  }

  /**
   * 取一份前端数据。
   * @param {string} name  形如 'analytics.json' / 'comm-0.json'
   * @returns {Promise<object>}
   */
  function kg(name) {
    if (cache[name]) return Promise.resolve(cache[name]);
    var api = '/api/kg/' + encodeURIComponent(name);
    var stat = './kg-data/' + name;

    return withTimeout(fetch(api, { cache: 'no-store' }), 2500)
      .then(function (r) {
        if (!r.ok) throw new Error('API HTTP ' + r.status);
        // 静态站（python -m http.server）访问 /api/... 会返回 404 的 HTML，
        // 所以要校验 content-type，避免把 HTML 当 JSON 解析。
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') < 0) throw new Error('API 非 JSON 响应');
        return r.json();
      })
      .then(function (d) {
        KGData.source[name] = 'api';
        cache[name] = d;
        return d;
      })
      .catch(function () {
        return fetch(stat).then(function (r) {
          if (!r.ok) throw new Error('静态文件 HTTP ' + r.status + '：' + name);
          return r.json();
        }).then(function (d) {
          KGData.source[name] = 'static';
          cache[name] = d;
          return d;
        });
      });
  }

  /** 取一份质量产物（data/quality/<name>），同样优先 API。 */
  function quality(name) {
    var key = 'quality:' + name;
    if (cache[key]) return Promise.resolve(cache[key]);
    return withTimeout(fetch('/api/quality/' + encodeURIComponent(name), { cache: 'no-store' }), 2500)
      .then(function (r) {
        if (!r.ok) throw new Error('API HTTP ' + r.status);
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('json') < 0) throw new Error('API 非 JSON 响应');
        return r.json();
      })
      .then(function (d) { KGData.source[key] = 'api'; cache[key] = d; return d; })
      .catch(function () {
        // 静态站下 data/quality 在 web/ 之外，取不到就明确失败，
        // 不要静默返回空对象让页面显示一堆 0。
        KGData.source[key] = 'unavailable';
        throw new Error('质量产物 ' + name + ' 只能通过后端 API 取得');
      });
  }

  var KGData = {
    kg: kg,
    quality: quality,
    /** 每个文件本次实际走的路径：'api' | 'static' | 'unavailable' */
    source: Object.create(null),
    /** 给验证脚本用：本次是否至少有一个文件走了 API */
    usedApi: function () {
      for (var k in KGData.source) if (KGData.source[k] === 'api') return true;
      return false;
    },
    summary: function () { return JSON.parse(JSON.stringify(KGData.source)); }
  };

  window.KGData = KGData;
})();
