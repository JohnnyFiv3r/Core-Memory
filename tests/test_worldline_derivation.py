"""Worldline derivation: claim chains, entity threads, goal threads.

Worldlines are derived projections over the canonical index — nothing is
stored. These tests build index fixtures directly and assert ordering,
alias merging, goal status, membership counts, and the HTTP projection route.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from core_memory.graph.worldlines import derive_worldlines, worldline_membership


def _write_index(root: Path, index: dict) -> None:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _bead(title: str, created_at: str, *, type_: str = "context", entities: list | None = None, claims: list | None = None) -> dict:
    out = {
        "type": type_,
        "title": title,
        "summary": [title],
        "session_id": "s1",
        "created_at": created_at,
        "retrieval_eligible": True,
        "status": "open",
    }
    if entities is not None:
        out["entities"] = entities
    if claims is not None:
        out["claims"] = claims
    return out


def _claim(subject: str, slot: str, value: str, chain_seq: int) -> dict:
    return {
        "subject": subject,
        "slot": slot,
        "value": value,
        "claim_text": f"{subject} {slot} is {value}",
        "chain_seq": chain_seq,
        "claim_kind": "fact_reference",
    }


class TestClaimWorldlines(unittest.TestCase):
    def test_chain_ordered_by_chain_seq_not_bead_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Deliberately give the later chain link the lexically-smaller id.
            _write_index(root, {"beads": {
                "bead-AAAAAAAAAAA1": _bead("v2", "2026-02-01T00:00:00+00:00", claims=[_claim("hq", "city", "Austin", 2)]),
                "bead-ZZZZZZZZZZZ9": _bead("v1", "2026-01-01T00:00:00+00:00", claims=[_claim("hq", "city", "Chicago", 1)]),
            }, "associations": []})
            out = derive_worldlines(root, kinds=["claim"])
            self.assertTrue(out["ok"])
            self.assertEqual(1, out["total"])
            wl = out["worldlines"][0]
            self.assertEqual("claim", wl["kind"])
            self.assertEqual("hq/city", wl["key"])
            self.assertEqual(["bead-ZZZZZZZZZZZ9", "bead-AAAAAAAAAAA1"], wl["bead_ids"])
            self.assertEqual("2026-01-01T00:00:00+00:00", wl["span"]["from"])
            self.assertEqual("2026-02-01T00:00:00+00:00", wl["span"]["to"])


class TestEntityWorldlines(unittest.TestCase):
    def test_alias_merge_and_time_ordering(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "beads": {
                    "bead-AAAAAAAAAAA2": _bead("later", "2026-03-01T00:00:00+00:00", entities=["ACME Corp"]),
                    "bead-AAAAAAAAAAA1": _bead("earlier", "2026-01-01T00:00:00+00:00", entities=["acme"]),
                    "bead-AAAAAAAAAAA3": _bead("other", "2026-02-01T00:00:00+00:00", entities=["Globex"]),
                },
                "associations": [],
                "entities": {"ent-1": {"label": "ACME Corp", "normalized_label": "acme corp"}},
                "entity_aliases": {"acme": "ent-1", "acme corp": "ent-1"},
            })
            out = derive_worldlines(root, kinds=["entity"])
            by_key = {w["key"]: w for w in out["worldlines"]}
            self.assertIn("ent-1", by_key, "aliases must merge into the registry entity")
            self.assertEqual(["bead-AAAAAAAAAAA1", "bead-AAAAAAAAAAA2"], by_key["ent-1"]["bead_ids"])
            self.assertEqual("ACME Corp", by_key["ent-1"]["label"])
            self.assertIn("globex", by_key, "unregistered entities still thread by normalized text")


class TestGoalWorldlines(unittest.TestCase):
    def _fixture(self, root: Path, *, assoc_status: str = "active") -> None:
        _write_index(root, {
            "beads": {
                "bead-GOAL00000001": _bead("ship the demo", "2026-01-01T00:00:00+00:00", type_="goal"),
                "bead-OUTC00000001": _bead("demo shipped", "2026-02-01T00:00:00+00:00", type_="outcome"),
            },
            "associations": [{
                "source_bead": "bead-OUTC00000001",
                "target_bead": "bead-GOAL00000001",
                "relationship": "resolves",
                "status": assoc_status,
            }],
        })

    def test_goal_thread_with_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            out = derive_worldlines(root, kinds=["goal"])
            self.assertEqual(1, out["total"])
            wl = out["worldlines"][0]
            self.assertEqual("resolved", wl["status"])
            self.assertEqual(["bead-GOAL00000001", "bead-OUTC00000001"], wl["bead_ids"])
            self.assertEqual("ship the demo", wl["label"])

    def test_retracted_resolution_leaves_goal_open(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root, assoc_status="retracted")
            out = derive_worldlines(root, kinds=["goal"])
            wl = out["worldlines"][0]
            self.assertEqual("open", wl["status"])
            self.assertEqual(["bead-GOAL00000001"], wl["bead_ids"])


class TestMembershipAndFilters(unittest.TestCase):
    def test_membership_counts_and_min_length(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "beads": {
                    # member of an entity thread AND sources a claim chain
                    "bead-AAAAAAAAAAA1": _bead("a", "2026-01-01T00:00:00+00:00", entities=["acme"], claims=[_claim("hq", "city", "Chicago", 1)]),
                    "bead-AAAAAAAAAAA2": _bead("b", "2026-02-01T00:00:00+00:00", entities=["acme"]),
                },
                "associations": [],
            })
            membership = worldline_membership(root)
            self.assertEqual(2, membership["bead-AAAAAAAAAAA1"])  # entity + claim
            self.assertEqual(1, membership["bead-AAAAAAAAAAA2"])  # entity only
            # min_length=2 drops the single-bead claim chain
            out = derive_worldlines(root, min_length=2)
            kinds = {w["kind"] for w in out["worldlines"]}
            self.assertEqual({"entity"}, kinds)

    def test_empty_store_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            out = derive_worldlines(Path(td))
            self.assertTrue(out["ok"])
            self.assertEqual(0, out["total"])
            self.assertEqual({}, worldline_membership(Path(td)))


class TestHttpProjectionRoute(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE")
        os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = "degraded_allowed"
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", None)
        else:
            os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = self._old

    def test_worldlines_endpoint_returns_projection(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "beads": {
                    "bead-AAAAAAAAAAA1": _bead("a", "2026-01-01T00:00:00+00:00", entities=["acme"]),
                    "bead-AAAAAAAAAAA2": _bead("b", "2026-02-01T00:00:00+00:00", entities=["acme"]),
                },
                "associations": [],
            })
            c = TestClient(app)
            r = c.get("/v1/memory/projection/worldlines", params={"root": str(root), "kinds": "entity", "include_membership": "true"})
            self.assertEqual(200, r.status_code)
            body = r.json()
            self.assertTrue(body["ok"])
            self.assertEqual(1, body["counts"].get("entity"))
            self.assertEqual(2, body["membership"]["bead-AAAAAAAAAAA1"] + body["membership"]["bead-AAAAAAAAAAA2"])
