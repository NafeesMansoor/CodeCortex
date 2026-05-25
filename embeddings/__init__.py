"""Vector embedding layer for CodeCortex — Phase 4."""

from .code_embedder import CodeEmbedder
from .embedding_pipeline import EmbeddingPipeline, PipelineStats
from .embedding_store import EmbeddedItem, EmbeddingStore, SearchResult
from .provider_factory import (
    EmbeddingConfig,
    EmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
    StubEmbeddingProvider,
    create_embedding_provider,
)

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
