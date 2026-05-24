# /benchmark

Run the CodeCortex benchmark suite against the `code-review-graph` codebase and append results to `docs/results.md`.

## What to do

1. Run the benchmark using:
   ```
   /Users/nafees/Desktop/Claude/zombie_trades_venv/venv/bin/python3 tests/benchmarks/benchmark_runner.py
   ```
   Optionally pass `--phase <label>` to tag the result with a custom phase name, or `--quiet` to suppress verbose output.

2. After it completes, read `docs/results.md` and summarize the latest entry:
   - Composite score
   - Any regressions vs the previous entry (build time, latency, node count)
   - Any new highs

3. If the score dropped vs the previous run, identify which dimension degraded and suggest a likely cause.

## Notes

- The benchmark ingests all `.py` files under `code-review-graph/code_review_graph/`
- It uses `StubEmbeddingProvider` so semantic scores are structural proxies only
- Benchmark output appends to `docs/results.md` automatically; do not overwrite existing entries
