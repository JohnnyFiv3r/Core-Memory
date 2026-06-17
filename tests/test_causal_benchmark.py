"""Tests for the causal-chain reconstruction benchmark."""
from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.causal.schema import (
    CausalCase,
    CausalGold,
    build_cases,
    validate_fixture_row,
    validate_gold_row,
)
from benchmarks.causal.reporting import build_report, render_summary
from benchmarks.causal.runner import (
    _collect_traversed_edges,
    _distractor_survival,
    _evaluate_case,
    run_case,
)

_HERE = Path(__file__).resolve().parent.parent / "benchmarks" / "causal"
_FIXTURES = _HERE / "fixtures"
_GOLD = _HERE / "gold"


class TestSchemaValidation(unittest.TestCase):
    def _valid_fixture(self) -> dict:
        return {
            "id": "c1",
            "gold_id": "c1",
            "query": "why did x happen",
            "bucket_labels": ["linear_chain"],
            "beads": [
                {"key": "root", "type": "decision", "title": "Root", "summary": ["r"]},
                {"key": "outcome", "type": "outcome", "title": "Outcome", "summary": ["o"]},
            ],
            "edges": [
                {"source_key": "root", "target_key": "outcome", "relationship": "causes"},
            ],
            "distractor_keys": [],
        }

    def test_valid_fixture_passes(self):
        ok, errs = validate_fixture_row(self._valid_fixture())
        self.assertTrue(ok, errs)

    def test_missing_query_fails(self):
        row = self._valid_fixture()
        del row["query"]
        ok, errs = validate_fixture_row(row)
        self.assertFalse(ok)
        self.assertIn("missing:query", errs)

    def test_edge_with_unknown_source_fails(self):
        row = self._valid_fixture()
        row["edges"] = [{"source_key": "ghost", "target_key": "root", "relationship": "causes"}]
        ok, errs = validate_fixture_row(row)
        self.assertFalse(ok)
        self.assertIn("edge_source_unknown:ghost", errs)

    def test_duplicate_bead_key_fails(self):
        row = self._valid_fixture()
        row["beads"].append({"key": "root", "type": "event", "title": "dup", "summary": ["d"]})
        ok, errs = validate_fixture_row(row)
        self.assertFalse(ok)
        self.assertIn("bead_key_duplicate", errs)

    def test_valid_gold_passes(self):
        ok, errs = validate_gold_row({
            "id": "c1",
            "gold_root_cause_key": "root",
            "bucket_labels": ["linear_chain"],
        })
        self.assertTrue(ok, errs)

    def test_gold_missing_root_cause_key_fails(self):
        ok, errs = validate_gold_row({"id": "c1", "gold_root_cause_key": "", "bucket_labels": ["x"]})
        self.assertFalse(ok)
        self.assertIn("gold_root_cause_key_empty", errs)

    def test_gold_invalid_grounding_fails(self):
        ok, errs = validate_gold_row({
            "id": "c1", "gold_root_cause_key": "root",
            "expected_grounding": "bogus", "bucket_labels": ["x"],
        })
        self.assertFalse(ok)
        self.assertIn("expected_grounding_invalid", errs)


class TestBuildCases(unittest.TestCase):
    def test_checked_in_fixtures_load(self):
        pairs = build_cases(fixtures_dir=_FIXTURES, gold_dir=_GOLD)
        self.assertGreaterEqual(len(pairs), 5)
        for case, gold in pairs:
            self.assertIsInstance(case, CausalCase)
            self.assertIsInstance(gold, CausalGold)
            self.assertTrue(case.query)
            self.assertTrue(gold.gold_root_cause_key)

    def test_every_case_has_matching_gold(self):
        pairs = build_cases(fixtures_dir=_FIXTURES, gold_dir=_GOLD)
        for case, gold in pairs:
            self.assertEqual(case.gold_id, gold.id)

    def test_adversarial_cases_have_distractors(self):
        pairs = build_cases(fixtures_dir=_FIXTURES, gold_dir=_GOLD)
        for case, _ in pairs:
            if "adversarial" in case.bucket_labels:
                self.assertTrue(case.distractor_keys, f"{case.id} must define distractors")


