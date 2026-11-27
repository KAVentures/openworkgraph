"""Counts-only durable journal for admitted retained enterprise ingestion runs.

Writes share the caller's profile/checkpoint transaction. Provider messages,
credentials, source identifiers, response bodies and event payloads never enter
this table. A terminated local intent does not prove remote cancellation.
"""
from collections import Counter
from datetime import datetime, timezone
import math
import re
import time

FORMAT = "owg_enterprise_ingestion_run_history_v1"
MAX_PER_PROFILE = 200
MAX_QUERY_ROWS = 1000
MAX_LOOKBACK_DAYS = 90
STATES = frozenset({"running", "succeeded", "failed", "interrupted", "invalidated"})
PROFILE = re.compile(r"rest_[a-f0-9]{24}\Z")
ERROR = re.compile(r"[a-z][a-z0-9_]{0,100}\Z")
COUNT_FIELDS = ("events_added", "duplicates_skipped", "distinct_events_in_window",
                "records_received", "pages", "query_bytes_billed")
SCHEMA = """CREATE TABLE IF NOT EXISTS enterprise_ingestion_runs (
    run_id VARCHAR PRIMARY KEY, profile_id VARCHAR NOT NULL, provider VARCHAR NOT NULL,
    revision BIGINT NOT NULL, admitted_at DOUBLE NOT NULL, lease_until DOUBLE NOT NULL,
    since_at VARCHAR NOT NULL, until_at VARCHAR NOT NULL, checkpoint_before VARCHAR,
    checkpoint_after VARCHAR, state VARCHAR NOT NULL, finished_at DOUBLE,
    error_code VARCHAR, retry_not_before DOUBLE,
    events_added BIGINT, duplicates_skipped BIGINT, distinct_events_in_window BIGINT,
    records_received BIGINT, pages BIGINT, query_bytes_billed BIGINT
)"""
COLS = ("run_id", "profile_id", "provider", "revision", "admitted_at", "lease_until",
        "since_at", "until_at", "checkpoint_before", "checkpoint_after", "state",
        "finished_at", "error_code", "retry_not_before", *COUNT_FIELDS)


def init(c):
    c.execute(SCHEMA)


def exists(c):
    return bool(c.execute("""SELECT 1 FROM information_schema.tables
        WHERE table_name='enterprise_ingestion_runs'""").fetchone())


def safe_count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def safe_error(value):
    return value if isinstance(value, str) and ERROR.fullmatch(value) else "unclassified_ingestion_error"


def prune(c, profile_id):
    # Running intents are never evicted. Terminal history is bounded per live
    # profile, including when a feed has unusually frequent empty windows.
    c.execute("""DELETE FROM enterprise_ingestion_runs WHERE run_id IN (
        SELECT run_id FROM enterprise_ingestion_runs WHERE profile_id=? AND state<>'running'
        ORDER BY admitted_at DESC, run_id DESC OFFSET ?)""", [profile_id, MAX_PER_PROFILE])


def start(c, p, since, until, *, previous_run_id=None, now=None):
    """Call inside the same transaction that installs the new lease."""
    now = time.time() if now is None else now
    if previous_run_id:
        finish(c, p["profile_id"], previous_run_id, "interrupted",
               error="lease_expired_before_replacement", now=now)
    c.execute("""INSERT INTO enterprise_ingestion_runs
        (run_id,profile_id,provider,revision,admitted_at,lease_until,since_at,until_at,
         checkpoint_before,state) VALUES (?,?,?,?,?,?,?,?,?,'running')""",
        [p["run_id"], p["profile_id"], p.get("provider", "rest"), p["revision"], now,
         p["lease_until"], since, until, p["checkpoint"]])
    prune(c, p["profile_id"])


def finish(c, profile_id, run_id, state, *, checkpoint=None, counts=None,
           error=None, retry_not_before=None, now=None):
    """Finalize only the exact admitted running intent; never rewrite success.

    A missing row is permitted for a pre-upgrade in-flight intent. No history
    is fabricated for legacy runs.
    """
    if not run_id:
        return
    if state not in STATES - {"running"}:
        raise ValueError("invalid terminal ingestion state")
    now = time.time() if now is None else now
    counts = counts or {}
    c.execute("""UPDATE enterprise_ingestion_runs SET state=?,finished_at=?,checkpoint_after=?,
        error_code=?,retry_not_before=?,events_added=?,duplicates_skipped=?,
        distinct_events_in_window=?,records_received=?,pages=?,query_bytes_billed=?
        WHERE profile_id=? AND run_id=? AND state='running'""",
        [state, now, checkpoint if state == "succeeded" else None,
         safe_error(error) if error is not None else None,
         retry_not_before if isinstance(retry_not_before, (int, float))
         and math.isfinite(retry_not_before) and retry_not_before > 0 else None,
         *[safe_count(counts.get(k)) if state == "succeeded" else None for k in COUNT_FIELDS],
         profile_id, run_id])
    prune(c, profile_id)


