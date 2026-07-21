import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.provider_config import ProviderConfig
from core_memory.runtime.associations.coverage import (
    LLMAssociationJudge,
    apply_association_proposals,
    association_coverage_summary,
    association_judge_readiness,
    decide_association_candidate,
    enqueue_association_coverage,
    get_association_run,
    list_association_candidates,
    on_bead_committed,
    run_association_coverage,
)
from core_memory.runtime.engine import process_flush, process_turn_finalized
from core_memory.runtime.ingest.external_evidence import (
    ingest_document_reference,
    ingest_state_assertion,
    ingest_structured_observation,
)
from core_memory.runtime.queue.jobs import enqueue_async_job, run_async_jobs
from core_memory.persistence.semantic_task_receipts import list_semantic_task_runs


def _index(root: str) -> dict:
    return json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))


def _assocs(root: str, relationship: str) -> list[dict]:
    idx = _index(root)
    return [
        a for a in (idx.get("associations") or [])
        if str((a or {}).get("relationship") or "") == relationship
    ]


def _jsonl_rows(root: str, name: str) -> list[dict]:
    path = Path(root) / ".beads" / "events" / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _add_test_bead(store: MemoryStore, **kwargs):
    kwargs.setdefault("_association_coverage", False)
    kwargs.setdefault("retrieval_eligible", True)
    return store.add_bead(**kwargs)


def _test_provider_config(model: str = "standard-association-model") -> ProviderConfig:
    return ProviderConfig(
        kind="chat",
        provider="openai",
        base_url="https://example.test/v1",
        api_key="test",
        model=model,
        source="unit",
        explicit=True,
    )


def _agent_decision_for_candidate(candidate: dict, *, action: str = "accept") -> dict:
    signals = [signal for signal in (candidate.get("signals") or []) if isinstance(signal, dict)]
    kinds = {str(signal.get("kind") or "") for signal in signals}
    source = str(candidate.get("source_bead") or "")
    target = str(candidate.get("target_bead") or "")
    relationship = "associated_with"

    section_signal = next((signal for signal in signals if signal.get("kind") == "document_section_scope"), None)
    if section_signal:
        source = str(section_signal.get("section_bead_id") or source)
        target = str(section_signal.get("document_bead_id") or target)
        relationship = "part_of"
    else:
        explicit = next((signal for signal in signals if signal.get("kind") == "explicit_reference"), None)
        fields = set(explicit.get("fields") or []) if explicit else set()
        if explicit and str(explicit.get("from_bead") or "") == target:
            source, target = target, source
        if "supersedes" in fields:
            relationship = "supersedes"
        elif fields.intersection({"derived_from", "derived_from_bead_ids"}):
            relationship = "derived_from"
        elif "supports_bead_ids" in fields:
            relationship = "supports"
        elif fields.intersection({"prev_bead_id", "next_bead_id"}):
            relationship = "follows"
        elif kinds.intersection({"temporal_adjacency", "temporal_proximity", "same_session", "same_transcript"}):
            relationship = "follows"

    return {
        "candidate_id": candidate["candidate_id"],
        "action": action,
        "source_bead": source,
        "target_bead": target,
        "relationship": relationship,
        "confidence": 0.9,
        "reason_text": f"Agent judged the pair as {relationship} from visible evidence.",
        "truth_basis": "fake_judge_evidence_review",
        "evidence_bead_ids": candidate.get("evidence_bead_ids") or [source, target],
        "evidence_refs": candidate.get("evidence_refs") or [],
    }


class AcceptingFakeAssociationJudge:
    def review(self, context):
        return {
            "contract": "memory.association_judge.v2",
            "run_id": context["run_id"],
            "judge_model": "fake-accepting",
            "prompt_version": context["prompt_version"],
            "rubric_version": context["rubric_version"],
            "decisions": [_agent_decision_for_candidate(candidate) for candidate in context.get("candidates", [])],
            "reviewed_beads": [{"bead_id": bid, "association_state": "linked"} for bid in context.get("source_bead_ids", [])],
        }


class RejectingFakeAssociationJudge:
    def review(self, context):
        return {
            "contract": "memory.association_judge.v2",
            "run_id": context["run_id"],
            "judge_model": "fake-rejecting",
            "prompt_version": context["prompt_version"],
            "rubric_version": context["rubric_version"],
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "reject",
                    "reason_text": "Candidate unsupported in fake review.",
                    "truth_basis": "insufficient_evidence",
                }
                for candidate in context.get("candidates", [])
            ],
            "reviewed_beads": [{"bead_id": bid, "association_state": "no_supported_links"} for bid in context.get("source_bead_ids", [])],
        }


class ModifyingFakeAssociationJudge:
    def review(self, context):
        candidate = (context.get("candidates") or [])[0]
        return {
            "contract": "memory.association_judge.v2",
            "run_id": context["run_id"],
            "judge_model": "fake-modifying",
            "prompt_version": context["prompt_version"],
            "rubric_version": context["rubric_version"],
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "modify",
                    "source_bead": candidate["source_bead"],
                    "target_bead": candidate["target_bead"],
                    "relationship": "derived_from",
                    "confidence": 0.91,
                    "reason_text": "The fake judge chose a stronger evidence relation.",
                    "truth_basis": "metadata_and_content_entailment",
                    "evidence_bead_ids": candidate.get("evidence_bead_ids") or [],
                }
            ],
            "reviewed_beads": [{"bead_id": bid, "association_state": "linked"} for bid in context.get("source_bead_ids", [])],
        }


class InvalidFakeAssociationJudge:
    def review(self, context):
        candidate = (context.get("candidates") or [])[0]
        return {
            "contract": "memory.association_judge.v2",
            "run_id": context["run_id"],
            "judge_model": "fake-invalid",
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "accept",
                    "source_bead": candidate["source_bead"],
                    "target_bead": candidate["target_bead"],
                    "relationship": "not_a_relation",
                    "confidence": 0.9,
                    "reason_text": "Invalid relation.",
                    "truth_basis": "fake_invalid_review",
                    "evidence_bead_ids": candidate.get("evidence_bead_ids") or [],
                }
            ],
        }


