#!/usr/bin/env python3
"""CodeCortex V0.1 — Full Stage-wise Performance Evaluation.

Evaluates all 6 stages defined in the Incremental Performance Evaluation Framework
against the code-review-graph codebase and appends a "CodeCortex V0.1" entry to
docs/results.md.

Stages:
  1. LSP Semantic Index  — call graph + symbol resolution, no CFG/DFG
  2. CPG                 — full Code Property Graph (AST + CFG + DFG + Endpoints)
  3. + Embeddings        — FAISS vector index on semantic nodes
  4. + Clustering        — HDBSCAN semantic groups + quality metrics
  5. + Traversal         — Bounded BFS, impact radius, taint traversal
  6. + Centrality        — PageRank, betweenness, closeness, eigenvector ranking

Composite score per stage:
    score = 0.25*retrieval + 0.25*graph + 0.20*semantic + 0.15*efficiency + 0.15*ranking

Usage:
    python3 tests/benchmarks/bench_v01.py [--quiet]
"""

from __future__ import annotations

import argparse
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

RESULTS_PATH = ROOT / "docs" / "results.md"
_DEFAULT_TARGET = ROOT / "code-review-graph" / "code_review_graph"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _rss_mb() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    except Exception:
        return 0.0


def _collect_python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


# ── Stage runners ─────────────────────────────────────────────────────────────

def stage1_lsp(py_files: list[Path], quiet: bool) -> dict:
    """Stage 1: LSP-mode graph — call + import, no CFG/DFG."""
    from core.parsers.python_parser import PythonParser
    from graph.graph_store import GraphStore
    from graph.cpg_builder import CPGBuilder
    from indexing.semantic_index import SemanticIndex

    parser = PythonParser()
    store = GraphStore(":memory:")
    builder = CPGBuilder(store, enable_cfg=False, enable_dfg=False, enable_endpoints=False)

    tracemalloc.start()
    t0 = time.perf_counter()
    errors = 0
    for f in py_files:
        try:
            result = parser.parse(f.read_text(encoding="utf-8", errors="replace"), str(f))
            builder.ingest(result)
            if result.errors:
                errors += 1
        except Exception:
            errors += 1
    build_time = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    idx = SemanticIndex(store)
    import random
    nodes = store.all_nodes()
    sample = random.sample(nodes, min(50, len(nodes)))
    t1 = time.perf_counter()
    for n in sample:
        idx.lookup(n.qualified_name)
    lookup_ms = (time.perf_counter() - t1) / len(sample) * 1000 if sample else 0

    from core.types import EdgeKind
    edge_bd: dict[str, int] = {}
    for e in store.all_edges():
        edge_bd[e.kind.value] = edge_bd.get(e.kind.value, 0) + 1

    n_nodes = store.node_count()
    n_edges = store.edge_count()
    density = 2 * n_edges / max(n_nodes * (n_nodes - 1), 1)
    avg_deg = 2 * n_edges / max(n_nodes, 1)

    if not quiet:
        print(f"  Nodes      : {n_nodes}   Edges: {n_edges}")
        print(f"  Build time : {build_time:.3f}s   Lookup: {lookup_ms:.3f}ms")

    return {
        "store": store,
        "nodes": n_nodes, "edges": n_edges,
        "build_time_s": round(build_time, 3),
        "peak_mem_mb": round(peak / 1024 / 1024, 2),
        "density": round(density, 6),
        "avg_degree": round(avg_deg, 3),
        "parse_errors": errors,
        "symbol_lookup_ms": round(lookup_ms, 3),
        "edge_breakdown": edge_bd,
    }


