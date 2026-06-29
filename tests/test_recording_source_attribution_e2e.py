import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval.agent import recall
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.ingest.external_evidence import (
    ingest_document_reference,
    ingest_external_evidence,
    ingest_state_assertion,
    ingest_structured_observation,
)
from core_memory.runtime.semantic_tasks import ModelProfile, SemanticTaskResult


class UnavailableSemanticRuntime:
    def run(self, request):
        return SemanticTaskResult(
            task_id="causal-recall-unavailable",
            task_type=request.task_type,
            ok=False,
            status="unavailable",
            model_profile=ModelProfile(tier="standard", runtime="provider"),
            error="missing_chat_provider",
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
        )


def _read_index(root):
    return json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))


def _citation(citations, *, bead_id=None, source_ref=None):
    for citation in citations:
        if bead_id is not None and citation.get("bead_id") != bead_id:
            continue
        if source_ref is not None and citation.get("source_ref") != source_ref:
            continue
        return citation
    return None


class TestRecordingSourceAttributionE2E(unittest.TestCase):
    def test_recording_sources_survive_write_to_causal_recall_citations(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
                "CORE_MEMORY_CLAIM_LAYER": "0",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
                "CORE_MEMORY_GRAPH_BACKEND": "none",
                "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
            },
            clear=False,
        ):
            turn = process_turn_finalized(
                root=td,
                session_id="s-e2e",
                turn_id="t-e2e-1",
                turns=[
                    {"speaker": "user", "role": "user", "content": "Why did COGS spike?"},
                    {
                        "speaker": "assistant",
                        "role": "assistant",
                        "content": "Fresh Produce pricing changed after the vendor contract update.",
                    },
                ],
                metadata={
                    "crawler_updates": {
                        "beads_create": [
                            {
                                "type": "transcript",
                                "title": "COGS pricing discussion transcript",
                                "summary": [
                                    "The assistant noted Fresh Produce pricing changed after a vendor contract update."
                                ],
                                "source_turn_ids": ["t-e2e-1"],
                                "entities": ["Fresh Produce LLC", "COGS"],
                                "topics": ["vendor pricing", "COGS"],
                            }
                        ]
                    }
                },
            )
            self.assertTrue(turn["ok"])
            self.assertTrue(turn["bead_id"])

            transcript = ingest_external_evidence(
                td,
                {
                    "data_type_flag": "conversation.transcript",
                    "title": "COGS pricing discussion transcript source",
                    "summary": ["Transcript source for the COGS pricing discussion."],
                    "source_id": "src_transcript",
                    "source_event_id": "evt_transcript_e2e",
                    "source_system": "chat_runtime",
                    "source_kind": "transcript",
                    "transcript_id": "tr_e2e",
                    "conversation_id": "conv_e2e",
                    "message_refs": ["t-e2e-1"],
                    "speaker_refs": ["user", "assistant"],
                    "core_memory_unifying_id": "transcript_source_e2e",
                    "hydration_ref": {
                        "store": "turn_archive",
                        "ref": "s-e2e:t-e2e-1",
                        "url": "https://example.com/transcript/t-e2e-1",
                    },
                    "entities": ["Fresh Produce LLC", "COGS"],
                },
                session_id="s-e2e",
            )
            structured = ingest_structured_observation(
                td,
                {
                    "title": "COGS increased 38% over the prior 7-day baseline",
                    "summary": ["COGS was $12,450 for the measured window, 38% above baseline."],
                    "source_id": "src_quickbooks",
                    "source_event_id": "evt_structured_e2e",
                    "source_system": "quickbooks",
                    "source_kind": "relational",
                    "source_table": "qbo_expenses",
                    "source_record_id": "QB-98231",
                    "record_grain": "metric_period",
                    "metric_name": "COGS",
                    "metric_value": 12450,
                    "change_pct": 38,
                    "entities": ["Fresh Produce LLC", "COGS"],
                    "as_of_timestamp": "2026-05-04T15:00:00Z",
                    "observed_at": "2026-05-04T15:00:00Z",
                    "core_memory_unifying_id": "cogs_spike_e2e",
                    "hydration_ref": {
                        "store": "supabase",
                        "ref": "qbo_expenses:QB-98231",
                        "url": "https://example.com/rows/QB-98231",
                    },
                },
                session_id="external-source",
            )
            document = ingest_document_reference(
                td,
                {
                    "title": "Fresh Produce vendor contract amendment",
                    "summary": ["Contract amendment changed Fresh Produce pricing."],
                    "source_id": "src_docs",
                    "source_event_id": "evt_doc_e2e",
                    "source_system": "ragie",
                    "source_kind": "document",
                    "document_id": "doc_fresh_produce_contract",
                    "ragie_document_id": "ragie_doc_fresh_produce_contract",
                    "document_name": "Fresh Produce Contract Amendment.pdf",
                    "mime_type": "application/pdf",
                    "document_kind": "contract",
                    "entities": ["Fresh Produce LLC"],
                    "observed_at": "2026-05-01T15:00:00Z",
                    "core_memory_unifying_id": "fresh_produce_contract_e2e",
                    "hydration_ref": {
                        "store": "ragie",
                        "ref": "ragie_doc_fresh_produce_contract",
                        "url": "https://example.com/docs/fresh-produce",
                    },
                    "section_refs": [{"label": "pricing clause", "page": 4, "chunk_ref": "ragie_chunk_pricing"}],
                },
                session_id="external-source",
            )
            state = ingest_state_assertion(
                td,
                {
                    "title": "Fresh Produce LLC became the primary COGS driver",
                    "summary": [
                        "Fresh Produce LLC accounted for 61% of the COGS increase during the measured period."
                    ],
                    "derived_from": [
                        "structured_observation:cogs_spike_e2e",
                        "document_reference:fresh_produce_contract_e2e",
                        "transcript:transcript_source_e2e",
                    ],
                    "derived_from_bead_ids": [
                        structured["bead_id"],
                        document["bead_id"],
                        transcript["bead_id"],
                    ],
                    "assertion_kind": "business_state",
                    "assertion_subject": "Fresh Produce LLC",
                    "assertion_predicate": "became_primary_driver_of",
                    "assertion_value": "COGS increase",
                    "effective_from": "2026-05-04T00:00:00Z",
                    "observed_at": "2026-05-04T16:00:00Z",
                    "confidence": 0.82,
                    "authority": "derived_analysis",
                    "entities": ["Fresh Produce LLC", "COGS"],
                    "topics": ["COGS"],
                    "source_turn_ids": ["t-e2e-1"],
                    "message_refs": ["t-e2e-1"],
                },
                session_id="analysis",
            )

            index = _read_index(td)
            turn_bead = index["beads"][turn["bead_id"]]
            transcript_bead = index["beads"][transcript["bead_id"]]
            structured_bead = index["beads"][structured["bead_id"]]
            document_bead = index["beads"][document["bead_id"]]
            state_bead = index["beads"][state["bead_id"]]

            self.assertEqual(["t-e2e-1"], turn_bead["source_turn_ids"])
            self.assertEqual("src_transcript", transcript_bead["source_attribution"]["source_id"])
            self.assertEqual(
                {
                    "store": "turn_archive",
                    "ref": "s-e2e:t-e2e-1",
                    "url": "https://example.com/transcript/t-e2e-1",
                },
                transcript_bead["hydration_ref"],
            )
            self.assertEqual(["t-e2e-1"], transcript_bead["message_refs"])
            self.assertEqual("src_quickbooks", structured_bead["source_attribution"]["source_id"])
            self.assertEqual("qbo_expenses:QB-98231", structured_bead["hydration_ref"]["ref"])
            self.assertEqual("src_docs", document_bead["source_attribution"]["source_id"])
            self.assertEqual("ragie_chunk_pricing", document_bead["section_refs"][0]["chunk_ref"])
            self.assertEqual(
                [structured["bead_id"], document["bead_id"], transcript["bead_id"]],
                state_bead["derived_from_bead_ids"],
            )
            self.assertEqual(["t-e2e-1"], state_bead["source_turn_ids"])

            raw = {
                "ok": True,
                "request": {"intent": "causal"},
                "results": [
                    {
                        "bead_id": state["bead_id"],
                        "title": state_bead["title"],
                        "summary": state_bead["summary"],
                        "type": "state_assertion",
                    }
                ],
            }
            with patch("core_memory.retrieval.agent.memory_execute", return_value=raw), patch(
                "core_memory.retrieval.causal_recall.get_semantic_task_runtime",
                return_value=UnavailableSemanticRuntime(),
            ):
                result = recall(
                    {
                        "query": "Why did Fresh Produce LLC become the primary COGS driver?",
                        "hints": {
                            "anchor_ids": [state["bead_id"]],
                            "source_scope": {
                                "denied_source_ids": ["src_docs"],
                                "redaction_policy": "redact_evidence",
                            },
                        },
                    },
                    effort="high",
                    root=td,
                    include_raw=False,
                )

            self.assertEqual("answered", result.status)
            self.assertEqual("core_memory.state_packet.v1", result.state_packet["schema_version"])
            self.assertIn("source", result.tier_path)
            self.assertGreaterEqual(len(result.root_cause_attribution["causal_paths"]), 3)
            path_nodes = {
                node
                for path in result.root_cause_attribution["causal_paths"]
                for node in path["nodes"]
            }
            self.assertIn(document["bead_id"], path_nodes)

            availability = {row["availability"]: row["count"] for row in result.state_packet["source_availability"]}
            self.assertGreaterEqual(availability.get("available", 0), 1)
            self.assertGreaterEqual(availability.get("redacted", 0), 1)

            structured_citation = _citation(
                result.source_citations,
                bead_id=structured["bead_id"],
                source_ref="qbo_expenses:QB-98231",
            )
            self.assertIsNotNone(structured_citation)
            self.assertEqual("available", structured_citation["availability"])
            self.assertEqual("https://example.com/rows/QB-98231", structured_citation["url"])
            self.assertEqual("relational", structured_citation["source_kind"])

            transcript_citation = _citation(
                result.source_citations,
                bead_id=transcript["bead_id"],
                source_ref="t-e2e-1",
            )
            self.assertIsNotNone(transcript_citation)
            self.assertEqual("available", transcript_citation["availability"])
            self.assertEqual("transcript", transcript_citation["source_kind"])

            document_citation = _citation(
                result.source_citations,
                bead_id=document["bead_id"],
                source_ref="ragie_doc_fresh_produce_contract",
            )
            self.assertIsNotNone(document_citation)
            self.assertEqual("redacted", document_citation["availability"])
            self.assertEqual("", document_citation["url"])
            self.assertEqual("denied_source", document_citation["metadata"]["redaction_reason"])

            section_citation = _citation(
                result.source_citations,
                bead_id=document["bead_id"],
                source_ref="ragie_chunk_pricing",
            )
            self.assertIsNotNone(section_citation)
            self.assertEqual("redacted", section_citation["availability"])
            self.assertEqual("pricing clause", section_citation["label"])


if __name__ == "__main__":
    unittest.main()
