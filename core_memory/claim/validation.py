"""Claim validation and cleanup utilities."""

REQUIRED_CLAIM_FIELDS = {"id", "claim_kind", "subject", "slot", "value", "reason_text", "confidence"}


def validate_claim(data: dict) -> tuple[bool, list[str]]:
    """Validate a claim dict. Returns (is_valid, errors)."""
    errors = []
    for field in REQUIRED_CLAIM_FIELDS:
        if field not in data or data[field] is None:
            errors.append(f"Missing required field: {field}")

    if "reason_text" in data and not str(data["reason_text"]).strip():
        errors.append("reason_text cannot be empty")

    if "subject" in data and not isinstance(data["subject"], str):
        errors.append("subject must be a string")

    if "confidence" in data:
        try:
            c = float(data["confidence"])
            if not (0.0 <= c <= 1.0):
                errors.append("confidence must be between 0.0 and 1.0")
        except (TypeError, ValueError):
            errors.append("confidence must be a number")

    return len(errors) == 0, errors


def validate_claims_batch(claims: list[dict]) -> list[dict]:
    """Validate a batch, return only valid claims."""
    return [c for c in claims if validate_claim(c)[0]]


def dedup_claims(claims: list[dict]) -> list[dict]:
    """Remove duplicate claims by subject+slot within a batch."""
    seen = set()
    result = []
    for claim in claims:
        key = (claim.get("subject", ""), claim.get("slot", ""))
        if key not in seen:
            seen.add(key)
            result.append(claim)
    return result
