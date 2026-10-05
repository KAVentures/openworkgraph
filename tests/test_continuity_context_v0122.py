from __future__ import annotations


def _resource_event(event_id, when, ref, title, *, tab=""):
    return {
        "event_id": event_id,
        "observed_at": when,
        "app": "Browser",
        "window_title": title,
        "event_type": "focus_span",
        "duration_seconds": 30,
        "tab_context_id": tab,
        "metadata": {
            "resource_reference": {
                "provider": "salesforce" if "crm" in ref else "gmail",
                "resource_kind": "record" if "crm" in ref else "thread_locator",
                "host": "example.invalid",
                "resource_ref": ref,
                "resolution": "observed",
            }
        },
    }


def test_compact_pointer_projection_excludes_file_path_and_hash():
    from mcp_server.continuity import extract_resource_pointers

    row = {
        "metadata": {
            "file_reference": {
                "file_ref": "owg:f:0123456789abcdef01234567",
                "path": "/Users/alice/Secret/Acme.xlsx",
                "sha256": "f" * 64,
                "size": 1234,
            }
        }
    }
    assert extract_resource_pointers(row) == [{
        "provider": "local_file",
        "resource_kind": "file",
        "resource_ref": "owg:f:0123456789abcdef01234567",
        "resolution": "observed",
    }]


def test_compact_trace_keeps_stable_resource_pointer():
    from mcp_server.compact_hardening import _compact_trace_row

    row = _resource_event(
        "e1", "2026-10-04T10:00:00+00:00",
        "owg:r:0123456789abcdef0123456789abcdef",
        "Acme renewal - Gmail",
    )
    compact = _compact_trace_row(row)
    assert compact["resource_pointers"][0]["resource_ref"].startswith("owg:r:")
    assert "metadata" not in compact


def test_shared_context_can_group_resources_but_time_alone_cannot():
    from mcp_server.continuity import build_continuity_context

    acme_mail = "owg:r:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    acme_crm = "owg:r:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    globex = "owg:r:cccccccccccccccccccccccccccccccc"
    events = [
        _resource_event("e1", "2026-10-04T10:00:00+00:00", acme_mail, "Acme renewal - Gmail"),
        _resource_event("e2", "2026-10-04T10:04:00+00:00", acme_crm, "Acme renewal - CRM"),
        _resource_event("e3", "2026-10-04T10:05:00+00:00", globex, "Globex pricing"),
    ]
    context = build_continuity_context(events, [{
        "execution_id": "execution:agent-1",
        "started_at": "2026-10-04T10:03:00+00:00",
        "ended_at": "2026-10-04T10:06:00+00:00",
        "agent": {"framework": "claude-code"},
        "outcome_status": "success",
    }])

    multi = next(c for c in context["candidates"] if len(c["resource_refs"]) == 2)
    assert set(multi["resource_refs"]) == {acme_mail, acme_crm}
    assert multi["task_label"] is None
    assert multi["task_identity_inferred"] is False
    assert globex not in multi["resource_refs"]

    temporal = [
        r for r in context["resource_relations"]
        if globex in {r["left_resource_ref"], r["right_resource_ref"]}
    ]
    assert temporal and all(r["relation"] == "temporal_proximity" for r in temporal)
    assert all(r["proves_same_work"] is False for r in temporal)
    assert context["unfinished_state"]["status"] == "unknown"


def test_same_tab_context_is_explicit_but_does_not_merge_by_itself():
    from mcp_server.continuity import build_continuity_context

    left = "owg:r:11111111111111111111111111111111"
    right = "owg:r:22222222222222222222222222222222"
    context = build_continuity_context([
        _resource_event("e1", "2026-10-04T10:00:00+00:00", left, "Case A", tab="tab-x"),
        _resource_event("e2", "2026-10-04T10:02:00+00:00", right, "Case B", tab="tab-x"),
    ])
    relation = context["resource_relations"][0]
    assert relation["relation"] == "same_tab_context"
    assert relation["confidence"] == 0.50
    assert relation["association_status"] == "ambiguous"
    assert relation["proves_same_work"] is False
    assert all(len(candidate["resource_refs"]) == 1 for candidate in context["candidates"])


def test_generic_work_words_do_not_bind_unrelated_resources():
    from mcp_server.continuity import build_continuity_context

    left = "owg:r:33333333333333333333333333333333"
    right = "owg:r:44444444444444444444444444444444"
    context = build_continuity_context([
        _resource_event("e1", "2026-10-04T10:00:00+00:00", left, "Acme renewal"),
        _resource_event("e2", "2026-10-04T10:01:00+00:00", right, "Globex renewal"),
    ])
    assert all(len(candidate["resource_refs"]) == 1 for candidate in context["candidates"])
    assert context["resource_relations"][0]["relation"] == "temporal_proximity"


def test_candidate_grouping_never_transitively_chains_resources():
    from mcp_server.continuity import _candidate_sets

    resources = [
        {"resource_ref": "a"},
        {"resource_ref": "b"},
        {"resource_ref": "c"},
    ]
    relations = [
        {"left_resource_ref": "a", "right_resource_ref": "b", "confidence": 0.75, "association_status": "possible", "_merge_candidate": True},
        {"left_resource_ref": "b", "right_resource_ref": "c", "confidence": 0.75, "association_status": "possible", "_merge_candidate": True},
    ]
    candidates = _candidate_sets(resources, relations, [])
    assert {tuple(candidate["resource_refs"]) for candidate in candidates} == {("a", "b"), ("b", "c")}
    assert all(len(candidate["resource_refs"]) <= 2 for candidate in candidates)


def test_empty_continuity_context_does_not_claim_absence():
    from mcp_server.continuity import build_continuity_context

    context = build_continuity_context([])
    assert context["resources"] == []
    assert context["coverage"]["distinct_stable_resources"] == 0
    assert context["association_contract"]["same_task_identity_is_observed"] is False
    assert context["unfinished_state"]["status"] == "unknown"


def test_resource_agent_overlap_relation_is_non_authoritative():
    from mcp_server.continuity import build_continuity_context

    ref = "owg:r:55555555555555555555555555555555"
    context = build_continuity_context(
        [_resource_event("e1", "2026-10-04T10:00:00+00:00", ref, "Acme case")],
        [{
            "execution_id": "execution:agent-before",
            "started_at": "2026-10-04T09:59:00+00:00",
            "ended_at": "2026-10-04T10:00:10+00:00",
            "agent": {"framework": "codex"},
        }],
    )
    relation = context["resource_agent_relations"][0]
    assert relation["relation"] == "temporal_overlap"
    assert relation["proves_same_work"] is False


def test_redacted_ai_context_preserves_opaque_refs_but_redacts_text(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_OBSERVER_CONFIG", str(tmp_path / "config.json"))

    from server.ai_context import redact_contextually

    resource_ref = "owg:r:0123456789abcdef0123456789abcdef"
    file_ref = "owg:f:0123456789abcdef01234567"
    result = redact_contextually({
        "resource_ref": resource_ref,
        "file_ref": file_ref,
        "window_title": "Contract for Anna Svensson - Gmail",
        "resolver_locator": "customer/Anna-Svensson/record",
    })
    assert result["resource_ref"] == resource_ref
    assert result["file_ref"] == file_ref
    assert "Anna Svensson" not in result["window_title"]
    # A resolver locator is not an opaque protected ref and remains subject to
    # contextual/structured redaction.
    assert result["resolver_locator"] != ""
