from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core_memory import maintain
from core_memory.integrations.mcp.typed_write import maintain as mcp_maintain
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.turn.reauthoring import (
    SEMANTIC_MAINTENANCE_RECEIPT_V1,
    semantic_backfill_report,
)
from core_memory.runtime.turn.semantic_state import get_semantic_write_state, mark_semantic_write_state
from core_memory.schema.agent_authored_updates import AGENT_AUTHORED_UPDATES_V1
from core_memory.schema.semantic_tasks import (
    TASK_TURN_MEMORY_AUTHORING,
    ModelProfile,
    SemanticTaskRequest,
    SemanticTaskResult,
)


class _SemanticAuthor:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid
        self.requests: list[SemanticTaskRequest] = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        turn_id = str(request.payload.get("turn_id") or "")
        output = {
            "schema_version": AGENT_AUTHORED_UPDATES_V1,
            "beads_create": [
                {
                    "creation_role": "current_turn",
                    "type": "context",
                    "title": "Governed semantic interpretation",
                    "summary": ["A delegated semantic author enriched durable source evidence."],
                    "entities": ["Core Memory"],
                    "retrieval_eligible": True,
                    "retrieval_title": "Agent-led semantic maintenance",
                    "retrieval_facts": ["Maintenance appends a new interpretation without rewriting its source."],
                    "supporting_facts": ["The source evidence was supplied in bounded visible context."],
                    "source_turn_ids": [turn_id],
                    "decision_keys": ["agent-led-semantic-maintenance"],
                    "claims": [
                        {
                            "subject": "Core Memory",
                            "predicate": "uses",
                            "object": "delegated semantic authorship",
                        }
                    ],
                }
            ],
            "associations": [],
            "reviewed_beads": [],
        }
        if not self.valid:
            output["beads_create"][0].pop("retrieval_eligible")
        return SemanticTaskResult(
            task_id=f"task-{len(self.requests)}",
            task_type=TASK_TURN_MEMORY_AUTHORING,
            ok=True,
            status="succeeded",
            output_json=output,
            model_profile=ModelProfile(
                tier="standard",
                provider="openai",
                model="gpt-test",
                runtime="provider",
            ),
            prompt_version=request.prompt_version,
            rubric_version=request.rubric_version,
            output_schema=request.output_schema,
            authority_boundary=request.authority_boundary,
            receipt_id=f"receipt-{len(self.requests)}",
        )


def _index(root: str) -> dict:
    return json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))


def _authority() -> dict:
    return {"actor": "test-operator", "allowed_authority": ["admin_repair"]}


def _turns() -> list[dict]:
    return [
        {
            "speaker": "user",
            "role": "user",
            "content": "Remember that semantic repair must stay agent-led.",
        },
        {
            "speaker": "assistant",
            "role": "assistant",
            "content": "I will preserve the finalized turn for repair.",
        },
    ]


