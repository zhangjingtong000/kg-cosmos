"""按 GOLD_ARBITRATION_RULES.md 机械裁决实体分歧项，生成最终金标。

规则来源：docs/GOLD_ARBITRATION_RULES.md
裁决依据：§2 明确排除（泛指/图表编号/统计符号/示例数据/仅出现在参考文献）
          §3 明确收录（论文自己命名的流程步骤、对比基线、工具链）
          §5 票数门槛（实体 ≥2 票采纳；单票项按 §1–§3 裁决）
"""
import json
from pathlib import Path

GOLD_DIR = Path(r"D:\kg-demo\data\gold")
PAPER = "2012.00456v1"

# 逐条裁决：收录(accept) 或 排除(reject)，附规则条款与理由
DECISIONS = {
    # ── §3 明确收录：论文自己命名的流程/方法步骤 ──
    "Five-Step Methodology": ("accept", "§3", "论文自己命名的方法流程（Human-in-the-Loop 五步）"),
    "Character Extraction": ("accept", "§3", "论文命名的流程步骤（与 Table/Reference Extraction 并列）"),
    "Fully Automated Approach": ("accept", "§3", "论文命名的方案名（与 Fully Manual 对比）"),
    "Fully Manual Approach": ("accept", "§3", "论文命名的方案名"),
    "Manual Selection Method": ("accept", "§3", "论文命名的选取方法"),
    # ── §3 明确收录：论文定义的产物 ──
    "Abstract Table Representation": ("accept", "§3", "论文定义的具名表格表示（Artifact）"),
    "Relational Schema": ("accept", "§3", "论文定义的具名数据模式；与 Relational Database 非同一实体"),
    "Selected Papers File": ("accept", "§3", "论文具名的工作产物文件（Artifact）"),
    "Settings File": ("accept", "§3", "论文具名的工作产物文件（Artifact）"),
    "Integrated Spreadsheet Editor": ("accept", "§3", "论文具名的系统组件"),
    # ── §2 明确排除：泛指短语（无具体所指） ──
    "Domain Expert": ("reject", "§2-泛指", "泛指角色，非命名对象"),
    "Research Problem": ("reject", "§2-泛指", "泛指概念"),
    "Research Question": ("reject", "§2-泛指", "泛指概念"),
    "Semi-Structured Data": ("reject", "§2-泛指", "泛指数据类别"),
    "Bibliographic Data": ("reject", "§2-泛指", "泛指数据类别"),
    "Semantic Data": ("reject", "§2-泛指", "泛指数据类别"),
    "Systematic Workflow": ("reject", "§2-泛指", "图注里的描述性短语"),
    "Scholarly Information Systems": ("reject", "§2-泛指", "泛指系统类别"),
    # ── §2 明确排除：示例/虚构数据、统计符号 ──
    "95% CI": ("reject", "§2-统计符号", "纯统计符号，非命名实体"),
    "Corrˆea": ("reject", "§2-示例数据", "连字残缺的参考文献作者名"),

    # ── 四方合并（A/B/C/GLM）新增分歧项的裁决 ──
    "Human-in-the-Loop Methodology": ("accept", "§3", "论文自己命名的方法论"),
    "Open Research Knowledge Graph Search Interface": ("accept", "§3", "论文具名的系统界面组件"),
    "R0 Estimates": ("reject", "§2-示例数据", "图 3 示例表格中的统计量"),
    "Wuhan": ("reject", "§2-示例数据", "图 3 示例表格中的示例地点"),
    "Hubei Province": ("reject", "§2-示例数据", "图 3 示例表格中的示例地名"),
    "Owl": ("reject", "§2-泛指", "泛指本体语言，未作为具名系统阐述"),
    "Web Ontology Language": ("reject", "§2-泛指", "Owl 的全称，同上"),
    "Selected Papers": ("reject", "§2-泛指", "与 Selected Papers File 重叠的泛指短语"),
    "RDF Triples": ("reject", "§2-泛指", "泛指数据表示形式"),
    "Python Script": ("reject", "§2-泛指", "泛指脚本，无具名"),
    "Domain Experts": ("reject", "§2-泛指", "泛指角色（Domain Expert 的复数形式）"),
    "Knowledge Graph Curation": ("reject", "§2-泛指", "泛指活动，非命名对象"),
    "Corrˆea": ("reject", "§2-示例数据", "连字残缺的作者名"),
    "John Doe": ("reject", "§2-示例数据", "图 2 中的虚构占位作者"),
    "Joseph et al.": ("reject", "§2-示例数据", "图 3 示例表格中的作者"),
    "Shen et al.": ("reject", "§2-示例数据", "图 3 示例表格中的作者"),
    "Liu et al.": ("reject", "§2-示例数据", "图 3 示例表格中的作者"),
}


def main():
    path = GOLD_DIR / f"merged-{PAPER}.json"
    gold = json.loads(path.read_text(encoding="utf-8"))

    disputed = gold.get("disputed_entities", [])
    print(f"待裁决分歧实体: {len(disputed)}")

    accepted, rejected, unknown = [], [], []
    for item in disputed:
        verdict = DECISIONS.get(item["name"])
        if verdict is None:
            unknown.append(item["name"])
            continue
        decision, clause, reason = verdict
        entry = dict(item, arbitration={"decision": decision, "clause": clause, "reason": reason})
        if decision == "accept":
            accepted.append(entry)
        else:
            rejected.append(entry)

    if unknown:
        print(f"[warn] 规则未覆盖（保留待议）: {unknown}")

    # 合并进最终实体表（去重）
    existing = {(e["name"] or "").lower() for e in gold["entities"]}
    added = 0
    for e in accepted:
        if (e["name"] or "").lower() not in existing:
            gold["entities"].append({
                "name": e["name"], "type": e["type"], "page": e.get("page"),
                "quote": e.get("quote", ""), "votes": e.get("votes", 1),
                "voters": e.get("voters", []),
                "arbitrated": True, "clause": e["arbitration"]["clause"],
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

    # 单票关系按 §5 一律不收录（gold["relations"] 已只含 ≥2 票项）
    gold["stats"]["relation_final"] = len(gold["relations"])
    gold["stats"]["relation_single_vote_dropped"] = len(gold.get("disputed_relations", []))

    path.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n裁决结果：收录 {len(accepted)}（其中新增 {added}），排除 {len(rejected)}")
    print(f"最终实体 {len(gold['entities'])}  关系 {len(gold['relations'])}")
    print(f"\n收录: {[e['name'] for e in accepted]}")
    print(f"\n排除: {[e['name'] for e in rejected]}")


if __name__ == "__main__":
    main()
