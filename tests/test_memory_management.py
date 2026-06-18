import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory import maintain, remove_bead, remove_source
from core_memory.integrations.mcp.registry import call_tool
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.associations.coverage import get_association_run
from core_memory.runtime.queue.jobs import run_async_jobs


def _index(root: str) -> dict:
    return json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))


def _event_rows(root: str) -> list[dict]:
    events_dir = Path(root) / ".beads" / "events"
    rows: list[dict] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def _add(store: MemoryStore, **kwargs):
    kwargs.setdefault("_association_coverage", False)
    return store.add_bead(**kwargs)


class TestMemoryManagement(unittest.TestCase):
    def test_remove_bead_prunes_active_graph_and_rebuild_honors_tombstone(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            keeper = _add(store, type="context", title="Keep", summary=["keep"], session_id="s1")
            mistake = _add(store, type="context", title="Mistake", summary=["wrong"], session_id="s1")
            assoc_id = store.link(keeper, mistake, "supports", "test")

            preview = remove_bead(root=td, bead_id=mistake, reason="user identified mistake")
            self.assertTrue(preview.get("ok"), preview)
            self.assertFalse(preview.get("applied"))
            self.assertEqual(1, preview.get("matched_count"))
            self.assertIn(mistake, (_index(td).get("beads") or {}))

            blocked = remove_bead(
                root=td,
                bead_id=mistake,
                reason="user identified mistake",
                apply=True,
                dry_run=False,
            )
            self.assertFalse(blocked.get("ok"), blocked)

            out = remove_bead(
                root=td,
                bead_id=mistake,
                reason="user identified mistake",
                actor="agent.chat",
                authority={"user_confirmed": True},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("applied"))
            self.assertEqual([mistake], out.get("removed_bead_ids"))

            idx = _index(td)
            self.assertIn(keeper, idx.get("beads") or {})
            self.assertNotIn(mistake, idx.get("beads") or {})
            self.assertEqual([], idx.get("associations") or [])
            self.assertIn(mistake, idx.get("removed_bead_ids") or [])

            removed_events = [row for row in _event_rows(td) if row.get("event_type") == "bead_removed"]
            self.assertEqual(1, len(removed_events))
            self.assertEqual(mistake, (removed_events[0].get("payload") or {}).get("bead_id"))
            self.assertIn(assoc_id, (removed_events[0].get("payload") or {}).get("removed_association_ids") or [])

            rebuilt = store.rebuild_index()
            self.assertNotIn(mistake, rebuilt.get("beads") or {})
            self.assertEqual([], rebuilt.get("associations") or [])
            self.assertIn(mistake, rebuilt.get("removed_bead_ids") or [])

    def test_remove_source_removes_document_and_section_beads(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            whole = _add(store, type="context", title="Doc", summary=["doc"], session_id="external", document_id="doc-1")
            section = _add(store, type="context", title="Doc section", summary=["section"], session_id="external", document_id="doc-1", section_refs=[{"section_id": "a"}])
            other = _add(store, type="context", title="Other", summary=["other"], session_id="external", document_id="doc-2")
            store.link(section, whole, "part_of", "section belongs to doc")

            out = remove_source(
                root=td,
                source={"document_id": "doc-1"},
                reason="source file removed",
                actor="source-cleanup",
                authority={"mode": "event_hook"},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(2, out.get("removed_count"))
            idx = _index(td)
            self.assertNotIn(whole, idx.get("beads") or {})
            self.assertNotIn(section, idx.get("beads") or {})
            self.assertIn(other, idx.get("beads") or {})
            self.assertEqual([], idx.get("associations") or [])

    def test_remove_source_limit_reports_truncation_and_apply_removes_all(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead_ids = [
                _add(store, type="context", title=f"Doc section {i}", summary=[str(i)], session_id="external", document_id="doc-many")
                for i in range(3)
            ]

            preview = remove_source(
                root=td,
                source={"document_id": "doc-many"},
                reason="source file removed",
                limit=2,
            )
            self.assertTrue(preview.get("ok"), preview)
            self.assertFalse(preview.get("applied"))
            self.assertTrue(preview.get("truncated"), preview)
            self.assertEqual(3, preview.get("matched_total"))
            self.assertEqual(3, preview.get("matched_count"))
            self.assertEqual(2, preview.get("preview_count"))
            self.assertEqual(1, preview.get("remaining_count"))
            for bead_id in bead_ids:
                self.assertIn(bead_id, (_index(td).get("beads") or {}))

            applied = remove_source(
                root=td,
                source={"document_id": "doc-many"},
                reason="source file removed",
                actor="source-cleanup",
                authority={"mode": "event_hook"},
                limit=2,
                apply=True,
                dry_run=False,
            )
            self.assertTrue(applied.get("ok"), applied)
            self.assertFalse(applied.get("truncated"), applied)
            self.assertEqual(3, applied.get("matched_total"))
            self.assertEqual(3, applied.get("removed_count"))
            self.assertEqual(0, applied.get("remaining_count"))
            for bead_id in bead_ids:
                self.assertNotIn(bead_id, (_index(td).get("beads") or {}))

    def test_remove_bead_retracts_configured_sync_target(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            with patch.dict(
                "os.environ",
                {
                    "CORE_MEMORY_SYNC_TARGETS": "obsidian",
                    "CORE_MEMORY_OBSIDIAN_VAULT": str(vault),
                    "CORE_MEMORY_GRAPH_BACKEND": "none",
                },
                clear=False,
            ):
                store = MemoryStore(td)
                bead = _add(store, type="context", title="Synced", summary=["synced"], session_id="s1")
                note_path = vault / "s1" / f"{bead}.md"
                self.assertTrue(note_path.exists())
                self.assertIn("status: open", note_path.read_text(encoding="utf-8"))

                out = remove_bead(
                    root=td,
                    bead_id=bead,
                    reason="user identified mistake",
                    actor="agent.chat",
                    authority={"user_confirmed": True},
                    apply=True,
                    dry_run=False,
                )
                self.assertTrue(out.get("ok"), out)
                text = note_path.read_text(encoding="utf-8")
                self.assertIn("status: retracted", text)
                self.assertIn("RETRACTED", text)

    def test_maintain_remove_beads_previews_then_applies(self):
        with tempfile.TemporaryDirectory() as td:
            bead = _add(MemoryStore(td), type="context", title="Trim", summary=["trim"], session_id="s1")

            preview = maintain(
                root=td,
                action="remove_beads",
                targets={"bead_ids": [bead]},
                decision={"reason": "user asked to prune"},
                authority={"actor": "agent.chat", "user_confirmed": True},
            )
            self.assertTrue(preview.get("ok"), preview)
            self.assertFalse(preview.get("applied"))

            applied = maintain(
                root=td,
                action="remove_beads",
                targets={"bead_ids": [bead]},
                decision={"reason": "user asked to prune"},
                authority={"actor": "agent.chat", "user_confirmed": True},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(applied.get("ok"), applied)
            self.assertEqual([bead], applied.get("removed_bead_ids"))

    def test_http_remove_and_maintain_endpoints(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            bead = _add(MemoryStore(td), type="context", title="HTTP", summary=["http"], session_id="s1")
            client = TestClient(app)

            preview = client.post(
                "/v1/memory/beads/remove",
                json={"root": td, "bead_ids": [bead], "reason": "preview only"},
            )
            self.assertEqual(200, preview.status_code)
            self.assertFalse(preview.json().get("applied"))

            applied = client.post(
                "/v1/memory/maintain",
                json={
                    "root": td,
                    "action": "remove_beads",
                    "targets": {"bead_ids": [bead]},
                    "decision": {"reason": "user confirmed"},
                    "authority": {"actor": "agent.chat", "user_confirmed": True},
                    "apply": True,
                    "dry_run": False,
                },
            )
            self.assertEqual(200, applied.status_code)
            self.assertTrue(applied.json().get("applied"))

    def test_mcp_maintain_tool_dispatches_remove(self):
        with tempfile.TemporaryDirectory() as td:
            bead = _add(MemoryStore(td), type="context", title="MCP", summary=["mcp"], session_id="s1")
            out = call_tool(
                "maintain",
                {
                    "root": td,
                    "action": "remove_beads",
                    "targets": {"bead_ids": [bead]},
                    "decision": {"reason": "user confirmed"},
                    "authority": {"actor": "agent.chat", "user_confirmed": True},
                    "apply": True,
                    "dry_run": False,
                },
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("applied"))
            self.assertNotIn(bead, (_index(td).get("beads") or {}))

    def test_maintain_policy_denies_apply_but_allows_preview(self):
        with tempfile.TemporaryDirectory() as td:
            bead = _add(MemoryStore(td), type="context", title="Review", summary=["review"], session_id="s1")

            preview = maintain(
                root=td,
                action="approve_memory",
                targets={"bead_id": bead},
                authority={"actor": "agent.chat"},
            )
            self.assertTrue(preview.get("ok"), preview)
            self.assertFalse(preview.get("authority_ok"))
            self.assertIn("approve_memory", preview.get("required_authority") or [])

            denied = maintain(
                root=td,
                action="approve_memory",
                targets={"bead_id": bead},
                authority={"actor": "agent.chat"},
                apply=True,
                dry_run=False,
            )
            self.assertFalse(denied.get("ok"), denied)
            self.assertEqual("maintain_authority_required", denied.get("error"))

    def test_remove_source_flat_metadata_does_not_narrow_selector(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = _add(store, type="context", title="Doc", summary=["doc"], session_id="external", document_id="doc-meta")

            preview = remove_source(
                root=td,
                source={"document_id": "doc-meta", "display_name": "renamed file", "resource_type": "document"},
                reason="source file removed",
            )
            self.assertTrue(preview.get("ok"), preview)
            self.assertEqual(1, preview.get("matched_total"))
            self.assertEqual({"document_id": "doc-meta"}, preview.get("source"))
            self.assertEqual("renamed file", (preview.get("source_metadata") or {}).get("display_name"))

            applied = remove_source(
                root=td,
                source={"document_id": "doc-meta", "display_name": "renamed file", "resource_type": "document"},
                reason="source file removed",
                actor="source-cleanup",
                authority={"mode": "event_hook"},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(applied.get("ok"), applied)
            self.assertEqual([bead], applied.get("removed_bead_ids"))

    def test_destructive_idempotency_replays_and_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add(store, type="context", title="First", summary=["first"], session_id="s1")
            second = _add(store, type="context", title="Second", summary=["second"], session_id="s1")

            out1 = remove_bead(
                root=td,
                bead_id=first,
                reason="same request",
                actor="agent.chat",
                authority={"user_confirmed": True},
                apply=True,
                dry_run=False,
                idempotency_key="rm-once",
            )
            self.assertTrue(out1.get("ok"), out1)
            self.assertEqual(1, out1.get("removed_count"))

            replay = remove_bead(
                root=td,
                bead_id=first,
                reason="same request",
                actor="agent.chat",
                authority={"user_confirmed": True},
                apply=True,
                dry_run=False,
                idempotency_key="rm-once",
            )
            self.assertTrue(replay.get("ok"), replay)
            self.assertTrue(replay.get("idempotent_replay"), replay)
            self.assertEqual(1, replay.get("removed_count"))

            conflict = remove_bead(
                root=td,
                bead_id=second,
                reason="same request",
                actor="agent.chat",
                authority={"user_confirmed": True},
                apply=True,
                dry_run=False,
                idempotency_key="rm-once",
            )
            self.assertFalse(conflict.get("ok"), conflict)
            self.assertEqual("idempotency_key_conflict", conflict.get("error"))

    def test_remove_source_idempotency_key_replays_source_result(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add(store, type="context", title="Doc 1", summary=["one"], session_id="external", document_id="doc-idem")
            second = _add(store, type="context", title="Doc 2", summary=["two"], session_id="external", document_id="doc-idem")

            out1 = remove_source(
                root=td,
                source={"selector": {"document_id": "doc-idem"}, "metadata": {"name": "Doc"}},
                reason="source removed",
                actor="source-cleanup",
                authority={"mode": "event_hook"},
                apply=True,
                dry_run=False,
                idempotency_key="source-cleanup-1",
            )
            self.assertTrue(out1.get("ok"), out1)
            self.assertEqual(2, out1.get("removed_count"))
            self.assertEqual({first, second}, set(out1.get("removed_bead_ids") or []))

            replay = remove_source(
                root=td,
                source={"selector": {"document_id": "doc-idem"}, "metadata": {"name": "Doc"}},
                reason="source removed",
                actor="source-cleanup",
                authority={"mode": "event_hook"},
                apply=True,
                dry_run=False,
                idempotency_key="source-cleanup-1",
            )
            self.assertTrue(replay.get("ok"), replay)
            self.assertTrue(replay.get("idempotent_replay"), replay)
            self.assertEqual(2, replay.get("removed_count"))
            self.assertEqual({first, second}, set(replay.get("removed_bead_ids") or []))

            conflict = remove_source(
                root=td,
                source={"document_id": "other-doc"},
                reason="source removed",
                actor="source-cleanup",
                authority={"mode": "event_hook"},
                apply=True,
                dry_run=False,
                idempotency_key="source-cleanup-1",
            )
            self.assertFalse(conflict.get("ok"), conflict)
            self.assertEqual("idempotency_key_conflict", conflict.get("error"))

    def test_deactivate_association_rebuild_filters_edge_and_queues_myelination(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            source = _add(store, type="context", title="Source", summary=["source"], session_id="s1")
            target = _add(store, type="context", title="Target", summary=["target"], session_id="s1")
            assoc_id = store.link(source, target, "supports", "support edge")

            out = maintain(
                root=td,
                action="deactivate_association",
                targets={"association_id": assoc_id},
                decision={"reason": "support no longer valid"},
                authority={"actor": "agent.chat", "allowed_authority": ["deactivate_association"]},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("retracted"))
            self.assertEqual([], (_index(td).get("associations") or []))

            rebuilt = store.rebuild_index()
            self.assertEqual([], rebuilt.get("associations") or [])
            self.assertIn(assoc_id, rebuilt.get("retracted_association_ids") or [])
            self.assertEqual(
                {source, target},
                {
                    (rebuilt.get("retracted_associations") or {}).get(assoc_id, {}).get("source_bead"),
                    (rebuilt.get("retracted_associations") or {}).get(assoc_id, {}).get("target_bead"),
                },
            )

            rereview = maintain(
                root=td,
                action="request_re_review",
                targets={"association_id": assoc_id},
                authority={"actor": "agent.chat", "allowed_authority": ["run_association_judge"]},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(rereview.get("ok"), rereview)
            self.assertEqual({source, target}, set(rereview.get("bead_ids") or []))

            rows = _event_rows(td)
            self.assertTrue(any(row.get("event_type") == "association_retracted" for row in rows))
            queue_path = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertTrue(any(item.get("kind") == "myelination-update" for item in queue), queue)

    def test_soul_revision_actions_are_routed_through_revision_store(self):
        with tempfile.TemporaryDirectory() as td:
            proposed = maintain(
                root=td,
                action="propose_soul_update",
                proposal={
                    "subject": "acme",
                    "target_file": "IDENTITY.md",
                    "entry_key": "Working style",
                    "content": "Prefers explicit governance checkpoints.",
                    "reason": "user supplied profile update",
                },
                authority={"actor": "agent.chat", "allowed_authority": ["propose_soul_update"]},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(proposed.get("ok"), proposed)
            self.assertEqual("proposed", proposed.get("status"))

            approved = maintain(
                root=td,
                action="approve_soul_update",
                targets={"subject": "acme", "revision_id": proposed.get("revision_id")},
                authority={"actor": "user", "user_confirmed": True},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(approved.get("ok"), approved)
            self.assertEqual("applied", approved.get("status"))

            inspected = maintain(
                root=td,
                action="inspect_soul",
                targets={"subject": "acme", "file_name": "IDENTITY.md"},
            )
            self.assertTrue(inspected.get("ok"), inspected)
            self.assertIn("Prefers explicit governance checkpoints.", inspected.get("markdown") or "")
            self.assertFalse((Path(td) / ".beads" / "index.json").exists())

    def test_refresh_myelination_uses_side_effect_job(self):
        with tempfile.TemporaryDirectory() as td:
            status = maintain(root=td, action="myelination_status")
            self.assertTrue(status.get("ok"), status)
            self.assertFalse(status.get("manifest_present"))
            self.assertFalse((status.get("manifest") or {}).get("present"))

            out = maintain(
                root=td,
                action="refresh_myelination",
                authority={"actor": "agent.chat", "allowed_authority": ["refresh_myelination"]},
                apply=True,
                dry_run=False,
                idempotency_key="refresh-1",
            )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual("myelination-update", out.get("kind"))
            self.assertEqual("myelination-update", ((out.get("queue") or {}).get("kind")))

    def test_association_run_maintain_supports_sweep_without_client_bead_ids(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            out = maintain(
                root=td,
                action="association_run",
                targets={
                    "sweep": True,
                    "sweep_mode": "all",
                    "sweep_limit": 1,
                    "run_inline": True,
                    "max_candidates": 1,
                },
                authority={"actor": "agent.chat", "allowed_authority": ["run_association_judge"]},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("sweep"), out)
            self.assertEqual("all", out.get("sweep_mode"))
            self.assertEqual(1, len(out.get("bead_ids") or []))
            self.assertIn((out.get("bead_ids") or [None])[0], {first, second})
            self.assertTrue(out.get("next_sweep_cursor"))

    def test_association_run_maintain_preserves_source_ingest_envelope_refs_through_queue(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            _add(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            _add(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])
            envelope_ref = {
                "schema": "core_memory.source_ingest_envelope.v1",
                "envelope_id": "env-maintain-source-1",
                "boundary_type": "DocumentImported",
                "ingest_batch_id": "batch-maintain-source-1",
                "source_object_id": "doc-maintain-source-1",
            }

            queued = maintain(
                root=td,
                action="association_run",
                targets={
                    "sweep": True,
                    "sweep_mode": "all",
                    "sweep_limit": 1,
                    "max_candidates": 1,
                    "source_ingest_envelope_refs": [envelope_ref],
                },
                authority={"actor": "agent.chat", "allowed_authority": ["run_association_judge"]},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(queued.get("ok"), queued)
            self.assertEqual("queued", queued.get("status"))
            self.assertIn(
                "env-maintain-source-1",
                {ref.get("envelope_id") for ref in (queued.get("source_ingest_envelope_refs") or [])},
            )

            ran = run_async_jobs(td, run_semantic=False, max_compaction=0, max_side_effects=1)
            self.assertTrue(ran.get("ok"), ran)
            final = get_association_run(td, str(queued.get("run_id") or ""))
            self.assertTrue(final.get("ok"), final)
            run_record = final.get("run") or {}
            self.assertIn(
                "env-maintain-source-1",
                {ref.get("envelope_id") for ref in (run_record.get("source_ingest_envelope_refs") or [])},
            )
            self.assertIn("batch-maintain-source-1", run_record.get("source_ingest_batch_ids") or [])

    @patch.dict(os.environ, {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "disabled"}, clear=False)
    def test_maintain_exposes_association_review_control_plane(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = _add(store, type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = _add(store, type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            run = maintain(
                root=td,
                action="association_run",
                targets={
                    "bead_ids": [second],
                    "candidate_bead_ids": [first],
                    "run_inline": True,
                },
                authority={"actor": "association.agent", "allowed_authority": ["run_association_judge"]},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(run.get("ok"), run)
            self.assertEqual("pending_judge", run.get("status"))

            summary = maintain(root=td, action="association_coverage_summary", targets={"limit": 10})
            self.assertTrue(summary.get("ok"), summary)
            self.assertEqual("association_coverage_summary", summary.get("action"))
            self.assertEqual(2, summary.get("eligible_bead_count"))
            self.assertGreaterEqual((summary.get("candidate_status_counts") or {}).get("pending_judge") or 0, 1)

            pending = maintain(
                root=td,
                action="list_association_candidates",
                targets={"status": "pending_judge", "limit": 10},
            )
            self.assertTrue(pending.get("ok"), pending)
            self.assertEqual("list_association_candidates", pending.get("action"))
            self.assertGreaterEqual(pending.get("count") or 0, 1)
            candidate = pending["results"][0]

            denied = maintain(
                root=td,
                action="decide_association_candidate",
                targets={"candidate_id": candidate.get("candidate_id")},
                decision={"action": "accept"},
                authority={"actor": "qa"},
                apply=True,
                dry_run=False,
            )
            self.assertFalse(denied.get("ok"), denied)
            self.assertEqual("maintain_authority_required", denied.get("error"))

            decided = maintain(
                root=td,
                action="decide_association_candidate",
                targets={"candidate_id": candidate.get("candidate_id")},
                decision={
                    "action": "linked",
                    "truth_basis": "manual_association_review",
                    "reason_text": "The two beads are related in this test fixture.",
                },
                authority={"actor": "qa", "user_confirmed": True},
                apply=True,
                dry_run=False,
            )
            self.assertTrue(decided.get("ok"), decided)
            self.assertEqual("linked", decided.get("status"))
            self.assertTrue(decided.get("authority_ok"))
            self.assertTrue(decided.get("association_ids"))

            after = maintain(root=td, action="coverage_summary")
            self.assertEqual(1, after.get("active_association_count"))

    def test_apply_association_proposals_requires_authority_and_judge_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            source = _add(store, type="context", title="Source", summary=["source"], session_id="s1")
            target = _add(store, type="context", title="Target", summary=["target"], session_id="s1")
            proposal = {
                "associations": [
                    {
                        "source_bead": source,
                        "target_bead": target,
                        "relationship": "supports",
                        "confidence": 0.9,
                        "reason_text": "Reviewed support.",
                    }
                ]
            }

            missing_provenance = maintain(
                root=td,
                action="apply_association_proposals",
                proposal=proposal,
                authority={"actor": "judge.agent", "allowed_authority": ["append_judged_association"]},
                apply=True,
                dry_run=False,
            )
            self.assertFalse(missing_provenance.get("ok"), missing_provenance)
            self.assertEqual("maintain_validation_failed", missing_provenance.get("error"))

            missing_authority = maintain(
                root=td,
                action="apply_association_proposals",
                proposal={
                    **proposal,
                    "judge_model": "unit-judge",
                    "prompt_version": "p1",
                    "rubric_version": "r1",
                    "truth_basis": "unit_reviewed",
                },
                authority={"actor": "judge.agent"},
                apply=True,
                dry_run=False,
            )
            self.assertFalse(missing_authority.get("ok"), missing_authority)
            self.assertEqual("maintain_authority_required", missing_authority.get("error"))


if __name__ == "__main__":
    unittest.main()
