"""Table-driven parsers for the languages beyond the original four.

Node-type names come from each language's tree-sitter grammar wheel (see
``grammars._WHEELS``); ``tests/test_languages.py`` pins what each one extracts.
"""

from __future__ import annotations

from codecortex.core.parsers.treesitter_parser import TreeSitterParser, _children, _named_children
from codecortex.core.source_text import node_text
from codecortex.core.types import EdgeKind, NodeKind


class GoParser(TreeSitterParser):
    language = "go"
    aliases = ["golang"]
    extensions = [".go"]
    grammar = "go"
    function_types = frozenset({"function_declaration", "method_declaration"})
    type_kinds = {"type_spec": NodeKind.TYPE}
    call_types = frozenset({"call_expression"})
    import_types = frozenset({"import_spec"})

    def container(self, node, source):
        opened = super().container(node, source)
        if opened is None:
            return None
        body = node.child_by_field_name("type")
        if body is not None and body.type == "struct_type":
            return NodeKind.STRUCT, opened[1]
        if body is not None and body.type == "interface_type":
            return NodeKind.INTERFACE, opened[1]
        return opened

    def owner_of(self, node, source):
        # func (s *Server) Handle() — the receiver type owns the method.
        receiver = node.child_by_field_name("receiver")
        if receiver is None:
            return None
        for param in _named_children(receiver):
            declared = param.child_by_field_name("type")
            if declared is not None:
                return self.type_name(declared, source)
        return None

    def is_test(self, node, name, source, file_path):
        return file_path.endswith("_test.go") and name.startswith(
            ("Test", "Benchmark", "Example", "Fuzz")
        )


class RustParser(TreeSitterParser):
    language = "rust"
    aliases = ["rs"]
    extensions = [".rs"]
    grammar = "rust"
    function_types = frozenset({"function_item"})
    type_kinds = {
        "struct_item": NodeKind.STRUCT,
        "enum_item": NodeKind.ENUM,
        "union_item": NodeKind.STRUCT,
        "trait_item": NodeKind.INTERFACE,
        "type_item": NodeKind.TYPE,
    }
    call_types = frozenset({"call_expression"})
    import_types = frozenset({"use_declaration"})

    def container(self, node, source):
        if node.type == "impl_item":
            # `impl Trait for Type` adds methods to Type without defining it.
            target = node.child_by_field_name("type")
            name = self.type_name(target, source) if target is not None else None
            return (None, name) if name else None
        return super().container(node, source)

    def bases_of(self, node, source):
        if node.type != "impl_item":
            return []
        trait = node.child_by_field_name("trait")
        name = self.type_name(trait, source) if trait is not None else None
        return [(EdgeKind.IMPLEMENTS, name)] if name else []

    def imports_of(self, node, source):
        argument = node.child_by_field_name("argument")
        if argument is None:
            return []
        # `use std::io::{self, Read}` → std::io
        path = node_text(argument, source).split("::{", 1)[0]
        return [path.split(" as ", 1)[0].strip()]

    def is_test(self, node, name, source, file_path):
        return "test" in self.decorations(node, source)


class JavaParser(TreeSitterParser):
    language = "java"
    extensions = [".java"]
    grammar = "java"
    function_types = frozenset({"method_declaration", "constructor_declaration"})
    type_kinds = {
        "class_declaration": NodeKind.CLASS,
        "record_declaration": NodeKind.CLASS,
        "interface_declaration": NodeKind.INTERFACE,
        "annotation_type_declaration": NodeKind.INTERFACE,
        "enum_declaration": NodeKind.ENUM,
    }
    call_types = frozenset({"method_invocation", "object_creation_expression"})
    import_types = frozenset({"import_declaration"})
    base_clauses = {
        "superclass": EdgeKind.INHERITS,
        "super_interfaces": EdgeKind.IMPLEMENTS,
        "extends_interfaces": EdgeKind.INHERITS,
    }


