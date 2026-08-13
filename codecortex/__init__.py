"""CodeCortex — adaptive semantic intelligence engine for code understanding."""

from codecortex.compat import require_version
from codecortex.config import CodeCortexConfig, find_config
from codecortex.config import load as load_config
from codecortex.version import __version__
from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult, SourceRange
from graph.schema import CPGEdge, CPGNode
from pipeline.context_builder import CodeCortexPipeline, PipelineConfig, PipelineStats

__all__ = [
    # Pipeline
    "CodeCortexPipeline",
    "PipelineConfig",
    "PipelineStats",
    # Configuration
    "CodeCortexConfig",
    "load_config",
    "find_config",
    # Version / compatibility
    "__version__",
    "require_version",
    # Core types
    "NodeKind",
    "EdgeKind",
    "NodeInfo",
    "EdgeInfo",
    "SourceRange",
    "ParseResult",
    # Graph schema
    "CPGNode",
    "CPGEdge",
]

__author__ = "Prof. Dr. Nafees Mansoor"
