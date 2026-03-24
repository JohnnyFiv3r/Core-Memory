from __future__ import annotations

import json
import os
from typing import Literal

BeadType = Literal["decision", "outcome", "lesson", "context"]
SemanticType = Literal[
    "decision",
    "change",
    "observation",
    "evidence",
    "outcome",
    "constraint",
    "lesson",
    "correction",
    "context",
]


def _map_semantic_to_bead_type(semantic_type: str) -> BeadType:
    t = str(semantic_type or "").strip().lower()
    if t in {"decision", "constraint"}:
        return "decision"
    if t in {"outcome", "change", "correction"}:
        return "outcome"
    if t in {"lesson", "observation", "evidence"}:
        return "lesson"
    return "context"


def _heuristic_semantic_type(user_query: str, assistant_final: str) -> SemanticType:
    text = f"{user_query} {assistant_final}".lower()

    # deterministic bias cues
    if any(k in text for k in ["must", "must not", "should not", "constraint", "requires"]):
        return "constraint"
    if any(k in text for k in ["switched", "changed", "migrated", "replaced", "from", "to"]):
        return "change"
    if any(k in text for k in ["evidence", "found", "observed", "measured", "metric"]):
        return "evidence"
    if any(k in text for k in ["because", "reason", "due to", "rationale", "decide", "decision", "policy"]):
        return "decision"
    if any(k in text for k in ["corrected", "fix", "wrong", "correction"]):
        return "correction"
    if any(k in text for k in ["outcome", "result", "completed", "done", "shipped", "resolved"]):
        return "outcome"
    if any(k in text for k in ["lesson", "learned", "takeaway", "insight"]):
        return "lesson"
    if any(k in text for k in ["observed", "noticed", "saw"]):
        return "observation"
    return "context"


def _heuristic_type(user_query: str, assistant_final: str) -> BeadType:
    return _map_semantic_to_bead_type(_heuristic_semantic_type(user_query, assistant_final))


def classify_bead_type(user_query: str, assistant_final: str) -> BeadType:
    """LLM-first bead typing with loose policy gates.

    Uses the OpenClaw default model by default (via env), and only falls back
    to heuristic classification when explicitly allowed.
    """
    model = (
        os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL")
        or os.getenv("OPENCLAW_DEFAULT_MODEL")
        or os.getenv("OPENCLAW_MODEL")
        or "gpt-4o-mini"
    )
    allow_fallback = str(os.getenv("CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK", "1")).strip().lower() in {"1", "true", "yes", "on"}
    key = os.getenv("OPENAI_API_KEY")

    if key:
        try:
            from openai import OpenAI  # type: ignore

            client = OpenAI(api_key=key)
            prompt = (
                "Classify this memory turn into the most retrieval-useful semantic type from: "
                "decision, change, observation, evidence, outcome, constraint, lesson, correction, context. "
                "Prefer specific durable types over context. "
                "Use context only for low-semantic-delta/runtime chatter. "
                "Return JSON only: {\"semantic_type\":\"...\"}.\n"
                f"USER: {user_query}\nASSISTANT: {assistant_final}"
            )
            resp = client.responses.create(
                model=model,
                input=prompt,
                temperature=0,
                max_output_tokens=60,
            )
            text = (resp.output_text or "").strip()
            obj = json.loads(text)
            st = str((obj or {}).get("semantic_type") or "").strip().lower()
            if st in {"decision", "change", "observation", "evidence", "outcome", "constraint", "lesson", "correction", "context"}:
                return _map_semantic_to_bead_type(st)
        except Exception:
            if allow_fallback:
                return _heuristic_type(user_query, assistant_final)
            raise

    if allow_fallback:
        return _heuristic_type(user_query, assistant_final)
    # strict mode: no key, no fallback
    raise RuntimeError("bead_type_llm_unavailable: OPENAI_API_KEY missing and fallback disabled")
