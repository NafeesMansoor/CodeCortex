"""Hybrid CPG graph pruner.

Reduces a full CPG (3000+ nodes, 16k+ edges) to a compact semantic graph:
  - Keeps only architecturally meaningful node kinds
  - Drops VARIABLE / PARAMETER / CALLSITE noise nodes
  - Removes edges whose endpoints are non-semantic
  - Deduplicates parallel edges of the same kind
  - Optionally collapses redundant CONTAINS chains

Target: 60-80% edge reduction while preserving semantic fidelity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.types import NodeKind, EdgeKind
from graph.graph_store import GraphStore
from graph.schema import CPGNode, CPGEdge

logger = logging.getLogger(__name__)

# Nodes that carry architectural signal
_SEMANTIC_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CLASS,
    NodeKind.INTERFACE,
    NodeKind.ENDPOINT,
    NodeKind.TEST,
    NodeKind.MODULE,
    NodeKind.FILE,
    NodeKind.ENUM,
    NodeKind.STRUCT,
    NodeKind.TYPE,
    NodeKind.NAMESPACE,
})

# Edge types with their semantic weight (used in ranking / traversal)
EDGE_WEIGHTS: dict[EdgeKind, float] = {
    EdgeKind.CALLS: 1.0,
    EdgeKind.INHERITS: 0.9,
    EdgeKind.TESTS: 0.7,
    EdgeKind.CONTAINS: 0.5,
    EdgeKind.IMPORTS_FROM: 0.4,
    EdgeKind.CONTROLS: 0.2,
    EdgeKind.READS: 0.15,
    EdgeKind.WRITES: 0.15,
}


@dataclass
class PruneStats:
    nodes_before: int = 0
    nodes_after: int = 0
    edges_before: int = 0
    edges_after: int = 0
    edges_deduped: int = 0

    @property
    def node_reduction_pct(self) -> float:
        if self.nodes_before == 0:
            return 0.0
        return round((1 - self.nodes_after / self.nodes_before) * 100, 1)

    @property
    def edge_reduction_pct(self) -> float:
        if self.edges_before == 0:
            return 0.0
        return round((1 - self.edges_after / self.edges_before) * 100, 1)

    def __str__(self) -> str:
        return (
            f"nodes {self.nodes_before}→{self.nodes_after} "
            f"(-{self.node_reduction_pct}%)  "
            f"edges {self.edges_before}→{self.edges_after} "
            f"(-{self.edge_reduction_pct}%, deduped {self.edges_deduped})"
        )


class GraphPruner:
    """Prune a full CPG to a compact semantic graph.

    Usage:
        pruned_store, stats = GraphPruner().prune(full_store)
    """

    def __init__(self, semantic_kinds: frozenset[NodeKind] = _SEMANTIC_KINDS):
        self.semantic_kinds = semantic_kinds

    def prune(self, store: GraphStore) -> tuple[GraphStore, PruneStats]:
        """Return a new semantically-filtered GraphStore and pruning statistics."""
        stats = PruneStats(
            nodes_before=store.node_count(),
            edges_before=store.edge_count(),
        )

        pruned = GraphStore(":memory:")

        # Collect semantic node names in one pass
        semantic: set[str] = set()
        for node in store.all_nodes():
            if node.kind in self.semantic_kinds:
                semantic.add(node.qualified_name)
                pruned.add_node(node)

        # Add edges where BOTH endpoints are semantic — deduplicate in the same pass
        seen: set[tuple] = set()
        deduped = 0
        for edge in store.all_edges():
            if edge.source not in semantic or edge.target not in semantic:
                continue
            key = (edge.kind, edge.source, edge.target)
            if key in seen:
                deduped += 1
                continue
            seen.add(key)
            pruned.add_edge(edge)

        stats.nodes_after = pruned.node_count()
        stats.edges_after = pruned.edge_count()
        stats.edges_deduped = deduped

        logger.info("GraphPruner: %s", stats)
        return pruned, stats
