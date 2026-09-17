"""Batch-ingest the arXiv corpus into Neo4j and per-document JSON snapshots.

Usage: python tools/ingest_corpus.py [corpus_dir] [mode]

Resume-safe: a paper is skipped only when its snapshot contains real content.
Empty snapshots (0 nodes) are treated as poisoned residue -- they are deleted
and the paper is re-processed, because a zero-yield result on a real PDF means
the extraction failed (quota/limit degradation), not that the document is
entity-free.  Snapshots are written only when nodes exist, and each batch
records a batch id so a re-ingest stays traceable.

Default mode is "local" (Ollama); pair with KG_EXTRACT_WORKERS>1 and an Ollama
server started with OLLAMA_NUM_PARALLEL matching it to keep the GPU saturated.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("KG_RECORD_DIR", "data/distill")

from pipeline import ExtractionFailed, extract_graph  # noqa: E402
from graph_store import GraphStore  # noqa: E402

CORPUS = Path(sys.argv[1] if len(sys.argv) > 1 else "data/corpus")
MODE = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("KG_INGEST_MODE", "local")
MAX_CHARS = int(os.environ.get("KG_CHUNK_CHARS", "500"))
# KG_ONLY="1703.08098v9,2012.00456v1" 只处理指定文献（逗号分隔的 stem），用于单篇验证。
ONLY = [s.strip() for s in os.environ.get("KG_ONLY", "").split(",") if s.strip()]
SNAPSHOTS = CORPUS / "graphs"
SNAPSHOTS.mkdir(parents=True, exist_ok=True)
BATCH_ID = os.environ.get("KG_BATCH_ID") or time.strftime("ingest-%Y%m%d-%H%M%S")


def is_usable_snapshot(path: Path) -> bool:
    """快照只有含实体才算"已完成"；空快照是失败残留，不能挡住重跑。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("nodes"))


def purge_poisoned_snapshots() -> int:
    """删除历史遗留的空/损坏快照，让它们不再被当作已完成。"""
    purged = 0
    for snap in sorted(SNAPSHOTS.glob("*.json")):
        if not is_usable_snapshot(snap):
            print(f"[cleanup] 删除空/损坏快照: {snap.name}", flush=True)
            snap.unlink()
            purged += 1
    return purged


