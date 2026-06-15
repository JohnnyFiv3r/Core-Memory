import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.assembly_depth import (
    ASSEMBLY_DEPTH_SCHEMA,
    compute_assembly_depth,
)


def _reports_by_id(out):
    return {r["target_id"]: r for r in out["reports"]}


class TestAssemblyDepthEngine(unittest.TestCase):
    def test_empty_store_returns_no_reports(self):
        with tempfile.TemporaryDirectory() as td:
            out = compute_assembly_depth(td, target_kind="goal")
            self.assertEqual(ASSEMBLY_DEPTH_SCHEMA, out["schema"])
            self.assertEqual([], out["reports"])
            self.assertEqual(0, out["config"]["population"])

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            store.add_bead(type="goal", title="G1", summary=["s"], goal_id="g1", session_id="s1")
            store.add_bead(type="goal", title="G2", summary=["s"], goal_id="g2", session_id="s2")
            a = compute_assembly_depth(td, target_kind="goal")
            b = compute_assembly_depth(td, target_kind="goal")
            self.assertEqual(
                [(r["target_id"], r["score"]) for r in a["reports"]],
                [(r["target_id"], r["score"]) for r in b["reports"]],
            )

    def test_report_shape_and_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            store.add_bead(type="goal", title="G", summary=["s"], goal_id="g1", session_id="s1")
            out = compute_assembly_depth(td, target_kind="goal")
            r = out["reports"][0]
            self.assertEqual(ASSEMBLY_DEPTH_SCHEMA, r["schema"])
            self.assertTrue(0.0 <= r["score"] <= 1.0)
            self.assertIn("factors_raw", r["components"])
            self.assertIn("factors_norm", r["components"])
            self.assertIn("anti_factors", r["components"])
            self.assertIn(r["interpretation"], {"low", "medium", "high"})

    def test_well_supported_goal_outranks_isolated_goal(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            # Rich goal: cross-session evidence, claims, human confirmation, causal edges.
            rich = store.add_bead(type="goal", title="Rich goal", summary=["s"], goal_id="rich",
                                  because=["x"], session_id="s1")
            ev1 = store.add_bead(type="evidence", title="E1", summary=["s"], detail="d", session_id="s2")
            ev2 = store.add_bead(type="decision", title="D1", summary=["s"], because=["y"], detail="d", session_id="s3",
                                 claims=[{"id": "c1", "subject": "u", "slot": "x", "value": "v", "claim_kind": "preference"}])
            store.link(ev1, rich, "supports")
            store.link(ev2, rich, "caused_by")
            store.confirm(ev2)  # human confirmation in support set

            # Isolated goal: single session, no support.
            lone = store.add_bead(type="goal", title="Lone goal", summary=["s"], goal_id="lone", session_id="s1")

            out = compute_assembly_depth(td, target_kind="goal")
            by_id = _reports_by_id(out)
            self.assertGreater(by_id[rich]["score"], by_id[lone]["score"])

    def test_anti_factors_penalize_speculative_single_session(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            # A grounded multi-session goal vs a speculative single-session one.
            grounded = store.add_bead(type="goal", title="Grounded", summary=["s"], goal_id="gr", session_id="s1")
            ev = store.add_bead(type="evidence", title="E", summary=["s"], detail="d", session_id="s2")
            store.link(ev, grounded, "supports")

            spec = store.add_bead(type="goal", title="Speculative", summary=["s"], goal_id="sp",
                                  grounding="speculative", session_id="s1")
            out = _reports_by_id(compute_assembly_depth(td, target_kind="goal"))
            self.assertGreater(out[grounded]["score"], out[spec]["score"])
            self.assertEqual(1.0, out[spec]["components"]["anti_factors"]["speculative_only_support"])
            self.assertEqual(1.0, out[spec]["components"]["anti_factors"]["single_session_concentration"])

    def test_target_kind_filter(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            store.add_bead(type="goal", title="G", summary=["s"], goal_id="g1", session_id="s1")
            store.add_bead(type="decision", title="D", summary=["s"], because=["x"], detail="d", session_id="s1")
            goals = compute_assembly_depth(td, target_kind="goal")
            decisions = compute_assembly_depth(td, target_kind="decision")
            self.assertEqual(1, goals["config"]["population"])
            self.assertEqual(1, decisions["config"]["population"])
            self.assertEqual("decision", decisions["reports"][0]["target_kind"])


if __name__ == "__main__":
    unittest.main()
