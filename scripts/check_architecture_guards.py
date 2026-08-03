"""Ratchet Core Memory architecture debt without manufacturing a clean slate.

The guard has three modes:

* report mode: print all currently detected violations and exit 0
* baseline mode: compare against a checked-in baseline and fail only on new drift
* candidate mode: write a review-only diff that cannot replace the baseline

Known authority debt is exact, fingerprinted, owned, occurrence-bounded, and
assigned to a deletion PR. Reductions are allowed; additions and increases are
blocking.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

SCHEMA_VERSION = "core_memory.architecture_guards.v2"
COMPAT_SCHEMA_VERSION = "core_memory.compat_surface_usage_baseline.v1"
DEFAULT_BASELINE = Path("scripts/architecture_guards_baseline.json")
DEFAULT_COMPAT_BASELINE = Path("scripts/compat_surface_usage_baseline.json")

ARCHITECTURE_EXCEPTION_KEYS = {
    "id",
    "invariant_ids",
    "category",
    "path",
    "symbol",
    "fingerprint",
    "allowed_behavior",
    "forbidden_behavior",
    "justification",
    "provenance_requirement",
    "owner",
    "delete_by_pr",
    "max_occurrences",
    "governed_calls",
}

ARCHITECTURE_EXCEPTION_CATEGORIES = {
    "canonical_file_authority",
    "deterministic_semantic_author",
    "semantic_fallback",
    "duplicate_resolver",
    "feature_store",
    "legacy_queue",
    "boundary_import",
    "benchmark_shortcut",
}

ARCHITECTURE_INVARIANTS = {
    "CM-OBS-001": "Every bead is an evidence-bound annotation of an observed event.",
    "CM-SEM-001": "Meaning-bearing fields and conditional decisions are LLM-authored.",
    "CM-SEM-002": "Semantic failure writes no manufactured fallback meaning.",
    "CM-CLAIM-001": "Current truth follows explicit revision chains; unresolved conflict is ambiguous.",
    "CM-ASSOC-001": "Association predicate, direction, time, and causality are evidence-bound LLM decisions.",
    "CM-ARTIFACT-001": "Derived artifacts share one evidence-bound append-only lifecycle.",
    "CM-PROMOTION-001": "Promotion affects hot context only and cannot upgrade truth or delete archive evidence.",
    "CM-RETRIEVAL-001": "Retrieval uses one evidence-hydrating pipeline and cites or abstains.",
    "CM-LEDGER-001": "Canonical semantic state is append-only and has one authority.",
    "CM-BOUNDARY-001": "Integrations translate observations and never invent semantic authority.",
    "CM-BENCH-001": "Benchmark shortcuts and gold leakage disqualify quality evidence.",
}

_EXCEPTION_FINGERPRINT_KEYS = tuple(sorted(ARCHITECTURE_EXCEPTION_KEYS - {"fingerprint"}))
_PR_PHASE_RE = re.compile(r"^PR-(?:0[1-9]|[1-9][0-9])[A-Z]$")
_CURRENT_PHASE_RE = re.compile(r"^PR-(?:[0-9]{2})[A-Z]$")
_GLOB_META_RE = re.compile(r"[*?\[\]{}]")
_SEMANTIC_FALLBACK_NAME_RE = re.compile(
    r"fallback.*(?:bead|claim|association|relationship|goal|story|soul|promotion|"
    r"memory|semantic|causal)|(?:bead|claim|association|relationship|goal|story|"
    r"soul|promotion|memory|semantic|causal).*fallback",
    re.IGNORECASE,
)
_GOVERNED_SEMANTIC_MUTATION_CALLS = {
    "_write_association_if_missing",
    "add_structural_edge",
    "apply_crawler_updates",
    "resolve_goal_candidate_for_store",
    "transition_goal_state_for_store",
    "write_claim_updates_to_bead",
    "write_claims_to_bead",
    "write_memory_outcome_to_bead",
}


_FORBIDDEN_DETERMINISTIC_CALLS: tuple[tuple[str, str, str], ...] = (
    (
        "core_memory/association/crawler_contract.py",
        "infer_relationship",
        "preview classifier cannot author a canonical association",
    ),
    (
        "core_memory/claim/turn_integration.py",
        "write_claims_to_bead",
        "claim extraction cannot write canonical claims",
    ),
    (
        "core_memory/runtime/turn/turn_flow.py",
        "write_memory_outcome_to_bead",
        "memory-use classification cannot write canonical bead fields",
    ),
    (
        "core_memory/runtime/session/goal_lifecycle.py",
        "apply_crawler_updates",
        "goal overlap cannot append a canonical association",
    ),
    (
        "core_memory/runtime/session/goal_lifecycle.py",
        "resolve_goal_candidate_for_store",
        "goal overlap cannot resolve canonical promotion state",
    ),
    (
        "core_memory/soul/dreamer_bridge.py",
        "transition_goal_state_for_store",
        "Dreamer cannot auto-endorse a canonical goal",
    ),
)

_FORBIDDEN_WRITER_CALLS: tuple[tuple[str, str, str, str], ...] = (
    (
        "core_memory/persistence/promotion_service.py",
        "decide_session_promotion_states_for_store",
        "_write_json",
        "session promotion must be shadow-only",
    ),
    (
        "core_memory/persistence/promotion_service.py",
        "decide_session_promotion_states_for_store",
        "mark_semantic_dirty",
        "session promotion must be shadow-only",
    ),
    (
        "core_memory/graph/core.py",
        "backfill_causal_links",
        "add_structural_edge",
        "causal backfill must remain candidate-only",
    ),
    (
        "core_memory/graph/core.py",
        "infer_structural_edges",
        "add_structural_edge",
        "structural inference must remain candidate-only",
    ),
    (
        "core_memory/persistence/store_compaction_ops.py",
        "compact_for_store",
        "promotion_state",
        "compaction cannot auto-promote",
    ),
)

LAYER_RANK: dict[str, int] = {
    "schema": 0,
    "temporal": 0,
    "config": 0,
    "ledger": 0,
    "persistence": 1,
    "semantic": 1,
    "association": 2,
    "claim": 2,
    "data": 2,
    "entity": 2,
    "graph": 2,
    "policy": 2,
    "reporting": 2,
    "soul": 2,
    "retrieval": 3,
    "runtime": 4,
    "write_pipeline": 4,
    "integrations": 5,
}

CORE_ROOT_ALLOWED = {
    "__init__.py",
    "_version.py",
    "identifiers.py",
    "llm_client.py",
    "memory.py",
    "provider_config.py",
    "transcript_ingest.py",
}

RUNTIME_ROOT_ALLOWED = {
    "__init__.py",
    "engine.py",
    "event_schemas.py",
    "state.py",
}

CURRENT_DOC_SKIP_PARTS = {
    "archive",
    "reports",
}

TRUTH_DOCS = {
    Path("CLAUDE.md"),
    Path("docs/status.md"),
    Path("docs/cleanup-plan.md"),
    Path("docs/PRD/README.md"),
}

ACTIVE_CLEANUP_DEBT_PATHS = {
    "core_memory/cli_handlers_semantic.py",
    "core_memory/graph/api.py",
    "core_memory/persistence/encryption.py",
    "core_memory/persistence/store_core_delegates_mixin.py",
    "core_memory/persistence/store_reporting_promotion_mixin.py",
    "core_memory/persistence/write_ops.py",
    "core_memory/retrieval/pipeline/explain.py",
}

ACTIVE_LIVE_PATHS = {
    "core_memory/retrieval/vector_backend.py",
}

PUBLIC_COMPAT_TRUTH_SURFACES: dict[str, dict[str, object]] = {
    "graph_api_facade": {
        "label": "graph/api.py compatibility facade",
        "pattern": re.compile(r"core_memory/graph/api\.py|core_memory\.graph\.api|graph/api\.py"),
    },
    "persistence_encryption_module": {
        "label": "persistence encryption compatibility module",
        "pattern": re.compile(
            r"core_memory/persistence/encryption\.py|"
            r"core_memory\.persistence\.encryption|"
            r"persistence/encryption\.py"
        ),
    },
    "runtime_semantic_tasks_facades": {
        "label": "runtime semantic-task compatibility facades",
        "pattern": re.compile(
            r"core_memory\.runtime\.semantic_tasks|core_memory/runtime/semantic_tasks|"
            r"runtime/semantic_tasks"
        ),
    },
    "typed_search_form_submission_alias": {
        "label": "typed-search form_submission request alias",
        "pattern": re.compile(r"\bform_submission\b"),
    },
    "memory_store_dream_bridge": {
        "label": "MemoryStore.dream legacy bridge",
        "pattern": re.compile(r"MemoryStore\.dream"),
    },
}

STALE_TRUTH_WORDS = re.compile(
    r"\b(delete|deleted|remove|removed|removal|retire|retired|done|complete|gone)\b",
    re.IGNORECASE,
)
SAFE_TRUTH_WORDS = re.compile(
    r"\b(active|classify|classification|classify-not-delete|do not delete|not deleted|"
    r"not as deleted|pending classification|retained|truth-audit|deprecation window|"
    r"deprecation note|"
    r"breaking-change|removal condition|remove only after|before any removal|"
    r"future removal)\b",
    re.IGNORECASE,
)
FALSE_DEAD_WORDS = re.compile(r"\b(no imports anywhere|zero references|unreferenced|dead)\b", re.IGNORECASE)
SAFE_LIVE_WORDS = re.compile(
    r"\b(not dead|not deleted|do not delete|must not be deleted|live|imported by|covered by)\b",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"(!?\[[^\]]*\]\(([^)]+)\))")

COMPAT_SCAN_ROOTS = (
    "core_memory",
    "tests",
    "docs",
    "scripts",
    ".github",
    "demo",
)

COMPAT_SCAN_SUFFIXES = {
    ".cfg",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
}

COMPAT_SCAN_SKIP_PATHS = {
    "docs/cleanup-plan.md",
    "docs/compatibility_ledger.md",
    "docs/PRD/README.md",
    "docs/status.md",
    "scripts/architecture_guards_baseline.json",
    "scripts/check_architecture_guards.py",
    "scripts/compat_surface_usage_baseline.json",
    "tests/test_architecture_guards.py",
}

COMPAT_SCAN_SKIP_PREFIXES = {
    "core_memory/runtime/semantic_tasks/",
    "docs/archive/",
    "docs/reports/",
}

COMPAT_SURFACES: dict[str, dict[str, object]] = {
    "runtime_semantic_tasks": {
        "label": "runtime semantic-task compatibility facades",
        "pattern": re.compile(r"\bcore_memory\.runtime\.semantic_tasks\b|runtime/semantic_tasks"),
        "skip_prefixes": ("core_memory/runtime/semantic_tasks/",),
    },
    "typed_search_form_submission": {
        "label": "typed-search form_submission request alias",
        "pattern": re.compile(r"\bform_submission\b"),
        "skip_paths": (),
    },
    "memory_search_wrapper": {
        "label": "retrieval tools memory_search.py wrapper",
        "pattern": re.compile(
            r"\bcore_memory\.retrieval\.tools\.memory_search\b|"
            r"core_memory/retrieval/tools/memory_search\.py"
        ),
        "skip_paths": ("core_memory/retrieval/tools/memory_search.py",),
    },
    "memory_store_dream": {
        "label": "MemoryStore.dream legacy bridge",
        "pattern": re.compile(r"\bMemoryStore\.dream\b|\.dream\("),
        "skip_paths": ("core_memory/persistence/store.py",),
    },
    "runtime_event_schemas": {
        "label": "runtime event schema compatibility import path",
        "pattern": re.compile(
            r"\bcore_memory\.runtime\.event_schemas\b|"
            r"from\s+core_memory\.runtime\s+import\s+event_schemas|"
            r"runtime/event_schemas\.py"
        ),
        "skip_paths": ("core_memory/runtime/event_schemas.py",),
    },
}


@dataclass(frozen=True)
class Violation:
    check: str
    id: str
    path: str
    line: int
    message: str
    detail: dict[str, str]

    def to_json(self) -> dict:
        return asdict(self)


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else "unknown"


def architecture_exception_fingerprint(row: dict) -> str:
    """Return the stable review fingerprint for a governed exception."""

    payload = {key: row.get(key) for key in _EXCEPTION_FINGERPRINT_KEYS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _symbol_nodes(root: Path, row: dict) -> list[ast.AST]:
    path = root / str(row.get("path") or "")
    symbol = str(row.get("symbol") or "")
    if not path.is_file() or not symbol:
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    name = symbol.rsplit(".", 1)[-1]
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name
    ]


def _symbol_occurrences(root: Path, row: dict) -> int:
    """Count the governed debt, not merely duplicate Python definitions."""

    nodes = _symbol_nodes(root, row)
    governed_calls = row.get("governed_calls")
    if not isinstance(governed_calls, list) or not governed_calls:
        return len(nodes)
    call_names = {str(item) for item in governed_calls}
    return sum(
        1
        for node in nodes
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and _call_name(child) in call_names
    )


def _exception_registry(baseline: dict) -> list[dict]:
    rows = baseline.get("exceptions")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _phase_order(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"PR-([0-9]{2})([A-Z])", value)
    if match is None:
        raise ValueError(value)
    return int(match.group(1)), ord(match.group(2)) - ord("A")


def _exception_line(baseline_text: str, exception_id: str, fallback: int) -> int:
    match = re.search(rf'^\s*"id"\s*:\s*"{re.escape(exception_id)}"', baseline_text, re.MULTILINE)
    return _line_for_offset(baseline_text, match.start()) if match else fallback


def compare_exception_registries(current: dict, previous: dict) -> list[Violation]:
    """Reject widening an already-committed v2 exception registry."""

    if previous.get("schema_version") != SCHEMA_VERSION:
        return []
    violations: list[Violation] = []
    try:
        current_phase = _phase_order(str(current.get("current_phase") or ""))
        previous_phase = _phase_order(str(previous.get("current_phase") or ""))
    except ValueError:
        pass
    else:
        if current_phase < previous_phase:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id="architecture_exception:current_phase_regression",
                    path=DEFAULT_BASELINE.as_posix(),
                    line=1,
                    message="The architecture exception registry phase cannot move backward",
                    detail={
                        "previous": str(previous.get("current_phase")),
                        "current": str(current.get("current_phase")),
                    },
                )
            )
    current_by_id = {str(row.get("id")): row for row in _exception_registry(current)}
    previous_by_id = {str(row.get("id")): row for row in _exception_registry(previous)}
    for exception_id, row in sorted(current_by_id.items()):
        prior = previous_by_id.get(exception_id)
        if prior is None:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"architecture_exception:{exception_id}:new_exception",
                    path=str(row.get("path") or DEFAULT_BASELINE.as_posix()),
                    line=1,
                    message="New architecture exceptions cannot silently bless additional debt",
                    detail={"exception_id": exception_id},
                )
            )
            continue
        metadata_keys = ARCHITECTURE_EXCEPTION_KEYS - {
            "fingerprint",
            "governed_calls",
            "max_occurrences",
        }
        metadata_changed = any(row.get(key) != prior.get(key) for key in metadata_keys)
        current_calls = set(row.get("governed_calls") or [])
        previous_calls = set(prior.get("governed_calls") or [])
        governed_calls_increased = not current_calls.issubset(previous_calls)
        try:
            ceiling_increased = int(row.get("max_occurrences")) > int(prior.get("max_occurrences"))
        except (TypeError, ValueError):
            ceiling_increased = True
        if metadata_changed or governed_calls_increased or ceiling_increased:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"architecture_exception:{exception_id}:exception_widened",
                    path=str(row.get("path") or DEFAULT_BASELINE.as_posix()),
                    line=1,
                    message="Committed architecture exceptions cannot widen governed behavior or debt",
                    detail={
                        "exception_id": exception_id,
                        "previous_max": str(prior.get("max_occurrences")),
                        "current_max": str(row.get("max_occurrences")),
                    },
                )
            )
    return violations


def _load_baseline_at_ref(root: Path, ref: str) -> dict:
    result = subprocess.run(
        ["git", "show", f"{ref}:{DEFAULT_BASELINE.as_posix()}"],
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or f"could not read baseline at {ref}")
    return json.loads(result.stdout)


def check_architecture_exception_registry(
    root: Path,
    *,
    current_phase: str | None = None,
) -> list[Violation]:
    """Validate that every temporary authority exception is explicit and expiring."""

    baseline_path = root / DEFAULT_BASELINE
    try:
        baseline_text = baseline_path.read_text(encoding="utf-8")
        baseline = json.loads(baseline_text)
    except (OSError, json.JSONDecodeError) as exc:
        return [
            Violation(
                check="architecture_exception",
                id="architecture_exception:baseline_unreadable",
                path=_relative(baseline_path, root),
                line=1,
                message="Could not read the architecture exception registry",
                detail={"error": exc.__class__.__name__},
            )
        ]

    violations: list[Violation] = []
    baseline_rel = _relative(baseline_path, root)
    if baseline.get("schema_version") != SCHEMA_VERSION:
        violations.append(
            Violation(
                check="architecture_exception",
                id="architecture_exception:schema_version",
                path=baseline_rel,
                line=1,
                message=f"Architecture baseline must use {SCHEMA_VERSION}",
                detail={"actual": str(baseline.get("schema_version") or "missing")},
            )
        )
    if not re.fullmatch(r"[0-9a-f]{40}", str(baseline.get("generated_from_commit") or "")):
        violations.append(
            Violation(
                check="architecture_exception",
                id="architecture_exception:generated_from_commit",
                path=baseline_rel,
                line=1,
                message="Architecture baseline must record its 40-character source commit",
                detail={},
            )
        )

    effective_phase = current_phase or str(baseline.get("current_phase") or "")
    if not _CURRENT_PHASE_RE.fullmatch(effective_phase):
        violations.append(
            Violation(
                check="architecture_exception",
                id="architecture_exception:current_phase",
                path=baseline_rel,
                line=1,
                message="Architecture baseline must identify the current Observation Ledger PR phase",
                detail={"actual": effective_phase or "missing"},
            )
        )

    rows = baseline.get("exceptions")
    if not isinstance(rows, list):
        return violations + [
            Violation(
                check="architecture_exception",
                id="architecture_exception:registry_missing",
                path=baseline_rel,
                line=1,
                message="Architecture baseline must contain an exceptions array",
                detail={},
            )
        ]

    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issue_prefix = f"architecture_exception:row:{index}"
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:not_object",
                    path=baseline_rel,
                    line=index + 1,
                    message="Architecture exception rows must be objects",
                    detail={},
                )
            )
            continue

        exception_id = str(row.get("id") or f"row-{index}")
        issue_prefix = f"architecture_exception:{exception_id}"
        row_line = _exception_line(baseline_text, exception_id, index + 1)
        missing = sorted(
            key
            for key in ARCHITECTURE_EXCEPTION_KEYS
            if key not in row or row.get(key) in (None, "") or (key == "invariant_ids" and row.get(key) == [])
        )
        if missing:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:missing_fields",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception is missing required fields",
                    detail={"exception_id": exception_id, "missing": ",".join(missing)},
                )
            )
            continue

        if exception_id in seen_ids:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:duplicate_id",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception IDs must be unique",
                    detail={"exception_id": exception_id},
                )
            )
        seen_ids.add(exception_id)

        category = str(row["category"])
        if category not in ARCHITECTURE_EXCEPTION_CATEGORIES:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:category",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception uses an unknown category",
                    detail={"exception_id": exception_id, "category": category},
                )
            )

        invariant_ids = row.get("invariant_ids")
        unknown_invariants = (
            sorted(set(str(item) for item in invariant_ids) - set(ARCHITECTURE_INVARIANTS))
            if isinstance(invariant_ids, list)
            else ["not-a-list"]
        )
        if unknown_invariants:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:invariants",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception references unknown invariant IDs",
                    detail={"exception_id": exception_id, "unknown": ",".join(unknown_invariants)},
                )
            )

        path_text = str(row["path"])
        if Path(path_text).is_absolute() or ".." in Path(path_text).parts or _GLOB_META_RE.search(path_text):
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:path",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception paths must be exact repo-relative paths without globs",
                    detail={"exception_id": exception_id, "target_path": path_text},
                )
            )

        if not _PR_PHASE_RE.fullmatch(str(row["delete_by_pr"])):
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:delete_by_pr",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exceptions must expire in a future PR phase",
                    detail={"exception_id": exception_id, "delete_by_pr": str(row["delete_by_pr"])},
                )
            )
        elif _CURRENT_PHASE_RE.fullmatch(effective_phase) and _phase_order(str(row["delete_by_pr"])) <= _phase_order(
            effective_phase
        ):
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:expired",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception reached its mandatory deletion phase",
                    detail={
                        "exception_id": exception_id,
                        "delete_by_pr": str(row["delete_by_pr"]),
                        "current_phase": effective_phase,
                    },
                )
            )

        governed_calls = row.get("governed_calls")
        invalid_calls = (
            sorted(str(item) for item in governed_calls if str(item) not in _GOVERNED_SEMANTIC_MUTATION_CALLS)
            if isinstance(governed_calls, list)
            else ["not-a-list"]
        )
        if invalid_calls:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:governed_calls",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception lists unknown governed mutation calls",
                    detail={"exception_id": exception_id, "invalid": ",".join(invalid_calls)},
                )
            )

        max_occurrences = row["max_occurrences"]
        if isinstance(max_occurrences, bool) or not isinstance(max_occurrences, int) or max_occurrences < 1:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:max_occurrences",
                    path=baseline_rel,
                    line=row_line,
                    message="max_occurrences must be a positive integer",
                    detail={"exception_id": exception_id},
                )
            )
        else:
            actual = _symbol_occurrences(root, row)
            if actual == 0:
                violations.append(
                    Violation(
                        check="architecture_exception",
                        id=f"architecture_exception:{exception_id}:stale_target",
                        path=path_text,
                        line=row_line,
                        message="Architecture exception target no longer exists; remove the stale row",
                        detail={"exception_id": exception_id},
                    )
                )
            elif actual > max_occurrences:
                violations.append(
                    Violation(
                        check="architecture_exception",
                        id=f"architecture_exception:{exception_id}:occurrence_increase",
                        path=path_text,
                        line=row_line,
                        message="Governed architecture debt increased beyond its ratchet",
                        detail={
                            "exception_id": exception_id,
                            "allowed": str(max_occurrences),
                            "actual": str(actual),
                        },
                    )
                )

        expected_fingerprint = architecture_exception_fingerprint(row)
        fingerprint = str(row["fingerprint"])
        if fingerprint != expected_fingerprint or fingerprint in seen_fingerprints:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id=f"{issue_prefix}:fingerprint",
                    path=baseline_rel,
                    line=row_line,
                    message="Architecture exception fingerprint is stale or duplicated",
                    detail={"exception_id": exception_id, "expected": expected_fingerprint},
                )
            )
        seen_fingerprints.add(fingerprint)

    return sorted(violations, key=lambda violation: violation.id)


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _module_name_for_path(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _first_core_part(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "core_memory":
        return None
    return parts[1]


def _resolve_import_from(node: ast.ImportFrom, current_module: str, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""

    current_parts = current_module.split(".")
    if not is_package:
        current_parts = current_parts[:-1]

    if node.level > 1:
        current_parts = current_parts[: -(node.level - 1)]

    target_parts = list(current_parts)
    if node.module:
        target_parts.extend(node.module.split("."))
    return ".".join(target_parts)


def _iter_import_targets(tree: ast.AST, current_module: str, is_package: bool) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import_from(node, current_module, is_package)
            if base:
                yield base, node.lineno
            else:
                for alias in node.names:
                    yield f"{base}.{alias.name}".strip("."), node.lineno


def check_upward_imports(root: Path) -> list[Violation]:
    core = root / "core_memory"
    violations: dict[str, Violation] = {}
    for path in sorted(core.rglob("*.py")):
        rel = _relative(path, root)
        source_part = path.relative_to(core).parts[0]
        source_rank = LAYER_RANK.get(source_part)
        if source_rank is None:
            continue

        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            violation_id = f"upward_import:{rel}:parse_error"
            violations[violation_id] = Violation(
                check="upward_import",
                id=violation_id,
                path=rel,
                line=exc.lineno or 1,
                message=f"Could not parse Python file while checking imports: {exc.msg}",
                detail={"source_layer": source_part, "target": "parse_error"},
            )
            continue

        current_module = _module_name_for_path(path, root)
        is_package = path.name == "__init__.py"
        for target_module, line in _iter_import_targets(tree, current_module, is_package):
            if target_module == "core_memory":
                continue
            target_part = _first_core_part(target_module)
            target_rank = LAYER_RANK.get(target_part or "")
            if target_part is None or target_rank is None:
                continue
            if target_rank <= source_rank:
                continue

            violation_id = f"upward_import:{rel}:{source_part}->{target_part}:{target_module}"
            violations.setdefault(
                violation_id,
                Violation(
                    check="upward_import",
                    id=violation_id,
                    path=rel,
                    line=line,
                    message=(f"{source_part}/ imports upward into {target_part}/ via {target_module}"),
                    detail={
                        "source_layer": source_part,
                        "target_layer": target_part,
                        "target_module": target_module,
                    },
                ),
            )
    return sorted(violations.values(), key=lambda v: v.id)


def check_flat_files(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    checks = [
        (root / "core_memory", CORE_ROOT_ALLOWED, "core_memory root"),
        (root / "core_memory" / "runtime", RUNTIME_ROOT_ALLOWED, "runtime root"),
    ]
    for directory, allowed, label in checks:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name in allowed:
                continue
            rel = _relative(path, root)
            violation_id = f"flat_file:{rel}"
            violations.append(
                Violation(
                    check="flat_file",
                    id=violation_id,
                    path=rel,
                    line=1,
                    message=f"{rel} is not in the {label} flat-file allowlist",
                    detail={"directory": label, "file": path.name},
                )
            )
    return violations


def _iter_current_markdown_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.glob("*.md")):
        yield path
    docs = root / "docs"
    if not docs.exists():
        return
    for path in sorted(docs.rglob("*.md")):
        rel_parts = path.relative_to(docs).parts
        if rel_parts and rel_parts[0] in CURRENT_DOC_SKIP_PARTS:
            continue
        yield path


def _normalise_markdown_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if not target:
        return None
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(" ", 1)[0].strip()
    if not target or target.startswith("#"):
        return None

    split = urlsplit(target)
    if split.scheme in {"http", "https", "mailto", "app"}:
        return None
    if split.scheme and len(split.scheme) > 1:
        return None

    without_fragment = target.split("#", 1)[0].split("?", 1)[0]
    if not without_fragment:
        return None
    return unquote(without_fragment)


def check_markdown_links(root: Path) -> list[Violation]:
    violations: dict[str, Violation] = {}
    for path in _iter_current_markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            raw_target = match.group(2)
            target = _normalise_markdown_target(raw_target)
            if target is None:
                continue
            target_path = (path.parent / target).resolve()
            root_resolved = root.resolve()
            try:
                target_path.relative_to(root_resolved)
            except ValueError:
                # Links outside the repo are deliberate for files like ../README.md.
                if target_path.exists():
                    continue
            if target_path.exists():
                continue

            rel = _relative(path, root)
            violation_id = f"markdown_link:{rel}:{target}"
            violations.setdefault(
                violation_id,
                Violation(
                    check="markdown_link",
                    id=violation_id,
                    path=rel,
                    line=_line_for_offset(text, match.start()),
                    message=f"{rel} links to missing local target {target}",
                    detail={"target": target},
                ),
            )
    return sorted(violations.values(), key=lambda v: v.id)


def _line_is_stale_truth_claim(line: str) -> bool:
    if not STALE_TRUTH_WORDS.search(line):
        return False
    if SAFE_TRUTH_WORDS.search(line):
        return False
    return True


def _line_is_false_dead_claim_for_live_path(line: str) -> bool:
    if not FALSE_DEAD_WORDS.search(line):
        return False
    if SAFE_LIVE_WORDS.search(line):
        return False
    return True


def check_cleanup_truth(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    existing_debt = {path for path in ACTIVE_CLEANUP_DEBT_PATHS if (root / path).exists()}
    existing_live_paths = {path for path in ACTIVE_LIVE_PATHS if (root / path).exists()}
    current_docs = {Path(_relative(path, root)) for path in _iter_current_markdown_files(root)}
    docs = sorted({*TRUTH_DOCS, *current_docs})
    for doc in docs:
        path = root / doc
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line_has_path_truth_violation = False
            for active_path in sorted(existing_debt):
                if active_path not in line:
                    continue
                if not _line_is_stale_truth_claim(line):
                    continue
                line_has_path_truth_violation = True
                violation_id = f"cleanup_truth:{doc.as_posix()}:{active_path}"
                violations.append(
                    Violation(
                        check="cleanup_truth",
                        id=violation_id,
                        path=doc.as_posix(),
                        line=lineno,
                        message=(
                            f"{doc.as_posix()} appears to describe existing {active_path} as deleted/removed/done"
                        ),
                        detail={"active_path": active_path, "line": line.strip()},
                    )
                )
            if line_has_path_truth_violation:
                continue
            for active_path in sorted(existing_live_paths):
                if active_path not in line:
                    continue
                if not _line_is_false_dead_claim_for_live_path(line):
                    continue
                violation_id = f"cleanup_truth:{doc.as_posix()}:{active_path}:false_dead"
                violations.append(
                    Violation(
                        check="cleanup_truth",
                        id=violation_id,
                        path=doc.as_posix(),
                        line=lineno,
                        message=(f"{doc.as_posix()} appears to describe live {active_path} as dead or unreferenced"),
                        detail={"active_path": active_path, "line": line.strip()},
                    )
                )
            for surface_key, surface in sorted(PUBLIC_COMPAT_TRUTH_SURFACES.items()):
                pattern = surface["pattern"]
                search = getattr(pattern, "search")
                if not search(line):
                    continue
                if not _line_is_stale_truth_claim(line):
                    continue
                violation_id = f"cleanup_truth:{doc.as_posix()}:{surface_key}"
                violations.append(
                    Violation(
                        check="cleanup_truth",
                        id=violation_id,
                        path=doc.as_posix(),
                        line=lineno,
                        message=(
                            f"{doc.as_posix()} appears to describe retained {surface['label']} as deleted/removed/done"
                        ),
                        detail={
                            "surface_key": surface_key,
                            "surface_label": str(surface["label"]),
                            "line": line.strip(),
                        },
                    )
                )
    return sorted(violations, key=lambda v: v.id)


def check_prd_index(root: Path) -> list[Violation]:
    prd_dir = root / "docs" / "PRD"
    readme = prd_dir / "README.md"
    if not prd_dir.exists() or not readme.exists():
        return []

    index_text = readme.read_text(encoding="utf-8")
    violations: list[Violation] = []
    for path in sorted(prd_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        if path.name in index_text:
            continue
        rel = _relative(path, root)
        violation_id = f"prd_index:{rel}"
        violations.append(
            Violation(
                check="prd_index",
                id=violation_id,
                path="docs/PRD/README.md",
                line=1,
                message=f"docs/PRD/README.md does not list {rel}",
                detail={"prd_file": rel},
            )
        )
    return violations


def _is_compat_scan_path(path: Path, root: Path) -> bool:
    rel = _relative(path, root)
    if path.suffix not in COMPAT_SCAN_SUFFIXES:
        return False
    if rel in COMPAT_SCAN_SKIP_PATHS:
        return False
    if any(rel.startswith(prefix) for prefix in COMPAT_SCAN_SKIP_PREFIXES):
        return False
    return True


def _iter_compat_scan_files(root: Path) -> Iterable[Path]:
    for scan_root in COMPAT_SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        if base.is_file():
            if _is_compat_scan_path(base, root):
                yield base
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and _is_compat_scan_path(path, root):
                yield path


def _surface_skips_path(surface: dict[str, object], rel: str) -> bool:
    skip_paths = tuple(str(item) for item in surface.get("skip_paths", ()))
    if rel in skip_paths:
        return True
    skip_prefixes = tuple(str(item) for item in surface.get("skip_prefixes", ()))
    return any(rel.startswith(prefix) for prefix in skip_prefixes)


def check_compat_surface_usage(root: Path) -> list[Violation]:
    """Find first-party references to compatibility surfaces under governance.

    This is intentionally separate from architecture debt: public compatibility
    facades can remain, but new first-party reliance on them should not grow
    while the cleanup closeout train migrates callers to canonical paths.
    """

    violations: list[Violation] = []
    for path in _iter_compat_scan_files(root):
        rel = _relative(path, root)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for surface_key, surface in sorted(COMPAT_SURFACES.items()):
            if _surface_skips_path(surface, rel):
                continue
            pattern = surface["pattern"]
            search = getattr(pattern, "search")
            label = str(surface["label"])
            for lineno, line in enumerate(lines, start=1):
                if not search(line):
                    continue
                violation_id = f"compat_surface_usage:{surface_key}:{rel}:{lineno}"
                violations.append(
                    Violation(
                        check="compat_surface_usage",
                        id=violation_id,
                        path=rel,
                        line=lineno,
                        message=f"{rel} references {label}",
                        detail={
                            "surface_key": surface_key,
                            "surface_label": label,
                            "line": line.strip(),
                        },
                    )
                )
    return sorted(violations, key=lambda v: (v.detail.get("surface_key", ""), v.path, v.line))


def _function_node(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _function_calls(node: ast.AST, target: str) -> bool:
    return any(isinstance(child, ast.Call) and _call_name(child) == target for child in ast.walk(node))


def _function_assigns_subscript_key(node: ast.AST, key: str) -> bool:
    for child in ast.walk(node):
        targets: list[ast.AST] = []
        if isinstance(child, ast.Assign):
            targets = list(child.targets)
        elif isinstance(child, ast.AnnAssign):
            targets = [child.target]
        elif isinstance(child, ast.AugAssign):
            targets = [child.target]
        for target in targets:
            if not isinstance(target, ast.Subscript):
                continue
            if isinstance(target.slice, ast.Constant) and target.slice.value == key:
                return True
    return False


def _exception_lookup(baseline: dict) -> set[tuple[str, str, str]]:
    return {
        (str(row.get("category") or ""), str(row.get("path") or ""), str(row.get("symbol") or ""))
        for row in _exception_registry(baseline)
    }


def _governed_mutation_lookup(baseline: dict) -> set[tuple[str, str, str]]:
    allowed_categories = {
        "deterministic_semantic_author",
        "semantic_fallback",
        "feature_store",
        "boundary_import",
    }
    return {
        (str(row.get("path") or ""), str(row.get("symbol") or ""), str(call))
        for row in _exception_registry(baseline)
        if row.get("category") in allowed_categories
        for call in row.get("governed_calls", [])
    }


def check_semantic_fallback_paths(root: Path) -> list[Violation]:
    """Reject newly named semantic fallback functions outside the exception registry."""

    try:
        baseline = load_baseline(root / DEFAULT_BASELINE)
    except (OSError, json.JSONDecodeError):
        return []
    exceptions = _exception_lookup(baseline)
    violations: list[Violation] = []
    for path in sorted((root / "core_memory").rglob("*.py")):
        rel = _relative(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = _module_name_for_path(path, root)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _SEMANTIC_FALLBACK_NAME_RE.search(node.name):
                continue
            symbol = f"{module}.{node.name}"
            if ("semantic_fallback", rel, symbol) in exceptions:
                continue
            violation_id = f"semantic_fallback:{rel}:{symbol}"
            violations.append(
                Violation(
                    check="semantic_fallback",
                    id=violation_id,
                    path=rel,
                    line=node.lineno,
                    message="Semantic fallback path is not registered as expiring architecture debt",
                    detail={"category": "semantic_fallback", "symbol": symbol},
                )
            )
    return sorted(violations, key=lambda violation: violation.id)


def check_semantic_mutation_calls(root: Path) -> list[Violation]:
    """Require every call into a legacy semantic mutator to be exact and governed."""

    try:
        baseline = load_baseline(root / DEFAULT_BASELINE)
    except (OSError, json.JSONDecodeError):
        return []
    allowed = _governed_mutation_lookup(baseline)
    violations: list[Violation] = []
    for path in sorted((root / "core_memory").rglob("*.py")):
        rel = _relative(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = _module_name_for_path(path, root)
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            if call_name not in _GOVERNED_SEMANTIC_MUTATION_CALLS:
                continue
            owner: ast.AST = node
            while owner in parents and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents[owner]
            owner_name = owner.name if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"
            symbol = f"{module}.{owner_name}"
            if (rel, symbol, call_name) in allowed:
                continue
            violation_id = f"semantic_mutation:{rel}:{symbol}:{call_name}"
            violations.append(
                Violation(
                    check="semantic_mutation",
                    id=violation_id,
                    path=rel,
                    line=node.lineno,
                    message="Legacy semantic mutation call is not governed by an exact exception",
                    detail={
                        "category": "deterministic_semantic_author",
                        "symbol": symbol,
                        "mutation_call": call_name,
                    },
                )
            )
    return sorted(violations, key=lambda violation: violation.id)


TARGET_BOUNDARY_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "core_memory/ledger": (
        "core_memory.config",
        "core_memory.domain",
        "core_memory.identifiers",
        "core_memory.schema",
        "core_memory.temporal",
    ),
    "core_memory/semantic": (
        "core_memory.config",
        "core_memory.domain",
        "core_memory.identifiers",
        "core_memory.ledger",
        "core_memory.llm_client",
        "core_memory.provider_config",
        "core_memory.schema",
        "core_memory.temporal",
    ),
}


def check_target_boundaries(root: Path) -> list[Violation]:
    """Activate target-package dependency rules as soon as target packages exist."""

    violations: list[Violation] = []
    for target_root, allowed_prefixes in TARGET_BOUNDARY_ALLOWLIST.items():
        directory = root / target_root
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            rel = _relative(path, root)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            current_module = _module_name_for_path(path, root)
            for imported, line in _iter_import_targets(tree, current_module, path.name == "__init__.py"):
                if imported != "core_memory" and not imported.startswith("core_memory."):
                    continue
                allowed = any(imported == prefix or imported.startswith(prefix + ".") for prefix in allowed_prefixes)
                if allowed:
                    continue
                violation_id = f"target_boundary:{rel}:{imported}"
                violations.append(
                    Violation(
                        check="target_boundary",
                        id=violation_id,
                        path=rel,
                        line=line,
                        message=f"Target package imports non-allowlisted Core Memory module {imported}",
                        detail={
                            "target_root": target_root,
                            "allowed_prefixes": ",".join(allowed_prefixes),
                        },
                    )
                )
    return sorted(violations, key=lambda violation: violation.id)


def check_deterministic_semantic_writers(root: Path) -> list[Violation]:
    """Enforce the explicit allowlist around deterministic semantic authority."""
    violations: list[Violation] = []

    def parse(path_text: str) -> tuple[Path, ast.AST | None]:
        path = root / path_text
        try:
            return path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            return path, None

    for path_text, forbidden, message in _FORBIDDEN_DETERMINISTIC_CALLS:
        path, tree = parse(path_text)
        if tree is None:
            continue
        call = next(
            (node for node in ast.walk(tree) if isinstance(node, ast.Call) and _call_name(node) == forbidden),
            None,
        )
        if call is not None:
            violations.append(
                Violation(
                    check="deterministic_writer",
                    id=f"deterministic_writer:{path_text}:{forbidden}",
                    path=path_text,
                    line=call.lineno,
                    message=message,
                    detail={"forbidden_call": forbidden},
                )
            )

    for path_text, function_name, forbidden, message in _FORBIDDEN_WRITER_CALLS:
        path, tree = parse(path_text)
        if tree is None:
            continue
        function = _function_node(tree, function_name)
        if function is None:
            continue
        violated = (
            _function_assigns_subscript_key(function, forbidden)
            if forbidden == "promotion_state"
            else _function_calls(function, forbidden)
        )
        if violated:
            violations.append(
                Violation(
                    check="deterministic_writer",
                    id=f"deterministic_writer:{path_text}:{function_name}:{forbidden}",
                    path=path_text,
                    line=function.lineno,
                    message=message,
                    detail={"function": function_name, "forbidden": forbidden},
                )
            )
    return sorted(violations, key=lambda violation: violation.id)


def collect_violations(
    root: Path,
    *,
    current_phase: str | None = None,
    ratchet_ref: str | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(check_architecture_exception_registry(root, current_phase=current_phase))
    if ratchet_ref:
        try:
            current = load_baseline(root / DEFAULT_BASELINE)
            previous = _load_baseline_at_ref(root, ratchet_ref)
        except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            violations.append(
                Violation(
                    check="architecture_exception",
                    id="architecture_exception:ratchet_ref_unreadable",
                    path=DEFAULT_BASELINE.as_posix(),
                    line=1,
                    message="Could not compare the exception registry to the requested Git ref",
                    detail={"ref": ratchet_ref, "error": exc.__class__.__name__},
                )
            )
        else:
            violations.extend(compare_exception_registries(current, previous))
    violations.extend(check_upward_imports(root))
    violations.extend(check_flat_files(root))
    violations.extend(check_markdown_links(root))
    violations.extend(check_cleanup_truth(root))
    violations.extend(check_prd_index(root))
    violations.extend(check_semantic_fallback_paths(root))
    violations.extend(check_semantic_mutation_calls(root))
    violations.extend(check_deterministic_semantic_writers(root))
    violations.extend(check_target_boundaries(root))
    return sorted(violations, key=lambda v: (v.check, v.id))


def make_baseline(root: Path, violations: list[Violation]) -> dict:
    try:
        current = load_baseline(root / DEFAULT_BASELINE)
    except (OSError, json.JSONDecodeError):
        current = {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_from_commit": _git_commit(root),
        "current_phase": current.get("current_phase"),
        "repo_root": ".",
        "invariants": ARCHITECTURE_INVARIANTS,
        "violation_ids": [v.id for v in violations],
        "violations": [v.to_json() for v in violations],
        "exceptions": _exception_registry(current),
    }


def make_baseline_candidate(root: Path, violations: list[Violation]) -> dict:
    current = load_baseline(root / DEFAULT_BASELINE)
    new, resolved = compare_to_baseline(violations, current)
    payload = make_baseline(root, violations)
    payload["candidate_only"] = True
    payload["candidate_diff"] = {
        "new_violation_ids": [violation.id for violation in new],
        "resolved_violation_ids": resolved,
        "exception_ids": [row.get("id") for row in _exception_registry(current)],
        "instruction": (
            "Review this diff and edit the canonical baseline explicitly. "
            "The guard never promotes a candidate automatically."
        ),
    }
    return payload


def _compat_counts(violations: list[Violation]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for violation in violations:
        surface_key = violation.detail.get("surface_key", "unknown")
        surface_counts = counts.setdefault(surface_key, {})
        surface_counts[violation.path] = surface_counts.get(violation.path, 0) + 1
    return {surface: dict(sorted(path_counts.items())) for surface, path_counts in sorted(counts.items())}


def make_compat_baseline(root: Path, violations: list[Violation]) -> dict:
    counts = _compat_counts(violations)
    return {
        "schema_version": COMPAT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": ".",
        "description": (
            "Compatibility-surface usage ratchet. Counts are allowed current "
            "first-party references, grouped by surface and path; new or "
            "increased counts are drift."
        ),
        "allowed_counts": counts,
        "surface_totals": {surface: sum(path_counts.values()) for surface, path_counts in counts.items()},
    }


def load_baseline(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_to_baseline(violations: list[Violation], baseline: dict) -> tuple[list[Violation], list[str]]:
    current_by_id = {v.id: v for v in violations}
    baseline_ids = set(baseline.get("violation_ids") or [])
    if not baseline_ids:
        baseline_ids = {v.get("id", "") for v in baseline.get("violations", [])}
    new_ids = sorted(set(current_by_id) - baseline_ids)
    resolved_ids = sorted(baseline_ids - set(current_by_id))
    return [current_by_id[i] for i in new_ids], resolved_ids


def compare_compat_to_baseline(
    violations: list[Violation],
    baseline: dict,
) -> tuple[list[Violation], list[str]]:
    allowed_counts = baseline.get("allowed_counts") or {}
    current_counts = _compat_counts(violations)
    by_surface_path: dict[tuple[str, str], list[Violation]] = {}
    for violation in violations:
        surface_key = violation.detail.get("surface_key", "unknown")
        by_surface_path.setdefault((surface_key, violation.path), []).append(violation)

    new: list[Violation] = []
    resolved: list[str] = []
    all_surfaces = sorted(set(allowed_counts) | set(current_counts))
    for surface_key in all_surfaces:
        allowed_paths = allowed_counts.get(surface_key, {}) or {}
        current_paths = current_counts.get(surface_key, {}) or {}
        all_paths = sorted(set(allowed_paths) | set(current_paths))
        for path in all_paths:
            allowed_count = int(allowed_paths.get(path, 0))
            current_count = int(current_paths.get(path, 0))
            if current_count > allowed_count:
                path_violations = by_surface_path.get((surface_key, path), [])
                new.extend(path_violations[allowed_count:])
            elif current_count < allowed_count:
                resolved.append(f"{surface_key}:{path}:{allowed_count - current_count}")
    return new, resolved


def print_report(
    violations: list[Violation],
    *,
    new: list[Violation] | None = None,
    resolved: list[str] | None = None,
) -> None:
    counts: dict[str, int] = {}
    for violation in violations:
        counts[violation.check] = counts.get(violation.check, 0) + 1

    print("Architecture guard report")
    print("=========================")
    for check in [
        "architecture_exception",
        "upward_import",
        "flat_file",
        "markdown_link",
        "cleanup_truth",
        "prd_index",
        "semantic_fallback",
        "semantic_mutation",
        "deterministic_writer",
        "target_boundary",
    ]:
        print(f"{check}: {counts.get(check, 0)}")
    print(f"total: {len(violations)}")

    if new is not None:
        print()
        print(f"new violations: {len(new)}")
        print(f"resolved baseline violations: {len(resolved or [])}")

    if violations and new is None:
        print()
        print("Violations:")
        for violation in violations:
            print(f"- [{violation.check}] {violation.path}:{violation.line} {violation.message}")

    if new:
        print()
        print("New drift:")
        for violation in new:
            print(f"- [{violation.check}] {violation.path}:{violation.line} {violation.message}")


def print_compat_report(
    violations: list[Violation],
    *,
    new: list[Violation] | None = None,
    resolved: list[str] | None = None,
) -> None:
    counts = _compat_counts(violations)

    print()
    print("Compatibility surface usage report")
    print("==================================")
    for surface_key in sorted(COMPAT_SURFACES):
        print(f"{surface_key}: {sum(counts.get(surface_key, {}).values())}")
    print(f"total: {len(violations)}")

    if new is not None:
        print()
        print(f"new compatibility usage: {len(new)}")
        print(f"reduced baseline entries: {len(resolved or [])}")

    if violations and new is None:
        print()
        print("Compatibility surface usage:")
        for violation in violations:
            print(f"- [{violation.detail.get('surface_key')}] {violation.path}:{violation.line}")

    if new:
        print()
        print("New compatibility drift:")
        for violation in new:
            print(f"- [{violation.detail.get('surface_key')}] {violation.path}:{violation.line} {violation.message}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root_from_script())
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current-phase", help="Override the registry phase for expiry checks")
    parser.add_argument("--ratchet-ref", help="Reject exception-registry widening from this Git ref")
    parser.add_argument(
        "--write-baseline-candidate",
        "--write-baseline",
        dest="write_baseline_candidate",
        type=Path,
        help="Write a review-only candidate diff; never updates the canonical baseline",
    )
    parser.add_argument("--fail-on-new", action="store_true")
    parser.add_argument("--compat-baseline", type=Path, default=DEFAULT_COMPAT_BASELINE)
    parser.add_argument("--write-compat-baseline", type=Path)
    parser.add_argument("--fail-on-new-compat", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    root = args.root.resolve()
    violations = collect_violations(
        root,
        current_phase=args.current_phase,
        ratchet_ref=args.ratchet_ref,
    )
    run_compat_guard = bool(args.write_compat_baseline or args.fail_on_new_compat)
    compat_violations = check_compat_surface_usage(root) if run_compat_guard else []

    if args.write_baseline_candidate:
        baseline_path = args.write_baseline_candidate
        if not baseline_path.is_absolute():
            baseline_path = root / baseline_path
        if baseline_path.resolve() == (root / DEFAULT_BASELINE).resolve():
            print(
                "Refusing to overwrite the canonical architecture baseline; "
                "write a candidate to a separate path for review",
                file=sys.stderr,
            )
            return 2
        try:
            candidate = make_baseline_candidate(root, violations)
        except (OSError, json.JSONDecodeError) as exc:
            print(
                "Cannot create architecture baseline candidate: "
                f"canonical baseline is unavailable or malformed ({exc.__class__.__name__})",
                file=sys.stderr,
            )
            return 2
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.write_compat_baseline:
        compat_baseline_path = args.write_compat_baseline
        if not compat_baseline_path.is_absolute():
            compat_baseline_path = root / compat_baseline_path
        compat_baseline_path.parent.mkdir(parents=True, exist_ok=True)
        compat_baseline_path.write_text(
            json.dumps(make_compat_baseline(root, compat_violations), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    new: list[Violation] | None = None
    resolved: list[str] | None = None
    if args.fail_on_new:
        baseline_path = args.baseline
        if not baseline_path.is_absolute():
            baseline_path = root / baseline_path
        if not baseline_path.exists():
            print(f"Missing architecture guard baseline: {baseline_path}", file=sys.stderr)
            return 2
        new, resolved = compare_to_baseline(violations, load_baseline(baseline_path))

    new_compat: list[Violation] | None = None
    resolved_compat: list[str] | None = None
    if args.fail_on_new_compat:
        compat_baseline_path = args.compat_baseline
        if not compat_baseline_path.is_absolute():
            compat_baseline_path = root / compat_baseline_path
        if not compat_baseline_path.exists():
            print(f"Missing compatibility surface baseline: {compat_baseline_path}", file=sys.stderr)
            return 2
        new_compat, resolved_compat = compare_compat_to_baseline(
            compat_violations,
            load_baseline(compat_baseline_path),
        )

    if args.json_output:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "violations": [v.to_json() for v in violations],
            "new_violations": [v.to_json() for v in new or []],
            "resolved_violation_ids": resolved or [],
        }
        if run_compat_guard:
            payload.update(
                {
                    "compat_surface_usage": [v.to_json() for v in compat_violations],
                    "new_compat_surface_usage": [v.to_json() for v in new_compat or []],
                    "resolved_compat_surface_usage": resolved_compat or [],
                }
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_report(violations, new=new, resolved=resolved)
        if args.fail_on_new_compat or args.write_compat_baseline:
            print_compat_report(compat_violations, new=new_compat, resolved=resolved_compat)

    if args.fail_on_new and new:
        return 1
    if args.fail_on_new_compat and new_compat:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
