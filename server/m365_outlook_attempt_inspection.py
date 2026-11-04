"""Read-only, authenticated inspection of an uncertain M365 append attempt.

Never POST, retry, clear a latch, or infer event-level membership from counts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .m365_outlook_delta import _delta_private_parent, _grant, _private_read, _scope
from .m365_outlook_owg_receipt import ReceiptError, _target, _unique
from .m365_outlook_verified_append_receipt import FORMAT as ATTEMPT_FORMAT
from .servicenow_owg_append import _LOG, _opener, _session
from .servicenow_owg_bootstrap import _inventory

FORMAT = "openworkgraph.m365-attempt-inspection.v1"


def inspect_attempt(*, tenant, mailbox, folder, grant_file: Path,
                    receipt_file: Path, log_id, confirm_log_id, port=8765,
                    session=None, consent_local_read=False, opener=None):
    """Inspect a persisted attempt against live OWG inventory, without writes.

    Inventory totals can indicate changes, but never establish which events
    committed. All valid attempts remain indeterminate until independently
    reconciled using event identities.
    """
    if consent_local_read is not True:
        raise ReceiptError("Fresh local-read consent required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise ReceiptError("Exact target confirmation required")
    receipt_file, grant_file = Path(receipt_file), Path(grant_file)
    _delta_private_parent(receipt_file.parent)
    if (receipt_file.suffix != ".json"
            or not receipt_file.name.startswith("receipt-")
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for c in receipt_file.name)):
        raise ReceiptError("Invalid receipt path")
    latch = receipt_file.with_name(receipt_file.name + ".attempt")
    if receipt_file.exists() or receipt_file.is_symlink():
        raise ReceiptError("Receipt exists; use verified receipt workflow")
    raw = _private_read(latch, 8192)
    try:
        data = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, TypeError):
        raise ReceiptError("Invalid attempt latch") from None
    if (type(data) is not dict or data.get("format") != ATTEMPT_FORMAT
            or data.get("status") != "attempt_started"
            or data.get("log_id") != log_id
            or data.get("checkpoint_advanced") is not False
            or data.get("automatic_retry") is not False
            or type(data.get("csv_sha256")) is not str
            or len(data["csv_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in data["csv_sha256"])):
        raise ReceiptError("Attempt latch scope or schema mismatch")
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    authorized_session = _session(session)
    transport = _opener() if opener is None else opener
    count = _target(_inventory(transport, port, authorized_session),
                    log_id, "m365_" + scope[:32])
    _grant(grant_file, tenant, mailbox, folder)
    return {"format": FORMAT, "status": "indeterminate",
            "log_id": log_id, "inventory_events_now": count,
            "attempt_present": True, "event_membership_verified": False,
            "safe_to_retry": False, "checkpoint_advanced": False,
            "provider_requests": 0, "owg_write_requests": 0}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        p.add_argument("--" + name, required=True)
    for name in ("grant-file", "receipt-file"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--consent-local-read", action="store_true")
    a = p.parse_args(argv)
    try:
        result = inspect_attempt(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            grant_file=a.grant_file, receipt_file=a.receipt_file,
            log_id=a.log_id, confirm_log_id=a.confirm_log_id,
            port=a.port, session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=a.consent_local_read)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReceiptError, ValueError, TypeError, OSError, KeyError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "safe_to_retry": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
