"""Export CodeCortex pipeline state to a visualization-ready JSON format.

Operates entirely on already-computed pipeline outputs — does NOT trigger
any re-analysis or modify internal pipeline state.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Stable color palette: one per cluster index (cycles for >12 clusters)
_CLUSTER_COLORS = [
    "#58A6FF", "#3FB950", "#FF7B72", "#D2A8FF", "#FFA657",
    "#79C0FF", "#56D364", "#FF6E40", "#E3B341", "#BC8CFF",
    "#89DDFF", "#F78166",
]


def _cluster_color(cluster_id: int) -> str:
    if cluster_id < 0:
        return "#8B949E"
    return _CLUSTER_COLORS[cluster_id % len(_CLUSTER_COLORS)]


def _node_base_color(kind: str) -> str:
    return {
        "File":      "#1F6FEB",
        "Module":    "#388BFD",
        "Namespace": "#388BFD",
        "Class":     "#3FB950",
        "Struct":    "#3FB950",
        "Interface": "#79C0FF",
        "Function":  "#F78166",
        "Method":    "#FF7B72",
        "Endpoint":  "#FF6E40",
        "Test":      "#D2A8FF",
        "Constant":  "#FFA657",
        "Enum":      "#FFA657",
        "Type":      "#79C0FF",
        "Variable":  "#6E7681",
        "Parameter": "#6E7681",
        "Callsite":  "#6E7681",
    }.get(kind, "#8B949E")


class GraphExporter:
    """Converts pipeline state into a serializable dict for the visualization layer."""

    def __init__(self, pipeline):
        self._pipeline = pipeline

    def export(self, project_name: str = "") -> dict[str, Any]:
        store = self._pipeline.graph_store
        if store is None:
            return {"metadata": {}, "nodes": [], "edges": [], "clusters": []}

        centrality = self._pipeline._centrality_scores or {}
        cluster_memberships = self._pipeline._build_cluster_memberships()

        # Resolve cluster labels
        cluster_labels: dict[int, str] = {}
        if self._pipeline._cluster_result is not None:
            for c in self._pipeline._cluster_result.clusters:
                cluster_labels[c.cluster_id] = c.label or f"cluster-{c.cluster_id}"
        if self._pipeline._louvain_result is not None:
            for cid, members in enumerate(getattr(self._pipeline._louvain_result, "communities", [])):
                if cid not in cluster_labels:
                    cluster_labels[cid] = f"community-{cid}"

        # Build cluster summary
        cluster_sizes: dict[int, int] = {}
        for cid in cluster_memberships.values():
            cluster_sizes[cid] = cluster_sizes.get(cid, 0) + 1

        clusters = [
            {
                "id": cid,
                "label": cluster_labels.get(cid, f"cluster-{cid}"),
                "size": cluster_sizes.get(cid, 0),
                "color": _cluster_color(cid),
            }
            for cid in sorted(cluster_sizes)
        ]

        # Compute centrality stats for normalization
        scores_list = [s.composite_score for s in centrality.values()] if centrality else []
        max_score = max(scores_list, default=1.0) or 1.0
        max_fan_in = max((s.fan_in for s in centrality.values()), default=1) if centrality else 1

        # Top 10% are hotspots
        if scores_list:
            threshold = sorted(scores_list)[int(len(scores_list) * 0.90)]
        else:
            threshold = float("inf")

        all_nodes = store.all_nodes()
        nodes = []
        for node in all_nodes:
            score_obj = centrality.get(node.qualified_name)
            composite = score_obj.composite_score if score_obj else 0.0
            fan_in = score_obj.fan_in if score_obj else 0
            fan_out = score_obj.fan_out if score_obj else 0
            cluster_id = cluster_memberships.get(node.qualified_name, -1)
            is_hotspot = composite >= threshold and threshold != float("inf")

            # Node size: log-scaled by centrality (20–60 px range)
            norm = composite / max_score if max_score else 0
            size = 20 + math.log1p(norm * 10) / math.log1p(10) * 40

            nodes.append({
                "id": node.qualified_name,
                "name": node.name,
                "kind": node.kind.value if hasattr(node.kind, "value") else str(node.kind),
                "file": node.file_path,
                "language": node.language or "",
                "line": node.range.start_line if node.range else 0,
                "cluster_id": cluster_id,
                "cluster_label": cluster_labels.get(cluster_id, ""),
                "centrality": round(composite, 6),
                "fan_in": fan_in,
                "fan_out": fan_out,
                "is_hotspot": is_hotspot,
                "is_test": bool(getattr(node, "is_test", False)),
                "docstring": (node.docstring or "")[:200],
                "color": _node_base_color(
                    node.kind.value if hasattr(node.kind, "value") else str(node.kind)
                ),
                "cluster_color": _cluster_color(cluster_id),
                "size": round(size, 1),
            })

        all_edges = store.all_edges()
        edges = [
            {
                "id": f"e_{i}",
                "source": edge.source,
                "target": edge.target,
                "kind": edge.kind.value if hasattr(edge.kind, "value") else str(edge.kind),
                "confidence": round(getattr(edge, "confidence", 1.0), 3),
            }
            for i, edge in enumerate(all_edges)
        ]

        node_count = len(nodes)
        edge_count = len(edges)
        hotspot_count = sum(1 for n in nodes if n["is_hotspot"])

        return {
            "metadata": {
                "project": project_name or Path(store._db_path).stem if hasattr(store, "_db_path") else "project",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "version": "1.0.0",
                "node_count": node_count,
                "edge_count": edge_count,
                "cluster_count": len(clusters),
                "hotspot_count": hotspot_count,
                "languages": list({n["language"] for n in nodes if n["language"]}),
            },
            "nodes": nodes,
            "edges": edges,
            "clusters": clusters,
        }


def export_pipeline(pipeline, project_name: str = "") -> dict[str, Any]:
    """Convenience function — export a built pipeline to visualization JSON."""
    return GraphExporter(pipeline).export(project_name=project_name)
