from __future__ import annotations

import json
import logging
import os
import re
from typing import Literal

from core_memory.runtime.semantic_tasks import SemanticTaskRequest, get_semantic_task_runtime
from core_memory.runtime.semantic_tasks.contracts import TASK_BEAD_TYPE_CLASSIFIER

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
- Retrieval imperatives like "show me...", "tell me...", "remind me why...", or "explain why..." are also context.
- Declarative capture imperatives like "Record that..." or "Remember that..." should be typed by what they encode.

Strong hints:
- decision: winner/choice between alternatives when asserted as a fact ("X won over Y", "we chose")
- goal: target, deadline, objective ("deadline", "target", "must", "need to")
- evidence: benchmark/load-test/measured/proof/justification statements
- lesson: takeaway/learning or guidance learned from prior incidents

If uncertain, use context.

Return JSON only: {{"type": "..."}}

USER: {user_query}"""

_PROMPT_VERSION = "bead_type_classifier.v1"
_OUTPUT_SCHEMA = "memory.bead_type_classifier.v1"


_QUESTION_START_RE = re.compile(r"^\s*(?:who|what|when|where|why|how|which|did|do|does|can|could|should|would|is|are|was|were)\b", re.IGNORECASE)
_RETRIEVAL_DIRECTIVE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"show\s+me|tell\s+me|remind\s+me|explain|summarize|find|look\s+up|search\s+for|"
    r"can\s+you\s+(?:show|tell|remind|explain|summarize|find|look\s+up|search)|"
    r"could\s+you\s+(?:show|tell|remind|explain|summarize|find|look\s+up|search)"
    r")\b",
    re.IGNORECASE,
)
_EMBEDDED_QUESTION_RE = re.compile(r"\b(?:who|what|when|where|why|how|which)\b", re.IGNORECASE)


def _is_question_turn(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return s.endswith("?") or bool(_QUESTION_START_RE.search(s))


def is_retrieval_turn(text: str) -> bool:
    """Return true for turns asking to retrieve/explain existing memory.

    This is intentionally separate from declarative capture directives such as
    "record that" or "remember that". Retrieval turns should be stored only as
    context, never promoted as decisions/lessons/precedents.
    """
    s = str(text or "").strip()
    if not s:
        return False
    if _is_question_turn(s):
        return True
    if _RETRIEVAL_DIRECTIVE_RE.search(s):
        return True
    lowered = s.lower()
    if lowered.startswith(("i'm asking", "i am asking", "my question is")) and _EMBEDDED_QUESTION_RE.search(s):
        return True
    return False


def _heuristic_type(user_query: str, assistant_final: str) -> BeadType:
    """Conservative fallback when LLM classification is unavailable.

    Intentionally avoids hard-coded semantic routing.
    """
    return "context"


def _parse_json(text: str) -> dict | None:
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


def _type_from_obj(obj: dict | None) -> BeadType | None:
    if not isinstance(obj, dict):
        return None
    t = str(obj.get("type", "")).strip().lower()
    if t in CLASSIFIABLE_TYPES:
        return t  # type: ignore[return-value]
    return None


def _classify_semantic_task(user_query: str, assistant_final: str, *, root: str | None = None) -> BeadType | None:
    try:
        result = get_semantic_task_runtime().run(
            SemanticTaskRequest(
                task_type=TASK_BEAD_TYPE_CLASSIFIER,
                root=root,
                prompt=_CLASSIFY_PROMPT.format(user_query=user_query),
                payload={
                    "user_query": user_query,
                    "assistant_final": assistant_final,
                    "classifiable_types": list(CLASSIFIABLE_TYPES),
                },
                prompt_version=_PROMPT_VERSION,
                output_schema=_OUTPUT_SCHEMA,
                max_tokens=80,
                temperature=0,
                json_mode=True,
                fallback_mode="heuristic_context",
                authority_boundary="advisory",
                metadata={"policy": "bead_typing"},
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("semantic task bead typing failed: %s", exc)
        return None
    if not result.ok:
        logger.debug("semantic task bead typing unavailable status=%s error=%s", result.status, result.error)
        return None
    parsed = result.output_json if isinstance(result.output_json, dict) else _parse_json(result.output_text)
    classified = _type_from_obj(parsed)
    if not classified:
        logger.debug("semantic task bead typing returned invalid payload: %r", parsed or result.output_text)
    return classified


def classify_bead_type(user_query: str, assistant_final: str, *, root: str | None = None) -> BeadType:
    """Classify a turn into a bead type through the semantic task runtime.

    Uses the cheap semantic model tier when configured, then falls back to the
    conservative heuristic when allowed.
    """
    allow_fallback = str(
        os.getenv("CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}

    # A user question/retrieval request is not a durable decision/lesson/precedent
    # declaration. Guard before model calls to prevent promotion inflation.
    if is_retrieval_turn(user_query):
        return "context"

    result = _classify_semantic_task(user_query, assistant_final, root=root)
    if result:
        return result

    # Fallback to heuristic
    if allow_fallback:
        return _heuristic_type(user_query, assistant_final)

    raise RuntimeError("bead_type_llm_unavailable: semantic task unavailable and fallback disabled")
