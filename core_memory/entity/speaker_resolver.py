"""Speaker identity resolution for multi-participant transcripts (#10).

Resolves observed speaker labels (Discord usernames, Slack IDs, Zoom diarization
labels, GitHub handles) to canonical entities in the entity registry.
Never writes to the entity store directly — uses upsert_canonical_entity().
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from core_memory.entity.registry import (
    _find_entity_id,
    _is_valid_entity_alias,
    normalize_entity_alias,
    upsert_canonical_entity,
)

_DEFAULT_CONFIDENCE_THRESHOLD = 0.75


@dataclass
class SpeakerResolution:
    """Result of resolving one observed speaker label."""

    speaker_observed: str
    resolved_entity_id: str | None
    resolution_confidence: float
    source_system: str
    aliases: list[str] = field(default_factory=list)
    resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker_observed": self.speaker_observed,
            "resolved_entity_id": self.resolved_entity_id,
            "resolution_confidence": self.resolution_confidence,
            "source_system": self.source_system,
            "aliases": list(self.aliases),
            "resolved": self.resolved,
        }


def _confidence_threshold() -> float:
    try:
        return float(os.getenv("SPEAKER_RESOLUTION_CONFIDENCE_THRESHOLD", str(_DEFAULT_CONFIDENCE_THRESHOLD)))
    except (ValueError, TypeError):
        return _DEFAULT_CONFIDENCE_THRESHOLD


def _strip_source_prefix(label: str, source_system: str) -> str:
    """Strip platform-specific markup prefixes from observed labels.

    Rules applied universally: strip leading @.
    Discord-specific: strip #discriminator suffix.
    """
    cleaned = str(label or "").strip()
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    if "#" in cleaned:
        cleaned = cleaned.split("#")[0].strip()
    return cleaned


def resolve_speaker(
    index: dict[str, Any],
    observed_label: str,
    source_system: str = "",
) -> SpeakerResolution:
    """Resolve an observed speaker label to a canonical entity.

    Mutates *index* in place when a new entity is created — callers are
    responsible for persisting the updated index.

    Returns:
        SpeakerResolution with resolved=True when confidence >= threshold.
        Does not create entities for invalid or empty labels.
    """
    raw = str(observed_label or "").strip()
    src = str(source_system or "").strip().lower()

    if not raw:
        return SpeakerResolution(
            speaker_observed="",
            resolved_entity_id=None,
            resolution_confidence=0.0,
            source_system=src,
            resolved=False,
        )

    cleaned = _strip_source_prefix(raw, src)
    normalized = normalize_entity_alias(cleaned or raw)

    if not normalized or not _is_valid_entity_alias(cleaned or raw, normalized):
        return SpeakerResolution(
            speaker_observed=raw,
            resolved_entity_id=None,
            resolution_confidence=0.0,
            source_system=src,
            aliases=[raw],
            resolved=False,
        )

    threshold = _confidence_threshold()

    # Exact alias match in registry → confidence 1.0
    entity_id = _find_entity_id(index, normalized)
    if entity_id:
        return SpeakerResolution(
            speaker_observed=raw,
            resolved_entity_id=entity_id,
            resolution_confidence=1.0,
            source_system=src,
            aliases=[a for a in {raw, cleaned, normalized} if a],
            resolved=True,
        )

    # No existing entity — create via registry (confidence 0.9 for new entities)
    aliases = [raw]
    if cleaned and cleaned != raw:
        aliases.append(cleaned)
    result = upsert_canonical_entity(
        index,
        label=cleaned or raw,
        aliases=aliases,
        confidence=0.9,
        provenance={"kind": "speaker_label", "source": src, "observed": raw},
    )
    if not result.get("ok"):
        return SpeakerResolution(
            speaker_observed=raw,
            resolved_entity_id=None,
            resolution_confidence=0.0,
            source_system=src,
            aliases=[raw],
            resolved=False,
        )

    confidence = 0.9
    return SpeakerResolution(
        speaker_observed=raw,
        resolved_entity_id=str(result.get("entity_id") or ""),
        resolution_confidence=confidence,
        source_system=src,
        aliases=aliases,
        resolved=confidence >= threshold,
    )


__all__ = ["SpeakerResolution", "resolve_speaker"]
