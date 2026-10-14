"""Three real-looking invoicing runs must win despite passive app noise.

Do not replace the quality gate with another silent cut: low-scoring work
remains available, ranked using observed facts and labelled navigation only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server import workflow_evidence as evidence
from server.workflow_candidates import cluster_runs, _specificity
from shared.core.identity_cues import redact_explicit_person_cues
from shared.core.browser_title_privacy import protect_text


def _run(i: int, day: int, apps: list[str], *, duration: float = 600,
         noise: bool = False) -> dict:
    at = datetime(2026, 10, 8 + day, 8, tzinfo=timezone.utc)
    structural = [f"surface:{app}" for app in apps]
    return {
        "execution_id": f"execution:{i:016x}",
        "actor_kind": "human", "family_key": "noise" if noise else "invoice",
        "started_at": at.isoformat(),
        "ended_at": (at + timedelta(seconds=duration)).isoformat(),
        "duration_seconds": duration,
        "steps": list(structural), "structural_steps": list(structural),
        "semantic_steps": [f"{app.title()} · Work" for app in apps],
        "_session_ids": [f"session-{i}"],
        "cross_surface_clipboard_observed": not noise,
    }


def _invoice_runs() -> list[dict]:
    return [_run(i, i, ["gmail", "excel", "fortnox", "gmail"])
            for i in range(3)]


def _noise_runs() -> list[dict]:
    return (
        [_run(100 + i, i % 3, ["finder"], duration=9000, noise=True)
         for i in range(9)]
        + [_run(200 + i, i % 3, ["browser", "finder"], duration=8000,
                noise=True) for i in range(5)]
    )


@pytest.mark.parametrize("with_noise", [False, True])
def test_invoicing_three_runs_first_rank_clean_or_noisy(with_noise):
    runs = _invoice_runs() + (_noise_runs() if with_noise else [])
    assert _specificity(["surface:gmail", "surface:excel", "surface:fortnox"]) == .3
    result = cluster_runs(runs, min_runs=2, limit=100,
                          min_cluster_quality=0.0, rank_by_navigation=True)
    assert result
    first = result[0]
    assert first["execution_count"] == 3
    assert first["core_structural_steps"] == [
        "surface:gmail", "surface:excel", "surface:fortnox", "surface:gmail",
    ]
    assert first["navigation_rank_evidence"]["distinct_ordered_apps"] == 3
    assert first["navigation_rank_evidence"]["observed_cross_app_transitions"] == 3
    assert first["navigation_rank_evidence"]["observed_active_days"] == 3
    assert first["navigation_rank_evidence"]["runs_with_linked_cross_app_clipboard"] == 3
    assert first["navigation_rank_is_task_confidence"] is False
    assert first["cluster_quality"] == .3
    assert {r["execution_id"] for r in _invoice_runs()} == set(first["execution_ids"])
    if with_noise:
        assert len(result) >= 2
        assert result[0]["navigation_rank_score"] > result[1]["navigation_rank_score"]


@pytest.mark.parametrize("with_noise", [False, True])
def test_api_navigation_has_rank_one_invoice_and_derives_tasks_once(
    monkeypatch, with_noise
):
    runs = _invoice_runs() + (_noise_runs() if with_noise else [])
    task_calls = []
    from server import context_layers

    def tasks(*args, **kwargs):
        task_calls.append((args, kwargs))
        return {"tasks": [], "patterns": []}

    def executions(_raw, **kw):
        assert kw["_task_data"] is task_data
        return runs

    def projected(_raw, _executions, **kw):
        assert kw["_task_data"] is task_data
        return [dict(item) for item in runs]

    task_data = {"tasks": [], "patterns": []}
    def shared_tasks(*a, **kw):
        task_calls.append(1)
        return task_data

    monkeypatch.setattr(context_layers, "candidate_tasks", shared_tasks)
    monkeypatch.setattr(evidence, "derive_executions", executions)
    monkeypatch.setattr(evidence, "_candidate_runs", projected)

    # Copy and paste share the SAME transfer id only within observed run 0.
    raw = [
        {
            "event_id": "copy", "observed_at": "2026-10-08T08:02:00+00:00",
            "source": "desktop", "app": "Gmail", "session_id": "session-0",
            "event_type": "clipboard_copy",
            "metadata": {"clipboard_transfer_id": "local-transfer-1", "action": "copy"},
        },
        {
            "event_id": "paste", "observed_at": "2026-10-08T08:03:00+00:00",
            "source": "desktop", "app": "Excel", "session_id": "session-0",
            "event_type": "clipboard_paste",
            "metadata": {"clipboard_transfer_id": "local-transfer-1", "action": "paste"},
        },
    ]
    result = evidence.list_workflow_evidence_candidates(
        _raw_events=raw, min_runs=2, limit=8,
        rank_by_navigation=True, navigation_summary_only=True,
    )
    assert len(task_calls) == 1
    assert result["candidate_clusters"]
    top = result["candidate_clusters"][0]
    assert top["execution_count"] == 3, result
    assert "surface:fortnox" in top["core_structural_steps"]
    assert top["navigation_rank_evidence"]["runs_with_linked_cross_app_clipboard"] == 1
    assert result["ranking_filters_out_low_specificity"] is False
    assert result["candidate_ranking"] == "ordered_multi_app_days_duration_copy_paste"
    assert result["families"]  # compact family index still present


def test_window_index_matches_legacy_inclusive_event_filter():
    from server import procedural_memory as memory
    base = datetime(2026, 10, 8, tzinfo=timezone.utc)
    events = [
        {"observed_at": (base + timedelta(seconds=i)).isoformat(),
         "app": "Gmail" if i % 2 else "Excel", "event_type": "focus_span"}
        for i in range(12_000)
    ]
    task = {
        "started_at": (base + timedelta(seconds=5_000)).isoformat(),
        "ended_at": (base + timedelta(seconds=5_060)).isoformat(),
    }
    indexes = memory._window_time_index(events)
    window = memory._task_window_events(task, events, indexes)
    assert window == events[5000:5061]
    assert memory._human_steps(task, window) == memory._human_steps(task, events)


def test_remaining_swedish_person_cues_and_local_at_rest():
    examples = (
        ("Fredrik har lagt till uppgiften", "Fredrik"),
        ("Malin är frånvarande", "Malin"),
        ("Hälsa Björn", "Björn"),
    )
    for sentence, name in examples:
        safe = redact_explicit_person_cues(sentence, person_token=lambda _: "PERSON_PRIVATE")
        assert name not in safe, safe
        assert name not in protect_text(sentence), sentence


def test_company_and_common_titles_are_not_classified_as_person_cues():
    for title in (
        "Medlink har lagt till avtal", "Fortnox är frånvarande",
        "Gmail · Fakturor", "Hej alla", "Invoice 4471 - Fortnox",
    ):
        # Callers pass the existing known-org/never-redact vocabulary.
        result = redact_explicit_person_cues(
            title, person_token=lambda _: "PERSON_PRIVATE",
            blocked_names={"medlink", "fortnox", "gmail"},
        )
        assert result == title
