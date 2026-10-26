"""Wire CodeCortex into a project's AI assistant.

    codecortex install   [--platform claude|agents|cursor|gemini|all] [--dir DIR] [--no-hooks]
    codecortex uninstall [--platform ...] [--dir DIR] [--purge]

``install`` writes short instructions telling the assistant to ask the saved
code graph (``codecortex query`` / ``explain`` / ``--impact``) before
grepping or reading files for a codebase question. Nothing runs in the
background: the assistant calls CodeCortex when a question needs it, and each
call reuses ``.codecortex/`` and re-parses only changed files.

For Claude Code it also registers ``codecortex hook-guard`` as a PreToolUse
hook (see ``codecortex.hook``) that notes the graph when a search or source
read is about to run.

Instruction files are edited between marker comments, and the hook entry is
found by its command, so a re-install replaces them and an uninstall removes
exactly what was added.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

START = "<!-- codecortex:start -->"
END = "<!-- codecortex:end -->"

_RULES = """\
This project is indexed by CodeCortex: a code graph saved in `.codecortex/`
(definitions, calls, imports, inheritance across 16 languages).

- For a question about this codebase, run `codecortex query --query "<question>"`
  before your first grep or source-file read, and read raw files only for the
  lines it points you to. Pass this rule on to any subagent that explores code.
- `codecortex explain <Symbol>` shows one symbol's definition, callers, callees
  and base types. Before changing a symbol, run
  `codecortex query --impact <Qualified.name>` to see what depends on it.
- These commands reuse the saved index and re-parse only files that changed, so
  run them whenever you need them. If `.codecortex/` is missing,
  `codecortex index .` builds it once.
- Skip CodeCortex for an edit to a file you already have open.
"""

_SECTION = f"{START}\n## codecortex\n\n{_RULES}{END}\n"

_SKILL = f"""\
---
name: codecortex
description: Answer questions about this codebase from the CodeCortex code graph instead of grepping and reading files. Use for "where is / what calls / what depends on / how does X work" questions and before changing a widely used symbol.
---

# CodeCortex

{_RULES}
## Commands

| Need | Command |
|---|---|
| Find the code relevant to a question | `codecortex query --query "how are sessions validated"` |
| One symbol's definition, callers, callees | `codecortex explain UserService.login` |
| What breaks if a symbol changes | `codecortex query --impact UserService.login` |
| Machine-readable output | add `--json` to any of the above |
| Rebuild after a large refactor or branch switch | `codecortex index .` |
"""

_CURSOR_RULE = f"""\
---
description: Use the CodeCortex code graph for codebase questions
alwaysApply: true
---

{_RULES}"""

# platform → files it owns. A "section" is merged into a shared instruction
# file; a "file" belongs to CodeCortex outright.
PLATFORMS: dict[str, list[tuple[str, str, str]]] = {
    "claude": [
        ("section", "CLAUDE.md", _SECTION),
        ("file", ".claude/skills/codecortex/SKILL.md", _SKILL),
    ],
    "agents": [("section", "AGENTS.md", _SECTION)],  # Codex, OpenCode, Amp, Copilot …
    "gemini": [("section", "GEMINI.md", _SECTION)],
    "cursor": [("file", ".cursor/rules/codecortex.mdc", _CURSOR_RULE)],
}


def _strip_section(text: str) -> str:
    start = text.find(START)
    end = text.find(END, start)
    if start == -1 or end == -1:
        return text
    before = text[:start].rstrip("\n")
    after = text[end + len(END) :].lstrip("\n")
    if before and after:
        return f"{before}\n\n{after}"
    return f"{before}\n" if before else after


HOOK_COMMAND = "codecortex hook-guard"
_SETTINGS = ".claude/settings.json"


class SettingsError(RuntimeError):
    """``.claude/settings.json`` is not a JSON object we can safely edit."""


def _read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise SettingsError(f"{path} is not valid JSON ({e}); left unchanged") from None
    hooks = settings.get("hooks", {}) if isinstance(settings, dict) else None
    if not isinstance(hooks, dict) or not isinstance(hooks.get("PreToolUse", []), list):
        raise SettingsError(f"{path} has an unexpected shape; left unchanged")
    return settings


def _without_our_hook(entries: list) -> list:
    return [
        entry
        for entry in entries
        if not any(
            HOOK_COMMAND in str(hook.get("command", ""))
            for hook in (entry.get("hooks", []) if isinstance(entry, dict) else [])
            if isinstance(hook, dict)
        )
    ]


def install_hook(project: Path) -> Path:
    """Register the PreToolUse hook in .claude/settings.json, keeping the rest."""
    path = project / _SETTINGS
    settings = _read_settings(path)
    hooks = settings.setdefault("hooks", {})
    entries = _without_our_hook(hooks.get("PreToolUse", []))
    entries.append(
        {
            "matcher": "Grep|Glob|Read|Bash",
            # A bare command: the file is committed, so a machine-specific
            # path would be wrong for everyone else.
            "hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 10}],
        }
    )
    hooks["PreToolUse"] = entries
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path


def uninstall_hook(project: Path) -> Optional[Path]:
    """Remove our hook entry; return the settings path if it changed."""
    path = project / _SETTINGS
    if not path.exists():
        return None
    settings = _read_settings(path)
    hooks = settings.get("hooks", {})
    entries = hooks.get("PreToolUse", [])
    kept = _without_our_hook(entries)
    if kept == entries:
        return None
    if kept:
        hooks["PreToolUse"] = kept
    else:
        hooks.pop("PreToolUse", None)
        if not hooks:
            settings.pop("hooks", None)
    if settings:
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    else:
        path.unlink()
    return path


def install(project: Path, platforms: list[str]) -> list[Path]:
    """Write each platform's files under project; return the paths written."""
    written = []
    for platform in platforms:
        for kind, relative, content in PLATFORMS[platform]:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "section":
                existing = path.read_text(encoding="utf-8") if path.exists() else ""
                base = _strip_section(existing).rstrip("\n")
                content = f"{base}\n\n{content}" if base else content
            path.write_text(content, encoding="utf-8")
            written.append(path)
    return written


