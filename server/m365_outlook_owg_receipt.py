"""Explicit, bounded local OWG append of a completed Microsoft Graph delta export.

The provider is never contacted. An uncertain POST is never automatically retried.
A receipt is issued only after independent authenticated inventory readback.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib import parse, request

from .m365_outlook_delta import (
    DeltaError, FORMAT as DELTA_FORMAT, _scope, _grant, _private_read,
    _delta_private_parent, _candidate, SHA,
)
from .servicenow_owg_append import _LOG, _session, _opener
from .servicenow_owg_bootstrap import _inventory, _read_json, _same_scope

FORMAT = "openworkgraph.m365-outlook-append-receipt.v1"
MAX_CSV = 2 * 1024 * 1024


class ReceiptError(ValueError):
    """Refusal without sensitive source data."""


class UncertainReceipt(ReceiptError):
    """POST may have committed. Review OWG history; never retry blindly."""


def _target(logs, log_id, namespace):
    matching = [row for row in logs if row.get("log_id") == log_id]
    if len(matching) != 1 or any(
        row.get("log_id") != log_id and _same_scope(row, namespace) for row in logs
    ):
        raise ReceiptError("Missing or ambiguous target")
    row = matching[0]
    identity = row.get("source_identity")
    namespaces = identity.get("namespaces") if type(identity) is dict else None
    if (type(namespaces) is not list or len(namespaces) != 1
            or type(namespaces[0]) is not dict
            or namespaces[0].get("source_namespace") != namespace
            or namespaces[0].get("case_namespace") != namespace
            or row.get("source_format") != "csv"
            or type(row.get("events")) is not int or row["events"] < 0):
        raise ReceiptError("Target namespace mismatch")
    return row["events"]


def _artifact(candidate, csv_path, scope, folder):
    _delta_private_parent(candidate.parent)
    if candidate.parent != csv_path.parent:
        raise ReceiptError("Candidate and CSV directory mismatch")
    raw = _private_read(candidate, 32768)
    try:
        data = json.loads(raw, object_pairs_hook=lambda pairs: _unique(pairs))
    except (ValueError, TypeError, UnicodeError):
        raise ReceiptError("Invalid candidate") from None
    if (type(data) is not dict or data.get("format") != DELTA_FORMAT
            or data.get("scope_sha256") != scope or data.get("csv_name") != csv_path.name
            or data.get("status") != "candidate_requires_confirmed_import"
            or type(data.get("csv_sha256")) is not str
            or not SHA.fullmatch(data["csv_sha256"])
            or type(data.get("events")) is not int or not 0 <= data["events"] <= 200):
        raise ReceiptError("Candidate scope mismatch")
    # Reuse the production candidate parser, including strict delta URL validation.
    # It verifies the complete candidate schema, prior CSV digest and folder scope.
    _candidate(candidate, scope, folder, True)
    body = _private_read(csv_path, MAX_CSV)
    if hashlib.sha256(body).hexdigest() != data["csv_sha256"]:
        raise ReceiptError("CSV digest mismatch")
    try:
        text = body.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text), strict=True))
    except (UnicodeError, csv.Error):
        raise ReceiptError("Invalid CSV") from None
    if (not rows or rows[0] != ["case_id", "activity", "timestamp", "source_event_id"]
            or len(rows) - 1 != data["events"]
            or any(len(row) != 4 or not row[0].startswith("outlookcase:")
                   or not row[3].startswith("outlookevent:") for row in rows[1:])
            or len({row[3] for row in rows[1:]}) != len(rows) - 1):
        raise ReceiptError("CSV event count or schema mismatch")
    return body, data


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ReceiptError("Duplicate JSON field")
        result[k] = v
    return result


def append_verified(*, tenant, mailbox, folder, grant_file: Path,
                    candidate: Path, csv_path: Path, log_id, confirm_log_id,
                    port, session, consent_local_read=False,
                    consent_local_append=False, opener=None):
    if consent_local_read is not True or consent_local_append is not True:
        raise ReceiptError("Separate local read and append consent required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise ReceiptError("Exact log confirmation required")
    session = _session(session)
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    body, data = _artifact(candidate, csv_path, scope, folder)
    namespace = "m365_" + scope[:32]
    transport = _opener() if opener is None else opener
    before = _target(_inventory(transport, port, session), log_id, namespace)
    _grant(grant_file, tenant, mailbox, folder)
    if data["events"] == 0:
        return {"format": FORMAT, "status": "verified_empty", "log_id": log_id,
                "events_before": before, "events_after": before,
                "csv_sha256": data["csv_sha256"], "checkpoint_advanced": False,
                "provider_requests": 0}
    query = parse.urlencode({
        "format": "csv", "case_column": "case_id",
        "activity_column": "activity", "timestamp_column": "timestamp",
        "event_id_column": "source_event_id",
        "source_namespace": namespace, "case_namespace": namespace,
    })
    url = f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/append?{query}"
    req = request.Request(url, data=body, method="POST", headers={
        "Authorization": "OWG-Session " + session,
        "x-owg-local-storage-confirmed": "true",
        "Content-Type": "text/csv; charset=utf-8", "Accept": "application/json",
    })
    # Once attempted, every failure is uncertain, even if the server committed.
    try:
        response = _read_json(transport, req)
        if (type(response.get("log")) is not dict
                or response["log"].get("log_id") != log_id
                or any(type(response.get(k)) is not int or response[k] < 0
                       for k in ("added_events", "duplicate_events_skipped", "rejected_events", "batch"))
                or response["batch"] < 1
                or sum(response[k] for k in ("added_events", "duplicate_events_skipped", "rejected_events")) != data["events"]
                or response["rejected_events"] != 0):
            raise ReceiptError("Unverified append result")
        _grant(grant_file, tenant, mailbox, folder)
        after = _target(_inventory(transport, port, session), log_id, namespace)
        if after != before + response["added_events"]:
            raise ReceiptError("Inventory readback mismatch")
        _grant(grant_file, tenant, mailbox, folder)
    except Exception:
        raise UncertainReceipt("Append possibly committed; inspect OWG history") from None
    return {"format": FORMAT, "status": "verified", "log_id": log_id,
            "events_before": before, "events_after": after,
            "added_events": response["added_events"],
            "duplicate_events_skipped": response["duplicate_events_skipped"],
            "batch": response["batch"], "csv_sha256": data["csv_sha256"],
            "checkpoint_advanced": False, "provider_requests": 0}


def main(argv=None, *, opener=None):
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        p.add_argument("--" + flag, required=True)
    for flag in ("grant-file", "candidate", "csv-path"):
        p.add_argument("--" + flag, required=True, type=Path)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--consent-local-read", action="store_true")
    p.add_argument("--consent-local-append", action="store_true")
    a = p.parse_args(argv)
    try:
        result = append_verified(
            tenant=a.tenant, mailbox=a.mailbox, folder=a.folder,
            grant_file=a.grant_file, candidate=a.candidate, csv_path=a.csv_path,
            log_id=a.log_id, confirm_log_id=a.confirm_log_id, port=a.port,
            session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=a.consent_local_read,
            consent_local_append=a.consent_local_append, opener=opener)
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainReceipt:
        print(json.dumps({"format": FORMAT, "status": "unverified",
                          "possible_local_append": True}, sort_keys=True))
        return 3
    except (ReceiptError, DeltaError, ValueError, OSError, TypeError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "possible_local_append": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
