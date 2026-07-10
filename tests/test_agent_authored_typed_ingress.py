from __future__ import annotations

import inspect
import tempfile
from unittest.mock import patch

import pytest

from core_memory.association.crawler_contract import _normalize_creation_rows_with_diagnostics
from core_memory.integrations.mcp.registry import TOOLS
from core_memory.integrations.mcp.typed_write import MCP_TYPED_WRITE_TOOL_SCHEMAS
from core_memory.integrations.openclaw.hosted_capture_bridge import _build_http_payload
from core_memory.integrations.openclaw.runtime import coordinator_finalize_hook
from core_memory.integrations.pydanticai import authoring_prompt, run_with_memory
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.state import TurnEnvelope
from core_memory.runtime.turn.turn_prep import normalize_turn_request
from core_memory.schema.agent_authored_updates import (
    AGENT_AUTHORED_UPDATES_V1,
    AGENT_AUTHORED_V1_BEAD_FIELDS,
    AgentAuthoredBeadV1,
    agent_authored_updates_json_schema,
    validate_agent_authored_updates_v1_transport,
)


def _updates(*, title: str = "Typed authoring contract", retrieval_eligible: bool = True) -> dict:
    return {
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "beads_create": [
            {
                "creation_role": "current_turn",
                "type": "decision",
                "title": title,
                "summary": ["Core Memory accepts the schema-owned authored update."],
                "entities": ["Core Memory"],
                "retrieval_eligible": retrieval_eligible,
                "retrieval_title": "Core Memory typed authoring contract",
                "retrieval_facts": ["The typed authored update wins over its metadata alias."],
                "because": ["One schema must govern every ingress."],
                "source_turn_ids": ["t1"],
                "decision_keys": ["agent-led-write-integrity"],
                "claims": [
                    {
                        "subject": "Core Memory",
                        "slot": "authorship",
                        "value": "agent-led",
                        "reason_text": "The primary agent supplied the update.",
                        "confidence": 0.95,
                    }
                ],
            }
        ],
        "associations": [],
        "reviewed_beads": [],
    }


def _normalize(**overrides):
    kwargs = {
        "session_id": "s1",
        "turn_id": "t1",
        "transaction_id": "tx1",
        "trace_id": "tr1",
        "turns": [
            {"speaker": "user", "role": "user", "content": "Use the typed contract."},
            {"speaker": "assistant", "role": "assistant", "content": "Done."},
        ],
        "trace_depth": 0,
        "origin": "USER_TURN",
        "tools_trace": [],
        "mesh_trace": [],
        "window_turn_ids": [],
        "window_bead_ids": [],
        "crawler_updates": None,
        "authoring_mode": None,
        "metadata": {},
    }
    kwargs.update(overrides)
    return normalize_turn_request(**kwargs)


def test_schema_owned_contract_contains_full_bead_inventory() -> None:
    schema = agent_authored_updates_json_schema()
    bead_schema = schema["properties"]["beads_create"]["items"]

    assert schema["$id"] == AGENT_AUTHORED_UPDATES_V1
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_version", "beads_create", "associations", "reviewed_beads"}
    assert bead_schema["additionalProperties"] is False
    assert {
        "retrieval_title",
        "retrieval_facts",
        "decision_keys",
        "claims",
        "claim_updates",
        "goal_id",
        "state_change",
    }.issubset(bead_schema["properties"])
    assert set(AgentAuthoredBeadV1.__annotations__) == set(AGENT_AUTHORED_V1_BEAD_FIELDS)


def test_transport_contract_allows_empty_typed_entities_and_rejects_unknown_fields() -> None:
    pydantic = pytest.importorskip("pydantic")
    from core_memory.integrations.http.server import AgentAuthoredUpdatesRequest

    valid = _updates(retrieval_eligible=False)
    valid["beads_create"][0]["entities"] = []
    ok, errors = validate_agent_authored_updates_v1_transport(valid)
    assert ok, errors

    invalid = _updates()
    invalid["beads_create"][0]["invented_field"] = "never stored"
    ok, errors = validate_agent_authored_updates_v1_transport(invalid)
    assert not ok
    assert errors[0]["code"] == "unknown_fields"
    warn_model = AgentAuthoredUpdatesRequest.model_validate(invalid)
    assert warn_model.model_dump()["beads_create"][0]["invented_field"] == "never stored"
    with patch.dict("os.environ", {"CORE_MEMORY_AGENT_AUTHORED_MODE": "hard"}, clear=False):
        with pytest.raises(pydantic.ValidationError):
            AgentAuthoredUpdatesRequest.model_validate(invalid)

    bad_association = _updates()
    bad_association["associations"] = [
        {
            "source_bead_id": "$current_turn",
            "target_bead_id": "bead-prior",
            "relationship": "supports",
            "reason_text": "The current decision supports the prior goal.",
            "confidence": 0.9,
            "invented_relation_field": True,
        }
    ]
    ok, errors = validate_agent_authored_updates_v1_transport(bad_association)
    assert not ok
    assert any(error["code"] == "unknown_fields" for error in errors)


