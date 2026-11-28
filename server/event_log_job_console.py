"""Session-owned ephemeral job inventory and atomic terminal cleanup.

No additional persistence, query execution, raw spec, result bytes or
cross-session queue statistics. The canonical manager owns the lock/expiry.
"""
from collections import Counter
import hashlib
import json
import re
import time

from . import event_log_jobs as engine

FORMAT = "openworkgraph.analysis-job-console.v1"
STATES = ("queued", "running", "succeeded", "cancelled", "timed_out", "failed")


def _digest(rows):
    # Terminal job identities are unique, immutable and never reused.
    return hashlib.sha256(json.dumps([FORMAT, rows],sort_keys=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _terminal(manager, owner):
    return sorted((j["job_id"],j["log_id"],j["state"],j["finished_at"],j["spec_sha256"])
                  for j in manager._jobs.values() if j["owner"]==owner and j["state"] in engine._TERMINAL)


def inventory(manager, owner):
    with manager._condition:
        manager._purge()
        now=time.monotonic()
        owned=[j for j in manager._jobs.values() if j["owner"]==owner]
        rows=[]
        for j in owned:
            row=manager._status(j)
            # Workspace errors are useful in the exact job view, but this
            # cross-log overview deliberately does not collect their text.
            row.pop("error",None)
            row["result_bytes"]=len(j["result"]) if j["state"]=="succeeded" and j["result"] is not None else None
            row["expires_in_seconds"]=max(0,int(j["expires"]-now))
            rows.append(row)
        counts=Counter(r["state"] for r in rows)
        terminal=_terminal(manager,owner)
        return {"format":FORMAT,"jobs":sorted(rows,key=lambda r:(r["submitted_at"],r["job_id"]),reverse=True),
                "owned_job_count":len(rows),"owned_state_counts":{k:counts[k] for k in STATES},
                "terminal_count":len(terminal),"terminal_review_sha256":_digest(terminal),
                "session_scoped":True,"durable":False,"source_version_pinned_at_submission":False,
                "current_source_verified":False,"execution_authorized":False}


def clear_terminal(manager,owner,payload):
    if (not isinstance(payload,dict) or set(payload)!={"expected_terminal_review_sha256","confirm"}
            or payload["confirm"] is not True
            or not isinstance(payload["expected_terminal_review_sha256"],str)
            or not re.fullmatch(r"[a-f0-9]{64}",payload["expected_terminal_review_sha256"])):
        raise engine.JobError("Confirm the exact reviewed terminal job population",422)
    with manager._condition:
        manager._purge()
        terminal=_terminal(manager,owner)
        if _digest(terminal)!=payload["expected_terminal_review_sha256"]:
            raise engine.JobError("Terminal job population changed; refresh the review",409)
        # All validation precedes mutation; no active job or other owner is
        # eligible, including jobs that became terminal after preview.
        for job_id,*_ in terminal:
            job=manager._jobs[job_id]
            job["control"].stop()
            del manager._jobs[job_id]
        manager._purge()
        manager._condition.notify_all()
        return {"format":FORMAT,"removed_terminal_jobs":len(terminal),
                "active_jobs_cancelled":0,"other_sessions_changed":False,
                "execution_authorized":False}
