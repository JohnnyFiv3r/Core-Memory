from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class BenchmarkTurn:
    turn_id: str
    speaker: str
    role: str
    content: str
    timestamp: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkQA:
    qa_id: str
    question: str
    expected_answer: str | None = None
    gold_evidence: list[str] = field(default_factory=list)
    category: str | None = None
    bucket_labels: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkConversation:
    benchmark_name: str
    conversation_id: str
    session_id: str
    turns: list[BenchmarkTurn]
    qa_cases: list[BenchmarkQA]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkShortcutFlags:
    """Contamination guard — any True flag disqualifies faithful evaluation."""
    synthetic_crawler_updates: bool = False
    synthetic_temporal_edges: bool = False
    bead_direct_ingest: bool = False
    oracle_gold_used: bool = False
    benchmark_aware_answer_prompt: bool = False

    def is_faithful(self) -> bool:
        return not any([
            self.synthetic_crawler_updates,
            self.synthetic_temporal_edges,
            self.bead_direct_ingest,
            self.oracle_gold_used,
            self.benchmark_aware_answer_prompt,
        ])

    def to_dict(self) -> dict[str, Any]:
        return {
            "synthetic_crawler_updates": self.synthetic_crawler_updates,
            "synthetic_temporal_edges": self.synthetic_temporal_edges,
            "bead_direct_ingest": self.bead_direct_ingest,
            "oracle_gold_used": self.oracle_gold_used,
            "benchmark_aware_answer_prompt": self.benchmark_aware_answer_prompt,
            "is_faithful": self.is_faithful(),
        }


@runtime_checkable
class BenchmarkAdapter(Protocol):
    """Protocol every dataset adapter must satisfy."""

    @property
    def name(self) -> str: ...

    def load_conversations(self, **kwargs: Any) -> list[BenchmarkConversation]: ...

    def score_answer(self, *, qa: BenchmarkQA, prediction: str) -> float: ...

    def score_evidence(self, *, qa: BenchmarkQA, retrieved_ids: list[str], k: int) -> dict[str, Any]: ...