def test_top_level_authored_updates_win_over_metadata_alias_with_warning() -> None:
    typed = _updates(title="Typed update wins")
    alias = _updates(title="Metadata alias loses")
    req = _normalize(
        crawler_updates=typed,
        authoring_mode="inline",
        metadata={"crawler_updates": alias},
    )

    assert req["crawler_updates"]["beads_create"][0]["title"] == "Typed update wins"
    assert req["_crawler_updates_source"] == "crawler_updates"
    assert req["authorship_provenance"]["source"] == "primary_agent"
    assert req["authorship_warnings"] == [
        {
            "code": "metadata_crawler_updates_ignored",
            "winner": "crawler_updates",
            "ignored": "metadata.crawler_updates",
        }
    ]


def test_legacy_bead_judge_directive_maps_to_delegated_mode() -> None:
    req = _normalize(metadata={"bead_judge": "llm"})
    assert req["authoring_mode"] == "delegated"
    assert req["authorship_warnings"][0]["code"] == "bead_judge_directive_deprecated"


def test_envelope_hash_includes_authored_updates_and_authoring_mode() -> None:
    base = TurnEnvelope(session_id="s1", turn_id="t1", turns=[{"role": "assistant", "content": "done"}])
    base.finalize_hashes()
    inline = TurnEnvelope(
        session_id="s1",
        turn_id="t1",
        turns=[{"role": "assistant", "content": "done"}],
        crawler_updates=_updates(),
        authoring_mode="inline",
    )
    inline.finalize_hashes()
    delegated = TurnEnvelope(
        session_id="s1",
        turn_id="t1",
        turns=[{"role": "assistant", "content": "done"}],
        crawler_updates=_updates(),
        authoring_mode="delegated",
    )
    delegated.finalize_hashes()

    assert base.envelope_hash != inline.envelope_hash
    assert inline.envelope_hash != delegated.envelope_hash


def test_v1_retrieval_quality_bar_is_downgrade_only() -> None:
    incomplete = _updates()
    row = incomplete["beads_create"][0]
    row.pop("retrieval_title")
    row.pop("retrieval_facts")
    row.pop("because")
    rows, diagnostics = _normalize_creation_rows_with_diagnostics(incomplete)
    assert rows[0]["retrieval_eligible"] is False
    assert {
        "retrieval_title_missing_or_generic",
        "retrieval_facts_missing",
        "grounded_quality_signal_missing",
    } == set(diagnostics[0]["reasons"])

    valid_rows, valid_diagnostics = _normalize_creation_rows_with_diagnostics(_updates())
    assert valid_rows[0]["retrieval_eligible"] is True
    assert not [row for row in valid_diagnostics if row.get("code") == "retrieval_eligibility_downgraded"]

    authored_false = _updates(retrieval_eligible=False)
    false_rows, _ = _normalize_creation_rows_with_diagnostics(authored_false)
    assert false_rows[0]["retrieval_eligible"] is False


