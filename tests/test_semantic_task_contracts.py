from core_memory.schema import semantic_tasks as schema_contracts
from core_memory.runtime.semantic_tasks import contracts as runtime_contracts


def test_runtime_semantic_task_contracts_reexport_schema_contracts():
    assert runtime_contracts.SemanticTaskRequest is schema_contracts.SemanticTaskRequest
    assert runtime_contracts.SemanticTaskResult is schema_contracts.SemanticTaskResult
    assert runtime_contracts.ModelProfile is schema_contracts.ModelProfile
    assert runtime_contracts.TASK_CAUSAL_RECALL_EXECUTE == schema_contracts.TASK_CAUSAL_RECALL_EXECUTE
    assert runtime_contracts.DEFAULT_TASK_MODEL_TIERS is schema_contracts.DEFAULT_TASK_MODEL_TIERS
