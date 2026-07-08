import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.agent import recall
from core_memory.retrieval.causal_recall import execute_state_packet, extract_source_citations, normalize_recall_hints, should_run_causal_pipeline
from core_memory.retrieval.pipeline import memory_search_request
from core_memory.persistence.semantic_task_receipts import list_semantic_task_runs, record_semantic_task_run
from core_memory.schema.semantic_tasks import ModelProfile, SemanticTaskResult, TASK_CAUSAL_RECALL_EXECUTE


class UnavailableSemanticRuntime:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
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


class SuccessfulSemanticRuntime:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        result = SemanticTaskResult(
            task_id="causal-recall-execute",
            task_type=request.task_type,
            ok=True,
            status="succeeded",
            output_json={
                "selected_explanation": "Vendor prices increased before the COGS spike.",
                "temporal_frame": "current_truth",
                "primary_trace_ids": ["trace-1"],
                "secondary_trace_ids": [],
                "rejected_trace_ids": [],
                "root_causes": [{"bead_id": "cause-1", "role": "upstream", "confidence": "high"}],
                "confidence": "high",
                "citations": [{"bead_id": "cause-1"}],
                "open_questions": [],
            },
            model_profile=ModelProfile(
                tier="standard",
                provider="openai",
                adapter="openai",
                model="standard-recall",
                runtime="provider",
                source="unit",
            ),
            prompt_version=request.prompt_version,
            output_schema=request.output_schema,
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
        )
        if request.root:
            row = record_semantic_task_run(request.root, request, result)
            result = SemanticTaskResult(
                **{
                    **result.as_dict(),
                    "model_profile": result.model_profile,
                    "receipt_id": str(row.get("receipt_id") or ""),
                }
            )
        return result


