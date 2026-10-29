"""The saved per-project index and the assistant integration built on it.

Queries used to rebuild the whole index on every call. These tests pin the
build-once behaviour: ``index`` saves to ``.codecortex/``, later runs load it
and re-parse only what changed, and ``install`` wires the CLI into the
project's assistant instructions.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import codecortex.pipeline.state as state
from codecortex import cli
from codecortex.core.types import EdgeKind
from codecortex.embeddings.provider_factory import LexicalEmbeddingProvider
from codecortex.explain import explain
from codecortex.integrate import END, START, install, uninstall
from codecortex.pipeline.context_builder import PipelineConfig

SERVICE = "def login(user):\n    return check(user)\n\ndef check(user):\n    return True\n"
APP = "from service import login\n\ndef main():\n    login('a')\n"


def _project(tmp_path: Path) -> Path:
    (tmp_path / "service.py").write_text(SERVICE)
    (tmp_path / "app.py").write_text(APP)
    return tmp_path


def _open(root: Path, **kwargs):
    return state.open_pipeline(root, config=PipelineConfig(enable_clustering=False), **kwargs)


def _callers(pipeline, name: str) -> set[str]:
    return {
        e.source for e in pipeline.graph_store.get_incoming_edges(name) if e.kind == EdgeKind.CALLS
    }


class TestSavedIndex:
    def test_first_open_builds_and_saves(self, tmp_path):
        root = _project(tmp_path)
        pipeline, opened = _open(root)

        assert opened.source == "built" and opened.saved
        assert (root / ".codecortex" / "graph.db").is_file()
        assert (root / ".codecortex" / ".gitignore").read_text().strip().endswith("*")
        assert pipeline.graph_store.get_node("login") is not None

    def test_second_open_loads_without_reparsing(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        _open(root)

        def no_build(*args, **kwargs):
            raise AssertionError("a saved index must not be rebuilt")

        monkeypatch.setattr(
            "codecortex.pipeline.context_builder.CodeCortexPipeline.build", no_build
        )
        pipeline, opened = _open(root)

        assert (opened.source, opened.reparsed, opened.removed) == ("loaded", 0, 0)
        assert _callers(pipeline, "login") == {"main"}
        assert pipeline.retrieve("login")  # derived layers are ready

    def test_changed_file_is_reparsed_and_inbound_calls_survive(self, tmp_path):
        root = _project(tmp_path)
        _open(root)
        service = root / "service.py"
        service.write_text(SERVICE + "\ndef logout(user):\n    return check(user)\n")

        pipeline, opened = _open(root)

        assert opened.reparsed == 1
        assert pipeline.graph_store.get_node("logout") is not None
        # app.py did not change; its call into service.py must not be lost.
        assert _callers(pipeline, "login") == {"main"}
        # Centrality is recomputed for the changed graph, so the saved state is
        # current rather than stale (2.0.1 reused the old scores).
        assert not json.loads((root / ".codecortex" / "state.json").read_text())["derived_stale"]
        assert "logout" in pipeline._centrality_scores

    def test_deleted_file_is_dropped(self, tmp_path):
        root = _project(tmp_path)
        (root / "extra.py").write_text("def unused():\n    pass\n")
        (root / "more.py").write_text("def other():\n    pass\n")
        _open(root)
        (root / "extra.py").unlink()

        pipeline, opened = _open(root)

        assert opened.removed == 1
        assert pipeline.graph_store.get_node("unused") is None

    def test_graph_only_refresh_keeps_saved_centrality(self, tmp_path):
        root = _project(tmp_path)
        _open(root)
        (root / "app.py").write_text(APP + "\ndef extra():\n    main()\n")

        pipeline, opened = _open(root, derived=False)

        assert opened.reparsed == 1 and pipeline.graph_store.get_node("extra") is not None
        saved = json.loads((root / ".codecortex" / "state.json").read_text())
        assert "login" in saved["centrality"]

    def test_saved_embeddings_are_reused(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        _open(root)
        assert (root / ".codecortex" / "embeddings.npy").is_file()

        def no_embed(*args, **kwargs):
            raise AssertionError("saved vectors must not be recomputed")

        monkeypatch.setattr(
            "codecortex.embeddings.embedding_pipeline.EmbeddingPipeline.index_all", no_embed
        )
        pipeline, _ = _open(root)
        assert pipeline.retrieve("login check")[0].qualified_name in {"login", "check"}

    def test_vectors_follow_a_graph_only_refresh(self, tmp_path):
        root = _project(tmp_path)
        _open(root)
        (root / "service.py").write_text("def charge_invoice():\n    pass\n")
        _open(root, derived=False)  # explain: graph refreshed, vectors untouched

        pipeline, _ = _open(root)

        names = set(pipeline.embedding_store._items)
        assert "charge_invoice" in names and "login" not in names
        assert pipeline.retrieve("invoice")[0].qualified_name == "charge_invoice"

    def test_index_from_another_release_is_rebuilt(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        _open(root)
        monkeypatch.setattr(state, "__version__", "0.0.0")

        _, opened = _open(root)
        assert opened.source == "built"

    def test_different_language_selection_is_a_different_index(self, tmp_path):
        root = _project(tmp_path)
        (root / "web.ts").write_text("function render() {}\n")
        _open(root, languages=["py"])

        pipeline, opened = _open(root, languages=["ts"])
        assert opened.source == "built"
        assert pipeline.graph_store.get_node("render") is not None


class TestLexicalEmbeddings:
    def test_vectors_are_stable_across_processes(self):
        # A saved index is reused by later processes, so vectors must not
        # depend on Python's per-process string-hash salt.
        vector = LexicalEmbeddingProvider(64).embed(["function user login"])[0]
        assert [round(v, 6) for v in vector[:8]] == [
            round(v, 6) for v in LexicalEmbeddingProvider(64).embed(["function user login"])[0][:8]
        ]
        assert any(vector)

    def test_shared_words_score_higher(self, tmp_path):
        root = _project(tmp_path)
        (root / "billing.py").write_text("def charge_invoice():\n    pass\n")
        pipeline, _ = _open(root)

        top = pipeline.retrieve("invoice charge")[0]
        assert top.qualified_name == "charge_invoice"


class TestExplain:
    def test_reports_callers_and_callees(self, tmp_path):
        root = _project(tmp_path)
        pipeline, _ = _open(root)

        [entry] = explain(pipeline.graph_store, "login", root, limit=5)

        assert entry["location"] == "service.py:1"
        assert {"kind": "CALLS", "target": "check"} in entry["outgoing"]
        assert {"kind": "CALLS", "source": "main"} in entry["incoming"]

    def test_cli_uses_the_saved_index(self, tmp_path, capsys):
        root = _project(tmp_path)
        assert cli.main(["index", str(root), "--quiet"]) == 0

        assert cli.main(["explain", "login", "--target", str(root)]) == 0
        captured = capsys.readouterr()
        assert "Using saved index." in captured.err
        assert "<--calls-- main" in captured.out


class TestInstall:
    def test_install_is_idempotent_and_keeps_existing_content(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Project rules\n\nUse tabs.\n")

        install(tmp_path, ["claude"])
        install(tmp_path, ["claude"])

        text = (tmp_path / "CLAUDE.md").read_text()
        assert text.startswith("# Project rules\n\nUse tabs.\n")
        assert text.count(START) == 1 and text.count(END) == 1
        assert "codecortex query" in text
        skill = tmp_path / ".claude" / "skills" / "codecortex" / "SKILL.md"
        assert skill.read_text().startswith("---\nname: codecortex\n")

    def test_uninstall_removes_only_what_install_added(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        install(tmp_path, ["claude", "agents", "cursor"])

        uninstall(tmp_path, ["claude", "agents", "cursor"])

        assert (tmp_path / "AGENTS.md").read_text() == "# Agents\n"
        assert not (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / ".claude" / "skills" / "codecortex").exists()
        assert not (tmp_path / ".cursor" / "rules" / "codecortex.mdc").exists()

    def test_uninstall_purge_deletes_the_saved_index(self, tmp_path):
        root = _project(tmp_path)
        _open(root)
        assert cli.main(["install", "--dir", str(root)]) == 0

        assert cli.main(["uninstall", "--dir", str(root), "--purge"]) == 0
        assert not (root / ".codecortex").exists()


def test_impact_text_output(tmp_path, capsys):
    root = _project(tmp_path)
    assert cli.main(["index", str(root), "--quiet"]) == 0

    assert cli.main(["query", "--impact", "login", "--target", str(root), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "Direct callers  : main" in out
    assert "Affected files  : app.py" in out
    assert "Transitive nodes:" in out


class TestHook:
    def _payload(self, root, tool, session="s1", **tool_input):
        return {
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": str(root),
            "session_id": session,
        }

    def test_notes_searches_and_source_reads_once_per_session(self, tmp_path):
        from codecortex.hook import note_for

        root = _project(tmp_path)
        _open(root)

        assert "codecortex query" in note_for(self._payload(root, "Grep", pattern="login"))
        assert note_for(self._payload(root, "Bash", command="rg login src/")) is None  # once
        assert "codecortex explain" in note_for(
            self._payload(root, "Read", file_path=str(root / "app.py"))
        )
        assert note_for(self._payload(root, "Grep", session="s2", pattern="x")) is not None

    def test_silent_without_an_index_or_for_other_tools(self, tmp_path):
        from codecortex.hook import note_for

        root = _project(tmp_path)
        assert note_for(self._payload(root, "Grep", pattern="login")) is None
        _open(root)
        assert note_for(self._payload(root, "Bash", command="ls -la")) is None
        assert note_for(self._payload(root, "Read", file_path=str(root / "README.md"))) is None
        assert note_for(self._payload(root, "Edit", file_path=str(root / "app.py"))) is None

    def test_cli_emits_hook_json_and_never_fails(self, tmp_path, monkeypatch, capsys):
        import io

        root = _project(tmp_path)
        _open(root)
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps(self._payload(root, "Glob", pattern="*.py")))
        )
        assert cli.main(["hook-guard"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

        monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
        assert cli.main(["hook-guard"]) == 0
        assert capsys.readouterr().out == ""

    def test_install_merges_settings_and_uninstall_restores_them(self, tmp_path):
        from codecortex.integrate import HOOK_COMMAND, install_hook, uninstall_hook

        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir()
        original = {
            "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"command": "fmt"}]}]},
        }
        settings.write_text(json.dumps(original))

        install_hook(tmp_path)
        install_hook(tmp_path)
        entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
        assert [e["hooks"][0]["command"] for e in entries] == ["fmt", HOOK_COMMAND]

        uninstall_hook(tmp_path)
        assert json.loads(settings.read_text()) == original

    def test_invalid_settings_are_left_alone(self, tmp_path, capsys):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("{ not json")

        assert cli.main(["install", "--dir", str(tmp_path)]) == 0
        assert settings.read_text() == "{ not json"
        assert "hook not installed" in capsys.readouterr().err
