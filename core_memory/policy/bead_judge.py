from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from typing import Any

from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime
from core_memory.policy.turn_memory_authoring import build_turn_memory_authoring_request
from core_memory.schema.agent_authored_updates import (
    AGENT_AUTHORED_UPDATES_V1,
    AGENT_AUTHORED_V1_BEAD_FIELDS,
)
from core_memory.schema.normalization import normalize_state_change
from core_memory.schema.semantic_tasks import TASK_BEAD_FIELD_JUDGE

from .bead_typing import CLASSIFIABLE_TYPES, classify_bead_type, is_retrieval_turn
from .rationale import extract_causal_because, is_question_turn, sanitize_because_for_turn

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = set(CLASSIFIABLE_TYPES)


def _clean_text(value: Any, *, limit: int = 240) -> str:
    s = re.sub(r"\s+", " ", str(value or "").strip())
    if len(s) > int(limit):
        logger.warning("bead_judge.text_truncated limit=%d original_len=%d", int(limit), len(s))
    return s[:limit].strip()


def _prompt_template() -> str:
    prompt_file = str(os.getenv("CORE_MEMORY_BEAD_FIELD_PROMPT_FILE") or "").strip()
    if prompt_file:
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("bead_judge.prompt_file_unreadable path=%s error=%s", prompt_file, exc)
    return str(os.getenv("CORE_MEMORY_BEAD_FIELD_PROMPT") or "").strip()


