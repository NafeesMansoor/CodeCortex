"""CodeCortex unified CLI entry point.

Usage:
    codecortex index   <dir> [options]     — build and save the semantic graph index
    codecortex query   --query TEXT [opts] — query an indexed codebase
    codecortex explain SYMBOL [opts]       — one symbol's callers, callees, bases
    codecortex install [--platform P]      — tell this project's AI assistant to use it
    codecortex uninstall [--purge]         — remove that setup
    codecortex visualize <dir> [options]   — launch interactive graph explorer
    codecortex version [--check]           — show version / check for updates
    codecortex update                      — update this installation
    codecortex health                      — verify this installation

Aliases: cx index / cx query / cx explain / cx visualize
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from codecortex.version import __version__


def make_progress_printer(
    quiet: bool, label: str = "Parsing", every: int = 25
) -> Optional[Callable[[int, int, Path], None]]:
    """Return a progress callback for pipeline.build(), or None when quiet.

    Indexing a large tree used to be silent, making a slow run indistinguishable
    from a hang. Writes to stderr so piped stdout stays clean.
    """
    if quiet:
        return None

    stream = sys.stderr
    interactive = stream.isatty()

    def report(done: int, total: int, path: Path) -> None:
        if done != total and done % every:
            return
        if interactive:
            stream.write(f"\r  {label} : {done}/{total} files")
            if done == total:
                stream.write("\n")
        else:
            stream.write(f"  {label} : {done}/{total} files\n")
        stream.flush()

    return report


_USAGE = f"""\
CodeCortex v{__version__} — Adaptive Semantic Intelligence Engine

Usage:
  codecortex index     <dir> [options]       Build the graph and save it to .codecortex/
  codecortex query     --query TEXT [opts]   Query an indexed codebase
  codecortex explain   SYMBOL [opts]         One symbol's definition, callers, callees
  codecortex install   [--platform P]        Set up this project's AI assistant to use it
  codecortex uninstall [--purge]             Remove that setup (and the saved index)
  codecortex visualize <dir> [options]       Launch interactive graph explorer
  codecortex version   [--check]             Show version, or check for updates
  codecortex update    [--check] [--yes]     Update this installation
  codecortex health    [--json]              Verify this installation

Options (per subcommand):
  codecortex index --help
  codecortex query --help
  codecortex explain --help
  codecortex install --help
  codecortex visualize --help

Examples:
  codecortex install
  codecortex index .
  codecortex index . --mode cpg --enable-embeddings --enable-clustering
  codecortex query --query "authentication middleware" --target .
  codecortex query --impact UserService.login --target .
  codecortex explain UserService.login
  codecortex visualize .
  codecortex visualize /path/to/repo --port 8080
  codecortex version --check
  codecortex update --yes
"""


def notify_update_available(stream=sys.stderr) -> None:
    """Print a cached update notice, if any. Reads no network and never raises."""
    try:
        from codecortex.updater import cached_notification

        notice = cached_notification()
    except Exception:
        return
    if notice:
        print(f"\n{notice}\n", file=stream)


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        notify_update_available(sys.stdout)
        return 0

    if args[0] in ("-v", "--version"):
        print(f"CodeCortex {__version__}")
        return 0

    subcommand = args[0]
    rest = args[1:]

    if subcommand == "index":
        from codecortex.index import main as _main

        return _main(rest)

    if subcommand == "query":
        from codecortex.query import main as _main

        return _main(rest)

    if subcommand == "explain":
        from codecortex.explain import main as _main

        return _main(rest)

    if subcommand == "install":
        from codecortex.integrate import install_main

        return install_main(rest)

    if subcommand == "hook-guard":
        from codecortex.hook import main as _main

        return _main(rest)

    if subcommand == "uninstall":
        from codecortex.integrate import uninstall_main

        return uninstall_main(rest)

    if subcommand in ("visualize", "viz"):
        from codecortex.visualization.server import main as _main

        return _main(rest)

    if subcommand == "version":
        from codecortex.update_cli import version_main

        return version_main(rest)

    if subcommand == "update":
        from codecortex.update_cli import update_main

        return update_main(rest)

    if subcommand == "health":
        from codecortex.health import main as _main

        return _main(rest)

    print(f"Unknown subcommand: {subcommand!r}", file=sys.stderr)
    print("Run 'codecortex --help' for usage.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
