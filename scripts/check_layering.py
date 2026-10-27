#!/usr/bin/env python3
"""Enforce the crate layering law (ARC-01).

Each workspace crate may depend only on the workspace crates its layer
allows, and the lower layers may not pull in storage, network or async
runtime crates. Dev-dependencies are exempt. A new crate must match a rule
here before CI passes.
"""

import fnmatch
import json
import subprocess
import sys

# crate name pattern -> workspace crates it may depend on
ALLOWED = {
    "core-memory-contract": [],
    "core-memory-chunker": ["core-memory-contract"],
    "core-memory-core": ["core-memory-contract", "core-memory-chunker"],
    "core-memory-store-*": ["core-memory-contract", "core-memory-core"],
    "core-memory-source-*": ["core-memory-contract", "core-memory-core"],
    "core-memory-sink-*": ["core-memory-contract", "core-memory-core"],
    "core-memory-model-http": ["core-memory-contract", "core-memory-core"],
    "core-memory-model-*": ["core-memory-contract", "core-memory-core", "core-memory-model-http"],
    # Test-only: adapters take it as a dev-dependency.
    "core-memory-conformance": ["core-memory-contract", "core-memory-core"],
    "core-memory-sidecar": ["*"],
    "core-memory": ["*"],
}

# Third-party crates the lower layers must not use: core stays free of
# storage, transport and runtime choices so adapters can make them.
FORBIDDEN_BELOW_ADAPTERS = [
    "rusqlite", "sqlx", "postgres", "tokio-postgres", "diesel",
    "reqwest", "ureq", "hyper", "axum", "tonic", "tokio", "async-std",
]
LOWER_LAYERS = ["core-memory-contract", "core-memory-chunker", "core-memory-core"]


def rule_for(name):
    if name in ALLOWED:
        return ALLOWED[name]
    for pattern, allowed in ALLOWED.items():
        if fnmatch.fnmatchcase(name, pattern):
            return allowed
    return None


def main():
    meta = json.loads(
        subprocess.check_output(["cargo", "metadata", "--format-version", "1", "--no-deps"])
    )
    workspace = {p["name"] for p in meta["packages"]}
    problems = []
    for package in meta["packages"]:
        name = package["name"]
        allowed = rule_for(name)
        if allowed is None:
            problems.append(f"{name}: no layering rule; add one to scripts/check_layering.py")
            continue
        for dep in package["dependencies"]:
            if dep.get("kind") == "dev":
                continue
            dep_name = dep["name"]
            if dep_name in workspace:
                if "*" not in allowed and dep_name not in allowed:
                    problems.append(f"{name} must not depend on {dep_name}")
            elif name in LOWER_LAYERS and dep_name in FORBIDDEN_BELOW_ADAPTERS:
                problems.append(f"{name} must not depend on {dep_name}; that belongs in an adapter")
    if problems:
        print("Layering violations:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"layering ok ({len(meta['packages'])} crates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
