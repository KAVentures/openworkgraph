"""Explicitly reviewed, overlap-based Salesforce CaseHistory checkpoint workflow.

Prepare reads a bounded source window and creates a private review manifest. Only
an independently attested OWG import (or explicit empty-window review) advances
an exclusive local checkpoint. This is not a scheduler or verified import receipt.
"""
from __future__ import annotations

from server.connector_oauth import connector_token as _connector_token
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

from server.salesforce_case_history_live import (
    LiveHistoryError, _instance, _utc, extract, MAX_WINDOW, MAX_CASES,
)
from scripts.salesforce_case_history import ID

FORMAT = "openworkgraph.salesforce-incremental.v1"
MAX_MANIFEST = 8192
MAX_CSV = 2 * 1024 * 1024
MAX_OVERLAP_HOURS = 168
SHA = re.compile(r"[a-f0-9]{64}\Z")


class IncrementalError(ValueError):
    """Refusal never includes source IDs, paths, credentials or business values."""


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("utf-8")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _private_parent(path):
    if not isinstance(path, Path) or path.name in {"", ".", ".."}:
        raise IncrementalError("Private file path required")
    try:
        st = path.parent.lstat()
    except OSError:
        raise IncrementalError("Private directory required") from None
    if (not stat.S_ISDIR(st.st_mode)
            or (hasattr(os, "getuid") and st.st_uid != os.getuid())
            or (os.name == "posix" and st.st_mode & 0o077)):
        raise IncrementalError("Owner-only directory required")


def _paths(checkpoint, manifest, csv_path=None):
    paths = [checkpoint, manifest] + ([csv_path] if csv_path is not None else [])
    if len({str(p.absolute()) for p in paths}) != len(paths):
        raise IncrementalError("Distinct file paths required")
    for path in paths:
        _private_parent(path)
    if len({p.parent.resolve() for p in paths}) != 1:
        raise IncrementalError("One private directory required")


