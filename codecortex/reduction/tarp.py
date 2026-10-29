"""TARP — Task-Aware Reduction and Preservation.

Selects a query-specific subgraph G_q of a repository graph G under a cost
budget B, where C(G_q) = |V_q| + mu * |E_q| (E_q: edges induced by V_q).

Node importance (each term min-max normalised to [0, 1] before mixing):

    I(v | q) = alpha * S(v, q) + beta * D(v, q) + gamma * P(v, q) + delta * E(v)

    S  semantic relevance: the pipeline's embedding search score for v
    D  dependency relevance: max over seeds s of S(s) * w(path) * lambda^hops, hops <= 3
    P  path participation: sum of w_st over seed pairs (s, t) whose shortest path contains v
    E  structural importance: node-kind prior blended with centrality

Components (each switchable, which gives the ablation variants A-G):

    query_conditioned  S and D enter I; otherwise I uses only P and E
    path_aware         P enters I, and whole seed-to-seed paths become selection units
    budget_greedy      lazy greedy on marginal value / marginal cost; otherwise rank-and-fill
    recovery           bounded expansion around the selection when evidence looks insufficient
    condense           Level-1 condensation of the view (see view.py)

Weights and constants are fixed a priori (TARPConfig defaults); they were not
tuned on evaluation data. The greedy selector is a heuristic: no approximation
guarantee is claimed.
"""

from __future__ import annotations

import collections
import heapq
import time
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Optional

from codecortex.core.types import NodeKind
from codecortex.reduction.view import GraphView

KIND_PRIOR: dict[NodeKind, float] = {
    NodeKind.ENDPOINT: 1.0,
    NodeKind.INTERFACE: 0.9,
    NodeKind.CLASS: 0.8,
    NodeKind.MODULE: 0.8,
    NodeKind.STRUCT: 0.8,
    NodeKind.ENUM: 0.6,
    NodeKind.TYPE: 0.6,
    NodeKind.FUNCTION: 0.6,
    NodeKind.METHOD: 0.6,
    NodeKind.NAMESPACE: 0.5,
    NodeKind.FILE: 0.5,
    NodeKind.TEST: 0.4,
    NodeKind.CONSTANT: 0.3,
    NodeKind.VARIABLE: 0.1,
    NodeKind.PARAMETER: 0.05,
    NodeKind.CALLSITE: 0.05,
}


@dataclass
class TARPConfig:
    alpha: float = 0.40
    beta: float = 0.25
    gamma: float = 0.20
    delta: float = 0.15
    query_conditioned: bool = True
    path_aware: bool = True
    budget_greedy: bool = True
    recovery: bool = False
    condense: bool = False
    n_seeds: int = 8  # seeds for dependency relevance
    path_seeds: int = 6  # seeds whose pairwise paths form T_q
    dep_depth: int = 3
    dep_decay: float = 0.6
    max_path_len: int = 4  # edges
    edge_cost: float = 0.1  # mu in C(G_q) = |V_q| + mu |E_q|
    path_bonus: float = 1.0  # added value for completing a seed path
    candidate_factor: int = 20  # candidate pool <= factor * budget
    recovery_budget: float = 0.2  # extra cost allowed, fraction of B
    recovery_rounds: int = 2
    recovery_topk: int = 10
    recovery_coverage: float = 0.8

    @classmethod
    def variant(cls, name: str, **overrides) -> "TARPConfig":
        """Ablation variants A-G of the paper (query, path, budget, recovery)."""
        flags = {
            "A": (False, False, False, False),
            "B": (True, False, False, False),
            "C": (False, True, False, False),
            "D": (True, True, False, False),
            "E": (True, False, True, False),
            "F": (True, True, True, False),
            "G": (True, True, True, True),
        }[name]
        q, p, b, r = flags
        return cls(query_conditioned=q, path_aware=p, budget_greedy=b, recovery=r, **overrides)


@dataclass
class ReductionResult:
    method: str
    selected: set[str]
    original_nodes: set[str]
    budget: float
    cost: float
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "budget": self.budget,
            "cost": round(self.cost, 3),
            "selected": len(self.selected),
            "original_nodes": len(self.original_nodes),
            **self.stats,
        }


# ---------------------------------------------------------------- scoring
def _normalise(scores: dict[str, float]) -> dict[str, float]:
    top = max(scores.values(), default=0.0)
    return {k: v / top for k, v in scores.items()} if top > 0 else {k: 0.0 for k in scores}


