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
from kg_extractor import (
    MODELS, canonicalize_relation_endpoints, deduplicate_entities, extract_chunk,
    extract_chunk_once,
)
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
    on_progress=None,
    text: str = None,
) -> dict:
    """跑完整抽取管线。

    `on_progress(stage, pct, detail)` 是**可选**回调，用于「上传页」显示真实进度
    （解析 → 分块 → 逐段抽取 → 归一 → 汇总）。不传就完全等于旧行为。
    回调里的异常一律吞掉——进度上报绝不能反过来把抽取搞挂。

    `text` 是**可选**的纯文本入口（2026-09-14 加）。传了它就跳过 PDF 解析，
    把整段文本当成"一页"喂给同一个分块器 —— 其余步骤（分块 / 抽取 / 归一 /
    端点规范化 / 证据合并）**完全不变**。

    **为什么需要它**：用开源人工标注数据集做外部对照评测时，数据集给的是
    **已解析的文本**（SciREX 的 `words` 列表），不是 PDF。没有这个入口就没法
    在自己的管线上跑别人的数据，也就永远拿不到外部对照 —— 而这正是评审
    反复指出的缺口（「全文没有任何与外部系统/公开基准的可比结果」）。
    """
    def report(stage: str, pct: float, detail: str = "") -> None:
        if on_progress is None:
            return
        try:
            on_progress(stage, max(0.0, min(100.0, pct)), detail)
        except Exception:
            pass

    if text is not None:
        # 构造与 parse_pdf 同构的 parsed，单页承载全部文本。
        # chunk_document 只读 pages[].text / num / images，所以这样就够了。
        report("parse", 3.0, "纯文本输入（跳过 PDF 解析）")
        parsed = {
            "file": source_name,
            "total_pages": 1,
            "pages": [{"num": 1, "text": text, "images": []}],
        }
    else:
        report("parse", 3.0, "解析 PDF")
        parsed = parse_pdf(pdf_path, work_dir)
    report("chunk", 15.0, f"{parsed['total_pages']} 页")
    chunked = chunk_document(parsed, max_chars=max_chars)
    total_chunks = len(chunked["chunks"])
    report("extract", 20.0, f"{total_chunks} 段")

    model_text = MODELS[mode]["text"]
    model_vis = MODELS[mode]["vision"]
    total_e_s, total_r_s = 0.0, 0.0
    failures: list[tuple[int, str]] = []
    # "combined" 走合并调用（实体+关系一次产出），实测每 chunk 成本 -21%；
    # "split" 保持原两次调用。默认 split 以维持既有结论的可比性。
    style = os.environ.get("KG_EXTRACT_STYLE", "split").strip().lower()
    extract_fn = extract_chunk_once if style == "combined" else extract_chunk

    done_chunks = 0

    def extract_one(chunk):
        nonlocal done_chunks
        img_path = chunk["images"][0]["path"] if chunk["images"] else None
        try:
            out = chunk, extract_fn(
                chunk["text"], model_text, mode, model_vis, img_path,
                content_kind=chunk.get("kind", "text"),
            ), None
        except Exception as exc:  # 单个 chunk 失败不拖垮全篇，但要留痕
            out = chunk, None, exc
        done_chunks += 1
        # 抽取阶段占总进度 20% → 90%
        report("extract", 20.0 + 70.0 * done_chunks / max(1, total_chunks),
               f"{done_chunks}/{total_chunks} 段")
        return out

    workers = max(1, int(os.environ.get("KG_EXTRACT_WORKERS", "1")))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(extract_one, chunked["chunks"]))
    else:
        results = [extract_one(ch) for ch in chunked["chunks"]]

    report("norm", 92.0, "归一与去重")
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

    # 端点规范化：deduplicate_entities 会把实体名改成"最长的变体"，关系端点若不
    # 同步改写就会失配——实测第一篇云端输出 779 条关系中 328 条（42%）的端点
    # 在实体集合里找不到，写 Neo4j 时被静默丢弃。
    all_edges = canonicalize_relation_endpoints(all_edges, all_nodes)
    known = {n["name"].strip().casefold() for n in all_nodes if n.get("name")}
    resolved, dangling = [], 0
    for e in all_edges:
        if (e.get("source", "").strip().casefold() in known
                and e.get("target", "").strip().casefold() in known):
            resolved.append(e)
        else:
            dangling += 1
    if dangling:
        print(f"    [warn] {dangling} 条关系的端点无法解析到实体，已丢弃", flush=True)
    all_edges = resolved

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
        "dangling_edges_dropped": dangling,
    }
