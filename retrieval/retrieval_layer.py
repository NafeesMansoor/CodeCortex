"""Hybrid GraphRAG retrieval layer.

Combines four signals to score nodes for AI context generation:
  1. Semantic similarity  (embedding cosine distance)
  2. Graph distance       (BFS hops from seed nodes)
  3. Centrality weight    (composite_score from CentralityEngine)
  4. Cluster relevance    (same cluster as query result)

Final score:
    score = α·semantic + β·graph_proximity + γ·centrality + δ·cluster
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from core.types import TraversalConfig
from graph.graph_store import GraphStore
from traversal.traversal_engine import AdjacencyCache, TraversalEngine
from traversal.query_optimizer import OptimizationHints, QueryOptimizer
from embeddings.embedding_store import EmbeddingStore
from ranking.centrality_engine import CentralityEngine, NodeScore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalConfig:
    weight_semantic: float = 0.40
    weight_graph: float = 0.25
    weight_centrality: float = 0.20
    weight_cluster: float = 0.15

    max_hops: int = 3
    max_graph_nodes: int = 200
    token_budget: Optional[int] = 4000

    top_k: int = 20
    min_semantic_score: float = 0.20


@dataclass
class RetrievalResult:
    qualified_name: str
    score: float
    semantic_score: float = 0.0
    graph_score: float = 0.0
    centrality_score: float = 0.0
    cluster_score: float = 0.0
    file_path: str = ""
    components: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "qualified_name": self.qualified_name,
            "score": round(self.score, 4),
            "semantic": round(self.semantic_score, 4),
            "graph": round(self.graph_score, 4),
            "centrality": round(self.centrality_score, 4),
            "cluster": round(self.cluster_score, 4),
            "file_path": self.file_path,
        }

    def explain(self) -> str:
        lines = [
            f"{self.qualified_name}  (score={self.score:.4f})",
            f"  semantic={self.semantic_score:.4f}  graph={self.graph_score:.4f}  "
            f"centrality={self.centrality_score:.4f}  cluster={self.cluster_score:.4f}",
        ]
        if self.file_path:
            lines.append(f"  file: {self.file_path}")
        return "\n".join(lines)


@dataclass
class ImpactSummary:
    """Structured impact analysis for a changed node."""

    root: str
    direct_callers: list[str]
    transitive_affected: dict[str, int]    # qname → depth
    affected_files: list[str]
    affected_tests: list[str]
    max_depth: int

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "direct_callers": self.direct_callers,
            "transitive_count": len(self.transitive_affected),
            "affected_files": self.affected_files,
            "affected_tests": self.affected_tests,
            "max_depth": self.max_depth,
        }


class RetrievalLayer:
    """Hybrid GraphRAG retrieval combining graph + semantic + ranking signals.

    Usage:
        layer = RetrievalLayer(store, embedding_store, config=RetrievalConfig())
        results = layer.retrieve("parse request body in controller")
        context = layer.generate_context("parse request body in controller")
    """

    def __init__(
        self,
        store: GraphStore,
        embedding_store: EmbeddingStore,
        config: Optional[RetrievalConfig] = None,
        cluster_memberships: Optional[dict[str, int]] = None,
        adjacency_cache: Optional[AdjacencyCache] = None,
    ):
        self.store = store
        self.embedding_store = embedding_store
        self.config = config or RetrievalConfig()
        self.cluster_memberships = cluster_memberships or {}

        self._traversal = TraversalEngine(
            store,
            enable_memoization=True,
            adjacency_cache=adjacency_cache,
        )
        self._optimizer = QueryOptimizer(store)
        self._centrality_cache: Optional[dict[str, NodeScore]] = None

    # ------------------------------------------------------------------
    # Core retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        seed_nodes: Optional[list[str]] = None,
        intent: Optional[str] = None,
    ) -> list[RetrievalResult]:
        cfg = self.config

        semantic_hits = self.embedding_store.search(
            query,
            top_k=cfg.top_k * 2,
            threshold=cfg.min_semantic_score,
        )
        semantic_scores: dict[str, float] = {h.qualified_name: h.score for h in semantic_hits}

        seeds = seed_nodes or [h.qualified_name for h in semantic_hits[:5]]
        graph_depths = self._graph_expand(seeds, intent)

        centrality = self._get_centrality()
        query_clusters = self._query_clusters(semantic_hits)

        candidates = set(semantic_scores) | set(graph_depths)
        max_graph_depth = max(graph_depths.values(), default=1) or 1
        results: list[RetrievalResult] = []

        for qname in candidates:
            sem = semantic_scores.get(qname, 0.0)
            depth = graph_depths.get(qname)
            graph = _depth_to_score(depth, max_graph_depth) if depth is not None else 0.0
            cent_s = centrality.get(qname)
            cent = cent_s.composite_score if cent_s else 0.0
            clust = 1.0 if self.cluster_memberships.get(qname) in query_clusters else 0.0

            score = (
                cfg.weight_semantic * sem
                + cfg.weight_graph * graph
                + cfg.weight_centrality * cent
                + cfg.weight_cluster * clust
            )

            node = self.store.get_node(qname)
            results.append(RetrievalResult(
                qualified_name=qname,
                score=score,
                semantic_score=sem,
                graph_score=graph,
                centrality_score=cent,
                cluster_score=clust,
                file_path=node.file_path if node else "",
            ))

        results.sort(key=lambda r: r.score, reverse=True)

        if cfg.token_budget:
            results = self._apply_token_budget(results, cfg.token_budget)

        return results[: cfg.top_k]

    # ------------------------------------------------------------------
    # AI context generation
    # ------------------------------------------------------------------

    def generate_context(
        self,
        query: str,
        seed_nodes: Optional[list[str]] = None,
        intent: Optional[str] = None,
        max_tokens: int = 3000,
    ) -> str:
        """Return a ranked, token-budgeted context string for AI consumption.

        Each entry includes qualified name, file, and score breakdown.
        Truncated at max_tokens (estimated at 4 chars/token).
        """
        results = self.retrieve(query, seed_nodes=seed_nodes, intent=intent)
        char_budget = max_tokens * 4
        lines = [f"# Context for: {query!r}\n"]

        for i, r in enumerate(results, 1):
            node = self.store.get_node(r.qualified_name)
            entry_lines = [
                f"\n## {i}. {r.qualified_name}  [{r.score:.3f}]",
                f"   file: {r.file_path}" if r.file_path else "",
                f"   kind: {node.kind.value}" if node else "",
                f"   scores: sem={r.semantic_score:.3f}  graph={r.graph_score:.3f}"
                f"  centrality={r.centrality_score:.4f}",
            ]
            block = "\n".join(l for l in entry_lines if l)
            if len("\n".join(lines)) + len(block) > char_budget:
                lines.append(f"\n... {len(results) - i + 1} results omitted (token budget)")
                break
            lines.append(block)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Impact analysis
    # ------------------------------------------------------------------

    def impact_summary(
        self,
        qualified_name: str,
        max_depth: int = 4,
    ) -> ImpactSummary:
        """Compute structured impact analysis for a changed node."""
        from core.types import EdgeKind

        # Direct callers (inbound CALLS/IMPORTS)
        in_edges = self.store.get_incoming_edges(qualified_name)
        direct_callers = list({
            e.source for e in in_edges
            if e.kind in (EdgeKind.CALLS, EdgeKind.IMPORTS_FROM, EdgeKind.DEPENDS_ON)
        })

        # Transitive affected (inbound BFS — who calls this, transitively)
        from core.types import TraversalConfig
        cfg = TraversalConfig(
            max_depth=max_depth,
            max_nodes=500,
            direction="inbound",
            edge_filter=[EdgeKind.CALLS, EdgeKind.IMPORTS_FROM, EdgeKind.DEPENDS_ON],
        )
        trav_results = self._traversal.bfs([qualified_name], cfg)
        transitive: dict[str, int] = {
            r.qualified_name: r.depth for r in trav_results
            if r.qualified_name != qualified_name
        }

        # Affected files
        files: set[str] = set()
        for qname in transitive:
            node = self.store.get_node(qname)
            if node and node.file_path:
                files.add(node.file_path)

        # Affected tests
        from core.types import NodeKind
        tests = [
            qname for qname in transitive
            if (n := self.store.get_node(qname)) and n.kind == NodeKind.TEST
        ]

        return ImpactSummary(
            root=qualified_name,
            direct_callers=direct_callers,
            transitive_affected=transitive,
            affected_files=sorted(files),
            affected_tests=tests,
            max_depth=max_depth,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _graph_expand(self, seeds: list[str], intent: Optional[str]) -> dict[str, int]:
        tc = TraversalConfig(
            max_depth=self.config.max_hops,
            max_nodes=self.config.max_graph_nodes,
        )
        hints = OptimizationHints(
            token_budget=self.config.token_budget,
            query_intent=intent,
        )
        tc = self._optimizer.optimize(tc, hints)
        nodes = self._traversal.bfs(seeds, tc)
        return {n.qualified_name: n.depth for n in nodes}

    def _get_centrality(self) -> dict[str, NodeScore]:
        if self._centrality_cache is None:
            self._centrality_cache = CentralityEngine(self.store).compute()
        return self._centrality_cache

    def _query_clusters(self, semantic_hits) -> set[int]:
        clusters = set()
        for hit in semantic_hits[:5]:
            cid = self.cluster_memberships.get(hit.qualified_name)
            if cid is not None:
                clusters.add(cid)
        return clusters

    def _apply_token_budget(self, results: list[RetrievalResult], budget: int) -> list[RetrievalResult]:
        kept: list[RetrievalResult] = []
        tokens_used = 0
        for r in results:
            if tokens_used + 150 > budget:
                break
            kept.append(r)
            tokens_used += 150
        return kept

    def invalidate_centrality_cache(self) -> None:
        self._centrality_cache = None
        self._traversal.clear_memoization()

    def set_centrality_cache(self, scores: dict[str, NodeScore]) -> None:
        """Inject pre-computed centrality scores to avoid recomputation."""
        self._centrality_cache = scores


def _depth_to_score(depth: int, max_depth: int) -> float:
    if depth == 0:
        return 1.0
    return 1.0 / (1.0 + math.log1p(depth / max(max_depth, 1)))
