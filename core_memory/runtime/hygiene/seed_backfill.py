"""Read-only compatibility census for the retired seed-quality backfill.

The original one-shot pass directly rewrote stored bead fields, rebuilt the
entity registry, and auto-applied Dreamer outcomes.  Those mutations conflict
with the agent-led, append-only semantic maintenance contract.  The public HTTP
route remains readable for one compatibility window, but ``apply=True`` is now
rejected with migration guidance to governed ``reauthor_memory``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import store_lock

SEED_BACKFILL_TAG = "seed_backfilled"
SEED_BACKFILL_APPLY_RETIRED = "seed_quality_backfill_apply_retired"
SEED_BACKFILL_REPLACEMENT = "maintain(action='reauthor_memory')"
_INACTIVE_STATUSES = {"superseded", "archived"}

_GENERIC_ENTITY_STOPWORDS = {
    "test",
    "tests",
    "testing",
    "spec",
    "specs",
    "fixture",
    "fixtures",
    "doc",
    "docs",
    "documentation",
    "readme",
    "changelog",
    "license",
    "src",
    "lib",
    "libs",
    "app",
    "apps",
    "api",
    "apis",
    "core",
    "sdk",
    "util",
    "utils",
    "helper",
    "helpers",
    "common",
    "shared",
    "misc",
    "main",
    "master",
    "dev",
    "develop",
    "staging",
    "prod",
    "production",
    "build",
    "builds",
    "config",
    "configs",
    "configuration",
    "settings",
    "setup",
    "script",
    "scripts",
    "asset",
    "assets",
    "public",
    "static",
    "dist",
    "package",
    "packages",
    "module",
    "modules",
    "index",
    "temp",
    "tmp",
    "cache",
    "backup",
    "archive",
    "log",
    "logs",
    "debug",
    "data",
    "file",
    "files",
    "folder",
    "folders",
    "directory",
    "document",
    "documents",
    "item",
    "items",
    "record",
    "records",
    "event",
    "events",
    "message",
    "messages",
    "note",
    "notes",
    "info",
    "information",
    "content",
    "text",
    "detail",
    "details",
    "general",
    "other",
    "others",
    "unknown",
    "untitled",
    "default",
    "example",
    "examples",
    "sample",
    "samples",
    "demo",
    "user",
    "users",
    "admin",
    "account",
    "accounts",
    "name",
    "title",
    "label",
    "value",
    "type",
    "status",
    "session",
    "memory",
    "turn",
    "context",
    "summary",
    "update",
    "updates",
    "reply",
    "follow",
    "follows",
    "following",
    "precede",
    "precedes",
    "supersede",
    "supersedes",
    "create",
    "created",
    "delete",
    "deleted",
    "add",
    "added",
    "remove",
    "removed",
    "fix",
    "fixed",
    "change",
    "changes",
    "changed",
    "please",
    "thanks",
    "okay",
    "yes",
    "no",
    "none",
    "null",
    "undefined",
    "true",
    "false",
    "should",
    "would",
    "could",
    "will",
    "have",
    "has",
    "about",
    "before",
    "after",
    "because",
    "there",
    "here",
    "when",
    "where",
    "what",
    "which",
    "who",
    "why",
    "how",
    "this",
    "that",
    "these",
    "those",
    "with",
    "from",
    "into",
    "your",
    "their",
}

_URL_RE = re.compile(r"^(https?://|www\.)", re.I)
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_HASH_RE = re.compile(r"^[0-9a-f]{7,64}$", re.I)
_NUMBER_VERSION_RE = re.compile(r"^v?\d+([._-]\d+)*$", re.I)
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?")
_FILE_NAME_RE = re.compile(r"^\S+\.[a-z0-9]{1,5}$", re.I)
_STRUCTURAL_TITLE_RE = re.compile(r"^document section \d+", re.I)


def is_meaningful_entity(value: str) -> bool:
    """Return whether a legacy entity label is meaningful enough to review."""

    label = " ".join(str(value or "").split())
    if len(label) < 2 or len(label) > 96 or "/" in label or "\\" in label:
        return False
    if (
        _URL_RE.match(label)
        or _EMAIL_RE.search(label)
        or _UUID_RE.match(label)
        or _HEX_HASH_RE.match(label)
        or _NUMBER_VERSION_RE.match(label)
        or _DATE_RE.match(label)
    ):
        return False
    if len(label.split(" ")) == 1:
        lower = label.lower()
        if lower in _GENERIC_ENTITY_STOPWORDS or _FILE_NAME_RE.match(label):
            return False
        if label == lower and label.isalpha():
            return False
        if label == lower and len(label) < 4 and not any(ch.isdigit() for ch in label):
            return False
    letters = len(re.findall(r"[^\W\d_]", label, re.UNICODE))
    return letters / max(1, len(label.replace(" ", ""))) >= 0.5


def clean_entity_list(values: list[Any], *, limit: int = 16) -> list[str]:
    """Return the legacy cleanup proposal without modifying stored beads."""

    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        label = " ".join(str(raw or "").split())
        key = label.lower()
        if not label or key in seen or not is_meaningful_entity(label):
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= limit:
            break
    return out


def _summary_text(bead: dict[str, Any]) -> str:
    summary = bead.get("summary")
    if isinstance(summary, list):
        return " ".join(str(value) for value in summary)
    return str(summary or "")


def _title_is_structural(bead: dict[str, Any]) -> bool:
    title = " ".join(str(bead.get("title") or "").split())
    document_name = " ".join(str(bead.get("document_name") or "").split())
    return bool(
        not title
        or (document_name and (title == document_name or title.startswith(f"{document_name} (part")))
        or _STRUCTURAL_TITLE_RE.match(title)
        or _FILE_NAME_RE.match(title)
    )


def _summary_is_truncation(bead: dict[str, Any]) -> bool:
    raw = bead.get("summary")
    summary = [str(value).strip() for value in raw if str(value).strip()] if isinstance(raw, list) else []
    if not summary:
        return True
    if len(summary) != 1:
        return False
    only = " ".join(summary[0].split())
    detail = " ".join(str(bead.get("detail") or "").split())
    title = " ".join(str(bead.get("title") or "").split())
    return only == title or bool(detail and len(only) >= 40 and detail.startswith(only[: len(only) - 1]))


def _is_thin(bead: dict[str, Any]) -> bool:
    if str(bead.get("status") or "").strip().lower() in _INACTIVE_STATUSES:
        return False
    if str(bead.get("type") or "").strip().lower() in {"goal", "proposed_theme"}:
        return False
    if SEED_BACKFILL_TAG in [str(value) for value in (bead.get("tags") or [])]:
        return False
    grounding = " ".join([str(bead.get("title") or ""), _summary_text(bead), str(bead.get("detail") or "")]).strip()
    if len(grounding) < 24:
        return False
    return not list(bead.get("entities") or []) or _title_is_structural(bead) or _summary_is_truncation(bead)


def run_seed_quality_backfill(
    root: str | Path,
    *,
    apply: bool = False,
    max_enrich: int = 150,
    max_storylines: int = 12,
    max_goals: int = 5,
    reviewer: str = "seed_backfill",
) -> dict[str, Any]:
    """Return the legacy census and reject the retired mutating behavior."""

    del max_storylines, max_goals, reviewer
    root_path = Path(root)
    index_path = root_path / ".beads" / "index.json"
    report: dict[str, Any] = {
        "ok": True,
        "applied": False,
        "deprecated": True,
        "replacement": SEED_BACKFILL_REPLACEMENT,
    }
    if not index_path.exists():
        return {**report, "ok": False, "error": "index_missing"}
    with store_lock(root_path):
        try:
            snapshot = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - compatibility census receipt
            return {**report, "ok": False, "error": f"index_unreadable:{exc}"}
    if not isinstance(snapshot, dict):
        return {**report, "ok": False, "error": "index_not_object"}

    beads = snapshot.get("beads") if isinstance(snapshot.get("beads"), dict) else {}
    touched = 0
    removed = 0
    samples: list[str] = []
    for bead in beads.values():
        if not isinstance(bead, dict):
            continue
        original = [str(value) for value in (bead.get("entities") or []) if str(value).strip()]
        cleaned = clean_entity_list(original)
        if cleaned == original:
            continue
        touched += 1
        for value in original:
            if value in cleaned:
                continue
            removed += 1
            if len(samples) < 12 and value not in samples:
                samples.append(value)
    thin_ids = [str(bead_id) for bead_id, bead in beads.items() if isinstance(bead, dict) and _is_thin(bead)]
    report.update(
        {
            "entities": {
                "beads_scanned": len(beads),
                "beads_touched": touched,
                "entities_removed": removed,
                "removed_samples": samples,
            },
            "enrichment": {
                "eligible": len(thin_ids),
                "selected": thin_ids[: max(0, int(max_enrich))],
                "attempted": 0,
                "changed": 0,
                "failed": 0,
            },
            "registry": {
                "entities_before": len(snapshot.get("entities") or {}),
                "entities_after": None,
                "note": "read_only_census",
            },
        }
    )
    if apply:
        report.update(
            {
                "ok": False,
                "error": SEED_BACKFILL_APPLY_RETIRED,
                "migration": (
                    "Run maintain(action='reauthor_memory') as a dry run, apply it to a copied tenant, "
                    "then supply that successful receipt before live-tenant apply."
                ),
            }
        )
    return report


__all__ = [
    "SEED_BACKFILL_APPLY_RETIRED",
    "SEED_BACKFILL_REPLACEMENT",
    "SEED_BACKFILL_TAG",
    "clean_entity_list",
    "is_meaningful_entity",
    "run_seed_quality_backfill",
]
