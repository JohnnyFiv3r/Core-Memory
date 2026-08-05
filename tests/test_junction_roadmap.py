from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.graph.roadmap import (
    build_junction_roadmap,
    retain_nondominated_alternatives,
    roadmap_input_revision,
    sample_junction_identities,
)
from core_memory.management import maintain
from core_memory.persistence.junction_roadmap import (
    JUNCTION_ROADMAP_SCHEMA,
    read_junction_roadmap,
)
from core_memory.retrieval.junctions import derive_junction_projection
from core_memory.runtime.queue.side_effect_queue import process_side_effect_event, side_effect_queue_status


def _claim(claim_id: str, subject: str, slot: str, value: str) -> dict:
    return {
        "id": claim_id,
        "claim_kind": "state",
        "subject": subject,
        "slot": slot,
        "value": value,
        "reason_text": "Observed in source evidence.",
        "confidence": 0.9,
    }


def _bead(title: str, observed_at: str, *, source_id: str, claim: dict) -> dict:
    return {
        "type": "state_assertion",
        "title": title,
        "summary": [title],
        "observed_at": observed_at,
        "status": "open",
        "retrieval_eligible": True,
        "source_id": source_id,
        "claims": [claim],
    }


def _association(effect: str, cause: str, *, source: str) -> dict:
    return {
        "id": f"{effect}:caused_by:{cause}",
        "source_bead_id": effect,
        "target_bead_id": cause,
        "relationship": "caused_by",
        "confidence": 0.9,
        "status": "active",
        "authority": "user_confirmed",
        "evidence_refs": [{"source_id": source, "ref": f"evidence:{effect}"}],
    }


def _write_roadmap_fixture(root: Path) -> None:
    def cause_claim(suffix: str) -> dict[str, object]:
        return _claim(f"cause-{suffix}", "Acme", "billing cause", suffix)

    def effect_claim(suffix: str) -> dict[str, object]:
        return _claim(f"effect-{suffix}", "Acme", "retention outcome", suffix)

    beads = {
        "cause-a": _bead(
            "Billing migration A",
            "2026-01-01T00:00:00Z",
            source_id="source-a",
            claim=cause_claim("a"),
        ),
        "effect-a": _bead(
            "Retention outcome A",
            "2026-01-02T00:00:00Z",
            source_id="source-a",
            claim=effect_claim("a"),
        ),
        "cause-b": _bead(
            "Billing migration B",
            "2025-01-01T00:00:00Z",
            source_id="source-b",
            claim=cause_claim("b"),
        ),
        "effect-b": _bead(
            "Retention outcome B",
            "2025-01-02T00:00:00Z",
            source_id="source-b",
            claim=effect_claim("b"),
        ),
    }
    associations = [
        _association("effect-a", "cause-a", source="source-a"),
        _association("effect-b", "cause-b", source="source-b"),
    ]
    events = root / ".beads" / "events"
    events.mkdir(parents=True, exist_ok=True)
    (root / ".beads" / "index.json").write_text(
        json.dumps({"beads": beads, "associations": associations}),
        encoding="utf-8",
    )
    (events / "myelination-manifest.json").write_text(
        json.dumps(
            {
                "schema": "core_memory.myelination_manifest.v2",
                "enabled": True,
                "bonus_by_edge_key": {"effect-a|caused_by|cause-a": 0.05},
            }
        ),
        encoding="utf-8",
    )


def _alternative(segment_id: str, partition: str, *, structural: float, evidence: int = 1) -> dict:
    return {
        "segment_id": segment_id,
        "edge_cost_rows": [
            {
                "cached_components": {
                    "structural": structural,
                    "confidence": 0.1,
                    "myelination": 0.0,
                    "evidence": -0.08,
                    "validation": 0.0,
                },
                "cached_floor_cost": max(0.001, structural + 0.02),
            }
        ],
        "dynamic_cost_signature": {
            "partition_key": partition,
            "evidence_refs": [f"evidence:{index}" for index in range(evidence)],
        },
    }


def _build_roadmap(root: Path, **options: object) -> dict:
    projection = derive_junction_projection(root, include_beads=True)
    return build_junction_roadmap(root, projection=projection, **options)


