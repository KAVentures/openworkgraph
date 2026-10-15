"""Browser copy/paste correlation survives raw → contextualized projection."""
from __future__ import annotations

from shared.core.contextualizer import contextualize_event
from server.work_profile import _transfer_patterns, _clipboard_observation_summary


def _event(number: int, action: str, host: str, *, link: str = "transfer-safe-1") -> dict:
    return {
        "event_id": f"browser-e{number}", "event_type": f"browser_{action}",
        "observed_at": f"2026-10-15T10:00:0{number}Z",
        "schema_version": "1.0", "source": "browser_extension",
        "session_id": "browser-session-1", "app": "Chrome",
        "window_title": host, "duration_seconds": 0,
        "metadata": {
            "source": "browser_extension", "action": action,
            "page": {"hostname": host, "pathname": "/work"},
            "clipboard_transfer_id": link,
            "clipboard_contents_captured": False,
            "clipboard_source_observed": True,
            "tab_context_id": f"browser:tab{number}",
        },
    }


def test_browser_copy_and_paste_preserve_linkage_without_clipboard_value():
    copy = contextualize_event(_event(1, "copy", "example-crm.test"))
    paste = contextualize_event(_event(2, "paste", "example-sheet.test"))
    assert copy["metadata"]["clipboard_transfer_id"] == "transfer-safe-1"
    assert paste["metadata"]["clipboard_transfer_id"] == "transfer-safe-1"
    assert copy["metadata"]["clipboard_contents_captured"] is False
    assert copy["metadata"]["tab_context_id"] == "browser:tab1"
    assert paste["metadata"]["tab_context_id"] == "browser:tab2"
    assert "clipboard_text" not in str(copy)
    transfers = _transfer_patterns([copy, paste])
    assert len(transfers) == 1
    assert transfers[0]["count"] == 1
    assert transfers[0]["cross_surface"] is True
    assert _clipboard_observation_summary([copy, paste])["pastes_with_transfer_id"] == 1


def test_invalid_tab_context_is_not_forwarded_to_derivative_evidence():
    raw = _event(1, "copy", "example-crm.test")
    raw["metadata"]["tab_context_id"] = "browser:https://secret.example/?token=private"
    raw["metadata"]["clipboard_transfer_id"] = "https://secret.example/?token=private"
    raw["metadata"]["clipboard_source_tab_context_id"] = "customer@example.com"
    result = contextualize_event(raw)
    assert "tab_context_id" not in result["metadata"]
    assert "clipboard_transfer_id" not in result["metadata"]
    assert "clipboard_source_tab_context_id" not in result["metadata"]
    assert "private" not in str(result)
    assert "customer@example.com" not in str(result)
