from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .bead_typing import CLASSIFIABLE_TYPES, classify_bead_type, is_retrieval_turn
from .rationale import extract_causal_because, sanitize_because_for_turn, is_question_turn

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = set(CLASSIFIABLE_TYPES)

_PROMPT = """You are the Core Memory bead-field judge. Author every semantic field for one memory bead from this finalized conversation turn.

Return JSON only with this shape:
{
  "type": "decision|goal|lesson|outcome|evidence|context|precedent|design_principle|reflection|correction|reversal",
  "title": "short factual title",
  "summary": ["1-3 concise factual bullets"],
  "detail": "assistant-side detail or concise turn detail",
  "because": [
    {"text":"causal rationale", "category":"cause|purpose|constraint|evidence|tradeoff|comparison|mechanism|conditional|preference|value", "source_span":"exact source text", "confidence":0.0, "stated":"direct|inferred"}
  ],
  "supporting_facts": ["facts from the turn supporting the bead"],
  "evidence_refs": ["stable ids only if explicitly present"],
  "entities": ["named entities / project terms"],
  "topics": ["short topical labels"],
  "state_change": "optional state transition text or empty",
  "validity": "current|deprecated|uncertain|",
  "retrieval_eligible": true,
  "retrieval_title": "search-optimized title or empty",
  "retrieval_facts": ["search-optimized durable facts"],
  "effective_from": "ISO/date text only if stated or empty",
  "effective_to": "ISO/date text only if stated or empty",
  "observed_at": "ISO/date text only if stated or empty"
}

Rules:
- Judge every semantic field. Do not copy deterministic defaults unless they are the best judged value.
- Do not echo the whole user message as `because`.
- `because` must be exhaustive over English causal forms: cause/effect, purpose, constraint, evidence/result, tradeoff, comparison, mechanism, conditional rationale, preference/value.
- Include implicit rationale only when grounded in the turn; mark it `inferred` and lower confidence.
- For questions/retrieval turns, use type `context` and empty `because` unless the turn also states a durable fact with rationale.
- Speculation without a grounded reason gets empty `because`.
- Never invent evidence refs or dates. If no stable id/date appears, return []/empty.
- `retrieval_eligible` should be true only for durable semantic memory worth later recall.
- Keep arrays short and deduplicated.

USER: {user_query}
ASSISTANT: {assistant_final}
"""


def _clean_text(value: Any, *, limit: int = 240) -> str:
    s = re.sub(r"\s+", " ", str(value or "").strip())
    return s[:limit].strip()


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
        "the", "and", "for", "with", "that", "this", "from", "into", "your", "our", "their",
        "were", "was", "have", "has", "had", "will", "would", "should", "could", "can", "cant",
        "about", "after", "before", "then", "than", "because", "there", "here", "when", "where",
        "what", "which", "who", "whom", "whose", "why", "how", "turn", "main", "session",
        "these", "those", "decision", "confirmed", "logged",
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


def _fallback_bead_fields(user_query: str, assistant_final: str = "") -> dict[str, Any]:
    uq = str(user_query or "").strip()
    af = str(assistant_final or "").strip()
    text = uq or af or "turn memory"
    return {
        "type": classify_bead_type(user_query=uq, assistant_final=af),
        "title": (text.splitlines()[0] if text else "Turn memory")[:160] or "Turn memory",
        "summary": [_clean_text(text, limit=240) or "turn memory"],
        "detail": (af or text)[:1200],
        "because": extract_causal_because(user_query=uq, assistant_final=af),
        "supporting_facts": [],
        "evidence_refs": [],
        "entities": _heuristic_entities(uq, af),
        "topics": [],
        "state_change": "",
        "validity": "",
        "retrieval_eligible": False,
        "retrieval_title": "",
        "retrieval_facts": [],
        "effective_from": "",
        "effective_to": "",
        "observed_at": "",
        "judge": {"mode": "fallback"},
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


def _llm_judge_anthropic(user_query: str, assistant_final: str) -> dict[str, Any] | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic  # type: ignore
        client = anthropic.Anthropic(api_key=key)
        model = os.getenv("CORE_MEMORY_BEAD_FIELD_MODEL") or os.getenv("CORE_MEMORY_BECAUSE_MODEL") or os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL") or "claude-haiku-4-5-20251001"
        resp = client.messages.create(
            model=model,
            max_tokens=700,
            temperature=0,
            messages=[{"role": "user", "content": _PROMPT.format(user_query=user_query, assistant_final=assistant_final)}],
        )
        return _parse_json(resp.content[0].text)
    except Exception as exc:
        logger.debug("anthropic bead-field judge failed: %s", exc)
        return None


def _llm_judge_openai(user_query: str, assistant_final: str) -> dict[str, Any] | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=key)
        model = os.getenv("CORE_MEMORY_BEAD_FIELD_MODEL") or os.getenv("CORE_MEMORY_BECAUSE_MODEL") or os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL") or "gpt-4o-mini"
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=700,
            messages=[{"role": "user", "content": _PROMPT.format(user_query=user_query, assistant_final=assistant_final)}],
        )
        return _parse_json(resp.choices[0].message.content or "")
    except Exception as exc:
        logger.debug("openai bead-field judge failed: %s", exc)
        return None


