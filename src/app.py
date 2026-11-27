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
import json, os, re, uuid, shutil, sys, time
from collections import Counter
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from pdf_parser import parse_pdf  # noqa: F401  (kept for direct API consumers)
from pipeline import extract_graph
from graph_store import GraphStore
from explorer_models import build_detail_window, build_source_summary
from logging_setup import setup as _log_setup, get_logger

# ── 跨盘安全的相对路径（2026-09-16）─────────────────────────────────────
# `Path.relative_to` 在 Windows 上**跨盘会抛 ValueError**
# （`path is on mount 'C:', start on mount 'D:'`）。
# 而本项目的写入目标**允许被重定向**（测试用 tmp_path、计划用 KG_EDIT_PLAN_DIR）
# ⇒ **跨盘是常态而不是例外**。
#
# **这个坑本项目已经踩了三次**：
#   ① `tools/metric_provenance.py`（归档目录被指到 C 盘）
#   ② `tools/apply_edit_plan.py`（计划文件在 pytest 的 C 盘临时目录）
#   ③ `src/app.py` 的 `/api/kg/edit-plan`（同上）
# 三次的症状都是"一个**打印/返回**语句把整个请求搞挂"。
# 实现已在 `tools/_paths.py`，这里接上它，**不在本文件再写一份**。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
from _paths import rel as _rel  # noqa: E402

# ── 日志（2026-09-15 加，J3）──────────────────────────────
# 此前 `src/` 下**零 logging**，只有 44 处 print：没有时间戳、没有级别、不落盘。
# 而本项目**抽一篇要 36 分钟**，中途出错如果不落盘，事后只能靠状态里那一行
# `error` 字符串。这里只做"请求 + 异常"两类记录，**不动那 44 处 print**
# （它们承担的是"给人看的进度"，混进日志反而更乱）。
_log_setup()
log = get_logger('app')

# ── App Setup ────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
KG_DATA = WEB_DIR / "kg-data"
QUALITY = ROOT / "data" / "quality"

# ★ 2026-10-04 收尾时实测发现：**中文响应在 PowerShell 里是乱码**。
#
# 现象：`Invoke-RestMethod http://127.0.0.1:8000/api/qa/step` 拿到的 `agent`
# 字段显示成 `æ£ç´¢ Agent`（"检索 Agent" 的 UTF-8 字节被按 Latin-1 解码）。
# **服务端没错** —— 抓原始字节验过：`e6 a3 80` 就是标准 UTF-8 的"检"。
# 根因是 `Content-Type: application/json` **没带 `charset`**，
# 而 JSON 的 UTF-8 是规范默认、HTTP 的默认却是 ISO-8859-1 ⇒
# 客户端（PowerShell 尤其明显）有权按 Latin-1 解，于是乱码。
#
# **为什么值得在收尾时修**：演示现场如果当场用命令行调一次 API，
# 屏幕上是乱码，会显得整个后端"中文都支持不好" —— 而它其实是好的。
# 显式声明 charset 是零风险的标准做法（浏览器 `fetch`、Python `urllib`
# 都能正确处理带 charset 的 JSON）。
class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="KG Extraction API", version="1.1.0",
              default_response_class=UTF8JSONResponse)
# ★ 2026-09-21 安全加固：此前是 `allow_origins=["*"]`。
# 后果不是理论问题：本服务只监听 127.0.0.1，但**浏览器里的任意网页**都能跨源
# 读走 /api/kg/*、/api/quality/*、/api/manifest 的全部内容，并 POST 到
# /api/papers/trash（移文件）、/api/kg/edit-plan（改图计划）、/api/flag（写标记）。
# 实测响应头就是 `access-control-allow-origin: *`。
# 本服务只可能被本机页面调用 ⇒ 白名单足够，不需要通配。
_ALLOWED_ORIGINS = [
    "http://127.0.0.1:8000", "http://localhost:8000",
    "http://127.0.0.1:8098", "http://localhost:8098",
]
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _access_log(request, call_next):
    """请求日志：方法 / 路径 / 状态 / 耗时。

    **为什么要它**：此前 44 处 print **全在抽取与脚本里**，
    **HTTP 层一条记录都没有** —— 于是"哪个请求 500 了、什么参数"事后查不到。
    这是 36 分钟的抽取任务出问题时最缺的一条线索。

    **刻意只记"摘要"**：不记请求体（上传是二进制 PDF、chat 是用户输入，
    落盘既占地方又可能带隐私）。要定位具体输入时，配合调用方自己的日志。
    """
    import time as _t
    t0 = _t.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        # 让 FastAPI 的异常处理照常走，但**这里先记一笔带栈的**
        log.exception('请求异常 %s %s', request.method, request.url.path)
        raise
    ms = (_t.perf_counter() - t0) * 1000
    lvl = log.warning if resp.status_code >= 500 else (
        log.info if resp.status_code >= 400 else log.debug)
    lvl('%s %s -> %s (%.0fms)', request.method, request.url.path,
        resp.status_code, ms)
    return resp


