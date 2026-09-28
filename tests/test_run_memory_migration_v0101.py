from __future__ import annotations


def test_old_ephemeral_policy_does_not_silently_enable_run_memory():
    from shared.history_policy import _normalize

    old = {
        "version": 1,
        "onboarding_complete": True,
        "human_retention": {"mode": "ephemeral", "days": None},
        "agent_retention": {"mode": "ephemeral", "days": None},
        "history_generation": 1,
        "session_tombstones": [],
        "ai_history_access": {"mode": "off"},
    }
    assert _normalize(old)["run_memory"] == {"enabled": False, "days": 90}


def test_upgrade_keeps_run_memory_for_users_who_already_keep_history():
    from shared.history_policy import _normalize

    old = {
        "version": 1,
        "onboarding_complete": True,
        "human_retention": {"mode": "forever", "days": None},
        "agent_retention": {"mode": "ephemeral", "days": None},
        "history_generation": 1,
        "session_tombstones": [],
        "ai_history_access": {"mode": "off"},
    }
    assert _normalize(old)["run_memory"] == {"enabled": True, "days": 90}
    explicit = {**old, "run_memory": {"enabled": False, "days": 30}}
    assert _normalize(explicit)["run_memory"] == {"enabled": False, "days": 30}
