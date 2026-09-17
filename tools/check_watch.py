# -*- coding: utf-8 -*-
r"""M6 的守门：**"能拿到全文的才算数"** —— 三个信号必须分开报。

## 为什么这条守门值得写

M6 的第一次运行给出的是：

    49 篇新的

**而这个数字会误导人** —— 实测其中：

    能拿到全文的 15 篇（31%）
    拿不到全文的 34 篇   ← "知道有，但收不进来"（本项目要 PDF 才能抽取）

⇒ **"发现了 49 篇新论文"听起来很好，而对"扩充语料"这件事，只有 15 篇有用。**
**把三个信号混成一个数，就会让人以为"扩语料很容易"** ——
而那与 M8 的实测（**36 分钟/篇，100 篇约 60 小时**）**严重不符**。

## 断言五组

1. **产物在**且**三个计数都有**（`new_total` / `obtainable` / `not_obtainable`）；
2. **★ 三个数是自洽的**：`obtainable + not_obtainable == new_total`；
3. **★ `obtainable <= new_total`** 且**逐条核对**：
   每条 `obtainable=True` 的 work 必须**真的有 arXiv 版或 OA 的 pdf**
   （**不是脚本说了算，而是从它自己的两个信号里重算一遍**）；
4. **改写过的检索式也记在报告里** —— 因为 `_manifest.json` 里是 **arXiv 语法**，
   `title.search` 不认 ⇒ **必须能看出"我拿哪个串去查的"**；
5. **页面/文档里写清"这不是推送"** —— 本机无常驻服务，
   **它不能提醒你，除非有人去跑它**。

用法：
    python tools/check_watch.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / 'data' / 'quality' / 'watch-report.json'
STATE = ROOT / 'data' / 'quality' / 'watch-state.json'


def main():
    print('=' * 92)
    print('M6 · 检查新论文（**能拿到全文的才算数**）')
    print('=' * 92)
    if not REPORT.exists():
        print(f'  ❌ 缺 {REPORT}（先跑 tools/watch_openalex.py）')
        return 1
    d = json.loads(REPORT.read_text(encoding='utf-8'))
    c = d.get('counts') or {}
    fails = []

    # ── ① 三个计数都在 ───────────────────────────────────────────
    keys = ('new_total', 'obtainable', 'not_obtainable')
    print(f'  ① counts = {c}')
    ok1 = all(c.get(k) is not None for k in keys)
    print(f'     三个计数都在：{"✅" if ok1 else "❌"}')
    if not ok1:
        fails.append(f'缺计数：{c}')
    print()

    # ── ② 自洽 ───────────────────────────────────────────────────
    s = (c.get('obtainable') or 0) + (c.get('not_obtainable') or 0)
    print(f'  ② obtainable {c.get("obtainable")} + not_obtainable '
          f'{c.get("not_obtainable")} = {s}（应等于 new_total {c.get("new_total")}）')
    ok2 = s == c.get('new_total')
    print(f'     {"✅" if ok2 else "❌"}')
    if not ok2:
        fails.append(f'三个数不自洽：{c}')
    print()

    # ── ③★ 逐条重算 obtainable ───────────────────────────────────
    news = d.get('new') or []
    bad3 = []
    for w in news:
        # **重新算一遍**（不看 `obtainable` 字段，只看它的两个原始信号）
        want = bool(w.get('has_arxiv') or w.get('has_oa_pdf'))
        if bool(w.get('obtainable')) != want:
            bad3.append((w.get('openalex_id'), w.get('obtainable'), want))
    n_ok = sum(1 for w in news if w.get('obtainable'))
    print(f'  ③ 逐条重算 {len(news)} 篇 ⇒ 不符 {len(bad3)}')
    print(f'     标 obtainable 的 {n_ok} 篇（counts 说 {c.get("obtainable")}）')
    for x in bad3[:4]:
        print(f'     ⚠️ {x}')
    ok3 = (not bad3) and n_ok == c.get('obtainable')
    print(f'     **obtainable 是从 has_arxiv/has_oa_pdf 算出来的**：'
          f'{"✅" if ok3 else "❌"}')
    if not ok3:
        fails.append(f'obtainable 与原始信号不符：{bad3[:3]}（标了 {n_ok} 篇）')
    print()

    # ── ④ 改写过的检索式也记着 ───────────────────────────────────
    pq = d.get('per_query') or []
    print(f'  ④ 检索式 {len(pq)} 条')
    for x in pq[:3]:
        print(f'     {str(x.get("query"))[:44]:<46} → title.search:'
              f'{str(x.get("openalex_query"))[:30]}')
    ok4 = pq and all(x.get('openalex_query') for x in pq)
    print(f'     **能看出"拿哪个串去查的"**：{"✅" if ok4 else "❌"}')
    if not ok4:
        fails.append('per_query 里缺 openalex_query（改写过就查不出差异）')
    print()

    # ── ⑤ "不是推送"写在产物里 ───────────────────────────────────
    note = str((d.get('design') or {}).get('not_a_push_service') or '')
    print(f'  ⑤ 产物里的说明：{note[:90]!r}')
    ok5 = ('不能' in note or '不是' in note) and ('本地' in note or '本机' in note)
    print(f'     **写清"这不是推送服务"**：{"✅" if ok5 else "❌"}')
    if not ok5:
        fails.append('产物里没说清"这不是推送"')
    print()

    # 参考（不进判定）
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding='utf-8'))
        print(f'  · 参考：state 的 last_checked = {st.get("last_checked")}')
    print(f'  · 参考：**拿到全文的比例 '
          f'{(c.get("obtainable") or 0) / max(1, c.get("new_total") or 1):.0%}**'
          f' —— 这个比例本身是有用结论（扩语料没那么容易）')
    print()

    print('=' * 92)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 五组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
