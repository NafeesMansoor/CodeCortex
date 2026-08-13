"""Update detection against GitHub Releases.

Design constraints that shaped this module:

* An update check must never stop CodeCortex from working. Every network path
  here is wrapped, bounded by a timeout, and degrades to "unknown" — never to an
  exception reaching the caller.
* No new dependencies: `urllib.request` over HTTPS only.
* No credentials. The check reads a public release list and sends nothing about
  the installation, so there is no token to leak and no telemetry to opt out of.
* Release metadata arrives from the network and is therefore untrusted: tags
  must parse as semantic versions and every URL must live on a known GitHub
  host before it is shown to a user or handed to the installer.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from codecortex.version import (
    DEFAULT_CHANNEL,
    MINIMUM_SUPPORTED_VERSION,
    SemVer,
    channel_accepts,
    current,
)

logger = logging.getLogger(__name__)

DEFAULT_REPO = "NafeesMansoor/CodeCortex"
DEFAULT_TIMEOUT = 5.0
DEFAULT_MAX_AGE = 86_400  # re-check at most once a day
RELEASE_METADATA_ASSET = "update.json"

_ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "www.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


def state_dir() -> Path:
    """User-level directory for update state (not the per-project index cache)."""
    override = os.environ.get("CODECORTEX_STATE_DIR")
    return Path(override).expanduser() if override else Path.home() / ".codecortex"


def repo_slug() -> str:
    return os.environ.get("CODECORTEX_UPDATE_REPO", DEFAULT_REPO)


def updates_disabled() -> bool:
    """Deployment policy can switch off all outbound update checks."""
    return os.environ.get("CODECORTEX_NO_UPDATE_CHECK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@dataclass
class Release:
    """A validated GitHub release."""

    version: SemVer
    tag: str
    url: str
    published_at: str = ""
    prerelease: bool = False
    minimum_version: Optional[SemVer] = None
    schema_versions: dict[str, int] = field(default_factory=dict)
    assets: dict[str, str] = field(default_factory=dict)


@dataclass
class UpdateInfo:
    """Structured result of an update check."""

    current_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    mandatory_update: bool = False
    update_kind: str = "none"  # major | minor | patch | none
    channel: str = DEFAULT_CHANNEL
    release_url: Optional[str] = None
    minimum_version: Optional[str] = None
    schema_change: bool = False
    checked_at: str = ""
    from_cache: bool = False
    error: Optional[str] = None

    @property
    def checked_successfully(self) -> bool:
        return self.error is None and self.latest_version is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _host_allowed(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in _ALLOWED_HOSTS


def _get_json(url: str, timeout: float) -> Any:
    """HTTPS GET returning parsed JSON. Raises on any failure."""
    if not _host_allowed(url):
        raise ValueError(f"refusing to fetch non-HTTPS or untrusted URL: {url}")

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CodeCortex/{current()}",
        },
    )
    # Scheme and host are validated by _host_allowed above, so only HTTPS
    # requests to known GitHub hosts reach this call.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        payload = response.read(2_000_000)
    return json.loads(payload.decode("utf-8"))


def _parse_release(raw: Any) -> Optional[Release]:
    """Convert one GitHub API release object into a Release, or None if unusable."""
    if not isinstance(raw, dict) or raw.get("draft"):
        return None

    version = SemVer.try_parse(str(raw.get("tag_name") or ""))
    if version is None:
        return None

    url = str(raw.get("html_url") or "")
    if url and not _host_allowed(url):
        return None

    assets: dict[str, str] = {}
    for asset in raw.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        download = str(asset.get("browser_download_url") or "")
        if name and download and _host_allowed(download):
            assets[name] = download

    return Release(
        version=version,
        tag=str(raw.get("tag_name")),
        url=url,
        published_at=str(raw.get("published_at") or ""),
        prerelease=bool(raw.get("prerelease")),
        assets=assets,
    )


def _apply_metadata(release: Release, metadata: Any) -> None:
    """Merge a validated update.json payload into a Release.

    Metadata that disagrees with the release it came from is ignored rather than
    trusted — it is the one field an attacker could use to force a downgrade.
    """
    if not isinstance(metadata, dict):
        return

    declared = SemVer.try_parse(str(metadata.get("version") or ""))
    if declared is None or declared != release.version:
        logger.debug("ignoring update.json for %s: version mismatch", release.tag)
        return

    minimum = SemVer.try_parse(str(metadata.get("minimum_version") or ""))
    if minimum is not None and minimum <= release.version:
        release.minimum_version = minimum

    schema = metadata.get("schema_versions")
    if isinstance(schema, dict):
        release.schema_versions = {
            str(k): int(v)
            for k, v in schema.items()
            if isinstance(v, (int, str)) and str(v).isdigit()
        }


def fetch_releases(
    repo: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    limit: int = 30,
) -> list[Release]:
    """All valid releases for the repo, newest version first. Raises on network failure."""
    url = f"https://api.github.com/repos/{repo or repo_slug()}/releases?per_page={int(limit)}"
    payload = _get_json(url, timeout)
    if not isinstance(payload, list):
        raise ValueError("unexpected release payload: expected a list")

    releases = [r for r in (_parse_release(item) for item in payload) if r is not None]
    releases.sort(key=lambda r: r.version, reverse=True)
    return releases


def fetch_tags(
    repo: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    limit: int = 30,
) -> list[Release]:
    """Version tags as releases, newest first.

    Fallback for repositories that tag releases without publishing a GitHub
    Release. A tag carries no artifacts or metadata, so only the git strategy
    can act on one; the pip strategy installs from the tag over git+https.
    """
    slug = repo or repo_slug()
    payload = _get_json(f"https://api.github.com/repos/{slug}/tags?per_page={int(limit)}", timeout)
    if not isinstance(payload, list):
        raise ValueError("unexpected tag payload: expected a list")

    tags: list[Release] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        version = SemVer.try_parse(name)
        if version is None:
            continue
        tags.append(
            Release(
                version=version,
                tag=name,
                url=f"https://github.com/{slug}/releases/tag/{name}",
                prerelease=version.is_prerelease,
            )
        )
    tags.sort(key=lambda r: r.version, reverse=True)
    return tags


def latest_release(
    channel: str = DEFAULT_CHANNEL,
    repo: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    with_metadata: bool = True,
    allow_tag_fallback: bool = True,
) -> Optional[Release]:
    """Newest release the channel accepts, with update.json merged in when present."""
    candidates = fetch_releases(repo=repo, timeout=timeout)
    if not candidates and allow_tag_fallback:
        logger.debug("no published releases for %s — falling back to tags", repo or repo_slug())
        candidates = fetch_tags(repo=repo, timeout=timeout)

    for release in candidates:
        if not channel_accepts(channel, release.version):
            continue
        if with_metadata and RELEASE_METADATA_ASSET in release.assets:
            try:
                _apply_metadata(release, _get_json(release.assets[RELEASE_METADATA_ASSET], timeout))
            except Exception as exc:  # metadata is optional — never fail the check
                logger.debug("could not read %s: %s", RELEASE_METADATA_ASSET, exc)
        return release
    return None


# ----------------------------------------------------------------------
# Cached checking
# ----------------------------------------------------------------------


def _cache_path() -> Path:
    return state_dir() / "update-check.json"


def read_cache() -> Optional[dict[str, Any]]:
    path = _cache_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_cache(info: UpdateInfo) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info.to_dict(), indent=2) + "\n")
    except OSError as exc:
        logger.debug("could not write update cache: %s", exc)


def _cache_age(data: dict[str, Any]) -> float:
    stamp = data.get("checked_at")
    if not isinstance(stamp, str):
        return float("inf")
    try:
        checked = datetime.fromisoformat(stamp)
    except ValueError:
        return float("inf")
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - checked).total_seconds()


def _info_from_release(release: Optional[Release], channel: str) -> UpdateInfo:
    installed = current()
    info = UpdateInfo(
        current_version=str(installed),
        channel=channel,
        checked_at=_now_iso(),
        minimum_version=MINIMUM_SUPPORTED_VERSION,
    )
    if release is None:
        info.error = "no release matched this channel"
        return info

    info.latest_version = str(release.version)
    info.release_url = release.url or None
    info.update_available = release.version > installed
    info.update_kind = installed.bump_kind(release.version) if info.update_available else "none"

    if release.minimum_version is not None:
        info.minimum_version = str(release.minimum_version)
        # Installed is too old for the new release to support a direct upgrade.
        info.mandatory_update = installed < release.minimum_version

    if release.schema_versions:
        from codecortex.migrations import schema_versions

        local = schema_versions()
        info.schema_change = any(
            local.get(store, 0) != version for store, version in release.schema_versions.items()
        )

    return info


def check_for_updates(
    channel: str = DEFAULT_CHANNEL,
    *,
    repo: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    force: bool = False,
    max_age: float = DEFAULT_MAX_AGE,
    use_cache: bool = True,
) -> UpdateInfo:
    """Check for a newer release. Never raises — failures land in `.error`.

    A cached result younger than `max_age` is reused unless `force` is set, so
    repeated CLI calls do not hit the network on every invocation.
    """
    installed = current()

    if updates_disabled() and not force:
        return UpdateInfo(
            current_version=str(installed),
            channel=channel,
            checked_at=_now_iso(),
            error="update checks disabled by CODECORTEX_NO_UPDATE_CHECK",
        )

    if use_cache and not force:
        cached = read_cache()
        if (
            cached
            and cached.get("channel") == channel
            and cached.get("current_version") == str(installed)
            and _cache_age(cached) < max_age
        ):
            cached["from_cache"] = True
            known = {f for f in UpdateInfo.__dataclass_fields__}
            return UpdateInfo(**{k: v for k, v in cached.items() if k in known})

    try:
        release = latest_release(channel=channel, repo=repo, timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        # Offline, rate-limited, or malformed payload: report and carry on.
        return UpdateInfo(
            current_version=str(installed),
            channel=channel,
            checked_at=_now_iso(),
            error=f"could not reach the release service: {exc}",
        )

    info = _info_from_release(release, channel)
    if use_cache:
        write_cache(info)
    return info


def check_in_background(
    channel: str = DEFAULT_CHANNEL,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_age: float = DEFAULT_MAX_AGE,
) -> Optional[threading.Thread]:
    """Refresh the update cache on a daemon thread. Returns None when disabled.

    Daemon so it can never hold up interpreter exit, and silent so a failed
    check leaves no trace in normal output.
    """
    if updates_disabled():
        return None

    def _run() -> None:
        try:
            check_for_updates(channel=channel, timeout=timeout, max_age=max_age)
        except Exception as exc:  # a background check must never surface
            logger.debug("background update check failed: %s", exc)

    thread = threading.Thread(target=_run, name="codecortex-update-check", daemon=True)
    thread.start()
    return thread


def cached_notification(channel: str = DEFAULT_CHANNEL) -> Optional[str]:
    """A one-line 'update available' notice from cache only — never touches the network."""
    cached = read_cache()
    if not cached or cached.get("channel") != channel:
        return None
    if not cached.get("update_available"):
        return None

    latest = cached.get("latest_version")
    if not isinstance(latest, str) or SemVer.try_parse(latest) is None:
        return None
    if SemVer.parse(latest) <= current():
        return None

    urgency = "Required update" if cached.get("mandatory_update") else "Update available"
    return f"CodeCortex {current()}\n{urgency}: {latest}\n\nRun:\ncodecortex update"


def installation_metadata(channel: str = DEFAULT_CHANNEL) -> dict[str, Any]:
    """Locally-known installation state. Reads cache only; sends nothing."""
    from codecortex.installation import detect_installation

    installation = detect_installation()
    cached = read_cache() or {}
    return {
        "version": str(current()),
        "installation_type": installation.kind,
        "install_root": str(installation.root) if installation.root else None,
        "channel": channel,
        "last_update_check": cached.get("checked_at"),
        "latest_version": cached.get("latest_version"),
        "update_available": bool(cached.get("update_available", False)),
    }
