"""Read-only authenticated Microsoft 365 source-event membership reconciliation.

A complete local source-key export is compared with the separately reviewed
candidate. This is OWG membership evidence, not Microsoft Graph verification,
checkpoint authorization, or permission to replay a possibly committed POST.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
from urllib import parse, request

from .m365_outlook_delta import _grant, _scope
from .m365_outlook_owg_receipt import ReceiptError, _artifact, _target
from .servicenow_owg_append import _LOG, _session, _opener
from .servicenow_owg_bootstrap import _inventory

FORMAT = "openworkgraph.m365-event-membership.v1"
MAX_EXPORT_BYTES = 16 * 1024 * 1024
HEADER = ["source_namespace", "source_event_id", "case_namespace",
          "case_id", "activity", "timestamp", "resource", "attributes"]


def reconcile(*, tenant, mailbox, folder, grant_file, candidate, csv_path,
              log_id, confirm_log_id, port, session, consent_local_read=False,
              opener=None):
    """Check exact source-key membership; never write or infer retry safety."""
    if consent_local_read is not True:
        raise ReceiptError("Separate local read consent required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise ReceiptError("Exact local log confirmation required")
    session = _session(session)
    scope = _scope(tenant, mailbox, folder)
    namespace = "m365_" + scope[:32]
    _grant(Path(grant_file), tenant, mailbox, folder)
    body, artifact = _artifact(Path(candidate), Path(csv_path), scope, folder)
    expected = {}
    rows = csv.DictReader(io.StringIO(body.decode("utf-8")), strict=True)
    if rows.fieldnames != ["case_id", "activity", "timestamp", "source_event_id"]:
        raise ReceiptError("Invalid candidate CSV")
    for row in rows:
        key = row["source_event_id"]
        if key in expected:
            raise ReceiptError("Duplicate candidate source key")
        expected[key] = row["case_id"]
    if len(expected) != artifact["events"]:
        raise ReceiptError("Candidate event count changed")

    transport = _opener() if opener is None else opener
    _target(_inventory(transport, port, session), log_id, namespace)
    url = (f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/export?"
           + parse.urlencode({"format": "sourcecsv", "case_type": "source_case"}))
    req = request.Request(url, headers={
        "Authorization": "OWG-Session " + session, "Accept": "text/csv"})
    # The response must be complete. A capped read that exceeds the limit is
    # an error, never a partial-membership success.
    with transport.open(req, timeout=30) as response:
        if getattr(response, "status", 200) != 200:
            raise ReceiptError("Authenticated source export refused")
        exported = response.read(MAX_EXPORT_BYTES + 1)
    if len(exported) > MAX_EXPORT_BYTES:
        raise ReceiptError("Source export exceeds reconciliation budget")
    try:
        text = exported.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames != HEADER:
            raise ReceiptError("Source export schema mismatch")
        present = set()
        observed = set()
        for row in reader:
            if None in row or None in row.values():
                raise ReceiptError("Malformed source export")
            if row["source_namespace"] != namespace or row["case_namespace"] != namespace:
                raise ReceiptError("Cross-namespace source export")
            key = row["source_event_id"]
            if key in observed:
                raise ReceiptError("Duplicate saved source key")
            observed.add(key)
            if key in expected:
                if row["case_id"] != expected[key]:
                    raise ReceiptError("Source case identity mismatch")
                present.add(key)
        if len(observed) != _target(_inventory(transport, port, session), log_id, namespace):
            raise ReceiptError("Source export and current inventory disagree")
    except (UnicodeError, csv.Error, TypeError, ValueError) as exc:
        raise ReceiptError("Source export could not be verified") from exc
    _grant(Path(grant_file), tenant, mailbox, folder)
    missing = len(expected) - len(present)
    return {"format": FORMAT,
            "status": "all_candidate_events_present" if not missing else "manual_reconciliation_required",
            "candidate_events": len(expected), "matched_events": len(present),
            "missing_events": missing, "checkpoint_advanced": False,
            "automatic_retry_allowed": False, "provider_requests": 0,
            "owg_writes": 0, "provider_acceptance_verified": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        p.add_argument("--" + flag, required=True)
    for flag in ("grant-file", "candidate", "csv-path"):
        p.add_argument("--" + flag, type=Path, required=True)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--consent-local-read", action="store_true")
    args = vars(p.parse_args(argv))
    args["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
    try:
        result = reconcile(**args)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "all_candidate_events_present" else 3
    except Exception:
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "automatic_retry_allowed": False,
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
