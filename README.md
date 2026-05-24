# CodeCortex

**Adaptive semantic intelligence engine for large-scale code understanding.**

CodeCortex transforms source code into a living, queryable semantic graph — combining Code Property Graphs, real embedding models, approximate nearest-neighbor retrieval, and intelligent graph traversal into a unified pipeline purpose-built for production-scale repositories.

---

## What CodeCortex Is

CodeCortex is not a linter, not a static analyzer, and not a dependency graph visualizer. It is a **semantic intelligence infrastructure** layer that answers questions like:

- *Which components are structurally central to this codebase?*
- *What is the full blast radius if `UserService.authenticate` changes?*
- *Which functions are semantically similar to "authentication middleware"?*
- *What does the dataflow look like through this API endpoint?*

It ingests multi-language source code and produces a compressed, semantically-enriched graph that supports sub-20 ms retrieval across repositories of 100k+ nodes.

---

## Core Architecture

```
Source Files (Python · JavaScript · TypeScript · PHP)
        │
        ▼
┌───────────────────────────────────┐
│   Tier 1 — Semantic Index         │  LSP · SCIP · Jedi · Tree-sitter
│   Symbols · APIs · Imports        │  ~O(files) build time
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Tier 2 — Code Property Graph    │  AST + CFG + DFG + Endpoints
│   Hybrid CPG (pruned, bounded)    │  Semantic node filtering
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Tier 3 — Embedding Layer        │  CodeBERT · UniXcoder · local models
│   FAISS IVF · HNSW · cosine ANN  │  Batched · incremental · cached
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Clustering & Community          │  Louvain · HDBSCAN · spectral
│   Adaptive cluster sizing         │  Semantic group labeling
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Traversal & Ranking             │  Beam BFS · Personalized PageRank
│   Semantic-aware path scoring     │  Impact radius · Taint flow
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Retrieval Orchestration         │  Hybrid semantic + structural
│   Token-budget context generation │  AI-ready output
└───────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Hybrid CPG, not full-graph | Full CPG causes node explosion. Semantic filtering retains 20–40% of nodes while preserving retrieval quality. |
| AdjacencyCache (in-memory) | Eliminates per-hop SQL overhead. BFS latency drops from ~1400 ms to <1 ms. |
| Semantic-only centrality | Filtering out VARIABLE/PARAMETER/CALLSITE nodes gives 32× better PageRank discrimination. |
| On-demand CFG/DFG | Tier-1 index builds in seconds; deep CPG is only expanded for files touched by a query. |
| Incremental embedding updates | Only re-embed nodes whose source files changed (mtime-driven delta). |

---

## Major Capabilities

### Semantic Retrieval
Natural language queries return ranked, structurally-aware results across the full codebase. Results are fused from embedding similarity, graph centrality, and cluster membership.

```python
from codecortex import CodeCortexPipeline

pipeline = CodeCortexPipeline.from_directory("/path/to/repo")
context = pipeline.query("authentication middleware", max_tokens=3000)
print(context)  # AI-ready context string
```

### Impact Analysis
Trace the full blast radius of any change — direct callers, transitive dependents, affected tests, and cross-file propagation.

```python
impact = pipeline.impact("UserService.authenticate", max_depth=4)
print(impact.to_dict())
# {root, direct_callers, affected_files, affected_tests, transitive_affected}
```

### Hotspot Detection
Identify structurally critical nodes by composite centrality: PageRank, betweenness, fan-in/fan-out, eigenvector.

```python
hotspots = pipeline.top_nodes(n=20)
```

### Incremental Indexing
Reindex a single changed file without rebuilding the full graph.

```python
pipeline.reindex_file("/path/to/repo/auth/service.py")
```

---

## Repository Structure

```
codecortex/                 Main package
  __init__.py               Public API surface
  cli.py                    Unified CLI entry point
  config.py                 YAML config loader
  index.py                  Index subcommand
  query.py                  Query subcommand

core/                       Shared types and parser framework
  types.py                  NodeKind, EdgeKind, NodeInfo, EdgeInfo
  parser_framework.py       LanguageParser ABC + ParserRegistry
  parsers/                  Language-specific parsers
    python_parser.py
    javascript_parser.py
    typescript_parser.py
    php_parser.py

graph/                      Code Property Graph
  schema.py                 CPGNode, CPGEdge
  graph_store.py            SQLite-backed persistent store
  cpg_builder.py            AST → CPG ingestion
  cfg_builder.py            Control Flow Graph
  dfg_builder.py            Data Flow Graph
  endpoint_detector.py      HTTP API / route detection
  graph_pruner.py           Semantic node filtering (60–80% reduction)

