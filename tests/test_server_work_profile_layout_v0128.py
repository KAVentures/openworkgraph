from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_work_profile_implementation_is_grouped_as_a_domain_package():
    package = ROOT / "server" / "work_profile"
    assert package.is_dir()
    for name in ("__init__.py", "accuracy.py", "export.py", "routes.py", "service.py", "signals.py"):
        assert (package / name).is_file()
    assert not (ROOT / "server" / "work_profile.py").exists()


def test_legacy_work_profile_module_paths_alias_canonical_modules_without_polluting_app_state():
    code = r"""
import importlib
pairs = {
    "server.work_profile_accuracy": "server.work_profile.accuracy",
    "server.work_profile_export": "server.work_profile.export",
    "server.work_profile_routes": "server.work_profile.routes",
    "server.work_profile_service": "server.work_profile.service",
    "server.work_profile_signals": "server.work_profile.signals",
}
for legacy, canonical in pairs.items():
    assert importlib.import_module(legacy) is importlib.import_module(canonical)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_internal_consumers_use_canonical_work_profile_package():
    exporter = (ROOT / "server" / "context_exporter.py").read_text(encoding="utf-8")
    compact = (ROOT / "mcp_server" / "compact.py").read_text(encoding="utf-8")

    assert "from .work_profile.service import compute_work_profile" in exporter
    assert "from .work_profile.export import append_profile_sheets, profile_tables" in exporter
    assert "from server.work_profile.accuracy import local_day_since" in compact

    for old in (
        "from .work_profile_service import",
        "from .work_profile_export import",
        "from server.work_profile_accuracy import",
    ):
        assert old not in exporter + compact


def test_legacy_alias_files_stay_tiny():
    for name in (
        "work_profile_accuracy.py",
        "work_profile_export.py",
        "work_profile_routes.py",
        "work_profile_service.py",
        "work_profile_signals.py",
    ):
        text = (ROOT / "server" / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 10
        assert "Compatibility alias" in text
