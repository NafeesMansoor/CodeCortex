"""BM25 relevance over the source text of definitions, for the semantic term S.

The default lexical embedding of CodeCortex describes a node by its name,
parameters and the first sentence of its docstring. Requirements, however,
often share vocabulary with the body of the code that implements them, so TARP
can blend in a BM25 score over each definition's source (functions, methods)
or header and docstring (classes). Dependency-free; built once per view.
"""

from __future__ import annotations

import collections
import math
import re
from typing import Iterable

from codecortex.core.types import NodeKind

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+|[0-9]+")
_TEXT_KINDS = frozenset(
    {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS, NodeKind.TEST, NodeKind.ENDPOINT}
)


def tokens(text: str) -> list[str]:
    """Identifiers, lower-cased, plus their snake_case / camelCase parts."""
    out = []
    for t in _IDENT.findall(text):
        out.append(t.lower())
        subs = [p.lower() for part in t.split("_") for p in _CAMEL.findall(part) if p]
        if len(subs) > 1:
            out.extend(subs)
    return out


def node_text(node, lines: list[str]) -> str:
    if node.kind == NodeKind.CLASS:
        return f"{node.name} {node.docstring or ''}"
    r = node.range
    return "".join(lines[r.start_line - 1 : r.end_line]) if r else node.name


class BM25Index:
    def __init__(self, docs: dict[str, list[str]], k1: float = 0.9, b: float = 0.4):
        self.k1, self.b = k1, b
        self.len = {d: len(t) for d, t in docs.items()}
        self.avg = (sum(self.len.values()) / len(docs)) if docs else 1.0
        self.post: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
        for d, toks in docs.items():
            for t, c in collections.Counter(toks).items():
                self.post[t].append((d, c))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.post.items()}

    def scores(self, query: str) -> dict[str, float]:
        out: dict[str, float] = collections.defaultdict(float)
        for t in set(tokens(query)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for d, tf in self.post[t]:
                norm = self.k1 * (1 - self.b + self.b * self.len[d] / self.avg)
                out[d] += idf * tf * (self.k1 + 1) / (tf + norm)
        return dict(out)


def build_index(nodes: Iterable, names: set[str]) -> BM25Index:
    """Index the definitions among ``names`` (view nodes) by their source text."""
    cache: dict[str, list[str]] = {}
    docs: dict[str, list[str]] = {}
    for n in nodes:
        if n.qualified_name not in names or n.kind not in _TEXT_KINDS:
            continue
        if n.file_path not in cache:
            try:
                with open(n.file_path, encoding="utf-8", errors="replace") as fh:
                    cache[n.file_path] = fh.readlines()
            except OSError:
                cache[n.file_path] = []
        docs[n.qualified_name] = tokens(node_text(n, cache[n.file_path])) or ["<empty>"]
    return BM25Index(docs)
