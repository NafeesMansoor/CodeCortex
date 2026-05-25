"""Semantic indexing layer for CodeCortex — Phase 2."""

from .graph_enricher import EnrichmentStats, GraphEnricher
from .indexing_provider import (
    IndexingProvider,
    IndexResult,
    ProviderRegistry,
    SymbolDefinition,
    SymbolOccurrence,
)
from .jedi_provider import JediProvider
from .language_config import (
    LanguageConfig,
    LanguageConfigRegistry,
    get_language_config,
    get_language_config_registry,
)
from .lsp_client import LSPClient
from .lsp_provider import LSPProvider
from .reference_graph import ReferenceGraph
from .scip_provider import SCIPIndex, SCIPProvider
from .semantic_index import SemanticIndex, SymbolInfo
from .symbol_cache import SymbolCache
from .symbol_resolver import SymbolResolver

__all__ = [
    # Phase 1
    "LanguageConfig",
    "LanguageConfigRegistry",
    "get_language_config",
    "get_language_config_registry",
    "SemanticIndex",
    "SymbolInfo",
    "SymbolResolver",
    # Phase 2
    "IndexingProvider",
    "IndexResult",
    "SymbolDefinition",
    "SymbolOccurrence",
    "ProviderRegistry",
    "JediProvider",
    "LSPClient",
    "LSPProvider",
    "SCIPProvider",
    "SCIPIndex",
    "ReferenceGraph",
    "SymbolCache",
    "GraphEnricher",
    "EnrichmentStats",
]