def main() -> None:
    # ★★ **backend 必须可配，且默认 networkx**（2026-09-17 修）。
    #
    # 原来这里**硬编码 `backend="neo4j"`** —— 而 **neo4j 早就没在跑了**
    # （实测 7687/7474 均无监听；见 `docs/J8_NO_DOCKER_COMPOSE.md`）。
    # 后果非常具体：
    #   · **B5（prompt A/B 多 seed）六次抽取全部 `rc=1`**，0.1 分钟就失败，
    #     报 `neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7687`；
    #   · 而 **N7 记的"已下载未抽取的 11 篇"**，很可能也是它 ——
    #     **11 篇 PDF 在、图不在**，与"抽取器连不上存储"完全一致。
    #
    # ⚠️ 而 `src/graph_store.py:391` 的默认**本来就是 `networkx`** ——
    # 是这里**显式覆盖**成了 neo4j。**"默认值"与"调用处传的值"不一致时，
    # 要看调用处** —— 这个坑与本轮 H5 的 `dataloader_num_workers` 是同一类。
    #
    # `src/app.py:114` 的做法（`KG_BACKEND` 环境变量 + 回退）是对的，这里对齐它。
    backend = os.environ.get("KG_BACKEND", "networkx")
    if backend == "neo4j":
        store = GraphStore(
            backend="neo4j",
            uri=os.environ.get("KG_NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("KG_NEO4J_USER", "neo4j"),
            password=os.environ.get("KG_NEO4J_PASSWORD", "kgdemo2026"),
        )
    else:
        store = GraphStore(backend="networkx")
    print(f"[ingest] backend={backend}", flush=True)

    # ★★ **单实例锁**（2026-09-17 加）。
    #
    # 为什么要加：**我自己踩了一次** —— 在 B5（prompt A/B 多 seed）正跑抽取时，
    # 又起了一次 `ingest_corpus.py` 想"验证一下默认 backend"。
    # 两次都调 Ollama ⇒ **`llama-server` 的 CPU 计数归零重载**（306s → 180s），
    # ⇒ **正在跑的那次抽取被污染了**（它的上下文/显存被抢）。
    #
    # **而这个错误在输出里看不出来** —— 两边都打印正常的进度。
    # ⇒ 所以必须**在入口挡住**，而不是靠人记得。
    #
    # 实现：一个锁文件，内容写 pid 与启动时间。**不删锁**（进程被杀时锁会留），
    # 所以判定是"pid 还活着吗"，不是"锁文件在不在"。
    LOCK = CORPUS / ".ingest.lock"
    if LOCK.exists():
        try:
            info = json.loads(LOCK.read_text(encoding="utf-8"))
            other = int(info.get("pid") or 0)
        except Exception:
            info, other = {}, 0
        alive = False
        if other:
            try:
                os.kill(other, 0)          # 只探测存在性，不发信号
                alive = True
            except OSError:
                alive = False
        if alive and other != os.getpid():
            print(f"[lock] ❌ **已有抽取在跑**（pid={other}，"
                  f"起于 {info.get('started')}）⇒ 拒绝启动。", flush=True)
            print("[lock]   为什么：两次并发抽取会**互相抢 Ollama**，"
                  "把它重载（`llama-server` CPU 归零），**两边的结果都不可信**。", flush=True)
            print("[lock]   若那个进程已经死了，删掉 "
                  f"{LOCK} 再跑。", flush=True)
            raise SystemExit(2)
    LOCK.write_text(json.dumps(
        {"pid": os.getpid(),
         "started": time.strftime("%Y-%m-%d %H:%M:%S")},
        ensure_ascii=False), encoding="utf-8")

    pdfs = sorted(CORPUS.glob("*.pdf"))
    if ONLY:
        pdfs = [p for p in pdfs if p.stem in ONLY]
        print(f"[filter] KG_ONLY 生效，仅处理 {len(pdfs)} 篇: {[p.stem for p in pdfs]}",
              flush=True)
    print(f"ingesting {len(pdfs)} papers from {CORPUS} "
          f"(mode={MODE}, max_chars={MAX_CHARS}, batch={BATCH_ID})", flush=True)
    print(f"[cleanup] 清理空/损坏快照 {purge_poisoned_snapshots()} 个", flush=True)

    done, failed = 0, 0
    for idx, pdf in enumerate(pdfs, 1):
        paper_id = pdf.stem
        snapshot = SNAPSHOTS / f"{paper_id}.json"
        if snapshot.exists():
            print(f"[{idx}/{len(pdfs)}] skip (done): {paper_id}", flush=True)
            done += 1
            continue

        started = time.time()
        try:
            core = extract_graph(
                str(pdf), str(SNAPSHOTS / f"work-{paper_id}"),
                source_id=paper_id, source_name=pdf.name, mode=MODE,
                max_chars=MAX_CHARS,
            )
            nodes, edges = core["nodes"], core["edges"]
            if not nodes:
                # extract_graph 已保证不为空；这里是第二道闸，防止未来回归。
                raise ExtractionFailed(f"{paper_id}: 抽取产出为空，拒绝写入快照")

            # 先写快照（"这篇已完成"的唯一凭据）再入库；空结果绝不落盘。
            snapshot.write_text(
                json.dumps(
                    {
                        "id": paper_id,
                        "source": {"id": paper_id, "name": pdf.name, "kind": "pdf"},
                        "batch_id": BATCH_ID,
                        "nodes": nodes,
                        "edges": edges,
                        "stats": {
                            "pdf_pages": core["parsed"]["total_pages"],
                            "chunks": core["chunked"]["total_chunks"],
                            "chunk_failures": core.get("chunk_failures", 0),
                            "nodes": len(nodes),
                            "edges": len(edges),
                            "extraction_time_s": core["extraction_time_s"],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            store.store.import_data(nodes, edges)
            done += 1
            print(
                f"[{idx}/{len(pdfs)}] ok: {paper_id} "
                f"({len(nodes)} nodes, {len(edges)} edges, "
                f"{time.time() - started:.0f}s) total={store.stats()}",
                flush=True,
            )
        except Exception:
            failed += 1
            print(f"[{idx}/{len(pdfs)}] FAILED: {paper_id}", flush=True)
            traceback.print_exc()

    print(f"corpus ingest complete: ok={done} failed={failed} stats={store.stats()}",
          flush=True)
    if hasattr(store.store, "close"):
        store.store.close()
    else:
        print("[warn] 后端无 close()，跳过显式关闭", flush=True)


if __name__ == "__main__":
    main()
