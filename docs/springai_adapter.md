# SpringAI Adapter (Wave 2, HTTP ingress + runtime memory tools)

Status: Canonical
Canonical surfaces: `/v1/memory/execute`, `/v1/memory/turn-finalized`, `/v1/memory/classify-intent`
See also:
- `docs/index.md`
- `docs/canonical_surfaces.md`
- `docs/contracts/http_api.v1.json`

SpringAI runs on JVM, so integration remains HTTP -> Python service.

Canonical contract:
- `docs/contracts/http_api.v1.json`

## Write path (non-blocking)

`POST /v1/memory/turn-finalized`

Body (minimal):
- `session_id`
- `turn_id`
- `user_query`
- `assistant_final`

Optional:
- `root` (omit to use server-side env/default resolution)
- `transaction_id`
- `metadata`
- `traces` (`{"tools":[...], "mesh":[...]}`)
- `window_turn_ids`
- `window_bead_ids`
- `origin` (default `USER_TURN`)

## Runtime tool path (sync)

### 0) Intent classification (optional)
- `POST /v1/memory/classify-intent`
- Body:
  - `query`
- Use for telemetry/UX routing if desired.
- Not required for correctness; `POST /v1/memory/execute` is the preferred single-call path.

### 1) Search form discovery
- `GET /v1/memory/search-form?root=<optional>`

### 2) Typed search
- `POST /v1/memory/search`
- Body:
  - `root` (optional)
  - `form_submission` (typed form fields)
  - `explain` (bool)

### 3) Causal reason
- `POST /v1/memory/reason`
- Body:
  - `root` (optional)
  - `query`
  - `k`
  - `debug`
  - `explain`
  - `pinned_incident_ids[]`
  - `pinned_topic_keys[]`
  - `pinned_bead_ids[]`

### 4) Unified execution (recommended)
- `POST /v1/memory/execute`
- Body:
  - `root` (optional)
  - `request` (MemoryRequest object)
  - `explain` (bool)

## Health probe
- `GET /healthz` -> `{ "ok": true }`

## Auth

Set server env:
- `CORE_MEMORY_HTTP_TOKEN=<shared-secret>`

Then clients send either:
- `Authorization: Bearer <shared-secret>`
- or `X-Memory-Token: <shared-secret>`

If token env is unset, endpoints are open (local/dev mode).

## Deterministic IDs

- `session_id = sha256(tenant + userId + threadId)`
- `turn_id = requestId || messageId`

## Failure behavior

- Write path is async; never block user response on turn-finalized POST failures.
- Runtime tool calls are sync; apply client timeout and retry policy.

## Advisor/interceptor pseudocode

```java
String sessionId = sha256(tenant + userId + threadId);
String turnId = requestId != null ? requestId : messageId;

MemoryPayload payload = new MemoryPayload(
  sessionId,
  turnId,
  userQuery,
  assistantFinal,
  metadata
);

CompletableFuture.runAsync(() -> {
  try {
    http.post("http://memory-ingress:8765/v1/memory/turn-finalized", payload);
  } catch (Exception ignored) {
    // non-blocking by design
  }
});

// Runtime tool call (sync)
MemoryExecuteRequest req = ...;
MemoryExecuteResponse res = http.post("http://memory-ingress:8765/v1/memory/execute", req);
```
