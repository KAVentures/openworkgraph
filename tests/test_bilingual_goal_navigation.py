"""Grounded English/Swedish work navigation without inferred task identity."""
from __future__ import annotations

from gateway.goal_context import _goal_terms, build_goal_context


def test_bilingual_goal_matches_retained_swedish_timesheet_or_invoice():
    terms, hints = _goal_terms("invoice my locum hours")
    assert {"invoice", "faktura", "hours", "tidrapport", "locum", "vikarie"} <= terms
    assert not hints or isinstance(hints, list)
    example = [
        {"event_id":"a","observed_at":"2026-10-10T10:00:00Z",
         "window_title":"Tidrapport - September", "app":"Excel",
         "event_type":"focus_span","session_id":"s"},
        {"event_id":"b","observed_at":"2026-10-10T10:05:00Z",
         "window_title":"Faktura för timarbete", "app":"Fortnox",
         "event_type":"browser_click","session_id":"s"},
    ]
    result=build_goal_context("invoice my locum hours", example)
    assert result["status"]=="possible_matches"
    assert result["examples"]
    assert result["examples"][0]["task_identity_confirmed"] is False
    assert result["examples"][0]["outcome_verified"] is False


def test_unrelated_work_does_not_match_and_synonyms_are_not_translated_content():
    rows=[{"event_id":"c","observed_at":"2026-10-10T10:00:00Z",
           "window_title":"Clinical referrals", "app":"Safari",
           "event_type":"browser_navigation"}]
    result=build_goal_context("invoice my locum hours",rows)
    assert result["status"]=="no_relevant_match"
    assert result["examples"]==[]
    terms, _ = _goal_terms("review the pull request")
    assert "github" in terms
