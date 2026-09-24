from __future__ import annotations

from adapters.codex_config import config_snippet


def test_codex_config_uses_trace_only_and_disables_content_logging():
    text = config_snippet(token="write-token", base_url="http://127.0.0.1:8787")
    assert "[otel]" in text
    assert "log_user_prompt = false" in text
    assert "log_agent_responses = false" in text
    assert "log_guardian_assessments = false" in text
    assert "trace_exporter" in text
    assert "agent-ingest/v1/codex-otel" in text
    assert "Bearer write-token" in text
    assert "exporter =" not in text.replace("trace_exporter =", "")
    assert "metrics_exporter" not in text
