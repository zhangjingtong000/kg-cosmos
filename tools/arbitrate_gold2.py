"""第二篇（2401.04507v1）分歧项的逐条裁决。

依据 docs/GOLD_ARBITRATION_RULES.md：
  §2 排除：泛指短语、图表编号、统计符号、示例/虚构数据、仅出现在参考文献/URL/示例句
  §3 收录：论文自己命名的流程/方法步骤、对比基线、工具链、论文定义的产物
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
from pathlib import Path

GOLD_DIR = ROOT / "data/gold"
PAPER = "2401.04507v1"

# (name) -> (decision, clause, reason)
DECISIONS = {
    # ── §2 排除：域名列举（引文是"such as A, B, C"式的罗列，论文未逐一阐述）──
    "Medicine": ("reject", "§2-泛指", "域名列举，引文为 'domains of medicine and law' 示例式提及"),
    "Law": ("reject", "§2-泛指", "同上"),
    "Geographical Areas": ("reject", "§2-泛指", "域名列举"),
    "Transportation": ("reject", "§2-泛指", "域名列举"),
    "Literary Works": ("reject", "§2-泛指", "域名列举"),
    "Biology": ("reject", "§2-泛指", "域名列举"),
    "Natural Sciences": ("reject", "§2-泛指", "域名列举"),
    "Astronomical Objects": ("reject", "§2-泛指", "域名列举"),
    "Architecture": ("reject", "§2-泛指", "域名列举末尾项（引文跨行截断）"),
    # ── §2 排除：示例句 / URL / 数量罗列 ──
    "Beijing": ("reject", "§2-示例句", "引文为 'The capital of China is Beijing'，论文用来说明事实性知识，非研究对象"),
    "GitHub": ("reject", "§2-URL", "仅出现在代码链接 https://github.com/neukg/TechGPT-2.0 中"),
    "GPT Series": ("reject", "§2-泛指", "指代一族模型而非单一具名系统；具体模型（ChatGPT/Baichuan2 等）已单列"),
    "Medical RET Data": ("reject", "§2-数量罗列", "引文仅给实例数 '1.8W instances of medical RET data'，无正式命名"),
    "General Task and Dialogue Data": ("reject", "§2-数量罗列", "类别描述（'general task and dialogue data' corpus），无正式名"),
    "Title Expansion Summaries": ("reject", "§2-数量罗列", "引文为 '15W title expansion summaries' 式数量枚举"),
    "Abstract Abbreviation Titles": ("reject", "§2-数量罗列", "同上"),
    "Code Instruction Fine-Tuning Data": ("reject", "§2-数量罗列", "同上"),
    "Keyword Extraction Data": ("reject", "§2-数量罗列", "同上"),
    "Subject Area Identification Data": ("reject", "§2-数量罗列", "同上"),
    # ── §2 排除：泛称/未命名 ──
    "Springboard Machine": ("reject", "§2-泛指", "泛指硬件（'a springboard machine'），无型号或专名"),
    "Retrieval-Augmented Generation (RAG)": ("reject", "§2-泛指", "仅在未来工作列表中与其他技术并列提及，论文未阐述该方法"),
    "Loss": ("reject", "§2-统计量", "训练过程量（loss increased），非命名指标"),
    "Model Weights": ("reject", "§2-泛指", "泛指产物类别（'model weights'）"),
    "Checkpoint": ("reject", "§2-泛指", "泛指训练产物"),
    "Chinese Alpaca Command Format": ("reject", "§2-泛指", "描述性名称（'the same Chinese Alpaca command format as ...'），非论文自定名词"),
    # ── §3 收录：论文讨论的具体组件/模型/概念 ──
    "Encoder": ("accept", "§3", "论文讨论 Transformer 的 Encoder 组件"),
    "Decoder": ("accept", "§3", "同上（论文讨论 Decoder 网络）"),
    "Chinese LLaMA2": ("accept", "§3", "论文讨论的具体模型（HFL 发布）"),
    "Nested NER": ("accept", "§3", "论文讨论的具体任务形态（nested NER data）"),

    # ── 第二批：边界项（论文讨论的技术/产物/能力名）──
    "Long Text Processing": ("accept", "§3", "论文明确讨论的能力方向（'long text processing in the TechGPT-2'）"),
    "Multi-modality": ("accept", "§3", "论文列出的后续研究方向之一"),
    "Tool Calling": ("accept", "§3", "论文列出的后续研究方向之一"),
    "Unanswerable Query": ("accept", "§3", "论文讨论的模型能力（处理无法回答的问题）"),
    "Toxicity Test": ("accept", "§3", "论文明确规划的评测项（'toxicity tests ... will be conducted'）"),
    "Data Collection and Processing": ("accept", "§3", "论文承诺阐述的流程环节（'we will furnish comprehensive insights into data collection and processing'）"),
    "Manual Annotation": ("accept", "§3", "论文描述的具体流程（数据由研究者人工标注）"),
    "Training Efficiency": ("reject", "§2-统计量", "训练过程效率描述（5 台服务器效率低于 4 台），非命名指标"),
}


def main():
    path = GOLD_DIR / f"merged-{PAPER}.json"
    merged = json.loads(path.read_text(encoding="utf-8"))
    disputed = merged.get("disputed_entities", [])
    print(f"论文 {PAPER}: 待裁决分歧实体 {len(disputed)}")

    accepted, rejected, unknown = [], [], []
    for item in disputed:
        v = DECISIONS.get(item["name"])
        if v is None:
            unknown.append(item["name"])
            continue
        decision, clause, reason = v
        entry = dict(item, arbitration={"decision": decision, "clause": clause, "reason": reason})
        (accepted if decision == "accept" else rejected).append(entry)

    if unknown:
        print(f"[warn] 规则未覆盖: {unknown}")

    gold = dict(merged)
    existing = {(e["name"] or "").lower() for e in gold["entities"]}
    added = 0
    for e in accepted:
        if (e["name"] or "").lower() not in existing:
            gold["entities"].append({
                "name": e["name"], "type": e["type"], "page": e.get("page"),
                "quote": e.get("quote", ""), "votes": e.get("votes", 1),
                "voters": e.get("voters", []), "arbitrated": True,
                "clause": e["arbitration"]["clause"],
            })
            added += 1

    gold["arbitration"] = {
        "rules": "docs/GOLD_ARBITRATION_RULES.md",
        "disputed_total": len(disputed),
        "accepted": [e["name"] for e in accepted],
        "rejected": [e["name"] for e in rejected],
        "unresolved": unknown,
    }
    gold["stats"]["entity_final"] = len(gold["entities"])
    gold["stats"]["arbitrated_accepted"] = added
    gold["stats"]["arbitrated_rejected"] = len(rejected)
    gold["stats"]["relation_final"] = len(gold["relations"])
    gold["stats"]["relation_single_vote_dropped"] = len(gold.get("disputed_relations", []))

    out = GOLD_DIR / f"gold-{PAPER}.json"
    out.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n裁决：收录 {len(accepted)}（新增 {added}），排除 {len(rejected)}")
    print(f"最终金标：{len(gold['entities'])} 实体 / {len(gold['relations'])} 关系")
    print(f"收录: {[e['name'] for e in accepted]}")
    print(f"排除: {[e['name'] for e in rejected][:12]} ...")
    print(f"已写出 {out.name}")


if __name__ == "__main__":
    main()
