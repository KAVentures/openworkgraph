"""Explicit Jira handoff writes. Default off, separate write grant, no retries.

Only a configured project/type and minimized structural text are transmitted.
An attempted operation is durably reserved BEFORE the POST. Uncertain results
are never retried automatically or treated as a successful ticket creation.
"""
from __future__ import annotations

import json
import os
import re
from typing import Literal

import httpx
from pydantic import StrictBool

from shared.agent_procurement import Strict,digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_remediation as register
from . import agent_procurement_workforce_remediation_tickets as tickets
from . import agent_procurement_source_outcome as source
from . import agent_procurement_monitor_journal as journal
from . import jira_source_evidence as jira
from . import db

MAX_ATTEMPTS=400
MAX_RESPONSE=320000


class Preview(tickets.Scope):
    operation: Literal["create","comment"]


class Dispatch(Preview):
    expected_plan_sha256: Hash
    confirm_external_write: StrictBool


def _config():
    env=os.environ
    if env.get("OWG_JIRA_REMEDIATION_WRITE_ENABLED")!="1":
        raise ValueError("Jira remediation writes are not enabled")
    host=env.get("OWG_JIRA_HOST","")
    cloud=env.get("OWG_JIRA_CLOUD_ID","")
    project=env.get("OWG_JIRA_REMEDIATION_PROJECT","")
    issue_type=env.get("OWG_JIRA_REMEDIATION_ISSUE_TYPE_ID","")
    if (not jira._TENANT.fullmatch(host) or not jira._CLOUD_ID.fullmatch(cloud)
            or not re.fullmatch(r"[A-Z][A-Z0-9]{1,20}",project)
            or not re.fullmatch(r"[1-9][0-9]{0,15}",issue_type)):
        raise ValueError("invalid configured Jira remediation destination")
    return host,cloud,project,issue_type


def _token():
    token=os.environ.get("OWG_JIRA_REMEDIATION_WRITE_TOKEN","")
    if not token or len(token)>4096 or any(c in token for c in "\r\n"):
        raise ValueError("separate Jira write token required")
    return token


