from __future__ import annotations

import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core_memory.identifiers import validate_archive_id
from core_memory.integrations.api import (
    IntegrationContext,
    inspect_state,
    inspect_bead,
    inspect_bead_hydration,
    inspect_claim_slot,
    list_turn_summaries,
)
from core_memory.runtime.engine import process_flush, process_turn_finalized, process_session_start
from core_memory.runtime.ingest.external_evidence import (
    ingest_document_reference,
    ingest_external_evidence,
    ingest_operational_event,
    ingest_state_assertion,
    ingest_structured_observation,
)
from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate, list_dreamer_candidates
from core_memory.runtime.queue.jobs import async_jobs_status, enqueue_async_job, run_async_jobs
from core_memory.runtime.associations.coverage import (
    apply_association_proposals,
    enqueue_association_coverage,
    get_association_run,
)
from core_memory.management import (
    maintain as maintain_memory,
    remove_beads as remove_memory_beads,
    remove_source as remove_memory_source,
)
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.retrieval.query_norm import classify_intent
from core_memory.write_pipeline.continuity_injection import load_continuity_injection
from core_memory.integrations.mcp.typed_read import (
    query_current_state as mcp_query_current_state,
    query_temporal_window as mcp_query_temporal_window,
    query_causal_chain as mcp_query_causal_chain,
    query_contradictions as mcp_query_contradictions,
)
from core_memory.integrations.mcp.typed_write import (
    write_turn_finalized as mcp_write_turn_finalized,
    apply_reviewed_proposal as mcp_apply_reviewed_proposal,
    submit_entity_merge_proposal as mcp_submit_entity_merge_proposal,
)
from core_memory.integrations.mcp.constants import MCP_HTTP_PATH
from core_memory.integrations.mcp.protocol_server import build_mcp_app

MAX_BODY_BYTES = 256_000
HTTP_TOKEN_ENV = "CORE_MEMORY_HTTP_TOKEN"
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(host: str) -> bool:
    return host.strip().lower() in _LOOPBACK_HOSTS


def _is_hosted_mode() -> bool:
    """True when bound to a non-loopback interface (server-authority / hosted mode)."""
    return not _is_loopback(str(os.getenv("CORE_MEMORY_HTTP_HOST") or "127.0.0.1"))


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
    turns: list[dict[str, Any]] = Field(default_factory=list)
    user_query: Optional[str] = None
    assistant_final: Optional[str] = None
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


class ExternalEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    root: Optional[str] = None
    session_id: str = "external"
    payload: dict[str, Any] = Field(default_factory=dict)

    def evidence_payload(self) -> dict[str, Any]:
        data = dict(getattr(self, "model_extra", None) or {})
        data.update(dict(self.payload or {}))
        data.setdefault("session_id", self.session_id)
        return data


class ConfirmBeadRequest(BaseModel):
    root: Optional[str] = None
    bead_id: str
    note: str = ""


class SoulProposeRequest(BaseModel):
    root: Optional[str] = None
    subject: str = "self"
    target_file: str
    entry_key: str
    content: str = ""
    op: str = "upsert"
    source: str = "agent"
    epistemic_status: str = "inferred"
    reason: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    requires_approval: bool = True


class SoulApproveRequest(BaseModel):
    root: Optional[str] = None
    subject: str = "self"
    revision_id: str
    approver: str = ""
    note: str = ""


class SoulRejectRequest(BaseModel):
    root: Optional[str] = None
    subject: str = "self"
    revision_id: str
    reviewer: str = ""
    reason: str = ""


class SoulIntegrityRequest(BaseModel):
    root: Optional[str] = None
    subject: str = "self"


class SoulIntegrityRepairRequest(BaseModel):
    root: Optional[str] = None
    subject: str = "self"
    apply: bool = True


class ApproveBeadRequest(BaseModel):
    root: Optional[str] = None
    bead_id: str
    approver: str = ""
    note: str = ""


class RejectBeadRequest(BaseModel):
    root: Optional[str] = None
    bead_id: str
    approver: str = ""
    reason: str = ""


class RequestApprovalRequest(BaseModel):
    root: Optional[str] = None
    bead_id: str
    requested_by: str = ""
    note: str = ""


class RemoveBeadsRequest(BaseModel):
    root: Optional[str] = None
    bead_ids: list[str] = Field(default_factory=list)
    bead_id: str = ""
    reason: str
    actor: str = ""
    authority: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True
    apply: bool = False
    idempotency_key: str = ""


class RemoveSourceRequest(BaseModel):
    root: Optional[str] = None
    source: dict[str, Any] = Field(default_factory=dict)
    reason: str = "source removed"
    actor: str = ""
    authority: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True
    apply: bool = False
    idempotency_key: str = ""
    limit: int = 1000


