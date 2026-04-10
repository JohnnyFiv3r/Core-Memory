"""
Claim store operations — canonical bead-embedded claim storage and resolution.

Canonical source of truth:
- .beads/index.json -> beads[bead_id].claims
- .beads/index.json -> beads[bead_id].claim_updates

Legacy compatibility:
- read fallback from sidecar files under <root>/<bead_id>/claims.json and
  <root>/<bead_id>/claim_updates.json (no canonical writes to sidecars).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.schema.models import Claim, ClaimUpdate


def _index_path(root: str) -> Path:
    return Path(root) / ".beads" / "index.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_index(root: str) -> dict:
    idx = _read_json(_index_path(root), default={})
    if not isinstance(idx, dict):
        idx = {}
    beads = idx.get("beads")
    if not isinstance(beads, dict):
        idx["beads"] = {}
    idx.setdefault("associations", [])
    return idx


def _ensure_bead(index: dict, bead_id: str) -> dict:
    beads = index.setdefault("beads", {})
    row = beads.get(bead_id)
    if not isinstance(row, dict):
        row = {"id": bead_id}
    row.setdefault("id", bead_id)
    if not isinstance(row.get("claims"), list):
        row["claims"] = []
    if not isinstance(row.get("claim_updates"), list):
        row["claim_updates"] = []
    beads[bead_id] = row
    return row


def _legacy_claims_path(root: str, bead_id: str) -> Path:
    return Path(root) / bead_id / "claims.json"


def _legacy_claim_updates_path(root: str, bead_id: str) -> Path:
    return Path(root) / bead_id / "claim_updates.json"


def _normalize_claim_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            out.append(Claim.from_dict(r).to_dict())
        except Exception:
            continue
    return out


def _normalize_claim_update_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            out.append(ClaimUpdate.from_dict(r).to_dict())
        except Exception:
            continue
    return out


def _legacy_read_claims_for_bead(root: str, bead_id: str) -> list[dict]:
    path = _legacy_claims_path(root, bead_id)
    raw = _read_json(path, default=[])
    return raw if isinstance(raw, list) else []


def _legacy_read_claim_updates_for_bead(root: str, bead_id: str) -> list[dict]:
    path = _legacy_claim_updates_path(root, bead_id)
    raw = _read_json(path, default=[])
    return raw if isinstance(raw, list) else []


def write_claims_to_bead(root: str, bead_id: str, claims: list[dict]) -> None:
    """Append claims to a bead's canonical embedded claim list."""
    bead_id = str(bead_id or "").strip()
    if not bead_id:
        return

    idx = _read_index(root)
    row = _ensure_bead(idx, bead_id)

    normalized = _normalize_claim_rows(claims)
    row["claims"].extend(normalized)

    _write_json_atomic(_index_path(root), idx)


def write_claim_updates_to_bead(root: str, bead_id: str, claim_updates: list[dict]) -> None:
    """Append claim updates to a bead's canonical embedded claim_update list."""
    bead_id = str(bead_id or "").strip()
    if not bead_id:
        return

    idx = _read_index(root)
    row = _ensure_bead(idx, bead_id)

    normalized = _normalize_claim_update_rows(claim_updates)
    row["claim_updates"].extend(normalized)

    _write_json_atomic(_index_path(root), idx)


def read_claims_for_bead(root: str, bead_id: str) -> list[dict]:
    """Read all claims for a bead from canonical storage (with legacy fallback)."""
    bead_id = str(bead_id or "").strip()
    if not bead_id:
        return []

    idx = _read_index(root)
    row = (idx.get("beads") or {}).get(bead_id) or {}
    claims = row.get("claims")
    if isinstance(claims, list) and claims:
        return _normalize_claim_rows(claims)

    # legacy fallback
    return _normalize_claim_rows(_legacy_read_claims_for_bead(root, bead_id))


def read_claim_updates_for_bead(root: str, bead_id: str) -> list[dict]:
    """Read all claim updates for a bead from canonical storage (with legacy fallback)."""
    bead_id = str(bead_id or "").strip()
    if not bead_id:
        return []

    idx = _read_index(root)
    row = (idx.get("beads") or {}).get(bead_id) or {}
    updates = row.get("claim_updates")
    if isinstance(updates, list) and updates:
        return _normalize_claim_update_rows(updates)

    # legacy fallback
    return _normalize_claim_update_rows(_legacy_read_claim_updates_for_bead(root, bead_id))


def _all_claim_rows(root: str) -> tuple[list[dict], list[dict]]:
    idx = _read_index(root)
    beads = idx.get("beads") or {}

    all_claims: list[dict] = []
    all_updates: list[dict] = []

    for bead_id in sorted(beads.keys()):
        row = beads.get(bead_id) or {}
        all_claims.extend(_normalize_claim_rows(row.get("claims") or []))
        all_updates.extend(_normalize_claim_update_rows(row.get("claim_updates") or []))

    # legacy compatibility read (append-only fallback for existing sidecar data)
    root_path = Path(root)
    if root_path.exists():
        for entry in sorted(root_path.iterdir(), key=lambda p: p.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            all_claims.extend(_normalize_claim_rows(_legacy_read_claims_for_bead(root, entry.name)))
            all_updates.extend(_normalize_claim_update_rows(_legacy_read_claim_updates_for_bead(root, entry.name)))

    return all_claims, all_updates


def read_all_claim_rows(root: str) -> tuple[list[dict], list[dict]]:
    """Public helper for resolver surfaces to load all claim/update rows."""
    return _all_claim_rows(root)


def resolve_current_state(root: str, subject: str, slot: str) -> dict:
    """
    Resolve the current state for a subject+slot pair using claim history + claim updates.

    Returns: {current_claim, history, conflicts, status}
    """
    subject_s = str(subject or "")
    slot_s = str(slot or "")

    all_claims, all_updates = _all_claim_rows(root)
    slot_claims = [
        c for c in all_claims
        if str(c.get("subject") or "") == subject_s and str(c.get("slot") or "") == slot_s
    ]
    slot_updates = [
        u for u in all_updates
        if str(u.get("subject") or "") == subject_s and str(u.get("slot") or "") == slot_s
    ]

    if not slot_claims:
        return {"current_claim": None, "history": [], "conflicts": [], "status": "not_found"}

    retracted_ids = set()
    superseded_ids = set()
    conflict_ids = set()

    for update in slot_updates:
        decision = str(update.get("decision") or "")
        target_id = str(update.get("target_claim_id") or "")
        if not target_id:
            continue
        if decision == "retract":
            retracted_ids.add(target_id)
        elif decision == "supersede":
            superseded_ids.add(target_id)
        elif decision == "conflict":
            conflict_ids.add(target_id)

    active_claims = [
        c for c in slot_claims
        if str(c.get("id") or "") not in retracted_ids
        and str(c.get("id") or "") not in superseded_ids
    ]
    conflicts = [c for c in slot_claims if str(c.get("id") or "") in conflict_ids]

    current = active_claims[-1] if active_claims else None
    status = "active" if current else "retracted"
    if conflicts:
        status = "conflict"

    return {
        "current_claim": current,
        "history": slot_claims,
        "conflicts": conflicts,
        "status": status,
    }
