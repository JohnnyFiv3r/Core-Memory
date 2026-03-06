# SpringAI Quickstart

Status: Canonical
See also:
- `README.md`
- `api-reference.md`
- `../../contracts/http_api.v1.json`

## Goal
Get Core Memory running as a SpringAI companion service with:
1. write-path finalized-turn ingestion
2. runtime memory tool calls via HTTP

## 1) Start Core Memory service

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
python3 -m core_memory.integrations.http.server
```

Optional auth:
```bash
export CORE_MEMORY_HTTP_TOKEN="change-me"
```

## 2) Verify service

```bash
curl http://localhost:8000/healthz
curl -X POST http://localhost:8000/v1/memory/classify-intent \
  -H "Authorization: Bearer $CORE_MEMORY_HTTP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"why did promotion inflation happen"}'
```

## 3) SpringAI write path
POST assistant-finalized turns asynchronously to:
- `POST /v1/memory/turn-finalized`

Minimum body:
- `session_id`
- `turn_id`
- `user_query`
- `assistant_final`

## 4) SpringAI runtime path
Preferred single-call endpoint:
- `POST /v1/memory/execute`

Use `/v1/memory/classify-intent` only if you want telemetry/UX routing. It is not required for correctness.

## 5) Validate
Run:
```bash
python -m unittest tests.test_http_ingress
python eval/memory_execute_eval.py
```
