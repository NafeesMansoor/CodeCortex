"""Semantic label generation for code clusters.

Derives human-readable labels by analyzing the most common tokens
and module patterns across cluster member names.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from clustering.semantic_clusters import Cluster


_STOP_WORDS = frozenset({
    "self", "cls", "args", "kwargs", "result", "data", "value",
    "item", "items", "obj", "obj", "get", "set", "add", "the",
    "for", "with", "and", "not", "is", "in", "of", "to", "a",
    "node", "nodes", "edge", "edges",
})


def _tokenize(name: str) -> list[str]:
    """Split qualified name into lowercase words."""
    # Drop module path prefix (take last two segments)
    parts = name.rsplit(".", maxsplit=2)
    tokens: list[str] = []
    for part in parts:
        # Split on underscores and camelCase
        sub = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", part)
        sub = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", sub)
        tokens.extend(t.lower() for t in sub.split("_") if t)
    return [t for t in tokens if t and t not in _STOP_WORDS and len(t) > 1]


def _module_prefix(name: str) -> Optional[str]:
    """Extract top-level module from a qualified name."""
    parts = name.split(".")
    if len(parts) >= 2:
        return parts[0]
    return None


class ClusterLabeler:
    """Generates concise labels for Cluster objects.

    Labels are built from the top recurring tokens in member names,
    optionally qualified with a shared module prefix.

    Usage:
        labeler = ClusterLabeler(max_tokens=3)
        labeler.label_all(result.clusters)
    """

    def __init__(self, max_tokens: int = 3):
        self.max_tokens = max_tokens

    def label(self, cluster: Cluster) -> str:
        """Generate a label string for one cluster. Updates cluster.label in-place."""
        if not cluster.members:
            cluster.label = f"cluster_{cluster.cluster_id}"
            return cluster.label

        token_counter: Counter = Counter()
        module_counter: Counter = Counter()

        for name in cluster.members:
            tokens = _tokenize(name)
            token_counter.update(tokens)
            mod = _module_prefix(name)
            if mod:
                module_counter[mod] += 1

        top_tokens = [t for t, _ in token_counter.most_common(self.max_tokens + 4)
                      if t not in _STOP_WORDS][:self.max_tokens]

        # Find dominant module (>= 50% of members)
        dominant_mod = None
        threshold = len(cluster.members) * 0.5
        for mod, count in module_counter.most_common(1):
            if count >= threshold:
                dominant_mod = mod
                break

        if top_tokens:
            label = " ".join(top_tokens)
            if dominant_mod and dominant_mod not in label:
                label = f"{dominant_mod}: {label}"
        else:
            label = f"cluster_{cluster.cluster_id}"

        cluster.label = label
        return label

    def label_all(self, clusters: list[Cluster]) -> None:
        """Label all clusters in-place."""
        for cluster in clusters:
            self.label(cluster)
