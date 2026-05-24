#!/usr/bin/env python3
"""CodeCortex Incremental Performance Evaluation Benchmark.

Evaluates the current phase implementation against the code-review-graph
codebase (~60 Python files, ~30 000 LOC) and appends a timestamped entry
to docs/results.md.

Metrics captured (aligned with code_cortex_incremental_performance_evaluation_framework.md):

  System Performance:
    - Index build time
    - Graph query latency
    - Memory footprint (RSS)
    - Scalability (nodes/second)

  Graph Metrics:
    - Node coverage (total nodes extracted)
    - Edge fidelity (total edges, edge-type breakdown)
    - Graph density
    - Average degree

  Ranking Metrics:
    - Hotspot detection (top-10 ranked nodes)
    - PageRank distribution spread

  Retrieval Metrics:
    - Query response time
    - Result count per query

  Composite CodeCortex Score (Phase 1-8 evaluation):
    score = 0.25*retrieval + 0.25*graph + 0.20*semantic + 0.15*efficiency + 0.15*ranking

Usage:
    python3 tests/benchmarks/benchmark_runner.py

    Optional flags:
      --phase LABEL    Phase label to record (e.g. "Phase-8-Complete")
      --quiet          Suppress terminal output, only write results.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent   # /CodeCortex/
sys.path.insert(0, str(ROOT))

RESULTS_PATH = ROOT / "docs" / "results.md"
_DEFAULT_TARGET = ROOT / "code-review-graph" / "code_review_graph"

# ── Helpers ────────────────────────────────────────────────────────────────

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


# ── Stage benchmarks ───────────────────────────────────────────────────────

def bench_parse_and_build_cpg(py_files: list[Path], quiet: bool) -> dict:
    """Stage 1+3: Parse Python files and build CPG. Returns timing & graph stats."""
    from core.parsers.python_parser import PythonParser
    from graph.graph_store import GraphStore
    from graph.cpg_builder import CPGBuilder

    parser = PythonParser()
    store = GraphStore(":memory:")
    builder = CPGBuilder(store)

    parse_errors = 0
    tracemalloc.start()
    t0 = time.perf_counter()

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            result = parser.parse(source, str(py_file))
            builder.ingest(result)
            if result.errors:
                parse_errors += 1
        except Exception:
            parse_errors += 1

    elapsed = time.perf_counter() - t0
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    node_count = store.node_count()
    edge_count = store.edge_count()
    files_processed = len(py_files)

    from core.types import EdgeKind
    edge_breakdown: dict[str, int] = {}
    for edge in store.all_edges():
        k = edge.kind.value
        edge_breakdown[k] = edge_breakdown.get(k, 0) + 1

    density = (2 * edge_count / max(node_count * (node_count - 1), 1)) if node_count > 1 else 0
    avg_degree = (2 * edge_count / max(node_count, 1))

    if not quiet:
        print(f"  Files parsed : {files_processed}  (errors: {parse_errors})")
        print(f"  Nodes        : {node_count}")
        print(f"  Edges        : {edge_count}")
        print(f"  Build time   : {elapsed:.3f}s")
        print(f"  Peak memory  : {peak_mem / 1024 / 1024:.1f} MB")
        print(f"  Throughput   : {files_processed / elapsed:.1f} files/s")

    return {
        "store": store,
        "files": files_processed,
        "parse_errors": parse_errors,
        "nodes": node_count,
        "edges": edge_count,
        "build_time_s": round(elapsed, 3),
        "peak_mem_mb": round(peak_mem / 1024 / 1024, 2),
        "density": round(density, 6),
        "avg_degree": round(avg_degree, 3),
        "edge_breakdown": edge_breakdown,
        "throughput_files_per_s": round(files_processed / elapsed, 1),
    }


def bench_semantic_index(store, quiet: bool) -> dict:
    """Stage 2: Build SemanticIndex and measure lookup latency."""
    from indexing.semantic_index import SemanticIndex

    t0 = time.perf_counter()
    idx = SemanticIndex(store)
    build_time = time.perf_counter() - t0

    all_nodes = store.all_nodes()
    if not all_nodes:
        return {"index_build_s": round(build_time, 4), "lookup_latency_ms": 0, "symbols": 0}

    # Sample 50 random lookups
    import random
    sample = random.sample(all_nodes, min(50, len(all_nodes)))
    t1 = time.perf_counter()
    hits = 0
    for node in sample:
        info = idx.lookup(node.qualified_name)
        if info:
            hits += 1
    lookup_time = (time.perf_counter() - t1) / len(sample) * 1000  # ms per lookup

    stats = idx.stats()

    if not quiet:
        print(f"  Index build  : {build_time:.4f}s")
        print(f"  Lookup (avg) : {lookup_time:.3f} ms")
        print(f"  Symbols      : {stats['nodes']}")

    return {
        "index_build_s": round(build_time, 4),
        "lookup_latency_ms": round(lookup_time, 3),
        "symbols": stats["nodes"],
        "files_indexed": stats["files"],
    }


def bench_traversal(store, quiet: bool) -> dict:
    """Stage 5+6: BFS traversal latency and impact radius."""
    from traversal.traversal_engine import TraversalEngine
    from core.types import TraversalConfig

    engine = TraversalEngine(store, enable_memoization=False)
    all_nodes = store.all_nodes()
    if not all_nodes:
        return {"avg_bfs_ms": 0, "avg_impact_ms": 0}

    import random
    sample = random.sample(all_nodes, min(20, len(all_nodes)))
    names = [n.qualified_name for n in sample]

    # BFS latency
    t0 = time.perf_counter()
    for name in names:
        engine.bfs([name], TraversalConfig(max_depth=3, max_nodes=500))
    bfs_ms = (time.perf_counter() - t0) / len(names) * 1000

    # Impact radius latency
    t1 = time.perf_counter()
    for name in names:
        engine.get_impact_radius([name], max_depth=3)
    impact_ms = (time.perf_counter() - t1) / len(names) * 1000

    if not quiet:
        print(f"  BFS latency  : {bfs_ms:.3f} ms/query")
        print(f"  Impact radius: {impact_ms:.3f} ms/query")

    return {
        "avg_bfs_ms": round(bfs_ms, 3),
        "avg_impact_ms": round(impact_ms, 3),
        "traversal_samples": len(names),
    }


def bench_ranking(store, quiet: bool) -> dict:
    """Stage 7: Centrality computation and hotspot detection."""
    from ranking.centrality_engine import CentralityEngine

    t0 = time.perf_counter()
    engine = CentralityEngine(store)
    scores = engine.compute()
    elapsed = time.perf_counter() - t0

    if not scores:
        return {"ranking_time_s": 0, "top_hotspots": []}

    top = engine.top_nodes(scores, n=10)
    hotspots = engine.hotspots(scores, n=5)

    # PageRank spread (std dev)
    pr_values = [s.influence_score for s in scores.values()]
    mean_pr = sum(pr_values) / len(pr_values)
    variance = sum((x - mean_pr) ** 2 for x in pr_values) / len(pr_values)
    pr_std = variance ** 0.5

    top_list = [
        {"name": s.qualified_name.split("/")[-1][:60], "score": round(s.composite_score, 5)}
        for s in top[:10]
    ]
    hotspot_list = [
        {"name": s.qualified_name.split("/")[-1][:60], "fan_in": s.fan_in}
        for s in hotspots[:5]
    ]

    if not quiet:
        print(f"  Ranking time : {elapsed:.3f}s")
        print(f"  Nodes scored : {len(scores)}")
        print(f"  PageRank std : {pr_std:.6f}")
        print(f"  Top node     : {top[0].qualified_name.split('/')[-1][:50] if top else 'n/a'}")

    return {
        "ranking_time_s": round(elapsed, 3),
        "nodes_scored": len(scores),
        "pagerank_std": round(pr_std, 6),
        "top_nodes": top_list,
        "hotspots": hotspot_list,
        "_scores": scores,   # passed to bench_retrieval to avoid recompute
    }


def bench_retrieval(store, quiet: bool, precomputed_centrality: dict = None) -> dict:
    """Stage 8: Retrieval layer with Phase 4-10 improvements.

    Uses:
    - EmbeddingPipeline (FAISS + CodeEmbedder — skips VARIABLE/PARAMETER nodes)
    - AdjacencyCache (in-memory edge maps, avoids per-hop SQL)
    - Pre-computed centrality (injected from bench_ranking to skip 7s recompute)
    """
    from embeddings.provider_factory import EmbeddingConfig
    from embeddings.embedding_pipeline import EmbeddingPipeline
    from retrieval.retrieval_layer import RetrievalConfig, RetrievalLayer
    from traversal.traversal_engine import AdjacencyCache

    all_nodes = store.all_nodes()
    if not all_nodes:
        return {"embed_build_s": 0, "avg_retrieval_ms": 0}

    config = EmbeddingConfig(provider="stub", dimensions=64)
    pipeline = EmbeddingPipeline.from_config(store, config)

    t0 = time.perf_counter()
    stats = pipeline.index_all(batch_size=512)
    embed_time = time.perf_counter() - t0

    # Build adjacency cache once — avoids per-hop SQL during traversal
    adj_cache = AdjacencyCache.build(store)

    queries = [
        "parse function definition",
        "class inheritance hierarchy",
        "import module dependency",
        "test function coverage",
        "graph traversal algorithm",
    ]
    cfg = RetrievalConfig(top_k=10, min_semantic_score=0.0, token_budget=None)
    layer = RetrievalLayer(
        store,
        pipeline.embedding_store,
        config=cfg,
        adjacency_cache=adj_cache,
    )
    # Inject pre-computed centrality so the first retrieve() call doesn't
    # trigger a full 7s CentralityEngine.compute() internally.
    if precomputed_centrality:
        layer.set_centrality_cache(precomputed_centrality)

    t1 = time.perf_counter()
    result_counts = []
    for q in queries:
        results = layer.retrieve(q)
        result_counts.append(len(results))
    retrieval_ms = (time.perf_counter() - t1) / len(queries) * 1000

    embedded = stats.nodes_embedded
    if not quiet:
        print(f"  Embed build  : {embed_time:.3f}s  ({embedded} nodes, {stats.nodes_skipped} skipped)")
        print(f"  Retrieval    : {retrieval_ms:.3f} ms/query (avg results: {sum(result_counts)//len(result_counts)})")

    return {
        "embed_build_s": round(embed_time, 3),
        "embedded_nodes": embedded,
        "avg_retrieval_ms": round(retrieval_ms, 3),
        "avg_results_per_query": round(sum(result_counts) / len(result_counts), 1),
    }


# ── Composite score ─────────────────────────────────────────────────────────

def _composite_score(cpg: dict, idx: dict, trav: dict, rank: dict, retr: dict) -> float:
    """
    CodeCortex Score = 0.25*retrieval + 0.25*graph + 0.20*semantic + 0.15*efficiency + 0.15*ranking

    Normalise each dimension to [0,1] against empirical reference values.
    """
    # Retrieval quality: higher avg_results = better (capped at top_k=10)
    retrieval = min(retr.get("avg_results_per_query", 0) / 10.0, 1.0)

    # Graph quality: edge count / node count ratio (target > 1.5 is "good")
    ratio = cpg.get("edges", 0) / max(cpg.get("nodes", 1), 1)
    graph = min(ratio / 2.0, 1.0)

    # Semantic: coverage = nodes per file (target > 5 means good extraction)
    nodes_per_file = cpg.get("nodes", 0) / max(cpg.get("files", 1), 1)
    semantic = min(nodes_per_file / 10.0, 1.0)

    # Efficiency: 1 - normalised build time (target < 2s for 60 files)
    build_t = cpg.get("build_time_s", 10)
    efficiency = max(0.0, 1.0 - build_t / 10.0)

    # Ranking: PageRank spread indicates discrimination ability
    pr_std = rank.get("pagerank_std", 0)
    ranking = min(pr_std * 100, 1.0)

    score = (
        0.25 * retrieval
        + 0.25 * graph
        + 0.20 * semantic
        + 0.15 * efficiency
        + 0.15 * ranking
    )
    return round(score, 4)


# ── Markdown formatter ──────────────────────────────────────────────────────

def _format_results_entry(
    phase_label: str,
    py_files: list[Path],
    cpg: dict,
    idx: dict,
    trav: dict,
    rank: dict,
    retr: dict,
    composite: float,
) -> str:
    ts = _ts()
    rss = round(_rss_mb(), 1)

    edge_bd = cpg.get("edge_breakdown", {})
    edge_lines = "\n".join(
        f"    - {k}: {v}" for k, v in sorted(edge_bd.items(), key=lambda x: -x[1])
    ) or "    - (none)"

    top_nodes = rank.get("top_nodes", [])
    top_lines = "\n".join(
        f"    {i+1}. `{r['name']}` — {r['score']}"
        for i, r in enumerate(top_nodes[:10])
    ) or "    (none)"

    hotspots = rank.get("hotspots", [])
    hotspot_lines = "\n".join(
        f"    {i+1}. `{r['name']}` (fan_in={r['fan_in']})"
        for i, r in enumerate(hotspots[:5])
    ) or "    (none)"

    return f"""