indexing/                   Semantic symbol indexing
  semantic_index.py         In-memory symbol lookup
  symbol_resolver.py        Cross-file name resolution
  symbol_cache.py           SQLite persistent symbol table
  reference_graph.py        Cross-file occurrence store
  jedi_provider.py          Python semantic analysis (Jedi)
  lsp_provider.py           JSON-RPC LSP client
  scip_provider.py          SCIP index reader
  graph_enricher.py         Upgrades unresolved CALLS edges

embeddings/                 Vector embedding layer
  code_embedder.py          Code → text normalization + rich context
  embedding_pipeline.py     CPG → FAISS index (batch, incremental)
  embedding_store.py        FAISS wrapper with cosine similarity
  provider_factory.py       stub | local | openai provider selection

clustering/                 Semantic community detection
  semantic_clusters.py      HDBSCAN + KMeans fallback
  louvain_clusterer.py      Graph-based Louvain community detection
  cluster_labeler.py        Token frequency + module dominance labels
  cluster_quality.py        Silhouette, cohesion, separation metrics
  cluster_store.py          SQLite cluster persistence

traversal/                  Graph traversal engine
  traversal_engine.py       AdjacencyCache, BFS, DFS, TaintFlow
  beam_traversal.py         Beam search with semantic scoring
  query_optimizer.py        Query plan optimization

ranking/                    Centrality and ranking
  centrality_engine.py      PageRank, betweenness, closeness, eigenvector
  ppr_engine.py             Personalized PageRank (query-relative ranking)

retrieval/                  Retrieval orchestration
  retrieval_layer.py        Hybrid semantic + structural retrieval

pipeline/                   End-to-end orchestration
  context_builder.py        CodeCortexPipeline (build → query → impact)

performance/                Incremental performance optimizations
  graph_snapshot.py         JSON graph persistence + mtime delta
  parallel_indexer.py       ThreadPoolExecutor multi-file indexing
  delta_embedder.py         Mtime-driven incremental re-embedding

tests/                      Test suite (223 tests)
  test_phase1_core.py
  test_phase2_indexing.py
  test_phase3_cpg.py
  test_phase4_embeddings.py
  test_phase5_clustering.py
  test_phase6_traversal.py
  test_phase7_ranking.py
  test_phase8_retrieval.py
  benchmarks/               Performance benchmarks
    benchmark_runner.py     Composite score benchmark
    bench_v01.py            Stage-wise V0.1 evaluation
    bench_v02.py            Per-phase V0.2 evaluation

docs/                       Documentation
  results.md                Benchmark history
codecortex.yaml             Default configuration
```

---

## Installation

### Prerequisites

- Python 3.10+
- pip

### Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/nafees-mansoor/codecortex.git
cd codecortex

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate

# 3. Install core dependencies
pip install -r requirements.txt

# 4. Install the package in editable mode
pip install -e .

# 5. (Optional) Install all optional dependencies
pip install -e ".[all]"

# 6. Run the test suite
pytest tests/ --ignore=tests/benchmarks
```

### Production Setup

```bash
# Minimal production install (no dev tools)
pip install codecortex

# With GPU-accelerated embeddings
pip install "codecortex[embeddings]"
pip install torch --index-url https://download.pytorch.org/whl/cu121

# With full semantic indexing stack
pip install "codecortex[all]"
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CODECORTEX_EMBEDDING_PROVIDER` | `stub` | `stub` · `local` · `openai` |
| `OPENAI_API_KEY` | — | Required for `openai` provider |
| `CODECORTEX_CACHE_DIR` | `.codecortex/` | Graph snapshot and cache directory |
| `CODECORTEX_LOG_LEVEL` | `WARNING` | `DEBUG` · `INFO` · `WARNING` · `ERROR` |

---

## Quick Start

### Indexing a Repository

```bash
# Minimal index (call graph + imports, fast)
codecortex index /path/to/repo

# Full CPG with embeddings and clustering
codecortex index /path/to/repo --mode cpg --enable-embeddings --enable-clustering

# Hybrid mode: Tier-1 index at build time, CFG/DFG expanded on demand
codecortex index /path/to/repo --on-demand-cfg --on-demand-dfg

# Save graph snapshot for fast subsequent loads
codecortex index /path/to/repo --save-snapshot .codecortex/graph.json

# Limit to specific languages
codecortex index /path/to/repo --languages py ts
```

### Querying

```bash
# Natural language query
codecortex query --query "authentication middleware" --target /path/to/repo

# Impact analysis
codecortex query --impact UserService.authenticate --target /path/to/repo

# JSON output
codecortex query --query "HTTP routing" --json --target /path/to/repo

# With centrality ranking and larger context
codecortex query \
  --query "database connection pooling" \
  --enable-centrality-ranking \
  --max-tokens 5000 \
  --target /path/to/repo
```