class MaintainRequest(BaseModel):
    root: Optional[str] = None
    action: str
    scope: dict[str, Any] = Field(default_factory=dict)
    targets: dict[str, Any] = Field(default_factory=dict)
    proposal: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)
    authority: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True
    apply: bool = False
    idempotency_key: str = ""


class MemoryTraceRequest(BaseModel):
    root: Optional[str] = None
    query: str = ""
    anchor_ids: list[str] = Field(default_factory=list)
    k: int = 8
    hydration: dict[str, Any] = Field(default_factory=dict)


class MemoryRecallRequest(BaseModel):
    root: Optional[str] = None
    query: str
    effort: str = "medium"
    intent: Optional[str] = None
    k: Optional[int] = None
    speaker: Optional[Any] = None  # str or list[str]
    as_of: Optional[str] = None
    include_raw: bool = False
    hints: dict[str, Any] = Field(default_factory=dict)


class AssociationRunRequest(BaseModel):
    root: Optional[str] = None
    bead_ids: list[str] = Field(default_factory=list)
    session_id: Optional[str] = None
    trigger: str = "operator"
    candidate_bead_ids: list[str] = Field(default_factory=list)
    run_inline: bool = False
    max_candidates: int = 40
    graph_revision: str = ""
    prompt_version: str = "association_judge.v1"
    rubric_version: str = "association_truth.v1"


class AssociationProposalRequest(BaseModel):
    root: Optional[str] = None
    run_id: str = ""
    session_id: Optional[str] = None
    associations: list[dict[str, Any]] = Field(default_factory=list)


class MCPQueryCurrentStateRequest(BaseModel):
    root: Optional[str] = None
    subject: str = "user"
    slot: str = ""
    slot_key: str = ""
    as_of: str = ""
    k: int = 8
    query: str = ""
    include_history: bool = False


class MCPQueryTemporalWindowRequest(BaseModel):
    root: Optional[str] = None
    query: str
    window_start: str = ""
    window_end: str = ""
    intent: str = "remember"
    k: int = 10


class MCPQueryCausalChainRequest(BaseModel):
    root: Optional[str] = None
    query: str
    anchor_ids: list[str] = Field(default_factory=list)
    k: int = 8
    hydration: dict[str, Any] = Field(default_factory=dict)


class MCPQueryContradictionsRequest(BaseModel):
    root: Optional[str] = None
    subject: str = ""
    slot: str = ""
    slot_key: str = ""
    as_of: str = ""
    query: str = ""
    k: int = 10


class MCPWriteTurnFinalizedRequest(BaseModel):
    root: Optional[str] = None
    session_id: str
    turn_id: str
    turns: list[dict[str, Any]] = Field(default_factory=list)
    user_query: Optional[str] = None
    assistant_final: Optional[str] = None
    transaction_id: str = ""
    trace_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools_trace: list[dict[str, Any]] = Field(default_factory=list)
    mesh_trace: list[dict[str, Any]] = Field(default_factory=list)
    window_turn_ids: list[str] = Field(default_factory=list)
    window_bead_ids: list[str] = Field(default_factory=list)
    origin: str = "USER_TURN"


class MCPApplyReviewedProposalRequest(BaseModel):
    root: Optional[str] = None
    candidate_id: str
    decision: str
    reviewer: str = ""
    notes: str = ""
    apply: bool = True
    resolution: str = ""
    context_a: str = ""
    context_b: str = ""


class MCPSubmitEntityMergeProposalRequest(BaseModel):
    root: Optional[str] = None
    source_entity_id: str
    target_entity_id: str
    source_bead_id: str = ""
    target_bead_id: str = ""
    confidence: float = 0.9
    reviewer: str = ""
    rationale: str = ""
    notes: str = ""
    run_metadata: dict[str, Any] = Field(default_factory=dict)


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


class DreamerCandidateDecideRequest(BaseModel):
    root: Optional[str] = None
    candidate_id: str
    decision: str
    reviewer: str = ""
    notes: str = ""
    apply: bool = False


def _build_mcp_subapp() -> FastAPI:
    try:
        hosted = _is_hosted_mode()
        server_root = str(os.getenv("CORE_MEMORY_ROOT") or ".") if hosted else None
        return build_mcp_app(root=server_root, lock_root=hosted)
    except RuntimeError as exc:
        error_message = str(exc)
        unavailable = FastAPI(title="Core Memory MCP Protocol Server (unavailable)")

        @unavailable.get("/healthz")
        async def mcp_unavailable_healthz():
            return {"ok": False, "surface": "mcp", "error": error_message}

        return unavailable


