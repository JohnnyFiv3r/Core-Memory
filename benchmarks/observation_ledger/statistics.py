"""Deterministic confidence intervals used by Observation Ledger reports."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def wilson_interval(
    successes: int,
    total: int,
    *,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    """Return a two-sided 95% Wilson score interval for a binary metric."""

    if total <= 0 or successes < 0 or successes > total:
        raise ValueError("invalid_binary_sample")
    proportion = successes / total
    denominator = 1.0 + (z * z / total)
    center = (proportion + (z * z / (2.0 * total))) / denominator
    margin = z * math.sqrt((proportion * (1.0 - proportion) / total) + (z * z / (4.0 * total * total))) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def seeded_bootstrap_mean_interval(
    values: Sequence[float],
    *,
    seed: int = 0,
    samples: int = 2000,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return a reproducible percentile-bootstrap interval for a sample mean."""

    normalized = [float(value) for value in values]
    if not normalized:
        raise ValueError("empty_continuous_sample")
    if samples < 2:
        raise ValueError("bootstrap_samples_too_small")
    if confidence <= 0 or confidence >= 1:
        raise ValueError("bootstrap_confidence_invalid")

    generator = random.Random(seed)
    count = len(normalized)
    means = sorted(sum(normalized[generator.randrange(count)] for _ in range(count)) / count for _ in range(samples))
    tail = (1.0 - confidence) / 2.0
    lower_index = max(0, min(samples - 1, math.floor(tail * (samples - 1))))
    upper_index = max(0, min(samples - 1, math.ceil((1.0 - tail) * (samples - 1))))
    return means[lower_index], means[upper_index]


def metric_interval(values: Sequence[float], *, seed: int = 0) -> dict[str, float | int | str]:
    """Choose Wilson for binary rows and seeded bootstrap for continuous rows."""

    normalized = [float(value) for value in values]
    if not normalized:
        raise ValueError("empty_metric_sample")
    if all(value in {0.0, 1.0} for value in normalized):
        lower, upper = wilson_interval(sum(value == 1.0 for value in normalized), len(normalized))
        method = "wilson_95"
    else:
        lower, upper = seeded_bootstrap_mean_interval(normalized, seed=seed)
        method = "seeded_bootstrap_mean_95"
    return {
        "method": method,
        "sample_size": len(normalized),
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "seed": seed,
    }
