"""Compatibility coverage for the retired mutating seed backfill."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from core_memory.runtime.hygiene.seed_backfill import (
    SEED_BACKFILL_APPLY_RETIRED,
    clean_entity_list,
    is_meaningful_entity,
    run_seed_quality_backfill,
)


def _seed_store(root: Path) -> None:
    detail = (
        "Acme Corp disputed the March invoice after the price change. "
        "Finance agreed to reissue it before the April close."
    )
    beads = {
        "bead-SEED0000001": {
            "type": "context",
            "title": "please fix the invoice for acme",
            "summary": [detail],
            "detail": detail,
            "session_id": "s1",
            "created_at": "2026-05-01T00:00:00+00:00",
            "retrieval_eligible": True,
            "status": "open",
            "entities": ["please", "Acme Corp", "tests/pipeline", "a1b2c3d4e5f6a7b8"],
            "tags": [],
        },
        "bead-SEED0000002": {
            "type": "document_reference",
            "title": "Q2 Vendor Review.pdf",
            "document_name": "Q2 Vendor Review.pdf",
            "summary": [detail[:80]],
            "detail": detail,
            "session_id": "s2",
            "created_at": "2026-05-02T00:00:00+00:00",
            "retrieval_eligible": True,
            "status": "open",
            "entities": [],
            "tags": [],
        },
    }
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": []}),
        encoding="utf-8",
    )


def test_legacy_entity_census_helpers_remain_read_only() -> None:
    assert is_meaningful_entity("Acme Corp") is True
    assert is_meaningful_entity("tests/pipeline") is False
    assert clean_entity_list(["Acme Corp", "acme corp", "please", "QuickBooks"]) == [
        "Acme Corp",
        "QuickBooks",
    ]


def test_dry_run_reports_without_writes_or_model_calls() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_store(root)
        index_path = root / ".beads" / "index.json"
        before = index_path.read_text(encoding="utf-8")
        with patch("core_memory.policy.semantic_task_runtime.get_semantic_task_runtime") as semantic_runtime:
            out = run_seed_quality_backfill(root)
        after = index_path.read_text(encoding="utf-8")

    assert out["ok"] is True
    assert out["applied"] is False
    assert out["deprecated"] is True
    assert out["entities"]["entities_removed"] >= 2
    assert out["enrichment"]["eligible"] >= 1
    assert after == before
    semantic_runtime.assert_not_called()


def test_apply_is_rejected_without_writes_or_model_calls() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_store(root)
        index_path = root / ".beads" / "index.json"
        before = index_path.read_text(encoding="utf-8")
        with patch("core_memory.policy.semantic_task_runtime.get_semantic_task_runtime") as semantic_runtime:
            out = run_seed_quality_backfill(root, apply=True)
        after = index_path.read_text(encoding="utf-8")

    assert out["ok"] is False
    assert out["applied"] is False
    assert out["error"] == SEED_BACKFILL_APPLY_RETIRED
    assert "reauthor_memory" in out["migration"]
    assert after == before
    assert "backup_path" not in out
    semantic_runtime.assert_not_called()


def test_http_route_retains_read_only_compatibility() -> None:
    try:
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app
    except Exception as exc:  # noqa: BLE001 - optional HTTP extras
        import pytest

        pytest.skip(f"fastapi stack unavailable: {exc}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _seed_store(root)
        response = TestClient(app).post(
            "/v1/memory/hygiene/seed-backfill",
            json={"root": str(root), "apply": False},
        )
        rejected = TestClient(app).post(
            "/v1/memory/hygiene/seed-backfill",
            json={"root": str(root), "apply": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["applied"] is False
    assert body["deprecated"] is True
    assert rejected.status_code == 400
    assert rejected.json()["error"] == SEED_BACKFILL_APPLY_RETIRED
