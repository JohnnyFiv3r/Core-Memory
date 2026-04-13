import tempfile
import unittest

from core_memory.entity.merge_flow import (
    decide_entity_merge_proposal,
    list_entity_merge_proposals,
    suggest_entity_merge_proposals,
)
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_flush


class TestEntityMergeFlow(unittest.TestCase):
    def test_suggest_on_empty_root_preserves_index_stats_for_later_flush(self):
        with tempfile.TemporaryDirectory() as td:
            out = suggest_entity_merge_proposals(td, min_score=0.9, max_pairs=10)
            self.assertTrue(out.get("ok"))

            s = MemoryStore(td)
            idx = s._read_json(s.beads_dir / "index.json")
            self.assertTrue(isinstance(idx.get("stats"), dict))
            self.assertIn("total_beads", idx.get("stats") or {})

            flush = process_flush(
                root=td,
                session_id="s-empty",
                source="test",
                promote=True,
                token_budget=1200,
                max_beads=12,
            )
            self.assertTrue(flush.get("ok"))

    def test_suggest_proposals_emits_pending_alias_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(
                type="context",
                title="Org 1",
                summary=["OpenAI update"],
                entities=["OpenAI"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            _ = b1
            b2 = s.add_bead(
                type="context",
                title="Org 2",
                summary=["Open AI note"],
                entities=["Open AI"],
                session_id="main",
                source_turn_ids=["t2"],
            )
            _ = b2

            idx = s._read_json(s.beads_dir / "index.json")
            entities = idx.get("entities") or {}
            # Force split entities to simulate unresolved duplicates for review flow.
            if len(entities) == 1:
                only_id, row = next(iter(entities.items()))
                row2 = dict(row)
                row2["id"] = "entity-manual-2"
                row2["label"] = "Open AI"
                row2["normalized_label"] = "openai"
                row2["aliases"] = ["openai"]
                entities["entity-manual-2"] = row2
                idx["entities"] = entities
                # keep alias map split across separate aliases if possible
                amap = idx.get("entity_aliases") or {}
                amap["openai"] = only_id
                amap["openaiorg"] = "entity-manual-2"
                idx["entity_aliases"] = amap
                s._write_json(s.beads_dir / "index.json", idx)

            out = suggest_entity_merge_proposals(td, min_score=0.70, max_pairs=20)
            self.assertTrue(out.get("ok"))
            self.assertGreaterEqual(int(out.get("pending") or 0), 1)
            rows = list_entity_merge_proposals(td, status="pending", limit=10)
            self.assertGreaterEqual(len(rows), 1)

    def test_accept_merge_updates_entity_ids_and_registry(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid = s.add_bead(
                type="context",
                title="Entity refs",
                summary=["references"],
                entities=["OpenAI", "Open AI"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            idx = s._read_json(s.beads_dir / "index.json")
            entities = idx.get("entities") or {}
            if len(entities) < 2:
                # synthesize split entity to exercise merge apply
                first_id, first = next(iter(entities.items()))
                entities["entity-extra"] = {
                    **dict(first),
                    "id": "entity-extra",
                    "label": "Open AI",
                    "normalized_label": "openai",
                    "aliases": ["openai"],
                    "status": "active",
                }
                bead = (idx.get("beads") or {}).get(bid) or {}
                bead["entity_ids"] = [first_id, "entity-extra"]
                idx["beads"][bid] = bead
                idx["entities"] = entities
                idx.setdefault("entity_aliases", {})["openai_alias_alt"] = "entity-extra"
                s._write_json(s.beads_dir / "index.json", idx)

            sug = suggest_entity_merge_proposals(td, min_score=0.70, max_pairs=20)
            self.assertTrue(sug.get("ok"))
            pending = list_entity_merge_proposals(td, status="pending", limit=10)
            self.assertTrue(pending)

            pid = str(pending[0].get("id") or "")
            left = str(pending[0].get("left_entity_id") or "")
            right = str(pending[0].get("right_entity_id") or "")
            dec = decide_entity_merge_proposal(
                td,
                proposal_id=pid,
                decision="accept",
                reviewer="qa",
                notes="looks like same org",
                apply=True,
                keep_entity_id=left,
            )
            self.assertTrue(dec.get("ok"))
            self.assertEqual("accepted", dec.get("status"))
            self.assertTrue((dec.get("applied") or {}).get("ok"))

            idx2 = s._read_json(s.beads_dir / "index.json")
            row_l = (idx2.get("entities") or {}).get(left) or {}
            row_r = (idx2.get("entities") or {}).get(right) or {}
            self.assertEqual("active", str(row_l.get("status") or "active"))
            self.assertEqual("merged", str(row_r.get("status") or ""))
            self.assertEqual(left, str(row_r.get("merged_into") or ""))

            bead = (idx2.get("beads") or {}).get(bid) or {}
            ids = list(bead.get("entity_ids") or [])
            self.assertIn(left, ids)
            self.assertNotIn(right, ids)

    def test_reject_proposal_leaves_registry_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="A", summary=["A"], entities=["Foo Org"], session_id="main", source_turn_ids=["t1"])
            s.add_bead(type="context", title="B", summary=["B"], entities=["Foo-Org"], session_id="main", source_turn_ids=["t2"])
            suggest_entity_merge_proposals(td, min_score=0.70, max_pairs=20)
            pending = list_entity_merge_proposals(td, status="pending", limit=10)
            if not pending:
                # force split entity pair to guarantee rejection-path exercise
                idx = s._read_json(s.beads_dir / "index.json")
                entities = idx.get("entities") or {}
                first_id, first = next(iter(entities.items()))
                entities["entity-reject-2"] = {
                    **dict(first),
                    "id": "entity-reject-2",
                    "label": "Foo Organization",
                    "normalized_label": "fooorg",
                    "aliases": ["fooorg"],
                    "status": "active",
                }
                idx["entities"] = entities
                s._write_json(s.beads_dir / "index.json", idx)
                suggest_entity_merge_proposals(td, min_score=0.20, max_pairs=20)
                pending = list_entity_merge_proposals(td, status="pending", limit=10)
            self.assertTrue(pending)
            pid = str(pending[0].get("id") or "")

            idx_before = s._read_json(s.beads_dir / "index.json")
            dec = decide_entity_merge_proposal(td, proposal_id=pid, decision="reject", reviewer="qa", notes="not enough confidence")
            self.assertTrue(dec.get("ok"))
            self.assertEqual("rejected", dec.get("status"))
            idx_after = s._read_json(s.beads_dir / "index.json")
            self.assertEqual(idx_before.get("entities"), idx_after.get("entities"))


if __name__ == "__main__":
    unittest.main()
