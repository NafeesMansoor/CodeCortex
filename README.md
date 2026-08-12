# CodeCortex

**Adaptive semantic intelligence engine for large-scale code understanding.**

> Version 1.0.1

CodeCortex transforms source code into a living, queryable semantic graph — combining Code Property Graphs, real embedding models, approximate nearest-neighbor retrieval, intelligent graph traversal, and an interactive human-facing visualization layer into a unified platform purpose-built for production-scale repositories.

---

## What CodeCortex Is

CodeCortex is not a linter, not a static analyzer, and not a dependency graph visualizer. It is a **semantic intelligence infrastructure** layer that answers questions like:

- *Which components are structurally central to this codebase?*
- *What is the full blast radius if `UserService.authenticate` changes?*
- *Which functions are semantically similar to "authentication middleware"?*
- *What does the dataflow look like through this API endpoint?*
- *How are these modules architecturally coupled?*

It ingests multi-language source code and produces a compressed, semantically-enriched graph that supports sub-20 ms retrieval across repositories of 100k+ nodes — and renders as a fully interactive visual graph for human exploration.

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
│   Tier 2 — Hybrid Code Property   │  AST + CFG + DFG + Endpoints
│   Graph  (pruned, bounded)        │  60–80% node reduction
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Tier 3 — Embedding Layer        │  CodeBERT · local models · OpenAI
│   FAISS IVF · HNSW · cosine ANN  │  Batched · incremental · cached
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Clustering & Community          │  Louvain · HDBSCAN · adaptive
│   Semantic group labeling         │  Cluster confidence scoring
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│   Traversal & Ranking             │  Beam BFS · Personalized PageRank
│   Semantic-aware path scoring     │  Impact radius · Taint flow
└──────────────────┬────────────────┘
                   │
             ┌─────┴──────┐
             ▼            ▼
┌────────────────┐  ┌──────────────────────┐
│   Retrieval    │  │  Graph Visualization  │
│   Layer        │  │  Interactive Explorer │
│   AI-ready     │  │  Human-centric UI     │
│   context      │  │  Zero pipeline impact │
└────────────────┘  └──────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Hybrid CPG, not full-graph | Full CPG causes node explosion. Semantic filtering retains 20–40% of nodes while preserving retrieval quality. |
| AdjacencyCache (in-memory) | Eliminates per-hop SQL overhead. BFS latency drops from ~1400 ms to <1 ms. |
| Semantic-only centrality | Filtering out VARIABLE/PARAMETER/CALLSITE nodes gives 32× better PageRank discrimination. |
| On-demand CFG/DFG | Tier-1 index builds in seconds; deep CPG is only expanded for files touched by a query. |
| Incremental embedding updates | Only re-embed nodes whose source files changed (mtime-driven delta). |
| Visualization as a separate layer | Graph explorer consumes exported data — zero changes to pipeline internals. |

---

## Major Capabilities

### Semantic Retrieval
Natural language queries return ranked, structurally-aware results. Results are fused from embedding similarity, graph centrality, and cluster membership.

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

### Interactive Graph Visualization
Launch a full-featured browser-based graph explorer. Navigate the codebase visually, inspect semantic clusters, trace impact paths, and identify hotspots — all without modifying a line of pipeline code.

```bash
codecortex visualize /path/to/repo
# Opens http://localhost:7979 in the browser
```

### Incremental Indexing
Reindex a single changed file without rebuilding the full graph.

```python
pipeline.reindex_file("/path/to/repo/auth/service.py")
```

---

## Interactive Graph Explorer

The visualization layer is a purpose-built human interface on top of the semantic graph. It runs as a local web server and opens automatically in the browser.

### Features

