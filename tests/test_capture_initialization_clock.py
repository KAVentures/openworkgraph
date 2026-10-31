"""A synthesized in-memory state is not a persisted recording decision."""
import json

import pytest

from shared import capture_control as capture

STAMP = '2026-10-30T12:00:00+00:00'


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setenv('WORKFLOW_OBSERVER_DATA', str(tmp_path))
    monkeypatch.delenv('WORKFLOW_OBSERVER_RUN_STARTED_AT', raising=False)
    monkeypatch.setattr(capture, '_now', lambda: STAMP)
    return tmp_path


@pytest.mark.parametrize('explicit_start', [False, True])
def test_equal_clock_fresh_initialization_persists_before_noop(clock, explicit_start):
    assert not capture.state_path().exists()
    state = capture.initialize_run(STAMP if explicit_start else None)
    assert capture.state_path().is_file()
    assert json.loads(capture.state_path().read_text()) == state
    assert state['state'] == 'recording'
    assert state['run_started_at'] == STAMP
    before = capture.state_path().read_bytes()
    assert capture.initialize_run(STAMP) == state
    assert capture.state_path().read_bytes() == before


@pytest.mark.parametrize('action,state', [('pause', 'paused'), ('stop', 'stopped')])
def test_same_run_initialization_preserves_explicit_off_state(clock, action, state):
    capture.initialize_run(STAMP)
    capture.set_state(action, at=STAMP)
    before = capture.state_path().read_bytes()
    result = capture.initialize_run(STAMP)
    assert result['state'] == state
    assert result['skip_intervals'] == [{'since': STAMP, 'until': None,
                                         'reason': 'capture_' + state}]
    assert capture.state_path().read_bytes() == before
