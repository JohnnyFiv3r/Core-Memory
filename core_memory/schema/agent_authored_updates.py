"""Canonical agent-authored turn-memory contract.

This module is the dependency-light source of truth for the authored write
surface. Runtime validation, agent instructions, HTTP typing, MCP schemas, and
delegated semantic tasks all import this module downward rather than maintaining
parallel field lists.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from types import UnionType
from typing import Any, Literal, TypedDict, Union, cast, get_args, get_origin, get_type_hints

from .models import Bead, BeadType

AGENT_AUTHORED_UPDATES_V1 = "agent_authored_updates.v1"
AUTHORING_MODES = frozenset({"inline", "delegated"})
AuthoringMode = Literal["inline", "delegated"]


class _AgentAuthoredAssociationOptionalV1(TypedDict, total=False):
    rationale: str
    provenance: str
    reason_code: str
    evidence_fields: list[str]
    evidence_bead_ids: list[str]
    evidence_refs: list[str]
    judge_model: str
    prompt_version: str
    rubric_version: str
    grounding_hash: str
    turn_id: str
    visible_bead_ids: list[str]


class AgentAuthoredAssociationV1(_AgentAuthoredAssociationOptionalV1):
    """One agent-judged semantic relationship between visible beads."""

    source_bead_id: str
    target_bead_id: str
    relationship: str
    reason_text: str
    confidence: float


class _PromotionReviewOptionalV1(TypedDict, total=False):
    reason: str
    associations: list[AgentAuthoredAssociationV1]


class PromotionReviewV1(_PromotionReviewOptionalV1):
    """Agent review of one visible session bead's promotion state."""

    bead_id: str
    promotion_state: str


class _AgentAuthoredBeadOptionalV1(TypedDict, total=False):
    scope: str
    authority: str
    confidence: float
    tags: list[str]
    derived_from_bead_ids: list[str]
    retrieval_title: str
    retrieval_facts: list[str]
    entity_ids: list[str]
    topics: list[str]
    incident_keys: list[str]
    decision_keys: list[str]
    goal_keys: list[str]
    action_keys: list[str]
    outcome_keys: list[str]
    time_keys: list[str]
    detail: str
    because: list[str]
    supporting_facts: list[str]
    evidence_refs: list[str]
    cause_candidates: list[str]
    effect_candidates: list[str]
    state_change: dict[str, Any] | str | None
    data_type_flag: str
    source_id: str
    source_event_id: str
    source_system: str
    source_kind: str
    source_ref: str
    source_refs: list[str]
    source_attribution: dict[str, Any]
    core_memory_unifying_id: str
    hydration_ref: dict[str, Any]
    derived_from: list[str]
    observed_at: str
    recorded_at: str
    effective_from: str
    effective_to: str
    validity: str
    supersedes: list[str]
    superseded_by: list[str]
    mechanism: str
    impact_level: str
    uncertainty: float
    grounding: str
    what_almost_happened: str
    what_was_rejected: str
    what_felt_risky: str
    assumption: str
    claims: list[dict[str, Any]]
    claim_updates: list[dict[str, Any]]
    interaction_role: str
    memory_outcome: dict[str, Any]
    incident_id: str
    revises_bead_id: str
    revision_type: str
    speaker_attribution: dict[str, Any]
    attributed_entity_id: str
    resolution_confidence: float
    context_tags: list[str]
    goal_id: str
    success_criteria: str
    result: str
    linked_bead_id: str
    supports_bead_ids: list[str]
    transcript_id: str
    conversation_id: str
    source_thread_id: str
    source_session_id: str
    message_refs: list[str]
    speaker_refs: list[str]
    document_id: str
    raw_source_object_id: str
    document_name: str
    mime_type: str
    document_kind: str
    document_date: str
    author_or_owner: str
    section_refs: list[str]
    actor: str
    source_table: str
    source_record_id: str
    record_action: str
    record_grain: str
    business_object_type: str
    business_object_id: str
    metric_name: str
    metric_value: float
    metric_unit: str
    change_pct: float
    currency: str
    as_of_timestamp: str
    entity_refs: list[str]
    attribute_tags: list[str]
    assertion_kind: str
    assertion_subject: str
    assertion_predicate: str
    assertion_value: str
    condition: str
    action: str
    hypothesis_status: str
    tested_by: str
    reflection_type: str
    tool: str
    capability: str
    tool_result_status: str
    tool_output_id: str
    tool_output_ids: list[str]
    constraints: list[str]
    blocked_by_description: str
    blocking_bead_id: str
    severity: str
    resolved_at: str


