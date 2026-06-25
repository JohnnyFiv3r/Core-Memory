import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory import remove_bead
from core_memory.integrations.api import bead_titles
from core_memory.persistence.store import MemoryStore


def _add(store: MemoryStore, **kwargs):
    kwargs.setdefault("_association_coverage", False)
    return store.add_bead(**kwargs)


class TestBeadTitles(unittest.TestCase):
    def test_batch_titles_resolve_active_beads_and_omit_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            a = _add(store, type="context", title="Alpha note", summary=["a"], session_id="s1")
            b = _add(store, type="context", title="Beta note", summary=["b"], session_id="s1")

            out = bead_titles(root=td, bead_ids=[a, b, "bead-missing", "", a])

            self.assertTrue(out["ok"])
            # Unknown ids are absent (caller falls back to the raw id); dupes/blank ignored.
            self.assertEqual({a: "Alpha note", b: "Beta note"}, out["titles"])

    def test_titles_omit_removed_bead(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            keep = _add(store, type="context", title="Keep", summary=["k"], session_id="s1")
            gone = _add(store, type="context", title="Gone", summary=["g"], session_id="s1")
            remove_bead(
                root=td,
                bead_id=gone,
                reason="mistaken",
                actor="operator",
                authority={"user_confirmed": True},
                dry_run=False,
                apply=True,
            )

            out = bead_titles(root=td, bead_ids=[keep, gone])

            self.assertEqual({keep: "Keep"}, out["titles"])  # removed bead drops out of active index

    def test_http_endpoint_returns_titles(self):
        try:
            from fastapi.testclient import TestClient

            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            a = _add(store, type="context", title="Alpha note", summary=["a"], session_id="s1")
            b = _add(store, type="context", title="Beta note", summary=["b"], session_id="s1")

            c = TestClient(app)
            r = c.get("/v1/beads/titles", params={"ids": f"{a}, {b} ,missing", "root": td})

            self.assertEqual(200, r.status_code)
            body = r.json()
            self.assertTrue(body["ok"])
            self.assertEqual({a: "Alpha note", b: "Beta note"}, body["titles"])

    def test_empty_ids_returns_empty_map(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(td)
            out = bead_titles(root=td, bead_ids=[])
            self.assertEqual({"ok": True, "titles": {}}, out)


if __name__ == "__main__":
    unittest.main()
