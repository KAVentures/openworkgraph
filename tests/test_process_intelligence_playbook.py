from mcp_server.process_intelligence_tools import investigate_process_prompt


def test_playbook_targets_a_valid_log_and_bounds_the_question():
    text = investigate_process_prompt("Why are EU orders slow?", "log_0123456789abcdef")
    assert "Why are EU orders slow?" in text and "log_0123456789abcdef" in text
    assert "get_process_overview" in text and "not causes" in text


def test_playbook_ignores_a_malformed_log_id_and_long_questions():
    text = investigate_process_prompt("x" * 2000, "../etc")
    assert "../etc" not in text and "list_process_logs" in text
    assert "x" * 501 not in text
    assert "Question: How does this process perform" in investigate_process_prompt()


def test_monitor_playbook_uses_preview_and_never_saves():
    from mcp_server.process_intelligence_tools import propose_monitors_prompt
    text = propose_monitors_prompt("Spot slow approvals", "log_0123456789abcdef")
    assert "Spot slow approvals" in text and "log_0123456789abcdef" in text
    assert "preview_process_signal" in text and "cannot save" in text
    assert "list_process_logs" in propose_monitors_prompt("", "bad")
