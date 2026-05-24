"""TypeScript language parser implementation."""

from __future__ import annotations

from core.parser_framework import LanguageParser
from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange


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
            tree = parser.parse(source.encode("utf-8"))
            
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
            if node.type in ("function_declaration", "arrow_function", "method_definition"):
                name = self._get_identifier(node, source)
                if name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    nodes.append(NodeInfo(
                        kind=NodeKind.FUNCTION,
                        name=name,
                        file_path=fp,
                        range=SourceRange(start_line, end_line),
                        language=self.language,
                    ))
            
            for child in node.children:
                visit(child)
        
        visit(tree.root_node)
        return nodes
    
    def extract_calls(self, tree, source: str) -> list[EdgeInfo]:
        """Extract function calls."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node, enclosing_func=None):
            if node.type == "call_expression":
                target = self._get_call_target(node, source)
                if target and enclosing_func:
                    edges.append(EdgeInfo(
                        kind=EdgeKind.CALLS,
                        source_qualified=enclosing_func,
                        target_qualified=target,
                        file_path=fp,
                    ))
            
            new_func = enclosing_func
            if node.type in ("function_declaration", "method_definition"):
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
                        source_qualified="module",
                        target_qualified=module,
                        file_path=fp,
                    ))
            
            for child in node.children:
                visit(child)
        
        visit(tree.root_node)
        return edges
    
    def extract_inheritance(self, tree, source: str) -> list[EdgeInfo]:
        """Extract class inheritance and interface implementation."""
        edges = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "class_declaration":
                class_name = self._get_identifier(node, source)

                # Look for extends/implements
                for child in node.children:
                    if child.type == "class_heritage":
                        for heritage in child.children:
                            if heritage.type == "identifier":
                                base = source[heritage.start_byte:heritage.end_byte].decode("utf-8") if isinstance(source, bytes) else source[heritage.start_byte:heritage.end_byte]
                                edge_kind = EdgeKind.INHERITS
                                edges.append(EdgeInfo(
                                    kind=edge_kind,
                                    source_qualified=class_name,
                                    target_qualified=base,
                                    file_path=fp,
                                ))
            
            for child in node.children:
                visit(child)
        
        visit(tree.root_node)
        return edges
    
    def extract_types(self, tree, source: str) -> list[NodeInfo]:
        """Extract class and interface definitions."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type in ("class_declaration", "interface_declaration"):
                name = self._get_identifier(node, source)
                if name:
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1

                    kind = NodeKind.CLASS if node.type == "class_declaration" else NodeKind.INTERFACE

                    nodes.append(NodeInfo(
                        kind=kind,
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
        """Extract test functions."""
        nodes = []
        fp = getattr(self, "_current_file_path", "")

        def visit(node):
            if node.type == "function_declaration":
                name = self._get_identifier(node, source)
                if name and (name.startswith("test") or name.startswith("it") or name.startswith("describe")):
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
    
    # Helpers
    def _get_identifier(self, node, source: str) -> str | None:
        """Extract identifier."""
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte:child.end_byte].decode("utf-8") if isinstance(source, bytes) else source[child.start_byte:child.end_byte]
        return None
    
    def _get_call_target(self, node, source: str) -> str | None:
        """Extract call target."""
        if node.children:
            target_node = node.children[0]
            if target_node.type == "identifier":
                return source[target_node.start_byte:target_node.end_byte].decode("utf-8") if isinstance(source, bytes) else source[target_node.start_byte:target_node.end_byte]
        return None
    
    def _extract_import_module(self, node, source: str) -> str | None:
        """Extract imported module."""
        for child in node.children:
            if child.type == "string":
                return source[child.start_byte+1:child.end_byte-1].decode("utf-8") if isinstance(source, bytes) else source[child.start_byte+1:child.end_byte-1]
        return None
