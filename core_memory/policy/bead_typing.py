from __future__ import annotations

import json
import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

# Core bead types that the classifier should distinguish between.
# Structural types (session_start, session_end, checkpoint) are set by the
# runtime, not by classification.
CLASSIFIABLE_TYPES = (
    "decision",
    "goal",
    "lesson",
    "outcome",
    "evidence",
    "context",
    "precedent",
    "design_principle",
    "reflection",
    "correction",
    "reversal",
)

BeadType = Literal[
    "decision", "goal", "lesson", "outcome", "evidence", "context",
    "precedent", "design_principle", "reflection", "correction", "reversal",
]

_CLASSIFY_PROMPT = """Classify this conversation turn into exactly one bead type.

Types:
- decision: A choice was made between alternatives (e.g. "we chose X over Y", "we went with X", "X won the evaluation")
- goal: A target, deadline, or objective was stated (e.g. "we need to do X by Y", "the goal is X")
- lesson: A takeaway, insight, or learning from experience (e.g. "we learned that", "the lesson is", "always do X")
- outcome: A result or consequence of a completed prior action (e.g. "the migration succeeded", "we shipped X")
- evidence: Data, metrics, or proof supporting a claim (e.g. "benchmarks showed", "the data proves")
- context: Background information or situational detail
- precedent: A past example being referenced as a pattern
- design_principle: An architectural or design guideline
- reflection: A retrospective thought about process or approach
- correction: Fixing a prior mistake or misunderstanding
- reversal: Overturning a previous decision

Classify based ONLY on the user message below. Ignore the assistant response.
- If the user is asking a question or requesting information, classify as context.
- Only use decision, goal, lesson, etc. when the user is explicitly stating or declaring something new.
If uncertain, use context.

Return JSON only: {{"type": "..."}}

USER: {user_query}"""


def _heuristic_type(user_query: str, assistant_final: str) -> BeadType:
    """Keyword-based fallback when no LLM is available."""
    text = f"{user_query} {assistant_final}".lower()

    # Check most specific patterns first
    if any(k in text for k in ["goal", "target", "deadline", "by end of", "objective", "milestone", "need to", "migrate", "must"]):
        return "goal"
    if any(k in text for k in ["decide", "decision", "we chose", "chose", "picked", "selected", "adopted", "went with"]):
        return "decision"
    if any(k in text for k in ["lesson", "learned", "takeaway", "insight", "never again", "always", "rule of thumb"]):
        return "lesson"
    if any(k in text for k in ["result", "outcome", "shipped", "completed", "launched", "deployed", "achieved"]):
        return "outcome"
    if any(k in text for k in ["evidence", "data shows", "benchmark", "metric", "measured", "proves", "numbers"]):
        return "evidence"
    if any(k in text for k in ["reversed", "overturned", "changed our mind", "no longer"]):
        return "reversal"
    if any(k in text for k in ["corrected", "correction", "was wrong", "mistake", "fixed"]):
        return "correction"
    if any(k in text for k in ["principle", "guideline", "design rule", "pattern"]):
        return "design_principle"
    if any(k in text for k in ["reflecting", "looking back", "retrospective", "in hindsight"]):
        return "reflection"
    return "context"


def _classify_anthropic(user_query: str, assistant_final: str) -> BeadType | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        model = os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL") or "claude-haiku-4-5-20251001"
        resp = client.messages.create(
            model=model,
            max_tokens=60,
            temperature=0,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(
                user_query=user_query,
            )}],
        )
        text = resp.content[0].text.strip()
        # Handle both raw JSON and markdown-wrapped JSON
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        obj = json.loads(text)
        t = str(obj.get("type", "")).strip().lower()
        if t in CLASSIFIABLE_TYPES:
            return t  # type: ignore[return-value]
    except Exception as exc:
        logger.debug("anthropic bead typing failed: %s", exc)
    return None


def _classify_openai(user_query: str, assistant_final: str) -> BeadType | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=key)
        model = os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL") or "gpt-4o-mini"
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=60,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(
                user_query=user_query,
            )}],
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        obj = json.loads(text)
        t = str(obj.get("type", "")).strip().lower()
        if t in CLASSIFIABLE_TYPES:
            return t  # type: ignore[return-value]
    except Exception as exc:
        logger.debug("openai bead typing failed: %s", exc)
    return None


def classify_bead_type(user_query: str, assistant_final: str) -> BeadType:
    """Classify a turn into a bead type using the available LLM provider.

    Tries Anthropic first, then OpenAI, then falls back to heuristic.
    Uses a cheap/fast model (Haiku or gpt-4o-mini) to keep latency low.
    """
    allow_fallback = str(
        os.getenv("CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}

    # Try LLM classification: Anthropic first, then OpenAI
    result = _classify_anthropic(user_query, assistant_final)
    if result:
        return result

    result = _classify_openai(user_query, assistant_final)
    if result:
        return result

    # Fallback to heuristic
    if allow_fallback:
        return _heuristic_type(user_query, assistant_final)

    raise RuntimeError("bead_type_llm_unavailable: no API key found and fallback disabled")
