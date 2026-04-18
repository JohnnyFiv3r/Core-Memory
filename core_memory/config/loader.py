"""Config loader for Core Memory retrieval pipeline.

Two-tier config: shipped defaults (bundled in this package) merged with
user overrides discovered via a three-level precedence chain:

  1. CORE_MEMORY_CONFIG_DIR env var (explicit override)
  2. {root}/config/               (co-located with memory store)
  3. ~/.core-memory/config/        (global user config)

User values win on key conflicts. Missing user files are silently skipped.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_DEFAULTS_DIR = Path(__file__).parent / "defaults"


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning empty dict on missing or malformed."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("config: failed to parse %s: %s", path, exc)
        return {}


def get_config_dir(root: Optional[Path] = None) -> Optional[Path]:
    """Resolve the user config directory via the precedence chain.

    Returns the first directory that exists, or None if no user config is found.
    """
    candidates: list[Path] = []

    env = os.environ.get("CORE_MEMORY_CONFIG_DIR")
    if env:
        candidates.append(Path(env))

    if root is not None:
        candidates.append(Path(root) / "config")

    candidates.append(Path.home() / ".core-memory" / "config")

    for d in candidates:
        if d.is_dir():
            return d
    return None


def _merge_deep(base: dict, override: dict) -> dict:
    """Shallow-merge override into base. For domain_tags and expansion maps,
    user keys win on conflict; base keys not in override are preserved."""
    merged = dict(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def load_domain_tags(root: Optional[Path] = None) -> dict[str, list[str]]:
    """Load domain tags: shipped defaults merged with user overrides.

    Returns dict mapping tag name to list of matcher strings.
    """
    defaults = _load_yaml(_DEFAULTS_DIR / "domain_tags.yaml")
    base_tags = defaults.get("domain_tags", {})

    user_dir = get_config_dir(root)
    if user_dir is None:
        return base_tags

    user = _load_yaml(user_dir / "domain_tags.yaml")
    user_tags = user.get("domain_tags", {})
    if not user_tags:
        return base_tags

    merged = _merge_deep(base_tags, user_tags)
    logger.debug("config: merged %d shipped + %d user domain tags → %d total",
                 len(base_tags), len(user_tags), len(merged))
    return merged


def load_query_expansions(root: Optional[Path] = None) -> dict:
    """Load query expansions: shipped defaults merged with user overrides.

    Returns dict with 'phrase_map' and 'token_map' keys.
    """
    defaults = _load_yaml(_DEFAULTS_DIR / "query_expansions.yaml")
    base_phrases = defaults.get("phrase_map", {})
    base_tokens = defaults.get("token_map", {})

    user_dir = get_config_dir(root)
    if user_dir is None:
        return {"phrase_map": base_phrases, "token_map": base_tokens}

    user = _load_yaml(user_dir / "query_expansions.yaml")
    user_phrases = user.get("phrase_map", {})
    user_tokens = user.get("token_map", {})

    merged_phrases = _merge_deep(base_phrases, user_phrases)
    merged_tokens = _merge_deep(base_tokens, user_tokens)

    if user_phrases or user_tokens:
        logger.debug("config: merged query expansions — phrases: %d shipped + %d user, tokens: %d shipped + %d user",
                     len(base_phrases), len(user_phrases), len(base_tokens), len(user_tokens))

    return {"phrase_map": merged_phrases, "token_map": merged_tokens}