def semantic_scores(view: GraphView, semantic: dict[str, float]) -> dict[str, float]:
    """Lift scores of original nodes onto view nodes (max over condensed members)."""
    out: dict[str, float] = {}
    for v, members in view.members.items():
        if v not in view.kind:
            continue
        s = max((semantic.get(m, 0.0) for m in members), default=0.0)
        if s > 0:
            out[v] = s
    return _normalise(out)


def structural_scores(view: GraphView, centrality: Optional[dict[str, float]]) -> dict[str, float]:
    cent = _normalise({v: (centrality or {}).get(v, 0.0) for v in view.kind})
    return {v: 0.5 * KIND_PRIOR.get(k, 0.3) + 0.5 * cent.get(v, 0.0) for v, k in view.kind.items()}


def dependency_scores(
    view: GraphView, S: dict[str, float], seeds: list[str], depth: int, decay: float
) -> dict[str, float]:
    best = {s: S[s] for s in seeds}
    frontier = dict(best)
    for _ in range(depth):
        nxt: dict[str, float] = {}
        for u, su in frontier.items():
            for v, w in view.adj.get(u, {}).items():
                sv = su * w * decay
                if sv > best.get(v, 0.0) and sv > nxt.get(v, 0.0):
                    nxt[v] = sv
        best.update(nxt)
        frontier = nxt
    return _normalise(best)


def shortest_path(view: GraphView, s: str, t: str, max_len: int) -> Optional[list[str]]:
    if s == t:
        return [s]
    parent = {s: None}
    frontier = [s]
    for _ in range(max_len):
        nxt = []
        for u in frontier:
            for v in view.adj.get(u, {}):
                if v not in parent:
                    parent[v] = u
                    if v == t:
                        path = [v]
                        while parent[path[-1]] is not None:
                            path.append(parent[path[-1]])
                        return path[::-1]
                    nxt.append(v)
        frontier = nxt
    return None


def seed_paths(
    view: GraphView, S: dict[str, float], seeds: list[str], max_len: int
) -> list[tuple[float, list[str]]]:
    paths = []
    for s, t in combinations(seeds, 2):
        p = shortest_path(view, s, t, max_len)
        if p and len(p) > 1:
            paths.append((S.get(s, 0.0) * S.get(t, 0.0), p))
    paths.sort(key=lambda x: (-x[0], x[1]))
    return paths


def path_scores(view: GraphView, paths: list[tuple[float, list[str]]]) -> dict[str, float]:
    p: dict[str, float] = collections.defaultdict(float)
    for w, path in paths:
        for v in path:
            p[v] += w
    return _normalise(dict(p))


# ---------------------------------------------------------------- selection with the cost model
class _Selection:
    def __init__(self, view: GraphView, edge_cost: float):
        self.view = view
        self.mu = edge_cost
        self.nodes: set[str] = set()
        self.edges = 0
        self._dir = view.neighbour_edge_counts()

    @property
    def cost(self) -> float:
        return len(self.nodes) + self.mu * self.edges

    def marginal(self, unit: list[str]) -> tuple[float, int, list[str]]:
        new = [x for x in dict.fromkeys(unit) if x not in self.nodes]
        pool = self.nodes | set(new)
        added = 0
        seen = set()
        for x in new:
            for y, c in self._dir[x].items():
                if y in pool and (y not in new or (y, x) not in seen):
                    added += c
                    seen.add((x, y))
        return len(new) + self.mu * added, added, new

    def add(self, new: list[str], added_edges: int) -> None:
        self.nodes.update(new)
        self.edges += added_edges


def _fill_ranked(sel: _Selection, order: list[str], budget: float) -> None:
    for v in order:
        if v in sel.nodes:
            continue
        cost, added, new = sel.marginal([v])
        if sel.cost + cost <= budget:
            sel.add(new, added)


