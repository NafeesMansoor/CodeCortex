"""Health checks and the version/update/health CLI surface."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from codecortex import health as health_mod
from codecortex import update_cli
from codecortex import updater as updater_mod
from codecortex.cli import main as cli_main
from codecortex.health import run_health_check
from codecortex.installation import Installation
from codecortex.updater import Release, UpdateInfo
from codecortex.upgrade import UpgradeResult
from codecortex.version import SemVer, __version__

ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CODECORTEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("CODECORTEX_NO_UPDATE_CHECK", raising=False)
    # No test in this file may reach the network.
    monkeypatch.setattr(
        updater_mod,
        "_get_json",
        lambda url, timeout: pytest.fail(f"unexpected network call to {url}"),
    )


class TestHealthCheck:
    def test_installation_is_healthy(self):
        report = run_health_check()
        assert report.healthy
        assert report.version == __version__

    def test_core_module_failure_is_fatal(self, monkeypatch):
        monkeypatch.setattr(health_mod, "CORE_MODULES", ("codecortex.definitely_not_a_module",))
        report = run_health_check()
        assert not report.healthy
        assert any(c.name == "Core modules" and c.fatal for c in report.checks)

    def test_missing_optional_module_is_only_a_warning(self, monkeypatch):
        monkeypatch.setattr(health_mod, "OPTIONAL_MODULES", {"nothing": "not_a_real_module"})
        report = run_health_check()
        assert report.healthy
        assert any(c.name.startswith("Optional") and not c.ok for c in report.checks)

    def test_unreadable_config_is_fatal(self, tmp_path):
        bad = tmp_path / "codecortex.yaml"
        bad.write_text("codecortex:\n  bfs_depth: [unclosed\n")
        report = run_health_check(config_path=bad)
        assert not report.healthy
        assert any(c.name == "Configuration" and c.fatal for c in report.checks)

    def test_valid_config_passes(self, tmp_path):
        good = tmp_path / "codecortex.yaml"
        good.write_text("codecortex:\n  bfs_depth: 4\n")
        report = run_health_check(config_path=good)
        assert report.healthy

    def test_current_caches_pass(self, tmp_path):
        from graph.graph_store import GraphStore

        GraphStore(str(tmp_path / "graph.db"))
        report = run_health_check(index_dir=tmp_path)
        assert report.healthy
        assert any("1 database(s)" in c.detail for c in report.checks)

    def test_cache_from_a_newer_version_is_fatal(self, tmp_path):
        import sqlite3

        from core.db_schema import write_schema_version
        from graph.graph_store import GraphStore

        GraphStore(str(tmp_path / "graph.db"))
        with sqlite3.connect(tmp_path / "graph.db") as conn:
            write_schema_version(conn, 999)

        report = run_health_check(index_dir=tmp_path)
        assert not report.healthy
        assert any(c.name == "Index caches" and c.fatal for c in report.checks)

    def test_stale_cache_is_a_warning_not_a_failure(self, tmp_path):
        import sqlite3

        from core.db_schema import write_schema_version
        from graph.graph_store import GraphStore

        GraphStore(str(tmp_path / "graph.db"))
        with sqlite3.connect(tmp_path / "graph.db") as conn:
            write_schema_version(conn, 0)

        report = run_health_check(index_dir=tmp_path)
        assert report.healthy
        assert any("rebuild" in c.detail for c in report.checks)

    def test_report_serialisation(self):
        data = run_health_check().to_dict()
        assert data["status"] in ("HEALTHY", "UNHEALTHY")
        assert data["version"] == __version__
        assert all({"name", "ok", "detail", "severity"} == set(c) for c in data["checks"])

    def test_render_is_human_readable(self):
        text = run_health_check().render()
        assert "CodeCortex Health Check" in text
        assert "Status:" in text


class TestHealthCLI:
    def test_json_output_and_exit_code(self, capsys):
        code = health_mod.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0 and payload["status"] == "HEALTHY"

    def test_failure_exits_nonzero(self, monkeypatch, capsys):
        monkeypatch.setattr(health_mod, "CORE_MODULES", ("nope.not_here",))
        assert health_mod.main([]) == 1
        assert "UNHEALTHY" in capsys.readouterr().out

    def test_subprocess_entry_point_is_the_one_the_updater_uses(self):
        """`python -m codecortex health --json` is what verifies an update."""
        completed = subprocess.run(
            [sys.executable, "-m", "codecortex", "health", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0
        assert json.loads(completed.stdout)["status"] == "HEALTHY"


class TestVersionCLI:
    def test_version_flag(self, capsys):
        assert cli_main(["--version"]) == 0
        assert capsys.readouterr().out.strip() == f"CodeCortex {__version__}"

    def test_usage_shows_the_real_version(self, capsys):
        assert cli_main([]) == 0
        assert f"CodeCortex v{__version__}" in capsys.readouterr().out

    def test_unknown_subcommand_exits_nonzero(self, capsys):
        assert cli_main(["frobnicate"]) == 1
        assert "Unknown subcommand" in capsys.readouterr().err

    def test_version_subcommand_json(self, capsys):
        assert cli_main(["version", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["version"] == __version__
        assert payload["installation_type"] in ("git", "pip", "unknown")

    def test_version_check_reports_update(self, monkeypatch, capsys):
        monkeypatch.setattr(
            update_cli,
            "check_for_updates",
            lambda **kw: UpdateInfo(
                current_version=__version__,
                latest_version="9.9.9",
                update_available=True,
                update_kind="major",
                release_url="https://github.com/NafeesMansoor/CodeCortex/releases/tag/v9.9.9",
            ),
        )
        assert cli_main(["version", "--check"]) == 0
        out = capsys.readouterr().out
        assert "Update available" in out and "9.9.9" in out
        assert "codecortex update" in out

    def test_offline_check_is_not_an_error(self, monkeypatch, capsys):
        monkeypatch.setattr(
            update_cli,
            "check_for_updates",
            lambda **kw: UpdateInfo(current_version=__version__, error="offline"),
        )
        assert cli_main(["version", "--check"]) == 0
        assert "continue normally" in capsys.readouterr().out


class TestUpdateCLI:
    def _plan(self, monkeypatch, info, release, installation=None):
        from codecortex import upgrade as upgrade_mod

        installation = installation or Installation(
            kind="git", package_dir=ROOT / "codecortex", root=ROOT, can_self_update=True
        )
        monkeypatch.setattr(upgrade_mod, "plan_upgrade", lambda **kw: (installation, info, release))
        return installation

    def test_up_to_date_exits_zero(self, monkeypatch, capsys):
        self._plan(
            monkeypatch,
            UpdateInfo(current_version=__version__, latest_version=__version__),
            None,
        )
        assert cli_main(["update"]) == 0
        assert "Already on the latest" in capsys.readouterr().out

    def test_offline_exits_zero(self, monkeypatch, capsys):
        self._plan(monkeypatch, UpdateInfo(current_version=__version__, error="offline"), None)
        assert cli_main(["update"]) == 0
        assert "continue normally" in capsys.readouterr().out

    def test_check_flag_makes_no_changes(self, monkeypatch, capsys):
        from codecortex import upgrade as upgrade_mod

        release = Release(version=SemVer.parse("9.9.9"), tag="v9.9.9", url="")
        self._plan(
            monkeypatch,
            UpdateInfo(
                current_version=__version__,
                latest_version="9.9.9",
                update_available=True,
                update_kind="major",
            ),
            release,
        )
        monkeypatch.setattr(
            upgrade_mod,
            "perform_upgrade",
            lambda *a, **kw: pytest.fail("--check must not install"),
        )
        assert cli_main(["update", "--check"]) == 0
        assert "no changes made" in capsys.readouterr().out

    def test_unupdatable_installation_prints_manual_instructions(self, monkeypatch, capsys):
        release = Release(version=SemVer.parse("9.9.9"), tag="v9.9.9", url="")
        self._plan(
            monkeypatch,
            UpdateInfo(current_version=__version__, latest_version="9.9.9", update_available=True),
            release,
            installation=Installation(
                kind="unknown",
                package_dir=Path("/opt/vendored"),
                can_self_update=False,
                reason="vendored copy",
            ),
        )
        assert cli_main(["update"]) == 1
        out = capsys.readouterr().out
        assert "cannot update itself" in out
        assert "pip install --upgrade" in out

    def test_yes_flag_runs_the_upgrade(self, monkeypatch, capsys):
        from codecortex import upgrade as upgrade_mod

        release = Release(version=SemVer.parse("9.9.9"), tag="v9.9.9", url="")
        self._plan(
            monkeypatch,
            UpdateInfo(current_version=__version__, latest_version="9.9.9", update_available=True),
            release,
        )
        monkeypatch.setattr(
            upgrade_mod,
            "perform_upgrade",
            lambda *a, **kw: UpgradeResult(ok=True, to_version="9.9.9", message="updated to 9.9.9"),
        )
        assert cli_main(["update", "--yes"]) == 0
        assert "updated to 9.9.9" in capsys.readouterr().out

    def test_non_interactive_without_yes_refuses(self, monkeypatch, capsys):
        from codecortex import upgrade as upgrade_mod

        release = Release(version=SemVer.parse("9.9.9"), tag="v9.9.9", url="")
        self._plan(
            monkeypatch,
            UpdateInfo(current_version=__version__, latest_version="9.9.9", update_available=True),
            release,
        )
        monkeypatch.setattr(
            upgrade_mod,
            "perform_upgrade",
            lambda *a, **kw: pytest.fail("must not install without confirmation"),
        )
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        assert cli_main(["update"]) == 1
        assert "Cancelled" in capsys.readouterr().out

    def test_rollback_without_backup_exits_nonzero(self, capsys):
        assert cli_main(["update", "--rollback", "--yes"]) == 1
        assert "No backup found" in capsys.readouterr().err
