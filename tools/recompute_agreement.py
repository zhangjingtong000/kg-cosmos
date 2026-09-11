"""重新计算标注者间一致性：用 krippendorff 官方包 + 正确的测度级别。

为什么必须重算（子代理发现）：
  `krippendorff` 包的 `alpha()` 默认 `level_of_measurement="interval"`，
  而**关系类型是无序名义变量**，必须显式传 `nominal`。
  此前用自写实现得到的关系 α = -0.0119 / -0.1465 可能因此是错误的——
  interval 距离会惩罚"类型不同"，nominal 只关心"是否同类"。
  这是整个"评测层不可用"结论的基础，必须用官方实现复现确认。

集合值标注的正确处理（子代理已核实的两个关键点）：
  1. 用 `value_counts=[(N, V)]` 传"0/1 计数矩阵"，而不是把缺失当 0
  2. **缺失值必须用 `np.nan`，不能用 0** —— 填 0 等于声称"该标注者明确判定
     这个单元不属于该类别"，会系统性扭曲 α

用法：
  python tools/recompute_agreement.py
  python tools/recompute_agreement.py --paper 2401.04507v1
"""
import argparse
import json
import re
from pathlib import Path

GOLD_DIR = Path(r"D:\kg-demo\data\gold")


def parse_args():
    ap = argparse.ArgumentParser(description="重算标注者间一致性")
    ap.add_argument("--paper", default="2012.00456v1")
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


def build_count_matrix(units_by_annotator):
    """构建 (N_units, V_annotators) 的 0/1 计数矩阵。

    约定：单元被某标注者**收录**记 1；未收录记 **np.nan（缺失）**，不是 0。
    理由：标注者只被要求列出他判定的实体/关系，未列出不等于"明确判定不存在"。
    """
    import numpy as np

    annotators = list(units_by_annotator)
    all_units = set()
    for s in units_by_annotator.values():
        all_units |= set(s)
    all_units = sorted(all_units)
    M = np.full((len(all_units), len(annotators)), np.nan)
    for j, a in enumerate(annotators):
        for i, u in enumerate(all_units):
            if u in units_by_annotator[a]:
                M[i, j] = 1
    return M, annotators, all_units


def jaccard_pairs(units_by_annotator, limit=None):
    names = list(units_by_annotator)
    vals = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = units_by_annotator[names[i]], units_by_annotator[names[j]]
            if not a and not b:
                continue
            vals.append(len(a & b) / len(a | b))
    return sum(vals) / len(vals) if vals else 0.0


def main():
    paper = ARGS.paper
    anns = load_annotations(paper)
    if not anns:
        raise SystemExit(f"没有找到 {paper} 的标注文件")

    print(f"=== 重算标注者间一致性：{paper} ===")
    print(f"标注者 {len(anns)} 人\n")
    print(f"  {'标注者':12s} {'实体':>6s} {'关系':>6s}  文件")
    for a, v in anns.items():
        print(f"  {a:12s} {len(v['entities']):6d} {len(v['relations']):6d}  {v['file']}")

    print()
    print("⚠️ 各标注者标注量不等 ⇒ Fleiss' κ（要求等量）与 Cohen's κ（仅 2 人）均不适用")
    print("   Krippendorff's α 是唯一规范选择\n")

    try:
        import krippendorff
        print(f"krippendorff 包版本: {krippendorff.__version__ if hasattr(krippendorff,'__version__') else '?'}")
    except ImportError:
        raise SystemExit("未安装 krippendorff，请先 pip install krippendorff")

    import numpy as np

    report = {"paper": paper, "annotators": {}}

    for layer in ("entities", "relations"):
        sets = {a: v[layer] for a, v in anns.items()}
        M, names, units = build_count_matrix(sets)
        pj = jaccard_pairs(sets)

        print(f"── {layer} ──")
        print(f"   单元总数 {len(units)}   矩阵形状 {M.shape}（行=单元，列=标注者）")

        results = {}
        # 用二值名义 α：value_counts 形式
        try:
            a_bin = krippendorff.alpha(
                value_counts=M, level_of_measurement="nominal")
            results["alpha_binary_nominal"] = round(float(a_bin), 4)
            print(f"   α (二值名义, 缺失=nan)        = {a_bin:.4f}")
        except Exception as e:
            print(f"   α(二值) 失败: {type(e).__name__}: {str(e)[:100]}")

        # 对比：错误地填 0 会怎样
        M0 = np.nan_to_num(M, nan=0.0)
        try:
            a_zero = krippendorff.alpha(value_counts=M0, level_of_measurement="nominal")
            results["alpha_binary_nominal_zero_filled"] = round(float(a_zero), 4)
            print(f"   α (二值名义, 缺失填0)        = {a_zero:.4f}   ← 对照，展示填0的偏差")
        except Exception as e:
            print(f"   α(填0对照) 失败: {str(e)[:80]}")

        # 对比：用 interval 测度（错误做法）
        try:
            a_int = krippendorff.alpha(value_counts=M, level_of_measurement="interval")
            results["alpha_binary_interval"] = round(float(a_int), 4)
            print(f"   α (interval 测度, 错误用法)  = {a_int:.4f}   ← 若此前误用，说明原数字不可靠")
        except Exception as e:
            print(f"   α(interval对照) 失败: {str(e)[:80]}")

        results["pairwise_jaccard"] = round(pj, 4)
        print(f"   成对集合相似度（非 chance-corrected，不能称'一致率'） = {pj*100:.1f}%")
        print()
        report["annotators"][layer] = results

    # 逐对 α（留一法：看单个标注者对整体的影响）
    print("── 留一法：随机丢弃一个标注者后的 α 稳定性 ──")
    import itertools
    for layer in ("entities", "relations"):
        sets = {a: v[layer] for a, v in anns.items()}
        vals = []
        for drop in sets:
            sub = {a: s for a, s in sets.items() if a != drop}
            if len(sub) < 2:
                continue
            M, _, _ = build_count_matrix(sub)
            try:
                a = krippendorff.alpha(value_counts=M, level_of_measurement="nominal")
                vals.append((drop, round(float(a), 4)))
            except Exception:
                pass
        if vals:
            nums = [v for _, v in vals]
            print(f"   {layer:10s} 丢弃后 α: {vals}   均值 {sum(nums)/len(nums):.4f} "
                  f"极差 {max(nums)-min(nums):.4f}")
            report["annotators"][layer]["leave_one_out"] = vals

    out = GOLD_DIR / f"agreement-{paper}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {out}")


if __name__ == "__main__":
    main()
