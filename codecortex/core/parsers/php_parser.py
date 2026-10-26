"""PHP language parser — covers Laravel applications.

Extracts:
  - Classes (Eloquent models, controllers, services, middleware), enums
  - Methods (instance and static) and functions
  - Interfaces and traits (traits are CLASS nodes with ``extra["is_trait"]``)
  - Use statements, grouped uses and require/include (imports)
  - Inheritance, interface implementation and trait use
"""

from __future__ import annotations

from codecortex.core.parsers.treesitter_parser import TreeSitterParser, _named_children
from codecortex.core.source_text import node_text
from codecortex.core.types import EdgeKind, NodeKind

_INCLUDES = frozenset(
    {
        "require_expression",
        "require_once_expression",
        "include_expression",
        "include_once_expression",
    }
)


class PHPParser(TreeSitterParser):
    """PHP parser using tree-sitter — tuned for Laravel conventions."""

    language = "php"
    extensions = [".php"]
    grammar = "php"
    function_types = frozenset({"function_definition", "method_declaration"})
    type_kinds = {
        "class_declaration": NodeKind.CLASS,
        "interface_declaration": NodeKind.INTERFACE,
        "trait_declaration": NodeKind.CLASS,
        "enum_declaration": NodeKind.ENUM,
    }
    call_types = frozenset(
        {
            "function_call_expression",
            "member_call_expression",
            "nullsafe_member_call_expression",
            "scoped_call_expression",
            "object_creation_expression",
        }
    )
    import_types = frozenset({"namespace_use_declaration"}) | _INCLUDES
    base_clauses = {
        "base_clause": EdgeKind.INHERITS,
        "class_interface_clause": EdgeKind.IMPLEMENTS,
    }

    def extra_of(self, node, source):
        return {"is_trait": True} if node.type == "trait_declaration" else {}

    def bases_of(self, node, source):
        found = super().bases_of(node, source)
        # `use HasFactory, Notifiable;` inside a class body mixes traits in.
        body = node.child_by_field_name("body")
        for member in _named_children(body) if body is not None else []:
            if member.type != "use_declaration":
                continue
            for trait in _named_children(member):
                name = self.type_name(trait, source)
                if name:
                    found.append((EdgeKind.EXTENDS, name))
        return found

    def imports_of(self, node, source):
        if node.type in _INCLUDES:
            path = self.string_content(node, source)
            return [path] if path else []

        # use App\Models\User;  /  use Illuminate\Support\{Str, Arr};
        prefix = ""
        modules = []
        for child in _named_children(node):
            if child.type == "namespace_name":
                prefix = node_text(child, source) + "\\"
            elif child.type == "namespace_use_clause":
                modules.append(self._use_clause(child, source))
            elif child.type == "namespace_use_group":
                modules.extend(
                    prefix + self._use_clause(clause, source)
                    for clause in _named_children(child)
                    if clause.type == "namespace_use_clause"
                )
        return [m for m in modules if m and m != prefix]

    @staticmethod
    def _use_clause(clause, source) -> str:
        # The imported name is the first child; an `as` alias follows it.
        named = _named_children(clause)
        return node_text(named[0], source) if named else ""
