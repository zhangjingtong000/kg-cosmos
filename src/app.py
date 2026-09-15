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


class QARequest(BaseModel):
    """多 Agent 问答的请求。

    `use_llm=False` 时只跑检索 Agent（纯规则），用于离线/无模型环境 ——
    结果里会标 `degraded: true`，不假装有模型参与。
    """
    graph_id: str
    question: str
    use_llm: bool = True


class QAStepRequest(BaseModel):
    """**分步**多 Agent 问答的请求 —— 让"多个 Agent 在分工"在界面上看得见。

    本项目最主要的看点是多 Agent 问答，而一次性返回全部结果，观众只会看到
    "一次问答给了两段文字"，看不出有分工。拆成四步后界面可以一步一行状态。

    step 取值：
      · `retrieve` —— 纯规则检索（不调模型）
      · `support`  —— 支持方读证据
      · `oppose`   —— 反对方**独立**读同一批证据
      · `synth`    —— 综合方只看两方交上来的东西

    步骤之间**无状态**：`evidence` / `support` / `oppose` 由前端回传，
    后端不存 session。证据只有几十 KB，回传比维护会话状态安全得多 ——
    这个项目在上传任务上已经栽过一次（后端重启丢任务，后加 `_save_task`）。
    """
    graph_id: str
    question: str
    step: str = 'retrieve'
    evidence: list[dict] = []
    support: str = ''
    oppose: str = ''


# ── 数据面：把前端要用的 JSON 暴露成 HTTP ──────────────────
# 白名单而非通配，避免把整个目录结构暴露出去。

def _serve_json(path: Path):
    if not path.exists():
        raise HTTPException(404, f"未找到 {path.name}")
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, f"读取 {path.name} 失败: {e}")


# 盲判保护名单：文件名里出现任一片段就拒绝经 HTTP 提供。
# 这些文件含逐条判别器分数（entail_prob/pass/hit）、分层权重或探针 id —— 
# 看到它们，人工盲判就不是盲的了。
_BLIND_PROTECTED = (
    "l5-validity-key", "l5-validity-labels", "l5-validity-design",
    "l5-validity-report", "l5-validity-probe", "l5-validity-check",
    "l5-validity-sample", "l5-annotator-agreement", "relation-verify-all",
    "l5-single", "l5-labels",
)


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
    """质量产物（data/quality/<name>.json），分析面板的可追溯来源。

    【盲态】含盲判答案/分层/逐条分数的产物一律 404。8000 与 web/ 同源，
    修复前实测 GET /api/quality/l5-validity-key.json = 200 / 18498B，
    明文含 entail_prob/pass/hit/stratum —— 标注者一条 URL 就破盲，
    人工盲判（L5 唯一可信的效度来源）当场作废。
    运行手册：docs/L5_HUMAN_REVIEW_RUNBOOK.md
    """
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    low = name.lower()
    if any(p in low for p in _BLIND_PROTECTED):
        raise HTTPException(404, f"{name} 不对外提供（盲判答案/分层）")
    return _serve_json(QUALITY / name)


# ── Routes ───────────────────────────────────────────────

