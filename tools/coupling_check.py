#!/usr/bin/env python3
"""Coupling checker — misura il grado di disaccoppiamento tra UI e business layer.

Indicatori raccolti:
  - VIOLATION  : import da core.* o db.* in ui/ (accoppiamento diretto — da eliminare)
  - COMPLIANT  : import da services.* in ui/ (pattern corretto)
  - RAW_QUERY  : query SQLAlchemy dirette in ui/ (get_session, .query(), sessionmaker)
  - API_COVERAGE: % metodi service esposti via API

Exit code:
  0  — nessuna violazione (o soglia rispettata)
  1  — violazioni sopra soglia (usare in CI con --max-violations N)

Uso:
  python tools/coupling_check.py                    # report completo
  python tools/coupling_check.py --max-violations 0 # fail se ci sono violazioni
  python tools/coupling_check.py --json             # output JSON per integrazione
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── Configurazione ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent

UI_DIR     = ROOT / "ui"
SERVICES_DIR = ROOT / "services"
API_DIR    = ROOT / "api"

VIOLATION_MODULES  = ("core", "db")   # import da questi in ui/ = violazione
COMPLIANT_MODULES  = ("services",)    # import da questi in ui/ = conforme
RAW_QUERY_PATTERNS = [
    r"get_session\(",
    r"sessionmaker\(",
    r"\.query\(",
    r"session\.execute\(",
]

# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ImportLine:
    file: str
    line: int
    statement: str
    module: str


@dataclass
class FileReport:
    path: str
    violations: list[ImportLine] = field(default_factory=list)
    compliant:  list[ImportLine] = field(default_factory=list)
    raw_queries: list[dict]      = field(default_factory=list)

    @property
    def score(self) -> float:
        """0.0 = completamente accoppiato, 1.0 = completamente disaccoppiato."""
        total = len(self.violations) + len(self.compliant)
        if total == 0:
            return 1.0
        return len(self.compliant) / total


@dataclass
class CouplingReport:
    files: list[FileReport] = field(default_factory=list)
    service_methods: dict[str, list[str]] = field(default_factory=dict)
    api_endpoints: list[str] = field(default_factory=list)

    @property
    def total_violations(self) -> int:
        return sum(len(f.violations) for f in self.files)

    @property
    def total_compliant(self) -> int:
        return sum(len(f.compliant) for f in self.files)

    @property
    def total_raw_queries(self) -> int:
        return sum(len(f.raw_queries) for f in self.files)

    @property
    def coupling_score(self) -> float:
        """% di file UI senza violazioni."""
        if not self.files:
            return 1.0
        clean = sum(1 for f in self.files if len(f.violations) == 0)
        return clean / len(self.files)

    @property
    def api_coverage(self) -> float:
        """% di metodi service esposti via API."""
        total = sum(len(v) for v in self.service_methods.values())
        if total == 0:
            return 0.0
        covered = sum(
            1 for methods in self.service_methods.values()
            for m in methods
            if any(m in ep for ep in self.api_endpoints)
        )
        return covered / total


# ── Analisi import ─────────────────────────────────────────────────────────────

def _extract_imports(path: Path) -> list[tuple[int, str, str]]:
    """Ritorna lista di (line_no, statement, top_module)."""
    results = []
    try:
        source = path.read_text(encoding="utf-8")
        tree   = ast.parse(source, filename=str(path))
    except SyntaxError:
        return results

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            stmt = f"from {node.module} import {', '.join(a.name for a in node.names)}"
            results.append((node.lineno, stmt, top))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                stmt = f"import {alias.name}"
                results.append((node.lineno, stmt, top))
    return results


def _scan_raw_queries(path: Path) -> list[dict]:
    """Cerca pattern SQLAlchemy raw nel sorgente (regex su testo)."""
    found = []
    source = path.read_text(encoding="utf-8")
    for pattern in RAW_QUERY_PATTERNS:
        for m in re.finditer(pattern, source):
            lineno = source[:m.start()].count("\n") + 1
            found.append({"line": lineno, "match": m.group(), "pattern": pattern})
    return found


def analyze_ui(ui_dir: Path) -> list[FileReport]:
    reports = []
    for py_file in sorted(ui_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        rel = py_file.relative_to(ROOT)
        fr  = FileReport(path=str(rel))

        for lineno, stmt, top in _extract_imports(py_file):
            entry = ImportLine(file=str(rel), line=lineno, statement=stmt, module=top)
            if top in VIOLATION_MODULES:
                fr.violations.append(entry)
            elif top in COMPLIANT_MODULES:
                fr.compliant.append(entry)

        fr.raw_queries = _scan_raw_queries(py_file)
        reports.append(fr)

    return reports


# ── Analisi service API coverage ───────────────────────────────────────────────

def _public_methods(cls_node: ast.ClassDef) -> list[str]:
    return [
        n.name for n in cls_node.body
        if isinstance(n, ast.FunctionDef)
        and not n.name.startswith("_")
    ]


def analyze_service_methods(services_dir: Path) -> dict[str, list[str]]:
    result = {}
    for py_file in sorted(services_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Service"):
                result[node.name] = _public_methods(node)
    return result


def analyze_api_endpoints(api_dir: Path) -> list[str]:
    """Ritorna la lista di funzioni endpoint definite nei router."""
    endpoints = []
    for py_file in sorted((api_dir / "routers").glob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                endpoints.append(node.name)
    return endpoints


# ── Report ─────────────────────────────────────────────────────────────────────

_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


def _pct_color(v: float) -> str:
    if v >= 0.8:
        return _GREEN
    if v >= 0.5:
        return _YELLOW
    return _RED


def print_report(report: CouplingReport) -> None:
    print(f"\n{_BOLD}{'═' * 64}{_RESET}")
    print(f"{_BOLD}  Coupling Report — UI ↔ Service layer{_RESET}")
    print(f"{'═' * 64}")

    print(f"\n{_BOLD}Riepilogo globale{_RESET}")
    cs  = report.coupling_score
    api = report.api_coverage
    print(f"  File UI senza violazioni : {_pct_color(cs)}{cs*100:.0f}%{_RESET}  ({sum(1 for f in report.files if not f.violations)}/{len(report.files)} file)")
    print(f"  Violazioni totali        : {_RED if report.total_violations else _GREEN}{report.total_violations}{_RESET}  (import diretti core/db in ui/)")
    print(f"  Import da services/      : {_GREEN}{report.total_compliant}{_RESET}")
    print(f"  Query SQLAlchemy raw     : {_YELLOW if report.total_raw_queries else _GREEN}{report.total_raw_queries}{_RESET}  (in ui/)")
    print(f"  API coverage services    : {_pct_color(api)}{api*100:.0f}%{_RESET}")

    print(f"\n{_BOLD}Per file UI{_RESET}")
    print(f"  {'File':<30} {'Violazioni':>10} {'Compliant':>9} {'RawQuery':>8} {'Score':>6}")
    print(f"  {'-'*30} {'-'*10} {'-'*9} {'-'*8} {'-'*6}")
    for fr in report.files:
        score_col = _GREEN if fr.score == 1.0 else (_YELLOW if fr.score > 0 else _RED)
        viol_col  = _RED if fr.violations else _GREEN
        print(
            f"  {fr.path:<30} "
            f"{viol_col}{len(fr.violations):>10}{_RESET} "
            f"{_GREEN if fr.compliant else ''}{len(fr.compliant):>9}{_RESET} "
            f"{_YELLOW if fr.raw_queries else ''}{len(fr.raw_queries):>8}{_RESET} "
            f"{score_col}{fr.score*100:>5.0f}%{_RESET}"
        )

    if report.total_violations:
        print(f"\n{_BOLD}Dettaglio violazioni{_RESET}")
        for fr in report.files:
            if fr.violations:
                print(f"\n  {_CYAN}{fr.path}{_RESET}")
                for v in fr.violations:
                    print(f"    L{v.line:>4}  {v.statement}")

    print(f"\n{_BOLD}Service methods → API coverage{_RESET}")
    for svc, methods in sorted(report.service_methods.items()):
        covered   = [m for m in methods if any(m in ep for ep in report.api_endpoints)]
        uncovered = [m for m in methods if m not in covered]
        pct = len(covered) / len(methods) * 100 if methods else 0
        col = _pct_color(pct / 100)
        print(f"  {svc:<25} {col}{pct:>5.0f}%{_RESET}  ({len(covered)}/{len(methods)})")
        if uncovered:
            print(f"    {'non esposti':>10}: {', '.join(uncovered)}")

    print(f"\n{'═' * 64}\n")


def print_json(report: CouplingReport) -> None:
    out = {
        "coupling_score": round(report.coupling_score, 4),
        "api_coverage": round(report.api_coverage, 4),
        "total_violations": report.total_violations,
        "total_compliant": report.total_compliant,
        "total_raw_queries": report.total_raw_queries,
        "files": [
            {
                "path": f.path,
                "violations": len(f.violations),
                "compliant": len(f.compliant),
                "raw_queries": len(f.raw_queries),
                "score": round(f.score, 4),
            }
            for f in report.files
        ],
        "service_methods": {
            svc: {
                "total": len(methods),
                "covered": sum(1 for m in methods if any(m in ep for ep in report.api_endpoints)),
                "uncovered": [m for m in methods if not any(m in ep for ep in report.api_endpoints)],
            }
            for svc, methods in report.service_methods.items()
        },
    }
    print(json.dumps(out, indent=2))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="UI ↔ Service coupling checker")
    parser.add_argument("--max-violations", type=int, default=None,
                        help="Exit 1 se violazioni > N (usare in CI)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON invece di testo")
    args = parser.parse_args()

    file_reports     = analyze_ui(UI_DIR)
    service_methods  = analyze_service_methods(SERVICES_DIR)
    api_endpoints    = analyze_api_endpoints(API_DIR) if API_DIR.exists() else []

    report = CouplingReport(
        files=file_reports,
        service_methods=service_methods,
        api_endpoints=api_endpoints,
    )

    if args.json:
        print_json(report)
    else:
        print_report(report)

    if args.max_violations is not None and report.total_violations > args.max_violations:
        if not args.json:
            print(f"❌  FAIL — {report.total_violations} violazioni > soglia {args.max_violations}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
