"""TARP task-aware reduction and the incremental-load centrality fix.

The reduction tests pin the contract the paper relies on: every method stays
within the same cost budget, results are deterministic, selected view nodes
map back to original nodes, and path-aware selection keeps whole seed paths.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import codecortex.pipeline.state as state
from codecortex.pipeline.context_builder import CodeCortexPipeline, PipelineConfig
from codecortex.reduction import (
    BASELINES,
    TARPConfig,
    TaskAwareReducer,
    build_view,
    reduce_baseline,
)

AUTH = (
    "class AuthService:\n"
    "    def login(self, user):\n"
    "        token = self.issue_token(user)\n"
    "        return validate_token(token)\n\n"
    "    def issue_token(self, user):\n"
    "        return sign(user)\n"
)
TOKENS = "def validate_token(token):\n    return verify_signature(token)\n\ndef verify_signature(token):\n    return True\n"
CRYPTO = "def sign(user):\n    return digest(user)\n\ndef digest(value):\n    return value\n"
NOISE = "".join(
    f"def report_{i}(rows):\n    total = len(rows)\n    return total\n\n" for i in range(30)
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "auth.py").write_text(AUTH)
    (tmp_path / "tokens.py").write_text(TOKENS)
    (tmp_path / "crypto.py").write_text(CRYPTO)
    (tmp_path / "reports.py").write_text(NOISE)
    return tmp_path


@pytest.fixture
def pipeline(tmp_path):
    p = CodeCortexPipeline(PipelineConfig(enable_clustering=False))
    p.build(_project(tmp_path))
    return p


class TestGraphView:
    def test_resolves_bare_name_calls_to_definitions(self, pipeline):
        view = build_view(pipeline.graph_store)
        assert view.stats.endpoints_resolved > 0
        assert ("validate_token", "verify_signature") in view.edges

    def test_condensation_maps_members_to_parent(self, pipeline):
        view = build_view(pipeline.graph_store, condense=True)
        assert view.stats.nodes_condensed > 0
        everything = {m for members in view.members.values() for m in members}
        assert everything == {n.qualified_name for n in pipeline.graph_store.all_nodes()}
        assert not any(
            k.value in ("Variable", "Parameter") and v in view.kind for v, k in view.kind.items()
        )


class TestTARP:
    QUERY = "validate token signature for login"

    @pytest.mark.parametrize("variant", list("ABCDEFG"))
    def test_variants_respect_budget(self, pipeline, variant):
        res = pipeline.reduce(self.QUERY, budget=12, config=TARPConfig.variant(variant))
        cfg = TARPConfig.variant(variant)
        limit = 12 * (1 + cfg.recovery_budget) if cfg.recovery else 12
        assert 0 < res.cost <= limit + 1e-9
        assert res.selected <= set(pipeline.reduction_view().kind)
        assert res.selected <= res.original_nodes

    @pytest.mark.parametrize("method", BASELINES)
    def test_baselines_respect_budget(self, pipeline, method):
        res = pipeline.reduce(self.QUERY, budget=12, method=method)
        assert 0 < res.cost <= 12 + 1e-9

    def test_deterministic(self, pipeline):
        a = pipeline.reduce(self.QUERY, budget=10)
        b = pipeline.reduce(self.QUERY, budget=10)
        assert a.selected == b.selected and a.cost == b.cost

    def test_query_conditioning_selects_relevant_code(self, pipeline):
        res = pipeline.reduce(self.QUERY, budget=8, config=TARPConfig.variant("F"))
        assert {"validate_token", "verify_signature"} & res.selected
        assert not any(v.startswith("report_") for v in res.selected)

    def test_path_aware_keeps_whole_seed_paths(self, pipeline):
        view = pipeline.reduction_view()
        semantic = {"AuthService.login": 1.0, "digest": 0.9}
        res = TaskAwareReducer(view).reduce(
            semantic, budget=10, config=TARPConfig.variant("F", n_seeds=2, path_seeds=2)
        )
        assert res.stats["seed_paths"] >= 1
        assert res.stats["seed_paths_retained"] == res.stats["seed_paths"]

    def test_unknown_baseline_rejected(self, pipeline):
        with pytest.raises(ValueError):
            reduce_baseline("nope", TaskAwareReducer(pipeline.reduction_view()), {}, 5)

    def test_retrieve_restricted_to_reduced_graph(self, tmp_path):
        p = CodeCortexPipeline(
            PipelineConfig(enable_clustering=False, use_tarp=True, tarp_budget=8)
        )
        p.build(_project(tmp_path))
        kept = p.reduce(self.QUERY).original_nodes
        assert all(r.qualified_name in kept for r in p.retrieve(self.QUERY))


class TestIncrementalCentrality:
    def test_reload_after_edit_matches_fresh_rebuild(self, tmp_path):
        root = _project(tmp_path)
        cfg = PipelineConfig(enable_clustering=False)
        state.open_pipeline(root, config=cfg)
        (root / "tokens.py").write_text(
            TOKENS + "\ndef refresh(token):\n    return validate_token(token)\n"
        )

        loaded, opened = state.open_pipeline(root, config=cfg)
        fresh, _ = state.open_pipeline(root, config=cfg, rebuild=True, save=False)

        assert opened.source == "loaded" and opened.reparsed == 1
        assert set(loaded._centrality_scores) == set(fresh._centrality_scores)
        for name, score in fresh._centrality_scores.items():
            assert loaded._centrality_scores[name].composite_score == pytest.approx(
                score.composite_score
            )
        query = "validate token"
        assert [r.qualified_name for r in loaded.retrieve(query)] == [
            r.qualified_name for r in fresh.retrieve(query)
        ]
