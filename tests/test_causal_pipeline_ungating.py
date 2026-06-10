"""Causal pipeline un-gating: the graph decides, not the query regex.

Covers the three trigger paths into attach_causal_recall_pipeline:
1. caller-declared intent (pre-existing)
2. system-classified intent (classify_intent verdict reaches the gate)
3. structural trigger — causal-class edge density in the retrieved evidence
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval.agent import recall
from core_memory.retrieval.causal_recall import (
    causal_edge_pressure,
    structural_causal_trigger,
)


def _write_index(root: Path, beads: dict, associations: list) -> None:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (beads_dir / "index.json").write_text(
        json.dumps({"beads": beads, "associations": associations}), encoding="utf-8"
    )


def _bead(title: str) -> dict:
    return {
        "type": "context",
        "title": title,
        "summary": [title],
        "session_id": "s1",
        "retrieval_eligible": True,
        "status": "open",
    }


_CAUSAL_INDEX_BEADS = {
    "bead-AAAAAAAAAAA1": _bead("deploy pipeline change"),
    "bead-AAAAAAAAAAA2": _bead("staging outage"),
    "bead-AAAAAAAAAAA3": _bead("rollback decision"),
}

_CAUSAL_INDEX_ASSOCS = [
    {"source_bead": "bead-AAAAAAAAAAA1", "target_bead": "bead-AAAAAAAAAAA2", "relationship": "causes", "status": "active"},
    {"source_bead": "bead-AAAAAAAAAAA3", "target_bead": "bead-AAAAAAAAAAA2", "relationship": "resolves", "status": "active"},
    {"source_bead": "bead-AAAAAAAAAAA1", "target_bead": "bead-AAAAAAAAAAA3", "relationship": "follows", "status": "active"},
]


def _canned_execute(bead_ids: list[str]):
    """Return a fake memory_execute producing the given beads as results."""

    def _fake(*, request, root, explain=True):
        return {
            "ok": True,
            "results": [
                {"bead_id": bid, "score": 0.8, "title": bid, "type": "context"}
                for bid in bead_ids
            ],
            "chains": [],
            "request": {"raw_query": str(request.get("raw_query") or ""), "intent": "remember", "k": 8},
        }

    return _fake


class TestCausalEdgePressure(unittest.TestCase):
    def test_counts_only_active_causal_class_edges_within_set(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assocs = list(_CAUSAL_INDEX_ASSOCS) + [
                # retracted causal edge must not count
                {"source_bead": "bead-AAAAAAAAAAA2", "target_bead": "bead-AAAAAAAAAAA3", "relationship": "causes", "status": "retracted"},
            ]
            _write_index(root, _CAUSAL_INDEX_BEADS, assocs)
            ids = list(_CAUSAL_INDEX_BEADS)
            # causes + resolves count; follows (temporal) and retracted do not
            self.assertEqual(2, causal_edge_pressure(root, ids))
            # subset: only the causes edge connects A1-A2
            self.assertEqual(1, causal_edge_pressure(root, ids[:2]))
            self.assertEqual(0, causal_edge_pressure(root, ids[:1]))

    def test_structural_trigger_thresholds_and_low_effort_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, _CAUSAL_INDEX_BEADS, _CAUSAL_INDEX_ASSOCS)
            ids = list(_CAUSAL_INDEX_BEADS)
            trig = structural_causal_trigger(root, ids, effort="medium")
            self.assertIsNotNone(trig)
            self.assertEqual("structural", trig.get("kind"))
            self.assertEqual(2, trig.get("causal_edges"))
            # low effort never structurally triggers (latency contract)
            self.assertIsNone(structural_causal_trigger(root, ids, effort="low"))
            # raise the threshold above available pressure
            with patch.dict(os.environ, {"CORE_MEMORY_CAUSAL_TRIGGER_MIN_EDGES": "3"}, clear=False):
                self.assertIsNone(structural_causal_trigger(root, ids, effort="medium"))


class TestPipelineTriggerWiring(unittest.TestCase):
    def _recall_with_patches(self, td: str, query: str, *, effort: str, intent_echo: str, bead_ids: list[str]):
        """Run recall() with canned execute results; return (result, attach_called)."""
        calls: list[str] = []

        def _fake_attach(result, *, root, query, hints=None, max_depth=6, max_paths=20):
            calls.append(query)
            return result

        fake_exec = _canned_execute(bead_ids)

        def _fake_exec_with_intent(*, request, root, explain=True):
            out = fake_exec(request=request, root=root, explain=explain)
            out["request"]["intent"] = intent_echo
            return out

        with patch("core_memory.retrieval.agent.memory_execute", _fake_exec_with_intent), patch(
            "core_memory.retrieval.agent.attach_causal_recall_pipeline", _fake_attach
        ):
            result = recall(query, effort=effort, root=td, include_raw=False)
        return result, bool(calls)

    def test_classified_causal_intent_reaches_the_gate(self):
        # No declared intent, neutral evidence — the classifier's verdict
        # (echoed on request.intent by execute_request) must trigger the gate.
        with tempfile.TemporaryDirectory() as td:
            _write_index(Path(td), _CAUSAL_INDEX_BEADS, [])
            result, attach_called = self._recall_with_patches(
                td, "why did the deploy fail", effort="medium",
                intent_echo="causal", bead_ids=list(_CAUSAL_INDEX_BEADS),
            )
            self.assertTrue(attach_called)
            trigger = result.metadata.get("causal_pipeline_trigger") or {}
            self.assertEqual("intent", trigger.get("kind"))

    def test_structural_trigger_fires_for_neutral_query(self):
        # Neutral phrasing, no causal intent — but the evidence set is
        # connected by two causal-class edges, so the pipeline runs.
        with tempfile.TemporaryDirectory() as td:
            _write_index(Path(td), _CAUSAL_INDEX_BEADS, _CAUSAL_INDEX_ASSOCS)
            result, attach_called = self._recall_with_patches(
                td, "deploy pipeline staging notes", effort="medium",
                intent_echo="remember", bead_ids=list(_CAUSAL_INDEX_BEADS),
            )
            self.assertTrue(attach_called)
            trigger = result.metadata.get("causal_pipeline_trigger") or {}
            self.assertEqual("structural", trigger.get("kind"))
            self.assertGreaterEqual(int(trigger.get("causal_edges") or 0), 2)

    def test_neutral_query_without_causal_edges_skips_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            # Only a temporal edge — no causal structure, no trigger.
            _write_index(
                Path(td),
                _CAUSAL_INDEX_BEADS,
                [{"source_bead": "bead-AAAAAAAAAAA1", "target_bead": "bead-AAAAAAAAAAA2", "relationship": "follows", "status": "active"}],
            )
            result, attach_called = self._recall_with_patches(
                td, "deploy pipeline staging notes", effort="medium",
                intent_echo="remember", bead_ids=list(_CAUSAL_INDEX_BEADS),
            )
            self.assertFalse(attach_called)
            self.assertNotIn("causal_pipeline_trigger", result.metadata or {})

    def test_low_effort_consults_graph_but_never_runs_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            _write_index(Path(td), _CAUSAL_INDEX_BEADS, _CAUSAL_INDEX_ASSOCS)
            # Seed only the first bead: 1-hop expansion at low effort should
            # pull in causally-linked neighbours, but the pipeline stays off.
            result, attach_called = self._recall_with_patches(
                td, "deploy pipeline staging notes", effort="low",
                intent_echo="remember", bead_ids=list(_CAUSAL_INDEX_BEADS)[:1],
            )
            self.assertFalse(attach_called)
            reasons = {e.reason for e in result.evidence}
            self.assertIn("association_hop", reasons, "low effort must consult the causal graph")
