from __future__ import annotations

from pathlib import Path


def test_browser_heartbeat_source_reports_sanitized_active_tab():
    root = Path(__file__).resolve().parents[1]
    background = (root / "browser_extension" / "background.js").read_text(encoding="utf-8")
    manifest = (root / "browser_extension" / "manifest.json").read_text(encoding="utf-8")

    assert 'ext.tabs.query({active: true, lastFocusedWindow: true})' in background
    assert 'page,' in background
    assert 'safeUrl(tab?.url || "")' in background
    assert '"version": "1.10.0"' in manifest
    assert '"version_name": "1.10.0-v52-workflow-links"' in manifest


def test_browser_heartbeat_keeps_safe_active_page_and_surface():
    from server import main

    main.BROWSER_STATUS.clear()
    status = main.BrowserHeartbeat(
        observed_at="2026-09-18T12:00:00+00:00",
        status="connected",
        sensor_id="browser:test",
        sensor_version=main.EXPECTED_BROWSER_SENSOR_VERSION,
        browser_session_id="browser-runtime",
        work_session_id="work-session",
        page={
            "origin": "https://chatgpt.com",
            "hostname": "chatgpt.com",
            "pathname": "/c/123456789?token=SECRET#fragment",
            "title": "Enterprise Task Mining Software",
        },
    )

    assert main.browser_heartbeat(status) == {"status": "ok"}
    assert main.BROWSER_STATUS["hostname"] == "chatgpt.com"
    assert main.BROWSER_STATUS["pathname"] == "/c/:id"
    assert main.BROWSER_STATUS["work_surface"] == "ChatGPT"
    assert main.BROWSER_STATUS["work_session_id"] == "work-session"
    assert "SECRET" not in str(main.BROWSER_STATUS)


def test_live_active_tab_labels_only_new_unresolved_browser_rows():
    from server import main

    main.BROWSER_STATUS.clear()
    main.BROWSER_STATUS.update({
        "observed_at": "2026-09-18T12:00:00+00:00",
        "hostname": "chatgpt.com",
        "pathname": "/c/example",
        "page_title": "Enterprise Task Mining Software",
        "work_surface": "ChatGPT",
        "work_session_id": "work-session",
    })
    result = {
        "recent_evidence": [
            {
                "observed_at": "2026-09-18T12:00:05+00:00",
                "session_id": "work-session",
                "source": "desktop",
                "app": "Google Chrome",
                "window_title": (
                    "Enterprise Task Mining Software – Hög minnesanvändning – "
                    "1,6 GB - Google Chrome – OWNER (kinvectum.com)"
                ),
                "label": "click: AXGroup",
            },
            {
                "observed_at": "2026-09-18T11:59:30+00:00",
                "session_id": "work-session",
                "source": "desktop",
                "app": "Google Chrome",
                "window_title": "Earlier browser work - Google Chrome",
            },
            {
                "observed_at": "2026-09-18T12:00:06+00:00",
                "session_id": "work-session",
                "source": "desktop",
                "app": "Finder",
                "window_title": "Downloads",
            },
        ]
    }

    main._apply_live_browser_surface(result)

    current, earlier, finder = result["recent_evidence"]
    assert current["app"] == "Google Chrome"
    assert current["work_surface"] == "ChatGPT"
    assert current["browser_hostname"] == "chatgpt.com"
    assert current["browser_context_join"] == "live_active_tab"
    assert "work_surface" not in earlier
    assert "work_surface" not in finder


def test_existing_resolved_surface_is_never_overwritten():
    from server import main

    main.BROWSER_STATUS.clear()
    main.BROWSER_STATUS.update({
        "observed_at": "2026-09-18T12:00:00+00:00",
        "hostname": "chatgpt.com",
        "pathname": "/",
        "page_title": "ChatGPT",
        "work_surface": "ChatGPT",
        "work_session_id": "work-session",
    })
    result = {
        "recent_evidence": [{
            "observed_at": "2026-09-18T12:00:10+00:00",
            "session_id": "work-session",
            "source": "desktop",
            "app": "Google Chrome",
            "window_title": "Inbox - Google Chrome",
            "work_surface": "Gmail",
            "browser_hostname": "mail.google.com",
        }]
    }

    main._apply_live_browser_surface(result)
    assert result["recent_evidence"][0]["work_surface"] == "Gmail"
    assert result["recent_evidence"][0]["browser_hostname"] == "mail.google.com"


def test_dashboard_prefers_work_surface_suffix():
    root = Path(__file__).resolve().parents[1]
    html = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "const src=x.work_surface||(x.source==='browser'?'browser':'desktop');" in html
