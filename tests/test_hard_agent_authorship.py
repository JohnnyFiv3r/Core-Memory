from __future__ import annotations

import tempfile
from copy import deepcopy
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_flush, process_turn_finalized
from core_memory.runtime.passes.agent_authored_contract import validate_agent_authored_updates
from core_memory.runtime.queue.worker import SidecarPolicy
from core_memory.runtime.turn.semantic_state import get_semantic_write_state
from core_memory.schema.agent_authored_updates import AGENT_AUTHORED_UPDATES_V1


def _turns() -> list[dict]:
    return [
        {"speaker": "user", "role": "user", "content": "Record the hard authorship invariant."},
        {"speaker": "assistant", "role": "assistant", "content": "Recorded."},
    ]


def _updates(*, turn_id: str = "t1", derived_count: int = 0, entities: list[str] | None = None) -> dict:
    rows = [
        {
            "creation_role": "current_turn",
            "type": "decision",
            "title": "Require typed semantic authorship",
            "summary": ["Missing or invalid authorship remains pending."],
            "entities": ["Core Memory"] if entities is None else entities,
            "retrieval_eligible": True,
            "retrieval_title": "Core Memory hard semantic authorship",
            "retrieval_facts": ["Hard mode never writes a deterministic context bead."],
            "because": ["The raw turn event is the never-forget source."],
            "source_turn_ids": [turn_id],
            "decision_keys": ["hard-authorship"],
        }
    ]
    for index in range(derived_count):
        rows.append(
            {
                "creation_role": "derived",
                "type": "lesson",
                "title": f"Derived semantic result {index + 1}",
                "summary": ["Derived rows cite the canonical turn bead."],
                "entities": [],
                "retrieval_eligible": False,
                "because": ["The companion meaning follows from the canonical decision."],
                "source_turn_ids": [],
                "derived_from_bead_ids": ["$current_turn"],
            }
        )
    return {
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "beads_create": rows,
        "associations": [],
        "reviewed_beads": [],
    }


def test_v1_cardinality_is_contract_owned_and_allows_two_derived_rows() -> None:
    updates = _updates(derived_count=2)
    ok, code, details = validate_agent_authored_updates(
        updates,
        max_create_per_turn=1,
        turn_id="t1",
        require_v1=True,
    )
    assert ok is True
    assert code is None
    assert details["beads_create_count"] == 3

    duplicate = deepcopy(updates)
    duplicate["beads_create"][1]["creation_role"] = "current_turn"
    duplicate["beads_create"][1]["source_turn_ids"] = ["t1"]
    ok, code, details = validate_agent_authored_updates(duplicate, turn_id="t1", require_v1=True)
    assert ok is False
    assert code == "agent_bead_fields_missing"
    assert details["reason"] == "current_turn_cardinality_invalid"


def test_runtime_persists_two_derived_rows_despite_sidecar_creation_limit() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard", "CORE_MEMORY_ENRICHMENT_QUEUE": "off"},
            clear=False,
        ),
    ):
        receipt = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(derived_count=2),
            authoring_mode="inline",
            policy=SidecarPolicy(max_create_per_turn=1),
        )
        store = MemoryStore(root)
        index = store._read_json(store.beads_dir / "index.json")

    assert receipt["semantic_status"] == "committed"
    assert receipt["derived"]["written"] == 2
    assert len(index["beads"]) == 3


def test_derived_rows_require_exact_current_turn_sentinel_and_distinct_source() -> None:
    invalid_link = _updates(derived_count=1)
    invalid_link["beads_create"][1]["derived_from_bead_ids"] = ["bead-other"]
    ok, _, details = validate_agent_authored_updates(invalid_link, turn_id="t1", require_v1=True)
    assert ok is False
    assert details["reason"] == "derived_current_turn_link_invalid"

    invalid_source = _updates(derived_count=1)
    invalid_source["beads_create"][1]["source_turn_ids"] = ["t1"]
    ok, _, details = validate_agent_authored_updates(invalid_source, turn_id="t1", require_v1=True)
    assert ok is False
    assert details["reason"] == "derived_row_claims_current_turn_source"


def test_hard_contract_accepts_empty_typed_entities() -> None:
    ok, code, _ = validate_agent_authored_updates(_updates(entities=[]), turn_id="t1", require_v1=True)
    assert ok is True
    assert code is None


def test_missing_hard_authorship_stays_pending_without_context_bead() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard", "CORE_MEMORY_ENRICHMENT_QUEUE": "off"},
            clear=False,
        ),
    ):
        receipt = process_turn_finalized(root=root, session_id="s1", turn_id="t1", turns=_turns())
        store = MemoryStore(root)
        index = store._read_json(store.beads_dir / "index.json")
        state = get_semantic_write_state(root, "s1", "t1")

    assert receipt["accepted"] is True
    assert receipt["ok"] is False
    assert receipt["semantic_status"] == "pending"
    assert receipt["error_code"] == "agent_updates_missing"
    assert receipt["bead_id"] == ""
    assert receipt["queue"]["status"] == "skipped"
    assert receipt["authorship"]["used_fallback"] is False
    assert index.get("beads") == {}
    assert state and state["status"] == "pending"


def test_invalid_hard_authorship_requires_repair_without_writing_stub() -> None:
    invalid = _updates()
    invalid["beads_create"][0].pop("because")
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard", "CORE_MEMORY_ENRICHMENT_QUEUE": "off"},
            clear=False,
        ),
    ):
        receipt = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=invalid,
            authoring_mode="inline",
        )
        store = MemoryStore(root)
        index = store._read_json(store.beads_dir / "index.json")

    assert receipt["semantic_status"] == "repair_required"
    assert receipt["error_code"] == "agent_causal_rationale_missing"
    assert receipt["bead_id"] == ""
    assert index.get("beads") == {}


