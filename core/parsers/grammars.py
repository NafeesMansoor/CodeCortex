"""Grammar loading for the supported languages.

Grammars come from the packaged wheels (``tree-sitter-python`` and friends),
which ship the compiled grammar inside the distribution. The alternative,
``tree_sitter_language_pack.get_parser``, fetches a manifest and the grammar
over the network on first use — so a parse could fail with an HTTP error, and
an offline or air-gapped installation could not parse at all. Parsing is the
one thing CodeCortex cannot do without, so it does not depend on a download.

The language pack stays as a fallback for languages with no wheel among the
dependencies; nothing in the supported stack reaches it.

Languages are cached because building one costs real work. Parsers are not:
``Parser`` holds mutable state and the indexer parses from several threads, so
each call gets its own.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# language name → (module, factory attribute) for the grammars we package.
_WHEELS: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "php": ("tree_sitter_php", "language_php"),
}

_languages: dict[str, object] = {}


def _load_language(language: str) -> Optional[object]:
    """Build a Language from the packaged wheel, or None if it is unavailable."""
    target = _WHEELS.get(language)
    if target is None:
        return None

    module_name, attribute = target
    try:
        import importlib

        import tree_sitter

        factory: Callable[[], object] = getattr(importlib.import_module(module_name), attribute)
        return tree_sitter.Language(factory())
    except Exception as exc:
        logger.debug("packaged grammar for %s unavailable: %s", language, exc)
        return None


def get_language(language: str):
    """Cached Language for `language`, or None when no wheel provides it."""
    if language not in _languages:
        loaded = _load_language(language)
        if loaded is None:
            return None
        _languages[language] = loaded
    return _languages[language]


def get_parser(language: str):
    """A fresh Parser for `language`.

    Raises whatever the language pack raises when the grammar cannot be
    obtained at all, so a missing grammar surfaces as a parse error for that
    file rather than silently producing an empty tree.
    """
    packaged = get_language(language)
    if packaged is not None:
        import tree_sitter

        return tree_sitter.Parser(packaged)

    import tree_sitter_language_pack as tslp

    return tslp.get_parser(language)
