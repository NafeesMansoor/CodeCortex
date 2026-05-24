"""Louvain community detection for semantic graph clustering.

Uses graph structure (call / inheritance / containment topology) rather than
raw embedding vectors, so it produces meaningful clusters even with stub
embeddings and works on the pruned semantic graph alone.

Algorithm: Louvain (python-louvain / community package)
  - Maximises modularity Q of the partition
  - O(n log n) average, runs in <1s on graphs with ~700 nodes

The resulting communities group functions / classes that are densely
interconnected — which corresponds to semantic modules, features, and layers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Edge-type weights used when building the networkx graph.
# Higher weight → nodes are more likely to end up in the same community.
_EDGE_WEIGHTS = {
    "calls": 1.0,
    "inherits": 1.0,
    "tests": 0.8,
    "contains": 0.5,
    "imports_from": 0.4,
    "parameter_of": 0.3,
    "controls": 0.15,
    "reads": 0.1,
    "writes": 0.1,
}


@dataclass
class LouvainCluster:
    cluster_id: int
    members: list[str] = field(default_factory=list)
    label: str = ""
    modularity_contribution: float = 0.0

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass
class LouvainResult:
    clusters: list[LouvainCluster] = field(default_factory=list)
    modularity: float = 0.0
    node_to_cluster: dict[str, int] = field(default_factory=dict)

    @property
    def n_clusters(self) -> int:
        return len(self.clusters)


class LouvainClusterer:
    """Partition a graph store into semantic communities via Louvain."""

    def __init__(self, resolution: float = 1.0, random_state: int = 42):
        self.resolution = resolution
        self.random_state = random_state

    def cluster(self, store) -> LouvainResult:
        """Run Louvain on the graph store. Returns LouvainResult."""
        import networkx as nx

        try:
            import community as community_louvain
        except ImportError:
            raise RuntimeError(
                "python-louvain is required. Install: pip install python-louvain"
            )

        G = self._build_graph(store)
        if G.number_of_nodes() < 2:
            return LouvainResult()

        partition = community_louvain.best_partition(
            G,
            weight="weight",
            resolution=self.resolution,
            random_state=self.random_state,
        )
        modularity = community_louvain.modularity(partition, G, weight="weight")

        # Group nodes by community
        community_map: dict[int, list[str]] = {}
        for node, comm_id in partition.items():
            community_map.setdefault(comm_id, []).append(node)

        clusters = []
        node_to_cluster: dict[str, int] = {}
        for new_id, (comm_id, members) in enumerate(
            sorted(community_map.items(), key=lambda x: -len(x[1]))
        ):
            cluster = LouvainCluster(
                cluster_id=new_id,
                members=members,
                label=self._label(members, store),
            )
            clusters.append(cluster)
            for m in members:
                node_to_cluster[m] = new_id

        logger.info(
            "Louvain: %d communities, modularity=%.4f", len(clusters), modularity
        )
        return LouvainResult(
            clusters=clusters,
            modularity=modularity,
            node_to_cluster=node_to_cluster,
        )

    def _build_graph(self, store) -> "nx.Graph":
        import networkx as nx

        G = nx.Graph()
        for node in store.all_nodes():
            G.add_node(node.qualified_name)

        for edge in store.all_edges():
            kind_str = edge.kind.value.lower()
            w = _EDGE_WEIGHTS.get(kind_str, 0.1)
            src, tgt = edge.source, edge.target
            if G.has_edge(src, tgt):
                G[src][tgt]["weight"] = G[src][tgt]["weight"] + w
            else:
                G.add_edge(src, tgt, weight=w)

        return G

    @staticmethod
    def _label(members: list[str], store) -> str:
        """Derive a label from the dominant file/module among cluster members."""
        from collections import Counter

        file_counts: Counter = Counter()
        for qname in members:
            node = store.get_node(qname)
            if node and node.file_path:
                # Use the stem of the filename as a label candidate
                import os
                stem = os.path.splitext(os.path.basename(node.file_path))[0]
                file_counts[stem] += 1

        if file_counts:
            dominant = file_counts.most_common(1)[0][0]
            return f"{dominant} ({len(members)} nodes)"
        return f"community ({len(members)} nodes)"