def _read_private(path, limit):
    _private_parent(path)
    if path.is_symlink():
        raise IncrementalError("Unsafe private symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            st = os.fstat(stream.fileno())
            if (not stat.S_ISREG(st.st_mode)
                    or (os.name == "posix" and st.st_mode & 0o077)
                    or st.st_size > limit):
                raise IncrementalError("Unsafe private file")
            raw = stream.read(limit + 1)
    except OSError:
        raise IncrementalError("Private file unavailable") from None
    if len(raw) > limit:
        raise IncrementalError("Private file exceeds limit")
    return raw


def _read_json(path, limit):
    raw = _read_private(path, limit)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise IncrementalError("Invalid private metadata") from None
    if type(value) is not dict or raw != _canonical(value):
        raise IncrementalError("Noncanonical private metadata")
    return value, _digest(raw)


def _new_private(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise IncrementalError("Private file creation refused") from None


def _scope_hash(origin, namespace, cases):
    if (type(namespace) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", namespace)
            or type(cases) is not list or not 1 <= len(cases) <= MAX_CASES
            or any(type(c) is not str or not ID.fullmatch(c) for c in cases)
            or len(set(cases)) != len(cases)):
        raise IncrementalError("Invalid selected source scope")
    return _digest(_canonical({"origin": origin, "namespace": namespace,
                               "cases": sorted(cases)}))


def _state(path):
    if not path.exists() and not path.is_symlink():
        return None, None
    state, sha = _read_json(path, MAX_MANIFEST)
    if (set(state) != {"format", "scope_sha256", "bootstrap_since", "until",
                       "overlap_hours", "manifest_sha256"}
            or state["format"] != FORMAT
            or type(state["scope_sha256"]) is not str
            or not SHA.fullmatch(state["scope_sha256"])
            or type(state["manifest_sha256"]) is not str
            or not SHA.fullmatch(state["manifest_sha256"])
            or type(state["overlap_hours"]) is not int
            or not 1 <= state["overlap_hours"] <= MAX_OVERLAP_HOURS):
        raise IncrementalError("Invalid checkpoint schema")
    if not _utc(state["bootstrap_since"]) < _utc(state["until"]):
        raise IncrementalError("Invalid checkpoint window")
    return state, sha


def prepare(*, instance, confirm_host, namespace, cases, bootstrap_since,
            until, overlap_hours, token, checkpoint, manifest, output,
            allow_loopback_http=False, opener=None, max_pages=1):
    """Live source GET; no checkpoint mutation. Returns a counts-only review receipt."""
    _paths(checkpoint, manifest, output)
    if (manifest.exists() or manifest.is_symlink() or output.exists()
            or output.is_symlink()):
        raise IncrementalError("Review output already exists")
    origin = _instance(instance, confirm_host, allow_loopback_http)
    scope = _scope_hash(origin, namespace, cases)
    if type(overlap_hours) is not int or not 1 <= overlap_hours <= MAX_OVERLAP_HOURS:
        raise IncrementalError("Overlap must be 1–168 hours")
    end = _utc(until)
    state, previous_sha = _state(checkpoint)
    if state is None:
        if bootstrap_since is None:
            raise IncrementalError("Explicit bootstrap start required")
        start = _utc(bootstrap_since)
        bootstrap = bootstrap_since
        previous_until = None
    else:
        if (state["scope_sha256"] != scope or state["overlap_hours"] != overlap_hours
                or (bootstrap_since is not None
                    and bootstrap_since != state["bootstrap_since"])):
            raise IncrementalError("Checkpoint scope mismatch")
        bootstrap = state["bootstrap_since"]
        previous_until = state["until"]
        if end <= _utc(previous_until):
            raise IncrementalError("New upper bound must advance")
        start = max(_utc(bootstrap), _utc(previous_until) - timedelta(hours=overlap_hours))
    if not start < end <= start + MAX_WINDOW:
        raise IncrementalError("Incremental window exceeds source limit")
    since = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = extract(instance, confirm_host, namespace, cases, since, until,
                     token, output, allow_loopback_http=allow_loopback_http,
                     opener=opener, max_pages=max_pages)
    created = result["csv_created"]
    csv_sha = _digest(_read_private(output, MAX_CSV)) if created else None
    record = {
        "format": FORMAT, "scope_sha256": scope, "bootstrap_since": bootstrap,
        "since": since, "until": until, "previous_until": previous_until,
        "overlap_hours": overlap_hours, "expected_checkpoint_sha256": previous_sha,
        "csv_name": output.name if created else None, "csv_sha256": csv_sha,
        "events": result.get("events", 0), "source_rows": result["source_rows"],
        "status": result["status"],
    }
    payload = _canonical(record)
    _new_private(manifest, payload)
    return {"format": FORMAT, "status": "prepared", "manifest_sha256": _digest(payload),
            "scope_sha256": scope, "events": record["events"],
            "source_rows": record["source_rows"], "csv_created": created,
            "pages_fetched": result["pages_fetched"],
            "checkpoint_advanced": False, "source_completeness": "unknown",
            "provider_writes_performed": False}


def commit(*, checkpoint, manifest, confirm_manifest_sha256,
           confirm_imported=False, confirm_empty_window=False):
    """Advance only after explicit operator attestation, with exclusive CAS lock."""
    _paths(checkpoint, manifest)
    if (type(confirm_manifest_sha256) is not str
            or not SHA.fullmatch(confirm_manifest_sha256)):
        raise IncrementalError("Review digest confirmation required")
    record, manifest_sha = _read_json(manifest, MAX_MANIFEST)
    if manifest_sha != confirm_manifest_sha256:
        raise IncrementalError("Review manifest changed")
    expected = {"format", "scope_sha256", "bootstrap_since", "since", "until",
                "previous_until", "overlap_hours", "expected_checkpoint_sha256",
                "csv_name", "csv_sha256", "events", "source_rows", "status"}
    if (set(record) != expected or record["format"] != FORMAT
            or type(record["scope_sha256"]) is not str
            or not SHA.fullmatch(record["scope_sha256"])
            or type(record["overlap_hours"]) is not int
            or not 1 <= record["overlap_hours"] <= MAX_OVERLAP_HOURS
            or type(record["events"]) is not int or record["events"] < 0
            or type(record["source_rows"]) is not int or record["source_rows"] < 0
            or not _utc(record["bootstrap_since"]) <= _utc(record["since"])
            or not _utc(record["since"]) < _utc(record["until"])
            or _utc(record["until"]) > _utc(record["since"]) + MAX_WINDOW):
        raise IncrementalError("Invalid review manifest")
    if record["previous_until"] is not None:
        if (_utc(record["previous_until"]) >= _utc(record["until"])
                or record["expected_checkpoint_sha256"] is None):
            raise IncrementalError("Invalid prior checkpoint reference")
    elif record["expected_checkpoint_sha256"] is not None:
        raise IncrementalError("Invalid bootstrap reference")
    if record["status"] == "exported":
        if (confirm_imported is not True or confirm_empty_window is True
                or type(record["csv_name"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.csv", record["csv_name"])
                or type(record["csv_sha256"]) is not str
                or not SHA.fullmatch(record["csv_sha256"])
                or record["events"] < 1):
            raise IncrementalError("Explicit successful import confirmation required")
        csv_path = manifest.parent / record["csv_name"]
        if csv_path == checkpoint or csv_path == manifest:
            raise IncrementalError("Invalid review CSV path")
        if _digest(_read_private(csv_path, MAX_CSV)) != record["csv_sha256"]:
            raise IncrementalError("Reviewed CSV changed")
    elif record["status"] == "no_selected_history":
        if (confirm_empty_window is not True or confirm_imported is True
                or record["csv_name"] is not None or record["csv_sha256"] is not None
                or record["events"] != 0 or record["source_rows"] != 0):
            raise IncrementalError("Explicit empty-window confirmation required")
    else:
        raise IncrementalError("Invalid review status")
    lock = checkpoint.with_name(checkpoint.name + ".lock")
    _private_parent(lock)
    _new_private(lock, b"locked\n")
    temporary = None
    try:
        current, current_sha = _state(checkpoint)
        if current_sha != record["expected_checkpoint_sha256"]:
            raise IncrementalError("Stale checkpoint: review again")
        if current is not None:
            if (current["scope_sha256"] != record["scope_sha256"]
                    or current["until"] != record["previous_until"]
                    or current["bootstrap_since"] != record["bootstrap_since"]
                    or current["overlap_hours"] != record["overlap_hours"]):
                raise IncrementalError("Checkpoint scope changed")
        elif record["previous_until"] is not None:
            raise IncrementalError("Missing prior checkpoint")
        new_state = {"format": FORMAT, "scope_sha256": record["scope_sha256"],
                     "bootstrap_since": record["bootstrap_since"],
                     "until": record["until"], "overlap_hours": record["overlap_hours"],
                     "manifest_sha256": manifest_sha}
        temporary = checkpoint.with_name(checkpoint.name + ".tmp-" + secrets.token_hex(12))
        _new_private(temporary, _canonical(new_state))
        os.replace(temporary, checkpoint)
        temporary = None
        if os.name == "posix":
            fd = os.open(checkpoint.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    return {"format": FORMAT, "status": "checkpoint_advanced",
            "checkpoint_advanced": True, "operator_attested_import": confirm_imported,
            "operator_reviewed_empty": confirm_empty_window,
            "source_completeness": "unknown", "provider_writes_performed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--instance", required=True)
    p.add_argument("--confirm-host", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--case", action="append", dest="cases", required=True)
    p.add_argument("--bootstrap-since")
    p.add_argument("--until", required=True)
    p.add_argument("--overlap-hours", type=int, default=24)
    p.add_argument("--max-pages", type=int, default=1)
    p.add_argument("--checkpoint", required=True, type=Path)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--consent-selected-read", action="store_true")
    p.add_argument("--consent-private-export", action="store_true")
    p.add_argument("--allow-loopback-http", action="store_true", help="Local tests only")
    c = sub.add_parser("commit")
    c.add_argument("--checkpoint", required=True, type=Path)
    c.add_argument("--manifest", required=True, type=Path)
    c.add_argument("--confirm-manifest-sha256", required=True)
    c.add_argument("--confirm-imported", action="store_true")
    c.add_argument("--confirm-empty-window", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            if not args.consent_selected_read or not args.consent_private_export:
                raise IncrementalError("Explicit read and export consent required")
            result = prepare(instance=args.instance, confirm_host=args.confirm_host,
                             namespace=args.namespace, cases=args.cases,
                             bootstrap_since=args.bootstrap_since, until=args.until,
                             overlap_hours=args.overlap_hours,
                             token=_connector_token("OWG_SALESFORCE_READ_TOKEN"),
                             checkpoint=args.checkpoint, manifest=args.manifest,
                             output=args.output, allow_loopback_http=args.allow_loopback_http,
                             max_pages=args.max_pages)
        else:
            result = commit(checkpoint=args.checkpoint, manifest=args.manifest,
                            confirm_manifest_sha256=args.confirm_manifest_sha256,
                            confirm_imported=args.confirm_imported,
                            confirm_empty_window=args.confirm_empty_window)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (IncrementalError, LiveHistoryError, OSError, TypeError, ValueError, OverflowError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "reason": "Check private scope, source consent, review digest and checkpoint",
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