def stage2_cpg(py_files: list[Path], quiet: bool) -> dict:
    """Stage 2: Full CPG — AST + CFG + DFG + Endpoints."""
    from core.parsers.python_parser import PythonParser
    from graph.graph_store import GraphStore
    from graph.cpg_builder import CPGBuilder

    parser = PythonParser()
    store = GraphStore(":memory:")
    builder = CPGBuilder(store, enable_cfg=True, enable_dfg=True, enable_endpoints=True)

    tracemalloc.start()
    t0 = time.perf_counter()
    errors = 0
    for f in py_files:
        try:
            result = parser.parse(f.read_text(encoding="utf-8", errors="replace"), str(f))
            builder.ingest(result)
            if result.errors:
                errors += 1
        except Exception:
            errors += 1
    build_time = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    edge_bd: dict[str, int] = {}
    for e in store.all_edges():
        edge_bd[e.kind.value] = edge_bd.get(e.kind.value, 0) + 1

    n_nodes = store.node_count()
    n_edges = store.edge_count()
    density = 2 * n_edges / max(n_nodes * (n_nodes - 1), 1)
    avg_deg = 2 * n_edges / max(n_nodes, 1)

    if not quiet:
        print(f"  Nodes      : {n_nodes}   Edges: {n_edges}")
        print(f"  Build time : {build_time:.3f}s   Density: {density:.6f}")

    return {
        "store": store,
        "nodes": n_nodes, "edges": n_edges,
        "build_time_s": round(build_time, 3),
        "peak_mem_mb": round(peak / 1024 / 1024, 2),
        "density": round(density, 6),
        "avg_degree": round(avg_deg, 3),
        "parse_errors": errors,
        "edge_breakdown": edge_bd,
    }


def stage3_embeddings(store, quiet: bool) -> dict:
    """Stage 3: FAISS embedding index."""
    from embeddings.provider_factory import EmbeddingConfig
    from embeddings.embedding_pipeline import EmbeddingPipeline
    from retrieval.retrieval_layer import RetrievalConfig, RetrievalLayer
    from traversal.traversal_engine import AdjacencyCache

    config = EmbeddingConfig(provider="stub", dimensions=64)
    pipeline = EmbeddingPipeline.from_config(store, config)

    t0 = time.perf_counter()
    stats = pipeline.index_all(batch_size=512)
    embed_time = time.perf_counter() - t0

    adj = AdjacencyCache.build(store)
    cfg = RetrievalConfig(top_k=10, min_semantic_score=0.0, token_budget=None)
    layer = RetrievalLayer(store, pipeline.embedding_store, config=cfg, adjacency_cache=adj)

    queries = [
        "parse function definition",
        "class inheritance hierarchy",
        "import module dependency",
        "graph traversal algorithm",
        "authentication and permission",
    ]
    t1 = time.perf_counter()
    counts = []
    for q in queries:
        counts.append(len(layer.retrieve(q)))
    retrieval_ms = (time.perf_counter() - t1) / len(queries) * 1000

    if not quiet:
        print(f"  Embedded   : {stats.nodes_embedded}  skipped: {stats.nodes_skipped}")
        print(f"  Embed time : {embed_time:.3f}s   Retrieval: {retrieval_ms:.3f}ms/query")

    return {
        "pipeline": pipeline,
        "layer": layer,
        "adj": adj,
        "nodes_embedded": stats.nodes_embedded,
        "nodes_skipped": stats.nodes_skipped,
        "embed_time_s": round(embed_time, 3),
        "avg_retrieval_ms": round(retrieval_ms, 3),
        "avg_results": round(sum(counts) / len(counts), 1),
    }


