from __future__ import annotations

from difflib import SequenceMatcher


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(a=(a or "").lower(), b=(b or "").lower()).ratio()


def _snap_one(value: str, allowed: list[str], threshold: float = 0.62) -> dict:
    if not value or not allowed:
        return {"value": "", "snapped": None, "confidence": 0.0}
    ranked = sorted(((x, _sim(value, x)) for x in allowed), key=lambda t: (-t[1], t[0]))
    top, score = ranked[0]
    if score < threshold:
        return {"value": value, "snapped": None, "confidence": round(score, 4), "candidates": ranked[:2]}
    return {"value": value, "snapped": top, "confidence": round(score, 4), "candidates": ranked[:2]}


def snap_form(submission: dict, catalog: dict) -> dict:
    out = dict(submission or {})
    decisions = {}

    iid = str(out.get("incident_id") or "")
    if iid:
        d = _snap_one(iid, list(catalog.get("incident_ids") or []), threshold=0.55)
        out["incident_id"] = d.get("snapped")
        decisions["incident_id"] = d

    tks = [str(x) for x in (out.get("topic_keys") or [])][:3]
    snapped_topics = []
    topic_ds = []
    for t in tks:
        d = _snap_one(t, list(catalog.get("topic_keys") or []), threshold=0.55)
        if d.get("snapped"):
            snapped_topics.append(d.get("snapped"))
        topic_ds.append(d)
    out["topic_keys"] = sorted(set(snapped_topics))
    decisions["topic_keys"] = topic_ds

    bts = [str(x) for x in (out.get("bead_types") or [])][:3]
    out["bead_types"] = [x for x in bts if x in set(catalog.get("bead_types") or [])]

    rts = [str(x) for x in (out.get("relation_types") or [])][:3]
    out["relation_types"] = [x for x in rts if x in set(catalog.get("relation_types") or [])]

    k = int(out.get("k") or 10)
    out["k"] = max(1, min(30, k))

    out["must_terms"] = [str(x) for x in (out.get("must_terms") or [])][:5]
    out["avoid_terms"] = [str(x) for x in (out.get("avoid_terms") or [])][:5]
    out["intent"] = str(out.get("intent") or "other")
    out["require_structural"] = bool(out.get("require_structural"))

    return {"snapped": out, "decisions": decisions}
