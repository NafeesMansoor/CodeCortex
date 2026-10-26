"""JavaScript language parser implementation."""

from __future__ import annotations

from typing import Optional

from codecortex.core.parsers.treesitter_parser import TreeSitterParser
from codecortex.core.source_text import node_text
from codecortex.core.types import EdgeKind, NodeKind

# Function nodes that carry no name of their own; they take it from what they
# are bound to (`const handler = async () => {}`, `{ run: function () {} }`).
_ANONYMOUS_FUNCTIONS = frozenset({"arrow_function", "function_expression", "function"})


class JavaScriptParser(TreeSitterParser):
    """JavaScript code parser using tree-sitter."""

    language = "javascript"
    aliases = ["js"]
    extensions = [".js", ".jsx", ".mjs", ".cjs"]
    grammar = "javascript"
    function_types = frozenset(
        {"function_declaration", "generator_function_declaration", "method_definition"}
        | _ANONYMOUS_FUNCTIONS
    )
    type_kinds = {"class_declaration": NodeKind.CLASS}
    call_types = frozenset({"call_expression", "new_expression"})
    import_types = frozenset({"import_statement", "export_statement", "call_expression"})
    base_clauses = {"class_heritage": EdgeKind.INHERITS}

    def name_of(self, node, source: str) -> Optional[str]:
        named = node.child_by_field_name("name")
        if named is not None or node.type not in _ANONYMOUS_FUNCTIONS:
            return super().name_of(node, source)

        binding = node.parent
        if binding is None:
            return None
        if binding.type == "variable_declarator":
            target = binding.child_by_field_name("name")
        elif binding.type == "pair":
            target = binding.child_by_field_name("key")
        elif binding.type == "assignment_expression":
            target = binding.child_by_field_name("left")
        elif binding.type in ("public_field_definition", "field_definition"):
            target = binding.child_by_field_name("name") or binding.child_by_field_name("property")
        else:
            # A callback passed inline (`items.map(x => ...)`) has no name;
            # its calls are attributed to the enclosing function.
            return None
        return self.last_name(target, source) if target is not None else None

    def imports_of(self, node, source: str) -> list[str]:
        if node.type == "call_expression":
            # require("x") and dynamic import("x").
            callee = node.child_by_field_name("function")
            if callee is None or not (
                callee.type == "import"
                or (callee.type == "identifier" and node_text(callee, source) == "require")
            ):
                return []
            arguments = node.child_by_field_name("arguments")
            module = self.string_content(arguments, source) if arguments is not None else None
            return [module] if module else []

        # import ... from "x" / export ... from "x"
        module_node = node.child_by_field_name("source")
        module = self.string_content(module_node, source) if module_node is not None else None
        return [module] if module else []
