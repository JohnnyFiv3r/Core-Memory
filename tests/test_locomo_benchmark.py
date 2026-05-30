"""Tests for the LoCoMo benchmark adapter.

These tests use synthetic data that mirrors LoCoMo's structure without requiring
the actual corpus file. They cover:
  - scoring functions (unit)
  - loader conversion (unit)
  - ingest + QA round-trip (integration, uses a real temp dir)
  - contamination guard (unit)
"""
from __future__ import annotations

import pytest

from benchmarks.contracts import (
    BenchmarkConversation,
    BenchmarkQA,
    BenchmarkShortcutFlags,
    BenchmarkTurn,
)
from benchmarks.locomo.scoring import (
    aggregate_case_scores,
    compute_evidence_recall,
    multihop_f1,
    normalize_text,
    score_answer,
    token_f1,
)
from benchmarks.locomo.loader import locomo_samples_to_conversations


# ---------------------------------------------------------------------------
# Scoring unit tests
# ---------------------------------------------------------------------------


def test_normalize_text_removes_articles_and_punctuation():
    assert normalize_text("The quick Brown Fox!") == "quick brown fox"
    assert normalize_text("a cat sat on the mat.") == "cat sat on mat"
    assert normalize_text("") == ""


def test_token_f1_perfect_match():
    assert token_f1("hello world", "hello world") == 1.0


def test_token_f1_no_match():
    assert token_f1("hello", "world") == 0.0


def test_token_f1_partial_match():
    score = token_f1("hello world", "hello there")
    assert 0.0 < score < 1.0


def test_token_f1_empty_inputs():
    assert token_f1("", "") == 1.0
    assert token_f1("hello", "") == 0.0
    assert token_f1("", "hello") == 0.0


def test_multihop_f1_exact_sub_answer():
    # Category 1: gold "Alice, Bob" — match on first sub-answer
    assert multihop_f1("Alice", "Alice, Bob") == 1.0
    assert multihop_f1("Bob", "Alice, Bob") == 1.0


def test_score_answer_categories():
    # Cat 1: multihop
    assert score_answer(category=1, prediction="Alice", answer="Alice, Bob") == 1.0
    # Cat 2: token_f1
    assert score_answer(category=2, prediction="London", answer="London") == 1.0
    # Cat 3: strip after semicolon
    assert score_answer(category=3, prediction="London", answer="London; more details here") == 1.0
    # Cat 4: token_f1
    assert score_answer(category=4, prediction="engineer", answer="software engineer") > 0.0
    # Cat 5: always 1.0 (excluded from official eval, don't drag metrics)
    assert score_answer(category=5, prediction="wrong", answer="correct") == 1.0


def test_compute_evidence_recall_hit_rank1():
    result = compute_evidence_recall(
        gold_evidence=["D1:3"],
        retrieved=["D1:3", "D1:5", "D1:7"],
        ks=[1, 5],
    )
    assert result["recall@1"] == 1.0
    assert result["recall@5"] == 1.0
    assert result["mrr"] == 1.0
    assert result["hit_any"] is True
    assert result["vacuous"] is False


def test_compute_evidence_recall_miss_rank1_hit_rank3():
    result = compute_evidence_recall(
        gold_evidence=["D1:7"],
        retrieved=["D1:3", "D1:5", "D1:7"],
        ks=[1, 3, 5],
    )
    assert result["recall@1"] == 0.0
    assert result["recall@3"] == 1.0
    assert result["mrr"] == pytest.approx(1 / 3, rel=1e-3)


def test_compute_evidence_recall_vacuous():
    result = compute_evidence_recall(gold_evidence=[], retrieved=["D1:3"], ks=[1, 5])
    assert result["vacuous"] is True
    assert result["recall@1"] == 1.0
    assert result["recall@5"] == 1.0


def test_aggregate_case_scores_excludes_vacuous_from_primary():
    cases = [
        {
            "qa_id": "q1", "category": "4", "excluded": False,
            "answer_f1": 1.0,
            "evidence_recall": {"vacuous": False, "recall@1": 1.0, "recall@5": 1.0, "mrr": 1.0, "hit_any": True},
        },
        {
            "qa_id": "q2", "category": "2", "excluded": False,
            "answer_f1": 0.0,
            "evidence_recall": {"vacuous": True, "recall@1": 1.0, "recall@5": 1.0, "mrr": 1.0, "hit_any": True},
        },
    ]
    agg = aggregate_case_scores(cases)
    assert agg["total_cases"] == 2
    assert agg["cases_with_evidence"] == 1
    # Primary recall metrics come from non-vacuous cases only
    assert agg["overall"]["recall@1_mean"] == 1.0  # only q1 counts


# ---------------------------------------------------------------------------
# Loader unit tests
# ---------------------------------------------------------------------------


