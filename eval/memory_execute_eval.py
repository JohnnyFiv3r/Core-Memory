from __future__ import annotations

import json
import random
from pathlib import Path

from core_memory.retrieval.query_norm import classify_intent, resolve_query_anchors
from core_memory.tools.memory import execute

ROOT = '/home/node/.openclaw/workspace/memory'


def _query_set_50(seed: int = 42) -> list[str]:
    random.seed(seed)
    base = [
        'promotion inflation',
        'what was that thing where everything got promoted',
        'promotion blow-up what happened',
        'why did compaction get starved during promotion',
        'candidate-only promotion rationale',
        'why did we stop auto promoting everything',
        'agent authoritative promotion why',
        'what changed in the link edge graph sync',
        'quick recap of structural pipeline updates',
        'immutable causal sync summary',
        'remember retrieval hardening work',
        'memory reason retrieval updates',
        'remind me what we shipped for graph+archive recall',
        'when did we do sync-structural strict apply',
        'what changed in memory reason tool',
    ]
    mods = [
        '{q}', 'can you {q}', 'pls {q}', 'briefly: {q}', 'in one line, {q}',
        '{q} again', 'for the project, {q}', 'quickly {q}', 'need context: {q}', '{q}?',
    ]
    out = []
    while len(out) < 50:
        nq = random.choice(mods).format(q=random.choice(base))
        if nq not in out:
            out.append(nq)
    return out


def main() -> int:
    rows = []
    for q in _query_set_50():
        ic = classify_intent(q).get('intent_class') or 'other'
        am = resolve_query_anchors(q, Path(ROOT))
        req = {
            'raw_query': q,
            'intent': ic,
            'constraints': {'require_structural': False},
            'facets': {
                'incident_ids': [x.get('incident_id') for x in (am.get('matched_incidents') or []) if x.get('incident_id')][:1],
                'topic_keys': [x.get('topic_key') for x in (am.get('matched_topics') or []) if x.get('topic_key')][:2],
            },
            'k': 8,
        }
        out = execute(req, root=ROOT, explain=True)
        g = out.get('grounding') or {}
        ex = out.get('explain') or {}
        cd = ex.get('confidence_diagnostics') or {}
        rows.append(
            {
                'query': q,
                'intent': ic,
                'results': len(out.get('results') or []),
                'chains': len(out.get('chains') or []),
                'confidence': str(out.get('confidence') or ''),
                'next_action': out.get('next_action'),
                'anchor': bool((out.get('snapped') or {}).get('incident_id')) or bool((out.get('snapped') or {}).get('topic_keys') or []),
                'grounding_required': bool(g.get('required')),
                'grounding_achieved': bool(g.get('achieved')),
                'grounding_reason': g.get('reason'),
                'chain_quality': float(cd.get('chain_quality') or 0.0),
                'warnings': out.get('warnings') or [],
            }
        )

    non_c = [r for r in rows if r['intent'] in {'remember', 'what_changed', 'when', 'other'}]
    ca = [r for r in rows if r['intent'] == 'causal']

    summary = {
        'count': len(rows),
        'ok_rate': 1.0,
        'non_empty_results_rate': round(sum(1 for r in rows if r['results'] > 0) / len(rows), 4),
        'anchor_presence_rate': round(sum(1 for r in rows if r['anchor']) / len(rows), 4),
        'confidence_high_rate': round(sum(1 for r in rows if r['confidence'] == 'high') / len(rows), 4),
        'confidence_medium_rate': round(sum(1 for r in rows if r['confidence'] == 'medium') / len(rows), 4),
        'warning_rate': round(sum(1 for r in rows if r['warnings']) / len(rows), 4),
        'answerable_rate': round(sum(1 for r in rows if r['next_action'] == 'answer') / len(rows), 4),
        'answerable_rate_non_causal': round(sum(1 for r in non_c if r['next_action'] == 'answer') / max(1, len(non_c)), 4),
        'causal_grounding_achieved_rate': round(sum(1 for r in ca if r['grounding_achieved']) / max(1, len(ca)), 4),
        'causal_strong_grounding_rate': round(sum(1 for r in ca if r['chain_quality'] >= 0.2) / max(1, len(ca)), 4),
    }

    print(json.dumps({'summary': summary, 'rows': rows}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
