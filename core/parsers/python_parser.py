"""Python language parser implementation.

Extracts Python-specific structures: functions, classes, imports, decorators, etc.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.parser_framework import LanguageParser
from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange

logger = logging.getLogger(__name__)


class PythonParser(LanguageParser):
    """Python code parser using tree-sitter."""
    
    language = "python"
    extensions = [".py"]
    
    def parse(self, source: str, file_path: str) -> ParseResult:
        """Parse Python source code.

        Args:
            source: Python source code
            file_path: Path to file

        Returns:
            ParseResult with extracted nodes and edges
        """
        try:
            import tree_sitter_language_pack as tslp

            # Store file path for use in extract_* methods
            self._current_file_path = file_path

            # Get tree-sitter parser
            parser = tslp.get_parser("python")
            tree = parser.parse(source.encode("utf-8"))

            # Extract definitions and relationships
            nodes = self.extract_definitions(tree, source)
            nodes.extend(self.extract_types(tree, source))
            # extract_tests is subsumed by extract_definitions (test_ prefix detection)
            
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
            logger.error(f"Error parsing {file_path}: {e}")
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
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    params = self._extract_params(node, source)
                    is_test = name.startswith("test_") or (
                        parent_name and "Test" in parent_name
                    )

                    if is_test:
                        kind = NodeKind.TEST
                    elif inside_class:
                        kind = NodeKind.METHOD
                    else:
                        kind = NodeKind.FUNCTION

                    nodes.append(NodeInfo(
                        kind=kind,
                        name=name,
                        file_path=fp,
                        range=SourceRange(start_line, end_line),
                        language=self.language,
                        parent_name=parent_name,
                        params=params,
                        is_test=is_test,
                    ))
                    # Recurse into function body (nested functions)
                    for child in node.children:
                        visit(child, parent_name=name, inside_class=False)
                    return

            if node.type == "class_definition":
                class_name = self._get_identifier(node, source)
                for child in node.children:
                    visit(child, parent_name=class_name, inside_class=True)
                return

            for child in node.children:
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
                    edges.append(EdgeInfo(
                        kind=EdgeKind.CALLS,
                        source_qualified=enclosing_func,
                        target_qualified=target,
                        file_path=fp,
                        position=None,
                    ))
            
            # Track enclosing function
            new_func = enclosing_func
            if node.type == "function_definition":
                new_func = self._get_identifier(node, source)
            
            for child in node.children:
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
                    edges.append(EdgeInfo(
                        kind=EdgeKind.IMPORTS_FROM,
                        source_qualified="__main__",
                        target_qualified=module,
                        file_path=fp,
                    ))
            elif node.type == "import_from_statement":
                module = self._extract_from_import(node, source)
                if module:
                    edges.append(EdgeInfo(
                        kind=EdgeKind.IMPORTS_FROM,
                        source_qualified="__main__",
                        target_qualified=module,
                        file_path=fp,
                    ))
            
            for child in node.children:
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

                # Get base classes
                for child in node.children:
                    if child.type == "argument_list":
                        bases = self._extract_bases(child, source)
                        for base in bases:
                            edges.append(EdgeInfo(
                                kind=EdgeKind.INHERITS,
                                source_qualified=class_name,
                                target_qualified=base,
                                file_path=fp,
                            ))
            
            for child in node.children:
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
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    nodes.append(NodeInfo(
                        kind=NodeKind.CLASS,
                        name=name,
                        file_path=fp,
                        range=SourceRange(start_line, end_line),
                        language=self.language,
                    ))
            
            for child in node.children:
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
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    nodes.append(NodeInfo(
                        kind=NodeKind.TEST,
                        name=name,
                        file_path=fp,
                        range=SourceRange(start_line, end_line),
                        language=self.language,
                        is_test=True,
                    ))
            
            for child in node.children:
                visit(child)
        
        visit(tree.root_node)
        return nodes
    
    # Helper methods
    def _get_identifier(self, node, source: str) -> Optional[str]:
        """Extract identifier name from tree-sitter node."""
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte:child.end_byte].decode("utf-8") if isinstance(source, bytes) else source[child.start_byte:child.end_byte]
        return None
    
    def _extract_params(self, node, source: str) -> Optional[list[str]]:
        """Extract function parameters."""
        params = []
        for child in node.children:
            if child.type == "parameters":
                for param in child.children:
                    if param.type == "identifier":
                        param_text = source[param.start_byte:param.end_byte]
                        if isinstance(param_text, bytes):
                            param_text = param_text.decode("utf-8")
                        params.append(param_text)
        return params if params else None
    
    def _get_call_target(self, node, source: str) -> Optional[str]:
        """Extract the function being called."""
        if node.children:
            func_node = node.children[0]
            if func_node.type == "identifier":
                return source[func_node.start_byte:func_node.end_byte].decode("utf-8") if isinstance(source, bytes) else source[func_node.start_byte:func_node.end_byte]
        return None
    
    def _extract_import_module(self, node, source: str) -> Optional[str]:
        """Extract module name from import statement."""
        for child in node.children:
            if child.type == "dotted_name" or child.type == "identifier":
                return source[child.start_byte:child.end_byte].decode("utf-8") if isinstance(source, bytes) else source[child.start_byte:child.end_byte]
        return None
    
    def _extract_from_import(self, node, source: str) -> Optional[str]:
        """Extract module name from 'from X import Y' statement."""
        for child in node.children:
            if child.type == "dotted_name" or child.type == "identifier":
                return source[child.start_byte:child.end_byte].decode("utf-8") if isinstance(source, bytes) else source[child.start_byte:child.end_byte]
        return None
    
    def _extract_bases(self, node, source: str) -> list[str]:
        """Extract base class names."""
        bases = []
        for child in node.children:
            if child.type == "identifier":
                base = source[child.start_byte:child.end_byte]
                if isinstance(base, bytes):
                    base = base.decode("utf-8")
                bases.append(base)
        return bases