class AgentAuthoredBeadV1(_AgentAuthoredBeadOptionalV1):
    """Typed core of one authored bead row.

    The generated JSON schema below contains the complete schema-derived Bead
    field inventory. The TypedDict calls out the contract's required and most
    semantically important fields without duplicating every optional Bead field.
    """

    creation_role: Literal["current_turn", "derived"]
    type: str
    title: str
    summary: list[str]
    entities: list[str]
    retrieval_eligible: bool
    source_turn_ids: list[str]


class AgentAuthoredUpdatesV1(TypedDict):
    """Full typed response authored for one finalized turn."""

    schema_version: Literal["agent_authored_updates.v1"]
    beads_create: list[AgentAuthoredBeadV1]
    associations: list[AgentAuthoredAssociationV1]
    reviewed_beads: list[PromotionReviewV1]


BEAD_FIELD_NAMES = frozenset(field.name for field in fields(Bead))

# Values that Core Memory generates or advances as persistence mechanics.  A
# creation payload may carry a subset of the structural attachment fields as a
# compatibility input, but these values are overlaid by the runtime rather than
# treated as semantic authorship.
RUNTIME_OWNED_BEAD_FIELDS = frozenset(
    {
        "id",
        "created_at",
        "session_id",
        "source_turn_ids",
        "turn_index",
        "prev_bead_id",
        "next_bead_id",
        "status",
        "links",
        "recall_count",
        "last_recalled",
        "type_log",
        "type_coerced_from",
        "promoted_at",
        "promotion_locked",
        "promotion_score",
        "promotion_threshold",
        "confidence_class",
        "failure_signature",
        "validation_warnings",
        "decision_conflict_with",
        "unjustified_flip",
    }
)

# These remain readable for stored-data compatibility but are not new authored
# semantic fields. ``validity`` remains an accepted legacy field; the Ragie
# identifier is a sunset field and is never written by the authored path.
COMPATIBILITY_BEAD_FIELDS = frozenset({"validity", "ragie_document_id"})

AGENT_OWNED_BEAD_FIELDS = frozenset(BEAD_FIELD_NAMES - RUNTIME_OWNED_BEAD_FIELDS - COMPATIBILITY_BEAD_FIELDS)

# Control fields exist only on the creation envelope and are never bead state.
# Only ``creation_role`` is agent-authored; identifiers and source references
# are populated after strict authored-payload validation.
AUTHORED_CREATION_CONTROL_FIELDS = frozenset({"creation_role"})
RUNTIME_CREATION_CONTROL_FIELDS = frozenset({"bead_id", "turn_id", "source_turn_ref"})
CREATION_CONTROL_FIELDS = frozenset(AUTHORED_CREATION_CONTROL_FIELDS | RUNTIME_CREATION_CONTROL_FIELDS)

# Runtime-populated structural values accepted on the compatibility ingress.
# They are deliberately separate from agent-owned semantic fields.
CREATION_STRUCTURAL_INPUT_FIELDS = frozenset(
    {
        "source_turn_ids",
        "turn_index",
        "prev_bead_id",
    }
)

AUTHORED_CREATION_ROW_FIELDS = frozenset(
    AGENT_OWNED_BEAD_FIELDS | AUTHORED_CREATION_CONTROL_FIELDS | CREATION_STRUCTURAL_INPUT_FIELDS | {"validity"}
)

# The versioned public contract exposes semantic authorship plus the one turn
# attachment that the agent must supply for grounding. ``turn_index`` and
# ``prev_bead_id`` remain legacy compatibility inputs but are runtime-owned and
# therefore do not appear in agent_authored_updates.v1.
_V1_GOVERNED_WORKFLOW_FIELDS = frozenset(
    {
        "promoted",
        "promotion_candidate",
        "promotion_reason",
        "approval_status",
        "approved_by",
        "approved_at",
        "approval_note",
    }
)
AGENT_AUTHORED_V1_BEAD_FIELDS = frozenset(
    (AGENT_OWNED_BEAD_FIELDS - _V1_GOVERNED_WORKFLOW_FIELDS)
    | AUTHORED_CREATION_CONTROL_FIELDS
    | {"source_turn_ids", "validity"}
)

