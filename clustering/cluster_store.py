"""SQLite-backed persistence for cluster assignments.

Stores cluster metadata and member assignments so that clustering
results survive restarts and can be incrementally updated.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Optional

from clustering.semantic_clusters import Cluster, ClusteringResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clusters (
    cluster_id  INTEGER PRIMARY KEY,
    label       TEXT    NOT NULL DEFAULT '',
    algorithm   TEXT    NOT NULL DEFAULT '',
    centroid    TEXT                          -- JSON list[float]
);

CREATE TABLE IF NOT EXISTS cluster_members (
    qualified_name  TEXT NOT NULL,
    cluster_id      INTEGER NOT NULL,
    PRIMARY KEY (qualified_name),
    FOREIGN KEY (cluster_id) REFERENCES clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_members_cluster ON cluster_members(cluster_id);
"""


class ClusterStore:
    """Persists ClusteringResult to/from SQLite.

    Usage:
        store = ClusterStore("data/clusters.db")
        store.save(result)
        reloaded = store.load()
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, result: ClusteringResult) -> None:
        """Persist a ClusteringResult, replacing any existing data."""
        with closing(self._conn.cursor()) as cur:
            cur.execute("DELETE FROM cluster_members")
            cur.execute("DELETE FROM clusters")

            all_clusters = list(result.clusters)
            if result.noise_members:
                all_clusters.append(
                    Cluster(
                        cluster_id=-1,
                        members=result.noise_members,
                        label="noise",
                    )
                )

            for cluster in all_clusters:
                centroid_json = json.dumps(cluster.centroid) if cluster.centroid else None
                cur.execute(
                    "INSERT OR REPLACE INTO clusters (cluster_id, label, algorithm, centroid) "
                    "VALUES (?, ?, ?, ?)",
                    (cluster.cluster_id, cluster.label, result.algorithm, centroid_json),
                )
                cur.executemany(
                    "INSERT OR IGNORE INTO cluster_members (qualified_name, cluster_id) VALUES (?, ?)",
                    [(m, cluster.cluster_id) for m in cluster.members],
                )

        self._conn.commit()

    def update_label(self, cluster_id: int, label: str) -> None:
        """Update the human-readable label for a cluster."""
        self._conn.execute(
            "UPDATE clusters SET label=? WHERE cluster_id=?",
            (label, cluster_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> Optional[ClusteringResult]:
        """Reload the most recently saved ClusteringResult."""
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT cluster_id, label, algorithm, centroid FROM clusters")
            rows = cur.fetchall()
            if not rows:
                return None

            algorithm = rows[0][2] if rows else "unknown"

            clusters_map: dict[int, Cluster] = {}
            for cid, label, alg, centroid_json in rows:
                centroid = json.loads(centroid_json) if centroid_json else None
                clusters_map[cid] = Cluster(
                    cluster_id=cid,
                    label=label,
                    centroid=centroid,
                )

            cur.execute("SELECT qualified_name, cluster_id FROM cluster_members")
            for name, cid in cur.fetchall():
                if cid in clusters_map:
                    clusters_map[cid].members.append(name)

        noise = clusters_map.pop(-1, None)
        return ClusteringResult(
            clusters=sorted(clusters_map.values(), key=lambda c: c.cluster_id),
            noise_members=noise.members if noise else [],
            algorithm=algorithm,
        )

    def cluster_of(self, qualified_name: str) -> Optional[int]:
        """Return the cluster_id for a node, or None if not stored."""
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT cluster_id FROM cluster_members WHERE qualified_name=?",
                (qualified_name,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def size(self) -> int:
        """Total number of stored cluster assignments."""
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM cluster_members")
            return cur.fetchone()[0]

    def n_clusters(self) -> int:
        """Number of non-noise clusters stored."""
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM clusters WHERE cluster_id >= 0")
            return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
