import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _read_candidates
from core_memory.runtime.dreamer.identity_value_research import (
    detect_identity_value_findings,
    enqueue_identity_value_candidates,
)
from core_memory.soul.store import propose_soul_update


def _decision(store, title, topics, session):
    return store.add_bead(type="decision", title=title, summary=["s"], because=["x"],
                          detail="d", topics=topics, session_id=session)


def _endorse_identity(root, key, content, subject="self"):
    return propose_soul_update(
        root, target_file="IDENTITY.md", entry_key=key, content=content,
        source="agent", epistemic_status="endorsed", subject=subject,
        requires_approval=False,
    )


class TestValueCandidateDetection(unittest.TestCase):
    def test_emergent_value_from_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _decision(store, "Chose simplest path", ["simplicity"], "s1")
            _decision(store, "Removed a feature", ["simplicity"], "s2")
            _decision(store, "Cut scope again", ["simplicity"], "s3")
            _decision(store, "Declined complexity", ["simplicity"], "s4")
            dets = detect_identity_value_findings(td)
            values = [d for d in dets if d["finding"] == "value_candidate"]
            self.assertEqual(1, len(values))
            self.assertEqual("simplicity", values[0]["value_theme"])
            self.assertEqual(4, values[0]["occurrence_count"])
            self.assertEqual(4, values[0]["session_count"])

    def test_value_already_in_identity_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _endorse_identity(td, "Simplicity", "I consistently favor simplicity.")
            for i, s in enumerate(["s1", "s2", "s3", "s4"]):
                _decision(store, f"d{i}", ["simplicity"], s)
            dets = detect_identity_value_findings(td)
            self.assertEqual([], [d for d in dets if d["finding"] == "value_candidate"])

    def test_single_session_loop_not_a_value(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            for i in range(6):
                _decision(store, f"d{i}", ["simplicity"], "s1")
            self.assertEqual([], [d for d in detect_identity_value_findings(td)
                                  if d["finding"] == "value_candidate"])

    def test_phrase_and_short_token_acknowledgment_matches(self):
        # An endorsed IDENTITY entry phrased in free text ("cache invalidation",
        # "API") must suppress value candidates for the same behavior themes —
        # both sides tokenize the same way (regression for the phrase/length-3
        # mismatch).
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _endorse_identity(td, "Engineering",
                              "I value cache invalidation discipline and clean API design.")
            for i, s in enumerate(["s1", "s2", "s3", "s4"]):
                store.add_bead(type="decision", title=f"d{i}", summary=["s"], because=["x"],
                               detail="d", topics=["cache invalidation", "api"], session_id=s)
            values = [d for d in detect_identity_value_findings(td) if d["finding"] == "value_candidate"]
            themes = {v["value_theme"] for v in values}
            self.assertNotIn("cache", themes)
            self.assertNotIn("invalidation", themes)
            self.assertNotIn("api", themes)

    def test_below_threshold_not_a_value(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _decision(store, "d1", ["simplicity"], "s1")
            _decision(store, "d2", ["simplicity"], "s2")
            _decision(store, "d3", ["simplicity"], "s3")  # 3 < default 4 occurrences
            self.assertEqual([], [d for d in detect_identity_value_findings(td)
                                  if d["finding"] == "value_candidate"])


class TestIdentityDivergenceDetection(unittest.TestCase):
    def test_endorsed_value_without_behavior_diverges(self):
        with tempfile.TemporaryDirectory() as td:
            _endorse_identity(td, "Craftsmanship", "I value careful craftsmanship above speed.")
            dets = detect_identity_value_findings(td)
            div = [d for d in dets if d["finding"] == "identity_divergence_candidate"]
            self.assertEqual(1, len(div))
            self.assertEqual("Craftsmanship", div[0]["identity_entry_key"])

    def test_endorsed_value_with_behavior_does_not_diverge(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _endorse_identity(td, "Craftsmanship", "I value careful craftsmanship.")
            _decision(store, "Polished the API", ["craftsmanship"], "s1")
            dets = detect_identity_value_findings(td)
            self.assertEqual([], [d for d in dets if d["finding"] == "identity_divergence_candidate"])

    def test_inferred_identity_entry_is_not_divergence_checked(self):
        # Only endorsed self-statements are drift-checked; inferred ones are not.
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="IDENTITY.md", entry_key="Maybe",
                                content="possibly values novelty", source="dreamer",
                                epistemic_status="inferred", requires_approval=False)
            dets = detect_identity_value_findings(td)
            self.assertEqual([], [d for d in dets if d["finding"] == "identity_divergence_candidate"])


class TestEnqueueIdentityValue(unittest.TestCase):
    def test_enqueue_and_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            for i, s in enumerate(["s1", "s2", "s3", "s4"]):
                _decision(store, f"d{i}", ["simplicity"], s)
            _endorse_identity(td, "Craftsmanship", "careful craftsmanship matters")
            out = enqueue_identity_value_candidates(td)
            self.assertTrue(out["ok"])
            self.assertEqual(2, out["enqueued"])
            kinds = {c["hypothesis_type"] for c in _read_candidates(td)}
            self.assertEqual({"value_candidate", "identity_divergence_candidate"}, kinds)
            # Idempotent: a second run enqueues nothing new.
            again = enqueue_identity_value_candidates(td)
            self.assertEqual(0, again["enqueued"])
            self.assertEqual(2, len(_read_candidates(td)))

    def test_empty_corpus(self):
        with tempfile.TemporaryDirectory() as td:
            out = enqueue_identity_value_candidates(td)
            self.assertEqual(0, out["enqueued"])


if __name__ == "__main__":
    unittest.main()
