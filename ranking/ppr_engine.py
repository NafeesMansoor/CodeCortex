"""Personalized PageRank (PPR) with semantic fusion ranking.

Addresses the key V0.1 flaw: global PageRank on 690 semantic nodes produces
near-uniform scores (std ≈ 0.000765) because every node receives teleportation
mass equally.

PPR fixes this by teleporting back only to the query-relevant seed nodes,
creating a probability distribution concentrated around the query context.
The result: scores spread over 3-4 orders of magnitude instead of near-zero std.

Additionally fuses:
  - PPR score (structural, query-conditioned)
  - Semantic similarity (cosine to query embedding)
  - Betweenness / fan-in (architectural importance)
  - Edge-type weights (calls > inherits > contains > reads/writes)

Usage:
    engine = PPREngine(adj_cache, store)
    scores = engine.rank(seed_nodes, query_embedding=vec)
    # → dict[str, float] sorted by combined relevance
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

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
class PPRScore:
    qualified_name: str
    ppr_score: float = 0.0
    semantic_score: float = 0.0
    structural_score: float = 0.0   # fan-in normalized
    combined_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "qualified_name": self.qualified_name,
            "combined": round(self.combined_score, 6),
            "ppr": round(self.ppr_score, 6),
            "semantic": round(self.semantic_score, 4),
            "structural": round(self.structural_score, 4),
        }


class PPREngine:
    """Query-conditioned ranking via Personalized PageRank.

    Args:
        adj_cache:  AdjacencyCache (in-memory forward/backward edges).
        store:      GraphStore (used to get all node names once).
        alpha:      Damping factor (0.85 default — probability of following an edge).
        max_iter:   Maximum PPR iteration steps.
        tol:        Convergence tolerance (L-inf norm).
    """

    def __init__(self, adj_cache, store, alpha: float = 0.85,
                 max_iter: int = 100, tol: float = 1e-7):
        self.adj = adj_cache
        self.alpha = alpha
        self.max_iter = max_iter
        self.tol = tol

        all_nodes = [n.qualified_name for n in store.all_nodes()]
        self._nodes = all_nodes
        self._node_idx: dict[str, int] = {n: i for i, n in enumerate(all_nodes)}
        self._n = len(all_nodes)

        # Pre-build weighted out-degree and transition matrix info
        self._out_weights: dict[str, float] = {}
        for node in all_nodes:
            total = sum(
                EDGE_WEIGHTS.get(kind, 0.1)
                for _, kind in adj_cache.outgoing(node)
            )
            self._out_weights[node] = total

    def rank(
        self,
        seeds: list[str],
        query_embedding: Optional[list] = None,
        embedding_store=None,
        ppr_weight: float = 0.50,
        semantic_weight: float = 0.30,
        structural_weight: float = 0.20,
        top_k: int = 50,
    ) -> list[PPRScore]:
        """Return top-k nodes by fused PPR + semantic + structural score.

        Args:
            seeds:            Nodes to start personalized random walk from.
            query_embedding:  L2-normalized query vector (optional).
            embedding_store:  EmbeddingStore for per-node vectors (optional).
            top_k:            Return at most this many results.
        """
        if self._n == 0:
            return []

        # --- PPR ---
        ppr = self._compute_ppr(seeds)

        # --- Structural: fan-in normalized ---
        max_fan_in = max(
            (len(list(self.adj.incoming(n))) for n in self._nodes), default=1
        )
        fan_in_scores = {
            n: len(list(self.adj.incoming(n))) / max(max_fan_in, 1)
            for n in self._nodes
        }

        # --- Semantic: cosine to query embedding ---
        sem_scores: dict[str, float] = {}
        has_real_emb = False
        if query_embedding is not None and embedding_store is not None:
            q = np.array(query_embedding, dtype="float32")
            q_norm = float(np.linalg.norm(q))
            if q_norm > 1e-9:
                q = q / q_norm
                for node in self._nodes:
                    item = embedding_store.get(node)
                    if item is not None:
                        v = np.array(item.vector, dtype="float32")
                        v_norm = float(np.linalg.norm(v))
                        if v_norm > 1e-9:
                            sem_scores[node] = max(0.0, float(np.dot(q, v / v_norm)))
                            has_real_emb = True

        if not has_real_emb:
            ppr_weight += semantic_weight / 2
            structural_weight += semantic_weight / 2
            semantic_weight = 0.0

        # --- Fuse ---
        results = []
        for node in self._nodes:
            p = ppr.get(node, 0.0)
            s = sem_scores.get(node, 0.0)
            f = fan_in_scores.get(node, 0.0)
            combined = ppr_weight * p + semantic_weight * s + structural_weight * f
            results.append(PPRScore(
                qualified_name=node,
                ppr_score=p,
                semantic_score=s,
                structural_score=f,
                combined_score=combined,
            ))

        results.sort(key=lambda x: -x.combined_score)
        return results[:top_k]

    def _compute_ppr(self, seeds: list[str]) -> dict[str, float]:
        n = self._n
        if n == 0:
            return {}

        # Personalization vector: uniform over seeds
        p = np.zeros(n, dtype="float64")
        valid_seeds = [s for s in seeds if s in self._node_idx]
        if not valid_seeds:
            # Fall back to uniform (global PageRank)
            p[:] = 1.0 / n
        else:
            for s in valid_seeds:
                p[self._node_idx[s]] = 1.0 / len(valid_seeds)

        pr = p.copy()

        for iteration in range(self.max_iter):
            new_pr = np.zeros(n, dtype="float64")

            for node in self._nodes:
                idx = self._node_idx[node]
                if pr[idx] == 0.0:
                    continue
                outgoing = list(self.adj.outgoing(node))
                total_w = self._out_weights[node]
                if total_w == 0:
                    # Dangling node: distribute to all (standard PR fix)
                    new_pr += self.alpha * pr[idx] / n
                else:
                    for nbr, kind in outgoing:
                        if nbr in self._node_idx:
                            w = EDGE_WEIGHTS.get(kind, 0.1) / total_w
                            new_pr[self._node_idx[nbr]] += self.alpha * pr[idx] * w

            new_pr += (1.0 - self.alpha) * p

            # Normalize to prevent drift
            s = new_pr.sum()
            if s > 0:
                new_pr /= s

            delta = float(np.max(np.abs(new_pr - pr)))
            pr = new_pr
            if delta < self.tol:
                logger.debug("PPR converged in %d iterations (delta=%.2e)", iteration + 1, delta)
                break

        return {self._nodes[i]: float(pr[i]) for i in range(n)}

    # ------------------------------------------------------------------
    # Convenience: global PPR standard statistics
    # ------------------------------------------------------------------

    def discrimination_stats(self, seeds: list[str]) -> dict:
        """Return std-dev and entropy of the PPR distribution as quality metrics."""
        ppr = self._compute_ppr(seeds)
        vals = np.array(list(ppr.values()), dtype="float64")
        std = float(np.std(vals))
        entropy = -float(np.sum(vals * np.log(vals + 1e-15)))
        return {
            "ppr_std": round(std, 8),
            "ppr_entropy": round(entropy, 6),
            "ppr_max": round(float(vals.max()), 8),
            "ppr_min": round(float(vals.min()), 10),
        }