_mcp_app = _build_mcp_subapp()


class _MCPAuthMiddleware:
    """Pure ASGI auth gate for the mounted MCP sub-app.

    BaseHTTPMiddleware breaks SSE streaming, so this wraps the sub-app at the
    ASGI level. Token validation mirrors _check_auth() for REST endpoints.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            tok = _auth_required()
            if tok:
                headers = {k.lower(): v for k, v in scope.get("headers", [])}
                auth = headers.get(b"authorization", b"").decode()
                x_token = headers.get(b"x-memory-token", b"").decode()
                bearer = ""
                if auth:
                    parts = auth.split(" ", 1)
                    if len(parts) == 2 and parts[0].lower() == "bearer":
                        bearer = parts[1].strip()
                presented = (x_token or bearer or "").strip()
                if presented != tok:
                    if scope["type"] == "http":
                        await send({"type": "http.response.start", "status": 401,
                                    "headers": [[b"content-type", b"application/json"]]})
                        await send({"type": "http.response.body",
                                    "body": b'{"detail":"unauthorized"}', "more_body": False})
                    return
        await self._app(scope, receive, send)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    session_manager = getattr(getattr(_mcp_app, "state", None), "mcp_session_manager", None)
    if session_manager is None:
        yield
    else:
        async with session_manager.run():
            yield


app = FastAPI(title="Core Memory SpringAI Bridge Ingress (HTTP-Compatible)", version="1.1", lifespan=_lifespan)
app.mount(MCP_HTTP_PATH, _MCPAuthMiddleware(_mcp_app))


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
    if _is_hosted_mode():
        # Hosted mode: CORE_MEMORY_ROOT is the server authority.
        # Caller-supplied root is denied unless it matches the server root
        # or CORE_MEMORY_HTTP_ALLOW_ARBITRARY_ROOT=1 is set (dev escape hatch).
        server_root = str(os.getenv("CORE_MEMORY_ROOT") or ".")
        effective_root = server_root
        if root and root != server_root:
            allow = str(os.getenv("CORE_MEMORY_HTTP_ALLOW_ARBITRARY_ROOT", "")).lower() in {"1", "true", "yes"}
            if not allow:
                raise HTTPException(status_code=403, detail="arbitrary_root_denied_in_hosted_mode")
            effective_root = root
        base = Path(effective_root)
    else:
        base = Path(str(root or os.getenv("CORE_MEMORY_ROOT") or "."))
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

    if payload.user_query is not None or payload.assistant_final is not None:
        return JSONResponse(status_code=400, content={"ok": False, "error": "legacy_turn_fields_removed", "message": "user_query/assistant_final were removed; pass turns=[{speaker, role, content}] instead. See docs/concepts/turn_schema.md."})
    try:
        session_id = validate_archive_id(payload.session_id, field="session_id")
        turn_id = validate_archive_id(payload.turn_id, field="turn_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    transaction_id = payload.transaction_id or f"tx-{turn_id}-{uuid.uuid4().hex[:8]}"
    trace_id = payload.trace_id or f"tr-{turn_id}-{uuid.uuid4().hex[:8]}"

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
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        turns=list(payload.turns or []),
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


@app.post("/v1/memory/external-evidence")
async def memory_external_evidence(
    payload: ExternalEvidenceRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    try:
        return ingest_external_evidence(
            root=_resolve_root(payload.root, x_tenant_id),
            payload=payload.evidence_payload(),
            session_id=payload.session_id,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "contract": "memory.external_evidence.v1"})


@app.post("/v1/memory/confirm")
async def memory_confirm(
    payload: ConfirmBeadRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """User-confirmation surface: authority=user_confirmed, confidence class A."""
    _check_auth(authorization, x_memory_token)
    from core_memory import confirm_bead

    out = confirm_bead(
        root=_resolve_root(payload.root, x_tenant_id),
        bead_id=payload.bead_id,
        note=payload.note,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=404, content={**out, "contract": "memory.confirm.v1"})
    return out


@app.post("/v1/memory/request-approval")
async def memory_request_approval(
    payload: RequestApprovalRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Flag a bead as awaiting human review (approval_status=pending)."""
    _check_auth(authorization, x_memory_token)
    from core_memory import request_approval

    out = request_approval(
        root=_resolve_root(payload.root, x_tenant_id),
        bead_id=payload.bead_id,
        requested_by=payload.requested_by,
        note=payload.note,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=404, content={**out, "contract": "memory.request_approval.v1"})
    return out