# Store graphs in memory + on disk
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
# `graph_id` 会被拼进文件路径（见 `_ensure_graph`），所以先卡一道白名单：
# 只允许字母/数字/点/下划线/连字符，首字符不能是点。配合 `..` 检查挡住穿越。
_GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# 上传上限。演示用的论文 PDF 都在几十 MB 以内，200 MB 足够宽松，
# 而它挡掉的是"一个请求把磁盘写满"这种最低成本的破坏。
MAX_UPLOAD_BYTES = 200 * (1 << 20)


def _safe_pdf_name(name: str | None) -> str:
    """把**客户端给的文件名**变成一个只能落在目标目录里的纯文件名。

    为什么不能直接用 `Path(name).name`：Windows 上反斜杠也是分隔符，
    而 `PurePosixPath` 只按正斜杠切分，会原样保留反斜杠。
    所以先把反斜杠归一成正斜杠，再取最后一段，最后再剥一次 `..`。

    返回空串或剥完仍可疑时，退回一个固定名 —— **宁可覆盖成固定名，
    也不能让路径由客户端决定**。
    """
    raw = (name or "").replace("\\", "/")
    base = raw.rsplit("/", 1)[-1].strip()
    if not base or base in (".", "..") or ".." in base:
        return "upload.pdf"
    return base


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
    # G4（2026-09-16）：**限制问题长度**。此前没有任何上限 ——
    # 一个几万字符的问题会一路拼进 prompt，轻则变慢、重则撑爆上下文。
    # 500 字符的依据：`data/distill/` 里 prompt 的中位长度是 1207 字符，
    # 而那是"论文片段 + 规则"，**用户的问题**通常远短于它。
    question: str = Field(max_length=500)
    use_llm: bool = True
    # K7 多轮追问：历史由前端回传（后端不存 session）。
    # 形如 `[{"question": "...", "answer": "..."}]`，**只用于补全没主语的追问**。
    history: list[dict] = []


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
    # G4：与 `QARequest` 同一个上限（500 字符）
    question: str = Field(max_length=500)
    step: str = 'retrieve'
    evidence: list[dict] = []
    support: str = ''
    oppose: str = ''
    # K7 多轮追问（2026-09-15）。**历史由前端回传，后端不存 session** ——
    # 与 `evidence`/`support`/`oppose` 同一个设计理由（见上方的 docstring）：
    # 回传几十 KB 比维护会话状态安全得多，而这个项目在上传任务上已经栽过
    # 一次"后端重启丢任务"。
    #
    # ⚠️ **它只喂给检索的"问题补全"，不进检索算法本身**（见
    # `multi_agent_qa.rewrite_question` 的说明）：没有主语的追问
    # （「那它的缺点呢？」）字面匹配不到任何实体，检索会空转。
    history: list[dict] = []


class QAGlobalStepRequest(BaseModel):
    """**全库问答**（GraphRAG 式 Global Search）的一步请求 —— **不要求 `graph_id`**。

    ## 为什么是一个新模型 / 新端点，而不是给 `QAStepRequest` 加个 `mode` 字段

    单篇问答的 `graph_id` 是**必填**的，而全库问答**没有**"哪张图"这回事 ——
    它检索的是 B6 生成的 **18 条社区摘要**（`web/kg-data/community-summaries.json`）。
    把 `graph_id` 改成可选，等于让"这张图不存在"（404）与"没指定图"（全库）
    在同一个端点上混淆；而 `ask.html` 的既有行为又有 44 项测试盯着。
    ⇒ **新端点、新模型，老端点一个字节不改。**

    `top_k`：全库检索取分数最高的几个社区（默认 4）。
    上限 8 —— 每条摘要 300~900 字，取太多会把 prompt 撑过本地模型的上下文，
    而"超上下文被静默截断"是最危险的失败方式（模型没看到，却像看到了一样回答）。
    """
    # 与 `QARequest` / `QAStepRequest` 同一个上限（500 字符，G4）
    question: str = Field(max_length=500)
    step: str = 'retrieve'
    top_k: int = Field(default=4, ge=1, le=8)
    evidence: list[dict] = []
    support: str = ''
    oppose: str = ''
    history: list[dict] = []


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
        # 不回显仓库绝对路径（`D:\kg-demo\web` 这类信息对访问者没有用，
        # 却把"这个仓库在哪、目录结构长什么样"直接送出去）
        "web_dir": "web",
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


def _kg_file(name: str) -> Path:
    """数据面的文件定位：web/kg-data/<name>，缺失时回退 web/kg-data/demo/<name>。

    **为什么需要回退**：本仓库是精简版，不带全量图谱（nebula.json 约 3.4 MB、
    每个社区的 evidence-*.json 等）。没有回退时，十几个页面只能显示「数据加载失败」。
    示例数据放在 `web/kg-data/demo/`，规模被裁过，不代表真实评测结果 ——
    每个文件带 `_demo: true` 与 `_demo_note`，该目录另有 README.md。
    """
    p = KG_DATA / name
    if p.exists():
        return p
    demo = KG_DATA / "demo" / name
    return demo if demo.exists() else p