---

## {phase_label}

**Evaluated:** {ts}
**Codebase:** `code-review-graph/code_review_graph/` ({len(py_files)} Python files)
**RSS at end:** {rss} MB

### 2.4 System Performance

| Metric | Value |
|--------|-------|
| Index Build Time | {cpg['build_time_s']} s |
| Peak Memory (traced) | {cpg['peak_mem_mb']} MB |
| Throughput | {cpg['throughput_files_per_s']} files/s |
| Parse Errors | {cpg['parse_errors']} / {cpg['files']} |
| Avg BFS Latency | {trav['avg_bfs_ms']} ms |
| Avg Impact-Radius Latency | {trav['avg_impact_ms']} ms |
| Embed Build Time | {retr['embed_build_s']} s ({retr['embedded_nodes']} nodes) |
| Avg Retrieval Latency | {retr['avg_retrieval_ms']} ms/query |
| Ranking Computation | {rank['ranking_time_s']} s |

### 2.2 Graph Metrics

| Metric | Value |
|--------|-------|
| Node Coverage | {cpg['nodes']} nodes |
| Edge Fidelity | {cpg['edges']} edges |
| Graph Density | {cpg['density']} |
| Average Degree | {cpg['avg_degree']} |
| Nodes Scored (Centrality) | {rank.get('nodes_scored', 'n/a')} |
| PageRank Std Dev | {rank.get('pagerank_std', 'n/a')} |

