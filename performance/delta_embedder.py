"""Delta embedding updater.

Tracks which files have changed since the last embedding run and
only re-embeds the affected nodes — avoiding a full re-index.

Usage:
    updater = DeltaEmbedder(pipeline, snapshot_mtimes)
    stats = updater.update(current_files)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DeltaEmbedder:
    """Incremental embedding updater driven by file mtime changes.

    Compares current file modification times against a recorded baseline and
    triggers re-embedding only for files that changed.
    """

    def __init__(self, embedding_pipeline, baseline_mtimes: Optional[dict[str, float]] = None):
        self.pipeline = embedding_pipeline
        self._baseline: dict[str, float] = baseline_mtimes or {}

    def update(self, files: list[Path]) -> dict:
        """Re-embed only files that changed since the baseline.

        Args:
            files: List of source files to consider.

        Returns:
            Dict with keys: changed_files, nodes_embedded, nodes_skipped.
        """
        changed = [f for f in files if self._is_stale(f)]
        if not changed:
            logger.debug("DeltaEmbedder: no changed files")
            return {"changed_files": 0, "nodes_embedded": 0, "nodes_skipped": 0}

        total_embedded = 0
        total_skipped = 0

        for file_path in changed:
            stats = self.pipeline.index_file(str(file_path))
            total_embedded += stats.nodes_embedded
            total_skipped += stats.nodes_skipped
            self._baseline[str(file_path)] = file_path.stat().st_mtime

        logger.info(
            "DeltaEmbedder: %d files changed, %d nodes re-embedded",
            len(changed),
            total_embedded,
        )
        return {
            "changed_files": len(changed),
            "nodes_embedded": total_embedded,
            "nodes_skipped": total_skipped,
        }

    def record_baseline(self, files: list[Path]) -> None:
        """Record current mtimes as the new baseline."""
        for f in files:
            try:
                self._baseline[str(f)] = f.stat().st_mtime
            except OSError:
                pass

    def baseline_snapshot(self) -> dict[str, float]:
        """Return a copy of the current baseline mtime map."""
        return dict(self._baseline)

    def _is_stale(self, file_path: Path) -> bool:
        try:
            current_mtime = file_path.stat().st_mtime
        except OSError:
            return False
        return self._baseline.get(str(file_path)) != current_mtime
