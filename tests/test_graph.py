"""Tests for graph layer: GraphStore, CPGBuilder, schema."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import EdgeKind, NodeKind, SourceRange
from graph import CPGBuilder, CPGEdge, CPGNode, GraphStore


def _make_store() -> GraphStore:
    return GraphStore(":memory:")


def _sample_node(
    qname: str, kind: NodeKind = NodeKind.FUNCTION, file_path: str = "a.py"
) -> CPGNode:
    return CPGNode(
        qualified_name=qname,
        kind=kind,
        name=qname.split(".")[-1],
        file_path=file_path,
        language="python",
        range=SourceRange(1, 10),
    )


def _sample_edge(src: str, tgt: str, kind: EdgeKind = EdgeKind.CALLS) -> CPGEdge:
    return CPGEdge(kind=kind, source=src, target=tgt, file_path="a.py")


class TestGraphStore:
    def test_add_and_retrieve_node(self):
        store = _make_store()
        node = _sample_node("mymod.MyClass.method")
        store.add_node(node)
        retrieved = store.get_node("mymod.MyClass.method")
        assert retrieved is not None
        assert retrieved.qualified_name == "mymod.MyClass.method"
        assert retrieved.kind == NodeKind.FUNCTION

    def test_get_node_missing(self):
        store = _make_store()
        assert store.get_node("does.not.exist") is None

    def test_add_and_retrieve_edge(self):
        store = _make_store()
        store.add_node(_sample_node("a.foo"))
        store.add_node(_sample_node("a.bar"))
        store.add_edge(_sample_edge("a.foo", "a.bar"))

        out = store.get_outgoing_edges("a.foo")
        assert len(out) == 1
        assert out[0].target == "a.bar"
        assert out[0].kind == EdgeKind.CALLS

        inc = store.get_incoming_edges("a.bar")
        assert len(inc) == 1
        assert inc[0].source == "a.foo"

    def test_node_count_and_edge_count(self):
        store = _make_store()
        store.add_nodes([_sample_node(f"mod.func{i}") for i in range(5)])
        store.add_edges([_sample_edge(f"mod.func{i}", f"mod.func{i + 1}") for i in range(4)])
        assert store.node_count() == 5
        assert store.edge_count() == 4

    def test_clear(self):
        store = _make_store()
        store.add_node(_sample_node("a.b"))
        store.clear()
        assert store.node_count() == 0
        assert store.edge_count() == 0

    def test_get_nodes_by_kind(self):
        store = _make_store()
        store.add_node(_sample_node("a.MyClass", kind=NodeKind.CLASS))
        store.add_node(_sample_node("a.my_func", kind=NodeKind.FUNCTION))
        classes = store.get_nodes_by_kind(NodeKind.CLASS)
        assert len(classes) == 1
        assert classes[0].qualified_name == "a.MyClass"

    def test_get_nodes_by_file(self):
        store = _make_store()
        store.add_node(_sample_node("a.foo", file_path="a.py"))
        store.add_node(_sample_node("b.bar", file_path="b.py"))
        a_nodes = store.get_nodes_by_file("a.py")
        assert len(a_nodes) == 1
        assert a_nodes[0].qualified_name == "a.foo"

    def test_has_node(self):
        store = _make_store()
        store.add_node(_sample_node("a.foo"))
        assert store.has_node("a.foo") is True
        assert store.has_node("a.missing") is False

    def test_bulk_insert_matches_per_row_insert(self):
        nodes = [_sample_node(f"mod.func{i}") for i in range(50)]
        edges = [_sample_edge(f"mod.func{i}", f"mod.func{i + 1}") for i in range(49)]

        batched = _make_store()
        batched.add_nodes(nodes)
        batched.add_edges(edges)

        one_by_one = _make_store()
        for node in nodes:
            one_by_one.add_node(node)
        for edge in edges:
            one_by_one.add_edge(edge)

        assert batched.node_count() == one_by_one.node_count() == 50
        assert batched.edge_count() == one_by_one.edge_count() == 49
        assert [n.qualified_name for n in batched.all_nodes()] == [
            n.qualified_name for n in one_by_one.all_nodes()
        ]

    def test_bulk_insert_of_empty_list(self):
        store = _make_store()
        store.add_nodes([])
        store.add_edges([])
        assert store.node_count() == 0
        assert store.edge_count() == 0

    def test_bulk_insert_replaces_duplicates(self):
        store = _make_store()
        store.add_nodes([_sample_node("mod.func"), _sample_node("mod.func")])
        assert store.node_count() == 1

    def test_replace_node_on_duplicate(self):
        store = _make_store()
        n1 = _sample_node("mod.func")
        n2 = CPGNode(
            qualified_name="mod.func",
            kind=NodeKind.FUNCTION,
            name="func",
            file_path="mod_v2.py",
            language="python",
        )
        store.add_node(n1)
        store.add_node(n2)
        assert store.node_count() == 1
        retrieved = store.get_node("mod.func")
        assert retrieved.file_path == "mod_v2.py"


class TestCPGBuilder:
    def test_ingest_parse_result(self):
        from core.types import NodeInfo, ParseResult

        store = _make_store()
        builder = CPGBuilder(store)

        result = ParseResult(
            language="python",
            file_path="mymodule.py",
            nodes=[
                NodeInfo(
                    kind=NodeKind.FUNCTION,
                    name="hello",
                    file_path="mymodule.py",
                    range=SourceRange(1, 5),
                    language="python",
                )
            ],
            edges=[],
        )
        builder.ingest(result)

        # File node + function node
        assert store.node_count() == 2
        assert store.get_node("mymodule.py") is not None  # File node
        assert store.get_node("hello") is not None

    def test_ingest_skips_errors(self):
        from core.types import ParseResult

        store = _make_store()
        builder = CPGBuilder(store)
        bad = ParseResult(
            language="python",
            file_path="bad.py",
            errors=["syntax error"],
        )
        builder.ingest(bad)
        assert store.node_count() == 0

    def test_remove_file(self):
        from core.types import NodeInfo, ParseResult

        store = _make_store()
        builder = CPGBuilder(store)

        result = ParseResult(
            language="python",
            file_path="to_remove.py",
            nodes=[
                NodeInfo(
                    kind=NodeKind.FUNCTION,
                    name="stale_func",
                    file_path="to_remove.py",
                    range=SourceRange(1, 3),
                    language="python",
                )
            ],
            edges=[],
        )
        builder.ingest(result)
        assert store.node_count() >= 1

        builder.remove_file("to_remove.py")
        assert store.get_node("to_remove.py") is None
        assert store.get_node("stale_func") is None
