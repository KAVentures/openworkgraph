"""Regression guard for the first authenticated human-review journey.

The review read API intentionally returns revision 0 / decision 'unreviewed'
when no durable review exists. The browser must accept this state so that the
operator can create the very first draft or approval. This source-contract
check complements (and does not replace) API-backed browser acceptance.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_first_review_state_is_supported_by_api_and_browser():
    server = (ROOT / "server" / "review_choice_routes.py").read_text()
    browser = (ROOT / "dashboard" / "review_choice_workbench.js").read_text()
    assert '"decision": row[1] if row else "unreviewed"' in server
    assert '"revision": row[0] if row else 0' in server
    assert "!['unreviewed','draft','approved','revoked'].includes(state.decision)" in browser
    assert "reviewRevision=state.revision" in browser
    assert "reviewTrusted=true" in browser
