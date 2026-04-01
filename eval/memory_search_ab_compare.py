from __future__ import annotations

import json
from pathlib import Path

from core_memory.retrieval.tools.memory_reason import memory_reason
from core_memory.retrieval.tools.memory_search import search_typed
from core_memory.retrieval.query_norm import resolve_query_anchors

ROOT = '/home/node/.openclaw/workspace/memory'
FIXTURE = Path('/home/node/.openclaw/workspace/eval/fixtures/paraphrase_kpi_pack.json')


def _typed_submission(intent_class: str, query: str) -> dict:
    intent = intent_class if intent_class in {'remember', 'causal', 'what_changed', 'when'} else 'other'
    am = resolve_query_anchors(query, Path(ROOT))
    incidents = am.get('matched_incidents') or []
    topics = am.get('matched_topics') or []
    return {
        'intent': intent,
        'query_text': query,
        'incident_id': (incidents[0].get('incident_id') if incidents else None),
        'topic_keys': [t.get('topic_key') for t in topics[:2] if t.get('topic_key')],
        'k': 8,
        # Agent-controlled knob: do not auto-force structural for causal intent.
        'require_structural': False,
    }


def main() -> int:
    fx = json.loads(FIXTURE.read_text(encoding='utf-8'))
    families = fx.get('families') or []

    rows = []
    a_ok = 0
    b_ok = 0
    b_anchor = 0
    b_high = 0
    b_warn = 0
    b_total = 0
    non_causal_a_total = 0
    non_causal_a_why = 0

    for fam in families:
        intent_class = str(fam.get('intent_class') or 'remember')
        for q in (fam.get('phrasings') or []):
            q = str(q)
            a = memory_reason(q, root=ROOT, k=8, debug=False, explain=False)
            a_int = a.get('intent') or {}
            a_route = str(a_int.get('selected') or '')

            sub = _typed_submission(intent_class, q)
            b = search_typed(submission=sub, root=ROOT, explain=True)
            b_snapped = b.get('snapped_query') or {}

            a_ok += 1 if bool(a.get('ok')) else 0
            b_ok += 1 if bool(b.get('ok')) else 0
            b_total += 1
            has_anchor = bool(b_snapped.get('incident_id')) or bool(b_snapped.get('topic_keys') or [])
            b_anchor += 1 if has_anchor else 0
            b_high += 1 if str(b.get('confidence') or '') == 'high' else 0
            b_warn += 1 if bool(b.get('warnings') or []) else 0

            if intent_class != 'causal':
                non_causal_a_total += 1
                non_causal_a_why += 1 if a_route == 'why' else 0

            rows.append({
                'intent_class': intent_class,
                'query': q,
                'A': {
                    'ok': bool(a.get('ok')),
                    'route': a_route,
                    'result_count': len(a.get('citations') or []),
                    'chain_count': len(a.get('chains') or []),
                },
                'B': {
                    'ok': bool(b.get('ok')),
                    'result_count': len((b.get('anchors') or b.get('results') or [])),
                    'chain_count': len(b.get('chains') or []),
                    'confidence': b.get('confidence'),
                    'suggested_next': b.get('next_action') or b.get('suggested_next'),
                    'has_anchor': has_anchor,
                    'warnings': b.get('warnings') or [],
                },
            })

    out = {
        'schema_version': 'memory_search_ab_compare.v1',
        'total_queries': len(rows),
        'summary': {
            'A_ok_rate': round(a_ok / max(1, len(rows)), 4),
            'B_ok_rate': round(b_ok / max(1, len(rows)), 4),
            'B_anchor_presence_rate': round(b_anchor / max(1, b_total), 4),
            'B_confidence_high_rate': round(b_high / max(1, b_total), 4),
            'B_warning_rate': round(b_warn / max(1, b_total), 4),
            'A_non_causal_why_rate': round(non_causal_a_why / max(1, non_causal_a_total), 4),
        },
        'rows': rows,
    }

    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
