"""Offline, bounded review of a consented Microsoft 365 delta candidate.

No provider requests, OWG writes, checkpoint commits or secrets in output.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from .m365_outlook_delta import (
    DeltaError, MAX_CANDIDATE_BYTES, MAX_RECORDS, _candidate,
    _delta_private_parent, _grant, _private_read, _scope,
)
from .m365_outlook_selected import _pairs

FORMAT = "openworkgraph.m365-delta-review.v1"
HEADER = ["case_id", "activity", "timestamp", "source_event_id"]


def review(*, tenant: str, mailbox: str, folder: str, grant_file: Path,
           candidate: Path, consent_local_read=False):
    """Verify current grant, private candidate/CSV and bounded row identities."""
    if consent_local_read is not True:
        raise DeltaError("Explicit local read consent required")
    candidate, grant_file = Path(candidate), Path(grant_file)
    _delta_private_parent(candidate.parent)
    _grant(grant_file, tenant, mailbox, folder)
    # The production continuation validator checks exact schema, scope,
    # complete-round cursor, digest, removal IDs and event budget.
    _candidate(candidate, _scope(tenant, mailbox, folder), folder, True)
    record = json.loads(_private_read(candidate, MAX_CANDIDATE_BYTES),
                        object_pairs_hook=_pairs)
    data = _private_read(candidate.parent / record["csv_name"], 2 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != record["csv_sha256"]:
        raise DeltaError("Candidate CSV changed")
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True)
        if next(reader) != HEADER:
            raise DeltaError("Unexpected CSV header")
        count, ids = 0, set()
        for row in reader:
            if (len(row) != 4 or not row[0].startswith("outlookcase:")
                    or len(row[0]) != 76
                    or not row[3].startswith("outlookevent:")
                    or len(row[3]) != 77
                    or any(c not in "0123456789abcdef" for c in row[0][12:] + row[3][13:])
                    or row[1] not in ("Observed read status: read",
                                      "Observed read status: unread")
                    or not row[2] or row[3] in ids):
                raise DeltaError("Invalid event projection")
            ids.add(row[3])
            count += 1
            if count > MAX_RECORDS:
                raise DeltaError("Event budget exceeded")
    except (UnicodeError, csv.Error, StopIteration):
        raise DeltaError("Invalid event CSV") from None
    if count != record["events"]:
        raise DeltaError("Candidate event count mismatch")
    _grant(grant_file, tenant, mailbox, folder)
    return {
        "format": FORMAT, "status": "review_ready",
        "event_snapshots": count,
        "removal_markers_for_review": record["removals"],
        "pages": record["pages"],
        "csv_sha256": record["csv_sha256"],
        "scope_sha256": record["scope_sha256"],
        "manual_review_and_separate_append_required": True,
        "provider_requests": 0, "owg_writes": 0,
        "checkpoint_advanced": False, "provider_acceptance_verified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant", "mailbox", "folder"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--grant-file", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--consent-local-read", action="store_true")
    args = vars(parser.parse_args(argv))
    try:
        result = review(**args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (DeltaError, OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
