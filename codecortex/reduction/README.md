# TARP: Task-Aware Reduction and Preservation

TARP selects, for one query, a subgraph `G_q` of the repository graph whose cost
`C(G_q) = |V_q| + mu * |E_q|` stays within a budget `B`. It is opt-in: with it off,
indexing and retrieval behave exactly as before.

## Using it

```bash
codecortex query --query "validate auth token" --tarp-budget 0.05   # keep ~5% of the graph
```

```yaml
# codecortex.yaml
codecortex:
  use_tarp: true
  tarp_budget: 0.05      # < 1: fraction of graph nodes; >= 1: absolute cost units
```

```python
result = pipeline.reduce("validate auth token", budget=0.05)        # TARP, variant F
result = pipeline.reduce(q, budget=200, method="generic_budget")    # a baseline, same budget
result.selected, result.original_nodes, result.to_dict()
```

## How it works

1. **Graph view** (`reduction/view.py`). A read-only view over the stored graph.
   Bare-name call and import endpoints are resolved to a definition only when the
   match is unambiguous (a unique definition, or a unique one in the edge's file);
   resolution counts are reported. Optional Level-1 condensation folds variable,
   parameter and call-site nodes into their parent definition, keeping the members
   so every original node maps back to source.
2. **Scoring.** `I(v|q) = alpha S + beta D + gamma P + delta E`:
   - **S:** the pipeline's semantic search score.
   - **D:** decayed dependency proximity to the top seeds.
   - **P:** participation in shortest paths between seed pairs.
   - **E:** a node-kind prior blended with centrality.

   Each term is min-max normalised. Defaults are 0.40 / 0.25 / 0.20 / 0.15, fixed a priori.
3. **Selection.** A lazy greedy on marginal value per marginal cost, over single nodes
   and whole seed-to-seed paths (path units carry a completion bonus). It is a
   heuristic: no approximation guarantee is claimed.
4. **Recovery** (optional). If the selection misses top seeds or breaks a seed path,
   it expands around the selection by at most `recovery_budget * B` extra cost, for at
   most `recovery_rounds` rounds.

`TARPConfig.variant("A".."G")` switches query conditioning, path awareness, budget
optimisation and recovery on and off, which gives the ablation variants. Baselines
(`codecortex.reduction.BASELINES`): `degree`, `static_importance`, `fixed_hop`,
`generic_budget`, `dependency_aware`, `path_preserving`. They use the same view,
cost model and budget.

## Measured behaviour (pilot)

These figures come from 4 repositories and 40 held-out queries with provisional
labels. They are not a general claim.

- Query-conditioned selection kept far more labelled evidence than query-independent
  pruning at every budget from 1% to 20%.
- Against generic budgeted and path-preserving selection, TARP kept more evidence at
  most budgets, with retrieval quality statistically tied.
- Path units, recovery and condensation showed no measurable gain on this sample.
- Reduction takes about 11 ms per query on graphs of 1.6k–4.5k nodes.
