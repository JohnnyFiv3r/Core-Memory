import tempfile
import unittest
import os
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.runtime.dreamer_candidates import decide_dreamer_candidate, enqueue_dreamer_candidates, list_dreamer_candidates


class TestDreamerCandidateFamiliesDV2(unittest.TestCase):
    def test_enqueue_emits_contradiction_and_retrieval_value_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            src = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            tgt = s.add_bead(type="decision", title="B", summary=["b"], session_id="main", source_turn_ids=["t1"])

            out = enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": src,
                        "target": tgt,
                        "relationship": "contradicts",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.85,
                        "rationale": "conflicting evidence",
                    }
                ],
                run_metadata={"run_id": "dv2-1", "mode": "suggest", "source": "unit_test"},
            )
            self.assertTrue(out.get("ok"))
            self.assertGreaterEqual(int(out.get("added") or 0), 2)

            rows = (list_dreamer_candidates(root=td, status="pending", limit=20).get("results") or [])
            htypes = {str(r.get("hypothesis_type") or "") for r in rows}
            self.assertIn("contradiction_candidate", htypes)
            self.assertIn("retrieval_value_candidate", htypes)

    def test_enqueue_emits_entity_merge_candidate_when_entity_ids_diverge(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            src = s.add_bead(
                type="context",
                title="OpenAI note",
                summary=["OpenAI reference"],
                entities=["OpenAI"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            tgt = s.add_bead(
                type="context",
                title="Open AI note",
                summary=["Open AI reference"],
                entities=["Open AI"],
                session_id="main",
                source_turn_ids=["t2"],
            )

            # Force split entity ids to exercise merge-candidate generation.
            idx = s._read_json(s.beads_dir / "index.json")
            entities = idx.get("entities") or {}
            first_id, first_row = next(iter(entities.items()))
            second_id = "entity-dv2-split"
            entities[second_id] = {
                **dict(first_row),
                "id": second_id,
                "label": "Open AI",
                "normalized_label": "openai",
                "aliases": ["openai"],
                "status": "active",
            }
            idx["entities"] = entities
            bead_tgt = (idx.get("beads") or {}).get(tgt) or {}
            bead_tgt["entity_ids"] = [second_id]
            idx["beads"][tgt] = bead_tgt
            idx.setdefault("entity_aliases", {})["openai"] = first_id
            idx["entity_aliases"]["openai_alias_split"] = second_id
            s._write_json(s.beads_dir / "index.json", idx)

            out = enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": src,
                        "target": tgt,
                        "relationship": "similar_pattern",
                        "novelty": 0.7,
                        "grounding": 0.8,
                        "confidence": 0.8,
                    }
                ],
                run_metadata={"run_id": "dv2-entity", "mode": "suggest", "source": "unit_test"},
            )
            self.assertTrue(out.get("ok"))

            rows = (list_dreamer_candidates(root=td, status="pending", limit=30).get("results") or [])
            merges = [r for r in rows if str(r.get("hypothesis_type") or "") == "entity_merge_candidate"]
            self.assertTrue(merges)
            m = merges[0]
            self.assertTrue(str(m.get("source_entity_id") or ""))
            self.assertTrue(str(m.get("target_entity_id") or ""))
            self.assertIn("entity_coreference", list(m.get("benchmark_tags") or []))

    def test_decide_retrieval_value_candidate_records_without_association_write(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            src = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            tgt = s.add_bead(type="decision", title="B", summary=["b"], session_id="main", source_turn_ids=["t1"])
            enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": src,
                        "target": tgt,
                        "relationship": "supports",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.9,
                    }
                ],
                run_metadata={"run_id": "dv2-rv", "mode": "suggest", "source": "unit_test"},
            )
            rows = (list_dreamer_candidates(root=td, status="pending", limit=20).get("results") or [])
            rv = next(r for r in rows if str(r.get("hypothesis_type") or "") == "retrieval_value_candidate")

            before = s._read_json(s.beads_dir / "index.json")
            before_n = len(list(before.get("associations") or []))

            out = decide_dreamer_candidate(
                root=td,
                candidate_id=str(rv.get("id") or ""),
                decision="accept",
                reviewer="qa",
                notes="record only",
                apply=True,
            )
            self.assertTrue(out.get("ok"))
            applied = dict(out.get("applied") or {})
            self.assertEqual("retrieval_value_override_apply", applied.get("application_mode"))
            self.assertTrue(bool((applied.get("result") or {}).get("ok")))

            after = s._read_json(s.beads_dir / "index.json")
            after_n = len(list(after.get("associations") or []))
            self.assertEqual(before_n, after_n)
            overrides = dict(after.get("retrieval_value_overrides") or {})
            self.assertTrue(overrides)

            with patch.dict(
                os.environ,
                {
                    "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
                    "CORE_MEMORY_CLAIM_LAYER": "1",
                    "CORE_MEMORY_CLAIM_RESOLUTION": "1",
                    "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST": "1",
                },
                clear=False,
            ):
                res = memory_tools.execute(
                    {
                        "raw_query": "why A supports B",
                        "intent": "causal",
                        "grounding_mode": "search_only",
                        "constraints": {"require_structural": False},
                        "k": 5,
                    },
                    root=td,
                    explain=True,
                )
            self.assertTrue(res.get("ok"))
            first = (res.get("results") or [{}])[0]
            fs = dict(first.get("feature_scores") or {})
            self.assertGreaterEqual(float(fs.get("retrieval_value_bonus") or 0.0), 0.0)

    def test_reject_candidate_keeps_status_and_no_apply(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            src = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            tgt = s.add_bead(type="decision", title="B", summary=["b"], session_id="main", source_turn_ids=["t1"])
            enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": src,
                        "target": tgt,
                        "relationship": "supports",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.9,
                    }
                ],
                run_metadata={"run_id": "dv2-rj", "mode": "suggest", "source": "unit_test"},
            )
            rows = (list_dreamer_candidates(root=td, status="pending", limit=20).get("results") or [])
            cand = rows[0]
            out = decide_dreamer_candidate(
                root=td,
                candidate_id=str(cand.get("id") or ""),
                decision="reject",
                reviewer="qa",
                notes="not convinced",
                apply=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("rejected", out.get("status"))
            self.assertIsNone(out.get("applied"))

    def test_decide_entity_merge_candidate_applies_reviewed_merge(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            src = s.add_bead(type="context", title="Org A", summary=["x"], entities=["OpenAI"], session_id="main", source_turn_ids=["t1"])
            tgt = s.add_bead(type="context", title="Org B", summary=["y"], entities=["Open AI"], session_id="main", source_turn_ids=["t2"])

            idx = s._read_json(s.beads_dir / "index.json")
            entities = idx.get("entities") or {}
            first_id, first_row = next(iter(entities.items()))
            second_id = "entity-merge-dv2"
            entities[second_id] = {
                **dict(first_row),
                "id": second_id,
                "label": "Open AI",
                "normalized_label": "openai",
                "aliases": ["openai"],
                "status": "active",
            }
            idx["entities"] = entities
            bead_src = (idx.get("beads") or {}).get(src) or {}
            bead_tgt = (idx.get("beads") or {}).get(tgt) or {}
            bead_src["entity_ids"] = [first_id]
            bead_tgt["entity_ids"] = [second_id]
            idx["beads"][src] = bead_src
            idx["beads"][tgt] = bead_tgt
            s._write_json(s.beads_dir / "index.json", idx)

            enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": src,
                        "target": tgt,
                        "relationship": "similar_pattern",
                        "novelty": 0.7,
                        "grounding": 0.9,
                        "confidence": 0.9,
                    }
                ],
                run_metadata={"run_id": "dv2-em", "mode": "reviewed_apply", "source": "unit_test"},
            )
            rows = (list_dreamer_candidates(root=td, status="pending", limit=30).get("results") or [])
            em = next(r for r in rows if str(r.get("hypothesis_type") or "") == "entity_merge_candidate")

            out = decide_dreamer_candidate(
                root=td,
                candidate_id=str(em.get("id") or ""),
                decision="accept",
                reviewer="qa",
                notes="merge accepted",
                apply=True,
            )
            self.assertTrue(out.get("ok"))
            applied = dict(out.get("applied") or {})
            self.assertEqual("entity_merge_apply", applied.get("application_mode"))
            self.assertTrue(bool((applied.get("result") or {}).get("ok")))


if __name__ == "__main__":
    unittest.main()
