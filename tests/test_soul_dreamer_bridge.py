import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.runtime.dreamer.candidates import _write_candidates
from core_memory.soul.dreamer_bridge import propose_soul_from_dreamer
from core_memory.soul.store import (
    approve_soul_update,
    read_soul_file,
    reject_soul_update,
    soul_history,
)


def _tension(cid, key, status="pending"):
    return {
        "id": cid,
        "status": status,
        "hypothesis_type": "tension_candidate",
        "tension_key": key,
        "statement": f"Goals conflict: {key}.",
        "supporting_bead_ids": ["b1", "b2"],
    }


def _goal(cid, theme, status="pending"):
    return {
        "id": cid,
        "status": status,
        "hypothesis_type": "goal_candidate",
        "goal_theme": theme,
        "statement": f"Latent goal: {theme}.",
        "supporting_bead_ids": ["b3", "b4", "b5"],
    }


def _decay(cid, bead_id, status="pending"):
    return {
        "id": cid,
        "status": status,
        "hypothesis_type": "goal_decay_warning",
        "goal_bead_id": bead_id,
        "statement": f"Goal {bead_id} appears dormant.",
        "supporting_bead_ids": [bead_id],
    }


def _value(cid, theme, status="pending"):
    return {
        "id": cid,
        "status": status,
        "hypothesis_type": "value_candidate",
        "value_theme": theme,
        "statement": f"Emergent value: {theme}.",
        "supporting_bead_ids": ["b1", "b2", "b3", "b4"],
    }


def _divergence(cid, entry_key, status="pending"):
    return {
        "id": cid,
        "status": status,
        "hypothesis_type": "identity_divergence_candidate",
        "identity_entry_key": entry_key,
        "statement": f"Endorsed identity '{entry_key}' has no supporting behavior.",
        "supporting_bead_ids": [],
    }


