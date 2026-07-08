"""Report pytest skip/marker drift for the Core Memory test suite.

The guard is intentionally narrow. It does not try to eliminate every skip; it
keeps optional backend and live integration skips classified, requires explicit
reason text for new skips/xfails, and prevents marker descriptions from drifting
back into stale cleanup-removal language.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "core_memory.test_suite_health.v1"

OPTIONAL_BACKEND_RULES = (
    ("qdrant", re.compile(r"\bqdrant(?:-client)?\b.*\bnot installed\b", re.IGNORECASE), ("optional_backend", "qdrant")),
    ("kuzu", re.compile(r"\bkuzu\b.*\bnot installed\b", re.IGNORECASE), ("optional_backend", "kuzu")),
    (
        "neo4j_pkg",
        re.compile(r"\bneo4j package\b.*\bnot installed\b", re.IGNORECASE),
        ("optional_backend", "neo4j_pkg"),
    ),
)

LIVE_BACKEND_RULES = (
    (
        "neo4j_live",
        re.compile(r"\bNEO4J_URI\b.*\bnot set\b|\blive Neo4j\b", re.IGNORECASE),
        ("neo4j_live",),
    ),
)

STALE_MARKER_RE = re.compile(
    r"\b(targeted for|removal target|delete marker|deletion marker|retire marker|cleanup-removal)\b",
    re.IGNORECASE,
)


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


def _line_for_node(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 1) or 1)


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _string_value(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{...}")
        return "".join(parts)
    return None


def _keyword_arg(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _reason_for_call(call: ast.Call, name: str) -> str | None:
    if name in {"unittest.skip", "pytest.mark.skip", "pytest.skip", "pytest.xfail"}:
        return _string_value(_keyword_arg(call, "reason")) or (
            _string_value(call.args[0]) if call.args else None
        )
    if name in {"unittest.skipIf", "unittest.skipUnless", "pytest.mark.skipif", "pytest.mark.xfail"}:
        return _string_value(_keyword_arg(call, "reason")) or (
            _string_value(call.args[1]) if len(call.args) > 1 else None
        )
    if name.endswith(".skipTest"):
        return _string_value(call.args[0]) if call.args else None
    return None


def _is_skip_or_xfail_call(name: str) -> bool:
    return name in {
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "pytest.skip",
        "pytest.xfail",
    } or name.endswith(".skipTest")


def _is_reasonless_marker_attribute(node: ast.AST) -> bool:
    return _call_name(node) in {"pytest.mark.skip", "pytest.mark.xfail"}


def _markers_in_text(text: str) -> set[str]:
    return set(re.findall(r"pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)", text))


def _missing_markers(text: str, required: Iterable[str]) -> list[str]:
    markers = _markers_in_text(text)
    return [marker for marker in required if marker not in markers]


def _iter_test_files(root: Path) -> Iterable[Path]:
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return ()
    return sorted(tests_dir.rglob("test_*.py"))


def check_skip_reason_text(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_test_files(root):
        rel = _relative(path, root)
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for decorator in node.decorator_list:
                    if not _is_reasonless_marker_attribute(decorator):
                        continue
                    line = _line_for_node(decorator)
                    violations.append(
                        Violation(
                            check="skip_reason",
                            id=f"skip_reason:{rel}:{line}",
                            path=rel,
                            line=line,
                            message="skip/xfail marker must include explicit reason text",
                            detail={"call": _call_name(decorator)},
                        )
                    )
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if not _is_skip_or_xfail_call(name):
                continue
            reason = _reason_for_call(node, name)
            if reason and reason.strip():
                continue
            line = _line_for_node(node)
            violations.append(
                Violation(
                    check="skip_reason",
                    id=f"skip_reason:{rel}:{line}",
                    path=rel,
                    line=line,
                    message="skip/xfail call must include explicit reason text",
                    detail={"call": name},
                )
            )
    return violations


def check_backend_skip_markers(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_test_files(root):
        rel = _relative(path, root)
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if not _is_skip_or_xfail_call(name):
                continue
            reason = _reason_for_call(node, name) or ""
            for rule_key, pattern, required in (*OPTIONAL_BACKEND_RULES, *LIVE_BACKEND_RULES):
                if not pattern.search(reason):
                    continue
                missing = _missing_markers(text, required)
                if not missing:
                    continue
                line = _line_for_node(node)
                violations.append(
                    Violation(
                        check="backend_skip_marker",
                        id=f"backend_skip_marker:{rule_key}:{rel}:{line}",
                        path=rel,
                        line=line,
                        message="backend skip must carry the matching pytest marker(s)",
                        detail={
                            "reason": reason,
                            "missing_markers": ",".join(missing),
                            "rule": rule_key,
                        },
                    )
                )
    return violations


def check_marker_descriptions(root: Path) -> list[Violation]:
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    violations: list[Violation] = []
    rel = _relative(path, root)
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if '"facade:' not in line and '"mixin_assembly:' not in line:
            continue
        if not STALE_MARKER_RE.search(line):
            continue
        violations.append(
            Violation(
                check="marker_description",
                id=f"marker_description:{rel}:{line_no}",
                path=rel,
                line=line_no,
                message="facade/mixin_assembly marker descriptions must not imply cleanup-removal status",
                detail={"line": line.strip()},
            )
        )
    return violations


def run_checks(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(check_skip_reason_text(root))
    violations.extend(check_backend_skip_markers(root))
    violations.extend(check_marker_descriptions(root))
    return sorted(violations, key=lambda v: (v.path, v.line, v.check, v.id))


def _print_report(violations: list[Violation]) -> None:
    counts: dict[str, int] = {}
    for violation in violations:
        counts[violation.check] = counts.get(violation.check, 0) + 1

    print("Test suite health report")
    print("========================")
    for check in ("skip_reason", "backend_skip_marker", "marker_description"):
        print(f"{check}: {counts.get(check, 0)}")
    print(f"total: {len(violations)}")
    if violations:
        print()
        for violation in violations:
            print(f"- {violation.check} {violation.path}:{violation.line} {violation.message}")
            if violation.detail:
                detail = ", ".join(f"{key}={value}" for key, value in sorted(violation.detail.items()))
                print(f"  {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root_from_script())
    parser.add_argument("--json", action="store_true", help="emit machine-readable report")
    parser.add_argument(
        "--fail-on-violations",
        action="store_true",
        help="exit non-zero when any test-suite health violation is detected",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    violations = run_checks(root)
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "violations": [violation.to_json() for violation in violations],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_report(violations)

    return 1 if args.fail_on_violations and violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
