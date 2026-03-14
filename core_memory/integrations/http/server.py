from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core_memory.integrations.api import emit_turn_finalized
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.retrieval.query_norm import classify_intent

MAX_BODY_BYTES = 256_000
HTTP_TOKEN_ENV = "CORE_MEMORY_HTTP_TOKEN"


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


class MemorySearchFormRequest(BaseModel):
    root: Optional[str] = None


class MemorySearchRequest(BaseModel):
    root: Optional[str] = None
    form_submission: dict[str, Any]
    explain: bool = True


class MemoryReasonRequest(BaseModel):
    root: Optional[str] = None
    query: str
    k: int = 8
    debug: bool = False
    explain: bool = False
    pinned_incident_ids: list[str] = Field(default_factory=list)
    pinned_topic_keys: list[str] = Field(default_factory=list)
    pinned_bead_ids: list[str] = Field(default_factory=list)


class MemoryExecuteRequest(BaseModel):
    root: Optional[str] = None
    request: dict[str, Any]
    explain: bool = True


class MemoryClassifyIntentRequest(BaseModel):
    query: str


app = FastAPI(title="Core Memory SpringAI Bridge Ingress (HTTP-Compatible)", version="1.1")


def _auth_required() -> str:
    return str(os.getenv(HTTP_TOKEN_ENV, "")).strip()


def _check_auth(authorization: Optional[str], x_memory_token: Optional[str]) -> None:
    tok = _auth_required()
    if not tok:
        return
    bearer = ""
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            bearer = parts[1].strip()
    presented = (x_memory_token or bearer or "").strip()
    if presented != tok:
        raise HTTPException(status_code=401, detail="unauthorized")


def _resolve_root(root: Optional[str]) -> str:
    return str(root or "./memory")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/v1/memory/turn-finalized")
async def turn_finalized(
    req: Request,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)

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


@app.get("/v1/memory/search-form")
async def memory_search_form(
    root: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return memory_tools.get_search_form(root=_resolve_root(root))


@app.post("/v1/memory/search")
async def memory_search_typed(
    payload: MemorySearchRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return memory_tools.search(
        form_submission=payload.form_submission,
        root=_resolve_root(payload.root),
        explain=bool(payload.explain),
    )


@app.post("/v1/memory/reason")
async def memory_reason(
    payload: MemoryReasonRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return memory_tools.reason(
        query=payload.query,
        root=_resolve_root(payload.root),
        k=int(payload.k),
        debug=bool(payload.debug),
        explain=bool(payload.explain),
        pinned_incident_ids=payload.pinned_incident_ids,
        pinned_topic_keys=payload.pinned_topic_keys,
        pinned_bead_ids=payload.pinned_bead_ids,
    )


@app.post("/v1/memory/execute")
async def memory_execute(
    payload: MemoryExecuteRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return memory_tools.execute(
        request=payload.request,
        root=_resolve_root(payload.root),
        explain=bool(payload.explain),
    )


@app.post("/v1/memory/classify-intent")
async def memory_classify_intent(
    payload: MemoryClassifyIntentRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return classify_intent(str(payload.query or ""))
