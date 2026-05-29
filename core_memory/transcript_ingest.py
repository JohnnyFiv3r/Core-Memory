from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.runtime.engine import _session_visible_bead_ids, process_flush, process_turn_finalized

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_ROLE_ALIASES = {
    "user": "user",
    "human": "user",
    "customer": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "agent": "assistant",
    "model": "assistant",
}
_ALLOWED_FLUSH_POLICIES = {"end_only", "per_session", "none"}


def _safe_id(value: Any, *, default: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    safe = _SAFE_ID_RE.sub("-", raw).strip("-_.:")
    return safe[:120] or default


def _parse_timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    probe = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(probe)
    except Exception as exc:  # pragma: no cover - exact parser exception varies by input
        raise ValueError(f"invalid_timestamp:{raw}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    mapped = _ROLE_ALIASES.get(role, "")
    if not mapped:
        raise ValueError(f"unsupported_role:{role or 'missing'}")
    return mapped


def normalize_transcript_payload(payload: dict[str, Any], *, max_turns: int = 500) -> dict[str, Any]:
    """Validate generic transcript input and build canonical turn envelopes.

    Input is intentionally small and adapter-friendly::

        {"transcript_id": "demo", "turns": [{"role": "user", "content": "..."}, ...]}

    Consecutive user/assistant utterances are paired into a single completed Core
    Memory turn and emitted through the canonical turn-finalization boundary.
    """

    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    raw_turns = payload.get("turns")
    if raw_turns is None and isinstance(payload.get("messages"), list):
        raw_turns = payload.get("messages")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ValueError("turns_required")
    if len(raw_turns) > max(1, int(max_turns)):
        raise ValueError(f"turns_limit_exceeded:{max_turns}")

    transcript_id = _safe_id(payload.get("transcript_id"), default="transcript")
    session_id = _safe_id(payload.get("session_id"), default=f"transcript:{transcript_id}")
    flush_policy = str(payload.get("flush_policy") or "none").strip().lower()
    if flush_policy not in _ALLOWED_FLUSH_POLICIES:
        raise ValueError(f"unsupported_flush_policy:{flush_policy}")
    base_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    utterances: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_turns):
        if not isinstance(item, dict):
            raise ValueError(f"turn_must_be_object:{idx}")
        role = _normalize_role(item.get("role") or item.get("type") or item.get("speaker") or item.get("name"))
        content = str(item.get("content") or item.get("text") or item.get("message") or item.get("body") or "").strip()
        if not content:
            raise ValueError(f"content_required:{idx}")
        utterances.append(
            {
                "index": idx,
                "role": role,
                "content": content,
                "timestamp": _parse_timestamp(item.get("timestamp") or item.get("ts") or ""),
                "speaker": str(item.get("speaker") or item.get("name") or "").strip(),
                "metadata": dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {},
            }
        )

    pairs: list[dict[str, Any]] = []
    i = 0
    while i < len(utterances):
        cur = utterances[i]
        if cur["role"] == "user":
            assistant = utterances[i + 1] if i + 1 < len(utterances) and utterances[i + 1]["role"] == "assistant" else None
            pairs.append({"user": cur, "assistant": assistant})
            i += 2 if assistant else 1
            continue
        pairs.append({"user": None, "assistant": cur})
        i += 1

    warnings: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    for pair_index, pair in enumerate(pairs):
        user = pair.get("user")
        assistant = pair.get("assistant")
        if isinstance(user, dict) and assistant is None:
            warnings.append(
                {
                    "code": "unpaired_final_user_turn",
                    "message": "Final user turn has no assistant response; ingesting it as a user-only turn.",
                    "source_index": int(user.get("index") or 0),
                }
            )
        first = user or assistant or {}
        last = assistant or user or {}
        first_meta = first.get("metadata") if isinstance(first.get("metadata"), dict) else {}
        turn_id = _safe_id(first_meta.get("turn_id"), default=f"transcript:{transcript_id}:turn-{pair_index + 1:04d}")
        metadata = {
            **dict(base_metadata or {}),
            "source": "transcript_ingest",
            "transcript_id": transcript_id,
            "source_index_start": int(first.get("index") or 0),
            "source_index_end": int(last.get("index") or first.get("index") or 0),
            "timestamp": str(first.get("timestamp") or last.get("timestamp") or ""),
            "user_speaker": str((user or {}).get("speaker") or ""),
            "assistant_speaker": str((assistant or {}).get("speaker") or ""),
            "roles": [r for r in [str((user or {}).get("role") or ""), str((assistant or {}).get("role") or "")] if r],
        }
        for prefix, utt in [("user", user), ("assistant", assistant)]:
            if isinstance(utt, dict) and isinstance(utt.get("metadata"), dict):
                for key, value in dict(utt.get("metadata") or {}).items():
                    metadata[f"{prefix}_{key}"] = value
        turn_rows: list[dict[str, Any]] = []
        if isinstance(user, dict):
            turn_rows.append(
                {
                    "speaker": str(user.get("speaker") or "user"),
                    "role": "user",
                    "content": str(user.get("content") or ""),
                    "ts": str(user.get("timestamp") or "") or None,
                    "metadata": dict(user.get("metadata") or {}),
                }
            )
        if isinstance(assistant, dict):
            turn_rows.append(
                {
                    "speaker": str(assistant.get("speaker") or "assistant"),
                    "role": "assistant",
                    "content": str(assistant.get("content") or ""),
                    "ts": str(assistant.get("timestamp") or "") or None,
                    "metadata": dict(assistant.get("metadata") or {}),
                }
            )
        envelopes.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "transaction_id": f"tx-{turn_id}",
                "trace_id": f"tr-{turn_id}",
                "turns": turn_rows,
                "trace_depth": 0,
                "origin": "TRANSCRIPT_INGEST",
                "tools_trace": [],
                "mesh_trace": [],
                "window_turn_ids": [],
                "window_bead_ids": [],
                "metadata": metadata,
            }
        )

    return {
        "ok": True,
        "transcript_id": transcript_id,
        "session_id": session_id,
        "flush_policy": flush_policy,
        "turns_received": len(utterances),
        "turns_paired": len(envelopes),
        "warnings": warnings,
        "envelopes": envelopes,
    }


