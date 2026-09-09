"""GraphStore tests: evidence merge invariants and backend parity.

Neo4j tests run against a live server and skip when unreachable; every test
cleans up only the nodes it created (prefix-scoped DETACH DELETE).
"""

import pytest

from graph_store import NetworkXStore, Neo4jStore, merge_evidence

EVIDENCE_A = [{"source_id": "t1", "page": 2, "chunk_idx": 1, "text": "alpha"}]
EVIDENCE_A_DUP = [{"source_id": "t1", "page": 2, "chunk_idx": 1, "text": "alpha"}]
EVIDENCE_B = [{"source_id": "t1", "page": 3, "chunk_idx": 0, "text": "beta"}]


def _nodes(name, evidence):
    return [{"name": name, "type": "Concept", "description": "d", "evidence": evidence}]


def _edges(src, tgt, evidence):
    return [{"source": src, "target": tgt, "type": "RELATES_TO",
             "description": "", "evidence": evidence}]


# ── merge_evidence ───────────────────────────────────────

def test_merge_evidence_dedupes_same_location():
    merged = merge_evidence(EVIDENCE_A, EVIDENCE_A_DUP, EVIDENCE_B)
    assert len(merged) == 2


def test_merge_evidence_handles_none():
    assert merge_evidence(None, EVIDENCE_A, []) == EVIDENCE_A


# ── NetworkXStore ────────────────────────────────────────

def test_networkx_reimport_merges_evidence_without_duplicates():
    store = NetworkXStore()
    store.import_data(_nodes("X", EVIDENCE_A), _edges("X", "Y", EVIDENCE_A))
    store.import_data(_nodes("X", EVIDENCE_A_DUP), [])
    store.import_data(_nodes("X", EVIDENCE_B), [])
    evidence = store.g.nodes["X"]["evidence"]
    assert len(evidence) == 2 and {e["page"] for e in evidence} == {2, 3}


def test_networkx_skips_dangling_edges():
    store = NetworkXStore()
    store.import_data(_nodes("Only", []), _edges("Only", "Ghost", []))
    assert store.g.number_of_edges() == 0


# ── Neo4jStore (live server, prefix-scoped cleanup) ──────

PREFIX = "unittest-gs"

try:
    _probe = Neo4jStore(password="kgdemo2026")
    _probe.close()
    NEO4J_AVAILABLE = True
except Exception:
    NEO4J_AVAILABLE = False

requires_neo4j = pytest.mark.skipif(
    not NEO4J_AVAILABLE, reason="no reachable Neo4j server"
)


@pytest.fixture
def neo_store():
    store = Neo4jStore(password="kgdemo2026")
    yield store
    with store.driver.session() as s:
        s.run(f"MATCH (n) WHERE n.name STARTS WITH '{PREFIX} ' DETACH DELETE n")
    store.close()


@requires_neo4j
def test_neo4j_roundtrip_and_evidence_merge(neo_store):
    neo_store.import_data(_nodes(f"{PREFIX} A", EVIDENCE_A), [])
    neo_store.import_data(_nodes(f"{PREFIX} A", EVIDENCE_A_DUP), [])
    neo_store.import_data(_nodes(f"{PREFIX} A", EVIDENCE_B), [])

    node = next(n for n in neo_store.query_nodes() if n["name"] == f"{PREFIX} A")
    assert {e["page"] for e in node["evidence"]} == {2, 3}


@requires_neo4j
def test_neo4j_edge_evidence_and_neighbors(neo_store):
    neo_store.import_data(
        _nodes(f"{PREFIX} A", EVIDENCE_A) + _nodes(f"{PREFIX} B", []),
        _edges(f"{PREFIX} A", f"{PREFIX} B", EVIDENCE_A),
    )
    edges = [e for e in neo_store.query_edges() if e["source"] == f"{PREFIX} A"]
    assert len(edges) == 1 and edges[0]["evidence"][0]["page"] == 2

    hood = neo_store.get_neighbors(f"{PREFIX} A", depth=1)
    assert {n["name"] for n in hood["nodes"]} == {f"{PREFIX} A", f"{PREFIX} B"}
    assert len(hood["edges"]) == 1


@requires_neo4j
def test_neo4j_dangling_edges_skipped(neo_store):
    neo_store.import_data(_nodes(f"{PREFIX} C", []), _edges(f"{PREFIX} C", "ghost", []))
    with neo_store.driver.session() as s:
        dangling = s.run(
            "MATCH (n {name: $name})-[r]->() RETURN count(r) AS n",
            name=f"{PREFIX} C",
        ).single()["n"]
    assert dangling == 0
