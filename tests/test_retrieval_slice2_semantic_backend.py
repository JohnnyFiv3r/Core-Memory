from __future__ import annotations

import json
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.lifecycle import mark_semantic_dirty
from core_memory.retrieval.semantic_index import build_semantic_index, semantic_lookup


def _idx(root: Path) -> dict:
    return json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))


def _set_retrieval_eligible(root: Path, bead_id: str, *, status: str = "open") -> None:
    idx = _idx(root)
    idx["beads"][bead_id]["retrieval_eligible"] = True
    idx["beads"][bead_id]["status"] = status
    (root / ".beads" / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def test_build_writes_manifest_and_rows(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_EMBEDDINGS_PROVIDER", "hash")
    s = MemoryStore(root=str(tmp_path))
    b = s.add_bead(type="decision", title="Redis fix", summary=["Raised pool size"], session_id="s1", source_turn_ids=["t1"])
    _set_retrieval_eligible(tmp_path, b, status="open")

    out = build_semantic_index(tmp_path)
    assert out.get("ok") is True
    assert Path(out.get("manifest")).exists()
    assert Path(out.get("rows")).exists()

    manifest = json.loads(Path(out.get("manifest")).read_text(encoding="utf-8"))
    assert manifest.get("row_count") >= 1
    assert manifest.get("backend") in {"faiss-hash", "lexical"}


def test_stale_serving_warns_and_keeps_results(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_EMBEDDINGS_PROVIDER", "hash")
    s = MemoryStore(root=str(tmp_path))
    b = s.add_bead(type="decision", title="Redis fix", summary=["Raised pool size"], session_id="s1", source_turn_ids=["t1"])
    _set_retrieval_eligible(tmp_path, b, status="open")

    build_semantic_index(tmp_path)
    mark_semantic_dirty(tmp_path, reason="test")

    out = semantic_lookup(tmp_path, "redis fix", k=5)
    assert out.get("ok") is True
    assert "semantic_index_stale" in (out.get("warnings") or [])
    assert isinstance(out.get("results") or [], list)


def test_cold_start_lookup_builds_index(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_EMBEDDINGS_PROVIDER", "hash")
    s = MemoryStore(root=str(tmp_path))
    b = s.add_bead(type="decision", title="Latency", summary=["cut p95"], session_id="s1", source_turn_ids=["t1"])
    _set_retrieval_eligible(tmp_path, b, status="open")

    out = semantic_lookup(tmp_path, "latency", k=3)
    assert out.get("ok") is True
    assert Path(tmp_path / ".beads" / "semantic" / "manifest.json").exists()

