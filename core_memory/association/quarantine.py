from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(root: Path) -> Path:
    return root / ".beads" / "events" / "association-quarantine.jsonl"


def _dedupe_key(record: dict[str, Any]) -> str:
    raw_relationship = str(record.get("relationship_raw") or record.get("relationship") or "").strip().lower()
    packed = "||".join(
        [
            str(record.get("source_bead") or "").strip(),
            str(record.get("target_bead") or "").strip(),
            raw_relationship,
            str(record.get("reason_text") or "").strip(),
            str(record.get("provenance") or "").strip().lower(),
        ]
    )
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def write_quarantine(
    root: Path,
    record: dict[str, Any],
    *,
    reasons: list[str],
    warnings: list[str] | None = None,
    original_payload: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    dedupe_key = _dedupe_key(record)

    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    existing: dict[str, Any] | None = None
    for row in rows:
        if str(row.get("dedupe_key") or "") == dedupe_key:
            existing = row
            break

    now = _now()

    if existing is not None:
        existing["seen_count"] = int(existing.get("seen_count") or 1) + 1
        existing["last_seen_at"] = now
        for reason in list(reasons or []):
            _append_unique(existing.setdefault("reasons", []), str(reason))
        for warning in list(warnings or []):
            _append_unique(existing.setdefault("warnings", []), str(warning))

        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return {"ok": True, "deduped": True, "dedupe_key": dedupe_key, "seen_count": existing["seen_count"]}

    row = {
        "schema": "core_memory.association_quarantine.v1",
        "dedupe_key": dedupe_key,
        "created_at": now,
        "last_seen_at": now,
        "seen_count": 1,
        "session_id": str(session_id or ""),
        "reasons": list(dict.fromkeys(str(x) for x in (reasons or []) if str(x))),
        "warnings": list(dict.fromkeys(str(x) for x in (warnings or []) if str(x))),
        "edge": record,
        "original_payload": original_payload if isinstance(original_payload, dict) else {},
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    return {"ok": True, "deduped": False, "dedupe_key": dedupe_key, "seen_count": 1}
