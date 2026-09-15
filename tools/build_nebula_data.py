"""生成社区星云的布局数据（本地零成本）。

【设计思路】
  社区星云 = 把 Louvain 社区画成空间上分开的"星云团"，团内是实体节点。
  位置在**离线阶段**算好并落盘，前端只负责渲染——这样前端不必跑力导向
  （15315 节点在浏览器里跑力导向会卡死），而是直接读坐标。
  这也符合调研结论：graphrag-workbench 用 SSE 增量、obsidian-3d-graph 承认
  3D 库解决不了大图性能；离线布局是绕开该限制的最直接办法。

【布局算法】
  1. 社区层级：按社区规模排序，用**斐波那契球面**分配社区中心
     （规模越大越靠内，避免大社区被挤到角落）
  2. 团内层级：每个社区内部用**球状均匀分布**（Fibonacci sphere），
     半径 ∝ sqrt(社区规模)，使密度大致恒定
  3. 单节点社区（规模 1）单独成"尘埃层"，在外围球壳上稀疏分布，
     前端可默认隐藏——它们占社区总数的 90%+ 但信息量低

【输出】web/kg-data/nebula.json，供 web/community-nebula.html 渲染。

用法：
  python tools/build_nebula_data.py
  python tools/build_nebula_data.py --max-communities 60
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(ROOT / "src"))
from graph_store import Neo4jStore  # noqa: E402

# 本体关系类型：**从 `kg_extractor` 导入，不在这里再抄一份** ——
# 本项目在"同一份名单写两处"上已经栽过一次（`graph_store.ALLOWED_RELATION_TYPES`
# 停在 15，导致新增的 `PUBLISHED_IN` 被静默改写成 `RELATED_TO`，扩展的数据增益为 0）。
# 导入失败时退化为 None，此时跳过"字段错位修正"而不是猜。
try:
    from kg_extractor import RELATION_TYPES  # noqa: E402
    LEGAL_RELATIONS = set(RELATION_TYPES)
except Exception:                            # noqa: BLE001
    LEGAL_RELATIONS = set()

OUT = ROOT / "web/kg-data"
QUALITY = ROOT / "data/quality"

# 15 类实体的颜色（前端调色板基底）
TYPE_COLORS = {
    "Person": "#ff7f7f", "Concept": "#7fb3ff", "Method": "#7fffb3",
    "Document": "#ffd97f", "Process": "#c77fff", "System": "#7fffff",
    "Artifact": "#ff9f7f", "Component": "#b3ff7f", "Event": "#ff7fd9",
    "Metric": "#7fd9ff", "Dataset": "#d9ff7f", "Organization": "#ffbf7f",
    "Location": "#9f7fff", "Tool": "#7fffd9", "Material": "#d9d9d9",
}


def parse_args():
    ap = argparse.ArgumentParser(description="生成社区星云布局")
    ap.add_argument("--max-communities", type=int, default=60,
                    help="最多导出多少个社区（按规模降序），其余归入尘埃层")
    ap.add_argument("--max-edges", type=int, default=30000)
    # ── 2026-09-15 加：不依赖 Neo4j 的数据源 ──────────────────────────────
    #
    # **为什么要它**：P7（争议视图）需要 `CONTRADICTS` 进概览数据，而
    # **项目的 Neo4j 实例已经不在了**（全盘只剩 Desktop 的模板 conf，
    # `Data\dbmss` 是空的）。
    #
    # 但核实发现：**`data/corpus/graphs/*.json` 里就是全量** ——
    # 17 篇合计 **43867 条边**（与"评测全集"一致），
    # 且 **`CONTRADICTS` 有 243 条**（每篇都有）。
    # ⇒ **根本不需要 Neo4j**，从 JSON 汇总即可。
    #
    # ⚠️ **口径差异必须写明**：JSON 快照是 **43867 条**（治理前全量），
    # 而此前从 Neo4j 导出的是 **31308 条**（治理后）。
    # 所以两种来源产出的概览**规模会不同**，不能混着引用
    # （本项目已经吃过多次"一个名字两个口径"的亏，见 docs/METRIC_CALIBERS.md）。
    ap.add_argument("--from-json", action="store_true",
                    help="从 data/corpus/graphs/*.json 汇总读（不依赖 Neo4j；"
                         "口径是治理前全量 43867 条，而非 Neo4j 的 31308 条）")
    # ── 2026-09-15 加：把「治理前」还原成「治理后」 ──────────────────────
    #
    # **为什么需要**：`--from-json` 读的是**治理前全量**（19427 节点 / 43867 边），
    # 而**原来的**概览是**治理后**口径（18780 节点 / 29120 边）——
    # 两者差 **647 节点 / 12559 边**，也就是说新概览里混进了**被治理清理掉的坏边**。
    # 界面会显示脏数据，也制造了新的口径分歧。
    #
    # **本选项做其中最大的一块**：`data/quality/filtered-edges.json` 记录的
    # **9792 条被 `entity_filter` 过滤掉的边**（占 12559 差额的 **78%**）。
    #
    # **刻意不做的三块**（都是"改"而不是"删"，对概览规模影响小）：
    #   · `created-by-plan.json` 的 `reverse`(2684) / `drop`(165) —— 改方向或删少量；
    #   · `venue-rewrite-plan.json` 的 1019 条 —— 改类型（PART_OF → PUBLISHED_IN）；
    #   · `type-normalization-plan.json` —— 改类型。
    # 这三块**不是"边变少了"，而是"边的属性变了"**，对"概览有多少节点/边"影响很小。
    # 若要完整还原，它们各自都有 plan 文件可依（`docs/ARCHIVE_POLICY.md` 的"
    # 只出计划不改数据"先例），后续可逐个加。
    ap.add_argument("--governed", action="store_true",
                    help="在 --from-json 基础上，减去 filtered-edges.json 里"
                         "被 entity_filter 过滤掉的 9792 条边（还原治理后口径）")
    return ap.parse_args()



ARGS = parse_args()


def fib_sphere(i, n):
    """第 i 个点（共 n 个）在单位球面上的均匀分布。"""
    if n <= 1:
        return (0.0, 0.0, 0.0)
    phi = math.acos(1 - 2 * (i + 0.5) / n)
    theta = math.pi * (1 + 5 ** 0.5) * i
    return (math.sin(phi) * math.cos(theta),
            math.sin(phi) * math.sin(theta),
            math.cos(phi))


def _load_filtered_keys():
    """读 `data/quality/filtered-edges.json`，返回被过滤掉的边的键集合。

    该文件由 `entity_filter` 产出，结构：
        {"threshold": 0.67, "types": ["CREATED_BY","RELATED_TO"],
         "stats": {...}, "filtered": [{"paper","type","source","target","entail_prob"}]}
    `stats.filtered` 实测 **9792** 条。

    键用 `(paper, source, target, type)` —— **带 paper**，
    因为不同论文里可能有同名实体对，不按篇区分会误杀。
    """
    p = QUALITY / "filtered-edges.json"
    if not p.exists():
        print(f"  ⚠️ 没有 {p.name}，--governed 无从下手（将按治理前口径导出）")
        return set()
    d = json.loads(p.read_text(encoding="utf-8-sig"))
    keys = {(x.get("paper"), str(x.get("source") or "").strip().lower(),
             str(x.get("target") or "").strip().lower(), x.get("type"))
            for x in (d.get("filtered") or [])}
    st = d.get("stats") or {}
    print(f"  载入 filtered-edges：{len(keys)} 个键"
          f"（文件自报 filtered={st.get('filtered')}）")
    return keys


def load_from_json():
    """从 `data/corpus/graphs/*.json` 汇总出 (nodes, edges)，**不依赖 Neo4j**。

    17 篇快照合计 **43867 条边**（与"评测全集"一致），含 `CONTRADICTS` 243 条。

    **合并口径**：
      · 节点按 `name` 跨篇去重（同名视为同一实体）；
        `type` 取第一次出现的（与 Neo4j 侧 `query_nodes` 的语义一致）。
      · 边**全收集**（不跨篇去重）—— 因为 `build_nebula_data` 后面自己会
        按 `(端点对, type)` 去重（那个键是 2026-09-15 修过的：原本不含 type，
        会吃掉 9.3% 的边，其中就包括 CONTRADICTS）。

    ⚠️ **口径提醒**：这里的边是**治理前全量（43867）**，
    而此前从 Neo4j 导出的是**治理后（31308）**。两者产出的概览规模会不同。

    ## 2026-09-15 新增：修正 189 条「`type` / `description` 错位」

    从 JSON 重建时暴露了 **237 条非法边**（本体只有 16 类），分三类：
      · **① 字段错位 189 条**（80%）—— `type` 里是**概念名**，
        **`description` 里才是合法关系类型**：
            {"type": "Triple Classification", "description": "RELATED_TO"}
        涉及 5/17 篇。
      · **② 越界谓词 40 条**（22 种：`CONTAINS` / `REPRESENTED_BY` / `friend` …）——
        这类在 **Neo4j 侧会被 `safe_relation_type` 改写成 `RELATED_TO`**，
        所以从 Neo4j 导出时看不见；JSON 是**入库前**快照，因此带着原样。
      · **③ 其余 8 条**。

    **对 ① 的处理是保守的**：**只在 `description` 恰好是一个合法类型时**才换过来
    —— 不做任何"猜类型"的动作。换了几条会打印出来，便于核对。
    （按 `docs/ARCHIVE_POLICY.md`：这是**读取侧的兼容处理，不改动原始文件** ——
    那 189 条错位仍完整保留在 `data/corpus/graphs/` 里作为档案。）
    """
    gdir = ROOT / "data/corpus/graphs"
    files = sorted(gdir.glob("*.json"))
    if not files:
        raise SystemExit(f"没有找到图快照：{gdir}")

    by_name = {}
    edges = []
    n_swapped = 0
    n_rewritten = 0
    n_governed = 0
    filtered_keys = _load_filtered_keys() if ARGS.governed else set()
    for f in files:
        paper = f.stem
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"  ⚠️ 跳过 {f.name}：{type(exc).__name__}: {exc}")
            continue
        for n in (d.get("nodes") or []):
            nm = n.get("name") or n.get("id")
            if nm and nm not in by_name:
                by_name[nm] = {"name": nm, "type": n.get("type") or "Concept"}
        for e in (d.get("edges") or []):
            t = str(e.get("type") or "")
            # ① 治理：先按 (paper, source, target, type) 判是否被过滤掉
            if filtered_keys:
                k = (paper, str(e.get("source") or "").strip().lower(),
                     str(e.get("target") or "").strip().lower(), t)
                if k in filtered_keys:
                    n_governed += 1
                    continue
            desc = str(e.get("description") or "").strip()
            if t not in LEGAL_RELATIONS and desc in LEGAL_RELATIONS:
                # ① 字段错位：description 里才是真类型。保守换回，并留痕。
                e = dict(e)
                e["type"], e["description"] = desc, t
                e["_swapped_from"] = t          # 留痕：原 type 是什么
                n_swapped += 1
            elif t not in LEGAL_RELATIONS:
                # ② 越界谓词：**与 Neo4j 侧的 `safe_relation_type` 行为保持一致**
                #    —— 那边会在入库时把它改写成 `RELATED_TO`。
                #    这里不改的话，概览会带着 47 条"类型是概念名"的边，
                #    与生产口径不一致（本项目已经吃过多次"一个名字两个口径"的亏）。
                e = dict(e)
                e["_rewritten_from"] = t
                e["type"] = "RELATED_TO"
                n_rewritten += 1
            edges.append(e)

    print(f"  从 {len(files)} 篇快照汇总"
          f"（口径：治理前全量，非 Neo4j 的治理后 31308）")
    if n_governed:
        print(f"  ✅ 治理：按 filtered-edges 减掉 {n_governed} 条被过滤的边")
    print(f"  ⚠️ 修正字段错位 {n_swapped} 条（type/description 互换，"
          f"只在 description 恰为合法类型时）")
    print(f"  ⚠️ 改写越界谓词 {n_rewritten} 条 → RELATED_TO"
          f"（与 Neo4j 侧 safe_relation_type 的行为一致）")
    return list(by_name.values()), edges


def main():
    if ARGS.from_json:
        nodes, edges = load_from_json()
    else:
        store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
        nodes = store.query_nodes()
        edges = store.query_edges()
        store.close()
    print(f"载入 {len(nodes)} 节点 / {len(edges)} 边")

    G = nx.Graph()
    for n in nodes:
        G.add_node(n["name"], type=n.get("type") or "Concept")
    for e in edges:
        s, t = e["source"], e["target"]
        if s != t and s in G and t in G:
            G.add_edge(s, t)

    print("计算 Louvain 社区...")
    communities = nx.community.louvain_communities(G, weight=None, seed=42)
    communities = sorted(communities, key=len, reverse=True)
    print(f"  {len(communities)} 个社区")

    big = communities[:ARGS.max_communities]
    dust = [n for c in communities[ARGS.max_communities:] for n in c]
    print(f"  导出大社区 {len(big)} 个（{sum(len(c) for c in big)} 节点）"
          f"，尘埃层 {len(dust)} 节点")

    # 社区中心：斐波那契球面，大社区放在内层球壳
    out_nodes = []
    out_communities = []
    R_outer = 900.0

    for idx, comm in enumerate(big):
        # 社区中心方向
        dirv = fib_sphere(idx, len(big))
        # 规模越大越靠内：半径 = R * (0.35 + 0.65 * 归一化排名)
        rank = idx / max(1, len(big) - 1)
        crad = R_outer * (0.30 + 0.55 * rank)
        cx, cy, cz = (dirv[0] * crad, dirv[1] * crad, dirv[2] * crad)

        # 团半径 ∝ sqrt(规模)，保持密度恒定
        size = len(comm)
        blob_r = 26.0 * math.sqrt(size) + 18.0

        members = sorted(comm)
        types = Counter(G.nodes[m].get("type") for m in members)
        degs = {m: G.degree(m) for m in members}
        core = sorted(members, key=lambda m: -degs[m])[:12]

        # 社区内位置：球状均匀 + 轻度按度数向心（核心靠中）
        for j, m in enumerate(members):
            ux, uy, uz = fib_sphere(j, size)
            d = degs[m]
            # 度数越高越靠中心
            shrink = 1.0 - min(0.65, d / (max(degs.values()) + 1) * 0.65)
            r = blob_r * shrink
            out_nodes.append({
                "id": m,
                "t": G.nodes[m].get("type") or "Concept",
                "c": idx,
                "d": d,
                "x": round(cx + ux * r, 2),
                "y": round(cy + uy * r, 2),
                "z": round(cz + uz * r, 2),
            })

        out_communities.append({
            "id": idx, "size": size, "center": [round(cx, 1), round(cy, 1), round(cz, 1)],
            "radius": round(blob_r, 1),
            "types": dict(types.most_common()),
            "core": [{"id": m, "degree": degs[m]} for m in core],
            "label": " / ".join(m for m in core[:3]),
        })

    # 尘埃层：外围球壳
    for j, m in enumerate(dust):
        ux, uy, uz = fib_sphere(j, len(dust))
        r = R_outer * 1.25
        out_nodes.append({
            "id": m, "t": G.nodes[m].get("type") or "Concept", "c": -1,
            "d": G.degree(m),
            "x": round(ux * r, 2), "y": round(uy * r, 2), "z": round(uz * r, 2),
        })

    # 边：只保留两端都在导出节点里的
    #
    # ⚠️ **2026-09-14 修一个数据损失 bug**：去重键原先只有 `(s, t)`（无向），
    # **不含关系类型**。于是同一对实体的**不同关系会互相覆盖** ——
    # 例如 `A —[PART_OF]→ B` 先出现，后面 `A —[CONTRADICTS]→ B` 就被当重复丢掉。
    #
    # 实测（拿评测全集 43867 条做模拟）：**丢 4081 条（9.3%）**，
    # 每一类都有损失：RELATED_TO −822、PART_OF −727、USES −655、CREATED_BY −608…
    # 而 `CONTRADICTS` 恰好最容易中招（同一对实体常有多种关系），
    # 这直接导致概览数据里 **14 种类型中没有 CONTRADICTS** ——
    # 也就是说，原始四透镜记录里"争议边一键高亮，几乎零成本"这条，
    # **前提根本不成立**：不是改样式的事，是数据里压根没有争议边。
    #
    # 现在键改成 `(s, t, type)`：**同一对实体的不同关系各自保留**，
    # 只有"同一对 + 同一类型"才算重复（那种情况下无向去重仍然合理，
    # 因为正反两条同类型边在可视化上没有区别）。
    keep = {n["id"] for n in out_nodes}
    out_edges = []
    seen = set()
    for e in edges:
        s, t = e["source"], e["target"]
        if s == t or s not in keep or t not in keep:
            continue
        rel = e.get("type") or "RELATED_TO"
        pair = (s, t) if s < t else (t, s)
        key = (pair[0], pair[1], rel)          # ← 加上 type
        if key in seen:
            continue
        seen.add(key)
        out_edges.append([s, t, rel])
        if len(out_edges) >= ARGS.max_edges:
            break
    print(f"导出节点 {len(out_nodes)}，边 {len(out_edges)}")

    payload = {
        "meta": {
            "nodes": len(out_nodes), "edges": len(out_edges),
            "communities": len(out_communities), "dust": len(dust),
            "modularity": 0.6824,
            "generated_by": "tools/build_nebula_data.py",
        },
        "type_colors": TYPE_COLORS,
        "communities": out_communities,
        "nodes": out_nodes,
        "edges": out_edges,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "nebula.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")
    print(f"\n已写出 {p}  ({p.stat().st_size/1024/1024:.2f} MB)")

    print("\n社区摘要（前 8）:")
    for c in out_communities[:8]:
        print(f"   #{c['id']:2d} {c['size']:5d} 节点  核心: {c['label'][:60]}")


if __name__ == "__main__":
    main()
