from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("_", " ").replace("-", " ").split())


def load_incidents(root: Path) -> list[dict]:
    p = Path(__file__).parent / "data" / "incidents.yml"
    if not p.exists():
        return []

    # tiny YAML reader for constrained schema
    rows = []
    cur = None
    in_aliases = False
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("- incident_id:"):
            if cur:
                rows.append(cur)
            cur = {"incident_id": line.split(":", 1)[1].strip(), "aliases": [], "notes": ""}
            in_aliases = False
            continue
        if cur is None:
            continue
        if line.strip().startswith("aliases:"):
            in_aliases = True
            continue
        if in_aliases and line.strip().startswith("-"):
            cur["aliases"].append(line.strip()[1:].strip())
            continue
        if line.strip().startswith("notes:"):
            cur["notes"] = line.split(":", 1)[1].strip().strip('"')
            in_aliases = False
            continue
    if cur:
        rows.append(cur)
    return rows


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

    # deterministic change hash
    h = hashlib.sha256((incident_id + "|" + "|".join(sorted(changed))).encode("utf-8")).hexdigest()[:16]
    return {"ok": True, "incident_id": incident_id, "changed": changed, "changed_count": len(changed), "change_hash": h}
