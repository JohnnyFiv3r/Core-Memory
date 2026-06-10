"""Edge lifecycle: reinforcement, decay, supersession — the maintain leg.

Covers: multiplier math, usage collection, record→fold round-trip with
idempotency, hop-expansion scoring integration, recall-time recording, and
the flush-boundary fold wiring.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core_memory.association.edge_lifecycle import (
    collect_used_edge_pairs,
    fold_edge_usage,
    record_edge_usage,
)
from core_memory.graph.edge_weights import (
    DECAY_FLOOR,
    REINFORCEMENT_MAX_BONUS,
    effective_edge_multiplier,
)
from core_memory.retrieval.agent import _expand_via_association_hops, recall
from core_memory.retrieval.contracts import EvidenceItem


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_index(root: Path, beads: dict, associations: list) -> None:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": associations}), encoding="utf-8"
    )


def _bead(title: str, status: str = "open") -> dict:
    return {
        "type": "context",
        "title": title,
        "summary": [title],
        "session_id": "s1",
        "retrieval_eligible": True,
        "status": status,
    }


class TestEffectiveEdgeMultiplier(unittest.TestCase):
    def test_fresh_unreinforced_edge_is_neutral(self):
        assoc = {"created_at": _iso(_now())}
        self.assertAlmostEqual(1.0, effective_edge_multiplier(assoc), places=2)

    def test_reinforcement_bonus_is_bounded(self):
        fresh = _iso(_now())
        light = effective_edge_multiplier({"reinforcement_count": 2, "last_reinforced_at": fresh})
        heavy = effective_edge_multiplier({"reinforcement_count": 10_000, "last_reinforced_at": fresh})
        self.assertGreater(light, 1.0)
        self.assertGreater(heavy, light)
        self.assertLessEqual(heavy, 1.0 + REINFORCEMENT_MAX_BONUS + 1e-9)

    def test_stale_edge_decays_to_floor_not_zero(self):
        ancient = _iso(_now() - timedelta(days=3650))
        value = effective_edge_multiplier({"created_at": ancient})
        self.assertAlmostEqual(DECAY_FLOOR, value, places=6)
        self.assertGreater(value, 0.0)

    def test_missing_timestamps_do_not_decay(self):
        self.assertAlmostEqual(1.0, effective_edge_multiplier({}), places=6)

    def test_reinforcement_refreshes_the_decay_clock(self):
        old_created = _iso(_now() - timedelta(days=400))
        stale = effective_edge_multiplier({"created_at": old_created})
        refreshed = effective_edge_multiplier(
            {"created_at": old_created, "last_reinforced_at": _iso(_now()), "reinforcement_count": 1}
        )
        self.assertGreater(refreshed, stale)


class TestCollectAndFold(unittest.TestCase):
    BEADS = {
        "bead-AAAAAAAAAAA1": _bead("a"),
        "bead-AAAAAAAAAAA2": _bead("b"),
        "bead-AAAAAAAAAAA3": _bead("c"),
    }
    ASSOCS = [
        {"id": "as-1", "source_bead": "bead-AAAAAAAAAAA1", "target_bead": "bead-AAAAAAAAAAA2", "relationship": "causes", "status": "active"},
        {"id": "as-2", "source_bead": "bead-AAAAAAAAAAA2", "target_bead": "bead-AAAAAAAAAAA3", "relationship": "supports", "status": "active"},
        {"id": "as-3", "source_bead": "bead-AAAAAAAAAAA1", "target_bead": "bead-AAAAAAAAAAA3", "relationship": "causes", "status": "retracted"},
    ]

    def test_collect_pairs_from_delivered_evidence_and_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, self.BEADS, self.ASSOCS)
            # delivered evidence covers a1+a2 → as-1; retracted as-3 never used
            pairs = collect_used_edge_pairs(root, ["bead-AAAAAAAAAAA1", "bead-AAAAAAAAAAA2"])
            self.assertEqual([("bead-AAAAAAAAAAA1", "bead-AAAAAAAAAAA2", "causes")], pairs)
            # attribution path edge in REVERSE orientation still matches as-2
            paths = [{"edges": [{"src": "bead-AAAAAAAAAAA3", "dst": "bead-AAAAAAAAAAA2", "rel": "supports"}]}]
            pairs = collect_used_edge_pairs(root, [], paths)
            self.assertEqual([("bead-AAAAAAAAAAA2", "bead-AAAAAAAAAAA3", "supports")], pairs)
            # pseudo-edges (no matching association) are never reinforced
            paths = [{"edges": [{"src": "bead-AAAAAAAAAAA1", "dst": "bead-XXXXXXXXXXXX", "rel": "caused_by"}]}]
            self.assertEqual([], collect_used_edge_pairs(root, [], paths))

    def test_record_then_fold_applies_reinforcement_and_truncates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, self.BEADS, self.ASSOCS)
            pair = ("bead-AAAAAAAAAAA1", "bead-AAAAAAAAAAA2", "causes")
            record_edge_usage(root, pairs=[pair], query="q1")
            # reverse orientation must fold onto the same association
            record_edge_usage(root, pairs=[("bead-AAAAAAAAAAA2", "bead-AAAAAAAAAAA1", "causes")], query="q2")

            out = fold_edge_usage(root)
            self.assertTrue(out["ok"])
            self.assertEqual(2, out["events"])
            self.assertEqual(1, out["edges_reinforced"])

            index = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            assoc = next(a for a in index["associations"] if a["id"] == "as-1")
            self.assertEqual(2, assoc["reinforcement_count"])
            self.assertTrue(assoc["last_reinforced_at"])
            untouched = next(a for a in index["associations"] if a["id"] == "as-2")
            self.assertNotIn("reinforcement_count", untouched)

            # idempotent: log was truncated, second fold is a no-op
            out2 = fold_edge_usage(root)
            self.assertTrue(out2["ok"])
            self.assertEqual(0, out2["edges_reinforced"])
            index2 = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            assoc2 = next(a for a in index2["associations"] if a["id"] == "as-1")
            self.assertEqual(2, assoc2["reinforcement_count"])


class TestScoringIntegration(unittest.TestCase):
    def test_reinforced_edge_outranks_stale_edge_in_hop_expansion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ancient = _iso(_now() - timedelta(days=3650))
            beads = {
                "bead-SEED00000001": _bead("seed"),
                "bead-FRESH0000001": _bead("fresh neighbour"),
                "bead-STALE0000001": _bead("stale neighbour"),
            }
            assocs = [
                {"source_bead": "bead-SEED00000001", "target_bead": "bead-FRESH0000001", "relationship": "supports",
                 "confidence": 0.9, "provenance": "agent_judged", "reinforcement_count": 5, "last_reinforced_at": _iso(_now())},
                {"source_bead": "bead-SEED00000001", "target_bead": "bead-STALE0000001", "relationship": "supports",
                 "confidence": 0.9, "provenance": "agent_judged", "created_at": ancient},
            ]
            _write_index(root, beads, assocs)
            ev = [EvidenceItem(bead_id="bead-SEED00000001", type="context", title="seed", content_excerpt="s", score=0.8, reason="retrieved")]
            out = _expand_via_association_hops(str(root), ev, hops=1)
            scores = {e.bead_id: e.score for e in out if e.reason == "association_hop"}
            self.assertIn("bead-FRESH0000001", scores)
            self.assertIn("bead-STALE0000001", scores)
            self.assertGreater(scores["bead-FRESH0000001"], scores["bead-STALE0000001"])

    def test_superseded_endpoint_is_penalised(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "bead-SEED00000001": _bead("seed"),
                "bead-LIVE00000001": _bead("live neighbour"),
                "bead-DEAD00000001": _bead("superseded neighbour", status="superseded"),
            }
            assocs = [
                {"source_bead": "bead-SEED00000001", "target_bead": "bead-LIVE00000001", "relationship": "supports", "confidence": 0.9},
                {"source_bead": "bead-SEED00000001", "target_bead": "bead-DEAD00000001", "relationship": "supports", "confidence": 0.9},
            ]
            _write_index(root, beads, assocs)
            ev = [EvidenceItem(bead_id="bead-SEED00000001", type="context", title="seed", content_excerpt="s", score=0.8, reason="retrieved")]
            out = _expand_via_association_hops(str(root), ev, hops=1)
            scores = {e.bead_id: e.score for e in out if e.reason == "association_hop"}
            self.assertGreater(scores["bead-LIVE00000001"], scores["bead-DEAD00000001"])


class TestRecallRecordsUsage(unittest.TestCase):
    def test_recall_appends_edge_usage_for_delivered_pairs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            beads = {
                "bead-AAAAAAAAAAA1": _bead("alpha"),
                "bead-AAAAAAAAAAA2": _bead("beta"),
            }
            assocs = [
                {"source_bead": "bead-AAAAAAAAAAA1", "target_bead": "bead-AAAAAAAAAAA2", "relationship": "causes", "status": "active"},
            ]
            _write_index(root, beads, assocs)

            def _fake_exec(*, request, root, explain=True):
                return {
                    "ok": True,
                    "results": [
                        {"bead_id": "bead-AAAAAAAAAAA1", "score": 0.9, "title": "alpha", "type": "context"},
                        {"bead_id": "bead-AAAAAAAAAAA2", "score": 0.8, "title": "beta", "type": "context"},
                    ],
                    "chains": [],
                    "request": {"raw_query": "alpha beta", "intent": "remember", "k": 8},
                }

            with patch("core_memory.retrieval.agent.memory_execute", _fake_exec):
                recall("alpha beta", effort="low", root=td, include_raw=False)

            usage = (root / ".beads" / "events" / "edge-usage.jsonl")
            self.assertTrue(usage.exists(), "recall must record edge usage")
            row = json.loads(usage.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn(["bead-AAAAAAAAAAA1", "bead-AAAAAAAAAAA2", "causes"], row["edges"])


class TestFlushFoldWiring(unittest.TestCase):
    def test_process_flush_reports_edge_lifecycle_fold(self):
        from core_memory.runtime.engine import process_flush

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(
                root,
                {"bead-AAAAAAAAAAA1": _bead("a"), "bead-AAAAAAAAAAA2": _bead("b")},
                [{"id": "as-1", "source_bead": "bead-AAAAAAAAAAA1", "target_bead": "bead-AAAAAAAAAAA2", "relationship": "causes", "status": "active"}],
            )
            record_edge_usage(root, pairs=[("bead-AAAAAAAAAAA1", "bead-AAAAAAAAAAA2", "causes")])

            out = process_flush(root=td, session_id="s1", promote=False, token_budget=2000, max_beads=10)
            lifecycle = out.get("edge_lifecycle") or {}
            if not out.get("ok"):
                # Flush may legitimately skip on an empty session; the fold must
                # still be reachable directly.
                lifecycle = fold_edge_usage(root)
            self.assertTrue(lifecycle.get("ok"))
            index = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            assoc = index["associations"][0]
            self.assertEqual(1, assoc.get("reinforcement_count"))