class TestCollectTraversedEdges(unittest.TestCase):
    def test_extracts_raw_src_dst_relation(self):
        rca = {
            "causal_paths": [
                {"edges": [
                    {"raw_src": "b1", "raw_dst": "b2", "relation": "causes"},
                    {"raw_src": "b2", "raw_dst": "b3", "relation": "causes"},
                ]},
            ]
        }
        edges = _collect_traversed_edges(rca)
        self.assertEqual(edges, {("b1", "b2", "causes"), ("b2", "b3", "causes")})

    def test_deduplicates_across_paths(self):
        rca = {
            "causal_paths": [
                {"edges": [{"raw_src": "b1", "raw_dst": "b2", "relation": "causes"}]},
                {"edges": [{"raw_src": "b1", "raw_dst": "b2", "relation": "causes"}]},
            ]
        }
        self.assertEqual(len(_collect_traversed_edges(rca)), 1)

    def test_empty_attribution_yields_no_edges(self):
        self.assertEqual(_collect_traversed_edges({}), set())


class TestDistractorSurvival(unittest.TestCase):
    def test_survives_when_gold_ranks_above_distractor(self):
        self.assertTrue(_distractor_survival(["gold", "distractor"], "gold", {"distractor"}))

    def test_fails_when_distractor_ranks_above_gold(self):
        self.assertFalse(_distractor_survival(["distractor", "gold"], "gold", {"distractor"}))

    def test_fails_when_gold_absent(self):
        self.assertFalse(_distractor_survival(["distractor"], "gold", {"distractor"}))

    def test_vacuously_survives_with_no_distractors(self):
        self.assertTrue(_distractor_survival(["anything"], "gold", set()))

    def test_survives_only_if_above_all_distractors(self):
        self.assertFalse(_distractor_survival(["d1", "gold", "d2"], "gold", {"d1", "d2"}))
        self.assertTrue(_distractor_survival(["gold", "d1", "d2"], "gold", {"d1", "d2"}))


class TestEvaluateCase(unittest.TestCase):
    def _case(self) -> CausalCase:
        return CausalCase(
            id="t", query="why", intent="causal", bucket_labels=("linear_chain",),
            gold_id="t",
            beads=(
                {"key": "root", "type": "decision", "title": "R", "summary": ["r"]},
                {"key": "mid", "type": "event", "title": "M", "summary": ["m"]},
                {"key": "outcome", "type": "outcome", "title": "O", "summary": ["o"]},
            ),
            edges=(
                {"source_key": "mid", "target_key": "outcome", "relationship": "causes"},
                {"source_key": "root", "target_key": "mid", "relationship": "causes"},
            ),
            distractor_keys=("distractor",),
            k=8,
        )

    def _gold(self) -> CausalGold:
        return CausalGold(id="t", gold_root_cause_key="root",
                          gold_chain_keys=("outcome", "mid", "root"),
                          expected_grounding="full", bucket_labels=("linear_chain",))

    def test_perfect_reconstruction_passes(self):
        key_to_id = {"root": "bR", "mid": "bM", "outcome": "bO", "distractor": "bD"}
        payload = {
            "root_cause_attribution": {
                "root_causes": [
                    {"bead_id": "bR", "influence": 1.0, "depth": 2},
                    {"bead_id": "bM", "influence": 0.5, "depth": 1},
                ],
                "causal_paths": [
                    {"depth": 2, "nodes": ["bO", "bM", "bR"], "edges": [
                        {"raw_src": "bM", "raw_dst": "bO", "relation": "causes"},
                        {"raw_src": "bR", "raw_dst": "bM", "relation": "causes"},
                    ]},
                ],
            },
            "evidence": [{"bead_id": "bD"}, {"bead_id": "bR"}],
        }
        m = _evaluate_case(case=self._case(), gold=self._gold(), payload=payload, key_to_id=key_to_id)
        self.assertEqual(1.0, m["edge_recall"])
        self.assertEqual(1.0, m["edge_precision"])
        self.assertTrue(m["root_cause_correct"])
        self.assertTrue(m["grounding_full"])
        self.assertTrue(m["distractor_survived"])
        self.assertTrue(m["pass"])

    def test_distractor_wins_in_evidence_fallback_fails(self):
        # When attribution is empty, ranking falls back to evidence order where
        # the distractor (semantically closest) ranks first → survival fails.
        key_to_id = {"root": "bR", "mid": "bM", "outcome": "bO", "distractor": "bD"}
        payload = {
            "root_cause_attribution": {},  # traversal produced nothing
            "evidence": [{"bead_id": "bD"}, {"bead_id": "bR"}],  # distractor first
        }
        m = _evaluate_case(case=self._case(), gold=self._gold(), payload=payload, key_to_id=key_to_id)
        self.assertFalse(m["distractor_survived"])
        self.assertEqual(0.0, m["edge_recall"])
        self.assertFalse(m["pass"])

    def test_partial_edge_recall(self):
        key_to_id = {"root": "bR", "mid": "bM", "outcome": "bO", "distractor": "bD"}
        payload = {
            "root_cause_attribution": {
                "root_causes": [{"bead_id": "bM", "influence": 1.0, "depth": 1}],
                "causal_paths": [
                    {"depth": 1, "nodes": ["bO", "bM"], "edges": [
                        {"raw_src": "bM", "raw_dst": "bO", "relation": "causes"},
                    ]},
                ],
            },
            "evidence": [{"bead_id": "bM"}],
        }
        m = _evaluate_case(case=self._case(), gold=self._gold(), payload=payload, key_to_id=key_to_id)
        self.assertEqual(0.5, m["edge_recall"])  # 1 of 2 gold edges
        self.assertFalse(m["root_cause_correct"])  # top is bM, not bR
        self.assertFalse(m["grounding_full"])


