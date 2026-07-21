"""Bead → storyline → goal quality chain.

Covers the fixes for the "poorly named storylines / templated goals / thin
initial beads" failure chain:

- candidate refinement retitles pending narrative + goal candidates via the
  semantic runtime, fail-open, preserving the deterministic template
- an accepted narrative's reviewed title names the storyline in the
  projection (label prefers overlay title, backbone label otherwise)
- accepting a goal_candidate with apply=True mints a real Goal Bead through
  the SOUL lifecycle and links supporting behavior beads (previously this
  fell into the association path and always failed)
- external-evidence ingest honors CORE_MEMORY_EXTERNAL_EVIDENCE_BEAD_JUDGE_MODE
  and authors missing summary/entities/topics, idempotently
- the full-schema bead-field judge compat path requests the standard tier
- heuristic entity fallbacks no longer promote lowercase sentence fragments
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core_memory.graph.storylines import derive_storylines, read_active_overlays
from core_memory.persistence.semantic_task_receipts import record_semantic_task_run
from core_memory.runtime.dreamer.candidates import (
    _read_candidates,
    _write_candidates,
    decide_dreamer_candidate,
    list_dreamer_candidates,
)
from core_memory.runtime.dreamer.convergence import enqueue_narrative_candidates
from core_memory.runtime.dreamer.refinement import refine_pending_candidates
from core_memory.runtime.ingest.external_evidence import ingest_external_evidence
from core_memory.schema.semantic_tasks import SemanticTaskRequest, SemanticTaskResult


class FakeSemanticRuntime:
    def __init__(self, output_json: dict | None, *, ok: bool = True):
        self.output_json = output_json
        self.ok = ok
        self.requests: list[SemanticTaskRequest] = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        result = SemanticTaskResult(
            task_id="fake-task-1",
            task_type=request.task_type,
            ok=self.ok,
            status="succeeded" if self.ok else "unavailable",
            output_json=self.output_json if self.ok else None,
            prompt_version=request.prompt_version,
            rubric_version=request.rubric_version,
            output_schema=request.output_schema,
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
            evidence_refs=list(request.evidence_refs or []),
            error="" if self.ok else "unavailable",
            metadata=dict(request.metadata or {}),
        )
        if request.root:
            row = record_semantic_task_run(str(request.root), request, result)
            return replace(result, receipt_id=str(row.get("receipt_id") or ""))
        return result


def _write_index(root: Path, beads: dict, associations: list) -> None:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": associations}), encoding="utf-8"
    )


def _bead(title: str, created_at: str, *, type_: str = "context", entities: list | None = None) -> dict:
    return {
        "type": type_,
        "title": title,
        "summary": [title],
        "session_id": "s1",
        "created_at": created_at,
        "retrieval_eligible": True,
        "status": "open",
        "entities": entities or [],
    }


def _convergent_fixture(root: Path) -> None:
    beads = {
        "bead-AAAAAAAAAAA1": _bead("kickoff", "2026-01-01T00:00:00+00:00", entities=["acme"]),
        "bead-AAAAAAAAAAA2": _bead("acme demo on pipeline", "2026-02-01T00:00:00+00:00", entities=["acme", "pipeline"]),
        "bead-AAAAAAAAAAA3": _bead("pipeline fix for acme", "2026-03-01T00:00:00+00:00", entities=["acme", "pipeline"]),
        "bead-GOAL00000001": _bead("win acme", "2026-01-05T00:00:00+00:00", type_="goal", entities=["acme"]),
    }
    _write_index(root, beads, [])


def _goal_candidate_row(cid: str = "dc-goal00000001") -> dict:
    return {
        "id": cid,
        "created_at": "2026-06-01T00:00:00+00:00",
        "status": "pending",
        "hypothesis_type": "goal_candidate",
        "proposal_family": "goal",
        "goal_theme": "invoice reconciliation",
        "title": "Recurring focus: invoice reconciliation",
        "statement": "Repeated behavior involving 'invoice reconciliation' across 2 sessions (3 decisions/outcomes) suggests a latent goal.",
        "supporting_bead_ids": ["bead-AAAAAAAAAAA2", "bead-AAAAAAAAAAA3"],
        "occurrence_count": 3,
        "session_count": 2,
        "run_metadata": {"run_id": "run-1", "source": "test"},
    }


class TestCandidateRefinement(unittest.TestCase):
    def test_refinement_titles_pending_candidates_and_keeps_template(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            enqueue_narrative_candidates(root)
            rows = _read_candidates(root)
            narrative_id = next(r["id"] for r in rows if r["hypothesis_type"] == "narrative_candidate")
            template_statement = next(
                r["statement"] for r in rows if r["hypothesis_type"] == "narrative_candidate"
            )
            rows.append(_goal_candidate_row())
            _write_candidates(root, rows)

            runtime = FakeSemanticRuntime(
                {
                    "contract": "memory.dreamer_candidate_refinement.v1",
                    "refinements": [
                        {
                            "candidate_id": narrative_id,
                            "title": "Acme pipeline recovery arc",
                            "statement": "Work on Acme keeps converging on the pipeline: a demo, a fix, and the win-acme goal all thread the same beads.",
                        },
                        {
                            "candidate_id": "dc-goal00000001",
                            "title": "Keep invoices reconciled every close",
                            "statement": "Two sessions of decisions and outcomes about invoice reconciliation point at an unstated standing goal.",
                        },
                        {"candidate_id": "dc-unknown", "title": "Should be ignored xx", "statement": "Not a known candidate row in this queue."},
                    ],
                }
            )
            with patch(
                "core_memory.runtime.dreamer.refinement.get_semantic_task_runtime",
                return_value=runtime,
            ):
                out = refine_pending_candidates(root, run_id="run-1", source="unit")

            self.assertTrue(out["ok"], out)
            self.assertEqual(2, out["refined"])
            self.assertEqual(1, len(runtime.requests))
            self.assertEqual("dreamer_research", runtime.requests[0].task_type)
            self.assertEqual("candidate_only", runtime.requests[0].authority_boundary)

            refined = {r["id"]: r for r in _read_candidates(root)}
            narrative = refined[narrative_id]
            self.assertEqual("Acme pipeline recovery arc", narrative["title"])
            self.assertEqual(template_statement, narrative["statement_template"])
            self.assertIn("converging on the pipeline", narrative["statement"])
            self.assertTrue(narrative["refined_at"])
            self.assertEqual("candidate_refinement", narrative["refinement"]["role"])
            goal = refined["dc-goal00000001"]
            self.assertEqual("Keep invoices reconciled every close", goal["title"])

    def test_refinement_fails_open(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            enqueue_narrative_candidates(root)
            before = _read_candidates(root)

            runtime = FakeSemanticRuntime(None, ok=False)
            with patch(
                "core_memory.runtime.dreamer.refinement.get_semantic_task_runtime",
                return_value=runtime,
            ):
                out = refine_pending_candidates(root, run_id="run-2", source="unit")

            self.assertFalse(out["ok"])
            self.assertEqual(0, out["refined"])
            self.assertEqual(before, _read_candidates(root))


class TestStorylineNaming(unittest.TestCase):
    def test_accepted_refined_title_names_the_storyline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            enqueue_narrative_candidates(root)
            rows = _read_candidates(root)
            for row in rows:
                if row["hypothesis_type"] == "narrative_candidate":
                    row["title"] = "Acme pipeline recovery arc"
                    row["statement"] = "Acme work keeps converging on the pipeline thread."
            _write_candidates(root, rows)
            cid = next(r["id"] for r in rows if r["hypothesis_type"] == "narrative_candidate")

            out = decide_dreamer_candidate(
                root=root, candidate_id=cid, decision="accept", reviewer="test", apply=True
            )
            self.assertTrue(out["ok"], out)

            overlays = read_active_overlays(root)
            self.assertEqual(1, len(overlays))
            self.assertEqual("Acme pipeline recovery arc", overlays[0]["title"])

            projection = derive_storylines(root)
            named = [s for s in projection["storylines"] if s["overlays"]]
            self.assertTrue(named)
            for storyline in named:
                self.assertEqual("Acme pipeline recovery arc", storyline["label"])
                self.assertEqual("Acme pipeline recovery arc", storyline["title"])
            unnamed = [s for s in projection["storylines"] if not s["overlays"]]
            for storyline in unnamed:
                self.assertEqual(storyline["backbone"]["label"], storyline["label"])
                self.assertEqual("", storyline["title"])


class TestGoalCandidateApply(unittest.TestCase):
    def test_accept_apply_creates_goal_bead_and_links_support(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _convergent_fixture(root)
            _write_candidates(root, [_goal_candidate_row()])

            out = decide_dreamer_candidate(
                root=root,
                candidate_id="dc-goal00000001",
                decision="accept",
                reviewer="reviewer-1",
                apply=True,
            )
            self.assertTrue(out["ok"], out)
            applied = out["applied"]
            self.assertEqual("goal_bead_created", applied["application_mode"])
            goal_bead_id = applied["goal_bead_id"]
            self.assertTrue(goal_bead_id)
            self.assertEqual("candidate", applied["goal_status"])
            self.assertEqual(
                ["bead-AAAAAAAAAAA2", "bead-AAAAAAAAAAA3"], sorted(applied["linked_bead_ids"])
            )

            index = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            goal_bead = (index.get("beads") or {}).get(goal_bead_id)
            self.assertIsNotNone(goal_bead)
            self.assertEqual("goal", goal_bead["type"])
            self.assertEqual("Recurring focus: invoice reconciliation", goal_bead["title"])
            self.assertEqual("soul-goals:self", goal_bead["session_id"])
            support_edges = [
                a
                for a in (index.get("associations") or [])
                if str(a.get("target_bead") or "") == goal_bead_id
                and str(a.get("relationship") or "") == "supports"
            ]
            self.assertEqual(2, len(support_edges))

            retry = decide_dreamer_candidate(
                root=root,
                candidate_id="dc-goal00000001",
                decision="accept",
                reviewer="reviewer-1",
                apply=True,
            )
            self.assertTrue(retry["ok"])
            self.assertEqual("already_applied", retry["applied"]["application_mode"])
            self.assertEqual(goal_bead_id, retry["applied"]["goal_bead_id"])
            index_after = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            goal_beads = [
                b
                for b in (index_after.get("beads") or {}).values()
                if str(b.get("type") or "") == "goal" and str(b.get("session_id") or "").startswith("soul-goals:")
            ]
            self.assertEqual(1, len(goal_beads))


def _document_payload(**overrides):
    payload = {
        "data_type_flag": "document",
        "title": "Q2 Vendor Review.pdf",
        "summary": ["Acme Corp remains the largest vendor; renewal is due in August and pricing"],
        "detail": (
            "Acme Corp remains the largest vendor; renewal is due in August and pricing "
            "increased 12% year over year. Finance recommends renegotiating the support tier "
            "before renewal and consolidating the Beta Logistics contract into the same cycle."
        ),
        "source_id": "src_docs",
        "source_event_id": "evt_doc_001",
        "source_system": "owned_ingestion",
        "source_kind": "document",
        "document_id": "doc_001",
        "raw_source_object_id": "raw_001",
        "document_name": "Q2 Vendor Review.pdf",
        "mime_type": "application/pdf",
        "core_memory_unifying_id": "vendor_review_q2",
        "hydration_ref": {"store": "host_app", "ref": "doc_001"},
    }
    payload.update(overrides)
    return payload


class TestExternalEvidenceEnrichment(unittest.TestCase):
    def test_enrichment_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = ingest_external_evidence(td, _document_payload())
            self.assertTrue(receipt["ok"], receipt)
            self.assertFalse(receipt["enrichment"]["attempted"])
            index = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = (index.get("beads") or {}).get(receipt["bead_id"])
            self.assertEqual([], list(bead.get("entities") or []))

    def test_enrichment_fills_structural_gaps_and_stays_idempotent(self):
        runtime = FakeSemanticRuntime(
            {
                "title": "Acme Corp renewal: renegotiate before August",
                "summary": [
                    "Acme Corp is the largest vendor; renewal is due in August after a 12% price increase.",
                    "Finance recommends renegotiating support and consolidating Beta Logistics into the cycle.",
                ],
                "entities": ["Acme Corp", "Beta Logistics", "Finance"],
                "topics": ["vendor renewal", "pricing"],
            }
        )
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ", {"CORE_MEMORY_EXTERNAL_EVIDENCE_BEAD_JUDGE_MODE": "llm"}
        ), patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ):
            receipt = ingest_external_evidence(td, _document_payload())
            self.assertTrue(receipt["ok"], receipt)
            enrichment = receipt["enrichment"]
            self.assertTrue(enrichment["attempted"])
            self.assertTrue(enrichment["ok"], enrichment)
            self.assertIn("entities", enrichment["fields"])

            self.assertEqual(1, len(runtime.requests))
            self.assertEqual("bead_field_judge", runtime.requests[0].task_type)
            self.assertEqual("standard", runtime.requests[0].model_tier)

            index = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = (index.get("beads") or {}).get(receipt["bead_id"])
            self.assertEqual("Acme Corp renewal: renegotiate before August", bead["title"])
            self.assertEqual(["Acme Corp", "Beta Logistics", "Finance"], bead["entities"])
            self.assertEqual(["vendor renewal", "pricing"], bead["topics"])
            self.assertEqual(2, len(bead["summary"]))
            self.assertIn("semantic_enriched", bead["tags"])
            self.assertTrue(bead["content_signature_raw"])

            # Re-delivery of the same source event: idempotent, no new task.
            again = ingest_external_evidence(td, _document_payload())
            self.assertEqual("already_exists", again["status"])
            self.assertEqual(1, len(runtime.requests))

            # Same raw content under a NEW event id must also dedupe — the raw
            # content signature is compared, not the enriched stored fields.
            renamed_event = ingest_external_evidence(
                td, _document_payload(source_event_id="evt_doc_001b")
            )
            self.assertEqual("already_exists", renamed_event["status"])

    def test_enrichment_never_overwrites_caller_semantics(self):
        runtime = FakeSemanticRuntime(
            {
                "title": "Judge title that must not win",
                "summary": ["Judge summary that must not win."],
                "entities": ["JudgeEntity"],
                "topics": ["judge topic"],
            }
        )
        payload = _document_payload(
            title="Acme contract analysis",
            summary=["Reviewed conclusions about the Acme renewal terms."],
            entities=["Acme Corp"],
            topics=["contracts"],
        )
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ", {"CORE_MEMORY_EXTERNAL_EVIDENCE_BEAD_JUDGE_MODE": "llm"}
        ), patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ):
            receipt = ingest_external_evidence(td, payload)
            self.assertTrue(receipt["ok"], receipt)
            self.assertFalse(receipt["enrichment"]["attempted"])
            index = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = (index.get("beads") or {}).get(receipt["bead_id"])
            self.assertEqual("Acme contract analysis", bead["title"])
            self.assertEqual(["Acme Corp"], bead["entities"])

    def test_enrichment_fails_open_to_structural_bead(self):
        runtime = FakeSemanticRuntime(None, ok=False)
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ", {"CORE_MEMORY_EXTERNAL_EVIDENCE_BEAD_JUDGE_MODE": "llm"}
        ), patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ):
            receipt = ingest_external_evidence(td, _document_payload())
            self.assertTrue(receipt["ok"], receipt)
            self.assertTrue(receipt["enrichment"]["attempted"])
            self.assertFalse(receipt["enrichment"]["ok"])
            index = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = (index.get("beads") or {}).get(receipt["bead_id"])
            self.assertEqual("Q2 Vendor Review.pdf", bead["title"])


class TestBeadFieldJudgeTier(unittest.TestCase):
    def test_full_schema_compat_judge_requests_standard_tier(self):
        from core_memory.policy.bead_judge import judge_bead_fields

        runtime = FakeSemanticRuntime(
            {
                "schema_version": "agent_authored_updates.v1",
                "beads_create": [
                    {
                        "creation_role": "current_turn",
                        "type": "decision",
                        "title": "Use Redis for cache invalidation",
                        "summary": ["Redis selected for cache invalidation."],
                        "entities": ["Redis"],
                        "topics": ["cache"],
                    }
                ],
                "associations": [],
                "reviewed_beads": [],
            }
        )
        with patch(
            "core_memory.policy.bead_judge.get_semantic_task_runtime",
            return_value=runtime,
        ):
            out = judge_bead_fields("Should we use Redis?", "Yes — Redis for cache invalidation.", mode="llm")
        self.assertEqual("llm", out["judge"]["mode"])
        self.assertEqual(1, len(runtime.requests))
        self.assertEqual("bead_field_judge", runtime.requests[0].task_type)
        self.assertEqual("standard", runtime.requests[0].model_tier)


class TestHeuristicEntityNoise(unittest.TestCase):
    def test_bead_judge_heuristic_entities_skip_sentence_fragments(self):
        from core_memory.policy.bead_judge import _heuristic_entities

        out = _heuristic_entities(
            "please fix the billing export for Acme Corp before tomorrow standup",
            "The billing export now retries; QuickBooks sync-v2 is unblocked.",
        )
        self.assertIn("Acme Corp", out)
        self.assertIn("QuickBooks", out)
        self.assertIn("sync-v2", out)
        for junk in ("please", "fix", "billing", "export", "before", "tomorrow", "standup", "retries"):
            self.assertNotIn(junk, [x.lower() for x in out])

    def test_engine_default_entities_have_no_lowercase_fallback(self):
        from core_memory.runtime.engine import _default_entities_from_text

        self.assertEqual([], _default_entities_from_text("hello there friend, checking in again"))
        out = _default_entities_from_text("Deploying Corestack ingestion for Midwest Muscle")
        self.assertIn("Corestack", out)
        self.assertIn("Midwest", out)
        self.assertNotIn("ingestion", [x.lower() for x in out])


class TestGoalDiscoveryTitles(unittest.TestCase):
    def test_latent_goal_candidates_carry_baseline_titles(self):
        from core_memory.runtime.dreamer.goal_discovery import enqueue_latent_goal_candidates

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {}
            for i, session in enumerate(["s1", "s1", "s2"], start=1):
                bead = _bead(
                    f"invoice decision {i}", f"2026-0{i}-01T00:00:00+00:00", type_="decision",
                    entities=["invoice reconciliation"],
                )
                bead["session_id"] = session
                beads[f"bead-GD{i:012d}"] = bead
            _write_index(root, beads, [])

            out = enqueue_latent_goal_candidates(root, run_id="gd-1")
            self.assertEqual(1, out["enqueued"], out)
            rows = list_dreamer_candidates(root=root, status="pending")["results"]
            goal_rows = [r for r in rows if r["hypothesis_type"] == "goal_candidate"]
            self.assertEqual(1, len(goal_rows))
            self.assertEqual("Recurring focus: invoice reconciliation", goal_rows[0]["title"])


if __name__ == "__main__":
    unittest.main()