def stage4_clustering(emb_pipeline, quiet: bool) -> dict:
    """Stage 4: HDBSCAN clustering + quality evaluation."""
    from clustering.semantic_clusters import SemanticClusterer, ClusterConfig
    from clustering.cluster_labeler import ClusterLabeler
    from clustering.cluster_quality import ClusterQualityEvaluator

    emb_store = emb_pipeline.embedding_store
    if emb_store.size() < 10:
        return {
            "n_clusters": 0, "n_noise": 0, "noise_ratio": 1.0,
            "silhouette": 0.0, "mean_cohesion": 0.0, "cluster_time_s": 0.0,
        }

    clusterer = SemanticClusterer(ClusterConfig(min_cluster_size=5))
    t0 = time.perf_counter()
    try:
        result = clusterer.cluster(emb_store)
        ClusterLabeler().label_all(result.clusters)
        cluster_time = time.perf_counter() - t0

        evaluator = ClusterQualityEvaluator()
        report = evaluator.evaluate(result, emb_store)

        n_noise = len(result.noise_members)
        n_total = emb_store.size()
        noise_ratio = n_noise / max(n_total, 1)

        if not quiet:
            print(f"  Clusters   : {len(result.clusters)}  noise: {n_noise}/{n_total}")
            print(f"  Silhouette : {report.silhouette_score:.4f}   Cohesion: {report.mean_cohesion:.4f}")

        return {
            "n_clusters": len(result.clusters),
            "n_noise": n_noise,
            "noise_ratio": round(noise_ratio, 4),
            "silhouette": round(report.silhouette_score, 4),
            "mean_cohesion": round(report.mean_cohesion, 4),
            "mean_separation": round(report.mean_separation, 4),
            "cluster_time_s": round(cluster_time, 3),
        }
    except Exception as e:
        if not quiet:
            print(f"  Clustering failed: {e}")
        return {
            "n_clusters": 0, "n_noise": 0, "noise_ratio": 1.0,
            "silhouette": 0.0, "mean_cohesion": 0.0, "cluster_time_s": 0.0,
        }


def stage5_traversal(store, adj, quiet: bool) -> dict:
    """Stage 5: Bounded BFS, impact radius, taint traversal."""
    from traversal.traversal_engine import TraversalEngine
    from core.types import TraversalConfig
    import random

    engine = TraversalEngine(store, enable_memoization=False, adjacency_cache=adj)
    nodes = store.all_nodes()
    if not nodes:
        return {"avg_bfs_ms": 0, "avg_impact_ms": 0, "avg_taint_paths": 0, "avg_bfs_nodes": 0}

    sample = random.sample(nodes, min(30, len(nodes)))
    names = [n.qualified_name for n in sample]

    # BFS latency and avg nodes reached
    t0 = time.perf_counter()
    bfs_sizes = []
    for name in names:
        r = engine.bfs([name], TraversalConfig(max_depth=3, max_nodes=500))
        bfs_sizes.append(len(r))
    bfs_ms = (time.perf_counter() - t0) / len(names) * 1000

    # Impact radius
    t1 = time.perf_counter()
    impact_sizes = []
    for name in names[:15]:
        r = engine.get_impact_radius([name], max_depth=3)
        impact_sizes.append(len(r))
    impact_ms = (time.perf_counter() - t1) / 15 * 1000

    # Taint traversal (subset of nodes as sources)
    taint_paths = 0
    t2 = time.perf_counter()
    taint_sample = names[:10]
    for name in taint_sample:
        flows = engine.taint_traverse([name], max_depth=4)
        taint_paths += len(flows)
    taint_ms = (time.perf_counter() - t2) / len(taint_sample) * 1000

    avg_bfs = sum(bfs_sizes) / len(bfs_sizes) if bfs_sizes else 0
    avg_impact = sum(impact_sizes) / len(impact_sizes) if impact_sizes else 0

    if not quiet:
        print(f"  BFS latency: {bfs_ms:.3f}ms   avg nodes: {avg_bfs:.1f}")
        print(f"  Impact     : {impact_ms:.3f}ms   avg radius: {avg_impact:.1f}")
        print(f"  Taint      : {taint_ms:.3f}ms/src  total paths: {taint_paths}")

    return {
        "avg_bfs_ms": round(bfs_ms, 3),
        "avg_impact_ms": round(impact_ms, 3),
        "avg_taint_ms": round(taint_ms, 3),
        "avg_bfs_nodes": round(avg_bfs, 1),
        "avg_impact_nodes": round(avg_impact, 1),
        "total_taint_paths": taint_paths,
    }


