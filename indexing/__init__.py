"""Semantic indexing layer for CodeCortex — Phase 2."""

from .language_config import (
    LanguageConfig,
    LanguageConfigRegistry,
    get_language_config,
    get_language_config_registry,
)
from .semantic_index import SemanticIndex, SymbolInfo
from .symbol_resolver import SymbolResolver
from .indexing_provider import (
    IndexingProvider,
    IndexResult,
    SymbolDefinition,
    SymbolOccurrence,
    ProviderRegistry,
)
from .jedi_provider import JediProvider
from .lsp_client import LSPClient
from .lsp_provider import LSPProvider
from .scip_provider import SCIPProvider, SCIPIndex
from .reference_graph import ReferenceGraph
from .symbol_cache import SymbolCache
from .graph_enricher import GraphEnricher, EnrichmentStats

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
