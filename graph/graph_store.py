"""In-memory graph store backed by SQLite for persistence.

Provides the runtime graph used by TraversalEngine and CPGBuilder.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from typing import Generator, Optional

from core.db_schema import ensure_schema
from core.types import EdgeKind, NodeKind
from graph.schema import CPGEdge, CPGNode

logger = logging.getLogger(__name__)

# Bump when the nodes/edges DDL below changes; stale caches are then rebuilt.
SCHEMA_VERSION = 1

_NODES_DDL = """
CREATE TABLE IF NOT EXISTS nodes (
    qualified_name TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    language       TEXT DEFAULT '',
    range_start    INTEGER DEFAULT 0,
    range_end      INTEGER DEFAULT 0,
    parent_qualified TEXT,
    is_test        INTEGER DEFAULT 0,
    extra          TEXT DEFAULT '{}'
)
"""

_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS edges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    source     TEXT NOT NULL,
    target     TEXT NOT NULL,
    file_path  TEXT DEFAULT '',
    confidence REAL DEFAULT 1.0,
    extra      TEXT DEFAULT '{}'
)
"""

_EDGE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target);
"""

_NODE_INSERT = """INSERT OR REPLACE INTO nodes
    (qualified_name, kind, name, file_path, language,
     range_start, range_end, parent_qualified, is_test, extra)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_EDGE_INSERT = """INSERT INTO edges (kind, source, target, file_path, confidence, extra)
    VALUES (?, ?, ?, ?, ?, ?)"""


def _node_params(node: CPGNode) -> tuple:
    return (
        node.qualified_name,
        node.kind.value,
        node.name,
        node.file_path,
        node.language,
        node.range.start_line if node.range else 0,
        node.range.end_line if node.range else 0,
        node.parent_qualified,
        int(node.is_test or False),
        json.dumps(node.extra),
    )


def _edge_params(edge: CPGEdge) -> tuple:
    return (
        edge.kind.value,
        edge.source,
        edge.target,
        edge.file_path,
        edge.confidence,
        json.dumps(edge.extra),
    )


class GraphStore:
    """SQLite-backed graph store with in-memory fallback.

    Usage:
        store = GraphStore(":memory:")   # in-process
        store = GraphStore("path/to/graph.db")  # persistent
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        ensure_schema(
            self._conn,
            store="graph",
            expected=SCHEMA_VERSION,
            create_sql=_NODES_DDL + ";" + _EDGES_DDL + ";" + _EDGE_INDEX_DDL,
            tables=("nodes", "edges"),
        )

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Cursor, None, None]:
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # --- Node operations ---

    def add_node(self, node: CPGNode) -> None:
        with self._tx() as cur:
            cur.execute(_NODE_INSERT, _node_params(node))

    def add_nodes(self, nodes: list[CPGNode]) -> None:
        """Insert nodes in a single transaction — one commit, not one per row."""
        if not nodes:
            return
        with self._tx() as cur:
            cur.executemany(_NODE_INSERT, [_node_params(n) for n in nodes])

    def get_node(self, qualified_name: str) -> Optional[CPGNode]:
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE qualified_name = ?", (qualified_name,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_node(row)

    def has_node(self, qualified_name: str) -> bool:
        """Existence check that skips row hydration."""
        return (
            self._conn.execute(
                "SELECT 1 FROM nodes WHERE qualified_name = ? LIMIT 1", (qualified_name,)
            ).fetchone()
            is not None
        )

    def get_nodes_by_file(self, file_path: str) -> list[CPGNode]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_by_kind(self, kind: NodeKind) -> list[CPGNode]:
        rows = self._conn.execute("SELECT * FROM nodes WHERE kind = ?", (kind.value,)).fetchall()
        return [self._row_to_node(r) for r in rows]

    def all_nodes(self) -> list[CPGNode]:
        rows = self._conn.execute("SELECT * FROM nodes").fetchall()
        return [self._row_to_node(r) for r in rows]

    def node_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    # --- Edge operations ---

    def add_edge(self, edge: CPGEdge) -> None:
        with self._tx() as cur:
            cur.execute(_EDGE_INSERT, _edge_params(edge))

    def add_edges(self, edges: list[CPGEdge]) -> None:
        """Insert edges in a single transaction — one commit, not one per row."""
        if not edges:
            return
        with self._tx() as cur:
            cur.executemany(_EDGE_INSERT, [_edge_params(e) for e in edges])

    def get_outgoing_edges(self, qualified_name: str) -> list[CPGEdge]:
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE source = ?", (qualified_name,)
        ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_incoming_edges(self, qualified_name: str) -> list[CPGEdge]:
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE target = ?", (qualified_name,)
        ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_edges_by_kind(self, kind: EdgeKind) -> list[CPGEdge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE kind = ?", (kind.value,)).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def all_edges(self) -> list[CPGEdge]:
        rows = self._conn.execute("SELECT * FROM edges").fetchall()
        return [self._row_to_edge(r) for r in rows]

    def edge_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    # --- Bulk clear ---

    def clear(self) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM edges")
            cur.execute("DELETE FROM nodes")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Row → dataclass helpers ---

    def _row_to_node(self, row: sqlite3.Row) -> CPGNode:
        from core.types import SourceRange

        return CPGNode(
            qualified_name=row["qualified_name"],
            kind=NodeKind(row["kind"]),
            name=row["name"],
            file_path=row["file_path"],
            language=row["language"] or "",
            range=SourceRange(row["range_start"], row["range_end"]) if row["range_start"] else None,
            parent_qualified=row["parent_qualified"],
            is_test=bool(row["is_test"]),
            extra=json.loads(row["extra"] or "{}"),
        )

    def _row_to_edge(self, row: sqlite3.Row) -> CPGEdge:
        return CPGEdge(
            kind=EdgeKind(row["kind"]),
            source=row["source"],
            target=row["target"],
            file_path=row["file_path"] or "",
            confidence=row["confidence"],
            extra=json.loads(row["extra"] or "{}"),
        )