def ingest_turn_envelopes(*, root: str, envelopes: list[dict[str, Any]], flush_policy: str = "none") -> dict[str, Any]:
    emitted: list[dict[str, Any]] = []
    skipped_existing = 0
    errors: list[dict[str, Any]] = []
    session_ids: list[str] = []
    for env in list(envelopes or []):
        env = dict(env or {})
        session_id = str(env.get("session_id") or "")
        if session_id and session_id not in session_ids:
            session_ids.append(session_id)
        try:
            if session_id:
                prior_visible = _session_visible_bead_ids(root=root, session_id=session_id)
                env["window_bead_ids"] = sorted(
                    set([str(x) for x in (env.get("window_bead_ids") or []) if str(x).strip()] + prior_visible)
                )
            out = process_turn_finalized(root=root, **env)
            emitted_flag = bool(out.get("ok", True))
            row = {
                "turn_id": str(env.get("turn_id") or ""),
                "session_id": session_id,
                "status": "ingested" if emitted_flag else "skipped_existing",
                "worker_ok": bool(out.get("ok", True)),
                "bead_ids": list(out.get("bead_ids") or []),
            }
            emitted.append(row)
            if not emitted_flag:
                skipped_existing += 1
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            errors.append({"turn_id": str(env.get("turn_id") or ""), "session_id": session_id, "error": str(exc)})

    flushes: list[dict[str, Any]] = []
    policy = str(flush_policy or "none").strip().lower()
    if policy not in _ALLOWED_FLUSH_POLICIES:
        policy = "none"
    if policy != "none":
        for session_id in session_ids:
            try:
                flushes.append(
                    process_flush(
                        root=root,
                        session_id=session_id,
                        promote=True,
                        token_budget=128000,
                        max_beads=200,
                        source="transcript_ingest",
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive integration boundary
                flushes.append({"ok": False, "step": "process_flush", "error": str(exc), "session_id": session_id})

    return {
        "ok": not errors,
        "turns_ingested": sum(1 for row in emitted if row.get("status") == "ingested"),
        "skipped_existing_count": skipped_existing,
        "errors": errors,
        "ingested": emitted,
        "flush_policy": policy,
        "flushes": flushes,
    }


def _associations_created_summary(root: str, bead_ids: list[str]) -> dict[str, Any]:
    import json as _json
    created = {str(x) for x in (bead_ids or []) if str(x).strip()}
    if not created:
        return {"count": 0, "by_type": {}, "items": []}
    try:
        idx = _json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return {"count": 0, "by_type": {}, "items": []}
    rows: list[dict[str, Any]] = []
    for assoc in (idx.get("associations") or []) if isinstance(idx, dict) else []:
        if not isinstance(assoc, dict):
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "").strip()
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "").strip()
        if src not in created:
            continue
        rows.append(
            {
                "source": src,
                "target": tgt,
                "source_bead_id": src,
                "target_bead_id": tgt,
                "relationship": str(assoc.get("relationship") or ""),
                "confidence": assoc.get("confidence"),
                "reason_code": assoc.get("reason_code"),
                "reason_text": assoc.get("reason_text") or assoc.get("rationale"),
                "provenance": assoc.get("provenance"),
            }
        )
    rows.sort(key=lambda r: (r.get("source_bead_id") or "", r.get("target_bead_id") or "", r.get("relationship") or ""))
    by_type: dict[str, int] = {}
    for row in rows:
        rel = str(row.get("relationship") or "").strip() or "unknown"
        by_type[rel] = by_type.get(rel, 0) + 1
    return {"count": len(rows), "by_type": dict(sorted(by_type.items())), "items": rows}