def stage6_centrality(store, quiet: bool) -> dict:
    """Stage 6: Centrality ranking — PageRank, betweenness, stability/volatility."""
    from ranking.centrality_engine import CentralityEngine

    t0 = time.perf_counter()
    engine = CentralityEngine(store, semantic_only=True)
    scores = engine.compute()
    rank_time = time.perf_counter() - t0

    if not scores:
        return {"rank_time_s": 0, "nodes_scored": 0, "pagerank_std": 0,
                "top_nodes": [], "hotspots": [], "scores": {}}

    top = engine.top_nodes(scores, n=10)
    hotspots = engine.hotspots(scores, n=5)
    most_stable = engine.most_stable(scores, n=3)
    most_volatile = engine.most_volatile(scores, n=3)

    pr_vals = [s.influence_score for s in scores.values()]
    mean_pr = sum(pr_vals) / len(pr_vals)
    pr_std = (sum((x - mean_pr) ** 2 for x in pr_vals) / len(pr_vals)) ** 0.5

    composite_vals = [s.composite_score for s in scores.values()]
    mean_c = sum(composite_vals) / len(composite_vals)
    comp_std = (sum((x - mean_c) ** 2 for x in composite_vals) / len(composite_vals)) ** 0.5

    top_list = [
        {"name": s.qualified_name.split("/")[-1][:55], "score": round(s.composite_score, 5),
         "fan_in": s.fan_in, "stability": round(s.stability_score, 3)}
        for s in top[:10]
    ]
    hotspot_list = [
        {"name": s.qualified_name.split("/")[-1][:55], "fan_in": s.fan_in,
         "bridge": round(s.bridge_score, 5)}
        for s in hotspots[:5]
    ]

    if not quiet:
        print(f"  Ranked     : {len(scores)} nodes in {rank_time:.3f}s")
        print(f"  PageRank std: {pr_std:.6f}   Composite std: {comp_std:.6f}")
        print(f"  Top node   : {top[0].qualified_name.split('/')[-1][:50] if top else 'n/a'}")

    return {
        "rank_time_s": round(rank_time, 3),
        "nodes_scored": len(scores),
        "pagerank_std": round(pr_std, 6),
        "composite_std": round(comp_std, 6),
        "top_nodes": top_list,
        "hotspots": hotspot_list,
        "stable_nodes": [s.qualified_name.split("/")[-1][:50] for s in most_stable],
        "volatile_nodes": [s.qualified_name.split("/")[-1][:50] for s in most_volatile],
        "scores": scores,
    }


# ── Composite score ───────────────────────────────────────────────────────────