@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    # ⚠️ **默认值从 "cloud" 改成 "local"（2026-09-15 修）**：
    # `src/kg_extractor.py` 自 2026-09-11 起在 `KG_ALLOW_CLOUD` 未设时直接抛
    # RuntimeError（云端密钥刻意不从注册表注入，防误调用扣费）。
    # 所以**旧的默认值 "cloud" 会让任何不带参数的调用必然 500**。
    # 同文件的 `/api/upload/start` 早就默认 "local"，这里是漏改的一处。
    # 前端 `upload.html:341` 一直显式传 local，所以此前只影响 API / 脚本侧。
    mode: str = Query("local", description="Model backend: cloud or local（默认 local；云端需先设 KG_ALLOW_CLOUD=1）"),
    max_chars: int = Query(500, description="Max chars per chunk"),
):
    """上传 PDF，跑完整抽取管线，返回**本次这一篇**的图。

    前置条件：需要可用的模型后端。`mode=local` 走本机 Ollama（**零 API 成本**，
    实测实体 F1 高于云端 55%，见 `docs/CHANNEL_ATTRIBUTION.md`）；
    `mode=cloud` 调云端 LLM。**当前不可用**，两个独立原因：
      ① 自 2026-09-11 起，云端密钥**刻意不从系统注册表自动注入**
         （防任何 import 该模块的脚本误调用扣费），要显式设 `KG_ALLOW_CLOUD=1`；
      ② 实测带 key 调用返回 **HTTP 401**。
    此前这里写的是「账号余额已耗尽」—— 那是**推测**，不是实测到的原因，已改准。

    【2026-09-13 修订】此前这里写着「界面上不把这个端点当作主推功能」，
    并把「零成本环境下不能随时抽新论文」当作既成事实 —— 那是被
    「离线演示铁律」带偏的结论，而那条铁律并非项目方的要求：
    本地通道零成本、一篇云端也只要 ¥0.013，真正烧钱的是调试期反复调用。
    现在「上传 → 生成图谱」是演示主线，界面上有独立页面 `web/upload.html`。
    因为抽取耗时以分钟计，界面不用本端点（同步、跑完才回），
    而是用下面的 `/api/upload/start` + `/api/upload/status/{gid}` 拿真实进度。

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


# ── 异步上传：给「上传页」用真实进度 ────────────────────────────────────
# 为什么另开一对端点而不是改 /upload：
#   /upload 是同步的（跑完才返回），适合脚本与回归测试，语义清晰，不动它。
#   但界面上要让用户看见"图谱在生长"，就必须能在抽取过程中读到进度，
#   所以这里加一对 start/status —— 进度由 pipeline.extract_graph 的
#   on_progress 回调上报，是**真进度**，不是按耗时估算的假进度条。
#
# 【2026-09-14 修】任务状态**落盘**。
#   此前 UPLOAD_TASKS 只存在内存里，于是后端一重启，跑到一半的抽取就
#   静默消失，界面上只表现为"进度不动了"（我自己在验证别的功能时踩到：
#   68/141 的任务丢了）。现在每次更新都写 data/upload-tasks/<gid>.json，
#   启动时扫盘恢复；恢复出来的 running 任务一律标成 interrupted 并带上
#   原因 —— 让界面能明确告诉用户"任务被中断了"，而不是永远转圈。
UPLOAD_TASKS: dict[str, dict] = {}
UPLOAD_TASKS_DIR = DATA_DIR / "upload-tasks"


def _save_task(task: dict) -> None:
    try:
        UPLOAD_TASKS_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOAD_TASKS_DIR / f"{task['id']}.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:      # 落盘失败不该影响抽取本身
        print(f"[upload] 任务状态落盘失败: {exc}")


def _load_tasks() -> None:
    """启动时扫盘恢复。running 的一律标 interrupted（进程已不在了）。"""
    if not UPLOAD_TASKS_DIR.exists():
        return
    n_int = 0
    for f in sorted(UPLOAD_TASKS_DIR.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            if not t.get("id"):
                continue
            if t.get("state") == "running":
                t["state"] = "interrupted"
                t["error"] = "后端进程重启，该任务已中断（抽取无法从断点续跑，请重新提交）"
                n_int += 1
                _save_task(t)
            UPLOAD_TASKS[t["id"]] = t
        except Exception:
            continue
    if UPLOAD_TASKS:
        print(f"[upload] 恢复 {len(UPLOAD_TASKS)} 个历史任务"
              f"（其中 {n_int} 个标为 interrupted）")


_load_tasks()


def _progress_cb(gid: str):
    def cb(stage: str, pct: float, detail: str = "") -> None:
        t = UPLOAD_TASKS.get(gid)
        if t is not None and t.get("state") == "running":
            t["stage"] = stage
            t["pct"] = round(pct, 1)
            t["detail"] = detail
            _save_task(t)
    return cb


async def _run_upload(gid: str, pdf_path: Path, work_dir: Path,
                      filename: str, mode: str, max_chars: int) -> None:
    task = UPLOAD_TASKS[gid]
    try:
        core = await asyncio.to_thread(
            extract_graph, str(pdf_path), str(work_dir), gid, filename,
            mode, max_chars, _progress_cb(gid),
        )
        nodes, edges = core["nodes"], core["edges"]
        await asyncio.to_thread(build_store().store.import_data, nodes, edges)

        result = {
            "id": gid,
            # 记下走的是哪条通道。new-graph.html 要按它如实说明
            # 「走本机模型」还是「走云端 API」—— 不能让界面写死一句话。
            "mode": mode,
            "source": {"id": gid, "name": filename, "kind": "pdf"},
            "nodes": [
                {"name": n.get("name"), "type": n.get("type"),
                 "description": n.get("description"), "evidence": n.get("evidence", [])}
                for n in nodes
            ],
            "edges": [
                {"source": e.get("source"), "target": e.get("target"),
                 "type": e.get("type"), "description": e.get("description"),
                 "evidence": e.get("evidence", [])}
                for e in edges
            ],
            "stats": {
                "pdf_pages": core["parsed"]["total_pages"],
                "chunks": core["chunked"]["total_chunks"],
                "nodes": len(nodes),
                "edges": len(edges),
                "tables": len(core["chunked"].get("tables", [])),
                "extraction_time_s": core["extraction_time_s"],
                "images_found": core["images_found"],
            },
            "created_at": datetime.now().isoformat(),
        }
        graphs[gid] = result
        (work_dir / "graph.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        task.update(state="done", pct=100.0, stage="graph", detail="已就绪",
                    stats=result["stats"],
                    redirect=f"./new-graph.html?src={gid}")
        _save_task(task)
    except Exception as exc:
        task.update(state="error", stage="error",
                    error=f"{type(exc).__name__}: {exc}")
        _save_task(task)


@app.post("/api/upload/start")
async def upload_start(
    file: UploadFile = File(...),
    mode: str = Query("local", description="Model backend: local or cloud"),
    max_chars: int = Query(500),
):
    """接收 PDF 并**立即返回**，抽取在后台跑；进度用 /api/upload/status/{gid} 查。"""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")

    gid = uuid.uuid4().hex[:8]
    work_dir = DATA_DIR / gid
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / file.filename
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    UPLOAD_TASKS[gid] = {
        "id": gid, "filename": file.filename, "mode": mode,
        "state": "running", "stage": "queued", "pct": 0.0, "detail": "已入队",
        "started_at": datetime.now().isoformat(),
    }
    asyncio.create_task(_run_upload(gid, pdf_path, work_dir,
                                    file.filename, mode, max_chars))
    return {"id": gid, "state": "running"}


@app.get("/api/upload/status/{gid}")
async def upload_status(gid: str):
    """查询某次上传的进度。state ∈ running / done / error。"""
    t = UPLOAD_TASKS.get(gid)
    if t is None:
        raise HTTPException(404, f"Upload task '{gid}' not found")
    return t


@app.get("/api/upload/tasks")
async def upload_tasks():
    """列出本次进程内所有上传任务（调试用）。"""
    return {"tasks": list(UPLOAD_TASKS.values())}


# ── 人工标记：只落盘，不改图 ────────────────────────────────────────────
# 对应项目功能清单 #3「图谱人工修正」，论文落点是「人机协同质量控制」。
# 刻意**只记录不执行**：删边/合并会改变图规模，而六层评测的全部数字都绑定在
# 当前规模上（README §5 明确要求跨口径比较前先确认口径）。一个"点一下就删边"
# 的界面会让所有已发布指标失去可比性。这里把人工判断落盘成独立文件，
# 供后续审核与批量处理——与 docs/KNOWN_DATA_DEFECTS.md §6 的原则一致。
FLAGS_PATH = QUALITY / "human-flags.json"
FLAG_KINDS = {"wrong_entity", "wrong_relation", "wrong_evidence"}


class FlagRequest(BaseModel):
    node: str
    kind: str
    view: Optional[str] = None
    note: Optional[str] = None


@app.post("/api/flag")
async def add_flag(req: FlagRequest):
    if req.kind not in FLAG_KINDS:
        raise HTTPException(400, f"kind 必须是 {sorted(FLAG_KINDS)} 之一")
    if not req.node.strip():
        raise HTTPException(400, "node 不能为空")

    flags = []
    if FLAGS_PATH.exists():
        try:
            flags = json.loads(FLAGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            flags = []
    flags.append({
        "node": req.node.strip(),
        "kind": req.kind,
        "view": req.view,
        "note": req.note,
        "at": datetime.now().isoformat(),
    })
    QUALITY.mkdir(parents=True, exist_ok=True)
    FLAGS_PATH.write_text(json.dumps(flags, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return {"total": len(flags), "saved_to": str(FLAGS_PATH.relative_to(ROOT))}


@app.get("/api/flags")
async def list_flags():
    """列出已记录的人工标记。**注意：这些标记没有被应用到图上。**"""
    if not FLAGS_PATH.exists():
        return {"total": 0, "applied": False, "flags": []}
    try:
        flags = json.loads(FLAGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        flags = []
    return {"total": len(flags), "applied": False, "flags": flags}


def _ensure_graph(graph_id: str):
    """取图：内存优先，没有就回落到 `data/<id>/graph.json`。

    抽成函数是因为 `/graph/{id}` 与 `/api/qa` **都需要**这个回退 ——
    后端一重启内存就空了，而磁盘上的图还在。`/api/qa` 第一版只查内存，
    重启后直接 404，**而同一时刻 `/graph/ad690dfb` 却能正常返回** ——
    这种"同一个东西，两个端点一个能用一个不能用"最容易骗过测试。
    """
    if graph_id not in graphs:
        disk_path = DATA_DIR / graph_id / "graph.json"
        if not disk_path.exists():
            raise HTTPException(404, f"Graph '{graph_id}' not found")
        try:
            # utf-8-sig 会吃掉 BOM（PowerShell 的 Out-File -Encoding utf8 会写 BOM）
            graphs[graph_id] = json.loads(disk_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise HTTPException(
                500,
                f"Graph '{graph_id}' 的产物文件无法解析"
                f"（{disk_path}）：{type(exc).__name__}: {exc}",
            )
    return graphs[graph_id]


@app.get("/graph/{graph_id}")
async def get_graph(graph_id: str):
    """Retrieve a previously extracted graph by ID.

    内存里没有就回落到 `data/<id>/graph.json`。这让**后端重启后仍能查看
    刚上传的图**（`_run_upload` 完成时会把结果写到那里）。

    两处加固（2026-09-14）：
      · 用 `utf-8-sig` 读 —— 它会自动吃掉 BOM。PowerShell 的
        `Out-File -Encoding utf8` 默认写 BOM，而带 BOM 的 JSON 会让
        `json.loads` 直接失败。这个坑我自己踩过一次。
      · 解析失败时给**明确**的错误而不是裸 500 —— 前端 `new-graph.html`
        会把 detail 显示给用户，说得出"产物损坏"比"取不到"有用得多。
    """
    return _ensure_graph(graph_id)


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

    **2026-09-15 修**：这里原本是 `if req.graph_id not in graphs:` —— **只查内存**。
    而 `_ensure_graph` 的 docstring（见上方）**正是为这个坑写的**：
    「`/api/qa` 第一版只查内存，重启后直接 404，**而同一时刻 `/graph/ad690dfb`
    却能正常返回** —— 这种"同一个东西，两个端点一个能用一个不能用"最容易骗过测试。」
    `/api/qa` 当时已修，**`/chat` 漏了** ⇒ 后端一重启，`/graph/{id}` 200、
    `/chat` 404。现在统一走 `_ensure_graph`。
    """
    graph = _ensure_graph(req.graph_id)
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


