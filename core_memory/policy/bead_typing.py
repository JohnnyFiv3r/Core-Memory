from __future__ import annotations

import json
import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

# Canonical classifiable bead types (first-class output contract)
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
]

# Legacy semantic labels accepted on read/parse compatibility paths.
# New writes/classifier output should use canonical `type` values.
LEGACY_SEMANTIC_TO_TYPE = {
    "decision": "decision",
    "change": "outcome",
    "observation": "lesson",
    "evidence": "evidence",
    "outcome": "outcome",
    "constraint": "goal",
    "lesson": "lesson",
    "correction": "correction",
    "context": "context",
}

LEGACY_4TYPE_MAP = {
    "decision": "decision",
    "goal": "decision",
    "precedent": "decision",
    "design_principle": "decision",
    "outcome": "outcome",
    "correction": "outcome",
    "reversal": "outcome",
    "lesson": "lesson",
    "reflection": "lesson",
    "evidence": "lesson",
    "context": "context",
}

_CLASSIFY_PROMPT = """Classify this conversation turn into exactly one bead type.
Types:
- decision: A choice was made between alternatives
- goal: A target, deadline, objective, or required future state was stated
- lesson: A takeaway or learned principle from experience
- outcome: A result or consequence of prior action
- evidence: Data, metrics, logs, or proof supporting a claim
- context: Background, framing, or informational request without new durable decision/value
- precedent: A past example referenced as reusable pattern
- design_principle: An architectural or design rule/guideline
- reflection: Retrospective thought about process/approach
- correction: Fixing a prior mistake or misunderstanding
- reversal: Overturning/retracting a previous decision

Classify based ONLY on the user message below. Ignore assistant response.
- If the user is asking a question or requesting information, classify as context.
- Only use non-context labels when the user states or declares something durable.
If uncertain, use context.
Return JSON only: {{"type": "..."}}
USER: {user_query}
"""


def _normalize_classifier_label(raw: str) -> str:
    t = str(raw or "").strip().lower()
    if t in CLASSIFIABLE_TYPES:
        return t
    if t in LEGACY_SEMANTIC_TO_TYPE:
        return LEGACY_SEMANTIC_TO_TYPE[t]
    return ""


def map_expanded_to_legacy(bead_type: str) -> Literal["decision", "outcome", "lesson", "context"]:
    return LEGACY_4TYPE_MAP.get(str(bead_type or "").strip().lower(), "context")  # type: ignore[return-value]


def _heuristic_type(user_query: str, assistant_final: str) -> BeadType:
    """Keyword fallback classification when LLM providers are unavailable."""
    text = f"{user_query} {assistant_final}".lower()

    if any(k in text for k in ["goal", "target", "deadline", "objective", "milestone", "need to", "must"]):
        return "goal"
    if any(k in text for k in ["decide", "decision", "we chose", "chose", "picked", "selected", "adopted", "went with"]):
        return "decision"
    if any(k in text for k in ["precedent", "last time", "previously", "similar case", "as before"]):
        return "precedent"
    if any(k in text for k in ["principle", "guideline", "design rule", "architectural rule", "pattern"]):
        return "design_principle"
    if any(k in text for k in ["reflecting", "looking back", "retrospective", "in hindsight"]):
        return "reflection"
    if any(k in text for k in ["corrected", "correction", "was wrong", "mistake", "fixed"]):
        return "correction"
    if any(k in text for k in ["reversed", "overturned", "changed our mind", "retracted", "no longer"]):
        return "reversal"
    if any(k in text for k in ["evidence", "data shows", "benchmark", "metric", "measured", "proves", "numbers", "logs"]):
        return "evidence"
    if any(k in text for k in ["lesson", "learned", "takeaway", "insight", "never again", "always", "rule of thumb"]):
        return "lesson"
    if any(k in text for k in ["result", "outcome", "shipped", "completed", "launched", "deployed", "achieved", "resolved"]):
        return "outcome"
    return "context"


def _extract_json_block(text: str) -> str:
    t = str(text or "").strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1]
            if t.startswith("json"):
                t = t[4:]
    return t.strip()


def _parse_classifier_output(text: str) -> str:
    t = _extract_json_block(text)
    try:
        obj = json.loads(t)
    except Exception:
        return ""
    # Canonical key first, legacy semantic fallback second.
    cand = _normalize_classifier_label(obj.get("type"))
    if cand:
        return cand
    cand = _normalize_classifier_label(obj.get("semantic_type"))
    return cand


def _classify_anthropic(user_query: str) -> BeadType | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=key)
        model = os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL") or "claude-3-5-haiku-latest"
        resp = client.messages.create(
            model=model,
            max_tokens=60,
            temperature=0,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(user_query=user_query)}],
        )
        text = ""
        if getattr(resp, "content", None):
            first = resp.content[0]
            text = getattr(first, "text", "") or ""
        label = _parse_classifier_output(text)
        if label:
            return label  # type: ignore[return-value]
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
        model = os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL") or os.getenv("OPENCLAW_DEFAULT_MODEL") or "gpt-4o-mini"

        # Chris-style chat completions path (primary)
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=60,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(user_query=user_query)}],
        )
        text = (resp.choices[0].message.content or "").strip()
        label = _parse_classifier_output(text)
        if label:
            return label  # type: ignore[return-value]

        # Backstop: responses API compatibility
        prompt = (
            "Classify this memory turn into exactly one bead type from: "
            + ", ".join(CLASSIFIABLE_TYPES)
            + ". Return JSON only: {\"type\":\"...\"}.\n"
            + f"USER: {user_query}\nASSISTANT: {assistant_final}"
        )
        resp2 = client.responses.create(model=model, input=prompt, temperature=0, max_output_tokens=60)
        text2 = (resp2.output_text or "").strip()
        label2 = _parse_classifier_output(text2)
        if label2:
            return label2  # type: ignore[return-value]

    except Exception as exc:
        logger.debug("openai bead typing failed: %s", exc)
    return None


def classify_bead_type(user_query: str, assistant_final: str) -> BeadType:
    """Classify a turn into canonical expanded bead type.

    Provider order: Anthropic -> OpenAI -> heuristic fallback (if enabled).
    Forward contract: emit `type` in expanded taxonomy.
    Read compatibility: parser also accepts legacy `semantic_type` key.
    """
    allow_fallback = str(os.getenv("CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    out = _classify_anthropic(user_query)
    if out:
        return out

    out = _classify_openai(user_query, assistant_final)
    if out:
        return out

    if allow_fallback:
        return _heuristic_type(user_query, assistant_final)

    raise RuntimeError("bead_type_llm_unavailable: no provider key found and fallback disabled")
