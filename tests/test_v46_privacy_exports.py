from __future__ import annotations

import importlib
import io
import json
import re
import zipfile

import pytest

from sensitive_identifiers import redact_sensitive_identifiers


def _token(kind: str, value: str) -> bool:
    return bool(re.fullmatch(rf"{kind}_[0-9A-F]{{6}}", value))


def test_swedish_ocr_references_are_not_payment_cards():
    values = [
        "Betala med OCR 4001234567890120",
        "Betala med OCR 20260441788219",
        "Betala med OCR 5500123456786",
    ]
    for value in values:
        safe = redact_sensitive_identifiers(value)
        assert "PAYMENT_CARD_" not in safe
        assert "SENSITIVE_NUMBER_" not in safe
        assert safe == value


def test_real_card_stays_masked():
    safe = redact_sensitive_identifiers("4111 1111 1111 1111")
    assert _token("PAYMENT_CARD", safe)


def test_ambiguous_luhn_number_is_masked_without_false_card_claim():
    safe = redact_sensitive_identifiers("9912345678901239")
    assert _token("SENSITIVE_NUMBER", safe)


def _secret_cases():
    aws = "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY"
    github = "gho_" + "16C7abcdefghijklmnopqrstuvwxyzABCDEFGHI"
    stripe = "rk_" + "live_" + "abcdefghijklmnopqrstuvwx"
    return [
        (f"export AWS_SECRET_ACCESS_KEY={aws}", "wJalrXUtnFEMI"),
        (f"export GITHUB_TOKEN={github}", "gho_16C7"),
        ("DATABASE_PASSWORD=Sup3rS3cretPassw0rd!", "Sup3rS3cretPassw0rd"),
        (f"STRIPE_API_KEY={stripe}", "rk_live_"),
        ("postgres://admin:Sup3rS3cret@db.prod.internal:5432/app", "Sup3rS3cret"),
        ("Authorization: Bearer abc123def456ghi789jkl", "abc123def456"),
        ('PASSWORD="secret with spaces 123"', "secret with spaces"),
    ]


@pytest.mark.parametrize("raw, forbidden", _secret_cases())
def test_realistic_secret_shapes_are_masked(raw: str, forbidden: str):
    safe = redact_sensitive_identifiers(raw)
    assert forbidden not in safe
    assert "SECRET_" in safe


def test_private_key_block_is_fully_replaced():
    raw = "-----BEGIN PRIVATE KEY-----\nabc123secretmaterial\n-----END PRIVATE KEY-----"
    safe = redact_sensitive_identifiers(raw)
    assert "BEGIN PRIVATE KEY" not in safe
    assert "abc123secretmaterial" not in safe
    assert _token("SECRET", safe)


def test_connection_string_keeps_context_but_not_password():
    raw = "postgres://admin:Sup3rS3cret@db.prod.internal:5432/app"
    safe = redact_sensitive_identifiers(raw)
    assert safe.startswith("postgres://admin:SECRET_")
    assert "@db.prod.internal:5432/app" in safe


def test_legacy_ocr_payment_card_token_is_semantically_repaired():
    safe = redact_sensitive_identifiers("OCR PAYMENT_CARD_A1B2C3")
    assert safe == "OCR_REFERENCE_A1B2C3"


def _reload_privacy(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    import server.privacy_pipeline as pipeline
    importlib.reload(presentation)
    return importlib.reload(pipeline)


def test_status_and_team_phrases_are_not_learned_as_people(monkeypatch, tmp_path):
    pipeline = _reload_privacy(monkeypatch, tmp_path)

    pipeline.learn_persistent_identities({"label": "Transition to In Progress"})
    pipeline.learn_persistent_identities({"label": "Meeting with Legal Team"})

    registry = tmp_path / ".presentation_people.json"
    if registry.exists():
        payload = json.loads(registry.read_text(encoding="utf-8"))
        assert payload == {}

    safe = pipeline.redact_for_display({
        "label": "Sprint board: In Progress column",
    })
    assert safe["label"] == "Sprint board: In Progress column"


def test_strong_person_evidence_still_learns_and_reset_is_reversible(monkeypatch, tmp_path):
    pipeline = _reload_privacy(monkeypatch, tmp_path)
    evidence = {
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "From: Anna Svensson <anna.s@acme.com>",
    }
    pipeline.learn_persistent_identities(evidence)

    registry = tmp_path / ".presentation_people.json"
    assert registry.exists()
    assert json.loads(registry.read_text(encoding="utf-8"))

    assert pipeline.reset_persistent_identities() is True
    assert not registry.exists()
    assert pipeline.reset_persistent_identities() is False


def _minimal_payload():
    event = {
        "observed_at": "2026-09-18T12:00:00+00:00",
        "schema_version": "1.0",
        "organization_id": "",
        "actor_id": "",
        "device_id": "device",
        "sensor_id": "browser:test",
        "source": "browser_extension",
        "session_id": "session",
        "app": "Browser",
        "window_title": "Inbox",
        "event_type": "browser_click",
        "duration_seconds": 0,
        "metadata": {
            "action": "click",
            "page": {"hostname": "mail.google.com", "pathname": "/mail/u/0"},
            "target": {"role": "button", "label": "Send"},
        },
    }
    return {
        "export": {
            "product": "OpenWorkGraph / Workflow Observer",
            "version": "0.46.0",
            "generated_at": "2026-09-18T12:00:00+00:00",
            "scope": "current",
            "run_started_at": "2026-09-18T11:00:00+00:00",
            "include_raw_local_evidence": True,
            "privacy_note": "Review before sharing.",
        },
        "overview": {"operational": {}, "raw_capture_counts": {}},
        "effort_by_surface": [],
        "transitions": [],
        "repeated_workflow_fragments": [],
        "semantic_action_counts": [],
        "inferred_tasks": [],
        "repeated_task_families": [],
        "task_inference": {},
        "operational_events": [event],
        "operational_semantic_activity": [],
        "raw_local_evidence": [event],
    }


def test_export_is_ai_legible_without_changing_json_schema():
    from server.exporter import json_bytes

    payload = _minimal_payload()
    encoded = json_bytes(payload)
    decoded = json.loads(encoded)
    assert decoded.keys() == payload.keys()
    assert b"\n  " not in encoded


def test_csv_zip_contains_ai_readme_and_plain_semantic_columns():
    from server.exporter import csv_zip_bytes

    raw = csv_zip_bytes(_minimal_payload())
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert "README_FOR_AI.md" in zf.namelist()
        guide = zf.read("README_FOR_AI.md").decode("utf-8")
        assert "keypress_count" in guide
        assert "Amounts, company names" in guide
        csv_text = zf.read("raw_local_evidence.csv").decode("utf-8-sig")
        header = csv_text.splitlines()[0]
        assert "target_label" in header
        assert "page_host" in header
        assert "action" in header
        assert "Send" in csv_text
        assert "mail.google.com" in csv_text


def test_xlsx_overview_contains_ai_dictionary():
    from server.exporter import xlsx_bytes

    raw = xlsx_bytes(_minimal_payload())
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        searchable = b"\n".join(
            zf.read(name)
            for name in zf.namelist()
            if name.endswith(".xml")
        )
    assert b"AI data dictionary" in searchable
    assert b"keypress_count" in searchable
