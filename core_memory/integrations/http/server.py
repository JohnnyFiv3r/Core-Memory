from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from core_memory.integrations.api import emit_turn_finalized

MAX_BODY_BYTES = 256_000


class TurnFinalizedRequest(BaseModel):
    root: Optional[str] = None
    session_id: str
    turn_id: str
    transaction_id: Optional[str] = None
    trace_id: Optional[str] = None
    user_query: str
    assistant_final: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    traces: dict[str, list[dict]] = Field(default_factory=dict)
    window_turn_ids: list[str] = Field(default_factory=list)
    window_bead_ids: list[str] = Field(default_factory=list)
    origin: str = "USER_TURN"


app = FastAPI(title="Core Memory HTTP Ingress", version="1.0")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/v1/memory/turn-finalized")
async def turn_finalized(req: Request):
    content_length = req.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="payload_too_large")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_content_length")

    raw = await req.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload_too_large")

    try:
        payload = TurnFinalizedRequest.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid_payload: {exc}")

    transaction_id = payload.transaction_id or f"tx-{payload.turn_id}-{uuid.uuid4().hex[:8]}"
    trace_id = payload.trace_id or f"tr-{payload.turn_id}-{uuid.uuid4().hex[:8]}"

    # Emit only (no processing) to keep ingress thin and non-blocking for caller semantics.
    event_id = emit_turn_finalized(
        root=payload.root,
        session_id=payload.session_id,
        turn_id=payload.turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=payload.user_query,
        assistant_final=payload.assistant_final,
        origin=payload.origin,
        tools_trace=(payload.traces or {}).get("tools") or [],
        mesh_trace=(payload.traces or {}).get("mesh") or [],
        window_turn_ids=payload.window_turn_ids,
        window_bead_ids=payload.window_bead_ids,
        metadata=payload.metadata,
    )
    return {"accepted": True, "event_id": event_id}
