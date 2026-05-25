"""CodeCortex unified CLI entry point.

Usage:
    codecortex index   <dir> [options]     — build semantic graph index
    codecortex query   --query TEXT [opts] — query an indexed codebase
    codecortex visualize <dir> [options]   — launch interactive graph explorer

Aliases: cx index / cx query / cx visualize
"""

from __future__ import annotations

import sys

_USAGE = """\
CodeCortex v1.0.1 — Adaptive Semantic Intelligence Engine

Usage:
  codecortex index     <dir> [options]       Build semantic graph index
  codecortex query     --query TEXT [opts]   Query an indexed codebase
  codecortex visualize <dir> [options]       Launch interactive graph explorer

Options (per subcommand):
  codecortex index --help
  codecortex query --help
  codecortex visualize --help

Examples:
  codecortex index .
  codecortex index . --mode cpg --enable-embeddings --enable-clustering
  codecortex query --query "authentication middleware" --target .
  codecortex query --impact UserService.login --target .
  codecortex visualize .
  codecortex visualize /path/to/repo --port 8080
"""


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0

    if args[0] in ("-v", "--version"):
        print("CodeCortex 1.0.1")
        return 0

    subcommand = args[0]
    rest = args[1:]

    if subcommand == "index":
        from codecortex.index import main as _main

        return _main(rest)

    if subcommand == "query":
        from codecortex.query import main as _main

        return _main(rest)

    if subcommand in ("visualize", "viz"):
        from visualization.server import main as _main

        return _main(rest)

    print(f"Unknown subcommand: {subcommand!r}", file=sys.stderr)
    print("Run 'codecortex --help' for usage.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
