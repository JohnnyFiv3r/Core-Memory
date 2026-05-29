from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal

RecallEffort = Literal["low", "medium", "high", "dynamic"]
RecallStatus = Literal["answered", "partial", "empty", "failed"]

RECALL_RESULT_SCHEMA_VERSION = "recall_result.v1"
_RECALL_EFFORTS = {"low", "medium", "high", "dynamic"}


def _known_dataclass_kwargs(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in dict(data or {}).items() if k in known}


def _clean_list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class EvidenceItem:
    """One grounded item used by recall.

    `content_excerpt` is intentionally short and display-safe; full source hydration
    belongs in `sources` or in a follow-up hydration endpoint.
    """

    bead_id: str
    type: str = ""
    title: str = ""
    content_excerpt: str = ""
    score: float | None = None
    reason: str = ""
    grounding_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class ResolvedGoalItem:
    """Resolved goal state surfaced by recall without triggering new writes."""

    bead_id: str
    title: str = ""
    resolved_by_bead_id: str = ""
    resolved_at: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResolvedGoalItem":
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class ClaimSlotItem:
    """Current resolved state for a subject+slot claim chain."""

    key: str
    subject: str = ""
    slot: str = ""
    current_value: Any = None
    status: str = ""
    current_claim_id: str = ""
    chain_seq: int | None = None
    grounding_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClaimSlotItem":
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class SourceItem:
    """Source turn or transcript context behind an evidence item."""

    turn_id: str = ""
    session_id: str = ""
    speaker: str = ""
    ts: str = ""
    bead_id: str = ""
    content_excerpt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceItem":
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class RecallStep:
    """Observable retrieval/planning step for diagnostics."""

    tier: str
    query: str = ""
    status: str = ""
    result_count: int = 0
    why: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecallStep":
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class RecallPlanning:
    """Query planning metadata for future dynamic effort selection.

    Phase 1 only standardizes the shape. The orchestrator can populate this later
    with expected bead types, inferred time ranges, relations, and why it chose an
    effort level.
    """

    selected_effort: str = "medium"
    reason: str = ""
    expected_shape: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecallPlanning":
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class ConflictItem:
    """One active subject+slot conflict surfaced by recall."""

    subject: str
    slot: str
    claim_a_id: str
    claim_b_id: str
    epistemic_conflict_score: float
    conflict_since: str = ""
    chain_seq_gap: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConflictItem":
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class RecallResult:
    """Stable recall response contract shared by package, HTTP, CLI, and demos."""

    answer: str | None = None
    why: str | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)
    resolved_goals: list[ResolvedGoalItem] = field(default_factory=list)
    claim_slots: dict[str, ClaimSlotItem] = field(default_factory=dict)
    conflicts: list[ConflictItem] = field(default_factory=list)
    sources: list[SourceItem] = field(default_factory=list)
    tier_path: list[str] = field(default_factory=list)
    steps: list[RecallStep] = field(default_factory=list)
    planning: RecallPlanning = field(default_factory=RecallPlanning)
    status: str = "empty"
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    as_of: str | None = None
    raw: dict[str, Any] | None = None
    schema_version: str = RECALL_RESULT_SCHEMA_VERSION
    contract: str = "recall_result"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence": [e.to_dict() for e in self.evidence],
            "resolved_goals": [g.to_dict() for g in self.resolved_goals],
            "claim_slots": {str(k): v.to_dict() for k, v in (self.claim_slots or {}).items()},
            "conflicts": [c.to_dict() for c in self.conflicts],
            "sources": [s.to_dict() for s in self.sources],
            "steps": [s.to_dict() for s in self.steps],
            "planning": self.planning.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecallResult":
        payload = dict(data or {})
        payload["evidence"] = [EvidenceItem.from_dict(x) for x in _clean_list(payload.get("evidence")) if isinstance(x, dict)]
        payload["resolved_goals"] = [ResolvedGoalItem.from_dict(x) for x in _clean_list(payload.get("resolved_goals")) if isinstance(x, dict)]
        raw_slots = payload.get("claim_slots") if isinstance(payload.get("claim_slots"), dict) else {}
        payload["claim_slots"] = {
            str(k): ClaimSlotItem.from_dict(v)
            for k, v in dict(raw_slots or {}).items()
            if isinstance(v, dict)
        }
        payload["conflicts"] = [ConflictItem.from_dict(x) for x in _clean_list(payload.get("conflicts")) if isinstance(x, dict)]
        payload["sources"] = [SourceItem.from_dict(x) for x in _clean_list(payload.get("sources")) if isinstance(x, dict)]
        payload["steps"] = [RecallStep.from_dict(x) for x in _clean_list(payload.get("steps")) if isinstance(x, dict)]
        planning = payload.get("planning")
        payload["planning"] = RecallPlanning.from_dict(planning if isinstance(planning, dict) else {})
        return cls(**_known_dataclass_kwargs(cls, payload))


