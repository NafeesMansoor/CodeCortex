# Changelog

All notable changes to CodeCortex are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and CodeCortex uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Single authoritative version source: `codecortex/version.py`. `pyproject.toml`
  reads it statically, so packaging metadata and running code cannot drift apart.
- Semantic version parsing and comparison (`codecortex.version.SemVer`) with full
  SemVer 2.0.0 precedence, including prerelease ordering. No new dependency.
- Release channels — `stable`, `beta`, `dev`. A stable installation never
  upgrades onto a prerelease.
- Update detection (`codecortex.updater`) against GitHub Releases, falling back
  to version tags when a repository publishes tags without releases. Results are
  cached for a day; failures degrade to "unknown" and never raise.
- `codecortex version [--check] [--json]`, `codecortex update [--check|--yes|--rollback]`
  and `codecortex health [--json]` commands, plus `python -m codecortex`.
- Installation-model detection and per-model update strategies for git checkouts
  and pip installs (`codecortex.installation`). Unrecognised installations refuse
  to self-update and print manual instructions instead.
- Safe update process (`codecortex.upgrade`): backup, install, migrate, verify in
  a fresh interpreter, and roll back automatically when verification fails.
- Post-update health check (`codecortex.health`) covering core imports, version
  metadata, configuration, SQLite engine and index cache schemas.
- Schema versioning for all four SQLite stores via `PRAGMA user_version`
  (`core.db_schema`). Stale caches rebuild; caches written by a newer CodeCortex
  are refused rather than misread.
- `codecortex.migrations` — inspect index caches, report what a version change
  would rebuild, and execute it.
- `require_version()` for consuming applications, so an incompatible CodeCortex
  fails at startup with a clear message instead of an obscure error later.
- `update.json` release metadata, generated from the source tree by
  `scripts/release_metadata.py` and attached to each GitHub Release.
- Update documentation: [UPDATING.md](UPDATING.md).

### Changed

- CI verifies that packaging metadata, `codecortex.version`, and the CLI all
  report the same version, and builds the distribution on every run.
- The release workflow refuses to publish when the git tag does not match the
  package version, and attaches `update.json` to the release.
- `codecortex.yaml` loading now reports unrecognised settings on stderr instead
  of discarding them silently — a renamed key after an upgrade is visible.

- Dependency bounds on the tree-sitter family and ruff. Both had floated freely,
  so a fresh install picked up incompatible releases and CI failed on commits
  that changed nothing related.

### Fixed

- **Parsers were broken on any fresh install.** `tree-sitter-language-pack` 1.14
  swapped its bundled parser binding for the upstream `tree_sitter` one: node
  accessors became properties instead of methods and `Parser.parse()` takes
  bytes rather than `str`. All four parsers and the CFG/DFG/endpoint builders
  are migrated to that API. Because nothing pinned the dependency, every install
  resolving to 1.14+ failed to parse a single file.
- Segfault while indexing: the buffer handed to `Parser.parse()` is borrowed,
  not copied, so a temporary left the tree reading freed memory. `SourceText`
  now owns its UTF-8 buffer for its whole lifetime.
- Pinned `tree-sitter<0.26`: 0.26.0 segfaults when many `Node.start_point` /
  `Node.end_point` objects are created and later collected, which indexing any
  non-trivial tree does. Reproducible with the binding alone.
- Version drift: `pyproject.toml` (1.1.0), `codecortex/__init__.py` (1.1.0) and
  the CLI banner each carried their own literal, and the installed distribution
  metadata had fallen behind at 1.0.0.
- CI: `ruff format --check` failed on six Markdown files after ruff 0.16 began
  formatting Python blocks inside Markdown; bandit's `--exclude` paths did not
  match what it walks under `-r .`, so the test tree was scanned and a test
  fixture writing `"/tmp/"` failed the security job.

### Migration Notes

- Existing index caches predate schema stamping and carry `user_version = 0`.
  The first open under this version rebuilds them; re-index to repopulate. No
  source data or configuration is affected.
- No configuration changes are required.

## [1.1.0] - 2026-08-12

### Fixed

- Indexer no longer walks dependency trees; `core.file_walker` prunes excluded
  directories before descending.
- Byte-offset corruption in parsers — tree-sitter offsets are byte offsets, and
  slicing is now routed through `core.source_text`.

## [1.0.1] - 2026-05-25

### Added

- `--exclude` flag on the indexer.

### Fixed

- `tree-sitter-language-pack` API compatibility.
- PHP parser fixes and CI workflow repairs.

## [1.0.0] - 2026-05-25

### Added

- Initial release: parsing, code property graph, semantic indexing, embeddings,
  clustering, traversal, ranking, retrieval and the end-to-end pipeline.

[Unreleased]: https://github.com/NafeesMansoor/CodeCortex/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.1.0
[1.0.1]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.0.1
[1.0.0]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.0.0
