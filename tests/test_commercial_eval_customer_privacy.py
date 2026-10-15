"""Synthetic customer-name redaction cases from the commercial evaluation."""
from __future__ import annotations

from connector.cloud_privacy import DeviceUploadRedactor
from gateway.plugin_redaction import PublicRedactor, project


def _public() -> PublicRedactor:
    return PublicRedactor(secret="synthetic-unit-secret", principal="fictional-test-account")


def test_explicit_customer_name_fields_redacted_in_hosted_text():
    redactor = _public()
    for source, forbidden in [
        ("Customer: Valeska Nordholm — refund approved", "Valeska Nordholm"),
        ("Kund: Torell Strandberg, faktura 12", "Torell Strandberg"),
        ("Requester name: Zareena Linden", "Zareena Linden"),
        ("Contact: Jorund Storvik - ticket reopened", "Jorund Storvik"),
    ]:
        for output in (redactor.text(source), redactor.target_label(source)):
            assert forbidden.casefold() not in output.casefold(), output
            assert "PERSON_" in output, output
    assert redactor.text("Customer support dashboard") == "Customer support dashboard"


def test_explicit_customer_name_fields_redacted_before_cloud_upload():
    local = DeviceUploadRedactor(secret="synthetic-device-secret-32-characters")
    redacted = local.label("Customer: Valeska Nordholm — pending refund")
    assert "Valeska" not in redacted
    assert "Nordholm" not in redacted
    assert "PERSON_" in redacted


def test_hosted_project_never_reexposes_explicit_customer_name():
    result = project(
        {"rows": [{"event_id": "e1", "event_type": "browser_click",
                   "app": "Chrome",
                   "window_title": "Customer: Valeska Nordholm",
                   "target_label": "Contact: Jorund Storvik"}]},
        detail="rich", secret="synthetic-unit-secret", principal="fictional-test-account",
    )
    assert "Valeska" not in str(result)
    assert "Nordholm" not in str(result)
    assert "Jorund" not in str(result)
    assert "Storvik" not in str(result)
