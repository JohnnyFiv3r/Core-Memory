from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _write_candidates
from core_memory.runtime.dreamer.identity_value_research import enqueue_identity_value_candidates
from core_memory.soul.goals import approve_goal, propose_goal
from core_memory.soul.store import propose_soul_update
from core_memory.soul.summary import SOUL_SUMMARY_SCHEMA, build_soul_summary


def _index_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "index.json"


def _update_bead(root: str | Path, bead_id: str, **fields) -> None:
    p = _index_path(root)
    idx = json.loads(p.read_text(encoding="utf-8"))
    idx["beads"][bead_id].update(fields)
    p.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _decision(store: MemoryStore, title: str, topics: list[str], session: str) -> str:
    return store.add_bead(
        type="decision",
        title=title,
        summary=["summary"],
        because=["reason"],
        detail="detail",
        topics=topics,
        session_id=session,
    )


class TestSoulSummary(unittest.TestCase):
    def test_empty_summary_is_readable_and_declares_measurements_not_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            out = build_soul_summary(td)

            self.assertTrue(out["ok"])
            self.assertEqual(SOUL_SUMMARY_SCHEMA, out["schema"])
            self.assertFalse(out["measurements_are_evidence"])
            self.assertEqual("partial", out["light_cone_breadth"]["status"])
            self.assertEqual("complete", out["observed_endorsed_divergence"]["status"])
            self.assertEqual("complete", out["persistent_tensions"]["status"])

    def test_light_cone_uses_endorsed_goal_horizon_scope_and_binding_mass(self):
        with tempfile.TemporaryDirectory() as td:
            p = propose_goal(td, title="Ship governed continuity", goal_id="g-continuity")
            self.assertTrue(approve_goal(td, goal_id="g-continuity")["ok"])
            _update_bead(
                td,
                p["bead_id"],
                target_horizon_days=180,
                subject_scope="organization",
            )

            out = build_soul_summary(td)
            light = out["light_cone_breadth"]

            self.assertEqual("complete", light["status"])
            self.assertEqual(180.0, light["temporal_horizon_days_p90"])
            self.assertEqual(1, light["spatial_scope_count"])
            self.assertGreater(light["light_cone_index"], 0.0)
            self.assertIsInstance(light["binding_mass"], float)
            self.assertNotIn("non_bead_assembly_depth_unavailable", light["limitations"])
            self.assertEqual("g-continuity", light["breakdown"][0]["goal_id"])

    def test_light_cone_reports_candidate_goals_without_inflating_horizon(self):
        with tempfile.TemporaryDirectory() as td:
            endorsed = propose_goal(td, title="Ship near-term continuity", goal_id="g-endorsed")
            candidate = propose_goal(td, title="Someday span the whole company", goal_id="g-candidate")
            self.assertTrue(approve_goal(td, goal_id="g-endorsed")["ok"])
            _update_bead(td, endorsed["bead_id"], target_horizon_days=90)
            _update_bead(td, candidate["bead_id"], target_horizon_days=1000)

            light = build_soul_summary(td)["light_cone_breadth"]
            rows = {row["goal_id"]: row for row in light["breakdown"] if row["kind"] == "goal"}

            self.assertEqual(90.0, light["temporal_horizon_days_p90"])
            self.assertTrue(rows["g-endorsed"]["contributes_to_primary_horizon"])
            self.assertFalse(rows["g-candidate"]["contributes_to_primary_horizon"])
            self.assertEqual(1000.0, rows["g-candidate"]["target_horizon_days"])

    def test_light_cone_uses_storyline_projection_as_non_bead_binding_mass(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            b1 = store.add_bead(
                type="decision",
                title="Choose architecture",
                summary=["s"],
                entities=["Acme"],
                session_id="s1",
                created_at="2026-01-01T00:00:00+00:00",
            )
            b2 = store.add_bead(
                type="outcome",
                title="Architecture stabilized",
                summary=["s"],
                entities=["Acme"],
                session_id="s2",
                created_at="2026-03-01T00:00:00+00:00",
            )
            store.link(b1, b2, "supports")

            light = build_soul_summary(td)["light_cone_breadth"]
            storyline_rows = [row for row in light["breakdown"] if row["kind"] == "storyline"]

            self.assertGreater(light["binding_mass"], 0.0)
            self.assertGreater(light["storyline_span_days_p90"], 0.0)
            self.assertGreaterEqual(len(storyline_rows), 1)
            self.assertGreater(storyline_rows[0]["binding_mass_component"], 0.0)
            self.assertNotIn("non_bead_assembly_depth_unavailable", light["limitations"])

    def test_observed_endorsed_divergence_summarizes_value_and_identity_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            for i, session in enumerate(["s1", "s2", "s3", "s4"]):
                _decision(store, f"Simplified path {i}", ["simplicity"], session)
            propose_soul_update(
                td,
                target_file="IDENTITY.md",
                entry_key="Craftsmanship",
                content="I value careful craftsmanship.",
                source="agent",
                epistemic_status="endorsed",
                requires_approval=False,
            )

            enqueue_identity_value_candidates(td)
            div = build_soul_summary(td)["observed_endorsed_divergence"]

            self.assertEqual("complete", div["status"])
            self.assertGreater(div["divergence_index"], 0.0)
            self.assertEqual(
                ["simplicity"],
                [row["value_theme"] for row in div["positive_observed_not_endorsed"]],
            )
            self.assertEqual(
                ["Craftsmanship"],
                [row["identity_entry_key"] for row in div["negative_endorsed_not_observed"]],
            )

    def test_divergence_projects_live_findings_without_enqueuing_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            for i, session in enumerate(["s1", "s2", "s3", "s4"]):
                _decision(store, f"Reliable path {i}", ["reliability"], session)
            propose_soul_update(
                td,
                target_file="IDENTITY.md",
                entry_key="Craftsmanship",
                content="I value careful craftsmanship.",
                source="agent",
                epistemic_status="endorsed",
                requires_approval=False,
            )

            div = build_soul_summary(td)["observed_endorsed_divergence"]

            self.assertEqual(
                ["reliability"],
                [row["value_theme"] for row in div["positive_observed_not_endorsed"]],
            )
            self.assertEqual("deterministic_projection", div["positive_observed_not_endorsed"][0]["source"])
            self.assertEqual(4, div["positive_observed_not_endorsed"][0]["session_count"])
            self.assertEqual(
                ["Craftsmanship"],
                [row["identity_entry_key"] for row in div["negative_endorsed_not_observed"]],
            )
            self.assertEqual("deterministic_projection", div["negative_endorsed_not_observed"][0]["source"])
            self.assertFalse((Path(td) / ".beads" / "events" / "dreamer-candidates.json").exists())

    def test_divergence_does_not_flag_supported_endorsed_identity(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _decision(store, "Choose simplicity", ["simplicity"], "s1")
            propose_soul_update(
                td,
                target_file="IDENTITY.md",
                entry_key="Simplicity",
                content="I value simplicity in product decisions.",
                source="agent",
                epistemic_status="endorsed",
                requires_approval=False,
            )

            div = build_soul_summary(td)["observed_endorsed_divergence"]

            self.assertEqual([], div["negative_endorsed_not_observed"])
            self.assertEqual([], div["positive_observed_not_endorsed"])

    def test_persistent_tensions_include_multi_session_goal_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g1 = store.add_bead(
                type="goal",
                title="Move fast",
                summary=["s"],
                goal_id="g1",
                session_id="s1",
            )
            g2 = store.add_bead(
                type="goal",
                title="Avoid irreversible mistakes",
                summary=["s"],
                goal_id="g2",
                session_id="s2",
            )
            store.link(g1, g2, "contradicts")

            tensions = build_soul_summary(td)["persistent_tensions"]

            self.assertGreaterEqual(tensions["persistence_qualified_count"], 1)
            self.assertGreater(tensions["active_load"], 0.0)
            rows = [
                row
                for row in tensions["tensions"]
                if row["source"] == "goal_conflict_detection"
            ]
            self.assertEqual(1, len(rows))
            self.assertTrue(rows[0]["persistence_qualified"])
            self.assertEqual(sorted(["s1", "s2"]), rows[0]["sessions"])

    def test_same_session_goal_conflicts_do_not_qualify_as_persistent(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g1 = store.add_bead(
                type="goal",
                title="Move fast",
                summary=["s"],
                goal_id="g1",
                session_id="s1",
            )
            g2 = store.add_bead(
                type="goal",
                title="Avoid irreversible mistakes",
                summary=["s"],
                goal_id="g2",
                session_id="s1",
            )
            store.link(g1, g2, "contradicts")

            tensions = build_soul_summary(td)["persistent_tensions"]

            rows = [
                row
                for row in tensions["tensions"]
                if row["source"] == "goal_conflict_detection"
            ]
            self.assertEqual(1, len(rows))
            self.assertFalse(rows[0]["persistence_qualified"])
            self.assertEqual(["s1"], rows[0]["sessions"])
            self.assertEqual(1, rows[0]["periods_spanned"])
            self.assertEqual(0, tensions["persistence_qualified_count"])

    def test_soul_tension_rows_expose_timing_source_refs_and_churn_rates(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(
                td,
                target_file="TENSIONS.md",
                entry_key="Speed versus care",
                content="Speed creates pressure against careful review.",
                source="dreamer",
                epistemic_status="inferred",
                requires_approval=False,
                evidence=[{"type": "document", "id": "doc-a"}],
                metadata={"first_seen_at": "2026-01-01T00:00:00+00:00"},
            )
            propose_soul_update(
                td,
                target_file="TENSIONS.md",
                entry_key="Resolved: unclear ownership",
                content="resolved after ownership was clarified.",
                source="dreamer",
                epistemic_status="inferred",
                requires_approval=False,
                evidence=[{"type": "source_object", "id": "obj-b"}],
                metadata={
                    "first_seen_at": "2026-01-05T00:00:00+00:00",
                    "resolved_at": "2026-01-10T00:00:00+00:00",
                },
            )

            tensions = build_soul_summary(td)["persistent_tensions"]
            rows = {row["title"]: row for row in tensions["tensions"]}

            active = rows["Speed versus care"]
            self.assertEqual("active", active["status"])
            self.assertIn("document:doc-a", active["source_refs"])
            self.assertEqual("2026-01-01T00:00:00+00:00", active["first_seen_at"])
            self.assertGreaterEqual(active["age_days"], 0.0)

            resolved = rows["Resolved: unclear ownership"]
            self.assertEqual("resolved", resolved["status"])
            self.assertEqual("2026-01-10T00:00:00+00:00", resolved["resolved_at"])
            self.assertIn("source_object:obj-b", resolved["source_refs"])

            self.assertIsNotNone(tensions["new_tension_rate"])
            self.assertIsNotNone(tensions["resolution_rate"])
            self.assertIsNotNone(tensions["churn"])
            self.assertNotIn("tension_churn_history_unavailable", tensions["limitations"])

    def test_dreamer_candidate_reader_does_not_create_event_directory(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(root=td).add_bead(
                type="note",
                title="seed",
                summary=["seed"],
                session_id="s1",
            )
            events = Path(td) / ".beads" / "events"
            if events.exists():
                shutil.rmtree(events)
            self.assertFalse(events.exists())

            build_soul_summary(td)

            self.assertFalse(events.exists())

    def test_http_summary_endpoint(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            c = TestClient(app)
            r = c.get("/v1/soul/summary", params={"root": td})
            self.assertEqual(200, r.status_code)
            self.assertEqual(SOUL_SUMMARY_SCHEMA, r.json()["schema"])

    def test_summary_reads_existing_candidate_file_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(
                td,
                [
                    {
                        "id": "dc-t",
                        "status": "pending",
                        "hypothesis_type": "tension_candidate",
                        "tension_key": "tension:manual",
                        "statement": "Manual tension candidate.",
                        "supporting_bead_ids": [],
                    }
                ],
            )
            before = (Path(td) / ".beads" / "events" / "dreamer-candidates.json").read_text(
                encoding="utf-8"
            )

            out = build_soul_summary(td)

            after = (Path(td) / ".beads" / "events" / "dreamer-candidates.json").read_text(
                encoding="utf-8"
            )
            self.assertEqual(before, after)
            self.assertEqual(1, len(out["persistent_tensions"]["tensions"]))


if __name__ == "__main__":
    unittest.main()