class PartialFakeAssociationJudge:
    def review(self, context):
        return {
            "contract": "memory.association_judge.v2",
            "run_id": context["run_id"],
            "judge_model": "fake-partial",
            "prompt_version": context["prompt_version"],
            "rubric_version": context["rubric_version"],
            "decisions": [],
            "reviewed_beads": [],
        }


class LinkedAliasAssociationJudge:
    def review(self, context):
        candidate = (context.get("candidates") or [])[0]
        return {
            "contract": "memory.association_judge.v2",
            "run_id": context["run_id"],
            "judge_model": "fake-linked-alias",
            "decisions": [_agent_decision_for_candidate(candidate, action="linked")],
            "reviewed_beads": [{"bead_id": bid, "association_state": "linked"} for bid in context.get("source_bead_ids", [])],
        }


class NoSupportedLinksAliasAssociationJudge:
    def review(self, context):
        candidate = (context.get("candidates") or [])[0]
        return {
            "contract": "memory.association_judge.v2",
            "run_id": context["run_id"],
            "judge_model": "fake-no-supported-alias",
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "no_supported_links",
                    "reason_text": "Alias should be treated as no_link.",
                    "truth_basis": "fake_no_supported_links_review",
                }
            ],
            "reviewed_beads": [{"bead_id": bid, "association_state": "no_supported_links"} for bid in context.get("source_bead_ids", [])],
        }


class RateLimitedFakeAssociationJudge:
    def review(self, context):
        raise RuntimeError("HTTP Error 429: Too Many Requests")


class BrokenFakeAssociationJudge:
    def review(self, context):
        raise RuntimeError("schema mismatch")


def _document_payload(**overrides):
    payload = {
        "title": "Vendor Contract",
        "summary": ["Vendor contract source document."],
        "source_id": "src_docs",
        "source_event_id": "evt_doc_whole",
        "source_system": "documents",
        "document_id": "doc_001",
        "document_name": "Vendor Contract.pdf",
        "core_memory_unifying_id": "vendor_contract",
        "hydration_ref": {"store": "object_store", "ref": "documents/doc_001"},
        "retrieval_eligible": True,
    }
    payload.update(overrides)
    return payload


def _structured_payload(**overrides):
    payload = {
        "title": "Invoice total observed",
        "summary": ["Invoice INV-1 total was observed."],
        "source_id": "src_table",
        "source_event_id": "evt_row_1",
        "source_system": "warehouse",
        "source_table": "invoices",
        "source_record_id": "INV-1",
        "as_of_timestamp": "2026-06-01T00:00:00Z",
        "entities": ["Invoice INV-1"],
        "core_memory_unifying_id": "invoice_inv_1",
        "hydration_ref": {"store": "warehouse", "ref": "invoices:INV-1"},
        "retrieval_eligible": True,
    }
    payload.update(overrides)
    return payload


