from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory import maintain
from core_memory.persistence.dreamer_candidate_store import read_candidates
from core_memory.runtime.dreamer.seam_healing import propose_seam_healing_candidates


def _stitched_plan() -> dict:
    return {
        "schema_version": "core_memory.stitched_plan.v1",
        "source_path_id": "path-fixture-1",
        "stitched": True,
        "segments": [
            {"segment_id": "second", "bead_ids": ["terminal", "middle-b"], "cost": 0.1},
            {"segment_id": "first", "bead_ids": ["middle-a", "anchor"], "cost": 0.1},
        ],
        "junctions": [
            {
                "junction_id": "claim:middle",
                "tier": "claim_slot",
                "is_seam": True,
                "junction_cost": 0.08,
                "left_bead_id": "middle-b",
                "right_bead_id": "middle-a",
            }
        ],
        "seam_count": 1,
        "fallback_used": False,
        "terminal_bead_ids": ["terminal"],
    }


def _write_empty_index(root: str) -> None:
    beads_dir = Path(root) / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps(
            {
                "beads": {
                    "middle-a": {"id": "middle-a", "title": "First middle"},
                    "middle-b": {"id": "middle-b", "title": "Second middle"},
                },
                "associations": [],
            }
        ),
        encoding="utf-8",
    )


class TestSeamHealingCandidates(unittest.TestCase):
    def test_validated_stitched_path_creates_review_candidate_without_graph_write(self):
        with tempfile.TemporaryDirectory(prefix="cm-seam-") as td:
            _write_empty_index(td)
            out = propose_seam_healing_candidates(
                root=td,
                plan=_stitched_plan(),
                validation={"validated": True, "granularity": "answer", "confidence": 1.0},
                reviewer="agent.review",
                notes="thumbs up on the answer",
            )

            self.assertTrue(out.get("ok"), out)
            self.assertEqual("candidates_submitted", out.get("status"))
            self.assertEqual("stitch_healed", out.get("origin"))
            self.assertEqual("path-fixture-1", out.get("source_path_id"))
            self.assertEqual(1, out.get("candidate_count"))
            self.assertEqual(0, out.get("deduped_count"))
            self.assertEqual("answer", out.get("validation_granularity"))
            self.assertLessEqual(float(out.get("candidate_prior") or 1.0), 0.35)
            self.assertFalse(out.get("direct_graph_writes"))

            rows = read_candidates(td)
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("pending", row.get("status"))
            self.assertEqual("seam_healing_candidate", row.get("hypothesis_type"))
            self.assertEqual("association", row.get("proposal_family"))
            self.assertEqual("stitch_healed", row.get("origin"))
            self.assertEqual("stitch_healed", row.get("relationship_signal"))
            self.assertEqual("associated_with", row.get("relationship"))
            self.assertEqual("middle-b", row.get("source_bead_id"))
            self.assertEqual("middle-a", row.get("target_bead_id"))
            review = row.get("review_payload") or {}
            self.assertEqual("seam_healing_association_candidate", review.get("kind"))
            self.assertEqual("stitch_healed", review.get("origin"))
            self.assertEqual("claim:middle", review.get("junction_id"))
            self.assertEqual("claim_slot", review.get("junction_tier"))
            self.assertEqual("path-fixture-1", review.get("source_path_id"))
            self.assertFalse(review.get("direct_graph_writes"))

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([], idx.get("associations") or [])

            replay = propose_seam_healing_candidates(
                root=td,
                plan=_stitched_plan(),
                validation={"validated": True, "granularity": "answer", "confidence": 1.0},
            )
            self.assertTrue(replay.get("ok"), replay)
            self.assertEqual(0, replay.get("candidate_count"))
            self.assertEqual(1, replay.get("deduped_count"))
            self.assertEqual(1, len(read_candidates(td)))

    def test_unvalidated_or_endpointless_plan_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="cm-seam-") as td:
            plan = _stitched_plan()
            plan["junctions"] = [dict(plan["junctions"][0], left_bead_id="")]
            out = propose_seam_healing_candidates(
                root=td,
                plan=plan,
                validation={"validated": False},
            )

            self.assertFalse(out.get("ok"), out)
            codes = {row.get("code") for row in out.get("validation_errors") or []}
            self.assertIn("seam_junctions_with_endpoints_required", codes)
            self.assertIn("validated_stitched_path_required", codes)
            self.assertEqual([], read_candidates(td))

    def test_maintain_surface_validates_authority_and_submits_candidates(self):
        with tempfile.TemporaryDirectory(prefix="cm-seam-") as td:
            preview = maintain(
                root=td,
                action="propose_seam_healing_candidates",
                proposal={"plan": _stitched_plan()},
                decision={"validation": {"validated": True, "granularity": "path"}},
                authority={"actor": "agent.review"},
                dry_run=True,
                apply=False,
            )
            self.assertTrue(preview.get("ok"), preview)
            self.assertEqual("preview", preview.get("status"))
            self.assertIn("submit_seam_healing_candidate", preview.get("required_authority") or [])
            self.assertFalse((Path(td) / ".beads" / "events" / "dreamer-candidates.json").exists())

            denied = maintain(
                root=td,
                action="seam_healing",
                proposal={"plan": _stitched_plan()},
                decision={"validation": {"validated": True, "granularity": "path"}},
                authority={"actor": "agent.review"},
                dry_run=False,
                apply=True,
            )
            self.assertFalse(denied.get("ok"), denied)
            self.assertEqual("authority_denied", denied.get("status"))

            applied = maintain(
                root=td,
                action="propose_seam_healing_candidates",
                proposal={"plan": _stitched_plan()},
                decision={"validation": {"validated": True, "granularity": "path"}},
                authority={
                    "actor": "agent.review",
                    "allowed_authority": ["submit_seam_healing_candidate"],
                },
                dry_run=False,
                apply=True,
            )
            self.assertTrue(applied.get("ok"), applied)
            self.assertEqual("candidates_submitted", applied.get("status"))
            self.assertEqual(1, applied.get("candidate_count"))
            self.assertTrue(applied.get("authority_ok"))
            self.assertEqual(1, len(read_candidates(td)))


if __name__ == "__main__":
    unittest.main()
