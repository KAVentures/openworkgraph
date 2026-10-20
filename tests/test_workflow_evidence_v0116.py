from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _execution(execution_id: str, session_id: str, steps: list[str], *, family: str = "family:demo") -> dict:
    return {
        "execution_id": execution_id,
        "actor_kind": "human",
        "family_key": family,
        "family_basis": "structural",
        "started_at": "2026-10-01T10:00:00Z",
        "ended_at": "2026-10-01T10:05:00Z",
        "duration_seconds": 300,
        "outcome_status": "observed_completion",
        "steps": steps,
        "observation_level": "structural",
        "evidence_refs": [],
        "_session_ids": [session_id],
    }


def _event(event_id: str, session_id: str, at: str, event_type: str, app: str, *, metadata: dict | None = None) -> dict:
    return {
        "event_id": event_id,
        "session_id": session_id,
        "observed_at": at,
        "schema_version": "1",
        "actor_id": "human",
        "device_id": "device",
        "sensor_id": "sensor",
        "source": "test",
        "app": app,
        "window_title": app,
        "event_type": event_type,
        "duration_seconds": 30,
        "metadata": metadata or {},
    }


def _install_fixture(monkeypatch: pytest.MonkeyPatch):
    from server import workflow_evidence as module

    executions = [
        _execution("execution:aaaaaaaaaaaaaaaa", "s1", ["surface:gmail", "surface:salesforce", "surface:sheets"]),
        _execution("execution:bbbbbbbbbbbbbbbb", "s2", ["surface:gmail", "surface:salesforce", "surface:sheets"]),
        _execution("execution:cccccccccccccccc", "s3", ["surface:gmail", "surface:slack", "surface:sheets"]),
    ]
    events = [
        _event(
            "e1",
            "s1",
            "2026-10-01T10:00:10Z",
            "focus_span",
            "Gmail",
            metadata={"resource_reference": {"provider": "gmail", "resource_kind": "conversation"}},
        ),
        _event(
            "e2",
            "s1",
            "2026-10-01T10:01:00Z",
            "clipboard_copy",
            "Google Sheets",
            metadata={"clipboard_transfer_id": "t1", "action": "copy"},
        ),
        _event(
            "e3",
            "s1",
            "2026-10-01T10:01:05Z",
            "clipboard_paste",
            "Salesforce",
            metadata={"clipboard_transfer_id": "t1", "action": "paste"},
        ),
        _event("e4", "s2", "2026-10-01T10:00:10Z", "focus_span", "Gmail"),
        _event("e5", "s3", "2026-10-01T10:00:10Z", "focus_span", "Gmail"),
    ]
    monkeypatch.setattr(module, "_load", lambda **_kwargs: list(events))
    monkeypatch.setattr(module, "derive_executions", lambda _raw: list(executions))
    return module


def test_explicit_run_selection_keeps_support_descriptive(monkeypatch: pytest.MonkeyPatch):
    module = _install_fixture(monkeypatch)
    bundle = module.build_workflow_evidence(
        execution_ids=(
            "execution:aaaaaaaaaaaaaaaa,execution:bbbbbbbbbbbbbbbb,"
            "execution:cccccccccccccccc"
        )
    )

    assert bundle["selector"]["selection_mode"] == "explicit_executions"
    assert bundle["selector"]["family_selection_is_ground_truth"] is False
    assert bundle["structural_alignment"]["common_path_claimed"] is False
    assert bundle["interpretation_contract"]["descriptive_not_prescriptive"] is True
    assert bundle["interpretation_contract"]["observed_behavior_is_not_policy"] is True
    assert bundle["interpretation_contract"]["observed_behavior_is_not_permission"] is True
    agent_map = bundle["agent_workflow_map"]
    assert agent_map["selected_execution_count"] == 3
    assert agent_map["observed_sequence_candidates"][0]["observed_step"] == "surface:gmail"
    assert agent_map["business_outcome_verified"] is False
    assert agent_map["execution_permissions_granted"] is False

    high = {row["step"]: row for row in bundle["structural_alignment"]["high_support_steps"]}
    uncommon = {row["step"]: row for row in bundle["structural_alignment"]["less_common_observed_steps"]}
    assert high["surface:gmail"]["support_runs"] == 3
    assert high["surface:salesforce"]["support_runs"] == 2
    assert uncommon["surface:slack"]["support_runs"] == 1
    assert "not a required order" in high["surface:gmail"]["interpretation"]
    variations = {row["step"]: row for row in bundle["structural_alignment"]["observed_variations"]}
    assert variations["surface:slack"]["support_runs"] == 1
    assert variations["surface:slack"]["absent_in_runs"] == 2
    assert len(bundle["structural_alignment"]["structural_sequence_variants"]) == 2


