from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.mixin_assembly

from core_memory.persistence.store import MemoryStore


class TestStoreAddBeadDelegationSlice77A(unittest.TestCase):
    def test_add_bead_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-add-bead-deleg-") as td:
            store = MemoryStore(td)
            with patch("core_memory.persistence.store_add_bead_ops.add_bead_for_store", return_value="bead-abc") as stub:
                out = store.add_bead(
                    type="decision",
                    title="Use canary",
                    summary=["rollout safely"],
                    because=["reduce risk"],
                    source_turn_ids=["t1"],
                    session_id="s1",
                    tags=["release"],
                )

            self.assertEqual("bead-abc", out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("decision", kwargs.get("type"))
            self.assertEqual("Use canary", kwargs.get("title"))
            self.assertEqual(["t1"], kwargs.get("source_turn_ids"))
            self.assertEqual("s1", kwargs.get("session_id"))

    def test_add_bead_runs_post_commit_side_effect_provider(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-add-bead-sidefx-") as td:
            store = MemoryStore(td)
            calls = []

            def fake_side_effects(**kwargs):
                calls.append(kwargs)
                return {"ok": True}

            with patch(
                "core_memory.persistence.store_add_bead_ops._bead_commit_side_effects_provider",
                return_value=fake_side_effects,
            ):
                bead_id = store.add_bead(
                    type="context",
                    title="Provider hook",
                    summary=["post-write side effects"],
                    session_id="s1",
                    source_turn_ids=["t1"],
                    _association_trigger="bead_committed",
                    _association_source="memory_store",
                )

            self.assertEqual(1, len(calls))
            call = calls[0]
            self.assertEqual(Path(td), Path(call.get("root")))
            self.assertEqual(bead_id, call.get("bead_id"))
            self.assertEqual(bead_id, (call.get("bead") or {}).get("id"))
            self.assertTrue(call.get("association_coverage_enabled"))
            self.assertEqual("bead_committed", call.get("association_coverage_trigger"))
            self.assertEqual("memory_store", call.get("association_coverage_source"))
            self.assertEqual("s1", call.get("session_id"))


if __name__ == "__main__":
    unittest.main()
