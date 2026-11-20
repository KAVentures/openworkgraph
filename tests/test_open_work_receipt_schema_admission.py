"""Receipt journals must refuse untrusted SQLite schema substitutions."""
import sqlite3

import pytest

from server.open_work_attempt_receipts import append_receipt, list_receipts


SCOPE = {"tenant": "owg:scope:" + "a" * 32, "actor": "actor-a", "attempt": "attempt-a"}
UNKNOWN = {"status": "unknown", "provider_operation": "unknown", "customer_outcome": "unknown"}


def test_receipt_journal_normal_roundtrip(tmp_path):
    path = str(tmp_path / "receipts.sqlite3")
    append_receipt(path, **SCOPE, verdict=UNKNOWN)
    result = list_receipts(path, **SCOPE)
    assert len(result) == 1
    assert result[0]["status"] == "unknown"
    assert result[0]["customer_outcome"] == "unknown"


@pytest.mark.parametrize("tamper", [
    "CREATE TRIGGER steal AFTER INSERT ON attempt_receipts BEGIN DELETE FROM attempt_receipts; END",
    "ALTER TABLE attempt_receipts ADD COLUMN unreviewed TEXT",
    "CREATE VIEW leak AS SELECT * FROM attempt_receipts",
    "CREATE INDEX extra_idx ON attempt_receipts(actor)",
])
def test_refuse_untrusted_schema_before_receipt_access(tmp_path, tamper):
    path = str(tmp_path / "receipts.sqlite3")
    append_receipt(path, **SCOPE, verdict=UNKNOWN)
    with sqlite3.connect(path) as db:
        db.execute(tamper)
    with pytest.raises(ValueError, match="incompatible receipt journal schema"):
        list_receipts(path, **SCOPE)
    with pytest.raises(ValueError, match="incompatible receipt journal schema"):
        append_receipt(path, **SCOPE, verdict=UNKNOWN)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0] == 1
