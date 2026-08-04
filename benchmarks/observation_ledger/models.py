"""Dependency-light benchmark model and report contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from benchmarks.contracts import BenchmarkShortcutFlags


class RunStatus(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    DISQUALIFIED = "disqualified"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelSelection:
    requested: str
    provider: str
    model: str

    @classmethod
    def parse(cls, value: str | None) -> ModelSelection:
        requested = str(value or "").strip()
        if not requested:
            raise ValueError("missing_model_selector")
        provider, separator, model = requested.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if not separator or not provider or not model:
            raise ValueError("model_selector_must_be_provider_colon_model")
        return cls(requested=requested, provider=provider, model=model)


@dataclass(frozen=True)
class ResolvedModel:
    requested: str
    provider: str
    model: str
    adapter: str
    baseline_eligible: bool = True

    @property
    def identifier(self) -> str:
        return f"{self.provider}:{self.model}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "resolved": self.identifier,
            "provider": self.provider,
            "model": self.model,
            "adapter": self.adapter,
            "baseline_eligible": self.baseline_eligible,
        }


def selection_relationship(author: ResolvedModel, judge: ResolvedModel) -> str:
    if author.identifier == judge.identifier:
        return "same_model"
    if author.provider == judge.provider:
        return "same_provider"
    return "cross_provider"


@dataclass(frozen=True)
class ModelRequest:
    role: str
    prompt_version: str
    payload: dict[str, Any]
    max_tokens: int = 1200


@dataclass(frozen=True)
class ModelResponse:
    text: str
    resolved_model: ResolvedModel
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@runtime_checkable
class ModelAdapter(Protocol):
    def resolve(self, selection: ModelSelection) -> ResolvedModel: ...

    def complete(self, request: ModelRequest, resolved: ResolvedModel) -> ModelResponse: ...


@dataclass(frozen=True)
class RuntimeCaseResult:
    candidate: dict[str, Any]
    semantic_roles_exercised: tuple[str, ...]
    model_identifier: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    hard_invariant_failures: tuple[str, ...] = ()
    shortcut_flags: BenchmarkShortcutFlags = field(default_factory=BenchmarkShortcutFlags)
    deterministic_fallback_used: bool = False


@runtime_checkable
class RuntimeAdapter(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def semantic_roles(self) -> tuple[str, ...]: ...

    def execute(self, case: dict[str, Any], author: ResolvedModel) -> RuntimeCaseResult: ...

    def state_digest(self) -> str: ...