def _schema(conn):
    tickets._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_audit_jira_dispatch (
        worklist_sha256 TEXT NOT NULL, operation_key TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL, result_json TEXT NOT NULL,
        PRIMARY KEY(worklist_sha256,operation_key))""")


def _adf(text):
    return {"type":"doc","version":1,"content":[{"type":"paragraph",
            "content":[{"type":"text","text":text}]}]}


def _plan(conn,req,config):
    host,cloud,project,issue_type=config
    value,states,ledger,bindings,checks=tickets._prepare(conn,req)
    state=states[req.finding_id]
    if state["state"]=="unassigned":
        raise ValueError("assign a finding before external handoff")
    finding=next(f for f in value["basis"]["findings"] if f["finding_id"]==req.finding_id)
    binding=bindings.get(req.finding_id)
    if req.operation=="create":
        if binding:
            raise ValueError("finding already mapped to a ticket")
        target=None
        operation_key=digest(["create",req.finding_id])
    else:
        if not binding:
            raise ValueError("comment requires an observed bound Jira issue")
        record=source.resolve_resource_reference(binding["resource_ref"])
        if source._identity(record,tickets.PREDICATE)!=binding["record_identity_hmac"]:
            raise ValueError("bound Jira record identity changed")
        target=record["resolver_locator"]
        if record["host"]!=host or target.rsplit("-",1)[0]!=project:
            raise ValueError("ticket outside configured write project")
        # No write to a merely guessed resolver entry. Require an exact
        # retained observation and unambiguous source type without live I/O.
        ref=binding["resource_ref"]
        graph=source.record_graph(ref,include_source_status=True,
                                  required_source_type=("jira","issue",host),
                                  source_reader=lambda requested: {"local_guard_passed":requested==ref})
        if (graph.get("source_evidence",{}).get("local_guard_passed") is not True
                or graph.get("record_observed") is not True
                or graph.get("record_identity_complete_within_retained_window") is not True
                or graph.get("record_type")!={"provider":"jira","kind":"issue"}):
            raise ValueError("exact retained Jira observation required")
        operation_key=digest(["comment",req.finding_id,req.expected_history_sha256])
    # No aliases, source text, case tags, credentials, or raw evidence.
    text=("OWG audit follow-up: "+finding["kind"].replace("_"," ")+
          ". Finding "+req.finding_id+". Worklist "+req.worklist_sha256+
          ". Declared local state: "+state["state"]+
          ". This is a review referral, not incident clearance or verified completion.")
    payload=({"fields":{"project":{"key":project},"issuetype":{"id":issue_type},
              "summary":"OWG audit follow-up "+req.finding_id[:12],
              "description":_adf(text),"labels":["owg-audit-"+req.finding_id[:16]]}}
             if req.operation=="create" else {"body":_adf(text)})
    body={"format":"openworkgraph.jira-remediation-handoff.v1",
          "operation":req.operation,"operation_key":operation_key,
          "worklist_sha256":req.worklist_sha256,"finding_id":req.finding_id,
          "finding_sha256":req.expected_finding_sha256,
          "register_history_sha256":req.expected_history_sha256,
          "ticket_history_sha256":req.expected_ticket_history_sha256,
          "destination":{"host":host,"cloud_id":cloud,"project":project,"issue_type_id":issue_type,
                         "bound_issue_key":target},"payload":payload}
    return {**body,"plan_sha256":digest(body)}


def _ensure_available(conn,plan):
    # Creation keys intentionally exclude the worklist: the same stable
    # finding identity in a successor must not create another ticket, even
    # after an uncertain provider response or a changed evidence pin.
    if plan["operation"]=="create":
        prior=conn.execute("SELECT 1 FROM procurement_audit_jira_dispatch WHERE operation_key=?",
                           (plan["operation_key"],)).fetchone()
        if prior:
            raise journal.JournalError("Creation already attempted for this finding; inspect prior Jira handoff history",409)
    elif conn.execute("SELECT 1 FROM procurement_audit_jira_dispatch WHERE worklist_sha256=? AND operation_key=?",
                      (plan["worklist_sha256"],plan["operation_key"])).fetchone():
        raise journal.JournalError("Operation already attempted; inspect Jira manually",409)


def preview(document):
    req=Preview.model_validate(document)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        plan=_plan(conn,req,_config())
        _ensure_available(conn,plan)
        return plan


def _authorize(config,token):
    host,cloud,_,_=config
    resources=jira._jira_get("https://api.atlassian.com/oauth/token/accessible-resources",{},token)
    matches=[r for r in resources if isinstance(r,dict)
             and r.get("id")==cloud and str(r.get("url","")).rstrip("/")==f"https://{host}"
             and isinstance(r.get("scopes"),list) and "write:jira-work" in r["scopes"]] if isinstance(resources,list) else []
    if len(matches)!=1:
        raise ValueError("configured Jira site write grant not established")


def _post(plan,token):
    cloud=plan["destination"]["cloud_id"]
    target=plan["destination"]["bound_issue_key"]
    suffix="/issue" if plan["operation"]=="create" else "/issue/"+target+"/comment"
    url="https://api.atlassian.com/ex/jira/"+cloud+"/rest/api/3"+suffix
    with httpx.Client(headers={"Authorization":"Bearer "+token,"Accept":"application/json"},
                      follow_redirects=False,trust_env=False,timeout=8.0) as client:
        with client.stream("POST",url,json=plan["payload"]) as response:
            if response.status_code!=201:
                return {"status":"unconfirmed","http_status":response.status_code}
            data=bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data)>MAX_RESPONSE:
                    return {"status":"unconfirmed","http_status":201}
    result=json.loads(data)
    if not isinstance(result,dict):
        return {"status":"unconfirmed","http_status":201}
    if plan["operation"]=="create":
        key=result.get("key")
        if not isinstance(key,str) or not jira._ISSUE.fullmatch(key) or key.rsplit("-",1)[0]!=plan["destination"]["project"]:
            return {"status":"unconfirmed","http_status":201}
        return {"status":"provider_reported_created","http_status":201,"issue_key":key}
    comment=result.get("id")
    if not isinstance(comment,str) or not re.fullmatch(r"[1-9][0-9]{0,30}",comment):
        return {"status":"unconfirmed","http_status":201}
    return {"status":"provider_reported_comment_added","http_status":201,"comment_id":comment}


def dispatch(document):
    req=Dispatch.model_validate(document)
    if not req.confirm_external_write:
        raise ValueError("explicit exact external write confirmation required")
    config=_config();token=_token()
    # Preflight pins before network authorization.
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        plan=_plan(conn,req,config)
        if plan["plan_sha256"]!=req.expected_plan_sha256:
            raise ValueError("handoff preview changed")
        _ensure_available(conn,plan)
    try:
        _authorize(config,token)
    except (httpx.HTTPError,OSError,TypeError,KeyError):
        raise ValueError("Jira write grant verification unavailable") from None
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        if config!=_config() or token!=_token() or _plan(conn,req,config)!=plan:
            raise ValueError("handoff changed during authorization")
        _ensure_available(conn,plan)
        if conn.execute("SELECT COUNT(*) FROM procurement_audit_jira_dispatch").fetchone()[0]>=MAX_ATTEMPTS:
            raise journal.JournalError("Jira dispatch attempt capacity reached",409)
        reserved={"status":"attempt_reserved_outcome_unknown","plan_sha256":plan["plan_sha256"],
                  "operation":req.operation,"finding_id":req.finding_id,
                  "destination_host":config[0],"destination_project":config[2],
                  "recorded_at_ms":register._stamp(None)}
        conn.execute("INSERT INTO procurement_audit_jira_dispatch VALUES (?,?,?,?)",
                     (req.worklist_sha256,plan["operation_key"],plan["plan_sha256"],register._encode(reserved)))
    # Exactly one POST per retained reservation. Timeout, crash, malformed
    # response or DB failure is uncertain; never release the reservation.
    try:
        outcome=_post(plan,token)
    except (httpx.HTTPError,ValueError,TypeError,KeyError,OSError):
        outcome={"status":"unconfirmed","http_status":None}
    result={**reserved,**outcome,"incident_clearance_established":False,
            "work_completed_verified":False,"automatic_retry_allowed":False}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        conn.execute("UPDATE procurement_audit_jira_dispatch SET result_json=? WHERE worklist_sha256=? AND operation_key=?",
                     (register._encode(result),req.worklist_sha256,plan["operation_key"]))
    return result


def _related_creations(conn,worklist,findings):
    """Match the exact stable creation reservation key, not titles or aliases."""
    keys={digest(["create",f["finding_id"]]):f["finding_id"] for f in findings}
    related=[]
    for previous,key,raw in conn.execute(
        """SELECT worklist_sha256,operation_key,result_json FROM procurement_audit_jira_dispatch
           WHERE worklist_sha256<>? ORDER BY worklist_sha256,operation_key""",(worklist,)):
        if key not in keys:
            continue
        row=json.loads(raw)
        if row.get("operation")!="create" or row.get("finding_id")!=keys[key]:
            raise ValueError("stored creation reservation mismatch")
        # Previous worklist remains the source of the result. A matching
        # structural finding ID does not verify incident identity or linkage.
        related.append({"previous_worklist_sha256":previous,
                        "finding_id":keys[key],"status":row["status"],
                        "recorded_at_ms":row["recorded_at_ms"],
                        "plan_sha256":row["plan_sha256"],
                        "issue_key":row.get("issue_key"),
                        "destination_host":row.get("destination_host"),
                        "destination_project":row.get("destination_project"),
                        "ticket_mapping_verified":False})
    return related


def status(document):
    req=register.Scope.model_validate(document)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        value,_=register._load(conn,req.worklist_sha256)
        rows=[json.loads(r[0]) for r in conn.execute(
            "SELECT result_json FROM procurement_audit_jira_dispatch WHERE worklist_sha256=? ORDER BY operation_key",
            (req.worklist_sha256,))]
        return {"attempts":rows,
                "related_creation_attempts":_related_creations(conn,req.worklist_sha256,value["basis"]["findings"]),
                "automatic_retry_allowed":False,"incident_clearance_established":False}
