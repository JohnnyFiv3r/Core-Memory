from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from core_memory.persistence.io_utils import store_lock
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.integrations.neo4j.sync import sync_to_neo4j
from core_memory.runtime.dreamer import analysis as dreamer
from core_memory.runtime.dreamer.candidates import enqueue_dreamer_candidates


_SIDE_EFFECT_KINDS = {
    "dreamer-run", "neo4j-sync", "health-recompute",
    "turn-enrichment", "graphiti-episode-add", "myelination-update",
    "data-insight-poll", "association-pass",
}
_CLAIM_LEASE_SECONDS = 120


def _events_dir(root: str | Path) -> Path:
    p = Path(root) / ".beads" / "events"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _queue_path(root: str | Path) -> Path:
    return _events_dir(root) / "side-effects-queue.json"


def _state_path(root: str | Path) -> Path:
    return _events_dir(root) / "side-effects-queue-state.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _default_state() -> dict[str, Any]:
    return {"consecutive_failures": 0, "opened_until": 0, "last_error": ""}


def _load_queue_and_state_locked(root: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    qpath = _queue_path(root)
    spath = _state_path(root)
    queue = _read_json(qpath, [])
    state = _read_json(spath, _default_state())
    if not isinstance(queue, list):
        queue = []
    if not isinstance(state, dict):
        state = _default_state()
    return [x for x in queue if isinstance(x, dict)], dict(state)


def _persist_queue_and_state_locked(root: str | Path, queue: list[dict[str, Any]], state: dict[str, Any]) -> None:
    _write_json(_queue_path(root), list(queue))
    _write_json(_state_path(root), dict(state))


def enqueue_side_effect_event(
    *,
    root: str | Path,
    kind: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    k = str(kind or "").strip().lower()
    if k not in _SIDE_EFFECT_KINDS:
        return {
            "ok": False,
            "error": {"code": "unknown_kind", "kind": k, "allowed": sorted(_SIDE_EFFECT_KINDS)},
        }

    idem = str(idempotency_key or "").strip()
    with store_lock(Path(root)):
        queue, state = _load_queue_and_state_locked(root)

        if idem:
            for item in queue:
                if str((item or {}).get("idempotency_key") or "") == idem:
                    return {
                        "ok": True,
                        "duplicate": True,
                        "id": item.get("id"),
                        "queue_depth": len(queue),
                        "kind": k,
                    }

        item = {
            "id": f"se-{uuid.uuid4().hex[:12]}",
            "kind": k,
            "payload": dict(payload or {}),
            "idempotency_key": idem or None,
            "created_at": int(time.time()),
            "attempts": 0,
            "next_retry_at": 0,
            "lease_until": 0,
            "lease_token": None,
        }
        queue.append(item)
        _persist_queue_and_state_locked(root, queue, state)
        return {
            "ok": True,
            "duplicate": False,
            "id": item["id"],
            "queue_depth": len(queue),
            "kind": k,
        }


def side_effect_queue_status(root: str | Path, *, now_ts: int | None = None) -> dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    qpath = _queue_path(root)
    spath = _state_path(root)
    with store_lock(Path(root)):
        queue, state = _load_queue_and_state_locked(root)

    opened_until = int(state.get("opened_until") or 0)
    circuit_open = opened_until > now

    ready = 0
    leased = 0
    next_retry_at: int | None = None
    by_kind: dict[str, int] = {}
    for item in queue:
        if not isinstance(item, dict):
            continue
        k = str(item.get("kind") or "unknown")
        by_kind[k] = int(by_kind.get(k, 0)) + 1
        nr = int(item.get("next_retry_at") or 0)
        lease_until = int(item.get("lease_until") or 0)
        if lease_until > now:
            leased += 1
            if next_retry_at is None or lease_until < next_retry_at:
                next_retry_at = lease_until
            continue
        if nr <= now:
            ready += 1
        else:
            if next_retry_at is None or nr < next_retry_at:
                next_retry_at = nr

    return {
        "ok": True,
        "kind": "side_effects",
        "path": str(qpath),
        "state_path": str(spath),
        "queue_depth": len(queue),
        "ready": ready,
        "leased": leased,
        "processable_now": 0 if circuit_open else ready,
        "next_retry_at": next_retry_at,
        "circuit_open": circuit_open,
        "opened_until": opened_until,
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "last_error": str(state.get("last_error") or ""),
        "by_kind": by_kind,
    }


def process_side_effect_event(*, root: str | Path, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    k = str(kind or "").strip().lower()
    p = dict(payload or {})

    if k == "dreamer-run":
        mode = str(p.get("mode") or "suggest").strip().lower()
        if mode not in {"off", "suggest", "reviewed_apply"}:
            mode = "suggest"
        if mode == "off":
            return {
                "ok": True,
                "kind": k,
                "mode": mode,
                "skipped": True,
                "reason": "dreamer_mode_off",
            }

        store = MemoryStore(root=str(root))
        run_id = f"dream-sidefx-{uuid.uuid4().hex[:10]}"
        out = dreamer.run_analysis(
            store=store,
            novel_only=bool(p.get("novel_only", True)),
            seen_window_runs=int(p.get("seen_window_runs", 3)),
            max_exposure=int(p.get("max_exposure", 10)),
        )
        queue_out = enqueue_dreamer_candidates(
            root=root,
            associations=list(out if isinstance(out, list) else []),
            run_metadata={
                "run_id": run_id,
                "mode": mode,
                "source": "side_effect_queue",
                "session_id": str(p.get("session_id") or ""),
                "flush_tx_id": str(p.get("flush_tx_id") or ""),
                "novel_only": bool(p.get("novel_only", True)),
                "seen_window_runs": int(p.get("seen_window_runs", 3)),
                "max_exposure": int(p.get("max_exposure", 10)),
            },
        )

        # Storyline overlays: detect worldline convergence and enqueue
        # narrative candidates (threshold-gated; decide flow still required).
        convergence_out: dict[str, Any] = {"ok": True, "detected": 0, "enqueued": 0}
        try:
            from core_memory.runtime.dreamer.convergence import enqueue_narrative_candidates
            convergence_out = enqueue_narrative_candidates(root, run_id=run_id, source="side_effect_queue")
        except Exception:
            convergence_out = {"ok": False, "error": "convergence_detection_failed"}

        # Synthesize themes from the updated candidate pool and enqueue them.
        theme_queue_out: dict[str, Any] = {"ok": True, "added": 0, "quarantined": 0}
        try:
            from core_memory.runtime.dreamer.analysis import synthesize_themes
            from core_memory.runtime.dreamer.candidates import enqueue_synthesized_themes
            themes = synthesize_themes(root)
            if themes:
                theme_queue_out = enqueue_synthesized_themes(root, themes)
        except Exception:
            pass

        # Tension discovery: goal-conflict candidates (threshold-free; decide flow
        # still required before anything is endorsed).
        tension_out: dict[str, Any] = {"ok": True, "detected": 0, "enqueued": 0}
        try:
            from core_memory.runtime.dreamer.tension_discovery import enqueue_goal_conflict_candidates
            tension_out = enqueue_goal_conflict_candidates(root, run_id=run_id, source="side_effect_queue")
        except Exception:
            tension_out = {"ok": False, "error": "tension_discovery_failed"}

        # Goal decay: surface dormant goals for SOUL / goal-lifecycle review.
        goal_decay_out: dict[str, Any] = {"ok": True, "detected": 0, "enqueued": 0}
        try:
            from core_memory.runtime.dreamer.goal_decay import enqueue_goal_decay_warnings
            goal_decay_out = enqueue_goal_decay_warnings(root, run_id=run_id, source="side_effect_queue")
        except Exception:
            goal_decay_out = {"ok": False, "error": "goal_decay_failed"}

        # Goal discovery: propose latent goals from repeated behavior.
        goal_discovery_out: dict[str, Any] = {"ok": True, "detected": 0, "enqueued": 0}
        try:
            from core_memory.runtime.dreamer.goal_discovery import enqueue_latent_goal_candidates
            goal_discovery_out = enqueue_latent_goal_candidates(root, run_id=run_id, source="side_effect_queue")
        except Exception:
            goal_discovery_out = {"ok": False, "error": "goal_discovery_failed"}

        # Future projection: advisory storyline continuations (never creates goals).
        projection_out: dict[str, Any] = {"ok": True, "projection_count": 0}
        try:
            from core_memory.runtime.dreamer.projection import compute_future_projections
            proj = compute_future_projections(root, run_id=run_id, persist=True)
            projection_out = {"ok": bool(proj.get("ok")), "projection_count": int(proj.get("projection_count") or 0)}
        except Exception:
            projection_out = {"ok": False, "error": "future_projection_failed"}

        return {
            "ok": True,
            "kind": k,
            "mode": mode,
            "results": out,
            "result_count": len(out) if isinstance(out, list) else 0,
            "candidate_queue": queue_out,
            "theme_queue": theme_queue_out,
            "convergence": convergence_out,
            "tension": tension_out,
            "goal_decay": goal_decay_out,
            "goal_discovery": goal_discovery_out,
            "projection": projection_out,
        }

    if k == "neo4j-sync":
        out = sync_to_neo4j(
            root=str(root),
            session_id=(str(p.get("session_id")) if p.get("session_id") is not None else None),
            dry_run=bool(p.get("dry_run", False)),
            prune=bool(p.get("prune", False)),
        )
        # Non-runtime dependency/config issues are treated as terminal skip rather
        # than perpetual retries.
        if not bool(out.get("ok")):
            errs = [e for e in (out.get("errors") or []) if isinstance(e, dict)]
            terminal_codes = {"neo4j_disabled", "neo4j_dependency_missing", "neo4j_config_error"}
            if any(str(e.get("code") or "") in terminal_codes for e in errs):
                return {
                    "ok": True,
                    "kind": k,
                    "terminal_skipped": True,
                    "result": out,
                }
        return {
            "ok": bool(out.get("ok")),
            "kind": k,
            "result": out,
            "error": (out.get("errors") or [{}])[0] if not bool(out.get("ok")) else None,
        }

    if k == "health-recompute":
        out = semantic_doctor(Path(root))
        return {
            "ok": True,
            "kind": k,
            "result": out,
        }

    if k == "turn-enrichment":
        from core_memory.runtime.passes.enrichment import run_turn_enrichment
        enrichment_run_id = str(p.get("enrichment_run_id") or "").strip() or uuid.uuid4().hex
        out = run_turn_enrichment(root=str(root), payload=p, enrichment_run_id=enrichment_run_id)
        return {
            "ok": bool(out.get("ok")),
            "kind": k,
            "result": out,
        }

    if k == "graphiti-episode-add":
        try:
            from core_memory.persistence.graph.factory import create_graph_backend
            from core_memory.persistence.graph.graphiti_backend import GraphitiGraphBackend
            gb = create_graph_backend(Path(root))
            if not isinstance(gb, GraphitiGraphBackend):
                return {
                    "ok": True,
                    "kind": k,
                    "terminal_skipped": True,
                    "reason": "active backend is not GraphitiGraphBackend",
                }
            if p.get("bulk_sync"):
                index_path = Path(root) / ".beads" / "index.json"
                index = _read_json(index_path, {"beads": {}, "associations": []})
                beads = list(index.get("beads", {}).values())
                assocs = index.get("associations", [])
                result = gb.sync_from_storage(beads=beads, associations=assocs)
                return {"ok": True, "kind": k, "result": result}
            bead = p.get("bead") or {}
            assoc = p.get("assoc")
            if assoc:
                gb._write_association_sync(assoc)
            else:
                gb._write_bead_sync(bead)
            return {"ok": True, "kind": k}
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("graphiti-episode-add failed: %s", exc)
            return {
                "ok": False,
                "kind": k,
                "error": {"code": "graphiti_write_error", "detail": str(exc)},
            }

    if k == "data-insight-poll":
        import os as _os
        db_url = str(p.get("db_url") or _os.getenv("CORE_MEMORY_PIPEHOUSE_DB_URL") or "").strip()
        session_id = str(p.get("session_id") or _os.getenv("CORE_MEMORY_PIPEHOUSE_SESSION_ID") or "data-insights").strip()
        batch_size = max(1, int(p.get("batch_size") or 50))

        if not db_url:
            return {"ok": True, "kind": k, "skipped": True, "reason": "CORE_MEMORY_PIPEHOUSE_DB_URL not configured"}

        try:
            import psycopg2  # type: ignore
        except ImportError:
            return {
                "ok": True,
                "kind": k,
                "terminal_skipped": True,
                "reason": "psycopg2 not installed; install core-memory[pipehouse] to enable polling",
            }

        from core_memory.runtime.ingest.data_insight import ingest_data_insight_row

        try:
            conn = psycopg2.connect(db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, source_table, as_of_timestamp, entity_refs, attribute_tags, "
                        "title, content, because, confidence, core_memory_unifying_id, pipehouse_metadata "
                        "FROM core_memory_insights WHERE ingested_at IS NULL "
                        "ORDER BY as_of_timestamp ASC LIMIT %s",
                        (batch_size,),
                    )
                    rows = cur.fetchall()
                    columns = [desc[0] for desc in cur.description]

                ingested = 0
                failed = 0
                bead_ids: list[str] = []
                for raw_row in rows:
                    row = dict(zip(columns, raw_row))
                    try:
                        result = ingest_data_insight_row(str(root), session_id, row)
                        bead_id = str(result.get("bead_id") or "")
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE core_memory_insights SET ingested_at = NOW(), core_memory_bead_id = %s WHERE id = %s",
                                (bead_id or None, row["id"]),
                            )
                        conn.commit()
                        ingested += 1
                        if bead_id:
                            bead_ids.append(bead_id)
                    except Exception as row_exc:
                        failed += 1
                        import logging as _logging
                        _logging.getLogger(__name__).warning("data-insight-poll: row %s failed: %s", row.get("id"), row_exc)
            finally:
                conn.close()
        except Exception as conn_exc:
            return {
                "ok": False,
                "kind": k,
                "error": {"code": "db_connection_error", "detail": str(conn_exc)},
            }

        return {
            "ok": True,
            "kind": k,
            "ingested": ingested,
            "failed": failed,
            "bead_ids": bead_ids,
        }

    if k == "association-pass":
        from core_memory.runtime.associations.coverage import run_association_coverage

        out = run_association_coverage(
            root=root,
            run_id=str(p.get("run_id") or ""),
            bead_ids=[str(x) for x in (p.get("bead_ids") or []) if str(x).strip()],
            session_id=str(p.get("session_id") or ""),
            trigger=str(p.get("trigger") or "operator"),
            candidate_bead_ids=[str(x) for x in (p.get("candidate_bead_ids") or []) if str(x).strip()],
            max_candidates=int(p.get("max_candidates") or 40),
            policy_version=str(p.get("policy_version") or "bead_association.v1"),
            prompt_version=str(p.get("prompt_version") or "association_judge.v1"),
            rubric_version=str(p.get("rubric_version") or "association_truth.v1"),
            graph_revision=str(p.get("graph_revision") or ""),
            skipped_bead_ids=[str(x) for x in (p.get("skipped_bead_ids") or []) if str(x).strip()],
        )
        return {
            "ok": bool(out.get("ok")),
            "kind": k,
            "result": out,
            "error": out.get("error") if not bool(out.get("ok")) else None,
        }

    if k == "myelination-update":
        from core_memory.runtime.observability.myelination import (
            compute_myelination_bonus_map,
            apply_contradiction_decay,
        )
        since = str(p.get("since") or "30d")
        limit = int(p.get("limit") or 1000)
        manifest = compute_myelination_bonus_map(root, since=since, limit=limit)
        if bool(manifest.get("enabled")):
            apply_contradiction_decay(root, manifest.get("bonus_by_bead_id") or {})
        manifest_path = Path(root) / ".beads" / "events" / "myelination-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(manifest_path, manifest)
        return {
            "ok": True,
            "kind": k,
            "enabled": bool(manifest.get("enabled")),
            "stats": dict(manifest.get("stats") or {}),
            "manifest_path": str(manifest_path),
        }

    return {
        "ok": False,
        "kind": k,
        "error": {"code": "unknown_kind", "kind": k, "allowed": sorted(_SIDE_EFFECT_KINDS)},
    }


def drain_side_effect_queue(
    *,
    root: str | Path,
    max_items: int = 2,
    processor: Callable[..., dict[str, Any]] | None = None,
    now_ts: int | None = None,
) -> dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    max_n = max(0, int(max_items))
    process_item = processor or process_side_effect_event

    with store_lock(Path(root)):
        queue, state = _load_queue_and_state_locked(root)
        opened_until = int(state.get("opened_until") or 0)
        if opened_until > now:
            return {
                "ok": True,
                "processed": 0,
                "failed": 0,
                "queue_depth": len(queue),
                "circuit_open": True,
                "opened_until": opened_until,
                "last_error": str(state.get("last_error") or ""),
            }

        claimed: list[dict[str, Any]] = []
        for item in queue:
            if len(claimed) >= max_n:
                break
            if int(item.get("next_retry_at") or 0) > now:
                continue
            if int(item.get("lease_until") or 0) > now:
                continue
            token = f"lease-{uuid.uuid4().hex[:10]}"
            item["lease_until"] = now + _CLAIM_LEASE_SECONDS
            item["lease_token"] = token
            claimed.append(dict(item))

        _persist_queue_and_state_locked(root, queue, state)

    processed = 0
    failed = 0
    skipped_terminal = 0
    item_results: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for claim in claimed:
        item_id = str(claim.get("id") or "")
        lease_token = str(claim.get("lease_token") or "")
        try:
            out = process_item(root=root, kind=str(claim.get("kind") or ""), payload=dict(claim.get("payload") or {}))
        except Exception as exc:
            out = {"ok": False, "error": {"code": "processor_exception", "message": str(exc)}}

        if isinstance(out, dict):
            results.append(dict(out))

        with store_lock(Path(root)):
            queue, state = _load_queue_and_state_locked(root)
            target = None
            for row in queue:
                if str(row.get("id") or "") == item_id and str(row.get("lease_token") or "") == lease_token:
                    target = row
                    break
            if target is None:
                _persist_queue_and_state_locked(root, queue, state)
                continue

            if bool(out.get("ok")):
                processed += 1
                if bool(out.get("terminal_skipped")):
                    skipped_terminal += 1
                item_results.append({"id": item_id, "kind": claim.get("kind"), "result": out})
                queue = [r for r in queue if str(r.get("id") or "") != item_id]
                state["consecutive_failures"] = 0
                state["opened_until"] = 0
                state["last_error"] = ""
                _persist_queue_and_state_locked(root, queue, state)
                continue

            failed += 1
            target["attempts"] = int(target.get("attempts") or 0) + 1
            backoff = min(300, 2 ** min(8, int(target.get("attempts") or 0)))
            target["next_retry_at"] = now + backoff
            target["lease_until"] = 0
            target["lease_token"] = None

            state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
            err = out.get("error") or {}
            state["last_error"] = str((err.get("code") if isinstance(err, dict) else err) or "side_effect_failed")
            if int(state.get("consecutive_failures") or 0) >= 3:
                state["opened_until"] = now + 30

            _persist_queue_and_state_locked(root, queue, state)

    with store_lock(Path(root)):
        queue, state = _load_queue_and_state_locked(root)

    return {
        "ok": True,
        "processed": processed,
        "failed": failed,
        "skipped_terminal": skipped_terminal,
        "claimed": len(claimed),
        "queue_depth": len(queue),
        "circuit_open": bool(int(state.get("opened_until") or 0) > int(time.time())),
        "opened_until": int(state.get("opened_until") or 0),
        "last_error": str(state.get("last_error") or ""),
        "item_results": item_results,
        "results": item_results,
    }


__all__ = [
    "enqueue_side_effect_event",
    "side_effect_queue_status",
    "drain_side_effect_queue",
    "process_side_effect_event",
]
