"""CodeCortex — adaptive semantic intelligence engine for code understanding."""

from codecortex.config import CodeCortexConfig, find_config
from codecortex.config import load as load_config
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