# Runtime invariants add these before the persistence normalizer runs. They are
# accepted at that internal boundary, then replaced/ignored by the overlay.
CREATION_RUNTIME_OVERLAY_INPUT_FIELDS = frozenset({"created_at", "session_id"})
NORMALIZABLE_CREATION_ROW_FIELDS = frozenset(
    AUTHORED_CREATION_ROW_FIELDS | CREATION_CONTROL_FIELDS | CREATION_RUNTIME_OVERLAY_INPUT_FIELDS
)

AGENT_AUTHORED_BEAD_TYPES = tuple(
    sorted(
        bead_type.value
        for bead_type in BeadType
        if bead_type not in {BeadType.SESSION_START, BeadType.SESSION_END, BeadType.CHECKPOINT}
    )
)

AGENT_AUTHORED_REQUIRED_BEAD_FIELDS = (
    "creation_role",
    "type",
    "title",
    "summary",
    "entities",
    "retrieval_eligible",
    "source_turn_ids",
)

AGENT_AUTHORED_REQUIRED_ASSOCIATION_FIELDS = (
    "source_bead_id",
    "target_bead_id",
    "relationship",
    "reason_text",
    "confidence",
)

_STRING_LIST_FIELDS = frozenset(
    {
        "summary",
        "tags",
        "source_turn_ids",
        "retrieval_facts",
        "entities",
        "entity_ids",
        "topics",
        "incident_keys",
        "decision_keys",
        "goal_keys",
        "action_keys",
        "outcome_keys",
        "time_keys",
        "because",
        "supporting_facts",
        "evidence_refs",
        "cause_candidates",
        "effect_candidates",
        "source_refs",
        "derived_from",
        "derived_from_bead_ids",
        "supersedes",
        "superseded_by",
        "decision_conflict_with",
        "context_tags",
        "supports_bead_ids",
        "message_refs",
        "speaker_refs",
        "section_refs",
        "entity_refs",
        "attribute_tags",
        "tool_output_ids",
        "constraints",
    }
)

_OBJECT_LIST_FIELDS = frozenset({"claims", "claim_updates"})


def normalize_authoring_mode(value: Any, *, allow_empty: bool = True) -> AuthoringMode | None:
    """Normalize and validate the explicit authorship execution mode."""

    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return None
    if normalized not in AUTHORING_MODES:
        raise ValueError("authoring_mode must be 'inline' or 'delegated'")
    return cast(AuthoringMode, normalized)


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is Any:
        return {}
    if origin in {Union, UnionType}:
        return {"anyOf": [_annotation_schema(arg) for arg in args]}
    if annotation in {str}:
        return {"type": "string"}
    if annotation in {bool}:
        return {"type": "boolean"}
    if annotation in {int}:
        return {"type": "integer"}
    if annotation in {float}:
        return {"type": "number"}
    if annotation in {list} or origin is list:
        item_annotation = args[0] if args else Any
        return {"type": "array", "items": _annotation_schema(item_annotation)}
    if annotation in {dict} or origin is dict:
        return {"type": "object", "additionalProperties": True}
    if annotation is type(None):
        return {"type": "null"}
    return {}


def _authored_bead_properties() -> dict[str, Any]:
    hints = get_type_hints(Bead)
    properties: dict[str, Any] = {"creation_role": {"type": "string", "enum": ["current_turn", "derived"]}}
    for name in sorted(AGENT_AUTHORED_V1_BEAD_FIELDS - {"creation_role"}):
        schema = _annotation_schema(hints.get(name, Any))
        if name in _STRING_LIST_FIELDS:
            schema = {"type": "array", "items": {"type": "string"}}
        elif name in _OBJECT_LIST_FIELDS:
            schema = {"type": "array", "items": {"type": "object", "additionalProperties": True}}
        elif name == "type":
            schema = {"type": "string", "enum": list(AGENT_AUTHORED_BEAD_TYPES)}
        elif name == "state_change":
            schema = {
                "oneOf": [
                    {"type": "object", "additionalProperties": True},
                    {"type": "string", "description": "Legacy input normalized to {'description': ...}."},
                    {"type": "null"},
                ]
            }
        properties[name] = schema
    return properties


