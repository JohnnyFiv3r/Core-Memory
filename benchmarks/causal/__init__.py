"""Causal-chain reconstruction benchmark.

Unlike the LoCoMo-style semantic-QA harness, this benchmark measures the thing
the Core Memory thesis actually claims: notate, maintain, and *retrieve over
causal connections*.

Each case seeds a synthetic history with a known causal chain plus one or more
adversarial distractors — beads that are semantically closest to the query but
are NOT on the causal chain.  The benchmark then measures whether causal
traversal reconstructs the true chain rather than surfacing the distractor.

Headline metric: ``distractor_survival_rate`` — the fraction of cases where the
true root cause outranks every adversarial distractor.  This is the one-number
argument for causal memory over pure vector similarity.
"""
