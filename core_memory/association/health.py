from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def association_health_report(root: str, *, session_id: str | None = None) -> dict[str, Any]:
    idx_file = Path(root) / ".beads" / "index.json"
    if not idx_file.exists():
        return {"ok": False, "error": "index_missing"}

    try:
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "index_read_failed"}

    beads = {str(k): dict(v) for k, v in ((idx.get("beads") or {}).items()) if isinstance(v, dict)}
    assocs = [a for a in (idx.get("associations") or []) if isinstance(a, dict)]

    if session_id:
        sid = str(session_id)
        scope_ids = {bid for bid, b in beads.items() if str(b.get("session_id") or "") == sid}
        scoped = []
        for a in assocs:
            s = str(a.get("source_bead") or a.get("source_bead_id") or "")
            t = str(a.get("target_bead") or a.get("target_bead_id") or "")
            if s in scope_ids or t in scope_ids:
                scoped.append(a)
        assocs = scoped
        beads = {bid: b for bid, b in beads.items() if bid in scope_ids}

    rel = Counter()
    status = Counter()
    deg = defaultdict(int)
    for a in assocs:
        r = str(a.get("relationship") or "").strip().lower() or "unknown"
        st = str(a.get("status") or "active").strip().lower() or "active"
        rel[r] += 1
        status[st] += 1
        if st in {"retracted", "superseded", "inactive"}:
            continue
        s = str(a.get("source_bead") or a.get("source_bead_id") or "")
        t = str(a.get("target_bead") or a.get("target_bead_id") or "")
        if s:
            deg[s] += 1
        if t:
            deg[t] += 1

    active_assocs = int(sum(v for k, v in status.items() if k not in {"retracted", "superseded", "inactive"}))
    isolated = sum(1 for bid in beads if deg.get(bid, 0) == 0)

    noise_rels = {"shared_tag", "follows", "precedes"}
    active_noise = sum(v for k, v in rel.items() if k in noise_rels)

    return {
        "ok": True,
        "session_id": str(session_id or "") or None,
        "beads": len(beads),
        "associations_total": len(assocs),
        "associations_active": active_assocs,
        "status_distribution": dict(status),
        "relationship_top": rel.most_common(20),
        "isolated_beads": isolated,
        "isolated_pct": round((isolated / max(1, len(beads))) * 100.0, 2),
        "active_noise_pct": round((active_noise / max(1, active_assocs)) * 100.0, 2),
    }
