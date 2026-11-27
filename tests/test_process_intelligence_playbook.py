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


def test_change_playbook_separates_observed_and_simulated_evidence():
    from mcp_server.process_intelligence_tools import evaluate_change_prompt
    text = evaluate_change_prompt("Add a second approver", "log_0123456789abcdef")
    assert "Add a second approver" in text and "compare_process_scenarios" in text
    assert "observed vs simulated" in text and "not a cause" in text
    assert "list_process_logs" in evaluate_change_prompt("", "nope")


def test_playbooks_tell_the_agent_to_filter_to_a_named_group():
    from mcp_server.process_intelligence_tools import (evaluate_change_prompt, investigate_process_prompt,
                                                       propose_monitors_prompt)
    for prompt in (investigate_process_prompt, propose_monitors_prompt, evaluate_change_prompt):
        text = prompt("Why are EU orders slow?", "log_0123456789abcdef")
        assert "case_filter" in text and "fewer than 5 cases is refused" in text
    assert "stray dates" in investigate_process_prompt()


def test_case_filter_guide_lists_every_cohort_key_and_valid_examples():
    import json
    import re
    from mcp_server.process_intelligence_tools import CASE_FILTER_GUIDE
    from server import event_log_cohorts as cohorts
    for key in cohorts._KEYS:
        assert f"{key}" in CASE_FILTER_GUIDE, key
    examples = re.findall(r"^- [^:]+: (\{.*\})$", CASE_FILTER_GUIDE.split("Examples:")[1].replace("\n  ", " "), re.M)
    assert len(examples) == 3
    for example in examples:
        cohorts.parse_cohort(json.loads(example))
