"""CodeCortex indexer CLI.

Usage:
    python -m codecortex.index [TARGET_DIR] [options]

Examples:
    python -m codecortex.index .
    python -m codecortex.index /path/to/repo --mode cpg
    python -m codecortex.index . --enable-embeddings --enable-clustering hdbscan
    python -m codecortex.index . --save-snapshot .codecortex/graph.json
    python -m codecortex.index . --config codecortex.yaml
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m codecortex.index",
        description="Build a CodeCortex semantic graph index for a source directory.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Source directory to index (default: current directory)",
    )
    parser.add_argument(
        "--mode",
        choices=["lsp", "cpg"],
        default="cpg",
        help="lsp = call/import graph only; cpg = full CPG with CFG+DFG (default: cpg)",
    )
    parser.add_argument(
        "--enable-embeddings",
        action="store_true",
        default=False,
        help="Build FAISS embedding index after graph construction",
    )
    parser.add_argument(
        "--enable-clustering",
        metavar="ALGO",
        nargs="?",
        const="hdbscan",
        default=None,
        help="Cluster embedded nodes (default algo: hdbscan)",
    )
    parser.add_argument(
        "--on-demand-cfg",
        action="store_true",
        help="Defer CFG construction to query time (hybrid Tier-2 mode)",
    )
    parser.add_argument(
        "--on-demand-dfg",
        action="store_true",
        help="Defer DFG construction to query time (hybrid Tier-2 mode)",
    )
    parser.add_argument(
        "--save-snapshot",
        metavar="PATH",
        default=None,
        help="Save graph snapshot to PATH after indexing",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to codecortex.yaml config file",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        metavar="LANG",
        default=None,
        help="Limit to specific languages: py js ts php",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        metavar="PATH",
        default=None,
        help="Directory names or paths to exclude (e.g. vendor node_modules storage)",
    )
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)
    target = Path(args.target).resolve()

    if not target.is_dir():
        print(f"ERROR: target directory does not exist: {target}", file=sys.stderr)
        return 1

    # Load YAML config if provided, then apply CLI overrides
    from codecortex.config import CodeCortexConfig, find_config, load as load_config

    config_path = args.config or find_config(target)
    cc_config = load_config(config_path) if config_path else CodeCortexConfig()

    # CLI flags override YAML
    cc_config.enable_cpg = args.mode == "cpg"
    cc_config.enable_embeddings = args.enable_embeddings
    cc_config.enable_clustering = args.enable_clustering is not None
    if args.on_demand_cfg:
        cc_config.on_demand_cfg = True
    if args.on_demand_dfg:
        cc_config.on_demand_dfg = True

    pipeline_cfg = cc_config.to_pipeline_config()

    if not args.quiet:
        mode_label = "CPG (CFG+DFG)" if cc_config.enable_cpg else "LSP (call+import)"
        if cc_config.on_demand_cfg or cc_config.on_demand_dfg:
            mode_label += " + on-demand CFG/DFG"
        print(f"\nCodeCortex Indexer")
        print(f"  Target   : {target}")
        print(f"  Mode     : {mode_label}")
        print(f"  Embeddings : {'yes' if cc_config.enable_embeddings else 'no'}")
        print(f"  Clustering : {'yes' if cc_config.enable_clustering else 'no'}")
        print()

    from pipeline.context_builder import CodeCortexPipeline

    # Merge CLI --exclude with any exclude_paths from YAML
    exclude = list(args.exclude or [])
    if hasattr(cc_config, "exclude_paths") and cc_config.exclude_paths:
        exclude = list({*exclude, *cc_config.exclude_paths})

    t0 = time.perf_counter()
    pipeline = CodeCortexPipeline(pipeline_cfg)
    stats = pipeline.build(target, languages=args.languages, exclude=exclude or None)
    elapsed = time.perf_counter() - t0

    if not args.quiet:
        print(f"  Files    : {stats.files_parsed}  (errors: {stats.parse_errors})")
        print(f"  Nodes    : {stats.nodes}")
        print(f"  Edges    : {stats.edges}")
        print(f"  Embedded : {stats.nodes_embedded}")
        print(f"  Clusters : {stats.clusters}")
        print(f"  Time     : {elapsed:.2f}s")

    if args.save_snapshot:
        snap_path = Path(args.save_snapshot)
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        from performance.graph_snapshot import GraphSnapshot
        snap = GraphSnapshot(pipeline.graph_store)
        mtimes = {str(f): f.stat().st_mtime for f in pipeline._indexed_files if f.exists()}
        n = snap.save(snap_path, file_mtimes=mtimes)
        if not args.quiet:
            print(f"\n  Snapshot : {snap_path}  ({n} nodes saved)")

    if not args.quiet:
        print("\nIndexing complete.\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
