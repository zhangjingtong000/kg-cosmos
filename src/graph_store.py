"""
graph_store.py — Knowledge graph storage with Neo4j-compatible data model.

Backends:
  - networkx  (lightweight, no server needed)
  - neo4j     (production, requires running Neo4j server)

Usage:
  store = GraphStore(backend="networkx")
  store.import_from_json("test_pipeline.json")
  store.query("MATCH (n:Exercise) RETURN n.name")
"""

import json
from math import cos, pi, sin, sqrt
from pathlib import Path


def merge_evidence(*evidence_lists: list[dict]) -> list[dict]:
    """Merge chunk references while keeping one record for each source location."""
    merged = []
    seen = set()
    for evidence_list in evidence_lists:
        for evidence in evidence_list or []:
            key = (
                evidence.get("source_id"),
                evidence.get("page"),
                evidence.get("chunk_idx"),
            )
            if key not in seen:
                seen.add(key)
                merged.append(evidence)
    return merged


def separate_structural_twins(graph, positions: dict, minimum_distance: float = 0.32) -> None:
    """Give indistinguishable connected nodes a deterministic, symmetric offset.

    A force-directed layout correctly sees nodes with the same predecessor and
    successor sets as equivalent.  In a knowledge graph that is mathematically
    valid but visually unhelpful: their rendered spheres can occupy the same
    point.  Only near-coincident non-isolated twins are separated, so ordinary
    layouts retain their natural geometry.
    """
    signatures: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
    for node in graph.nodes:
        incoming = tuple(sorted(graph.predecessors(node)))
        outgoing = tuple(sorted(graph.successors(node)))
        if incoming or outgoing:
            signatures.setdefault((incoming, outgoing), []).append(node)

    for twins in signatures.values():
        if len(twins) < 2:
            continue
        twins.sort()
        points = [positions[node] for node in twins]
        closest = min(
            sqrt(sum((points[left][axis] - points[right][axis]) ** 2 for axis in range(3)))
            for left in range(len(points))
            for right in range(left + 1, len(points))
        )
        if closest >= minimum_distance:
            continue

        center = [sum(point[axis] for point in points) / len(points) for axis in range(3)]
        # A repeatable tangent plane around the cluster center.  The fallback
        # covers a node group whose center happens to lie on the Z axis.
        radial_length = sqrt(sum(value * value for value in center))
        radial = [value / radial_length for value in center] if radial_length else [1.0, 0.0, 0.0]
        tangent = [-radial[1], radial[0], 0.0]
        tangent_length = sqrt(sum(value * value for value in tangent))
        if tangent_length < 1e-9:
            tangent = [0.0, -radial[2], radial[1]]
            tangent_length = sqrt(sum(value * value for value in tangent))
        tangent = [value / tangent_length for value in tangent]
        bitangent = [
            radial[1] * tangent[2] - radial[2] * tangent[1],
            radial[2] * tangent[0] - radial[0] * tangent[2],
            radial[0] * tangent[1] - radial[1] * tangent[0],
        ]
        radius = minimum_distance / (2 * sin(pi / len(twins)))
        for index, node in enumerate(twins):
            angle = (2 * pi * index / len(twins)) + (pi / 2)
            positions[node] = [
                center[axis] + radius * (cos(angle) * tangent[axis] + sin(angle) * bitangent[axis])
                for axis in range(3)
            ]


# ── NetworkX Backend ─────────────────────────────────────

