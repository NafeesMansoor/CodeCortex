"""CodeCortex explain CLI: one symbol and its immediate neighbourhood.

Usage:
    codecortex explain SYMBOL [--target DIR] [--json]

Prints where the symbol is defined and what it calls, imports and inherits,
plus who calls it — a scoped answer that costs far fewer tokens than reading
the files. Uses the saved index in <target>/.codecortex/, building it first
if there is none.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Edges worth showing; CONTAINS and the CFG/DFG kinds are structural noise here.
_OUTGOING = ("CALLS", "IMPORTS_FROM", "INHERITS", "IMPLEMENTS", "EXTENDS", "USES")
_INCOMING = ("CALLS", "INHERITS", "IMPLEMENTS", "EXTENDS", "TESTS")


def _relative(path: str, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def explain(store, symbol: str, root: Path, limit: int) -> list[dict]:
    """Describe every node matching ``symbol``."""
    described = []
    for node in store.find_nodes(symbol)[:limit]:
        if node.kind.value == "File":
            continue
        outgoing = sorted(
            {
                (e.kind.value, e.target)
                for e in store.get_outgoing_edges(node.qualified_name)
                if e.kind.value in _OUTGOING
            }
        )
        # Calls are recorded against the callee's bare name, so callers of
        # `Cls.method` are found under "method" too.
        incoming = set()
        for target in {node.qualified_name, node.name}:
            incoming.update(
                (e.kind.value, e.source)
                for e in store.get_incoming_edges(target)
                if e.kind.value in _INCOMING
            )
        line = f":{node.range.start_line}" if node.range and node.range.start_line else ""
        described.append(
            {
                "symbol": node.qualified_name,
                "kind": node.kind.value,
                "location": _relative(node.file_path, root) + line,
                "language": node.language,
                "outgoing": [{"kind": k, "target": t} for k, t in outgoing],
                "incoming": [{"kind": k, "source": s} for k, s in sorted(incoming)],
            }
        )
    return described


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="codecortex explain",
        description="Explain one symbol: definition, callers, callees, base types.",
    )
    parser.add_argument("symbol", help="Bare or qualified name, e.g. login or UserService.login")
    parser.add_argument("--target", default=".", metavar="DIR", help="Project root (default: .)")
    parser.add_argument("--limit", type=int, default=5, help="Matches to show (default: 5)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the saved index first")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"ERROR: target directory does not exist: {target}", file=sys.stderr)
        return 1

    from codecortex.cli import make_progress_printer
    from codecortex.config import CodeCortexConfig, find_config
    from codecortex.config import load as load_config
    from codecortex.pipeline.state import open_pipeline
    from codecortex.query import describe_open

    config_path = find_config(target)
    cc_config = load_config(config_path) if config_path else CodeCortexConfig()
    pipeline, opened = open_pipeline(
        target,
        config=cc_config.to_pipeline_config(),
        on_progress=make_progress_printer(args.quiet),
        rebuild=args.rebuild,
        derived=False,  # a lookup needs only the graph
    )
    if not args.quiet:
        print(describe_open(opened), file=sys.stderr)

    described = explain(pipeline.graph_store, args.symbol, target, args.limit)
    if args.json:
        print(json.dumps(described, indent=2))
        return 0 if described else 1
    if not described:
        print(f"No symbol named {args.symbol!r} in the index.")
        return 1

    for entry in described:
        print(f"{entry['symbol']}  [{entry['kind']}]  {entry['location']}")
        for edge in entry["outgoing"]:
            print(f"  --{edge['kind'].lower()}--> {edge['target']}")
        for edge in entry["incoming"]:
            print(f"  <--{edge['kind'].lower()}-- {edge['source']}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
