from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core_memory
from core_memory.graph.root_cause import root_cause_trace
from core_memory.retrieval.roadmap_planner import plan_over_roadmap


def _bead(title: str, source_id: str, *, status: str = "open") -> dict:
    return {
        "type": "state_assertion",
        "title": title,
        "summary": [title],
        "status": status,
        "source_id": source_id,
        "retrieval_eligible": True,
    }


def _edge_row(
    edge_id: str,
    cause: str,
    effect: str,
    *,
    source_id: str,
    structural: float = 0.1,
) -> dict:
    return {
        "edge_id": edge_id,
        "from_bead_id": cause,
        "to_bead_id": effect,
        "raw_source_bead_id": effect,
        "raw_target_bead_id": cause,
        "relationship": "caused_by",
        "cached_components": {
            "structural": structural,
            "confidence": 0.0,
            "myelination": 0.0,
            "evidence": 0.0,
            "validation": 0.0,
        },
        "dynamic_refs": {
            "source_ids": [source_id],
            "claim_refs": [],
            "temporal_refs": [],
            "contradiction_refs": [],
            "evidence_refs": [f"evidence:{edge_id}"],
        },
    }


def _alternative(
    segment_id: str,
    bead_ids: list[str],
    *,
    source_id: str,
    structural: float = 0.1,
) -> dict:
    return {
        "segment_id": segment_id,
        "direction": "downstream",
        "bead_ids": bead_ids,
        "edge_cost_rows": [
            _edge_row(
                f"edge:{segment_id}:{index}",
                bead_ids[index],
                bead_ids[index + 1],
                source_id=source_id,
                structural=structural,
            )
            for index in range(len(bead_ids) - 1)
        ],
        "dynamic_cost_signature": {"source_footprint": [source_id]},
    }


def _roadmap(
    first_alternatives: list[dict] | None = None,
    second_alternatives: list[dict] | None = None,
) -> dict:
    return {
        "present": True,
        "status": "ready",
        "schema_version": "core_memory.junction_roadmap.v1",
        "vertices": [
            {"id": "claim:middle", "tier": "claim_slot", "bead_ids": ["middle-a", "middle-b"]},
            {"id": "claim:anchor", "tier": "claim_slot", "bead_ids": ["anchor", "anchor-peer"]},
            {"id": "claim:terminal", "tier": "claim_slot", "bead_ids": ["terminal"]},
        ],
        "edges": [
            {
                "start_junction_id": "claim:middle",
                "end_junction_id": "claim:anchor",
                "alternatives": first_alternatives
                or [_alternative("first", ["middle-a", "anchor"], source_id="source-a")],
            },
            {
                "start_junction_id": "claim:terminal",
                "end_junction_id": "claim:middle",
                "alternatives": second_alternatives
                or [_alternative("second", ["terminal", "middle-b"], source_id="source-a")],
            },
        ],
        "roadmap_meta": {"vertex_count": 3, "edge_count": 2, "alternative_count": 2},
    }


def _write_index(root: Path, *, associations: list[dict] | None = None) -> None:
    beads = {
        "anchor": _bead("Outcome", "source-a"),
        "anchor-peer": _bead("Peer outcome", "source-a"),
        "middle-a": _bead("First middle", "source-a"),
        "middle-b": _bead("Second middle", "source-a"),
        "old-middle": _bead("Superseded middle", "source-a", status="superseded"),
        "new-middle": _bead("Current middle", "source-a"),
        "terminal": _bead("Root cause", "source-a"),
        "goal": _bead("Protect margin", "source-a"),
    }
    events = root / ".beads" / "events"
    events.mkdir(parents=True, exist_ok=True)
    (root / ".beads" / "index.json").write_text(
        json.dumps({"beads": beads, "associations": associations or []}),
        encoding="utf-8",
    )


