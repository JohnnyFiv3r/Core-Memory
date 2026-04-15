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
- Questions can still be decision/goal/evidence/etc if they are about those topics.
- Imperatives like "Record that...", "Show...", "Explain...", "As of..." should be typed by what they encode.
- Do NOT default all questions to context.

Strong hints:
- decision: winner/choice between alternatives ("why did X win over Y", "which vendor won", "we chose")
- goal: target, deadline, objective ("deadline", "target", "must", "need to")
- evidence: benchmark/load-test/measured/proof/justification statements
- lesson: takeaway/learning or guidance learned from prior incidents

If uncertain, use context.

Return JSON only: {{"type": "..."}}

USER: {user_query}"""


def _heuristic_type(user_query: str, assistant_final: str) -> BeadType:
    """Keyword-based fallback when no LLM is available."""
    text = f"{user_query} {assistant_final}".lower()
    uq = (user_query or "").strip().lower()

    # Imperative record/log phrasing should classify by payload semantics, not as generic context.
    record_like = bool(re.match(r"^(record|log|capture|note)\b", uq))

    decision_terms = ["decide", "decision", "we chose", "chose", "picked", "selected", "adopted", "went with", "won over", "winner", "recommendation"]
    goal_terms = ["goal", "target", "deadline", "by end of", "objective", "milestone", "need to", "must", "cutover", "launch"]
    lesson_terms = ["lesson", "learned", "takeaway", "insight", "never again", "always", "rule of thumb", "guidance"]
    outcome_terms = ["result", "outcome", "shipped", "completed", "launched", "deployed", "achieved", "slipped"]
    evidence_terms = ["evidence", "data shows", "benchmark", "load test", "metric", "measured", "proves", "numbers", "because"]

    # Check most specific patterns first
    if any(k in text for k in decision_terms):
        return "decision"
    if any(k in text for k in goal_terms):
        return "goal"
    if any(k in text for k in lesson_terms):
        return "lesson"
    if any(k in text for k in outcome_terms):
        return "outcome"
    if any(k in text for k in evidence_terms):
        return "evidence"
    if any(k in text for k in ["reversed", "overturned", "changed our mind", "no longer"]):
        return "reversal"
    if any(k in text for k in ["corrected", "correction", "was wrong", "mistake", "fixed"]):
        return "correction"
    if any(k in text for k in ["principle", "guideline", "design rule", "pattern"]):
        return "design_principle"
    if any(k in text for k in ["reflecting", "looking back", "retrospective", "in hindsight"]):
        return "reflection"
    # Prefer context only when no semantic signal exists.
    if record_like:
        return "evidence"
    return "context"


def _strong_signal_type(user_query: str) -> BeadType | None:
    """Deterministic high-confidence classifier for common semantic intents.

    This runs before LLM classification to prevent over-defaulting to `context`
    on prompt-like questions (e.g. "Why did X win over Y?").
    """
    q = (user_query or "").strip().lower()
    if not q:
        return None

    # Decision-like winner/choice phrasing
    if re.search(r"\b(chose|choose|picked|selected|went with|winner|won over|win over|recommendation)\b", q):
        return "decision"
    if re.search(r"^why did\s+.+\s+win\s+over\s+.+\??$", q):
        return "decision"

    # Goal-like targets/deadlines
    if re.search(r"\b(goal|target|deadline|objective|milestone|cutover|launch date)\b", q):
        return "goal"

    # Evidence-like support/measurement/justification
    if re.search(r"\b(evidence|benchmark|load test|metric|measured|data shows|proof|because)\b", q):
        return "evidence"

    # Lesson-like takeaways
    if re.search(r"\b(lesson|learned|takeaway|guidance|rule of thumb|in hindsight)\b", q):
        return "lesson"

    return None


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

    # First pass deterministic strong-signal classification.
    strong = _strong_signal_type(user_query)
    if strong:
        return strong

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