def _association_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "source_bead_id": {"type": "string"},
        "target_bead_id": {"type": "string"},
        "relationship": {"type": "string"},
        "reason_text": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "rationale": {"type": "string"},
        "provenance": {"type": "string"},
        "reason_code": {"type": "string"},
        "evidence_fields": {"type": "array", "items": {"type": "string"}},
        "evidence_bead_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "judge_model": {"type": "string"},
        "prompt_version": {"type": "string"},
        "rubric_version": {"type": "string"},
        "grounding_hash": {"type": "string"},
        "turn_id": {"type": "string"},
        "visible_bead_ids": {"type": "array", "items": {"type": "string"}},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(AGENT_AUTHORED_REQUIRED_ASSOCIATION_FIELDS),
        "additionalProperties": False,
    }


def agent_authored_updates_json_schema() -> dict[str, Any]:
    """Return the generated JSON Schema for ``agent_authored_updates.v1``."""

    association = _association_schema()
    promotion_review = {
        "type": "object",
        "properties": {
            "bead_id": {"type": "string"},
            "promotion_state": {"type": "string"},
            "reason": {"type": "string"},
            "associations": {"type": "array", "items": deepcopy(association)},
        },
        "required": ["bead_id", "promotion_state"],
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": AGENT_AUTHORED_UPDATES_V1,
        "title": "AgentAuthoredUpdatesV1",
        "type": "object",
        "properties": {
            "schema_version": {"const": AGENT_AUTHORED_UPDATES_V1},
            "beads_create": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": _authored_bead_properties(),
                    "required": list(AGENT_AUTHORED_REQUIRED_BEAD_FIELDS),
                    "additionalProperties": False,
                },
            },
            "associations": {"type": "array", "items": association},
            "reviewed_beads": {"type": "array", "items": promotion_review},
        },
        "required": ["schema_version", "beads_create", "associations", "reviewed_beads"],
        "additionalProperties": False,
    }


AGENT_AUTHORED_UPDATES_V1_JSON_SCHEMA = agent_authored_updates_json_schema()


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_json_value(path: str, value: Any, schema: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list):
        if not any(_json_value_valid(value, option) for option in alternatives if isinstance(option, dict)):
            errors.append({"path": path, "code": "no_matching_schema"})
        return

    if "const" in schema and value != schema["const"]:
        errors.append({"path": path, "code": "invalid_const", "expected": schema["const"]})
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"path": path, "code": "invalid_enum", "allowed": list(schema["enum"])})
        return

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_type_matches(value, item) for item in expected_type):
            errors.append({"path": path, "code": "expected_type", "expected": expected_type})
            return
    elif isinstance(expected_type, str) and not _type_matches(value, expected_type):
        errors.append({"path": path, "code": "expected_type", "expected": expected_type})
        return

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append({"path": path, "code": "too_few_items", "min": int(schema["minItems"])})
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append({"path": path, "code": "too_many_items", "max": int(schema["maxItems"])})
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_json_value(f"{path}[{index}]", item, item_schema, errors)

    if isinstance(value, dict):
        raw_properties = schema.get("properties")
        properties: dict[str, Any] = dict(raw_properties) if isinstance(raw_properties, dict) else {}
        required = [str(item) for item in (schema.get("required") or [])]
        missing = [field_name for field_name in required if field_name not in value]
        if missing:
            errors.append({"path": path, "code": "missing_required_fields", "fields": missing})
        if schema.get("additionalProperties") is False:
            unknown = sorted(str(key) for key in value if key not in properties)
            if unknown:
                errors.append({"path": path, "code": "unknown_fields", "fields": unknown})
        for field_name, field_value in value.items():
            field_schema = properties.get(field_name)
            if isinstance(field_schema, dict):
                _validate_json_value(f"{path}.{field_name}", field_value, field_schema, errors)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append({"path": path, "code": "below_minimum", "minimum": schema["minimum"]})
        if "maximum" in schema and value > schema["maximum"]:
            errors.append({"path": path, "code": "above_maximum", "maximum": schema["maximum"]})


def _json_value_valid(value: Any, schema: dict[str, Any]) -> bool:
    errors: list[dict[str, Any]] = []
    _validate_json_value("$", value, schema, errors)
    return not errors