def _normalize_judged_fields(obj: dict[str, Any], *, user_query: str, assistant_final: str, mode: str) -> dict[str, Any]:
    fallback = _fallback_bead_fields(user_query, assistant_final)
    forced_context = is_retrieval_turn(user_query)
    btype = _clean_text(obj.get("type"), limit=80).lower()
    if forced_context:
        btype = "context"
    elif btype not in _ALLOWED_TYPES:
        btype = str(fallback.get("type") or "context")
    title = _clean_text(obj.get("title"), limit=160) or str(fallback.get("title") or "Turn memory")
    summary = _clean_list(obj.get("summary"), limit=3, item_limit=240) or list(fallback.get("summary") or [])
    detail = _clean_text(obj.get("detail"), limit=1200) or str(fallback.get("detail") or "")
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
    )
    if forced_context or (is_question_turn(user_query) and btype == "context"):
        because = []
    return {
        "type": btype,
        "title": title,
        "summary": summary,
        "detail": detail,
        "because": because,
        "supporting_facts": _clean_list(obj.get("supporting_facts"), limit=6),
        "evidence_refs": _clean_list(obj.get("evidence_refs"), limit=6, item_limit=120),
        "entities": _clean_list(obj.get("entities"), limit=16, item_limit=120),
        "topics": _clean_list(obj.get("topics"), limit=8, item_limit=80),
        "state_change": _clean_text(obj.get("state_change"), limit=240),
        "validity": _clean_text(obj.get("validity"), limit=80),
        "retrieval_eligible": False if forced_context else bool(obj.get("retrieval_eligible", False)),
        "retrieval_title": _clean_text(obj.get("retrieval_title"), limit=160),
        "retrieval_facts": _clean_list(obj.get("retrieval_facts"), limit=6),
        "effective_from": _clean_text(obj.get("effective_from"), limit=80),
        "effective_to": _clean_text(obj.get("effective_to"), limit=80),
        "observed_at": _clean_text(obj.get("observed_at"), limit=80),
        "judge": {"mode": mode},
    }


def judge_bead_fields(user_query: str, assistant_final: str = "") -> dict[str, Any]:
    """LLM-first semantic bead-field judge with deterministic fallback.

    The normal write path should use this to author every semantic field. The
    fallback exists for offline/test deployments, not as the preferred policy.
    """
    uq = str(user_query or "")
    af = str(assistant_final or "")
    mode = str(os.getenv("CORE_MEMORY_BEAD_FIELD_JUDGE_MODE") or "auto").strip().lower()
    if mode not in {"auto", "llm", "heuristic", "off"}:
        mode = "auto"
    if mode in {"auto", "llm"}:
        obj = _llm_judge_anthropic(uq, af)
        if obj is None:
            obj = _llm_judge_openai(uq, af)
        if isinstance(obj, dict):
            return _normalize_judged_fields(obj, user_query=uq, assistant_final=af, mode="llm")
        if mode == "llm":
            out = _fallback_bead_fields(uq, af)
            out["judge"] = {"mode": "llm_failed_fallback"}
            return out
    out = _fallback_bead_fields(uq, af)
    out["judge"] = {"mode": "heuristic"}
    return out


__all__ = ["judge_bead_fields"]
