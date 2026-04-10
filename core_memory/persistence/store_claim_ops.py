"""
Claim store operations — read/write claims on beads and resolve current state.
All operations are append-only. ClaimUpdates govern supersession/retraction.
"""
import json
import os
from pathlib import Path
from typing import Optional

from core_memory.schema.models import Claim, ClaimUpdate


def _bead_dir(root: str, bead_id: str) -> Path:
    return Path(root) / bead_id


def _claims_path(root: str, bead_id: str) -> Path:
    return _bead_dir(root, bead_id) / "claims.json"


def _claim_updates_path(root: str, bead_id: str) -> Path:
    return _bead_dir(root, bead_id) / "claim_updates.json"


def write_claims_to_bead(root: str, bead_id: str, claims: list[dict]) -> None:
    """Append claims to a bead's claims.json (atomic write)."""
    path = _claims_path(root, bead_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = []
    if path.exists():
        with open(path) as f:
            existing = json.load(f)

    existing.extend(claims)

    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(existing, f, indent=2)
    tmp.rename(path)


def write_claim_updates_to_bead(root: str, bead_id: str, claim_updates: list[dict]) -> None:
    """Append claim updates to a bead's claim_updates.json (atomic write)."""
    path = _claim_updates_path(root, bead_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = []
    if path.exists():
        with open(path) as f:
            existing = json.load(f)

    existing.extend(claim_updates)

    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(existing, f, indent=2)
    tmp.rename(path)


def read_claims_for_bead(root: str, bead_id: str) -> list[dict]:
    """Read all claims for a bead."""
    path = _claims_path(root, bead_id)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def read_claim_updates_for_bead(root: str, bead_id: str) -> list[dict]:
    """Read all claim updates for a bead."""
    path = _claim_updates_path(root, bead_id)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def resolve_current_state(root: str, subject: str, slot: str) -> dict:
    """
    Resolve the current state for a subject+slot by scanning all bead claim files.
    Applies ClaimUpdates (supersede/retract/reaffirm/conflict) in order.
    Returns: {current_claim, history, conflicts, status}
    """
    root_path = Path(root)
    all_claims = []
    all_updates = []

    # Scan all bead directories
    if root_path.exists():
        for bead_dir in sorted(root_path.iterdir()):
            if not bead_dir.is_dir():
                continue

            claims_file = bead_dir / "claims.json"
            if claims_file.exists():
                with open(claims_file) as f:
                    beads_claims = json.load(f)
                for c in beads_claims:
                    if c.get("subject") == subject and c.get("slot") == slot:
                        all_claims.append(c)

            updates_file = bead_dir / "claim_updates.json"
            if updates_file.exists():
                with open(updates_file) as f:
                    bead_updates = json.load(f)
                for u in bead_updates:
                    if u.get("subject") == subject and u.get("slot") == slot:
                        all_updates.append(u)

    if not all_claims:
        return {"current_claim": None, "history": [], "conflicts": [], "status": "not_found"}

    # Apply updates to determine current state
    retracted_ids = set()
    superseded_ids = set()
    conflict_ids = set()

    for update in all_updates:
        decision = update.get("decision", "")
        target_id = update.get("target_claim_id")

        if decision == "retract" and target_id:
            retracted_ids.add(target_id)
        elif decision == "supersede" and target_id:
            superseded_ids.add(target_id)
        elif decision == "conflict" and target_id:
            conflict_ids.add(target_id)

    # Current = last claim not retracted or superseded
    active_claims = [
        c for c in all_claims
        if c.get("id") not in retracted_ids and c.get("id") not in superseded_ids
    ]
    conflicts = [c for c in all_claims if c.get("id") in conflict_ids]

    current = active_claims[-1] if active_claims else None
    status = "active" if current else "retracted"
    if conflicts:
        status = "conflict"

    return {
        "current_claim": current,
        "history": all_claims,
        "conflicts": conflicts,
        "status": status,
    }
