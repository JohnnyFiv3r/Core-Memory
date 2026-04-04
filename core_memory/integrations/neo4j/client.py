from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Neo4jConfig


class Neo4jDependencyError(RuntimeError):
    pass


class Neo4jConfigError(RuntimeError):
    pass


@dataclass
class Neo4jClient:
    config: Neo4jConfig

    def _require_dependency(self):
        try:
            from neo4j import GraphDatabase  # type: ignore
        except ImportError as exc:
            raise Neo4jDependencyError(
                "Neo4j adapter requires the 'neo4j' package. "
                "Install with: pip install core-memory[neo4j]"
            ) from exc
        return GraphDatabase

    def _validate_config(self) -> None:
        if not self.config.uri:
            raise Neo4jConfigError("missing_config: CORE_MEMORY_NEO4J_URI")
        if not self.config.user:
            raise Neo4jConfigError("missing_config: CORE_MEMORY_NEO4J_USER")
        if not self.config.password:
            raise Neo4jConfigError("missing_config: CORE_MEMORY_NEO4J_PASSWORD")

    def _open_driver(self):
        self._validate_config()
        GraphDatabase = self._require_dependency()
        return GraphDatabase.driver(
            self.config.uri,
            auth=(self.config.user, self.config.password),
            encrypted=bool(self.config.tls),
            connection_timeout=float(self.config.timeout_ms) / 1000.0,
        )

    def status(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {
                "ok": True,
                "enabled": False,
                "status": "disabled",
                "warnings": ["neo4j_disabled"],
            }

        try:
            driver = self._open_driver()
        except Neo4jConfigError as exc:
            return {
                "ok": False,
                "enabled": True,
                "status": "misconfigured",
                "error": {"code": "neo4j_config_error", "message": str(exc)},
                "warnings": [],
            }
        except Neo4jDependencyError as exc:
            return {
                "ok": False,
                "enabled": True,
                "status": "missing_dependency",
                "error": {"code": "neo4j_dependency_missing", "message": str(exc)},
                "warnings": [],
            }

        try:
            with driver.session(database=self.config.database) as session:
                ping = session.run("RETURN 1 AS ok").single()
                ok_val = int((ping or {}).get("ok", 0)) if ping is not None else 0
                counts = session.run(
                    "MATCH (n:Bead) WITH count(n) AS nodes "
                    "OPTIONAL MATCH ()-[r:ASSOCIATED]->() RETURN nodes, count(r) AS edges"
                ).single()
                return {
                    "ok": bool(ok_val == 1),
                    "enabled": True,
                    "status": "ready" if ok_val == 1 else "unknown",
                    "database": self.config.database,
                    "nodes": int((counts or {}).get("nodes", 0)) if counts is not None else 0,
                    "edges": int((counts or {}).get("edges", 0)) if counts is not None else 0,
                    "warnings": [],
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "enabled": True,
                "status": "connection_failed",
                "database": self.config.database,
                "error": {"code": "neo4j_connection_failed", "message": str(exc)},
                "warnings": [],
            }
        finally:
            try:
                driver.close()
            except Exception:
                pass

    def upsert_projection(
        self,
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        prune: bool = False,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {
                "ok": False,
                "nodes_upserted": 0,
                "edges_upserted": 0,
                "nodes_pruned": 0,
                "edges_pruned": 0,
                "warnings": ["neo4j_disabled"],
                "errors": [{"code": "neo4j_disabled", "message": "CORE_MEMORY_NEO4J_ENABLED=0"}],
            }

        warnings: list[str] = []
        if prune:
            warnings.append("neo4j_prune_not_implemented_in_slice3")

        try:
            driver = self._open_driver()
        except Neo4jConfigError as exc:
            return {
                "ok": False,
                "nodes_upserted": 0,
                "edges_upserted": 0,
                "nodes_pruned": 0,
                "edges_pruned": 0,
                "warnings": warnings,
                "errors": [{"code": "neo4j_config_error", "message": str(exc)}],
            }
        except Neo4jDependencyError as exc:
            return {
                "ok": False,
                "nodes_upserted": 0,
                "edges_upserted": 0,
                "nodes_pruned": 0,
                "edges_pruned": 0,
                "warnings": warnings,
                "errors": [{"code": "neo4j_dependency_missing", "message": str(exc)}],
            }

        nodes_upserted = 0
        edges_upserted = 0
        try:
            with driver.session(database=self.config.database) as session:
                for node in list(nodes or []):
                    props = dict(node.get("properties") or {})
                    bead_id = str(props.get("bead_id") or "").strip()
                    if not bead_id:
                        continue

                    labels = _sanitize_labels(list(node.get("labels") or []))
                    session.run(
                        "MERGE (b:Bead {bead_id: $bead_id}) "
                        "SET b += $props",
                        bead_id=bead_id,
                        props=props,
                    )
                    if labels:
                        label_clause = ":" + ":".join(labels)
                        session.run(
                            f"MATCH (b:Bead {{bead_id: $bead_id}}) SET b{label_clause}",
                            bead_id=bead_id,
                        )
                    nodes_upserted += 1

                for edge in list(edges or []):
                    props = dict(edge.get("properties") or {})
                    assoc_id = str(props.get("association_id") or "").strip()
                    src = str(edge.get("start_bead_id") or "").strip()
                    dst = str(edge.get("end_bead_id") or "").strip()
                    if not assoc_id or not src or not dst:
                        continue

                    session.run(
                        "MATCH (s:Bead {bead_id: $src}), (t:Bead {bead_id: $dst}) "
                        "MERGE (s)-[r:ASSOCIATED {association_id: $association_id}]->(t) "
                        "SET r += $props",
                        src=src,
                        dst=dst,
                        association_id=assoc_id,
                        props=props,
                    )
                    edges_upserted += 1

            return {
                "ok": True,
                "database": self.config.database,
                "nodes_upserted": int(nodes_upserted),
                "edges_upserted": int(edges_upserted),
                "nodes_pruned": 0,
                "edges_pruned": 0,
                "warnings": warnings,
                "errors": [],
                "scope": dict(scope or {}),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "database": self.config.database,
                "nodes_upserted": int(nodes_upserted),
                "edges_upserted": int(edges_upserted),
                "nodes_pruned": 0,
                "edges_pruned": 0,
                "warnings": warnings,
                "errors": [{"code": "neo4j_sync_failed", "message": str(exc)}],
            }
        finally:
            try:
                driver.close()
            except Exception:
                pass


def _sanitize_labels(labels: list[Any]) -> list[str]:
    out: list[str] = []
    for label in labels:
        s = str(label or "").strip()
        if not s:
            continue
        if not s[:1].isalpha():
            continue
        if any((not c.isalnum()) and c != "_" for c in s):
            continue
        if s not in out and s != "Bead":
            out.append(s)
    return out