**Edge type breakdown:**
{edge_lines}

### 2.1 Retrieval Metrics

| Metric | Value |
|--------|-------|
| Semantic Index Build | {idx['index_build_s']} s |
| Symbol Lookup Latency | {idx['lookup_latency_ms']} ms |
| Avg Results / Query | {retr['avg_results_per_query']} |
| Embedded Nodes | {retr['embedded_nodes']} |

*Note: Precision@K and Recall@K require an annotated gold dataset (not yet available).*
*Stub embeddings used — semantic scores are structural proxies only.*

### 2.3 Ranking Intelligence

**Top-10 nodes by composite score:**
{top_lines}

**Top-5 hotspots (bridge × fan-in):**
{hotspot_lines}

### CodeCortex Composite Score

```
score = 0.25×retrieval + 0.25×graph + 0.20×semantic + 0.15×efficiency + 0.15×ranking
      = {composite}
```

| Dimension | Sub-score |
|-----------|-----------|
| Retrieval Quality | {min(retr.get('avg_results_per_query', 0) / 10.0, 1.0):.4f} |
| Graph Quality (edge/node ratio) | {min(cpg['edges'] / max(cpg['nodes'], 1) / 2.0, 1.0):.4f} |
| Semantic Coverage (nodes/file) | {min(cpg['nodes'] / max(cpg['files'], 1) / 10.0, 1.0):.4f} |
| Efficiency (build speed) | {max(0.0, 1.0 - cpg['build_time_s'] / 10.0):.4f} |
| Ranking Discrimination | {min(rank.get('pagerank_std', 0) * 100, 1.0):.4f} |
| **COMPOSITE** | **{composite}** |

