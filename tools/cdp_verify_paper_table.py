# -*- coding: utf-8 -*-
r"""L17 的浏览器实测：论文对比表。

## 为什么必须用浏览器测

表格渲染、列开关、**导出 CSV** 全是前端。
**在 Python 里读源码证明不了它能跑** —— 本项目栽过：
`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，而页面绑的是 `#go` 的 click。

## 断言五组

1. **数据载入**（`__tableReady`）且**行数 = 论文数**、**默认列数 = 4**
   （默认只开前 4 列 —— 一次摊开 7 列看不出重点）；
2. **表格真的渲染出内容**：`td` 数量 > 0，且**至少有一格非空**
   —— 只断言"数据取到了"是不够的，**画不出来才是这一页真正的失败模式**；
3. **勾选/取消一列 ⇒ 表格列数真的变**（是开关，且双向）；
4. **★ 导出 CSV：内容与"屏幕上打开的那几列"一致**
   —— 这是 Elicit `Extract Data` 的同款动作，**也是这一页的交付物**；
   ⚠️ **不下载真的文件**（那要落盘），而是**读 `#csv` 点击后 `__lastExport`
   记录的字节数**，并**直接调页面里的 `toCSV()`** 比对表头列。
5. **空列会显示"（这一篇没有）"而不是空白**（若有空单元格）——
   **空白会让人以为是页面坏了**。

用法：
    python tools/cdp_verify_paper_table.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')
PAGE = BASE + '/paper-table.html'
DATA = os.path.join(ROOT, 'web', 'kg-data', 'paper-table.json')


def main():
    from playwright.sync_api import sync_playwright

    if not os.path.exists(DATA):
        print(f'  ❌ 缺 {DATA}（先跑 tools/build_paper_table.py）')
        return 1
    with open(DATA, encoding='utf-8') as f:
        d = json.load(f)
    n_papers = len(d.get('papers') or [])
    n_cols = len(d.get('columns') or [])

    print('=' * 88)
    print('L17 · 论文对比表的浏览器实测')
    print('=' * 88)
    print(f'  数据 {n_papers} 篇 × {n_cols} 列 · 默认开前 4 列')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2000)

        # ── ① 载入 + 行数/列数 ───────────────────────────────────────
        st = page.evaluate("""() => ({
          ready: !!window.__tableReady, st: window.__tableState || null,
          rows: document.querySelectorAll('table tbody tr').length,
          heads: document.querySelectorAll('table thead th').length,
          chips: document.querySelectorAll('#cols label.chip').length,
          sub: (document.getElementById('sub') || {}).textContent || '',
        })""")
        print(f'  ① ready={st["ready"]} · state={st["st"]}')
        print(f'     表体行 {st["rows"]} · 表头 {st["heads"]}（含"论文/年"两列）'
              f' · 勾选框 {st["chips"]}')
        ok1 = (st['ready'] and st['st'] and st['rows'] == n_papers
               and st['heads'] == 4 + 2 and st['chips'] == n_cols)
        print(f'     行/列/勾选框都对：{"✅" if ok1 else "❌"}')
        if not ok1:
            fails.append(f'载入或行数/列数不对：{st}')
        print()

        # ── ② 真的渲染出内容 ─────────────────────────────────────────
        cells = page.evaluate("""() => {
          const tds = [...document.querySelectorAll('table tbody td')];
          const nonEmpty = tds.filter(t => t.textContent.trim()
            && !t.classList.contains('pid') && t.textContent.trim() !== '—').length;
          return { tds: tds.length, nonEmpty: nonEmpty,
                   sample: (tds.find(t => !t.classList.contains('pid')
                            && t.textContent.trim()) || {}).textContent || '' };
        }""")
        print(f'  ② td 总数 {cells["tds"]} · 非空单元格 {cells["nonEmpty"]}')
        print(f'     样例：{cells["sample"][:90]!r}')
        ok2 = cells['tds'] > 0 and cells['nonEmpty'] > 0
        print(f'     真画出了内容：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'表格没渲染出内容：{cells}')
        print()

        # ── ③ 勾选一列 ⇒ 列数变（双向）────────────────────────────────
        before_h = page.evaluate("() => document.querySelectorAll('table thead th').length")
        page.evaluate("""() => {
          const lab = document.querySelector('#cols label.chip');
          const inp = lab.querySelector('input');
          inp.checked = false;
          inp.dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        page.wait_for_timeout(400)
        after_h = page.evaluate("() => document.querySelectorAll('table thead th').length")
        page.evaluate("""() => {
          const lab = document.querySelector('#cols label.chip');
          const inp = lab.querySelector('input');
          inp.checked = true;
          inp.dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        page.wait_for_timeout(400)
        back_h = page.evaluate("() => document.querySelectorAll('table thead th').length")
        print(f'  ③ 列数：{before_h} → 取消一列 {after_h} → 再勾回 {back_h}')
        ok3 = (after_h == before_h - 1 and back_h == before_h)
        print(f'     是双向开关：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'列开关不对：{before_h} → {after_h} → {back_h}')
        print()

        # ── ④ ★ 导出：内容与"屏幕上打开的列"一致 ─────────────────────
        exp = page.evaluate("""() => {
          document.getElementById('csv').click();
          const md = (typeof toMD === 'function') ? toMD() : '';
          const csv = (typeof toCSV === 'function') ? toCSV() : '';
          return { last: window.__lastExport || null,
                   csv_head: csv.split('\\n')[0] || '',
                   md_head: md.split('\\n')[0] || '',
                   n_lines: csv.split('\\n').length,
                   cols_open: (window.__tableState || {}).cols || [] };
        }""")
        print(f'  ④ 导出记录：{exp["last"]}')
        print(f'     CSV 表头：{exp["csv_head"][:110]}')
        print(f'     CSV 行数：{exp["n_lines"]}（应为 1 表头 + {n_papers} 篇）')
        # 判据：**表头里出现的列标签数 == 打开的列数**
        labels = [c['label'] for c in d['columns'] if c['key'] in exp['cols_open']]
        head_ok = all(lb in exp['csv_head'] for lb in labels)
        ok4 = (exp['last'] is not None and exp['n_lines'] == n_papers + 1
               and head_ok and len(exp['cols_open']) == 4)
        print(f'     导出内容与打开的 {len(exp["cols_open"])} 列一致：'
              f'{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'导出不一致：lines={exp["n_lines"]} '
                         f'cols={exp["cols_open"]} head={exp["csv_head"][:60]!r}')
        print()

        # ── ⑤ 空单元格不是空白 ───────────────────────────────────────
        empty = page.evaluate(
            "() => document.querySelectorAll('table tbody td.empty').length")
        blobs = page.evaluate(
            "() => [...document.querySelectorAll('table tbody td')].filter(t => "
            "!t.classList.contains('pid') && !t.textContent.trim()).length")
        print(f'  ⑤ 标了「（这一篇没有）」的格 {empty} · **真正空白的格 {blobs}**')
        ok5 = blobs == 0
        print(f'     没有会让人以为"页面坏了"的空白：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'有 {blobs} 个真正空白的单元格')
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
