import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.memory import confirm_bead
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.visible_corpus import build_visible_corpus
from core_memory.runtime.ingest.external_evidence import (
    ingest_document_reference,
    ingest_structured_observation,
)
from core_memory.schema.models import Authority, Bead, ConfidenceClass, Grounding
from core_memory.schema.normalization import (
    ASSERTION_KINDS,
    EXTERNAL_BEAD_TYPES,
    GROUNDING_LEVELS,
    confidence_class_rank,
    derive_confidence_class,
    derive_grounding,
    normalize_assertion_kind,
    normalize_confidence_class,
    normalize_grounding,
    resolve_confidence_class,
    resolve_grounding,
)


def _document_payload(**overrides):
    payload = {
        "data_type_flag": "document.media",
        "title": "Acme Vendor Contract",
        "summary": ["Vendor contract uploaded for Acme Corp."],
        "source_id": "src_legal_uploads",
        "source_event_id": "evt_document_001",
        "source_system": "upload",
        "source_kind": "document",
        "document_id": "doc_001",
        "document_name": "Acme Vendor Contract.pdf",
        "mime_type": "application/pdf",
        "core_memory_unifying_id": "acme_vendor_contract",
        "hydration_ref": {"store": "ragie", "ref": "ragie_doc_001"},
    }
    payload.update(overrides)
    return payload


def _structured_payload(**overrides):
    payload = {
        "data_type_flag": "relational",
        "title": "COGS increased 38% over baseline",
        "summary": ["COGS was $12,450, 38% above baseline."],
        "source_id": "src_quickbooks",
        "source_event_id": "evt_structured_001",
        "source_system": "quickbooks",
        "source_table": "qbo_expenses",
        "source_record_id": "QB-98231",
        "metric_name": "COGS",
        "metric_value": 12450,
        "entities": ["Fresh Produce LLC"],
        "entity_refs": ["Fresh Produce LLC"],
        "attribute_tags": ["cogs"],
        "as_of_timestamp": "2026-05-04T15:00:00Z",
        "core_memory_unifying_id": "cogs_spike",
        "hydration_ref": {"store": "supabase", "ref": "qbo_expenses:QB-98231"},
    }
    payload.update(overrides)
    return payload


