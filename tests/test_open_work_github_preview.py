"""Open Work GitHub inventory: explicit repo, source consent and no native IDs."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.open_work_github_preview import github_open_work_preview


def item(number, *, state="OPEN", decision="REVIEW_REQUIRED", draft=False):
    return {
        "number": number, "state": state,
        "reviewDecision": decision, "isDraft": draft,
        "updatedAt": "2026-10-19T10:00:00Z",
        "title": "VERY_PRIVATE_TITLE", "author": "PRIVATE_OWNER",
    }


def runner(data, calls):
    def run(argv, **opts):
        calls.append((argv, opts))
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    return run


def test_consent_must_precede_any_github_cli_or_query():
    calls = []
    result = github_open_work_preview("acme/project",
        runner=runner([item(1)], calls), which=lambda name: "/usr/bin/gh",
        consent=lambda: True)
    assert result["reason"] == "explicit_inventory_scan_not_authorized"
    assert calls == []
    result = github_open_work_preview("acme/project", explicit_scan_authorized=True,
        runner=runner([item(1)], calls), which=lambda name: "/usr/bin/gh",
        consent=lambda: False)
    assert result["reason"] == "existing_github_outcome_tracking_opt_in_off"
    assert calls == []


def test_selected_repo_open_prs_only_and_no_identifiers():
    calls = []
    result = github_open_work_preview(
        "acme/project", explicit_scan_authorized=True,
        runner=runner([item(1), item(2, decision="APPROVED")], calls),
        which=lambda name: "/opt/homebrew/bin/gh", consent=lambda: True,
    )
    assert result["availability"] == "source_reported"
    assert result["returned"] == 2
    assert result["source_coverage_complete"] is True
    assert result["candidates"][0]["review_status"] == "review_required"
    assert result["candidates"][0]["work_item_ref"].startswith("owg:w:")
    assert result["business_obligation_verified"] is False
    assert result["automatic_actions"] is False
    assert calls[0][0] == [
        "/opt/homebrew/bin/gh", "pr", "list", "--repo", "acme/project",
        "--state", "open", "--limit", "50", "--json",
        "number,state,isDraft,reviewDecision,updatedAt",
    ]
    assert calls[0][1]["timeout"] == 15
    for forbidden in ("acme/project", "VERY_PRIVATE_TITLE", "PRIVATE_OWNER"):
        assert forbidden not in str(result)


@pytest.mark.parametrize("repo", [
    "../x", "-evil/x", "someone/..", "owner/repo;rm", "owner/repo another",
    "owner/repo/extra", "https://github.com/x/y", "",
])
def test_invalid_or_unapproved_repo_prevents_shell_access(repo):
    with pytest.raises(ValueError):
        github_open_work_preview(
            repo, explicit_scan_authorized=True, consent=lambda: True,
            runner=lambda *_a, **_kw: pytest.fail("runner called"),
        )


@pytest.mark.parametrize("data", [
    [item(1, state="MERGED")],
    [item(1), item(1)],
    [item(1, decision="UNVERIFIED")],
    [item(1, draft="yes")],
    [item(True)],
    {"not": "a list"},
])
def test_invalid_provider_reply_fail_closed(data):
    result = github_open_work_preview(
        "acme/project", explicit_scan_authorized=True,
        runner=runner(data, []), consent=lambda: True,
        which=lambda name: "/usr/bin/gh",
    )
    assert result["availability"] == "unknown"
    assert result["candidates"] == []


def test_truncated_source_does_not_claim_coverage():
    data = [item(i) for i in range(1, 4)]
    result = github_open_work_preview(
        "acme/project", explicit_scan_authorized=True, limit=3,
        runner=runner(data, []), consent=lambda: True,
        which=lambda name: "/usr/bin/gh",
    )
    assert result["source_coverage_complete"] is False
    assert result["source_limit_reached"] is True


def test_auth_missing_does_not_probe_repository():
    result = github_open_work_preview(
        "acme/project", explicit_scan_authorized=True,
        consent=lambda: True, which=lambda name: None,
    )
    assert result["reason"] == "github_cli_not_installed"


def test_dashboard_endpoint_requires_existing_session(tmp_path):
    import os
    import subprocess
    import sys

    code = """
import server.secure_app
from fastapi.testclient import TestClient
from server.secure_app import app
r = TestClient(app).get('/v1/open-work/github-preview',
    params={'repository': 'acme/project', 'confirm_scan': 'true'})
assert r.status_code in (401, 403), r.text
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    r = subprocess.run([sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[1]), env=env,
        capture_output=True, text=True, timeout=75 if os.name == "nt" else 35)
    assert r.returncode == 0, r.stderr[-3000:]
