from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def _event(title: str) -> dict:
    return {
        "event_id": "leak-test-1",
        "observed_at": "2026-09-18T10:00:00+00:00",
        "schema_version": "1.0",
        "organization_id": "",
        "actor_id": "",
        "device_id": "d1",
        "sensor_id": "desktop:test",
        "source": "desktop",
        "session_id": "s1",
        "app": "Google Chrome",
        "window_title": title,
        "event_type": "focus_span",
        "duration_seconds": 10,
        "screenshot_path": None,
        "metadata": {"source": "desktop"},
    }


def test_validated_financial_and_credential_canaries_are_masked(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from sensitive_identifiers import redact_sensitive_identifiers

    canaries = [
        "IBAN GB82 WEST 1234 5698 7654 32",
        "Card 4111 1111 1111 1111",
        "AWS AKIAIOSFODNN7EXAMPLE",
        "OpenAI sk-abcdefghijklmnopqrstuvwxyz123456",
        "GitHub ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "JWT eyJabcdefghijk.abcdefghijk.abcdefghijk",
        "api_key=supersecretvalue123",
        "org.nr 556677-8899",
        "bankgiro 123-4567",
    ]
    for raw in canaries:
        safe = redact_sensitive_identifiers(raw)
        assert safe != raw, raw
        assert "AKIAIOSFODNN7EXAMPLE" not in safe
        assert "4111 1111 1111 1111" not in safe
        assert "GB82 WEST 1234 5698 7654 32" not in safe
        assert "supersecretvalue123" not in safe


def test_high_confidence_rules_preserve_business_semantics(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from sensitive_identifiers import redact_sensitive_identifiers

    ordinary = [
        "Project Falcon pricing review for Volvo Cars",
        "Candidate salary 62 000 SEK per month",
        "Deal value 2.4 MSEK",
        "Order 1234567890123456",  # deliberately non-Luhn
        "Matter ID discussed in weekly review",
        "Counterparty RESCUE Sverige",
    ]
    for raw in ordinary:
        assert redact_sensitive_identifiers(raw) == raw


def test_native_row_labels_drop_body_sized_text_but_buttons_keep_labels():
    from collector.accessibility import _safe_native_label

    long_row = "Anna Svensson Diabetes typ 2 Avd 4 Dr Koyar journal note and medication information for follow-up"
    assert _safe_native_label(long_row, row_like=True) is None
    assert _safe_native_label("Open patient", row_like=True) == "Open patient"
    assert _safe_native_label(long_row, row_like=False) == long_row


def test_collector_persists_only_sanitized_copy(monkeypatch, tmp_path):
    import collector.main as collector_main
    from collector.outbox import EventOutbox

    monkeypatch.setattr(collector_main, "LOCAL_DIR", tmp_path)
    raw = _event("Journal - Anna Svensson 19850412-1234 - Cosmic")
    outbox = EventOutbox(tmp_path / "collector_outbox.db")
    collector_main.persist_event(raw, outbox)

    jsonl = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    queued = json.dumps(outbox.pending(10), ensure_ascii=False)
    assert "19850412-1234" not in jsonl
    assert "19850412-1234" not in queued
    assert "PERSONNUMMER_" in jsonl
    assert "PERSONNUMMER_" in queued


def test_legacy_jsonl_and_pending_outbox_are_rewritten(monkeypatch, tmp_path):
    import collector.main as collector_main
    from collector.outbox import EventOutbox

    monkeypatch.setattr(collector_main, "LOCAL_DIR", tmp_path)
    raw = _event("Journal - Anna Svensson 19850412-1234 - Cosmic")
    (tmp_path / "events.jsonl").write_text(json.dumps(raw) + "\n", encoding="utf-8")
    collector_main._sanitize_existing_jsonl()
    assert "19850412-1234" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")

    outbox_path = tmp_path / "collector_outbox.db"
    first = EventOutbox(outbox_path)
    first.enqueue(raw)
    with sqlite3.connect(outbox_path) as conn:
        conn.execute(
            "UPDATE pending_events SET payload_json = ? WHERE event_id = ?",
            (json.dumps(raw), raw["event_id"]),
        )
        conn.commit()
    migrated = EventOutbox(outbox_path)
    assert "19850412-1234" not in json.dumps(migrated.pending(10))


def test_excluded_browser_heartbeat_does_not_leak_or_carry_previous_surface(monkeypatch):
    from server import main

    main.BROWSER_STATUS.clear()
    main.BROWSER_STATUS.update({
        "browser_session_id": "browser-runtime",
        "hostname": "chatgpt.com",
        "pathname": "/",
        "page_title": "ChatGPT",
        "work_surface": "ChatGPT",
    })
    monkeypatch.setattr(main, "_runtime_config", lambda: {
        "excluded_apps": [],
        "excluded_title_patterns": ["bank"],
        "excluded_browser_host_patterns": ["secretbank"],
    })
    status = main.BrowserHeartbeat(
        observed_at="2026-09-18T10:05:00+00:00",
        sensor_id="browser:test",
        sensor_version=main.EXPECTED_BROWSER_SENSOR_VERSION,
        browser_session_id="browser-runtime",
        work_session_id="s1",
        page={
            "origin": "https://secretbank.example",
            "hostname": "secretbank.example",
            "pathname": "/accounts",
            "title": "Mina konton - Privatbank",
        },
    )
    assert main.browser_heartbeat(status) == {"status": "ok"}
    assert main.BROWSER_STATUS["excluded"] is True
    for key in ("hostname", "pathname", "page_title", "work_surface"):
        assert key not in main.BROWSER_STATUS


def test_privacy_key_files_are_private_when_posix(monkeypatch, tmp_path):
    if os.name == "nt":
        return
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    from server import presentation
    from server.privacy_pipeline import initialize_privacy_state

    presentation._KEY_CACHE.clear()
    initialize_privacy_state()
    key_path = tmp_path / ".display_redaction_key"
    assert key_path.exists()
    assert key_path.stat().st_mode & 0o777 == 0o600
