"""
app.py — FastAPI backend for the KG extraction pipeline.

Endpoints:
  POST /upload       — Upload PDF, run full pipeline, return graph
  GET  /graph/{id}   — Get stored graph
  GET  /stats        — Pipeline statistics

Run:
  python app.py
  uvicorn app:app --reload
"""

import json, os, uuid, shutil
from collections import Counter
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from pdf_parser import parse_pdf  # noqa: F401  (kept for direct API consumers)
from pipeline import extract_graph
from graph_store import GraphStore
from explorer_models import build_detail_window, build_source_summary

# ── App Setup ────────────────────────────────────────────

app = FastAPI(title="KG Extraction API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Store graphs in memory + on disk
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
graphs: dict[str, dict] = {}  # id → graph data


def build_store() -> GraphStore:
    """Neo4j when configured and reachable; NetworkX keeps /upload usable offline."""
    if os.environ.get("KG_BACKEND", "neo4j") == "neo4j":
        try:
            return GraphStore(
                backend="neo4j",
                uri=os.environ.get("KG_NEO4J_URI", "bolt://localhost:7687"),
                user=os.environ.get("KG_NEO4J_USER", "neo4j"),
                password=os.environ.get("KG_NEO4J_PASSWORD", "kgdemo2026"),
            )
        except Exception as exc:
            print(f"[store] Neo4j unavailable ({exc}); falling back to networkx")
    return GraphStore(backend="networkx")


# ── Models ───────────────────────────────────────────────

class GraphResponse(BaseModel):
    id: str
    nodes: list[dict]
    edges: list[dict]
    stats: dict
    created_at: str

class ChatRequest(BaseModel):
    graph_id: str
    question: str

class ChatResponse(BaseModel):
    answer: str
    sources: list[dict] = []


# ── Routes ───────────────────────────────────────────────

@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    mode: str = Query("cloud", description="Model backend: cloud or local"),
    max_chars: int = Query(500, description="Max chars per chunk"),
):
    """Upload a PDF, run the full extraction pipeline, return the graph."""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files supported")

    # Save uploaded file
    gid = uuid.uuid4().hex[:8]
    upload_dir = DATA_DIR / gid
    upload_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = upload_dir / file.filename
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        # Steps 1-5: parse, chunk, extract, dedup (shared with batch ingestion)
        result_core = extract_graph(
            str(pdf_path), str(upload_dir), gid, file.filename,
            mode=mode, max_chars=max_chars,
        )
        all_nodes, all_edges = result_core["nodes"], result_core["edges"]
        parsed, chunked = result_core["parsed"], result_core["chunked"]

        # Step 6: Store — write through to the persistent store; the per-graph
        # JSON snapshot below keeps the single-document view restart-safe.
        store = build_store()
        store.store.import_data(all_nodes, all_edges)
        graph_3d = store.get_3d_data()

        # Save
        result = {
            "id": gid,
            "source": {"id": gid, "name": file.filename, "kind": "pdf"},
            "nodes": graph_3d["nodes"],
            "edges": graph_3d["edges"],
            "stats": {
                "pdf_pages": parsed["total_pages"],
                "chunks": chunked["total_chunks"],
                "nodes": len(all_nodes),
                "edges": len(all_edges),
                "tables": len(chunked.get("tables", [])),
                "extraction_time_s": result_core["extraction_time_s"],
                "images_found": result_core["images_found"],
            },
            "created_at": datetime.now().isoformat(),
        }
        graphs[gid] = result

        # Save to disk
        (upload_dir / "graph.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return JSONResponse(result)

    except Exception as e:
        raise HTTPException(500, f"Pipeline failed: {str(e)}")


@app.get("/graph/{graph_id}")
async def get_graph(graph_id: str):
    """Retrieve a previously extracted graph by ID."""
    if graph_id not in graphs:
        # Try loading from disk
        disk_path = DATA_DIR / graph_id / "graph.json"
        if disk_path.exists():
            graphs[graph_id] = json.loads(disk_path.read_text(encoding="utf-8"))
        else:
            raise HTTPException(404, f"Graph '{graph_id}' not found")
    return graphs[graph_id]


@app.get("/graph/{graph_id}/explorer")
async def get_explorer_graph(graph_id: str, limit: int = Query(80, ge=1, le=500)):
    """Return source metadata and a bounded detailed reading window."""
    graph = await get_graph(graph_id)
    degree = Counter(
        endpoint
        for edge in graph["edges"]
        for endpoint in (edge["source"], edge["target"])
    )
    nodes = [
        {
            "id": node["name"],
            "name": node["name"],
            "importance": degree[node["name"]],
            "bridge_score": 0,
            "relevance": 0,
            "evidence": node.get("evidence", []),
        }
        for node in graph["nodes"]
    ]
    source = graph.get("source", {"id": graph_id, "name": graph_id, "kind": "unknown"})
    return {
        "graph_id": graph_id,
        "sources": [
            {
                **build_source_summary(source["id"], source["name"], nodes),
                "kind": source.get("kind", "unknown"),
            }
        ],
        "detail_window": build_detail_window(nodes, limit),
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    """Simple KG-based Q&A (uses Neo4j + LLM for retrieval)."""
    if req.graph_id not in graphs:
        raise HTTPException(404, f"Graph '{req.graph_id}' not found")

    graph = graphs[req.graph_id]
    nodes = {n["name"]: n for n in graph["nodes"]}
    edges = graph["edges"]

    # Simple retrieval: find matching nodes and their neighbors
    q_lower = req.question.lower()
    matched_nodes = [
        n for n in graph["nodes"]
        if any(word in n["name"].lower() or word in n.get("description", "").lower()
               for word in q_lower.split())
    ]

    # Get connected edges
    matched_names = {n["name"] for n in matched_nodes}
    relevant_edges = [
        e for e in edges
        if e["source"] in matched_names or e["target"] in matched_names
    ]

    # Build natural language answer from graph data
    if not matched_nodes:
        answer = "No relevant entities found in the knowledge graph for your question."
    else:
        parts = []
        for e in relevant_edges[:10]:
            parts.append(f"{e['source']} {e['type'].replace('_',' ').lower()} {e['target']}")
        answer = "Based on the knowledge graph:\n" + "\n".join(f"- {p}" for p in parts)

    return ChatResponse(answer=answer, sources=relevant_edges[:10])


@app.get("/stats")
async def stats():
    """Get overall pipeline statistics."""
    return {
        "graphs_stored": len(graphs),
        "total_nodes": sum(len(g["nodes"]) for g in graphs.values()),
        "total_edges": sum(len(g["edges"]) for g in graphs.values()),
    }


# ── Main ─────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
