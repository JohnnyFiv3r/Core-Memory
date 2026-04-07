from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core_memory.integrations.api import IntegrationContext
from core_memory.runtime.engine import process_flush, process_turn_finalized, process_session_start
from core_memory.runtime.jobs import async_jobs_status, enqueue_async_job, run_async_jobs
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.retrieval.query_norm import classify_intent
from core_memory.write_pipeline.continuity_injection import load_continuity_injection

MAX_BODY_BYTES = 256_000
HTTP_TOKEN_ENV = "CORE_MEMORY_HTTP_TOKEN"
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _resolve_tenant(x_tenant_id: Optional[str]) -> Optional[str]:
    """Extract and validate tenant ID from header.

    Security: reject invalid values rather than normalizing potentially unsafe
    path-like input.
    """
    raw = str(x_tenant_id or "").strip()
    if not raw:
        return None
    if not TENANT_ID_PATTERN.fullmatch(raw):
        raise HTTPException(status_code=400, detail="invalid_tenant_id")
    return raw


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


class MemorySearchRequest(BaseModel):
    root: Optional[str] = None
    request: dict[str, Any] = Field(default_factory=dict)
    # compatibility alias
    form_submission: dict[str, Any] = Field(default_factory=dict)
    explain: bool = True


class MemoryExecuteRequest(BaseModel):
    root: Optional[str] = None
    request: dict[str, Any]
    explain: bool = True


class MemoryClassifyIntentRequest(BaseModel):
    query: str


class SessionFlushRequest(BaseModel):
    root: Optional[str] = None
    session_id: str
    source: str = "http"
    flush_tx_id: Optional[str] = None
    promote: bool = True
    token_budget: int = 1200
    max_beads: int = 12


class SessionStartRequest(BaseModel):
    root: Optional[str] = None
    session_id: str
    source: str = "http"
    max_items: int = 80


class MemoryTraceRequest(BaseModel):
    root: Optional[str] = None
    query: str = ""
    anchor_ids: list[str] = Field(default_factory=list)
    k: int = 8
    hydration: dict[str, Any] = Field(default_factory=dict)


class AsyncJobsEnqueueRequest(BaseModel):
    root: Optional[str] = None
    kind: str
    event: dict[str, Any] = Field(default_factory=dict)
    ctx: dict[str, Any] = Field(default_factory=dict)


class AsyncJobsRunRequest(BaseModel):
    root: Optional[str] = None
    run_semantic: bool = True
    max_compaction: int = 1
    max_side_effects: int = 2


app = FastAPI(title="Core Memory SpringAI Bridge Ingress (HTTP-Compatible)", version="1.1")


def _semantic_http_response(result: dict[str, Any]) -> JSONResponse | None:
    code = str(((result.get("error") or {}).get("code") or "")).strip()
    if not result.get("ok") and code == "semantic_backend_unavailable":
        return JSONResponse(status_code=503, content=result)
    return None


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


def _resolve_root(root: Optional[str], tenant_id: Optional[str] = None) -> str:
    base = Path(str(root or "."))
    tenant = _resolve_tenant(tenant_id)
    if not tenant:
        return str(base)
    return str(base / ".tenants" / tenant)


@app.get("/healthz")
async def healthz(root: Optional[str] = None):
    import json as _json
    from pathlib import Path
    from core_memory._version import VERSION

    info: dict[str, Any] = {"ok": True, "version": VERSION}
    resolved = _resolve_root(root)
    index_path = Path(resolved) / ".beads" / "index.json"
    if index_path.exists():
        try:
            idx = _json.loads(index_path.read_text(encoding="utf-8"))
            stats = idx.get("stats") or {}
            info["bead_count"] = stats.get("total_beads", 0)
            info["association_count"] = stats.get("total_associations", 0)
            info["created_at"] = stats.get("created_at")
        except Exception:
            info["index_status"] = "corrupt_or_unreadable"
    else:
        info["index_status"] = "not_initialized"

    # Report semantic backend status (canonical semantic manifest)
    manifest_path = Path(resolved) / ".beads" / "semantic" / "manifest.json"
    legacy_meta_path = Path(resolved) / ".beads" / "bead_index_meta.json"
    if manifest_path.exists():
        try:
            meta = _json.loads(manifest_path.read_text(encoding="utf-8"))
            info["semantic_backend"] = meta.get("backend", "unknown")
            info["embeddings_provider"] = meta.get("provider", "unknown")
        except Exception:
            info["semantic_backend"] = "error"
    elif legacy_meta_path.exists():
        try:
            meta = _json.loads(legacy_meta_path.read_text(encoding="utf-8"))
            info["semantic_backend"] = meta.get("backend", "unknown")
            info["embeddings_provider"] = meta.get("provider", "unknown")
        except Exception:
            info["semantic_backend"] = "error"
    else:
        info["semantic_backend"] = "not_built"

    return info


