"""Indexing provider hierarchy: SCIP > LSP > Tree-sitter.

Each provider produces a normalised IndexResult that the GraphEnricher
uses to upgrade tree-sitter-only CPG edges with semantically resolved
cross-file targets.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.types import NodeKind

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SymbolDefinition:
    """A symbol defined somewhere in the repository."""
    qualified_name: str
    kind: NodeKind
    file_path: str
    line: int
    column: int = 0
    language: str = ""
    module_path: str = ""   # dotted module name, e.g. "pkg.sub.module"
    display_name: str = ""  # short unqualified name


@dataclass
class SymbolOccurrence:
    """A use (reference or definition) of a symbol at a source location."""
    symbol: str         # qualified name of the referenced symbol
    file_path: str
    line: int
    column: int
    role: str = "reference"   # "definition" | "reference" | "write" | "read"


@dataclass
class IndexResult:
    """Normalised output from any indexing provider."""
    definitions: list[SymbolDefinition] = field(default_factory=list)
    occurrences: list[SymbolOccurrence] = field(default_factory=list)
    language: str = ""
    source: str = ""    # "scip" | "lsp" | "jedi" | "tree-sitter"
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class IndexingProvider(ABC):
    """Base class for all semantic indexing backends."""

    #: Lower = higher priority (SCIP=1, LSP=2, tree-sitter=99)
    priority: int = 50

    #: Human-readable name for logging / diagnostics
    name: str = "unknown"

    @abstractmethod
    def supports(self, language: str) -> bool:
        """Return True if this provider can handle the given language."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the underlying tool / library is installed."""

    @abstractmethod
    def index_file(self, file_path: Path, project_root: Path) -> IndexResult:
        """Index a single file and return resolved symbols + occurrences."""

    def index_project(self, root: Path, language: str) -> IndexResult:
        """Index all files of the given language under root.

        Default: iterate and merge per-file results. Providers that have a
        project-wide index command (scip-python, pyright) should override.
        """
        extensions = _LANGUAGE_EXTENSIONS.get(language, [])
        combined = IndexResult(language=language, source=self.name)
        for ext in extensions:
            for path in root.rglob(f"*{ext}"):
                result = self.index_file(path, root)
                combined.definitions.extend(result.definitions)
                combined.occurrences.extend(result.occurrences)
                combined.errors.extend(result.errors)
        return combined


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """Maintains a priority-ordered list of IndexingProviders.

    Usage:
        registry = ProviderRegistry()
        registry.register(JediProvider())
        registry.register(LSPProvider("pyright"))
        provider = registry.best_for("python")
    """

    def __init__(self):
        self._providers: list[IndexingProvider] = []

    def register(self, provider: IndexingProvider) -> None:
        self._providers.append(provider)
        self._providers.sort(key=lambda p: p.priority)

    def best_for(self, language: str) -> Optional[IndexingProvider]:
        """Return the highest-priority available provider for language."""
        for p in self._providers:
            if p.supports(language) and p.is_available():
                return p
        return None

    def all_for(self, language: str) -> list[IndexingProvider]:
        """Return all available providers for language, best-first."""
        return [p for p in self._providers if p.supports(language) and p.is_available()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python":     [".py"],
    "javascript": [".js", ".jsx", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "php":        [".php"],
}
