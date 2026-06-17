import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import memory_search_request
from core_memory.runtime.ingest.external_evidence import (
    ingest_document_reference,
    ingest_external_evidence,
    ingest_state_assertion,
    ingest_structured_observation,
    resolve_external_bead_type,
)
from core_memory.schema.models import Bead, BeadType
from core_memory.schema.normalization import is_allowed_bead_type


def _structured_payload(**overrides):
    payload = {
        "data_type_flag": "relational",
        "title": "COGS increased 38% over the prior 7-day baseline",
        "summary": ["COGS was $12,450 for the measured window, 38% above baseline."],
        "source_id": "src_quickbooks",
        "source_event_id": "evt_structured_001",
        "source_system": "quickbooks",
        "source_kind": "relational",
        "source_table": "qbo_expenses",
        "source_record_id": "QB-98231",
        "record_action": "summarized",
        "record_grain": "metric_period",
        "business_object_type": "expense",
        "metric_name": "COGS",
        "metric_value": 12450,
        "change_pct": 38,
        "currency": "USD",
        "entities": ["Fresh Produce LLC", "COGS"],
        "entity_refs": ["Fresh Produce LLC"],
        "attribute_tags": ["cogs", "variance", "expense"],
        "observed_at": "2026-05-04T15:00:00Z",
        "as_of_timestamp": "2026-05-04T15:00:00Z",
        "core_memory_unifying_id": "cogs_spike_2026_05_04",
        "confidence": 0.88,
        "authority": "derived_from_source_financial_records",
        "hydration_ref": {"store": "supabase", "ref": "qbo_expenses:QB-98231"},
    }
    payload.update(overrides)
    return payload


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
        "raw_source_object_id": "raw_001",
        "ragie_document_id": "ragie_doc_001",
        "document_name": "Acme Vendor Contract.pdf",
        "mime_type": "application/pdf",
        "document_kind": "contract",
        "document_date": "2026-05-20",
        "author_or_owner": "Legal",
        "entities": ["Acme Corp"],
        "topics": ["vendor contract"],
        "observed_at": "2026-05-20T00:00:00Z",
        "core_memory_unifying_id": "acme_vendor_contract_2026_05_20",
        "hydration_ref": {"store": "ragie", "ref": "ragie_doc_001"},
        "section_refs": [{"label": "termination clause", "page": 4, "chunk_ref": "ragie_chunk_001"}],
    }
    payload.update(overrides)
    return payload


def _state_assertion_payload(**overrides):
    payload = {
        "data_type_flag": "state_assertion",
        "title": "Fresh Produce LLC became the primary COGS driver",
        "summary": ["Fresh Produce LLC accounted for 61% of the COGS increase during the measured period."],
        "derived_from": ["structured_observation:cogs_spike_2026_05_04"],
        "assertion_kind": "business_state",
        "assertion_subject": "Fresh Produce LLC",
        "assertion_predicate": "became_primary_driver_of",
        "assertion_value": "COGS increase",
        "effective_from": "2026-05-04T00:00:00Z",
        "confidence": 0.82,
        "authority": "derived_analysis",
        "entities": ["Fresh Produce LLC", "COGS"],
        "topics": ["COGS"],
    }
    payload.update(overrides)
    return payload


class TestExternalEvidenceSchema(unittest.TestCase):
    def test_new_external_evidence_bead_types_are_canonical(self):
        self.assertEqual(BeadType.TRANSCRIPT.value, "transcript")
        self.assertEqual(BeadType.DOCUMENT_REFERENCE.value, "document_reference")
        self.assertEqual(BeadType.STRUCTURED_OBSERVATION.value, "structured_observation")
        self.assertEqual(BeadType.STATE_ASSERTION.value, "state_assertion")
        self.assertTrue(is_allowed_bead_type("transcript"))
        self.assertTrue(is_allowed_bead_type("document_reference"))
        self.assertTrue(is_allowed_bead_type("structured_observation"))
        self.assertTrue(is_allowed_bead_type("state_assertion"))

    def test_source_fields_survive_bead_round_trip(self):
        bead = Bead.from_dict({
            "id": "b-source",
            "type": "structured_observation",
            "title": "COGS spike",
            "summary": ["COGS increased"],
            "source_id": "src_quickbooks",
            "source_event_id": "evt_structured_001",
            "source_system": "quickbooks",
            "source_kind": "relational",
            "core_memory_unifying_id": "cogs_spike",
            "hydration_ref": {"store": "supabase", "ref": "row:1"},
            "source_table": "qbo_expenses",
            "source_record_id": "QB-98231",
            "as_of_timestamp": "2026-05-04T15:00:00Z",
            "entity_refs": ["Acme Corp"],
            "attribute_tags": ["cogs"],
        })
        out = bead.to_dict()
        self.assertEqual("src_quickbooks", out["source_id"])
        self.assertEqual({"store": "supabase", "ref": "row:1"}, out["hydration_ref"])
        self.assertEqual("qbo_expenses", out["source_table"])
        self.assertEqual(["cogs"], out["attribute_tags"])


