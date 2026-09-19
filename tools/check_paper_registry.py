# -*- coding: utf-8 -*-
r"""N7 的验证：注册表的四个数字必须**与文件系统逐一对得上**。

## 为什么这条守门值得写

N7 的全部内容是**"把散在三处的真相连起来"** ——
**而"连起来"最容易出的错是：连出来的数字与文件系统不符。**
（本项目栽过：`check_artifacts_tracked` 三次误报、`check_doc_numbers` 三次假警报。）

⇒ 所以判据很直接：**注册表里的每一个计数，都去文件系统数一遍**。

## 断言八组

1. **四个身份的数字**：清单 / 已下载 / 已入库 / 上传图
   —— 前三个**独立数一遍**（不看注册表自己的 `counts`）；
2. **每个"已入库"的 id 都有对应的非空图文件**（**0 字节不算入库**）；
3. **★ 上传图的关联**：每条上传图的 `arxiv_id` 要么**严格匹配 arXiv 正则**，
   要么**如实为 None** —— **不许出现"猜出来的 id"**
   （这是本项最容易做假的地方）；
4. **失败的上传也在注册表里**（`upload_failures` 非空）
   —— 「我传过哪些」漏掉失败的那些，就是这一项的主要失败模式；
5. **gold 的重复文件确实已被归档**（`data/gold/` 下不再有 p1-15，
   而 `_archive/` 下有它 + README）；
6. **★ `lang` 不漏、不空、取值合法、且与标题实际内容一致**（2026-09-19 加）——
   **用 `build_paper_registry.py` 的同一条规则独立复算**（标题含 CJK ⇒ zh），
   所以**手改注册表把某条写成错的 lang，这一组会当场报红**。
   同时盯三件事：归档条目也有合法 lang、zh/en 的**条数**与清单里独立数出来的相等、
   `counts.lang_zh/lang_en` 与逐行数出来的一致。

7. **★ 归档标签贴对了人**（2026-09-19 加）—— 前六组核对的是"**数字/取值对不对**"，
   而**数字自洽也可能贴错对象**。实测栽过这一次：`archived: true` 被贴在
   `jsjkx2025-kg-completion-llm`（主题中性、PDF 与 179 节点的图都还在
   `data/corpus/`）上，而真正该归档的 `jsjkx2025-kg-rag-qa`（军事情报问答，
   PDF 与图已移入 `_archive-military/`）**一条标记都没有** ⇒ 六组断言全绿。
   ⇒ 判据把**标签与文件落位对起来**：
   · 标了 `archived` ⇒ 它的 PDF **不该**在 `data/corpus/` 根目录、
     图**不该**留在 `data/corpus/graphs/`，且 `archived_date`/`archived_reason`/
     `archived_to` 非空、`archived_to` 是**真实存在的目录**且里面**确实有**这份 PDF；
   · 没标的 ⇒ 它的 PDF **应当**在 `data/corpus/` 根目录；
   · 注册表的 `archived`/`archived_to`/`archived_reason` 是**透传**的 ⇒
     手改清单却没重跑 `build_paper_registry.py`，这里当场报红。

8. **★ 身份一一对应：PDF 文件名集合 ⇔ 未归档 id 集合**（2026-09-19 加）——
   ①–⑥ 核对的是**数字与取值**，⑦ 核对的是**逐条的标签 vs 落位**；
   而**"数字相等"可以掩盖身份不一致**。实测栽过这一次：
   `data/corpus/jsjkx2025-kg-negative-sampling.pdf`（「融合知识图谱的负采样方法」）
   **真实躺在语料目录里，却不在任何清单上**（早前"找替代论文"把它下载了，
   但只登记了同时下载的 `jsjkx2025-kg-kbqa`）⇒ 33 = 33 这个等式**把它掩盖了**：
   论文管理页看不到它、注册表不数它、抽取也不会碰它，但它占着位置。
   ⇒ 判据是两条**集合相等**（不是数字相等）：
   · `data/corpus/*.pdf` 的**文件名集合** == 清单里**未归档**条目的 **id 集合**
     （两个方向都拦：磁盘上有却没登记 = 野文件；登记了却没 PDF = 幽灵条目）；
   · **已归档**条目的 id，其 PDF **不得**出现在 `data/corpus/` 根下
     （应当在它的 `archived_to` 目录里 —— 落位细节仍由第 ⑦ 组负责）。
   与第 ⑦ 组的**分工**：⑦ 只遍历**清单里已有的条目**，所以"磁盘上有、清单里没有"
   这一类它**永远看不见**（本次的错误正是这一类）；⑧ 不问条目，直接比**两个集合**。

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
    print('N7 · 论文注册表（四个身份的数字与文件系统对得上 · 身份一一对应）')
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

    # ── ⑥ ★ lang：不漏 · 不空 · 合法 · 与标题实际内容一致 ────────────
    #
    # **为什么这一组必须独立复算，而不能信注册表自己的 `lang`**：
    #   `lang` 是**派生字段**（标题 ⇒ 语言）。派生字段最典型的坏法是
    #   "手改一行、或改了判定规则却没重跑 build" ⇒ 于是注册表里躺着一条
    #   `lang: "zh"` 而标题是英文。**只看"取值是不是 zh/en"永远发现不了这种错。**
    # ⇒ 判据：用**与 `build_paper_registry.py` 完全相同的规则**（标题含 CJK ⇒ zh）
    #    在这里独立算一遍，逐行比对。
    RE_CJK = re.compile(r'[\u4e00-\u9fff]')
    rows = d.get('papers') or []
    # 清单里的标题（**判定 lang 的首选来源**；不在清单里的才退回注册表自己的 title）
    mf_title = {p.get('id'): (p.get('title') or '') for p in mf if p.get('id')}
    by_id = {p.get('arxiv_id'): p for p in rows}
    bad6 = []
    for p in rows:
        pid = p.get('arxiv_id')
        lg = p.get('lang')
        if lg not in ('zh', 'en'):
            # **空 / 缺字段 / 第三种取值** 都在这里被抓住
            bad6.append((pid, lg, '缺失或取值不在 {zh,en}'))
            continue
        t = mf_title.get(pid)
        if t is None:
            # 清单里没有的（上传的外部 PDF）⇒ 用它自己的标题判，规则不变
            t = p.get('title') or ''
        want = 'zh' if RE_CJK.search(str(t)) else 'en'
        if lg != want:
            bad6.append((pid, lg, f'应为 {want} · 标题 {str(t)[:34]!r}'))
    n_zh = sum(1 for p in rows if p.get('lang') == 'zh')
    n_en = sum(1 for p in rows if p.get('lang') == 'en')
    # **独立数一遍**：清单里含 CJK 标题的条数（不看注册表）
    mf_zh = sum(1 for t in mf_title.values() if RE_CJK.search(str(t)))
    mf_en = len(mf_title) - mf_zh
    # 归档条目**不是特例** —— 它也必须有合法 lang
    arch_ids = [p.get('id') for p in mf if p.get('archived')]
    arch_bad = [i for i in arch_ids if (by_id.get(i) or {}).get('lang') not in ('zh', 'en')]
    print(f'  ⑥ lang：zh {n_zh} · en {n_en} · '
          f'两值合计 {n_zh + n_en}/{len(rows)} 行 · 清单独立算出 zh {mf_zh}/en {mf_en}')
    print(f'     `counts` 里写的：lang_zh={c.get("lang_zh")} · lang_en={c.get("lang_en")}')
    print(f'     与标题不符的：{len(bad6)} {bad6[:3]}')
    print(f'     归档条目 {len(arch_ids)} 条 ⇒ 缺合法 lang 的：{len(arch_bad)} {arch_bad[:3]}')
    # **"不漏"的四种坏法都要拦**：
    #   ① 有行没有 lang（空/缺失/第三种值）；② 与标题不一致；
    #   ③ 有一类全空（`n_zh`/`n_en` 为 0 说明判定规则写坏了 —— 恒真或恒假）；
    #   ④ 条数与 counts、与清单独立算出的不符。
    ok6 = (not bad6 and not arch_bad
           and n_zh + n_en == len(rows)
           and n_zh > 0 and n_en > 0
           and n_zh == mf_zh and n_en == mf_en
           and c.get('lang_zh') == n_zh and c.get('lang_en') == n_en)
    print(f'     **lang 不漏不空、取值合法、与标题一致**：{"✅" if ok6 else "❌"}')
    if not ok6:
        fails.append(f'lang 不对：bad={bad6[:3]} arch_bad={arch_bad[:2]} '
                     f'zh={n_zh}/{mf_zh} en={n_en}/{mf_en} '
                     f'counts={c.get("lang_zh")}/{c.get("lang_en")}')
    print()

    # ── ⑦ ★ 归档标签**贴对了人**（标签 ⇔ PDF/图的落位）────────────────
    #
    # **为什么单开这一组**：①–⑥ 全是"数字 / 取值对不对"，而**数字自洽也可能贴错对象**。
    #   实测（2026-09-19）：`archived: true` 贴在 `jsjkx2025-kg-completion-llm` 上
    #   （中性主题，PDF 与 179 节点的图都还在 `data/corpus/`），而真正该归档的
    #   `jsjkx2025-kg-rag-qa`（军事情报问答，PDF 与图已移入 `_archive-military/`）
    #   **一条标记都没有** ⇒ 前六组全绿，错误却躺在清单里。
    # ⇒ 判据：**把标签与文件落位对起来**（这一条能精确抓出上面那种错）。
    CORPUS = ROOT / 'data' / 'corpus'
    mf_by_id = {m.get('id'): m for m in mf if m.get('id')}
    bad7, n_arch7 = [], 0
    for pid, m in mf_by_id.items():
        is_arch = bool(m.get('archived'))
        has_pdf = (CORPUS / f'{pid}.pdf').exists()
        if not is_arch:
            # **逆向判据**：没标归档的，PDF 就该在在库目录里
            # （否则就是"幽灵条目"：清单挂着，产物却已归档 —— 本次的 rag-qa）
            if not has_pdf:
                bad7.append((pid, '未标 archived，但 data/corpus/ 根目录下没有它的 PDF'
                                  '（已被归档？那标记该贴上来）'))
            continue
        n_arch7 += 1
        # ① 归档 ⇒ PDF **不该**还在在库目录（本次错误正是这一条抓出来的）
        if has_pdf:
            bad7.append((pid, '标了 archived，但 PDF 仍在 data/corpus/ 根目录'
                              '（标签贴错了对象？）'))
        # ② 图同理：归档的产物应随归档目录走，不该留在在库目录
        if (CORPUS / 'graphs' / f'{pid}.json').exists():
            bad7.append((pid, '标了 archived，但图仍在 data/corpus/graphs/ 下'))
        # ③ ★ 元数据要撑得住这个标签：日期 / 原因 / 去向都不许空
        for k in ('archived_date', 'archived_reason', 'archived_to'):
            if not m.get(k):
                bad7.append((pid, f'标了 archived，但缺 {k}'))
        # ④ ★ `archived_to` 必须指向**真实存在的目录**，且里面**确实有**这份 PDF
        #    （"指向一个不存在的目录"是归档标记最常见的假账）
        to = str(m.get('archived_to') or '').strip()
        if to:
            d = ROOT / to.strip('/\\')
            if not d.is_dir():
                bad7.append((pid, f'archived_to 指向的不是目录：{to}'))
            elif not (d / f'{pid}.pdf').exists():
                bad7.append((pid, f'{to} 下没有 {pid}.pdf'))
    # ⑤ 注册表那三列是**透传**的 —— 改了清单却没重跑 build，这里当场报红
    for pid, p in by_id.items():
        m = mf_by_id.get(pid)
        if m is None:
            continue          # 上传的外部 PDF：清单里没有，不适用
        want = bool(m.get('archived'))
        if bool(p.get('archived')) != want:
            bad7.append((pid, f"注册表的 archived={p.get('archived')} ≠ 清单 {want}"
                              '（忘了重跑 tools/build_paper_registry.py？）'))
        elif want:
            for k in ('archived_to', 'archived_reason'):
                if (p.get(k) or None) != (m.get(k) or None):
                    bad7.append((pid, f'注册表 {k} 与清单不一致'))
    print(f'  ⑦ 清单里标 archived 的 {n_arch7} 条 · 注册表标 archived 的 '
          f'{sum(1 for p in rows if p.get("archived"))} 条 '
          f'⇒ 标签与 PDF/图落位不符的：{len(bad7)}')
    for pid, why in bad7[:6]:
        print(f'     ⚠️ {pid} · {why}')
    ok7 = not bad7
    print(f'     **归档标签贴对了人（标了的不在库 · 没标的真在库 · '
          f'archived_to 是真目录）**：{"✅" if ok7 else "❌"}')
    if not ok7:
        fails.append(f'归档标签与文件落位不符：{bad7[:4]}')
    print()

    # ── ⑧ ★ 身份一一对应：PDF 文件名集合 ⇔ 未归档 id 集合 ────────────
    #
    # **为什么"数字相等"还不够**：① 数的是"清单 33 条 / PDF 33 个" ——
    #   而**一个野文件 + 一个幽灵条目**能让这两个数**同时正确**。
    #   实测（2026-09-19）：`jsjkx2025-kg-negative-sampling.pdf` 真实在磁盘上、
    #   却不在清单里，而"清单 33 = PDF 33"把这个身份不一致**掩盖了**；
    #   ⑦ 也看不见它 —— **⑦ 只遍历清单里已有的条目，野文件不在它的视野里**。
    # ⇒ 判据换成**集合关系**（不问条数）：
    #   ① 磁盘 PDF 的文件名集合 == 清单里**未归档**条目的 id 集合（两个方向）；
    #   ② 已归档条目的 id，其 PDF **不得**在 `data/corpus/` 根下。
    pdf_stems = {f.stem for f in CORPUS.glob('*.pdf')}
    live_ids = {i for i, m in mf_by_id.items() if not m.get('archived')}
    arch_only = {i for i, m in mf_by_id.items() if m.get('archived')}
    two_way = sorted(pdf_stems ^ live_ids)          # 两个方向的不一致，合起来看
    # 分三类打印：成因不同 ⇒ 修法不同（不能让它们混成一个数字）
    stray_arch = sorted(i for i in arch_only if i in pdf_stems)      # 归档了却还在库
    wild = sorted(i for i in two_way if i in pdf_stems and i not in arch_only)  # 野文件
    ghost = sorted(i for i in two_way if i not in pdf_stems)         # 幽灵条目
    print(f'  ⑧ data/corpus 根下 PDF {len(pdf_stems)} 个 · '
          f'清单未归档 {len(live_ids)} 条 · 已归档 {len(arch_only)} 条')
    print(f'     磁盘上有却不在清单里（野文件）：{len(wild)} {wild[:4]}')
    print(f'     清单里未归档却无 PDF（幽灵条目）：{len(ghost)} {ghost[:4]}')
    print(f'     已归档但 PDF 仍在根下：{len(stray_arch)} {stray_arch[:4]}')
    ok8 = not wild and not ghost and not stray_arch
    print(f'     **PDF 文件名集合与未归档 id 集合完全相等**：{"✅" if ok8 else "❌"}')
    if not ok8:
        fails.append(f'身份不一一对应：野文件 {wild[:4]} · 幽灵条目 {ghost[:4]} · '
                     f'归档残留 {stray_arch[:4]}')
    print()

    print('=' * 92)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 八组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
