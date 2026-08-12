"""Phase 3 tests: full CPG — CFG, DFG, endpoints, METHOD nodes, TESTS edges."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.parsers import PythonParser
from core.types import EdgeKind, NodeKind
from graph.cfg_builder import CFGBuilder
from graph.cpg_builder import CPGBuilder
from graph.dfg_builder import DFGBuilder
from graph.endpoint_detector import EndpointDetector, _extract_http_method, _extract_route_path
from graph.graph_store import GraphStore

# ---------------------------------------------------------------------------
# NodeKind / EdgeKind taxonomy
# ---------------------------------------------------------------------------


class TestNodeKindTaxonomy:
    def test_method_kind_exists(self):
        assert NodeKind.METHOD.value == "Method"

    def test_endpoint_kind_exists(self):
        assert NodeKind.ENDPOINT.value == "Endpoint"

    def test_controls_edge_exists(self):
        assert EdgeKind.CONTROLS.value == "CONTROLS"

    def test_reads_writes_exist(self):
        assert EdgeKind.READS.value == "READS"
        assert EdgeKind.WRITES.value == "WRITES"

    def test_tests_edge_exists(self):
        assert EdgeKind.TESTS.value == "TESTS"


# ---------------------------------------------------------------------------
# METHOD vs FUNCTION distinction
# ---------------------------------------------------------------------------


class TestMethodVsFunction:
    def _parse(self, source: str, path: str = "sample.py"):
        return PythonParser().parse(source, path)

    def test_top_level_function_is_function(self):
        result = self._parse("def greet(name):\n    return name\n")
        funcs = [n for n in result.nodes if n.name == "greet"]
        assert len(funcs) == 1
        assert funcs[0].kind == NodeKind.FUNCTION

    def test_class_method_is_method(self):
        src = "class Foo:\n    def bar(self):\n        pass\n"
        result = self._parse(src)
        methods = [n for n in result.nodes if n.name == "bar"]
        assert len(methods) == 1
        assert methods[0].kind == NodeKind.METHOD

    def test_test_function_is_test(self):
        result = self._parse("def test_something():\n    assert True\n")
        tests = [n for n in result.nodes if n.name == "test_something"]
        assert len(tests) == 1
        assert tests[0].kind == NodeKind.TEST


# ---------------------------------------------------------------------------
# TESTS edges
# ---------------------------------------------------------------------------


class TestTestsEdges:
    def test_tests_edge_emitted(self):
        src = "def create_user():\n    pass\n\ndef test_create_user():\n    create_user()\n"
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=False)
        builder.ingest(PythonParser().parse(src, "sample.py"))
        tests_edges = [e for e in store.all_edges() if e.kind == EdgeKind.TESTS]
        assert len(tests_edges) >= 1
        assert tests_edges[0].source == "test_create_user"
        assert tests_edges[0].target == "create_user"


# ---------------------------------------------------------------------------
# CFG builder
# ---------------------------------------------------------------------------


class TestCFGBuilder:
    def _tree_and_source(self, src: str):
        import tree_sitter_language_pack as tslp

        parser = tslp.get_parser("python")
        # tree-sitter parses bytes; the offsets it reports index into them.
        tree = parser.parse(src.encode())
        return tree, src

    def test_controls_edges_from_if(self):
        src = "def foo():\n    if True:\n        bar()\n\ndef bar():\n    pass\n"
        tree, source = self._tree_and_source(src)
        store = GraphStore(":memory:")
        cfg = CFGBuilder(store)
        func_map = {"foo": "foo", "bar": "bar"}
        edges = cfg.process(tree, source, "sample.py", func_map)
        controls = [e for e in edges if e.kind == EdgeKind.CONTROLS]
        assert len(controls) >= 1
        sources = {e.source for e in controls}
        assert "foo" in sources

    def test_controls_edges_from_for(self):
        src = "def process():\n    for x in items:\n        transform(x)\n"
        tree, source = self._tree_and_source(src)
        store = GraphStore(":memory:")
        cfg = CFGBuilder(store)
        func_map = {"process": "process", "transform": "transform"}
        edges = cfg.process(tree, source, "sample.py", func_map)
        controls = [e for e in edges if e.kind == EdgeKind.CONTROLS]
        assert len(controls) >= 1

    def test_no_self_controls_edge(self):
        src = "def foo():\n    if True:\n        foo()\n"
        tree, source = self._tree_and_source(src)
        store = GraphStore(":memory:")
        cfg = CFGBuilder(store)
        edges = cfg.process(tree, source, "sample.py", {"foo": "foo"})
        # foo should not CONTROLS itself
        self_controls = [e for e in edges if e.source == e.target]
        assert len(self_controls) == 0


# ---------------------------------------------------------------------------
# DFG builder
# ---------------------------------------------------------------------------


class TestDFGBuilder:
    def _tree_and_source(self, src: str):
        import tree_sitter_language_pack as tslp

        parser = tslp.get_parser("python")
        # tree-sitter parses bytes; the offsets it reports index into them.
        tree = parser.parse(src.encode())
        return tree, src

    def test_writes_edge_from_assignment(self):
        src = "def foo():\n    result = compute()\n    return result\n"
        tree, source = self._tree_and_source(src)
        store = GraphStore(":memory:")
        dfg = DFGBuilder(store)
        func_map = {"foo": "foo"}
        var_nodes, edges = dfg.process(tree, source, "sample.py", func_map)
        writes = [e for e in edges if e.kind == EdgeKind.WRITES]
        assert len(writes) >= 1
        assert writes[0].source == "foo"

    def test_variable_node_created(self):
        src = "def foo():\n    result = 42\n"
        tree, source = self._tree_and_source(src)
        store = GraphStore(":memory:")
        dfg = DFGBuilder(store)
        var_nodes, _ = dfg.process(tree, source, "sample.py", {"foo": "foo"})
        names = [n.name for n in var_nodes]
        assert "result" in names
        assert all(n.kind == NodeKind.VARIABLE for n in var_nodes)


# ---------------------------------------------------------------------------
# Endpoint detector
# ---------------------------------------------------------------------------


class TestEndpointDetector:
    def _tree(self, src: str, lang: str = "python"):
        import tree_sitter_language_pack as tslp

        parser = tslp.get_parser(lang)
        # tree-sitter parses bytes; the offsets it reports index into them.
        return parser.parse(src.encode()), src

    def test_fastapi_get_detected(self):
        src = (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n\n"
            "@router.get('/users')\n"
            "async def get_users():\n"
            "    return []\n"
        )
        tree, source = self._tree(src)
        det = EndpointDetector()
        eps = det.detect(tree, source, "routes.py", {"get_users": "get_users"})
        assert len(eps) >= 1
        assert eps[0].kind == NodeKind.ENDPOINT
        assert eps[0].extra.get("http_method", "").upper() in ("GET", "ANY")

    def test_laravel_route_detected(self):
        src = "Route::get('/users', [UserController::class, 'index']);"
        det = EndpointDetector()
        # PHP detection uses raw source, no tree needed
        eps = det._detect_php(src, "routes/web.php")
        assert len(eps) >= 1
        assert eps[0].extra["http_method"] == "GET"
        assert eps[0].extra["framework"] == "laravel"

    def test_helper_extract_http_method(self):
        assert _extract_http_method("@router.get('/x')") == "GET"
        assert _extract_http_method("@app.post('/x')") == "POST"

    def test_helper_extract_route_path(self):
        path = _extract_route_path("@app.get('/users/{id}')")
        assert path == "/users/{id}"


# ---------------------------------------------------------------------------
# Full CPG integration — CFG+DFG+endpoints wired together
# ---------------------------------------------------------------------------


class TestCPGBuilderPhase3:
    SOURCE = """
