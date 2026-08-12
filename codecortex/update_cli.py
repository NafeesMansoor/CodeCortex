"""`codecortex version` and `codecortex update` command implementations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codecortex.installation import detect_installation, latest_backup
from codecortex.updater import (
    DEFAULT_TIMEOUT,
    check_for_updates,
    installation_metadata,
    repo_slug,
)
from codecortex.version import CHANNELS, DEFAULT_CHANNEL, __version__


def _channel_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--channel",
        choices=CHANNELS,
        default=DEFAULT_CHANNEL,
        help=f"Release channel to follow (default: {DEFAULT_CHANNEL})",
    )


def version_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codecortex version",
        description="Show the installed CodeCortex version and update status.",
    )
    parser.add_argument(
        "--check", action="store_true", help="Query the release service for a newer version"
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Network timeout in seconds"
    )
    _channel_arg(parser)
    args = parser.parse_args(argv)

    if not args.check:
        metadata = installation_metadata(channel=args.channel)
        if args.json:
            print(json.dumps(metadata, indent=2))
        else:
            print(f"CodeCortex {__version__}")
            print(f"  Installation : {detect_installation().describe()}")
            print(f"  Channel      : {args.channel}")
            if metadata["last_update_check"]:
                print(f"  Last check   : {metadata['last_update_check']}")
                print(f"  Latest known : {metadata['latest_version'] or 'unknown'}")
        return 0

    info = check_for_updates(channel=args.channel, timeout=args.timeout, force=True)
    if args.json:
        print(json.dumps(info.to_dict(), indent=2))
        return 0

    print(f"CodeCortex {info.current_version}")
    if info.error:
        # Offline is a normal state, not a failure of the program.
        print(f"\nUnable to check for updates: {info.error}")
        print("CodeCortex will continue normally.")
        return 0
    if not info.update_available:
        print(f"Up to date (channel: {info.channel})")
        return 0

    print(
        f"\nUpdate available: {info.current_version} → {info.latest_version} ({info.update_kind})"
    )
    if info.mandatory_update:
        print(f"This installation is below the minimum supported version {info.minimum_version}.")
    if info.schema_change:
        print("This release changes the index cache schema; caches rebuild on the next index.")
    if info.release_url:
        print(f"Release notes: {info.release_url}")
    print("\nRun:\ncodecortex update")
    return 0


def _print_step(label: str, ok: bool, detail: str) -> None:
    mark = "✓" if ok else "✗"
    line = f"  {mark} {label}"
    if detail:
        line += f" — {detail}"
    print(line)


def update_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codecortex update",
        description="Update this CodeCortex installation to the latest release.",
    )
    parser.add_argument("--check", action="store_true", help="Report what would happen and exit")
    parser.add_argument("--yes", "-y", action="store_true", help="Do not prompt for confirmation")
    parser.add_argument("--rollback", action="store_true", help="Restore the most recent backup")
    parser.add_argument(
        "--index-dir",
        metavar="DIR",
        default=None,
        help="Index cache directory to migrate as part of the update",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Network timeout in seconds"
    )
    _channel_arg(parser)
    args = parser.parse_args(argv)

    from codecortex.upgrade import perform_upgrade, plan_upgrade, rollback_to_backup

    if args.rollback:
        backup = latest_backup()
        if backup is None:
            print("No backup found — nothing to roll back to.", file=sys.stderr)
            return 1
        if not args.yes and not _confirm(
            f"Restore CodeCortex {backup.version} from {backup.path}?"
        ):
            print("Cancelled.")
            return 1
        result = rollback_to_backup(backup, on_step=_print_step)
        print(f"\n{result.message}")
        return 0 if result.ok else 1

    installation, info, release = plan_upgrade(channel=args.channel, timeout=args.timeout)

    print(f"CodeCortex {info.current_version}")
    print(f"  Installation : {installation.describe()}")

    if info.error:
        print(f"\nUnable to check for updates: {info.error}")
        print("CodeCortex will continue normally.")
        return 0

    if not info.update_available or release is None:
        print(f"\nAlready on the latest {args.channel} release ({info.latest_version}).")
        return 0

    print(f"\nUpdate available:\n{info.current_version} → {info.latest_version}")
    if info.mandatory_update:
        print(f"\nRequired: this version is below the minimum supported {info.minimum_version}.")
    if info.schema_change:
        print("Note: index cache schema changes in this release; stale caches are rebuilt.")

    if not installation.can_self_update:
        print(f"\nThis installation cannot update itself: {installation.reason}")
        print(
            f"Update manually, for example:\n  pip install --upgrade "
            f"'codecortex @ git+https://github.com/{repo_slug()}.git@{release.tag}'"
        )
        return 1

    if args.check:
        print("\n--check given; no changes made.")
        return 0

    if not args.yes and not _confirm(f"\nUpdate to {info.latest_version} now?"):
        print("Cancelled.")
        return 1

    print("\nPreparing update...")
    result = perform_upgrade(
        installation,
        release,
        index_dir=Path(args.index_dir) if args.index_dir else None,
        on_step=_print_step,
    )
    print(f"\n{result.message}")
    if result.backup_path and not result.ok:
        print(f"Backup kept at: {result.backup_path}")
    return 0 if result.ok else 1


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        # Non-interactive callers must pass --yes explicitly; defaulting to
        # "yes" here would let a cron job update production silently.
        print(f"{question} (no TTY — pass --yes to proceed non-interactively)")
        return False
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")
