from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Neo4jConfig:
    enabled: bool
    uri: str
    user: str
    password: str
    database: str
    dataset: str
    tls: bool
    timeout_ms: int

    @classmethod
    def from_env(cls) -> "Neo4jConfig":
        return cls(
            enabled=_env_bool("CORE_MEMORY_NEO4J_ENABLED", False),
            uri=str(os.environ.get("CORE_MEMORY_NEO4J_URI") or "").strip(),
            user=str(os.environ.get("CORE_MEMORY_NEO4J_USER") or "").strip(),
            password=str(os.environ.get("CORE_MEMORY_NEO4J_PASSWORD") or "").strip(),
            database=str(os.environ.get("CORE_MEMORY_NEO4J_DATABASE") or "neo4j").strip() or "neo4j",
            dataset=str(os.environ.get("CORE_MEMORY_NEO4J_DATASET") or "").strip(),
            tls=_env_bool("CORE_MEMORY_NEO4J_TLS", True),
            timeout_ms=_env_int("CORE_MEMORY_NEO4J_TIMEOUT_MS", 5000),
        )

    def redacted(self) -> dict[str, object]:
        return {
            "enabled": bool(self.enabled),
            "uri": self.uri,
            "user": self.user,
            "database": self.database,
            "dataset": self.dataset,
            "tls": bool(self.tls),
            "timeout_ms": int(self.timeout_ms),
            "password_set": bool(self.password),
        }



def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    v = str(raw).strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return bool(default)



def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return max(1, int(raw or default))
    except Exception:
        return int(default)
