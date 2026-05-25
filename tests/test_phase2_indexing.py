"""Phase 2 tests: SCIP/LSP/jedi semantic indexing layer."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.parsers import PythonParser
from core.types import NodeKind
from graph.cpg_builder import CPGBuilder
from graph.graph_store import GraphStore
from indexing.graph_enricher import EnrichmentStats, GraphEnricher
from indexing.indexing_provider import (
    IndexingProvider,
    IndexResult,
    ProviderRegistry,
    SymbolDefinition,
    SymbolOccurrence,
)
from indexing.jedi_provider import JediProvider
from indexing.reference_graph import ReferenceGraph
from indexing.scip_provider import SCIPProvider, _scip_symbol_to_qualified
from indexing.semantic_index import SemanticIndex
from indexing.symbol_cache import SymbolCache
from indexing.symbol_resolver import SymbolResolver

# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_register_and_best_for(self):
        registry = ProviderRegistry()
        p = JediProvider()
        registry.register(p)
        best = registry.best_for("python")
        assert best is p

    def test_best_for_unavailable_returns_none(self):
        class BrokenProvider(IndexingProvider):
            priority = 1
            name = "broken"

            def supports(self, lang):
                return lang == "python"

            def is_available(self):
                return False

            def index_file(self, f, r):
                return IndexResult()

        registry = ProviderRegistry()
        registry.register(BrokenProvider())
        assert registry.best_for("python") is None

    def test_priority_ordering(self):
        class Hi(IndexingProvider):
            priority = 1
            name = "hi"

            def supports(self, lang):
                return True

            def is_available(self):
                return True

            def index_file(self, f, r):
                return IndexResult()

        class Lo(IndexingProvider):
            priority = 99
            name = "lo"

            def supports(self, lang):
                return True

            def is_available(self):
                return True

            def index_file(self, f, r):
                return IndexResult()

        registry = ProviderRegistry()
        registry.register(Lo())
        registry.register(Hi())
        best = registry.best_for("python")
        assert best.name == "hi"


# ---------------------------------------------------------------------------
# JediProvider
# ---------------------------------------------------------------------------


class TestJediProvider:
    def test_supports_python_only(self):
        p = JediProvider()
        assert p.supports("python")
        assert not p.supports("typescript")

    def test_is_available(self):
        p = JediProvider()
        assert p.is_available()

    def test_index_file_returns_definitions(self, tmp_path):
        src = tmp_path / "sample.py"
        src.write_text("""
def greet(name):
    return "Hello " + name

class Greeter:
    def hello(self):
        greet("world")
