"""Read-only handoff choice route contract."""
from pathlib import Path

def test_review_choice_workbench_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'server/review_choice_routes.py').read_text()
    script = (root / 'dashboard/review_choice_workbench.js').read_text()
    assert 'dashboard_session_valid' in source
    assert 'execution_authorized": False' in source
    assert 'source_read_authorized": False' in source
    assert 'Cache-Control": "no-store"' in source
    assert 'Review basis changed. Inspect again' in script
    assert 'window.__owgAuthReady' in script
