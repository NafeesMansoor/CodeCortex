"""Graph snapshot: serialize / deserialize GraphStore to JSON.

Enables fast cold-start by loading a pre-built CPG from disk rather than
re-parsing the entire codebase on every startup.

Delta strategy: snapshots include a file → mtime map. On reload, only files
whose mtime has changed are re-parsed and merged into the loaded graph.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GraphSnapshot:
    """Serialize and deserialize a GraphStore to/from a JSON snapshot file.

    Format:
        {
          "version": 1,
          "created_at": <unix timestamp>,
          "file_mtimes": {"path": mtime, ...},
          "nodes": [{...}, ...],
          "edges": [{...}, ...]
        }
    """

    VERSION = 1

    def __init__(self, store):
        self.store = store

    def save(self, path: Path, file_mtimes: Optional[dict[str, float]] = None) -> int:
        """Write snapshot to path. Returns number of nodes saved."""
        nodes = self.store.all_nodes()
        edges = self.store.all_edges()

        snapshot = {
            "version": self.VERSION,
            "created_at": time.time(),
            "file_mtimes": file_mtimes or {},
            "nodes": [self._node_to_dict(n) for n in nodes],
            "edges": [self._edge_to_dict(e) for e in edges],
        }

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
        logger.info("Snapshot saved: %d nodes, %d edges → %s", len(nodes), len(edges), path)
        return len(nodes)

    def load(self, path: Path) -> dict[str, float]:
        """Load snapshot into the store. Returns stored file_mtimes dict."""
        from core.types import NodeKind, EdgeKind
        from graph.schema import CPGNode, CPGEdge

        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))

        if data.get("version") != self.VERSION:
            raise ValueError(f"Unsupported snapshot version: {data.get('version')}")

        for nd in data["nodes"]:
            node = CPGNode(
                qualified_name=nd["qualified_name"],
                kind=NodeKind(nd["kind"]),
                name=nd["name"],
                file_path=nd["file_path"],
                language=nd.get("language", ""),
                parent_qualified=nd.get("parent_qualified"),
                is_test=nd.get("is_test", False),
                extra=nd.get("extra") or {},
            )
            self.store.add_node(node)

        for ed in data["edges"]:
            edge = CPGEdge(
                kind=EdgeKind(ed["kind"]),
                source=ed["source"],
                target=ed["target"],
                file_path=ed.get("file_path", ""),
                confidence=ed.get("confidence", 1.0),
            )
            self.store.add_edge(edge)

        logger.info(
            "Snapshot loaded: %d nodes, %d edges from %s",
            len(data["nodes"]), len(data["edges"]), path,
        )
        return data.get("file_mtimes", {})

    def stale_files(
        self,
        snapshot_path: Path,
        current_mtimes: dict[str, float],
    ) -> list[str]:
        """Return files that have changed since the snapshot was saved."""
        try:
            data = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            saved = data.get("file_mtimes", {})
        except (FileNotFoundError, json.JSONDecodeError):
            return list(current_mtimes.keys())

        stale = []
        for file_path, mtime in current_mtimes.items():
            if file_path not in saved or saved[file_path] != mtime:
                stale.append(file_path)
        # Also detect deleted files
        for file_path in saved:
            if file_path not in current_mtimes:
                stale.append(file_path)
        return stale

    @staticmethod
    def _node_to_dict(node) -> dict:
        return {
            "qualified_name": node.qualified_name,
            "kind": node.kind.value,
            "name": node.name,
            "file_path": node.file_path,
            "language": getattr(node, "language", ""),
            "parent_qualified": getattr(node, "parent_qualified", None),
            "is_test": bool(getattr(node, "is_test", False)),
            "extra": getattr(node, "extra", None) or {},
        }

    @staticmethod
    def _edge_to_dict(edge) -> dict:
        return {
            "kind": edge.kind.value,
            "source": edge.source,
            "target": edge.target,
            "file_path": getattr(edge, "file_path", ""),
            "confidence": getattr(edge, "confidence", 1.0),
        }
