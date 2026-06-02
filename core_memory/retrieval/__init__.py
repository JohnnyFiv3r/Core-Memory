from .hybrid import hybrid_lookup
from .lexical import lexical_lookup
from .contracts import (
    ConflictItem,
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    SourceItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)


def __getattr__(name: str):
    # `recall` is lazy to break the cycle:
    # retrieval.agent → retrieval.tools.memory → retrieval.pipeline → integrations.api → runtime
    # Loading agent.py eagerly during retrieval/__init__.py init would trigger that whole chain
    # before this package is fully initialized.
    if name == "recall":
        from .agent import recall

        return recall
    raise AttributeError(name)


__all__ = [
    "hybrid_lookup",
    "lexical_lookup",
    "recall",
    "ConflictItem",
    "EvidenceItem",
    "SourceItem",
    "RecallPlanning",
    "RecallResult",
    "RecallStep",
    "recall_result_from_memory_execute",
    "validate_recall_effort",
]
