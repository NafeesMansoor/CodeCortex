"""Schema stamping on the SQLite caches, and the rebuild-on-stale migration path."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from clustering.cluster_store import ClusterStore
from codecortex import migrations
from core.db_schema import (
    SchemaVersionError,
    ensure_schema,
    read_schema_version,
    write_schema_version,
)
from core.types import EdgeKind, NodeKind
from graph.graph_store import GraphStore
from graph.schema import CPGEdge, CPGNode
from indexing.reference_graph import ReferenceGraph
from indexing.symbol_cache import SymbolCache

DDL = "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY, name TEXT)"


def probe(conn, **overrides):
    kwargs = dict(store="probe", expected=2, create_sql=DDL, tables=("widgets",))
    kwargs.update(overrides)
    return ensure_schema(conn, **kwargs)


class TestEnsureSchema:
    def test_fresh_database_is_created_and_stamped(self):
        conn = sqlite3.connect(":memory:")
        assert probe(conn) == "created"
        assert read_schema_version(conn) == 2

    def test_matching_version_opens_unchanged(self):
        conn = sqlite3.connect(":memory:")
        probe(conn)
        conn.execute("INSERT INTO widgets (name) VALUES ('keep')")
        conn.commit()
        assert probe(conn) == "current"
        assert conn.execute("SELECT name FROM widgets").fetchone()[0] == "keep"

    def test_stale_database_is_rebuilt(self):
        conn = sqlite3.connect(":memory:")
        probe(conn)
        conn.execute("INSERT INTO widgets (name) VALUES ('stale')")
        conn.commit()
        write_schema_version(conn, 1)

        assert probe(conn) == "rebuilt"
        assert read_schema_version(conn) == 2
        assert conn.execute("SELECT count(*) FROM widgets").fetchone()[0] == 0

    def test_newer_database_is_refused(self):
        conn = sqlite3.connect(":memory:")
        probe(conn)
        write_schema_version(conn, 99)
        with pytest.raises(SchemaVersionError, match="version 99"):
            probe(conn)

    def test_rebuild_can_be_disabled(self):
        conn = sqlite3.connect(":memory:")
        probe(conn)
        write_schema_version(conn, 1)
        with pytest.raises(SchemaVersionError, match="rebuilding is disabled"):
            probe(conn, allow_rebuild=False)

    def test_unstamped_legacy_database_is_rebuilt_not_misread(self):
        """A pre-versioning cache has user_version 0 but real tables."""
        conn = sqlite3.connect(":memory:")
        conn.execute(DDL)
        conn.execute("INSERT INTO widgets (name) VALUES ('legacy')")
        conn.commit()
        assert read_schema_version(conn) == 0

        assert probe(conn) == "rebuilt"
        assert read_schema_version(conn) == 2
        assert conn.execute("SELECT count(*) FROM widgets").fetchone()[0] == 0


class TestStoresAreStamped:
    @pytest.mark.parametrize(
        "factory,name",
        [
            (GraphStore, "graph"),
            (SymbolCache, "symbols"),
            (ReferenceGraph, "occurrences"),
            (ClusterStore, "clusters"),
        ],
    )
    def test_every_store_stamps_its_schema(self, tmp_path, factory, name):
        path = tmp_path / f"{name}.db"
        factory(str(path))
        with sqlite3.connect(path) as conn:
            assert read_schema_version(conn) >= 1

    def test_reopening_preserves_data(self, tmp_path):
        path = str(tmp_path / "graph.db")
        store = GraphStore(path)
        store.add_node(CPGNode("alpha", NodeKind.FUNCTION, "alpha", "f.py"))
        store.add_node(CPGNode("beta", NodeKind.FUNCTION, "beta", "f.py"))
        store.add_edge(CPGEdge(EdgeKind.CALLS, "alpha", "beta"))

        reopened = GraphStore(path)
        assert reopened.node_count() == 2

    def test_stale_graph_cache_is_rebuilt_on_open(self, tmp_path):
        path = str(tmp_path / "graph.db")
        store = GraphStore(path)
        store.add_node(CPGNode("alpha", NodeKind.FUNCTION, "alpha", "f.py"))

        with sqlite3.connect(path) as conn:
            write_schema_version(conn, 0)
            conn.execute("PRAGMA user_version = 0")

        rebuilt = GraphStore(path)
        assert rebuilt.node_count() == 0

    def test_newer_graph_cache_refuses_to_open(self, tmp_path):
        path = str(tmp_path / "graph.db")
        GraphStore(path)
        with sqlite3.connect(path) as conn:
            write_schema_version(conn, 999)
        with pytest.raises(SchemaVersionError):
            GraphStore(path)


class TestMigrationPlanning:
    def _stores(self, tmp_path):
        GraphStore(str(tmp_path / "graph.db"))
        SymbolCache(str(tmp_path / "symbols.db"))
        ReferenceGraph(str(tmp_path / "refs.db"))
        ClusterStore(str(tmp_path / "clusters.db"))

    def test_schema_versions_cover_every_store(self):
        versions = migrations.schema_versions()
        assert set(versions) == {"graph", "symbol_cache", "reference_graph", "cluster_store"}
        assert all(isinstance(v, int) and v >= 1 for v in versions.values())

    def test_discover_finds_databases(self, tmp_path):
        self._stores(tmp_path)
        nested = tmp_path / "nested"
        nested.mkdir()
        GraphStore(str(nested / "graph.db"))
        assert len(migrations.discover(tmp_path)) == 5

    def test_discover_missing_directory(self, tmp_path):
        assert migrations.discover(tmp_path / "nope") == []

    def test_current_caches_need_no_migration(self, tmp_path):
        self._stores(tmp_path)
        steps = migrations.plan(migrations.discover(tmp_path))
        assert len(steps) == 4
        assert all(step.action == "none" for step in steps)
        assert not any(step.needed for step in steps)

    def test_stale_cache_is_planned_for_rebuild(self, tmp_path):
        self._stores(tmp_path)
        with sqlite3.connect(tmp_path / "graph.db") as conn:
            write_schema_version(conn, 0)

        steps = {s.store: s for s in migrations.plan(migrations.discover(tmp_path))}
        assert steps["graph"].action == "rebuild"
        assert steps["symbol_cache"].action == "none"

    def test_newer_cache_is_blocked_not_rebuilt(self, tmp_path):
        self._stores(tmp_path)
        with sqlite3.connect(tmp_path / "graph.db") as conn:
            write_schema_version(conn, 999)

        steps = {s.store: s for s in migrations.plan(migrations.discover(tmp_path))}
        assert steps["graph"].action == "blocked"
        assert "newer" in steps["graph"].detail

    def test_unknown_database_is_ignored(self, tmp_path):
        path = tmp_path / "other.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
        assert migrations.plan([path]) == []

    def test_already_migrated_database_is_idempotent(self, tmp_path):
        self._stores(tmp_path)
        paths = migrations.discover(tmp_path)
        migrations.run(paths)
        assert all(step.action == "none" for step in migrations.run(paths))


class TestMigrationExecution:
    def test_run_rebuilds_stale_cache(self, tmp_path):
        path = tmp_path / "graph.db"
        store = GraphStore(str(path))
        store.add_node(CPGNode("alpha", NodeKind.FUNCTION, "alpha", "f.py"))
        with sqlite3.connect(path) as conn:
            write_schema_version(conn, 0)

        steps = migrations.run([path])
        assert steps[0].action == "rebuild"
        assert read_schema_version(sqlite3.connect(path)) == steps[0].expected_version
        assert GraphStore(str(path)).node_count() == 0

    def test_dry_run_changes_nothing(self, tmp_path):
        path = tmp_path / "graph.db"
        GraphStore(str(path))
        with sqlite3.connect(path) as conn:
            write_schema_version(conn, 0)

        steps = migrations.run([path], dry_run=True)
        assert steps[0].action == "rebuild"
        assert read_schema_version(sqlite3.connect(path)) == 0

    def test_requires_migration_compares_against_local(self):
        local = migrations.schema_versions()
        assert not migrations.requires_migration(local)
        assert migrations.requires_migration({"graph": max(local.values()) + 10})
        assert not migrations.requires_migration({})
