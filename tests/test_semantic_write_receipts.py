from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core_memory.integrations.api import write_turn_finalized as python_write_turn_finalized
from core_memory.integrations.mcp.typed_write import write_turn_finalized as mcp_write_turn_finalized
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import emit_turn_finalized, process_flush, process_turn_finalized
from core_memory.runtime.queue.side_effect_queue import drain_side_effect_queue, side_effect_queue_status
from core_memory.runtime.queue.worker import SidecarPolicy
from core_memory.runtime.turn.semantic_state import (
    SEMANTIC_WRITE_STATUS_V1,
    get_semantic_write_state,
    mark_semantic_write_state,
    semantic_write_health,
)
from core_memory.runtime.turn.receipt import public_association_status
from core_memory.schema.agent_authored_updates import AGENT_AUTHORED_UPDATES_V1
from core_memory.schema.turn_receipt import TURN_FINALIZED_RECEIPT_V2


def _updates(*, derived: bool = False) -> dict:
    rows = [
        {
            "creation_role": "current_turn",
            "type": "decision",
            "title": "Commit semantic state from canonical lookup",
            "summary": ["The canonical bead is semantic-write truth."],
            "entities": ["Core Memory"],
            "retrieval_eligible": True,
            "retrieval_title": "Canonical semantic write state",
            "retrieval_facts": ["A mechanical memory pass cannot commit semantic state."],
            "because": ["Flush and callers must observe the same durable invariant."],
            "source_turn_ids": ["t1"],
            "decision_keys": ["semantic-write-state"],
        }
    ]
    if derived:
        rows.append(
            {
                "creation_role": "derived",
                "type": "lesson",
                "title": "Derived writes do not erase the primary",
                "summary": ["A derived failure is reported independently."],
                "entities": ["Core Memory"],
                "retrieval_eligible": True,
                "retrieval_title": "Independent derived write failure",
                "retrieval_facts": ["The primary current-turn bead remains committed."],
                "because": ["Canonical turn continuity takes priority."],
                "source_turn_ids": ["t1"],
                "derived_from_bead_ids": ["$current_turn"],
            }
        )
    return {
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "beads_create": rows,
        "associations": [],
        "reviewed_beads": [],
    }


def _turns() -> list[dict]:
    return [
        {"speaker": "user", "role": "user", "content": "Record semantic truth."},
        {"speaker": "assistant", "role": "assistant", "content": "Recorded."},
    ]


def test_processed_receipt_commits_only_after_canonical_lookup_and_appends_history() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict("os.environ", {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False),
    ):
        receipt = python_write_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(),
            authoring_mode="inline",
        )
        state = get_semantic_write_state(root, "s1", "t1")
        history_path = Path(root) / ".beads" / "events" / "semantic-write-status.jsonl"
        history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]

    assert receipt["contract"] == TURN_FINALIZED_RECEIPT_V2
    assert receipt["accepted"] is True
    assert receipt["ok"] is True
    assert receipt["semantic_status"] == "committed"
    assert receipt["bead_id"]
    assert receipt["validation"]["valid"] is True
    assert state and state["status"] == "committed"
    assert state["bead_id"] == receipt["bead_id"]
    assert [row["status"] for row in history] == ["pending", "committed"]
    assert all(row["schema"] == SEMANTIC_WRITE_STATUS_V1 for row in history)


