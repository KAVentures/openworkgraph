from server import analytics


def _focus(ts, title, duration, keys=0, engaged=0, app="Google Chrome"):
    return {
        "session_id": "s1",
        "observed_at": ts,
        "app": app,
        "window_title": title,
        "event_type": "focus_span",
        "duration_seconds": duration,
        "metadata": {"activity": {"engaged_seconds": engaged, "keypress_count": keys, "click_count": 2, "scroll_count": 0}},
    }


def _browser(ts, host, path, action, label="", title=""):
    return {
        "session_id": "s1",
        "observed_at": ts,
        "app": "Google Chrome",
        "window_title": title or host,
        "event_type": f"browser_{action}",
        "duration_seconds": 0,
        "metadata": {
            "action": action,
            "page": {"hostname": host, "pathname": path, "title": title or host},
            "target": {"tag": "button", "label": label} if label else {},
        },
    }


def test_candidate_task_preserves_effort_and_uses_observed_completion_label(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "GitHub", 40, keys=65, engaged=35),
        _browser("2026-01-01T00:00:05Z", "github.com", "/new", "click", "New repository", "GitHub"),
        _browser("2026-01-01T00:00:25Z", "github.com", "/new", "focus_control", "Repository name", "GitHub"),
        _browser("2026-01-01T00:00:35Z", "github.com", "/new", "click", "Create repository", "GitHub"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks()
    assert len(out["tasks"]) == 1
    task = out["tasks"][0]
    assert task["suggested_label"] == "Create repository"
    assert task["task_family"] == "github.create_repository"
    assert task["label_evidence"] == "Create repository"
    assert task["confidence"] == "medium"
    assert task["keypress_count"] == 65
    assert task["engaged_seconds"] == 35
    assert task["primary_surface"] == "GitHub"
    assert task["needs_review"] is True


def test_long_gap_splits_candidate_tasks(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "Gmail", 20, keys=10, engaged=18),
        _browser("2026-01-01T00:00:10Z", "mail.google.com", "/mail/u/0", "click", "Reply", "Gmail"),
        _focus("2026-01-01T00:05:00Z", "Google Docs", 30, keys=50, engaged=25),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=120)
    assert len(out["tasks"]) == 2
    labels = {x["primary_surface"] for x in out["tasks"]}
    assert "Gmail" in labels
    assert "Google Docs" in labels


def test_repeated_task_patterns_group_same_signature(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "GitHub", 30, keys=30, engaged=25),
        _browser("2026-01-01T00:00:20Z", "github.com", "/new", "click", "Create repository", "GitHub"),
        _focus("2026-01-01T00:03:00Z", "GitHub", 35, keys=40, engaged=30),
        _browser("2026-01-01T00:03:20Z", "github.com", "/new", "click", "Create repository", "GitHub"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=60)
    assert len(out["patterns"]) == 1
    pattern = out["patterns"][0]
    assert pattern["observed_count"] == 2
    assert pattern["keypress_count"] == 70
    assert pattern["total_engaged_seconds"] == 55
    assert "Create repository" in pattern["suggested_label"]


