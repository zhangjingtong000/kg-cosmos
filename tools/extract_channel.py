"""按生产同款分块与 prompt，对单篇论文跑指定通道的抽取，产出独立评分文件。

用途：M2 四通道对比需要各通道在同一篇、同一分块上的抽取结果。
不写 Neo4j（避免污染已验收的图谱），结果落 data/gold/channel-<mode>-<paper>.json。

用法：
  python tools/extract_channel.py local 2012.00456v1
  python tools/extract_channel.py cloud 2012.00456v1
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("KG_RECORD_DIR", "data/distill")

from kg_extractor import (  # noqa: E402
    ENTITY_PROMPT, LOCAL_ENTITY_PROMPT, MODELS, build_local_entity_prompt,
    call_deepseek, call_ollama_json, deduplicate_entities, parse_entities,
    parse_relations, parse_structured_entities, parse_structured_relations,
    retain_relations_with_known_endpoints, LOCAL_RELATION_PROMPT, RELATION_PROMPT,
    ENTITY_TYPES, RELATION_TYPES,
)
from pdf_parser import parse_pdf, chunk_document  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "local"
PAPER = sys.argv[2] if len(sys.argv) > 2 else "2012.00456v1"
LIMIT = int(os.environ.get("CHANNEL_CHUNKS", "0"))  # 0 = 全部
CORPUS = Path("data/corpus")
OUT_DIR = Path("data/gold")
LOG = Path("output") / f"channel-{MODE}-{PAPER}.log"
OUT_DIR.mkdir(parents=True, exist_ok=True)
Path("output").mkdir(exist_ok=True)


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg, flush=True)


open(LOG, "w", encoding="utf-8").close()

parsed = parse_pdf(str(CORPUS / f"{PAPER}.pdf"), str(CORPUS / "graphs" / f"ch-{MODE}-{PAPER}"))
chunks = chunk_document(parsed, max_chars=500)["chunks"]
if LIMIT:
    chunks = chunks[:LIMIT]
log(f"通道 {MODE} / {PAPER}: {len(chunks)} chunks")

model_text = MODELS[MODE]["text"]

# 断点续跑：每块处理完即追加到 JSONL，进程被杀也不丢已完成的工作。
# （2026-09-10 教训：原实现只在结束时写结果，一次卡死导致 90/111 块全废。）
PARTIAL = OUT_DIR / f"channel-{MODE}-{PAPER}.partial.jsonl"
done_idx = set()
if PARTIAL.exists() and "--fresh" not in sys.argv:
    for line in PARTIAL.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            done_idx.add(rec["i"])
        except Exception:
            continue
    log(f"[resume] 已完成 {len(done_idx)} 块，跳过")

nodes, edges, failures = [], [], []
t_all = time.time()
WORKERS = max(1, int(os.environ.get("CHANNEL_WORKERS", "1")))
log(f"并发 {WORKERS} 路" + (f"，已完成 {len(done_idx)} 块将跳过" if done_idx else ""))


def extract_one(item):
    """单块两阶段抽取，返回 (序号, 页, 实体, 关系, 错误)。"""
    i, ch = item
    text, kind = ch["text"], ch.get("kind", "text")
    ents, rels, err = [], [], None
    try:
        if MODE == "local":
            ep = build_local_entity_prompt(text, kind)
            ents = parse_structured_entities(call_ollama_json(model_text, ep))
            if ents:
                rp = LOCAL_RELATION_PROMPT.format(
                    entity_list="\n".join(f"- {e['name']}" for e in ents),
                    relation_types=", ".join(RELATION_TYPES), text=text)
                rels = retain_relations_with_known_endpoints(
                    parse_structured_relations(call_ollama_json(model_text, rp)),
                    [e["name"] for e in ents])
        else:
            ep = ENTITY_PROMPT.format(entity_types=", ".join(ENTITY_TYPES), text=text)
            ents = parse_entities(call_deepseek(model_text, ep))
            if ents:
                rp = RELATION_PROMPT.format(
                    entity_list="\n".join(f"- {e['name']}" for e in ents),
                    relation_types=", ".join(RELATION_TYPES), text=text)
                rels = retain_relations_with_known_endpoints(
                    parse_relations(call_deepseek(model_text, rp)),
                    [e["name"] for e in ents])
        ev = {"source_id": PAPER, "source_name": f"{PAPER}.pdf",
              "page": ch["page"], "chunk_idx": ch["chunk_idx"], "text": text}
        for e in ents:
            e["evidence"] = [ev]
        for r in rels:
            r["evidence"] = [ev]
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:200]
    return i, ch["page"], ents, rels, err


todo = [(i, ch) for i, ch in enumerate(chunks, 1) if i not in done_idx]
if todo:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with open(PARTIAL, "a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(extract_one, item) for item in todo]
        finished = 0
        for fut in as_completed(futures):
            i, page, ents, rels, err = fut.result()
            finished += 1
            if err:
                failures.append({"chunk": i, "page": page, "error": err})
                if len(failures) <= 3:
                    log(f"  [{i}] 失败: {err[:150]}")
            # 立即落盘该块结果（含失败标记，避免重跑失败块时死循环）
            fh.write(json.dumps({"i": i, "page": page, "nodes": ents, "edges": rels,
                                 "error": err}, ensure_ascii=False) + "\n")
            fh.flush()
            nodes.extend(ents)
            edges.extend(rels)
            if finished % 10 == 0 or finished == len(todo):
                log(f"  [{finished}/{len(todo)}] 累计 {len(nodes)} 实体 "
                    f"{len(edges)} 关系 ({time.time()-t_all:.0f}s)")


# 断点续跑时，从 partial 汇总全部结果
if done_idx:
    nodes, edges, failures = [], [], []
    for line in PARTIAL.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        nodes.extend(rec.get("nodes", []))
        edges.extend(rec.get("edges", []))
        if rec.get("error"):
            failures.append({"chunk": rec.get("i"), "page": rec.get("page"),
                             "error": rec["error"]})

dedup_nodes = deduplicate_entities(list(nodes), MODE)
dedup_edges = {}
for e in edges:
    key = (e["source"].lower(), e["target"].lower(), e["type"].lower())
    dedup_edges.setdefault(key, e)

out = OUT_DIR / f"channel-{MODE}-{PAPER}.json"
out.write_text(json.dumps({
    "paper_id": PAPER, "mode": MODE, "model": model_text,
    "chunks": len(chunks), "chunk_failures": len(failures),
    "elapsed_s": round(time.time() - t_all, 1),
    "nodes_raw": len(nodes), "edges_raw": len(edges),
    "nodes": dedup_nodes, "edges": list(dedup_edges.values()),
    "failures": failures[:20],
}, ensure_ascii=False, indent=2), encoding="utf-8")

log(f"完成: {len(dedup_nodes)} 实体 / {len(dedup_edges)} 关系 "
    f"(原始 {len(nodes)}/{len(edges)}), 失败 {len(failures)}, "
    f"耗时 {time.time()-t_all:.0f}s")
log(f"输出 {out}")
