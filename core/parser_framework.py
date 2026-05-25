"""Parser framework: abstract base class and plugin architecture.

Enables language-specific parsing via plugin system rather than hardcoded dispatch.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .types import EdgeInfo, NodeInfo, ParseResult

logger = logging.getLogger(__name__)


class LanguageParser(ABC):
    """Abstract base class for language-specific code parsers.

    Subclasses implement language-specific extraction of definitions, calls,
    types, and other structural elements.

    Example:
        class PythonParser(LanguageParser):
            language = "python"
            extensions = [".py"]

            def extract_definitions(self, tree, source):
                # Tree-sitter specific extraction
                ...
    """

    # Subclasses must define these
    language: str = ""  # e.g., "python", "javascript"
    extensions: list[str] = []  # e.g., [".py"], [".ts", ".tsx"]

    def __init__(self, language: str = ""):
        """Initialize parser.

        Args:
            language: Override class language if needed.
        """
        if language:
            self.language = language

    @abstractmethod
    def parse(self, source: str, file_path: str) -> ParseResult:
        """Parse source code and extract nodes & edges.

        Args:
            source: Raw source code text
            file_path: Path to source file (for error reporting)

        Returns:
            ParseResult with nodes, edges, errors
        """
        pass

    @abstractmethod
    def extract_definitions(self, tree, source: str) -> list[NodeInfo]:
        """Extract structural definitions (functions, classes, types, etc.)

        Args:
            tree: Language parser tree (e.g., tree-sitter TSTree)
            source: Raw source code

        Returns:
            List of extracted NodeInfo objects
        """
        pass

    @abstractmethod
    def extract_calls(self, tree, source: str) -> list[EdgeInfo]:
        """Extract function/method calls and references.

        Args:
            tree: Language parser tree
            source: Raw source code

        Returns:
            List of CALLS edges
        """
        pass

    @abstractmethod
    def extract_imports(self, tree, source: str) -> list[EdgeInfo]:
        """Extract import/require statements.

        Args:
            tree: Language parser tree
            source: Raw source code

        Returns:
            List of IMPORTS_FROM edges
        """
        pass

    @abstractmethod
    def extract_inheritance(self, tree, source: str) -> list[EdgeInfo]:
        """Extract class inheritance and interface implementation.

        Args:
            tree: Language parser tree
            source: Raw source code

        Returns:
            List of INHERITS and IMPLEMENTS edges
        """
        pass

    def extract_types(self, tree, source: str) -> list[NodeInfo]:
        """Extract type definitions (classes, interfaces, types).

        Default implementation: return empty. Override in language-specific parsers.

        Args:
            tree: Language parser tree
            source: Raw source code

        Returns:
            List of type NodeInfo objects
        """
        return []

    def extract_tests(self, tree, source: str) -> list[NodeInfo]:
        """Extract test functions/methods.

        Default implementation: return empty. Override in language-specific parsers.

        Args:
            tree: Language parser tree
            source: Raw source code

        Returns:
            List of test NodeInfo objects (marked with is_test=True)
        """
        return []

    def supports_file(self, file_path: str) -> bool:
        """Check if parser supports a file by extension.

        Args:
            file_path: Path to file

        Returns:
            True if parser can handle this file
        """
        ext = Path(file_path).suffix.lower()
        return ext in self.extensions

    def validate_config(self, config: dict) -> bool:
        """Validate parser-specific configuration.

        Override in subclass if needed.

        Args:
            config: Parser configuration dict

        Returns:
            True if config is valid
        """
        return True


class ParserRegistry:
    """Registry for language-specific parsers.

    Manages parser discovery, registration, and lookup.
    """

    def __init__(self):
        """Initialize parser registry."""
        self._parsers: dict[str, type[LanguageParser]] = {}
        self._by_extension: dict[str, str] = {}  # ext -> language

    def register(self, parser_class: type[LanguageParser]) -> None:
        """Register a language parser.

        Args:
            parser_class: LanguageParser subclass

        Raises:
            ValueError: If parser_class doesn't define language/extensions
        """
        if not parser_class.language:
            raise ValueError(f"{parser_class.__name__} must define 'language'")
        if not parser_class.extensions:
            raise ValueError(f"{parser_class.__name__} must define 'extensions'")

        language = parser_class.language
        self._parsers[language] = parser_class

        for ext in parser_class.extensions:
            self._by_extension[ext.lower()] = language

        logger.debug(f"Registered parser: {language} for extensions {parser_class.extensions}")

    def get_parser(self, language: str) -> Optional[LanguageParser]:
        """Get parser instance by language name.

        Args:
            language: Language name (e.g., "python")

        Returns:
            Parser instance or None if not registered
        """
        if language not in self._parsers:
            return None
        return self._parsers[language](language)

    def get_parser_by_extension(self, file_path: str) -> Optional[LanguageParser]:
        """Get parser instance by file extension.

        Args:
            file_path: Path to file

        Returns:
            Parser instance or None if extension not recognized
        """
        ext = Path(file_path).suffix.lower()
        if ext not in self._by_extension:
            return None
        language = self._by_extension[ext]
        return self.get_parser(language)

    def supported_languages(self) -> list[str]:
        """Get list of supported languages.

        Returns:
            Sorted list of language names
        """
        return sorted(self._parsers.keys())

    def supported_extensions(self) -> dict[str, str]:
        """Get mapping of extensions to languages.

        Returns:
            Dict mapping file extension to language name
        """
        return dict(self._by_extension)


# Global registry instance
_default_registry = ParserRegistry()


def get_parser_registry() -> ParserRegistry:
    """Get the default global parser registry.

    Returns:
        ParserRegistry instance
    """
    return _default_registry


def register_parser(parser_class: type[LanguageParser]) -> None:
    """Register a parser with the default registry.

    Args:
        parser_class: LanguageParser subclass
    """
    _default_registry.register(parser_class)
