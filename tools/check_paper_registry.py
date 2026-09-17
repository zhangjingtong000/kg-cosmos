# -*- coding: utf-8 -*-
r"""N7 的验证：注册表的四个数字必须**与文件系统逐一对得上**。

## 为什么这条守门值得写

N7 的全部内容是**"把散在三处的真相连起来"** ——
**而"连起来"最容易出的错是：连出来的数字与文件系统不符。**
（本项目栽过：`check_artifacts_tracked` 三次误报、`check_doc_numbers` 三次假警报。）

⇒ 所以判据很直接：**注册表里的每一个计数，都去文件系统数一遍**。

## 断言五组

1. **四个身份的数字**：清单 28 / 已下载 28 / 已入库 17 / 上传图 3
   —— 前三个**独立数一遍**（不看注册表自己的 `counts`）；
2. **每个"已入库"的 id 都有对应的非空图文件**（**0 字节不算入库**）；
3. **★ 上传图的关联**：每条上传图的 `arxiv_id` 要么**严格匹配 arXiv 正则**，
   要么**如实为 None** —— **不许出现"猜出来的 id"**
   （这是本项最容易做假的地方）；
4. **失败的上传也在注册表里**（`upload_failures` 非空）
   —— 「我传过哪些」漏掉失败的那些，就是这一项的主要失败模式；
5. **gold 的重复文件确实已被归档**（`data/gold/` 下不再有 p1-15，
   而 `_archive/` 下有它 + README）。

用法：
    python tools/check_paper_registry.py
"""
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
RE_ARXIV = re.compile(r'^\d{4}\.\d{4,5}v\d+$')