""")
        p = JediProvider()
        result = p.index_file(src, tmp_path)
        assert result.source == "jedi"
        assert result.language == "python"
        names = [d.display_name for d in result.definitions]
        assert "greet" in names or any("greet" in n for n in names)

    def test_index_file_records_occurrences(self, tmp_path):
        src = tmp_path / "caller.py"
        src.write_text("def foo():\n    len([1, 2, 3])\n")
        p = JediProvider()
        result = p.index_file(src, tmp_path)
        assert result.source == "jedi"
        # At minimum no crash; occurrences may or may not resolve builtins
        assert isinstance(result.occurrences, list)

    def test_index_file_no_crash_on_syntax_error(self, tmp_path):
        src = tmp_path / "bad.py"
        src.write_text("def (broken syntax")
        p = JediProvider()
        result = p.index_file(src, tmp_path)
        # Should return result with errors, not raise
        assert isinstance(result, IndexResult)


# ---------------------------------------------------------------------------
# SCIPProvider
# ---------------------------------------------------------------------------


class TestSCIPProvider:
    def test_supports_python_typescript(self):
        p = SCIPProvider()
        assert p.supports("python")
        assert p.supports("typescript")
        assert not p.supports("php")

    def test_load_index_minimal_json(self, tmp_path):
        index_data = {
            "metadata": {"tool": {"name": "scip-python"}},
            "documents": [
                {
                    "relativePath": "src/foo.py",
                    "language": "python",
                    "symbols": [
                        {
                            "symbol": "python . pkg src/foo.py `bar`().",
                            "displayName": "bar",
                            "kind": 10,
                        }
                    ],
                    "occurrences": [
                        {
                            "range": [5, 0, 3],
                            "symbol": "python . pkg src/foo.py `bar`().",
                            "symbolRoles": 1,
                        }
                    ],
                }
            ],
            "externalSymbols": [],
        }
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data))

        p = SCIPProvider()
        result = p.load_index(index_path, tmp_path)
        assert result.source == "scip"
        assert len(result.definitions) == 1
        assert result.definitions[0].display_name == "bar"
        assert len(result.occurrences) == 1
        assert result.occurrences[0].role == "definition"

    def test_scip_symbol_to_qualified(self):
        sym = "python . pkg foo/bar.py `my_func`()."
        q = _scip_symbol_to_qualified(sym)
        assert "my_func" in q

    def test_load_index_malformed_graceful(self, tmp_path):
        bad = tmp_path / "index.json"
        bad.write_text("{not valid json")
        p = SCIPProvider()
        result = p.load_index(bad, tmp_path)
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# ReferenceGraph
# ---------------------------------------------------------------------------


class TestReferenceGraph:
    def test_add_and_retrieve(self):
        rg = ReferenceGraph(":memory:")
        occ = SymbolOccurrence(
            symbol="utils.helpers.bar",
            file_path="src/foo.py",
            line=10,
            column=4,
            role="reference",
        )
        rg.add(occ)
        refs = rg.references_to("utils.helpers.bar")
        assert len(refs) == 1
        assert refs[0].file_path == "src/foo.py"

    def test_add_many(self):
        rg = ReferenceGraph(":memory:")
        occs = [
            SymbolOccurrence("pkg.A", "a.py", 1, 0),
            SymbolOccurrence("pkg.B", "b.py", 2, 0),
            SymbolOccurrence("pkg.A", "c.py", 3, 0),
        ]
        rg.add_many(occs)
        assert rg.count() == 3
        assert len(rg.references_to("pkg.A")) == 2

    def test_remove_file(self):
        rg = ReferenceGraph(":memory:")
        rg.add(SymbolOccurrence("sym.A", "file1.py", 1, 0))
        rg.add(SymbolOccurrence("sym.B", "file1.py", 2, 0))
        rg.add(SymbolOccurrence("sym.A", "file2.py", 3, 0))
        rg.remove_file("file1.py")
        assert rg.count() == 1
        assert rg.references_in_file("file1.py") == []

    def test_callers_of(self):
        rg = ReferenceGraph(":memory:")
        rg.add(SymbolOccurrence("my.func", "a.py", 1, 0, role="reference"))
        rg.add(SymbolOccurrence("my.func", "b.py", 2, 0, role="reference"))
        callers = rg.callers_of("my.func")
        assert set(callers) == {"a.py", "b.py"}


# ---------------------------------------------------------------------------
# SymbolCache
# ---------------------------------------------------------------------------


class TestSymbolCache:
    def test_put_and_get(self):
        cache = SymbolCache(":memory:")
        defn = SymbolDefinition(
            qualified_name="pkg.utils.bar",
            kind=NodeKind.FUNCTION,
            file_path="pkg/utils.py",
            line=10,
            language="python",
            display_name="bar",
        )
        cache.put(defn)
        retrieved = cache.get("pkg.utils.bar")
        assert retrieved is not None
        assert retrieved.display_name == "bar"
        assert retrieved.kind == NodeKind.FUNCTION

    def test_search_prefix(self):
        cache = SymbolCache(":memory:")
        cache.put(
            SymbolDefinition("mod.helper", NodeKind.FUNCTION, "mod.py", 1, display_name="helper")
        )
        cache.put(
            SymbolDefinition("mod.Helper", NodeKind.CLASS, "mod.py", 5, display_name="Helper")
        )
        results = cache.search("help")
        assert len(results) >= 1

    def test_search_exact(self):
        cache = SymbolCache(":memory:")
        cache.put(SymbolDefinition("a.foo", NodeKind.FUNCTION, "a.py", 1, display_name="foo"))
        cache.put(SymbolDefinition("b.foo", NodeKind.FUNCTION, "b.py", 2, display_name="foo"))
        exact = cache.search("foo", exact=True)
        assert len(exact) == 2

    def test_invalidate_file(self):
        cache = SymbolCache(":memory:")
        cache.put(SymbolDefinition("mod.A", NodeKind.CLASS, "mod.py", 1, display_name="A"))
        cache.put(SymbolDefinition("mod.B", NodeKind.FUNCTION, "mod.py", 5, display_name="B"))
        assert cache.count() == 2
        cache.invalidate_file("mod.py")
        assert cache.count() == 0

    def test_put_many(self):
        cache = SymbolCache(":memory:")
        defs = [
            SymbolDefinition(
                f"mod.func{i}", NodeKind.FUNCTION, "mod.py", i, display_name=f"func{i}"
            )
            for i in range(10)
        ]
        cache.put_many(defs)
        assert cache.count() == 10


# ---------------------------------------------------------------------------
# GraphEnricher
# ---------------------------------------------------------------------------


class TestGraphEnricher:
    def _build_simple_graph(self):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store)
        parser = PythonParser()
        source = """
