import tempfile
import unittest
from unittest.mock import patch

from core_memory.provider_config import ProviderConfig
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.agent import recall
from core_memory.retrieval.causal_recall import extract_source_citations, normalize_recall_hints
from core_memory.retrieval.pipeline import memory_search_request


class TestCausalRecallPipeline(unittest.TestCase):
    def test_hints_normalize_as_generic_soft_priors(self):
        hints = normalize_recall_hints({
            "bead_types": ["Outcome", "state_assertion"],
            "causal_labels": ["caused_by"],
            "keywords": ["COGS"],
            "source_scope": {"denied_source_ids": ["secret-source"]},
        })

        self.assertEqual(["outcome", "state_assertion"], hints["bead_types"])
        self.assertEqual(["caused_by"], hints["causal_labels"])
        self.assertEqual(["COGS"], hints["keywords"])
        self.assertEqual(["secret-source"], hints["source_scope"]["denied_source_ids"])

    def test_recall_attaches_trace_state_execute_with_missing_llm_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            outcome = store.add_bead(type="outcome", title="COGS spike", summary=["COGS increased."], observed_at="2026-05-04T15:00:00Z")
            cause = store.add_bead(type="state_assertion", title="Vendor price increase", summary=["Vendor prices increased."], observed_at="2026-05-03T15:00:00Z")
            store.link(source_id=outcome, target_id=cause, relationship="caused_by", confidence=0.95)
            raw = {"ok": True, "results": [{"bead_id": outcome, "title": "COGS spike", "summary": ["COGS increased."], "type": "outcome"}]}

            with patch("core_memory.retrieval.agent.memory_execute", return_value=raw), patch(
                "core_memory.retrieval.causal_recall.resolve_chat_config",
                return_value=ProviderConfig("chat", provider="", model="", source=""),
            ):
                result = recall("Why did COGS spike?", effort="high", root=td, include_raw=False)

        self.assertEqual("answered", result.status)
        self.assertIn("trace", result.tier_path)
        self.assertIn("state", result.tier_path)
        self.assertIn("execute", result.tier_path)
        self.assertTrue(result.root_cause_attribution["causal_paths"])
        self.assertEqual("core_memory.state_packet.v1", result.state_packet["schema_version"])
        self.assertEqual("core_memory.execute_decision.v1", result.execute_decision["schema_version"])
        self.assertIn("execute_llm_unavailable", result.warnings)
        self.assertIn("Vendor price increase", result.answer)

    def test_pinned_hint_anchor_is_included_without_hard_filtering_others(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            pinned = store.add_bead(type="decision", title="Pinned billing decision", summary=["Billing changed."])
            other = store.add_bead(type="outcome", title="Other billing outcome", summary=["Outcome changed."])

            with patch("core_memory.retrieval.pipeline.canonical.semantic_lookup", return_value={"ok": True, "results": [{"bead_id": other, "score": 0.7}]}):
                out = memory_search_request(
                    root=td,
                    request={
                        "query": "billing",
                        "k": 5,
                        "hints": {"anchor_ids": [pinned], "bead_types": ["decision"]},
                    },
                    explain=True,
                )

        ids = [r["bead_id"] for r in out["results"]]
        self.assertIn(pinned, ids)
        self.assertIn(other, ids)
        pinned_row = next(r for r in out["results"] if r["bead_id"] == pinned)
        self.assertEqual("pinned_hint", pinned_row["anchor_reason"])
        self.assertGreater(pinned_row["hint_boost"], 0.0)

    def test_source_citation_redaction_keeps_shell_without_link(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            bead = store.add_bead(
                type="document_reference",
                title="Vendor contract",
                summary=["Contract uploaded."],
                source_id="secret-source",
                source_ref="doc-secret",
                hydration_ref={"store": "docs", "ref": "doc-secret", "url": "https://example.com/secret"},
            )

            citations = extract_source_citations(
                td,
                [bead],
                hints={"source_scope": {"denied_source_ids": ["secret-source"], "redaction_policy": "redact_evidence"}},
            )

        self.assertTrue(citations)
        self.assertEqual("redacted", citations[0]["availability"])
        self.assertEqual("", citations[0]["url"])
        self.assertEqual("denied_source", citations[0]["metadata"]["redaction_reason"])


if __name__ == "__main__":
    unittest.main()
