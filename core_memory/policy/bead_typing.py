from __future__ import annotations

import json
import logging
import os
import re
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

Classify based on the *semantic content* of the user message, not just speech form.
- Questions are retrieval/context turns, not declarative memory writes. Classify questions as context.
- Imperatives like "Record that...", "Show...", "Explain...", "As of..." should be typed by what they encode.

Strong hints:
- decision: winner/choice between alternatives when asserted as a fact ("X won over Y", "we chose")
- goal: target, deadline, objective ("deadline", "target", "must", "need to")
- evidence: benchmark/load-test/measured/proof/justification statements
- lesson: takeaway/learning or guidance learned from prior incidents

If uncertain, use context.

Return JSON only: {{"type": "..."}}

USER: {user_query}"""


_QUESTION_START_RE = re.compile(r"^\s*(?:who|what|when|where|why|how|which|did|do|does|can|could|should|would|is|are|was|were)\b", re.IGNORECASE)


def _is_question_turn(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return s.endswith("?") or bool(_QUESTION_START_RE.search(s))


def _heuristic_type(user_query: str, assistant_final: str) -> BeadType:
    """Conservative fallback when LLM classification is unavailable.

    Intentionally avoids hard-coded semantic routing.
    """
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

    # A user question is a retrieval act, not a durable decision/lesson/precedent
    # declaration. Guard before model calls to prevent promotion inflation.
    if _is_question_turn(user_query):
        return "context"

    # Try LLM classification: Anthropic first, then OpenAI.
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
