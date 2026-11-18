"""Dashboard checkpoint operator affordance regression contracts."""
from pathlib import Path


def test_dashboard_checkpoint_is_explicit_and_read_only():
    page = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "owgM365OperatorAudit" in page
    assert "I consent to read the local Microsoft 365 checkpoint" in page
    assert "checkpointConsent.checked" in page
    assert "method: 'POST', cache: 'no-store', credentials: 'same-origin'" in page
    assert "/v1/m365/operator-checkpoint?consent_local_read=true" in page
    assert "checkpointConsent.checked = false" in page
    assert "No Graph read, append or commit performed." in page
    assert "No automatic retry." in page
