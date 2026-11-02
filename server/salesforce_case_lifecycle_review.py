"""Read-only Salesforce Case lifecycle audit for a selected OWG source scope.

This is a review artifact, never a deletion instruction or proof of hard deletion.
An absent Case row is unresolved (including permission and retention ambiguity).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib import error, parse, request

from scripts.salesforce_case_history import _key
from server.salesforce_case_history_live import (
    _instance, _NoRedirect, _unique, _nonfinite, TOKEN,
)
from server.salesforce_case_history_incremental import (
    _scope_hash, _private_parent, _new_private, _canonical, _digest,
)

FORMAT = "openworkgraph.salesforce-case-lifecycle-review.v1"
MAX_BYTES = 65536


class LifecycleReviewError(ValueError):
    """Public errors never include provider identities or secrets."""


def _query(cases):
    selected = ",".join("'" + case + "'" for case in sorted(cases))
    return ("SELECT Id, IsDeleted FROM Case WHERE Id IN (" + selected +
            ") ORDER BY Id LIMIT 9")


def _read(opener, origin, bearer, cases):
    url = origin + "/services/data/v60.0/queryAll?" + parse.urlencode(
        {"q": _query(cases)})
    req = request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + bearer,
        "Accept": "application/json",
        "Cache-Control": "no-store",
    })
    try:
        with opener.open(req, timeout=10) as response:
            status = response.status
            final_url = response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            raw = response.read(MAX_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise LifecycleReviewError("Salesforce lifecycle read refused") from None
    if (status != 200 or final_url != url or media != "application/json"
            or len(raw) > MAX_BYTES):
        raise LifecycleReviewError("Incomplete or unsafe lifecycle response")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise LifecycleReviewError("Invalid lifecycle JSON") from None
    if (type(data) is not dict or data.get("done") is not True
            or "nextRecordsUrl" in data or type(data.get("totalSize")) is not int
            or type(data.get("records")) is not list
            or data["totalSize"] != len(data["records"])
            or len(data["records"]) > len(cases)):
        raise LifecycleReviewError("Incomplete lifecycle selection")
    found = {}
    for row in data["records"]:
        if (type(row) is not dict or type(row.get("Id")) is not str
                or row["Id"] not in cases or type(row.get("IsDeleted")) is not bool
                or row["Id"] in found):
            raise LifecycleReviewError("Foreign, duplicate or invalid lifecycle row")
        found[row["Id"]] = row["IsDeleted"]
    return found


def review(*, instance, confirm_host, namespace, cases, token, output,
           allow_loopback_http=False, opener=None):
    """One real read and a private, digest-bound case-state review, with no writes."""
    origin = _instance(instance, confirm_host, allow_loopback_http)
    scope = _scope_hash(origin, namespace, cases)
    if type(token) is not str or TOKEN.fullmatch(token) is None:
        raise LifecycleReviewError("Current read-only bearer required")
    if not isinstance(output, Path):
        raise LifecycleReviewError("New private review path required")
    _private_parent(output)
    if output.exists() or output.is_symlink():
        raise LifecycleReviewError("Review path already exists")
    transport = opener if opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())
    found = _read(transport, origin, token, cases)
    states = []
    for case in cases:
        state = ("soft_deleted" if found[case] else "active") if case in found else "unresolved"
        states.append({"case_id": _key(namespace, "case", case), "source_state": state})
    states.sort(key=lambda item: item["case_id"])
    counts = {name: sum(item["source_state"] == name for item in states)
              for name in ("active", "soft_deleted", "unresolved")}
    artifact = {
        "format": FORMAT, "scope_sha256": scope,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "selected_cases": len(cases), "case_states": states, "counts": counts,
        "source_completeness": "unknown", "missing_means_deleted": False,
        "hard_deletion_proven": False, "review_required": True,
        "owg_deletion_performed": False, "source_writes_performed": False,
        "checkpoint_advanced": False,
    }
    payload = _canonical(artifact)
    try:
        _new_private(output, payload)
    except (OSError, ValueError):
        raise LifecycleReviewError("Private review creation refused") from None
    return {
        "format": FORMAT, "status": "review_created",
        "selected_cases": len(cases), "counts": counts,
        "review_sha256": _digest(payload), "review_required": True,
        "source_completeness": "unknown", "checkpoint_advanced": False,
        "owg_deletion_performed": False, "source_writes_performed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--case", action="append", dest="cases", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--consent-source-lifecycle-read", action="store_true")
    parser.add_argument("--consent-private-review", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true",
                        help="Disposable local acceptance only")
    args = parser.parse_args(argv)
    try:
        if not (args.consent_source_lifecycle_read and args.consent_private_review):
            raise LifecycleReviewError("Explicit source read and review consents required")
        result = review(instance=args.instance, confirm_host=args.confirm_host,
                        namespace=args.namespace, cases=args.cases,
                        token=os.environ.get("OWG_SALESFORCE_READ_TOKEN"),
                        output=args.output, allow_loopback_http=args.allow_loopback_http)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (LifecycleReviewError, ValueError, TypeError, OSError, OverflowError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "reason": "Check current source grant, selected scope, consents and private new output",
            "checkpoint_advanced": False, "owg_deletion_performed": False,
            "source_writes_performed": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
