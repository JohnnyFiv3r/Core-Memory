"""Audit script for Phase 5: classify each *_for_store function as STATEFUL / STATELESS / PARTIAL.

Usage:
    python scripts/audit_store_delegation.py [--md]

Output:
    Plain table to stdout (default).
    Markdown table when --md is passed.
    Exit code 0 always (informational only).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PERSISTENCE_DIR = Path(__file__).resolve().parent.parent / "core_memory" / "persistence"


def _store_usages_in_body(func: ast.FunctionDef) -> list[str]:
    """Return all AST contexts in which the name 'store' appears in the function body."""
    usages: list[str] = []
    # We skip the args node itself — we only care about the body.
    for node in ast.walk(ast.Module(body=func.body, type_ignores=[])):
        if isinstance(node, ast.Name) and node.id == "store":
            usages.append("name_ref")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "store":
            usages.append(f"attr:{node.attr}")
    return usages


def _verdict(usages: list[str]) -> str:
    if not usages:
        return "STATELESS"
    # If every usage is a simple Name ref (passed to another function), it's
    # worth distinguishing from attribute access on store.
    has_attr = any(u.startswith("attr:") for u in usages)
    has_name = any(u == "name_ref" for u in usages)
    if has_attr:
        return "STATEFUL"
    if has_name:
        # store appears but only as a bare name — likely passed to another fn.
        return "PARTIAL"
    return "STATELESS"


def _analyze_file(path: Path) -> list[dict]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [{"file": path.name, "function": "PARSE_ERROR", "verdict": str(exc), "usages": []}]

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.endswith("_for_store"):
            continue
        # Confirm 'store' is actually a parameter name
        param_names = [a.arg for a in node.args.args]
        if "store" not in param_names:
            continue
        usages = _store_usages_in_body(node)
        results.append({
            "file": path.name,
            "function": node.name,
            "verdict": _verdict(usages),
            "usages": usages,
        })
    return results


def main() -> None:
    md_mode = "--md" in sys.argv

    all_rows: list[dict] = []
    for path in sorted(PERSISTENCE_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        rows = _analyze_file(path)
        all_rows.extend(rows)

    col_fn = max((len(r["function"]) for r in all_rows), default=8)
    col_fi = max((len(r["file"]) for r in all_rows), default=8)
    col_vd = max(len("VERDICT"), max((len(r["verdict"]) for r in all_rows), default=8))

    stateless = [r for r in all_rows if r["verdict"] == "STATELESS"]
    stateful  = [r for r in all_rows if r["verdict"] == "STATEFUL"]
    partial   = [r for r in all_rows if r["verdict"] == "PARTIAL"]

    if md_mode:
        print("# Store Delegation Audit\n")
        print(f"| {'Function':<{col_fn}} | {'File':<{col_fi}} | {'Verdict':<{col_vd}} | Notes |")
        print(f"|{'-'*(col_fn+2)}|{'-'*(col_fi+2)}|{'-'*(col_vd+2)}|-------|")
        for r in all_rows:
            attrs = [u for u in r["usages"] if u.startswith("attr:")]
            note = ", ".join(sorted(set(attrs)))[:80] if attrs else ("passes store" if r["usages"] else "")
            print(f"| {r['function']:<{col_fn}} | {r['file']:<{col_fi}} | {r['verdict']:<{col_vd}} | {note} |")
        print(f"\n**Summary:** {len(stateless)} STATELESS, {len(stateful)} STATEFUL, {len(partial)} PARTIAL out of {len(all_rows)} total\n")
    else:
        hdr = f"{'FUNCTION':<{col_fn}}  {'FILE':<{col_fi}}  {'VERDICT':<{col_vd}}  NOTES"
        print(hdr)
        print("-" * len(hdr))
        for r in all_rows:
            attrs = [u for u in r["usages"] if u.startswith("attr:")]
            note = ", ".join(sorted(set(attrs)))[:80] if attrs else ("passes_store_as_arg" if r["usages"] else "")
            print(f"{r['function']:<{col_fn}}  {r['file']:<{col_fi}}  {r['verdict']:<{col_vd}}  {note}")
        print()
        print(f"Summary: {len(stateless)} STATELESS  {len(stateful)} STATEFUL  {len(partial)} PARTIAL  ({len(all_rows)} total)")

    print()
    if stateless:
        print("STATELESS (safe to remove store param):")
        for r in stateless:
            print(f"  {r['file']}::{r['function']}")
    if partial:
        print()
        print("PARTIAL (review manually):")
        for r in partial:
            note = [u for u in r["usages"] if u == "name_ref"]
            print(f"  {r['file']}::{r['function']}  [{len(note)} bare name refs]")


if __name__ == "__main__":
    main()