@app.get("/api/kg/{name}")
async def api_kg(name: str):
    """前端数据面（web/kg-data/<name>.json，缺失时用 demo/ 示例数据）。"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    return _serve_json(_kg_file(name))


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


# ── K12（2026-09-16）：界面上的「当场跑一遍评测」───────────────────────
#
# ## 为什么做这个
#
# TODO K12 的原话：
# > 项目有 9 层评测 + 45 项回归，**但全在命令行**。
# > 老师问"你自己怎么验证的"，**最好的答案是当场点一下跑出来**。
#
# ## 为什么只跑"数据侧"
#
# `verify_all` 的后半（`CHECKS`）每一项都要**起一个无头 Chrome**。
# 从后端进程里拉 Chrome 既慢（每项 3–35 秒）又依赖桌面环境 ——
# 而界面上要的是**快、稳、当场看得见**。
# ⇒ 只跑 `--data-only`（数据侧那些，不依赖浏览器），**完整回归仍在命令行跑**。
#
# ## 为什么是"后台线程 + 轮询"而不是直接返回
#
# 数据侧 20 项实测**约 60 秒** —— 一个 HTTP 请求挂 60 秒会被各种超时掐掉，
# 而且界面在这 60 秒里**什么都看不到**。
# ⇒ 与上传同一套模式：`start` 立刻返回，`status` 供轮询。
#
# ## 为什么**不**持久化
#
# 上传任务要持久化（跑到一半重启不能丢，那是一次真实的数据丢失教训）。
# **而这个不需要**：它是一段**只读的检查**，重启后结果就过期了 ——
# **重跑一次的成本（60 秒）比"恢复一个可能过期的结果"更低，也更诚实。**
VERIFY_TASK: dict = {
    "running": False, "started": None, "finished": None,
    "passed": None, "total": None, "results": None, "error": None,
}


def _run_verify():
    """在**后台线程**里跑 `verify_all --data-only --json`。"""
    import subprocess
    global VERIFY_TASK
    try:
        p = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "verify_all.py"),
             "--data-only", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, cwd=str(ROOT))
        # **只解析最后一行** —— 前面是各检查自己打的日志
        last = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]
        payload = json.loads(last[-1]) if last else {}
        VERIFY_TASK.update({
            "running": False, "finished": datetime.now().isoformat(timespec="seconds"),
            "passed": payload.get("passed"), "total": payload.get("total"),
            "results": payload.get("results"), "error": None,
        })
        _LOG = log        # 本模块的 logger 名就叫 `log`（L66）
        _LOG.info("[verify] 完成 %s/%s", payload.get("passed"), payload.get("total"))
    except Exception as exc:                      # noqa: BLE001
        # **失败要如实说出来** —— 不能留一个永远 running 的任务
        VERIFY_TASK.update({"running": False, "error": f"{type(exc).__name__}: {exc}",
                            "finished": datetime.now().isoformat(timespec="seconds")})
        log.exception("[verify] 失败")


@app.post("/api/verify/start")
async def verify_start():
    """起一次数据侧检查（立刻返回；用 `status` 轮询）。

    **已经在跑就不重复起** —— 返回 `already_running: true`，
    而不是静默地再起一个（那会让两次结果互相覆盖，且白占 CPU）。
    """
    import threading
    if VERIFY_TASK.get("running"):
        return {"running": True, "already_running": True,
                "started": VERIFY_TASK.get("started")}
    VERIFY_TASK.update({"running": True, "error": None, "results": None,
                        "passed": None, "total": None,
                        "started": datetime.now().isoformat(timespec="seconds"),
                        "finished": None})
    threading.Thread(target=_run_verify, daemon=True).start()
    return {"running": True, "already_running": False,
            "started": VERIFY_TASK["started"]}


@app.get("/api/verify/status")
async def verify_status():
    """轮询用。返回 `running` / `passed` / `total` / `results` / `error`。"""
    return VERIFY_TASK


# ── K2（2026-09-17）：论文管理 ────────────────────────────────────────
#
# TODO 的原话：论文管理（已入库列表 / 删除 / 元数据）——
# **"我传过哪些"的真相散在三处**，而**删除**是这里唯一危险的动作。
#
# ## ★★ 删除**永不真删**（这是这一块的全部设计）
#
# `ARCHIVE_POLICY`：被替代的东西不删。而删除**用户自己的数据**比"替代一个实现"
# 严重得多 —— 点错一下，**几十分钟的本地抽取就没了**（C3 那次还遇到 500）。
# ⇒ 所以这里的"删除"只有一种语义：**移到 `data/_trash/` 并留一张 `_why.json`。**
#
# ## ⚠️ 为什么要求 `why` 非空
#
# 回收站的文件名带时间戳，但**"为什么删"只有人知道**。
# 不强制填，一周后就是一堆 `20260917-142457-upload-ad6xxxx`，
# **没人知道哪个能删**。⇒ 强制填写，是让回收站**在未来仍然可用**。
#
# ## ⚠️ 三个 kind 的危险程度不同
#
# `upload`（一次上传）最安全；`corpus_graph` 会让那篇从图谱里消失；
# **`corpus_pdf` 最危险** —— 它让已入库的图**失去可核对的原文**
# （证据深链会断）。⇒ 前端对后两者要求**输入 id 二次确认**。


class TrashRequest(BaseModel):
    kind: str                    # upload | corpus_graph | corpus_pdf
    # ★ 2026-09-21：`id` 会被 `tools/papers_admin.py` 直接拼进路径
    # （`DATA / pid`、`TRASH / f'{stamp}-{kind}-{id}'`），而它此前**完全没有校验** ——
    # 这是本项目唯一一处"写操作可穿越"的入口。规则与 `_GRAPH_ID_RE` 一致。
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    why: str                     # **必填**（见上面的理由）


@app.get("/api/papers/registry")
async def papers_registry():
    """K2 的数据面：论文注册表（由 `tools/build_paper_registry.py` 生成）。

    ⚠️ 与 `kg-api.js` 的静态回退一致：产物在 `web/kg-data/`，所以静态站也能取。
    """
    return _serve_json(_kg_file('paper-registry.json'))


@app.get("/api/papers/trashcan")
async def papers_trashcan():
    """回收站里有什么（每项带原路径、字节数、原因）。"""
    t = DATA_DIR / '_trash'
    items = []
    if t.exists():
        for d in sorted(t.iterdir()):
            if not d.is_dir():
                continue
            wf = d / '_why.json'
            info = {}
            if wf.exists():
                try:
                    info = json.loads(wf.read_text(encoding='utf-8'))
                except Exception:
                    info = {'note': '_why.json 读失败'}
            items.append({'name': d.name, **info})
    return {'count': len(items), 'items': items}


@app.post("/api/papers/trash")
async def papers_trash(req: TrashRequest):
    """把一项移入回收站（**永不真删**）。前端负责二次确认。"""
    if not (req.why or '').strip():
        raise HTTPException(400, '必须填"为什么删" —— 否则回收站一周后就没人看得懂了')
    if req.kind not in ('upload', 'corpus_graph', 'corpus_pdf'):
        raise HTTPException(400, f'kind 必须是 upload / corpus_graph / corpus_pdf')
    # **不在这里重实现一遍移动逻辑** —— 直接调 `tools/papers_admin.py`，
    # 那样 CLI 与 API **走同一条路径**（两边行为不一致是这类功能最常见的坑）。
    import subprocess          # 顶层没 import（`_run_verify` 里也是局部 import）
    p = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'papers_admin.py'),
         'trash', req.kind, req.id, '--why', req.why],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=120, cwd=str(ROOT))
    out = (p.stdout or '') + (p.stderr or '')
    log.info('[papers] trash %s %s ⇒ %s', req.kind, req.id, p.returncode)
    return {'ok': p.returncode == 0, 'output': out[-1500:],
            'returncode': p.returncode}


@app.post("/api/papers/restore")
async def papers_restore(req: dict):
    """从回收站恢复（按目录名）。"""
    name = str((req or {}).get('name') or '')
    if not name or '/' in name or '\\' in name or '..' in name:
        raise HTTPException(400, '非法的回收站目录名')
    import subprocess
    p = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'papers_admin.py'), 'restore', name],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=120, cwd=str(ROOT))
    out = (p.stdout or '') + (p.stderr or '')
    return {'ok': p.returncode == 0, 'output': out[-1500:],
            'returncode': p.returncode}


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
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files supported")

    # Save uploaded file
    gid = uuid.uuid4().hex[:8]
    upload_dir = DATA_DIR / gid
    upload_dir.mkdir(parents=True, exist_ok=True)
    # ★ 2026-09-21：**文件名必须净化**。`file.filename` 是客户端给的字符串，
    # 而 Starlette 0.52.1 的 `formparsers.py` 只做 `_user_safe_decode`、
    # **不剥离 `..` 与 `\`**（实测包内 0 处 `basename`/`PurePath`）。
    # 原样拼路径时，一个 `filename="..\..\..\evil.pdf"` 就能写到 `data/` 之外。
    pdf_path = upload_dir / _safe_pdf_name(file.filename)
    # 顺手加大小上限：此前没有任何限制，一个超大文件就能把磁盘写满
    # （上传是演示主线，这条一定会被走到）。
    written = 0
    with open(pdf_path, "wb") as f:
        while True:
            chunk = file.file.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    413, f"PDF 超过上限 {MAX_UPLOAD_BYTES // (1 << 20)} MB")
            f.write(chunk)

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


def _save_with_sha1(file, pdf_path):
    """边写盘边算 sha1（不为了哈希重读一遍文件）。

    返回前 16 位十六进制。**G2（2026-09-16）新增** ——
    它用于"同一篇论文传了两次"的查重。
    """
    import hashlib
    h = hashlib.sha1()
    with open(pdf_path, "wb") as f:
        while True:
            chunk = file.file.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            f.write(chunk)
    return h.hexdigest()[:16]


def _find_by_sha1(digest):
    """在已有上传目录里找**同一个 PDF**（且已经抽完的）。

    ## 为什么需要它（G2）

    `upload_start` 原先每次都 `uuid.uuid4().hex[:8]` ⇒
    **同一篇论文传两次会得到两个互不相识的 gid**，
    界面上看起来是两篇不同的东西，而用户会问"我传了两次为什么有两份"。

    ## 判据

    `data/<task_id>/<同名 PDF>` 的 sha1 相同 **且该目录已有 `graph.json`**
    ⇒ 说明这篇已经抽完了，**直接复用**，不必再花 36 分钟重抽一遍。
    （只查"已完成"的：跑到一半的任务不该被复用，那会让用户以为它已经好了。）

    返回 `task_id` 或 `None`。
    """
    if not DATA_DIR.exists():
        return None
    import hashlib
    for d in DATA_DIR.iterdir():
        if not d.is_dir() or not (d / "graph.json").exists():
            continue
        for p in d.glob("*.pdf"):
            try:
                h = hashlib.sha1()
                with open(p, "rb") as f:
                    while True:
                        c = f.read(1 << 20)
                        if not c:
                            break
                        h.update(c)
                if h.hexdigest()[:16] == digest:
                    return d.name
            except Exception:
                continue
    return None


@app.post("/api/upload/start")
async def upload_start(
    file: UploadFile = File(...),
    mode: str = Query("local", description="Model backend: local or cloud"),
    max_chars: int = Query(500),
):
    """接收 PDF 并**立即返回**，抽取在后台跑；进度用 /api/upload/status/{gid} 查。

    **G2（2026-09-16）**：同一个文件传第二次时**复用已有的 gid**，
    并在返回里标明 `reused: true` ——
    否则界面上会出现两个互不相识的图，而用户不知道它们是同一篇。
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")

    gid = uuid.uuid4().hex[:8]
    work_dir = DATA_DIR / gid
    work_dir.mkdir(parents=True, exist_ok=True)
    # 同 `/upload`：客户端给的文件名必须净化后再拼路径（见那边的注释）
    pdf_path = work_dir / _safe_pdf_name(file.filename)
    digest = _save_with_sha1(file, pdf_path)

    # 查重：已经抽完的同一篇 ⇒ 复用，并把刚写下的临时目录清掉
    existing = _find_by_sha1(digest)
    if existing and existing != gid:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
        log.info(f'upload: 同一份 PDF 已存在（sha1 {digest}）⇒ 复用 {existing}')
        return {
            "id": existing, "state": "done", "reused": True,
            "sha1_16": digest,
            "note": '这份 PDF 此前已上传并抽完，**直接复用那次的结果**，没有重抽。'
                    '（此前每次都生成新 gid，同一篇会变成两个互不相识的图。）',
        }

    UPLOAD_TASKS[gid] = {
        "id": gid, "filename": file.filename, "mode": mode, "sha1_16": digest,
        "state": "running", "stage": "queued", "pct": 0.0, "detail": "已入队",
        "started_at": datetime.now().isoformat(),
    }
    asyncio.create_task(_run_upload(gid, pdf_path, work_dir,
                                    file.filename, mode, max_chars))
    return {"id": gid, "state": "running", "reused": False, "sha1_16": digest}


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
    return {"total": len(flags), "saved_to": str(_rel(FLAGS_PATH))}


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


