"""Phase 10 tests: graph snapshots, parallel indexing, delta embeddings, pipeline."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from graph.graph_store import GraphStore
from graph.schema import CPGEdge, CPGNode
from core.types import EdgeKind, NodeKind
from performance.graph_snapshot import GraphSnapshot
from performance.parallel_indexer import ParallelIndexer
from performance.delta_embedder import DeltaEmbedder
from embeddings.provider_factory import EmbeddingConfig, StubEmbeddingProvider
from embeddings.embedding_store import EmbeddingStore
from embeddings.embedding_pipeline import EmbeddingPipeline
from pipeline.context_builder import CodeCortexPipeline, PipelineConfig


# ---------------------------------------------------------------------------
# GraphSnapshot
# ---------------------------------------------------------------------------

class TestGraphSnapshot:
    def _filled_store(self) -> GraphStore:
        store = GraphStore(":memory:")
        store.add_node(CPGNode("alpha", NodeKind.FUNCTION, "alpha", "f.py"))
        store.add_node(CPGNode("beta", NodeKind.FUNCTION, "beta", "f.py"))
        store.add_node(CPGNode("MyClass", NodeKind.CLASS, "MyClass", "c.py"))
        store.add_edge(CPGEdge(EdgeKind.CALLS, "alpha", "beta"))
        store.add_edge(CPGEdge(EdgeKind.CONTAINS, "MyClass", "alpha"))
        return store

    def test_save_and_load_roundtrip(self, tmp_path):
        store = self._filled_store()
        snap = GraphSnapshot(store)
        snap_path = tmp_path / "graph.json"
        snap.save(snap_path)
        assert snap_path.exists()

        store2 = GraphStore(":memory:")
        snap2 = GraphSnapshot(store2)
        snap2.load(snap_path)
        assert store2.node_count() == 3
        assert store2.edge_count() == 2

    def test_snapshot_preserves_kinds(self, tmp_path):
        store = self._filled_store()
        snap = GraphSnapshot(store)
        path = tmp_path / "g.json"
        snap.save(path)

        store2 = GraphStore(":memory:")
        GraphSnapshot(store2).load(path)
        class_node = store2.get_node("MyClass")
        assert class_node is not None
        assert class_node.kind == NodeKind.CLASS

    def test_snapshot_with_mtimes(self, tmp_path):
        store = self._filled_store()
        snap = GraphSnapshot(store)
        mtimes = {"f.py": 1000.0, "c.py": 2000.0}
        path = tmp_path / "g.json"
        snap.save(path, file_mtimes=mtimes)

        stale = snap.stale_files(path, {"f.py": 1000.0, "c.py": 3000.0})
        assert "c.py" in stale
        assert "f.py" not in stale

    def test_stale_files_all_new(self, tmp_path):
        store = self._filled_store()
        snap = GraphSnapshot(store)
        snap_path = tmp_path / "g.json"
        snap.save(snap_path, file_mtimes={"a.py": 1.0})
        stale = snap.stale_files(snap_path, {"a.py": 1.0, "b.py": 2.0})
        assert "b.py" in stale
        assert "a.py" not in stale

    def test_stale_files_missing_snapshot(self, tmp_path):
        store = self._filled_store()
        snap = GraphSnapshot(store)
        # Non-existent snapshot → all files are stale
        stale = snap.stale_files(tmp_path / "nonexistent.json", {"x.py": 1.0})
        assert "x.py" in stale

    def test_load_wrong_version_raises(self, tmp_path):
        import json
        bad_path = tmp_path / "bad.json"
        bad_path.write_text(json.dumps({"version": 99, "nodes": [], "edges": []}))
        store2 = GraphStore(":memory:")
        with pytest.raises(ValueError, match="version"):
            GraphSnapshot(store2).load(bad_path)


# ---------------------------------------------------------------------------
# ParallelIndexer
# ---------------------------------------------------------------------------

class TestParallelIndexer:
    def test_parse_directory_returns_results(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo(): pass\n")
        (tmp_path / "b.py").write_text("def bar(): pass\n")
        indexer = ParallelIndexer(max_workers=2)
        results = indexer.parse_directory(tmp_path, language="python")
        assert len(results) == 2

    def test_parse_files_subset(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo(): pass\n")
        (tmp_path / "b.py").write_text("class Bar: pass\n")
        indexer = ParallelIndexer(max_workers=2)
        files = [tmp_path / "a.py"]
        results = indexer.parse_files(files, language="python")
        assert len(results) == 1

    def test_unknown_language_returns_empty(self, tmp_path):
        indexer = ParallelIndexer()
        results = indexer.parse_directory(tmp_path, language="cobol")
        assert results == []

    def test_empty_directory_returns_empty(self, tmp_path):
        indexer = ParallelIndexer()
        results = indexer.parse_directory(tmp_path, language="python")
        assert results == []

    def test_single_worker_equivalent_to_multi(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text(f"def fn{i}(): pass\n")
        r1 = ParallelIndexer(max_workers=1).parse_directory(tmp_path)
        r2 = ParallelIndexer(max_workers=4).parse_directory(tmp_path)
        assert len(r1) == len(r2) == 5


# ---------------------------------------------------------------------------
# DeltaEmbedder
# ---------------------------------------------------------------------------

class TestDeltaEmbedder:
    def _pipeline(self, tmp_path):
        store = GraphStore(":memory:")
        from core.parsers.python_parser import PythonParser
        from graph.cpg_builder import CPGBuilder
        f = tmp_path / "mod.py"
        f.write_text("def alpha(): pass\ndef beta(): pass\n")
        builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=False)
        builder.ingest(PythonParser().parse(f.read_text(), str(f)))
        config = EmbeddingConfig(provider="stub", dimensions=8)
        pipeline = EmbeddingPipeline.from_config(store, config)
        pipeline.index_all()
        return pipeline, f

    def test_no_change_returns_zero(self, tmp_path):
        pipeline, f = self._pipeline(tmp_path)
        baseline = {str(f): f.stat().st_mtime}
        updater = DeltaEmbedder(pipeline, baseline)
        result = updater.update([f])
        assert result["changed_files"] == 0

    def test_changed_file_triggers_reindex(self, tmp_path):
        pipeline, f = self._pipeline(tmp_path)
        # Record an old mtime (0 = epoch) → file is always stale
        updater = DeltaEmbedder(pipeline, {str(f): 0.0})
        result = updater.update([f])
        assert result["changed_files"] == 1
        assert result["nodes_embedded"] >= 1

    def test_record_baseline_updates_mtimes(self, tmp_path):
        pipeline, f = self._pipeline(tmp_path)
        updater = DeltaEmbedder(pipeline)
        updater.record_baseline([f])
        snap = updater.baseline_snapshot()
        assert str(f) in snap
        assert snap[str(f)] == f.stat().st_mtime


# ---------------------------------------------------------------------------
# End-to-end pipeline (Phase 9 integration via Phase 10 test)
# ---------------------------------------------------------------------------

class TestCodeCortexPipelineE2E:
    def test_build_from_directory(self, tmp_path):
        (tmp_path / "svc.py").write_text(
            "def create_user(name):\n    return save(name)\ndef save(name):\n    pass\n"
        )
        config = PipelineConfig(
            enable_cfg=False, enable_dfg=False, enable_endpoints=False,
            embedding_backend="stub", embedding_dimensions=8,
            enable_clustering=False,
        )
        pipeline = CodeCortexPipeline.from_directory(tmp_path, config=config)
        assert pipeline.graph_store is not None
        assert pipeline.graph_store.node_count() > 0

    def test_query_returns_string(self, tmp_path):
        (tmp_path / "mod.py").write_text("def fetch_data(): pass\ndef process(): pass\n")
        config = PipelineConfig(
            enable_cfg=False, enable_dfg=False, enable_endpoints=False,
            embedding_backend="stub", embedding_dimensions=8,
            enable_clustering=False,
        )
        pipeline = CodeCortexPipeline.from_directory(tmp_path, config=config)
        ctx = pipeline.query("fetch data function")
        assert isinstance(ctx, str)

    def test_impact_returns_summary(self, tmp_path):
        (tmp_path / "app.py").write_text(
            "def controller():\n    service()\ndef service():\n    repo()\ndef repo():\n    pass\n"
        )
        config = PipelineConfig(
            enable_cfg=False, enable_dfg=False, enable_endpoints=False,
            embedding_backend="stub", embedding_dimensions=8,
            enable_clustering=False,
        )
        pipeline = CodeCortexPipeline.from_directory(tmp_path, config=config)
        summary = pipeline.impact("repo")
        assert summary.root == "repo"

    def test_top_nodes_returns_list(self, tmp_path):
        (tmp_path / "code.py").write_text(
            "def a(): b()\ndef b(): c()\ndef c(): pass\n"
        )
        config = PipelineConfig(
            enable_cfg=False, enable_dfg=False, enable_endpoints=False,
            embedding_backend="stub", embedding_dimensions=8,
            enable_clustering=False,
        )
        pipeline = CodeCortexPipeline.from_directory(tmp_path, config=config)
        top = pipeline.top_nodes(n=3)
        assert isinstance(top, list)

    def test_reindex_file_runs_without_error(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def alpha(): pass\n")
        config = PipelineConfig(
            enable_cfg=False, enable_dfg=False, enable_endpoints=False,
            embedding_backend="stub", embedding_dimensions=8,
            enable_clustering=False,
        )
        pipeline = CodeCortexPipeline.from_directory(tmp_path, config=config)
        f.write_text("def alpha(): pass\ndef beta(): pass\n")
        pipeline.reindex_file(str(f))  # should not raise