def _greedy(
    sel: _Selection, units: list[tuple[list[str], float]], imp: dict[str, float], budget: float
) -> None:
    """Lazy greedy on marginal value per marginal cost (value of a unit = sum imp(new) + bonus)."""
    heap = []
    for idx, (unit, bonus) in enumerate(units):
        cost, _, new = sel.marginal(unit)
        if cost > 0:
            heapq.heappush(heap, (-(sum(imp.get(x, 0.0) for x in new) + bonus) / cost, idx))
    while heap:
        neg, idx = heapq.heappop(heap)
        unit, bonus = units[idx]
        cost, added, new = sel.marginal(unit)
        if not new:
            continue
        ratio = (sum(imp.get(x, 0.0) for x in new) + bonus) / cost
        if heap and ratio < -heap[0][0] - 1e-12:
            heapq.heappush(heap, (-ratio, idx))  # stale estimate: re-queue
            continue
        if sel.cost + cost <= budget:
            sel.add(new, added)


class TaskAwareReducer:
    """Query-specific reduction over a fixed GraphView."""

    def __init__(self, view: GraphView, centrality: Optional[dict[str, float]] = None):
        self.view = view
        self.E = structural_scores(view, centrality)

    def reduce(
        self,
        semantic: dict[str, float],
        budget: float,
        config: Optional[TARPConfig] = None,
        method: str = "tarp",
    ) -> ReductionResult:
        cfg = config or TARPConfig()
        t0 = time.perf_counter()
        view = self.view
        S = semantic_scores(view, semantic)
        ranked_seeds = sorted(S, key=lambda v: (-S[v], v))
        seeds = ranked_seeds[: cfg.n_seeds]
        paths = (
            seed_paths(view, S, ranked_seeds[: cfg.path_seeds], cfg.max_path_len)
            if cfg.path_aware
            else []
        )
        D = (
            dependency_scores(view, S, seeds, cfg.dep_depth, cfg.dep_decay)
            if cfg.query_conditioned
            else {}
        )
        P = path_scores(view, paths) if cfg.path_aware else {}

        weights = {
            "S": cfg.alpha if cfg.query_conditioned else 0.0,
            "D": cfg.beta if cfg.query_conditioned else 0.0,
            "P": cfg.gamma if cfg.path_aware else 0.0,
            "E": cfg.delta,
        }
        total = sum(weights.values()) or 1.0
        weights = {k: w / total for k, w in weights.items()}
        imp = {
            v: weights["S"] * S.get(v, 0.0)
            + weights["D"] * D.get(v, 0.0)
            + weights["P"] * P.get(v, 0.0)
            + weights["E"] * self.E[v]
            for v in view.kind
        }
        t_score = time.perf_counter() - t0

        order = sorted(view.kind, key=lambda v: (-imp[v], v))
        pool = order[: max(1, int(cfg.candidate_factor * budget))]
        sel = _Selection(view, cfg.edge_cost)
        top_w = paths[0][0] if paths else 1.0
        if cfg.budget_greedy:
            units = [([v], 0.0) for v in pool]
            units += [(p, cfg.path_bonus * w / top_w) for w, p in paths]
            _greedy(sel, units, imp, budget)
        else:
            for w, p in paths:  # path preservation without optimisation: whole paths first
                cost, added, new = sel.marginal(p)
                if new and sel.cost + cost <= budget:
                    sel.add(new, added)
            _fill_ranked(sel, pool, budget)

        recovery = {"rounds": 0, "added_nodes": 0, "added_cost": 0.0}
        if cfg.recovery:
            recovery = self._recover(sel, imp, S, ranked_seeds, paths, budget, cfg)

        sel_paths = sum(1 for _, p in paths if all(v in sel.nodes for v in p))
        stats = {
            "nodes_total": len(view.kind),
            "edges_total": len(view.edges),
            "nodes_selected": len(sel.nodes),
            "edges_selected": sel.edges,
            "node_reduction": round(1 - len(sel.nodes) / len(view.kind), 4) if view.kind else 0.0,
            "edge_reduction": round(1 - sel.edges / len(view.edges), 4) if view.edges else 0.0,
            "seeds": len(seeds),
            "seed_paths": len(paths),
            "seed_paths_retained": sel_paths,
            "recovery": recovery,
            "weights": weights,
            "config": asdict(cfg),
            "score_s": round(t_score, 4),
            "total_s": round(time.perf_counter() - t0, 4),
        }
        return ReductionResult(
            method, set(sel.nodes), view.original_nodes(sel.nodes), budget, sel.cost, stats
        )

    def _recover(self, sel, imp, S, ranked_seeds, paths, budget, cfg) -> dict:
        """Bounded expansion: add neighbours of the selection while evidence is insufficient."""
        limit = budget * (1 + cfg.recovery_budget)
        top = ranked_seeds[: cfg.recovery_topk]
        info = {"rounds": 0, "added_nodes": 0, "added_cost": 0.0}

        def sufficient() -> bool:
            coverage = sum(v in sel.nodes for v in top) / len(top) if top else 1.0
            connected = all(all(v in sel.nodes for v in p) for _, p in paths)
            return coverage >= cfg.recovery_coverage and connected

        start = sel.cost
        for _ in range(cfg.recovery_rounds):
            if sufficient() or sel.cost >= limit:
                break
            info["rounds"] += 1
            need = [v for v in top if v not in sel.nodes] + [
                v for _, p in paths for v in p if v not in sel.nodes
            ]
            frontier = {
                v: imp[v]
                * max(
                    (
                        self.view.edge_weight(v, u)
                        for u in sel.nodes
                        if u in self.view.adj.get(v, {})
                    ),
                    default=0.5,
                )
                for s in sel.nodes
                for v in self.view.adj.get(s, {})
                if v not in sel.nodes
            }
            for v in dict.fromkeys(need):
                frontier[v] = frontier.get(v, 0.0) + 1.0
            for v in sorted(frontier, key=lambda x: (-frontier[x], x)):
                cost, added, new = sel.marginal([v])
                if new and sel.cost + cost <= limit:
                    sel.add(new, added)
                    info["added_nodes"] += 1
        info["added_cost"] = round(sel.cost - start, 3)
        return info