| Feature | Description |
|---------|-------------|
| Force / Hierarchy / Circle / Grid / Concentric layouts | Switch between graph layouts instantly |
| Node type legend | Click to show/hide nodes by kind (Function, Class, Endpoint, etc.) |
| Semantic cluster chips | Filter the graph to a single semantic community |
| Edge type legend | Understand CALLS, IMPORTS, INHERITS, CONTROLS, READS, WRITES |
| Hotspot highlighting | One-click to dim all non-hotspot nodes |
| Variable/Parameter toggle | Show or hide low-level structural noise |
| Centrality slider | Progressively filter out low-importance nodes |
| Symbol search | Fuzzy match by name, kind, or file path |
| Node detail panel | Click any node: file, line, centrality score, cluster, docstring, connections |
| Neighbor traversal | Click neighbors in the panel to jump to them |
| Minimap | Viewport overview with live update |
| Zoom / Pan / Fit | Full graph navigation controls |
| Keyboard shortcuts | `Ctrl/Cmd+F` search · `Esc` deselect |

### Launch the Explorer

```bash
# Basic launch
codecortex visualize .

# Point at any repository
codecortex visualize /path/to/your/repo

# Custom port, no auto-open
codecortex visualize . --port 8080 --no-open

# Limit to specific languages
codecortex visualize . --languages py ts
```

### What You Can Do in the Explorer

**Architecture discovery** — use the Hierarchy layout to see module structure from top to bottom.

**Hotspot review** — click "Hotspots" in the header to highlight the top 10% most-connected nodes. These are your highest-risk change points.

**Cluster exploration** — click a semantic cluster chip to isolate one community and understand what it does.

**Impact tracing** — click any node, inspect its connections in the detail panel, and jump through the call chain.

**Dependency audit** — filter to IMPORTS_FROM edges only to map inter-module dependencies cleanly.

**Onboarding** — point a new team member at the graph to let them explore the codebase structure visually before reading a single line of code.

---

## Repository Structure

```
codecortex/                 Main package + unified CLI
  __init__.py               Public API surface
  cli.py                    Unified CLI (index / query / visualize)
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

visualization/              Interactive graph explorer (human-facing)
  __init__.py
  graph_exporter.py         Pipeline state → visualization JSON
  server.py                 Local HTTP server + browser launcher
  static/
    index.html              Full interactive graph UI (Cytoscape.js)

tests/                      Test suite (223 tests)
  test_phase1_core.py       … through test_phase10_performance.py
  benchmarks/               Performance benchmarks
    benchmark_runner.py     Composite score benchmark
    bench_v01.py            Stage-wise evaluation
    bench_v02.py            Per-phase architectural evaluation

codecortex.yaml             Default configuration
```

---

## Installation

### Prerequisites

- Python 3.10+
- pip / pip3

> **macOS note:** Homebrew Python enforces PEP 668 and blocks global `pip install`.
> Always install into a virtual environment (step 2 below).
> If `pip` is not found, use `pip3` or `python3 -m pip` instead.

### Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/NafeesMansoor/codecortex.git
cd codecortex

# 2. Create a virtual environment (required on macOS Homebrew Python)
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate

# 3. Install core dependencies
pip install -r requirements.txt

# 4. Install in editable mode
pip install -e .

# 5. Install tree-sitter-language-pack (required for PHP/JS/TS parsing)
pip install tree-sitter-language-pack

# 6. Optional: real embeddings + full semantic stack
pip install -e ".[all]"

# 7. Verify
pytest tests/ --ignore=tests/benchmarks
codecortex --help
```

### Production Setup

```bash
# Minimal (structural analysis only)
pip install codecortex

# With GPU-accelerated embeddings
pip install "codecortex[embeddings]"
pip install torch --index-url https://download.pytorch.org/whl/cu121

# Full stack
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

### Index a Repository

```bash
# Fast structural index
codecortex index .

# Full CPG with embeddings and clustering
codecortex index . --mode cpg --enable-embeddings --enable-clustering

# Hybrid: Tier-1 at build time, CFG/DFG on demand
codecortex index . --on-demand-cfg --on-demand-dfg

# Save snapshot for fast reload
codecortex index . --save-snapshot .codecortex/graph.json

# Limit languages
codecortex index . --languages py ts

# Exclude extra project paths, on top of the built-in defaults
codecortex index . --languages php --exclude storage/ bootstrap/cache/
```

