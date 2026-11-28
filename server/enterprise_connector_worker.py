"""Customer-hosted connector worker, with durable completion outbox.

Run on a dedicated local event-log workspace (one owner process). The Gateway
coordinates a pinned job; it never receives source credentials or raw events.
An incomplete local intent becomes an ambiguous receipt, not a second read.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import stat
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

import httpx

from gateway.connector_jobs import profile_digest
from server import enterprise_rest_feed as rest
from server import local_scheduler as scheduler
from server import enterprise_connector_deployment as deployment
from gateway.connector_jobs import Claim


class WorkerError(RuntimeError):
    pass


def base_url(value):
    p=urlsplit(value)
    if (p.username or p.password or p.query or p.fragment or p.path not in ("","/")
            or not p.hostname or p.scheme not in ("http","https")
            or p.scheme=="http" and p.hostname not in ("127.0.0.1","::1","localhost")):
        raise WorkerError("gateway_url_requires_https_or_loopback")
    return value.rstrip("/")


def _atomic(path,data):
    fd,temp=tempfile.mkstemp(prefix=".receipt-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as out:
            json.dump(data,out,separators=(",",":"),allow_nan=False)
            out.flush();os.fsync(out.fileno())
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def worker_lock(directory):
    # This first worker is intentionally POSIX-only. The deployment runs
    # one process per dedicated workspace, not multiple desktop processes.
    try:
        import fcntl
    except ImportError:
        raise WorkerError("connector_worker_requires_posix_host") from None
    try:
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        deployment.private_dir(directory)
        fd=os.open(directory/"worker.lock",os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    except deployment.DeploymentError as exc:
        raise WorkerError(str(exc)) from None
    except OSError:
        raise WorkerError("worker_outbox_or_lock_unavailable") from None
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or info.st_mode&0o077:
            raise WorkerError("invalid_private_worker_lock")
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerError("worker_already_running") from None
        yield
    finally:
        os.close(fd)


class Worker:
    def __init__(self,*,gateway,token,organization,job,profile_id,revision,digest,outbox,client_factory=httpx.Client,report_readiness=False,require_explicit_recording=False):
        self.gateway=base_url(gateway)
        if not token.startswith("owg_connector_worker_"):
            raise WorkerError("worker_credential_required")
        self.token,self.org,self.job=token,organization,job
        self.profile_id,self.revision,self.digest=profile_id,revision,digest
        self.directory=Path(outbox)
        self.client_factory=client_factory
        self.report_readiness=report_readiness is True
        self.require_explicit_recording=require_explicit_recording is True
        try:
            Claim(profile_id=profile_id,profile_revision=revision,profile_sha256=digest)
            if not self.directory.is_absolute():
                raise ValueError()
        except ValueError:
            raise WorkerError("invalid_worker_deployment_pin_or_outbox") from None

    def recording(self):
        if self.require_explicit_recording:
            return deployment.explicit_recording()
        return scheduler._recording()

    def pin(self):
        return {"profile_id":self.profile_id,"profile_revision":self.revision,"profile_sha256":self.digest}

    def _heartbeat(self,client,code):
        if not self.report_readiness:
            return
        response=self._api(client,"readiness",{**self.pin(),"code":code})
        self._verify_job(response.get("job"))
        if response.get("dispatch_authorized") is not False:
            raise WorkerError("invalid_worker_readiness_reply")

    def _api(self,client,path,payload):
        if path not in ("inspect","readiness","claim","complete"):
            raise WorkerError("invalid_gateway_worker_operation")
        try:
            with client.stream("POST",self.gateway+"/v1/connector-worker/"+path,
                               json=payload,headers={"Authorization":"Bearer "+self.token}) as response:
                if response.status_code!=200:
                    raise WorkerError("gateway_refused_"+str(response.status_code))
                data=bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    data.extend(chunk)
                    if len(data)>65536:
                        raise WorkerError("gateway_reply_budget_exceeded")
        except httpx.HTTPError:
            raise WorkerError("gateway_transport_failure_retry_receipt_only") from None
        try:
            value=rest._strict_json(bytes(data))
        except rest.FeedError:
            raise WorkerError("invalid_gateway_reply") from None
        if not isinstance(value,dict):
            raise WorkerError("invalid_gateway_reply")
        return value

    def _local(self):
        try:
            p=next((x for x in rest.inventory()["profiles"] if x["profile_id"]==self.profile_id),None)
        except Exception:
            return None,"local_profile_unavailable"
        if p is None or not p["enabled"] or not p["credential_binding_available"]:
            return None,"local_profile_unavailable"
        if p["revision"]!=self.revision or profile_digest(p)!=self.digest:
            return None,"local_profile_changed"
        try:
            if any(s["kind"]=="enterprise_rest_feed" and s["target_id"]==self.profile_id and s["enabled"]
                   for s in scheduler.status()["schedules"]):
                return None,"local_schedule_conflict"
        except Exception:
            return None,"local_profile_unavailable"
        return p,None

    def _verify_job(self,job):
        if not isinstance(job,dict) or (job.get("organization_id"),job.get("job_id"),job.get("profile_id"),
            job.get("profile_revision"),job.get("profile_sha256"))!=(self.org,self.job,self.profile_id,self.revision,self.digest):
            raise WorkerError("gateway_job_pin_mismatch")

    def _submit(self,client,path,state):
        if state.get("job")!=self.job or state.get("organization")!=self.org or state.get("profile_sha256")!=self.digest:
            raise WorkerError("outbox_identity_mismatch")
        receipt=state["receipt"]
        if state["phase"]=="intent":
            receipt={**receipt,"outcome":"blocked","code":"local_run_uncertain"}
            state={**state,"phase":"receipt","receipt":receipt}
            _atomic(path,state)
        elif state["phase"]!="receipt":
            raise WorkerError("invalid_outbox_state")
        result=self._api(client,"complete",receipt)
        self._verify_job(result.get("job"))
        if result.get("accepted") is not True:
            raise WorkerError("completion_not_confirmed")
        try:
            deployment.finish_pending(path)
        except OSError:
            raise WorkerError("confirmed_receipt_cleanup_failed") from None
        return {"outcome":receipt["outcome"],"code":receipt["code"],"receipt_replayed":result.get("replayed") is True}

    def once(self):
        with worker_lock(self.directory),self.client_factory(timeout=15,follow_redirects=False,trust_env=False) as client:
            path=self.directory/"pending.json"
            try:
                pending=deployment.read_pending(self.directory,job=self.job,organization=self.org,digest=self.digest)
            except deployment.DeploymentError as exc:
                if self.report_readiness:
                    self._heartbeat(client,"invalid_outbox")
                raise WorkerError(str(exc)) from None
            if pending is not None:
                # Recovery takes precedence over optional readiness reporting.
                result=self._submit(client,path,pending)
                return result
            p,error=self._local()
            if error:
                self._heartbeat(client,error)
                return {"outcome":"blocked","code":error}
            if self.report_readiness:
                if not self.recording():
                    self._heartbeat(client,"local_recording_paused")
                    return {"outcome":"blocked","code":"local_recording_paused"}
                if p["retry_not_before"]>time.time():
                    self._heartbeat(client,"local_cooldown")
                    return {"outcome":"blocked","code":"local_cooldown"}
                if p["status"] in ("running","interrupted"):
                    self._heartbeat(client,"workspace_unavailable")
                    return {"outcome":"blocked","code":"local_run_requires_reconciliation"}
                self._heartbeat(client,"ready")
            claim=self._api(client,"claim",self.pin())
            job=claim.get("job")
            self._verify_job(job)
            if claim.get("claimed") is not True:
                return {"outcome":"idle","state":job.get("state")}
            lease=claim.get("lease_id")
            if not isinstance(lease,str) or len(lease)!=64:
                raise WorkerError("invalid_gateway_lease")
            base={"lease_id":lease,"job_revision":job["revision"]}
            state={"phase":"intent","job":self.job,"organization":self.org,"profile_sha256":self.digest,"receipt":base}
            _atomic(path,state)
            # Do not run if the dispatch expired while waiting for local IO.
            if job["lease_expires_at"]<=time.time()+rest.LEASE_SECONDS+10:
                receipt={**base,"outcome":"blocked","code":"local_run_uncertain"}
            else:
                p,error=self._local()
                if error:
                    receipt={**base,"outcome":"blocked","code":error}
                elif not self.recording():
                    receipt={**base,"outcome":"skipped","code":"local_recording_paused"}
                else:
                    before=p["checkpoint"]
                    try:
                        result=rest.run(self.profile_id,self.revision,confirm_reads=True,confirm_retention=True)
                        receipt={**base,"outcome":"succeeded","code":"window_committed",
                                 "events_added":result["events_added"],"duplicates_skipped":result["duplicates_skipped"],
                                 "checkpoint":result["checkpoint"]}
                    except rest.FeedError as exc:
                        current,current_error=self._local()
                        if current_error or current["checkpoint"]!=before:
                            receipt={**base,"outcome":"blocked","code":"local_run_uncertain"}
                        elif exc.code=="no_new_checkpoint_window":
                            receipt={**base,"outcome":"skipped","code":"no_new_window"}
                        elif exc.code in ("provider_rate_limited","provider_retry_cooldown"):
                            remaining=max(0,int(current["retry_not_before"]-time.time())+1)
                            receipt={**base,"outcome":"failed","code":"rate_limited",
                                     "retry_after_seconds":min(86400,max(remaining,int(exc.retry_after)))}
                        elif exc.code in ("rest_run_in_progress","rest_run_invalidated","retention_or_source_failed_inspect_checkpoint"):
                            receipt={**base,"outcome":"blocked","code":"local_run_uncertain"}
                        else:
                            receipt={**base,"outcome":"failed","code":"source_failed"}
                    except Exception:
                        receipt={**base,"outcome":"blocked","code":"local_run_uncertain"}
            state.update(phase="receipt",receipt=receipt)
            _atomic(path,state)
            return self._submit(client,path,state)


def main():
    parser=argparse.ArgumentParser(description="Run an explicitly approved local connector profile under a Gateway job.")
    parser.add_argument("--describe-profile",help="Print only the exact profile pin for administrator approval")
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument("--once",action="store_true",help="One poll, then exit")
    mode.add_argument("--preflight",action="store_true",help="Inspect deployment without claiming or reading a provider")
    parser.add_argument("--inspect-gateway",action="store_true",help="Allow preflight to contact the Gateway only")
    args=parser.parse_args()
    if args.describe_profile:
        p=next((x for x in rest.inventory()["profiles"] if x["profile_id"]==args.describe_profile),None)
        if not p:
            raise SystemExit("unknown local profile")
        print(json.dumps({"profile_id":p["profile_id"],"profile_revision":p["revision"],"profile_sha256":profile_digest(p),"provider":p["provider"]}))
        return
    if args.inspect_gateway and not args.preflight:
        parser.error("--inspect-gateway requires --preflight")
    if not args.preflight and os.environ.get("OWG_CONNECTOR_WORKER_APPROVED")!="true":
        raise SystemExit("explicit local deployment approval required")
    try:
        worker=Worker(gateway=os.environ["OWG_CONNECTOR_GATEWAY_URL"],token=rest.resolve_secret("OWG_CONNECTOR_WORKER_TOKEN"),
            organization=os.environ["OWG_CONNECTOR_ORGANIZATION"],job=os.environ["OWG_CONNECTOR_JOB_ID"],
            profile_id=os.environ["OWG_CONNECTOR_PROFILE_ID"],revision=int(os.environ["OWG_CONNECTOR_PROFILE_REVISION"]),
            digest=os.environ["OWG_CONNECTOR_PROFILE_SHA256"],outbox=os.environ["OWG_CONNECTOR_OUTBOX"],report_readiness=os.environ.get("OWG_CONNECTOR_REPORT_READINESS")=="true",
            require_explicit_recording=os.environ.get("OWG_CONNECTOR_REQUIRE_EXPLICIT_RECORDING")=="true")
    except (KeyError,ValueError,WorkerError):
        raise SystemExit("invalid or missing connector deployment configuration") from None
    if args.preflight:
        result=deployment.preflight(worker,remote=args.inspect_gateway)
        print(json.dumps(result),flush=True)
        if not result["ready_for_review"]:
            raise SystemExit(2)
        return
    while True:
        try:
            result=worker.once()
            print(json.dumps(result),flush=True)
        except WorkerError as exc:
            print(json.dumps({"outcome":"blocked","code":str(exc)}),flush=True)
        if args.once:
            return
        time.sleep(30)


if __name__=="__main__":
    main()