class TestJunctionRoadmap(unittest.TestCase):
    def test_input_revision_tracks_semantic_projection_changes(self):
        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            root = Path(td)
            _write_roadmap_fixture(root)
            semantic_manifest = root / ".beads" / "semantic" / "manifest.json"
            semantic_manifest.parent.mkdir(parents=True, exist_ok=True)
            semantic_manifest.write_text('{"epoch": 1}', encoding="utf-8")
            before = roadmap_input_revision(root)

            semantic_manifest.write_text('{"epoch": 2}', encoding="utf-8")
            after = roadmap_input_revision(root)

        self.assertNotEqual(before, after)

    def test_vertex_sampling_always_keeps_goal_vertices(self):
        projection = {
            "identities": [
                {
                    "id": "goal:launch",
                    "tier": "goal",
                    "label": "Launch beta",
                    "bead_ids": ["goal-bead"],
                    "support": 1,
                },
                {
                    "id": "claim:margin",
                    "tier": "claim_slot",
                    "label": "Acme margin",
                    "bead_ids": ["b1", "b2", "b3"],
                    "support": 3,
                },
            ]
        }

        sampled = sample_junction_identities(projection, max_vertices=1)

        self.assertEqual(["goal:launch"], [row["id"] for row in sampled])

    def test_builder_persists_partitioned_alternatives_and_complete_edge_ledgers(self):
        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            root = Path(td)
            _write_roadmap_fixture(root)

            result = _build_roadmap(root, max_vertices=10, radius=4)
            persisted = read_junction_roadmap(root)

        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["status"])
        self.assertEqual(JUNCTION_ROADMAP_SCHEMA, persisted["schema_version"])
        self.assertEqual(1, persisted["roadmap_meta"]["edge_count"])
        self.assertEqual(2, persisted["roadmap_meta"]["alternative_count"])
        alternatives = persisted["edges"][0]["alternatives"]
        self.assertEqual(2, len(alternatives))
        source_partitions = {
            tuple(alternative["dynamic_cost_signature"]["source_footprint"])
            for alternative in alternatives
        }
        self.assertTrue(any("source-a" in partition for partition in source_partitions))
        self.assertTrue(any("source-b" in partition for partition in source_partitions))
        rows = [row for alternative in alternatives for row in alternative["edge_cost_rows"]]
        self.assertTrue(all(set(row["cached_components"]) == {
            "structural",
            "confidence",
            "myelination",
            "evidence",
            "validation",
        } for row in rows))
        self.assertTrue(all(set(row["dynamic_refs"]) == {
            "source_ids",
            "claim_refs",
            "temporal_refs",
            "contradiction_refs",
            "evidence_refs",
        } for row in rows))
        source_a_row = next(row for row in rows if "source-a" in row["dynamic_refs"]["source_ids"])
        self.assertEqual(-0.05, source_a_row["cached_components"]["myelination"])
        self.assertEqual(-0.08, source_a_row["cached_components"]["evidence"])
        self.assertEqual(-0.08, source_a_row["cached_components"]["validation"])

    def test_incomplete_pair_frontier_is_recorded_but_never_cached(self):
        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            root = Path(td)
            _write_roadmap_fixture(root)
            with patch(
                "core_memory.graph.roadmap.causal_graph.segment_frontier_between",
                return_value={
                    "complete": False,
                    "termination_reason": "expansion_cap",
                    "expansions": 2,
                    "partitions_seen": 1,
                    "segments": [{"segment_id": "must-not-cache"}],
                },
            ):
                result = _build_roadmap(root, max_vertices=10, radius=4)

        self.assertTrue(result["ok"])
        self.assertEqual(0, result["roadmap_meta"]["edge_count"])
        self.assertEqual(1, result["roadmap_meta"]["omitted_pair_count"])
        self.assertEqual({"expansion_cap": 1}, result["roadmap_meta"]["omission_reasons"])

    def test_pareto_retention_preserves_partitions_and_enforces_hard_limit(self):
        retained = retain_nondominated_alternatives(
            [
                _alternative("a-best", "source-a", structural=0.1),
                _alternative("a-dominated", "source-a", structural=0.4),
                _alternative("b", "source-b", structural=0.8),
            ],
            soft_limit=1,
            hard_limit=2,
        )
        rejected = retain_nondominated_alternatives(
            [
                _alternative("a", "source-a", structural=0.1),
                _alternative("b", "source-b", structural=0.2),
            ],
            soft_limit=1,
            hard_limit=1,
        )

        self.assertTrue(retained["ok"])
        self.assertEqual({"a-best", "b"}, {row["segment_id"] for row in retained["alternatives"]})
        self.assertFalse(rejected["ok"])
        self.assertEqual("mandatory_partition_representatives_exceed_hard_limit", rejected["reason"])

    def test_read_status_can_omit_the_graph_payload(self):
        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            root = Path(td)
            _write_roadmap_fixture(root)
            _build_roadmap(root, max_vertices=10, radius=4)

            status = read_junction_roadmap(root, include_graph=False)

        self.assertTrue(status["present"])
        self.assertNotIn("vertices", status)
        self.assertNotIn("edges", status)
        self.assertEqual(2, status["roadmap_meta"]["alternative_count"])

    def test_status_reports_when_persisted_inputs_are_stale(self):
        from core_memory.retrieval.roadmap import junction_roadmap_status

        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            root = Path(td)
            _write_roadmap_fixture(root)
            _build_roadmap(root, max_vertices=10, radius=4)
            current = junction_roadmap_status(root)
            semantic_manifest = root / ".beads" / "semantic" / "manifest.json"
            semantic_manifest.parent.mkdir(parents=True, exist_ok=True)
            semantic_manifest.write_text('{"epoch": 2}', encoding="utf-8")

            stale = junction_roadmap_status(root)

        self.assertFalse(current["stale"])
        self.assertTrue(stale["stale"])
        self.assertIn("junction_roadmap_inputs_changed", stale["limitations"])

    def test_side_effect_processor_runs_the_persisted_builder(self):
        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            root = Path(td)
            with patch(
                "core_memory.retrieval.roadmap.refresh_junction_roadmap",
                return_value={
                    "ok": True,
                    "status": "ready",
                    "manifest_path": str(root / ".beads" / "events" / "junction-roadmap.json"),
                    "roadmap_meta": {"vertex_count": 4, "edge_count": 2},
                    "limitations": [],
                },
            ) as refresh:
                result = process_side_effect_event(
                    root=root,
                    kind="junction-roadmap-build",
                    payload={"max_vertices": 25, "trigger": "test"},
                )

        self.assertTrue(result["ok"])
        self.assertEqual("junction-roadmap-build", result["kind"])
        refresh.assert_called_once_with(root, max_vertices=25)

    def test_maintenance_facade_exposes_status_and_durable_refresh(self):
        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            status = maintain(root=td, action="junction_roadmap_status")
            queued = maintain(
                root=td,
                action="refresh_junction_roadmap",
                targets={"max_vertices": 25},
                authority={"actor": "operator", "allowed_authority": ["queue_ops"]},
                apply=True,
                dry_run=False,
                idempotency_key="roadmap-refresh-1",
            )

        self.assertTrue(status["ok"])
        self.assertFalse(status["present"])
        self.assertEqual("junction_roadmap_status", status["action"])
        self.assertTrue(queued["ok"])
        self.assertEqual("junction-roadmap-build", queued["kind"])
        self.assertEqual("junction-roadmap-build", queued["queue"]["kind"])

    def test_myelination_maintenance_enqueues_the_roadmap_cadence(self):
        with tempfile.TemporaryDirectory(prefix="cm-roadmap-") as td:
            root = Path(td)
            result = process_side_effect_event(root=root, kind="myelination-update", payload={})
            queue = side_effect_queue_status(root)

        self.assertTrue(result["ok"])
        self.assertTrue(result["junction_roadmap_queue"]["ok"])
        self.assertEqual(1, queue["by_kind"].get("junction-roadmap-build"))


class TestHttpJunctionRoadmap(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401

            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_projection_endpoint_defaults_to_metadata_only(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-roadmap-") as td:
            root = Path(td)
            _write_roadmap_fixture(root)
            _build_roadmap(root, max_vertices=10, radius=4)
            response = TestClient(app).get(
                "/v1/memory/projection/junction-roadmap",
                params={"root": str(root)},
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["present"])
        self.assertNotIn("vertices", payload)
        self.assertNotIn("edges", payload)
        self.assertEqual(2, payload["roadmap_meta"]["alternative_count"])


if __name__ == "__main__":
    unittest.main()
