from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

try:
    import pydantic_ai  # noqa: F401 — loads pydantic_ai into sys.modules when installed
except ImportError:
    pass

from core_memory.integrations.api import IntegrationContext, _resolve_root
from core_memory.integrations.openclaw.flags import core_memory_enabled, runtime_flags_snapshot
from core_memory.runtime.engine import process_flush, process_turn_finalized
from core_memory.schema.agent_authored_updates import AgentAuthoredUpdatesV1, AuthoringMode
from core_memory.schema.turn import Turn

logger = logging.getLogger(__name__)

ADAPTER_KIND = "native"
ADAPTER_RUNTIME = "pydanticai"
ADAPTER_STATUS = "production_ready"


def _extract_assistant_final(result: Any) -> str:
    for attr in ("output", "data", "text"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if value is not None:
                return str(value)
    return str(result)


def _build_metadata(metadata: Optional[dict] = None) -> dict:
    ctx = IntegrationContext(framework="pydanticai", source="inproc")
    md = ctx.to_metadata()
    md.update(
        {
            "adapter_kind": ADAPTER_KIND,
            "adapter_runtime": ADAPTER_RUNTIME,
            "adapter_status": ADAPTER_STATUS,
            "fail_open": True,
            "core_memory_flags": runtime_flags_snapshot(),
        }
    )
    md.update(metadata or {})
    return md


def _run_turn_pipeline(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    turns: list[Turn | dict[str, Any]],
    metadata: dict,
    tools_trace: list[dict],
    mesh_trace: list[dict],
    window_turn_ids: list[str],
    window_bead_ids: list[str],
    crawler_updates: AgentAuthoredUpdatesV1 | None,
    authoring_mode: AuthoringMode | None,
) -> dict:
    """Run the full canonical turn pipeline: emit → bead write → association → promotion."""
    return process_turn_finalized(
        root=root,
        session_id=session_id,
        turn_id=turn_id,
        turns=turns,
        metadata=metadata,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        crawler_updates=crawler_updates,
        authoring_mode=authoring_mode,
    )


async def run_with_memory(
    agent: Any,
    user_query: str,
    *,
    root: Optional[str] = None,
    session_id: str,
    turn_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    tools_trace: Optional[list[dict]] = None,
    mesh_trace: Optional[list[dict]] = None,
    window_turn_ids: Optional[list[str]] = None,
    window_bead_ids: Optional[list[str]] = None,
    crawler_updates: AgentAuthoredUpdatesV1 | None = None,
    authoring_mode: AuthoringMode | None = None,
) -> Any:
    root_final = _resolve_root(root)
    turn_id_final = (turn_id or uuid.uuid4().hex[:12]).strip()

    if hasattr(agent, "run"):
        result = await agent.run(user_query)
    else:
        raise AttributeError("agent must provide async run() for run_with_memory")
    assistant_final = _extract_assistant_final(result)

    if not core_memory_enabled():
        return result

    md = _build_metadata(metadata)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: _run_turn_pipeline(
                root=root_final,
                session_id=session_id,
                turn_id=turn_id_final,
                turns=[
                    Turn(speaker="user", role="user", content=user_query),
                    Turn(speaker="assistant", role="assistant", content=assistant_final),
                ],
                metadata=md,
                tools_trace=tools_trace or [],
                mesh_trace=mesh_trace or [],
                window_turn_ids=window_turn_ids or [],
                window_bead_ids=window_bead_ids or [],
                crawler_updates=crawler_updates,
                authoring_mode=authoring_mode,
            ),
        )
    except Exception:
        # Fail-open by contract: runtime result must still return.
        logger.debug("turn pipeline failed; fail-open", exc_info=True)

    return result


async def flush_session_async(
    *,
    root: Optional[str] = None,
    session_id: str,
    promote: bool = True,
    token_budget: int = 3000,
    max_beads: int = 80,
) -> dict:
    """Async version of flush_session. Runs sync I/O in a thread executor."""
    if not core_memory_enabled():
        return {"ok": True, "flushed": False, "reason": "core_memory_disabled"}

    root_final = _resolve_root(root)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: process_flush(
            root=root_final,
            session_id=session_id,
            promote=promote,
            token_budget=token_budget,
            max_beads=max_beads,
            source="pydanticai_adapter_async",
        ),
    )


def run_with_memory_sync(
    agent: Any,
    user_query: str,
    *,
    root: Optional[str] = None,
    session_id: str,
    turn_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    tools_trace: Optional[list[dict]] = None,
    mesh_trace: Optional[list[dict]] = None,
    window_turn_ids: Optional[list[str]] = None,
    window_bead_ids: Optional[list[str]] = None,
    crawler_updates: AgentAuthoredUpdatesV1 | None = None,
    authoring_mode: AuthoringMode | None = None,
) -> Any:
    root_final = _resolve_root(root)
    turn_id_final = (turn_id or uuid.uuid4().hex[:12]).strip()

    if hasattr(agent, "run_sync"):
        result = agent.run_sync(user_query)
    else:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                raise RuntimeError(
                    "run_with_memory_sync cannot be used inside a running event loop without agent.run_sync()"
                )
        except RuntimeError:
            pass
        return asyncio.run(
            run_with_memory(
                agent,
                user_query,
                root=root,
                session_id=session_id,
                turn_id=turn_id,
                metadata=metadata,
                tools_trace=tools_trace,
                mesh_trace=mesh_trace,
                window_turn_ids=window_turn_ids,
                window_bead_ids=window_bead_ids,
                crawler_updates=crawler_updates,
                authoring_mode=authoring_mode,
            )
        )

    assistant_final = _extract_assistant_final(result)

    if not core_memory_enabled():
        return result

    md = _build_metadata(metadata)

    try:
        _run_turn_pipeline(
            root=root_final,
            session_id=session_id,
            turn_id=turn_id_final,
            turns=[
                {"speaker": "user", "role": "user", "content": user_query},
                {"speaker": "assistant", "role": "assistant", "content": assistant_final},
            ],
            metadata=md,
            tools_trace=tools_trace or [],
            mesh_trace=mesh_trace or [],
            window_turn_ids=window_turn_ids or [],
            window_bead_ids=window_bead_ids or [],
            crawler_updates=crawler_updates,
            authoring_mode=authoring_mode,
        )
    except Exception:
        logger.debug("turn pipeline failed; fail-open", exc_info=True)

    return result


def flush_session(
    *,
    root: Optional[str] = None,
    session_id: str,
    promote: bool = True,
    token_budget: int = 3000,
    max_beads: int = 80,
) -> dict:
    """Run the canonical session flush: archive, compress, rebuild rolling window.

    This is the PydanticAI analog of OpenClaw's session_flush hook.
    Call this at app-defined session boundaries (idle timeout, explicit end,
    context budget threshold, process shutdown, etc.).
    """
    if not core_memory_enabled():
        return {"ok": True, "flushed": False, "reason": "core_memory_disabled"}

    root_final = _resolve_root(root)
    return process_flush(
        root=root_final,
        session_id=session_id,
        promote=promote,
        token_budget=token_budget,
        max_beads=max_beads,
        source="pydanticai_adapter",
    )