class CSharpParser(TreeSitterParser):
    language = "csharp"
    aliases = ["cs", "c#"]
    extensions = [".cs"]
    grammar = "csharp"
    function_types = frozenset(
        {"method_declaration", "constructor_declaration", "local_function_statement"}
    )
    type_kinds = {
        "class_declaration": NodeKind.CLASS,
        "record_declaration": NodeKind.CLASS,
        "interface_declaration": NodeKind.INTERFACE,
        "struct_declaration": NodeKind.STRUCT,
        "enum_declaration": NodeKind.ENUM,
    }
    call_types = frozenset({"invocation_expression", "object_creation_expression"})
    import_types = frozenset({"using_directive"})

    def bases_of(self, node, source):
        # C# lists the base class and interfaces in one `base_list`; by
        # convention interfaces are named IThing.
        found = []
        for child in _named_children(node):
            if child.type != "base_list":
                continue
            for base in _named_children(child):
                name = self.type_name(base, source)
                if not name:
                    continue
                interface = len(name) > 1 and name[0] == "I" and name[1].isupper()
                found.append((EdgeKind.IMPLEMENTS if interface else EdgeKind.INHERITS, name))
        return found


class CParser(TreeSitterParser):
    language = "c"
    extensions = [".c", ".h"]
    grammar = "c"
    function_types = frozenset({"function_definition"})
    type_kinds = {
        "struct_specifier": NodeKind.STRUCT,
        "union_specifier": NodeKind.STRUCT,
        "enum_specifier": NodeKind.ENUM,
    }
    call_types = frozenset({"call_expression"})
    import_types = frozenset({"preproc_include"})

    def container(self, node, source):
        # `struct P *p;` mentions a struct without defining it.
        if node.type in self.type_kinds and node.child_by_field_name("body") is None:
            return None
        return super().container(node, source)


class CppParser(CParser):
    language = "cpp"
    aliases = ["c++", "cxx"]
    extensions = [".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++"]
    grammar = "cpp"
    type_kinds = {
        "class_specifier": NodeKind.CLASS,
        "struct_specifier": NodeKind.STRUCT,
        "union_specifier": NodeKind.STRUCT,
        "enum_specifier": NodeKind.ENUM,
    }
    base_clauses = {"base_class_clause": EdgeKind.INHERITS}

    def owner_of(self, node, source):
        # void Widget::draw() { ... } — defined out of line, owned by Widget.
        declarator = node.child_by_field_name("declarator")
        while declarator is not None:
            if declarator.type == "qualified_identifier":
                scope = declarator.child_by_field_name("scope")
                return self.last_name(scope, source) if scope is not None else None
            declarator = declarator.child_by_field_name("declarator")
        return None


class RubyParser(TreeSitterParser):
    language = "ruby"
    aliases = ["rb"]
    extensions = [".rb", ".rake"]
    grammar = "ruby"
    function_types = frozenset({"method", "singleton_method"})
    type_kinds = {"class": NodeKind.CLASS, "module": NodeKind.MODULE}
    call_types = frozenset({"call"})
    import_types = frozenset({"call"})
    base_clauses = {"superclass": EdgeKind.INHERITS}

    _REQUIRES = frozenset({"require", "require_relative", "load"})

    def imports_of(self, node, source):
        method = node.child_by_field_name("method")
        if method is None or node_text(method, source) not in self._REQUIRES:
            return []
        arguments = node.child_by_field_name("arguments")
        path = self.string_content(arguments, source) if arguments is not None else None
        return [path] if path else []


class KotlinParser(TreeSitterParser):
    language = "kotlin"
    aliases = ["kt"]
    extensions = [".kt", ".kts"]
    grammar = "kotlin"
    function_types = frozenset({"function_declaration", "secondary_constructor"})
    type_kinds = {"class_declaration": NodeKind.CLASS, "object_declaration": NodeKind.CLASS}
    call_types = frozenset({"call_expression"})
    import_types = frozenset({"import"})

    def container(self, node, source):
        opened = super().container(node, source)
        if opened is None or node.type != "class_declaration":
            return opened
        keywords = {c.type for c in _children(node) if not c.is_named}
        if "interface" in keywords:
            return NodeKind.INTERFACE, opened[1]
        modifiers = next((c for c in _children(node) if c.type == "modifiers"), None)
        if modifiers is not None and "enum" in node_text(modifiers, source).split():
            return NodeKind.ENUM, opened[1]
        return opened

    def bases_of(self, node, source):
        # `class A : Base(), Iface` — a constructor call marks the superclass.
        found = []
        for child in _named_children(node):
            if child.type != "delegation_specifiers":
                continue
            for spec in _named_children(child):
                name = self.type_name(spec, source)
                if not name:
                    continue
                calls_constructor = any(
                    c.type == "constructor_invocation" for c in _named_children(spec)
                )
                found.append(
                    (EdgeKind.INHERITS if calls_constructor else EdgeKind.IMPLEMENTS, name)
                )
        return found

    def imports_of(self, node, source):
        path = next((c for c in _named_children(node) if c.type != "comment"), None)
        return [node_text(path, source)] if path is not None else []


