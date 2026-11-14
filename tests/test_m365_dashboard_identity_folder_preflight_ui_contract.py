"""Static dashboard contract for explicitly approved Graph preflight."""
from pathlib import Path


def test_identity_folder_preflight_is_opt_in_and_no_automatic_retry():
    page = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "Verify Microsoft 365 identity and folder" in page
    assert "operator-identity-folder-preflight?consent_graph_read=true&consent_private_report=true" in page
    assert "if (!identityGraphConsent.checked || !identityReportConsent.checked)" in page
    assert "identityGraphConsent.checked = false;" in page
    assert "identityReportConsent.checked = false;" in page
    assert "Tenant identity is not verified" in page
    assert "No automatic retry." in page
