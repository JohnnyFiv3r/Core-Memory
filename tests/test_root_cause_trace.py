import tempfile
import unittest
from pathlib import Path

from core_memory.graph.root_cause import root_cause_trace
from core_memory.persistence.store import MemoryStore


class TestRootCauseTrace(unittest.TestCase):
    def test_simple_upstream_chain_returns_terminal_root_cause(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            outcome = store.add_bead(type="structured_observation", title="COGS spike", summary=["COGS increased 38%."], observed_at="2026-05-04T15:00:00Z")
            vendor = store.add_bead(type="state_assertion", title="Vendor price increase", summary=["Vendor prices increased."], observed_at="2026-05-03T15:00:00Z")
            contract = store.add_bead(type="document_reference", title="Contract amendment", summary=["Pricing changed."], observed_at="2026-05-01T15:00:00Z")
            store.link(source_id=vendor, target_id=outcome, relationship="causes", confidence=0.95)
            store.link(source_id=vendor, target_id=contract, relationship="derived_from", confidence=0.90)

            out = root_cause_trace(Path(td), [outcome], query="Why did COGS spike?", max_depth=4, max_paths=8)

        self.assertEqual("core_memory.root_cause_attribution.v1", out["schema_version"])
        self.assertTrue(out["causal_paths"])
        paths = out["causal_paths"]
        self.assertTrue(any(p["nodes"][:3] == [outcome, vendor, contract] for p in paths))
        self.assertEqual(contract, paths[-1]["terminal_cause_bead_id"])
        cause_ids = {c["bead_id"] for c in out["root_causes"]}
        self.assertIn(vendor, cause_ids)

    def test_convergent_root_accumulates_influence(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            outcome = store.add_bead(type="outcome", title="Support backlog", summary=["Backlog increased."], observed_at="2026-06-05T00:00:00Z")
            a = store.add_bead(type="state_assertion", title="Long ticket times", summary=["Tickets waited longer."], observed_at="2026-06-04T00:00:00Z")
            b = store.add_bead(type="state_assertion", title="Low staffing coverage", summary=["Coverage was low."], observed_at="2026-06-04T00:00:00Z")
            root = store.add_bead(type="decision", title="Support budget reduction", summary=["Budget was reduced."], observed_at="2026-06-01T00:00:00Z")
            store.link(source_id=a, target_id=outcome, relationship="causes", confidence=0.9)
            store.link(source_id=b, target_id=outcome, relationship="causes", confidence=0.9)
            store.link(source_id=root, target_id=a, relationship="causes", confidence=0.9)
            store.link(source_id=root, target_id=b, relationship="causes", confidence=0.9)

            out = root_cause_trace(Path(td), [outcome], query="What caused the support backlog?", max_depth=4, max_paths=10)

        ranked = out["root_causes"]
        self.assertTrue(ranked)
        self.assertEqual(root, ranked[0]["bead_id"])
        self.assertGreaterEqual(ranked[0]["path_count"], 2)

    def test_legacy_caused_by_edge_keeps_cause_direction(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            outcome = store.add_bead(type="outcome", title="COGS spike", summary=["COGS increased."], observed_at="2026-05-04T00:00:00Z")
            vendor = store.add_bead(type="state_assertion", title="Vendor increase", summary=["Vendor prices increased."], observed_at="2026-05-03T00:00:00Z")
            idx_path = store.beads_dir / "index.json"
            idx = store._read_json(idx_path)
            idx.setdefault("associations", []).append(
                {
                    "id": "legacy-caused-by",
                    "source_bead": outcome,
                    "target_bead": vendor,
                    "relationship": "caused_by",
                    "confidence": 0.95,
                    "status": "active",
                }
            )
            store._write_json(idx_path, idx)

            out = root_cause_trace(Path(td), [outcome], query="Why did COGS spike?", max_depth=2, max_paths=5)

        cause_ids = {c["bead_id"] for c in out["root_causes"]}
        self.assertIn(vendor, cause_ids)
        self.assertTrue(any(path["nodes"][:2] == [outcome, vendor] for path in out["causal_paths"]))

    def test_semantic_cold_hop_is_penalized_not_pruned(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            outcome = store.add_bead(type="outcome", title="Enterprise churn increased", summary=["Enterprise churn increased."], observed_at="2026-06-05T00:00:00Z")
            cause = store.add_bead(type="decision", title="Billing system migration", summary=["Invoice routing changed."], observed_at="2026-06-01T00:00:00Z")
            store.link(source_id=cause, target_id=outcome, relationship="causes", confidence=0.99)

            out = root_cause_trace(Path(td), [outcome], query="Why did enterprise churn increase?", max_depth=2, max_paths=5)

        self.assertTrue(out["causal_paths"])
        path = out["causal_paths"][0]
        self.assertEqual(cause, path["terminal_cause_bead_id"])
        self.assertGreaterEqual(path["semantic_cold_hop_count"], 0)
        self.assertIn("semantic_mismatch_penalty", path["edges"][0]["cost_breakdown"])


if __name__ == "__main__":
    unittest.main()
