# Speaker Schema Research — #10 Multi-Speaker Attribution

**Date:** 2026-05-29  
**Author:** Research phase artifact for task #10  
**Status:** Complete — schema decisions recorded below

---

## Source System Label Inventory

### Discord

| Field | Format | Stability | Notes |
|---|---|---|---|
| Legacy display name | `username#discriminator` (e.g., `johnnyfiv3r#1234`) | Mutable (username), stable (discriminator) | Phased out mid-2023 for new accounts |
| New username | `@johnnyfiv3r` / `johnnyfiv3r` | Mutable (can be changed once every 30 days) | No discriminator suffix |
| User ID (snowflake) | 18-digit integer string (e.g., `187198988139290624`) | **Immutable** | Most reliable cross-session key |
| Bot IDs | Same snowflake format, role flag set | Immutable | |

**Normalization decisions:**
- Strip leading `@`
- Strip `#discriminator` suffix when present
- Snowflake IDs normalize well via `normalize_entity_alias` (digits only, passes `_is_valid_entity_alias`)
- Human labels like `johnnyfiv3r` also normalize cleanly

---

### Slack

| Field | Format | Stability | Notes |
|---|---|---|---|
| User ID | `U` + alphanumeric, e.g., `U12345ABCDE` (11 chars) | **Immutable** (workspace-scoped) | Primary stable identifier |
| Bot ID | `B` + alphanumeric | Immutable | |
| Display name | Human-readable string, may include spaces | Mutable | Changes frequently |
| `@mention` | `@U12345ABCDE` or `@display-name` in message text | — | `@` is markup only |

**Normalization decisions:**
- Strip leading `@`
- `U12345ABCDE` normalizes to `u12345abcde` via `normalize_entity_alias` (11 chars, passes validity)
- Display names with spaces collapse to concatenated form (e.g., `John Smith` → `johnsmith`)

---

### Zoom / Otter (Meeting Transcripts)

| Field | Format | Stability | Notes |
|---|---|---|---|
| Diarization label | `SPEAKER_00`, `SPEAKER_01`, … | **Per-transcript only** | NOT cross-session stable |
| Named speaker | `John Smith` or `John` if identified | Mutable | Available if host identifies speakers |
| External ID | Email or Zoom user ID (optional) | Stable | Rarely exported in diarized transcripts |

**Key constraint:** `SPEAKER_00` in transcript A and `SPEAKER_00` in transcript B are NOT the same person. These are positional labels assigned fresh per recording.

**Normalization decisions:**
- `SPEAKER_00` normalizes to `speaker00` via `normalize_entity_alias` (8 chars, digit present, passes validity)
- Register as entity per-transcript; do not merge across transcripts without explicit confirmation
- Named speakers (when available): normalize and merge normally

---

### GitHub

| Field | Format | Stability | Notes |
|---|---|---|---|
| Login handle | `johnnyfiv3r` (no prefix in API) | **Immutable** after account creation | Username changes are rare and redirect |
| Mention in text | `@johnnyfiv3r` | — | `@` is markup only |
| User ID | Integer (e.g., `1234567`) | Immutable | Available via API, not usually in text exports |
| Display name | Human-readable | Mutable | |

**Normalization decisions:**
- Strip leading `@`
- Login handles normalize cleanly (lowercase alphanumeric)

---

## Cross-System Field Mapping Table

| System | Raw example | After `_strip_source_prefix` | After `normalize_entity_alias` | Valid? |
|---|---|---|---|---|
| Discord (new) | `@johnnyfiv3r` | `johnnyfiv3r` | `johnnyfiv3r` | ✓ |
| Discord (legacy) | `johnnyfiv3r#1234` | `johnnyfiv3r` | `johnnyfiv3r` | ✓ |
| Discord (snowflake) | `187198988139290624` | `187198988139290624` | `187198988139290624` | ✓ |
| Slack (ID) | `@U12345ABCDE` | `U12345ABCDE` | `u12345abcde` | ✓ |
| Slack (display) | `@John Smith` | `John Smith` | `johnsmith` | ✓ |
| Zoom | `SPEAKER_00` | `SPEAKER_00` | `speaker00` | ✓ (digit present) |
| Otter (named) | `John Smith` | `John Smith` | `johnsmith` | ✓ |
| GitHub | `@johnnyfiv3r` | `johnnyfiv3r` | `johnnyfiv3r` | ✓ |
| Invalid (too short) | `U` | `U` | `u` | ✗ (< 4 chars, no digit) |
| Stopword | `the` | `the` | `the` | ✗ (stopword) |

---

## Schema Decisions

### Identity confidence model

Rather than a binary resolved/unresolved, every resolution carries a float `resolution_confidence`:

| Scenario | Confidence | Action |
|---|---|---|
| Exact alias match in entity registry | 1.0 | Use existing entity |
| New entity created from valid label | 0.9 | Create entity, mark resolved |
| Invalid label (too short, stopword) | 0.0 | No entity created, store observation only |

`SPEAKER_RESOLUTION_CONFIDENCE_THRESHOLD` (env var, default 0.75) gates the `resolved=True` flag. Callers below the threshold must not create false merges.

### Per-transcript diarization labels (Zoom/Otter)

`SPEAKER_00` labels are valid (pass the alias validator via the digit check) and will create entities. For cross-transcript de-duplication, callers should supply an explicit `source_system` that includes a transcript or session identifier if they want positional labels to remain isolated. The base resolver treats them as any other label.

### `@` prefix handling

Strip universally. The `@` symbol is display/markup convention in every platform studied; it never forms part of the stable identifier.

### Discord `#discriminator` suffix

Strip for matching purposes. Two users `johnnyfiv3r#1234` and `johnnyfiv3r#5678` would collide post-normalization — this is a known limitation of the heuristic-only v1 approach. LLM-assisted disambiguation is deferred to v2 (explicitly out of scope per PRD).

### Alias merging

When the same normalized label appears under two different source systems (e.g., `johnnyfiv3r` on Discord and GitHub), the resolver will correctly merge them to one entity. This is the intended behavior for users with consistent usernames across platforms.

---

## Implementation summary

These findings drive the implementation in `entity/speaker_resolver.py`:
- `_strip_source_prefix()` handles `@` removal and `#discriminator` stripping
- Resolution uses `normalize_entity_alias()` + `_find_entity_id()` from `entity/registry.py`
- `SPEAKER_RESOLUTION_CONFIDENCE_THRESHOLD` env var tunes the resolved gate
- No new entity store; the existing entity registry absorbs all speaker identities
