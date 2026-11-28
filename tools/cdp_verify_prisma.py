# -*- coding: utf-8 -*-
r"""L18 的浏览器实测：PRISMA 流程图。

## 断言五组

1. **载入**（`__prismaReady`）且**四个阶段的数字与数据逐字相等** ——
   PRISMA 图的全部价值就是那几个数字，**画错一个数比不画更糟**；
2. **排除框在**，且它的数字 = 数据里的排除数；
3. **★ 图上没有编出来的数字** ——
   断言 `初筛` 那一框的数字**等于检索数**（我们**没有**初筛淘汰），
   且页面上**明确写出**"没有初筛淘汰"这句话。
   这一条针对的是"**照抄模板填一个好看的数**"这个失败模式；
4. **检索式表渲染了 3 行**，且**每行都有 query 与计数**；
5. **零外部请求** + **页面自己说明数据来源**
   —— 这一页要能**离线打开**（演示机可能没网）。

用法：
    python tools/cdp_verify_prisma.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')
PAGE = BASE + '/legacy/prisma.html'
DATA = os.path.join(ROOT, 'web', 'kg-data', 'prisma.json')


def main():
    from playwright.sync_api import sync_playwright

    if not os.path.exists(DATA):
        print(f'  ❌ 缺 {DATA}（先跑 tools/build_prisma_data.py）')
        return 1
    with open(DATA, encoding='utf-8') as f:
        d = json.load(f)
    stages = d.get('stages') or []
    excl = (d.get('excluded') or [{}])[0]
    n_excl = excl.get('n', 0)
    queries = d.get('queries') or []

    print('=' * 88)
    print('L18 · PRISMA 流程图的浏览器实测')
    print('=' * 88)
    print(f'  数据：{len(stages)} 个阶段 · 排除 {n_excl} · 检索式 {len(queries)} 个')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        errs, external = [], []
        page.on('pageerror', lambda e: errs.append(str(e)))
        page.on('request', lambda r: external.append(r.url)
                if not r.url.startswith(BASE) else None)

        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1200)

        # ── ① 四个阶段的数字与数据逐字相等 ───────────────────────────
        got = page.evaluate("""() => ({
          ready: !!window.__prismaReady, st: window.__prismaState || null,
          nums: [...document.querySelectorAll('#flow .stage .n')]
                  .map(e => e.textContent.trim()),
          labels: [...document.querySelectorAll('#flow .stage .lb')]
                  .map(e => e.textContent.trim()),
          notes: [...document.querySelectorAll('#flow .stage .note')]
                  .map(e => e.textContent.trim()),
        })""")
        print(f'  ① ready={got["ready"]} · 阶段数 {len(got["nums"])}')
        print(f'     图上的数字：{got["nums"]}')
        want = [str(s['n']) for s in stages]
        print(f'     数据里的数字：{want}')
        ok1 = (got['ready'] and got['nums'][:len(want)] == want
               and got['st'] and len(got['st'].get('stages') or []) == len(stages))
        print(f'     **逐字相等**：{"✅" if ok1 else "❌"}')
        if not ok1:
            fails.append(f'阶段数字不符：页面 {got["nums"]} vs 数据 {want}')
        print()

        # ── ② 排除框在且数字对 ───────────────────────────────────────
        exn = page.evaluate(
            "() => (document.querySelector('#flow .stage.excl .n') || {}).textContent")
        print(f'  ② 排除框：{exn!r}（数据 {n_excl}）')
        ok2 = exn is not None and int(str(exn).strip()) == n_excl
        print(f'     {"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'排除框数字不对：{exn!r} vs {n_excl}')
        print()

        # ── ③ ★ 图上没有编出来的数字 / 说明没初筛淘汰 ────────────────
        #  本流程**没有初筛淘汰** ⇒ Screening 的数字应**等于** Identification
        n_ident = stages[0]['n'] if stages else None
        n_screen = stages[1]['n'] if len(stages) > 1 else None
        said = page.evaluate("""() => {
          const t = (document.body.innerText || '');
          return { no_screen: t.indexOf('没有初筛淘汰') >= 0
                            || t.indexOf('本流程没有初筛淘汰') >= 0,
                   no_fabricate: t.indexOf('不编一个数') >= 0,
                   caliber: t.indexOf('口径') >= 0 };
        }""")
        print(f'  ③ Identification={n_ident} · Screening={n_screen}（应相等）')
        print(f'     页面写明"没有初筛淘汰"：{said["no_screen"]}')
        print(f'     页面写明"不编一个数"：{said["no_fabricate"]}')
        ok3 = (n_ident == n_screen and said['no_screen'] and said['no_fabricate'])
        print(f'     **没有照抄模板编数**：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'初筛阶段像是在套模板：ident={n_ident} '
                         f'screen={n_screen} said={said}')
        print()

        # ── ④ 检索式表 ───────────────────────────────────────────────
        rows = page.evaluate("""() => [...document.querySelectorAll('#queries tbody tr')]
          .map(tr => [...tr.querySelectorAll('td')].map(td => td.textContent.trim()))""")
        print(f'  ④ 检索式表 {len(rows)} 行（数据 {len(queries)}）')
        if rows:
            print(f'     样例：{rows[0][0][:56]!r} 命中 {rows[0][1]} 入库 {rows[0][2]}')
        ok4 = len(rows) == len(queries) and all(len(r) == 3 for r in rows)
        print(f'     {"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'检索式表不对：{len(rows)} vs {len(queries)}')
        print()

        # ── ⑤ 零外部请求 + 说明了数据来源 ────────────────────────────
        src = page.evaluate("""() => {
          const t = (document.body.innerText || '');
          return { corpus: t.indexOf('corpus-index.json') >= 0
                           || t.indexOf('data/corpus') >= 0,
                   script: t.indexOf('build_prisma_data') >= 0 };
        }""")
        print(f'  ⑤ 非本站请求 {len(external)} 条')
        for u in external[:4]:
            print(f'     ⚠️ {u[:96]}')
        print(f'     页面说明数据来源：{src}')
        ok5 = not external and src['script']
        print(f'     **离线可开 + 说明来源**：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'外部请求或来源说明缺失：ext={external[:2]} src={src}')
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
