"""Commercial-evaluation regressions: distinguish evidence from inferred activity."""
from __future__ import annotations

from server.work_profile import (
    _canonical_surface,
    _clipboard_observation_summary,
    _hunting_candidates,
    _transfer_patterns,
)


def _row(n: int, *, session: str = "s1", resource: str = "sheets.test/rows",
         action: str = "click", surface: str = "Internal Sheets",
         metadata: dict | None = None) -> dict:
    return {
        "event_id": f"e{n}", "session_id": session,
        "observed_at": f"2026-10-15T10:00:{n % 60:02d}Z",
        "surface": surface, "resource_locator": resource,
        "action": action, "metadata": metadata or {},
    }


def test_generic_sheets_surface_is_not_misidentified_as_google():
    assert _canonical_surface("Internal Sheets") == "Internal Sheets"
    assert _canonical_surface("Sheets") == "Sheets"
    assert _canonical_surface("Google Sheets") == "Google Sheets"
    assert _canonical_surface("docs.google.com/spreadsheets/d/abc") == "Google Sheets"


def test_hunting_counts_distinct_entries_not_individual_events():
    rows = [_row(n) for n in range(200)]
    # Returning after opening a different resource is another entry.
    rows.extend([_row(201, resource="mail.test/inbox"),
                 _row(202, action="click")])
    candidates = _hunting_candidates(rows)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["surface"] == "Internal Sheets"
    assert candidate["visit_count"] == 2
    assert candidate["observed_event_count"] == 201
    assert candidate["visit_count_basis"] == "observed_resource_entry_episodes_lower_bound"
    # An uninterrupted page with 200 events isn't 200 visits.
    assert _hunting_candidates(rows[:200]) == []


def test_hunting_does_not_link_sessions_or_browser_tabs():
    rows = [
        _row(1, session="s1", metadata={"tab_context_id": "tab-a"}),
        _row(2, session="s1", metadata={"tab_context_id": "tab-a"}),
        _row(3, session="s1", metadata={"tab_context_id": "tab-b"}),
    ]
    assert _hunting_candidates(rows)[0]["visit_count"] == 2


def test_prefixed_clipboard_actions_and_unmatched_paste_coverage():
    rows = [
        _row(1, action="clipboard_copy", metadata={"clipboard_transfer_id": "t1"}),
        _row(2, action="clipboard_paste", surface="CRM", metadata={"clipboard_transfer_id": "t1"}),
        _row(3, action="browser_paste", surface="CRM"),
        _row(4, action="clipboard_copy"),
    ]
    transfers = _transfer_patterns(rows)
    assert len(transfers) == 1
    assert transfers[0]["count"] == 1
    assert transfers[0]["cross_surface"] is True
    summary = _clipboard_observation_summary(rows)
    assert summary["copy_events_observed"] == 2
    assert summary["paste_events_observed"] == 2
    assert summary["pastes_with_transfer_id"] == 1
    assert summary["pastes_without_transfer_id"] == 1
    assert summary["zero_does_not_prove_no_real_world_copy_paste"] is True
    assert summary["clipboard_contents_captured"] is False


def test_no_clipboard_events_means_not_observed_not_no_real_world_transfers():
    summary = _clipboard_observation_summary([_row(1)])
    assert summary["paste_events_observed"] == 0
    assert summary["manual_transfer_count_is_observed_lower_bound"] is True
    assert summary["scope"] == "recorded_context_events_only"
