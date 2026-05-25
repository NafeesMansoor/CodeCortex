"""Local visualization server for CodeCortex semantic graph explorer.

Builds the pipeline, exports graph data, and serves the interactive
graph UI on a local HTTP server. Opens the browser automatically.

Usage:
    codecortex visualize /path/to/repo [--port 7979] [--no-open]
    python -m visualization.server /path/to/repo
"""

from __future__ import annotations

import argparse
import http.server
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATIC_DIR = Path(__file__).resolve().parent / "static"
_GRAPH_DATA: dict = {}
_PROJECT_NAME: str = ""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request output

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif self.path == "/graph.json":
            payload = json.dumps(_GRAPH_DATA, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(404)

    def _serve_file(self, path: Path, content_type: str):
        if not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _build_and_export(target: Path, languages: list[str] | None) -> dict:
    from pipeline.context_builder import CodeCortexPipeline, PipelineConfig
    from visualization.graph_exporter import GraphExporter

    print("\nCodeCortex Visualizer")
    print(f"  Target  : {target}")
    print("  Building index…", flush=True)

    config = PipelineConfig(
        enable_cfg=True,
        enable_dfg=True,
        enable_endpoints=True,
        enable_clustering=True,
        use_louvain=True,
        use_graph_pruning=True,
        semantic_only=True,
    )
    t0 = time.perf_counter()
    pipeline = CodeCortexPipeline.from_directory(target, config=config, languages=languages)
    elapsed = time.perf_counter() - t0

    store = pipeline.graph_store
    nodes = store.node_count() if store else 0
    edges = store.edge_count() if store else 0
    print(f"  Done in {elapsed:.1f}s — {nodes} nodes, {edges} edges")
    print("  Exporting graph…", flush=True)

    data = GraphExporter(pipeline).export(project_name=target.name)
    print(
        f"  Export ready — {data['metadata']['cluster_count']} clusters, "
        f"{data['metadata']['hotspot_count']} hotspots"
    )
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="codecortex visualize",
        description="Launch the interactive CodeCortex semantic graph explorer.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Source directory to index and visualize (default: current directory)",
    )
    parser.add_argument("--port", type=int, default=7979, help="HTTP port (default: 7979)")
    parser.add_argument("--no-open", action="store_true", help="Do not open browser automatically")
    parser.add_argument(
        "--languages",
        nargs="+",
        metavar="LANG",
        default=None,
        help="Limit to specific languages: py js ts php",
    )
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"ERROR: {target} is not a directory", file=sys.stderr)
        return 1

    global _GRAPH_DATA, _PROJECT_NAME
    _GRAPH_DATA = _build_and_export(target, args.languages)
    _PROJECT_NAME = target.name

    url = f"http://localhost:{args.port}"
    server = http.server.HTTPServer(("localhost", args.port), _Handler)

    print(f"\n  Graph Explorer : {url}")
    print("  Press Ctrl+C to stop\n")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
