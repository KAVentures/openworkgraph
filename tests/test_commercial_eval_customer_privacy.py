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


def test_token_safe_customer_redaction_consistent_across_all_three_paths():
    from server.contextual_redaction import Detector
    from shared.core.identity_cues import redact_labeled_person_fields

    local = Detector(token=lambda kind, value: "PERSON_LOCALSAFE")
    hosted = _public()
    device = DeviceUploadRedactor(secret="synthetic-device-secret-32-characters")
    for phrase in (
        "Customer: Zubeyde Okonkwo — pending",
        "customer: anna svensson",
        "Customer: Ingrid Bergqvist Refund Approved",
    ):
        outputs = [local.redact(phrase), hosted.text(phrase), device.label(phrase)]
        for output in outputs:
            assert "Zubeyde" not in output and "Okonkwo" not in output, output
            assert "anna svensson" not in output.casefold(), output
            assert "PERSON_" in output, output
        if "Refund Approved" in phrase:
            assert "Refund Approved" in hosted.text(phrase)

    for phrase in (
        "Recipient: someone@acme.se",
        "Contact: anders@kund.se",
    ):
        out = hosted.text(phrase)
        assert "EMAIL_" in out
        assert "PERSON_" not in out, out
        assert hosted.text(out) == out
        out_upload = device.label(phrase)
        assert "EMAIL_" in out_upload
        assert "PERSON_" not in out_upload, out_upload
        # One pass of the local detector after the privacy presentation layer
        # must not reinterpret pseudonyms as names.
        assert "PERSON_" not in local.redact("Recipient: EMAIL_A1B2C3D4E5")


def test_shared_customer_field_helper_is_idempotent_and_limits_name_span():
    from shared.core.identity_cues import redact_labeled_person_fields
    token = lambda name: "PERSON_ABCD1234"
    for text in ("Recipient: EMAIL_ABCDEF1234", "Contact: PERSON_A1B2C3D4",
                 "Customer: OWNER_A1B2C3D4"):
        assert redact_labeled_person_fields(text, person_token=token) == text
    assert redact_labeled_person_fields("Customer: Ingrid Bergqvist Refund Approved",
                                        person_token=token) == "Customer: PERSON_ABCD1234 Refund Approved"
