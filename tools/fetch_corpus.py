"""Fetch an open-access arXiv corpus of KG/LLM survey PDFs for M1 ingestion.

Usage: python tools/fetch_corpus.py [target_count]
Saves PDFs to data/corpus/ and metadata to data/corpus/_manifest.json.
Polite fetching: one request per file, 3s sleep between downloads.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path("data/corpus")
NS = {"a": "http://www.w3.org/2005/Atom"}

QUERIES = [
    'all:"knowledge graph" AND all:"survey"',
    'all:"large language model" AND all:"knowledge graph" AND all:"survey"',
    'all:"knowledge graph construction" AND all:"large language model"',
    'all:"graph retrieval augmented generation"',
    'all:"knowledge graph completion" AND all:"survey"',
]

MAX_RESULTS_PER_QUERY = 12


def search(query: str) -> list[dict]:
    url = (
        "https://export.arxiv.org/api/query?search_query="
        + urllib.parse.quote(query)
        + f"&max_results={MAX_RESULTS_PER_QUERY}&sortBy=relevance"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "kg-demo-corpus/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())
    papers = []
    for entry in root.findall("a:entry", NS):
        arxiv_id = entry.find("a:id", NS).text.rsplit("/", 1)[-1]
        title = re.sub(r"\s+", " ", entry.find("a:title", NS).text).strip()
        year = entry.find("a:published", NS).text[:4]
        papers.append({"id": arxiv_id, "title": title, "year": year, "query": query})
    return papers


def download(paper: dict) -> str:
    pdf_url = f"https://export.arxiv.org/pdf/{paper['id']}"
    dest = OUT / f"{paper['id'].replace('/', '_')}.pdf"
    if dest.exists() and dest.stat().st_size > 10_000:
        return "cached"
    req = urllib.request.Request(pdf_url, headers={"User-Agent": "kg-demo-corpus/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())
    return "downloaded" if dest.stat().st_size > 10_000 else "too-small"


def main() -> None:
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 28
    OUT.mkdir(parents=True, exist_ok=True)

    seen: dict[str, dict] = {}
    for query in QUERIES:
        if len(seen) >= target:
            break
        try:
            for paper in search(query):
                seen.setdefault(paper["id"], paper)
        except Exception as exc:  # keep collecting from remaining queries
            print(f"query failed: {query}: {exc}", flush=True)
        time.sleep(3)

    manifest = []
    ok = 0
    for paper in list(seen.values())[:target]:
        try:
            status = download(paper)
        except Exception as exc:
            status = f"failed: {exc}"
        if status in ("cached", "downloaded"):
            ok += 1
        paper["status"] = status
        manifest.append(paper)
        print(f"[{ok}/{target}] {status}: {paper['id']} {paper['title'][:60]}", flush=True)
        time.sleep(3)

    (OUT / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"DONE: {ok}/{target} PDFs available in {OUT}", flush=True)


if __name__ == "__main__":
    main()
