import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory import maintain, remove_bead, remove_source
from core_memory.integrations.mcp.registry import call_tool
from core_memory.persistence.store import MemoryStore


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


if __name__ == "__main__":
    unittest.main()
