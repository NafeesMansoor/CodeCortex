"""Code Property Graph schema: node and edge definitions for AST + CFG + DFG."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.types import EdgeKind, NodeKind, SourceRange


@dataclass
class CPGNode:
    """A node in the Code Property Graph.

    Represents a structural code entity: file, class, function, variable, etc.
    """

    qualified_name: str       # Globally unique identifier (e.g., "pkg.Class.method")
    kind: NodeKind
    name: str
    file_path: str
    language: str = ""
    range: Optional[SourceRange] = None
    parent_qualified: Optional[str] = None
    modifiers: list[str] = field(default_factory=list)  # public, static, async, …
    params: list[str] = field(default_factory=list)
    return_type: Optional[str] = None
    is_test: bool = False
    docstring: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.qualified_name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CPGNode):
            return NotImplemented
        return self.qualified_name == other.qualified_name


@dataclass
class CPGEdge:
    """A directed edge in the Code Property Graph."""

    kind: EdgeKind
    source: str   # Source node qualified_name
    target: str   # Target node qualified_name
    file_path: str = ""
    confidence: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((self.kind, self.source, self.target))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CPGEdge):
            return NotImplemented
        return (self.kind, self.source, self.target) == (other.kind, other.source, other.target)


# Convenience aliases matching the plan's node-type vocabulary
FILE_NODE = NodeKind.FILE
CLASS_NODE = NodeKind.CLASS
FUNCTION_NODE = NodeKind.FUNCTION
INTERFACE_NODE = NodeKind.INTERFACE
MODULE_NODE = NodeKind.MODULE
VARIABLE_NODE = NodeKind.VARIABLE
TEST_NODE = NodeKind.TEST
