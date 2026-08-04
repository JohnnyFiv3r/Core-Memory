#!/usr/bin/env python3
"""Read-only legacy Core Memory inventory and consolidation CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_memory.migration.consolidation import (  # noqa: E402
    load_authority_registry,
    load_inventory_report,
    merge_inventory_reports,
    verify_consolidated_manifest,
)
from core_memory.migration.inventory import (  # noqa: E402
    InventoryReport,
    scan_code,
    scan_filesystem,
    validate_code_output_path,
    validate_output_path,
)
from core_memory.migration.sql_inventory import (  # noqa: E402
    scan_postgresql,
    scan_sqlite,
    validate_sql_output_path,
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
    filesystem.add_argument("--locator-prefix", default="")
    filesystem.add_argument("--classification", type=Path)
    filesystem.add_argument("--out", type=Path, required=True)

    sql = subparsers.add_parser("scan-sql", help="Inventory SQLite or PostgreSQL through a read-only connection")
    sql.add_argument("--engine", choices=("sqlite", "postgresql"), required=True)
    sql.add_argument("--sqlite-path", type=Path)
    sql.add_argument("--dsn-env")
    sql.add_argument("--schema", action="append", default=[])
    sql.add_argument("--source-label", required=True)
    sql.add_argument("--tenant-workspace", required=True)
    sql.add_argument("--classification", type=Path)
    sql.add_argument("--out", type=Path, required=True)

    merge = subparsers.add_parser("merge", help="Reconcile inventory reports against the known-authority registry")
    merge.add_argument("--registry", type=Path, required=True)
    merge.add_argument("--report", type=Path, action="append", required=True)
    merge.add_argument("--out", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="Verify a consolidated manifest is complete and uncontaminated")
    verify.add_argument("--manifest", type=Path, required=True)
    return parser


def _write_report(path: Path, report: InventoryReport) -> None:
    _write_payload(path, report.to_dict())


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _explicit_output(path: Path, *, inputs: list[Path]) -> Path:
    if not path.is_absolute():
        raise ValueError("inventory_output_must_be_absolute")
    resolved = path.resolve()
    if any(resolved == item.resolve() for item in inputs):
        raise ValueError("inventory_output_overwrites_input")
    return resolved


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
        elif args.command == "scan-filesystem":
            output = validate_output_path(args.out, scan_root=args.root)
            report = scan_filesystem(
                args.root,
                workspace_root=args.workspace_root,
                source_label=args.source_label,
                tenant_workspace_classification=args.tenant_workspace,
                classification_path=args.classification,
                locator_prefix=args.locator_prefix,
            )
        elif args.command == "scan-sql":
            if args.engine == "sqlite":
                if args.sqlite_path is None or args.dsn_env:
                    raise ValueError("sqlite_scan_requires_only_sqlite_path")
                output = validate_sql_output_path(args.out, sqlite_path=args.sqlite_path)
                report = scan_sqlite(
                    args.sqlite_path,
                    source_label=args.source_label,
                    tenant_workspace_classification=args.tenant_workspace,
                    classification_path=args.classification,
                )
            else:
                if args.sqlite_path is not None or not str(args.dsn_env or "").strip():
                    raise ValueError("postgresql_scan_requires_only_dsn_env")
                dsn = str(os.environ.get(args.dsn_env) or "").strip()
                if not dsn:
                    raise ValueError("postgresql_dsn_environment_missing")
                output = validate_sql_output_path(args.out)
                report = scan_postgresql(
                    dsn,
                    source_label=args.source_label,
                    tenant_workspace_classification=args.tenant_workspace,
                    schemas=args.schema,
                    classification_path=args.classification,
                )
        elif args.command == "merge":
            output = _explicit_output(args.out, inputs=[args.registry, *args.report])
            registry = load_authority_registry(args.registry)
            payload = merge_inventory_reports(registry, [load_inventory_report(path) for path in args.report])
            _write_payload(output, payload)
            print(json.dumps({"status": "completed", "complete": payload["complete"], "out": str(output)}))
            return 0 if payload["complete"] else 1
        else:
            if not args.manifest.is_absolute():
                raise ValueError("inventory_manifest_path_must_be_absolute")
            payload = json.loads(args.manifest.read_text(encoding="utf-8"))
            errors = verify_consolidated_manifest(payload)
            print(json.dumps({"status": "completed" if not errors else "failed", "errors": errors}, sort_keys=True))
            return 0 if not errors else 1
    except (ImportError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    _write_report(output, report)
    print(json.dumps({"status": "completed", "complete": report.complete, "out": str(output)}, sort_keys=True))
    return 0 if report.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
