"""Browser AI run reconstruction only from explicit content-free lifecycle events."""
from gateway.browser_ai_run_recall import browser_ai_agent_runs


def row(event_id, action, at, *, provider="chatgpt", run="web-abcdef1234567890",
        session="desktop-s1", source="browser_extension", metadata=None):
    return {
        "event_id": event_id, "observed_at": at,
        "source": source, "session_id": session,
        "event_type": "browser_" + action,
        "window_title": "SENSITIVE_TITLE_NEVER_INCLUDED",
        "metadata": {"agent_provider": provider, "agent_run_id": run,
                     "message_body": "PRIVATE_MESSAGE_NEVER_INCLUDED",
                     **(metadata or {})},
    }


def test_web_ai_run_requires_observed_start_and_keeps_unknown_outcome():
    rows = [
        row("s", "agent_run_started", "2026-10-13T08:00:00Z"),
        row("f", "agent_run_finished", "2026-10-13T08:00:45Z"),
    ]
    runs = browser_ai_agent_runs(rows)
    assert len(runs) == 1
    run = runs[0]
    assert run["actor_kind"] == "agent"
    assert run["provider"] == "chatgpt"
    assert run["duration_seconds"] == 45
    assert run["outcome_status"] == "unknown"
    assert run["outcome_basis"] == "browser_ui_signals_not_verified_business_success"
    assert run["steps"] == ["run_started", "run_finished"]
    assert run["task_identity_confirmed"] is False
    assert "SENSITIVE_TITLE" not in str(run)
    assert "PRIVATE_MESSAGE" not in str(run)


def test_agent_run_matching_excludes_noise_foreign_source_and_unstarted():
    rows = [
        row("noise", "click", "2026-10-13T08:00:00Z"),
        row("err", "agent_error", "2026-10-13T08:01:00Z"),
        row("native", "agent_run_started", "2026-10-13T08:01:00Z", source="agent"),
        row("bad", "agent_run_started", "2026-10-13T08:01:00Z", run="raw name"),
    ]
    assert browser_ai_agent_runs(rows) == []


def test_multiple_web_ai_runs_are_not_merged_across_sessions():
    rows = [
        row("a1", "agent_run_started", "2026-10-13T08:00:00Z"),
        row("a2", "agent_run_cancelled", "2026-10-13T08:00:10Z"),
        row("b1", "agent_run_started", "2026-10-13T08:10:00Z",
            provider="claude", session="desktop-s2"),
        row("b2", "agent_run_finished", "2026-10-13T08:12:00Z",
            provider="claude", session="desktop-s2"),
    ]
    runs = browser_ai_agent_runs(rows)
    assert len(runs) == 2
    assert runs[1]["outcome_status"] == "cancelled"
    assert runs[0]["outcome_status"] == "unknown"
    assert runs[0]["execution_id"] != runs[1]["execution_id"]


def test_browser_run_preserves_only_valid_scoped_resource_refs():
    ref = "owg:r:" + "a" * 32
    rows = [
        row("s", "agent_run_started", "2026-10-13T08:00:00Z",
            metadata={"resource_reference": {"resource_ref": ref}}),
        row("f", "agent_run_finished", "2026-10-13T08:01:00Z",
            metadata={"resource_reference": {"resource_ref": "https://secret.url"}}),
    ]
    runs = browser_ai_agent_runs(rows)
    assert runs[0]["resource_refs"] == [ref]
    assert "secret.url" not in str(runs)


def test_run_and_separate_conversation_resource_link_by_same_opaque_tab_and_time():
    ref_one = "owg:r:" + "a" * 32
    ref_other = "owg:r:" + "b" * 32
    tab = "ref_" + "c" * 24
    runtime = "browser-runtime-s1"
    rows = [
        {
            "event_id": "resource-before", "source": "browser_extension",
            "event_type": "browser_resource_reference_observed",
            "observed_at": "2026-10-13T08:00:00Z", "session_id": "desktop-s1",
            "metadata": {"tab_id": tab, "browser_session_id": runtime,
                         "resource_reference": {"provider": "chatgpt", "resource_ref": ref_one}},
        },
        {
            "event_id": "resource-other-tab", "source": "browser_extension",
            "event_type": "browser_resource_reference_observed",
            "observed_at": "2026-10-13T08:00:01Z", "session_id": "desktop-s1",
            "metadata": {"tab_id": "ref_" + "d" * 24, "browser_session_id": runtime,
                         "resource_reference": {"provider": "chatgpt", "resource_ref": ref_other}},
        },
        row("start", "agent_run_started", "2026-10-13T08:01:00Z",
            metadata={"tab_id": tab, "browser_session_id": runtime}),
        row("finish", "agent_run_finished", "2026-10-13T08:01:25Z",
            metadata={"tab_id": tab, "browser_session_id": runtime}),
    ]
    result = browser_ai_agent_runs(rows)
    assert len(result) == 1
    assert result[0]["resource_refs"] == [ref_one]
    assert ref_other not in str(result)


