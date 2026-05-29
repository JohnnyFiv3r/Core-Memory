"""Tests for the contradiction review UX (#14 surface layer).

Covers:
- build_conflict_review produces a render-agnostic, speakable prompt
- resolution_to_claim_updates maps each choice to canonical claim-update rows
- resolve_current_state: a supersede/retract issued AFTER a conflict clears it;
  a simultaneous conflict marker stays live
- decide_dreamer_candidate(resolution=...) writes a real claim update and the
  conflict clears end-to-end
- decide_dreamer_candidate(resolution="defer") writes nothing and marks deferred
- recall._attach_conflict_reviews links candidate_id + review_prompt, suppresses deferred
"""
import os
import shutil
import tempfile
import unittest

from core_memory.persistence.store_claim_ops import (
    resolve_current_state,
    write_claims_to_bead,
    write_claim_updates_to_bead,
)


def _claim(cid, value, *, subject="db", slot="engine", chain_seq=None, created_at=None):
    row = {
        "id": cid,
        "claim_kind": "fact",
        "subject": subject,
        "slot": slot,
        "value": value,
        "reason_text": "stated",
        "confidence": 0.9,
    }
    if chain_seq is not None:
        row["chain_seq"] = chain_seq
    if created_at is not None:
        row["created_at"] = created_at
    return row


# ── Review prompt builder ──────────────────────────────────────────────────────

class TestBuildConflictReview(unittest.TestCase):
    def _build(self, **over):
        from core_memory.claim.conflict_review import build_conflict_review
        kwargs = dict(
            subject="db",
            slot="engine",
            claim_a={"id": "c1", "value": "PostgreSQL", "created_at": "2026-01-03T00:00:00Z"},
            claim_b={"id": "c2", "value": "DynamoDB", "created_at": "2026-06-28T00:00:00Z"},
            epistemic_conflict_score=0.82,
            conflict_since="2026-01-03T00:00:00Z",
            candidate_id="dc-abc",
        )
        kwargs.update(over)
        return build_conflict_review(**kwargs)

    def test_question_mentions_both_values(self):
        p = self._build()
        self.assertIn("PostgreSQL", p["question"])
        self.assertIn("DynamoDB", p["question"])

    def test_question_mentions_subject_and_slot(self):
        p = self._build()
        self.assertIn("db", p["question"])
        self.assertIn("engine", p["question"])

    def test_has_four_resolution_choices(self):
        p = self._build()
        choices = {r["choice"] for r in p["resolutions"]}
        self.assertEqual(choices, {"prefer_a", "prefer_b", "retract_both", "defer"})

    def test_prefer_choices_carry_claim_ids(self):
        p = self._build()
        by_choice = {r["choice"]: r for r in p["resolutions"]}
        self.assertEqual(by_choice["prefer_a"]["claim_id"], "c1")
        self.assertEqual(by_choice["prefer_b"]["claim_id"], "c2")

    def test_candidate_id_propagated(self):
        p = self._build()
        self.assertEqual(p["candidate_id"], "dc-abc")

    def test_agent_instructions_say_do_not_pick_side(self):
        p = self._build()
        self.assertIn("Do NOT pick a side", p["agent_instructions"])

    def test_age_days_computed(self):
        p = self._build()
        # conflict_since 2026-01-03; "today" per env is 2026-05-29 → well over 100 days
        self.assertIsInstance(p["age_days"], int)
        self.assertGreater(p["age_days"], 100)

    def test_missing_value_is_safe(self):
        p = self._build(claim_a={"id": "c1"}, claim_b={"id": "c2"})
        self.assertIn("no value recorded", p["question"])


# ── Resolution → claim updates ──────────────────────────────────────────────────

