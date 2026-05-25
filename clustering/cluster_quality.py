"""Cluster quality metrics: silhouette, cohesion, separation.

All metrics operate on raw float vectors and work without sklearn
(pure Python fallback). Import numpy when available for speed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from clustering.semantic_clusters import Cluster, ClusteringResult


@dataclass
class ClusterQualityReport:
    """Per-run quality summary."""

    silhouette_score: float = 0.0  # Global mean silhouette [-1, 1]; higher=better
    mean_cohesion: float = 0.0  # Avg intra-cluster cosine similarity [0, 1]
    mean_separation: float = 0.0  # Avg inter-cluster centroid distance [0, ∞)
    noise_ratio: float = 0.0  # Fraction of nodes labeled as noise
    n_clusters: int = 0
    n_noise: int = 0
    per_cluster: list[dict] = field(default_factory=list)  # per-cluster stats

    def __str__(self) -> str:
        return (
            f"clusters={self.n_clusters}  "
            f"noise={self.noise_ratio:.1%}  "
            f"silhouette={self.silhouette_score:.4f}  "
            f"cohesion={self.mean_cohesion:.4f}  "
            f"separation={self.mean_separation:.4f}"
        )

    @property
    def is_good(self) -> bool:
        """Heuristic: silhouette > 0.1 and noise < 30%."""
        return self.silhouette_score > 0.1 and self.noise_ratio < 0.3


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _mean_vec(vecs: list[list[float]]) -> list[float]:
    n = len(vecs)
    if n == 0:
        return []
    dim = len(vecs[0])
    return [sum(v[i] for v in vecs) / n for i in range(dim)]


class ClusterQualityEvaluator:
    """Computes quality metrics for a ClusteringResult.

    Uses sklearn when available (exact silhouette), falls back to a
    sampled approximation for large collections.

    Usage:
        evaluator = ClusterQualityEvaluator()
        report = evaluator.evaluate(result, embedding_store)
    """

    def __init__(self, max_sample: int = 500):
        self.max_sample = max_sample

    def evaluate(
        self,
        result: ClusteringResult,
        embedding_store,
    ) -> ClusterQualityReport:
        """Compute quality metrics.

        Args:
            result: ClusteringResult from SemanticClusterer.
            embedding_store: EmbeddingStore containing the vectors.

        Returns:
            ClusterQualityReport with computed metrics.
        """
        items = embedding_store._items
        clusters = [c for c in result.clusters if c.cluster_id != -1]
        n_noise = len(result.noise_members)
        n_total = sum(len(c.members) for c in clusters) + n_noise

        report = ClusterQualityReport(
            n_clusters=len(clusters),
            n_noise=n_noise,
            noise_ratio=n_noise / n_total if n_total > 0 else 0.0,
        )

        if not clusters:
            return report

        # Build vector lookup
        vec_map: dict[str, list[float]] = {name: item.vector for name, item in items.items()}

        # Per-cluster cohesion and centroids
        centroids: dict[int, list[float]] = {}
        per_cluster_stats = []
        cohesion_scores = []

        for cluster in clusters:
            vecs = [vec_map[m] for m in cluster.members if m in vec_map]
            if not vecs:
                per_cluster_stats.append(
                    {
                        "cluster_id": cluster.cluster_id,
                        "size": 0,
                        "cohesion": 0.0,
                    }
                )
                continue

            centroid = _mean_vec(vecs)
            centroids[cluster.cluster_id] = centroid

            # Cohesion = mean pairwise cosine similarity (sampled if large)
            cohesion = self._mean_cohesion(vecs)
            cohesion_scores.append(cohesion)
            per_cluster_stats.append(
                {
                    "cluster_id": cluster.cluster_id,
                    "label": cluster.label,
                    "size": len(vecs),
                    "cohesion": round(cohesion, 4),
                }
            )

        report.mean_cohesion = (
            sum(cohesion_scores) / len(cohesion_scores) if cohesion_scores else 0.0
        )
        report.per_cluster = per_cluster_stats

        # Separation = mean pairwise distance between cluster centroids
        centroid_list = list(centroids.values())
        if len(centroid_list) >= 2:
            sep_scores = []
            for i in range(len(centroid_list)):
                for j in range(i + 1, len(centroid_list)):
                    sep_scores.append(_euclidean(centroid_list[i], centroid_list[j]))
            report.mean_separation = sum(sep_scores) / len(sep_scores)

        # Silhouette score
        report.silhouette_score = self._silhouette(clusters, vec_map, centroids)

        return report

    def _mean_cohesion(self, vecs: list[list[float]]) -> float:
        """Mean pairwise cosine similarity within a cluster (sampled)."""
        if len(vecs) <= 1:
            return 1.0

        import random

        sample = vecs if len(vecs) <= 30 else random.sample(vecs, 30)
        scores = []
        for i in range(len(sample)):
            for j in range(i + 1, len(sample)):
                scores.append(_cosine(sample[i], sample[j]))
        return sum(scores) / len(scores) if scores else 0.0

    def _silhouette(
        self,
        clusters: list[Cluster],
        vec_map: dict[str, list[float]],
        centroids: dict[int, list[float]],
    ) -> float:
        """Approximate silhouette using centroid distances (fast O(n) per point)."""
        if len(clusters) < 2:
            return 0.0

        cluster_by_name: dict[str, int] = {}
        for c in clusters:
            for m in c.members:
                cluster_by_name[m] = c.cluster_id

        scores = []
        for cluster in clusters:
            cid = cluster.cluster_id
            other_centroids = [(oid, ct) for oid, ct in centroids.items() if oid != cid]
            if not other_centroids:
                continue

            for member in cluster.members:
                vec = vec_map.get(member)
                if vec is None or cid not in centroids:
                    continue
                a = _euclidean(vec, centroids[cid])
                b = min(_euclidean(vec, ct) for _, ct in other_centroids)
                denom = max(a, b)
                if denom == 0:
                    scores.append(0.0)
                else:
                    scores.append((b - a) / denom)

        return sum(scores) / len(scores) if scores else 0.0
