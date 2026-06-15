import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.goal_filters import is_active_goal
from core_memory.runtime.dreamer.projection import (
    FUTURE_PROJECTION_SCHEMA,
    FUTURE_VECTOR_SCHEMA,
    _attractor_strength,
    compute_future_projections,
    read_future_projections,
)


def _claim_chain(store):
    # A claim worldline (subject/slot) forms a backbone storyline.
    b1 = store.add_bead(type="decision", title="Adopt PST", summary=["s"], because=["x"], detail="d",
                        entities=["timezone"], topics=["timezone"], session_id="s1",
                        claims=[{"id": "c1", "subject": "user", "slot": "timezone", "value": "PST", "claim_kind": "preference"}])
    b2 = store.add_bead(type="decision", title="Confirm PST", summary=["s"], because=["y"], detail="d",
                        entities=["timezone"], topics=["timezone"], session_id="s2",
                        claims=[{"id": "c2", "subject": "user", "slot": "timezone", "value": "PST", "claim_kind": "preference"}])
    return b1, b2


class TestFutureProjection(unittest.TestCase):
    def test_empty_store_no_projections(self):
        with tempfile.TemporaryDirectory() as td:
            out = compute_future_projections(td, persist=False)
            self.assertTrue(out["ok"])
            self.assertEqual(0, out["projection_count"])

    def test_projection_shape_and_governance(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _claim_chain(store)
            out = compute_future_projections(td, persist=False)
            self.assertGreaterEqual(out["projection_count"], 1)
            proj = out["projections"][0]
            self.assertEqual(FUTURE_PROJECTION_SCHEMA, proj["schema"])
            self.assertTrue(proj["future_vectors"])
            v = proj["future_vectors"][0]
            self.assertEqual(FUTURE_VECTOR_SCHEMA, v["schema"])
            self.assertTrue(0.0 <= v["narrative_strength"] <= 1.0)
            self.assertTrue(0.0 <= v["attractor_strength"] <= 1.0)
            # Governance: advisory only.
            self.assertFalse(proj["governance"]["may_create_goals"])
            self.assertFalse(proj["governance"]["may_create_beads"])
            self.assertTrue(proj["governance"]["may_influence_goal_pursuit"])
            # A most-likely vector is selected.
            ids = {vv["id"] for vv in proj["future_vectors"]}
            self.assertIn(proj["narratively_most_likely_vector_id"], ids)

    def test_deterministic_scores(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _claim_chain(store)
            a = compute_future_projections(td, persist=False)["projections"]
            b = compute_future_projections(td, persist=False)["projections"]

            def _scores(ps):
                return sorted((p["source_storyline_id"],
                               round(p["future_vectors"][0]["narrative_strength"], 6),
                               round(p["future_vectors"][0]["attractor_strength"], 6)) for p in ps)
            self.assertEqual(_scores(a), _scores(b))

    def test_continuation_outranks_tension_fork(self):
        # Build a storyline that carries a tension (two competing overlays) so a
        # tension-resolution vector exists; the continuation should score higher.
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _claim_chain(store)
            out = compute_future_projections(td, persist=False)
            for proj in out["projections"]:
                kinds = {v["kind"]: v["narrative_strength"] for v in proj["future_vectors"]}
                if "continuation" in kinds and "tension_resolution" in kinds:
                    self.assertGreater(kinds["continuation"], kinds["tension_resolution"])

    def test_persist_and_read(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _claim_chain(store)
            compute_future_projections(td, persist=True)
            rows = read_future_projections(td)
            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(FUTURE_PROJECTION_SCHEMA, rows[0]["schema"])
            # Stored under events, not as beads.
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            types = {b.get("type") for b in idx["beads"].values()}
            self.assertNotIn("future_projection", types)

    def test_dreamer_run_invokes_projection(self):
        from core_memory.runtime.queue.side_effect_queue import process_side_effect_event

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _claim_chain(store)
            out = process_side_effect_event(root=td, kind="dreamer-run", payload={"mode": "suggest"})
            self.assertIn("projection", out)
            self.assertTrue(out["projection"]["ok"])


class TestProjectionFixes(unittest.TestCase):
    def test_attractor_excludes_source_goal_bead(self):
        # Codex P2: a goal-backed storyline must not count its own goal bead.
        theme = {"explainability"}
        goal_themes = [("g_self", {"explainability"}), ("g_other", {"explainability"})]
        # g_self is a backbone member of this storyline → excluded.
        score, conv = _attractor_strength(theme, "wl-goal-xyz", {"g_self"}, [], goal_themes)
        self.assertIn("goal:g_other", conv)
        self.assertNotIn("goal:g_self", conv)

    def test_is_active_goal_excludes_terminal(self):
        self.assertTrue(is_active_goal({"type": "goal", "status": "open"}))
        self.assertFalse(is_active_goal({"type": "goal", "status": "resolved"}))
        self.assertFalse(is_active_goal({"type": "goal", "goal_status": "resolved"}))
        self.assertFalse(is_active_goal({"type": "goal", "promotion_state": "resolved"}))
        self.assertFalse(is_active_goal({"type": "goal", "status": "promoted"}))  # legacy
        self.assertFalse(is_active_goal({"type": "goal", "promoted": True}))
        self.assertFalse(is_active_goal({"type": "goal", "approval_status": "rejected"}))
        self.assertFalse(is_active_goal({"type": "decision"}))

    def test_superseded_support_beads_excluded_from_vectors(self):
        import json as _json
        from pathlib import Path as _Path

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            b1, b2 = _claim_chain(store)
            # Supersede one backbone bead.
            idx_path = _Path(td) / ".beads" / "index.json"
            idx = _json.loads(idx_path.read_text(encoding="utf-8"))
            idx["beads"][b1]["status"] = "superseded"
            idx_path.write_text(_json.dumps(idx), encoding="utf-8")
            out = compute_future_projections(td, persist=False)
            for proj in out["projections"]:
                for v in proj["future_vectors"]:
                    self.assertNotIn(b1, v["supporting_beads"])  # stale evidence excluded


if __name__ == "__main__":
    unittest.main()
