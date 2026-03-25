from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..persistence.io_utils import append_jsonl, atomic_write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"^\s*\[\[\s*reply_to_current\s*\]\]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def _infer_type(cur_type: str, text: str) -> str | None:
    t = (text or "").lower()
    if cur_type != "context":
        return None
    if re.search(r"\b(decision|decided|policy reset|rationale|candidate-only|agent-authoritative)\b", t):
        return "decision"
    if re.search(r"\b(evidence|metrics|kpi|measured|observed|inflation|score)\b", t):
        return "evidence"
    if re.search(r"\b(outcome|result|completed|after|now)\b", t):
        return "outcome"
    if re.search(r"\b(lesson|learned|takeaway)\b", t):
        return "lesson"
    return None


def curated_type_title_hygiene(root: Path, bead_ids: list[str], *, apply: bool = False) -> dict:
    idx_file = root / ".beads" / "index.json"
    if not idx_file.exists():
        return {"ok": False, "error": "index_missing"}
    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    beads = idx.get("beads") or {}

    changes = []
    for bid in sorted(set([str(x) for x in bead_ids if str(x)])):
        b = beads.get(bid)
        if not b:
            continue
        old_title = str(b.get("title") or "")
        new_title = _clean_title(old_title)
        if not new_title:
            s = " ".join(b.get("summary") or []).strip()
            new_title = _clean_title(s[:120]) if s else old_title

        old_type = str(b.get("type") or "")
        text = " ".join([old_title, " ".join(b.get("summary") or [])])
        new_type = _infer_type(old_type, text)

        if new_title != old_title or (new_type and new_type != old_type):
            changes.append(
                {
                    "bead_id": bid,
                    "old_title": old_title,
                    "new_title": new_title,
                    "old_type": old_type,
                    "new_type": new_type or old_type,
                }
            )

    if apply and changes:
        for ch in changes:
            b = beads.get(ch["bead_id"]) or {}
            b["title"] = ch["new_title"]
            if ch["new_type"] != ch["old_type"]:
                b["type"] = ch["new_type"]
            b.setdefault("tags", [])
            for tag in ["hygiene_curated"]:
                if tag not in b["tags"]:
                    b["tags"].append(tag)

        atomic_write_json(idx_file, idx)

        ev = root / ".beads" / "events" / "type-title-hygiene.jsonl"
        for ch in changes:
            append_jsonl(
                ev,
                {
                    "event": "type_title_hygiene",
                    "at": _now(),
                    **ch,
                },
            )

    return {"ok": True, "apply": bool(apply), "changes": len(changes), "sample": changes[:50]}


# === Content sanitization (moved from store.py) ===

