"""CodeCortex — adaptive semantic intelligence engine for code understanding."""

from pipeline.context_builder import CodeCortexPipeline, PipelineConfig, PipelineStats
from codecortex.config import CodeCortexConfig, load as load_config, find_config
from core.types import NodeKind, EdgeKind, NodeInfo, EdgeInfo, SourceRange, ParseResult
from graph.schema import CPGNode, CPGEdge

__all__ = [
    # Pipeline
    "CodeCortexPipeline",
    "PipelineConfig",
    "PipelineStats",
    # Configuration
    "CodeCortexConfig",
    "load_config",
    "find_config",
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

__version__ = "1.0.1"
__author__ = "Prof. Dr. Nafees Mansoor"