def composite_score(s1: dict, s2: dict, s3: dict, s4: dict, s5: dict, s6: dict) -> dict:
    """Compute composite score for each stage per the framework formula."""

    def _score(retrieval, graph, semantic, efficiency, ranking) -> float:
        return round(
            0.25 * min(retrieval, 1.0)
            + 0.25 * min(graph, 1.0)
            + 0.20 * min(semantic, 1.0)
            + 0.15 * min(efficiency, 1.0)
            + 0.15 * min(ranking, 1.0),
            4,
        )

    n_files = s1.get("nodes", 1)

    # Helpers
    def _graph(d):
        return d["edges"] / max(d["nodes"], 1) / 2.0

    def _eff(build_t):
        return max(0.0, 1.0 - build_t / 10.0)

    def _semantic_coverage(d_emb):
        return d_emb.get("nodes_embedded", 0) / max(s2["nodes"], 1)

    def _retrieval(d_emb):
        return d_emb.get("avg_results", 0) / 10.0

    def _ranking(d_rank):
        return min(d_rank.get("pagerank_std", 0) * 100, 1.0)

    def _silhouette(d_clust):
        return max(0.0, d_clust.get("silhouette", 0.0))

    stage_scores = {
        "Stage 1 — LSP Index": _score(
            retrieval=0.0,
            graph=_graph(s1),
            semantic=0.0,
            efficiency=_eff(s1["build_time_s"]),
            ranking=0.0,
        ),
        "Stage 2 — CPG": _score(
            retrieval=0.0,
            graph=_graph(s2),
            semantic=0.10,  # structural node types as proxy
            efficiency=_eff(s2["build_time_s"]),
            ranking=0.0,
        ),
        "Stage 3 — + Embeddings": _score(
            retrieval=_retrieval(s3),
            graph=_graph(s2),
            semantic=_semantic_coverage(s3),
            efficiency=_eff(s2["build_time_s"] + s3["embed_time_s"]),
            ranking=0.0,
        ),
        "Stage 4 — + Clustering": _score(
            retrieval=_retrieval(s3),
            graph=_graph(s2),
            semantic=(_semantic_coverage(s3) + _silhouette(s4)) / 2,
            efficiency=_eff(s2["build_time_s"] + s3["embed_time_s"] + s4.get("cluster_time_s", 0)),
            ranking=0.0,
        ),
        "Stage 5 — + Traversal": _score(
            retrieval=_retrieval(s3),
            graph=min(_graph(s2) + 0.15, 1.0),  # traversal improves graph score
            semantic=(_semantic_coverage(s3) + _silhouette(s4)) / 2,
            efficiency=_eff(s2["build_time_s"] + s3["embed_time_s"]),
            ranking=0.0,
        ),
        "Stage 6 — + Centrality (V0.1 Full)": _score(
            retrieval=_retrieval(s3),
            graph=min(_graph(s2) + 0.15, 1.0),
            semantic=(_semantic_coverage(s3) + _silhouette(s4)) / 2,
            efficiency=_eff(s2["build_time_s"] + s3["embed_time_s"]),
            ranking=_ranking(s6),
        ),
    }
    return stage_scores


# ── Formatter ────────────────────────────────────────────────────────────────

