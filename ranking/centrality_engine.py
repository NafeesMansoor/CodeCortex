"""Centrality ranking engine.

Computes graph intelligence scores over the CPG:
  - PageRank (influence / importance)
  - Betweenness centrality (bridge / bottleneck detection)
  - Closeness centrality (approximate via BFS eccentricity inverse)
  - Eigenvector centrality (recursive importance)
  - In/out degree (fan-in, fan-out)

Each scored node exposes:
  - influence_score   (PageRank, normalized)
  - bridge_score      (betweenness, normalized 0-1)
  - closeness_score   (closeness centrality, [0, 1])
  - eigenvector_score (eigenvector centrality, [0, 1])
  - stability_score   (high fan-in + low fan-out = stable API)
  - volatility_score  (high fan-out = many dependencies, likely to change)
  - fan_in / fan_out  (raw degree counts)
  - composite_score   (weighted combination)

Non-semantic node kinds (VARIABLE, PARAMETER, CALLSITE) are excluded from
the computation by default — they dilute PageRank and add noise to ranking.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from core.types import EdgeKind, NodeKind
from graph.graph_store import GraphStore

logger = logging.getLogger(__name__)

# Node kinds that carry architectural meaning and should be ranked
_SEMANTIC_KINDS = frozenset(
    {
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
    }
)


@dataclass
class NodeScore:
    """Centrality scores for a single graph node."""

    qualified_name: str
    influence_score: float  # PageRank
    bridge_score: float  # Betweenness (normalized 0-1)
    closeness_score: float  # Closeness centrality [0, 1]
    eigenvector_score: float  # Eigenvector centrality [0, 1]
    fan_in: int  # In-degree
    fan_out: int  # Out-degree
    stability_score: float  # fan_in / (fan_in + fan_out + 1) — stable API proxy
    volatility_score: float  # fan_out / (fan_in + fan_out + 1) — change-prone proxy
    composite_score: float  # Weighted combined score

    def to_dict(self) -> dict:
        return {
            "qualified_name": self.qualified_name,
            "influence_score": round(self.influence_score, 6),
            "bridge_score": round(self.bridge_score, 6),
            "closeness_score": round(self.closeness_score, 6),
            "eigenvector_score": round(self.eigenvector_score, 6),
            "fan_in": self.fan_in,
            "fan_out": self.fan_out,
            "stability_score": round(self.stability_score, 4),
            "volatility_score": round(self.volatility_score, 4),
            "composite_score": round(self.composite_score, 6),
        }


class CentralityEngine:
    """Computes node importance scores for the Code Property Graph.

    Uses NetworkX when available for fast implementations; falls back to
    pure-Python PageRank + power iteration for environments without NetworkX.

    Non-semantic nodes (VARIABLE, PARAMETER, CALLSITE) are excluded by default
    to prevent rank dilution in large CPGs.

    Usage:
        engine = CentralityEngine(graph_store)
        scores = engine.compute()
        top = engine.top_nodes(scores, n=20)
    """

    # Composite score weights (sum to 1.0)
    WEIGHT_PAGERANK = 0.40
    WEIGHT_BETWEENNESS = 0.25
    WEIGHT_CLOSENESS = 0.15
    WEIGHT_EIGENVECTOR = 0.10
    WEIGHT_FAN_IN = 0.10

    def __init__(
        self,
        store: GraphStore,
        edge_filter: Optional[list[EdgeKind]] = None,
        semantic_only: bool = True,
    ):
        self.store = store
        self.edge_filter = edge_filter or [
            EdgeKind.CALLS,
            EdgeKind.IMPORTS_FROM,
            EdgeKind.INHERITS,
            EdgeKind.IMPLEMENTS,
            EdgeKind.DEPENDS_ON,
        ]
        self.semantic_only = semantic_only

    def compute(self) -> dict[str, NodeScore]:
        """Compute centrality scores for all (semantic) nodes.

        Returns:
            Dict mapping qualified_name → NodeScore.
        """
        all_nodes = self.store.all_nodes()
        edges = self.store.all_edges()

        if not all_nodes:
            return {}

        # Filter to semantic node kinds only
        if self.semantic_only:
            nodes = [n for n in all_nodes if n.kind in _SEMANTIC_KINDS]
        else:
            nodes = list(all_nodes)

        if not nodes:
            return {}

        node_names = [n.qualified_name for n in nodes]
        node_set = set(node_names)

        filtered_edges = [
            e
            for e in edges
            if e.kind in self.edge_filter and e.source in node_set and e.target in node_set
        ]

        try:
            return self._compute_networkx(node_names, filtered_edges)
        except ImportError:
            logger.info("NetworkX not available, using pure-Python centrality")
            return self._compute_python(node_names, filtered_edges)

    def _compute_networkx(self, node_names, edges) -> dict[str, NodeScore]:
        import networkx as nx

        G = nx.DiGraph()
        G.add_nodes_from(node_names)
        G.add_edges_from((e.source, e.target) for e in edges)

        pagerank = nx.pagerank(G, alpha=0.85, max_iter=200)

        G_undirected = G.to_undirected()

        # Betweenness: use k-sample approximation for large graphs to avoid O(V*E)
        n = G.number_of_nodes()
        k_sample = min(n, max(50, n // 10))
        betweenness = nx.betweenness_centrality(
            G_undirected, normalized=True, k=k_sample if n > 200 else None
        )

        # Closeness (nx computes it fast on undirected)
        closeness = nx.closeness_centrality(G_undirected)

        # Eigenvector centrality (may not converge; fallback to degree)
        try:
            eigenvector = nx.eigenvector_centrality(G, max_iter=500)
        except nx.PowerIterationFailedConvergence:
            max_deg = max(dict(G.in_degree()).values(), default=1) or 1
            eigenvector = {n: dict(G.in_degree()).get(n, 0) / max_deg for n in node_names}

        in_degree = dict(G.in_degree())
        out_degree = dict(G.out_degree())
        max_indeg = max(in_degree.values(), default=1) or 1

        return self._build_scores(
            node_names,
            pagerank,
            betweenness,
            closeness,
            eigenvector,
            in_degree,
            out_degree,
            max_indeg,
        )

    def _compute_python(self, node_names, edges) -> dict[str, NodeScore]:
        """Pure-Python: PageRank + approximate betweenness + closeness."""
        n = len(node_names)
        if n == 0:
            return {}

        name_to_idx = {name: i for i, name in enumerate(node_names)}
        out_links: list[list[int]] = [[] for _ in range(n)]
        in_links: list[list[int]] = [[] for _ in range(n)]

        for e in edges:
            s = name_to_idx.get(e.source)
            t = name_to_idx.get(e.target)
            if s is not None and t is not None:
                out_links[s].append(t)
                in_links[t].append(s)

        # PageRank power iteration
        alpha = 0.85
        pr = [1.0 / n] * n
        for _ in range(100):
            new_pr = [(1 - alpha) / n] * n
            for i in range(n):
                if out_links[i]:
                    contrib = alpha * pr[i] / len(out_links[i])
                    for j in out_links[i]:
                        new_pr[j] += contrib
                else:
                    contrib = alpha * pr[i] / n
                    for j in range(n):
                        new_pr[j] += contrib
            pr = new_pr

        in_degree = {node_names[i]: len(in_links[i]) for i in range(n)}
        out_degree = {node_names[i]: len(out_links[i]) for i in range(n)}
        pagerank = {node_names[i]: pr[i] for i in range(n)}

        # Approximate betweenness via log(fan_in * fan_out)
        betweenness = {}
        for i, name in enumerate(node_names):
            fan = len(in_links[i]) * len(out_links[i])
            betweenness[name] = math.log1p(fan) / math.log1p(n)

        # Closeness: approximate via inverse average BFS distance (depth-2 sample)
        closeness = self._approx_closeness(node_names, out_links, in_links, n)

        # Eigenvector: approximate via power iteration (undirected adjacency)
        eigenvector = self._approx_eigenvector(node_names, out_links, in_links, n)

        max_indeg = max(in_degree.values(), default=1) or 1
        return self._build_scores(
            node_names,
            pagerank,
            betweenness,
            closeness,
            eigenvector,
            in_degree,
            out_degree,
            max_indeg,
        )

    def _approx_closeness(self, node_names, out_links, in_links, n) -> dict[str, float]:
        """Approximate closeness: 1 / (1 + avg_hop_count) via 2-hop BFS."""
        from collections import deque

        closeness = {}
        # Sample up to 100 nodes as sources
        import random

        sample_size = min(100, n)
        sample_indices = random.sample(range(n), sample_size)
        set(sample_indices)

        total_hops = [0.0] * n
        reach_count = [0] * n

        for src in sample_indices:
            visited = {src: 0}
            queue = deque([src])
            while queue:
                cur = queue.popleft()
                d = visited[cur]
                if d >= 3:
                    continue
                for nb in out_links[cur]:
                    if nb not in visited:
                        visited[nb] = d + 1
                        total_hops[nb] += d + 1
                        reach_count[nb] += 1
                        queue.append(nb)

        for i, name in enumerate(node_names):
            if reach_count[i] > 0:
                avg_hop = total_hops[i] / reach_count[i]
                closeness[name] = 1.0 / (1.0 + avg_hop)
            else:
                closeness[name] = 0.0
        return closeness

    def _approx_eigenvector(self, node_names, out_links, in_links, n) -> dict[str, float]:
        """Power iteration eigenvector approximation (undirected adjacency)."""
        ev = [1.0 / max(n, 1)] * n
        for _ in range(20):
            new_ev = [0.0] * n
            for i in range(n):
                total = sum(ev[j] for j in out_links[i])
                total += sum(ev[j] for j in in_links[i])
                new_ev[i] = total
            norm = math.sqrt(sum(x * x for x in new_ev)) or 1.0
            ev = [x / norm for x in new_ev]
        return {node_names[i]: ev[i] for i in range(n)}

    def _build_scores(
        self,
        node_names,
        pagerank,
        betweenness,
        closeness,
        eigenvector,
        in_degree,
        out_degree,
        max_indeg,
    ) -> dict[str, NodeScore]:
        # Normalize eigenvector to [0, 1]
        max_ev = max(eigenvector.values(), default=1.0) or 1.0
        scores = {}
        for name in node_names:
            pr = pagerank.get(name, 0.0)
            bt = betweenness.get(name, 0.0)
            cl = closeness.get(name, 0.0)
            ev = eigenvector.get(name, 0.0) / max_ev
            fi = in_degree.get(name, 0)
            fo = out_degree.get(name, 0)
            fi_norm = fi / max_indeg

            total = fi + fo + 1
            stability = fi / total
            volatility = fo / total

            composite = (
                self.WEIGHT_PAGERANK * pr
                + self.WEIGHT_BETWEENNESS * bt
                + self.WEIGHT_CLOSENESS * cl
                + self.WEIGHT_EIGENVECTOR * ev
                + self.WEIGHT_FAN_IN * fi_norm
            )
            scores[name] = NodeScore(
                qualified_name=name,
                influence_score=pr,
                bridge_score=bt,
                closeness_score=cl,
                eigenvector_score=ev,
                fan_in=fi,
                fan_out=fo,
                stability_score=stability,
                volatility_score=volatility,
                composite_score=composite,
            )
        return scores

    @staticmethod
    def top_nodes(
        scores: dict[str, NodeScore],
        n: int = 20,
        by: str = "composite_score",
    ) -> list[NodeScore]:
        return sorted(scores.values(), key=lambda s: getattr(s, by, 0), reverse=True)[:n]

    @staticmethod
    def hotspots(scores: dict[str, NodeScore], n: int = 10) -> list[NodeScore]:
        """Return likely hotspots: high fan_in and high betweenness."""
        return sorted(
            scores.values(),
            key=lambda s: s.bridge_score + s.fan_in * 0.01,
            reverse=True,
        )[:n]

    @staticmethod
    def most_stable(scores: dict[str, NodeScore], n: int = 10) -> list[NodeScore]:
        """Return the most stable nodes (high fan_in, low fan_out)."""
        return sorted(scores.values(), key=lambda s: s.stability_score, reverse=True)[:n]

    @staticmethod
    def most_volatile(scores: dict[str, NodeScore], n: int = 10) -> list[NodeScore]:
        """Return the most volatile nodes (high fan_out relative to fan_in)."""
        return sorted(scores.values(), key=lambda s: s.volatility_score, reverse=True)[:n]