def test_evidence_bundle_preserves_provenance_without_inventing_payloads(monkeypatch: pytest.MonkeyPatch):
    module = _install_fixture(monkeypatch)
    bundle = module.build_workflow_evidence(execution_ids="execution:aaaaaaaaaaaaaaaa")

    assert bundle["provenance"]["source"] == "canonical_local_event_store"
    assert bundle["canonical_evidence_included"] is True
    returned = bundle["canonical_evidence"][0]
    assert returned["execution_id"] == "execution:aaaaaaaaaaaaaaaa"
    assert all(event["untrusted_observed_data"] is True for event in returned["events"])

    transfers = bundle["data_movement"]["clipboard_transfers"]
    assert len(transfers) == 1
    assert transfers[0]["clipboard_contents_observed"] is False
    assert bundle["data_movement"]["clipboard_contents_captured"] is False
    assert "clipboard values" in bundle["ai_drafting_guidance"]["rules"][5]


def test_family_selection_is_navigation_only(monkeypatch: pytest.MonkeyPatch):
    module = _install_fixture(monkeypatch)
    bundle = module.build_workflow_evidence(family_key="family:demo")
    candidates = module.list_workflow_evidence_candidates(min_runs=2)

    assert bundle["selector"]["selection_mode"] == "derived_family_candidate"
    assert bundle["selector"]["family_selection_is_ground_truth"] is False
    assert candidates["family_grouping_is_navigation_only"] is True
    assert candidates["explicit_execution_selection_supported"] is True
    assert candidates["families"][0]["family_is_ground_truth"] is False
    assert candidates["preferred_navigation"] == "candidate_clusters"
    assert candidates["coarse_families_kept_for_compatibility"] is True
    clusters = candidates["candidate_clusters"]
    assert len(clusters) == 1
    assert clusters[0]["execution_count"] == 2
    assert clusters[0]["execution_ids"] == [
        "execution:aaaaaaaaaaaaaaaa",
        "execution:bbbbbbbbbbbbbbbb",
    ]
    assert clusters[0]["cluster_is_business_workflow_ground_truth"] is False
    variants = candidates["families"][0]["structural_variants"]
    assert sorted(row["execution_count"] for row in variants) == [1, 2]


def test_skill_prompt_preserves_authority_boundary():
    from mcp_server.workflow_evidence_tools import draft_skill_prompt

    prompt = draft_skill_prompt(execution_ids="execution:aaaaaaaaaaaaaaaa")
    lowered = prompt.lower()
    assert "get_workflow_evidence" in prompt
    assert "canonical evidence" in lowered
    assert "never infer policy or permission from repetition" in lowered
    assert "never claim clipboard contents were observed" in lowered
    assert "do not write anything back to live business systems" in lowered


def test_dashboard_exports_through_authenticated_fetch_and_defaults_to_redacted():
    source = (ROOT / "dashboard" / "workflow_evidence.js").read_text(encoding="utf-8")
    assert "await window.__owgAuthReady" in source
    assert "fetch(`/v1/workflow-evidence/export?" in source
    assert "downloadEvidence(index,'redacted')" in source
    assert "Export redacted evidence" in source
    assert "confirm('Export the stored privacy-hardened representation?" in source
    assert "refreshWorkflowEvidence" in source
    assert "setInterval" not in source
    assert "window.location" not in source


