# -*- coding: utf-8 -*-
r"""F1 的浏览器实测：时间演化。

## 为什么必须用浏览器测

这一页全靠前端：读 `temporal.json` → 渲染 KPI / 折线图 / 新增列表，
拖滑块或播放切帧。**在 Python 里读源码证明不了它能跑** ——
本项目栽过：`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，
而页面绑的是 `#go` 的 click。

## 断言五组

1. **载入**（`__temporalReady`）且**帧数对**（= 数据里的 `years` 数）；
2. **切到最后一帧后，KPI 上的数字等于该帧的**（
   **不是"页面没报错"** —— 要读 DOM 里的文本）；
3. **折线图真的画出来了**（`svg` 里有 `path.pline` 与圆点），
   且**有与帧数相等的数据点**；
4. **新增列表非空**（`top_new_nodes` 渲染成了 pill）；
5. **播放/暂停是开关**（点两次回到"▶ 播放"）。

用法：
    python tools/cdp_verify_temporal.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/legacy/temporal.html'
DATA = ROOT + '/web/kg-data/temporal.json'


def main():
    import json
    from pathlib import Path
    from playwright.sync_api import sync_playwright

    if not Path(DATA).exists():
        print(f'  ❌ 缺 {DATA}（先跑 tools/build_temporal_data.py）')
        return 1
    d = json.loads(Path(DATA).read_text(encoding='utf-8'))
    n_frames = len(d.get('frames') or [])
    last = d['frames'][-1]

    print('=' * 88)
    print('F1 · 时间演化的浏览器实测')
    print('=' * 88)
    print(f'  数据 {n_frames} 帧（{d["years"][0]}–{d["years"][-1]}）'
          f' · 末帧 {last["year"]}：{last["n_nodes"]} 实体 / {last["n_edges"]} 关系')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1500)

        # ── ① 载入 + 帧数 ────────────────────────────────────────────
        info = page.evaluate("""() => {
          const st = window.__temporalState || null;
          const svg = document.getElementById('chart');
          return { ready: !!window.__temporalReady, state: st,
                   slider_max: (document.getElementById('slider')||{}).max,
                   pline: svg ? svg.querySelectorAll('path.pline').length : -1,
                   pts: svg ? svg.querySelectorAll('circle.pt').length : -1,
                   sub: (document.getElementById('sub')||{}).textContent || '' };
        }""")
        print(f'  ① ready={info["ready"]} · state={info["state"]}')
        print(f'     slider.max={info["slider_max"]} · 折线 {info["pline"]} 条'
              f' · 数据点 {info["pts"]} 个')
        print(f'     副标题：{info["sub"][:110]!r}')
        if not info['ready'] or not info['state']:
            fails.append('页面没载入数据（__temporalReady 未置位）')
        else:
            if info['state']['n_frames'] != n_frames:
                fails.append(f'帧数 {info["state"]["n_frames"]} ≠ 数据 {n_frames}')
            else:
                print(f'     ✅ 帧数对（{n_frames}）')
        print()

        # ── ② 切到最后一帧 ⇒ KPI 上的数字要对 ────────────────────────
        page.evaluate('(i) => window.__temporalGo(i)', n_frames - 1)
        page.wait_for_timeout(500)
        dom = page.evaluate("""() => ({
          year: (document.getElementById('year')||{}).textContent,
          nodes: (document.getElementById('n_nodes')||{}).textContent,
          edges: (document.getElementById('n_edges')||{}).textContent,
          nn: (document.getElementById('nn')||{}).textContent,
        })""")
        print(f'  ② 末帧 DOM: {dom}')
        want_nodes = f'{last["n_nodes"]:,}'
        want_edges = f'{last["n_edges"]:,}'
        ok2 = (str(dom['year']) == str(last['year'])
               and dom['nodes'] == want_nodes and dom['edges'] == want_edges)
        print(f'     期望 {last["year"]} / {want_nodes} / {want_edges}：'
              f'{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'末帧 KPI 不对：得到 {dom}，期望 year={last["year"]} '
                         f'nodes={want_nodes} edges={want_edges}')
        print()

        # ── ③ 折线图真的画出来了 ─────────────────────────────────────
        ok3 = (info['pline'] == 2 and info['pts'] == n_frames * 2)
        print(f'  ③ 折线 2 条（实体/关系）与 {n_frames * 2} 个数据点：'
              f'{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'折线图没画全：pline={info["pline"]} pts={info["pts"]}'
                         f'（期望 2 / {n_frames * 2}）')
        print()

        # ── ④ 新增列表非空 ───────────────────────────────────────────
        pills = page.evaluate(
            "() => document.querySelectorAll('#newnodes .pill').length")
        print(f'  ④ 新增实体 pill 数：{pills}（数据里末帧有 '
              f'{len(last.get("top_new_nodes") or [])} 个）')
        ok4 = pills == len(last.get('top_new_nodes') or [])
        print(f'     渲染完整：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'新增实体列表渲染不全（{pills} vs '
                         f'{len(last.get("top_new_nodes") or [])}）')
        print()

        # ── ⑤ 播放/暂停是开关 ────────────────────────────────────────
        t1 = page.evaluate("""() => {
          document.getElementById('play').click();
          return document.getElementById('play').textContent;
        }""")
        page.wait_for_timeout(300)
        t2 = page.evaluate("""() => {
          document.getElementById('play').click();
          return document.getElementById('play').textContent;
        }""")
        print(f'  ⑤ 播放按钮：点一次 {t1!r} → 再点 {t2!r}')
        ok5 = '暂停' in t1 and '播放' in t2
        print(f'     是开关：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'播放按钮不是开关（{t1!r} → {t2!r}）')
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
