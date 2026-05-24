"""JSON-RPC 2.0 LSP client — spawns a language-server subprocess.

Implements the minimum LSP surface needed for CodeCortex:
  - initialize / initialized / shutdown / exit lifecycle
  - textDocument/definition
  - textDocument/references

Wire format: Content-Length header framing over the process's stdin/stdout.

Usage:
    client = LSPClient(["pyright", "--stdio"], root_uri="file:///path/to/project")
    client.start()
    client.open_file("file:///path/to/project/foo.py", text)
    defs = client.definition("file:///...foo.py", line=5, character=10)
    client.shutdown()
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds


class LSPError(Exception):
    pass


class LSPClient:
    """Synchronous JSON-RPC 2.0 client for LSP language servers."""

    def __init__(self, server_command: list[str], root_uri: str):
        self.server_command = server_command
        self.root_uri = root_uri
        self._proc: Optional[subprocess.Popen] = None
        self._seq = 0
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, Any] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the language server process and run LSP initialize."""
        self._proc = subprocess.Popen(
            self.server_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        result = self._request("initialize", {
            "processId": None,
            "rootUri": self.root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                }
            },
            "initializationOptions": {},
        })
        if result is None:
            raise LSPError("initialize returned no result")

        self._notify("initialized", {})

    def shutdown(self) -> None:
        """Send shutdown + exit and terminate the process."""
        if not self._running:
            return
        try:
            self._request("shutdown", {})
            self._notify("exit", {})
        except Exception:
            pass
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    def open_file(self, uri: str, text: str, language_id: str = "python") -> None:
        self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            }
        })

    def close_file(self, uri: str) -> None:
        self._notify("textDocument/didClose", {
            "textDocument": {"uri": uri}
        })

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def definition(self, uri: str, line: int, character: int) -> list[dict]:
        """Return definition locations for the symbol at (line, character)."""
        result = self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        return _normalise_locations(result)

    def references(self, uri: str, line: int, character: int) -> list[dict]:
        """Return all reference locations for the symbol at (line, character)."""
        result = self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": False},
        })
        return _normalise_locations(result)

    def hover(self, uri: str, line: int, character: int) -> str:
        result = self._request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result:
            return ""
        contents = result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", "")
        if isinstance(contents, list):
            return " ".join(
                c.get("value", c) if isinstance(c, dict) else c
                for c in contents
            )
        return str(contents)

    # ------------------------------------------------------------------
    # Internal JSON-RPC transport
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _request(self, method: str, params: Any) -> Any:
        req_id = self._next_id()
        event = threading.Event()
        self._pending[req_id] = event
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        if not event.wait(timeout=_TIMEOUT):
            self._pending.pop(req_id, None)
            raise LSPError(f"timeout waiting for {method} response")
        return self._results.pop(req_id, None)

    def _notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _reader_loop(self) -> None:
        """Background thread: reads Content-Length framed messages from stdout."""
        assert self._proc and self._proc.stdout
        while self._running:
            try:
                header = b""
                while b"\r\n\r\n" not in header:
                    chunk = self._proc.stdout.read(1)
                    if not chunk:
                        return
                    header += chunk
                length_line = header.split(b"\r\n")[0]
                length = int(length_line.split(b":")[1].strip())
                body = self._proc.stdout.read(length)
                msg = json.loads(body)
                self._dispatch(msg)
            except Exception as e:
                if self._running:
                    logger.debug("LSP reader error: %s", e)
                return

    def _dispatch(self, msg: dict) -> None:
        req_id = msg.get("id")
        if req_id is not None and req_id in self._pending:
            self._results[req_id] = msg.get("result")
            self._pending.pop(req_id).set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_locations(result: Any) -> list[dict]:
    """Normalise LSP Location | Location[] | LocationLink[] to a flat list."""
    if not result:
        return []
    if isinstance(result, dict):
        return [result]
    return list(result)


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a filesystem path."""
    if uri.startswith("file://"):
        return uri[7:]
    return uri


def path_to_uri(path: str) -> str:
    p = Path(path).resolve()
    return f"file://{p}"