def test_reauthor_preview_is_model_free_and_apply_is_append_only() -> None:
    runtime = _SemanticAuthor()
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
                "CORE_MEMORY_ASSOCIATION_JUDGE": "off",
            },
            clear=False,
        ),
        patch(
            "core_memory.policy.turn_memory_authoring.get_semantic_task_runtime",
            return_value=runtime,
        ),
    ):
        source_id = MemoryStore(root=root).add_bead(
            type="document_reference",
            title="Legacy source has enough grounding to inspect",
            summary=["The source predates the authored update contract."],
            entities=["Core Memory"],
            source_id="document-legacy-1",
            hydration_ref={"provider": "test", "object_id": "document-legacy-1"},
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        source_before = deepcopy(_index(root)["beads"][source_id])

        preview = maintain(
            root=root,
            action="reauthor_memory",
            targets={"bead_ids": [source_id]},
            authority=_authority(),
        )

        assert preview["ok"] is True
        assert preview["applied"] is False
        assert preview["sources"]["selected_bead_ids"] == [source_id]
        assert runtime.requests == []
        assert _index(root)["beads"][source_id] == source_before

        with patch(
            "core_memory.runtime.turn.reauthoring._association_after_commit",
            return_value={"ok": True, "status": "queued", "counts": {}},
        ) as association_after_commit:
            applied = maintain(
                root=root,
                action="reauthor_memory",
                targets={"bead_ids": [source_id]},
                authority=_authority(),
                dry_run=False,
                apply=True,
                idempotency_key="reauthor-source-once",
            )
        replayed = maintain(
            root=root,
            action="reauthor_memory",
            targets={"bead_ids": [source_id]},
            authority=_authority(),
            dry_run=False,
            apply=True,
            idempotency_key="reauthor-source-once",
        )

        index_after = _index(root)
        new_id = applied["results"][0]["bead_id"]
        new_bead = index_after["beads"][new_id]
        audit_path = Path(root) / ".beads" / "events" / "semantic-maintenance.jsonl"
        audit_exists = audit_path.exists()

    assert applied["ok"] is True
    assert applied["operation_contract"] == SEMANTIC_MAINTENANCE_RECEIPT_V1
    assert applied["counts"]["primary_writes"] == 1
    assert replayed["idempotent_replay"] is True
    assert len(runtime.requests) == 1
    assert index_after["beads"][source_id] == source_before
    assert new_id != source_id
    assert source_id in new_bead["derived_from_bead_ids"]
    assert f"bead:{source_id}" in new_bead["source_refs"]
    assert "agent_led_backfill" in new_bead["tags"]
    provenance = new_bead["source_attribution"]["core_memory_maintenance"]
    assert provenance["action"] == "reauthor_memory"
    assert provenance["authorship_source"] == "delegated_semantic_agent"
    assert provenance["task_receipt_id"] == "receipt-1"
    assert runtime.requests[0].metadata["maintenance_action"] == "reauthor_memory"
    assert source_id in runtime.requests[0].prompt
    association_after_commit.assert_called_once()
    assert audit_exists is True


def test_invalid_reauthoring_never_writes_or_runs_causal_coverage() -> None:
    runtime = _SemanticAuthor(valid=False)
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard", "CORE_MEMORY_ENRICHMENT_QUEUE": "off"},
            clear=False,
        ),
        patch(
            "core_memory.policy.turn_memory_authoring.get_semantic_task_runtime",
            return_value=runtime,
        ),
    ):
        source_id = MemoryStore(root=root).add_bead(
            type="context",
            title="Legacy source has enough grounding to inspect",
            summary=["This row must remain the only row when authorship is invalid."],
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        with patch("core_memory.runtime.turn.reauthoring._association_after_commit") as association_after_commit:
            out = maintain(
                root=root,
                action="reauthor_memory",
                targets={"bead_ids": [source_id]},
                authority=_authority(),
                dry_run=False,
                apply=True,
                idempotency_key="invalid-author",
            )
        beads = _index(root)["beads"]

    assert out["ok"] is False
    assert out["counts"]["failed"] == 1
    assert out["counts"]["primary_writes"] == 0
    assert set(beads) == {source_id}
    association_after_commit.assert_not_called()


def test_retry_pending_semantic_uses_preserved_turn_and_commits_canonical_bead() -> None:
    runtime = _SemanticAuthor()
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REPAIR": "0",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
                "CORE_MEMORY_ASSOCIATION_JUDGE": "off",
            },
            clear=False,
        ),
    ):
        pending = process_turn_finalized(
            root=root,
            session_id="pending-session",
            turn_id="pending-turn",
            turns=_turns(),
            authoring_mode="inline",
        )
        assert pending["semantic_status"] == "pending"
        assert pending["bead_id"] == ""

        preview = maintain(
            root=root,
            action="retry_pending_semantic",
            scope={"session_id": "pending-session", "turn_id": "pending-turn"},
            authority=_authority(),
        )
        assert preview["sources"]["examined"] == 1
        assert runtime.requests == []

        with (
            patch(
                "core_memory.policy.turn_memory_authoring.get_semantic_task_runtime",
                return_value=runtime,
            ),
            patch(
                "core_memory.runtime.turn.reauthoring._association_after_commit",
                return_value={"ok": True, "status": "queued", "counts": {}},
            ) as association_after_commit,
        ):
            applied = maintain(
                root=root,
                action="retry_pending_semantic",
                scope={"session_id": "pending-session", "turn_id": "pending-turn"},
                authority=_authority(),
                dry_run=False,
                apply=True,
                idempotency_key="retry-pending-once",
            )
        state = get_semantic_write_state(root, "pending-session", "pending-turn")
        bead = _index(root)["beads"][applied["results"][0]["bead_id"]]

    assert applied["ok"] is True
    assert applied["counts"]["committed"] == 1
    assert state and state["status"] == "committed"
    assert state["authorship"]["source"] == "delegated_semantic_agent"
    assert bead["source_turn_ids"] == ["pending-turn"]
    assert "retry_pending_semantic" in bead["tags"]
    assert [row["content"] for row in runtime.requests[0].payload["turns"]] == [row["content"] for row in _turns()]
    association_after_commit.assert_called_once()