class TestResolutionToClaimUpdates(unittest.TestCase):
    def _map(self, resolution):
        from core_memory.claim.conflict_review import resolution_to_claim_updates
        return resolution_to_claim_updates(
            resolution=resolution,
            subject="db",
            slot="engine",
            claim_a_id="c1",
            claim_b_id="c2",
            trigger_bead_id="t1",
        )

    def test_prefer_a_supersedes_b_with_a(self):
        rows = self._map("prefer_a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "supersede")
        self.assertEqual(rows[0]["target_claim_id"], "c2")
        self.assertEqual(rows[0]["replacement_claim_id"], "c1")

    def test_prefer_b_supersedes_a_with_b(self):
        rows = self._map("prefer_b")
        self.assertEqual(rows[0]["target_claim_id"], "c1")
        self.assertEqual(rows[0]["replacement_claim_id"], "c2")

    def test_retract_both_emits_two_retracts(self):
        rows = self._map("retract_both")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["decision"] == "retract" for r in rows))
        self.assertEqual({r["target_claim_id"] for r in rows}, {"c1", "c2"})

    def test_defer_writes_nothing(self):
        self.assertEqual(self._map("defer"), [])

    def test_unknown_writes_nothing(self):
        self.assertEqual(self._map("whatever"), [])


# ── Resolver: temporal clearing ─────────────────────────────────────────────────

