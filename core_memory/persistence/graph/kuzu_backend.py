from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core_memory.persistence.backend import BackendCapabilities

logger = logging.getLogger(__name__)


class KuzuGraphBackend:
    """Embedded graph backend using Kuzu — zero-ops, local directory, Cypher-compatible."""

    name = "kuzu"

    def __init__(self, path: Path):
        try:
            import kuzu
        except ImportError:
            raise ImportError("Kuzu backend requires: pip install core-memory[kuzu]")

        self._path = Path(path)
        # Kuzu manages its own path — only ensure the parent directory exists.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self._path))
        self._conn = kuzu.Connection(self._db)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        stmts = [
            """
            CREATE NODE TABLE IF NOT EXISTS Bead(
                id         STRING,
                type       STRING,
                title      STRING,
                session_id STRING,
                created_at STRING,
                status     STRING,
                PRIMARY KEY(id)
            )
            """,
            """
            CREATE REL TABLE IF NOT EXISTS Association(
                FROM Bead TO Bead,
                rel_type   STRING,
                confidence DOUBLE,
                created_at STRING
            )
            """,
        ]
        for stmt in stmts:
            try:
                self._conn.execute(stmt.strip())
            except Exception as exc:
                # Schema already exists — ignore; Kuzu raises on duplicate CREATE
                msg = str(exc).lower()
                if "already exist" not in msg and "already exists" not in msg:
                    raise

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(graph_traversal=True)

    def health(self) -> dict:
        try:
            result = self._conn.execute("MATCH (b:Bead) RETURN count(b) AS n")
            count = result.get_next()[0] if result.has_next() else 0
            return {"ok": True, "backend": "kuzu", "bead_count": int(count)}
        except Exception as exc:
            return {"ok": False, "backend": "kuzu", "error": str(exc)}

    def on_bead_written(self, bead: dict) -> None:
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            return
        params = {
            "id": bead_id,
            "type": str(bead.get("type") or ""),
            "title": str(bead.get("title") or ""),
            "session_id": str(bead.get("session_id") or ""),
            "created_at": str(bead.get("created_at") or ""),
            "status": str(bead.get("status") or "open"),
        }
        try:
            # Try update first, then insert if not found
            result = self._conn.execute(
                "MATCH (b:Bead {id: $id}) "
                "SET b.type = $type, b.title = $title, b.session_id = $session_id, "
                "    b.created_at = $created_at, b.status = $status "
                "RETURN b.id",
                params,
            )
            if not result.has_next():
                # Node doesn't exist — create it
                self._conn.execute(
                    "CREATE (b:Bead {id: $id, type: $type, title: $title, "
                    "session_id: $session_id, created_at: $created_at, status: $status})",
                    params,
                )
        except Exception as exc:
            logger.warning("kuzu on_bead_written failed for %s: %s", bead_id, exc)

    def on_association_written(self, assoc: dict) -> None:
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        rel_type = str(assoc.get("relationship") or assoc.get("rel_type") or "")
        if not (src and tgt and rel_type):
            return
        params = {
            "src": src,
            "tgt": tgt,
            "rel_type": rel_type,
            "confidence": float(assoc.get("confidence") or 1.0),
            "created_at": str(assoc.get("created_at") or ""),
        }
        try:
            self._conn.execute(
                "MATCH (s:Bead {id: $src}), (t:Bead {id: $tgt}) "
                "CREATE (s)-[:Association {rel_type: $rel_type, confidence: $confidence, created_at: $created_at}]->(t)",
                params,
            )
        except Exception as exc:
            logger.warning("kuzu on_association_written failed %s->%s: %s", src, tgt, exc)

    def on_bead_retracted(self, bead_id: str) -> None:
        try:
            self._conn.execute(
                "MATCH (b:Bead {id: $id}) SET b.status = 'retracted'",
                {"id": str(bead_id)},
            )
        except Exception as exc:
            logger.warning("kuzu on_bead_retracted failed for %s: %s", bead_id, exc)

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
        max_chains = max(1, int(max_chains))

        seed_list = [str(s) for s in seed_ids if str(s).strip()]
        if not seed_list:
            return []

        try:
            # Kuzu doesn't support variable-length path + node/rel extraction cleanly in all versions.
            # We walk hop-by-hop up to max_hops and reconstruct chains in Python.
            return self._traverse_hops(seed_list, edge_types, max_hops, max_chains)
        except Exception as exc:
            logger.warning("kuzu traverse failed (seeds=%s): %s", seed_list[:3], exc)
            return []

    def _traverse_hops(
        self,
        seed_ids: list[str],
        edge_types: list[str] | None,
        max_hops: int,
        max_chains: int,
    ) -> list[dict]:
        """BFS hop-by-hop traversal, reconstructing chains."""
        chains: list[dict] = []
        # Each frontier item: (bead_id, chain_so_far_nodes, chain_so_far_edges)
        frontier = [
            (sid, [{"id": sid, "type": "", "title": ""}], [])
            for sid in seed_ids
        ]
        # Enrich seed node metadata
        frontier = [
            (sid, [self._node_meta(sid)], [])
            for sid in seed_ids
        ]

        for _hop in range(max_hops):
            if not frontier or len(chains) >= max_chains:
                break
            next_frontier = []
            for current_id, nodes, edges in frontier:
                neighbors = self._neighbors(current_id, edge_types)
                for nbr_id, rel_type, confidence in neighbors:
                    nbr_meta = self._node_meta(nbr_id)
                    if nbr_meta.get("status") == "retracted":
                        continue
                    new_nodes = nodes + [nbr_meta]
                    new_edges = edges + [{"rel": rel_type, "src": current_id, "tgt": nbr_id, "confidence": confidence}]
                    chain = {"nodes": new_nodes, "edges": new_edges}
                    chains.append(chain)
                    next_frontier.append((nbr_id, new_nodes, new_edges))
                    if len(chains) >= max_chains:
                        break
                if len(chains) >= max_chains:
                    break
            frontier = next_frontier

        return chains[:max_chains]

    def _node_meta(self, bead_id: str) -> dict[str, Any]:
        try:
            result = self._conn.execute(
                "MATCH (b:Bead {id: $id}) RETURN b.id, b.type, b.title, b.status",
                {"id": bead_id},
            )
            if result.has_next():
                row = result.get_next()
                return {"id": str(row[0] or ""), "type": str(row[1] or ""), "title": str(row[2] or ""), "status": str(row[3] or "")}
        except Exception:
            pass
        return {"id": bead_id, "type": "", "title": "", "status": ""}

    def _neighbors(
        self, bead_id: str, edge_types: list[str] | None
    ) -> list[tuple[str, str, float]]:
        try:
            result = self._conn.execute(
                "MATCH (s:Bead {id: $id})-[r:Association]->(t:Bead) "
                "WHERE t.status <> 'retracted' "
                "RETURN t.id, r.rel_type, r.confidence",
                {"id": bead_id},
            )
            rows = []
            while result.has_next():
                row = result.get_next()
                tgt_id = str(row[0] or "")
                rel = str(row[1] or "")
                conf = float(row[2] or 1.0)
                if edge_types and rel not in edge_types:
                    continue
                rows.append((tgt_id, rel, conf))
            return rows
        except Exception as exc:
            logger.warning("kuzu _neighbors failed for %s: %s", bead_id, exc)
            return []

    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict:
        """Bulk-load beads and associations — used by core-memory migrate."""
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

        return {
            "synced_beads": bead_count,
            "synced_associations": assoc_count,
            "errors": errors,
        }

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            self._db.close()
        except Exception:
            pass