> **Dependency directories are skipped automatically.** Virtualenvs (`venv/`,
> `.venv/`), `node_modules/`, `vendor/`, `site-packages/`, `__pycache__/`,
> build output and VCS metadata are pruned before the walker descends into
> them, and the repository `.gitignore` is honoured. Pass
> `--no-default-excludes` / `--no-gitignore` to opt out, or set `exclude_dirs`
> in `codecortex.yaml` to replace the built-in list.

### Query

```bash
# Natural language query
codecortex query --query "authentication middleware" --target .

# Impact analysis
codecortex query --impact UserService.authenticate --target .

# JSON output
codecortex query --query "HTTP routing" --json --target .

# With centrality ranking and larger context budget
codecortex query \
  --query "database connection pooling" \
  --enable-centrality-ranking \
  --max-tokens 5000 \
  --target .
```

### Visualize

```bash
# Launch graph explorer (opens browser automatically)
codecortex visualize .

# Custom port
codecortex visualize /path/to/repo --port 8080

# Headless (server only, no browser open)
codecortex visualize . --no-open

# Limit to Python and TypeScript
codecortex visualize . --languages py ts
```

### Python API

```python
from codecortex import CodeCortexPipeline, PipelineConfig

config = PipelineConfig(
    embedding_backend="local",
    enable_clustering=True,
    use_louvain=True,
    use_beam_traversal=True,
    use_ppr=True,
    use_graph_pruning=True,
)

pipeline = CodeCortexPipeline.from_directory("/path/to/repo", config=config)

# Semantic query
context = pipeline.query("authentication flow", max_tokens=3000)

# Ranked results
results = pipeline.retrieve("database models")
for r in results:
    print(r.qualified_name, r.score)

# Impact analysis
impact = pipeline.impact("UserService.authenticate", max_depth=5)

# Hotspot detection
top = pipeline.top_nodes(n=20)

# Incremental update
pipeline.reindex_file("/path/to/repo/auth/service.py")

# Export for visualization
from visualization import export_pipeline
graph_json = export_pipeline(pipeline, project_name="my-repo")
```

---

## Configuration

`codecortex.yaml` in your project root:

```yaml
codecortex:
  # Graph construction
  enable_cpg: true
  on_demand_cfg: false
  on_demand_dfg: false

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

  # Paths excluded from indexing (relative to the indexed root)
  # Useful for Laravel/Node projects with large vendor/node_modules trees
  exclude_paths:
    - vendor/
    - node_modules/
    - storage/
    - bootstrap/cache/
```

---

## Performance Modes

### Lightweight Mode
Fast index, structural retrieval only. Builds in <1s for 100 files.

```yaml
codecortex:
  enable_cpg: false
  enable_embeddings: false
  enable_clustering: false
  centrality_ranking: false
```

### Balanced Mode
Default. Full CPG with stub embeddings. Builds in ~8s for 60 files.

```yaml
codecortex:
  enable_cpg: true
  embedding_backend: stub
  enable_clustering: true
```

### Deep Semantic Analysis Mode
Real code-aware embeddings, Louvain clustering, beam traversal, personalized PageRank. Best retrieval quality.

```yaml
codecortex:
  enable_cpg: true
  embedding_backend: local
  enable_clustering: true
  use_louvain: true
  use_beam_traversal: true
  use_ppr: true
  use_graph_pruning: true
```

Requires: `pip install "codecortex[all]"`

### Enterprise Scale Mode
On-demand CPG expansion + graph pruning + parallel indexing + incremental updates. Designed for monorepos with 500k+ LOC.

```yaml
codecortex:
  on_demand_cfg: true
  on_demand_dfg: true
  use_graph_pruning: true
  embedding_backend: openai
  embedding_dimensions: 3072
```

---

## Benchmarking