def main():
    print('=' * 92)
    print('N7 · 论文注册表（四个身份的数字必须与文件系统对得上）')
    print('=' * 92)
    if not REG.exists():
        print(f'  ❌ 缺 {REG}（先跑 tools/build_paper_registry.py）')
        return 1
    d = json.loads(REG.read_text(encoding='utf-8'))
    c = d.get('counts') or {}
    fails = []

    # ── ① 四个数字：**独立数**，不信注册表自己 ──────────────────────
    mf = json.loads((ROOT / 'data' / 'corpus' / '_manifest.json')
                    .read_text(encoding='utf-8'))
    n_manifest = len(mf)
    n_pdf = len(list((ROOT / 'data' / 'corpus').glob('*.pdf')))
    n_graph = len([f for f in (ROOT / 'data' / 'corpus' / 'graphs').glob('*.json')
                   if f.stat().st_size > 0])
    n_upload = len(glob.glob(str(ROOT / 'data' / '*' / 'graph.json')))
    print(f'  ① 文件系统实数是：清单 {n_manifest} · PDF {n_pdf} · '
          f'非空图 {n_graph} · 上传图 {n_upload}')
    print(f'     注册表里写的是：{c}')
    ok1 = (c.get('manifest_total') == n_manifest
           and c.get('downloaded_pdf') == n_pdf
           and c.get('in_graph') == n_graph
           and c.get('uploaded_graphs') == n_upload)
    print(f'     **四个数字逐一对得上**：{"✅" if ok1 else "❌"}')
    if not ok1:
        fails.append(f'数字不符：注册表 {c} vs 文件系统 '
                     f'{n_manifest}/{n_pdf}/{n_graph}/{n_upload}')
    print()

    # ── ② 每个"已入库"都要有非空图 ──────────────────────────────
    bad2 = []
    for p in (d.get('papers') or []):
        if not p.get('in_graph'):
            continue
        f = ROOT / 'data' / 'corpus' / 'graphs' / f'{p["arxiv_id"]}.json'
        if not f.exists() or f.stat().st_size == 0:
            bad2.append(p['arxiv_id'])
    n_in = sum(1 for p in (d.get('papers') or []) if p.get('in_graph'))
    print(f'  ② 注册表里标"已入库"的 {n_in} 篇 ⇒ 图文件缺失或 0 字节的：{len(bad2)}')
    if bad2:
        print(f'     ⚠️ {bad2[:6]}')
    ok2 = not bad2
    print(f'     {"✅" if ok2 else "❌"}')
    if not ok2:
        fails.append(f'{len(bad2)} 篇标了已入库但没有非空图：{bad2[:5]}')
    print()

    # ── ③ ★ 上传图的关联：严格匹配或如实 None ─────────────────────
    ups = d.get('uploads') or []
    bad3 = []
    for u in ups:
        aid = u.get('arxiv_id')
        if aid is None:
            # **允许**：文件名不是 arXiv id（如 __probe_2.pdf）⇒ 如实 None
            # 但要确认**它真的不匹配**，而不是"代码漏了"
            raw = u.get('filename') or ''
            stem = raw[:-4] if raw.endswith('.pdf') else raw
            if RE_ARXIV.match(stem):
                bad3.append((u['gid'], raw, '本该匹配上却是 None'))
            continue
        if not RE_ARXIV.match(str(aid)):
            bad3.append((u['gid'], aid, '不是合法的 arXiv id'))
    print(f'  ③ 上传图 {len(ups)} 条 ⇒ 关联可疑的：{len(bad3)}')
    for g, a, why in bad3[:5]:
        print(f'     ⚠️ {g} · {a} · {why}')
    print(f'     **没有猜出来的 id**：{"✅" if not bad3 else "❌"}')
    if bad3:
        fails.append(f'上传图关联可疑：{bad3[:3]}')
    print()

    # ── ④ 上传的三个类别都记全了 ─────────────────────────────────
    fl = d.get('upload_failures') or []
    nt = d.get('upload_graphs_without_task') or []
    ui = d.get('upload_identity') or {}
    n_tasks = len(list((ROOT / 'data' / 'upload-tasks').glob('*.json')))
    print(f'  ④ 上传任务 {n_tasks} · 上传图 {len(ups)} · '
          f'失败/中断 {len(fl)} · 有图无任务 {len(nt)}')
    print(f'     三个类别：{ui.get("counts")}')
    # ⚠️ **判据不能是 `tasks - ups`** —— 第一版就是那么写的，于是
    #    8 - 3 = 5 ≠ 6 报红。**根因是"任务与图不是一一对应"**：
    #    `ad690dfb` 有图却没有任务 ⇒ 差的那 1 个。
    # ⇒ 正确判据是两条等式：
    #    ① **有任务+有图 + 有任务+无图 = 任务数**
    #    ② **有任务+有图 + 无任务+有图 = 图数**
    both = (ui.get('counts') or {}).get('with_task_and_graph')
    eq1 = (both is not None and both + len(fl) == n_tasks)
    eq2 = (both is not None and both + len(nt) == len(ups))
    print(f'     ① 成功 {both} + 失败 {len(fl)} = 任务 {n_tasks}：{eq1}')
    print(f'     ② 成功 {both} + 无任务 {len(nt)} = 图 {len(ups)}：{eq2}')
    ok4 = eq1 and eq2 and len(fl) > 0
    print(f'     **三个类别都记全（没有漏掉失败的那些）**：{"✅" if ok4 else "❌"}')
    if not ok4:
        fails.append(f'上传类别对不上：tasks={n_tasks} ups={len(ups)} '
                     f'fail={len(fl)} no_task={len(nt)} both={both}')
    print()

    # ── ⑤ gold 重复文件已归档 ────────────────────────────────────
    g = ROOT / 'data' / 'gold'
    live = list(g.glob('SOURCE-2401.04507v1-p1-15*'))
    arch = list((g / '_archive').glob('SOURCE-2401.04507v1-p1-15*')) \
        if (g / '_archive').exists() else []
    readme = (g / '_archive' / 'README.md').exists() if (g / '_archive').exists() else False
    # 再核实一次：它与 p1-10 **确实相同**
    same = None
    a10 = g / 'SOURCE-2401.04507v1-p1-10.txt'
    if a10.exists() and arch:
        same = (hashlib.sha256(a10.read_bytes()).hexdigest()
                == hashlib.sha256(arch[0].read_bytes()).hexdigest())
    print(f'  ⑤ data/gold 下的 p1-15：{len(live)} 个（应为 0）· '
          f'_archive 下：{len(arch)} 个 · README：{readme}')
    print(f'     归档的那份与 p1-10 **仍逐字节相同**：{same}')
    ok5 = (not live) and len(arch) == 1 and readme and same is True
    print(f'     **按 ARCHIVE_POLICY 归档（不删）**：{"✅" if ok5 else "❌"}')
    if not ok5:
        fails.append(f'gold 归档不对：live={len(live)} arch={len(arch)} '
                     f'readme={readme} same={same}')
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
