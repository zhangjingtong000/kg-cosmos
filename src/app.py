"""
app.py — FastAPI backend for the KG extraction pipeline.

Endpoints:
  GET  /health             — liveness + 数据面可用性
  GET  /api/kg/{name}      — 前端数据面（web/kg-data/*.json）
  GET  /api/quality/{name} — 质量产物（data/quality/*.json）
  GET  /api/manifest       — 数据面清单与各文件的更新时间
  POST /upload             — Upload PDF, run full pipeline, return **this** graph
  GET  /graph/{id}         — Get stored graph
  GET  /stats              — Pipeline statistics

【与前端的关系（2026-09-12 修）】
  此前这个后端与前端是**两份平行世界**：前端全部读预生成的静态 JSON，
  5 个 API 端点一个都没被调用过。现在 `/api/kg/*` 与 `/api/quality/*`
  把前端数据面暴露成 HTTP，前端优先走 API、失败时回退静态文件
  （保持「离线演示铁律」不被破坏）。
  同时用 StaticFiles 把 `web/` 挂在同一个 origin 下，消除 CORS 与 file:// 问题。

Run:
  python app.py
  uvicorn app:app --reload
"""

import asyncio
import json, os, uuid, shutil
from collections import Counter
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from pdf_parser import parse_pdf  # noqa: F401  (kept for direct API consumers)
from pipeline import extract_graph
from graph_store import GraphStore
from explorer_models import build_detail_window, build_source_summary

# ── App Setup ────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
KG_DATA = WEB_DIR / "kg-data"
QUALITY = ROOT / "data" / "quality"

app = FastAPI(title="KG Extraction API", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Store graphs in memory + on disk
DATA_DIR = ROOT / "data"
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


# ── 数据面：把前端要用的 JSON 暴露成 HTTP ──────────────────
# 白名单而非通配，避免把整个目录结构暴露出去。

def _serve_json(path: Path):
    if not path.exists():
        raise HTTPException(404, f"未找到 {path.name}")
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, f"读取 {path.name} 失败: {e}")


@app.get("/health")
async def health():
    """存活 + 数据面可用性。前端可据此决定走 API 还是回退静态文件。"""
    return {
        "ok": True,
        "time": datetime.now().isoformat(),
        "web_dir": str(WEB_DIR),
        "kg_data_files": sorted(p.name for p in KG_DATA.glob("*.json"))[:20] if KG_DATA.exists() else [],
        "quality_files": len(list(QUALITY.glob("*.json"))) if QUALITY.exists() else 0,
        "graphs_in_memory": len(graphs),
    }


@app.get("/api/manifest")
async def manifest():
    """数据面清单：前端用它知道有哪些文件、各自什么时候生成的。"""
    def entries(d: Path):
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob("*.json")):
            st = p.stat()
            out.append({
                "name": p.name,
                "bytes": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            })
        return out
    return {"kg_data": entries(KG_DATA), "quality": entries(QUALITY)}


@app.get("/api/kg/{name}")
async def api_kg(name: str):
    """前端数据面（web/kg-data/<name>.json）。"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    return _serve_json(KG_DATA / name)


@app.get("/api/quality/{name}")
async def api_quality(name: str):
    """质量产物（data/quality/<name>.json），分析面板的可追溯来源。"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    return _serve_json(QUALITY / name)


# ── Routes ───────────────────────────────────────────────

@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    mode: str = Query("cloud", description="Model backend: cloud or local"),
    max_chars: int = Query(500, description="Max chars per chunk"),
):
    """上传 PDF，跑完整抽取管线，返回**本次这一篇**的图。

    ⚠️ 前置条件：需要可用的模型后端。`mode=cloud` 会调用云端 LLM
    （本项目账号余额已耗尽，实际不可用）；`mode=local` 走本机 Ollama。
    界面上不把这个端点当作主推功能——它存在是为了让「上传→入图」这条链路
    可被端到端测试，而不是假装零成本环境下能随时抽新论文。

    【2026-09-12 修】此前这里返回的是 `store.get_3d_data()`，
    即**整个图库**的 3D 数据（graph_store 里该调用没有按 graph_id 过滤），
    语义上等于"上传一篇论文，返回全部论文的图"，而且会在 15k 节点上
    触发 spring_layout 而极慢。现在只返回本次的 nodes/edges。
    """
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
        # 抽取是同步且耗时的（一次上传可能几分钟），放到线程里跑，
        # 否则会阻塞事件循环、把 /health 之类的轻请求一起卡住。
        result_core = await asyncio.to_thread(
            extract_graph,
            str(pdf_path), str(upload_dir), gid, file.filename,
            mode=mode, max_chars=max_chars,
        )
        all_nodes, all_edges = result_core["nodes"], result_core["edges"]
        parsed, chunked = result_core["parsed"], result_core["chunked"]

        # 写进持久层（Neo4j 或 networkx），供后续查询。
        store = build_store()
        store.store.import_data(all_nodes, all_edges)

        # ⚠️ 这里刻意**不再**调用 store.get_3d_data()：那是全库视图。
        # 本次上传只看这一次抽出来的东西。
        result = {
            "id": gid,
            "source": {"id": gid, "name": file.filename, "kind": "pdf"},
            "nodes": [
                {"name": n.get("name"), "type": n.get("type"),
                 "description": n.get("description"), "evidence": n.get("evidence", [])}
                for n in all_nodes
            ],
            "edges": [
                {"source": e.get("source"), "target": e.get("target"),
                 "type": e.get("type"), "description": e.get("description"),
                 "evidence": e.get("evidence", [])}
                for e in all_edges
            ],
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
    """**原型级**的图内问答：字符串匹配 + 邻边拼接，不调用任何模型。

    ⚠️ 此前的 docstring 写着 "uses Neo4j + LLM for retrieval"，与实现不符——
    实现里没有任何 LLM，也没有查 Neo4j，就是 `q_lower.split()` 的子串匹配。
    这会让人（和演示老师）误以为项目有一个 LLM 问答能力。
    现在如实标注为原型，并把检索方式写清楚。

    真正的图问答在 `web/pathqa.html`：它做的是**字面种子匹配 + 2 跳 BFS
    + 上限 70 节点的子图**，结果可复现、可解释，且不依赖模型。
    两者定位不同：本端点是"给定 graph_id 的单文档问答壳"，
    PathQA 是"面向全图的子图检索视图"。
    """
    if req.graph_id not in graphs:
        raise HTTPException(404, f"Graph '{req.graph_id}' not found")

    graph = graphs[req.graph_id]
    edges = graph["edges"]

    q_lower = req.question.lower()
    matched_nodes = [
        n for n in graph["nodes"]
        if any(word in n["name"].lower() or word in (n.get("description") or "").lower()
               for word in q_lower.split())
    ]

    matched_names = {n["name"] for n in matched_nodes}
    relevant_edges = [
        e for e in edges
        if e["source"] in matched_names or e["target"] in matched_names
    ]

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

# 把 web/ 挂在同一个 origin 下（必须在所有 API 路由**之后**挂载，
# 否则 StaticFiles 的根路由会吃掉 /health、/api/* 这些前缀）。
# 收益：① 前端与 API 同源，消除 CORS 与 file:// 限制；
#       ② 只需起一个进程就能同时提供界面与数据面，演示时少一个失败点。
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
