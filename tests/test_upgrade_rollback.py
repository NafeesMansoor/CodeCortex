"""Installation detection, the git/pip update strategies, and the upgrade state machine."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from codecortex import installation as install_mod
from codecortex import upgrade as upgrade_mod
from codecortex.installation import (
    GitInstallationUpdater,
    Installation,
    PipInstallationUpdater,
    UpdateError,
    create_backup,
    detect_installation,
    latest_backup,
    restore_config,
    updater_for,
)
from codecortex.updater import Release
from codecortex.upgrade import perform_upgrade, rollback_to_backup
from codecortex.version import SemVer

GIT_AVAILABLE = install_mod._git_available()
needs_git = pytest.mark.skipif(not GIT_AVAILABLE, reason="git is not installed")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CODECORTEX_STATE_DIR", str(tmp_path / "state"))


def make_release(version="1.2.0", assets=None):
    return Release(
        version=SemVer.parse(version),
        tag=f"v{version}",
        url=f"https://github.com/NafeesMansoor/CodeCortex/releases/tag/v{version}",
        assets=assets or {},
    )


def git(args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture
def git_checkout(tmp_path):
    """A work tree cloned from a bare origin with tags v1.0.0 and v1.2.0.

    HEAD sits on v1.0.0, so an update has somewhere real to go.
    """
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)

    git(["config", "user.email", "dev@example.com"], work)
    git(["config", "user.name", "Dev"], work)

    (work / "pyproject.toml").write_text('[project]\nname = "codecortex"\n')
    (work / "codecortex.yaml").write_text("codecortex:\n  bfs_depth: 3\n")
    (work / "codecortex").mkdir()
    (work / "codecortex" / "__init__.py").write_text("")
    git(["add", "-A"], work)
    git(["commit", "-q", "-m", "v1.0.0"], work)
    git(["tag", "v1.0.0"], work)

    (work / "codecortex" / "__init__.py").write_text("# newer\n")
    git(["commit", "-q", "-am", "v1.2.0"], work)
    git(["tag", "v1.2.0"], work)
    git(["push", "-q", "--tags", "origin", "HEAD"], work)

    git(["checkout", "-q", "--detach", "v1.0.0"], work)
    return work


def git_installation(work, **overrides):
    kwargs = dict(
        kind="git",
        package_dir=work / "codecortex",
        root=work,
        editable=True,
        git_ref="v1.0.0",
        git_commit=git(["rev-parse", "HEAD"], work),
        dirty=False,
        can_self_update=True,
    )
    kwargs.update(overrides)
    return Installation(**kwargs)


class TestInstallationDetection:
    def test_detects_this_installation(self):
        found = detect_installation()
        assert found.kind in ("git", "pip", "unknown")
        assert found.package_dir.name == "codecortex"
        assert found.describe()

    @needs_git
    def test_dirty_work_tree_blocks_self_update(self, git_checkout, monkeypatch):
        (git_checkout / "codecortex" / "__init__.py").write_text("local edit\n")

        import codecortex

        monkeypatch.setattr(
            codecortex, "__file__", str(git_checkout / "codecortex" / "__init__.py")
        )
        found = detect_installation()
        assert found.kind == "git"
        assert found.dirty and not found.can_self_update
        assert "uncommitted" in found.reason

    def test_unknown_installation_cannot_be_updated(self):
        unknown = Installation(kind="unknown", package_dir=Path("/tmp/x"), reason="vendored copy")
        with pytest.raises(UpdateError, match="vendored copy"):
            updater_for(unknown)

    def test_strategy_selection(self, tmp_path):
        git_install = Installation(kind="git", package_dir=tmp_path, root=tmp_path)
        pip_install = Installation(kind="pip", package_dir=tmp_path)
        assert isinstance(updater_for(git_install), GitInstallationUpdater)
        assert isinstance(updater_for(pip_install), PipInstallationUpdater)


class TestBackup:
    def test_backup_records_state_and_config(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        (root / "codecortex.yaml").write_text("codecortex:\n  bfs_depth: 7\n")
        installation = Installation(
            kind="git", package_dir=root / "codecortex", root=root, git_commit="abc123"
        )

        backup = create_backup(installation)
        assert (backup.path / "manifest.json").is_file()
        assert backup.git_commit == "abc123"
        assert "codecortex.yaml" in backup.config_files
        assert (backup.path / "config" / "codecortex.yaml").read_text().endswith("bfs_depth: 7\n")

    def test_latest_backup_roundtrips(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        created = create_backup(Installation(kind="git", package_dir=root, root=root))
        loaded = latest_backup()
        assert loaded is not None
        assert loaded.path == created.path
        assert loaded.version == created.version

    def test_no_backup_returns_none(self):
        assert latest_backup() is None

    def test_two_backups_in_the_same_second_do_not_collide(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        (root / "codecortex.yaml").write_text("codecortex: {}\n")
        installation = Installation(kind="git", package_dir=root, root=root)

        first = create_backup(installation)
        second = create_backup(installation)
        assert first.path != second.path
        assert (first.path / "manifest.json").is_file()
        assert (second.path / "manifest.json").is_file()

    def test_config_is_restored_after_being_overwritten(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        config = root / "codecortex.yaml"
        config.write_text("codecortex:\n  bfs_depth: 7\n")
        backup = create_backup(Installation(kind="git", package_dir=root, root=root))

        config.write_text("codecortex:\n  bfs_depth: 1\n")
        restored = restore_config(backup)

        assert restored == [str(config)]
        assert "bfs_depth: 7" in config.read_text()

    def test_secrets_are_not_copied_into_backups(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        (root / ".env").write_text("API_KEY=secret\n")
        (root / "codecortex.yaml").write_text("codecortex: {}\n")

        backup = create_backup(Installation(kind="git", package_dir=root, root=root))
        copied = {p.name for p in (backup.path / "config").iterdir()}
        assert copied == {"codecortex.yaml"}


@needs_git
class TestGitStrategy:
    def test_update_moves_to_the_release_tag(self, git_checkout, monkeypatch):
        monkeypatch.setattr(install_mod, "_pip_install", lambda args: None)
        updater = GitInstallationUpdater(git_installation(git_checkout))
        updater.preflight()
        updater.install(make_release("1.2.0"))

        assert git(["describe", "--tags", "--exact-match"], git_checkout) == "v1.2.0"
        assert "# newer" in (git_checkout / "codecortex" / "__init__.py").read_text()

    def test_rollback_restores_the_previous_commit(self, git_checkout, monkeypatch):
        monkeypatch.setattr(install_mod, "_pip_install", lambda args: None)
        installation = git_installation(git_checkout)
        original = installation.git_commit
        backup = create_backup(installation)

        updater = GitInstallationUpdater(installation)
        updater.install(make_release("1.2.0"))
        assert git(["rev-parse", "HEAD"], git_checkout) != original

        updater.rollback(backup)
        assert git(["rev-parse", "HEAD"], git_checkout) == original
        assert (git_checkout / "codecortex" / "__init__.py").read_text() == ""

    def test_missing_tag_is_reported(self, git_checkout, monkeypatch):
        monkeypatch.setattr(install_mod, "_pip_install", lambda args: None)
        updater = GitInstallationUpdater(git_installation(git_checkout))
        with pytest.raises(UpdateError, match="not found"):
            updater.install(make_release("9.9.9"))

    def test_preflight_refuses_dirty_tree(self, git_checkout):
        updater = GitInstallationUpdater(git_installation(git_checkout, dirty=True))
        with pytest.raises(UpdateError, match="uncommitted changes"):
            updater.preflight()

    def test_rollback_without_recorded_commit_fails_loudly(self, git_checkout):
        installation = git_installation(git_checkout, git_commit=None)
        backup = create_backup(installation)
        with pytest.raises(UpdateError, match="does not record a git commit"):
            GitInstallationUpdater(installation).rollback(backup)


class TestPipStrategy:
    def test_prefers_wheel_asset(self):
        release = make_release(
            "1.2.0",
            assets={
                "codecortex-1.2.0.tar.gz": "https://github.com/x/y/releases/download/v1.2.0/a.tar.gz",
                "codecortex-1.2.0-py3-none-any.whl": "https://github.com/x/y/releases/download/v1.2.0/a.whl",
            },
        )
        assert PipInstallationUpdater._source_for(release).endswith(".whl")

    def test_falls_back_to_sdist_then_git_tag(self):
        sdist = make_release(
            "1.2.0",
            assets={
                "codecortex-1.2.0.tar.gz": "https://github.com/x/y/releases/download/v/a.tar.gz"
            },
        )
        assert PipInstallationUpdater._source_for(sdist).endswith(".tar.gz")

        bare = make_release("1.2.0")
        source = PipInstallationUpdater._source_for(bare)
        assert source.startswith("codecortex @ git+https://github.com/")
        assert source.endswith("@v1.2.0")

    def test_install_only_upgrades_codecortex(self, monkeypatch):
        captured = []
        monkeypatch.setattr(install_mod, "_pip_install", lambda args: captured.append(args))
        PipInstallationUpdater(Installation(kind="pip", package_dir=Path("/x"))).install(
            make_release("1.2.0")
        )
        assert captured[0][0] == "--upgrade"
        assert "--upgrade-strategy" not in captured[0]

    def test_failed_pip_becomes_update_error(self, monkeypatch):
        monkeypatch.setattr(
            install_mod,
            "_run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "no matching distribution"),
        )
        with pytest.raises(UpdateError, match="no matching distribution"):
            install_mod._pip_install(["codecortex"])


class FakeUpdater:
    """Stand-in strategy so the state machine can be tested without installing."""

    def __init__(self, installation, fail_on=None):
        self.installation = installation
        self.fail_on = (fail_on,) if isinstance(fail_on, str) else tuple(fail_on or ())
        self.calls = []

    def preflight(self):
        self.calls.append("preflight")
        if "preflight" in self.fail_on:
            raise UpdateError("preflight blew up")

    def install(self, release):
        self.calls.append("install")
        if "install" in self.fail_on:
            raise UpdateError("install blew up")

    def rollback(self, backup):
        self.calls.append("rollback")
        if "rollback" in self.fail_on:
            raise UpdateError("rollback blew up")


@pytest.fixture
def orchestration(tmp_path, monkeypatch):
    """Wire perform_upgrade to a fake strategy and a controllable health check."""
    root = tmp_path / "install"
    root.mkdir()
    (root / "codecortex.yaml").write_text("codecortex: {}\n")
    installation = Installation(
        kind="git", package_dir=root / "codecortex", root=root, git_commit="abc", editable=True
    )

    state = {"healthy": True, "detail": "verified", "fake": None}

    def factory(inst):
        state["fake"] = FakeUpdater(inst, fail_on=state.get("fail_on"))
        return state["fake"]

    monkeypatch.setattr(upgrade_mod, "updater_for", factory)
    monkeypatch.setattr(
        upgrade_mod,
        "_health_check_subprocess",
        lambda cwd: (state["healthy"], state["detail"]),
    )
    return installation, state


def step_names(result):
    return [label for label, _, _ in result.steps]


class TestUpgradeStateMachine:
    def test_successful_update_runs_every_stage_in_order(self, orchestration):
        installation, state = orchestration
        result = perform_upgrade(installation, make_release("1.2.0"))

        assert result.ok and not result.rolled_back
        assert step_names(result) == [
            "Preflight checks",
            "Backup created",
            "Release installed",
            "Database migration",
            "Health check",
        ]
        assert state["fake"].calls == ["preflight", "install"]
        assert result.to_version == "1.2.0"
        assert "successfully updated to 1.2.0" in result.message

    def test_backup_exists_before_install(self, orchestration):
        installation, state = orchestration
        result = perform_upgrade(installation, make_release("1.2.0"))
        assert Path(result.backup_path).is_dir()

    def test_preflight_failure_stops_before_backup(self, orchestration):
        installation, state = orchestration
        state["fail_on"] = "preflight"
        result = perform_upgrade(installation, make_release("1.2.0"))

        assert not result.ok and not result.rolled_back
        assert step_names(result) == ["Preflight checks"]
        assert result.backup_path is None
        assert "preflight blew up" in result.message

    def test_install_failure_triggers_rollback(self, orchestration):
        installation, state = orchestration
        state["fail_on"] = "install"
        result = perform_upgrade(installation, make_release("1.2.0"))

        assert not result.ok and result.rolled_back
        assert "rollback" in state["fake"].calls
        assert "install blew up" in result.message

    def test_failed_health_check_triggers_rollback(self, orchestration):
        installation, state = orchestration
        state["healthy"] = False
        state["detail"] = "failed: Core modules"
        result = perform_upgrade(installation, make_release("1.2.0"))

        assert not result.ok and result.rolled_back
        assert state["fake"].calls == ["preflight", "install", "rollback"]
        assert "Core modules" in result.message

    def test_blocked_migration_triggers_rollback(self, orchestration, tmp_path):
        import sqlite3

        from core.db_schema import write_schema_version
        from graph.graph_store import GraphStore

        index_dir = tmp_path / "index"
        index_dir.mkdir()
        GraphStore(str(index_dir / "graph.db"))
        with sqlite3.connect(index_dir / "graph.db") as conn:
            write_schema_version(conn, 999)

        installation, state = orchestration
        result = perform_upgrade(installation, make_release("1.2.0"), index_dir=index_dir)

        assert not result.ok and result.rolled_back
        assert "newer CodeCortex" in result.message

    def test_stale_caches_are_rebuilt_during_update(self, orchestration, tmp_path):
        import sqlite3

        from core.db_schema import read_schema_version, write_schema_version
        from graph.graph_store import GraphStore

        index_dir = tmp_path / "index"
        index_dir.mkdir()
        GraphStore(str(index_dir / "graph.db"))
        with sqlite3.connect(index_dir / "graph.db") as conn:
            write_schema_version(conn, 0)

        installation, state = orchestration
        result = perform_upgrade(installation, make_release("1.2.0"), index_dir=index_dir)

        assert result.ok
        assert read_schema_version(sqlite3.connect(index_dir / "graph.db")) >= 1

    def test_failed_rollback_reports_manual_recovery(self, orchestration):
        installation, state = orchestration
        state["fail_on"] = ("install", "rollback")
        result = perform_upgrade(installation, make_release("1.2.0"))

        assert not result.ok and not result.rolled_back
        assert "Restore manually" in result.message
        assert result.backup_path in result.message

    def test_steps_are_reported_to_the_callback(self, orchestration):
        installation, state = orchestration
        seen = []
        perform_upgrade(
            installation, make_release("1.2.0"), on_step=lambda *args: seen.append(args)
        )
        assert [label for label, _, _ in seen] == step_names(
            perform_upgrade(installation, make_release("1.2.0"))
        )


class TestExplicitRollback:
    def test_rollback_without_backup_reports_clearly(self, monkeypatch):
        result = rollback_to_backup()
        assert not result.ok
        assert result.message == "no backup found"

    def test_rollback_restores_and_verifies(self, orchestration, monkeypatch):
        installation, state = orchestration
        monkeypatch.setattr(upgrade_mod, "detect_installation", lambda: installation)
        backup = create_backup(installation)

        result = rollback_to_backup(backup)
        assert result.ok and result.rolled_back
        assert step_names(result) == ["Rollback", "Health check"]

    def test_rollback_reports_unhealthy_restore(self, orchestration, monkeypatch):
        installation, state = orchestration
        monkeypatch.setattr(upgrade_mod, "detect_installation", lambda: installation)
        backup = create_backup(installation)
        state["healthy"] = False
        state["detail"] = "failed: Core modules"

        result = rollback_to_backup(backup)
        assert not result.ok
        assert "health check failed" in result.message
