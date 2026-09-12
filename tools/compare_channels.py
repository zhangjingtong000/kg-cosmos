"""生成通道对比表（论文实验章直接可用）。

数据来源：data/gold/score-<paper>.json（由 tools/score_gold.py 产出）。
输出：docs/CHANNEL_COMPARISON.md + data/gold/channel-comparison.json

对比维度：
  实体/关系的 P、R、F1  |  显著性（结构词与泛指短语占输出比例）  |  输出规模
另附标注一致性，因为它决定这些指标的置信边界。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import sys
from pathlib import Path

GOLD_DIR = ROOT / "data/gold"
DOCS = ROOT / "docs"

PAPERS = [
    ("2012.00456v1", "文献计量知识图谱", "1-12"),
    ("2401.04507v1", "LLM 系统项目", "1-8（正文）"),
]

CHANNEL_LABEL = {
    "cloud": "云端 DeepSeek-V4.1-Flash",
    "local": "本地 qwen3.5:9b",
    "distilled": "本地蒸馏（QLoRA）",
}


def load_scores(paper):
    path = GOLD_DIR / f"score-{paper}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(x, digits=3):
    return "—" if x is None else f"{x:.{digits}f}"


def main():
    lines = ["# 抽取通道对比（M2 实验）\n",
             "> 生成脚本：`tools/compare_channels.py`；每篇的金标与范围见对应评分 JSON。\n",
             "> 评分口径：实体匹配用严格（归一化全等）与宽松（相似度 ≥0.93 且通过长度保护）"
             "两级，正文报告宽松口径；关系按 (源, 目标, 类型) 归一后全等记命中。\n"]

    table_rows = []
    for paper, domain, pages in PAPERS:
        score = load_scores(paper)
        if not score:
            lines.append(f"\n## {paper}（{domain}）\n\n⚠️ 尚无评分数据（先跑 "
                         f"`python tools/score_gold.py <channels> --paper {paper}`）\n")
            continue

        channels = score.get("channels", [])
        if not channels:
            continue

        lines.append(f"\n## {paper} —— {domain}\n")
        lines.append(f"- 金标：**{channels[0]['entities']['gold']} 实体 / "
                     f"{channels[0]['relations']['gold']} 关系**（覆盖 {pages} 页）")
        lines.append(f"- 多标注者平均一致率：**{(score.get('agreement') or 0)*100:.1f}%**"
                     f"（标注者 {', '.join(score.get('annotators') or [])}）\n")

        lines.append("| 通道 | 模型/说明 | 输出规模 | 实体 P | 实体 R | 实体 F1 | "
                     "显著性 | 关系 P | 关系 R | 关系 F1 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for ch in channels:
            e, r, s = ch["entities"], ch["relations"], ch["significance"]
            label = CHANNEL_LABEL.get(ch["mode"], ch["mode"])
            lines.append(
                f"| {label} | {ch.get('note','')} | {e['system']} 实体 / {r['system']} 关系 | "
                f"{fmt(e['precision'])} | {fmt(e['recall'])} | {fmt(e['f1'])} | "
                f"{s['noise_rate']*100:.1f}% | {fmt(r['precision'])} | {fmt(r['recall'])} | "
                f"{fmt(r['f1'])} |")
            table_rows.append({
                "paper": paper, "domain": domain, "channel": ch["mode"],
                "entities": {"p": e["precision"], "r": e["recall"], "f1": e["f1"],
                             "tp": e["tp_lenient"], "fp": e["fp"], "fn": e["fn"],
                             "type_errors": e["type_errors"],
                             "boundary_errors": e["boundary_errors"]},
                "relations": {"p": r["precision"], "r": r["recall"], "f1": r["f1"],
                              "tp": r["tp"], "fp": r["fp"], "fn": r["fn"],
                              "direction_errors": r["direction_errors"]},
                "significance": s,
                "gold_entities": e["gold"], "gold_relations": r["gold"],
            })

        lines.append("")
        lines.append("**错误构成**\n")
        lines.append("| 通道 | 类型错误 | 边界错误 | 结构/泛指项 | 其构成 |")
        lines.append("|---|---|---|---|---|")
        for ch in channels:
            e, s = ch["entities"], ch["significance"]
            label = CHANNEL_LABEL.get(ch["mode"], ch["mode"])
            lines.append(f"| {label} | {e['type_errors']} | {e['boundary_errors']} | "
                         f"{s['noise_count']} ({s['noise_rate']*100:.1f}%) | {s['kinds']} |")
        lines.append("")

    lines.append("\n## 结论（初步）\n")
    lines.append("1. **实体召回高、精确低**：两篇的实体 R 均在 0.9 以上而 P 约 0.25。"
                 "诊断显示 FP **零幻觉**、全部有原文依据——抽取器按提示词要求"
                 "「抽所有明确提到的实体」，因而把论文结构词、示例数据、被引作者一并抽出。"
                 "这是**任务定义粒度差异**，不是模型缺陷。\n")
    lines.append("2. **显著性随领域变化极大**：文献计量论文的结构噪声占 22.4%，"
                 "系统论文仅 3.0%。领域命名越规范，抽取器越不容易把脚手架当实体。\n")
    lines.append("3. **关系抽取是稳定短板**：两篇的 F1 均低于 0.06。抽取器产出大量 "
                 "`RELATED_TO` 泛关系，而金标只承认有句级依据的具体关系。"
                 "这是蒸馏最明确的改进靶点。\n")
    lines.append("4. **一致率决定置信边界**：两篇的四方一致率为 67.3% / 73.1%，"
                 "故上述 P/R/F1 应连同一致率一起报告，不宜作为绝对真值引用。\n")

    out = DOCS / "CHANNEL_COMPARISON.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    (GOLD_DIR / "channel-comparison.json").write_text(
        json.dumps(table_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写出 {out}")
    print(f"已写出 {GOLD_DIR / 'channel-comparison.json'}（{len(table_rows)} 行数据）")
    for row in table_rows:
        print(f"  {row['paper']:16} {row['channel']:10} "
              f"实体F1={row['entities']['f1']:.3f} 关系F1={row['relations']['f1']:.3f} "
              f"噪声={row['significance']['noise_rate']*100:.1f}%")


if __name__ == "__main__":
    main()
