"""Framework endpoint detector.

Identifies HTTP API endpoint handlers across frameworks:
  - Python: FastAPI (@app.get, @router.post, @app.route), Flask
  - PHP: Laravel (Route::get, Route::post in routes/*.php)
  - TypeScript/JavaScript: Next.js (pages/api/, app/api/ exports)
  - Node.js: Express (app.get, router.post)

Produces ENDPOINT-kind nodes with metadata in the `extra` field:
  {
    "http_method": "GET",
    "path": "/users/{id}",
    "framework": "fastapi"
  }

Usage:
    detector = EndpointDetector()
    endpoints = detector.detect(tree, source, file_path, existing_nodes)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from core.source_text import node_text
from core.types import NodeKind, SourceRange
from graph.schema import CPGNode


def _children(node) -> list:
    return [node.child(i) for i in range(node.child_count())]


logger = logging.getLogger(__name__)

# FastAPI / Flask / Starlette decorator patterns
_PYTHON_ROUTE_PATTERNS = [
    re.compile(r"@\w+\.(get|post|put|patch|delete|head|options)\s*\(", re.I),
    re.compile(r"@\w+\.route\s*\(", re.I),
    re.compile(r"@api_view\s*\(", re.I),
]

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Laravel route patterns
_PHP_ROUTE_PATTERNS = re.compile(
    r"Route::(get|post|put|patch|delete|any)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.I,
)

# Next.js API route: any file under pages/api/ or app/.../route.ts
_NEXTJS_API_PATH = re.compile(r"(pages/api/|app/.+/route\.[jt]sx?)$")


class EndpointDetector:
    """Detects HTTP endpoint handlers across multiple frameworks."""

    def detect(
        self,
        tree,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
    ) -> list[CPGNode]:
        """Return ENDPOINT-kind nodes for all detected endpoints in the file."""
        ext = Path(file_path).suffix.lower()

        if ext == ".py":
            return self._detect_python(tree, source, file_path, func_name_map)
        elif ext == ".php":
            return self._detect_php(source, file_path)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            return self._detect_js_ts(tree, source, file_path, func_name_map)
        return []

    # ------------------------------------------------------------------
    # Python (FastAPI / Flask / Django)
    # ------------------------------------------------------------------

    def _detect_python(
        self, tree, source: str, file_path: str, func_name_map: dict[str, str]
    ) -> list[CPGNode]:
        endpoints: list[CPGNode] = []

        # Walk tree-sitter decorated functions
        self._walk_python(
            tree.root_node(), source, file_path, func_name_map, endpoints, parent_decorators=[]
        )
        return endpoints

    def _walk_python(
        self,
        node,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
        endpoints: list[CPGNode],
        parent_decorators: list[str],
    ) -> None:
        if node.kind() == "decorated_definition":
            decorators = []
            inner = None
            for child in _children(node):
                if child.kind() == "decorator":
                    dec_text = _text(child, source)
                    decorators.append(dec_text)
                elif child.kind() in ("function_definition", "async_function_definition"):
                    inner = child
            if inner:
                ep = self._python_endpoint_from_decorators(
                    inner, source, file_path, func_name_map, decorators
                )
                if ep:
                    endpoints.append(ep)
                # Recurse into function body
                self._walk_python(inner, source, file_path, func_name_map, endpoints, [])
                return

        for child in _children(node):
            self._walk_python(child, source, file_path, func_name_map, endpoints, [])

    def _python_endpoint_from_decorators(
        self,
        func_node,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
        decorators: list[str],
    ) -> Optional[CPGNode]:
        for dec in decorators:
            for pattern in _PYTHON_ROUTE_PATTERNS:
                if pattern.search(dec):
                    http_method = _extract_http_method(dec)
                    route_path = _extract_route_path(dec)
                    func_name = _identifier(func_node, source)
                    qname = func_name_map.get(func_name, func_name)

                    framework = "fastapi"
                    if "flask" in dec.lower() or "@app.route" in dec:
                        framework = "flask"
                    elif "router" in dec.lower():
                        framework = "fastapi"

                    return CPGNode(
                        qualified_name=f"{qname}__endpoint",
                        kind=NodeKind.ENDPOINT,
                        name=func_name,
                        file_path=file_path,
                        range=SourceRange(
                            func_node.start_position().row + 1,
                            func_node.end_position().row + 1,
                        ),
                        extra={
                            "http_method": http_method,
                            "path": route_path,
                            "framework": framework,
                            "handler": qname,
                        },
                    )
        return None

    # ------------------------------------------------------------------
    # PHP (Laravel)
    # ------------------------------------------------------------------

    def _detect_php(self, source: str, file_path: str) -> list[CPGNode]:
        endpoints: list[CPGNode] = []
        text = source if isinstance(source, str) else source.decode()

        for m in _PHP_ROUTE_PATTERNS.finditer(text):
            method = m.group(1).upper()
            path = m.group(2)
            lineno = text[: m.start()].count("\n") + 1
            qname = f"laravel.route.{method.lower()}.{path.strip('/').replace('/', '_')}"
            endpoints.append(
                CPGNode(
                    qualified_name=qname,
                    kind=NodeKind.ENDPOINT,
                    name=path,
                    file_path=file_path,
                    range=SourceRange(lineno, lineno),
                    extra={
                        "http_method": method,
                        "path": path,
                        "framework": "laravel",
                    },
                )
            )
        return endpoints

    # ------------------------------------------------------------------
    # TypeScript / JavaScript (Next.js, Express)
    # ------------------------------------------------------------------

    def _detect_js_ts(
        self,
        tree,
        source: str,
        file_path: str,
        func_name_map: dict[str, str],
    ) -> list[CPGNode]:
        endpoints: list[CPGNode] = []

        # Next.js API route: any exported function from pages/api/ or app/**/route.ts
        if _NEXTJS_API_PATH.search(file_path.replace("\\", "/")):
            for export_name in ["GET", "POST", "PUT", "PATCH", "DELETE", "default", "handler"]:
                qname = func_name_map.get(export_name, export_name)
                if qname != export_name or self._node_exists(qname):
                    endpoints.append(
                        CPGNode(
                            qualified_name=f"{qname}__endpoint",
                            kind=NodeKind.ENDPOINT,
                            name=export_name,
                            file_path=file_path,
                            extra={
                                "http_method": export_name
                                if export_name in {m.upper() for m in _HTTP_METHODS}
                                else "ANY",
                                "path": _nextjs_route_path(file_path),
                                "framework": "nextjs",
                                "handler": qname,
                            },
                        )
                    )

        # Express: app.get / router.post patterns
        text = source if isinstance(source, str) else source.decode()
        express_pat = re.compile(
            r"\b(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]",
            re.I,
        )
        for m in express_pat.finditer(text):
            method = m.group(1).upper()
            path = m.group(2)
            lineno = text[: m.start()].count("\n") + 1
            qname = f"express.route.{method.lower()}.{path.strip('/').replace('/', '_')}"
            endpoints.append(
                CPGNode(
                    qualified_name=qname,
                    kind=NodeKind.ENDPOINT,
                    name=path,
                    file_path=file_path,
                    range=SourceRange(lineno, lineno),
                    extra={
                        "http_method": method,
                        "path": path,
                        "framework": "express",
                    },
                )
            )

        return endpoints

    def _node_exists(self, qname: str) -> bool:
        try:
            return self.store.get_node(qname) is not None  # type: ignore
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_http_method(decorator: str) -> str:
    for method in _HTTP_METHODS:
        if f".{method}" in decorator.lower() or f"'{method}'" in decorator.lower():
            return method.upper()
    return "ANY"


def _extract_route_path(decorator: str) -> str:
    m = re.search(r"['\"]([^'\"]+)['\"]", decorator)
    return m.group(1) if m else "/"


def _nextjs_route_path(file_path: str) -> str:
    fp = file_path.replace("\\", "/")
    for prefix in ("pages/api/", "app/"):
        idx = fp.find(prefix)
        if idx != -1:
            path = fp[idx + len(prefix) :]
            path = re.sub(r"\.[jt]sx?$", "", path)
            path = re.sub(r"/route$", "", path)
            path = re.sub(r"\[([^\]]+)\]", r"{\1}", path)
            return "/" + path
    return "/"


def _identifier(node, source: str) -> str:
    for child in _children(node):
        if child.kind() == "identifier":
            return _text(child, source)
    return ""


def _text(node, source: str) -> str:
    return node_text(node, source)
