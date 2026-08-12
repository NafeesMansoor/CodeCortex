"""Tests for byte-accurate source slicing.

tree-sitter reports byte offsets; slicing a str with them silently corrupts
every identifier after the first non-ASCII character in a file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.parsers.python_parser import PythonParser
from core.source_text import SourceText, byte_slice, node_text

# Three em-dashes and an arrow in the docstring push every later byte offset
# eight positions past the matching character offset.
NON_ASCII_SOURCE = '''"""Ranking — scores — weights → normalised."""


class CentralityEngine:
    def compute(self):
        return 1


def build_scores(nodes):
    total = len(nodes)
    return total
'''


class _FakeNode:
    def __init__(self, start, end):
        self._start, self._end = start, end

    def start_byte(self):
        return self._start

    def end_byte(self):
        return self._end


class TestByteSlice:
    def test_ascii_str_slices_directly(self):
        assert byte_slice("hello world", 6, 11) == "world"

    def test_non_ascii_str_uses_byte_offsets(self):
        text = "héllo world"
        start = len("héllo ".encode())
        assert byte_slice(text, start, start + 5) == "world"

    def test_source_text_matches_plain_str(self):
        text = "docstring — then code"
        data = text.encode()
        for start in range(0, len(data), 3):
            end = min(start + 7, len(data))
            assert byte_slice(SourceText(text), start, end) == byte_slice(text, start, end)

    def test_bytes_input_is_decoded(self):
        assert byte_slice("héllo".encode(), 0, 6) == "héllo"

    def test_split_multibyte_character_does_not_raise(self):
        # Slicing mid-character yields a replacement char rather than an error.
        assert byte_slice(SourceText("—"), 0, 1) == "�"

    def test_node_text_uses_node_offsets(self):
        text = SourceText("alpha — beta")
        start = len("alpha — ".encode())
        assert node_text(_FakeNode(start, start + 4), text) == "beta"


class TestSourceText:
    def test_is_a_str(self):
        wrapped = SourceText("line one\nline two")
        assert isinstance(wrapped, str)
        assert wrapped.splitlines() == ["line one", "line two"]
        assert wrapped == "line one\nline two"

    def test_ascii_source_skips_the_byte_copy(self):
        assert SourceText("pure ascii").utf8 is None

    def test_non_ascii_source_caches_its_encoding(self):
        wrapped = SourceText("em — dash")
        assert wrapped.utf8 == "em — dash".encode()

    def test_double_wrapping_is_stable(self):
        once = SourceText("em — dash")
        twice = SourceText(once)
        assert twice == once and twice.utf8 == once.utf8


class TestParserOnNonAsciiSource:
    def test_identifiers_are_not_mangled(self):
        result = PythonParser().parse(NON_ASCII_SOURCE, "centrality_engine.py")
        names = {node.name for node in result.nodes}
        assert {"CentralityEngine", "compute", "build_scores"} <= names

    def test_matches_the_ascii_equivalent(self):
        ascii_source = NON_ASCII_SOURCE.replace("—", "-").replace("→", "->")
        parser = PythonParser()
        with_unicode = sorted(n.name for n in parser.parse(NON_ASCII_SOURCE, "a.py").nodes)
        without = sorted(n.name for n in parser.parse(ascii_source, "a.py").nodes)
        assert with_unicode == without
