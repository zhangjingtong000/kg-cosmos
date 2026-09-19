# -*- coding: utf-8 -*-
r"""首屏数字守门：**渲染出来的那个数字**必须等于权威产物。

## 为什么需要（这是 `check_doc_numbers.py` 管不到的一块）

`check_doc_numbers.py` 扫的是**文件里的字**。而 `web/index.html` 的首屏数字是
`data-count="20785"` 这样的**属性**，由 `lib/voyage.js` 在运行时：

1. 读 `IntersectionObserver`，滚到那一屏时用 `countUp()` 动画写成
   `Math.round(to).toLocaleString()`；
2. 另有一个 `fetch('./kg-data/nebula.json')` 把 `figNodes`/`figEdges`
   从 `nebula.json` **实测值**覆盖一遍（`setFig`）。

⇒ 于是有三个层次的"对"与"错"，**只有第三层是真对**：

| 层 | 例子 | 够不够 |
|---|---|---|
| ① HTML 属性对 | `data-count="20785"` | **不够** —— 动画或 `setFig` 可能把它改掉 |
| ② JS 用的产物对 | `nebula.json` 里是 20785 | **不够** —— 台词（`liveFacts` 的"评测全集 43867"）是另一处手写文案 |
| ③ **屏幕上真的显示** | 断言后的 `innerText` | ✅ 只有这一层是"评委看到的" |

`早先的内部记录` 第 6 条记的正是这个坑：`index.html` 曾硬编码 `18780`/`29120`，
后来改成同样写死的 `19427`/`30000` —— **守门脚本不查 HTML**，所以改了没人守。
本脚本就是那条缺口的补丁：**不查属性、只查渲染结果**。

## 断言五组

1. **主题词里的语料篇数**（首屏 eyebrow）与 `corpus-index.json` 的 `stats` 一致；
2. **`liveFacts` 的三段规模**与 `nebula.json` 的 `meta` 一致
   （实体 / 概览层关系 / 社区）；
3. **评测全集边数**（同一段台词里手写的那个数）与 `l5-six-layer.json` 的
   `edges_total` 一致 —— **这是最容易漏的一处：它是文案，不是 `data-count`**；
4. **`figNodes` / `figEdges` 渲染出来的数值**分别等于 `nebula.json` 的
   `meta.nodes` / `meta.edges`（**等动画结束再读**，不是读属性）；
5. **`figNodes` 的注释文案**「Louvain 归入 N 个社区」与 `meta.communities` 一致。

## 技术栈

**Playwright**（`tools/check_e2e_stack.py` 的判据：新增 E2E 一律 Playwright，
不许再手搓 CDP）—— 所以本文件刻意**不叫** `cdp_verify_*.py`。

用法（需要 8098 静态服务在跑，与 `verify_all.py` 同前置）：
    cd D:\kg-demo && python -m http.server 8098 --bind 127.0.0.1
    python tools/verify_firstscreen_numbers.py
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/index.html'


def _load():
    """读权威产物。**不抄文档里的数** —— 三者互相对账才有意义。"""
    n = json.loads((ROOT / 'web' / 'kg-data' / 'nebula.json')
                   .read_text(encoding='utf-8'))['meta']
    c = json.loads((ROOT / 'web' / 'kg-data' / 'corpus-index.json')
                   .read_text(encoding='utf-8'))['stats']
    six = json.loads((ROOT / 'data' / 'quality' / 'l5-six-layer.json')
                     .read_text(encoding='utf-8'))
    return n, c, six


def _text_num(s):
    """把页面上的 `20,785` / `20785` 读成 int；读不出返回 None。"""
    m = re.search(r'\d[\d,]*', s or '')
    if not m:
        return None
    return int(m.group(0).replace(',', ''))


def main():
    print('=' * 92)
    print('首屏数字守门（**渲染出来的数** vs 权威产物）')
    print('=' * 92)
    print(f'  页面 {PAGE}')

    meta, stats, six = _load()
    exp = {
        'nodes': meta['nodes'],
        'edges_nebula': meta['edges'],
        'communities': meta['communities'],
        'corpus_total': stats['total'],
        'corpus_in_graph': stats['in_graph'],
        'edges_eval': six['edges_total'],
    }
    print('  权威取值：')
    for k, v in exp.items():
        print(f'    {k:16s} {v}')
    print()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:                                  # pragma: no cover
        print(f'  ❌ 没有 playwright：{e}')
        return 1

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # `reduced_motion='reduce'` ⇒ `voyage.js` 走 `REDUCED` 分支，
        # **不走 countUp 动画**而是直接写终值。这不是"绕过"断言：
        # 终值仍由同一个 `data-count` / `setFig` 逻辑算出，
        # 而动画只是插值 —— 断言逐帧插值没有意义，断言终值才有。
        page = browser.new_page(viewport={'width': 1280, 'height': 900},
                                reduced_motion='reduce')
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))
        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1200)

        # ── ① eyebrow：语料篇数（含未下载）与入库数 ─────────────────
        eyebrow = page.inner_text('.eyebrow')
        print(f'  ① 首屏主题词：{eyebrow!r}')
        m = re.search(r'(\d+)\s*篇论文', eyebrow)
        if not m:
            fails.append(f'首屏主题词里找不到「N 篇论文」：{eyebrow!r}')
            print('     ❌ 找不到「N 篇论文」')
        elif int(m.group(1)) != exp['corpus_total']:
            fails.append(f'首屏主题词写「{m.group(1)} 篇论文」，'
                         f'corpus-index.json 的 stats.total 是 {exp["corpus_total"]}')
            print(f'     ❌ {m.group(1)} ≠ {exp["corpus_total"]}')
        else:
            print(f'     ✅ {m.group(1)} 篇论文 = corpus-index.json')
        m = re.search(r'入库\s*(\d+)\s*篇', eyebrow)
        if m and int(m.group(1)) != exp['corpus_in_graph']:
            fails.append(f'首屏主题词写「入库 {m.group(1)} 篇」，'
                         f'stats.in_graph 是 {exp["corpus_in_graph"]}')
            print(f'     ❌ 入库 {m.group(1)} ≠ {exp["corpus_in_graph"]}')
        elif m:
            print(f'     ✅ 入库 {m.group(1)} 篇 = stats.in_graph')

        # ── ② liveFacts：三段规模 ────────────────────────────────────
        # 它由 `fetch('./kg-data/nebula.json')` 之后 `lf.innerHTML = …` 写入，
        # 所以**必须等它出现** —— 等不到就是"运行期没接上产物"，
        # 那比数字写错更严重（界面会一直显示 HTML 里的死值）。
        facts = ''
        try:
            page.wait_for_function(
                "() => { const e = document.getElementById('liveFacts');"
                " return e && e.innerText.replace(/\\s/g,'').length > 10; }",
                timeout=15000)
            facts = page.inner_text('#liveFacts')
        except Exception:
            fails.append('首屏 liveFacts 始终为空 —— fetch(nebula.json) 后没写入，'
                         '页面在拿 HTML 里的死值显示')
            print('  ② ❌ liveFacts 为空（运行期没接上产物）')
        if facts:
            print(f'  ② liveFacts：{facts.replace(chr(10), " | ")!r}')
            # 结构是 `<span class="v">20,785</span><span class="l">实体 · 真实抽取</span>`，
            # innerText 里两个字串相邻 ⇒ 用"数字紧跟标签词"匹配。
            for key, label in (('nodes', '实体'), ('edges_nebula', '关系'),
                               ('communities', '社区')):
                want = exp[key]
                m = re.search(r'([\d,]+)\s*' + label, facts)
                got = _text_num(m.group(1)) if m else None
                if got is None:
                    fails.append(f'liveFacts 里读不到「{label}」的数字')
                    print(f'     ❌ 读不到 {label}')
                elif got != want:
                    fails.append(f'liveFacts 的「{label}」显示 {got}，权威产物是 {want}')
                    print(f'     ❌ {label} {got} ≠ {want}')
                else:
                    print(f'     ✅ {label} {got} = 产物')

            # ── ③ 评测全集边数：**文案里的手写数**（最容易漏的一处） ──
            m = re.search(r'评测全集\s*(\d[\d,]*)', facts)
            if not m:
                fails.append('liveFacts 里没有「评测全集 N」这一句 —— '
                             '口径说明被删掉了（30000 会被误读成全集）')
                print('     ❌ 缺「评测全集 N」')
            else:
                got = _text_num(m.group(1))
                if got != exp['edges_eval']:
                    fails.append(f'liveFacts 的「评测全集」显示 {got}，'
                                 f'l5-six-layer.json 的 edges_total 是 {exp["edges_eval"]}')
                    print(f'     ❌ 评测全集 {got} ≠ {exp["edges_eval"]}')
                else:
                    print(f'     ✅ 评测全集 {got} = l5-six-layer.json')

        # ── ④ figNodes / figEdges：**渲染出来的数值** ────────────────
        # 逐屏滚动到第 2 屏触发 IntersectionObserver，再等终值稳定。
        page.evaluate("document.getElementById('ch-1').scrollIntoView()")
        page.wait_for_timeout(600)
        for fid, key, label in (('figNodes', 'nodes', '实体（概览层）'),
                                ('figEdges', 'edges_nebula', '关系（概览层）')):
            want = exp[key]
            if not page.query_selector(f'#{fid}'):
                fails.append(f'首屏找不到 #{fid} —— 尺度那一屏的结构变了')
                print(f'  ④ ❌ 找不到 #{fid}')
                continue
            # **等渲染出来的终值**：`countUp()` 是 1400ms 的缓动动画，
            # 而 `setFig()` 又会在 `nebula.json` 到货后直接改终值。
            # 只等"等于期望值"会在超时后继续读 —— 那时读到的是错值，正是要报的。
            try:
                page.wait_for_function(
                    "([id, w]) => { const e = document.getElementById(id);"
                    " if (!e) return false;"
                    " const t = e.innerText.replace(/[^0-9]/g, '');"
                    " return t !== '' && parseInt(t, 10) === w; }",
                    arg=[fid, want], timeout=8000)
            except Exception:
                pass
            shown = page.inner_text(f'#{fid}')
            got = _text_num(shown)
            attr = page.get_attribute(f'#{fid}', 'data-count')
            print(f'  ④ {label}: 渲染 {shown!r}（data-count={attr!r}）')
            if got != want:
                fails.append(f'{fid} 渲染出 {shown!r}（{got}），权威产物是 {want}'
                             f'（data-count={attr!r} —— 属性对不代表渲染对）')
                print(f'     ❌ {got} ≠ {want}')
            else:
                print(f'     ✅ {got} = 产物')

        # ── ⑤ figNodes 的社区注释文案 ────────────────────────────────
        note = (page.inner_text('#ch-1 .grid .fig:nth-child(1) .d')
                if page.query_selector('#ch-1 .grid .fig:nth-child(1) .d') else '')
        m = re.search(r'(\d+)\s*个社区', note)
        print(f'  ⑤ 社区注释：{note!r}')
        if not m:
            fails.append(f'figNodes 的社区注释里读不到「N 个社区」：{note!r}')
            print('     ❌ 读不到社区数')
        elif int(m.group(1)) != exp['communities']:
            fails.append(f'社区注释写 {m.group(1)}，nebula.json 的 meta.communities '
                         f'是 {exp["communities"]}')
            print(f'     ❌ {m.group(1)} ≠ {exp["communities"]}')
        else:
            print(f'     ✅ {m.group(1)} 个社区 = nebula.json')

        if errs:
            fails.append(f'页面有 JS 报错：{errs[:2]}')
            print(f'  ❌ 页面 JS 报错：{errs[:2]}')
        browser.close()

    print()
    print('=' * 92)
    if fails:
        print(f'发现 {len(fails)} 处问题：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('✅ 首屏渲染出的数字与权威产物一致')
    return 0


if __name__ == '__main__':
    sys.exit(main())
