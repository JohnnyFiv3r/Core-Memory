#!/usr/bin/env python3
"""Read-only legacy Core Memory code and filesystem inventory CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_memory.migration.inventory import (  # noqa: E402
    InventoryReport,
    scan_code,
    scan_filesystem,
    validate_code_output_path,
    validate_output_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/inventory_legacy_memory.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    code = subparsers.add_parser("scan-code", help="Inventory static code authorities without executing code")
    code.add_argument("--root", type=Path, required=True)
    code.add_argument("--include", type=Path, action="append", required=True)
    code.add_argument("--exclude", type=Path, action="append", default=[])
    code.add_argument("--source-label", required=True)
    code.add_argument("--tenant-workspace", required=True)
    code.add_argument("--locator-prefix", default="")
    code.add_argument("--classification", type=Path)
    code.add_argument("--out", type=Path, required=True)

    filesystem = subparsers.add_parser("scan-filesystem", help="Inventory a bounded filesystem data root")
    filesystem.add_argument("--root", type=Path, required=True)
    filesystem.add_argument("--workspace-root", type=Path, required=True)
    filesystem.add_argument("--source-label", required=True)
    filesystem.add_argument("--tenant-workspace", required=True)
    filesystem.add_argument("--classification", type=Path)
    filesystem.add_argument("--out", type=Path, required=True)
    return parser


def _write_report(path: Path, report: InventoryReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan-code":
            output = validate_code_output_path(args.out, code_root=args.root, includes=args.include)
            report = scan_code(
                args.root,
                includes=args.include,
                excludes=args.exclude,
                source_label=args.source_label,
                tenant_workspace_classification=args.tenant_workspace,
                classification_path=args.classification,
                locator_prefix=args.locator_prefix,
            )
        else:
            output = validate_output_path(args.out, scan_root=args.root)
            report = scan_filesystem(
                args.root,
                workspace_root=args.workspace_root,
                source_label=args.source_label,
                tenant_workspace_classification=args.tenant_workspace,
                classification_path=args.classification,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    _write_report(output, report)
    print(json.dumps({"status": "completed", "complete": report.complete, "out": str(output)}, sort_keys=True))
    return 0 if report.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
