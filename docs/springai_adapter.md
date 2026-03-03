# SpringAI Adapter (Wave 1, HTTP ingress)

SpringAI runs on JVM, so Wave 1 uses HTTP -> Python ingress.

## Endpoint

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

Health probe:
- `GET /healthz` -> `{ "ok": true }`

## Deterministic IDs

- `session_id = sha256(tenant + userId + threadId)`
- `turn_id = requestId || messageId`

## Failure behavior

- POST asynchronously.
- Never block user response on memory POST failures.
- Future: local spool/retry queue in JVM side.

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
```