_SAMPLE_DATA = [
    {
        "sample_id": "s0",
        "turns": [
            {"dia_id": "D1:1", "speaker": "Alice", "text": "I work at Acme.", "session_index": 0, "turn_index": 0, "session_date_time": ""},
            {"dia_id": "D1:2", "speaker": "Bob", "text": "How long?", "session_index": 0, "turn_index": 1, "session_date_time": ""},
            {"dia_id": "D2:1", "speaker": "Alice", "text": "Three years.", "session_index": 1, "turn_index": 0, "session_date_time": ""},
        ],
        "qa_list": [
            {"qa_id": "q1", "question": "Where does Alice work?", "answer": "Acme", "gold_evidence": ["D1:1"], "category": 4},
            {"qa_id": "q5", "question": "Adversarial question?", "answer": "N/A", "gold_evidence": [], "category": 5},
        ],
    }
]


def test_loader_produces_benchmark_conversations():
    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    assert len(convs) == 1
    conv = convs[0]
    assert conv.benchmark_name == "locomo"
    assert conv.session_id == "locomo:s0"
    assert len(conv.turns) == 3


def test_loader_turn_id_format():
    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    assert convs[0].turns[0].turn_id == "locomo:s0:D1:1"
    assert convs[0].turns[0].metadata["dia_id"] == "D1:1"


def test_loader_excludes_category5():
    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    qa_ids = [q.qa_id for q in convs[0].qa_cases]
    assert "q1" in qa_ids
    assert "q5" not in qa_ids


def test_loader_gold_evidence_not_in_turn_metadata():
    """Gold evidence dia_ids must not appear in turn metadata."""
    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    conv = convs[0]
    gold_ids = {d for qa in conv.qa_cases for d in qa.gold_evidence}
    for turn in conv.turns:
        # turn.metadata may contain the turn's own dia_id but not QA gold evidence
        assert "gold_evidence" not in turn.metadata
        assert "expected_answer" not in turn.metadata


# ---------------------------------------------------------------------------
# Contamination guard unit tests
# ---------------------------------------------------------------------------


def test_shortcut_flags_faithful_default():
    flags = BenchmarkShortcutFlags()
    assert flags.is_faithful()


def test_shortcut_flags_oracle_gold_breaks_faithful():
    flags = BenchmarkShortcutFlags(oracle_gold_used=True)
    assert not flags.is_faithful()


def test_shortcut_flags_to_dict():
    flags = BenchmarkShortcutFlags()
    d = flags.to_dict()
    assert d["is_faithful"] is True
    assert all(v is False for k, v in d.items() if k != "is_faithful")


# ---------------------------------------------------------------------------
# Integration tests: ingest + QA round-trip
# ---------------------------------------------------------------------------


def test_ingest_builds_dia_bead_map(tmp_path):
    from benchmarks.locomo.ingest import ingest_conversation

    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    flags = BenchmarkShortcutFlags()
    dia_to_bead = ingest_conversation(str(tmp_path), convs[0], shortcut_flags=flags)

    assert "D1:1" in dia_to_bead
    assert "D1:2" in dia_to_bead
    assert "D2:1" in dia_to_bead
    # All bead IDs must be non-empty strings
    for dia_id, bead_id in dia_to_bead.items():
        assert bead_id.startswith("bead-"), f"Unexpected bead_id for {dia_id}: {bead_id}"


def test_ingest_non_faithful_flags_raises(tmp_path):
    from benchmarks.locomo.ingest import ingest_conversation

    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    dirty = BenchmarkShortcutFlags(oracle_gold_used=True)
    with pytest.raises(ValueError, match="non-faithful"):
        ingest_conversation(str(tmp_path), convs[0], shortcut_flags=dirty)


def test_qa_round_trip(tmp_path):
    from benchmarks.locomo.runner import run_conversation

    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    result = run_conversation(convs[0], shortcut_flags=BenchmarkShortcutFlags(), k=5, root=str(tmp_path))

    assert result["conversation_id"] == "s0"
    assert result["turn_count"] == 3
    assert result["qa_count"] == 1  # cat5 excluded
    assert result["dia_bead_map_size"] == 3

    case = result["cases"][0]
    assert case["qa_id"] == "q1"
    assert not case["excluded"]
    assert "recall@1" in case["evidence_recall"]
    assert isinstance(case["answer_f1"], float)
    # Evidence for "Where does Alice work?" includes D1:1 — should appear somewhere in top-5
    assert case["evidence_recall"]["recall@5"] == 1.0, "D1:1 must appear within top-5 for Acme question"


def test_suite_run_non_faithful_raises():
    from benchmarks.locomo.runner import run_locomo_suite

    convs = locomo_samples_to_conversations(_SAMPLE_DATA, exclude_categories={5})
    with pytest.raises(ValueError, match="non-faithful"):
        run_locomo_suite(convs, shortcut_flags=BenchmarkShortcutFlags(bead_direct_ingest=True))
