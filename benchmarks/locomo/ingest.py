"""LoCoMo turn ingestion into Core Memory.

Design invariants:
  - One bead per utterance. The bead encodes the dia_id in source_turn_ids so
    retrieval results can be mapped back to dia_id space for scoring.
  - No claim injection. Benchmark transcripts must not produce dataset-specific
    claims — the claim layer runs on live agent turns only.
  - No gold answer pollution. gold_evidence and expected_answer exist only in
    Python QA objects; they never touch the benchmark temp dir.
  - process_turn_finalized is used directly (not emit_turn_finalized) because
    the emission guard blocks synchronous writes outside runtime contexts.
  - Temporal adjacency associations are only synthesised when
    shortcut_flags.synthetic_temporal_edges=True.  The default (False) skips
    them so faithful eval runs carry no synthetic structure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.passes.association_pass import run_association_pass
from core_memory.association.crawler_contract import merge_crawler_updates

from benchmarks.contracts import BenchmarkConversation, BenchmarkShortcutFlags

_ENTITY_RE = re.compile(r"\b([A-Z][A-Za-z0-9._-]{2,}|[A-Z]{2,}[A-Za-z0-9._-]*)\b")
_STOP_TOKENS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "our",
    "their", "were", "was", "have", "has", "had", "will", "would", "should",
    "could", "can", "about", "after", "before", "then", "than", "because",
    "there", "here", "when", "where", "what", "which", "who", "session",
})


def _extract_entities(text: str, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _ENTITY_RE.finditer(str(text or "")):
        token = m.group(1).strip(".,:;!?()[]{}\"'")
        if len(token) < 3:
            continue
        key = token.lower()
        if key in _STOP_TOKENS or key in seen:
            continue
        seen.add(key)
        out.append(token)
        if len(out) >= limit:
            break
    if not out:
        # Fall back to non-capitalized tokens
        for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", str(text or "")):
            key = token.lower()
            if key in _STOP_TOKENS or key in seen:
                continue
            seen.add(key)
            out.append(token)
            if len(out) >= 6:
                break
    return out


def _build_crawler_updates(*, content: str, turn_id: str) -> dict[str, Any]:
    """Build production-shaped crawler_updates for one LoCoMo turn."""
    text = str(content or "").strip()
    title = (text.splitlines()[0] if text else "LoCoMo turn")[:160]
    entities = _extract_entities(text)
    topics: list[str] = []
    for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{3,}\b", text.lower()):
        if token in _STOP_TOKENS or token in topics:
            continue
        topics.append(token)
        if len(topics) >= 8:
            break
    if not topics:
        topics = ["locomo"]
    return {
        "beads_create": [
            {
                "type": "context",
                "title": title or "LoCoMo turn",
                "summary": [text[:240]],
                "because": [],
                "source_turn_ids": [str(turn_id)],
                "entities": entities or ["LoCoMo"],
                "topics": topics,
                "retrieval_eligible": True,
                "retrieval_title": title or text[:160] or "LoCoMo turn",
                "retrieval_facts": [text[:500]],
                "tags": ["locomo_replay", "benchmark_preload", "crawler_reviewed"],
            }
        ]
    }


def _read_index(root: str) -> dict[str, Any]:
    idx_path = Path(root) / ".beads" / "index.json"
    if not idx_path.exists():
        return {}
    try:
        return json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _bead_id_for_turn(root: str, *, session_id: str, turn_id: str) -> str:
    """Find the bead created for a given turn_id by scanning the bead index."""
    idx = _read_index(root)
    beads = dict((idx.get("beads") or {})) if isinstance(idx, dict) else {}
    hits: list[dict[str, Any]] = []
    for bead in beads.values():
        if not isinstance(bead, dict):
            continue
        if str(bead.get("session_id") or "") != session_id:
            continue
        src = [str(x) for x in (bead.get("source_turn_ids") or [])]
        if turn_id in src:
            hits.append(bead)
    if not hits:
        return ""
    hits.sort(key=lambda b: str(b.get("created_at") or ""), reverse=True)
    return str(hits[0].get("id") or "")


def ingest_conversation(
    root: str,
    conversation: BenchmarkConversation,
    *,
    shortcut_flags: BenchmarkShortcutFlags | None = None,
) -> dict[str, str]:
    """
    Replay all turns of a BenchmarkConversation into the given root dir.

    Returns a mapping of {dia_id: bead_id} built from the index after ingestion.
    This mapping is used to convert retrieved bead_ids back to dia_id space
    for evidence scoring.

    shortcut_flags must have is_faithful()=True for official evaluation.
    """
    if shortcut_flags is not None and not shortcut_flags.is_faithful():
        raise ValueError(f"ingest_conversation called with non-faithful shortcut flags: {shortcut_flags.to_dict()}")

    inject_temporal_edges = shortcut_flags is not None and shortcut_flags.synthetic_temporal_edges

    session_id = conversation.session_id
    last_bead_by_session: dict[str, str] = {}
    # session_index grouping for adjacency links (only used when inject_temporal_edges=True)
    session_of_turn: dict[str, int] = {
        t.turn_id: int(t.metadata.get("session_index") or 0)
        for t in conversation.turns
    }
    last_bead_by_session_idx: dict[int, str] = {}

    for turn in conversation.turns:
        turn_id = turn.turn_id
        dia_id = str(turn.metadata.get("dia_id") or "")
        session_idx = int(turn.metadata.get("session_index") or 0)

        raw_turns = [
            {"speaker": turn.speaker, "role": turn.role, "content": turn.content}
        ]
        crawler_updates = _build_crawler_updates(content=turn.content, turn_id=turn_id)

        process_turn_finalized(
            root=root,
            session_id=session_id,
            turn_id=turn_id,
            transaction_id=f"tx-{turn_id}",
            trace_id=f"tr-{turn_id}",
            turns=raw_turns,
            metadata={"crawler_updates": crawler_updates, "replay_source": "locomo"},
            tools_trace=[],
            mesh_trace=[],
            origin="LOCOMO_BENCHMARK",
        )

        current_bead_id = _bead_id_for_turn(root, session_id=session_id, turn_id=turn_id)

        if inject_temporal_edges:
            prev_bead_id = last_bead_by_session_idx.get(session_idx, "")
            if current_bead_id and prev_bead_id and current_bead_id != prev_bead_id:
                run_association_pass(
                    root=root,
                    session_id=session_id,
                    updates={
                        "associations": [
                            {
                                "source_bead_id": current_bead_id,
                                "target_bead_id": prev_bead_id,
                                "relationship": "follows",
                                "confidence": 0.72,
                                "reason_text": "locomo temporal adjacency",
                                "provenance": "locomo_replay",
                            }
                        ]
                    },
                    visible_bead_ids=[prev_bead_id, current_bead_id],
                )
                merge_crawler_updates(root=root, session_id=session_id)

        if current_bead_id:
            last_bead_by_session_idx[session_idx] = current_bead_id

    # Build dia_id → bead_id map after all turns are ingested
    dia_to_bead: dict[str, str] = {}
    idx = _read_index(root)
    beads = dict((idx.get("beads") or {})) if isinstance(idx, dict) else {}
    for bead in beads.values():
        if not isinstance(bead, dict):
            continue
        if str(bead.get("session_id") or "") != session_id:
            continue
        bead_id = str(bead.get("id") or "")
        if not bead_id:
            continue
        for src in bead.get("source_turn_ids") or []:
            src = str(src)
            # turn_id format: "locomo:{sample_id}:{dia_id}"
            parts = src.split(":", 2)
            if len(parts) == 3 and parts[0] == "locomo":
                dia_id = parts[2]
                dia_to_bead[dia_id] = bead_id

    return dia_to_bead
