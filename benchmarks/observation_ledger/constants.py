"""Versioned names shared by the Observation Ledger benchmark package."""

PACK_SCHEMA = "observation_ledger.pack.v1"
CASE_SCHEMA = "observation_ledger.case.v1"
GOLD_SCHEMA = "observation_ledger.gold.v1"
ADJUDICATION_SCHEMA = "observation_ledger.adjudication.v1"
ADJUDICATION_RUN_SCHEMA = "observation_ledger.adjudication_run.v1"
REPORT_SCHEMA = "observation_ledger.report.v1"
BASELINE_SCHEMA = "observation_ledger.baseline.v1"
THRESHOLDS_SCHEMA = "observation_ledger.thresholds.v1"

VISIBILITIES = {"public", "private"}
SUITES = {"observations", "claims", "associations", "artifacts", "promotion", "retrieval"}
RUN_STATUSES = {"completed", "blocked", "disqualified", "failed"}

SEMANTIC_SCORE_KEYS = (
    "evidence_entailment",
    "label_summary_faithfulness",
    "relevance",
    "sufficiency",
    "appropriate_ambiguity_abstention",
    "causal_directional_correctness",
    "retrieval_answer_support",
)

HARD_INVARIANT_KEYS = (
    "no_benchmark_contamination",
    "schema_and_structural_integrity",
    "admissible_evidence_only",
    "tenant_isolation",
    "no_deterministic_semantic_fallback",
    "provider_failure_writes_no_semantics",
    "no_implicit_latest_wins",
    "no_truth_upgrade_from_salience",
)

FORBIDDEN_RUNTIME_KEYS = {
    "accepted_adjudication",
    "accepted_gold",
    "adjudication",
    "gold",
    "gold_expectations",
    "judge_critique",
    "oracle_answer",
}

AUTHOR_MODEL_ENV_KEYS = (
    "CORE_MEMORY_AGENT_MODEL_CHEAP",
    "CORE_MEMORY_AGENT_MODEL_STANDARD",
    "CORE_MEMORY_AGENT_MODEL_FRONTIER",
    "CORE_MEMORY_ASSOCIATION_JUDGE_MODEL",
    "CORE_MEMORY_BEAD_FIELD_MODEL",
    "CORE_MEMORY_BEAD_TYPE_MODEL",
    "CORE_MEMORY_BECAUSE_MODEL",
    "CORE_MEMORY_CHAT_MODEL",
    "CORE_MEMORY_DREAMER_MODEL",
    "CORE_MEMORY_LLM_MODEL",
    "CORE_MEMORY_RECALL_MODEL",
    "CORE_MEMORY_TURN_MEMORY_AUTHOR_MODEL",
)

AUTHOR_PROVIDER_ENV_KEYS = (
    "CORE_MEMORY_CHAT_PROVIDER",
    "CORE_MEMORY_LLM_PROVIDER",
)
