from __future__ import annotations

import importlib
import json


def _presentation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)
    from server.first_name_policy import install as install_first_name
    from server.mail_row_policy import install as install_mail_row
    from server.typed_privacy_policy import install as install_typed_privacy
    install_first_name(presentation)
    install_mail_row(presentation)
    install_typed_privacy(presentation)
    monkeypatch.setattr(
        presentation,
        "_owner_identity",
        lambda: ({"koyar afrasyab": "OWNER", "koyar": "OWNER"}, set(), set()),
    )
    return presentation


def test_ehr_name_and_personnummer_mask_without_vendor_allowlist(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Cosmic (EHR)",
        "window_title": "Remiss: Erik Lindqvist, personnr 640321-9876",
    })

    assert "Erik Lindqvist" not in safe["window_title"]
    assert "640321-9876" not in safe["window_title"]
    assert "PERSON_" in safe["window_title"]
    assert "PERSONNUMMER_" in safe["window_title"]
    assert safe["window_title"].startswith("Remiss: ")


def test_personnummer_adjacency_masks_name_without_any_app_context(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Unknown Internal System",
        "window_title": "Journal - Anna Svensson 19850412-1234",
    })

    assert "Anna Svensson" not in safe["window_title"]
    assert "19850412-1234" not in safe["window_title"]
    assert "PERSONNUMMER_" in safe["window_title"]


def test_patient_label_is_person_evidence_without_app_name(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Internal Tool 17",
        "title": "Patient: Erik Lindqvist",
    })

    assert safe["title"].startswith("Patient: PERSON_")
    assert "Erik Lindqvist" not in safe["title"]


def test_title_case_organizations_and_products_survive(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    examples = [
        "Region Skåne",
        "Volvo Cars",
        "Klarna Checkout",
        "Sankt Görans Sjukhus",
    ]
    for title in examples:
        safe = presentation.redact_for_display({
            "surface": "Notion",
            "hostname": "notion.so",
            "window_title": title,
        })
        assert safe["window_title"] == title


def test_structured_url_is_sanitized_before_name_redaction(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Cosmic",
        "resource_locator": "https://ehr.example/patient/123456789?patient=Anna+Svensson&token=SECRET#journal",
    })

    assert safe["resource_locator"] == "https://ehr.example/patient/:id"
    assert "Anna" not in safe["resource_locator"]
    assert "Svensson" not in safe["resource_locator"]
    assert "SECRET" not in safe["resource_locator"]
    assert "?" not in safe["resource_locator"]


def test_schemeless_structured_locator_is_also_sanitized(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "resource_locator": "ehr.example/patient/123456789?patient=Anna+Svensson#journal",
    })
    assert safe["resource_locator"] == "ehr.example/patient/:id"


def test_identifier_detector_handles_personnummer_coordination_and_explicit_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import sensitive_identifiers as identifiers
    importlib.reload(identifiers)

    text = (
        "personnr 640321-9876; samordningsnr 850472-1234; "
        "patient ID ABC12345; case number 67448291; account number 88776655"
    )
    safe = identifiers.redact_sensitive_identifiers(text)

    for literal in ("640321-9876", "850472-1234", "ABC12345", "67448291", "88776655"):
        assert literal not in safe
    assert "PERSONNUMMER_" in safe
    assert "PATIENT_ID_" in safe
    assert "CASE_ID_" in safe
    assert "ACCOUNT_ID_" in safe


def test_invalid_unseparated_business_number_is_not_guessed_as_personnummer(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import sensitive_identifiers as identifiers
    importlib.reload(identifiers)

    raw = "Order reference 6403219876"
    assert identifiers.redact_sensitive_identifiers(raw) == raw
    assert "6403219876" not in identifiers.redact_sensitive_identifiers("personnr 6403219876")


def test_new_events_pseudonymize_sensitive_ids_before_database_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.db as db
    importlib.reload(db)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workflow_observer.db")
    from server.sensitive_storage_policy import install
    install(db)
    db.init_db()

    raw = {
        "event_id": "v40-storage-1",
        "observed_at": "2026-09-17T20:00:00+00:00",
        "device_id": "device",
        "session_id": "session",
        "source": "desktop",
        "app": "Cosmic",
        "window_title": "Remiss: Erik Lindqvist, personnr 640321-9876",
        "event_type": "focus_span",
        "duration_seconds": 10,
        "metadata": {"patient_id": "patient ID ABC12345"},
    }
    db.insert_events([raw])
    stored = db.rows("SELECT * FROM events WHERE event_id = ?", (raw["event_id"],))[0]
    serialized = json.dumps(stored, ensure_ascii=False)

    assert "640321-9876" not in serialized
    assert "ABC12345" not in serialized
    assert "PERSONNUMMER_" in serialized
    assert "PATIENT_ID_" in serialized
    # Names remain rich locally for inference; display masking is downstream.
    assert "Erik Lindqvist" in serialized


def test_legacy_rows_are_migrated_once_and_derived_rows_rebuilt(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.db as db
    importlib.reload(db)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workflow_observer.db")
    from server.sensitive_storage_policy import MIGRATION_KEY, install
    install(db)
    db.init_db()

    # Simulate a literal row captured by a pre-v0.40 build.
    with db.connect() as conn:
        conn.execute("DELETE FROM privacy_migrations WHERE migration_key = ?", (MIGRATION_KEY,))
        raw = {
            "event_id": "legacy-sensitive-1",
            "observed_at": "2026-09-17T19:00:00+00:00",
            "device_id": "device",
            "session_id": "session",
            "source": "desktop",
            "app": "Cosmic",
            "window_title": "Journal - Anna Svensson 19850412-1234",
            "event_type": "focus_span",
            "duration_seconds": 5,
            "metadata": {},
        }
        db._insert_event(conn, "events", raw)
        db._insert_event(conn, "normalized_events", db.normalize_event(raw))
        db._insert_context(conn, db.contextualize_event(raw))

    changed = db.harden_existing_sensitive_identifiers()
    assert changed == 1
    assert db.harden_existing_sensitive_identifiers() == 0

    stored = db.rows("SELECT * FROM events WHERE event_id = 'legacy-sensitive-1'")[0]
    normalized = db.normalized_rows("SELECT * FROM normalized_events WHERE event_id = 'legacy-sensitive-1'")[0]
    context = db.context_rows("SELECT * FROM context_events WHERE event_id = 'legacy-sensitive-1'")[0]
    serialized = json.dumps([stored, normalized, context], ensure_ascii=False)
    assert "19850412-1234" not in serialized
    assert "PERSONNUMMER_" in serialized


def test_localized_gmail_first_name_sender_still_redacts(monkeypatch, tmp_path):
    presentation = _presentation(monkeypatch, tmp_path)
    safe = presentation.redact_for_display({
        "surface": "Gmail",
        "hostname": "mail.google.com",
        "label": "click: oläst, Julia, RESCUE Sverige",
    })
    assert safe["label"] == "click: oläst, PERSON, RESCUE Sverige"
