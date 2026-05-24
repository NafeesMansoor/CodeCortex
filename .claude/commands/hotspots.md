# /hotspots

Show the top centrality hotspots in the code-review-graph CPG — the nodes with highest bridge × fan-in composite scores.

## What to do

1. Build the CPG + centrality scores in memory:

```python
import sys
sys.path.insert(0, '/Users/nafees/Desktop/Claude/CodeCortex')

from core.parsers import PythonParser
from core.parser_framework import ParserRegistry
from graph.graph_store import GraphStore
from graph.cpg_builder import CPGBuilder
from ranking.centrality_engine import CentralityEngine
import pathlib

registry = ParserRegistry()
registry.register(PythonParser())
store = GraphStore(':memory:')
builder = CPGBuilder(store)
target = pathlib.Path('code-review-graph/code_review_graph')
for f in target.rglob('*.py'):
    result = registry.get('py').parse(f.read_text(errors='replace'), str(f))
    builder.ingest(result)

engine = CentralityEngine(store)
scores = engine.compute()
hotspots = CentralityEngine.hotspots(scores, n=20)
for rank, (name, s) in enumerate(hotspots, 1):
    print(f"{rank:2}. {name:40s}  composite={s.composite_score:.5f}  fan_in={s.fan_in}  bridge={s.bridge_score:.4f}")
```

2. Present results as a ranked table: rank, symbol name, composite score, fan-in, bridge score.
3. For each of the top 5, show a one-line summary of what the node does based on its kind and file path.

## Notes

- Hotspots are ranked by `bridge_score × (fan_in + 1)` — nodes that are both structural bridges and heavily called.
- High-fan-in + high-bridge nodes are change-blast-radius candidates: modifying them ripples widely.
- Run from the project root: `/Users/nafees/Desktop/Claude/CodeCortex/`