@app.post("/api/qa")
async def api_qa(req: QARequest):
    """**多 Agent 问答**：检索 → 支持取证 / 反对取证（并行、互不可见）→ 综合。

    与上面那个 `/chat` 原型的区别，正是它存在的理由：

    · `/chat`  —— 子串匹配 + 邻边拼接，**不调模型**，只回一句边列表；
    · `/api/qa` —— 四个 Agent 分工：检索是**纯规则可复现**的，
      支持方与反对方**各自独立**读同一批证据（不共享中间结论），
      综合方只看到两方交上来的东西。

    **证据一律指向论文原文**：每条都带 `paper`（论文文件名）、`page`、
    `quote`（原文片段）。这一点是刻意的 —— 评审指出旧 ask.html
    「18 条证据全部指向项目自己的文档，0 条指向论文原文」，
    所以这里从数据结构上就不给"引用自己文档"留位置。

    模型用本地 Ollama；不可用时自动降级为纯规则检索，并在结果里
    标 `degraded: true`。
    """
    g = _ensure_graph(req.graph_id)   # 内存优先，带磁盘回退
    try:
        import multi_agent_qa
        return multi_agent_qa.answer(
            req.question, g.get("nodes") or [], g.get("edges") or [],
            use_llm=req.use_llm)
    except ImportError:
        raise HTTPException(500, "multi_agent_qa 模块不可用")


