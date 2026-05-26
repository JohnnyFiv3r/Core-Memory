from __future__ import annotations

import os

from core_memory.persistence.backend import BackendCapabilities


class Neo4jGraphBackend:
    """Neo4j graph backend — same Cypher traversal as Kuzu, different driver."""

    name = "neo4j"

    def __init__(self, uri: str, user: str, password: str):
        try:
            from neo4j import GraphDatabase
        except ImportError:
            raise ImportError("Neo4j backend requires: pip install core-memory[neo4j]")
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def from_env(cls) -> "Neo4jGraphBackend":
        uri = os.environ.get("CORE_MEMORY_NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("CORE_MEMORY_NEO4J_USER", "neo4j")
        password = os.environ.get("CORE_MEMORY_NEO4J_PASSWORD", "")
        return cls(uri=uri, user=user, password=password)

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(graph_traversal=True)

    def health(self) -> dict:
        try:
            with self._driver.session() as session:
                result = session.run("RETURN 1 AS ok")
                result.single()
            return {"ok": True, "backend": "neo4j"}
        except Exception as exc:
            return {"ok": False, "backend": "neo4j", "error": str(exc)}

    def traverse(
        self,
        seed_ids: list[str],
        edge_types: list[str] | None,
        max_hops: int,
        max_chains: int = 16,
    ) -> list[dict]:
        if not seed_ids:
            return []
        max_hops = max(1, int(max_hops))

        edge_filter = ""
        if edge_types:
            types_str = ", ".join(f'"{t}"' for t in edge_types)
            edge_filter = f" AND ALL(r IN relationships(path) WHERE r.rel_type IN [{types_str}])"

        cypher = f"""
        MATCH path = (s:Bead)-[r:ASSOCIATION*1..{max_hops}]->(n:Bead)
        WHERE s.id IN $seed_ids
          AND n.status = 'active'{edge_filter}
        WITH
            [node IN nodes(path) | {{id: node.id, type: node.type, title: node.title}}] AS nodes,
            [rel IN relationships(path) | {{
                rel: rel.rel_type,
                src: startNode(rel).id,
                tgt: endNode(rel).id
            }}] AS edges,
            length(path) AS depth
        ORDER BY depth ASC
        LIMIT {max_chains}
        RETURN nodes, edges
        """
        chains = []
        try:
            with self._driver.session() as session:
                result = session.run(cypher, seed_ids=seed_ids)
                for record in result:
                    chains.append({"nodes": list(record["nodes"]), "edges": list(record["edges"])})
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("neo4j traverse failed: %s", exc)
        return chains

    def on_bead_written(self, bead: dict) -> None:
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            return
        try:
            with self._driver.session() as session:
                session.run(
                    "MERGE (b:Bead {id: $id}) "
                    "SET b.type=$type, b.title=$title, b.session_id=$session_id, "
                    "    b.created_at=$created_at, b.status=$status",
                    id=bead_id,
                    type=str(bead.get("type") or ""),
                    title=str(bead.get("title") or ""),
                    session_id=str(bead.get("session_id") or ""),
                    created_at=str(bead.get("created_at") or ""),
                    status=str(bead.get("status") or "open"),
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("neo4j on_bead_written failed: %s", exc)

    def on_association_written(self, assoc: dict) -> None:
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        rel_type = str(assoc.get("relationship") or assoc.get("rel_type") or "")
        if not (src and tgt and rel_type):
            return
        try:
            with self._driver.session() as session:
                session.run(
                    "MATCH (s:Bead {id: $src}), (t:Bead {id: $tgt}) "
                    "MERGE (s)-[r:ASSOCIATION {rel_type: $rel_type}]->(t) "
                    "SET r.confidence=$confidence, r.created_at=$created_at",
                    src=src, tgt=tgt, rel_type=rel_type,
                    confidence=float(assoc.get("confidence") or 1.0),
                    created_at=str(assoc.get("created_at") or ""),
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("neo4j on_association_written failed: %s", exc)

    def on_bead_retracted(self, bead_id: str) -> None:
        try:
            with self._driver.session() as session:
                session.run("MATCH (b:Bead {id: $id}) SET b.status='retracted'", id=str(bead_id))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("neo4j on_bead_retracted failed: %s", exc)

    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict:
        bead_count = 0
        assoc_count = 0
        errors: list[str] = []
        for bead in beads:
            try:
                self.on_bead_written(bead)
                bead_count += 1
            except Exception as exc:
                errors.append(f"bead:{bead.get('id')}:{exc}")
        for assoc in associations:
            try:
                self.on_association_written(assoc)
                assoc_count += 1
            except Exception as exc:
                errors.append(f"assoc:{assoc.get('id')}:{exc}")
        return {"synced_beads": bead_count, "synced_associations": assoc_count, "errors": errors}

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            pass
