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

            self.assertEqual("partial", light["status"])
            self.assertEqual(180.0, light["temporal_horizon_days_p90"])
            self.assertEqual(1, light["spatial_scope_count"])
            self.assertGreater(light["light_cone_index"], 0.0)
            self.assertIsInstance(light["binding_mass"], float)
            self.assertIn("non_bead_assembly_depth_unavailable", light["limitations"])
            self.assertEqual("g-continuity", light["breakdown"][0]["goal_id"])

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
