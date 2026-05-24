"""HDBSCAN-based semantic clustering of code nodes.

Groups semantically similar code regions using:
  - embedding vectors (from EmbeddingStore)
  - UMAP for dimensionality reduction
  - HDBSCAN for density-based clustering

Falls back to K-Means when HDBSCAN/UMAP are not installed.
Integrates with ClusterLabeler and ClusterQualityEvaluator (Phase 5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ClusterConfig:
    """Configuration for the semantic clustering pipeline."""

    min_cluster_size: int = 5       # HDBSCAN: min members to form a cluster
    min_samples: int = 3            # HDBSCAN: core point density
    umap_n_components: int = 10     # Reduce to this many dims before clustering
    umap_n_neighbors: int = 15
    metric: str = "cosine"
    allow_noise: bool = True        # If False, assign noise points to nearest cluster


@dataclass
class Cluster:
    """A semantic cluster of code nodes."""

    cluster_id: int                        # -1 = noise/outlier
    members: list[str] = field(default_factory=list)  # Qualified names
    label: str = ""                        # Auto-generated or human label
    centroid: Optional[list[float]] = None


@dataclass
class ClusteringResult:
    """Output of a clustering run."""

    clusters: list[Cluster]
    noise_members: list[str]              # Nodes in cluster_id == -1
    algorithm: str = "hdbscan"

    def by_id(self, cluster_id: int) -> Optional[Cluster]:
        for c in self.clusters:
            if c.cluster_id == cluster_id:
                return c
        return None

    def cluster_of(self, qualified_name: str) -> Optional[Cluster]:
        for c in self.clusters:
            if qualified_name in c.members:
                return c
        return None


class SemanticClusterer:
    """Clusters code nodes by semantic similarity of their embeddings.

    Usage:
        store = EmbeddingStore(provider)
        # ... add embeddings ...
        clusterer = SemanticClusterer(config=ClusterConfig())
        result = clusterer.cluster(store)
        result = clusterer.cluster_and_label(store)  # also auto-labels
    """

    def __init__(self, config: Optional[ClusterConfig] = None):
        self.config = config or ClusterConfig()

    def cluster(self, embedding_store) -> ClusteringResult:
        """Run the clustering pipeline on an EmbeddingStore.

        Args:
            embedding_store: EmbeddingStore with embedded nodes.

        Returns:
            ClusteringResult with cluster assignments.
        """
        items = list(embedding_store._items.values())
        if len(items) < self.config.min_cluster_size:
            logger.info(
                "Too few items (%d) for clustering (min_cluster_size=%d) — "
                "returning single cluster",
                len(items),
                self.config.min_cluster_size,
            )
            return ClusteringResult(
                clusters=[Cluster(cluster_id=0, members=[i.qualified_name for i in items])],
                noise_members=[],
                algorithm="trivial",
            )

        names = [item.qualified_name for item in items]
        vectors = [item.vector for item in items]

        labels, algorithm = self._fit(vectors)
        return self._build_result(names, vectors, labels, algorithm)

    def cluster_and_label(self, embedding_store) -> "ClusteringResult":
        """Cluster and auto-label all clusters in one call."""
        from clustering.cluster_labeler import ClusterLabeler
        result = self.cluster(embedding_store)
        ClusterLabeler().label_all(result.clusters)
        return result

    def evaluate(self, result: "ClusteringResult", embedding_store) -> "ClusterQualityReport":
        """Compute quality metrics for a ClusteringResult."""
        from clustering.cluster_quality import ClusterQualityEvaluator
        return ClusterQualityEvaluator().evaluate(result, embedding_store)

    def _fit(self, vectors: list[list[float]]) -> tuple[list[int], str]:
        """Run UMAP → HDBSCAN (or fall back to KMeans)."""
        try:
            return self._fit_hdbscan(vectors)
        except ImportError:
            logger.warning("hdbscan/umap not available, falling back to KMeans")
            return self._fit_kmeans(vectors)

    def _fit_hdbscan(self, vectors: list[list[float]]) -> tuple[list[int], str]:
        import numpy as np
        import umap
        import hdbscan

        X = np.array(vectors, dtype="float32")

        reducer = umap.UMAP(
            n_components=min(self.config.umap_n_components, X.shape[1]),
            n_neighbors=min(self.config.umap_n_neighbors, len(vectors) - 1),
            metric=self.config.metric,
            random_state=42,
        )
        X_reduced = reducer.fit_transform(X)

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.config.min_cluster_size,
            min_samples=self.config.min_samples,
            metric="euclidean",
        )
        labels = clusterer.fit_predict(X_reduced).tolist()
        return labels, "hdbscan"

    def _fit_kmeans(self, vectors: list[list[float]]) -> tuple[list[int], str]:
        import numpy as np
        from sklearn.cluster import KMeans

        X = np.array(vectors, dtype="float32")
        n_clusters = max(2, len(vectors) // self.config.min_cluster_size)
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init="auto")
        labels = km.fit_predict(X).tolist()
        return labels, "kmeans"

    def _build_result(
        self,
        names: list[str],
        vectors: list[list[float]],
        labels: list[int],
        algorithm: str,
    ) -> ClusteringResult:
        import numpy as np

        cluster_map: dict[int, list[str]] = {}
        noise: list[str] = []

        for name, label in zip(names, labels):
            if label == -1:
                noise.append(name)
            else:
                cluster_map.setdefault(label, []).append(name)

        clusters = []
        for cid, members in cluster_map.items():
            member_vecs = [vectors[names.index(m)] for m in members]
            centroid = (
                np.mean(member_vecs, axis=0).tolist() if member_vecs else None
            )
            clusters.append(Cluster(
                cluster_id=cid,
                members=members,
                label=f"cluster_{cid}",
                centroid=centroid,
            ))

        clusters.sort(key=lambda c: c.cluster_id)

        if not self.config.allow_noise and noise:
            # Assign noise to nearest cluster by centroid distance
            for name in noise:
                vec = vectors[names.index(name)]
                best = min(clusters, key=lambda c: _centroid_dist(vec, c.centroid))
                best.members.append(name)
            noise = []

        logger.info(
            "Clustering complete: %d clusters, %d noise points (%s)",
            len(clusters),
            len(noise),
            algorithm,
        )
        return ClusteringResult(clusters=clusters, noise_members=noise, algorithm=algorithm)


def _centroid_dist(vec: list[float], centroid: Optional[list[float]]) -> float:
    if centroid is None:
        return float("inf")
    return sum((a - b) ** 2 for a, b in zip(vec, centroid)) ** 0.5
