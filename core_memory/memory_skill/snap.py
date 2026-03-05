from __future__ import annotations

from difflib import SequenceMatcher


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(a=(a or "").lower(), b=(b or "").lower()).ratio()


def _bucket(score: float, threshold: float) -> str:
    if score >= max(threshold + 0.15, 0.85):
        return "high"
    if score >= threshold:
        return "medium"
    return "low"


def _snap_one(value: str, allowed: list[str], threshold: float = 0.62) -> dict:
    if not value or not allowed:
        return {"value": value or "", "snapped": None, "confidence": 0.0, "confidence_label": "low", "candidates": []}
    ranked = sorted(((x, _sim(value, x)) for x in allowed), key=lambda t: (-t[1], t[0]))
    top, score = ranked[0]
    label = _bucket(score, threshold)
    if score < threshold:
        return {
            "value": value,
            "snapped": None,
            "confidence": round(score, 4),
            "confidence_label": label,
            "candidates": [{"value": x, "score": round(s, 4)} for x, s in ranked[:2]],
        }
    return {
        "value": value,
        "snapped": top,
        "confidence": round(score, 4),
        "confidence_label": label,
        "candidates": [{"value": x, "score": round(s, 4)} for x, s in ranked[:2]],
    }


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

    rels = [str(x) for x in (out.get("relation_types") or [])][:3]
    rel_snapped = []
    rel_ds = []
    for r in rels:
        d = _snap_one(r, list(catalog.get("relation_types") or []), threshold=0.6)
        if d.get("snapped"):
            rel_snapped.append(d.get("snapped"))
        rel_ds.append(d)
    out["relation_types"] = sorted(set(rel_snapped))
    decisions["relation_types"] = rel_ds

    bead_typed = [str(x) for x in (out.get("bead_types") or [])][:3]
    bt_snapped = []
    bt_ds = []
    for bt in bead_typed:
        d = _snap_one(bt, list(catalog.get("bead_types") or []), threshold=0.7)
        if d.get("snapped"):
            bt_snapped.append(d.get("snapped"))
        bt_ds.append(d)
    out["bead_types"] = sorted(set(bt_snapped))
    decisions["bead_types"] = bt_ds

    intent = str(out.get("intent") or "other")
    if intent not in {"remember", "causal", "what_changed", "when", "other"}:
        intent = "other"
    out["intent"] = intent

    k = int(out.get("k") or 10)
    out["k"] = max(1, min(30, k))

    out["must_terms"] = [str(x) for x in (out.get("must_terms") or [])][:5]
    out["avoid_terms"] = [str(x) for x in (out.get("avoid_terms") or [])][:5]
    out["require_structural"] = bool(out.get("require_structural"))

    return {"snapped": out, "decisions": decisions}
