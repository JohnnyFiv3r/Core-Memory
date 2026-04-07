from __future__ import annotations

import re
from typing import Any, Optional

from core_memory.runtime.turn_archive import find_turn_record


def resolve_bead_session_id_for_store(
    store: Any,
    *,
    session_id: Optional[str],
    source_turn_ids: Optional[list],
) -> str:
    sid = str(session_id or "").strip()
    if sid:
        return sid

    mode = str(store.bead_session_id_mode or "infer").strip().lower()
    if mode not in {"infer", "strict", "unknown"}:
        mode = "infer"

    inferred = ""
    if mode in {"infer", "strict"}:
        for tid in (source_turn_ids or []):
            t = str(tid or "").strip()
            if not t:
                continue
            row = find_turn_record(root=store.root, turn_id=t, session_id=None)
            if isinstance(row, dict):
                inferred = str(row.get("session_id") or "").strip()
                if inferred:
                    break

    if inferred:
        return inferred

    if mode == "strict":
        raise ValueError("missing:session_id (strict mode; unable to infer from source_turn_ids)")

    return "unknown"


def title_tokens_for_store(store: Any, text: str) -> set[str]:
    return {t for t in store._tokenize(text) if t not in {"the", "and", "for", "with", "this", "that"}}


def is_contradictory_decision(a_title: str, b_title: str) -> bool:
    a = (a_title or "").lower()
    b = (b_title or "").lower()
    neg = [" not ", " don't ", " never ", " avoid ", " disable ", " remove "]
    a_neg = any(x in f" {a} " for x in neg)
    b_neg = any(x in f" {b} " for x in neg)
    if a_neg != b_neg:
        return True
    antonym_pairs = [("enable", "disable"), ("use", "avoid"), ("allow", "deny")]
    for p, q in antonym_pairs:
        if (p in a and q in b) or (q in a and p in b):
            return True
    return False


def detect_decision_conflicts_for_store(store: Any, index: dict, bead: dict) -> tuple[int, int, list[str]]:
    """Heuristic conflict detector for new decision bead.

    Returns: (decision_conflicts, unjustified_flips, conflicting_bead_ids)
    """
    if bead.get("type") != "decision":
        return 0, 0, []

    new_tokens = title_tokens_for_store(store, bead.get("title", ""))
    if not new_tokens:
        return 0, 0, []

    conflicts = []
    assocs = index.get("associations", [])

    for prior in index.get("beads", {}).values():
        if prior.get("id") == bead.get("id"):
            continue
        if prior.get("type") != "decision":
            continue
        overlap = len(new_tokens.intersection(title_tokens_for_store(store, prior.get("title", ""))))
        if overlap < 2:
            continue
        if not is_contradictory_decision(bead.get("title", ""), prior.get("title", "")):
            continue

        prior_id = prior.get("id")
        justified = (prior.get("status") == "superseded") or any(
            (
                (a.get("source_bead") == prior_id or a.get("target_bead") == prior_id)
                and a.get("relationship") in {"supersedes", "reversal", "reversed_by"}
            )
            for a in assocs
        )
        if not justified:
            conflicts.append(prior_id)

    if not conflicts:
        return 0, 0, []
    return len(conflicts), 1, sorted(conflicts)


def norm_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def bead_similarity(a: dict, b: dict) -> float:
    ta = norm_text((a.get("title") or "") + " " + " ".join(a.get("summary") or []))
    tb = norm_text((b.get("title") or "") + " " + " ".join(b.get("summary") or []))
    sa = set(ta.split())
    sb = set(tb.split())
    if not sa or not sb:
        return 0.0
    inter = len(sa.intersection(sb))
    union = len(sa.union(sb))
    return float(inter) / float(max(1, union))


def find_recent_duplicate_bead_id_for_store(
    store: Any,
    index: dict,
    bead: dict,
    *,
    session_id: str | None,
    window: int = 25,
) -> str | None:
    beads = list((index.get("beads") or {}).values())
    if session_id:
        beads = [b for b in beads if str((b or {}).get("session_id") or "") == str(session_id)]
    beads = sorted(beads, key=lambda b: str((b or {}).get("created_at") or ""), reverse=True)[: max(1, int(window))]

    for prior in beads:
        if str(prior.get("type") or "") != str(bead.get("type") or ""):
            continue
        a_turns = {str(x) for x in (bead.get("source_turn_ids") or []) if str(x)}
        p_turns = {str(x) for x in (prior.get("source_turn_ids") or []) if str(x)}
        if a_turns and p_turns and a_turns.intersection(p_turns):
            return str(prior.get("id") or "") or None
        sim = bead_similarity(bead, prior)
        if sim >= 0.9:
            return str(prior.get("id") or "") or None
    return None


__all__ = [
    "resolve_bead_session_id_for_store",
    "title_tokens_for_store",
    "is_contradictory_decision",
    "detect_decision_conflicts_for_store",
    "norm_text",
    "bead_similarity",
    "find_recent_duplicate_bead_id_for_store",
]
