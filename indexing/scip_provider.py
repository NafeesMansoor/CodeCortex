"""SCIP (Sourcegraph Code Intelligence Protocol) index reader.

Reads a SCIP JSON dump (produced by: scip convert --from index.scip --to index.json)
and converts it to CodeCortex IndexResult format.

SCIP symbol string format:
  <scheme> <manager> <package> <descriptor>+
  e.g. "python-stdlib 3.11 os/path . `abspath`()."

Priority: 1 (equal to jedi — chosen over jedi when a .scip file is present)

Usage:
    provider = SCIPProvider()
    if provider.is_available():  # True when scip CLI is on PATH
        result = provider.index_project(root, language="python")

    # Or load a pre-generated index directly:
    result = provider.load_index(Path("index.json"), root)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.types import NodeKind
from indexing.indexing_provider import (
    IndexingProvider,
    IndexResult,
    SymbolDefinition,
    SymbolOccurrence,
)

logger = logging.getLogger(__name__)

# SCIP symbol role bitmask values
_ROLE_DEFINITION = 1
_ROLE_REFERENCE = 2
_ROLE_WRITE = 8

# SCIP SymbolInformation.kind values (subset)
_SCIP_KIND_MAP: dict[int, NodeKind] = {
    1: NodeKind.MODULE,  # Package
    2: NodeKind.MODULE,  # Namespace
    3: NodeKind.MODULE,  # Module
    4: NodeKind.CLASS,  # Class
    5: NodeKind.CLASS,  # Interface
    6: NodeKind.FUNCTION,  # Method
    7: NodeKind.VARIABLE,  # Property
    8: NodeKind.ENUM,  # Enum
    9: NodeKind.VARIABLE,  # EnumMember
    10: NodeKind.FUNCTION,  # Function
    11: NodeKind.VARIABLE,  # Variable
    12: NodeKind.CONSTANT,  # Constant
    13: NodeKind.STRUCT,  # Struct
    14: NodeKind.TYPE,  # TypeParameter
    15: NodeKind.VARIABLE,  # Parameter
    16: NodeKind.NAMESPACE,  # Namespace (duplicate key — harmless)
}


@dataclass
class SCIPIndex:
    """In-memory representation of a parsed SCIP JSON index."""

    metadata: dict
    documents: list[dict]
    external_symbols: list[dict]

    @classmethod
    def from_json(cls, data: dict) -> "SCIPIndex":
        return cls(
            metadata=data.get("metadata", {}),
            documents=data.get("documents", []),
            external_symbols=data.get("externalSymbols", []),
        )


class SCIPProvider(IndexingProvider):
    """Reads SCIP JSON indexes into CodeCortex IndexResult objects.

    Generates the index by running:
        scip-python index <root>  →  index.scip
        scip convert --from index.scip --to index.json  →  index.json

    Falls back to loading a pre-existing index.json in the project root.
    """

    priority = 1
    name = "scip"

    def supports(self, language: str) -> bool:
        return language in ("python", "typescript", "javascript")

    def is_available(self) -> bool:
        return bool(
            shutil.which("scip") or shutil.which("scip-python") or shutil.which("scip-typescript")
        )

    def index_file(self, file_path: Path, project_root: Path) -> IndexResult:
        # SCIP operates project-wide; loading per-file is not efficient.
        # Check if a project-level index exists and filter to this file.
        index_path = self._find_index(project_root)
        if index_path:
            full = self.load_index(index_path, project_root)
            return self._filter_to_file(full, str(file_path))

        result = IndexResult(language="", source=self.name)
        result.errors.append("No SCIP index found; run scip-python index first")
        return result

    def index_project(self, root: Path, language: str) -> IndexResult:
        """Generate + load a SCIP index for the whole project."""
        index_path = self._find_index(root)

        if not index_path:
            index_path = self._generate_index(root, language)

        if not index_path:
            result = IndexResult(language=language, source=self.name)
            result.errors.append("SCIP index generation failed")
            return result

        return self.load_index(index_path, root)

    def load_index(self, index_json_path: Path, project_root: Path) -> IndexResult:
        """Parse a SCIP JSON file and return an IndexResult."""
        result = IndexResult(source=self.name)
        try:
            data = json.loads(index_json_path.read_text())
            scip = SCIPIndex.from_json(data)
            result.language = _detect_language(scip)

            # Build a symbol → SymbolDefinition map from externalSymbols
            # and per-document symbol tables.
            sym_map: dict[str, SymbolDefinition] = {}
            for ext in scip.external_symbols:
                sym = ext.get("symbol", "")
                if sym:
                    defn = _parse_symbol_definition(sym, ext, project_root)
                    if defn:
                        sym_map[sym] = defn

            for doc in scip.documents:
                rel_path = doc.get("relativePath", "")
                abs_path = str(project_root / rel_path)
                lang = doc.get("language", result.language)

                for sym_info in doc.get("symbols", []):
                    sym = sym_info.get("symbol", "")
                    if not sym:
                        continue
                    defn = _parse_symbol_definition(
                        sym, sym_info, project_root, file_path=abs_path, language=lang
                    )
                    if defn:
                        sym_map[sym] = defn
                        result.definitions.append(defn)

                for occ in doc.get("occurrences", []):
                    sym = occ.get("symbol", "")
                    roles = occ.get("symbolRoles", 0)
                    rng = occ.get("range", [0, 0, 0])
                    line = rng[0] + 1 if rng else 1
                    col = rng[1] if len(rng) > 1 else 0
                    role = _role_name(roles)
                    result.occurrences.append(
                        SymbolOccurrence(
                            symbol=sym,
                            file_path=abs_path,
                            line=line,
                            column=col,
                            role=role,
                        )
                    )

        except Exception as e:
            logger.error("SCIPProvider.load_index %s: %s", index_json_path, e)
            result.errors.append(str(e))

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_index(self, root: Path) -> Optional[Path]:
        for name in ("index.json", "scip-index.json", ".scip/index.json"):
            p = root / name
            if p.exists():
                return p
        return None

    def _generate_index(self, root: Path, language: str) -> Optional[Path]:
        scip_path = root / "index.scip"
        json_path = root / "index.json"
        try:
            if language == "python" and shutil.which("scip-python"):
                subprocess.run(
                    ["scip-python", "index", str(root), "--output", str(scip_path)],
                    cwd=str(root),
                    timeout=120,
                    check=True,
                    capture_output=True,
                )
            elif language in ("typescript", "javascript") and shutil.which("scip-typescript"):
                subprocess.run(
                    ["scip-typescript", "index", "--output", str(scip_path)],
                    cwd=str(root),
                    timeout=120,
                    check=True,
                    capture_output=True,
                )
            else:
                return None

            if scip_path.exists() and shutil.which("scip"):
                subprocess.run(
                    ["scip", "convert", "--from", str(scip_path), "--to", str(json_path)],
                    timeout=60,
                    check=True,
                    capture_output=True,
                )
                return json_path

        except Exception as e:
            logger.warning("SCIP index generation: %s", e)

        return None

    def _filter_to_file(self, full: IndexResult, file_path: str) -> IndexResult:
        filtered = IndexResult(language=full.language, source=self.name)
        filtered.definitions = [d for d in full.definitions if d.file_path == file_path]
        filtered.occurrences = [o for o in full.occurrences if o.file_path == file_path]
        return filtered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_symbol_definition(
    symbol: str,
    info: dict,
    root: Path,
    file_path: str = "",
    language: str = "",
) -> Optional[SymbolDefinition]:
    display = info.get("displayName", "") or _display_name_from_scip(symbol)
    if not display:
        return None
    kind_int = info.get("kind", 0)
    kind = _SCIP_KIND_MAP.get(kind_int, NodeKind.VARIABLE)
    qname = _scip_symbol_to_qualified(symbol)
    return SymbolDefinition(
        qualified_name=qname,
        kind=kind,
        file_path=file_path,
        line=0,
        language=language,
        display_name=display,
    )


def _scip_symbol_to_qualified(symbol: str) -> str:
    """Convert a SCIP symbol string to a dotted qualified name."""
    # Format: "scheme manager package descriptor..."
    # We want the descriptor parts joined with dots.
    parts = symbol.split(" ")
    if len(parts) < 4:
        return symbol
    descriptors = " ".join(parts[3:])
    # Strip trailing punctuation and backtick quoting
    import re

    name = re.sub(r"[().`/]+", ".", descriptors).strip(".")
    return name or symbol


def _display_name_from_scip(symbol: str) -> str:
    """Extract the last descriptor segment as a display name."""
    parts = symbol.rstrip(".").rsplit(".", 1)
    return parts[-1].strip("`()/") if parts else ""


def _role_name(roles: int) -> str:
    if roles & _ROLE_DEFINITION:
        return "definition"
    if roles & _ROLE_WRITE:
        return "write"
    return "reference"


def _detect_language(scip: SCIPIndex) -> str:
    for doc in scip.documents:
        lang = doc.get("language", "")
        if lang:
            return lang.lower()
    return ""
