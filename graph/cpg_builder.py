"""Code Property Graph builder — Phase 3 enhanced.

Converts ParseResult into CPGNode/CPGEdge objects and populates a GraphStore.
Integrates:
  - AST structure (parser-extracted nodes and edges)
  - Containment edges (file → class → method)
  - TESTS edges (test functions → their subjects)
  - CFG: CONTROLS edges (control-flow branches) via CFGBuilder
  - DFG: READS/WRITES edges (variable data flow) via DFGBuilder
  - ENDPOINT nodes (FastAPI/Laravel/Next.js) via EndpointDetector
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.types import EdgeInfo, EdgeKind, NodeInfo, NodeKind, ParseResult
from graph.graph_store import GraphStore
from graph.schema import CPGEdge, CPGNode

logger = logging.getLogger(__name__)


class CPGBuilder:
    """Builds and incrementally updates a Code Property Graph.

    Usage:
        store = GraphStore(":memory:")
        builder = CPGBuilder(store)
        result = python_parser.parse(source, "path/to/file.py")
        builder.ingest(result)

    Set enable_cfg / enable_dfg / enable_endpoints = False to skip
    those passes if performance is more important than completeness.
    """

    def __init__(
        self,
        store: GraphStore,
        enable_cfg: bool = True,
        enable_dfg: bool = True,
        enable_endpoints: bool = True,
    ):
        self.store = store
        self.enable_cfg = enable_cfg
        self.enable_dfg = enable_dfg
        self.enable_endpoints = enable_endpoints

        # Lazy-import builders (tree-sitter not required if flags are off)
        self._cfg: object = None
        self._dfg: object = None
        self._ep: object = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(self, result: ParseResult) -> None:
        """Ingest a ParseResult into the CPG."""
        if not result.is_success():
            logger.warning("Skipping %s — parse errors: %s", result.file_path, result.errors)
            return

        self._ensure_file_node(result.file_path, result.language)

        nodes = [self._node_info_to_cpg(n) for n in result.nodes]
        edges = [self._edge_info_to_cpg(e) for e in result.edges]

        # Structural containment edges
        edges.extend(self._containment_edges(result))

        # TESTS edges: test functions → their subjects
        edges.extend(self._tests_edges(result))

        self.store.add_nodes(nodes)
        self.store.add_edges(edges)

        # Phase 3 passes — need the raw tree from the parser
        tree = getattr(result, "_tree", None)
        source = getattr(result, "_source", None)
        if tree is not None and source is not None:
            func_map = {
                n.name: n.qualified_name
                for n in result.nodes
                if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.TEST)
            }

            if self.enable_cfg:
                cfg_edges = self._cfg_builder().process(tree, source, result.file_path, func_map)
                self.store.add_edges(cfg_edges)

            if self.enable_dfg:
                var_nodes, dfg_edges = self._dfg_builder().process(
                    tree, source, result.file_path, func_map
                )
                self.store.add_nodes(var_nodes)
                self.store.add_edges(dfg_edges)

            if self.enable_endpoints:
                ep_nodes = self._ep_detector().detect(tree, source, result.file_path, func_map)
                if ep_nodes:
                    self.store.add_nodes(ep_nodes)
                    # Wire endpoint → handler via CONTAINS
                    ep_edges = [
                        CPGEdge(
                            kind=EdgeKind.CONTAINS,
                            source=ep.qualified_name,
                            target=ep.extra.get("handler", ""),
                            file_path=result.file_path,
                        )
                        for ep in ep_nodes
                        if ep.extra.get("handler")
                    ]
                    self.store.add_edges(ep_edges)

        logger.debug("Ingested %s: %d nodes, %d edges", result.file_path, len(nodes), len(edges))

    def ingest_many(self, results: list[ParseResult]) -> None:
        for result in results:
            self.ingest(result)

    def remove_file(self, file_path: str) -> None:
        """Remove all nodes and edges associated with a file."""
        nodes = self.store.get_nodes_by_file(file_path)
        qualified_names = {n.qualified_name for n in nodes}

        all_edges = self.store.all_edges()
        stale = [
            e
            for e in all_edges
            if e.source in qualified_names
            or e.target in qualified_names
            or e.file_path == file_path
        ]

        with self.store._tx() as cur:
            for e in stale:
                cur.execute(
                    "DELETE FROM edges WHERE kind=? AND source=? AND target=?",
                    (e.kind.value, e.source, e.target),
                )
            for name in qualified_names:
                cur.execute("DELETE FROM nodes WHERE qualified_name=?", (name,))
            cur.execute("DELETE FROM nodes WHERE qualified_name=?", (file_path,))

        logger.debug("Removed file from CPG: %s", file_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _containment_edges(self, result: ParseResult) -> list[CPGEdge]:
        edges = []
        for node in result.nodes:
            if node.parent_name is None:
                edges.append(
                    CPGEdge(
                        kind=EdgeKind.CONTAINS,
                        source=result.file_path,
                        target=node.qualified_name,
                        file_path=result.file_path,
                    )
                )
            else:
                edges.append(
                    CPGEdge(
                        kind=EdgeKind.CONTAINS,
                        source=node.parent_name,
                        target=node.qualified_name,
                        file_path=result.file_path,
                    )
                )
        return edges

    def _tests_edges(self, result: ParseResult) -> list[CPGEdge]:
        """Emit TESTS edges: test_foo() → foo() by convention."""
        edges = []
        test_nodes = [n for n in result.nodes if n.is_test or n.kind == NodeKind.TEST]
        non_test = {
            n.name: n.qualified_name
            for n in result.nodes
            if not n.is_test and n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        }

        for test in test_nodes:
            # test_create_user → create_user
            subject = test.name
            for prefix in ("test_", "test"):
                if subject.startswith(prefix):
                    candidate = subject[len(prefix) :]
                    if candidate in non_test:
                        edges.append(
                            CPGEdge(
                                kind=EdgeKind.TESTS,
                                source=test.qualified_name,
                                target=non_test[candidate],
                                file_path=result.file_path,
                                confidence=0.7,
                            )
                        )
                        break
        return edges

    def _ensure_file_node(self, file_path: str, language: str) -> None:
        if self.store.get_node(file_path) is None:
            self.store.add_node(
                CPGNode(
                    qualified_name=file_path,
                    kind=NodeKind.FILE,
                    name=Path(file_path).name,
                    file_path=file_path,
                    language=language,
                )
            )

    def _node_info_to_cpg(self, node: NodeInfo) -> CPGNode:
        return CPGNode(
            qualified_name=node.qualified_name,
            kind=node.kind,
            name=node.name,
            file_path=node.file_path,
            language=node.language,
            range=node.range,
            parent_qualified=node.parent_name,
            modifiers=node.modifiers or [],
            params=node.params or [],
            return_type=node.return_type,
            is_test=node.is_test,
            docstring=node.docstring,
            extra=node.extra,
        )

    def _edge_info_to_cpg(self, edge: EdgeInfo) -> CPGEdge:
        return CPGEdge(
            kind=edge.kind,
            source=edge.source_qualified,
            target=edge.target_qualified,
            file_path=edge.file_path,
            confidence=edge.confidence,
            extra=edge.extra,
        )

    def _cfg_builder(self):
        if self._cfg is None:
            from graph.cfg_builder import CFGBuilder

            self._cfg = CFGBuilder(self.store)
        return self._cfg

    def _dfg_builder(self):
        if self._dfg is None:
            from graph.dfg_builder import DFGBuilder

            self._dfg = DFGBuilder(self.store)
        return self._dfg

    def _ep_detector(self):
        if self._ep is None:
            from graph.endpoint_detector import EndpointDetector

            self._ep = EndpointDetector()
        return self._ep