class TestExternalVersionSupersession(unittest.TestCase):
    def test_adjusted_document_creates_new_version_and_supersedes_old(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_document_reference(td, _document_payload(), session_id="external")
            second = ingest_document_reference(
                td,
                _document_payload(
                    source_event_id="evt_document_002",
                    title="Acme Vendor Contract (amended)",
                    summary=["Amended contract: termination clause updated."],
                ),
                session_id="external",
            )
            self.assertEqual("version_superseded", second["status"])
            self.assertEqual(first["bead_id"], second["superseded_bead_id"])
            self.assertNotEqual(first["bead_id"], second["bead_id"])

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            old = idx["beads"][first["bead_id"]]
            new = idx["beads"][second["bead_id"]]
            self.assertEqual("superseded", old["status"])
            self.assertIn(second["bead_id"], old["superseded_by"])
            self.assertTrue(old.get("effective_to"))
            self.assertIn(first["bead_id"], new["supersedes"])
            rels = [
                a for a in idx.get("associations", [])
                if a.get("relationship") == "supersedes"
                and a.get("source_bead") == second["bead_id"]
                and a.get("target_bead") == first["bead_id"]
            ]
            self.assertEqual(1, len(rels))

    def test_updated_record_supersedes_prior_observation(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_structured_observation(td, _structured_payload(), session_id="external")
            second = ingest_structured_observation(
                td,
                _structured_payload(
                    source_event_id="evt_structured_002",
                    title="COGS revised to 41% over baseline",
                    metric_value=12990,
                ),
                session_id="external",
            )
            self.assertEqual("version_superseded", second["status"])
            self.assertEqual(first["bead_id"], second["superseded_bead_id"])

    def test_same_event_redelivery_stays_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_document_reference(td, _document_payload(), session_id="external")
            replay = ingest_document_reference(td, _document_payload(), session_id="external")
            self.assertEqual("already_exists", replay["status"])
            self.assertEqual(first["bead_id"], replay["bead_id"])

    def test_old_event_redelivery_after_versioning_stays_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_document_reference(td, _document_payload(), session_id="external")
            ingest_document_reference(
                td,
                _document_payload(source_event_id="evt_document_002", title="Acme Vendor Contract v2"),
                session_id="external",
            )
            replay = ingest_document_reference(td, _document_payload(), session_id="external")
            self.assertEqual("already_exists", replay["status"])
            self.assertEqual(first["bead_id"], replay["bead_id"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(idx["beads"]))

    def test_new_event_with_identical_content_does_not_version(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_document_reference(td, _document_payload(), session_id="external")
            same_content = ingest_document_reference(
                td, _document_payload(source_event_id="evt_document_002"), session_id="external"
            )
            self.assertEqual("already_exists", same_content["status"])
            self.assertEqual(first["bead_id"], same_content["bead_id"])

    def test_third_version_chains_from_current_truth(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = ingest_document_reference(td, _document_payload(), session_id="external")
            v2 = ingest_document_reference(
                td,
                _document_payload(source_event_id="evt_document_002", title="Acme Vendor Contract v2"),
                session_id="external",
            )
            v3 = ingest_document_reference(
                td,
                _document_payload(source_event_id="evt_document_003", title="Acme Vendor Contract v3"),
                session_id="external",
            )
            self.assertEqual(v2["bead_id"], v3["superseded_bead_id"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("superseded", idx["beads"][v1["bead_id"]]["status"])
            self.assertEqual("superseded", idx["beads"][v2["bead_id"]]["status"])
            self.assertNotEqual("superseded", idx["beads"][v3["bead_id"]]["status"])


class TestRebuildIndexAfterLifecycleOps(unittest.TestCase):
    def test_rebuild_preserves_full_record_for_superseded_bead(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = ingest_document_reference(td, _document_payload(), session_id="external")
            ingest_document_reference(
                td,
                _document_payload(source_event_id="evt_document_002", title="Acme Vendor Contract v2"),
                session_id="external",
            )
            store = MemoryStore(root=td)
            store.rebuild_index()
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            old = idx["beads"][v1["bead_id"]]
            # Lifecycle state survived the rebuild...
            self.assertEqual("superseded", old["status"])
            # ...and so did the full original record (P1: partial session lines
            # must never replace the bead on rebuild).
            self.assertEqual("document_reference", old["type"])
            self.assertEqual("Acme Vendor Contract", old["title"])
            self.assertEqual("doc_001", old["document_id"])
            self.assertEqual("src_legal_uploads", old["source_id"])

    def test_rebuild_preserves_full_record_for_confirmed_bead(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(
                type="decision", title="Choose vendor", summary=["s"], because=["b"],
                detail="d", session_id="s1",
            )
            confirm_bead(td, bid)
            store.rebuild_index()
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][bid]
            self.assertEqual("user_confirmed", bead["authority"])
            self.assertEqual("A", bead["confidence_class"])
            self.assertEqual("decision", bead["type"])
            self.assertEqual("Choose vendor", bead["title"])


class TestCurrentTruthGuard(unittest.TestCase):
    def test_superseded_versions_are_excluded_from_visible_corpus(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = ingest_document_reference(td, _document_payload(), session_id="external")
            v2 = ingest_document_reference(
                td,
                _document_payload(source_event_id="evt_document_002", title="Acme Vendor Contract v2"),
                session_id="external",
            )
            ids = {r["bead_id"] for r in build_visible_corpus(td)}
            self.assertNotIn(v1["bead_id"], ids)
            self.assertIn(v2["bead_id"], ids)

    def test_provenance_callers_can_opt_in_to_superseded(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = ingest_document_reference(td, _document_payload(), session_id="external")
            v2 = ingest_document_reference(
                td,
                _document_payload(source_event_id="evt_document_002", title="Acme Vendor Contract v2"),
                session_id="external",
            )
            ids = {r["bead_id"] for r in build_visible_corpus(td, include_superseded=True)}
            self.assertIn(v1["bead_id"], ids)
            self.assertIn(v2["bead_id"], ids)


class TestConfidenceClass(unittest.TestCase):
    def test_normalization_and_aliases(self):
        self.assertEqual("C", normalize_confidence_class(None))
        self.assertEqual("A", normalize_confidence_class("a"))
        self.assertEqual("B", normalize_confidence_class("reinforced"))
        self.assertEqual("A", normalize_confidence_class("canonical"))
        self.assertEqual("C", normalize_confidence_class("nonsense"))
        self.assertTrue(confidence_class_rank("A") > confidence_class_rank("B") > confidence_class_rank("C"))

    def test_derivation_floor(self):
        self.assertEqual("C", derive_confidence_class({}))
        self.assertEqual("B", derive_confidence_class({"recall_count": 3}))
        self.assertEqual("B", derive_confidence_class({"promotion_candidate": True}))
        self.assertEqual("A", derive_confidence_class({"promoted": True}))
        self.assertEqual("A", derive_confidence_class({"authority": "user_confirmed"}))

    def test_bead_from_dict_applies_floor(self):
        bead = Bead.from_dict({"id": "b1", "type": "decision", "title": "t", "recall_count": 2})
        self.assertEqual("B", bead.confidence_class)
        confirmed = Bead.from_dict({"id": "b2", "type": "decision", "title": "t", "authority": "user_confirmed"})
        self.assertEqual("A", confirmed.confidence_class)
        explicit = Bead.from_dict({"id": "b3", "type": "decision", "title": "t", "confidence_class": "A"})
        self.assertEqual("A", explicit.confidence_class)

    def test_new_beads_start_as_captured(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="decision", title="Choose vendor", summary=["s"], because=["b"], detail="d")
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("C", idx["beads"][bid]["confidence_class"])

    def test_recall_raises_class_to_reinforced(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="decision", title="Choose vendor", summary=["s"], because=["b"], detail="d")
            store.recall(bid)
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("B", idx["beads"][bid]["confidence_class"])

    def test_confirm_bead_surface_records_user_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="decision", title="Choose vendor", summary=["s"], because=["b"], detail="d")
            out = confirm_bead(td, bid, note="verified with vendor")
            self.assertTrue(out["ok"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][bid]
            self.assertEqual("user_confirmed", bead["authority"])
            self.assertEqual("A", bead["confidence_class"])

    def test_confirm_bead_missing_id_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(root=td).add_bead(type="context", title="x", summary=["s"])
            out = confirm_bead(td, "bead-DOES-NOT-EXIST")
            self.assertFalse(out["ok"])
            self.assertEqual("bead_not_found", out["error"])

    def test_confirmation_never_lowers_class(self):
        self.assertEqual(ConfidenceClass.CANONICAL.value, "A")
        self.assertEqual(Authority.SOURCE_ATTRIBUTED.value, "source_attributed")
        self.assertEqual(Authority.DERIVED_ANALYSIS.value, "derived_analysis")


class TestGroundingGatesConfidenceClass(unittest.TestCase):
    def test_grounding_vocabulary_and_normalization(self):
        self.assertEqual(GROUNDING_LEVELS, {"observed", "extracted", "inferred", "speculative"})
        self.assertEqual("observed", normalize_grounding("primary_source"))
        self.assertEqual("inferred", normalize_grounding("derived_analysis"))
        self.assertEqual("speculative", normalize_grounding("untested"))
        self.assertEqual("inferred", normalize_grounding("nonsense"))  # default
        self.assertEqual(Grounding.OBSERVED.value, "observed")

    def test_grounding_derived_from_type(self):
        self.assertEqual("observed", derive_grounding({"type": "structured_observation"}))
        self.assertEqual("observed", derive_grounding({"type": "operational_event"}))
        self.assertEqual("observed", derive_grounding({"type": "evidence"}))
        self.assertEqual("inferred", derive_grounding({"type": "decision"}))
        self.assertEqual("inferred", derive_grounding({"type": "state_assertion"}))
        self.assertEqual("speculative", derive_grounding({"type": "hypothesis"}))
        # A validated hypothesis is no longer speculative.
        self.assertEqual("inferred", derive_grounding({"type": "hypothesis", "hypothesis_status": "validated"}))

    def test_explicit_grounding_overrides_type_default(self):
        self.assertEqual("extracted", resolve_grounding({"type": "decision", "grounding": "extracted"}))

    def test_observed_enters_at_b_floor(self):
        # Source-supported from birth: observed/extracted enter at B.
        self.assertEqual("B", derive_confidence_class({"type": "structured_observation"}))
        self.assertEqual("B", derive_confidence_class({"type": "decision", "grounding": "extracted"}))
        # Inferred without reinforcement stays C.
        self.assertEqual("C", derive_confidence_class({"type": "decision"}))

    def test_speculative_capped_at_b(self):
        # Even promoted, a still-speculative bead cannot be canonical.
        self.assertEqual("B", resolve_confidence_class({"type": "hypothesis", "promoted": True}))
        self.assertEqual("B", resolve_confidence_class({"type": "hypothesis", "confidence_class": "A"}))
        # Pending hypothesis with no reinforcement is C.
        self.assertEqual("C", derive_confidence_class({"type": "hypothesis"}))

    def test_validated_hypothesis_can_reach_a(self):
        bead = {"type": "hypothesis", "hypothesis_status": "validated", "promoted": True}
        self.assertEqual("A", resolve_confidence_class(bead))

    def test_structured_observation_bead_starts_at_b(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = ingest_structured_observation(td, _structured_payload(), session_id="external")
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][receipt["bead_id"]]
            self.assertEqual("observed", bead["grounding"])
            self.assertEqual("B", bead["confidence_class"])

    def test_confirming_speculative_bead_lifts_grounding(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(
                type="hypothesis", title="Sync worker drops under load",
                summary=["s"], hypothesis_status="pending", session_id="s1",
            )
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("speculative", idx["beads"][bid]["grounding"])
            self.assertEqual("C", idx["beads"][bid]["confidence_class"])
            confirm_bead(td, bid)
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][bid]
            self.assertEqual("inferred", bead["grounding"])  # no longer speculative
            self.assertEqual("A", bead["confidence_class"])


class TestLifecycleSnapshotDurability(unittest.TestCase):
    """Recall/promote class changes must survive the session-overlay merge and
    index rebuild, and promotion must respect the speculative ceiling."""

    def _read(self, td, bid):
        idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
        return idx["beads"][bid]

    def test_recall_class_survives_corpus_overlay_and_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="decision", title="Pick vendor", summary=["s"],
                                 because=["b"], detail="d", session_id="s1")
            store.recall(bid)
            # Visible corpus (which overlays session-*.jsonl) must reflect B.
            row = next(r for r in build_visible_corpus(td) if r["bead_id"] == bid)
            self.assertEqual("B", row["bead"]["confidence_class"])
            self.assertEqual(1, row["bead"]["recall_count"])
            # ...and so must a rebuilt index.
            store.rebuild_index()
            bead = self._read(td, bid)
            self.assertEqual("B", bead["confidence_class"])
            self.assertEqual(1, bead["recall_count"])

    def test_promoting_speculative_bead_is_capped_at_b(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="hypothesis", title="Cache stampede under load",
                                 summary=["s"], hypothesis_status="pending", session_id="s1")
            store.promote(bid)
            bead = self._read(td, bid)
            # Promoted in lifecycle, but still speculative → capped at B, not A.
            self.assertEqual("speculative", bead["grounding"])
            self.assertEqual("B", bead["confidence_class"])
            # Survives rebuild (not silently rewritten to A or back to C).
            store.rebuild_index()
            self.assertEqual("B", self._read(td, bid)["confidence_class"])

    def test_promoting_validated_hypothesis_reaches_a(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="hypothesis", title="Retceil fixes stampede",
                                 summary=["s"], hypothesis_status="validated", session_id="s1")
            store.promote(bid)
            self.assertEqual("A", self._read(td, bid)["confidence_class"])

    def test_promoting_grounded_bead_reaches_a(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="decision", title="Adopt NetSuite", summary=["s"],
                                 because=["cost"], detail="d", session_id="s1")
            store.promote(bid)
            self.assertEqual("A", self._read(td, bid)["confidence_class"])


class TestVocabularyAdditions(unittest.TestCase):
    def test_assertion_kind_vocabulary(self):
        self.assertIn("business_state", ASSERTION_KINDS)
        self.assertEqual("business_state", normalize_assertion_kind(""))
        self.assertEqual("business_state", normalize_assertion_kind("derived_business_state"))
        self.assertEqual("document_claim", normalize_assertion_kind("document_observation"))
        # unknown non-empty values are preserved, not destroyed
        self.assertEqual("vendor_specific_kind", normalize_assertion_kind("vendor_specific_kind"))

    def test_external_flag_vocabulary_lives_in_schema(self):
        self.assertIn("document_reference", EXTERNAL_BEAD_TYPES)

    def test_external_types_have_explicit_promotion_policy(self):
        from core_memory.policy.promotion import BEAD_TYPE_PRIORS, TYPE_DURABILITY_MULTIPLIERS
        for bt in ("transcript", "document_reference", "structured_observation", "state_assertion"):
            self.assertIn(bt, BEAD_TYPE_PRIORS)
            self.assertIn(bt, TYPE_DURABILITY_MULTIPLIERS)


if __name__ == "__main__":
    unittest.main()
