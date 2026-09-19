# -*- coding: utf-8 -*-
r"""统一的「取图」接口 —— 让所有下游脚本都能**不依赖 Neo4j** 拿到图。

## 为什么需要它（2026-09-15）

本项目的**整套概览数据**都从 Neo4j 导出：

    tools/build_nebula_data.py       → web/kg-data/nebula.json         （PathQA / 分析面板）
    tools/build_filament_data.py     → web/kg-data/filaments.json      （主视图星云）
    tools/build_community_details.py → web/kg-data/comm-*.json         （社区详情）
                                     + web/kg-data/community-index.json

而**那个 Neo4j 实例已经不在了** —— 全盘只剩 Neo4j Desktop 的模板 conf，
`Data\dbmss` 是空的，只有 `system` 库的 neostore。
⇒ **三个脚本一个都跑不起来**，整套概览数据无法重建。

**但数据没丢**：`data/corpus/graphs/*.json`（**21 篇**，合计 **45708** 条边）
就是全量快照，**含 `CONTRADICTS` 243 条**。
（原写「17 篇，合计 43867 条边」—— 那是只有 17 篇英文 arXiv 时的读数；
43867 现仍等于 `web/kg-data/bipartite.json` 的 `totals.edges`，即 arXiv 子集口径，
而 L3–L6 的分母用的是含 4 篇中文图在内的 45708。）

所以把「取图」抽成一个统一的接口，三个脚本共用 ——
**不在三个地方各写一遍同样的 JSON 读取 + 治理还原逻辑**
（本项目在"同一份逻辑写两处"上已经栽过：`graph_store.ALLOWED_RELATION_TYPES`
停在 15，导致 `PUBLISHED_IN` 被静默改写成 `RELATED_TO`）。

## 两种口径

    load_graph(governed=False)   # 治理前全量（19427 节点 / 43867 边）
    load_graph(governed=True)    # 减去 entity_filter 过滤掉的边（还原治理后）

并修正两类已知的数据缺陷（都会打印条数，便于核对）：
  · **字段错位 189 条** —— `type` 里是概念名、`description` 里才是真类型；
    只在 `description` 恰为合法类型时才换回（保守，不猜）；
  · **越界谓词 48 条** —— 改写成 `RELATED_TO`，与 Neo4j 侧
    `safe_relation_type` 的行为保持一致。

**按 `docs/ARCHIVE_POLICY.md`：这些都是读取侧的兼容处理，原始文件一字不动。**
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPHS = ROOT / 'data/corpus/graphs'
QUALITY = ROOT / 'data/quality'

# 本体关系类型：从 src/kg_extractor.py 导入，**不在多处各抄一份**
import sys
sys.path.insert(0, str(ROOT / 'src'))
try:
    from kg_extractor import RELATION_TYPES  # noqa: E402
    LEGAL_RELATIONS = set(RELATION_TYPES)
except Exception:                             # noqa: BLE001
    LEGAL_RELATIONS = set()


def _load_filtered_keys():
    """`data/quality/filtered-edges.json` → 被 entity_filter 过滤掉的边的键集合。

    键是 `(paper, source, target, type)` —— **带 paper**，
    因为不同论文里可能有同名实体对，不按篇区分会误杀。
    """
    p = QUALITY / 'filtered-edges.json'
    if not p.exists():
        print(f'  ⚠️ 没有 {p.name}，治理还原无从下手')
        return set()
    d = json.loads(p.read_text(encoding='utf-8-sig'))
    keys = {(x.get('paper'),
             str(x.get('source') or '').strip().lower(),
             str(x.get('target') or '').strip().lower(),
             x.get('type'))
            for x in (d.get('filtered') or [])}
    st = d.get('stats') or {}
    print(f'  载入 filtered-edges：{len(keys)} 个键'
          f'（文件自报 filtered={st.get("filtered")}）')
    return keys


def load_graph(governed=False, verbose=True):
    """返回 `(nodes, edges)`。

    `nodes`: `[{"name":..., "type":...}]` —— 按 name 跨篇去重。
    `edges`: `[{"source":..., "target":..., "type":..., "description":...,
                "evidence":[...]}]` —— 全收集（不去重；去重由下游各自决定，
                因为不同下游的去重键不同）。

    `governed=True` 时减去 `filtered-edges.json` 里被过滤掉的边。
    """
    if not GRAPHS.exists():
        raise SystemExit(f'没有找到图快照：{GRAPHS}')
    files = sorted(GRAPHS.glob('*.json'))
    if not files:
        raise SystemExit(f'{GRAPHS} 是空的')

    filtered = _load_filtered_keys() if governed else set()
    by_name = {}
    edges = []
    n_swapped = n_rewritten = n_governed = 0

    for f in files:
        paper = f.stem
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception as exc:
            print(f'  ⚠️ 跳过 {f.name}：{type(exc).__name__}: {exc}')
            continue
        for n in (d.get('nodes') or []):
            nm = n.get('name') or n.get('id')
            if nm and nm not in by_name:
                by_name[nm] = {'name': nm, 'type': n.get('type') or 'Concept'}
        for e in (d.get('edges') or []):
            t = str(e.get('type') or '')
            if filtered:
                k = (paper, str(e.get('source') or '').strip().lower(),
                     str(e.get('target') or '').strip().lower(), t)
                if k in filtered:
                    n_governed += 1
                    continue
            desc = str(e.get('description') or '').strip()
            if t not in LEGAL_RELATIONS and desc in LEGAL_RELATIONS:
                e = dict(e)
                e['type'], e['description'] = desc, t
                e['_swapped_from'] = t
                n_swapped += 1
            elif t not in LEGAL_RELATIONS:
                e = dict(e)
                e['_rewritten_from'] = t
                e['type'] = 'RELATED_TO'
                n_rewritten += 1
            # ★ K5（2026-09-16）：**记下这条边来自哪篇论文**。
            #
            # `paper` 本来就在这个循环里（`f.stem`），只是此前没人把它存下来 ⇒
            # 于是下游（社区内部图、概览图）**只知道边，不知道是谁贡献的**，
            # 而"只看某一篇贡献的部分"是自然需求（TODO K5）。
            #
            # **不改 `edges` 的既有字段**（下游按 `source/target/type` 取值）——
            # 只加一个内部字段 `_paper`，由各生成器在导出时聚合成并行的 `edge_papers`。
            #
            # ⚠️ **本文件与 `build_nebula_data.py` 的 `load_from_json` 是同源的**
            #    （两处都读 `data/corpus/graphs/*.json`、都做同一套字段修正）。
            #    **改动要同步** —— 否则概览图与社区内部图的论文归属会不一致，
            #    而那种不一致表现为"同一个筛选在两处结果不同"，很难查。
            e = dict(e)
            e['_paper'] = paper
            edges.append(e)

    if verbose:
        print(f'  图源：data/corpus/graphs/（{len(files)} 篇快照，'
              f'口径：{"治理后还原" if governed else "治理前全量"}）')
        if n_governed:
            print(f'  ✅ 治理：减掉 {n_governed} 条被 entity_filter 过滤的边')
        print(f'  ⚠️ 修正字段错位 {n_swapped} 条 · 改写越界谓词 {n_rewritten} 条')
        print(f'  ⇒ {len(by_name)} 节点 / {len(edges)} 边')
    return list(by_name.values()), edges


def type_counts(edges):
    """边类型计数（便于各脚本打日志）。"""
    return Counter(str(e.get('type') or '?') for e in edges)
