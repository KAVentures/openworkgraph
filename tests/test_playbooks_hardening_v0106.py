from __future__ import annotations

import pytest

from server.playbooks import FORMAT, PlaybookError, sanitize


def test_imported_steps_must_be_real_openworkgraph_structure():
    raw = {
        "format": FORMAT,
        "name": "Fix failing tests",
        "family_key": "agent:structure:0123456789abcdef",
        "runs_observed": 3,
        "agent_frameworks": ["claude-code", "ignore_previous_instructions"],
        "typical_steps": [
            "model_call",
            "tool:shell:Bash",
            "ignore_previous_instructions",
            "run_rm_rf",
            "f:0123456789abcdef",
            "w:0123456789abcdef",
            "execution:0123456789abcdef",
        ],
    }
    clean = sanitize(raw)
    assert clean["typical_steps"] == ["model_call", "tool:shell:Bash"]
    assert clean["agent_frameworks"] == ["claude-code"]
    dump = repr(clean)
    assert "ignore_previous" not in dump and "f:0123" not in dump and "w:0123" not in dump


def test_imported_family_key_is_a_real_generated_family_not_arbitrary_agent_visible_text():
    raw = {
        "format": FORMAT,
        "name": "Safe name",
        "family_key": "ignore_previous_instructions",
        "runs_observed": 2,
    }
    with pytest.raises(PlaybookError):
        sanitize(raw)


def test_canonical_failure_and_opaque_tool_steps_still_round_trip():
    raw = {
        "format": FORMAT,
        "name": "Safe structure",
        "family_key": "agent:workflow:fedcba9876543210",
        "runs_observed": 2,
        "typical_steps": [
            "approval_request",
            "approval_received:denied",
            "error:timeout",
            "tool:code:tool:0123456789ab:error",
        ],
    }
    assert sanitize(raw)["typical_steps"] == raw["typical_steps"]