class NetworkXStore:
    def __init__(self):
        import networkx as nx
        self.g = nx.DiGraph()

    def import_data(self, nodes: list[dict], edges: list[dict]):
        """Import nodes and edges with case-insensitive name matching."""
        # Build case-insensitive lookup
        name_map = {}  # lowercase → canonical name
        for n in nodes:
            canonical = n["name"].strip()
            key = canonical.lower()
            if key in name_map:
                # Keep the Title Case version
                if canonical[0].isupper():
                    name_map[key] = canonical
            else:
                name_map[key] = canonical
            node_name = name_map[key]
            existing = self.g.nodes[node_name] if node_name in self.g else {}
            self.g.add_node(
                node_name,
                label=n.get("type", "Entity"),
                description=n.get("description", ""),
                evidence=merge_evidence(existing.get("evidence", []), n.get("evidence", [])),
            )

        for e in edges:
            src_key = e["source"].strip().lower()
            tgt_key = e["target"].strip().lower()
            src = name_map.get(src_key, e["source"].strip())
            tgt = name_map.get(tgt_key, e["target"].strip())
            # Only add edge if both endpoints exist (skip dangling edges)
            if src in self.g and tgt in self.g:
                existing = self.g.get_edge_data(src, tgt, default={})
                self.g.add_edge(
                    src, tgt,
                    type=e.get("type", "RELATED_TO"),
                    description=e.get("description", ""),
                    evidence=merge_evidence(existing.get("evidence", []), e.get("evidence", [])),
                )

    def query_nodes(self, label: str = None) -> list[dict]:
        """Return all nodes, optionally filtered by label."""
        result = []
        for name, data in self.g.nodes(data=True):
            if label and data.get("label") != label:
                continue
            result.append({"name": name, **data})
        return result

    def query_edges(self, rel_type: str = None) -> list[dict]:
        """Return all edges, optionally filtered by type."""
        result = []
        for src, tgt, data in self.g.edges(data=True):
            if rel_type and data.get("type") != rel_type:
                continue
            result.append({"source": src, "target": tgt, **data})
        return result

    def get_neighbors(self, node_name: str, depth: int = 1) -> dict:
        """Get a node and its N-hop neighborhood (for subgraph queries)."""
        nodes_set = {node_name}
        edges_list = []
        frontier = {node_name}

        for _ in range(depth):
            next_frontier = set()
            for n in list(frontier):
                for _, tgt, data in self.g.out_edges(n, data=True):
                    edges_list.append({"source": n, "target": tgt, **data})
                    if tgt not in nodes_set:
                        nodes_set.add(tgt)
                        next_frontier.add(tgt)
                for src, _, data in self.g.in_edges(n, data=True):
                    edges_list.append({"source": src, "target": n, **data})
                    if src not in nodes_set:
                        nodes_set.add(src)
                        next_frontier.add(src)
            frontier = next_frontier

        nodes_list = [{"name": n, **self.g.nodes[n]} for n in nodes_set]
        return {"nodes": nodes_list, "edges": edges_list}

    def stats(self) -> dict:
        return {
            "nodes": self.g.number_of_nodes(),
            "edges": self.g.number_of_edges(),
        }


# ── Neo4j Backend (when server is available) ─────────────

