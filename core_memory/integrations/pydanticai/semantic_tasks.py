from __future__ import annotations

"""Optional PydanticAI semantic task runtime adapter.

The concrete PydanticAI implementation is introduced in a later migration PR.
This placeholder preserves the import boundary and gives explicit opt-in
deployments a clear unavailable state instead of leaking optional imports into
Core Memory's deterministic kernel.
"""

import uuid

from core_memory.runtime.semantic_tasks.contracts import SemanticTaskRequest, SemanticTaskResult
from core_memory.runtime.semantic_tasks.receipts import record_semantic_task_run, stable_hash


class PydanticAISemanticTaskRuntime:
    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        task_id = request.task_id or (
            f"semtask-{stable_hash([request.task_type, request.idempotency_key])}-{uuid.uuid4().hex[:8]}"
        )
        result = SemanticTaskResult(
            task_id=task_id,
            task_type=request.task_type,
            ok=False,
            status="unavailable",
            input_hash=stable_hash(
                {"task_type": request.task_type, "prompt": request.prompt, "payload": request.payload}
            ),
            output_hash=stable_hash({"error": "pydanticai_semantic_task_runtime_not_configured"}),
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
            evidence_refs=list(request.evidence_refs or []),
            error="pydanticai_semantic_task_runtime_not_configured",
            metadata={"runtime_mode": "pydanticai", **dict(request.metadata or {})},
        )
        if not request.root:
            return result
        row = record_semantic_task_run(request.root, request, result)
        return SemanticTaskResult(
            **{
                **result.as_dict(),
                "model_profile": result.model_profile,
                "receipt_id": str(row.get("receipt_id") or ""),
            }
        )


__all__ = ["PydanticAISemanticTaskRuntime"]
