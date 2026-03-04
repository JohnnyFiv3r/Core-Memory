"""
Dreamer — Move 37 Association Analysis

This module runs periodic structural recombination analysis to discover
novel connections between memory beads.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any


RELATIONSHIP_TYPES = {
    "similar_pattern",
    "transferable_lesson",
    "contradicts",
    "reinforces",
    "generalizes",
    "structural_symmetry",
    "reveals_bias",
}


def _pair_key(source: str, target: str) -> str:
    a, b = sorted([str(source or ""), str(target or "")])
    return f"{a}::{b}"


def _seen_file_from_store(store) -> Path:
    return Path(store.root) / ".beads" / "events" / "dreamer-seen.jsonl"


def _load_seen_state(path: Path, seen_window_runs: int = 0) -> tuple[set[str], dict[str, int]]:
    if not path.exists():
        return set(), {}

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("pair_key"):
                rows.append(row)

    if seen_window_runs > 0:
        run_order: list[str] = []
        seen_run_ids: set[str] = set()
        for row in rows:
            rid = str(row.get("run_id") or "")
            if rid and rid not in seen_run_ids:
                seen_run_ids.add(rid)
                run_order.append(rid)
        allowed = set(run_order[-seen_window_runs:])
        rows = [r for r in rows if str(r.get("run_id") or "") in allowed]

    seen_pairs: set[str] = set()
    bead_exposure: dict[str, int] = {}
    for row in rows:
        pk = str(row.get("pair_key") or "")
        if pk:
            seen_pairs.add(pk)
        source = str(row.get("source") or "")
        target = str(row.get("target") or "")
        if source:
            bead_exposure[source] = bead_exposure.get(source, 0) + 1
        if target:
            bead_exposure[target] = bead_exposure.get(target, 0) + 1

    return seen_pairs, bead_exposure


def _append_seen(path: Path, run_id: str, associations: list[dict[str, Any]]) -> None:
    if not associations:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for a in associations:
            row = {
                "run_id": run_id,
                "ts": now,
                "source": a.get("source"),
                "target": a.get("target"),
                "pair_key": a.get("pair_key") or _pair_key(a.get("source"), a.get("target")),
                "relationship": a.get("relationship"),
                "novelty": a.get("novelty"),
                "confidence": a.get("confidence"),
                "grounding": a.get("grounding"),
                "final_score": a.get("final_score"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_index(beads_dir: Path) -> dict:
    """Load the index file."""
    index_file = beads_dir / "index.json"
    if not index_file.exists():
        return {"beads": {}, "associations": []}
    with open(index_file, 'r') as f:
        return json.load(f)


def get_promoted_beads(index: dict) -> list:
    """Get beads eligible for association (promoted status)."""
    candidates = []
    for bead_id, meta in index.get("beads", {}).items():
        if meta.get("status") in ("open", "promoted"):
            if meta.get("type") not in ("association", "session_start", "session_end", "checkpoint"):
                candidates.append(meta)
    return candidates


def extract_mechanism(bead: dict) -> str:
    """Extract the core mechanism from a bead (not topic, the underlying logic)."""
    bead_type = bead.get("type", "")
    title = bead.get("title", "")
    
    # For now, derive mechanism from type + title
    mechanisms = {
        "goal": "intention to achieve something",
        "decision": "choice between alternatives",
        "outcome": "result of prior action",
        "lesson": "learned insight from experience",
        "evidence": "data supporting a claim",
        "failed_hypothesis": "assumption that proved wrong",
        "design_principle": "recurring architectural pattern",
        "precedent": "pattern to apply elsewhere",
    }
    
    return mechanisms.get(bead_type, f"unknown mechanism: {title}")


def compute_distance(bead1: dict, bead2: dict) -> float:
    """Compute distance between two beads (0=close, 1=far)."""
    score = 0.0
    
    # Different scope = higher distance
    if bead1.get("scope") != bead2.get("scope"):
        score += 0.3
    
    # Different type = higher distance
    if bead1.get("type") != bead2.get("type"):
        score += 0.2
    
    # Different session = higher distance
    if bead1.get("session_id") != bead2.get("session_id"):
        score += 0.3
    
    # Different tags = higher distance
    tags1 = set(bead1.get("tags", []))
    tags2 = set(bead2.get("tags", []))
    if tags1 and tags2 and not tags1.intersection(tags2):
        score += 0.2
    
    return min(score, 1.0)


def score_association(bead1: dict, bead2: dict, distance: float) -> dict:
    """Score a potential association."""
    mechanisms = {
        "contradicts": 0.8,
        "transferable_lesson": 0.75,
        "reinforces": 0.6,
        "generalizes": 0.7,
        "structural_symmetry": 0.85,
        "reveals_bias": 0.8,
    }
    
    # Default novelty based on distance
    novelty = distance * 0.5 + 0.3
    
    # Ground in actual content
    title1 = bead1.get("title", "")
    title2 = bead2.get("title", "")
    summary1 = " ".join(bead1.get("summary", []))
    summary2 = " ".join(bead2.get("summary", []))
    
    relationship = "similar_pattern"

    s1 = summary1.lower()
    s2 = summary2.lower()

    # Check for contradiction
    if any(w in s1 for w in ["not", "no", "don't", "never"]):
        if any(w in s2 for w in ["yes", "always", "do", "need"]):
            relationship = "contradicts"

    # Check for reinforcement
    elif bool(set(s1.split()) & set(s2.split())):
        relationship = "reinforces"
    
    return {
        "relationship": relationship,
        "novelty": novelty,
        "confidence": 0.7,
        "grounding": 0.8,
    }


def run_analysis(
    beads_dir: str = None,
    store=None,
    novel_only: bool = False,
    seen_window_runs: int = 0,
    max_exposure: int = -1,
) -> list:
    """
    Run Dreamer association analysis on beads.
    
    Can be called with either:
    - beads_dir: Path to the .beads directory (legacy)
    - store: A MemoryStore instance (preferred)
    
    Args:
        beads_dir: Path to the .beads directory (deprecated)
        store: MemoryStore instance (preferred)
        novel_only: Exclude pairs already surfaced in Dreamer history
        seen_window_runs: Use only last N run_ids for seen-pair dedupe (0=all)
        max_exposure: Skip candidate pairs where either bead exceeded this surfaced count (-1=disabled)

    Returns:
        List of discovered associations
    """
    # Use store if provided, otherwise fall back to beads_dir
    if store is not None:
        # Use MemoryStore API
        beads = store.query(status="open", limit=50) + store.query(status="promoted", limit=50)
    elif beads_dir is not None:
        # Legacy: read from directory
        beads_path = Path(beads_dir)
        index = load_index(beads_path)
        beads = get_promoted_beads(index)
    else:
        return [{"error": "Either beads_dir or store must be provided"}]
    
    if len(beads) < 2:
        return [{"status": "need_more_beads", "message": "Need at least 2 beads for analysis"}]

    seen_pairs: set[str] = set()
    bead_exposure: dict[str, int] = {}
    seen_file: Optional[Path] = None
    run_id = f"dream-{uuid.uuid4().hex[:12]}"
    if store is not None:
        seen_file = _seen_file_from_store(store)
        seen_pairs, bead_exposure = _load_seen_state(seen_file, seen_window_runs=seen_window_runs)

    # Find potential associations
    associations = []
    
    for i, bead1 in enumerate(beads[:20]):  # Limit to 20 for performance
        for bead2 in beads[i+1:25]:
            source_id = str(bead1.get("id") or "")
            target_id = str(bead2.get("id") or "")
            if not source_id or not target_id:
                continue

            pair_key = _pair_key(source_id, target_id)
            if novel_only and pair_key in seen_pairs:
                continue

            exposure = max(bead_exposure.get(source_id, 0), bead_exposure.get(target_id, 0))
            if max_exposure >= 0 and exposure > max_exposure:
                continue

            distance = compute_distance(bead1, bead2)

            # Only consider distant beads (distance > 0.3)
            if distance < 0.3:
                continue

            score = score_association(bead1, bead2, distance)

            repetition_penalty = 0.35 if pair_key in seen_pairs else 0.0
            coverage_bonus = max(0.0, 0.2 - 0.02 * float(exposure))
            final_score = max(0.0, float(score["novelty"]) + coverage_bonus - repetition_penalty)

            # Only surface high-quality associations
            if final_score >= 0.5 and score["grounding"] >= 0.7:
                associations.append({
                    "source": source_id,
                    "target": target_id,
                    "pair_key": pair_key,
                    "source_title": bead1.get("title"),
                    "target_title": bead2.get("title"),
                    "relationship": score["relationship"],
                    "novelty": score["novelty"],
                    "confidence": score["confidence"],
                    "grounding": score["grounding"],
                    "final_score": final_score,
                    "seen_before": pair_key in seen_pairs,
                    "exposure": exposure,
                })
    
    if not associations:
        return [{
            "status": "no_associations",
            "message": "No significant associations found",
            "novel_only": novel_only,
            "seen_window_runs": seen_window_runs,
            "max_exposure": max_exposure,
        }]

    # Sort by final score, then novelty
    associations.sort(key=lambda a: (a.get("final_score", 0.0), a.get("novelty", 0.0)), reverse=True)

    top = associations[:5]
    if seen_file is not None:
        _append_seen(seen_file, run_id=run_id, associations=top)

    return top  # Return top 5


def record_association(beads_dir: str, source: str, target: str, relationship: str,
                      explanation: str, novelty: float, confidence: float) -> str:
    """Record a confirmed association via canonical MemoryStore path."""
    root = str(Path(beads_dir).parent)
    from .store import MemoryStore

    store = MemoryStore(root=root)
    return store.link(source, target, relationship, explanation=explanation)


def prompt_template() -> str:
    """Return the Move 37 prompt template for human-assisted discovery."""
    return """
You are running Dreamer — Move 37 structural recombination analysis.

Reward:
- Cross-domain recombination
- Deep structural symmetry  
- Constraint → pattern transformations
- Non-obvious contradictions

Do NOT reward:
- Shared keywords
- Same-session summaries
- Obvious restatements

Required Process:
1. Extract mechanism (not topic) from each bead
2. Apply distance bias (prefer cross-project, cross-time)
3. Look for structural recombinations
4. Test decision impact
5. Apply human blind spot test

Output format:
{
  "source": "bead-id",
  "target": "bead-id", 
  "relationship": "contradicts|transferable_lesson|...",
  "insight": "...",
  "decision_impact": "...",
  "novelty_score": 0.0-1.0,
  "grounding_score": 0.0-1.0
}

Surface only insights where novelty >= 0.6 and grounding >= 0.7.
"""