class Neo4jStore:
    # Evidence records are stored as a list of JSON strings.  Re-uploads are
    # merged atomically in Cypher by list dedup (identical records are exact
    # duplicates of the same source location), so no read-modify-write exists.
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="password"):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver.verify_connectivity()

    @staticmethod
    def _evidence_items(record: dict) -> list[str]:
        return [
            json.dumps(ev, ensure_ascii=False)
            for ev in (record.get("evidence") or [])
        ]

    @staticmethod
    def _parse_evidence(raw) -> list[dict]:
        return [json.loads(item) for item in (raw or [])]

    def import_data(self, nodes: list[dict], edges: list[dict]):
        def esc(s):
            """Escape Neo4j label/type with spaces or special chars."""
            return f"`{s}`" if any(c in s for c in ' -/()[]{}:') else s

        # Case-insensitive endpoint matching, on parity with NetworkXStore:
        # entity dedup keeps one canonical casing, so edges naming case
        # variants must resolve to the merged node instead of dangling.
        name_map = {}
        for n in nodes:
            canonical = n["name"].strip()
            key = canonical.lower()
            if key not in name_map or (canonical[:1].isupper() and not name_map[key][:1].isupper()):
                name_map[key] = canonical

        with self.driver.session() as s:
            for n in nodes:
                canonical = name_map[n["name"].strip().lower()]
                s.run(
                    f"MERGE (x:{esc(n['type'])} {{name: $name}}) "
                    "SET x.description = $desc, "
                    "x.evidence = coalesce(x.evidence, []) "
                    "+ [item IN $items WHERE NOT item IN coalesce(x.evidence, [])]",
                    name=canonical, desc=n.get("description", ""),
                    items=self._evidence_items(n),
                )
            # Endpoints must exist, so dangling edges are silently skipped
            for e in edges:
                src = name_map.get(e["source"].strip().lower(), e["source"].strip())
                tgt = name_map.get(e["target"].strip().lower(), e["target"].strip())
                s.run(
                    f"MATCH (a {{name: $src}}), (b {{name: $tgt}}) "
                    f"MERGE (a)-[r:{esc(e['type'])}]->(b) "
                    "ON CREATE SET r.description = $desc, r.evidence = [] "
                    "SET r.evidence = r.evidence "
                    "+ [item IN $items WHERE NOT item IN r.evidence]",
                    src=src, tgt=tgt, desc=e.get("description", ""),
                    items=self._evidence_items(e),
                )

    def query_nodes(self, label: str = None) -> list[dict]:
        with self.driver.session() as s:
            if label:
                result = s.run(
                    f"MATCH (n:{esc(label)}) "
                    "RETURN n.name AS name, labels(n) AS labels, "
                    "n.description AS description, n.evidence AS evidence"
                )
            else:
                result = s.run(
                    "MATCH (n) RETURN n.name AS name, labels(n) AS labels, "
                    "n.description AS description, n.evidence AS evidence"
                )
            return [{
                "name": r["name"],
                "type": r["labels"][0] if r["labels"] else "Entity",
                "description": r["description"] or "",
                "evidence": self._parse_evidence(r["evidence"]),
            } for r in result]

    def query_edges(self, rel_type: str = None) -> list[dict]:
        with self.driver.session() as s:
            q = (
                "MATCH (a)-[r]->(b) "
                + ("WHERE type(r) = $rel_type " if rel_type else "")
                + "RETURN a.name AS source, b.name AS target, type(r) AS type, "
                "r.description AS description, r.evidence AS evidence"
            )
            result = s.run(q, rel_type=rel_type) if rel_type else s.run(q)
            return [{
                "source": r["source"], "target": r["target"], "type": r["type"],
                "description": r["description"] or "",
                "evidence": self._parse_evidence(r["evidence"]),
            } for r in result]

    def get_neighbors(self, node_name: str, depth: int = 1) -> dict:
        """Get a node and its N-hop neighborhood (for subgraph queries)."""
        depth = max(1, int(depth))
        with self.driver.session() as s:
            result = s.run(
                f"MATCH (a {{name: $name}})-[r*1..{depth}]-(b) "
                "UNWIND r AS rel "
                "WITH DISTINCT startNode(rel) AS sn, rel, endNode(rel) AS en "
                "RETURN sn.name AS source, en.name AS target, type(rel) AS type, "
                "rel.evidence AS evidence",
                name=node_name,
            )
            edges_list, names = [], set()
            for r in result:
                edges_list.append({
                    "source": r["source"], "target": r["target"], "type": r["type"],
                    "evidence": self._parse_evidence(r["evidence"]),
                })
                names.update((r["source"], r["target"]))
            node_data = {n["name"]: n for n in self.query_nodes()}
            nodes_list = [node_data[n] for n in names if n in node_data]
            return {"nodes": nodes_list, "edges": edges_list}

    def stats(self) -> dict:
        with self.driver.session() as s:
            n = s.run("MATCH (n) RETURN count(n)").single()[0]
            e = s.run("MATCH ()-[r]->() RETURN count(r)").single()[0]
        return {"nodes": n, "edges": e}

    def close(self):
        self.driver.close()


# ── Unified Interface ────────────────────────────────────

class GraphStore:
    def __init__(self, backend: str = "networkx", **kwargs):
        if backend == "neo4j":
            self.store = Neo4jStore(**kwargs)
        else:
            self.store = NetworkXStore()

    def import_from_json(self, json_path: str):
        """Import from kg_extractor output JSON."""
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        nodes = data.get("nodes", data.get("result", {}).get("nodes", []))
        edges = data.get("edges", data.get("result", {}).get("edges", []))
        self.store.import_data(nodes, edges)
        return self.store.stats()

    def get_3d_data(self) -> dict:
        """Return data in format ready for 3D visualization."""
        nodes = self.store.query_nodes()
        edges = self.store.query_edges()

        # Generate initial 3D positions using spring layout
        try:
            import networkx as nx
            g = nx.DiGraph()
            for n in nodes:
                g.add_node(n["name"])
            for e in edges:
                g.add_edge(e["source"], e["target"])
            pos = nx.spring_layout(g, dim=3, seed=42)
            separate_structural_twins(g, pos)
            for n in nodes:
                p = pos.get(n["name"], [0, 0, 0])
                n["x"], n["y"], n["z"] = float(p[0]), float(p[1]), float(p[2])
        except ImportError:
            for n in nodes:
                n["x"] = n["y"] = n["z"] = 0

        return {"nodes": nodes, "edges": edges, "stats": self.store.stats()}

    def stats(self) -> dict:
        return self.store.stats()


# ── CLI ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    store = GraphStore(backend="networkx")
    path = sys.argv[1] if len(sys.argv) > 1 else "test_pipeline.json"
    stats = store.import_from_json(path)
    print(f"Imported: {stats['nodes']} nodes, {stats['edges']} edges")

    data = store.get_3d_data()
    print(f"3D data: {len(data['nodes'])} nodes with positions, {len(data['edges'])} edges")
