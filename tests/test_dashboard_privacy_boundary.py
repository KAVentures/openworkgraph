from __future__ import annotations

import json


def test_paged_dashboard_evidence_searches_rich_index_but_returns_minimized_rows():
    from server import db
    from server.evidence_query import query_evidence

    db.init_db()
    event_id = "dashboard-private-canary-evidence"
    private_subject = "PRIVATE_SUBJECT_CANARY_91827"
    private_name = "Anna Svensson"
    private_path = "mail.google.com/mail/u/0/#inbox/private-anna-case"

    with db.connect() as conn:
        conn.execute("DELETE FROM context_events WHERE event_id = ?", (event_id,))
        conn.execute(
            """
            INSERT INTO context_events(
              event_id, observed_at, session_id, source, surface, action,
              resource_title, resource_locator, target_label, context_text, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                "2099-01-01T12:00:00+00:00",
                "dashboard-privacy-session",
                "browser_extension",
                "Gmail",
                "click",
                f"{private_name} — {private_subject}",
                private_path,
                f"Send to {private_name}",
                f"Gmail | click | {private_name} | {private_subject} | {private_path}",
                json.dumps({"event_type": "browser_click"}),
            ),
        )

    result = query_evidence(scope="all", since=None, limit=20, q=private_subject)
    item = next(x for x in result["items"] if x["event_id"] == event_id)
    blob = json.dumps(item, ensure_ascii=False)

    # Rich local search remains useful, but arbitrary visible content never
    # crosses the browser boundary.
    assert result["total"] >= 1
    assert private_subject not in blob
    assert private_name not in blob
    assert private_path not in blob
    assert item["surface"] == "Gmail"
    assert item["page"] == "Gmail"
    assert item["resource_title"] == ""
    assert item["action"] == "Send"
    assert result["presentation_layer"] == "dashboard_content_minimized"


def test_dashboard_summary_uses_operational_layer_and_minimizes_live_status():
    from server import db, main
    from server.dashboard_privacy import dashboard_summary

    db.init_db()
    event_id = "dashboard-private-canary-summary"
    private_subject = "PRIVATE_SUMMARY_CANARY_44119"
    private_name = "Erik Nilsson"

    raw = {
        "event_id": event_id,
        "observed_at": "2099-01-01T12:01:00+00:00",
        "device_id": "dashboard-device",
        "session_id": "dashboard-privacy-session",
        "app": "Google Chrome",
        "window_title": f"{private_name} — {private_subject} - Gmail",
        "event_type": "browser_click",
        "duration_seconds": 0,
        "screenshot_path": None,
        "metadata": {
            "source": "browser_extension",
            "action": "click",
            "page": {
                "hostname": "mail.google.com",
                "pathname": "/mail/u/0/#inbox/private-case",
                "title": f"{private_name} — {private_subject} - Gmail",
            },
            "target": {"tag": "button", "label": f"Send to {private_name}"},
        },
    }
    db.insert_events([raw])

    main.COLLECTOR_STATUS.clear()
    main.COLLECTOR_STATUS.update(
        {
            "received_at": "2099-01-01T12:01:01+00:00",
            "window_title": f"{private_name} — {private_subject}",
            "app": "Google Chrome",
        }
    )
    main.BROWSER_STATUS.clear()
    main.BROWSER_STATUS.update(
        {
            "status": "connected",
            "received_at": "2099-01-01T12:01:01+00:00",
            "sensor_version": "test",
            "expected_sensor_version": "test",
            "version_ok": True,
            "hostname": "mail.google.com",
            "pathname": "/mail/u/0/#inbox/private-case",
            "page_title": f"{private_name} — {private_subject}",
        }
    )

    try:
        result = dashboard_summary(limit=10000, scope="all")
        blob = json.dumps(result, ensure_ascii=False)
        assert private_subject not in blob
        assert private_name not in blob
        assert "/mail/u/0/#inbox/private-case" not in blob
        assert result["data_layer"] == "operational_normalized"
        assert result["dashboard_data_layer"] == "operational_normalized"
        assert set(result["collector"].keys()) <= {"connected", "received_at"}
        assert "hostname" not in result["browser_sensor"]
        assert "pathname" not in result["browser_sensor"]
        assert "page_title" not in result["browser_sensor"]
    finally:
        main.COLLECTOR_STATUS.clear()
        main.BROWSER_STATUS.clear()


def test_work_profile_dashboard_response_drops_rich_context_echoes():
    from server.work_profile_routes import _dashboard_safe_profile

    private_name = "PRIVATE_WORK_PROFILE_NAME_55231"
    private_path = f"example.test/customer/{private_name}"
    profile = {
        "navigation_hunting_candidates": [
            {
                "surface": "CRM",
                "resource_locator": private_path,
                "visit_count": 4,
                "needs_review": True,
            }
        ],
        "rapid_click_candidates": [
            {
                "surface": "CRM",
                "resource_locator": private_path,
                "target_label": private_name,
                "max_clicks_in_1_5s": 4,
                "needs_review": True,
            }
        ],
    }

    safe = _dashboard_safe_profile(profile)
    blob = json.dumps(safe, ensure_ascii=False)
    assert private_name not in blob
    assert private_path not in blob
    assert safe["navigation_hunting_candidates"][0]["surface"] == "CRM"
    assert safe["navigation_hunting_candidates"][0]["visit_count"] == 4
    assert safe["rapid_click_candidates"][0]["max_clicks_in_1_5s"] == 4
    assert safe["dashboard_data_layer"] == "content_minimized"


def test_enterprise_runner_installs_dashboard_privacy_last():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    runner = (root / "server" / "enterprise_runner.py").read_text(encoding="utf-8")
    privacy = runner.index("import server.dashboard_privacy")
    assert privacy > runner.index("import server.evidence_paging")
    assert privacy > runner.index("import server.work_profile_routes")
