import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory import (
    approve_bead,
    list_pending_approvals,
    reject_bead,
    request_approval,
)
from core_memory.integrations.mcp.registry import TOOLS, call_tool
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.visible_corpus import build_visible_corpus
from core_memory.schema.models import ApprovalStatus, Bead
from core_memory.schema.normalization import normalize_approval_status


def _bead(store, **kw):
    base = dict(type="context", title="Vendor switched to NetSuite", summary=["s"])
    base.update(kw)
    return store.add_bead(**base)


class TestApprovalVocabulary(unittest.TestCase):
    def test_normalize_and_enum(self):
        self.assertEqual("pending", normalize_approval_status("awaiting_review"))
        self.assertEqual("approved", normalize_approval_status("accepted"))
        self.assertEqual("rejected", normalize_approval_status("declined"))
        self.assertIsNone(normalize_approval_status(""))
        self.assertIsNone(normalize_approval_status("nonsense"))
        self.assertEqual(ApprovalStatus.PENDING.value, "pending")

    def test_bead_default_no_approval_status(self):
        b = Bead.from_dict({"id": "b1", "type": "context", "title": "t"})
        self.assertIsNone(b.approval_status)

    def test_bead_accepts_pending_at_write(self):
        b = Bead.from_dict({"id": "b1", "type": "context", "title": "t", "approval_status": "needs_approval"})
        self.assertEqual("pending", b.approval_status)


class TestApprovalLifecycle(unittest.TestCase):
    def test_request_then_approve(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = _bead(store, session_id="s1")
            r = request_approval(td, bid, requested_by="ingest-host", note="auto-written, needs sign-off")
            self.assertTrue(r["ok"])
            self.assertEqual("pending", r["approval_status"])

            pend = list_pending_approvals(td)
            self.assertEqual(1, pend["count"])
            self.assertEqual(bid, pend["pending"][0]["bead_id"])

            a = approve_bead(td, bid, approver="john", note="confirmed with vendor")
            self.assertTrue(a["ok"])
            self.assertEqual("approved", a["approval_status"])
            self.assertEqual("A", a["confidence_class"])

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][bid]
            self.assertEqual("approved", bead["approval_status"])
            self.assertEqual("user_confirmed", bead["authority"])
            self.assertEqual("A", bead["confidence_class"])
            self.assertEqual("john", bead["approved_by"])
            # no longer pending
            self.assertEqual(0, list_pending_approvals(td)["count"])

    def test_reject_excludes_from_retrieval_but_retains_record(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = _bead(store, session_id="s1")
            self.assertIn(bid, {r["bead_id"] for r in build_visible_corpus(td)})

            rej = reject_bead(td, bid, approver="john", reason="spurious auto-capture")
            self.assertTrue(rej["ok"])
            self.assertEqual("rejected", rej["approval_status"])

            # gone from current-truth retrieval...
            self.assertNotIn(bid, {r["bead_id"] for r in build_visible_corpus(td)})
            # ...even with include_superseded (rejection is not provenance history)
            self.assertNotIn(bid, {r["bead_id"] for r in build_visible_corpus(td, include_superseded=True)})
            # ...but retained in the index for audit
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("rejected", idx["beads"][bid]["approval_status"])
            self.assertEqual("spurious auto-capture", idx["beads"][bid]["approval_note"])

    def test_pending_bead_remains_retrievable(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = _bead(store, session_id="s1")
            request_approval(td, bid)
            # pending is a review signal, not a hard retrieval gate
            self.assertIn(bid, {r["bead_id"] for r in build_visible_corpus(td)})

    def test_approve_speculative_lifts_grounding(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = store.add_bead(type="hypothesis", title="Cache stampede under load",
                                 summary=["s"], hypothesis_status="pending", session_id="s1")
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("speculative", idx["beads"][bid]["grounding"])
            approve_bead(td, bid, approver="john")
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("inferred", idx["beads"][bid]["grounding"])
            self.assertEqual("A", idx["beads"][bid]["confidence_class"])

    def test_missing_bead_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(root=td).add_bead(type="context", title="x", summary=["s"])
            self.assertFalse(approve_bead(td, "bead-NOPE")["ok"])
            self.assertEqual("bead_not_found", reject_bead(td, "bead-NOPE")["error"])

    def test_rejected_record_survives_index_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = _bead(store, session_id="s1", detail="full detail here")
            reject_bead(td, bid, approver="john", reason="noise")
            store.rebuild_index()
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][bid]
            self.assertEqual("rejected", bead["approval_status"])
            self.assertEqual("context", bead["type"])  # full record preserved
            self.assertEqual("Vendor switched to NetSuite", bead["title"])


class TestApprovalMCPSurface(unittest.TestCase):
    def test_mcp_tools_registered(self):
        for name in ("request_memory_approval", "approve_memory", "reject_memory", "list_pending_approvals"):
            self.assertIn(name, TOOLS)

    def test_mcp_approve_and_list_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = _bead(store, session_id="s1")
            req = call_tool("request_memory_approval", {"root": td, "bead_id": bid, "requested_by": "ingest-host"})
            self.assertTrue(req["ok"])
            listed = call_tool("list_pending_approvals", {"root": td})
            self.assertEqual(1, listed["count"])
            appr = call_tool("approve_memory", {"root": td, "bead_id": bid, "approver": "john"})
            self.assertTrue(appr["ok"])
            self.assertEqual("A", appr["confidence_class"])

    def test_mcp_reject(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            bid = _bead(store, session_id="s1")
            out = call_tool("reject_memory", {"root": td, "bead_id": bid, "approver": "john", "reason": "noise"})
            self.assertTrue(out["ok"])
            self.assertEqual("rejected", out["approval_status"])


if __name__ == "__main__":
    unittest.main()