def test_python_mcp_and_http_return_the_same_v2_receipt_shape() -> None:
    with (
        tempfile.TemporaryDirectory() as root_python,
        patch.dict("os.environ", {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False),
    ):
        py_receipt = python_write_turn_finalized(
            root=root_python,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(),
            authoring_mode="inline",
        )
    with (
        tempfile.TemporaryDirectory() as root_mcp,
        patch.dict("os.environ", {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False),
    ):
        mcp_receipt = mcp_write_turn_finalized(
            root=root_mcp,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(),
            authoring_mode="inline",
        )

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from core_memory.integrations.http.server import app

    with (
        tempfile.TemporaryDirectory() as root_http,
        patch.dict("os.environ", {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False),
    ):
        response = TestClient(app).post(
            "/v1/memory/turn-finalized",
            json={
                "root": root_http,
                "session_id": "s1",
                "turn_id": "t1",
                "turns": _turns(),
                "crawler_updates": _updates(),
                "authoring_mode": "inline",
            },
        )
        assert response.status_code == 200
        http_receipt = response.json()

    assert set(py_receipt) == set(mcp_receipt) == set(http_receipt)
    assert {py_receipt["contract"], mcp_receipt["contract"], http_receipt["contract"]} == {TURN_FINALIZED_RECEIPT_V2}
    assert {py_receipt["semantic_status"], mcp_receipt["semantic_status"], http_receipt["semantic_status"]} == {
        "committed"
    }


@pytest.mark.parametrize(
    ("internal", "public"),
    [
        ("linked", "complete"),
        ("no_supported_links", "complete"),
        ("deferred", "pending"),
        ("pending_judge", "pending_judge"),
        ("judge_failed", "failed"),
        ("quarantined", "failed"),
        ("skipped_ineligible", "skipped"),
    ],
)
def test_public_association_status_mapping(internal: str, public: str) -> None:
    assert public_association_status(internal) == public


def test_derived_failure_is_reported_without_erasing_committed_primary() -> None:
    original_add = MemoryStore.add_bead
    calls = 0

    def fail_second_add(self: MemoryStore, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("derived write failed")
        return original_add(self, **kwargs)

    with (
        tempfile.TemporaryDirectory() as root,
        patch.object(MemoryStore, "add_bead", fail_second_add),
    ):
        out = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(derived=True),
            authoring_mode="inline",
            policy=SidecarPolicy(max_create_per_turn=3),
        )

    assert out["ok"] is True
    assert out["semantic_status"] == "committed"
    assert out["bead_id"]
    assert out["derived"]["written"] == 0
    assert out["derived"]["failures"][0]["code"] == "derived_bead_persistence_failed"


def test_queue_failure_stays_pending_and_retries_to_committed() -> None:
    with tempfile.TemporaryDirectory() as root:
        with patch(
            "core_memory.runtime.queue.side_effect_queue.drain_side_effect_queue",
            return_value={"ok": True, "processed": 0, "failed": 1, "queue_depth": 1, "item_results": []},
        ):
            pending = process_turn_finalized(
                root=root,
                session_id="s1",
                turn_id="t1",
                turns=_turns(),
                crawler_updates=_updates(),
                authoring_mode="inline",
            )
        queue_before = side_effect_queue_status(root)

        drained = drain_side_effect_queue(root=root, max_items=1)
        committed = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(),
            authoring_mode="inline",
        )

    assert pending["ok"] is False
    assert pending["semantic_status"] == "pending"
    assert pending["queue"]["status"] == "failed"
    assert queue_before["queue_depth"] >= 1
    assert drained["processed"] == 1
    assert committed["ok"] is True
    assert committed["semantic_status"] == "committed"


def test_latest_only_flush_blocks_latest_pending_but_not_older_pending() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict("os.environ", {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False),
    ):
        older = emit_turn_finalized(root=root, session_id="s1", turn_id="t1", turns=_turns())
        assert older["emitted"] is True
        committed = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t2",
            turns=_turns(),
            crawler_updates={
                **_updates(),
                "beads_create": [{**_updates()["beads_create"][0], "source_turn_ids": ["t2"]}],
            },
            authoring_mode="inline",
        )
        assert committed["semantic_status"] == "committed"
        flush = process_flush(root=root, session_id="s1", promote=False, token_budget=800, max_beads=20)
        health = semantic_write_health(root)

    assert flush["ok"] is True
    assert flush["latest_turn_id"] == "t2"
    assert flush["semantic_barrier"]["semantic_status"] == "committed"
    assert health["pending_count"] == 1
    assert health["turns"][0]["turn_id"] == "t1"


def test_latest_pending_turn_blocks_until_audited_waiver() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict("os.environ", {"CORE_MEMORY_ENRICHMENT_QUEUE": "off"}, clear=False),
    ):
        committed = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(),
            authoring_mode="inline",
        )
        assert committed["semantic_status"] == "committed"
        emitted = emit_turn_finalized(root=root, session_id="s1", turn_id="t2", turns=_turns())
        assert emitted["emitted"] is True
        blocked = process_flush(root=root, session_id="s1", promote=False, token_budget=800, max_beads=20)
        invalid_override = process_flush(
            root=root,
            session_id="s1",
            promote=False,
            token_budget=800,
            max_beads=20,
            semantic_override=True,
        )
        waived = process_flush(
            root=root,
            session_id="s1",
            promote=False,
            token_budget=800,
            max_beads=20,
            semantic_override=True,
            override_operator="test-operator",
            override_reason="explicit test waiver",
        )

    assert blocked["ok"] is False
    assert blocked["error"] == "semantic_write_barrier_not_satisfied"
    assert blocked["barrier"]["latest_turn_id"] == "t2"
    assert invalid_override["error"] == "invalid_semantic_flush_override"
    assert waived["ok"] is True
    assert waived["semantic_barrier"]["semantic_status"] == "waived"
    assert waived["semantic_barrier"]["waiver_id"].startswith("swv-")


def test_pending_semantic_health_warns_at_five_minutes_and_is_critical_at_sixty() -> None:
    with tempfile.TemporaryDirectory() as root:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mark_semantic_write_state(
            root,
            session_id="s1",
            turn_id="t1",
            status="pending",
            retryable=True,
            now=start,
        )
        warning = semantic_write_health(root, now=start + timedelta(minutes=5, seconds=1))
        critical = semantic_write_health(root, now=start + timedelta(hours=1, seconds=1))

    assert warning["severity"] == "warning"
    assert warning["warning_count"] == 1
    assert warning["turns"][0]["age_seconds"] == 301
    assert critical["severity"] == "critical"
    assert critical["critical_count"] == 1
    assert critical["oldest_pending_age_seconds"] == 3601


def test_doctor_reports_old_pending_semantic_turn_as_critical() -> None:
    from core_memory.cli.handlers.setup import _pending_semantic_probe

    with tempfile.TemporaryDirectory() as root:
        MemoryStore(root)
        mark_semantic_write_state(
            root,
            session_id="s1",
            turn_id="t1",
            status="pending",
            retryable=True,
            now=datetime.now(timezone.utc) - timedelta(hours=1, seconds=5),
        )
        probe = _pending_semantic_probe(root)

    assert probe["status"] == "error"
    assert probe["health"]["severity"] == "critical"
    assert probe["health"]["critical_count"] == 1
