"""Atomic local mining import of a reviewed ordinary Gateway bundle."""
from io import BytesIO,StringIO
import csv
import json
import re
from shared.connector_bundle_import import inspect,MAPPING
from . import event_log_workspace as ws
from . import event_log_source_identity as identity
from .enterprise_rest_feed import FeedError

MAX_RECEIPTS=100


def _inspect(raw):
    try:return inspect(raw)
    except ValueError as exc:
        raise FeedError(str(exc),422) from None


def _csv(bundle):
    buf=StringIO(newline="")
    writer=csv.DictWriter(buf,fieldnames=["source_namespace","event_id","case_id","activity","observed_at"])
    writer.writeheader()
    writer.writerows(e.model_dump() for e in bundle.events)
    return BytesIO(buf.getvalue().encode("utf-8"))


def _pins(report):
    return [{"job_id":s["job_id"],"profile_id":s["manifest"]["profile_id"],
             "profile_revision":s["manifest"]["profile_revision"],"profile_sha256":s["manifest"]["profile_sha256"],
             "provider":s["manifest"]["provider"],"activity_mode":s["manifest"]["activity_mode"]}
            for s in report["sources"]]


def receipts(c,log_id):
    return [json.loads(row[0]) for row in c.execute(
        "SELECT definition FROM connector_bundle_import_receipts WHERE log_id=? ORDER BY revision",[log_id]).fetchall()]


def metadata(c,log_id,current_count):
    row=c.execute("SELECT count(*),arg_min(bundle_sha256,revision),arg_max(definition,revision) FROM connector_bundle_import_receipts WHERE log_id=?",[log_id]).fetchone()
    if not row[0]:return None
    last=json.loads(row[2])
    return {"revision":last["revision"],"receipt_count":row[0],
            "initial_bundle_sha256":row[1],
            "latest_bundle_sha256":last["canonical_bundle_sha256"],
            "events_after_last_verified_import":last["retained_event_count"],
            "current_event_count_matches_last_receipt":current_count==last["retained_event_count"],
            "gateway_origin_authenticated":False,"live_grants_verified":False,
            "original_import_receipts_not_current_log_certification":True}


def _history_check(c,log_id,report):
    log=ws._check_log(c,log_id)
    history=receipts(c,log_id)
    if not history:raise FeedError("target_is_not_a_verified_bundle_log",422)
    latest=history[-1]
    batches=c.execute("SELECT count(*) FROM log_appends WHERE log_id=?",[log_id]).fetchone()[0]
    if (log["events"]!=latest["retained_event_count"] or log["objects"]!=latest["retained_case_count"]
            or batches!=len(history)-1):
        raise FeedError("target_log_changed_outside_verified_import")
    # Bind exact source namespaces, provider profile pins and label mode.
    # A rotated profile or newly linked case scope requires a new log.
    if _pins(report)!=_pins(history[0]):
        raise FeedError("bundle_source_scope_changed_new_log_required")
    prior_grants={(src["job_id"],src["grant_id"]):src["manifest_sha256"]
                  for r in history for src in r["sources"]}
    for src in report["sources"]:
        prior=prior_grants.get((src["job_id"],src["grant_id"]))
        if prior is not None and prior!=src["manifest_sha256"]:
            raise FeedError("bundle_grant_manifest_conflict")
    return log,history


def preview(raw,target=None):
    _,report=_inspect(raw)
    result={**report,"persisted":False,"target_log_id":target,"expected_target_revision":0}
    if target:
        with ws._LOCK:
            c=ws._connect()
            try:
                log,history=_history_check(c,target,report)
                result.update(expected_target_revision=history[-1]["revision"],
                              target_event_count=log["events"],
                              bundle_already_imported=any(r["canonical_bundle_sha256"]==report["canonical_bundle_sha256"] for r in history))
            except ws.EventLogWorkspaceError:
                raise FeedError("invalid_bundle_target",422) from None
            finally:c.close()
    return result


def _verify_retained(c,log_id,bundle):
    """Check exact projected rows inside the existing workspace transaction.

    A CSV parser normalization, dropped event or incorrect case namespace
    must abort the complete operation, not merely lower the retained count.
    """
    identity.validate_index(c,log_id)
    c.execute("CREATE OR REPLACE TEMP TABLE connector_bundle_expected (ns VARCHAR,eid VARCHAR,case_id VARCHAR,activity VARCHAR,ts TIMESTAMP)")
    try:
        # executemany is bounded by the validated 20,000-event cap.
        c.executemany("INSERT INTO connector_bundle_expected VALUES (?,?,?,?,?)",
            [(e.source_namespace,e.event_id,"src:"+e.source_namespace+":"+e.case_id,e.activity,e.observed_at)
             for e in bundle.events])
        mismatch=c.execute("""SELECT 1 FROM connector_bundle_expected x
            LEFT JOIN event_source_keys k ON k.log_id=? AND k.source_namespace=x.ns AND k.source_event_id=x.eid
            LEFT JOIN events e ON e.log_id=k.log_id AND e.event_id=k.event_id
            LEFT JOIN event_objects l ON l.log_id=e.log_id AND l.event_id=e.event_id
            WHERE e.event_id IS NULL OR e.activity IS DISTINCT FROM x.activity
            OR e.ts IS DISTINCT FROM x.ts OR l.object_id IS DISTINCT FROM x.case_id LIMIT 1""",[log_id]).fetchone()
        if mismatch:raise FeedError("retained_projection_integrity_failure")
    finally:
        c.execute("DROP TABLE IF EXISTS connector_bundle_expected")


