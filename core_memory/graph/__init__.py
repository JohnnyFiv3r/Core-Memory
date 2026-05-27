from core_memory.graph.core import build_graph, graph_stats, STRUCTURAL_RELS
from core_memory.graph.semantic import (
    add_semantic_edge,
    deactivate_semantic_edge,
    decay_semantic_edges,
    reinforce_semantic_edges,
    update_semantic_edge,
)
from core_memory.graph.structural import (
    add_structural_edge,
    backfill_causal_links,
    backfill_structural_edges,
    infer_structural_edges,
    sync_structural_pipeline,
)
from core_memory.graph.traversal import (
    causal_traverse,
    causal_traverse_bidirectional,
    causal_traverse_chains,
)
