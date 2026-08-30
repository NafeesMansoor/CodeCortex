"""Regression tests for the .tsx/.jsx discovery and grammar-selection bugs.

Both bugs made every .tsx/.jsx file in a project invisible to the graph with
no error or warning: the file walker only globbed one hardcoded extension per
language, and even a discovered .tsx file was parsed with the plain
"typescript" tree-sitter grammar, which has no JSX productions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.parsers.typescript_parser import TypeScriptParser
from core.types import NodeKind
from pipeline.context_builder import CodeCortexPipeline, PipelineConfig

TSX_SOURCE = """
export function Widget({ label }: { label: string }) {
  return <div className="widget">{label}</div>
}
"""


class TestTsxGrammarSelection:
    def test_tsx_file_parses_without_error(self):
        result = TypeScriptParser().parse(TSX_SOURCE, "src/Widget.tsx")
        assert result.errors == []

    def test_tsx_component_is_extracted(self):
        result = TypeScriptParser().parse(TSX_SOURCE, "src/Widget.tsx")
        names = [(n.kind, n.name) for n in result.nodes]
        assert (NodeKind.FUNCTION, "Widget") in names

    def test_plain_ts_file_still_uses_typescript_grammar(self):
        # A .ts file has no JSX and must keep parsing on the non-tsx grammar.
        result = TypeScriptParser().parse(
            "export function add(a: number, b: number) { return a + b }", "src/math.ts"
        )
        assert result.errors == []
        assert [n.name for n in result.nodes] == ["add"]


class TestExtensionDiscovery:
    def test_tsx_and_jsx_files_are_discovered_and_indexed(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Widget.tsx").write_text(TSX_SOURCE)
        (tmp_path / "src" / "Legacy.jsx").write_text(
            "export function Legacy() { return <span>hi</span> }\n"
        )

        pipeline = CodeCortexPipeline.from_directory(
            tmp_path,
            config=PipelineConfig(enable_clustering=False),
        )

        indexed = {p.name for p in pipeline._indexed_files}
        assert indexed == {"Widget.tsx", "Legacy.jsx"}
        assert pipeline.graph_store.node_count() >= 2

    def test_reindex_file_uses_the_matching_parser_for_tsx(self, tmp_path):
        # reindex_file() and the on-demand-expansion path used to hardcode
        # PythonParser regardless of the changed file's language.
        f = tmp_path / "Widget.tsx"
        f.write_text(TSX_SOURCE)

        pipeline = CodeCortexPipeline.from_directory(
            tmp_path,
            config=PipelineConfig(enable_clustering=False),
        )
        before = pipeline.graph_store.node_count()

        f.write_text(TSX_SOURCE + "\nexport function Extra() { return <span/> }\n")
        pipeline.reindex_file(str(f))

        assert pipeline.graph_store.node_count() > before
