"""重新计算标注者间一致性：用 krippendorff 官方包 + 正确的测度级别。

为什么必须重算：
  1. `krippendorff.alpha()` 默认 `level_of_measurement="interval"`，
     而**关系类型是无序名义变量**，必须显式传 `nominal`。
  2. 本脚本 v1 曾把 (N_units, N_annotators) 的矩阵传给 `value_counts`，
     而该参数期望 (N_units, N_values) —— 维度语义完全不同，
     官方包会静默算出全 `nan`。正确入口是 `reliability_data`，
     形状 (M_coders, N_units)，缺失值用 `np.nan`。

口径决定（关键，直接影响数字）：
  标注者被要求"列出本文的全部实体/关系"，因此对任意候选单元，
  未列出 == 明确判定不属于。**填 0 是正确口径，不是缺失**。
  脚本同时跑 `nan` 口径作为对照，以展示两种口径的差异。

用法：
  python tools/recompute_agreement.py                       # 两篇都算
  python tools/recompute_agreement.py --paper 2401.04507v1
"""
import argparse
import itertools
import json
import re
from pathlib import Path

GOLD_DIR = Path(r"D:\kg-demo\data\gold")


def parse_args():
    ap = argparse.ArgumentParser(description="重算标注者间一致性")
    ap.add_argument("--paper", default=None, help="不指定则算全部")
    return ap.parse_args()


ARGS = parse_args()


def norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


