"""Configuration handling across upgrades: nothing is dropped without saying so."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from codecortex.config import CodeCortexConfig, find_config
from codecortex.config import load as load_config


def write_config(tmp_path, body):
    path = tmp_path / "codecortex.yaml"
    path.write_text(body)
    return path


class TestConfigLoading:
    def test_known_settings_are_applied(self, tmp_path):
        path = write_config(
            tmp_path,
            "codecortex:\n  bfs_depth: 7\n  token_budget: 9000\n  exclude_paths:\n    - storage/\n",
        )
        config = load_config(path)
        assert config.bfs_depth == 7
        assert config.token_budget == 9000
        assert config.exclude_paths == ["storage/"]

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        assert load_config(tmp_path / "absent.yaml") == CodeCortexConfig()

    def test_empty_file_is_defaults(self, tmp_path):
        assert load_config(write_config(tmp_path, "")) == CodeCortexConfig()

    def test_settings_survive_a_reload(self, tmp_path):
        path = write_config(tmp_path, "codecortex:\n  bfs_depth: 5\n")
        assert load_config(path) == load_config(path)

    def test_find_config_walks_upwards(self, tmp_path):
        write_config(tmp_path, "codecortex:\n  bfs_depth: 2\n")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_config(nested) == tmp_path / "codecortex.yaml"

    def test_find_config_returns_none_when_absent(self, tmp_path):
        assert find_config(tmp_path / "nowhere") is None


class TestUnknownSettings:
    def test_unknown_keys_are_reported_not_silently_dropped(self, tmp_path, capsys):
        path = write_config(
            tmp_path,
            "codecortex:\n  bfs_depth: 4\n  legacy_option: true\n  another_typo: 1\n",
        )
        config = load_config(path)

        err = capsys.readouterr().err
        assert "another_typo" in err and "legacy_option" in err
        # Recognised settings still apply — a stray key is not fatal.
        assert config.bfs_depth == 4

    def test_no_warning_when_everything_is_recognised(self, tmp_path, capsys):
        load_config(write_config(tmp_path, "codecortex:\n  bfs_depth: 4\n"))
        assert capsys.readouterr().err == ""

    def test_non_mapping_section_falls_back_with_a_warning(self, tmp_path, capsys):
        config = load_config(write_config(tmp_path, "codecortex:\n  - not\n  - a mapping\n"))
        assert config == CodeCortexConfig()
        assert "not a mapping" in capsys.readouterr().err

    def test_top_level_settings_without_the_codecortex_key(self, tmp_path):
        assert load_config(write_config(tmp_path, "bfs_depth: 6\n")).bfs_depth == 6
