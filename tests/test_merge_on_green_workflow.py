"""GitHub auto-merge coordination: scheduled job must actually run; no CI bypass."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "owg-merge-on-green.yml"
SCRIPT = ROOT / ".github" / "scripts" / "merge_on_green.cjs"


def test_hourly_reconciliation_has_an_executable_job_not_only_a_trigger():
    content = WORKFLOW.read_text(encoding="utf-8")
    trigger = content.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
    job = content.split("  merge-opted-in:", 1)[1]
    assert "  schedule:" in trigger and "cron: '17 * * * *'" in trigger
    assert "github.event_name == 'schedule'" in job
    assert "await run({github, context, core});" in job
    assert "ref: ${{ github.event.repository.default_branch }}" in job
    assert "persist-credentials: false" in job
    assert "github.event.pull_request.draft == false" in job
    assert "contains(github.event.pull_request.body, 'OWG-MERGE-ON-GREEN:')" in job


def test_only_tests_completion_is_a_fast_trigger_but_all_six_gates_remain_required():
    content = WORKFLOW.read_text(encoding="utf-8")
    trigger = content.split("  workflow_run:", 1)[1].split("  pull_request_target:", 1)[0]
    assert "workflows: [tests]" in trigger
    for old in ("- scale-acceptance", "- explorer-accessibility",
                "- starter-runtimes", "- packaged-launch-smoke", "- tester-release"):
        assert old not in trigger
    merger = SCRIPT.read_text(encoding="utf-8")
    for group in ("tests", "scale-acceptance", "explorer-accessibility",
                  "starter-runtimes", "packaged-launch-smoke", "tester-release"):
        assert f"'{group}'" in merger
    for platform in ("pytest (ubuntu-latest, 3.11)", "pytest (macos-latest, 3.11)",
                     "pytest (windows-latest, 3.11)"):
        assert platform in merger
    assert "upToDateWithMain !== true" in merger
    assert "if (!pr || pr.state !== 'open' || pr.number === 1)" in merger
    assert "expected" not in content.lower() or "exact-head" in content
    assert "sha: current.head.sha" in merger
    assert "Never checkout PR head" in content
