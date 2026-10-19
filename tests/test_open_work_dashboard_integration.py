"""Dashboard remains workflow-first; Open Work previews are optional in Advanced."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source():
    return (ROOT / "dashboard" / "basic_mode.js").read_text(encoding="utf-8")


def test_open_work_specialist_views_are_advanced_only():
    script = _source()
    for row in ("pvSourceConnectionsRow", "pvOpenWorkRow", "pvGitHubOpenWorkRow"):
        assert f"mark($('#{row}'), true)" in script
    assert "mark($('#pvRestartRow'), true)" in script
    assert "function setMode(next)" in script
    assert "find_repeated_workflows" not in script  # no changes to workflow mapper


def test_explicit_source_scan_preserves_per_candidate_owg_evidence_context():
    script = _source()
    assert "confirm_scan=true&max_pages=1&include_local_observation=true" in script
    assert "Observed in retained OWG activity" in script
    assert "No matching retained OWG evidence" in script
    assert "OWG evidence uncertain" in script
    assert "No OWG observation alone proves" in script
    assert "candidate.historically_observed_in_owg" in script


def test_errors_are_user_facing_not_raw_provider_code():
    script = _source()
    assert "Connect Stripe" in script
    assert "openWorkStatus(result.reason, 'stripe')" in script
    assert "openWorkStatus(result.reason, 'github')" in script
    assert "replace(/_/g, ' ')" not in script[script.index("function openWorkStatus"):script.index("panel.querySelector('#pvRetention')")]
