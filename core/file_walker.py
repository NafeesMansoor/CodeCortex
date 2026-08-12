"""Source file discovery with directory pruning.

``Path.rglob`` descends into every directory below the root. On a repository
that keeps its virtualenv in-tree — what ``python -m venv venv`` produces by
default — that means parsing the entire dependency tree, commonly two orders of
magnitude more source than the project itself.

``walk_source_files`` prunes excluded directories *before* descending, so the
cost is proportional to the project rather than to what is installed inside it.

Usage:
    for path in walk_source_files(root, ["py", "js"]):
        ...
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

# Directory names skipped at any depth unless the caller overrides the set.
DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        # virtualenvs
        "venv",
        ".venv",
        "env",
        ".env",
        "virtualenv",
        ".direnv",
        # installed dependencies
        "site-packages",
        "dist-packages",
        "node_modules",
        "bower_components",
        "vendor",
        # version control
        ".git",
        ".hg",
        ".svn",
        # caches
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        # build output
        "build",
        "dist",
        ".eggs",
        ".next",
        ".nuxt",
    }
)

# Directory names skipped by suffix (e.g. "codecortex.egg-info").
DEFAULT_EXCLUDED_DIR_SUFFIXES = (".egg-info",)


class GitIgnore:
    """Matcher for the repository-root ``.gitignore``.

    Supports the patterns that matter for file discovery: comments, blank
    lines, negation (``!``), directory-only rules (trailing ``/``), anchored
    rules (a leading or embedded ``/``) and ``*`` / ``?`` / ``**`` globs.
    Nested ``.gitignore`` files are not read.
    """

    def __init__(self, rules: Sequence[tuple[re.Pattern, bool, bool]] = ()):
        self._rules = list(rules)

    def __bool__(self) -> bool:
        return bool(self._rules)

    @classmethod
    def load(cls, root: Path) -> "GitIgnore":
        path = Path(root) / ".gitignore"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return cls()

        rules = []
        for line in lines:
            rule = _compile_rule(line)
            if rule is not None:
                rules.append(rule)
        return cls(rules)

    def ignored(self, rel_path: str, is_dir: bool) -> bool:
        """Test a root-relative POSIX path. Later rules win, as git does."""
        result = False
        for pattern, negated, dir_only in self._rules:
            if dir_only and not is_dir:
                continue
            if pattern.match(rel_path):
                result = not negated
        return result


def _compile_rule(line: str) -> Optional[tuple[re.Pattern, bool, bool]]:
    line = line.rstrip()
    if not line or line.lstrip().startswith("#"):
        return None

    negated = line.startswith("!")
    if negated:
        line = line[1:]
    if line.startswith("\\"):  # escaped leading '!' or '#'
        line = line[1:]

    dir_only = line.endswith("/")
    pattern = line.rstrip("/")
    if not pattern:
        return None

    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    pattern = pattern.lstrip("/")
    if pattern.startswith("**/"):
        pattern, anchored = pattern[3:], False
    if not pattern:
        return None

    prefix = "" if anchored else r"(?:.*/)?"
    # Trailing (?:/.*)? so a matched directory also covers everything below it.
    return re.compile(f"^{prefix}{_translate(pattern)}(?:/.*)?$"), negated, dir_only


def _translate(pattern: str) -> str:
    """Translate gitignore glob syntax to a regex body."""
    out = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                if pattern.startswith("/", i):  # "**/" spans zero or more dirs
                    out.append("/?")
                    i += 1
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(r"\[")
            else:
                body = pattern[i + 1 : end].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end + 1
                continue
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def walk_source_files(
    root: str | Path,
    extensions: Iterable[str],
    exclude_dirs: Optional[Iterable[str]] = None,
    exclude_paths: Optional[Iterable[str]] = None,
    respect_gitignore: bool = True,
    follow_symlinks: bool = False,
) -> Iterator[Path]:
    """Yield source files under ``root``, pruning excluded directories.

    Args:
        root: Directory to walk.
        extensions: Extensions to match, with or without a leading dot.
        exclude_dirs: Directory names skipped at any depth. Defaults to
            ``DEFAULT_EXCLUDED_DIRS``; pass an empty iterable to disable.
        exclude_paths: Paths skipped, relative to ``root`` (absolute paths
            under ``root`` are accepted too).
        respect_gitignore: Honour ``root/.gitignore`` when present.
        follow_symlinks: Descend into symlinked directories. Off by default,
            since symlink cycles make the walk unbounded.

    Yields:
        Paths in sorted order, so repeated runs index files identically.
    """
    root = Path(root)
    suffixes = tuple(f".{str(ext).lstrip('.')}" for ext in extensions)
    if not suffixes:
        return

    excluded = DEFAULT_EXCLUDED_DIRS if exclude_dirs is None else frozenset(exclude_dirs)
    excluded_rel = _normalize_exclude_paths(root, exclude_paths)
    gitignore = GitIgnore.load(root) if respect_gitignore else GitIgnore()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        rel_dir = os.path.relpath(dirpath, root)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"

        dirnames[:] = sorted(
            name
            for name in dirnames
            if not _skip_dir(name, prefix + name, excluded, excluded_rel, gitignore)
        )

        for name in sorted(filenames):
            if not name.endswith(suffixes):
                continue
            rel = prefix + name
            if _under_any(rel, excluded_rel):
                continue
            if gitignore and gitignore.ignored(rel, is_dir=False):
                continue
            yield Path(dirpath) / name


def _skip_dir(
    name: str,
    rel: str,
    excluded: frozenset[str],
    excluded_rel: tuple[str, ...],
    gitignore: GitIgnore,
) -> bool:
    if name in excluded or name.endswith(DEFAULT_EXCLUDED_DIR_SUFFIXES):
        return True
    if _under_any(rel, excluded_rel):
        return True
    return bool(gitignore) and gitignore.ignored(rel, is_dir=True)


def _under_any(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


def _normalize_exclude_paths(root: Path, exclude_paths: Optional[Iterable[str]]) -> tuple[str, ...]:
    """Reduce exclude paths to root-relative POSIX prefixes."""
    normalized = []
    root_abs = None
    for raw in exclude_paths or ():
        path = Path(raw)
        if path.is_absolute():
            # Resolve both sides, or a symlinked root (/tmp on macOS) never matches.
            if root_abs is None:
                root_abs = root.resolve()
            try:
                text = path.resolve().relative_to(root_abs).as_posix()
            except ValueError:
                continue  # outside the tree — nothing to prune
        else:
            text = path.as_posix()
        text = text.strip("/")
        if text and text != ".":
            normalized.append(text)
    return tuple(normalized)