# ---------------------------------------------------------------- conventional baselines (same view, cost, budget)
BASELINES = (
    "degree",
    "static_importance",
    "fixed_hop",
    "generic_budget",
    "dependency_aware",
    "path_preserving",
)


def reduce_baseline(
    name: str,
    reducer: TaskAwareReducer,
    semantic: dict[str, float],
    budget: float,
    config: Optional[TARPConfig] = None,
) -> ReductionResult:
    cfg = config or TARPConfig()
    view = reducer.view
    t0 = time.perf_counter()
    S = semantic_scores(view, semantic)
    seeds = sorted(S, key=lambda v: (-S[v], v))
    sel = _Selection(view, cfg.edge_cost)
    if name == "degree":
        deg = {v: len(view.adj.get(v, {})) for v in view.kind}
        _fill_ranked(sel, sorted(view.kind, key=lambda v: (-deg[v], v)), budget)
    elif name == "static_importance":
        _fill_ranked(sel, sorted(view.kind, key=lambda v: (-reducer.E[v], v)), budget)
    elif name == "fixed_hop":  # BFS layers from the seeds until the budget is spent
        layer, seen = seeds[: cfg.n_seeds], set()
        while layer:
            _fill_ranked(sel, layer, budget)
            seen.update(layer)
            layer = sorted({u for v in layer for u in view.adj.get(v, {})} - seen)
    elif name == "generic_budget":  # same greedy, semantic value only, no paths
        _greedy(
            sel,
            [([v], 0.0) for v in seeds[: max(1, int(cfg.candidate_factor * budget))]],
            S,
            budget,
        )
    elif name == "dependency_aware":
        D = dependency_scores(view, S, seeds[: cfg.n_seeds], cfg.dep_depth, cfg.dep_decay)
        _fill_ranked(sel, sorted(D, key=lambda v: (-D[v], -S.get(v, 0.0), v)) + seeds, budget)
    elif name == "path_preserving":
        for _, p in seed_paths(view, S, seeds[: cfg.path_seeds], cfg.max_path_len):
            cost, added, new = sel.marginal(p)
            if new and sel.cost + cost <= budget:
                sel.add(new, added)
        _fill_ranked(sel, seeds, budget)
    else:
        raise ValueError(f"unknown baseline {name!r}")
    stats = {
        "nodes_total": len(view.kind),
        "edges_total": len(view.edges),
        "nodes_selected": len(sel.nodes),
        "edges_selected": sel.edges,
        "node_reduction": round(1 - len(sel.nodes) / len(view.kind), 4) if view.kind else 0.0,
        "edge_reduction": round(1 - sel.edges / len(view.edges), 4) if view.edges else 0.0,
        "total_s": round(time.perf_counter() - t0, 4),
    }
    return ReductionResult(
        name, set(sel.nodes), view.original_nodes(sel.nodes), budget, sel.cost, stats
    )