def test_completion_splits_two_tasks_inside_one_focus_span_without_double_counting(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "GitHub", 60, keys=90, engaged=54),
        _browser("2026-01-01T00:00:05Z", "github.com", "/new", "click", "New repository", "GitHub"),
        _browser("2026-01-01T00:00:20Z", "github.com", "/new", "click", "Create repository", "GitHub"),
        _browser("2026-01-01T00:00:30Z", "github.com", "/issues/new", "click", "New issue", "GitHub"),
        _browser("2026-01-01T00:00:50Z", "github.com", "/issues/new", "click", "Submit new issue", "GitHub"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks()
    assert len(out["tasks"]) == 2
    chronological = list(reversed(out["tasks"]))
    first, second = chronological
    assert "Create repository" in first["suggested_label"]
    assert second["suggested_label"] == "Create issue"
    assert second["task_family"] == "github.create_issue"
    assert first["boundary"]["end_reason"] == "explicit_completion"
    assert first["effort_estimated"] is True
    assert second["effort_estimated"] is True
    # The 10-second transition between completion and the next observed action is
    # intentionally unassigned, so subtask effort cannot exceed the parent span.
    assert first["keypress_count"] + second["keypress_count"] <= 90
    assert first["foreground_seconds"] + second["foreground_seconds"] <= 60


def test_repeated_family_normalizes_dynamic_identifiers(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "Salesforce", 25, keys=20, engaged=20),
        _browser("2026-01-01T00:00:20Z", "acme.salesforce.com", "/invoice/12345", "click", "Approve invoice #12345", "Salesforce"),
        _focus("2026-01-01T00:03:00Z", "Salesforce", 30, keys=25, engaged=24),
        _browser("2026-01-01T00:03:25Z", "acme.salesforce.com", "/invoice/67890", "click", "Approve invoice #67890", "Salesforce"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=60)
    assert len(out["patterns"]) == 1
    pattern = out["patterns"][0]
    assert pattern["observed_count"] == 2
    assert pattern["signature"] == "salesforce|approve invoice"
    assert pattern["surface_variant_count"] >= 1
    assert "p90_engaged_seconds" in pattern



def test_passive_navigation_after_completion_does_not_create_new_task(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "GitHub", 45, keys=30, engaged=35),
        _browser("2026-01-01T00:00:10Z", "github.com", "/new", "click", "Create repository", "GitHub"),
        _browser("2026-01-01T00:00:10.300000Z", "github.com", "/acme/newrepo", "page_view", "", "GitHub"),
        _browser("2026-01-01T00:00:11Z", "github.com", "/acme/newrepo", "page_view", "", "GitHub"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks()
    assert len(out["tasks"]) == 1
    assert out["tasks"][0]["suggested_label"] == "Create repository"


def test_weak_completion_like_substep_does_not_split_task(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "Internal Tool", 60, keys=45, engaged=50, app="Internal Tool"),
        {
            "session_id": "s1", "observed_at": "2026-01-01T00:00:15Z", "app": "Internal Tool",
            "window_title": "Case", "event_type": "screen_click", "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"title": "Upload attachment", "role": "button"}},
        },
        {
            "session_id": "s1", "observed_at": "2026-01-01T00:00:25Z", "app": "Internal Tool",
            "window_title": "Case", "event_type": "screen_click", "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"title": "Confirm details", "role": "button"}},
        },
        {
            "session_id": "s1", "observed_at": "2026-01-01T00:00:40Z", "app": "Internal Tool",
            "window_title": "Case", "event_type": "screen_click", "duration_seconds": 0,
            "metadata": {"action": "click", "target": {"title": "Finish case", "role": "button"}},
        },
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks()
    assert len(out["tasks"]) == 1
    assert out["tasks"][0]["label_evidence"] == "Complete case"


def test_repeated_gmail_replies_group_despite_different_message_paths(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "Inbox - Gmail", 30, keys=80, engaged=26),
        _browser("2026-01-01T00:00:05Z", "mail.google.com", "/mail/u/0/#inbox/a", "click", "Reply", "Gmail"),
        _browser("2026-01-01T00:00:25Z", "mail.google.com", "/mail/u/0/#inbox/a", "click", "Send", "Gmail"),
        _focus("2026-01-01T00:02:00Z", "Different subject - Gmail", 35, keys=95, engaged=30),
        _browser("2026-01-01T00:02:05Z", "mail.google.com", "/mail/u/0/#inbox/b", "click", "Reply", "Gmail"),
        _browser("2026-01-01T00:02:30Z", "mail.google.com", "/mail/u/0/#inbox/b", "click", "Send", "Gmail"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=60)
    assert len(out["patterns"]) == 1
    p = out["patterns"][0]
    assert p["task_family"] == "email.reply"
    assert p["observed_count"] == 2
    assert p["suggested_label"] == "Reply to email"


def test_two_fresh_gmail_emails_group_even_if_compose_is_only_seen_once(monkeypatch):
    """Real capture can miss Compose on one execution; Send should still unify family."""
    sample = [
        _focus("2026-01-01T00:00:00Z", "New Message - Gmail", 28, keys=90, engaged=25),
        _browser("2026-01-01T00:00:02Z", "mail.google.com", "/mail/u/0/#inbox", "click", "Compose", "Gmail"),
        _browser("2026-01-01T00:00:24Z", "mail.google.com", "/mail/u/0/#inbox", "click", "Send", "Gmail"),
        _focus("2026-01-01T00:01:00Z", "Another message", 31, keys=105, engaged=29),
        # Second run deliberately lacks a Compose/New message event.
        _browser("2026-01-01T00:01:27Z", "mail.google.com", "/mail/u/0/#inbox", "click", "Send", "Gmail"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=20)
    assert len(out["patterns"]) == 1
    p = out["patterns"][0]
    assert p["task_family"] == "email.compose_send"
    assert p["observed_count"] == 2
    assert p["suggested_label"] == "Compose and send email"


def test_email_family_recovered_from_semantic_evidence_when_primary_surface_is_noisy(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "Draft subject A", 20, keys=60, engaged=18),
        _browser("2026-01-01T00:00:18Z", "mail.google.com", "/mail/u/0/", "click", "Send", "Draft subject A"),
        _focus("2026-01-01T00:01:00Z", "Draft subject B", 22, keys=70, engaged=20),
        _browser("2026-01-01T00:01:20Z", "mail.google.com", "/mail/u/0/", "click", "Send", "Draft subject B"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=20)
    assert len(out["patterns"]) == 1
    assert out["patterns"][0]["task_family"] == "email.compose_send"


def test_site_switching_alone_never_becomes_repeated_task(monkeypatch):
    sample = [
        _focus("2026-01-01T00:00:00Z", "Gmail", 10, engaged=8),
        _browser("2026-01-01T00:00:02Z", "mail.google.com", "/", "page_view", "", "Gmail"),
        _focus("2026-01-01T00:00:20Z", "GitHub", 10, engaged=8),
        _browser("2026-01-01T00:00:22Z", "github.com", "/", "page_view", "", "GitHub"),
        _focus("2026-01-01T00:00:40Z", "Gmail", 10, engaged=8),
        _browser("2026-01-01T00:00:42Z", "mail.google.com", "/", "page_view", "", "Gmail"),
        _focus("2026-01-01T00:01:00Z", "GitHub", 10, engaged=8),
        _browser("2026-01-01T00:01:02Z", "github.com", "/", "page_view", "", "GitHub"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=5)
    assert out["patterns"] == []


def test_repeated_email_family_uses_completed_actions_not_navigation(monkeypatch):
    sample = [
        _browser("2026-01-01T00:00:01Z", "mail.google.com", "/mail/u/0/", "click", "Compose", "Gmail"),
        _browser("2026-01-01T00:00:03Z", "mail.google.com", "/mail/u/0/", "focus_control", "Message body", "Gmail"),
        _browser("2026-01-01T00:00:10Z", "mail.google.com", "/mail/u/0/", "click", "Send", "Gmail"),
        _browser("2026-01-01T00:00:15Z", "github.com", "/", "page_view", "", "GitHub"),
        _browser("2026-01-01T00:00:20Z", "mail.google.com", "/mail/u/0/", "click", "Compose", "Gmail"),
        _browser("2026-01-01T00:00:22Z", "mail.google.com", "/mail/u/0/", "focus_control", "Message body", "Gmail"),
        _browser("2026-01-01T00:00:30Z", "mail.google.com", "/mail/u/0/", "click", "Send", "Gmail"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=60)
    assert len(out["patterns"]) == 1
    p = out["patterns"][0]
    assert p["task_family"] == "email.compose_send"
    assert p["observed_count"] == 2
    assert p["repeat_basis"] == "completed_task_execution"
    assert "send" in p["action_skeleton"]
