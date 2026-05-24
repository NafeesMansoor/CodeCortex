"""Vector embedding layer for CodeCortex — Phase 4."""

from .provider_factory import (
    EmbeddingConfig,
    EmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
    StubEmbeddingProvider,
    create_embedding_provider,
)
from .embedding_store import EmbeddedItem, EmbeddingStore, SearchResult
from .code_embedder import CodeEmbedder
from .embedding_pipeline import EmbeddingPipeline, PipelineStats

__all__ = [
    "EmbeddingConfig",
    "EmbeddingProvider",
    "LocalEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "StubEmbeddingProvider",
    "create_embedding_provider",
    "EmbeddedItem",
    "EmbeddingStore",
    "SearchResult",
    "CodeEmbedder",
    "EmbeddingPipeline",
    "PipelineStats",
]