@app.post("/api/qa/step")
async def api_qa_step(req: QAStepRequest):
    """**分步**多 Agent 问答：每一步单独一个请求，界面上就能逐行显示进度。

    `ask.html` 依次调四次：retrieve → support → oppose → synth。
    每步之间无状态，证据由前端回传（见 `QAStepRequest` 的说明）。
    """
    g = _ensure_graph(req.graph_id)
    nodes = g.get("nodes") or []
    edges = g.get("edges") or []
    try:
        import multi_agent_qa as M
    except ImportError:
        raise HTTPException(500, "multi_agent_qa 模块不可用")

    if req.step == 'retrieve':
        return M.step_retrieve(req.question, nodes, edges)
    if req.step in ('support', 'oppose'):
        return M.step_agents(req.question, req.evidence, req.step)
    if req.step == 'synth':
        return M.step_synth(req.question, req.support, req.oppose)
    raise HTTPException(400, f"未知的 step：{req.step}（应为 retrieve/support/oppose/synth）")


@app.get("/stats")
async def stats():
    """Get overall pipeline statistics.

    **2026-09-15 修**：这里原本只累加**内存里已加载过的上传图**（`graphs`），
    没有任何磁盘回退 ⇒ **冷启动返回 `{0,0,0}`**，与 docstring
    「overall pipeline statistics」严重不符 —— 18780 / 31308 这些全库数字
    **从不出现**。

    现在**分两个口径**报，并各自标明含义（这个项目已经吃过多次
    "一个名字两个口径"的亏，见 `docs/METRIC_CALIBERS.md`）：
      · `overview`  —— **概览层全库**（读 `web/kg-data/nebula.json` 的 `meta`）；
      · `uploaded`  —— **本进程内已加载的上传图**（内存态，冷启动就是 0）。
    """
    # 概览层全库：优先读 meta，读不到就给 None（不猜）
    overview = None
    try:
        p = KG_DATA / 'nebula.json'
        if p.exists():
            meta = json.loads(p.read_text(encoding='utf-8')).get('meta') or {}
            overview = {
                'nodes': meta.get('nodes'),
                'edges': meta.get('edges'),
                'communities': meta.get('communities'),
                'source': 'web/kg-data/nebula.json',
            }
    except Exception:
        overview = None

    return {
        # 概览层：全库规模（与界面首屏一致）
        "overview": overview,
        # 本进程内已加载的上传图（冷启动会是 0，这是正常的）
        "uploaded": {
            "graphs_loaded": len(graphs),
            "total_nodes": sum(len(g.get("nodes") or []) for g in graphs.values()),
            "total_edges": sum(len(g.get("edges") or []) for g in graphs.values()),
            "note": "只统计本进程已加载的图；磁盘上的图要靠 /graph/{id} 取，取过才算",
        },
        # 兼容旧字段名（此前只有这三个），但值改为"已加载"口径并保留 note 说明
        "graphs_stored": len(graphs),
        "total_nodes": sum(len(g.get("nodes") or []) for g in graphs.values()),
        "total_edges": sum(len(g.get("edges") or []) for g in graphs.values()),
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