class TestExternalEvidenceIngest(unittest.TestCase):
    def test_flag_routes_to_specific_bead_type(self):
        self.assertEqual("structured_observation", resolve_external_bead_type({"data_type_flag": "relational"}))
        self.assertEqual("document_reference", resolve_external_bead_type({"data_type_flag": "document.media"}))
        self.assertEqual("transcript", resolve_external_bead_type({"data_type_flag": "conversation.transcript"}))
        self.assertEqual("state_assertion", resolve_external_bead_type({"data_type_flag": "document_claim"}))

    def test_structured_observation_writes_typed_source_attributed_bead(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = ingest_structured_observation(td, _structured_payload(), session_id="external-source")
            self.assertTrue(receipt["ok"])
            self.assertEqual("accepted", receipt["status"])
            self.assertEqual("structured_observation", receipt["type"])
            self.assertEqual(1, receipt["created_count"])
            self.assertTrue(receipt["bead_ids"])
            self.assertTrue(str(receipt["event_id"]).startswith("evt-"))

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][receipt["bead_id"]]
            self.assertEqual("structured_observation", bead["type"])
            self.assertEqual("src_quickbooks", bead["source_id"])
            self.assertEqual("evt_structured_001", bead["source_event_id"])
            self.assertEqual("cogs_spike_2026_05_04", bead["core_memory_unifying_id"])
            self.assertEqual({"store": "supabase", "ref": "qbo_expenses:QB-98231"}, bead["hydration_ref"])
            self.assertEqual("qbo_expenses", bead["source_table"])
            self.assertEqual("QB-98231", bead["source_record_id"])
            self.assertEqual("2026-05-04T15:00:00Z", bead["as_of_timestamp"])

    def test_llm_judge_authors_external_semantics_without_unifying_id(self):
        judged = json.dumps({
            "title": "LLM-authored QuickBooks COGS variance",
            "summary": ["The QuickBooks row shows COGS 38% above baseline."],
            "detail": "Fresh Produce LLC is the named vendor in the COGS variance evidence.",
            "entities": ["Fresh Produce LLC", "COGS"],
            "topics": ["financial variance"],
            "supporting_facts": ["COGS was 38% above baseline."],
            "evidence_refs": [{"source_event_id": "evt_structured_001"}],
            "confidence": 0.91,
            "authority": "llm_source_evidence_review",
        })
        payload = _structured_payload(core_memory_unifying_id="")
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_EXTERNAL_EVIDENCE_BEAD_JUDGE_MODE": "llm"},
            clear=False,
        ), patch(
            "core_memory.runtime.ingest.external_evidence.chat_complete",
            return_value=judged,
        ):
            receipt = ingest_structured_observation(td, payload, session_id="external-source")

            self.assertTrue(receipt["ok"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][receipt["bead_id"]]
            self.assertEqual("LLM-authored QuickBooks COGS variance", bead["title"])
            self.assertEqual(["The QuickBooks row shows COGS 38% above baseline."], bead["summary"])
            self.assertFalse(bead.get("core_memory_unifying_id"))
            self.assertIn("llm_judged", bead["tags"])
            self.assertIn("external_evidence_bead_judge", bead["tags"])
            self.assertEqual("qbo_expenses", bead["source_table"])
            self.assertEqual("QB-98231", bead["source_record_id"])

    def test_document_reference_writes_artifact_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = ingest_document_reference(td, _document_payload(), session_id="external-source")
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][receipt["bead_id"]]
            self.assertEqual("document_reference", bead["type"])
            self.assertEqual("doc_001", bead["document_id"])
            self.assertEqual("ragie_doc_001", bead["ragie_document_id"])
            self.assertEqual("Acme Vendor Contract.pdf", bead["document_name"])
            self.assertEqual({"store": "ragie", "ref": "ragie_doc_001"}, bead["hydration_ref"])
            self.assertEqual("termination clause", bead["section_refs"][0]["label"])

    def test_document_reference_carries_source_ingest_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = ingest_document_reference(
                td,
                _document_payload(
                    ingest_batch_id="batch-doc-001",
                    source_ingest_envelope={
                        "boundary_type": "DocumentImported",
                        "ingest_batch_id": "batch-doc-001",
                        "source_type": "document",
                        "source_object_id": "doc_001",
                        "source_version": "v1",
                        "authority_class": "uploaded_source",
                    },
                ),
                session_id="external-source",
            )
            self.assertEqual("batch-doc-001", receipt["source_ingest_batch_id"])
            self.assertTrue(receipt["source_ingest_envelope_id"].startswith("env-"))
            self.assertEqual("batch-doc-001", receipt["source_ingest_envelope_ref"]["ingest_batch_id"])

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][receipt["bead_id"]]
            envelope = bead["source_ingest_envelope"]
            self.assertEqual("core_memory.source_ingest_envelope.v1", envelope["schema"])
            self.assertEqual("DocumentImported", envelope["boundary_type"])
            self.assertEqual("batch-doc-001", envelope["ingest_batch_id"])
            self.assertEqual("doc_001", envelope["source_object_id"])
            self.assertEqual("uploaded_source", envelope["authority_class"])
            self.assertEqual("termination clause", envelope["local_refs"]["section_refs"][0]["label"])
            self.assertEqual("batch-doc-001", bead["source_ingest_batch_id"])

    def test_state_assertion_writes_derived_business_state(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = ingest_state_assertion(td, _state_assertion_payload(), session_id="external-source")
            self.assertTrue(receipt["ok"])
            self.assertEqual("state_assertion", receipt["type"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][receipt["bead_id"]]
            self.assertEqual("state_assertion", bead["type"])
            self.assertEqual(["structured_observation:cogs_spike_2026_05_04"], bead["derived_from"])
            self.assertEqual("business_state", bead["assertion_kind"])
            self.assertEqual("Fresh Produce LLC", bead["assertion_subject"])
            self.assertEqual("became_primary_driver_of", bead["assertion_predicate"])
            self.assertEqual("COGS increase", bead["assertion_value"])
            self.assertEqual("2026-05-04T00:00:00Z", bead["effective_from"])
            self.assertEqual("derived_analysis", bead["authority"])

    def test_external_evidence_dedupes_by_source_event_id(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_external_evidence(td, _structured_payload(), session_id="external-source")
            second = ingest_external_evidence(td, _structured_payload(title="COGS spike duplicate title"), session_id="external-source")
            self.assertEqual(first["bead_id"], second["bead_id"])
            self.assertEqual("already_exists", second["status"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(idx["beads"]))

    def test_missing_required_source_fields_fails_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            payload = _structured_payload(source_event_id="")
            with self.assertRaises(ValueError) as ctx:
                ingest_structured_observation(td, payload, session_id="external-source")
            self.assertIn("source_event_id", str(ctx.exception))
            self.assertFalse((Path(td) / ".beads" / "index.json").exists())

    def test_state_assertion_requires_derivation(self):
        with tempfile.TemporaryDirectory() as td:
            payload = _state_assertion_payload(derived_from=[])
            with self.assertRaises(ValueError) as ctx:
                ingest_state_assertion(td, payload, session_id="external-source")
            self.assertIn("derived_from", str(ctx.exception))

    def test_structured_observation_is_retrievable_by_metric_terms(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False):
            receipt = ingest_structured_observation(td, _structured_payload(), session_id="external-source")
            out = memory_search_request(
                root=td,
                request={
                    "raw_query": "QuickBooks COGS variance qbo_expenses",
                    "intent": "remember",
                    "k": 5,
                    "facets": {"bead_types": ["structured_observation"]},
                    "constraints": {"require_structural": False},
                },
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            ids = [r.get("bead_id") for r in (out.get("results") or [])]
            self.assertIn(receipt["bead_id"], ids)


if __name__ == "__main__":
    unittest.main()