class TestRoadmapPlanner(unittest.TestCase):
    def test_planner_is_exported_from_the_package_root(self):
        self.assertIs(core_memory.plan_over_roadmap, plan_over_roadmap)

    def test_fallback_root_cause_scope_filters_before_ranking(self):
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            events = root / ".beads" / "events"
            events.mkdir(parents=True, exist_ok=True)
            (root / ".beads" / "index.json").write_text(
                json.dumps(
                    {
                        "beads": {
                            "outcome": _bead("Outcome", "source-a"),
                            "allowed-cause": _bead("Allowed cause", "source-a"),
                            "denied-cause": _bead("Denied cause", "source-denied"),
                        },
                        "associations": [
                            {
                                "source_bead_id": "outcome",
                                "target_bead_id": "allowed-cause",
                                "relationship": "caused_by",
                                "status": "active",
                            },
                            {
                                "source_bead_id": "outcome",
                                "target_bead_id": "denied-cause",
                                "relationship": "caused_by",
                                "status": "active",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out = root_cause_trace(
                root,
                ["outcome"],
                query="Why?",
                allowed_source_ids=["source-a"],
                denied_source_ids=["source-denied"],
            )

        serialized = json.dumps(out)
        self.assertIn("allowed-cause", serialized)
        self.assertNotIn("denied-cause", serialized)
        self.assertTrue(out["diagnostics"]["source_scope_applied"])

    def test_stitches_upstream_segments_and_marks_the_claim_seam(self):
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                roadmap=_roadmap(),
            )

        plan = result["plan"]
        self.assertFalse(plan["fallback_used"])
        self.assertTrue(plan["stitched"])
        self.assertEqual(["second", "first"], [row["segment_id"] for row in plan["segments"]])
        self.assertEqual(["terminal"], plan["terminal_bead_ids"])
        self.assertEqual(1, plan["seam_count"])
        self.assertEqual("claim:middle", plan["junctions"][0]["junction_id"])
        self.assertEqual("middle-a", plan["junctions"][0]["left_bead_id"])
        self.assertEqual("middle-b", plan["junctions"][0]["right_bead_id"])
        self.assertAlmostEqual(0.28, plan["total_cost"], places=6)

    def test_source_scope_removes_denied_alternative_before_ranking(self):
        denied = _alternative("second-denied", ["terminal", "middle-b"], source_id="source-denied", structural=0.001)
        allowed = _alternative("second-allowed", ["terminal", "middle-b"], source_id="source-a", structural=0.3)
        roadmap = _roadmap(second_alternatives=[denied, allowed])
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                allowed_source_ids=["source-a"],
                denied_source_ids=["source-denied"],
                roadmap=roadmap,
            )

        self.assertEqual("second-allowed", result["plan"]["segments"][0]["segment_id"])
        self.assertEqual(1, result["roadmap_meta"]["scope_excluded_alternative_count"])
        self.assertNotIn("source-denied", json.dumps(result))

    def test_transition_dependent_seam_cost_selects_the_global_path(self):
        first_cheap = _alternative("first-cheap", ["middle-a", "anchor"], source_id="source-a", structural=0.1)
        first_exact = _alternative("first-exact", ["middle-b", "anchor"], source_id="source-a", structural=0.13)
        second_cheap = _alternative("second-cheap", ["terminal", "middle-b"], source_id="source-a", structural=0.1)
        second_other = _alternative("second-other", ["terminal", "middle-a"], source_id="source-a", structural=0.2)
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                roadmap=_roadmap([first_cheap, first_exact], [second_cheap, second_other]),
            )

        self.assertEqual(
            ["second-cheap", "first-exact"],
            [row["segment_id"] for row in result["plan"]["segments"]],
        )
        self.assertEqual(0, result["plan"]["seam_count"])
        self.assertAlmostEqual(0.23, result["plan"]["total_cost"], places=6)

    def test_temporal_frame_selects_different_cached_alternatives(self):
        first = _alternative("first", ["middle-b", "anchor"], source_id="source-a", structural=0.1)
        historical = _alternative(
            "historical",
            ["terminal", "old-middle", "middle-b"],
            source_id="source-a",
            structural=0.001,
        )
        current = _alternative(
            "current",
            ["terminal", "new-middle", "middle-b"],
            source_id="source-a",
            structural=0.15,
        )
        roadmap = _roadmap([first], [historical, current])
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            historical_result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                temporal_frame="historical",
                roadmap=roadmap,
            )
            current_result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                temporal_frame="current_truth",
                roadmap=roadmap,
            )

        self.assertEqual("historical", historical_result["plan"]["segments"][0]["segment_id"])
        self.assertEqual("current", current_result["plan"]["segments"][0]["segment_id"])

    def test_per_edge_floor_is_applied_before_segment_sum(self):
        first = _alternative("first", ["middle-a", "anchor"], source_id="source-a", structural=2.0)
        second = _alternative("second", ["terminal", "middle-b"], source_id="source-a", structural=-1.0)
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                roadmap=_roadmap([first], [second]),
            )

        self.assertAlmostEqual(2.081, result["plan"]["total_cost"], places=6)
        costs = [
            row["cost"]
            for segment in result["plan"]["segments"]
            for row in segment["edge_cost_rows"]
        ]
        self.assertIn(0.001, costs)
        self.assertIn(2.0, costs)

    def test_complete_edge_ledger_reconciles_cached_and_dynamic_components(self):
        first = _alternative("first", ["middle-a", "anchor"], source_id="source-a")
        second = _alternative("second", ["terminal", "middle-b"], source_id="source-a")
        row = second["edge_cost_rows"][0]
        row["cached_components"] = {
            "structural": 0.1,
            "confidence": 0.2,
            "myelination": -0.05,
            "evidence": -0.08,
            "validation": -0.08,
        }
        row["dynamic_refs"]["contradiction_refs"] = ["conflict:terminal"]
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            index = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            index["beads"]["terminal"]["observed_at"] = "2026-02-02T00:00:00Z"
            index["beads"]["middle-b"]["observed_at"] = "2026-02-01T00:00:00Z"
            (root / ".beads" / "index.json").write_text(json.dumps(index), encoding="utf-8")
            result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                temporal_frame="historical",
                roadmap=_roadmap([first], [second]),
            )

        receipt = next(
            edge
            for segment in result["plan"]["segments"]
            for edge in segment["edge_cost_rows"]
            if edge["edge_id"] == "edge:second:0"
        )
        self.assertEqual(
            {"structural", "confidence", "myelination", "evidence", "validation"},
            set(receipt["cached_components"]),
        )
        self.assertEqual(
            {"temporal", "claim_state", "contradiction", "permission_evidence_gap"},
            set(receipt["dynamic_components"]),
        )
        self.assertEqual(0.18, receipt["dynamic_components"]["temporal"])
        self.assertEqual(0.45, receipt["dynamic_components"]["contradiction"])
        self.assertAlmostEqual(
            sum(receipt["cached_components"].values())
            + sum(receipt["dynamic_components"].values()),
            receipt["raw_cost"],
            places=6,
        )

    def test_virtual_source_charges_initial_claim_mismatch(self):
        roadmap = _roadmap(
            first_alternatives=[_alternative("first", ["middle-a", "anchor-peer"], source_id="source-a")]
        )
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                roadmap=roadmap,
            )

        self.assertAlmostEqual(0.36, result["plan"]["total_cost"], places=6)
        self.assertEqual(2, result["plan"]["seam_count"])
        initial = result["plan"]["junctions"][0]
        self.assertEqual("claim:anchor", initial["junction_id"])
        self.assertEqual("anchor", initial["left_bead_id"])
        self.assertEqual("anchor-peer", initial["right_bead_id"])

    def test_goal_conditioning_terminates_at_exact_advancing_evidence(self):
        association = {
            "source_bead_id": "terminal",
            "target_bead_id": "goal",
            "relationship": "advances_goal",
            "status": "active",
        }
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root, associations=[association])
            result = plan_over_roadmap(
                root,
                query="What advances the margin goal?",
                anchor_ids=["anchor"],
                goal_bead_ids=["goal"],
                direction="upstream",
                roadmap=_roadmap(),
            )

        goal = result["plan"]["goal_conditioning"]
        self.assertTrue(goal["satisfied"])
        self.assertEqual(["terminal"], goal["terminal_evidence_ids"])
        self.assertEqual(["terminal"], result["plan"]["terminal_bead_ids"])

    def test_missing_exact_goal_evidence_never_terminates_at_shared_junction(self):
        association = {
            "source_bead_id": "terminal",
            "target_bead_id": "goal",
            "relationship": "advances_goal",
            "status": "active",
        }
        no_exact_terminal = _alternative("second", ["middle-a", "middle-b"], source_id="source-a")
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root, associations=[association])
            with patch("core_memory.retrieval.roadmap_planner.segment_between", return_value=None):
                result = plan_over_roadmap(
                    root,
                    query="",
                    anchor_ids=["anchor"],
                    goal_bead_ids=["goal"],
                    direction="upstream",
                    roadmap=_roadmap(second_alternatives=[no_exact_terminal]),
                )

        self.assertTrue(result["plan"]["fallback_used"])
        self.assertFalse(result["plan"]["goal_conditioning"]["satisfied"])
        self.assertEqual("goal_unsatisfied", result["plan"]["goal_conditioning"]["status"])

    def test_unconditioned_request_uses_exact_root_cause_candidate_terminal(self):
        attribution = {
            "causal_paths": [{"terminal_cause_bead_id": "terminal"}],
            "root_causes": [{"bead_id": "terminal"}],
        }
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            with patch(
                "core_memory.retrieval.roadmap_planner.causal_graph.root_cause_trace",
                return_value=attribution,
            ):
                result = plan_over_roadmap(
                    root,
                    query="Why?",
                    anchor_ids=["anchor"],
                    direction="upstream",
                    roadmap=_roadmap(),
                )

        self.assertFalse(result["plan"]["fallback_used"])
        self.assertEqual("root_cause_candidates", result["plan"]["terminal_mode"])
        self.assertEqual(["terminal"], result["plan"]["terminal_bead_ids"])

    def test_sparse_roadmap_falls_back_instead_of_returning_an_empty_answer(self):
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            with patch(
                "core_memory.retrieval.roadmap_planner.segment_between",
                return_value={
                    "segment_id": "fallback-segment",
                    "bead_ids": ["terminal", "anchor"],
                    "total_cost": 0.4,
                    "confidence": 0.67,
                },
            ):
                result = plan_over_roadmap(
                    root,
                    query="",
                    anchor_ids=["anchor"],
                    destination_anchor_ids=["terminal"],
                    direction="upstream",
                    roadmap={"present": True, "status": "ready", "edges": [], "roadmap_meta": {}},
                )

        self.assertTrue(result["plan"]["fallback_used"])
        self.assertEqual("fallback-segment", result["plan"]["segments"][0]["segment_id"])

    def test_cycle_in_the_roadmap_terminates_with_a_plan(self):
        roadmap = _roadmap()
        roadmap["edges"].append(
            {
                "start_junction_id": "claim:anchor",
                "end_junction_id": "claim:middle",
                "alternatives": [
                    _alternative("cycle", ["anchor", "middle-a"], source_id="source-a")
                ],
            }
        )
        with tempfile.TemporaryDirectory(prefix="cm-plan-") as td:
            root = Path(td)
            _write_index(root)
            result = plan_over_roadmap(
                root,
                query="",
                anchor_ids=["anchor"],
                destination_anchor_ids=["terminal"],
                direction="upstream",
                roadmap=roadmap,
            )

        self.assertFalse(result["plan"]["fallback_used"])
        self.assertEqual(["terminal"], result["plan"]["terminal_bead_ids"])


