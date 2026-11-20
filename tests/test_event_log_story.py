"""The process in plain words."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_story as st
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def test_sentences_come_from_figures():
    rows = ["case,activity,time"]
    for c in range(30):
        steps = ["Receive", "Check", "Pay"] if c % 5 else ["Receive", "Pay"]
        for j, a in enumerate(steps):
            rows.append(f"c{c},{a},2024-01-{1 + c % 28:02d}T{j * 2:02d}:00:00Z")
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="s", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity",
                                        "timestamp_column": "time"})["log_id"]
    r = st.story(log)
    text = r["text"]
    assert text.startswith("The log has 30 cases with 84 events across 3 activities")
    assert "Receive → Check → Pay) is followed by 80% of cases" in text
    assert "follow it exactly" in text
    assert all(s["figures"] is not None for s in r["sentences"]) and "language model" in r["method"]
