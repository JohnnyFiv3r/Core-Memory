import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.associations.coverage import (
    apply_association_proposals,
    enqueue_association_coverage,
    get_association_run,
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
    def test_section_document_bead_links_part_of_whole_document(self):
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
            part_of = _assocs(td, "part_of")
            self.assertEqual(1, len(part_of))
            self.assertEqual(section["bead_id"], part_of[0]["source_bead"])
            self.assertEqual(whole["bead_id"], part_of[0]["target_bead"])
            self.assertEqual("linked", section["association_state"])

    def test_state_assertion_links_derived_from_source_bead(self):
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

            derived = _assocs(td, "derived_from")
            self.assertEqual(1, len(derived))
            self.assertEqual(assertion["bead_id"], derived[0]["source_bead"])
            self.assertEqual(source["bead_id"], derived[0]["target_bead"])

    def test_async_association_pass_job_runs_from_ops_queue(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = store.add_bead(type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = store.add_bead(type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

            enq = enqueue_async_job(td, kind="association-pass", event={"bead_ids": [second], "candidate_bead_ids": [first]})
            self.assertTrue(enq.get("ok"), enq)
            ran = run_async_jobs(td, run_semantic=False, max_compaction=0, max_side_effects=5)
            self.assertTrue(ran.get("ok"), ran)
            follows = _assocs(td, "follows")
            self.assertEqual(1, len(follows))
            self.assertEqual(second, follows[0]["source_bead"])
            self.assertEqual(first, follows[0]["target_bead"])

            rerun = run_association_coverage(td, bead_ids=[second], candidate_bead_ids=[first], trigger="operator")
            self.assertTrue(rerun.get("ok"), rerun)
            self.assertEqual(1, len(_assocs(td, "follows")))

    def test_association_proposals_append_valid_and_quarantine_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            first = store.add_bead(type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = store.add_bead(type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])

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

    def test_http_association_endpoints(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            store = MemoryStore(root)
            first = store.add_bead(type="context", title="First", summary=["first"], session_id="s1", source_turn_ids=["t1"])
            second = store.add_bead(type="context", title="Second", summary=["second"], session_id="s1", source_turn_ids=["t2"])
            client = TestClient(app)

            created = client.post(
                "/v1/memory/association-runs",
                json={"root": root, "bead_ids": [second], "candidate_bead_ids": [first], "run_inline": True},
            )
            self.assertEqual(200, created.status_code)
            data = created.json()
            self.assertTrue(data.get("ok"), data)
            run_id = data.get("run_id")

            fetched = client.get(f"/v1/memory/association-runs/{run_id}", params={"root": root})
            self.assertEqual(200, fetched.status_code)
            self.assertEqual(run_id, (fetched.json().get("run") or {}).get("run_id"))

            inspect = client.get(f"/v1/memory/inspect/beads/{second}", params={"root": root})
            self.assertEqual(200, inspect.status_code)
            coverage = ((inspect.json().get("bead") or {}).get("association_coverage") or {})
            self.assertIn(coverage.get("state"), {"linked", "no_supported_links"})

    def test_association_run_requires_beads_or_session(self):
        with tempfile.TemporaryDirectory() as td:
            out = enqueue_association_coverage(td, trigger="operator")
            self.assertFalse(out.get("ok"))
            self.assertEqual("association_run_requires_bead_ids_or_session_id", out.get("error"))
            self.assertFalse(get_association_run(td, "missing").get("ok"))


if __name__ == "__main__":
    unittest.main()
