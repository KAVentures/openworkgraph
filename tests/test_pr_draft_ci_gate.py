"""All full OWG CI gates must run on ready PRs; drafts only use cheap syntax.

This policy is intended to keep five parallel worker branches from flooding
the runner queue while preserving native/browser/scale/package merge checks.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL = (
    "tests.yml", "scale-acceptance.yml", "explorer-accessibility.yml",
    "starter-runtimes.yml", "package-launch-smoke.yml", "release.yml",
)
GUARD = "if: github.event_name != 'pull_request' || github.event.pull_request.draft == false"


def test_drafts_skip_expensive_jobs_but_ready_event_enables_all_required_ci():
    for filename in FULL:
        source = (WORKFLOWS / filename).read_text(encoding="utf-8")
        assert "  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review]\n" in source, filename
        assert "  push:\n" in source, filename
        assert "jobs:\n" in source
        # Match top-level jobs, not nested strategy/steps. All must be guarded
        # except publish-release, which already runs on main only.
        body = source.split("\njobs:\n", 1)[1]
        job_positions = list(re.finditer(r"^  ([\w-]+):\s*$", body, re.MULTILINE))
        assert job_positions, filename
        for match in job_positions:
            job = match.group(1)
            next_lines = body[match.end():].splitlines()
            next_nonempty = next((line.strip() for line in next_lines if line.strip()), "")
            if job == "publish-release":
                assert filename == "release.yml"
                assert next_nonempty == "if: github.ref == 'refs/heads/main'"
            else:
                assert next_nonempty == GUARD, (filename, job, next_nonempty)


def test_draft_smoke_is_pr_scoped_and_cannot_replace_required_merge_gates():
    source = (WORKFLOWS / "draft-smoke.yml").read_text(encoding="utf-8")
    assert "name: draft-smoke" in source
    assert "github.event.pull_request.draft == true" in source
    assert "ready_for_review" in source
    assert "timeout-minutes: 6" in source
    assert "python -m compileall -q" in source
    assert "node --check" in source
    assert "contents: read" in source
    merger = (ROOT / ".github" / "scripts" / "merge_on_green.cjs").read_text(encoding="utf-8")
    for name in (
        "tests", "scale-acceptance", "explorer-accessibility",
        "starter-runtimes", "packaged-launch-smoke", "tester-release",
    ):
        assert f"'{name}'" in merger
    for native in (
        "pytest (windows-latest, 3.11)", "pytest (ubuntu-latest, 3.11)",
        "pytest (macos-latest, 3.11)",
    ):
        assert native in merger
    # The coordinator refuses a skipped draft test matrix.
    assert "job.conclusion !== 'success'" in merger
    assert "run.conclusion !== 'success'" in merger


def test_no_on_push_path_is_dropped_from_full_workflows():
    for filename in FULL:
        source = (WORKFLOWS / filename).read_text(encoding="utf-8")
        # Mainline pushes keep full validation even without PR metadata.
        assert re.search(r"^  push:\s*$", source, re.MULTILINE), filename
        assert "github.event_name != 'pull_request'" in source
