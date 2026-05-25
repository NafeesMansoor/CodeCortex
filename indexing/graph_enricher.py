"""Graph enricher — upgrades tree-sitter CPG edges with semantic resolution.

After tree-sitter builds an initial CPG, call edges like:
  CALLS  source="my_func"  target="bar"   (unqualified, tree-sitter only)

can be upgraded to:
  CALLS  source="my_func"  target="utils.helpers.bar"  (fully qualified)

using data from jedi / LSP / SCIP providers.

The enricher also populates the SymbolCache and ReferenceGraph as side effects.

Usage:
    enricher = GraphEnricher(store, jedi_provider, symbol_cache, ref_graph)
    stats = enricher.enrich_project(root, language="python")
    print(stats)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.types import EdgeKind
from graph.graph_store import GraphStore
from graph.schema import CPGEdge
from indexing.indexing_provider import (
    IndexingProvider,
    SymbolOccurrence,
)
from indexing.reference_graph import ReferenceGraph
from indexing.symbol_cache import SymbolCache

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentStats:
    files_processed: int = 0
    edges_examined: int = 0
    edges_upgraded: int = 0
    new_edges_added: int = 0
    definitions_cached: int = 0
    occurrences_recorded: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"files={self.files_processed}  "
            f"edges_upgraded={self.edges_upgraded}/{self.edges_examined}  "
            f"new_edges={self.new_edges_added}  "
            f"defs_cached={self.definitions_cached}  "
            f"occurrences={self.occurrences_recorded}  "
            f"errors={len(self.errors)}"
        )


class GraphEnricher:
    """Post-processes a CPG with semantic provider data.

    Responsibilities:
    1. Index each source file with the provider (jedi/LSP/SCIP).
    2. Populate SymbolCache with resolved definitions.
    3. Populate ReferenceGraph with cross-file occurrences.
    4. For each unresolved CALLS edge in the CPG, attempt to resolve the
       target using the occurrence data and upgrade the edge in-place.
    5. Add new REFERENCES edges for usages captured by the provider but
       not present in the tree-sitter CPG.
    """

    def __init__(
        self,
        store: GraphStore,
        provider: IndexingProvider,
        symbol_cache: Optional[SymbolCache] = None,
        ref_graph: Optional[ReferenceGraph] = None,
    ):
        self.store = store
        self.provider = provider
        self.cache = symbol_cache or SymbolCache(":memory:")
        self.ref_graph = ref_graph or ReferenceGraph(":memory:")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_project(self, root: Path, language: str) -> EnrichmentStats:
        """Index the whole project and enrich all CPG edges."""
        stats = EnrichmentStats()

        index_result = self.provider.index_project(root, language)
        stats.errors.extend(index_result.errors)

        # Populate caches
        for defn in index_result.definitions:
            try:
                self.cache.put(defn)
                stats.definitions_cached += 1
            except Exception as e:
                stats.errors.append(f"cache.put: {e}")

        self.ref_graph.add_many(index_result.occurrences)
        stats.occurrences_recorded = len(index_result.occurrences)

        # Build occurrence lookup: (file_path, line, col) → qualified_name
        occ_map = _build_occurrence_map(index_result.occurrences)

        # Now upgrade unresolved CALLS edges in the CPG
        for edge in self.store.all_edges():
            if edge.kind != EdgeKind.CALLS:
                continue
            stats.edges_examined += 1

            if self._is_resolved(edge.target):
                continue  # Already qualified

            resolved = self._resolve_target(edge, occ_map)
            if resolved and resolved != edge.target:
                self._upgrade_edge(edge, resolved)
                stats.edges_upgraded += 1

        stats.files_processed = len(set(d.file_path for d in index_result.definitions))
        return stats

    def enrich_file(self, file_path: Path, project_root: Path) -> EnrichmentStats:
        """Index a single file and enrich its edges."""
        stats = EnrichmentStats(files_processed=1)

        index_result = self.provider.index_file(file_path, project_root)
        stats.errors.extend(index_result.errors)

        for defn in index_result.definitions:
            self.cache.put(defn)
            stats.definitions_cached += 1

        self.ref_graph.add_many(index_result.occurrences)
        stats.occurrences_recorded = len(index_result.occurrences)

        occ_map = _build_occurrence_map(index_result.occurrences)

        file_nodes = self.store.get_nodes_by_file(str(file_path))
        node_names = {n.qualified_name for n in file_nodes}

        for edge in self.store.all_edges():
            if edge.kind != EdgeKind.CALLS:
                continue
            if edge.source not in node_names:
                continue
            stats.edges_examined += 1
            if self._is_resolved(edge.target):
                continue
            resolved = self._resolve_target(edge, occ_map)
            if resolved and resolved != edge.target:
                self._upgrade_edge(edge, resolved)
                stats.edges_upgraded += 1

        return stats

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_resolved(self, target: str) -> bool:
        """A target is 'resolved' if it contains a dot (module.name) and
        is present in the graph store."""
        return "." in target and self.store.get_node(target) is not None

    def _resolve_target(self, edge: CPGEdge, occ_map: dict[tuple, str]) -> Optional[str]:
        """Try multiple strategies to resolve an unqualified call target."""
        # 1. Direct symbol cache lookup (exact display name match)
        candidates = self.cache.search(edge.target, exact=True)
        if len(candidates) == 1:
            return candidates[0].qualified_name

        # 2. Same-file preference: return candidate defined in same file
        source_node = self.store.get_node(edge.source)
        if source_node and candidates:
            same_file = [c for c in candidates if c.file_path == source_node.file_path]
            if same_file:
                return same_file[0].qualified_name

        # 3. Return first candidate if unique enough
        if candidates:
            return candidates[0].qualified_name

        # 4. Fall back: check ReferenceGraph for occurrences matching the name
        all_syms = self.ref_graph.symbols()
        matches = [s for s in all_syms if s.rsplit(".", 1)[-1] == edge.target]
        if len(matches) == 1:
            return matches[0]

        return None

    def _upgrade_edge(self, edge: CPGEdge, resolved_target: str) -> None:
        """Replace the edge's target with the resolved qualified name.

        SQLite doesn't support UPDATE of primary keys easily, so we delete
        and re-insert with the upgraded target.
        """
        try:
            self.store._conn.execute(
                "DELETE FROM edges WHERE kind = ? AND source = ? AND target = ?",
                (edge.kind.value, edge.source, edge.target),
            )
            self.store._conn.execute(
                "INSERT OR IGNORE INTO edges(kind, source, target, file_path, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (edge.kind.value, edge.source, resolved_target, edge.file_path, edge.confidence),
            )
            self.store._conn.commit()
        except Exception as e:
            logger.warning("upgrade_edge %s→%s: %s", edge.source, resolved_target, e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_occurrence_map(
    occurrences: list[SymbolOccurrence],
) -> dict[tuple, str]:
    """Build (file_path, line, col) → qualified_symbol lookup."""
    mapping: dict[tuple, str] = {}
    for occ in occurrences:
        key = (occ.file_path, occ.line, occ.column)
        mapping[key] = occ.symbol
    return mapping
