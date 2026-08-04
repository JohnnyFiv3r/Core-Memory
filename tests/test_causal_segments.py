from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from core_memory.graph.junctions import derive_junction_projection
from core_memory.graph.root_cause import (
    root_cause_trace,
    segment_between,
    segment_frontier_between,
)


def _bead(title: str, observed_at: str, *, source_id: str = "source-main") -> dict:
    return {
        "type": "state_assertion",
        "title": title,
        "summary": [title],
        "observed_at": observed_at,
        "status": "open",
        "retrieval_eligible": True,
        "source_id": source_id,
    }


def _association(source: str, target: str, *, rel: str = "caused_by", confidence: float = 0.9) -> dict:
    return {
        "id": f"{source}:{rel}:{target}",
        "source_bead_id": source,
        "target_bead_id": target,
        "relationship": rel,
        "confidence": confidence,
        "status": "active",
    }


def _write_graph(root: Path, beads: dict[str, dict], associations: list[dict]) -> dict:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": associations}),
        encoding="utf-8",
    )
    return derive_junction_projection(root, embeddings={}, include_beads=True)


class TestCausalSegments(unittest.TestCase):
    def test_upstream_search_returns_normalized_cause_to_effect_order(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            beads = {
                "outcome": _bead("Customer churn", "2026-03-03T00:00:00Z"),
                "driver": _bead("Invoice failures", "2026-03-02T00:00:00Z"),
                "cause": _bead("Billing migration", "2026-03-01T00:00:00Z"),
            }
            projection = _write_graph(
                root,
                beads,
                [_association("outcome", "driver"), _association("driver", "cause")],
            )

            segment = segment_between(
                root,
                "outcome",
                "cause",
                direction="upstream",
                projection=projection,
            )

        self.assertIsNotNone(segment)
        assert segment is not None
        self.assertEqual(["cause", "driver", "outcome"], segment["bead_ids"])
        self.assertEqual(
            [("cause", "driver"), ("driver", "outcome")],
            [(edge["from"], edge["to"]) for edge in segment["edges"]],
        )
        self.assertEqual("upstream", segment["direction"])

    def test_downstream_and_any_respect_legal_orientation(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            beads = {
                "outcome": _bead("Delayed launch", "2026-04-03T00:00:00Z"),
                "cause": _bead("Vendor outage", "2026-04-01T00:00:00Z"),
            }
            projection = _write_graph(root, beads, [_association("outcome", "cause")])

            downstream = segment_between(
                root,
                "cause",
                "outcome",
                direction="downstream",
                projection=projection,
            )
            any_direction = segment_between(
                root,
                "outcome",
                "cause",
                direction="any",
                projection=projection,
            )

        self.assertEqual(["cause", "outcome"], downstream["bead_ids"] if downstream else None)
        self.assertEqual("downstream", downstream["direction"] if downstream else None)
        self.assertEqual(["cause", "outcome"], any_direction["bead_ids"] if any_direction else None)
        self.assertEqual("upstream", any_direction["direction"] if any_direction else None)

    def test_no_observed_chain_returns_none(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            beads = {
                "left": _bead("Left", "2026-01-01T00:00:00Z"),
                "right": _bead("Right", "2026-01-02T00:00:00Z"),
            }
            projection = _write_graph(root, beads, [])

            segment = segment_between(root, "left", "right", projection=projection)

        self.assertIsNone(segment)

    def test_relation_family_and_source_scope_are_enforced_during_search(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            beads = {
                "outcome": _bead("Outcome", "2026-02-03T00:00:00Z", source_id="ledger"),
                "cause": _bead("Cause", "2026-02-01T00:00:00Z", source_id="ledger"),
            }
            projection = _write_graph(
                root,
                beads,
                [_association("outcome", "cause", rel="associated_with")],
            )

            excluded_family = segment_between(
                root,
                "outcome",
                "cause",
                relation_families=["causal"],
                projection=projection,
            )
            denied = segment_between(
                root,
                "outcome",
                "cause",
                denied_source_ids=["ledger"],
                projection=projection,
            )
            allowed = segment_between(
                root,
                "outcome",
                "cause",
                relation_families=["structural"],
                allowed_source_ids=["ledger"],
                projection=projection,
            )

        self.assertIsNone(excluded_family)
        self.assertIsNone(denied)
        self.assertIsNotNone(allowed)

    def test_cost_uses_additive_negative_log_confidence_per_edge(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            beads = {
                "outcome": _bead("Outcome", "2026-02-03T00:00:00Z"),
                "middle": _bead("Middle", "2026-02-02T00:00:00Z"),
                "cause": _bead("Cause", "2026-02-01T00:00:00Z"),
            }
            projection = _write_graph(
                root,
                beads,
                [
                    _association("outcome", "middle", confidence=0.5),
                    _association("middle", "cause", confidence=0.25),
                ],
            )

            segment = segment_between(root, "outcome", "cause", projection=projection)

        self.assertIsNotNone(segment)
        assert segment is not None
        penalties = [edge["cost_breakdown"]["confidence_penalty"] for edge in segment["edges"]]
        self.assertAlmostEqual(-math.log(0.25), penalties[0], places=6)
        self.assertAlmostEqual(-math.log(0.5), penalties[1], places=6)
        self.assertAlmostEqual(
            sum(float(edge["cost"]) for edge in segment["edges"]),
            float(segment["total_cost"]),
            places=6,
        )
        self.assertTrue(all(not edge["cost_breakdown"]["semantic_drag_enabled"] for edge in segment["edges"]))

    def test_frontier_preserves_source_temporal_alternatives_until_queue_exhaustion(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            beads = {
                "outcome": _bead("Outcome", "2026-05-04T00:00:00Z"),
                "route-a": _bead("Historical route", "2025-05-02T00:00:00Z", source_id="archive"),
                "route-b": _bead("Current route", "2026-05-02T00:00:00Z", source_id="operations"),
                "cause": _bead("Cause", "2026-05-01T00:00:00Z"),
            }
            projection = _write_graph(
                root,
                beads,
                [
                    _association("outcome", "route-a", confidence=0.92),
                    _association("route-a", "cause", confidence=0.92),
                    _association("outcome", "route-b", confidence=0.9),
                    _association("route-b", "cause", confidence=0.9),
                ],
            )

            frontier = segment_frontier_between(
                root,
                "outcome",
                "cause",
                max_expansions=100,
                projection=projection,
            )
            capped = segment_frontier_between(
                root,
                "outcome",
                "cause",
                max_expansions=1,
                projection=projection,
            )

        self.assertTrue(frontier["complete"])
        self.assertEqual("exhausted", frontier["termination_reason"])
        self.assertEqual(2, len(frontier["segments"]))
        self.assertEqual(
            {"archive", "operations"},
            {
                next(
                    source
                    for source in segment["dynamic_cost_signature"]["source_footprint"]
                    if source in {"archive", "operations"}
                )
                for segment in frontier["segments"]
            },
        )
        self.assertFalse(capped["complete"])
        self.assertEqual("expansion_cap", capped["termination_reason"])

    def test_frontier_reports_partition_and_result_truncation(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            shared_time = "2026-06-02T00:00:00Z"
            beads = {
                "outcome": _bead("Outcome", "2026-06-03T00:00:00Z"),
                "a": _bead("Route A", shared_time, source_id="source-a"),
                "b": _bead("Route B", shared_time, source_id="source-b"),
                "c": _bead("Route C", shared_time, source_id="source-a"),
                "d": _bead("Route D", shared_time, source_id="source-a"),
                "cause": _bead("Cause", "2026-06-01T00:00:00Z"),
            }
            projection = _write_graph(
                root,
                beads,
                [
                    *(
                        _association(left, right)
                        for left, right in (
                            ("outcome", "a"),
                            ("a", "cause"),
                            ("outcome", "b"),
                            ("b", "cause"),
                            ("outcome", "c"),
                            ("c", "cause"),
                            ("outcome", "d"),
                            ("d", "cause"),
                        )
                    )
                ],
            )

            partition_capped = segment_frontier_between(
                root,
                "outcome",
                "cause",
                max_partitions=1,
                max_results_per_partition=4,
                projection=projection,
            )
            result_capped = segment_frontier_between(
                root,
                "outcome",
                "cause",
                max_partitions=4,
                max_results_per_partition=2,
                projection=projection,
            )

        self.assertFalse(partition_capped["complete"])
        self.assertEqual("partition_cap", partition_capped["termination_reason"])
        self.assertEqual(2, partition_capped["partitions_seen"])
        self.assertFalse(result_capped["complete"])
        self.assertEqual("result_cap", result_capped["termination_reason"])
        self.assertEqual(3, len(result_capped["segments"]))

    def test_root_cause_algorithm_uses_shared_search_without_changing_path_shape(self):
        with tempfile.TemporaryDirectory(prefix="cm-segment-") as td:
            root = Path(td)
            beads = {
                "outcome": _bead("Outcome", "2026-03-03T00:00:00Z"),
                "middle": _bead("Middle", "2026-03-02T00:00:00Z"),
                "cause": _bead("Cause", "2026-03-01T00:00:00Z"),
            }
            _write_graph(
                root,
                beads,
                [_association("outcome", "middle"), _association("middle", "cause")],
            )

            result = root_cause_trace(root, ["outcome"], query="Why did the outcome happen?")

        self.assertEqual(
            [["outcome", "middle"], ["outcome", "middle", "cause"]],
            [path["nodes"] for path in result["causal_paths"]],
        )
        self.assertTrue(
            all(
                edge["cost_breakdown"]["semantic_drag_enabled"]
                for path in result["causal_paths"]
                for edge in path["edges"]
            )
        )


if __name__ == "__main__":
    unittest.main()
