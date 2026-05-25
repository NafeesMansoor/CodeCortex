"""LSP-backed indexing provider.

Wraps LSPClient to implement the IndexingProvider interface.
Supports any language that has an LSP server command configured.

Built-in server commands:
  python     → pyright --stdio
  typescript → typescript-language-server --stdio
  javascript → typescript-language-server --stdio
  php        → intelephense --stdio

Usage:
    provider = LSPProvider("python")
    if provider.is_available():
        result = provider.index_file(Path("src/foo.py"), Path("src"))
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from indexing.indexing_provider import (
    IndexingProvider,
    IndexResult,
    SymbolOccurrence,
)
from indexing.lsp_client import LSPClient, path_to_uri, uri_to_path

logger = logging.getLogger(__name__)

_SERVER_COMMANDS: dict[str, list[str]] = {
    "python": ["pyright", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "php": ["intelephense", "--stdio"],
}

_LANGUAGE_IDS: dict[str, str] = {
    "python": "python",
    "typescript": "typescript",
    "javascript": "javascript",
    "php": "php",
}


class LSPProvider(IndexingProvider):
    """Indexing provider backed by a running LSP language server.

    Creates one LSP client per index_project call and reuses it across
    all files in that project. The client is shut down when the provider
    goes out of scope or shutdown() is called explicitly.
    """

    priority = 2
    name = "lsp"

    def __init__(self, language: str):
        self.language = language
        self._client: LSPClient | None = None

    def supports(self, language: str) -> bool:
        return language == self.language

    def is_available(self) -> bool:
        cmd = _SERVER_COMMANDS.get(self.language, [])
        if not cmd:
            return False
        return shutil.which(cmd[0]) is not None

    def shutdown(self) -> None:
        if self._client:
            self._client.shutdown()
            self._client = None

    def index_file(self, file_path: Path, project_root: Path) -> IndexResult:
        result = IndexResult(language=self.language, source=self.name)
        if not self.is_available():
            result.errors.append(f"LSP server for {self.language} not found")
            return result

        try:
            client = self._get_or_start_client(project_root)
            uri = path_to_uri(str(file_path))
            source = file_path.read_text(errors="replace")
            lang_id = _LANGUAGE_IDS.get(self.language, self.language)

            client.open_file(uri, source, lang_id)
            lines = source.splitlines()

            for lineno, line in enumerate(lines):
                # Scan for identifiers followed by '(' (call sites)
                col = 0
                while col < len(line):
                    idx = line.find("(", col)
                    if idx == -1:
                        break
                    start = idx - 1
                    while start >= 0 and (line[start].isalnum() or line[start] == "_"):
                        start -= 1
                    start += 1
                    ident = line[start:idx]
                    if ident and (ident[0].isalpha() or ident[0] == "_"):
                        try:
                            defs = client.definition(uri, lineno, start)
                            for loc in defs:
                                target_uri = loc.get("uri") or loc.get("targetUri", "")
                                target_path = uri_to_path(target_uri)
                                target_range = loc.get("range") or loc.get(
                                    "targetSelectionRange", {}
                                )
                                target_line = target_range.get("start", {}).get("line", 0)
                                result.occurrences.append(
                                    SymbolOccurrence(
                                        symbol=f"{target_path}:{target_line}:{ident}",
                                        file_path=str(file_path),
                                        line=lineno + 1,
                                        column=start,
                                        role="reference",
                                    )
                                )
                                break
                        except Exception:
                            pass
                    col = idx + 1

            client.close_file(uri)
        except Exception as e:
            logger.warning("LSP index_file %s: %s", file_path, e)
            result.errors.append(str(e))

        return result

    def _get_or_start_client(self, project_root: Path) -> LSPClient:
        if self._client is None:
            cmd = _SERVER_COMMANDS[self.language]
            root_uri = path_to_uri(str(project_root))
            self._client = LSPClient(cmd, root_uri)
            self._client.start()
        return self._client

    def __del__(self):
        self.shutdown()
