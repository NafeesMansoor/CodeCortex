"""TypeScript language parser implementation."""

from __future__ import annotations

from typing import Optional

from core.parser_framework import LanguageParser
from core.source_text import SourceText, byte_slice, node_text
from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange


# tree-sitter-language-pack new callable API helpers
def _children(node) -> list:
    return [node.child(i) for i in range(node.child_count())]


def _start_line(node) -> int:
    return node.start_position().row + 1


def _end_line(node) -> int:
    return node.end_position().row + 1


def _node_text(node, source: str) -> str:
    return node_text(node, source)


class TypeScriptParser(LanguageParser):
    """TypeScript code parser using tree-sitter."""

    language = "typescript"
    extensions = [".ts", ".tsx"]

    def parse(self, source: str, file_path: str) -> ParseResult:
        """Parse TypeScript source code."""
        try:
            import tree_sitter_language_pack as tslp

            self._current_file_path = file_path
            parser = tslp.get_parser("typescript")
            tree = parser.parse(source)
            # Wrap once so every byte-offset slice below is O(1) and correct.
            source = SourceText(source)

            nodes = self.extract_definitions(tree, source)
            nodes.extend(self.extract_types(tree, source))
            nodes.extend(self.extract_tests(tree, source))

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
            return ParseResult(
                language=self.language,
                file_path=file_path,
                errors=[str(e)],
            )

    def extract_definitions(self, tree, source: str) -> list[NodeInfo]:
        """Extract function declarations."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() in ("function_declaration", "arrow_function", "method_definition"):
                name = self._get_identifier(node, source)
                if name:
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.FUNCTION,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                        )
                    )

            for child in _children(node):
                visit(child)

        visit(tree.root_node())
        return nodes

    def extract_calls(self, tree, source: str) -> list[EdgeInfo]:
        """Extract function calls."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, enclosing_func=None):
            if node.kind() == "call_expression":
                target = self._get_call_target(node, source)
                if target and enclosing_func:
                    edges.append(
                        EdgeInfo(
                            kind=EdgeKind.CALLS,
                            source_qualified=enclosing_func,
                            target_qualified=target,
                            file_path=fp,
                        )
                    )

            new_func = enclosing_func
            if node.kind() in ("function_declaration", "method_definition"):
                new_func = self._get_identifier(node, source)

            for child in _children(node):
                visit(child, new_func)

        visit(tree.root_node())
        return edges

    def extract_imports(self, tree, source: str) -> list[EdgeInfo]:
        """Extract import statements."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() == "import_statement":
                module = self._extract_import_module(node, source)
                if module:
                    edges.append(
                        EdgeInfo(
                            kind=EdgeKind.IMPORTS_FROM,
                            source_qualified="module",
                            target_qualified=module,
                            file_path=fp,
                        )
                    )

            for child in _children(node):
                visit(child)

        visit(tree.root_node())
        return edges

    def extract_inheritance(self, tree, source: str) -> list[EdgeInfo]:
        """Extract class inheritance and interface implementation."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() == "class_declaration":
                class_name = self._get_identifier(node, source)

                for child in _children(node):
                    if child.kind() == "class_heritage":
                        for heritage in _children(child):
                            if heritage.kind() == "identifier":
                                base = _node_text(heritage, source)
                                edges.append(
                                    EdgeInfo(
                                        kind=EdgeKind.INHERITS,
                                        source_qualified=class_name,
                                        target_qualified=base,
                                        file_path=fp,
                                    )
                                )

            for child in _children(node):
                visit(child)

        visit(tree.root_node())
        return edges

    def extract_types(self, tree, source: str) -> list[NodeInfo]:
        """Extract class and interface definitions."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() in ("class_declaration", "interface_declaration"):
                name = self._get_identifier(node, source)
                if name:
                    kind = (
                        NodeKind.CLASS if node.kind() == "class_declaration" else NodeKind.INTERFACE
                    )

                    nodes.append(
                        NodeInfo(
                            kind=kind,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                        )
                    )

            for child in _children(node):
                visit(child)

        visit(tree.root_node())
        return nodes

    def extract_tests(self, tree, source: str) -> list[NodeInfo]:
        """Extract test functions."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() == "function_declaration":
                name = self._get_identifier(node, source)
                if name and (
                    name.startswith("test") or name.startswith("it") or name.startswith("describe")
                ):
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

        visit(tree.root_node())
        return nodes

    # Helpers
    def _get_identifier(self, node, source: str) -> Optional[str]:
        for child in _children(node):
            if child.kind() == "identifier":
                return _node_text(child, source)
        return None

    def _get_call_target(self, node, source: str) -> Optional[str]:
        if node.child_count() > 0:
            target_node = node.child(0)
            if target_node.kind() == "identifier":
                return _node_text(target_node, source)
        return None

    def _extract_import_module(self, node, source: str) -> Optional[str]:
        for child in _children(node):
            if child.kind() == "string":
                # Trim the surrounding quotes, which are one byte each.
                return byte_slice(source, child.start_byte() + 1, child.end_byte() - 1)
        return None
