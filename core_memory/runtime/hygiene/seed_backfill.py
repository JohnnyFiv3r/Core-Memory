# ============================================================================
# SEED_BACKFILL_ONESHOT — TEMPORARY ONE-SHOT MIGRATION CODE. REMOVE AFTER RUN.
#
# Everything in core_memory/runtime/hygiene/ (this file + __init__.py), the
# /v1/memory/hygiene/seed-backfill route in integrations/http/server.py, and
# tests/test_seed_quality_backfill.py exist ONLY to clean a store populated
# before the bead→storyline→goal quality fixes. Once the backfill has run
# against every live store, delete all of it — leaving it in is tech debt.
# Find every removal point with:  grep -rIl "SEED_BACKFILL_ONESHOT"
# Removal checklist: docs/deployment/seed-quality-backfill-runbook.md#removal
# ============================================================================
"""Seed-quality backfill — one-shot cleanup of a store written before the
bead→storyline→goal quality fixes landed.

Stores populated by the old write paths carry three kinds of debt the new
paths no longer produce:

1. **Junk entities** — the retired all-words heuristics promoted lowercase
   sentence fragments, paths, hashes, and file names to entities. Those junk
   entities are junk worldline labels and junk storyline backbones forever,
   because worldlines re-derive from bead fields on every read.
2. **Structural beads** — document/section and fallback turn beads with a
   file name or first-line title, a truncated-prefix summary, and no
   entities/topics at all.
3. **Unrealised interpretation** — convergence groups and latent goals that
   were never named (or, for goals, could never be applied), so the manifold
   renders bare entity threads.

This pass is explicitly best-effort and operator-invoked ("reviewer" is the
operator running it). It:

- **triages** each bead cheaply and deterministically — is it thin (structural
  title / truncated summary / no entities) or entity-dirty (any entity that
  looks like an extraction fragment, path, hash, or file name)? — then
  **re-authors every flagged bead with the LLM** (standard-tier bead-field
  judge), which reads the bead's own content and authors the definitive
  title/summary/entities/topics. The regex rules only *route* beads to the
  model and *guard the model's own output*; they never decide the survivors
  of a bead's entity list. When the model is unavailable a bead is **deferred**
  (left untouched, reported), not silently pruned by regex — judging is the
  LLM's job, so a model outage means "try again later," not "let the script
  decide";
- rebuilds the entity registry from the re-authored fields (orphaned junk
  entities disappear as beads are re-authored);
- enqueues + refines narrative/goal candidates and auto-accepts the refined
  ones (capped), materialising named storyline overlays and real Goal Beads;
- marks the semantic index dirty and rebuilds the geometry manifest so the
  manifold reflects the cleaned store immediately.

Safety: ``apply=False`` (default) reports what would change without writing
and without model calls. ``apply=True`` snapshots ``index.json`` (and the
overlay log) to a timestamped per-run backup plus a stable pristine baseline
for a full rollback across batched reruns. Every stage is fail-open and
individually counted in the report; a model outage defers work, never
corrupts the store and never lets the deterministic layer author meaning.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.entity_registry import (
    ensure_entity_registry_for_index,
    sync_bead_entities_for_index,
)
from core_memory.persistence.io_utils import atomic_write_json, store_lock

logger = logging.getLogger(__name__)

SEED_BACKFILL_TAG = "seed_backfilled"
_INACTIVE_STATUSES = {"superseded", "archived"}

# ── Entity quality (mirrors the presentation-layer rules so cleaned labels
#    survive host curation) ────────────────────────────────────────────────

_GENERIC_ENTITY_STOPWORDS = {
    # dev/repo artifacts
    "test", "tests", "testing", "spec", "specs", "fixture", "fixtures",
    "doc", "docs", "documentation", "readme", "changelog", "license",
    "src", "lib", "libs", "app", "apps", "api", "apis", "core", "sdk",
    "util", "utils", "helper", "helpers", "common", "shared", "misc",
    "main", "master", "dev", "develop", "staging", "prod", "production",
    "build", "builds", "config", "configs", "configuration", "settings",
    "setup", "script", "scripts", "asset", "assets", "public", "static",
    "dist", "package", "packages", "module", "modules", "index", "temp",
    "tmp", "cache", "backup", "archive", "log", "logs", "debug",
    # generic nouns
    "data", "file", "files", "folder", "folders", "directory", "document",
    "documents", "item", "items", "record", "records", "event", "events",
    "message", "messages", "note", "notes", "info", "information",
    "content", "text", "detail", "details", "general", "other", "others",
    "unknown", "untitled", "default", "example", "examples", "sample",
    "samples", "demo", "user", "users", "admin", "account", "accounts",
    "name", "title", "label", "value", "type", "status", "session",
    "memory", "turn", "context", "summary", "update", "updates", "reply",
    # verbs/adverbs that extraction passes mistook for entities
    "follow", "follows", "following", "precede", "precedes", "supersede",
    "supersedes", "create", "created", "delete", "deleted", "add", "added",
    "remove", "removed", "fix", "fixed", "change", "changes", "changed",
    "please", "thanks", "okay", "yes", "no", "none", "null", "undefined",
    "true", "false", "should", "would", "could", "will", "have", "has",
    "about", "before", "after", "because", "there", "here", "when",
    "where", "what", "which", "who", "why", "how", "this", "that",
    "these", "those", "with", "from", "into", "your", "their",
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
    """Would a human track this label over time? Mirrors host curation rules."""
    label = " ".join(str(value or "").split())
    if len(label) < 2 or len(label) > 96:
        return False
    if "/" in label or "\\" in label:
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
    words = label.split(" ")
    if len(words) == 1:
        lower = label.lower()
        if lower in _GENERIC_ENTITY_STOPWORDS:
            return False
        if _FILE_NAME_RE.match(label):
            return False
        # Seed cleanup is deliberately stricter than the host label filter: a
        # bare single all-lowercase, all-letters token ("invoice", "reissue")
        # is almost always an extraction fragment, not a named entity. Real
        # entities are capitalized, multi-word, or carry distinctive shape
        # (digits, hyphens, dots). Going-forward writes author capitalized
        # names, so this only prunes legacy junk from the seeded base.
        if label == lower and label.isalpha():
            return False
        if label == lower and len(label) < 4 and not any(ch.isdigit() for ch in label):
            return False
    letters = len(re.findall(r"[^\W\d_]", label, re.UNICODE))
    if letters / max(1, len(label.replace(" ", ""))) < 0.5:
        return False
    return True


def clean_entity_list(values: list[Any], *, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        label = " ".join(str(raw or "").split())
        if not label or not is_meaningful_entity(label):
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= limit:
            break
    return out


# ── Thinness detection ────────────────────────────────────────────────────


def _summary_text(bead: dict[str, Any]) -> str:
    summary = bead.get("summary")
    if isinstance(summary, list):
        return " ".join(str(x) for x in summary)
    return str(summary or "")


def _title_is_structural(bead: dict[str, Any]) -> bool:
    title = " ".join(str(bead.get("title") or "").split())
    if not title:
        return True
    document_name = " ".join(str(bead.get("document_name") or "").split())
    if document_name and (title == document_name or title.startswith(f"{document_name} (part")):
        return True
    if _STRUCTURAL_TITLE_RE.match(title):
        return True
    if _FILE_NAME_RE.match(title):
        return True
    return False


def _summary_is_truncation(bead: dict[str, Any]) -> bool:
    summary = [s for s in (bead.get("summary") or []) if str(s).strip()] if isinstance(bead.get("summary"), list) else []
    if not summary:
        return True
    if len(summary) != 1:
        return False
    only = " ".join(str(summary[0]).split())
    detail = " ".join(str(bead.get("detail") or "").split())
    title = " ".join(str(bead.get("title") or "").split())
    if only == title:
        return True
    return bool(detail) and len(only) >= 40 and detail.startswith(only[: len(only) - 1])


def _entity_dirty(bead: dict[str, Any]) -> bool:
    """True when any of the bead's entities looks like extraction junk.

    This is a cheap DETECTION signal used only to route a bead to the LLM —
    it never decides which entities survive. The old all-words heuristic left
    fragments, paths, hashes, and file names; if the bead carries any, the LLM
    should re-author the whole entity set from the bead's content.
    """
    for raw in bead.get("entities") or []:
        label = str(raw or "").strip()
        if label and not is_meaningful_entity(label):
            return True
    return False


def _reauthorable(bead: dict[str, Any]) -> bool:
    """Structural gate shared by thinness and dirtiness triage."""
    if str(bead.get("status") or "").strip().lower() in _INACTIVE_STATUSES:
        return False
    # Goal / proposed-theme beads are authored by the goal lifecycle and the
    # refinement pass, not the content bead-field judge. Their "summary" is a
    # statement, not a document excerpt — enriching them here is wrong, and it
    # would re-target goals this same pass just created.
    if str(bead.get("type") or "").strip().lower() in {"goal", "proposed_theme"}:
        return False
    tags = [str(t) for t in (bead.get("tags") or [])]
    if SEED_BACKFILL_TAG in tags:
        return False
    grounding = " ".join(
        [str(bead.get("title") or ""), _summary_text(bead), str(bead.get("detail") or "")]
    ).strip()
    return len(grounding) >= 24


def _is_thin(bead: dict[str, Any]) -> bool:
    if not _reauthorable(bead):
        return False
    return (
        not list(bead.get("entities") or [])
        or _title_is_structural(bead)
        or _summary_is_truncation(bead)
    )


def _needs_reauthoring(bead: dict[str, Any]) -> bool:
    """Route a bead to the LLM when it is thin OR carries junk entities.

    Triage is deterministic and cheap; the AUTHORING it triggers is the LLM's.
    """
    if not _reauthorable(bead):
        return False
    return (
        not list(bead.get("entities") or [])
        or _title_is_structural(bead)
        or _summary_is_truncation(bead)
        or _entity_dirty(bead)
    )


# ── LLM re-authoring ──────────────────────────────────────────────────────


def _enrich_prompt(bead: dict[str, Any]) -> str:
    grounding = {
        "bead_type": str(bead.get("type") or ""),
        "title": str(bead.get("title") or "")[:200],
        "document_name": str(bead.get("document_name") or "")[:200],
        "summary": [str(x)[:220] for x in (bead.get("summary") or [])][:3],
        "detail": str(bead.get("detail") or "")[:4000],
        "existing_entities": [str(x) for x in (bead.get("entities") or [])][:16],
        "existing_topics": [str(x) for x in (bead.get("topics") or [])][:8],
    }
    return (
        "You are re-authoring semantic fields for one existing memory bead in a "
        "causal memory graph, as a one-time quality backfill. Work ONLY from the "
        "grounding text; never invent names, numbers, dates, or events that are "
        "not present.\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "title": "specific name for what this memory is about (<=120 chars)",\n'
        '  "summary": ["1-3 bullets, <=220 chars each, stating what the content says"],\n'
        '  "entities": ["canonical named things: people, companies, products, systems, projects"],\n'
        '  "topics": ["short lowercase subject phrases"]\n'
        "}\n\n"
        "entities must be proper named things a person would track over time — "
        "never generic words, verbs, paths, or fragments. Use each entity's "
        "canonical capitalized form. Leave a list empty when nothing grounded "
        "qualifies.\n\n"
        "Grounding JSON:\n" + json.dumps(grounding, ensure_ascii=False, sort_keys=True)
    )


def _enrich_bead(bead: dict[str, Any], *, root: str) -> tuple[dict[str, Any], bool, str]:
    """Re-author one thin bead. Returns (fields_to_set, changed, error).

    The bead passed here is a snapshot with deterministic entity cleanup
    already applied. This MUST NOT run while the caller holds the store lock:
    the semantic runtime records a receipt under its own store lock, and
    ``fcntl.flock`` self-deadlocks on a re-entrant acquire in the same process.
    The returned fields are applied to the freshly-read index under the final
    write lock.
    """
    from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime
    from core_memory.schema.semantic_tasks import (
        MODEL_TIER_STANDARD,
        SemanticTaskRequest,
        TASK_BEAD_FIELD_JUDGE,
    )

    try:
        result = get_semantic_task_runtime().run(
            SemanticTaskRequest(
                root=root,
                task_type=TASK_BEAD_FIELD_JUDGE,
                prompt=_enrich_prompt(bead),
                payload={},
                idempotency_key=f"seed-backfill:{bead.get('id')}",
                prompt_version="seed_backfill_enrichment.v1",
                rubric_version="seed_backfill_semantic_fields.v1",
                model_tier=MODEL_TIER_STANDARD,
                max_tokens=700,
                temperature=0,
                json_mode=True,
                fallback_mode="keep_structural_fields",
                authority_boundary="advisory",
                evidence_refs=[str(bead.get("id") or "")],
                metadata={"policy": "seed_quality_backfill", "bead_id": str(bead.get("id") or "")},
            )
        )
    except Exception as exc:  # noqa: BLE001 - per-bead fail-open
        return {}, False, str(exc)
    if not result.ok or not isinstance(result.output_json, dict):
        return {}, False, str(result.error or "enrichment_unavailable")

    judged = result.output_json
    # A degenerate/empty model response must DEFER (leave the bead for a later
    # run), never author. Otherwise an entity-dirty bead could be silently
    # emptied by a model that returned nothing.
    has_content = bool(
        str(judged.get("title") or "").strip()
        or [x for x in (judged.get("summary") or []) if str(x).strip()]
        or [x for x in (judged.get("entities") or []) if str(x).strip()]
        or [x for x in (judged.get("topics") or []) if str(x).strip()]
    )
    if not has_content:
        return {}, False, "empty_model_output"

    fields: dict[str, Any] = {}
    changed = False
    judged_title = " ".join(str(judged.get("title") or "").split())[:200]
    if _title_is_structural(bead) and len(judged_title) >= 8:
        fields["title"] = judged_title
        changed = True
    judged_summary = [str(x).strip()[:220] for x in (judged.get("summary") or []) if str(x).strip()][:3]
    if _summary_is_truncation(bead) and judged_summary:
        fields["summary"] = judged_summary
        changed = True
    # The LLM is the entity judge. When the bead has no entities or carries junk
    # ones, replace the whole set with the model's — read from the bead's own
    # content. clean_entity_list here is a SAFETY NET on the model's output (drop
    # a path/hash it might echo), not the source-data editor. `judged_entities`
    # key is absent -> caller leaves entities alone; present (even []) -> caller
    # replaces, so an entity-dirty bead the model judges to have no real entity
    # is emptied rather than left holding junk.
    if not list(bead.get("entities") or []) or _entity_dirty(bead):
        fields["judged_entities"] = clean_entity_list(list(judged.get("entities") or []))
        changed = True
    if not list(bead.get("topics") or []):
        judged_topics = [str(x).strip()[:80].lower() for x in (judged.get("topics") or []) if str(x).strip()][:8]
        if judged_topics:
            fields["topics"] = judged_topics
            changed = True

    fields["seed_backfill"] = {
        "task_id": str(result.task_id or ""),
        "receipt_id": str(result.receipt_id or ""),
        "changed": changed,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    return fields, changed, ""


def _apply_enrichment_fields(bead: dict[str, Any], fields: dict[str, Any]) -> None:
    """Apply LLM-authored fields plus the rerun-skip tag onto a live bead."""
    for key in ("title", "summary", "topics", "seed_backfill"):
        if key in fields:
            bead[key] = fields[key]
    # judged_entities present (even []) means the model re-judged the entity set
    # from content — replace wholesale so junk cannot survive.
    if "judged_entities" in fields:
        bead["entities"] = list(fields["judged_entities"])
    tags = [str(t) for t in (bead.get("tags") or [])]
    if SEED_BACKFILL_TAG not in tags:
        tags.append(SEED_BACKFILL_TAG)
    bead["tags"] = tags


# ── Registry rebuild ──────────────────────────────────────────────────────


def _rebuild_entity_registry(index: dict[str, Any]) -> dict[str, int]:
    before = len(index.get("entities") or {})
    index["entities"] = {}
    index["entity_aliases"] = {}
    ensure_entity_registry_for_index(index)
    for bead_id, bead in (index.get("beads") or {}).items():
        if not isinstance(bead, dict):
            continue
        bead.setdefault("id", str(bead_id))
        bead["entity_ids"] = []
        sync_bead_entities_for_index(index, bead, source="seed_backfill")
    return {"entities_before": before, "entities_after": len(index.get("entities") or {})}


# ── Dreamer seeding ───────────────────────────────────────────────────────


def _seed_dreamer(
    root: str,
    *,
    run_id: str,
    reviewer: str,
    max_storylines: int,
    max_goals: int,
) -> dict[str, Any]:
    from core_memory.runtime.dreamer.candidates import _read_candidates, decide_dreamer_candidate
    from core_memory.runtime.dreamer.convergence import enqueue_narrative_candidates
    from core_memory.runtime.dreamer.goal_discovery import enqueue_latent_goal_candidates
    from core_memory.runtime.dreamer.refinement import refine_pending_candidates

    out: dict[str, Any] = {}
    try:
        out["narratives_enqueued"] = int(enqueue_narrative_candidates(root, run_id=run_id).get("enqueued") or 0)
    except Exception as exc:  # noqa: BLE001
        out["narratives_enqueued"] = 0
        out["narrative_enqueue_error"] = str(exc)
    try:
        out["goals_enqueued"] = int(enqueue_latent_goal_candidates(root, run_id=run_id).get("enqueued") or 0)
    except Exception as exc:  # noqa: BLE001
        out["goals_enqueued"] = 0
        out["goal_enqueue_error"] = str(exc)

    try:
        refinement = refine_pending_candidates(
            root, run_id=run_id, source="seed_backfill", limit=max(24, (max_storylines + max_goals) * 2)
        )
        out["refined"] = int(refinement.get("refined") or 0)
        if refinement.get("error"):
            out["refinement_error"] = str(refinement.get("error"))
    except Exception as exc:  # noqa: BLE001
        out["refined"] = 0
        out["refinement_error"] = str(exc)

    # Auto-accept only candidates the refiner actually named: an unrefined
    # template row stays pending for a human. The operator invoking this pass
    # is the reviewer of record.
    rows = [r for r in _read_candidates(root) if isinstance(r, dict)]
    notes = "Accepted by seed-quality backfill (operator-invoked cleanup pass)."

    def _refined_pending(hypothesis: str) -> list[dict[str, Any]]:
        return [
            r
            for r in rows
            if str(r.get("hypothesis_type") or "") == hypothesis
            and str(r.get("status") or "").strip().lower() == "pending"
            and str(r.get("refined_at") or "").strip()
            and str(r.get("title") or "").strip()
        ]

    accepted_overlays: list[str] = []
    for row in _refined_pending("narrative_candidate")[: max(0, int(max_storylines))]:
        try:
            decided = decide_dreamer_candidate(
                root=root, candidate_id=str(row.get("id") or ""), decision="accept",
                reviewer=reviewer, notes=notes, apply=True,
            )
            overlay_id = str(((decided.get("applied") or {}) if decided.get("ok") else {}).get("overlay_id") or "")
            if overlay_id:
                accepted_overlays.append(overlay_id)
        except Exception:  # noqa: BLE001 - keep seeding the rest
            continue

    goal_rows = _refined_pending("goal_candidate")
    goal_rows.sort(key=lambda r: (-int(r.get("session_count") or 0), -int(r.get("occurrence_count") or 0)))
    accepted_goals: list[str] = []
    for row in goal_rows[: max(0, int(max_goals))]:
        try:
            decided = decide_dreamer_candidate(
                root=root, candidate_id=str(row.get("id") or ""), decision="accept",
                reviewer=reviewer, notes=notes, apply=True,
            )
            goal_bead = str(((decided.get("applied") or {}) if decided.get("ok") else {}).get("goal_bead_id") or "")
            if goal_bead:
                accepted_goals.append(goal_bead)
        except Exception:  # noqa: BLE001
            continue

    out["accepted_overlay_ids"] = accepted_overlays
    out["accepted_goal_bead_ids"] = accepted_goals
    return out


# ── The pass ──────────────────────────────────────────────────────────────


def run_seed_quality_backfill(
    root: str | Path,
    *,
    apply: bool = False,
    max_enrich: int = 150,
    max_storylines: int = 12,
    max_goals: int = 5,
    reviewer: str = "seed_backfill",
) -> dict[str, Any]:
    """Clean and semantically seed an existing store. See module docstring."""
    root_str = str(root)
    root_path = Path(root_str)
    index_path = root_path / ".beads" / "index.json"
    report: dict[str, Any] = {
        "ok": True,
        "applied": bool(apply),
        "run_id": f"seedfix-{uuid.uuid4().hex[:10]}",
    }
    if not index_path.exists():
        return {**report, "ok": False, "error": "index_missing"}

    # Stage 1 — census over a snapshot (brief read lock, no mutation). The
    # snapshot drives the report and the enrichment worklist; the authoritative
    # write re-reads under the final lock so nothing is clobbered.
    with store_lock(root_path):
        try:
            snapshot = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {**report, "ok": False, "error": f"index_unreadable:{exc}"}
    if not isinstance(snapshot, dict):
        return {**report, "ok": False, "error": "index_not_object"}
    snapshot_beads: dict[str, Any] = snapshot.get("beads") or {}

    # Stage 1 — deterministic TRIAGE census (no mutation, no model calls). It
    # only routes beads to the LLM; it never edits a bead's entities. A bead is
    # eligible when it is thin or carries junk-looking entities.
    thin_count = 0
    entity_dirty_count = 0
    junk_samples: list[str] = []
    worklist: list[str] = []
    for bead_id, bead in snapshot_beads.items():
        if not isinstance(bead, dict):
            continue
        bead.setdefault("id", str(bead_id))
        if not _needs_reauthoring(bead):
            continue
        worklist.append(str(bead_id))
        if _is_thin(bead):
            thin_count += 1
        if _entity_dirty(bead):
            entity_dirty_count += 1
            for raw in bead.get("entities") or []:
                label = str(raw or "").strip()
                if label and not is_meaningful_entity(label) and label not in junk_samples and len(junk_samples) < 12:
                    junk_samples.append(label)
    worklist.sort(key=lambda bid: str((snapshot_beads.get(bid) or {}).get("created_at") or ""), reverse=True)
    report["triage"] = {
        "beads_scanned": len(snapshot_beads),
        "eligible": len(worklist),
        "thin": thin_count,
        "entity_dirty": entity_dirty_count,
        "junk_entity_samples": junk_samples,
    }

    if not apply:
        report["reauthoring"] = {
            "eligible": len(worklist),
            "note": (
                "On apply the LLM authors title/summary/entities/topics for each "
                "eligible bead from its content; the regex rules only triage and "
                "guard model output, and a model outage defers a bead (never prunes)."
            ),
        }
        report["registry"] = {
            "entities_before": len(snapshot.get("entities") or {}),
            "entities_after": None,
            "note": "rebuilt_on_apply",
        }
    else:
        # Stage 2 — LLM re-authoring, NO store lock held (the semantic runtime
        # records receipts under its own lock; a re-entrant flock deadlocks).
        # The LLM is the judge: it authors each eligible bead's fields from
        # content. When the model is unavailable the bead is DEFERRED (left
        # untouched, counted) — the deterministic layer never authors meaning.
        reauthoring: dict[str, Any] = {"attempted": 0, "llm_authored": 0, "llm_unavailable": 0}
        enriched_fields: dict[str, dict[str, Any]] = {}
        for bid in worklist[: max(0, int(max_enrich))]:
            bead = snapshot_beads.get(bid)
            if not isinstance(bead, dict):
                continue
            reauthoring["attempted"] += 1
            fields, changed, error = _enrich_bead(bead, root=root_str)
            if error or not changed:
                reauthoring["llm_unavailable"] += 1
                continue
            reauthoring["llm_authored"] += 1
            enriched_fields[bid] = fields
        report["reauthoring"] = reauthoring

        # Stage 3 — authoritative write under a single lock: back up (per-run +
        # stable pristine baseline), re-read fresh, apply ONLY the LLM-authored
        # fields to their beads, rebuild the entity registry, and atomically
        # write. No bead is edited except by the model.
        with store_lock(root_path):
            beads_dir = root_path / ".beads"
            # Unique per-run suffix: the timestamp alone has one-second
            # resolution, so a batched loop firing two apply calls within the
            # same second would otherwise clobber the earlier per-run backup.
            run_suffix = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
            backup_path = beads_dir / f"index.seed-backfill-{run_suffix}.bak.json"
            shutil.copyfile(index_path, backup_path)
            report["backup_path"] = str(backup_path)

            overlays_file = beads_dir / "overlays.jsonl"

            # Stable pristine baseline for a FULL rollback across batched reruns.
            # Each run's per-run backup snapshots the already-mutated store, so
            # restoring the latest only reverses the final batch. The baseline is
            # written once (first apply, pre-mutation), never overwritten, and
            # reported every run — the true pre-first-run state.
            baseline_index = beads_dir / "index.seed-backfill-baseline.bak.json"
            first_run = not baseline_index.exists()
            if first_run:
                shutil.copyfile(index_path, baseline_index)
            report["baseline_backup_path"] = str(baseline_index)

            # Stage 4 appends accepted storyline overlays to overlays.jsonl.
            # Snapshot its pre-seeding state too so rollback is symmetric: a
            # restore of only index.json would leave those overlays in place,
            # and because worldline backbones re-derive deterministically from
            # the restored beads, the overlays re-attach and stay visible
            # instead of detaching. `overlays_existed_before=false` means a
            # rollback deletes overlays.jsonl rather than restoring a snapshot.
            if overlays_file.exists():
                overlays_backup_path = beads_dir / f"overlays.seed-backfill-{run_suffix}.bak.jsonl"
                shutil.copyfile(overlays_file, overlays_backup_path)
                report["overlays_backup_path"] = str(overlays_backup_path)
                report["overlays_existed_before"] = True
            else:
                report["overlays_backup_path"] = None
                report["overlays_existed_before"] = False

            baseline_overlays = beads_dir / "overlays.seed-backfill-baseline.bak.jsonl"
            if first_run and overlays_file.exists():
                shutil.copyfile(overlays_file, baseline_overlays)
            report["baseline_overlays_backup_path"] = (
                str(baseline_overlays) if baseline_overlays.exists() else None
            )
            report["baseline_overlays_existed_before"] = baseline_overlays.exists()

            index = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(index, dict):
                return {**report, "ok": False, "error": "index_not_object"}
            live_beads: dict[str, Any] = index.get("beads") or {}
            for bead_id, bead in live_beads.items():
                if not isinstance(bead, dict):
                    continue
                bead.setdefault("id", str(bead_id))
                fields = enriched_fields.get(bead_id)
                if fields:
                    _apply_enrichment_fields(bead, fields)
            report["registry"] = _rebuild_entity_registry(index)
            atomic_write_json(index_path, index)

    # Stage 4 — dreamer seeding (outside the index lock: candidate accept
    # paths take the store lock themselves through the canonical writers).
    if apply:
        report["seeding"] = _seed_dreamer(
            root_str,
            run_id=str(report["run_id"]),
            reviewer=str(reviewer or "seed_backfill"),
            max_storylines=max_storylines,
            max_goals=max_goals,
        )
    else:
        try:
            from core_memory.runtime.dreamer.convergence import detect_worldline_convergence
            from core_memory.runtime.dreamer.goal_discovery import detect_latent_goals

            report["seeding"] = {
                "convergence_groups_detected": len(detect_worldline_convergence(root_str)),
                "latent_goals_detected": len(detect_latent_goals(root_str)),
                "note": "enqueue/refine/accept run on apply",
            }
        except Exception as exc:  # noqa: BLE001
            report["seeding"] = {"error": str(exc)}

    # Stage 5 — downstream refresh (best-effort).
    if apply:
        refresh: dict[str, Any] = {}
        try:
            from core_memory.persistence.semantic_lifecycle import mark_semantic_dirty

            mark_semantic_dirty(root_str, reason="seed_quality_backfill")
            refresh["semantic_marked_dirty"] = True
        except Exception as exc:  # noqa: BLE001
            refresh["semantic_marked_dirty"] = False
            refresh["semantic_error"] = str(exc)
        try:
            from core_memory.runtime.dreamer.geometry import build_geometry_manifest

            manifest = build_geometry_manifest(root_str)
            refresh["geometry_nodes"] = int(manifest.get("node_count") or 0)
        except Exception as exc:  # noqa: BLE001
            refresh["geometry_error"] = str(exc)
        report["refresh"] = refresh

    return report


__all__ = [
    "SEED_BACKFILL_TAG",
    "clean_entity_list",
    "is_meaningful_entity",
    "run_seed_quality_backfill",
]
