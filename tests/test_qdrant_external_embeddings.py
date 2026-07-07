"""Qdrant external-embedding mode.

By default Qdrant uses its built-in FastEmbed (small ~384-dim model), which
discriminates poorly on conversational text and caps retrieval recall. With
CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS=1 the corpus is embedded with the
configured external provider (e.g. OpenAI text-embedding-3-large) and the query
path searches by vector instead of FastEmbed query_text.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval import semantic_index as si
from core_memory.retrieval import hybrid


def test_add_bead_external_qdrant_mirror_uses_module_os_scope(tmp_path: Path, monkeypatch, caplog):
    """Delta bead writes must upsert external-Qdrant vectors without os scoping errors.

    A late ``import os`` inside the mirror helper made Python treat ``os`` as a
    local variable, so the earlier CORE_MEMORY_EMBEDDINGS_MODEL lookup raised
    ``UnboundLocalError`` only on the external-Qdrant write path and was swallowed
    as a best-effort warning. This regression keeps that path live.
    """
    from core_memory.runtime.post_write import bead_commit

    monkeypatch.setenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", "1")
    monkeypatch.setenv("CORE_MEMORY_EMBEDDINGS_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("CORE_MEMORY_GRAPH_BACKEND", "none")
    monkeypatch.setenv("CORE_MEMORY_SYNC_TARGETS", "none")

    upserts = []

    class FakeBackend:
        def upsert(self, *, bead_id, embedding, metadata):
            upserts.append({"bead_id": bead_id, "embedding": embedding, "metadata": metadata})

    monkeypatch.setattr(si, "_configured_vector_backend", lambda: si.VECTOR_BACKEND_QDRANT)
    monkeypatch.setattr(si, "_qdrant_external_embeddings_enabled", lambda: True)
    monkeypatch.setattr(si, "_auto_configure_embedding_provider_from_keys", lambda: "openai")
    monkeypatch.setattr(si, "_embed_vectors", lambda *, texts, provider, model, hash_dim: [[0.1, 0.2, 0.3]])
    monkeypatch.setattr(si, "_vector_dim", lambda vecs, fallback=256: 3)
    monkeypatch.setattr(si, "_vector_rows", lambda vecs: vecs)
    monkeypatch.setattr(si, "_create_external_backend", lambda *, root, backend, dimension: FakeBackend())

    bead = {
        "id": "bead-test",
        "type": "fact",
        "title": "Test bead",
        "summary": ["A concise test summary"],
        "retrieval_eligible": True,
    }
    with caplog.at_level("WARNING"):
        bead_commit._mirror_bead_to_backends(tmp_path, bead)

    assert upserts == [
        {
            "bead_id": "bead-test",
            "embedding": [0.1, 0.2, 0.3],
            "metadata": {
                "bead_id": "bead-test",
                "type": "fact",
                "session_id": "",
                "created_at": "",
                "retrieval_eligible": True,
                "status": "open",
                "topics": [],
                "entities": [],
                "title": "Test bead",
                "promoted": False,
            },
        }
    ]
    assert "qdrant upsert failed" not in caplog.text


def test_flag_default_off_and_env_on(monkeypatch):
    monkeypatch.delenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", raising=False)
    assert si._qdrant_external_embeddings_enabled() is False
    for val in ("1", "true", "on", "YES"):
        monkeypatch.setenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", val)
        assert si._qdrant_external_embeddings_enabled() is True
    monkeypatch.setenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", "0")
    assert si._qdrant_external_embeddings_enabled() is False


def test_external_mode_uses_separate_collection(tmp_path: Path, monkeypatch):
    # FastEmbed and external-embedding modes must resolve to DIFFERENT Qdrant
    # collections so 3072-dim OpenAI vectors never collide with an existing
    # ~384-dim FastEmbed collection (fixed vector params).
    monkeypatch.delenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", raising=False)
    monkeypatch.delenv("CORE_MEMORY_EMBEDDINGS_MODEL", raising=False)
    fastembed_name = si._vector_collection_name(tmp_path)

    monkeypatch.setenv("CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS", "1")
    monkeypatch.setenv("CORE_MEMORY_EMBEDDINGS_MODEL", "text-embedding-3-large")
    ext_name = si._vector_collection_name(tmp_path)

    assert ext_name != fastembed_name
    assert ext_name.startswith(fastembed_name)  # shares the root-scoped base
    assert "_ext_" in ext_name

    # A different model lands in yet another collection (its own dimension).
    monkeypatch.setenv("CORE_MEMORY_EMBEDDINGS_MODEL", "text-embedding-3-small")
    other_model_name = si._vector_collection_name(tmp_path)
    assert other_model_name != ext_name


def test_delta_metadata_preserves_retrieval_eligible_filter_payload():
    assert si._row_metadata({"status": "open", "session_id": "s1"})["retrieval_eligible"] is True
    assert si._row_metadata({"status": "open", "retrieval_eligible": False})["retrieval_eligible"] is False


def test_qdrant_backend_recreates_incompatible_existing_collection(monkeypatch):
    instances = []

    class FakeVectorParams:
        def __init__(self, *, size, distance):
            self.size = size
            self.distance = distance

    class FakeQdrantClient:
        def __init__(self, *args, **kwargs):
            self.deleted = []
            self.created = []
            instances.append(self)

        def get_collections(self):
            return types.SimpleNamespace(collections=[types.SimpleNamespace(name="core_memory_beads")])

        def get_collection(self, collection_name):
            # Existing collection is FastEmbed-sized/incompatible with OpenAI's
            # 3072-dim external vectors.
            vectors = types.SimpleNamespace(size=384)
            return types.SimpleNamespace(config=types.SimpleNamespace(params=types.SimpleNamespace(vectors=vectors)))

        def delete_collection(self, *, collection_name):
            self.deleted.append(collection_name)

        def create_collection(self, *, collection_name, vectors_config):
            self.created.append((collection_name, vectors_config.size))

    fake_qdrant = types.ModuleType("qdrant_client")
    fake_qdrant.QdrantClient = FakeQdrantClient
    fake_models = types.ModuleType("qdrant_client.models")
    fake_models.Distance = types.SimpleNamespace(COSINE="Cosine")
    fake_models.VectorParams = FakeVectorParams
    monkeypatch.setitem(sys.modules, "qdrant_client", fake_qdrant)
    monkeypatch.setitem(sys.modules, "qdrant_client.models", fake_models)

    from core_memory.retrieval.vector_backend import QdrantBackend

    QdrantBackend(collection_name="core_memory_beads", dimensions=3072)

    client = instances[0]
    assert client.deleted == ["core_memory_beads"]
    assert client.created == [("core_memory_beads", 3072)]


def test_query_path_fastembed_manifest_uses_hybrid_search(tmp_path: Path):
    # Manifest provider=fastembed -> native hybrid_search (query_text).
    # dimension=0 (FastEmbed sentinel) must be forwarded so QdrantBackend skips
    # VectorParams creation and does not delete/recreate the existing collection.
    beads = tmp_path / ".beads" / "semantic"
    beads.mkdir(parents=True)
    calls = {"hybrid": 0, "search": 0}
    captured_dim = {}

    class FakeBackend:
        def hybrid_search(self, query, k, filters):
            calls["hybrid"] += 1
            return [{"bead_id": "b1", "score": 0.9, "metadata": {}}]

        def search(self, query_embedding, k, filters):
            calls["search"] += 1
            return []

    def fake_create_external_backend(*, root, backend, dimension):
        captured_dim["dimension"] = dimension
        return FakeBackend()

    with patch("core_memory.retrieval.semantic_index._paths", return_value=(beads / "manifest.json",)), \
         patch("core_memory.retrieval.semantic_index._create_external_backend", side_effect=fake_create_external_backend):
        (beads / "manifest.json").write_text(json.dumps({"provider": "fastembed", "dimension": 1}), encoding="utf-8")
        rows = hybrid._qdrant_hybrid_rows(tmp_path, "when did melanie paint", k=8)

    assert calls == {"hybrid": 1, "search": 0}
    assert rows and rows[0]["bead_id"] == "b1"
    # Sentinel must be 0 — not the manifest's dim=1 — so the FastEmbed collection
    # is never wiped by a VectorParams(size=1) recreation.
    assert captured_dim["dimension"] == 0, f"expected dimension=0, got {captured_dim['dimension']}"


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