@app.post("/v1/memory/approve")
async def memory_approve(
    payload: ApproveBeadRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Approve a bead under review: grants confidence class A, records approver."""
    _check_auth(authorization, x_memory_token)
    from core_memory import approve_bead

    out = approve_bead(
        root=_resolve_root(payload.root, x_tenant_id),
        bead_id=payload.bead_id,
        approver=payload.approver,
        note=payload.note,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=404, content={**out, "contract": "memory.approve.v1"})
    return out


@app.post("/v1/memory/reject")
async def memory_reject(
    payload: RejectBeadRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Reject a bead under review: excluded from retrieval, retained for audit."""
    _check_auth(authorization, x_memory_token)
    from core_memory import reject_bead

    out = reject_bead(
        root=_resolve_root(payload.root, x_tenant_id),
        bead_id=payload.bead_id,
        approver=payload.approver,
        reason=payload.reason,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=404, content={**out, "contract": "memory.reject.v1"})
    return out


@app.post("/v1/memory/beads/remove")
async def memory_remove_beads(
    payload: RemoveBeadsRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Remove beads from active memory projection, preserving audit tombstones."""
    _check_auth(authorization, x_memory_token)
    bead_ids = list(payload.bead_ids or [])
    if payload.bead_id:
        bead_ids.append(payload.bead_id)
    out = remove_memory_beads(
        root=_resolve_root(payload.root, x_tenant_id),
        bead_ids=bead_ids,
        reason=payload.reason,
        actor=payload.actor,
        authority=dict(payload.authority or {}),
        dry_run=bool(payload.dry_run),
        apply=bool(payload.apply),
        idempotency_key=payload.idempotency_key,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


@app.post("/v1/memory/sources/remove")
async def memory_remove_source(
    payload: RemoveSourceRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Remove all active beads matching a strong source identifier."""
    _check_auth(authorization, x_memory_token)
    out = remove_memory_source(
        root=_resolve_root(payload.root, x_tenant_id),
        source=dict(payload.source or {}),
        reason=payload.reason,
        actor=payload.actor,
        authority=dict(payload.authority or {}),
        dry_run=bool(payload.dry_run),
        apply=bool(payload.apply),
        idempotency_key=payload.idempotency_key,
        limit=max(1, int(payload.limit)),
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


@app.post("/v1/memory/maintain")
async def memory_maintain(
    payload: MaintainRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Unified agent control-plane facade for governed memory maintenance."""
    _check_auth(authorization, x_memory_token)
    out = maintain_memory(
        root=_resolve_root(payload.root, x_tenant_id),
        action=payload.action,
        scope=dict(payload.scope or {}),
        targets=dict(payload.targets or {}),
        proposal=dict(payload.proposal or {}),
        decision=dict(payload.decision or {}),
        authority=dict(payload.authority or {}),
        dry_run=bool(payload.dry_run),
        apply=bool(payload.apply),
        idempotency_key=payload.idempotency_key,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


@app.get("/v1/memory/pending-approvals")
async def memory_pending_approvals(
    root: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """List beads awaiting human review (approval_status=pending)."""
    _check_auth(authorization, x_memory_token)
    from core_memory import list_pending_approvals

    return list_pending_approvals(root=_resolve_root(root, x_tenant_id), limit=int(limit))


# ── SOUL: agent-authored self-model surface (PRD §13) ──────────────────


@app.get("/v1/soul/files")
async def soul_files(
    root: Optional[str] = None,
    subject: str = "self",
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """List SOUL files and their entry counts for a subject."""
    _check_auth(authorization, x_memory_token)
    from core_memory import list_soul_files

    return list_soul_files(_resolve_root(root, x_tenant_id), subject=subject)


@app.get("/v1/soul/files/{file_name}")
async def soul_file(
    file_name: str,
    root: Optional[str] = None,
    subject: str = "self",
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Return the rendered markdown for one SOUL file."""
    _check_auth(authorization, x_memory_token)
    from core_memory import read_soul_file

    out = read_soul_file(_resolve_root(root, x_tenant_id), file_name=file_name, subject=subject)
    if not out.get("ok"):
        return JSONResponse(status_code=400, content={**out, "contract": "soul.read.v1"})
    return out


@app.get("/v1/soul/history")
async def soul_history_endpoint(
    root: Optional[str] = None,
    subject: str = "self",
    limit: int = 500,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Return the SOUL revision history for a subject."""
    _check_auth(authorization, x_memory_token)
    from core_memory import soul_history

    return soul_history(_resolve_root(root, x_tenant_id), subject=subject, limit=int(limit))


@app.get("/v1/dreamer/geometry")
async def dreamer_geometry(
    root: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Serve the continuity-geometry manifest (read-only projection, §16.1).

    Served from the manifest built on the Dreamer cadence — never recomputed on
    read. When no manifest exists yet, returns ``present=false``; trigger a
    dreamer-run to build it.
    """
    _check_auth(authorization, x_memory_token)
    from core_memory.runtime.dreamer.geometry import read_geometry_manifest

    return read_geometry_manifest(_resolve_root(root, x_tenant_id))


@app.post("/v1/soul/propose-update")
async def soul_propose_update(
    payload: SoulProposeRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Propose a SOUL revision (applied immediately when requires_approval=False)."""
    _check_auth(authorization, x_memory_token)
    from core_memory import propose_soul_update

    out = propose_soul_update(
        _resolve_root(payload.root, x_tenant_id),
        target_file=payload.target_file,
        entry_key=payload.entry_key,
        content=payload.content,
        op=payload.op,
        subject=payload.subject,
        source=payload.source,
        epistemic_status=payload.epistemic_status,
        reason=payload.reason,
        evidence=payload.evidence,
        requires_approval=payload.requires_approval,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content={**out, "contract": "soul.propose.v1"})
    return out


@app.post("/v1/soul/approve-update")
async def soul_approve_update(
    payload: SoulApproveRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Approve a proposed SOUL revision (approval applies it)."""
    _check_auth(authorization, x_memory_token)
    from core_memory import approve_soul_update

    out = approve_soul_update(
        _resolve_root(payload.root, x_tenant_id),
        revision_id=payload.revision_id,
        subject=payload.subject,
        approver=payload.approver,
        note=payload.note,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content={**out, "contract": "soul.approve.v1"})
    return out


@app.post("/v1/soul/reject-update")
async def soul_reject_update(
    payload: SoulRejectRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Reject a proposed SOUL revision (it never folds into the projection)."""
    _check_auth(authorization, x_memory_token)
    from core_memory import reject_soul_update

    out = reject_soul_update(
        _resolve_root(payload.root, x_tenant_id),
        revision_id=payload.revision_id,
        subject=payload.subject,
        reviewer=payload.reviewer,
        reason=payload.reason,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content={**out, "contract": "soul.reject.v1"})
    return out


@app.post("/v1/soul/integrity/check")
async def soul_integrity_check_endpoint(
    payload: SoulIntegrityRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Check a subject's SOUL files for structural issues (read-only)."""
    _check_auth(authorization, x_memory_token)
    from core_memory import soul_integrity_check

    return soul_integrity_check(_resolve_root(payload.root, x_tenant_id), subject=payload.subject)


@app.post("/v1/soul/integrity/repair")
async def soul_integrity_repair_endpoint(
    payload: SoulIntegrityRepairRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Apply auto-safe structural repairs (or dry-run with apply=false)."""
    _check_auth(authorization, x_memory_token)
    from core_memory import soul_integrity_repair

    return soul_integrity_repair(
        _resolve_root(payload.root, x_tenant_id),
        subject=payload.subject,
        apply=payload.apply,
    )


@app.post("/v1/ingest/github")
async def ingest_github_webhook(
    request: Request,
    root: Optional[str] = None,
    x_github_event: Optional[str] = Header(default=None),
    x_github_delivery: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """GitHub webhook receiver — example system-of-record connector.

    Only bead-worthy events (merged PRs, closed issues, published releases,
    default-branch doc changes) write beads; everything else returns a
    skip receipt.
    """
    _check_auth(authorization, x_memory_token)
    from core_memory.integrations.github import ingest_github_event

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_json_body", "contract": "ingest.github.v1"})
    try:
        return ingest_github_event(
            root=_resolve_root(root, x_tenant_id),
            event_name=str(x_github_event or ""),
            event=body if isinstance(body, dict) else {},
            delivery_id=str(x_github_delivery or ""),
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "contract": "ingest.github.v1"})


@app.post("/v1/memory/structured-observation")
async def memory_structured_observation(
    payload: ExternalEvidenceRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    try:
        return ingest_structured_observation(
            root=_resolve_root(payload.root, x_tenant_id),
            payload=payload.evidence_payload(),
            session_id=payload.session_id,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "contract": "memory.structured_observation.v1"})


@app.post("/v1/memory/document-reference")
async def memory_document_reference(
    payload: ExternalEvidenceRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    try:
        return ingest_document_reference(
            root=_resolve_root(payload.root, x_tenant_id),
            payload=payload.evidence_payload(),
            session_id=payload.session_id,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "contract": "memory.document_reference.v1"})


@app.post("/v1/memory/state-assertion")
async def memory_state_assertion(
    payload: ExternalEvidenceRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    try:
        return ingest_state_assertion(
            root=_resolve_root(payload.root, x_tenant_id),
            payload=payload.evidence_payload(),
            session_id=payload.session_id,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "contract": "memory.state_assertion.v1"})


@app.post("/v1/memory/operational-event")
async def memory_operational_event(
    payload: ExternalEvidenceRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Operational event systems anchor (state transitions of the business)."""
    _check_auth(authorization, x_memory_token)
    try:
        return ingest_operational_event(
            root=_resolve_root(payload.root, x_tenant_id),
            payload=payload.evidence_payload(),
            session_id=payload.session_id,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "contract": "memory.operational_event.v1"})


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


@app.post("/v1/memory/recall")
async def memory_recall(
    payload: MemoryRecallRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Full recall orchestrator over HTTP — parity with the MCP recall tool.

    Unlike /v1/memory/{search,execute,trace}, this runs the complete recall()
    pipeline: effort tiers, association-hop expansion, the causal pipeline,
    conflict reviews, myelination, fanout, and retrieval-feedback telemetry.
    """
    _check_auth(authorization, x_memory_token)
    from core_memory.integrations.recall_payload import run_recall_payload
    body = payload.model_dump()
    body["root"] = _resolve_root(payload.root, x_tenant_id)
    out = run_recall_payload(body, surface="http.recall")
    if not out.get("ok") and str(((out.get("error") or {}).get("code") or "")) == "cm.invalid_request":
        return JSONResponse(status_code=400, content=out)
    maybe = _semantic_http_response(out)
    return maybe or out


@app.post("/v1/memory/association-runs")
async def memory_association_runs(
    payload: AssociationRunRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = enqueue_association_coverage(
        root=_resolve_root(payload.root, x_tenant_id),
        bead_ids=list(payload.bead_ids or []),
        session_id=(str(payload.session_id or "").strip() or None),
        trigger=str(payload.trigger or "operator"),
        candidate_bead_ids=list(payload.candidate_bead_ids or []),
        run_inline=bool(payload.run_inline),
        max_candidates=max(1, int(payload.max_candidates)),
        graph_revision=str(payload.graph_revision or ""),
        prompt_version=str(payload.prompt_version or "association_judge.v1"),
        rubric_version=str(payload.rubric_version or "association_truth.v1"),
    )
    if not out.get("ok") and str(out.get("status") or "") not in {"judge_failed", "quarantined"}:
        return JSONResponse(status_code=400, content=out)
    return out


@app.get("/v1/memory/association-runs/{run_id}")
async def memory_association_run_status(
    run_id: str,
    root: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = get_association_run(root=_resolve_root(root, x_tenant_id), run_id=str(run_id or ""))
    if not out.get("ok"):
        return JSONResponse(status_code=404, content=out)
    return out


@app.post("/v1/memory/association-proposals")
async def memory_association_proposals(
    payload: AssociationProposalRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = apply_association_proposals(
        root=_resolve_root(payload.root, x_tenant_id),
        associations=list(payload.associations or []),
        run_id=str(payload.run_id or ""),
        session_id=(str(payload.session_id or "").strip() or None),
    )
    return out


@app.post("/v1/memory/classify-intent")
async def memory_classify_intent(
    payload: MemoryClassifyIntentRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    return classify_intent(str(payload.query or ""))


@app.post("/v1/mcp/query-current-state")
async def mcp_query_current_state_endpoint(
    payload: MCPQueryCurrentStateRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = mcp_query_current_state(
        root=_resolve_root(payload.root, x_tenant_id),
        subject=str(payload.subject or "user"),
        slot=str(payload.slot or ""),
        slot_key=str(payload.slot_key or ""),
        as_of=str(payload.as_of or ""),
        k=max(1, int(payload.k)),
        query=str(payload.query or ""),
        include_history=bool(payload.include_history),
    )
    return out


@app.post("/v1/mcp/query-temporal-window")
async def mcp_query_temporal_window_endpoint(
    payload: MCPQueryTemporalWindowRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = mcp_query_temporal_window(
        root=_resolve_root(payload.root, x_tenant_id),
        query=str(payload.query or ""),
        window_start=str(payload.window_start or ""),
        window_end=str(payload.window_end or ""),
        intent=str(payload.intent or "remember"),
        k=max(1, int(payload.k)),
    )
    return out


@app.post("/v1/mcp/query-causal-chain")
async def mcp_query_causal_chain_endpoint(
    payload: MCPQueryCausalChainRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = mcp_query_causal_chain(
        root=_resolve_root(payload.root, x_tenant_id),
        query=str(payload.query or ""),
        anchor_ids=list(payload.anchor_ids or []),
        k=max(1, int(payload.k)),
        hydration=dict(payload.hydration or {}),
    )
    return out


@app.post("/v1/mcp/query-contradictions")
async def mcp_query_contradictions_endpoint(
    payload: MCPQueryContradictionsRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = mcp_query_contradictions(
        root=_resolve_root(payload.root, x_tenant_id),
        subject=str(payload.subject or ""),
        slot=str(payload.slot or ""),
        slot_key=str(payload.slot_key or ""),
        as_of=str(payload.as_of or ""),
        query=str(payload.query or ""),
        k=max(1, int(payload.k)),
    )
    return out


@app.post("/v1/mcp/write-turn-finalized")
async def mcp_write_turn_finalized_endpoint(
    payload: MCPWriteTurnFinalizedRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    if payload.user_query is not None or payload.assistant_final is not None:
        return JSONResponse(status_code=400, content={"ok": False, "error": "legacy_turn_fields_removed", "message": "user_query/assistant_final were removed; pass turns=[{speaker, role, content}] instead. See docs/concepts/turn_schema.md."})
    try:
        session_id = validate_archive_id(payload.session_id, field="session_id")
        turn_id = validate_archive_id(payload.turn_id, field="turn_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = mcp_write_turn_finalized(
        root=_resolve_root(payload.root, x_tenant_id),
        session_id=session_id,
        turn_id=turn_id,
        turns=list(payload.turns or []),
        transaction_id=str(payload.transaction_id or ""),
        trace_id=str(payload.trace_id or ""),
        metadata=dict(payload.metadata or {}),
        tools_trace=list(payload.tools_trace or []),
        mesh_trace=list(payload.mesh_trace or []),
        window_turn_ids=list(payload.window_turn_ids or []),
        window_bead_ids=list(payload.window_bead_ids or []),
        origin=str(payload.origin or "USER_TURN"),
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


@app.post("/v1/mcp/apply-reviewed-proposal")
async def mcp_apply_reviewed_proposal_endpoint(
    payload: MCPApplyReviewedProposalRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = mcp_apply_reviewed_proposal(
        root=_resolve_root(payload.root, x_tenant_id),
        candidate_id=str(payload.candidate_id or ""),
        decision=str(payload.decision or ""),
        reviewer=str(payload.reviewer or ""),
        notes=str(payload.notes or ""),
        apply=bool(payload.apply),
        resolution=str(payload.resolution or ""),
        context_a=str(payload.context_a),
        context_b=str(payload.context_b),
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


@app.post("/v1/mcp/submit-entity-merge-proposal")
async def mcp_submit_entity_merge_proposal_endpoint(
    payload: MCPSubmitEntityMergeProposalRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = mcp_submit_entity_merge_proposal(
        root=_resolve_root(payload.root, x_tenant_id),
        source_entity_id=str(payload.source_entity_id or ""),
        target_entity_id=str(payload.target_entity_id or ""),
        source_bead_id=str(payload.source_bead_id or ""),
        target_bead_id=str(payload.target_bead_id or ""),
        confidence=float(payload.confidence or 0.0),
        reviewer=str(payload.reviewer or ""),
        rationale=str(payload.rationale or ""),
        notes=str(payload.notes or ""),
        run_metadata=dict(payload.run_metadata or {}),
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


@app.get("/v1/memory/projection/worldlines")
async def memory_projection_worldlines(
    root: Optional[str] = None,
    kinds: Optional[str] = None,
    min_length: int = 1,
    include_membership: bool = False,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Derived worldline projection (claim chains, entity threads, goal threads).

    Read-side perspective over the canonical index — nothing is stored.
    ``kinds`` is a comma-separated subset of claim,entity,goal.
    """
    _check_auth(authorization, x_memory_token)
    from core_memory.graph.worldlines import derive_worldlines, worldline_membership
    resolved = _resolve_root(root, x_tenant_id)
    kind_list = [k.strip() for k in str(kinds or "").split(",") if k.strip()] or None
    out = derive_worldlines(resolved, kinds=kind_list, min_length=int(min_length))
    if include_membership:
        out["membership"] = worldline_membership(resolved, kinds=kind_list)
    return out


@app.get("/v1/memory/projection/storylines")
async def memory_projection_storylines(
    root: Optional[str] = None,
    kinds: Optional[str] = None,
    min_length: int = 1,
    include_superseded: bool = False,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    """Storylines: worldline backbones + interpretive overlays + tensions.

    backbone = grounded causal history (derived, evidence-backed);
    overlays = accepted dreamer interpretations (versioned, falsifiable);
    tensions = computed (competing overlays, claim-slot conflicts).
    The overlay layer is never an input to backbone derivation.
    """
    _check_auth(authorization, x_memory_token)
    from core_memory.graph.storylines import derive_storylines
    resolved = _resolve_root(root, x_tenant_id)
    kind_list = [k.strip() for k in str(kinds or "").split(",") if k.strip()] or None
    return derive_storylines(
        resolved,
        kinds=kind_list,
        min_length=int(min_length),
        include_superseded=bool(include_superseded),
    )


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


@app.get("/v1/memory/inspect/state")
async def memory_inspect_state(
    root: Optional[str] = None,
    session_id: Optional[str] = None,
    as_of: Optional[str] = None,
    limit_beads: int = 200,
    limit_associations: int = 200,
    limit_flushes: int = 20,
    limit_merge_proposals: int = 40,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = inspect_state(
        root=_resolve_root(root, x_tenant_id),
        session_id=(str(session_id or "").strip() or None),
        as_of=(str(as_of or "").strip() or None),
        limit_beads=max(1, int(limit_beads)),
        limit_associations=max(1, int(limit_associations)),
        limit_flushes=max(1, int(limit_flushes)),
        limit_merge_proposals=max(1, int(limit_merge_proposals)),
    )
    return out


@app.get("/v1/memory/inspect/beads/{bead_id}")
async def memory_inspect_bead(
    bead_id: str,
    root: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = inspect_bead(root=_resolve_root(root, x_tenant_id), bead_id=str(bead_id or ""))
    if out is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "bead_not_found", "bead_id": str(bead_id)})
    return {"ok": True, "bead": out}


@app.get("/v1/memory/inspect/beads/{bead_id}/hydrate")
async def memory_inspect_bead_hydrate(
    bead_id: str,
    root: Optional[str] = None,
    include_tools: bool = False,
    before: int = 0,
    after: int = 0,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = inspect_bead_hydration(
        root=_resolve_root(root, x_tenant_id),
        bead_id=str(bead_id or ""),
        include_tools=bool(include_tools),
        before=max(0, int(before)),
        after=max(0, int(after)),
    )
    return out


@app.get("/v1/memory/inspect/claim-slots/{subject}/{slot}")
async def memory_inspect_claim_slot(
    subject: str,
    slot: str,
    root: Optional[str] = None,
    as_of: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = inspect_claim_slot(
        root=_resolve_root(root, x_tenant_id),
        subject=str(subject or ""),
        slot=str(slot or ""),
        as_of=(str(as_of or "").strip() or None),
    )
    return out


@app.get("/v1/memory/inspect/turns")
async def memory_inspect_turns(
    root: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 200,
    cursor: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = list_turn_summaries(
        root=_resolve_root(root, x_tenant_id),
        session_id=(str(session_id or "").strip() or None),
        limit=max(1, int(limit)),
        cursor=(str(cursor or "").strip() or None),
    )
    return out


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
    from core_memory.runtime.observability.observability import get_metrics
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


@app.get("/v1/ops/dreamer/candidates")
async def ops_dreamer_candidates(
    root: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = list_dreamer_candidates(
        root=_resolve_root(root, x_tenant_id),
        status=status,
        limit=max(1, int(limit)),
    )
    return out


@app.post("/v1/ops/dreamer/candidates/decide")
async def ops_dreamer_candidates_decide(
    payload: DreamerCandidateDecideRequest,
    authorization: Optional[str] = Header(default=None),
    x_memory_token: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None),
):
    _check_auth(authorization, x_memory_token)
    out = decide_dreamer_candidate(
        root=_resolve_root(payload.root, x_tenant_id),
        candidate_id=str(payload.candidate_id or ""),
        decision=str(payload.decision or ""),
        reviewer=str(payload.reviewer or ""),
        notes=str(payload.notes or ""),
        apply=bool(payload.apply),
    )
    if not out.get("ok"):
        return JSONResponse(status_code=400, content=out)
    return out


def main() -> None:
    """Run HTTP server via `python -m core_memory.integrations.http.server`."""
    import uvicorn

    host = str(os.getenv("CORE_MEMORY_HTTP_HOST") or "127.0.0.1")
    port = int(os.getenv("CORE_MEMORY_HTTP_PORT") or "8000")

    if not _is_loopback(host) and not _auth_required():
        print(
            f"ERROR: CORE_MEMORY_HTTP_HOST is set to '{host}' (non-loopback) but "
            "CORE_MEMORY_HTTP_TOKEN is not set. Refusing to start an unauthenticated "
            "server on a non-loopback interface.\n"
            "Set CORE_MEMORY_HTTP_TOKEN=<secret> or bind to 127.0.0.1.",
            file=sys.stderr,
        )
        sys.exit(1)

    uvicorn.run("core_memory.integrations.http.server:app", host=host, port=port)


if __name__ == "__main__":
    main()
