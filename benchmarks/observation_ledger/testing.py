"""Test-only adapters. Reports using these adapters are baseline-ineligible."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ModelRequest,
    ModelResponse,
    ModelSelection,
    ResolvedModel,
    RuntimeCaseResult,
)
from .providers import ModelExecutionError


@dataclass
class ScriptedModelAdapter:
    responses: list[dict[str, Any] | str]
    on_complete: Callable[[ModelRequest], None] | None = None
    requests: list[ModelRequest] = field(default_factory=list)

    def resolve(self, selection: ModelSelection) -> ResolvedModel:
        return ResolvedModel(
            requested=selection.requested,
            provider=selection.provider,
            model=selection.model,
            adapter="test-only-scripted",
            baseline_eligible=False,
        )

    def complete(self, request: ModelRequest, resolved: ResolvedModel) -> ModelResponse:
        self.requests.append(request)
        if self.on_complete is not None:
            self.on_complete(request)
        if not self.responses:
            raise ModelExecutionError("test_adapter_responses_exhausted")
        payload = self.responses.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        return ModelResponse(text=text, resolved_model=resolved, latency_ms=1.0)


@dataclass
class ScriptedRuntimeAdapter:
    result: RuntimeCaseResult
    name: str = "test-only-scripted-runtime"
    semantic_roles: tuple[str, ...] = ("turn_memory_authoring",)
    state: dict[str, Any] = field(default_factory=dict)
    observed_environment: dict[str, str] = field(default_factory=dict)
    on_execute: Callable[[dict[str, Any], ResolvedModel], None] | None = None

    def execute(self, case: dict[str, Any], author: ResolvedModel) -> RuntimeCaseResult:
        if self.on_execute is not None:
            self.on_execute(case, author)
        return self.result

    def state_digest(self) -> str:
        return json.dumps(self.state, sort_keys=True, separators=(",", ":"))
