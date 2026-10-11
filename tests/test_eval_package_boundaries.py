"""Development evaluation assets must not ship in runtime packages."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_macos_release_omits_evaluations():
    release = (ROOT / "scripts/build_macos_release.sh").read_text()
    bundle = (ROOT / "scripts/build_macos_app_bundle.sh").read_text()
    assert "--exclude 'evals/'" in release
    assert "--exclude 'evals/'" in bundle
    assert "--exclude 'tests/'" in release
    assert "--exclude 'tests/'" in bundle


def test_windows_distribution_omits_evaluations():
    script = (ROOT / "scripts/build_windows_release.ps1").read_text()
    assert '"/XD"' in script
    assert '"evals"' in script.split('"/XD"', 1)[1].split("\n", 1)[0]


def test_docker_context_omits_evaluations():
    ignores = (ROOT / ".dockerignore").read_text().splitlines()
    assert "evals" in ignores and "tests" in ignores
    vercel = (ROOT / "Dockerfile.vercel").read_text()
    assert "COPY evals" not in vercel


def test_release_ci_checks_actual_payloads():
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert 'test ! -d "$PAYLOAD/evals"' in release
    assert "Test-Path $EvalDir" in release
