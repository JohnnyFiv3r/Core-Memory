"""Canonical processed turn-write receipt contract."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

TURN_FINALIZED_RECEIPT_V2 = "memory.turn_finalized_receipt.v2"

SEMANTIC_STATUSES = frozenset({"committed", "pending", "repair_required", "waived", "failed"})
ASSOCIATION_STATUSES = frozenset({"complete", "pending", "pending_judge", "failed", "skipped"})
QUEUE_STATUSES = frozenset({"drained", "pending", "failed", "skipped"})

SemanticStatus = Literal["committed", "pending", "repair_required", "waived", "failed"]
AssociationStatus = Literal["complete", "pending", "pending_judge", "failed", "skipped"]
QueueStatus = Literal["drained", "pending", "failed", "skipped"]


class AuthorshipReceipt(TypedDict, total=False):
    source: str
    schema_version: str
    used_fallback: bool
    repair_used: bool
    repaired_fields: list[str]
    model_profile: dict[str, Any]
    prompt_version: str
    rubric_version: str
    grounding_hash: str
    task_id: str
    task_receipt_id: str


class ValidationReceipt(TypedDict):
    valid: bool
    warnings: list[dict[str, Any]]
    downgrades: list[dict[str, Any]]


class AssociationReceipt(TypedDict):
    status: AssociationStatus
    candidates: int
    judged: int
    written: int
    pending: int


class QueueReceipt(TypedDict):
    status: QueueStatus
    item_id: str


class DerivedWriteReceipt(TypedDict):
    written: int
    bead_ids: list[str]
    failures: list[dict[str, Any]]


class _TurnFinalizedReceiptOptionalV2(TypedDict, total=False):
    error_code: str
    error: str


class TurnFinalizedReceiptV2(_TurnFinalizedReceiptOptionalV2):
    contract: Literal["memory.turn_finalized_receipt.v2"]
    accepted: bool
    ok: bool
    retryable: bool
    session_id: str
    turn_id: str
    event_id: str
    bead_id: str
    semantic_status: SemanticStatus
    authorship: AuthorshipReceipt
    validation: ValidationReceipt
    associations: AssociationReceipt
    queue: QueueReceipt
    derived: DerivedWriteReceipt


__all__ = [
    "ASSOCIATION_STATUSES",
    "QUEUE_STATUSES",
    "SEMANTIC_STATUSES",
    "TURN_FINALIZED_RECEIPT_V2",
    "AssociationReceipt",
    "AssociationStatus",
    "AuthorshipReceipt",
    "DerivedWriteReceipt",
    "QueueReceipt",
    "QueueStatus",
    "SemanticStatus",
    "TurnFinalizedReceiptV2",
    "ValidationReceipt",
]
