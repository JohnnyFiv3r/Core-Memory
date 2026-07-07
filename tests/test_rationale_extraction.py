import tempfile
import unittest
from unittest.mock import Mock, patch

from core_memory.policy.bead_typing import classify_bead_type, is_retrieval_turn
from core_memory.policy.bead_judge import judge_bead_fields
from core_memory.policy.rationale import extract_causal_because, sanitize_because_for_turn
from core_memory.persistence.store import MemoryStore
from core_memory.provider_config import ProviderConfig
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.queue.worker import SidecarPolicy
from core_memory.runtime.semantic_tasks import SemanticTaskResult, list_semantic_task_runs


class FakeSemanticTaskRuntime:
    def __init__(self, output_json=None, *, ok=True, status="succeeded"):
        self.output_json = output_json or {}
        self.ok = ok
        self.status = status
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return SemanticTaskResult(
            task_id=request.task_id or "fake-semantic-task",
            task_type=request.task_type,
            ok=self.ok,
            status=self.status,
            output_json=dict(self.output_json or {}),
            prompt_version=request.prompt_version,
            output_schema=request.output_schema,
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
        )


class TestRationaleExtraction(unittest.TestCase):
    def test_does_not_echo_weak_speculation(self):
        self.assertEqual([], extract_causal_because("maybe we should use Redis"))

    def test_extracts_explicit_because_clause(self):
        out = extract_causal_because(
            "We chose PostgreSQL over MySQL because JSONB support and 2x better performance made it the clear winner."
        )
        self.assertEqual(["JSONB support and 2x better performance made it the clear winner"], out)

    def test_llm_rationale_extractor_records_semantic_task_receipt(self):
        cfg = ProviderConfig(
            kind="chat",
            provider="openai",
            base_url="https://example.test/v1",
            api_key="test",
            model="fallback-model",
            source="unit",
            explicit=True,
        )
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BECAUSE_EXTRACTOR_MODE": "llm",
                "CORE_MEMORY_AGENT_MODEL_CHEAP": "cheap-rationale-model",
                "CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "provider",
            },
            clear=False,
        ), patch(
            "core_memory.policy.semantic_task_runtime.resolve_chat_config",
            return_value=cfg,
        ), patch(
            "core_memory.policy.semantic_task_runtime.chat_complete",
            return_value='{"because":["JSONB support"]}',
        ) as complete:
            out = extract_causal_because(
                "Record decision: PostgreSQL because JSONB support",
                bead_type="decision",
                root=td,
            )
            rows = list_semantic_task_runs(td, task_type="rationale_extractor")

        self.assertEqual(["JSONB support"], out)
        complete.assert_called_once()
        self.assertEqual("cheap-rationale-model", complete.call_args.kwargs["config"].model)
        self.assertEqual(1, rows.get("count"))
        row = (rows.get("results") or [{}])[0]
        self.assertEqual("rationale_extractor", row.get("task_type"))
        self.assertEqual("succeeded", row.get("status"))
        self.assertEqual("cheap", row.get("model_tier"))
        self.assertEqual("rationale_extractor.v1", row.get("prompt_version"))
        self.assertEqual("memory.rationale_extractor.v1", row.get("output_schema"))
        self.assertEqual("heuristic", row.get("fallback_mode"))
        self.assertEqual("advisory", row.get("authority_boundary"))

    def test_questions_are_not_rationale(self):
        self.assertEqual([], extract_causal_because("Why did we choose PostgreSQL?"))
        self.assertEqual("context", classify_bead_type("Why did we choose PostgreSQL?", ""))

    def test_retrieval_imperatives_are_context_without_llm_calls(self):
        cases = [
            "Can you explain why we chose PostgreSQL?",
            "Remind me why PostgreSQL won over MySQL",
            "Show me the decision about PostgreSQL",
            "Tell me what we decided about benchmark workloads",
            "I'm asking: why did we decide to always benchmark representative workloads",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(is_retrieval_turn(text))
                runtime_factory = Mock()
                with patch("core_memory.policy.bead_typing.get_semantic_task_runtime", runtime_factory):
                    self.assertEqual("context", classify_bead_type(text, ""))
                    runtime_factory.assert_not_called()

    def test_declarative_capture_imperatives_are_not_forced_context(self):
        self.assertFalse(is_retrieval_turn("Record that PostgreSQL won because JSONB was faster"))
        self.assertFalse(is_retrieval_turn("Remember that we learned representative benchmarks catch regressions"))

    def test_llm_judge_keeps_short_grounded_support_that_overlaps_user_text(self):
        runtime = FakeSemanticTaskRuntime(
            {
                "type": "decision",
                "title": "Use PostgreSQL",
                "summary": ["Use PostgreSQL"],
                "because": [{"text": "JSONB support", "source_span": "JSONB support", "stated": "direct"}],
                "retrieval_eligible": True,
            }
        )
        with patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "auto",
                "ANTHROPIC_API_KEY": "test-key",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ), patch(
            "core_memory.policy.bead_judge.get_semantic_task_runtime",
            return_value=runtime,
        ):
            out = judge_bead_fields("Record decision: PostgreSQL because JSONB support", "")
        self.assertEqual(1, len(runtime.requests))
        self.assertEqual("decision", out.get("type"))
        self.assertEqual(["JSONB support"], out.get("because"))

    def test_llm_judge_cannot_promote_retrieval_question_as_precedent(self):
        runtime = FakeSemanticTaskRuntime(
            {
                "type": "precedent",
                "title": "Benchmark precedent",
                "summary": ["Question about benchmark precedent"],
                "because": [{"text": "the user asked why", "source_span": "why", "stated": "direct"}],
                "retrieval_eligible": True,
            }
        )
        with patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "auto",
                "ANTHROPIC_API_KEY": "test-key",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ), patch(
            "core_memory.policy.bead_judge.get_semantic_task_runtime",
            return_value=runtime,
        ):
            out = judge_bead_fields("Why did we decide to always benchmark representative workloads?", "")
        self.assertEqual(1, len(runtime.requests))
        self.assertEqual("context", out.get("type"))
        self.assertEqual([], out.get("because"))
        self.assertFalse(out.get("retrieval_eligible"))

    def test_sanitize_removes_speculation_but_keeps_real_reason(self):
        self.assertEqual(
            [],
            sanitize_because_for_turn(["maybe we should use Redis"], user_query="maybe we should use Redis"),
        )
        self.assertEqual(
            ["lower operational risk"],
            sanitize_because_for_turn(["lower operational risk"], user_query="We chose SQLite because lower operational risk"),
        )

    def test_sanitize_keeps_short_user_text_when_it_is_support(self):
        self.assertEqual(
            ["JSONB support"],
            sanitize_because_for_turn(["JSONB support"], user_query="Record decision: JSONB support"),
        )
        self.assertEqual(
            ["PostgreSQL because JSONB"],
            sanitize_because_for_turn(["PostgreSQL because JSONB"], user_query="PostgreSQL because JSONB"),
        )

    def test_sanitize_drops_obvious_long_whole_turn_dump(self):
        long_turn = (
            "We chose PostgreSQL over MySQL because JSONB support and 2x better performance made it the clear winner "
            "for the benchmark workload, and this should be retained as the database decision for the demo architecture."
        )
        self.assertEqual(
            ["JSONB support and 2x better performance made it the clear winner for the benchmark workload, and this should be retained as the database decision for the demo architecture"],
            sanitize_because_for_turn([long_turn], user_query=long_turn),
        )

    def test_turn_write_for_retrieval_question_creates_context_not_precedent(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK": "1",
                "CORE_MEMORY_BECAUSE_EXTRACTOR_MODE": "heuristic",
                "CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "heuristic",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s-question",
                turn_id="t-question",
                turns=[
                    {"speaker": "user", "role": "user", "content": "Why did we decide to always benchmark representative workloads?"},
                    {"speaker": "assistant", "role": "assistant", "content": "Because synthetic benchmarks missed important gaps."},
                ],
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            store = MemoryStore(td)
            idx = store._read_json(store.beads_dir / "index.json")
            bead = next(iter((idx.get("beads") or {}).values()))
            self.assertEqual("context", bead.get("type"))
            self.assertNotEqual("precedent", bead.get("type"))
            self.assertEqual([], bead.get("because"))
            self.assertTrue(bool(bead.get("retrieval_eligible")))

    def test_default_turn_write_keeps_empty_because_without_causal_reason(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK": "1",
                "CORE_MEMORY_BECAUSE_EXTRACTOR_MODE": "heuristic",
                "CORE_MEMORY_ENRICHMENT_QUEUE": "off",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[{"speaker": "user", "role": "user", "content": "maybe we should use Redis"}, {"speaker": "assistant", "role": "assistant", "content": "Noted as a tentative idea."}],
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            store = MemoryStore(td)
            idx = store._read_json(store.beads_dir / "index.json")
            bead = next(iter((idx.get("beads") or {}).values()))
            self.assertEqual([], bead.get("because"))
            self.assertNotEqual("maybe we should use Redis", " ".join(bead.get("because") or []))


if __name__ == "__main__":
    unittest.main()
