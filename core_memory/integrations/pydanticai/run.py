from __future__ import annotations

import uuid
from typing import Any, Optional

from core_memory.integrations.api import IntegrationContext, emit_turn_finalized


def _extract_assistant_final(result: Any) -> str:
    for attr in ("output", "data", "text"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if value is not None:
                return str(value)
    return str(result)


async def run_with_memory(
    agent: Any,
    user_query: str,
    *,
    root: str,
    session_id: str,
    turn_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    window_turn_ids: Optional[list[str]] = None,
    window_bead_ids: Optional[list[str]] = None,
) -> Any:
    turn_id_final = (turn_id or uuid.uuid4().hex[:12]).strip()
    tx_id = f"tx-{turn_id_final}-{uuid.uuid4().hex[:8]}"

    result = await agent.run(user_query)
    assistant_final = _extract_assistant_final(result)

    ctx = IntegrationContext(framework="pydanticai", source="inproc")
    md = ctx.to_metadata()
    md.update(metadata or {})

    emit_turn_finalized(
        root=root,
        session_id=session_id,
        turn_id=turn_id_final,
        transaction_id=tx_id,
        user_query=user_query,
        assistant_final=assistant_final,
        metadata=md,
        tools_trace=[],
        mesh_trace=[],
        window_turn_ids=window_turn_ids or [],
        window_bead_ids=window_bead_ids or [],
    )

    return result
