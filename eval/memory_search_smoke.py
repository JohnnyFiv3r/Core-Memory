from __future__ import annotations

import json
from pathlib import Path

from core_memory.retrieval.tools.memory_search import get_search_form, search_typed

ROOT = Path('/home/node/.openclaw/workspace/memory')

CASES = [
    {
        'name': 'causal_promotion',
        'submission': {
            'intent': 'causal',
            'query_text': 'why did we move to candidate-first promotion',
            'topic_keys': ['promotion_workflow'],
            'require_structural': False,
            'k': 8,
        },
    },
    {
        'name': 'changes_structural',
        'submission': {
            'intent': 'what_changed',
            'query_text': 'what changed in structural sync pipeline',
            'topic_keys': ['structural_sync'],
            'k': 8,
        },
    },
    {
        'name': 'remember_graph_archive',
        'submission': {
            'intent': 'remember',
            'query_text': 'remember graph archive retrieval work',
            'topic_keys': ['graph_archive_retrieval'],
            'k': 8,
        },
    },
]


def main() -> int:
    form = get_search_form(str(ROOT))
    out = {
        'schema_version': 'memory_search_smoke.v1',
        'form_schema': form.get('schema_version'),
        'cases': [],
    }

    for c in CASES:
        r = search_typed(c['submission'], root=str(ROOT), explain=True)
        out['cases'].append({
            'name': c['name'],
            'ok': bool(r.get('ok')),
            'result_count': len(r.get('results') or []),
            'chain_count': len(r.get('chains') or []),
            'confidence': r.get('confidence'),
            'suggested_next': r.get('suggested_next'),
            'warnings': r.get('warnings') or [],
        })

    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
