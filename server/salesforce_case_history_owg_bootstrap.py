"""Operator-reviewed first Salesforce CaseHistory import into a new local OWG log.

Uses the production OWG HTTP import route and independently reads back the
new log before advancing the source checkpoint. No Salesforce network access.
A failed/uncertain import may have created a log: never retry automatically.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request

from .salesforce_case_history_incremental import (
    IncrementalError, _new_private, _state, commit,
)
from .salesforce_case_history_owg_apply import _review, ApplyError
from .servicenow_owg_append import (
    _LOG, _opener, _pairs_unique, _session, AppendError, MAX_RESPONSE_BYTES,
)

FORMAT = "openworkgraph.salesforce-reviewed-owg-bootstrap.v1"
LOG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}\Z", re.ASCII)


class BootstrapError(ValueError):
    """Non-sensitive failure, without identifiers, paths or credentials."""


class UncertainBootstrap(BootstrapError):
    """A new local log may exist, but checkpoint was not advanced."""


def _json_response(opener, req):
    """One bounded no-proxy/no-redirect local HTTP request."""
    try:
        with opener.open(req, timeout=15) as response:
            status, final_url = response.status, response.geturl()
            media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, error.URLError, ValueError):
        raise BootstrapError("Local OWG response unavailable") from None
    if (status != 200 or final_url != req.full_url or media != "application/json"
            or len(raw) > MAX_RESPONSE_BYTES):
        raise BootstrapError("Local OWG response refused")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique)
    except (ValueError, UnicodeError, TypeError, RecursionError, AppendError):
        raise BootstrapError("Invalid local OWG response") from None
    if type(data) is not dict:
        raise BootstrapError("Invalid local OWG response")
    return data


def _logs(port, session, *, opener):
    url = f"http://127.0.0.1:{port}/v1/event-logs"
    req = request.Request(url, method="GET", headers={
        "Authorization": "OWG-Session " + session,
        "Accept": "application/json", "Cache-Control": "no-store",
    })
    data = _json_response(opener, req)
    logs = data.get("logs")
    if type(logs) is not list or any(type(item) is not dict for item in logs):
        raise BootstrapError("Invalid local log inventory")
    ids = [item.get("log_id") for item in logs]
    if any(type(item) is not str or not _LOG.fullmatch(item) for item in ids):
        raise BootstrapError("Invalid local log identity")
    if len(ids) != len(set(ids)):
        raise BootstrapError("Ambiguous local log inventory")
    return logs


def _scope_matches(item, namespace):
    identity = item.get("source_identity")
    # Ordinary unrelated CSV/XES logs may have no declared source identity.
    if identity is None:
        return False
    namespaces = identity.get("namespaces") if type(identity) is dict else None
    if type(namespaces) is not list:
        raise BootstrapError("Unverifiable local source identity")
    return any(type(ns) is dict and
               (ns.get("source_namespace") == namespace or
                ns.get("case_namespace") == namespace)
               for ns in namespaces)


def _verify_new(logs, log_id, namespace, expected, previous_ids):
    if log_id in previous_ids:
        raise BootstrapError("Local log ID existed before import")
    matching = [item for item in logs if item.get("log_id") == log_id]
    if len(matching) != 1:
        raise BootstrapError("New local log not independently readable")
    item = matching[0]
    identity = item.get("source_identity")
    namespaces = identity.get("namespaces") if type(identity) is dict else None
    if (type(namespaces) is not list or len(namespaces) != 1
            or type(namespaces[0]) is not dict
            or namespaces[0].get("source_namespace") != namespace
            or namespaces[0].get("case_namespace") != namespace
            or item.get("source_format") != "csv"
            or type(item.get("events")) is not int or item["events"] != expected
            or type(item.get("rejected_events")) is not int
            or item["rejected_events"] != 0):
        raise BootstrapError("New local log counts or scope did not verify")
    return item


def _import(port, session, name, namespace, body, expected, *, opener):
    query = parse.urlencode({
        "format": "csv", "name": name, "case_column": "case_id",
        "activity_column": "activity", "timestamp_column": "timestamp",
        "event_id_column": "source_event_id", "source_namespace": namespace,
        "case_namespace": namespace,
    })
    url = f"http://127.0.0.1:{port}/v1/event-logs/import?{query}"
    req = request.Request(url, data=body, method="POST", headers={
        "Authorization": "OWG-Session " + session,
        "X-OWG-Local-Storage-Confirmed": "true",
        "Content-Type": "text/csv; charset=utf-8",
        "Accept": "application/json", "Cache-Control": "no-store",
    })
    result = _json_response(opener, req)
    log_id = result.get("log_id")
    if (type(log_id) is not str or not _LOG.fullmatch(log_id)
            or type(result.get("events")) is not int
            or result["events"] != expected
            or type(result.get("rejected_events")) is not int
            or result["rejected_events"] != 0):
        raise BootstrapError("Local import receipt did not verify selected events")
    return log_id


def bootstrap(*, checkpoint: Path, manifest: Path, confirm_manifest_sha256: str,
              log_name: str, confirm_log_name: str, port: int,
              consent_prepared_read=False, consent_local_import=False,
              consent_checkpoint_advance=False, session=None, opener=None):
    """Create one reviewed log; verify exact import before checkpoint CAS."""
    if not (consent_prepared_read is True and consent_local_import is True
            and consent_checkpoint_advance is True):
        raise BootstrapError("Three separate explicit consents required")
    if (type(log_name) is not str or not LOG_NAME.fullmatch(log_name)
            or confirm_log_name != log_name or type(port) is not int
            or not 1 <= port <= 65535):
        raise BootstrapError("Exact new log name and local port confirmation required")
    try:
        session = _session(session)
        record, body, namespace = _review(checkpoint, manifest, confirm_manifest_sha256)
        if (record["previous_until"] is not None
                or record["expected_checkpoint_sha256"] is not None
                or _state(checkpoint)[1] is not None):
            raise BootstrapError("Only an uncommitted first window may bootstrap")
    except (AppendError, ApplyError, IncrementalError):
        raise BootstrapError("Prepared review or session refused") from None
    transport = _opener() if opener is None else opener
    lock = checkpoint.with_name(checkpoint.name + ".bootstrap.lock")
    try:
        _new_private(lock, b"bootstrap in progress\n")
    except IncrementalError:
        raise BootstrapError("Another bootstrap requires review") from None
    try:
        if _state(checkpoint)[1] is not None:
            raise BootstrapError("Checkpoint advanced during bootstrap")
        before = _logs(port, session, opener=transport)
        if any(_scope_matches(item, namespace) for item in before):
            raise BootstrapError("Selected source already has a local log; use reviewed apply")
        previous_ids = {item["log_id"] for item in before}
        # From here on, an error is an uncertain local write, not a safe retry.
        try:
            log_id = _import(port, session, log_name, namespace, body,
                             record["events"], opener=transport)
            after = _logs(port, session, opener=transport)
            _verify_new(after, log_id, namespace, record["events"], previous_ids)
            committed = commit(
                checkpoint=checkpoint, manifest=manifest,
                confirm_manifest_sha256=confirm_manifest_sha256,
                confirm_imported=True,
            )
        except (BootstrapError, IncrementalError, ApplyError, AppendError, OSError, ValueError):
            raise UncertainBootstrap("Inspect local logs and checkpoint; use reviewed apply to recover") from None
        return {
            "format": FORMAT, "status": "bootstrapped",
            "log_id": log_id, "events": record["events"],
            "source_rows": record["source_rows"], "rejected_events": 0,
            "owg_import_readback_verified": True,
            "checkpoint_advanced": committed["checkpoint_advanced"],
            "provider_writes_performed": False,
            "source_completeness": "unknown",
        }
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--confirm-manifest-sha256", required=True)
    parser.add_argument("--log-name", required=True)
    parser.add_argument("--confirm-log-name", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-prepared-read", action="store_true")
    parser.add_argument("--consent-local-import", action="store_true")
    parser.add_argument("--consent-checkpoint-advance", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = bootstrap(
            checkpoint=args.checkpoint, manifest=args.manifest,
            confirm_manifest_sha256=args.confirm_manifest_sha256,
            log_name=args.log_name, confirm_log_name=args.confirm_log_name,
            port=args.port, consent_prepared_read=args.consent_prepared_read,
            consent_local_import=args.consent_local_import,
            consent_checkpoint_advance=args.consent_checkpoint_advance,
            session=os.environ.get("OWG_DASHBOARD_SESSION"),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainBootstrap:
        print(json.dumps({
            "format": FORMAT, "status": "unverified",
            "possible_local_import": True, "checkpoint_advanced": False,
            "reason": "Inspect local logs and checkpoint before reviewed recovery",
        }, sort_keys=True))
        return 3
    except (BootstrapError, IncrementalError, ApplyError, AppendError, OSError, ValueError, TypeError):
        print(json.dumps({
            "format": FORMAT, "status": "refused",
            "possible_local_import": False, "checkpoint_advanced": False,
            "reason": "Check review, session, exact name, consents and local source scope",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
