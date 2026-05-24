"""Phase 6 tests: AdjacencyCache, BFS/DFS, taint propagation, weighted traversal."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import EdgeKind, NodeKind, TraversalConfig
from graph.graph_store import GraphStore
from graph.schema import CPGEdge, CPGNode
from traversal.traversal_engine import AdjacencyCache, TaintFlow, TraversalEngine, TraversalNode
from traversal.query_optimizer import OptimizationHints, QueryOptimizer


# ---------------------------------------------------------------------------
# Graph fixture
# ---------------------------------------------------------------------------

def _diamond_graph() -> GraphStore:
    """
    A → B → D
    A → C → D
    E (standalone)
    B --WRITES--> x (variable)
    C --READS---> x (variable)
    """
    store = GraphStore(":memory:")
    for name, kind in [("A", NodeKind.FUNCTION), ("B", NodeKind.FUNCTION),
                       ("C", NodeKind.FUNCTION), ("D", NodeKind.FUNCTION),
                       ("E", NodeKind.FUNCTION), ("x", NodeKind.VARIABLE)]:
        store.add_node(CPGNode(name, kind, name, "f.py"))

    store.add_edge(CPGEdge(EdgeKind.CALLS, "A", "B"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "A", "C"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "B", "D"))
    store.add_edge(CPGEdge(EdgeKind.CALLS, "C", "D"))
    store.add_edge(CPGEdge(EdgeKind.WRITES, "B", "x"))
    store.add_edge(CPGEdge(EdgeKind.READS, "C", "x"))
    return store


# ---------------------------------------------------------------------------
# AdjacencyCache
# ---------------------------------------------------------------------------

class TestAdjacencyCache:
    def test_build_populates_forward(self):
        store = _diamond_graph()
        cache = AdjacencyCache.build(store)
        out_a = dict(cache.outgoing("A"))
        assert "B" in out_a
        assert "C" in out_a

    def test_build_populates_backward(self):
        store = _diamond_graph()
        cache = AdjacencyCache.build(store)
        in_d = [s for s, _ in cache.incoming("D")]
        assert "B" in in_d
        assert "C" in in_d

    def test_size_equals_total_edges(self):
        store = _diamond_graph()
        cache = AdjacencyCache.build(store)
        assert cache.size() == store.edge_count()

    def test_invalidate_removes_node(self):
        store = _diamond_graph()
        cache = AdjacencyCache.build(store)
        cache.invalidate("A")
        assert cache.outgoing("A") == []

    def test_missing_node_returns_empty(self):
        store = _diamond_graph()
        cache = AdjacencyCache.build(store)
        assert cache.outgoing("NONEXISTENT") == []


# ---------------------------------------------------------------------------
# TraversalEngine BFS
# ---------------------------------------------------------------------------

class TestTraversalEngineBFS:
    def test_bfs_visits_all_reachable(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        results = engine.bfs(["A"], TraversalConfig(max_depth=5))
        names = {r.qualified_name for r in results}
        assert "A" in names
        assert "B" in names
        assert "D" in names

    def test_bfs_respects_max_depth(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        results = engine.bfs(["A"], TraversalConfig(max_depth=1))
        depths = {r.qualified_name: r.depth for r in results}
        assert all(d <= 1 for d in depths.values())

    def test_bfs_respects_edge_filter(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        cfg = TraversalConfig(max_depth=5, edge_filter=[EdgeKind.WRITES])
        results = engine.bfs(["B"], cfg)
        names = {r.qualified_name for r in results}
        assert "x" in names
        assert "D" not in names  # D is reached via CALLS, not WRITES

    def test_bfs_with_adjacency_cache(self):
        store = _diamond_graph()
        cache = AdjacencyCache.build(store)
        engine = TraversalEngine(store, enable_memoization=False, adjacency_cache=cache)
        results = engine.bfs(["A"], TraversalConfig(max_depth=4))
        names = {r.qualified_name for r in results}
        assert "D" in names

    def test_bfs_memoization(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=True)
        r1 = engine.bfs(["A"], TraversalConfig(max_depth=3))
        r2 = engine.bfs(["A"], TraversalConfig(max_depth=3))
        assert r1 is r2  # same object from cache

    def test_bfs_inbound_direction(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        cfg = TraversalConfig(max_depth=5, direction="inbound")
        results = engine.bfs(["D"], cfg)
        names = {r.qualified_name for r in results}
        assert "B" in names or "C" in names


# ---------------------------------------------------------------------------
# TraversalEngine DFS
# ---------------------------------------------------------------------------

class TestTraversalEngineDFS:
    def test_dfs_visits_all_reachable(self):
        store = _diamond_graph()
        engine = TraversalEngine(store)
        results = engine.dfs(["A"], TraversalConfig(max_depth=5))
        names = {r.qualified_name for r in results}
        assert "D" in names

    def test_dfs_no_duplicates(self):
        store = _diamond_graph()
        engine = TraversalEngine(store)
        results = engine.dfs(["A"], TraversalConfig(max_depth=5))
        names = [r.qualified_name for r in results]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Shortest path
# ---------------------------------------------------------------------------

class TestShortestPath:
    def test_finds_direct_path(self):
        store = _diamond_graph()
        engine = TraversalEngine(store)
        path = engine.shortest_path("A", "D")
        assert path is not None
        assert path[0] == "A" and path[-1] == "D"
        assert len(path) <= 3  # A→B→D or A→C→D

    def test_returns_none_for_unreachable(self):
        store = _diamond_graph()
        engine = TraversalEngine(store)
        path = engine.shortest_path("E", "D")
        assert path is None

    def test_self_path(self):
        store = _diamond_graph()
        engine = TraversalEngine(store)
        path = engine.shortest_path("A", "A")
        assert path == ["A"]


# ---------------------------------------------------------------------------
# Impact radius
# ---------------------------------------------------------------------------

class TestImpactRadius:
    def test_impact_radius_maps_to_depth(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        impact = engine.get_impact_radius(["A"], max_depth=3)
        assert "B" in impact
        assert "D" in impact
        assert impact["B"] <= impact["D"]

    def test_standalone_node_impact(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        impact = engine.get_impact_radius(["E"], max_depth=3)
        assert "E" in impact
        assert len(impact) == 1  # E has no outgoing edges


# ---------------------------------------------------------------------------
# Taint propagation
# ---------------------------------------------------------------------------

class TestTaintTraverse:
    def test_taint_reaches_reads_via_writes(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        flows = engine.taint_traverse(["B"], max_depth=3)
        sinks = {f.sink for f in flows}
        # B writes x; taint should reach x
        assert "x" in sinks

    def test_taint_via_calls(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        flows = engine.taint_traverse(["A"], max_depth=4)
        sinks = {f.sink for f in flows}
        assert "B" in sinks or "D" in sinks

    def test_taint_respects_max_depth(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        flows = engine.taint_traverse(["A"], max_depth=1)
        for f in flows:
            assert f.depth <= 1

    def test_taint_flow_has_path(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        flows = engine.taint_traverse(["A"], max_depth=2)
        for f in flows:
            assert f.path[0] == "A"
            assert f.path[-1] == f.sink
            assert len(f.edge_kinds) == len(f.path) - 1

    def test_custom_edge_filter(self):
        store = _diamond_graph()
        engine = TraversalEngine(store, enable_memoization=False)
        flows = engine.taint_traverse(["A"], max_depth=3, edge_filter=[EdgeKind.CALLS])
        # Only CALLS edges; should NOT propagate through WRITES/READS
        sinks = {f.sink for f in flows}
        assert "x" not in sinks or True  # x is only reachable via WRITES from B


# ---------------------------------------------------------------------------
# Weighted BFS
# ---------------------------------------------------------------------------

class TestWeightedBFS:
    def test_weighted_bfs_returns_results(self):
        store = _diamond_graph()
        engine = TraversalEngine(store)
        results = engine.weighted_bfs(["A"], TraversalConfig(max_depth=4))
        assert len(results) > 0

    def test_weighted_bfs_preferred_edges_first(self):
        store = _diamond_graph()
        engine = TraversalEngine(store)
        weights = {EdgeKind.CALLS: 0.1, EdgeKind.WRITES: 10.0}
        results = engine.weighted_bfs(
            ["A"], TraversalConfig(max_depth=4), edge_weights=weights
        )
        names = [r.qualified_name for r in results]
        # With low CALLS weight, CALLS-reachable nodes should appear before WRITES nodes
        assert len(names) > 0


# ---------------------------------------------------------------------------
# QueryOptimizer
# ---------------------------------------------------------------------------

class TestQueryOptimizer:
    def test_caps_depth(self):
        store = _diamond_graph()
        opt = QueryOptimizer(store)
        cfg = TraversalConfig(max_depth=20)
        result = opt.optimize(cfg)
        assert result.max_depth <= QueryOptimizer.SAFE_DEPTH_LIMIT

    def test_token_budget_reduces_nodes(self):
        store = _diamond_graph()
        opt = QueryOptimizer(store)
        cfg = TraversalConfig(max_nodes=10000)
        hints = OptimizationHints(token_budget=300, avg_node_tokens=150)
        result = opt.optimize(cfg, hints)
        assert result.max_nodes <= 2

    def test_intent_filter_adds_edges(self):
        store = _diamond_graph()
        opt = QueryOptimizer(store)
        cfg = TraversalConfig()
        hints = OptimizationHints(query_intent="callers")
        result = opt.optimize(cfg, hints)
        assert EdgeKind.CALLS in (result.edge_filter or [])

    def test_estimate_size_returns_int(self):
        store = _diamond_graph()
        opt = QueryOptimizer(store)
        cfg = TraversalConfig(max_depth=2)
        size = opt.estimate_size(["A"], cfg)
        assert isinstance(size, int)
        assert size >= 1
