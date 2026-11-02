"""Read-only Salesforce Case lifecycle audit for a selected OWG source scope.

This is a review artifact, never a deletion instruction or proof of hard deletion.
An absent Case row is unresolved (including permission and retention ambiguity).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sys
from urllib import error, parse, request

from scripts.salesforce_case_history import _key
from .servicenow_owg_append import (
    _LOG, _opener, _session, verify_local_target, AppendError,
)
from server.salesforce_case_history_live import (
    _instance, _NoRedirect, _unique, _nonfinite, TOKEN,
)
from server.salesforce_case_history_incremental import (
    _scope_hash, _private_parent, _new_private, _canonical, _digest,
    _read_private,
)

FORMAT = "openworkgraph.salesforce-case-lifecycle-review.v1"
MAX_BYTES = 65536
MAX_PAGES = 8
STATES = ("active", "soft_deleted", "unresolved")
BASE_KEYS = frozenset({
    "format", "scope_sha256", "checked_at_utc", "selected_cases",
    "pages_fetched", "case_states", "counts", "source_completeness",
    "missing_means_deleted", "hard_deletion_proven", "review_required",
    "owg_deletion_performed", "source_writes_performed", "checkpoint_advanced",
})


class LifecycleReviewError(ValueError):
    """Public errors never include provider identities or secrets."""


def _query(cases):
    selected = ",".join("'" + case + "'" for case in sorted(cases))
    return ("SELECT Id, IsDeleted FROM Case WHERE Id IN (" + selected +
            ") ORDER BY Id LIMIT 9")


def _read(opener, origin, bearer, cases, *, max_pages=1):
    """Read a complete bounded queryAll cursor chain, never a partial selection."""
    url = origin + "/services/data/v60.0/queryAll?" + parse.urlencode(
        {"q": _query(cases)})
    seen_urls = set()
    found = {}
    expected_total = None
    last_id = None
    for page in range(1, max_pages + 1):
        if url in seen_urls:
            raise LifecycleReviewError("Repeated lifecycle cursor")
        seen_urls.add(url)
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
        if (type(data) is not dict or type(data.get("done")) is not bool
                or type(data.get("totalSize")) is not int
                or not 0 <= data["totalSize"] <= len(cases)
                or type(data.get("records")) is not list
                or len(found) + len(data["records"]) > len(cases)):
            raise LifecycleReviewError("Incomplete lifecycle selection")
        if expected_total is None:
            expected_total = data["totalSize"]
        elif expected_total != data["totalSize"]:
            raise LifecycleReviewError("Lifecycle result count changed between pages")
        if len(found) + len(data["records"]) > expected_total:
            raise LifecycleReviewError("Excess lifecycle rows")
        for row in data["records"]:
            if (type(row) is not dict or type(row.get("Id")) is not str
                    or row["Id"] not in cases or type(row.get("IsDeleted")) is not bool
                    or row["Id"] in found or (last_id is not None and row["Id"] <= last_id)):
                raise LifecycleReviewError("Foreign, duplicate or unordered lifecycle row")
            last_id = row["Id"]
            found[row["Id"]] = row["IsDeleted"]
        if data["done"]:
            if "nextRecordsUrl" in data or len(found) != expected_total:
                raise LifecycleReviewError("Incomplete lifecycle final page")
            return found, page
        cursor = data.get("nextRecordsUrl")
        # Salesforce queryAll may return a query or queryAll locator; neither
        # permits a caller-supplied URL, query, fragment or encoded traversal.
        if (type(cursor) is not str
                or re.fullmatch(
                    r"/services/data/v60\.0/(?:query|queryAll)/[A-Za-z0-9_-]{1,200}",
                    cursor) is None
                or not data["records"] or page == max_pages):
            raise LifecycleReviewError("Unfinished or unsafe lifecycle cursor")
        url = origin + cursor
    raise LifecycleReviewError("Lifecycle page limit reached")



def _previous(path, expected_sha256, scope, namespace, cases):
    """Read a pinned, owner-private earlier review before any source request.

    A digest is an operator-supplied integrity pin, not a signature or identity
    assertion. Never infer deletion from a prior/current missing row.
    """
    if (not isinstance(path, Path) or type(expected_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None):
        raise LifecycleReviewError("Previous review and exact digest required")
    try:
        raw = _read_private(path, MAX_BYTES)
        prior = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_nonfinite)
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        raise LifecycleReviewError("Invalid private previous review") from None
    if (type(prior) is not dict or raw != _canonical(prior)
            or _digest(raw) != expected_sha256
            or set(prior) not in (BASE_KEYS, BASE_KEYS | {"comparison"})
            or prior.get("format") != FORMAT or prior.get("scope_sha256") != scope
            or type(prior.get("selected_cases")) is not int
            or prior["selected_cases"] != len(cases)
            or type(prior.get("pages_fetched")) is not int
            or not 1 <= prior["pages_fetched"] <= MAX_PAGES
            or prior.get("source_completeness") != "unknown"
            or any(prior.get(flag) is not expected for flag, expected in (
                ("missing_means_deleted", False), ("hard_deletion_proven", False),
                ("review_required", True), ("owg_deletion_performed", False),
                ("source_writes_performed", False), ("checkpoint_advanced", False),
            ))):
        raise LifecycleReviewError("Previous review scope or schema mismatch")
    try:
        stamp = prior["checked_at_utc"]
        if (type(stamp) is not str
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp) is None):
            raise ValueError("invalid timestamp")
        datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
        rows = prior["case_states"]
        expected_ids = sorted(_key(namespace, "case", case) for case in cases)
        if (type(rows) is not list or len(rows) != len(cases)
                or [row["case_id"] for row in rows] != expected_ids
                or any(type(row) is not dict or set(row) != {"case_id", "source_state"}
                       or type(row["source_state"]) is not str
                       or row["source_state"] not in STATES for row in rows)):
            raise ValueError("invalid prior rows")
        counts = {name: sum(row["source_state"] == name for row in rows)
                  for name in STATES}
        if prior["counts"] != counts or type(prior["counts"]) is not dict:
            raise ValueError("invalid prior counts")
        if "comparison" in prior:
            comparison = prior["comparison"]
            if (type(comparison) is not dict or
                    set(comparison) != {"previous_review_sha256", "previous_checked_at_utc",
                                        "transitions", "changed", "unchanged"}
                    or type(comparison["previous_review_sha256"]) is not str
                    or re.fullmatch(r"[a-f0-9]{64}", comparison["previous_review_sha256"]) is None
                    or type(comparison["previous_checked_at_utc"]) is not str
                    or type(comparison["changed"]) is not int
                    or type(comparison["unchanged"]) is not int
                    or type(comparison["transitions"]) is not list
                    or len(comparison["transitions"]) != len(cases)):
                raise ValueError("invalid prior comparison")
            transitions = comparison["transitions"]
            if (any(type(row) is not dict
                    or set(row) != {"case_id", "previous_state", "current_state"}
                    or row["case_id"] != rows[i]["case_id"]
                    or row["current_state"] != rows[i]["source_state"]
                    or row["previous_state"] not in STATES
                    for i, row in enumerate(transitions))
                    or comparison["changed"] != sum(
                        row["previous_state"] != row["current_state"] for row in transitions)
                    or comparison["unchanged"] != sum(
                        row["previous_state"] == row["current_state"] for row in transitions)):
                raise ValueError("invalid prior transitions")
    except (KeyError, ValueError, TypeError, IndexError):
        raise LifecycleReviewError("Previous review validation refused") from None
    return prior


def review(*, instance, confirm_host, namespace, cases, token, output,
           allow_loopback_http=False, opener=None, max_pages=1,
           previous_review=None, previous_sha256=None,
           consent_compare_previous=False):
    """Bounded real read and a private, digest-bound case-state review, with no writes."""
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise LifecycleReviewError("Explicit page bound must be 1–8")
    origin = _instance(instance, confirm_host, allow_loopback_http)
    scope = _scope_hash(origin, namespace, cases)
    if type(token) is not str or TOKEN.fullmatch(token) is None:
        raise LifecycleReviewError("Current read-only bearer required")
    if not isinstance(output, Path):
        raise LifecycleReviewError("New private review path required")
    _private_parent(output)
    if output.exists() or output.is_symlink():
        raise LifecycleReviewError("Review path already exists")
    if previous_review is None:
        if previous_sha256 is not None or consent_compare_previous:
            raise LifecycleReviewError("Previous review and consent must agree")
        prior = None
    else:
        if consent_compare_previous is not True:
            raise LifecycleReviewError("Separate previous-review comparison consent required")
        prior = _previous(previous_review, previous_sha256, scope, namespace, cases)
    transport = opener if opener is not None else request.build_opener(
        request.ProxyHandler({}), _NoRedirect())
    found, pages = _read(transport, origin, token, cases, max_pages=max_pages)
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
        "selected_cases": len(cases), "pages_fetched": pages,
        "case_states": states, "counts": counts,
        "source_completeness": "unknown", "missing_means_deleted": False,
        "hard_deletion_proven": False, "review_required": True,
        "owg_deletion_performed": False, "source_writes_performed": False,
        "checkpoint_advanced": False,
    }
    if prior is not None:
        if prior["checked_at_utc"] > artifact["checked_at_utc"]:
            raise LifecycleReviewError("Previous review is newer than current read")
        old = {row["case_id"]: row["source_state"] for row in prior["case_states"]}
        transitions = [
            {"case_id": row["case_id"], "previous_state": old[row["case_id"]],
             "current_state": row["source_state"]}
            for row in states
        ]
        changed = sum(row["previous_state"] != row["current_state"]
                      for row in transitions)
        artifact["comparison"] = {
            "previous_review_sha256": previous_sha256,
            "previous_checked_at_utc": prior["checked_at_utc"],
            "transitions": transitions, "changed": changed,
            "unchanged": len(transitions) - changed,
        }
    payload = _canonical(artifact)
    try:
        _new_private(output, payload)
    except (OSError, ValueError):
        raise LifecycleReviewError("Private review creation refused") from None
    receipt = {
        "format": FORMAT, "status": "review_created",
        "selected_cases": len(cases), "pages_fetched": pages, "counts": counts,
        "review_sha256": _digest(payload), "review_required": True,
        "source_completeness": "unknown", "checkpoint_advanced": False,
        "owg_deletion_performed": False, "source_writes_performed": False,
    }
    if prior is not None:
        receipt["comparison"] = {
            "changed": artifact["comparison"]["changed"],
            "unchanged": artifact["comparison"]["unchanged"],
            "previous_review_sha256": previous_sha256,
        }
    return receipt



# Optional second phase: compare a pinned source lifecycle observation with
# actual retained evidence in one explicitly selected, local OWG source log.
IMPACT_FORMAT = "openworkgraph.salesforce-case-owg-impact.v1"
MAX_OWG_CASES = 100


def _local_case_evidence(*, log_id, port, session, case_key, opener):
    """Read a bounded source_case page; never export raw activities/resources.

    Starting just before the selected key makes this a targeted seek without
    exposing all retained cases or fetching individual event payloads.
    """
    url = (f"http://127.0.0.1:{port}/v1/event-logs/{log_id}/cases/query"
           "?case_type=source_case")
    body = _canonical({"after": case_key[:-1], "limit": MAX_OWG_CASES})
    req = request.Request(url, data=body, method="POST", headers={
        "Authorization": "OWG-Session " + session,
        "Content-Type": "application/json", "Accept": "application/json",
        "Cache-Control": "no-store",
    })
    try:
        with opener.open(req, timeout=15) as response:
            status = response.status
            final_url = response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            raw = response.read(MAX_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise LifecycleReviewError("Local OWG case evidence read refused") from None
    if status != 200 or final_url != url or media != "application/json" or len(raw) > MAX_BYTES:
        raise LifecycleReviewError("Incomplete local OWG case evidence response")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=_nonfinite)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise LifecycleReviewError("Invalid local OWG case evidence") from None
    if (type(data) is not dict or type(data.get("selected_cases")) is not int
            or data["selected_cases"] < 0
            or type(data.get("cases")) is not list
            or len(data["cases"]) > MAX_OWG_CASES
            or len(data["cases"]) > data["selected_cases"]
            or (data.get("next_after") is not None and not data["cases"])
            or data.get("case_identity") !=
            "literal supplied key within this imported log and object type"
            or data.get("next_after") is not None
            and type(data["next_after"]) is not str):
        raise LifecycleReviewError("Unverifiable local OWG case page")
    last = case_key[:-1]
    observed = None
    for row in data["cases"]:
        if (type(row) is not dict or type(row.get("case_id")) is not str
                or row["case_id"] <= last or type(row.get("events")) is not int
                or row["events"] < 1):
            raise LifecycleReviewError("Invalid local OWG case row")
        last = row["case_id"]
        if last == case_key:
            observed = row["events"]
    if data["next_after"] is not None and data["next_after"] != last:
        raise LifecycleReviewError("Inconsistent local OWG case cursor")
    # A page that stops before the selected key cannot establish its absence.
    if observed is not None:
        return "observed", observed
    if data["next_after"] is not None and last < case_key:
        return "unknown", None
    return "not_observed", None


def impact(*, instance, confirm_host, namespace, cases, previous_review,
           previous_sha256, output, log_id, confirm_log_id, port, session,
           consent_local_owg_read=False, consent_private_impact=False,
           allow_loopback_http=False, opener=None):
    """Bounded authenticated OWG evidence impact of a pinned source review.

    No Salesforce request, provider/local writes, deletion or checkpoint change.
    """
    if consent_local_owg_read is not True or consent_private_impact is not True:
        raise LifecycleReviewError("Separate local evidence and private review consents required")
    origin = _instance(instance, confirm_host, allow_loopback_http)
    scope = _scope_hash(origin, namespace, cases)
    prior = _previous(previous_review, previous_sha256, scope, namespace, cases)
    if (type(log_id) is not str or _LOG.fullmatch(log_id) is None
            or confirm_log_id != log_id or type(port) is not int
            or not 1 <= port <= 65535):
        raise LifecycleReviewError("Exact local OWG log and port confirmation required")
    try:
        session = _session(session)
    except AppendError:
        raise LifecycleReviewError("Current local dashboard session required") from None
    if not isinstance(output, Path):
        raise LifecycleReviewError("New private impact review path required")
    _private_parent(output)
    if output.exists() or output.is_symlink():
        raise LifecycleReviewError("Impact review path already exists")
    transport = _opener() if opener is None else opener
    source_namespace = "sf_" + scope[:32]
    try:
        verify_local_target(log_id, port, source_namespace, session, opener=transport)
    except AppendError:
        raise LifecycleReviewError("Selected OWG log source scope or authorization refused") from None
    rows = []
    for state in prior["case_states"]:
        key = "src:" + source_namespace + ":" + state["case_id"]
        presence, count = _local_case_evidence(
            log_id=log_id, port=port, session=session, case_key=key,
            opener=transport)
        rows.append({
            "case_id": state["case_id"], "source_state": state["source_state"],
            "owg_evidence": presence, "owg_events": count,
        })
    summary = {
        "source_soft_deleted_with_owg_evidence": sum(
            row["source_state"] == "soft_deleted" and row["owg_evidence"] == "observed"
            for row in rows),
        "source_unresolved_with_owg_evidence": sum(
            row["source_state"] == "unresolved" and row["owg_evidence"] == "observed"
            for row in rows),
        "owg_cases_observed": sum(row["owg_evidence"] == "observed" for row in rows),
        "owg_cases_not_observed": sum(row["owg_evidence"] == "not_observed" for row in rows),
        "owg_cases_unknown": sum(row["owg_evidence"] == "unknown" for row in rows),
    }
    artifact = {
        "format": IMPACT_FORMAT, "scope_sha256": scope,
        "source_review_sha256": previous_sha256,
        "source_checked_at_utc": prior["checked_at_utc"],
        "owg_checked_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
        "owg_log_id": log_id, "selected_cases": len(rows),
        "case_impacts": rows, "counts": summary,
        "source_completeness": "unknown",
        "owg_historical_completeness": "unknown",
        "missing_means_deleted": False,
        "deletion_or_retention_authorized": False,
        "owg_deletion_performed": False, "source_writes_performed": False,
        "checkpoint_advanced": False, "review_required": True,
    }
    payload = _canonical(artifact)
    try:
        _new_private(output, payload)
    except (OSError, ValueError):
        raise LifecycleReviewError("Private impact review creation refused") from None
    return {
        "format": IMPACT_FORMAT, "status": "impact_review_created",
        "selected_cases": len(rows), "counts": summary,
        "impact_sha256": _digest(payload),
        "review_required": True, "deletion_or_retention_authorized": False,
        "owg_deletion_performed": False, "source_writes_performed": False,
        "checkpoint_advanced": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--confirm-host", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--case", action="append", dest="cases", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--previous-review", type=Path)
    parser.add_argument("--impact-review", type=Path,
                        help="Pinned lifecycle review for local OWG evidence impact mode")
    parser.add_argument("--impact-sha256")
    parser.add_argument("--owg-log-id")
    parser.add_argument("--confirm-owg-log-id")
    parser.add_argument("--owg-port", type=int, default=8765)
    parser.add_argument("--consent-local-owg-read", action="store_true")
    parser.add_argument("--consent-private-impact", action="store_true")
    parser.add_argument("--previous-sha256")
    parser.add_argument("--consent-compare-previous-review", action="store_true")
    parser.add_argument("--consent-source-lifecycle-read", action="store_true")
    parser.add_argument("--consent-private-review", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true",
                        help="Disposable local acceptance only")
    args = parser.parse_args(argv)
    try:
        if args.impact_review is not None:
            if (args.previous_review is not None or args.previous_sha256 is not None
                    or args.consent_compare_previous_review
                    or args.consent_source_lifecycle_read or args.consent_private_review):
                raise LifecycleReviewError("Source-read and local impact modes must remain separate")
            result = impact(
                instance=args.instance, confirm_host=args.confirm_host,
                namespace=args.namespace, cases=args.cases,
                previous_review=args.impact_review,
                previous_sha256=args.impact_sha256, output=args.output,
                log_id=args.owg_log_id, confirm_log_id=args.confirm_owg_log_id,
                port=args.owg_port, session=os.environ.get("OWG_DASHBOARD_SESSION"),
                consent_local_owg_read=args.consent_local_owg_read,
                consent_private_impact=args.consent_private_impact,
                allow_loopback_http=args.allow_loopback_http)
            print(json.dumps(result, sort_keys=True))
            return 0
        if (args.impact_sha256 is not None or args.owg_log_id is not None
                or args.confirm_owg_log_id is not None
                or args.consent_local_owg_read or args.consent_private_impact):
            raise LifecycleReviewError("Local impact flags require a pinned impact review")
        if not (args.consent_source_lifecycle_read and args.consent_private_review):
            raise LifecycleReviewError("Explicit source read and review consents required")
        result = review(instance=args.instance, confirm_host=args.confirm_host,
                        namespace=args.namespace, cases=args.cases,
                        token=os.environ.get("OWG_SALESFORCE_READ_TOKEN"),
                        output=args.output, allow_loopback_http=args.allow_loopback_http,
                        max_pages=args.max_pages,
                        previous_review=args.previous_review,
                        previous_sha256=args.previous_sha256,
                        consent_compare_previous=args.consent_compare_previous_review)
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
