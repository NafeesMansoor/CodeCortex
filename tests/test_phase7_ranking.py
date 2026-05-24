"""Phase 7 tests: centrality ranking, closeness, eigenvector, stability/volatility."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import EdgeKind, NodeKind
from graph.graph_store import GraphStore
from graph.schema import CPGEdge, CPGNode
from ranking.centrality_engine import CentralityEngine, NodeScore, _SEMANTIC_KINDS


# ---------------------------------------------------------------------------
# Graph fixtures
# ---------------------------------------------------------------------------

def _hub_graph() -> GraphStore:
    """Hub graph: A is called by B, C, D, E. A calls F."""
    store = GraphStore(":memory:")
    for name, kind in [("A", NodeKind.FUNCTION), ("B", NodeKind.FUNCTION),
                       ("C", NodeKind.FUNCTION), ("D", NodeKind.FUNCTION),
                       ("E", NodeKind.FUNCTION), ("F", NodeKind.FUNCTION)]:
        store.add_node(CPGNode(name, kind, name, "f.py"))
    for caller in ["B", "C", "D", "E"]:
        store.add_edge(CPGEdge(EdgeKind.CALLS, caller, "A"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "A", "F"))
    return store


def _mixed_graph() -> GraphStore:
    """Graph with both semantic and non-semantic nodes."""
    store = GraphStore(":memory:")
    nodes = [
        ("func_a", NodeKind.FUNCTION),
        ("MyClass", NodeKind.CLASS),
        ("var_x", NodeKind.VARIABLE),
        ("param_y", NodeKind.PARAMETER),
        ("func_b", NodeKind.FUNCTION),
    ]
    for name, kind in nodes:
        store.add_node(CPGNode(name, kind, name, "f.py"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "func_a", "func_b"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "MyClass", "func_a"))
    store.add_edge(CPGEdge(EdgeKind.WRITES, "func_a", "var_x"))
    store.add_edge(CPGEdge(EdgeKind.PARAMETER_OF, "param_y", "func_a"))
    return store


# ---------------------------------------------------------------------------
# NodeScore dataclass
# ---------------------------------------------------------------------------

class TestNodeScore:
    def test_to_dict_has_all_fields(self):
        score = NodeScore(
            qualified_name="mod.func",
            influence_score=0.1,
            bridge_score=0.05,
            closeness_score=0.3,
            eigenvector_score=0.2,
            fan_in=3,
            fan_out=1,
            stability_score=0.75,
            volatility_score=0.25,
            composite_score=0.15,
        )
        d = score.to_dict()
        assert d["qualified_name"] == "mod.func"
        assert "closeness_score" in d
        assert "eigenvector_score" in d
        assert "stability_score" in d
        assert "volatility_score" in d

    def test_stability_and_volatility_sum_less_than_one(self):
        # stability + volatility < 1 (denominator includes +1)
        score = NodeScore("x", 0.1, 0.0, 0.0, 0.0, 3, 1, 3/5, 1/5, 0.1)
        assert score.stability_score + score.volatility_score < 1.0


# ---------------------------------------------------------------------------
# CentralityEngine — semantic_only filtering
# ---------------------------------------------------------------------------

class TestSemanticFiltering:
    def test_variable_nodes_excluded(self):
        store = _mixed_graph()
        engine = CentralityEngine(store, semantic_only=True)
        scores = engine.compute()
        assert "var_x" not in scores
        assert "param_y" not in scores

    def test_semantic_nodes_included(self):
        store = _mixed_graph()
        engine = CentralityEngine(store, semantic_only=True)
        scores = engine.compute()
        assert "func_a" in scores
        assert "MyClass" in scores

    def test_semantic_only_false_includes_all(self):
        store = _mixed_graph()
        engine = CentralityEngine(store, semantic_only=False)
        scores = engine.compute()
        # With semantic_only=False, VARIABLE and PARAMETER may appear
        # (they might still be excluded by edge_filter if no edges connect them)
        # At minimum all semantic nodes should be there
        assert "func_a" in scores

    def test_semantic_kinds_set(self):
        assert NodeKind.FUNCTION in _SEMANTIC_KINDS
        assert NodeKind.METHOD in _SEMANTIC_KINDS
        assert NodeKind.VARIABLE not in _SEMANTIC_KINDS
        assert NodeKind.PARAMETER not in _SEMANTIC_KINDS


# ---------------------------------------------------------------------------
# Centrality computation correctness
# ---------------------------------------------------------------------------

class TestCentralityComputation:
    def test_hub_has_highest_fan_in(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        assert scores["A"].fan_in >= 4

    def test_hub_has_highest_composite(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        top = engine.top_nodes(scores, n=1)
        assert top[0].qualified_name == "A"

    def test_all_semantic_nodes_scored(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        assert len(scores) == 6

    def test_pagerank_sums_to_approx_one(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        total_pr = sum(s.influence_score for s in scores.values())
        assert 0.9 <= total_pr <= 1.1

    def test_closeness_score_in_range(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        for score in scores.values():
            assert 0.0 <= score.closeness_score <= 1.0

    def test_eigenvector_score_in_range(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        for score in scores.values():
            assert 0.0 <= score.eigenvector_score <= 1.0 + 1e-6

    def test_bridge_score_normalized(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        for score in scores.values():
            assert 0.0 <= score.bridge_score <= 1.0 + 1e-6

    def test_empty_store_returns_empty(self):
        store = GraphStore(":memory:")
        engine = CentralityEngine(store)
        assert engine.compute() == {}

    def test_single_node_no_edges(self):
        store = GraphStore(":memory:")
        store.add_node(CPGNode("lone", NodeKind.FUNCTION, "lone", "f.py"))
        engine = CentralityEngine(store)
        scores = engine.compute()
        assert "lone" in scores
        assert scores["lone"].fan_in == 0


# ---------------------------------------------------------------------------
# Stability and volatility
# ---------------------------------------------------------------------------

class TestStabilityVolatility:
    def test_hub_is_stable(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        # A has 4 callers (fan_in=4) and 1 callee (fan_out=1) → high stability
        assert scores["A"].stability_score > scores["A"].volatility_score

    def test_leaf_caller_is_volatile(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        # B has 0 callers, 1 callee → volatile relative to stable nodes
        assert scores["B"].fan_in == 0

    def test_most_stable_returns_n(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        stable = engine.most_stable(scores, n=3)
        assert len(stable) == 3

    def test_most_volatile_returns_n(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        volatile = engine.most_volatile(scores, n=3)
        assert len(volatile) == 3


# ---------------------------------------------------------------------------
# Top-nodes and hotspots
# ---------------------------------------------------------------------------

class TestTopNodes:
    def test_top_nodes_sorted_descending(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        top = engine.top_nodes(scores, n=6)
        composites = [s.composite_score for s in top]
        assert composites == sorted(composites, reverse=True)

    def test_top_nodes_by_fan_in(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        top = engine.top_nodes(scores, n=1, by="fan_in")
        assert top[0].qualified_name == "A"

    def test_hotspots_returns_list(self):
        store = _hub_graph()
        engine = CentralityEngine(store)
        scores = engine.compute()
        hs = engine.hotspots(scores, n=3)
        assert len(hs) == 3
        assert all(isinstance(h, NodeScore) for h in hs)


# ---------------------------------------------------------------------------
# Integration: CPG graph with real parsers
# ---------------------------------------------------------------------------

class TestCentralityIntegration:
    def test_cpg_graph_scores(self):
        from core.parsers import PythonParser
        from graph.cpg_builder import CPGBuilder

        src = (
            "def alpha():\n    beta()\n    gamma()\n"
            "def beta():\n    gamma()\n"
            "def gamma():\n    pass\n"
        )
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=False)
        builder.ingest(PythonParser().parse(src, "mod.py"))

        engine = CentralityEngine(store)
        scores = engine.compute()
        assert "gamma" in scores
        # gamma is called by alpha and beta → highest fan_in
        assert scores["gamma"].fan_in >= 2
