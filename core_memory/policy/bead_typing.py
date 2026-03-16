from __future__ import annotations

import json
import os
from typing import Literal

BeadType = Literal["decision", "outcome", "lesson", "context"]


def _heuristic_type(user_query: str, assistant_final: str) -> BeadType:
    text = f"{user_query} {assistant_final}".lower()
    if any(k in text for k in ["decide", "decision", "we chose", "chose", "policy"]):
        return "decision"
    if any(k in text for k in ["outcome", "result", "completed", "done", "shipped"]):
        return "outcome"
    if any(k in text for k in ["lesson", "learned", "insight"]):
        return "lesson"
    return "context"


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
                "Classify this memory turn into one bead type: decision, outcome, lesson, or context. "
                "Use loose policy gates: if uncertain, return context. "
                "Return JSON only: {\"type\":\"...\"}.\n"
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
            t = str((obj or {}).get("type") or "").strip().lower()
            if t in {"decision", "outcome", "lesson", "context"}:
                return t  # type: ignore[return-value]
        except Exception:
            if allow_fallback:
                return _heuristic_type(user_query, assistant_final)
            raise

    if allow_fallback:
        return _heuristic_type(user_query, assistant_final)
    # strict mode: no key, no fallback
    raise RuntimeError("bead_type_llm_unavailable: OPENAI_API_KEY missing and fallback disabled")
