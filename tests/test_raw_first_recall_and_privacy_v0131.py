"""Regression tests for raw-first recall, field-safe redaction and hosted ranking.

All identities, domains and events in this test are synthetic.
"""
from datetime import datetime, timedelta, timezone

from gateway.auth import Principal
from gateway.db import GatewayDB
from gateway.goal_context import gateway_goal_context
from gateway.plugin_redaction import PublicRedactor, project
from gateway.public_plugin_mcp import _compact_response
from server.workflow_candidates import cluster_runs
from server.workflow_evidence import _candidate_runs


def test_unknown_people_and_companies_do_not_leak_through_target_or_title():
    redactor = PublicRedactor(secret="synthetic-test-secret", principal="test-actor",
                              owner_names=["Uniqueowner"])
    assert redactor.target_label("Open quote") == "Open quote"
    assert redactor.target_label("Michaela") != "Michaela"
    assert redactor.target_label("Medlink Nordic AB").startswith(("TARGET_", "ORG_"))
    assert redactor.text("Medlink Nordic AB").startswith("ORG_")
    assert redactor.text("OWNER") == redactor.owner_token
    assert redactor.target_label("Chrome – Uniqueowner", token_kind="TITLE") == (
        "Chrome – " + redactor.owner_token
    )
    # Swedish UI and file/PR references must remain navigable, never opaque.
    for caption in ("Hämta igen", "Ny", "Adress- och sökfält",
                    "3. Rapportering", "Continue connecting",
                    "OpenWorkGraph", "GitHub", "Oneflow",
                    "Pull requests · KAVentures/openworkgraph",
                    "Sync v31 … Pull Request #1"):
        assert redactor.target_label(caption) == caption
    raw = {"rows": [{
        "event_id": "synthetic-1", "observed_at": "2026-10-13T10:00:00Z",
        "event_type": "browser_click", "app": "Chrome",
        "window_title": "Confidential work for Michaela at Medlink Nordic AB",
        "target_label": "Review confidential terms for Michaela – Medlink Nordic AB",
        "metadata": {"target": {"role": "button",
                                "label": "Michaela at Medlink Nordic AB",
                                "title": "Private Michaela message"}},
    }]}
    for detail in ("compact", "rich"):
        safe = project(raw, secret="synthetic-test-secret", principal="test-actor",
                       detail=detail, owner_names=["Uniqueowner"])
        row = safe["rows"][0]
        assert row["event_id"] == "synthetic-1"
        assert "PERSON_" in row["target_label"]
        assert "ORG_" in row["target_label"]
        assert row["window_title"].startswith("Confidential work for ")
        assert "ORG_" in row["window_title"]
        assert "Michaela" not in str(safe)
        assert "Medlink Nordic" not in str(safe)
        if detail == "rich":
            assert "ORG_" in row["metadata"]["target"]["label"]
            assert "PERSON_" in row["metadata"]["target"]["title"]


def test_browser_surface_never_inherits_visited_claude_label():
    events = [
        {
            "event_id": "chrome-event", "observed_at": "2026-10-13T10:00:00Z",
            "app": "Google Chrome", "event_type": "browser_click",
            "metadata": {"page": {"hostname": "claude.ai"}},
        },
        {
            "event_id": "claude-event", "observed_at": "2026-10-13T10:01:00Z",
            "app": "Claude", "event_type": "focus_span",
        },
    ]
    projected = _candidate_runs(events, [{
        "execution_id": "execution:aaaaaaaaaaaaaaaa", "actor_kind": "human",
        "steps": ["surface:chrome", "surface:claude", "surface:deadbeef0011"],
    }])
    labels = projected[0]["surface_labels"]
    assert labels["surface:chrome"] == "Browser"
    assert labels["surface:claude"] == "Claude"
    assert labels["surface:deadbeef0011"] == "Other surface (redacted)"


def test_quality_is_ranking_not_discarding_raw_work():
    def run(identifier, steps):
        return {
            "execution_id": f"execution:{identifier:016x}",
            "actor_kind": "human",
            "structural_steps": steps,
        }
    weak = [
        run(1, ["surface:chrome", "action:click"]),
        run(2, ["surface:chrome", "action:click"]),
    ]
    strong = [
        run(3, ["surface:github", "action:copy", "surface:google-sheets", "action:paste"]),
        run(4, ["surface:github", "action:copy", "surface:google-sheets", "action:paste"]),
    ]
    clusters = cluster_runs(weak + strong)
    assert len(clusters) == 2
    assert any(row["cluster_quality"] < 0.45 for row in clusters)
    assert any(row["cluster_quality"] > 0.45 for row in clusters)
    assert len(cluster_runs(weak + strong, min_cluster_quality=0.45)) == 1


