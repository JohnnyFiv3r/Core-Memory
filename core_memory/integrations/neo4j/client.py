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

    def status(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {
                "ok": True,
                "enabled": False,
                "status": "disabled",
                "warnings": ["neo4j_disabled"],
            }

        try:
            self._validate_config()
            GraphDatabase = self._require_dependency()
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

        driver = None
        try:
            driver = GraphDatabase.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
                encrypted=bool(self.config.tls),
                connection_timeout=float(self.config.timeout_ms) / 1000.0,
            )
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
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass
