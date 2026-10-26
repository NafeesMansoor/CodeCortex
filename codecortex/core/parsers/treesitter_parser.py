"""Table-driven tree-sitter parser shared by most supported languages.

Each language subclass names the grammar node types that define functions,
types, calls, imports and base-type clauses; one walk over the tree turns
them into the same NodeInfo/EdgeInfo shapes the hand-written parsers emit.
Grammar quirks that a table cannot express (Go method receivers, Rust
``impl`` blocks, Kotlin interfaces) are handled by overriding a hook.

Conventions shared with the other parsers:
  - a function inside a type body is a METHOD, otherwise a FUNCTION; one
    recognised as a test is a TEST
  - CALLS edges run from the enclosing function's qualified name to the
    callee's bare name (calls outside any function are not recorded)
  - IMPORTS_FROM edges run from the file to the imported module or path
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from codecortex.core.parser_framework import LanguageParser
from codecortex.core.source_text import SourceText, node_text
from codecortex.core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange

logger = logging.getLogger(__name__)

# Leaf node types that carry a symbol name, across the supported grammars.
NAME_TYPES = frozenset(
    {
        "identifier",
        "type_identifier",
        "field_identifier",
        "property_identifier",
        "private_property_identifier",
        "simple_identifier",
        "namespace_identifier",
        "constant",
        "word",
        "name",
        "destructor_name",
        "operator_name",
    }
)

# Fields that point at the meaningful part of a callee or name expression,
# most specific first: `obj.method` → method, `Foo::new` → new.
_NAME_FIELDS = ("field", "property", "method", "name", "suffix", "constructor", "type")

# Children that never name a callee or a base type.
_SKIP_TYPES = frozenset(
    {
        "arguments",
        "argument_list",
        "value_arguments",
        "type_arguments",
        "type_parameters",
        "access_specifier",
        "call_suffix",
        "comment",
    }
)

# Wrappers whose rightmost name is the one that matters (`a.b.C` → C).
_DOTTED = re.compile(r"scoped|qualified|navigation|member|selector|field_expression|index")

# Annotation and attribute words that mark a test across test frameworks.
_TEST_MARKERS = frozenset({"test", "Test", "Fact", "Theory", "TestMethod", "ParameterizedTest"})

_DECORATION_TYPES = frozenset(
    {"modifiers", "modifier", "attribute_list", "attribute_item", "annotation", "marker_annotation"}
)

_IMPORT_KEYWORDS = re.compile(r"^\s*(?:import|using|use|static|global|package)\s+")
_QUOTES = "\"'`<>"


def _children(node) -> list:
    return [node.child(i) for i in range(node.child_count)]


def _named_children(node) -> list:
    return [c for c in _children(node) if c.is_named]


class TreeSitterParser(LanguageParser):
    """Base for languages described by node-type tables.

    Subclasses set ``language``, ``extensions`` and ``grammar`` plus the
    tables below, and override a hook only where a grammar needs it.
    """

    grammar: str = ""
    # Definitions that become FUNCTION / METHOD / TEST nodes.
    function_types: frozenset[str] = frozenset()
    # Type definitions → the node kind they produce.
    type_kinds: dict[str, NodeKind] = {}
    call_types: frozenset[str] = frozenset()
    import_types: frozenset[str] = frozenset()
    # Base-type clauses (direct or nested children of a type) → edge kind.
    base_clauses: dict[str, EdgeKind] = {}

    # ------------------------------------------------------------------
    # LanguageParser API
    # ------------------------------------------------------------------

    def grammar_for(self, file_path: str) -> str:
        return self.grammar

    def parse(self, source: str, file_path: str) -> ParseResult:
        try:
            from codecortex.core.parsers.grammars import get_parser

            parser = get_parser(self.grammar_for(file_path))
            # Wrap once so every byte-offset slice below is O(1) and correct,
            # and parse the very bytes those offsets index into.
            source = SourceText(source)
            tree = parser.parse(source.as_bytes())
            nodes, calls, imports, bases = self._extract(tree, source, file_path)

            result = ParseResult(
                language=self.language,
                file_path=file_path,
                nodes=nodes,
                edges=calls + imports + bases,
            )
            result._tree = tree
            result._source = source
            return result
        except Exception as e:
            logger.error("Error parsing %s: %s", file_path, e)
            return ParseResult(language=self.language, file_path=file_path, errors=[str(e)])

    def extract_definitions(self, tree, source: str) -> list[NodeInfo]:
        return self._extract(tree, source, "")[0]

    def extract_calls(self, tree, source: str) -> list[EdgeInfo]:
        return self._extract(tree, source, "")[1]

    def extract_imports(self, tree, source: str) -> list[EdgeInfo]:
        return self._extract(tree, source, "")[2]

    def extract_inheritance(self, tree, source: str) -> list[EdgeInfo]:
        return self._extract(tree, source, "")[3]

    # ------------------------------------------------------------------
    # Hooks — override where a grammar does not fit the tables
    # ------------------------------------------------------------------

    def container(self, node, source: str) -> Optional[tuple[Optional[NodeKind], str]]:
        """(kind, name) when `node` opens a type scope, else None.

        A None kind opens a scope without creating a node — a Rust ``impl``
        or a Swift ``extension`` adds methods to a type defined elsewhere.
        """
        kind = self.type_kinds.get(node.type)
        if kind is None:
            return None
        name = self.name_of(node, source)
        return (kind, name) if name else None

    def name_of(self, node, source: str) -> Optional[str]:
        """The declared name of a definition node."""
        named = node.child_by_field_name("name")
        if named is not None:
            return self.last_name(named, source)
        # C and C++ bury the name in a declarator chain:
        # pointer_declarator → function_declarator → identifier.
        declarator = node.child_by_field_name("declarator")
        while declarator is not None:
            if declarator.type in NAME_TYPES or _DOTTED.search(declarator.type):
                return self.last_name(declarator, source)
            inner = declarator.child_by_field_name("declarator")
            if inner is None:
                return self.last_name(declarator, source)
            declarator = inner
        for child in _named_children(node):
            if child.type in NAME_TYPES:
                return node_text(child, source)
        return None

    def extra_of(self, node, source: str) -> dict:
        """Language-specific data stored on a type node's ``extra``."""
        return {}

    def owner_of(self, node, source: str) -> Optional[str]:
        """Type a function belongs to when that is not its lexical scope."""
        return None

    def callee_of(self, node, source: str) -> Optional[str]:
        """Bare name of the function a call node invokes."""
        for field in ("function", "name", "method", "constructor", "type"):
            target = node.child_by_field_name(field)
            if target is not None:
                return self.last_name(target, source)
        named = _named_children(node)
        return self.last_name(named[0], source) if named else None

    def imports_of(self, node, source: str) -> list[str]:
        """Modules or paths an import node pulls in."""
        path = node.child_by_field_name("path") or node.child_by_field_name("source")
        text = node_text(path if path is not None else node, source)
        text = _IMPORT_KEYWORDS.sub("", text.strip()).rstrip(";").strip()
        if "=" in text:  # C# alias: `using Json = System.Text.Json`
            text = text.split("=", 1)[1].strip()
        text = text.strip(_QUOTES)
        return [text] if text else []

    def is_test(self, node, name: str, source: str, file_path: str) -> bool:
        if name.startswith(("test_", "test", "Test")):
            return True
        return bool(self.decorations(node, source) & _TEST_MARKERS)

    # ------------------------------------------------------------------
    # Helpers available to subclasses
    # ------------------------------------------------------------------

    def last_name(self, node, source: str) -> Optional[str]:
        """Rightmost meaningful name in an expression (`a.b.c` → c)."""
        if node.type in NAME_TYPES:
            return node_text(node, source)
        for field in _NAME_FIELDS:
            inner = node.child_by_field_name(field)
            if inner is not None:
                return self.last_name(inner, source)
        for child in reversed(_named_children(node)):
            if child.type in _SKIP_TYPES:
                continue
            found = self.last_name(child, source)
            if found:
                return found
        return None

    def type_name(self, node, source: str) -> Optional[str]:
        """Name of a type reference: `Base<T>` → Base, `pkg.Base` → Base."""
        if node.type in NAME_TYPES:
            return node_text(node, source)
        if _DOTTED.search(node.type):
            return self.last_name(node, source)
        named = node.child_by_field_name("name")
        if named is not None:
            return self.type_name(named, source)
        for child in _named_children(node):
            if child.type in _SKIP_TYPES:
                continue
            found = self.type_name(child, source)
            if found:
                return found
        return None

    def string_content(self, node, source: str) -> Optional[str]:
        """Text of the first string literal at or under `node`, unquoted."""
        if "string" in node.type:
            text = node_text(node, source).strip()
            return text.strip(_QUOTES) or None
        for child in _named_children(node):
            found = self.string_content(child, source)
            if found:
                return found
        return None

    def decorations(self, node, source: str) -> set[str]:
        """Annotation / attribute words on a definition (`@Test`, `#[test]`)."""
        decorators = [c for c in _children(node) if c.type in _DECORATION_TYPES]
        sibling = node.prev_named_sibling
        while sibling is not None and sibling.type in _DECORATION_TYPES:
            decorators.append(sibling)
            sibling = sibling.prev_named_sibling
        words: set[str] = set()
        for decorator in decorators:
            words.update(re.findall(r"[A-Za-z_]\w*", node_text(decorator, source)))
        return words

    def bases_of(self, node, source: str) -> list[tuple[EdgeKind, str]]:
        """(edge kind, base name) for every base clause of a type node."""
        found: list[tuple[EdgeKind, str]] = []

        def collect(clause, kind: EdgeKind) -> None:
            for child in _named_children(clause):
                if child.type in self.base_clauses:
                    collect(child, self.base_clauses[child.type])
                elif child.type in ("type_list", "delegation_specifiers"):
                    collect(child, kind)
                elif child.type not in _SKIP_TYPES:
                    name = self.type_name(child, source)
                    if name:
                        found.append((kind, name))

        for child in _named_children(node):
            if child.type in self.base_clauses:
                collect(child, self.base_clauses[child.type])
        return found

    # ------------------------------------------------------------------
    # The walk
    # ------------------------------------------------------------------

    def _extract(self, tree, source: str, file_path: str):
        nodes: list[NodeInfo] = []
        calls: list[EdgeInfo] = []
        imports: list[EdgeInfo] = []
        bases: list[EdgeInfo] = []
        lang = self.language

        def node_range(n) -> SourceRange:
            return SourceRange(n.start_point.row + 1, n.end_point.row + 1)

        def visit(n, parent: Optional[str], in_type: bool, func: Optional[str]) -> None:
            ntype = n.type

            if ntype in self.import_types:
                for module in self.imports_of(n, source):
                    imports.append(
                        EdgeInfo(
                            kind=EdgeKind.IMPORTS_FROM,
                            source_qualified=file_path,
                            target_qualified=module,
                            file_path=file_path,
                        )
                    )

            if ntype in self.call_types and func:
                target = self.callee_of(n, source)
                if target:
                    calls.append(
                        EdgeInfo(
                            kind=EdgeKind.CALLS,
                            source_qualified=func,
                            target_qualified=target,
                            file_path=file_path,
                        )
                    )

            opened = self.container(n, source)
            if opened is not None:
                kind, name = opened
                if kind is not None:
                    info = NodeInfo(
                        kind=kind,
                        name=name,
                        file_path=file_path,
                        range=node_range(n),
                        language=lang,
                        parent_name=parent,
                        extra=self.extra_of(n, source),
                    )
                    nodes.append(info)
                    qualified = info.qualified_name
                else:
                    qualified = name
                for base_kind, base in self.bases_of(n, source):
                    bases.append(
                        EdgeInfo(
                            kind=base_kind,
                            source_qualified=qualified,
                            target_qualified=base,
                            file_path=file_path,
                        )
                    )
                for child in _children(n):
                    visit(child, qualified, True, None)
                return

            if ntype in self.function_types:
                name = self.name_of(n, source)
                if name:
                    owner = self.owner_of(n, source)
                    test = self.is_test(n, name, source, file_path)
                    if test:
                        kind = NodeKind.TEST
                    elif in_type or owner:
                        kind = NodeKind.METHOD
                    else:
                        kind = NodeKind.FUNCTION
                    info = NodeInfo(
                        kind=kind,
                        name=name,
                        file_path=file_path,
                        range=node_range(n),
                        language=lang,
                        parent_name=owner or parent,
                        is_test=test,
                    )
                    nodes.append(info)
                    for child in _children(n):
                        visit(child, info.qualified_name, False, info.qualified_name)
                    return

            for child in _children(n):
                visit(child, parent, in_type, func)

        visit(tree.root_node, None, False, None)
        return nodes, calls, imports, bases
