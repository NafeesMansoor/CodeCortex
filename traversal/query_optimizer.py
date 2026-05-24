"""Traversal query optimizer.

Applies pre-traversal optimizations to TraversalConfig:
  - Token budget enforcement
  - Edge-type pruning based on query intent
  - Depth capping for large graphs
  - Semantic filtering via cluster membership
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Optional

from core.types import EdgeKind, NodeKind, TraversalConfig

logger = logging.getLogger(__name__)


@dataclass
class OptimizationHints:
    """Caller-provided hints to guide optimization decisions."""

    token_budget: Optional[int] = None     # Max tokens for resulting context
    avg_node_tokens: int = 150             # Estimated tokens per node
    query_intent: Optional[str] = None    # "impact" | "callers" | "imports" | None
    cluster_ids: Optional[list[int]] = None  # Restrict to these cluster IDs


class QueryOptimizer:
    """Rewrites TraversalConfig to avoid expensive or unbounded traversals.

    Usage:
        optimizer = QueryOptimizer(graph_store)
        optimized_config = optimizer.optimize(config, hints)
        results = engine.bfs(start_nodes, optimized_config)
    """

    # Maximum recommended depth before graph explosion risk
    SAFE_DEPTH_LIMIT = 8
    # Maximum nodes before pruning becomes mandatory
    SAFE_NODE_LIMIT = 5000

    def __init__(self, graph_store):
        self.graph_store = graph_store

    def optimize(
        self,
        config: TraversalConfig,
        hints: Optional[OptimizationHints] = None,
    ) -> TraversalConfig:
        """Return an optimized copy of config.

        Does not mutate the original.
        """
        hints = hints or OptimizationHints()
        config = self._apply_depth_cap(config)
        config = self._apply_token_budget(config, hints)
        config = self._apply_intent_filter(config, hints)
        config = self._apply_node_cap(config)
        return config

    def _apply_depth_cap(self, config: TraversalConfig) -> TraversalConfig:
        if config.max_depth > self.SAFE_DEPTH_LIMIT:
            logger.debug(
                "Capping traversal depth from %d to %d",
                config.max_depth,
                self.SAFE_DEPTH_LIMIT,
            )
            return replace(config, max_depth=self.SAFE_DEPTH_LIMIT)
        return config

    def _apply_token_budget(
        self, config: TraversalConfig, hints: OptimizationHints
    ) -> TraversalConfig:
        if hints.token_budget is None:
            return config
        max_nodes_by_budget = hints.token_budget // max(hints.avg_node_tokens, 1)
        effective_max = min(config.max_nodes, max_nodes_by_budget)
        if effective_max < config.max_nodes:
            logger.debug(
                "Reducing max_nodes from %d to %d to fit token budget %d",
                config.max_nodes,
                effective_max,
                hints.token_budget,
            )
            return replace(config, max_nodes=effective_max)
        return config

    def _apply_intent_filter(
        self, config: TraversalConfig, hints: OptimizationHints
    ) -> TraversalConfig:
        intent_edge_map = {
            "impact": [EdgeKind.CALLS, EdgeKind.IMPORTS_FROM, EdgeKind.DEPENDS_ON],
            "callers": [EdgeKind.CALLS],
            "imports": [EdgeKind.IMPORTS_FROM],
            "inheritance": [EdgeKind.INHERITS, EdgeKind.IMPLEMENTS],
            "tests": [EdgeKind.TESTED_BY],
        }
        intent = hints.query_intent
        if intent and intent in intent_edge_map:
            merged = list(
                set(intent_edge_map[intent]) | set(config.edge_filter or [])
            )
            logger.debug("Applying intent filter '%s': edges=%s", intent, merged)
            return replace(config, edge_filter=merged)
        return config

    def _apply_node_cap(self, config: TraversalConfig) -> TraversalConfig:
        if config.max_nodes > self.SAFE_NODE_LIMIT:
            logger.debug(
                "Capping max_nodes from %d to %d",
                config.max_nodes,
                self.SAFE_NODE_LIMIT,
            )
            return replace(config, max_nodes=self.SAFE_NODE_LIMIT)
        return config

    def estimate_size(
        self,
        start_nodes: list[str],
        config: TraversalConfig,
    ) -> int:
        """Estimate traversal result size via lightweight BFS count.

        Traverses only up to depth 2 to get a rough count without running
        the full query. Used to pre-check whether token budget is sufficient.
        """
        visited = set(start_nodes)
        frontier = list(start_nodes)

        for _ in range(min(2, config.max_depth)):
            next_frontier = []
            for node_name in frontier:
                edges = self.graph_store.get_outgoing_edges(node_name)
                for edge in edges:
                    if config.edge_filter and edge.kind not in config.edge_filter:
                        continue
                    if edge.target not in visited:
                        visited.add(edge.target)
                        next_frontier.append(edge.target)
            frontier = next_frontier

        return len(visited)
