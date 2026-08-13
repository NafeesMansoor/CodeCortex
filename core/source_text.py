"""Byte-accurate text extraction for tree-sitter nodes.

tree-sitter reports ``start_byte()`` / ``end_byte()`` as offsets into the UTF-8
encoding of the source. Slicing a ``str`` with them is only correct while the
source is pure ASCII — past the first multi-byte character every subsequent
offset drifts, and the drift accumulates. The result is silently wrong node
names (``'lityEngine:'`` instead of ``'class CentralityEngine'``) rather than an
exception.

``SourceText`` encodes the source once so slicing stays cheap. It subclasses
``str``, so code that treats the source as a string — ``splitlines()``,
``isinstance(source, str)`` — keeps working unchanged.

Usage:
    source = SourceText(path.read_text())
    name = node_text(node, source)
"""

from __future__ import annotations


class SourceText(str):
    """A ``str`` carrying its UTF-8 encoding for byte-offset slicing."""

    def __new__(cls, text: str) -> "SourceText":
        obj = super().__new__(cls, text)
        # The parser borrows this buffer rather than copying it, and the tree
        # keeps reading from it long after parse() returns — so it has to stay
        # reachable for the lifetime of the source. Handing tree-sitter a
        # temporary here is a use-after-free that segfaults much later, in
        # whatever file happens to be parsed once the allocator reuses the page.
        obj.buffer = text.encode("utf-8")
        # ASCII text needs no second copy for slicing: byte offsets are
        # character offsets, so `utf8` stays None and byte_slice takes the
        # fast path.
        obj.utf8 = None if obj.isascii() else obj.buffer
        return obj

    def as_bytes(self) -> bytes:
        """The UTF-8 buffer to parse — the same bytes the offsets index into."""
        return self.buffer


def byte_slice(source, start: int, end: int) -> str:
    """Slice ``source`` by UTF-8 byte offsets, whatever form it arrives in."""
    utf8 = getattr(source, "utf8", None)
    if utf8 is not None:
        return utf8[start:end].decode("utf-8", errors="replace")
    if isinstance(source, bytes):
        return source[start:end].decode("utf-8", errors="replace")
    # str.isascii() is an O(1) flag check in CPython; ASCII offsets need no work.
    if source.isascii():
        return source[start:end]
    return source.encode("utf-8")[start:end].decode("utf-8", errors="replace")


def node_text(node, source) -> str:
    """Return the source text spanned by a tree-sitter node."""
    return byte_slice(source, node.start_byte, node.end_byte)
