from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _turns_dir(root: Path) -> Path:
    return root / ".turns"


def _session_turns_file(root: Path, session_id: str) -> Path:
    return _turns_dir(root) / f"session-{session_id}.jsonl"


def _session_idx_file(root: Path, session_id: str) -> Path:
    return _turns_dir(root) / f"session-{session_id}.idx.json"


def _session_id_from_idx_name(name: str) -> str | None:
    # session-<session_id>.idx.json
    if not name.startswith("session-") or not name.endswith(".idx.json"):
        return None
    return name[len("session-") : -len(".idx.json")]


def _read_idx(path: Path) -> dict[str, dict[str, int]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            out: dict[str, dict[str, int]] = {}
            for k, v in payload.items():
                if isinstance(v, dict):
                    off = int(v.get("offset", -1))
                    ln = int(v.get("length", -1))
                    if off >= 0 and ln >= 0:
                        out[str(k)] = {"offset": off, "length": ln}
            return out
    except Exception:
        return {}
    return {}


def _write_idx(path: Path, idx: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def rebuild_session_index(*, root: Path, session_id: str) -> dict[str, Any]:
    """Rebuild turn_id->offset/length index from authoritative session JSONL."""
    turns_file = _session_turns_file(root, session_id)
    idx_file = _session_idx_file(root, session_id)
    if not turns_file.exists():
        _write_idx(idx_file, {})
        return {"ok": True, "session_id": session_id, "entries": 0, "path": str(idx_file)}

    idx: dict[str, dict[str, int]] = {}
    with open(turns_file, "rb") as f:
        while True:
            offset = int(f.tell())
            raw = f.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            tid = str(obj.get("turn_id") or "").strip()
            if not tid:
                continue
            idx[tid] = {"offset": offset, "length": int(len(raw))}

    _write_idx(idx_file, idx)
    return {"ok": True, "session_id": session_id, "entries": len(idx), "path": str(idx_file)}


def rebuild_all_indexes(*, root: Path) -> dict[str, Any]:
    turns_dir = _turns_dir(root)
    turns_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for turns_file in sorted(turns_dir.glob("session-*.jsonl")):
        name = turns_file.name
        if not name.startswith("session-") or not name.endswith(".jsonl"):
            continue
        sid = name[len("session-") : -len(".jsonl")]
        if not sid:
            continue
        out.append(rebuild_session_index(root=root, session_id=sid))
    return {"ok": True, "sessions": out, "count": len(out)}


def append_turn_record(
    *,
    root: Path,
    session_id: str,
    turn_id: str,
    transaction_id: str,
    trace_id: str,
    origin: str,
    ts: str,
    user_query: str,
    assistant_final: str | None,
    assistant_final_ref: str | None,
    assistant_final_hash: str,
    tools_trace: list[dict[str, Any]] | None,
    mesh_trace: list[dict[str, Any]] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Append authoritative raw turn payload and update per-session turn index.

    Files:
      - .turns/session-<session_id>.jsonl
      - .turns/session-<session_id>.idx.json
    """
    turns_file = _session_turns_file(root, session_id)
    idx_file = _session_idx_file(root, session_id)
    turns_file.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "schema": "core_memory.turn_record.v1",
        "session_id": str(session_id),
        "turn_id": str(turn_id),
        "transaction_id": str(transaction_id),
        "trace_id": str(trace_id),
        "ts": str(ts),
        "origin": str(origin),
        "user_query": str(user_query or ""),
        "assistant_final": assistant_final,
        "assistant_final_ref": assistant_final_ref,
        "assistant_final_hash": str(assistant_final_hash or ""),
        "tools_trace": list(tools_trace or []),
        "mesh_trace": list(mesh_trace or []),
        "metadata": dict(metadata or {}),
    }

    encoded = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")

    with open(turns_file, "ab") as f:
        offset = int(f.tell())
        f.write(encoded)
        f.flush()

    idx = _read_idx(idx_file)
    idx[str(turn_id)] = {"offset": offset, "length": int(len(encoded))}
    _write_idx(idx_file, idx)

    return {"ok": True, "path": str(turns_file), "offset": offset, "length": len(encoded)}


def get_turn_record(*, root: Path, session_id: str, turn_id: str) -> dict[str, Any] | None:
    idx = _read_idx(_session_idx_file(root, session_id))
    hit = idx.get(str(turn_id))
    if not hit:
        return None
    turns_file = _session_turns_file(root, session_id)
    if not turns_file.exists():
        return None
    try:
        with open(turns_file, "rb") as f:
            f.seek(int(hit.get("offset", 0)))
            raw = f.read(int(hit.get("length", 0)))
        line = raw.decode("utf-8").strip()
        obj = json.loads(line)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def find_turn_record(*, root: Path, turn_id: str, session_id: str | None = None) -> dict[str, Any] | None:
    """Find a turn by turn_id, optionally constrained to a session.

    Returns the full turn record (from JSONL archive) when found.
    """
    if session_id:
        return get_turn_record(root=root, session_id=session_id, turn_id=turn_id)

    turns_dir = _turns_dir(root)
    if not turns_dir.exists():
        return None

    for idx_file in sorted(turns_dir.glob("session-*.idx.json")):
        sid = _session_id_from_idx_name(idx_file.name)
        if not sid:
            continue
        hit = get_turn_record(root=root, session_id=sid, turn_id=turn_id)
        if hit:
            return hit
    return None


def get_turn_tools(*, root: Path, turn_id: str, session_id: str | None = None) -> dict[str, Any] | None:
    row = find_turn_record(root=root, turn_id=turn_id, session_id=session_id)
    if not row:
        return None
    return {
        "schema": "core_memory.turn_tools.v1",
        "session_id": row.get("session_id"),
        "turn_id": row.get("turn_id"),
        "tools_trace": list(row.get("tools_trace") or []),
        "mesh_trace": list(row.get("mesh_trace") or []),
    }


def _read_all_session_turns(root: Path, session_id: str) -> list[dict[str, Any]]:
    turns_file = _session_turns_file(root, session_id)
    if not turns_file.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in turns_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def get_adjacent_turns(
    *,
    root: Path,
    turn_id: str,
    session_id: str | None = None,
    before: int = 1,
    after: int = 1,
) -> dict[str, Any] | None:
    pivot = find_turn_record(root=root, turn_id=turn_id, session_id=session_id)
    if not pivot:
        return None
    sid = str(pivot.get("session_id") or "")
    if not sid:
        return None

    rows = _read_all_session_turns(root, sid)
    if not rows:
        return None

    idx = -1
    for i, r in enumerate(rows):
        if str(r.get("turn_id") or "") == str(turn_id):
            idx = i
            break
    if idx < 0:
        return None

    b = max(0, int(before or 0))
    a = max(0, int(after or 0))
    lo = max(0, idx - b)
    hi = min(len(rows), idx + a + 1)

    return {
        "schema": "core_memory.adjacent_turns.v1",
        "session_id": sid,
        "pivot_turn_id": str(turn_id),
        "before": rows[lo:idx],
        "pivot": rows[idx],
        "after": rows[idx + 1 : hi],
    }
