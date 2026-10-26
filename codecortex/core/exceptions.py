"""Custom exceptions for CodeCortex."""

from __future__ import annotations


class CodeCortexException(Exception):
    """Base exception for all CodeCortex errors."""

    pass


class ParserException(CodeCortexException):
    """Raised when code parsing fails."""

    def __init__(self, language: str, file_path: str, message: str, line: int = 0):
        self.language = language
        self.file_path = file_path
        self.line = line
        super().__init__(f"Parse error in {file_path}:{line} ({language}): {message}")


class LanguageNotSupportedException(CodeCortexException):
    """Raised when language is not supported."""

    def __init__(self, language: str, supported: list[str]):
        self.language = language
        self.supported = supported
        super().__init__(f"Language '{language}' not supported. Supported: {', '.join(supported)}")


class GraphStoreException(CodeCortexException):
    """Raised when graph store operation fails."""

    pass


class TraversalException(CodeCortexException):
    """Raised when graph traversal fails."""

    pass


class EmbeddingException(CodeCortexException):
    """Raised when embedding operation fails."""

    pass


class IndexingException(CodeCortexException):
    """Raised when indexing operation fails."""

    pass


class SymbolResolutionException(CodeCortexException):
    """Raised when symbol resolution fails."""

    pass


class ConfigurationException(CodeCortexException):
    """Raised when configuration is invalid."""

    pass
