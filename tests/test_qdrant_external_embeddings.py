"""Qdrant external-embedding mode.

By default Qdrant uses its built-in FastEmbed (small ~384-dim model), which
discriminates poorly on conversational text and caps retrieval recall. With
CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS=1 the corpus is embedded with the
configured external provider (e.g. OpenAI text-embedding-3-large) and the query
path searches by vector instead of FastEmbed query_text.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval import semantic_index as si
from core_memory.retrieval import hybrid


def test_flag_default_off_and_env_on(monkeypatch):
    monkeypatch.delenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", raising=False)
    assert si._qdrant_external_embeddings_enabled() is False
    for val in ("1", "true", "on", "YES"):
        monkeypatch.setenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", val)
        assert si._qdrant_external_embeddings_enabled() is True
    monkeypatch.setenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", "0")
    assert si._qdrant_external_embeddings_enabled() is False


def test_query_path_fastembed_manifest_uses_hybrid_search(tmp_path: Path):
    # Manifest provider=fastembed -> native hybrid_search (query_text).
    beads = tmp_path / ".beads" / "semantic"
    beads.mkdir(parents=True)
    calls = {"hybrid": 0, "search": 0}

    class FakeBackend:
        def hybrid_search(self, query, k, filters):
            calls["hybrid"] += 1
            return [{"bead_id": "b1", "score": 0.9, "metadata": {}}]

        def search(self, query_embedding, k, filters):
            calls["search"] += 1
            return []

    with patch("core_memory.retrieval.semantic_index._paths", return_value=(beads / "manifest.json",)), \
         patch("core_memory.retrieval.semantic_index._create_external_backend", return_value=FakeBackend()):
        (beads / "manifest.json").write_text(json.dumps({"provider": "fastembed", "dimension": 384}), encoding="utf-8")
        rows = hybrid._qdrant_hybrid_rows(tmp_path, "when did melanie paint", k=8)

    assert calls == {"hybrid": 1, "search": 0}
    assert rows and rows[0]["bead_id"] == "b1"


def test_query_path_external_manifest_embeds_query_and_vector_searches(tmp_path: Path):
    # Manifest provider=openai -> embed query + search by vector.
    beads = tmp_path / ".beads" / "semantic"
    beads.mkdir(parents=True)
    calls = {"hybrid": 0, "search": 0}
    captured = {}

    class FakeBackend:
        def hybrid_search(self, query, k, filters):
            calls["hybrid"] += 1
            return []

        def search(self, query_embedding, k, filters):
            calls["search"] += 1
            captured["vec"] = query_embedding
            captured["filters"] = filters
            return [{"bead_id": "b2", "score": 0.81, "metadata": {}}]

    with patch("core_memory.retrieval.semantic_index._paths", return_value=(beads / "manifest.json",)), \
         patch("core_memory.retrieval.semantic_index._create_external_backend", return_value=FakeBackend()), \
         patch("core_memory.retrieval.semantic_index._embed_vectors", return_value=[[0.1, 0.2, 0.3]]), \
         patch("core_memory.retrieval.semantic_index._vector_rows", return_value=[[0.1, 0.2, 0.3]]):
        (beads / "manifest.json").write_text(
            json.dumps({"provider": "openai", "model": "text-embedding-3-large", "dimension": 3072}), encoding="utf-8"
        )
        rows = hybrid._qdrant_hybrid_rows(tmp_path, "when did melanie paint", k=8)

    assert calls == {"hybrid": 0, "search": 1}
    assert captured["vec"] == [0.1, 0.2, 0.3]
    assert captured["filters"] == {"retrieval_eligible": True}
    assert rows and rows[0]["bead_id"] == "b2"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