# ── K1 改图：把「人工判断」变成可执行的计划（2026-09-16）─────────────────
#
# 【为什么要有这一步】上面 `/api/flag` 的 docstring 写着
# 「**这些标记没有被应用到图上**」—— 那是有意的（改图会改变全库实体数、
# 从而打破六层指标的可比性）。但"只记录不执行"意味着**用户的操作没有出口**。
#
# 【K1 的解法】沿用项目已有的 `plan → apply` 先例
# （`data/quality/venue-rewrite-plan.json` 只出计划、由 `apply_*.py` 执行）：
#   · **本端点只写计划文件**（秒回，不碰图数据）；
#   · **执行交给 `tools/apply_edit_plan.py`** —— 它会在覆盖前归档指标（N1）、
#     快照真源、改完重跑 6 个下游、再归档一次并打 before/after diff。
#
# 【为什么不在端点里直接改】改图要几小时机器时间（重跑下游），
# 而且**必须有人看过 before/after 再决定是否保留**。把执行放在批处理里，
# 前端这半天的工作量就与机器时间解耦了。
EDIT_PLAN_DIR = QUALITY / "edit-plans"
EDIT_OPS = ("rename_entity", "merge_entities", "delete_edge", "retag_edge")


def _edit_plan_dir():
    """**每次调用时读环境变量**，而不是在 import 时读一次。

    ⚠️ **这是被测试逼出来的**：`tests/test_edit_plan_api.py` 单独跑 6 passed、
    但和别的测试一起跑就失败 —— 因为 `app` 早已被别的测试 import 过，
    模块级常量已经定型，`monkeypatch.setenv` 再改也没用。

    **这同时是个真实的可用性问题**：任何人想在**同进程内**切换计划目录
    （测试、或将来按项目分目录）都会静默失效 —— 而"静默失效"正是本项目
    最想避免的一类缺陷。所以改成函数，**每次请求时算**。
    """
    d = os.environ.get("KG_EDIT_PLAN_DIR")
    return Path(d) if d else (QUALITY / "edit-plans")