def validate_recall_effort(effort: str) -> str:
    normalized = _text(effort or "medium").lower()
    if normalized not in _RECALL_EFFORTS:
        allowed = ", ".join(sorted(_RECALL_EFFORTS))
        raise ValueError(f"recall effort must be one of: {allowed}")
    return normalized


def evidence_from_result_row(row: dict[str, Any]) -> EvidenceItem:
    """Normalize a legacy search/execute result row into recall evidence."""
    r = dict(row or {})
    bead_id = _text(r.get("bead_id") or r.get("id"))
    score = _float_or_none(r.get("score") or r.get("rank_score") or r.get("semantic_score") or r.get("fused_score"))
    facts = r.get("retrieval_facts") or r.get("supporting_facts") or r.get("summary") or []
    if isinstance(facts, list):
        excerpt = " ".join(_text(x) for x in facts if _text(x))
    else:
        excerpt = _text(facts)
    if not excerpt:
        excerpt = _text(r.get("content_excerpt") or r.get("detail") or r.get("text"))
    return EvidenceItem(
        bead_id=bead_id,
        type=_text(r.get("type") or r.get("bead_type") or r.get("source_surface")),
        title=_text(r.get("title") or r.get("retrieval_title")),
        content_excerpt=excerpt[:600],
        score=score,
        reason=_text(r.get("reason") or r.get("anchor_reason") or r.get("recommendation")),
        grounding_hash=_text(r.get("grounding_hash")) or None,
        metadata={k: v for k, v in r.items() if k not in {"detail", "text", "summary", "retrieval_facts", "supporting_facts"}},
    )


def sources_from_result_row(row: dict[str, Any]) -> list[SourceItem]:
    r = dict(row or {})
    bead_id = _text(r.get("bead_id") or r.get("id"))
    session_id = _text(r.get("session_id"))
    turn_ids = _clean_list(r.get("source_turn_ids"))
    if not turn_ids and r.get("turn_id"):
        turn_ids = [r.get("turn_id")]
    return [
        SourceItem(
            turn_id=_text(turn_id),
            session_id=session_id,
            speaker=_text(r.get("speaker") or r.get("interaction_role")),
            ts=_text(r.get("ts") or r.get("created_at") or r.get("observed_at") or r.get("recorded_at")),
            bead_id=bead_id,
        )
        for turn_id in turn_ids
        if _text(turn_id)
    ]


def recall_result_from_memory_execute(
    raw: dict[str, Any],
    *,
    query: str = "",
    effort: str = "medium",
    include_raw: bool = True,
) -> RecallResult:
    """Best-effort adapter from existing memory_execute/search/trace payloads.

    This is a compatibility bridge for Phase 1. The orchestrator should eventually
    produce `RecallResult` directly.
    """
    payload = dict(raw or {})
    selected_effort = validate_recall_effort(effort)
    rows = [dict(r or {}) for r in _clean_list(payload.get("results") or payload.get("anchors")) if isinstance(r, dict)]
    evidence = [evidence_from_result_row(row) for row in rows if _text(row.get("bead_id") or row.get("id"))]
    sources: list[SourceItem] = []
    seen_sources: set[tuple[str, str, str]] = set()
    for row in rows:
        for source in sources_from_result_row(row):
            key = (source.turn_id, source.session_id, source.bead_id)
            if key not in seen_sources:
                seen_sources.add(key)
                sources.append(source)

    chains = _clean_list(payload.get("chains"))
    tier_path: list[str] = []
    if rows:
        tier_path.append("semantic")
    if chains:
        tier_path.append("causal")
    if payload.get("hydration_data") or any(sources):
        tier_path.append("source")
    if not tier_path:
        tier_path.append("semantic")

    steps = [
        RecallStep(
            tier=tier_path[0],
            query=query or _text((payload.get("request") or {}).get("raw_query") if isinstance(payload.get("request"), dict) else ""),
            status="ok" if payload.get("ok", True) else "failed",
            result_count=len(evidence),
            why=_text(payload.get("next_action") or payload.get("suggested_next") or payload.get("answer_outcome")),
        )
    ]
    if chains:
        steps.append(RecallStep(tier="causal", query=steps[0].query, status="ok", result_count=len(chains), why="causal chains available"))

    candidate = payload.get("answer_candidate") if isinstance(payload.get("answer_candidate"), dict) else {}
    answer = _text(candidate.get("answer") or candidate.get("value") or payload.get("answer")) or None
    why = _text(candidate.get("why") or payload.get("why") or payload.get("answer_outcome")) or None
    if not payload.get("ok", True):
        status = "failed"
    elif evidence or answer:
        status = "answered" if answer else "partial"
    else:
        status = "empty"

    return RecallResult(
        answer=answer,
        why=why,
        evidence=evidence,
        sources=sources,
        tier_path=tier_path,
        steps=steps,
        planning=RecallPlanning(selected_effort=selected_effort),
        status=status,
        warnings=[_text(w) for w in _clean_list(payload.get("warnings")) if _text(w)],
        metadata={
            "query": query,
            "source_surface": "memory_execute",
            "legacy_ok": bool(payload.get("ok", True)),
        },
        raw=payload if include_raw else None,
    )