def test_history_guard_limits_workflow_evidence_to_current_run_when_history_access_is_off():
    from mcp_server.history_guard import install_history_guard

    calls: list[tuple[str, dict | None]] = []

    def secure_get(path: str, params: dict | None = None):
        if path == "/v1/history/ai-access":
            return {"access": {"mode": "off"}}
        if path == "/v1/capture/status":
            return {"run_started_at": "2026-10-01T12:00:00+00:00"}
        calls.append((path, dict(params or {})))
        return {"ok": True}

    runtime = SimpleNamespace(secure_get=secure_get)
    install_history_guard(runtime)
    runtime.secure_get(
        "/v1/workflow-evidence",
        {"execution_ids": "execution:aaaaaaaaaaaaaaaa", "since": "2026-09-01T00:00:00+00:00"},
    )

    assert calls == [
        (
            "/v1/workflow-evidence",
            {"execution_ids": "execution:aaaaaaaaaaaaaaaa", "since": "2026-10-01T12:00:00+00:00"},
        )
    ]


def test_history_guard_clamps_family_discovery_to_explicit_saved_history_range():
    from mcp_server.history_guard import install_history_guard

    calls: list[tuple[str, dict | None]] = []

    def secure_get(path: str, params: dict | None = None):
        if path == "/v1/history/ai-access":
            return {
                "access": {
                    "mode": "selected_range",
                    "since": "2026-09-28T00:00:00+00:00",
                    "until": "2026-09-30T23:59:59+00:00",
                }
            }
        calls.append((path, dict(params or {})))
        return {"ok": True}

    runtime = SimpleNamespace(secure_get=secure_get)
    install_history_guard(runtime)
    runtime.secure_get(
        "/v1/workflow-evidence/families",
        {"since": "2026-09-01T00:00:00+00:00", "until": "2026-10-01T23:59:59+00:00", "min_runs": 2},
    )

    assert calls == [
        (
            "/v1/workflow-evidence/families",
            {
                "since": "2026-09-28T00:00:00+00:00",
                "until": "2026-09-30T23:59:59+00:00",
                "min_runs": 2,
            },
        )
    ]


def test_transfer_destination_rollup_keeps_source_breakdown_without_hiding_exact_pairs():
    from server.workflow_evidence import _clipboard_by_destination

    exact = [
        {
            "source_surface": "Microsoft Excel",
            "destination_surface": "Internal Pricing Tool",
            "observed_transfer_count": 2,
            "supporting_execution_ids": ["execution:aaaaaaaaaaaaaaaa", "execution:bbbbbbbbbbbbbbbb"],
        },
        {
            "source_surface": "Adobe Acrobat",
            "destination_surface": "Internal Pricing Tool",
            "observed_transfer_count": 2,
            "supporting_execution_ids": ["execution:cccccccccccccccc", "execution:dddddddddddddddd"],
        },
    ]
    rows = _clipboard_by_destination(exact)
    assert len(rows) == 1
    assert rows[0]["destination_surface"] == "Internal Pricing Tool"
    assert rows[0]["observed_transfer_count"] == 4
    assert rows[0]["support_runs"] == 4
    assert rows[0]["clipboard_contents_observed"] is False
    assert rows[0]["source_breakdown"] == [
        {
            "source_surface": "Adobe Acrobat",
            "observed_transfer_count": 2,
            "supporting_execution_ids": ["execution:cccccccccccccccc", "execution:dddddddddddddddd"],
        },
        {
            "source_surface": "Microsoft Excel",
            "observed_transfer_count": 2,
            "supporting_execution_ids": ["execution:aaaaaaaaaaaaaaaa", "execution:bbbbbbbbbbbbbbbb"],
        },
    ]


