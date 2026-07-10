"""Report architecture drift without forcing existing cleanup debt closed.

The guard has two modes:

* report mode: print all currently detected violations and exit 0
* baseline mode: compare against a checked-in baseline and fail only on new drift

This keeps the cleanup train honest without turning known debt into a blocking
mega-refactor.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = "core_memory.architecture_guards.v1"
COMPAT_SCHEMA_VERSION = "core_memory.compat_surface_usage_baseline.v1"
DEFAULT_BASELINE = Path("scripts/architecture_guards_baseline.json")
DEFAULT_COMPAT_BASELINE = Path("scripts/compat_surface_usage_baseline.json")

SANCTIONED_DETERMINISTIC_WRITER_KEYS = {
    "symbol",
    "record_or_edge_class",
    "rationale",
    "provenance_requirement",
}

# These are the only deterministic semantic paths permitted in this rollout.
# They either persist structural/mechanical records with explicit provenance or
# append advisory candidates.  Canonical promotion, claim, and relationship
# truth must arrive through an agent-authorized contract instead.
SANCTIONED_DETERMINISTIC_WRITERS = [
    {
        "symbol": "core_memory.runtime.session.session_start_flow.process_session_start_impl",
        "record_or_edge_class": "session_boundary_bead",
        "rationale": "Session boundaries are mechanical lifecycle records.",
        "provenance_requirement": "session_id and continuity snapshot",
    },
    {
        "symbol": "core_memory.runtime.flush.flush_state.upsert_process_flush_checkpoint_bead",
        "record_or_edge_class": "flush_checkpoint_bead",
        "rationale": "Flush checkpoints record process progress, not semantic interpretation.",
        "provenance_requirement": "flush transaction id",
    },
    {
        "symbol": "core_memory.runtime.ingest.external_evidence.ingest_external_evidence",
        "record_or_edge_class": "external_evidence_anchor",
        "rationale": "External anchors preserve caller-owned source evidence.",
        "provenance_requirement": "source id, source event id, and hydration ref",
    },
    {
        "symbol": "core_memory.runtime.turn.semantic_state.mark_semantic_write_state",
        "record_or_edge_class": "pending_semantic_state",
        "rationale": "Pending/repair state is an operational receipt, never a canonical bead.",
        "provenance_requirement": "session id, turn id, and status history",
    },
    {
        "symbol": "core_memory.graph.core.sync_structural_pipeline",
        "record_or_edge_class": "explicit_structural_field_projection",
        "rationale": "Projects explicit persisted links and agent associations into structural edges.",
        "provenance_requirement": "association or link source field",
    },
    {
        "symbol": "core_memory.graph.core.backfill_structural_edges",
        "record_or_edge_class": "explicit_structural_field_projection",
        "rationale": "Repairs graph projections from already persisted structural fields.",
        "provenance_requirement": "explicit link or association id",
    },
    {
        "symbol": "core_memory.persistence.promotion_service.decide_session_promotion_states_for_store",
        "record_or_edge_class": "promotion_shadow_recommendation",
        "rationale": "Heuristic promotion signals are append-only advice only.",
        "provenance_requirement": "heuristic version and visible bead id",
    },
    {
        "symbol": "core_memory.persistence.promotion_service.evaluate_candidates_for_store",
        "record_or_edge_class": "promotion_shadow_recommendation",
        "rationale": "Candidate scoring is advisory rather than canonical promotion authority.",
        "provenance_requirement": "score, threshold, and candidate bead id",
    },
    {
        "symbol": "core_memory.persistence.promotion_service.rebalance_promotions_for_store",
        "record_or_edge_class": "promotion_shadow_recommendation",
        "rationale": "Rebalance results are demotion recommendations only.",
        "provenance_requirement": "score, threshold, and candidate bead id",
    },
    {
        "symbol": "core_memory.claim.turn_integration.extract_and_attach_claims",
        "record_or_edge_class": "claim_advisory",
        "rationale": "Heuristic or legacy claim extraction is review context only.",
        "provenance_requirement": "extractor mode and turn id",
    },
    {
        "symbol": "core_memory.claim.update_policy.emit_claim_updates",
        "record_or_edge_class": "claim_update_advisory",
        "rationale": "Automatic reconciliation is advice; explicit authored updates remain canonical.",
        "provenance_requirement": "trigger bead id and authored review provenance",
    },
    {
        "symbol": "core_memory.runtime.dreamer.candidates.decide_dreamer_candidate",
        "record_or_edge_class": "governed_claim_resolution",
        "rationale": "An explicit agent decision can apply a reviewed conflict resolution.",
        "provenance_requirement": "candidate id, explicit_agent_action, and scoped-resolution receipt",
    },
    {
        "symbol": "core_memory.graph.core.backfill_causal_links",
        "record_or_edge_class": "causal_link_candidate",
        "rationale": "Legacy causal backfill now emits candidates and deprecation telemetry only.",
        "provenance_requirement": "candidate overlap and source/target ids",
    },
]


_FORBIDDEN_DETERMINISTIC_CALLS: tuple[tuple[str, str, str], ...] = (
    ("core_memory/association/crawler_contract.py", "infer_relationship", "preview classifier cannot author a canonical association"),
    ("core_memory/claim/turn_integration.py", "write_claims_to_bead", "claim extraction cannot write canonical claims"),
    ("core_memory/runtime/turn/turn_flow.py", "write_memory_outcome_to_bead", "memory-use classification cannot write canonical bead fields"),
    ("core_memory/runtime/session/goal_lifecycle.py", "apply_crawler_updates", "goal overlap cannot append a canonical association"),
    ("core_memory/runtime/session/goal_lifecycle.py", "resolve_goal_candidate_for_store", "goal overlap cannot resolve canonical promotion state"),
    ("core_memory/soul/dreamer_bridge.py", "transition_goal_state_for_store", "Dreamer cannot auto-endorse a canonical goal"),
)

_FORBIDDEN_WRITER_CALLS: tuple[tuple[str, str, str, str], ...] = (
    ("core_memory/persistence/promotion_service.py", "decide_session_promotion_states_for_store", "_write_json", "session promotion must be shadow-only"),
    ("core_memory/persistence/promotion_service.py", "decide_session_promotion_states_for_store", "mark_semantic_dirty", "session promotion must be shadow-only"),
    ("core_memory/graph/core.py", "backfill_causal_links", "add_structural_edge", "causal backfill must remain candidate-only"),
    ("core_memory/graph/core.py", "infer_structural_edges", "add_structural_edge", "structural inference must remain candidate-only"),
    ("core_memory/persistence/store_compaction_ops.py", "compact_for_store", "promotion_state", "compaction cannot auto-promote"),
)

LAYER_RANK: dict[str, int] = {
    "schema": 0,
    "temporal": 0,
    "config": 0,
    "persistence": 1,
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
        "pattern": re.compile(
            r"core_memory/graph/api\.py|core_memory\.graph\.api|graph/api\.py"
        ),
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
        "pattern": re.compile(
            r"\bcore_memory\.runtime\.semantic_tasks\b|runtime/semantic_tasks"
        ),
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
                    message=(
                        f"{source_part}/ imports upward into {target_part}/ "
                        f"via {target_module}"
                    ),
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
    existing_debt = {
        path for path in ACTIVE_CLEANUP_DEBT_PATHS if (root / path).exists()
    }
    existing_live_paths = {
        path for path in ACTIVE_LIVE_PATHS if (root / path).exists()
    }
    current_docs = {
        Path(_relative(path, root)) for path in _iter_current_markdown_files(root)
    }
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
                            f"{doc.as_posix()} appears to describe existing "
                            f"{active_path} as deleted/removed/done"
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
                        message=(
                            f"{doc.as_posix()} appears to describe live "
                            f"{active_path} as dead or unreferenced"
                        ),
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
                            f"{doc.as_posix()} appears to describe retained "
                            f"{surface['label']} as deleted/removed/done"
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


def check_deterministic_semantic_writers(root: Path) -> list[Violation]:
    """Enforce the explicit allowlist around deterministic semantic authority."""
    baseline_path = root / DEFAULT_BASELINE
    if not baseline_path.exists():
        return []
    try:
        baseline = load_baseline(baseline_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [
            Violation(
                check="deterministic_writer",
                id="deterministic_writer:baseline_unreadable",
                path=_relative(baseline_path, root),
                line=1,
                message="Could not read sanctioned deterministic writer allowlist",
                detail={"error": exc.__class__.__name__},
            )
        ]

    violations: list[Violation] = []
    rows = baseline.get("sanctioned_deterministic_writers")
    if not isinstance(rows, list) or not rows:
        return [
            Violation(
                check="deterministic_writer",
                id="deterministic_writer:allowlist_missing",
                path=_relative(baseline_path, root),
                line=1,
                message="Architecture baseline must enumerate sanctioned deterministic writers",
                detail={},
            )
        ]

    seen_symbols: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            violations.append(
                Violation(
                    check="deterministic_writer",
                    id=f"deterministic_writer:allowlist_row_not_object:{index}",
                    path=_relative(baseline_path, root),
                    line=index + 1,
                    message="Sanctioned deterministic writer rows must be objects",
                    detail={},
                )
            )
            continue
        missing = sorted(key for key in SANCTIONED_DETERMINISTIC_WRITER_KEYS if not str(row.get(key) or "").strip())
        symbol = str(row.get("symbol") or "").strip()
        if missing or not symbol or symbol in seen_symbols:
            violations.append(
                Violation(
                    check="deterministic_writer",
                    id=f"deterministic_writer:invalid_allowlist_row:{index}",
                    path=_relative(baseline_path, root),
                    line=index + 1,
                    message="Sanctioned deterministic writer row is missing required detail or duplicates a symbol",
                    detail={"missing": ",".join(missing), "symbol": symbol},
                )
            )
        seen_symbols.add(symbol)

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


def collect_violations(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(check_upward_imports(root))
    violations.extend(check_flat_files(root))
    violations.extend(check_markdown_links(root))
    violations.extend(check_cleanup_truth(root))
    violations.extend(check_prd_index(root))
    violations.extend(check_deterministic_semantic_writers(root))
    return sorted(violations, key=lambda v: (v.check, v.id))


def make_baseline(root: Path, violations: list[Violation]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": ".",
        "violation_ids": [v.id for v in violations],
        "violations": [v.to_json() for v in violations],
        "sanctioned_deterministic_writers": SANCTIONED_DETERMINISTIC_WRITERS,
    }


def _compat_counts(violations: list[Violation]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for violation in violations:
        surface_key = violation.detail.get("surface_key", "unknown")
        surface_counts = counts.setdefault(surface_key, {})
        surface_counts[violation.path] = surface_counts.get(violation.path, 0) + 1
    return {
        surface: dict(sorted(path_counts.items()))
        for surface, path_counts in sorted(counts.items())
    }


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
        "surface_totals": {
            surface: sum(path_counts.values()) for surface, path_counts in counts.items()
        },
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
    for check in ["upward_import", "flat_file", "markdown_link", "cleanup_truth", "prd_index", "deterministic_writer"]:
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
            print(
                f"- [{violation.detail.get('surface_key')}] "
                f"{violation.path}:{violation.line} {violation.message}"
            )



def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root_from_script())
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--fail-on-new", action="store_true")
    parser.add_argument("--compat-baseline", type=Path, default=DEFAULT_COMPAT_BASELINE)
    parser.add_argument("--write-compat-baseline", type=Path)
    parser.add_argument("--fail-on-new-compat", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    root = args.root.resolve()
    violations = collect_violations(root)
    run_compat_guard = bool(args.write_compat_baseline or args.fail_on_new_compat)
    compat_violations = check_compat_surface_usage(root) if run_compat_guard else []

    if args.write_baseline:
        baseline_path = args.write_baseline
        if not baseline_path.is_absolute():
            baseline_path = root / baseline_path
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(make_baseline(root, violations), indent=2, sort_keys=True) + "\n",
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
