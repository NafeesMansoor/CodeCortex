"""__init__ for core module."""

from .exceptions import (
    CodeCortexException,
    ConfigurationException,
    EmbeddingException,
    GraphStoreException,
    IndexingException,
    LanguageNotSupportedException,
    ParserException,
    SymbolResolutionException,
    TraversalException,
)
from .lifecycle import (
    CodeCortexContext,
    TTLCache,
    cleanup_all,
    create_context,
    get_default_context,
    set_default_context,
)
from .parser_framework import (
    LanguageParser,
    ParserRegistry,
    get_parser_registry,
    register_parser,
)
from .types import (
    ConfidenceTier,
    EdgeInfo,
    EdgeKind,
    NodeInfo,
    NodeKind,
    ParseResult,
    Position,
    SourceRange,
    TraversalConfig,
)

__all__ = [
    # Types
    "NodeKind",
    "EdgeKind",
    "NodeInfo",
    "EdgeInfo",
    "Position",
    "SourceRange",
    "ParseResult",
    "TraversalConfig",
    "ConfidenceTier",
    # Parser Framework
    "LanguageParser",
    "ParserRegistry",
    "get_parser_registry",
    "register_parser",
    # Lifecycle
    "CodeCortexContext",
    "TTLCache",
    "create_context",
    "get_default_context",
    "set_default_context",
    "cleanup_all",
    # Exceptions
    "CodeCortexException",
    "ParserException",
    "LanguageNotSupportedException",
    "GraphStoreException",
    "TraversalException",
    "EmbeddingException",
    "IndexingException",
    "SymbolResolutionException",
    "ConfigurationException",
]