"""


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CodeCortex performance benchmark")
    parser.add_argument("--phase", default=None, help="Phase label (e.g. 'Phase-8-Complete')")
    parser.add_argument(
        "--target",
        default=None,
        metavar="DIR",
        help="Directory of Python source to analyze (default: code-review-graph/code_review_graph)",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress terminal output")
    args = parser.parse_args()

    target_dir = Path(args.target).resolve() if args.target else _DEFAULT_TARGET
    py_files = _collect_python_files(target_dir)
    if not py_files:
        print(f"ERROR: No Python files found in {target_dir}", file=sys.stderr)
        sys.exit(1)

    # Auto-detect phase label from docs/PHASE_1_ARCHITECTURE.md or use default
    phase_label = args.phase
    if not phase_label:
        arch_file = ROOT / "PHASE_1_ARCHITECTURE.md"
        if arch_file.exists():
            content = arch_file.read_text()
            import re
            done = re.findall(r"Phase (\d+)[^\|]*\| ✅", content)
            if done:
                phase_label = f"Phases 1–{done[-1]} Complete"
            else:
                phase_label = "Baseline Evaluation"
        else:
            phase_label = "Baseline Evaluation"

    if not args.quiet:
        print(f"\n{'='*60}")
        print(f"  CodeCortex Benchmark — {phase_label}")
        print(f"  {_ts()}")
        print(f"  Target: {target_dir}")
        print(f"  Files : {len(py_files)} Python files")
        print(f"{'='*60}\n")

    # Stage 1+3: Parse & CPG
    if not args.quiet:
        print("[1] Parsing & CPG Build")
    cpg = bench_parse_and_build_cpg(py_files, args.quiet)
    store = cpg.pop("store")

    # Stage 2: Semantic Index
    if not args.quiet:
        print("\n[2] Semantic Index")
    idx = bench_semantic_index(store, args.quiet)

    # Stage 5+6: Traversal
    if not args.quiet:
        print("\n[3] Graph Traversal")
    trav = bench_traversal(store, args.quiet)

    # Stage 7: Ranking
    if not args.quiet:
        print("\n[4] Centrality Ranking")
    rank = bench_ranking(store, args.quiet)

    # Stage 8: Retrieval
    if not args.quiet:
        print("\n[5] Hybrid Retrieval")
    retr = bench_retrieval(store, args.quiet, precomputed_centrality=rank.get("_scores"))

    # Composite
    composite = _composite_score(cpg, idx, trav, rank, retr)

    if not args.quiet:
        print(f"\n{'='*60}")
        print(f"  COMPOSITE CodeCortex Score: {composite}")
        print(f"{'='*60}\n")

    # Write to results.md
    entry = _format_results_entry(phase_label, py_files, cpg, idx, trav, rank, retr, composite)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not RESULTS_PATH.exists():
        header = """# CodeCortex Performance Results

This document records incremental performance evaluations of the CodeCortex
system against the `code-review-graph` Python codebase.

Metrics align with `docs/code_cortex_incremental_performance_evaluation_framework.md`.

Each entry is appended automatically after a phase is completed.

"""
        RESULTS_PATH.write_text(header)

    with open(RESULTS_PATH, "a") as f:
        f.write(entry)

    if not args.quiet:
        print(f"Results appended → {RESULTS_PATH.relative_to(ROOT)}\n")

    return composite


if __name__ == "__main__":
    main()