def uninstall(project: Path, platforms: list[str]) -> list[Path]:
    """Remove what install wrote; return the paths changed or deleted."""
    changed = []
    for platform in platforms:
        for kind, relative, _content in PLATFORMS[platform]:
            path = project / relative
            if not path.exists():
                continue
            if kind == "file":
                path.unlink()
                if path.parent.name == "codecortex" and not any(path.parent.iterdir()):
                    path.parent.rmdir()
                changed.append(path)
                continue
            text = path.read_text(encoding="utf-8")
            stripped = _strip_section(text)
            if stripped == text:
                continue
            if stripped.strip():
                path.write_text(stripped, encoding="utf-8")
            else:
                path.unlink()
            changed.append(path)
    return changed


def _parse(argv, prog: str, purge: bool) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument(
        "--platform",
        action="append",
        choices=[*PLATFORMS, "all"],
        help="Assistant to configure; repeatable (default: claude)",
    )
    parser.add_argument("--dir", default=".", help="Project root (default: .)")
    if purge:
        parser.add_argument(
            "--purge", action="store_true", help="Also delete the saved index (.codecortex/)"
        )
    else:
        parser.add_argument(
            "--no-hooks",
            action="store_true",
            help="Claude Code: skip the PreToolUse hook that points searches at the graph",
        )
    args = parser.parse_args(argv)
    chosen = args.platform or ["claude"]
    args.platforms = list(PLATFORMS) if "all" in chosen else list(dict.fromkeys(chosen))
    return args


def install_main(argv=None) -> int:
    args = _parse(argv, "codecortex install", purge=False)
    project = Path(args.dir).resolve()
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 1

    for path in install(project, args.platforms):
        print(f"  wrote {path.relative_to(project)}")
    if "claude" in args.platforms and not args.no_hooks:
        try:
            path = install_hook(project)
            print(f"  wrote {path.relative_to(project)}  (PreToolUse hook: {HOOK_COMMAND})")
        except SettingsError as e:
            print(f"WARNING: hook not installed: {e}", file=sys.stderr)

    from codecortex.pipeline.state import has_saved_index

    if not has_saved_index(project):
        print("\nNext: build the index once with `codecortex index .`")
    if shutil.which("codecortex") is None:
        print(
            "\nWARNING: `codecortex` is not on PATH, so the assistant cannot run it.\n"
            "Install it as a tool: pipx install "
            '"codecortex @ git+https://github.com/NafeesMansoor/CodeCortex.git"',
            file=sys.stderr,
        )
    print("\nCommit the files above to share the setup; .codecortex/ ignores itself.")
    return 0


def uninstall_main(argv=None) -> int:
    args = _parse(argv, "codecortex uninstall", purge=True)
    project = Path(args.dir).resolve()

    for path in uninstall(project, args.platforms):
        print(f"  removed CodeCortex from {path.relative_to(project)}")
    if "claude" in args.platforms:
        try:
            path = uninstall_hook(project)
        except SettingsError as e:
            print(f"WARNING: hook not removed: {e}", file=sys.stderr)
        else:
            if path is not None:
                print(f"  removed the CodeCortex hook from {path.relative_to(project)}")

    if args.purge:
        from codecortex.pipeline.state import state_dir_for

        state_dir = state_dir_for(project)
        if state_dir.is_dir():
            shutil.rmtree(state_dir)
            print(f"  deleted {state_dir.relative_to(project)}/")
    return 0