@app.post("/v1/memory/turn-finalized")
async def turn_finalized(
    req: Request,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
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

    tenant = _resolve_tenant(x_tenant_id)
    ictx = IntegrationContext(
        framework="http",
        source="http_ingress",
        adapter_kind="server",
        adapter_status="active",
        tenant_id=tenant,
    )
    merged_metadata = ictx.to_metadata()
    merged_metadata.update(payload.metadata or {})

    out = process_turn_finalized(
        root=_resolve_root(payload.root, x_tenant_id),
        session_id=payload.session_id,
        turn_id=payload.turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=payload.user_query,
        assistant_final=payload.assistant_final,
        origin=payload.origin,
        tools_trace=list((payload.traces or {}).get("tools") or []),
        mesh_trace=list((payload.traces or {}).get("mesh") or []),
        window_turn_ids=payload.window_turn_ids,
        window_bead_ids=payload.window_bead_ids,
        metadata=merged_metadata,
    )
    event_id = str(((((out.get("emitted") or {}).get("payload") or {}).get("event") or {}).get("event_id") or ""))
    return {
        "accepted": True,
        "ok": bool(out.get("ok", True)),
        "event_id": event_id,
        "processed": int(out.get("processed") or 0),
        "authority_path": str(out.get("authority_path") or "canonical_in_process"),
    }


@app.post("/v1/memory/session-flush")
async def session_flush(
    payload: SessionFlushRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = process_flush(
        root=_resolve_root(payload.root, x_tenant_id),
        session_id=payload.session_id,
        source=payload.source,
        flush_tx_id=payload.flush_tx_id,
        promote=bool(payload.promote),
        token_budget=int(payload.token_budget),
        max_beads=int(payload.max_beads),
    )
    return out


@app.post("/v1/memory/search")
async def memory_search_typed(
    payload: MemorySearchRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    req_payload = dict(payload.request or payload.form_submission or {})
    out = memory_tools.search(
        request=req_payload,
        root=_resolve_root(payload.root, x_tenant_id),
        explain=bool(payload.explain),
    )
    maybe = _semantic_http_response(out if isinstance(out, dict) else {})
    return maybe or out


@app.post("/v1/memory/execute")
async def memory_execute(
    payload: MemoryExecuteRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = memory_tools.execute(
        request=payload.request,
        root=_resolve_root(payload.root, x_tenant_id),
        explain=bool(payload.explain),
    )
    maybe = _semantic_http_response(out if isinstance(out, dict) else {})
    return maybe or out


@app.post("/v1/memory/trace")
async def memory_trace(
    payload: MemoryTraceRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = memory_tools.trace(
        query=str(payload.query or ""),
        root=_resolve_root(payload.root, x_tenant_id),
        k=int(payload.k),
        anchor_ids=list(payload.anchor_ids or []),
        hydration=dict(payload.hydration or {}),
    )
    maybe = _semantic_http_response(out if isinstance(out, dict) else {})
    return maybe or out


@app.post("/v1/memory/classify-intent")
async def memory_classify_intent(
    payload: MemoryClassifyIntentRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return classify_intent(str(payload.query or ""))


@app.get("/v1/memory/continuity")
async def memory_continuity(
    root: Optional[str] = None,
    session_id: Optional[str] = None,
    max_items: int = 80,
    format: str = "json",
    ensure_session_start: bool = True,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    resolved = _resolve_root(root, x_tenant_id)
    result = load_continuity_injection(
        resolved,
        max_items=max(1, int(max_items)),
        session_id=str(session_id or "") or None,
        ensure_session_start=bool(ensure_session_start and session_id),
    )
    fmt = str(format).strip().lower()
    if fmt == "text":
        records = result.get("records") or []
        lines = []
        for r in records:
            typ = r.get("type", "")
            title = r.get("title", "")
            summary = " ".join(r.get("summary") or []) if isinstance(r.get("summary"), list) else str(r.get("summary", ""))
            lines.append(f"[{typ}] {title}: {summary}")
        return {"ok": True, "format": "text", "text": "\n".join(lines), "count": len(records)}
    return {"ok": True, "format": "json", **result}


@app.post("/v1/memory/session-start")
async def memory_session_start(
    payload: SessionStartRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = process_session_start(
        root=_resolve_root(payload.root, x_tenant_id),
        session_id=payload.session_id,
        source=payload.source,
        max_items=max(1, int(payload.max_items)),
    )
    return out


@app.get("/v1/metrics")
async def metrics(
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    from core_memory.runtime.observability import get_metrics
    return get_metrics()


@app.get("/v1/ops/async-jobs/status")
async def ops_async_jobs_status(
    root: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return async_jobs_status(_resolve_root(root, x_tenant_id))


@app.post("/v1/ops/async-jobs/enqueue")
async def ops_async_jobs_enqueue(
    payload: AsyncJobsEnqueueRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = enqueue_async_job(
        root=_resolve_root(payload.root, x_tenant_id),
        kind=str(payload.kind or ""),
        event=dict(payload.event or {}),
        ctx=dict(payload.ctx or {}),
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


@app.post("/v1/ops/async-jobs/run")
async def ops_async_jobs_run(
    payload: AsyncJobsRunRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = run_async_jobs(
        root=_resolve_root(payload.root, x_tenant_id),
        run_semantic=bool(payload.run_semantic),
        max_compaction=max(0, int(payload.max_compaction)),
        max_side_effects=max(0, int(payload.max_side_effects)),
    )
    # Run responses are always structured status payloads; keep 200 for
    # operator observability even when substeps report ok=false.
    return out


def main() -> None:
    """Run HTTP server via `python -m core_memory.integrations.http.server`."""
    import uvicorn

    host = str(os.getenv("CORE_MEMORY_HTTP_HOST") or "127.0.0.1")
    port = int(os.getenv("CORE_MEMORY_HTTP_PORT") or "8000")
    uvicorn.run("core_memory.integrations.http.server:app", host=host, port=port)


if __name__ == "__main__":
    main()
