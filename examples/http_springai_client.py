"""HTTP/SpringAI client example — write + search + continuity via REST API.

Contract Level: Recommended
Audience: HTTP/SpringAI integrators

Prerequisites:
    pip install core-memory[http]
    # Terminal 1: start the server
    uvicorn core_memory.integrations.http.server:app --port 8000
    # Terminal 2: run this script
    PYTHONPATH=. python examples/http_springai_client.py

This example demonstrates the full lifecycle over HTTP:
    1. Emit a turn-finalized event (write path)
    2. Search memory (read path)
    3. Load continuity injection (context for next turn)
"""
from __future__ import annotations

import httpx

BASE = "http://localhost:8000"
ROOT = "./memory"


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=30)

    # --- Health check ---
    health = client.get("/healthz", params={"root": ROOT}).json()
    print(f"Health: {health}")

    # --- 1. Write: emit a turn-finalized event ---
    write_resp = client.post(
        "/v1/memory/turn-finalized",
        json={
            "root": ROOT,
            "session_id": "demo-http-001",
            "turn_id": "t1",
            "user_query": "Why did we choose PostgreSQL over MySQL?",
            "assistant_final": (
                "We chose PostgreSQL because it supports JSONB columns for "
                "semi-structured data and has better support for complex queries "
                "with CTEs. The decision was confirmed in the Q1 architecture review."
            ),
        },
    )
    print(f"\nWrite: {write_resp.json()}")

    # --- 2. Read: search memory ---
    search_resp = client.post(
        "/v1/memory/search",
        json={
            "root": ROOT,
            "form_submission": {
                "query_text": "PostgreSQL decision",
                "k": 5,
            },
            "explain": True,
        },
    )
    data = search_resp.json()
    print(f"\nSearch results ({len(data.get('results', []))} hits):")
    for r in data.get("results", [])[:3]:
        print(f"  - [{r.get('type')}] {r.get('title')} (score: {r.get('score', 'n/a')})")

    # --- 3. Read: causal trace ---
    trace_resp = client.post(
        "/v1/memory/trace",
        json={
            "root": ROOT,
            "query": "why PostgreSQL?",
            "k": 5,
        },
    )
    trace_data = trace_resp.json()
    print(f"\nTrace: ok={trace_data.get('ok')}, chains={len(trace_data.get('chains', []))}")

    # --- 4. Read: execute ---
    exec_resp = client.post(
        "/v1/memory/execute",
        json={
            "root": ROOT,
            "request": {
                "raw_query": "why PostgreSQL?",
                "intent": "causal",
                "k": 5,
            },
            "explain": True,
        },
    )
    exec_data = exec_resp.json()
    print(f"\nExecute: ok={exec_data.get('ok')}, results={len(exec_data.get('results', []))}")

    # --- 5. Read: continuity injection ---
    continuity_resp = client.get(
        "/v1/memory/continuity",
        params={"root": ROOT, "max_items": 10, "format": "json"},
    )
    cont_data = continuity_resp.json()
    print(f"\nContinuity: authority={cont_data.get('authority')}, records={len(cont_data.get('records', []))}")

    # --- 6. Read: continuity as text (for SpringAI system prompt injection) ---
    text_resp = client.get(
        "/v1/memory/continuity",
        params={"root": ROOT, "max_items": 10, "format": "text"},
    )
    text_data = text_resp.json()
    print(f"\nContinuity (text): {text_data.get('count', 0)} lines")
    if text_data.get("text"):
        print(f"  Preview: {text_data['text'][:200]}...")

    print("\nDone! All HTTP endpoints exercised successfully.")


if __name__ == "__main__":
    main()