class TestConflictClearsAfterResolution(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _seed_conflict(self):
        # Two claims for db:engine, a conflict marker on c1.
        write_claims_to_bead(self.td, "bead1", [_claim("c1", "PostgreSQL", chain_seq=1)])
        write_claims_to_bead(self.td, "bead2", [_claim("c2", "DynamoDB", chain_seq=2)])
        write_claim_updates_to_bead(self.td, "bead2", [{
            "id": "u-conflict", "decision": "conflict", "target_claim_id": "c1",
            "subject": "db", "slot": "engine", "reason_text": "two live values",
            "trigger_bead_id": "bead2", "chain_seq": 3,
        }])

    def test_unresolved_conflict_is_live(self):
        self._seed_conflict()
        state = resolve_current_state(self.td, "db", "engine")
        self.assertEqual("conflict", state["status"])

    def test_supersede_after_conflict_clears_it(self):
        self._seed_conflict()
        # Resolution: prefer_b → supersede c1 with c2, issued AFTER the conflict.
        write_claim_updates_to_bead(self.td, "bead3", [{
            "id": "u-resolve", "decision": "supersede", "target_claim_id": "c1",
            "replacement_claim_id": "c2", "subject": "db", "slot": "engine",
            "reason_text": "user resolved", "trigger_bead_id": "bead3", "chain_seq": 4,
        }])
        state = resolve_current_state(self.td, "db", "engine")
        self.assertEqual("active", state["status"])
        self.assertEqual("c2", str((state["current_claim"] or {}).get("id")))

    def test_retract_both_after_conflict_clears_to_retracted(self):
        self._seed_conflict()
        write_claim_updates_to_bead(self.td, "bead3", [
            {"id": "r1", "decision": "retract", "target_claim_id": "c1", "subject": "db",
             "slot": "engine", "reason_text": "drop", "trigger_bead_id": "bead3", "chain_seq": 4},
            {"id": "r2", "decision": "retract", "target_claim_id": "c2", "subject": "db",
             "slot": "engine", "reason_text": "drop", "trigger_bead_id": "bead3", "chain_seq": 5},
        ])
        state = resolve_current_state(self.td, "db", "engine")
        self.assertEqual("retracted", state["status"])


# ── decide_dreamer_candidate resolution branch ──────────────────────────────────

class TestDecideContradictionResolution(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.td, ".beads", "events"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _seed_candidate(self):
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        from core_memory.retrieval.contracts import ConflictItem
        conflict = ConflictItem(
            subject="db", slot="engine", claim_a_id="c1", claim_b_id="c2",
            epistemic_conflict_score=0.85, conflict_since="2026-01-03T00:00:00Z", chain_seq_gap=3,
        )
        res = enqueue_contradiction_pressure_candidates(root=self.td, conflicts=[conflict], threshold=0.7)
        return res["candidate_ids"]["db:engine"]

    def test_defer_marks_deferred_no_write(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate, list_dreamer_candidates
        cid = self._seed_candidate()
        out = decide_dreamer_candidate(root=self.td, candidate_id=cid, decision="accept", resolution="defer")
        self.assertTrue(out["ok"])
        self.assertEqual(out["applied"]["application_mode"], "deferred_no_write")
        rows = list_dreamer_candidates(root=self.td)["results"]
        self.assertEqual(rows[0]["review_state"], "deferred")

    def test_invalid_resolution_rejected(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        cid = self._seed_candidate()
        out = decide_dreamer_candidate(root=self.td, candidate_id=cid, decision="accept",
                                       resolution="bogus", apply=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "invalid_resolution")

    def test_prefer_b_writes_claim_update_and_clears_conflict(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        # Seed a real conflict in the store first.
        write_claims_to_bead(self.td, "bead1", [_claim("c1", "PostgreSQL", chain_seq=1)])
        write_claims_to_bead(self.td, "bead2", [_claim("c2", "DynamoDB", chain_seq=2)])
        write_claim_updates_to_bead(self.td, "bead2", [{
            "id": "u-conflict", "decision": "conflict", "target_claim_id": "c1",
            "subject": "db", "slot": "engine", "reason_text": "two values",
            "trigger_bead_id": "bead2", "chain_seq": 3,
        }])
        self.assertEqual("conflict", resolve_current_state(self.td, "db", "engine")["status"])

        cid = self._seed_candidate()
        out = decide_dreamer_candidate(root=self.td, candidate_id=cid, decision="accept",
                                       resolution="prefer_b", apply=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["applied"]["application_mode"], "claim_update_resolution")

        state = resolve_current_state(self.td, "db", "engine")
        self.assertEqual("active", state["status"])
        self.assertEqual("c2", str((state["current_claim"] or {}).get("id")))


# ── recall attach (link + suppress-deferred) ────────────────────────────────────

class TestAttachConflictReviews(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.td, ".beads", "events"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _result_with_conflict(self, score=0.85):
        from core_memory.retrieval.contracts import ConflictItem, RecallResult
        r = RecallResult()
        r.conflicts = [ConflictItem(
            subject="db", slot="engine", claim_a_id="c1", claim_b_id="c2",
            epistemic_conflict_score=score, conflict_since="2026-01-03T00:00:00Z",
            chain_seq_gap=3,
            metadata={"claim_a": {"id": "c1", "value": "PostgreSQL", "created_at": "2026-01-03T00:00:00Z"},
                      "claim_b": {"id": "c2", "value": "DynamoDB", "created_at": "2026-06-28T00:00:00Z"}},
        )]
        return r

    def test_above_threshold_attaches_prompt_and_candidate_id(self):
        from core_memory.retrieval.agent import _attach_conflict_reviews
        r = self._result_with_conflict()
        _attach_conflict_reviews(r, self.td)
        c = r.conflicts[0]
        self.assertTrue(c.candidate_id)
        self.assertIsNotNone(c.review_prompt)
        self.assertIn("PostgreSQL", c.review_prompt["question"])

    def test_below_threshold_no_prompt(self):
        from core_memory.retrieval.agent import _attach_conflict_reviews
        r = self._result_with_conflict(score=0.4)
        _attach_conflict_reviews(r, self.td)
        self.assertIsNone(r.conflicts[0].review_prompt)
        self.assertEqual(r.conflicts[0].candidate_id, "")

    def test_deferred_conflict_not_re_prompted(self):
        from core_memory.retrieval.agent import _attach_conflict_reviews
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate

        r = self._result_with_conflict()
        _attach_conflict_reviews(r, self.td)
        cid = r.conflicts[0].candidate_id
        self.assertTrue(cid)
        # User defers.
        decide_dreamer_candidate(root=self.td, candidate_id=cid, decision="accept", resolution="defer")
        # Next recall on the same conflict: prompt suppressed.
        r2 = self._result_with_conflict()
        _attach_conflict_reviews(r2, self.td)
        self.assertIsNone(r2.conflicts[0].review_prompt)


if __name__ == "__main__":
    unittest.main()