def test_live_apply_requires_successful_copied_tenant_receipt() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_MAINTENANCE_ENVIRONMENT": "live_tenant"},
            clear=False,
        ),
    ):
        source_id = MemoryStore(root=root).add_bead(
            type="context",
            title="Legacy source has enough grounding to inspect",
            summary=["Live maintenance must prove the copied-tenant run first."],
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        denied = maintain(
            root=root,
            action="reauthor_memory",
            scope={"environment": "live_tenant"},
            targets={"bead_ids": [source_id]},
            authority=_authority(),
            dry_run=False,
            apply=True,
            idempotency_key="live-without-copy",
        )

    assert denied["ok"] is False
    assert denied["status"] == "validation_failed"
    assert denied["validation_errors"] == [
        {
            "field": "decision.copied_tenant_validation_receipt",
            "code": "copied_tenant_validation_required_before_live_apply",
        }
    ]


def test_live_apply_rejects_a_copied_receipt_for_a_different_plan() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_MAINTENANCE_ENVIRONMENT": "live_tenant"},
            clear=False,
        ),
    ):
        source_id = MemoryStore(root=root).add_bead(
            type="context",
            title="Legacy source has enough grounding to inspect",
            summary=["The copied run must cover the exact live selection."],
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        wrong_plan = {
            "ok": True,
            "applied": True,
            "action": "reauthor_memory",
            "environment": "copied_tenant",
            "operation_contract": SEMANTIC_MAINTENANCE_RECEIPT_V1,
            "plan_fingerprint": "different-plan",
        }
        out = maintain(
            root=root,
            action="reauthor_memory",
            scope={"environment": "live_tenant"},
            targets={"bead_ids": [source_id]},
            decision={"copied_tenant_validation_receipt": wrong_plan},
            authority=_authority(),
            dry_run=False,
            apply=True,
            idempotency_key="live-wrong-plan",
        )

    assert out["ok"] is False
    assert out["applied"] is False
    assert out["error"] == "copied_tenant_validation_required_before_live_apply"


def test_hosted_environment_configuration_prevents_live_store_mislabeling() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_MAINTENANCE_ENVIRONMENT": "live_tenant"},
            clear=False,
        ),
    ):
        source_id = MemoryStore(root=root).add_bead(
            type="context",
            title="Legacy source has enough grounding to inspect",
            summary=["A configured live store cannot be mislabeled as a copied tenant."],
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        out = maintain(
            root=root,
            action="reauthor_memory",
            scope={"environment": "copied_tenant"},
            targets={"bead_ids": [source_id]},
            authority=_authority(),
            dry_run=False,
            apply=True,
            idempotency_key="mislabel-live-store",
        )

    assert out["ok"] is False
    assert out["status"] == "validation_failed"
    assert {
        "field": "scope.environment",
        "code": "maintenance_environment_does_not_match_configured_store",
    } in out["validation_errors"]


def test_nonlocal_maintenance_requires_an_explicit_store_environment_binding() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict("os.environ", {}, clear=True),
    ):
        source_id = MemoryStore(root=root).add_bead(
            type="context",
            title="Legacy source has enough grounding to inspect",
            summary=["A non-local store role must be configured before maintenance can run."],
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        out = maintain(
            root=root,
            action="reauthor_memory",
            scope={"environment": "copied_tenant"},
            targets={"bead_ids": [source_id]},
            authority=_authority(),
            dry_run=False,
            apply=True,
            idempotency_key="unbound-copied-store",
        )

    assert out["ok"] is False
    assert out["status"] == "validation_failed"
    assert {
        "field": "CORE_MEMORY_MAINTENANCE_ENVIRONMENT",
        "code": "configured_maintenance_environment_required",
    } in out["validation_errors"]


def test_backfill_report_keeps_legacy_v1_and_backfilled_cohorts_separate() -> None:
    with tempfile.TemporaryDirectory() as root:
        store = MemoryStore(root=root)
        legacy_id = store.add_bead(
            type="context",
            title="Legacy cohort row",
            summary=["Stored before the v1 authored contract."],
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        v1_id = store.add_bead(
            type="context",
            title="V1 cohort row",
            summary=["Stored under the authored contract."],
            retrieval_title="V1 authored row",
            retrieval_facts=["This row is tagged as v1 authored."],
            session_id="v1-session",
            source_turn_ids=["v1-turn"],
        )
        store.add_bead(
            type="lesson",
            title="V1 companion row",
            summary=["Derived companions inherit the authored cohort through explicit lineage."],
            retrieval_title="V1 authored companion",
            retrieval_facts=["The companion derives from the canonical v1 bead."],
            derived_from_bead_ids=[v1_id],
            session_id="v1-session",
            source_turn_ids=[],
        )
        mark_semantic_write_state(
            root,
            session_id="v1-session",
            turn_id="v1-turn",
            status="committed",
            bead_id=v1_id,
            authorship={
                "source": "inline_agent",
                "schema_version": AGENT_AUTHORED_UPDATES_V1,
            },
        )
        backfilled_id = store.add_bead(
            type="context",
            title="Backfilled cohort row",
            summary=["Appended by governed semantic maintenance."],
            retrieval_title="Backfilled authored row",
            retrieval_facts=["This row belongs in the separate backfill cohort."],
            claims=[{"subject": "Core Memory", "predicate": "appends", "object": "revisions"}],
            decision_keys=["append-only-backfill"],
            tags=["agent_led_backfill", AGENT_AUTHORED_UPDATES_V1],
            session_id="backfill-session",
            source_turn_ids=["backfill-turn"],
        )
        decision_path = Path(root) / ".beads" / "events" / "association-judge-decisions.jsonl"
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "decisions": [
                        {
                            "action": "no_link",
                            "source_bead": backfilled_id,
                            "target_bead": legacy_id,
                        }
                    ],
                }
            )
            + "\n"
            + json.dumps(
                {
                    "status": "pending_judge",
                    "source_bead_ids": [backfilled_id, legacy_id],
                    "no_candidate_source_bead_ids": [legacy_id],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        report = semantic_backfill_report(root)
        public_report = maintain(root=root, action="semantic_backfill_report")

    assert {legacy_id, v1_id, backfilled_id}
    assert report["cohorts"]["legacy"]["beads"] == 1
    assert report["cohorts"]["v1_authored"]["beads"] == 2
    assert report["cohorts"]["backfilled"]["beads"] == 1
    assert report["cohorts"]["backfilled"]["claims"] == 1
    assert report["cohorts"]["backfilled"]["semantic_keys"] == 1
    assert report["cohorts"]["backfilled"]["association_decisions"]["no_link"] == 1
    assert report["cohorts"]["backfilled"]["association_decisions"]["pending_judge"] == 1
    assert report["cohorts"]["legacy"]["association_decisions"]["pending_judge"] == 0
    assert public_report["ok"] is True
    assert public_report["operation_contract"] == "memory.semantic_backfill_report.v1"
    assert public_report["cohorts"] == report["cohorts"]


def test_http_and_mcp_expose_semantic_maintenance_preview() -> None:
    try:
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app
    except Exception as exc:  # noqa: BLE001 - optional HTTP extras
        import pytest

        pytest.skip(f"fastapi stack unavailable: {exc}")

    with tempfile.TemporaryDirectory() as root:
        source_id = MemoryStore(root=root).add_bead(
            type="context",
            title="Legacy source has enough grounding to inspect",
            summary=["The generic transports must expose governed preview semantics."],
            session_id="legacy-session",
            source_turn_ids=["legacy-turn"],
        )
        payload = {
            "action": "reauthor_memory",
            "targets": {"bead_ids": [source_id]},
            "authority": _authority(),
        }
        http = TestClient(app).post(
            "/v1/memory/maintain",
            json={"root": root, **payload},
        )
        mcp = mcp_maintain(root=root, **payload)

    assert http.status_code == 200
    assert http.json()["sources"]["selected_bead_ids"] == [source_id]
    assert mcp["sources"]["selected_bead_ids"] == [source_id]
    assert http.json()["applied"] is False
    assert mcp["applied"] is False
