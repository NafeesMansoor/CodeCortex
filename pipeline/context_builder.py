"""End-to-end CodeCortex pipeline: repository → AI-ready context.

Orchestrates all phases in the correct order:
  1. Parse files into ParseResult objects (multi-language)
  2. Build CPG (nodes + edges + CFG/DFG/endpoints)
  3. Index embeddings via EmbeddingPipeline (FAISS)
  4. Cluster semantic groups (HDBSCAN / KMeans fallback)
  5. Compute centrality scores (PageRank, betweenness, closeness, eigenvector)
  6. Build AdjacencyCache for fast traversal
  7. Expose RetrievalLayer for query-time context generation

Usage:
    pipeline = CodeCortexPipeline.from_directory("/path/to/repo")
    context = pipeline.query("authentication middleware", max_tokens=3000)
    impact  = pipeline.impact("UserService.authenticate")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Unified configuration for the full CodeCortex pipeline."""

    # CPG settings
    enable_cfg: bool = True
    enable_dfg: bool = True
    enable_endpoints: bool = True

    # Embedding settings
    embedding_backend: str = "stub"      # "stub" | "local" | "openai"
    embedding_dimensions: int = 384
    embedding_batch_size: int = 256

    # Clustering settings
    enable_clustering: bool = True
    min_cluster_size: int = 5

    # Centrality settings
    semantic_only: bool = True

    # Hybrid on-demand CFG/DFG expansion (Tier 2)
    on_demand_cfg: bool = False
    on_demand_dfg: bool = False

    # V0.2 architectural upgrades
    use_graph_pruning: bool = False      # prune non-semantic nodes after CPG build
    use_louvain: bool = False            # Louvain graph-based clustering
    use_beam_traversal: bool = False     # beam search traversal in retrieval
    use_ppr: bool = False                # personalized PageRank ranking

    # Retrieval settings
    retrieval_top_k: int = 20
    retrieval_token_budget: int = 4000
    retrieval_min_semantic: float = 0.0


@dataclass
class PipelineStats:
    """Aggregate statistics from a full pipeline run."""

    files_parsed: int = 0
    parse_errors: int = 0
    nodes: int = 0
    edges: int = 0
    nodes_embedded: int = 0
    nodes_skipped: int = 0
    clusters: int = 0
    noise_nodes: int = 0
    build_time_s: float = 0.0
    embed_time_s: float = 0.0
    rank_time_s: float = 0.0

    def __str__(self) -> str:
        return (
            f"files={self.files_parsed}  nodes={self.nodes}  edges={self.edges}  "
            f"embedded={self.nodes_embedded}  clusters={self.clusters}  "
            f"build={self.build_time_s:.2f}s  embed={self.embed_time_s:.2f}s  "
            f"rank={self.rank_time_s:.2f}s"
        )


