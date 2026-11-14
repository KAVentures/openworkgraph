"""Audit receipts must be ordered by actual UTC instants, not timestamp strings."""
import json
import os
from pathlib import Path

import pytest

from server.m365_operator_audit_routes import read_operator_audit


@pytest.mark.skipif(os.name != "posix", reason="Private audit reader requires POSIX")
def test_operator_audit_orders_fractional_and_whole_second_timestamps(tmp_path: Path):
    tmp_path.chmod(0o700)
    base = {
        "format": "openworkgraph.m365-operator-audit.v1",
        "action": "extract",
        "status": "review_required",
        "scope_sha256": "a" * 64,
        "log_id_sha256": "b" * 64,
        "automatic_retry_allowed": False,
    }
    # Lexical order incorrectly places the whole-second value after fractional
    # seconds because '+' sorts before '.'; actual chronological order is opposite.
    timestamps = (
        "2026-11-14T22:00:00+00:00",
        "2026-11-14T22:00:00.100000+00:00",
        "2026-11-14T22:00:00.900000+00:00",
    )
    for i, stamp in enumerate(timestamps):
        receipt = dict(base, timestamp_utc=stamp)
        path = tmp_path / ("m365-" + format(i, "032x") + ".json")
        path.write_text(json.dumps(receipt), encoding="utf-8")
        path.chmod(0o600)

    result = read_operator_audit(tmp_path, limit=2)
    assert [row["timestamp_utc"] for row in result["records"]] == [
        timestamps[2], timestamps[1],
    ]
    assert result["total_receipts"] == 3
    assert result["truncated"] is True
    assert all("scope_sha256" not in row and "log_id_sha256" not in row
               for row in result["records"])
