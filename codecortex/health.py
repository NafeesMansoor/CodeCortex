"""Post-update health check.

Only the things CodeCortex actually depends on are checked: the interpreter can
import the pipeline, the version metadata is coherent, configuration parses, and
the SQLite caches are readable at a schema this build understands. Optional
extras (embeddings, clustering, LSP) are reported but never fail the run — they
are optional by design.

The updater runs this in a *fresh subprocess*. Checking inside the process that
just performed the update would import modules already resident in memory and
validate the version being replaced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from codecortex.version import __version__, installed_metadata_version

CORE_MODULES = (
    "core.types",
    "core.parser_framework",
    "graph.cpg_builder",
    "graph.graph_store",
    "indexing.symbol_cache",
    "pipeline.context_builder",
    "retrieval.retrieval_layer",
)

OPTIONAL_MODULES = {
    "embeddings (faiss)": "faiss",
    "clustering (hdbscan)": "hdbscan",
    "semantic indexing (jedi)": "jedi",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    severity: str = "error"  # "error" fails the run; "warning" is informational

    @property
    def fatal(self) -> bool:
        return not self.ok and self.severity == "error"


@dataclass
class HealthReport:
    checks: list[Check] = field(default_factory=list)
    version: str = __version__

    @property
    def healthy(self) -> bool:
        return not any(check.fatal for check in self.checks)

    def add(self, name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        self.checks.append(Check(name, ok, detail, severity))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": "HEALTHY" if self.healthy else "UNHEALTHY",
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "detail": c.detail,
                    "severity": c.severity,
                }
                for c in self.checks
            ],
        }

    def render(self) -> str:
        lines = [f"CodeCortex Health Check ({self.version})", ""]
        for check in self.checks:
            mark = "OK  " if check.ok else ("FAIL" if check.severity == "error" else "WARN")
            line = f"  [{mark}] {check.name}"
            if check.detail:
                line += f" — {check.detail}"
            lines.append(line)
        lines.append("")
        lines.append(f"Status: {'HEALTHY' if self.healthy else 'UNHEALTHY'}")
        return "\n".join(lines)


def _check_core_modules(report: HealthReport) -> None:
    import importlib

    failed = []
    for module in CORE_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            failed.append(f"{module} ({exc})")
    report.add(
        "Core modules",
        not failed,
        "all import cleanly" if not failed else "; ".join(failed),
    )


def _check_optional_modules(report: HealthReport) -> None:
    import importlib.util

    for label, module in OPTIONAL_MODULES.items():
        present = importlib.util.find_spec(module) is not None
        report.add(
            f"Optional: {label}",
            present,
            "installed" if present else "not installed",
            severity="warning",
        )


def _check_version_metadata(report: HealthReport) -> None:
    metadata = installed_metadata_version()
    if metadata is None:
        report.add(
            "Version metadata",
            True,
            f"running from source at {__version__} (no installed distribution)",
            severity="warning",
        )
        return
    matches = metadata == __version__
    report.add(
        "Version metadata",
        matches,
        f"code {__version__} == metadata {metadata}"
        if matches
        else f"code {__version__} but installed metadata says {metadata} — reinstall to resync",
        severity="warning",
    )


def _check_config(report: HealthReport, config_path: Optional[Path]) -> None:
    from codecortex.config import find_config
    from codecortex.config import load as load_config

    path = config_path or find_config(Path.cwd())
    if path is None:
        report.add("Configuration", True, "no codecortex.yaml found — using defaults")
        return
    try:
        load_config(path)
    except Exception as exc:
        report.add("Configuration", False, f"{path} failed to load: {exc}")
        return
    report.add("Configuration", True, f"{path} parsed")


def _check_caches(report: HealthReport, index_dir: Optional[Path]) -> None:
    from codecortex.migrations import discover, plan

    if index_dir is None:
        report.add("Index caches", True, "no index directory given — skipped", severity="warning")
        return

    paths = discover(index_dir)
    if not paths:
        report.add("Index caches", True, f"no databases under {index_dir}")
        return

    steps = plan(paths)
    blocked = [s for s in steps if s.action == "blocked"]
    stale = [s for s in steps if s.action == "rebuild"]
    if blocked:
        report.add(
            "Index caches",
            False,
            "; ".join(f"{s.store} at {s.path}: {s.detail}" for s in blocked),
        )
        return
    if stale:
        report.add(
            "Index caches",
            True,
            f"{len(stale)} cache(s) will rebuild on next index: "
            + ", ".join(s.store for s in stale),
            severity="warning",
        )
        return
    report.add("Index caches", True, f"{len(steps)} database(s) at the current schema")


def _check_database_engine(report: HealthReport) -> None:
    import sqlite3

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE probe (id INTEGER)")
        conn.execute("PRAGMA user_version = 1")
        conn.close()
    except Exception as exc:
        report.add("Database engine", False, str(exc))
        return
    report.add("Database engine", True, f"sqlite {sqlite3.sqlite_version}")


def run_health_check(
    index_dir: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> HealthReport:
    report = HealthReport()
    _check_core_modules(report)
    _check_version_metadata(report)
    _check_database_engine(report)
    _check_config(report, config_path)
    _check_caches(report, index_dir)
    _check_optional_modules(report)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="codecortex health", description="Verify this CodeCortex installation."
    )
    parser.add_argument(
        "--index-dir", metavar="DIR", default=None, help="Directory holding index caches"
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None, help="codecortex.yaml to validate"
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    report = run_health_check(
        index_dir=Path(args.index_dir) if args.index_dir else None,
        config_path=Path(args.config) if args.config else None,
    )
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render())
    return 0 if report.healthy else 1
