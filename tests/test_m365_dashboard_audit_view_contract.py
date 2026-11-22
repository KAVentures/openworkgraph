"""Regression guard for the read-only M365 dashboard operator evidence view."""
from pathlib import Path


def test_operator_audit_ui_keeps_phases_review_only():
    html = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    block = html.split("/* Read-only Microsoft 365 operator audit receipts;", 1)[1]
    block = block.split("</script>", 1)[0]
    assert "fetch('/v1/m365/operator-audit', {cache: 'no-store'})" in block
    assert "Historical receipts only." in block
    assert "receipts may belong to different folders" in block
    assert "does not authorize another extraction" in block
    assert "Next-round handoff prepared — not executed" in block
    assert "Candidate extracted — inspect before append" in block
    assert "removal_markers_for_review" in block
    assert "if (!list.children.length)" in block
    assert "textContent" in block and "createElement" in block
    assert "innerHTML =" not in block.split("output.replaceChildren();", 1)[1]
    # The surrounding operator panel intentionally contains separately approved POST actions. 
    # Only the audit receipt handler itself must remain a read-only GET.
    audit_handler = block.split("fetch('/v1/m365/operator-audit', {cache: 'no-store'})", 1)[1]
    assert "method: 'POST'" not in audit_handler


def test_audit_revoked_mid_read_refuses_disclosure(monkeypatch):
    """A revoked session cannot receive an audit timeline after filesystem IO."""
    from unittest.mock import patch
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request
    from server.m365_operator_audit_routes import operator_audit

    request = Request({"type": "http", "method": "GET",
                       "path": "/v1/m365/operator-audit",
                       "headers": [(b"authorization", b"OWG-Session valid")]})
    monkeypatch.setenv("OWG_M365_AUDIT_DIR", "/private/audit")
    private = {"format": "openworkgraph.m365-operator-audit-list.v1",
               "configured": True, "records": [{"action": "commit"}]}
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid",
                side_effect=[True, False]) as session,
          patch("server.m365_operator_audit_routes.read_operator_audit",
                return_value=private) as read):
        with pytest.raises(HTTPException) as exc:
            operator_audit(request)
    assert exc.value.status_code == 409
    assert "records" not in str(exc.value.detail)
    assert session.call_count == 2
    read.assert_called_once()


def test_audit_active_session_still_returns_redacted_timeline(monkeypatch):
    from unittest.mock import patch
    import json
    from starlette.requests import Request
    from server.m365_operator_audit_routes import operator_audit

    request = Request({"type": "http", "method": "GET",
                       "path": "/v1/m365/operator-audit",
                       "headers": [(b"authorization", b"OWG-Session valid")]})
    monkeypatch.setenv("OWG_M365_AUDIT_DIR", "/private/audit")
    result = {"format": "openworkgraph.m365-operator-audit-list.v1",
              "configured": True, "records": [], "total_receipts": 0,
              "truncated": False, "provider_validated": False}
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid",
                return_value=True) as session,
          patch("server.m365_operator_audit_routes.read_operator_audit",
                return_value=result)):
        response = operator_audit(request)
    assert json.loads(response.body) == result
    assert response.headers["cache-control"] == "no-store"
    assert session.call_count == 2