class TestAssociationCoverage(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            "os.environ",
            {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "disabled"},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_llm_association_judge_uses_json_mode_and_scales_output_budget(self):
        context = {
            "run_id": "arun-json-budget",
            "prompt_version": "association_judge.v2",
            "rubric_version": "association_truth.v2",
            "source_bead_ids": [f"bead-{i}" for i in range(12)],
            "candidates": [
                {
                    "candidate_id": f"cand-{i}",
                    "source_bead": f"bead-{i}",
                    "target_bead": f"bead-target-{i}",
                    "candidate_class": "relationship_neutral_pair",
                }
                for i in range(24)
            ],
        }
        calls = []

        def fake_chat_complete(prompt, *, max_tokens=700, temperature=0, json_mode=False, **_kwargs):
            calls.append({"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature, "json_mode": json_mode})
            return json.dumps({
                "contract": "memory.association_judge.v2",
                "run_id": "arun-json-budget",
                "decisions": [],
                "reviewed_beads": [],
            })

        with patch.dict(
            "os.environ",
            {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "provider"},
            clear=False,
        ), patch(
            "core_memory.policy.semantic_task_runtime.resolve_chat_config",
            return_value=_test_provider_config(),
        ), patch("core_memory.policy.semantic_task_runtime.chat_complete", fake_chat_complete):
            out = LLMAssociationJudge().review(context)

        self.assertEqual("memory.association_judge.v2", out.get("contract"))
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0]["json_mode"])
        self.assertGreater(calls[0]["max_tokens"], 1800)
        self.assertEqual(0, calls[0]["temperature"])

    def test_association_judge_readiness_reports_runtime_configuration(self):
        disabled = association_judge_readiness()
        self.assertFalse(disabled.get("ready"))
        self.assertEqual("semantic_task_runtime_disabled", disabled.get("reason"))

        with patch.dict(
            "os.environ",
            {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "provider"},
            clear=False,
        ), patch(
            "core_memory.runtime.associations.coverage.resolve_chat_config",
            return_value=_test_provider_config(),
        ):
            ready = association_judge_readiness()

        self.assertTrue(ready.get("ready"))
        self.assertEqual("provider_configured", ready.get("reason"))

    def test_llm_association_judge_falls_back_when_json_mode_is_unsupported(self):
        context = {
            "run_id": "arun-json-fallback",
            "prompt_version": "association_judge.v2",
            "rubric_version": "association_truth.v2",
            "source_bead_ids": ["bead-1"],
            "candidates": [],
        }
        calls = []

        def fake_chat_complete(_prompt, *, max_tokens=700, temperature=0, json_mode=False, **_kwargs):
            calls.append({"max_tokens": max_tokens, "temperature": temperature, "json_mode": json_mode})
            if json_mode:
                raise RuntimeError("response_format_unsupported")
            return """```json
{"contract":"memory.association_judge.v2","run_id":"arun-json-fallback","decisions":[],"reviewed_beads":[]}
```"""

        with patch.dict(
            "os.environ",
            {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "provider"},
            clear=False,
        ), patch(
            "core_memory.policy.semantic_task_runtime.resolve_chat_config",
            return_value=_test_provider_config(),
        ), patch("core_memory.policy.semantic_task_runtime.chat_complete", fake_chat_complete):
            out = LLMAssociationJudge().review(context)

        self.assertEqual("memory.association_judge.v2", out.get("contract"))
        self.assertEqual([True, False], [call["json_mode"] for call in calls])

    def test_association_judge_records_semantic_task_receipt_by_default(self):
        calls = []

        def fake_chat_complete(prompt, *, max_tokens=700, temperature=0, json_mode=False, **_kwargs):
            calls.append({"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature, "json_mode": json_mode})
            context = json.loads(prompt.split("\n\n", 1)[1])
            candidate = (context.get("candidates") or [])[0]
            return json.dumps(
                {
                    "contract": "memory.association_judge.v2",
                    "run_id": context["run_id"],
                    "judge_model": "standard-association-runtime",
                    "prompt_version": context["prompt_version"],
                    "rubric_version": context["rubric_version"],
                    "decisions": [_agent_decision_for_candidate(candidate)],
                    "reviewed_beads": [
                        {"bead_id": bid, "association_state": "linked"}
                        for bid in context.get("source_bead_ids", [])
                    ],
                }
            )

        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_MODEL_STANDARD": "standard-association-runtime",
                "CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "provider",
            },
            clear=False,
        ), patch(
            "core_memory.policy.semantic_task_runtime.resolve_chat_config",
            return_value=_test_provider_config(),
        ), patch("core_memory.policy.semantic_task_runtime.chat_complete", fake_chat_complete):
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
            )
            rows = list_semantic_task_runs(td, task_type="association_decision")
            assoc_count = len(_assocs(td, "follows"))

        self.assertTrue(out.get("ok"), out)
        self.assertEqual("completed", out.get("status"))
        self.assertEqual(1, assoc_count)
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0]["json_mode"])
        self.assertEqual(1, rows.get("count"))
        row = (rows.get("results") or [{}])[0]
        self.assertEqual("association_decision", row.get("task_type"))
        self.assertEqual("succeeded", row.get("status"))
        self.assertEqual("standard", row.get("model_tier"))
        self.assertEqual("association_judge.v2", row.get("prompt_version"))
        self.assertEqual("association_truth.v2", row.get("rubric_version"))
        self.assertEqual("memory.association_judge.v2", row.get("output_schema"))
        self.assertEqual("pending_judge", row.get("fallback_mode"))
        self.assertEqual("semantic_author", row.get("authority_boundary"))

    def test_unavailable_semantic_association_judge_keeps_pending_judge_state(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "disabled",
            },
            clear=False,
        ):
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
            )
            rows = list_semantic_task_runs(td, task_type="association_decision", status="unavailable")

            self.assertTrue(out.get("ok"), out)
            self.assertEqual("pending_judge", out.get("status"))
            self.assertEqual("semantic_task_runtime_disabled", out.get("warning"))
            self.assertEqual([], _assocs(td, "follows"))
            self.assertEqual(1, rows.get("count"))
            self.assertEqual("pending_judge", (rows.get("results") or [{}])[0].get("fallback_mode"))

    def test_rate_limited_association_judge_keeps_pending_judge_state(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=RateLimitedFakeAssociationJudge(),
            )
            rows = _jsonl_rows(td, "association-judge-decisions.jsonl")
            candidates = list_association_candidates(td, status="pending_judge", limit=10)

            self.assertTrue(out.get("ok"), out)
            self.assertEqual("pending_judge", out.get("status"))
            self.assertTrue(out.get("transient_error"))
            self.assertTrue(out.get("retryable_judge_error"))
            self.assertEqual("HTTP Error 429: Too Many Requests", out.get("warning"))
            self.assertEqual(1, (out.get("counts") or {}).get("pending_judge"))
            self.assertEqual([], _assocs(td, "follows"))
            self.assertEqual(1, candidates.get("count"))
            self.assertEqual("pending_judge", rows[-1].get("status"))
            self.assertTrue(rows[-1].get("transient_error"))

    def test_non_transient_association_judge_exception_still_fails(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=BrokenFakeAssociationJudge(),
            )
            rows = _jsonl_rows(td, "association-judge-decisions.jsonl")

            self.assertFalse(out.get("ok"), out)
            self.assertEqual("judge_failed", out.get("status"))
            self.assertEqual("schema mismatch", out.get("error"))
            self.assertEqual(1, (out.get("counts") or {}).get("failed"))
            self.assertEqual([], _assocs(td, "follows"))
            self.assertEqual("judge_failed", rows[-1].get("status"))

    def test_bead_without_candidate_proposals_completes_without_judge_failure(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = _add_test_bead(store, type="context", title="Only", summary=["only"], session_id="s1", source_turn_ids=["t1"])

            out = run_association_coverage(
                td,
                bead_ids=[bead],
                trigger="operator",
                judge=InvalidFakeAssociationJudge(),
            )

            self.assertTrue(out.get("ok"), out)
            self.assertEqual("completed", out.get("status"))
            self.assertEqual("no_supported_links", (out.get("association_state_by_bead") or {}).get(bead))
            self.assertEqual(0, (out.get("counts") or {}).get("pending_judge"))
            self.assertEqual(1, (out.get("counts") or {}).get("no_supported_links"))
            self.assertEqual([], _assocs(td, "follows"))

    def test_linked_action_alias_accepts_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=LinkedAliasAssociationJudge(),
            )

            self.assertTrue(out.get("ok"), out)
            self.assertEqual("completed", out.get("status"))
            follows = _assocs(td, "follows")
            self.assertEqual(1, len(follows))
            self.assertEqual("fake_judge_evidence_review", follows[0].get("truth_basis"))

    def test_no_supported_links_action_alias_rejects_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=NoSupportedLinksAliasAssociationJudge(),
            )

            self.assertTrue(out.get("ok"), out)
            self.assertEqual("completed", out.get("status"))
            self.assertEqual("no_supported_links", (out.get("association_state_by_bead") or {}).get(second))
            self.assertEqual([], _assocs(td, "follows"))

    def test_section_document_bead_links_part_of_whole_document_after_judge_approval(self):
        with tempfile.TemporaryDirectory() as td:
            whole = ingest_document_reference(td, _document_payload(), session_id="external")
            section = ingest_document_reference(
                td,
                _document_payload(
                    source_event_id="evt_doc_terms",
                    title="Vendor Contract - Terms",
                    summary=["Terms section from the vendor contract."],
                    section_refs=[{"section_id": "terms", "chunk_ref": "chunk_terms"}],
                ),
                session_id="external",
            )

            self.assertEqual("accepted", whole["status"])
            self.assertEqual("accepted", section["status"])
            self.assertEqual("pending_judge", section["association_state"])
            self.assertEqual([], _assocs(td, "part_of"))

            judged = run_association_coverage(
                td,
                bead_ids=[section["bead_id"]],
                candidate_bead_ids=[whole["bead_id"]],
                trigger="operator",
                judge=AcceptingFakeAssociationJudge(),
            )
            self.assertTrue(judged.get("ok"), judged)
            part_of = _assocs(td, "part_of")
            self.assertEqual(1, len(part_of))
            self.assertEqual(section["bead_id"], part_of[0]["source_bead"])
            self.assertEqual(whole["bead_id"], part_of[0]["target_bead"])
            self.assertEqual("fake-accepting", part_of[0]["judge_model"])
            self.assertEqual(judged["run_id"], part_of[0]["association_run_id"])
            self.assertTrue(part_of[0].get("candidate_ids"))

    def test_source_ingest_envelope_propagates_from_ingest_to_judged_association(self):
        with tempfile.TemporaryDirectory() as td:
            envelope = {
                "boundary_type": "DocumentImported",
                "ingest_batch_id": "drive-import-42",
                "workspace_id": "workspace-1",
                "source_type": "google_drive",
                "source_object_id": "doc_001",
                "source_event_id": "drive-event-42",
                "source_uri": "gdrive://doc_001",
                "authority_class": "source_attributed",
            }
            whole = ingest_document_reference(
                td,
                _document_payload(source_ingest_envelope=envelope),
                session_id="external",
            )
            section = ingest_document_reference(
                td,
                _document_payload(
                    source_event_id="evt_doc_terms",
                    title="Vendor Contract - Terms",
                    summary=["Terms section from the vendor contract."],
                    section_refs=[{"section_id": "terms", "chunk_ref": "chunk_terms"}],
                    source_ingest_envelope={
                        **envelope,
                        "local_refs": {"section_refs": [{"section_id": "terms", "chunk_ref": "chunk_terms"}]},
                    },
                ),
                session_id="external",
            )

            idx = _index(td)
            section_bead = idx["beads"][section["bead_id"]]
            bead_ref = section_bead.get("source_ingest_envelope_ref") or {}
            self.assertEqual("drive-import-42", bead_ref.get("ingest_batch_id"))
            self.assertEqual("google_drive", bead_ref.get("source_type"))

            candidates = list_association_candidates(td, status="pending_judge", limit=20)
            candidate = next(
                row for row in candidates.get("results") or []
                if "document_section_scope" in (row.get("reason_codes") or [])
            )
            self.assertIn("drive-import-42", candidate.get("source_ingest_batch_ids") or [])
            self.assertTrue(candidate.get("source_microbatch_key"))

            summary = association_coverage_summary(td)
            envelope_summary = summary.get("source_ingest_envelope_summary") or {}
            self.assertIn("drive-import-42", envelope_summary.get("batch_ids") or [])

            decided = decide_association_candidate(
                td,
                candidate_id=candidate["candidate_id"],
                action="accept",
                relationship="part_of",
                source_bead=section["bead_id"],
                target_bead=whole["bead_id"],
                confidence=0.93,
                reason_text="The section bead is scoped to the whole source document.",
                truth_basis="host_reviewed_source_envelope_candidate",
                evidence_bead_ids=[section["bead_id"], whole["bead_id"]],
            )
            self.assertTrue(decided.get("ok"), decided)
            self.assertEqual("linked", decided.get("status"))
            self.assertIn("drive-import-42", decided.get("source_ingest_batch_ids") or [])

            part_of = _assocs(td, "part_of")
            self.assertEqual(1, len(part_of))
            self.assertEqual(section["bead_id"], part_of[0]["source_bead"])
            self.assertEqual(whole["bead_id"], part_of[0]["target_bead"])
            self.assertIn("drive-import-42", part_of[0].get("source_ingest_batch_ids") or [])
            assoc_refs = part_of[0].get("source_ingest_envelope_refs") or []
            self.assertTrue(any((ref or {}).get("source_type") == "google_drive" for ref in assoc_refs))

            decision_rows = _jsonl_rows(td, "association-judge-decisions.jsonl")
            latest_decision = decision_rows[-1]
            self.assertIn("drive-import-42", latest_decision.get("source_ingest_batch_ids") or [])

    def test_state_assertion_links_derived_from_source_bead_after_judge_approval(self):
        with tempfile.TemporaryDirectory() as td:
            source = ingest_structured_observation(td, _structured_payload(), session_id="external")
            assertion = ingest_state_assertion(
                td,
                {
                    "title": "Invoice INV-1 requires review",
                    "summary": ["Invoice INV-1 requires review because the total crossed the threshold."],
                    "derived_from_bead_ids": [source["bead_id"]],
                    "assertion_kind": "business_state",
                    "assertion_subject": "Invoice INV-1",
                    "assertion_predicate": "requires",
                    "assertion_value": "review",
                    "effective_from": "2026-06-01T00:00:00Z",
                    "confidence": 0.82,
                    "retrieval_eligible": True,
                },
                session_id="external",
            )

            self.assertEqual([], _assocs(td, "derived_from"))
            judged = run_association_coverage(
                td,
                bead_ids=[assertion["bead_id"]],
                candidate_bead_ids=[source["bead_id"]],
                trigger="operator",
                judge=AcceptingFakeAssociationJudge(),
            )
            self.assertTrue(judged.get("ok"), judged)
            derived = _assocs(td, "derived_from")
            self.assertEqual(1, len(derived))
            self.assertEqual(assertion["bead_id"], derived[0]["source_bead"])
            self.assertEqual(source["bead_id"], derived[0]["target_bead"])

    def test_association_pass_without_judge_writes_candidates_but_no_edges(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            enq = enqueue_async_job(td, kind="association-pass", event={"bead_ids": [second], "candidate_bead_ids": [first]})
            self.assertTrue(enq.get("ok"), enq)
            ran = run_async_jobs(td, run_semantic=False, max_compaction=0, max_side_effects=5)
            self.assertTrue(ran.get("ok"), ran)
            side_effect = ran.get("side_effect_run") or {}
            self.assertEqual(1, side_effect.get("processed"), ran)
            self.assertEqual(0, side_effect.get("failed"), ran)
            self.assertEqual(0, side_effect.get("queue_depth"), ran)
            self.assertEqual([], _assocs(td, "follows"))
            candidate_rows = _jsonl_rows(td, "association-candidates.jsonl")
            self.assertTrue(candidate_rows)
            latest = get_association_run(td, (candidate_rows[-1] or {}).get("run_id"))
            self.assertTrue(latest.get("ok"), latest)
            self.assertEqual("pending_judge", (latest.get("run") or {}).get("status"))

            rerun = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=AcceptingFakeAssociationJudge(),
            )
            self.assertTrue(rerun.get("ok"), rerun)
            self.assertEqual(1, len(_assocs(td, "follows")))

    def test_rejecting_judge_records_no_supported_links_without_edge(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=RejectingFakeAssociationJudge(),
            )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual("no_supported_links", (out.get("association_state_by_bead") or {}).get(second))
            self.assertEqual([], _assocs(td, "follows"))
            decision_rows = _jsonl_rows(td, "association-judge-decisions.jsonl")
            self.assertTrue(decision_rows)
            self.assertEqual(1, ((decision_rows[-1].get("counts") or {}).get("rejected") or 0))

    def test_judge_can_modify_candidate_relation_before_commit(self):
        with tempfile.TemporaryDirectory() as td:
            whole = ingest_document_reference(td, _document_payload(), session_id="external")
            section = ingest_document_reference(
                td,
                _document_payload(
                    source_event_id="evt_doc_terms",
                    title="Vendor Contract - Terms",
                    summary=["Terms section from the vendor contract."],
                    section_refs=[{"section_id": "terms"}],
                ),
                session_id="external",
            )

            out = run_association_coverage(
                td,
                bead_ids=[section["bead_id"]],
                candidate_bead_ids=[whole["bead_id"]],
                trigger="operator",
                judge=ModifyingFakeAssociationJudge(),
            )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual([], _assocs(td, "part_of"))
            derived = _assocs(td, "derived_from")
            self.assertEqual(1, len(derived))
            self.assertEqual("metadata_and_content_entailment", derived[0]["truth_basis"])

    def test_candidates_are_relationship_neutral_and_include_rich_shortlist_signals(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            decision = _add_test_bead(
                store,
                type="decision",
                title="Adopt delegated authoring",
                summary=["Use delegated authoring for hosted capture."],
                session_id="s1",
                source_turn_ids=["t1"],
                entities=["Hosted bridge"],
                decision_keys=["hosted-authoring"],
                claims=[{"claim_id": "claim-hosted", "proposition": "Hosted capture needs an author."}],
            )
            outcome = _add_test_bead(
                store,
                type="outcome",
                title="Hosted capture authored rich beads",
                summary=["Delegated authoring filled the hosted memory contract."],
                session_id="s1",
                source_turn_ids=["t2"],
                entities=["Hosted bridge"],
                decision_keys=["hosted-authoring"],
                claims=[{"claim_id": "claim-hosted", "proposition": "Hosted capture needs an author."}],
            )

            run = run_association_coverage(
                td,
                bead_ids=[outcome],
                candidate_bead_ids=[decision],
                trigger="operator",
            )
            self.assertEqual("pending_judge", run.get("status"))
            candidates = list_association_candidates(td, status="pending_judge", limit=50)
            candidate = next(
                row
                for row in candidates.get("results") or []
                if set(row.get("pair_bead_ids") or []) == {decision, outcome}
            )

        self.assertEqual("memory.association_candidates.v2", candidates.get("contract"))
        self.assertEqual("relationship_neutral_pair", candidate.get("candidate_class"))
        self.assertNotIn("proposed_relationship", candidate)
        self.assertNotIn("proposed_direction", candidate)
        kinds = {signal.get("kind") for signal in candidate.get("signals") or []}
        self.assertTrue(
            {
                "explicit_candidate_request",
                "entity_overlap",
                "semantic_key_overlap",
                "claim_interaction",
                "goal_decision_outcome_continuity",
            }.issubset(kinds)
        )

    def test_judge_receives_causal_retrieval_claim_temporal_and_provenance_context(self):
        captured = {}

        class CapturingJudge(RejectingFakeAssociationJudge):
            def review(self, context):
                captured.update(context)
                return super().review(context)

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1")
            second = _add_test_bead(
                store,
                type="decision",
                title="Choose the richer path",
                summary=["The richer path was selected."],
                detail="The agent compared both paths before choosing.",
                session_id="s1",
                because=["It preserves causal evidence."],
                supporting_facts=["The thin path dropped claims."],
                state_change={"description": "The hosted path now delegates authorship."},
                retrieval_title="Why hosted capture delegates memory authoring",
                retrieval_facts=["The bridge is passive.", "A delegated agent fills the full schema."],
                decision_keys=["delegated-authoring"],
                claims=[{"claim_id": "claim-passive", "proposition": "The bridge is passive."}],
                observed_at="2026-07-21T12:00:00Z",
                effective_from="2026-07-21T12:00:00Z",
                authority="agent_inferred",
                confidence=0.91,
                source_attribution={"source": "turn", "turn_id": "t2"},
            )

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=CapturingJudge(),
            )

        self.assertTrue(out.get("ok"), out)
        bead = (captured.get("beads") or {}).get(second) or {}
        for field in (
            "because",
            "supporting_facts",
            "state_change",
            "retrieval_title",
            "retrieval_facts",
            "decision_keys",
            "claims",
            "observed_at",
            "effective_from",
            "authority",
            "confidence",
            "source_attribution",
        ):
            self.assertIn(field, bead)
        self.assertEqual(set(captured.get("bounded_visible_bead_ids") or []), {first, second})

    def test_neutral_candidate_missing_agent_relation_and_direction_is_quarantined(self):
        class IncompleteJudge:
            def review(self, context):
                candidate = (context.get("candidates") or [])[0]
                return {
                    "contract": "memory.association_judge.v2",
                    "run_id": context["run_id"],
                    "judge_model": "fake-incomplete",
                    "decisions": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "action": "accept",
                            "confidence": 0.8,
                            "reason_text": "The pair appears related.",
                            "truth_basis": "visible_context",
                            "evidence_bead_ids": candidate.get("evidence_bead_ids") or [],
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1")
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1")
            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                judge=IncompleteJudge(),
            )
            quarantine = _jsonl_rows(td, "association-quarantine.jsonl")
            edges = list(_index(td).get("associations") or [])

        self.assertEqual("quarantined", out.get("status"))
        self.assertEqual([], edges)
        self.assertIn("missing_agent_relationship", quarantine[-1].get("reasons") or [])
        self.assertIn("missing_agent_direction", quarantine[-1].get("reasons") or [])

    def test_agent_can_author_justified_non_temporal_causal_relation(self):
        class CausalJudge:
            def review(self, context):
                candidate = (context.get("candidates") or [])[0]
                bead_ids = set(candidate.get("pair_bead_ids") or [])
                return {
                    "contract": "memory.association_judge.v2",
                    "run_id": context["run_id"],
                    "judge_model": "fake-causal",
                    "decisions": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "action": "accept",
                            "source_bead": next(
                                bead_id
                                for bead_id in bead_ids
                                if (context.get("beads") or {}).get(bead_id, {}).get("type") == "decision"
                            ),
                            "target_bead": next(
                                bead_id
                                for bead_id in bead_ids
                                if (context.get("beads") or {}).get(bead_id, {}).get("type") == "outcome"
                            ),
                            "relationship": "led_to",
                            "confidence": 0.94,
                            "reason_text": "The outcome reports that the selected decision produced the result.",
                            "truth_basis": "decision_outcome_continuity_and_supporting_facts",
                            "evidence_bead_ids": list(bead_ids),
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            decision = _add_test_bead(
                store,
                type="decision",
                title="Enable delegated authoring",
                summary=["Enable the delegated author."],
                session_id="s1",
                decision_keys=["delegated-authoring"],
            )
            outcome = _add_test_bead(
                store,
                type="outcome",
                title="Rich hosted beads produced",
                summary=["Delegated authoring produced rich hosted beads."],
                supporting_facts=["The full schema was filled after the author was enabled."],
                session_id="s1",
                decision_keys=["delegated-authoring"],
            )
            out = run_association_coverage(
                td,
                bead_ids=[outcome],
                candidate_bead_ids=[decision],
                judge=CausalJudge(),
            )
            edges = _assocs(td, "led_to")

        self.assertTrue(out.get("ok"), out)
        self.assertEqual(1, len(edges))
        self.assertEqual(decision, edges[0]["source_bead"])
        self.assertEqual(outcome, edges[0]["target_bead"])

    def test_agent_added_pairs_must_stay_inside_bounded_visible_context(self):
        class AddedPairJudge:
            def __init__(self, extra_target):
                self.extra_target = extra_target

            def review(self, context):
                candidate = (context.get("candidates") or [])[0]
                source = candidate["source_bead"]
                target = candidate["target_bead"] if self.extra_target is None else self.extra_target
                return {
                    "contract": "memory.association_judge.v2",
                    "run_id": context["run_id"],
                    "judge_model": "fake-added-pair",
                    "decisions": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "action": "no_link",
                            "reason_text": "The shortlisted orientation is not the intended relation.",
                            "truth_basis": "visible_context",
                        }
                    ],
                    "new_associations": [
                        {
                            "action": "add",
                            "source_bead": source,
                            "target_bead": target,
                            "relationship": "supports",
                            "confidence": 0.88,
                            "reason_text": "The visible source supports the visible target.",
                            "truth_basis": "bounded_visible_context",
                            "evidence_bead_ids": [source, target],
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1")
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1")
            outside = _add_test_bead(store, type="context", title="Outside", summary=["outside"], session_id="s2")

            inside = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                judge=AddedPairJudge(None),
            )
            outside_result = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                judge=AddedPairJudge(outside),
            )
            quarantine = _jsonl_rows(td, "association-quarantine.jsonl")
            supports = _assocs(td, "supports")

        self.assertTrue(inside.get("ok"), inside)
        self.assertEqual(1, len(supports))
        self.assertEqual("quarantined", outside_result.get("status"))
        self.assertIn("pair_outside_bounded_context", quarantine[-1].get("reasons") or [])

    def test_invalid_judge_output_is_quarantined_without_edge(self):
        class BadRelationJudge:
            def review(self, context):
                candidate = (context.get("candidates") or [])[0]
                return {
                    "contract": "memory.association_judge.v2",
                    "run_id": context["run_id"],
                    "judge_model": "fake-bad",
                    "decisions": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "action": "modify",
                            "source_bead": candidate["source_bead"],
                            "target_bead": candidate["target_bead"],
                            "relationship": "not_a_relation",
                            "confidence": 0.8,
                            "reason_text": "Bad relation.",
                            "truth_basis": "test",
                            "evidence_bead_ids": candidate.get("evidence_bead_ids") or [],
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=BadRelationJudge(),
            )
            self.assertFalse(out.get("ok"), out)
            self.assertEqual("quarantined", out.get("status"))
            self.assertEqual([], _assocs(td, "not_a_relation"))
            self.assertTrue((Path(td) / ".beads" / "events" / "association-quarantine.jsonl").exists())

    def test_partial_judge_result_fails_run_with_pending_bead(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
                trigger="operator",
                judge=PartialFakeAssociationJudge(),
            )
            self.assertFalse(out.get("ok"), out)
            self.assertEqual("failed", out.get("status"))
            self.assertEqual("pending_judge", (out.get("association_state_by_bead") or {}).get(second))
            self.assertEqual(1, (out.get("counts") or {}).get("pending_judge"))
            self.assertEqual(1, (out.get("counts") or {}).get("failed"))
            self.assertIn("unresolved_judge_decision", json.dumps(out.get("errors") or []))

    def test_association_proposals_append_valid_and_quarantine_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = apply_association_proposals(
                td,
                associations=[
                    {
                        "source_bead_id": second,
                        "target_bead_id": first,
                        "relationship": "supports",
                        "confidence": 0.9,
                        "reason_text": "Second supports first.",
                    },
                    {
                        "source_bead_id": second,
                        "target_bead_id": first,
                        "relationship": "supports",
                    },
                ],
            )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(1, out.get("appended"))
            self.assertEqual(1, out.get("quarantined"))
            self.assertTrue((Path(td) / ".beads" / "events" / "association-quarantine.jsonl").exists())

    def test_association_proposal_run_tracks_states_per_source_bead(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            target = _add_test_bead(store, type="context", title="Target", summary=["target"], session_id="s1", source_turn_ids=["t1"])
            source_ok = _add_test_bead(store, type="context", title="Source OK", summary=["ok"], session_id="s1", source_turn_ids=["t2"])
            source_bad = _add_test_bead(store, type="context", title="Source Bad", summary=["bad"], session_id="s1", source_turn_ids=["t3"])

            out = apply_association_proposals(
                td,
                run_id="arun-proposals",
                associations=[
                    {
                        "source_bead_id": source_ok,
                        "target_bead_id": target,
                        "relationship": "supports",
                        "confidence": 0.9,
                        "reason_text": "The source supports the target.",
                    },
                    {
                        "source_bead_id": source_bad,
                        "target_bead_id": target,
                        "relationship": "supports",
                    },
                ],
            )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(1, out.get("appended"))
            self.assertEqual(1, out.get("quarantined"))

            run = get_association_run(td, "arun-proposals")
            self.assertTrue(run.get("ok"), run)
            states = (run.get("run") or {}).get("association_state_by_bead") or {}
            self.assertEqual("linked", states.get(source_ok))
            self.assertEqual("quarantined", states.get(source_bad))

    def test_session_coverage_queue_key_includes_resolved_bead_set(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            first_enqueued = enqueue_association_coverage(td, session_id="s1", trigger="session_flush")
            self.assertTrue(first_enqueued.get("ok"), first_enqueued)
            self.assertFalse((first_enqueued.get("queue") or {}).get("duplicate"), first_enqueued)

            third = _add_test_bead(store, type="context", title="Third", summary=["third"], session_id="s1", source_turn_ids=["t3"])
            second_enqueued = enqueue_association_coverage(td, session_id="s1", trigger="session_flush")
            self.assertTrue(second_enqueued.get("ok"), second_enqueued)
            self.assertFalse((second_enqueued.get("queue") or {}).get("duplicate"), second_enqueued)
            self.assertNotEqual(first_enqueued.get("queued_job_id"), second_enqueued.get("queued_job_id"))

            queue_path = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(2, len(queue))
            self.assertIn(second, (queue[0].get("payload") or {}).get("bead_ids") or [])
            self.assertIn(third, (queue[1].get("payload") or {}).get("bead_ids") or [])

    def test_flush_enqueues_session_association_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            turn = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[
                    {"speaker": "user", "role": "user", "content": "remember this"},
                    {"speaker": "assistant", "role": "assistant", "content": "captured"},
                ],
            )
            self.assertTrue(turn.get("ok"))
            out = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(out.get("ok"))
            coverage = out.get("association_coverage") or {}
            self.assertTrue(coverage.get("ok"), coverage)
            self.assertEqual("completed", coverage.get("status"))
            self.assertGreaterEqual((coverage.get("counts") or {}).get("skipped", 0), 1)
            self.assertTrue(coverage.get("run_id"))

    def test_association_summary_candidates_and_decision_contract(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            run = run_association_coverage(
                td,
                bead_ids=[second],
                candidate_bead_ids=[first],
            )
            self.assertTrue(run.get("ok"), run)
            self.assertEqual("pending_judge", run.get("status"))

            pending = list_association_candidates(td, status="pending_judge", limit=10)
            self.assertTrue(pending.get("ok"), pending)
            self.assertGreaterEqual(pending.get("count"), 1)
            candidate = pending["results"][0]
            self.assertEqual("Second", candidate.get("source_title"))
            self.assertEqual("First", candidate.get("target_title"))

            summary = association_coverage_summary(td, limit=10)
            self.assertTrue(summary.get("ok"), summary)
            self.assertEqual(2, summary.get("eligible_bead_count"))
            self.assertEqual(0, summary.get("active_association_count"))
            self.assertEqual(2, summary.get("isolated_eligible_bead_count"))
            self.assertGreaterEqual((summary.get("candidate_status_counts") or {}).get("pending_judge") or 0, 1)

            decided = decide_association_candidate(
                td,
                candidate_id=str(candidate.get("candidate_id") or ""),
                action="accept",
                relationship="follows",
                direction="source_to_target",
                confidence=0.9,
                reason_text="The second bead follows the first in the visible session sequence.",
                truth_basis="unit_test_operator_review",
                evidence_bead_ids=[second, first],
                reviewer="qa",
            )
            self.assertTrue(decided.get("ok"), decided)
            self.assertEqual("linked", decided.get("status"))
            self.assertTrue(decided.get("association_ids"))

            linked = list_association_candidates(td, status="linked", limit=10)
            self.assertEqual(1, linked.get("count"))
            after = association_coverage_summary(td, limit=10)
            self.assertEqual(1, after.get("active_association_count"))
            self.assertLess(after.get("isolated_eligible_bead_count"), 2)

    def test_stored_v1_candidate_is_readable_but_cannot_supply_semantic_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1")
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1")
            candidate_id = "cand-legacy-v1"
            events_dir = Path(td) / ".beads" / "events"
            events_dir.mkdir(parents=True, exist_ok=True)
            with (events_dir / "association-candidates.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "schema": "core_memory.association_candidates.v1",
                            "recorded_at": "2026-07-10T12:00:00Z",
                            "run_id": "arun-legacy-v1",
                            "candidates": [
                                {
                                    "candidate_id": candidate_id,
                                    "source_bead": second,
                                    "target_bead": first,
                                    "proposed_relationship": "follows",
                                    "proposed_direction": "source_to_target",
                                    "confidence_prior": 0.99,
                                }
                            ],
                        }
                    )
                    + "\n"
                )

            listed = list_association_candidates(td, limit=50)
            legacy = next(row for row in listed.get("results") or [] if row.get("candidate_id") == candidate_id)
            incomplete = decide_association_candidate(
                td,
                candidate_id=candidate_id,
                action="accept",
                truth_basis="legacy_operator_review",
            )
            explicit = decide_association_candidate(
                td,
                candidate_id=candidate_id,
                action="accept",
                relationship="follows",
                direction="source_to_target",
                confidence=0.9,
                reason_text="The second bead follows the first in the visible session.",
                truth_basis="legacy_operator_review",
                evidence_bead_ids=[second, first],
            )

        self.assertEqual("follows", legacy.get("proposed_relationship"))
        self.assertFalse(incomplete.get("ok"))
        self.assertIn("missing_agent_relationship", incomplete.get("validation_errors") or [])
        self.assertTrue(explicit.get("ok"), explicit)

    def test_sweep_mode_selects_eligible_beads_without_client_ids(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = enqueue_association_coverage(
                td,
                trigger="operator",
                run_inline=True,
                sweep=True,
                sweep_mode="all",
                sweep_limit=1,
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("sweep"))
            self.assertEqual("all", out.get("sweep_mode"))
            self.assertEqual(1, len(out.get("bead_ids") or []))
            self.assertIn((out.get("bead_ids") or [])[0], {first, second})
            self.assertFalse(out.get("sweep_complete"))
            self.assertTrue(out.get("next_sweep_cursor"))

            done = enqueue_association_coverage(
                td,
                trigger="operator",
                run_inline=True,
                sweep=True,
                sweep_mode="all",
                sweep_cursor=str(out.get("next_sweep_cursor") or ""),
                sweep_limit=10,
            )
            self.assertTrue(done.get("ok"), done)
            self.assertTrue(done.get("sweep_complete"))

    def test_http_association_endpoints(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            store = MemoryStore(root)
            first = _add_test_bead(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add_test_bead(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])
            client = TestClient(app)
            envelope_ref = {
                "schema": "core_memory.source_ingest_envelope.v1",
                "envelope_id": "env-http-association-source",
                "boundary_type": "DocumentImported",
                "ingest_batch_id": "batch-http-association-source",
                "source_object_id": "doc-http-association-source",
            }

            created = client.post(
                "/v1/memory/association-runs",
                json={
                    "root": root,
                    "bead_ids": [second],
                    "candidate_bead_ids": [first],
                    "run_inline": True,
                    "source_ingest_envelope_refs": [envelope_ref],
                },
            )
            self.assertEqual(200, created.status_code)
            data = created.json()
            self.assertTrue(data.get("ok"), data)
            self.assertEqual("pending_judge", data.get("status"))
            run_id = data.get("run_id")

            fetched = client.get(f"/v1/memory/association-runs/{run_id}", params={"root": root})
            self.assertEqual(200, fetched.status_code)
            self.assertEqual(run_id, (fetched.json().get("run") or {}).get("run_id"))
            self.assertIn(
                "env-http-association-source",
                {ref.get("envelope_id") for ref in ((fetched.json().get("run") or {}).get("source_ingest_envelope_refs") or [])},
            )

            summary = client.get("/v1/memory/association-coverage/summary", params={"root": root})
            self.assertEqual(200, summary.status_code)
            self.assertEqual("memory.association_coverage_summary.v1", summary.json().get("contract"))

            candidates = client.get(
                "/v1/memory/association-candidates",
                params={"root": root, "status": "pending_judge", "limit": 10},
            )
            self.assertEqual(200, candidates.status_code)
            self.assertGreaterEqual(candidates.json().get("count"), 1)

            candidate_id = ((candidates.json().get("results") or [{}])[0] or {}).get("candidate_id")
            decided = client.post(
                f"/v1/memory/association-candidates/{candidate_id}/decide",
                json={
                    "root": root,
                    "action": "reject",
                    "reason_text": "The pair has no supported semantic relationship.",
                    "truth_basis": "unit_test_no_link",
                },
            )
            self.assertEqual(200, decided.status_code)
            self.assertEqual("no_supported_links", decided.json().get("status"))

            inspect = client.get(f"/v1/memory/inspect/beads/{second}", params={"root": root})
            self.assertEqual(200, inspect.status_code)
            coverage = ((inspect.json().get("bead") or {}).get("association_coverage") or {})
            self.assertEqual("no_supported_links", coverage.get("state"))

    def test_on_bead_committed_store_hook_queues_association_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = store.add_bead(
                type="context",
                title="Hooked",
                summary=["hooked"],
                session_id="s1",
                source_turn_ids=["t1"],
                retrieval_eligible=True,
            )

            runs = _jsonl_rows(td, "association-runs.jsonl")
            self.assertEqual(1, len(runs))
            self.assertEqual("queued", runs[0].get("status"))
            self.assertEqual([bead], runs[0].get("bead_ids"))
            self.assertEqual("bead_committed", runs[0].get("trigger"))
            self.assertTrue(runs[0].get("association_queued"))

            queue_path = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(queue))
            self.assertEqual("association-pass", queue[0].get("kind"))
            self.assertEqual([bead], (queue[0].get("payload") or {}).get("bead_ids"))

            extra = on_bead_committed(td, bead, trigger="operator", source="test", run_inline=True, judge=RejectingFakeAssociationJudge())
            self.assertTrue(extra.get("ok"), extra)
            self.assertEqual("no_supported_links", (extra.get("association_state_by_bead") or {}).get(bead))

    def test_association_run_requires_beads_or_session(self):
        with tempfile.TemporaryDirectory() as td:
            out = enqueue_association_coverage(td, trigger="operator")
            self.assertFalse(out.get("ok"))
            self.assertEqual("association_run_requires_bead_ids_or_session_id", out.get("error"))
            self.assertFalse(get_association_run(td, "missing").get("ok"))


if __name__ == "__main__":
    unittest.main()
