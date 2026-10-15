"""Dashboard polish contracts: valid post-join lifecycle and local history browse."""
from __future__ import annotations

import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def content(path):
    return (ROOT/path).read_text(encoding="utf-8")


def test_join_does_not_dereference_deleted_button_or_leak_consumed_invite():
    js=content("dashboard/org_join.js")
    assert "const joinButton = $('orgJoinButton');" in js
    assert "if (joinButton) joinButton.disabled = false;" in js
    assert "card.replaceChildren()" not in js
    assert "if ($('orgJoinCode')) $('orgJoinCode').value = '';" in js
    assert "if (card) card.hidden = !!st.enrolled;" in js


def test_redaction_policy_is_not_shown_as_an_error_when_optional():
    js=content("dashboard/org_join.js")
    assert "Organization does not require Redacted AI context" in js
    assert "🔒" in js
    assert "yes(s.forces_redacted_ai_context" not in js


def test_saved_local_history_does_not_require_new_ai_history_permission():
    html=content("dashboard/index.html")
    assert 'id="savedDayPicker"' in html
    assert 'id="savedDayPrevious"' in html
    assert 'id="savedDayNext"' in html
    assert "scope:'all',order:'newest'" in html
    assert "window.__owgAuthReady" in html
    assert "rows.replaceChildren()" in html
    assert "new URLSearchParams" in html
    assert "if(append&&savedDayCursor)params.set('cursor',savedDayCursor)" in html
    assert "cell.textContent=String(value||'')" in html
    assert "No retained activity for this date" in html


def test_today_counter_explicitly_current_and_saved_examples_separate():
    html=content("dashboard/index.html")
    assert "Current-run pattern runs" in html
    assert "Patterns in this run" in html
    assert "saved-history workflow examples appear separately below" in html


def test_local_sync_time_and_version_are_human_readable():
    gateway=content("dashboard/gateway_panel.js")
    signal=content("dashboard/browser_signals.js")
    assert "time.toLocaleString" in gateway
    assert "your local time" in gateway
    assert "Expected: ${expected}" not in signal
    assert "needs an update" in signal


def test_browser_date_bounds_and_dst_are_in_user_local_time():
    html=content("dashboard/index.html")
    assert "new Date(year,month-1,day)" in html
    assert "new Date(year,month-1,day+1)" in html
    assert "new Date(year,month-1,day-1)" in html



def test_today_pattern_count_and_card_share_one_authoritative_endpoint():
    html=content("dashboard/index.html")
    gateway=content("dashboard/gateway_panel.js")
    assert "repeated_task_patterns||[]" not in html
    assert "const patterns=Array.isArray(payload.patterns)?payload.patterns:[]" in gateway
    assert "if(counter) counter.textContent=String(patterns.reduce(" in gateway
    assert "renderRichPatterns(patterns)" in gateway
    assert "temporarily unavailable" in gateway
