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
    assert candidate["visit_count_basis"] == "observed_active_resource_entries_across_tabs_and_desktop_lower_bound"
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


def test_visits_count_back_and_forth_between_tabs_on_same_session():
    rows = []
    n = 0
    for _ in range(10):
        n += 1
        rows.extend(_row(n * 100 + i, resource="sheets.test/s1",
                         metadata={"tab_context_id": "tab-sheet"}) for i in range(12))
        n += 1
        rows.extend(_row(n * 100 + i, resource="mail.test/inbox", surface="Mail",
                         metadata={"tab_context_id": "tab-mail"}) for i in range(12))
    counts = {v["resource_locator"]: v["visit_count"] for v in _hunting_candidates(rows)}
    assert counts["sheets.test/s1"] == 10
    assert counts["mail.test/inbox"] == 10


def test_desktop_focus_without_resource_breaks_browser_visit_episode():
    rows = [
        _row(1, action="click", metadata={"tab_context_id": "tab-a"}),
        _row(2, action="focus_span", surface="Excel", resource=""),
        _row(3, action="click", metadata={"tab_context_id": "tab-a"}),
    ]
    candidates = _hunting_candidates(rows)
    assert candidates[0]["visit_count"] == 2


def test_background_navigation_noise_does_not_create_phantom_visits():
    rows = [
        _row(1, action="click", resource="sheet.example"),
        _row(2, action="navigation_requested", resource="mail.example"),
        _row(3, action="navigation_started", resource="mail.example"),
        _row(4, action="click", resource="sheet.example"),
    ]
    assert _hunting_candidates(rows) == []


def test_passive_background_tab_timing_cannot_create_false_resource_visits():
    rows = []
    for n in range(20):
        rows.append(_row(n * 2, resource="sheet.example",
                         action="click" if n == 0 else "browser_performance_timing",
                         metadata={"tab_context_id": "tab-sheet"}))
        rows.append(_row(n * 2 + 1, resource="mail.example",
                         action="browser_performance_timing",
                         metadata={"tab_context_id": "tab-mail"}))
    assert _hunting_candidates(rows) == []
    # Explicit tab activation (as opposed to background page timing) is a
    # reliable visit boundary even if the tab was already open.
    rows += [
        _row(200, resource="mail.example", action="browser_tab_activated",
             metadata={"tab_context_id": "tab-mail"}),
        _row(201, resource="sheet.example", action="browser_tab_activated",
             metadata={"tab_context_id": "tab-sheet"}),
        _row(202, resource="mail.example", action="browser_tab_activated",
             metadata={"tab_context_id": "tab-mail"}),
    ]
    counted = {x["resource_locator"]: x["visit_count"] for x in _hunting_candidates(rows)}
    assert counted == {"mail.example": 2, "sheet.example": 2}
