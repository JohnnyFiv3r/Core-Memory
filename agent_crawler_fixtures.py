from __future__ import annotations

_STATE = {"fail_once_calls": 0, "always_fail_calls": 0, "invalid_then_success_calls": 0}


def reset_state() -> None:
    _STATE["fail_once_calls"] = 0
    _STATE["always_fail_calls"] = 0
    _STATE["invalid_then_success_calls"] = 0


def fail_once_then_success(payload: dict):
    _STATE["fail_once_calls"] += 1
    if _STATE["fail_once_calls"] == 1:
        raise RuntimeError("transient_failure")
    req = dict(payload.get("request") or {})
    turn_id = str(req.get("turn_id") or "t")
    return {
        "crawler_updates": {
            "beads_create": [
                {
                    "type": "decision",
                    "title": "Agent derived bead",
                    "summary": ["agent summary"],
                    "because": ["agent rationale"],
                    "retrieval_eligible": True,
                    "retrieval_title": "Agent derived bead",
                    "retrieval_facts": ["agent summary"],
                    "entities": ["Agent"],
                    "topics": ["decision"],
                    "source_turn_ids": [turn_id],
                }
            ],
            "associations": [
                {
                    "source_bead_id": "seed-src",
                    "target_bead_id": "seed-dst",
                    "relationship": "supports",
                    "reason_text": "agent link",
                    "confidence": 0.7,
                }
            ],
        }
    }


def always_fail(payload: dict):
    _STATE["always_fail_calls"] += 1
    raise RuntimeError("always_fail")


def invalid_then_success(payload: dict):
    _STATE["invalid_then_success_calls"] += 1
    req = dict(payload.get("request") or {})
    turn_id = str(req.get("turn_id") or "t")
    if _STATE["invalid_then_success_calls"] == 1:
        return {"crawler_updates": {"beads_create": [{"title": "missing required fields"}]}}
    assert (payload.get("prior_error") or {}).get("code") == "agent_bead_fields_missing"
    return {
        "crawler_updates": {
            "beads_create": [
                {
                    "type": "decision",
                    "title": "Corrected agent bead",
                    "summary": ["agent summary"],
                    "because": ["agent used validation feedback"],
                    "retrieval_eligible": True,
                    "retrieval_title": "Corrected agent bead",
                    "retrieval_facts": ["agent summary"],
                    "entities": ["Agent"],
                    "topics": ["decision"],
                    "source_turn_ids": [turn_id],
                }
            ]
        }
    }
