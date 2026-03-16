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
    """LLM-first bead typing with loose policy gates and deterministic fallback.

    - Uses OpenAI if available (key present and not explicitly disabled)
    - Falls back to lightweight heuristic classifier
    """
    use_llm = str(os.getenv("CORE_MEMORY_BEAD_TYPE_LLM", "1")).strip().lower() in {"1", "true", "yes", "on"}
    key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("CORE_MEMORY_BEAD_TYPE_MODEL", "gpt-4o-mini")

    if use_llm and key:
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
            pass

    return _heuristic_type(user_query, assistant_final)