```bash
# Run against any codebase
python tests/benchmarks/benchmark_runner.py --target /path/to/repo --phase "v1.0"

# Stage-wise evaluation
python tests/benchmarks/bench_v01.py --target /path/to/repo

# Per-feature architectural evaluation
python tests/benchmarks/bench_v02.py --target /path/to/repo

# Quiet mode (results appended to docs/results.md)
python tests/benchmarks/benchmark_runner.py --target . --quiet
```

### Composite Score Formula

```
CodeCortex Score = 0.25×retrieval + 0.25×graph + 0.20×semantic + 0.15×efficiency + 0.15×ranking
```

| Dimension | What it measures |
|-----------|-----------------|
| Retrieval quality | Results per query (structural proxy) |
| Graph quality | Edge/node density ratio |
| Semantic coverage | Nodes extracted per file |
| Efficiency | Index build speed |
| Ranking discrimination | PageRank standard deviation |

---

## Testing

```bash
# All 223 unit tests
pytest tests/ --ignore=tests/benchmarks

# With coverage
pytest tests/ --ignore=tests/benchmarks --cov=. --cov-report=term-missing

# Specific phase
pytest tests/test_phase3_cpg.py -v
```

---

## Developer Experience

### Debugging

```bash
CODECORTEX_LOG_LEVEL=DEBUG codecortex index .
```

### Profiling

```bash
python -m cProfile -o profile.out tests/benchmarks/benchmark_runner.py --target . --quiet
python -c "import pstats; p=pstats.Stats('profile.out'); p.sort_stats('cumulative'); p.print_stats(20)"
```

### Troubleshooting

**No nodes in visualization / empty graph**
Ensure the target directory contains supported source files (`.py`, `.js`, `.ts`, `.php`). Check `CODECORTEX_LOG_LEVEL=DEBUG codecortex visualize .` for parser errors.

**8000+ files indexed / graph polluted with framework internals (Laravel, Node)**
You are likely indexing `vendor/` or `node_modules/`. Target the application directory directly or use `--exclude`:
```bash
codecortex index /path/to/laravel --languages php --exclude vendor/ node_modules/
```
Or add `exclude_paths` to `codecortex.yaml` (see Configuration above).

**Clustering skipped — scikit-learn / hdbscan not installed**
Install the optional clustering extras:
```bash
pip install scikit-learn hdbscan umap-learn
```
HDBSCAN also requires real (non-stub) embeddings. Set `embedding_backend: local`.

**Clustering produces 0 clusters**
HDBSCAN requires real (non-stub) embeddings. Set `embedding_backend: local` and install sentence-transformers.

**Slow CPG build (>10s for 60 files)**
Enable graph pruning and on-demand CFG/DFG:
```yaml
use_graph_pruning: true
on_demand_cfg: true
on_demand_dfg: true
```

**Browser does not open**
Use `--no-open` and navigate manually to `http://localhost:7979`.

**`pip: command not found` on macOS**
Use `pip3` or `python3 -m pip`. macOS Homebrew Python does not symlink `pip`.

**`error: externally-managed-environment` (PEP 668)**
Create a virtualenv first: `python3 -m venv .venv && source .venv/bin/activate`, then run `pip install`.

---

## Extensibility

### Add a Language Parser

```python
from core.parser_framework import LanguageParser
from core.types import ParseResult

class RustParser(LanguageParser):
    @property
    def language(self) -> str:
        return "rust"

    def parse(self, source: str, file_path: str) -> ParseResult:
        ...
```

### Add an Embedding Provider

```python
from embeddings.provider_factory import EmbeddingProvider
import numpy as np

class MyProvider(EmbeddingProvider):
    def embed(self, texts: list[str]) -> np.ndarray:
        ...
```

### Export Graph Programmatically

```python
from visualization import export_pipeline
import json

pipeline = CodeCortexPipeline.from_directory(".")
data = export_pipeline(pipeline, project_name="my-project")
json.dump(data, open("graph.json", "w"), indent=2)
```

---

## License

MIT

---

## Author

**Nafees Mansoor**
