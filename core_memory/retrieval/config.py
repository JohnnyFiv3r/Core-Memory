from __future__ import annotations

# Slice 2.1 hardening coefficients (single source of truth)
W_FUSED = 0.50
W_STRUCTURAL = 0.20
W_EDGE_SUPPORT = 0.15
W_COVERAGE = 0.10
W_INCIDENT = 0.05
W_PENALTY = 0.10

INTENT_WEIGHT_OVERRIDES = {
    "causal": {
        "W_STRUCTURAL": 0.24,
        "W_EDGE_SUPPORT": 0.17,
        "W_COVERAGE": 0.08,
        "W_INCIDENT": 0.06,
    },
    "remember": {
        "W_STRUCTURAL": 0.10,
        "W_EDGE_SUPPORT": 0.08,
        "W_COVERAGE": 0.20,
        "W_INCIDENT": 0.10,
    },
    "what_changed": {
        "W_STRUCTURAL": 0.18,
        "W_EDGE_SUPPORT": 0.14,
        "W_COVERAGE": 0.15,
        "W_INCIDENT": 0.07,
    },
    "when": {
        "W_STRUCTURAL": 0.10,
        "W_EDGE_SUPPORT": 0.08,
        "W_COVERAGE": 0.22,
        "W_INCIDENT": 0.08,
    },
}

# Quality gate buckets
SHORT_QUERY_TOKENS = 3
QUALITY_THRESHOLD_SHORT = 0.30
QUALITY_THRESHOLD_LONG = 0.38
CAUSAL_MIN_STRUCTURAL_QUALITY = 0.25
TOPK_STRUCTURAL_CHECK = 3

# Retrieval/retry
RETRY_APPEND_HINT = " decision evidence outcome"
INCIDENT_FLOOR = 0.15
NORM_EPS = 1e-9

# Low-info scoring
LOW_INFO_TITLE_LEN = 8
LOW_INFO_SUMMARY_LEN = 20
LOW_INFO_ALNUM_RATIO_MIN = 0.55
LOW_INFO_TEMPLATES = [
    "[[reply_to_current]]",
    "auto-compaction complete",
]
