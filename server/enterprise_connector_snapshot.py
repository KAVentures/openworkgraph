"""Build a minimized snapshot from the exact retained source-key index.

Run on the source host. Explicit preview first, then a separate upload
using the approved digest. The source log remains canonical.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import tempfile

import httpx

from shared.connector_event_snapshot import Snapshot, Manifest, Event, utc, digest, manifest_digest, MAX_EVENTS, MAX_BYTES
from gateway.connector_jobs import profile_digest
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server.enterprise_connector_worker import base_url, WorkerError


def prepare(profile_id,since,until,*,activity_mode="coded",confirm_labels=False):
    if activity_mode not in ("coded","labels") or activity_mode=="labels" and confirm_labels is not True:
        raise ValueError("explicit activity label disclosure required")
    start,end=utc(since),utc(until)
    if start>=end:
        raise ValueError("invalid event time range")
    with ws._LOCK:
        c=rest._connect()
        try:
            p=rest._get(c,profile_id)
            public=rest._public(p,rest.time.time())
            if not p["enabled"] or not p["log_id"] or not p["checkpoint"] or not public["credential_binding_available"]:
                raise ValueError("retained enabled profile and current source binding required")
            if p["run_id"]:
                raise ValueError("finish or reconcile local run before snapshot")
            log=p["log_id"]
            ws._check_log(c,log)
            # All retained connector events must have one exact supplied
            # identity and exactly one source_case object. Refuse mixed or
            # externally appended log shapes rather than infer mappings.
            total=c.execute("""SELECT count(*) FROM events
                WHERE log_id=? AND ts>=? AND ts<?""",[log,start,end]).fetchone()[0]
            if not 1<=total<=MAX_EVENTS:
                raise ValueError("select a nonempty window of at most 5000 events")
            records=c.execute("""SELECT k.source_event_id,k.source_namespace,e.activity,e.ts,
                o.object_id,o.object_type
                FROM events e JOIN event_source_keys k ON k.log_id=e.log_id AND k.event_id=e.event_id
                JOIN event_objects l ON l.log_id=e.log_id AND l.event_id=e.event_id
                JOIN objects o ON o.log_id=l.log_id AND o.object_id=l.object_id
                WHERE e.log_id=? AND e.ts>=? AND e.ts<?
                LIMIT ?""",[log,start,end,MAX_EVENTS+1]).fetchall()
            if len(records)!=total or any(ns!=profile_id or typ!="source_case" for _,ns,_,_,_,typ in records):
                raise ValueError("retained source identity or case shape changed")
            rows=[]
            for event_id,ns,activity,stamp,case_id,typ in records:
                if not case_id.startswith("src:"+profile_id+":"):
                    raise ValueError("retained case namespace changed")
                rows.append(Event(event_id=rest._key(p,"publication_event",event_id),
                    case_id=rest._key(p,"publication_case",case_id),
                    activity=activity if activity_mode=="labels" else "A_"+rest._key(p,"publication_activity",activity),
                    observed_at=stamp.replace(tzinfo=timezone.utc).isoformat()).model_dump())
            rows.sort(key=lambda r:(r["observed_at"],r["event_id"]))
            m=Manifest(profile_id=profile_id,profile_revision=p["revision"],profile_sha256=profile_digest(public),
                       provider=public["provider"],since=start,until=end,checkpoint=p["checkpoint"],event_count=len(rows),
                       case_count=len({r["case_id"] for r in rows}),activity_mode=activity_mode,rows_sha256=digest(rows))
            return Snapshot(manifest=m,events=rows)
        finally:
            c.close()


def write_private(path,snapshot):
    path=Path(path)
    data=snapshot.model_dump_json().encode()
    if len(data)>MAX_BYTES:
        raise ValueError("snapshot payload exceeds 5 MiB")
    # Exclusive creation; no silent overwrite of the artifact an admin reviewed.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,"wb") as out:
            out.write(data);out.flush();os.fsync(out.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def read_private(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077 or path.stat().st_size>MAX_BYTES:
        raise ValueError("snapshot must be a private bounded file")
    return Snapshot.model_validate_json(path.read_bytes())


def publish(snapshot,*,gateway,token,organization,job,expected_grant,client_factory=httpx.Client):
    if not token.startswith("owg_connector_worker_"):
        raise ValueError("job-scoped worker credential required")
    root=base_url(gateway)+"/v1/connector-worker/event-publication"
    headers={"Authorization":"Bearer "+token}
    with client_factory(timeout=30,follow_redirects=False,trust_env=False) as client:
        r=client.get(root,headers=headers)
        if r.status_code!=200:
            raise WorkerError("no_current_event_publication_grant")
        grant=r.json()
        if (grant.get("organization_id"),grant.get("job_id"),grant.get("grant_id"),grant.get("manifest_sha256"))!=(organization,job,expected_grant,manifest_digest(snapshot.manifest)):
            raise WorkerError("approved_snapshot_grant_mismatch")
        r=client.post(root,json={"grant_id":expected_grant,"snapshot":snapshot.model_dump()},headers=headers)
        if r.status_code!=200:
            raise WorkerError("publication_refused_"+str(r.status_code))
        result=r.json()
        if result.get("manifest_sha256")!=grant["manifest_sha256"] or result.get("state")!="published":
            raise WorkerError("publication_not_confirmed")
        return {"published":True,"manifest_sha256":result["manifest_sha256"],"replayed":result.get("replayed") is True}


def main():
    parser=argparse.ArgumentParser(description="Review and explicitly publish a selected retained connector event snapshot.")
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("prepare")
    p.add_argument("--profile",required=True);p.add_argument("--since",required=True);p.add_argument("--until",required=True)
    p.add_argument("--out",required=True);p.add_argument("--labels",action="store_true");p.add_argument("--confirm-label-disclosure",action="store_true")
    u=sub.add_parser("publish")
    u.add_argument("--snapshot",required=True);u.add_argument("--grant",required=True);u.add_argument("--confirm-disclosure",action="store_true")
    args=parser.parse_args()
    if args.command=="prepare":
        snap=prepare(args.profile,args.since,args.until,activity_mode="labels" if args.labels else "coded",
                     confirm_labels=args.confirm_label_disclosure)
        write_private(args.out,snap)
        print(json.dumps({"manifest":snap.manifest.model_dump(),"manifest_sha256":manifest_digest(snap.manifest)}))
    else:
        if not args.confirm_disclosure:
            raise SystemExit("explicit local disclosure confirmation required")
        snap=read_private(args.snapshot)
        # Re-read the canonical local projection. Deletion or change after
        # review invalidates the immutable artifact instead of exporting it.
        m=snap.manifest
        current=prepare(m.profile_id,m.since,m.until,activity_mode=m.activity_mode,confirm_labels=True)
        if current.model_dump()!=snap.model_dump():
            raise SystemExit("retained snapshot changed; obtain new approval")
        print(json.dumps(publish(snap,gateway=os.environ["OWG_CONNECTOR_GATEWAY_URL"],
            token=rest.resolve_secret("OWG_CONNECTOR_WORKER_TOKEN"),organization=os.environ["OWG_CONNECTOR_ORGANIZATION"],
            job=os.environ["OWG_CONNECTOR_JOB_ID"],expected_grant=args.grant)))


if __name__=="__main__":
    main()
