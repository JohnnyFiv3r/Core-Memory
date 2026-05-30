"""Tests for #14A — both_valid resolution + context_scope claim discriminator."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_index(td: str, beads: dict | None = None, associations: list | None = None) -> None:
    p = Path(td) / ".beads" / "index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "beads": beads or {},
        "associations": associations or [],
    }), encoding="utf-8")


def _seed_claims_in_bead(td: str, bead_id: str, claims: list[dict], updates: list[dict] | None = None) -> None:
    idx_path = Path(td) / ".beads" / "index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    if idx_path.exists():
        idx = json.loads(idx_path.read_text())
    else:
        idx = {"beads": {}, "associations": []}
    if bead_id not in idx["beads"]:
        idx["beads"][bead_id] = {
            "id": bead_id,
            "session_id": "test-session",
            "claims": [],
            "claim_updates": [],
        }
    idx["beads"][bead_id]["claims"].extend(claims)
    idx["beads"][bead_id]["claim_updates"].extend(updates or [])
    idx_path.write_text(json.dumps(idx), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. RESOLUTION_BOTH_VALID constant
# ---------------------------------------------------------------------------

class TestBothValidConstant(unittest.TestCase):
    def test_constant_value(self):
        from core_memory.claim.conflict_review import RESOLUTION_BOTH_VALID
        self.assertEqual("both_valid", RESOLUTION_BOTH_VALID)

    def test_in_resolution_choices(self):
        from core_memory.claim.conflict_review import RESOLUTION_CHOICES, RESOLUTION_BOTH_VALID
        self.assertIn(RESOLUTION_BOTH_VALID, RESOLUTION_CHOICES)

    def test_in_all_exports(self):
        from core_memory.claim.conflict_review import __all__
        self.assertIn("RESOLUTION_BOTH_VALID", __all__)


# ---------------------------------------------------------------------------
# 2. build_conflict_review — both_valid in resolutions
# ---------------------------------------------------------------------------

class TestBuildConflictReviewBothValid(unittest.TestCase):
    def _build(self):
        from core_memory.claim.conflict_review import build_conflict_review
        return build_conflict_review(
            subject="alice",
            slot="database_backend",
            claim_a={"id": "ca1", "value": "AWS", "created_at": "2024-01-01T00:00:00Z"},
            claim_b={"id": "cb1", "value": "SQLite", "created_at": "2024-06-01T00:00:00Z"},
            epistemic_conflict_score=0.5,
        )

    def test_both_valid_present_in_resolutions(self):
        review = self._build()
        choices = {r["choice"] for r in review["resolutions"]}
        self.assertIn("both_valid", choices)

    def test_both_valid_effect_mentions_scope(self):
        review = self._build()
        bv = next(r for r in review["resolutions"] if r["choice"] == "both_valid")
        self.assertIn("scope", bv["effect"].lower())

    def test_agent_instructions_mention_both_valid(self):
        review = self._build()
        self.assertIn("both_valid", review["agent_instructions"])

    def test_agent_instructions_mention_context_a_context_b(self):
        review = self._build()
        instr = review["agent_instructions"]
        self.assertIn("context_a", instr)
        self.assertIn("context_b", instr)

    def test_five_resolutions_total(self):
        review = self._build()
        self.assertEqual(5, len(review["resolutions"]))


# ---------------------------------------------------------------------------
# 3. Epistemic scorer — cross-context pairs score 0.0
# ---------------------------------------------------------------------------

class TestEpistemicScorerContextScope(unittest.TestCase):
    def test_different_scopes_return_zero(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"context_scope": "prod", "created_at": "2024-01-01T00:00:00Z", "chain_seq": 1}
        b = {"context_scope": "staging", "created_at": "2024-06-01T00:00:00Z", "chain_seq": 10}
        self.assertEqual(0.0, conflict_score_for_pair(a, b))

    def test_same_scope_scores_normally(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"context_scope": "prod", "created_at": "2024-01-01T00:00:00Z", "chain_seq": 1}
        b = {"context_scope": "prod", "created_at": "2025-01-01T00:00:00Z", "chain_seq": 10}
        score = conflict_score_for_pair(a, b)
        self.assertGreater(score, 0.0)

    def test_empty_vs_nonempty_scope_returns_zero(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"context_scope": "", "created_at": "2024-01-01T00:00:00Z"}
        b = {"context_scope": "prod", "created_at": "2024-01-01T00:00:00Z"}
        self.assertEqual(0.0, conflict_score_for_pair(a, b))

    def test_none_vs_nonempty_scope_returns_zero(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"context_scope": None, "created_at": "2024-01-01T00:00:00Z"}
        b = {"context_scope": "prod", "created_at": "2024-01-01T00:00:00Z"}
        self.assertEqual(0.0, conflict_score_for_pair(a, b))

    def test_both_none_scores_normally(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"context_scope": None, "created_at": "2024-01-01T00:00:00Z", "chain_seq": 1}
        b = {"context_scope": None, "created_at": "2025-01-01T00:00:00Z", "chain_seq": 10}
        score = conflict_score_for_pair(a, b)
        self.assertGreater(score, 0.0)


# ---------------------------------------------------------------------------
# 4. resolve_all_current_state — context_scope coexistence
# ---------------------------------------------------------------------------

class TestResolverContextScopeCoexistence(unittest.TestCase):
    def test_different_scopes_no_conflict(self):
        from core_memory.claim.resolver import resolve_all_current_state
        with tempfile.TemporaryDirectory() as td:
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "db", "value": "AWS",
                 "context_scope": "prod", "created_at": "2024-01-01T00:00:00Z"},
                {"id": "cb1", "subject": "alice", "slot": "db", "value": "SQLite",
                 "context_scope": "staging", "created_at": "2024-01-01T00:00:00Z"},
            ])
            result = resolve_all_current_state(td)
        slots = result.get("slots") or {}
        # Scoped claims get separate buckets — no conflicts anywhere.
        for state in slots.values():
            self.assertNotEqual("conflict", state.get("status"), f"Unexpected conflict: {state}")

    def test_same_scope_still_conflicts(self):
        """Two claims with identical context_scope + explicit conflict markers stay in conflict."""
        from core_memory.claim.resolver import resolve_all_current_state
        with tempfile.TemporaryDirectory() as td:
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "db", "value": "AWS",
                 "context_scope": "prod", "created_at": "2024-01-01T00:00:00Z"},
                {"id": "cb1", "subject": "alice", "slot": "db", "value": "MySQL",
                 "context_scope": "prod", "created_at": "2024-02-01T00:00:00Z"},
            ], updates=[
                {"id": "u1", "decision": "conflict", "target_claim_id": "ca1",
                 "subject": "alice", "slot": "db", "trigger_bead_id": "b1"},
                {"id": "u2", "decision": "conflict", "target_claim_id": "cb1",
                 "subject": "alice", "slot": "db", "trigger_bead_id": "b1"},
            ])
            result = resolve_all_current_state(td)
        slots = result.get("slots") or {}
        # Same scope → same bucket → conflict detected
        has_conflict = any(s.get("status") == "conflict" for s in slots.values())
        self.assertTrue(has_conflict)

    def test_global_claims_backward_compat(self):
        """Global claims (no context_scope) with conflict markers still show as conflict."""
        from core_memory.claim.resolver import resolve_all_current_state
        with tempfile.TemporaryDirectory() as td:
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "db", "value": "AWS",
                 "created_at": "2024-01-01T00:00:00Z"},
                {"id": "cb1", "subject": "alice", "slot": "db", "value": "MySQL",
                 "created_at": "2024-02-01T00:00:00Z"},
            ], updates=[
                {"id": "u1", "decision": "conflict", "target_claim_id": "ca1",
                 "subject": "alice", "slot": "db", "trigger_bead_id": "b1"},
                {"id": "u2", "decision": "conflict", "target_claim_id": "cb1",
                 "subject": "alice", "slot": "db", "trigger_bead_id": "b1"},
            ])
            result = resolve_all_current_state(td)
        slots = result.get("slots") or {}
        has_conflict = any(s.get("status") == "conflict" for s in slots.values())
        self.assertTrue(has_conflict)

    def test_scoped_bucket_key_format(self):
        from core_memory.claim.resolver import resolve_all_current_state
        with tempfile.TemporaryDirectory() as td:
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "db", "value": "AWS",
                 "context_scope": "prod", "created_at": "2024-01-01T00:00:00Z"},
            ])
            result = resolve_all_current_state(td)
        slots = result.get("slots") or {}
        # Scoped claim should produce a key containing "::"
        scoped_keys = [k for k in slots if "::" in k]
        self.assertEqual(1, len(scoped_keys))
        self.assertIn("alice:db::prod", scoped_keys[0])

    def test_complement_scope_empty_string_coexists(self):
        from core_memory.claim.resolver import resolve_all_current_state
        with tempfile.TemporaryDirectory() as td:
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "db", "value": "AWS",
                 "context_scope": "prod", "created_at": "2024-01-01T00:00:00Z"},
                # empty-string scope = complement default (global)
                {"id": "cb1", "subject": "alice", "slot": "db", "value": "SQLite",
                 "context_scope": "", "created_at": "2024-01-01T00:00:00Z"},
            ])
            result = resolve_all_current_state(td)
        slots = result.get("slots") or {}
        for state in slots.values():
            self.assertNotEqual("conflict", state.get("status"))

    def test_global_claims_resolve_after_both_valid_supersede(self):
        """After both_valid, old global claims are superseded — no conflict in global bucket."""
        from core_memory.claim.resolver import resolve_all_current_state
        with tempfile.TemporaryDirectory() as td:
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "db", "value": "AWS",
                 "created_at": "2024-01-01T00:00:00Z"},
                {"id": "cb1", "subject": "alice", "slot": "db", "value": "SQLite",
                 "created_at": "2024-02-01T00:00:00Z"},
            ])
            # Simulate fork bead with two new scoped claims + supersede updates.
            _seed_claims_in_bead(td, "fork-bead", [
                {"id": "new-a", "subject": "alice", "slot": "db", "value": "AWS",
                 "context_scope": "prod", "created_at": "2024-03-01T00:00:00Z"},
                {"id": "new-b", "subject": "alice", "slot": "db", "value": "SQLite",
                 "context_scope": "staging", "created_at": "2024-03-01T00:00:00Z"},
            ], updates=[
                {"id": "u1", "decision": "supersede", "target_claim_id": "ca1",
                 "replacement_claim_id": "new-a", "subject": "alice", "slot": "db",
                 "trigger_bead_id": "fork-bead"},
                {"id": "u2", "decision": "supersede", "target_claim_id": "cb1",
                 "replacement_claim_id": "new-b", "subject": "alice", "slot": "db",
                 "trigger_bead_id": "fork-bead"},
            ])
            result = resolve_all_current_state(td)
        slots = result.get("slots") or {}
        # Global bucket should have no conflict (both superseded).
        global_state = slots.get("alice:db") or {}
        self.assertNotEqual("conflict", global_state.get("status"))
        # Scoped buckets should be active.
        prod_state = slots.get("alice:db::prod") or {}
        staging_state = slots.get("alice:db::staging") or {}
        self.assertEqual("active", prod_state.get("status"))
        self.assertEqual("active", staging_state.get("status"))


# ---------------------------------------------------------------------------
# 5. decide_dreamer_candidate — both_valid branch
# ---------------------------------------------------------------------------

def _enqueue_contradiction_candidate(td: str, claim_a_id: str, claim_b_id: str) -> str:
    import uuid
    from core_memory.runtime.dreamer.candidates import _write_candidates, _candidates_path
    cid = f"dc-bv-{uuid.uuid4().hex[:8]}"
    Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
    _write_candidates(td, [{
        "id": cid,
        "status": "unreviewed",
        "hypothesis_type": "contradiction_pressure_candidate",
        "subject": "alice",
        "slot": "database_backend",
        "claim_a_id": claim_a_id,
        "claim_b_id": claim_b_id,
        "run_metadata": {"session_id": "test-session"},
        "confidence": 0.8,
        "relationship": "contradicts",
        "source_bead_id": claim_a_id,
        "target_bead_id": claim_b_id,
        "rationale": "test conflict",
    }])
    return cid


class TestDecideBothValid(unittest.TestCase):
    def test_needs_clarification_when_scope_a_missing(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        with tempfile.TemporaryDirectory() as td:
            Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "database_backend", "value": "AWS"},
                {"id": "cb1", "subject": "alice", "slot": "database_backend", "value": "SQLite"},
            ])
            cid = _enqueue_contradiction_candidate(td, "ca1", "cb1")
            result = decide_dreamer_candidate(
                root=td, candidate_id=cid, decision="accept", apply=True,
                resolution="both_valid",
                scope_a="",  # missing
                scope_b="staging",
            )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("needs_clarification"))
        self.assertEqual("scope_a", result.get("missing"))

    def test_needs_clarification_when_scope_b_missing(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        with tempfile.TemporaryDirectory() as td:
            Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "database_backend", "value": "AWS"},
                {"id": "cb1", "subject": "alice", "slot": "database_backend", "value": "SQLite"},
            ])
            cid = _enqueue_contradiction_candidate(td, "ca1", "cb1")
            result = decide_dreamer_candidate(
                root=td, candidate_id=cid, decision="accept", apply=True,
                resolution="both_valid",
                scope_a="prod",
                scope_b="",  # missing
            )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("needs_clarification"))
        self.assertEqual("scope_b", result.get("missing"))

    def test_needs_clarification_does_not_change_candidate_status(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate, _read_candidates
        with tempfile.TemporaryDirectory() as td:
            Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "database_backend", "value": "AWS"},
                {"id": "cb1", "subject": "alice", "slot": "database_backend", "value": "SQLite"},
            ])
            cid = _enqueue_contradiction_candidate(td, "ca1", "cb1")
            decide_dreamer_candidate(
                root=td, candidate_id=cid, decision="accept", apply=True,
                resolution="both_valid", scope_a="prod", scope_b="",
            )
            rows = _read_candidates(td)
            candidate = next(r for r in rows if r["id"] == cid)
        self.assertEqual("unreviewed", candidate.get("status"))

    def test_accept_both_valid_calls_process_turn_finalized(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        with tempfile.TemporaryDirectory() as td:
            Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "database_backend", "value": "AWS"},
                {"id": "cb1", "subject": "alice", "slot": "database_backend", "value": "SQLite"},
            ])
            cid = _enqueue_contradiction_candidate(td, "ca1", "cb1")
            with patch("core_memory.runtime.engine.process_turn_finalized",
                       return_value={"ok": True}) as mock_ptf:
                result = decide_dreamer_candidate(
                    root=td, candidate_id=cid, decision="accept", apply=True,
                    resolution="both_valid", scope_a="prod", scope_b="staging",
                )
        self.assertTrue(mock_ptf.called)
        applied = result.get("applied") or {}
        self.assertEqual("context_scope_fork", applied.get("application_mode"))
        self.assertEqual("prod", applied.get("scope_a"))
        self.assertEqual("staging", applied.get("scope_b"))

    def test_accept_both_valid_application_mode(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        with tempfile.TemporaryDirectory() as td:
            Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "database_backend", "value": "AWS"},
                {"id": "cb1", "subject": "alice", "slot": "database_backend", "value": "SQLite"},
            ])
            cid = _enqueue_contradiction_candidate(td, "ca1", "cb1")
            with patch("core_memory.runtime.engine.process_turn_finalized",
                       return_value={"ok": True}):
                result = decide_dreamer_candidate(
                    root=td, candidate_id=cid, decision="accept", apply=True,
                    resolution="both_valid", scope_a="prod", scope_b="staging",
                )
        self.assertTrue(result.get("ok"))
        self.assertEqual("accepted", result.get("status"))

    def test_complement_default_empty_string_scope(self):
        """scope_b='' (complement default) is accepted as a valid non-empty resolution."""
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        with tempfile.TemporaryDirectory() as td:
            Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "database_backend", "value": "AWS"},
                {"id": "cb1", "subject": "alice", "slot": "database_backend", "value": "SQLite"},
            ])
            cid = _enqueue_contradiction_candidate(td, "ca1", "cb1")
            # scope_b="" should trigger needs_clarification because empty = missing
            result = decide_dreamer_candidate(
                root=td, candidate_id=cid, decision="accept", apply=True,
                resolution="both_valid", scope_a="prod", scope_b="",
            )
        # Empty string means not provided — still needs clarification
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("needs_clarification"))


# ---------------------------------------------------------------------------
# 6. apply_reviewed_proposal — context_a / context_b threaded through
# ---------------------------------------------------------------------------

class TestApplyReviewedProposalBothValid(unittest.TestCase):
    def test_context_a_context_b_passed_as_scope(self):
        from core_memory.integrations.mcp.typed_write import apply_reviewed_proposal
        calls = []
        def fake_decide(**kw):
            calls.append(kw)
            return {"ok": True, "status": "accepted", "applied": {"application_mode": "context_scope_fork"}, "path": ""}
        with patch("core_memory.integrations.mcp.typed_write.decide_dreamer_candidate", fake_decide):
            apply_reviewed_proposal(
                root="/tmp/x",
                candidate_id="cid-1",
                decision="accept",
                resolution="both_valid",
                context_a="prod",
                context_b="staging",
            )
        self.assertEqual(1, len(calls))
        self.assertEqual("prod", calls[0].get("scope_a"))
        self.assertEqual("staging", calls[0].get("scope_b"))

    def test_none_context_passes_as_none(self):
        from core_memory.integrations.mcp.typed_write import apply_reviewed_proposal
        calls = []
        def fake_decide(**kw):
            calls.append(kw)
            return {"ok": True, "status": "accepted", "applied": {}, "path": ""}
        with patch("core_memory.integrations.mcp.typed_write.decide_dreamer_candidate", fake_decide):
            apply_reviewed_proposal(
                root="/tmp/x",
                candidate_id="cid-2",
                decision="accept",
                resolution="prefer_a",
            )
        self.assertIsNone(calls[0].get("scope_a"))
        self.assertIsNone(calls[0].get("scope_b"))


# ---------------------------------------------------------------------------
# 7. Full round-trip: conflict clears after both_valid resolution
# ---------------------------------------------------------------------------

class TestBothValidRoundTrip(unittest.TestCase):
    def test_conflict_resolves_to_two_active_scoped_claims(self):
        """
        Seed two conflicting global claims; apply both_valid with two scopes;
        verify the resolver sees two active scoped buckets and no global conflict.
        """
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate
        from core_memory.claim.resolver import resolve_all_current_state

        with tempfile.TemporaryDirectory() as td:
            Path(td + "/.beads/events").mkdir(parents=True, exist_ok=True)
            _seed_claims_in_bead(td, "b1", [
                {"id": "ca1", "subject": "alice", "slot": "db_backend",
                 "value": "AWS", "created_at": "2024-01-01T00:00:00Z"},
                {"id": "cb1", "subject": "alice", "slot": "db_backend",
                 "value": "SQLite", "created_at": "2024-02-01T00:00:00Z"},
            ])
            cid = _enqueue_contradiction_candidate(td, "ca1", "cb1")

            with patch("core_memory.runtime.engine.process_turn_finalized",
                       return_value={"ok": True}) as mock_ptf:
                with patch("core_memory.persistence.store_claim_ops.find_canonical_turn_bead_id",
                           return_value="fork-bead-001"):
                    result = decide_dreamer_candidate(
                        root=td, candidate_id=cid, decision="accept", apply=True,
                        resolution="both_valid", scope_a="prod", scope_b="staging",
                    )

            self.assertTrue(result.get("ok"))
            applied = result.get("applied") or {}
            self.assertEqual("context_scope_fork", applied.get("application_mode"))

            # After both_valid: two new scoped claims written to fork bead.
            # Verify the index was updated with the new claims.
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text())
            fork_bead = (idx.get("beads") or {}).get("fork-bead-001") or {}
            scoped_claims = [c for c in (fork_bead.get("claims") or [])
                             if c.get("context_scope")]
            self.assertEqual(2, len(scoped_claims))
            scopes = {c["context_scope"] for c in scoped_claims}
            self.assertEqual({"prod", "staging"}, scopes)

            # And supersede updates were emitted.
            updates = fork_bead.get("claim_updates") or []
            self.assertGreaterEqual(len(updates), 2)


if __name__ == "__main__":
    unittest.main()
