# Contributing to CodeCortex

Thank you for your interest in contributing to CodeCortex.

---

## Development Setup

```bash
git clone https://github.com/NafeesMansoor/CodeCortex.git
cd CodeCortex

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements-dev.txt
pip install -e .
```

Verify everything works:

```bash
pytest tests/ --ignore=tests/benchmarks -q
```

---

## Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable, production-ready. Protected — no direct pushes. |
| `develop` | Integration branch for in-progress features. |
| `feat/<name>` | Feature branches — branch from `develop`. |
| `fix/<name>` | Bug fix branches. |
| `docs/<name>` | Documentation-only changes. |

---

## Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Rust language parser
fix: correct adjacency cache edge deduplication
perf: reduce CPG build time by 40% via parallel parsing
refactor: simplify EmbeddingPipeline.index_all signature
docs: expand visualization usage examples in README
test: add phase 11 incremental indexing tests
chore: bump faiss-cpu to 1.8.0
```

---

## Code Style

```bash
# Lint
ruff check .

# Format
ruff format .
```

All code must pass `ruff check .` before submitting a PR.

---

## Running Tests

```bash
# All unit tests
pytest tests/ --ignore=tests/benchmarks -q

# Single phase
pytest tests/test_phase3_cpg.py -v

# With coverage
pytest tests/ --ignore=tests/benchmarks --cov=. --cov-report=term-missing
```

Do not run benchmarks in CI — they require a target codebase and take several minutes.

---

## Pull Request Process

1. Fork the repository and create a branch from `develop`.
2. Make your changes with tests.
3. Ensure `ruff check .` and all tests pass.
4. Open a PR against `main` (hotfixes) or `develop` (features).
5. Fill in the PR template.
6. At least one review approval is required before merge.

---

## Adding a Language Parser

1. Create `core/parsers/<language>_parser.py` implementing `LanguageParser`.
2. Register it in `pipeline/context_builder.py` `_build_cpg()`.
3. Add tests in `tests/test_phase1_core.py` or a new phase file.
4. Document the language in README.

---

## Adding a Visualization Feature

The visualization layer (`visualization/`) is a **read-only consumer** of pipeline outputs. Do not modify pipeline internals from within `visualization/`. Export new data via `graph_exporter.py`, consume it in `static/index.html`.

---

## Questions

Open a [GitHub Discussion](https://github.com/NafeesMansoor/CodeCortex/discussions) or file an issue using the appropriate template.
