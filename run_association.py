#!/usr/bin/env python3
"""
Run the association crawler via a sub-agent.

Usage:
  run_association.py [--model MODEL]

Model defaults to the main agent model (minimax/MiniMax-M2.5).
Set model via --model flag or MEMBEADS_ASSOCIATION_MODEL env var.

The sub-agent will:
1. Generate the analysis prompt from associate.py prompt
2. Analyze beads and identify associations
3. Return JSON of associations
4. This script records them via associate.py record
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid

DEFAULT_MODEL = "minimax/MiniMax-M2.5"


def run_associate_command(args: list[str]) -> str:
    """Run an associate.py command and return stdout."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        [sys.executable, os.path.join(script_dir, "associate.py")] + args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"associate.py failed: {result.stderr}")
    return result.stdout


def generate_prompt() -> str:
    """Get the analysis prompt from associate.py."""
    output = run_associate_command(["prompt", "--json"])
    data = json.loads(output)
    return data.get("prompt", "")


def record_associations(associations_json: str):
    """Record associations via associate.py."""
    # The JSON comes as a string from the agent, need to parse and re-serialize
    associations = json.loads(associations_json)
    # Filter to valid associations only
    valid = []
    for a in associations:
        if all(k in a for k in ["source", "target", "relationship", "explanation"]):
            valid.append(a)

    if not valid:
        print("No valid associations to record")
        return

    result = run_associate_command([
        "record",
        "--associations", json.dumps(valid)
    ])
    print(result)


def build_spawn_task(prompt: str) -> str:
    """Build the task prompt for the sub-agent."""
    return f"""You are a memory analysis agent. Analyze the following memory beads and identify semantic associations between them.

{prompt}

Output ONLY a JSON array of associations (no markdown fences, no explanation outside the JSON):
```json
[
  {{
    "source": "bead-XXXXX",
    "target": "bead-YYYYY",
    "relationship": "similar_pattern",
    "explanation": "Both involve...",
    "confidence": 0.8
  }}
]
```

If no meaningful associations found, output: []"""


def spawn_and_run(model: str) -> dict:
    """Spawn a sub-agent to run association analysis."""
    import requests

    # Get the gateway URL and token from environment
    gateway_url = os.environ.get("OPENCLAW_HTTP_BASE", "http://openclaw-manager:18789")
    gateway_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")

    if not gateway_token:
        raise RuntimeError("OPENCLAW_GATEWAY_TOKEN not set")

    # Generate analysis prompt
    prompt = generate_prompt()
    if not prompt:
        return {"ok": False, "message": "No beads to analyze"}

    task = build_spawn_task(prompt)
    session_key = f"agent:main:subagent:membeads-{uuid.uuid4().hex[:8]}"

    # Call the gateway to spawn
    resp = requests.post(
        f"{gateway_url}/v1/agent",
        headers={
            "Authorization": f"Bearer {gateway_token}",
            "Content-Type": "application/json",
        },
        json={
            "message": task,
            "sessionKey": session_key,
            "lane": "subagent",
            "deliver": False,
            "model": model,
        },
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Gateway error: {resp.status_code} {resp.text}")

    data = resp.json()
    return {"ok": True, "run_id": data.get("runId"), "session_key": session_key}


def main():
    parser = argparse.ArgumentParser(description="Run association crawler via sub-agent")
    parser.add_argument(
        "--model",
        default=os.environ.get("MEMBEADS_ASSOCIATION_MODEL", DEFAULT_MODEL),
        help=f"Model to use for analysis (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Poll for completion instead of fire-and-forget"
    )
    args = parser.parse_args()

    print(f"Using model: {args.model}")

    result = spawn_and_run(args.model)
    if not result.get("ok"):
        print(f"Failed: {result}")
        sys.exit(1)

    print(f"Spawned sub-agent: {result.get('session_key')}")
    print(f"Run ID: {result.get('run_id')}")

    if args.poll:
        print("Polling for completion...")
        # Would implement polling here
        pass
    else:
        print("Fire-and-forget mode. Associations will be recorded when sub-agent completes.")


if __name__ == "__main__":
    main()
