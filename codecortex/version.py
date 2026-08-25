"""Authoritative version source and semantic-version handling.

`__version__` below is the single source of truth: `pyproject.toml` reads it
statically via `[tool.setuptools.dynamic]`, so packaging metadata and the
running code can never drift. Nothing else in the tree should hold a literal
version string.

Keep the `__version__` assignment a plain literal — setuptools parses this file
with the AST rather than importing it, and an expression would break the build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

__version__ = "1.2.0"

# Installations older than this cannot upgrade in a single step; the updater
# reports a mandatory staged upgrade instead. Raise it only on a release that
# genuinely breaks the upgrade path.
MINIMUM_SUPPORTED_VERSION = "1.0.0"

# Release channels, loosest constraint last.
CHANNELS = ("stable", "beta", "dev")
DEFAULT_CHANNEL = "stable"

_SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

Identifier = Union[int, str]


class InvalidVersionError(ValueError):
    """Raised when a string is not a valid semantic version."""


@dataclass(frozen=True)
class SemVer:
    """A semantic version, ordered per the SemVer 2.0.0 precedence rules."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[Identifier, ...] = ()
    build: str = field(default="", compare=False)

    @classmethod
    def parse(cls, raw: str) -> SemVer:
        match = _SEMVER_RE.match(raw.strip()) if raw else None
        if match is None:
            raise InvalidVersionError(f"not a semantic version: {raw!r}")

        pre = match.group("prerelease")
        identifiers: tuple[Identifier, ...] = ()
        if pre:
            identifiers = tuple(int(p) if p.isdigit() else p for p in pre.split("."))

        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=identifiers,
            build=match.group("build") or "",
        )

    @classmethod
    def try_parse(cls, raw: str) -> Optional[SemVer]:
        try:
            return cls.parse(raw)
        except InvalidVersionError:
            return None

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def release(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def bump_kind(self, other: SemVer) -> str:
        """Describe the step from self to other: major / minor / patch / none."""
        if other.major != self.major:
            return "major"
        if other.minor != self.minor:
            return "minor"
        if other.patch != self.patch or other.prerelease != self.prerelease:
            return "patch"
        return "none"

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(str(p) for p in self.prerelease)
        if self.build:
            text += "+" + self.build
        return text

    def __lt__(self, other: SemVer) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        if self.release != other.release:
            return self.release < other.release
        # A version with a prerelease tag precedes the matching release.
        if self.prerelease and not other.prerelease:
            return True
        if other.prerelease and not self.prerelease:
            return False
        return _compare_prerelease(self.prerelease, other.prerelease) < 0

    def __le__(self, other: SemVer) -> bool:
        return self == other or self < other

    def __gt__(self, other: SemVer) -> bool:
        return not self <= other

    def __ge__(self, other: SemVer) -> bool:
        return not self < other


def _compare_prerelease(left: tuple[Identifier, ...], right: tuple[Identifier, ...]) -> int:
    for a, b in zip(left, right):
        if a == b:
            continue
        # Numeric identifiers always rank lower than alphanumeric ones.
        if isinstance(a, int) and isinstance(b, int):
            return -1 if a < b else 1
        if isinstance(a, int):
            return -1
        if isinstance(b, int):
            return 1
        return -1 if a < b else 1
    # All shared identifiers equal — the longer set has higher precedence.
    return (len(left) > len(right)) - (len(left) < len(right))


def parse(raw: str) -> SemVer:
    return SemVer.parse(raw)


def current() -> SemVer:
    """The version of the CodeCortex code currently executing."""
    return SemVer.parse(__version__)


def minimum_supported() -> SemVer:
    return SemVer.parse(MINIMUM_SUPPORTED_VERSION)


def installed_metadata_version() -> Optional[str]:
    """Version recorded in the installed distribution metadata, if installed.

    This can legitimately differ from `__version__` — an editable install keeps
    the metadata written at `pip install` time — so the health check reports the
    mismatch rather than treating either value as wrong.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - stdlib since 3.8
        return None
    try:
        return version("codecortex")
    except PackageNotFoundError:
        return None


def channel_of(version: SemVer) -> str:
    """Classify a release: prerelease tags map to beta/dev, everything else stable."""
    if not version.is_prerelease:
        return "stable"
    first = str(version.prerelease[0]).lower()
    if first.startswith(("a", "b", "rc")):
        return "beta"
    return "dev"


def channel_accepts(channel: str, version: SemVer) -> bool:
    """Whether an installation on `channel` may upgrade to `version`.

    stable never accepts a prerelease, so a production box cannot drift onto a
    development build by accident.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel: {channel!r} (expected one of {', '.join(CHANNELS)})")
    if channel == "stable":
        return not version.is_prerelease
    if channel == "beta":
        return channel_of(version) in ("stable", "beta")
    return True