class TestSoulDreamerBridge(unittest.TestCase):
    def test_eligible_findings_become_proposed_revisions(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [
                _tension("dc-1", "ship-fast-vs-quality"),
                _goal("dc-2", "reduce-onboarding-friction"),
                _decay("dc-3", "bead-goal-9"),
            ])
            out = propose_soul_from_dreamer(td)
            self.assertTrue(out["ok"])
            self.assertEqual(3, out["proposed"])

            # All land as proposed (never auto-applied): projection stays empty.
            self.assertNotIn("conflict", read_soul_file(td, file_name="TENSIONS.md")["markdown"])
            revs = soul_history(td)["revisions"]
            self.assertEqual(3, len(revs))
            self.assertTrue(all(r["status"] == "proposed" for r in revs))
            self.assertTrue(all(r["source"] == "dreamer" for r in revs))
            self.assertTrue(all(r["epistemic_status"] == "inferred" for r in revs))

            files = {r["target_file"] for r in revs}
            self.assertEqual({"TENSIONS.md", "GOALS.md"}, files)
            keys = {r["entry_key"] for r in revs}
            self.assertEqual(
                {"tension:ship-fast-vs-quality", "goal:reduce-onboarding-friction", "decay:bead-goal-9"},
                keys,
            )
            # Evidence carried through.
            tension_rev = next(r for r in revs if r["entry_key"].startswith("tension:"))
            self.assertEqual(
                [{"bead_id": "b1", "relationship": "supports"}, {"bead_id": "b2", "relationship": "supports"}],
                tension_rev["evidence"],
            )

    def test_only_pending_candidates_are_bridged(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [
                _tension("dc-1", "k1", status="rejected"),
                _tension("dc-2", "k2", status="accepted"),
                _tension("dc-3", "k3", status="pending"),
            ])
            out = propose_soul_from_dreamer(td)
            self.assertEqual(1, out["proposed"])
            keys = {r["entry_key"] for r in soul_history(td)["revisions"]}
            self.assertEqual({"tension:k3"}, keys)

    def test_idempotent_across_runs(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [_tension("dc-1", "k1"), _goal("dc-2", "g1")])
            first = propose_soul_from_dreamer(td)
            self.assertEqual(2, first["proposed"])
            second = propose_soul_from_dreamer(td)
            self.assertEqual(0, second["proposed"])
            self.assertEqual(2, second["skipped"])
            self.assertEqual(2, len(soul_history(td)["revisions"]))

    def test_dedup_survives_rejected_proposal(self):
        # A rejected Dreamer proposal still counts as covered — the bridge must
        # not re-surface a finding the human already declined.
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [_tension("dc-1", "k1")])
            out = propose_soul_from_dreamer(td)
            rid = out["revision_ids"][0]
            reject_soul_update(td, revision_id=rid, reviewer="human", reason="not a real tension")
            again = propose_soul_from_dreamer(td)
            self.assertEqual(0, again["proposed"])

    def test_dedup_survives_approved_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [_goal("dc-1", "g1")])
            out = propose_soul_from_dreamer(td)
            approve_soul_update(td, revision_id=out["revision_ids"][0], approver="human")
            again = propose_soul_from_dreamer(td)
            self.assertEqual(0, again["proposed"])
            self.assertIn("Latent goal", read_soul_file(td, file_name="GOALS.md")["markdown"])

    def test_does_not_clobber_existing_authoritative_entry(self):
        # A human/agent already owns goal:g1 in GOALS.md. The bridge must not
        # propose a Dreamer duplicate for the same key — approving it would
        # overwrite the endorsed content with inferred Dreamer text.
        with tempfile.TemporaryDirectory() as td:
            from core_memory.soul.store import propose_soul_update
            propose_soul_update(
                td, target_file="GOALS.md", entry_key="goal:g1",
                content="Endorsed: reduce onboarding friction.",
                source="human", epistemic_status="endorsed", requires_approval=False,
            )
            _write_candidates(td, [_goal("dc-1", "g1")])
            out = propose_soul_from_dreamer(td)
            self.assertEqual(0, out["proposed"])
            self.assertEqual(1, out["skipped"])
            md = read_soul_file(td, file_name="GOALS.md")["markdown"]
            self.assertIn("Endorsed: reduce onboarding friction.", md)
            self.assertNotIn("Latent goal", md)

    def test_subject_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [_tension("dc-1", "k1")])
            propose_soul_from_dreamer(td, subject="acme")
            self.assertEqual(1, soul_history(td, subject="acme")["count"])
            self.assertEqual(0, soul_history(td, subject="self")["count"])

    def test_subject_tagged_candidate_does_not_leak_across_subjects(self):
        # A value candidate tagged subject="acme" must not bridge into self's
        # IDENTITY.md, even though it stays pending in the shared queue.
        with tempfile.TemporaryDirectory() as td:
            cand = _value("dc-1", "simplicity")
            cand["subject"] = "acme"
            _write_candidates(td, [cand])
            self_out = propose_soul_from_dreamer(td, subject="self")
            self.assertEqual(0, self_out["proposed"])
            self.assertEqual(0, soul_history(td, subject="self")["count"])
            # It bridges correctly for its own subject, and only once.
            acme_out = propose_soul_from_dreamer(td, subject="acme")
            self.assertEqual(1, acme_out["proposed"])
            self.assertEqual(1, soul_history(td, subject="acme")["count"])

    def test_unrelated_candidate_types_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [
                {"id": "dc-1", "status": "pending", "hypothesis_type": "entity_merge_candidate",
                 "source_entity_id": "e1", "target_entity_id": "e2"},
                {"id": "dc-2", "status": "pending", "hypothesis_type": "contradiction_candidate",
                 "source_bead_id": "b1", "target_bead_id": "b2"},
            ])
            out = propose_soul_from_dreamer(td)
            self.assertEqual(0, out["proposed"])
            self.assertEqual(0, soul_history(td)["count"])

    def test_identity_value_findings_route_to_identity_file(self):
        with tempfile.TemporaryDirectory() as td:
            _write_candidates(td, [
                _value("dc-1", "simplicity"),
                _divergence("dc-2", "Craftsmanship"),
            ])
            out = propose_soul_from_dreamer(td)
            self.assertEqual(2, out["proposed"])
            revs = soul_history(td)["revisions"]
            self.assertTrue(all(r["target_file"] == "IDENTITY.md" for r in revs))
            keys = {r["entry_key"] for r in revs}
            self.assertEqual({"value:simplicity", "divergence:Craftsmanship"}, keys)
            self.assertTrue(all(r["source"] == "dreamer" for r in revs))

    def test_empty_queue(self):
        with tempfile.TemporaryDirectory() as td:
            out = propose_soul_from_dreamer(td)
            self.assertTrue(out["ok"])
            self.assertEqual(0, out["proposed"])