def _redact_text(text: str) -> str:
    """Conservative secret redaction for high-confidence credential patterns only."""
    if not text:
        return text

    patterns = [
        (r"github_pat_[A-Za-z0-9_]{20,}", "github_pat"),
        (r"ghp_[A-Za-z0-9]{20,}", "github_pat_classic"),
        (r"x-access-token:[^\s@]{12,}", "x_access_token"),
        (r"AKIA[0-9A-Z]{16}", "aws_access_key_id"),
        (r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b", "bearer_token"),
    ]

    redacted = text
    for pattern, kind in patterns:
        def repl(m):
            h = hashlib.sha256(m.group(0).encode("utf-8")).hexdigest()[:10]
            return f"[REDACTED_SECRET:{kind}:{h}]"
        redacted = re.sub(pattern, repl, redacted)

    return redacted


def sanitize_bead_content(bead: dict) -> dict:
    """Apply content redaction to a bead's text fields."""
    bead["title"] = _redact_text(bead.get("title", ""))
    bead["detail"] = _redact_text(bead.get("detail", ""))
    bead["summary"] = [_redact_text(str(s)) for s in (bead.get("summary") or [])]
    bead["because"] = [_redact_text(str(s)) for s in (bead.get("because") or [])]
    return bead


def extract_constraints(text: str) -> list[str]:
    """Deterministic, conservative constraint extraction.

    Cleanup pass: avoid noisy matches from markdown fragments, ids, and
    accidental snippets. Prefer explicit policy language only.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    # Keep only plain-ish sentence segments.
    segments = [s.strip() for s in re.split(r"[\n.;]+", raw) if s.strip()]
    cue_re = re.compile(r"\b(must(?:\s+not)?|never|do\s+not|avoid|requires?)\b", re.IGNORECASE)

    out: list[str] = []
    seen = set()
    for seg in segments:
        s = re.sub(r"`[^`]*`", " ", seg)  # drop code-ish inline blocks
        s = re.sub(r"\[\[.*?\]\]", " ", s)  # drop reply tags
        s = re.sub(r"\s+", " ", s).strip(" -:\t")
        if not s:
            continue
        if len(s) < 12 or len(s) > 180:
            continue
        if not cue_re.search(s):
            continue

        # Remove obvious non-policy fragments.
        low = s.lower()
        banned = ["http://", "https://", "core_memory/", "--", "commit", "bead-"]
        if any(b in low for b in banned):
            continue

        # Normalize and bound token count to keep concise constraints.
        toks = re.findall(r"[a-z0-9_\-]+", low)
        if len(toks) < 3 or len(toks) > 20:
            continue
        normalized = " ".join(toks)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)

    return out[:8]


# === Retrieval-richness hygiene contract ===

GENERIC_TITLE_PATTERNS = [
    r"^turn memory$",
    r"^reply$",
    r"^update$",
    r"^current turn$",
    r"^bridge[_\s-]*ok$",
    r"^acknowledged$",
    r"^done$",
    r"^ok$",
]


RUNTIME_META_PATTERNS = [
    r"openclaw runtime context",
    r"inter-session message",
    r"subagent_announce",
    r"reply_skip",
    r"\bbridge[_\s-]*ok\b",
]


def is_generic_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return True
    return any(re.search(p, t, re.IGNORECASE) for p in GENERIC_TITLE_PATTERNS)


def rewrite_generic_title(title: str) -> str:
    t = _clean_title(title)
    if not is_generic_title(t):
        return t
    if re.search(r"bridge[_\s-]*ok", t or "", re.IGNORECASE):
        return "bridge acknowledgement"
    return "assistant turn"


def is_runtime_meta_chatter(user_query: str = "", assistant_final: str = "") -> bool:
    txt = f"{user_query} {assistant_final}".lower()
    return any(re.search(p, txt, re.IGNORECASE) for p in RUNTIME_META_PATTERNS)


def extract_entities(text: str) -> list[str]:
    txt = (text or "")
    # deterministic lightweight extraction from canonical-ish tokens
    cands = set(re.findall(r"\b[A-Za-z][A-Za-z0-9_.:/-]{2,}\b", txt))
    out = []
    for c in sorted(cands):
        if c.lower() in {"the", "and", "with", "from", "this", "that"}:
            continue
        if c.startswith("bead-"):
            continue
        out.append(c)
    return out[:20]


def extract_state_change(text: str) -> Optional[dict]:
    t = (text or "")
    m = re.search(r"(?:changed|switched|migrated|replaced)\s+from\s+(.+?)\s+to\s+(.+?)(?:[\.;\n]|$)", t, re.IGNORECASE)
    if not m:
        return None
    return {"from": m.group(1).strip(), "to": m.group(2).strip()}


def extract_validity(text: str) -> Optional[str]:
    t = (text or "").lower()
    if any(k in t for k in ["superseded", "replaced by", "deprecated"]):
        return "superseded"
    if any(k in t for k in ["closed", "resolved"]):
        return "closed"
    if any(k in t for k in ["transient", "temporary"]):
        return "transient"
    if t.strip():
        return "open"
    return None


def _information_signals(bead: dict) -> dict:
    title = str(bead.get("title") or "")
    summary = " ".join(bead.get("summary") or [])
    detail = str(bead.get("detail") or "")
    because = bead.get("because") or []
    supporting = bead.get("supporting_facts") or []
    evidence_refs = bead.get("evidence_refs") or []
    state_change = bead.get("state_change") or extract_state_change(f"{title} {summary} {detail}")
    entities = bead.get("entities") or extract_entities(f"{title} {summary} {detail}")
    validity = bead.get("validity") or extract_validity(f"{title} {summary} {detail}")

    return {
        "has_entities": bool(entities),
        "has_state_change": bool(state_change),
        "has_because": bool(because),
        "has_supporting": bool(supporting),
        "has_evidence_refs": bool(evidence_refs),
        "has_validity": bool(validity),
        "is_runtime_meta": is_runtime_meta_chatter(str(bead.get("user_query") or ""), f"{title} {summary} {detail}"),
        "is_generic_title": is_generic_title(title),
    }


def classify_bead_richness(bead: dict) -> str:
    """Classify write richness as LOW or NORMAL.

    LOW: temporal continuity bead (usually non-retrieval).
    NORMAL: retrieval-rich bead candidate.
    """
    s = _information_signals(bead)
    positives = sum(1 for k in ["has_entities", "has_state_change", "has_because", "has_supporting", "has_evidence_refs", "has_validity"] if s.get(k))
    if s.get("is_runtime_meta"):
        return "LOW"
    if positives >= 2 and not s.get("is_generic_title"):
        return "NORMAL"
    return "LOW"


def can_be_retrieval_eligible(bead: dict) -> bool:
    title_ok = not is_generic_title(str(bead.get("title") or ""))
    retrieval_title_ok = bool(str(bead.get("retrieval_title") or "").strip())
    facts_ok = bool(bead.get("retrieval_facts"))
    quality_signal = any([
        bool(bead.get("because")),
        bool(bead.get("supporting_facts")),
        bool(bead.get("state_change")),
        bool(bead.get("evidence_refs")),
        bool(bead.get("supersedes")),
        bool(bead.get("superseded_by")),
    ])
    return bool(title_ok and retrieval_title_ok and facts_ok and quality_signal)


def enforce_bead_hygiene_contract(bead: dict) -> dict:
    """Normalize bead to thin/rich hygiene contract without rejecting thin beads."""
    out = dict(bead or {})
    out["title"] = rewrite_generic_title(str(out.get("title") or ""))

    # summary is optional by contract
    out.setdefault("summary", [])

    # ensure temporal minimum surface
    out.setdefault("session_id", out.get("session_id"))
    out.setdefault("source_turn_ids", out.get("source_turn_ids") or [])
    if out.get("prev_bead_id") is None:
        out["prev_bead_id"] = out.get("prev_bead_id")

    richness = classify_bead_richness(out)
    out["bead_richness"] = richness

    if richness == "LOW":
        out["retrieval_eligible"] = False
        return out

    # NORMAL path: allow eligibility only when payload quality passes.
    out["retrieval_eligible"] = bool(out.get("retrieval_eligible", True)) and can_be_retrieval_eligible(out)
    return out