def test_idempotent_invalid_retry_keeps_repair_required_status() -> None:
    invalid = _updates()
    invalid["beads_create"][0].pop("because")
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard", "CORE_MEMORY_ENRICHMENT_QUEUE": "off"},
            clear=False,
        ),
    ):
        first = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=invalid,
            authoring_mode="inline",
        )
        retried = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=invalid,
            authoring_mode="inline",
        )

    assert first["semantic_status"] == "repair_required"
    assert retried["semantic_status"] == "repair_required"


def test_valid_primary_v1_row_never_passes_through_narrow_judge_fallback() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_BEAD_JUDGE_FALLBACK": "1",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ),
        patch("core_memory.runtime.engine.judge_bead_fields", side_effect=AssertionError("judge must not run")),
    ):
        receipt = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=_updates(),
            authoring_mode="inline",
        )
        store = MemoryStore(root)
        bead = next(iter(store._read_json(store.beads_dir / "index.json")["beads"].values()))

    assert receipt["semantic_status"] == "committed"
    assert receipt["authorship"]["source"] == "primary_agent"
    assert receipt["authorship"]["used_fallback"] is False
    assert bead["title"] == "Require typed semantic authorship"
    assert "bead_judge_fallback" not in bead.get("tags", [])


def test_explicit_repair_uses_full_contract_and_field_provenance() -> None:
    invalid = _updates()
    invalid["beads_create"][0].pop("because")
    repaired = _updates()
    authorship = {
        "source": "repair_agent",
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "repair_used": True,
        "repaired_fields": ["$.beads_create[0].because"],
        "field_provenance": {"$.beads_create[0].because": {"source": "repair_agent", "task_receipt_id": "repair-1"}},
        "primary_authorship": {"source": "primary_agent"},
        "repair_authorship": {"source": "repair_agent", "task_receipt_id": "repair-1"},
    }
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard", "CORE_MEMORY_ENRICHMENT_QUEUE": "off"},
            clear=False,
        ),
        patch(
            "core_memory.runtime.engine.repair_turn_memory",
            return_value=(
                repaired,
                {"attempted": True, "ok": True, "source": "repair_agent", "authorship": authorship},
            ),
        ) as repair,
    ):
        receipt = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=invalid,
            authoring_mode="inline",
            policy=SidecarPolicy(max_create_per_turn=1, semantic_repair_enabled=True),
        )

    repair.assert_called_once()
    assert receipt["semantic_status"] == "committed"
    assert receipt["authorship"]["source"] == "repair_agent"
    assert receipt["authorship"]["repair_used"] is True
    assert receipt["authorship"]["repaired_fields"] == ["$.beads_create[0].because"]
    assert receipt["authorship"]["primary_authorship"]["source"] == "primary_agent"
    assert receipt["authorship"]["field_provenance"]["$.beads_create[0].because"]["source"] == "repair_agent"


def test_repair_is_not_invoked_without_explicit_policy() -> None:
    invalid = _updates()
    invalid["beads_create"][0].pop("because")
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_AGENT_AUTHORED_REPAIR": "0",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ),
        patch("core_memory.runtime.engine.repair_turn_memory") as repair,
    ):
        receipt = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=invalid,
            authoring_mode="inline",
        )

    repair.assert_not_called()
    assert receipt["semantic_status"] == "repair_required"


def test_authored_claims_persist_when_extraction_flags_are_off() -> None:
    updates = _updates()
    updates["beads_create"][0]["claims"] = [{"subject": "Core Memory", "slot": "authorship", "value": "agent-led"}]
    updates["beads_create"][0]["claim_updates"] = [
        {"subject": "Core Memory", "slot": "mode", "old_value": "warn", "new_value": "hard"}
    ]
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "hard",
                "CORE_MEMORY_CLAIM_LAYER": "0",
                "CORE_MEMORY_CLAIM_EXTRACTION_MODE": "off",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ),
    ):
        receipt = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=updates,
            authoring_mode="inline",
        )
        store = MemoryStore(root)
        bead = next(iter(store._read_json(store.beads_dir / "index.json")["beads"].values()))

    assert receipt["semantic_status"] == "committed"
    assert bead["claims"] == updates["beads_create"][0]["claims"]
    assert bead["claim_updates"] == updates["beads_create"][0]["claim_updates"]


def test_latest_repair_required_state_blocks_flush_even_if_legacy_stub_exists() -> None:
    invalid = _updates()
    invalid["beads_create"][0].pop("because")
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard", "CORE_MEMORY_ENRICHMENT_QUEUE": "off"},
            clear=False,
        ),
    ):
        pending = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=_turns(),
            crawler_updates=invalid,
            authoring_mode="inline",
        )
        stub_id = MemoryStore(root).add_bead(
            type="context",
            title="Legacy deterministic stub",
            summary=["This must not satisfy the hard semantic barrier."],
            session_id="s1",
            source_turn_ids=["t1"],
            retrieval_eligible=False,
            tags=["seeded_by_engine"],
        )
        flush = process_flush(root=root, session_id="s1", promote=False, token_budget=800, max_beads=20)

    assert pending["semantic_status"] == "repair_required"
    assert flush["ok"] is False
    assert flush["error"] == "semantic_write_barrier_not_satisfied"
    assert flush["barrier"]["semantic_status"] == "repair_required"
    assert flush["barrier"]["canonical_bead_id"] == stub_id
