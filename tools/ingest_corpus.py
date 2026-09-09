"""Batch-ingest the arXiv corpus into Neo4j and per-document JSON snapshots.

Usage: python tools/ingest_corpus.py [corpus_dir]
Resume-safe: papers with an existing snapshot JSON are skipped, so the job
can be interrupted and relaunched.  Each paper's extraction runs through the
same pipeline as POST /upload (mode=local, Ollama).
"""

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipeline import extract_graph  # noqa: E402
from graph_store import GraphStore  # noqa: E402

CORPUS = Path(sys.argv[1] if len(sys.argv) > 1 else "data/corpus")
SNAPSHOTS = CORPUS / "graphs"
SNAPSHOTS.mkdir(parents=True, exist_ok=True)


def main() -> None:
    store = GraphStore(
        backend="neo4j",
        uri="bolt://localhost:7687",
        user="neo4j",
        password="kgdemo2026",
    )
    pdfs = sorted(CORPUS.glob("*.pdf"))
    print(f"ingesting {len(pdfs)} papers from {CORPUS}", flush=True)

    for idx, pdf in enumerate(pdfs, 1):
        paper_id = pdf.stem
        snapshot = SNAPSHOTS / f"{paper_id}.json"
        if snapshot.exists():
            print(f"[{idx}/{len(pdfs)}] skip (done): {paper_id}", flush=True)
            continue

        started = time.time()
        try:
            core = extract_graph(
                str(pdf), str(SNAPSHOTS / f"work-{paper_id}"),
                source_id=paper_id, source_name=pdf.name, mode="local",
            )
            store.store.import_data(core["nodes"], core["edges"])

            snapshot.write_text(
                json.dumps(
                    {
                        "id": paper_id,
                        "source": {"id": paper_id, "name": pdf.name, "kind": "pdf"},
                        "nodes": core["nodes"],
                        "edges": core["edges"],
                        "stats": {
                            "pdf_pages": core["parsed"]["total_pages"],
                            "chunks": core["chunked"]["total_chunks"],
                            "nodes": len(core["nodes"]),
                            "edges": len(core["edges"]),
                            "extraction_time_s": core["extraction_time_s"],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                f"[{idx}/{len(pdfs)}] ok: {paper_id} "
                f"({len(core['nodes'])} nodes, {len(core['edges'])} edges, "
                f"{time.time() - started:.0f}s) total={store.stats()}",
                flush=True,
            )
        except Exception:
            print(f"[{idx}/{len(pdfs)}] FAILED: {paper_id}", flush=True)
            traceback.print_exc()

    print(f"corpus ingest complete: {store.stats()}", flush=True)
    store.close()


if __name__ == "__main__":
    main()
