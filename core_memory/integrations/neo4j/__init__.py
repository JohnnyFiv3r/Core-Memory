"""Neo4j shadow-graph adapter (optional, projection-only).

This package is intentionally non-authoritative. It must not participate in
canonical read/write runtime paths.
"""

from .config import Neo4jConfig
from .sync import neo4j_status, sync_to_neo4j

__all__ = [
    "Neo4jConfig",
    "neo4j_status",
    "sync_to_neo4j",
]
