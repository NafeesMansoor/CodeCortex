"""Semantic index: symbol-aware code index built on top of the CPG.

Provides fast lookup of symbols, cross-file references, and call hierarchies
without re-traversing the full graph on every query.

PHASE 2 entry point — wraps GraphStore with symbol-oriented access patterns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.types import EdgeKind, NodeKind
from graph.graph_store import GraphStore
from graph.schema import CPGNode

logger = logging.getLogger(__name__)


@dataclass
class SymbolInfo:
    """Resolved symbol with location and relationship metadata."""

    qualified_name: str
    kind: NodeKind
    file_path: str
    language: str = ""
    callers: list[str] = field(default_factory=list)    # Who calls this
    callees: list[str] = field(default_factory=list)    # What this calls
    references: list[str] = field(default_factory=list) # Other uses
    parent: Optional[str] = None


class SemanticIndex:
    """Symbol-oriented view over a GraphStore.

    Caches call-hierarchy and reference lookups so they don't require
    repeated graph scans.

    Usage:
        store = GraphStore(":memory:")
        # ... populate store via CPGBuilder ...
        idx = SemanticIndex(store)
        info = idx.lookup("my_module.MyClass.my_method")
    """

    def __init__(self, store: GraphStore):
        self.store = store
        self._symbol_cache: dict[str, SymbolInfo] = {}
        self._dirty = True  # Cache invalidated on store changes

    def invalidate(self) -> None:
        """Mark cache as stale (call after CPGBuilder.ingest)."""
        self._symbol_cache.clear()
        self._dirty = True

    def lookup(self, qualified_name: str) -> Optional[SymbolInfo]:
        """Look up a symbol by qualified name.

        Returns None if the symbol is not in the graph.
        """
        if qualified_name in self._symbol_cache:
            return self._symbol_cache[qualified_name]

        node = self.store.get_node(qualified_name)
        if node is None:
            return None

        info = self._build_symbol_info(node)
        self._symbol_cache[qualified_name] = info
        return info

    def find_by_name(self, name: str) -> list[SymbolInfo]:
        """Find all symbols with a given short name (unqualified)."""
        results = []
        for node in self.store.all_nodes():
            if node.name == name:
                info = self.lookup(node.qualified_name)
                if info:
                    results.append(info)
        return results

    def callers_of(self, qualified_name: str) -> list[str]:
        """Return qualified names of all nodes that call this symbol."""
        edges = self.store.get_incoming_edges(qualified_name)
        return [e.source for e in edges if e.kind == EdgeKind.CALLS]

    def callees_of(self, qualified_name: str) -> list[str]:
        """Return qualified names of all symbols called by this node."""
        edges = self.store.get_outgoing_edges(qualified_name)
        return [e.target for e in edges if e.kind == EdgeKind.CALLS]

    def imports_of(self, file_path: str) -> list[str]:
        """Return modules imported by a file."""
        edges = self.store.get_outgoing_edges(file_path)
        return [e.target for e in edges if e.kind == EdgeKind.IMPORTS_FROM]

    def subclasses_of(self, qualified_name: str) -> list[str]:
        """Return all classes that inherit from the given class."""
        edges = self.store.get_incoming_edges(qualified_name)
        return [e.source for e in edges if e.kind == EdgeKind.INHERITS]

    def symbols_in_file(self, file_path: str) -> list[SymbolInfo]:
        """Return all symbols defined in a file."""
        nodes = self.store.get_nodes_by_file(file_path)
        infos = []
        for node in nodes:
            if node.kind != NodeKind.FILE:
                info = self.lookup(node.qualified_name)
                if info:
                    infos.append(info)
        return infos

    def all_files(self) -> list[str]:
        """Return all indexed file paths."""
        return [
            n.qualified_name
            for n in self.store.get_nodes_by_kind(NodeKind.FILE)
        ]

    def stats(self) -> dict:
        """Return index statistics."""
        return {
            "nodes": self.store.node_count(),
            "edges": self.store.edge_count(),
            "files": len(self.all_files()),
            "cached_symbols": len(self._symbol_cache),
        }

    def _build_symbol_info(self, node: CPGNode) -> SymbolInfo:
        callers = self.callers_of(node.qualified_name)
        callees = self.callees_of(node.qualified_name)

        ref_edges = self.store.get_incoming_edges(node.qualified_name)
        references = [
            e.source for e in ref_edges if e.kind == EdgeKind.REFERENCES
        ]

        return SymbolInfo(
            qualified_name=node.qualified_name,
            kind=node.kind,
            file_path=node.file_path,
            language=node.language,
            callers=callers,
            callees=callees,
            references=references,
            parent=node.parent_qualified,
        )
