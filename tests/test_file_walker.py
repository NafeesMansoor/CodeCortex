"""Tests for source file discovery and directory pruning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.file_walker import DEFAULT_EXCLUDED_DIRS, GitIgnore, walk_source_files


def _touch(path: Path, content: str = "x = 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _names(root: Path, **kwargs) -> set[str]:
    return {p.relative_to(root).as_posix() for p in walk_source_files(root, ["py"], **kwargs)}


class TestDefaultExclusions:
    def test_prunes_in_tree_virtualenv(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "pkg" / "service.py")
        _touch(tmp_path / "venv" / "lib" / "dep.py")
        _touch(tmp_path / ".venv" / "lib" / "site-packages" / "other.py")

        assert _names(tmp_path) == {"app.py", "pkg/service.py"}

    def test_prunes_caches_and_build_output(self, tmp_path):
        _touch(tmp_path / "app.py")
        for excluded in ("__pycache__", "node_modules", "build", ".git", ".mypy_cache", "vendor"):
            _touch(tmp_path / excluded / "junk.py")

        assert _names(tmp_path) == {"app.py"}

    def test_prunes_egg_info_by_suffix(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "codecortex.egg-info" / "junk.py")

        assert _names(tmp_path) == {"app.py"}

    def test_empty_exclude_dirs_walks_everything(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "venv" / "dep.py")

        assert _names(tmp_path, exclude_dirs=[]) == {"app.py", "venv/dep.py"}

    def test_default_set_covers_common_layouts(self):
        for name in ("venv", ".venv", "node_modules", "site-packages", "__pycache__", ".git"):
            assert name in DEFAULT_EXCLUDED_DIRS


class TestExtensionMatching:
    def test_matches_multiple_extensions_in_one_walk(self, tmp_path):
        _touch(tmp_path / "a.py")
        _touch(tmp_path / "b.ts")
        _touch(tmp_path / "c.php")
        _touch(tmp_path / "d.md")

        found = {p.name for p in walk_source_files(tmp_path, ["py", "ts", "php"])}
        assert found == {"a.py", "b.ts", "c.php"}

    def test_leading_dot_in_extension_is_accepted(self, tmp_path):
        _touch(tmp_path / "a.py")
        assert {p.name for p in walk_source_files(tmp_path, [".py"])} == {"a.py"}

    def test_no_extensions_yields_nothing(self, tmp_path):
        _touch(tmp_path / "a.py")
        assert list(walk_source_files(tmp_path, [])) == []

    def test_results_are_sorted(self, tmp_path):
        for name in ("c.py", "a.py", "b.py"):
            _touch(tmp_path / name)
        found = [p.name for p in walk_source_files(tmp_path, ["py"])]
        assert found == sorted(found)


class TestExcludePaths:
    def test_excludes_relative_path(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "docs" / "sample.py")

        assert _names(tmp_path, exclude_paths=["docs"]) == {"app.py"}

    def test_trailing_slash_is_tolerated(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "docs" / "deep" / "sample.py")

        assert _names(tmp_path, exclude_paths=["docs/"]) == {"app.py"}

    def test_excludes_absolute_path_under_root(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "docs" / "sample.py")

        assert _names(tmp_path, exclude_paths=[str(tmp_path / "docs")]) == {"app.py"}

    def test_excludes_absolute_path_through_a_symlinked_root(self, tmp_path):
        real = tmp_path / "real"
        _touch(real / "app.py")
        _touch(real / "docs" / "sample.py")
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)

        # Root reached via the symlink, exclude given against the real path.
        found = {
            p.name for p in walk_source_files(link, ["py"], exclude_paths=[str(real / "docs")])
        }
        assert found == {"app.py"}

    def test_path_outside_root_is_ignored(self, tmp_path):
        _touch(tmp_path / "app.py")
        assert _names(tmp_path, exclude_paths=["/somewhere/else"]) == {"app.py"}

    def test_excludes_a_single_file(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "skip.py")

        assert _names(tmp_path, exclude_paths=["skip.py"]) == {"app.py"}


class TestGitIgnoreSupport:
    def test_honours_gitignore(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "generated" / "pb.py")
        (tmp_path / ".gitignore").write_text("generated/\n")

        assert _names(tmp_path) == {"app.py"}

    def test_can_be_disabled(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "generated" / "pb.py")
        (tmp_path / ".gitignore").write_text("generated/\n")

        assert _names(tmp_path, respect_gitignore=False) == {"app.py", "generated/pb.py"}

    def test_glob_and_negation(self, tmp_path):
        _touch(tmp_path / "app.py")
        _touch(tmp_path / "app_pb2.py")
        _touch(tmp_path / "keep_pb2.py")
        (tmp_path / ".gitignore").write_text("*_pb2.py\n!keep_pb2.py\n")

        assert _names(tmp_path) == {"app.py", "keep_pb2.py"}

    def test_anchored_pattern_only_matches_at_root(self, tmp_path):
        _touch(tmp_path / "tmp" / "a.py")
        _touch(tmp_path / "pkg" / "tmp" / "b.py")
        (tmp_path / ".gitignore").write_text("/tmp/\n")

        assert _names(tmp_path) == {"pkg/tmp/b.py"}

    def test_unanchored_pattern_matches_at_any_depth(self, tmp_path):
        _touch(tmp_path / "tmp" / "a.py")
        _touch(tmp_path / "pkg" / "tmp" / "b.py")
        (tmp_path / ".gitignore").write_text("tmp/\n")

        assert _names(tmp_path) == set()

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        _touch(tmp_path / "app.py")
        (tmp_path / ".gitignore").write_text("# a comment\n\n   \n")

        assert _names(tmp_path) == {"app.py"}

    def test_missing_gitignore_matches_nothing(self, tmp_path):
        gitignore = GitIgnore.load(tmp_path)
        assert not gitignore
        assert not gitignore.ignored("anything.py", is_dir=False)

    def test_directory_only_rule_spares_same_named_file(self, tmp_path):
        _touch(tmp_path / "dist.py")
        _touch(tmp_path / "out" / "x.py")
        (tmp_path / ".gitignore").write_text("dist.py/\nout/\n")

        assert _names(tmp_path) == {"dist.py"}


class TestSymlinks:
    def test_does_not_follow_symlinked_directories(self, tmp_path):
        _touch(tmp_path / "app.py")
        target = tmp_path / "outside"
        _touch(target / "dep.py")
        (tmp_path / "link").symlink_to(target, target_is_directory=True)

        found = _names(tmp_path)
        assert "link/dep.py" not in found
        assert "app.py" in found
