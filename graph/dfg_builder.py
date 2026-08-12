"""Data Flow Graph (DFG) builder.

Extracts READS and WRITES edges by scanning the AST for variable
assignments and usages within function bodies.

- WRITES: function assigns a value to a variable (x = ..., x += ...)
- READS: function uses a variable (passes it to a call, returns it, etc.)

Variable nodes are added to the CPG as VARIABLE-kind nodes. This increases
graph density and enables taint-propagation traversal.

Usage:
    dfg = DFGBuilder(store)
    edges = dfg.process(tree, source, file_path, func_name_map)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.source_text import node_text
from core.types import EdgeKind, NodeKind
from graph.schema import CPGEdge, CPGNode

logger = logging.getLogger(__name__)


def _children(node) -> list:
    return [node.child(i) for i in range(node.child_count)]


# Assignment node types per language
_ASSIGN_TYPES = frozenset(
    {
        "assignment",  # Python: x = y
        "augmented_assignment",  # Python: x += y
        "named_expression",  # Python: (x := y)
        "variable_declaration",  # JS/TS: let x = y
        "lexical_declaration",  # JS/TS: const/let
        "assignment_expression",  # JS/TS: x = y
        "expression_statement",  # PHP wrapper
    }
)

# Return/yield nodes — track what a function returns
_RETURN_TYPES = frozenset({"return_statement", "yield", "yield_statement"})


class DFGBuilder:
    """Extracts READS and WRITES edges for variable data flow."""

    def __init__(self, store):
        self.store = store

    def process(
        self,
        tree,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
    ) -> tuple[list[CPGNode], list[CPGEdge]]:
        """Walk AST and emit variable nodes + READS/WRITES edges.

        Returns:
            (new_variable_nodes, data_flow_edges)
        """
        nodes: list[CPGNode] = []
        edges: list[CPGEdge] = []
        seen_vars: set[str] = set()

        self._walk(
            tree.root_node,
            source,
            file_path,
            func_name_map,
            enclosing_func=None,
            nodes=nodes,
            edges=edges,
            seen_vars=seen_vars,
        )
        return nodes, edges

    def _walk(
        self,
        node,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
        enclosing_func: Optional[str],
        nodes: list[CPGNode],
        edges: list[CPGEdge],
        seen_vars: set[str],
    ) -> None:
        ntype = node.type

        # Track enclosing function
        if ntype in (
            "function_definition",
            "function_declaration",
            "method_definition",
            "arrow_function",
        ):
            fname = _identifier(node, source)
            enclosing_func = func_name_map.get(fname, fname)

        # Handle assignments → WRITES edge (skip trivial loop vars like i, x, n)
        if ntype in _ASSIGN_TYPES and enclosing_func:
            lhs = _lhs_name(node, source)
            if lhs and len(lhs) > 1 and not lhs.startswith("_"):
                var_qname = f"{enclosing_func}.{lhs}"
                if var_qname not in seen_vars:
                    seen_vars.add(var_qname)
                    # Only add variable node if it doesn't exist yet
                    if not self.store.has_node(var_qname):
                        nodes.append(
                            CPGNode(
                                qualified_name=var_qname,
                                kind=NodeKind.VARIABLE,
                                name=lhs,
                                file_path=file_path,
                            )
                        )
                edges.append(
                    CPGEdge(
                        kind=EdgeKind.WRITES,
                        source=enclosing_func,
                        target=var_qname,
                        file_path=file_path,
                        confidence=0.9,
                    )
                )

        # Handle identifier usages inside calls → READS edge
        if ntype == "identifier" and enclosing_func:
            name = _text(node, source)
            if name and not name[0].isupper():
                # Check if this name is a known local variable of this func
                var_qname = f"{enclosing_func}.{name}"
                # Set membership first: it short-circuits the SQLite round-trip,
                # and this runs once per identifier token in every file.
                if var_qname in seen_vars or self.store.has_node(var_qname):
                    edges.append(
                        CPGEdge(
                            kind=EdgeKind.READS,
                            source=enclosing_func,
                            target=var_qname,
                            file_path=file_path,
                            confidence=0.7,
                        )
                    )

        for child in _children(node):
            self._walk(
                child, source, file_path, func_name_map, enclosing_func, nodes, edges, seen_vars
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identifier(node, source: str) -> str:
    for child in _children(node):
        if child.type == "identifier":
            return _text(child, source)
    return ""


def _lhs_name(node, source: str) -> Optional[str]:
    """Extract the left-hand side variable name from an assignment."""
    if node.child_count == 0:
        return None
    lhs = node.child(0)
    if lhs.type == "identifier":
        return _text(lhs, source)
    if lhs.type in ("pattern", "tuple_pattern"):
        # x, y = ... → just take first
        for child in _children(lhs):
            if child.type == "identifier":
                return _text(child, source)
    return None


def _text(node, source: str) -> str:
    return node_text(node, source)
