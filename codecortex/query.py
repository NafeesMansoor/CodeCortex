"""CodeCortex query CLI.

Usage:
    python -m codecortex.query --query TEXT [options]

Examples:
    python -m codecortex.query --query "authentication flow" --target .
    python -m codecortex.query --query "database connection" --bfs-depth 4 --enable-centrality-ranking
    python -m codecortex.query --query "HTTP endpoint routing" --enable-on-demand-cfg --enable-on-demand-dfg
    python -m codecortex.query --query "find user service" --mode hybrid --max-tokens 2000
    python -m codecortex.query --impact UserService.authenticate --target .
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m codecortex.query",
        description="Query a CodeCortex semantic index.",
    )

    # Input
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", "-q", metavar="TEXT", help="Natural language query")
    group.add_argument("--impact", metavar="QNAME", help="Impact analysis for a qualified name")

    parser.add_argument(
        "--target",
        default=".",
        metavar="DIR",
        help="Source directory to index (default: current directory)",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "hybrid"],
        default="full",
        help="full = eager CPG; hybrid = on-demand CFG/DFG expansion (default: full)",
    )
    parser.add_argument(
        "--bfs-depth",
        type=int,
        default=3,
        metavar="N",
        help="Maximum BFS traversal depth (default: 3)",
    )
    parser.add_argument(
        "--enable-centrality-ranking",
        action="store_true",
        help="Weight results by pre-computed centrality scores",
    )
    parser.add_argument(
        "--enable-on-demand-cfg",
        action="store_true",
        help="Expand CFG lazily for files containing query results",
    )
    parser.add_argument(
        "--enable-on-demand-dfg",
        action="store_true",
        help="Expand DFG lazily for files containing query results",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=3000,
        metavar="N",
        help="Token budget for context output (default: 3000)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        metavar="N",
        help="Number of results to retrieve (default: 20)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to codecortex.yaml",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        metavar="LANG",
        default=None,
        help="Limit to: py js ts php",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        metavar="PATH",
        default=None,
        help="Extra paths to exclude, relative to target (e.g. storage/ docs/)",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Walk every directory, including virtualenvs, node_modules and caches",
    )
    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Do not honour the repository .gitignore when walking",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of plain text",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")

    args = parser.parse_args(argv)
    target = Path(args.target).resolve()

    if not target.is_dir():
        print(f"ERROR: target directory does not exist: {target}", file=sys.stderr)
        return 1

    from codecortex.config import CodeCortexConfig, find_config
    from codecortex.config import load as load_config

    config_path = args.config or find_config(target)
    cc_config = load_config(config_path) if config_path else CodeCortexConfig()

    # CLI overrides
    cc_config.enable_embeddings = True
    cc_config.centrality_ranking = args.enable_centrality_ranking
    cc_config.retrieval_top_k = args.top_k
    cc_config.token_budget = args.max_tokens

    if args.mode == "hybrid" or args.enable_on_demand_cfg:
        cc_config.on_demand_cfg = True
    if args.mode == "hybrid" or args.enable_on_demand_dfg:
        cc_config.on_demand_dfg = True

    if args.exclude:
        cc_config.exclude_paths = list(dict.fromkeys(cc_config.exclude_paths + args.exclude))
    if args.no_default_excludes:
        cc_config.exclude_dirs = []
    if args.no_gitignore:
        cc_config.respect_gitignore = False

    pipeline_cfg = cc_config.to_pipeline_config()

    if not args.quiet:
        print(f"\nBuilding index for {target} …", file=sys.stderr)

    from codecortex.cli import make_progress_printer
    from pipeline.context_builder import CodeCortexPipeline

    pipeline = CodeCortexPipeline.from_directory(
        target,
        config=pipeline_cfg,
        languages=args.languages,
        on_progress=make_progress_printer(args.quiet),
    )

    if not args.quiet:
        print("Index ready.\n", file=sys.stderr)

    # ------------------------------------------------------------------
    # Impact mode
    # ------------------------------------------------------------------
    if args.impact:
        summary = pipeline.impact(args.impact, max_depth=args.bfs_depth)
        if args.json:
            import json

            print(json.dumps(summary.to_dict(), indent=2))
        else:
            d = summary.to_dict()
            print(f"Impact: {d['root']}")
            print(f"  Direct callers  : {', '.join(d['direct_callers']) or '(none)'}")
            print(f"  Affected files  : {', '.join(d['affected_files']) or '(none)'}")
            print(f"  Affected tests  : {', '.join(d['affected_tests']) or '(none)'}")
            print(f"  Transitive nodes: {len(d['transitive_affected'])}")
        return 0

    # ------------------------------------------------------------------
    # Query mode
    # ------------------------------------------------------------------
    if args.json:
        results = pipeline.retrieve(args.query)
        import json

        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        context = pipeline.query(args.query, max_tokens=args.max_tokens)
        print(context)

    return 0


if __name__ == "__main__":
    sys.exit(main())
