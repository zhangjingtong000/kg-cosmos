"""pipeline.py — shared PDF→KG extraction core used by the API and batch ingestion.

Steps: parse → chunk → per-chunk LLM extraction (text + optional vision) →
dedup entities/edges → evidence merge.  Returns plain dicts so callers choose
their own storage and response shapes.
"""

from pdf_parser import parse_pdf, chunk_document
from kg_extractor import MODELS, extract_chunk, deduplicate_entities
from graph_store import merge_evidence


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
    all_nodes, all_edges = [], []
    total_e_s, total_r_s = 0.0, 0.0

    for ch in chunked["chunks"]:
        img_path = ch["images"][0]["path"] if ch["images"] else None
        res = extract_chunk(
            ch["text"], model_text, mode, model_vis, img_path,
            content_kind=ch.get("kind", "text"),
        )
        evidence = {
            "source_id": source_id,
            "source_name": source_name,
            "page": ch["page"],
            "chunk_idx": ch["chunk_idx"],
            "text": ch["text"],
        }
        for node in res["nodes"]:
            node["evidence"] = merge_evidence(node.get("evidence", []), [evidence])
        for edge in res["edges"]:
            edge["evidence"] = merge_evidence(edge.get("evidence", []), [evidence])
        all_nodes.extend(res["nodes"])
        all_edges.extend(res["edges"])
        total_e_s += res["timing"]["entities_s"]
        total_r_s += res["timing"]["relations_s"]

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
    }
