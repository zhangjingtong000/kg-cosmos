# -*- coding: utf-8 -*-
r"""F4 的浏览器实测：使用关系二部图。

## 为什么必须用浏览器测

这一页全靠前端画 SVG：读数据 → 排版 → 绑定悬停/点击 → 聚焦时改 class。
**在 Python 里读源码证明不了它能跑** —— 本项目栽过：
`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，而页面绑的是 `#go` 的 click。

## 断言四组

1. **数据载入**（`__bipartiteReady`）且**两列节点都真的画出来了**
   （SVG 里的 `.node` 数量 = 左 + 右）——
   只断言"数据取到了"是不够的：**画不出来才是这一页真正的失败模式**；
2. **连线数对**（`path.link` 数量 = `links` 长度）；
3. **点一个节点 ⇒ 聚焦生效**：`__bipartiteState.focus` 等于它，
   **且有连线被标成 `hot`**（不是"点了没报错"）；
4. **再点一次 ⇒ 取消聚焦**（是开关，不是一次性）。

用法：
    python tools/cdp_verify_bipartite.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/bipartite.html'


def main():
    from playwright.sync_api import sync_playwright

    print('=' * 88)
    print('F4 · 使用关系二部图的浏览器实测')
    print('=' * 88)
    print(f'  页面 {PAGE}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 900})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1500)

        # ── ① 载入 + 节点真的画出来了 ─────────────────────────────────
        info = page.evaluate("""() => {
          const st = window.__bipartiteState || null;
          const svg = document.getElementById('svg');
          return { ready: !!window.__bipartiteReady, state: st,
                   nodes: svg ? svg.querySelectorAll('.node').length : -1,
                   links: svg ? svg.querySelectorAll('path.link').length : -1,
                   texts: svg ? svg.querySelectorAll('.node text').length : -1,
                   sub: (document.getElementById('sub') || {}).textContent || '' };
        }""")
        print(f'  ① ready={info["ready"]} · state={info["state"]}')
        print(f'     SVG 里：.node {info["nodes"]} · path.link {info["links"]} '
              f'· .node text {info["texts"]}')
        print(f'     副标题：{info["sub"][:110]!r}')
        if not info['ready'] or not info['state']:
            fails.append('页面没载入数据（__bipartiteReady 未置位）')
        else:
            want = info['state']['n_left'] + info['state']['n_right']
            if info['nodes'] != want:
                fails.append(f'画出来的节点 {info["nodes"]} ≠ 应有 {want}'
                             f'（左 {info["state"]["n_left"]} + 右 {info["state"]["n_right"]}）')
            else:
                print(f'     ✅ 节点数对（{want}）')
        print()

        # ── ② 连线数 ──────────────────────────────────────────────────
        if info['state']:
            if info['links'] != info['state']['n_links']:
                fails.append(f'画出来的连线 {info["links"]} ≠ 应有 {info["state"]["n_links"]}')
            else:
                print(f'  ② ✅ 连线数对（{info["links"]}）')
        print()

        # ── ③ 点一个节点 ⇒ 聚焦 ───────────────────────────────────────
        #   挑一个**真的有连线的**节点（挑到孤立点则 hot 会是 0，测不出东西）
        #
        # ⚠️ **不能用 `g.click()`** —— `g` 是 **SVG 元素**，
        #   `SVGElement.prototype.click` 在部分环境里不存在
        #   （实测报 `TypeError: g.click is not a function`）。
        #   ⇒ 用 `dispatchEvent(new MouseEvent('click', {bubbles:true}))`，
        #     它在 SVG 与 HTML 上行为一致。
        clicked = """(id) => {
          const svg = document.getElementById('svg');
          const g = [...svg.querySelectorAll('.node')].find(x => x.dataset.id === id);
          if (!g) return false;
          g.dispatchEvent(new MouseEvent('click', { bubbles: true }));
          return true;
        }"""
        picked = page.evaluate("""() => {
          const svg = document.getElementById('svg');
          const p = svg.querySelector('path.link');
          if (!p) return null;
          const id = p.dataset.l;
          const g = [...svg.querySelectorAll('.node')].find(x => x.dataset.id === id);
          if (!g) return null;
          g.dispatchEvent(new MouseEvent('click', { bubbles: true }));
          return id;
        }""")
        page.wait_for_timeout(500)
        st3 = page.evaluate('window.__bipartiteState || null')
        print(f'  ③ 点了 {picked!r}')
        print(f'     __bipartiteState: {st3}')
        ok3 = bool(st3 and st3.get('focus') == picked and (st3.get('hot_links') or 0) > 0)
        print(f'     聚焦生效且有高亮连线：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'点了节点但聚焦/hot 没生效（picked={picked!r}, state={st3}）')
        print()

        # ── ④ 再点一次 ⇒ 取消 ─────────────────────────────────────────
        page.evaluate(clicked, picked)
        page.wait_for_timeout(400)
        st4 = page.evaluate('window.__bipartiteState || null')
        print(f'  ④ 再点一次 ⇒ {st4}')
        ok4 = bool(st4 and st4.get('focus') is None)
        print(f'     取消聚焦：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'再点一次没取消（state={st4}）—— 应当是开关')
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
    print('✅ 四组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
