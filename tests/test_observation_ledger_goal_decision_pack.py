from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.observation_ledger.constants import ADJUDICATION_SCHEMA, CASE_SCHEMA, GOLD_SCHEMA
from benchmarks.observation_ledger.pack import file_sha256, gold_checksum, runtime_input, validate_pack

ROOT = Path(__file__).resolve().parents[1]
PACK = (
    ROOT
    / "benchmarks"
    / "observation_ledger"
    / "packs"
    / "public"
    / "observations-goal-decision-v1"
)


def _load(relative: str) -> Any:
    return json.loads((PACK / relative).read_text(encoding="utf-8"))


def _indexed(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["case_id"]): row for row in rows}


def test_goal_decision_pack_has_25_accepted_cases_without_runtime_gold_leakage():
    validation = validate_pack(
        PACK,
        visibility="public",
        repo_root=ROOT,
        require_accepted=True,
    )

    assert validation.valid, validation.to_dict()
    assert validation.case_count == 25
    assert validation.bucket_counts["goal_decision"] == 25
    assert validation.bucket_counts["goal"] == 13
    assert validation.bucket_counts["decision"] == 12

    cases = _load("inputs/cases.json")
    assert [row["case_id"] for row in cases] == [f"obs-gd-{index:03d}" for index in range(1, 26)]
    for case in cases:
        assert case["schema_version"] == CASE_SCHEMA
        assert case["suite"] == "observations"
        assert case["privacy_classification"] == "public"
        assert case["expected_structural_behavior"]["assertions_may_be_empty"] is True
        assert case["expected_structural_behavior"]["exactly_one_observation_bead"] is True
        assert set(runtime_input(case)) == {"source_events", "admissible_evidence", "task_input"}
        assert not {
            "case_id",
            "suite",
            "behavioral_buckets",
            "expected_structural_behavior",
            "evidence_checksum",
            "gold",
            "adjudication",
        } & set(runtime_input(case))


def test_goal_decision_gold_is_observation_first_and_exactly_evidence_bound():
    cases = _indexed(_load("inputs/cases.json"))
    gold = _indexed(_load("gold/gold.json"))

    assert set(gold) == set(cases)
    preferred_counts = {"goal": 0, "decision": 0}
    evidence_span_count = 0
    canonical_facets = {
        "blocked",
        "incident",
        "hypothesis",
        "lesson",
        "principle",
        "precedent",
        "correction",
        "reversal",
        "checkpoint",
        "completion",
        "failure",
        "preference",
        "identity",
        "commitment",
        "state_change",
        "custom",
    }
    for case_id, expected in gold.items():
        case = cases[case_id]
        assert expected["schema_version"] == GOLD_SCHEMA
        semantic = expected["semantic_expectations"]
        preferred = semantic["preferred_primary_label"]
        preferred_counts[preferred] += 1
        assert preferred in expected["acceptable_labels"]
        assert set(expected["acceptable_labels"]) <= {"goal", "decision", "statement", "reflection"}
        assert semantic["assertion_expectation"]["required"] is False
        assert semantic["assertion_expectation"]["empty_is_valid"] is True
        assert semantic["associations_expected"] == []
        assert semantic["revisions_expected"] == []
        assert expected["supported_propositions"]
        assert expected["forbidden_propositions"]
        assert not set(expected["supported_propositions"]) & set(expected["forbidden_propositions"])

        event_by_id = {row["event_id"]: row for row in case["source_events"]}
        evidence_by_id = {row["evidence_id"]: row for row in case["admissible_evidence"]}
        assert len(expected["evidence_spans"]) == len(evidence_by_id)
        evidence_span_count += len(evidence_by_id)
        for span in expected["evidence_spans"]:
            evidence = evidence_by_id[span["evidence_id"]]
            assert span["source_event_id"] == evidence["source_event_id"]
            assert {"start": span["start"], "end": span["end"]} == evidence["span"]
            source = event_by_id[span["source_event_id"]]["content"]
            assert source[span["start"] : span["end"]] == span["quote"] == evidence["content"]

        facet_evidence = {
            evidence_id
            for facet in semantic["acceptable_facets"]
            for evidence_id in facet.get("evidence_refs") or []
        }
        assert facet_evidence <= set(evidence_by_id)
        assert {facet["kind"] for facet in semantic["acceptable_facets"]} <= canonical_facets

    assert preferred_counts == {"goal": 13, "decision": 12}
    assert evidence_span_count == 51
    assert gold["obs-gd-004"]["acceptable_labels"] == ["goal"]


def test_goal_decision_manifest_checksums_and_adjudications_are_exact():
    manifest = _load("manifest.json")
    listed = [
        str(relative)
        for field in ("cases", "gold", "adjudications")
        for relative in manifest[field]
    ]

    assert manifest["pack_id"] == "public-observations-goal-decision-v1"
    assert manifest["visibility"] == "public"
    assert manifest["schema_versions"] == [CASE_SCHEMA, GOLD_SCHEMA, ADJUDICATION_SCHEMA]
    assert manifest["adjudications"] == ["adjudications/adjudications.json"]
    assert set(manifest["checksums"]) == set(listed)
    assert manifest["checksums"] == {relative: file_sha256(PACK / relative) for relative in listed}

    adjudications = _indexed(_load("adjudications/adjudications.json"))
    gold = _indexed(_load("gold/gold.json"))
    assert set(adjudications) == set(gold)
    assert {row["human_decision"] for row in adjudications.values()} == {"revised"}
    assert {row["judge_model"] for row in adjudications.values()} == {"codex:gpt-5.6-sol-high"}
    assert {row["judge_execution"]["provider_attempt"]["status"] for row in adjudications.values()} == {
        "blocked"
    }
    for case_id, adjudication in adjudications.items():
        assert adjudication["accepted_gold_checksum"] == gold_checksum(gold[case_id])
        assert adjudication["judge_execution"]["model_selection_user_supplied"] is True
        assert adjudication["human_review"]["decision_source"] == "explicit_user_acceptance"
        assert adjudication["human_review"]["acceptance_scope"] == "all_25_revised_gold_records"
        assert adjudication["judge_critique"]["strengths"]
        if adjudication["human_decision"] == "accepted":
            assert adjudication["proposed_gold_checksum"] == adjudication["accepted_gold_checksum"]
            assert adjudication["judge_critique"]["acceptable_as_proposed"] is True
            assert adjudication["human_revision"] is None
        else:
            assert adjudication["proposed_gold_checksum"] != adjudication["accepted_gold_checksum"]
            assert adjudication["judge_critique"]["acceptable_as_proposed"] is False
            assert adjudication["human_revision"]["changes"]
