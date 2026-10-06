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
