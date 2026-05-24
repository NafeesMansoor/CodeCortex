"""Jedi-based semantic indexing provider for Python.

Uses the jedi library to resolve cross-file call targets, references, and
type-inferred definitions — producing higher-fidelity edges than tree-sitter
alone can provide.

Priority: 1 (highest — runs before LSP and tree-sitter fallback)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from core.types import NodeKind
from indexing.indexing_provider import (
    IndexResult,
    IndexingProvider,
    SymbolDefinition,
    SymbolOccurrence,
)

logger = logging.getLogger(__name__)

_JEDI_KIND_MAP: dict[str, NodeKind] = {
    "class":     NodeKind.CLASS,
    "function":  NodeKind.FUNCTION,
    "module":    NodeKind.MODULE,
    "instance":  NodeKind.VARIABLE,
    "statement": NodeKind.VARIABLE,
    "param":     NodeKind.VARIABLE,
    "keyword":   NodeKind.VARIABLE,
}


class JediProvider(IndexingProvider):
    """Python semantic indexing using the jedi static analysis library.

    Extracts:
    - All symbol definitions (functions, classes, imports) with qualified names
    - Cross-file call/reference occurrences resolved to their definition sites

    Usage:
        provider = JediProvider()
        result = provider.index_file(Path("src/foo.py"), Path("src"))
    """

    priority = 1
    name = "jedi"

    def supports(self, language: str) -> bool:
        return language == "python"

    def is_available(self) -> bool:
        try:
            import jedi  # noqa: F401
            return True
        except ImportError:
            return False

    def index_file(self, file_path: Path, project_root: Path) -> IndexResult:
        try:
            import jedi
        except ImportError:
            return IndexResult(errors=["jedi not installed"], source=self.name)

        result = IndexResult(language="python", source=self.name)
        try:
            source = file_path.read_text(errors="replace")
            project = jedi.Project(path=str(project_root))
            script = jedi.Script(source, path=str(file_path), project=project)

            result.definitions.extend(self._extract_definitions(script, file_path))
            result.occurrences.extend(self._extract_occurrences(script, source, file_path))
        except Exception as e:
            logger.warning("jedi index_file %s: %s", file_path, e)
            result.errors.append(str(e))

        return result

    def index_project(self, root: Path, language: str) -> IndexResult:
        """Index a whole Python project in one pass using jedi.Script per file."""
        combined = IndexResult(language="python", source=self.name)
        py_files = list(root.rglob("*.py"))
        for f in py_files:
            r = self.index_file(f, root)
            combined.definitions.extend(r.definitions)
            combined.occurrences.extend(r.occurrences)
            combined.errors.extend(r.errors)
        logger.debug("jedi indexed %d files → %d defs, %d occurrences",
                     len(py_files), len(combined.definitions), len(combined.occurrences))
        return combined

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_definitions(
        self, script, file_path: Path
    ) -> list[SymbolDefinition]:
        defs: list[SymbolDefinition] = []
        try:
            names = script.get_names(all_scopes=True, definitions=True, references=False)
        except Exception:
            return defs

        for name in names:
            if name.type in ("keyword", "param"):
                continue
            try:
                kind = _JEDI_KIND_MAP.get(name.type, NodeKind.VARIABLE)
                qname = _qualified_name(name)
                if not qname:
                    continue
                defs.append(SymbolDefinition(
                    qualified_name=qname,
                    kind=kind,
                    file_path=str(file_path),
                    line=name.line or 0,
                    column=name.column or 0,
                    language="python",
                    module_path=_module_from_path(file_path),
                    display_name=name.name,
                ))
            except Exception:
                continue

        return defs

    def _extract_occurrences(
        self, script, source: str, file_path: Path
    ) -> list[SymbolOccurrence]:
        """Walk every call expression and resolve its definition via jedi.goto."""
        occurrences: list[SymbolOccurrence] = []
        lines = source.splitlines()

        for lineno, line in enumerate(lines, 1):
            # Find call sites: any identifier followed by '('
            col = 0
            while col < len(line):
                idx = line.find("(", col)
                if idx == -1:
                    break
                # Walk back to start of identifier
                start = idx - 1
                while start >= 0 and (line[start].isalnum() or line[start] in "_"):
                    start -= 1
                start += 1
                ident = line[start:idx]
                if not ident or not ident[0].isalpha() and ident[0] != "_":
                    col = idx + 1
                    continue

                try:
                    definitions = script.goto(lineno, start)
                    for d in definitions:
                        if d.module_path and d.full_name:
                            occurrences.append(SymbolOccurrence(
                                symbol=d.full_name,
                                file_path=str(file_path),
                                line=lineno,
                                column=start,
                                role="reference",
                            ))
                            break
                except Exception:
                    pass

                col = idx + 1

        return occurrences


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qualified_name(name) -> Optional[str]:
    """Build a qualified name from a jedi Name object."""
    try:
        full = name.full_name
        if full:
            return full
        # Fallback: module.name
        mod = name.module_name or ""
        return f"{mod}.{name.name}" if mod else name.name
    except Exception:
        return name.name if name.name else None


def _module_from_path(file_path: Path) -> str:
    """Convert a file path to a dotted module name."""
    parts = list(file_path.with_suffix("").parts)
    # Strip common root prefixes
    for i, part in enumerate(parts):
        if part in ("src", "lib", "app"):
            parts = parts[i:]
            break
    return ".".join(parts)
