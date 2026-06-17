import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.associations.coverage import (
    apply_association_proposals,
    association_coverage_summary,
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
    return store.add_bead(**kwargs)


class AcceptingFakeAssociationJudge:
    def review(self, context):
        return {
            "contract": "memory.association_judge.v1",
            "run_id": context["run_id"],
            "judge_model": "fake-accepting",
            "prompt_version": context["prompt_version"],
            "rubric_version": context["rubric_version"],
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "accept",
                    "confidence": candidate.get("confidence_prior", 0.9),
                    "reason_text": f"Accepted {candidate['proposed_relationship']} from candidate evidence.",
                    "truth_basis": "fake_judge_evidence_review",
                    "evidence_bead_ids": candidate.get("evidence_bead_ids") or [],
                    "evidence_refs": candidate.get("evidence_refs") or [],
                }
                for candidate in context.get("candidates", [])
            ],
            "reviewed_beads": [{"bead_id": bid, "association_state": "linked"} for bid in context.get("source_bead_ids", [])],
        }


class RejectingFakeAssociationJudge:
    def review(self, context):
        return {
            "contract": "memory.association_judge.v1",
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
            "contract": "memory.association_judge.v1",
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
            "contract": "memory.association_judge.v1",
            "run_id": context["run_id"],
            "judge_model": "fake-invalid",
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "accept",
                    "relationship": "not_a_relation",
                    "confidence": 0.9,
                    "reason_text": "Invalid relation.",
                }
            ],
        }


class PartialFakeAssociationJudge:
    def review(self, context):
        return {
            "contract": "memory.association_judge.v1",
            "run_id": context["run_id"],
            "judge_model": "fake-partial",
            "prompt_version": context["prompt_version"],
            "rubric_version": context["rubric_version"],
            "decisions": [],
            "reviewed_beads": [],
        }


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
    }
    payload.update(overrides)
    return payload


class TestAssociationCoverage(unittest.TestCase):
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

    def test_invalid_judge_output_is_quarantined_without_edge(self):
        class BadRelationJudge:
            def review(self, context):
                candidate = (context.get("candidates") or [])[0]
                return {
                    "contract": "memory.association_judge.v1",
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
            self.assertEqual("queued", coverage.get("status"))
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
                truth_basis="unit_test_operator_review",
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

            created = client.post(
                "/v1/memory/association-runs",
                json={"root": root, "bead_ids": [second], "candidate_bead_ids": [first], "run_inline": True},
            )
            self.assertEqual(200, created.status_code)
            data = created.json()
            self.assertTrue(data.get("ok"), data)
            self.assertEqual("pending_judge", data.get("status"))
            run_id = data.get("run_id")

            fetched = client.get(f"/v1/memory/association-runs/{run_id}", params={"root": root})
            self.assertEqual(200, fetched.status_code)
            self.assertEqual(run_id, (fetched.json().get("run") or {}).get("run_id"))

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
                json={"root": root, "action": "reject", "truth_basis": "unit_test_no_link"},
            )
            self.assertEqual(200, decided.status_code)
            self.assertEqual("no_supported_links", decided.json().get("status"))

            inspect = client.get(f"/v1/memory/inspect/beads/{second}", params={"root": root})
            self.assertEqual(200, inspect.status_code)
            coverage = ((inspect.json().get("bead") or {}).get("association_coverage") or {})
            self.assertEqual("no_supported_links", coverage.get("state"))

    def test_on_bead_committed_store_hook_records_deferred_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = store.add_bead(type="context", title="Hooked", summary=["hooked"], session_id="s1", source_turn_ids=["t1"])

            runs = _jsonl_rows(td, "association-runs.jsonl")
            self.assertEqual(1, len(runs))
            self.assertEqual("deferred", runs[0].get("status"))
            self.assertEqual([bead], runs[0].get("bead_ids"))
            self.assertEqual("bead_committed", runs[0].get("trigger"))
            self.assertFalse(runs[0].get("association_queued"))

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
