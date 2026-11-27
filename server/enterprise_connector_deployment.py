"""Private worker state inspection and hardened source-host deployment checks.

This module never executes connector reads, claims a lease, clears an
outbox, modifies a recording state or enables a schedule.
"""
import json
import os
from pathlib import Path
import stat
import time

from gateway.connector_jobs import Receipt

MAX_OUTBOX=8192


class DeploymentError(ValueError):
    pass


def private_dir(directory,*,allow_missing=False):
    path=Path(directory)
    if not path.is_absolute() or ".." in path.parts:
        raise DeploymentError("worker_outbox_requires_absolute_path")
    try:
        info=path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        raise DeploymentError("worker_outbox_unavailable") from None
    except OSError:
        raise DeploymentError("worker_outbox_unavailable") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise DeploymentError("worker_outbox_directory_must_be_private")
    # Reject redirected ancestors as well as a symlinked final component.
    # On macOS deployments use the canonical path (/private/var/...).
    if path.resolve()!=path:
        raise DeploymentError("worker_outbox_path_not_canonical")
    return True


def _pairs(items):
    result={}
    for k,v in items:
        if k in result:
            raise ValueError()
        result[k]=v
    return result


def read_pending(directory,*,job,organization,digest):
    """Read and validate a pending receipt without exposing lease material."""
    path=Path(directory)/"pending.json"
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise DeploymentError("invalid_private_receipt_outbox") from None
    try:
        with os.fdopen(fd,"rb") as stream:
            before=os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid()
                or before.st_nlink!=1 or before.st_mode&0o077 or not 1<=before.st_size<=MAX_OUTBOX):
                raise ValueError()
            raw=stream.read(MAX_OUTBOX+1)
            after=os.fstat(stream.fileno())
            if (len(raw)!=before.st_size or len(raw)>MAX_OUTBOX or
                (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
                raise ValueError()
        state=json.loads(raw,object_pairs_hook=_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(state,dict) or set(state)!={"phase","job","organization","profile_sha256","receipt"}:
            raise ValueError()
        if (state["job"],state["organization"],state["profile_sha256"])!=(job,organization,digest):
            raise DeploymentError("outbox_identity_mismatch")
        receipt=state["receipt"]
        if state["phase"]=="intent":
            if not isinstance(receipt,dict) or set(receipt)!={"lease_id","job_revision"}:
                raise ValueError()
            Receipt.model_validate({**receipt,"outcome":"blocked","code":"local_run_uncertain"})
        elif state["phase"]=="receipt":
            Receipt.model_validate(receipt)
        else:
            raise ValueError()
        return state
    except DeploymentError:
        raise
    except (ValueError,TypeError,UnicodeError,RecursionError,OSError):
        raise DeploymentError("invalid_private_receipt_outbox") from None


def finish_pending(path):
    """Durably remove a completion only after a confirmed Gateway response."""
    path.unlink()
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def explicit_recording():
    """Dedicated service mode: no fallback to recording on absent/corrupt state."""
    from shared.capture_control import state_path
    try:
        path=state_path()
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,"rb") as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022 or not 1<=info.st_size<=1024*1024:
                return False
            raw=stream.read(1024*1024+1)
        if len(raw)>1024*1024:
            return False
        state=json.loads(raw,object_pairs_hook=_pairs)
        return isinstance(state,dict) and type(state.get("version")) is int and state.get("version")==1 and state.get("state")=="recording"
    except (ValueError,TypeError,OSError,AttributeError,RecursionError):
        return False


def preflight(worker,*,remote=False):
    from . import enterprise_rest_feed as rest
    from . import event_log_workspace as ws
    from . import local_scheduler as scheduler
    from gateway.connector_jobs import profile_digest
    checks=[]

    def add(name,state,code):
        checks.append({"check":name,"state":state,"code":code})

    if os.name!="posix":
        add("platform","blocked","connector_worker_requires_posix_host")
    else:
        add("platform","passed","posix_worker_supported")
    try:
        present=private_dir(worker.directory,allow_missing=True)
        if present:
            pending=read_pending(worker.directory,job=worker.job,organization=worker.org,digest=worker.digest)
            if pending:
                add("outbox","blocked","pending_"+pending["phase"]+"_requires_reconciliation")
            else:
                add("outbox","passed","no_pending_completion_observed")
        else:
            add("outbox","warning","private_outbox_not_created_yet")
    except (DeploymentError,OSError,AttributeError):
        add("outbox","blocked","private_outbox_invalid_or_wrong_identity")

    # Do not call inventory on a missing database: it would create it.
    # Existing workspace inspection may initialize the existing schema,
    # but never imports, deletes or changes retained events.
    try:
        exists=ws.database_path().is_file()
    except Exception:
        exists=False
    if not exists:
        add("workspace","blocked","existing_retained_workspace_required")
    else:
        add("workspace","passed","existing_workspace_observed")
        try:
            p=next((x for x in rest.inventory()["profiles"] if x["profile_id"]==worker.profile_id),None)
            if p is None:
                add("profile","blocked","local_profile_unavailable")
            elif p["revision"]!=worker.revision or profile_digest(p)!=worker.digest:
                add("profile","blocked","local_profile_changed")
            else:
                add("profile","passed","exact_local_profile_pin_matches")
                add("local_authorization","passed" if p["enabled"] else "blocked",
                    "profile_enabled" if p["enabled"] else "profile_disabled")
                add("credential","passed" if p["credential_binding_available"] else "blocked",
                    "local_binding_matches" if p["credential_binding_available"] else "local_binding_unavailable")
                add("cooldown","blocked" if p["retry_not_before"]>time.time() else "passed",
                    "provider_retry_cooldown" if p["retry_not_before"]>time.time() else "no_local_cooldown")
                if p["status"] in ("running","interrupted"):
                    add("local_run","blocked","local_run_requires_reconciliation")
        except Exception:
            add("profile","blocked","local_profile_unavailable")
    try:
        conflict=any(s["kind"]=="enterprise_rest_feed" and s["target_id"]==worker.profile_id and s["enabled"]
                     for s in scheduler.status()["schedules"])
        add("schedule","blocked" if conflict else "passed",
            "local_schedule_conflict" if conflict else "no_enabled_local_schedule_observed")
    except Exception:
        add("schedule","blocked","local_scheduler_state_unavailable")
    recording=worker.recording()
    add("recording","passed" if recording else "blocked","recording_enabled" if recording else "local_recording_paused")
    contacted=False
    if remote:
        try:
            with worker.client_factory(timeout=15,follow_redirects=False,trust_env=False) as client:
                contacted=True
                result=worker._api(client,"inspect",worker.pin())
                worker._verify_job(result.get("job"))
                if result.get("dispatch_authorized") is not False:
                    raise ValueError()
                job=result["job"]
                add("gateway","passed","exact_gateway_job_pin_matches")
                add("gateway_job","passed" if job["enabled"] else "warning",
                    "gateway_job_enabled" if job["enabled"] else "gateway_job_paused")
                if job.get("lease_expires_at") is not None:
                    add("gateway_lease","blocked","gateway_lease_requires_reconciliation")
        except Exception:
            add("gateway","blocked","gateway_inspection_failed_or_pin_mismatch")
    else:
        add("gateway","not_checked","remote_inspection_not_requested")
    return {"format":"openworkgraph.connector-deployment-preflight.v1",
            "strict_recording_state_required":worker.require_explicit_recording,
            "checks":checks,"ready_for_review":not any(x["state"]=="blocked" for x in checks),
            "gateway_inspection_requested":remote,"gateway_contact_attempted":contacted,
            "provider_contacted":False,"lease_claimed":False,"events_read":False,
            "execution_authorized":False,"workspace_inspection_may_initialize_schema":exists}