class SwiftParser(TreeSitterParser):
    language = "swift"
    extensions = [".swift"]
    grammar = "swift"
    function_types = frozenset({"function_declaration", "init_declaration"})
    type_kinds = {"class_declaration": NodeKind.CLASS, "protocol_declaration": NodeKind.INTERFACE}
    call_types = frozenset({"call_expression"})
    import_types = frozenset({"import_declaration"})
    base_clauses = {"inheritance_specifier": EdgeKind.INHERITS}

    # `class_declaration` covers every nominal type; its keyword says which.
    _KEYWORD_KINDS = {
        "class": NodeKind.CLASS,
        "actor": NodeKind.CLASS,
        "struct": NodeKind.STRUCT,
        "enum": NodeKind.ENUM,
        "extension": None,
    }

    def container(self, node, source):
        opened = super().container(node, source)
        if opened is None or node.type != "class_declaration":
            return opened
        for child in _children(node):
            if not child.is_named and child.type in self._KEYWORD_KINDS:
                return self._KEYWORD_KINDS[child.type], opened[1]
        return opened

    def name_of(self, node, source):
        if node.type == "init_declaration":
            return "init"
        return super().name_of(node, source)

    def imports_of(self, node, source):
        path = next((c for c in _named_children(node) if c.type == "identifier"), None)
        return [node_text(path, source)] if path is not None else []


class ScalaParser(TreeSitterParser):
    language = "scala"
    extensions = [".scala", ".sc"]
    grammar = "scala"
    function_types = frozenset({"function_definition"})
    type_kinds = {
        "class_definition": NodeKind.CLASS,
        "object_definition": NodeKind.CLASS,
        "trait_definition": NodeKind.INTERFACE,
        "enum_definition": NodeKind.ENUM,
    }
    call_types = frozenset({"call_expression"})
    import_types = frozenset({"import_declaration"})
    base_clauses = {"extends_clause": EdgeKind.INHERITS}

    def imports_of(self, node, source):
        # The path is split into one `path` field per segment.
        path = node_text(node, source).strip()
        path = path[len("import") :].strip() if path.startswith("import") else path
        return [path] if path else []


class LuaParser(TreeSitterParser):
    language = "lua"
    extensions = [".lua"]
    grammar = "lua"
    function_types = frozenset({"function_declaration"})
    call_types = frozenset({"function_call"})
    import_types = frozenset({"function_call"})

    def owner_of(self, node, source):
        # function M.f() / function M:g() — the table owns the function.
        named = node.child_by_field_name("name")
        if named is None or named.type not in ("dot_index_expression", "method_index_expression"):
            return None
        table = named.child_by_field_name("table")
        return self.last_name(table, source) if table is not None else None

    def imports_of(self, node, source):
        callee = node.child_by_field_name("name")
        if callee is None or node_text(callee, source) != "require":
            return []
        arguments = node.child_by_field_name("arguments")
        path = self.string_content(arguments, source) if arguments is not None else None
        return [path] if path else []


class BashParser(TreeSitterParser):
    language = "bash"
    aliases = ["sh", "shell"]
    extensions = [".sh", ".bash"]
    grammar = "bash"
    function_types = frozenset({"function_definition"})
    call_types = frozenset({"command"})
    import_types = frozenset({"command"})

    def imports_of(self, node, source):
        callee = node.child_by_field_name("name")
        if callee is None or node_text(callee, source) not in ("source", "."):
            return []
        argument = node.child_by_field_name("argument")
        return [node_text(argument, source).strip("\"'")] if argument is not None else []

    def is_test(self, node, name, source, file_path):
        return name.startswith("test_")
