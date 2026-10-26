"""TypeScript language parser implementation."""

from __future__ import annotations

from codecortex.core.parsers.javascript_parser import JavaScriptParser
from codecortex.core.types import EdgeKind, NodeKind


class TypeScriptParser(JavaScriptParser):
    """TypeScript code parser using tree-sitter."""

    language = "typescript"
    aliases = ["ts"]
    extensions = [".ts", ".tsx", ".mts", ".cts"]
    grammar = "typescript"
    type_kinds = {
        "class_declaration": NodeKind.CLASS,
        "abstract_class_declaration": NodeKind.CLASS,
        "interface_declaration": NodeKind.INTERFACE,
        "enum_declaration": NodeKind.ENUM,
        "type_alias_declaration": NodeKind.TYPE,
    }
    base_clauses = {
        "class_heritage": EdgeKind.INHERITS,
        "extends_clause": EdgeKind.INHERITS,
        "implements_clause": EdgeKind.IMPLEMENTS,
        "extends_type_clause": EdgeKind.INHERITS,
    }

    def grammar_for(self, file_path: str) -> str:
        # ".tsx" needs the "tsx" grammar variant — the plain "typescript"
        # grammar has no JSX productions and turns JSX into ERROR nodes.
        return "tsx" if file_path.endswith(".tsx") else "typescript"
