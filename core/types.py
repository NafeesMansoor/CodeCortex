"""Core data types and shared models for CodeCortex.

These types bridge between the parser, graph store, and analysis layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class NodeKind(str, Enum):
    """Standard node types across all languages."""

    FILE = "File"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"  # Class method (distinct from free function)
    TYPE = "Type"
    CONSTANT = "Constant"
    VARIABLE = "Variable"
    PARAMETER = "Parameter"  # Function parameter
    TEST = "Test"
    INTERFACE = "Interface"
    MODULE = "Module"
    NAMESPACE = "Namespace"
    ENUM = "Enum"
    STRUCT = "Struct"
    ENDPOINT = "Endpoint"  # HTTP/API route handler
    CALLSITE = "Callsite"  # Explicit call-site node


class EdgeKind(str, Enum):
    """Standard edge types for code relationships."""

    CALLS = "CALLS"  # Direct function/method calls
    IMPORTS_FROM = "IMPORTS_FROM"  # Import/require relationships
    INHERITS = "INHERITS"  # Class inheritance
    IMPLEMENTS = "IMPLEMENTS"  # Interface implementation
    CONTAINS = "CONTAINS"  # Structural containment (class→method)
    TESTED_BY = "TESTED_BY"  # Test relationship (tested → test)
    TESTS = "TESTS"  # Test function → function under test
    DEPENDS_ON = "DEPENDS_ON"  # Dependency edge
    REFERENCES = "REFERENCES"  # General reference
    USES = "USES"  # Uses/instantiates type
    DEFINES = "DEFINES"  # Symbol definition
    EXTENDS = "EXTENDS"  # Prototype/mixin extension
    # Phase 3 — CFG/DFG
    CONTROLS = "CONTROLS"  # CFG: condition/loop controls a block
    READS = "READS"  # DFG: function reads a variable
    WRITES = "WRITES"  # DFG: function writes/assigns a variable
    PARAMETER_OF = "PARAMETER_OF"  # Parameter belongs to function


@dataclass
class Position:
    """Code location: file, line, column."""

    file_path: str
    line: int
    column: int = 0

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}:{self.column}"


@dataclass
class SourceRange:
    """Range of source code: start line to end line."""

    start_line: int
    end_line: int

    def __str__(self) -> str:
        if self.start_line == self.end_line:
            return f"L{self.start_line}"
        return f"L{self.start_line}-L{self.end_line}"


@dataclass
class NodeInfo:
    """Extracted code node (entity) from parser.

    Represents a structural unit: function, class, type, constant, etc.
    """

    kind: NodeKind
    name: str
    file_path: str
    range: SourceRange
    language: str = ""
    qualified_name: str = ""  # e.g., "module.Class.method"
    parent_name: Optional[str] = None  # Enclosing class/module
    params: Optional[list[str]] = None  # Function parameters
    return_type: Optional[str] = None
    modifiers: Optional[list[str]] = None  # public, static, async, etc.
    is_test: bool = False
    docstring: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)  # Language-specific data

    def __post_init__(self):
        if not self.qualified_name:
            if self.parent_name:
                self.qualified_name = f"{self.parent_name}.{self.name}"
            else:
                self.qualified_name = self.name


@dataclass
class EdgeInfo:
    """Extracted code relationship (edge) from parser.

    Represents a dependency, call, inheritance, or reference between nodes.
    """

    kind: EdgeKind
    source_qualified: str  # Qualified name of source node
    target_qualified: str  # Qualified name of target node
    file_path: str  # File where edge is declared
    source_node_kind: Optional[NodeKind] = None
    target_node_kind: Optional[NodeKind] = None
    position: Optional[Position] = None
    confidence: float = 1.0  # 0-1, lower for inferred edges
    confidence_tier: str = "EXTRACTED"  # EXTRACTED, INFERRED, etc.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    """Complete output from a language parser."""

    language: str
    file_path: str
    nodes: list[NodeInfo] = field(default_factory=list)
    edges: list[EdgeInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Phase 3: raw tree-sitter tree and source for CFG/DFG/endpoint passes
    _tree: Any = field(default=None, repr=False, compare=False)
    _source: Any = field(default=None, repr=False, compare=False)

    def is_success(self) -> bool:
        return len(self.errors) == 0

    def total_nodes(self) -> int:
        return len(self.nodes)

    def total_edges(self) -> int:
        return len(self.edges)


@dataclass
class TraversalConfig:
    """Configuration for graph traversal queries."""

    max_depth: int = 5
    max_nodes: int = 10000
    direction: str = "outbound"  # outbound, inbound, bidirectional
    edge_filter: Optional[list[EdgeKind]] = None
    node_filter: Optional[list[NodeKind]] = None
    memoize: bool = True


class ConfidenceTier(str, Enum):
    """Confidence levels for extracted code relationships."""

    EXTRACTED = "EXTRACTED"  # Direct from AST
    INFERRED = "INFERRED"  # Via type analysis
    HEURISTIC = "HEURISTIC"  # Via pattern matching
    LOW = "LOW"  # Low confidence edge