class EditOperation(BaseModel):
    """一条改图操作。字段按 `op` 取用 —— 与 `tools/apply_edit_plan.py` 一致。"""
    op: str
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    into: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None
    type: Optional[str] = None
    to_type: Optional[str] = None
    reason: Optional[str] = None

    model_config = {"populate_by_name": True}


class EditPlanRequest(BaseModel):
    """一份改图计划。**端点只把它落盘，不改图。**"""
    operations: list[EditOperation] = []
    note: Optional[str] = None
    generated_by: Optional[str] = "web"


@app.post("/api/kg/edit-plan")
async def submit_edit_plan(req: EditPlanRequest):
    """**写一份改图计划文件**（不执行）。

    返回里给出**下一步该跑的命令** —— 否则前端提交完就"没有出口"了，
    那正是这个端点要解决的问题。
    """
    ops = [o.model_dump(by_alias=True, exclude_none=True) for o in req.operations]
    if not ops:
        raise HTTPException(400, "operations 不能为空")
    bad = sorted({o.get("op") for o in ops} - set(EDIT_OPS))
    if bad:
        raise HTTPException(400, f"未知操作 {bad}；允许 {list(EDIT_OPS)}")
    # 逐条做最小的必填校验 —— 让错误在**写文件之前**暴露
    for i, o in enumerate(ops, 1):
        k = o["op"]
        need = {
            "rename_entity": ("from", "to"),
            "merge_entities": ("from", "into"),
            "delete_edge": ("source", "target"),
            "retag_edge": ("source", "target", "to_type"),
        }[k]
        miss = [f for f in need if not o.get(f)]
        if miss:
            raise HTTPException(400, f"第 {i} 条 {k} 缺字段 {miss}")

    plan_dir = _edit_plan_dir()          # ← 每次请求时算，见该函数的说明
    plan_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = plan_dir / f"edit-{ts}.json"
    n = 2
    while out.exists():                       # **不覆盖已有计划**
        out = plan_dir / f"edit-{ts}-{n}.json"
        n += 1

    out.write_text(json.dumps({
        "generated_by": req.generated_by or "web",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": req.note or "",
        "method": "记录而非立即改图 —— 改图会改变图规模，必须先归档指标（N1）",
        "operations": ops,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    rel = str(_rel(out))
    return {
        "saved_to": rel,
        "operations": len(ops),
        "applied": False,
        "next": [
            f"python tools/apply_edit_plan.py {rel}            # 看影响（dry-run）",
            f"python tools/apply_edit_plan.py {rel} --apply    # 真的改（含 before/after 归档）",
        ],
        "note": "改图会重跑 6 个下游并归档指标；执行前请先看 dry-run 的影响条数。",
    }


def _ensure_graph(graph_id: str):
    """取图：内存优先，没有就回落到磁盘上的图产物。

    抽成函数是因为 `/graph/{id}` 与 `/api/qa` **都需要**这个回退 ——
    后端一重启内存就空了，而磁盘上的图还在。`/api/qa` 第一版只查内存，
    重启后直接 404，**而同一时刻 `/graph/ad690dfb` 却能正常返回** ——
    这种"同一个东西，两个端点一个能用一个不能用"最容易骗过测试。

    ★ 2026-09-21 修：**磁盘上有两种产物路径约定，此前只认了第一种**，
    于是"上传后能问、直接问就 404"：

        ① `data/<id>/graph.json`              —— 上传流程写的（实测 3 张）
        ② `data/corpus/graphs/<id>.json`      —— 语料预置的（实测 21 张）

    只认 ① 的后果是：**语料里已入库的 21 篇一篇都问不了**，`/api/qa` 一律回
    `{"detail":"Graph 'xxx' not found"}`，而同一时刻 `/api/kg/...` 明明能读到
    这篇图的数据 —— 与上面那句 docstring 里记的老坑是同一类病（两条约定打架）。
    现在两种都认，并把"找过哪些路径"写进 404 的响应里，下次再出这种事不用猜。

    ⚠️ `graph_id` 是**客户端给的字符串**，要拼进文件路径，所以先做白名单校验：
    只允许字母/数字/点/下划线/连字符，且不含 `..`。否则 `/api/qa` 的 `graph_id`
    就成了一个可以往上跳目录的入口。
    """
    if not _GRAPH_ID_RE.match(graph_id or "") or ".." in graph_id:
        raise HTTPException(400, f"非法的 graph_id：{graph_id!r}")
    if graph_id not in graphs:
        candidates = [
            DATA_DIR / graph_id / "graph.json",
            DATA_DIR / "corpus" / "graphs" / f"{graph_id}.json",
        ]
        disk_path = next((p for p in candidates if p.exists()), None)
        if disk_path is None:
            raise HTTPException(
                404,
                f"Graph '{graph_id}' not found"
                # 只回显相对路径（`_rel` 是本文件已有的工具）：绝对路径会把
                # 仓库位置与目录结构写进错误响应里，而这对排查没有任何必要。
                f"（找过：{'、'.join(str(_rel(p)) for p in candidates)}）",
            )
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
    + 上限 60 节点的子图**，结果可复现、可解释，且不依赖模型。
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
            use_llm=req.use_llm, history=req.history)
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
        return M.step_retrieve(req.question, nodes, edges, history=req.history)
    if req.step in ('support', 'oppose'):
        return M.step_agents(req.question, req.evidence, req.step)
    if req.step == 'synth':
        return M.step_synth(req.question, req.support, req.oppose)
    raise HTTPException(400, f"未知的 step：{req.step}（应为 retrieve/support/oppose/synth）")


@app.get("/api/communities")
async def api_communities():
    """**全库问答的料在哪**：B6 生成的社区摘要清单（轻量，不含正文）。

    前端在全库模式下用它显示"全库 = N 个社区摘要（由某个本地模型在某天生成了）"，
    这样用户（和演示老师）能一眼看出这一模式的证据**不是论文原文**，
    而是"每个社区一段概括"。**清单里不带摘要正文** —— 正文随检索结果按需下发。
    """
    try:
        import multi_agent_qa as M
    except ImportError:
        raise HTTPException(500, "multi_agent_qa 模块不可用")
    return M.community_catalog()


@app.post("/api/qa/global/step")
async def api_qa_global_step(req: QAGlobalStepRequest):
    """**全库问答**（GraphRAG 式 Global Search）的分步端点。

    `ask.html` 在「全库」模式下依次调四次：retrieve → support → oppose → synth，
    与单篇模式**同一套四步链路**，差别只在证据来自哪里：

      · 单篇模式（`/api/qa/step`）：检索**某一张图的实体与边**（2 跳 BFS 子图）；
      · 全库模式（本端点）        ：检索 **B6 的社区摘要**（词面匹配 top-K）。

    ## 三条刻意的边界

    · **不需要 `graph_id`**：全局问题不落在任何一篇论文上（见 `QAGlobalStepRequest`）。
    · **检索零模型**：社区选取是纯规则打分，**不依赖 Ollama 是否在跑**
      —— 全库模式的"找到哪些社区"永远可复现；模型只用在后三步的判断上。
    · **证据带 `level='community'`**：社区摘要是**生成的概括**，不是论文原文，
      所以它的 `citations` 恒为空、`has_quote` 恒为 false，
      支持/反对/综合三步换用社区版 prompt（原 prompt 写着"每条都来自论文原文"）。
      这一条对应项目纪律：**不要把结构推断说成事实**。
    """
    try:
        import multi_agent_qa as M
    except ImportError:
        raise HTTPException(500, "multi_agent_qa 模块不可用")

    if req.step == 'retrieve':
        return M.step_retrieve_global(req.question, top_k=req.top_k,
                                      history=req.history)
    if req.step in ('support', 'oppose'):
        # 证据的级别由证据自身的 `level` 字段决定，不由本端点传参（见 step_agents）
        return M.step_agents(req.question, req.evidence, req.step)
    if req.step == 'synth':
        return M.step_synth(req.question, req.support, req.oppose, level='community')
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


# ── H4 语义兜底（2026-09-16）──────────────────────────────────────────────
#
# TODO 里 H4 的第二个用途：「**PathQA 端点匹配失败时语义召回兜底**」。
# PathQA 在前端做"字面种子匹配 + 2 跳 BFS"，**字面匹配不到时现在就是"检索不到"**；
# 语义兜底 = 拿问题里的词去和全库实体名比余弦，找回最像的几个。
#
# ## 两个刻意的设计
#
# ① **向量预计算 + 惰性加载**：全库 1.9 万个实体名，现算一次要 6 分钟。
#    所以嵌入离线算好存成 `.npz`（`tools/build_entity_embeddings.py`），
#    首次请求时载入内存并缓存。**没有那个文件时端点如实报"未就绪"**，
#    不静默返回空 —— 前端要能区分"没找到"和"功能没开"。
#
# ② **必须给最低分阈值**（`min_score`）：H4 的标定实验结论是
#    **"余弦的中间区不可判"**（该拒的最高分 0.85 > 该合的最低分 0.41）。
#    所以这里**不返回"最像的那个"** —— 返回一个 0.3 分的候选比返回空更危险，
#    调用方会以为"找到了"。默认 `min_score=0.5`，低于它的一律不返回。
#    （详见 `docs/H4_EMBEDDING_RESULT.md`。）
ENTITY_EMB = QUALITY / "entity-embeddings.npz"
_EMB_CACHE = {"mtime": None, "names": None, "vectors": None}


class SemanticMatchRequest(BaseModel):
    query: str
    top_k: int = 5
    # 低于这个余弦一律不返回（**不要改成 0** —— 见上面的 ②）
    min_score: float = 0.5


def _load_entity_embeddings():
    """惰性加载 `.npz`，按 mtime 缓存。返回 `(names, vectors)` 或 `(None, None)`。"""
    import numpy as np
    if not ENTITY_EMB.exists():
        return None, None
    mtime = ENTITY_EMB.stat().st_mtime
    if _EMB_CACHE["mtime"] != mtime:
        try:
            z = np.load(ENTITY_EMB, allow_pickle=True)
            _EMB_CACHE.update(mtime=mtime, names=list(z["names"]),
                              vectors=z["vectors"])
        except Exception as e:
            log.warning(f'实体嵌入载入失败：{type(e).__name__}: {e}')
            return None, None
    return _EMB_CACHE["names"], _EMB_CACHE["vectors"]


@app.post("/api/semantic-match")
async def api_semantic_match(req: SemanticMatchRequest):
    """**字面匹配失败时的语义兜底**：把 query 和全库实体名比余弦。

    与 `/api/qa/step` 的检索**不是一回事**：那个是"字面种子匹配 + BFS"（可复现、
    不依赖模型）；这个是**在字面匹配不到时才用的宽松召回**，
    所以它返回的是**候选（带分数）**，不是"答案"。
    """
    import numpy as np
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(400, "query 不能为空")

    names, vectors = _load_entity_embeddings()
    if names is None:
        # **如实报"未就绪"**，而不是返回空数组让前端以为"没找到"
        return {"ready": False, "matches": [],
                "note": f"实体嵌入未生成：先跑 tools/build_entity_embeddings.py"
                        f"（期望产物 {_rel(ENTITY_EMB)}）"}

    try:
        # `embeddings.py` 与本文件同在 `src/`，直接 import
        from embeddings import embed
        qv = embed([q])[0]
    except Exception as e:
        raise HTTPException(503, f"嵌入服务不可用（Ollama 在跑吗）：{type(e).__name__}")

    # 预计算时已归一化 ⇒ 余弦 = 点积
    qa = np.asarray(qv, dtype=np.float32)
    n = float(np.linalg.norm(qa)) or 1.0
    sims = (vectors @ (qa / n)).astype(float)

    k = max(1, min(int(req.top_k or 5), 50))
    idx = np.argsort(-sims)[:k]
    out = [{"name": names[i], "score": round(float(sims[i]), 4)}
           for i in idx if float(sims[i]) >= float(req.min_score or 0)]

    return {
        "ready": True,
        "query": q,
        "matches": out,
        "min_score": req.min_score,
        "n_candidates": len(names),
        "note": ("**候选，不是答案** —— 余弦的中间区不可判（该拒的最高分 0.85 > "
                 "该合的最低分 0.41），所以只返回 >= min_score 的。"
                 "见 docs/H4_EMBEDDING_RESULT.md。"),
    }


# ── Main ─────────────────────────────────────────────────

# 语料 PDF：让**证据深链**（点证据行 → 打开原文那一页并高亮）在后端下真的可用。
#
# **为什么必须显式挂**（2026-09-20 修）：`web/kg-nebula.html` 的 PDF 回退链是
#
#     [ `./l5-data/${src}`, `../data/corpus/${src}` ]
#
# 该文件的注释写着「静态站能跑（有哪篇就开哪篇）、**后端在时全都能跑**」——
# 但下面只挂了 `web/`，**`data/` 从未暴露** ⇒ 在正式演示用的 8000 上，
# `../data/corpus/xxx.pdf` 同样 404。后果：**只有被预先放进 `web/l5-data/`
# 的那几篇 PDF 打得开**，点别的论文的证据就开不出原文 ——
# 而这正是演示要看的那一条链（是补跑 `verify_all` **全量**模式才发现的：
# `--fast` 跳过的「证据深链 PDF 高亮」这一项，此前一直没跑过）。
#
# **只挂 `corpus/`，不挂整个 `data/`**：`data/quality/` 是评测中间产物，
# 演示不需要，少暴露一个面。挂载路径与前端写的 `../data/corpus/` 逐字对应。
_CORPUS_DIR = ROOT / "data" / "corpus"
if _CORPUS_DIR.exists():
    app.mount("/data/corpus", StaticFiles(directory=str(_CORPUS_DIR)), name="corpus-pdf")

# 把 web/ 挂在同一个 origin 下（必须在所有 API 路由**之后**挂载，
# 否则 StaticFiles 的根路由会吃掉 /health、/api/* 这些前缀）。
# 收益：① 前端与 API 同源，消除 CORS 与 file:// 限制；
#       ② 只需起一个进程就能同时提供界面与数据面，演示时少一个失败点。
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