class TestSoulAuthorityGating(unittest.TestCase):
    def test_confidence_gated_authority_tiers(self):
        import core_memory.soul.dreamer_bridge as bridge

        with tempfile.TemporaryDirectory() as td, patch.object(
            bridge, "_auto_mode_paused", return_value=False
        ):
            _write_candidates(
                td,
                [
                    {**_goal("dc-hi", "ship-fast"), "confidence": 0.95},     # >=0.90 -> auto_write
                    {**_goal("dc-mid", "scope-care"), "confidence": 0.85},   # 0.80-0.90 -> candidate_only
                    {**_goal("dc-lo", "vague-idea"), "confidence": 0.50},    # <0.80 -> not_surfaced
                    {**_divergence("dc-div", "Careful"), "confidence": 0.99},  # contradiction -> candidate_only
                ],
            )

            out = propose_soul_from_dreamer(td)

            self.assertFalse(out["auto_mode_paused"])
            self.assertEqual(1, out["auto_written"])      # only dc-hi
            self.assertEqual(2, out["candidate_only"])    # dc-mid + dc-div
            self.assertEqual(1, out["not_surfaced"])      # dc-lo dropped before LLM/store
            self.assertEqual(3, out["proposed"])

            by_key = {r["entry_key"]: r for r in (soul_history(td).get("revisions") or [])}
            self.assertEqual("applied", by_key["goal:ship-fast"]["status"])
            self.assertEqual("auto_write", by_key["goal:ship-fast"]["metadata"]["authority_tier"])
            self.assertEqual("proposed", by_key["goal:scope-care"]["status"])
            self.assertEqual("candidate_only", by_key["goal:scope-care"]["metadata"]["authority_tier"])
            # Contradiction-shaped finding is human-reviewed even at 0.99, and is
            # recorded as a pruning-flagged revision (PRD-D §4.3) that can never
            # auto-write.
            self.assertEqual("proposed", by_key["divergence:Careful"]["status"])
            self.assertTrue(by_key["divergence:Careful"]["metadata"]["contradiction_present"])
            self.assertTrue(by_key["divergence:Careful"]["metadata"]["pruning_flag"])
            self.assertEqual(
                "candidate_only", by_key["divergence:Careful"]["metadata"]["authority_tier"]
            )
            # not_surfaced never reaches the store.
            self.assertNotIn("goal:vague-idea", by_key)

    def test_auto_mode_paused_forces_candidate_only(self):
        import core_memory.soul.dreamer_bridge as bridge

        with tempfile.TemporaryDirectory() as td, patch.object(
            bridge, "_auto_mode_paused", return_value=True
        ):
            _write_candidates(td, [{**_goal("dc-hi", "ship-fast"), "confidence": 0.99}])

            out = propose_soul_from_dreamer(td)

            self.assertTrue(out["auto_mode_paused"])
            self.assertEqual(0, out["auto_written"])
            by_key = {r["entry_key"]: r for r in (soul_history(td).get("revisions") or [])}
            self.assertEqual("proposed", by_key["goal:ship-fast"]["status"])
            self.assertEqual("candidate_only", by_key["goal:ship-fast"]["metadata"]["authority_tier"])


if __name__ == "__main__":
    unittest.main()