def retain(raw,*,file_sha256,canonical_bundle_sha256,request_id,target=None,expected_revision=0,name=None,
           confirm_retention=False,confirm_origin_review=False):
    if not isinstance(request_id,str) or not re.fullmatch(r"[a-f0-9]{32}",request_id):
        raise FeedError("invalid_bundle_import_request_id",422)
    if confirm_retention is not True or confirm_origin_review is not True:
        raise FeedError("explicit_bundle_retention_and_origin_review_required",403)
    bundle,report=_inspect(raw)
    if report["file_sha256"]!=file_sha256 or report["canonical_bundle_sha256"]!=canonical_bundle_sha256:
        raise FeedError("bundle_review_changed")
    if type(expected_revision) is not int or expected_revision<0 or (target is None)!=(expected_revision==0):
        raise FeedError("exact_target_revision_required",422)
    if target is None and (not isinstance(name,str) or not name.strip()):
        raise FeedError("new_bundle_log_name_required",422)
    if target is not None and name is not None:
        raise FeedError("existing_target_cannot_be_renamed_by_import",422)
    with ws._LOCK:
        c=ws._connect()
        try:
            row=c.execute("SELECT definition FROM connector_bundle_import_receipts WHERE request_id=?",[request_id]).fetchone()
            if row:
                previous=json.loads(row[0])
                if (previous["file_sha256"]!=file_sha256 or previous["canonical_bundle_sha256"]!=canonical_bundle_sha256
                    or previous["target_log_id"]!=target or previous["revision"]!=expected_revision+1
                    or previous["requested_name"]!=name):
                    raise FeedError("bundle_import_request_identity_conflict")
                log=ws._check_log(c,previous["log_id"])
                _verify_retained(c,previous["log_id"],bundle)
                return {"log":log,"receipt":previous,"replayed_receipt":True}
            if target:
                log,history=_history_check(c,target,report)
                if history[-1]["revision"]!=expected_revision:
                    raise FeedError("bundle_target_revision_changed")
                previous=next((r for r in history if r["canonical_bundle_sha256"]==canonical_bundle_sha256),None)
                if previous:
                    _verify_retained(c,target,bundle)
                    return {"log":log,"receipt":previous,"replayed_receipt":True}
                if len(history)>=MAX_RECEIPTS:
                    raise FeedError("bundle_receipt_limit_new_log_required")
                before=log["events"]
            else:
                history=[];before=0
        except ws.EventLogWorkspaceError:
            raise FeedError("invalid_bundle_target",422) from None
        finally:c.close()
        made=[]
        def record(c,log_id):
            _verify_retained(c,log_id,bundle)
            actual=c.execute("SELECT events,objects,rejected_events FROM logs WHERE log_id=?",[log_id]).fetchone()
            if actual is None or actual[2]!=0 or not before<=actual[0]<=before+bundle.event_count:
                raise FeedError("retained_projection_count_failure")
            if target is None and (actual[0]!=bundle.event_count or actual[1]!=report["source_local_case_count"]):
                raise FeedError("retained_projection_count_failure")
            entry={**report,"log_id":log_id,"revision":expected_revision+1,
                   "request_id":request_id,"target_log_id":target,"requested_name":name,
                   "events_added":actual[0]-before,"duplicates_skipped":bundle.event_count-(actual[0]-before),
                   "retained_event_count":actual[0],"retained_case_count":actual[1],
                   "local_copy_not_revocable_by_gateway":True}
            c.execute("INSERT INTO connector_bundle_import_receipts VALUES (?,?,?,?,?)",
                      [log_id,entry["revision"],canonical_bundle_sha256,request_id,json.dumps(entry,separators=(",",":"))])
            made.append(entry)
        try:
            if target:
                ws.append_log(target,_csv(bundle),source_format="csv",mapping=MAPPING,
                              _transaction_hook=record)
                log_id=target
            else:
                log_id=ws.import_log(_csv(bundle),name=name,source_format="csv",mapping=MAPPING,
                                     _transaction_hook=record)["log_id"]
        except FeedError:raise
        except (ws.EventLogWorkspaceError,ValueError):
            # The existing workspace writes auxiliary quality metadata after
            # COMMIT. The atomic receipt is the source of truth if that fails.
            c=ws._connect()
            try:
                row=c.execute("SELECT definition FROM connector_bundle_import_receipts WHERE request_id=?",[request_id]).fetchone()
                if row:
                    entry=json.loads(row[0])
                    return {"log":ws._check_log(c,entry["log_id"]),"receipt":entry,
                            "replayed_receipt":True,"auxiliary_workspace_result_incomplete":True}
            finally:c.close()
            raise FeedError("bundle_retention_failed_no_partial_import",422) from None
        c=ws._connect()
        try:return {"log":ws._check_log(c,log_id),"receipt":made[0],"replayed_receipt":False}
        finally:c.close()


def inventory():
    with ws._LOCK:
        c=ws._connect()
        try:
            ids=c.execute("SELECT DISTINCT log_id FROM connector_bundle_import_receipts ORDER BY log_id").fetchall()
            return {"logs":[ws._check_log(c,row[0]) for row in ids],
                    "local_only":True,"gateway_origin_authenticated":False}
        finally:c.close()


def history(log_id):
    with ws._LOCK:
        c=ws._connect()
        try:
            log=ws._check_log(c,log_id)
            return {"log":log,"receipts":receipts(c,log_id)}
        except ws.EventLogWorkspaceError:
            raise FeedError("invalid_bundle_target",404) from None
        finally:c.close()
