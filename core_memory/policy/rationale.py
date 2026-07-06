from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .bead_typing import is_retrieval_turn
from core_memory.runtime.semantic_tasks import get_semantic_task_runtime
from core_memory.schema.semantic_tasks import SemanticTaskRequest, TASK_RATIONALE_EXTRACTOR

logger = logging.getLogger(__name__)

_CAUSAL_MARKERS = (
    "because",
    "since",
    "due to",
    "thanks to",
    "as a result of",
    "driven by",
    "caused by",
    "so that",
    "in order to",
)

_DIRECTIVE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:remember|record|note|capture)\s+(?:this|that)?\s*(?:decision|lesson|goal|outcome|fact)?\s*[:\-]?\s*",
    re.IGNORECASE,
)
_QUESTION_START_RE = re.compile(r"^\s*(?:who|what|when|where|why|how|which|did|do|does|can|could|should|would|is|are|was|were)\b", re.IGNORECASE)

_PROMPT = """Extract causal rationale from this memory-capture turn.

Return JSON only: {"because": ["..."]}

Rules:
- Only include reasons actually stated in the user message.
- Do not echo the whole user message.
- Return an empty list when the user is asking a question, speculating, or giving no causal reason.
- Keep each reason short and grounded.

USER: {user_query}
ASSISTANT: {assistant_final}
BEAD_TYPE: {bead_type}
"""
_PROMPT_VERSION = "rationale_extractor.v1"
_OUTPUT_SCHEMA = "memory.rationale_extractor.v1"


def _render_prompt(
    template: str,
    *,
    user_query: str,
    assistant_final: str,
    bead_type: str | None,
) -> str:
    return (
        str(template or "")
        .replace("{user_query}", str(user_query or ""))
        .replace("{assistant_final}", str(assistant_final or ""))
        .replace("{bead_type}", str(bead_type or ""))
    )


