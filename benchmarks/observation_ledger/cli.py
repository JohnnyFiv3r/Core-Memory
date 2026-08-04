"""Command-line interface for Observation Ledger evaluation packs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .models import RunStatus, RuntimeAdapter
from .pack import validate_pack
from .providers import ModelRegistry
from .runner import adjudicate_pack, build_baseline, run_pack


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("document_not_object")
    return payload


def _status_exit(payload: dict[str, Any]) -> int:
    status = str(payload.get("status") or "")
    if status == RunStatus.COMPLETED.value:
        return 0
    if status == RunStatus.BLOCKED.value:
        return 2
    if status == RunStatus.DISQUALIFIED.value:
        return 3
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.observation_ledger")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a pack without model or network access")
    validate.add_argument("--pack", type=Path, required=True)
    validate.add_argument("--visibility", choices=("public", "private"), required=True)

    adjudicate = subparsers.add_parser("adjudicate", help="Generate judge critiques for human adjudication")
    adjudicate.add_argument("--pack", type=Path, required=True)
    adjudicate.add_argument("--visibility", choices=("public", "private"), default="public")
    adjudicate.add_argument("--judge-model")
    adjudicate.add_argument("--out", type=Path, required=True)

    run = subparsers.add_parser("run", help="Run author/runtime output and independent judge scoring")
    run.add_argument("--pack", type=Path, required=True)
    run.add_argument("--visibility", choices=("public", "private"), default="public")
    run.add_argument("--author-model")
    run.add_argument("--judge-model")
    run.add_argument("--out", type=Path, required=True)

    baseline = subparsers.add_parser("baseline", help="Apply approved gates to public/private reports")
    baseline.add_argument("--public-report", type=Path, required=True)
    baseline.add_argument("--private-report", type=Path, required=True)
    baseline.add_argument("--thresholds", type=Path, required=True)
    baseline.add_argument("--out", type=Path, required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    registry: ModelRegistry | None = None,
    runtime: RuntimeAdapter | None = None,
    repo_root: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    root = (repo_root or _repo_root()).resolve()

    if args.command == "validate":
        validation = validate_pack(
            args.pack,
            visibility=args.visibility,
            repo_root=root,
            require_accepted=True,
        )
        print(json.dumps(validation.to_dict(), indent=2, sort_keys=True))
        return 0 if validation.valid else 1

    if args.command == "adjudicate":
        if args.visibility == "private":
            try:
                args.out.resolve().relative_to(root)
            except ValueError:
                pass
            else:
                payload = {
                    "schema_version": "observation_ledger.adjudication_run.v1",
                    "status": RunStatus.BLOCKED.value,
                    "status_reason": "private_adjudication_output_inside_repository",
                    "records": [],
                }
                _write_json(args.out, payload)
                return _status_exit(payload)
        payload = adjudicate_pack(
            pack_path=args.pack,
            visibility=args.visibility,
            judge_model=args.judge_model,
            repo_root=root,
            registry=registry,
        )
        _write_json(args.out, payload)
        return _status_exit(payload)

    if args.command == "run":
        payload = run_pack(
            pack_path=args.pack,
            visibility=args.visibility,
            author_model=args.author_model,
            judge_model=args.judge_model,
            repo_root=root,
            registry=registry,
            runtime=runtime,
        )
        _write_json(args.out, payload)
        return _status_exit(payload)

    if args.command == "baseline":
        try:
            payload = build_baseline(
                public_report=_load_json(args.public_report),
                private_report=_load_json(args.private_report),
                thresholds=_load_json(args.thresholds),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"baseline input unreadable: {exc.__class__.__name__}", file=sys.stderr)
            return 1
        _write_json(args.out, payload)
        return _status_exit(payload)

    return 1


__all__ = ["build_parser", "main"]