class TestRoadmapPlanHttp(unittest.TestCase):
    def test_plan_endpoint_forwards_the_typed_contract(self):
        try:
            from fastapi.testclient import TestClient

            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")
        receipt = {"ok": True, "plan": {"schema_version": "core_memory.stitched_plan.v1"}}
        with patch("core_memory.retrieval.tools.memory.plan", return_value=receipt) as planner:
            response = TestClient(app).post(
                "/v1/memory/plan",
                json={
                    "query": "What connects A to B?",
                    "anchor_ids": ["a"],
                    "destination_anchor_ids": ["b"],
                    "direction": "downstream",
                    "temporal_frame": "current_truth",
                    "allowed_source_ids": ["source-a"],
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(receipt, response.json())
        self.assertEqual(["b"], planner.call_args.kwargs["request"]["destination_anchor_ids"])

    def test_plan_endpoint_rejects_goal_and_destination_together(self):
        try:
            from fastapi.testclient import TestClient

            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")
        response = TestClient(app).post(
            "/v1/memory/plan",
            json={
                "anchor_ids": ["a"],
                "destination_anchor_ids": ["b"],
                "goal_bead_ids": ["goal"],
            },
        )
        self.assertEqual(422, response.status_code)

    def test_plan_endpoint_requires_a_nonempty_anchor_list(self):
        try:
            from fastapi.testclient import TestClient

            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")
        response = TestClient(app).post("/v1/memory/plan", json={"anchor_ids": []})
        self.assertEqual(422, response.status_code)


if __name__ == "__main__":
    unittest.main()