def test_candidate_projection_reuses_central_readable_agent_steps(monkeypatch):
    from server import agent_execution_traces, procedural_feedback, workflow_evidence

    monkeypatch.setattr(procedural_feedback, "_human_runs", lambda _raw: [])
    monkeypatch.setattr(
        agent_execution_traces,
        "agent_execution_traces",
        lambda *_args, **_kwargs: {
            "executions": [{
                "execution_id": "execution:aaaaaaaaaaaaaaaa",
                "structural_steps": ["Read", "Grep", "Edit", "Bash"],
            }]
        },
    )
    projected = workflow_evidence._candidate_runs(
        [],
        [{
            "execution_id": "execution:aaaaaaaaaaaaaaaa",
            "actor_kind": "agent",
            "steps": [
                "tool:filesystem:tool:4914deadbeef",
                "tool:search:tool:1111deadbeef",
            ],
        }],
    )
    assert projected[0]["semantic_steps"] == ["Read", "Grep", "Edit", "Bash"]
    assert projected[0]["structural_steps"][0].startswith("tool:filesystem:")


def test_gateway_discovery_requires_multi_surface_and_positive_duration(monkeypatch):
    from gateway import workflow_evidence as gw

    seen = {}
    def fake_candidates(**kwargs):
        seen.update(kwargs)
        return {"candidate_clusters": [], "families": [], "returned": 0, "total": 0}
    monkeypatch.setattr(gw, "load_gateway_evidence", lambda *_a, **_k: [])
    monkeypatch.setattr(gw, "list_workflow_evidence_candidates", fake_candidates)
    gw.repeated_workflows(object(), organization_id="org", actor_id="actor")
    assert seen["require_multi_surface"] is True
    assert seen["require_nonzero_duration"] is True
    assert seen["rank_by_quality"] is True


def test_filter_and_counter_consistency_for_candidate_clusters(monkeypatch):
    from server import workflow_evidence as module

    rows = [
        {
            "execution_id": "execution:" + "a" * 16,
            "family_key": "family:demo",
            "actor_kind": "human",
            "duration_seconds": 180,
            "steps": ["surface:gmail", "surface:github"],
        },
        {
            "execution_id": "execution:" + "b" * 16,
            "family_key": "family:demo",
            "actor_kind": "human",
            "duration_seconds": 200,
            "steps": ["surface:gmail", "surface:github"],
        },
    ]
    monkeypatch.setattr(module, "derive_executions", lambda *_a, **_k: rows)
    monkeypatch.setattr(module, "_candidate_runs", lambda *_a, **_k: [
        {**row, "structural_steps": row["steps"], "semantic_steps": row["steps"],
         "surface_labels": {"surface:gmail": "Gmail", "surface:github": "GitHub"}}
        for row in rows
    ])

    def candidate(cluster_id, steps, duration):
        return {
            "candidate_cluster_id": cluster_id, "cluster_quality": 0.6,
            "execution_count": 2, "execution_ids": [x["execution_id"] for x in rows],
            "core_structural_steps": steps,
            "median_duration_seconds": duration,
        }

    monkeypatch.setattr(module, "cluster_runs", lambda *_a, **_k: [
        candidate("cluster:multi", ["surface:gmail", "surface:github"], 190),
        candidate("cluster:zero", ["surface:gmail", "surface:github"], 0),
        candidate("cluster:single", ["surface:gmail"], 190),
    ])
    result = module.list_workflow_evidence_candidates(
        _raw_events=[], require_multi_surface=True,
        require_nonzero_duration=True,
    )
    assert result["returned"] == 1
    assert result["total"] == 1
    assert result["candidate_clusters"][0]["candidate_cluster_id"] == "cluster:multi"
    assert result["suppressed_zero_or_single_surface_clusters"] == 2
    assert result["families_total"] == len(result["families"])
    assert result["families"][0]["surface_labels"]["surface:gmail"] == "Gmail"
    assert result["families"][0]["high_support_display_steps"][0] in {"Gmail", "GitHub"}
