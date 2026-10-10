from __future__ import annotations

import pytest

from mcp_server import compact as mcp


def test_local_work_text_tool_bounds_reads_and_marks_content_untrusted(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp.core, "_begin", lambda name: None)
    monkeypatch.setattr(mcp.core, "_finish", lambda name, output: output)

    def fake_get(path, params):
        calls.append((path, params))
        return {
            "source": "local_opt_in_browser_text",
            "items": [{"kind": "page", "redacted_text": "Budget review notes."}],
            "gateway_shared": False,
        }
    monkeypatch.setattr(mcp.secure_runtime, "secure_get", fake_get)
    result = mcp.get_opted_in_work_text(100)
    assert calls == [("/v1/work-text/ai", {"limit": 10})]
    assert result["gateway_shared"] is False
    assert "untrusted" in result["usage"].lower()


def test_local_work_text_tool_fails_closed_on_unavailable_api(monkeypatch):
    monkeypatch.setattr(mcp.core, "_begin", lambda name: None)
    monkeypatch.setattr(mcp.secure_runtime, "secure_get", lambda path, params: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(Exception, match="user must explicitly enable"):
        mcp.get_opted_in_work_text()