def test_mcp_capture_and_write_expose_the_same_generated_contract() -> None:
    pytest.importorskip("pydantic")
    from core_memory.integrations.http.server import AgentAuthoredUpdatesRequest, app

    generated = agent_authored_updates_json_schema()
    capture_schema = TOOLS["capture"].input_schema
    write_schema = MCP_TYPED_WRITE_TOOL_SCHEMAS["write_turn_finalized"]["input"]
    assert capture_schema["properties"]["crawler_updates"] == generated
    assert write_schema["properties"]["crawler_updates"] == generated
    assert capture_schema["properties"]["authoring_mode"]["enum"] == ["inline", "delegated"]
    assert write_schema["properties"]["authoring_mode"]["enum"] == ["inline", "delegated"]
    assert AgentAuthoredUpdatesRequest.model_json_schema() == generated

    http_body = app.openapi()["paths"]["/v1/memory/turn-finalized"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert http_body["properties"]["crawler_updates"]["anyOf"][0] == {
        "$ref": "#/components/schemas/AgentAuthoredUpdatesRequest"
    }
    assert http_body["properties"]["authoring_mode"]["anyOf"][0]["enum"] == ["inline", "delegated"]


def test_inline_capable_adapters_expose_contract_and_typed_fields() -> None:
    assert AGENT_AUTHORED_UPDATES_V1 in authoring_prompt()
    assert "crawler_updates" in inspect.signature(run_with_memory).parameters
    assert "authoring_mode" in inspect.signature(run_with_memory).parameters
    assert "crawler_updates" in inspect.signature(coordinator_finalize_hook).parameters
    assert "authoring_mode" in inspect.signature(coordinator_finalize_hook).parameters


def test_hosted_bridge_requests_delegated_authoring_without_bead_judge() -> None:
    payload = _build_http_payload(
        {"metadata": {"tenant_note": "keep"}, "success": True},
        {"sessionKey": "s1"},
        {
            "session_id": "s1",
            "turn_id": "t1",
            "transaction_id": "tx1",
            "trace_id": "tr1",
            "user_query": "Remember the contract.",
            "assistant_final": "Recorded.",
            "run_id": "r1",
        },
    )
    assert payload["authoring_mode"] == "delegated"
    assert payload["crawler_updates"] is None
    assert "bead_judge" not in payload["metadata"]

    inline_payload = _build_http_payload(
        {"crawler_updates": _updates(), "success": True},
        {"sessionKey": "s1"},
        {
            "session_id": "s1",
            "turn_id": "t1",
            "transaction_id": "tx1",
            "trace_id": "tr1",
            "user_query": "Remember the contract.",
            "assistant_final": "Recorded.",
            "run_id": "r1",
        },
    )
    assert inline_payload["authoring_mode"] == "inline"
    assert inline_payload["crawler_updates"] == _updates()

    invalid_inline_payload = _build_http_payload(
        {"crawler_updates": {"title": "narrow legacy output"}, "success": True},
        {"sessionKey": "s1"},
        {
            "session_id": "s1",
            "turn_id": "t1",
            "transaction_id": "tx1",
            "trace_id": "tr1",
            "user_query": "Remember the contract.",
            "assistant_final": "Recorded.",
            "run_id": "r1",
        },
    )
    assert invalid_inline_payload["authoring_mode"] == "delegated"
    assert invalid_inline_payload["crawler_updates"] is None
    assert invalid_inline_payload["metadata"]["inline_authorship_validation_errors"]


def test_processed_python_surface_persists_top_level_v1_updates() -> None:
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "warn",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "1",
                "CORE_MEMORY_AGENT_AUTHORED_SEMANTIC_GATE": "off",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ),
    ):
        out = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=[
                {"speaker": "user", "role": "user", "content": "Use the typed contract."},
                {"speaker": "assistant", "role": "assistant", "content": "Done."},
            ],
            crawler_updates=_updates(title="Typed surface persisted"),
            authoring_mode="inline",
            metadata={"crawler_updates": _updates(title="Alias must not persist")},
        )
        index = MemoryStore(root)._read_json(MemoryStore(root).beads_dir / "index.json")

    assert out["ok"] is True
    bead = next(iter(index["beads"].values()))
    assert bead["title"] == "Typed surface persisted"
    gate = out["crawler_handoff"]["agent_authored_gate"]
    assert gate["source"] == "crawler_updates"
    assert gate["authorship"]["source"] == "primary_agent"
    assert gate["warnings"][0]["code"] == "metadata_crawler_updates_ignored"


def test_warn_mode_drops_every_unknown_v1_field_and_reports_it() -> None:
    updates = _updates(title="Warn mode remains lossless")
    updates["unknown_top_level"] = "drop me"
    updates["beads_create"][0]["unknown_bead_field"] = "drop me too"
    with (
        tempfile.TemporaryDirectory() as root,
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "warn",
                "CORE_MEMORY_AGENT_AUTHORED_SEMANTIC_GATE": "off",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ),
    ):
        out = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=[{"speaker": "assistant", "role": "assistant", "content": "Persist known fields only."}],
            crawler_updates=updates,
            authoring_mode="inline",
        )
        store = MemoryStore(root)
        index = store._read_json(store.beads_dir / "index.json")

    assert out["ok"] is True
    bead = next(iter(index["beads"].values()))
    assert "unknown_bead_field" not in bead
    gate = out["crawler_handoff"]["agent_authored_gate"]
    dropped = {
        warning["field"] for warning in gate["warnings"] if warning.get("code") == "unknown_authored_field_dropped"
    }
    assert dropped == {"unknown_top_level", "unknown_bead_field"}


def test_delegated_mode_routes_through_full_schema_author_before_persistence() -> None:
    authorship = {
        "source": "delegated_semantic_agent",
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "prompt_version": "turn_memory_authoring.v1",
        "grounding_hash": "grounding-hash",
        "task_receipt_id": "receipt-1",
        "model_profile": {"model": "gpt-test"},
    }
    with (
        tempfile.TemporaryDirectory() as root,
        patch(
            "core_memory.runtime.passes.agent_crawler_invoke.author_turn_memory",
            return_value=(
                _updates(title="Delegated author persisted"),
                {
                    "attempted": True,
                    "ok": True,
                    "source": "delegated_semantic_agent",
                    "attempts": 1,
                    "error_code": None,
                    "authorship": authorship,
                },
            ),
        ),
        patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "warn",
                "CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN": "1",
                "CORE_MEMORY_AGENT_AUTHORED_SEMANTIC_GATE": "off",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
            },
            clear=False,
        ),
    ):
        out = process_turn_finalized(
            root=root,
            session_id="s1",
            turn_id="t1",
            turns=[
                {"speaker": "user", "role": "user", "content": "Delegate this write."},
                {"speaker": "assistant", "role": "assistant", "content": "Done."},
            ],
            authoring_mode="delegated",
        )
        store = MemoryStore(root)
        index = store._read_json(store.beads_dir / "index.json")

    assert out["ok"] is True
    assert next(iter(index["beads"].values()))["title"] == "Delegated author persisted"
    gate = out["crawler_handoff"]["agent_authored_gate"]
    assert gate["source"] == "delegated_semantic_agent"
    assert gate["authorship"] == authorship
