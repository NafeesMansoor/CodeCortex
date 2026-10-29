"""Read-only reduction view over a GraphStore.

The stored CPG keeps call and import targets as bare names (``send``) while
definitions are keyed ``Owner.method``, and Python method calls even use a
bare name as the source. Path-aware reduction needs those relationships, so
the view resolves an endpoint to a definition only when the match is
unambiguous: a unique definition with that simple name, or a unique one in
the edge's own file. Ambiguous endpoints are left unresolved and counted.
The stored graph is never modified.

Optional Level-1 condensation folds VARIABLE / PARAMETER / CALLSITE nodes
into their parent definition, keeping the members so every original node
still maps to a source location.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Optional

from codecortex.core.types import EdgeKind, NodeKind
from codecortex.graph.graph_store import GraphStore

# Relationship weights for dependency relevance and path cost. Fixed a priori.
EDGE_TYPE_WEIGHTS: dict[EdgeKind, float] = {
    EdgeKind.CALLS: 1.0,
    EdgeKind.INHERITS: 0.9,
    EdgeKind.IMPLEMENTS: 0.9,
    EdgeKind.EXTENDS: 0.9,
    EdgeKind.CONTAINS: 0.8,
    EdgeKind.USES: 0.7,
    EdgeKind.REFERENCES: 0.7,
    EdgeKind.TESTS: 0.7,
    EdgeKind.TESTED_BY: 0.7,
    EdgeKind.DEPENDS_ON: 0.6,
    EdgeKind.IMPORTS_FROM: 0.6,
    EdgeKind.DEFINES: 0.6,
    EdgeKind.CONTROLS: 0.5,
    EdgeKind.READS: 0.4,
    EdgeKind.WRITES: 0.4,
    EdgeKind.PARAMETER_OF: 0.3,
}

# Kinds a bare-name endpoint may resolve to.
_DEFINITION_KINDS = frozenset(
    {
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.CLASS,
        NodeKind.INTERFACE,
        NodeKind.STRUCT,
        NodeKind.ENUM,
        NodeKind.TYPE,
        NodeKind.ENDPOINT,
        NodeKind.TEST,
        NodeKind.MODULE,
        NodeKind.NAMESPACE,
        NodeKind.CONSTANT,
    }
)
_CONDENSABLE_KINDS = frozenset({NodeKind.VARIABLE, NodeKind.PARAMETER, NodeKind.CALLSITE})


def simple_name(qualified_name: str) -> str:
    return qualified_name.rsplit(".", 1)[-1].lstrip("#")


@dataclass
class ResolutionStats:
    edges_in: int = 0
    edges_kept: int = 0
    endpoints_resolved: int = 0
    endpoints_ambiguous: int = 0
    endpoints_unresolved: int = 0
    self_loops_dropped: int = 0
    parallel_merged: int = 0
    nodes_condensed: int = 0

    def to_dict(self) -> dict:
        return dict(vars(self))


@dataclass
class GraphView:
    """Typed, resolved, undirected-traversable view of a repository graph."""

    kind: dict[str, NodeKind]
    file_of: dict[str, str]
    members: dict[str, list[str]]  # view node -> original nodes it stands for
    edges: dict[tuple[str, str], dict[EdgeKind, int]]  # directed (u, v) -> kind counts
    adj: dict[str, dict[str, float]] = field(default_factory=dict)  # undirected, max weight
    stats: ResolutionStats = field(default_factory=ResolutionStats)

    @property
    def nodes(self) -> list[str]:
        return list(self.kind)

    def edge_weight(self, u: str, v: str) -> float:
        return self.adj.get(u, {}).get(v, 0.0)

    def neighbour_edge_counts(self) -> dict[str, dict[str, int]]:
        """Directed edges between each node pair, counted from both ends; built once."""
        if not hasattr(self, "_counts"):
            counts: dict[str, dict[str, int]] = collections.defaultdict(
                lambda: collections.defaultdict(int)
            )
            for u, v in self.edges:
                counts[u][v] += 1
                counts[v][u] += 1
            self._counts = counts
        return self._counts

    def induced_edges(self, selected: set[str]) -> int:
        return sum(1 for (u, v) in self.edges if u in selected and v in selected)

    def original_nodes(self, selected: set[str]) -> set[str]:
        return {m for v in selected for m in self.members.get(v, [v])}


def build_view(store: GraphStore, condense: bool = False) -> GraphView:
    nodes = store.all_nodes()
    kind = {n.qualified_name: n.kind for n in nodes}
    file_of = {n.qualified_name: n.file_path for n in nodes}
    stats = ResolutionStats()

    # Level-1 condensation: fold low-level nodes into their parent definition.
    owner: dict[str, str] = {}
    if condense:
        for n in nodes:
            # DFG variables are named "func.var" without parent_qualified set.
            parent = n.parent_qualified or (
                n.qualified_name.rsplit(".", 1)[0] if "." in n.qualified_name else None
            )
            if (
                n.kind in _CONDENSABLE_KINDS
                and parent
                and parent in kind
                and parent != n.qualified_name
            ):
                owner[n.qualified_name] = parent
        stats.nodes_condensed = len(owner)
    members: dict[str, list[str]] = collections.defaultdict(list)
    for name in kind:
        members[owner.get(name, name)].append(name)
    view_kind = {name: k for name, k in kind.items() if name not in owner}

    by_name: dict[str, list[str]] = collections.defaultdict(list)
    for name, k in view_kind.items():
        if k in _DEFINITION_KINDS:
            by_name[simple_name(name)].append(name)

    def resolve(endpoint: str, edge_file: str) -> Optional[str]:
        if endpoint in kind:
            return owner.get(endpoint, endpoint)
        candidates = by_name.get(simple_name(endpoint), [])
        if len(candidates) == 1:
            stats.endpoints_resolved += 1
            return candidates[0]
        local = [c for c in candidates if file_of.get(c) == edge_file]
        if len(local) == 1:
            stats.endpoints_resolved += 1
            return local[0]
        if candidates:
            stats.endpoints_ambiguous += 1
        else:
            stats.endpoints_unresolved += 1
        return None

    edges: dict[tuple[str, str], dict[EdgeKind, int]] = {}
    adj: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for e in store.all_edges():
        stats.edges_in += 1
        u = resolve(e.source, e.file_path)
        v = resolve(e.target, e.file_path)
        if u is None or v is None:
            continue
        if u == v:
            stats.self_loops_dropped += 1
            continue
        kinds = edges.setdefault((u, v), {})
        if kinds:
            stats.parallel_merged += 1
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
        w = EDGE_TYPE_WEIGHTS.get(e.kind, 0.5)
        if w > adj[u].get(v, 0.0):
            adj[u][v] = w
            adj[v][u] = w
    stats.edges_kept = len(edges)
    return GraphView(
        kind=view_kind,
        file_of=file_of,
        members=dict(members),
        edges=edges,
        adj=dict(adj),
        stats=stats,
    )
