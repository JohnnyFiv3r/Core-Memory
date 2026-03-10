from __future__ import annotations

"""Legacy transcript source resolver for backfill/replay flows."""

from pathlib import Path


def find_transcript(session_id: str | None, agent_id: str = "main") -> tuple[Path, str]:
    """Resolve transcript path and normalized session id.

    Preserves existing extract-beads resolution behavior:
    - explicit session id -> direct file lookup
    - fallback -> latest session in agent folder
    """
    base = Path("/home/node/.openclaw/agents") / agent_id / "sessions"

    if session_id:
        p = base / f"{session_id}.jsonl"
        if not p.exists():
            raise FileNotFoundError(f"Session transcript not found: {p}")
        return p, session_id

    files = sorted(base.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No transcripts found in: {base}")
    p = files[0]
    return p, p.stem
