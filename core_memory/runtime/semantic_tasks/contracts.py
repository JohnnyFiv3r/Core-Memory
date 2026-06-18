from __future__ import annotations

"""Dependency-light contracts for Core Memory semantic task execution."""

from dataclasses import dataclass, field
from typing import Any, Protocol

SEMANTIC_TASK_RUNS_SCHEMA = "core_memory.semantic_task_runs.v1"
SEMANTIC_TASK_RUNS_CONTRACT = "memory.semantic_task_runs.v1"

TASK_BEAD_FIELD_JUDGE = "bead_field_judge"
TASK_RATIONALE_EXTRACTOR = "rationale_extractor"
TASK_ASSOCIATION_DECISION = "association_decision"
TASK_DREAMER_RESEARCH = "dreamer_research"
TASK_SOUL_PROPOSAL = "soul_proposal"
TASK_VERIFIER = "verifier"

SEMANTIC_TASK_TYPES = {
    TASK_BEAD_FIELD_JUDGE,
    TASK_RATIONALE_EXTRACTOR,
    TASK_ASSOCIATION_DECISION,
    TASK_DREAMER_RESEARCH,
    TASK_SOUL_PROPOSAL,
    TASK_VERIFIER,
}

MODEL_TIER_CHEAP = "cheap"
MODEL_TIER_STANDARD = "standard"
MODEL_TIER_FRONTIER = "frontier"

MODEL_TIERS = {MODEL_TIER_CHEAP, MODEL_TIER_STANDARD, MODEL_TIER_FRONTIER}

DEFAULT_TASK_MODEL_TIERS = {
    TASK_BEAD_FIELD_JUDGE: MODEL_TIER_CHEAP,
    TASK_RATIONALE_EXTRACTOR: MODEL_TIER_CHEAP,
    TASK_ASSOCIATION_DECISION: MODEL_TIER_STANDARD,
    TASK_DREAMER_RESEARCH: MODEL_TIER_FRONTIER,
    TASK_SOUL_PROPOSAL: MODEL_TIER_STANDARD,
    TASK_VERIFIER: MODEL_TIER_CHEAP,
}


@dataclass(frozen=True)
class ModelProfile:
    """Resolved model routing profile for one semantic task invocation."""

    tier: str
    provider: str = ""
    adapter: str = ""
    model: str = ""
    runtime: str = "provider"
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "provider": self.provider,
            "adapter": self.adapter,
            "model": self.model,
            "runtime": self.runtime,
            "source": self.source,
        }


@dataclass(frozen=True)
class TaskProfile:
    """Static task routing and governance metadata."""

    task_type: str
    model_tier: str
    prompt_version: str = ""
    rubric_version: str = ""
    output_schema: str = ""
    authority_boundary: str = "advisory"

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "model_tier": self.model_tier,
            "prompt_version": self.prompt_version,
            "rubric_version": self.rubric_version,
            "output_schema": self.output_schema,
            "authority_boundary": self.authority_boundary,
        }


@dataclass(frozen=True)
class SemanticTaskRequest:
    """A single LLM-backed semantic task request.

    Core modules pass task intent and structured context here; concrete runtimes
    decide whether a model is available or whether the caller should use its
    deterministic fallback.
    """

    task_type: str
    prompt: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    root: str | None = None
    task_id: str = ""
    idempotency_key: str = ""
    prompt_version: str = ""
    rubric_version: str = ""
    output_schema: str = ""
    model_tier: str = ""
    max_tokens: int = 700
    temperature: float = 0.0
    json_mode: bool = False
    fallback_mode: str = ""
    authority_boundary: str = "advisory"
    evidence_refs: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticTaskResult:
    """Normalized result from a semantic task runtime."""

    task_id: str
    task_type: str
    ok: bool
    status: str
    output_text: str = ""
    output_json: dict[str, Any] | None = None
    model_profile: ModelProfile | None = None
    prompt_version: str = ""
    rubric_version: str = ""
    output_schema: str = ""
    input_hash: str = ""
    output_hash: str = ""
    latency_ms: int | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    fallback_mode: str = ""
    authority_boundary: str = "advisory"
    evidence_refs: list[Any] = field(default_factory=list)
    result_refs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    receipt_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "ok": self.ok,
            "status": self.status,
            "output_text": self.output_text,
            "output_json": self.output_json,
            "model_profile": self.model_profile.as_dict() if self.model_profile else {},
            "prompt_version": self.prompt_version,
            "rubric_version": self.rubric_version,
            "output_schema": self.output_schema,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "latency_ms": self.latency_ms,
            "token_usage": dict(self.token_usage or {}),
            "fallback_mode": self.fallback_mode,
            "authority_boundary": self.authority_boundary,
            "evidence_refs": list(self.evidence_refs or []),
            "result_refs": dict(self.result_refs or {}),
            "error": self.error,
            "metadata": dict(self.metadata or {}),
            "receipt_id": self.receipt_id,
        }


class SemanticTaskRuntime(Protocol):
    """Runtime capable of executing a semantic task."""

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        ...
