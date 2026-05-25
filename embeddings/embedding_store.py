"""Vector embedding store with FAISS backend and in-memory fallback.

Stores per-node embeddings and provides similarity search with optional
metadata filtering. Supports incremental updates and re-indexing.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from embeddings.provider_factory import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass
class EmbeddedItem:
    """An embedded code entity."""

    qualified_name: str
    text: str  # Source text that was embedded
    vector: list[float]
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result from similarity search."""

    qualified_name: str
    score: float  # Cosine similarity [0, 1]
    metadata: dict = field(default_factory=dict)


class EmbeddingStore:
    """Vector store for code embeddings.

    Uses FAISS when available for fast approximate nearest-neighbor search.
    Falls back to exact cosine similarity for small collections (< 10k items).

    Usage:
        provider = StubEmbeddingProvider()
        store = EmbeddingStore(provider)
        store.add("my.func", "def my_func(): pass")
        results = store.search("utility function", top_k=5)
    """

    def __init__(self, provider: EmbeddingProvider, use_faiss: bool = True):
        self.provider = provider
        self._use_faiss = use_faiss
        self._items: dict[str, EmbeddedItem] = {}  # qualified_name → item
        self._index = None  # FAISS index (lazy init)
        self._index_dirty = False  # Index needs rebuild

    def add(
        self,
        qualified_name: str,
        text: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Embed text and store it under qualified_name."""
        vector = self.provider.embed_one(text)
        item = EmbeddedItem(
            qualified_name=qualified_name,
            text=text,
            vector=vector,
            metadata=metadata or {},
        )
        self._items[qualified_name] = item
        self._index_dirty = True

    def add_batch(
        self,
        names: list[str],
        texts: list[str],
        metadata: Optional[list[dict]] = None,
    ) -> None:
        """Embed and store a batch of (name, text) pairs.

        Args:
            names: Qualified node names.
            texts: Corresponding embedding texts.
            metadata: Optional per-item metadata dicts.
        """
        if not names:
            return
        metas = metadata or [{} for _ in names]
        vectors = self.provider.embed(texts)
        for name, text, meta, vector in zip(names, texts, metas, vectors):
            self._items[name] = EmbeddedItem(
                qualified_name=name,
                text=text,
                vector=vector,
                metadata=meta,
            )
        self._index_dirty = True

    def remove(self, qualified_name: str) -> bool:
        """Remove an item from the store. Returns True if removed."""
        if qualified_name in self._items:
            del self._items[qualified_name]
            self._index_dirty = True
            return True
        return False

    def search(
        self,
        query: str,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> list[SearchResult]:
        """Find most similar items to query text.

        Args:
            query: Natural language or code query.
            top_k: Number of results to return.
            threshold: Minimum cosine similarity score (0-1).

        Returns:
            List of SearchResult ordered by descending similarity.
        """
        if not self._items:
            return []

        query_vec = self.provider.embed_one(query)

        if self._use_faiss and self._try_faiss_search(query_vec, top_k, threshold) is not None:
            return self._try_faiss_search(query_vec, top_k, threshold)

        return self._exact_search(query_vec, top_k, threshold)

    def _exact_search(
        self,
        query_vec: list[float],
        top_k: int,
        threshold: float,
    ) -> list[SearchResult]:
        scores = []
        for item in self._items.values():
            score = _cosine_similarity(query_vec, item.vector)
            if score >= threshold:
                scores.append((score, item.qualified_name, item.metadata))

        scores.sort(reverse=True)
        return [
            SearchResult(qualified_name=name, score=score, metadata=meta)
            for score, name, meta in scores[:top_k]
        ]

    def _try_faiss_search(
        self,
        query_vec: list[float],
        top_k: int,
        threshold: float,
    ) -> Optional[list[SearchResult]]:
        try:
            import faiss
            import numpy as np
        except ImportError:
            return None

        if self._index is None or self._index_dirty:
            self._rebuild_faiss_index(faiss, np)

        if self._index is None:
            return None

        q = np.array([query_vec], dtype="float32")
        faiss.normalize_L2(q)
        k = min(top_k, len(self._items))
        distances, indices = self._index.search(q, k)

        # Use stored names list (supports both flat and HNSW positional indexing)
        names = getattr(self, "_index_names", list(self._items.keys()))
        index_type = getattr(self, "_index_type", "flat")

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(names):
                continue
            # Convert distance to cosine similarity [0, 1]
            if index_type == "hnsw":
                # HNSW returns squared L2; for L2-normalized vectors: cos = 1 - dist/2
                score = max(0.0, 1.0 - float(dist) / 2.0)
            else:
                # IndexFlatIP returns inner product = cosine for normalized vectors
                score = float(dist)
            if score < threshold:
                continue
            name = names[idx]
            if name not in self._items:
                continue
            results.append(
                SearchResult(
                    qualified_name=name,
                    score=score,
                    metadata=self._items[name].metadata,
                )
            )
        return results

    def _rebuild_faiss_index(self, faiss, np) -> None:
        items = list(self._items.values())
        if not items:
            return
        dim = len(items[0].vector)
        vectors = np.array([item.vector for item in items], dtype="float32")
        faiss.normalize_L2(vectors)

        n = len(items)
        if n >= 100:
            # HNSW: O(log n) ANN search, sub-5ms for collections up to ~1M
            M = 32  # graph connectivity — higher = better recall, more memory
            index = faiss.IndexHNSWFlat(dim, M)
            index.hnsw.efConstruction = 200  # quality of graph build
            index.hnsw.efSearch = 64  # quality of search (trade recall vs speed)
            index.add(vectors)
            self._index_type = "hnsw"
        else:
            # Flat exact search for small collections (always 100% recall)
            index = faiss.IndexFlatIP(dim)
            index.add(vectors)
            self._index_type = "flat"

        # HNSW uses L2 metric; store names list for position→name mapping
        self._index_names = [item.qualified_name for item in items]
        self._index = index
        self._index_dirty = False
        logger.debug(
            "Rebuilt FAISS %s index: %d vectors (dim=%d)",
            getattr(self, "_index_type", "?"),
            n,
            dim,
        )

    def size(self) -> int:
        return len(self._items)

    def get(self, qualified_name: str) -> Optional[EmbeddedItem]:
        return self._items.get(qualified_name)

    def clear(self) -> None:
        self._items.clear()
        self._index = None
        self._index_dirty = False


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