def _fail_closed_enabled() -> bool:
    return str(os.getenv("CORE_MEMORY_BEAD_FIELD_JUDGE_FAIL_CLOSED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _render_prompt(template: str, *, user_query: str, assistant_final: str) -> str:
    return (
        str(template or "")
        .replace("{user_query}", str(user_query or ""))
        .replace("{assistant_final}", str(assistant_final or ""))
    )


def _clean_list(value: Any, *, limit: int = 6, item_limit: int = 240) -> list[str]:
    rows = value if isinstance(value, list) else ([] if value is None else [value])
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            row = row.get("text") or row.get("value") or row.get("label") or row.get("name") or ""
        s = _clean_text(row, limit=item_limit)
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _heuristic_entities(*texts: str, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "your",
        "our",
        "their",
        "were",
        "was",
        "have",
        "has",
        "had",
        "will",
        "would",
        "should",
        "could",
        "can",
        "cant",
        "about",
        "after",
        "before",
        "then",
        "than",
        "because",
        "there",
        "here",
        "when",
        "where",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "turn",
        "main",
        "session",
        "these",
        "those",
        "decision",
        "confirmed",
        "logged",
    }
    for raw in texts:
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9._-]{2,}\b", str(raw or "")):
            key = token.lower().strip(".,:;!?()[]{}\"'")
            if key in stop or key in seen:
                continue
            seen.add(key)
            out.append(token.strip(".,:;!?()[]{}\"'"))
            if len(out) >= max(1, int(limit)):
                return out
    return out


def _heuristic_topics(*texts: str, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    stop = {
        "about",
        "after",
        "again",
        "assistant",
        "because",
        "before",
        "could",
        "decision",
        "from",
        "have",
        "memory",
        "should",
        "that",
        "their",
        "there",
        "these",
        "this",
        "those",
        "turn",
        "user",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
    }
    for raw in texts:
        for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{3,}\b", str(raw or "").lower()):
            if token in stop or token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= max(1, int(limit)):
                return out
    return out


_ALLOWED_CLAIM_KINDS = {
    "preference",
    "identity",
    "policy",
    "commitment",
    "condition",
    "location",
    "relationship",
    "custom",
}


def _normalize_judged_claims(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalize LLM-produced claims to the canonical claim schema."""
    import uuid

    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("claim_kind") or "").strip().lower()
        if kind not in _ALLOWED_CLAIM_KINDS:
            kind = "custom"
        subject = _clean_text(item.get("subject"), limit=120)
        slot = _clean_text(item.get("slot"), limit=120)
        value = _clean_text(item.get("value"), limit=200)
        reason_text = _clean_text(item.get("reason_text"), limit=240)
        if not subject or not slot or not value or not reason_text:
            continue
        try:
            confidence = float(item.get("confidence") or 0.0)
            confidence = round(max(0.0, min(1.0, confidence)), 4)
        except (TypeError, ValueError):
            confidence = 0.7
        out.append(
            {
                "id": str(uuid.uuid4()),
                "claim_kind": kind,
                "subject": subject,
                "slot": slot,
                "value": value,
                "reason_text": reason_text,
                "confidence": confidence,
            }
        )
    return out


def _fallback_bead_fields(user_query: str, assistant_final: str = "", *, root: str | None = None) -> dict[str, Any]:
    uq = str(user_query or "").strip()
    af = str(assistant_final or "").strip()
    text = uq or af or "turn memory"
    title = (text.splitlines()[0] if text else "Turn memory")[:160] or "Turn memory"
    summary = [_clean_text(text, limit=240) or "turn memory"]
    durable = bool(text.strip()) and not is_retrieval_turn(uq)
    return {
        "type": classify_bead_type(user_query=uq, assistant_final=af, root=root),
        "title": title,
        "summary": summary,
        "detail": _clean_text(af or text, limit=1200),
        "because": extract_causal_because(user_query=uq, assistant_final=af, root=root),
        "supporting_facts": [],
        "evidence_refs": [],
        "entities": _heuristic_entities(uq, af),
        "topics": _heuristic_topics(uq, af),
        "state_change": "",
        "validity": "",
        "retrieval_eligible": durable,
        "effective_from": "",
        "effective_to": "",
        "observed_at": "",
        "claims": [],
        "judge": {"mode": "fallback", "retrieval_authored_by": "heuristic"},
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.strip().startswith("json"):
            raw = raw.strip()[4:]
    try:
        obj = json.loads(raw.strip())
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _llm_judge_semantic_task(
    user_query: str,
    assistant_final: str,
    *,
    root: str | None = None,
) -> dict[str, Any] | None:
    request, _grounding_hash = build_turn_memory_authoring_request(
        root=root,
        req={
            "session_id": "bead-judge-compat",
            "turn_id": "bead-judge-compat",
            "turns": [
                {"speaker": "user", "role": "user", "content": user_query},
                {"speaker": "assistant", "role": "assistant", "content": assistant_final},
            ],
            "speakers": ["user", "assistant"],
            "tools_trace": [],
            "mesh_trace": [],
            "window_turn_ids": [],
            "window_bead_ids": [],
        },
        crawler_context={"session_id": "bead-judge-compat", "visible_bead_ids": [], "beads": []},
        task_type=TASK_BEAD_FIELD_JUDGE,
        metadata={"compatibility_alias_for": "turn_memory_authoring"},
        additional_instructions=_render_prompt(
            _prompt_template(), user_query=user_query, assistant_final=assistant_final
        ),
        fallback_mode="heuristic",
        authority_boundary="advisory",
    )
    try:
        result = get_semantic_task_runtime().run(request)
    except Exception as exc:  # noqa: BLE001
        logger.debug("semantic task bead-field judge failed: %s", exc)
        return None
    if not result.ok:
        logger.debug("semantic task bead-field judge unavailable status=%s error=%s", result.status, result.error)
        return None
    obj = result.output_json if isinstance(result.output_json, dict) else _parse_json(result.output_text)
    if not isinstance(obj, dict):
        return None
    if obj.get("schema_version") == AGENT_AUTHORED_UPDATES_V1:
        rows = [row for row in (obj.get("beads_create") or []) if isinstance(row, dict)]
        primary = next(
            (row for row in rows if str(row.get("creation_role") or "") == "current_turn"),
            rows[0] if rows else None,
        )
        return dict(primary) if isinstance(primary, dict) else None
    # One-release compatibility for custom/fake runtimes that still return the
    # old narrow bead object. Provider prompts now request the full v1 envelope.
    return obj


def _normalize_judged_fields(
    obj: dict[str, Any],
    *,
    user_query: str,
    assistant_final: str,
    mode: str,
    root: str | None = None,
) -> dict[str, Any]:
    fallback: dict[str, Any] | None = None

    def fallback_fields() -> dict[str, Any]:
        nonlocal fallback
        if fallback is None:
            fallback = _fallback_bead_fields(user_query, assistant_final, root=root)
        return fallback

    forced_context = is_retrieval_turn(user_query)
    btype = _clean_text(obj.get("type"), limit=80).lower()
    if forced_context:
        btype = "context"
    elif btype not in _ALLOWED_TYPES:
        btype = str(fallback_fields().get("type") or "context")
    title = _clean_text(obj.get("title"), limit=160) or str(fallback_fields().get("title") or "Turn memory")
    summary = _clean_list(obj.get("summary"), limit=3, item_limit=240) or list(fallback_fields().get("summary") or [])
    detail = _clean_text(obj.get("detail"), limit=1200) or str(fallback_fields().get("detail") or "")
    because_candidates: list[str] = []
    for row in list(obj.get("because") or []):
        if isinstance(row, dict):
            text = row.get("text") or row.get("reason") or ""
            span = _clean_text(row.get("source_span"), limit=240)
            reason = _clean_text(text, limit=240)
            # Require some grounding for inferred rationale; direct reasons may be the text.
            if reason and (span or str(row.get("stated") or "").lower() == "direct"):
                because_candidates.append(reason)
        else:
            because_candidates.append(str(row))
    because = sanitize_because_for_turn(
        because_candidates,
        user_query=user_query,
        assistant_final=assistant_final,
        bead_type=btype,
        root=root,
    )
    if forced_context or (is_question_turn(user_query) and btype == "context"):
        because = []
    normalized = {key: deepcopy(value) for key, value in obj.items() if key in AGENT_AUTHORED_V1_BEAD_FIELDS}
    normalized.update(
        {
            "type": btype,
            "title": title,
            "summary": summary,
            "detail": detail,
            "because": because,
            "supporting_facts": _clean_list(obj.get("supporting_facts"), limit=6),
            "evidence_refs": _clean_list(obj.get("evidence_refs"), limit=6, item_limit=120),
            "entities": _clean_list(obj.get("entities"), limit=16, item_limit=120),
            "topics": _clean_list(obj.get("topics"), limit=8, item_limit=80),
            "state_change": normalize_state_change(obj.get("state_change")),
            "validity": _clean_text(obj.get("validity"), limit=80),
            "retrieval_title": _clean_text(obj.get("retrieval_title"), limit=240),
            "retrieval_facts": _clean_list(obj.get("retrieval_facts"), limit=8, item_limit=320),
            "retrieval_eligible": False if forced_context else bool(obj.get("retrieval_eligible", True)),
            "effective_from": _clean_text(obj.get("effective_from"), limit=80),
            "effective_to": _clean_text(obj.get("effective_to"), limit=80),
            "observed_at": _clean_text(obj.get("observed_at"), limit=80),
            "claims": _normalize_judged_claims(obj.get("claims")),
            "judge": {"mode": mode},
        }
    )
    return normalized


def judge_bead_fields(
    user_query: str,
    assistant_final: str = "",
    *,
    mode: str | None = None,
    root: str | None = None,
) -> dict[str, Any]:
    """LLM-first semantic bead-field judge with deterministic fallback.

    The normal write path should use this to author every semantic field. The
    fallback exists for offline/test deployments, not as the preferred policy.

    Pass ``mode`` to override the ``CORE_MEMORY_BEAD_FIELD_JUDGE_MODE`` env var
    for a single call (e.g. per-request directive from ``metadata["bead_judge"]``).
    """
    uq = str(user_query or "")
    af = str(assistant_final or "")
    if mode is None:
        mode = str(os.getenv("CORE_MEMORY_BEAD_FIELD_JUDGE_MODE") or "auto").strip().lower()
    if mode not in {"auto", "llm", "heuristic", "off"}:
        mode = "auto"
    if mode in {"auto", "llm"}:
        obj = _llm_judge_semantic_task(uq, af, root=root)
        if isinstance(obj, dict):
            return _normalize_judged_fields(obj, user_query=uq, assistant_final=af, mode="llm", root=root)
        if _fail_closed_enabled():
            raise RuntimeError("bead_field_judge_unavailable: semantic task unavailable and fallback disabled")
        if mode == "llm":
            out = _fallback_bead_fields(uq, af, root=root)
            judge = dict(out.get("judge") or {})
            judge["mode"] = "llm_failed_fallback"
            out["judge"] = judge
            return out
    out = _fallback_bead_fields(uq, af, root=root)
    judge = dict(out.get("judge") or {})
    judge["mode"] = "heuristic"
    out["judge"] = judge
    return out


__all__ = ["judge_bead_fields"]
