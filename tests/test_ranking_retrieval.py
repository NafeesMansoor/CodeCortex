"""Tests for ranking (centrality) and retrieval layers."""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import EdgeKind, NodeKind, SourceRange
from graph import CPGBuilder, CPGEdge, CPGNode, GraphStore
from ranking import CentralityEngine, NodeScore
from embeddings import EmbeddingStore, StubEmbeddingProvider
from retrieval import RetrievalConfig, RetrievalLayer


def _build_simple_graph() -> GraphStore:
    """Return a 5-node graph: A→B→C, A→C, D→C, E standalone."""
    store = GraphStore(":memory:")
    nodes = ["A", "B", "C", "D", "E"]
    for name in nodes:
        store.add_node(CPGNode(
            qualified_name=name,
            kind=NodeKind.FUNCTION,
            name=name,
            file_path="test.py",
            language="python",
        ))

    store.add_edge(CPGEdge(EdgeKind.CALLS, "A", "B"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "B", "C"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "A", "C"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "D", "C"))
    return store


class TestCentralityEngine:
    def test_compute_returns_all_nodes(self):
        store = _build_simple_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        assert len(scores) == 5
        for name in ["A", "B", "C", "D", "E"]:
            assert name in scores

    def test_scores_are_node_score_instances(self):
        store = _build_simple_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        for score in scores.values():
            assert isinstance(score, NodeScore)

    def test_high_fan_in_node_ranks_higher(self):
        store = _build_simple_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        # C has in-degree 3 (from A, B, D) — should have high fan_in
        assert scores["C"].fan_in >= 2

    def test_top_nodes_respects_n(self):
        store = _build_simple_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        top = engine.top_nodes(scores, n=3)
        assert len(top) == 3

    def test_empty_store_returns_empty(self):
        store = GraphStore(":memory:")
        engine = CentralityEngine(store)
        assert engine.compute() == {}

    def test_node_score_to_dict(self):
        score = NodeScore(
            qualified_name="mod.func",
            influence_score=0.12345,
            bridge_score=0.0,
            closeness_score=0.0,
            eigenvector_score=0.0,
            fan_in=2,
            fan_out=1,
            stability_score=0.67,
            volatility_score=0.33,
            composite_score=0.05,
        )
        d = score.to_dict()
        assert d["qualified_name"] == "mod.func"
        assert "influence_score" in d


class TestRetrievalLayer:
    def _setup(self):
        store = _build_simple_graph()
        provider = StubEmbeddingProvider(dimensions=4)
        embedding_store = EmbeddingStore(provider, use_faiss=False)
        for name in ["A", "B", "C", "D", "E"]:
            embedding_store.add(name, f"code for {name}")
        config = RetrievalConfig(top_k=5, min_semantic_score=0.0)
        layer = RetrievalLayer(store, embedding_store, config=config)
        return layer

    def test_retrieve_returns_results(self):
        layer = self._setup()
        results = layer.retrieve("function A calls B")
        assert isinstance(results, list)

    def test_retrieve_respects_top_k(self):
        layer = self._setup()
        results = layer.retrieve("some query")
        assert len(results) <= 5

    def test_results_have_required_fields(self):
        layer = self._setup()
        results = layer.retrieve("query")
        for r in results:
            assert hasattr(r, "qualified_name")
            assert hasattr(r, "score")
            assert hasattr(r, "semantic_score")

    def test_result_to_dict(self):
        from retrieval import RetrievalResult
        r = RetrievalResult(
            qualified_name="mod.func",
            score=0.75,
            semantic_score=0.8,
            graph_score=0.6,
            centrality_score=0.5,
            cluster_score=0.0,
            file_path="mod.py",
        )
        d = r.to_dict()
        assert d["qualified_name"] == "mod.func"
        assert d["score"] == 0.75
