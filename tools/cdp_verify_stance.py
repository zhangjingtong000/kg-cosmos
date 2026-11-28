# -*- coding: utf-8 -*-
r"""L16 的浏览器实测：立场聚合视图。

## 为什么必须用浏览器测

这一页全是前端：读 `stance.json` → 渲染分布卡 / 每行的立场条 / 可展开的
"谁支持谁反对"。**在 Python 里读源码证明不了它能跑** —— 本项目栽过：
`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，而页面绑的是 `#go` 的 click。

## 断言五组

1. **载入**（`__stanceReady`）且**渲染出的行数 = 数据里的主张数**；
2. **四个分布卡都渲染了**（Yes / No / Mixed / Possibly），
   且**数字与数据一致** —— 只断言"卡在"不够，**数字错了也是错**；
3. **★ 每行的 S/O/T 与数据逐字相等** ——
   这是"立场是数出来的"那句主张的**可核对形式**：
   页面上的"支持 26 · 反对 0"必须与 `stance.json` 里的一致；
4. **进度条宽度 = S/T 与 O/T**（`Math.round` 内 1% 容差）——
   **条形图画错会让人看出错误的印象**，而它比数字更显眼；
5. **可展开区里真的有支持者/反对者**（`<details>` 里的 `li` 数 > 0）
   —— 立场**可回溯**是这一页的一半价值。

用法：
    python tools/cdp_verify_stance.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')
PAGE = BASE + '/legacy/stance.html'
DATA = os.path.join(ROOT, 'web', 'kg-data', 'stance.json')


def main():
    from playwright.sync_api import sync_playwright

    if not os.path.exists(DATA):
        print(f'  ❌ 缺 {DATA}（先跑 tools/build_stance_data.py）')
        return 1
    with open(DATA, encoding='utf-8') as f:
        d = json.load(f)
    claims = d.get('claims') or []
    dist = d.get('distribution') or {}

    print('=' * 88)
    print('L16 · 立场聚合的浏览器实测')
    print('=' * 88)
    print(f'  数据 {len(claims)} 个主张 · 分布 {dist}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1800)

        # ── ① 载入 + 行数 ────────────────────────────────────────────
        st = page.evaluate("""() => ({
          ready: !!window.__stanceReady, st: window.__stanceState || null,
          rows: document.querySelectorAll('#rows .row').length,
          chips: document.querySelectorAll('#dist .dchip').length,
          sub: (document.getElementById('sub') || {}).textContent || '',
        })""")
        print(f'  ① ready={st["ready"]} · 行数 {st["rows"]} · 分布卡 {st["chips"]}')
        print(f'     副标题：{st["sub"][:100]!r}')
        ok1 = st['ready'] and st['rows'] == len(claims)
        print(f'     行数对：{"✅" if ok1 else "❌"}')
        if not ok1:
            fails.append(f'行数不对：{st["rows"]} vs {len(claims)}')
        print()

        # ── ② 四个分布卡 + 数字一致 ──────────────────────────────────
        cards = page.evaluate("""() => [...document.querySelectorAll('#dist .dchip')]
          .map(c => ({k: c.querySelector('.k').textContent.trim(),
                      v: c.querySelector('.v').textContent.trim()}))""")
        print(f'  ② 分布卡：{cards}')
        got = {}
        for c in cards:
            for k in ('Yes', 'No', 'Mixed', 'Possibly'):
                if k in c['k']:
                    got[k] = int(c['v'])
        ok2 = (len(cards) == 4
               and all(got.get(k) == dist.get(k, 0) for k in dist))
        print(f'     四个卡且数字与数据一致：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'分布卡不对：页面 {got} vs 数据 {dist}')
        print()

        # ── ③ 每行的 S/O/T 与数据逐字相等 ───────────────────────────
        rows = page.evaluate("""() => [...document.querySelectorAll('#rows .row')]
          .map(r => {
            const t = r.querySelector('.claim').textContent.trim();
            const n = r.querySelector('.nums').textContent;
            const m = n.match(/支持\\s*(\\d+)\\s*·\\s*反对\\s*(\\d+)\\s*·\\s*合计\\s*(\\d+)/);
            const v = r.querySelector('.verdict').textContent.trim();
            const w = r.querySelectorAll('.meter i');
            return { claim: t, verdict: v,
                     S: m ? +m[1] : null, O: m ? +m[2] : null, T: m ? +m[3] : null,
                     w: w.length === 2 ? [w[0].style.width, w[1].style.width] : null };
          })""")
        bad3 = []
        for r, c in zip(rows, claims):
            if (r['S'] != c['S'] or r['O'] != c['O'] or r['T'] != c['T']
                    or r['verdict'] != c['verdict']):
                bad3.append((r, c))
        print(f'  ③ 逐行比对 {len(rows)} 行 ⇒ 不一致 {len(bad3)} 行')
        if bad3:
            r, c = bad3[0]
            print(f'     例：页面 {r} vs 数据 S={c["S"]} O={c["O"]} T={c["T"]} {c["verdict"]}')
        ok3 = not bad3 and len(rows) == len(claims)
        print(f'     **S/O/T 与标签逐字相等**：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'{len(bad3)} 行的 S/O/T 或标签与数据不符')
        print()

        # ── ④ 进度条宽度 == S/T, O/T ─────────────────────────────────
        bad4 = []
        for r, c in zip(rows, claims):
            if not r['w'] or not c['T']:
                continue
            try:
                ws = float(str(r['w'][0]).replace('%', '') or 0)
                wo = float(str(r['w'][1]).replace('%', '') or 0)
            except ValueError:
                bad4.append((r['claim'], r['w']))
                continue
            if abs(ws - c['S'] / c['T'] * 100) > 1.0 or abs(wo - c['O'] / c['T'] * 100) > 1.0:
                bad4.append((r['claim'], r['w'], c['S'], c['O'], c['T']))
        print(f'  ④ 进度条宽度比对 ⇒ 不符 {len(bad4)} 行')
        if bad4:
            print(f'     例：{bad4[0]}')
        ok4 = not bad4
        print(f'     条形图与数字一致：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'{len(bad4)} 行的进度条宽度与 S/T 不符')
        print()

        # ── ⑤ 可展开区里真的有支持者/反对者 ──────────────────────────
        #   判据①：**所有** details 都展开后，li 总数 > 0；
        #   判据②：至少**一行**同时列了支持者与反对者（否则"双方"只是摆设）
        det = page.evaluate("""() => {
          const ds = [...document.querySelectorAll('#rows details')];
          ds.forEach(d => d.open = true);
          const lis = ds.map(d => d.querySelectorAll('li').length);
          // 找一行同时有"支持方"和"反对方"内容的
          let both = 0;
          for (const r of document.querySelectorAll('#rows .row')) {
            const s = r.querySelectorAll('details li').length;
            const c = r.querySelectorAll('.nums').length;
            if (s >= 2 && c) both++;
          }
          return { n_details: ds.length, total_li: lis.reduce((a, b) => a + b, 0),
                   empty: lis.filter(x => x === 0).length, both: both };
        }""")
        print(f'  ⑤ 可展开区 {det["n_details"]} 个 · li 合计 {det["total_li"]}'
              f' · 空展开区 {det["empty"]} · 有内容的行 {det["both"]}')
        ok5 = (det['n_details'] == len(claims) and det['total_li'] > 0
               and det['empty'] == 0)
        print(f'     立场可回溯（每行都能展开看到谁支持/谁反对）：'
              f'{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'可展开区不对：{det}')
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
