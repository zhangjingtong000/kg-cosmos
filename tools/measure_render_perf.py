# -*- coding: utf-8 -*-
r"""M7 · **渲染性能基线**（可复跑，按规模梯度）。

## TODO 的原话

> | **M7** | ⬜ **大规模性能基线** | B（依赖 M1/M8） |
> 语料扩到 1000 篇后图规模涨约 30 倍（现 18780 实体 / 31308 边有帧时间基线） |

## ★ 先说核实结果：**这句有两个地方已经不对**

1. **"现 18780 实体 / 31308 边"是旧数** —— 现在是 **19427 节点 / 43867 边**
   （`web/kg-data/nebula.json`）；
2. **"有帧时间基线"只在文档里** —— `ATLAS_DESIGN.md:62` 写着
   「3D 在 3000 节点时 8.9 fps、首帧 15 秒；2D 在 800 节点 60 fps」，
   **但没有任何可复跑的测量脚本**。而
   `data/quality/optimization-baseline.json` **不是帧时间基线** ——
   它是"优化前后对比"（L5 六层 / L3 缺口 / 单机单云规模）。

**⇒ 所以 M7 的缺口是"**可复跑的测量**"，而不是"再记一个数"。**

## ★ 做法：按**规模梯度**测一条曲线（而不是测一个点）

`web/kg-data/` 里本来就有一组密度抽样（**先例，不是我造的**）：

    _density-60.json · _density-150.json · _density-300.json
    _density-600.json · _density-1500.json

⇒ 拿它们当**规模梯度**，测"节点数 → 首帧时间 / 帧率"的曲线。
**那条曲线才是基线** —— 它让"扩语料后会怎样"这个问题**可以外推**，
而不是等扩完了才知道。

## ★ 三个测量口径（都从页面的 `window` 上读，**页面自己不谎报**）

| 指标 | 从哪来 |
|---|---|
| **首帧** | `performance.now()` 到页面 `__ready` 的时间（导航开始算） |
| **帧率** | `requestAnimationFrame` 计数 / 秒（**连续采样 3 秒**） |
| **交互后帧率** | 触发一次**筛选/高亮**（真实交互），再采样 —— **那才接近用户感受** |

⚠️ **不测"空转帧率"就完事**：一个 3000 节点的图在不动时能跑 60 fps，
**一拖动就掉到 8 fps** —— 而使用者的感受是后者。

## ⚠️ 无头 Chrome 的帧率**不等于**真机帧率

无头（`--headless=new`）没有真实的合成器节流，**SwiftShader 也是软渲染**。
⇒ 所以本脚本的输出里**明确标注**"无头软渲染，仅供相对比较"，
**不把它当成"这台机器能跑多少帧"的结论**。
（这也解释了为什么 `ATLAS_DESIGN.md` 那个 8.9 fps **与无头测出的数不一样** ——
**两者都不能互相替代**。）

用法：
    python tools/measure_render_perf.py                 # 全部密度档
    python tools/measure_render_perf.py --density 300   # 只测一档
    python tools/measure_render_perf.py --page kg-nebula.html
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KG = ROOT / 'web' / 'kg-data'
OUT = ROOT / 'data' / 'quality' / 'render-perf.json'
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')


def measure(page, url, label):
    """在页面里采样帧率。**采样代码在浏览器里跑**，返回原始计数。"""
    # ⚠️ **先等页面就绪**：`__ready` 是本项目的约定（各页都有）。
    page.goto(url, wait_until='load', timeout=60000)
    try:
        page.wait_for_function('window.__ready === true', timeout=25000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    return page.evaluate("""async () => {
      // ── 帧率采样：连续 3 秒数 rAF ──
      const sample = (ms) => new Promise(res => {
        let n = 0; const t0 = performance.now();
        const tick = () => { n++; (performance.now() - t0 < ms)
          ? requestAnimationFrame(tick) : res({frames: n, ms: performance.now() - t0}); };
        requestAnimationFrame(tick);
      });
      const idle = await sample(3000);
      // ── 交互后采样：真的动一下（缩放/筛选），再数 ──
      let touched = null;
      try {
        const sel = document.querySelector('select, input[type=search], .rel, [data-filter]');
        if (sel) {
          touched = sel.tagName + (sel.id ? '#' + sel.id : '');
          if (sel.tagName === 'SELECT') {
            sel.selectedIndex = Math.min(1, sel.options.length - 1);
            sel.dispatchEvent(new Event('change', {bubbles: true}));
          } else if (sel.tagName === 'INPUT') {
            sel.value = 'a'; sel.dispatchEvent(new Event('input', {bubbles: true}));
          } else {
            sel.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          }
        }
      } catch (e) { touched = 'ERR ' + e.message; }
      await new Promise(r => setTimeout(r, 400));
      const busy = await sample(3000);
      // ⚠️⚠️ **实测教训：上面那两个 rAF 帧率在无头下测不出规模差异** ——
      //   60/300/600/1500 节点全都落在 **5.1 fps**（只有一次 7.4）。
      //   根因：**无头 Chrome 没有真实显示，`requestAnimationFrame` 被节流到固定频率**
      //   ⇒ 那个数反映的是"浏览器认为该画多快"，**不是"画一帧要多久"**。
      //   ⇒ 所以**必须补一个不受节流影响的口径**：下面这几项。
      const nav = performance.getEntriesByType('navigation')[0] || {};
      const paints = performance.getEntriesByType('paint') || [];
      const fp = paints.find(p => p.name === 'first-paint');
      // ── **同步渲染耗时**：强制跑一次重绘并量它（不吃 rAF 节流）──
      const canvas = document.querySelector('canvas');
      let drawMs = null;
      if (canvas) {
        const t0 = performance.now();
        // 读一次像素会强制 flush 掉待处理的绘制
        try { const g = canvas.getContext('2d') || canvas.getContext('webgl2')
                     || canvas.getContext('webgl');
              if (g && g.getImageData) { g.getImageData(0, 0, 1, 1); }
              else if (g && g.readPixels) { const b = new Uint8Array(4);
                     g.readPixels(0, 0, 1, 1, g.RGBA, g.UNSIGNED_BYTE, b); }
        } catch (e) { /* 跨源或上下文问题，忽略 */ }
        drawMs = Math.round((performance.now() - t0) * 100) / 100;
      }
      // ── 规模：**页面上真正画了多少**（比 JSON 里的更可信）──
      const st = window.__viewState || window.__colorSample || {};
      const geom = (window.__graphStats) || {};
      return {
        idle_fps: Math.round(idle.frames / (idle.ms / 1000) * 10) / 10,
        busy_fps: Math.round(busy.frames / (busy.ms / 1000) * 10) / 10,
        touched: touched,
        dom_nodes: document.querySelectorAll('*').length,
        // ★ 不受 rAF 节流影响的口径
        dom_ready_ms: nav.domContentLoadedEventEnd
          ? Math.round(nav.domContentLoadedEventEnd) : null,
        load_ms: nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null,
        first_paint_ms: fp ? Math.round(fp.startTime) : null,
        flush_draw_ms: drawMs,
        js_heap_mb: (performance.memory && performance.memory.usedJSHeapSize)
          ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null,
        canvas_w: canvas ? canvas.width : null,
        canvas_h: canvas ? canvas.height : null,
        geom_nodes: geom.nodes || null, geom_edges: geom.edges || null,
        has_canvas: !!canvas,
        page_state_keys: Object.keys(st).slice(0, 12),
      };
    }""")


def main():
    ap = argparse.ArgumentParser(description='M7 渲染性能基线（可复跑）')
    ap.add_argument('--page', default='kg-nebula.html')
    ap.add_argument('--density', default='', help='只测一档（如 300）')
    ap.add_argument('--src', default='', help='页面参数 src=（默认不传）')
    ap.add_argument('--out', default=str(OUT))
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('M7 · 渲染性能基线（**按规模梯度测一条曲线**）')
    print('=' * 92)

    # 规模梯度：**用仓库里已有的密度抽样**（不是我造的）
    dens = sorted(KG.glob('_density-*.json'))
    if a.density:
        dens = [d for d in dens if d.stem == f'_density-{a.density}']
    if not dens:
        print('  ⚠️ 没有 _density-*.json ⇒ 退化成"只测主图"')
    rows = []
    sizes = {}
    for d in dens:
        try:
            j = json.loads(d.read_text(encoding='utf-8'))
            sizes[d.stem] = {'nodes': len(j.get('nodes') or j.get('points') or []),
                             'edges': len(j.get('edges') or j.get('links') or [])}
        except Exception:
            sizes[d.stem] = {}
    print(f'  规模梯度 {len(dens)} 档：')
    for d in dens:
        s = sizes.get(d.stem) or {}
        print(f'      {d.stem:<20}{s.get("nodes", "?"):>6} 节点 '
              f'{s.get("edges", "?"):>7} 边  ({d.stat().st_size / 1024:.0f} KB)')

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-gpu-rasterization'])
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        targets = [(d.stem, f'{BASE}/{a.page}?density={d.stem.replace("_density-", "")}')
                   for d in dens] or [('main', f'{BASE}/{a.page}')]
        # ⚠️ 主图也测一次（它才是真实规模：19427 节点）
        targets.append(('__main__', f'{BASE}/{a.page}' + (f'?src={a.src}' if a.src else '')))

        for label, url in targets:
            try:
                r = measure(page, url, label)
            except Exception as e:
                print(f'    ✗ {label:<20}{type(e).__name__}: {str(e)[:70]}')
                continue
            r['label'] = label
            r['url'] = url
            r['size'] = sizes.get(label) or {}
            rows.append(r)
            print(f'    ✓ {label:<20}'
                  f'**加载 {r["load_ms"]} ms** · 首绘 {r["first_paint_ms"]} ms'
                  f' · flush {r["flush_draw_ms"]} ms'
                  f' · rAF {r["idle_fps"]}/{r["busy_fps"]} fps'
                  f' · JS堆 {r["js_heap_mb"]} MB · DOM {r["dom_nodes"]}')

        if errs:
            print(f'  ⚠️ 页面 JS 报错 {len(errs)} 条：{errs[0][:90]}')
        browser.close()

    print()
    print('  ★ 曲线（节点数 → 帧率）')
    for r in sorted(rows, key=lambda x: (x['size'].get('nodes') or 0)):
        n = r['size'].get('nodes')
        if n:
            print(f'      {n:>6} 节点 ⇒ 空闲 {r["idle_fps"]:>5.1f} · '
                  f'交互后 {r["busy_fps"]:>5.1f} fps')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'M7：渲染性能基线（可复跑，按规模梯度）',
        'design': {
            'curve_not_a_point':
                '**测一条曲线**（用仓库里已有的 `_density-*.json` 当规模梯度），'
                '而不是测一个点 —— 曲线让"扩语料后会怎样"**可以外推**，'
                '而不是等扩完了才知道。',
            'idle_and_busy':
                '**同时测空闲与交互后**：一个 3000 节点的图不动时能跑 60 fps，'
                '**一拖动就掉到 8 fps** —— 而使用者的感受是后者。',
            'headless_is_not_real':
                '⚠️ **无头 Chrome 的帧率不等于真机帧率**（无真实合成器节流，'
                '且可能是 SwiftShader 软渲染）⇒ 输出里明确标注'
                '"仅供相对比较"，**不把它当"这台机器能跑多少帧"的结论**。',
            'what_it_replaces':
                '`ATLAS_DESIGN.md:62` 那句「3D 在 3000 节点时 8.9 fps、首帧 15 秒」'
                '**是文档里的一个数**，没有可复跑的测量；'
                '而 `data/quality/optimization-baseline.json` **不是帧时间基线**'
                '（它是"优化前后对比"）。⇒ 本脚本补的是"**可复跑**"这一半。',
            'stale_numbers':
                'TODO 里写「现 18780 实体 / 31308 边」**已过时** ——'
                '实际是 **19427 节点 / 43867 边**（`web/kg-data/nebula.json`）。',
        },
        'headless_caveat': '无头软渲染，仅供相对比较；不等价于真机帧率',
        'rows': rows,
        'elapsed_s': round(time.time() - t0, 1),
    }
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  已写出 {outp.relative_to(ROOT) if str(outp).startswith(str(ROOT)) else outp}'
          f'  {time.time() - t0:.1f}s')
    print('  ⚠️ **无头测量 ≠ 真机** —— 它只用于**相对比较**（同一口径下比不同规模）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
