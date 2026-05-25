"""Phase 4 tests: code-aware embeddings, pipeline, FAISS integration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.parsers import PythonParser
from core.types import NodeKind
from embeddings.code_embedder import CodeEmbedder, _camel_to_words, _kind_label
from embeddings.embedding_pipeline import EmbeddingPipeline, PipelineStats
from embeddings.embedding_store import EmbeddingStore
from embeddings.provider_factory import EmbeddingConfig, StubEmbeddingProvider
from graph.cpg_builder import CPGBuilder
from graph.graph_store import GraphStore
from graph.schema import CPGNode

# ---------------------------------------------------------------------------
# CodeEmbedder
# ---------------------------------------------------------------------------


class TestCodeEmbedder:
    def _node(
        self,
        kind=NodeKind.FUNCTION,
        name="my_func",
        parent=None,
        params=None,
        file_path="src/utils.py",
        **kwargs,
    ) -> CPGNode:
        return CPGNode(
            qualified_name=f"{parent}.{name}" if parent else name,
            kind=kind,
            name=name,
            file_path=file_path,
            parent_qualified=parent,
            params=params or [],
            **kwargs,
        )

    def test_function_text_includes_kind_and_name(self):
        e = CodeEmbedder()
        node = self._node(name="compute_total")
        text = e.embed_text(node)
        assert "function" in text
        assert "compute" in text and "total" in text  # camelCase split

    def test_method_text_includes_parent(self):
        e = CodeEmbedder()
        node = self._node(kind=NodeKind.METHOD, name="get_user", parent="UserService")
        text = e.embed_text(node)
        assert "method" in text
        assert "user" in text.lower()
        assert "service" in text.lower() or "user service" in text.lower()

    def test_endpoint_text_includes_http_info(self):
        e = CodeEmbedder()
        node = CPGNode(
            qualified_name="get_users__endpoint",
            kind=NodeKind.ENDPOINT,
            name="get_users",
            file_path="routes.py",
            extra={"http_method": "GET", "path": "/users", "framework": "fastapi"},
        )
        text = e.embed_text(node)
        assert "endpoint" in text
        assert "GET" in text or "get" in text.lower()
        assert "/users" in text

    def test_variable_node_skipped(self):
        e = CodeEmbedder()
        node = self._node(kind=NodeKind.VARIABLE, name="result")
        assert not e.should_embed(node)

    def test_parameter_node_skipped(self):
        e = CodeEmbedder()
        node = self._node(kind=NodeKind.PARAMETER, name="user_id")
        assert not e.should_embed(node)

    def test_embed_texts_filters_non_semantic(self):
        e = CodeEmbedder()
        nodes = [
            self._node(NodeKind.FUNCTION, "foo"),
            self._node(NodeKind.VARIABLE, "x"),
            self._node(NodeKind.METHOD, "bar"),
            self._node(NodeKind.PARAMETER, "p"),
        ]
        pairs = e.embed_texts(nodes)
        assert len(pairs) == 2
        names = [n for n, _ in pairs]
        assert "foo" in names
        assert "bar" in names

    def test_camel_to_words(self):
        assert _camel_to_words("getUserById") == "get user by id"
        assert _camel_to_words("my_function") == "my function"
        assert _camel_to_words("HTTPClient") == "http client"

    def test_kind_label(self):
        assert _kind_label(NodeKind.FUNCTION) == "function"
        assert _kind_label(NodeKind.METHOD) == "method"
        assert _kind_label(NodeKind.ENDPOINT) == "api endpoint"


# ---------------------------------------------------------------------------
# EmbeddingStore (updated add_batch signature)
# ---------------------------------------------------------------------------


class TestEmbeddingStoreUpdated:
    def test_add_batch_new_signature(self):
        provider = StubEmbeddingProvider(dimensions=16)
        store = EmbeddingStore(provider)
        names = ["func.a", "func.b"]
        texts = ["function a", "function b"]
        store.add_batch(names, texts)
        assert store.size() == 2

    def test_search_with_threshold(self):
        provider = StubEmbeddingProvider(dimensions=16)
        store = EmbeddingStore(provider)
        store.add_batch(["a", "b", "c"], ["text a", "text b", "text c"])
        # With threshold=0 all results returned
        results = store.search("text", top_k=5, threshold=0.0)
        assert len(results) >= 1

    def test_faiss_search_used_when_available(self):
        provider = StubEmbeddingProvider(dimensions=16)
        store = EmbeddingStore(provider, use_faiss=True)
        store.add_batch(
            [f"node_{i}" for i in range(20)],
            [f"text {i}" for i in range(20)],
        )
        results = store.search("text 5", top_k=5)
        assert len(results) == 5
        # Scores should be in [0, 1] range
        for r in results:
            assert 0.0 <= r.score <= 1.0 + 1e-6


# ---------------------------------------------------------------------------
# EmbeddingPipeline
# ---------------------------------------------------------------------------


class TestEmbeddingPipeline:
    def _build_store(self, source: str = None) -> GraphStore:
        src = source or (
            "def compute_total(items):\n    return sum(items)\n\n"
            "class OrderService:\n    def create_order(self, data):\n        pass\n"
        )
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=False)
        parser = PythonParser()
        builder.ingest(parser.parse(src, "service.py"))
        return store

    def test_index_all_populates_store(self):
        graph = self._build_store()
        config = EmbeddingConfig(provider="stub", dimensions=16)
        pipeline = EmbeddingPipeline.from_config(graph, config)
        stats = pipeline.index_all()
        assert stats.nodes_embedded > 0
        assert pipeline.embedding_store.size() > 0

    def test_skips_variable_nodes(self):
        graph = self._build_store()
        graph.add_node(CPGNode("compute_total.result", NodeKind.VARIABLE, "result", "service.py"))

        config = EmbeddingConfig(provider="stub", dimensions=16)
        pipeline = EmbeddingPipeline.from_config(graph, config)
        stats = pipeline.index_all()
        assert stats.nodes_skipped >= 1

    def test_search_returns_results(self):
        graph = self._build_store()
        config = EmbeddingConfig(provider="stub", dimensions=16)
        pipeline = EmbeddingPipeline.from_config(graph, config)
        pipeline.index_all()
        results = pipeline.search("compute total function", top_k=5)
        assert isinstance(results, list)
        assert len(results) <= 5

    def test_search_with_threshold(self):
        graph = self._build_store()
        config = EmbeddingConfig(provider="stub", dimensions=16)
        pipeline = EmbeddingPipeline.from_config(graph, config)
        pipeline.index_all()
        # Very high threshold — should return fewer results
        results_high = pipeline.search("anything", top_k=10, threshold=0.99)
        results_low = pipeline.search("anything", top_k=10, threshold=0.0)
        assert len(results_high) <= len(results_low)

    def test_index_file_incremental(self, tmp_path):
        store = GraphStore(":memory:")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=False)
        src_file = tmp_path / "mod.py"
        src_file.write_text("def alpha():\n    pass\ndef beta():\n    pass\n")
        parser = PythonParser()
        builder.ingest(parser.parse(src_file.read_text(), str(src_file)))

        config = EmbeddingConfig(provider="stub", dimensions=16)
        pipeline = EmbeddingPipeline.from_config(store, config)
        pipeline.index_all()
        pipeline.embedding_store.size()

        # Add a new function and re-index that file
        src_file.write_text(
            "def alpha():\n    pass\ndef beta():\n    pass\ndef gamma():\n    pass\n"
        )
        builder.ingest(parser.parse(src_file.read_text(), str(src_file)))
        stats = pipeline.index_file(str(src_file))
        assert stats.nodes_embedded >= 1

    def test_pipeline_stats_str(self):
        stats = PipelineStats(nodes_embedded=10, nodes_skipped=5, embed_time_s=0.5)
        s = str(stats)
        assert "embedded=10" in s
        assert "skipped=5" in s
