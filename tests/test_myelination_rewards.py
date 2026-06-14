import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.memory import confirm_bead
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.observability.myelination import compute_myelination_bonus_map
from core_memory.runtime.observability.myelination_rewards import (
    emit_myelination_reward_event,
    read_reward_events,
    reward_bonus_by_edge_key,
    supporting_edge_keys_for_bead,
)

_ENV = {"CORE_MEMORY_MYELINATION_ENABLED": "1", "CORE_MEMORY_SEMANTIC_AUTODRAIN": "off"}


def _edge(src, dst, rel="supports"):
    return f"{src}|{rel}|{dst}"


class TestRewardEventGuardrail(unittest.TestCase):
    def test_no_edges_no_event(self):
        with tempfile.TemporaryDirectory() as td:
            out = emit_myelination_reward_event(
                td, source_type="human_approval", polarity="positive", edge_keys=[]
            )
            self.assertFalse(out["ok"])
            self.assertEqual("no_concrete_edges", out["skipped"])
            self.assertFalse((Path(td) / ".beads" / "events" / "myelination-rewards.jsonl").exists())

    def test_bad_polarity_and_source(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(emit_myelination_reward_event(td, source_type="human_approval", polarity="meh", edge_keys=[_edge("a", "b")])["ok"])
            self.assertFalse(emit_myelination_reward_event(td, source_type="bogus", polarity="positive", edge_keys=[_edge("a", "b")])["ok"])

    def test_valid_event_is_written(self):
        with tempfile.TemporaryDirectory() as td:
            out = emit_myelination_reward_event(
                td, source_type="human_approval", polarity="positive", edge_keys=[_edge("a", "b")], reason="x"
            )
            self.assertTrue(out["ok"])
            rows = read_reward_events(td)
            self.assertEqual(1, len(rows))
            self.assertEqual("myelination_reward_event.v1", rows[0]["schema"])
            self.assertEqual([_edge("a", "b")], rows[0]["edge_keys"])
            self.assertFalse(rows[0]["guardrails"]["mutates_beads"])


class TestRewardAggregation(unittest.TestCase):
    def test_signed_sum_and_count(self):
        with tempfile.TemporaryDirectory() as td:
            ek = _edge("a", "b")
            emit_myelination_reward_event(td, source_type="human_approval", polarity="positive", edge_keys=[ek], strength=0.04)
            emit_myelination_reward_event(td, source_type="human_approval", polarity="positive", edge_keys=[ek], strength=0.04)
            emit_myelination_reward_event(td, source_type="human_rejection", polarity="negative", edge_keys=[ek], strength=0.04)
            agg = reward_bonus_by_edge_key(td)
            self.assertAlmostEqual(0.04, agg[ek]["bonus"], places=6)  # 0.04+0.04-0.04
            self.assertEqual(3, agg[ek]["count"])


class TestFusionIntoManifest(unittest.TestCase):
    def test_reward_only_edge_bypasses_min_hits(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            ek = _edge("bX", "bY")
            emit_myelination_reward_event(td, source_type="human_approval", polarity="positive", edge_keys=[ek], strength=0.05)
            m = compute_myelination_bonus_map(td)
            self.assertTrue(m["enabled"])
            self.assertEqual("core_memory.myelination_manifest.v2", m["schema"])
            self.assertIn(ek, m["bonus_by_edge_key"])
            self.assertAlmostEqual(0.05, m["bonus_by_edge_key"][ek], places=6)
            self.assertEqual({"human_approval": 1}, m["source_event_counts"])

    def test_cap_clamped(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            ek = _edge("a", "b")
            for _ in range(10):
                emit_myelination_reward_event(td, source_type="human_approval", polarity="positive", edge_keys=[ek], strength=0.04)
            m = compute_myelination_bonus_map(td)
            # 10 * 0.04 = 0.4, clamped to pos_cap default 0.12
            self.assertAlmostEqual(0.12, m["bonus_by_edge_key"][ek], places=6)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            emit_myelination_reward_event(td, source_type="human_approval", polarity="positive", edge_keys=[_edge("a", "b")])
            a = compute_myelination_bonus_map(td)
            b = compute_myelination_bonus_map(td)
            self.assertEqual(a["bonus_by_edge_key"], b["bonus_by_edge_key"])

    def test_disabled_no_fusion(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_MYELINATION_ENABLED": "0"}, clear=False):
            emit_myelination_reward_event(td, source_type="human_approval", polarity="positive", edge_keys=[_edge("a", "b")])
            m = compute_myelination_bonus_map(td)
            self.assertFalse(m["enabled"])
            self.assertEqual({}, m["bonus_by_edge_key"])

    def test_edge_only_no_smearing_to_unrelated_paths(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            emit_myelination_reward_event(td, source_type="human_approval", polarity="positive", edge_keys=[_edge("a", "b")], strength=0.05)
            m = compute_myelination_bonus_map(td)
            # Only beads a and b (endpoints of the rewarded edge) get projection;
            # an unrelated bead c is untouched.
            self.assertIn("a", m["bonus_by_bead_id"])
            self.assertIn("b", m["bonus_by_bead_id"])
            self.assertNotIn("c", m["bonus_by_bead_id"])


class TestSupportingEdgeDerivation(unittest.TestCase):
    def test_evidence_edges_from_associations(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            ev = store.add_bead(type="evidence", title="Metric spiked", summary=["s"], supports_bead_ids=["x"], detail="d", session_id="s1")
            dec = store.add_bead(type="decision", title="Cut the vendor", summary=["s"], because=["cost"], detail="d", session_id="s1")
            store.link(ev, dec, "supports")
            store.link(dec, ev, "associated_with")  # non-evidential, must be ignored
            eks = supporting_edge_keys_for_bead(td, dec)
            self.assertIn(f"{ev}|supports|{dec}", eks)
            self.assertNotIn(f"{dec}|associated_with|{ev}", eks)


class TestRecallTraceIncidentFilter(unittest.TestCase):
    def test_recall_trace_only_includes_edges_touching_the_bead(self):
        # Codex P1: a multi-result feedback row stores the flat union of all
        # chain edges; only edges incident to the decided bead are supporting.
        from core_memory.runtime.observability.retrieval_feedback import record_retrieval_feedback

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            record_retrieval_feedback(
                td,
                request={"query": "q", "intent": "remember"},
                response={
                    "ok": True,
                    "answer_outcome": "answer",
                    "results": [{"bead_id": "target"}, {"bead_id": "other"}],
                    "chains": [
                        {"edges": [
                            {"src": "E1", "dst": "target", "rel": "supports"},      # touches target
                            {"src": "X", "dst": "other", "rel": "supports"},         # unrelated chain
                        ]},
                    ],
                },
            )
            eks = supporting_edge_keys_for_bead(td, "target")
            self.assertIn("E1|supports|target", eks)
            self.assertNotIn("X|supports|other", eks)


class TestRewardSinceWindow(unittest.TestCase):
    def test_since_window_excludes_old_events(self):
        # Codex P2: read_reward_events must honor the since window.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".beads" / "events" / "myelination-rewards.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            old = {
                "schema": "myelination_reward_event.v1", "id": "mr-old",
                "created_at": "2000-01-01T00:00:00+00:00", "source_type": "human_approval",
                "polarity": "positive", "strength": 0.04, "edge_keys": [_edge("a", "b")],
            }
            new = {
                "schema": "myelination_reward_event.v1", "id": "mr-new",
                "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "source_type": "human_approval", "polarity": "positive", "strength": 0.04,
                "edge_keys": [_edge("a", "b")],
            }
            with path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(old) + "\n")
                f.write(json.dumps(new) + "\n")
            recent = read_reward_events(td, since="1d")
            self.assertEqual(["mr-new"], [r["id"] for r in recent])
            # No window (since="") keeps both.
            self.assertEqual({"mr-old", "mr-new"}, {r["id"] for r in read_reward_events(td, since="")})


class TestApprovalProducesReward(unittest.TestCase):
    def test_approve_emits_positive_reward_on_supporting_edge(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            ev = store.add_bead(type="evidence", title="Vendor overcharged", summary=["s"], detail="d", session_id="s1")
            dec = store.add_bead(type="decision", title="Switch vendor", summary=["s"], because=["cost"], detail="d", session_id="s1")
            store.link(ev, dec, "supports")
            store.approve(dec, approver="john")
            rows = read_reward_events(td)
            self.assertEqual(1, len(rows))
            self.assertEqual("human_approval", rows[0]["source_type"])
            self.assertEqual("positive", rows[0]["polarity"])
            self.assertIn(f"{ev}|supports|{dec}", rows[0]["edge_keys"])
            m = compute_myelination_bonus_map(td)
            self.assertGreater(m["bonus_by_edge_key"].get(f"{ev}|supports|{dec}", 0.0), 0.0)

    def test_reject_emits_negative_reward(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            ev = store.add_bead(type="evidence", title="Noise signal", summary=["s"], detail="d", session_id="s1")
            dec = store.add_bead(type="context", title="Spurious link", summary=["s"], session_id="s1")
            store.link(ev, dec, "supports")
            store.reject(dec, approver="john", reason="noise")
            rows = read_reward_events(td)
            self.assertEqual("negative", rows[0]["polarity"])
            self.assertEqual("human_rejection", rows[0]["source_type"])
            m = compute_myelination_bonus_map(td)
            self.assertLess(m["bonus_by_edge_key"].get(f"{ev}|supports|{dec}", 0.0), 0.0)

    def test_confirm_emits_positive_reward(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            ev = store.add_bead(type="evidence", title="Receipt", summary=["s"], detail="d", session_id="s1")
            dec = store.add_bead(type="decision", title="Refund issued", summary=["s"], because=["policy"], detail="d", session_id="s1")
            store.link(ev, dec, "supports")
            confirm_bead(td, dec)
            rows = read_reward_events(td)
            self.assertEqual(1, len(rows))
            self.assertEqual("positive", rows[0]["polarity"])

    def test_approve_with_no_supporting_edge_emits_nothing(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            lone = store.add_bead(type="decision", title="Standalone", summary=["s"], because=["x"], detail="d", session_id="s1")
            store.approve(lone, approver="john")
            self.assertEqual([], read_reward_events(td))

    def test_myelination_off_emits_no_reward(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_MYELINATION_ENABLED": "0"}, clear=False):
            store = MemoryStore(root=td)
            ev = store.add_bead(type="evidence", title="E", summary=["s"], detail="d", session_id="s1")
            dec = store.add_bead(type="decision", title="D", summary=["s"], because=["x"], detail="d", session_id="s1")
            store.link(ev, dec, "supports")
            store.approve(dec, approver="john")
            self.assertEqual([], read_reward_events(td))


class TestGoalResolutionReward(unittest.TestCase):
    def test_reward_goal_resolution_emits_positive_on_resolves_edge(self):
        from core_memory.runtime.observability.myelination_rewards import reward_goal_resolution

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            goal = store.add_bead(type="goal", title="G", summary=["s"], goal_id="g1", session_id="s1")
            outcome = store.add_bead(type="outcome", title="O", summary=["s"], result="resolved", detail="d", session_id="s1")
            store.link(outcome, goal, "resolves")
            out = reward_goal_resolution(td, goal_bead_id=goal, outcome_bead_id=outcome)
            self.assertTrue(out["ok"])
            rows = read_reward_events(td)
            self.assertEqual(1, len(rows))
            self.assertEqual("goal_resolution", rows[0]["source_type"])
            self.assertEqual("positive", rows[0]["polarity"])
            self.assertEqual([f"{outcome}|resolves|{goal}"], rows[0]["edge_keys"])

    def test_reward_goal_resolution_noop_without_association(self):
        # Edge-only invariant: do not reward an edge that isn't in the graph.
        from core_memory.runtime.observability.myelination_rewards import reward_goal_resolution

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            out = reward_goal_resolution(td, goal_bead_id="g1", outcome_bead_id="o1")
            self.assertFalse(out["ok"])
            self.assertEqual("no_resolves_association", out["skipped"])
            self.assertEqual([], read_reward_events(td))

    def test_reward_goal_resolution_disabled_is_noop(self):
        from core_memory.runtime.observability.myelination_rewards import reward_goal_resolution

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_MYELINATION_ENABLED": "0"}, clear=False):
            out = reward_goal_resolution(td, goal_bead_id="g1", outcome_bead_id="o1")
            self.assertFalse(out["ok"])
            self.assertEqual([], read_reward_events(td))

    def test_resolving_a_goal_candidate_reinforces_resolves_edge(self):
        from core_memory.persistence.promotion_service import resolve_goal_candidate_for_store

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            goal = store.add_bead(
                type="goal", title="Reduce onboarding friction", summary=["s"],
                goal_id="g-onboard", success_criteria="new user completes setup",
                promotion_candidate=True, session_id="s1",
            )
            outcome = store.add_bead(
                type="outcome", title="Onboarding flow shipped", summary=["s"],
                result="resolved", linked_bead_id=goal, detail="d", session_id="s1",
            )
            store.link(outcome, goal, "resolves")
            res = resolve_goal_candidate_for_store(store, goal_bead_id=goal, outcome_bead_id=outcome)
            self.assertTrue(res["ok"])
            self.assertEqual("resolved", res["after_status"])

            rows = read_reward_events(td)
            self.assertEqual(1, len(rows))
            self.assertEqual("goal_resolution", rows[0]["source_type"])
            ek = f"{outcome}|resolves|{goal}"
            self.assertEqual([ek], rows[0]["edge_keys"])
            m = compute_myelination_bonus_map(td)
            self.assertGreater(m["bonus_by_edge_key"].get(ek, 0.0), 0.0)
            self.assertEqual(1, m["source_event_counts"].get("goal_resolution"))


class TestDreamerCandidateReward(unittest.TestCase):
    def _candidate(self, store, src, dst, rel="supports", htype="retrieval_value_candidate"):
        store.link(src, dst, rel)
        return {"id": "dc-1", "hypothesis_type": htype, "source_bead_id": src, "target_bead_id": dst, "relationship": rel}

    def test_accept_reinforces_existing_edge(self):
        from core_memory.runtime.observability.myelination_rewards import reward_dreamer_candidate_decision

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s1")
            b = store.add_bead(type="decision", title="B", summary=["s"], because=["x"], detail="d", session_id="s1")
            cand = self._candidate(store, a, b)
            out = reward_dreamer_candidate_decision(td, candidate=cand, decision="accept")
            self.assertTrue(out["ok"])
            rows = read_reward_events(td)
            self.assertEqual("dreamer_candidate_decision", rows[0]["source_type"])
            self.assertEqual("positive", rows[0]["polarity"])
            self.assertEqual([f"{a}|supports|{b}"], rows[0]["edge_keys"])

    def test_reject_weakens_existing_edge(self):
        from core_memory.runtime.observability.myelination_rewards import reward_dreamer_candidate_decision

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s1")
            b = store.add_bead(type="context", title="B", summary=["s"], session_id="s1")
            cand = self._candidate(store, a, b)
            out = reward_dreamer_candidate_decision(td, candidate=cand, decision="reject")
            self.assertTrue(out["ok"])
            self.assertEqual("negative", read_reward_events(td)[0]["polarity"])

    def test_noop_when_edge_absent(self):
        from core_memory.runtime.observability.myelination_rewards import reward_dreamer_candidate_decision

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            cand = {"id": "dc-2", "hypothesis_type": "retrieval_value_candidate", "source_bead_id": "x", "target_bead_id": "y", "relationship": "supports"}
            out = reward_dreamer_candidate_decision(td, candidate=cand, decision="accept")
            self.assertFalse(out["ok"])
            self.assertEqual("no_concrete_edge", out["skipped"])
            self.assertEqual([], read_reward_events(td))

    def test_contradiction_candidate_deferred_to_claim_path(self):
        from core_memory.runtime.observability.myelination_rewards import reward_dreamer_candidate_decision

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s1")
            b = store.add_bead(type="context", title="B", summary=["s"], session_id="s1")
            cand = self._candidate(store, a, b, rel="contradicts", htype="contradiction_pressure_candidate")
            out = reward_dreamer_candidate_decision(td, candidate=cand, decision="accept")
            self.assertFalse(out["ok"])
            self.assertEqual("claim_conflict_path", out["skipped"])

    def test_legacy_relation_is_normalized_for_match_and_edge_key(self):
        # Codex P2: a stored "Causes" edge must reward under the canonical
        # caused_by key consumers query, not the raw src|Causes|dst.
        from core_memory.runtime.observability.myelination_rewards import (
            reward_dreamer_candidate_decision,
            supporting_edge_keys_for_bead,
        )

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s1")
            b = store.add_bead(type="decision", title="B", summary=["s"], because=["x"], detail="d", session_id="s1")
            store.link(a, b, "Causes")  # legacy/mixed-case relation
            cand = {"id": "dc-leg", "hypothesis_type": "retrieval_value_candidate",
                    "source_bead_id": a, "target_bead_id": b, "relationship": "Causes"}
            out = reward_dreamer_candidate_decision(td, candidate=cand, decision="accept")
            self.assertTrue(out["ok"])
            self.assertEqual([f"{a}|caused_by|{b}"], read_reward_events(td)[0]["edge_keys"])
            # supporting-edge derivation normalizes too.
            self.assertIn(f"{a}|caused_by|{b}", supporting_edge_keys_for_bead(td, b))

    def test_decide_dreamer_candidate_emits_reward_once(self):
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate, _write_candidates

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s1")
            b = store.add_bead(type="decision", title="B", summary=["s"], because=["x"], detail="d", session_id="s1")
            store.link(a, b, "supports")
            # Seed a candidate referencing the existing edge.
            _write_candidates(td, [{
                "id": "dc-live", "status": "pending", "hypothesis_type": "retrieval_value_candidate",
                "source_bead_id": a, "target_bead_id": b, "relationship": "supports", "confidence": 0.7,
            }])
            r1 = decide_dreamer_candidate(root=td, candidate_id="dc-live", decision="accept")
            self.assertTrue(r1["ok"])
            self.assertEqual(1, len(read_reward_events(td)))
            # Idempotent re-decision must not double-emit.
            decide_dreamer_candidate(root=td, candidate_id="dc-live", decision="accept")
            self.assertEqual(1, len(read_reward_events(td)))


class TestClaimConflictReward(unittest.TestCase):
    def _setup(self, store):
        # Two conflicting claims, each carried by its own bead, each with an
        # evidence association feeding its carrier.
        bead_a = store.add_bead(type="context", title="Claim A carrier", summary=["s"], session_id="s1",
                                claims=[{"id": "ca", "subject": "user", "slot": "tz", "value": "PST", "claim_kind": "preference"}])
        bead_b = store.add_bead(type="context", title="Claim B carrier", summary=["s"], session_id="s1",
                                claims=[{"id": "cb", "subject": "user", "slot": "tz", "value": "EST", "claim_kind": "preference"}])
        ev_a = store.add_bead(type="evidence", title="EvA", summary=["s"], detail="d", session_id="s1")
        ev_b = store.add_bead(type="evidence", title="EvB", summary=["s"], detail="d", session_id="s1")
        store.link(ev_a, bead_a, "supports")
        store.link(ev_b, bead_b, "supports")
        return bead_a, bead_b, ev_a, ev_b

    def test_prefer_a_reinforces_a_weakens_b(self):
        from core_memory.runtime.observability.myelination_rewards import reward_claim_conflict_resolution

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            bead_a, bead_b, ev_a, ev_b = self._setup(store)
            out = reward_claim_conflict_resolution(td, resolution="prefer_a", claim_a_id="ca", claim_b_id="cb")
            self.assertTrue(out["ok"])
            self.assertEqual(2, out["emitted"])
            m = compute_myelination_bonus_map(td)
            self.assertGreater(m["bonus_by_edge_key"].get(f"{ev_a}|supports|{bead_a}", 0.0), 0.0)
            self.assertLess(m["bonus_by_edge_key"].get(f"{ev_b}|supports|{bead_b}", 0.0), 0.0)
            self.assertEqual(2, m["source_event_counts"].get("claim_conflict_resolution"))

    def test_retract_both_weakens_both(self):
        from core_memory.runtime.observability.myelination_rewards import reward_claim_conflict_resolution

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            bead_a, bead_b, ev_a, ev_b = self._setup(store)
            reward_claim_conflict_resolution(td, resolution="retract_both", claim_a_id="ca", claim_b_id="cb")
            for r in read_reward_events(td):
                self.assertEqual("negative", r["polarity"])
            m = compute_myelination_bonus_map(td)
            self.assertLess(m["bonus_by_edge_key"].get(f"{ev_a}|supports|{bead_a}", 0.0), 0.0)
            self.assertLess(m["bonus_by_edge_key"].get(f"{ev_b}|supports|{bead_b}", 0.0), 0.0)

    def test_both_valid_is_scoped_no_reward(self):
        from core_memory.runtime.observability.myelination_rewards import reward_claim_conflict_resolution

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            self._setup(store)
            out = reward_claim_conflict_resolution(td, resolution="both_valid", claim_a_id="ca", claim_b_id="cb")
            self.assertFalse(out["ok"])
            self.assertEqual("both_valid_scoped", out["skipped"])
            self.assertEqual([], read_reward_events(td))

    def test_decay_is_edge_level_only_no_recall_trace_smear(self):
        # Decay uses evidence associations only — a retrieval-trace edge that
        # merely touched the carrier bead must not be weakened.
        from core_memory.runtime.observability.myelination_rewards import reward_claim_conflict_resolution
        from core_memory.runtime.observability.retrieval_feedback import record_retrieval_feedback

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            bead_a, bead_b, ev_a, ev_b = self._setup(store)
            # A recall trace touching bead_b via a non-evidence edge.
            record_retrieval_feedback(td, request={"query": "q"}, response={
                "ok": True, "answer_outcome": "answer", "results": [{"bead_id": bead_b}],
                "chains": [{"edges": [{"src": "X", "dst": bead_b, "rel": "associated_with"}]}],
            })
            reward_claim_conflict_resolution(td, resolution="prefer_a", claim_a_id="ca", claim_b_id="cb")
            keys = {ek for r in read_reward_events(td) for ek in r["edge_keys"]}
            self.assertNotIn(f"X|associated_with|{bead_b}", keys)  # recall-trace edge untouched
            self.assertIn(f"{ev_b}|supports|{bead_b}", keys)        # evidence edge weakened

    def test_disabled_is_noop(self):
        from core_memory.runtime.observability.myelination_rewards import reward_claim_conflict_resolution

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_MYELINATION_ENABLED": "0"}, clear=False):
            store = MemoryStore(root=td)
            self._setup(store)
            out = reward_claim_conflict_resolution(td, resolution="prefer_a", claim_a_id="ca", claim_b_id="cb")
            self.assertFalse(out["ok"])
            self.assertEqual([], read_reward_events(td))


if __name__ == "__main__":
    unittest.main()
