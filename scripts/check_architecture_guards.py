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
DEFAULT_BASELINE = Path("scripts/architecture_guards_baseline.json")

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

STALE_TRUTH_WORDS = re.compile(r"\b(deleted|removed|retired|done|complete)\b", re.IGNORECASE)
SAFE_TRUTH_WORDS = re.compile(
    r"\b(active|classify|classification|classify-not-delete|do not delete|not deleted|"
    r"not as deleted|pending classification|retained|truth-audit)\b",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"(!?\[[^\]]*\]\(([^)]+)\))")


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


def check_cleanup_truth(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    existing_debt = {
        path for path in ACTIVE_CLEANUP_DEBT_PATHS if (root / path).exists()
    }
    for doc in sorted(TRUTH_DOCS):
        path = root / doc
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for active_path in sorted(existing_debt):
                if active_path not in line:
                    continue
                if not _line_is_stale_truth_claim(line):
                    continue
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
    return sorted(violations, key=lambda v: v.id)


def collect_violations(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(check_upward_imports(root))
    violations.extend(check_flat_files(root))
    violations.extend(check_markdown_links(root))
    violations.extend(check_cleanup_truth(root))
    return sorted(violations, key=lambda v: (v.check, v.id))


def make_baseline(root: Path, violations: list[Violation]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": ".",
        "violation_ids": [v.id for v in violations],
        "violations": [v.to_json() for v in violations],
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
    for check in ["upward_import", "flat_file", "markdown_link", "cleanup_truth"]:
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root_from_script())
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--fail-on-new", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    root = args.root.resolve()
    violations = collect_violations(root)

    if args.write_baseline:
        baseline_path = args.write_baseline
        if not baseline_path.is_absolute():
            baseline_path = root / baseline_path
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(make_baseline(root, violations), indent=2, sort_keys=True) + "\n",
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

    if args.json_output:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "violations": [v.to_json() for v in violations],
            "new_violations": [v.to_json() for v in new or []],
            "resolved_violation_ids": resolved or [],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_report(violations, new=new, resolved=resolved)

    if args.fail_on_new and new:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