def _drop_unknown_json_value(
    path: str,
    value: Any,
    schema: dict[str, Any],
    dropped: list[dict[str, str]],
) -> Any:
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list):
        matching = next(
            (option for option in alternatives if isinstance(option, dict) and _json_value_valid(value, option)),
            None,
        )
        return (
            _drop_unknown_json_value(path, value, matching, dropped) if isinstance(matching, dict) else deepcopy(value)
        )

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [
                _drop_unknown_json_value(f"{path}[{index}]", item, item_schema, dropped)
                for index, item in enumerate(value)
            ]
        return deepcopy(value)

    if isinstance(value, dict):
        raw_properties = schema.get("properties")
        properties: dict[str, Any] = dict(raw_properties) if isinstance(raw_properties, dict) else {}
        if schema.get("additionalProperties") is not False:
            return deepcopy(value)
        sanitized: dict[str, Any] = {}
        for field_name, field_value in value.items():
            field_schema = properties.get(field_name)
            if not isinstance(field_schema, dict):
                dropped.append({"path": path, "field": str(field_name)})
                continue
            sanitized[field_name] = _drop_unknown_json_value(f"{path}.{field_name}", field_value, field_schema, dropped)
        return sanitized

    return deepcopy(value)


def drop_unknown_agent_authored_updates_v1(value: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Warn-mode normalization that never stores unknown authored fields."""

    dropped: list[dict[str, str]] = []
    sanitized = _drop_unknown_json_value("$", value, AGENT_AUTHORED_UPDATES_V1_JSON_SCHEMA, dropped)
    return (sanitized if isinstance(sanitized, dict) else {}), dropped


def validate_agent_authored_updates_v1_transport(value: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Dependency-light transport validation for the canonical v1 envelope.

    Semantic and lifecycle validation remains runtime-owned. This guard verifies
    that every authoring surface is exchanging the same versioned typed shape.
    """

    errors: list[dict[str, Any]] = []
    _validate_json_value("$", value, AGENT_AUTHORED_UPDATES_V1_JSON_SCHEMA, errors)
    return not errors, errors


def bead_field_ownership_snapshot() -> dict[str, list[str]]:
    """Return the complete, testable Bead-field ownership inventory."""

    return {
        "agent_owned": sorted(AGENT_OWNED_BEAD_FIELDS),
        "runtime_owned": sorted(RUNTIME_OWNED_BEAD_FIELDS),
        "compatibility": sorted(COMPATIBILITY_BEAD_FIELDS),
    }


def authored_contract_snapshot() -> dict[str, Any]:
    """Compact machine-readable contract metadata for prompts and tests."""

    return {
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "authoring_modes": sorted(AUTHORING_MODES),
        "required_bead_fields": list(AGENT_AUTHORED_REQUIRED_BEAD_FIELDS),
        "required_association_fields": list(AGENT_AUTHORED_REQUIRED_ASSOCIATION_FIELDS),
        "agent_owned_bead_fields": sorted(AGENT_OWNED_BEAD_FIELDS),
        "allowed_bead_types": list(AGENT_AUTHORED_BEAD_TYPES),
        "current_turn_rows": 1,
        "max_derived_rows": 2,
        "derived_from_sentinel": "$current_turn",
        "unknown_fields": "reject_hard_drop_warn_never_store",
    }


__all__ = [
    "AGENT_AUTHORED_BEAD_TYPES",
    "AGENT_AUTHORED_REQUIRED_ASSOCIATION_FIELDS",
    "AGENT_AUTHORED_REQUIRED_BEAD_FIELDS",
    "AGENT_AUTHORED_UPDATES_V1",
    "AGENT_AUTHORED_UPDATES_V1_JSON_SCHEMA",
    "AGENT_AUTHORED_V1_BEAD_FIELDS",
    "AGENT_OWNED_BEAD_FIELDS",
    "AUTHORING_MODES",
    "AgentAuthoredAssociationV1",
    "AgentAuthoredBeadV1",
    "AgentAuthoredUpdatesV1",
    "AuthoringMode",
    "AUTHORED_CREATION_CONTROL_FIELDS",
    "AUTHORED_CREATION_ROW_FIELDS",
    "BEAD_FIELD_NAMES",
    "COMPATIBILITY_BEAD_FIELDS",
    "CREATION_CONTROL_FIELDS",
    "CREATION_RUNTIME_OVERLAY_INPUT_FIELDS",
    "CREATION_STRUCTURAL_INPUT_FIELDS",
    "NORMALIZABLE_CREATION_ROW_FIELDS",
    "RUNTIME_OWNED_BEAD_FIELDS",
    "RUNTIME_CREATION_CONTROL_FIELDS",
    "PromotionReviewV1",
    "agent_authored_updates_json_schema",
    "authored_contract_snapshot",
    "bead_field_ownership_snapshot",
    "drop_unknown_agent_authored_updates_v1",
    "normalize_authoring_mode",
    "validate_agent_authored_updates_v1_transport",
]
