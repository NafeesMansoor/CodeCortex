"""Cross-file reference graph — SQLite-backed occurrence store.

Tracks every symbol occurrence (definition, reference, write) across all
indexed files. The GraphEnricher uses this to upgrade unresolved CPG edges
with semantically resolved targets.

Schema:
  occurrences(id, symbol, file_path, line, col, role)
  -- indexes on symbol and file_path

Usage:
    rg = ReferenceGraph(":memory:")
    rg.add(SymbolOccurrence(symbol="utils.helpers.bar", file_path="src/foo.py", ...))
    refs = rg.references_to("utils.helpers.bar")  # all call sites
"""

from __future__ import annotations

import contextlib
import sqlite3

from indexing.indexing_provider import SymbolOccurrence


class ReferenceGraph:
    """Persistent cross-file reference index backed by SQLite."""

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS occurrences (
                id        INTEGER PRIMARY KEY,
                symbol    TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line      INTEGER NOT NULL,
                col       INTEGER NOT NULL DEFAULT 0,
                role      TEXT NOT NULL DEFAULT 'reference'
            );
            CREATE INDEX IF NOT EXISTS idx_occ_symbol    ON occurrences(symbol);
            CREATE INDEX IF NOT EXISTS idx_occ_file_path ON occurrences(file_path);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(self, occ: SymbolOccurrence) -> None:
        with self._tx():
            self._conn.execute(
                "INSERT INTO occurrences(symbol, file_path, line, col, role) "
                "VALUES (?, ?, ?, ?, ?)",
                (occ.symbol, occ.file_path, occ.line, occ.column, occ.role),
            )

    def add_many(self, occurrences: list[SymbolOccurrence]) -> None:
        with self._tx():
            self._conn.executemany(
                "INSERT INTO occurrences(symbol, file_path, line, col, role) "
                "VALUES (?, ?, ?, ?, ?)",
                [(o.symbol, o.file_path, o.line, o.column, o.role) for o in occurrences],
            )

    def remove_file(self, file_path: str) -> None:
        """Remove all occurrences originating from file_path (for incremental updates)."""
        with self._tx():
            self._conn.execute("DELETE FROM occurrences WHERE file_path = ?", (file_path,))

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def references_to(self, symbol: str) -> list[SymbolOccurrence]:
        """All occurrences of symbol across the whole codebase."""
        rows = self._conn.execute(
            "SELECT symbol, file_path, line, col, role FROM occurrences WHERE symbol = ?",
            (symbol,),
        ).fetchall()
        return [_row_to_occ(r) for r in rows]

    def references_in_file(self, file_path: str) -> list[SymbolOccurrence]:
        """All occurrences recorded for a specific file."""
        rows = self._conn.execute(
            "SELECT symbol, file_path, line, col, role FROM occurrences WHERE file_path = ?",
            (file_path,),
        ).fetchall()
        return [_row_to_occ(r) for r in rows]

    def callers_of(self, symbol: str) -> list[str]:
        """Return file paths of all files that reference symbol."""
        rows = self._conn.execute(
            "SELECT DISTINCT file_path FROM occurrences WHERE symbol = ? AND role = 'reference'",
            (symbol,),
        ).fetchall()
        return [r[0] for r in rows]

    def definitions_of(self, symbol: str) -> list[SymbolOccurrence]:
        rows = self._conn.execute(
            "SELECT symbol, file_path, line, col, role FROM occurrences "
            "WHERE symbol = ? AND role = 'definition'",
            (symbol,),
        ).fetchall()
        return [_row_to_occ(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]

    def symbols(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT symbol FROM occurrences").fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _tx(self):
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


def _row_to_occ(row: tuple) -> SymbolOccurrence:
    return SymbolOccurrence(
        symbol=row[0],
        file_path=row[1],
        line=row[2],
        column=row[3],
        role=row[4],
    )
