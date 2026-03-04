from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("_", " ").replace("-", " ").split())


def _default_incidents_path() -> Path:
    return Path(__file__).parent / "data" / "incidents.json"


def load_incidents(root: Path) -> list[dict]:
    # Allow per-memory-root override, then fallback to packaged defaults.
    candidates = [
        root / "incidents.json",
        root / ".beads" / "incidents.json",
        _default_incidents_path(),
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            continue
    return []


def matched_incident_ids(query: str, root: Path) -> list[str]:
    q = _norm(query)
    out = []
    for row in load_incidents(root):
        iid = str(row.get("incident_id") or "")
        aliases = [str(a) for a in (row.get("aliases") or [])]
        if any(_norm(a) in q for a in aliases if a):
            out.append(iid)
    return sorted(set(out))


def tag_incident(root: Path, incident_id: str, bead_ids: list[str]) -> dict:
    idx_file = root / ".beads" / "index.json"
    if not idx_file.exists():
        return {"ok": False, "error": "index_missing"}
    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    beads = idx.get("beads") or {}

    changed = []
    for bid in bead_ids:
        b = beads.get(str(bid))
        if not b:
            continue
        if str(b.get("incident_id") or "") == incident_id:
            continue
        b["incident_id"] = incident_id
        changed.append(str(bid))

    idx_file.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    h = hashlib.sha256((incident_id + "|" + "|".join(sorted(changed))).encode("utf-8")).hexdigest()[:16]
    return {"ok": True, "incident_id": incident_id, "changed": changed, "changed_count": len(changed), "change_hash": h}