def is_question_turn(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return s.endswith("?") or bool(_QUESTION_START_RE.search(s))


def _clean_reason(value: str) -> str:
    s = str(value or "").strip().strip(" .;:-\n\t")
    s = re.sub(r"\s+", " ", s)
    return s[:240].strip()


def _strip_directive(text: str) -> str:
    return _DIRECTIVE_RE.sub("", str(text or "")).strip()


def _looks_like_unsupported_speculation(text: str) -> bool:
    s = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return bool(
        re.match(r"^(?:maybe|perhaps)\b", s)
        or re.match(r"^(?:i\s+think\s+)?we\s+(?:could|might|may)\b", s)
        or re.match(r"^(?:it\s+)?could\s+be\s+worth\b", s)
    )


def _is_obvious_long_turn_dump(reason_key: str, source_norm: str) -> bool:
    """Return true only for clear whole-turn dumps, not short grounded support.

    Short human turns often contain the complete rationale in just a few words.
    A sanitizer that rejects all high-overlap text would erase legitimate support,
    so only long exact/near-exact full-turn dumps are treated as poison here.
    """
    if not reason_key or not source_norm or reason_key != source_norm:
        return False
    return len(source_norm) > 120 or len(source_norm.split()) > 18


def _dedupe_reasons(rows: list[str], *, source_text: str) -> list[str]:
    source_norm = _clean_reason(_strip_directive(source_text)).lower()
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        reason = _clean_reason(row)
        if not reason:
            continue
        key = reason.lower()
        # Unsupported speculation is not causal support even if an agent/model
        # supplied it as a because candidate.
        if _looks_like_unsupported_speculation(key):
            continue
        # Do not reject short quoted/paraphrased user text: for terse human
        # input, the support often is substantially similar to the turn. Only
        # drop obvious long whole-turn dumps.
        if _is_obvious_long_turn_dump(key, source_norm):
            continue
        if len(key) < 4 or key in seen:
            continue
        seen.add(key)
        out.append(reason)
        if len(out) >= 3:
            break
    return out


def _heuristic_extract(user_query: str, assistant_final: str = "", bead_type: str | None = None) -> list[str]:
    text = _strip_directive(user_query)
    if not text:
        return []
    if is_question_turn(text):
        return []

    low = text.lower()
    reasons: list[str] = []

    # Explicit causal markers: keep only the rationale side, not the full utterance.
    for marker in _CAUSAL_MARKERS:
        idx = low.find(marker)
        if idx < 0:
            continue
        after = text[idx + len(marker):]
        # Stop before a new unrelated sentence when possible, but preserve compact
        # multi-clause reasons such as "JSONB support and 2x faster workloads".
        part = re.split(r"(?<=[.!?])\s+(?=[A-Z])", after.strip(), maxsplit=1)[0]
        reasons.append(part)

    # Common goal/decision driver phrasing without an explicit "because".
    if not reasons:
        m = re.search(r"\b(?:legal|security|compliance|customer|benchmark|data|load\s*test|incident)\b[^.!?]*(?:flagged|showed|proved|found|caught|blocked|required|forced)[^.!?]*", text, re.IGNORECASE)
        if m:
            reasons.append(m.group(0))

    # Lesson phrasing: "that's how we caught..." is causal support, not just prose.
    if not reasons:
        m = re.search(r"\bthat['’]?s\s+how\s+([^.!?]+)", text, re.IGNORECASE)
        if m:
            reasons.append(m.group(0))

    return _dedupe_reasons(reasons, source_text=user_query)


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.strip().startswith("json"):
            raw = raw.strip()[4:]
    try:
        obj = json.loads(raw.strip())
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _llm_extract_semantic_task(
    user_query: str,
    assistant_final: str,
    bead_type: str | None,
    *,
    root: str | None = None,
) -> list[str] | None:
    prompt = _render_prompt(
        _PROMPT,
        user_query=user_query,
        assistant_final=assistant_final,
        bead_type=bead_type,
    )
    try:
        result = get_semantic_task_runtime().run(
            SemanticTaskRequest(
                task_type=TASK_RATIONALE_EXTRACTOR,
                root=root,
                prompt=prompt,
                payload={
                    "user_query": user_query,
                    "assistant_final": assistant_final,
                    "bead_type": bead_type or "",
                },
                prompt_version=_PROMPT_VERSION,
                output_schema=_OUTPUT_SCHEMA,
                max_tokens=160,
                temperature=0,
                json_mode=True,
                fallback_mode="heuristic",
                authority_boundary="advisory",
                metadata={"policy": "rationale"},
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("semantic task because extraction failed: %s", exc)
        return None
    if not result.ok:
        logger.debug("semantic task because extraction unavailable status=%s error=%s", result.status, result.error)
        return None
    obj = result.output_json if isinstance(result.output_json, dict) else _parse_json(result.output_text)
    if not isinstance(obj, dict):
        return None
    return _dedupe_reasons([str(x) for x in list(obj.get("because") or [])], source_text=user_query)


def extract_causal_because(
    user_query: str,
    assistant_final: str = "",
    bead_type: str | None = None,
    *,
    root: str | None = None,
) -> list[str]:
    """Return grounded causal rationale points for the bead `because` field.

    This intentionally differs from the old fallback behavior: if no causal
    rationale is present, return [] rather than echoing the user's raw turn.
    Set CORE_MEMORY_BECAUSE_EXTRACTOR_MODE=llm or auto to use a cheap model as
    an extractor; deterministic heuristics remain the default for predictable
    writes and offline adoption.
    """
    mode = str(os.getenv("CORE_MEMORY_BECAUSE_EXTRACTOR_MODE") or "heuristic").strip().lower()
    if mode in {"llm", "auto"} and not is_retrieval_turn(user_query):
        out = _llm_extract_semantic_task(user_query, assistant_final, bead_type, root=root)
        if out:
            return out
        if mode == "llm":
            return []
    return _heuristic_extract(user_query, assistant_final, bead_type)


def sanitize_because_for_turn(
    candidates: list[Any] | None,
    *,
    user_query: str,
    assistant_final: str = "",
    bead_type: str | None = None,
    root: str | None = None,
) -> list[str]:
    """Keep legitimate support, remove obvious poison, and backfill when empty.

    `because` is free-text support for applied semantic labels/state. It may
    legitimately quote or closely paraphrase short user text, so this sanitizer
    acts as a validator/normalizer rather than an aggressive similarity filter.
    It removes unsupported speculation, duplicates, and obvious long whole-turn
    dumps; when nothing remains, it uses the fallback extractor.
    """
    rows = [str(x) for x in list(candidates or []) if str(x).strip()]
    cleaned = _dedupe_reasons(rows, source_text=user_query)
    if cleaned:
        return cleaned
    return extract_causal_because(
        user_query=user_query,
        assistant_final=assistant_final,
        bead_type=bead_type,
        root=root,
    )


__all__ = ["extract_causal_because", "sanitize_because_for_turn", "is_question_turn"]
