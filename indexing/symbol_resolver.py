"""Symbol resolver: cross-file reference resolution.

Resolves short names to qualified names.  Resolution order:
  1. SymbolCache (pre-built from jedi/LSP/SCIP provider) — O(1)
  2. SemanticIndex (in-memory CPG view) — graph scan
  3. Heuristic same-file / import locality

Usage:
    resolver = SymbolResolver(semantic_index)
    qname = resolver.resolve("bar", context_file="src/utils.py")

    # With symbol cache (Phase 2):
    cache = SymbolCache(...)
    resolver = SymbolResolver(semantic_index, symbol_cache=cache)
"""

from __future__ import annotations

import logging
from typing import Optional

from indexing.semantic_index import SemanticIndex, SymbolInfo

logger = logging.getLogger(__name__)


class SymbolResolver:
    """Resolves ambiguous symbol names to qualified names.

    Uses the symbol cache when available (populated by GraphEnricher),
    falling back to the SemanticIndex heuristics for symbols not yet cached.

    Usage:
        resolver = SymbolResolver(semantic_index)
        qname = resolver.resolve("bar", context_file="src/utils.py")
    """

    def __init__(self, index: SemanticIndex, symbol_cache=None):
        self.index = index
        self._cache = symbol_cache  # Optional[SymbolCache]

    def resolve(
        self,
        name: str,
        context_file: Optional[str] = None,
        prefer_kind: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve a short name to a qualified name.

        Strategy (priority order):
        1. Exact qualified-name match in CPG.
        2. SymbolCache lookup (jedi/LSP/SCIP resolved — highest fidelity).
        3. Symbol defined in the same file as context_file.
        4. Symbol imported by context_file's module.
        5. First match by name across entire index.

        Args:
            name: Short or partially qualified name.
            context_file: File where the reference originates (for locality preference).
            prefer_kind: Prefer symbols of this NodeKind string (e.g., "Function").

        Returns:
            Qualified name string, or None if not resolved.
        """
        # 1. Already qualified
        if self.index.lookup(name) is not None:
            return name

        # 2. Symbol cache (Phase 2 — provider-resolved)
        if self._cache is not None:
            cache_hits = self._cache.search(name, exact=True)
            if len(cache_hits) == 1:
                return cache_hits[0].qualified_name
            if cache_hits and context_file:
                same_file = [h for h in cache_hits if h.file_path == context_file]
                if same_file:
                    return same_file[0].qualified_name
            if cache_hits:
                return cache_hits[0].qualified_name

        candidates = self.index.find_by_name(name)
        if not candidates:
            return None

        if prefer_kind:
            preferred = [c for c in candidates if c.kind.value == prefer_kind]
            if preferred:
                candidates = preferred

        # 2. Prefer same file
        if context_file:
            local = [c for c in candidates if c.file_path == context_file]
            if local:
                return local[0].qualified_name

        # 3. Prefer symbols imported by context file
        if context_file:
            imports = self.index.imports_of(context_file)
            for candidate in candidates:
                module = _module_of(candidate.file_path)
                if module in imports:
                    return candidate.qualified_name

        # 4. First match
        return candidates[0].qualified_name

    def resolve_import(self, module_name: str) -> list[str]:
        """Return all symbols exported by a module (file).

        Looks for file nodes whose path ends with the module name pattern.
        """
        results = []
        for file_path in self.index.all_files():
            if _module_of(file_path) == module_name or file_path.endswith(module_name):
                symbols = self.index.symbols_in_file(file_path)
                results.extend(s.qualified_name for s in symbols)
        return results

    def resolve_all_calls(self) -> dict[str, Optional[str]]:
        """Attempt to resolve every unresolved call target in the index.

        Returns a map of (unresolved_name → resolved_qualified_name | None).
        Useful for post-processing the graph after initial ingestion.
        """
        from core.types import EdgeKind
        unresolved: dict[str, Optional[str]] = {}

        for edge in self.index.store.all_edges():
            if edge.kind != EdgeKind.CALLS:
                continue
            target = edge.target
            if self.index.lookup(target) is None:
                if target not in unresolved:
                    context_file = self.index.store.get_node(edge.source)
                    ctx_file = context_file.file_path if context_file else None
                    unresolved[target] = self.resolve(target, context_file=ctx_file)

        return unresolved


def _module_of(file_path: str) -> str:
    """Convert a file path to a module-style dotted name.

    Examples:
        "src/utils/helpers.py" → "src.utils.helpers"
        "app/Http/Controllers/UserController.php" → "app.Http.Controllers.UserController"
    """
    import re
    path = file_path.replace("\\", "/")
    path = re.sub(r"\.(py|php|js|ts|tsx|jsx)$", "", path)
    return path.replace("/", ".")
