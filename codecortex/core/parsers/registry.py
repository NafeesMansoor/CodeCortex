"""The one list of language parsers.

Every caller that needs a parser — the pipeline, the parallel indexer, the
CLI's ``--languages`` filter — resolves it here, so adding a language means
adding its class to ``PARSER_CLASSES`` and nothing else.
"""

from __future__ import annotations

from typing import Iterable, Optional

from codecortex.core.parser_framework import LanguageParser
from codecortex.core.parsers.javascript_parser import JavaScriptParser
from codecortex.core.parsers.languages import (
    BashParser,
    CParser,
    CppParser,
    CSharpParser,
    GoParser,
    JavaParser,
    KotlinParser,
    LuaParser,
    RubyParser,
    RustParser,
    ScalaParser,
    SwiftParser,
)
from codecortex.core.parsers.php_parser import PHPParser
from codecortex.core.parsers.python_parser import PythonParser
from codecortex.core.parsers.typescript_parser import TypeScriptParser

PARSER_CLASSES: tuple[type[LanguageParser], ...] = (
    PythonParser,
    JavaScriptParser,
    TypeScriptParser,
    PHPParser,
    GoParser,
    RustParser,
    JavaParser,
    CSharpParser,
    CParser,
    CppParser,
    RubyParser,
    KotlinParser,
    SwiftParser,
    ScalaParser,
    LuaParser,
    BashParser,
)


def _handles(parser_class: type[LanguageParser]) -> set[str]:
    """Every name that selects a parser: language, aliases, bare extensions."""
    names = {parser_class.language, *parser_class.aliases}
    names.update(ext.lstrip(".") for ext in parser_class.extensions)
    return {name.lower() for name in names}


def _selected(languages: Optional[Iterable[str]]) -> list[type[LanguageParser]]:
    if not languages:
        return list(PARSER_CLASSES)
    wanted = {str(lang).lstrip(".").lower() for lang in languages}
    return [cls for cls in PARSER_CLASSES if _handles(cls) & wanted]


def get_parser(language: str) -> Optional[LanguageParser]:
    """A parser for a language name, alias or extension (``"rust"``, ``"rs"``)."""
    matches = _selected([language])
    return matches[0]() if matches else None


def build_extension_map(languages: Optional[Iterable[str]] = None) -> dict[str, LanguageParser]:
    """Map file extension (no dot, lowercase) → parser instance.

    Args:
        languages: Restrict to these languages, given as names, aliases or
            extensions (``["py", "rust", "tsx"]``). None selects every language.
            Naming one extension selects that language's parser, which still
            covers all of its extensions.
    """
    ext_to_parser: dict[str, LanguageParser] = {}
    for parser_class in _selected(languages):
        parser = parser_class()
        for ext in parser.extensions:
            ext_to_parser[ext.lstrip(".").lower()] = parser
    return ext_to_parser


def supported_languages() -> list[str]:
    return [cls.language for cls in PARSER_CLASSES]
