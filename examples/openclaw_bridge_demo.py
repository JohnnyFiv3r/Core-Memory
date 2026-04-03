"""OpenClaw bridge demo — stdin/stdout JSON dispatch for read + write.

This demonstrates both the write bridge (agent-end) and read bridge (search,
trace, execute, continuity) using the same stdin/stdout JSON protocol that OpenClaw
hooks use.

Run:
    PYTHONPATH=. python examples/openclaw_bridge_demo.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _run_bridge(module: str, payload: dict) -> dict:
    """Run a bridge module via subprocess, mimicking OpenClaw hook dispatch."""
    result = subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"  stderr: {result.stderr[:500]}", file=sys.stderr)
    return json.loads(result.stdout) if result.stdout.strip() else {"error": "no_output"}


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = str(Path(td) / "memory")
        print(f"Using temp memory root: {root}\n")

        # --- 1. Write: emit a turn via the agent-end bridge ---
        print("=== Write Bridge (agent-end) ===")
        write_payload = {
            "root": root,
            "event": {
                "messages": [
                    {"role": "user", "content": "Why did we choose event-sourcing?"},
                    {
                        "role": "assistant",
                        "content": (
                            "We chose event-sourcing because it gives us a complete audit trail "
                            "and allows rebuilding projections from events. This was decided during "
                            "the architecture review after the data-loss incident."
                        ),
                    },
                ],
                "success": True,
            },
            "ctx": {
                "sessionId": "demo-openclaw-001",
                "agentId": "demo-agent",
            },
        }
        write_result = _run_bridge("core_memory.integrations.openclaw_agent_end_bridge", write_payload)
        print(f"  emitted: {write_result.get('emitted')}")
        print(f"  session: {write_result.get('session_id')}")
        print(f"  turn: {write_result.get('turn_id')}")

        # --- 2. Read: search via the read bridge ---
        print("\n=== Read Bridge (search) ===")
        search_result = _run_bridge(
            "core_memory.integrations.openclaw_read_bridge",
            {"action": "search", "query": "event-sourcing", "root": root},
        )
        results = search_result.get("results") or []
        print(f"  hits: {len(results)}")
        for r in results[:3]:
            print(f"    - [{r.get('type')}] {r.get('title')}")

        # --- 3. Read: causal trace ---
        print("\n=== Read Bridge (trace) ===")
        trace_result = _run_bridge(
            "core_memory.integrations.openclaw_read_bridge",
            {"action": "trace", "query": "why event-sourcing?", "root": root},
        )
        print(f"  ok: {trace_result.get('ok')}")
        chains = trace_result.get("chains") or []
        print(f"  chains: {len(chains)}")

        # --- 4. Read: execute ---
        print("\n=== Read Bridge (execute) ===")
        exec_result = _run_bridge(
            "core_memory.integrations.openclaw_read_bridge",
            {
                "action": "execute",
                "query": "why event-sourcing?",
                "root": root,
                "explain": True,
            },
        )
        print(f"  ok: {exec_result.get('ok')}")
        print(f"  results: {len(exec_result.get('results', []))}")

        # --- 5. Read: continuity injection ---
        print("\n=== Read Bridge (continuity) ===")
        cont_result = _run_bridge(
            "core_memory.integrations.openclaw_read_bridge",
            {"action": "continuity", "root": root, "max_items": 10},
        )
        print(f"  authority: {cont_result.get('authority')}")
        print(f"  records: {len(cont_result.get('records', []))}")

        print("\nDone! All OpenClaw bridge operations exercised successfully.")


if __name__ == "__main__":
    main()