### Python API

```python
from codecortex import CodeCortexPipeline, PipelineConfig

# Configure
config = PipelineConfig(
    embedding_backend="local",      # or "stub" / "openai"
    enable_clustering=True,
    use_louvain=True,               # graph-based community detection
    use_beam_traversal=True,        # semantic-aware beam search
    use_ppr=True,                   # personalized PageRank
    use_graph_pruning=True,         # 60–80% node reduction
)

# Build once
pipeline = CodeCortexPipeline.from_directory("/path/to/repo", config=config)

# Query
context = pipeline.query("authentication flow", max_tokens=3000)

# Retrieve raw results
results = pipeline.retrieve("database models")
for r in results:
    print(r.qualified_name, r.score)

# Impact analysis
impact = pipeline.impact("UserService.authenticate", max_depth=5)

# Hotspot detection
top = pipeline.top_nodes(n=20)

# Incremental update
pipeline.reindex_file("/path/to/repo/auth/service.py")
```

---

## Configuration

`codecortex.yaml` controls all pipeline behavior:

```yaml
codecortex:
  # Graph construction
  enable_cpg: true           # Full CPG with CFG/DFG/endpoints
  on_demand_cfg: false       # Defer CFG to query time (Tier-2 mode)
  on_demand_dfg: false       # Defer DFG to query time

  # Embedding provider: stub | local | openai
  embedding_backend: stub
  embedding_dimensions: 384

  # Clustering
  enable_clustering: true
  min_cluster_size: 5

  # Retrieval
  retrieval_top_k: 20
  token_budget: 4000
  min_semantic_score: 0.0

  # Traversal
  bfs_depth: 3
  max_nodes_per_query: 500

  # Centrality
  centrality_ranking: true
```

CodeCortex searches for `codecortex.yaml` by walking up from the target directory.

---

## Performance Modes

### Lightweight Mode
Fast index, structural retrieval only. No embeddings, no clustering.

```yaml
codecortex:
  enable_cpg: false          # Call + import graph only
  enable_embeddings: false
  enable_clustering: false
  centrality_ranking: false
```

Build time: <1s for 100 files. BFS latency: <1 ms.

### Balanced Mode
Default. Full CPG with stub embeddings for structural + approximate semantic retrieval.

```yaml
codecortex:
  enable_cpg: true
  embedding_backend: stub
  enable_clustering: true
```

Build time: ~8s for 60 files. Retrieval: <5 ms.

### Deep Semantic Analysis Mode
Real code-aware embeddings, Louvain clustering, beam traversal, PPR ranking.

```yaml
codecortex:
  enable_cpg: true
  embedding_backend: local   # sentence-transformers
  enable_clustering: true
  use_louvain: true
  use_beam_traversal: true
  use_ppr: true
  use_graph_pruning: true
```

Best retrieval quality. Requires `pip install "codecortex[all]"`.

### Enterprise Scale Mode
On-demand CFG/DFG + graph pruning + parallel indexing + incremental updates.

```yaml
codecortex:
  on_demand_cfg: true
  on_demand_dfg: true
  use_graph_pruning: true
  embedding_backend: openai
  embedding_dimensions: 3072  # text-embedding-3-large
```

Designed for monorepos with 500k+ LOC.

---

## Indexing Workflow

```
1. Parser discovers source files (py/js/ts/php)
2. Language parser extracts NodeInfo + EdgeInfo from AST
3. CPGBuilder ingests into GraphStore (SQLite, :memory:, or file)
4. If enable_cfg: CFGBuilder adds CONTROLS edges
5. If enable_dfg: DFGBuilder adds READS/WRITES edges
6. If use_graph_pruning: GraphPruner filters non-semantic nodes
7. EmbeddingPipeline generates vectors and indexes into FAISS
8. Clustering groups semantically similar nodes
9. AdjacencyCache builds in-memory edge maps
10. CentralityEngine computes composite scores
11. RetrievalLayer is initialized and ready for queries
```

## Retrieval Workflow

```
Query: "authentication middleware"
  │
  ├─ EmbeddingStore.search(query_vec, top_k=20)   ← ANN search, <1ms
  │     Returns: [(qualified_name, score), ...]
  │
  ├─ AdjacencyCache.expand(seed_nodes, depth=3)   ← in-memory BFS, <1ms
  │     Returns: neighbor nodes
  │
  ├─ CentralityEngine.score(nodes)                ← pre-computed, O(1)
  │     Adjusts: structural importance weighting
  │
  ├─ ClusterStore.lookup(nodes)                   ← cluster membership
  │     Adjusts: community coherence bonus
  │
  └─ RetrievalLayer.rank_and_budget(results)      ← token budget enforcement
        Returns: AI-ready context string
```

