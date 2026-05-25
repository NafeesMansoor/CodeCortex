"""Unified graph traversal engine for CodeCortex.

Supports BFS, DFS, shortest-path, taint-propagation, and weighted traversal.
Uses an AdjacencyCache (in-memory edge maps) so repeated queries skip SQL
lookups entirely.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

from core.types import EdgeKind, NodeKind, TraversalConfig

logger = logging.getLogger(__name__)


@dataclass
class TraversalNode:
    """A node in traversal results with path information."""

    qualified_name: str
    node_kind: NodeKind
    depth: int
    path: list[str]
    edges_traversed: list[EdgeKind]


@dataclass
class TaintFlow:
    """One data-flow path discovered during taint propagation."""

    source: str
    sink: str
    path: list[str]
    edge_kinds: list[EdgeKind]
    depth: int


class AdjacencyCache:
    """In-memory edge index built once from a GraphStore.

    Stores forward (outgoing) and backward (incoming) adjacency lists so
    traversal avoids per-hop SQL queries.

    Usage:
        cache = AdjacencyCache.build(store)
        engine = TraversalEngine(store, adjacency_cache=cache)
    """

    def __init__(self) -> None:
        # qname → list[(target_qname, EdgeKind)]
        self._forward: dict[str, list[tuple[str, EdgeKind]]] = {}
        # qname → list[(source_qname, EdgeKind)]
        self._backward: dict[str, list[tuple[str, EdgeKind]]] = {}

    @classmethod
    def build(cls, store) -> "AdjacencyCache":
        cache = cls()
        for edge in store.all_edges():
            cache._forward.setdefault(edge.source, []).append((edge.target, edge.kind))
            cache._backward.setdefault(edge.target, []).append((edge.source, edge.kind))
        logger.debug(
            "AdjacencyCache built: %d sources, %d targets",
            len(cache._forward),
            len(cache._backward),
        )
        return cache

    def outgoing(self, node: str) -> list[tuple[str, EdgeKind]]:
        return self._forward.get(node, [])

    def incoming(self, node: str) -> list[tuple[str, EdgeKind]]:
        return self._backward.get(node, [])

    def invalidate(self, node: str) -> None:
        self._forward.pop(node, None)
        self._backward.pop(node, None)

    def size(self) -> int:
        return sum(len(v) for v in self._forward.values())


class TraversalEngine:
    """Unified interface for graph traversal queries.

    Supports BFS, DFS, shortest path, impact radius, taint propagation,
    and weighted BFS. Optionally uses an AdjacencyCache for sub-millisecond
    lookups on cached graphs.
    """

    def __init__(
        self,
        graph_store,
        enable_memoization: bool = True,
        adjacency_cache: Optional[AdjacencyCache] = None,
    ):
        self.graph_store = graph_store
        self.enable_memoization = enable_memoization
        self._memo_cache: dict[str, list[TraversalNode]] = {}
        self._adj: Optional[AdjacencyCache] = adjacency_cache

    def use_cache(self, cache: AdjacencyCache) -> None:
        """Attach a pre-built AdjacencyCache and clear memoization."""
        self._adj = cache
        self._memo_cache.clear()

    # ------------------------------------------------------------------
    # Core BFS / DFS
    # ------------------------------------------------------------------

    def bfs(
        self,
        start_nodes: list[str],
        config: Optional[TraversalConfig] = None,
    ) -> list[TraversalNode]:
        if config is None:
            config = TraversalConfig()

        if self.enable_memoization:
            key = self._memo_key(start_nodes, config)
            if key in self._memo_cache:
                return self._memo_cache[key]

        results: list[TraversalNode] = []
        visited: set[str] = set()
        queue: deque = deque()

        for node_name in start_nodes:
            node = self.graph_store.get_node(node_name)
            if node:
                queue.append((node, 0, [node_name], []))
                visited.add(node_name)

        while queue and len(results) < config.max_nodes:
            node, depth, path, edges = queue.popleft()

            if depth > config.max_depth:
                continue
            if config.node_filter and node.kind not in config.node_filter:
                # Still expand children even if this node is filtered out
                for neighbor_name, edge_kind in self._neighbors_out(node.qualified_name, config):
                    if neighbor_name not in visited:
                        neighbor = self.graph_store.get_node(neighbor_name)
                        if neighbor:
                            visited.add(neighbor_name)
                            queue.append(
                                (neighbor, depth + 1, path + [neighbor_name], edges + [edge_kind])
                            )
                continue

            results.append(
                TraversalNode(
                    qualified_name=node.qualified_name,
                    node_kind=node.kind,
                    depth=depth,
                    path=path,
                    edges_traversed=edges,
                )
            )

            for neighbor_name, edge_kind in self._neighbors_out(node.qualified_name, config):
                if neighbor_name not in visited and len(results) < config.max_nodes:
                    neighbor = self.graph_store.get_node(neighbor_name)
                    if neighbor:
                        visited.add(neighbor_name)
                        queue.append(
                            (neighbor, depth + 1, path + [neighbor_name], edges + [edge_kind])
                        )

        if self.enable_memoization:
            self._memo_cache[self._memo_key(start_nodes, config)] = results
        return results

    def dfs(
        self,
        start_nodes: list[str],
        config: Optional[TraversalConfig] = None,
    ) -> list[TraversalNode]:
        if config is None:
            config = TraversalConfig()

        results: list[TraversalNode] = []
        visited: set[str] = set()

        def _dfs(node, depth, path, edges):
            if len(results) >= config.max_nodes or depth > config.max_depth:
                return
            visited.add(node.qualified_name)

            if not config.node_filter or node.kind in config.node_filter:
                results.append(
                    TraversalNode(
                        qualified_name=node.qualified_name,
                        node_kind=node.kind,
                        depth=depth,
                        path=path,
                        edges_traversed=edges,
                    )
                )

            for neighbor_name, edge_kind in self._neighbors_out(node.qualified_name, config):
                if neighbor_name not in visited:
                    neighbor = self.graph_store.get_node(neighbor_name)
                    if neighbor:
                        _dfs(neighbor, depth + 1, path + [neighbor_name], edges + [edge_kind])

        for name in start_nodes:
            node = self.graph_store.get_node(name)
            if node and name not in visited:
                _dfs(node, 0, [name], [])
        return results

    # ------------------------------------------------------------------
    # Shortest path
    # ------------------------------------------------------------------

    def shortest_path(
        self,
        source: str,
        target: str,
        config: Optional[TraversalConfig] = None,
    ) -> Optional[list[str]]:
        if config is None:
            config = TraversalConfig(max_depth=10)

        visited: set[str] = set([source])
        parent_map: dict[str, Optional[str]] = {source: None}
        queue: deque = deque([source])

        while queue:
            current = queue.popleft()
            if current == target:
                path = []
                node = target
                while node is not None:
                    path.append(node)
                    node = parent_map[node]
                return list(reversed(path))

            for neighbor_name, _ in self._neighbors_out_raw(current, config):
                if neighbor_name not in visited:
                    visited.add(neighbor_name)
                    parent_map[neighbor_name] = current
                    queue.append(neighbor_name)

        return None

    # ------------------------------------------------------------------
    # Impact radius
    # ------------------------------------------------------------------

    def get_impact_radius(
        self,
        start_nodes: list[str],
        max_depth: int = 5,
    ) -> dict[str, int]:
        """Return all nodes affected by changes to start_nodes, mapped to depth."""
        config = TraversalConfig(max_depth=max_depth)
        results = self.bfs(start_nodes, config)
        impact: dict[str, int] = {}
        for n in results:
            if n.qualified_name not in impact or n.depth < impact[n.qualified_name]:
                impact[n.qualified_name] = n.depth
        return impact

    # ------------------------------------------------------------------
    # Taint propagation
    # ------------------------------------------------------------------

    def taint_traverse(
        self,
        sources: list[str],
        max_depth: int = 6,
        edge_filter: Optional[list[EdgeKind]] = None,
    ) -> list[TaintFlow]:
        """Propagate taint from source nodes through data-flow edges.

        Follows WRITES, READS, CALLS edges (or custom edge_filter) to find
        all reachable sinks. Returns one TaintFlow per source→sink path.

        Args:
            sources: Taint source qualified names.
            max_depth: Maximum propagation depth.
            edge_filter: Override default data-flow edge set.

        Returns:
            List of TaintFlow records.
        """
        taint_edges = edge_filter or [
            EdgeKind.WRITES,
            EdgeKind.READS,
            EdgeKind.CALLS,
            EdgeKind.DEPENDS_ON,
        ]

        flows: list[TaintFlow] = []

        for source in sources:
            visited: set[str] = set([source])
            queue: deque = deque([(source, [source], [], 0)])

            while queue:
                current, path, edge_path, depth = queue.popleft()
                if depth >= max_depth:
                    continue

                for neighbor_name, edge_kind in self._neighbors_out_raw_filtered(
                    current, taint_edges
                ):
                    if neighbor_name in visited:
                        continue
                    visited.add(neighbor_name)
                    new_path = path + [neighbor_name]
                    new_edges = edge_path + [edge_kind]

                    flows.append(
                        TaintFlow(
                            source=source,
                            sink=neighbor_name,
                            path=new_path,
                            edge_kinds=new_edges,
                            depth=depth + 1,
                        )
                    )
                    queue.append((neighbor_name, new_path, new_edges, depth + 1))

        return flows

    # ------------------------------------------------------------------
    # Weighted BFS (priority-queue, edge-type weighted)
    # ------------------------------------------------------------------

    def weighted_bfs(
        self,
        start_nodes: list[str],
        config: Optional[TraversalConfig] = None,
        edge_weights: Optional[dict[EdgeKind, float]] = None,
    ) -> list[TraversalNode]:
        """BFS variant that expands lower-cost (higher-priority) paths first.

        Edge weights default to 1.0; lower weight = higher priority.
        Useful for semantic traversal where CALLS edges matter more than CONTAINS.

        Returns nodes ordered by accumulated cost (lowest first).
        """
        import heapq

        if config is None:
            config = TraversalConfig()

        weights = edge_weights or {}
        default_w = 1.0

        visited: set[str] = set()
        # (cost, depth, name, path, edge_path)
        heap: list = []

        for name in start_nodes:
            node = self.graph_store.get_node(name)
            if node:
                heapq.heappush(heap, (0.0, 0, name, [name], []))
                visited.add(name)

        results: list[TraversalNode] = []

        while heap and len(results) < config.max_nodes:
            cost, depth, name, path, edge_path = heapq.heappop(heap)

            node = self.graph_store.get_node(name)
            if not node:
                continue
            if depth > config.max_depth:
                continue
            if config.node_filter and node.kind not in config.node_filter:
                continue

            results.append(
                TraversalNode(
                    qualified_name=name,
                    node_kind=node.kind,
                    depth=depth,
                    path=path,
                    edges_traversed=edge_path,
                )
            )

            for neighbor_name, edge_kind in self._neighbors_out(name, config):
                if neighbor_name not in visited:
                    visited.add(neighbor_name)
                    w = weights.get(edge_kind, default_w)
                    heapq.heappush(
                        heap,
                        (
                            cost + w,
                            depth + 1,
                            neighbor_name,
                            path + [neighbor_name],
                            edge_path + [edge_kind],
                        ),
                    )

        return results

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_memoization(self) -> None:
        self._memo_cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _neighbors_out(self, node_name: str, config: TraversalConfig) -> list[tuple[str, EdgeKind]]:
        """Outgoing neighbors, filtered by edge_filter and direction."""
        raw = self._neighbors_out_raw(node_name, config)

        if config.direction == "bidirectional":
            raw = raw + self._neighbors_in_raw(node_name, config)
        elif config.direction == "inbound":
            raw = self._neighbors_in_raw(node_name, config)

        return raw

    def _neighbors_out_raw(
        self, node_name: str, config: TraversalConfig
    ) -> list[tuple[str, EdgeKind]]:
        if self._adj is not None:
            pairs = self._adj.outgoing(node_name)
        else:
            edges = self.graph_store.get_outgoing_edges(node_name)
            pairs = [(e.target, e.kind) for e in edges]

        if config.edge_filter:
            pairs = [(t, k) for t, k in pairs if k in config.edge_filter]
        return pairs

    def _neighbors_in_raw(
        self, node_name: str, config: TraversalConfig
    ) -> list[tuple[str, EdgeKind]]:
        if self._adj is not None:
            pairs = self._adj.incoming(node_name)
        else:
            edges = self.graph_store.get_incoming_edges(node_name)
            pairs = [(e.source, e.kind) for e in edges]

        if config.edge_filter:
            pairs = [(s, k) for s, k in pairs if k in config.edge_filter]
        return pairs

    def _neighbors_out_raw_filtered(
        self, node_name: str, edge_filter: list[EdgeKind]
    ) -> list[tuple[str, EdgeKind]]:
        if self._adj is not None:
            pairs = self._adj.outgoing(node_name)
        else:
            edges = self.graph_store.get_outgoing_edges(node_name)
            pairs = [(e.target, e.kind) for e in edges]
        return [(t, k) for t, k in pairs if k in edge_filter]

    def _memo_key(self, start_nodes: list[str], config: TraversalConfig) -> str:
        ef = tuple(sorted(e.value for e in (config.edge_filter or [])))
        nf = tuple(sorted(n.value for n in (config.node_filter or [])))
        return f"{':'.join(sorted(start_nodes))}|d{config.max_depth}|n{config.max_nodes}|{config.direction}|e{ef}|nf{nf}"