def test_compact_preserves_raw_cursor_and_security_diagnostics():
    base = {
        "rows": [{"event_id": "e1", "window_title": "TITLE_123456789A"}],
        "has_more": True, "next_cursor": "cursor",
        "_openworkgraph_security": {
            "trust": "untrusted_observed_data",
            "handling": "Long standard instructions repeated on every tool",
            "instruction_like_fields_suppressed": 1,
            "suppressed_field_paths": ["rows[0].window_title"],
        },
        "raw_evidence_fallback": {
            "tool": "get_workflow_trace", "no_inferred_task_required": True,
            "reason": "Long generic boilerplate",
        },
        "navigation_hints": [{"reason": "more boilerplate"}],
    }
    compact = _compact_response(base)
    assert compact["rows"] == base["rows"]
    assert compact["next_cursor"] == "cursor"
    assert compact["has_more"] is True
    assert compact["_openworkgraph_security"]["instruction_like_fields_suppressed"] == 1
    assert compact["_openworkgraph_security"]["handling"] == base["_openworkgraph_security"]["handling"]
    assert compact["raw_evidence_fallback"]["no_inferred_task_required"] is True
    assert "navigation_hints" not in compact
    assert len(str(compact)) < len(str(base))


def test_older_work_survives_tail_cap_and_more_than_180_lexical_hits(tmp_path):
    db = GatewayDB("sqlite:///" + str(tmp_path / "recall.db"))
    db.init()
    now = datetime.now(timezone.utc)
    org = "oauth-sub:owner-test"
    principal = Principal("device", "device", org, org, "d1", frozenset())
    other = Principal("device", "device", org, "other-user", "d2", frozenset())

    base = now - timedelta(days=5)
    old = [
        {
            "event_id": f"old-quote-{i:03d}",
            "observed_at": (base + timedelta(seconds=i)).isoformat(),
            "source": "browser", "event_type": "browser_click",
            "app": "Chrome", "window_title":
                "Quotation milestone" if i == 249 else "Quotation draft",
        } for i in range(250)
    ]
    recent = [
        {
            "event_id": f"recent-noise-{i:03d}",
            "observed_at": (now - timedelta(minutes=i)).isoformat(),
            "source": "browser", "event_type": "browser_click",
            "app": "Chrome", "window_title": "Generic browser",
        } for i in range(510)
    ]
    db.insert_events(principal, old + recent)
    db.insert_events(other, [{
        "event_id": "foreign-private", "observed_at": now.isoformat(),
        "source": "browser", "event_type": "browser_click",
        "app": "Chrome", "window_title": "Quotation milestone secret other user",
    }])
    result = gateway_goal_context(
        db, organization_id=org, actor_id=org,
        goal="Find quotation milestone",
        max_examples=4,
    )
    assert result["coverage"]["recent_tail_capped"] is True
    assert result["coverage"]["lexical_rows_scanned"] >= 200
    assert result["coverage"]["raw_sample_is_query_free"] is True
    assert len(result["recent_raw_evidence"]) == 10
    assert result["raw_evidence_is_independent_of_matches"] is True
    assert "foreign-private" not in str(result)
    assert result["coverage"]["complete_work_history"] is False


def test_raw_first_benchmark_never_loses_raw_only_recall():
    from evals.raw_first_recall import run_benchmark

    report = run_benchmark()
    assert report["evaluation_is_synthetic"] is True
    assert report["raw_fallback_executed_by_test_policy"] is True
    assert report["raw_only_recall"] == 1.0
    assert report["combined_recall"] >= report["raw_only_recall"]
    assert report["inference_only_recall"] < report["raw_only_recall"]


def test_readable_steps_and_unknown_search_queries_are_not_plaintext():
    from gateway.plugin_redaction import project

    raw = {
        "query": "Zorina",
        "candidate_clusters": [{
            "readable_steps": [
                "Gmail · Open ticket for Zorina at Sample Nordic AB",
                "Gmail · Open ticket",
            ],
            "structural_steps": ["surface:chrome", "action:click"],
        }],
    }
    safe = project(raw, secret="test-secret", principal="owner-a", detail="rich")
    assert safe["query"] != "Zorina"
    result = safe["candidate_clusters"][0]
    assert result["readable_steps"][0].startswith("Gmail · Open ticket for ")
    assert "PERSON_" in result["readable_steps"][0]
    assert "ORG_" in result["readable_steps"][0]
    assert result["readable_steps"][1] == "Gmail · Open ticket"
    assert result["structural_steps"] == ["surface:chrome", "action:click"]
    assert "Zorina" not in str(safe)
    assert "Sample Nordic" not in str(safe)


