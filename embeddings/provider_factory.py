"""Embedding provider factory.

Consolidates provider initialization behind a single factory function.
Supports local (sentence-transformers), OpenAI-compatible, and stub providers.
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingConfig:
    """Configuration for an embedding provider."""

    provider: str = "local"           # local | openai | stub
    model_name: str = "all-MiniLM-L6-v2"
    api_key: Optional[str] = None
    api_base: Optional[str] = None    # For OpenAI-compatible endpoints
    dimensions: int = 384             # Expected embedding dimension
    batch_size: int = 64
    extra: dict = field(default_factory=dict)


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (same length as texts).
        """

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Embedding vector dimension."""


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers provider (runs locally, no API key needed)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", batch_size: int = 64):
        self._model_name = model_name
        self._batch_size = batch_size
        self._model = None  # Lazy load

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
                logger.info("Loaded sentence-transformers model: %s", self._model_name)
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers is required for LocalEmbeddingProvider. "
                    "Install with: pip install sentence-transformers"
                )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        vectors = model.encode(
            texts,
            batch_size=self._batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,   # L2-normalize for cosine similarity via IP
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vectors]

    @property
    def dimensions(self) -> int:
        return self._get_model().get_sentence_embedding_dimension()


class CachedEmbeddingProvider(EmbeddingProvider):
    """Content-hash cache wrapper around any EmbeddingProvider.

    Avoids re-computing embeddings for identical code snippets (e.g., unchanged
    functions across incremental re-indexing runs).  Uses SHA-256 of the text as
    the cache key — collision probability is negligible for code corpora.
    """

    def __init__(self, inner: EmbeddingProvider, max_size: int = 50_000):
        self._inner = inner
        self._max_size = max_size
        self._cache: dict[str, list[float]] = {}  # sha256 → vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []

        for i, text in enumerate(texts):
            key = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
            if key in self._cache:
                results[i] = self._cache[key]
            else:
                miss_indices.append(i)
                miss_texts.append(text)

        if miss_texts:
            vecs = self._inner.embed(miss_texts)
            for i, (orig_idx, text) in enumerate(zip(miss_indices, miss_texts)):
                vec = vecs[i]
                results[orig_idx] = vec
                if len(self._cache) < self._max_size:
                    key = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
                    self._cache[key] = vec

        return results  # type: ignore[return-value]

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    @property
    def cache_size(self) -> int:
        return len(self._cache)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding provider.

    Works with OpenAI API and any compatible endpoint (Azure, local vLLM, etc.).
    """

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        batch_size: int = 512,
        dimensions: int = 1536,
    ):
        self._model_name = model_name
        self._api_key = api_key
        self._api_base = api_base
        self._batch_size = batch_size
        self._dimensions = dimensions
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                kwargs = {}
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                if self._api_base:
                    kwargs["base_url"] = self._api_base
                self._client = OpenAI(**kwargs)
            except ImportError:
                raise RuntimeError(
                    "openai package is required for OpenAIEmbeddingProvider. "
                    "Install with: pip install openai"
                )
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        results = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            response = client.embeddings.create(
                model=self._model_name,
                input=batch,
            )
            results.extend(item.embedding for item in response.data)
        return results

    @property
    def dimensions(self) -> int:
        return self._dimensions


class StubEmbeddingProvider(EmbeddingProvider):
    """Zero-vector stub provider for tests and CI environments."""

    def __init__(self, dimensions: int = 384):
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dimensions for _ in texts]

    @property
    def dimensions(self) -> int:
        return self._dimensions


def create_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Factory function: create the correct provider from config.

    Args:
        config: EmbeddingConfig specifying provider type and parameters.

    Returns:
        Instantiated EmbeddingProvider.

    Raises:
        ValueError: For unknown provider type.
    """
    if config.provider == "local":
        inner = LocalEmbeddingProvider(
            model_name=config.model_name,
            batch_size=config.batch_size,
        )
        return CachedEmbeddingProvider(inner)
    if config.provider == "openai":
        return OpenAIEmbeddingProvider(
            model_name=config.model_name,
            api_key=config.api_key,
            api_base=config.api_base,
            batch_size=config.batch_size,
            dimensions=config.dimensions,
        )
    if config.provider == "stub":
        return StubEmbeddingProvider(dimensions=config.dimensions)

    raise ValueError(
        f"Unknown embedding provider: {config.provider!r}. "
        "Supported: local, openai, stub"
    )