class TestReporting(unittest.TestCase):
    def _rows(self) -> list[dict]:
        return [
            {"case_id": "a", "bucket_labels": ["adversarial"], "pass": True,
             "edge_precision": 1.0, "edge_recall": 1.0, "edge_f1": 1.0,
             "attribution_depth": 2, "grounding_full": True, "root_cause_correct": True,
             "distractor_survived": True, "distractor_count": 1, "latency_ms": 100.0,
             "retrieval_ms": 50.0, "warnings": []},
            {"case_id": "b", "bucket_labels": ["control"], "pass": True,
             "edge_precision": 1.0, "edge_recall": 1.0, "edge_f1": 1.0,
             "attribution_depth": 1, "grounding_full": True, "root_cause_correct": True,
             "distractor_survived": True, "distractor_count": 0, "latency_ms": 80.0,
             "retrieval_ms": 40.0, "warnings": []},
        ]

    def test_survival_rate_excludes_control_cases(self):
        report = build_report(metadata={"runner": "causal"}, case_results=self._rows())
        ds = report["distractor_survival"]
        # Only the adversarial case counts toward the denominator.
        self.assertEqual(1, ds["adversarial_case_count"])
        self.assertEqual(1, ds["survived_count"])
        self.assertEqual(1.0, ds["survival_rate"])

    def test_causal_metrics_aggregate(self):
        report = build_report(metadata={"runner": "causal"}, case_results=self._rows())
        cm = report["causal_metrics"]
        self.assertEqual(1.0, cm["edge_recall_mean"])
        self.assertEqual(1.0, cm["root_cause_accuracy"])
        self.assertEqual(1.5, cm["attribution_depth_mean"])

    def test_render_summary_includes_headline(self):
        report = build_report(metadata={"runner": "causal"}, case_results=self._rows())
        text = render_summary(report)
        self.assertIn("DISTRACTOR SURVIVAL", text)
        self.assertIn("survival rate", text)


class TestEndToEndCase(unittest.TestCase):
    """Materializes a real history and runs recall — the integration guarantee."""

    def test_adversarial_case_distractor_does_not_win(self):
        pairs = build_cases(fixtures_dir=_FIXTURES, gold_dir=_GOLD)
        case, gold = next((c, g) for c, g in pairs if c.id == "adversarial_distractor")
        row = run_case(case=case, gold=gold)
        # Causal traversal must reconstruct the chain and beat the distractor.
        self.assertEqual(1.0, row["edge_recall"], row)
        self.assertTrue(row["root_cause_correct"], row)
        self.assertTrue(row["distractor_survived"], row)

    def test_control_case_reconstructs_chain(self):
        pairs = build_cases(fixtures_dir=_FIXTURES, gold_dir=_GOLD)
        case, gold = next((c, g) for c, g in pairs if c.id == "no_distractor_control")
        row = run_case(case=case, gold=gold)
        self.assertEqual(1.0, row["edge_recall"], row)
        self.assertTrue(row["root_cause_correct"], row)


if __name__ == "__main__":
    unittest.main()
