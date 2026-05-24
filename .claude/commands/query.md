# /query $ARGUMENTS

Run a hybrid GraphRAG retrieval query against the code-review-graph CPG and return the top results.

## Usage

```
/query <natural language question or symbol name>
```

## What to do

1. Build the full index in memory (parse `code-review-graph/code_review_graph/`, build CPG, SemanticIndex, EmbeddingStore with StubEmbeddingProvider, CentralityEngine):

```python
import sys
sys.path.insert(0, '/Users/nafees/Desktop/Claude/CodeCortex')

from core.parsers import PythonParser
from core.parser_framework import ParserRegistry
from graph.graph_store import GraphStore
from graph.cpg_builder import CPGBuilder
from indexing.semantic_index import SemanticIndex
from embeddings.provider_factory import create_embedding_provider, EmbeddingConfig
from embeddings.embedding_store import EmbeddingStore
from ranking.centrality_engine import CentralityEngine
from retrieval.retrieval_layer import RetrievalLayer
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
provider = create_embedding_provider(EmbeddingConfig(backend='stub'))
emb_store = EmbeddingStore(provider, dimension=384)
for node in store.all_nodes():
    emb_store.add(node.qualified_name, node.name + ' ' + node.file_path)

centrality = CentralityEngine(store).compute()
layer = RetrievalLayer(idx, emb_store, centrality)
results = layer.retrieve('$ARGUMENTS', top_k=10)
for r in results:
    print(f"{r.score:.4f}  {r.node.qualified_name}  [{r.node.kind.value}]  {r.node.file_path}")
```

2. Present results as a ranked table with columns: rank, score, symbol, kind, file.
3. For the top 3 results, show their callers and callees from the SemanticIndex.

## Notes

- Scores are structural proxies (stub embeddings) — not true semantic similarity.
- Real semantic search requires `backend='local'` or `backend='openai'` in `EmbeddingConfig`.
- Run from the project root: `/Users/nafees/Desktop/Claude/CodeCortex/`
