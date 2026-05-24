# /impact $ARGUMENTS

Show the blast radius of a symbol change — which nodes are reachable from it via the CPG.

## Usage

```
/impact <symbol_name>
```

`<symbol_name>` — the short or qualified name of a function, class, or method.

## What to do

1. Build CPG and run bounded BFS traversal from the named symbol:

```python
import sys
sys.path.insert(0, '/Users/nafees/Desktop/Claude/CodeCortex')

from core.parsers import PythonParser
from core.parser_framework import ParserRegistry
from graph.graph_store import GraphStore
from graph.cpg_builder import CPGBuilder
from indexing.semantic_index import SemanticIndex
from traversal.traversal_engine import TraversalEngine
from core.types import TraversalConfig
import pathlib

registry = ParserRegistry()
registry.register(PythonParser())
store = GraphStore(':memory:')
builder = CPGBuilder(store)
target = pathlib.Path('code-review-graph/code_review_graph')
for f in target.rglob('*.py'):
    result = registry.get('py').parse(f.read_text(errors='replace'), str(f))
    builder.ingest(result)

idx = SemanticIndex(store)
engine = TraversalEngine(store)

# Resolve the symbol
matches = idx.find_by_name('$ARGUMENTS')
if not matches:
    print(f"Symbol not found: $ARGUMENTS")
    sys.exit(1)

seed = matches[0].qualified_name
config = TraversalConfig(max_depth=4, max_nodes=200)
nodes = list(engine.bfs(seed, config))
print(f"Seed: {seed}")
print(f"Impact radius ({len(nodes)} nodes, depth<=4):")
for n in sorted(nodes, key=lambda x: x.file_path):
    print(f"  {n.qualified_name:45s}  {n.file_path}")
```

2. Report: seed node, total nodes in impact radius, breakdown by file.
3. Group the impacted nodes by file and show counts per file as a summary table.
4. Flag any impacted nodes that are also top-10 hotspots (high centrality) — those are secondary blast-radius amplifiers.

## Notes

- Depth is capped at 4 to keep output manageable; pass a different depth by editing the script.
- Traversal follows outgoing CALLS and IMPORTS_FROM edges by default.
- Run from the project root: `/Users/nafees/Desktop/Claude/CodeCortex/`