def load_annotations(paper: str):
    """返回 {annotator: {"entities": set, "relations": set}}。"""
    prefix = "annotation2-" if paper.startswith("2401") else "annotation-"
    out = {}
    for f in sorted(GOLD_DIR.glob(f"{prefix}*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
            continue
        ann = d.get("annotator", f.stem)
        ents = {norm(e["name"]) for e in d.get("entities", []) if norm(e.get("name", ""))}
        rels = set()
        for r in d.get("relations", []):
            s, t = norm(r.get("source", "")), norm(r.get("target", ""))
            if s and t:
                rels.add((s, t, (r.get("type") or "").upper().strip()))
        out[ann] = {"entities": ents, "relations": rels, "file": f.name}
    return out


def build_matrix(units_by_annotator, fill):
    """构建 (M_annotators, N_units) 可靠性矩阵。

    fill=0.0  ：未列出记为 0 —— **本脚本主报的口径**（唯一可行，原因见
                `alpha_of` 的说明：缺失口径下决策空间退化为单类别）。
                操作性语义 = 「该标注者未采纳此候选」。
    fill=nan  ：未列出记为缺失 —— 保留作对照，但在这份数据上 α 不可定义。
    """
    import numpy as np

    annotators = list(units_by_annotator)
    all_units = set()
    for s in units_by_annotator.values():
        all_units |= set(s)
    all_units = sorted(all_units)
    M = np.full((len(annotators), len(all_units)), fill, dtype=float)
    for i, a in enumerate(annotators):
        for j, u in enumerate(all_units):
            if u in units_by_annotator[a]:
                M[i, j] = 1
    return M, annotators, all_units


def alpha_of(units_by_annotator, fill):
    """算 Krippendorff α（nominal）。

    ⚠️ **`fill=nan` 在这份数据上数学不可定义，不是"另一种口径"。**
    标注数据只记录了「谁列入了哪些候选项」，没有任何"明确拒绝"的记录；
    若把未列入记为缺失，矩阵的非缺失值就**全是 1**，决策空间退化为
    单类别 ⇒ 期望不一致度 $D_e = 0$ ⇒ α 的分母为 0。
    官方包的报错形式是 `RuntimeWarning: invalid value encountered in
    scalar divide` 后返回 `nan`，不是异常——很容易被误读成"算不出来但可以绕"。

    `value_domain=[0, 1]` 仍需显式给出：否则官方包会从数据推断值域，
    在只有 1 的情形下抛 `There has to be more than one value in the domain`。

    因此 **`fill=0.0` 是本项目唯一可行、也是主报的口径**。它的操作性语义是
    「该标注者未采纳此候选」——由标注指南要求逐条给出有原文依据的条目所支撑，
    与"必须逐项打勾的封闭式问卷"不同，这个语义必须在论文里写清。
    """
    import numpy as np
    import krippendorff

    M, _, _ = build_matrix(units_by_annotator, fill)
    with np.errstate(invalid="ignore"):
        try:
            a = krippendorff.alpha(
                reliability_data=M,
                level_of_measurement="nominal",
                value_domain=[0, 1],
            )
        except ValueError:
            return float("nan")
    return float(a)


def jaccard_pairs(units_by_annotator):
    names = list(units_by_annotator)
    vals = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = units_by_annotator[names[i]], units_by_annotator[names[j]]
            if not a and not b:
                continue
            vals.append(len(a & b) / len(a | b))
    return sum(vals) / len(vals) if vals else 0.0


def unit_support(units_by_annotator):
    """返回 {支持该单元的标注者数: 单元数}。"""
    names = list(units_by_annotator)
    all_units = set()
    for s in units_by_annotator.values():
        all_units |= set(s)
    dist = {}
    for u in all_units:
        k = sum(1 for a in names if u in units_by_annotator[a])
        dist[k] = dist.get(k, 0) + 1
    return dict(sorted(dist.items()))


def alias_aware_sets(units_by_annotator):
    """按抽取器同款 `_names_are_duplicates`（阈值 0.93）做全局贪心聚类。

    这是 `tools/merge_gold.py` 使用的口径：把 "Knowledge Graph" 与
    "Knowledge Graphs" 这类近名先并成一个簇，再算一致性与 α。
    结果是**候选单元数变少、一致性变高**，与字面归一的口径不可直接比较。

    实体单元是字符串；关系单元是 `(source, target, type)` 三元组，
    此时对两个端点分别聚类、谓词保持原样，聚类后可能产生重复单元，
    由 set 去重（这正是"两条不同写法的边其实是同一条"的情形）。

    注意：该聚类是顺序依赖的贪心算法（先到先占），
    因此本函数用确定性排序消除顺序抖动；这与 `merge_gold.py`
    按加载顺序聚类的实现有极小差异，差异已如实报告。
    """
    import sys

    sys.path.insert(0, r"D:\kg-demo\src")
    from kg_extractor import _names_are_duplicates

    # 1. 收集全部待聚类名字（实体会员 + 关系两端）
    names = set()
    for s in units_by_annotator.values():
        for u in s:
            if isinstance(u, tuple):
                names.add(u[0])
                names.add(u[1])
            else:
                names.add(u)

    # 2. 确定性贪心聚类
    clusters = []
    mapping = {}
    for nm in sorted(names):
        for i, canon in enumerate(clusters):
            if _names_are_duplicates(nm, canon):
                mapping[nm] = i
                break
        else:
            mapping[nm] = len(clusters)
            clusters.append(nm)

    # 3. 重写单元
    merged = {}
    for a, s in units_by_annotator.items():
        out = set()
        for u in s:
            if isinstance(u, tuple):
                out.add((mapping[u[0]], mapping[u[1]], u[2]))
            else:
                out.add(mapping[u])
        merged[a] = out
    return merged, len(clusters)


def run_paper(paper: str):
    anns = load_annotations(paper)
    if not anns:
        print(f"!! 没有找到 {paper} 的标注文件")
        return None

    print("=" * 72)
    print(f"=== 标注者间一致性：{paper} ===")
    print("=" * 72)
    print(f"标注者 {len(anns)} 人\n")
    print(f"  {'标注者':12s} {'实体':>6s} {'关系':>6s}  文件")
    for a, v in anns.items():
        print(f"  {a:12s} {len(v['entities']):6d} {len(v['relations']):6d}  {v['file']}")

    print()
    print("⚠️ 各标注者标注量不等 ⇒ Fleiss' κ（要求等量）与 Cohen's κ（仅 2 人）均不适用")
    print("   Krippendorff's α 是唯一规范选择\n")

    import krippendorff
    print(f"krippendorff 包版本: {getattr(krippendorff, '__version__', '?')}\n")

    report = {"paper": paper, "annotators": {}, "layers": {}}

    from collections import defaultdict
    layer_units = {"entities": {}, "relations": {}}
    for a, v in anns.items():
        layer_units["entities"][a] = v["entities"]
        layer_units["relations"][a] = v["relations"]

    for layer in ("entities", "relations"):
        sets = layer_units[layer]
        all_units = set()
        for s in sets.values():
            all_units |= set(s)

        print(f"── {layer} ──")
        print(f"   候选单元并集 {len(all_units)}")
        print(f"   支持度分布（被 k 个标注者同时列出）: {unit_support(sets)}")

        a_zero = alpha_of(sets, 0.0)
        a_nan = alpha_of(sets, float("nan"))
        multi = sum(v for k, v in unit_support(sets).items() if int(k) >= 2)
        print(f"   ★ α (nominal, 未列出=0)   = {a_zero:.4f}   ← 唯一可行口径（见 alpha_of 注释）")
        print(f"     α (nominal, 未列出=nan) = {a_nan:.4f}"
              f"   ← 数学上不可定义：非缺失值全为 1，决策空间退化为单类别")
        print(f"     被 ≥2 人标注过的单元: {multi} / {len(all_units)}")

        pj = jaccard_pairs(sets)
        print(f"     成对集合相似度（非 chance-corrected）  = {pj*100:.1f}%")

        # 留一法
        loo = []
        for drop in list(sets):
            sub = {a: s for a, s in sets.items() if a != drop}
            if len(sub) < 2:
                continue
            loo.append((drop, round(alpha_of(sub, 0.0), 4)))
        if loo:
            nums = [v for _, v in loo]
            print(f"     留一法 α: {loo}")
            print(f"               均值 {sum(nums)/len(nums):.4f}  "
                  f"极差 {max(nums)-min(nums):.4f}")

        # ── 别名感知口径对照（merge_gold.py 使用的口径）──
        merged_sets, n_clusters = alias_aware_sets(sets)
        a_alias = alpha_of(merged_sets, 0.0)
        pj_alias = jaccard_pairs(merged_sets)
        print(f"   ── 对照口径：先按 _names_are_duplicates(0.93) 聚类 ──")
        print(f"      簇数 {n_clusters}（字面口径为 {len(all_units)}）")
        print(f"      α (未列出=0)                 = {a_alias:.4f}")
        print(f"      成对集合相似度                = {pj_alias*100:.1f}%")

        report["layers"][layer] = {
            "unit_union": len(all_units),
            "units_multi_annotated": multi,
            "alpha_nominal_zero_filled": round(a_zero, 4),
            "alpha_nominal_nan_undefined": None if a_nan != a_nan else round(a_nan, 4),
            "pairwise_jaccard": round(pj, 4),
            "support_distribution": {str(k): v for k, v in unit_support(sets).items()},
            "leave_one_out": loo,
            "alias_aware": {
                "n_clusters": n_clusters,
                "alpha_nominal_zero_filled": round(a_alias, 4),
                "pairwise_jaccard": round(pj_alias, 4),
            },
        }
        print()

    for a, v in anns.items():
        report["annotators"][a] = {
            "entities": len(v["entities"]),
            "relations": len(v["relations"]),
            "file": v["file"],
        }

    out = GOLD_DIR / f"agreement-{paper}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写出: {out}\n")
    return report


def main():
    if ARGS.paper:
        run_paper(ARGS.paper)
        return
    for paper in ("2012.00456v1", "2401.04507v1"):
        run_paper(paper)


if __name__ == "__main__":
    main()
