# -*- coding: utf-8 -*-
r"""F2 的浏览器实测：多图对比。

## 为什么必须用浏览器测

这一页全靠前端：取索引 → 按需取两篇 → **在浏览器里算交集** → 渲染三栏与差异表。
**在 Python 里读源码证明不了它能跑** —— 本项目栽过：
`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，而页面绑的是 `#go` 的 click。

## ★ 最强的那条断言：**在 Python 里独立算一遍，与浏览器算出的 KPI 比对**

"页面渲染出来了"是弱断言（渲染一个错的数也算渲染）。
这一页的核心输出是**交集的规模**，而那**可以在 Python 里独立复算** ——
⇒ 两边必须逐字相等。**那才证明"算对了"，而不只是"画出来了"。**

## 断言六组

1. 载入（`__diffReady`）且两个 `<select>` 都填满了；
2. **默认选的是 J 最高的那一对**（与索引里的 `pairs[0]` 一致）；
3. **★ KPI 与 Python 独立复算逐字相等**（共有实体 / Jaccard / 共有关系）；
4. 三栏列表都渲染了（只在左 / 共有 / 只在右）；
5. **「值得看的对」条数 = 索引里零共享关系的对数**；
6. 点其中一条 ⇒ **切换生效**（`__diffState.a/b` 变了）。

用法：
    python tools/cdp_verify_diff.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/diff.html'
DATA = ROOT / 'web' / 'kg-data' / 'diff.json'
DIFF_DIR = ROOT / 'web' / 'kg-data' / 'diff'


def main():
    from playwright.sync_api import sync_playwright

    if not DATA.exists():
        print(f'  ❌ 缺 {DATA}（先跑 tools/build_diff_data.py）')
        return 1
    idx = json.loads(DATA.read_text(encoding='utf-8'))
    a0, b0 = idx['pairs'][0]['a'], idx['pairs'][0]['b']
    n_zero = sum(1 for p in idx['pairs'] if p['zero_shared_edges'])

    print('=' * 88)
    print('F2 · 多图对比的浏览器实测')
    print('=' * 88)
    print(f'  数据 {len(idx["papers"])} 篇 · {len(idx["pairs"])} 对'
          f' · 默认对比 {a0} × {b0} · 零共享关系的对 {n_zero}')
    print()

    # ── Python 侧独立复算（用于第 3 组断言）──
    da = json.loads((DIFF_DIR / f'{a0}.json').read_text(encoding='utf-8'))
    db = json.loads((DIFF_DIR / f'{b0}.json').read_text(encoding='utf-8'))
    na, nb = set(da['nodes']), set(db['nodes'])
    inter = na & nb
    j_want = len(inter) / max(1, len(na | nb))
    einter = set(da['edges']) & set(db['edges'])
    print(f'  Python 复算：共有实体 {len(inter)} · J={j_want:.4f}'
          f' · 共有关系 {len(einter)}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2500)

        # ── ① 载入 + 两个 select ─────────────────────────────────────
        info = page.evaluate("""() => ({
          ready: !!window.__diffReady,
          st: window.__diffState || null,
          optsA: document.getElementById('selA').options.length,
          optsB: document.getElementById('selB').options.length,
          vA: document.getElementById('selA').value,
          vB: document.getElementById('selB').value,
          sub: (document.getElementById('sub')||{}).textContent || '',
        })""")
        print(f'  ① ready={info["ready"]} · st={info["st"]}')
        print(f'     select 选项 {info["optsA"]}/{info["optsB"]}'
              f' · 默认 {info["vA"]!r} × {info["vB"]!r}')
        print(f'     副标题：{info["sub"][:110]!r}')
        if not info['ready'] or not info['st']:
            fails.append('页面没就绪（__diffReady 未置位）')
        if info['optsA'] != len(idx['papers']) or info['optsB'] != len(idx['papers']):
            fails.append(f'select 选项数不对（{info["optsA"]}/{info["optsB"]} '
                         f'vs {len(idx["papers"])}）')
        else:
            print(f'     ✅ 两个 select 各 {len(idx["papers"])} 项')
        print()

        # ── ② 默认是 J 最高那对 ──────────────────────────────────────
        ok2 = info['vA'] == a0 and info['vB'] == b0
        print(f'  ② 默认选 {a0} × {b0}（J 最高）：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'默认不是 J 最高那对（得到 {info["vA"]} × {info["vB"]}）')
        print()

        # ── ③ ★ KPI 与 Python 复算逐字相等 ──────────────────────────
        dom = page.evaluate("""() => ({
          nInter: (document.getElementById('nInter')||{}).textContent,
          nJ: (document.getElementById('nJ')||{}).textContent,
          nEInter: (document.getElementById('nEInter')||{}).textContent,
        })""")
        print(f'  ③ DOM KPI: {dom}')
        print(f'     Python  ：共有实体 {len(inter)} · J={j_want:.4f}'
              f' · 共有关系 {len(einter)}')
        ok3 = (dom['nInter'] == f'{len(inter):,}'
               and abs(float(dom['nJ']) - j_want) < 1e-4
               and dom['nEInter'] == f'{len(einter):,}')
        print(f'     **逐字相等**：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(
                f'KPI 与独立复算不符：DOM={dom}，Python 期望 '
                f'nInter={len(inter):,} nJ={j_want:.4f} nEInter={len(einter):,}')
        print()

        # ── ④ 三栏都渲染了 ───────────────────────────────────────────
        cols = page.evaluate("""() => ({
          a: document.querySelectorAll('#onlyA li').length,
          c: document.querySelectorAll('#bothN li').length,
          b: document.querySelectorAll('#onlyB li').length,
        })""")
        print(f'  ④ 三栏 li 数：只在左 {cols["a"]} · 共有 {cols["c"]} · 只在右 {cols["b"]}')
        ok4 = cols['c'] > 0 and cols['a'] > 0 and cols['b'] > 0
        print(f'     三栏都不空：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'三栏有空栏：{cols}')
        print()

        # ── ⑤ 「值得看的对」条数 ─────────────────────────────────────
        nbtn = page.evaluate(
            "() => document.querySelectorAll('#interesting button').length")
        want_btn = min(24, n_zero)
        print(f'  ⑤ 「值得看的对」按钮 {nbtn} 个（索引里 {n_zero} 对，'
              f'列表上限 24 ⇒ 期望 {want_btn}）')
        ok5 = nbtn == want_btn
        print(f'     条数对：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'「值得看的对」条数不对（{nbtn} vs {want_btn}）')
        print()

        # ── ⑥ 点一条 ⇒ 切换生效 ─────────────────────────────────────
        before = page.evaluate('window.__diffState')
        clicked = page.evaluate("""() => {
          const b = document.querySelector('#interesting button');
          if (!b) return null;
          const ab = [b.dataset.a, b.dataset.b];
          b.click();
          return ab;
        }""")
        page.wait_for_timeout(1800)
        after = page.evaluate('window.__diffState')
        print(f'  ⑥ 点了 {clicked} ⇒ {before} → {after}')
        ok6 = bool(clicked and after and
                   (after.get('a'), after.get('b')) == (clicked[0], clicked[1]))
        print(f'     切换生效：{"✅" if ok6 else "❌"}')
        if not ok6:
            fails.append(f'点了「值得看的对」但没切换（clicked={clicked}, after={after}）')
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
    print('✅ 六组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
