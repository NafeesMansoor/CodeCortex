"""The safe update process: backup → install → migrate → verify → commit or roll back.

The ordering here is the whole point. Nothing is installed before the current
state is recorded, and nothing is declared successful before a *fresh
interpreter* confirms the new code imports and runs. If verification fails the
previous version is restored, so an installation is never knowingly left half
updated.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from codecortex.installation import (
    Backup,
    Installation,
    InstallationUpdater,
    UpdateError,
    create_backup,
    detect_installation,
    updater_for,
)
from codecortex.updater import (
    DEFAULT_TIMEOUT,
    Release,
    UpdateInfo,
    check_for_updates,
    latest_release,
)
from codecortex.version import DEFAULT_CHANNEL, current

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 300

StepCallback = Callable[[str, bool, str], None]


@dataclass
class UpgradeResult:
    ok: bool = False
    from_version: str = ""
    to_version: Optional[str] = None
    steps: list[tuple[str, bool, str]] = field(default_factory=list)
    rolled_back: bool = False
    backup_path: Optional[str] = None
    message: str = ""

    def add(self, label: str, ok: bool, detail: str = "") -> None:
        self.steps.append((label, ok, detail))


def _health_check_subprocess(cwd: Optional[Path]) -> tuple[bool, str]:
    """Run `codecortex health --json` in a fresh interpreter and parse the verdict."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "codecortex", "health", "--json"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=HEALTH_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"health check could not run: {exc}"

    try:
        report = json.loads(completed.stdout)
    except ValueError:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return False, detail[-1] if detail else "health check produced no output"

    if report.get("status") == "HEALTHY":
        return True, f"verified {report.get('version', 'unknown')}"

    failures = [
        c["name"] for c in report.get("checks", []) if not c["ok"] and c["severity"] == "error"
    ]
    return False, ("failed: " + ", ".join(failures)) if failures else "reported UNHEALTHY"


def _migrate(index_dir: Optional[Path]) -> tuple[bool, str]:
    if index_dir is None:
        return True, "no index directory given — caches rebuild on next index"

    from codecortex.migrations import discover, run

    steps = run(discover(index_dir))
    blocked = [s for s in steps if s.action == "blocked"]
    rebuilt = [s for s in steps if s.action == "rebuild"]
    if blocked:
        return False, "; ".join(f"{s.store}: {s.detail}" for s in blocked)
    if rebuilt:
        return True, f"rebuilt {len(rebuilt)} stale cache(s): " + ", ".join(
            s.store for s in rebuilt
        )
    return True, f"{len(steps)} cache(s) already current"


def plan_upgrade(
    channel: str = DEFAULT_CHANNEL,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    target: Optional[str] = None,
) -> tuple[Installation, UpdateInfo, Optional[Release]]:
    """Gather everything needed to decide on an upgrade, without changing anything."""
    installation = detect_installation()
    info = check_for_updates(channel=channel, timeout=timeout, force=True)

    release: Optional[Release] = None
    if info.checked_successfully:
        try:
            release = latest_release(channel=channel, timeout=timeout)
        except Exception as exc:  # already reported through info.error paths
            logger.debug("could not re-fetch release: %s", exc)

    if target and release is not None and release.tag != target and str(release.version) != target:
        release = None
        info.error = f"requested version {target} is not the latest release on channel {channel}"

    return installation, info, release


def perform_upgrade(
    installation: Installation,
    release: Release,
    *,
    index_dir: Optional[Path] = None,
    on_step: Optional[StepCallback] = None,
) -> UpgradeResult:
    """Execute the update. Rolls back on any failure after installation begins."""
    result = UpgradeResult(from_version=str(current()), to_version=str(release.version))

    def step(label: str, ok: bool, detail: str = "") -> None:
        result.add(label, ok, detail)
        if on_step:
            on_step(label, ok, detail)

    updater = updater_for(installation)

    try:
        updater.preflight()
        step("Preflight checks", True, installation.describe())
    except UpdateError as exc:
        step("Preflight checks", False, str(exc))
        result.message = str(exc)
        return result

    try:
        backup = create_backup(installation)
        result.backup_path = str(backup.path)
        step("Backup created", True, str(backup.path))
    except OSError as exc:
        step("Backup created", False, str(exc))
        result.message = f"refusing to update without a backup: {exc}"
        return result

    try:
        updater.install(release)
        step("Release installed", True, release.tag)
    except UpdateError as exc:
        step("Release installed", False, str(exc))
        # Installation failed before anything was replaced in the common case,
        # but roll back anyway so a partially applied checkout cannot survive.
        return _rollback(result, updater, backup, step, str(exc))

    migrated, detail = _migrate(index_dir)
    step("Database migration", migrated, detail)
    if not migrated:
        return _rollback(result, updater, backup, step, detail)

    healthy, detail = _health_check_subprocess(installation.root)
    step("Health check", healthy, detail)
    if not healthy:
        return _rollback(result, updater, backup, step, detail)

    result.ok = True
    result.message = f"CodeCortex successfully updated to {release.version}"
    return result


def _rollback(
    result: UpgradeResult,
    updater: InstallationUpdater,
    backup: Backup,
    step: StepCallback,
    reason: str,
) -> UpgradeResult:
    result.message = reason
    try:
        updater.rollback(backup)
    except UpdateError as exc:
        step("Rollback", False, str(exc))
        result.message = (
            f"{reason}; rollback also failed: {exc}. Restore manually from {backup.path}."
        )
        return result

    result.rolled_back = True
    healthy, detail = _health_check_subprocess(updater.installation.root)
    step("Rollback", True, f"restored {backup.version} ({detail})")
    if not healthy:
        result.message = (
            f"{reason}; rolled back to {backup.version} but it is not healthy: {detail}"
        )
    return result


def rollback_to_backup(
    backup: Optional[Backup] = None,
    *,
    on_step: Optional[StepCallback] = None,
) -> UpgradeResult:
    """Restore the most recent backup on request, outside of a failed update."""
    from codecortex.installation import latest_backup

    backup = backup or latest_backup()
    result = UpgradeResult(from_version=str(current()))
    if backup is None:
        result.message = "no backup found"
        return result

    installation = detect_installation()
    updater = updater_for(installation)

    def step(label: str, ok: bool, detail: str = "") -> None:
        result.add(label, ok, detail)
        if on_step:
            on_step(label, ok, detail)

    result.to_version = backup.version
    result.backup_path = str(backup.path)
    try:
        updater.rollback(backup)
    except UpdateError as exc:
        step("Rollback", False, str(exc))
        result.message = str(exc)
        return result

    healthy, detail = _health_check_subprocess(installation.root)
    step("Rollback", True, f"restored {backup.version}")
    step("Health check", healthy, detail)
    result.ok = healthy
    result.rolled_back = True
    result.message = (
        f"Restored CodeCortex {backup.version}"
        if healthy
        else f"Restored {backup.version} but health check failed: {detail}"
    )
    return result
