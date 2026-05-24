"""YAML configuration loader for CodeCortex V0.1.

Expected file layout (codecortex.yaml):

    codecortex:
      bfs_depth: 3
      max_nodes_per_query: 500
      enable_cpg: true
      enable_embeddings: true
      enable_clustering: true
      on_demand_cfg: true
      on_demand_dfg: true
      centrality_ranking: true
      embedding_backend: stub   # stub | local | openai
      embedding_dimensions: 384
      min_cluster_size: 5
      retrieval_top_k: 20
      token_budget: 4000
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CodeCortexConfig:
    """Flat config derived from codecortex.yaml."""

    bfs_depth: int = 3
    max_nodes_per_query: int = 500

    enable_cpg: bool = True
    enable_embeddings: bool = True
    enable_clustering: bool = True
    centrality_ranking: bool = True

    on_demand_cfg: bool = False
    on_demand_dfg: bool = False

    embedding_backend: str = "stub"
    embedding_dimensions: int = 384
    min_cluster_size: int = 5

    retrieval_top_k: int = 20
    token_budget: int = 4000
    min_semantic_score: float = 0.0

    def to_pipeline_config(self):
        from pipeline.context_builder import PipelineConfig

        return PipelineConfig(
            enable_cfg=self.enable_cpg and not self.on_demand_cfg,
            enable_dfg=self.enable_cpg and not self.on_demand_dfg,
            enable_endpoints=self.enable_cpg,
            embedding_backend=self.embedding_backend if self.enable_embeddings else "stub",
            embedding_dimensions=self.embedding_dimensions,
            enable_clustering=self.enable_clustering,
            min_cluster_size=self.min_cluster_size,
            semantic_only=self.centrality_ranking,
            on_demand_cfg=self.on_demand_cfg,
            on_demand_dfg=self.on_demand_dfg,
            retrieval_top_k=self.retrieval_top_k,
            retrieval_token_budget=self.token_budget,
            retrieval_min_semantic=self.min_semantic_score,
        )


def load(path: str | Path) -> CodeCortexConfig:
    """Load a codecortex.yaml file and return CodeCortexConfig."""
    path = Path(path)
    if not path.exists():
        return CodeCortexConfig()

    try:
        import yaml
    except ImportError:
        print("PyYAML not installed — using default config. Run: pip install pyyaml", file=sys.stderr)
        return CodeCortexConfig()

    raw = yaml.safe_load(path.read_text()) or {}
    section = raw.get("codecortex", raw)

    mapping = {
        "bfs_depth": "bfs_depth",
        "max_nodes_per_query": "max_nodes_per_query",
        "enable_cpg": "enable_cpg",
        "enable_embeddings": "enable_embeddings",
        "enable_clustering": "enable_clustering",
        "centrality_ranking": "centrality_ranking",
        "on_demand_cfg": "on_demand_cfg",
        "on_demand_dfg": "on_demand_dfg",
        "embedding_backend": "embedding_backend",
        "embedding_dimensions": "embedding_dimensions",
        "min_cluster_size": "min_cluster_size",
        "retrieval_top_k": "retrieval_top_k",
        "token_budget": "token_budget",
        "min_semantic_score": "min_semantic_score",
    }
    kwargs = {attr: section[key] for key, attr in mapping.items() if key in section}
    return CodeCortexConfig(**kwargs)


def find_config(start: Path) -> Optional[Path]:
    """Walk up from start to find codecortex.yaml."""
    for parent in [start, *start.parents]:
        candidate = parent / "codecortex.yaml"
        if candidate.exists():
            return candidate
    return None
