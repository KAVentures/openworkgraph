"""Passive enterprise connector inventory. No credential refresh or provider GET.

Separate source adapters, sign-ins, retained datasets and local job schedules.
Only explicitly known target IDs are joined. Unobserved provider health is
unknown, not healthy. No raw records, endpoint URLs, hostnames or token data
are included in the unified operations export.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import re
import time

from . import enterprise_rest_feed as rest
from . import event_log_workspace as workspace
from . import connector_oauth, local_scheduler, sap_o2c_sync
from .local_source_credentials import stripe_source_status

FORMAT = "openworkgraph.enterprise-connector-operations.v1"
from shared.enterprise_connector_contract import PROVIDERS as RETAINED_PROVIDERS, capabilities

PROVIDERS = (
    ("salesforce","Salesforce","native_multi_object_field_history_and_selected_case_history"),
    ("servicenow","ServiceNow","native_multi_table_audit_creation_and_selected_incident_journal"),
    ("microsoft","Microsoft 365","reviewed_outlook_delta"),
    ("atlassian","Jira","retained_selected_issue_profiles"),
    ("zendesk","Zendesk","selected_ticket_audit"),
    ("sap","SAP S/4HANA","order_to_cash_and_purchase_order_procurement_day_evidence"),
    ("stripe","Stripe","selected_record_evidence"),
    ("hubspot","HubSpot","native_selected_record_property_history"),
    ("dataverse","Microsoft Dynamics 365 / Dataverse","native_selected_table_audit"),
    ("rest","Customer REST","retained_event_feed"),
    ("google","Google Workspace","native_admin_reports_audit"),
    ("slack","Slack","native_enterprise_audit_and_selected_channel_activity"),
    ("bigquery","Google BigQuery","native_approved_bigquery_event_views"),
    ("oracle_fusion","Oracle Fusion Cloud","native_selected_procurement_header_lifecycle"),
    ("workday","Workday","native_curated_raas_event_and_control_reports"),
    ("netsuite","Oracle NetSuite","native_curated_suiteql_event_records"),
    ("snowflake","Snowflake","native_approved_sql_api_event_view"),
    ("databricks","Databricks","native_approved_sql_warehouse_event_view"),
    ("sql","SQL databases","native_postgresql_sqlite_event_feeds"),
    ("folder","Folder exports","retained_csv_feed"),
    ("agent_runs","Agent runs","local_agent_run_log"),
)
KNOWN_KINDS = {"enterprise_rest_feed","retained_jira_profile","event_log_folder_feed","event_log_agent_runs"}


def _stamp(value):
    if type(value) in (int,float):
        try:
            return datetime.fromtimestamp(value,timezone.utc).isoformat()
        except (ValueError,OverflowError,OSError):
            return None
    if isinstance(value,datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
    if isinstance(value,str):
        try:
            parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
            if parsed.utcoffset() is not None:
                return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return None


def _age(stamp,now):
    if stamp is None:
        return None
    seconds=(now-datetime.fromisoformat(stamp)).total_seconds()
    return int(seconds) if seconds>=0 else None


def _id(value,pattern):
    return value if isinstance(value,str) and re.fullmatch(pattern,value) else None


def _table(c,name):
    return bool(c.execute("SELECT 1 FROM information_schema.tables WHERE table_name=?",[name]).fetchone())


def _local_rows():
    with workspace._LOCK:
        c=workspace._connect()
        try:
            logs={r[0]:r[1] for r in c.execute("SELECT log_id,events FROM logs").fetchall()}
            jira=[json.loads(r[0]) for r in c.execute("SELECT definition FROM jira_ingestion_profiles").fetchall()] if _table(c,"jira_ingestion_profiles") else []
            feeds=[]
            if _table(c,"event_log_feeds"):
                for (raw,) in c.execute("SELECT definition FROM event_log_feeds").fetchall():
                    p=json.loads(raw)
                    count,failed,last=c.execute("""SELECT count(*) FILTER(WHERE outcome='appended'),
                        count(*) FILTER(WHERE outcome='failed'),max(processed_at) FILTER(WHERE outcome='appended')
                        FROM event_log_feed_files WHERE feed_id=?""",[p["feed_id"]]).fetchone()
                    feeds.append((p,count,failed,last))
            runs=c.execute("SELECT log_id,created_at,last_refreshed_at FROM event_log_agent_run_sources").fetchall() if _table(c,"event_log_agent_run_sources") else []
            return logs,jira,feeds,runs
        finally:
            c.close()


def inventory(freshness_hours=24,*,now=None):
    if type(freshness_hours) is not int or not 1<=freshness_hours<=720:
        raise ValueError("freshness threshold must be 1–720 hours")
    now=now or datetime.now(timezone.utc)
    errors=[]
    def attempt(name,fn,default):
        try:
            return fn()
        except Exception:
            errors.append(name)
            return default
    local=attempt("retained_workspace",_local_rows,({},[],[],[]))
    logs,jira,feeds,runs=local
    oauth=attempt("oauth_inventory",connector_oauth.status,{"connections":[]})
    schedules=attempt("scheduler_inventory",local_scheduler.status,{"schedules":[],"scheduler_running":None})
    rest_inventory=attempt("rest_inventory",rest.inventory,{"profiles":[]})
    sap=attempt("sap_inventory",sap_o2c_sync.status,{})
    stripe=attempt("stripe_inventory",stripe_source_status,{})
    grants=defaultdict(Counter)
    for connection in oauth.get("connections",[]):
        provider=connection.get("provider")
        if provider in {p[0] for p in PROVIDERS}:
            grants[provider]["stored"]+=1
            if connection.get("reauthorization_required") is True:
                grants[provider]["reconnect_required"]+=1
    jobs={}
    for s in schedules.get("schedules",[]):
        if s.get("kind") in KNOWN_KINDS:
            jobs.setdefault((s["kind"],s["target_id"]),[]).append({
                "schedule_id":_id(s.get("schedule_id"),r"sch_[a-f0-9]{16}"),
                "enabled":s.get("enabled") is True,
                "interval_minutes":s.get("interval_minutes") if type(s.get("interval_minutes")) is int else None,
                "next_run_at":_stamp(s.get("next_run_at")),
                "last_run_at":_stamp(s.get("last_run_at")),
                "consecutive_failures":s.get("consecutive_failures") if type(s.get("consecutive_failures")) is int else None,
                "paused":bool(s.get("paused_reason")),
                "profile_revision":s.get("rest_profile_revision") if type(s.get("rest_profile_revision")) is int else None})
    rows=[]
    def row(provider,kind,target,log_id,state,last,checkpoint=None,revision=None,credential=None,details=None):
        target_jobs=jobs.pop((kind,target),[])
        valid_log=_id(log_id,r"log_[a-f0-9]{16}")
        last=_stamp(last); checkpoint=_stamp(checkpoint)
        age=_age(last,now); lag=_age(checkpoint,now)
        flags=[]
        if state in ("failed","interrupted","dataset_deleted","disabled","checking_credentials"):
            flags.append("profile_"+state)
        if credential is False:
            flags.append("credential_binding_unavailable")
        if valid_log and "retained_workspace" not in errors and valid_log not in logs:
            flags.append("retained_dataset_missing")
        if any(j["paused"] for j in target_jobs):
            flags.append("schedule_paused")
        if any(j["enabled"] and (j["consecutive_failures"] or 0)>0 for j in target_jobs):
            flags.append("scheduled_failures")
        if any(j["enabled"] for j in target_jobs) and schedules.get("scheduler_running") is False:
            flags.append("scheduler_not_running")
        if kind=="enterprise_rest_feed" and any(j["profile_revision"]!=revision for j in target_jobs):
            flags.append("schedule_profile_revision_mismatch")
        if last is None:
            flags.append("no_retained_success_timestamp")
        elif age is None:
            flags.append("success_timestamp_in_future")
        elif age>freshness_hours*3600:
            flags.append("last_retained_success_older_than_threshold")
        if lag is not None and lag>freshness_hours*3600:
            flags.append("checkpoint_older_than_threshold")
        rows.append({"provider":provider,"adapter":kind,"target_id":target,"log_id":valid_log,
                     "retained_event_count":logs.get(valid_log),"local_state":state,"revision":revision,
                     "last_retained_success_at":last,"success_age_seconds":age,
                     "checkpoint_at":checkpoint,"checkpoint_age_seconds":lag,
                     "credential_binding_available":credential,"schedules":target_jobs,
                     "review_flags":flags,"counts":details or {},"provider_live_health":"unknown"})
    for p in rest_inventory.get("profiles",[]):
        row(p.get("provider","rest"),"enterprise_rest_feed",p["profile_id"],p["log_id"],p["status"],p["last_success_at"],
            p["checkpoint"],p["revision"],p["credential_binding_available"],
            details={**({"schema_pin_observed":bool(p.get("source_schema_sha256"))} if p.get("provider")=="sql" else {}),
                     "integrity_hold":bool(p.get("integrity_hold"))})
    for p in jira:
        target=_id(p.get("profile_id"),r"jira_[a-f0-9]{24}")
        if not target:
            continue
        state=p.get("status")
        if state not in {"ready","running","checking_credentials","interrupted","failed","disabled","dataset_deleted","read_retained"}:
            state="unknown"
        if state in {"running","checking_credentials"} and (p.get("lease_until") or 0)<=now.timestamp():
            state="interrupted"
        row("atlassian","retained_jira_profile",target,p.get("log_id"),state,p.get("last_success_at"),
            revision=p.get("revision") if type(p.get("revision")) is int else None,
            details={"selected_cases":len(p.get("issue_keys",[]))})
    for p,appended,failed,last in feeds:
        target=_id(p.get("feed_id"),r"feed_[a-f0-9]{16}")
        if target:
            row("folder","event_log_folder_feed",target,p.get("log_id"),"configured",last,
                details={"files_appended":appended,"files_failed":failed})
    for log_id,created,refreshed in runs:
        target=_id(log_id,r"log_[a-f0-9]{16}")
        if target:
            row("agent_runs","event_log_agent_runs",target,target,"configured",refreshed or created)
    if sap.get("configured") is True:
        last=sap.get("last_run") or {}
        state="failed" if last.get("outcome")=="failed" else "configured"
        # SAP advances its checkpoint only after successful retention.
        row("sap","sap_o2c","sap_o2c",sap.get("log_id"),state,sap.get("checkpoint"),sap.get("checkpoint"))
    providers=[]
    for pid,label,adapter in PROVIDERS:
        selected=[r for r in rows if r["provider"]==pid]
        providers.append({"provider":pid,"label":label,"implemented_adapter_scope":adapter,
                          "stored_oauth_connections":grants[pid]["stored"] if "oauth_inventory" not in errors and pid in connector_oauth.PROVIDERS else None,
                          "oauth_reconnect_required":grants[pid]["reconnect_required"] if "oauth_inventory" not in errors and pid in connector_oauth.PROVIDERS else None,
                          "retained_profiles_observed":len(selected),
                          "local_configuration_observed":bool(selected) or (pid=="stripe" and stripe.get("configured") is True),
                          "provider_live_health":"unknown"})
    return {"format":FORMAT,"observed_at":now.isoformat(),"freshness_review_hours":freshness_hours,
            "inventory_sources_unavailable":errors,"inventory_complete":not errors,
            "provider_reads_performed":False,"credential_refresh_performed":False,
            "provider_live_health_verified":False,"scheduler_running":schedules.get("scheduler_running"),
            "retained_adapter_capabilities":[capabilities(p) for p in sorted(RETAINED_PROVIDERS)],
            "providers":providers,"profiles":sorted(rows,key=lambda r:(r["provider"],r["target_id"])),
            "unmatched_schedules":[{"kind":kind,"target_id":target,"schedules":entries}
                                   for (kind,target),entries in sorted(jobs.items())],
            "review_flag_counts":dict(sorted(Counter(flag for r in rows for flag in r["review_flags"]).items())),
            "interpretation":"Local point-in-time operational inventory. CLI-only provider scopes are not enumerated; absence of a retained profile is not proof of no configured connection. A stored sign-in, successful prior read or recent checkpoint never establishes current provider access, coverage or business completeness."}
