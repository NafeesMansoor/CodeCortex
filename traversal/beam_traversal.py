"""Semantic beam search traversal.

Replaces plain BFS with a priority-queue traversal that scores each node by a
combination of:
  - Structural centrality  (composite_score from CentralityEngine)
  - Semantic similarity    (cosine distance to query embedding, if available)
  - Edge-type weight       (calls > inherits > contains > reads/writes)
  - Depth decay            (exponential: score *= decay^depth)

The beam keeps only the top-B candidates at each expansion step, preventing
graph explosion while directing exploration toward high-signal nodes.

Also provides:
  - LRU traversal result cache (keyed by (seeds, depth, beam_width))
  - Adaptive depth: stops expansion when max marginal score falls below threshold
"""

from __future__ import annotations

import heapq
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core.types import EdgeKind

logger = logging.getLogger(__name__)

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
class BeamNode:
    qualified_name: str
    score: float
    depth: int
    path: list[str] = field(default_factory=list)

    def __lt__(self, other: "BeamNode") -> bool:
        return self.score > other.score  # highest score first


@dataclass
class BeamResult:
    nodes: list[BeamNode] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def ranked_names(self) -> list[str]:
        return [n.qualified_name for n in self.nodes]


class BeamTraversal:
    """Priority-queue beam search over a CPG adjacency cache.

    Args:
        adj_cache:         AdjacencyCache for fast edge lookup.
        centrality_scores: Optional dict[str, NodeScore] from CentralityEngine.
        embedding_store:   Optional EmbeddingStore for semantic scoring.
        beam_width:        Maximum nodes to keep in the active frontier.
        depth_decay:       Score multiplier per hop (0.8 = 20% decay/hop).
        min_score:         Stop expanding nodes below this threshold.
    """

    def __init__(
        self,
        adj_cache,
        centrality_scores: Optional[dict] = None,
        embedding_store=None,
        beam_width: int = 30,
        depth_decay: float = 0.8,
        min_score: float = 0.001,
    ):
        self.adj = adj_cache
        self.centrality = centrality_scores or {}
        self.emb = embedding_store
        self.beam_width = beam_width
        self.depth_decay = depth_decay
        self.min_score = min_score
        self._cache: dict[tuple, BeamResult] = {}

    def traverse(
        self,
        seeds: list[str],
        query: Optional[str] = None,
        max_depth: int = 4,
        semantic_weight: float = 0.40,
        structural_weight: float = 0.60,
    ) -> BeamResult:
        """Run beam search from seeds. Returns nodes sorted by relevance."""
        t0 = time.perf_counter()

        cache_key = (tuple(sorted(seeds)), query, max_depth, self.beam_width)
        if cache_key in self._cache:
            return self._cache[cache_key]

        query_vec = self._get_query_vec(query) if query and self.emb else None
        # If no real embeddings, fall back to structural-only
        if query_vec is not None and self._is_zero_vec(query_vec):
            query_vec = None
            structural_weight, semantic_weight = 1.0, 0.0

        heap: list[tuple] = []
        visited: dict[str, float] = {}

        for seed in seeds:
            s = self._score_node(seed, query_vec, semantic_weight, structural_weight)
            heapq.heappush(heap, (-s, seed, 0, [seed]))

        results: list[BeamNode] = []

        while heap:
            neg_score, node, depth, path = heapq.heappop(heap)
            score = -neg_score

            if node in visited:
                continue
            if score < self.min_score:
                break

            visited[node] = score
            results.append(BeamNode(qualified_name=node, score=score, depth=depth, path=list(path)))

            if depth >= max_depth:
                continue

            # Expand outgoing edges, decayed by depth
            candidates: list[tuple] = []
            for neighbor, kind in self.adj.outgoing(node):
                if neighbor in visited:
                    continue
                edge_w = EDGE_WEIGHTS.get(kind, 0.1)
                nbr_score = self._score_node(
                    neighbor, query_vec, semantic_weight, structural_weight
                )
                combined = (score * 0.5 + nbr_score * 0.5) * edge_w * (self.depth_decay**depth)
                candidates.append((-combined, neighbor, depth + 1, path + [neighbor]))

            # Beam pruning: keep only top beam_width candidates per expansion
            candidates.sort()
            for c in candidates[: self.beam_width]:
                heapq.heappush(heap, c)

        results.sort(key=lambda x: -x.score)
        elapsed = (time.perf_counter() - t0) * 1000

        result = BeamResult(
            nodes=results[: self.beam_width * max_depth], elapsed_ms=round(elapsed, 2)
        )
        self._cache[cache_key] = result
        return result

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------

    def _score_node(
        self,
        node: str,
        query_vec: Optional[list],
        sem_w: float,
        struct_w: float,
    ) -> float:
        structural = 0.0
        if node in self.centrality:
            ns = self.centrality[node]
            structural = getattr(ns, "composite_score", 0.0)

        if query_vec is None or self.emb is None:
            return structural

        item = self.emb.get(node)
        if item is None:
            return struct_w * structural

        import numpy as np

        vec = np.array(item.vector, dtype="float32")
        qv = np.array(query_vec, dtype="float32")
        norm_v = float(np.linalg.norm(vec))
        norm_q = float(np.linalg.norm(qv))
        if norm_v < 1e-9 or norm_q < 1e-9:
            return struct_w * structural
        sem = float(np.dot(qv, vec) / (norm_q * norm_v))
        sem = max(0.0, sem)
        return sem_w * sem + struct_w * structural

    def _get_query_vec(self, query: str) -> Optional[list]:
        if self.emb is None:
            return None
        try:
            vec = self.emb.provider.embed_one(query)
            return vec
        except Exception:
            return None

    @staticmethod
    def _is_zero_vec(vec) -> bool:
        return all(abs(v) < 1e-9 for v in vec[:8])
