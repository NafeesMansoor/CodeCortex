"""Language parser implementations.

Python has a hand-written parser; every other language is described by
node-type tables on ``TreeSitterParser``. ``registry.PARSER_CLASSES`` is the
one list of supported languages:

  Python · JavaScript · TypeScript · PHP · Go · Rust · Java · C# · C · C++
  Ruby · Kotlin · Swift · Scala · Lua · Bash
"""

from .javascript_parser import JavaScriptParser
from .languages import (
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
from .php_parser import PHPParser
from .python_parser import PythonParser
from .treesitter_parser import TreeSitterParser
from .typescript_parser import TypeScriptParser

__all__ = [
    "PythonParser",
    "PHPParser",
    "JavaScriptParser",
    "TypeScriptParser",
    "TreeSitterParser",
    "GoParser",
    "RustParser",
    "JavaParser",
    "CSharpParser",
    "CParser",
    "CppParser",
    "RubyParser",
    "KotlinParser",
    "SwiftParser",
    "ScalaParser",
    "LuaParser",
    "BashParser",
]
