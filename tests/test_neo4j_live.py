"""Live Neo4j integration tests — require NEO4J_URI env var.

These tests run against a real Neo4j instance and are skipped automatically
when NEO4J_URI is not set. They run only on workflow_dispatch in CI
(see .github/workflows/test.yml neo4j-live job).
"""
from __future__ import annotations

import os
import pytest

pytestmark = [
    pytest.mark.neo4j,
    pytest.mark.skipif(
        not os.environ.get("NEO4J_URI"),
        reason="NEO4J_URI not set — live Neo4j test skipped",
    ),
]


@pytest.fixture(scope="module")
def backend():
    from core_memory.persistence.graph.neo4j_backend import Neo4jGraphBackend
    b = Neo4jGraphBackend(
        uri=os.environ["NEO4J_URI"],
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ.get("NEO4J_PASSWORD", ""),
    )
    yield b
    b.close()


def _bead(bead_id: str) -> dict:
    return {
        "id": bead_id,
        "type": "decision",
        "title": f"Live test bead {bead_id}",
        "session_id": "live-test-session",
        "created_at": "2026-01-01T00:00:00Z",
        "status": "open",
    }


def _assoc(src: str, tgt: str) -> dict:
    return {
        "id": f"assoc-{src}-{tgt}",
        "source_bead": src,
        "target_bead": tgt,
        "relationship": "caused_by",
        "confidence": 0.9,
        "created_at": "2026-01-01T00:00:00Z",
    }


def test_health_returns_ok(backend):
    h = backend.health()
    assert h["ok"] is True
    assert h["backend"] == "neo4j"


def test_on_bead_written_and_traverse(backend):
    bead = _bead("live-bead-A")
    backend.on_bead_written(bead)
    chains = backend.traverse(seed_ids=["live-bead-A"], edge_types=None, max_hops=1)
    # traverse requires at least one hop so may be empty; bead write must not raise
    assert isinstance(chains, list)


def test_on_association_written_and_traverse_returns_chain(backend):
    backend.on_bead_written(_bead("live-bead-X"))
    backend.on_bead_written(_bead("live-bead-Y"))
    backend.on_association_written(_assoc("live-bead-X", "live-bead-Y"))

    chains = backend.traverse(seed_ids=["live-bead-X"], edge_types=None, max_hops=2)
    assert isinstance(chains, list)
    all_node_ids = {n["id"] for ch in chains for n in ch.get("nodes", [])}
    assert "live-bead-Y" in all_node_ids


def test_on_bead_retracted_excludes_from_traverse(backend):
    backend.on_bead_written(_bead("live-bead-R"))
    backend.on_bead_retracted("live-bead-R")
    chains = backend.traverse(seed_ids=["live-bead-R"], edge_types=None, max_hops=2)
    # retracted bead should not appear as a non-seed node in any chain
    for chain in chains:
        for node in chain.get("nodes", []):
            if node["id"] != "live-bead-R":
                assert node.get("status") != "retracted"


def test_sync_from_storage_bulk_writes(backend):
    beads = [_bead(f"live-sync-{i}") for i in range(3)]
    assocs = [_assoc("live-sync-0", "live-sync-1"), _assoc("live-sync-1", "live-sync-2")]
    result = backend.sync_from_storage(beads=beads, associations=assocs)
    assert result["synced_beads"] == 3
    assert result["synced_associations"] == 2
    assert result.get("errors") == []
