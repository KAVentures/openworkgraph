"""M365 dashboard audit evidence: authorization, privacy and filesystem integrity."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server.m365_operator_audit_routes import read_operator_audit, AuditEvidenceError


def _receipt(directory, name="m365-" + "a" * 32 + ".json"):
    record = {"format": "openworkgraph.m365-operator-audit.v1",
              "timestamp_utc": "2026-11-11T05:00:00+00:00",
              "action": "extract", "status": "review_required",
              "scope_sha256": "a" * 64, "log_id_sha256": "b" * 64,
              "expected_generation": 2, "event_snapshots": 3,
              "automatic_retry_allowed": False,
              "secret": "never-expose"}
    p = directory / name
    p.write_text(json.dumps(record))
    p.chmod(0o600)
    return p


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only audit reader")
def test_private_receipts_are_bounded_and_redacted(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    _receipt(directory)
    result = read_operator_audit(directory)
    assert result["total_receipts"] == 1
    assert result["records"][0]["event_snapshots"] == 3
    assert "scope_sha256" not in result["records"][0]
    assert "log_id_sha256" not in result["records"][0]
    assert "secret" not in str(result)
    assert result["provider_validated"] is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only audit reader")
def test_rejects_symlink_hardlink_and_public_files(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    original = _receipt(directory)
    other = tmp_path / "other"
    other.write_text(original.read_text())
    (directory / ("m365-" + "b" * 32 + ".json")).symlink_to(other)
    with pytest.raises(AuditEvidenceError):
        read_operator_audit(directory)
    (directory / ("m365-" + "b" * 32 + ".json")).unlink()
    os.link(original, directory / ("m365-" + "b" * 32 + ".json"))
    with pytest.raises(AuditEvidenceError):
        read_operator_audit(directory)
    (directory / ("m365-" + "b" * 32 + ".json")).unlink()
    original.chmod(0o644)
    with pytest.raises(AuditEvidenceError):
        read_operator_audit(directory)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only audit reader")
def test_rejects_public_directory_and_invalid_record(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    receipt = _receipt(directory)
    directory.chmod(0o755)
    with pytest.raises(AuditEvidenceError):
        read_operator_audit(directory)
    directory.chmod(0o700)
    receipt.write_text('{"format":"wrong"}')
    with pytest.raises(AuditEvidenceError):
        read_operator_audit(directory)


def test_dashboard_route_denies_bearer_and_missing_session(monkeypatch):
    from server.m365_operator_audit_routes import operator_audit
    from starlette.requests import Request
    from fastapi import HTTPException
    for authorization in ("", "Bearer ai-token", "OWG-Session invalid"):
        request = Request({"type": "http", "method": "GET", "path": "/v1/m365/operator-audit",
                           "headers": [(b"authorization", authorization.encode())]})
        with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=False):
            with pytest.raises(HTTPException) as exc:
                operator_audit(request)
        assert exc.value.status_code == 403


def test_dashboard_route_does_not_read_unconfigured_storage(monkeypatch):
    from server.m365_operator_audit_routes import operator_audit
    from starlette.requests import Request
    monkeypatch.delenv("OWG_M365_AUDIT_DIR", raising=False)
    request = Request({"type": "http", "method": "GET", "path": "/v1/m365/operator-audit",
                       "headers": [(b"authorization", b"OWG-Session valid")]})
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True):
        response = operator_audit(request)
    assert json.loads(response.body)["configured"] is False
    assert response.headers["cache-control"] == "no-store"