def test_new_chat_same_tab_prefers_first_conversation_seen_after_run_start():
    # A's pointer persists in the same tab. The new chat B only receives its
    # conversation UUID after the first message has started the Web AI run.
    for provider in ("chatgpt", "claude"):
        ref_a = "owg:r:" + "a" * 32
        ref_b = "owg:r:" + "b" * 32
        ref_c = "owg:r:" + "c" * 32
        tab = "ref_" + "d" * 24
        runtime = "browser-runtime-s1"

        def resource(event_id, at, ref):
            return {
                "event_id": event_id, "source": "browser_extension",
                "event_type": "browser_resource_reference_observed",
                "observed_at": at, "session_id": "desktop-s1",
                "metadata": {
                    "tab_id": tab, "browser_session_id": runtime,
                    "resource_reference": {"provider": provider, "resource_ref": ref},
                },
            }

        rows = [
            resource("conversation-a", "2026-10-13T08:00:00Z", ref_a),
            row("start", "agent_run_started", "2026-10-13T08:10:00Z",
                provider=provider,
                metadata={"tab_id": tab, "browser_session_id": runtime}),
            resource("conversation-b", "2026-10-13T08:10:05Z", ref_b),
            # A different conversation opened later must not replace B.
            resource("conversation-c", "2026-10-13T08:10:20Z", ref_c),
            row("finish", "agent_run_finished", "2026-10-13T08:10:45Z",
                provider=provider,
                metadata={"tab_id": tab, "browser_session_id": runtime}),
        ]
        result = browser_ai_agent_runs(rows)
        assert len(result) == 1
        assert result[0]["resource_refs"] == [ref_b]
        assert ref_a not in str(result)
        assert ref_c not in str(result)


def test_post_start_conversation_pointer_must_be_within_30_seconds():
    ref = "owg:r:" + "e" * 32
    tab = "ref_" + "f" * 24
    runtime = "browser-runtime-s1"
    rows = [
        row("start", "agent_run_started", "2026-10-13T08:00:00Z",
            metadata={"tab_id": tab, "browser_session_id": runtime}),
        {
            "event_id": "too-late", "source": "browser_extension",
            "event_type": "browser_resource_reference_observed",
            "observed_at": "2026-10-13T08:00:31Z", "session_id": "desktop-s1",
            "metadata": {"tab_id": tab, "browser_session_id": runtime,
                         "resource_reference": {"provider": "chatgpt",
                                                "resource_ref": ref}},
        },
        row("finish", "agent_run_finished", "2026-10-13T08:01:00Z",
            metadata={"tab_id": tab, "browser_session_id": runtime}),
    ]
    assert browser_ai_agent_runs(rows)[0]["resource_refs"] == []


def test_device_upload_preserves_only_opaque_browser_tab_correlation():
    from connector.cloud_privacy import project_event_for_upload

    def project(tab_id):
        return project_event_for_upload({
            "event_id": "evt-test", "observed_at": "2026-10-13T08:00:00Z",
            "source": "browser_extension", "event_type": "browser_agent_run_started",
            "app": "Google Chrome", "metadata": {
                "agent_provider": "chatgpt", "agent_run_id": "web-abcdef1234567890",
                "browser_session_id": "browser-runtime-s1", "tab_id": tab_id,
                "message_body": "NEVER_SEND_MY_SECRET",
            }
        }, secret="local-sufficiently-long-random-secret")["metadata"]

    first, second = project(7), project(7)
    assert first["tab_id"] == second["tab_id"]
    assert first["tab_id"].startswith("ref_")
    assert first["tab_id"] != "7"
    assert first["agent_run_id"] == "web-abcdef1234567890"
    assert "NEVER_SEND_MY_SECRET" not in str(first)
