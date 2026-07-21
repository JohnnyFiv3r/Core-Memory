# SEED_BACKFILL_ONESHOT — delete this test file when the seed-backfill pass is
# removed after the one-shot store cleanup (see the runbook#removal).
"""Seed-quality backfill: the one-shot cleanup pass over pre-fix stores.

Contract under test:
- dry-run reports junk entities / thin beads / detections and writes nothing
- apply strips non-meaningful entities, re-authors thin beads through the
  semantic runtime (fail-open per bead), rebuilds the entity registry, and
  auto-accepts only refiner-named narrative/goal candidates
- a backup snapshot of index.json is taken before any apply write
- reruns skip already-backfilled beads (seed_backfilled tag)
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core_memory.graph.storylines import derive_storylines
from core_memory.persistence.semantic_task_receipts import record_semantic_task_run
from core_memory.runtime.hygiene.seed_backfill import (
    clean_entity_list,
    is_meaningful_entity,
    run_seed_quality_backfill,
)
from core_memory.schema.semantic_tasks import SemanticTaskRequest, SemanticTaskResult


class FakeRuntime:
    """Returns bead enrichment for bead_field_judge and refinement for dreamer_research."""

    def __init__(self, *, ok: bool = True):
        self.ok = ok
        self.requests: list[SemanticTaskRequest] = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        output: dict | None = None
        if self.ok and request.task_type == "bead_field_judge":
            bead_id = str((request.metadata or {}).get("bead_id") or "")
            output = {
                "title": f"Acme invoice dispute follow-up ({bead_id[-4:]})",
                "summary": ["Acme Corp disputed the March invoice; finance agreed to reissue it."],
                "entities": ["Acme Corp", "March Invoice"],
                "topics": ["invoice dispute"],
            }
        elif self.ok and request.task_type == "dreamer_research":
            candidate_ids = [str(x) for x in ((request.metadata or {}).get("candidate_ids") or [])]
            output = {
                "contract": "memory.dreamer_candidate_refinement.v1",
                "refinements": [
                    {
                        "candidate_id": cid,
                        "title": f"Refined thread {index + 1}: Acme invoice recovery",
                        "statement": "Acme invoice work keeps threading the same beads across sessions.",
                    }
                    for index, cid in enumerate(candidate_ids)
                ],
            }
        result = SemanticTaskResult(
            task_id=f"seed-task-{len(self.requests)}",
            task_type=request.task_type,
            ok=self.ok,
            status="succeeded" if self.ok else "unavailable",
            output_json=output,
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


def _bead(
    title: str,
    created_at: str,
    *,
    type_: str = "context",
    entities: list | None = None,
    summary: list | None = None,
    detail: str = "",
    session_id: str = "s1",
) -> dict:
    return {
        "type": type_,
        "title": title,
        "summary": summary if summary is not None else [title],
        "detail": detail,
        "session_id": session_id,
        "created_at": created_at,
        "retrieval_eligible": True,
        "status": "open",
        "entities": entities or [],
        "tags": [],
    }


def _seed_store(root: Path) -> None:
    detail = (
        "Acme Corp disputed the March invoice after the price change. "
        "Finance reviewed the contract terms and agreed to reissue the invoice "
        "with the corrected rate before the April close."
    )
    beads = {
        # Junk-entity conversation beads (old heuristics): fragments + a real name.
        "bead-SEED0000001": _bead(
            "please fix the invoice for acme",
            "2026-05-01T00:00:00+00:00",
            type_="decision",
            entities=["please", "fix", "the", "invoice", "Acme Corp", "tests/pipeline", "a1b2c3d4e5f6a7b8"],
            detail=detail,
        ),
        "bead-SEED0000002": _bead(
            "acme follow-up outcome",
            "2026-05-08T00:00:00+00:00",
            type_="outcome",
            entities=["acme corp", "follow", "reissue"],
            detail=detail,
            session_id="s2",
        ),
        # Thin document-section bead: filename title, truncated summary, no entities.
        "bead-SEED0000003": {
            **_bead(
                "Q2 Vendor Review.pdf",
                "2026-05-15T00:00:00+00:00",
                type_="document_reference",
                entities=[],
                summary=[detail[:80]],
                detail=detail,
                session_id="s3",
            ),
            "document_name": "Q2 Vendor Review.pdf",
        },
        # Healthy bead: untouched by cleanup or enrichment.
        "bead-SEED0000004": _bead(
            "Renewal decision for Acme Corp",
            "2026-05-20T00:00:00+00:00",
            type_="decision",
            entities=["Acme Corp"],
            summary=["Chose to renegotiate the Acme Corp support tier before renewal."],
            session_id="s2",
        ),
    }
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": []}), encoding="utf-8"
    )


class TestEntityValidator(unittest.TestCase):
    def test_meaningful_entity_rules(self):
        for junk in (
            "please", "fix", "the", "tests/pipeline", "a1b2c3d4e5f6a7b8",
            "report.pdf", "v1.2.3", "2026-05-01", "user@example.com", "tmp",
        ):
            self.assertFalse(is_meaningful_entity(junk), junk)
        for good in ("Acme Corp", "QuickBooks", "sync-v2", "Midwest Muscle", "invoice reconciliation"):
            self.assertTrue(is_meaningful_entity(good), good)

    def test_clean_entity_list_dedupes_case_insensitively(self):
        out = clean_entity_list(["Acme Corp", "acme corp", "please", "QuickBooks"])
        self.assertEqual(["Acme Corp", "QuickBooks"], out)


class TestDryRun(unittest.TestCase):
    def test_dry_run_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_store(root)
            before = (root / ".beads" / "index.json").read_text(encoding="utf-8")

            out = run_seed_quality_backfill(root)

            self.assertTrue(out["ok"], out)
            self.assertFalse(out["applied"])
            self.assertGreaterEqual(out["entities"]["beads_touched"], 2)
            self.assertGreaterEqual(out["entities"]["entities_removed"], 5)
            self.assertGreaterEqual(out["enrichment"]["eligible"], 1)
            self.assertEqual(0, out["enrichment"].get("attempted", 0))
            self.assertNotIn("backup_path", out)
            self.assertEqual(before, (root / ".beads" / "index.json").read_text(encoding="utf-8"))


class TestApply(unittest.TestCase):
    def test_apply_cleans_enriches_rebuilds_and_seeds(self):
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ), patch(
            "core_memory.runtime.dreamer.refinement.get_semantic_task_runtime",
            return_value=runtime,
        ):
            root = Path(td)
            _seed_store(root)

            out = run_seed_quality_backfill(root, apply=True, max_goals=3)

            self.assertTrue(out["ok"], out)
            self.assertTrue(Path(out["backup_path"]).exists())
            backup = json.loads(Path(out["backup_path"]).read_text(encoding="utf-8"))
            self.assertIn("please", backup["beads"]["bead-SEED0000001"]["entities"])

            index = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            beads = index["beads"]

            # Junk entities stripped; real names normalized through the registry.
            self.assertEqual(["Acme Corp"], beads["bead-SEED0000001"]["entities"])
            self.assertEqual(["acme corp"], beads["bead-SEED0000002"]["entities"])

            # Thin document bead re-authored and tagged for rerun-skip.
            doc = beads["bead-SEED0000003"]
            self.assertNotEqual("Q2 Vendor Review.pdf", doc["title"])
            self.assertIn("Acme Corp", doc["entities"])
            self.assertIn("seed_backfilled", doc["tags"])
            self.assertGreaterEqual(out["enrichment"]["changed"], 1)

            # Healthy bead untouched.
            self.assertEqual(
                "Renewal decision for Acme Corp", beads["bead-SEED0000004"]["title"]
            )
            self.assertNotIn("seed_backfilled", beads["bead-SEED0000004"]["tags"])

            # Registry rebuilt: junk entities gone, Acme resolved to one entity.
            labels = {
                str(row.get("normalized_label") or "")
                for row in (index.get("entities") or {}).values()
            }
            self.assertNotIn("please", labels)
            self.assertTrue(any("acme" in label for label in labels), labels)

            # Seeding: refined goal candidates became Goal Beads; storylines named
            # when convergence exists.
            seeding = out["seeding"]
            self.assertGreaterEqual(len(seeding["accepted_goal_bead_ids"]), 1, seeding)
            goal_beads = [
                b for b in beads.values()
                if str(b.get("type") or "") == "goal"
            ] + [
                b
                for b in json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))["beads"].values()
                if str(b.get("type") or "") == "goal"
            ]
            self.assertTrue(goal_beads)

            projection = derive_storylines(root)
            self.assertTrue(projection["ok"])
            named = [s for s in projection["storylines"] if s.get("title")]
            for storyline in named:
                self.assertIn("Refined thread", storyline["label"])

    def test_apply_rerun_skips_backfilled_beads(self):
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ), patch(
            "core_memory.runtime.dreamer.refinement.get_semantic_task_runtime",
            return_value=runtime,
        ):
            root = Path(td)
            _seed_store(root)
            first = run_seed_quality_backfill(root, apply=True)
            self.assertGreaterEqual(first["enrichment"]["attempted"], 1)

            second = run_seed_quality_backfill(root, apply=True)
            self.assertTrue(second["ok"], second)
            self.assertEqual(0, second["enrichment"]["attempted"], second["enrichment"])

    def test_apply_survives_runtime_outage(self):
        runtime = FakeRuntime(ok=False)
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ), patch(
            "core_memory.runtime.dreamer.refinement.get_semantic_task_runtime",
            return_value=runtime,
        ):
            root = Path(td)
            _seed_store(root)

            out = run_seed_quality_backfill(root, apply=True)

            self.assertTrue(out["ok"], out)
            self.assertGreaterEqual(out["enrichment"]["failed"], 1)
            index = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            # Deterministic cleanup still landed even though the model was down.
            self.assertEqual(["Acme Corp"], index["beads"]["bead-SEED0000001"]["entities"])
            # No refined candidates -> nothing auto-accepted.
            self.assertEqual([], out["seeding"]["accepted_goal_bead_ids"])
            self.assertEqual([], out["seeding"]["accepted_overlay_ids"])


class TestRollbackSnapshots(unittest.TestCase):
    def test_fresh_store_reports_no_overlay_snapshot(self):
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ), patch(
            "core_memory.runtime.dreamer.refinement.get_semantic_task_runtime",
            return_value=runtime,
        ):
            root = Path(td)
            _seed_store(root)
            self.assertFalse((root / ".beads" / "overlays.jsonl").exists())

            out = run_seed_quality_backfill(root, apply=True)

            self.assertTrue(out["ok"], out)
            # No pre-existing overlay log -> rollback deletes overlays.jsonl.
            self.assertFalse(out["overlays_existed_before"])
            self.assertIsNone(out["overlays_backup_path"])

    def test_pre_existing_overlays_are_snapshotted_for_rollback(self):
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.policy.semantic_task_runtime.get_semantic_task_runtime",
            return_value=runtime,
        ), patch(
            "core_memory.runtime.dreamer.refinement.get_semantic_task_runtime",
            return_value=runtime,
        ):
            root = Path(td)
            _seed_store(root)
            overlays_file = root / ".beads" / "overlays.jsonl"
            overlays_file.write_text(
                json.dumps({"schema": "storyline_overlay.v1", "id": "ovl-preexisting"}) + "\n",
                encoding="utf-8",
            )
            original = overlays_file.read_text(encoding="utf-8")

            out = run_seed_quality_backfill(root, apply=True)

            self.assertTrue(out["ok"], out)
            self.assertTrue(out["overlays_existed_before"])
            snapshot_path = Path(out["overlays_backup_path"])
            self.assertTrue(snapshot_path.exists())
            # The snapshot captures the pre-seeding overlay log verbatim, so a
            # rollback restores exactly the state before this run appended.
            self.assertEqual(original, snapshot_path.read_text(encoding="utf-8"))
            self.assertNotIn("seed-backfill", snapshot_path.read_text(encoding="utf-8"))


class TestHttpSeedBackfillRoute(unittest.TestCase):
    def test_route_runs_dry_run(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_store(root)
            client = TestClient(app)
            response = client.post(
                "/v1/memory/hygiene/seed-backfill",
                json={"root": str(root), "apply": False},
            )
            self.assertEqual(200, response.status_code, response.text)
            body = response.json()
            self.assertTrue(body["ok"])
            self.assertFalse(body["applied"])
            self.assertGreaterEqual(body["entities"]["entities_removed"], 5)


if __name__ == "__main__":
    unittest.main()
