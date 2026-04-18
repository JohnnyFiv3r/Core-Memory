"""Configuration loading for Core Memory retrieval pipeline.

Two-tier config: shipped defaults in config/defaults/ + user overrides
from CORE_MEMORY_CONFIG_DIR or {root}/config/.
"""

from .loader import get_config_dir, load_domain_tags, load_query_expansions

__all__ = ["load_domain_tags", "load_query_expansions", "get_config_dir"]
