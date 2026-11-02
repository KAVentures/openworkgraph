"""Explicit reviewed Salesforce CSV -> local OWG source-ID append -> checkpoint.

No Salesforce request is made here. Only the existing authenticated loopback
OWG REST append can write locally; checkpoint advances after verified readback.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request

from .salesforce_case_history_incremental import (
    FORMAT as REVIEW_FORMAT, IncrementalError, MAX_CSV, MAX_MANIFEST, SHA,
    _digest, _paths, _read_json, _read_private, _state, commit,
)
from .salesforce_case_history_live import _utc, MAX_WINDOW
from .servicenow_owg_append import (
    _LOG, _opener, _pairs_unique, _session, verify_local_target, AppendError,
    MAX_RESPONSE_BYTES,
)

FORMAT = "openworkgraph.salesforce-reviewed-owg-apply.v1"
CSV_FIELDS = ("case_id", "activity", "timestamp", "source_event_id")
HASHED_ID = re.compile(r"sf:[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.csv\Z")
MANIFEST_FIELDS = {"format", "scope_sha256", "bootstrap_since", "since", "until",
                   "previous_until", "overlap_hours", "expected_checkpoint_sha256",
                   "csv_name", "csv_sha256", "events", "source_rows", "status"}


class ApplyError(ValueError):
    """Counts-only errors; never expose source IDs, paths or secrets."""


class UncertainApply(ApplyError):
    """The OWG append may have committed, but checkpoint was not confirmed."""


def _review(checkpoint: Path, manifest: Path, confirmed: str):
    """Validate the entire private review before any local HTTP write."""
    _paths(checkpoint, manifest)
    if type(confirmed) is not str or not SHA.fullmatch(confirmed):
        raise ApplyError("Original review digest confirmation required")
    record, actual_sha = _read_json(manifest, MAX_MANIFEST)
    if actual_sha != confirmed or set(record) != MANIFEST_FIELDS:
        raise ApplyError("Reviewed manifest changed or has invalid schema")
    if (record["format"] != REVIEW_FORMAT
            or type(record["scope_sha256"]) is not str
            or not SHA.fullmatch(record["scope_sha256"])
            or type(record["csv_sha256"]) is not str
            or not SHA.fullmatch(record["csv_sha256"])
            or type(record["csv_name"]) is not str
            or not NAME.fullmatch(record["csv_name"])
            or record["status"] != "exported"
            or type(record["events"]) is not int or not 1 <= record["events"] <= 200
            or type(record["source_rows"]) is not int
            or not record["events"] <= record["source_rows"] <= 200
            or type(record["overlap_hours"]) is not int
            or not 1 <= record["overlap_hours"] <= 168):
        raise ApplyError("Invalid prepared source scope")
    start, end = _utc(record["since"]), _utc(record["until"])
    if (not _utc(record["bootstrap_since"]) <= start < end
            or end > start + MAX_WINDOW):
        raise ApplyError("Invalid prepared source window")
    previous = record["previous_until"]
    expected = record["expected_checkpoint_sha256"]
    if previous is None:
        if expected is not None:
            raise ApplyError("Invalid bootstrap checkpoint reference")
    elif (type(previous) is not str or not _utc(previous) < end
          or type(expected) is not str or not SHA.fullmatch(expected)):
        raise ApplyError("Invalid prior checkpoint reference")
    current, current_sha = _state(checkpoint)
    if current_sha != expected:
        raise ApplyError("Stale checkpoint: prepare a new review")
    if current is not None:
        if (current["scope_sha256"] != record["scope_sha256"]
                or current["until"] != previous
                or current["bootstrap_since"] != record["bootstrap_since"]
                or current["overlap_hours"] != record["overlap_hours"]):
            raise ApplyError("Prepared scope differs from checkpoint")
    elif previous is not None:
        raise ApplyError("Prior checkpoint missing")
    csv_path = manifest.parent / record["csv_name"]
    _paths(checkpoint, manifest, csv_path)
    body = _read_private(csv_path, MAX_CSV)
    if _digest(body) != record["csv_sha256"]:
        raise ApplyError("Prepared CSV changed")
    try:
        reader = csv.reader(io.StringIO(body.decode("utf-8"), newline=""), strict=True)
        if tuple(next(reader)) != CSV_FIELDS:
            raise ApplyError("Prepared CSV header mismatch")
        seen = set()
        count = 0
        for row in reader:
            if len(row) != 4 or count >= 200:
                raise ApplyError("Prepared CSV row limit or shape mismatch")
            case, activity, stamp, event = row
            if (not HASHED_ID.fullmatch(case) or not HASHED_ID.fullmatch(event)
                    or event in seen or not (activity == "Case created" or
                    (activity.startswith("Status: ") and 1 <= len(activity[8:]) <= 80
                     and all(32 <= ord(ch) < 127 or ord(ch) > 159 for ch in activity[8:])))):
                raise ApplyError("Prepared source identity or activity invalid")
            instant = datetime.fromisoformat(stamp)
            if (instant.tzinfo is None or instant.utcoffset().total_seconds() != 0
                    or not start <= instant < end):
                raise ApplyError("Prepared timestamp outside selected window")
            seen.add(event)
            count += 1
        if count != record["events"]:
            raise ApplyError("Prepared event count changed")
    except (UnicodeError, csv.Error, StopIteration, ValueError, OverflowError, TypeError) as exc:
        if isinstance(exc, ApplyError):
            raise
        raise ApplyError("Invalid prepared Salesforce CSV") from None
    return record, body, "sf_" + record["scope_sha256"][:32]


def _get_append_history(log_id, port, session, batch, expected_added,
                        expected_duplicates, *, opener=None):
    url = f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/appends"
    req = request.Request(url, method="GET", headers={
        "Authorization": "OWG-Session " + session, "Accept": "application/json",
        "Cache-Control": "no-store",
    })
    transport = _opener() if opener is None else opener
    try:
        with transport.open(req, timeout=15) as response:
            status, final = response.status, response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise ApplyError("OWG append history readback unavailable") from None
    if status != 200 or final != url or media != "application/json" or len(raw) > MAX_RESPONSE_BYTES:
        raise ApplyError("OWG append history readback refused")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique)
    except (UnicodeError, ValueError, TypeError):
        raise ApplyError("Invalid OWG append history") from None
    if type(data) is not dict or type(data.get("appends")) is not list:
        raise ApplyError("Invalid OWG append history")
    matching = [r for r in data["appends"] if type(r) is dict and r.get("batch") == batch]
    if (len(matching) != 1 or matching[0].get("source_format") != "csv"
            or type(matching[0].get("added_events")) is not int
            or matching[0]["added_events"] != expected_added
            or type(matching[0].get("duplicate_events_skipped")) is not int
            or matching[0]["duplicate_events_skipped"] != expected_duplicates
            or type(matching[0].get("rejected_events")) is not int
            or matching[0]["rejected_events"] != 0):
        raise ApplyError("OWG append batch not independently readable")


def _append(log_id, port, session, namespace, body, expected, *, opener=None):
    query = parse.urlencode({
        "format": "csv", "case_column": "case_id", "activity_column": "activity",
        "timestamp_column": "timestamp", "event_id_column": "source_event_id",
        "source_namespace": namespace, "case_namespace": namespace,
    })
    url = f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/append?{query}"
    req = request.Request(url, data=body, method="POST", headers={
        "Authorization": "OWG-Session " + session,
        "X-OWG-Local-Storage-Confirmed": "true", "Content-Type": "text/csv; charset=utf-8",
        "Accept": "application/json", "Cache-Control": "no-store",
    })
    transport = _opener() if opener is None else opener
    try:
        with transport.open(req, timeout=15) as response:
            status, final = response.status, response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise ApplyError("Local OWG append outcome unknown") from None
    if status != 200 or final != url or media != "application/json" or len(raw) > MAX_RESPONSE_BYTES:
        raise ApplyError("Local OWG append outcome unverified")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique)
    except (UnicodeError, ValueError, TypeError):
        raise ApplyError("Invalid local OWG append response") from None
    if (type(data) is not dict or type(data.get("log")) is not dict
            or data["log"].get("log_id") != log_id
            or any(type(data.get(k)) is not int or data[k] < 0 for k in
                   ("batch", "added_events", "duplicate_events_skipped", "rejected_events"))
            or data["batch"] < 1 or data["rejected_events"] != 0
            or data["added_events"] + data["duplicate_events_skipped"] != expected
            or type(data.get("source_identity")) is not dict
            or data["source_identity"].get("deduplication_basis")
               != "declared_source_namespace_and_supplied_event_id"):
        raise ApplyError("Local OWG append receipt did not verify all selected events")
    return data


def apply(*, checkpoint, manifest, confirm_manifest_sha256, log_id,
          confirm_log_id, port, consent_prepared_read=False,
          consent_local_append=False, consent_checkpoint_advance=False,
          session=None, opener=None):
    """One operator-approved local append, readback and durable checkpoint commit."""
    if not (consent_prepared_read and consent_local_append and consent_checkpoint_advance):
        raise ApplyError("Three explicit review, append and checkpoint consents required")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise ApplyError("Exact local target log and port confirmation required")
    session = _session(session)
    record, body, namespace = _review(checkpoint, manifest, confirm_manifest_sha256)
    try:
        verify_local_target(log_id, port, namespace, session, opener=opener)
    except AppendError:
        raise ApplyError("Selected local event log has a different source scope or is unavailable") from None
    # An exclusive apply lock avoids concurrent POSTs of the same review.
    lock = checkpoint.with_name(checkpoint.name + ".apply.lock")
    from .salesforce_case_history_incremental import _new_private
    _new_private(lock, b"applying\n")
    try:
        if _state(checkpoint)[1] != record["expected_checkpoint_sha256"]:
            raise ApplyError("Checkpoint changed before local append")
        try:
            result = _append(log_id, port, session, namespace, body, record["events"], opener=opener)
            _get_append_history(log_id, port, session, result["batch"],
                                result["added_events"], result["duplicate_events_skipped"],
                                opener=opener)
            committed = commit(checkpoint=checkpoint, manifest=manifest,
                               confirm_manifest_sha256=confirm_manifest_sha256,
                               confirm_imported=True)
        except (ApplyError, IncrementalError, AppendError, OSError, ValueError):
            raise UncertainApply("Inspect local append history before an operator-controlled retry") from None
        return {"format": FORMAT, "status": "applied", "batch": result["batch"],
                "source_rows": record["source_rows"], "events": record["events"],
                "added_events": result["added_events"],
                "duplicate_events_skipped": result["duplicate_events_skipped"],
                "checkpoint_advanced": committed["checkpoint_advanced"],
                "owg_append_readback_verified": True, "provider_writes_performed": False,
                "source_completeness": "unknown"}
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--confirm-manifest-sha256", required=True)
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--confirm-log-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-prepared-read", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    parser.add_argument("--consent-checkpoint-advance", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = apply(checkpoint=args.checkpoint, manifest=args.manifest,
                       confirm_manifest_sha256=args.confirm_manifest_sha256,
                       log_id=args.log_id, confirm_log_id=args.confirm_log_id,
                       port=args.port, consent_prepared_read=args.consent_prepared_read,
                       consent_local_append=args.consent_local_append,
                       consent_checkpoint_advance=args.consent_checkpoint_advance,
                       session=os.environ.get("OWG_DASHBOARD_SESSION"))
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainApply:
        print(json.dumps({"format": FORMAT, "status": "unverified",
                          "possible_local_append": True, "checkpoint_advanced": False,
                          "reason": "Inspect local OWG append history before retrying"}, sort_keys=True))
        return 3
    except (ApplyError, IncrementalError, AppendError, OSError, ValueError, TypeError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "possible_local_append": False, "checkpoint_advanced": False,
                          "reason": "Check review digest, private artifacts, consents, session and target"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
