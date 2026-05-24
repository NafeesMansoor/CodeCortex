"""Phase 5 tests: cluster labeling, quality metrics, store persistence."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustering.semantic_clusters import (
    Cluster, ClusterConfig, ClusteringResult, SemanticClusterer,
)
from clustering.cluster_labeler import ClusterLabeler, _tokenize, _module_prefix
from clustering.cluster_quality import ClusterQualityEvaluator, ClusterQualityReport
from clustering.cluster_store import ClusterStore
from embeddings.embedding_store import EmbeddingStore
from embeddings.provider_factory import StubEmbeddingProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(n: int = 20, dim: int = 16) -> EmbeddingStore:
    """Build a stub EmbeddingStore with n nodes."""
    provider = StubEmbeddingProvider(dimensions=dim)
    store = EmbeddingStore(provider)
    names = [f"module_{i % 4}.func_{i}" for i in range(n)]
    texts = [f"function number {i}" for i in range(n)]
    store.add_batch(names, texts)
    return store


def _make_result(n_clusters: int = 3, size: int = 5) -> ClusteringResult:
    """Build a synthetic ClusteringResult."""
    clusters = [
        Cluster(
            cluster_id=i,
            members=[f"mod_{i}.fn_{j}" for j in range(size)],
            label="",
        )
        for i in range(n_clusters)
    ]
    return ClusteringResult(clusters=clusters, noise_members=["noise.orphan"], algorithm="stub")


# ---------------------------------------------------------------------------
# ClusterLabeler
# ---------------------------------------------------------------------------

class TestClusterLabeler:
    def test_tokenize_snake_case(self):
        tokens = _tokenize("parse_function_body")
        assert "parse" in tokens
        assert "function" in tokens
        assert "body" in tokens

    def test_tokenize_camel_case(self):
        tokens = _tokenize("getUserById")
        assert "user" in tokens
        assert "id" in tokens

    def test_tokenize_filters_stop_words(self):
        tokens = _tokenize("get_result")
        assert "result" not in tokens

    def test_module_prefix_extracts_first_segment(self):
        assert _module_prefix("graph.store.get_node") == "graph"
        assert _module_prefix("standalone") is None

    def test_label_uses_top_tokens(self):
        labeler = ClusterLabeler(max_tokens=2)
        cluster = Cluster(
            cluster_id=0,
            members=["graph.parse_node", "graph.parse_edge", "graph.parse_file"],
            label="",
        )
        label = labeler.label(cluster)
        assert label != ""
        assert "parse" in label or "graph" in label

    def test_label_all_updates_in_place(self):
        labeler = ClusterLabeler()
        result = _make_result(n_clusters=3, size=4)
        labeler.label_all(result.clusters)
        for cluster in result.clusters:
            assert cluster.label != ""

    def test_empty_cluster_gets_fallback_label(self):
        labeler = ClusterLabeler()
        cluster = Cluster(cluster_id=7, members=[], label="")
        label = labeler.label(cluster)
        assert label == "cluster_7"

    def test_dominant_module_prepended(self):
        labeler = ClusterLabeler()
        cluster = Cluster(
            cluster_id=0,
            members=["embeddings.embed_text", "embeddings.embed_batch", "embeddings.embed_one"],
            label="",
        )
        label = labeler.label(cluster)
        assert "embeddings" in label


# ---------------------------------------------------------------------------
# ClusterQualityEvaluator
# ---------------------------------------------------------------------------

class TestClusterQuality:
    def test_evaluate_returns_report(self):
        store = _make_store(n=30)
        result = _make_result(n_clusters=3, size=5)
        evaluator = ClusterQualityEvaluator()
        report = evaluator.evaluate(result, store)
        assert isinstance(report, ClusterQualityReport)

    def test_noise_ratio_computed(self):
        store = _make_store(n=20)
        result = ClusteringResult(
            clusters=[Cluster(cluster_id=0, members=["mod.a", "mod.b", "mod.c"])],
            noise_members=["mod.d", "mod.e"],
            algorithm="stub",
        )
        # Add these specific nodes to the store
        store.add_batch(["mod.a", "mod.b", "mod.c", "mod.d", "mod.e"],
                        ["a", "b", "c", "d", "e"])
        evaluator = ClusterQualityEvaluator()
        report = evaluator.evaluate(result, store)
        # 2 noise out of 5 total = 0.4
        assert abs(report.noise_ratio - 0.4) < 0.01

    def test_single_cluster_silhouette_zero(self):
        store = _make_store(n=10)
        result = ClusteringResult(
            clusters=[Cluster(cluster_id=0, members=[f"module_0.func_{i}" for i in range(10)])],
            noise_members=[],
            algorithm="stub",
        )
        evaluator = ClusterQualityEvaluator()
        report = evaluator.evaluate(result, store)
        assert report.silhouette_score == 0.0

    def test_quality_report_str(self):
        report = ClusterQualityReport(
            silhouette_score=0.35, mean_cohesion=0.6,
            mean_separation=1.2, noise_ratio=0.1,
            n_clusters=4, n_noise=3,
        )
        s = str(report)
        assert "clusters=4" in s
        assert "silhouette=0.3500" in s

    def test_is_good_heuristic(self):
        good = ClusterQualityReport(silhouette_score=0.25, noise_ratio=0.1)
        bad = ClusterQualityReport(silhouette_score=-0.1, noise_ratio=0.5)
        assert good.is_good
        assert not bad.is_good


# ---------------------------------------------------------------------------
# ClusterStore
# ---------------------------------------------------------------------------

class TestClusterStore:
    def test_save_and_load_roundtrip(self):
        result = _make_result(n_clusters=3, size=4)
        store = ClusterStore(":memory:")
        store.save(result)
        reloaded = store.load()
        assert reloaded is not None
        assert len(reloaded.clusters) == 3
        assert reloaded.algorithm == "stub"

    def test_noise_members_preserved(self):
        result = _make_result(n_clusters=2, size=3)
        result.noise_members = ["noise.a", "noise.b"]
        store = ClusterStore(":memory:")
        store.save(result)
        reloaded = store.load()
        assert "noise.a" in reloaded.noise_members

    def test_cluster_of_lookup(self):
        result = _make_result(n_clusters=2, size=3)
        store = ClusterStore(":memory:")
        store.save(result)
        member = result.clusters[0].members[0]
        cid = store.cluster_of(member)
        assert cid == result.clusters[0].cluster_id

    def test_cluster_of_unknown_returns_none(self):
        store = ClusterStore(":memory:")
        assert store.cluster_of("nonexistent.symbol") is None

    def test_update_label(self):
        result = _make_result(n_clusters=1, size=2)
        store = ClusterStore(":memory:")
        store.save(result)
        store.update_label(0, "authentication flow")
        reloaded = store.load()
        assert reloaded.clusters[0].label == "authentication flow"

    def test_size_and_n_clusters(self):
        result = _make_result(n_clusters=3, size=5)
        store = ClusterStore(":memory:")
        store.save(result)
        assert store.n_clusters() == 3
        # 3 clusters × 5 members + 1 noise member from _make_result
        assert store.size() == 16

    def test_save_overwrites_previous(self):
        store = ClusterStore(":memory:")
        store.save(_make_result(n_clusters=5, size=3))
        store.save(_make_result(n_clusters=2, size=4))
        assert store.n_clusters() == 2


# ---------------------------------------------------------------------------
# SemanticClusterer integration (cluster_and_label + evaluate)
# ---------------------------------------------------------------------------

class TestSemanticClustererPhase5:
    def test_cluster_and_label_produces_labels(self):
        store = _make_store(n=15, dim=8)
        clusterer = SemanticClusterer(ClusterConfig(min_cluster_size=3))
        result = clusterer.cluster_and_label(store)
        for cluster in result.clusters:
            assert cluster.label != ""

    def test_evaluate_returns_quality_report(self):
        store = _make_store(n=20, dim=8)
        clusterer = SemanticClusterer(ClusterConfig(min_cluster_size=3))
        result = clusterer.cluster(store)
        report = clusterer.evaluate(result, store)
        assert isinstance(report, ClusterQualityReport)
        assert 0 <= report.noise_ratio <= 1.0

    def test_full_pipeline_stub_store_to_cluster_store(self):
        emb_store = _make_store(n=20, dim=8)
        clusterer = SemanticClusterer(ClusterConfig(min_cluster_size=3))
        result = clusterer.cluster_and_label(emb_store)
        report = clusterer.evaluate(result, emb_store)

        cluster_store = ClusterStore(":memory:")
        cluster_store.save(result)
        assert cluster_store.size() > 0
        assert str(report)  # smoke test
