## 1. Installation

> **Note on tree-sitter:** CodeCortex downloads `tree-sitter` and its language grammars (`tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript`) as **core dependencies** — not optional extras. They are the AST parser engine used to extract functions, classes, call edges, and control-flow from source files. Without them the CPG cannot be built.

### Option A — Install from local clone (recommended for development)

> **macOS note:** Homebrew Python installs `pip3`, not `pip`. Use `pip3` or
> `python3 -m pip` everywhere below. Homebrew Python 3.12+ also enforces
> PEP 668 — global `pip install` is blocked. Always install inside a
> virtualenv (shown below).

```bash
# Clone into your project directory (or any location)
git clone https://github.com/NafeesMansoor/CodeCortex.git codecortex
cd codecortex

# Create a virtualenv (required on macOS Homebrew Python)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install core dependencies into the virtual environment
pip install -r requirements.txt

# Install the package in editable mode (registers `codecortex` and `cx` CLI)
pip install -e .

# Install tree-sitter-language-pack (required for PHP/JS/TS parsing)
pip install tree-sitter-language-pack

# Verify
codecortex --help
```

### Option B — Install directly from GitHub

```bash
pip install git+https://github.com/NafeesMansoor/CodeCortex.git
```

### Option C — Install from PyPI (when published)

```bash
# Minimal (structural analysis only)
pip install codecortex

# With GPU-accelerated real embeddings
pip install "codecortex[embeddings]"
pip install torch --index-url https://download.pytorch.org/whl/cu121

# Full stack (embeddings + LSP + clustering)
pip install "codecortex[all]"
```

### Optional extras

```bash
# Real code-aware embeddings (sentence-transformers / CodeBERT)
pip install sentence-transformers torch

# Python semantic analysis via Jedi
pip install jedi pygls

# HDBSCAN clustering + UMAP
pip install hdbscan umap-learn scikit-learn

# PHP support (tree-sitter-language-pack ships the PHP grammar)
pip install tree-sitter-language-pack
```

---

## 2. Environment Variables

```bash
# Embedding backend: stub (default) | local | openai
export CODECORTEX_EMBEDDING_PROVIDER=stub

# Required only when embedding_backend=openai
export OPENAI_API_KEY=sk-...

# Where graph snapshots and caches are stored (default: .codecortex/)
export CODECORTEX_CACHE_DIR=.codecortex/

# Logging level: DEBUG | INFO | WARNING | ERROR
export CODECORTEX_LOG_LEVEL=WARNING
```

---

## 3. Indexing

```bash
# Fast structural index (Tier-1 only, default)
codecortex index .

# Full CPG with embeddings and semantic clustering
codecortex index . --mode cpg --enable-embeddings --enable-clustering

# Hybrid: CFG/DFG computed lazily per query (faster builds on large repos)
codecortex index . --on-demand-cfg --on-demand-dfg

# Save a snapshot for fast incremental reload
codecortex index . --save-snapshot .codecortex/graph.json

# Limit to specific languages (py / js / ts / php)
codecortex index . --languages py ts

# Exclude extra project paths on top of the built-in defaults
codecortex index . --languages php --exclude storage bootstrap

# Index everything, including virtualenvs and dependencies (rarely wanted)
codecortex index . --no-default-excludes --no-gitignore

# Full recommended build for medium projects
codecortex index . \
  --mode cpg \
  --enable-embeddings \
  --enable-clustering \
  --languages py \
  --save-snapshot .codecortex/graph.json

# Debug mode to diagnose parser errors
CODECORTEX_LOG_LEVEL=DEBUG codecortex index .
```

> **Note:** Virtualenvs (`venv/`, `.venv/`), `node_modules/`, `vendor/`,
> `__pycache__/`, build output and VCS metadata are skipped automatically, and
> the repository `.gitignore` is honoured. Use `--exclude` only for extra
> project paths, or add `exclude_paths` to `codecortex.yaml` so you never need
> to pass it on every run (see Section 6).

---

## 4. Querying

```bash
# Natural language semantic search
codecortex query --query "authentication middleware" --target .

# Impact analysis — blast radius of a function/class
codecortex query --impact UserService.authenticate --target .

# JSON output (machine-readable ranked results)
codecortex query --query "HTTP routing" --json --target .

# With centrality-weighted ranking and larger context budget
codecortex query \
  --query "database connection pooling" \
  --enable-centrality-ranking \
  --max-tokens 5000 \
  --target .

# Quick alias (cx = codecortex)
cx query --query "trade signal generation" --target .
cx query --impact RankerService.rank --target .
```

---

## 5. Visualization

```bash
# Launch interactive browser graph explorer (auto-opens http://localhost:7979)
codecortex visualize .

# Point at any repository
codecortex visualize /path/to/your/repo

# Custom port
codecortex visualize . --port 8080

# Headless — start server without opening browser
codecortex visualize . --no-open

# Limit to Python and TypeScript files
codecortex visualize . --languages py ts

# Full recommended launch
codecortex visualize . --port 7979 --languages py
```

---

## 6. Configuration File

Place `codecortex.yaml` in the project root:

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

  # Honour the repository .gitignore (default: true)
  respect_gitignore: true

  # Extra paths to skip, on top of the built-in directory exclusions
  # (virtualenvs, node_modules, vendor, caches, build output, VCS metadata).
  exclude_paths:
    - storage
    - bootstrap/cache

  # Optional: replace the built-in directory list entirely
  # exclude_dirs: [.venv, node_modules, vendor]
```

### Lightweight mode (fastest build, structural only)

```yaml
codecortex:
  enable_cpg: false
  enable_embeddings: false
  enable_clustering: false
  centrality_ranking: false
