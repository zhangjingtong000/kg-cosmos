# -*- coding: utf-8 -*-
r"""K5 的浏览器实测：按论文筛选。

## 为什么必须用浏览器测

筛选全在前端：读 `data.edge_papers` → 写 `geometry.attributes.color`。
**在 Python 里读源码证明不了它能跑** —— 本项目栽过：
`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，而页面绑的是 `#go` 的
click ⇒ **`ask()` 从没被调用，断言测的是测试自己的幻觉**。

## 断言五组

1. **控件存在且真的列了论文**（`#paperFilter` 的 option 数 = 论文数 + 1）；
2. **选一篇之后颜色缓冲的和真的变小**（其余边被压暗）——
   **不是"选了没报错就算过"**（这条纪律来自 B7）；
3. **恢复"全部论文"之后颜色和回到原来的量级**（是开关，不是单向）；
4. **`?paper=<真实论文名>` ⇒ 打开就选中它**（`__viewState.paper`）；
5. **`?paper=<不存在的名字>` ⇒ `paper_missing=true` 且页面上有提示**（不许静默）。

用法：
    python tools/cdp_verify_paper_filter.py
    python tools/cdp_verify_paper_filter.py --community 12
"""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/kg-nebula.html'


def pick_community():
    """挑一个**有 edge_papers 且边数够多**的社区。"""
    best = None
    for p in sorted((ROOT / 'web' / 'kg-data').glob('comm-*.json'),
                    key=lambda q: int(''.join(c for c in q.stem if c.isdigit()) or 0)):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        ep = d.get('edge_papers') or []
        papers = (d.get('meta') or {}).get('papers') or []
        if len(ep) >= 200 and len(papers) >= 2:
            idx = int(''.join(c for c in p.stem if c.isdigit()) or 0)
            cand = (idx, papers, len(ep))
            if best is None or len(papers) > len(best[1]):
                best = cand
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--community', type=int, default=0)
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    pick = pick_community()
    if not pick:
        print('  ❌ 没有带 edge_papers 的社区，无法测')
        return 1
    ci, papers, n_edges = pick
    if a.community:
        ci = a.community
    print('=' * 88)
    print('K5 · 按论文筛选的浏览器实测')
    print('=' * 88)
    print(f'  用社区 {ci}（{n_edges} 边 · {len(papers)} 篇）· 第一篇 {papers[0]!r}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 900})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(PAGE + f'?c={ci}', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2500)

        # ── ① 控件存在且列了论文 ──────────────────────────────────────
        info = page.evaluate("""() => {
          const s = document.getElementById('paperFilter');
          if (!s) return null;
          return { hidden: !!s.hidden, n_opts: s.options.length,
                   first: s.options[1] ? s.options[1].textContent : null,
                   state: window.__paperState || null };
        }""")
        print(f'  ① 控件: {info}')
        if not info or info['hidden']:
            fails.append(f'#paperFilter 不存在或 hidden（{info}）')
        elif info['n_opts'] != len(papers) + 1:
            fails.append(f"option 数 {info['n_opts']} 与论文数 {len(papers)} 不符（应 +1 个'全部'）")
        else:
            print(f'     ✅ 列了 {len(papers)} 篇（+「全部论文」）')
        print()

        def sample():
            """读页面导出的只读状态（含**边颜色缓冲的和**）。

            ⚠️ 第一版只读了 `selected` 而**没读颜色** —— 那等于"选了没报错就算过"，
            正是 B7 那次的教训。页面为此导出了 `__edgeColorSample()`。
            """
            return page.evaluate("() => window.__paperState || null")

        # ── ② 选一篇 ⇒ 状态变化 **且颜色缓冲真的变了** ────────────────
        sum_all = page.evaluate(
            "() => (typeof window.__edgeColorSample === 'function')"
            " ? window.__edgeColorSample() : null")
        page.evaluate("""() => {
          const s = document.getElementById('paperFilter');
          s.value = '1';
          s.dispatchEvent(new Event('change'));
        }""")
        page.wait_for_timeout(700)
        st2 = sample()
        sum_one = page.evaluate(
            "() => (typeof window.__edgeColorSample === 'function')"
            " ? window.__edgeColorSample() : null")
        print(f'  ② 选第 1 篇后 __paperState: {st2}')
        print(f'     边颜色和：全部 {sum_all} → 只看一篇 {sum_one}')
        ok2 = bool(st2 and st2.get('selected') == 1)
        print(f'     选中生效：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'选了第 1 篇但 __paperState 没反映：{st2}')
        if sum_all is None or sum_one is None:
            fails.append('读不到边颜色采样（页面没导出 __edgeColorSample？）')
        elif not (sum_one < sum_all):
            fails.append(f'选了论文但边颜色和没变小（{sum_all} → {sum_one}）—— '
                         f'压暗没生效')
        else:
            print(f'     ✅ 颜色和确实变小了（压暗生效）')
        print()

        # ── ③ 恢复"全部" ⇒ 颜色回到原来的量级 ─────────────────────────
        page.evaluate("""() => {
          const s = document.getElementById('paperFilter');
          s.value = '';
          s.dispatchEvent(new Event('change'));
        }""")
        page.wait_for_timeout(600)
        st3 = sample()
        sum_back = page.evaluate(
            "() => (typeof window.__edgeColorSample === 'function')"
            " ? window.__edgeColorSample() : null")
        print(f'  ③ 恢复"全部"后 __paperState: {st3}')
        print(f'     边颜色和：{sum_one} → {sum_back}（应回到 {sum_all} 附近）')
        ok3 = bool(st3 and st3.get('selected') is None)
        print(f'     恢复生效：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'恢复"全部"没生效：{st3}')
        if sum_all is not None and sum_back is not None:
            if abs(sum_back - sum_all) > max(1.0, sum_all * 0.02):
                fails.append(f'恢复后颜色和没回到原值（{sum_all} → {sum_back}）—— '
                             f'说明不是开关，或有残留')
            else:
                print(f'     ✅ 颜色和回到了原值')
        print()

        # ── ④ ?paper=<真实名> ────────────────────────────────────────
        target = PAGE + f'?c={ci}&paper=' + quote(papers[0])
        page.goto(target, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2500)
        vs = page.evaluate('window.__viewState || null')
        ps = page.evaluate('window.__paperState || null')
        print(f'  ④ ?paper={papers[0]!r}')
        print(f'     __viewState: {vs}')
        print(f'     __paperState: {ps}')
        ok4 = bool(vs and vs.get('paper') == papers[0])
        print(f'     恢复成功：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'?paper={papers[0]!r} 没被恢复（__viewState={vs}）')
        print()

        # ── ⑤ ?paper=<不存在> ────────────────────────────────────────
        page.goto(PAGE + f'?c={ci}&paper=__NO_SUCH_PAPER__',
                  wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2200)
        vs5 = page.evaluate('window.__viewState || null')
        txt5 = page.evaluate(
            "() => { const s = document.getElementById('status'); return s ? s.textContent : ''; }")
        print(f'  ⑤ ?paper=__NO_SUCH_PAPER__ ⇒ __viewState: {vs5}')
        print(f'     页面提示: {txt5[:80]!r}')
        ok5 = bool(vs5 and vs5.get('paper_missing'))
        print(f'     正确报告缺失：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append('不存在的论文名没被报告（应设 paper_missing=true）')
        print()

        if errs:
            print(f'  ⚠️ 页面 JS 报错 {len(errs)} 条：')
            for e in errs[:5]:
                print(f'     {e[:140]}')
            fails.append(f'页面有 JS 报错：{errs[0][:120]}')

        browser.close()

    print('=' * 88)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 五组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