class TestCausalRecallPipeline(unittest.TestCase):
    def test_hints_normalize_as_generic_soft_priors(self):
        hints = normalize_recall_hints({
            "bead_types": ["Outcome", "state_assertion"],
            "causal_labels": ["caused_by"],
            "keywords": ["COGS"],
            "source_scope": {"denied_source_ids": ["secret-source"]},
        })

        self.assertEqual(["outcome", "state_assertion"], hints["bead_types"])
        self.assertEqual(["caused_by"], hints["causal_labels"])
        self.assertEqual(["COGS"], hints["keywords"])
        self.assertEqual(["secret-source"], hints["source_scope"]["denied_source_ids"])

    def test_recall_attaches_trace_state_execute_with_missing_llm_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            outcome = store.add_bead(type="outcome", title="COGS spike", summary=["COGS increased."], observed_at="2026-05-04T15:00:00Z")
            cause = store.add_bead(type="state_assertion", title="Vendor price increase", summary=["Vendor prices increased."], observed_at="2026-05-03T15:00:00Z")
            store.link(source_id=outcome, target_id=cause, relationship="caused_by", confidence=0.95)
            raw = {"ok": True, "results": [{"bead_id": outcome, "title": "COGS spike", "summary": ["COGS increased."], "type": "outcome"}]}

            with patch("core_memory.retrieval.agent.memory_execute", return_value=raw), patch(
                "core_memory.retrieval.causal_recall.get_semantic_task_runtime",
                return_value=UnavailableSemanticRuntime(),
            ):
                result = recall("Why did COGS spike?", effort="high", root=td, include_raw=False)

        self.assertEqual("answered", result.status)
        self.assertIn("causal", result.tier_path)
        self.assertIn("trace", result.tier_path)
        self.assertIn("state", result.tier_path)
        self.assertIn("execute", result.tier_path)
        self.assertTrue(result.root_cause_attribution["causal_paths"])
        self.assertEqual("core_memory.state_packet.v1", result.state_packet["schema_version"])
        self.assertEqual("core_memory.execute_decision.v1", result.execute_decision["schema_version"])
        self.assertIn("execute_llm_unavailable", result.warnings)
        self.assertIn("Vendor price increase", result.answer)

    def test_recall_does_not_promote_fallback_answer_without_causal_paths(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            outcome = store.add_bead(type="outcome", title="COGS spike", summary=["COGS increased."])
            raw = {"ok": True, "results": [{"bead_id": outcome, "title": "COGS spike", "summary": ["COGS increased."], "type": "outcome"}]}

            with patch("core_memory.retrieval.agent.memory_execute", return_value=raw), patch(
                "core_memory.retrieval.causal_recall.get_semantic_task_runtime",
                return_value=UnavailableSemanticRuntime(),
            ):
                result = recall("Why did COGS spike?", effort="high", root=td, include_raw=False)

        self.assertEqual("partial", result.status)
        self.assertIsNone(result.answer)
        self.assertEqual([], result.root_cause_attribution["causal_paths"])
        self.assertIn("execute_llm_unavailable", result.warnings)

    def test_causal_pipeline_trigger_is_narrow_for_implicit_queries(self):
        self.assertFalse(should_run_causal_pipeline("What changed in the onboarding notes?", "medium", None))
        self.assertFalse(should_run_causal_pipeline("What was the impact of onboarding?", "high", None))
        self.assertTrue(should_run_causal_pipeline("What caused the onboarding regression?", "high", None))
        self.assertTrue(should_run_causal_pipeline("Summarize onboarding", "medium", "causal"))

    def test_pinned_hint_anchor_is_included_without_hard_filtering_others(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            pinned = store.add_bead(type="decision", title="Pinned billing decision", summary=["Billing changed."])
            other = store.add_bead(type="outcome", title="Other billing outcome", summary=["Outcome changed."])

            with patch("core_memory.retrieval.pipeline.canonical.semantic_lookup", return_value={"ok": True, "results": [{"bead_id": other, "score": 0.7}]}):
                out = memory_search_request(
                    root=td,
                    request={
                        "query": "billing",
                        "k": 5,
                        "hints": {"anchor_ids": [pinned], "bead_types": ["decision"]},
                    },
                    explain=True,
                )

        ids = [r["bead_id"] for r in out["results"]]
        self.assertIn(pinned, ids)
        self.assertIn(other, ids)
        pinned_row = next(r for r in out["results"] if r["bead_id"] == pinned)
        self.assertEqual("pinned_hint", pinned_row["anchor_reason"])
        self.assertGreater(pinned_row["hint_boost"], 0.0)

    def test_source_citation_redaction_keeps_shell_without_link(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = store.add_bead(
                type="document_reference",
                title="Vendor contract",
                summary=["Contract uploaded."],
                source_id="secret-source",
                source_ref="doc-secret",
                hydration_ref={"store": "docs", "ref": "doc-secret", "url": "https://example.com/secret"},
            )

            citations = extract_source_citations(
                td,
                [bead],
                hints={"source_scope": {"denied_source_ids": ["secret-source"], "redaction_policy": "redact_evidence"}},
            )

        self.assertTrue(citations)
        self.assertEqual("redacted", citations[0]["availability"])
        self.assertEqual("", citations[0]["url"])
        self.assertEqual("denied_source", citations[0]["metadata"]["redaction_reason"])

    def test_execute_state_packet_uses_semantic_task_runtime_and_records_receipt(self):
        runtime = SuccessfulSemanticRuntime()
        state_packet = {
            "temporal_frame": "current_truth",
            "root_causes": [{"bead_id": "cause-1", "title": "Vendor prices", "confidence": "high"}],
            "source_citations": [{"bead_id": "cause-1"}],
            "uncertainty": {"confidence": "moderate", "open_questions": []},
        }
        trace_package = {"candidate_traces": [{"trace_id": "trace-1", "summary": "Vendor prices increased."}]}

        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.retrieval.causal_recall.get_semantic_task_runtime",
            return_value=runtime,
        ):
            decision = execute_state_packet(state_packet=state_packet, trace_package=trace_package, root=td)
            rows = list_semantic_task_runs(td, task_type=TASK_CAUSAL_RECALL_EXECUTE)

        self.assertEqual("Vendor prices increased before the COGS spike.", decision.get("selected_explanation"))
        self.assertEqual("high", decision.get("confidence"))
        self.assertEqual([], decision.get("warnings"))
        self.assertEqual(True, (decision.get("llm") or {}).get("available"))
        self.assertEqual(TASK_CAUSAL_RECALL_EXECUTE, ((decision.get("llm") or {}).get("semantic_task") or {}).get("task_type"))
        self.assertEqual(1, len(runtime.requests))
        request = runtime.requests[0]
        self.assertEqual(TASK_CAUSAL_RECALL_EXECUTE, request.task_type)
        self.assertEqual("causal_recall_execute.v1", request.prompt_version)
        self.assertEqual("core_memory.execute_decision.v1", request.output_schema)
        self.assertEqual("deterministic_execute", request.fallback_mode)
        self.assertEqual(1, rows.get("count"))
        row = (rows.get("results") or [{}])[0]
        self.assertEqual(TASK_CAUSAL_RECALL_EXECUTE, row.get("task_type"))
        self.assertEqual("succeeded", row.get("status"))
        self.assertEqual("standard", row.get("model_tier"))
        self.assertEqual("causal_recall_execute.v1", row.get("prompt_version"))
        self.assertEqual("core_memory.execute_decision.v1", row.get("output_schema"))

    def test_causal_recall_policy_has_no_direct_chat_provider_call(self):
        import os

        here = os.path.dirname(__file__)
        path = os.path.abspath(os.path.join(here, "..", "core_memory", "retrieval", "causal_recall.py"))
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        self.assertNotIn("resolve_chat_config", source)
        self.assertNotIn("chat_complete", source)


if __name__ == "__main__":
    unittest.main()
