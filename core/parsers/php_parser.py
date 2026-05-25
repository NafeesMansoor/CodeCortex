"""PHP language parser — covers Laravel applications.

Extracts:
  - Classes (Eloquent models, controllers, services, middleware)
  - Methods (public, protected, static)
  - Functions
  - Interfaces and traits
  - Use statements (imports)
  - Inheritance / interface implementation
"""

from __future__ import annotations

import logging
from typing import Optional

from core.parser_framework import LanguageParser
from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange

logger = logging.getLogger(__name__)


# tree-sitter-language-pack new callable API helpers
def _children(node) -> list:
    return [node.child(i) for i in range(node.child_count())]


def _start_line(node) -> int:
    return node.start_position().row + 1


def _end_line(node) -> int:
    return node.end_position().row + 1


class PHPParser(LanguageParser):
    """PHP parser using tree-sitter — tuned for Laravel conventions."""

    language = "php"
    extensions = [".php"]

    def parse(self, source: str, file_path: str) -> ParseResult:
        try:
            import tree_sitter_language_pack as tslp

            self._current_file_path = file_path
            parser = tslp.get_parser("php")
            tree = parser.parse(source)

            nodes = self.extract_definitions(tree, source)
            nodes.extend(self.extract_types(tree, source))
            nodes.extend(self.extract_tests(tree, source))

            edges = []
            edges.extend(self.extract_calls(tree, source))
            edges.extend(self.extract_imports(tree, source))
            edges.extend(self.extract_inheritance(tree, source))

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

    def extract_definitions(self, tree, source: str) -> list[NodeInfo]:
        """Extract function and method definitions."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, parent_class=None):
            if node.kind() == "function_definition":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.FUNCTION,
                        name=name,
                        file_path=fp,
                        range=SourceRange(_start_line(node), _end_line(node)),
                        language=self.language,
                        parent_name=parent_class,
                    ))

            if node.kind() == "method_declaration":
                name = self._get_name(node, source)
                if name:
                    modifiers = self._get_modifiers(node, source)
                    is_test = name.startswith("test")
                    nodes.append(NodeInfo(
                        kind=NodeKind.TEST if is_test else NodeKind.FUNCTION,
                        name=name,
                        file_path=fp,
                        range=SourceRange(_start_line(node), _end_line(node)),
                        language=self.language,
                        parent_name=parent_class,
                        modifiers=modifiers,
                        is_test=is_test,
                    ))

            new_class = parent_class
            if node.kind() == "class_declaration":
                new_class = self._get_name(node, source)

            for child in _children(node):
                visit(child, new_class)

        visit(tree.root_node())
        return nodes

    def extract_calls(self, tree, source: str) -> list[EdgeInfo]:
        """Extract method / function calls."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, enclosing=None):
            if node.kind() in ("function_call_expression", "member_call_expression"):
                target = self._get_call_target(node, source)
                if target and enclosing:
                    edges.append(EdgeInfo(
                        kind=EdgeKind.CALLS,
                        source_qualified=enclosing,
                        target_qualified=target,
                        file_path=fp,
                    ))

            new_enc = enclosing
            if node.kind() in ("function_definition", "method_declaration"):
                new_enc = self._get_name(node, source) or enclosing

            for child in _children(node):
                visit(child, new_enc)

        visit(tree.root_node())
        return edges

    def extract_imports(self, tree, source: str) -> list[EdgeInfo]:
        """Extract use / require / include statements."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() == "use_declaration":
                for child in _children(node):
                    if child.kind() == "use_class_name_list":
                        for use_name in _children(child):
                            if use_name.kind() == "name":
                                target = _extract_text(use_name, source)
                                if target:
                                    edges.append(EdgeInfo(
                                        kind=EdgeKind.IMPORTS_FROM,
                                        source_qualified="__module__",
                                        target_qualified=target,
                                        file_path=fp,
                                    ))
                    elif child.kind() == "qualified_name":
                        target = _extract_text(child, source)
                        if target:
                            edges.append(EdgeInfo(
                                kind=EdgeKind.IMPORTS_FROM,
                                source_qualified="__module__",
                                target_qualified=target,
                                file_path=fp,
                            ))
            for child in _children(node):
                visit(child)

        visit(tree.root_node())
        return edges

    def extract_inheritance(self, tree, source: str) -> list[EdgeInfo]:
        """Extract extends and implements relationships."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() == "class_declaration":
                class_name = self._get_name(node, source)
                for child in _children(node):
                    if child.kind() == "base_clause":
                        for base in _children(child):
                            if base.kind() in ("qualified_name", "name"):
                                base_name = _extract_text(base, source)
                                if base_name and class_name:
                                    edges.append(EdgeInfo(
                                        kind=EdgeKind.INHERITS,
                                        source_qualified=class_name,
                                        target_qualified=base_name,
                                        file_path=fp,
                                    ))
                    elif child.kind() == "class_implements":
                        for iface in _children(child):
                            if iface.kind() in ("qualified_name", "name"):
                                iface_name = _extract_text(iface, source)
                                if iface_name and class_name:
                                    edges.append(EdgeInfo(
                                        kind=EdgeKind.IMPLEMENTS,
                                        source_qualified=class_name,
                                        target_qualified=iface_name,
                                        file_path=fp,
                                    ))
            for child in _children(node):
                visit(child)

        visit(tree.root_node())
        return edges

    def extract_types(self, tree, source: str) -> list[NodeInfo]:
        """Extract class, interface, and trait declarations."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.kind() == "class_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.CLASS,
                        name=name,
                        file_path=fp,
                        range=SourceRange(_start_line(node), _end_line(node)),
                        language=self.language,
                    ))
            elif node.kind() == "interface_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.INTERFACE,
                        name=name,
                        file_path=fp,
                        range=SourceRange(_start_line(node), _end_line(node)),
                        language=self.language,
                    ))
            elif node.kind() == "trait_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.CLASS,    # Traits map to CLASS for graph purposes
                        name=name,
                        file_path=fp,
                        range=SourceRange(_start_line(node), _end_line(node)),
                        language=self.language,
                        extra={"is_trait": True},
                    ))
            for child in _children(node):
                visit(child)

        visit(tree.root_node())
        return nodes

    def extract_tests(self, tree, source: str) -> list[NodeInfo]:
        """Extract PHPUnit test methods."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, parent_class=None):
            if node.kind() == "method_declaration":
                name = self._get_name(node, source)
                if name and name.startswith("test"):
                    nodes.append(NodeInfo(
                        kind=NodeKind.TEST,
                        name=name,
                        file_path=fp,
                        range=SourceRange(_start_line(node), _end_line(node)),
                        language=self.language,
                        parent_name=parent_class,
                        is_test=True,
                    ))

            new_class = parent_class
            if node.kind() == "class_declaration":
                new_class = self._get_name(node, source)

            for child in _children(node):
                visit(child, new_class)

        visit(tree.root_node())
        return nodes

    # --- Helpers ---

    def _get_name(self, node, source: str) -> Optional[str]:
        for child in _children(node):
            if child.kind() == "name":
                return _extract_text(child, source)
        return None

    def _get_modifiers(self, node, source: str) -> list[str]:
        mods = []
        for child in _children(node):
            if child.kind() in ("public", "protected", "private", "static", "abstract", "final"):
                mods.append(child.kind())
        return mods

    def _get_call_target(self, node, source: str) -> Optional[str]:
        if node.child_count() > 0:
            first = node.child(0)
            if first.kind() == "name":
                return _extract_text(first, source)
            if first.kind() == "variable_name":
                return _extract_text(first, source)
        return None


def _extract_text(node, source: str) -> str:
    return source[node.start_byte():node.end_byte()].strip()
