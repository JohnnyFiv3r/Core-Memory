from core_memory.policy.association_inference_v21 import (
    INFERENCE_MODE_PERMISSIVE,
    INFERENCE_MODE_STRICT,
    Q_MISSING_OR_INVALID_CONFIDENCE,
    Q_MISSING_REASON_TEXT,
    Q_NONCANONICAL_PREFIX,
    WARN_ALIAS_RATIONALE_TO_REASON_TEXT,
    WARN_NONCANONICAL_PREFIX,
    validate_and_normalize_inference_payload,
)


def _base_payload(**overrides):
    payload = {
        "source_bead": "bead-A",
        "target_bead": "bead-B",
        "relationship": "supports",
        "reason_text": "explicit support",
        "confidence": 0.82,
        "provenance": "model_inferred",
    }
    payload.update(overrides)
    return payload


def test_model_inferred_requires_reason_text_and_confidence():
    payload = _base_payload(reason_text="", confidence=None)
    out = validate_and_normalize_inference_payload(payload, mode=INFERENCE_MODE_STRICT)

    assert out.ok is False
    assert Q_MISSING_REASON_TEXT in out.quarantine_reasons
    assert Q_MISSING_OR_INVALID_CONFIDENCE in out.quarantine_reasons


def test_rationale_alias_maps_to_reason_text_with_warning():
    payload = _base_payload(reason_text="", rationale="legacy rationale")
    out = validate_and_normalize_inference_payload(payload, mode=INFERENCE_MODE_STRICT)

    assert out.ok is True
    assert out.record["reason_text"] == "legacy rationale"
    assert WARN_ALIAS_RATIONALE_TO_REASON_TEXT in out.warnings


def test_precedes_is_accepted_in_strict_mode_without_direction_rewrite():
    payload = _base_payload(relationship="precedes")
    out = validate_and_normalize_inference_payload(payload, mode=INFERENCE_MODE_STRICT)

    assert out.ok is True
    assert out.quarantine_reasons == []
    assert out.warnings == []
    # No silent inversion: source/target are preserved exactly.
    assert out.record["source_bead"] == "bead-A"
    assert out.record["target_bead"] == "bead-B"
    assert out.record["relationship"] == "precedes"


def test_relation_aliases_are_normalized_before_strict_validation():
    payload = _base_payload(relationship="leads_to")
    out = validate_and_normalize_inference_payload(payload, mode=INFERENCE_MODE_STRICT)

    assert out.ok is True
    assert out.quarantine_reasons == []
    assert out.record["relationship"] == "led_to"
    assert out.record["relationship_raw"] == "leads_to"
    assert out.record["normalization_applied"] is True

    enabled = validate_and_normalize_inference_payload(
        _base_payload(relationship="enabled"),
        mode=INFERENCE_MODE_STRICT,
    )
    assert enabled.ok is True
    assert enabled.record["relationship"] == "enables"


def test_active_blocks_label_is_not_normalized_without_endpoint_rewrite():
    payload = _base_payload(relationship="blocks")
    out = validate_and_normalize_inference_payload(payload, mode=INFERENCE_MODE_STRICT)

    assert out.ok is False
    assert f"{Q_NONCANONICAL_PREFIX}blocks" in out.quarantine_reasons
    assert f"{WARN_NONCANONICAL_PREFIX}blocks" in out.warnings


def test_unknown_relation_maps_to_associated_with_in_permissive_mode():
    payload = _base_payload(relationship="mystery_rel")
    out = validate_and_normalize_inference_payload(payload, mode=INFERENCE_MODE_PERMISSIVE)

    assert out.ok is True
    assert out.record["relationship"] == "associated_with"
    assert out.record["relationship_raw"] == "mystery_rel"
    assert out.record["normalization_applied"] is True
    assert f"{WARN_NONCANONICAL_PREFIX}mystery_rel" in out.warnings