class CodeCortexPipeline:
    """Full CodeCortex analysis pipeline.

    Build once per repository; call query() / impact() on-demand.
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self._store = None
        self._embedding_pipeline = None
        self._retrieval_layer = None
        self._centrality_scores = None
        self._cluster_result = None
        self._adjacency_cache = None
        self._indexed_files: list[Path] = []      # all files seen at build time
        self._expanded_files: set[str] = set()    # files already expanded with CFG/DFG
        self._louvain_result = None               # V0.2 Louvain result

    @classmethod
    def from_directory(
        cls,
        path: str | Path,
        config: Optional[PipelineConfig] = None,
        languages: Optional[list[str]] = None,
    ) -> "CodeCortexPipeline":
        """Build the full pipeline from a source directory."""
        pipeline = cls(config)
        pipeline.build(Path(path), languages=languages)
        return pipeline

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        root: Path,
        languages: Optional[list[str]] = None,
    ) -> PipelineStats:
        """Run all phases over source files under root."""
        stats = PipelineStats()
        t_start = time.perf_counter()

        # Phase 1+2+3: Parse → CPG (CFG/DFG deferred when on_demand is set)
        stats.files_parsed, stats.parse_errors, self._store = self._build_cpg(root, languages)

        # V0.2 Phase 1: Graph pruning — compact semantic graph
        if self.config.use_graph_pruning:
            self._store = self._apply_graph_pruning()

        stats.nodes = self._store.node_count()
        stats.edges = self._store.edge_count()
        stats.build_time_s = time.perf_counter() - t_start

        # Phase 4: Embeddings
        t_embed = time.perf_counter()
        emb_stats = self._build_embeddings()
        stats.nodes_embedded = emb_stats.nodes_embedded
        stats.nodes_skipped = emb_stats.nodes_skipped
        stats.embed_time_s = time.perf_counter() - t_embed

        # Phase 5: Clustering (HDBSCAN or Louvain)
        if self.config.enable_clustering:
            if self.config.use_louvain:
                self._build_louvain_clusters(stats)
            else:
                self._build_clusters(stats)

        # Phase 6: Adjacency cache
        from traversal.traversal_engine import AdjacencyCache
        self._adjacency_cache = AdjacencyCache.build(self._store)

        # Phase 7: Centrality (standard or PPR-ready)
        t_rank = time.perf_counter()
        from ranking.centrality_engine import CentralityEngine
        engine = CentralityEngine(self._store, semantic_only=self.config.semantic_only)
        self._centrality_scores = engine.compute()
        stats.rank_time_s = time.perf_counter() - t_rank

        # Phase 8: Retrieval layer
        cluster_memberships = self._build_cluster_memberships()
        self._build_retrieval_layer(cluster_memberships)

        logger.info("CodeCortex pipeline built: %s", stats)
        return stats

    def _build_cpg(self, root: Path, languages: Optional[list[str]]) -> tuple[int, int, object]:
        from graph.graph_store import GraphStore
        from graph.cpg_builder import CPGBuilder
        from core.parsers.python_parser import PythonParser

        store = GraphStore(":memory:")
        cfg = self.config

        # Tier 1 build: skip CFG/DFG when on-demand expansion is requested
        effective_cfg = cfg.enable_cfg and not cfg.on_demand_cfg
        effective_dfg = cfg.enable_dfg and not cfg.on_demand_dfg

        builder = CPGBuilder(
            store,
            enable_cfg=effective_cfg,
            enable_dfg=effective_dfg,
            enable_endpoints=cfg.enable_endpoints,
        )

        parsers = {"py": PythonParser()}

        try:
            from core.parsers.javascript_parser import JavaScriptParser
            from core.parsers.typescript_parser import TypeScriptParser
            from core.parsers.php_parser import PHPParser
            parsers.update({"js": JavaScriptParser(), "ts": TypeScriptParser(), "php": PHPParser()})
        except Exception:
            pass

        files = 0
        errors = 0
        self._indexed_files = []
        for ext, parser in parsers.items():
            if languages and ext not in languages:
                continue
            for file_path in root.rglob(f"*.{ext}"):
                try:
                    source = file_path.read_text(encoding="utf-8", errors="replace")
                    result = parser.parse(source, str(file_path))
                    builder.ingest(result)
                    self._indexed_files.append(file_path)
                    files += 1
                    if result.errors:
                        errors += 1
                except Exception as e:
                    logger.debug("Error parsing %s: %s", file_path, e)
                    errors += 1

        return files, errors, store

    def _expand_on_demand(self, file_paths: list[str]) -> int:
        """Re-parse specific files with CFG/DFG and merge new edges into the graph.

        Only runs for files not yet expanded. Returns count of new edges added.
        """
        cfg = self.config
        to_expand = [fp for fp in file_paths if fp not in self._expanded_files]
        if not to_expand:
            return 0

        from graph.cpg_builder import CPGBuilder
        from core.parsers.python_parser import PythonParser

        builder = CPGBuilder(
            self._store,
            enable_cfg=cfg.on_demand_cfg,
            enable_dfg=cfg.on_demand_dfg,
            enable_endpoints=False,
        )
        parser = PythonParser()
        before = self._store.edge_count()

        for fp in to_expand:
            path = Path(fp)
            if not path.exists():
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                result = parser.parse(source, fp)
                builder.ingest(result)
                self._expanded_files.add(fp)
            except Exception as e:
                logger.debug("On-demand expand failed for %s: %s", fp, e)

        added = self._store.edge_count() - before
        if added > 0:
            from traversal.traversal_engine import AdjacencyCache
            self._adjacency_cache = AdjacencyCache.build(self._store)
            if self._retrieval_layer is not None:
                self._retrieval_layer._adjacency_cache = self._adjacency_cache
                self._retrieval_layer.invalidate_centrality_cache()
            logger.debug("On-demand expansion: +%d edges for %d files", added, len(to_expand))

        return added

    def _build_embeddings(self):
        from embeddings.provider_factory import EmbeddingConfig
        from embeddings.embedding_pipeline import EmbeddingPipeline

        cfg = self.config
        emb_config = EmbeddingConfig(
            provider=cfg.embedding_backend,
            dimensions=cfg.embedding_dimensions,
        )
        self._embedding_pipeline = EmbeddingPipeline.from_config(self._store, emb_config)
        return self._embedding_pipeline.index_all(batch_size=cfg.embedding_batch_size)

    def _build_clusters(self, stats: PipelineStats) -> None:
        from clustering.semantic_clusters import SemanticClusterer, ClusterConfig
        from clustering.cluster_labeler import ClusterLabeler

        if self._embedding_pipeline is None or self._embedding_pipeline.embedding_store.size() < 5:
            return

        clusterer = SemanticClusterer(
            ClusterConfig(min_cluster_size=self.config.min_cluster_size)
        )
        try:
            self._cluster_result = clusterer.cluster(self._embedding_pipeline.embedding_store)
            ClusterLabeler().label_all(self._cluster_result.clusters)
            stats.clusters = len(self._cluster_result.clusters)
            stats.noise_nodes = len(self._cluster_result.noise_members)
        except Exception as e:
            logger.warning("Clustering failed: %s", e)

    def _apply_graph_pruning(self):
        from graph.graph_pruner import GraphPruner
        pruned, stats = GraphPruner().prune(self._store)
        logger.info("Graph pruned: %s", stats)
        return pruned

    def _build_louvain_clusters(self, stats: PipelineStats) -> None:
        from clustering.louvain_clusterer import LouvainClusterer
        try:
            result = LouvainClusterer().cluster(self._store)
            self._louvain_result = result
            stats.clusters = result.n_clusters
        except Exception as e:
            logger.warning("Louvain clustering failed: %s", e)
            self._louvain_result = None

    def _build_cluster_memberships(self) -> dict[str, int]:
        # Prefer Louvain (graph-based) if available
        if hasattr(self, "_louvain_result") and self._louvain_result is not None:
            return dict(self._louvain_result.node_to_cluster)
        if self._cluster_result is None:
            return {}
        memberships: dict[str, int] = {}
        for cluster in self._cluster_result.clusters:
            for member in cluster.members:
                memberships[member] = cluster.cluster_id
        return memberships

    def _build_retrieval_layer(self, cluster_memberships: dict[str, int]) -> None:
        from retrieval.retrieval_layer import RetrievalLayer, RetrievalConfig

        cfg = self.config
        retrieval_cfg = RetrievalConfig(
            top_k=cfg.retrieval_top_k,
            token_budget=cfg.retrieval_token_budget,
            min_semantic_score=cfg.retrieval_min_semantic,
        )
        self._retrieval_layer = RetrievalLayer(
            self._store,
            self._embedding_pipeline.embedding_store,
            config=retrieval_cfg,
            cluster_memberships=cluster_memberships,
            adjacency_cache=self._adjacency_cache,
        )
        if self._centrality_scores:
            self._retrieval_layer.set_centrality_cache(self._centrality_scores)

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def query(
        self,
        query: str,
        seed_nodes: Optional[list[str]] = None,
        intent: Optional[str] = None,
        max_tokens: int = 3000,
    ) -> str:
        """Return AI-ready context string for a natural language query."""
        if self._retrieval_layer is None:
            raise RuntimeError("Pipeline not built. Call build() or from_directory() first.")
        if self.config.on_demand_cfg or self.config.on_demand_dfg:
            self._trigger_on_demand_expansion(query, seed_nodes, intent)
        return self._retrieval_layer.generate_context(
            query, seed_nodes=seed_nodes, intent=intent, max_tokens=max_tokens
        )

    def retrieve(
        self,
        query: str,
        seed_nodes: Optional[list[str]] = None,
        intent: Optional[str] = None,
    ):
        """Return ranked RetrievalResult list."""
        if self._retrieval_layer is None:
            raise RuntimeError("Pipeline not built.")
        if self.config.on_demand_cfg or self.config.on_demand_dfg:
            self._trigger_on_demand_expansion(query, seed_nodes, intent)
        return self._retrieval_layer.retrieve(query, seed_nodes=seed_nodes, intent=intent)

    def _trigger_on_demand_expansion(
        self,
        query: str,
        seed_nodes: Optional[list[str]],
        intent: Optional[str],
    ) -> None:
        """Run a lightweight retrieval pass to identify relevant files, then expand."""
        initial = self._retrieval_layer.retrieve(query, seed_nodes=seed_nodes, intent=intent)
        files = list({r.file_path for r in initial if r.file_path})
        if files:
            self._expand_on_demand(files)

    def impact(self, qualified_name: str, max_depth: int = 4):
        """Return an ImpactSummary for a changed node."""
        if self._retrieval_layer is None:
            raise RuntimeError("Pipeline not built.")
        return self._retrieval_layer.impact_summary(qualified_name, max_depth=max_depth)

    def top_nodes(self, n: int = 20):
        """Return top-n nodes by composite centrality score."""
        if self._centrality_scores is None:
            return []
        from ranking.centrality_engine import CentralityEngine
        return CentralityEngine.top_nodes(self._centrality_scores, n=n)

    # ------------------------------------------------------------------
    # Incremental update
    # ------------------------------------------------------------------

    def reindex_file(self, file_path: str) -> None:
        """Re-parse and re-embed a single changed file."""
        if self._store is None or self._embedding_pipeline is None:
            return

        from core.parsers.python_parser import PythonParser
        from graph.cpg_builder import CPGBuilder

        path = Path(file_path)
        if not path.exists():
            return

        parser = PythonParser()
        cfg = self.config
        builder = CPGBuilder(
            self._store,
            enable_cfg=cfg.enable_cfg,
            enable_dfg=cfg.enable_dfg,
            enable_endpoints=cfg.enable_endpoints,
        )
        source = path.read_text(encoding="utf-8", errors="replace")
        result = parser.parse(source, file_path)
        builder.ingest(result)

        self._embedding_pipeline.index_file(file_path)

        # Rebuild adjacency cache and invalidate centrality
        from traversal.traversal_engine import AdjacencyCache
        self._adjacency_cache = AdjacencyCache.build(self._store)
        if self._retrieval_layer:
            self._retrieval_layer.invalidate_centrality_cache()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def graph_store(self):
        return self._store

    @property
    def embedding_store(self):
        return self._embedding_pipeline.embedding_store if self._embedding_pipeline else None