def _resolve_envelope_speakers(
    root: str,
    envelopes: list[dict[str, Any]],
    source_system: str = "",
) -> list[dict[str, Any]]:
    """Resolve speaker labels in envelopes to canonical entity IDs.

    Loads the entity registry, resolves each unique speaker label, attaches
    speaker_attribution to each envelope's metadata, and persists any newly
    created entities back to the index.
    """
    unique_labels: set[str] = set()
    for env in envelopes:
        meta = (env or {}).get("metadata") or {}
        for key in ("user_speaker", "assistant_speaker"):
            label = str(meta.get(key) or "").strip()
            if label:
                unique_labels.add(label)

    if not unique_labels:
        return envelopes

    try:
        from core_memory.entity.registry import load_entity_registry, save_entity_registry
        from core_memory.entity.speaker_resolver import resolve_speaker

        index = load_entity_registry(root)
        resolutions: dict[str, dict[str, Any]] = {}
        any_new = False
        for label in sorted(unique_labels):
            res = resolve_speaker(index, label, source_system)
            resolutions[label] = res.to_dict()
            if res.resolved:
                any_new = True

        result: list[dict[str, Any]] = []
        for env in envelopes:
            env = dict(env)
            meta = dict(env.get("metadata") or {})
            attribution: list[dict[str, Any]] = []
            for key, role in (("user_speaker", "user"), ("assistant_speaker", "assistant")):
                label = str(meta.get(key) or "").strip()
                if label and label in resolutions:
                    entry = dict(resolutions[label])
                    entry["role"] = role
                    attribution.append(entry)
            if attribution:
                meta["speaker_attribution"] = attribution
            env["metadata"] = meta
            result.append(env)

        if any_new:
            save_entity_registry(root, index)

        return result
    except Exception:
        return envelopes


def ingest_transcript(
    *,
    root: str = ".",
    transcript_id: str = "transcript",
    turns: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    flush_policy: str = "none",
    metadata: dict[str, Any] | None = None,
    max_turns: int = 500,
) -> dict[str, Any]:
    """Synchronously ingest a generic transcript through canonical turn finalization."""

    normalized = normalize_transcript_payload(
        {
            "transcript_id": transcript_id,
            "session_id": session_id,
            "turns": list(turns or []),
            "flush_policy": flush_policy,
            "metadata": dict(metadata or {}),
        },
        max_turns=max_turns,
    )

    source_system = str((metadata or {}).get("source_system") or "").strip().lower()
    envelopes = _resolve_envelope_speakers(
        root,
        list(normalized.get("envelopes") or []),
        source_system=source_system,
    )

    out = ingest_turn_envelopes(
        root=root,
        envelopes=envelopes,
        flush_policy=str(normalized.get("flush_policy") or flush_policy),
    )
    new_bead_ids = sorted(
        {
            str(bid)
            for row in (out.get("ingested") or [])
            if isinstance(row, dict)
            for bid in (row.get("bead_ids") or [])
            if str(bid).strip()
        }
    )
    return {
        "ok": bool(out.get("ok")),
        "kind": "transcript_ingest",
        "transcript_id": str(normalized.get("transcript_id") or ""),
        "session_id": str(normalized.get("session_id") or ""),
        "turns_received": int(normalized.get("turns_received") or 0),
        "turns_paired": int(normalized.get("turns_paired") or 0),
        "warnings": list(normalized.get("warnings") or []) + list(out.get("warnings") or []),
        "associations_created": _associations_created_summary(root, new_bead_ids),
        **out,
    }
