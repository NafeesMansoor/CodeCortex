# Updating CodeCortex

How CodeCortex versions itself, how an existing installation moves to a new
release, and what to do when that goes wrong.

- [Versioning](#versioning)
- [Checking your version](#checking-your-version)
- [Updating](#updating)
- [Release channels](#release-channels)
- [What an update does](#what-an-update-does)
- [Index cache migrations](#index-cache-migrations)
- [Configuration](#configuration)
- [Rollback](#rollback)
- [Health checks](#health-checks)
- [Offline and air-gapped installations](#offline-and-air-gapped-installations)
- [Consuming applications](#consuming-applications)
- [Cutting a release](#cutting-a-release)
- [Supported upgrade paths](#supported-upgrade-paths)
- [Troubleshooting](#troubleshooting)

## Versioning

CodeCortex follows Semantic Versioning: `MAJOR.MINOR.PATCH`.

| Part  | Meaning                                    |
|-------|--------------------------------------------|
| PATCH | Backward-compatible bug fixes              |
| MINOR | Backward-compatible features               |
| MAJOR | Breaking changes to the public API or CLI  |

The version lives in exactly one place — `codecortex/version.py`:

```python
__version__ = "2.0.0"
```

`pyproject.toml` reads that attribute statically (`[tool.setuptools.dynamic]`),
so packaging metadata always agrees with the running code. CI fails if they
diverge. Never add a second version literal anywhere.

Read it programmatically:

```python
from codecortex import __version__

print(__version__)
```

## Checking your version

```bash
codecortex --version              # CodeCortex 2.0.0
codecortex version                # version, installation model, last update check
codecortex version --check        # ask the release service for a newer version
codecortex version --json         # machine-readable installation metadata
python -m codecortex --version    # when the console script is not on PATH
```

`codecortex version --json` reports version, installation type, install root,
channel, last check time, latest known version, and whether an update is
available. Nothing is transmitted — the values come from local state.

## Updating

```bash
codecortex update             # check, show the change, ask, then apply
codecortex update --check     # report what would happen; change nothing
codecortex update --yes       # non-interactive (required when there is no TTY)
codecortex update --rollback  # restore the most recent backup
```

A run looks like this:

```text
CodeCortex 1.4.1
  Installation : git checkout at /srv/codecortex (v1.4.1)

Update available:
1.4.1 → 1.4.2

Preparing update...
  ✓ Preflight checks — git checkout at /srv/codecortex (v1.4.1)
  ✓ Backup created — ~/.codecortex/backups/1.4.1-20260812T170200Z
  ✓ Release installed — v1.4.2
  ✓ Database migration — 4 cache(s) already current
  ✓ Health check — verified 1.4.2

CodeCortex successfully updated to 1.4.2
```

CodeCortex detects how it was installed and updates accordingly:

| Installation                          | Strategy                                                        |
|---------------------------------------|-----------------------------------------------------------------|
| git checkout (`git clone` + `pip install -e .`) | Fetch tags, check out the release tag, reinstall the project     |
| pip package                            | `pip install --upgrade` from the release wheel, sdist, or tag    |
| Anything else (vendored, system-managed) | Refuses to act; prints the manual command                       |

Two safety rules apply to git checkouts: the working tree must be clean, and the
update moves to an immutable tag — never to a moving branch. `main` is not a
production version identifier.

Unrelated dependencies are never upgraded as a side effect; only CodeCortex and
its own declared requirements move.

## Release channels

```text
stable   production default — never installs a prerelease
beta     accepts rc/alpha/beta builds
dev      accepts everything, including dev builds
```

```bash
codecortex update --channel beta
```

A stable installation cannot accidentally land on a development build: the
channel filter is applied to the parsed version, not to a naming convention.

## What an update does

```text
current state → backup → install release → migrate caches → health check
                                                              │
                                                    healthy? ─┴─ no → rollback
                                                              └─ yes → done
```

The health check runs in a **fresh interpreter** (`python -m codecortex health
--json`). Verifying inside the process that performed the update would test the
code being replaced, not the code just installed.

If any step after installation fails, the previous version is restored
automatically. An installation is never knowingly left half updated.

## Index cache migrations

CodeCortex persists four SQLite stores — graph, symbol cache, reference graph
and cluster store. All four are **derived caches**: re-indexing reproduces them
from source. So CodeCortex does not carry a migration framework; it stamps each
database with `PRAGMA user_version` and reacts to a mismatch:

| Stamp             | Behaviour                                                  |
|-------------------|------------------------------------------------------------|
| `0`, empty file   | Create the schema and stamp it                              |
| equal to expected | Open normally                                               |
| older             | Drop and recreate the tables, then stamp (data re-indexes)  |
| newer             | Refuse to open — an older build must not misread new data   |

Caches created before schema stamping existed are unstamped, so the first open
under this version rebuilds them. Re-index to repopulate:

```bash
codecortex index . --save-snapshot .codecortex/graph.json
```

To migrate an index directory explicitly as part of an update:

```bash
codecortex update --index-dir .codecortex
```

A release that changes a cache schema publishes the new schema versions in its
`update.json`, and `codecortex version --check` says so before you update.

## Configuration

Updates never touch your configuration:

- `codecortex.yaml` is copied into the backup before an update and restored if a
  rollback happens.
- `.env` and other secret files are gitignored, so no update path writes to them.
  They are deliberately **not** copied into backups — a backup directory is not a
  place for credentials.
- Unrecognised settings in `codecortex.yaml` are reported on stderr rather than
  dropped silently, so a key renamed by an upgrade is visible instead of quietly
  falling back to a default.

## Rollback

Every update writes a backup to `~/.codecortex/backups/<version>-<timestamp>/`
containing a manifest (installation model, version, git commit) and a copy of
`codecortex.yaml`.

```bash
codecortex update --rollback
```

For a git checkout this returns HEAD to the recorded commit and reinstalls; for
a pip install it reinstalls the previous version from its tag. Configuration is
restored, and a health check confirms the result.

Database rollback is deliberately **not** automatic. Caches are regenerated by
re-indexing, so there is nothing to restore; and a genuinely irreversible schema
change is reported rather than silently reversed.

Set `CODECORTEX_STATE_DIR` to move update state and backups elsewhere.

## Health checks

```bash
codecortex health          # human-readable
codecortex health --json   # machine-readable, exit code 1 when unhealthy
codecortex health --index-dir .codecortex --config codecortex.yaml
```

```text
CodeCortex Health Check (2.0.0)

  [OK  ] Core modules — all import cleanly
  [OK  ] Version metadata — code 2.0.0 == metadata 2.0.0
  [OK  ] Database engine — sqlite 3.53.3
  [OK  ] Configuration — codecortex.yaml parsed
  [OK  ] Index caches — 4 database(s) at the current schema
  [WARN] Optional: clustering (hdbscan) — not installed

Status: HEALTHY
```

Missing optional extras are warnings, never failures.

## Offline and air-gapped installations

An update check never blocks CodeCortex. If GitHub is unreachable:

```text
Unable to check for updates: could not reach the release service: <reason>
CodeCortex will continue normally.
```

Disable outbound checks entirely:

```bash
export CODECORTEX_NO_UPDATE_CHECK=1
```

Update an air-gapped installation manually from a release artifact:

```bash
pip install --no-index ./codecortex-1.4.2-py3-none-any.whl
codecortex health
```

Or, for a git checkout with a mirrored remote:

```bash
git fetch --tags origin && git checkout --detach refs/tags/v1.4.2
pip install -e . && codecortex health
```

Update checking reads a public release list over HTTPS, sends no credentials and
no information about your installation, and collects no telemetry.

## Consuming applications

Applications that embed CodeCortex should declare the range they support:

```python
from codecortex import require_version

require_version(minimum="1.1.0", below="2.0.0", consumer="review-bot")
```

An incompatible installation fails immediately:

```text
review-bot requires CodeCortex >= 1.1.0 and < 2.0.0. Installed version: 2.1.0
```

The public API is what `codecortex/__init__.py` exports. Anything else —
including the `_`-prefixed helpers in these modules — is internal and may change
in a MINOR release.

## Cutting a release

```bash
# 1. Update the version — the only place it lives
$EDITOR codecortex/version.py          # __version__ = "2.0.0"

# 2. Move the Unreleased notes into a dated section
$EDITOR CHANGELOG.md

# 3. Verify locally
python -m pytest tests/ --ignore=tests/benchmarks -q
ruff check . && ruff format --check .
python scripts/release_metadata.py --check-only

# 4. Commit, tag, push
git commit -am "release: v2.0.0"
git tag v2.0.0
git push origin main --tags
```

Pushing the tag runs the release workflow, which reruns the tests, refuses to
publish if the tag does not match `codecortex/version.py`, builds the wheel and
sdist, generates `update.json`, and publishes the GitHub Release with generated
notes. Existing installations pick it up on their next check.

Raise `MINIMUM_SUPPORTED_VERSION` in `codecortex/version.py` only when a release
genuinely cannot be upgraded to directly; installations below it are told the
update is required.

## Supported upgrade paths

```text
1.0.x → latest
1.1.x → latest
1.2.x → latest
```

Every release upgrades directly to the newest release; there are no staged
upgrades today. If that changes, `minimum_version` in the release's
`update.json` will say so, and `codecortex update` will report the requirement
instead of attempting the jump.

### The one exception: installations older than 1.2.0

`codecortex update` was introduced in 1.2.0, so 1.0.x and 1.1.x have no such
command — there is nothing on those versions to run. They need one manual
upgrade to pick it up; after that the normal flow applies.

```bash
# pip installation
# Installed 2.0.0 or earlier with pip? The package was renamed in 2.0.1;
# remove the old name first so both don't claim the same files.
pip uninstall -y codecortex
pip install codecortex-engine

# git checkout (commit or stash local changes first)
cd /path/to/CodeCortex
git fetch --tags origin && git checkout v2.0.1 && pip install -e .
```

Then verify and re-index:

```bash
codecortex --version    # CodeCortex 2.0.0
codecortex health
codecortex index .      # unstamped caches from older versions rebuild here
```

Configuration is untouched by either route. Caches written before 1.2.0 carry
no schema stamp, so the first open rebuilds them — see
[Index cache migrations](#index-cache-migrations).

### Upgrading to 2.0.1: the package is now `codecortex-engine`

CodeCortex is published on PyPI as `codecortex-engine`, because the name
`codecortex` belongs to an unrelated project there — `pip install codecortex`
does not install CodeCortex. Only the distribution name changed: the import
package, the `codecortex` command, configuration and index caches are the same.

A pip installation of 2.0.0 or earlier is registered under the old name, and
`codecortex update` from those versions would install the new name alongside
it, leaving two packages that own the same files. Replace it by hand once:

```bash
pip uninstall -y codecortex
pip install codecortex-engine
# tool installs: pipx uninstall codecortex && pipx install codecortex-engine
```

A git checkout needs nothing beyond the usual `git checkout v2.0.1 && pip
install -e .`; pip replaces the editable install's metadata.

### Upgrading to 2.0.0: code that imports CodeCortex modules

2.0.0 moves every module under the `codecortex` package. Before, installing
CodeCortex added twelve top-level packages to an environment — `core`,
`graph`, `pipeline`, `retrieval` and so on — which could clash with an
application's own modules of the same name. Now it adds only `codecortex`.

The CLI, `codecortex.yaml` and index caches are unaffected. Code that imports
the public API from `codecortex` itself is unaffected:

```python
from codecortex import CodeCortexPipeline, PipelineConfig  # 1.x and 2.x
```

Code that imported a module directly needs the `codecortex.` prefix:

| 1.x | 2.0 |
|---|---|
| `from pipeline.context_builder import CodeCortexPipeline` | `from codecortex.pipeline.context_builder import CodeCortexPipeline` |
| `from core.parser_framework import LanguageParser` | `from codecortex.core.parser_framework import LanguageParser` |
| `from graph.graph_store import GraphStore` | `from codecortex.graph.graph_store import GraphStore` |
| `from visualization import export_pipeline` | `from codecortex.visualization import export_pipeline` |

The same holds for `indexing`, `embeddings`, `clustering`, `traversal`,
`ranking`, `retrieval` and `performance`. A one-line rewrite covers most code:

```bash
sed -E -i.bak 's/^(\s*)(from|import) (core|graph|indexing|embeddings|clustering|traversal|ranking|retrieval|pipeline|performance|visualization)([. ])/\1\2 codecortex.\3\4/' your_module.py
```

An application that cannot move yet should pin below 2.0 — see
[Consuming applications](#consuming-applications).

Saved project indexes (`.codecortex/`) written by 1.x are rebuilt on first use.

## Troubleshooting

**"the working tree has uncommitted changes"** — the git strategy will not
discard your work. Commit or stash, then rerun.

**"cannot update this installation automatically"** — CodeCortex is vendored or
managed by another packaging system. Use that system, or the printed
`pip install --upgrade` command.

**"no TTY — pass --yes to proceed non-interactively"** — an automated caller must
opt in explicitly. Scheduled jobs should pass `--yes`.

**"database is at schema version N, but this CodeCortex understands up to M"** —
an older CodeCortex is reading a cache written by a newer one. Upgrade
CodeCortex, or delete the cache directory and re-index.

**Health check warns about version metadata** — the installed distribution
metadata is stale, which happens after editing an editable checkout. Reinstall:

```bash
pip install -e .
```

**Update check finds nothing on a repository that has tags** — the repository has
git tags but no published GitHub Releases. CodeCortex falls back to tags
automatically; publish releases to get artifacts and release notes.
