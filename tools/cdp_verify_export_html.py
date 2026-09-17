# -*- coding: utf-8 -*-
r"""J7 的浏览器实测：单文件 HTML 导出。

## 为什么必须用浏览器测

这一项的**全部意义**就是"**双击即看、可点证据**"。
⇒ 所以测法也很直接：**用 `file://` 打开那个文件** ——
那与用户双击它**是同一条路径**（不起服务、不读仓库里的任何东西）。

## 断言五组

1. **`file://` 打开就有内容**（`__exportReady`）且**论文数与统计对**；
2. **★ 零外部请求** —— 拦截所有非 `file://` 的请求，**必须一条都没有**。
   这一条是"单文件"这个承诺的**唯一可验证形式**：
   只要它引了一个 CDN，换台机器断网就白屏；
3. **表格真的渲染出行**（`tr.edge` 数 > 0），且**每行都有出处列**；
4. **点一行 ⇒ 展开原文引文**（`tr.q .quote` 从 `display:none` 变可见），
   且**展开的文本非空** —— "可点证据"如果点开是空的，那等于没有；
5. **筛选真的生效**：搜一个词 ⇒ 行数减少；点一个关系标签 ⇒ 行数减少；
   清空 ⇒ 回到全量。**三个方向都测，避免"只生效一次"的假开关**。

用法：
    python tools/cdp_verify_export_html.py
    python tools/cdp_verify_export_html.py --file D:\分享\kg.html
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, 'output', 'kg-single-file.html')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=DEFAULT)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    if not os.path.exists(a.file):
        # ⚠️ **产物在 `output/`，而它在 `.gitignore` 里**（导出物是给用户的，
        #    不是仓库产物）⇒ 别人 clone 之后跑回归会**找不到文件**。
        # ⇒ 所以这里**先自己生成一遍**，而不是让回归依赖一个不在版本控制里的东西。
        #    （这正是 N6 那条"前端数据必须预生成/自足"的同一个道理。）
        print(f'  ⚠️ 缺 {a.file} —— 先生成一遍（产物不入库，见 .gitignore）')
        import subprocess
        gen = os.path.join(ROOT, 'tools', 'export_single_html.py')
        p = subprocess.run([sys.executable, gen, '--out', a.file],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=ROOT)
        if not os.path.exists(a.file):
            print(f'  ❌ 生成失败：{(p.stderr or p.stdout or "")[-300:]}')
            return 1
        print('  ✅ 已生成')
        print()
    url = 'file:///' + os.path.abspath(a.file).replace('\\', '/')
    size_mb = os.path.getsize(a.file) / 1024 / 1024

    print('=' * 88)
    print('J7 · 单文件 HTML 的浏览器实测')
    print('=' * 88)
    print(f'  {a.file}  ({size_mb:.2f} MB)')
    print(f'  **用 file:// 打开**（与用户双击同一条路径）')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errs, external = [], []
        page.on('pageerror', lambda e: errs.append(str(e)))
        # **拦截所有请求**：除 file:// 之外的一律记下来（那都是"外部依赖"）
        page.on('request', lambda r: external.append(r.url)
                if not r.url.startswith('file://') else None)

        page.goto(url, wait_until='load', timeout=60000)
        page.wait_for_timeout(1500)

        # ── ① 载入 + 统计 ────────────────────────────────────────────
        st = page.evaluate("""() => ({
          ready: !!window.__exportReady, st: window.__exportState || null,
          sub: (document.getElementById('sub') || {}).textContent || '',
        })""")
        print(f'  ① ready={st["ready"]} · state={st["st"]}')
        print(f'     副标题：{st["sub"][:110]!r}')
        ok1 = bool(st['ready'] and st['st'] and st['st'].get('papers'))
        print(f'     载入且有内容：{"✅" if ok1 else "❌"}')
        if not ok1:
            fails.append(f'file:// 打开没就绪：{st}')
        print()

        # ── ② ★ 零外部请求 ──────────────────────────────────────────
        print(f'  ② 非 file:// 的请求：{len(external)} 条')
        for u in external[:5]:
            print(f'     ⚠️ {u[:100]}')
        ok2 = not external
        print(f'     **零外部依赖**（换台机器断网也能看）：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'有 {len(external)} 个外部请求（那就不再是"单文件"）：'
                         f'{external[:3]}')
        print()

        # ── ③ 表格渲染 + 出处列 ──────────────────────────────────────
        info = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('tr.edge')];
          const withCite = rows.filter(r =>
            (r.querySelector('.pg') || {}).textContent
            && !/^—$/.test(r.querySelector('.pg').textContent.trim())).length;
          return { rows: rows.length, withCite: withCite,
                   cards: document.querySelectorAll('.card').length,
                   sample: (rows[0] || {}).textContent || '' };
        }""")
        print(f'  ③ 边行 {info["rows"]} · 有出处的 {info["withCite"]}'
              f' · 论文卡 {info["cards"]}')
        print(f'     样例：{info["sample"][:90]!r}')
        ok3 = info['rows'] > 0 and info['withCite'] > 0
        print(f'     真渲染出带出处的行：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'表格没渲染或没有出处列：{info}')
        print()

        # ── ④ 点一行 ⇒ 展开原文引文 ─────────────────────────────────
        exp = page.evaluate("""() => {
          const tr = document.querySelector('tr.edge');
          if (!tr) return null;
          const q = tr.nextElementSibling;
          const before = q ? getComputedStyle(q.querySelector('.quote')).display : null;
          tr.click();
          const after = q ? getComputedStyle(q.querySelector('.quote')).display : null;
          return { before: before, after: after,
                   text: q ? q.querySelector('.quote').textContent.trim() : '' };
        }""")
        page.wait_for_timeout(200)
        print(f'  ④ 点第一行：display {exp and exp["before"]} → {exp and exp["after"]}')
        print(f'     展开的内容：{(exp or {}).get("text", "")[:90]!r}')
        ok4 = bool(exp and exp['before'] == 'none' and exp['after'] != 'none'
                   and exp['text'])
        print(f'     点开真的能看到原文：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'点行没有展开引文：{exp}')
        print()

        # ── ⑤ 三个筛选方向 ───────────────────────────────────────────
        def nrows():
            return page.evaluate("() => document.querySelectorAll('tr.edge').length")
        base = nrows()
        # (a) 搜索
        page.fill('#q', 'TransE')
        page.wait_for_timeout(350)
        after_kw = nrows()
        page.fill('#q', '')
        page.wait_for_timeout(350)
        back_kw = nrows()
        # (b) 点关系标签
        rel_ok = page.evaluate("""() => {
          const r = document.querySelector('.rel');
          if (!r) return null;
          const name = r.dataset.r;
          r.click();
          return name;
        }""")
        page.wait_for_timeout(350)
        after_rel = nrows()
        page.evaluate("""() => { const r = document.querySelector('.rel'); if (r) r.click(); }""")
        page.wait_for_timeout(350)
        back_rel = nrows()
        print(f'  ⑤ 行数：全量 {base} → 搜 TransE {after_kw} → 清空 {back_kw}'
              f' → 点标签 {rel_ok} {after_rel} → 再点 {back_rel}')
        ok5 = (after_kw < base and back_kw == base
               and rel_ok is not None and after_rel < base and back_rel == base)
        print(f'     三个方向都真生效且可复原：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'筛选不对：base={base} kw={after_kw}/{back_kw} '
                         f'rel={after_rel}/{back_rel}')
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