def caller():
    bar()

def bar():
    pass
"""
        result = parser.parse(source, "sample.py")
        builder.ingest(result)
        return store

    def test_enrich_project_runs_without_error(self, tmp_path):
        src = tmp_path / "sample.py"
        src.write_text("def foo():\n    pass\n\ndef bar():\n    foo()\n")
        store = GraphStore(":memory:")
        builder = CPGBuilder(store)
        parser = PythonParser()
        result = parser.parse(src.read_text(), str(src))
        builder.ingest(result)

        provider = JediProvider()
        enricher = GraphEnricher(store, provider)
        stats = enricher.enrich_project(tmp_path, "python")

        assert isinstance(stats, EnrichmentStats)
        assert stats.files_processed >= 1

    def test_symbol_cache_populated_after_enrichment(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("def my_function():\n    pass\n")
        store = GraphStore(":memory:")
        builder = CPGBuilder(store)
        parser = PythonParser()
        builder.ingest(parser.parse(src.read_text(), str(src)))

        cache = SymbolCache(":memory:")
        provider = JediProvider()
        enricher = GraphEnricher(store, provider, symbol_cache=cache)
        stats = enricher.enrich_project(tmp_path, "python")

        assert stats.definitions_cached > 0


# ---------------------------------------------------------------------------
# SymbolResolver with cache
# ---------------------------------------------------------------------------


class TestSymbolResolverWithCache:
    def test_resolves_via_cache(self):
        store = GraphStore(":memory:")
        idx = SemanticIndex(store)
        cache = SymbolCache(":memory:")
        cache.put(
            SymbolDefinition(
                "pkg.utils.my_func",
                NodeKind.FUNCTION,
                "pkg/utils.py",
                line=5,
                display_name="my_func",
            )
        )

        resolver = SymbolResolver(idx, symbol_cache=cache)
        result = resolver.resolve("my_func", context_file="pkg/utils.py")
        assert result == "pkg.utils.my_func"

    def test_falls_back_to_index_when_cache_empty(self):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store)
        parser = PythonParser()
        result = parser.parse("def known(): pass\n", "src/known.py")
        builder.ingest(result)

        idx = SemanticIndex(store)
        resolver = SymbolResolver(idx)
        # "known" is in the CPG — resolver should find it
        qname = resolver.resolve("known")
        assert qname == "known"