def invalidate(c, p, reason):
    if exists(c):
        finish(c, p["profile_id"], p.get("run_id"), "invalidated", error=reason)


def erase(c, profile_id):
    if exists(c):
        c.execute("DELETE FROM enterprise_ingestion_runs WHERE profile_id=?", [profile_id])


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z") if epoch is not None else None


def public(row, now):
    item = dict(zip(COLS, row))
    admitted, lease, finished = (item.pop(k) for k in ("admitted_at", "lease_until", "finished_at"))
    retry = item.pop("retry_not_before")
    state = item["state"]
    item["stored_state"] = state
    if state == "running" and lease <= now:
        item["state"] = "interrupted"
        item["error_code"] = "lease_expired_unreconciled"
    item.update(admitted_at=iso(admitted), finished_at=iso(finished),
                lease_expires_at=iso(lease), retry_not_before=iso(retry),
                elapsed_seconds=max(0, round(finished - admitted, 3)) if finished is not None else None,
                local_intent_unreconciled=state == "running" and lease <= now,
                remote_cancellation_verified=False)
    return item


def summarize(rows):
    states = Counter(r["state"] for r in rows)
    committed = [r for r in rows if r["state"] == "succeeded"]
    duration = [r["elapsed_seconds"] for r in committed if r["elapsed_seconds"] is not None]
    totals = {}
    for key in COUNT_FIELDS:
        known = [r[key] for r in committed if r[key] is not None]
        totals[key] = {"sum_of_reported": sum(known) if known else None,
                       "reported_runs": len(known), "unknown_successful_runs": len(committed)-len(known)}
    errors = Counter(r["error_code"] for r in rows if r["error_code"])
    return {"runs": len(rows), "states": {k: states[k] for k in sorted(STATES)},
            "successful_runs": len(committed),
            "terminal_runs": sum(r["stored_state"] != "running" for r in rows),
            "unreconciled_expired_intents": sum(r["local_intent_unreconciled"] for r in rows),
            "successful_elapsed_seconds_mean": round(sum(duration)/len(duration), 3) if duration else None,
            "successful_elapsed_seconds_max": max(duration) if duration else None,
            "counts": totals, "error_counts": dict(sorted(errors.items()))}


def history(*, profile_id=None, days=30, limit=200, state=None, now=None):
    """Passive, bounded local review; never checks or renews provider credentials."""
    from . import enterprise_rest_feed as rest
    from . import event_log_workspace as ws
    if (profile_id is not None and (not isinstance(profile_id, str) or not PROFILE.fullmatch(profile_id))
            or type(days) is not int or not 1 <= days <= MAX_LOOKBACK_DAYS
            or type(limit) is not int or not 1 <= limit <= MAX_QUERY_ROWS
            or state is not None and state not in STATES):
        raise rest.FeedError("invalid_ingestion_history_filter", 422)
    now = time.time() if now is None else now
    with ws._LOCK:
        c = rest._connect()
        try:
            if profile_id is not None:
                rest._get(c, profile_id)
            where, params = ["admitted_at>=?"], [now-days*86400]
            if profile_id:
                where.append("profile_id=?")
                params.append(profile_id)
            # Derive interrupted display state without changing the ledger or
            # pretending an expired local lease has been reconciled.
            if state == "interrupted":
                where.append("(state='interrupted' OR (state='running' AND lease_until<=?))")
                params.append(now)
            elif state == "running":
                where.append("state='running' AND lease_until>?")
                params.append(now)
            elif state is not None:
                where.append("state=?")
                params.append(state)
            condition = " AND ".join(where)
            population = c.execute("SELECT count(*) FROM enterprise_ingestion_runs WHERE "+condition,
                                   params).fetchone()[0]
            raw = c.execute("SELECT "+",".join(COLS)+" FROM enterprise_ingestion_runs WHERE "+condition+
                            " ORDER BY admitted_at DESC,run_id DESC LIMIT ?", [*params,limit]).fetchall()
            rows = [public(r, now) for r in raw]
            profiles = sorted({r["profile_id"] for r in rows})
            return {"format": FORMAT, "observed_at": iso(now), "days": days, "limit": limit,
                    "profile_id": profile_id, "state_filter": state, "matching_retained_runs": population,
                    "returned_runs": len(rows), "truncated": population > len(rows),
                    "summary_scope": "returned_rows_only", "summary": summarize(rows),
                    "profiles": [{"profile_id": p, **summarize([r for r in rows if r["profile_id"] == p])}
                                 for p in profiles],
                    "runs": rows, "max_terminal_runs_per_profile": MAX_PER_PROFILE,
                    "provider_reads_performed": False, "credential_refresh_performed": False,
                    "provider_live_health_verified": False, "pre_upgrade_history_reconstructed": False}
        finally:
            c.close()
