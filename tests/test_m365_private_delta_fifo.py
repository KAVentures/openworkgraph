"""Regression: a hostile local artifact cannot stall the M365 operator read."""
import os
import threading

import pytest

from server.m365_outlook_delta import DeltaError, _private_read


@pytest.mark.skipif(not hasattr(os, "mkfifo") or os.name != "posix",
                    reason="POSIX FIFO semantics required")
def test_private_delta_read_refuses_fifo_without_waiting_for_writer(tmp_path):
    fifo = tmp_path / "candidate.json"
    os.mkfifo(fifo, 0o600)
    outcome = []

    def attempt():
        try:
            _private_read(fifo, 8192)
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive(), "FIFO read blocked before metadata validation"
    assert len(outcome) == 1
    assert isinstance(outcome[0], DeltaError)
    assert str(outcome[0]) == "Private artifact invalid"
