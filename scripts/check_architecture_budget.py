from __future__ import annotations

"""Fail CI if OpenWorkGraph's known layering debt grows.

This is a stop-loss, not the refactor. The baselines are the 2026-10-03 audit
counts. The later app-factory/router migration should reduce these numbers and
then lower the budgets.
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

BUDGETS = {
    "import_registrars": 28,
    "dashboard_html_rewriters": 32,
    "runtime_route_replacements": 21,
}

REGISTRAR = re.compile(
    r"@app\.(?:get|post|put|delete|patch)\(|"
    r"app\.add_api_route\(|app\.include_router\(|extend_lifespan\(app"
)
DASHBOARD_REWRITE = re.compile(
    r"(?:DASHBOARD|dashboard|HTMLResponse).{0,240}"
    r"(?:\.replace\(|write_text\(|read_text\(|body\.replace\()",
    re.IGNORECASE | re.DOTALL,
)
ROUTE_REPLACE = re.compile(
    r"app\.router\.routes|route\.endpoint\s*=|route\.dependant\s*=|"
    r"(?:replace|remove|patch|swap)[_a-zA-Z0-9]*route",
    re.IGNORECASE,
)


def text_files() -> list[Path]:
    roots = [ROOT / "server", ROOT / "gateway"]
    rows: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        rows.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(rows)


def count_files(pattern: re.Pattern[str]) -> tuple[int, list[str]]:
    matches: list[str] = []
    for path in text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if pattern.search(text):
            matches.append(str(path.relative_to(ROOT)))
    return len(matches), matches


def main() -> int:
    measured = {
        "import_registrars": count_files(REGISTRAR),
        "dashboard_html_rewriters": count_files(DASHBOARD_REWRITE),
        "runtime_route_replacements": count_files(ROUTE_REPLACE),
    }
    failed = False
    for name, (count, files) in measured.items():
        budget = BUDGETS[name]
        print(f"{name}: {count} (budget {budget})")
        if count > budget:
            failed = True
            print("  New layering detected. Matching files:")
            for path in files:
                print(f"  - {path}")
    if failed:
        print(
            "Architecture budget exceeded. Prefer the existing router/app-factory "
            "seams instead of adding another import-time registrar or runtime rewrite.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
