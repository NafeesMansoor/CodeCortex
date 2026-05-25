"""Parallel file indexing using concurrent.futures.

Parses files in a thread pool (I/O-bound read + CPU-bound parse).
The GIL limits CPU parallelism for pure-Python parse work, but the
tree-sitter C extension releases the GIL, so ThreadPoolExecutor provides
real speedup for parse-heavy workloads.

Usage:
    indexer = ParallelIndexer(max_workers=4)
    results = indexer.parse_directory("/path/to/src", language="python")
    for result in results:
        builder.ingest(result)
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from core.types import ParseResult

logger = logging.getLogger(__name__)

_EXT_MAP = {
    "python": "py",
    "javascript": "js",
    "typescript": "ts",
    "php": "php",
}


class ParallelIndexer:
    """Thread-pool-based file parser.

    Returns ParseResult objects in completion order (not file order).
    """

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers

    def parse_directory(
        self,
        root: str | Path,
        language: str = "python",
    ) -> list[ParseResult]:
        """Parse all files of the given language under root."""
        root = Path(root)
        ext = _EXT_MAP.get(language, language)
        files = sorted(root.rglob(f"*.{ext}"))
        if not files:
            return []

        parser = self._get_parser(language)
        if parser is None:
            logger.warning("No parser for language %r", language)
            return []

        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._parse_one, parser, f): f for f in files}
            for future in as_completed(futures):
                file_path = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except Exception as e:
                    logger.debug("Failed to parse %s: %s", file_path, e)

        return results

    def parse_files(
        self,
        files: list[Path],
        language: str = "python",
    ) -> list[ParseResult]:
        """Parse a specific list of files."""
        parser = self._get_parser(language)
        if parser is None:
            return []

        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._parse_one, parser, f): f for f in files}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except Exception as e:
                    logger.debug("Parse error: %s", e)

        return results

    @staticmethod
    def _parse_one(parser, file_path: Path) -> Optional[ParseResult]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            return parser.parse(source, str(file_path))
        except Exception as e:
            logger.debug("Error reading/parsing %s: %s", file_path, e)
            return None

    @staticmethod
    def _get_parser(language: str):
        try:
            if language == "python":
                from core.parsers.python_parser import PythonParser

                return PythonParser()
            if language in ("javascript", "js"):
                from core.parsers.javascript_parser import JavaScriptParser

                return JavaScriptParser()
            if language in ("typescript", "ts"):
                from core.parsers.typescript_parser import TypeScriptParser

                return TypeScriptParser()
            if language == "php":
                from core.parsers.php_parser import PHPParser

                return PHPParser()
        except Exception as e:
            logger.debug("Parser init failed for %r: %s", language, e)
        return None
