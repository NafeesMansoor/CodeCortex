"""End-to-end embedding pipeline: CPG → text → FAISS index.

Orchestrates:
  1. CodeEmbedder — generates rich text for each CPGNode
  2. EmbeddingProvider — converts text to vectors
  3. EmbeddingStore — stores vectors in FAISS or cosine fallback

Supports:
  - Full re-indexing
  - Incremental updates (single file or node list)
  - Similarity threshold filtering
  - Serialise / deserialise FAISS index to disk

Usage:
    pipeline = EmbeddingPipeline.from_config(store, EmbeddingConfig(backend="stub"))
    pipeline.index_all()
    results = pipeline.search("user authentication", top_k=10, threshold=0.3)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from embeddings.code_embedder import CodeEmbedder
from embeddings.embedding_store import EmbeddingStore, SearchResult
from embeddings.provider_factory import EmbeddingConfig, create_embedding_provider
from graph.graph_store import GraphStore

logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    nodes_embedded: int = 0
    nodes_skipped: int = 0
    embed_time_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"embedded={self.nodes_embedded}  "
            f"skipped={self.nodes_skipped}  "
            f"time={self.embed_time_s:.3f}s  "
            f"errors={len(self.errors)}"
        )


class EmbeddingPipeline:
    """Connects GraphStore → CodeEmbedder → EmbeddingStore.

    The pipeline filters out non-semantic node kinds (VARIABLE, PARAMETER,
    CALLSITE) before embedding so that retrieval focuses on architectural
    entities.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        embedding_store: EmbeddingStore,
        embedder: Optional[CodeEmbedder] = None,
    ):
        self.graph_store = graph_store
        self.embedding_store = embedding_store
        self.embedder = embedder or CodeEmbedder()

    @classmethod
    def from_config(
        cls,
        graph_store: GraphStore,
        config: EmbeddingConfig,
        dimension: int = 384,
    ) -> "EmbeddingPipeline":
        provider = create_embedding_provider(config)
        store = EmbeddingStore(provider)
        return cls(graph_store, store)

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_all(self, batch_size: int = 256) -> PipelineStats:
        """Embed all embeddable nodes in the GraphStore."""
        stats = PipelineStats()
        t0 = time.perf_counter()

        all_nodes = self.graph_store.all_nodes()
        names, texts = self.embedder.embed_texts_for_store(all_nodes)

        stats.nodes_skipped = len(all_nodes) - len(names)

        # Batch embed
        for i in range(0, len(names), batch_size):
            batch_names = names[i:i + batch_size]
            batch_texts = texts[i:i + batch_size]
            try:
                self.embedding_store.add_batch(batch_names, batch_texts)
                stats.nodes_embedded += len(batch_names)
            except Exception as e:
                logger.warning("Batch embed %d-%d failed: %s", i, i + batch_size, e)
                stats.errors.append(str(e))

        stats.embed_time_s = time.perf_counter() - t0
        logger.info("Embedding pipeline complete: %s", stats)
        return stats

    def index_file(self, file_path: str) -> PipelineStats:
        """Re-embed all nodes from a specific file (for incremental updates)."""
        stats = PipelineStats()
        t0 = time.perf_counter()

        nodes = self.graph_store.get_nodes_by_file(file_path)
        names, texts = self.embedder.embed_texts_for_store(nodes)
        stats.nodes_skipped = len(nodes) - len(names)

        # Remove stale embeddings for this file first
        for node in nodes:
            self.embedding_store.remove(node.qualified_name)

        try:
            self.embedding_store.add_batch(names, texts)
            stats.nodes_embedded = len(names)
        except Exception as e:
            stats.errors.append(str(e))

        stats.embed_time_s = time.perf_counter() - t0
        return stats

    def index_nodes(self, qualified_names: list[str]) -> PipelineStats:
        """Re-embed specific nodes by qualified name."""
        stats = PipelineStats()
        t0 = time.perf_counter()

        nodes = [
            self.graph_store.get_node(qn)
            for qn in qualified_names
        ]
        nodes = [n for n in nodes if n is not None]
        names, texts = self.embedder.embed_texts_for_store(nodes)

        for n in names:
            self.embedding_store.remove(n)

        try:
            self.embedding_store.add_batch(names, texts)
            stats.nodes_embedded = len(names)
        except Exception as e:
            stats.errors.append(str(e))

        stats.embed_time_s = time.perf_counter() - t0
        return stats

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> list[SearchResult]:
        """Semantic search over embedded nodes.

        Args:
            query: Natural language or symbol query.
            top_k: Maximum number of results.
            threshold: Minimum cosine similarity (0-1). Results below this
                       score are filtered out.

        Returns:
            Ranked list of SearchResult objects.
        """
        results = self.embedding_store.search(query, top_k=top_k)
        if threshold > 0.0:
            results = [r for r in results if r.score >= threshold]
        return results

    def search_with_nodes(
        self,
        query: str,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> list[tuple[SearchResult, object]]:
        """Like search(), but also returns the CPGNode for each result."""
        results = self.search(query, top_k=top_k, threshold=threshold)
        paired = []
        for r in results:
            node = self.graph_store.get_node(r.qualified_name)
            if node is not None:
                paired.append((r, node))
        return paired

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_index(self, path: Path) -> None:
        """Persist the FAISS index to disk."""
        try:
            import faiss
            import pickle

            faiss_idx = getattr(self.embedding_store, "_faiss_index", None)
            if faiss_idx is not None:
                faiss.write_index(faiss_idx, str(path.with_suffix(".faiss")))
                # Also save the name/text mapping
                meta = {
                    "items": {
                        name: {"text": item.text, "metadata": item.metadata}
                        for name, item in self.embedding_store._items.items()
                    }
                }
                with open(path.with_suffix(".meta"), "wb") as f:
                    pickle.dump(meta, f)
                logger.info("FAISS index saved to %s", path)
        except Exception as e:
            logger.warning("save_index failed: %s", e)

    def load_index(self, path: Path) -> bool:
        """Load a previously saved FAISS index. Returns True on success."""
        try:
            import faiss
            import pickle

            faiss_path = path.with_suffix(".faiss")
            meta_path = path.with_suffix(".meta")
            if not faiss_path.exists():
                return False

            idx = faiss.read_index(str(faiss_path))
            self.embedding_store._faiss_index = idx

            if meta_path.exists():
                with open(meta_path, "rb") as f:
                    meta = pickle.load(f)
                from embeddings.embedding_store import EmbeddedItem
                for name, data in meta["items"].items():
                    self.embedding_store._items[name] = EmbeddedItem(
                        qualified_name=name,
                        text=data["text"],
                        vector=[],
                        metadata=data.get("metadata", {}),
                    )
            return True
        except Exception as e:
            logger.warning("load_index failed: %s", e)
            return False
