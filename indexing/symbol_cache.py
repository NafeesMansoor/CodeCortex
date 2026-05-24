"""Disk-backed symbol table — persistent repository symbol cache.

Stores fully resolved SymbolDefinitions so that subsequent sessions don't
need to re-run jedi/LSP/SCIP for files that haven't changed.

Invalidation is file-based: when a file changes (mtime), its symbols
are dropped and will be re-indexed on next access.

Schema:
  symbols(qualified_name, kind, file_path, line, col, language,
          module_path, display_name, mtime)
  -- primary key on qualified_name, index on display_name

Usage:
    cache = SymbolCache(Path("~/.cache/codecortex/symbols.db"))
    cache.put(defn, file_mtime=1234567890.0)
    defn = cache.get("utils.helpers.bar")
    results = cache.search("bar")   # prefix / exact search
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from pathlib import Path
from typing import Optional

from core.types import NodeKind
from indexing.indexing_provider import SymbolDefinition


class SymbolCache:
    """SQLite-backed persistent cache of symbol definitions."""

    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS symbols (
                qualified_name TEXT PRIMARY KEY,
                kind           TEXT NOT NULL,
                file_path      TEXT NOT NULL,
                line           INTEGER NOT NULL DEFAULT 0,
                col            INTEGER NOT NULL DEFAULT 0,
                language       TEXT NOT NULL DEFAULT '',
                module_path    TEXT NOT NULL DEFAULT '',
                display_name   TEXT NOT NULL DEFAULT '',
                mtime          REAL NOT NULL DEFAULT 0.0
            );
            CREATE INDEX IF NOT EXISTS idx_sym_display ON symbols(display_name);
            CREATE INDEX IF NOT EXISTS idx_sym_file    ON symbols(file_path);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def put(self, defn: SymbolDefinition, file_mtime: float = 0.0) -> None:
        with self._tx():
            self._conn.execute("""
                INSERT OR REPLACE INTO symbols
                  (qualified_name, kind, file_path, line, col,
                   language, module_path, display_name, mtime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                defn.qualified_name,
                defn.kind.value,
                defn.file_path,
                defn.line,
                defn.column,
                defn.language,
                defn.module_path,
                defn.display_name or defn.qualified_name.rsplit(".", 1)[-1],
                file_mtime,
            ))

    def put_many(self, definitions: list[SymbolDefinition], file_mtime: float = 0.0) -> None:
        with self._tx():
            self._conn.executemany("""
                INSERT OR REPLACE INTO symbols
                  (qualified_name, kind, file_path, line, col,
                   language, module_path, display_name, mtime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    d.qualified_name,
                    d.kind.value,
                    d.file_path,
                    d.line,
                    d.column,
                    d.language,
                    d.module_path,
                    d.display_name or d.qualified_name.rsplit(".", 1)[-1],
                    file_mtime,
                )
                for d in definitions
            ])

    def invalidate_file(self, file_path: str) -> None:
        """Drop all symbols from file_path (call before re-indexing it)."""
        with self._tx():
            self._conn.execute(
                "DELETE FROM symbols WHERE file_path = ?", (file_path,)
            )

    def invalidate_if_changed(self, file_path: str) -> bool:
        """Drop symbols for file_path if the file's mtime has changed.

        Returns True if the file was invalidated (needs re-indexing).
        """
        try:
            current_mtime = Path(file_path).stat().st_mtime
        except FileNotFoundError:
            self.invalidate_file(file_path)
            return True

        row = self._conn.execute(
            "SELECT mtime FROM symbols WHERE file_path = ? LIMIT 1",
            (file_path,),
        ).fetchone()

        if row is None or abs(row[0] - current_mtime) > 0.01:
            self.invalidate_file(file_path)
            return True
        return False

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, qualified_name: str) -> Optional[SymbolDefinition]:
        row = self._conn.execute(
            "SELECT qualified_name, kind, file_path, line, col, "
            "language, module_path, display_name FROM symbols "
            "WHERE qualified_name = ?",
            (qualified_name,),
        ).fetchone()
        return _row_to_defn(row) if row else None

    def search(self, name: str, exact: bool = False) -> list[SymbolDefinition]:
        """Search by display_name or qualified_name suffix.

        Args:
            name: Short or partial name to match.
            exact: If True, require exact display_name match.
        """
        if exact:
            rows = self._conn.execute(
                "SELECT qualified_name, kind, file_path, line, col, "
                "language, module_path, display_name FROM symbols "
                "WHERE display_name = ?",
                (name,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT qualified_name, kind, file_path, line, col, "
                "language, module_path, display_name FROM symbols "
                "WHERE display_name LIKE ? OR qualified_name LIKE ?",
                (f"{name}%", f"%.{name}%"),
            ).fetchall()
        return [_row_to_defn(r) for r in rows]

    def symbols_in_file(self, file_path: str) -> list[SymbolDefinition]:
        rows = self._conn.execute(
            "SELECT qualified_name, kind, file_path, line, col, "
            "language, module_path, display_name FROM symbols "
            "WHERE file_path = ?",
            (file_path,),
        ).fetchall()
        return [_row_to_defn(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

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


def _row_to_defn(row: tuple) -> SymbolDefinition:
    try:
        kind = NodeKind(row[1])
    except ValueError:
        kind = NodeKind.VARIABLE
    return SymbolDefinition(
        qualified_name=row[0],
        kind=kind,
        file_path=row[2],
        line=row[3],
        column=row[4],
        language=row[5],
        module_path=row[6],
        display_name=row[7],
    )