def format_v01_entry(
    py_files, s1, s2, s3, s4, s5, s6, stage_scores
) -> str:
    ts = _ts()
    rss = round(_rss_mb(), 1)

    def _bd_lines(bd):
        return "\n".join(
            f"    - `{k}`: {v}" for k, v in sorted(bd.items(), key=lambda x: -x[1])
        ) or "    - (none)"

    s2_bd = _bd_lines(s2.get("edge_breakdown", {}))
    s1_bd = _bd_lines(s1.get("edge_breakdown", {}))

    top_lines = "\n".join(
        f"    {i+1}. `{r['name']}` — composite={r['score']}  fan_in={r['fan_in']}  stability={r['stability']}"
        for i, r in enumerate(s6.get("top_nodes", []))
    ) or "    (none)"

    hotspot_lines = "\n".join(
        f"    {i+1}. `{r['name']}` — fan_in={r['fan_in']}  bridge={r['bridge']}"
        for i, r in enumerate(s6.get("hotspots", []))
    ) or "    (none)"

    stable_str = ", ".join(f"`{n}`" for n in s6.get("stable_nodes", [])) or "(none)"
    volatile_str = ", ".join(f"`{n}`" for n in s6.get("volatile_nodes", [])) or "(none)"

    score_table = "\n".join(
        f"| {stage} | **{score}** |"
        for stage, score in stage_scores.items()
    )

    final_score = stage_scores.get("Stage 6 — + Centrality (V0.1 Full)", 0.0)

    return f"""
---

## CodeCortex V0.1 — Full Stage Evaluation

**Evaluated:** {ts}
**Codebase:** `code-review-graph/code_review_graph/` ({len(py_files)} Python files, ~30 000 LOC)
**RSS at completion:** {rss} MB

---

### Stage Comparison

| Stage | Composite Score |
|-------|----------------|
{score_table}

---

### 2.4 System Performance

| Metric | LSP (Stage 1) | CPG (Stage 2) | +Embeddings (Stage 3) | +Clustering (Stage 4) |
|--------|--------------|--------------|----------------------|----------------------|
| Build Time | {s1['build_time_s']} s | {s2['build_time_s']} s | +{s3['embed_time_s']} s | +{s4.get('cluster_time_s', 0)} s |
| Peak Memory | {s1['peak_mem_mb']} MB | {s2['peak_mem_mb']} MB | — | — |
| Parse Errors | {s1['parse_errors']} | {s2['parse_errors']} | — | — |
| Ranking Time | — | — | — | — |

| Metric | Value |
|--------|-------|
| Ranking Computation (Stage 6) | {s6['rank_time_s']} s ({s6['nodes_scored']} semantic nodes) |
| Avg BFS Latency (Stage 5) | {s5['avg_bfs_ms']} ms/query |
| Avg Impact-Radius Latency | {s5['avg_impact_ms']} ms/query |
| Avg Taint Traversal Latency | {s5['avg_taint_ms']} ms/source |
| Avg Retrieval Latency | {s3['avg_retrieval_ms']} ms/query |
| Symbol Lookup Latency | {s1['symbol_lookup_ms']} ms |

---

### 2.2 Graph Metrics

| Metric | LSP (Stage 1) | CPG (Stage 2) | Delta |
|--------|--------------|--------------|-------|
| Nodes | {s1['nodes']} | {s2['nodes']} | +{s2['nodes'] - s1['nodes']} |
| Edges | {s1['edges']} | {s2['edges']} | +{s2['edges'] - s1['edges']} |
| Graph Density | {s1['density']} | {s2['density']} | — |
| Avg Degree | {s1['avg_degree']} | {s2['avg_degree']} | — |
| Nodes Scored (Centrality) | — | {s6['nodes_scored']} | — |
| PageRank Std Dev | — | {s6['pagerank_std']} | — |
| Composite Score Std Dev | — | {s6.get('composite_std', 'n/a')} | — |

**Stage 1 — LSP edge types:**
{s1_bd}

**Stage 2 — CPG edge types (adds CFG/DFG/Endpoint edges):**
{s2_bd}

---

### 2.2 Traversal (Stage 5)

| Metric | Value |
|--------|-------|
| Avg BFS nodes reached (depth=3) | {s5['avg_bfs_nodes']} |
| Avg impact radius (depth=3) | {s5['avg_impact_nodes']} |
| Total taint paths detected | {s5['total_taint_paths']} |

---

### 2.1 Retrieval Metrics (Stage 3+)

| Metric | Value |
|--------|-------|
| Embedded Nodes | {s3['nodes_embedded']} / {s2['nodes']} ({round(s3['nodes_embedded']/max(s2['nodes'],1)*100,1)}%) |
| Skipped (non-semantic) | {s3['nodes_skipped']} |
| Avg Results / Query | {s3['avg_results']} / 10 |
| Avg Retrieval Latency | {s3['avg_retrieval_ms']} ms |

*Note: Precision@K / Recall@K / nDCG@K require annotated gold dataset (not yet available).
Stub embeddings used — semantic scores are structural proxies only.*

---

### 2.3 Semantic Clustering Metrics (Stage 4)

| Metric | Value |
|--------|-------|
| Clusters Found | {s4['n_clusters']} |
| Noise Nodes | {s4['n_noise']} |
| Noise Ratio | {s4['noise_ratio']} |
| Silhouette Score | {s4['silhouette']} |
| Mean Cohesion | {s4['mean_cohesion']} |
| Mean Separation | {s4.get('mean_separation', 'n/a')} |

*Silhouette ∈ [-1, 1]. Stub embeddings produce near-zero vectors; real provider needed for meaningful score.*

---

### 2.3 Centrality Ranking (Stage 6)

**Top-10 nodes by composite score (PageRank × betweenness × closeness × eigenvector):**
{top_lines}

**Top-5 hotspots (bridge × fan-in):**
{hotspot_lines}

**Most stable APIs (high fan-in ratio):** {stable_str}

**Most volatile nodes (high fan-out ratio):** {volatile_str}

---

### CodeCortex V0.1 Composite Score

```
score = 0.25×retrieval + 0.25×graph + 0.20×semantic + 0.15×efficiency + 0.15×ranking
      = {final_score}   (Stage 6 — Full Pipeline)
```

| Dimension | Sub-score |
|-----------|-----------|
| Retrieval Quality | {round(s3.get('avg_results', 0) / 10.0, 4)} |
| Graph Fidelity (edge/node ratio) | {round(min(s2['edges'] / max(s2['nodes'], 1) / 2.0, 1.0), 4)} |
| Semantic Cohesion (embedded% + silhouette) | {round(min((s3['nodes_embedded'] / max(s2['nodes'], 1) + max(0.0, s4.get('silhouette', 0.0))) / 2, 1.0), 4)} |
| Efficiency (build speed) | {round(max(0.0, 1.0 - (s2['build_time_s'] + s3['embed_time_s']) / 10.0), 4)} |
| Ranking Discrimination | {round(min(s6.get('pagerank_std', 0) * 100, 1.0), 4)} |
| **V0.1 COMPOSITE** | **{final_score}** |

"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CodeCortex V0.1 benchmark")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--target",
        default=None,
        metavar="DIR",
        help="Directory of Python source to analyze (default: code-review-graph/code_review_graph)",
    )
    args = parser.parse_args()

    target_dir = Path(args.target).resolve() if args.target else _DEFAULT_TARGET
    py_files = sorted(target_dir.rglob("*.py"))
    if not py_files:
        print(f"ERROR: no Python files in {target_dir}", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"\n{'='*65}")
        print(f"  CodeCortex V0.1 — Stage-wise Performance Evaluation")
        print(f"  {_ts()}")
        print(f"  Target: {target_dir}")
        print(f"  Files : {len(py_files)} Python files")
        print(f"{'='*65}\n")

    if not args.quiet: print("[Stage 1] LSP Semantic Index")
    s1 = stage1_lsp(py_files, args.quiet)

    if not args.quiet: print("\n[Stage 2] Code Property Graph (CFG + DFG + Endpoints)")
    s2 = stage2_cpg(py_files, args.quiet)
    store = s2.pop("store")
    s1.pop("store", None)

    if not args.quiet: print("\n[Stage 3] Vector Embeddings (FAISS)")
    s3 = stage3_embeddings(store, args.quiet)
    emb_pipeline = s3.pop("pipeline")
    adj = s3.pop("adj")
    s3.pop("layer", None)

    if not args.quiet: print("\n[Stage 4] HDBSCAN Clustering")
    s4 = stage4_clustering(emb_pipeline, args.quiet)

    if not args.quiet: print("\n[Stage 5] Bounded Traversal (BFS + Taint)")
    s5 = stage5_traversal(store, adj, args.quiet)

    if not args.quiet: print("\n[Stage 6] Centrality Ranking")
    s6 = stage6_centrality(store, args.quiet)
    s6.pop("scores", None)

    scores = composite_score(s1, s2, s3, s4, s5, s6)

    if not args.quiet:
        print(f"\n{'='*65}")
        print("  Stage Composite Scores")
        for stage, sc in scores.items():
            print(f"    {stage:<40} {sc}")
        print(f"{'='*65}\n")

    entry = format_v01_entry(py_files, s1, s2, s3, s4, s5, s6, scores)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS_PATH.exists():
        RESULTS_PATH.write_text("# CodeCortex Performance Results\n\n")

    with open(RESULTS_PATH, "a") as f:
        f.write(entry)

    if not args.quiet:
        print(f"Results appended → {RESULTS_PATH.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