def test_idle_boundaries_keep_quick_cross_app_work_but_split_unrelated_sessions():
    from server.procedural_memory import split_human_execution_windows

    t0 = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
    def event(seconds, app):
        return {
            "observed_at": (t0 + timedelta(seconds=seconds)).isoformat(),
            "event_id": f"e-{seconds}", "event_type": "focus_span",
            "app": app, "session_id": "same-session",
        }
    # Continuous real work can move Gmail -> Sheets -> Salesforce.
    events = [event(0, "Gmail"), event(15, "Google Sheets"), event(30, "Salesforce")]
    task = {
        "task_id": "inferred", "session_id": "same-session",
        "started_at": events[0]["observed_at"],
        "ended_at": (t0 + timedelta(seconds=210)).isoformat(),
    }
    assert split_human_execution_windows(task, events, mode="idle") == [task]
    # Following an actual quiet interval, a Oneflow contract is a new
    # candidate even though the desktop session is unchanged.
    events.append(event(210, "Oneflow"))
    split = split_human_execution_windows(task, events, mode="idle")
    assert len(split) == 2
    assert split[0]["idle_gap_partitioned"] is True
    assert split[1]["started_at"] == events[-1]["observed_at"]
    assert not any(row.get("host_switch_partitioned") for row in split)
    assert len(events) == 4


def test_medlink_email_preview_redacts_swedish_self_introduction_and_signature():
    from gateway.plugin_redaction import project
    redactor = PublicRedactor(secret="synthetic-test-secret", principal="actor-medlink")

    # Exercise the same linguistic forms seen in the report, across compact,
    # rich, nested UI fields and free-text redaction.
    preview = (
        "Medlink Nordic AB – Mitt namn är Michaela och jag jobbar på Medlink. "
        "Vi hjälper vårdgivare att effektivisera arbetet. "
        "Hälsningar, Michaela"
    )
    for detail in ("compact", "rich"):
        projected = project({"rows": [{
            "event_id": "medlink-preview",
            "observed_at": "2026-10-13T08:00:00Z",
            "event_type": "browser_click",
            "app": "Chrome", "window_title": "Gmail · Inbox",
            "target_label": preview,
            "metadata": {"target": {
                "role": "button", "label": preview, "title": preview,
            }},
        }]}, secret="synthetic-test-secret", principal="actor-medlink",
            detail=detail)
        serialized = str(projected)
        assert "Michaela" not in serialized
        assert "Medlink Nordic AB" not in serialized
        assert "Medlink" not in serialized
        assert "Mitt namn är PERSON_" in serialized
        assert "Hälsningar, PERSON_" in serialized
        assert "Vi hjälper vårdgivare" in serialized
        assert "jag jobbar på ORG_" in serialized

    # A standalone organization in a job/employer sentence also gets masked.
    assert "Medlink" not in redactor.text("Jag jobbar på Medlink idag")
    # Previously inferred firm prefixes are masked across later labels in
    # the same authorized redactor request (without a global denylist).
    redactor.text("Medlink Nordic AB")
    assert redactor.text("Kontakta Medlink imorgon") == (
        "Kontakta " + redactor._token("ORG", "Medlink") + " imorgon"
    )


def test_name_cues_preserve_swedish_non_names_and_verbs():
    redactor = PublicRedactor(secret="synthetic-test-secret", principal="actor-test")
    assert redactor.target_label("Välkommen till oss på mottagningen") == (
        "Välkommen till oss på mottagningen"
    )
    ring = redactor.target_label("Ring Erik imorgon")
    assert ring.startswith("Ring ")
    assert "Erik" not in ring
    assert redactor.target_label("Hälsningar, Michaela").startswith(
        "Hälsningar, PERSON_"
    )
    assert redactor.target_label("Mitt namn är Michaela och jag jobbar").startswith(
        "Mitt namn är PERSON_"
    )
    assert redactor.target_label("/ Michaela").endswith(
        redactor._token("PERSON", "Michaela")
    )
    for caption in ("Hämta igen", "Adress- och sökfält",
                    "Sync v31 … Pull Request #1",
                    "Pull requests · KAVentures/openworkgraph"):
        assert redactor.target_label(caption) == caption
