"""Installation-model detection and the update strategies built on it.

CodeCortex ships in two shapes that can update themselves — a git checkout
(`git clone` + `pip install -e .`) and a plain package install (`pip install`
from a release artifact) — plus a third shape that cannot: a vendored or
system-managed copy. Guessing wrong here is how an updater destroys someone's
working tree, so detection is explicit and anything unrecognised refuses to act
and prints manual instructions instead.

Both strategies install from an immutable git tag or a release asset and drive
the standard installers (`git`, `pip`) as subprocesses. Nothing is downloaded
and executed directly.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from codecortex.version import SemVer, current

logger = logging.getLogger(__name__)

GIT_TIMEOUT = 120
PIP_TIMEOUT = 900

# Config files worth preserving across an update. Secrets (.env and friends) are
# intentionally excluded: they are gitignored, so no update path touches them,
# and copying them into a backup directory would spread them further.
CONFIG_FILES = ("codecortex.yaml",)


class UpdateError(RuntimeError):
    """An update step failed; the caller should roll back."""


@dataclass
class Installation:
    kind: str  # "git" | "pip" | "unknown"
    package_dir: Path
    root: Optional[Path] = None  # git work tree, or install prefix
    editable: bool = False
    git_ref: Optional[str] = None
    git_commit: Optional[str] = None
    dirty: bool = False
    can_self_update: bool = False
    reason: str = ""

    def describe(self) -> str:
        if self.kind == "git":
            editable = ", editable" if self.editable else ""
            ref = self.git_ref or self.git_commit or "unknown ref"
            return f"git checkout at {self.root} ({ref}{editable})"
        if self.kind == "pip":
            return f"pip package at {self.package_dir.parent}"
        return f"unrecognised installation at {self.package_dir}"


def _run(
    args: list[str], cwd: Optional[Path] = None, timeout: int = GIT_TIMEOUT
) -> subprocess.CompletedProcess:
    logger.debug("running %s", " ".join(args))
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd=cwd, timeout=timeout)


def _git_available() -> bool:
    return shutil.which("git") is not None


def _is_editable_install() -> bool:
    """True when the import path points at a source tree rather than site-packages."""
    try:
        from importlib.metadata import PackageNotFoundError, distribution
    except ImportError:  # pragma: no cover
        return False
    try:
        dist = distribution("codecortex")
    except PackageNotFoundError:
        return False
    try:
        raw = dist.read_text("direct_url.json")
    except OSError:
        return False
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except ValueError:
        return False
    return bool(info.get("dir_info", {}).get("editable"))


def detect_installation() -> Installation:
    """Work out how this CodeCortex was installed."""
    import codecortex

    package_dir = Path(codecortex.__file__).resolve().parent
    editable = _is_editable_install()

    if _git_available():
        result = _git(["rev-parse", "--show-toplevel"], cwd=package_dir)
        if result.returncode == 0:
            root = Path(result.stdout.strip())
            if root.exists() and (root / "pyproject.toml").exists():
                status = _git(["status", "--porcelain"], cwd=root)
                describe = _git(["describe", "--tags", "--exact-match"], cwd=root)
                branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
                commit = _git(["rev-parse", "HEAD"], cwd=root)
                dirty = bool(status.stdout.strip())
                return Installation(
                    kind="git",
                    package_dir=package_dir,
                    root=root,
                    editable=editable,
                    git_ref=(describe.stdout.strip() if describe.returncode == 0 else None)
                    or (branch.stdout.strip() if branch.returncode == 0 else None),
                    git_commit=commit.stdout.strip() if commit.returncode == 0 else None,
                    dirty=dirty,
                    can_self_update=not dirty,
                    reason="working tree has uncommitted changes" if dirty else "",
                )

    if "site-packages" in package_dir.parts or "dist-packages" in package_dir.parts:
        return Installation(
            kind="pip",
            package_dir=package_dir,
            root=package_dir.parent,
            editable=editable,
            can_self_update=True,
        )

    return Installation(
        kind="unknown",
        package_dir=package_dir,
        can_self_update=False,
        reason="not a git checkout and not installed into site-packages",
    )


# ----------------------------------------------------------------------
# Backups
# ----------------------------------------------------------------------


@dataclass
class Backup:
    """Everything needed to put an installation back the way it was."""

    path: Path
    kind: str
    version: str
    created_at: str
    git_commit: Optional[str] = None
    git_ref: Optional[str] = None
    editable: bool = False
    config_files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "kind": self.kind,
            "version": self.version,
            "created_at": self.created_at,
            "git_commit": self.git_commit,
            "git_ref": self.git_ref,
            "editable": self.editable,
            "config_files": self.config_files,
        }
        return data

    @classmethod
    def load(cls, path: Path) -> Backup:
        data = json.loads((path / "manifest.json").read_text())
        return cls(
            path=path,
            kind=data["kind"],
            version=data["version"],
            created_at=data["created_at"],
            git_commit=data.get("git_commit"),
            git_ref=data.get("git_ref"),
            editable=bool(data.get("editable")),
            config_files=data.get("config_files") or {},
        )


def backups_dir() -> Path:
    from codecortex.updater import state_dir

    return state_dir() / "backups"


def create_backup(installation: Installation) -> Backup:
    """Record the current state, and copy configuration out of harm's way."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = backups_dir() / f"{current()}-{stamp}"
    # Two updates in the same second must not overwrite each other's manifest.
    path, suffix = base, 1
    while path.exists():
        path = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    (path / "config").mkdir(parents=True, exist_ok=True)

    saved: dict[str, str] = {}
    if installation.root:
        for name in CONFIG_FILES:
            source = installation.root / name
            if source.is_file():
                shutil.copy2(source, path / "config" / name)
                saved[name] = str(source)

    backup = Backup(
        path=path,
        kind=installation.kind,
        version=str(current()),
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        git_commit=installation.git_commit,
        git_ref=installation.git_ref,
        editable=installation.editable,
        config_files=saved,
    )
    (path / "manifest.json").write_text(json.dumps(backup.to_dict(), indent=2) + "\n")
    return backup


