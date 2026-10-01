from __future__ import annotations


def test_compact_trace_row_keeps_surface_and_protected_title_without_rich_metadata():
    from mcp_server.compact_hardening import _compact_trace_row

    row = _compact_trace_row({
        "event_id": "e1",
        "observed_at": "2026-10-01T08:00:00Z",
        "app": "Google Chrome",
        "work_surface": "Gmail",
        "window_title": "Re: Contract renewal - PERSON_ABC123 - Gmail",
        "event_type": "focus_span",
        "duration_seconds": 30,
        "metadata": {"private": "DO NOT EXPOSE"},
    })

    assert row["app"] == "Google Chrome"
    assert row["work_surface"] == "Gmail"
    assert row["window_title"] == "Re: Contract renewal - PERSON_ABC123 - Gmail"
    assert "metadata" not in row
    assert "DO NOT EXPOSE" not in str(row)


def test_compact_trace_row_can_derive_surface_from_safe_page_metadata():
    from mcp_server.compact_hardening import _compact_trace_row

    row = _compact_trace_row({
        "app": "Google Chrome",
        "window_title": "Customer account - Salesforce",
        "event_type": "browser_click",
        "metadata": {
            "page": {
                "surface": "Salesforce",
                "hostname": "example.my.salesforce.com",
                "pathname": "/lightning/r/Account/SECRET",
            },
            "arbitrary": "not returned",
        },
    })

    assert row["work_surface"] == "Salesforce"
    assert row["window_title"] == "Customer account - Salesforce"
    assert "pathname" not in row
    assert "arbitrary" not in row


def test_compact_trace_identity_is_bounded():
    from mcp_server.compact_hardening import _compact_trace_row

    row = _compact_trace_row({
        "app": "Google Chrome",
        "work_surface": "S" * 500,
        "window_title": "T" * 1000,
        "event_type": "focus_span",
    })

    assert len(row["work_surface"]) <= 120
    assert len(row["window_title"]) <= 240
    assert row["work_surface"].endswith("…")
    assert row["window_title"].endswith("…")
