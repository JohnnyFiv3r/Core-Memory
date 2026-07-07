from __future__ import annotations

from core_memory.persistence import semantic_task_receipts as persistence_receipts
from core_memory.policy import semantic_task_runtime as policy_runtime
from core_memory.policy import semantic_task_verifier as policy_verifier
from core_memory.runtime.semantic_tasks import receipts as runtime_receipts
from core_memory.runtime.semantic_tasks import runtime as runtime_facade
from core_memory.runtime.semantic_tasks import verifier as runtime_verifier


def test_runtime_semantic_task_facades_reexport_boundary_owners() -> None:
    assert runtime_facade.get_semantic_task_runtime is policy_runtime.get_semantic_task_runtime
    assert runtime_facade.resolve_model_profile is policy_runtime.resolve_model_profile
    assert runtime_facade.ProviderSemanticTaskRuntime is policy_runtime.ProviderSemanticTaskRuntime

    assert runtime_verifier.verify_semantic_task_output is policy_verifier.verify_semantic_task_output

    assert runtime_receipts.record_semantic_task_run is persistence_receipts.record_semantic_task_run
    assert runtime_receipts.list_semantic_task_runs is persistence_receipts.list_semantic_task_runs
    assert runtime_receipts.stable_hash is persistence_receipts.stable_hash
