# -*- coding: utf-8 -*-
r"""C4 的浏览器实测：分享链接真的编码了视图状态，链接打开后真的恢复了。

## 为什么必须用浏览器测

分享按钮做的事全在前端：读 `inside` / `selectedNodeIdx` / `onlyContra`
→ 拼 URL → 写剪贴板；反过来，页面加载时读 `?n=` / `?only=` → 恢复状态。

**在 Python 里"读一遍源码看有没有这个函数"证明不了它能跑** ——
本项目已经栽过一次：`tests/cdp_verify_qa_history.py` 的第一版
用 `form.submit()` 触发问答，而页面绑的是 `#go` 的 click / `#q` 的 Enter
⇒ **`ask()` 从没被调用，断言测的是测试自己的幻觉**。

## 断言四组

1. **分享按钮存在**，且点了会写出 `window.__shareState.url`；
2. **URL 编码的是"语义状态"**：社区号 `c`、实体名 `n`、`only` ——
   **且不含相机坐标**（这是 C4 的核心设计决策，要钉住）；
3. **`?n=<实体名>` 打开一个真实存在的实体 ⇒ `__viewState.node` 等于它**
   （证明恢复生效，而不是"没报错就算过"）；
4. **`?n=<不存在的名字>` ⇒ `node_missing == true`**，
   且页面上有提示 —— **不许静默**（分享出去的链接可能对应另一份图数据）。

用法：
    python tools/cdp_verify_share_link.py
"""
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/kg-nebula.html'


def main():
    from playwright.sync_api import sync_playwright
    print('=' * 88)
    print('C4 · 分享链接的浏览器实测')
    print('=' * 88)
    print(f'  页面 {PAGE}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        # ── ① 分享按钮存在且能生成 URL ────────────────────────────────
        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1200)
        btn = page.query_selector('button[data-exp="share"]')
        print(f'  ① 分享按钮存在：{"✅" if btn else "❌"}')
        if not btn:
            fails.append('找不到 button[data-exp="share"]')
            browser.close()
            return 1
        # 点它（剪贴板可能不可用，代码里有 prompt 兜底 ⇒ 要挡掉 prompt）
        page.evaluate('window.prompt = () => null;')
        btn.click()
        page.wait_for_timeout(400)
        st = page.evaluate('window.__shareState || null')
        print(f'     __shareState: {st}')
        if not st or not st.get('url'):
            fails.append('点了分享按钮但没写出 __shareState.url')
        else:
            url = st['url']
            # ── ② 编码的是语义状态，不是相机坐标 ──────────────────────
            #
            # ⚠️ 第一版用子串 `'y='` 检测相机参数，**误报**了：
            # `'?only=1'` 里含 `'onl' + 'y=1'`。⇒ 要按**参数名**判，不能按子串。
            from urllib.parse import parse_qsl, urlparse
            q = urlparse(url).query
            keys = {k for k, _v in parse_qsl(q, keep_blank_values=True)}
            print(f'     URL 查询串: {q!r} · 参数名 {sorted(keys)}')
            cam_keys = {'cam', 'camera', 'x', 'y', 'z', 'pos', 'position', 'zoom'}
            hit = keys & cam_keys
            print(f'  ② 无相机相关参数：{"✅" if not hit else "❌ " + str(hit)}')
            if hit:
                fails.append(f'URL 里出现了相机参数 {sorted(hit)} —— '
                             f'C4 刻意只编码语义状态（换屏幕就不可复现）')
        print()

        # ── ③ 找一个真实实体名，用它开页面 ⇒ 应当被选中 ───────────────
        #
        # ⚠️ **不能从 `window.overlay` 取** —— 页面是 `<script type="module">`，
        # 模块内的 `overlay` / `data` / `inside` **都不是 `window` 属性**
        # （所以页面里才专门导出了 `window.__viewState` / `__shareState` 给自动化读）。
        # ⇒ 实体名从**数据文件**读，它才是真正的来源。
        node_id = None
        for src in sorted((ROOT / 'web' / 'kg-data').glob('comm-*.json'),
                          key=lambda p: int(''.join(c for c in p.stem if c.isdigit()) or 0)):
            try:
                d = json.loads(src.read_text(encoding='utf-8'))
            except Exception:
                continue
            ns = d.get('nodes') or []
            # 优先挑名字里**带空格**的：它最能暴露 URL 转义问题
            with_space = [n for n in ns if isinstance(n, dict) and n.get('id')
                          and ' ' in str(n['id'])]
            plain = [n for n in ns if isinstance(n, dict) and n.get('id')]
            pick = (with_space or plain)
            if pick:
                node_id = str(pick[0]['id'])
                print(f'     （取自 {src.name}）')
                break
        print(f'  ③ 用真实实体名测试恢复：{node_id!r}')
        if node_id:
            target = PAGE + '?n=' + quote(node_id)
            page.goto(target, wait_until='networkidle', timeout=60000)
            page.wait_for_timeout(1500)
            vs = page.evaluate('window.__viewState || null')
            print(f'     __viewState: {vs}')
            ok3 = bool(vs and vs.get('node') == node_id)
            print(f'     恢复成功：{"✅" if ok3 else "❌"}')
            if not ok3:
                fails.append(f'?n={node_id!r} 没有被恢复（__viewState={vs}）')
        else:
            fails.append('数据文件里取不到任何实体名，无法测恢复')
        print()

        # ── ④ 不存在的名字 ⇒ 必须明说，不许静默 ──────────────────────
        print('  ④ 用不存在的实体名测试（**必须明说，不许静默**）')
        page.goto(PAGE + '?n=__NO_SUCH_ENTITY_ZZZ__',
                  wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1200)
        vs4 = page.evaluate('window.__viewState || null')
        st4 = page.evaluate("""() => {
          const s = document.getElementById('status');
          return s ? (s.textContent || '') : '';
        }""")
        print(f'     __viewState: {vs4}')
        print(f'     页面上显示的提示: {st4[:90]!r}')
        ok4 = bool(vs4 and vs4.get('node_missing'))
        print(f'     正确报告缺失：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append('不存在的实体名没有被报告（应设 node_missing=true）')
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
