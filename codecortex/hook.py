"""``codecortex hook-guard``: a Claude Code PreToolUse hook.

Registered by ``codecortex install`` for Grep, Glob, Read and Bash. When the
project has a saved index and the assistant is about to search or read source
files, it adds a one-line note that ``codecortex query`` / ``explain`` can
answer the question more cheaply. It never blocks a tool, says it at most once
per session for searches and once for reads, and exits silently on anything
unexpected: a hook that fails or stalls would cost every tool call.

Stdlib only, and nothing heavy is imported before the cheap checks pass.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

STATE_DIR = ".codecortex"  # pipeline.state.STATE_DIR_NAME, without importing it

# Notes state facts; imperative wording in a tool result reads like an
# injection attempt and gets ignored.
_SEARCH_NOTE = (
    "codecortex: this project has a saved code graph in .codecortex/. "
    '`codecortex query --query "<question>"` returns the symbols and files that '
    "matter for a question, usually in far fewer tokens than searching raw files."
)
_READ_NOTE = (
    "codecortex: this project has a saved code graph in .codecortex/. "
    "`codecortex explain <Symbol>` shows a symbol's definition, callers and "
    "callees, and `codecortex query --impact <name>` what depends on it — often "
    "enough without reading whole files."
)

_SEARCH_COMMAND = re.compile(r"(^|[\s|;&(])(grep|egrep|rg|ag|ack|find|fd|git\s+grep)\b")


def _project_root(start: Path) -> Optional[Path]:
    for directory in (start, *start.parents):
        if (directory / STATE_DIR / "state.json").is_file():
            return directory
    return None


def _kind(tool: str, tool_input: dict) -> Optional[str]:
    if tool in ("Grep", "Glob"):
        return "search"
    if tool == "Bash" and _SEARCH_COMMAND.search(str(tool_input.get("command", ""))):
        return "search"
    if tool == "Read":
        return "read"
    return None


def _is_source_file(path: str) -> bool:
    from codecortex.core.parsers.registry import PARSER_CLASSES

    suffix = Path(path).suffix.lower()
    return any(suffix in cls.extensions for cls in PARSER_CLASSES)


def _marker(root: Path, session: str, kind: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "default")[:120]
    return root / STATE_DIR / "hook-sessions" / f"{safe}.{kind}"


def note_for(payload: dict) -> Optional[str]:
    """The note to add for one PreToolUse payload, or None.

    The cheap checks run first: once a session has had its note, a call
    returns before importing anything.
    """
    tool_input = payload.get("tool_input") or {}
    kind = _kind(str(payload.get("tool_name", "")), tool_input)
    if kind is None:
        return None
    root = _project_root(Path(payload.get("cwd") or ".").resolve())
    if root is None:
        return None
    marker = _marker(root, str(payload.get("session_id", "")), kind)
    if marker.exists():
        return None
    if kind == "read":
        path = str(tool_input.get("file_path", ""))
        if STATE_DIR in Path(path).parts or not _is_source_file(path):
            return None
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return _SEARCH_NOTE if kind == "search" else _READ_NOTE


def main(argv=None) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        note = note_for(payload) if isinstance(payload, dict) else None
    except Exception:
        return 0
    if note:
        output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note}}
        sys.stdout.write(json.dumps(output) + "\n")
    return 0
