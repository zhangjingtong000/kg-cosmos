# -*- coding: utf-8 -*-
r"""L18 · **PRISMA 流程图**（系统综述的筛选流程，自动生成）。

## TODO 的原话

> | **L18** | ⬜ **系统综述流程** |
> **Elicit**：PRISMA 流程图自动生成、双人独立筛选、**Cohen's kappa**、审计轨迹 |
> ⚠️ **注意**：Elicit 用 kappa 保证**筛选阶段**的标注一致性；
> **而我们的负结果正是"关系层标注的 kappa 会崩"**（α = −0.0119）。
> **两者不矛盾** —— 他们做的是"要不要收这篇"，我们做的是"这条关系对不对"。
> **这一对对照要写进论文** ★★

**⇒ 那个"对照"已经在 `docs/L19_UNRELIABILITY_ARGUMENT.md §五` 写完了。**
本脚本做的是 L18 里**还没做的那一半**：**PRISMA 流程图自动生成**。

## ★ 实测出来的流程（不是照抄 PRISMA 模板）

`web/kg-data/corpus-index.json` 里有**真实的检索式与入库状态**，
而"排除原因"数据里**没有** ⇒ 所以我去查了一遍：

```
清单 33 篇 · 入库 21 篇（2026-09-19 实测）
检索式 8 个（加中文语料后每个中文来源各算一条 —— 见数据里的 queries）
未入库的 12 篇：11 篇 **PDF 有（Y）、图没有（N）**；1 篇**已归档**
（PDF 与图都移入 `data/corpus/_archive-military/`）
corpus/*.pdf 共 33 个 · graphs/*.json 共 21 个
```

**⇒ 所以排除原因明确**：**"已下载但未抽取"**（Eligibility 阶段），
外加一类**"已归档"**（主动移走留档，**下载过** —— 不许写成"未下载"）。

## ⚠️ 三处**如实**（PRISMA 的模板套不上我们的流程）

1. **没有"初筛淘汰"这一步** —— 检索式命中的记录**全都下载了**
   （PRISMA 模板里的 `Records excluded` 在 Identification 后，
   而我们这里**没有** ⇒ 画成 0 或干脆不画，**不能编一个数**）；
2. **"为什么这些篇没抽取"数据里没有记录** ⇒ 图上如实写
   **"已下载未抽取（原因未逐篇记录）"**，不编原因；
3. **没有"双人独立筛选"** —— 那需要**人类标注者**，
   而本项目的关系层标注**四个模型标注者的 α 是负的**（−0.0119）。
   ⇒ **PRISMA 里那两个"人工筛选"框，我们画不出来** ——
   **但这恰好是 §五 那一对对照的入口**（见页面上的说明）。

用法：
    python tools/build_prisma_data.py
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / 'web' / 'kg-data' / 'corpus-index.json'
OUT = ROOT / 'web' / 'kg-data' / 'prisma.json'
CORPUS = ROOT / 'data' / 'corpus'
GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'


def main():
    t0 = time.time()
    print('=' * 92)
    print('L18 · PRISMA 流程图（自动生成）')
    print('=' * 92)
    if not IDX.exists():
        print(f'  ❌ 缺 {IDX}')
        return 1
    idx = json.loads(IDX.read_text(encoding='utf-8'))
    ps = idx.get('papers') or []
    total = len(ps)
    ing = [p for p in ps if p.get('in_graph')]
    not_ing = [p for p in ps if not p.get('in_graph')]
    # ★ 2026-09-19：**归档条目单独一类** —— 它的 PDF 与图都移进了
    #   `data/corpus/_archive-military/` ⇒ 只看"文件在不在 corpus 根"会把它
    #   误报成"未下载"。**它下载过**（按 `ARCHIVE_POLICY` 留档不删）。
    arch = {p['id'] for p in ps if p.get('archived')}

    # **从文件系统核实**（不只看清单里的标记）
    pdfs = {f.stem for f in CORPUS.glob('*.pdf')}
    graphs = {f.stem for f in GRAPHS.glob('*.json')}
    print(f'  清单 {total} 篇 · 入库 {len(ing)} 篇')
    print(f'  文件系统核实：corpus/*.pdf {len(pdfs)} 个 · graphs/*.json {len(graphs)} 个')

    # 检索式分布
    qs = Counter((p.get('query') or '(未记录)') for p in ps)
    queries = []
    for q, n in qs.most_common():
        hit = [p for p in ps if (p.get('query') or '(未记录)') == q]
        queries.append({'query': q, 'n': n,
                        'in_graph': sum(1 for x in hit if x.get('in_graph'))})
    print(f'  检索式 {len(queries)} 个')

    # 逐篇核实"排除原因"
    reasons = Counter()
    detail = []
    for p in not_ing:
        pid = p['id']
        has_pdf = pid in pdfs
        has_graph = pid in graphs
        if pid in arch:
            # **不是"没下载"，是"按政策归档"** —— 两者都是"没进图"，
            # 但一个是流程没走到，一个是**主动移走留档**，混成一个数就误导人。
            r = '已归档'
        elif has_pdf and not has_graph:
            r = '已下载未抽取'
        elif not has_pdf:
            r = '未下载'
        else:
            r = '状态不明'
        reasons[r] += 1
        detail.append({'id': pid, 'reason': r, 'year': p.get('year'),
                       'archived': pid in arch,
                       'title': (p.get('title') or '')[:110]})
    print(f'  排除 {len(not_ing)} 篇的原因分布：{dict(reasons)}')

    # PRISMA 四阶段（**按我们的真实流程，不是套模板**）
    stages = [
        {'key': 'identification', 'label': '检索（Identification）',
         'n': total, 'note': f'{len(queries)} 个检索式，命中并下载 {total} 篇'},
        {'key': 'screening', 'label': '初筛（Screening）',
         'n': total, 'note': f'本流程没有初筛淘汰：{len(queries)} 个检索式/来源命中的记录'
                             '全部下载'
                             '（PRISMA 模板里的 "Records excluded" 在这里是 0 —— '
                             '图上不编一个数）'},
        {'key': 'eligibility', 'label': '全文可抽取性评估（Eligibility）',
         'n': len(ing),
         'note': f'PDF 可解析且抽取成功 {len(ing)} 篇；'
                 f'排除 {len(not_ing)} 篇（{"、".join(reasons) or "无"}）'},
        {'key': 'included', 'label': '纳入（Included）',
         'n': len(ing), 'note': f'入库 {len(ing)} 篇，全部进入图谱'},
    ]
    excluded = [{'stage': 'eligibility', 'n': len(not_ing),
                 'reason': '、'.join(reasons) or '—',
                 # ⚠️ 这条会进页面（`esc()` 后当纯文本）⇒ **不许带 markdown 粗体**
                 #    （`tools/check_dom_text.py` 会把字面的 `**` 判红）。
                 'note': '未抽取的"为什么"没有逐篇记录 ⇒ 如实写"已下载未抽取"，不编理由；'
                         '"已归档"是按 ARCHIVE_POLICY 主动移走留档的（下载过，'
                         '不是"未下载"，见 data/corpus/_archive-military/README.md）',
                 'items': detail}]

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'L18：PRISMA 流程图（系统综述的筛选流程）',
        'design': {
            'not_a_template':
                '**没有照抄 PRISMA 模板** —— 本流程没有"初筛淘汰"'
                '（3 个检索式命中的记录全部下载了），所以那一框画成 0 并写明原因，'
                '**不编一个数**。',
            'exclusion_reason_from_filesystem':
                '排除原因不是从清单里读的标记，而是**去文件系统核实 + 读清单的 '
                '`archived` 标记**：'
                f'{reasons.get("已下载未抽取", 0)} 篇 PDF 有、图没有 ⇒ "已下载未抽取"；'
                f'{len(arch)} 篇 PDF 与图都已移入归档目录 ⇒ "已归档"'
                '（它**下载过** —— 按 `ARCHIVE_POLICY` 留档不删，'
                '**不能写成"未下载"**）。'
                '而**"为什么"没抽取数据里没记录** ⇒ 如实写"原因未逐篇记录"。',
            'no_dual_human_screening':
                '**没有"双人独立筛选"** —— 那需要人类标注者，'
                '而本项目关系层的四个模型标注者的 α 是负的（−0.0119）。'
                '⇒ PRISMA 里那两个"人工筛选"框我们画不出来，'
                '**但那正是与 Elicit 那一对对照的入口**（见页面）。',
        },
        'stages': stages,
        'excluded': excluded,
        'queries': queries,
        'caliber': idx.get('caliber'),
        'elapsed_s': round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print()
    for s in stages:
        print(f'    {s["n"]:>3}  {s["label"]}')
    print(f'    （排除 {len(not_ing)}：{dict(reasons)}）')
    print(f'\n  已写出 {OUT.relative_to(ROOT)}  '
          f'({OUT.stat().st_size / 1024:.1f} KB · {time.time() - t0:.1f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
