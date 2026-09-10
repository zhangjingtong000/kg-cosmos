"""pipeline.py — shared PDF→KG extraction core used by the API and batch ingestion.

Steps: parse → chunk → per-chunk LLM extraction (text + optional vision) →
dedup entities/edges → evidence merge.  Returns plain dicts so callers choose
their own storage and response shapes.

Chunk extraction is independent per chunk and runs through a thread pool
sized by KG_EXTRACT_WORKERS (default 1) so a locally hosted model server can
be kept busy with parallel sequences instead of one request at a time.
"""

import os
from concurrent.futures import ThreadPoolExecutor

from pdf_parser import parse_pdf, chunk_document
from kg_extractor import MODELS, extract_chunk, deduplicate_entities
from graph_store import merge_evidence


class ExtractionFailed(RuntimeError):
    """整篇文档没有产出任何实体 —— 这不是"图谱为空"，而是抽取失败。

    失败模式（2026-09-10 实测）：云端网关达到配额上限后以 200 返回 error 体，
    旧代码把它解析成空结果，于是 111 个 chunk 全部产生 0 实体、却写出空快照
    并被记为完成。任何"零产出"都必须冒泡给调用方，不能静默落盘。
    """


def extract_graph(
    pdf_path: str,
    work_dir: str,
    source_id: str,
    source_name: str,
    mode: str = "local",
    max_chars: int = 500,
) -> dict:
    parsed = parse_pdf(pdf_path, work_dir)
    chunked = chunk_document(parsed, max_chars=max_chars)

    model_text = MODELS[mode]["text"]
    model_vis = MODELS[mode]["vision"]
    total_e_s, total_r_s = 0.0, 0.0
    failures: list[tuple[int, str]] = []

    def extract_one(chunk):
        img_path = chunk["images"][0]["path"] if chunk["images"] else None
        try:
            return chunk, extract_chunk(
                chunk["text"], model_text, mode, model_vis, img_path,
                content_kind=chunk.get("kind", "text"),
            ), None
        except Exception as exc:  # 单个 chunk 失败不拖垮全篇，但要留痕
            return chunk, None, exc

    workers = max(1, int(os.environ.get("KG_EXTRACT_WORKERS", "1")))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(extract_one, chunked["chunks"]))
    else:
        results = [extract_one(ch) for ch in chunked["chunks"]]

    all_nodes, all_edges = [], []
    for chunk, res, exc in results:
        if res is None:
            failures.append((chunk["chunk_idx"], f"{type(exc).__name__}: {exc}"))
            continue
        evidence = {
            "source_id": source_id,
            "source_name": source_name,
            "page": chunk["page"],
            "chunk_idx": chunk["chunk_idx"],
            "text": chunk["text"],
        }
        for node in res["nodes"]:
            node["evidence"] = merge_evidence(node.get("evidence", []), [evidence])
        for edge in res["edges"]:
            edge["evidence"] = merge_evidence(edge.get("evidence", []), [evidence])
        all_nodes.extend(res["nodes"])
        all_edges.extend(res["edges"])
        total_e_s += res["timing"]["entities_s"]
        total_r_s += res["timing"]["relations_s"]

    total_chunks = len(chunked["chunks"])
    if failures:
        head = "; ".join(f"chunk#{i} {msg}" for i, msg in failures[:3])
        print(f"    [warn] {len(failures)}/{total_chunks} chunks failed: {head}", flush=True)

    if total_chunks and not all_nodes:
        raise ExtractionFailed(
            f"{source_id}: {total_chunks} chunks 全部零产出"
            f"（失败 {len(failures)} 个；首因：{failures[0][1] if failures else '模型对每个 chunk 都返回 0 实体'}）"
        )

    all_nodes = deduplicate_entities(all_nodes, mode)
    deduped_edges = {}
    for e in all_edges:
        key = (e["source"].lower(), e["target"].lower(), e["type"].lower())
        if key in deduped_edges:
            existing = deduped_edges[key]
            existing["evidence"] = merge_evidence(
                existing.get("evidence", []), e.get("evidence", [])
            )
        else:
            e["evidence"] = merge_evidence(e.get("evidence", []))
            deduped_edges[key] = e

    return {
        "nodes": all_nodes,
        "edges": list(deduped_edges.values()),
        "parsed": parsed,
        "chunked": chunked,
        "extraction_time_s": round(total_e_s + total_r_s, 1),
        "images_found": sum(len(p["images"]) for p in parsed["pages"]),
        "chunk_failures": len(failures),
        "total_chunks": total_chunks,
    }
