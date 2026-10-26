# Changelog

All notable changes to CodeCortex are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and CodeCortex uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-10-27

### Breaking

- **Every module moved under the `codecortex` package.** Installing CodeCortex
  used to add twelve top-level packages to an environment — `core`, `graph`,
  `pipeline`, `retrieval`, `visualization` and more — which could clash with
  an application's own modules. Now it adds only `codecortex`. Imports from
  `codecortex` itself (`from codecortex import CodeCortexPipeline`) are
  unchanged; direct module imports need the prefix
  (`from codecortex.pipeline.context_builder import …`). The CLI, configuration
  and index caches are unaffected. See *Upgrading to 2.0.0* in UPDATING.md.

### Added

- **Build once, query on demand.** `codecortex index` now saves the graph and
  its centrality and cluster results to `<project>/.codecortex/` (which
  ignores itself in git). `codecortex query` and the new `codecortex explain`
  load it and re-parse only files whose size or mtime changed, instead of
  rebuilding the whole index on every call — on a 636-file repository, ~80 s
  per question became ~1 s. Search vectors are saved too (float16), so the
  model backends no longer re-embed the whole project per query.
  `--rebuild` forces a fresh build; `index --no-save` skips saving.
- `codecortex explain SYMBOL`: one symbol's definition, callers, callees and
  base types, read from the saved graph.
- `codecortex install` / `uninstall`: write (or remove) instructions telling
  the project's AI assistant to query CodeCortex before grepping or reading
  files — a marked block in `CLAUDE.md` plus a `.claude/skills/codecortex`
  skill by default; `--platform agents|cursor|gemini|all` for others.
  `uninstall --purge` also deletes `.codecortex/`.
- `codecortex hook-guard`, registered by `install` as a Claude Code
  `PreToolUse` hook on Grep/Glob/Read/Bash: when a saved index exists, it adds
  a one-line note pointing searches and source reads at `codecortex query` /
  `explain`. It never blocks a tool, notes at most once per session per kind,
  and exits silently on any error. `install --no-hooks` skips it; existing
  settings are merged, and invalid settings files are left untouched.
- `lexical` embedding backend, now the default: hashed keyword vectors over
  symbol names, kinds and modules, with no model or download. The previous
  default (`stub`) produced all-zero vectors, so queries on a default install
  had no semantic signal at all.
- `codecortex health` checks that every language grammar loads.

- **Twelve more languages: Go, Rust, Java, C#, C, C++, Ruby, Kotlin, Swift,
  Scala, Lua and Bash.** Each extracts definitions, calls, imports and base
  types into the same graph as the original four. Their grammars ship as
  wheels, so parsing stays offline. See *Supported Languages* in the README.
- `TreeSitterParser` (`core/parsers/treesitter_parser.py`): a table-driven
  parser — a language is a list of grammar node types plus optional hooks.
- `core/parsers/registry.py` is now the single list of parsers. The pipeline,
  `reindex_file()`, on-demand expansion, `ParallelIndexer` and the indexing
  providers all resolve parsers there instead of keeping their own tables.
- `--languages` accepts a language name, alias or extension
  (`py`, `rust`, `cs`, `tsx`).

### Changed

- `faiss-cpu` moved from the core dependencies to the `embeddings` extra
  (retrieval already fell back to exact search without it; exact search now
  uses numpy), and `tree-sitter-language-pack` is no longer a dependency.
  A fresh install is ~100 MB.
- The README recommends installing CodeCortex as an isolated tool
  (`pipx` / `uv tool`) rather than into a project's environment, where its
  top-level packages (`core`, `graph`, `pipeline`, …) can clash with the
  project's own.

### Fixed

- **Indexing a ~600-file repository took over five minutes without scipy.**
  The pure-Python PageRank fallback looped over every node for each node
  without out-links on every iteration (O(V²) per step). With scipy, exact
  closeness centrality and a 10 % betweenness sample cost ~35 s; large graphs
  now use sampled closeness and at most 256 betweenness sources. The same
  index now builds in ~21 s.
- `codecortex query --impact` crashed in text mode (`KeyError:
  'transitive_affected'`) after printing its first lines.
- **JavaScript/TypeScript extraction missed most of a modern codebase.**
  Arrow functions and function expressions bound to a name
  (`const handler = async () => {}`), class methods, method calls
  (`db.save()`, `this.auth.check()`), `new X()`, `require()`, dynamic
  `import()` and `export ... from` were all dropped, and calls made inside a
  method or arrow function were lost with them. TypeScript also dropped every
  class, because TS names classes with `type_identifier`, and never emitted
  `implements` edges; JavaScript never emitted `extends` edges.
- `.mjs`/`.cjs` (JavaScript) and `.mts`/`.cts` (TypeScript) files were never
  indexed.
- **PHP extraction missed imports and member calls.** `use` statements
  (including grouped `use A\{B, C}`) and `require`/`include` produced no
  edges; `$this->m()` was recorded as a call to `$this`; static calls
  (`User::find()`) and `new X()` were dropped; `implements` and trait use
  produced no edges; methods were labelled as plain functions; and test
  methods were emitted twice.

## [1.2.1] - 2026-08-30

### Fixed

- **`.tsx`/`.jsx` files were silently invisible to the indexer.** The file
  walker built its extension set from the language-handle keys (`"js"`,
  `"ts"`) rather than each parser's declared `extensions` list, so it only
  ever globbed `*.js`/`*.ts` and never looked for `*.jsx`/`*.tsx` — with no
  error or warning, `codecortex index` reported a clean run while the entire
  React/JSX component tree was absent from the graph.
- **`.tsx` files that *were* discovered parsed as if they had no JSX.**
  `TypeScriptParser` always requested the plain `"typescript"` tree-sitter
  grammar, which has no JSX productions; a `.tsx` file now selects the `"tsx"`
  grammar variant based on its extension.
- `reindex_file()` and the on-demand CFG/DFG expansion path
  (`on_demand_cfg`/`on_demand_dfg`) hardcoded `PythonParser` regardless of the
  changed file's actual language, so re-indexing or expanding a non-Python
  file after the initial build silently parsed it with the wrong grammar.
  Both now dispatch by file extension, same as the initial build.
- `ParallelIndexer.parse_directory()` had the same one-extension-per-language
  bug independently: a `language="typescript"`/`"javascript"` run derived its
  file-walk extension from a `{"typescript": "ts", ...}` map, so `.tsx`/`.jsx`
  files were never discovered even though `.parse_files()` and the parser
  dispatch handled them correctly. It now walks by the resolved parser's own
  `extensions` list.

## [1.2.0] - 2026-08-13

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
- Update checks failed permanently on interpreters without a system CA store —
  a python.org build on macOS most notably — with
  `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`. Checks
  now verify against certifi's bundle, and a certificate failure explains how to
  fix it. Verification is never disabled.
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

[1.2.1]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.2.1
[1.2.0]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.2.0
[1.1.0]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.1.0
[1.0.1]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.0.1
[1.0.0]: https://github.com/NafeesMansoor/CodeCortex/releases/tag/v1.0.0
