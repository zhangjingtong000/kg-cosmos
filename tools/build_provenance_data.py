# -*- coding: utf-8 -*-
"""把 `data/quality/metric-provenance.json` 压成前端能用的推导链数据。

为什么要这个脚本（而不是让页面直接 fetch `data/`）：
    `tools/check_frontend_data.py` 的 N6 纪律 —— **前端要用的数据必须预生成到
    `web/kg-data/`**，页面运行时不许指向仓库的 `data/`。静态站导出、`file://` 打开
    时更只能读 `web/` 内的东西。

产出：`web/kg-data/provenance.json`
    · `metrics`：**只保留本页真正显示的那些**指标，每条带
      读数 / 产物文件 / 产物内的取值路径 / 生成脚本 / 产物时间。
    · `sources`：这一页所有数字**共用的上游输入**（文件数、字节、时间、sha1 前 16 位）。
      刻意**不**给每个指标编一条"它的上游是哪几个文件" —— 溯源产物里没有这层映射，
      编出来就是假证据。宁可只声称"这些数字共享这些输入"。
    · `commands`：生成脚本 → 复算命令。

口径纪律（写在代码里免得后来者踩）：这里**只做搬运与裁剪，不做任何计算**。
    任何在本脚本里算出来的数，都会变成一个"页面上有、但产物里查不到"的数。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "quality" / "metric-provenance.json"
OUT = ROOT / "web" / "kg-data" / "provenance.json"

# 页面上真正显示出来的指标。按显示顺序排，前端照着这个顺序渲染即可。
SHOWN = [
    ("L1_entity_presence", "L1 实体存在"),
    ("L4_predicate_all", "L4 谓词落在本体"),
    ("L5_triplet_all", "L5 通过率"),
    ("L6_evidence_all", "L6 证据完整度"),
    ("L5_true_support_rate", "L5 真实支持率"),
    ("L5_ppv", "L5 PPV"),
    ("L5_domain_auc", "L5 域内加权 AUC"),
    ("L5_vs_human_kappa", "L5 与 LLM 盲判标签 κ"),
    ("L5_annotator_kappa", "两次 LLM 盲判之间 κ"),
    ("L9_endpoint_recall", "L9 端点召回"),
    ("L8_nodes", "图规模 · 节点"),
    ("edges_total", "评测全集 · 边"),
]


def main() -> int:
    if not SRC.exists():
        print(f"找不到 {SRC.relative_to(ROOT)}；先跑 tools/metric_provenance.py --write")
        return 1
    d = json.loads(SRC.read_text(encoding="utf-8"))
    src_metrics = d.get("metrics") or {}

    metrics = []
    for key, label in SHOWN:
        m = src_metrics.get(key)
        if not m:                      # 产物里没有就跳过，不补默认值
            continue
        metrics.append({
            "key": key,
            "label": label,
            "value": m.get("value"),
            "artifact": m.get("artifact"),
            "path": m.get("path"),
            "produced_by": m.get("produced_by"),
            "artifact_mtime": m.get("artifact_mtime"),
        })

    sources = []
    for name, s in (d.get("sources") or {}).items():
        sources.append({
            "name": name,
            "files": s.get("files"),
            "bytes": s.get("bytes"),
            "mtime": s.get("newest_mtime"),
            "sha1_16": s.get("sha1_16"),
        })
    sources.sort(key=lambda x: -(x.get("bytes") or 0))

    out = {
        "generated_by": "tools/build_provenance_data.py",
        "generated": d.get("generated"),
        "note": ("溯源数据来自 data/quality/metric-provenance.json；"
                 "本文件只做裁剪，不含任何新算出来的数。"),
        "metrics": metrics,
        "sources": sources,
        # ⚠️ 这一段会**直接进 DOM 当可见文本**（页面用 esc() 插进去，不经过 md()），
        #    所以里面**不能写 markdown 标记**。第一版这里写了 `**共用**`，
        #    于是 `tools/check_dom_text.py` 判失败 —— 屏上真的会显示两个星号。
        "caveat": ("sources 是这一页所有数字共用的上游输入，"
                   "不是逐指标的依赖边 —— 溯源产物里没有那层映射。"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"写出 {OUT.relative_to(ROOT)}：{len(metrics)} 个指标 / {len(sources)} 个输入源 / "
          f"{OUT.stat().st_size:,} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
