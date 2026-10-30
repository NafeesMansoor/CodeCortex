"""Scope-resolved Python references for the reduction view.

The Python parser of CodeCortex records calls by bare name and does not record
``self.method()`` calls at all, so most dependencies of a method are missing from
the graph. This module reads each Python definition once with ``ast`` and
resolves the names it uses, in this order:

  * ``self.x`` / ``cls.x``  -> member ``x`` of the enclosing class or its bases
  * ``Name.x``              -> member ``x`` of a class, or ``x`` in an imported module
  * ``x``                   -> definition in the same file, then an imported name,
                               then a unique definition with that simple name

A name is linked only when it resolves to exactly one existing graph node. The
stored graph is not modified; the edges are added to the read-only view only.
"""

from __future__ import annotations

import ast
import collections
import os
from typing import Iterable, Optional

from codecortex.core.types import EdgeKind, NodeKind

_DEF_KINDS = frozenset(
    {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS, NodeKind.TEST, NodeKind.ENDPOINT}
)
_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _dotted(path: str) -> list[str]:
    parts = os.path.normpath(path)[: -len(".py")].split(os.sep)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


class _Modules:
    """Maps an import name to a file by the longest matching dotted path suffix."""

    def __init__(self, files: Iterable[str]):
        self.files = {os.path.normpath(f) for f in files}
        self.by_last: dict[str, list[tuple[list[str], str]]] = collections.defaultdict(list)
        for f in self.files:
            parts = _dotted(f)
            if parts:
                self.by_last[parts[-1]].append((parts, f))

    def find(self, module: str) -> Optional[str]:
        want = module.split(".")
        hits = [f for parts, f in self.by_last.get(want[-1], []) if parts[-len(want):] == want]
        return hits[0] if len(hits) == 1 else None

    def relative(self, current: str, level: int, module: Optional[str]) -> Optional[str]:
        base = os.path.dirname(current)
        for _ in range(level - 1):
            base = os.path.dirname(base)
        target = os.path.join(base, *(module.split(".") if module else []))
        for cand in (target + ".py", os.path.join(target, "__init__.py")):
            if os.path.normpath(cand) in self.files:
                return os.path.normpath(cand)
        return None


def python_references(nodes) -> list[tuple[str, str, EdgeKind]]:
    """Resolved (source, target, kind) references between existing definition nodes."""
    defs = [n for n in nodes if n.kind in _DEF_KINDS and n.file_path.endswith(".py") and n.range]
    at_line: dict[tuple[str, int], str] = {
        (os.path.normpath(n.file_path), n.range.start_line): n.qualified_name for n in defs
    }
    by_simple: dict[str, list[str]] = collections.defaultdict(list)
    for n in defs:
        by_simple[n.name].append(n.qualified_name)
    files = sorted({os.path.normpath(n.file_path) for n in defs})
    modules = _Modules(files)

    trees: dict[str, ast.Module] = {}
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                trees[f] = ast.parse(fh.read())
        except (OSError, SyntaxError, ValueError):
            continue

    def node_of(f: str, d) -> Optional[str]:
        for line in [d.lineno] + [x.lineno for x in getattr(d, "decorator_list", [])]:
            q = at_line.get((f, line))
            if q:
                return q
        return None

    # Per file: top-level definitions, classes with members and bases, imported names.
    top: dict[str, dict[str, str]] = {}
    members: dict[str, dict[str, str]] = {}  # class qname -> member name -> qname
    bases: dict[str, list[str]] = {}
    imported: dict[str, dict[str, tuple[str, Optional[str]]]] = {}  # alias -> (file, name|None)
    for f, tree in trees.items():
        top[f], imported[f] = {}, {}
        for d in tree.body:
            if isinstance(d, (*_FUNC, ast.ClassDef)):
                q = node_of(f, d)
                if q:
                    top[f][d.name] = q
            if isinstance(d, ast.ImportFrom):
                src = (
                    modules.relative(f, d.level, d.module)
                    if d.level
                    else modules.find(d.module or "")
                )
                for a in d.names:
                    if src:
                        imported[f][a.asname or a.name] = (src, a.name)
                    elif d.level == 0 and d.module:  # "from pkg import module"
                        sub = modules.find(f"{d.module}.{a.name}")
                        if sub:
                            imported[f][a.asname or a.name] = (sub, None)
            elif isinstance(d, ast.Import):
                for a in d.names:
                    src = modules.find(a.name)
                    if src:
                        imported[f][a.asname or a.name.split(".")[0]] = (src, None)
        for d in ast.walk(tree):
            if isinstance(d, ast.ClassDef):
                q = node_of(f, d)
                if not q:
                    continue
                members[q] = {}
                for m in d.body:
                    if isinstance(m, (*_FUNC, ast.ClassDef)):
                        mq = node_of(f, m)
                        if mq:
                            members[q][m.name] = mq
                bases[q] = [b.id for b in d.bases if isinstance(b, ast.Name)]

    def unique(name: str) -> Optional[str]:
        c = by_simple.get(name, [])
        return c[0] if len(c) == 1 else None

    def lookup_name(f: str, name: str) -> Optional[str]:
        if name in top.get(f, {}):
            return top[f][name]
        if name in imported.get(f, {}):
            src, orig = imported[f][name]
            return top.get(src, {}).get(orig) if orig else None
        return unique(name)

    def member(cls: Optional[str], name: str, f: str, depth: int = 0) -> Optional[str]:
        if not cls or depth > 3:
            return None
        if name in members.get(cls, {}):
            return members[cls][name]
        for b in bases.get(cls, []):
            hit = member(lookup_name(f, b), name, f, depth + 1)
            if hit:
                return hit
        return None

    out: list[tuple[str, str, EdgeKind]] = []
    seen: set[tuple[str, str]] = set()
    for f, tree in trees.items():

        def visit(d, cls: Optional[str]):
            q = node_of(f, d)
            if isinstance(d, ast.ClassDef):
                for m in d.body:
                    if isinstance(m, (*_FUNC, ast.ClassDef)):
                        visit(m, q)
                return
            if not q:
                return
            called = {id(c.func) for c in ast.walk(d) if isinstance(c, ast.Call)}
            for x in ast.walk(d):
                target = None
                if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name):
                    base = x.value.id
                    if base in ("self", "cls"):
                        target = member(cls, x.attr, f)
                    elif base in imported.get(f, {}) and imported[f][base][1] is None:
                        target = top.get(imported[f][base][0], {}).get(x.attr)
                    else:
                        target = member(lookup_name(f, base), x.attr, f)
                elif isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                    target = lookup_name(f, x.id)
                if target and target != q and (q, target) not in seen:
                    seen.add((q, target))
                    out.append((q, target, EdgeKind.CALLS if id(x) in called else EdgeKind.REFERENCES))

        for d in tree.body:
            if isinstance(d, (*_FUNC, ast.ClassDef)):
                visit(d, None)
    return out
