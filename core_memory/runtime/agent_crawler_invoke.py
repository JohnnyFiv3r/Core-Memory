from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from core_memory.integrations.openclaw_flags import (
    agent_authored_required_enabled,
    agent_crawler_invoke_enabled,
    agent_crawler_max_attempts,
)
from .agent_authored_contract import (
    ERROR_AGENT_CALLABLE_MISSING,
    ERROR_AGENT_INVOCATION_EXHAUSTED,
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
    existing = (md or {}).get("crawler_updates") if isinstance(md, dict) else None
    if isinstance(existing, dict) and existing:
        return dict(existing), {
            "attempted": False,
            "ok": True,
            "source": "metadata.crawler_updates",
            "attempts": 0,
            "error_code": None,
        }

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
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn(
                {
                    "root": root,
                    "request": dict(req or {}),
                    "crawler_context": dict(crawler_context or {}),
                }
            )
            updates = _extract_updates(result)
            if isinstance(updates, dict) and updates:
                return dict(updates), {
                    "attempted": True,
                    "ok": True,
                    "source": "agent_callable",
                    "attempts": attempt,
                    "error_code": None,
                    "callable": callable_path,
                }
            last_error = "invalid_response"
        except Exception as exc:  # noqa: BLE001
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
