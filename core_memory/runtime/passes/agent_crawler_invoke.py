from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from core_memory.config.feature_flags import (
    agent_authored_required_enabled,
    agent_crawler_invoke_enabled,
    agent_crawler_max_attempts,
)
from core_memory.policy.turn_memory_authoring import author_turn_memory
from core_memory.runtime.passes.agent_authored_contract import (
    ERROR_AGENT_CALLABLE_MISSING,
    ERROR_AGENT_INVOCATION_EXHAUSTED,
    validate_agent_authored_updates,
)


def invoke_turn_crawler_agent(
    *,
    root: str,
    req: dict[str, Any],
    crawler_context: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Invoke optional turn-time crawler agent callable for reviewed updates.

    Callable contract:
    - configured via CORE_MEMORY_AGENT_CRAWLER_CALLABLE="module.submodule:function"
    - receives payload dict with root/request/crawler_context
    - returns either updates dict OR {"crawler_updates": updates_dict}

    Returns: (updates_or_none, diag)
    """

    md = req.get("metadata") if isinstance(req, dict) else None
    existing = req.get("crawler_updates") if isinstance(req, dict) else None
    if not isinstance(existing, dict):
        existing = (md or {}).get("crawler_updates") if isinstance(md, dict) else None
    if isinstance(existing, dict) and existing:
        return dict(existing), {
            "attempted": False,
            "ok": True,
            "source": str(req.get("_crawler_updates_source") or "crawler_updates"),
            "attempts": 0,
            "error_code": None,
            "authorship": dict(req.get("authorship_provenance") or {}),
        }

    if str(req.get("authoring_mode") or "").strip().lower() == "delegated":
        return author_turn_memory(root=root, req=req, crawler_context=crawler_context)

    callable_path = str(os.environ.get("CORE_MEMORY_AGENT_CRAWLER_CALLABLE") or "").strip()
    should_invoke = bool(agent_authored_required_enabled() or agent_crawler_invoke_enabled() or callable_path)
    if not should_invoke:
        return None, {
            "attempted": False,
            "ok": False,
            "source": "agent_callable",
            "attempts": 0,
            "error_code": None,
            "reason": "invocation_disabled",
        }

    if not callable_path:
        return None, {
            "attempted": True,
            "ok": False,
            "source": "agent_callable",
            "attempts": 0,
            "error_code": ERROR_AGENT_CALLABLE_MISSING,
            "reason": "missing_CORE_MEMORY_AGENT_CRAWLER_CALLABLE",
        }

    max_attempts = int(agent_crawler_max_attempts())
    try:
        fn = _load_callable(callable_path)
    except Exception as exc:  # noqa: BLE001
        return None, {
            "attempted": True,
            "ok": False,
            "source": "agent_callable",
            "attempts": 0,
            "error_code": ERROR_AGENT_CALLABLE_MISSING,
            "reason": "invalid_CORE_MEMORY_AGENT_CRAWLER_CALLABLE",
            "error": str(exc),
            "callable": callable_path,
        }

    last_error = ""
    prior_error: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            payload = {
                "root": root,
                "request": dict(req or {}),
                "crawler_context": dict(crawler_context or {}),
            }
            if prior_error:
                payload["prior_error"] = dict(prior_error)
            result = fn(payload)
            updates = _extract_updates(result)
            if isinstance(updates, dict) and updates:
                ok, code, details = validate_agent_authored_updates(updates)
                if ok:
                    return dict(updates), {
                        "attempted": True,
                        "ok": True,
                        "source": "agent_callable",
                        "attempts": attempt,
                        "error_code": None,
                        "callable": callable_path,
                    }
                prior_error = {
                    "code": str(code or "agent_updates_invalid"),
                    "details": dict(details or {}),
                    "attempt": attempt,
                }
                last_error = f"{prior_error['code']}:{prior_error['details']}"
                continue
            prior_error = {"code": "invalid_response", "details": {}, "attempt": attempt}
            last_error = "invalid_response"
        except Exception as exc:  # noqa: BLE001
            prior_error = {"code": "exception", "details": {"error": str(exc)}, "attempt": attempt}
            last_error = str(exc)

    return None, {
        "attempted": True,
        "ok": False,
        "source": "agent_callable",
        "attempts": max_attempts,
        "error_code": ERROR_AGENT_INVOCATION_EXHAUSTED,
        "error": last_error,
        "callable": callable_path,
    }


def _extract_updates(result: Any) -> dict[str, Any] | None:
    if isinstance(result, dict):
        cu = result.get("crawler_updates")
        if isinstance(cu, dict) and cu:
            return cu
        if result:
            return result
    return None


def _load_callable(path: str) -> Callable[[dict[str, Any]], Any]:
    if ":" not in path:
        raise ValueError("CORE_MEMORY_AGENT_CRAWLER_CALLABLE must be module:function")
    mod_name, fn_name = path.split(":", 1)
    mod = importlib.import_module(mod_name.strip())
    fn = getattr(mod, fn_name.strip(), None)
    if not callable(fn):
        raise ValueError(f"callable not found: {path}")
    return fn
