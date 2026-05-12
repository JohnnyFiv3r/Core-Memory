from .hybrid import hybrid_lookup
from .lexical import lexical_lookup
from .contracts import (
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    SourceItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)


def __getattr__(name: str):
    if name == "recall":
        from .agent import recall

        return recall
    raise AttributeError(name)


__all__ = [
    "hybrid_lookup",
    "lexical_lookup",
    "recall",
    "EvidenceItem",
    "SourceItem",
    "RecallPlanning",
    "RecallResult",
    "RecallStep",
    "recall_result_from_memory_execute",
    "validate_recall_effort",
]
