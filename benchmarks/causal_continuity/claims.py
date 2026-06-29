from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLAIM_CERTIFICATE_SCHEMA = "causal_continuity.claim_certificate.v1"
EVIDENCE_MANIFEST_SCHEMA = "causal_continuity.evidence_manifest.v1"

CLAIM_SCOPES: dict[str, dict[str, Any]] = {
    "local_fixture": {
        "label": "Deterministic local fixture evidence",
        "required_gates": ["local_fixture_claim_ready"],
        "tier": "local_deterministic",
        "claim_kind": "local_deterministic",
    },
    "provider_backed_comparison": {
        "label": "Provider-backed comparator evidence",
        "required_gates": ["local_fixture_claim_ready", "provider_backed_comparison_ready"],
        "tier": "configured_adapter",
        "claim_kind": "external_comparison",
    },
    "real_data_leaderboard": {
        "label": "External-corpus leaderboard evidence",
        "required_gates": ["local_fixture_claim_ready", "real_data_leaderboard_ready"],
        "tier": "real_data_external",
        "claim_kind": "external_corpus",
    },
    "t5_llm_judge_primary": {
        "label": "T5 LLM-judge primary evidence",
        "required_gates": ["local_fixture_claim_ready", "t5_llm_judge_primary_claim_ready"],
        "tier": "t5_judge",
        "claim_kind": "llm_judge",
    },
}


def _source_commit(report: dict[str, Any]) -> str:
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    return str(metadata.get("commit") or report.get("source_commit") or "")


def _tier_statuses(manifest: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    tiers = manifest.get("tiers") if isinstance(manifest.get("tiers"), dict) else {}
    for name, tier in sorted(tiers.items()):
        if isinstance(tier, dict):
            out[str(name)] = str(tier.get("status") or "")
    return out


def _tier_blockers(manifest: dict[str, Any], tier_name: str) -> list[str]:
    tiers = manifest.get("tiers") if isinstance(manifest.get("tiers"), dict) else {}
    tier = tiers.get(tier_name) if isinstance(tiers.get(tier_name), dict) else {}
    return [str(x) for x in list(tier.get("blockers") or []) if str(x).strip()]


def _parse_required_scopes(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if raw is None:
        return ["local_fixture"]
    if isinstance(raw, str):
        values = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        values = [str(x).strip() for x in raw if str(x).strip()]
    if not values:
        return ["local_fixture"]
    if "all" in values:
        return list(CLAIM_SCOPES)
    return values


def _scope_result(scope: str, manifest: dict[str, Any], *, schema_ok: bool) -> dict[str, Any]:
    gates = manifest.get("claim_gates") if isinstance(manifest.get("claim_gates"), dict) else {}
    spec = CLAIM_SCOPES.get(scope)
    if not spec:
        return {
            "scope": scope,
            "status": "unknown",
            "passed": False,
            "required": True,
            "required_gates": [],
            "failed_gates": [],
            "blockers": [f"unknown_claim_scope:{scope}"],
        }

    required_gates = [str(x) for x in list(spec.get("required_gates") or [])]
    failed_gates = [gate for gate in required_gates if not bool(gates.get(gate))]
    blockers: list[str] = []
    if not schema_ok:
        blockers.append("invalid_or_missing_evidence_manifest")
    blockers.extend(_tier_blockers(manifest, str(spec.get("tier") or "")))
    blockers.extend(f"gate_closed:{gate}" for gate in failed_gates)
    passed = schema_ok and not failed_gates
    return {
        "scope": scope,
        "label": str(spec.get("label") or scope),
        "claim_kind": str(spec.get("claim_kind") or ""),
        "status": "ready" if passed else "blocked",
        "passed": passed,
        "required": True,
        "required_gates": required_gates,
        "failed_gates": failed_gates,
        "tier": str(spec.get("tier") or ""),
        "tier_status": _tier_statuses(manifest).get(str(spec.get("tier") or ""), ""),
        "blockers": sorted(set(blockers)),
    }


def build_claim_certificate(
    report: dict[str, Any],
    *,
    required_scopes: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build a deterministic certificate for the report's publishable claim scope."""

    manifest = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    schema = str(manifest.get("schema_version") or "")
    schema_ok = schema == EVIDENCE_MANIFEST_SCHEMA
    requested = _parse_required_scopes(required_scopes)
    scope_results = {
        scope: _scope_result(scope, manifest, schema_ok=schema_ok)
        for scope in requested
    }
    all_scope_results = {
        scope: _scope_result(scope, manifest, schema_ok=schema_ok)
        for scope in CLAIM_SCOPES
    }
    supported_scopes = [
        scope
        for scope, result in all_scope_results.items()
        if bool(result.get("passed"))
    ]
    blocked_scopes = {
        scope: list(result.get("blockers") or [])
        for scope, result in all_scope_results.items()
        if not bool(result.get("passed"))
    }
    checks = [
        {
            "id": "evidence_manifest_schema",
            "passed": schema_ok,
            "expected": EVIDENCE_MANIFEST_SCHEMA,
            "actual": schema,
        },
        *[
            {
                "id": f"scope:{scope}",
                "passed": bool(result.get("passed")),
                "status": str(result.get("status") or ""),
                "failed_gates": list(result.get("failed_gates") or []),
                "blockers": list(result.get("blockers") or []),
            }
            for scope, result in scope_results.items()
        ],
    ]
    passed = schema_ok and all(bool(row.get("passed")) for row in scope_results.values())
    return {
        "schema_version": CLAIM_CERTIFICATE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_schema_version": str(report.get("schema_version") or ""),
        "report_commit": _source_commit(report),
        "requested_scopes": requested,
        "passed": passed,
        "status": "ready" if passed else "blocked",
        "supported_scopes": supported_scopes,
        "blocked_scopes": blocked_scopes,
        "tier_statuses": _tier_statuses(manifest),
        "gates": dict(manifest.get("claim_gates") or {}) if isinstance(manifest.get("claim_gates"), dict) else {},
        "scopes": scope_results,
        "checks": checks,
        "notes": [
            "certificate_reads_existing_report_only",
            "local_fixture_scope_does_not_imply_provider_or_leaderboard_claims",
            "blocked_external_scopes_require_configured_evidence_not_code_changes",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify causal-continuity report claim readiness")
    parser.add_argument("--report", required=True, help="Path to a causal-continuity suite report JSON")
    parser.add_argument(
        "--require",
        default="local_fixture",
        help="Comma-separated claim scopes to require, or 'all'. Default: local_fixture",
    )
    parser.add_argument("--out", default="", help="Optional path to write the certificate JSON")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--allow-blocked", action="store_true", help="Exit 0 even when requested scopes are blocked")
    args = parser.parse_args()

    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    certificate = build_claim_certificate(report, required_scopes=str(args.require or "local_fixture"))
    text = json.dumps(certificate, indent=2 if args.pretty else None)
    print(text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(certificate, indent=2), encoding="utf-8")
    if not bool(certificate.get("passed")) and not bool(args.allow_blocked):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
