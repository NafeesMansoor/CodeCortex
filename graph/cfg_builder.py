"""Control Flow Graph (CFG) builder.

Extracts CONTROLS edges by scanning tree-sitter AST for control-flow
constructs (if/elif/else, for, while, try/except, with) and connecting
the enclosing function to the functions/methods called within each branch.

These edges increase graph density and PageRank discrimination.

Usage:
    cfg = CFGBuilder(store)
    cfg.process_result(parse_result, tree, source)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.types import EdgeKind
from graph.schema import CPGEdge

logger = logging.getLogger(__name__)

# tree-sitter node types that introduce new control-flow scopes
_CONTROL_TYPES = frozenset({
    "if_statement",
    "elif_clause",
    "else_clause",
    "for_statement",
    "while_statement",
    "try_statement",
    "except_clause",
    "finally_clause",
    "with_statement",
    # JS/TS
    "if_statement",
    "for_statement",
    "for_in_statement",
    "while_statement",
    "try_statement",
    "catch_clause",
    "switch_statement",
    "case_clause",
    # PHP
    "if_statement",
    "foreach_statement",
    "while_statement",
    "try_statement",
    "catch_clause",
})

_CALL_TYPES = frozenset({"call", "call_expression", "member_call_expression"})


class CFGBuilder:
    """Extracts CONTROLS edges representing intra-function control flow.

    Strategy: for each control-flow node, emit a CONTROLS edge from the
    enclosing function to every call target found inside that branch.
    This captures 'function F controls calls to G under condition X'.
    """

    def __init__(self, store):
        self.store = store

    def process(
        self,
        tree,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
    ) -> list[CPGEdge]:
        """Walk the AST and emit CONTROLS edges.

        Args:
            tree: tree-sitter Tree object
            source: source code string
            file_path: path to the file
            func_name_map: {short_name → qualified_name} for functions in file

        Returns:
            List of CONTROLS CPGEdge objects
        """
        edges: list[CPGEdge] = []
        self._walk(tree.root_node, source, file_path, func_name_map,
                   enclosing_func=None, in_control=False, edges=edges)
        return edges

    def _walk(
        self,
        node,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
        enclosing_func: Optional[str],
        in_control: bool,
        edges: list[CPGEdge],
    ) -> None:
        ntype = node.type

        # Track enclosing function
        if ntype in ("function_definition", "function_declaration",
                     "method_definition", "arrow_function"):
            func_name = _identifier(node, source)
            enclosing_func = func_name_map.get(func_name, func_name)

        # When we enter a control block, mark in_control
        entered_control = ntype in _CONTROL_TYPES
        current_in_control = in_control or entered_control

        # Emit CONTROLS edge for calls inside a control block
        if current_in_control and ntype in _CALL_TYPES and enclosing_func:
            call_target = _call_target(node, source)
            if call_target:
                resolved = func_name_map.get(call_target, call_target)
                if resolved != enclosing_func:
                    edges.append(CPGEdge(
                        kind=EdgeKind.CONTROLS,
                        source=enclosing_func,
                        target=resolved,
                        file_path=file_path,
                        confidence=0.8,
                    ))

        for child in node.children:
            self._walk(child, source, file_path, func_name_map,
                       enclosing_func, current_in_control, edges)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _identifier(node, source: str) -> str:
    for child in node.children:
        if child.type == "identifier":
            text = source[child.start_byte:child.end_byte]
            return text.decode() if isinstance(text, bytes) else text
    return ""


def _call_target(node, source: str) -> Optional[str]:
    if not node.children:
        return None
    first = node.children[0]
    if first.type == "identifier":
        text = source[first.start_byte:first.end_byte]
        return text.decode() if isinstance(text, bytes) else text
    # member access: obj.method(...)
    if first.type in ("attribute", "member_expression"):
        for child in first.children:
            if child.type == "identifier":
                text = source[child.start_byte:child.end_byte]
                return (text.decode() if isinstance(text, bytes) else text)
    return None
