from __future__ import annotations

from adapters.codex_config import config_snippet


def test_codex_config_exports_structural_logs_and_traces_without_content_logging():
    text = config_snippet(token="write-token", base_url="http://127.0.0.1:8787")
    assert "[otel]" in text
    assert "log_user_prompt = false" in text
    assert "log_agent_responses = false" in text
    assert "log_guardian_assessments = false" in text
    assert "exporter =" in text
    assert "trace_exporter" in text
    assert text.count("agent-ingest/v1/codex-otel") == 2
    assert text.count("Bearer write-token") == 2
    assert "metrics_exporter" not in text
