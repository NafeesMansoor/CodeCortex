# /ingest $ARGUMENTS

Ingest a codebase directory into the CodeCortex CPG and report coverage metrics.

## Usage

```
/ingest <path>
```

`<path>` — absolute or relative path to a directory containing source files.

## What to do

1. Write and run a short Python script (via Bash) using the CodeCortex stack:

```python
import sys, time

sys.path.insert(0, "/Users/nafees/Desktop/Claude/CodeCortex")

from core.parser_framework import ParserRegistry
from core.parsers import PythonParser, JavaScriptParser, TypeScriptParser, PHPParser
from graph.graph_store import GraphStore
from graph.cpg_builder import CPGBuilder
from indexing.semantic_index import SemanticIndex
import pathlib, tracemalloc

registry = ParserRegistry()
registry.register(PythonParser())
registry.register(JavaScriptParser())
registry.register(TypeScriptParser())
registry.register(PHPParser())

store = GraphStore(":memory:")
builder = CPGBuilder(store)

target = pathlib.Path("$ARGUMENTS").expanduser().resolve()
extensions = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".php"}
files = [f for f in target.rglob("*") if f.suffix in extensions]

tracemalloc.start()
t0 = time.perf_counter()
errors = 0
for f in files:
    parser = registry.get(f.suffix.lstrip("."))
    if not parser:
        continue
    try:
        result = parser.parse(f.read_text(errors="replace"), str(f))
        builder.ingest(result)
    except Exception:
        errors += 1
elapsed = time.perf_counter() - t0
_, peak = tracemalloc.get_traced_memory()

idx = SemanticIndex(store)
print(f"Files: {len(files)}")
print(f"Nodes: {store.node_count()}")
print(f"Edges: {store.edge_count()}")
print(f"Parse errors: {errors}")
print(f"Build time: {elapsed:.3f}s")
print(f"Peak memory: {peak / 1e6:.2f} MB")
print(f"Throughput: {len(files) / elapsed:.1f} files/s")
```

2. Report the output as a summary table.
3. Note any parse errors and which file extensions caused them.

## Notes

- Uses in-memory SQLite so the graph is not persisted between runs.
- Target stack: `.py`, `.js/.jsx/.mjs/.cjs`, `.ts/.tsx`, `.php`
- For large codebases (>500 files) add `--quiet` and expect longer run times.
