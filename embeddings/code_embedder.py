"""Code-aware embedding text generator.

Converts a CPGNode into a rich text representation that captures:
  - Symbol kind and name
  - Parent class / module context
  - Parameters and return type (if known)
  - Docstring / summary
  - File-path module context
  - Framework hints (endpoint path, HTTP method)

The generated text is fed to the embedding provider to produce a vector
that captures semantic meaning rather than just the symbol name.

Usage:
    embedder = CodeEmbedder()
    text = embedder.embed_text(node)           # single node
    texts = embedder.embed_texts(store.all_nodes())  # batch
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from core.types import NodeKind
from graph.schema import CPGNode


# Kinds we always want to embed (structural landmarks)
_EMBED_KINDS = frozenset({
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CLASS,
    NodeKind.INTERFACE,
    NodeKind.ENDPOINT,
    NodeKind.TEST,
    NodeKind.MODULE,
    NodeKind.FILE,
    NodeKind.ENUM,
    NodeKind.STRUCT,
})

# Kinds we skip — too low-level for meaningful embeddings
_SKIP_KINDS = frozenset({
    NodeKind.VARIABLE,
    NodeKind.PARAMETER,
    NodeKind.CALLSITE,
    NodeKind.CONSTANT,
})


class CodeEmbedder:
    """Generates embedding-ready text from CPGNode objects.

    Text format:
        <kind> <name> [in <parent>] [<params>] [returns <type>]
        <module_path>
        [endpoint: <method> <path>]
        [<docstring>]
    """

    def should_embed(self, node: CPGNode) -> bool:
        """Return True if this node should be embedded."""
        return node.kind not in _SKIP_KINDS

    def embed_text(self, node: CPGNode) -> str:
        """Build embedding text for a single node."""
        parts: list[str] = []

        # Kind + name
        kind_label = _kind_label(node.kind)
        name = _camel_to_words(node.name)
        parts.append(f"{kind_label} {name}")

        # Parent context
        if node.parent_qualified:
            parent_name = node.parent_qualified.rsplit(".", 1)[-1]
            parts.append(f"in {_camel_to_words(parent_name)}")

        # Parameters
        if node.params:
            param_str = " ".join(
                _camel_to_words(p) for p in node.params if p not in ("self", "cls", "this")
            )
            if param_str:
                parts.append(f"params {param_str}")

        # Return type
        if node.return_type:
            parts.append(f"returns {node.return_type}")

        # Module path from file
        module = _module_from_path(node.file_path)
        if module:
            parts.append(module)

        # Endpoint metadata
        if node.kind == NodeKind.ENDPOINT and node.extra:
            method = node.extra.get("http_method", "")
            path = node.extra.get("path", "")
            framework = node.extra.get("framework", "")
            if method or path:
                parts.append(f"endpoint {method} {path} {framework}".strip())

        # Docstring (first sentence only)
        if node.docstring:
            first_sentence = node.docstring.strip().split(".")[0][:120]
            if first_sentence:
                parts.append(first_sentence)

        return " ".join(parts)

    def embed_texts(
        self, nodes: list[CPGNode]
    ) -> list[tuple[str, str]]:
        """Return [(qualified_name, text), ...] for all embeddable nodes."""
        return [
            (node.qualified_name, self.embed_text(node))
            for node in nodes
            if self.should_embed(node)
        ]

    def embed_texts_for_store(
        self, nodes: list[CPGNode]
    ) -> tuple[list[str], list[str]]:
        """Return (qualified_names, texts) lists for batch embedding."""
        names, texts = [], []
        for node in nodes:
            if self.should_embed(node):
                names.append(node.qualified_name)
                texts.append(self.embed_text(node))
        return names, texts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kind_label(kind: NodeKind) -> str:
    labels = {
        NodeKind.FUNCTION: "function",
        NodeKind.METHOD: "method",
        NodeKind.CLASS: "class",
        NodeKind.INTERFACE: "interface",
        NodeKind.ENDPOINT: "api endpoint",
        NodeKind.TEST: "test function",
        NodeKind.MODULE: "module",
        NodeKind.FILE: "file",
        NodeKind.ENUM: "enum",
        NodeKind.STRUCT: "struct",
        NodeKind.TYPE: "type",
        NodeKind.NAMESPACE: "namespace",
    }
    return labels.get(kind, kind.value.lower())


def _camel_to_words(name: str) -> str:
    """Convert CamelCase and snake_case to space-separated words."""
    # CamelCase → Camel Case
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    name = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", name)
    # snake_case → snake case
    name = name.replace("_", " ").replace("-", " ")
    return name.lower().strip()


def _module_from_path(file_path: str) -> str:
    """Extract a dotted module name from a file path."""
    if not file_path:
        return ""
    p = Path(file_path)
    # Strip common root prefixes
    parts = p.with_suffix("").parts
    for i, part in enumerate(parts):
        if part in ("src", "lib", "app", "pkg"):
            parts = parts[i:]
            break
    return " ".join(parts[-3:])  # last 3 path segments
