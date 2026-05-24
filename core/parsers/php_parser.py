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


class PHPParser(LanguageParser):
    """PHP parser using tree-sitter — tuned for Laravel conventions."""

    language = "php"
    extensions = [".php"]

    def parse(self, source: str, file_path: str) -> ParseResult:
        try:
            import tree_sitter_language_pack as tslp

            self._current_file_path = file_path
            parser = tslp.get_parser("php")
            tree = parser.parse(source.encode("utf-8"))

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
            if node.type == "function_definition":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.FUNCTION,
                        name=name,
                        file_path=fp,
                        range=SourceRange(node.start_point[0] + 1, node.end_point[0] + 1),
                        language=self.language,
                        parent_name=parent_class,
                    ))

            if node.type == "method_declaration":
                name = self._get_name(node, source)
                if name:
                    modifiers = self._get_modifiers(node, source)
                    is_test = name.startswith("test")
                    nodes.append(NodeInfo(
                        kind=NodeKind.TEST if is_test else NodeKind.FUNCTION,
                        name=name,
                        file_path=fp,
                        range=SourceRange(node.start_point[0] + 1, node.end_point[0] + 1),
                        language=self.language,
                        parent_name=parent_class,
                        modifiers=modifiers,
                        is_test=is_test,
                    ))

            new_class = parent_class
            if node.type == "class_declaration":
                new_class = self._get_name(node, source)

            for child in node.children:
                visit(child, new_class)

        visit(tree.root_node)
        return nodes

    def extract_calls(self, tree, source: str) -> list[EdgeInfo]:
        """Extract method / function calls."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, enclosing=None):
            if node.type in ("function_call_expression", "member_call_expression"):
                target = self._get_call_target(node, source)
                if target and enclosing:
                    edges.append(EdgeInfo(
                        kind=EdgeKind.CALLS,
                        source_qualified=enclosing,
                        target_qualified=target,
                        file_path=fp,
                    ))

            new_enc = enclosing
            if node.type in ("function_definition", "method_declaration"):
                new_enc = self._get_name(node, source) or enclosing

            for child in node.children:
                visit(child, new_enc)

        visit(tree.root_node)
        return edges

    def extract_imports(self, tree, source: str) -> list[EdgeInfo]:
        """Extract use / require / include statements."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "use_declaration":
                for child in node.children:
                    if child.type == "use_class_name_list":
                        for use_name in child.children:
                            if use_name.type == "name":
                                target = _extract_text(use_name, source)
                                if target:
                                    edges.append(EdgeInfo(
                                        kind=EdgeKind.IMPORTS_FROM,
                                        source_qualified="__module__",
                                        target_qualified=target,
                                        file_path=fp,
                                    ))
                    elif child.type == "qualified_name":
                        target = _extract_text(child, source)
                        if target:
                            edges.append(EdgeInfo(
                                kind=EdgeKind.IMPORTS_FROM,
                                source_qualified="__module__",
                                target_qualified=target,
                                file_path=fp,
                            ))
            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return edges

    def extract_inheritance(self, tree, source: str) -> list[EdgeInfo]:
        """Extract extends and implements relationships."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "class_declaration":
                class_name = self._get_name(node, source)
                for child in node.children:
                    if child.type == "base_clause":
                        for base in child.children:
                            if base.type in ("qualified_name", "name"):
                                base_name = _extract_text(base, source)
                                if base_name and class_name:
                                    edges.append(EdgeInfo(
                                        kind=EdgeKind.INHERITS,
                                        source_qualified=class_name,
                                        target_qualified=base_name,
                                        file_path=fp,
                                    ))
                    elif child.type == "class_implements":
                        for iface in child.children:
                            if iface.type in ("qualified_name", "name"):
                                iface_name = _extract_text(iface, source)
                                if iface_name and class_name:
                                    edges.append(EdgeInfo(
                                        kind=EdgeKind.IMPLEMENTS,
                                        source_qualified=class_name,
                                        target_qualified=iface_name,
                                        file_path=fp,
                                    ))
            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return edges

    def extract_types(self, tree, source: str) -> list[NodeInfo]:
        """Extract class, interface, and trait declarations."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "class_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.CLASS,
                        name=name,
                        file_path=fp,
                        range=SourceRange(node.start_point[0] + 1, node.end_point[0] + 1),
                        language=self.language,
                    ))
            elif node.type == "interface_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.INTERFACE,
                        name=name,
                        file_path=fp,
                        range=SourceRange(node.start_point[0] + 1, node.end_point[0] + 1),
                        language=self.language,
                    ))
            elif node.type == "trait_declaration":
                name = self._get_name(node, source)
                if name:
                    nodes.append(NodeInfo(
                        kind=NodeKind.CLASS,    # Traits map to CLASS for graph purposes
                        name=name,
                        file_path=fp,
                        range=SourceRange(node.start_point[0] + 1, node.end_point[0] + 1),
                        language=self.language,
                        extra={"is_trait": True},
                    ))
            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return nodes

    def extract_tests(self, tree, source: str) -> list[NodeInfo]:
        """Extract PHPUnit test methods."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, parent_class=None):
            if node.type == "method_declaration":
                name = self._get_name(node, source)
                if name and name.startswith("test"):
                    nodes.append(NodeInfo(
                        kind=NodeKind.TEST,
                        name=name,
                        file_path=fp,
                        range=SourceRange(node.start_point[0] + 1, node.end_point[0] + 1),
                        language=self.language,
                        parent_name=parent_class,
                        is_test=True,
                    ))

            new_class = parent_class
            if node.type == "class_declaration":
                new_class = self._get_name(node, source)

            for child in node.children:
                visit(child, new_class)

        visit(tree.root_node)
        return nodes

    # --- Helpers ---

    def _get_name(self, node, source: str) -> Optional[str]:
        for child in node.children:
            if child.type == "name":
                return _extract_text(child, source)
        return None

    def _get_modifiers(self, node, source: str) -> list[str]:
        mods = []
        for child in node.children:
            if child.type in ("public", "protected", "private", "static", "abstract", "final"):
                mods.append(child.type)
        return mods

    def _get_call_target(self, node, source: str) -> Optional[str]:
        if node.children:
            first = node.children[0]
            if first.type == "name":
                return _extract_text(first, source)
            if first.type == "variable_name":
                return _extract_text(first, source)
        return None


def _extract_text(node, source: str) -> str:
    text = source[node.start_byte:node.end_byte]
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return text.strip()
