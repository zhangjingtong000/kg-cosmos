# -*- coding: utf-8 -*-
r"""K12 的浏览器实测：`analytics.html` 上的「当场跑一遍」。

## 为什么必须用浏览器测

按钮、轮询、渲染全是前端；而且**要真跑一遍**才知道端到端通不通。
**在 Python 里读源码证明不了它能跑** —— 本项目栽过：
`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，而页面绑的是 `#go` 的 click。

## 断言四组

1. **按钮存在且可点**（`#runVerify`）；
2. **点了之后真的进入"跑着"状态** —— 且 `#verifyOut` 从 `hidden` 变可见；
3. **★ 端到端：等到结束，`__verifyState` 有 `passed`/`total`，且列表渲染出 N 行**
   —— **"界面点一下、机器上真跑一遍"就是这一项的全部意义**，
   所以必须等到**真的出结果**，而不是"点了没报错"；
4. **★★ 后端不可用时，必须说"没跑"，而不是"全过了"** ——
   在**静态站**（8098）上跑这一条：那里没有 `/api/verify/*`。
   **这是这一页最危险的混淆**：把"检查没跑"显示成"检查全过"。
   与 G3（404 静默降级到演示数据）是同一类错误。

用法：
    python tools/cdp_verify_run_verify.py
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 第 1–3 组走**后端**（8000，同源，才有 /api/verify/*）
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')
# 第 4 组走**静态站**（8098，没有 /api/verify/*）
STATIC = os.environ.get('KG_STATIC_PLAIN', 'http://127.0.0.1:8098/web')


def main():
    from playwright.sync_api import sync_playwright

    print('=' * 88)
    print('K12 · 「当场跑一遍」的浏览器实测')
    print('=' * 88)
    print(f'  后端页 {BASE}/analytics.html')
    print(f'  静态页 {STATIC}/analytics.html')
    print()

    # 先确认后端在（否则这一测没意义）
    try:
        with urllib.request.urlopen(BASE.rstrip('/') + '/health', timeout=6) as r:
            json.load(r)
        print('  后端在线 ✅')
    except Exception as e:
        print(f'  ❌ 后端不可达（{type(e).__name__}）—— 先起 `python src/app.py`')
        return 1
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(BASE + '/analytics.html', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2500)

        # ── ① 按钮存在且可点 ─────────────────────────────────────────
        b = page.evaluate("""() => {
          const b = document.getElementById('runVerify');
          return b ? { text: b.textContent.trim(), disabled: !!b.disabled,
                       hidden: !!b.hidden } : null;
        }""")
        print(f'  ① 按钮：{b}')
        ok1 = bool(b and not b['hidden'] and not b['disabled'])
        print(f'     可点：{"✅" if ok1 else "❌"}')
        if not ok1:
            fails.append(f'#runVerify 不可用：{b}')
        print()

        # ── ② 点了之后进入"跑着"状态 ─────────────────────────────────
        page.click('#runVerify')
        page.wait_for_timeout(1500)
        s2 = page.evaluate("""() => ({
          out: (document.getElementById('verifyOut') || {}).hidden,
          txt: ((document.getElementById('verifyOut') || {}).textContent || '').slice(0, 80),
          btn: (document.getElementById('runVerify') || {}).textContent,
          st: window.__verifyState || null,
        })""")
        print(f'  ② 点后：hidden={s2["out"]} · 按钮={s2["btn"]!r}')
        print(f'     输出区：{s2["txt"]!r}')
        ok2 = (s2['out'] is False or s2['out'] is None) and ('跑' in (s2['btn'] or ''))
        print(f'     进入跑着状态：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'点了之后没进入跑着状态：{s2}')
        print()

        # ── ③ ★ 端到端：等到真出结果 ─────────────────────────────────
        #  数据侧 20 项实测约 90 秒 ⇒ 给 240 秒
        deadline = 240
        waited = 0
        st = None
        while waited < deadline:
            page.wait_for_timeout(4000)
            waited += 4
            st = page.evaluate('window.__verifyState || null')
            if st and (st.get('total') or st.get('error')):
                break
        rows = page.evaluate(
            "() => document.querySelectorAll('#verifyOut .vlist li').length")
        print(f'  ③ 等了 {waited}s ⇒ __verifyState={st}')
        print(f'     渲染出 {rows} 行')
        ok3 = bool(st and st.get('total') and st.get('passed') is not None
                   and rows == st.get('total'))
        print(f'     **真跑出结果且渲染完整**：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'没等到真实结果（state={st}, rows={rows}）')
        else:
            print(f'     ⇒ {st["passed"]} / {st["total"]}'
                  f'{"（全过）" if st.get("all_ok") else "（有失败项）"}')
        print()

        # ── ④ ★★ 后端不可用 ⇒ 必须说"没跑" ───────────────────────────
        page.goto(STATIC + '/analytics.html', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2000)
        page.click('#runVerify')
        page.wait_for_timeout(2500)
        s4 = page.evaluate("""() => ({
          txt: ((document.getElementById('verifyOut') || {}).textContent || ''),
          st: window.__verifyState || null,
        })""")
        txt4 = s4['txt']
        print(f'  ④ 静态站（无 /api/verify）点按钮 ⇒')
        print(f'     __verifyState={s4["st"]}')
        print(f'     输出：{txt4[:110]!r}')
        # 判据：**必须出现"没有跑"这类字样，且不能报出通过数**
        ok4 = (('没有跑' in txt4) or ('没跑' in txt4)) and ('全部通过' not in txt4)
        print(f'     **说清是"没跑"而不是"全过了"**：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'后端不可用时没说清"没跑"：{txt4[:120]!r}')
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
