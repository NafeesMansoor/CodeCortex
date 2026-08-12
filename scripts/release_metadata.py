#!/usr/bin/env python3
"""Generate update.json for a release, and verify the tag matches the code.

Run by the release workflow before publishing:

    python scripts/release_metadata.py --tag v1.2.0 --output dist/update.json

The metadata is derived from the source tree — never hand-written — so a release
cannot advertise a version the code does not actually carry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codecortex.migrations import schema_versions  # noqa: E402
from codecortex.updater import DEFAULT_REPO  # noqa: E402
from codecortex.version import (  # noqa: E402
    MINIMUM_SUPPORTED_VERSION,
    SemVer,
    __version__,
    channel_of,
)


def build_metadata(tag: str | None, repo: str) -> dict:
    version = SemVer.parse(__version__)
    return {
        "name": "CodeCortex",
        "version": str(version),
        "channel": channel_of(version),
        "minimum_version": MINIMUM_SUPPORTED_VERSION,
        "release_url": f"https://github.com/{repo}/releases/tag/{tag or f'v{version}'}",
        "schema_versions": schema_versions(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=None, help="Git tag being released, e.g. v1.2.0")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name of the GitHub repository")
    parser.add_argument("--output", default=None, help="Write metadata here (default: stdout)")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only verify tag/version consistency, write nothing",
    )
    args = parser.parse_args(argv)

    if args.tag:
        tag_version = SemVer.try_parse(args.tag)
        if tag_version is None:
            print(f"ERROR: tag {args.tag!r} is not a semantic version", file=sys.stderr)
            return 1
        if str(tag_version) != __version__:
            print(
                f"ERROR: tag {args.tag} does not match codecortex.version.__version__ "
                f"({__version__}). Bump the version before tagging.",
                file=sys.stderr,
            )
            return 1

    metadata = build_metadata(args.tag, args.repo)
    if args.check_only:
        print(f"version {metadata['version']} consistent with tag {args.tag or '(none)'}")
        return 0

    payload = json.dumps(metadata, indent=2) + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)
        print(f"wrote {path}")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
