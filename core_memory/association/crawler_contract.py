from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.io_utils import store_lock
from core_memory.session_surface import read_session_surface


def build_crawler_context(root: str, session_id: str, limit: int = 200) -> dict[str, Any]:
    """Provide session-scoped bead context for agent-judged crawler decisions."""
    rows = read_session_surface(root, session_id)
    rows = rows[-max(1, int(limit)) :]
    return {
        "session_id": session_id,
        "beads": rows,
        "allowed_updates": {
            "promotions": "list[bead_id]",
            "associations": "list[{source_bead_id,target_bead_id,relationship}]",
        },
        "append_only_rules": [
            "promotion_marked can only be set true",
            "associations are append-only records",
        ],
    }


def apply_crawler_updates(root: str, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Apply append-only crawler updates from agent judgments."""
    idx_file = Path(root) / ".beads" / "index.json"
    with store_lock(Path(root)):
        if not idx_file.exists():
            return {"ok": False, "error": "index_missing"}
        index = json.loads(idx_file.read_text(encoding="utf-8"))
        beads = index.get("beads") or {}
        assoc = list(index.get("associations") or [])

        session_bead_ids = {
            str((r or {}).get("id") or "")
            for r in read_session_surface(root, session_id)
            if str((r or {}).get("id") or "")
        }

        promoted = 0
        for bid in updates.get("promotions") or []:
            b = beads.get(str(bid))
            if not b or str(b.get("session_id") or "") != str(session_id) or str(bid) not in session_bead_ids:
                continue
            if not b.get("promotion_marked"):
                b["promotion_marked"] = True
                b["promotion_marked_at"] = datetime.now(timezone.utc).isoformat()
                beads[str(bid)] = b
                promoted += 1

        appended = 0
        for row in updates.get("associations") or []:
            if not isinstance(row, dict):
                continue
            src = str(row.get("source_bead_id") or "")
            tgt = str(row.get("target_bead_id") or "")
            rel = str(row.get("relationship") or "").strip()
            if not src or not tgt or not rel:
                continue
            sb = beads.get(src)
            tb = beads.get(tgt)
            if not sb or not tb:
                continue
            if str(sb.get("session_id") or "") != str(session_id):
                continue
            if src not in session_bead_ids:
                continue
            exists = any(
                a.get("source_bead") == src and a.get("target_bead") == tgt and a.get("relationship") == rel
                for a in assoc
            )
            if exists:
                continue
            assoc.append(
                {
                    "id": f"assoc-{uuid.uuid4().hex[:12].upper()}",
                    "type": "association",
                    "source_bead": src,
                    "target_bead": tgt,
                    "relationship": rel,
                    "edge_class": "agent_judged",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            appended += 1

        index["beads"] = beads
        index["associations"] = sorted(assoc, key=lambda a: (a.get("created_at", ""), a.get("id", "")))
        index.setdefault("stats", {})["total_associations"] = len(index["associations"])
        idx_file.write_text(json.dumps(index, indent=2), encoding="utf-8")

    return {"ok": True, "promotions_marked": promoted, "associations_appended": appended}