from fastapi import APIRouter
router = APIRouter()

class UserService:
    def get_user(self, user_id: int):
        return self.db.find(user_id)

    def create_user(self, data: dict):
        user = self.db.save(data)
        return user

@router.get('/users/{id}')
async def get_user_endpoint(user_id: int):
    svc = UserService()
    return svc.get_user(user_id)

def test_get_user():
    svc = UserService()
    svc.get_user(1)
"""

    def test_method_nodes_present(self):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=True, enable_dfg=True, enable_endpoints=True)
        result = PythonParser().parse(self.SOURCE, "app.py")
        builder.ingest(result)

        methods = [n for n in store.all_nodes() if n.kind == NodeKind.METHOD]
        assert len(methods) >= 2

    def test_controls_edges_present(self):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=True, enable_dfg=False, enable_endpoints=False)
        builder.ingest(
            PythonParser().parse(
                "def f():\n    if True:\n        g()\ndef g():\n    pass\n", "s.py"
            )
        )
        controls = [e for e in store.all_edges() if e.kind == EdgeKind.CONTROLS]
        assert len(controls) >= 1

    def test_writes_edges_present(self):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=True, enable_endpoints=False)
        builder.ingest(
            PythonParser().parse(
                "def foo():\n    result = bar()\n    return result\ndef bar():\n    return 1\n",
                "s.py",
            )
        )
        writes = [e for e in store.all_edges() if e.kind == EdgeKind.WRITES]
        assert len(writes) >= 1

    def test_endpoint_node_present(self):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=True)
        builder.ingest(PythonParser().parse(self.SOURCE, "app.py"))
        endpoints = [n for n in store.all_nodes() if n.kind == NodeKind.ENDPOINT]
        assert len(endpoints) >= 1

    def test_tests_edge_present(self):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=False)
        builder.ingest(PythonParser().parse(self.SOURCE, "app.py"))
        tests_edges = [e for e in store.all_edges() if e.kind == EdgeKind.TESTS]
        assert len(tests_edges) >= 1

    def test_edge_count_exceeds_phase2(self):
        """Phase 3 CPG should have substantially more edges than AST-only."""
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=True, enable_dfg=True, enable_endpoints=True)
        builder.ingest(PythonParser().parse(self.SOURCE, "app.py"))
        # Baseline AST-only would have ~8-10 edges for this source
        assert store.edge_count() >= 15