def latest_backup() -> Optional[Backup]:
    root = backups_dir()
    if not root.is_dir():
        return None
    candidates = sorted((p for p in root.iterdir() if (p / "manifest.json").is_file()))
    return Backup.load(candidates[-1]) if candidates else None


def restore_config(backup: Backup) -> list[str]:
    """Put backed-up config files back if an update replaced them."""
    restored = []
    for name, original in backup.config_files.items():
        saved = backup.path / "config" / name
        if saved.is_file():
            shutil.copy2(saved, original)
            restored.append(original)
    return restored


# ----------------------------------------------------------------------
# Strategies
# ----------------------------------------------------------------------


class InstallationUpdater:
    """Common interface for the per-model update strategies."""

    kind = "base"

    def __init__(self, installation: Installation):
        self.installation = installation

    def preflight(self) -> None:
        """Raise UpdateError if this installation cannot be updated safely."""
        raise NotImplementedError

    def install(self, release) -> None:
        raise NotImplementedError

    def rollback(self, backup: Backup) -> None:
        raise NotImplementedError


def _pip_install(args: list[str]) -> None:
    result = _run([sys.executable, "-m", "pip", "install", *args], timeout=PIP_TIMEOUT)
    if result.returncode != 0:
        raise UpdateError(f"pip install failed: {result.stderr.strip() or result.stdout.strip()}")


class GitInstallationUpdater(InstallationUpdater):
    """Update a git checkout by moving to an immutable release tag."""

    kind = "git"

    def preflight(self) -> None:
        if not _git_available():
            raise UpdateError("git is not on PATH")
        root = self.installation.root
        if root is None:
            raise UpdateError("no git work tree found")
        if self.installation.dirty:
            raise UpdateError(
                "the working tree has uncommitted changes — commit or stash them before updating"
            )

    def install(self, release) -> None:
        root = self.installation.root
        fetch = _git(["fetch", "--tags", "--prune", "origin"], cwd=root)
        if fetch.returncode != 0:
            raise UpdateError(f"git fetch failed: {fetch.stderr.strip()}")

        tag = release.tag
        exists = _git(["rev-parse", "--verify", f"refs/tags/{tag}"], cwd=root)
        if exists.returncode != 0:
            raise UpdateError(f"release tag {tag} not found after fetch")

        checkout = _git(["checkout", "--detach", f"refs/tags/{tag}"], cwd=root)
        if checkout.returncode != 0:
            raise UpdateError(f"git checkout {tag} failed: {checkout.stderr.strip()}")

        # Dependencies may have changed with the release; reinstall the project
        # itself only, never a blanket upgrade of unrelated packages.
        _pip_install(["-e", str(root)] if self.installation.editable else [str(root)])

    def rollback(self, backup: Backup) -> None:
        root = self.installation.root
        target = backup.git_commit
        if not target:
            raise UpdateError("backup does not record a git commit to return to")
        result = _git(["checkout", "--detach", target], cwd=root)
        if result.returncode != 0:
            raise UpdateError(f"rollback checkout failed: {result.stderr.strip()}")
        _pip_install(["-e", str(root)] if backup.editable else [str(root)])
        restore_config(backup)


class PipInstallationUpdater(InstallationUpdater):
    """Update a pip-installed package from the release's own artifact."""

    kind = "pip"

    def preflight(self) -> None:
        result = _run([sys.executable, "-m", "pip", "--version"], timeout=60)
        if result.returncode != 0:
            raise UpdateError("pip is not available for this interpreter")

    @staticmethod
    def _source_for(release) -> str:
        from codecortex.updater import repo_slug

        wheels = [url for name, url in release.assets.items() if name.endswith(".whl")]
        if wheels:
            return wheels[0]
        sdists = [url for name, url in release.assets.items() if name.endswith(".tar.gz")]
        if sdists:
            return sdists[0]
        return f"codecortex @ git+https://github.com/{repo_slug()}.git@{release.tag}"

    def install(self, release) -> None:
        # --upgrade without --upgrade-strategy eager: only CodeCortex moves,
        # unrelated dependencies stay where the user pinned them.
        _pip_install(["--upgrade", self._source_for(release)])

    def rollback(self, backup: Backup) -> None:
        from codecortex.updater import repo_slug

        version = SemVer.try_parse(backup.version)
        if version is None:
            raise UpdateError(f"backup records an unusable version: {backup.version!r}")
        _pip_install(
            ["--upgrade", f"codecortex @ git+https://github.com/{repo_slug()}.git@v{version}"]
        )
        restore_config(backup)


def updater_for(installation: Installation) -> InstallationUpdater:
    if installation.kind == "git":
        return GitInstallationUpdater(installation)
    if installation.kind == "pip":
        return PipInstallationUpdater(installation)
    raise UpdateError(
        f"cannot update this installation automatically: {installation.reason or installation.kind}"
    )
