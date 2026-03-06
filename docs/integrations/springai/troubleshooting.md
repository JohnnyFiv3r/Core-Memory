# SpringAI Troubleshooting

Status: Canonical

## Common issues

### 1) 401 unauthorized
Cause:
- `CORE_MEMORY_HTTP_TOKEN` is set and SpringAI did not send the token.

Fix:
- send `Authorization: Bearer <token>` or `X-Memory-Token: <token>`

### 2) 404 model does not exist / no access
Cause:
- configured model id is wrong or account lacks access.

Fix:
- verify the actual provider model ids in OpenClaw/Core Memory config
- confirm account entitlement
- restart after config changes

### 3) No runtime results / weak search
Cause:
- no strong anchor match or weak bead metadata

Check:
- `warnings`
- `grounding.reason`
- `confidence`
- `next_action`

### 4) Duplicate finalized-turn ingestion
Expected behavior:
- `/turn-finalized` is idempotent by `session_id:turn_id`

If duplicates appear:
- verify stable turn IDs from Spring side
- run ingress/idempotency tests

### 5) Drift between JVM routing and Python routing
Preferred behavior:
- use `memory.execute` directly
- treat `/classify-intent` as optional telemetry/UX aid only

## Diagnostics
Run:
```bash
python -m unittest tests.test_http_ingress
python eval/memory_execute_eval.py
```
