"""Language parser implementations.

Supported stacks:
  - Python (FastAPI, Django, scripts)
  - PHP (Laravel)
  - JavaScript (Node.js, React)
  - TypeScript (Next.js, React, Node.js)
"""

from .javascript_parser import JavaScriptParser
from .php_parser import PHPParser
from .python_parser import PythonParser
from .typescript_parser import TypeScriptParser

__all__ = [
    "PythonParser",
    "PHPParser",
    "JavaScriptParser",
    "TypeScriptParser",
]
