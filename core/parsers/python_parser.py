"""Python language parser implementation.

Extracts Python-specific structures: functions, classes, imports, decorators, etc.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.parser_framework import LanguageParser
from core.source_text import SourceText, node_text
from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange

logger = logging.getLogger(__name__)


# Node accessors are properties on the upstream tree_sitter API used by
# tree-sitter-language-pack >= 1.14.
def _children(node) -> list:
    return [node.child(i) for i in range(node.child_count)]


def _start_line(node) -> int:
    return node.start_point.row + 1


def _end_line(node) -> int:
    return node.end_point.row + 1


def _node_text(node, source: str) -> str:
    return node_text(node, source)


class PythonParser(LanguageParser):
    """Python code parser using tree-sitter."""

    language = "python"
    extensions = [".py"]

    def parse(self, source: str, file_path: str) -> ParseResult:
        try:
            from core.parsers.grammars import get_parser

            self._current_file_path = file_path
            parser = get_parser("python")
            # Wrap once so every byte-offset slice below is O(1) and correct,
            # and parse the very bytes those offsets index into.
            source = SourceText(source)
            tree = parser.parse(source.as_bytes())

            nodes = self.extract_definitions(tree, source)
            nodes.extend(self.extract_types(tree, source))

            edges = []
            edges.extend(self.extract_calls(tree, source))
            edges.extend(self.extract_imports(tree, source))
            edges.extend(self.extract_inheritance(tree, source))

            result = ParseResult(
                language=self.language,
                file_path=file_path,
                nodes=nodes,
                edges=edges,
            )
            result._tree = tree
            result._source = source
            return result
        except Exception as e:
            logger.error("Error parsing %s: %s", file_path, e)
            return ParseResult(
                language=self.language,
                file_path=file_path,
                errors=[str(e)],
            )

    def extract_definitions(self, tree, source: str) -> list[NodeInfo]:
        """Extract function and method definitions.

        Functions inside a class body become METHOD nodes; top-level functions
        remain FUNCTION nodes.
        """
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, parent_name=None, inside_class=False):
            if node.type == "function_definition":
                name = self._get_identifier(node, source)
                if name:
                    params = self._extract_params(node, source)
                    is_test = name.startswith("test_") or (parent_name and "Test" in parent_name)

                    if is_test:
                        kind = NodeKind.TEST
                    elif inside_class:
                        kind = NodeKind.METHOD
                    else:
                        kind = NodeKind.FUNCTION

                    nodes.append(
                        NodeInfo(
                            kind=kind,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                            parent_name=parent_name,
                            params=params,
                            is_test=is_test,
                        )
                    )
                    for child in _children(node):
                        visit(child, parent_name=name, inside_class=False)
                    return

            if node.type == "class_definition":
                class_name = self._get_identifier(node, source)
                for child in _children(node):
                    visit(child, parent_name=class_name, inside_class=True)
                return

            for child in _children(node):
                visit(child, parent_name=parent_name, inside_class=inside_class)

        visit(tree.root_node)
        return nodes

    def extract_calls(self, tree, source: str) -> list[EdgeInfo]:
        """Extract function/method calls."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, enclosing_func=None):
            if node.type == "call":
                target = self._get_call_target(node, source)
                if target and enclosing_func:
                    edges.append(
                        EdgeInfo(
                            kind=EdgeKind.CALLS,
                            source_qualified=enclosing_func,
                            target_qualified=target,
                            file_path=fp,
                            position=None,
                        )
                    )

            new_func = enclosing_func
            if node.type == "function_definition":
                new_func = self._get_identifier(node, source)

            for child in _children(node):
                visit(child, new_func)

        visit(tree.root_node)
        return edges

    def extract_imports(self, tree, source: str) -> list[EdgeInfo]:
        """Extract import statements."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "import_statement":
                module = self._extract_import_module(node, source)
                if module:
                    edges.append(
                        EdgeInfo(
                            kind=EdgeKind.IMPORTS_FROM,
                            source_qualified="__main__",
                            target_qualified=module,
                            file_path=fp,
                        )
                    )
            elif node.type == "import_from_statement":
                module = self._extract_from_import(node, source)
                if module:
                    edges.append(
                        EdgeInfo(
                            kind=EdgeKind.IMPORTS_FROM,
                            source_qualified="__main__",
                            target_qualified=module,
                            file_path=fp,
                        )
                    )

            for child in _children(node):
                visit(child)

        visit(tree.root_node)
        return edges

    def extract_inheritance(self, tree, source: str) -> list[EdgeInfo]:
        """Extract class inheritance."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, parent_class=None):
            if node.type == "class_definition":
                class_name = self._get_identifier(node, source)

                for child in _children(node):
                    if child.type == "argument_list":
                        bases = self._extract_bases(child, source)
                        for base in bases:
                            edges.append(
                                EdgeInfo(
                                    kind=EdgeKind.INHERITS,
                                    source_qualified=class_name,
                                    target_qualified=base,
                                    file_path=fp,
                                )
                            )

            for child in _children(node):
                visit(child, parent_class)

        visit(tree.root_node)
        return edges

    def extract_types(self, tree, source: str) -> list[NodeInfo]:
        """Extract class definitions."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "class_definition":
                name = self._get_identifier(node, source)
                if name:
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.CLASS,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                        )
                    )

            for child in _children(node):
                visit(child)

        visit(tree.root_node)
        return nodes

    def extract_tests(self, tree, source: str) -> list[NodeInfo]:
        """Extract test methods/functions."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "function_definition":
                name = self._get_identifier(node, source)
                if name and (name.startswith("test_") or name == "setUp" or name == "tearDown"):
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.TEST,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                            is_test=True,
                        )
                    )

            for child in _children(node):
                visit(child)

        visit(tree.root_node)
        return nodes

    # Helper methods
    def _get_identifier(self, node, source: str) -> Optional[str]:
        for child in _children(node):
            if child.type == "identifier":
                return _node_text(child, source)
        return None

    def _extract_params(self, node, source: str) -> Optional[list[str]]:
        params = []
        for child in _children(node):
            if child.type == "parameters":
                for param in _children(child):
                    if param.type == "identifier":
                        params.append(_node_text(param, source))
        return params if params else None

    def _get_call_target(self, node, source: str) -> Optional[str]:
        if node.child_count > 0:
            func_node = node.child(0)
            if func_node.type == "identifier":
                return _node_text(func_node, source)
        return None

    def _extract_import_module(self, node, source: str) -> Optional[str]:
        for child in _children(node):
            if child.type in ("dotted_name", "identifier"):
                return _node_text(child, source)
        return None

    def _extract_from_import(self, node, source: str) -> Optional[str]:
        for child in _children(node):
            if child.type in ("dotted_name", "identifier"):
                return _node_text(child, source)
        return None

    def _extract_bases(self, node, source: str) -> list[str]:
        bases = []
        for child in _children(node):
            if child.type == "identifier":
                bases.append(_node_text(child, source))
        return bases
