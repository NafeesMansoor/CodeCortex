"""PHP language parser — covers Laravel applications.

Extracts:
  - Classes (Eloquent models, controllers, services, middleware)
  - Methods (public, protected, static)
  - Functions
  - Interfaces and traits
  - Use statements (imports)
  - Inheritance / interface implementation

Targets the upstream tree_sitter API used by tree-sitter-language-pack >= 1.14:
node accessors are properties (node.type, node.child_count, node.start_byte)
and Parser.parse takes bytes.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.parser_framework import LanguageParser
from core.source_text import SourceText, node_text
from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange

logger = logging.getLogger(__name__)


def _children(node) -> list:
    return [node.child(i) for i in range(node.child_count)]


def _extract_text(node, source: str) -> str:
    return node_text(node, source).strip()


def _start_line(node) -> int:
    return node.start_point.row + 1


def _end_line(node) -> int:
    return node.end_point.row + 1


class PHPParser(LanguageParser):
    """PHP parser using tree-sitter — tuned for Laravel conventions."""

    language = "php"
    extensions = [".php"]

    def parse(self, source: str, file_path: str) -> ParseResult:
        try:
            from core.parsers.grammars import get_parser

            self._current_file_path = file_path
            parser = get_parser("php")
            # Wrap once so every byte-offset slice below is O(1) and correct,
            # and parse the very bytes those offsets index into.
            source = SourceText(source)
            tree = parser.parse(source.as_bytes())
            root = tree.root_node

            nodes = self.extract_definitions(root, source)
            nodes.extend(self.extract_types(root, source))
            nodes.extend(self.extract_tests(root, source))

            edges = []
            edges.extend(self.extract_calls(root, source))
            edges.extend(self.extract_imports(root, source))
            edges.extend(self.extract_inheritance(root, source))

            return ParseResult(
                language=self.language,
                file_path=file_path,
                nodes=nodes,
                edges=edges,
            )
        except Exception as e:
            logger.error("Error parsing %s: %s", file_path, e)
            return ParseResult(
                language=self.language,
                file_path=file_path,
                errors=[str(e)],
            )

    def extract_definitions(self, root, source: str) -> list[NodeInfo]:
        nodes = []
        fp = self._current_file_path

        def visit(node, parent_class=None):
            kind = node.type
            if kind == "function_definition":
                name = self._get_name(node, source)
                if name:
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.FUNCTION,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                            parent_name=parent_class,
                        )
                    )

            if kind == "method_declaration":
                name = self._get_name(node, source)
                if name:
                    modifiers = self._get_modifiers(node, source)
                    is_test = name.startswith("test")
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.TEST if is_test else NodeKind.FUNCTION,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                            parent_name=parent_class,
                            modifiers=modifiers,
                            is_test=is_test,
                        )
                    )

            new_class = parent_class
            if kind == "class_declaration":
                new_class = self._get_name(node, source)

            for child in _children(node):
                visit(child, new_class)

        visit(root)
        return nodes

    def extract_calls(self, root, source: str) -> list[EdgeInfo]:
        edges = []
        fp = self._current_file_path

        def visit(node, enclosing=None):
            kind = node.type
            if kind in ("function_call_expression", "member_call_expression"):
                target = self._get_call_target(node, source)
                if target and enclosing:
                    edges.append(
                        EdgeInfo(
                            kind=EdgeKind.CALLS,
                            source_qualified=enclosing,
                            target_qualified=target,
                            file_path=fp,
                        )
                    )

            new_enc = enclosing
            if kind in ("function_definition", "method_declaration"):
                new_enc = self._get_name(node, source) or enclosing

            for child in _children(node):
                visit(child, new_enc)

        visit(root)
        return edges

    def extract_imports(self, root, source: str) -> list[EdgeInfo]:
        edges = []
        fp = self._current_file_path

        def visit(node):
            if node.type == "use_declaration":
                for child in _children(node):
                    if child.type == "use_class_name_list":
                        for use_name in _children(child):
                            if use_name.type == "name":
                                target = _extract_text(use_name, source)
                                if target:
                                    edges.append(
                                        EdgeInfo(
                                            kind=EdgeKind.IMPORTS_FROM,
                                            source_qualified="__module__",
                                            target_qualified=target,
                                            file_path=fp,
                                        )
                                    )
                    elif child.type == "qualified_name":
                        target = _extract_text(child, source)
                        if target:
                            edges.append(
                                EdgeInfo(
                                    kind=EdgeKind.IMPORTS_FROM,
                                    source_qualified="__module__",
                                    target_qualified=target,
                                    file_path=fp,
                                )
                            )
            for child in _children(node):
                visit(child)

        visit(root)
        return edges

    def extract_inheritance(self, root, source: str) -> list[EdgeInfo]:
        edges = []
        fp = self._current_file_path

        def visit(node):
            if node.type == "class_declaration":
                class_name = self._get_name(node, source)
                for child in _children(node):
                    if child.type == "base_clause":
                        for base in _children(child):
                            if base.type in ("qualified_name", "name"):
                                base_name = _extract_text(base, source)
                                if base_name and class_name:
                                    edges.append(
                                        EdgeInfo(
                                            kind=EdgeKind.INHERITS,
                                            source_qualified=class_name,
                                            target_qualified=base_name,
                                            file_path=fp,
                                        )
                                    )
                    elif child.type == "class_implements":
                        for iface in _children(child):
                            if iface.type in ("qualified_name", "name"):
                                iface_name = _extract_text(iface, source)
                                if iface_name and class_name:
                                    edges.append(
                                        EdgeInfo(
                                            kind=EdgeKind.IMPLEMENTS,
                                            source_qualified=class_name,
                                            target_qualified=iface_name,
                                            file_path=fp,
                                        )
                                    )
            for child in _children(node):
                visit(child)

        visit(root)
        return edges

    def extract_types(self, root, source: str) -> list[NodeInfo]:
        nodes = []
        fp = self._current_file_path

        def visit(node):
            kind = node.type
            if kind == "class_declaration":
                name = self._get_name(node, source)
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
            elif kind == "interface_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.INTERFACE,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                        )
                    )
            elif kind == "trait_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.CLASS,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                            extra={"is_trait": True},
                        )
                    )
            for child in _children(node):
                visit(child)

        visit(root)
        return nodes

    def extract_tests(self, root, source: str) -> list[NodeInfo]:
        nodes = []
        fp = self._current_file_path

        def visit(node, parent_class=None):
            if node.type == "method_declaration":
                name = self._get_name(node, source)
                if name and name.startswith("test"):
                    nodes.append(
                        NodeInfo(
                            kind=NodeKind.TEST,
                            name=name,
                            file_path=fp,
                            range=SourceRange(_start_line(node), _end_line(node)),
                            language=self.language,
                            parent_name=parent_class,
                            is_test=True,
                        )
                    )

            new_class = parent_class
            if node.type == "class_declaration":
                new_class = self._get_name(node, source)

            for child in _children(node):
                visit(child, new_class)

        visit(root)
        return nodes

    def _get_name(self, node, source: str) -> Optional[str]:
        for child in _children(node):
            if child.type == "name":
                return _extract_text(child, source)
        return None

    def _get_modifiers(self, node, source: str) -> list[str]:
        mods = []
        for child in _children(node):
            if child.type in ("public", "protected", "private", "static", "abstract", "final"):
                mods.append(child.type)
        return mods

    def _get_call_target(self, node, source: str) -> Optional[str]:
        children = _children(node)
        if children:
            first = children[0]
            if first.type in ("name", "variable_name"):
                return _extract_text(first, source)
        return None
