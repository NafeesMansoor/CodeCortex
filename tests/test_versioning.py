"""Semantic version parsing, ordering, channels, and consumer compatibility."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from codecortex.compat import IncompatibleVersionError, is_compatible, require_version
from codecortex.version import (
    CHANNELS,
    MINIMUM_SUPPORTED_VERSION,
    InvalidVersionError,
    SemVer,
    channel_accepts,
    channel_of,
    current,
)


class TestSemVerParsing:
    def test_plain_version(self):
        v = SemVer.parse("1.4.2")
        assert (v.major, v.minor, v.patch) == (1, 4, 2)
        assert not v.is_prerelease

    def test_leading_v_is_accepted(self):
        assert SemVer.parse("v2.0.0") == SemVer.parse("2.0.0")

    def test_prerelease_and_build(self):
        v = SemVer.parse("1.4.2-rc.1+build.7")
        assert v.prerelease == ("rc", 1)
        assert v.build == "build.7"
        assert v.is_prerelease

    def test_str_roundtrip(self):
        for raw in ("1.0.0", "1.4.2-rc.1", "2.0.0-alpha.3+sha.abc"):
            assert str(SemVer.parse(raw)) == raw

    @pytest.mark.parametrize(
        "raw",
        ["", "1.2", "1.2.3.4", "x.y.z", "01.2.3", "1.2.3-", "latest", "v", "1.2.3 extra"],
    )
    def test_invalid_versions_raise(self, raw):
        with pytest.raises(InvalidVersionError):
            SemVer.parse(raw)

    def test_try_parse_returns_none(self):
        assert SemVer.try_parse("not-a-version") is None
        assert SemVer.try_parse("1.2.3") is not None

    def test_build_metadata_ignored_in_equality(self):
        assert SemVer.parse("1.2.3+a") == SemVer.parse("1.2.3+b")


class TestSemVerOrdering:
    def test_release_precedence(self):
        assert SemVer.parse("1.0.0") < SemVer.parse("1.0.1")
        assert SemVer.parse("1.0.1") < SemVer.parse("1.1.0")
        assert SemVer.parse("1.9.9") < SemVer.parse("2.0.0")

    def test_prerelease_precedes_release(self):
        assert SemVer.parse("1.0.0-rc.1") < SemVer.parse("1.0.0")
        assert SemVer.parse("1.0.0") > SemVer.parse("1.0.0-rc.1")

    def test_prerelease_identifier_rules(self):
        # Numeric identifiers rank below alphanumeric ones.
        assert SemVer.parse("1.0.0-1") < SemVer.parse("1.0.0-alpha")
        assert SemVer.parse("1.0.0-alpha") < SemVer.parse("1.0.0-alpha.1")
        assert SemVer.parse("1.0.0-alpha.1") < SemVer.parse("1.0.0-beta")
        assert SemVer.parse("1.0.0-rc.1") < SemVer.parse("1.0.0-rc.2")

    def test_string_comparison_would_be_wrong(self):
        # "1.10.0" < "1.9.0" as plain strings; the whole point of parsing.
        assert SemVer.parse("1.10.0") > SemVer.parse("1.9.0")

    def test_sorting(self):
        raw = ["1.0.0", "1.0.0-rc.1", "0.9.9", "2.0.0", "1.10.0", "1.2.0"]
        ordered = [str(v) for v in sorted(SemVer.parse(r) for r in raw)]
        assert ordered == ["0.9.9", "1.0.0-rc.1", "1.0.0", "1.2.0", "1.10.0", "2.0.0"]

    def test_bump_kind(self):
        base = SemVer.parse("1.4.1")
        assert base.bump_kind(SemVer.parse("2.0.0")) == "major"
        assert base.bump_kind(SemVer.parse("1.5.0")) == "minor"
        assert base.bump_kind(SemVer.parse("1.4.2")) == "patch"
        assert base.bump_kind(SemVer.parse("1.4.1")) == "none"

    def test_hashable(self):
        assert len({SemVer.parse("1.0.0"), SemVer.parse("1.0.0")}) == 1


class TestChannels:
    def test_classification(self):
        assert channel_of(SemVer.parse("1.0.0")) == "stable"
        assert channel_of(SemVer.parse("1.0.0-rc.1")) == "beta"
        assert channel_of(SemVer.parse("1.0.0-beta.2")) == "beta"
        assert channel_of(SemVer.parse("1.0.0-dev.4")) == "dev"

    def test_stable_never_accepts_prerelease(self):
        assert channel_accepts("stable", SemVer.parse("2.0.0"))
        assert not channel_accepts("stable", SemVer.parse("2.0.0-rc.1"))
        assert not channel_accepts("stable", SemVer.parse("2.0.0-dev.1"))

    def test_beta_accepts_stable_and_beta_only(self):
        assert channel_accepts("beta", SemVer.parse("2.0.0"))
        assert channel_accepts("beta", SemVer.parse("2.0.0-rc.1"))
        assert not channel_accepts("beta", SemVer.parse("2.0.0-dev.1"))

    def test_dev_accepts_everything(self):
        assert all(
            channel_accepts("dev", SemVer.parse(v)) for v in ("1.0.0", "1.0.0-rc.1", "1.0.0-dev.1")
        )

    def test_unknown_channel_rejected(self):
        with pytest.raises(ValueError):
            channel_accepts("nightly", SemVer.parse("1.0.0"))

    def test_channel_list_is_ordered_loosest_last(self):
        assert CHANNELS == ("stable", "beta", "dev")


class TestProjectVersion:
    def test_current_is_valid_semver(self):
        assert isinstance(current(), SemVer)

    def test_minimum_supported_not_above_current(self):
        assert SemVer.parse(MINIMUM_SUPPORTED_VERSION) <= current()

    def test_pyproject_declares_version_dynamically(self):
        """The single-source guarantee: no literal version in pyproject.toml."""
        text = (Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert 'dynamic = ["version"]' in text
        assert 'attr = "codecortex.version.__version__"' in text

    def test_package_exports_the_same_version(self):
        import codecortex
        from codecortex.version import __version__

        assert codecortex.__version__ == __version__


class TestConsumerCompatibility:
    def test_in_range_returns_version(self):
        installed = current()
        assert require_version(minimum=str(installed), below="99.0.0") == installed

    def test_below_minimum_raises_with_both_bounds(self):
        with pytest.raises(IncompatibleVersionError) as excinfo:
            require_version(minimum="99.0.0", below="100.0.0", consumer="review-bot")
        message = str(excinfo.value)
        assert "review-bot requires CodeCortex >= 99.0.0 and < 100.0.0" in message
        assert f"Installed version: {current()}" in message

    def test_at_or_above_upper_bound_raises(self):
        with pytest.raises(IncompatibleVersionError):
            require_version(below="0.0.1")

    def test_is_compatible_matches_require(self):
        assert is_compatible(minimum="0.0.1", below="99.0.0")
        assert not is_compatible(minimum="99.0.0")

    def test_requires_at_least_one_bound(self):
        with pytest.raises(ValueError):
            require_version()
