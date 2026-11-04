"""Regression for the live dashboard's review-gated handoff navigation."""
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text()

def test_review_progress_requires_four_distinct_workspaces():
    assert 'id="owgJourneyReviewHandoff" disabled' in HTML
    assert 'const reviewOrder=[\'privacy\',\'discovery\',\'map\',\'review\']' in HTML
    for stage in ("privacy", "discovery", "map", "review"):
        assert f'data-owg-review="{stage}"' in HTML
    assert 'reviewHandoff.disabled=!reviewOrder.every(key=>checked.has(key))' in HTML

def test_no_automatic_authority_and_stale_progress_invalidated():
    assert 'visited.add(key)' in HTML
    assert 'checked.delete(key)' in HTML
    assert 'reviewOrder.slice(index+1).forEach(next=>checked.delete(next))' in HTML
    assert 'checked.clear();visited.clear();renderReview()' in HTML
    assert 'control?.click()' in HTML
    assert 'This does not authorize execution' in HTML