```

### Balanced mode (default — full CPG, stub embeddings)

```yaml
codecortex:
  enable_cpg: true
  embedding_backend: stub
  enable_clustering: true
```

### Deep semantic mode (best retrieval quality)

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

### Enterprise / monorepo mode (500k+ LOC)

```yaml
codecortex:
  on_demand_cfg: true
  on_demand_dfg: true
  use_graph_pruning: true
  embedding_backend: openai
  embedding_dimensions: 3072
```

---

## 7. Python API

```python
from codecortex import CodeCortexPipeline, PipelineConfig

# Build pipeline
config = PipelineConfig(
    embedding_backend="local",
    enable_clustering=True,
    use_louvain=True,
    use_beam_traversal=True,
    use_ppr=True,
    use_graph_pruning=True,
)
pipeline = CodeCortexPipeline.from_directory("/path/to/repo", config=config)

# Semantic query → AI-ready context string
context = pipeline.query("authentication flow", max_tokens=3000)

# Ranked retrieval results
results = pipeline.retrieve("database models")
for r in results:
    print(r.qualified_name, r.score)

# Impact analysis
impact = pipeline.impact("UserService.authenticate", max_depth=5)
print(impact.to_dict())
# keys: root, direct_callers, affected_files, affected_tests, transitive_affected

# Hotspot detection (structurally central nodes)
top = pipeline.top_nodes(n=20)

# Incremental update (reindex one changed file)
pipeline.reindex_file("/path/to/repo/auth/service.py")

# Export graph for visualization
from visualization import export_pipeline
import json
data = export_pipeline(pipeline, project_name="my-project")
json.dump(data, open("graph.json", "w"), indent=2)
```

---

## 8. Testing & Benchmarking

```bash
# Run all 224 unit tests
pytest tests/ --ignore=tests/benchmarks

# With coverage report
pytest tests/ --ignore=tests/benchmarks --cov=. --cov-report=term-missing

# Specific test phase
pytest tests/test_phase3_cpg.py -v

# Composite benchmark against any codebase
python tests/benchmarks/benchmark_runner.py --target /path/to/repo --phase "v1.0"

# Stage-wise evaluation
python tests/benchmarks/bench_v01.py --target /path/to/repo

# Per-feature architectural evaluation
python tests/benchmarks/bench_v02.py --target /path/to/repo

# Quiet mode (results appended to docs/results.md)
python tests/benchmarks/benchmark_runner.py --target . --quiet
```

---

## 9. Debugging & Profiling

```bash
# Verbose parser output
CODECORTEX_LOG_LEVEL=DEBUG codecortex index .

# Profile index build
python -m cProfile -o profile.out tests/benchmarks/benchmark_runner.py --target . --quiet
python -c "import pstats; p=pstats.Stats('profile.out'); p.sort_stats('cumulative'); p.print_stats(20)"
```

---

## 10. New Project Quickstart (copy-paste)

```bash
# 1. Clone CodeCortex into your project
git clone https://github.com/NafeesMansoor/CodeCortex.git codecortex

# 2. Activate your project venv and install
source venv/bin/activate
pip install -e codecortex/

# 3. Verify CLI
codecortex --help

# 4. Build index
codecortex index . --languages py --save-snapshot .codecortex/graph.json

# 5. Run a query
codecortex query --query "main entry point" --target .

# 6. Launch graph explorer
codecortex visualize . --port 7979
```

---

## 11. CLAUDE.md Snippet (for AI-assisted projects)

Paste this into your project's `CLAUDE.md` to instruct Claude Code to use CodeCortex:

```markdown
## CodeCortex — Semantic Code Intelligence

**IMPORTANT: This project uses CodeCortex. ALWAYS run `codecortex` (alias `cx`)
via Bash BEFORE using Grep/Glob/Read to explore the codebase.**

| Command | Use when |
|---------|----------|
| `cx index . --languages py` | Rebuild index after large changes |
| `cx query --query "TEXT" --target .` | Find code by natural language |
| `cx query --impact SYMBOL --target .` | Blast radius of a function/class |
| `cx query --json --query "TEXT" --target .` | Machine-readable ranked results |
| `cx visualize . --no-open` | Build graph headlessly |
| `cx visualize . --port 7979` | Launch interactive browser explorer |

Snapshot: `.codecortex/graph.json` — rebuild with:
`cx index . --languages py --save-snapshot .codecortex/graph.json`
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Empty graph / no nodes in visualizer | Check files exist for supported languages (`.py .js .ts .php`). Run with `CODECORTEX_LOG_LEVEL=DEBUG`. |
| Far more files indexed than the project has | A directory the defaults don't cover is being walked. Add it via `--exclude`, or check you did not pass `--no-default-excludes`. |
| "Clustering skipped" message | Expected when `scikit-learn`/`hdbscan` are not installed. Install with `pip install scikit-learn hdbscan umap-learn` to enable. |
| 0 clusters produced | HDBSCAN needs real embeddings. Set `embedding_backend: local` and `pip install sentence-transformers`. |
| Slow CPG build (>10s for 60 files) | Enable `use_graph_pruning: true`, `on_demand_cfg: true`, `on_demand_dfg: true` in `codecortex.yaml`. |
| Browser doesn't open | Use `--no-open` and navigate to `http://localhost:7979` manually. |
| `codecortex: command not found` | Install with `pip install -e .` from inside the cloned repo directory. |
| `pip: command not found` on macOS | Use `pip3` or `python3 -m pip`. Homebrew Python does not symlink `pip`. |
| `error: externally-managed-environment` | Create a virtualenv first: `python3 -m venv .venv && source .venv/bin/activate`. |
| PHP files produce no nodes | `tree-sitter-language-pack` is required for the PHP grammar: `pip install tree-sitter-language-pack`. |
