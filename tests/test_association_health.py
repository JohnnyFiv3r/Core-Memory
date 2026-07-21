from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core_memory.association.health import association_health_report, association_pending_judge_health
from core_memory.persistence.store import MemoryStore


class TestAssociationHealthSlice5(unittest.TestCase):
    def test_health_report_counts_and_noise_ratio(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            c = s.add_bead(type="context", title="C", summary=["z"], session_id="s2", source_turn_ids=["t3"])
            s.link(source_id=a, target_id=b, relationship="follows", explanation="temporal")
            sid = s.link(source_id=a, target_id=c, relationship="supports", explanation="semantic")

            # mark one association inactive
            idx_file = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            for row in (idx.get("associations") or []):
                if str(row.get("id") or "") == str(sid):
                    row["status"] = "retracted"
            idx_file.write_text(json.dumps(idx, indent=2), encoding="utf-8")

            out = association_health_report(td)
            self.assertTrue(out.get("ok"))
            self.assertEqual(3, int(out.get("beads") or 0))
            self.assertEqual(2, int(out.get("associations_total") or 0))
            self.assertEqual(1, int(out.get("associations_active") or 0))
            self.assertIn("retracted", dict(out.get("status_distribution") or {}))
            self.assertEqual(1, int(out.get("structural_continuity_active") or 0))
            self.assertEqual(0, int(out.get("semantic_relationships_active") or 0))
            self.assertEqual(0, int(out.get("semantic_causal_active") or 0))

    def test_health_report_session_scope(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["y"], session_id="s2", source_turn_ids=["t2"])
            s.link(source_id=a, target_id=b, relationship="supports", explanation="cross")

            out = association_health_report(td, session_id="s1")
            self.assertTrue(out.get("ok"))
            self.assertEqual("s1", out.get("session_id"))
            self.assertEqual(1, int(out.get("beads") or 0))

    def test_active_noise_pct_excludes_inactive_noise_edges(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

            noise_id = s.link(source_id=a, target_id=b, relationship="shared_tag", explanation="noise")
            s.link(source_id=a, target_id=b, relationship="supports", explanation="semantic")

            idx_file = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            for row in (idx.get("associations") or []):
                if str(row.get("id") or "") == str(noise_id):
                    row["status"] = "retracted"
            idx_file.write_text(json.dumps(idx, indent=2), encoding="utf-8")

            out = association_health_report(td)
            self.assertTrue(out.get("ok"))
            self.assertEqual(1, int(out.get("associations_active") or 0))
            self.assertEqual(0.0, float(out.get("active_noise_pct") or 0.0))

    def test_health_separates_structural_and_semantic_causal_edges(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = store.add_bead(type="decision", title="Decision", summary=["ship"], session_id="s1")
            second = store.add_bead(type="outcome", title="Outcome", summary=["shipped"], session_id="s1")
            store.link(source_id=second, target_id=first, relationship="follows", explanation="sequence")
            store.link(source_id=first, target_id=second, relationship="led_to", explanation="causal")

            out = association_health_report(td)

        self.assertEqual(1, out["structural_continuity_active"])
        self.assertEqual(1, out["semantic_relationships_active"])
        self.assertEqual(1, out["semantic_causal_active"])
        self.assertEqual([("follows", 1)], out["structural_relationship_top_active"])
        self.assertEqual([("led_to", 1)], out["semantic_causal_top_active"])

    def test_pending_judge_health_uses_latest_run_state_and_age_thresholds(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = store.add_bead(
                type="context",
                title="Pending",
                summary=["pending"],
                session_id="s1",
                source_turn_ids=["t1"],
            )
            events_dir = Path(td) / ".beads" / "events"
            events_dir.mkdir(parents=True, exist_ok=True)
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            rows = [
                {
                    "run_id": "arun-resolved",
                    "status": "pending_judge",
                    "session_id": "s1",
                    "bead_ids": [bead],
                    "recorded_at": start.isoformat(),
                },
                {
                    "run_id": "arun-resolved",
                    "status": "completed",
                    "session_id": "s1",
                    "bead_ids": [bead],
                    "recorded_at": (start + timedelta(minutes=1)).isoformat(),
                },
                {
                    "run_id": "arun-pending",
                    "status": "pending_judge",
                    "session_id": "s1",
                    "bead_ids": [bead],
                    "recorded_at": start.isoformat(),
                },
            ]
            (events_dir / "association-runs.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            warning = association_pending_judge_health(td, now=start + timedelta(minutes=5, seconds=1))
            critical = association_pending_judge_health(td, now=start + timedelta(hours=1, seconds=1))

        self.assertEqual(1, warning["pending_judge_count"])
        self.assertEqual("warning", warning["severity"])
        self.assertEqual(301, warning["pending_judge_runs"][0]["age_seconds"])
        self.assertEqual(["t1"], warning["pending_judge_runs"][0]["turn_ids"])
        self.assertEqual("critical", critical["severity"])
        self.assertEqual(3601, critical["oldest_pending_judge_age_seconds"])

    def test_doctor_reports_critical_pending_association_judge_run(self):
        from core_memory.cli.handlers.setup import _association_judge_probe

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = store.add_bead(type="context", title="Pending", summary=["pending"], session_id="s1")
            events_dir = Path(td) / ".beads" / "events"
            events_dir.mkdir(parents=True, exist_ok=True)
            (events_dir / "association-runs.jsonl").write_text(
                json.dumps(
                    {
                        "run_id": "arun-old",
                        "status": "pending_judge",
                        "session_id": "s1",
                        "bead_ids": [bead],
                        "recorded_at": (datetime.now(timezone.utc) - timedelta(hours=1, seconds=5)).isoformat(),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch(
                "core_memory.runtime.associations.coverage.association_judge_readiness",
                return_value={"ready": False, "reason": "missing_chat_provider"},
            ):
                probe = _association_judge_probe(td)

        self.assertEqual("error", probe["status"])
        self.assertEqual("critical", probe["health"]["severity"])


if __name__ == "__main__":
    unittest.main()