---

## Benchmarking

Run the composite performance benchmark against any codebase:

```bash
# Against included reference codebase
python tests/benchmarks/benchmark_runner.py --phase "V0.2-Full"

# Against your own repository
python tests/benchmarks/benchmark_runner.py \
  --target /path/to/your/repo/src \
  --phase "My-Project"

# Stage-wise V0.1 evaluation
python tests/benchmarks/bench_v01.py --target /path/to/repo

# Per-phase V0.2 evaluation
python tests/benchmarks/bench_v02.py --target /path/to/repo

# Suppress output, results only in docs/results.md
python tests/benchmarks/benchmark_runner.py --quiet
```

### Composite Score Formula

```
CodeCortex Score = 0.25×retrieval + 0.25×graph + 0.20×semantic + 0.15×efficiency + 0.15×ranking
```

| Dimension | What it measures |
|-----------|-----------------|
| Retrieval quality | Avg results returned per query (structural proxy) |
| Graph quality | Edge/node ratio (relationship density) |
| Semantic coverage | Nodes extracted per file |
| Efficiency | Index build speed (1 - normalized build time) |
| Ranking discrimination | PageRank standard deviation |

---

## Testing

```bash
# Run all unit tests
pytest tests/ --ignore=tests/benchmarks

# With coverage
pytest tests/ --ignore=tests/benchmarks --cov=. --cov-report=term-missing

# Specific phase
pytest tests/test_phase3_cpg.py -v

# All phases
pytest tests/test_phase1_core.py \
       tests/test_phase2_indexing.py \
       tests/test_phase3_cpg.py \
       tests/test_phase4_embeddings.py \
       tests/test_phase5_clustering.py \
       tests/test_phase6_traversal.py \
       tests/test_phase7_ranking.py \
       tests/test_phase8_retrieval.py \
       -v
```

---

## Developer Experience

### Debugging

Enable debug logging to trace pipeline decisions:

```bash
CODECORTEX_LOG_LEVEL=DEBUG codecortex index /path/to/repo
```

Or in Python:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Profiling

```bash
python -m cProfile -o profile.out tests/benchmarks/benchmark_runner.py --quiet
python -c "import pstats; p = pstats.Stats('profile.out'); p.sort_stats('cumulative'); p.print_stats(20)"
```

### Troubleshooting

**`ImportError: No module named 'tree_sitter_python'`**
```bash
pip install tree-sitter-python
```

**`FAISS index empty / 0 nodes embedded`**
The default `stub` provider skips embeddings. Set `embedding_backend: local` and install sentence-transformers:
```bash
pip install sentence-transformers
```

**CPG build is slow (>10s for 60 files)**
Enable graph pruning and on-demand CFG/DFG:
```yaml
use_graph_pruning: true
on_demand_cfg: true
on_demand_dfg: true
```

**Clustering produces 0 clusters**
HDBSCAN requires ≥ `min_cluster_size` real (non-stub) embeddings. Use `embedding_backend: local` or lower `min_cluster_size`.

**`jedi.InternalError` during indexing**
Jedi is optional. CodeCortex falls back to tree-sitter parsing if Jedi fails. No action needed.

---

## Extensibility

### Adding a Language Parser

```python
from core.parser_framework import LanguageParser
from core.types import ParseResult

class RustParser(LanguageParser):
    @property
    def language(self) -> str:
        return "rust"

    def parse(self, source: str, file_path: str) -> ParseResult:
        # Extract nodes and edges
        ...
```

### Adding an Embedding Provider

```python
from embeddings.provider_factory import EmbeddingProvider
import numpy as np

class MyProvider(EmbeddingProvider):
    def embed(self, texts: list[str]) -> np.ndarray:
        # Return shape (len(texts), dim)
        ...
```

### Custom Traversal Strategy

```python
from traversal.traversal_engine import TraversalEngine
from core.types import TraversalConfig

engine = TraversalEngine(store)
nodes = engine.bfs(
    seed=["MyClass.my_method"],
    config=TraversalConfig(max_depth=5, edge_filter=["CALLS", "IMPORTS_FROM"]),
)
```

---

## Evaluation Methodology

CodeCortex uses a multi-dimensional composite score that rewards semantic intelligence, not just graph size:

- **Graph metrics**: node coverage, edge fidelity, density, avg degree
- **Semantic metrics**: nodes/file coverage, embedding quality
- **Retrieval metrics**: result count, query latency, embedding build time
- **Ranking metrics**: PageRank spread (discrimination power)
- **Efficiency metrics**: index build time, memory footprint, throughput

The scoring framework explicitly penalizes graph explosion and rewards compact, semantically-rich graphs.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

**Nafees Mansoor**
