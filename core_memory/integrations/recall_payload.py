"""Shared recall payload handler for wire surfaces (MCP, HTTP).

Both wire adapters validate the same payload shape and run the same
``recall()`` orchestrator, so HTTP and MCP clients get identical recall
semantics — effort tiers, association-hop expansion, the causal pipeline,
conflict reviews, myelination, fanout, and retrieval-feedback telemetry.

NOTE: this module must not import from ``core_memory`` (the public package
``__init__``) at module level — ``core_memory/__init__.py`` imports
``integrations.api``, so a package-level import here would create a cycle.
It imports ``retrieval`` directly, matching the prior MCP tool.
"""

from __future__ import annotations

from typing import Any

from core_memory.retrieval.agent import recall
from core_memory.retrieval.contracts import validate_recall_effort


def _speaker_arg(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(v).strip() for v in value if str(v).strip()) or None
    text = str(value).strip()
    return text or None


def invalid_request(message: str, *, surface: str, field: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "cm.invalid_request",
            "message": message,
            "data": {"tool": surface, "field": field},
        },
    }


def run_recall_payload(payload: dict[str, Any] | None, *, surface: str = "recall") -> dict[str, Any]:
    """Validate a wire payload, run recall(), return a JSON-safe dict.

    Accepted fields: query (required), effort, intent, k, speaker (str|list),
    as_of, root, include_raw, hints.
    """
    payload = dict(payload or {})
    query = str(payload.get("query") or "").strip()
    if not query:
        return invalid_request("recall.query is required", surface=surface, field="query")
    try:
        effort = validate_recall_effort(str(payload.get("effort") or "medium"))
    except ValueError as exc:
        return invalid_request(str(exc), surface=surface, field="effort")
    if effort == "dynamic":
        return invalid_request("effort='dynamic' is reserved; use low, medium, or high", surface=surface, field="effort")
    kwargs: dict[str, Any] = {}
    if payload.get("intent") is not None:
        kwargs["intent"] = str(payload["intent"]).strip() or None
    if payload.get("k") is not None:
        try:
            kwargs["k"] = int(payload["k"])
        except (TypeError, ValueError):
            return invalid_request("recall.k must be an integer", surface=surface, field="k")
    if str(payload.get("as_of") or "").strip():
        kwargs["as_of"] = str(payload["as_of"]).strip()
    try:
        result = recall(
            query,
            effort=effort,
            speaker=_speaker_arg(payload.get("speaker")),
            root=str(payload.get("root") or "."),
            include_raw=bool(payload.get("include_raw", False)),
            hints=dict(payload.get("hints") or {}),
            **kwargs,
        )
    except ValueError as exc:  # e.g. invalid as_of timestamp
        return invalid_request(str(exc), surface=surface, field="request")
    out = result.to_dict()
    out["ok"] = result.status not in {"failed"}
    if result.status == "empty":
        out.setdefault("warnings", []).append("recall returned no grounded evidence")
    return out
