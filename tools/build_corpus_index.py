# -*- coding: utf-8 -*-
r"""生成论文索引 `web/kg-data/corpus-index.json`（K2 的数据前提）。

## 为什么需要它

`TODO.md` 的 K2「论文管理」被评审标了一个前提：
> 「**先定义"什么是一篇论文"** —— 三处数据无关联键」

**核实后发现：关联键其实存在，只是没人把它们串起来。**

    data/corpus/_manifest.json        33 条，含 {id, title, year, query, status}
    data/corpus/graphs/<id>.json      21 篇的图快照（文件名就是 id）
    web/kg-data/evidence-*.json       每条证据的 `src` = "<id>.pdf"

实测：**清单 33 条 ∩ 图快照 21 个 = 21**（完美对应，**0 个孤儿**），
另外 **12 篇只在清单里**（下载了但没入库，其中 1 篇已归档）—— **这个信息本身就有用**。

⇒ 所以 K2 不需要"新定义一篇论文"，只需要**把已有的三处串起来**。

## 产出什么

`web/kg-data/corpus-index.json`：

    {
      "generated_from": [...],
      "papers": [
        {"id", "title", "year", "query", "in_graph": bool, "archived": bool,
         "nodes", "edges", "top_types": {...}, "communities": [...]}
      ],
      "stats": {"total": 28, "in_graph": 17, "downloaded_only": 11}
    }

`communities` 是这篇论文的实体**出现在哪些社区**（用于"按论文筛图"）——
从 `evidence-*.json` 反查（那条链是：社区 → 实体 → 证据的 src）。

用法：
    python tools/build_corpus_index.py
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'data/corpus/_manifest.json'
GRAPHS = ROOT / 'data/corpus/graphs'
WEB = ROOT / 'web/kg-data'
OUT = WEB / 'corpus-index.json'


def main():
    if not MANIFEST.exists():
        raise SystemExit(f'找不到清单：{MANIFEST}')
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8-sig'))
    print(f'清单 {len(manifest)} 条')

    # ── ① 从 evidence-*.json 建"论文 → 社区"的倒排 ──────────────────────
    paper_comms = defaultdict(set)     # paper_id -> {社区号}
    paper_ents = defaultdict(int)      # paper_id -> 该篇贡献的实体-证据条数
    n_ev_files = 0
    for p in sorted(WEB.glob('evidence-*.json')):
        # 文件名解析用正则而不是 `split('-')[1]` —— 后者遇到没有编号的
        # `evidence-xxx.json` 会得到空串，然后 `int('')` 抛 ValueError。
        m_num = re.match(r'evidence-(\d+)$', p.stem)
        if not m_num:
            print(f'  · 跳过 {p.name}（不是 evidence-<数字>.json 形式）')
            continue
        ci = int(m_num.group(1))
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'  ⚠️ 跳过 {p.name}: {type(e).__name__}')
            continue
        n_ev_files += 1
        for _ent, evs in d.items():
            for e in (evs or []):
                src = str(e.get('src') or '')
                if not src:
                    continue
                pid = src[:-4] if src.lower().endswith('.pdf') else src
                paper_comms[pid].add(ci)
                paper_ents[pid] += 1
    print(f'从 {n_ev_files} 个 evidence-*.json 反查出 {len(paper_comms)} 篇论文的社区归属')

    # ── ② 逐篇组装 ──────────────────────────────────────────────────────
    papers = []
    for m in manifest:
        pid = m.get('id')
        g = GRAPHS / f'{pid}.json'
        rec = {
            'id': pid,
            'title': m.get('title') or '',
            'year': str(m.get('year') or ''),
            'query': m.get('query') or '',
            'in_graph': g.exists(),
            # ★ 2026-09-19：透传清单的**归档事实**（`archived` 是清单加的第五种状态）。
            #   为什么必须透传：下游 `build_prisma_data.py` 要把它与"未下载"分开报 ——
            #   归档过的论文**下载过**（PDF 移进了归档目录），
            #   只看"文件在不在 `data/corpus/` 根"就会把它写成"未下载"，那是不如实。
            'archived': bool(m.get('archived')),
            'communities': sorted(paper_comms.get(pid, set())),
        }
        if g.exists():
            try:
                d = json.loads(g.read_text(encoding='utf-8-sig'))
                edges = d.get('edges') or []
                rec['nodes'] = len(d.get('nodes') or [])
                rec['edges'] = len(edges)
                rec['top_types'] = dict(Counter(
                    str(e.get('type') or '?') for e in edges).most_common(5))
            except Exception as e:
                rec['error'] = f'{type(e).__name__}: {e}'
        papers.append(rec)

    # 清单里没有、但磁盘上有图快照的（防漏）
    ids = {m.get('id') for m in manifest}
    orphan = sorted(p.stem for p in GRAPHS.glob('*.json') if p.stem not in ids)
    if orphan:
        print(f'  ⚠️ 有 {len(orphan)} 个图快照不在清单里：{orphan}')

    in_graph = sum(1 for r in papers if r['in_graph'])
    payload = {
        'generated_from': [
            'data/corpus/_manifest.json',
            'data/corpus/graphs/*.json',
            'web/kg-data/evidence-*.json',
        ],
        'caliber': ('"一篇论文" = data/corpus/_manifest.json 的一条；'
                    'id 与 data/corpus/graphs/<id>.json 的文件名、'
                    '以及 evidence-*.json 里 src 去掉 .pdf 后一致。'
                    'in_graph=False 表示只下载未入库。'),
        'stats': {
            'total': len(papers),
            'in_graph': in_graph,
            'downloaded_only': len(papers) - in_graph,
        },
        'papers': papers,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'已写 {OUT.relative_to(ROOT)}（{OUT.stat().st_size / 1024:.1f} KB）')
    print()
    print('=' * 74)
    print(f'共 {len(papers)} 篇 · 已入库 {in_graph} · 只下载未入库 {len(papers) - in_graph}')
    print('=' * 74)
    print(f'{"id":<17}{"年":<6}{"入库":<6}{"节点":>6}{"边":>7}  社区  标题')
    for r in papers:
        print(f'{r["id"]:<17}{r["year"]:<6}'
              f'{"✅" if r["in_graph"] else "—":<6}'
              f'{r.get("nodes", ""):>6}{r.get("edges", ""):>7}'
              f'  {len(r["communities"]):>3}   {r["title"][:40]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
