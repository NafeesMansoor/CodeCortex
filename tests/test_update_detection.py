"""Update detection: release parsing, validation, caching, and failure tolerance."""

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from codecortex import updater
from codecortex.version import SemVer, current

REPO = "NafeesMansoor/CodeCortex"


def release_json(tag, *, prerelease=False, draft=False, assets=None):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
        "published_at": "2026-08-12T00:00:00Z",
        "prerelease": prerelease,
        "draft": draft,
        "assets": [
            {"name": name, "browser_download_url": url} for name, url in (assets or {}).items()
        ],
    }


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep every test off the network and out of the real ~/.codecortex."""
    monkeypatch.setenv("CODECORTEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("CODECORTEX_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(
        updater,
        "_get_json",
        lambda url, timeout: pytest.fail(f"unexpected network call to {url}"),
    )


def serve(monkeypatch, routes):
    """Route URL substrings to canned JSON payloads.

    Longest fragment wins — an asset URL contains "/releases" too, so the more
    specific route has to be tried first.
    """
    ordered = sorted(routes.items(), key=lambda item: len(item[0]), reverse=True)

    def fake_get(url, timeout):
        for fragment, payload in ordered:
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise urllib.error.URLError(f"no route for {url}")

    monkeypatch.setattr(updater, "_get_json", fake_get)


class TestURLValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "http://api.github.com/repos/x/releases",  # not HTTPS
            "https://evil.example.com/releases",  # unknown host
            "ftp://github.com/x",
            "https://api.github.com.evil.com/x",
        ],
    )
    def test_untrusted_urls_rejected(self, url):
        assert not updater._host_allowed(url)

    def test_github_hosts_allowed(self):
        assert updater._host_allowed("https://api.github.com/repos/x/releases")
        assert updater._host_allowed("https://objects.githubusercontent.com/asset.whl")

    def test_get_json_refuses_untrusted_url(self, monkeypatch):
        monkeypatch.undo()  # drop the autouse guard for this direct call
        with pytest.raises(ValueError, match="untrusted"):
            updater._get_json("http://example.com/x", timeout=1)


class TestReleaseParsing:
    def test_valid_release(self):
        release = updater._parse_release(release_json("v1.4.2"))
        assert release.version == SemVer.parse("1.4.2")
        assert release.tag == "v1.4.2"

    def test_draft_skipped(self):
        assert updater._parse_release(release_json("v1.4.2", draft=True)) is None

    @pytest.mark.parametrize("tag", ["latest", "", "release-2", "v1.2"])
    def test_unparseable_tag_skipped(self, tag):
        assert updater._parse_release(release_json(tag)) is None

    def test_non_dict_payload_skipped(self):
        assert updater._parse_release("not-a-release") is None
        assert updater._parse_release(None) is None

    def test_release_on_foreign_host_skipped(self):
        raw = release_json("v1.4.2")
        raw["html_url"] = "https://evil.example.com/releases/tag/v1.4.2"
        assert updater._parse_release(raw) is None

    def test_assets_on_foreign_hosts_dropped(self):
        raw = release_json(
            "v1.4.2",
            assets={
                "good.whl": f"https://github.com/{REPO}/releases/download/v1.4.2/good.whl",
                "bad.whl": "https://evil.example.com/bad.whl",
            },
        )
        release = updater._parse_release(raw)
        assert set(release.assets) == {"good.whl"}


class TestReleaseMetadata:
    def _release(self):
        return updater._parse_release(release_json("v1.4.2"))

    def test_valid_metadata_applied(self):
        release = self._release()
        updater._apply_metadata(
            release,
            {"version": "1.4.2", "minimum_version": "1.3.0", "schema_versions": {"graph": 2}},
        )
        assert release.minimum_version == SemVer.parse("1.3.0")
        assert release.schema_versions == {"graph": 2}

    def test_metadata_for_a_different_version_ignored(self):
        release = self._release()
        updater._apply_metadata(release, {"version": "9.9.9", "minimum_version": "9.0.0"})
        assert release.minimum_version is None

    def test_minimum_above_release_ignored(self):
        """A minimum newer than the release itself would make every install mandatory."""
        release = self._release()
        updater._apply_metadata(release, {"version": "1.4.2", "minimum_version": "5.0.0"})
        assert release.minimum_version is None

    @pytest.mark.parametrize("payload", ["", [], None, {"version": "junk"}, {"nope": 1}])
    def test_malformed_metadata_ignored(self, payload):
        release = self._release()
        updater._apply_metadata(release, payload)
        assert release.minimum_version is None
        assert release.schema_versions == {}


class TestLatestRelease:
    def test_picks_highest_version_not_list_order(self, monkeypatch):
        serve(monkeypatch, {"/releases": [release_json("v1.2.0"), release_json("v1.10.0")]})
        assert str(updater.latest_release(repo=REPO).version) == "1.10.0"

    def test_stable_channel_skips_prerelease(self, monkeypatch):
        serve(
            monkeypatch,
            {"/releases": [release_json("v2.0.0-rc.1", prerelease=True), release_json("v1.9.0")]},
        )
        assert str(updater.latest_release("stable", repo=REPO).version) == "1.9.0"
        assert str(updater.latest_release("dev", repo=REPO).version) == "2.0.0-rc.1"

    def test_falls_back_to_tags_when_no_releases(self, monkeypatch):
        serve(monkeypatch, {"/releases": [], "/tags": [{"name": "v1.5.0"}, {"name": "junk"}]})
        release = updater.latest_release(repo=REPO)
        assert str(release.version) == "1.5.0"
        assert release.assets == {}

    def test_tag_fallback_can_be_disabled(self, monkeypatch):
        serve(monkeypatch, {"/releases": [], "/tags": [{"name": "v1.5.0"}]})
        assert updater.latest_release(repo=REPO, allow_tag_fallback=False) is None

    def test_metadata_asset_failure_does_not_fail_the_check(self, monkeypatch):
        url = f"https://github.com/{REPO}/releases/download/v1.4.2/update.json"
        serve(
            monkeypatch,
            {
                "/releases": [release_json("v1.4.2", assets={"update.json": url})],
                "update.json": urllib.error.URLError("boom"),
            },
        )
        assert str(updater.latest_release(repo=REPO).version) == "1.4.2"

    def test_malformed_release_list_raises(self, monkeypatch):
        serve(monkeypatch, {"/releases": {"not": "a list"}})
        with pytest.raises(ValueError):
            updater.fetch_releases(repo=REPO)


class TestCheckForUpdates:
    def _check(self, monkeypatch, tag, **kwargs):
        serve(monkeypatch, {"/releases": [release_json(tag)]})
        return updater.check_for_updates(repo=REPO, force=True, **kwargs)

    def test_no_update_when_current_is_latest(self, monkeypatch):
        info = self._check(monkeypatch, f"v{current()}")
        assert not info.update_available
        assert info.update_kind == "none"
        assert info.error is None

    def test_patch_minor_major_are_classified(self, monkeypatch):
        installed = current()
        cases = {
            f"{installed.major}.{installed.minor}.{installed.patch + 1}": "patch",
            f"{installed.major}.{installed.minor + 1}.0": "minor",
            f"{installed.major + 1}.0.0": "major",
        }
        for version, kind in cases.items():
            info = self._check(monkeypatch, f"v{version}")
            assert info.update_available
            assert info.update_kind == kind
            assert info.latest_version == version

    def test_older_release_is_not_an_update(self, monkeypatch):
        info = self._check(monkeypatch, "v0.0.1")
        assert not info.update_available

    def test_mandatory_when_below_minimum(self, monkeypatch):
        url = f"https://github.com/{REPO}/releases/download/v9.0.0/update.json"
        serve(
            monkeypatch,
            {
                "/releases": [release_json("v9.0.0", assets={"update.json": url})],
                "update.json": {"version": "9.0.0", "minimum_version": "8.0.0"},
            },
        )
        info = updater.check_for_updates(repo=REPO, force=True)
        assert info.update_available and info.mandatory_update
        assert info.minimum_version == "8.0.0"

    def test_not_mandatory_when_above_minimum(self, monkeypatch):
        url = f"https://github.com/{REPO}/releases/download/v9.0.0/update.json"
        serve(
            monkeypatch,
            {
                "/releases": [release_json("v9.0.0", assets={"update.json": url})],
                "update.json": {"version": "9.0.0", "minimum_version": "0.0.1"},
            },
        )
        assert not updater.check_for_updates(repo=REPO, force=True).mandatory_update

    def test_schema_change_detected(self, monkeypatch):
        url = f"https://github.com/{REPO}/releases/download/v9.0.0/update.json"
        serve(
            monkeypatch,
            {
                "/releases": [release_json("v9.0.0", assets={"update.json": url})],
                "update.json": {"version": "9.0.0", "schema_versions": {"graph": 99}},
            },
        )
        assert updater.check_for_updates(repo=REPO, force=True).schema_change

    def test_network_failure_is_reported_not_raised(self, monkeypatch):
        serve(monkeypatch, {"/releases": urllib.error.URLError("offline")})
        info = updater.check_for_updates(repo=REPO, force=True)
        assert info.error and "could not reach" in info.error
        assert not info.update_available
        assert info.current_version == str(current())

    def test_malformed_json_is_reported_not_raised(self, monkeypatch):
        serve(monkeypatch, {"/releases": json.JSONDecodeError("bad", "doc", 0)})
        assert updater.check_for_updates(repo=REPO, force=True).error is not None

    def test_no_matching_release_is_reported(self, monkeypatch):
        serve(monkeypatch, {"/releases": [], "/tags": []})
        info = updater.check_for_updates(repo=REPO, force=True)
        assert info.latest_version is None
        assert info.error

    def test_env_var_disables_checks(self, monkeypatch):
        monkeypatch.setenv("CODECORTEX_NO_UPDATE_CHECK", "1")
        info = updater.check_for_updates(repo=REPO)  # network guard would fail the test
        assert "disabled" in info.error


class TestCache:
    def test_result_is_cached_and_reused(self, monkeypatch):
        serve(monkeypatch, {"/releases": [release_json("v9.9.9")]})
        first = updater.check_for_updates(repo=REPO, force=True)
        assert not first.from_cache

        # No route now: a second check must be served from cache.
        monkeypatch.setattr(
            updater, "_get_json", lambda url, timeout: pytest.fail("should not refetch")
        )
        second = updater.check_for_updates(repo=REPO)
        assert second.from_cache
        assert second.latest_version == first.latest_version

    def test_stale_cache_is_refetched(self, monkeypatch):
        serve(monkeypatch, {"/releases": [release_json("v9.9.9")]})
        updater.check_for_updates(repo=REPO, force=True)
        serve(monkeypatch, {"/releases": [release_json("v9.9.10")]})
        info = updater.check_for_updates(repo=REPO, max_age=-1)
        assert info.latest_version == "9.9.10"
        assert not info.from_cache

    def test_corrupt_cache_is_ignored(self, monkeypatch):
        path = updater._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert updater.read_cache() is None

        serve(monkeypatch, {"/releases": [release_json("v9.9.9")]})
        assert updater.check_for_updates(repo=REPO).latest_version == "9.9.9"

    def test_unknown_cache_keys_do_not_break_load(self, monkeypatch):
        path = updater._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "current_version": str(current()),
                    "latest_version": "9.9.9",
                    "update_available": True,
                    "channel": "stable",
                    "checked_at": updater._now_iso(),
                    "field_from_a_future_release": "ignored",
                }
            )
        )
        info = updater.check_for_updates(repo=REPO)
        assert info.latest_version == "9.9.9" and info.from_cache


class TestNotification:
    def test_notice_rendered_from_cache_only(self, monkeypatch):
        serve(monkeypatch, {"/releases": [release_json("v9.9.9")]})
        updater.check_for_updates(repo=REPO, force=True)

        monkeypatch.setattr(
            updater, "_get_json", lambda url, timeout: pytest.fail("notification must not fetch")
        )
        notice = updater.cached_notification()
        assert "9.9.9" in notice and "codecortex update" in notice

    def test_no_notice_without_cache(self):
        assert updater.cached_notification() is None

    def test_no_notice_when_cached_version_is_not_newer(self, monkeypatch):
        serve(monkeypatch, {"/releases": [release_json("v0.0.1")]})
        updater.check_for_updates(repo=REPO, force=True)
        assert updater.cached_notification() is None

    def test_background_check_populates_cache(self, monkeypatch):
        serve(monkeypatch, {"/releases": [release_json("v9.9.9")]})
        thread = updater.check_in_background()
        thread.join(timeout=10)
        assert updater.read_cache()["latest_version"] == "9.9.9"

    def test_background_check_swallows_failure(self, monkeypatch):
        serve(monkeypatch, {"/releases": urllib.error.URLError("offline")})
        thread = updater.check_in_background()
        thread.join(timeout=10)
        assert not thread.is_alive()

    def test_background_check_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("CODECORTEX_NO_UPDATE_CHECK", "true")
        assert updater.check_in_background() is None


class TestInstallationMetadata:
    def test_reports_local_state_without_network(self):
        metadata = updater.installation_metadata()
        assert metadata["version"] == str(current())
        assert metadata["installation_type"] in ("git", "pip", "unknown")
        assert metadata["update_available"] is False
        assert "channel" in metadata

    def test_no_secrets_or_paths_beyond_install_root(self):
        keys = set(updater.installation_metadata())
        assert keys == {
            "version",
            "installation_type",
            "install_root",
            "channel",
            "last_update_check",
            "latest_version",
            "update_available",
        }
